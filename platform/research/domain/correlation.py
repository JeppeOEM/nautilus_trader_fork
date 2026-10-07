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
Correlation of return series: alignment, pairwise-complete Pearson, lead-lag and clustering
(Story 27.1), plus the rolling correlation, the single-linkage merge order, the lead-lag peak and
the basis in basis points (Story 27.4); numpy only.

Gaps stay gaps: a timestamp one series lacks is NaN in the aligned matrix, and every statistic here
uses only the rows where both of its inputs are finite (pairwise-complete), never a filled value.
"""

import math
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from research.domain.returns import ReturnSeries


def _frozen(array: np.ndarray) -> np.ndarray:
    copy = np.array(array, copy=True)
    copy.flags.writeable = False
    return copy


@dataclass(frozen=True, eq=False)
class AlignedReturns:
    """
    Several return series on one shared timestamp axis.

    Invariant: `ids` are unique; `ts_ns` (int64) is strictly increasing; `matrix` has shape
    `(len(ts_ns), len(ids))`, column j being `ids[j]`'s return at each stamp or NaN where that series
    has no point; every series shares `period_seconds`. Violated by mixing periods or passing a
    mis-shaped matrix -- `align` and `__post_init__` raise `ValueError`.
    """

    ids: tuple[str, ...]
    ts_ns: np.ndarray
    matrix: np.ndarray
    period_seconds: int

    def __post_init__(self) -> None:
        ts = np.asarray(self.ts_ns, dtype=np.int64)
        matrix = np.asarray(self.matrix, dtype=np.float64)
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids must be unique")
        if ts.ndim != 1 or matrix.shape != (len(ts), len(self.ids)):
            raise ValueError("matrix must be (len(ts_ns), len(ids))")
        if len(ts) > 1 and not (np.diff(ts) > 0).all():
            raise ValueError("ts_ns must be strictly increasing")
        if self.period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        object.__setattr__(self, "ids", tuple(self.ids))
        object.__setattr__(self, "ts_ns", _frozen(ts))
        object.__setattr__(self, "matrix", _frozen(matrix))

    def column(self, instrument_id: str) -> np.ndarray:
        return self.matrix[:, self.ids.index(instrument_id)]


def align(series_by_id: Mapping[str, ReturnSeries]) -> AlignedReturns:
    """
    Outer-join the series on their timestamps (NaN where a series has no point), columns in the
    mapping's order. Series on different periods raise `ValueError`: resample them to one period
    first (`ReturnSeries.resample`), never compare mixed periods.
    """
    if not series_by_id:
        raise ValueError("align needs at least one series")
    periods = {s.period_seconds for s in series_by_id.values()}
    if len(periods) != 1:
        raise ValueError(f"series have different periods {sorted(periods)}: resample first")
    ts = np.unique(np.concatenate([s.ts_ns for s in series_by_id.values()]))
    matrix = np.full((len(ts), len(series_by_id)), np.nan)
    for j, series in enumerate(series_by_id.values()):
        matrix[np.searchsorted(ts, series.ts_ns), j] = series.values
    return AlignedReturns(tuple(series_by_id), ts, matrix, periods.pop())


# A side whose standard deviation is at most this fraction of max(1, |mean|) is constant: float
# rounding of a constant series (`[0.1, 0.1, 0.1]`) must not produce a spurious correlation.
# Known limit (DATA_DICTIONARY §2.12, audit D-89): a near-constant but unequal series (std under this
# bound) is undefined here although its Pearson is defined; the reference treats only an exactly
# constant side as undefined. Upgrade path: test constancy exactly (all values equal) and compute
# the correlation in a compensated sum so rounding cannot fake a spread.
_ZERO_VARIANCE_RTOL = 1e-12


def _is_constant(x: np.ndarray) -> bool:
    return float(x.std()) <= _ZERO_VARIANCE_RTOL * max(1.0, abs(float(x.mean())))


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    """
    Pearson correlation over the rows where both are finite; NaN below two rows or when either
    side is (numerically) constant. Clipped into [-1, 1] only to absorb float rounding of a perfect
    correlation.
    """
    both = np.isfinite(x) & np.isfinite(y)
    if both.sum() < 2:
        return math.nan
    xs, ys = x[both], y[both]
    if _is_constant(xs) or _is_constant(ys):
        return math.nan
    dx = xs - xs.mean()
    dy = ys - ys.mean()
    denominator = math.sqrt(float((dx * dx).sum()) * float((dy * dy).sum()))
    return float(np.clip(float((dx * dy).sum()) / denominator, -1.0, 1.0))


@dataclass(frozen=True, eq=False)
class CorrelationMatrix:
    """
    Pairwise-complete Pearson correlations between return series.

    Invariant: `values` is square over `ids` and symmetric (NaN mirrored); the diagonal is 1, or
    NaN for a series with fewer than two finite points or zero variance; off-diagonal entries are
    in [-1, 1] or NaN (fewer than two overlapping finite pairs, or zero variance). Violated by a
    hand-built asymmetric or out-of-range matrix -- `__post_init__` raises `ValueError`.
    """

    ids: tuple[str, ...]
    values: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.ids)
        values = np.asarray(self.values, dtype=np.float64)
        if values.shape != (n, n) or len(set(self.ids)) != n:
            raise ValueError("values must be square over unique ids")
        if not np.array_equal(values, values.T, equal_nan=True):
            raise ValueError("a correlation matrix is symmetric")
        diagonal = np.diag(values)
        if not ((diagonal == 1.0) | np.isnan(diagonal)).all():
            raise ValueError("the diagonal must be 1 (or NaN for an undefined series)")
        finite = values[np.isfinite(values)]
        if ((finite < -1.0) | (finite > 1.0)).any():
            raise ValueError("a correlation lies in [-1, 1]")
        object.__setattr__(self, "values", _frozen(values))

    def rho(self, a: str, b: str) -> float:
        return float(self.values[self.ids.index(a), self.ids.index(b)])


def correlation_of(ids: Sequence[str], columns: np.ndarray) -> CorrelationMatrix:
    """
    Pairwise-complete Pearson correlation of every pair of value columns (`columns[:, j]` is
    `ids[j]`), whatever the values are -- returns, funding levels, open-interest changes. Each pair
    uses only the rows where both are finite; nothing is filled.
    """
    values_in = np.asarray(columns, dtype=np.float64)
    n = len(ids)
    if values_in.ndim != 2 or values_in.shape[1] != n:
        raise ValueError("columns must be (rows, len(ids))")
    values = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(i, n):
            rho = _pearson(values_in[:, i], values_in[:, j])
            if i == j and not math.isnan(rho):
                rho = 1.0
            values[i, j] = values[j, i] = rho
    return CorrelationMatrix(tuple(ids), values)


def correlation_matrix(aligned: AlignedReturns) -> CorrelationMatrix:
    """Pairwise-complete Pearson correlation of every pair of columns (`correlation_of`)."""
    return correlation_of(aligned.ids, aligned.matrix)


def rolling_correlation(
    a: np.ndarray, b: np.ndarray, window: int, min_pairs: int | None = None
) -> np.ndarray:
    """
    Pearson correlation of each trailing `window` rows of two aligned series, at the window's last
    row: pairwise-complete inside the window, NaN before the window fills and where the window
    holds fewer than `min_pairs` finite pairs (default `max(2, window // 2)`) -- a correlation
    over a handful of pairs in a mostly-empty window is not reported as the window's.

    `window < 2`, `min_pairs` outside `[2, window]` or unequal lengths raise `ValueError`.

    Known limit: one `_pearson` per full-enough window, O(n * window) -- a day of 1 m returns with
    a one-day window is ~2 M operations, fine; a day of 1 s returns with a 1 h window is ~3 * 10^8.
    Upgrade path: masked cumulative sums (sum, sum of squares, cross sum over finite pairs), O(n).
    """
    x = np.asarray(a, dtype=np.float64)
    y = np.asarray(b, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 1:
        raise ValueError("a and b must be aligned 1-D series of equal length")
    if window < 2:
        raise ValueError(f"window must be at least 2 rows, got {window}")
    floor = max(2, window // 2) if min_pairs is None else min_pairs
    if not 2 <= floor <= window:
        raise ValueError(f"min_pairs must be in [2, {window}], got {floor}")
    out = np.full(len(x), np.nan)
    if len(x) < window:
        return out
    finite = (np.isfinite(x) & np.isfinite(y)).astype(np.int64)
    pairs = np.convolve(finite, np.ones(window, dtype=np.int64), mode="valid")
    xs, ys = sliding_window_view(x, window), sliding_window_view(y, window)
    for k in np.flatnonzero(pairs >= floor).tolist():
        out[k + window - 1] = _pearson(xs[k], ys[k])
    return out


def _lagged(a: np.ndarray, b: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    """Return the rows `(a[t], b[t + lag])` pair up, for a lag of either sign."""
    n = len(a)
    return (a[: n - lag], b[lag:]) if lag >= 0 else (a[-lag:], b[: n + lag])


def lagged_pairs(a: np.ndarray, b: np.ndarray, lag: int) -> int:
    """How many rows hold a finite `(a[t], b[t + lag])` pair: the sample behind that lag's rho."""
    x, y = _lagged(a, b, lag)
    return int((np.isfinite(x) & np.isfinite(y)).sum())


