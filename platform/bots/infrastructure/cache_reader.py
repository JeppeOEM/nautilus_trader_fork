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
The Nautilus anti-corruption layer's read side: one bot's view of its own strategy.

`StrategyCacheReader` is the `BotRuntime` port over a live `Strategy` -- the only way supervision
and history see Nautilus. Every figure is scoped to the strategy's own `strategy_id`: the
Portfolio's `net_exposure`/`realized_pnl`/`unrealized_pnl` are account+instrument scoped in the
Rust core (`cache.positions_open(strategy_id=None, ...)`), so under one node hosting many bots they
would silently blend two bots the moment they share an instrument (AD-11).
"""

from collections.abc import Callable

from bots.application.ports import PositionSnapshot
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy


class StrategyCacheReader:
    """
    `BotRuntime` over one strategy. Invariant: reads `cache.positions_open/closed` filtered by
    this strategy's id only, and hands `on_fill` handlers only this strategy's `OrderFilled`s.
    """

    def __init__(self, strategy: Strategy) -> None:
        self._strategy = strategy

    @property
    def strategy_name(self) -> str:
        return type(self._strategy).__name__

    @property
    def symbol(self) -> str:
        return str(self._strategy.config.instrument_id)

    @property
    def is_running(self) -> bool:
        return self._strategy.is_running

    @property
    def last_data_ns(self) -> int:
        # The ts_event of the strategy's last market data, set from a market-data callback that
        # keeps firing whether or not the strategy runs (see `DummyStrategy`; every hosted
        # strategy keeps one, e.g. research's `CandlePatternStrategy`).
        return self._strategy.last_data_ns

    def start(self) -> None:
        self._strategy.start()

    def stop(self) -> None:
        self._strategy.stop()

    def positions(self) -> PositionSnapshot:
        strategy = self._strategy
        instrument_id = strategy.config.instrument_id
        positions_open = strategy.cache.positions_open(
            instrument_id=instrument_id, strategy_id=strategy.id
        )
        positions_closed = strategy.cache.positions_closed(
            instrument_id=instrument_id, strategy_id=strategy.id
        )
        position_side = "flat"
        net_exposure = 0.0
        unrealized_pnl = 0.0
        if positions_open:
            # NETTING (the only OMS type here): at most one open position per strategy+instrument.
            position = positions_open[0]
            position_side = "long" if position.is_long else "short" if position.is_short else "flat"
            price = strategy.cache.price(instrument_id, PriceType.MID)
            if price is not None:
                # notional_value() (not signed_qty * price) matches the Portfolio's own
                # net_exposure (crates/portfolio/src/portfolio.rs), which scales by the
                # instrument's multiplier.
                sign = 1.0 if position.is_long else -1.0
                net_exposure = sign * position.notional_value(price).as_double()
                unrealized_pnl = position.unrealized_pnl(price).as_double()
        realized_pnl = sum(
            position.realized_pnl.as_double()
            for position in (*positions_open, *positions_closed)
            if position.realized_pnl is not None
        )
        return PositionSnapshot(position_side, net_exposure, realized_pnl, unrealized_pnl)

    def on_fill(self, handler: Callable[[OrderFilled, Position | None], None]) -> None:
        """
        Subscribe to this strategy's order-event topic (`events.order.{strategy_id}`, published by
        the ExecutionEngine for every order event) and hand over each fill with its position.
        """
        strategy = self._strategy

        def _on_order_event(event: object) -> None:
            if not isinstance(event, OrderFilled):
                return
            position = (
                strategy.cache.position(event.position_id)
                if event.position_id is not None
                else None
            )
            handler(event, position)

        strategy.msgbus.subscribe(topic=f"events.order.{strategy.id}", handler=_on_order_event)
