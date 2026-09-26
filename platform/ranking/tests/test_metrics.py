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
"""Unit tests for ranking.domain.metrics -- the one pct-change/volatility formula (AD-D10)."""

import numpy as np
import pytest

from ranking.domain.metrics import pct_change_from
from ranking.domain.metrics import price_stats_from_series


# Timestamps: 1 h = 3_600_000_000_000 ns, 24 h = 86_400_000_000_000 ns
_1H_NS = 3_600 * 1_000_000_000
_24H_NS = 86_400 * 1_000_000_000


def test_price_stats_empty_returns_all_none() -> None:
    assert price_stats_from_series([]) == {
        "price": None,
        "pct_change_1h": None,
        "pct_change_24h": None,
        "volatility": None,
    }


def test_price_stats_single_point_returns_price_only() -> None:
    result = price_stats_from_series([(1_000_000_000, 42.0)])
    assert result["price"] == 42.0
    assert result["pct_change_1h"] is None  # only one point, no history span
    assert result["pct_change_24h"] is None
    assert result["volatility"] is None  # need 2+ returns


def test_price_stats_pct_1h_correct() -> None:
    base = 200 * _1H_NS
    series = [(base - 2 * _1H_NS, 100.0), (base - _1H_NS, 110.0), (base, 120.0)]
    result = price_stats_from_series(series)
    # base price at base-1h = 110; (120-110)/110 * 100 ≈ 9.09
    assert result["pct_change_1h"] == pytest.approx((120 - 110) / 110 * 100)


def test_price_stats_pct_24h_none_when_series_too_short() -> None:
    base = 200 * _1H_NS
    series = [(base - 2 * _1H_NS, 100.0), (base, 120.0)]
    assert price_stats_from_series(series)["pct_change_24h"] is None  # spans 2h, not 24


def test_price_stats_pct_24h_correct() -> None:
    base = 30 * _24H_NS
    series = [(base - _24H_NS, 80.0), (base, 100.0)]
    assert price_stats_from_series(series)["pct_change_24h"] == pytest.approx(25.0)


def test_price_stats_volatility_is_std_of_returns() -> None:
    base = 30 * _24H_NS
    series = [(base - 2 * _1H_NS, 100.0), (base - _1H_NS, 110.0), (base, 121.0)]
    returns = [0.1, 121.0 / 110.0 - 1.0]
    assert price_stats_from_series(series)["volatility"] == pytest.approx(float(np.std(returns)))


def test_pct_change_from_is_signed_percent_and_none_without_history() -> None:
    assert pct_change_from(110.0, 100.0) == pytest.approx(10.0)
    assert pct_change_from(90.0, 100.0) == pytest.approx(-10.0)
    assert pct_change_from(110.0, None) is None
    assert pct_change_from(None, 100.0) is None
    assert pct_change_from(110.0, 0.0) is None
