# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
dYdX market data collector entrypoint.

Owns its own asyncio loop, a typed in-memory buffer, and a configurable snapshot loop.
No TradingNode/Strategy/DataEngine involved -- see client.py for why.

Instrument control (Story 6.1)
-------------------------------
`config.toml`'s [[instruments]] list is the single, always-authoritative source of what
gets collected -- at startup the collector subscribes to exactly that list and nothing
else. There is no automatic, timer-driven reclassification: the collected set only
changes in response to an explicit `collector:control` Redis message (see
`_control_loop`), never on its own.

Every instrument in `instruments` is pinned by definition -- there is no "collected but
not pinned" middle state. The four control actions:

start          : add a brand-new (or previously-unpinned) id and start collecting it,
                 pinned immediately -- also removes it from `config.exclude` if it was
                 there. Rejected past `_MAX_COLLECTED_INSTRUMENTS`.
unpin          : stop collecting an id and add it to `config.exclude` (persisted) --
                 same permanent denylist a hand-edited exclude entry uses, so an
                 unpinned coin is also excluded from any future liquidity ranking, not
                 just out of the collected set. `:start <ID>` reverses this. This is
                 the only way an actively-collected id stops via the TUI's `p` key.
stop           : stop collecting an id, same as unpin, but without excluding it --
                 use for an id you don't want bot_tui listing as "available to re-add."
pin_top_liquid : fill any empty collector slots (up to the cap) with the current
                 top-by-volume coins not already collected, pinned immediately. Never
                 removes or replaces an existing entry -- there's nothing left for it to
                 protect against, since every entry is already pinned.

