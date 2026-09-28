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
"""`research.domain.report.MetricReport`: a typed `all_metrics`, never a second formula (Story 27.1)."""

import pytest
from kernel.clocks import NS_PER_DAY
from kernel.performance_metrics import all_metrics

from research.domain.report import MetricReport
from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger


def _ledger() -> TradeLedger:
    return TradeLedger.of(
        ClosedTrade(
            "BTC-USD-PERP.DYDX", day * NS_PER_DAY, day * NS_PER_DAY + 1, "LONG", 1.0, pnl, 0.0
        )
        for day, pnl in ((20_000, 10.0), (20_001, -4.0), (20_002, 7.0), (20_003, 2.5))
    )


def test_fields_are_exactly_all_metrics_keys() -> None:
    assert MetricReport.field_names() == tuple(all_metrics([], [], 1.0))


def test_from_ledger_is_all_metrics_verbatim() -> None:
    ledger = _ledger()
    report = MetricReport.from_ledger(ledger, 1_000.0)
    expected = all_metrics(ledger.realized_pnls(), ledger.pnl_by_day(), 1_000.0)
    assert report.as_table() == list(expected.items())
    assert report.sharpe_ratio is not None


def test_no_balance_leaves_return_stats_none() -> None:
    report = MetricReport.from_ledger(_ledger(), None)
    assert report.sharpe_ratio is None
    assert report.win_rate == pytest.approx(0.75)


def test_a_key_mismatch_raises() -> None:
    metrics = all_metrics([], [], 1.0)
    with pytest.raises(ValueError, match="missing"):
        MetricReport.from_metrics({k: v for k, v in metrics.items() if k != "win_rate"})
    with pytest.raises(ValueError, match="unknown"):
        MetricReport.from_metrics({**metrics, "omega_ratio": 1.0})
