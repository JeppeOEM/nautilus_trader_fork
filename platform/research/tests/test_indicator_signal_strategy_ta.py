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
`IndicatorSignalStrategy`'s six `kernel.ta` signals (Story 33.11): each rule's direction on a
hand-built rising and a falling series of real Nautilus `Bar`s, fed as the strategy feeds them
(`handle_bar`), and the params each refuses at construction.

The series: rising closes `100 + i` with `h = c`, `l = c - 1` (every bar closes at its high, so
CLV is +1 and the typical price rises); falling closes `200 - i` with `h = c + 1`, `l = c` (the
mirror). 60 bars initialize every indicator (the Awesome Oscillator's slow SMA needs 34).
"""

from decimal import Decimal
from typing import Any

import msgspec
import pytest

from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from research.strategies.indicator_signal_strategy import _SIGNALS
from research.strategies.indicator_signal_strategy import IndicatorSignalStrategy
from research.strategies.indicator_signal_strategy import IndicatorSignalStrategyConfig
from research.strategies.indicator_signal_strategy import _merged


_BAR_TYPE = BarType.from_str("BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL")
_TA_SIGNALS = ("supertrend", "parabolic_sar", "adx", "mfi", "cmf", "awesome_oscillator")


def _bar(i: int, high: float, low: float, close: float) -> Bar:
    ts = (i + 1) * 60_000_000_000
    return Bar(
        _BAR_TYPE,
        Price(close, 1),
        Price(high, 1),
        Price(low, 1),
        Price(close, 1),
        Quantity(1, 0),
        ts,
        ts,
    )


def _rising(count: int = 60) -> list[Bar]:
    return [_bar(i, 100.0 + i, 99.0 + i, 100.0 + i) for i in range(count)]


def _falling(count: int = 60) -> list[Bar]:
    return [_bar(i, 201.0 - i, 200.0 - i, 200.0 - i) for i in range(count)]


def _last_side(signal: str, bars: list[Bar], overrides: dict[str, Any] | None = None) -> int:
    spec = _SIGNALS[signal]
    params = _merged("signal_params", spec.defaults, overrides or {})
    indicator = spec.build(params)
    for bar in bars:
        indicator.handle_bar(bar)
    assert indicator.initialized, signal
    return spec.rule(indicator, bars[-1], params)


@pytest.mark.parametrize(
    ("signal", "rising", "falling"),
    [
        ("supertrend", 1, -1),  # the band follows: up trend +1, flipped to -1 on the fall
        ("parabolic_sar", 1, -1),  # SAR below a rising close, above a falling one
        ("adx", 1, -1),  # one-sided DM: ADX 100 >= 25, the side of +DI - -DI
        ("mfi", -1, 1),  # a mean-reversion oscillator: every flow up is MFI 100 >= 80, short
        ("cmf", 1, -1),  # CLV +1 / -1 on every bar
        ("awesome_oscillator", 1, -1),  # SMA(5) of hl2 above / below SMA(34)
    ],
)
def test_each_rule_reads_the_direction_of_a_hand_built_series(
    signal: str, rising: int, falling: int
) -> None:
    assert _last_side(signal, _rising()) == rising
    assert _last_side(signal, _falling()) == falling


def test_adx_gives_no_signal_below_its_minimum_strength() -> None:
    assert _last_side("adx", _rising(), {"adx_min": 101.0}) == 0


def test_mfi_releases_at_its_neutral_level() -> None:
    spec = _SIGNALS["mfi"]
    params = _merged("signal_params", spec.defaults, {})
    indicator = spec.build(params)
    for bar in _rising():
        indicator.handle_bar(bar)
    assert spec.release is not None
    assert spec.release(indicator, _rising()[-1], params, -1) is False  # MFI 100 is above 50
    assert spec.release(indicator, _rising()[-1], params, 1) is True


@pytest.mark.parametrize("signal", _TA_SIGNALS)
def test_each_builder_casts_its_integer_params(signal: str) -> None:
    """A whole float (`14.0`, as a JSON or TOML sweep may pass it) builds like its int."""
    spec = _SIGNALS[signal]
    as_floats = {k: float(v) for k, v in spec.defaults.items()}
    by_float, by_int = spec.build(as_floats), spec.build(dict(spec.defaults))
    for bar in _rising():
        by_float.handle_bar(bar)
        by_int.handle_bar(bar)
    assert by_float.initialized
    assert spec.rule(by_float, _rising()[-1], as_floats) == spec.rule(
        by_int, _rising()[-1], dict(spec.defaults)
    )


def test_every_new_signal_is_registered_with_its_defaults() -> None:
    assert {name: dict(_SIGNALS[name].defaults) for name in _TA_SIGNALS} == {
        "supertrend": {"period": 10, "multiplier": 3.0},
        "parabolic_sar": {"step": 0.02, "max_step": 0.2},
        "adx": {"period": 14, "adx_min": 25.0},
        "mfi": {"period": 14, "low": 20.0, "high": 80.0, "neutral": 50.0},
        "cmf": {"period": 20},
        "awesome_oscillator": {"fast": 5, "slow": 34},
    }


def _strategy(signal: str, signal_params: dict[str, Any]) -> IndicatorSignalStrategy:
    config = IndicatorSignalStrategyConfig.parse(
        msgspec.json.encode(
            {
                "instrument_id": "BTCUSDT-LINEAR.BYBIT",
                "trade_size": str(Decimal("0.01")),
                "signal": signal,
                "signal_params": signal_params,
            }
        )
    )
    return IndicatorSignalStrategy(config)


@pytest.mark.parametrize(
    ("signal", "params", "message"),
    [
        ("supertrend", {"period": 0}, "period"),
        ("supertrend", {"multiplier": -1.0}, "multiplier"),
        ("parabolic_sar", {"step": 0.3}, "max_step"),
        ("adx", {"period": 2.5}, "whole number"),
        ("adx", {"strength": 20}, "unknown params"),
        ("mfi", {"low": 60.0}, "low < neutral < high"),
        ("cmf", {"period": 0}, "period"),
        ("awesome_oscillator", {"fast": 34}, "slow"),
    ],
)
def test_a_bad_param_raises_at_construction(
    signal: str, params: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _strategy(signal, params)


def test_a_good_config_builds_each_new_signal() -> None:
    for signal in _TA_SIGNALS:
        assert _strategy(signal, {})._indicator is not None, signal
