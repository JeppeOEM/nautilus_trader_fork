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
"""`research.domain.trades`: the ledger and its fills-store-shaped aggregates (Story 27.1)."""

import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.performance_metrics import trade_stats

from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger


# 2024-09-02 is a Monday (UTC).
_MONDAY = 19_968 * NS_PER_DAY
_HOUR = 3_600 * NS_PER_S


def _trade(exit_ts: int, pnl: float, held_s: int = 60, side: str = "LONG") -> ClosedTrade:
    return ClosedTrade(
        "BTC-USD-PERP.DYDX", exit_ts - held_s * NS_PER_S, exit_ts, side, 0.01, pnl, 0.001
    )


def _ledger() -> TradeLedger:
    return TradeLedger.of(
        [
            _trade(_MONDAY + NS_PER_DAY + 5 * _HOUR, -2.0, held_s=30),
            _trade(_MONDAY + 2 * _HOUR, 1.5),
            _trade(_MONDAY + 2 * _HOUR + 60 * NS_PER_S, 0.5, side="SHORT"),
        ]
    )


def test_exit_before_entry_raises() -> None:
    with pytest.raises(ValueError, match="precedes"):
        ClosedTrade("X.DYDX", 2, 1, "LONG", 1.0, 0.0, 0.0)


def test_bad_side_or_qty_raise() -> None:
    with pytest.raises(ValueError, match="side"):
        ClosedTrade("X.DYDX", 1, 2, "BUY", 1.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="qty"):
        ClosedTrade("X.DYDX", 1, 2, "LONG", 0.0, 0.0, 0.0)


def test_an_unordered_tuple_raises_and_of_sorts() -> None:
    trades = (_trade(2 * _HOUR, 1.0), _trade(_HOUR, 1.0))
    with pytest.raises(ValueError, match="ordered"):
        TradeLedger(trades)
    assert [t.exit_ts for t in TradeLedger.of(trades).trades] == [_HOUR, 2 * _HOUR]


def test_a_list_is_frozen_into_a_tuple() -> None:
    trades = [_trade(_HOUR, 1.0)]
    ledger = TradeLedger(trades)  # type: ignore[arg-type]
    trades.append(_trade(0, 1.0))
    assert isinstance(ledger.trades, tuple)
    assert len(ledger) == 1


def test_realized_pnls_are_trade_stats_input_in_exit_order() -> None:
    pnls = _ledger().realized_pnls()
    assert pnls == [1.5, 0.5, -2.0]
    assert trade_stats(pnls)["win_rate"] == pytest.approx(2 / 3)


def test_holding_times() -> None:
    assert _ledger().holding_times_s().tolist() == [60.0, 60.0, 30.0]


def test_by_hour_and_weekday() -> None:
    ledger = _ledger()
    assert ledger.by_hour_of_day() == {2: 2.0, 5: -2.0}
    assert ledger.by_weekday() == {0: 2.0, 1: -2.0}


def test_pnl_by_day_matches_the_fills_store_shape() -> None:
    assert _ledger().pnl_by_day() == [
        {"period_start": _MONDAY, "pnl": 2.0},
        {"period_start": _MONDAY + NS_PER_DAY, "pnl": -2.0},
    ]
