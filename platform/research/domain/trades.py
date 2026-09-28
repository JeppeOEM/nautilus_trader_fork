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
"""`ClosedTrade` and `TradeLedger`: a run's round trips (Story 27.1)."""

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

import numpy as np
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S


SIDES = frozenset({"LONG", "SHORT"})


@dataclass(frozen=True)
class ClosedTrade:
    """
    One closed round trip (a position from open to flat).

    Invariant: `exit_ts >= entry_ts`, `side` is `LONG` or `SHORT`, `qty` is positive and finite
    (the round trip's *peak* position size -- Nautilus's `peak_qty` -- not a traded volume: a
    position scaled 1 -> 3 -> 1 -> 0 has `qty` 3), `realized_pnl` and `fees` are finite. `realized_pnl` is net of every commission the position
    paid (Nautilus's own position PnL); `fees` is that commission total, informational only --
    never subtract it again. Violated by constructing it out of order or with a NaN amount --
    `__post_init__` raises `ValueError`.
    """

    instrument_id: str
    entry_ts: int
    exit_ts: int
    side: str
    qty: float
    realized_pnl: float
    fees: float

    def __post_init__(self) -> None:
        if self.exit_ts < self.entry_ts:
            raise ValueError(f"exit_ts {self.exit_ts} precedes entry_ts {self.entry_ts}")
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {sorted(SIDES)}, got {self.side!r}")
        if not (math.isfinite(self.qty) and self.qty > 0):
            raise ValueError(f"qty must be positive and finite, got {self.qty}")
        if not (math.isfinite(self.realized_pnl) and math.isfinite(self.fees)):
            raise ValueError("realized_pnl and fees must be finite")


def _exit_datetime(trade: ClosedTrade) -> datetime:
    return datetime.fromtimestamp(trade.exit_ts // NS_PER_S, tz=UTC)


@dataclass(frozen=True)
class TradeLedger:
    """
    A run's closed trades, oldest exit first.

    Invariant: `trades` is ordered by `exit_ts` (ties keep their given order), so
    `realized_pnls()` and `pnl_by_day()` are chronological -- `pnl_by_day` is exactly the
    all-time series `kernel.performance_metrics.equity_returns` requires. Violated by passing an
    unordered tuple -- `__post_init__` raises `ValueError`; `of()` sorts first.
    """

    trades: tuple[ClosedTrade, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "trades", tuple(self.trades))  # a list would stay mutable
        exits = [t.exit_ts for t in self.trades]
        if exits != sorted(exits):
            raise ValueError("trades must be ordered by exit_ts (use TradeLedger.of)")

    @classmethod
    def of(cls, trades: Iterable[ClosedTrade]) -> "TradeLedger":
        """Build a ledger of `trades` in exit order (a stable sort)."""
        return cls(tuple(sorted(trades, key=lambda t: t.exit_ts)))

    def __len__(self) -> int:
        return len(self.trades)

    def realized_pnls(self) -> list[float]:
        """Net realized PnL per trade: `performance_metrics.trade_stats`' input."""
        return [t.realized_pnl for t in self.trades]

    def holding_times_s(self) -> np.ndarray:
        """Seconds from entry to exit, per trade."""
        return np.array([(t.exit_ts - t.entry_ts) / NS_PER_S for t in self.trades], dtype=float)

    def by_hour_of_day(self) -> dict[int, float]:
        """Sum realized PnL by the UTC hour (0-23) of each exit; hours with no exit are absent."""
        sums: dict[int, float] = defaultdict(float)
        for trade in self.trades:
            sums[_exit_datetime(trade).hour] += trade.realized_pnl
        return dict(sorted(sums.items()))

    def by_weekday(self) -> dict[int, float]:
        """Sum realized PnL by the UTC weekday of each exit (0 = Monday); others are absent."""
        sums: dict[int, float] = defaultdict(float)
        for trade in self.trades:
            sums[_exit_datetime(trade).weekday()] += trade.realized_pnl
        return dict(sorted(sums.items()))

    def pnl_by_day(self) -> list[dict]:
        """
        Net realized PnL per UTC day of exit, `[{"period_start": day_start_ns, "pnl": float}]`
        in day order -- the `bots.infrastructure.fills_store.pnl_by_day` shape; days with no exit
        are absent, not zero.
        """
        sums: dict[int, float] = defaultdict(float)
        for day, trade in zip(self.exit_day_starts().tolist(), self.trades, strict=True):
            sums[day] += trade.realized_pnl
        return [{"period_start": day, "pnl": pnl} for day, pnl in sorted(sums.items())]

    def exit_day_starts(self) -> np.ndarray:
        """
        Return the UTC day start (int64 ns) of each trade's exit, in ledger order: the one day-binning
        rule `pnl_by_day` groups by, shared with `monte_carlo.bootstrap_trades` (Story 27.6) so a
        resampled path is binned into exactly the days its ledger is.
        """
        exits = np.array([t.exit_ts for t in self.trades], dtype=np.int64)
        return exits // NS_PER_DAY * NS_PER_DAY
