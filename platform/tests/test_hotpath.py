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
The capture hot path's cost budget (spine AD-D5): no refactor may add per-message allocation or
latency to `Collector._process_data`.

A fixed burst is replayed through `_process_data` (the queue bypassed): three collectors -- dYdX
arrival-timed, Bybit and Hyperliquid venue-timed -- with ten instruments each, and per
instrument one synthetic top-20 snapshot, `_DELTAS_PER_INSTRUMENT` incremental deltas and the
venue's recorded REST trades (`collector_core/tests/fixtures/*trades*.json`, re-stamped to now so
the stale-history filter accepts them). A venue-timed collector only holds a delta on arrival and
applies it when the second closes, so the burst ends with that close (`_drain_pending_deltas`,
exactly what `_sample_tick` runs): the apply is inside the measurement, and nothing stays held
between bursts. Measured per message, median of `_REPETITIONS` fresh repetitions after a warm-up
burst on the same collectors:

- `tracemalloc` (gc off): retained blocks, retained bytes and peak bytes above the start;
- `time.perf_counter_ns` over the same burst in a separate, untraced pass.

The measurement runs in a fresh interpreter with a fixed hash seed, so earlier tests' free-list
and cache state cannot move the allocation counts: they are exact and repeatable. The baseline is
`tests/fixtures/hotpath_baseline.json` of the *checkout* (`PLATFORM_SOURCE_DIR`, the tree `make
test` mounts; this directory when run from a checkout): a first run records it there, and never
into the image's own copy of the tree, where it would vanish with the container and every later
run would pass against itself. `make test` mounts the checkout read-only, so there a missing
baseline fails and `make hotpath-baseline` records it. Every later run asserts every allocation
figure <= baseline and, on the CPU the baseline was recorded on, wall time <= 2x baseline (on
another CPU the wall-time check is skipped with the reason shown; allocations still assert).

Known limit: the replay drives the base `Collector`, so the venue `_apply_deltas` overrides
(dYdX's per-level tagging, Bybit's `u` canary) are not on the measured path until capture's
policies move into the core (Story 26.1). Known limit: `tracemalloc` sees only the Python
allocator, not the Rust/Cython heaps behind `OrderBook.apply_delta`. Upgrade path for both: a
per-venue replay through the policy values once they exist, and an RSS-based soak for the
native heaps.
"""

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

import pytest


_HERE = Path(__file__).resolve().parent
_CODE_ROOT = _HERE.parent  # the tree under test: the image's /app, or a checkout's platform/
_TRADE_FIXTURES = _CODE_ROOT / "collector_core" / "tests" / "fixtures"


def _baseline_path() -> Path:
    """Return the checkout's `tests/fixtures/hotpath_baseline.json` (see the module docstring)."""
    source = os.environ.get("PLATFORM_SOURCE_DIR")
    tests = Path(source) / "tests" if source else _HERE
    return tests / "fixtures" / "hotpath_baseline.json"


_BASELINE = _baseline_path()

_INSTRUMENTS_PER_VENUE = 10
_DELTAS_PER_INSTRUMENT = 50
_BOOK_LEVELS = 20
_REPETITIONS = 3
_WALL_TIME_FACTOR = 2.0
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


# -- the burst (runs in the measuring subprocess) -------------------------------------------------


def _venues() -> list[dict[str, Any]]:
    """Venue shape: id format, time source, trade fixture and its parser, fixture precisions."""
    from collector_core import trade_backfill

    return [
        {
            "iid": "{t}-USD-PERP.DYDX",
            "source": "arrival",
            "fixture": "dydx_trades_btc_usd_20260921.json",
            "parse": trade_backfill.parse_dydx_trades,
            "precisions": (0, 4),
        },
        {
            "iid": "{t}USDT-LINEAR.BYBIT",
            "source": "venue",
            "fixture": "bybit_trades_btcusdt_linear_20260921.json",
            "parse": trade_backfill.parse_bybit_trades,
            "precisions": (2, 3),
        },
        {
            "iid": "{t}-USD-PERP.HYPERLIQUID",
            "source": "venue",
            "fixture": "hyperliquid_recent_trades_btc_20260921.json",
            "parse": trade_backfill.parse_hyperliquid_trades,
            "precisions": (1, 5),
        },
    ]


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


