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
"""`EquityCurve` and its drawdown episodes (Story 27.1)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def _frozen(array: np.ndarray) -> np.ndarray:
    copy = np.array(array, copy=True)
    copy.flags.writeable = False
    return copy


@dataclass(frozen=True)
class DrawdownEpisode:
    """
    One peak-to-recovery drawdown of an equity curve.

    Invariant: `peak_ts` is None when the peak is the curve's `starting_balance` (the equity
    before its first point, which has no stamp), else `peak_ts <= trough_ts`; `recovery_ts` is None
    (not recovered by the curve's end) or after `trough_ts`, and `depth` is the trough's positive
    fractional loss from the peak (`0.25` = 25% under water). Built only by `EquityCurve.drawdowns`.
    """

    peak_ts: int | None
    trough_ts: int
    recovery_ts: int | None
    depth: float

    def __post_init__(self) -> None:
        if self.peak_ts is not None and self.trough_ts < self.peak_ts:
            raise ValueError("a trough cannot precede its peak")
        if self.recovery_ts is not None and self.recovery_ts <= self.trough_ts:
            raise ValueError("a recovery must follow its trough")
        if not (self.depth > 0 and math.isfinite(self.depth)):
            raise ValueError(f"depth must be a positive finite fraction, got {self.depth}")


@dataclass(frozen=True, eq=False)
class EquityCurve:
    """
    Account equity over time.

    Invariant: at least one point; `ts_ns` (int64) strictly increasing; every value finite;
    `starting_balance` positive and finite (the equity before the first point). Violated by
    constructing it from unsorted or duplicate stamps, a NaN/inf value, or a non-positive balance
    -- `__post_init__` raises `ValueError`. The arrays are stored as read-only copies.
    """

    ts_ns: np.ndarray
    values: np.ndarray
    starting_balance: float

    def __post_init__(self) -> None:
        ts = np.asarray(self.ts_ns)
        values = np.asarray(self.values)
        if ts.ndim != 1 or ts.shape != values.shape or len(ts) == 0:
            raise ValueError("ts_ns and values must be non-empty 1-D arrays of equal length")
        if not np.issubdtype(ts.dtype, np.integer):
            raise ValueError("ts_ns must be integer nanoseconds")
        if not (np.diff(ts) > 0).all():
            raise ValueError("ts_ns must be strictly increasing")
        values = values.astype(np.float64)
        if not np.isfinite(values).all():
            raise ValueError("every equity value must be finite")
        if not (math.isfinite(self.starting_balance) and self.starting_balance > 0):
            raise ValueError(f"starting_balance must be positive, got {self.starting_balance}")
        object.__setattr__(self, "ts_ns", _frozen(ts.astype(np.int64)))
        object.__setattr__(self, "values", _frozen(values))

    def __len__(self) -> int:
        return len(self.values)

    @classmethod
    def from_pnl_by_day(cls, pnl_by_day: Sequence[dict], starting_balance: float) -> "EquityCurve":
        """
        One point per `fills_store.pnl_by_day()`-shaped entry (`{"period_start": ts_ns, "pnl":
        float}`, chronological, all-time): the equity after that day, accumulated with exactly
        `kernel.performance_metrics.equity_returns`' arithmetic (`equity += pnl`), so
        `returns_by_ts()` equals `equity_returns(pnl_by_day, starting_balance)` bit for bit.
        """
        equity = starting_balance
        ts: list[int] = []
        values: list[float] = []
        for point in pnl_by_day:
            equity += point["pnl"]
            ts.append(point["period_start"])
            values.append(equity)
        return cls(np.array(ts, dtype=np.int64), np.array(values), starting_balance)

    def returns_by_ts(self) -> dict[int, float]:
        """
        `{ts_ns: simple return}` against the previous point (`starting_balance` before the first),
        skipping a point whose previous equity is not positive -- the same loop and arithmetic as
        `kernel.performance_metrics.equity_returns`, so either feeds `return_stats` identically.
        """
        returns: dict[int, float] = {}
        prev_equity = self.starting_balance
        for ts, equity in zip(self.ts_ns.tolist(), self.values.tolist(), strict=True):
            if prev_equity > 0:
                returns[ts] = (equity - prev_equity) / prev_equity
            prev_equity = equity
        return returns

    def underwater(self) -> np.ndarray:
        """
        `value / running peak - 1` at every point (0 at a peak, negative under water). The running
        peak starts at `starting_balance`, so a loss from the starting capital before the curve
        ever rose is under water from its first point.
        """
        peak = np.maximum.accumulate(np.maximum(self.values, self.starting_balance))
        return self.values / peak - 1.0

    def drawdowns(self) -> tuple[np.ndarray, list[DrawdownEpisode]]:
        """
        Return the underwater series and every drawdown episode, oldest first.

        An episode opens when the curve drops below its running peak; `peak_ts` is that peak's
        stamp (the latest point at the peak value, None while the peak is still `starting_balance`),
        `trough_ts` the lowest point before recovery, `recovery_ts` the first point back at or above
        the peak value (None if the curve ends under water).
        """
        under = self.underwater()
        episodes: list[DrawdownEpisode] = []
        ts = self.ts_ns.tolist()
        peak: int | None = None
        i = 0
        while i < len(under):
            if under[i] == 0.0:
                peak = i
                i += 1
                continue
            end = self._recovery_index(under, i)
            episodes.append(self._episode(under, ts, peak, i, end))
            peak = end if end is not None else len(under)
            i = (end + 1) if end is not None else len(under)
        return under, episodes

    @staticmethod
    def _recovery_index(under: np.ndarray, start: int) -> int | None:
        recovered = np.flatnonzero(under[start:] == 0.0)
        return int(start + recovered[0]) if len(recovered) else None

    @staticmethod
    def _episode(
        under: np.ndarray, ts: list[int], peak: int | None, start: int, end: int | None
    ) -> DrawdownEpisode:
        stop = end if end is not None else len(under)
        trough = start + int(np.argmin(under[start:stop]))
        return DrawdownEpisode(
            peak_ts=ts[peak] if peak is not None else None,
            trough_ts=ts[trough],
            recovery_ts=ts[end] if end is not None else None,
            depth=float(-under[trough]),
        )
