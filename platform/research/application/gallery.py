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
`RunSpec`s (upstream example strategies by string path, plus the two family strategies), each run
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

import logging
import math
import time
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace

import pandas as pd

from research.application.backtest_runner import settlement_currency
from research.application.evaluation import equity_frame as equity_frame_of
from research.application.evaluation import fills_frame
from research.application.indicator_atlas import Sizer
from research.application.ports import FEE_MODELS
from research.application.ports import FILL_MODELS
from research.application.ports import BacktestRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.domain.report import MetricReport


logger = logging.getLogger(__name__)

_EXAMPLES = "nautilus_trader.examples.strategies"
_TWAP = "nautilus_trader.examples.algorithms.twap:TWAPExecAlgorithm"
_MA_CROSS = "research.strategies.ma_cross_strategy"
_SIGNAL = "research.strategies.indicator_signal_strategy"
TRADE_SIZE = "0.01"
NS_PER_MS = 1_000_000
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