def _book_messages(iid: str, now_ns: int) -> list[Any]:
    """One top-20 snapshot (Clear + 20 levels a side), then single-level size updates."""
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
    for n in range(_BOOK_LEVELS):
        snapshot.append(level(OrderSide.BUY, n, n + 1, BookAction.ADD, now_ns))
        snapshot.append(level(OrderSide.SELL, n, n + 1, BookAction.ADD, now_ns))
    messages = [OrderBookDeltas(inst, snapshot)]
    for k in range(_DELTAS_PER_INSTRUMENT):
        side = OrderSide.BUY if k % 2 == 0 else OrderSide.SELL
        ts = now_ns + (k + 1) * 1_000
        update = level(side, k % _BOOK_LEVELS, k + 2, BookAction.UPDATE, ts)
        messages.append(OrderBookDeltas(inst, [update]))
    return messages


def _trades(venue: dict[str, Any], iid: str, tag: str, now_ns: int) -> list[Any]:
    """Return the venue's recorded trades for `iid`, ids tagged per burst, clocks set to now."""
    from nautilus_trader.model.data import TradeTick
    from nautilus_trader.model.identifiers import TradeId

    payload = json.loads((_TRADE_FIXTURES / venue["fixture"]).read_text())
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


def _burst(venue: dict[str, Any], iids: list[str], tag: str) -> list[Any]:
    """Per instrument: snapshot, deltas and trades, interleaved as a socket would deliver them."""
    now_ns = _PINNED_NS - _BURST_LEAD_NS
    burst: list[Any] = []
    for iid in iids:
        book = _book_messages(iid, now_ns)
        trades = _trades(venue, iid, tag, now_ns)
        burst.append(book[0])
        for n in range(max(len(book) - 1, len(trades))):
            burst.extend(book[1 + n : 2 + n])
            burst.extend(trades[n : n + 1])
    return burst


def _collectors(root: Path) -> list[tuple[Any, list[str], dict[str, Any]]]:
    from collector_core.collector import Collector
    from collector_core.config import CoreConfig

    built = []
    for n, venue in enumerate(_venues()):
        iids = [venue["iid"].format(t=t) for t in _TICKERS[:_INSTRUMENTS_PER_VENUE]]
        catalog = root / f"catalog_{n}"
        os.environ["CANDLES_DB_PATH"] = str(root / f"candles_{n}.db")
        config = CoreConfig(
            environment="mainnet",
            catalog_path=str(catalog),
            instruments=tuple(iids),
            book_time_source=venue["source"],
        )
        built.append((Collector(config, object()), iids, venue))
    return built


_Prepared = list[tuple[Any, list[Any]]]  # (collector, measured burst) per venue


def _ingest(collector: Any, burst: list[Any]) -> None:
    """One burst as the live path runs it: every message, then the second's close."""
    process: Callable[[Any], None] = collector._process_data
    for message in burst:
        process(message)
    if collector._venue_time:
        collector._drain_pending_deltas(_SECOND_CLOSE_NS)


def _prepared(root: Path, tag: str) -> _Prepared:
    """Fresh collectors, warmed with one burst; returns (collector, measured burst) per venue."""
    prepared = []
    for collector, iids, venue in _collectors(root):
        _ingest(collector, _burst(venue, iids, f"warm{tag}"))
        prepared.append((collector, _burst(venue, iids, f"run{tag}")))
    return prepared


def _replay(prepared: _Prepared) -> int:
    count = 0
    for collector, burst in prepared:
        _ingest(collector, burst)
        count += len(burst)
    return count


