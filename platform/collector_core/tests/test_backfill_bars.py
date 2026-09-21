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
Tests for `collector_core.backfill_bars`.

Real Nautilus objects and a real `ParquetDataCatalog` throughout -- nothing is mocked (TEST-01).
The venue call is the one thing that cannot run here (no outbound network), so `_Session.fetch` is
an injected plain callable and the fetch-and-write path is exercised through a fake fetcher. That
matters: two of this story's defects (a window with an interior hole sealed as covered, and the
Hyperliquid retention short-circuit firing on a merely front-short window) reached review precisely
because every earlier test stopped at the pure helpers.
"""

import asyncio
import itertools
import logging
import tempfile
from collections.abc import Awaitable
from collections.abc import Callable
from decimal import Decimal

import pytest

from collector_core import backfill_bars
from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_MIN_NS = 60_000_000_000
_DAY_NS = backfill_bars._DAY_NS
# 2026-06-02T00:00:00Z, i.e. any "now" safely after the day the catalog tests archive.
_NOW_NS = 1_780_358_400_000_000_000


def _instrument(symbol: str, venue: str) -> CryptoPerpetual:
    iid = InstrumentId(Symbol(symbol), Venue(venue))
    return CryptoPerpetual(
        instrument_id=iid,
        raw_symbol=Symbol(symbol),
        base_currency=BTC,
        quote_currency=USDC,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price(0.1, 1),
        size_increment=Quantity(0.001, 3),
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        ts_event=0,
        ts_init=0,
    )


def _bar_type(symbol: str, venue: str, spec: str = "1-MINUTE-LAST") -> BarType:
    return BarType.from_str(f"{symbol}.{venue}-{spec}-EXTERNAL")


def _bars(
    bar_type: BarType,
    stamps: list[int],
    price_precision: int = 1,
    size_precision: int = 3,
) -> list[Bar]:
    return [
        Bar(
            bar_type,
            Price(100.0, price_precision),
            Price(101.0, price_precision),
            Price(99.0, price_precision),
            Price(100.5, price_precision),
            Quantity(1.0, size_precision),
            ts,
            ts,
        )
        for ts in stamps
    ]


def _stamps(w_start: int, w_end: int) -> list[int]:
    return list(range(w_start, w_end + 1, _MIN_NS))


_Serve = Callable[[int, int], list[int]]


def _fetcher(
    serve: _Serve,
    calls: list[tuple[int, int]],
    precisions: tuple[int, int] = (1, 3),
) -> Callable[[BarType, int, int], Awaitable[list[Bar]]]:
    """Build a `_Session.fetch` serving exactly the stamps `serve` names, recording every call."""

    async def fetch(bar_type: BarType, w_start: int, w_end: int) -> list[Bar]:
        calls.append((w_start, w_end))
        return _bars(bar_type, serve(w_start, w_end), *precisions)

    return fetch


def _session(
    serve: _Serve,
    calls: list[tuple[int, int]],
    stop_on_empty: bool = False,
    precisions: tuple[int, int] = (1, 3),
) -> backfill_bars._Session:
    return backfill_bars._Session(
        _fetcher(serve, calls, precisions), 0.0, stop_on_empty=stop_on_empty
    )


def _job(symbol: str, venue: str) -> backfill_bars._Job:
    return backfill_bars._Job(_bar_type(symbol, venue), _instrument(symbol, venue))


def _query(catalog: ParquetDataCatalog, bar_type: BarType, start: int, end: int) -> list[Bar]:
    """Read the bars back, always time-bounded even at fixture scale (MEM-01's pattern)."""
    config = BacktestDataConfig(
        catalog_path=catalog.path,
        data_cls=Bar,
        bar_types=[str(bar_type)],
        start_time=start,
        end_time=end,
    )
    return catalog.query(**config.query)


# -- WINDOW ARITHMETIC ----------------------------------------------------------------------------


def test_windows_splits_an_exact_multiple_of_limit_into_contiguous_bar_aligned_windows() -> None:
    windows = backfill_bars._windows(0, 9 * _MIN_NS, _MIN_NS, 5)
    assert windows == [(0, 4 * _MIN_NS), (5 * _MIN_NS, 9 * _MIN_NS)]
    assert all(low % _MIN_NS == 0 and high % _MIN_NS == 0 for low, high in windows)
    assert windows[1][0] == windows[0][1] + _MIN_NS


def test_windows_remainder_window_is_short_and_bar_count_is_exact() -> None:
    windows = backfill_bars._windows(0, 6 * _MIN_NS, _MIN_NS, 5)
    assert windows == [(0, 4 * _MIN_NS), (5 * _MIN_NS, 6 * _MIN_NS)]
    assert [backfill_bars._bar_count(a, b, _MIN_NS) for a, b in windows] == [5, 2]


def test_windows_single_bar_range_returns_one_degenerate_window() -> None:
    assert backfill_bars._windows(_MIN_NS, _MIN_NS, _MIN_NS, 1000) == [(_MIN_NS, _MIN_NS)]


def test_windows_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="is before start"):
        backfill_bars._windows(2 * _MIN_NS, _MIN_NS, _MIN_NS, 10)


