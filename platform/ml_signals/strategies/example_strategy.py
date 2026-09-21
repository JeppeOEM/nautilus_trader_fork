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
Example strategy using OnlineLogisticTrend as a buy/sell signal.

A second strategy reuses the signal the same way: instantiate its own
OnlineLogisticTrend in `on_start` and register it for its own bar type. No
shared registry needed — each strategy owns its own indicator instance.
"""

from decimal import Decimal

from ml_signals.indicators import OnlineLogisticTrend
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy


class LogisticTrendConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``LogisticTrendStrategy`` instances.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument ID for the strategy.
    bar_type : BarType
        The bar type for the strategy.
    trade_size : Decimal
        The position size per trade.
    lookback : int, default 5
        The `OnlineLogisticTrend` lookback period.
    buy_threshold : float, default 0.6
        Enter long when the signal value exceeds this.
    sell_threshold : float, default 0.4
        Enter short when the signal value drops below this.

    """

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    lookback: int = 5
    buy_threshold: float = 0.6
    sell_threshold: float = 0.4


class LogisticTrendStrategy(Strategy):
    """
    Trades on the `OnlineLogisticTrend` signal, publishing it for any
    subscriber (dashboard, other actors) via `publish_signal`.
    """

    def __init__(self, config: LogisticTrendConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.trend = OnlineLogisticTrend(lookback=config.lookback)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.config.bar_type, self.trend)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.trend.initialized:
            return

        self.publish_signal(name="logistic_trend", value=self.trend.value, ts_event=bar.ts_event)

        if self.trend.value > self.config.buy_threshold and self.portfolio.is_flat(
            self.config.instrument_id,
        ):
            self._submit(OrderSide.BUY)
        elif self.trend.value < self.config.sell_threshold and self.portfolio.is_flat(
            self.config.instrument_id,
        ):
            self._submit(OrderSide.SELL)

    def _submit(self, side: OrderSide) -> None:
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
