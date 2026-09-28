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
`research.domain.microstructure` (Story 27.3) against closed-form and synthetic cases: the spec's
I/O matrix rows for autocorrelation, the volatility signature, realised volatility, price impact
and hit rate by bin.
"""

import math

import numpy as np
import pytest
from kernel.clocks import NS_PER_S

from research.domain.microstructure import autocorrelation
from research.domain.microstructure import hit_rate_by_bin
from research.domain.microstructure import price_impact
from research.domain.microstructure import realised_volatility
from research.domain.microstructure import volatility_signature
from research.domain.returns import ReturnSeries


def _series(values: np.ndarray, period_seconds: int = 1, first: int = 0) -> ReturnSeries:
    ts = (first + np.arange(len(values), dtype=np.int64)) * period_seconds * NS_PER_S
    return ReturnSeries(np.asarray(values, dtype=np.float64), ts, period_seconds)


def _ar1(phi: float, n: int, seed: int) -> np.ndarray:
    noise = np.random.default_rng(seed).normal(0.0, 1e-4, n)
    out = np.empty(n)
    out[0] = noise[0]
    for i in range(1, n):
        out[i] = phi * out[i - 1] + noise[i]
    return out


# --- autocorrelation -------------------------------------------------------------------------


def test_the_lag_one_autocorrelation_of_an_ar1_series_is_phi() -> None:
    acf = autocorrelation(_series(_ar1(0.5, 20_000, seed=1)), [1])
    assert acf.rho[0] == pytest.approx(0.5, abs=0.03)


def test_the_lag_two_autocorrelation_of_an_ar1_series_is_phi_squared() -> None:
    acf = autocorrelation(_series(_ar1(0.5, 20_000, seed=2)), [2])
    assert acf.rho[0] == pytest.approx(0.25, abs=0.03)


def test_a_lag_at_or_past_the_length_is_nan_with_no_pairs() -> None:
    acf = autocorrelation(_series(np.array([0.1, -0.2, 0.3])), [3, 7])
    assert np.isnan(acf.rho).all()
    assert acf.pairs.tolist() == [0, 0]


def test_autocorrelation_leaves_out_pairs_with_a_gap() -> None:
    values = np.array([1.0, 2.0, math.nan, 4.0, 5.0, 6.0])
    acf = autocorrelation(_series(values), [1])
    assert acf.pairs.tolist() == [3]  # (1,2), (4,5), (5,6)


def test_an_absent_bucket_does_not_shorten_a_lag() -> None:
    # Stamps 0, 1, 3, 4: the pair at lag 1 must be (0,1) and (3,4), never (1,3).
    series = ReturnSeries(
        np.array([1.0, 2.0, 5.0, 6.0]), np.array([0, 1, 3, 4], dtype=np.int64) * NS_PER_S, 1
    )
    assert autocorrelation(series, [1]).pairs.tolist() == [2]


def test_a_constant_series_has_no_autocorrelation() -> None:
    acf = autocorrelation(_series(np.full(10, 0.01)), [1])
    assert math.isnan(acf.rho[0])


def test_a_lag_below_one_raises() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        autocorrelation(_series(np.zeros(5)), [0])


# --- volatility signature --------------------------------------------------------------------


def test_the_signature_of_iid_returns_is_flat() -> None:
    returns = np.random.default_rng(3).normal(0.0, 1e-4, 300 * 2_000)
    signature = volatility_signature(_series(returns), [1, 5, 30, 60, 300])
    base = signature.variance_per_second[0]
    assert np.all(np.abs(signature.variance_per_second / base - 1.0) < 0.10)


def test_the_signature_counts_complete_returns_per_interval() -> None:
    values = np.full(20, 1e-4)
    values[3] = math.nan
    signature = volatility_signature(_series(values), [1, 10])
    assert signature.n.tolist() == [19, 1]  # the 10 s bucket holding the gap is left out


def test_an_interval_the_window_cannot_fill_is_nan_with_n_zero() -> None:
    signature = volatility_signature(_series(np.full(600, 1e-4)), [3600])
    assert (math.isnan(signature.variance_per_second[0]), int(signature.n[0])) == (True, 0)


def test_an_interval_not_a_multiple_of_the_period_raises() -> None:
    with pytest.raises(ValueError, match="multiple"):
        volatility_signature(_series(np.zeros(20), period_seconds=2), [3])


# --- realised volatility ---------------------------------------------------------------------


def test_realised_volatility_is_the_annualised_root_mean_square() -> None:
    series = _series(np.array([0.01, -0.01, 0.02, -0.02]))
    rv = realised_volatility(series, 2)
    expected = math.sqrt((0.02**2 + 0.02**2) / 2) * series.annualisation_factor
    assert rv[3] == pytest.approx(expected)


def test_realised_volatility_is_nan_until_the_window_fills() -> None:
    rv = realised_volatility(_series(np.full(5, 0.01)), 3)
    assert np.isnan(rv[:2]).all()


def test_realised_volatility_is_nan_over_a_window_holding_a_gap() -> None:
    values = np.array([0.01, 0.01, math.nan, 0.01, 0.01, 0.01])
    rv = realised_volatility(_series(values), 2)
    assert np.isnan(rv[[1, 2, 3]]).tolist() == [False, True, True]


def test_realised_volatility_is_nan_over_a_window_that_is_not_contiguous() -> None:
    series = ReturnSeries(np.full(4, 0.01), np.array([0, 1, 5, 6], dtype=np.int64) * NS_PER_S, 1)
    rv = realised_volatility(series, 2)
    assert np.isnan(rv).tolist() == [True, False, True, False]


def test_realised_volatility_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        realised_volatility(_series(np.zeros(3)), 0)


# --- price impact ----------------------------------------------------------------------------


def test_the_impact_slope_of_a_linear_relation_is_its_slope() -> None:
    rng = np.random.default_rng(4)
    signed_volume = rng.normal(0.0, 5.0, 5_000)
    mid_change = 2.0 * signed_volume + rng.normal(0.0, 0.1, 5_000)
    fit = price_impact(signed_volume, mid_change, [0.0, 2.0, 5.0, 100.0])
    assert fit.slope == pytest.approx([2.0, 2.0, 2.0], abs=0.02)


def test_a_bucket_with_under_three_points_has_no_fit() -> None:
    fit = price_impact([1.0, -1.0, 5.0], [2.0, -2.0, 10.0], [0.0, 2.0, 10.0])
    assert (fit.n.tolist(), np.isnan(fit.slope).tolist()) == ([2, 1], [True, True])


def test_a_bucket_with_zero_volume_variance_has_no_fit() -> None:
    fit = price_impact([3.0, 3.0, 3.0], [1.0, 2.0, 3.0], [0.0, 10.0])
    assert math.isnan(fit.slope[0])


def test_impact_leaves_out_pairs_with_a_gap() -> None:
    fit = price_impact([1.0, 2.0, 3.0, 4.0], [2.0, math.nan, 6.0, 8.0], [0.0, 10.0])
    assert fit.n.tolist() == [3]


def test_impact_edges_must_increase() -> None:
    with pytest.raises(ValueError, match="increasing"):
        price_impact([1.0], [1.0], [0.0, 0.0])


# --- hit rate --------------------------------------------------------------------------------


def test_hit_rate_counts_agreeing_signs_and_flat_pairs_per_bin() -> None:
    predictor = [-2.0, -1.0, -1.0, 1.0, 2.0, 2.0, 0.5]
    outcome = [-1.0, 1.0, 0.0, 1.0, 1.0, -1.0, math.nan]
    rate = hit_rate_by_bin(predictor, outcome, [-3.0, 0.0, 3.0])
    assert rate.n.tolist() == [3, 3]
    assert rate.flat.tolist() == [1, 0]
    assert rate.rate.tolist() == pytest.approx([0.5, 2 / 3])


def test_each_bin_carries_its_mean_predictor_and_outcome() -> None:
    rate = hit_rate_by_bin([1.0, 3.0, -2.0], [2.0, 4.0, 1.0], [-3.0, 0.0, 3.0])
    assert (rate.mean_predictor.tolist(), rate.mean_outcome.tolist()) == ([-2.0, 2.0], [1.0, 3.0])


def test_an_empty_bin_has_no_pairs_and_no_rate() -> None:
    rate = hit_rate_by_bin([1.0, 2.0], [1.0, 1.0], [-5.0, -1.0, 5.0])
    assert (int(rate.n[0]), math.isnan(rate.rate[0])) == (0, True)


def test_the_top_edge_belongs_to_the_last_bin() -> None:
    rate = hit_rate_by_bin([5.0], [1.0], [0.0, 5.0])
    assert rate.n.tolist() == [1]