def test_windows_rejects_non_positive_limit_because_the_cursor_would_never_advance() -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        backfill_bars._windows(0, _MIN_NS, _MIN_NS, 0)


def test_windows_rejects_a_bound_that_is_not_a_bar_close() -> None:
    with pytest.raises(ValueError, match="multiples of bar_ns"):
        backfill_bars._windows(1, _MIN_NS, _MIN_NS, 10)


def test_bar_floor_and_ceil_snap_to_the_absolute_epoch_grid() -> None:
    assert backfill_bars._bar_floor(_MIN_NS + 1, _MIN_NS) == _MIN_NS
    assert backfill_bars._bar_floor(_MIN_NS, _MIN_NS) == _MIN_NS
    assert backfill_bars._bar_ceil(_MIN_NS + 1, _MIN_NS) == 2 * _MIN_NS
    assert backfill_bars._bar_ceil(_MIN_NS, _MIN_NS) == _MIN_NS


# -- DAY RANGE ------------------------------------------------------------------------------------


def test_day_range_maps_inclusive_days_to_the_first_and_last_close_of_those_days() -> None:
    start_ns, end_ns = backfill_bars._day_range_ns("2026-06-01", "2026-06-01", _MIN_NS, _NOW_NS)
    midnight = backfill_bars._parse_date_ns("2026-06-01")
    assert start_ns == midnight + _MIN_NS, "a close-stamped bar belongs to the day it opened in"
    assert end_ns == midnight + _DAY_NS, "the 23:59 bar closes at the next midnight"


def test_day_range_end_is_clamped_to_the_last_closed_bar() -> None:
    now_ns = backfill_bars._parse_date_ns("2026-06-01") + 3 * _MIN_NS + 12_345
    _, end_ns = backfill_bars._day_range_ns("2026-06-01", "2026-12-31", _MIN_NS, now_ns)
    assert end_ns == backfill_bars._parse_date_ns("2026-06-01") + 3 * _MIN_NS


def test_day_range_clamp_can_empty_the_request_when_it_is_entirely_in_the_future() -> None:
    now_ns = backfill_bars._parse_date_ns("2026-06-01")
    start_ns, end_ns = backfill_bars._day_range_ns("2026-07-01", "2026-07-02", _MIN_NS, now_ns)
    assert end_ns < start_ns


def test_day_range_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="is before start"):
        backfill_bars._day_range_ns("2026-06-03", "2026-06-01", _MIN_NS, _NOW_NS)


def test_day_range_rejects_a_date_before_the_epoch() -> None:
    with pytest.raises(ValueError, match="on or after 1970-01-01"):
        backfill_bars._day_range_ns("1969-06-01", "1969-06-02", _MIN_NS, _NOW_NS)


def test_request_bounds_convert_close_times_back_to_the_venues_open_time_domain() -> None:
    close_ns = backfill_bars._parse_date_ns("2026-06-01") + _MIN_NS
    assert backfill_bars._dt(close_ns - _MIN_NS).isoformat() == "2026-06-01T00:00:00+00:00"
    assert backfill_bars._dt(close_ns).isoformat() == "2026-06-01T00:01:00+00:00"


# -- GAP SNAPPING ---------------------------------------------------------------------------------


def test_real_gaps_drops_the_sub_bar_sliver_a_completed_window_leaves_behind() -> None:
    sliver = [(4 * _MIN_NS + 1, 5 * _MIN_NS - 1)]
    assert backfill_bars._real_gaps(sliver, _MIN_NS) == []


def test_real_gaps_snaps_a_real_gap_inward_to_bar_closes() -> None:
    gaps = [(4 * _MIN_NS + 1, 9 * _MIN_NS - 1)]
    assert backfill_bars._real_gaps(gaps, _MIN_NS) == [(5 * _MIN_NS, 8 * _MIN_NS)]


