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
`NodeRunner`: the `BacktestRunner` port over Nautilus's `BacktestNode` (Story 27.1, NAUT-03).

One `BacktestNode` per call, one `BacktestRunConfig` per distinct grid point, strategies by
`ImportableStrategyConfig` string path, data through `BacktestDataConfig` bounded by the spec's
window. Results are attributed by `BacktestRunConfig.id`, never list position: `BacktestNode.run()`
returns one result per config it finished, in its own order, and drops a config whose run raised
unless `raise_exception` is set (the Story 2.4 rule, `research/strategies/backtest_dydx.py`).

Reports: `BacktestNode._run` disposes each engine when `dispose_on_completion` is True (the
default), which is why reports read after `run()` came back empty (`backtest_dydx.py`'s
docstring). Here it is False, and each run's state is read from `node.get_engine(config.id)` --
`trader.generate_positions_report()` for the closed trades and
`cache.account_for_venue(Venue(venue)).events` (the account's own event order) for the equity
curve -- before `node.dispose()`.
Known limit: a sweep therefore holds every grid point's engine (its cache and account state, the
data is cleared) until the last run's reports are read; upgrade path: one node per grid point, or
reading each engine's reports from a `BacktestNode` subclass hook right after its own run.

Known limit: `chunk_size` is left None (one-shot loading of each config's window) because the
pinned Nautilus's streaming path cannot read a Python custom data type (`DydxSecondSnapshot` "is not
registered with an Arrow schema containing ts_init"), so each grid point loads its own window;
upgrade path: set `BacktestRunConfig.chunk_size` once the custom type streams through the Rust
backend session.
"""

import logging
import tempfile
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from kernel.catalog_files import query_top_of_book
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_MS
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

import nautilus_trader.analysis as nautilus_analysis
from nautilus_trader.backtest.config import ImportableFeeModelConfig
from nautilus_trader.backtest.config import ImportableFillModelConfig
from nautilus_trader.backtest.config import ImportableLatencyModelConfig
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.execution.config import ImportableExecAlgorithmConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.ports import FEE_MODELS
from research.application.ports import FILL_MODELS
from research.application.ports import LATENCY_KEYS
from research.application.ports import LIQUIDATION_KINDS
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.application.ports import check_params
from research.application.ports import window_ns
from research.application.quotes import derived_quotes
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger


logger = logging.getLogger(__name__)

# The statistics `PortfolioAnalyzer` does not register by default (`portfolio.pyx`), registered on
# every engine's analyzer between `node.build()` and `node.run()`; names of `nautilus_trader.analysis`.
# Known limit: Nautilus before 1.229.0 (the pinned version) lacks the five benchmark-relative ones
# (Alpha, BetaRatio, InformationRatio, TrackingError, TreynorRatio); they are then absent from
# `nautilus_stats` (one warning per run), never faked. Upgrade path: none needed once every
# environment runs the pinned version.
EXTRA_STATISTICS = (
    "CAGR",
    "Alpha",
    "BetaRatio",
    "CalmarRatio",
    "InformationRatio",
    "MaxDrawdown",
    "TrackingError",
    "TreynorRatio",
)


def _instruments(spec: RunSpec) -> list[Instrument]:
    """Instrument definitions (catalog metadata) in `spec.instrument_ids` order; one currency."""
    found = {
        str(i.id): i
        for i in ParquetDataCatalog(spec.catalog_path).instruments(
            instrument_ids=list(spec.instrument_ids)
        )
    }
    missing = [iid for iid in spec.instrument_ids if iid not in found]
    if missing:
        raise ValueError(f"no instrument definition in {spec.catalog_path} for {missing}")
    instruments = [found[iid] for iid in spec.instrument_ids]
    currencies = {str(i.settlement_currency) for i in instruments}
    if len(currencies) != 1:
        # Known limit: one starting balance per run; upgrade path: one balance per currency.
        raise ValueError(f"one settlement currency per run, got {sorted(currencies)}")
    return instruments


def settlement_currency(spec: RunSpec) -> str:
    """Return the one settlement currency of `spec`'s instruments (a fee model's money unit)."""
    return str(_instruments(spec)[0].settlement_currency)


def write_derived_quotes(spec: RunSpec, instruments: list[Instrument], directory: str) -> None:
    """
    Write the `seconds`, `liquidations` and `seconds_liquidations` kinds' quote catalog: each instrument and one `QuoteTick`
    per snapshot top of book in the window (`kernel.catalog_files.query_top_of_book`, level 0 only,
    MEM-01) -- the simulated exchange has no market to fill against otherwise. Written once per
    sweep.
    """
    start_ns, end_ns = window_ns(spec.start, spec.end)
    derived = ParquetDataCatalog(directory)
    for instrument in instruments:
        # Selected on `ts_event` but replayed on `ts_init` (each quote keeps its snapshot's), so
        # widen by the skew bound: every snapshot the node streams by `ts_init` has its quote.
        tops = query_top_of_book(
            spec.catalog_path,
            str(instrument.id),
            max(0, start_ns - MAX_TS_INIT_SKEW_NS),
            end_ns + MAX_TS_INIT_SKEW_NS,
            on_foreign=error_ledger.record,
        )
        if not tops:
            raise ValueError(f"no {instrument.id} snapshots between {spec.start} and {spec.end}")
        derived.write_data([instrument])
        derived.write_data(derived_quotes(instrument, tops))


# A custom data type goes in by its import path: `BacktestDataConfig.data_cls` is typed `str`.
_SNAPSHOT_CLS = f"{DydxSecondSnapshot.__module__}:{DydxSecondSnapshot.__qualname__}"
LIQUIDATION_CLS = f"{Liquidation.__module__}:{Liquidation.__qualname__}"
# The kinds that replay quotes derived from the snapshots' top of book (`write_derived_quotes`).
_QUOTED_KINDS = ("seconds", "liquidations", "seconds_liquidations")
# The kinds that stream the archived snapshots themselves.
_SNAPSHOT_KINDS = ("seconds", "seconds_liquidations")


def _data_configs(spec: RunSpec, quotes_dir: str) -> list[BacktestDataConfig]:
    """
    Build the run's data configs. Their bounds are inclusive and on `ts_init` (the replay clock),
    so `end` is passed as `end_ns - 1` to keep the spec's half-open `[start, end)`. Per id, in
    this order: the derived quotes (`_QUOTED_KINDS`), the snapshots (`_SNAPSHOT_KINDS`), the
    liquidation rows (`LIQUIDATION_KINDS`), else the raw trade archive (`trades`, `bars:<spec>`).
    """
    start_ns, end_ns = window_ns(spec.start, spec.end)
    bounds: dict[str, Any] = {"start_time": start_ns, "end_time": end_ns - 1}
    configs = []
    for iid in map(InstrumentId.from_str, spec.instrument_ids):
        if spec.data in _QUOTED_KINDS:
            configs.append(
                BacktestDataConfig(
                    catalog_path=quotes_dir, data_cls=QuoteTick, instrument_id=iid, **bounds
                )
            )
        if spec.data in _SNAPSHOT_KINDS:
            configs.append(_snapshot_config(spec, iid, bounds))
        if spec.data in LIQUIDATION_KINDS:
            configs.append(_liquidation_config(spec.catalog_path, iid, bounds))
        if spec.data not in _QUOTED_KINDS:
            configs.append(
                BacktestDataConfig(
                    catalog_path=spec.catalog_path, data_cls=TradeTick, instrument_id=iid, **bounds
                )
            )
    return configs


def _snapshot_config(
    spec: RunSpec, iid: InstrumentId, bounds: Mapping[str, Any]
) -> BacktestDataConfig:
    """Return the data config of one id's archived `DydxSecondSnapshot` rows."""
    return BacktestDataConfig(
        catalog_path=spec.catalog_path,
        data_cls=_SNAPSHOT_CLS,
        instrument_id=iid,
        client_id=spec.venue,  # custom type: bookkeeping label only
        **bounds,
    )


