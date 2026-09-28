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
`research.domain.correlation`: closed-form correlation, lead-lag and clustering (Story 27.1), and the
rolling correlation, merge order, lead-lag peak and basis (Story 27.4).
"""

import math

import numpy as np
import pytest
from kernel.clocks import NS_PER_S

from research.domain.correlation import AlignedReturns
from research.domain.correlation import CorrelationMatrix
from research.domain.correlation import MergeStep
from research.domain.correlation import align
from research.domain.correlation import basis_bps
from research.domain.correlation import cluster
from research.domain.correlation import correlation_matrix
from research.domain.correlation import correlation_of
from research.domain.correlation import lagged_pairs
from research.domain.correlation import lead_lag
from research.domain.correlation import merge_order
from research.domain.correlation import peak_lag
from research.domain.correlation import rolling_correlation
from research.domain.returns import ReturnSeries


_A = np.array([0.01, -0.02, 0.015, 0.03, -0.01, 0.005, -0.025, 0.02, 0.0, 0.01])


def _series(values: np.ndarray, start: int = 0, period: int = 1) -> ReturnSeries:
    ts = np.arange(start, start + len(values), dtype=np.int64) * period * NS_PER_S
    return ReturnSeries(np.asarray(values, dtype=np.float64), ts, period)


def test_perfect_negative_and_positive_correlation() -> None:
    aligned = align({"a": _series(_A), "neg": _series(-_A), "lin": _series(2 * _A + 1)})
    matrix = correlation_matrix(aligned)
    assert matrix.rho("a", "neg") == pytest.approx(-1.0)
    assert matrix.rho("a", "lin") == pytest.approx(1.0)
    assert matrix.rho("a", "a") == 1.0


def test_align_outer_joins_with_nan_and_correlation_is_pairwise_complete() -> None:
    b = _A.copy()
    b[3] = math.nan
    aligned = align({"a": _series(_A), "b": _series(b, start=2)})
    assert len(aligned.ts_ns) == len(_A) + 2
    assert np.isnan(aligned.column("b")[:2]).all()
    assert np.isnan(aligned.column("a")[-2:]).all()
    # b[t] = a[t - 2] on the shared rows, so they are not +1 correlated at lag 0...
    both = np.isfinite(aligned.column("a")) & np.isfinite(aligned.column("b"))
    assert both.sum() == len(_A) - 3
    assert correlation_matrix(aligned).rho("a", "b") < 1.0


def test_aligned_returns_from_plain_lists_are_coerced_or_raise_value_error() -> None:
    aligned = AlignedReturns(("a", "b"), [0, 1], [[0.1, 0.2], [0.3, math.nan]], 1)
    assert aligned.matrix.dtype == np.float64
    assert aligned.column("b")[0] == 0.2
    with pytest.raises(ValueError, match="matrix must be"):
        AlignedReturns(("a", "b"), [0, 1], [[0.1], [0.3]], 1)


def test_mixed_periods_raise() -> None:
    with pytest.raises(ValueError, match="periods"):
        align({"a": _series(_A, period=1), "b": _series(_A, period=2)})


def test_too_few_pairs_or_zero_variance_is_nan() -> None:
    matrix = correlation_matrix(align({"a": _series(_A), "flat": _series(np.ones(10))}))
    assert math.isnan(matrix.rho("a", "flat"))
    assert math.isnan(matrix.rho("flat", "flat"))
    disjoint = correlation_matrix(align({"a": _series(_A[:2]), "b": _series(_A[:2], start=5)}))
    assert math.isnan(disjoint.rho("a", "b"))


def test_lead_lag_finds_the_shift() -> None:
    rng = np.random.default_rng(3)
    a = rng.normal(size=200)
    b = np.full(200, math.nan)
    b[3:] = a[:-3]  # b[t] = a[t - 3]: a leads by 3
    lags = lead_lag(a, b, 5)
    best = max(lags, key=lambda pair: pair[1])
    assert best[0] == 3
    assert best[1] == pytest.approx(1.0)
    assert [lag for lag, _ in lags] == list(range(-5, 6))


def test_lead_lag_rejects_unaligned_or_oversized_input() -> None:
    with pytest.raises(ValueError, match="aligned"):
        lead_lag(_A, _A[:5], 1)
    with pytest.raises(ValueError, match="max_lag"):
        lead_lag(_A, _A, len(_A))
    with pytest.raises(ValueError, match="min_pairs"):
        lead_lag(_A, _A, 1, min_pairs=1)


def test_lead_lag_blanks_a_lag_with_too_few_pairs() -> None:
    rng = np.random.default_rng(7)
    a = rng.normal(size=40)
    b = rng.normal(size=40)
    lags = dict(lead_lag(a, b, 38, min_pairs=30))
    assert lagged_pairs(a, b, 10) == 30
    assert math.isfinite(lags[10])
    assert math.isnan(lags[11])  # 29 pairs: under the floor
    assert math.isnan(lags[-38])  # 2 pairs would be ±1 by construction


def test_cluster_single_linkage() -> None:
    rho = np.array(
        [
            [1.0, 0.9, 0.2, 0.1],
            [0.9, 1.0, 0.85, 0.0],
            [0.2, 0.85, 1.0, math.nan],
            [0.1, 0.0, math.nan, 1.0],
        ]
    )
    matrix = CorrelationMatrix(("a", "b", "c", "d"), rho)
    # a-b (0.1) and b-c (0.15) chain c in through b; d is never within 0.2 of anyone.
    assert cluster(matrix, 0.2) == [["a", "b", "c"], ["d"]]
    assert cluster(matrix, 0.05) == [["a"], ["b"], ["c"], ["d"]]
    assert cluster(matrix, 0.85) == [["a", "b", "c"], ["d"]]  # d's nearest link is 0.9
    assert cluster(matrix, 0.9) == [["a", "b", "c", "d"]]
    assert cluster(matrix, 2.0) == [["a", "b", "c", "d"]]


@pytest.mark.parametrize("threshold", [math.inf, math.nan, -0.1, 2.1])
def test_cluster_rejects_a_threshold_outside_the_distance_range(threshold: float) -> None:
    matrix = CorrelationMatrix(("a",), np.array([[1.0]]))
    with pytest.raises(ValueError, match="threshold"):
        cluster(matrix, threshold)


def test_a_float_rounded_constant_series_has_no_correlation() -> None:
    constant = np.array([0.1, 0.1, 0.1])
    matrix = correlation_matrix(align({"a": _series(_A[:3]), "c": _series(constant)}))
    assert math.isnan(matrix.rho("a", "c"))
    assert math.isnan(matrix.rho("c", "c"))


def test_an_asymmetric_matrix_raises() -> None:
    with pytest.raises(ValueError, match="symmetric"):
        CorrelationMatrix(("a", "b"), np.array([[1.0, 0.5], [0.4, 1.0]]))


# --- Story 27.4 ------------------------------------------------------------------------------


def test_rolling_correlation_is_nan_until_the_window_fills() -> None:
    rho = rolling_correlation(_A, 2 * _A + 1, 4)
    assert np.isnan(rho[:3]).all()
    assert rho[3:] == pytest.approx([1.0] * (len(_A) - 3))


def test_rolling_correlation_is_pairwise_complete_inside_the_window() -> None:
    b = -_A.copy()
    b[5] = math.nan
    rho = rolling_correlation(_A, b, 4)
    # Windows ending at 5..8 hold the NaN: three pairs left (>= 4 // 2), still exactly -1.
    assert rho[3:] == pytest.approx([-1.0] * (len(_A) - 3))


def test_rolling_correlation_needs_min_pairs() -> None:
    b = _A.copy()
    b[2:6] = math.nan
    rho = rolling_correlation(_A, b, 4, min_pairs=3)
    # Window [k-3, k] finite pairs: k=3 -> 2, k=4..6 -> <2, k=7 -> 2, k=8 -> 3, k=9 -> 4.
    assert np.isnan(rho[:8]).all()
    assert rho[8:] == pytest.approx([1.0, 1.0])


def test_rolling_correlation_shorter_than_the_window_is_all_nan() -> None:
    assert np.isnan(rolling_correlation(_A[:3], _A[:3], 4)).all()


@pytest.mark.parametrize(("window", "min_pairs"), [(1, None), (4, 1), (4, 5)])
def test_rolling_correlation_rejects_a_bad_window(window: int, min_pairs: int | None) -> None:
    with pytest.raises(ValueError):
        rolling_correlation(_A, _A, window, min_pairs)


def test_rolling_correlation_rejects_unaligned_series() -> None:
    with pytest.raises(ValueError, match="aligned"):
        rolling_correlation(_A, _A[:5], 2)


def test_correlation_of_any_columns_is_pairwise_complete() -> None:
    levels = np.column_stack([_A, 3 * _A - 2, np.where(np.arange(10) < 5, _A, math.nan)])
    matrix = correlation_of(("x", "y", "z"), levels)
    assert matrix.rho("x", "y") == pytest.approx(1.0)
    assert matrix.rho("x", "z") == pytest.approx(1.0)  # over the first five rows only
    with pytest.raises(ValueError, match="columns"):
        correlation_of(("x",), levels)


def _three() -> CorrelationMatrix:
    rho = np.array([[1.0, 0.9, 0.3], [0.9, 1.0, 0.5], [0.3, 0.5, 1.0]])
    return CorrelationMatrix(("a", "b", "c"), rho)


def test_merge_order_lists_every_single_linkage_merge_closest_first() -> None:
    steps = merge_order(_three())
    assert [(s.left, s.right) for s in steps] == [(("a",), ("b",)), (("a", "b"), ("c",))]
    assert [s.distance for s in steps] == pytest.approx([0.1, 0.5])
    assert isinstance(steps[0], MergeStep)


def test_merge_order_and_cluster_are_the_same_linkage() -> None:
    matrix = _three()
    assert cluster(matrix, 0.1 + 1e-12) == [["a", "b"], ["c"]]
    assert cluster(matrix, 0.5) == [["a", "b", "c"]]


def test_merge_order_stops_where_only_nan_pairs_remain() -> None:
    rho = np.array([[1.0, 0.8, math.nan], [0.8, 1.0, math.nan], [math.nan, math.nan, 1.0]])
    steps = merge_order(CorrelationMatrix(("a", "b", "c"), rho))
    assert [(s.left, s.right) for s in steps] == [(("a",), ("b",))]


def test_peak_lag_is_the_highest_finite_correlation() -> None:
    assert peak_lag([(-1, 0.2), (0, math.nan), (1, 0.7), (2, 0.5)]) == (1, 0.7)


def test_peak_lag_ties_go_to_the_smallest_absolute_lag() -> None:
    assert peak_lag([(-3, 0.6), (2, 0.6), (-2, 0.6), (5, 0.6)]) == (-2, 0.6)


def test_peak_lag_of_no_overlap_is_none() -> None:
    assert peak_lag([(-1, math.nan), (0, math.nan)]) is None
    assert peak_lag([]) is None


def test_lead_lag_peak_on_a_shift() -> None:
    rng = np.random.default_rng(5)
    a = rng.normal(size=300)
    b = np.full(300, math.nan)
    b[2:] = a[:-2]  # a leads b by 2
    peak = peak_lag(lead_lag(a, b, 30))
    assert peak is not None
    assert peak[0] == 2


def test_basis_in_bps() -> None:
    basis = basis_bps(np.array([100.01, math.nan, 99.0]), np.array([100.0, 100.0, 100.0]))
    assert basis[0] == pytest.approx(1.0)
    assert math.isnan(basis[1])
    assert basis[2] == pytest.approx(-100.0)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf])
def test_basis_refuses_a_non_positive_or_infinite_price(bad: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        basis_bps(np.array([bad]), np.array([1.0]))
    with pytest.raises(ValueError, match="positive"):
        basis_bps(np.array([1.0]), np.array([bad]))


def test_basis_rejects_unaligned_prices() -> None:
    with pytest.raises(ValueError, match="aligned"):
        basis_bps(np.array([1.0, 2.0]), np.array([1.0]))
