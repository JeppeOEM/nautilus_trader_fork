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
The strategy gallery service, behind `research/notebooks/08_strategy_gallery`: a fixed list of
`RunSpec`s (upstream example strategies by string path, plus the two family strategies, plus the
four liquidation cascade runs of Story 33.14 and the four `OFIStrategy` forced-flow runs of Story
33.13 on a liquidation-feed id), each run
through the `BacktestRunner` port, as a leaderboard, an equity overlay and the same spec repeated
across the execution models (fill, fee, latency). It runs nothing itself and computes no statistic:
the numbers are `RunResult`'s, and a notebook copies a row's spec into `04_backtest_evaluation`.

Not in the gallery: `EMACrossTrailingStop` and `VolatilityMarketMaker` read `cache.quote_tick`, and
the `bars:<spec>` kind streams trade ticks only (`docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md`
section 3), so they would never place their orders; `EMACrossStopEntry` and the others stay
reachable by hand with `RunSpec`.

Known limit: every spec is run in sample over one window with fixed, untuned parameters, so the
leaderboard orders strategies by what they did on that window, not by what they will do; upgrade
path: `04_backtest_evaluation`'s sweep and walk-forward on the row that interests you.
"""

import json
import logging
import math
import time
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace

import pandas as pd
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.liquidation import has_liquidation_feed

from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.backtest_runner import settlement_currency
from research.application.evaluation import equity_frame as equity_frame_of
from research.application.evaluation import fills_frame
from research.application.indicator_atlas import Sizer
from research.application.liquidations import read_liquidations
from research.application.liquidations import replay_cascade
from research.application.ports import FEE_MODELS
from research.application.ports import FILL_MODELS
from research.application.ports import BacktestRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.application.ports import window_ns
from research.domain.report import MetricReport
from research.strategies.liquidation_cascade_strategy import DEFAULT_STOP_PCT
from research.strategies.liquidation_cascade_strategy import LiquidationCascadeStrategyConfig


logger = logging.getLogger(__name__)

_EXAMPLES = "nautilus_trader.examples.strategies"
_TWAP = "nautilus_trader.examples.algorithms.twap:TWAPExecAlgorithm"
_MA_CROSS = "research.strategies.ma_cross_strategy"
_SIGNAL = "research.strategies.indicator_signal_strategy"
_CASCADE = "research.strategies.liquidation_cascade_strategy:LiquidationCascadeStrategy"
# The four cascade runs: (mode, position sides allowed, label suffix).
CASCADE_RUNS = (
    ("follow", ("short",), "short only"),
    ("follow", ("short", "long"), "both sides"),
    ("fade", ("short",), "short only"),
    ("fade", ("short", "long"), "both sides"),
)
# The data kind every cascade run uses, also named in its label.
CASCADE_DATA = "liquidations"
_OFI = "research.strategies.ofi_strategy:OFIStrategy"
# The four OFI runs (Story 33.13): (label, the forced-flow fields), each run setting both fields so
# a `params` override never turns the baseline into a variant; they differ by exactly one input.
OFI_RUNS = (
    ("baseline", {"forced_flow_filter": False, "liquidation_cascade_mode": "off"}),
    ("forced-flow filter", {"forced_flow_filter": True, "liquidation_cascade_mode": "off"}),
    ("cascade fade", {"forced_flow_filter": False, "liquidation_cascade_mode": "fade"}),
    ("cascade follow", {"forced_flow_filter": False, "liquidation_cascade_mode": "follow"}),
)
# The parameters every OFI run starts from, under `params` and each run's own fields: the
# cumulative-delta gate on (net flow over `cum_delta_seconds` must agree with the entry's side),
# the one OFI decision the forced-flow filter changes -- with the gate off, the filter run would
# trade exactly as the baseline by construction.
OFI_BASE_PARAMS: dict[str, object] = {"cum_delta_threshold": 0.0}
# The data kind every OFI run uses (the snapshots plus the liquidations), also named in its label.
OFI_DATA = "seconds_liquidations"
# `OFIStrategyConfig`'s detector fields -> `LiquidationCascadeStrategyConfig`'s, for the sample.
_OFI_DETECTOR_FIELDS = {
    "cascade_window_s": "window_s",
    "cascade_baseline_s": "baseline_s",
    "cascade_intensity_threshold": "intensity_threshold",
    "cascade_decay_ratio": "decay_ratio",
}
TRADE_SIZE = "0.01"
BPS = 10_000.0
LEADERBOARD_COUNTS = ("trades", "orders", "fills")
SLIPPAGE_COLUMNS = ("client_order_id", "side", "price", "baseline_price", "difference_bps")
LEADERBOARD_COLUMNS = (
    "label",
    "strategy",
    *MetricReport.field_names(),
    *LEADERBOARD_COUNTS,
    "wall_seconds",
    "total_seconds",
    "error",
)
BASELINE_LATENCY = "300 ms (baseline)"


@dataclass(frozen=True)
class GallerySpec:
    """
    One run of the gallery: a `label` and the `RunSpec` that produces it.

    Invariant: `label` names the run in every table and chart (one per spec in a list); `axis` and
    `value` are set only on a spec `execution_axes` made, naming the model and the setting it
    varies, and are empty on a gallery spec.
    """

    label: str
    spec: RunSpec
    axis: str = ""
    value: str = ""


@dataclass(frozen=True, eq=False)
class Outcome:
    """
    What running one `GallerySpec` gave.

    Invariant: exactly one of `result` and `error` is set -- a spec that raised keeps its place
    with the exception text, never dropped (DATA-07); `wall_seconds` is the whole call's time,
    data loading included, failed or not.
    """

    gallery: GallerySpec
    result: RunResult | None
    error: str | None
    wall_seconds: float


def _spec(
    base: Mapping[str, object], path: str, params: Mapping[str, object], **fields: object
) -> RunSpec:
    module, _, name = path.partition(":")
    return RunSpec(
        strategy_path=path,
        config_path=f"{module}:{name}Config",
        params={"trade_size": TRADE_SIZE, **params},
        **{**base, **fields},  # type: ignore[arg-type]
    )


def _upstream(
    base: Mapping[str, object], periods: Mapping[str, float]
) -> list[tuple[str, RunSpec]]:
    ema = {"fast_ema_period": Sizer(periods, "EMACross").i("fast", 10)}
    ema["slow_ema_period"] = Sizer(periods, "EMACross").i("slow", 20)
    bracket = Sizer(periods, "EMACrossBracket")
    bands = Sizer(periods, "BBMeanReversion")
    twap = Sizer(periods, "EMACrossTWAP")
    return [
        ("EMACross", _spec(base, f"{_EXAMPLES}.ema_cross:EMACross", ema)),
        ("EMACrossLongOnly", _spec(base, f"{_EXAMPLES}.ema_cross_long_only:EMACrossLongOnly", ema)),
        (
            "EMACrossBracket",
            _spec(
                base,
                f"{_EXAMPLES}.ema_cross_bracket:EMACrossBracket",
                {
                    **ema,
                    "atr_period": bracket.i("atr_period", 20),
                    "bracket_distance_atr": bracket.f("bracket_distance_atr", 3.0),
                },
            ),
        ),
        (
            "BBMeanReversion",
            _spec(
                base,
                f"{_EXAMPLES}.bb_mean_reversion:BBMeanReversion",
                {
                    "bb_period": bands.i("period", 20),
                    "bb_std": bands.f("k", 2.0),
                    "rsi_period": bands.i("rsi_period", 14),
                    "rsi_buy_threshold": bands.f("rsi_buy", 0.3),
                    "rsi_sell_threshold": bands.f("rsi_sell", 0.7),
                },
            ),
        ),
        (
            "EMACrossTWAP",
            _spec(
                base,
                f"{_EXAMPLES}.ema_cross_twap:EMACrossTWAP",
                {
                    **ema,
                    "twap_horizon_secs": twap.f("twap_horizon_secs", 30.0),
                    "twap_interval_secs": twap.f("twap_interval_secs", 3.0),
                },
                exec_algorithms=(_TWAP,),
            ),
        ),
    ]


def _ma_cross(
    base: Mapping[str, object], periods: Mapping[str, float]
) -> list[tuple[str, RunSpec]]:
    rows = []
    for ma_type, exit_rule in (
        ("EXPONENTIAL", "cross"),
        ("HULL", "atr_stop"),
        ("ADAPTIVE", "trailing_atr"),
    ):
        sizes = Sizer(periods, f"MACross.{ma_type}")
        params = {
            "ma_type": ma_type,
            "exit": exit_rule,
            "fast_period": sizes.i("fast", 10),
            "slow_period": sizes.i("slow", 20),
            "atr_period": sizes.i("atr_period", 14),
            "atr_multiple": sizes.f("atr_multiple", 2.0),
        }
        rows.append(
            (f"MACross {ma_type} {exit_rule}", _spec(base, f"{_MA_CROSS}:MACrossStrategy", params))
        )
    return rows


def _signals(base: Mapping[str, object], periods: Mapping[str, float]) -> list[tuple[str, RunSpec]]:
    def sized(signal: str, **defaults: float) -> dict[str, float]:
        sizer = Sizer(periods, f"Signal.{signal}")
        return {
            key: sizer.i(key, int(value)) if isinstance(value, int) else sizer.f(key, value)
            for key, value in defaults.items()
        }

    table = {
        "bollinger": sized("bollinger", period=20, k=2.0),
        "macd": sized("macd", fast=12, slow=26),
        "rsi": sized("rsi", period=14, low=0.3, high=0.7, neutral=0.5),
        "obv": sized("obv", period=20),
        "fuzzy_candle": sized("fuzzy_candle", period=10, min_size=4),
    }
    return [
        (
            f"Signal {name}",
            _spec(
                base,
                f"{_SIGNAL}:IndicatorSignalStrategy",
                {"signal": name, "signal_params": params, "allow_short": True},
            ),
        )
        for name, params in table.items()
    ]


def default_specs(
    catalog_path: str,
    instrument: str,
    start: str | int,
    end: str | int,
    data: str,
    periods: Mapping[str, float],
    starting_balance: int = 10_000,
) -> list[GallerySpec]:
    """
    Return the gallery: five upstream example strategies (`EMACross`, `EMACrossLongOnly`,
    `EMACrossBracket`, `BBMeanReversion`, `EMACrossTWAP` with the `TWAPExecAlgorithm`), three
    `MACrossStrategy` runs (EMA cross exit, Hull with an ATR stop, adaptive with a trailing ATR
    stop) and five `IndicatorSignalStrategy` runs (Bollinger, MACD, RSI, OBV, fuzzy candle), all on
    one instrument over one window and one `data` kind.

    Invariant: every label is distinct; every period, multiple and threshold comes from `periods`
    (`"<Owner>.<key>"` or the generic `"<key>"`, as `indicator_atlas.Sizer` resolves them) or the
    strategy's own default, so a short window can shrink them all; nothing is run here.
    """
    base = {
        "catalog_path": catalog_path,
        "instrument_ids": (instrument,),
        "start": start,
        "end": end,
        "starting_balance": starting_balance,
        "data": data,
    }
    rows = [*_upstream(base, periods), *_ma_cross(base, periods), *_signals(base, periods)]
    return [GallerySpec(label, spec) for label, spec in rows]


def cascade_specs(
    catalog_path: str,
    instrument: str,
    start: str | int,
    end: str | int,
    params: Mapping[str, object],
    starting_balance: int = 10_000,
) -> list[GallerySpec]:
    """
    Return the four `LiquidationCascadeStrategy` runs (`CASCADE_RUNS`: follow and fade, each short
    only and both sides) on `instrument` with `data="liquidations"`, `params` (any
    `LiquidationCascadeStrategyConfig` field) over every run's own mode and sides, and
    `stop_pct=DEFAULT_STOP_PCT` (the strategy module's one default, the CLI's too) unless `params`
    names a stop. Each label names the run, the instrument and the data kind, e.g. `Cascade follow
    short only (BTCUSDT-LINEAR.BYBIT, liquidations)`, so a cascade row is told apart from a bar
    strategy's in the shared leaderboard and equity chart.

    Invariant: `[]` for an id without a liquidation feed (`has_liquidation_feed`) -- the caller
    states it (`cascade_sample`), nothing is run or faked; labels are distinct; nothing is run here.
    """
    if not has_liquidation_feed(instrument):
        return []
    base = {
        "catalog_path": catalog_path,
        "instrument_ids": (instrument,),
        "start": start,
        "end": end,
        "starting_balance": starting_balance,
        "data": CASCADE_DATA,
    }
    stop = {} if {"stop_pct", "stop_atr_multiple"} & set(params) else {"stop_pct": DEFAULT_STOP_PCT}
    return [
        GallerySpec(
            f"Cascade {mode} {suffix} ({instrument}, {CASCADE_DATA})",
            _spec(base, _CASCADE, {**stop, **params, "mode": mode, "sides": list(sides)}),
        )
        for mode, sides, suffix in CASCADE_RUNS
    ]


@dataclass(frozen=True)
class CascadeSample:
    """
    The sample behind the cascade runs: the window's UTC days, its archived liquidations, the
    episodes `replay_cascade` finds in them with the runs' detector parameters and the rows it
    skipped because the definition's precisions cannot hold their notional.

    Invariant: `has_feed` is False exactly for an id without a liquidation feed, and
    `has_definition` False for one whose catalog holds no instrument definition; the counts are
    then 0 and the text says which -- a missing feed or definition is stated, never read as a quiet
    market.
    """

    instrument: str
    has_feed: bool
    days: int
    liquidations: int
    episodes: int
    unscalable_rows: int = 0
    has_definition: bool = True

    def __str__(self) -> str:
        if not self.has_feed:
            return f"{self.instrument} has no liquidation feed (Bybit LINEAR only): no cascade runs"
        if not self.has_definition:
            return f"{self.instrument} has no instrument definition in the catalog: no sample"
        return (
            f"{self.instrument} cascade sample: {self.days} UTC day(s), {self.liquidations} "
            f"liquidations, {self.episodes} episode(s), {self.unscalable_rows} unscalable row(s) "
            "skipped"
        )


def _detector(instrument: str, params: Mapping[str, object]) -> LiquidationCascadeStrategyConfig:
    """Return a strategy config holding `params`' detector fields, the defaults elsewhere."""
    keys = ("window_s", "baseline_s", "intensity_threshold", "decay_ratio")
    chosen = {key: params[key] for key in keys if key in params}
    # Parsed, not keyword-built: the config validates each value's type (as a run's build does).
    return LiquidationCascadeStrategyConfig.parse(
        json.dumps({"instrument_id": instrument, **chosen})
    )


