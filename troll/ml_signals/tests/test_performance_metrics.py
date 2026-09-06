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
"""Tests for ml_signals.performance_metrics (financial calculations, troll/CLAUDE.md TEST-01)."""

from ml_signals import performance_metrics


_NS_PER_DAY = 24 * 3600 * 1_000_000_000


def test_trade_stats_empty_input_is_all_none() -> None:
    stats = performance_metrics.trade_stats([])
    assert stats == {
        "win_rate": None,
        "expectancy": None,
        "avg_win": None,
        "avg_loss": None,
        "max_win": None,
        "max_loss": None,
    }


def test_trade_stats_matches_hand_computed_values() -> None:
    stats = performance_metrics.trade_stats([10.0, -5.0, 20.0, -8.0])
    assert stats["win_rate"] == 0.5
    assert stats["expectancy"] == (10.0 - 5.0 + 20.0 - 8.0) / 4
    assert stats["avg_win"] == (10.0 + 20.0) / 2
    assert stats["avg_loss"] == (-5.0 - 8.0) / 2
    assert stats["max_win"] == 20.0
    assert stats["max_loss"] == -8.0


def test_equity_returns_empty_pnl_series_is_empty() -> None:
    assert performance_metrics.equity_returns([], starting_balance=10_000.0) == {}


def test_equity_returns_zero_starting_balance_is_empty() -> None:
    pnl_by_day = [{"period_start": 0, "pnl": 5.0}]
    assert performance_metrics.equity_returns(pnl_by_day, starting_balance=0.0) == {}


def test_equity_returns_is_pct_change_of_running_equity() -> None:
    pnl_by_day = [
        {"period_start": 0, "pnl": 100.0},
        {"period_start": _NS_PER_DAY, "pnl": -50.0},
    ]
    returns = performance_metrics.equity_returns(pnl_by_day, starting_balance=1_000.0)
    assert returns[0] == 100.0 / 1_000.0
    assert returns[_NS_PER_DAY] == -50.0 / 1_100.0


def test_return_stats_empty_input_is_all_none() -> None:
    stats = performance_metrics.return_stats({})
    assert stats == {
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "max_drawdown": None,
        "profit_factor": None,
    }


def test_return_stats_all_gains_has_zero_drawdown() -> None:
    # Varying (not constant) positive returns -- a constant series has zero variance,
    # which makes Sharpe undefined (None) by design; this fixture checks the ordinary
    # case where it IS defined, while every return still being >= 0 keeps drawdown 0.
    returns = {i * _NS_PER_DAY: v for i, v in enumerate([0.01, 0.02, 0.005, 0.03, 0.01])}
    stats = performance_metrics.return_stats(returns)
    assert stats["max_drawdown"] == 0.0
    assert stats["sharpe_ratio"] is not None


def test_return_stats_cutoff_excludes_earlier_points() -> None:
    returns = {0: 0.01, _NS_PER_DAY: 0.02, 2 * _NS_PER_DAY: 0.03}
    windowed = performance_metrics.return_stats(returns, cutoff_ns=_NS_PER_DAY + 1)
    all_time = performance_metrics.return_stats(returns, cutoff_ns=None)
    # A single-point window makes Sharpe undefined (zero variance) -- None, not NaN --
    # while the full 3-point series has a well-defined (non-None) Sharpe.
    assert windowed["sharpe_ratio"] is None
    assert all_time["sharpe_ratio"] is not None


def test_all_metrics_without_starting_balance_skips_return_based_stats() -> None:
    pnl_by_day = [{"period_start": 0, "pnl": 10.0}]
    stats = performance_metrics.all_metrics([10.0], pnl_by_day, starting_balance=None)
    assert stats["win_rate"] == 1.0
    assert stats["sharpe_ratio"] is None
    assert stats["max_drawdown"] is None


def test_all_metrics_merges_trade_and_return_stats() -> None:
    pnl_by_day = [
        {"period_start": i * _NS_PER_DAY, "pnl": v} for i, v in enumerate([10.0, -5.0, 20.0])
    ]
    stats = performance_metrics.all_metrics([10.0, -5.0, 20.0], pnl_by_day, starting_balance=1_000.0)
    assert stats["win_rate"] == 2 / 3
    assert stats["max_drawdown"] is not None
