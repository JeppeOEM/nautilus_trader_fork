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
"""`research.domain.equity.EquityCurve`: drawdowns and the `equity_returns` identity (Story 27.1)."""

import math

import numpy as np
import pytest
from kernel.clocks import NS_PER_DAY
from kernel.performance_metrics import equity_returns

from research.domain.equity import DrawdownEpisode
from research.domain.equity import EquityCurve


def _curve(values: list[float], starting_balance: float = 100.0) -> EquityCurve:
    return EquityCurve(
        np.arange(1, len(values) + 1, dtype=np.int64), np.array(values), starting_balance
    )


def test_three_point_drawdown() -> None:
    under, episodes = _curve([100.0, 120.0, 90.0]).drawdowns()
    assert under.tolist() == [0.0, 0.0, -0.25]
    assert episodes == [DrawdownEpisode(peak_ts=2, trough_ts=3, recovery_ts=None, depth=0.25)]


def test_a_recovered_episode_and_a_second_one() -> None:
    _, episodes = _curve([100.0, 80.0, 90.0, 100.0, 110.0, 99.0, 121.0]).drawdowns()
    assert [(e.peak_ts, e.trough_ts, e.recovery_ts) for e in episodes] == [(1, 2, 4), (5, 6, 7)]
    assert [e.depth for e in episodes] == pytest.approx([0.2, 0.1])


def test_a_monotone_curve_has_no_episode() -> None:
    assert _curve([1.0, 2.0, 3.0], starting_balance=1.0).drawdowns()[1] == []


def test_a_loss_from_the_starting_balance_is_under_water_from_the_first_point() -> None:
    """The peak before the first point is `starting_balance`: a day-one loss is a drawdown."""
    under, episodes = _curve([95.0, 90.0, 96.0], starting_balance=100.0).drawdowns()
    assert under.tolist() == pytest.approx([-0.05, -0.10, -0.04])
    assert [(e.peak_ts, e.trough_ts, e.recovery_ts) for e in episodes] == [(None, 2, None)]
    assert episodes[0].depth == pytest.approx(0.10)


def test_non_increasing_stamps_raise() -> None:
    with pytest.raises(ValueError, match="increasing"):
        EquityCurve(np.array([1, 1]), np.array([1.0, 2.0]), 1.0)


def test_a_nan_value_raises() -> None:
    with pytest.raises(ValueError, match="finite"):
        EquityCurve(np.array([1, 2]), np.array([1.0, math.nan]), 1.0)


def test_a_non_positive_balance_or_empty_curve_raises() -> None:
    with pytest.raises(ValueError, match="starting_balance"):
        _curve([1.0], starting_balance=0.0)
    with pytest.raises(ValueError, match="non-empty"):
        _curve([])


def test_from_pnl_by_day_returns_equal_equity_returns_exactly() -> None:
    pnl_by_day = [
        {"period_start": 20_000 * NS_PER_DAY, "pnl": 0.1},
        {"period_start": 20_001 * NS_PER_DAY, "pnl": -0.3},
        {"period_start": 20_003 * NS_PER_DAY, "pnl": 12.7},
    ]
    curve = EquityCurve.from_pnl_by_day(pnl_by_day, 1000.0)
    assert curve.returns_by_ts() == equity_returns(pnl_by_day, 1000.0)
    assert curve.values.tolist() == [1000.1, 1000.1 - 0.3, 1000.1 - 0.3 + 12.7]


def test_returns_skip_a_non_positive_previous_equity_like_equity_returns() -> None:
    pnl_by_day = [{"period_start": 1, "pnl": -150.0}, {"period_start": 2, "pnl": 10.0}]
    curve = EquityCurve.from_pnl_by_day(pnl_by_day, 100.0)
    assert curve.returns_by_ts() == equity_returns(pnl_by_day, 100.0) == {1: -1.5}
