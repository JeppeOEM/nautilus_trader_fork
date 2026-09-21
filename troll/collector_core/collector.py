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
Venue-neutral market-data collector core (Story 22.1): own asyncio loop, ingest queue,
buffer and flush timer over a duck-typed venue client, writing to the shared
`ParquetDataCatalog` via Nautilus's own `write_data()`. No TradingNode/Strategy/DataEngine.

Extracted from the (identical) Bybit and Hyperliquid collectors, plus dYdX's venue-neutral
integrity guards: stale-trade age filter + bounded trade_id dedup (DATA-06), stale-book
skip with accumulator discard (DATA-01), crossed-book skip + ledger with resync as a
fallback only (DATA-03), `ohlc_outside_book` canary, candle-store feed, `snapshots:raw` Redis
publish, the `_second_loop` lag canary and the OBS-01 watchdog.

Trades (story 22.13): every accepted `TradeTick` is both kept for the live second (folded once
per sample by `collector_core.fold.fold_trades`) and archived raw to `data/trade_tick/<iid>/`
with both clocks untouched (`ts_event` = venue, `ts_init` = arrival). The live snapshot is
provisional and arrival-timed; `collector_core.rebuild_seconds` re-derives closed days from the
archive on exchange time.

Client contract (duck-typed -- this docstring is the contract, there is no base class):

    fetch_instruments() -> list          raw pyo3 instruments; written to the catalog via
                                         `instruments_from_pyo3` and passed to connect()
    connect(loop, instruments) -> None   open the WS; deliver every decoded message to the
                                         `on_data` callable the client was built with
    disconnect() -> None
    subscribe(iid: str) -> None          trades + book (+ whatever else the venue offers)
    unsubscribe(iid: str) -> None        one WS unsubscribe per topic subscribed
    subscribe_global() -> None           OPTIONAL: venue-wide channels (e.g. dYdX markets)
    fetch_book_levels(iid: str)          OPTIONAL: a REST book snapshot as (bids, asks), each a
        -> tuple[list, list]             best-first list of (price, size) floats; enables the
                                         periodic cross-check against the live book (22.5).
    resync_orderbook(iid: str) -> None   OPTIONAL: force a fresh book snapshot. Only a
                                         venue whose local book can drift (delta stream)
                                         should expose it; a full-snapshot venue must not.

The client must call `on_data` from the event loop (call_soon_threadsafe) and `on_data`
itself is O(1): it only enqueues, `_ingest_loop` does the real work.

