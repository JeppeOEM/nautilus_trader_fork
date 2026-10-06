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
`LiquidationCascade` (Story 33.14) on the spec's matrix: ten quiet minutes (one 1 000-unit LONG
every 60 s from t = 0), a 90 s burst (1 000 units every second, t = 600..689), then the decay.
`window_s=30, baseline_s=600, intensity_threshold=3, decay_ratio=0.5`, one update per second.

Hand counts: at second t of the burst the window holds the events placed in (t - 30, t], so the
rate is `count * 1000 / 30`: k + 1 events at t = 600 + k (k < 30), 30 from t = 629 to 689, then
`719 - t` from t = 690 (the entry placed at t - 30 has just expired). The EMA expectations are the
exact piecewise formula written out with `math.exp` below, divided by the bias correction
`1 - exp(-elapsed / 600)` (the EMA starts at 0 at t = 0, so its weights sum to that).

Hand transitions with the correction: the quiet baseline settles at ~16.25 units/s (the weighted
mean of 33.3 half the time, 0 the other half), so at t = 600 one entry (33.3/s) is an intensity of
only ~2.05 and the detector is initialized but not active; at t = 601 two entries (66.7/s) read
~4.09 and the episode starts. The decay drops intensity under 3 at t = 701 (600/s against ~202.0,
~2.97) before the rate is spent, so the episode stays open, inactive, until t = 705 (466.7/s <
500), which is both spent and not active: the end. Without the correction the baseline at t = 600
would be ~10.27 (63 % of the mean) and t = 600 itself would read ~3.25, a false start.
"""

import math

import pytest

from kernel.indicators import BASELINE_FLOOR
from kernel.indicators import LiquidationCascade
from kernel.liquidation import LiquidatedSide


_S = 1_000_000_000
_TAU = 600.0
_UNITS = 1_000


def _cascade() -> LiquidationCascade:
    return LiquidationCascade(window_s=30, baseline_s=600, intensity_threshold=3.0, decay_ratio=0.5)


def _feed(cascade: LiquidationCascade, second: int) -> None:
    """One update of the matrix scenario at `second`: an event or a plain advance."""
    quiet_event = second < 600 and second % 60 == 0
    if quiet_event or 600 <= second < 690:
        cascade.update_liquidation(LiquidatedSide.LONG, _UNITS, second * _S)
    else:
        cascade.advance(second * _S)


def _run_to(cascade: LiquidationCascade, last: int, first: int = 0) -> None:
    for second in range(first, last + 1):
        _feed(cascade, second)


def _ema(pieces: list[tuple[float, float]]) -> float:
    """
    Return the exact EMA of a piecewise-constant rate: `(rate, seconds)` pieces in order, from 0.
    """
    value = 0.0
    for rate, seconds in pieces:
        value = rate + (value - rate) * math.exp(-seconds / _TAU)
    return value


def _baseline(pieces: list[tuple[float, float]]) -> float:
    """
    Return the bias-corrected EMA: the EMA over its accumulated weight `1 - exp(-elapsed/tau)`.
    """
    elapsed = sum(seconds for _, seconds in pieces)
    return _ema(pieces) / (1.0 - math.exp(-elapsed / _TAU))


# Quiet: each minute 1 000 units sit in the window for 30 s (33.3 units/s), then nothing for 30 s.
_QUIET = [(_UNITS / 30, 30.0), (0.0, 30.0)] * 10
# The burst's ramp: k + 1 entries over [600 + k, 601 + k) for k = 0..28, then 30 entries from 629.
_RAMP = [((k + 1) * _UNITS / 30, 1.0) for k in range(29)]


def _decay_to(second: int) -> list[tuple[float, float]]:
    """From 629: 30 entries until 690, then `719 - t` entries over [t, t + 1)."""
    return [(30 * _UNITS / 30, 61.0)] + [((719 - t) * _UNITS / 30, 1.0) for t in range(690, second)]


def test_the_quiet_stretch_warms_up_and_never_activates() -> None:
    cascade = _cascade()
    for second in range(600):
        _feed(cascade, second)
        assert not cascade.initialized
        assert not cascade.active
        assert cascade.direction == 0
        assert cascade.episode_start_ns is None


def test_the_quiet_baseline_is_the_exact_piecewise_ema() -> None:
    cascade = _cascade()
    _run_to(cascade, 599)
    # At t = 599 the minute 540's entry expired at 570, so the last 29 s ran at rate 0.
    assert cascade.baseline == pytest.approx(_baseline([*_QUIET[:-1], (0.0, 29.0)]), rel=1e-12)


def test_the_first_burst_second_initializes_without_a_false_start() -> None:
    cascade = _cascade()
    _run_to(cascade, 600)
    # 33.3 units/s against the corrected ~16.25: intensity ~2.05 < 3. The uncorrected EMA
    # (~10.27, 63 % of the mean) would read ~3.25 and open a false episode here.
    baseline = _baseline(_QUIET)
    assert cascade.initialized
    assert cascade.baseline == pytest.approx(baseline, rel=1e-12)
    assert cascade.intensity == pytest.approx((_UNITS / 30) / baseline, rel=1e-12)
    assert cascade.intensity < 3.0 <= (_UNITS / 30) / _ema(_QUIET)
    assert not cascade.active
    assert cascade.direction == 0
    assert cascade.episode_start_ns is None


def test_the_second_burst_second_activates_falling() -> None:
    cascade = _cascade()
    _run_to(cascade, 601)
    # 66.7 units/s against ~16.30: intensity ~4.09 >= 3.
    baseline = _baseline(_QUIET + _RAMP[:1])
    assert cascade.baseline == pytest.approx(baseline, rel=1e-12)
    assert cascade.intensity == pytest.approx((2 * _UNITS / 30) / baseline, rel=1e-12)
    assert cascade.active
    assert cascade.direction == -1  # longs are being liquidated: the price is falling
    assert cascade.episode_start_ns == 601 * _S
    assert cascade.episode_direction == -1
    assert cascade.episode_notional_units == 2 * _UNITS  # the window's two entries at the start
    assert cascade.rising


def test_a_steady_rate_reads_intensity_one_right_after_the_warm_up() -> None:
    cascade = _cascade()
    for second in range(601):
        cascade.update_liquidation(LiquidatedSide.LONG, _UNITS, second * _S)
    # The rate ramps (k + 1) * 1000 / 30 over [k, k + 1) for k < 29, then holds 1000 from 29.
    pieces = [((k + 1) * _UNITS / 30, 1.0) for k in range(29)] + [(1_000.0, 571.0)]
    assert cascade.baseline == pytest.approx(_baseline(pieces), rel=1e-12)
    assert cascade.intensity == pytest.approx(1_000.0 / _baseline(pieces), rel=1e-12)
    assert cascade.intensity < 1.02  # ~1.0145: the ramp's first 29 s; uncorrected would be ~1.6


def test_the_long_rate_reaches_one_thousand_thirty_seconds_into_the_burst() -> None:
    cascade = _cascade()
    _run_to(cascade, 628)
    assert cascade.rate_long == pytest.approx(29 * _UNITS / 30)
    _feed(cascade, 629)
    assert cascade.rate_long == 1_000.0
    assert cascade.rate_short == 0.0
    assert cascade.baseline == pytest.approx(_baseline(_QUIET + _RAMP), rel=1e-12)


def test_the_peak_is_the_maximum_rate_and_holds_while_it_lasts() -> None:
    cascade = _cascade()
    _run_to(cascade, 689)
    assert cascade.peak_rate == 1_000.0
    assert cascade.rising  # at the peak
    assert cascade.episode_notional_units == 90 * _UNITS
    _feed(cascade, 690)
    assert cascade.rate_long == pytest.approx(29 * _UNITS / 30)
    assert not cascade.rising
    assert cascade.peak_rate == 1_000.0


def test_spent_latches_at_the_first_rate_under_half_the_peak() -> None:
    cascade = _cascade()
    _run_to(cascade, 704)
    assert cascade.rate_long == 500.0  # 15 entries: not below half the peak yet
    assert not cascade.spent
    _feed(cascade, 705)
    assert cascade.rate_long == pytest.approx(14 * _UNITS / 30)
    assert cascade.spent


def test_the_episode_stays_open_while_inactive_until_it_is_spent() -> None:
    cascade = _cascade()
    _run_to(cascade, 700)
    # 633.3 / ~201.0 = ~3.15: still active.
    assert cascade.intensity == pytest.approx(
        (19 * _UNITS / 30) / _baseline(_QUIET + _RAMP + _decay_to(700)), rel=1e-12
    )
    assert cascade.active
    _feed(cascade, 701)
    # 600 / ~202.0 = ~2.97: no longer active, but 600 >= half the peak, so not spent: still open.
    assert cascade.intensity == pytest.approx(
        (18 * _UNITS / 30) / _baseline(_QUIET + _RAMP + _decay_to(701)), rel=1e-12
    )
    assert not cascade.active
    assert cascade.direction == 0
    assert not cascade.spent
    assert not cascade.episode_ended
    assert cascade.episode_direction == -1
    assert cascade.episode_start_ns == 601 * _S


def test_the_episode_ends_at_the_first_spent_inactive_update_and_resets_after() -> None:
    cascade = _cascade()
    _run_to(cascade, 704)
    assert not cascade.episode_ended
    _feed(cascade, 705)
    # 466.7 < 500 and ~2.27 < 3: spent and not active -- the end, still showing the episode.
    assert cascade.intensity == pytest.approx(
        (14 * _UNITS / 30) / _baseline(_QUIET + _RAMP + _decay_to(705)), rel=1e-12
    )
    assert not cascade.active
    assert cascade.direction == 0
    assert cascade.episode_ended
    assert cascade.spent
    assert cascade.episode_direction == -1
    assert cascade.episode_start_ns == 601 * _S
    _feed(cascade, 706)
    assert not cascade.episode_ended
    assert not cascade.spent
    assert cascade.peak_rate == 0.0
    assert cascade.episode_direction == 0
    assert cascade.episode_start_ns is None
    assert cascade.episode_notional_units == 0


def test_short_liquidations_read_rising_prices() -> None:
    cascade = _cascade()
    cascade.advance(0)
    cascade.advance(600 * _S)
    cascade.update_liquidation(LiquidatedSide.SHORT, _UNITS, 600 * _S)
    assert cascade.rate_short == pytest.approx(_UNITS / 30)
    assert cascade.active  # 33.3 against the floor
    assert cascade.direction == 1


def test_the_baseline_never_falls_under_the_floor() -> None:
    cascade = _cascade()
    cascade.update_liquidation(LiquidatedSide.LONG, _UNITS, 0)
    cascade.advance(30 * _S)
    cascade.advance(30 * _S + 600 * 40 * _S)  # 40 time constants without a liquidation
    assert _baseline([(_UNITS / 30, 30.0), (0.0, 24_000.0)]) < BASELINE_FLOOR
    assert cascade.baseline == BASELINE_FLOOR
    assert cascade.intensity == 0.0
    cascade.update_liquidation(LiquidatedSide.LONG, 300, 24_030 * _S)
    assert cascade.intensity == pytest.approx((300 / 30) / BASELINE_FLOOR)
    assert math.isfinite(cascade.intensity)


def test_a_late_event_is_placed_at_the_clock() -> None:
    cascade = _cascade()
    cascade.advance(100 * _S)
    cascade.update_liquidation(LiquidatedSide.LONG, _UNITS, 98 * _S)
    assert cascade.clock_ns == 100 * _S
    cascade.advance(130 * _S - 1)
    assert cascade.rate_long == pytest.approx(_UNITS / 30)  # placed at 100, not 98
    cascade.advance(130 * _S)
    assert cascade.rate_long == 0.0


def test_the_clock_never_moves_backwards() -> None:
    cascade = _cascade()
    cascade.advance(100 * _S)
    cascade.advance(50 * _S)
    assert cascade.clock_ns == 100 * _S


def test_the_advance_frequency_does_not_change_the_baseline() -> None:
    every_second = _cascade()
    every_minute = _cascade()
    for second in range(901):
        if (second < 600 and second % 60 == 0) or 600 <= second < 690:
            every_second.update_liquidation(LiquidatedSide.LONG, _UNITS, second * _S)
            every_minute.update_liquidation(LiquidatedSide.LONG, _UNITS, second * _S)
            continue
        every_second.advance(second * _S)
        if second % 60 == 0:
            every_minute.advance(second * _S)
            assert every_minute.baseline == pytest.approx(every_second.baseline, rel=1e-12)
    assert every_minute.baseline == pytest.approx(every_second.baseline, rel=1e-12)


def test_reset_clears_every_output_and_the_clock() -> None:
    cascade = _cascade()
    _run_to(cascade, 650)
    cascade.reset()
    assert not cascade.initialized
    assert not cascade.has_inputs
    assert cascade.clock_ns is None
    assert (cascade.rate_long, cascade.rate_short, cascade.intensity) == (0.0, 0.0, 0.0)
    assert cascade.baseline == BASELINE_FLOOR
    assert (cascade.active, cascade.rising, cascade.spent, cascade.direction) == (
        False,
        False,
        False,
        0,
    )
    assert (cascade.peak_rate, cascade.episode_start_ns, cascade.episode_notional_units) == (
        0.0,
        None,
        0,
    )
    cascade.advance(1_000 * _S)
    assert cascade.rate_long == 0.0  # no entry survived the reset


@pytest.mark.parametrize(
    ("window_s", "baseline_s", "threshold", "decay"),
    [
        (0, 600, 3.0, 0.5),
        (30, 0, 3.0, 0.5),
        (30, 600, 0.0, 0.5),
        (30, 600, math.inf, 0.5),
        (30, 600, 3.0, 0.0),
        (30, 600, 3.0, 1.0),
        (30, 600, 3.0, 1.5),
    ],
)
def test_the_constructor_refuses_a_bad_parameter(
    window_s: int, baseline_s: int, threshold: float, decay: float
) -> None:
    # PyCondition's message and ours each name the field.
    with pytest.raises(ValueError):
        LiquidationCascade(window_s, baseline_s, threshold, decay)


@pytest.mark.parametrize("units", [0, -5])
def test_a_non_positive_notional_is_refused(units: int) -> None:
    with pytest.raises(ValueError, match="notional_units must be > 0"):
        _cascade().update_liquidation(LiquidatedSide.LONG, units, 0)


@pytest.mark.parametrize("units", [True, 1.5])
def test_a_non_int_notional_is_refused(units: object) -> None:
    with pytest.raises(TypeError, match="notional_units must be an int"):
        _cascade().update_liquidation(LiquidatedSide.LONG, units, 0)  # type: ignore[arg-type]