def _liquidation_config(
    catalog_path: str, iid: InstrumentId, bounds: Mapping[str, Any]
) -> BacktestDataConfig:
    """
    Return the data config of one id's archived `Liquidation` rows. Its client id is the one a
    strategy subscribes with (`LIQUIDATION_CLIENT_ID`), a label here: the backtest engine publishes
    every row on the custom-data topic of its type and instrument whichever client the command
    names, so the one subscription line serves the backtest and the live bot alike.
    """
    return BacktestDataConfig(
        catalog_path=catalog_path,
        data_cls=LIQUIDATION_CLS,
        instrument_id=iid,
        client_id=LIQUIDATION_CLIENT_ID,
        **bounds,
    )


def _latency_model(spec: RunSpec) -> ImportableLatencyModelConfig | None:
    """
    Return the order latency of `spec`: its `latency` fields when set (omitted ones are 0 -- the shortcut's
    1 s `LatencyModelConfig` default would otherwise leak in), else the fixed `latency_ms` on every
    command; None (no model) for `latency_ms=0`.
    """
    if spec.latency is not None:
        config: dict[str, int] = {key: spec.latency.get(key, 0) for key in LATENCY_KEYS}
    elif spec.latency_ms == 0:
        return None
    else:
        config = {"base_latency_nanos": spec.latency_ms * NS_PER_MS}
    return ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.backtest.config:LatencyModelConfig",
        config=config,
    )


