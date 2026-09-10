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
`_MAX_COLLECTED_INSTRUMENTS` (29) is this collector's own, smaller operating cap
(3-slot safety margin) enforced on `start`/`pin_top_liquid` -- never exceed it.
"""

import asyncio
import dataclasses
import json
import logging
import os
import re
import shutil
import signal
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import redis.asyncio as aioredis

from dydx_collector.client import DydxClient
from dydx_collector.config import CollectorConfig
from dydx_collector.config import InstrumentEntry
from dydx_collector.config import load_config
from dydx_collector.config import save_config
from dydx_collector.open_interest import _fetch_markets_json
from dydx_collector.open_interest import classify_liquidity
from dydx_collector.open_interest import fetch_open_interest
from dydx_collector.prune_catalog import prune_instrument
from dydx_collector.second_snapshot import BOOK_DEPTH
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import instruments_from_pyo3
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)
# Distinct logger name (not a new file/volume) so a steady-state desync escalation is
# separable from routine collector WARNING lines, while staying stdout/Dozzle-based.
critical_logger = logging.getLogger("dydx_collector.critical")

CONFIG_PATH = Path(__file__).parent / "config.toml"

_QUARANTINE_DIRNAME = "_quarantine"

# ponytail: ParquetDataCatalog.write_data() (pinned nautilus_trader 1.229.0) has no
# compression passthrough -- it calls pq.write_table() with pyarrow's "snappy" default,
# and nautilus_trader/persistence/catalog/parquet.py can't be modified (fork rule). Patch
# pyarrow's default here instead. `pq` is a shared module object (Python caches modules in
# sys.modules), so this reaches nautilus's `import pyarrow.parquet as pq` call site too.
# Ceiling: if a future nautilus_trader version passes `compression=` explicitly, this patch
# is silently ignored -- revisit on version bump.
_orig_write_table = pq.write_table


def _write_table_zstd(*args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("compression", "zstd")
    _orig_write_table(*args, **kwargs)


pq.write_table = _write_table_zstd


def quarantine_corrupt_parquet(catalog_path: str) -> None:
    """
    Move any unreadable .parquet file (e.g. left by a mid-write crash) out of the way.

    Runs once at process start, before any new writes. A half-written file from a killed
    process would otherwise sit forever next to good data and can break catalog reads or
    consolidation. ponytail: full recursive scan on every process start; if the catalog
    grows into the hundreds of thousands of files, switch to only checking files newer
    than the last clean shutdown.
    """
    root = Path(catalog_path).resolve()
    if not root.exists():
        return

    quarantine_root = root / _QUARANTINE_DIRNAME
    for path in root.rglob("*.parquet"):
        if quarantine_root in path.parents:
            continue
        try:
            pq.ParquetFile(path)
        except Exception:
            dest = quarantine_root / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            logger.warning(f"Quarantined corrupt parquet file: {path} -> {dest}")


# Skip snapshot if book hasn't received OrderBookDeltas in this many nanoseconds.
# During WS reconnect recovery the Rust client re-subscribes at 2/sec, so the last
# instrument in the sorted queue can wait up to N/2 seconds for a fresh snapshot.
# Emitting the pre-reconnect stale book state during that window produces flatlines
# on the coin chart. 5 seconds is conservative — liquid dYdX instruments receive
# book updates multiple times per second under normal conditions.
_STALE_BOOK_NS: int = 5_000_000_000  # 5 seconds

# dYdX's WS server hard-caps subscriptions per channel per connection at 32 (confirmed
# live via its own error: "Per-connection subscription limit reached for v4_trades
# (limit=32)"). Every liquid/pinned instrument subscribes both v4_trades and
# v4_orderbook, so this must bound the *combined* pinned+liquid instrument count, not
# just liquid alone -- going over it doesn't just drop the overflow, the repeated
# rejections get the whole connection detected as dead and endlessly reconnected/
# rejected again, which is what produced permanent "Stale book" warnings on every
# instrument (not just the overflow ones) rather than a one-off blip.
_MAX_WS_SUBSCRIPTIONS: int = 32

# This collector's own operating cap (Story 6.1) -- a deliberate 3-slot safety margin
# below dYdX's real _MAX_WS_SUBSCRIPTIONS above, enforced on `start`/`pin_top_liquid`
# control actions. Intentionally a separate constant from _MAX_WS_SUBSCRIPTIONS: one is
# the venue's hard ceiling, the other is our own choice of how close to run to it.
_MAX_COLLECTED_INSTRUMENTS: int = 29

# Redis channels for live instrument control (Story 6.1) -- mirrors the bots:control/
# bots:status pattern already used between bot_tui and live_paper (bot_status.py).
_CONTROL_CHANNEL = "collector:control"
_STATUS_CHANNEL = "collector:status"

# CONFIRMED root cause, category 2 (2026-09-04, cross-checked live against dYdX's own
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
# in troll/CLAUDE.md). Duration alone cannot distinguish the two categories; only the
# reference-client cross-check can, and that isn't wired into production. Raised
# 3s -> 10s as an evidence-informed but still provisional widening of the grace window
# (trades a longer stale-book gap for a genuine category-2 desync -- which never
# self-heals regardless of window size -- against fewer wasted resyncs of category-1
# episodes) while Story 5.1 keeps gathering real duration data on both categories.
# Revisit this number, don't treat it as settled, once more episodes are classified.
_CROSSED_RESYNC_NS: int = 10_000_000_000  # 10 seconds (was 3s -- see comment above)

# Story 5.2 / DATA-04: defensive bound on how many stale levels `_uncross_step` will
# drop in one _handle_crossed_book call before giving up and falling back to the
# existing resync path. A genuine crossed-book episode is normally one or two levels
# deep -- this is a safety cap against a pathological/many-levels-deep cross, not a
# value expected to be hit in practice.
_UNCROSS_MAX_STEPS: int = 5

# _ingest_loop yields to the event loop every this-many processed messages, so
# _second_loop's sleep-based wakeup gets a fair chance to run during a burst instead of
# waiting behind the entire backlog. Smaller = _second_loop wakes sooner but more
# event-loop round-trip overhead per message; larger = less overhead but a longer worst
# case gap. 64 is an arbitrary small value, not a measured optimum -- ponytail: retune
# only if _second_loop's own staleness canary (below) still fires after this shipped.
_INGEST_YIELD_EVERY: int = 64

# OBS-01: zero book updates across all liquid instruments for 30s+ is a pipeline
# failure, not a quiet market -- BTC/ETH/SOL perpetuals trade 24/7. Deployed behind
# an SSH tunnel nobody is watching the dashboard continuously, so this needs to push
# a notification rather than rely on someone noticing a frozen chart.
_WATCHDOG_CHECK_SECONDS: float = 30.0
_WATCHDOG_STALE_NS: int = 30_000_000_000  # 30 seconds
_WATCHDOG_STARTUP_GRACE_NS: int = 60_000_000_000  # subscriptions need time to establish
_WATCHDOG_REMINDER_NS: int = 600_000_000_000  # re-notify at most every 10 min while down

# _second_loop staleness canary: complementary safety net to _ingest_loop's yielding --
# that queue shrinks the risk of the event loop being monopolized during a message
# burst, it can't eliminate it (see _ingest_loop's docstring). If _second_loop's own
# wakeup still arrives this much later than the configured interval, the crossed-book
# detection/resync guard was silently not running for that whole gap -- surface it
# rather than let it look like a quiet market. 2s of slack tolerates normal scheduling
# jitter without false-positiving on every tick.
_SECOND_LOOP_LAG_WARN_NS: int = 2_000_000_000  # 2 seconds


def _buffer_key(data: Any) -> tuple[type, str]:
    return type(data), str(data.instrument_id)


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


def _prune_candidates(instruments: tuple[InstrumentEntry, ...], known_markets: set[str]) -> set[str]:
    """
    Ids whose catalog data is subject to non_config_retain_hours pruning (Story 6.1).

    Two groups, matching the pre-6.1 `(self._liquid | self._illiquid) - self._pinned`
    behavior exactly: currently-collected non-pinned instruments (a legacy state --
    nothing creates one anymore, since every instrument is pinned by definition; this
    stays a no-op group rather than a group that can never exist, for any config.toml
    hand-edited before this change), UNION every known market not currently collected
    at all (covers an instrument dropped by `stop` or `unpin` -- its leftover catalog
    data must still age out).
    """
    collected_ids = {e.id for e in instruments}
    non_pinned_collected = {e.id for e in instruments if not e.pinned}
    return non_pinned_collected | (known_markets - collected_ids)


def _watchdog_transition(
    now_ns: int,
    is_stale: bool,
    down_since_ns: int | None,
    last_reminder_ns: int,
) -> tuple[str | None, int | None, int]:
    """
    Pure state-machine step for the feed watchdog: (message_or_None, down_since_ns, last_reminder_ns).

    Kept separate from the asyncio loop and the notify transport so the alerting/
    debounce logic is unit-testable without mocking network calls.
    """
    if is_stale:
        if down_since_ns is None:
            return (
                "dydx-collector: all live instruments' order books have gone stale "
                "(no OrderBookDeltas for 30s+) — feed may be down",
                now_ns,
                now_ns,
            )
        if now_ns - last_reminder_ns > _WATCHDOG_REMINDER_NS:
            down_for_s = (now_ns - down_since_ns) / 1e9
            return (
                f"dydx-collector: still down, no book updates for {down_for_s:.0f}s",
                down_since_ns,
                now_ns,
            )
        return (None, down_since_ns, last_reminder_ns)

    if down_since_ns is not None:
        down_for_s = (now_ns - down_since_ns) / 1e9
        return (f"dydx-collector: recovered after {down_for_s:.0f}s", None, 0)

    return (None, None, last_reminder_ns)


def _notify(message: str) -> None:
    """POST to a ntfy.sh-compatible topic URL. No-op if WATCHDOG_NTFY_URL isn't set."""
    url = os.environ.get("WATCHDOG_NTFY_URL")
    if not url:
        logger.critical(message)
        return
    request = urllib.request.Request(  # noqa: S310 (fixed, operator-configured URL)
        url,
        data=message.encode(),
        headers={"Title": "dydx-collector"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass
    except OSError:
        logger.exception("Watchdog notification failed")


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


async def _publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """
    Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:raw.

    Empty batches are silently dropped. Publish failures are logged and swallowed —
    missing one tick is acceptable per the architecture.
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    try:
        await redis_client.publish("snapshots:raw", payload)
    except Exception as e:
        logger.warning("Redis publish failed: %s", e)


def _resolve_stale_level(
    book: OrderBook, bid: Price, ask: Price, bid_seq: int, ask_seq: int,
) -> tuple[OrderSide, Price, int, int]:
    """
    Which side of a crossed book is stale, per dYdX's own tie-break rule (DATA-04):
    the level with the strictly older (smaller) message-id, or on a tie, the side
    with the smaller resting size (dYdX's own documented tie-break).

    Returns (stale_side, stale_price, stale_seq, other_seq) -- extracted from
    Collector._uncross_step to keep that method under this project's ~30-line
    guideline (READ-01).
    """
    if bid_seq == ask_seq:
        bid_stale = book.best_bid_size().as_double() <= book.best_ask_size().as_double()
    else:
        bid_stale = bid_seq < ask_seq
    if bid_stale:
        return OrderSide.BUY, bid, bid_seq, ask_seq
    return OrderSide.SELL, ask, ask_seq, bid_seq


def _apply_stale_delete(book: OrderBook, stale_side: OrderSide, stale_price: Price, sequence: int) -> None:
    """
    Synthetic BookAction.DELETE for the stale level (DATA-04) -- applied identically
    to how a real dYdX-sent deletion is applied, never a full book wipe.
    """
    delete_order = BookOrder(
        side=stale_side, price=stale_price, size=Quantity(0.0, stale_price.precision), order_id=0,
    )
    now_ns = time.time_ns()
    book.apply_delta(
        OrderBookDelta(
            instrument_id=book.instrument_id,
            action=BookAction.DELETE,
            order=delete_order,
            flags=0,
            sequence=sequence,
            ts_event=now_ns,
            ts_init=now_ns,
        )
    )


def _log_uncross_result(
    iid: str,
    stale_side: OrderSide,
    stale_price: Price,
    stale_seq: int,
    other_seq: int,
    side_now_empty: bool,
) -> None:
    """
    DATA-02: a one-sided book after uncrossing is a real data-loss event, not routine
    self-healing -- must not be logged identically to the benign case, or this failure
    class stays invisible.
    """
    if side_now_empty:
        logger.warning(
            "Crossed book for %s uncrossed by dropping the LAST remaining %s level "
            "@ %.6f (msg_id %d, surviving side msg_id %d) -- book is now one-sided",
            iid, stale_side.name, stale_price.as_double(), stale_seq, other_seq,
        )
    else:
        logger.info(
            "Crossed book for %s actively uncrossed: dropped stale %s @ %.6f "
            "(msg_id %d, surviving side msg_id %d)",
            iid, stale_side.name, stale_price.as_double(), stale_seq, other_seq,
        )


class Collector:
    def __init__(self, config: CollectorConfig) -> None:
        self._config = config

        catalog_path = Path(config.catalog_path).resolve()
        catalog_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(str(catalog_path))

        self._client = DydxClient(on_data=self._on_data, network=config.network)
        self._buffer: dict[tuple[type, str], list[Any]] = defaultdict(list)
        # Set in run() -- None until then, so a Collector built for a unit test (never
        # calling run()) can still exercise control-action handling; _publish_status
        # guards on this being set rather than requiring a real Redis connection.
        self._redis: aioredis.Redis | None = None

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
        # Independent of the pinned/non-pinned tier split used for non_config_retain_hours below.
        self._delta_retain_hours: dict[str, float | None] = {
            e.id: e.retain_hours for e in config.instruments if e.store_order_book_deltas
        }

        # Trade volume/count accumulated between consecutive 1s ticks, reset each second
        self._second_buy_volume: dict[str, float] = defaultdict(float)
        self._second_sell_volume: dict[str, float] = defaultdict(float)
        self._second_buy_count: dict[str, int] = defaultdict(int)
        self._second_sell_count: dict[str, int] = defaultdict(int)
        # Running trade-price OHLC for the current second, built from TradeTicks as
        # they arrive (see _process_data) -- this is the collector's only record of
        # traded price now that raw TradeTicks are no longer persisted. Absence of a
        # key (checked via .pop(iid, None) in _second_loop) means no trade occurred
        # this second, distinct from a trade occurring at price 0.
        self._second_open_price: dict[str, float] = {}
        self._second_high_price: dict[str, float] = {}
        self._second_low_price: dict[str, float] = {}
        self._second_close_price: dict[str, float] = {}

        # Real-time order books — updated immediately on every OrderBookDeltas callback,
        # independent of the 60s flush cycle. _second_loop reads from here, not _bar_builder._books.
        self._live_books: dict[str, OrderBook] = {}
        # Wall-clock ns of the last OrderBookDeltas received per instrument.
        # Used by _second_loop to skip stale books (staleness = no updates for > _STALE_BOOK_NS).
        self._last_book_update_ns: dict[str, int] = {}
        # Wall-clock ns of the last delta seen *for that side specifically*, keyed by
        # instrument. _last_book_update_ns updates on ANY delta (either side), which
        # can't tell "book is crossed because bid-side deltas stopped arriving" apart
        # from "book is crossed while both sides keep updating normally" (a genuine
        # venue-level cross). Split per-side so the crossed-book log can say which one.
        self._last_bid_delta_ns: dict[str, int] = {}
        self._last_ask_delta_ns: dict[str, int] = {}

        # _watchdog_loop state: when the all-instruments-stale condition started (None
        # while healthy), and when the last reminder notification was sent.
        self._watchdog_started_ns: int = time.time_ns()
        self._watchdog_down_since_ns: int | None = None
        self._watchdog_last_reminder_ns: int = 0

        # Wall-clock ns when a book was first observed continuously crossed;
        # cleared as soon as it's seen uncrossed. Drives the resync watchdog below.
        self._crossed_since_ns: dict[str, int] = {}
        # (bid, ask) at the moment a crossing was first observed -- lets the resolution
        # log line prove a real price change happened (not a spurious/no-op clear).
        self._crossed_prices: dict[str, tuple[float, float]] = {}
        # Story 5.2: per-price-level tag of the dYdX connection-global message_id
        # (OrderBookDelta.sequence) that last touched it, keyed by (side, price). This is
        # a DIFFERENT use of that field than the per-instrument gap detection removed in
        # commit 944891bbba -- here it's a local "which of these two specific levels was
        # touched more recently" comparator, exactly as dYdX's own Indexer uses it
        # (Roundtable's uncross-orderbook.ts) to resolve a crossed book without a full
        # resync. See DATA-04 in troll/CLAUDE.md and _uncross_step below.
        self._level_msg_id: dict[str, dict[tuple[OrderSide, float], int]] = {}

        # _on_data (the WS callback, scheduled via the Rust client's call_soon_threadsafe)
        # only enqueues -- _ingest_loop does the real per-message work (_process_data).
        # Keeps the callback itself cheap enough that a burst of incoming messages can't
        # monopolize the event loop and starve _second_loop's crossed-book detection (two
        # real episodes took 10-19s to resync instead of ~3s before this queue existed --
        # see _ingest_loop's docstring). Unbounded: a real overflow would mean the process
        # can't keep up with the exchange at all, which is a different problem than this
        # queue can solve -- ponytail: revisit with a maxsize + drop policy only if that's
        # ever observed.
        self._ingest_queue: asyncio.Queue[Any] = asyncio.Queue()
        # Wall-clock ns of the previous _second_loop tick, for the staleness canary below.
        self._last_second_loop_tick_ns: int | None = None

        self._redis: aioredis.Redis | None = None
        self._stop = asyncio.Event()

    def _on_data(self, data: Any) -> None:
        # Called directly from the Rust WS client's callback thread/loop via
        # call_soon_threadsafe. Must stay this cheap: this scheduled callback and
        # _second_loop's sleep-based wakeup compete for the same event loop, and asyncio
        # drains every ready callback before honoring a timer. Real per-message work
        # (_process_data) happens in _ingest_loop instead, off this hot path.
        try:
            self._ingest_queue.put_nowait(data)
        except Exception:
            logger.exception(f"Failed to enqueue {type(data).__name__}, dropping")

    async def _ingest_loop(self) -> None:
        """
        Drain `_on_data`'s queue and apply each message off the WS callback's hot path.

        Yields to the event loop every _INGEST_YIELD_EVERY messages so a burst of
        incoming deltas can't monopolize it and starve _second_loop's crossed-book
        detection/resync -- confirmed in production: two real episodes took 10-19s to
        resync instead of the intended ~3s because this loop didn't exist yet and
        _apply_deltas ran directly on `_on_data`'s callback. This shrinks the risk to
        "how long N messages take", not eliminates it -- a sustained enough message rate
        can still delay any single-threaded consumer, which is what _second_loop's own
        staleness canary (see its docstring) is for.
        """
        processed = 0
        while not self._stop.is_set():
            try:
                data = await asyncio.wait_for(self._ingest_queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                self._process_data(data)
            except Exception:
                logger.exception(f"Failed to process {type(data).__name__}, dropping")
            processed += 1
            if processed % _INGEST_YIELD_EVERY == 0:
                await asyncio.sleep(0)

    def _process_data(self, data: Any) -> None:
        # TradeTick is deliberately excluded from the catalog buffer -- raw trades are
        # no longer persisted (see DydxSecondSnapshot's open/high/low/close_price
        # fields below, and troll/docs/DATA_DICTIONARY.md's Retention section). Every
        # other type still goes through the normal buffer/flush path.
        if not isinstance(data, TradeTick):
            self._buffer[_buffer_key(data)].append(data)
        if isinstance(data, OrderBookDeltas):
            iid = str(data.instrument_id)
            self._apply_deltas(iid, data)
            self._last_book_update_ns[iid] = time.time_ns()
        elif isinstance(data, TradeTick):
            iid = str(data.instrument_id)
            price = data.price.as_double()
            if iid not in self._second_open_price:
                self._second_open_price[iid] = price
                self._second_high_price[iid] = price
                self._second_low_price[iid] = price
            elif price > self._second_high_price[iid]:
                self._second_high_price[iid] = price
            elif price < self._second_low_price[iid]:
                self._second_low_price[iid] = price
            self._second_close_price[iid] = price
            if data.aggressor_side == AggressorSide.BUYER:
                self._second_buy_volume[iid] += data.size.as_double()
                self._second_buy_count[iid] += 1
            else:
                self._second_sell_volume[iid] += data.size.as_double()
                self._second_sell_count[iid] += 1

    def _apply_deltas(self, iid: str, data: OrderBookDeltas) -> None:
        """
        Apply deltas to the live book.

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
        if not data.deltas:
            return
        if iid not in self._live_books:
            self._live_books[iid] = OrderBook(data.instrument_id, BookType.L2_MBP)
        book = self._live_books[iid]
        level_msg_id = self._level_msg_id.setdefault(iid, {})
        now_ns = time.time_ns()
        for delta in data.deltas:
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

    async def _flush_once(self) -> None:
        """
        Write every buffered instrument/datatype's pending items to the catalog.

        `ParquetDataCatalog.write_data()` is real synchronous disk I/O (Arrow/zstd
        serialization + file writes) -- confirmed in production to take multiple seconds
        per flush cycle. Offload each write via `asyncio.to_thread` so it can't block
        _second_loop's crossed-book detection the same way delta processing used to
        (see _ingest_loop's docstring) -- this was the actual cause of a ~60s-periodic
        event-loop stall the staleness canary caught (_flush_interval_seconds' periodicity
        gave it away), not delta-processing bursts as originally suspected. The buffer
        swap itself (`self._buffer[key] = []`) stays synchronous on this thread -- only
        the already-extracted, thread-local `items` list crosses to the executor thread,
        so `self._buffer` is still only ever mutated from the event loop thread.
        """
        for key, items in list(self._buffer.items()):
            if not items:
                continue
            self._buffer[key] = []
            dtype, iid = key

            if dtype is OrderBookDeltas and iid not in self._delta_store:
                continue

            try:
                await asyncio.to_thread(self._catalog.write_data, items)
            except Exception:
                logger.exception(f"Failed to write {key}, dropping {len(items)} items")

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.flush_interval_seconds)
            await self._flush_once()

    async def _open_interest_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.open_interest_poll_seconds)
            try:
                for item in await fetch_open_interest(self._config.network):
                    self._on_data(item)
            except Exception:
                logger.exception("Failed to poll open interest")

    async def _subscribe(self, iid: str) -> None:
        await self._client.subscribe_trades(iid)
        await self._client.subscribe_orderbook(iid)
        logger.info(f"Subscribed {iid}")

    async def _unsubscribe(self, iid: str) -> None:
        await self._client.unsubscribe_trades(iid)
        await self._client.unsubscribe_orderbook(iid)
        self._clear_book_state(iid)
        logger.info(f"Unsubscribed {iid}")

    def _clear_book_state(self, iid: str) -> None:
        """
        Drop all per-instrument order-book tracking state.

        Required on both unsubscribe and resync -- otherwise a later resubscribe reads a
        stale `_crossed_since_ns` entry (set hours/days earlier) and can fire a false
        steady_state_crossed_book CRITICAL plus an unwarranted destructive resync on a
        book that was never actually stuck (DATA-02/DATA-03).
        """
        self._live_books.pop(iid, None)
        self._crossed_since_ns.pop(iid, None)
        self._crossed_prices.pop(iid, None)
        self._level_msg_id.pop(iid, None)

    async def _resync_book(self, iid: str) -> None:
        """Force a fresh order-book snapshot for a desynced instrument via resubscribe."""
        logger.warning(f"Resyncing desynced order book for {iid}")
        await self._client.unsubscribe_orderbook(iid)
        await self._client.subscribe_orderbook(iid)
        self._clear_book_state(iid)

    async def _apply_config(self, new_config: CollectorConfig) -> None:
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
                logger.exception("Status loop failed")
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
                "pinned": entry.pinned,
                "liquid": entry.id in self._last_liquid_by_volume,
                "last_trade_ts": self._last_book_update_ns.get(entry.id, 0),
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

    async def _apply_and_persist(self, new_config: CollectorConfig) -> None:
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
            new_instruments = (*self._config.instruments, InstrumentEntry(id=iid, pinned=True))
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
            InstrumentEntry(id=iid, pinned=True) for iid in sorted(top)
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
                            await self._handle_control_message(payload.get("action"), payload.get("id"))
                        except Exception:
                            logger.exception("collector:control message failed: %r", message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("collector:control listener error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)

    def _uncross_step(self, iid: str, book: OrderBook) -> bool:
        """
        One active-uncrossing correction step (DATA-04, Story 5.2).

        Ports dYdX's own Indexer remediation (Roundtable's `uncross-orderbook.ts`)
        instead of forcing a full resync: drop only the stale side of a crossed book,
        using each level's tagged message-id (`self._level_msg_id`, set in
        `_apply_deltas`) to decide which side is stale -- the level with the strictly
        older (smaller) message-id, or on a tie, the side with the smaller resting size
        (dYdX's own documented tie-break). Applies a synthetic `BookAction.DELETE` via
        `book.apply_delta()`, identical to how a real dYdX-sent deletion is applied.

        Returns True if a level was dropped (caller should re-check crossed state and
        may loop). Returns False if the book isn't crossed, or either level lacks a
        tag (can't arbitrate -- caller falls back to the existing resync path).
        """
        bid, ask = book.best_bid_price(), book.best_ask_price()
        if bid is None or ask is None or bid.as_double() < ask.as_double():
            return False
        level_msg_id = self._level_msg_id.get(iid, {})
        bid_seq = level_msg_id.get((OrderSide.BUY, bid.as_double()))
        ask_seq = level_msg_id.get((OrderSide.SELL, ask.as_double()))
        if bid_seq is None or ask_seq is None:
            return False

        stale_side, stale_price, stale_seq, other_seq = _resolve_stale_level(
            book, bid, ask, bid_seq, ask_seq
        )
        _apply_stale_delete(book, stale_side, stale_price, max(bid_seq, ask_seq))
        level_msg_id.pop((stale_side, stale_price.as_double()), None)

        side_now_empty = (
            book.best_bid_price() is None if stale_side == OrderSide.BUY
            else book.best_ask_price() is None
        )
        _log_uncross_result(iid, stale_side, stale_price, stale_seq, other_seq, side_now_empty)
        return True

    def _handle_uncrossed_book(self, iid: str, book: OrderBook, now_ns: int) -> None:
        """
        Book isn't (or is no longer) crossed -- clear tracking state and, if this
        instrument had an open crossed-book episode, log its resolution.

        Proves this was a real book update (the price actually moved), not a no-op
        or a silently-forced resync -- distinguishes a genuine, harmless sub-second
        touch (self-heals via normal delta activity, matches _CROSSED_RESYNC_NS's
        documented tolerance) from a stuck desync that only recovers via
        _resync_book's forced resubscribe (logged separately, as a CRITICAL).
        """
        crossed_since = self._crossed_since_ns.pop(iid, None)
        crossed_prices = self._crossed_prices.pop(iid, None)
        if crossed_since is None or crossed_prices is None:
            return
        logger.info(
            "Crossed book for %s resolved after %.2fs "
            "(was bid=%.6f/ask=%.6f, now bid=%.6f/ask=%.6f)",
            iid,
            (now_ns - crossed_since) / 1e9,
            crossed_prices[0],
            crossed_prices[1],
            book.best_bid_price().as_double(),
            book.best_ask_price().as_double(),
        )

    def _try_active_uncross(self, iid: str, book: OrderBook) -> bool:
        """
        DATA-04: try dYdX's own non-destructive fix first -- drop only the stale
        level(s), never the whole book -- before falling back to the existing
        WARNING/timer/CRITICAL/_resync_book machinery. The cap bounds a
        pathological/many-levels-deep cross; a genuine crossed-book episode is
        normally one or two levels. Returns True once the book is no longer crossed.
        """
        for _ in range(_UNCROSS_MAX_STEPS):
            if not self._uncross_step(iid, book):
                return False
            new_bid, new_ask = book.best_bid_price(), book.best_ask_price()
            if new_bid is None or new_ask is None or new_bid.as_double() < new_ask.as_double():
                self._crossed_since_ns.pop(iid, None)
                self._crossed_prices.pop(iid, None)
                return True
        return False

    async def _escalate_persistent_crossed_book(self, iid: str, book: OrderBook, now_ns: int) -> None:
        """
        Logs a WARNING for a crossed book active uncrossing couldn't resolve, and
        escalates to CRITICAL + a forced resync once it's persisted past
        _CROSSED_RESYNC_NS (DATA-02/DATA-03: resync is a last resort, not a first
        response).

        Diagnostic: _last_book_update_ns refreshes on a delta for EITHER side, so it
        can't tell "bid deltas stopped arriving" apart from "both sides keep updating
        and are genuinely crossed". These per-side timestamps can.
        """
        bid_stale_s = (now_ns - self._last_bid_delta_ns.get(iid, 0)) / 1e9
        ask_stale_s = (now_ns - self._last_ask_delta_ns.get(iid, 0)) / 1e9
        logger.warning(
            "Crossed book for %s (bid=%.6f >= ask=%.6f) — skipping snapshot "
            "[last bid delta %.1fs ago, last ask delta %.1fs ago]",
            iid,
            book.best_bid_price().as_double(),
            book.best_ask_price().as_double(),
            bid_stale_s,
            ask_stale_s,
        )
        crossed_since = self._crossed_since_ns.setdefault(iid, now_ns)
        self._crossed_prices.setdefault(
            iid, (book.best_bid_price().as_double(), book.best_ask_price().as_double())
        )
        if now_ns - crossed_since <= _CROSSED_RESYNC_NS:
            return
        # Persisted past the grace window this system already uses as its tolerance
        # for a genuine sub-second touch (see _CROSSED_RESYNC_NS's definition) --
        # confirmed local desync (a lost delta our reconstruction never recovers from
        # on its own), not bad data from dYdX. Not gated to "once ever": _resync_book
        # (below) resets crossed_since on every call, so for a book that keeps failing
        # to recover this naturally repeats roughly every _CROSSED_RESYNC_NS, not
        # every _second_loop tick -- an unresolved CRITICAL incident should keep
        # alerting, not go silent after a single log line.
        critical_logger.critical(
            json.dumps(
                {
                    "instrument_id": iid,
                    "reason": "steady_state_crossed_book",
                    "best_bid": book.best_bid_price().as_double(),
                    "best_ask": book.best_ask_price().as_double(),
                    "crossed_duration_ns": now_ns - crossed_since,
                    "bid_delta_stale_s": bid_stale_s,
                    "ask_delta_stale_s": ask_stale_s,
                    "ts_event_ns": now_ns,
                }
            )
        )
        await self._resync_book(iid)

    async def _handle_crossed_book(self, iid: str, book: OrderBook, now_ns: int) -> bool:
        """
        Detect/escalate/resolve a crossed book for one instrument. Returns True if
        currently crossed (caller should skip this tick's snapshot for it).

        Extracted from _second_loop to keep that loop's own cyclomatic complexity down --
        this is a self-contained state machine (crossed / resolved / stuck-past-grace),
        not something _second_loop's per-tick iteration needs to inline. Split further
        into _handle_uncrossed_book / _try_active_uncross /
        _escalate_persistent_crossed_book to keep each stage under this project's
        ~30-line guideline (READ-01).
        """
        if book.best_bid_price().as_double() < book.best_ask_price().as_double():
            self._handle_uncrossed_book(iid, book, now_ns)
            return False
        if self._try_active_uncross(iid, book):
            return False
        await self._escalate_persistent_crossed_book(iid, book, now_ns)
        return True

    async def _second_loop(self) -> None:
        """Sample L2 book at snapshot_interval_seconds; raw levels + trade volume only — signals computed on read."""
        while not self._stop.is_set():
            await asyncio.sleep(self._config.snapshot_interval_seconds)
            now_ns = time.time_ns()

            if self._last_second_loop_tick_ns is not None:
                expected_ns = int(self._config.snapshot_interval_seconds * 1e9)
                lag_ns = now_ns - self._last_second_loop_tick_ns - expected_ns
                if lag_ns > _SECOND_LOOP_LAG_WARN_NS:
                    logger.warning(
                        "_second_loop tick arrived %.1fs late (expected every %.1fs) -- "
                        "event loop was busy; crossed-book detection/resync was not "
                        "running during this gap",
                        lag_ns / 1e9,
                        self._config.snapshot_interval_seconds,
                    )
            self._last_second_loop_tick_ns = now_ns

            batch: list[DydxSecondSnapshot] = []
            for iid in {e.id for e in self._config.instruments}:
                book = self._live_books.get(iid)
                if book is None:
                    continue
                if book.best_bid_price() is None or book.best_ask_price() is None:
                    continue
                # Skip crossed/touched book — can occur briefly during reconnect snapshot replay
                if await self._handle_crossed_book(iid, book, now_ns):
                    continue

                # Staleness guard: skip if no OrderBookDeltas have arrived recently.
                # During WS reconnect recovery the book retains its pre-reconnect state
                # until the re-subscription snapshot arrives. Emitting that stale state
                # produces a flatline on the coin chart. See _STALE_BOOK_NS for threshold.
                last_book_update_ns = self._last_book_update_ns.get(iid, 0)
                if now_ns - last_book_update_ns > _STALE_BOOK_NS:
                    logger.warning(
                        "Stale book for %s (no OrderBookDeltas for %.1fs) — skipping snapshot",
                        iid,
                        (now_ns - last_book_update_ns) / 1e9,
                    )
                    continue

                bid_levels = book.bids()[:BOOK_DEPTH]
                ask_levels = book.asks()[:BOOK_DEPTH]
                buy_volume = self._second_buy_volume.pop(iid, 0.0)
                sell_volume = self._second_sell_volume.pop(iid, 0.0)
                buy_count = self._second_buy_count.pop(iid, 0)
                sell_count = self._second_sell_count.pop(iid, 0)
                # Absent from these dicts means no trade occurred this second --
                # .pop(iid, None) yields None, not a fabricated price.
                open_price = self._second_open_price.pop(iid, None)
                high_price = self._second_high_price.pop(iid, None)
                low_price = self._second_low_price.pop(iid, None)
                close_price = self._second_close_price.pop(iid, None)

                snapshot = DydxSecondSnapshot(
                    instrument_id=InstrumentId.from_str(iid),
                    bid_prices=[lv.price.as_double() for lv in bid_levels],
                    bid_sizes=[lv.size() for lv in bid_levels],
                    ask_prices=[lv.price.as_double() for lv in ask_levels],
                    ask_sizes=[lv.size() for lv in ask_levels],
                    buy_volume=buy_volume,
                    sell_volume=sell_volume,
                    buy_count=buy_count,
                    sell_count=sell_count,
                    open_price=open_price,
                    high_price=high_price,
                    low_price=low_price,
                    close_price=close_price,
                    ts_event=now_ns,
                    ts_init=now_ns,
                )
                batch.append(snapshot)
                self._on_data(snapshot)  # routes to buffer → Parquet flush

            await _publish_snapshot_batch(self._redis, batch)

    async def _prune_loop(self) -> None:
        """Prune non-pinned instruments' catalog data, and any per-coin raw-delta retention."""
        while not self._stop.is_set():
            # Recomputed each iteration so a hot-reloaded retain_hours takes effect promptly.
            interval = _prune_interval_seconds(
                self._config.non_config_retain_hours, self._delta_retain_hours
            )
            await asyncio.sleep(interval)
            catalog_path = str(Path(self._config.catalog_path).resolve())
            non_pinned = _prune_candidates(self._config.instruments, self._known_markets)
            freed = 0
            for iid in non_pinned:
                freed += prune_instrument(catalog_path, iid, self._config.non_config_retain_hours)
            if freed:
                logger.info(
                    f"Pruned {freed / 1024 / 1024:.1f} MB from {len(non_pinned)} non-pinned instruments"
                )

            delta_freed = _prune_delta_retention(catalog_path, self._delta_retain_hours)
            if delta_freed:
                logger.info(
                    f"Pruned {delta_freed / 1024 / 1024:.1f} MB of raw order-book deltas (per-coin retention)"
                )

    async def _watchdog_loop(self) -> None:
        """
        Notify when every live-tier instrument's book has gone stale. See OBS-01 and
        _WATCHDOG_STALE_NS: this deployment runs unattended behind an SSH tunnel, so
        a frozen feed needs to page someone rather than wait to be noticed on the dashboard.
        """
        while not self._stop.is_set():
            await asyncio.sleep(_WATCHDOG_CHECK_SECONDS)
            now_ns = time.time_ns()
            if now_ns - self._watchdog_started_ns < _WATCHDOG_STARTUP_GRACE_NS:
                continue

            live = {e.id for e in self._config.instruments}
            if not live:
                continue
            is_stale = all(
                now_ns - self._last_book_update_ns.get(iid, 0) > _WATCHDOG_STALE_NS for iid in live
            )

            message, down_since_ns, reminder_ns = _watchdog_transition(
                now_ns, is_stale, self._watchdog_down_since_ns, self._watchdog_last_reminder_ns
            )
            self._watchdog_down_since_ns = down_since_ns
            self._watchdog_last_reminder_ns = reminder_ns
            if message is not None:
                await asyncio.to_thread(_notify, message)

    async def run(self) -> None:
        self._redis = aioredis.Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"))

        instruments = await self._client.fetch_instruments()
        instruments_by_id = {i.id.value: i for i in instruments}

        self._catalog.write_data(instruments_from_pyo3(list(instruments_by_id.values())))

        loop = asyncio.get_running_loop()
        await self._client.connect(loop, list(instruments_by_id.values()))
        await self._client.subscribe_markets()

        # config.toml's [[instruments]] list is the sole subscribe source (Story 6.1) --
        # no classify_liquidity call here; that's only used by the pin_top_liquid
        # control action and _status_loop's informational display label now.
        known = set(instruments_by_id)
        configured = {e.id for e in self._config.instruments}
        to_subscribe = configured & known
        unknown = configured - known
        if unknown:
            logger.warning("Configured instruments not found on dYdX, skipping: %s", sorted(unknown))

        for iid in sorted(to_subscribe):
            await self._subscribe(iid)

        logger.info(f"Started: {len(to_subscribe)} subscribed")

        tasks = [
            asyncio.create_task(self._ingest_loop()),
            asyncio.create_task(self._flush_loop()),
            asyncio.create_task(self._reload_config_loop()),
            asyncio.create_task(self._open_interest_loop()),
            asyncio.create_task(self._status_loop()),
            asyncio.create_task(self._control_loop()),
            asyncio.create_task(self._prune_loop()),
            asyncio.create_task(self._second_loop()),
            asyncio.create_task(self._watchdog_loop()),
            # Raw-WS debug feed (Story 5.1) for the incident-report subsystem below --
            # a permanent feature, not scoped to any one investigation. Rust's file
            # logger only flushes its BufWriter to disk on an explicit Sync event --
            # without this, [WS_RAW] lines sit in memory forever.
            asyncio.create_task(_ws_raw_debug_flush_loop()),
        ]
        stop_task = asyncio.create_task(self._stop.wait())

        try:
            # Each loop already retries recoverable per-iteration errors internally (network
            # blips, etc). If one still dies, that's an unexpected bug -- surface it here so
            # the top-level restart loop in main() can do a full clean reconnect rather than
            # silently running degraded forever.
            done, _pending = await asyncio.wait(
                [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is not stop_task:
                    task.result()  # re-raises if the task died with an exception
        finally:
            stop_task.cancel()
            for task in tasks:
                task.cancel()
            await self._client.disconnect()
            await self._flush_once()
            if self._redis is not None:
                await self._redis.aclose()

    def stop(self) -> None:
        self._stop.set()


# Raw-WS debug feed (Story 5.1) for the incident-report subsystem below -- a
# permanent feature, not scoped to any one investigation.
async def _ws_raw_debug_flush_loop() -> None:
    while True:
        await asyncio.sleep(2.0)
        nautilus_pyo3.logging_sync_to_disk()


# ---------------------------------------------------------------------------
# Incident reporting (Story 5.1): any WARNING+ log line, from any logger in this
# process, gets a permanent human-readable report snapshotting the relevant
# [WS_RAW] rolling-buffer window -- turns "grep a 500MB debug file by hand" into
# an automatic, standing capability. Generic by design: classification is a
# best-effort heuristic over the already-formatted message text (no changes
# needed at existing logger.warning()/critical() call sites), so any *new*
# warning added later gets this for free too.
# ---------------------------------------------------------------------------

_WS_RAW_LOG_DIR = Path("/tmp/nautilus_logs")  # noqa: S108 -- deliberate: ephemeral rolling
# buffer inside a single-purpose container (docker-compose's `collector` service, PID 1),
# not a shared multi-tenant host -- no symlink/race risk this rule guards against applies.
_INCIDENT_DIR = Path("/app/incident_reports")

# Debounce window per (incident_type, instrument) -- a crossed book logs a fresh
# WARNING on every _second_loop tick while it persists (up to ~3 before CRITICAL
# escalation); without this, one ongoing incident would produce a report per tick.
_INCIDENT_DEBOUNCE_NS: int = 10_000_000_000  # 10 seconds

# How far back the raw-evidence window reaches from the triggering log line.
_INCIDENT_LOOKBACK_NS: int = 10_000_000_000  # 10 seconds

# Bounded like the other two log sinks (docker json-file: 400MB, ws_raw_debug: 500MB) --
# unlike those, nothing was capping this directory before, so it grew forever.
_INCIDENT_DIR_MAX_BYTES: int = 200_000_000  # 200MB, oldest files pruned past this
# Extraction is deferred this long after the trigger so the window also captures
# whatever resolves the incident (e.g. the delete that un-crosses a book), not
# just the run-up to it.
_INCIDENT_LOOKAHEAD_DELAY_S: float = 2.0

_IID_RE = re.compile(r"\b([A-Z0-9]+-USD-PERP\.DYDX)\b")

# _write_incident_report runs via asyncio.to_thread, and the default executor has
# multiple worker threads -- two incidents close together (different type/instrument,
# so neither is debounced) can each land on their own thread and call
# _prune_incident_reports() at the same time. Without this, both glob() the directory
# independently and can race to stat/unlink the same file: one thread deletes it after
# the other has already listed it but before that thread's own stat() call, raising
# FileNotFoundError (seen in production). Only _prune_incident_reports touches this
# directory's files, so a plain lock around the whole function is sufficient --
# nothing else can delete out from under it once serialized.
_INCIDENT_PRUNE_LOCK = threading.Lock()


def _classify_incident(message: str) -> tuple[str, str | None]:
    """Best-effort (incident_type, instrument_id) from an already-formatted log message."""
    try:
        payload = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict) and "reason" in payload:
        # CRITICAL escalations already log structured JSON (see _second_loop) -- exact,
        # no heuristics needed.
        return str(payload["reason"]), payload.get("instrument_id")

    iid_match = _IID_RE.search(message)
    iid = iid_match.group(1) if iid_match else None
    if "Crossed book" in message:
        return "crossed_book", iid
    if "Stale book" in message:
        return "stale_book", iid
    if "_second_loop tick arrived" in message:
        return "second_loop_lag", None
    if "Resyncing" in message:
        return "resync", iid
    return "unclassified", iid


def _ns_to_iso(ns: int) -> str:
    """
    Nanosecond-precision UTC ISO string matching handler.rs's [WS_RAW] line prefix
    format exactly (same width), so plain string comparison is chronologically correct.
    """
    dt = datetime.fromtimestamp(ns // 1_000_000_000, tz=UTC)
    frac_ns = ns % 1_000_000_000
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{frac_ns:09d}Z"


def _scan_ws_raw_window(ticker: str, start_ns: int, end_ns: int) -> list[str]:
    """
    Blocking file I/O -- always call via asyncio.to_thread. Scans every rotated file
    currently present (bounded by _INCIDENT_DEBOUNCE_NS keeping incidents infrequent, and
    the rolling buffer itself bounded to ~500MB) rather than tracking per-file byte ranges
    -- simplest thing that works, not a measured bottleneck.
    """
    start_ts = _ns_to_iso(start_ns)
    end_ts = _ns_to_iso(end_ns)
    needle = f'"id":"{ticker}"'
    matches: list[str] = []
    for path in sorted(_WS_RAW_LOG_DIR.glob("ws_raw_debug_*.log")):
        try:
            with path.open("r", errors="replace") as f:
                for line in f:
                    if needle not in line:
                        continue
                    ts = line.split(" ", 1)[0]
                    if start_ts <= ts <= end_ts:
                        matches.append(line)
        except OSError:
            continue
    return matches


def _write_incident_report(
    incident_type: str,
    iid: str | None,
    level: str,
    logger_name: str,
    message: str,
    trigger_ns: int,
) -> str:
    """Blocking -- always call via asyncio.to_thread."""
    _INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
    start_ns = trigger_ns - _INCIDENT_LOOKBACK_NS
    end_ns = trigger_ns + int(_INCIDENT_LOOKAHEAD_DELAY_S * 1e9)
    ticker = iid.split("-PERP")[0] if iid else None
    lines = _scan_ws_raw_window(ticker, start_ns, end_ns) if ticker else []

    ts_str = _ns_to_iso(trigger_ns)
    safe_iid = iid or "system"
    path = _INCIDENT_DIR / f"{incident_type}_{safe_iid}_{trigger_ns // 1_000_000_000}.log"
    with path.open("w") as f:
        f.write("=== dYdX Collector Incident Report ===\n")
        f.write(f"Time: {ts_str}\n")
        f.write(f"Level: {level}\n")
        f.write(f"Logger: {logger_name}\n")
        f.write(f"Type: {incident_type}\n")
        f.write(f"Instrument: {iid or '-'}\n")
        f.write(f"Message: {message}\n\n")
        if ticker:
            f.write(f"--- Raw WS evidence ({ticker}, {len(lines)} messages) ---\n")
            f.writelines(lines)
        else:
            f.write(
                "--- No instrument identified in this message; no raw WS evidence attached ---\n"
            )
    _prune_incident_reports()
    return str(path)


def _prune_incident_reports() -> None:
    """
    Delete oldest incident reports until the directory is back under the size cap.

    Runs on a worker thread (via asyncio.to_thread) -- see _INCIDENT_PRUNE_LOCK for why
    this must be serialized against concurrent calls from other incident writes.
    """
    with _INCIDENT_PRUNE_LOCK:
        files = sorted(_INCIDENT_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= _INCIDENT_DIR_MAX_BYTES:
                break
            total -= f.stat().st_size
            f.unlink()


async def _report_incident(
    incident_type: str, iid: str | None, level: str, logger_name: str, message: str, trigger_ns: int
) -> None:
    await asyncio.sleep(_INCIDENT_LOOKAHEAD_DELAY_S)
    report_path = await asyncio.to_thread(
        _write_incident_report, incident_type, iid, level, logger_name, message, trigger_ns
    )
    logger.info("Incident report written [%s/%s]: %s", incident_type, iid or "-", report_path)


class _IncidentHandler(logging.Handler):
    """
    Attached to the root logger at WARNING level -- catches every current and future
    warning/error/critical in this process (both `logger`/__main__ and `critical_logger`
    propagate to root by default), without needing changes at each call site.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(level=logging.WARNING)
        # Captured once, not looked up in emit(): _notify() (run via asyncio.to_thread
        # from _watchdog_loop) calls logger.exception() on its own notification-failure
        # path, which reaches this handler from a plain ThreadPoolExecutor worker thread
        # -- one with no running event loop of its own. asyncio.get_running_loop() would
        # raise there, silently dropping that incident report (swallowed below) and
        # spamming stderr. Storing the loop up front and scheduling with
        # run_coroutine_threadsafe (below) works correctly from either the loop's own
        # thread or any other thread.
        self._loop = loop or asyncio.get_running_loop()
        self._last_report_ns: dict[tuple[str, str | None], int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            incident_type, iid = _classify_incident(message)
            key = (incident_type, iid)
            now_ns = time.time_ns()
            if now_ns - self._last_report_ns.get(key, 0) < _INCIDENT_DEBOUNCE_NS:
                return
            self._last_report_ns[key] = now_ns
            asyncio.run_coroutine_threadsafe(
                _report_incident(
                    incident_type, iid, record.levelname, record.name, message, now_ns
                ),
                self._loop,
            )
        except Exception:
            # logging.Handler's own documented convention: emit() must never propagate --
            # a broken incident report must not crash the collector or the logging system
            # it's attached to. handleError() (not a bare pass) surfaces it to stderr.
            self.handleError(record)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger().addHandler(_IncidentHandler())
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
        directory=str(_WS_RAW_LOG_DIR),
        file_name="ws_raw_debug",
        # Bounded rolling buffer, not a growing archive: [WS_RAW] is ~1MB/s, so 250MB x 2
        # backups is ~500MB nominal / ~12 minutes of retention -- a debugging aid,
        # self-cleaning by design. It doesn't need to survive long: _IncidentHandler
        # (below) snapshots the relevant window into a permanent incident report the
        # moment something WARNING+ worthy happens, well within this retention window (a
        # fixed 100MB x 3 attempt was measured too short at ~6 min for a human to notice
        # and go check manually -- this one only needs to outlast
        # _INCIDENT_LOOKAHEAD_DELAY_S, not a human).
        #
        # Rotation size, not backup count, is what was raised here (was 50MB x 10, same
        # ~500MB nominal budget): nautilus_trader's file writer unconditionally
        # `eprintln!`s "Rotated log file..." on every rotation regardless of configured
        # log level (crates/common/src/logging/writer.rs's rotate_file(), not routed
        # through the `log` crate at all) -- that fired every ~50s at the old size, purely
        # docker-logs noise nothing in this codebase reads (_scan_ws_raw_window globs
        # every rotated file, never depends on which one is "current"). Fewer, bigger
        # files cut that print's frequency ~5x for the same nominal retention budget.
        file_rotate=(250_000_000, 2),
    )
    config = load_config(CONFIG_PATH)

    quarantine_corrupt_parquet(config.catalog_path)

    shutting_down = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutting_down.set)
    loop.add_signal_handler(signal.SIGTERM, shutting_down.set)

    backoff_seconds = 1.0
    while not shutting_down.is_set():
        collector = Collector(config)
        watcher = asyncio.create_task(_stop_on_shutdown(shutting_down, collector))
        try:
            await collector.run()
            backoff_seconds = 1.0  # clean stop (signal) -- reset for any future crash
        except Exception:
            logger.exception(f"Collector crashed, restarting in {backoff_seconds:.0f}s")
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60.0)
        finally:
            watcher.cancel()


async def _stop_on_shutdown(shutting_down: asyncio.Event, collector: Collector) -> None:
    await shutting_down.wait()
    collector.stop()


if __name__ == "__main__":
    asyncio.run(main())
