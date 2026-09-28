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
`RollingZScore` against closed forms, `MultiLevelOFI`'s z-score delegating to it unchanged, and the
snapshot depth functions (`snapshot_depth`, `cumulative_depth`, `depth_within_bps`), Story 27.3.
"""

import math
import statistics

import numpy as np
import pytest

from kernel.indicators import DepthProfile
from kernel.indicators import MultiLevelOFI
from kernel.indicators import RollingZScore
from kernel.indicators import cumulative_depth
from kernel.indicators import depth_within_bps
from kernel.indicators import snapshot_depth


# --- RollingZScore ---------------------------------------------------------------------------


def test_zscore_is_the_reading_against_the_population_std_of_its_window() -> None:
    z = RollingZScore(window=3)
    for value in (1.0, 2.0, 3.0, 10.0):
        z.update_raw(value)
    window = [2.0, 3.0, 10.0]
    expected = (10.0 - statistics.fmean(window)) / statistics.pstdev(window)
    assert z.value == pytest.approx(expected, rel=1e-12)


def test_zscore_is_zero_before_two_readings() -> None:
    z = RollingZScore(window=5)
    z.update_raw(42.0)
    assert z.value == 0.0


def test_zscore_is_zero_when_the_window_is_constant() -> None:
    z = RollingZScore(window=4)
    for _ in range(6):
        z.update_raw(7.0)
    assert z.value == 0.0


def test_zscore_of_two_readings_is_plus_one() -> None:
    z = RollingZScore(window=10)
    z.update_raw(0.0)
    z.update_raw(4.0)
    assert z.value == 1.0  # mean 2, population std 2


def test_zscore_initializes_when_the_window_is_full() -> None:
    z = RollingZScore(window=3)
    z.update_raw(1.0)
    z.update_raw(2.0)
    assert not z.initialized
    z.update_raw(3.0)
    assert z.initialized


def test_zscore_reset_forgets_the_window() -> None:
    z = RollingZScore(window=3)
    z.update_raw(1.0)
    z.update_raw(5.0)
    z.reset()
    z.update_raw(9.0)
    assert z.value == 0.0


def test_zscore_window_below_two_raises() -> None:
    with pytest.raises(ValueError, match=">= 2"):
        RollingZScore(window=1)


def _random_book(rng: np.random.Generator, levels: int) -> tuple[list[float], ...]:
    mid = 100.0 + float(rng.integers(-3, 4))
    return (
        [mid - 1 - i for i in range(levels)],
        rng.integers(1, 10, levels).astype(float).tolist(),
        [mid + 1 + i for i in range(levels)],
        rng.integers(1, 10, levels).astype(float).tolist(),
    )


def test_multilevel_ofi_zscore_equals_raw_ofi_through_rolling_zscore() -> None:
    """Parity: the delegated z-score is bit-identical to `RollingZScore` over the raw OFI."""
    rng = np.random.default_rng(273)
    with_z = MultiLevelOFI(levels=3, window=4, usd_notional=True, zscore_window=7)
    raw = MultiLevelOFI(levels=3, window=4, usd_notional=True)
    z = RollingZScore(7)
    seen, expected = [], []
    for step in range(200):
        book = _random_book(rng, 3)
        with_z.update_raw(*book)
        raw.update_raw(*book)
        if step:  # the first update only sets the baseline: no reading
            z.update_raw(raw.value)
            seen.append(with_z.value)
            expected.append(z.value)
    assert seen == expected


def test_multilevel_ofi_reset_clears_its_zscore_window() -> None:
    ofi = MultiLevelOFI(levels=1, window=1, zscore_window=5)
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])
    ofi.update_raw([101.0], [5.0], [101.0], [5.0])  # +5
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])  # -5
    ofi.reset()
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])
    ofi.update_raw([101.0], [5.0], [101.0], [5.0])  # one reading only after the reset
    assert ofi.value == 0.0


# --- depth -----------------------------------------------------------------------------------


def _snapshot(bids: list[float], asks: list[float]) -> dict:
    return {
        "bid_prices": bids,
        "bid_sizes": [1.0 + i for i in range(len(bids))],
        "ask_prices": asks,
        "ask_sizes": [2.0 + i for i in range(len(asks))],
    }


def test_snapshot_depth_takes_the_top_levels_of_each_side() -> None:
    profile = snapshot_depth(_snapshot([99.0, 98.0, 97.0], [101.0, 102.0, 103.0]), levels=2)
    assert profile == DepthProfile([99.0, 98.0], [1.0, 2.0], [101.0, 102.0], [2.0, 3.0])


def test_snapshot_depth_keeps_a_thin_side_unpadded() -> None:
    profile = snapshot_depth(_snapshot([99.0], [101.0, 102.0]), levels=20)
    assert profile is not None
    assert (len(profile.bid_prices), len(profile.ask_prices)) == (1, 2)


def test_snapshot_depth_of_an_empty_side_is_none() -> None:
    assert snapshot_depth(_snapshot([], [101.0]), levels=5) is None


def test_cumulative_depth_sums_from_the_touch_outward() -> None:
    profile = DepthProfile([99.0, 98.0, 97.0], [1.0, 2.0, 3.0], [101.0, 102.0], [4.0, 5.0])
    assert cumulative_depth(profile) == ([1.0, 3.0, 6.0], [4.0, 9.0])


def test_depth_within_bps_counts_levels_inside_each_distance() -> None:
    # mid 100: bids and asks at 1, 2 and 4 bps from it.
    profile = DepthProfile(
        [99.99, 99.98, 99.96], [1.0, 2.0, 3.0], [100.01, 100.02, 100.04], [4.0, 5.0, 6.0]
    )
    bids, asks = depth_within_bps(profile, [1.5, 2.5, 3.5])
    assert bids == [1.0, 3.0, 3.0]
    assert asks == [4.0, 9.0, 9.0]


def test_depth_within_bps_past_the_deepest_stored_level_is_nan() -> None:
    profile = DepthProfile([99.99], [1.0], [100.01], [4.0])
    bids, asks = depth_within_bps(profile, [0.5, 50.0])
    assert bids[0] == 0.0
    assert math.isnan(bids[1])
    assert math.isnan(asks[1])


def test_zscore_is_zero_when_equal_readings_average_inexactly() -> None:
    z = RollingZScore(window=300)
    for _ in range(300):
        z.update_raw(0.1)  # 300 x 0.1 has a mean one ulp off 0.1 and a ~1e-17 std
    assert z.value == 0.0


def test_zscore_refuses_a_non_finite_reading() -> None:
    z = RollingZScore(window=3)
    with pytest.raises(ValueError, match="finite"):
        z.update_raw(math.nan)


def test_a_level_exactly_on_a_bps_edge_is_within_it() -> None:
    # mid 0.47: the best ask is exactly 0.5 bp away, but the float distance is 0.5000000000002
    profile = DepthProfile([0.4699765], [1.0], [0.4700235], [2.0])
    assert depth_within_bps(profile, [0.5]) == ([1.0], [2.0])


def test_zscore_is_zero_when_unequal_readings_have_an_underflowing_std() -> None:
    z = RollingZScore(window=2)
    z.update_raw(0.0)
    z.update_raw(1e-170)  # squared deviations underflow to 0.0: flat, never a division by zero
    assert z.value == 0.0


def test_snapshot_depth_below_one_level_raises() -> None:
    with pytest.raises(ValueError, match="levels must be >= 1"):
        snapshot_depth(_snapshot([99.0], [101.0]), levels=0)


def test_depth_profile_is_immutable() -> None:
    profile = DepthProfile([99.0], [1.0], [101.0], [2.0])
    with pytest.raises(AttributeError):
        profile.bid_prices = [98.0]  # type: ignore[misc]
