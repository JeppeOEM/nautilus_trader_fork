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
The capture hot path's cost budget (spine AD-D5, audit D-65): no refactor may add per-message
allocation or latency to the ingest path, nor wall time to the flush encode.

Two bursts, each with its own baseline (Story 28.2):

- `default` (`tests/fixtures/hotpath_baseline.json`): three collectors -- dYdX arrival-timed,
  Bybit and Hyperliquid venue-timed -- with ten instruments each, per instrument one synthetic
  top-20 snapshot, `_DELTAS_PER_INSTRUMENT` single-level updates and the venue's recorded REST
  trades (`capture/venues/<venue>/tests/fixtures/*trades*.json`, re-stamped to now so the
  stale-history filter accepts them);
- `scale` (`tests/fixtures/hotpath_baseline_scale.json`): the fleet being scaled to, 30
  instruments per venue (tickers generated past `_TICKERS`) and 200-level snapshots, the same
  updates and trades.

A venue-timed collector only holds a delta on arrival (`LiveBook.hold`) and applies it when the
second closes, so each burst ends with that close (`_drain_pending_deltas`, exactly what
`_sample_tick` runs): the apply is inside the measurement, and nothing stays held between bursts.
Each burst is measured four ways, in one fresh interpreter with a fixed hash seed:

