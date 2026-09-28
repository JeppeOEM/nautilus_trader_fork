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
"""`research.domain.returns.ReturnSeries`: closed-form cases (Story 27.1)."""

import math

import numpy as np
import pytest
from kernel.clocks import NS_PER_S
from kernel.performance_metrics import return_stats

from research.domain.equity import EquityCurve
from research.domain.returns import ReturnSeries


_T0 = 20_000 * 86_400 * NS_PER_S


def _ts(*seconds: int) -> list[int]:
    return [_T0 + s * NS_PER_S for s in seconds]


def _series(values: list[float], seconds: list[int], period: int = 1) -> ReturnSeries:
    ts = np.array(_ts(*seconds), dtype=np.int64)
    return ReturnSeries(np.array(values, dtype=np.float64), ts, period)


def test_a_gap_is_never_bridged() -> None:
    series = ReturnSeries.from_prices([1.0, None, 2.0, 4.0], _ts(0, 1, 2, 3), 1)
    assert series.ts_ns.tolist() == _ts(1, 2, 3)
    assert np.array_equal(series.values, [math.nan, math.nan, 1.0], equal_nan=True)


def test_non_adjacent_buckets_yield_nan() -> None:
    series = ReturnSeries.from_prices([1.0, 2.0, 3.0], _ts(0, 1, 5), 1)
    assert np.array_equal(series.values, [1.0, math.nan], equal_nan=True)


def test_stamps_floor_to_their_bucket() -> None:
    series = ReturnSeries.from_prices([10.0, 11.0], [_T0 + 100, _T0 + NS_PER_S + 999], 1)
    assert series.ts_ns.tolist() == _ts(1)
    assert series.values.tolist() == pytest.approx([0.1])


def test_two_prices_in_one_bucket_raise() -> None:
    with pytest.raises(ValueError, match="bucket"):
        ReturnSeries.from_prices([1.0, 2.0], [_T0, _T0 + 1], 1)


def test_a_non_positive_price_raises() -> None:
    with pytest.raises(ValueError, match="positive"):
        ReturnSeries.from_prices([1.0, 0.0], _ts(0, 1), 1)


def test_an_infinite_price_raises_rather_than_reading_as_a_gap() -> None:
    with pytest.raises(ValueError, match="finite"):
        ReturnSeries.from_prices([1.0, math.inf], _ts(0, 1), 1)


def test_resample_compounds() -> None:
    series = _series([0.1, 0.1, -0.5, 0.2], [0, 1, 2, 3], 1).resample(2)
    assert series.ts_ns.tolist() == _ts(0, 2)
    assert series.values.tolist() == pytest.approx([1.1 * 1.1 - 1, 0.5 * 1.2 - 1])
    assert series.period_seconds == 2


def test_resample_nan_poisons_its_bucket_only() -> None:
    series = _series([0.1, math.nan, 0.2, 0.3], [0, 1, 2, 3], 1).resample(2)
    assert math.isnan(series.values[0])
    assert series.values[1] == pytest.approx(1.2 * 1.3 - 1)


def test_resample_leaves_an_incomplete_bucket_nan() -> None:
    series = _series([0.1, 0.2, 0.3], [0, 1, 3], 1).resample(2)  # second 2 is missing
    assert series.ts_ns.tolist() == _ts(0, 2)
    assert series.values[0] == pytest.approx(1.1 * 1.2 - 1)
    assert math.isnan(series.values[1])


def test_resample_to_a_non_multiple_raises() -> None:
    with pytest.raises(ValueError, match="multiple"):
        _series([0.1], [0], 2).resample(3)


def test_annualisation_factor_is_from_the_stored_period() -> None:
    assert _series([], [], 86_400).annualisation_factor == pytest.approx(math.sqrt(365))
    assert _series([], [], 60).annualisation_factor == pytest.approx(math.sqrt(365 * 1440))


def test_off_grid_or_unsorted_stamps_raise() -> None:
    with pytest.raises(ValueError, match="multiple"):
        ReturnSeries(np.array([0.1]), np.array([_T0 + 1]), 1)
    with pytest.raises(ValueError, match="increasing"):
        _series([0.1, 0.2], [1, 0], 1)
    with pytest.raises(ValueError, match="float64"):
        ReturnSeries(np.array([1]), np.array(_ts(0)), 1)


def test_from_equity_holds_account_state_between_points() -> None:
    curve = EquityCurve(np.array(_ts(0, 2)), np.array([100.0, 110.0]), 100.0)
    series = ReturnSeries.from_equity(curve, 1)
    assert series.ts_ns.tolist() == _ts(0, 1, 2)
    assert series.values.tolist() == pytest.approx([0.0, 0.0, 0.1])


def test_from_equity_takes_each_buckets_last_value() -> None:
    curve = EquityCurve(
        np.array([_T0, _T0 + 1, _T0 + NS_PER_S]), np.array([100.0, 90.0, 99.0]), 100.0
    )
    series = ReturnSeries.from_equity(curve, 1)
    # The first bucket's last value (90) is measured against the starting balance, never dropped.
    assert series.values.tolist() == pytest.approx([90.0 / 100.0 - 1, 99.0 / 90.0 - 1])


def test_rolling_sharpe_is_return_stats_per_window() -> None:
    day = 86_400
    values = [0.01, -0.02, 0.03, 0.01, math.nan, 0.02]
    series = _series(values, [k * day for k in range(6)], day)
    got = series.rolling_sharpe(3)
    ts = series.ts_ns.tolist()
    assert np.isnan(got[:2]).all()
    for last in (2, 3):
        window = dict(zip(ts[last - 2 : last + 1], values[last - 2 : last + 1], strict=True))
        assert got[last] == return_stats(window)["sharpe_ratio"]
    assert np.isnan(got[4:]).all()  # every window holding the NaN


def test_rolling_sharpe_refuses_a_window_under_two_days() -> None:
    series = _series([0.01] * 3, [0, 60, 120], 60)
    with pytest.raises(ValueError, match="two days"):
        series.rolling_sharpe(2 * 1440 - 1)
    assert len(series.rolling_sharpe(2 * 1440)) == 3


def test_as_dict_drops_gaps() -> None:
    assert _series([0.5, math.nan], [0, 1], 1).as_dict() == {_T0: 0.5}


def test_arrays_are_read_only() -> None:
    series = _series([0.1], [0], 1)
    with pytest.raises(ValueError):
        series.values[0] = 1.0
