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
(Story 27.1), numpy only.

Gaps stay gaps: a timestamp one series lacks is NaN in the aligned matrix, and every statistic here
uses only the rows where both of its inputs are finite (pairwise-complete), never a filled value.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

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


def correlation_matrix(aligned: AlignedReturns) -> CorrelationMatrix:
    """Pairwise-complete Pearson correlation of every pair of columns."""
    n = len(aligned.ids)
    values = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(i, n):
            rho = _pearson(aligned.matrix[:, i], aligned.matrix[:, j])
            if i == j and not math.isnan(rho):
                rho = 1.0
            values[i, j] = values[j, i] = rho
    return CorrelationMatrix(aligned.ids, values)


def lead_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> list[tuple[int, float]]:
    """
    `(lag, corr(a[t], b[t + lag]))` for every lag in `-max_lag..max_lag`, pairwise-complete.

    A positive lag with the highest correlation means `a` leads `b` by that many periods. `a` and
    `b` must be aligned (same length, same timestamps -- `AlignedReturns.column`).
    """
    if len(a) != len(b):
        raise ValueError("a and b must be aligned (equal length)")
    if max_lag < 0 or max_lag >= len(a):
        raise ValueError(f"max_lag must be in [0, {len(a) - 1}], got {max_lag}")
    n = len(a)
    out = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            out.append((lag, _pearson(a[: n - lag], b[lag:])))
        else:
            out.append((lag, _pearson(a[-lag:], b[: n + lag])))
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
    distance = 1.0 - matrix.values
    clusters = [[i] for i in range(len(matrix.ids))]
    while len(clusters) > 1:
        d, p, q = _closest_pair(distance, clusters)
        if math.isinf(d) or d > threshold:
            break
        clusters[p] = sorted(clusters[p] + clusters[q])
        del clusters[q]
    clusters.sort(key=lambda members: members[0])
    return [[matrix.ids[i] for i in members] for members in clusters]
