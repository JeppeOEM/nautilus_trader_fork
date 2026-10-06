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
Backtest any strategy that consumes DydxSecondSnapshot, with orders that actually fill.

The collector catalog holds no QuoteTick/TradeTick/book data, and the simulated exchange
rejects every order with "no market for <instrument>" when it has none. So each run derives
top-of-book QuoteTicks from the requested snapshot window into a throwaway catalog and feeds
them to the engine alongside the snapshots. Fills happen at the snapshot's best bid/ask.
"""

import tempfile
from typing import Any

from kernel.catalog_files import query_top_of_book
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.core.datetime import time_object_to_dt
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.quotes import derived_quotes


def run(
    catalog_path: str,
    symbol: str,
    start: str,
    end: str,
    strategy_path: str,
    config_path: str,
    params: dict[str, Any],
    starting_balance: int = 10_000,
) -> BacktestResult:
    """`start`/`end` are required: the window is materialised in memory (MEM-01)."""
    catalog = ParquetDataCatalog(catalog_path)
    instruments = catalog.instruments(instrument_ids=[symbol])
    if not instruments:
        # As `backtest_snapshot.run`: a bare IndexError named neither the symbol nor the catalog.
        raise ValueError(
            f"instrument {symbol!r} not found in the catalog at {catalog_path!r} -- "
            "check catalog_path/CATALOG_PATH and that the collector has written its definition",
        )
    instrument = instruments[0]
    venue = str(instrument.id.venue)
    # Level 0 only, never the decoded 20-level book (MEM-01). A row with an empty side is already
    # omitted: it has no top of book to quote.
    # Parsed exactly as `ParquetDataCatalog.query` parses a window (naive = UTC, aware converted).
    tops = query_top_of_book(
        catalog_path,
        symbol,
        dt_to_unix_nanos(time_object_to_dt(start)),
        dt_to_unix_nanos(time_object_to_dt(end)),
        on_foreign=error_ledger.record,
    )
    if not tops:
        raise ValueError(f"No {symbol} snapshots between {start} and {end}")

    with tempfile.TemporaryDirectory() as tmp:
        derived = ParquetDataCatalog(tmp)
        derived.write_data([instrument])
        derived.write_data(derived_quotes(instrument, tops))

        config = BacktestRunConfig(
            engine=BacktestEngineConfig(
                # Rust logger can only be initialised once per process; bypass so re-runs work.
                logging=LoggingConfig(bypass_logging=True),
                strategies=[
                    ImportableStrategyConfig(
                        strategy_path=strategy_path,
                        config_path=config_path,
                        config={"instrument_id": str(instrument.id), **params},
                    ),
                ],
            ),
            venues=[
                BacktestVenueConfig(
                    name=venue,
                    oms_type=OmsType.NETTING,
                    account_type=AccountType.MARGIN,
                    base_currency=str(instrument.settlement_currency),
                    starting_balances=[f"{starting_balance} {instrument.settlement_currency}"],
                ),
            ],
            data=[
                BacktestDataConfig(
                    catalog_path=tmp,
                    data_cls=QuoteTick,
                    instrument_id=instrument.id,
                ),
                BacktestDataConfig(
                    catalog_path=catalog_path,
                    data_cls=DydxSecondSnapshot,
                    instrument_id=instrument.id,
                    client_id=venue,  # custom type: bookkeeping label only
                    start_time=start,
                    end_time=end,
                ),
            ],
        )
        node = BacktestNode(configs=[config])
        result = node.run()[0]
        node.dispose()
    return result
