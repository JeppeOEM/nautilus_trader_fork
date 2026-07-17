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
Backtest SnapshotStrategy against locally collected dYdX catalog data, at raw
DydxSecondSnapshot (1s) granularity -- Story 2.3 AC1.

Uses BacktestNode + BacktestDataConfig(data_cls=DydxSecondSnapshot, ...) so the catalog is
streamed in chunks rather than loaded fully into RAM, exactly like backtest_dydx.py's
TradeTick stream -- no full-catalog in-memory load, same BacktestNode/ImportableStrategyConfig
pattern, no custom simulation loop.

Returns node.run()'s own `list[BacktestResult]`, not the BacktestNode itself -- see
backtest_dydx.py's module docstring for why (node.get_engines()[0]'s post-run report
generation was found to unreliably report an empty state even after a real fill occurred).
"""

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from dydx_collector.second_snapshot import DydxSecondSnapshot


def run(
    symbol: str = "BTC-USD-PERP.DYDX",
    catalog_path: str = "troll/dydx_collector/catalog",
    buy_threshold: float = 5.0,
    sell_threshold: float = -5.0,
) -> list[BacktestResult]:
    catalog = ParquetDataCatalog(catalog_path)
    instrument = catalog.instruments(instrument_ids=[symbol])[0]
    venue = instrument.id.venue

    config = BacktestRunConfig(
        engine=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
            strategies=[
                ImportableStrategyConfig(
                    strategy_path="ml_signals.snapshot_strategy:SnapshotStrategy",
                    config_path="ml_signals.snapshot_strategy:SnapshotStrategyConfig",
                    config={
                        "instrument_id": str(instrument.id),
                        "trade_size": Decimal("0.01"),
                        "buy_threshold": buy_threshold,
                        "sell_threshold": sell_threshold,
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
            ),
        ],
        data=[
            # BacktestNode only auto-registers instruments (engine.add_instrument(...)) from
            # BacktestDataConfig entries whose data_cls is a recognized Nautilus built-in type
            # (is_nautilus_class() checks __module__ prefix) -- DydxSecondSnapshot isn't one, so
            # without this TradeTick config the strategy's on_start cache.instrument() lookup
            # would silently find nothing and the strategy would immediately stop itself. This
            # mirrors backtest_ofi.py's own established pattern of multiple BacktestDataConfigs
            # in one run; the TradeTick stream itself is unused by SnapshotStrategy.
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=TradeTick,
                instrument_id=instrument.id,
            ),
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=DydxSecondSnapshot,
                instrument_id=instrument.id,
                # DydxSecondSnapshot isn't a recognized Nautilus built-in type either, so
                # BacktestNode requires an explicit client_id here or it raises "Data type ...
                # not setup for loading into BacktestEngine" -- the data itself carries
                # instrument_id, so this is a bookkeeping label only, not a real live data client.
                client_id=str(venue),
            ),
        ],
    )

    node = BacktestNode(configs=[config])
    results = node.run()
    node.dispose()
    return results


if __name__ == "__main__":
    result = run()[0]

    # "Total events" not "snapshots processed" -- this counts the merged TradeTick +
    # DydxSecondSnapshot stream, not snapshots alone (the parallel TradeTick config exists
    # only for instrument registration and exchange fills, see the data= comment above).
    print(f"Total events processed: {result.iterations}")
    # total_orders/total_positions were found, during verification, to sometimes read 0 even
    # when stats_pnls clearly shows real non-zero trading activity in the same run -- trust
    # stats_pnls/stats_returns for whether trades actually happened, not these two counts.
    print(f"Total orders: {result.total_orders}")
    print(f"Total positions: {result.total_positions}")
    print(f"Stats (PnL): {result.stats_pnls}")
    print(f"Stats (returns): {result.stats_returns}")
