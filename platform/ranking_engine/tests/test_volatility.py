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
"""Unit tests for ranking_engine.volatility -- the cross-sectional volatility tracker."""

import pytest

from ranking_engine.volatility import VolatilityTracker


_SEC = 1_000_000_000  # 1 second in ns


def test_score_known_price_series_matches_hand_computed_stdev() -> None:
    """Expected value is a literal computed independently of VolatilityTracker.score's
    own statistics.stdev() call -- the old version of this test re-derived "expected"
    via the exact same formula/stdev call as the implementation, so it could not have
    caught a formula regression (e.g. population vs. sample stdev, or a sign error).

    prices = [100.0, 110.0, 99.0]
    returns = [(110-100)/100, (99-110)/110] = [0.10, -0.10]
    mean(returns) = 0.0
    sample variance = ((0.10-0.0)**2 + (-0.10-0.0)**2) / (2-1) = (0.01+0.01)/1 = 0.02
    sample stdev = sqrt(0.02) ~= 0.14142135623730951
    """
    tracker = VolatilityTracker(lookback_seconds=3600)
    prices = [100.0, 110.0, 99.0]
    for i, price in enumerate(prices):
        tracker.update("BTC-USD-PERP.DYDX", i * _SEC, price)

    assert tracker.score("BTC-USD-PERP.DYDX") == pytest.approx(0.14142135623730951)


def test_score_none_with_fewer_than_two_returns() -> None:
    tracker = VolatilityTracker(lookback_seconds=3600)
    tracker.update("BTC-USD-PERP.DYDX", 0, 100.0)

    assert tracker.score("BTC-USD-PERP.DYDX") is None


def test_score_none_for_unknown_instrument() -> None:
    tracker = VolatilityTracker(lookback_seconds=3600)
    assert tracker.score("NOPE-USD-PERP.DYDX") is None


def test_update_evicts_entries_older_than_lookback() -> None:
    """A point older than lookback_seconds is pruned and no longer contributes to score()."""
    tracker = VolatilityTracker(lookback_seconds=10)
    tracker.update("BTC-USD-PERP.DYDX", 0, 100.0)
    tracker.update("BTC-USD-PERP.DYDX", 3 * _SEC, 101.0)
    tracker.update("BTC-USD-PERP.DYDX", 6 * _SEC, 99.0)
    assert tracker.score("BTC-USD-PERP.DYDX") is not None  # 3 points within the 10s window

    # ts=15s evicts anything older than 15-10=5s -- both ts=0 (age 15) and ts=3 (age
    # 12) fall out, leaving only ts=6 and the new ts=15: 2 points, 1 return, None.
    tracker.update("BTC-USD-PERP.DYDX", 15 * _SEC, 103.0)
    assert tracker.score("BTC-USD-PERP.DYDX") is None


def test_lookback_seconds_changes_computed_score_for_same_input() -> None:
    """AC2: reconfiguring lookback_seconds must change the score, with no code change.

    short's 12s window evicts ts=0 by the time ts=15s arrives (age 15 > 12), leaving 3
    points/2 returns; long's 3600s window keeps all 4 points/3 returns -- different
    surviving return sets, so both scores are non-None and numerically distinct.
    """
    prices = [(0, 100.0), (5 * _SEC, 101.0), (10 * _SEC, 99.0), (15 * _SEC, 105.0)]

    short = VolatilityTracker(lookback_seconds=12)
    for ts, price in prices:
        short.update("BTC-USD-PERP.DYDX", ts, price)

    long = VolatilityTracker(lookback_seconds=3600)
    for ts, price in prices:
        long.update("BTC-USD-PERP.DYDX", ts, price)

    short_score = short.score("BTC-USD-PERP.DYDX")
    long_score = long.score("BTC-USD-PERP.DYDX")
    assert short_score is not None
    assert long_score is not None
    assert short_score != long_score
