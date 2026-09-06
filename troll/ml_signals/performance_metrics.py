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
Shared portfolio performance-statistics library (Sharpe, Sortino, Calmar, max drawdown,
profit factor, expectancy, win rate, avg/max win/loss).

A thin wrapper over nautilus_trader's own PyO3 `PortfolioStatistic` classes
(`nautilus_trader.analysis` -- Rust implementations in `crates/analysis/src/statistics/`)
rather than reimplementing these formulas (troll/CLAUDE.md's "use Nautilus built-ins
first"). `live_paper/trade_history.py` is the only writer of `bots:history`'s "metrics"
field (SSOT-02) -- every reader of these numbers (bot_tui today; ML/backtest evaluation
code tomorrow) calls the functions in this module directly on its own realized-pnl/
pnl-by-day data rather than recomputing Sharpe/Sortino by hand, so there is exactly one
implementation of each formula shared across live trading and research.

Two input shapes, matching the two calculation paths nautilus's `PortfolioStatistic`
classes actually support (confirmed against crates/analysis/src/statistics/*.rs, not
just the .pyi stub, since the stub only lists each class's "primary" method):

  - Trade-level stats (win rate, expectancy, avg/max win, avg/max loss) need only a
    flat list of realized PnL per closed trade -- `trade_stats()`.
  - Return-based stats (Sharpe, Sortino, Calmar, max drawdown, profit factor) need a
    time-indexed *returns* series, not raw dollar PnLs -- SharpeRatio/SortinoRatio/
    CalmarRatio/MaxDrawdown/ProfitFactor only implement `calculate_from_returns`;
    `calculate_from_realized_pnls` returns `None` for every one of them.
    `equity_returns()` builds that series from a chronological pnl-by-day list plus a
    starting balance; `return_stats()` then wraps it.
"""

import math

from nautilus_trader.analysis import AvgLoser
from nautilus_trader.analysis import AvgWinner
from nautilus_trader.analysis import CalmarRatio
from nautilus_trader.analysis import Expectancy
from nautilus_trader.analysis import MaxDrawdown
from nautilus_trader.analysis import MaxLoser
from nautilus_trader.analysis import MaxWinner
from nautilus_trader.analysis import ProfitFactor
from nautilus_trader.analysis import SharpeRatio
from nautilus_trader.analysis import SortinoRatio
from nautilus_trader.analysis import WinRate


# Crypto perpetuals trade 24/7 -- 365, not the 252-trading-day default calibrated for
# equities markets -- annualizes daily Sharpe/Sortino/Calmar correctly for this venue.
# Never configurable (troll/CLAUDE.md DESIGN-01): the venue's trading calendar doesn't
# change.
_ANNUALIZATION_PERIOD = 365

_TRADE_STAT_NAMES = ("win_rate", "expectancy", "avg_win", "avg_loss", "max_win", "max_loss")
_RETURN_STAT_NAMES = ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_drawdown", "profit_factor")


def _clean(value: float | None) -> float | None:
    """
    NaN (nautilus's "undefined" sentinel, e.g. zero-variance returns) -> None, never a
    fabricated number -- mirrors this codebase's established "None means genuinely
    unknown" convention (bots_pane.format_win_rate's own docstring).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def trade_stats(realized_pnls: list[float]) -> dict[str, float | None]:
    """
    Per-closed-trade performance stats from a flat list of realized PnL (dollars),
    chronological order not required. Empty input yields every value `None` (never a
    fabricated 0.0).
    """
    if not realized_pnls:
        return dict.fromkeys(_TRADE_STAT_NAMES)
    return {
        "win_rate": _clean(WinRate().calculate_from_realized_pnls(realized_pnls)),
        "expectancy": _clean(Expectancy().calculate_from_realized_pnls(realized_pnls)),
        "avg_win": _clean(AvgWinner().calculate_from_realized_pnls(realized_pnls)),
        "avg_loss": _clean(AvgLoser().calculate_from_realized_pnls(realized_pnls)),
        "max_win": _clean(MaxWinner().calculate_from_realized_pnls(realized_pnls)),
        "max_loss": _clean(MaxLoser().calculate_from_realized_pnls(realized_pnls)),
    }


def equity_returns(pnl_by_day: list[dict], starting_balance: float) -> dict[int, float]:
    """
    dict[ts_ns -> simple daily return] from a chronological, all-time
    fills_store.pnl_by_day()-shaped list (each `{"period_start": ts_ns, "pnl": float}`)
    plus the account's starting balance. One point per day bucket that has closing
    fills; days with none are absent, not zero-filled (matches pnl_by_day's own "only
    days with a closing fill appear" shape).

    Must be fed the *all-time* series, not a pre-windowed one -- a windowed slice would
    anchor its first return against `starting_balance` instead of the account's actual
    equity at that point, silently misstating the return. Callers wanting a
    range-scoped view filter this function's *output* by timestamp instead (see
    `return_stats()`'s own `cutoff_ns` parameter).
    """
    if starting_balance <= 0 or not pnl_by_day:
        return {}
    returns: dict[int, float] = {}
    equity = starting_balance
    for point in pnl_by_day:
        prev_equity = equity
        equity += point["pnl"]
        if prev_equity > 0:
            returns[point["period_start"]] = (equity - prev_equity) / prev_equity
    return returns


def return_stats(
    returns: dict[int, float],
    cutoff_ns: int | None = None,
) -> dict[str, float | None]:
    """
    Return-based performance stats (Sharpe, Sortino, Calmar, max drawdown, profit
    factor) from an all-time `equity_returns()` series, optionally restricted to
    `cutoff_ns` onward for a range-scoped view (e.g. Bot-detail's day/week/month
    ranges). Filtering happens here, not in `equity_returns()`, so the underlying
    equity anchoring always reflects true account history (see that function's own
    docstring).
    """
    windowed = returns if cutoff_ns is None else {ts: r for ts, r in returns.items() if ts >= cutoff_ns}
    if not windowed:
        return dict.fromkeys(_RETURN_STAT_NAMES)
    return {
        "sharpe_ratio": _clean(SharpeRatio(period=_ANNUALIZATION_PERIOD).calculate_from_returns(windowed)),
        "sortino_ratio": _clean(
            SortinoRatio(period=_ANNUALIZATION_PERIOD).calculate_from_returns(windowed)
        ),
        "calmar_ratio": _clean(CalmarRatio(period=_ANNUALIZATION_PERIOD).calculate_from_returns(windowed)),
        "max_drawdown": _clean(MaxDrawdown().calculate_from_returns(windowed)),
        "profit_factor": _clean(ProfitFactor().calculate_from_returns(windowed)),
    }


def all_metrics(
    realized_pnls: list[float],
    pnl_by_day: list[dict],
    starting_balance: float | None,
    cutoff_ns: int | None = None,
) -> dict[str, float | None]:
    """
    Merge `trade_stats()` + `return_stats()` into one flat dict.

    `starting_balance=None` (e.g. real-money mode, where there is no fixed config
    value to anchor an equity curve on) skips every return-based stat rather than
    computing one against a fabricated balance -- only the trade-level stats are
    returned in that case.
    """
    stats = trade_stats(realized_pnls)
    if starting_balance is None:
        stats.update(dict.fromkeys(_RETURN_STAT_NAMES))
    else:
        returns = equity_returns(pnl_by_day, starting_balance)
        stats.update(return_stats(returns, cutoff_ns))
    return stats