def _definition(catalog_path: str, instrument: str) -> Instrument | None:
    """
    Return the catalog's definition of `instrument`, or None. Duplicate-tolerant, as
    `backtest_runner._instruments`: one definition stored twice is one id, never an unpack error.
    """
    found = {
        str(i.id): i
        for i in ParquetDataCatalog(catalog_path).instruments(instrument_ids=[instrument])
    }
    return found.get(instrument)


def cascade_sample(
    catalog_path: str,
    instrument: str,
    start: str | int,
    end: str | int,
    params: Mapping[str, object],
) -> CascadeSample:
    """
    Return the `CascadeSample` of `instrument` over `[start, end)`: the window's UTC days, the
    liquidations read day by day (`read_liquidations`) and the episodes of `replay_cascade` from
    the window's start, at the instrument definition's precisions and with the detector fields of
    `params` (else the strategy config's defaults) -- the strategy's own detector.
    """
    if not has_liquidation_feed(instrument):
        return CascadeSample(instrument, False, 0, 0, 0)
    definition = _definition(catalog_path, instrument)
    if definition is None:
        return CascadeSample(instrument, True, 0, 0, 0, has_definition=False)
    start_ns, end_ns = window_ns(start, end)
    rows = read_liquidations(catalog_path, instrument, start_ns, end_ns)
    detector = _detector(instrument, params)
    episodes = replay_cascade(
        rows,
        detector.window_s,
        detector.baseline_s,
        detector.intensity_threshold,
        detector.decay_ratio,
        end_ns,
        start_ns=start_ns,
        precisions=(definition.price_precision, definition.size_precision),
    )
    days = (end_ns - 1) // NS_PER_DAY - start_ns // NS_PER_DAY + 1
    return CascadeSample(instrument, True, days, len(rows), len(episodes), episodes.unscalable_rows)


