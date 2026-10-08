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
The research ports (Story 27.1): how a notebook reads market frames, reads ranking history and runs
a backtest, as `typing.Protocol`s, plus the `RunSpec`/`RunResult` values a backtest run exchanges.

Implementations: `frames.CatalogFrames`, `ranking_history.HttpRankingHistory`,
`backtest_runner.NodeRunner`. A notebook constructs the one it needs and calls only these methods.
"""

import re
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from types import MappingProxyType
from typing import Protocol

import pandas as pd
from kernel.liquidation import has_liquidation_feed
from kernel.venues import venue_of

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.core.datetime import time_object_to_dt
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import TradeLedger


# `bars:<step>-<aggregation>`, a Nautilus bar spec without the price type (`bars:1-MINUTE`).
_BARS_DATA = re.compile(r"bars:[1-9][0-9]*-(MILLISECOND|SECOND|MINUTE|HOUR|DAY|WEEK|MONTH)")
# The tick data kinds (`RunSpec.data`), besides `bars:<spec>`. `seconds_liquidations` (Story
# 33.13) is `seconds` plus the archived liquidation rows, for a snapshot strategy that also reads
# the forced flow (`OFIStrategy`'s `forced_flow_filter` and `liquidation_cascade_mode`).
DATA_KINDS = ("seconds", "trades", "liquidations", "seconds_liquidations")
# The kinds that stream the archived `Liquidation` rows: their ids need a liquidation feed.
LIQUIDATION_KINDS = ("liquidations", "seconds_liquidations")
# Keys the runner sets on every strategy config itself.
RESERVED_PARAMS = frozenset({"instrument_id", "order_id_tag", "bar_type"})
# The fixed time from a strategy's decision to the simulated exchange receiving the command, for
# every order command (submit, modify, cancel) of a run. 300 ms is an order sent from a box outside
# the venue's cloud region (operator's choice, 2026-09-29). Market-data delay is not part of it: the
# replay already runs on `ts_init`, the receive clock.
# Known limit: one rigid value, no jitter, the same for every venue; upgrade path: set it from the
# VPS-measured order round trip per venue, and for jitter a `LatencyModel` drawing from that
# measured distribution.
DEFAULT_LATENCY_MS = 300

_BACKTEST_MODELS = "nautilus_trader.backtest.models"
_FILL_CONFIG = "nautilus_trader.backtest.config:FillModelConfig"
# Every fill model's name -> (class path, config class path). The three models whose own `__init__`
# takes no `config` (`FillModelFactory` would raise `TypeError`) go through the thin subclasses of
# `research.application.fill_adapters`, which accept the same `FillModelConfig`.
FILL_MODELS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "fill": (f"{_BACKTEST_MODELS}:FillModel", _FILL_CONFIG),
        "best_price": (f"{_BACKTEST_MODELS}:BestPriceFillModel", _FILL_CONFIG),
        "one_tick_slippage": (f"{_BACKTEST_MODELS}:OneTickSlippageFillModel", _FILL_CONFIG),
        "two_tier": (f"{_BACKTEST_MODELS}:TwoTierFillModel", _FILL_CONFIG),
        "three_tier": (f"{_BACKTEST_MODELS}:ThreeTierFillModel", _FILL_CONFIG),
        "probabilistic": (f"{_BACKTEST_MODELS}:ProbabilisticFillModel", _FILL_CONFIG),
        "size_aware": (f"{_BACKTEST_MODELS}:SizeAwareFillModel", _FILL_CONFIG),
        "limit_order_partial": (f"{_BACKTEST_MODELS}:LimitOrderPartialFillModel", _FILL_CONFIG),
        "market_hours": ("research.application.fill_adapters:MarketHoursFillModel", _FILL_CONFIG),
        "volume_sensitive": (
            "research.application.fill_adapters:VolumeSensitiveFillModel",
            _FILL_CONFIG,
        ),
        "competition_aware": (
            "research.application.fill_adapters:CompetitionAwareFillModel",
            _FILL_CONFIG,
        ),
    }
)
FILL_KEYS = frozenset({"prob_fill_on_limit", "prob_slippage", "random_seed"})
_FEE_CONFIGS = "nautilus_trader.backtest.config"
# Every fee model's name -> (class path, config class path, allowed keys, required keys).
FEE_MODELS: Mapping[str, tuple[str, str, frozenset[str], frozenset[str]]] = MappingProxyType(
    {
        "maker_taker": (
            f"{_BACKTEST_MODELS}:MakerTakerFeeModel",
            f"{_FEE_CONFIGS}:MakerTakerFeeModelConfig",
            frozenset(),
            frozenset(),
        ),
        "fixed": (
            f"{_BACKTEST_MODELS}:FixedFeeModel",
            f"{_FEE_CONFIGS}:FixedFeeModelConfig",
            frozenset({"commission", "charge_commission_once"}),
            frozenset({"commission"}),
        ),
        "per_contract": (
            f"{_BACKTEST_MODELS}:PerContractFeeModel",
            f"{_FEE_CONFIGS}:PerContractFeeModelConfig",
            frozenset({"commission"}),
            frozenset({"commission"}),
        ),
    }
)
LATENCY_KEYS = (
    "base_latency_nanos",
    "insert_latency_nanos",
    "update_latency_nanos",
    "cancel_latency_nanos",
)


def window_ns(start: str | int, end: str | int) -> tuple[int, int]:
    """Parse a window exactly as `ParquetDataCatalog.query` does (naive = UTC, int = ns)."""
    start_ns = dt_to_unix_nanos(time_object_to_dt(start))
    end_ns = dt_to_unix_nanos(time_object_to_dt(end))
    if end_ns <= start_ns:
        raise ValueError(f"end {end!r} must be after start {start!r}")
    return start_ns, end_ns


@dataclass(frozen=True)
class RunSpec:
    """
    One backtest: which strategy, on which instruments, over which window, fed which data.

    Invariant: at least one instrument, no id twice, all on one venue (Known limit: one simulated venue per
    run -- upgrade path: one `BacktestVenueConfig` per venue with its own starting balance); a
    `data` kind of `"seconds"` (`DydxSecondSnapshot` + quotes derived from their top of book),
    `"trades"` (`TradeTick`), `"liquidations"` (the `seconds` kind's derived quotes plus the
    archived `kernel.liquidation.Liquidation` rows, only for ids with `has_liquidation_feed`, Story
    33.14), `"seconds_liquidations"` (`seconds` plus those rows, same ids, Story 33.13) or
    `"bars:<step>-<aggregation>"` (`TradeTick` aggregated by Nautilus into
    `<iid>-<step>-<aggregation>-LAST-INTERNAL` bars, injected as the strategy's `bar_type`);
    a positive int starting balance; a non-negative int `latency_ms`; a window whose end is after
    its start; `params` never sets a key the runner owns (`RESERVED_PARAMS`).
    Execution models (all optional, None = the venue's defaults): `fill_model`
    (`{"name": <FILL_MODELS key>, **prob_fill_on_limit/prob_slippage/random_seed}`), `fee_model`
    (`{"name": <FEE_MODELS key>, **that model's keys}`), `latency` (any of `LATENCY_KEYS`, non-
    negative ints in ns; replaces the `latency_ms` shortcut) and `exec_algorithms`
    (`"module:Class"` paths of `ExecAlgorithm`s, each with the `<Class>Config` beside it); a name,
    key or value outside those tables raises at construction, naming the valid ones.
    `latency_ms` delays every order command by that fixed time (Nautilus's `LatencyModel`), so an
    order fills against the market as it is when the command arrives, not as the strategy saw it;
    0 runs without a latency model (the fill is at the very quote the decision was made on). On the
    `"seconds"` kind the market moves only once a second, so any value from 1 to 1000 fills at the
    next second's top of book (the simulated exchange applies a timestamp's quote before it
    releases the commands due by then).
    `start`/`end` bound every read (MEM-01) and follow Nautilus's parsing (ISO string, naive = UTC,
    or int ns). The runner bounds the replay on `ts_init` (the clock a backtest replays on,
    `BacktestDataConfig`), while `MarketFrames` windows on `ts_event`, so a frame read over the
    same `start`/`end` can differ from the replayed rows at each edge by up to
    `kernel.clocks.MAX_TS_INIT_SKEW_NS`. Violated at construction -- `__post_init__` raises `ValueError`. `params` is
    stored as a read-only mapping.
    """

    catalog_path: str
    instrument_ids: tuple[str, ...]
    start: str | int
    end: str | int
    strategy_path: str
    config_path: str
    params: Mapping[str, object] = field(default_factory=dict)
    starting_balance: int = 10_000
    data: str = "seconds"
    latency_ms: int = DEFAULT_LATENCY_MS
    fill_model: Mapping[str, object] | None = None
    fee_model: Mapping[str, object] | None = None
    latency: Mapping[str, int] | None = None
    exec_algorithms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.instrument_ids, str):
            raise ValueError(
                f"instrument_ids must be a tuple of ids, not the str {self.instrument_ids!r}"
            )
        if not self.instrument_ids:
            raise ValueError("a run needs at least one instrument")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError(f"an instrument is listed twice in {self.instrument_ids}")
        venues = {venue_of(iid) for iid in self.instrument_ids}
        if len(venues) != 1:
            raise ValueError(
                f"one venue per run (Known limit), got {sorted(venues)} for {self.instrument_ids}"
            )
        _check_data(self.data, self.instrument_ids)
        balance = self.starting_balance
        if isinstance(balance, bool) or not isinstance(balance, int) or balance <= 0:
            raise ValueError(f"starting_balance must be a positive int, got {balance!r}")
        latency = self.latency_ms
        if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
            raise ValueError(f"latency_ms must be a non-negative int, got {latency!r}")
        window_ns(self.start, self.end)
        check_params(self.params)
        check_execution_models(self)
        object.__setattr__(self, "instrument_ids", tuple(self.instrument_ids))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        for name in ("fill_model", "fee_model", "latency"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))
        object.__setattr__(self, "exec_algorithms", tuple(self.exec_algorithms))

    @property
    def venue(self) -> str:
        return venue_of(self.instrument_ids[0])

    @property
    def bar_spec(self) -> str | None:
        """`1-MINUTE` for `data="bars:1-MINUTE"`, None for the tick kinds."""
        return self.data.removeprefix("bars:") if self.data.startswith("bars:") else None


def _check_data(data: str, instrument_ids: Sequence[str]) -> None:
    """
    Refuse an unknown data kind, and a `LIQUIDATION_KINDS` run on an id with no liquidation feed.
    """
    if data not in DATA_KINDS and not _BARS_DATA.fullmatch(data):
        raise ValueError(
            f"data must be one of {DATA_KINDS} or 'bars:<step>-<aggregation>', got {data!r}"
        )
    if data not in LIQUIDATION_KINDS:
        return
    without = [iid for iid in instrument_ids if not has_liquidation_feed(iid)]
    if without:
        raise ValueError(
            f"data={data!r} needs ids with a liquidation feed (Bybit LINEAR), got {without}"
        )


def check_params(params: Mapping[str, object]) -> None:
    reserved = RESERVED_PARAMS & set(params)
    if reserved:
        raise ValueError(f"params may not set {sorted(reserved)}: the runner sets them")


def _model_name(field_name: str, model: Mapping[str, object], table: Mapping[str, object]) -> str:
    name = model.get("name")
    if not isinstance(name, str) or name not in table:
        raise ValueError(f"{field_name}.name must be one of {sorted(table)}, got {name!r}")
    return name


def _check_keys(
    field_name: str, model: Mapping[str, object], allowed: frozenset[str], required: frozenset[str]
) -> None:
    unknown = set(model) - allowed - {"name"}
    if unknown:
        raise ValueError(f"{field_name}: unknown keys {sorted(unknown)}; valid: {sorted(allowed)}")
    missing = required - set(model)
    if missing:
        raise ValueError(f"{field_name}: missing required keys {sorted(missing)}")


def _check_fill_model(model: Mapping[str, object]) -> None:
    _model_name("fill_model", model, FILL_MODELS)
    _check_keys("fill_model", model, FILL_KEYS, frozenset())
    for key in ("prob_fill_on_limit", "prob_slippage"):
        value = model.get(key, 0.0)
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ValueError(f"fill_model.{key} must be a number in [0, 1], got {value!r}")
    seed = model.get("random_seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError(f"fill_model.random_seed must be an int or None, got {seed!r}")


def _check_fee_model(model: Mapping[str, object]) -> None:
    name = _model_name("fee_model", model, FEE_MODELS)
    _, _, allowed, required = FEE_MODELS[name]
    _check_keys("fee_model", model, allowed, required)
    commission = model.get("commission")
    if commission is not None and not (
        isinstance(commission, str) and len(commission.split()) == 2
    ):
        raise ValueError(
            f"fee_model.commission must be a money string like '0.5 USDC', got {commission!r}"
        )


def _check_latency(latency: Mapping[str, int]) -> None:
    unknown = set(latency) - set(LATENCY_KEYS)
    if unknown:
        raise ValueError(f"latency: unknown keys {sorted(unknown)}; valid: {list(LATENCY_KEYS)}")
    for key, value in latency.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"latency.{key} must be a non-negative int (ns), got {value!r}")


def check_execution_models(spec: RunSpec) -> None:
    """Validate a spec's fill/fee/latency/exec-algorithm fields against the closed tables."""
    if spec.fill_model is not None:
        _check_fill_model(spec.fill_model)
    if spec.fee_model is not None:
        _check_fee_model(spec.fee_model)
    if spec.latency is not None:
        _check_latency(spec.latency)
    if isinstance(spec.exec_algorithms, str):
        raise ValueError(
            f"exec_algorithms must be a tuple of paths, not the str {spec.exec_algorithms!r}"
        )
    for path in spec.exec_algorithms:
        module, _, cls = str(path).partition(":")
        if not module or not cls:
            raise ValueError(f"exec_algorithms entry must be 'module:Class', got {path!r}")


@dataclass(frozen=True, eq=False)
class RunResult:
    """
    The outcome of one `BacktestRunConfig`, attributed by its id.

    Invariant: `config_id` is the `BacktestRunConfig.id` that produced every other field (results
    are matched by id, never by list position); `params` are the merged strategy parameters of that
    config; `trades`, `equity` and `metrics` come from that config's own engine.
    `metrics` are `kernel.performance_metrics.all_metrics` over the *closed* trades
    (`pnl_by_day == trades.pnl_by_day()`, realized PnL per UTC day of exit -- the one path bots'
    history shares, SSOT-02), while `equity` is the account balance `total` at each account event
    -- realized PnL and every commission paid, including an open position's entry fee, so the
    two can differ (e.g. `equity` ends below `starting_balance + sum(realized pnl)` by that fee).
    Known limit: `equity` is not marked to market -- an open position's unrealized PnL never
    moves it, so `equity.drawdowns()` understates an adverse move held open, and
    `metrics.max_drawdown` (daily realized returns) is a third, coarser view; upgrade path: sample
    `portfolio.net_exposures`/`unrealized_pnls` on a timer in the engine and add them per event;
    `nautilus_stats` is Nautilus's own `{"pnls": stats_pnls, "returns": stats_returns}` for
    cross-checking (`pnls`, `returns` and `general`; `general` holds only `Long Ratio`. The eight
    upstream statistics the portfolio does not register by default, added by the runner -- CAGR,
    Alpha, BetaRatio, CalmarRatio, InformationRatio, MaxDrawdown, TrackingError, TreynorRatio --
    land in `returns`, e.g. `CAGR (252 days)`, `Calmar Ratio (252 days)`, `Max Drawdown`); `orders` / `fills` are the engine's own `generate_orders_report()` /
    `generate_order_fills_report()` frames (possibly empty); `wall_seconds` is the engine's run
    time (data loading excluded).
    """

    config_id: str
    params: Mapping[str, object]
    equity: EquityCurve
    trades: TradeLedger
    metrics: MetricReport
    pnl_by_day: list[dict]
    nautilus_stats: dict
    iterations: int
    wall_seconds: float
    orders: pd.DataFrame = field(default_factory=pd.DataFrame)
    fills: pd.DataFrame = field(default_factory=pd.DataFrame)

    def pnl_by_hour_of_day(self) -> dict[int, float]:
        """
        Realized PnL per UTC hour of exit, exactly `self.trades.by_hour_of_day()` (Story 27.5): an
        hour with no exit is absent, never 0; nothing is summed here.
        """
        return self.trades.by_hour_of_day()

    def pnl_by_weekday(self) -> dict[int, float]:
        """
        Realized PnL per UTC weekday of exit (0 = Monday), exactly `self.trades.by_weekday()`
        (Story 27.5): a weekday with no exit is absent, never 0; nothing is summed here.
        """
        return self.trades.by_weekday()


class MarketFrames(Protocol):
    """
    Time-bounded market-data frames for one instrument.

    Invariant: every read is bounded -- `start` and `end` are required keyword arguments with no
    default (MEM-01), the window is half-open `[start, end)` on `ts_event`, and a frame never holds a
    value that was not read: gaps stay gaps (missing rows, None/NaN), never forward-filled or
    interpolated. Every frame has a UTC `DatetimeIndex` named `ts` plus an int `ts_event` column.
    Derived microstructure columns come from `kernel.indicators`, bars from the candle store
    (never a third seconds-to-bars fold).
    """

    def seconds(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame: ...

    def trades(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame: ...

    def bars(
        self, instrument_id: str, bar_seconds: int, *, start: str | int, end: str | int
    ) -> pd.DataFrame: ...

    def bar_coverage(
        self, instrument_id: str, bar_seconds: int, *, start: str | int, end: str | int
    ) -> list[tuple[int, int]]:
        """
        Return the `[start_ns, end_ns)` spans of the maximal runs of stored `bar_seconds` buckets whose
        whole bar lies in `[start, end)`, oldest first; `FileNotFoundError` without a store (as
        `bars`), so a missing store is never read as an outage: each span is a window `bars` reads without raising, and the holes between spans are buckets never
        observed (a collector outage) or outside the store (Story 27.4).
        """
        ...

    def same_symbol(self, instrument_id: str) -> list[str]:
        """
        Return the catalog's defined ids trading the same asset (`kernel.venues.asset_key`), itself
        included, sorted by venue then id; `[]` when the id has no asset key (Story 27.4).
        """
        ...

    def definition_precisions(self, instrument_id: str) -> tuple[int, int] | None:
        """
        Return the catalog's instrument definition's `(price_precision, size_precision)`, or None
        without a definition (Story 33.13): the scale an exact sum over the instrument's rows takes.
        """
        ...

    def funding(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame: ...

    def open_interest(
        self, instrument_id: str, *, start: str | int, end: str | int
    ) -> pd.DataFrame: ...

    def mark_index(
        self, instrument_id: str, *, start: str | int, end: str | int
    ) -> pd.DataFrame: ...

    def liquidations(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame:
        """
        Return the instrument's archived liquidations of `[start, end)` (Story 33.13,
        `frames.LIQUIDATIONS_COLUMNS`): the stored integers with their precisions, `size`, `price`
        and `notional` decoded once, and `price_kind` (always `"bankruptcy"`); read one UTC day at
        a time. An id without a liquidation feed is the empty frame with every column, never rows.
        """
        ...

    def objects(
        self, data_cls: type, instrument_id: str, *, start: str | int, end: str | int
    ) -> list:
        """
        Return the typed rows of the window, `ts_event` ascending, for checks that need the exact
        `Price`/`Quantity` a frame's float columns drop (the trade fold). Rows are the catalog's own
        class, except `IndexPriceUpdate`, which the pinned catalog cannot decode: its rows are
        `kernel.catalog_files.IndexPrice` (`ts_event`, `ts_init`, an exact `price`, not `value`).
        """
        ...


class RankingHistory(Protocol):
    """
    Ranking's published metric history for one instrument.

    Invariant: every value is ranking's own (pct-change, volatility, rank, ... as `metrics.db`
    holds them, AD-D10); research computes none of them and never imports `ranking` or `data_api`.
    """

    def history(self, instrument_id: str, days: int) -> pd.DataFrame: ...


class BacktestRunner(Protocol):
    """
    Runs backtests through `BacktestNode` (NAUT-03) and returns typed results.

    Invariant: one `RunResult` per distinct run config, attributed by `BacktestRunConfig.id`; a
    config Nautilus returned no result for raises (DATA-07), never a silent gap.
    """

    def run(self, spec: RunSpec) -> RunResult: ...

    def sweep(self, spec: RunSpec, grid: Sequence[Mapping[str, object]]) -> list[RunResult]:
        """One run per grid point, each point's params merged over `spec.params`."""
        ...
