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
The indicator picker's read model: the two indicator catalogs the chart and the Technicals tab
offer, their replay over a candle window, and the dispatch between them (Story 24.2 merged
the `chart_indicators` and `custom_indicators` modules here verbatim, and moved the
dispatch out of `data_api/routes/indicators.py`). Only the two colliding public names were
renamed: `replay_native`/`native_catalog_json` (was `chart_indicators.replay_indicator/catalog_json`)
and `replay_custom`/`custom_catalog_json` (was `custom_indicators.replay_indicator/catalog_json`).

**Native** -- dispatch/metadata over OHLCV-fed `Indicator` classes: `nautilus_trader.indicators`,
plus the kernel's own candle-fed indicators (`kernel.candle_patterns.CandlePattern`, Story 27.7:
one pattern definition, shared by views, research and bots; six of `kernel.ta`'s, Story 33.11) --
no indicator math of its own (DESIGN-02). Every entry of both catalogs may carry a `plot` hint per
output (`PlotStyle`) and a legend `note` (Story 33.11). `INDICATOR_CATALOG` maps a registered name to an `IndicatorSpec` describing how to
build and feed the real indicator class. `replay_native` instantiates it and drives it from candle
data. An enum-typed param travels as its member name (`enum_params`), and `native_catalog_json`
lists each one's allowed names as `choices`, which the picker renders as a dropdown. Six of `nautilus_trader.indicators`'s public classes are intentionally excluded:
`Candle*`/`FuzzyCandle` are enums/value-types, not indicators; `SpreadAnalyzer` is tick-driven, not
candle-driven; `FuzzyCandlesticks`/`Swings` produce non-float outputs that don't fit this module's
`list[float | None]` contract.

**Custom** -- dispatch/metadata for dYdX-specific chart indicators that are not
`nautilus_trader.indicators` classes and cannot be computed from OHLCV candle fields alone (CVD,
Cancel Pressure, OFI -- Stories 10.2-10.4). Mirrors the native catalog/replay/catalog_json shape for
params/panel/dispatch, but deliberately does not mirror its `enum_params` round-tripping (no custom
indicator needs an enum-typed param yet -- add it if one does, DESIGN-01) and a custom indicator's
`replay` receives a `ReplayWindow` (instrument id + window bounds) alongside the candle list, since
it needs to fetch its own order-book/trade-level/second-snapshot rows for that window -- a candle
dict alone (o/h/l/c/v) doesn't carry that data. Since Story 33.3 a candle dict also carries the bar's
exact order-flow and liquidation sums, which CVD and Story 33.6's order-flow entries read instead
(their cumulative modes seeded from an exact store prefix); `DepthWithinBps` is the one entry
reading raw seconds. Story 33.11's `Supertrend`, `PivotPoints` and `ZigZag` drive `kernel.ta`
classes the native shape cannot carry (a split output, a store seed, sparse output). Each entry
declares its outputs' `units` (the legend's formatting), and an unlisted one (`listed=False`) is
replayed but never offered.