def ofi_specs(
    catalog_path: str,
    instrument: str,
    start: str | int,
    end: str | int,
    params: Mapping[str, object],
    starting_balance: int = 10_000,
) -> list[GallerySpec]:
    """
    Return the four `OFIStrategy` runs of Story 33.13 (`OFI_RUNS`: the baseline, the forced-flow
    filter, the cascade fade and the cascade follow gates) on `instrument` with
    `data="seconds_liquidations"`, `OFI_BASE_PARAMS` (the cumulative-delta gate on, so the filter
    can change a decision) under `params` (any `OFIStrategyConfig` field) under each run's own
    forced-flow fields; the execution models stay the venue defaults (the gallery's), so a row
    differs from the baseline by its one field. Each label names the run, the instrument and the
    data kind, e.g. `OFI cascade fade (BTCUSDT-LINEAR.BYBIT, seconds_liquidations)`.

    Invariant: `[]` for an id without a liquidation feed (`has_liquidation_feed`) -- nothing is run
    or faked; labels are distinct; nothing is run here.
    """
    if not has_liquidation_feed(instrument):
        return []
    base = {
        "catalog_path": catalog_path,
        "instrument_ids": (instrument,),
        "start": start,
        "end": end,
        "starting_balance": starting_balance,
        "data": OFI_DATA,
    }
    return [
        GallerySpec(
            f"OFI {name} ({instrument}, {OFI_DATA})",
            _spec(base, _OFI, {**OFI_BASE_PARAMS, **params, **run}),
        )
        for name, run in OFI_RUNS
    ]