def lead_lag(
    a: np.ndarray, b: np.ndarray, max_lag: int, min_pairs: int = 2
) -> list[tuple[int, float]]:
    """
    `(lag, corr(a[t], b[t + lag]))` for every lag in `-max_lag..max_lag`, pairwise-complete.

    A positive lag with the highest correlation means `a` leads `b` by that many periods. `a` and
    `b` must be aligned (same length, same timestamps -- `AlignedReturns.column`). Invariant: a lag
    whose shifted rows hold fewer than `min_pairs` finite pairs is NaN, so no lag's rho rests on a
    handful of pairs (two pairs are ±1 by construction); `min_pairs < 2` raises `ValueError`.
    """
    if len(a) != len(b):
        raise ValueError("a and b must be aligned (equal length)")
    if max_lag < 0 or max_lag >= len(a):
        raise ValueError(f"max_lag must be in [0, {len(a) - 1}], got {max_lag}")
    if min_pairs < 2:
        raise ValueError(f"min_pairs must be >= 2, got {min_pairs}")
    out = []
    for lag in range(-max_lag, max_lag + 1):
        rho = _pearson(*_lagged(a, b, lag)) if lagged_pairs(a, b, lag) >= min_pairs else math.nan
        out.append((lag, rho))
    return out


def _linkage_distance(distance: np.ndarray, left: list[int], right: list[int]) -> float:
    """Single linkage: the closest pair across the two clusters; NaN distances never link."""
    block = distance[np.ix_(left, right)]
    finite = block[np.isfinite(block)]
    return float(finite.min()) if len(finite) else math.inf


