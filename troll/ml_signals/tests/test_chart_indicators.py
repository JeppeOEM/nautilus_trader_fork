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
"""Unit tests for chart_indicators.py's INDICATOR_CATALOG dispatch/replay mechanism."""

import nautilus_trader.indicators as nt_indicators
from ml_signals.chart_indicators import INDICATOR_CATALOG
from ml_signals.chart_indicators import replay_indicator


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


if __name__ == "__main__":
    test_simple_moving_average_matches_hand_computed_values()
    test_bollinger_bands_returns_upper_middle_lower()
    test_stochastics_returns_value_k_and_value_d()
    test_warmup_none_padding_matches_indicator_initialized_transition()
    test_catalog_entries_all_construct_and_replay_with_default_params()
    test_unknown_indicator_name_raises_value_error()
    print("ok")