def ofi_sample(
    catalog_path: str,
    instrument: str,
    start: str | int,
    end: str | int,
    params: Mapping[str, object],
) -> CascadeSample:
    """
    Return the `cascade_sample` behind the OFI runs: the same window and liquidations, with the
    detector fields of `params` (`cascade_window_s`, ... mapped to the cascade strategy's names,
    else its defaults, which are `OFIStrategyConfig`'s too). The same detector with the same
    parameters, but not the same clock: the sample advances it on every whole second
    (`replay_cascade`, the cascade strategy's timer), `OFIStrategy` at each snapshot's `ts_init`
    (~0.2 s after the second's close), so an episode's start and end can differ by up to a second
    and a borderline episode can appear on one side only (audit D-223) -- a sample size, not the
    gates' exact episodes.
    """
    detector = {_OFI_DETECTOR_FIELDS[k]: v for k, v in params.items() if k in _OFI_DETECTOR_FIELDS}
    return cascade_sample(catalog_path, instrument, start, end, detector)


def ofi_outcomes(outcomes: Sequence[Outcome]) -> list[Outcome]:
    """Return the outcomes of the forced-flow OFI runs (`data="seconds_liquidations"`), in order."""
    return [o for o in outcomes if o.gallery.spec.data == OFI_DATA]


