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
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import chart_series
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
    return DydxSecondSnapshot(
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
        "bid": 105.0,
        "ask": 100.0,
        "mid": mid,
        # microprice = (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
        "micro": (105.0 * 1.0 + 100.0 * 2.0) / 3.0,
        # CVD-weighted price: mid + (net / total volume) * spread / 2 -- the spread is negative here
        "price": mid + ((3.0 - 1.0) / 4.0) * (100.0 - 105.0) * 0.5,
    }


def test_a_touched_second_is_priced_too() -> None:
    (row,) = price_series_rows([_snapshot(_BASE_NS, [100.0], [100.0])])
    assert (row["bid"], row["ask"], row["mid"], row["price"]) == (100.0, 100.0, 100.0, 100.0)


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


def test_gap_marker_inserted_between_rows_separated_by_more_than_threshold(tmp_path: Path) -> None:
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

    assert len(items) == 3  # real row, gap marker, real row
    real_first, gap, real_second = items
    assert real_first["bid"] is not None
    assert real_second["bid"] is not None
    assert gap["t"] == real_second["t"] - 1
    assert gap["bid"] is None
    assert gap["ask"] is None
    assert gap["mid"] is None
    assert gap["micro"] is None
    assert gap["price"] is None


def test_seconds_exactly_at_the_gap_threshold_are_not_broken() -> None:
    first = _snapshot(_BASE_NS, [100.0], [101.0])
    second = _snapshot(_BASE_NS + SNAPSHOT_GAP_THRESHOLD_MS * 1_000_000, [100.0], [101.0])
    assert [r["bid"] for r in price_series_rows([first, second])] == [100.0, 100.0]


# --- the bar-spaced gap marker: candles, indicator series, indicator values ----------------------


def test_bar_gap_marker_sits_one_bar_after_the_earlier_row() -> None:
    rows = [{"t": 0, "c": 1.0}, {"t": 60_000, "c": 2.0}, {"t": 240_000, "c": 3.0}]

    assert with_gap_markers(rows, 60) == [
        {"t": 0, "c": 1.0},
        {"t": 60_000, "c": 2.0},
        {"t": 120_000},  # the break starts right where real data stops
        {"t": 240_000, "c": 3.0},
    ]


def test_bar_gap_rule_leaves_contiguous_rows_and_page_edges_alone() -> None:
    rows = [{"t": 0}, {"t": 5_000}, {"t": 10_000}]
    assert with_gap_markers(rows, 5) == rows
    assert with_gap_markers([], 60) == []


# --- candle-page window arithmetic (moved from data_api/tests/test_candles.py) ------------------


def test_archive_fallback_window_is_capped_for_every_bar_size() -> None:
    week_ns = 7 * 86_400 * 1_000_000_000
    assert chart_series._candle_window_start_ns(0, 120, 3600) == -week_ns
    assert (
        chart_series._candle_window_start_ns(0, 120, 86_400) == -week_ns
    )  # no wider tier without rollups


def test_sub_minute_bars_look_back_at_least_an_hour() -> None:
    assert chart_series._candle_window_start_ns(0, 120, 1) == -3600 * 1_000_000_000