def _fill_model(spec: RunSpec) -> ImportableFillModelConfig | None:
    """Return the importable fill model `spec.fill_model` names; None for the venue default."""
    if spec.fill_model is None:
        return None
    model_path, config_path = FILL_MODELS[str(spec.fill_model["name"])]
    config = {key: value for key, value in spec.fill_model.items() if key != "name"}
    return ImportableFillModelConfig(
        fill_model_path=model_path, config_path=config_path, config=config
    )


def _fee_model(spec: RunSpec) -> ImportableFeeModelConfig | None:
    """Return the importable fee model `spec.fee_model` names; None for the venue default."""
    if spec.fee_model is None:
        return None
    model_path, config_path, _, _ = FEE_MODELS[str(spec.fee_model["name"])]
    config = {key: value for key, value in spec.fee_model.items() if key != "name"}
    return ImportableFeeModelConfig(
        fee_model_path=model_path, config_path=config_path, config=config
    )


def _exec_algorithms(spec: RunSpec) -> list[ImportableExecAlgorithmConfig]:
    """
    One importable config per `spec.exec_algorithms` path: the config class is the algorithm's
    own name plus `Config` in the same module (`twap:TWAPExecAlgorithm` -> `twap:TWAPExecAlgorithmConfig`),
    built with its defaults.
    """
    return [
        ImportableExecAlgorithmConfig(
            exec_algorithm_path=path, config_path=f"{path}Config", config={}
        )
        for path in spec.exec_algorithms
    ]


def build_run_config(
    spec: RunSpec,
    instruments: list[Instrument],
    params: Mapping[str, object],
    quotes_dir: str,
) -> BacktestRunConfig:
    """
    One `BacktestRunConfig`: one strategy per instrument (`order_id_tag` = its position, so the
    strategy ids differ), `params` plus the runner-owned keys, a NETTING/MARGIN venue in the
    settlement currency with the spec's latency, fill model and fee model, its exec algorithms on
    the engine, and the data `spec.data` names, all bounded by the spec's window.
    """
    check_params(params)
    strategies = []
    for tag, iid in enumerate(spec.instrument_ids):
        config: dict[str, object] = {**params, "instrument_id": iid, "order_id_tag": str(tag)}
        if spec.bar_spec is not None:
            config["bar_type"] = f"{iid}-{spec.bar_spec}-LAST-INTERNAL"
        strategies.append(
            ImportableStrategyConfig(
                strategy_path=spec.strategy_path, config_path=spec.config_path, config=config
            )
        )
    currency = str(instruments[0].settlement_currency)
    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            # Rust logger can only be initialised once per process; bypass so re-runs work.
            # Known limit: the engine's own log (order rejections, strategy errors) is therefore
            # suppressed in every run; upgrade path: route the Nautilus log to a file through the
            # process-wide guard (one `LoggingConfig` with `log_directory` initialised once per
            # process, as `research/tests/conftest.py` keeps its guard alive).
            logging=LoggingConfig(bypass_logging=True),
            strategies=strategies,
            exec_algorithms=_exec_algorithms(spec),
        ),
        venues=[
            BacktestVenueConfig(
                name=spec.venue,
                oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN,
                base_currency=currency,
                starting_balances=[f"{spec.starting_balance} {currency}"],
                latency_model=_latency_model(spec),
                fill_model=_fill_model(spec),
                fee_model=_fee_model(spec),
            ),
        ],
        data=_data_configs(spec, quotes_dir),
        raise_exception=True,
        dispose_on_completion=False,
    )