`_MAX_WS_SUBSCRIPTIONS` (32) is dYdX's real per-connection WS hard subscription limit;
`_MAX_COLLECTED_INSTRUMENTS` (30) is this collector's own, smaller operating cap
(2-slot safety margin) enforced on `start`/`pin_top_liquid` -- never exceed it.
"""

import asyncio
import dataclasses
import functools
import json
import logging
import os
import re
import time
import warnings
from pathlib import Path

import redis.asyncio as aioredis
from collector_core.collector import Collector
from collector_core.collector import run_forever
from collector_core.prune_catalog import prune_instrument
from observability import error_ledger
from observability import incidents
from observability.incidents import IncidentConfig
from observability.incidents import IncidentRule

from dydx_collector import uncross
from dydx_collector.client import DydxClient
from dydx_collector.config import DydxConfig
from dydx_collector.config import InstrumentEntry
from dydx_collector.config import load_config
from dydx_collector.config import save_config
from dydx_collector.open_interest import _fetch_markets_json
from dydx_collector.open_interest import classify_liquidity
from dydx_collector.open_interest import fetch_open_interest
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.toml"

# dYdX's WS server hard-caps subscriptions per channel per connection at 32 (confirmed
# live via its own error: "Per-connection subscription limit reached for v4_trades
# (limit=32)"). Every liquid/pinned instrument subscribes both v4_trades and
# v4_orderbook, so this must bound the *combined* pinned+liquid instrument count, not
# just liquid alone -- going over it doesn't just drop the overflow, the repeated
# rejections get the whole connection detected as dead and endlessly reconnected/
# rejected again, which is what produced permanent "Stale book" warnings on every
# instrument (not just the overflow ones) rather than a one-off blip.
_MAX_WS_SUBSCRIPTIONS: int = 32

# This collector's own operating cap (Story 6.1) -- a deliberate 2-slot safety margin
# below dYdX's real _MAX_WS_SUBSCRIPTIONS above, enforced on `start`/`pin_top_liquid`
# control actions. Intentionally a separate constant from _MAX_WS_SUBSCRIPTIONS: one is
# the venue's hard ceiling, the other is our own choice of how close to run to it.
_MAX_COLLECTED_INSTRUMENTS: int = 30

# Redis channels for live instrument control (Story 6.1) -- mirrors the bots:control/
# bots:status pattern already used between bot_tui and live_paper (bot_status.py).
_CONTROL_CHANNEL = "collector:control"
_STATUS_CHANNEL = "collector:status"

# CONFIRMED root cause, category 2 -- evidence for `CoreConfig.crossed_resync_seconds`
# (default 10 s; it was this module's `_CROSSED_RESYNC_NS` before story 22.2 moved the
# value into the shared config, and the number is still provisional). Original finding
# (2026-09-04, cross-checked live against dYdX's own
# REST orderbook endpoint during ~25 live episodes across BTC/ETH/ETC/UNI): one side of
# our local reconstruction gets stuck holding a stale price level -- some delta that
# should have removed/updated it never took effect locally -- while the other side keeps
# tracking the real venue almost exactly. A raw per-delta trace of a full episode also
# showed zero deltas ever touched the frozen price again during the whole window. This
# never self-heals from more deltas: the missing update is gone, not merely delayed, so
# waiting out THIS category is pure downside. There is no per-market sequence number to
# detect the drop directly -- dYdX's `sequence` field is the WS *connection-global*
# message_id (shared by every channel/market on the connection), not a per-instrument
# orderbook sequence, so per-instrument gap detection false-positives on any other
# channel's traffic (see _apply_deltas's docstring). The crossed-book symptom is the
# only detection signal available for this category.
#
# BUT (2026-09-06, Story 5.1 in-progress, independent reference-client cross-check):
# category 1 (genuine crossing already present in dYdX's own broadcast, not a local
# loss -- see crossed-book-root-cause.md) can ALSO persist a full 3s: a live BTC episode
# was confirmed byte-for-byte identical between our collector and a zero-shared-code
# reference client at every tick for the whole 3s window, yet still tripped this timer
# and got force-resynced -- a destructive no-op on an already-healthy book (see DATA-03
# in platform/CLAUDE.md). Duration alone cannot distinguish the two categories; only the
# reference-client cross-check can, and that isn't wired into production. Raised
# 3s -> 10s as an evidence-informed but still provisional widening of the grace window
# (trades a longer stale-book gap for a genuine category-2 desync -- which never
# self-heals regardless of window size -- against fewer wasted resyncs of category-1
# episodes) while Story 5.1 keeps gathering real duration data on both categories.


def _prune_interval_seconds(
    non_config_retain_hours: float, delta_retain_hours: dict[str, float | None]
) -> float:
    """
    Prune-loop cadence: roughly 4x per the shortest active retention window, minimum 15 min.

    Considers both the global non_config_retain_hours and any finite per-coin
    retain_hours, so a short per-coin window isn't left stale by a larger global one.
    """
    active_retain_hours = [
        non_config_retain_hours,
        *(h for h in delta_retain_hours.values() if h is not None),
    ]
    return max(min(active_retain_hours) * 900, 900)


def _prune_candidates(
    instruments: tuple[InstrumentEntry, ...], known_markets: set[str]
) -> set[str]:
    """
    Ids whose catalog data is subject to non_config_retain_hours pruning (Story 6.1).

    Every known market not currently collected (dropped by `stop` or `unpin`, or never
    added) -- its leftover catalog data must still age out.
    """
    collected_ids = {e.id for e in instruments}
    return known_markets - collected_ids


def _prune_all_instruments(catalog_path: str, ids: set[str], retain_hours: float) -> int:
    """Blocking filesystem walk over every candidate -- always call via asyncio.to_thread."""
    freed = 0
    for iid in ids:
        freed += prune_instrument(catalog_path, iid, retain_hours)
    return freed


def _prune_delta_retention(catalog_path: str, delta_retain_hours: dict[str, float | None]) -> int:
    """
    Prune order_book_deltas for instruments with a finite per-coin retain_hours.

    A `None` value means unlimited retention -- that instrument is skipped entirely.
    Extracted from _prune_loop so the None-skip behavior can be unit-tested directly.
    """
    freed = 0
    for iid, retain_hours in delta_retain_hours.items():
        if retain_hours is None:
            continue
        freed += prune_instrument(catalog_path, iid, retain_hours, data_types=["order_book_deltas"])
    return freed


class DydxCollector(Collector):
    """
    dYdX on the shared write gate: the core owns ingest/flush/sample/write; this class adds
    dYdX's per-level message-id book tagging + uncross (`uncross.py`, wired by overriding
    `_apply_deltas` / `_handle_crossed_book`) and the control plane as `extra_loops`
    (hot-reload, `collector:status`/`collector:control`, prune, open interest, `[WS_RAW]`
    flush).
    """

    def __init__(self, config: DydxConfig) -> None:
        client = DydxClient(on_data=self._on_data, network=config.network)
        super().__init__(
            config,
            client,
            extra_loops=(
                self._reload_config_loop,
                self._open_interest_loop,
                self._status_loop,
                self._control_loop,
                self._prune_loop,
                # Raw-WS debug feed (Story 5.1) for the incident reports (INCIDENTS below) --
                # a permanent feature, not scoped to any one investigation. Rust's file
                # logger only flushes its BufWriter to disk on an explicit Sync event --
                # without this, [WS_RAW] lines sit in memory forever.
                functools.partial(incidents.raw_log_flush_loop, nautilus_pyo3.logging_sync_to_disk),
            ),
        )
        self._config: DydxConfig  # narrows the core's CoreConfig

        # All ids dYdX currently lists, whether collected or not -- refreshed by
        # _status_loop. Used only by _prune_loop to clean up catalog data left behind
        # by an instrument that's no longer in config.instruments (dropped by a stop or
        # unpin control action) -- see that loop's docstring. Empty until the first
        # _status_loop tick.
        self._known_markets: set[str] = set()
        # Most recent volume-based liquidity classification, keyed by id -- refreshed by
        # _status_loop, reused by _publish_status for an immediate post-control-action
        # publish so pin/start/stop show up in the TUI without waiting up to
        # liquidity_check_seconds for the next periodic tick. Empty (so "liquid" reads
        # False for everyone) until the first _status_loop tick.
        self._last_liquid_by_volume: set[str] = set()
        # Instruments for which raw OrderBookDeltas are written to the catalog
        self._delta_store: set[str] = {
            e.id for e in config.instruments if e.store_order_book_deltas
        }
        # Per-coin raw-delta retention, in hours; None means unlimited (never pruned).
        # Independent of the collected/uncollected split used for non_config_retain_hours below.
        self._delta_retain_hours: dict[str, float | None] = {
            e.id: e.retain_hours for e in config.instruments if e.store_order_book_deltas
        }
        # Wall-clock ns of the last delta seen *for that side specifically*, keyed by
        # instrument. _last_book_update_ns updates on ANY delta (either side), which
        # can't tell "book is crossed because bid-side deltas stopped arriving" apart
        # from "book is crossed while both sides keep updating normally" (a genuine
        # venue-level cross). Split per-side so the crossed-book log can say which one.
        self._last_bid_delta_ns: dict[str, int] = {}
        self._last_ask_delta_ns: dict[str, int] = {}
        # (bid, ask) at the moment a crossing was first observed -- lets the resolution
        # log line prove a real price change happened (not a spurious/no-op clear).
        self._crossed_prices: dict[str, tuple[float, float]] = {}
        # Story 5.2: per-price-level tag of the dYdX connection-global message_id
        # (OrderBookDelta.sequence) that last touched it, keyed by (side, price). This is
        # a DIFFERENT use of that field than the per-instrument gap detection removed in
        # commit 944891bbba -- here it's a local "which of these two specific levels was
        # touched more recently" comparator, exactly as dYdX's own Indexer uses it
        # (Roundtable's uncross-orderbook.ts) to resolve a crossed book without a full
        # resync. See DATA-04 in platform/CLAUDE.md and uncross.uncross_step.
        self._level_msg_id: dict[str, dict[tuple[OrderSide, float], int]] = {}

    # -- core hooks --------------------------------------------------------------------------

    def _instrument_ids(self) -> set[str]:
        return {e.id for e in self._config.instruments}

    def _clear_book_state(self, iid: str) -> None:
        """
        Drop all per-instrument order-book tracking state.

        Required on both unsubscribe and resync -- otherwise a later resubscribe reads a
        stale `_crossed_since_ns` entry (set hours/days earlier) and can fire a false
        steady_state_crossed_book CRITICAL plus an unwarranted destructive resync on a
        book that was never actually stuck (DATA-02/DATA-03). The state is split across
        two classes: the core pops `_live_books` and `_crossed_since_ns`; dYdX adds
        `_crossed_prices` and `_level_msg_id`.
        """
        super()._clear_book_state(iid)
        self._crossed_prices.pop(iid, None)
        self._level_msg_id.pop(iid, None)

    def _apply_deltas(self, iid: str, deltas: OrderBookDeltas) -> None:
        """
        Apply deltas to the live book, tag every level with its message-id, and buffer
        the raw deltas for instruments in `_delta_store` (the core never buffers deltas;
        the rest are only ever used to maintain the live book, so are never held for the
        whole flush interval).

        No gap-detection against `OrderBookDelta.sequence` here: that field is dYdX's
        WS *connection-level* message_id (confirmed via the Rust adapter's own test
        fixtures -- a subscribe-ack, an unrelated market's trade, and this market's
        orderbook update share one incrementing counter), not a per-market orderbook
        sequence. Comparing consecutive values for one instrument therefore "gaps" on
        every bit of interleaved traffic from any other channel/market on the same
        connection -- a false positive on effectively every message once more than a
        handful of instruments share the connection, not a sign of a dropped delta.
        WebSocket/TCP already guarantees ordered, lossless delivery while connected;
        a genuine disconnect is handled by the Rust client's reconnect + resubscribe,
        which always starts with a fresh Clear + snapshot (self-healing regardless of
        sequence tracking). Steady-state local corruption with no such known cause is
        still caught structurally by the crossed-book escalation in `_second_loop`.
        """
        if iid in self._delta_store:
            self._buffer[(OrderBookDeltas, iid)].append(deltas)
        self._last_book_update_ns[iid] = time.time_ns()
        if not deltas.deltas:
            return
        if iid not in self._live_books:
            self._live_books[iid] = OrderBook(deltas.instrument_id, BookType.L2_MBP)
        book = self._live_books[iid]
        level_msg_id = self._level_msg_id.setdefault(iid, {})
        now_ns = time.time_ns()
        for delta in deltas.deltas:
            book.apply_delta(delta)
            if delta.is_clear:
                # A Clear wipes the whole book -- every prior per-level tag is now stale.
                level_msg_id.clear()
            elif delta.is_delete:
                level_msg_id.pop((delta.order.side, delta.order.price.as_double()), None)
            else:
                level_msg_id[(delta.order.side, delta.order.price.as_double())] = delta.sequence
            if delta.is_clear or delta.order.side == OrderSide.BUY:
                self._last_bid_delta_ns[iid] = now_ns
            if delta.is_clear or delta.order.side == OrderSide.SELL:
                self._last_ask_delta_ns[iid] = now_ns

    def _uncross_step(self, iid: str, book: OrderBook) -> bool:
        return uncross.uncross_step(iid, book, self._level_msg_id.get(iid, {}))

    async def _handle_crossed_book(self, iid: str, book: OrderBook, now_ns: int) -> bool:
        """DATA-04 uncross first, escalation ladder after (see `uncross.py`); True = skip the tick."""
        return await uncross.handle_crossed_book(
            iid,
            book,
            now_ns,
            level_msg_id=self._level_msg_id.get(iid, {}),
            crossed_since_ns=self._crossed_since_ns,
            crossed_prices=self._crossed_prices,
            last_bid_delta_ns=self._last_bid_delta_ns,
            last_ask_delta_ns=self._last_ask_delta_ns,
            resync_after_ns=int(self._config.crossed_resync_seconds * 1e9),
            resync=self._resync_book,
        )

    async def _resync_book(self, iid: str) -> None:
        """Force a fresh order-book snapshot for a desynced instrument via resubscribe."""
        logger.warning(f"Resyncing desynced order book for {iid}")
        await self._client.unsubscribe_orderbook(iid)
        await self._client.subscribe_orderbook(iid)
        self._clear_book_state(iid)

    # -- control plane (extra_loops) ---------------------------------------------------------

    async def _open_interest_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.open_interest_poll_seconds)
            try:
                for item in await fetch_open_interest(self._config.network):
                    # Straight to the buffer, not _on_data: REST-polled data must not count as
                    # WS feed liveness (`_last_feed_message_ns`, story 22.5) nor feed a
                    # reconnect's silence detection (story 22.14). Every market is kept, as
                    # before: the markets poll is venue-wide.
                    self._buffer[(type(item), str(item.instrument_id))].append(item)
            except Exception:
                error_ledger.record("collector.open_interest_poll", "failed to poll open interest")

    async def _subscribe(self, iid: str) -> None:
        await self._client.subscribe_trades(iid)
        await self._client.subscribe_orderbook(iid)
        logger.info(f"Subscribed {iid}")

    async def _unsubscribe(self, iid: str) -> None:
        await self._client.unsubscribe_trades(iid)
        await self._client.unsubscribe_orderbook(iid)
        self._clear_book_state(iid)
        logger.info(f"Unsubscribed {iid}")

    async def _apply_config(self, new_config: DydxConfig) -> None:
        """
        Diff old vs. new `instruments` and subscribe/unsubscribe accordingly, then adopt
        `new_config`. The one shared place this diffing happens (Story 6.1) -- used by
        both the periodic file-reload loop and every control-action handler below, so
        there is exactly one implementation of "what changed."
        """
        old_ids = {e.id for e in self._config.instruments}
        new_ids = {e.id for e in new_config.instruments}

        for iid in new_ids - old_ids:
            await self._subscribe(iid)
        for iid in old_ids - new_ids:
            await self._unsubscribe(iid)

        self._delta_store = {e.id for e in new_config.instruments if e.store_order_book_deltas}
        self._delta_retain_hours = {
            e.id: e.retain_hours for e in new_config.instruments if e.store_order_book_deltas
        }
        self._config = new_config

    async def _status_loop(self) -> None:
        """
        Publish collector:status (an informational liquid/illiquid label per currently-
        collected instrument) and refresh self._known_markets for _prune_loop's cleanup
        of abandoned instruments' catalog data (Story 6.1).

        Read-only: unlike the auto-resubscribing loop this replaces, it never
        subscribes/unsubscribes anything itself -- the collected set only changes via an
        explicit collector:control message (see this module's docstring and
        _handle_control_message).

        Publishes immediately on the first iteration, then every
        liquidity_check_seconds after that -- run-then-sleep, not sleep-then-run.
        liquidity_check_seconds defaults to 1800s (30 min); a sleep-first loop would
        leave the bot_tui Collector page showing "waiting for collector:status" for up
        to half an hour after every collector restart, which is exactly the bug a user
        hit in production before this fix.
        """
        while not self._stop.is_set():
            try:
                markets_json = await asyncio.to_thread(_fetch_markets_json, self._config.network)
                self._known_markets = {
                    f"{m['ticker']}-PERP.DYDX"
                    for m in markets_json.get("markets", {}).values()
                    if m.get("ticker")
                }
                # No max_liquid cap here -- this is a display label, not a selection.
                self._last_liquid_by_volume, _ = classify_liquidity(
                    markets_json, self._config.liquidity_min_oi_usd, self._config.exclude
                )
                await self._publish_status()
            except Exception:
                error_ledger.record("collector.status_loop", "status loop failed")
            await asyncio.sleep(self._config.liquidity_check_seconds)

    async def _publish_status(self) -> None:
        """
        Publish one collector:status message per currently-collected instrument, using
        the most recent volume classification (self._last_liquid_by_volume, refreshed by
        _status_loop), plus one aggregate message carrying config.exclude (bot_tui's
        "unpinned" section -- everything excluded, whether by hand or via unpin). Called
        both by _status_loop's own periodic tick and by _apply_and_persist, so a
        start/unpin/stop/pin_top_liquid action is reflected in the TUI immediately
        rather than waiting up to liquidity_check_seconds.

        No-op if self._redis isn't set yet (e.g. a control action fired before run()'s
        first _status_loop tick, or a unit test driving _handle_control_message directly
        without a real Redis connection).
        """
        if self._redis is None:
            return
        for entry in self._config.instruments:
            payload = {
                "id": entry.id,
                "liquid": entry.id in self._last_liquid_by_volume,
                "last_trade_ts": self._last_book_update_ns.get(entry.id, 0),
                # Trades recovered over REST after reconnects since start (story 22.14).
                "trade_backfill": self._trade_backfill_counts.get(entry.id, 0),
            }
            await self._redis.publish(_STATUS_CHANNEL, json.dumps(payload))
        await self._redis.publish(
            _STATUS_CHANNEL, json.dumps({"unpinned_ids": sorted(self._config.exclude)})
        )

    async def _reload_config_loop(self) -> None:
        """Hot-reload: picks up a hand-edited config.toml (e.g. after a git pull) without a restart."""
        while not self._stop.is_set():
            await asyncio.sleep(self._config.config_reload_seconds)
            new_config = load_config(CONFIG_PATH)
            await self._apply_config(new_config)

    async def _apply_and_persist(self, new_config: DydxConfig) -> None:
        await self._apply_config(new_config)
        save_config(self._config, CONFIG_PATH)
        await self._publish_status()

    async def _handle_control_message(self, action: str | None, iid: str | None) -> None:
        """Dispatch one collector:control message (Story 6.1, AC #3/#4/#5)."""
        entries = {e.id: e for e in self._config.instruments}

        if action == "start":
            if iid in entries:
                logger.warning("Cannot start %s: already collected", iid)
                return
            if len(entries) >= _MAX_COLLECTED_INSTRUMENTS:
                logger.warning(
                    "Cannot start %s: at %s-instrument cap", iid, _MAX_COLLECTED_INSTRUMENTS
                )
                return
            new_instruments = (*self._config.instruments, InstrumentEntry(id=iid))
            new_config = dataclasses.replace(
                self._config, instruments=new_instruments, exclude=self._config.exclude - {iid}
            )
            await self._apply_and_persist(new_config)

        elif action == "unpin":
            if iid not in entries:
                logger.warning("Cannot unpin %s: not currently collected", iid)
                return
            del entries[iid]
            new_config = dataclasses.replace(
                self._config,
                instruments=tuple(entries.values()),
                exclude=self._config.exclude | {iid},
            )
            await self._apply_and_persist(new_config)
            await self._publish_removed(iid)

        elif action == "stop":
            if iid not in entries:
                logger.warning("Cannot stop %s: not currently collected", iid)
                return
            del entries[iid]
            new_config = dataclasses.replace(self._config, instruments=tuple(entries.values()))
            await self._apply_and_persist(new_config)
            await self._publish_removed(iid)

        elif action == "pin_top_liquid":
            await self._pin_top_liquid()

        else:
            logger.warning("Unknown collector:control action: %r", action)

    async def _publish_removed(self, iid: str) -> None:
        """
        Tell bot_tui to drop `iid` immediately rather than waiting up to
        collector_state._STATUS_STALE_SECONDS for it to notice `iid` is no longer being
        republished by _publish_status -- stop/unpin both fully remove an instrument
        from collection, so its row should disappear from the Collector pane right away.
        """
        if self._redis is None:
            return
        await self._redis.publish(_STATUS_CHANNEL, json.dumps({"id": iid, "removed": True}))

    async def _pin_top_liquid(self) -> None:
        """
        Fill any empty collector slots (up to _MAX_COLLECTED_INSTRUMENTS) with the
        current top-by-volume coins not already collected and not in config.exclude,
        pinning them immediately.

        Additive only -- never removes or replaces an existing entry. Unlike the old
        refresh_top_coins this replaces, there is no "collected but not pinned" state
        left for it to overwrite; every entry is already pinned and stays put.

        An explicitly-unpinned id is excluded from the candidate set for free here --
        unpin adds it to config.exclude, and classify_liquidity already treats every
        excluded id as illiquid regardless of volume. Re-adding one is always
        `:start <ID>`, never automatic.
        """
        existing_ids = {e.id for e in self._config.instruments}
        free_slots = max(0, _MAX_COLLECTED_INSTRUMENTS - len(existing_ids))
        if free_slots == 0:
            return
        markets_json = await asyncio.to_thread(_fetch_markets_json, self._config.network)
        top, _ = classify_liquidity(
            markets_json,
            self._config.liquidity_min_oi_usd,
            self._config.exclude | existing_ids,
            max_liquid=free_slots,
        )
        new_instruments = self._config.instruments + tuple(
            InstrumentEntry(id=iid) for iid in sorted(top)
        )
        new_config = dataclasses.replace(self._config, instruments=new_instruments)
        await self._apply_and_persist(new_config)

    async def _control_loop(self) -> None:
        """
        Act on start/unpin/stop/pin_top_liquid messages published to collector:control
        (Story 6.1). Own connection + reconnect loop, mirroring
        bot_tui/bots_state.py's _redis_listener shape -- independent of self._redis
        (used for publishing snapshots/status).
        """
        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        while not self._stop.is_set():
            try:
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(_CONTROL_CHANNEL)
                    logger.info("collector:control listener subscribed")
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            payload = json.loads(message["data"])
                            await self._handle_control_message(
                                payload.get("action"), payload.get("id")
                            )
                        except Exception:
                            error_ledger.record(
                                "collector.control",
                                f"collector:control message failed: {message!r}",
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("collector:control listener error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)

    async def _prune_loop(self) -> None:
        """Prune uncollected instruments' catalog data, and any per-coin raw-delta retention."""
        while not self._stop.is_set():
            # Recomputed each iteration so a hot-reloaded retain_hours takes effect promptly.
            interval = _prune_interval_seconds(
                self._config.non_config_retain_hours, self._delta_retain_hours
            )
            await asyncio.sleep(interval)
            catalog_path = str(Path(self._config.catalog_path).resolve())
            dropped_ids = _prune_candidates(self._config.instruments, self._known_markets)

            # Both calls do real synchronous filesystem walks (up to 267 instruments'
            # worth) -- to_thread keeps them off the event loop, which _ingest_loop and
            # _second_loop's crossed-book/staleness detection also depend on running
            # promptly. A same-thread version of this loop once blocked the loop for
            # 20s+ and stalled every instrument's book simultaneously (2026-09-11 OOM).
            freed = await asyncio.to_thread(
                _prune_all_instruments,
                catalog_path,
                dropped_ids,
                self._config.non_config_retain_hours,
            )
            if freed:
                logger.info(
                    f"Pruned {freed / 1024 / 1024:.1f} MB from {len(dropped_ids)} uncollected instruments"
                )

            delta_freed = await asyncio.to_thread(
                _prune_delta_retention, catalog_path, self._delta_retain_hours
            )
            if delta_freed:
                logger.info(
                    f"Pruned {delta_freed / 1024 / 1024:.1f} MB of raw order-book deltas (per-coin retention)"
                )


# ---------------------------------------------------------------------------
# Incident reports (Story 5.1; the handler moved to `observability.incidents` in Story 23.1).
# Everything dYdX-specific about them is this one config: the id shape, the report title, where
# the Rust [WS_RAW] debug log lives and what one of its lines carries for an instrument.
# ---------------------------------------------------------------------------

INCIDENTS = IncidentConfig(
    report_title="dYdX Collector Incident Report",
    # `ticker` is what a [WS_RAW] line's "id" field carries for the instrument.
    iid_pattern=re.compile(r"\b(?P<iid>(?P<ticker>[A-Z0-9]+-USD)-PERP\.DYDX)\b"),
    evidence_needle='"id":"{ticker}"',
    # Deliberately /tmp: an ephemeral rolling buffer inside a single-purpose container (the
    # compose `collector` service), not a shared multi-tenant host -- no symlink/race risk.
    raw_log_dir=Path("/tmp/nautilus_logs"),  # noqa: S108
    raw_log_name="ws_raw_debug",
    # Bind-mounted (compose `./data/incident_reports`): must survive container restarts.
    report_dir=Path("/app/incident_reports"),
    # Tried in order; texts are the collector's own warning lines.
    rules=(
        IncidentRule("Crossed book", "crossed_book"),
        IncidentRule("Stale book", "stale_book"),
        IncidentRule("_second_loop tick arrived", "second_loop_lag", with_instrument=False),
        IncidentRule("Resyncing", "resync"),
    ),
)

# Story 23.1 moved the incident subsystem to `observability.incidents`. The one old name with a
# same-object, same-shape successor is served below with a DeprecationWarning. Every other name
# raises, naming its successor: the handler now needs an `IncidentConfig`, and the constants are
# fields of `INCIDENTS` -- serving a copy would make a stale monkeypatch silently do nothing.
MOVED_NAMES_REMOVE_AFTER = "24-1-candles-context-behind-the-secondsink-port"
_MOVED_NAMES: dict[str, str] = {
    "_ns_to_iso": "observability.incidents.ns_to_iso",
}
_REPLACED_NAMES: dict[str, str] = {
    "_IncidentHandler": "observability.incidents.IncidentHandler(config, loop)",
    "_IID_RE": "dydx_collector.collector.INCIDENTS.iid_pattern",
    "_WS_RAW_LOG_DIR": "dydx_collector.collector.INCIDENTS.raw_log_dir",
    "_INCIDENT_DIR": "dydx_collector.collector.INCIDENTS.report_dir",
    "_INCIDENT_DEBOUNCE_NS": "dydx_collector.collector.INCIDENTS.debounce_ns",
    "_INCIDENT_LOOKBACK_NS": "dydx_collector.collector.INCIDENTS.lookback_ns",
    "_INCIDENT_DIR_MAX_BYTES": "dydx_collector.collector.INCIDENTS.report_dir_max_bytes",
    "_INCIDENT_LOOKAHEAD_DELAY_S": "dydx_collector.collector.INCIDENTS.lookahead_s",
    "_ws_raw_debug_flush_loop": "observability.incidents.raw_log_flush_loop(sync)",
    "_classify_incident": "observability.incidents.classify_incident(config, message)",
    "_scan_ws_raw_window": "observability.incidents.scan_raw_window(config, ...)",
    "_write_incident_report": "observability.incidents.IncidentReportWriter.write",
    "_prune_incident_reports": "observability.incidents.IncidentReportWriter.prune",
    "_INCIDENT_PRUNE_LOCK": "observability.incidents.IncidentReportWriter (owns the lock)",
    "_report_incident": "observability.incidents.IncidentHandler.report",
    "_prune_stale_ws_raw_logs": "observability.incidents.prune_stale_raw_logs(config)",
}


def __getattr__(name: str) -> object:
    if name in _MOVED_NAMES:
        target = _MOVED_NAMES[name]
        warnings.warn(
            f"dydx_collector.collector.{name} moved to {target} (Story 23.1); "
            f"removed after {MOVED_NAMES_REMOVE_AFTER}",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(incidents, target.rpartition(".")[2])
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"dydx_collector.collector.{name} was replaced by {_REPLACED_NAMES[name]} (Story 23.1)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def main() -> None:

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    incidents.prune_stale_raw_logs(INCIDENTS)
    logging.getLogger().addHandler(incidents.IncidentHandler(INCIDENTS))
    # Rust's `log` crate is a no-op until a logger is installed -- without this, any
    # `log::warn!`/`log::error!` inside the Rust WS client (including the exact path that
    # reports a failed `call_soon_threadsafe` scheduling, i.e. a delta silently never
    # reaching `_on_data`) is completely invisible: not filtered out, never emitted at all.
    # This was never wired up because this collector never touches TradingNode/Kernel
    # (the usual place nautilus_trader calls it). WARNING+ only -- surfaces hidden
    # failures without adding Rust-side INFO/DEBUG noise on top of the Python logging above.
    # init_logging() returns a LogGuard that MUST be kept alive for the process lifetime --
    # LogGuard's Drop impl (crates/common/src/logging/logger.rs:1343) treats the LAST guard
    # being dropped as subsystem shutdown: it sets a global bypass flag, disables the log
    # crate's max level, and joins/closes the logging thread. Discarding the return value
    # (as this call used to) means Python garbage-collects the guard within microseconds of
    # this call returning -- Rust-side logging was silently DEAD immediately after every
    # single startup, this whole time. `_log_guard` must stay a live reference for `main()`'s
    # entire lifetime (it does, since this coroutine runs until shutdown).
    _log_guard = nautilus_pyo3.init_logging(
        trader_id=nautilus_pyo3.TraderId("COLLECTOR-001"),
        instance_id=nautilus_pyo3.UUID4(),
        level_stdout=nautilus_pyo3.LogLevel.WARNING,
        # Permanent raw-WS debug feed (Story 5.1), not scoped to any one investigation --
        # feeds the incident-report subsystem below. DEBUG+ goes to a file, not stdout --
        # component_levels/log_components_only can only make Logger's filtering MORE
        # restrictive than the global stdout/fileout level, never less (see
        # Logger::enabled() in crates/common/src/logging/logger.rs), so there is no way
        # to raise just handler.rs's [WS_RAW] debug! line above stdout=WARNING without a
        # separate, permissive file sink.
        level_file=nautilus_pyo3.LogLevel.DEBUG,
        directory=str(INCIDENTS.raw_log_dir),
        file_name=INCIDENTS.raw_log_name,
        # Bounded rolling buffer, not a growing archive: [WS_RAW] is ~1MB/s. IncidentHandler
        # (above) auto-snapshots the relevant INCIDENTS.lookback_ns (10s) + a
        # INCIDENTS.lookahead_s (2s) window into a permanent incident report the
        # moment something WARNING+ worthy happens -- this buffer only needs to outlast that
        # ~12s window by a safety margin, not a human noticing and checking manually (that
        # was the old, much larger 500MB-nominal design this replaces). 20MB x 1 backup is
        # ~40MB nominal / ~40s -- over 3x the required window.
        #
        # Smaller than the old 250MB x 2 (~750MB nominal, and the underlying trigger for a
        # disk-full incident on nifelheim once restarts orphaned old rotations -- see
        # prune_stale_raw_logs). Rotating every ~20s at 20MB does mean nautilus_trader's
        # file writer's unconditional `eprintln!("Rotated log file...")` on every rotation
        # (crates/common/src/logging/writer.rs's rotate_file(), not routed through the
        # `log` crate, so log level can't silence it) fires more often -- purely docker-logs
        # noise nothing in this codebase reads (scan_raw_window globs every rotated
        # file, never depends on which one is "current"), traded deliberately for a much
        # smaller worst-case disk footprint.
        file_rotate=(20_000_000, 1),
    )

    # run_forever owns the restart loop, signal handling and per-process quarantine; it
    # must not install a second Rust logger over the WS_RAW file sink above.
    await run_forever(lambda: DydxCollector(load_config(CONFIG_PATH)), init_rust_logging=False)


if __name__ == "__main__":
    asyncio.run(main())
