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

`DydxCollector` is capture only: dYdX's per-level message-id book tagging and uncross
(`uncross.py`), its forced resync, the open-interest poll and the `[WS_RAW]` flush. What is
collected is the collection-control context's (Story 25.4, `collection_control/`): the plan in
`config.toml` (`CollectionPlan`: the `[[instruments]]` list, `exclude`, the 30-instrument cap under
dYdX's 32-per-connection WS limit), changed only by an explicit `collector:control` command or a
hand edit of the file, and applied here through `Collector.apply`, whose `Applied` result is the
fact `collector:status` reports. `build_collector` is the composition root that wires that context's
three loops (plan reload, `collector:status`, `collector:control`) in as `extra_loops`.
"""

import asyncio
import functools
import logging
import os
import re
import time
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from candles.application.prune import loop as candle_prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import store_from_env
from collection_control.application.control import ControlService
from collection_control.application.reload import reload_loop
from collection_control.application.status import StatusPublisher
from collection_control.infrastructure.markets import DydxMarkets
from collection_control.infrastructure.plan_store import TomlPlanStore
from collection_control.infrastructure.redis import RedisControlChannel
from collection_control.infrastructure.redis import RedisStatusBus
from collector_core.collector import Collector
from collector_core.collector import run_forever
from collector_core.config import DydxConfig
from collector_core.config import load_venue_config
from collector_core.ports import Applied
from collector_core.ports import PlanDiff
from observability import error_ledger
from observability import incidents
from observability.incidents import IncidentConfig
from observability.incidents import IncidentRule

from dydx_collector import uncross
from dydx_collector.client import DydxClient
from dydx_collector.open_interest import fetch_open_interest
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.toml"

# The control-plane loops a composition root hands the collector (`build_collector`); a capture
# test builds a collector with none.
ControlPlane = Callable[["DydxCollector"], tuple[Callable[[], Awaitable[None]], ...]]


def _no_control_plane(_capture: "DydxCollector") -> tuple[Callable[[], Awaitable[None]], ...]:
    return ()


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


class DydxCollector(Collector):
    """
    dYdX on the shared write gate: the core owns ingest/flush/sample/write and the applied set;
    this class adds dYdX's per-level message-id book tagging + uncross (`uncross.py`, wired by
    overriding `_apply_deltas` / `_handle_crossed_book`), the raw-delta store of the plan's
    `store_order_book_deltas` instruments (kept current by `apply`), the open-interest poll and the
    `[WS_RAW]` flush. The control plane arrives as `control_plane`'s loops. It holds no prune loop:
    retention is the nightly `archive.prune_catalog`'s (Story 25.1).
    """

    VENUE: ClassVar[str] = "DYDX"

    def __init__(
        self,
        config: DydxConfig,
        plan_ids: Iterable[str],
        *,
        store_deltas: Iterable[str] = (),
        control_plane: ControlPlane = _no_control_plane,
    ) -> None:
        client = DydxClient(on_data=self._on_data, network=config.network)
        # Composition root: this process owns dYdX's candle store, so it opens it (one file per
        # venue, `CANDLES_DB_PATH`), hands capture the sink port and runs the retention loop.
        store = store_from_env(config.catalog_path)
        super().__init__(
            config,
            client,
            extra_loops=(
                self._open_interest_loop,
                # Raw-WS debug feed (Story 5.1) for the incident reports (INCIDENTS below) --
                # a permanent feature, not scoped to any one investigation. Rust's file
                # logger only flushes its BufWriter to disk on an explicit Sync event --
                # without this, [WS_RAW] lines sit in memory forever.
                functools.partial(incidents.raw_log_flush_loop, nautilus_pyo3.logging_sync_to_disk),
                candle_prune_loop(store),
            ),
            plan=plan_ids,
            second_sink=CandleSink(store),
        )
        self._config: DydxConfig  # narrows the core's CoreConfig

        # Instruments for which raw OrderBookDeltas are written to the catalog: the plan's
        # `store_order_book_deltas` entries, replaced by every `apply`.
        self._delta_store: set[str] = set(store_deltas)
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
        self._extra_loops = (*self._extra_loops, *control_plane(self))

    # -- core hooks --------------------------------------------------------------------------

    async def apply(self, diff: PlanDiff) -> Applied:
        """Adopt the diff's complete raw-delta storage set, then run the core's apply."""
        self._delta_store = set(diff.store_deltas)
        return await super().apply(diff)

    def _store_deltas(self) -> frozenset[str]:
        return frozenset(self._delta_store)

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
        if iid not in self._live_books and deltas.deltas and not deltas.deltas[0].is_clear:
            # A book starts only from a snapshot (dYdX's always leads with a Clear), as in the
            # core: after a resync drops the book, in-flight incremental deltas would otherwise
            # rebuild a shallow one that is sampled, and -- a book existing again -- the queued
            # resync (`_handle_missing_book`) would never run. Counted, reported each flush.
            self._deltas_before_snapshot_dropped[iid] += 1
            return
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
        """
        Force a fresh order-book snapshot for a desynced instrument via resubscribe. Through
        capture's `_resync`, so it is serialized with `apply` and never re-subscribes an id a
        concurrent `stop` removed; a failure is ledgered and retried from the next tick.
        """
        logger.warning(f"Resyncing desynced order book for {iid}")
        await self._resync(iid)

    # -- extra loops -------------------------------------------------------------------------

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


def build_collector(config_path: Path = CONFIG_PATH) -> DydxCollector:
    """
    Build the collector -- the composition root (DDD spine AD-D2/AD-D17): load the venue config and
    the plan through the one loader, and wire collection control's loops -- plan reload, `collector:status`,
    `collector:control` -- into a fresh collector as `extra_loops`. Called per `run_forever`
    attempt, so a restart starts from the plan the file holds now.
    """
    config, plan = load_venue_config(config_path, "DYDX")
    if not isinstance(config, DydxConfig):
        raise TypeError(f"the DYDX loader returned {type(config).__name__}, not DydxConfig")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

    def control_plane(capture: DydxCollector) -> tuple[Callable[[], Awaitable[None]], ...]:
        markets = DydxMarkets(config.network)
        status = StatusPublisher(capture, RedisStatusBus(redis_url), markets)
        store = TomlPlanStore(config_path, "DYDX")
        control = ControlService(plan, store, capture, status, markets)
        return (
            functools.partial(reload_loop, control, config.config_reload_seconds),
            functools.partial(status.loop, lambda: control.plan, config.liquidity_check_seconds),
            functools.partial(control.control_loop, RedisControlChannel(redis_url)),
        )

    return DydxCollector(
        config,
        plan.collected,
        store_deltas=plan.delta_store_ids,
        control_plane=control_plane,
    )


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
    await run_forever(build_collector, init_rust_logging=False)


if __name__ == "__main__":
    asyncio.run(main())