- `direct`: the burst replayed through `_process_data` (the queue bypassed);
- `queued`: the burst handed over the way the Rust clients do it, `loop.call_soon_threadsafe(
  _on_data, message)` per message on the collector's own event loop, drained by the running
  `_ingest_loop` (the hand-off, the queue and the loop's wake-ups on the path);
- `flush`: one venue batch of `instruments x 60` `DydxSecondSnapshot` rows (the `:02` flush),
  `encode_ms` through `ArrowSerializer.serialize_batch`, `write_ms` through
  `ParquetArchiveWriter.write`;
- `book_path` (`scale` only, recorded, never asserted): the Python book path's share of one core
  at 1,500 Bybit messages/s -- `capsule_to_data` + `LiveBook.apply` per message plus one
  `snapshot_top` per live book per second (Story 28.2's fix-4 decision number).

For `direct` and `queued`, per message, median of `_REPETITIONS` fresh repetitions after a
warm-up burst on the same collectors: `tracemalloc` (gc off) retained blocks, retained bytes and
peak bytes above the start, and `time.perf_counter_ns` over the same burst in a separate,
untraced pass. The fixed seed and fresh interpreter keep earlier tests' free-list and cache state
from moving the allocation counts: they are exact and repeatable.

The baselines are the files of the *checkout* (`PLATFORM_SOURCE_DIR`, the tree `make test`
mounts; this directory when run from a checkout): a first run records a missing one there, and
never into the image's own copy of the tree, where it would vanish with the container and every
later run would pass against itself. `make test` mounts the checkout read-only, so there a
missing baseline fails and `make hotpath-baseline` records both. Every later run asserts every
allocation figure <= baseline and, on the CPU the baseline was recorded on, wall time (direct
and queued ns per message, flush `encode_ms`) <= `_WALL_TIME_FACTOR` x baseline (on another CPU
the wall-time checks are skipped with the reason shown; allocations still assert). The 2x gate
tolerates a shared, intermittently loaded box; a change that only removes cost is held to 1x by
the median-of-5 figures in its commit message instead (audit D-65).

Known limit: the replay drives the base `CaptureService` with the core's default policies, so dYdX's
`DydxLevelTagger` and Bybit's `BybitSequenceCanary` (Story 26.1's policy values) are not on the
measured path: adding them changes the burst, which needs a newly recorded baseline in its own
reviewed change. Upgrade path: a per-venue replay through each venue's `CapturePolicies`.
Known limit: `tracemalloc` sees only the Python allocator, not the Rust/Cython heaps behind
`OrderBook.apply_delta` or Arrow's buffers. Upgrade path: an RSS-based soak for the native heaps.
"""

import asyncio
import gc
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import NamedTuple

import pytest


_HERE = Path(__file__).resolve().parent
_CODE_ROOT = _HERE.parent  # the tree under test: the image's /app, or a checkout's platform/


class _BurstSpec(NamedTuple):
    instruments_per_venue: int
    book_levels: int
    baseline_file: str


_BURSTS = {
    "default": _BurstSpec(10, 20, "hotpath_baseline.json"),
    "scale": _BurstSpec(30, 200, "hotpath_baseline_scale.json"),
}


def _baseline_path(burst: str) -> Path:
    """Return the checkout's baseline file for `burst` (see the module docstring)."""
    source = os.environ.get("PLATFORM_SOURCE_DIR")
    tests = Path(source) / "tests" if source else _HERE
    return tests / "fixtures" / _BURSTS[burst].baseline_file


_DELTAS_PER_INSTRUMENT = 50
_REPETITIONS = 3
_FLUSH_SECONDS = 60  # one flush window: a venue's rows for one minute
_FLUSH_LEVELS = 20  # BOOK_DEPTH: what the sampler stores per side
_FLUSH_REPETITIONS = 5
_BOOK_PATH_REPETITIONS = 5
_BOOK_PATH_MESSAGES_PER_SECOND = 1_500  # 30 instruments x Bybit's orderbook.50 push every 20 ms
_WALL_TIME_FACTOR = 2.0
_VARIANTS = ("direct", "queued")
_ALLOCATION_KEYS = (
    "retained_blocks_per_message",
    "retained_bytes_per_message",
    "peak_bytes_per_message",
)

# The measuring subprocess runs on a pinned clock (`_pin_clock`): every message is stamped in one
# exchange second, `_BURST_LEAD_NS` before "now", so no trade is ever stale, none crosses a second
# boundary, and the allocations cannot depend on when the run happens.
_PINNED_NS = 1_790_000_000 * 1_000_000_000 + 900_000_000  # S + 0.9 s
_BURST_LEAD_NS = 800_000_000  # messages at S + 0.1 s onwards, spaced 1 us
_SECOND_CLOSE_NS = (_PINNED_NS // 1_000_000_000 + 1) * 1_000_000_000  # S + 1: every delta is due

_TICKERS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC")


def _tickers(count: int) -> list[str]:
    """Return `_TICKERS`, then generated letter-only names (`ZAA`, `ZAB`, ...) every venue takes."""
    generated = (f"Z{chr(65 + n // 26)}{chr(65 + n % 26)}" for n in range(26 * 26))
    return [*_TICKERS, *generated][:count]


# -- the burst (runs in the measuring subprocess) -------------------------------------------------


def _venues() -> list[dict[str, Any]]:
    """Venue shape: id format, time source, trade fixture and its parser, fixture precisions."""
    from capture.venues.bybit.trade_history import parse_bybit_trades
    from capture.venues.dydx.trade_history import parse_dydx_trades
    from capture.venues.hyperliquid.trade_history import parse_hyperliquid_trades

    return [
        {
            "iid": "{t}-USD-PERP.DYDX",
            "source": "arrival",
            "fixture": _CODE_ROOT
            / "capture/venues/dydx/tests/fixtures/dydx_trades_btc_usd_20260921.json",
            "parse": parse_dydx_trades,
            "precisions": (0, 4),
        },
        {
            "iid": "{t}USDT-LINEAR.BYBIT",
            "source": "venue",
            "fixture": (
                _CODE_ROOT
                / "capture/venues/bybit/tests/fixtures/bybit_trades_btcusdt_linear_20260921.json"
            ),
            "parse": parse_bybit_trades,
            "precisions": (2, 3),
        },
        {
            "iid": "{t}-USD-PERP.HYPERLIQUID",
            "source": "venue",
            "fixture": (
                _CODE_ROOT
                / "capture/venues/hyperliquid/tests/fixtures/hyperliquid_recent_trades_btc_20260921.json"
            ),
            "parse": parse_hyperliquid_trades,
            "precisions": (1, 5),
        },
    ]


_BYBIT = 1  # the Bybit entry of `_venues()`: the book-path and flush figures are its


def _instrument(iid: str, price_p: int, size_p: int) -> Any:
    from nautilus_trader.model.currencies import BTC
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import Symbol
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Price
    from nautilus_trader.model.objects import Quantity

    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(iid.split(".")[0]),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_p,
        price_increment=Price.from_str(str(Decimal(1).scaleb(-price_p))),
        size_precision=size_p,
        size_increment=Quantity.from_str(str(Decimal(1).scaleb(-size_p))),
        ts_event=0,
        ts_init=0,
    )


def _book_messages(iid: str, now_ns: int, levels: int) -> list[Any]:
    """One snapshot (Clear + `levels` a side), then single-level size updates."""
    from nautilus_trader.model.data import BookOrder
    from nautilus_trader.model.data import OrderBookDelta
    from nautilus_trader.model.data import OrderBookDeltas
    from nautilus_trader.model.enums import BookAction
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price
    from nautilus_trader.model.objects import Quantity

    inst = InstrumentId.from_str(iid)

    def level(side: OrderSide, n: int, size: int, action: BookAction, ts: int) -> Any:
        cents = 10_000 - n if side == OrderSide.BUY else 10_010 + n
        price = Price.from_str(str(Decimal(cents).scaleb(-2)))
        order = BookOrder(side, price, Quantity.from_str(f"{size}.000"), 0)
        return OrderBookDelta(inst, action, order, 0, 0, ts, ts)

    snapshot = [OrderBookDelta.clear(inst, 0, now_ns, now_ns)]
    for n in range(levels):
        snapshot.append(level(OrderSide.BUY, n, n + 1, BookAction.ADD, now_ns))
        snapshot.append(level(OrderSide.SELL, n, n + 1, BookAction.ADD, now_ns))
    messages = [OrderBookDeltas(inst, snapshot)]
    for k in range(_DELTAS_PER_INSTRUMENT):
        side = OrderSide.BUY if k % 2 == 0 else OrderSide.SELL
        ts = now_ns + (k + 1) * 1_000
        update = level(side, k % levels, k + 2, BookAction.UPDATE, ts)
        messages.append(OrderBookDeltas(inst, [update]))
    return messages


def _trades(venue: dict[str, Any], iid: str, tag: str, now_ns: int) -> list[Any]:
    """Return the venue's recorded trades for `iid`, ids tagged per burst, clocks set to now."""
    from nautilus_trader.model.data import TradeTick
    from nautilus_trader.model.identifiers import TradeId

    payload = json.loads(venue["fixture"].read_text())
    instrument = _instrument(iid, *venue["precisions"])
    recorded = venue["parse"](payload, instrument, now_ns)
    return [
        TradeTick(
            instrument.id,
            trade.price,
            trade.size,
            trade.aggressor_side,
            TradeId(f"{tag}-{n}"),  # recorded ids can exceed the 36-char TradeId cap with a tag
            now_ns + n * 1_000,
            now_ns + n * 1_000,
        )
        for n, trade in enumerate(reversed(recorded))  # recorded newest first
    ]


def _burst(venue: dict[str, Any], iids: list[str], tag: str, levels: int) -> list[Any]:
    """Per instrument: snapshot, deltas and trades, interleaved as a socket would deliver them."""
    now_ns = _PINNED_NS - _BURST_LEAD_NS
    burst: list[Any] = []
    for iid in iids:
        book = _book_messages(iid, now_ns, levels)
        trades = _trades(venue, iid, tag, now_ns)
        burst.append(book[0])
        for n in range(max(len(book) - 1, len(trades))):
            burst.extend(book[1 + n : 2 + n])
            burst.extend(trades[n : n + 1])
    return burst


def _collectors(root: Path, spec: _BurstSpec) -> list[tuple[Any, list[str], dict[str, Any]]]:
    from capture.application.capture_service import CaptureService
    from capture.application.config import CoreConfig
    from capture.infrastructure.parquet_writer import ParquetArchiveWriter

    built = []
    for n, venue in enumerate(_venues()):
        iids = [venue["iid"].format(t=t) for t in _tickers(spec.instruments_per_venue)]
        catalog = root / f"catalog_{n}"
        config = CoreConfig(
            environment="mainnet",
            catalog_path=str(catalog),
            book_time_source=venue["source"],
        )
        collector = CaptureService(
            config,
            lambda _on_data, _ledger: object(),
            venue=venue["iid"].rsplit(".", 1)[1],
            plan=iids,
            archive=ParquetArchiveWriter(str(catalog)),
            live_stream=None,
        )
        collector._applied.update(iids)  # as `run()`'s initial apply leaves it
        built.append((collector, iids, venue))
    return built


class _Run(NamedTuple):
    """One collector's measured burst: `drive` replays it, `close` tears the harness down."""

    collector: Any
    burst: list[Any]
    drive: Callable[[], None]
    close: Callable[[], None]


def _ingest(collector: Any, burst: list[Any]) -> None:
    """Replay one burst as the live path runs it, queue bypassed: each message, then the close."""
    process: Callable[[Any], None] = collector._process_data
    for message in burst:
        process(message)
    if collector._venue_time:
        collector._drain_pending_deltas(_SECOND_CLOSE_NS)


def _direct_runs(root: Path, tag: str, spec: _BurstSpec) -> list[_Run]:
    """Fresh collectors, warmed with one burst; returns each one's measured burst."""
    runs = []
    for collector, iids, venue in _collectors(root, spec):
        _ingest(collector, _burst(venue, iids, f"warm{tag}", spec.book_levels))
        burst = _burst(venue, iids, f"run{tag}", spec.book_levels)
        runs.append(_Run(collector, burst, _bind(_ingest, collector, burst), _nothing))
    return runs


def _bind(
    ingest: Callable[[Any, list[Any]], None], collector: Any, burst: list[Any]
) -> Callable[[], None]:
    return lambda: ingest(collector, burst)


def _nothing() -> None:
    return None


def _new_loop() -> asyncio.AbstractEventLoop:
    """
    Build the loop the entrypoints run on: `asyncio.run`'s default. uvloop was measured and not
    kept (audit D-65, Story 28.2: faster, but its larger per-callback handle raised the scale
    burst's queued peak above the baseline).
    """
    return asyncio.new_event_loop()


async def _feed(collector: Any, burst: list[Any]) -> None:
    """
    Hand the burst over as the Rust clients do (`call_soon_threadsafe(_on_data, m)` per message),
    then wait for the running `_ingest_loop` to drain it: `get()` on a non-empty queue does not
    suspend, so an empty queue seen from here means every message was processed.
    """
    loop = asyncio.get_running_loop()
    on_data = collector._on_data
    for message in burst:
        loop.call_soon_threadsafe(on_data, message)
    queue = collector._ingest_queue
    await asyncio.sleep(0)  # the hand-offs were scheduled first, so they have all run by now
    while queue.qsize():
        await asyncio.sleep(0)
    if collector._venue_time:
        collector._drain_pending_deltas(_SECOND_CLOSE_NS)


def _stop_loop(task: asyncio.Task[None]) -> None:
    task.get_loop().stop()


def _queued_run(collector: Any, warm: list[Any], burst: list[Any]) -> _Run:
    """Put a collector on its own loop with `_ingest_loop` running; warm it through the queue."""
    loop = _new_loop()
    task = loop.create_task(collector._ingest_loop())
    loop.run_until_complete(asyncio.sleep(0))
    # An ingest task that ends stops the loop, so the replay fails by name ("Event loop stopped
    # before Future completed") instead of `_feed` spinning until the subprocess timeout. Set here,
    # not checked in `_feed`: a guard there is inside the traced pass and moves its figures.
    task.add_done_callback(_stop_loop)
    loop.run_until_complete(_feed(collector, warm))

    def drive() -> None:
        loop.run_until_complete(_feed(collector, burst))

    def close() -> None:
        task.remove_done_callback(_stop_loop)
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()

    return _Run(collector, burst, drive, close)


def _queued_runs(root: Path, tag: str, spec: _BurstSpec) -> list[_Run]:
    runs = []
    for collector, iids, venue in _collectors(root, spec):
        warm = _burst(venue, iids, f"warm{tag}", spec.book_levels)
        runs.append(
            _queued_run(collector, warm, _burst(venue, iids, f"run{tag}", spec.book_levels))
        )
    return runs


_PREPARE: dict[str, Callable[[Path, str, _BurstSpec], list[_Run]]] = {
    "direct": _direct_runs,
    "queued": _queued_runs,
}


def _replay(runs: list[_Run]) -> int:
    count = 0
    for run in runs:
        run.drive()
        count += len(run.burst)
    return count


def _assert_the_live_path_was_taken(runs: list[_Run], spec: _BurstSpec) -> None:
    """
    Every message took the accepted path: no trade dropped as stale or replayed, none late or
    ahead of its second, no delta before a snapshot or late for its second, every held delta
    applied into a live book, nothing ledgered. Otherwise the numbers would measure a different
    path than the baseline did.
    """
    from observability import error_ledger

    for run in runs:
        collector, burst = run.collector, run.burst
        trades = sum(1 for message in burst if type(message).__name__ == "TradeTick")
        intakes, books = collector._intakes, collector._books
        bypassed = {
            counter: {iid: n for iid, i in intakes.items() if (n := getattr(i, counter))}
            for counter in ("stale", "duplicate", "duplicate_feed", "late", "ahead")
        }
        bypassed.update(
            {
                counter: {iid: n for iid, b in books.items() if (n := getattr(b, counter))}
                for counter in ("before_snapshot", "late_deltas", "pending_count")
            }
        )
        assert not any(bypassed.values()), f"replay left the live path: {bypassed}"
        live_books = sum(1 for b in books.values() if b.book is not None)
        assert live_books == spec.instruments_per_venue, (
            f"{live_books} live books, expected every instrument's"
        )
        live = (
            sum(len(t) for i in intakes.values() for t in i.buckets.values())
            if collector._venue_time
            else sum(len(i.live) for i in intakes.values())
        )
        assert live == 2 * trades, f"{live} trades folded live, expected {2 * trades} (warm + run)"
    assert error_ledger.counts() == {}, f"the replay ledgered: {error_ledger.counts()}"


def _close(runs: list[_Run]) -> None:
    for run in runs:
        run.close()


def _traced(root: Path, tag: str, spec: _BurstSpec, variant: str) -> dict[str, float]:
    runs = _PREPARE[variant](root, tag, spec)
    gc.collect()
    gc.disable()
    tracemalloc.start()
    try:
        start_snapshot = tracemalloc.take_snapshot()
        start_bytes, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        messages = _replay(runs)
        end_bytes, peak_bytes = tracemalloc.get_traced_memory()
        end_snapshot = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
        gc.enable()
    try:
        _assert_the_live_path_was_taken(runs, spec)
    finally:
        _close(runs)  # a failed assertion must not leak a loop's warnings over its own message
    own = [tracemalloc.Filter(False, tracemalloc.__file__)]
    diff = end_snapshot.filter_traces(own).compare_to(start_snapshot.filter_traces(own), "filename")
    return {
        "messages": messages,
        "retained_blocks_per_message": sum(s.count_diff for s in diff) / messages,
        "retained_bytes_per_message": (end_bytes - start_bytes) / messages,
        "peak_bytes_per_message": (peak_bytes - start_bytes) / messages,
    }


def _timed(root: Path, tag: str, spec: _BurstSpec, variant: str) -> float:
    runs = _PREPARE[variant](root, tag, spec)
    gc.collect()
    gc.disable()
    try:
        started = time.perf_counter_ns()
        messages = _replay(runs)
        elapsed = time.perf_counter_ns() - started
    finally:
        gc.enable()
    try:
        _assert_the_live_path_was_taken(runs, spec)
    finally:
        _close(runs)
    return elapsed / messages


def _variant_figures(root: Path, spec: _BurstSpec, variant: str) -> dict[str, Any]:
    """Median over fresh, warmed repetitions: the three allocation figures and ns per message."""
    _traced(root / f"{variant}_warmup", "x", spec, variant)  # first-use caches, imports, names
    traced = [_traced(root / f"{variant}_t{n}", str(n), spec, variant) for n in range(_REPETITIONS)]
    timed = [_timed(root / f"{variant}_w{n}", str(n), spec, variant) for n in range(_REPETITIONS)]
    figures: dict[str, Any] = {"messages": traced[0]["messages"]}
    for key in _ALLOCATION_KEYS:
        figures[key] = round(statistics.median(run[key] for run in traced), 4)
    figures["ns_per_message"] = round(statistics.median(timed))
    return figures


# -- the flush (one venue's minute of rows) -------------------------------------------------------


def _flush_rows(spec: _BurstSpec) -> list[Any]:
    """
    `instruments x _FLUSH_SECONDS` rows as the sampler emits them: 20 levels a side, one row per
    instrument per second, alternating traded (OHLC set) and quiet (OHLC `None`) seconds.
    """
    from kernel.second_snapshot import DydxSecondSnapshot

    from nautilus_trader.model.identifiers import InstrumentId

    fmt = _venues()[_BYBIT]["iid"]
    ids = [InstrumentId.from_str(fmt.format(t=t)) for t in _tickers(spec.instruments_per_venue)]
    first = _PINNED_NS // 1_000_000_000 - _FLUSH_SECONDS
    rows = []
    for s in range(_FLUSH_SECONDS):
        second_ns = (first + s) * 1_000_000_000
        for n, iid in enumerate(ids):
            traded = (s + n) % 2 == 0
            price = 10_005 + s if traded else None
            rows.append(
                DydxSecondSnapshot(
                    iid,
                    2,
                    3,
                    bid_price_units=[10_000 - k for k in range(_FLUSH_LEVELS)],
                    bid_size_units=[1_000 + k + s for k in range(_FLUSH_LEVELS)],
                    ask_price_units=[10_010 + k for k in range(_FLUSH_LEVELS)],
                    ask_size_units=[2_000 + k + n for k in range(_FLUSH_LEVELS)],
                    buy_volume_units=500 * s if traded else 0,
                    sell_volume_units=300 * n if traded else 0,
                    buy_count=s if traded else 0,
                    sell_count=n if traded else 0,
                    ts_event=second_ns + 500_000_000,
                    ts_init=second_ns + 1_200_000_000,
                    open_price_units=price,
                    high_price_units=None if price is None else price + 3,
                    low_price_units=None if price is None else price - 2,
                    close_price_units=None if price is None else price + 1,
                )
            )
    return rows


def _median_ms(run: Callable[[], None], repetitions: int) -> float:
    elapsed = []
    for _ in range(repetitions):
        gc.collect()
        gc.disable()  # as the per-message passes: a collection is not the code under test
        try:
            started = time.perf_counter_ns()
            run()
            elapsed.append(time.perf_counter_ns() - started)
        finally:
            gc.enable()
    return round(statistics.median(elapsed) / 1e6, 2)


def _flush_figures(root: Path, spec: _BurstSpec) -> dict[str, Any]:
    """Time the flush's encode (the registered batch path) and its whole catalog write, in ms."""
    from capture.infrastructure.parquet_writer import ParquetArchiveWriter
    from kernel.second_snapshot import DydxSecondSnapshot

    from nautilus_trader.serialization.arrow.serializer import ArrowSerializer

    rows = _flush_rows(spec)

    def encode() -> None:
        ArrowSerializer.serialize_batch(rows, DydxSecondSnapshot)

    # Built before any timing: production reuses one writer, so its construction is not a cost
    # of the write.
    writers = iter(
        [ParquetArchiveWriter(str(root / f"flush_{n}")) for n in range(_FLUSH_REPETITIONS + 1)]
    )
    next(writers).write(rows)  # first-use: the catalog's filesystem, Parquet writer, imports
    encode()
    return {
        "rows": len(rows),
        "encode_ms": _median_ms(encode, _FLUSH_REPETITIONS),
        "write_ms": _median_ms(lambda: next(writers).write(rows), _FLUSH_REPETITIONS),
    }


# -- the Python book path (scale only: fix 4's decision number) -----------------------------------


def _book_path_figures(root: Path, spec: _BurstSpec) -> dict[str, Any]:
    """
    Measure the Bybit collector's book path at `spec`: `per_message_ns` is `capsule_to_data` (what
    each venue client runs on the Rust hand-over) + `LiveBook.apply` per update message, median of
    `_BOOK_PATH_REPETITIONS` passes' per-message mean; `reads_per_second_ns` one `snapshot_top`
    per live book with the collector's own sampler parameters (every read `Accepted`). The share
    is of one core at `_BOOK_PATH_MESSAGES_PER_SECOND`.
    """
    from capture.domain.verdicts import Accepted

    from nautilus_trader.model.data import OrderBookDeltas
    from nautilus_trader.model.data import capsule_to_data

    run = _direct_runs(root / "book_path", "b", spec)[_BYBIT]
    run.drive()  # every book live, every held delta applied
    books = run.collector._books
    updates = [
        (m.to_pyo3().as_pycapsule(), books[str(m.instrument_id)])
        for m in run.burst
        if isinstance(m, OrderBookDeltas) and not m.deltas[0].is_clear
    ]
    sampler, second = run.collector._sampler, _PINNED_NS // 1_000_000_000

    def apply_pass() -> float:
        started = time.perf_counter_ns()
        for capsule, book in updates:
            book.apply(capsule_to_data(capsule), _PINNED_NS)
        return (time.perf_counter_ns() - started) / len(updates)

    def read_pass() -> float:
        started = time.perf_counter_ns()
        for book in books.values():
            verdict, _ = book.snapshot_top(
                sampler.depth, _PINNED_NS, second, sampler.stale_ns, None, sampler.crossed
            )
            assert isinstance(verdict, Accepted), verdict
        return time.perf_counter_ns() - started

    per_message = _median_of(apply_pass)
    reads = _median_of(read_pass)
    share = (per_message * _BOOK_PATH_MESSAGES_PER_SECOND + reads) / 1e9 * 100
    return {
        "per_message_ns": round(per_message),
        "reads_per_second_ns": round(reads),
        "core_share_pct_at_1500": round(share, 3),
    }


def _median_of(measure_pass: Callable[[], float]) -> float:
    measure_pass()  # warm
    gc.collect()
    gc.disable()
    try:
        return statistics.median(measure_pass() for _ in range(_BOOK_PATH_REPETITIONS))
    finally:
        gc.enable()


def _pin_clock() -> None:
    """
    Pin `time.time_ns` for this measuring subprocess only: `_process_data` reads it for "now",
    and the burst is stamped relative to it. `perf_counter_ns` (the wall-time pass) is untouched.
    """
    time.time_ns = lambda: _PINNED_NS


def measure(burst: str) -> dict[str, Any]:
    """Run in the measuring subprocess: every figure of `burst` (see the module docstring)."""
    spec = _BURSTS[burst]
    _pin_clock()
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for variant in _VARIANTS:
            figures = _variant_figures(root, spec, variant)
            messages = figures.pop("messages")
            # Both variants must replay the one burst, or one set of figures is off-shape.
            assert result.setdefault("messages", messages) == messages, (variant, messages)
            result[variant] = figures
        result["flush"] = _flush_figures(root, spec)
        if burst == "scale":
            result["book_path"] = _book_path_figures(root, spec)
    return result


# -- the test (host side) -------------------------------------------------------------------------


def _venue_names() -> tuple[str, ...]:
    return ("dydx arrival", "bybit venue", "hyperliquid venue")


def _cpu_fingerprint() -> str:
    model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    return f"{model} | {os.cpu_count()} cpus | {os.uname().machine}"


def _run_replay(burst: str) -> dict[str, Any]:
    env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONPATH": str(_CODE_ROOT)}
    completed = subprocess.run(  # noqa: S603 (this interpreter, this file)
        [sys.executable, str(Path(__file__).resolve()), "--measure", burst],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(_CODE_ROOT),
        env=env,
        timeout=900,
    )
    assert completed.returncode == 0, f"replay failed:\n{completed.stderr[-4000:]}"
    return json.loads(completed.stdout.strip().splitlines()[-1])


_MEASURED: dict[str, dict[str, Any]] = {}


def _measured(burst: str) -> dict[str, Any]:
    if burst not in _MEASURED:
        _MEASURED[burst] = {**_run_replay(burst), "cpu": _cpu_fingerprint()}
    return _MEASURED[burst]


def _burst_shape(burst: str) -> dict[str, int]:
    """Return the burst's parameters: recorded with the baseline, checked against it every run."""
    spec = _BURSTS[burst]
    return {
        "instruments": spec.instruments_per_venue * len(_venue_names()),
        "deltas_per_instrument": _DELTAS_PER_INSTRUMENT,
        "book_levels": spec.book_levels,
        "repetitions": _REPETITIONS,
        "flush_rows": spec.instruments_per_venue * _FLUSH_SECONDS,
    }


def _python_version() -> str:
    return sys.version.split()[0]


def _record(burst: str, path: Path) -> None:
    measured = _measured(burst)
    record = {
        **{key: value for key, value in measured.items() if key != "cpu"},
        "host": {"cpu": measured["cpu"], "python": _python_version()},
        "recorded": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "burst": _burst_shape(burst),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n")
    except OSError as e:
        # A read-only source mount (`make test`): passing here would be passing against
        # nothing. Recording is its own deliberate step.
        pytest.fail(
            f"no baseline at {path} and it cannot be recorded there ({e}): "
            "run `make hotpath-baseline`, in a change that deliberately re-baselines"
        )


def _baseline(burst: str) -> dict[str, Any]:
    """
    Return the committed baseline of `burst`, written from this run when there is none yet
    (AD-D5). A baseline from another interpreter fails every check that reads it: allocation
    counts are exact per CPython version, so comparing across versions would read an allocator
    change as a hot-path regression, or hide one.
    """
    path = _baseline_path(burst)
    if not path.is_file():
        _record(burst, path)
    baseline: dict[str, Any] = json.loads(path.read_text())
    if not {*_VARIANTS, "flush"} <= baseline.keys():
        pytest.fail(
            f"{path} has the pre-Story-28.2 layout (no {', '.join(_VARIANTS)}/flush sections): "
            "re-record it with `make hotpath-baseline` in its own reviewed change"
        )
    if baseline["host"]["python"] != _python_version():
        pytest.fail(
            f"the baseline was recorded on Python {baseline['host']['python']}; this is "
            f"{_python_version()}. Run `make test` (the collector image records and checks on "
            "one interpreter), or re-record with `make hotpath-baseline` in the change that "
            "deliberately bumps Python"
        )
    return baseline


@pytest.mark.parametrize("burst", sorted(_BURSTS))
def test_replay_is_the_burst_the_baseline_was_recorded_on(burst: str) -> None:
    baseline, measured = _baseline(burst), _measured(burst)
    changed = "the replay burst changed: a new burst needs a newly recorded baseline, in its own "
    changed += "reviewed change (`make hotpath-baseline` on today's code)"
    assert measured["messages"] == baseline["messages"], changed
    # The same message count can come from a differently shaped burst (more levels, fewer
    # deltas); the figures are only comparable for the recorded shape.
    assert _burst_shape(burst) == baseline["burst"], changed
    assert measured["flush"]["rows"] == baseline["flush"]["rows"], changed


@pytest.mark.parametrize(
    ("burst", "variant", "key"),
    [(b, v, k) for b in sorted(_BURSTS) for v in _VARIANTS for k in _ALLOCATION_KEYS],
)
def test_allocations_per_message_do_not_exceed_the_baseline(
    burst: str, variant: str, key: str
) -> None:
    baseline, measured = _baseline(burst)[variant], _measured(burst)[variant]
    assert measured[key] <= baseline[key], (
        f"{burst} {variant} {key}: {measured[key]} > baseline {baseline[key]} -- the hot path "
        "gained an allocation per message (AD-D5). Remove it; or, when the cost is deliberate, "
        "re-baseline in its own reviewed change (`make hotpath-baseline`) and state the reason "
        "on audit row D-65. Never re-record to make this run pass"
    )


@pytest.mark.parametrize(
    ("burst", "section", "key"),
    [
        (b, section, key)
        for b in sorted(_BURSTS)
        for section, key in (
            ("direct", "ns_per_message"),
            ("queued", "ns_per_message"),
            ("flush", "encode_ms"),
        )
    ],
)
def test_wall_time_is_within_twice_the_baseline(burst: str, section: str, key: str) -> None:
    baseline, measured = _baseline(burst), _measured(burst)
    if baseline["host"]["cpu"] != measured["cpu"]:
        pytest.skip(
            f"wall time is only comparable on the baseline's CPU ({baseline['host']['cpu']}); "
            f"this is {measured['cpu']}. Allocations were still checked."
        )
    limit = _WALL_TIME_FACTOR * baseline[section][key]
    assert measured[section][key] <= limit, (
        f"{burst} {section} {key}: {measured[section][key]} > {_WALL_TIME_FACTOR}x baseline "
        f"{baseline[section][key]} (AD-D5)"
    )


if __name__ == "__main__" and sys.argv[1:2] == ["--measure"] and sys.argv[2:3]:
    print(json.dumps(measure(sys.argv[2])))
