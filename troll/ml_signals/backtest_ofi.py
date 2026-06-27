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
Backtest OFIStrategy against locally collected dYdX order book delta data.

Data feeds used:
  - OrderBookDelta  — OFI + book depth features + cancel pressure
  - TradeTick       — 5-min cumulative delta
  - Bar (1-MINUTE)  — EMA trend state (fast=8 min, slow=21 min)

Only 1-MINUTE bars exist in the catalog, so the trend EMA operates on
1-minute bars (trend_ema_fast=8 → 8-min EMA, trend_ema_slow=21 → 21-min EMA).
Swap trend_bar_type_str to "30-MINUTE-LAST-EXTERNAL" when 30-min bars are available.
"""

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def run(
    symbol: str = "BTC-USD-PERP.DYDX",
    catalog_path: str = "troll/dydx_collector/catalog",
    # OFI signal
    ofi_window: int = 20,
    ma_period: int = 10,
    buy_threshold: float = 0.5,
    sell_threshold: float = -0.5,
    # book depth filters
    min_depth_levels: int = 2,
    max_cancel_pressure: float = 0.6,
    min_imbalance_confirm: float | None = None,
    # mid-layer book confirmation
    mid_layer_confirm: float | None = None,
    # 5-min cumulative delta
    cum_delta_seconds: int = 300,
    cum_delta_threshold: float | None = None,
    # trend EMA (on 1-min bars — swap bar type for 30-min when available)
    trend_bar_type_str: str = "1-MINUTE-LAST-EXTERNAL",
    trend_ema_fast: int = 8,
    trend_ema_slow: int = 21,
    # position
    trade_size: str = "0.01",
    exit_on_zero: bool = True,
) -> BacktestNode:
    catalog = ParquetDataCatalog(catalog_path)
    instrument = catalog.instruments(instrument_ids=[symbol])[0]
    venue = instrument.id.venue

    config = BacktestRunConfig(
        engine=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
            strategies=[
                ImportableStrategyConfig(
                    strategy_path="ml_signals.ofi_strategy:OFIStrategy",
                    config_path="ml_signals.ofi_strategy:OFIStrategyConfig",
                    config={
                        "instrument_id": str(instrument.id),
                        "ofi_window": ofi_window,
                        "ma_period": ma_period,
                        "buy_threshold": buy_threshold,
                        "sell_threshold": sell_threshold,
                        "trade_size": Decimal(trade_size),
                        "exit_on_zero": exit_on_zero,
                        "min_depth_levels": min_depth_levels,
                        "max_cancel_pressure": max_cancel_pressure,
                        "min_imbalance_confirm": min_imbalance_confirm,
                        "mid_layer_confirm": mid_layer_confirm,
                        "cum_delta_seconds": cum_delta_seconds,
                        "cum_delta_threshold": cum_delta_threshold,
                        "trend_bar_type_str": trend_bar_type_str,
                        "trend_ema_fast": trend_ema_fast,
                        "trend_ema_slow": trend_ema_slow,
                    },
                ),
            ],
        ),
        venues=[
            BacktestVenueConfig(
                name=str(venue),
                oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN,
                base_currency=str(instrument.settlement_currency),
                starting_balances=[f"10000 {instrument.settlement_currency}"],
                book_type="L2_MBP",
            ),
        ],
        data=[
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=OrderBookDelta,
                instrument_id=instrument.id,
            ),
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=TradeTick,
                instrument_id=instrument.id,
            ),
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=Bar,
                instrument_id=instrument.id,
                bar_spec="1-MINUTE-LAST",
            ),
        ],
    )

    node = BacktestNode(configs=[config])
    node.run()
    return node


if __name__ == "__main__":
    from nautilus_trader.model.identifiers import Venue

    node = run()
    engine = node.get_engines()[0]

    print(f"\nEvents processed: {engine.iteration:,}")
    print("\n--- Account ---")
    print(engine.trader.generate_account_report(Venue("DYDX")))
    print("\n--- Fills ---")
    print(engine.trader.generate_order_fills_report())
    print("\n--- Positions ---")
    print(engine.trader.generate_positions_report())

    engine.reset()
    engine.dispose()