`CUSTOM_INDICATOR_CATALOG` is filled once, at import, by the registrations below each replay: a
static table, never mutated at runtime. Per DESIGN-02, the two catalogs stay unaware of each other's contents -- the native half never reads
`CUSTOM_INDICATOR_CATALOG`, the custom half never reads `INDICATOR_CATALOG`, and they meet only in
the dispatch at the bottom of this module (`merged_catalog`, `replay_entry`), which is also where a
name registered in both catalogs fails loud. The one shared name (`Panel`) is a type alias, not a
coupling to catalog internals.
"""

import math
import os
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import Any
from typing import Literal
from typing import NamedTuple
from typing import Protocol

import numpy as np
from candles.application import queries
from candles.domain.fold import FLOW_KEYS
from candles.domain.fold import LIQUIDATION_KEYS
from candles.domain.fold import PRECISION_KEYS
from candles.domain.fold import bucket_start_ms
from kernel import catalog_files
from kernel.candle_patterns import CandlePattern
from kernel.candle_patterns import PatternName
from kernel.candle_patterns import Thresholds
from kernel.indicators import bar_vwap
from kernel.indicators import depth_within_bps
from kernel.indicators import organic_delta_units
from kernel.indicators import snapshot_depth
from kernel.indicators import units_ratio
from kernel.second_snapshot import BOOK_DEPTH
from kernel.ta import PIVOT_KINDS
from kernel.ta import PIVOT_LEVELS
from kernel.ta import AverageDirectionalIndex
from kernel.ta import AwesomeOscillator
from kernel.ta import ChaikinMoneyFlow
from kernel.ta import MoneyFlowIndex
from kernel.ta import ParabolicSAR
from kernel.ta import PivotPoints
from kernel.ta import Supertrend
from kernel.ta import WilliamsPercentR
from kernel.ta import ZigZag
from kernel.venues import venue_of
from observability import error_ledger

from nautilus_trader import indicators as _ind
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import PriceType
from views.chart_series import MAX_QUERY_SPAN_SECONDS
from views.chart_series import CancellationTracker
from views.chart_series import stored_bar


# =============================================================================================
# Native: `nautilus_trader.indicators` (was the `chart_indicators` module)
# =============================================================================================

Panel = Literal["overlay", "oscillator", "histogram"]
# How the chart draws one output (Story 33.11): a plain line (the default, also for an output the
# entry's `plot` does not name), a stepped line held flat until the next value (`steps`), unjoined
# dots (`points`), or one line joining only the slots that carry a value (`swing`: the frontend
# drops the empty slots, so sparse swing points are connected across them).
PlotStyle = Literal["line", "steps", "points", "swing"]
PLOT_STYLES: tuple[str, ...] = ("line", "steps", "points", "swing")


@dataclass
class IndicatorSpec:
    cls: type
    # Constructor param name -> default value (JSON-safe: enums stored as .name strings).
    params: dict[str, Any]
    # Candle fields, in update_raw() order: "open"/"high"/"low"/"close"/"volume"/"timestamp".
    feed: tuple[str, ...]
    # Attribute names to read off the indicator instance after each update.
    outputs: tuple[str, ...]
    panel: Panel
    # Param name -> enum type, for string<->enum round-tripping through JSON.
    enum_params: dict[str, type[Enum]] = field(default_factory=dict)
    # Output -> how it is drawn (`PlotStyle`); an output not named here is a line (Story 33.11).
    plot: dict[str, PlotStyle] = field(default_factory=dict)
    # A short legend note shown after the entry's title (Story 33.11); None: no note.
    note: str | None = None


INDICATOR_CATALOG: dict[str, IndicatorSpec] = {
    "SimpleMovingAverage": IndicatorSpec(
        _ind.SimpleMovingAverage,
        {"period": 20, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "ExponentialMovingAverage": IndicatorSpec(
        _ind.ExponentialMovingAverage,
        {"period": 20, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "WeightedMovingAverage": IndicatorSpec(
        _ind.WeightedMovingAverage,
        {"period": 20, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "HullMovingAverage": IndicatorSpec(
        _ind.HullMovingAverage,
        {"period": 20, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "AdaptiveMovingAverage": IndicatorSpec(
        _ind.AdaptiveMovingAverage,
        {"period_er": 10, "period_alpha_fast": 2, "period_alpha_slow": 30, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "DoubleExponentialMovingAverage": IndicatorSpec(
        _ind.DoubleExponentialMovingAverage,
        {"period": 20, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "VariableIndexDynamicAverage": IndicatorSpec(
        _ind.VariableIndexDynamicAverage,
        {"period": 20, "price_type": "LAST", "cmo_ma_type": "SIMPLE"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType, "cmo_ma_type": MovingAverageType},
    ),
    "WilderMovingAverage": IndicatorSpec(
        _ind.WilderMovingAverage,
        {"period": 20, "price_type": "LAST"},
        ("close",),
        ("value",),
        "overlay",
        {"price_type": PriceType},
    ),
    "BollingerBands": IndicatorSpec(
        _ind.BollingerBands,
        {"period": 20, "k": 2.0, "ma_type": "SIMPLE"},
        ("high", "low", "close"),
        ("upper", "middle", "lower"),
        "overlay",
        {"ma_type": MovingAverageType},
    ),
    "KeltnerChannel": IndicatorSpec(
        _ind.KeltnerChannel,
        {
            "period": 20,
            "k_multiplier": 2.0,
            "ma_type": "EXPONENTIAL",
            "ma_type_atr": "SIMPLE",
            "use_previous": True,
            "atr_floor": 0.0,
        },
        ("high", "low", "close"),
        ("upper", "middle", "lower"),
        "overlay",
        {"ma_type": MovingAverageType, "ma_type_atr": MovingAverageType},
    ),
    "DonchianChannel": IndicatorSpec(
        _ind.DonchianChannel,
        {"period": 20},
        ("high", "low"),
        ("upper", "middle", "lower"),
        "overlay",
    ),
    "KeltnerPosition": IndicatorSpec(
        _ind.KeltnerPosition,
        {
            "period": 20,
            "k_multiplier": 2.0,
            "ma_type": "EXPONENTIAL",
            "ma_type_atr": "SIMPLE",
            "use_previous": True,
            "atr_floor": 0.0,
        },
        ("high", "low", "close"),
        ("value",),
        "oscillator",
        {"ma_type": MovingAverageType, "ma_type_atr": MovingAverageType},
    ),
    "VolumeWeightedAveragePrice": IndicatorSpec(
        _ind.VolumeWeightedAveragePrice, {}, ("close", "volume", "timestamp"), ("value",), "overlay"
    ),
    "RelativeStrengthIndex": IndicatorSpec(
        _ind.RelativeStrengthIndex, {"period": 14}, ("close",), ("value",), "oscillator"
    ),
    "MovingAverageConvergenceDivergence": IndicatorSpec(
        _ind.MovingAverageConvergenceDivergence,
        {"fast_period": 12, "slow_period": 26, "ma_type": "EXPONENTIAL", "price_type": "LAST"},
        ("close",),
        ("value",),
        "oscillator",
        {"ma_type": MovingAverageType, "price_type": PriceType},
    ),
    "Stochastics": IndicatorSpec(
        _ind.Stochastics,
        {"period_k": 14, "period_d": 3, "slowing": 1},
        ("high", "low", "close"),
        ("value_k", "value_d"),
        "oscillator",
    ),
    "CommodityChannelIndex": IndicatorSpec(
        _ind.CommodityChannelIndex,
        {"period": 20, "scalar": 0.015},
        ("high", "low", "close"),
        ("value",),
        "oscillator",
    ),
    "AverageTrueRange": IndicatorSpec(
        _ind.AverageTrueRange,
        {"period": 14, "ma_type": "SIMPLE", "use_previous": True, "value_floor": 0.0},
        ("high", "low", "close"),
        ("value",),
        "oscillator",
        {"ma_type": MovingAverageType},
    ),
    "VolatilityRatio": IndicatorSpec(
        _ind.VolatilityRatio,
        {
            "fast_period": 10,
            "slow_period": 40,
            "ma_type": "SIMPLE",
            "use_previous": True,
            "value_floor": 0.0,
        },
        ("high", "low", "close"),
        ("value",),
        "oscillator",
        {"ma_type": MovingAverageType},
    ),
    "AroonOscillator": IndicatorSpec(
        _ind.AroonOscillator,
        {"period": 14},
        ("high", "low"),
        ("aroon_up", "aroon_down", "value"),
        "oscillator",
    ),
    "DirectionalMovement": IndicatorSpec(
        _ind.DirectionalMovement,
        {"period": 14, "ma_type": "EXPONENTIAL"},
        ("high", "low"),
        ("pos", "neg", "value"),
        "oscillator",
        {"ma_type": MovingAverageType},
    ),
    "RateOfChange": IndicatorSpec(
        _ind.RateOfChange, {"period": 10, "use_log": False}, ("close",), ("value",), "oscillator"
    ),
    "ChandeMomentumOscillator": IndicatorSpec(
        _ind.ChandeMomentumOscillator, {"period": 14}, ("close",), ("value",), "oscillator"
    ),
    "OnBalanceVolume": IndicatorSpec(
        _ind.OnBalanceVolume, {"period": 0}, ("open", "close", "volume"), ("value",), "oscillator"
    ),
    "Pressure": IndicatorSpec(
        _ind.Pressure,
        {"period": 10},
        ("high", "low", "close", "volume"),
        ("value", "value_cumulative"),
        "oscillator",
    ),
    "KlingerVolumeOscillator": IndicatorSpec(
        _ind.KlingerVolumeOscillator,
        {"fast_period": 34, "slow_period": 55, "signal_period": 13},
        ("high", "low", "close", "volume"),
        ("value",),
        "oscillator",
    ),
    "ArcherMovingAveragesTrends": IndicatorSpec(
        _ind.ArcherMovingAveragesTrends,
        {"fast_period": 8, "slow_period": 21, "signal_period": 8, "ma_type": "EXPONENTIAL"},
        ("close",),
        ("long_run", "short_run"),
        "oscillator",
        {"ma_type": MovingAverageType},
    ),
    "IchimokuCloud": IndicatorSpec(
        _ind.IchimokuCloud,
        {"tenkan_period": 9, "kijun_period": 26, "senkou_period": 52, "displacement": 26},
        ("high", "low", "close"),
        ("tenkan_sen", "kijun_sen", "senkou_span_a", "senkou_span_b", "chikou_span"),
        "overlay",
    ),
    "LinearRegression": IndicatorSpec(
        _ind.LinearRegression,
        {"period": 14},
        ("close",),
        ("value", "slope", "intercept", "degree", "cfo", "R2"),
        "oscillator",
    ),
    "EfficiencyRatio": IndicatorSpec(
        _ind.EfficiencyRatio, {"period": 10}, ("close",), ("value",), "oscillator"
    ),
    "PsychologicalLine": IndicatorSpec(
        _ind.PsychologicalLine, {"period": 14}, ("close",), ("value",), "oscillator"
    ),
    "Bias": IndicatorSpec(_ind.Bias, {"period": 26}, ("close",), ("value",), "oscillator"),
    "RelativeVolatilityIndex": IndicatorSpec(
        _ind.RelativeVolatilityIndex, {"period": 14}, ("close",), ("value",), "oscillator"
    ),
    "VerticalHorizontalFilter": IndicatorSpec(
        _ind.VerticalHorizontalFilter, {"period": 28}, ("close",), ("value",), "oscillator"
    ),
    # +100 bullish / -100 bearish / 0 per bar: drawn as histogram spikes (Story 27.7).
    "CandlePattern": IndicatorSpec(
        CandlePattern,
        {"pattern": PatternName.ENGULFING.name, **asdict(Thresholds())},
        ("open", "high", "low", "close"),
        ("value",),
        "histogram",
        {"pattern": PatternName},
    ),
    # Story 33.11: `kernel.ta`, the indicators `nautilus_trader.indicators` lacks. Like every
    # native entry they warm up over the page's own bars (audit D-217).
    "ParabolicSAR": IndicatorSpec(
        ParabolicSAR,
        {"step": 0.02, "max_step": 0.2},
        ("high", "low"),
        ("value",),
        "overlay",
        plot={"value": "points"},
    ),
    "AverageDirectionalIndex": IndicatorSpec(
        AverageDirectionalIndex,
        {"period": 14},
        ("high", "low", "close"),
        ("adx", "plus_di", "minus_di"),
        "oscillator",
    ),
    "WilliamsPercentR": IndicatorSpec(
        WilliamsPercentR, {"period": 14}, ("high", "low", "close"), ("value",), "oscillator"
    ),
    "MoneyFlowIndex": IndicatorSpec(
        MoneyFlowIndex, {"period": 14}, ("high", "low", "close", "volume"), ("value",), "oscillator"
    ),
    "ChaikinMoneyFlow": IndicatorSpec(
        ChaikinMoneyFlow,
        {"period": 20},
        ("high", "low", "close", "volume"),
        ("value",),
        "oscillator",
    ),
    "AwesomeOscillator": IndicatorSpec(
        AwesomeOscillator, {"fast": 5, "slow": 34}, ("high", "low"), ("value",), "histogram"
    ),
}


# Price sources a close-fed indicator can be computed on (Story 32.3). `close` is the default and
# the only one that existed before; the others are derived per candle, never stored.
PRICE_SOURCES: tuple[str, ...] = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")
DEFAULT_SOURCE = "close"
_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "close": ("c",),
    "open": ("o",),
    "high": ("h",),
    "low": ("l",),
    "hl2": ("h", "l"),
    "hlc3": ("h", "l", "c"),
    "ohlc4": ("o", "h", "l", "c"),
}


def source_price(candle: dict, source: str) -> float | None:
    """
    One candle's price under `source`: a plain field, or `hl2 = (h+l)/2`,
    `hlc3 = (h+l+c)/3`, `ohlc4 = (o+h+l+c)/4`. Computed per candle on every replay, never stored.
    Reads only the keys the source needs. A candle missing a needed component (None) is a real gap:
    returns None, never a fabricated value (DATA-01); `replay_native` maps it to None outputs.
    """
    fields = _SOURCE_FIELDS.get(source)
    if fields is None:
        raise ValueError(f"unknown source {source!r} (choose one of {list(PRICE_SOURCES)})")
    values = [candle[k] for k in fields]
    if any(v is None for v in values):
        return None
    return sum(values) / len(values)


def is_source_selectable(spec: IndicatorSpec) -> bool:
    """
    Only an indicator fed exactly the close has a price input to choose. One fed `high`/`low`/
    `volume` (or several fields) keeps its fixed input -- a source there would be fabricated.
    """
    return spec.feed == ("close",)


def check_source(name: str, source: str) -> None:
    """
    Raise `ValueError` naming `source` unless it is `close` (always allowed) or a known source on
    a native, close-fed indicator. The one rule behind the routes' 422 and the replay's refusal.
    """
    if source == DEFAULT_SOURCE:
        return
    if source not in PRICE_SOURCES:
        raise ValueError(f"unknown source {source!r} (choose one of {list(PRICE_SOURCES)})")
    spec = INDICATOR_CATALOG.get(name)
    if spec is None or not is_source_selectable(spec):
        raise ValueError(f"source {source!r} is not selectable for indicator {name!r}")


# Field lookup: candle-side key each `feed` name maps to. VWAP's "timestamp" is handled
# specially in replay_native (converted from the candle's "t", not looked up directly).
_FEED_FIELD = {"open": "o", "high": "h", "low": "l", "close": "c", "volume": "v", "price": "c"}


def replay_native(
    candles: list[dict], name: str, params: dict[str, Any], source: str = DEFAULT_SOURCE
) -> dict[str, list[float | None]]:
    """
    Instantiate `name` from `INDICATOR_CATALOG` with `params`, replay it over `candles`
    in order, and return each registered output attribute as a list aligned 1:1 with
    `candles`. A candle before `indicator.initialized` becomes True maps to None. `source` picks
    the price a close-fed indicator reads (`check_source` refuses it anywhere else).
    """
    if name not in INDICATOR_CATALOG:
        raise ValueError(f"Unknown indicator: {name!r}")
    check_source(name, source)
    spec = INDICATOR_CATALOG[name]
    resolved = _resolve_enum_params(spec, params)
    indicator = spec.cls(**resolved)

    out: dict[str, list[float | None]] = {attr: [] for attr in spec.outputs}
    for candle in candles:
        feed = _feed_values(spec, candle, source)
        if any(v is None for v in feed):
            # A gap candle (a source component is None): no update, unknown output (DATA-01).
            for attr in spec.outputs:
                out[attr].append(None)
            continue
        indicator.update_raw(*feed)
        initialized = indicator.initialized
        for attr in spec.outputs:
            out[attr].append(float(getattr(indicator, attr)) if initialized else None)
    return out


# `MovingAverageFactory.create` returns None for ADAPTIVE (it needs extra periods the factory
# cannot take), so a `ma_type` of ADAPTIVE would fail deep in the replay as `'NoneType'.update_raw`.
# It is left out of the picker's dropdown and rejected by name up front instead.
_UNBUILDABLE_MEMBERS: frozenset[Enum] = frozenset({MovingAverageType.ADAPTIVE})


def _choices(enum_type: type[Enum]) -> list[str]:
    return [m.name for m in enum_type if m not in _UNBUILDABLE_MEMBERS]


def _resolve_enum_params(spec: IndicatorSpec, params: dict[str, Any]) -> dict[str, Any]:
    """
    Merge caller params over the spec's defaults, converting enum-name strings back to enums. An
    unknown name raises `KeyError`; a member the replay cannot build raises `ValueError`.
    """
    merged = {**spec.params, **params}
    for key, enum_type in spec.enum_params.items():
        value = merged.get(key)
        if isinstance(value, str):
            merged[key] = enum_type[value]
            if merged[key] in _UNBUILDABLE_MEMBERS:
                allowed = _choices(enum_type)
                raise ValueError(f"{key}={value} is not supported (choose one of {allowed})")
    return merged


def _feed_values(
    spec: IndicatorSpec, candle: dict, source: str = DEFAULT_SOURCE
) -> tuple[Any, ...]:
    if is_source_selectable(spec):
        return (source_price(candle, source),)
    values: list[Any] = []
    for field_name in spec.feed:
        if field_name == "timestamp":
            import datetime as _dt

            values.append(_dt.datetime.fromtimestamp(candle["t"] / 1000, tz=_dt.UTC))
        else:
            values.append(candle[_FEED_FIELD[field_name]])
    return tuple(values)


def native_catalog_json() -> dict[str, Any]:
    """
    `INDICATOR_CATALOG` serialized for `GET /data/indicators/catalog` -- name, params
    with JSON-safe defaults, panel classification, and `choices`: every enum param's allowed
    member names (what `_resolve_enum_params` accepts: every member the replay can build), `{}`
    for an entry without one. The single source Story 8.4's picker UI builds its list and its
    dropdowns from.
    """
    return {
        name: {
            "params": spec.params,
            "panel": spec.panel,
            "choices": {key: _choices(enum) for key, enum in spec.enum_params.items()},
            "source_selectable": is_source_selectable(spec),
            "outputs": list(spec.outputs),
            "plot": spec.plot,
            "note": spec.note,
        }
        for name, spec in INDICATOR_CATALOG.items()
    }


# =============================================================================================
# Custom: dYdX-specific indicators over the window's own rows (was the `custom_indicators` module)
# =============================================================================================

# Duplicated from dashboard.py's own module-level constant (same env var, same default) --
# this module cannot import it from there without a circular import (dashboard.py imports
# this module). One line, not worth a shared-constants module for just this (DESIGN-01).
# Known limit: the one environment read left in `views` -- every other read model takes its
# catalog path from the caller (the interface, AD-D12's env contract). It reads the same variable
# with the same default as `data_api.settings.CATALOG_PATH`, so the two resolve identically in
# every deployment, but a caller cannot point the custom replays at another catalog. Upgrade path:
# carry the catalog path on `ReplayWindow` (built by `chart_series.indicator_values_page` and
# `ranking_columns.technicals_values`, which already receive it) and drop this constant; deferred
# because Story 24.2 relocates these bodies verbatim.
_CATALOG_PATH = os.environ.get("CATALOG_PATH", "platform/data/catalog")


@dataclass(frozen=True)
class ReplayWindow:
    """
    The window context a custom indicator's `replay` needs beyond the candle list itself.

    `start_ms`/`end_ms` are `None` for the live (not-yet-closed) window -- mirrors
    `coin_indicators_handler`'s existing live/historical branch in dashboard.py, which already
    picks `_live_candles_json` vs `_historical_candles_json` on exactly this same condition.
    `candles_dir` is where the candle stores live (Story 33.3: CVD's `all` anchor sums the stored
    bars before the window); None when the caller has no store, and `all` is then refused.
    """

    instrument_id: str
    bar_seconds: int
    start_ms: int | None
    end_ms: int | None
    candles_dir: str | None = None


ReplayFn = Callable[[list[dict], dict[str, Any], ReplayWindow], dict[str, list[float | None]]]


@dataclass
class CustomIndicatorSpec:
    # JSON-safe default params (same role as IndicatorSpec.params in chart_indicators.py).
    params: dict[str, Any]
    panel: Panel
    # Computes every registered output attribute for the given candles/params/window, aligned
    # 1:1 with `candles` -- identical output contract to replay_native.
    replay: ReplayFn
    # Every output name the replay can return, under any params (Story 33.8): served as the
    # catalog's `outputs`, the alert form's output select, as a native spec's `outputs` are. A
    # params-dependent entry lists all of them (`TradeCount`: `value`, or `buys`/`sells` split).
    outputs: tuple[str, ...]
    # Raises `ValueError` for merged params the replay would refuse; the save-time half of the rule
    # `check_params` applies (a native spec gets it from its constructor). None: nothing to check.
    check_params: Callable[[dict[str, Any]], None] | None = None
    # A string param's allowed values, served as the catalog's `choices` (the picker's dropdown)
    # and enforced by `check_params`, as a native enum param's are.
    choices: dict[str, list[str]] = field(default_factory=dict)
    # Output -> one of `INDICATOR_UNITS` (Story 33.6): the legend formats that output at the
    # instrument's precision through `frontend/src/lib/units.ts`. An output without one keeps the
    # legend's generic formatting.
    units: dict[str, str] = field(default_factory=dict)
    # False keeps the entry out of `custom_catalog_json`, so out of `merged_catalog`: the picker, the
    # indicator-config PUT, the layout seed and the Technicals refuse it, while the values route
    # (`replay_entry`) still replays it -- the stored Anchored VWAP drawing's series (Story 33.6).
    listed: bool = True
    # Output -> how it is drawn and the legend note, as on `IndicatorSpec` (Story 33.11).
    plot: dict[str, PlotStyle] = field(default_factory=dict)
    note: str | None = None


# What a custom output's value measures (Story 33.6): a price (`price_precision`), a size
# (`size_precision`), a mean of sizes (finer than the size step: an average of 0.0004 on a 0.001
# grid must not print as 0), a whole count, or a ratio (shown as a percentage).
INDICATOR_UNITS = ("price", "size", "size_mean", "count", "ratio")

CUSTOM_INDICATOR_CATALOG: dict[str, CustomIndicatorSpec] = {}


def replay_custom(
    candles: list[dict],
    name: str,
    params: dict[str, Any],
    window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Look up `name` in `CUSTOM_INDICATOR_CATALOG` and run its `replay` function."""
    if name not in CUSTOM_INDICATOR_CATALOG:
        raise ValueError(f"Unknown custom indicator: {name!r}")
    spec = CUSTOM_INDICATOR_CATALOG[name]
    merged = {**spec.params, **params}
    return spec.replay(candles, merged, window)