def cascade_outcomes(outcomes: Sequence[Outcome]) -> list[Outcome]:
    """Return the outcomes of cascade runs (`data="liquidations"`), in order."""
    return [o for o in outcomes if o.gallery.spec.data == CASCADE_DATA]


def run_specs(runner: BacktestRunner, specs: Sequence[GallerySpec]) -> list[Outcome]:
    """
    Run every spec, in order, and return one `Outcome` per spec.

    Invariant: `len(outcomes) == len(specs)`; a spec whose run raises is logged with its traceback
    and kept as an `Outcome` with `error` set (the exception's text, never empty) -- one bad spec
    never hides the others, and is never skipped silently (DATA-07).
    Known limit: one node per spec, run one after another (`NodeRunner.sweep` batches only one spec's
    grid); upgrade path: group specs that share a strategy into one sweep.
    """
    outcomes = []
    for gallery in specs:
        started = time.monotonic()
        try:
            result, error = runner.run(gallery.spec), None
        except Exception as exc:
            logger.exception("gallery spec %r failed", gallery.label)
            result, error = None, f"{type(exc).__name__}: {exc}"
        outcomes.append(Outcome(gallery, result, error, time.monotonic() - started))
    return outcomes


def _row(outcome: Outcome) -> dict[str, object]:
    row: dict[str, object] = {
        "label": outcome.gallery.label,
        "strategy": outcome.gallery.spec.strategy_path.rpartition(":")[2],
    }
    result = outcome.result
    metrics = asdict(result.metrics) if result else dict.fromkeys(MetricReport.field_names())
    counts = (
        (len(result.trades), len(result.orders), len(result.fills))
        if result
        else (None, None, None)
    )
    wall = result.wall_seconds if result else math.nan
    return {
        **row,
        **metrics,
        **dict(zip(LEADERBOARD_COUNTS, counts, strict=True)),
        "wall_seconds": wall,
        "total_seconds": outcome.wall_seconds,
        "error": outcome.error,
    }