def _money(text: str) -> Money:
    """Parse a Nautilus money string (`"-0.01701600 USDC"`) exactly."""
    return Money.from_str(text)


_POSITION_SIDE = {"BUY": "LONG", "SELL": "SHORT"}


def _closed_trade(row: Any) -> ClosedTrade:  # a positions-report `itertuples()` row
    pnl = _money(str(row.realized_pnl))
    commissions = [_money(str(c)) for c in row.commissions]
    foreign = sorted({str(c.currency) for c in commissions} - {str(pnl.currency)})
    if foreign:
        # Known limit: a commission in another currency than the PnL (e.g. a spot fee in the base
        # asset) is not netted into Nautilus's realized PnL, which then reads gross; upgrade path:
        # convert each commission into the PnL currency at its fill price.
        raise ValueError(
            f"{row.instrument_id}: commissions in {foreign}, realized PnL in {pnl.currency}"
        )
    side = _POSITION_SIDE.get(row.entry)
    if side is None:
        raise ValueError(
            f"{row.instrument_id}: position entry {row.entry!r} is neither BUY nor SELL"
        )
    return ClosedTrade(
        instrument_id=str(row.instrument_id),
        entry_ts=pd.Timestamp(row.ts_opened).value,
        exit_ts=pd.Timestamp(row.ts_closed).value,
        side=side,
        qty=Quantity.from_str(str(row.peak_qty)).as_double(),
        realized_pnl=pnl.as_double(),
        fees=sum((c.as_double() for c in commissions), 0.0),
    )


def ledger_from_positions(report: pd.DataFrame) -> TradeLedger:
    """
    Build the closed trades from `Trader.generate_positions_report()`: each row with `ts_closed` --
    the NETTING snapshots of each earlier round trip (`is_snapshot`) and the final position if it
    closed; a position still open at the end is not a closed trade. `realized_pnl` is Nautilus's
    position PnL (net of its commissions); `fees` sums its `commissions`, which must all be in the
    PnL's currency (else `ValueError`, see the Known limit in `_closed_trade`).
    """
    if report.empty:
        return TradeLedger(())
    return TradeLedger.of(
        _closed_trade(row) for row in report[report["ts_closed"].notna()].itertuples()
    )


def equity_from_account(
    events: Sequence[AccountState], currency: str, starting_balance: float
) -> EquityCurve:
    """
    Equity (the balance `total` in `currency`) after every account state event, in the account's
    own event order (`Account.events`, append-only as the engine applies them -- no report sort is
    involved). Several events at one `ts_event` keep the last applied, so the curve's stamps are
    strictly increasing; an event stamped before its predecessor raises (the account's clock never
    runs backwards).
    """
    ts: list[int] = []
    values: list[float] = []
    for event in events:
        totals = [b.total for b in event.balances if str(b.currency) == currency]
        if len(totals) != 1:
            raise RuntimeError(
                f"account event at {event.ts_event} has no single {currency} balance"
            )
        if ts and event.ts_event < ts[-1]:
            raise RuntimeError(f"account event at {event.ts_event} precedes {ts[-1]}")
        if ts and event.ts_event == ts[-1]:
            values[-1] = totals[0].as_double()
            continue
        ts.append(event.ts_event)
        values.append(totals[0].as_double())
    if not ts:
        raise RuntimeError("the account has no events: the venue's account never existed")
    return EquityCurve(np.array(ts, dtype=np.int64), np.array(values), starting_balance)


_WARNED_MISSING = False


def register_statistics(node: BacktestNode) -> None:
    """Register `EXTRA_STATISTICS` on every built engine's analyzer (before `node.run()`)."""
    global _WARNED_MISSING
    missing = [name for name in EXTRA_STATISTICS if not hasattr(nautilus_analysis, name)]
    if missing and not _WARNED_MISSING:
        _WARNED_MISSING = True  # once per process: a sweep builds many nodes
        logger.warning(
            f"nautilus_trader.analysis has no {missing}: not registered (see EXTRA_STATISTICS)"
        )
    for engine in node.get_engines():
        for name in EXTRA_STATISTICS:
            if name not in missing:
                engine.portfolio.analyzer.register_statistic(getattr(nautilus_analysis, name)())


