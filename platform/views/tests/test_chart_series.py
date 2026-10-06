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
`views.chart_series`'s rendering rules and the Lines-mode read model (Story 24.2): a crossed
second in the archive is priced like any other, an empty top of book is a loud failure, and the two
gap-marker rules are the only thing that changes what is drawn.
"""

from pathlib import Path

import pytest
from kernel.indicators import OFI_GAP_NS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import chart_series
from views.chart_series import MAX_GAP_ROWS_PER_GAP
from views.chart_series import SNAPSHOT_GAP_THRESHOLD_MS
from views.chart_series import EmptyTopOfBook
from views.chart_series import price_series_rows
from views.chart_series import snapshot_series_page
from views.chart_series import with_gap_markers


_IID = "BTC-USD-PERP.DYDX"
_BASE_NS = 1_800_000_000_000_000_000


def _snapshot(
    ts_ns: int,
    bids: list[float],
    asks: list[float],
    buy_volume: float = 1.0,
    sell_volume: float = 0.5,
) -> DydxSecondSnapshot:
    return make_snapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=bids,
        bid_sizes=[2.0] * len(bids),
        ask_prices=asks,
        ask_sizes=[1.0] * len(asks),
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_count=1,
        sell_count=1,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


# --- Lines mode: the reader never re-validates the gate -----------------------------------------


def test_a_crossed_second_is_priced_exactly_like_any_other() -> None:
    """`bid >= ask` in the archive is rendered as written (AD-3): same formulas, nothing skipped."""
    crossed = _snapshot(_BASE_NS, [105.0], [100.0], buy_volume=3.0, sell_volume=1.0)

    (row,) = price_series_rows([crossed])

    mid = (105.0 + 100.0) / 2
    assert row == {
        "t": _BASE_NS // 1_000_000,
        # The stored integers and their precision (Story 30.2), not floats: 105.0 and 100.0.
        "bid_units": 1_050_000,
        "ask_units": 1_000_000,
        "price_precision": 4,
        "mid": mid,
        # microprice = (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
        "micro": (105.0 * 1.0 + 100.0 * 2.0) / 3.0,
        # CVD-weighted price: mid + (net / total volume) * spread / 2 -- the spread is negative here
        "price": mid + ((3.0 - 1.0) / 4.0) * (100.0 - 105.0) * 0.5,
    }


def test_an_undefined_microprice_is_null_never_the_mid() -> None:
    """Story 31.3: both top sizes zero -> no microprice; the mid is never passed off as one."""
    zero_tops = make_snapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[100.0],
        bid_sizes=[0.0],
        ask_prices=[101.0],
        ask_sizes=[0.0],
        ts_event=_BASE_NS,
    )

    (row,) = price_series_rows([zero_tops])

    assert (row["mid"], row["micro"]) == (100.5, None)


def test_the_replay_clears_the_previous_ofi_book_after_a_gap() -> None:
    """
    Story 31.3: a `ts_event` step over `OFI_GAP_NS` clears OFI's previous book before the next
    update, so the post-gap book is a baseline -- no contribution diffed across the gap.
    """
    bar = 60
    before = _snapshot(_BASE_NS, [100.0], [101.0])
    after_gap = _snapshot(_BASE_NS + OFI_GAP_NS + 1, [90.0], [91.0])  # a big move across the gap
    same_book = _snapshot(_BASE_NS + OFI_GAP_NS + 2_000_000_001, [90.0], [91.0])

    gapped = chart_series.replay_bucket_samples([before, after_gap], bar)
    continued = chart_series.replay_bucket_samples([before, after_gap, same_book], bar)

    assert [row["ofi"] for row in gapped.values()] == [None]  # still only a baseline
    assert [row["ofi"] for row in continued.values()] == [0.0]  # unchanged book: no flow


def test_a_touched_second_is_priced_too() -> None:
    (row,) = price_series_rows([_snapshot(_BASE_NS, [100.0], [100.0])])
    assert (row["bid_units"], row["ask_units"]) == (1_000_000, 1_000_000)
    assert (row["mid"], row["price"]) == (100.0, 100.0)


def test_the_served_mid_is_the_kernels_mid_price_not_an_inline_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSOT-01 (audit D-131): `mid` comes from `kernel.indicators.mid_price`, the one statement of it."""
    seen: list[dict] = []

    def recording_mid(snapshot: dict) -> float | None:
        seen.append(snapshot)
        return 12.5

    monkeypatch.setattr(chart_series, "calc_mid_price", recording_mid)
    (row,) = price_series_rows([_snapshot(_BASE_NS, [100.0], [101.0])])

    assert row["mid"] == 12.5
    assert [(s["bid_prices"][0], s["ask_prices"][0]) for s in seen] == [(100.0, 101.0)]


