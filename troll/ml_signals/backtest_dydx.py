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
Backtest LogisticTrendStrategy against locally collected dYdX catalog data.

The collector's recorded 1-MINUTE bars are too sparse this early on to warm up
the indicator's lookback window, so this aggregates tick bars from trade
ticks instead (same INTERNAL aggregation pattern nautilus_trader's own crypto
examples use) to get enough bars from whatever history is in the catalog.
"""

from decimal import Decimal

from ml_signals.example_strategy import LogisticTrendConfig
from ml_signals.example_strategy import LogisticTrendStrategy
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def run(
    symbol: str = "BTC-USD-PERP.DYDX",
    catalog_path: str = "troll/dydx_collector/catalog",
    tick_bar_size: int = 20,
    buy_threshold: float = 0.6,
    sell_threshold: float = 0.4,
) -> BacktestEngine:
    catalog = ParquetDataCatalog(catalog_path)
    instrument = catalog.instruments(instrument_ids=[symbol])[0]
    trades = sorted(catalog.trade_ticks(instrument_ids=[symbol]), key=lambda t: t.ts_init)

    venue = instrument.id.venue
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=instrument.settlement_currency,
        starting_balances=[Money(10_000, instrument.settlement_currency)],
    )
    engine.add_instrument(instrument)
    engine.add_data(trades)

    strategy = LogisticTrendStrategy(
        LogisticTrendConfig(
            instrument_id=instrument.id,
            bar_type=BarType.from_str(f"{instrument.id}-{tick_bar_size}-TICK-LAST-INTERNAL"),
            trade_size=Decimal("0.01"),
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        ),
    )
    engine.add_strategy(strategy=strategy)

    engine.run()
    return engine


if __name__ == "__main__":
    backtest_engine = run()

    print(f"Bars processed: {backtest_engine.iteration}")
    print(backtest_engine.trader.generate_account_report(Venue("DYDX")))
    print(backtest_engine.trader.generate_order_fills_report())
    print(backtest_engine.trader.generate_positions_report())

    backtest_engine.reset()
    backtest_engine.dispose()