def custom_catalog_json() -> dict[str, Any]:
    """
    `CUSTOM_INDICATOR_CATALOG`'s listed entries serialized for the merged
    `/api/indicators/catalog` response; an unlisted one (`listed=False`) is left out.
    """
    return {
        name: {
            "params": spec.params,
            "panel": spec.panel,
            "choices": spec.choices,
            "units": spec.units,
            "outputs": list(spec.outputs),
            "plot": spec.plot,
            "note": spec.note,
        }
        for name, spec in CUSTOM_INDICATOR_CATALOG.items()
        if spec.listed
    }


def _bucket_ns(ts_ns: int, bar_seconds: int) -> int:
    """
    Return the start (ns) of the bucket a stamp falls in, keyed like the candles' `t * 1_000_000`:
    `candles.domain.fold.bucket_start_ms`, the one bucket rule (a 1W bucket starts on Monday).
    """
    return bucket_start_ms(ts_ns // 1_000_000, bar_seconds) * 1_000_000


CVD_ANCHORS = ("session", "visible", "all")
_DAY_SECONDS = 86_400
_ZERO_TOTALS = queries.FlowTotals(0, 0, 0, 0, 0)
# Running exact sums at one `_Scale`: (delta, volume) in `10^-size`, pv in `10^-(price + size)`.
_Running = tuple[int, int, int]


class _Scale(NamedTuple):
    """The finest price and size precision of a replay's bars and store prefixes."""

    price: int
    size: int


def _bar_totals(candle: dict) -> queries.FlowTotals | None:
    """One bar's stored order flow as `FlowTotals`; None when its flow is null (unknown)."""
    if candle.get("buy_v") is None:
        return None
    if any(
        candle.get(key) is None for key in ("sell_v", "pv", "price_precision", "size_precision")
    ):
        # Impossible by the fold (the flow group and its precisions are null together); a corrupt
        # row is named, never a bare `TypeError` (DATA-07).
        raise ValueError(
            f"bar t={candle['t']}: known buy_v but a null sell_v, pv or precision (a corrupt row)"
        )
    buy_v, sell_v, sp = candle["buy_v"], candle["sell_v"], candle["size_precision"]
    pv_precision = candle["price_precision"] + sp
    return queries.FlowTotals(buy_v - sell_v, buy_v + sell_v, sp, candle["pv"], pv_precision)


def _scale_of(candles: list[dict], seeds: Iterable[queries.FlowTotals | None]) -> _Scale:
    """Return the finest precisions present: every term is rescaled to them by `10**k`, exactly."""
    totals = [t for t in (*map(_bar_totals, candles), *seeds) if t is not None]
    return _Scale(
        price=max([t.pv_precision - t.size_precision for t in totals], default=0),
        size=max([t.size_precision for t in totals], default=0),
    )


def _scaled(totals: queries.FlowTotals, scale: _Scale) -> _Running:
    size_k = scale.size - totals.size_precision
    pv_k = scale.price + scale.size - totals.pv_precision
    return (
        totals.delta_units * 10**size_k,
        totals.volume_units * 10**size_k,
        totals.pv_units * 10**pv_k,
    )


def _running_totals(
    candles: list[dict], seeds: dict[int, queries.FlowTotals | None], scale: _Scale
) -> list[_Running | None]:
    """
    Return the exact running sums at each bar, restarting at every index of `seeds` from that seed (the
    segment's store prefix, already summed); a None seed (an uncovered prefix) leaves its segment
    None, and a bar before the first seeded index is None. A bar whose flow is null is None and the
    total carries on past it, without its term (DATA-01: unknown, never 0, never a reset).
    """
    out: list[_Running | None] = []
    total: _Running | None = None
    for i, candle in enumerate(candles):
        if i in seeds:
            seed = seeds[i]
            total = None if seed is None else _scaled(seed, scale)
        bar = _bar_totals(candle)
        if total is None or bar is None:
            out.append(None)
            continue
        delta, volume, pv = _scaled(bar, scale)
        total = (total[0] + delta, total[1] + volume, total[2] + pv)
        out.append(total)
    return out


def _store_prefix(
    candles: list[dict], window: ReplayWindow, start_ms: int
) -> queries.FlowTotals | None:
    """
    Return the exact stored flow over `[start_ms, the page's first bar)`: the seed of a cumulative
    mode starting at `start_ms` (a session's UTC midnight, an anchor). Covered when the page's
    first bar is at or before `start_ms` (the page holds the whole segment: zero), or when the
    store's first observed bar of the widest stored width tiling the chart's (`stored_bar`) is
    (`queries.oldest_t`, then one `queries.flow_totals` aggregate); otherwise uncovered, None: no
    `candles_dir`, no store file, no tiling width (sub-minute and 90 s charts) or a store starting
    after `start_ms`. A covered range with no known-flow bar is zero (no trade there).

    Known limit: a page older than the 1m/5m retention (`RETAIN_DAYS`), or than the store itself,
    has no prefix, so its session or anchored values are None -- never a partial sum passed off as
    whole. Upgrade path: fold the raw seconds of `[start_ms, first bar)` for the prefix.
    Known limit: like CVD `all`'s prefix, the sum covers *known* stored bars only -- a null-flow bar
    (pre-migration, until the 33-3 history rebuild) contributes nothing (audit D-161).
    Known limit: "covered" tests where the store starts, not that it is continuous: a capture outage
    inside `[start_ms, first bar)` stored no bar, so the prefix sums the observed bars around it,
    exactly as the running total carries on across an outage inside the page itself (audit D-187).
    Upgrade path: check the range against the capture's recorded gaps (`archive.application.
    rebuild_day.coverage`'s gap markers) and return None over one.
    """
    if not candles or candles[0]["t"] <= start_ms:
        return _ZERO_TOTALS
    width = stored_bar(window.bar_seconds)
    if window.candles_dir is None or width is None:
        return None
    iid = window.instrument_id
    with queries.open_store(window.candles_dir, venue_of(iid)) as db:
        if db is None:
            return None
        first = queries.oldest_t(db, iid, width, traded_only=False)
        if first is None or first > start_ms:
            return None
        totals = queries.flow_totals(db, iid, width, candles[0]["t"], since_ms=start_ms)
    return _ZERO_TOTALS if totals is None else totals


def _session_seeds(
    candles: list[dict], window: ReplayWindow
) -> dict[int, queries.FlowTotals | None]:
    """
    Bar index -> seed of each UTC-day session (`bucket_start_ms(t, 86_400)`) in the page: the first
    session from the store prefix (`_store_prefix`), every later one from zero, since the page holds
    it from its midnight. At 1D and wider every bar is its own session.
    """
    seeds: dict[int, queries.FlowTotals | None] = {}
    session: int | None = None
    for i, candle in enumerate(candles):
        day = bucket_start_ms(candle["t"], _DAY_SECONDS)
        if day != session:
            seeds[i] = _store_prefix(candles, window, day) if session is None else _ZERO_TOTALS
            session = day
    return seeds


def _cvd_replay(
    candles: list[dict],
    params: dict[str, Any],
    window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """
    Per-candle cumulative volume delta, `Σ(buy_v - sell_v)` of the bars' own stored order flow
    (Story 33.3, `docs/DATA_DICTIONARY.md` §2.15): no raw-second replay, so every bar of every
    width has a value, live or historical. The sum is exact, in integer units rescaled to the
    finest `size_precision` present (`10**k`), and becomes a float only in the output.

    `anchor` picks where the sum starts:
    - `visible`: 0 before the first candle given (net flow within the current view; panning moves
      where it restarts);
    - `session`: 0 at every UTC day's midnight (`bucket_start_ms(t, 86_400)`), so on 1D and wider
      every bar is its own session. A page starting after its first session's midnight is seeded
      with the stored bars since that midnight (`_store_prefix`, Story 33.6: before it the sum
      restarted at the page's first bar, audit D-186); a prefix the store does not cover leaves that
      session's bars None, never a partial sum;
    - `all`: the stored bars before the first candle (`queries.flow_totals` at the widest
      stored width dividing `bar_seconds`) plus the running total. Known limit: the store keeps
      1m bars 30 days and 5m bars 90 (`RETAIN_DAYS`), so at 1m/5m/10m "all" starts at that
      retention edge, not at the first trade ever (audit D-159); upgrade path: a per-day delta
      table kept past the bars' retention.

    A bar whose flow is null (a pre-migration bar) gets None, never 0 (DATA-01), and the running
    total carries on past it. Unrelated to `ofi_strategy.py`'s 5-minute rolling "cum_delta".

    Known limit (null-flow bars): the total carries on *without* a null bar's term (the contract's
    rule: the value is None, the sum is not reset), so every level after a pre-migration gap omits
    that bar's delta until the operator's history rebuild (`docs/DEPLOY_CHECKLIST.md`, 33-3) fills
    it; the gap itself shows as None, never as a fabricated value. Upgrade path: none needed once
    the rebuild has run; a stored-null bar after it is a DATA-02 question.
    Known limit (`all`): its prefix sums the *known* stored bars only -- a null-flow bar before the
    window contributes nothing to it, the same omission as above. Upgrade path: the same rebuild.
    Known limit (older than the store): a page whose bars are older than the store's first bar of
    that width (the `raw_1s` history the store never held, or pruned 1m/5m) has no stored prefix,
    so `all` accumulates from 0 at the page's first bar, as `visible` does (audit D-159). Upgrade
    path: the per-day delta table above.
    """
    _check_cvd_params(params)
    seeds = _cvd_seeds(candles, params["anchor"], window)
    scale = _scale_of(candles, seeds.values())
    running = _running_totals(candles, seeds, scale)
    return {"value": [None if r is None else r[0] / 10**scale.size for r in running]}


def _cvd_seeds(
    candles: list[dict], anchor: str, window: ReplayWindow
) -> dict[int, queries.FlowTotals | None]:
    if anchor == "session":
        return _session_seeds(candles, window)
    if anchor == "all":
        return {0: _cvd_prefix(candles, window) or _ZERO_TOTALS}
    return {0: _ZERO_TOTALS}


def _cvd_prefix(candles: list[dict], window: ReplayWindow) -> queries.FlowTotals | None:
    """Return the exact stored flow before the first candle, or None."""
    if window.candles_dir is None:
        raise ValueError("CVD anchor 'all' needs the candle store (no candles_dir given)")
    if not candles:
        return None
    width = stored_bar(window.bar_seconds)
    if width is None:
        raise ValueError(
            f"no stored bar width divides {window.bar_seconds} s: CVD 'all' cannot anchor"
        )
    with queries.open_store(window.candles_dir, venue_of(window.instrument_id)) as db:
        if db is None:
            return None
        return queries.flow_totals(db, window.instrument_id, width, candles[0]["t"])


def _check_cvd_params(params: dict[str, Any]) -> None:
    if params.get("anchor") not in CVD_ANCHORS:
        raise ValueError(
            f"anchor={params.get('anchor')!r} is not one of the choices {list(CVD_ANCHORS)}"
        )


CUSTOM_INDICATOR_CATALOG["CumulativeVolumeDelta"] = CustomIndicatorSpec(
    params={"anchor": "visible"},
    panel="oscillator",
    replay=_cvd_replay,
    outputs=("value",),
    check_params=_check_cvd_params,
    choices={"anchor": list(CVD_ANCHORS)},
    units={"value": "size"},
)


def _order_book_deltas(window: ReplayWindow) -> list[OrderBookDelta]:
    """
    Fetch this window's OrderBookDelta rows from the catalog, sorted by ts_init --
    same catalog-query pattern chart_data.py's own replay loop already uses.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(_CATALOG_PATH)
    deltas = catalog.order_book_deltas(
        instrument_ids=[window.instrument_id],
        start=window.start_ms * 1_000_000,
        end=window.end_ms * 1_000_000,
    )
    return sorted(deltas, key=lambda d: d.ts_init)


# Cap on how many consecutive empty buckets forward-fill will carry a value across before
# giving up and reporting None again -- forward-filling a *quiet* market is correct (the
# level genuinely hasn't changed), but forward-filling forever across a real ingestion outage
# would render a stale reading as confidently current, which is exactly what DATA-01 forbids.
# Known limit: a flat bucket-count cap, not a time-aware one -- revisit if a real outage shorter
# than this many buckets still reads as a false "live" value in practice.
_MAX_FORWARD_FILL_BUCKETS = 10


def _cancel_pressure_replay(
    candles: list[dict],
    params: dict[str, Any],
    window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """
    Per-candle bid/ask cancel pressure, forward-filled: within a candle's bucket, the
    tracker's state as of the LAST delta processed becomes that candle's value; a bucket with
    no delta events carries forward the last known value, up to `_MAX_FORWARD_FILL_BUCKETS`
    (DATA-01 -- a real ingestion gap must eventually read as unknown again, not confidently
    stale forever). Unlike CVD's running-cumulative sum (a flow, never forward-filled),
    cancel pressure is a *level* -- the book's current cancellation-pressure state -- so
    persisting the last real observation across a quiet bucket is the metric's own correct
    behavior, not fabrication. Candles before the first delta is processed are None (real
    warm-up, same as any other indicator).

    A BookAction.CLEAR (platform/CLAUDE.md DATA-03: typically a forced resync, a destructive
    worst-case recovery) resets the tracker's rolling window to empty -- its immediate
    post-clear rate() is a meaningless (0.0, 0.0), not a real neutral reading, so that bucket
    is recorded as an explicit reset rather than a sample: forward-fill breaks there instead
    of treating it as genuine data or silently continuing the pre-clear value.

    Reuses book_features.CancellationTracker unchanged -- no new cancellation math (DESIGN-02).
    Historical only (Story 10.2): the old fixed row was never live.
    """
    if window.start_ms is None or window.end_ms is None:
        none_col: list[float | None] = [None] * len(candles)
        return {"bid_pressure": none_col, "ask_pressure": list(none_col)}
    from nautilus_trader.model.book import OrderBook
    from nautilus_trader.model.enums import BookAction
    from nautilus_trader.model.enums import BookType
    from nautilus_trader.model.identifiers import InstrumentId

    book = OrderBook(InstrumentId.from_str(window.instrument_id), BookType.L2_MBP)
    tracker = CancellationTracker(window=params["window"])
    # None value = explicit reset (a CLEAR happened in this bucket); absent key = no event at
    # all in this bucket (an ordinary gap, still eligible for bounded forward-fill).
    bucket_samples: dict[int, tuple[float, float] | None] = {}
    for delta in _order_book_deltas(window):
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        best_bid_p = best_bid.as_double() if best_bid else None
        best_ask_p = best_ask.as_double() if best_ask else None
        tracker.update(delta, best_bid_p, best_ask_p)
        book.apply_delta(delta)
        bucket = _bucket_ns(delta.ts_event, window.bar_seconds)
        if delta.action == BookAction.CLEAR:
            bucket_samples[bucket] = None
            continue
        rate = tracker.rate()
        bucket_samples[bucket] = (rate.bid_pressure, rate.ask_pressure)

    bid_out: list[float | None] = []
    ask_out: list[float | None] = []
    last_bid: float | None = None
    last_ask: float | None = None
    gap_buckets = 0
    for candle in candles:
        key = candle["t"] * 1_000_000
        if key in bucket_samples:
            sample = bucket_samples[key]
            last_bid, last_ask = sample if sample is not None else (None, None)
            gap_buckets = 0
        else:
            gap_buckets += 1
            if gap_buckets > _MAX_FORWARD_FILL_BUCKETS:
                last_bid = last_ask = None
        bid_out.append(last_bid)
        ask_out.append(last_ask)
    return {"bid_pressure": bid_out, "ask_pressure": ask_out}


def _check_positive_window(params: dict[str, Any]) -> None:
    """
    `window` sizes the replay's rolling deque (`CancellationTracker`, `OrderFlowImbalance`): a
    non-integer, zero or negative one cannot build a window at all.
    """
    window = params.get("window")
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise ValueError(f"window must be a positive integer, got {window!r}")


CUSTOM_INDICATOR_CATALOG["CancelPressure"] = CustomIndicatorSpec(
    params={"window": 200},
    panel="histogram",
    replay=_cancel_pressure_replay,
    outputs=("bid_pressure", "ask_pressure"),
    check_params=_check_positive_window,
)


def _ofi_bucket_samples(window: ReplayWindow, ofi_window: int) -> dict[int, float]:
    """
    Replay `_order_book_deltas(window)` and return last-value-in-bucket `OrderFlowImbalance`
    samples, keyed by bucket start (ns). Drives the indicator exactly as chart_data.py's
    retired fixed row did -- `book.apply_delta(delta)` FIRST, then read post-delta top-of-book,
    skipping the delta if either side is `None`, THEN `ofi.update_raw(...)` (DESIGN-02:
    unchanged reuse). This call order is the opposite of Cancel Pressure's
    `CancellationTracker.update`, which needs PRE-delta best prices -- do not conflate the two.
    """
    from kernel.indicators import OrderFlowImbalance

    from nautilus_trader.model.book import OrderBook
    from nautilus_trader.model.enums import BookType
    from nautilus_trader.model.identifiers import InstrumentId

    book = OrderBook(InstrumentId.from_str(window.instrument_id), BookType.L2_MBP)
    ofi = OrderFlowImbalance(window=ofi_window)
    bucket_samples: dict[int, float] = {}
    for delta in _order_book_deltas(window):
        book.apply_delta(delta)
        bid_price = book.best_bid_price()
        ask_price = book.best_ask_price()
        if bid_price is None or ask_price is None:
            continue
        ofi.update_raw(
            bid_price.as_double(),
            book.best_bid_size().as_double(),
            ask_price.as_double(),
            book.best_ask_size().as_double(),
        )
        if not ofi.initialized:
            continue
        bucket = _bucket_ns(delta.ts_event, window.bar_seconds)
        bucket_samples[bucket] = ofi.value
    return bucket_samples


def _ofi_replay(
    candles: list[dict],
    params: dict[str, Any],
    window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """
    Per-candle top-of-book Order Flow Imbalance, last-value-in-bucket and forward-filled
    (bounded by `_MAX_FORWARD_FILL_BUCKETS`, same DATA-01 reasoning as Cancel Pressure, Story
    10.3): `OrderFlowImbalance.value` is a continuously-recomputed trailing rolling-window sum,
    not a per-bucket flow, so persisting the last sampled value across a quiet bucket reflects
    real state, not fabrication. `None` before `ofi.initialized` first becomes True (real
    warm-up) and before the first delta is processed at all.

    Reuses `_order_book_deltas` (Story 10.3) -- OFI is the second, not third, book-delta
    consumer this module now shares that helper with (DESIGN-01: no further extraction needed).
    Historical only, same reasoning as Cancel Pressure: the old fixed row was never live.
    """
    if window.start_ms is None or window.end_ms is None:
        return {"value": [None] * len(candles)}
    bucket_samples = _ofi_bucket_samples(window, params["window"])

    out: list[float | None] = []
    last_value: float | None = None
    gap_buckets = 0
    for candle in candles:
        key = candle["t"] * 1_000_000
        if key in bucket_samples:
            last_value = bucket_samples[key]
            gap_buckets = 0
        else:
            gap_buckets += 1
            if gap_buckets > _MAX_FORWARD_FILL_BUCKETS:
                last_value = None
        out.append(last_value)
    return {"value": out}


CUSTOM_INDICATOR_CATALOG["OrderFlowImbalance"] = CustomIndicatorSpec(
    params={"window": 20},
    panel="oscillator",
    replay=_ofi_replay,
    outputs=("value",),
    check_params=_check_positive_window,
)


# -- Story 33.6: order-flow indicators from the bars' stored aggregates -------------------------
# Every entry below reads the candle dicts' Story 33.3 columns (`candles.domain.fold.AGGREGATE_KEYS`,
# `docs/DATA_DICTIONARY.md` §2.15), never a raw second, except `DepthWithinBps`. The formulas are
# `kernel.indicators`' (SSOT-01); a value becomes a float only at the output (DATA-04). A bar whose
# flow is null (pre-migration) is None, and an organic/forced figure is None on a bar whose
# liquidations are null (no feed: spot, Hyperliquid, dYdX; or before the feed's start), never 0
# (DATA-01). None, never an error, for every ordinary condition: the Technicals replay one window
# for every coin, and one raising column fails them all (`ranking_columns._latest_of_group`).


def _flow_values(
    candles: list[dict], value_of: Callable[[dict], float | None]
) -> list[float | None]:
    """`value_of(bar)` for every bar with known flow, None for a null-flow bar."""
    return [None if c.get("buy_v") is None else value_of(_whole_flow(c)) for c in candles]


def _whole_flow(candle: dict) -> dict:
    """
    Return `candle` once its known flow group (`FLOW_KEYS`) and precisions are whole. Impossible
    otherwise by the fold (the group and its precisions are null together); a corrupt row is named,
    never a bare `TypeError` (DATA-07), as `_bar_totals` does.
    """
    _require_whole(candle, (*FLOW_KEYS, *PRECISION_KEYS), "buy_v")
    return candle


def _require_whole(candle: dict, group: tuple[str, ...], known: str) -> None:
    missing = [key for key in group if candle.get(key) is None]
    if missing:
        raise ValueError(
            f"bar t={candle['t']}: known {known} but a null {', '.join(missing)} (a corrupt row)"
        )


def _size(units: int, candle: dict) -> float:
    return units / 10 ** candle["size_precision"]


def _liquidations_known(candle: dict) -> bool:
    """Return whether the bar's liquidation group (`LIQUIDATION_KEYS`) is known; a partial raises."""
    if candle.get("liq_long_v") is None:
        return False
    _require_whole(candle, LIQUIDATION_KEYS, "liq_long_v")
    return True


def _volume_delta_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """Per bar `buy_v - sell_v` (the bar's own delta, `kernel.indicators.volume_delta`'s per bar)."""
    return {"value": _flow_values(candles, lambda c: _size(c["buy_v"] - c["sell_v"], c))}


def _organic(candle: dict) -> float | None:
    if not _liquidations_known(candle):
        return None
    units = organic_delta_units(
        candle["buy_v"], candle["sell_v"], candle["liq_long_v"], candle["liq_short_v"]
    )
    return _size(units, candle)


def _organic_delta_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    Per bar the delta without its forced flow, `(buy_v - liq_short_v) - (sell_v - liq_long_v)`
    (`kernel.indicators.organic_delta_units`: a long liquidation is a forced sell, a short one a
    forced buy). None where the bar's liquidations are null.
    """
    return {"value": _flow_values(candles, _organic)}


def _forced_share(candle: dict) -> float | None:
    if not _liquidations_known(candle):
        return None
    sp = candle["size_precision"]
    forced = candle["liq_long_v"] + candle["liq_short_v"]
    return units_ratio(forced, sp, candle["buy_v"] + candle["sell_v"], sp)


def _forced_share_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    Per bar `(liq_long_v + liq_short_v) / (buy_v + sell_v)`: the share of the traded volume that was
    forced. None at 0 volume and where the liquidations are null.

    Known limit: never clamped, so it can exceed 1 (DATA-07): the liquidations come from Bybit's
    `allLiquidation` socket and the volume from the trade feed, two streams stamped apart, so a
    liquidation can land in the bar before or after the trades that filled it, and a trade the
    feed missed (a recorded gap) lowers the denominator. Upgrade path: match each liquidation to
    its fill trades (`verification/liquidations.py`'s matcher) and fold the matched volume.
    """
    return {"value": _flow_values(candles, _forced_share)}


def _check_split(params: dict[str, Any]) -> None:
    if not isinstance(params.get("split"), bool):
        raise ValueError(f"split must be a boolean, got {params.get('split')!r}")


def _trade_count_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    Per bar the trade count `buy_n + sell_n` (`value`), or with `split` the buys (`buys`, up) and
    the sells (`sells`, drawn down as a negative count).
    """
    _check_split(params)
    if not params["split"]:
        return {"value": _flow_values(candles, lambda c: float(c["buy_n"] + c["sell_n"]))}
    return {
        "buys": _flow_values(candles, lambda c: float(c["buy_n"])),
        "sells": _flow_values(candles, lambda c: -float(c["sell_n"])),
    }


def _average_trade_size_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """Per bar `(buy_v + sell_v) / (buy_n + sell_n)`, exact; None for a bar with no trade."""

    def average(c: dict) -> float | None:
        volume = c["buy_v"] + c["sell_v"]
        return units_ratio(volume, c["size_precision"], c["buy_n"] + c["sell_n"], 0)

    return {"value": _flow_values(candles, average)}


VWAP_MODES = ("bar", "session")


def _check_vwap_mode(params: dict[str, Any]) -> None:
    if params.get("mode") not in VWAP_MODES:
        raise ValueError(
            f"mode={params.get('mode')!r} is not one of the choices {list(VWAP_MODES)}"
        )


def _vwap_values(
    candles: list[dict], seeds: dict[int, queries.FlowTotals | None]
) -> list[float | None]:
    """Return the running `Σpv / ΣV` of `_running_totals`, exact until `bar_vwap`'s one float."""
    scale = _scale_of(candles, seeds.values())
    running = _running_totals(candles, seeds, scale)
    return [None if r is None else bar_vwap(r[2], r[1], scale.price) for r in running]


def _stored_vwap_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    Return the volume-weighted price from the bars' stored `pv` and volume (`bar_vwap`):
    `bar`, each bar's own `pv / V`; `session`, the cumulative `Σpv / ΣV` from each UTC midnight
    (at 1D and wider every bar is its own session), seeded from the store as CVD's `session` is
    and carrying across a null-flow bar. None at 0 volume and over an uncovered session prefix.

    Known limit: `pv` weights each second's traded volume at that second's close (the second-close
    VWAP, `candles.domain.fold`), not every trade at its own price, so it can differ from a
    per-trade VWAP inside a second that swept several levels (audit D-190). Upgrade path: the
    fold's own, Story 32.8's raw trade reader summing `price x size` per trade.
    """
    _check_vwap_mode(params)
    if params["mode"] == "bar":
        return {"value": _vwap_values(candles, dict.fromkeys(range(len(candles)), _ZERO_TOTALS))}
    return {"value": _vwap_values(candles, _session_seeds(candles, window))}


def _check_anchor_t(params: dict[str, Any]) -> None:
    """`anchor_t` is epoch ms as a string of at most 15 ASCII digits (a drawing's bar time)."""
    anchor = params.get("anchor_t")
    if not (
        isinstance(anchor, str) and anchor.isascii() and anchor.isdigit() and len(anchor) <= 15
    ):
        raise ValueError(f"anchor_t must be epoch milliseconds as a digit string, got {anchor!r}")


def _anchored_vwap_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    Return the stored-source Anchored VWAP drawing's line (unlisted: never offered by the picker): the
    cumulative `Σpv / ΣV` from the bar holding `anchor_t` (`bucket_start_ms` at the chart's width, the
    bar the drawing's handle snaps to, so the stored line starts where the hlc3/close/ohlc4 ones do),
    None before it, seeded with the stored bars between that bar and the page's first bar when the
    page starts after it. An anchor the
    store does not reach (older than its first bar of the width, or no store) raises `ValueError`
    naming both times, the drawing's legend error -- never a sum from the page start passed off as
    one from the anchor. Same second-close `pv` Known limit as `StoredVWAP`.
    """
    _check_anchor_t(params)
    anchor_ms = bucket_start_ms(int(params["anchor_t"]), window.bar_seconds)
    first = next((i for i, c in enumerate(candles) if c["t"] >= anchor_ms), None)
    if first is None:
        return {"value": [None] * len(candles)}
    prefix = _store_prefix(candles, window, anchor_ms)
    if prefix is None:
        raise ValueError(
            f"no exact stored sum from the anchor {_iso_ms(anchor_ms)}: {_store_start(window)}"
        )
    return {"value": _vwap_values(candles, {first: prefix})}


def _iso_ms(ms: int) -> str:
    """Epoch ms as UTC ISO text (`2026-10-06T10:00:00Z`), what a legend error shows a reader."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store_start(window: ReplayWindow) -> str:
    """Return why the store cannot seed an anchor's sum, naming its first bar when it has one."""
    width = stored_bar(window.bar_seconds)
    if width is None:
        return f"no stored bar width tiles a {window.bar_seconds} s chart"
    if window.candles_dir is None:
        return "no candle store"
    with queries.open_store(window.candles_dir, venue_of(window.instrument_id)) as db:
        first = (
            None
            if db is None
            else queries.oldest_t(db, window.instrument_id, width, traded_only=False)
        )
    if first is None:
        return f"the candle store holds no {width} s bar"
    return f"the candle store's first {width} s bar is {_iso_ms(first)}"


MAX_DEPTH_BPS = 1000.0


def _check_bps(params: dict[str, Any]) -> None:
    bps = params.get("bps")
    if isinstance(bps, bool) or not isinstance(bps, int | float) or not 0 < bps <= MAX_DEPTH_BPS:
        raise ValueError(f"bps must be a number above 0 and at most {MAX_DEPTH_BPS:g}, got {bps!r}")


def _last_snapshot_per_bar(
    candles: list[dict], window: ReplayWindow, read_from_ms: int, end_ms: int
) -> list[int | None]:
    """
    Each bar's last snapshot `ts_event` in `[t, t + bar)` at or after `read_from_ms`, or None:
    pass one of the two-pass read (`kernel.catalog_files.query_snapshot_times`, one column).
    """
    times = catalog_files.query_snapshot_times(
        _CATALOG_PATH,
        window.instrument_id,
        read_from_ms * 1_000_000,
        end_ms * 1_000_000 - 1,
        on_foreign=error_ledger.record,
    )
    bar_ms = window.bar_seconds * 1000
    picks: list[int | None] = []
    for candle in candles:
        index = int(np.searchsorted(times, (candle["t"] + bar_ms) * 1_000_000)) - 1
        inside = index >= 0 and times[index] >= candle["t"] * 1_000_000
        picks.append(int(times[index]) if inside else None)
    return picks


def _depth_at(book: dict[str, Any] | None, bps: float) -> tuple[float | None, float | None]:
    """Return the bid and ask size within `bps` of one book's mid; None for no book or past it."""
    profile = None if book is None else snapshot_depth(book, BOOK_DEPTH)
    if profile is None:
        return None, None
    bids, asks = depth_within_bps(profile, [bps])
    return _finite(bids[0]), _finite(asks[0])


def _finite(value: float) -> float | None:
    return None if math.isnan(value) else value


def _depth_within_bps_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    Per bar the resting size within `bps` of the mid on each side (`bid`, `ask`) of the bar's last
    stored 1 s book in `[t, t + bar)` (`kernel.indicators.snapshot_depth` -> `depth_within_bps`):
    the one entry reading raw seconds. None for a bar with no snapshot, an edge past the stored
    20 levels (NaN), the live window and every bar ending before the cap.

    Known limit: the read is capped at `MAX_QUERY_SPAN_SECONDS` (7 days) back from the window's end,
    applied here because the Technicals pass an uncapped window, so at 1D only the last 7 bars and
    at 1W the last one carry a value, and a bar straddling the cap reads only its seconds after it.
    The read is two passes, the stamps then only the chosen rows' books (MEM-01). The last snapshot
    is a point sample: depth that came and went inside the bar is not shown. Upgrade path: a stored
    per-bar depth aggregate in the candle fold.
    Known limit (cost): as a Technicals column only the newest closed bar is kept, yet every bar of
    the capped window is read -- at 1D, up to 7 daily snapshot files' book columns per ranked coin
    per 90 s cache miss (`query_books_at` reads a file one at a time, row groups pruned by the
    stamps). Upgrade path: a latest-bar-only window from `ranking_columns.technicals_values`, or the
    stored per-bar depth above.
    """
    _check_bps(params)
    nothing: list[float | None] = [None] * len(candles)
    if window.start_ms is None or window.end_ms is None or not candles:
        return {"bid": nothing, "ask": list(nothing)}
    read_from = max(window.start_ms, window.end_ms - MAX_QUERY_SPAN_SECONDS * 1000)
    picks = _last_snapshot_per_bar(candles, window, read_from, window.end_ms)
    stamps = [p for p in picks if p is not None]
    books = catalog_files.query_books_at(
        _CATALOG_PATH, window.instrument_id, stamps, on_foreign=error_ledger.record
    )
    depths = [_depth_at(None if p is None else books.get(p), params["bps"]) for p in picks]
    return {"bid": [d[0] for d in depths], "ask": [d[1] for d in depths]}


CUSTOM_INDICATOR_CATALOG["VolumeDelta"] = CustomIndicatorSpec(
    params={},
    panel="histogram",
    replay=_volume_delta_replay,
    outputs=("value",),
    units={"value": "size"},
)
CUSTOM_INDICATOR_CATALOG["OrganicDelta"] = CustomIndicatorSpec(
    params={},
    panel="histogram",
    replay=_organic_delta_replay,
    outputs=("value",),
    units={"value": "size"},
)
CUSTOM_INDICATOR_CATALOG["ForcedShare"] = CustomIndicatorSpec(
    params={},
    panel="histogram",
    replay=_forced_share_replay,
    outputs=("value",),
    units={"value": "ratio"},
)
CUSTOM_INDICATOR_CATALOG["TradeCount"] = CustomIndicatorSpec(
    params={"split": False},
    panel="histogram",
    replay=_trade_count_replay,
    outputs=("value", "buys", "sells"),
    check_params=_check_split,
    units={"value": "count", "buys": "count", "sells": "count"},
)
CUSTOM_INDICATOR_CATALOG["AverageTradeSize"] = CustomIndicatorSpec(
    params={},
    panel="oscillator",
    replay=_average_trade_size_replay,
    outputs=("value",),
    units={"value": "size_mean"},
)
CUSTOM_INDICATOR_CATALOG["StoredVWAP"] = CustomIndicatorSpec(
    params={"mode": "session"},
    panel="overlay",
    replay=_stored_vwap_replay,
    outputs=("value",),
    check_params=_check_vwap_mode,
    choices={"mode": list(VWAP_MODES)},
    units={"value": "price"},
)
# Unlisted: the stored-source Anchored VWAP drawing's series, never a picker entry. Its default
# `anchor_t` "0" (the epoch) only satisfies `check_params`; the drawing always sends its own bar time.
CUSTOM_INDICATOR_CATALOG["AnchoredStoredVWAP"] = CustomIndicatorSpec(
    params={"anchor_t": "0"},
    panel="overlay",
    replay=_anchored_vwap_replay,
    outputs=("value",),
    check_params=_check_anchor_t,
    units={"value": "price"},
    listed=False,
)
CUSTOM_INDICATOR_CATALOG["DepthWithinBps"] = CustomIndicatorSpec(
    params={"bps": 10.0},
    panel="oscillator",
    replay=_depth_within_bps_replay,
    outputs=("bid", "ask"),
    check_params=_check_bps,
    units={"bid": "size", "ask": "size"},
)


# -- Story 33.11: `kernel.ta` entries the native shape cannot carry ------------------------------
# Supertrend splits one value into two outputs by its direction, PivotPoints is seeded from the
# store, and ZigZag is sparse: each drives its `kernel.ta` class over the candles, as `replay_native`
# would (DESIGN-02: no indicator math here). A candle missing a price is a gap: not fed, None.


def _hlc_of(candle: dict) -> tuple[float, float, float] | None:
    values = (candle.get("h"), candle.get("l"), candle.get("c"))
    if any(v is None for v in values):
        return None
    return values  # type: ignore[return-value]


def _constructs(cls: Callable[..., object]) -> Callable[[dict[str, Any]], None]:
    """Return a `check_params` that builds `cls` from the merged params: its own checks decide."""

    def check(params: dict[str, Any]) -> None:
        cls(**params)

    return check


def _supertrend_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    `kernel.ta.Supertrend` split by direction: `up` carries the line on the bars of an up trend
    (+1), `down` on those of a down trend (-1), each None elsewhere, so a flip at bar i ends `down`
    at i - 1 and starts `up` at i (the two colours of TradingView's line).
    """
    supertrend = Supertrend(**params)
    up: list[float | None] = []
    down: list[float | None] = []
    for candle in candles:
        hlc = _hlc_of(candle)
        if hlc is not None:
            supertrend.update_raw(*hlc)
        value = supertrend.value if hlc is not None and supertrend.initialized else None
        up.append(value if supertrend.direction == 1 else None)
        down.append(value if supertrend.direction == -1 else None)
    return {"up": up, "down": down}


PIVOT_SESSIONS: dict[str, int] = {"D": _DAY_SECONDS, "W": 604_800}


def _check_pivot_params(params: dict[str, Any]) -> None:
    if params.get("kind") not in PIVOT_KINDS:
        raise ValueError(
            f"kind={params.get('kind')!r} is not one of the choices {list(PIVOT_KINDS)}"
        )
    if params.get("session") not in PIVOT_SESSIONS:
        raise ValueError(
            f"session={params.get('session')!r} is not one of the choices {list(PIVOT_SESSIONS)}"
        )


def _pivot_session_seconds(session: str, bar_seconds: int) -> int:
    """
    Return the session's width, refusing one a bar does not tile: narrower than the bar, or not a
    whole number of bars starting on a bar boundary. Both are `bucket_start_ms` buckets, each with
    its own anchor (a week starts on Monday, a day divisor at the epoch), so the session's anchor is
    checked against the bar's, never against the epoch: a `W` session on a 1W chart is one bar.
    """
    seconds = PIVOT_SESSIONS[session]
    if seconds < bar_seconds:
        raise ValueError(f"pivot session {session} is narrower than the {bar_seconds} s bar")
    offset = bucket_start_ms(0, seconds) - bucket_start_ms(0, bar_seconds)
    if seconds % bar_seconds or offset % (bar_seconds * 1000):
        raise ValueError(f"pivot session {session} is not a whole number of {bar_seconds} s bars")
    return seconds


def _seed_pivots_from_store(
    pivots: PivotPoints, window: ReplayWindow, previous: int, start: int, first_t: int
) -> bool:
    """
    Feed the stored session `[previous, start)` and the stored prefix `[start, first_t)` of the
    page's first session, each as one bar (`queries.session_hlc`), and return True; False when the
    store does not cover the previous session from its start (no `candles_dir`, no tiling width,
    no store file, or a store starting after `previous`): nothing is fed then.

    Known limit: "covered" tests where the store starts, not that it is continuous, as the CVD
    session seed does (audit D-187): an outage inside a session leaves its high and low from the
    bars observed around it. Upgrade path: the capture's recorded gaps, as there.
    """
    width = stored_bar(window.bar_seconds)
    if window.candles_dir is None or width is None:
        return False
    iid = window.instrument_id
    with queries.open_store(window.candles_dir, venue_of(iid)) as db:
        if db is None:
            return False
        oldest = queries.oldest_t(db, iid, width, traded_only=False)
        if oldest is None or oldest > previous:
            return False
        before = queries.session_hlc(db, iid, width, previous, start)
        prefix = queries.session_hlc(db, iid, width, start, first_t) if first_t > start else None
    session_s = (start - previous) // 1000  # `previous` and `start` are consecutive sessions
    if before is not None:
        _feed_pivots(pivots, before, previous, session_s)
    if prefix is not None:
        _feed_pivots(pivots, prefix, start, session_s)
    return True


def _feed_pivots(
    pivots: PivotPoints, hlc: tuple[float, float, float], session: int, session_s: int
) -> None:
    """
    Feed one bar of `session` (its `bucket_start_ms` key), first resetting `pivots` when the session
    does not directly follow the last one fed: a session in between had no traded (fed) bar, so the
    new session's previous one is unknown and its bars read None; the session after it is the next
    with levels (DATA-01, audit D-214).
    """
    last = pivots.session
    if last is not None and session != last:
        following = bucket_start_ms(last + session_s * 1000, session_s)
        if session != following:
            pivots.reset()
    pivots.update_raw(*hlc, session)


def _pivot_feed_start(
    pivots: PivotPoints, candles: list[dict], window: ReplayWindow, session_s: int
) -> int:
    """
    Return the first page bar to feed: 0 when the store seeds the sessions before the page or the
    page starts on a session boundary, else the first bar of the page's second session -- the bars
    before it belong to a session seen only in part, never a source of levels (DATA-01).
    """
    first_t = candles[0]["t"]
    start = bucket_start_ms(first_t, session_s)
    previous = bucket_start_ms(start - 1, session_s)
    if _seed_pivots_from_store(pivots, window, previous, start, first_t) or first_t == start:
        return 0
    later = (i for i, c in enumerate(candles) if bucket_start_ms(c["t"], session_s) != start)
    return next(later, len(candles))


def _pivot_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    `kernel.ta.PivotPoints` of `kind` over `session` (`D`/`W`, keyed by `bucket_start_ms`, the one
    bucket rule): every bar carries the nine levels of the session before its own (`r4`/`s4` None
    unless camarilla), seeded from the store when it covers that session from its start (see
    `_pivot_feed_start`; docs/DATA_DICTIONARY.md §2.19). A bar before the first level reads None,
    and so does every bar of a session whose previous session had no traded bar (`_feed_pivots`).
    A session narrower than the bar, or not tiled by it, raises `ValueError` (the entry's error).
    """
    _check_pivot_params(params)
    session_s = _pivot_session_seconds(params["session"], window.bar_seconds)
    pivots = PivotPoints(params["kind"])
    out: dict[str, list[float | None]] = {name: [] for name in PIVOT_LEVELS}
    feed_from = _pivot_feed_start(pivots, candles, window, session_s) if candles else 0
    for i, candle in enumerate(candles):
        hlc = _hlc_of(candle) if i >= feed_from else None
        if hlc is not None:
            _feed_pivots(pivots, hlc, bucket_start_ms(candle["t"], session_s), session_s)
        known = hlc is not None and pivots.initialized
        for name, level in pivots.levels().items():
            out[name].append(level if known else None)
    return out


def _zigzag_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow
) -> dict[str, list[float | None]]:
    """
    `kernel.ta.ZigZag` as sparse swing points: each confirmed pivot's price at its own bar, and,
    on the page reaching the newest stored data only (`_reaches_newest`), the last leg's running
    extreme at its bar (it repaints until a reversal confirms it, audit D-215); None everywhere
    else. The chart joins the points (`plot` `swing`), across pages too: an older page's tip is no
    swing (the newer bars continue its leg), so drawn it would be a permanent point the line joins
    to the newer page's first pivot -- a fabricated swing. For the same reason a pivot on the
    page's first fed bar is not drawn: that bar is an extreme only because the page starts there
    (the bars before it are on the older page), so it would be a swing the market never made.
    """
    zigzag = ZigZag(**params)
    out: list[float | None] = [None] * len(candles)
    candle_of_update: list[int] = []
    for i, candle in enumerate(candles):
        high, low = candle.get("h"), candle.get("l")
        if high is None or low is None:
            continue
        candle_of_update.append(i)
        zigzag.update_raw(high, low)
        if zigzag.confirmed and zigzag.pivot_bar:  # 0: the page's first fed bar, see above
            out[candle_of_update[zigzag.pivot_bar]] = zigzag.pivot_price
    if zigzag.extreme_bar is not None and _reaches_newest(window):
        out[candle_of_update[zigzag.extreme_bar]] = zigzag.extreme_price
    return {"value": out}


def _reaches_newest(window: ReplayWindow) -> bool:
    """
    Return whether the page reaches the newest stored data: the store's newest observed bucket of
    the widest stored width tiling the chart's (`stored_bar`, `queries.newest_t`) starts before
    the end of the bar after the page's last. That one bar of slack is the forming bar a
    closed-bar reader leaves out (the Technicals column and the alert reader end their page at the
    newest *closed* bar while the store already holds the forming one), so both read the tip as the
    chart's newest page does; an older chart page ends a whole newer page before the newest data.

    Known limit: sub-minute and 90 s charts (no tiling stored width) and store-less callers (no
    `candles_dir`, no store file) never know whether a newer page exists, so they draw confirmed
    pivots only, no tip. Known limit: a newer page holding only the forming bar leaves its older
    page the tip (one bar of slack); harmless, since a one-bar page has no ZigZag tip of its own.
    Upgrade path: the page reader passes `has_newer` (it knows whether it cut the page short).
    """
    width = stored_bar(window.bar_seconds)
    if window.candles_dir is None or width is None or window.end_ms is None:
        return False
    iid = window.instrument_id
    with queries.open_store(window.candles_dir, venue_of(iid)) as db:
        newest = None if db is None else queries.newest_t(db, iid, width)
    return newest is not None and newest < window.end_ms + window.bar_seconds * 1000


CUSTOM_INDICATOR_CATALOG["Supertrend"] = CustomIndicatorSpec(
    params={"period": 10, "multiplier": 3.0},
    panel="overlay",
    replay=_supertrend_replay,
    outputs=("up", "down"),
    check_params=_constructs(Supertrend),
    units={"up": "price", "down": "price"},
)
CUSTOM_INDICATOR_CATALOG["PivotPoints"] = CustomIndicatorSpec(
    params={"kind": "standard", "session": "D"},
    panel="overlay",
    replay=_pivot_replay,
    outputs=PIVOT_LEVELS,
    check_params=_check_pivot_params,
    choices={"kind": list(PIVOT_KINDS), "session": list(PIVOT_SESSIONS)},
    units=dict.fromkeys(PIVOT_LEVELS, "price"),
    plot=dict.fromkeys(PIVOT_LEVELS, "steps"),
)
CUSTOM_INDICATOR_CATALOG["ZigZag"] = CustomIndicatorSpec(
    params={"deviation_pct": 5.0},
    panel="overlay",
    replay=_zigzag_replay,
    outputs=("value",),
    check_params=_constructs(ZigZag),
    units={"value": "price"},
    plot={"value": "swing"},
    note="repaints last leg",
)


# =============================================================================================
# Dispatch: the one place both catalogs meet (was data_api/routes/indicators.py)
# =============================================================================================


class IndicatorRequest(Protocol):
    """One requested indicator: a catalog name, its params and its price source."""

    @property
    def name(self) -> str: ...

    @property
    def params(self) -> dict[str, Any]: ...

    @property
    def source(self) -> str: ...


def merged_catalog() -> dict[str, dict]:
    """
    Native + custom catalogs, each entry `{params, panel, category}` tagged with which one it came
    from.

    Tagging happens here, not inside either catalog -- the two halves of this module stay unaware
    of each other (DESIGN-02); only this dispatch knows both exist. A name registered in both
    catalogs is a real bug (whichever one the picker lists would silently disagree with the
    native-first dispatch order in `replay_entry`) -- raise immediately rather than let the two
    catalogs silently diverge (DATA-02: no mysteries).
    """
    collisions = set(INDICATOR_CATALOG) & set(CUSTOM_INDICATOR_CATALOG)
    if collisions:
        raise ValueError(f"Indicator name(s) registered in both catalogs: {sorted(collisions)}")
    merged: dict[str, dict] = {}
    for name, entry in native_catalog_json().items():
        merged[name] = {**entry, "category": "native"}
    for name, entry in custom_catalog_json().items():
        merged[name] = {**entry, "category": "custom"}
    return merged


# Known limit: one flat ceiling for every integer param (periods, windows, trend bars), not a
# per-indicator bound. It exists because some constructors allocate eagerly from a period
# (`HullMovingAverage(period=10**9)` builds its weight arrays for ~16 s) and every replay holds a
# window that large, so an unchecked integer could stall `data_api` at save time and on every
# later poll; 10_000 is far above any period the picker's bar widths make meaningful (1W bars hold
# ~190 years of history at that length). The custom indicators' `window` counts book events, not bars
# (`CancelPressure` defaults to 200, `OrderFlowImbalance` to 20): 10_000 still holds several seconds
# of a busy BTC book. Floats get a magnitude ceiling for the same reason: `k=1e308` would replay to
# `inf`, which the values JSON cannot carry. Upgrade path: a per-param `max` on `IndicatorSpec`,
# served in the catalog so the picker can bound its inputs too.
MAX_INT_PARAM = 10_000
MAX_ABS_FLOAT_PARAM = 1e6


def check_params(name: str, params: Any) -> None:
    """
    Raise `ValueError` naming the param for params `name` cannot be built with -- the one rule every
    writer of an indicator list applies before saving it (the indicators and technicals-columns
    PUTs, the layout seed/reset), so a saved selection is never one its replay refuses up front.
    Unknown keys, wrong types and out-of-range numbers are refused before anything is built; a
    native indicator is then constructed exactly as `replay_native` constructs it, a custom one runs
    its spec's check. Known limit: a failure only `update_raw` would hit is not caught here; the
    values routes still report it per request.
    """
    defaults = _default_params(name)
    if not isinstance(params, dict):
        raise ValueError(f"params must be an object, got {type(params).__name__}")
    unknown = sorted(set(params) - set(defaults))
    if unknown:
        raise ValueError(f"unknown param(s) {unknown} (expected a subset of {sorted(defaults)})")
    for key, value in params.items():
        _check_param_type(key, value, defaults[key])
    if name in INDICATOR_CATALOG:
        _check_native_params(name, INDICATOR_CATALOG[name], params)
        return
    custom = CUSTOM_INDICATOR_CATALOG[name]
    for key, allowed in custom.choices.items():
        if key in params and params[key] not in allowed:
            raise ValueError(f"{key}={params[key]!r} is not one of the choices {allowed}")
    if custom.check_params is not None:
        custom.check_params({**custom.params, **params})


def _default_params(name: str) -> dict[str, Any]:
    """Return the catalog defaults of `name` (every key it takes), with the dispatch's guards."""
    in_native = name in INDICATOR_CATALOG
    in_custom = name in CUSTOM_INDICATOR_CATALOG
    if in_native and in_custom:
        raise ValueError(f"Indicator name registered in both catalogs: {name!r}")
    if in_native:
        return INDICATOR_CATALOG[name].params
    if in_custom:
        return CUSTOM_INDICATOR_CATALOG[name].params
    raise ValueError(f"Unknown indicator: {name!r}")


def _check_param_type(key: str, value: Any, default: Any) -> None:
    """
    Refuse a numeric or boolean value not of its catalog default's type (an int default takes no
    bool, a float default also takes an int). Construction alone cannot catch this: a Cython `int`
    param truncates a float (`SimpleMovingAverage(period=2.5).period == 2`, so `period: 1e9` would
    slip past the integer cap) and a `bint` param takes any truthy value, so the saved value would
    not be the one replayed. A string default is an enum name: its choices are checked by the
    caller.
    """
    if isinstance(default, bool):
        ok, expected = isinstance(value, bool), "a boolean"
    elif isinstance(default, int):
        # The integer cap applies to integer params only: a float param sent as an int is bounded
        # by the float ceiling below, like the same value sent as a float.
        is_int = isinstance(value, int) and not isinstance(value, bool)
        ok, expected = is_int and value <= MAX_INT_PARAM, f"an integer at most {MAX_INT_PARAM}"
    elif isinstance(default, float):
        # Finite too: `request.json()` parses `NaN`/`Infinity`, and TOML stores them.
        # Compared as an int when it is one: `math.isfinite(10**400)` raises `OverflowError`.
        is_number = isinstance(value, int | float) and not isinstance(value, bool)
        in_range = is_number and (isinstance(value, int) or math.isfinite(value))
        ok = in_range and abs(value) <= MAX_ABS_FLOAT_PARAM
        expected = f"a finite number of magnitude at most {MAX_ABS_FLOAT_PARAM:g}"
    else:
        return
    if not ok:
        raise ValueError(f"{key}={value!r} must be {expected}")


def _check_native_params(name: str, spec: IndicatorSpec, params: dict[str, Any]) -> None:
    """
    Enum params must be one of the picker's choices (a string); then the indicator is built with the
    resolved params, so its own constructor checks (`Condition.positive_int` and the like) decide.
    """
    for key, enum_type in spec.enum_params.items():
        if key in params and params[key] not in _choices(enum_type):
            raise ValueError(
                f"{key}={params[key]!r} is not one of the choices {_choices(enum_type)}"
            )
    try:
        spec.cls(**_resolve_enum_params(spec, params))
    except Exception as exc:
        # Broad by design, as in `values_by_time`: the params are untrusted and every constructor
        # may refuse them with its own exception type; any refusal is the client's 400, never a 500.
        # The constructor's own message names the param it refuses (`'period' not a positive
        # integer, was 0`); the params themselves are not echoed back.
        raise ValueError(f"{name} cannot be built with these params: {exc}") from exc


def indicator_id(name: str, params: dict[str, Any], source: str = DEFAULT_SOURCE) -> str:
    """
    Return the series key of one configured indicator -- the registry key `LightweightChart.tsx`'s pane
    `Map` expects (AD-F4), and the prefix of every `values_by_time` series key. The source is part
    of the id only when it is not `close`, so every id that existed before sources is unchanged and
    SMA(20) on close and on hl2 are two series (`SimpleMovingAverage_period=20:hl2`).
    """
    base = (
        name if not params else name + "_" + ",".join(f"{k}={v}" for k, v in sorted(params.items()))
    )
    return base if source == DEFAULT_SOURCE else f"{base}:{source}"


def replay_entry(
    candles: list[dict],
    name: str,
    params: dict[str, Any],
    window: ReplayWindow,
    source: str = DEFAULT_SOURCE,
) -> dict[str, list[float | None]]:
    """
    Dispatch one requested indicator: native catalog first, then custom (via `ReplayWindow`) --
    same lookup order `dashboard.py`'s retired `_indicators_json` used, so a name registered in
    exactly one catalog behaves identically here.

    Checks for a name registered in both catalogs before dispatching, same guard `merged_catalog`
    applies for the picker's own listing -- without it, the values page could silently serve the
    native catalog's replay for a colliding name while the catalog listing fails loud for the
    exact same name (DATA-02: no mysteries).
    """
    in_native = name in INDICATOR_CATALOG
    in_custom = name in CUSTOM_INDICATOR_CATALOG
    if in_native and in_custom:
        raise ValueError(f"Indicator name registered in both catalogs: {name!r}")
    if in_native:
        return replay_native(candles, name, params, source)
    if in_custom:
        check_source(name, source)  # a custom indicator keeps its fixed input: non-close refuses
        return replay_custom(candles, name, params, window)
    raise ValueError(f"Unknown indicator: {name}")


def values_by_time(
    candles: list[dict],
    entries: Sequence[IndicatorRequest],
    window: ReplayWindow,
) -> tuple[dict[int, dict[str, float | None]], dict[str, str]]:
    """
    Replay every requested entry over the same bounded candle window and merge into one
    `t -> {series_key: value}` mapping -- every entry shares the identical candle list, so
    their outputs are already aligned 1:1 by index. An entry that fails is reported in the
    second (`indicator_id -> message`) result and skipped, never failing the others.
    """
    by_time: dict[int, dict[str, float | None]] = {c["t"]: {} for c in candles}
    errors: dict[str, str] = {}
    for entry in entries:
        series_id = indicator_id(entry.name, entry.params, entry.source)
        try:
            outputs = replay_entry(candles, entry.name, entry.params, window, entry.source)
        except Exception as exc:
            # Broad by design (DATA-02): params are untrusted and every current and future
            # indicator's replay may raise something different (e.g. period=0).
            errors[series_id] = str(exc)
            continue
        for attr, values in outputs.items():
            key = f"{series_id}.{attr}"
            for candle, value in zip(candles, values, strict=True):
                by_time[candle["t"]][key] = value
    return by_time, errors