@pytest.mark.parametrize(("bids", "asks"), [([], [101.0]), ([100.0], []), ([], [])])
def test_an_empty_top_of_book_is_ledgered_and_raised_never_skipped(
    bids: list[float], asks: list[float]
) -> None:
    error_ledger.reset()
    healthy = _snapshot(_BASE_NS, [100.0], [101.0])
    empty = _snapshot(_BASE_NS + 1_000_000_000, bids, asks)

    with pytest.raises(EmptyTopOfBook, match=rf"{_IID} at ts_event={_BASE_NS + 1_000_000_000}"):
        price_series_rows([healthy, empty])

    assert error_ledger.counts() == {"views.snapshot_without_top": 1}


def _null_price_fields(row: dict) -> list[object]:
    return [row[k] for k in ("bid_units", "ask_units", "price_precision", "mid", "micro", "price")]


def test_a_lines_hole_is_one_gap_row_per_missing_second(tmp_path: Path) -> None:
    """Moved from `data_api/tests/test_snapshots.py` with the rule itself (Story 24.2)."""
    catalog_path = str(tmp_path / "catalog")
    # limit=10 -> main query window span = 10 * the 2x multiplier = 20s -- both rows below must
    # sit inside that window to be queried at all.
    earlier_ns = _BASE_NS - 10_000_000_000  # 10s before base
    later_ns = earlier_ns + 3_000_000_000  # 3s later -- exceeds the 2.5s gap threshold
    ParquetDataCatalog(catalog_path).write_data(
        [_snapshot(earlier_ns, [100.0], [101.0]), _snapshot(later_ns, [105.0], [106.0])]
    )

    items, _has_more = snapshot_series_page(catalog_path, _IID, _BASE_NS, 10)

    earlier_ms = earlier_ns // 1_000_000
    assert [r["t"] for r in items] == [earlier_ms + k * 1000 for k in range(4)]
    real_first, gap_one, gap_two, real_second = items
    assert real_first["bid_units"] is not None
    assert real_second["bid_units"] is not None
    for gap in (gap_one, gap_two):
        assert _null_price_fields(gap) == [None] * 6


def test_a_five_second_lines_hole_emits_rows_at_every_missing_second() -> None:
    first = _snapshot(_BASE_NS, [100.0], [101.0])
    second = _snapshot(_BASE_NS + 5_000_000_000, [100.0], [101.0])
    base_ms = _BASE_NS // 1_000_000

    rows = price_series_rows([first, second])

    assert [r["t"] - base_ms for r in rows] == [0, 1000, 2000, 3000, 4000, 5000]
    assert all(_null_price_fields(r) == [None] * 6 for r in rows[1:-1])
    assert rows[0]["bid_units"] is not None
    assert rows[-1]["bid_units"] is not None


def test_two_second_lines_spacing_is_not_a_gap() -> None:
    first = _snapshot(_BASE_NS, [100.0], [101.0])
    second = _snapshot(_BASE_NS + 2_000_000_000, [100.0], [101.0])
    assert [r["bid_units"] for r in price_series_rows([first, second])] == [1_000_000] * 2


def test_seconds_exactly_at_the_gap_threshold_are_not_broken() -> None:
    first = _snapshot(_BASE_NS, [100.0], [101.0])
    second = _snapshot(_BASE_NS + SNAPSHOT_GAP_THRESHOLD_MS * 1_000_000, [100.0], [101.0])
    assert [r["bid_units"] for r in price_series_rows([first, second])] == [1_000_000] * 2


