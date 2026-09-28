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
`kernel.candle_patterns` (Story 27.7): hand-drawn bullish, bearish and near-miss bars per pattern,
the spec's I/O matrix, the mirror property over seeded random walks, O(1) state, and
`CandlePatternSet` against the lone detectors.
"""

import math
import random
from typing import Any

import numpy as np
import pytest

from kernel.candle_patterns import BEARISH
from kernel.candle_patterns import BULLISH
from kernel.candle_patterns import MAX_PATTERN_BARS
from kernel.candle_patterns import NO_PATTERN
from kernel.candle_patterns import CandlePattern
from kernel.candle_patterns import CandlePatternSet
from kernel.candle_patterns import PatternName
from kernel.candle_patterns import Thresholds
from kernel.candle_patterns import warmup_bars
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


OHLC = tuple[float, float, float, float]

MIRRORS = (
    (PatternName.DRAGONFLY_DOJI, PatternName.GRAVESTONE_DOJI),
    (PatternName.HAMMER, PatternName.HANGING_MAN),
    (PatternName.INVERTED_HAMMER, PatternName.SHOOTING_STAR),
    (PatternName.PIERCING, PatternName.DARK_CLOUD_COVER),
    (PatternName.TWEEZER_BOTTOM, PatternName.TWEEZER_TOP),
    (PatternName.MORNING_STAR, PatternName.EVENING_STAR),
    (PatternName.THREE_WHITE_SOLDIERS, PatternName.THREE_BLACK_CROWS),
    (PatternName.THREE_INSIDE_UP, PatternName.THREE_INSIDE_DOWN),
)


def _down(level: float) -> list[OHLC]:
    """Four black bars closing `level + 4` .. `level + 1`: a 3-bar downtrend before the next bar."""
    return [(c + 0.5, c + 0.7, c - 0.2, c) for c in (level + 4, level + 3, level + 2, level + 1)]


def _up(level: float) -> list[OHLC]:
    """Four white bars closing `level - 4` .. `level - 1`: a 3-bar uptrend before the next bar."""
    return [(c - 0.5, c + 0.2, c - 0.7, c) for c in (level - 4, level - 3, level - 2, level - 1)]


def _flat(level: float) -> list[OHLC]:
    """Four bars closing at `level`: no trend in either direction."""
    return [(level - 0.5, level + 0.2, level - 0.7, level)] * 4


def _last(pattern: PatternName, bars: list[OHLC], **thresholds: Any) -> int:
    detector = CandlePattern(pattern, **thresholds)
    for bar in bars:
        detector.update_raw(*bar)
    assert detector.initialized
    return detector.value


_BLACK_LONG: OHLC = (12.0, 12.1, 9.9, 10.0)
_WHITE_LONG: OHLC = (10.0, 12.1, 9.9, 12.0)

# (pattern, bars, the last bar's expected value): per pattern the bullish, bearish and near-miss
# cases it has.
CASES: list[tuple[PatternName, list[OHLC], int]] = [
    (PatternName.DOJI, [(10, 11, 9, 10.05)], BULLISH),
    (PatternName.DOJI, [(10, 11, 9, 10.5)], NO_PATTERN),
    (PatternName.DRAGONFLY_DOJI, [(10, 10.05, 8, 10)], BULLISH),
    (PatternName.DRAGONFLY_DOJI, [(10, 10.5, 8, 10)], NO_PATTERN),
    (PatternName.GRAVESTONE_DOJI, [(10, 12, 9.95, 10)], BEARISH),
    (PatternName.GRAVESTONE_DOJI, [(10, 12, 9.5, 10)], NO_PATTERN),
    (PatternName.HAMMER, [*_down(10), (10, 10.25, 8, 10.2)], BULLISH),
    (PatternName.HAMMER, [*_down(10), (10, 10.6, 8, 10.2)], NO_PATTERN),
    (PatternName.HAMMER, [*_flat(10), (10, 10.25, 8, 10.2)], NO_PATTERN),
    (PatternName.HANGING_MAN, [*_up(10), (10, 10.25, 8, 10.2)], BEARISH),
    (PatternName.HANGING_MAN, [*_down(10), (10, 10.25, 8, 10.2)], NO_PATTERN),
    (PatternName.INVERTED_HAMMER, [*_down(10), (10, 12, 9.95, 10.2)], BULLISH),
    (PatternName.INVERTED_HAMMER, [*_down(10), (10, 12, 9.5, 10.2)], NO_PATTERN),
    (PatternName.SHOOTING_STAR, [*_up(10), (10, 12, 9.95, 10.2)], BEARISH),
    (PatternName.SHOOTING_STAR, [*_flat(10), (10, 12, 9.95, 10.2)], NO_PATTERN),
    (PatternName.MARUBOZU, [(10, 11, 10, 11)], BULLISH),
    (PatternName.MARUBOZU, [(11, 11, 10, 10)], BEARISH),
    (PatternName.MARUBOZU, [(10, 11.2, 10, 11)], NO_PATTERN),
    (PatternName.SPINNING_TOP, [(10, 11, 9, 10.3)], BULLISH),
    (PatternName.SPINNING_TOP, [(10.3, 11, 9, 10)], BEARISH),
    (PatternName.SPINNING_TOP, [(10, 11, 9, 10.1)], NO_PATTERN),
    (PatternName.ENGULFING, [(10, 10.2, 8.9, 9), (8.9, 10.6, 8.8, 10.5)], BULLISH),
    (PatternName.ENGULFING, [(9, 10.2, 8.9, 10), (10.1, 10.2, 8.5, 8.8)], BEARISH),
    (PatternName.ENGULFING, [(10, 10.2, 8.9, 9), (8.9, 10.6, 8.8, 9.9)], NO_PATTERN),
    (PatternName.HARAMI, [_BLACK_LONG, (10.5, 11.5, 10.2, 10.8)], BULLISH),
    (PatternName.HARAMI, [_WHITE_LONG, (11.5, 11.8, 10.5, 11.2)], BEARISH),
    (PatternName.HARAMI, [_BLACK_LONG, (11.5, 12.6, 11, 12.3)], NO_PATTERN),
    (PatternName.HARAMI_CROSS, [_BLACK_LONG, (11, 11.5, 10.5, 11.02)], BULLISH),
    (PatternName.HARAMI_CROSS, [_WHITE_LONG, (11, 11.5, 10.5, 11.02)], BEARISH),
    (PatternName.HARAMI_CROSS, [_BLACK_LONG, (10.5, 11.5, 10.2, 10.8)], NO_PATTERN),
    (PatternName.PIERCING, [_BLACK_LONG, (9.9, 11.5, 9.8, 11.3)], BULLISH),
    (PatternName.PIERCING, [_BLACK_LONG, (9.9, 11.5, 9.8, 10.9)], NO_PATTERN),
    (PatternName.DARK_CLOUD_COVER, [_WHITE_LONG, (12.1, 12.2, 10.5, 10.7)], BEARISH),
    (PatternName.DARK_CLOUD_COVER, [_WHITE_LONG, (12.1, 12.2, 10.5, 11.2)], NO_PATTERN),
    (PatternName.TWEEZER_BOTTOM, [*_down(10), (11, 11.2, 9, 9.5), (9.6, 11, 9.02, 10.8)], BULLISH),
    (
        PatternName.TWEEZER_BOTTOM,
        [*_down(10), (11, 11.2, 9, 9.5), (9.6, 11, 9.5, 10.8)],
        NO_PATTERN,
    ),
    (PatternName.TWEEZER_TOP, [*_up(10), (10, 12, 9.8, 11.5), (11.4, 12.02, 10, 10.2)], BEARISH),
    (PatternName.TWEEZER_TOP, [*_up(10), (10, 12, 9.8, 11.5), (11.4, 12.5, 10, 10.2)], NO_PATTERN),
    (
        PatternName.MORNING_STAR,
        [*_down(12), (13, 13.1, 10.9, 11), (10.5, 10.8, 10.2, 10.6), (10.7, 12.6, 10.6, 12.5)],
        BULLISH,
    ),
    (
        PatternName.MORNING_STAR,
        [*_down(12), (13, 13.1, 10.9, 11), (11.2, 11.5, 10.9, 11.3), (10.7, 12.6, 10.6, 12.5)],
        NO_PATTERN,
    ),
    (
        PatternName.EVENING_STAR,
        [*_up(10), (9, 11.1, 8.9, 11), (11.4, 11.8, 11.2, 11.5), (11.3, 11.4, 9.4, 9.5)],
        BEARISH,
    ),
    (
        PatternName.EVENING_STAR,
        [*_up(10), (9, 11.1, 8.9, 11), (11.4, 11.8, 11.2, 11.5), (11.3, 11.4, 10.3, 10.4)],
        NO_PATTERN,
    ),
    (
        PatternName.THREE_WHITE_SOLDIERS,
        [*_down(10), (10, 11.1, 9.9, 11), (10.5, 12.1, 10.4, 12), (11.5, 13.1, 11.4, 13)],
        BULLISH,
    ),
    (
        PatternName.THREE_WHITE_SOLDIERS,
        [*_down(10), (10, 11.1, 9.9, 11), (10.5, 12.1, 10.4, 12), (12.5, 13.6, 12.4, 13.5)],
        NO_PATTERN,
    ),
    (
        PatternName.THREE_BLACK_CROWS,
        [*_up(14), (14, 14.1, 12.9, 13), (13.5, 13.6, 11.9, 12), (12.5, 12.6, 10.9, 11)],
        BEARISH,
    ),
    (
        PatternName.THREE_BLACK_CROWS,
        [*_flat(14), (14, 14.1, 12.9, 13), (13.5, 13.6, 11.9, 12), (12.5, 12.6, 10.9, 11)],
        NO_PATTERN,
    ),
    (
        PatternName.THREE_INSIDE_UP,
        [_BLACK_LONG, (10.5, 11.5, 10.2, 10.8), (10.9, 12.5, 10.8, 12.3)],
        BULLISH,
    ),
    (
        PatternName.THREE_INSIDE_UP,
        [_BLACK_LONG, (10.5, 11.5, 10.2, 10.8), (10.9, 12.1, 10.8, 11.9)],
        NO_PATTERN,
    ),
    (
        PatternName.THREE_INSIDE_DOWN,
        [_WHITE_LONG, (11.5, 11.8, 10.5, 11.2), (11.1, 11.2, 9.5, 9.7)],
        BEARISH,
    ),
    (
        PatternName.THREE_INSIDE_DOWN,
        [_WHITE_LONG, (11.5, 11.8, 10.5, 11.2), (11.1, 11.2, 10.0, 10.2)],
        NO_PATTERN,
    ),
]


@pytest.mark.parametrize(
    ("pattern", "bars", "expected"),
    CASES,
    ids=[f"{p.value}-{v:+d}-{i}" for i, (p, _, v) in enumerate(CASES)],
)
def test_hand_drawn_case(pattern: PatternName, bars: list[OHLC], expected: int) -> None:
    assert _last(pattern, bars) == expected


def test_every_pattern_has_a_firing_and_a_near_miss_case() -> None:
    fired = {pattern for pattern, _, value in CASES if value != NO_PATTERN}
    missed = {pattern for pattern, _, value in CASES if value == NO_PATTERN}
    assert fired == set(PatternName)
    assert missed == set(PatternName)


def test_directional_patterns_have_both_directions_drawn() -> None:
    both = {PatternName.MARUBOZU, PatternName.SPINNING_TOP, PatternName.ENGULFING}
    both |= {PatternName.HARAMI, PatternName.HARAMI_CROSS}
    for pattern in both:
        values = {value for p, _, value in CASES if p == pattern}
        assert {BULLISH, BEARISH} <= values, pattern


def test_the_matrix_hammer_after_an_uptrend_is_a_hanging_man() -> None:
    bars = [*_up(10), (10, 10.25, 8, 10.2)]
    assert _last(PatternName.HAMMER, bars) == NO_PATTERN
    assert _last(PatternName.HANGING_MAN, bars) == BEARISH


def test_the_star_gap_can_be_switched_off() -> None:
    no_gap = [*_down(12), (13, 13.1, 10.9, 11), (11.2, 11.5, 10.9, 11.3), (10.7, 12.6, 10.6, 12.5)]
    assert _last(PatternName.MORNING_STAR, no_gap) == NO_PATTERN
    assert _last(PatternName.MORNING_STAR, no_gap, star_gap=False) == BULLISH


def test_a_flat_bar_matches_no_pattern() -> None:
    patterns = CandlePatternSet()
    for bar in [*_down(12), (13, 13.1, 10.9, 11), (10.5, 10.8, 10.2, 10.6)]:
        patterns.update_raw(*bar)
    patterns.update_raw(10.6, 10.6, 10.6, 10.6)
    assert patterns.fired() == []


def test_a_flat_bar_inside_a_two_bar_pattern_matches_nothing() -> None:
    assert _last(PatternName.ENGULFING, [(9, 9, 9, 9), (8.9, 10.6, 8.8, 10.5)]) == NO_PATTERN


def test_warm_up_holds_initialized_back_for_the_pattern_and_its_trend() -> None:
    three_bar = CandlePattern(PatternName.THREE_INSIDE_UP)
    three_bar.update_raw(*_BLACK_LONG)
    assert not three_bar.initialized
    assert warmup_bars(PatternName.THREE_INSIDE_UP, Thresholds()) == 3
    assert warmup_bars(PatternName.HAMMER, Thresholds()) == 5  # 1 bar + trend_bars + 1
    assert warmup_bars(PatternName.MORNING_STAR, Thresholds(trend_bars=1)) == 5
    hammer = CandlePattern(PatternName.HAMMER)
    for i, bar in enumerate(_down(10)):
        hammer.update_raw(*bar)
        assert not hammer.initialized, i
    hammer.update_raw(10, 10.25, 8, 10.2)
    assert hammer.initialized


@pytest.mark.parametrize(
    "thresholds",
    [
        {"trend_bars": 0},
        {"trend_bars": 2.0},
        {"trend_bars": True},
        {"body_ratio": 0.0},
        {"body_ratio": 1.0},
        {"body_ratio": -0.1},
        {"shadow_ratio": 0.0},
        {"shadow_ratio": math.inf},
        {"doji_body_ratio": math.nan},
        {"marubozu_shadow_ratio": 0.5},
        {"tweezer_ratio": -0.01},
        {"star_gap": 1},
        {"body_ratio": "0.3"},
        {"doji_body_ratio": 0.3},  # a doji must be a small body
        {"doji_body_ratio": 0.2, "body_ratio": 0.15},
    ],
)
def test_a_bad_threshold_raises_at_construction(thresholds: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CandlePattern(PatternName.ENGULFING, **thresholds)


def test_the_keyword_defaults_are_the_thresholds_defaults() -> None:
    assert CandlePattern(PatternName.HAMMER).thresholds == Thresholds()


def test_numpy_scalar_thresholds_are_accepted_and_stored_as_python_numbers() -> None:
    # A parameter sweep over `np.arange` passes numpy scalars; they are valid thresholds.
    thresholds = CandlePattern(
        PatternName.HAMMER, trend_bars=np.int64(2), body_ratio=np.float32(0.25)
    ).thresholds
    assert thresholds.trend_bars == 2
    assert type(thresholds.trend_bars) is int
    assert type(thresholds.body_ratio) is float
    with pytest.raises(ValueError):
        Thresholds(trend_bars=np.int64(0))


def test_the_pattern_set_defaults_to_the_default_thresholds() -> None:
    assert CandlePatternSet().thresholds == Thresholds()


def test_an_unknown_pattern_name_raises_key_error() -> None:
    with pytest.raises(KeyError):
        CandlePattern("THREE_LINE_STRIKE")


def test_a_pattern_is_accepted_by_name_or_member() -> None:
    assert CandlePattern("HAMMER").pattern is PatternName.HAMMER
    assert CandlePattern(PatternName.HAMMER).pattern is PatternName.HAMMER


@pytest.mark.parametrize(
    "bar",
    [
        (10, 9, 11, 10),
        (10, 11, 9, 11.5),
        (8.5, 11, 9, 10),
        (math.nan, 11, 9, 10),
        (10, 11, 9, math.nan),  # `min`/`max` alone would let a NaN close through
        (10, math.inf, 9, 10),
        (10, 11, -math.inf, 10),
    ],
)
def test_a_malformed_bar_raises_and_changes_nothing(bar: OHLC) -> None:
    detector = CandlePattern(PatternName.DOJI)
    with pytest.raises(ValueError):
        detector.update_raw(*bar)
    assert not detector.has_inputs


def _walk(seed: int, count: int) -> list[OHLC]:
    """Return a seeded random walk whose opens gap off the previous close, so stars can form."""
    rng = random.Random(seed)  # noqa: S311 -- a reproducible walk, not a secret
    close = 100.0
    bars: list[OHLC] = []
    for _ in range(count):
        open_ = close + rng.gauss(0, 0.4)
        close = open_ + rng.gauss(0, 1.0)
        high = max(open_, close) + abs(rng.gauss(0, 0.5))
        low = min(open_, close) - abs(rng.gauss(0, 0.5))
        bars.append((open_, high, low, close))
    return bars


def _fired_per_bar(bars: list[OHLC]) -> list[dict[PatternName, int]]:
    patterns = CandlePatternSet()
    fired: list[dict[PatternName, int]] = []
    for bar in bars:
        patterns.update_raw(*bar)
        fired.append(dict(patterns.fired()))
    return fired


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_mirror_pair_fires_on_the_same_bar(seed: int) -> None:
    for fired in _fired_per_bar(_walk(seed, 2000)):
        for bullish, bearish in MIRRORS:
            assert not (bullish in fired and bearish in fired), (bullish, bearish)


def test_every_pattern_fires_over_the_walks_or_the_hand_drawn_cases() -> None:
    seen = {
        name for seed in (1, 2, 3) for fired in _fired_per_bar(_walk(seed, 2000)) for name in fired
    }
    seen |= {pattern for pattern, _, value in CASES if value != NO_PATTERN}
    assert seen == set(PatternName)


def test_the_walks_alone_fire_most_patterns() -> None:
    """Not just the hand-drawn bars: a realistic stream exercises the definitions too."""
    seen = {name for fired in _fired_per_bar(_walk(1, 2000)) for name in fired}
    assert len(seen) >= 18, sorted(set(PatternName) - seen)


def test_state_stays_constant_over_ten_thousand_bars() -> None:
    detector = CandlePattern(PatternName.MORNING_STAR)
    detector.update_raw(10, 11, 9, 10.5)
    attributes = len(vars(detector))
    for bar in _walk(4, 10_000):
        detector.update_raw(*bar)
        assert len(detector._bars) <= MAX_PATTERN_BARS
    assert len(vars(detector)) == attributes
    assert detector._rising <= detector.thresholds.trend_bars
    assert detector._falling <= detector.thresholds.trend_bars


def test_the_set_equals_the_lone_detectors_on_the_same_stream() -> None:
    bars = _walk(5, 1500)
    lone = {name: CandlePattern(name) for name in PatternName}
    patterns = CandlePatternSet()
    for bar in bars:
        patterns.update_raw(*bar)
        for detector in lone.values():
            detector.update_raw(*bar)
        expected = [
            (name, d.value) for name, d in lone.items() if d.initialized and d.value != NO_PATTERN
        ]
        assert patterns.fired() == expected


def test_reset_forgets_the_stream() -> None:
    detector = CandlePattern(PatternName.ENGULFING)
    detector.update_raw(10, 10.2, 8.9, 9)
    detector.reset()
    detector.update_raw(8.9, 10.6, 8.8, 10.5)
    assert not detector.initialized
    assert detector.value == NO_PATTERN


def test_handle_bar_feeds_a_real_nautilus_bar() -> None:
    bar_type = BarType.from_str("BTC-USD-PERP.DYDX-1-MINUTE-LAST-EXTERNAL")
    detector = CandlePattern(PatternName.ENGULFING)
    for o, h, l, c in [("10.0", "10.2", "8.9", "9.0"), ("8.9", "10.6", "8.8", "10.5")]:
        prices = [Price.from_str(v) for v in (o, h, l, c)]
        detector.handle_bar(Bar(bar_type, *prices, Quantity.from_str("1"), 0, 0))
    assert detector.value == BULLISH
