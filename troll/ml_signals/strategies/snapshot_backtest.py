"""
Backtest any strategy that consumes DydxSecondSnapshot, with orders that actually fill.

The collector catalog holds no QuoteTick/TradeTick/book data, and the simulated exchange
rejects every order with "no market for <instrument>" when it has none. So each run derives
top-of-book QuoteTicks from the requested snapshot window into a throwaway catalog and feeds
them to the engine alongside the snapshots. Fills happen at the snapshot's best bid/ask.
"""

import tempfile
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from dydx_collector.second_snapshot import DydxSecondSnapshot


def _quotes(instrument: Instrument, snapshots: list[DydxSecondSnapshot]) -> list[QuoteTick]:
    return [
        QuoteTick(
            instrument_id=instrument.id,
            bid_price=instrument.make_price(s.bid_prices[0]),
            ask_price=instrument.make_price(s.ask_prices[0]),
            bid_size=instrument.make_qty(s.bid_sizes[0]),
            ask_size=instrument.make_qty(s.ask_sizes[0]),
            ts_event=s.ts_event,
            ts_init=s.ts_init,
        )
        for s in snapshots
        if s.bid_prices and s.ask_prices  # an empty side has no top of book to quote
    ]


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
    instrument = catalog.instruments(instrument_ids=[symbol])[0]
    venue = str(instrument.id.venue)
    snapshots = [  # query() wraps custom types in CustomData
        c.data
        for c in catalog.query(DydxSecondSnapshot, identifiers=[symbol], start=start, end=end)
    ]
    if not snapshots:
        raise ValueError(f"No {symbol} snapshots between {start} and {end}")

    with tempfile.TemporaryDirectory() as tmp:
        derived = ParquetDataCatalog(tmp)
        derived.write_data([instrument])
        derived.write_data(_quotes(instrument, snapshots))

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
