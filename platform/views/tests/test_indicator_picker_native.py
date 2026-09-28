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
Unit tests for `views.indicator_picker`'s native half (was the `chart_indicators` module):
the INDICATOR_CATALOG dispatch/replay mechanism. `replay_indicator` is `replay_native`.
"""

import random

import pytest
from kernel.candle_patterns import CandlePattern
from kernel.candle_patterns import PatternName

import nautilus_trader.indicators as nt_indicators
from views.indicator_picker import INDICATOR_CATALOG
from views.indicator_picker import _resolve_enum_params
from views.indicator_picker import merged_catalog
from views.indicator_picker import native_catalog_json
from views.indicator_picker import replay_native as replay_indicator


def _candle(t: int, o: float, h: float, low: float, c: float, v: float = 1.0) -> dict:
    return {"t": t, "o": o, "h": h, "l": low, "c": c, "v": v}


def _flat_candles(closes: list[float]) -> list[dict]:
    """
    Candles with open==high==low==close for a given close series -- simplifies
    hand-computing expected indicator values against known formulas.
    """
    return [_candle(i * 60_000, c, c, c, c) for i, c in enumerate(closes)]


def test_simple_moving_average_matches_hand_computed_values() -> None:
    candles = _flat_candles([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = replay_indicator(candles, "SimpleMovingAverage", {"period": 3})
    assert result["value"] == [None, None, 2.0, 3.0, 4.0, 5.0]


def test_bollinger_bands_returns_upper_middle_lower() -> None:
    candles = _flat_candles([float(x) for x in range(1, 11)])
    result = replay_indicator(candles, "BollingerBands", {"period": 5, "k": 2.0})
    assert set(result.keys()) == {"upper", "middle", "lower"}
    assert result["middle"][-1] == 8.0  # SMA(5) of [6,7,8,9,10]
    assert result["upper"][-1] > result["middle"][-1] > result["lower"][-1]


def test_stochastics_returns_value_k_and_value_d() -> None:
    highs_lows_closes = [(float(i) + 1, float(i) - 1, float(i)) for i in range(1, 20)]
    candles = [_candle(i * 60_000, c, h, low, c) for i, (h, low, c) in enumerate(highs_lows_closes)]
    result = replay_indicator(candles, "Stochastics", {"period_k": 5, "period_d": 3})
    assert set(result.keys()) == {"value_k", "value_d"}
    assert result["value_k"][-1] is not None
    assert result["value_d"][-1] is not None


def test_warmup_none_padding_matches_indicator_initialized_transition() -> None:
    """
    None-padding must track the real indicator's own `initialized` flag, not a
    hand-computed guess at warm-up length.
    """
    closes = [float(x) for x in range(1, 8)]
    candles = _flat_candles(closes)
    result = replay_indicator(candles, "SimpleMovingAverage", {"period": 4})

    sma = nt_indicators.SimpleMovingAverage(period=4)
    expected: list[float | None] = []
    for c in closes:
        sma.update_raw(c)
        expected.append(sma.value if sma.initialized else None)

    assert result["value"] == expected


def test_catalog_entries_all_construct_and_replay_with_default_params() -> None:
    """
    Smoke-test every registered indicator: instantiable with defaults, produces one
    output-list per candle for every declared output attribute.
    """
    candles = _flat_candles([100.0 + i * 0.5 for i in range(60)])
    for name in INDICATOR_CATALOG:
        outputs = replay_indicator(candles, name, {})
        assert set(outputs.keys()) == set(INDICATOR_CATALOG[name].outputs), name
        for values in outputs.values():
            assert len(values) == len(candles), name


def test_unknown_indicator_name_raises_value_error() -> None:
    try:
        replay_indicator(_flat_candles([1.0, 2.0]), "NotARealIndicator", {})
        raised = False
    except ValueError:
        raised = True
    assert raised


# --- CandlePattern (Story 27.7): the kernel's detector through the same native path -------------


def _walk_candles(seed: int, count: int) -> list[dict]:
    """Return seeded random-walk candles whose opens gap off the previous close."""
    rng = random.Random(seed)  # noqa: S311 -- a reproducible walk, not a secret
    close = 100.0
    candles = []
    for i in range(count):
        open_ = close + rng.gauss(0, 0.4)
        close = open_ + rng.gauss(0, 1.0)
        high = max(open_, close) + abs(rng.gauss(0, 0.5))
        low = min(open_, close) - abs(rng.gauss(0, 0.5))
        candles.append(_candle(i * 60_000, open_, high, low, close))
    return candles


@pytest.mark.parametrize("pattern", list(PatternName))
def test_candle_pattern_replay_equals_the_kernel_detector(pattern: PatternName) -> None:
    candles = _walk_candles(7, 400)
    result = replay_indicator(candles, "CandlePattern", {"pattern": pattern.name})

    detector = CandlePattern(pattern)
    expected: list[float | None] = []
    for c in candles:
        detector.update_raw(c["o"], c["h"], c["l"], c["c"])
        expected.append(float(detector.value) if detector.initialized else None)
    assert result == {"value": expected}


def test_candle_pattern_replay_by_name_gives_none_then_signed_hundreds() -> None:
    values = replay_indicator(_walk_candles(8, 300), "CandlePattern", {"pattern": "HAMMER"})[
        "value"
    ]
    assert values[:4] == [None] * 4  # 1 bar + trend_bars (3) + 1 before the first verdict
    assert set(values[4:]) <= {-100.0, 0.0, 100.0}
    assert 100.0 in values[4:]


def test_the_first_bar_of_a_three_bar_pattern_replays_as_none() -> None:
    candles = [_candle(0, 12.0, 12.1, 9.9, 10.0)]
    assert replay_indicator(candles, "CandlePattern", {"pattern": "THREE_INSIDE_UP"}) == {
        "value": [None]
    }


def test_the_pattern_param_round_trips_from_its_name_to_the_enum() -> None:
    spec = INDICATOR_CATALOG["CandlePattern"]
    assert spec.params["pattern"] == "ENGULFING"
    assert _resolve_enum_params(spec, {})["pattern"] is PatternName.ENGULFING
    assert _resolve_enum_params(spec, {"pattern": "HAMMER"})["pattern"] is PatternName.HAMMER
    with pytest.raises(KeyError):
        replay_indicator(_walk_candles(1, 5), "CandlePattern", {"pattern": "NOT_A_PATTERN"})


def test_the_catalog_lists_every_enum_params_choices() -> None:
    catalog = native_catalog_json()
    assert catalog["CandlePattern"]["choices"] == {"pattern": [p.name for p in PatternName]}
    assert catalog["CandlePattern"]["panel"] == "histogram"
    assert catalog["SimpleMovingAverage"]["choices"]["price_type"][:4] == [
        "BID",
        "ASK",
        "MID",
        "LAST",
    ]
    assert catalog["RelativeStrengthIndex"]["choices"] == {}
    for name, spec in INDICATOR_CATALOG.items():
        assert set(catalog[name]["choices"]) == set(spec.enum_params), name
        for key, names in catalog[name]["choices"].items():
            assert spec.params[key] in names, (name, key)  # every default is one of its choices


def test_a_moving_average_type_the_factory_cannot_build_is_neither_offered_nor_accepted() -> None:
    assert (
        "ADAPTIVE"
        not in native_catalog_json()["MovingAverageConvergenceDivergence"]["choices"]["ma_type"]
    )
    with pytest.raises(ValueError, match="ADAPTIVE"):
        replay_indicator(
            _walk_candles(1, 40), "MovingAverageConvergenceDivergence", {"ma_type": "ADAPTIVE"}
        )


def test_the_merged_catalog_carries_the_choices_as_native() -> None:
    entry = merged_catalog()["CandlePattern"]
    assert entry["category"] == "native"
    assert len(entry["choices"]["pattern"]) == len(PatternName)


if __name__ == "__main__":
    test_simple_moving_average_matches_hand_computed_values()
    test_bollinger_bands_returns_upper_middle_lower()
    test_stochastics_returns_value_k_and_value_d()
    test_warmup_none_padding_matches_indicator_initialized_transition()
    test_catalog_entries_all_construct_and_replay_with_default_params()
    test_unknown_indicator_name_raises_value_error()
    print("ok")
