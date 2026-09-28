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
`ReturnSeries`: simple returns on one fixed period grid (Story 27.1).

Crypto perpetuals trade 24/7, so a year is `365 * 86400` seconds of trading, never 252 sessions:
the annualisation factor is derived from the series' own period and is never a caller constant.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from kernel.clocks import NS_PER_S
from kernel.performance_metrics import return_stats

from research.domain.equity import EquityCurve


_SECONDS_PER_YEAR = 365 * 86_400
# Nautilus's SharpeRatio bins returns into UTC days; a standard deviation needs two bins.
_MIN_SHARPE_WINDOW_SECONDS = 2 * 86_400


def _frozen(array: np.ndarray) -> np.ndarray:
    """Return a read-only copy, so a frozen value object cannot be mutated through its arrays."""
    copy = np.array(array, copy=True)
    copy.flags.writeable = False
    return copy


def _period_ns(period_seconds: int) -> int:
    if isinstance(period_seconds, bool) or not isinstance(period_seconds, int):
        raise ValueError(f"period_seconds must be an int, got {period_seconds!r}")
    if period_seconds <= 0:
        raise ValueError(f"period_seconds must be positive, got {period_seconds}")
    return period_seconds * NS_PER_S


def _price_array(prices: Sequence[float | None]) -> np.ndarray:
    """
    None/NaN -> NaN (a gap); an infinite or non-positive price is a defect, never a gap or a
    return (DATA-07).
    """
    values = np.array([math.nan if p is None else float(p) for p in prices], dtype=np.float64)
    if np.isinf(values).any():
        raise ValueError("a price must be finite (None/NaN marks a gap)")
    finite = values[np.isfinite(values)]
    if (finite <= 0).any():
        raise ValueError("a price must be positive (None/NaN marks a gap)")
    return values


def _grid_buckets(ts_ns: Sequence[int], period_ns: int) -> np.ndarray:
    buckets = np.asarray(ts_ns, dtype=np.int64) // period_ns * period_ns
    if len(buckets) > 1 and not (np.diff(buckets) > 0).all():
        raise ValueError(
            "timestamps must fall in strictly increasing grid buckets (two prices in one bucket, "
            "or out of order)"
        )
    return buckets


