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
"""
Story 29.6: `bots.strategies.exits` -- exit prices rounded to the instrument's increment away
from the entry (never closer than configured), built exactly at the instrument's precision, and
the entry `OrderList` shaped like `OrderFactory.bracket`'s whether one leg or both are set.
"""

from decimal import Decimal

import pytest

from bots.strategies.exits import entry_with_exits
from bots.strategies.exits import exit_prices
from bots.strategies.exits import make_price
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.model.enums import ContingencyType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_QTY = Quantity.from_str("0.001")


@pytest.mark.parametrize(
    ("side", "mid", "increment", "tp_bps", "sl_bps", "expected"),
    [
        # long: 100.5 * 1.01 = 101.505 -> up to 101.51; 100.5 * 0.99 = 99.495 -> down to 99.49
        (OrderSide.BUY, "100.5", "0.01", 100, 100, ("101.51", "99.49")),
        # short mirrors it: TP below rounded down, SL above rounded up
        (OrderSide.SELL, "100.5", "0.01", 100, 100, ("99.49", "101.51")),
        # already on the grid: no rounding (50_000 * 1.0005 = 50_025)
        (OrderSide.BUY, "50000", "1", 5, 5, ("50025", "49975")),
        # a coarse increment: 50_000.5 * 1.0005 = 50_025.500... -> 50_026; SL 49_975.49... -> 49_975
        (OrderSide.BUY, "50000.5", "1", 5, 5, ("50026", "49975")),
        # a half-tick increment: 1.2345 * 0.999 = 1.23326... -> 1.2330; * 1.002 = 1.23696... -> 1.2370
        (OrderSide.SELL, "1.2345", "0.0005", 10, 20, ("1.2330", "1.2370")),
        # one leg only
        (OrderSide.BUY, "100", "0.1", None, 50, (None, "99.5")),
        (OrderSide.BUY, "100", "0.1", 50, None, ("100.5", None)),
    ],
)
def test_exit_prices_round_away_from_the_entry(
    side: OrderSide,
    mid: str,
    increment: str,
    tp_bps: int | None,
    sl_bps: int | None,
    expected: tuple[str | None, str | None],
) -> None:
    take_profit, stop_loss = exit_prices(side, Decimal(mid), Decimal(increment), tp_bps, sl_bps)
    got = tuple(str(value) if value is not None else None for value in (take_profit, stop_loss))
    assert got == expected


def test_an_exit_is_never_closer_than_configured() -> None:
    mid = Decimal("61090.59855")
    take_profit, stop_loss = exit_prices(OrderSide.BUY, mid, Decimal("0.1"), 5, 5)
    assert take_profit is not None
    assert stop_loss is not None
    assert (take_profit - mid) / mid >= Decimal("0.0005")
    assert (mid - stop_loss) / mid >= Decimal("0.0005")


def test_make_price_is_exact_at_the_instrument_precision() -> None:
    # Price(Decimal("61090.59855"), 16) mis-stamps (platform/CLAUDE.md NAUT-01); from_raw is exact.
    assert str(make_price(Decimal("61090.6"), 1)) == "61090.6"
    assert make_price(Decimal("61090.6"), 1) == Price.from_str("61090.6")
    assert str(make_price(Decimal("0.00012345"), 8)) == "0.00012345"


def _factory() -> OrderFactory:
    return OrderFactory(TraderId("TESTER-001"), StrategyId("S-001"), LiveClock())


def test_both_legs_make_a_bracket() -> None:
    order_list = entry_with_exits(
        _factory(), _IID, OrderSide.BUY, _QTY, Price.from_str("110.0"), Price.from_str("90.0"), 0
    )
    entry, stop_loss, take_profit = order_list.orders
    assert entry.order_type == OrderType.MARKET
    assert (stop_loss.order_type, stop_loss.trigger_price) == (
        OrderType.STOP_MARKET,
        Price.from_str("90.0"),
    )
    assert (take_profit.order_type, take_profit.price) == (
        OrderType.LIMIT,
        Price.from_str("110.0"),
    )
    assert stop_loss.is_reduce_only
    assert take_profit.is_reduce_only
    assert stop_loss.side == take_profit.side == OrderSide.SELL
    assert take_profit.contingency_type == ContingencyType.OUO
    assert not take_profit.is_post_only
    # The legs are emulated (released only once the entry's fill is in the Cache); the entry not.
    assert entry.emulation_trigger == TriggerType.NO_TRIGGER
    assert stop_loss.emulation_trigger == take_profit.emulation_trigger == TriggerType.LAST_PRICE


@pytest.mark.parametrize("leg", ["take_profit", "stop_loss"])
def test_one_leg_is_an_oto_child_of_the_entry(leg: str) -> None:
    price = Price.from_str("90.0")
    take_profit = price if leg == "take_profit" else None
    stop_loss = price if leg == "stop_loss" else None
    order_list = entry_with_exits(_factory(), _IID, OrderSide.SELL, _QTY, take_profit, stop_loss, 7)
    entry, child = order_list.orders
    assert entry.contingency_type == ContingencyType.OTO
    assert entry.linked_order_ids == [child.client_order_id]
    assert child.parent_order_id == entry.client_order_id
    assert entry.order_list_id == child.order_list_id == order_list.id
    assert child.is_reduce_only
    assert child.side == OrderSide.BUY
    assert child.quantity == _QTY
    expected_type = OrderType.LIMIT if leg == "take_profit" else OrderType.STOP_MARKET
    assert child.order_type == expected_type
    assert child.tags == (["TAKE_PROFIT"] if leg == "take_profit" else ["STOP_LOSS"])
    assert entry.emulation_trigger == TriggerType.NO_TRIGGER
    assert child.emulation_trigger == TriggerType.LAST_PRICE


def test_no_leg_is_refused() -> None:
    with pytest.raises(ValueError, match="take-profit, a stop-loss or both"):
        entry_with_exits(_factory(), _IID, OrderSide.BUY, _QTY, None, None, 0)
