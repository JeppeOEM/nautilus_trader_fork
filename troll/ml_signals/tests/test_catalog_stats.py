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
"""Self-check: gap/outage detection finds obvious cases and ignores regular spacing; price_stats arithmetic."""

import tempfile
from unittest.mock import MagicMock

from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals.catalog_stats import _overlapping_intervals
from ml_signals.catalog_stats import find_gaps
from ml_signals.catalog_stats import price_series
from ml_signals.catalog_stats import price_stats


_IID = "BTC-USD-PERP.DYDX"


def test_finds_a_single_obvious_gap() -> None:
    one_second = 1_000_000_000
    regular = [i * one_second for i in range(10)]  # 0s, 1s, 2s, ... 9s
    after_gap = [t + 600 * one_second for t in range(10, 15)]  # resumes 10 minutes later
    ts_ns = regular + after_gap

    gaps = find_gaps(ts_ns)

    assert len(gaps) == 1
    assert gaps[0] == (regular[-1], after_gap[0])


def test_no_gaps_in_regularly_spaced_series() -> None:
    one_second = 1_000_000_000
    ts_ns = [i * one_second for i in range(20)]

    assert find_gaps(ts_ns) == []


def test_overlapping_intervals_finds_only_shared_outage() -> None:
    # Mark price gapped 100-200 and 500-600; book deltas gapped 150-300.
    # Only the 150-200 overlap represents both streams being silent at once.
    mark_gaps = [(100, 200), (500, 600)]
    book_gaps = [(150, 300)]

    overlaps = _overlapping_intervals(mark_gaps, book_gaps)

    assert overlaps == [(150, 200)]


def test_overlapping_intervals_empty_when_no_shared_window() -> None:
    assert _overlapping_intervals([(100, 200)], [(300, 400)]) == []


# ---- price_stats ----

# Timestamps: 1 h = 3_600_000_000_000 ns, 24 h = 86_400_000_000_000 ns
_1H_NS  = 3_600 * 1_000_000_000
_24H_NS = 86_400 * 1_000_000_000


def _patched_price_stats(series: list[tuple[int, float]]) -> dict:
    """Run price_stats with a fixed series, bypassing the catalog."""
    with MagicMock() as mock_catalog:
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "ml_signals.catalog_stats.price_series", return_value=series
        ):
            return price_stats(mock_catalog, "ANY")


def test_price_stats_empty_returns_all_none() -> None:
    result = _patched_price_stats([])
    assert result == {"price": None, "pct_change_1h": None, "pct_change_24h": None, "volatility": None}


def test_price_stats_single_point_returns_price_only() -> None:
    result = _patched_price_stats([(1_000_000_000, 42.0)])
    assert result["price"] == 42.0
    assert result["pct_change_1h"] is None     # only one point, no history span
    assert result["pct_change_24h"] is None
    assert result["volatility"] is None        # need 2+ returns


def test_price_stats_pct_1h_correct() -> None:
    # series spans 2 h: base-2h → 100, base-1h → 110, base → 120
    base = 200 * _1H_NS
    series = [(base - 2 * _1H_NS, 100.0), (base - _1H_NS, 110.0), (base, 120.0)]
    result = _patched_price_stats(series)
    # pct_change_1h: base price at base-1h = 110; (120-110)/110 * 100 ≈ 9.09
    assert result["pct_change_1h"] is not None
    assert abs(result["pct_change_1h"] - (120 - 110) / 110 * 100) < 1e-6


def test_price_stats_pct_24h_none_when_series_too_short() -> None:
    base = 200 * _1H_NS
    series = [(base - 2 * _1H_NS, 100.0), (base, 120.0)]
    result = _patched_price_stats(series)
    assert result["pct_change_24h"] is None  # series only spans 2h, not 24


def test_price_stats_pct_24h_correct() -> None:
    base = 30 * _24H_NS
    series = [(base - _24H_NS, 80.0), (base, 100.0)]
    result = _patched_price_stats(series)
    # (100-80)/80 * 100 = 25%
    assert result["pct_change_24h"] is not None
    assert abs(result["pct_change_24h"] - 25.0) < 1e-6


def test_price_stats_volatility_is_std_of_returns() -> None:
    # 3 points with returns [0.1, 0.1] → std = 0.0
    base = 30 * _24H_NS
    series = [(base - 2 * _1H_NS, 100.0), (base - _1H_NS, 110.0), (base, 121.0)]
    result = _patched_price_stats(series)
    import numpy as np
    returns = [0.1, 121.0 / 110.0 - 1.0]
    assert result["volatility"] is not None
    assert abs(result["volatility"] - float(np.std(returns))) < 1e-9


# ---- price_series (catalog integration) ----


def _write_snapshot(catalog_path: str, close_price: float | None, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data([
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[close_price - 1] if close_price else [100.0],
            bid_sizes=[1.0],
            ask_prices=[close_price + 1] if close_price else [102.0],
            ask_sizes=[1.0],
            buy_volume=1.0 if close_price else 0.0,
            sell_volume=0.0,
            buy_count=1 if close_price else 0,
            sell_count=0,
            open_price=close_price,
            high_price=close_price,
            low_price=close_price,
            close_price=close_price,
            ts_event=ts,
            ts_init=ts,
        )
    ])


def test_price_series_uses_second_snapshot_close_price() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_snapshot(tmp, close_price=100.0, ts=1_000_000_000)
        _write_snapshot(tmp, close_price=101.0, ts=2_000_000_000)
        series = price_series(ParquetDataCatalog(tmp), _IID)
        assert series == [(1_000_000_000, 100.0), (2_000_000_000, 101.0)]


def test_price_series_skips_seconds_with_no_trade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_snapshot(tmp, close_price=100.0, ts=1_000_000_000)
        _write_snapshot(tmp, close_price=None, ts=2_000_000_000)  # no trade this second
        series = price_series(ParquetDataCatalog(tmp), _IID)
        assert series == [(1_000_000_000, 100.0)]


def test_price_series_falls_back_to_mark_price_when_no_trades_exist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = ParquetDataCatalog(tmp)
        catalog.write_data([
            MarkPriceUpdate(
                instrument_id=InstrumentId.from_str(_IID),
                value=Price(50.0, 1),
                ts_event=1_000_000_000,
                ts_init=1_000_000_000,
            )
        ])
        series = price_series(catalog, _IID)
        assert series == [(1_000_000_000, 50.0)]


if __name__ == "__main__":
    test_finds_a_single_obvious_gap()
    test_no_gaps_in_regularly_spaced_series()
    test_overlapping_intervals_finds_only_shared_outage()
    test_overlapping_intervals_empty_when_no_shared_window()
    test_price_stats_empty_returns_all_none()
    test_price_stats_single_point_returns_price_only()
    test_price_stats_pct_1h_correct()
    test_price_stats_pct_24h_none_when_series_too_short()
    test_price_stats_pct_24h_correct()
    test_price_stats_volatility_is_std_of_returns()
    test_price_series_uses_second_snapshot_close_price()
    test_price_series_skips_seconds_with_no_trade()
    test_price_series_falls_back_to_mark_price_when_no_trades_exist()
    print("ok")