def _assert_the_live_path_was_taken(prepared: _Prepared) -> None:
    """
    Every message took the accepted path: no trade dropped as stale or replayed, none late or
    ahead of its second, no delta before a snapshot or late for its second, every held delta
    applied into a live book, nothing ledgered. Otherwise the numbers would measure a different
    path than the baseline did.
    """
    from observability import error_ledger

    for collector, burst in prepared:
        trades = sum(1 for message in burst if type(message).__name__ == "TradeTick")
        bypassed = {
            "stale": dict(collector._stale_trades_dropped),
            "duplicate": dict(collector._duplicate_trades_dropped),
            "duplicate_feed": dict(collector._duplicate_feed_dropped),
            "late": dict(collector._late_trades),
            "ahead": dict(collector._ahead_trades),
            "before_snapshot": dict(collector._deltas_before_snapshot_dropped),
            "late_deltas": dict(collector._late_deltas),
            "still_held": {iid: len(held) for iid, held in collector._pending_deltas.items()},
        }
        assert not any(bypassed.values()), f"replay left the live path: {bypassed}"
        books = len(collector._live_books)
        assert books == _INSTRUMENTS_PER_VENUE, f"{books} live books, expected every instrument's"
        live = (
            sum(
                len(t)
                for per_second in collector._venue_trades.values()
                for t in per_second.values()
            )
            if collector._venue_time
            else sum(len(t) for t in collector._second_trades.values())
        )
        assert live == 2 * trades, f"{live} trades folded live, expected {2 * trades} (warm + run)"
    assert error_ledger.counts() == {}, f"the replay ledgered: {error_ledger.counts()}"


def _traced(root: Path, tag: str) -> dict[str, float]:
    prepared = _prepared(root, tag)
    gc.collect()
    gc.disable()
    tracemalloc.start()
    try:
        start_snapshot = tracemalloc.take_snapshot()
        start_bytes, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        messages = _replay(prepared)
        end_bytes, peak_bytes = tracemalloc.get_traced_memory()
        end_snapshot = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
        gc.enable()
    _assert_the_live_path_was_taken(prepared)
    own = [tracemalloc.Filter(False, tracemalloc.__file__)]
    diff = end_snapshot.filter_traces(own).compare_to(start_snapshot.filter_traces(own), "filename")
    return {
        "messages": messages,
        "retained_blocks_per_message": sum(s.count_diff for s in diff) / messages,
        "retained_bytes_per_message": (end_bytes - start_bytes) / messages,
        "peak_bytes_per_message": (peak_bytes - start_bytes) / messages,
    }


def _timed(root: Path, tag: str) -> float:
    prepared = _prepared(root, tag)
    gc.collect()
    gc.disable()
    try:
        started = time.perf_counter_ns()
        messages = _replay(prepared)
        elapsed = time.perf_counter_ns() - started
    finally:
        gc.enable()
    _assert_the_live_path_was_taken(prepared)
    return elapsed / messages


def _pin_clock() -> None:
    """
    Pin `time.time_ns` for this measuring subprocess only: `_process_data` reads it for "now",
    and the burst is stamped relative to it. `perf_counter_ns` (the wall-time pass) is untouched.
    """
    time.time_ns = lambda: _PINNED_NS


def measure() -> dict[str, Any]:
    """Run in the measuring subprocess: median over fresh, warmed repetitions."""
    _pin_clock()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _traced(root / "interpreter_warmup", "x")  # first-use caches, imports, interned names
        traced = [_traced(root / f"t{n}", str(n)) for n in range(_REPETITIONS)]
        timed = [_timed(root / f"w{n}", str(n)) for n in range(_REPETITIONS)]
    result: dict[str, Any] = {"messages": traced[0]["messages"]}
    for key in _ALLOCATION_KEYS:
        result[key] = round(statistics.median(run[key] for run in traced), 4)
    result["ns_per_message"] = round(statistics.median(timed))
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