def _closest_pair(distance: np.ndarray, clusters: list[list[int]]) -> tuple[float, int, int]:
    best = (math.inf, -1, -1)
    for p in range(len(clusters)):
        for q in range(p + 1, len(clusters)):
            d = _linkage_distance(distance, clusters[p], clusters[q])
            if d < best[0]:
                best = (d, p, q)
    return best


class MergeStep(NamedTuple):
    """One single-linkage merge: the two clusters' ids (each in `matrix.ids` order) and 1 - rho."""

    left: tuple[str, ...]
    right: tuple[str, ...]
    distance: float


def _single_linkage(
    matrix: CorrelationMatrix, threshold: float
) -> tuple[list[MergeStep], list[list[int]]]:
    """
    Run the one single-linkage loop `cluster` and `merge_order` share: merge the closest pair
    (`_closest_pair`, earliest pair on a tie) while its distance is finite and at most
    `threshold`; return the merges in order and the final clusters (member positions, sorted).
    """
    distance = 1.0 - matrix.values
    clusters = [[i] for i in range(len(matrix.ids))]
    steps: list[MergeStep] = []
    while len(clusters) > 1:
        d, p, q = _closest_pair(distance, clusters)
        if math.isinf(d) or d > threshold:
            break
        steps.append(
            MergeStep(
                tuple(matrix.ids[i] for i in clusters[p]),
                tuple(matrix.ids[i] for i in clusters[q]),
                d,
            )
        )
        clusters[p] = sorted(clusters[p] + clusters[q])
        del clusters[q]
    return steps, clusters