def test_covered_spans_merges_the_complement_of_the_gaps_into_contiguous_runs() -> None:
    spans = backfill_bars._covered_spans(0, 10 * _MIN_NS, [(3 * _MIN_NS, 4 * _MIN_NS)])
    assert spans == [(0, 3 * _MIN_NS - 1), (4 * _MIN_NS + 1, 10 * _MIN_NS)]


# -- CLEANING / COVERAGE --------------------------------------------------------------------------


def test_clean_counts_and_logs_every_discard(caplog: pytest.LogCaptureFixture) -> None:
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    served = _bars(bar_type, [0, _MIN_NS, _MIN_NS, _MIN_NS + 1, 99 * _MIN_NS])
    with caplog.at_level(logging.WARNING, logger=backfill_bars.__name__):
        kept, discarded = _run_clean(served, 0, 2 * _MIN_NS, bar_type)
    assert [b.ts_init for b in kept] == [0, _MIN_NS]
    assert discarded == 3
    assert "1 out of window, 1 off the 60000000000 ns grid, 1 duplicate" in caplog.text


def _run_clean(served: list[Bar], w_start: int, w_end: int, bar_type: BarType) -> tuple:
    return backfill_bars._clean(served, w_start, w_end, _MIN_NS, str(bar_type))


def test_coverage_reports_a_short_front_as_its_own_case(caplog: pytest.LogCaptureFixture) -> None:
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    bars = _bars(bar_type, _stamps(3 * _MIN_NS, 9 * _MIN_NS))
    with caplog.at_level(logging.WARNING, logger=backfill_bars.__name__):
        missing = backfill_bars._report_coverage(str(bar_type), bars, 0, 9 * _MIN_NS, _MIN_NS)
    assert missing == 3
    assert "missing 3 front, 0 tail, 0 interior" in caplog.text


def test_coverage_reports_a_short_tail_as_its_own_case(caplog: pytest.LogCaptureFixture) -> None:
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    bars = _bars(bar_type, _stamps(0, 7 * _MIN_NS))
    with caplog.at_level(logging.WARNING, logger=backfill_bars.__name__):
        missing = backfill_bars._report_coverage(str(bar_type), bars, 0, 9 * _MIN_NS, _MIN_NS)
    assert missing == 2
    assert "missing 0 front, 2 tail, 0 interior" in caplog.text


def test_coverage_reports_an_interior_hole_as_its_own_case(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    stamps = [t for t in _stamps(0, 9 * _MIN_NS) if t not in (4 * _MIN_NS, 5 * _MIN_NS)]
    with caplog.at_level(logging.WARNING, logger=backfill_bars.__name__):
        missing = backfill_bars._report_coverage(
            str(bar_type), _bars(bar_type, stamps), 0, 9 * _MIN_NS, _MIN_NS
        )
    assert missing == 2
    assert "missing 0 front, 0 tail, 2 interior" in caplog.text


def test_coverage_reports_a_fully_empty_window_as_its_own_case_not_as_a_short_front(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    with caplog.at_level(logging.WARNING, logger=backfill_bars.__name__):
        missing = backfill_bars._report_coverage(str(bar_type), [], 0, 9 * _MIN_NS, _MIN_NS)
    assert missing == 10
    assert "returned no bars at all" in caplog.text
    assert "front" not in caplog.text


def test_contiguous_runs_split_at_every_interior_hole() -> None:
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    bars = _bars(bar_type, [0, _MIN_NS, 4 * _MIN_NS, 5 * _MIN_NS])
    runs = backfill_bars._contiguous_runs(bars, _MIN_NS)
    assert [[b.ts_init for b in run] for run in runs] == [
        [0, _MIN_NS],
        [4 * _MIN_NS, 5 * _MIN_NS],
    ]


# -- HYPERLIQUID CLOSE-STAMPING -------------------------------------------------------------------


def test_close_stamp_shifts_hyperliquid_open_times_by_one_interval_keeping_ohlcv_objects() -> None:
    bar_type = _bar_type("BTC-USDC-PERP", "HYPERLIQUID")
    original = _bars(bar_type, [0, _MIN_NS])
    stamped = backfill_bars._close_stamp(original, _MIN_NS)
    assert [b.ts_event for b in stamped] == [_MIN_NS, 2 * _MIN_NS]
    assert [b.ts_init for b in stamped] == [_MIN_NS, 2 * _MIN_NS]
    # Cython hands back a fresh wrapper per attribute access, so identity is not observable --
    # what must hold is that the raw integer and the precision label crossed over untouched.
    pairs = list(zip(original, stamped, strict=True))
    assert all((a.open.raw, a.open.precision) == (b.open.raw, b.open.precision) for a, b in pairs)
    assert all(
        (a.volume.raw, a.volume.precision) == (b.volume.raw, b.volume.precision) for a, b in pairs
    )


# -- VALIDATION -----------------------------------------------------------------------------------


def _catalog_with(tmp: str, *instruments: CryptoPerpetual) -> ParquetDataCatalog:
    catalog = ParquetDataCatalog(tmp)
    catalog.write_data(list(instruments))
    return catalog


@pytest.mark.parametrize(
    "spec",
    ["1-WEEK-LAST", "3-DAY-LAST", "1-SECOND-LAST", "7-MINUTE-LAST", "8-HOUR-LAST", "1-MONTH-LAST"],
)
def test_validate_rejects_bar_specs_that_are_off_grid_or_unserved(spec: str) -> None:
    """
    Every one of these would otherwise be fetched and then discarded as off-grid, or 500 mid-run.

    `7-MINUTE` never reaches this tool's own table -- Nautilus's `BarType.from_str` rejects it
    first -- so the supported set has to be named on that path too.
    """
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, _instrument("BTCUSDT-LINEAR", "BYBIT"))
        with pytest.raises(SystemExit, match="1/3/5/15/30-MINUTE"):
            backfill_bars._validate(catalog, ["BTCUSDT-LINEAR.BYBIT"], spec, "mainnet")


def test_validate_rejects_a_tick_spec_and_a_non_last_price_type() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, _instrument("BTCUSDT-LINEAR", "BYBIT"))
        with pytest.raises(SystemExit, match="not supported"):
            backfill_bars._validate(catalog, ["BTCUSDT-LINEAR.BYBIT"], "100-TICK-LAST", "mainnet")
        with pytest.raises(SystemExit, match="price type MID is not supported"):
            backfill_bars._validate(catalog, ["BTCUSDT-LINEAR.BYBIT"], "1-MINUTE-MID", "mainnet")