class NodeRunner:
    """
    `BacktestRunner` over `BacktestNode`.

    Invariant: every returned `RunResult` is the one its `config_id`'s engine produced (matched by
    `BacktestRunConfig.id`), and a config with no result raises `RuntimeError` naming it (DATA-07).
    Holds no state between calls.
    """

    def run(self, spec: RunSpec) -> RunResult:
        return self.sweep(spec, [{}])[0]

    def sweep(self, spec: RunSpec, grid: Sequence[Mapping[str, object]]) -> list[RunResult]:
        """
        One run per grid point (its params merged over `spec.params`), results in grid order, so
        `len(results) == len(grid)`. `BacktestNode` keys configs by their content hash, so two
        identical points (after merging) would silently collapse into one run inside it: they raise
        `ValueError` naming both positions instead.
        """
        if not grid:
            raise ValueError("a sweep needs at least one grid point")
        instruments = _instruments(spec)
        with tempfile.TemporaryDirectory() as quotes_dir:
            if spec.data in _QUOTED_KINDS:
                write_derived_quotes(spec, instruments, quotes_dir)
            planned: dict[str, tuple[BacktestRunConfig, dict[str, object]]] = {}
            positions: dict[str, int] = {}
            for position, point in enumerate(grid):
                params = {**spec.params, **point}
                config = build_run_config(spec, instruments, params, quotes_dir)
                if config.id in planned:
                    raise ValueError(
                        f"grid points {positions[config.id]} and {position} are identical after "
                        f"merging over spec.params ({params}): each point must be a distinct run"
                    )
                planned[config.id] = (config, params)
                positions[config.id] = position
            node = BacktestNode(configs=[config for config, _ in planned.values()])
            try:
                node.build()
                register_statistics(node)
                by_id = {result.run_config_id: result for result in node.run()}
                return [
                    self._result(node, spec, config_id, params, by_id.get(config_id))
                    for config_id, (_, params) in planned.items()
                ]
            finally:
                node.dispose()

    @staticmethod
    def _result(
        node: BacktestNode,
        spec: RunSpec,
        config_id: str,
        params: dict[str, object],
        result: BacktestResult | None,
    ) -> RunResult:
        if result is None:
            raise RuntimeError(f"BacktestNode returned no result for config {config_id} ({params})")
        if result.iterations == 0:
            # An empty window would otherwise read as a strategy that chose not to trade.
            raise RuntimeError(
                f"config {config_id} streamed no {spec.data} data between {spec.start} and "
                f"{spec.end}"
            )
        engine = node.get_engine(config_id)
        if engine is None:
            raise RuntimeError(f"BacktestNode holds no engine for config {config_id}")
        trades = ledger_from_positions(engine.trader.generate_positions_report())
        account = engine.cache.account_for_venue(Venue(spec.venue))
        if account is None:
            raise RuntimeError(f"config {config_id}: no {spec.venue} account in the engine cache")
        balance = float(spec.starting_balance)
        return RunResult(
            config_id=config_id,
            params=params,
            equity=equity_from_account(account.events, str(account.base_currency), balance),
            trades=trades,
            metrics=MetricReport.from_ledger(trades, balance),
            pnl_by_day=trades.pnl_by_day(),
            nautilus_stats={
                "pnls": result.stats_pnls,
                "returns": result.stats_returns,
                "general": engine.portfolio.analyzer.get_performance_stats_general(),
            },
            iterations=result.iterations,
            wall_seconds=_wall_seconds(result),
            orders=engine.trader.generate_orders_report(),
            fills=engine.trader.generate_order_fills_report(),
        )


def _wall_seconds(result: BacktestResult) -> float:
    """Return the engine's own run time (data loading excluded) from the result's stamps."""
    if result.run_started is None or result.run_finished is None:
        raise RuntimeError(f"result {result.run_config_id} carries no run timestamps")
    return (result.run_finished - result.run_started) / 1e9