def test_a_lines_page_whose_cursor_follows_a_hole_ends_on_a_real_row(tmp_path: Path) -> None:
    catalog_path = str(tmp_path / "catalog")
    real_ns = _BASE_NS - 5_000_000_000  # 5 s of hole between it and the cursor second
    ParquetDataCatalog(catalog_path).write_data(
        [_snapshot(real_ns, [100.0], [101.0]), _snapshot(_BASE_NS, [105.0], [106.0])]
    )

    items, _has_more = snapshot_series_page(catalog_path, _IID, _BASE_NS, 10)

    # The cursor second closes the hole but is not on this page, so neither is its gap run.
    assert [r["t"] for r in items] == [real_ns // 1_000_000]
    assert items[-1]["bid_units"] is not None


# --- the bar-spaced gap marker: candles, indicator series, indicator values ----------------------


def test_the_gap_row_cap_is_720() -> None:
    assert MAX_GAP_ROWS_PER_GAP == 720


def test_a_two_bar_hole_is_two_gap_rows_one_per_missing_bar() -> None:
    rows = [{"t": 0, "c": 1.0}, {"t": 60_000, "c": 2.0}, {"t": 240_000, "c": 3.0}]

    assert with_gap_markers(rows, 60) == [
        {"t": 0, "c": 1.0},
        {"t": 60_000, "c": 2.0},
        {"t": 120_000},  # the break starts right where real data stops
        {"t": 180_000},  # ... and is as wide as the hole
        {"t": 240_000, "c": 3.0},
    ]


def test_a_hole_over_the_cap_is_the_cap_contiguous_from_its_start() -> None:
    day_ms = 86_400_000
    rows = [{"t": 0, "c": 1.0}, {"t": day_ms, "c": 2.0}]

    out = with_gap_markers(rows, 60)

    assert out[0] == rows[0]
    assert out[1:-1] == [{"t": k * 60_000} for k in range(1, MAX_GAP_ROWS_PER_GAP + 1)]
    assert out[-2] == {"t": 43_200_000}
    assert out[-1] == rows[1]


def test_a_lines_hole_over_the_cap_is_capped_too() -> None:
    first = _snapshot(_BASE_NS, [100.0], [101.0])
    second = _snapshot(_BASE_NS + 3_600_000_000_000, [100.0], [101.0])  # an hour later

    rows = price_series_rows([first, second])

    assert len(rows) == MAX_GAP_ROWS_PER_GAP + 2
    base_ms = _BASE_NS // 1_000_000
    assert [r["t"] - base_ms for r in rows[1:-1]] == [
        k * 1000 for k in range(1, MAX_GAP_ROWS_PER_GAP + 1)
    ]


def test_bar_gap_rule_leaves_contiguous_rows_and_page_edges_alone() -> None:
    rows = [{"t": 0}, {"t": 5_000}, {"t": 10_000}]
    assert with_gap_markers(rows, 5) == rows
    assert with_gap_markers([], 60) == []


# --- candle-page window arithmetic (moved from data_api/tests/test_candles.py) ------------------


def test_archive_fallback_window_is_capped_for_every_bar_size() -> None:
    week_ns = 7 * 86_400 * 1_000_000_000
    assert chart_series._candle_window_span_ns(120, 3600) == week_ns
    assert chart_series._candle_window_span_ns(120, 86_400) == week_ns  # no wider tier


def test_sub_minute_bars_look_back_at_least_an_hour() -> None:
    assert chart_series._candle_window_span_ns(120, 1) == 3600 * 1_000_000_000


@pytest.mark.parametrize("bar_seconds", [1, 5, 60, 600, 1800, 2700, 3600, 86_400, 604_800])
@pytest.mark.parametrize("limit", [1, 2, 120, 500])
def test_the_archive_window_is_whole_buckets_within_the_cap(bar_seconds: int, limit: int) -> None:
    span_seconds = chart_series._candle_window_span_ns(limit, bar_seconds) // 1_000_000_000
    assert span_seconds % bar_seconds == 0
    assert bar_seconds <= span_seconds <= chart_series.MAX_QUERY_SPAN_SECONDS


def test_a_window_end_rounds_up_to_the_bucket_boundary_and_keeps_a_boundary() -> None:
    monday_ns = 4 * 86_400 * 1_000_000_000  # 1970-01-05, a 1W bucket start
    week_ns = 7 * 86_400 * 1_000_000_000
    assert chart_series._bucket_end_ns(monday_ns, 604_800) == monday_ns
    assert chart_series._bucket_end_ns(monday_ns + 1, 604_800) == monday_ns + week_ns
    assert chart_series._bucket_end_ns(monday_ns - 1, 604_800) == monday_ns
    assert chart_series._bucket_end_ns(2700_000_000_001, 2700) == 5400_000_000_000


def test_the_custom_indicator_replay_window_is_capped_at_the_query_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Review P1 (MEM-01): 500 1W candles span ~10 years; the custom replays' raw read window is capped
    at `MAX_QUERY_SPAN_SECONDS` back from the page's end, so bars before it read nothing (None).
    """
    from views import indicator_picker

    week_ms = 604_800_000
    kept = [
        {"t": k * week_ms, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0} for k in range(500)
    ]
    seen: list[indicator_picker.ReplayWindow] = []

    def capture(
        candles: list[dict], entries: object, window: indicator_picker.ReplayWindow
    ) -> tuple:
        seen.append(window)
        return {}, {}

    monkeypatch.setattr(chart_series, "candle_page", lambda *a, **k: (kept, False))
    monkeypatch.setattr(indicator_picker, "values_by_time", capture)

    chart_series.indicator_values_page(
        _IID,
        0,
        500,
        604_800,
        [],
        catalog_path="",
        candles_dir="",
        recent_rows=lambda *a: [],
        recent_liquidations=lambda *a: [],
    )

    (window,) = seen
    assert window.end_ms == 500 * week_ms
    assert window.end_ms - window.start_ms == chart_series.MAX_QUERY_SPAN_SECONDS * 1000


def test_compute_chart_series_spread_is_the_kernel_spread(tmp_path: Path) -> None:
    """Review P3: the one-tick spread at 8.578755 is 1e-06 exactly, not the cancelled difference."""
    catalog_path = str(tmp_path / "catalog")
    row = make_snapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=["8.578755"],
        bid_sizes=[1],
        ask_prices=["8.578756"],
        ask_sizes=[1],
        ts_event=_BASE_NS,
        price_precision=6,
        size_precision=0,
    )
    ParquetDataCatalog(catalog_path).write_data([row])

    series = chart_series.compute_chart_series(catalog_path, _IID, _BASE_NS - 1, _BASE_NS + 1)

    assert row.ask_prices[0] - row.bid_prices[0] != 1e-06  # the cancellation
    assert [p["value"] for p in series["spread"]] == [1e-06]