def _run_replay() -> dict[str, Any]:
    env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONPATH": str(_CODE_ROOT)}
    completed = subprocess.run(  # noqa: S603 (this interpreter, this file)
        [sys.executable, str(Path(__file__).resolve()), "--measure"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(_CODE_ROOT),
        env=env,
        timeout=600,
    )
    assert completed.returncode == 0, f"replay failed:\n{completed.stderr[-4000:]}"
    return json.loads(completed.stdout.strip().splitlines()[-1])


_MEASURED: dict[str, Any] = {}


def _measured() -> dict[str, Any]:
    if not _MEASURED:
        _MEASURED.update(_run_replay())
        _MEASURED["cpu"] = _cpu_fingerprint()
    return _MEASURED


def _burst_shape() -> dict[str, int]:
    """Return the burst's parameters: recorded with the baseline, checked against it every run."""
    return {
        "instruments": _INSTRUMENTS_PER_VENUE * len(_venue_names()),
        "deltas_per_instrument": _DELTAS_PER_INSTRUMENT,
        "book_levels": _BOOK_LEVELS,
        "repetitions": _REPETITIONS,
    }


def _python_version() -> str:
    return sys.version.split()[0]


def _baseline() -> dict[str, Any]:
    """
    Return the committed baseline, written from this run when there is none yet (AD-D5). A
    baseline from another interpreter fails every check that reads it: allocation counts are
    exact per CPython version, so comparing across versions would read an allocator change as a
    hot-path regression, or hide one.
    """
    if not _BASELINE.is_file():
        measured = _measured()
        record = {
            "messages": measured["messages"],
            **{key: measured[key] for key in _ALLOCATION_KEYS},
            "ns_per_message": measured["ns_per_message"],
            "host": {"cpu": measured["cpu"], "python": _python_version()},
            "recorded": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "burst": _burst_shape(),
        }
        try:
            _BASELINE.parent.mkdir(parents=True, exist_ok=True)
            _BASELINE.write_text(json.dumps(record, indent=2) + "\n")
        except OSError as e:
            # A read-only source mount (`make test`): passing here would be passing against
            # nothing. Recording is its own deliberate step.
            pytest.fail(
                f"no baseline at {_BASELINE} and it cannot be recorded there ({e}): "
                "run `make hotpath-baseline`, in a change that deliberately re-baselines"
            )
    baseline: dict[str, Any] = json.loads(_BASELINE.read_text())
    if baseline["host"]["python"] != _python_version():
        pytest.fail(
            f"the baseline was recorded on Python {baseline['host']['python']}; this is "
            f"{_python_version()}. Run `make test` (the collector image records and checks on "
            "one interpreter), or re-record with `make hotpath-baseline` in the change that "
            "deliberately bumps Python"
        )
    return baseline


def test_replay_is_the_burst_the_baseline_was_recorded_on() -> None:
    baseline, measured = _baseline(), _measured()
    changed = "the replay burst changed: a new burst needs a newly recorded baseline, in its own "
    changed += "reviewed change (`make hotpath-baseline` on today's code)"
    assert measured["messages"] == baseline["messages"], changed
    # The same message count can come from a differently shaped burst (more levels, fewer
    # deltas); the figures are only comparable for the recorded shape.
    assert _burst_shape() == baseline["burst"], changed


@pytest.mark.parametrize("key", _ALLOCATION_KEYS)
def test_allocations_per_message_do_not_exceed_the_baseline(key: str) -> None:
    baseline, measured = _baseline(), _measured()
    assert measured[key] <= baseline[key], (
        f"{key}: {measured[key]} > baseline {baseline[key]} -- the hot path gained an "
        "allocation per message (AD-D5). Remove it; or, when the cost is deliberate, re-baseline "
        "in its own reviewed change (`make hotpath-baseline`) and state the reason on audit row "
        "D-65. Never re-record to make this run pass"
    )


def test_wall_time_per_message_is_within_twice_the_baseline() -> None:
    baseline, measured = _baseline(), _measured()
    if baseline["host"]["cpu"] != measured["cpu"]:
        pytest.skip(
            f"wall time is only comparable on the baseline's CPU ({baseline['host']['cpu']}); "
            f"this is {measured['cpu']}. Allocations were still checked."
        )
    limit = _WALL_TIME_FACTOR * baseline["ns_per_message"]
    assert measured["ns_per_message"] <= limit, (
        f"{measured['ns_per_message']} ns/message > {_WALL_TIME_FACTOR}x baseline "
        f"{baseline['ns_per_message']} ns (AD-D5)"
    )


if __name__ == "__main__" and sys.argv[1:] == ["--measure"]:
    print(json.dumps(measure()))