def leaderboard(outcomes: Sequence[Outcome]) -> pd.DataFrame:
    """
    Return one row per outcome: `label`, `strategy` (the class name), every `MetricReport` field,
    `trades` (closed), `orders`, `fills`, the engine's `wall_seconds` (the run alone, NaN when it
    failed), `total_seconds` (the whole call, data loading included) and `error`.

    Invariant: one row per outcome in order; a failed run has NaN/None in every number and its
    error text -- never a 0, never dropped; the metrics are `MetricReport`'s own, None (NaN) where
    undefined. No outcome gives an empty frame with the same columns.
    """
    rows = [_row(outcome) for outcome in outcomes]
    return pd.DataFrame(rows, columns=list(LEADERBOARD_COLUMNS)).astype(
        dict.fromkeys((*MetricReport.field_names(), "wall_seconds", "total_seconds"), "float64")
    )


def find_outcome(outcomes: Sequence[Outcome], label: str) -> Outcome:
    """Return the outcome labelled `label`; `ValueError` listing the labels when none matches."""
    for outcome in outcomes:
        if outcome.gallery.label == label:
            return outcome
    raise ValueError(
        f"no outcome labelled {label!r}; labels: {[o.gallery.label for o in outcomes]}"
    )


def check_outcomes(outcomes: Sequence[Outcome]) -> None:
    """
    Raise `RuntimeError` listing every label whose run failed, with its error (DATA-07).

    Invariant: called after the leaderboard is shown, so the failed row is visible first and the
    run then fails loudly instead of reading as a strategy that did nothing.
    """
    failed = [f"{o.gallery.label}: {o.error}" for o in outcomes if o.error is not None]
    if failed:
        raise RuntimeError(f"{len(failed)} gallery run(s) failed: " + "; ".join(failed))


def equity_frame(outcomes: Sequence[Outcome]) -> pd.DataFrame:
    """
    Return the equity of every run that succeeded in long form: `label`, `ts` (UTC), `equity`.

    Invariant: one row per account event of each successful run, in that run's order, from its
    own `EquityCurve` (the account balance, not marked to market -- `RunResult`'s Known limit);
    a failed run has no rows (its error is in the leaderboard).
    """
    frames = []
    for outcome in outcomes:
        if outcome.result is None:
            continue
        points = equity_frame_of(outcome.result.equity)["equity"].rename_axis("ts").reset_index()
        frames.append(points.assign(label=outcome.gallery.label)[["label", "ts", "equity"]])
    columns = ["label", "ts", "equity"]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def _variants(spec: RunSpec, seed: int, currency: str) -> list[tuple[str, str, RunSpec]]:
    """Return (axis, value, spec) for every fill model, fee model and latency setting."""
    quiet = replace(spec, latency_ms=0, latency=None, fill_model=None, fee_model=None)
    knobs = {"prob_fill_on_limit": 0.5, "prob_slippage": 0.5, "random_seed": seed}
    fills = [
        ("fill", name, replace(spec, fill_model={"name": name, **knobs})) for name in FILL_MODELS
    ]
    commission = {"commission": f"0.1 {currency}"}
    fees = [
        (
            "fee",
            name,
            replace(
                spec, fee_model={"name": name, **(commission if name != "maker_taker" else {})}
            ),
        )
        for name in FEE_MODELS
    ]
    ms = NS_PER_MS
    delays = {
        "latency 0 ms": quiet,
        "insert 300 / update 100 / cancel 50 ms": replace(
            quiet,
            latency={
                "insert_latency_nanos": 300 * ms,
                "update_latency_nanos": 100 * ms,
                "cancel_latency_nanos": 50 * ms,
            },
        ),
    }
    return [*fills, *fees, *[("latency", value, run) for value, run in delays.items()]]


def execution_axes(gallery: GallerySpec, seed: int) -> list[GallerySpec]:
    """
    Return `gallery`'s spec repeated across every execution model, one spec per setting:
    every `FILL_MODELS` name (probabilities 0.5 / 0.5 and `random_seed=seed`, so a rerun is
    identical), every `FEE_MODELS` name (a flat 0.1 commission, in the instrument's settlement
    currency, for `fixed` and `per_contract`) and latency 0 ms and an insert / update / cancel
    dict. The fixed 300 ms latency is the gallery spec's own setting, so it is not run again:
    `axis_table` takes the baseline outcome as that row.

    Invariant: every other field of the spec is unchanged, so a difference between two outputs is
    the one model's; each label is `<label> | <axis>=<value>` and `axis`/`value` are set.
    Known limit: the 0.5 / 0.5 knobs are one illustrative setting -- a model that does not read a
    knob ignores it, and none is fitted to a venue; upgrade path: the venue's measured fill
    probability and slippage (a fill model is a hypothesis about the venue until measured).
    """
    currency = settlement_currency(gallery.spec)
    return [
        GallerySpec(f"{gallery.label} | {axis}={value}", spec, axis, value)
        for axis, value, spec in _variants(gallery.spec, seed, currency)
    ]