Snapshots are `DydxSecondSnapshot` (a venue-neutral schema despite its name -- moved in
story 22.3) so data_api serves every venue's ids with zero per-route code.
"""

import asyncio
import bisect
import json
import logging
import math
import os
import shutil
import signal
import time
import urllib.request
from collections import defaultdict
from collections import deque
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import redis.asyncio as aioredis
from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns
from ml_signals.catalog_stats import query_second_ohlc

from collector_core.archive_gaps import ARRIVAL_MARGIN_NS
from collector_core.archive_gaps import record_gap
from collector_core.book_check import persistent
from collector_core.book_check import top_levels_mismatch
from collector_core.config import CoreConfig
from collector_core.fold import fold_trades
from collector_core.integrity import ohlc_outside_book
from collector_core.second_snapshot import BOOK_DEPTH
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import instruments_from_pyo3
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_QUARANTINE_DIRNAME = "_quarantine"
_INGEST_YIELD_EVERY = 64
_IMPOSSIBLE_LOG_EVERY_NS = 60_000_000_000  # one ERROR per instrument per minute, not per second

# OBS-01: zero book updates across all instruments for 30s+ is a pipeline failure, not a
# quiet market. Deployed unattended, so this pushes a notification rather than relying on
# someone noticing a frozen chart.
_WATCHDOG_CHECK_SECONDS: float = 30.0
_WATCHDOG_STALE_NS: int = 30_000_000_000
_WATCHDOG_STARTUP_GRACE_NS: int = 60_000_000_000  # subscriptions need time to establish
_WATCHDOG_REMINDER_NS: int = 600_000_000_000  # re-notify at most every 10 min while down

# A flush carries back a TradeTick batch's newest-`ts_init` group while it is younger than this:
# every adapter stamps one `ts_init` per WS message, so a message's trades can straddle the flush,
# and `write_data` refuses a file whose `[first, last]` `ts_init` interval touches an existing one
# -- the next flush would lose its whole batch on the tie (audit, flush tie guard).
_TRADE_CARRY_NS: int = 5_000_000_000

# _second_loop staleness canary: if its wakeup arrives this much later than the configured
# interval, the event loop was busy and the crossed-book detection/resync guard was silently
# not running for that gap -- surface it rather than let it look like a quiet market.
_SECOND_LOOP_LAG_WARN_NS: int = 2_000_000_000

# Workaround: ParquetDataCatalog.write_data() (pinned nautilus_trader 1.229.0) has no
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


def quarantine_corrupt_parquet(catalog_path: str, instrument_ids: Iterable[str]) -> None:
    """
    Move any unreadable .parquet file (e.g. left by a mid-write crash) for *this collector's*
    instruments out of the way.

    Runs once at process start, before any new writes. A half-written file from a killed
    process would otherwise sit forever next to good data and can break catalog reads or
    consolidation. Scoped to `data/<type>/<iid>/` for the given ids: every venue's collector
    shares one catalog root, and `ParquetDataCatalog` writes straight to the final path, so
    scanning a sibling's directories would quarantine a file that is merely mid-write. Only
    this process writes its own ids' directories, and it is not writing yet when this runs
    (the shared instrument-definition tables are left alone for the same reason).
    Known limit: full scan of this venue's files on every start; if that grows into the
    hundreds of thousands of files, switch to only checking files newer than the last clean
    shutdown.
    """
    root = Path(catalog_path).resolve()
    quarantine_root = root / _QUARANTINE_DIRNAME
    for iid in instrument_ids:
        for path in root.glob(f"data/*/{iid}/*.parquet"):
            if _is_readable_parquet(path):
                continue
            dest = quarantine_root / path.relative_to(root)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
            except OSError as e:
                # Must not escape: this runs before run_forever's restart loop.
                error_ledger.record("collector.corrupt_parquet", f"could not quarantine {path}", e)
                continue
            error_ledger.record(
                "collector.corrupt_parquet", f"corrupt parquet file quarantined: {path} -> {dest}"
            )
            if path.parent.parent.name == "trade_tick":
                _mark_quarantined_trades(str(root), iid, path)


def _mark_quarantined_trades(catalog_path: str, iid: str, path: Path) -> None:
    """
    Record the quarantined trade file's span as an archive gap, so the nightly rebuild keeps the
    live values of the rows those trades were folded into. The name spans the batch's `ts_init`;
    a row folding them is sampled after arrival, so `to` is widened by the arrival margin. An
    unparseable name marks nothing and is ledgered: that file was never written by the collector.
    """
    try:
        first, _, last = path.stem.partition("_")
        from_ns, to_ns = _stamp_to_ns(first), _stamp_to_ns(last) + ARRIVAL_MARGIN_NS
    except ValueError as e:
        error_ledger.record(
            "collector.corrupt_parquet", f"no span in {path.name}; no gap marked", e
        )
        return
    record_gap(catalog_path, iid, from_ns, to_ns, "quarantined", 0)


def _is_readable_parquet(path: Path) -> bool:
    try:
        pq.ParquetFile(path)
    except Exception:
        return False
    return True


def _watchdog_transition(
    now_ns: int,
    is_stale: bool,
    down_since_ns: int | None,
    last_reminder_ns: int,
    name: str = "collector",
) -> tuple[str | None, int | None, int]:
    """
    Pure state-machine step for the feed watchdog: (message_or_None, down_since_ns, last_reminder_ns).

    Kept separate from the asyncio loop and the notify transport so the alerting/
    debounce logic is unit-testable without mocking network calls.
    """
    if is_stale:
        if down_since_ns is None:
            return (
                f"{name}: all live instruments' order books have gone stale "
                "(no OrderBookDeltas for 30s+) — feed may be down",
                now_ns,
                now_ns,
            )
        if now_ns - last_reminder_ns > _WATCHDOG_REMINDER_NS:
            down_for_s = (now_ns - down_since_ns) / 1e9
            return (
                f"{name}: still down, no book updates for {down_for_s:.0f}s",
                down_since_ns,
                now_ns,
            )
        return (None, down_since_ns, last_reminder_ns)

    if down_since_ns is not None:
        down_for_s = (now_ns - down_since_ns) / 1e9
        return (f"{name}: recovered after {down_for_s:.0f}s", None, 0)

    return (None, None, last_reminder_ns)


def _notify(message: str, title: str = "collector") -> None:
    """POST to a ntfy.sh-compatible topic URL. Log CRITICAL if WATCHDOG_NTFY_URL isn't set."""
    url = os.environ.get("WATCHDOG_NTFY_URL")
    if not url:
        logger.critical(message)
        return
    request = urllib.request.Request(  # noqa: S310 (fixed, operator-configured URL)
        url,
        data=message.encode(),
        headers={"Title": title},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass
    except Exception:  # a malformed URL raises ValueError; the watchdog loop must not die
        logger.exception("Watchdog notification failed")


# Parquet is flushed this many seconds past each interval boundary (:02 for the default 60 s), so a
# minute that just closed is in the archive -- and, right after it, in the candle store -- ~2 s later.
_FLUSH_PHASE_S = 2.0
_CATCH_UP_MAX_NS = 86_400 * 1_000_000_000


def _seconds_until_next_flush(now: float, interval: float) -> float:
    """Return seconds from `now` (epoch) to the next wall-clock flush; always in (0, interval]."""
    return interval - (now - _FLUSH_PHASE_S) % interval


def _next_sample_at(now: float, interval: float, last_tick: float | None) -> float:
    """
    Next sample time (epoch seconds): the first `k * interval + interval / 2` strictly after
    `now`, and never in the interval bucket (`floor(t / interval)`) `last_tick` already sampled.

    A plain `sleep(interval)` after the work drifts by the work's duration every tick and skips a
    whole floor second every few hundred seconds, orphaning that second's trades. The mid-interval
    phase leaves half an interval of margin both ways, so an early wake-up still lands in its own
    bucket and a late one is followed by the next free bucket, never a second tick in the same one.
    """
    k = math.floor((now - interval / 2) / interval) + 1
    if last_tick is not None:
        k = max(k, math.floor(last_tick / interval) + 1)
    return k * interval + interval / 2


def _split_open_ts_init_group(
    items: list[Any], now_ns: int, queue_pending: bool
) -> tuple[list[Any], list[Any]]:
    """
    Split a `ts_init`-sorted batch into (write now, carry to the next flush): the group sharing
    the newest `ts_init` is carried while it is younger than `_TRADE_CARRY_NS`, or while the
    ingest queue still holds messages (under lag, the rest of that WS message can sit there for
    longer than any age bound).
    """
    newest = items[-1].ts_init
    if now_ns - newest >= _TRADE_CARRY_NS and not queue_pending:
        return items, []
    cut = bisect.bisect_left([d.ts_init for d in items], newest)
    return items[:cut], items[cut:]


async def _publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """
    Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:raw.

    Empty batches are silently dropped. Publish failures are logged and swallowed —
    missing one tick is acceptable per the architecture (the Parquet write is durable).
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    try:
        await redis_client.publish("snapshots:raw", payload)
    except Exception as e:
        logger.warning("Redis publish failed: %s", e)


_CROSSCHECK_CONFIRM_SECONDS = 2.0  # gap before re-comparing a mismatch (story 22.5)


class Collector:
    """
    One venue's collector: `config` thresholds, a duck-typed `client` (contract in the
    module docstring) and `extra_loops` -- no-arg coroutine functions started as tasks
    alongside the core loops (e.g. a REST open-interest poll).
    """

    def __init__(
        self,
        config: CoreConfig,
        client: Any,
        extra_loops: tuple[Callable[[], Awaitable[None]], ...] = (),
    ) -> None:
        self._config = config
        self._client = client
        self._extra_loops = extra_loops
        catalog_path = Path(config.catalog_path).resolve()
        catalog_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(str(catalog_path))
        # Derived candle store for the UI (the catalog stays the archive): this collector is its
        # single writer, one file per venue (compose sets CANDLES_DB_PATH). Rebuildable via build_candles.
        self._candle_db = candle_store.connect_rw(
            os.environ.get("CANDLES_DB_PATH", str(catalog_path.parent / "candles" / "candles.db"))
        )

        self._buffer: dict[tuple[type, str], list[Any]] = defaultdict(list)
        # Unbounded: a real overflow would mean the process can't keep up with the
        # exchange at all -- revisit with a maxsize + drop policy only if observed.
        self._ingest_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._redis: aioredis.Redis | None = None
        self._stop = asyncio.Event()

        self._live_books: dict[str, OrderBook] = {}
        self._last_book_update_ns: dict[str, int] = {}
        # Any WS message at all (story 22.5's feed-liveness gate) -- REST-polled data must
        # therefore go straight to `_buffer`, never through `_on_data`/`_process_data`.
        self._last_feed_message_ns: int = 0
        # First-observed-crossed time per instrument; cleared once seen uncrossed.
        self._crossed_since_ns: dict[str, int] = {}
        # Instruments whose forced resync failed after the local book was dropped: retried
        # from _sample_tick, otherwise they would stay bookless forever (the Rust client's
        # reconnect only resubscribes what it still holds).
        self._resync_pending: set[str] = set()
        self._last_no_book_log_ns: dict[str, int] = {}
        self._book_crosscheck_mismatches: defaultdict[str, int] = defaultdict(int)

        # This sample interval's accepted trades per instrument, folded once by `_sample_tick`
        # (`fold_trades`: exact integer sums). An empty/absent list means no trade this second,
        # distinct from a trade at price 0 -- the fold yields None OHLC, never a fabricated price.
        self._second_trades: defaultdict[str, list[TradeTick]] = defaultdict(list)

        # DATA-06 guards: subscribe-time history dropped by age; reconnect replays (still
        # "fresh" after a short outage) dropped by bounded trade_id dedup. Both counted and
        # reported every flush by _report_stale_trades -- never silent (DATA-05).
        self._stale_trades_dropped: defaultdict[str, int] = defaultdict(int)
        self._duplicate_trades_dropped: defaultdict[str, int] = defaultdict(int)
        self._deltas_before_snapshot_dropped: defaultdict[str, int] = defaultdict(int)
        self._seen_trade_ids: defaultdict[str, deque[str]] = defaultdict(
            lambda: deque(maxlen=config.seen_trade_ids)
        )
        self._seen_trade_id_set: defaultdict[str, set[str]] = defaultdict(set)
        self._last_impossible_log_ns: dict[str, int] = {}

        self._last_second_loop_tick_ns: int | None = None
        if config.snapshot_interval_seconds != 1.0:
            # Not refused (config validation is unchanged), but loud: the nightly rebuild maps
            # trades to rows by floor second, which assumes one row per second.
            error_ledger.record(
                "collector.cadence",
                f"snapshot_interval_seconds={config.snapshot_interval_seconds}, not 1.0: "
                "rebuild_seconds' floor-second mapping assumes 1 s rows",
            )

        self._watchdog_started_ns: int = time.time_ns()
        self._watchdog_down_since_ns: int | None = None
        self._watchdog_last_reminder_ns: int = 0

    # -- hooks a venue subclass may override -------------------------------------------------

    def _instrument_ids(self) -> Iterable[str]:
        return self._config.instruments

    def _clear_book_state(self, iid: str) -> None:
        """
        Drop per-instrument book tracking state (on resync/unsubscribe).

        Otherwise a later resubscribe reads a stale `_crossed_since_ns` and fires an
        unwarranted resync.
        """
        self._live_books.pop(iid, None)
        self._crossed_since_ns.pop(iid, None)

    def _apply_deltas(self, iid: str, deltas: OrderBookDeltas) -> None:
        if not deltas.deltas:
            return
        book = self._live_books.get(iid)
        if book is None:
            if not deltas.deltas[0].is_clear:
                # A book must start from a snapshot (Clear + levels -- both the Bybit and
                # Hyperliquid adapters emit one). After a resync, or before the first
                # snapshot, in-flight incremental deltas would otherwise build a shallow
                # book that looks uncrossed and passes every gate. Counted and reported
                # each flush by _report_stale_trades -- never silent.
                self._deltas_before_snapshot_dropped[iid] += 1
                return
            book = self._live_books[iid] = OrderBook(deltas.instrument_id, BookType.L2_MBP)
        for delta in deltas.deltas:
            book.apply_delta(delta)
        self._last_book_update_ns[iid] = time.time_ns()

    async def _handle_crossed_book(self, iid: str, book: OrderBook, now_ns: int) -> bool:
        """
        Return True when the sample must be skipped.

        A cross on a central-book venue is *our* local corruption (or the venue's), never normal: ledger it once per episode, skip every
        tick, and only if the client can resync and the book stayed crossed longer than
        `crossed_resync_seconds`, force a fresh snapshot (DATA-03: a fallback, never the fix
        -- a rising resync count is an open DATA-02 incident). A client without
        `resync_orderbook` (full-snapshot venue) only ever skips; its next message replaces
        the book.
        """
        bid, ask = book.best_bid_price().as_double(), book.best_ask_price().as_double()
        if bid < ask:
            since = self._crossed_since_ns.pop(iid, None)
            if since is not None:
                logger.info(
                    "Crossed book for %s resolved after %.2fs (now bid=%s ask=%s)",
                    iid,
                    (now_ns - since) / 1e9,
                    bid,
                    ask,
                )
            return False
        since = self._crossed_since_ns.setdefault(iid, now_ns)
        if since == now_ns:
            error_ledger.record("collector.crossed_book", f"{iid} bid={bid} ask={ask}")
        logger.warning(
            "Crossed book for %s (bid=%s >= ask=%s) for %.1fs — skipping sample",
            iid,
            bid,
            ask,
            (now_ns - since) / 1e9,
        )
        if hasattr(self._client, "resync_orderbook") and (
            now_ns - since > self._config.crossed_resync_seconds * 1e9
        ):
            # Ledgered even when it succeeds (DATA-03): a forced resync is a fallback, never
            # a fix, and a rising count is an open DATA-02 incident that must show in
            # /api/errors, not only in Dozzle. Repeats per crossed_resync_seconds window
            # while the book keeps coming back crossed -- an unresolved incident must keep
            # alerting, not go quiet after one line.
            error_ledger.record(
                "collector.resync",
                f"forced resync for {iid}: crossed for {(now_ns - since) / 1e9:.0f}s "
                "(DATA-03 fallback, not a fix)",
            )
            await self._resync(iid)
        return True

    async def _resync(self, iid: str) -> None:
        """Drop the local book and ask the venue for a fresh snapshot; retried on failure."""
        self._clear_book_state(iid)
        try:
            await self._client.resync_orderbook(iid)
            self._resync_pending.discard(iid)
        except Exception as e:
            # The unsubscribe half may have gone through: retry from _sample_tick rather
            # than leave the instrument bookless forever.
            self._resync_pending.add(iid)
            error_ledger.record("collector.resync", f"resync failed for {iid}, retrying", e)

    # -- ingest ------------------------------------------------------------------------------

    def _on_data(self, data: Any) -> None:
        # Runs on the event loop from the Rust callback: O(1) only, _ingest_loop does the work.
        try:
            self._ingest_queue.put_nowait(data)
        except Exception as e:
            error_ledger.record(
                "collector.enqueue", f"failed to enqueue {type(data).__name__}, DROPPED", e
            )

    async def _ingest_loop(self) -> None:
        # Yields every _INGEST_YIELD_EVERY messages so a burst can't starve _second_loop.
        processed = 0
        while not self._stop.is_set():
            try:
                data = await asyncio.wait_for(self._ingest_queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                self._process_data(data)
            except Exception as e:
                error_ledger.record(
                    "collector.process", f"failed to process {type(data).__name__}, DROPPED", e
                )
            processed += 1
            if processed % _INGEST_YIELD_EVERY == 0:
                await asyncio.sleep(0)

    def _process_data(self, data: Any) -> None:
        now_ns = time.time_ns()
        self._last_feed_message_ns = now_ns
        if isinstance(data, OrderBookDeltas):
            self._apply_deltas(str(data.instrument_id), data)
        elif isinstance(data, TradeTick):
            iid = str(data.instrument_id)
            if now_ns - data.ts_event > self._config.stale_trade_seconds * 1e9:
                self._stale_trades_dropped[iid] += 1
                return
            if self._is_duplicate_trade(iid, str(data.trade_id)):
                self._duplicate_trades_dropped[iid] += 1
                return
            self._second_trades[iid].append(data)
            # Archived as received (both clocks), so the nightly rebuild can re-derive the
            # second from exchange time and correct what the live fold got wrong (D-45).
            self._buffer[(TradeTick, iid)].append(data)
        elif isinstance(data, QuoteTick):
            pass  # derivable from the snapshots; not persisted
        else:  # mark/index price, funding rate, open interest, ... -> catalog as-is
            self._buffer[(type(data), str(data.instrument_id))].append(data)

    def _is_duplicate_trade(self, iid: str, trade_id: str) -> bool:
        seen, order = self._seen_trade_id_set[iid], self._seen_trade_ids[iid]
        if trade_id in seen:
            return True
        if len(order) == order.maxlen:
            seen.discard(order[0])
        order.append(trade_id)
        seen.add(trade_id)
        return False

    def _report_stale_trades(self) -> None:
        if self._stale_trades_dropped:
            logger.info(f"Dropped subscribe-time trade history: {dict(self._stale_trades_dropped)}")
            self._stale_trades_dropped.clear()
        if self._duplicate_trades_dropped:
            logger.warning(
                f"Dropped duplicate trades (replayed after reconnect?): {dict(self._duplicate_trades_dropped)}"
            )
            self._duplicate_trades_dropped.clear()
        if self._deltas_before_snapshot_dropped:
            logger.warning(
                "Dropped order-book deltas that arrived before a snapshot (subscribe/resync "
                f"window): {dict(self._deltas_before_snapshot_dropped)}"
            )
            self._deltas_before_snapshot_dropped.clear()

    def _discard_second_accumulators(self, iid: str) -> None:
        """
        Drop this tick's trades for `iid` from the live second without emitting them. Called on
        every skipped sample (no book, crossed, stale): otherwise an outage's trades sit in the
        list until the next *valid* tick folds them, stamping the whole span's price range onto
        one second -- a giant-range candle at recovery instead of an honest gap. They stay in the
        raw archive: the nightly rebuild counts them as orphan trades (no snapshot row).
        """
        self._second_trades.pop(iid, None)

    # -- flush -------------------------------------------------------------------------------

    async def _flush_once(self, final: bool = False) -> None:
        """
        Write every buffered batch through `write_data`, each sorted by `ts_init` (stable).

        Unless `final` (shutdown: everything must go), a `TradeTick` batch keeps back the trades
        sharing its newest `ts_init` while that group is younger than `_TRADE_CARRY_NS`: the rest
        of that WS message may still be in the ingest queue (see `_TRADE_CARRY_NS`).
        """
        flushed_seconds: dict[str, list[DydxSecondSnapshot]] = {}
        now_ns = time.time_ns()
        batches = self._take_batches(now_ns, final)
        for key, items in batches:
            try:
                # Real disk I/O -- off the event loop so _second_loop isn't stalled.
                await asyncio.to_thread(self._catalog.write_data, items)
            except Exception as e:
                error_ledger.record(
                    "collector.flush_write", f"failed to write {key}, {len(items)} items LOST", e
                )
                if key[0] is TradeTick:
                    self._mark_lost_trades(key[1], items, now_ns)
                continue
            if key[0] is DydxSecondSnapshot:
                flushed_seconds[key[1]] = items
        self._apply_to_candle_store(flushed_seconds)

    def _take_batches(self, now_ns: int, final: bool) -> list[tuple[tuple[type, str], list[Any]]]:
        """
        Swap every non-empty buffer out as a `ts_init`-sorted batch, leaving any carried items.

        When a trade group is carried, that instrument's snapshot rows sampled after the group's
        `ts_init` are carried with it: they are the only rows that can hold those trades, so a
        crash before the next flush loses both together -- an honest gap -- instead of leaving
        rows whose trades the archive never received (the rebuild would zero them).
        """
        carried_from: dict[str, int] = {}
        batches: list[tuple[tuple[type, str], list[Any]]] = []
        pending = not self._ingest_queue.empty()
        # Trades first: their carry decides what the snapshot batches keep back.
        keys = sorted(self._buffer, key=lambda k: k[0] is not TradeTick)
        for key in keys:
            items = sorted(self._buffer[key], key=lambda d: d.ts_init)
            carry: list[Any] = []
            if key[0] is TradeTick and items and not final:
                items, carry = _split_open_ts_init_group(items, now_ns, pending)
                if carry:
                    carried_from[key[1]] = carry[0].ts_init
            elif key[0] is DydxSecondSnapshot and key[1] in carried_from:
                cut = bisect.bisect_right([d.ts_event for d in items], carried_from[key[1]])
                items, carry = items[:cut], items[cut:]
            self._buffer[key] = carry
            if items:
                batches.append((key, items))
        return batches

    def _mark_lost_trades(self, iid: str, lost: list[TradeTick], now_ns: int) -> None:
        """
        Record an archive gap for a trade batch that failed to write while this instrument's
        snapshots (holding those trades) may have landed, so the nightly rebuild keeps those
        rows' live values.
        """
        catalog_path = str(Path(self._config.catalog_path).resolve())
        record_gap(catalog_path, iid, lost[0].ts_init, now_ns, "write_failed", len(lost))

    def _apply_to_candle_store(self, flushed: dict[str, list[DydxSecondSnapshot]]) -> None:
        """
        Fold what just reached Parquet into the candle store, in one transaction.

        Only flushed seconds are applied, so the store is never ahead of the archive. A failure must
        not stop ingestion: it is loud (DATA-07), and the next start's catch-up or `build_candles`
        repairs it.
        """
        try:
            candle_store.apply_batch(self._candle_db, flushed)
        except Exception as e:
            error_ledger.record(
                "collector.candle_store",
                f"candle store write failed for {len(flushed)} instruments",
                e,
            )

    def _catch_up_candle_store(self) -> None:
        """
        Apply, at startup, the seconds the archive holds beyond each instrument's watermark.

        A crash between a Parquet flush and its store write (or a failed store write) leaves the
        store behind the archive, and nothing else would ever fill that hole. A gap wider than a day
        is left to `build_candles` (it would read too much here) and logged.
        """
        catalog_path = str(Path(self._config.catalog_path).resolve())
        now_ns = time.time_ns()
        for iid, mark in candle_store.watermarks(self._candle_db).items():
            if now_ns - mark > _CATCH_UP_MAX_NS:
                logger.warning(
                    f"Candle store for {iid} is more than a day behind: run build_candles"
                )
                continue
            try:
                candle_store.apply_seconds(
                    self._candle_db, iid, query_second_ohlc(catalog_path, iid, mark + 1, now_ns)
                )
            except Exception as e:
                error_ledger.record(
                    "collector.candle_store_catch_up", f"candle store catch-up failed for {iid}", e
                )

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(
                _seconds_until_next_flush(time.time(), self._config.flush_interval_seconds)
            )
            await self._flush_once()
            self._report_stale_trades()

    async def _candle_prune_loop(self) -> None:
        """Hourly: drop 1m/5m bars past their retention (`candle_store.RETAIN_DAYS`); wide bars are kept."""
        while not self._stop.is_set():
            try:
                candle_store.prune(self._candle_db)
            except Exception as e:
                error_ledger.record("collector.candle_store_prune", "candle store prune failed", e)
            await asyncio.sleep(3600)

    # -- sample ------------------------------------------------------------------------------

    async def _sample_tick(self, now_ns: int) -> list[DydxSecondSnapshot]:
        """
        Run the single write gate (AD-1): validate each book, then build one snapshot that
        feeds the catalog buffer and (via the caller) Redis -- same object, same
        iteration. A rejected instrument is skipped, its trades discarded, the reason logged.
        """
        stale_ns = self._config.stale_book_seconds * 1e9
        feed_stale_ns = (self._config.feed_stale_seconds or self._config.stale_book_seconds) * 1e9
        batch: list[DydxSecondSnapshot] = []
        sampled = list(self._instrument_ids())
        for iid in sampled:
            book = self._live_books.get(iid)
            if book is None:
                await self._handle_missing_book(iid, now_ns)
                self._discard_second_accumulators(iid)
                continue
            if book.best_bid_price() is None or book.best_ask_price() is None:
                self._discard_second_accumulators(iid)
                continue
            if await self._handle_crossed_book(iid, book, now_ns):
                self._discard_second_accumulators(iid)
                continue
            reason = self._stale_reason(iid, now_ns, feed_stale_ns, stale_ns)
            if reason:
                logger.warning("Stale book for %s (%s) — skipping snapshot", iid, reason)
                self._discard_second_accumulators(iid)
                continue

            bids, asks = book.bids()[:BOOK_DEPTH], book.asks()[:BOOK_DEPTH]
            trades = fold_trades(self._second_trades.pop(iid, [])).snapshot_values()
            snapshot = DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(iid),
                bid_prices=[lv.price.as_double() for lv in bids],
                bid_sizes=[lv.size() for lv in bids],
                ask_prices=[lv.price.as_double() for lv in asks],
                ask_sizes=[lv.size() for lv in asks],
                buy_volume=trades.buy_volume,
                sell_volume=trades.sell_volume,
                buy_count=trades.buy_count,
                sell_count=trades.sell_count,
                open_price=trades.open_price,
                high_price=trades.high_price,
                low_price=trades.low_price,
                close_price=trades.close_price,
                ts_event=now_ns,
                ts_init=now_ns,
            )
            if (
                ohlc_outside_book(snapshot)
                and now_ns - self._last_impossible_log_ns.get(iid, 0) >= _IMPOSSIBLE_LOG_EVERY_NS
            ):
                self._last_impossible_log_ns[iid] = now_ns
                # Unreachable after the stale/duplicate trade filters; if it fires, an
                # ingestion bug is writing impossible prices (DATA-02/DATA-06 canary).
                logger.error(
                    f"IMPOSSIBLE trade OHLC for {iid}: high={snapshot.high_price} "
                    f"low={snapshot.low_price} outside book "
                    f"[{min(snapshot.bid_prices)}, {max(snapshot.ask_prices)}]"
                )
            self._buffer[(DydxSecondSnapshot, iid)].append(snapshot)
            batch.append(snapshot)
        self._drop_unsampled_trades(sampled)
        return batch

    def _drop_unsampled_trades(self, sampled: list[str]) -> None:
        """
        MEM-02: a trade for an instrument this collector does not sample (unsubscribed, or not
        configured) would otherwise sit in `_second_trades` forever. It is still archived.
        """
        for iid in set(self._second_trades) - set(sampled):
            del self._second_trades[iid]

    def _stale_reason(self, iid: str, now_ns: int, feed_stale_ns: float, stale_ns: float) -> str:
        """
        Why this book must not be sampled ('' = fresh), naming the gap (DATA-01).

        Feed-level silence (no WS message for any instrument: dead socket, or a reconnect that
        has not yet delivered a fresh snapshot) is told apart from one quiet instrument on a
        live feed, whose book is simply unchanged.
        """
        feed_age_ns = now_ns - self._last_feed_message_ns
        if feed_age_ns > feed_stale_ns:
            return f"feed dead: no WS message of any kind for {feed_age_ns / 1e9:.1f}s"
        book_age_ns = now_ns - self._last_book_update_ns.get(iid, 0)
        if book_age_ns > stale_ns:
            return f"instrument silent: no OrderBookDeltas for {book_age_ns / 1e9:.1f}s, feed alive"
        return ""

    async def _handle_missing_book(self, iid: str, now_ns: int) -> None:
        if iid in self._resync_pending:
            await self._resync(iid)
        if now_ns - self._last_no_book_log_ns.get(iid, 0) >= _IMPOSSIBLE_LOG_EVERY_NS:
            # Rate-limited (one per minute): an instrument that never gets a book (not on
            # the venue, or awaiting a fresh snapshot) must not be a quiet gap (DATA-01) --
            # the watchdog only fires when *all* books are dead.
            self._last_no_book_log_ns[iid] = now_ns
            logger.warning("No book for %s — skipping snapshot", iid)

    async def _second_loop(self) -> None:
        """
        Sample on a drift-free wall-clock schedule (`_next_sample_at`): one sample per interval
        bucket, at mid-interval, so every floor second gets exactly one snapshot row -- the
        nightly rebuild maps trades to rows by `ts_event // 1 s` (audit, sampling drift).
        """
        interval = self._config.snapshot_interval_seconds
        last_tick_s: float | None = None
        while not self._stop.is_set():
            now = time.time()
            await asyncio.sleep(_next_sample_at(now, interval, last_tick_s) - now)
            now_ns = time.time_ns()
            last_tick_s = now_ns / 1e9
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

            batch = await self._sample_tick(now_ns)
            if self._redis is not None:
                await _publish_snapshot_batch(self._redis, batch)

    def _live_top(self, iid: str) -> tuple[list, list] | None:
        book = self._live_books.get(iid)
        if book is None:
            return None
        return (
            [(lv.price.as_double(), lv.size()) for lv in book.bids()[:BOOK_DEPTH]],
            [(lv.price.as_double(), lv.size()) for lv in book.asks()[:BOOK_DEPTH]],
        )

    async def _crosscheck_round(self, iid: str) -> list[str] | None:
        """
        One live-vs-REST comparison; None when there is no live book to judge.

        The live book is captured before and after the REST call and only a mismatch present
        against *both* counts. That filters skew between two samples of a moving book, but not
        REST latency on a book that churns faster than the request takes -- `_crosscheck_one`
        adds the persistence confirmation for that.
        """
        before = self._live_top(iid)
        rest_bids, rest_asks = await self._client.fetch_book_levels(iid)
        after = self._live_top(iid)
        if before is None or after is None:
            return None

        def mismatches(live: tuple[list, list]) -> list[str]:
            return [
                *(f"bids {m}" for m in top_levels_mismatch(live[0], rest_bids, depth=BOOK_DEPTH)),
                *(f"asks {m}" for m in top_levels_mismatch(live[1], rest_asks, depth=BOOK_DEPTH)),
            ]

        return persistent(mismatches(before), mismatches(after))

    async def _crosscheck_one(self, iid: str) -> None:
        """
        Diff the live top-20 with a REST snapshot (DATA-02's independent source of truth).

        A level is ledgered only when the *same* level is still wrong on a second round
        `_CROSSCHECK_CONFIRM_SECONDS` later: a book that missed a delta stays wrong until that
        level is next touched, while sampling/latency skew on a churning level does not repeat
        (first live run, 2026-09-20: BTC's top levels differed on ~every single round).
        """
        first = await self._crosscheck_round(iid)
        if first is None:
            logger.debug("Book cross-check skipped for %s: no live book", iid)
            return
        confirmed: list[str] = []
        if first:
            await asyncio.sleep(_CROSSCHECK_CONFIRM_SECONDS)
            confirmed = persistent(first, await self._crosscheck_round(iid) or [])
        if confirmed:
            self._book_crosscheck_mismatches[iid] += 1
            error_ledger.record(
                "collector.book_crosscheck",
                f"{iid} live book != REST snapshot on two rounds "
                f"(mismatch #{self._book_crosscheck_mismatches[iid]}): " + "; ".join(confirmed[:5]),
            )
        else:
            logger.debug(
                "Book cross-check %s clean (depth %d, %d unconfirmed)", iid, BOOK_DEPTH, len(first)
            )

    async def _crosscheck_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.book_crosscheck_seconds)
            for iid in self._instrument_ids():
                try:
                    await self._crosscheck_one(iid)
                except Exception as e:
                    error_ledger.record(
                        "collector.book_crosscheck", f"cross-check failed for {iid}", e
                    )

    async def _watchdog_loop(self) -> None:
        """OBS-01: page someone when every instrument's book has gone stale."""
        name = type(self._client).__name__
        while not self._stop.is_set():
            await asyncio.sleep(_WATCHDOG_CHECK_SECONDS)
            now_ns = time.time_ns()
            if now_ns - self._watchdog_started_ns < _WATCHDOG_STARTUP_GRACE_NS:
                continue
            live = list(self._instrument_ids())
            if not live:
                continue
            is_stale = all(
                now_ns - self._last_book_update_ns.get(iid, 0) > _WATCHDOG_STALE_NS for iid in live
            )
            message, down_since_ns, reminder_ns = _watchdog_transition(
                now_ns,
                is_stale,
                self._watchdog_down_since_ns,
                self._watchdog_last_reminder_ns,
                name,
            )
            self._watchdog_down_since_ns = down_since_ns
            self._watchdog_last_reminder_ns = reminder_ns
            if message is not None:
                await asyncio.to_thread(_notify, message, name)

    # -- lifecycle ---------------------------------------------------------------------------

    async def run(self) -> None:
        # Bounded socket timeouts: a black-holed Redis must not stall _second_loop's publish.
        self._redis = aioredis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"),
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )

        instruments = await self._client.fetch_instruments()
        by_id = {i.id.value: i for i in instruments}
        self._catalog.write_data(instruments_from_pyo3(list(by_id.values())))

        await self._client.connect(asyncio.get_running_loop(), list(by_id.values()))
        if hasattr(self._client, "subscribe_global"):
            await self._client.subscribe_global()
        configured = set(self._instrument_ids())
        unknown = configured - set(by_id)
        if unknown:
            logger.warning(
                "Configured instruments not found on the venue, skipping: %s", sorted(unknown)
            )
        self._catch_up_candle_store()
        subscribed = sorted(configured & set(by_id))
        for iid in subscribed:
            await self._client.subscribe(iid)
            logger.info(f"Subscribed {iid}")
        logger.info(f"Started: {len(subscribed)} subscribed")

        loops: tuple[Callable[[], Awaitable[None]], ...] = (
            self._ingest_loop,
            self._flush_loop,
            self._candle_prune_loop,
            self._second_loop,
            self._watchdog_loop,
            *(
                (self._crosscheck_loop,)
                if self._config.book_crosscheck_seconds > 0
                and hasattr(self._client, "fetch_book_levels")
                else ()
            ),
            *self._extra_loops,
        )
        # ensure_future (not create_task): extra_loops are typed as Awaitable, not Coroutine.
        tasks: list[asyncio.Future[Any]] = [asyncio.ensure_future(loop()) for loop in loops]
        stop_task: asyncio.Future[Any] = asyncio.ensure_future(self._stop.wait())
        try:
            # A loop dying is an unexpected bug: surface it so run_forever() does a clean restart.
            done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stop_task:
                    task.result()
        finally:
            stop_task.cancel()
            for task in tasks:
                task.cancel()
            try:
                await self._client.disconnect()
            except Exception as e:  # never skip the final flush over a closing WS
                error_ledger.record("collector.disconnect", "disconnect failed", e)
            await self._flush_once(final=True)
            self._report_stale_trades()
            await self._redis.aclose()

    def stop(self) -> None:
        self._stop.set()


async def run_forever(build: Callable[[], Collector], *, init_rust_logging: bool = True) -> None:
    """
    Process entrypoint: build a collector per attempt, restart with backoff on crash, stop
    cleanly on SIGINT/SIGTERM. `init_rust_logging=False` is for an entrypoint that installs
    its own richer `init_logging` first (dYdX's WS_RAW file sink, story 22.2).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Rust's `log` crate is a no-op until a logger is installed: without this every
    # `log::warn!`/`error!` inside the Rust WS client (including a failed
    # call_soon_threadsafe, i.e. a message silently never reaching `_on_data`) is invisible.
    # The returned LogGuard MUST stay referenced for this coroutine's whole lifetime --
    # dropping the last guard shuts Rust logging down (see dydx_collector/collector.py main()).
    _log_guard = (
        nautilus_pyo3.init_logging(
            trader_id=nautilus_pyo3.TraderId("COLLECTOR-001"),
            instance_id=nautilus_pyo3.UUID4(),
            level_stdout=nautilus_pyo3.LogLevel.WARNING,
        )
        if init_rust_logging
        else None
    )

    shutting_down = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutting_down.set)
    loop.add_signal_handler(signal.SIGTERM, shutting_down.set)

    backoff_seconds = 1.0
    first = True
    while not shutting_down.is_set():
        collector = build()
        if first:
            quarantine_corrupt_parquet(collector._config.catalog_path, collector._instrument_ids())
            first = False
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
    del _log_guard


async def _stop_on_shutdown(shutting_down: asyncio.Event, collector: Collector) -> None:
    await shutting_down.wait()
    collector.stop()