def test_validate_rejects_dydx_and_points_at_the_one_second_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, _instrument("BTC-USD-PERP", "DYDX"))
        with pytest.raises(SystemExit, match="1 s archive"):
            backfill_bars._validate(catalog, ["BTC-USD-PERP.DYDX"], "1-MINUTE-LAST", "mainnet")


def test_validate_rejects_an_instrument_absent_from_the_catalog() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, _instrument("BTCUSDT-LINEAR", "BYBIT"))
        with pytest.raises(SystemExit, match="no instrument definition in the catalog"):
            backfill_bars._validate(catalog, ["ETHUSDT-LINEAR.BYBIT"], "1-MINUTE-LAST", "mainnet")


def test_validate_reports_every_bad_instrument_before_any_good_one_is_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, _instrument("BTCUSDT-LINEAR", "BYBIT"))
        ids = ["BTCUSDT-LINEAR.BYBIT", "NOPE-LINEAR.BYBIT", "BTC-USD-PERP.DYDX"]
        with pytest.raises(SystemExit) as excinfo:
            backfill_bars._validate(catalog, ids, "1-MINUTE-LAST", "mainnet")
        assert "NOPE-LINEAR.BYBIT" in str(excinfo.value)
        assert "BTC-USD-PERP.DYDX" in str(excinfo.value)


def test_validate_rejects_the_bybit_only_demo_environment_for_hyperliquid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, _instrument("BTC-USDC-PERP", "HYPERLIQUID"))
        with pytest.raises(SystemExit, match="no 'demo' environment"):
            backfill_bars._validate(catalog, ["BTC-USDC-PERP.HYPERLIQUID"], "1-MINUTE-LAST", "demo")


def test_precision_mismatch_refuses_bars_that_disagree_with_the_catalog_definition() -> None:
    instrument = _instrument("BTCUSDT-LINEAR", "BYBIT")
    bar_type = _bar_type("BTCUSDT-LINEAR", "BYBIT")
    assert backfill_bars._precision_mismatch(_bars(bar_type, [0]), instrument) is None
    mismatch = backfill_bars._precision_mismatch(
        _bars(bar_type, [0], price_precision=2), instrument
    )
    assert mismatch is not None
    assert "price precision" in mismatch


# -- FETCH AND WRITE (injected fake fetcher, real catalog) -----------------------------------------


