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
Minimal strategy proving raw DydxSecondSnapshot data (HFT/1s granularity, Story 2.3 AC1) is
backtestable via BacktestNode. Feeds MultiLevelOFI directly from the snapshot's top-of-book
lists -- no OrderBook reconstruction needed, since DydxSecondSnapshot already carries the
top-20 bid/ask levels per second -- and enters/exits on an order-flow-imbalance threshold
cross. Deliberately minimal: this story is about backtest infrastructure, not signal quality
(mirrors example_strategy.py/ofi_strategy.py's own minimal-but-real scope).
"""

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.indicators import MultiLevelOFI


class SnapshotStrategyConfig(StrategyConfig, frozen=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    trade_size : Decimal
    ofi_levels : int
        Book depth (number of levels) MultiLevelOFI aggregates over.
    ofi_window : int
        Number of trailing snapshot-to-snapshot contributions summed into OFI.
    buy_threshold : float
        MultiLevelOFI level that triggers a long entry (or a short exit). Scale is
        instrument- and price-dependent (raw base-asset-unit contributions summed across
        ofi_levels/ofi_window, not USD-notional) -- the 5.0/-5.0 defaults are BTC-shaped and
        need retuning for any other symbol.
    sell_threshold : float
        MultiLevelOFI level that triggers a short entry (or a long exit). Must be < buy_threshold.
    """

    instrument_id: InstrumentId
    trade_size: Decimal
    ofi_levels: int = 10
    ofi_window: int = 20
    buy_threshold: float = 5.0
    sell_threshold: float = -5.0


class SnapshotStrategy(Strategy):
    """Enters/exits on a MultiLevelOFI threshold cross, computed from raw DydxSecondSnapshot data."""

    def __init__(self, config: SnapshotStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self._ofi = MultiLevelOFI(levels=config.ofi_levels, window=config.ofi_window)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return

        if not self.config.buy_threshold > self.config.sell_threshold:
            self.log.error(
                f"buy_threshold ({self.config.buy_threshold}) must exceed sell_threshold "
                f"({self.config.sell_threshold}) -- misconfigured thresholds would silently "
                f"disable entries/exits"
            )
            self.stop()
            return

        self.subscribe_data(DataType(DydxSecondSnapshot), instrument_id=self.config.instrument_id)
        self.log.info(
            f"SnapshotStrategy started — ofi_levels={self.config.ofi_levels} "
            f"ofi_window={self.config.ofi_window}"
        )

    def on_data(self, data: Data) -> None:
        if not isinstance(data, DydxSecondSnapshot):
            return

        self._ofi.update_raw(data.bid_prices, data.bid_sizes, data.ask_prices, data.ask_sizes)
        if not self._ofi.initialized:
            return

        is_flat = self.portfolio.is_flat(self.config.instrument_id)
        is_long = self.portfolio.is_net_long(self.config.instrument_id)
        is_short = self.portfolio.is_net_short(self.config.instrument_id)

        if is_flat:
            if self._ofi.value > self.config.buy_threshold:
                self._submit(OrderSide.BUY)
            elif self._ofi.value < self.config.sell_threshold:
                self._submit(OrderSide.SELL)
        elif is_long and self._ofi.value < self.config.sell_threshold:
            self._submit(OrderSide.SELL)
        elif is_short and self._ofi.value > self.config.buy_threshold:
            self._submit(OrderSide.BUY)

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
