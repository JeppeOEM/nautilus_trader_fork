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
Dispatch/metadata over `nautilus_trader.indicators` -- no indicator math of its own (DESIGN-02).

`INDICATOR_CATALOG` maps a registered name to an `IndicatorSpec` describing how to build and
feed the real indicator class. `replay_indicator` instantiates it and drives it from candle
data. Six of `nautilus_trader.indicators`'s public classes are intentionally excluded (see
module bottom): `Candle*`/`FuzzyCandle` are enums/value-types, not indicators; `SpreadAnalyzer`
is tick-driven, not candle-driven; `FuzzyCandlesticks`/`Swings` produce non-float outputs that
don't fit this module's `list[float | None]` contract.
"""

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Literal

from nautilus_trader import indicators as _ind
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.enums import PriceType


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
}


# Field lookup: candle-side key each `feed` name maps to. VWAP's "timestamp" is handled
# specially in replay_indicator (converted from the candle's "t", not looked up directly).
_FEED_FIELD = {"open": "o", "high": "h", "low": "l", "close": "c", "volume": "v", "price": "c"}


def replay_indicator(
    candles: list[dict], name: str, params: dict[str, Any]
) -> dict[str, list[float | None]]:
    """
    Instantiate `name` from `INDICATOR_CATALOG` with `params`, replay it over `candles`
    in order, and return each registered output attribute as a list aligned 1:1 with
    `candles`. A candle before `indicator.initialized` becomes True maps to None.
    """
    if name not in INDICATOR_CATALOG:
        raise ValueError(f"Unknown indicator: {name!r}")
    spec = INDICATOR_CATALOG[name]
    resolved = _resolve_enum_params(spec, params)
    indicator = spec.cls(**resolved)

    out: dict[str, list[float | None]] = {attr: [] for attr in spec.outputs}
    for candle in candles:
        indicator.update_raw(*_feed_values(spec, candle))
        initialized = indicator.initialized
        for attr in spec.outputs:
            out[attr].append(float(getattr(indicator, attr)) if initialized else None)
    return out


def _resolve_enum_params(spec: IndicatorSpec, params: dict[str, Any]) -> dict[str, Any]:
    """Merge caller params over the spec's defaults, converting enum-name strings back to enums."""
    merged = {**spec.params, **params}
    for key, enum_type in spec.enum_params.items():
        value = merged.get(key)
        if isinstance(value, str):
            merged[key] = enum_type[value]
    return merged


def _feed_values(spec: IndicatorSpec, candle: dict) -> tuple[Any, ...]:
    values: list[Any] = []
    for field_name in spec.feed:
        if field_name == "timestamp":
            import datetime as _dt

            values.append(_dt.datetime.fromtimestamp(candle["t"] / 1000, tz=_dt.UTC))
        else:
            values.append(candle[_FEED_FIELD[field_name]])
    return tuple(values)


def catalog_json() -> dict[str, Any]:
    """
    `INDICATOR_CATALOG` serialized for `GET /data/indicators/catalog` -- name, params
    with JSON-safe defaults, panel classification. The single source Story 8.4's picker
    UI builds its list from.
    """
    return {
        name: {"params": spec.params, "panel": spec.panel}
        for name, spec in INDICATOR_CATALOG.items()
    }
