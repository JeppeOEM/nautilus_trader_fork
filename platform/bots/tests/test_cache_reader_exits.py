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
Story 29.6: `StrategyCacheReader` classifies a bot's own resting orders into stop-loss and
take-profit by the order itself -- reduce-only flag, type, side against the open position -- on
real Nautilus orders in a real `Cache`, driven through a real `BacktestEngine` (TEST-03).

`_Scripted` opens a position with a market order on the first quote and places the test's exit
orders on the second; `engine.run(streaming=True)` leaves the strategy running (not stopped), so
the orders are read exactly as a live heartbeat would find them.
"""

from collections.abc import Callable
from decimal import Decimal

from bots.application.ports import PositionSnapshot
from bots.infrastructure.cache_reader import STOP_LOSS
from bots.infrastructure.cache_reader import TAKE_PROFIT
from bots.infrastructure.cache_reader import ExitSummary
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.cache_reader import protective_exit
from bots.infrastructure.cache_reader import summarize_exits
from bots.strategies.dummy import DummyStrategyConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.orders import Order
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.trading.strategy import Strategy


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_USDT = _INSTRUMENT.quote_currency
_QTY = _INSTRUMENT.make_qty(Decimal("0.001"))
_STEP_NS = 1_000_000_000

type _Exits = Callable[[OrderFactory, OrderSide], list[Order]]


class _Scripted(Strategy):
    """Enter on the first market update, place the scripted exits on the second."""

    def __init__(self, entry_side: OrderSide | None, exits: _Exits) -> None:
        # Any config carrying `instrument_id` will do: the reader scopes its reads by it.
        super().__init__(
            DummyStrategyConfig(instrument_id=_IID, trade_size=Decimal("0.001"), order_id_tag="001")
        )
        self._entry_side = entry_side
        self._exits = exits
        self._updates = 0

    def on_start(self) -> None:
        self.subscribe_quote_ticks(_IID)
        self.subscribe_trade_ticks(_IID)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self._step()

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._step()

    def _step(self) -> None:
        self._updates += 1
        if self._updates == 1 and self._entry_side is not None:
            self.submit_order(self.order_factory.market(_IID, self._entry_side, _QTY))
        elif self._updates == 2:
            closing = OrderSide.SELL if self._entry_side == OrderSide.BUY else OrderSide.BUY
            for order in self._exits(self.order_factory, closing):
                self.submit_order(order)


def _quotes(*mids: float) -> list[QuoteTick]:
    return [
        TestDataStubs.quote_tick(
            instrument=_INSTRUMENT,
            bid_price=mid - 0.5,
            ask_price=mid + 0.5,
            ts_event=(i + 1) * _STEP_NS,
            ts_init=(i + 1) * _STEP_NS,
        )
        for i, mid in enumerate(mids)
    ]


def _trades(*prices: float) -> list[TradeTick]:
    return [
        TradeTick(
            instrument_id=_IID,
            price=_INSTRUMENT.make_price(price),
            size=_INSTRUMENT.make_qty(1),
            aggressor_side=AggressorSide.BUYER,
            trade_id=TradeId(str(i)),
            ts_event=(i + 1) * _STEP_NS,
            ts_init=(i + 1) * _STEP_NS,
        )
        for i, price in enumerate(prices)
    ]


def _snapshot(
    entry_side: OrderSide | None, exits: _Exits, data: list | None = None
) -> PositionSnapshot:
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    try:
        engine.add_venue(
            venue=_IID.venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=_USDT,
            starting_balances=[Money(10_000, _USDT)],
        )
        engine.add_instrument(_INSTRUMENT)
        engine.add_data(data if data is not None else _quotes(100.5, 100.5, 100.5))
        strategy = _Scripted(entry_side, exits)
        engine.add_strategy(strategy)
        engine.run(streaming=True)
        return StrategyCacheReader(strategy).positions()
    finally:
        # end() stops the (still running) trader before dispose(), which refuses a running one.
        engine.end()
        engine.dispose()


def _price(value: float) -> Price:
    return _INSTRUMENT.make_price(value)


def _stop_market(price: float, *, reduce_only: bool = True, **kwargs: object) -> _Exits:
    return lambda f, side: [
        f.stop_market(_IID, side, _QTY, _price(price), reduce_only=reduce_only, **kwargs)
    ]


def _limits(*prices: float) -> _Exits:
    return lambda f, side: [
        f.limit(_IID, side, _QTY, _price(price), reduce_only=True) for price in prices
    ]


def _bracket(stop: float, limit: float) -> _Exits:
    return lambda f, side: [*_stop_market(stop)(f, side), *_limits(limit)(f, side)]


def test_a_long_with_a_resting_bracket_reports_both_exits() -> None:
    snapshot = _snapshot(OrderSide.BUY, _bracket(stop=90.0, limit=110.0))
    assert snapshot.position_side == "long"
    assert (snapshot.stop_loss, snapshot.take_profit) == ("90.00", "110.00")
    assert (snapshot.stop_loss_orders, snapshot.take_profit_orders, snapshot.open_orders) == (
        1,
        1,
        2,
    )


def test_a_long_reports_its_entry_mark_and_quantity_as_instrument_strings() -> None:
    snapshot = _snapshot(OrderSide.BUY, _bracket(stop=90.0, limit=110.0))
    # The market BUY filled at the ask; the mark is the MID, one digit wider than the price.
    assert snapshot.entry_price == "101.00"
    assert snapshot.mark_price == "100.500"
    assert snapshot.position_qty == "0.001000"


def test_a_short_classifies_with_buy_as_the_closing_side() -> None:
    snapshot = _snapshot(OrderSide.SELL, _bracket(stop=110.0, limit=90.0))
    assert snapshot.position_side == "short"
    assert (snapshot.stop_loss, snapshot.take_profit) == ("110.00", "90.00")
    assert (snapshot.stop_loss_orders, snapshot.take_profit_orders) == (1, 1)


def test_a_stop_limit_is_a_stop_loss_at_its_trigger() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [f.stop_limit(_IID, side, _QTY, _price(89.0), _price(90.0), reduce_only=True)]

    snapshot = _snapshot(OrderSide.BUY, exits)
    assert (snapshot.stop_loss, snapshot.stop_loss_orders) == ("90.00", 1)


def test_a_trailing_stop_market_is_a_stop_loss_at_its_current_trigger() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [
            f.trailing_stop_market(
                _IID, side, _QTY, trailing_offset=Decimal("5.00"), reduce_only=True
            )
        ]

    snapshot = _snapshot(OrderSide.BUY, exits)
    # bid 100.00 - offset 5.00
    assert (snapshot.stop_loss, snapshot.stop_loss_orders) == ("95.00", 1)


def test_a_trailing_stop_limit_is_a_stop_loss_at_its_current_trigger() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [
            f.trailing_stop_limit(
                _IID,
                side,
                _QTY,
                limit_offset=Decimal("1.00"),
                trailing_offset=Decimal("5.00"),
                reduce_only=True,
            )
        ]

    snapshot = _snapshot(OrderSide.BUY, exits)
    assert (snapshot.stop_loss, snapshot.stop_loss_orders) == ("95.00", 1)


def test_a_trailing_stop_reports_the_trigger_after_the_market_moves() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [
            f.trailing_stop_market(
                _IID, side, _QTY, trailing_offset=Decimal("5.00"), reduce_only=True
            )
        ]

    snapshot = _snapshot(OrderSide.BUY, exits, _quotes(100.5, 100.5, 104.5, 108.5))
    # The trail followed the bid up from 100.00 to 108.00.
    assert snapshot.stop_loss == "103.00"


def test_a_trailing_stop_not_yet_activated_is_counted_but_not_priced() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [
            f.trailing_stop_market(
                _IID,
                side,
                _QTY,
                trailing_offset=Decimal("5.00"),
                activation_price=_price(120.0),
                reduce_only=True,
            )
        ]

    snapshot = _snapshot(OrderSide.BUY, exits)
    assert (snapshot.stop_loss, snapshot.stop_loss_orders) == (None, 1)


def test_if_touched_orders_are_take_profits_at_their_trigger() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [
            f.limit_if_touched(_IID, side, _QTY, _price(111.0), _price(110.0), reduce_only=True),
            f.market_if_touched(_IID, side, _QTY, _price(115.0), reduce_only=True),
        ]

    snapshot = _snapshot(OrderSide.BUY, exits)
    assert (snapshot.take_profit, snapshot.take_profit_orders) == ("110.00", 2)
    assert snapshot.stop_loss_orders == 0


def test_an_emulated_stop_counts_as_a_stop_loss() -> None:
    snapshot = _snapshot(OrderSide.BUY, _stop_market(90.0, emulation_trigger=TriggerType.BID_ASK))
    assert (snapshot.stop_loss, snapshot.stop_loss_orders, snapshot.open_orders) == ("90.00", 1, 1)


def test_a_non_reduce_only_stop_is_an_entry_order_not_protection() -> None:
    snapshot = _snapshot(OrderSide.BUY, _stop_market(90.0, reduce_only=False))
    assert (snapshot.stop_loss, snapshot.stop_loss_orders) == (None, 0)
    assert snapshot.open_orders == 1


def test_scaled_take_profits_report_the_nearest_and_count_both() -> None:
    snapshot = _snapshot(OrderSide.BUY, _limits(110.0, 105.0))
    assert (snapshot.take_profit, snapshot.take_profit_orders) == ("105.00", 2)


def test_without_a_mid_the_nearest_exit_is_judged_from_the_entry() -> None:
    # Trades only: the position opens but no quote ever gives the Cache a MID price.
    snapshot = _snapshot(OrderSide.BUY, _limits(130.0, 105.0), _trades(100.0, 100.0, 100.0))
    assert snapshot.mark_price is None
    assert snapshot.entry_price is not None
    assert (snapshot.take_profit, snapshot.take_profit_orders) == ("105.00", 2)


def test_a_flat_bot_reports_no_exits_but_counts_its_open_orders() -> None:
    def exits(f: OrderFactory, side: OrderSide) -> list[Order]:
        return [f.limit(_IID, OrderSide.BUY, _QTY, _price(90.0))]

    snapshot = _snapshot(None, exits)
    assert snapshot.position_side == "flat"
    assert (snapshot.entry_price, snapshot.mark_price, snapshot.position_qty) == (None, None, None)
    assert (snapshot.stop_loss, snapshot.take_profit) == (None, None)
    assert (snapshot.stop_loss_orders, snapshot.take_profit_orders) == (0, 0)
    assert snapshot.open_orders == 1


def _factory() -> OrderFactory:
    return OrderFactory(TraderId("TESTER-001"), StrategyId("S-001"), LiveClock())


def test_a_reduce_only_order_on_the_opening_side_is_not_protection() -> None:
    factory = _factory()
    stop = factory.stop_market(_IID, OrderSide.BUY, _QTY, _price(90.0), reduce_only=True)
    limit = factory.limit(_IID, OrderSide.BUY, _QTY, _price(110.0), reduce_only=True)
    # A long closes with SELL: these BUYs protect nothing.
    assert protective_exit(stop, OrderSide.SELL) is None
    assert protective_exit(limit, OrderSide.SELL) is None
    assert protective_exit(stop, OrderSide.BUY) == (STOP_LOSS, _price(90.0))
    assert protective_exit(limit, OrderSide.BUY) == (TAKE_PROFIT, _price(110.0))


def test_equidistant_exits_are_picked_by_client_order_id() -> None:
    factory = _factory()
    first = factory.limit(_IID, OrderSide.SELL, _QTY, _price(105.0), reduce_only=True)
    second = factory.limit(_IID, OrderSide.SELL, _QTY, _price(96.0), reduce_only=True)
    for orders in ([first, second], [second, first]):
        exits = summarize_exits(orders, OrderSide.SELL, Decimal("100.5"))
        assert exits[TAKE_PROFIT] == ExitSummary(_price(105.0), 2)