@dataclass(frozen=True, eq=False)
class ReturnSeries:
    """
    Simple returns, one per grid bucket, on exactly one period.

    Invariant: `values` (float64) and `ts_ns` (int64) have equal length; `ts_ns` is strictly
    increasing and every stamp is a multiple of `period_seconds` (the bucket start); one period per
    series, so `annualisation_factor` follows from the stored period alone. NaN is a gap (a missing
    or non-adjacent price), never filled. Violated by constructing it from mismatched arrays, an
    off-grid or unsorted stamp, or a non-positive period -- `__post_init__` raises `ValueError`.
    The arrays are stored as read-only copies.
    """

    values: np.ndarray
    ts_ns: np.ndarray
    period_seconds: int

    def __post_init__(self) -> None:
        period_ns = _period_ns(self.period_seconds)
        if not isinstance(self.values, np.ndarray) or not isinstance(self.ts_ns, np.ndarray):
            raise ValueError("values and ts_ns must be numpy arrays")
        if self.values.dtype != np.float64 or self.ts_ns.dtype != np.int64:
            raise ValueError("values must be float64 and ts_ns int64")
        if self.values.ndim != 1 or self.values.shape != self.ts_ns.shape:
            raise ValueError("values and ts_ns must be 1-D arrays of equal length")
        if len(self.ts_ns) > 1 and not (np.diff(self.ts_ns) > 0).all():
            raise ValueError("ts_ns must be strictly increasing")
        if (self.ts_ns % period_ns != 0).any():
            raise ValueError("every ts_ns must be a bucket start (a multiple of the period)")
        object.__setattr__(self, "values", _frozen(self.values))
        object.__setattr__(self, "ts_ns", _frozen(self.ts_ns))

    def __len__(self) -> int:
        return len(self.values)

    @classmethod
    def from_prices(
        cls,
        prices: Sequence[float | None],
        ts_ns: Sequence[int],
        period_seconds: int,
    ) -> "ReturnSeries":
        """
        Build the returns of a price path observed on the `period_seconds` grid.

        Each stamp is floored to its bucket; two prices in one bucket raise `ValueError` (sample
        the path first -- this never picks one silently). The return at point i is
        `p[i] / p[i-1] - 1` only when the two buckets are adjacent and both prices are finite;
        otherwise NaN, so a gap is never bridged. The series starts at the second point.
        """
        if len(prices) != len(ts_ns):
            raise ValueError("prices and ts_ns must have equal length")
        period_ns = _period_ns(period_seconds)
        buckets = _grid_buckets(ts_ns, period_ns)
        p = _price_array(prices)
        if len(p) < 2:
            return cls(np.empty(0), np.empty(0, dtype=np.int64), period_seconds)
        adjacent = np.diff(buckets) == period_ns
        with np.errstate(invalid="ignore"):
            simple = p[1:] / p[:-1] - 1.0
        values = np.where(adjacent & np.isfinite(simple), simple, np.nan)
        return cls(values, buckets[1:], period_seconds)

    @classmethod
    def from_equity(cls, curve: EquityCurve, period_seconds: int) -> "ReturnSeries":
        """
        Build the returns of an equity curve sampled at every bucket from its first to its last point.

        Each bucket's equity is the curve's last value at or before the bucket's end. Holding it
        between points is the definition of equity (account state changes only on an event), not
        a market-price forward-fill. The first bucket's return is against `starting_balance` (the
        equity before the first point), as in `EquityCurve.returns_by_ts`, so a move inside the
        first bucket is never dropped. A bucket whose previous equity is not positive yields NaN (a
        return on a non-positive base is undefined).
        """
        period_ns = _period_ns(period_seconds)
        first = int(curve.ts_ns[0]) // period_ns * period_ns
        last = int(curve.ts_ns[-1]) // period_ns * period_ns
        buckets = np.arange(first, last + period_ns, period_ns, dtype=np.int64)
        at = np.searchsorted(curve.ts_ns, buckets + period_ns, side="left") - 1
        equity = np.r_[curve.starting_balance, curve.values[at]]
        with np.errstate(invalid="ignore", divide="ignore"):
            simple = equity[1:] / equity[:-1] - 1.0
        values = np.where(equity[:-1] > 0, simple, np.nan)
        return cls(values.astype(np.float64), buckets, period_seconds)

    def resample(self, period_seconds: int) -> "ReturnSeries":
        """
        Compound into a coarser grid: `prod(1 + r) - 1` per new bucket, never an average.

        `period_seconds` must be a positive multiple of the current period, else `ValueError`. A
        bucket is complete only when it holds all `period_seconds / self.period_seconds` points; an
        incomplete bucket (a missing sub-period, e.g. the series' first or last partial bucket) is
        NaN, as is a bucket holding a NaN -- a gap is never compounded over. A bucket with no point
        at all is absent.
        """
        new_ns = _period_ns(period_seconds)
        if period_seconds % self.period_seconds != 0:
            raise ValueError(
                f"{period_seconds}s is not a multiple of the series period {self.period_seconds}s"
            )
        if len(self) == 0:
            return ReturnSeries(np.empty(0), np.empty(0, dtype=np.int64), period_seconds)
        keys = self.ts_ns // new_ns
        starts = np.flatnonzero(np.r_[True, np.diff(keys) != 0])
        compounded = np.multiply.reduceat(1.0 + self.values, starts) - 1.0
        counts = np.diff(np.r_[starts, len(keys)])
        complete = counts == period_seconds // self.period_seconds
        values = np.where(complete, compounded, np.nan)
        return ReturnSeries(values, keys[starts] * new_ns, period_seconds)

    @property
    def annualisation_factor(self) -> float:
        """`sqrt(periods per year)`, a year being 365 days of 24/7 crypto trading."""
        return math.sqrt(_SECONDS_PER_YEAR / self.period_seconds)

    def as_dict(self) -> dict[int, float]:
        """`{ts_ns: return}` for the finite points: the `performance_metrics.return_stats` input."""
        return {
            int(ts): float(r)
            for ts, r in zip(self.ts_ns.tolist(), self.values.tolist(), strict=True)
            if math.isfinite(r)
        }

    def rolling_sharpe(self, window: int) -> np.ndarray:
        """
        Sharpe ratio of each trailing `window` points, aligned to `ts_ns` (NaN before the window
        fills, when the window holds a NaN, or when the statistic is undefined).

        Each window goes through `kernel.performance_metrics.return_stats` (Nautilus's
        `SharpeRatio`), never a local mean/std formula: Nautilus bins the window's returns into UTC
        days and annualises by 365, so a window of sub-daily returns is a daily-binned Sharpe, and a
        window spanning under two days (`window * period_seconds < 2 * 86400`) could never produce
        a value -- it raises `ValueError` instead of returning an all-NaN series.

        Known limit: one `return_stats` call (Nautilus's pandas-backed statistic) per window, so
        O(len * window) Python work -- seconds for daily/hourly returns, minutes for a month of
        minute returns; upgrade path: an incremental daily-binned Sharpe in
        `kernel.performance_metrics`, cross-checked against `return_stats`, so SSOT-02 holds.
        """
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValueError(f"window must be a positive int, got {window!r}")
        if window * self.period_seconds < _MIN_SHARPE_WINDOW_SECONDS:
            raise ValueError(
                f"a {window}-point window of {self.period_seconds}s returns spans under two days: "
                "Nautilus bins returns into UTC days, so its Sharpe needs at least two daily bins"
            )
        out = np.full(len(self), np.nan)
        ts = self.ts_ns.tolist()
        values = self.values.tolist()
        for end in range(window, len(self) + 1):
            chunk = values[end - window : end]
            if any(math.isnan(r) for r in chunk):
                continue
            sharpe = return_stats(dict(zip(ts[end - window : end], chunk, strict=True)))[
                "sharpe_ratio"
            ]
            if sharpe is not None:
                out[end - 1] = sharpe
        return out