def test_a_full_day_written_by_the_planner_replans_to_zero_windows_on_a_second_run() -> None:
    """AC #3: the second run issues zero REST calls, not merely zero writes."""
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    calls: list[tuple[int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        start_ns, end_ns = backfill_bars._day_range_ns("2026-06-01", "2026-06-01", _MIN_NS, _NOW_NS)
        first = asyncio.run(
            backfill_bars._run_instrument(
                catalog, job, start_ns, end_ns, _MIN_NS, _session(_stamps, calls), True
            )
        )
        assert first.planned == 2, "1440 one-minute bars split across two 1000-bar windows"
        assert first.written_bars == 1440
        assert len(calls) == 2

        stored = _query(catalog, job.bar_type, start_ns, end_ns)
        assert [stored[0].ts_init, stored[-1].ts_init] == [start_ns, end_ns]

        calls.clear()
        second = asyncio.run(
            backfill_bars._run_instrument(
                catalog, job, start_ns, end_ns, _MIN_NS, _session(_stamps, calls), True
            )
        )
        assert second.planned == 0
        assert calls == [], "a fully archived range must issue no request at all"
        assert len(_query(catalog, job.bar_type, start_ns, end_ns)) == 1440


def test_backfilled_bars_load_through_the_catalog_query_path_with_no_duplicate_timestamps() -> None:
    """AC #4: `catalog.query(**BacktestDataConfig(...).query)` is BacktestNode's own loading path."""
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    calls: list[tuple[int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        start_ns, end_ns = _MIN_NS, 30 * _MIN_NS
        asyncio.run(
            backfill_bars._run_instrument(
                catalog, job, start_ns, end_ns, _MIN_NS, _session(_stamps, calls), True
            )
        )
        stored = _query(catalog, job.bar_type, start_ns, end_ns)
        stamps = [b.ts_event for b in stored]
        assert len(stamps) == 30
        assert len(set(stamps)) == len(stamps)
        assert all(isinstance(b, Bar) for b in stored)


def test_a_window_served_with_an_interior_hole_keeps_the_hole_replannable() -> None:
    """AC #11a: one `write_data()` per contiguous run, so the gap is never sealed as covered."""
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    hole = {10 * _MIN_NS, 11 * _MIN_NS}
    calls: list[tuple[int, int]] = []

    def serve(w_start: int, w_end: int) -> list[int]:
        return [t for t in _stamps(w_start, w_end) if t not in hole]

    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        start_ns, end_ns = _MIN_NS, 20 * _MIN_NS
        summary = asyncio.run(
            backfill_bars._run_instrument(
                catalog, job, start_ns, end_ns, _MIN_NS, _session(serve, calls), True
            )
        )
        assert summary.written_files == 2, "the hole must split the window into two files"
        assert summary.missing_bars == 2
        assert len(_query(catalog, job.bar_type, start_ns, end_ns)) == 18

        replan = backfill_bars._plan(catalog, job.bar_type, start_ns, end_ns, _MIN_NS)
        assert replan == [(10 * _MIN_NS, 11 * _MIN_NS)]


def test_a_front_short_hyperliquid_window_still_attempts_older_windows() -> None:
    """AC #11b, first half: a single quiet minute must not abandon the venue's older history."""
    job = _job("BTC-USDC-PERP", "HYPERLIQUID")
    calls: list[tuple[int, int]] = []

    def serve(w_start: int, w_end: int) -> list[int]:
        return _stamps(w_start + 2 * _MIN_NS, w_end)

    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        start_ns, end_ns = _MIN_NS, 3000 * _MIN_NS
        summary = asyncio.run(
            backfill_bars._run_instrument(
                catalog,
                job,
                start_ns,
                end_ns,
                _MIN_NS,
                _session(serve, calls, stop_on_empty=True),
                True,
            )
        )
        assert summary.planned == 3
        assert len(calls) == 3, "every window must still be attempted"
        assert summary.skipped_windows == 0


def test_a_fully_empty_hyperliquid_window_short_circuits_the_older_ones() -> None:
    """AC #11b, second half: only a fully empty window is read as the retention floor."""
    job = _job("BTC-USDC-PERP", "HYPERLIQUID")
    calls: list[tuple[int, int]] = []

    def serve(w_start: int, w_end: int) -> list[int]:
        return [] if w_start < 2000 * _MIN_NS else _stamps(w_start, w_end)

    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        start_ns, end_ns = _MIN_NS, 3000 * _MIN_NS
        summary = asyncio.run(
            backfill_bars._run_instrument(
                catalog,
                job,
                start_ns,
                end_ns,
                _MIN_NS,
                _session(serve, calls, stop_on_empty=True),
                True,
            )
        )
        assert summary.planned == 3
        assert len(calls) == 2, "the empty window must stop the older ones"
        assert summary.skipped_windows == 1
        assert backfill_bars._plan(catalog, job.bar_type, start_ns, end_ns, _MIN_NS), (
            "an un-servable range must stay uncovered, never marked covered"
        )


def test_a_gap_larger_than_the_window_limit_splits_and_the_written_files_stay_disjoint() -> None:
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    calls: list[tuple[int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        start_ns, end_ns = _MIN_NS, 2500 * _MIN_NS
        summary = asyncio.run(
            backfill_bars._run_instrument(
                catalog, job, start_ns, end_ns, _MIN_NS, _session(_stamps, calls), True
            )
        )
        assert summary.planned == 3
        assert summary.written_files == 3
        intervals = sorted(catalog.get_intervals(Bar, str(job.bar_type)))
        assert all(a[1] < b[0] for a, b in itertools.pairwise(intervals))
        assert summary.written_bars == 2500


def test_report_only_plans_without_fetching_or_writing(caplog: pytest.LogCaptureFixture) -> None:
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        with caplog.at_level(logging.INFO, logger=backfill_bars.__name__):
            summary = asyncio.run(
                backfill_bars._run_instrument(
                    catalog, job, _MIN_NS, 10 * _MIN_NS, _MIN_NS, None, False
                )
            )
        assert summary.planned == 1
        assert summary.written_bars == 0
        assert "would fetch" in caplog.text
        assert catalog.get_intervals(Bar, str(job.bar_type)) == []


def test_one_failing_instrument_does_not_stop_the_others_and_every_summary_is_emitted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC #12: per-instrument isolation, with the summary emitted from a `finally`."""
    jobs = [_job(s, "BYBIT") for s in ("BTCUSDT-LINEAR", "ETHUSDT-LINEAR", "SOLUSDT-LINEAR")]
    calls: list[tuple[int, int]] = []

    async def fetch(bar_type: BarType, w_start: int, w_end: int) -> list[Bar]:
        calls.append((w_start, w_end))
        if "ETHUSDT" in str(bar_type):
            raise RuntimeError("venue returned HTTP 503")
        return _bars(bar_type, _stamps(w_start, w_end))

    session = backfill_bars._Session(fetch, 0.0, stop_on_empty=False)
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, *[j.instrument for j in jobs])
        with caplog.at_level(logging.INFO, logger=backfill_bars.__name__):
            summaries = [
                asyncio.run(
                    backfill_bars._run_instrument(
                        catalog, job, _MIN_NS, 10 * _MIN_NS, _MIN_NS, session, True
                    )
                )
                for job in jobs
            ]
        assert [bool(s.error) for s in summaries] == [False, True, False]
        assert "HTTP 503" in (summaries[1].error or "")
        assert [s.written_bars for s in summaries] == [10, 0, 10]
        assert len(calls) == 3, "the third instrument must still be attempted"
        assert caplog.text.count("window(s) planned") == 3


def test_a_delisted_instrument_fails_only_itself(caplog: pytest.LogCaptureFixture) -> None:
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    calls: list[tuple[int, int]] = []
    session = backfill_bars._Session(
        _fetcher(_stamps, calls),
        0.0,
        stop_on_empty=False,
        missing=frozenset({job.instrument.id.value}),
    )
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        with caplog.at_level(logging.ERROR, logger=backfill_bars.__name__):
            summary = asyncio.run(
                backfill_bars._run_instrument(
                    catalog, job, _MIN_NS, 10 * _MIN_NS, _MIN_NS, session, True
                )
            )
        assert summary.error is not None
        assert "delisted" in summary.error
        assert calls == []


def test_a_precision_mismatch_against_the_catalog_definition_fails_the_instrument() -> None:
    job = _job("BTCUSDT-LINEAR", "BYBIT")
    calls: list[tuple[int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog_with(tmp, job.instrument)
        summary = asyncio.run(
            backfill_bars._run_instrument(
                catalog,
                job,
                _MIN_NS,
                10 * _MIN_NS,
                _MIN_NS,
                _session(_stamps, calls, precisions=(2, 3)),
                True,
            )
        )
        assert summary.error is not None
        assert "price precision" in summary.error
        assert catalog.get_intervals(Bar, str(job.bar_type)) == []
