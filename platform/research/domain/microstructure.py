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
Microstructure statistics (Story 27.3): return autocorrelation, the volatility signature, rolling
realised volatility, a Kyle-lambda style price-impact fit and hit rate by bin -- numpy only.

NaN is a gap everywhere: a statistic is taken over the complete pairs (pairwise-complete) and is
NaN, never 0 and never an exception, where it is undefined (too few points, zero variance). A gap
is never bridged or filled. The volatility here is a research measure over one bounded window;
the screener's volatility is ranking's (`ranking/domain/metrics.py`, SSOT-02), never recomputed.
"""

import math
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np
import numpy.typing as npt
from kernel.clocks import NS_PER_S

from research.domain.returns import ReturnSeries


class Autocorrelation(NamedTuple):
    """
    Invariant: `lags`, `rho` and `pairs` have equal length, in the order asked; `rho` is NaN
    exactly where fewer than 2 complete pairs exist or either side has zero variance.
    """

    lags: np.ndarray
    rho: np.ndarray
    pairs: np.ndarray


class VolatilitySignature(NamedTuple):
    """
    Invariant: `intervals_s`, `variance_per_second` and `n` have equal length; `n` counts the
    complete returns at that interval and `variance_per_second` is NaN exactly where `n` is 0.
    """

    intervals_s: np.ndarray
    variance_per_second: np.ndarray
    n: np.ndarray


class ImpactFit(NamedTuple):
    """
    One OLS fit per `|signed volume|` bucket `[lower, upper)` (the last bucket closed). Invariant:
    all arrays have one entry per bucket; `slope`/`intercept` are NaN exactly where the bucket
    holds fewer than 3 complete points or its signed volume has zero variance.
    """

    lower: np.ndarray
    upper: np.ndarray
    slope: np.ndarray
    intercept: np.ndarray
    n: np.ndarray


class HitRate(NamedTuple):
    """
    Per predictor bin `[lower, upper)` (the last closed). Invariant: `n` is every complete pair in
    the bin, `flat` those with a zero predictor or outcome, and `rate` is the share of the other
    `n - flat` pairs whose signs agree -- NaN when there is none; `mean_predictor` and
    `mean_outcome` (the binned scatter's point) are NaN exactly when `n` is 0.
    """

    lower: np.ndarray
    upper: np.ndarray
    n: np.ndarray
    flat: np.ndarray
    rate: np.ndarray
    mean_predictor: np.ndarray
    mean_outcome: np.ndarray


def _dense(series: ReturnSeries) -> np.ndarray:
    """Return the series on its full period grid from the first stamp: an absent bucket is NaN."""
    if len(series) == 0:
        return np.empty(0)
    period_ns = series.period_seconds * NS_PER_S
    positions = (series.ts_ns - series.ts_ns[0]) // period_ns
    dense = np.full(int(positions[-1]) + 1, np.nan)
    dense[positions] = series.values
    return dense


def _pearson(x: np.ndarray, y: np.ndarray) -> tuple[float, int]:
    complete = np.isfinite(x) & np.isfinite(y)
    n = int(complete.sum())
    if n < 2:
        return math.nan, n
    dx = x[complete] - x[complete].mean()
    dy = y[complete] - y[complete].mean()
    denominator = math.sqrt(float(dx @ dx) * float(dy @ dy))
    if denominator == 0.0:
        return math.nan, n
    return float(dx @ dy) / denominator, n


def autocorrelation(series: ReturnSeries, lags: Sequence[int]) -> Autocorrelation:
    """
    Pearson correlation of each return with the one `lag` periods later, over the complete pairs
    (formula: Pearson's r of `r[t]` and `r[t + lag]`, each side with its own mean and variance
    over the complete pairs -- not the textbook sample ACF of Box, Jenkins & Reinsel §2.1, whose
    single global mean and variance cannot skip a gap). A lag counts periods on
    the series' own grid by timestamp, so an absent bucket never shortens it; it is a lag in
    points on a complete grid. A lag at or past the series' length is NaN with 0 pairs, never an
    error; a lag below 1 raises `ValueError`.
    """
    if any(lag < 1 for lag in lags):
        raise ValueError(f"lags must be >= 1, got {list(lags)}")
    dense = _dense(series)
    rho, pairs = [], []
    for lag in lags:
        value, n = _pearson(dense[:-lag], dense[lag:]) if lag < len(dense) else (math.nan, 0)
        rho.append(value)
        pairs.append(n)
    return Autocorrelation(
        np.asarray(lags, dtype=np.int64),
        np.asarray(rho, dtype=np.float64),
        np.asarray(pairs, dtype=np.int64),
    )


def volatility_signature(series: ReturnSeries, intervals_s: Sequence[int]) -> VolatilitySignature:
    """
    Realised variance per second at each sampling interval q: the mean of the squared q-second
    returns (compounded by `ReturnSeries.resample`, a bucket holding a gap is NaN and left out)
    divided by q (formula: the volatility signature plot, Andersen, Bollerslev, Diebold & Labys
    2000, "Great realizations"). For i.i.d. returns it is flat in q; microstructure noise bends it
    at short q. An interval that is not a multiple of the series' period raises `ValueError`.
    """
    variances, counts = [], []
    for interval in intervals_s:
        values = series.resample(interval).values
        finite = values[np.isfinite(values)]
        counts.append(len(finite))
        variances.append(
            float((finite @ finite) / len(finite) / interval) if len(finite) else math.nan
        )
    return VolatilitySignature(
        np.asarray(intervals_s, dtype=np.int64),
        np.asarray(variances, dtype=np.float64),
        np.asarray(counts, dtype=np.int64),
    )


def realised_volatility(series: ReturnSeries, window: int) -> np.ndarray:
    """
    Trailing realised volatility per point, annualised: `sqrt(mean(r^2)) * annualisation_factor`
    over the last `window` returns (formula: realised volatility with a zero-mean assumption,
    Andersen & Bollerslev 1998), aligned to `ts_ns`. NaN before the window fills, when the window
    holds a NaN, or when its points are not contiguous on the grid (an absent bucket inside it).
    `window` must be a positive int.
    """
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError(f"window must be a positive int, got {window!r}")
    out = np.full(len(series), np.nan)
    if len(series) < window:
        return out
    values = series.values
    gaps = np.r_[0, np.cumsum(~np.isfinite(values))]
    squares = np.r_[0.0, np.cumsum(np.where(np.isfinite(values), values * values, 0.0))]
    ends = np.arange(window, len(series) + 1)
    holds_gap = gaps[ends] - gaps[ends - window] > 0
    span_ns = series.ts_ns[ends - 1] - series.ts_ns[ends - window]
    contiguous = span_ns == (window - 1) * series.period_seconds * NS_PER_S
    mean_square = np.maximum((squares[ends] - squares[ends - window]) / window, 0.0)
    rv = np.sqrt(mean_square) * series.annualisation_factor
    out[ends - 1] = np.where(~holds_gap & contiguous, rv, np.nan)
    return out


def _edges(edges: npt.ArrayLike) -> np.ndarray:
    array = np.asarray(edges, dtype=np.float64)
    if array.ndim != 1 or len(array) < 2 or not np.isfinite(array).all():
        raise ValueError(f"edges must be at least 2 finite values, got {array.tolist()}")
    if not (np.diff(array) > 0).all():
        raise ValueError(f"edges must be strictly increasing, got {array.tolist()}")
    return array


def _bin_of(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Bin index per value (`[e_i, e_i+1)`, the last bin closed); -1 outside or NaN."""
    index = np.searchsorted(edges, values, side="right") - 1
    index[values == edges[-1]] = len(edges) - 2
    inside = np.isfinite(values) & (index >= 0) & (index < len(edges) - 1)
    return np.where(inside, index, -1)


def _fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    if len(x) < 3 or float(np.var(x)) == 0.0:
        return math.nan, math.nan
    design = np.column_stack([x, np.ones_like(x)])
    (slope, intercept), *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(slope), float(intercept)


def price_impact(
    signed_volume: npt.ArrayLike, mid_change: npt.ArrayLike, edges: npt.ArrayLike
) -> ImpactFit:
    """
    Per bucket of `|signed_volume|` (`edges`, strictly increasing), the OLS fit
    `mid_change = slope * signed_volume + intercept` over the bucket's complete pairs, by
    `numpy.linalg.lstsq` (formula: Kyle's lambda as the regression slope of price change on signed
    order flow -- Kyle 1985, "Continuous auctions and insider trading"; Hasbrouck 2007, *Empirical
    Market Microstructure*, ch. 5). A bucket with under 3 points or zero signed-volume variance is
    NaN. Pairs outside the edges are left out.
    """
    x = np.asarray(signed_volume, dtype=np.float64)
    y = np.asarray(mid_change, dtype=np.float64)
    if x.shape != y.shape:
        raise ValueError("signed_volume and mid_change must have equal length")
    bounds = _edges(edges)
    bins = _bin_of(np.abs(x), bounds)
    complete = np.isfinite(y)
    slopes, intercepts, counts = [], [], []
    for b in range(len(bounds) - 1):
        chosen = (bins == b) & complete
        slope, intercept = _fit(x[chosen], y[chosen])
        slopes.append(slope)
        intercepts.append(intercept)
        counts.append(int(chosen.sum()))
    return ImpactFit(
        bounds[:-1],
        bounds[1:],
        np.asarray(slopes, dtype=np.float64),
        np.asarray(intercepts, dtype=np.float64),
        np.asarray(counts, dtype=np.int64),
    )


def hit_rate_by_bin(
    predictor: npt.ArrayLike, outcome: npt.ArrayLike, edges: npt.ArrayLike
) -> HitRate:
    """
    Per predictor bin (`edges`, strictly increasing), how often the outcome's sign agrees with the
    predictor's over the complete pairs where neither is zero (formula: the directional hit rate,
    `sign(predictor) == sign(outcome)`, as in Stoikov 2018, "The micro-price", §4). Zero pairs are
    counted in `flat`, never as a miss. An empty bin has `n` 0 and a NaN rate. Each bin also
    carries its mean predictor and mean outcome, the point of a binned scatter.
    """
    p = np.asarray(predictor, dtype=np.float64)
    o = np.asarray(outcome, dtype=np.float64)
    if p.shape != o.shape:
        raise ValueError("predictor and outcome must have equal length")
    bounds = _edges(edges)
    bins = _bin_of(p, bounds)
    complete = np.isfinite(o)
    counts, flats, rates, means_p, means_o = [], [], [], [], []
    for b in range(len(bounds) - 1):
        chosen = (bins == b) & complete
        signs_p, signs_o = np.sign(p[chosen]), np.sign(o[chosen])
        moving = (signs_p != 0) & (signs_o != 0)
        counts.append(int(chosen.sum()))
        flats.append(int((~moving).sum()))
        rates.append(
            float((signs_p[moving] == signs_o[moving]).mean()) if moving.any() else math.nan
        )
        means_p.append(float(p[chosen].mean()) if chosen.any() else math.nan)
        means_o.append(float(o[chosen].mean()) if chosen.any() else math.nan)
    return HitRate(
        bounds[:-1],
        bounds[1:],
        np.asarray(counts, dtype=np.int64),
        np.asarray(flats, dtype=np.int64),
        np.asarray(rates, dtype=np.float64),
        np.asarray(means_p, dtype=np.float64),
        np.asarray(means_o, dtype=np.float64),
    )
