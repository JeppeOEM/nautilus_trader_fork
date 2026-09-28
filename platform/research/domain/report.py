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
"""`MetricReport`: `kernel.performance_metrics.all_metrics` as a typed record (Story 27.1)."""

from collections.abc import Mapping
from dataclasses import astuple
from dataclasses import dataclass
from dataclasses import fields

from kernel.performance_metrics import all_metrics

from research.domain.trades import TradeLedger


@dataclass(frozen=True)
class MetricReport:
    """
    The portfolio statistics of one run, exactly as `kernel.performance_metrics.all_metrics`
    computed them (Nautilus's `PortfolioStatistic`s); None where a statistic is undefined.

    Invariant: the field names are exactly `all_metrics`' keys and every value came from it --
    this record never computes a statistic (SSOT-02). A statistic added to `performance_metrics`
    fails `from_metrics` until it is added here too, so the two can never drift apart silently.
    """

    win_rate: float | None
    expectancy: float | None
    avg_win: float | None
    avg_loss: float | None
    max_win: float | None
    max_loss: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    max_drawdown: float | None
    profit_factor: float | None

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_metrics(cls, metrics: Mapping[str, float | None]) -> "MetricReport":
        """Freeze an `all_metrics` dict; any key mismatch raises `ValueError`."""
        expected = set(cls.field_names())
        if set(metrics) != expected:
            raise ValueError(
                f"all_metrics keys changed: missing {sorted(expected - set(metrics))}, "
                f"unknown {sorted(set(metrics) - expected)}"
            )
        return cls(**metrics)

    @classmethod
    def from_ledger(
        cls,
        ledger: TradeLedger,
        starting_balance: float | None,
        cutoff_ns: int | None = None,
    ) -> "MetricReport":
        """
        `all_metrics(ledger.realized_pnls(), ledger.pnl_by_day(), starting_balance, cutoff_ns)`:
        `starting_balance=None` leaves every return-based statistic None, as `all_metrics` does.
        """
        return cls.from_metrics(
            all_metrics(ledger.realized_pnls(), ledger.pnl_by_day(), starting_balance, cutoff_ns)
        )

    def as_table(self) -> list[tuple[str, float | None]]:
        """`(name, value)` rows in `all_metrics`' order."""
        return list(zip(self.field_names(), astuple(self), strict=True))
