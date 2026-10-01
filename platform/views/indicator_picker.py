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
one pattern definition, shared by views, research and bots) -- no indicator math of its own
(DESIGN-02). `INDICATOR_CATALOG` maps a registered name to an `IndicatorSpec` describing how to
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
dict alone (o/h/l/c/v) doesn't carry that data.

`CUSTOM_INDICATOR_CATALOG` is filled once, at import, by the registrations below each replay: a
static table, never mutated at runtime. Per DESIGN-02, the two catalogs stay unaware of each other's contents -- the native half never reads
`CUSTOM_INDICATOR_CATALOG`, the custom half never reads `INDICATOR_CATALOG`, and they meet only in
the dispatch at the bottom of this module (`merged_catalog`, `replay_entry`), which is also where a
name registered in both catalogs fails loud. The one shared name (`Panel`) is a type alias, not a
coupling to catalog internals.
"""

import os
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Literal
from typing import Protocol

from candles.domain.fold import bucket_start_ms
from kernel.candle_patterns import CandlePattern
from kernel.candle_patterns import PatternName
from kernel.candle_patterns import Thresholds
from kernel.indicators import trade_aggregates

from nautilus_trader import indicators as _ind
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import PriceType
from views.catalog_reads import query_second_snapshots
from views.chart_series import CancellationTracker


# =============================================================================================
# Native: `nautilus_trader.indicators` (was the `chart_indicators` module)
# =============================================================================================

Panel = Literal["overlay", "oscillator", "histogram"]


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
        }
        for name, spec in INDICATOR_CATALOG.items()
    }


# =============================================================================================
# Custom: dYdX-specific indicators over the window's own rows (was the `custom_indicators` module)
# =============================================================================================

# Duplicated from dashboard.py's own module-level constant (same env var, same default) --
# this module cannot import it from there without a circular import (dashboard.py imports
# this module). One line, not worth a shared-constants module for just this (DESIGN-01) --
# `_second_snapshots` below duplicates a larger chunk (the catalog-query + CustomData-unwrap
# logic itself); that duplication is worth revisiting into a shared helper once a second
# real call site needs the identical pattern (Story 10.3/10.4 need a different one, for
# OrderBookDelta, so this may end up staying a one-off).
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
    """

    instrument_id: str
    bar_seconds: int
    start_ms: int | None
    end_ms: int | None


ReplayFn = Callable[[list[dict], dict[str, Any], ReplayWindow], dict[str, list[float | None]]]


@dataclass
class CustomIndicatorSpec:
    # JSON-safe default params (same role as IndicatorSpec.params in chart_indicators.py).
    params: dict[str, Any]
    panel: Panel
    # Computes every registered output attribute for the given candles/params/window, aligned
    # 1:1 with `candles` -- identical output contract to replay_native.
    replay: ReplayFn


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
    """`CUSTOM_INDICATOR_CATALOG` serialized for the merged `/data/indicators/catalog` response."""
    return {
        name: {"params": spec.params, "panel": spec.panel}
        for name, spec in CUSTOM_INDICATOR_CATALOG.items()
    }


def _bucket_ns(ts_ns: int, bar_seconds: int) -> int:
    """
    Return the start (ns) of the bucket a stamp falls in, keyed like the candles' `t * 1_000_000`:
    `candles.domain.fold.bucket_start_ms`, the one bucket rule (a 1W bucket starts on Monday).
    """
    return bucket_start_ms(ts_ns // 1_000_000, bar_seconds) * 1_000_000


def _second_snapshots(window: ReplayWindow) -> list[dict]:
    """Fetch this window's DydxSecondSnapshot rows from the catalog, as plain dicts."""
    snapshots = query_second_snapshots(
        _CATALOG_PATH,
        window.instrument_id,
        window.start_ms * 1_000_000,
        window.end_ms * 1_000_000,
    )
    # buy_count/sell_count are required by trade_aggregates()'s reduction below even though
    # _cvd_replay only consumes the volume totals it returns -- not dead data, just an unused
    # part of a shared function's output.
    return [
        {
            "buy_volume": s.buy_volume,
            "sell_volume": s.sell_volume,
            "buy_count": s.buy_count,
            "sell_count": s.sell_count,
            "ts_event": s.ts_event,
        }
        for s in snapshots
    ]


def _cvd_replay(
    candles: list[dict],
    params: dict[str, Any],
    window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """
    Per-candle running-cumulative buy_volume - sell_volume for the currently-requested
    window -- an unbounded, request-anchored total (resets to 0 at whichever candle happens
    to be first in the current view), not the 5-minute rolling/decaying oscillator the old
    chart_data.py row computed. This is a deliberate scope choice for the picker version (see
    epics.md Story 10.2 AC #1) -- panning/resizing the visible window changes where the sum
    restarts, so read it as "net flow within the current view," not an absolute level.

    Unrelated to ofi_strategy.py's own "cum_delta" signal (a live Strategy's independent
    5-minute rolling-window implementation) -- same name, different metric, different code.

    Historical only -- the old fixed row was never live either (it always replayed the
    date-range form's explicit window, never an in-process live buffer). Live requests get
    None for every candle, a real gap, not a fabricated value (DATA-01).

    A candle bucket with zero snapshot rows is *not* treated as "no volume" (which would
    silently paper over a genuine second-snapshot collection gap as a flat/unchanged value,
    DATA-01) -- it gets None, and the running total resumes from its last real value on the
    next bucket that does have data.
    """
    if window.start_ms is None or window.end_ms is None:
        return {"value": [None] * len(candles)}
    buckets: dict[int, list[dict]] = defaultdict(list)
    for row in _second_snapshots(window):
        buckets[_bucket_ns(row["ts_event"], window.bar_seconds)].append(row)
    running_total = 0.0
    values: list[float | None] = []
    for candle in candles:
        rows = buckets.get(candle["t"] * 1_000_000, [])
        if not rows:
            values.append(None)
            continue
        buy_vol, sell_vol, _, _ = trade_aggregates(rows)
        running_total += buy_vol - sell_vol
        values.append(running_total)
    return {"value": values}


CUSTOM_INDICATOR_CATALOG["CumulativeVolumeDelta"] = CustomIndicatorSpec(
    params={},
    panel="oscillator",
    replay=_cvd_replay,
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
    stale forever). Unlike CVD's running-cumulative sum (a flow, correctly reset per bucket),
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
    Historical only, same reasoning as CVD (Story 10.2): the old fixed row was never live.
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


CUSTOM_INDICATOR_CATALOG["CancelPressure"] = CustomIndicatorSpec(
    params={"window": 200},
    panel="histogram",
    replay=_cancel_pressure_replay,
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
    Historical only, same reasoning as CVD/Cancel Pressure: the old fixed row was never live.
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