def merge_order(matrix: CorrelationMatrix) -> list[MergeStep]:
    """
    Return the single-linkage dendrogram as an ordered list: every merge `cluster` would make with no
    threshold, closest first, each with its distance `1 - rho`. A pair with a NaN correlation
    never links, so the list stops where only such pairs remain (fewer than `n - 1` steps).
    `left` is the cluster holding the earlier id in `matrix.ids`.
    """
    return _single_linkage(matrix, math.inf)[0]


def cluster(matrix: CorrelationMatrix, threshold: float) -> list[list[str]]:
    """
    Single-linkage agglomerative clustering on the distance `1 - rho`: merge the two closest
    clusters while their distance is at most `threshold` (NaN correlations never link);
    `threshold` must be finite and in [0, 2], else `ValueError`.

    Deterministic: ties merge the earliest pair; each cluster lists its ids in `matrix.ids` order
    and clusters are ordered by their first id's position.

    Known limit: the naive loop is O(n^3) in the number of series -- fine for n <= 100 (a
    universe of instruments). Upgrade path: SLINK / a minimum spanning tree, O(n^2).
    """
    if not (math.isfinite(threshold) and 0.0 <= threshold <= 2.0):
        raise ValueError(f"threshold is a distance 1 - rho, in [0, 2]; got {threshold}")
    clusters = _single_linkage(matrix, threshold)[1]
    clusters.sort(key=lambda members: members[0])
    return [[matrix.ids[i] for i in members] for members in clusters]


def peak_lag(pairs: Sequence[tuple[int, float]]) -> tuple[int, float] | None:
    """
    Return the `(lag, rho)` of `lead_lag`'s output with the highest finite correlation; on a tie the
    smallest `|lag|`, then the negative lag. None when no lag has a finite correlation (no
    overlapping returns), so a peak is never read from NaNs.
    """
    finite = [(lag, rho) for lag, rho in pairs if math.isfinite(rho)]
    if not finite:
        return None
    return min(finite, key=lambda pair: (-pair[1], abs(pair[0]), pair[0]))


def basis_bps(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Return `(a / b - 1) * 1e4` per aligned row: how far price `a` sits above price `b`, in basis points
    of `b`. NaN where either is NaN (a gap stays a gap); a price that is infinite or not positive
    is a defect and raises `ValueError`, never a basis. Its TS twin is the chart's cross-venue
    Spread pane, `frontend/src/lib/compare.ts` `spreadBps` (Story 33.9: one formula, a shared
    hand-computed fixture -- main 101 over compare 100 is 100 bps -- asserted on both sides).
    """
    x = np.asarray(a, dtype=np.float64)
    y = np.asarray(b, dtype=np.float64)
    if x.shape != y.shape:
        raise ValueError("a and b must be aligned (equal shape)")
    for prices in (x, y):
        present = prices[~np.isnan(prices)]
        if np.isinf(present).any() or (present <= 0).any():
            raise ValueError("a price must be finite and positive (NaN marks a gap)")
    out = np.full(x.shape, np.nan)
    both = ~np.isnan(x) & ~np.isnan(y)
    out[both] = (x[both] / y[both] - 1.0) * 1e4
    return out
