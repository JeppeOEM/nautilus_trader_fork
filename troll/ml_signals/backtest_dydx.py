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
Backtest LogisticTrendStrategy against locally collected dYdX catalog data, across every
coin in the live Watchlist (or an explicit coin-set) in a single call.

Uses BacktestNode + BacktestDataConfig so the catalog is streamed in chunks
rather than loaded fully into RAM — the catalog grows continuously, so this
stays correct as history accumulates.

The collector never writes Bar objects to the catalog (see catalog_stats.py's
comment), so bars are always aggregated from trade ticks via Nautilus's own
INTERNAL bar aggregation (same pattern as the official crypto examples) --
never an EXTERNAL bar query, which would silently stream nothing.

bar_interval is a plain Nautilus bar-spec string (e.g. "1-SECOND", "1-MINUTE",
"5-MINUTE") -- a config value, no code change, per Story 2.3 AC2.

Multi-coin (Story 2.4 AC1-4): symbols=None (the default) resolves the current coin-set via
ml_signals.watchlist.fetch_watchlist() -- this requires a running dashboard process, so tests
must always pass an explicit symbols list instead (see test_watchlist_multi_coin_backtest.py). Each
symbol gets its own fully independent BacktestRunConfig (own venue/portfolio/strategy instance)
-- no cross-coin state, no aggregation logic, so per-coin results are trivially distinguishable.
A symbol with no matching catalog instrument, or any other per-symbol construction failure, is
skipped (logged, not raised) so one bad/newly-added Watchlist coin can't abort the whole run;
skipped symbols are also summarized in one aggregate warning after the loop, since fetch_watchlist()
callers reading only the returned dict (not logs) would otherwise have no signal that some
requested symbols are silently missing.

BacktestNode keys its configs by `.id` (a deterministic content hash), so duplicate entries in
`symbols` would otherwise silently collapse into a single run inside BacktestNode -- deduplicated
up front here instead, to keep `configs`/`symbol_by_config_id` an honest 1:1 reflection of the
resolved coin-set. Relies on `BacktestRunConfig.dispose_on_completion` defaulting to True so that
a large Watchlist fan-out doesn't accumulate N live BacktestEngine instances in memory; this never
mattered before this story, when there was only ever one config/engine per run.

Returns a dict[str, BacktestResult] keyed by symbol, not node.run()'s bare `list[BacktestResult]`:
querying a BacktestNode's engines for reports *after* run() returns (`node.get_engines()[0]
.trader.generate_order_fills_report()`) was found, empirically, to unreliably report an empty
state even when a real OrderFilled event clearly occurred during the run (confirmed via direct
log inspection) -- `BacktestResult.total_orders`/`total_positions`/`stats_pnls` are the correct,
reliable way to get a run's outcome from BacktestNode in this pinned nautilus_trader version.
Results are matched back to their symbol via each BacktestRunConfig's own `.id` (a deterministic
hash of its content) rather than list position: `BacktestNode.run()` silently drops a config's
result (`raise_exception` defaults to False on `BacktestRunConfig`) if that config's engine build
or run raises internally, so the returned list can be shorter than the input config list -- a
positional zip would then silently mis-attribute every result after the drop.
"""

import logging
import re
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
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals.watchlist import fetch_watchlist

logger = logging.getLogger(__name__)


def _build_run_config(
    symbol: str,
    instrument: Instrument,
    catalog_path: str,
    bar_interval: str,
    buy_threshold: float,
    sell_threshold: float,
) -> BacktestRunConfig:
    venue = instrument.id.venue

    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
            strategies=[
                ImportableStrategyConfig(
                    strategy_path="ml_signals.example_strategy:LogisticTrendStrategy",
                    config_path="ml_signals.example_strategy:LogisticTrendConfig",
                    config={
                        "instrument_id": str(instrument.id),
                        "bar_type": f"{instrument.id}-{bar_interval}-LAST-INTERNAL",
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
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=TradeTick,
                instrument_id=instrument.id,
            ),
        ],
    )


def run(
    symbols: list[str] | None = None,
    catalog_path: str = "troll/dydx_collector/catalog",
    bar_interval: str = "1-MINUTE",
    buy_threshold: float = 0.6,
    sell_threshold: float = 0.4,
) -> dict[str, BacktestResult]:
    if not re.fullmatch(r"\d+-[A-Z]+", bar_interval):
        raise ValueError(
            f"invalid bar_interval {bar_interval!r} -- expected Nautilus bar-spec step-aggregation "
            f'form, e.g. "1-SECOND", "1-MINUTE", "5-MINUTE"',
        )

    if symbols is not None and isinstance(symbols, str):
        raise TypeError(
            f"symbols must be a list[str], not a bare str ({symbols!r}) -- "
            f"iterating a str yields single characters, not instrument IDs",
        )

    if symbols is None:
        try:
            symbols = fetch_watchlist()
        except Exception as exc:
            raise RuntimeError(
                "failed to fetch the live Watchlist -- is the ml_signals dashboard running "
                "(see watchlist.fetch_watchlist's docstring)? Pass an explicit symbols=[...] "
                "list to bypass the live Watchlist entirely.",
            ) from exc

    symbols = list(dict.fromkeys(symbols))  # dedupe, preserve order (see module docstring)

    catalog = ParquetDataCatalog(catalog_path)
    instrument_by_id = {str(i.id): i for i in catalog.instruments(instrument_ids=symbols)}

    symbol_by_config_id: dict[str, str] = {}
    configs: list[BacktestRunConfig] = []
    skipped: list[str] = []
    for symbol in symbols:
        instrument = instrument_by_id.get(symbol)
        if instrument is None:
            skipped.append(symbol)
            continue
        try:
            config = _build_run_config(
                symbol, instrument, catalog_path, bar_interval, buy_threshold, sell_threshold,
            )
        except Exception:
            logger.exception(f"Skipping {symbol}: failed to build its BacktestRunConfig")
            skipped.append(symbol)
            continue
        configs.append(config)
        symbol_by_config_id[config.id] = symbol

    if skipped:
        logger.warning(
            f"Skipped {len(skipped)}/{len(symbols)} symbol(s) with no matching catalog "
            f"instrument in {catalog_path!r}: {skipped}",
        )

    if not configs:
        return {}

    node = BacktestNode(configs=configs)
    results = node.run()
    node.dispose()

    return {symbol_by_config_id[result.run_config_id]: result for result in results}


if __name__ == "__main__":
    results_by_symbol = run()

    for symbol, result in results_by_symbol.items():
        print(f"--- {symbol} ---")
        print(f"Bars processed: {result.iterations}")
        # total_orders/total_positions were found, during verification, to sometimes read 0 even
        # when stats_pnls clearly shows real non-zero trading activity in the same run -- trust
        # stats_pnls/stats_returns for whether trades actually happened, not these two counts.
        print(f"Total orders: {result.total_orders}")
        print(f"Total positions: {result.total_positions}")
        print(f"Stats (PnL): {result.stats_pnls}")
        print(f"Stats (returns): {result.stats_returns}")