def _with_baseline_latency(outcomes: Sequence[Outcome], baseline: Outcome) -> list[Outcome]:
    """Return `outcomes` with `baseline` as the `latency` / `300 ms (baseline)` row, no new run."""
    gallery = baseline.gallery
    row = replace(
        baseline,
        gallery=GallerySpec(
            f"{gallery.label} | latency={BASELINE_LATENCY}",
            gallery.spec,
            "latency",
            BASELINE_LATENCY,
        ),
    )
    at = [i for i, o in enumerate(outcomes) if o.gallery.axis == "latency"]
    index = at[0] + 1 if at else len(outcomes)
    return [*outcomes[:index], row, *outcomes[index:]]


def axis_table(outcomes: Sequence[Outcome], baseline: Outcome | None = None) -> pd.DataFrame:
    """
    Return `leaderboard(outcomes)` with `axis` and `value` columns (the setting each outcome's
    spec varied) after `label`, for outcomes of `execution_axes`' specs. With `baseline`, its
    outcome is added as the `latency` / `300 ms (baseline)` row (after the first latency row): the
    gallery spec already runs at the 300 ms latency, so a second run would be identical.

    Invariant: the same rows in the same order as the leaderboard, plus that one row; nothing is
    dropped.
    """
    rows = _with_baseline_latency(outcomes, baseline) if baseline is not None else list(outcomes)
    table = leaderboard(rows)
    table.insert(1, "axis", [o.gallery.axis for o in rows])
    table.insert(2, "value", [o.gallery.value for o in rows])
    return table


def slippage_frame(result: RunResult, baseline: RunResult) -> pd.DataFrame:
    """
    Return, per fill of `result` whose order the `baseline` run filled too, `SLIPPAGE_COLUMNS`:
    the order's `client_order_id` and `side`, its `price` (`avg_px`), the baseline's
    `baseline_price` for that order and `difference_bps` = (price - baseline) / baseline in basis
    points (positive: dearer than the baseline, for a buy a worse fill, for a sell a better one).

    Invariant: fills are matched on (`client_order_id`, `side`, `quantity`); a fill with no such
    match in the baseline is left out (not 0), so a model that changes which orders fill shrinks
    the frame instead of inventing a comparison.
    Known limit: the id is a timestamp tag plus a running counter (`nautilus_trader/common/
    generators.pyx`), not a property of the decision, so the match holds only until the two runs
    diverge (a differing order count shifts every later id); side and quantity make a wrong pair
    unlikely, not impossible. Upgrade path: key on the strategy's own decision (bar time).
    """
    fills, base = fills_frame(result), fills_frame(baseline)
    if fills.empty or base.empty:
        return pd.DataFrame(columns=list(SLIPPAGE_COLUMNS))
    keys = ["client_order_id", "side", "quantity"]
    left = fills[[*keys, "avg_px"]].astype({"side": str, "quantity": str}).reset_index(drop=True)
    right = base[[*keys, "avg_px"]].astype({"side": str, "quantity": str}).reset_index(drop=True)
    merged = left.merge(
        right.drop_duplicates(keys), on=keys, how="inner", suffixes=("", "_baseline")
    )
    out = pd.DataFrame(
        {
            "client_order_id": merged["client_order_id"],
            "side": merged["side"],
            "price": merged["avg_px"].astype(float),
            "baseline_price": merged["avg_px_baseline"].astype(float),
        }
    )
    return out.assign(
        difference_bps=(out["price"] - out["baseline_price"]) / out["baseline_price"] * BPS
    )


def slippage_by_axis(axes: Sequence[Outcome], baseline: Outcome) -> pd.DataFrame:
    """
    Return `slippage_frame` of every successful outcome of `axes` against `baseline`, stacked, with
    `axis` and `value` columns first: the data of the fill-price difference histograms.

    Invariant: a failed outcome, or a failed baseline, contributes no rows (their errors are in the
    tables, DATA-07); nothing is filled in for them.
    """
    base = baseline.result
    frames = [
        slippage_frame(o.result, base).assign(axis=o.gallery.axis, value=o.gallery.value)
        for o in axes
        if o.result is not None and base is not None
    ]
    columns = ["axis", "value", *SLIPPAGE_COLUMNS]
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)[columns]
