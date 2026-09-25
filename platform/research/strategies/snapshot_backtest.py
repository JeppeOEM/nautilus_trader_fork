"""
Backtest any strategy that consumes DydxSecondSnapshot, with orders that actually fill.

The collector catalog holds no QuoteTick/TradeTick/book data, and the simulated exchange
rejects every order with "no market for <instrument>" when it has none. So each run derives
top-of-book QuoteTicks from the requested snapshot window into a throwaway catalog and feeds
them to the engine alongside the snapshots. Fills happen at the snapshot's best bid/ask.
"""

import tempfile
from typing import Any

from kernel.catalog_files import TopOfBook
from kernel.catalog_files import query_top_of_book
from kernel.second_snapshot import DydxSecondSnapshot

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
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def _quotes(instrument: Instrument, tops: list[TopOfBook]) -> list[QuoteTick]:
    """
    One QuoteTick per top of book, ordered by `ts_init`.

    `query_top_of_book` sorts by `ts_event`, but `ParquetDataCatalog.write_data` refuses rows
    whose `ts_init` decreases, and a writer's `ts_init` may trail `ts_event` by up to
    `kernel.clocks.MAX_TS_INIT_SKEW_NS`, so the two orders can disagree. `ts_init` is also the
    clock `BacktestNode` replays on, as `catalog.query` ordered them before.
    """
    return [
        QuoteTick(
            instrument_id=instrument.id,
            bid_price=instrument.make_price(t.bid_price),
            ask_price=instrument.make_price(t.ask_price),
            bid_size=instrument.make_qty(t.bid_size),
            ask_size=instrument.make_qty(t.ask_size),
            ts_event=t.ts_event,
            ts_init=t.ts_init,
        )
        for t in sorted(tops, key=lambda t: t.ts_init)
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
    # Level 0 only, never the decoded 20-level book (MEM-01). A row with an empty side is already
    # omitted: it has no top of book to quote.
    # Parsed exactly as `ParquetDataCatalog.query` parses a window (naive = UTC, aware converted).
    tops = query_top_of_book(
        catalog_path,
        symbol,
        dt_to_unix_nanos(time_object_to_dt(start)),
        dt_to_unix_nanos(time_object_to_dt(end)),
    )
    if not tops:
        raise ValueError(f"No {symbol} snapshots between {start} and {end}")

    with tempfile.TemporaryDirectory() as tmp:
        derived = ParquetDataCatalog(tmp)
        derived.write_data([instrument])
        derived.write_data(_quotes(instrument, tops))

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
