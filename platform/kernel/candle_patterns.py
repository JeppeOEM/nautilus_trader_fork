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
Candlestick pattern detection (kernel, DDD spine AD-D3; Story 27.7): one streaming
`CandlePattern(Indicator)` per pattern, and `CandlePatternSet` running all 22 over one stream.

Invariant: one pattern definition, shared by views, research and bots. The chart picker and the
screener's Technicals columns (`views.indicator_picker`), the scanner notebook
(`research.application.patterns`) and a strategy's `handle_bar` all feed this class, so a pattern
reads the same everywhere. Pure: no I/O, no clock, no module-level state; the state is O(1) -- at
most three bar records, two capped run counters and a capped warm-up counter.

`value` follows TA-Lib's convention so scanner tables read the same: `+100` bullish, `-100`
bearish, `0` none. `DOJI` is non-directional and gives `+100` when it fires, as `CDLDOJI` does.

**Notation.** For one bar `o h l c`: `range = h - l`, `body = |c - o|`,
`upper = h - max(o, c)`, `lower = min(o, c) - l`; *white* is `c > o`, *black* `c < o`. With the
`Thresholds` (constructor keywords, validated): *small* is `body <= body_ratio * range`, *long* is
not small, *doji* is `body <= doji_body_ratio * range`. A bar with `h == l` (flat) matches no
pattern: every bar a pattern reads must have `range > 0`. A bar outside `l <= min(o, c) <=
max(o, c) <= h`, or with a NaN, raises `ValueError` (a malformed candle is loud, DATA-07).

**Prior trend.** A *downtrend before bar k* means each of the `trend_bars` bars before k closed
below its predecessor's close (`c[k-1] < c[k-2] < ... < c[k-1-trend_bars]`); an *uptrend* is the
mirror. Each bar record carries the run lengths as they stood before it, so the check is two
comparisons. Trend patterns test the trend before their *first* bar, and warm up over their bar
count plus `trend_bars + 1` bars (the trend's own reference close); the others over their bar count.
`trend_bars >= 1` makes a downtrend and an uptrend exclusive, so a mirror pair never fires together.

**Patterns** (`b` = the last bar; `f`, `s`, `t` = first, second, third of a multi-bar pattern):

Single bar:

- `DOJI` (+100): doji.
- `DRAGONFLY_DOJI` (+100): doji and `upper <= doji_body_ratio * range` -- all shadow below.
- `GRAVESTONE_DOJI` (-100): doji and `lower <= doji_body_ratio * range` -- all shadow above.
- `HAMMER` (+100, trend): downtrend before `b`, hammer shape: small and
  `lower >= shadow_ratio * body` and `upper <= doji_body_ratio * range`.
- `HANGING_MAN` (-100, trend): the hammer shape after an uptrend.
- `INVERTED_HAMMER` (+100, trend): downtrend before `b`, inverted shape: small and
  `upper >= shadow_ratio * body` and `lower <= doji_body_ratio * range`.
- `SHOOTING_STAR` (-100, trend): the inverted shape after an uptrend.
- `MARUBOZU` (sign of the colour): `upper <= marubozu_shadow_ratio * range` and
  `lower <= marubozu_shadow_ratio * range`.
- `SPINNING_TOP` (sign of the colour): small, not doji, `upper > body` and `lower > body`.

Two bars:

- `ENGULFING` (+100 / -100): `f` black and `s` white (bullish), or `f` white and `s` black
  (bearish), and `s`'s body covers `f`'s: `min(s.o, s.c) <= min(f.o, f.c)` and
  `max(s.o, s.c) >= max(f.o, f.c)` with at least one side strict.
- `HARAMI` (+100 when `f` is black, -100 when white): `f` long, `s` small, `s`'s body inside
  `f`'s (`max(s.o, s.c) <= max(f.o, f.c)`, `min(s.o, s.c) >= min(f.o, f.c)`) and smaller.
- `HARAMI_CROSS` (same sign): a harami whose `s` is a doji.
- `PIERCING` (+100): `f` black and long, `s` white, `s.o <= f.c` and
  `(f.o + f.c) / 2 < s.c < f.o`.
- `DARK_CLOUD_COVER` (-100): `f` white and long, `s` black, `s.o >= f.c` and
  `f.o < s.c < (f.o + f.c) / 2`.
- `TWEEZER_BOTTOM` (+100, trend): downtrend before `f`, `f` black, `s` white,
  `|f.l - s.l| <= tweezer_ratio * max(range_f, range_s)`.
- `TWEEZER_TOP` (-100, trend): uptrend before `f`, `f` white, `s` black,
  `|f.h - s.h| <= tweezer_ratio * max(range_f, range_s)`.

Three bars:

- `MORNING_STAR` (+100, trend): downtrend before `f`, `f` black and long, `s` small, with
  `star_gap` also `max(s.o, s.c) < f.c` (the star's body gaps below `f`'s), `t` white and
  `t.c > (f.o + f.c) / 2`.
- `EVENING_STAR` (-100, trend): uptrend before `f`, `f` white and long, `s` small, with
  `star_gap` also `min(s.o, s.c) > f.c`, `t` black and `t.c < (f.o + f.c) / 2`.
- `THREE_WHITE_SOLDIERS` (+100, trend): downtrend before `f`; each bar white, long, and
  `upper <= body_ratio * range` (closes near its high); `s` and `t` each open inside the
  previous body (`prev.o <= o <= prev.c`) and close above the previous close.
- `THREE_BLACK_CROWS` (-100, trend): the mirror -- uptrend before `f`; each bar black, long,
  `lower <= body_ratio * range`; each opens inside the previous body and closes below its close.
- `THREE_INSIDE_UP` (+100): `f` black and `s` a harami of it, `t` white and `t.c > f.o`.
- `THREE_INSIDE_DOWN` (-100): `f` white and `s` a harami of it, `t` black and `t.c < f.o`.

**TA-Lib names** (for the one-off parity check, never a dependency): `DOJI`=`CDLDOJI`,
`DRAGONFLY_DOJI`=`CDLDRAGONFLYDOJI`, `GRAVESTONE_DOJI`=`CDLGRAVESTONEDOJI`, `HAMMER`=`CDLHAMMER`,
`HANGING_MAN`=`CDLHANGINGMAN`, `INVERTED_HAMMER`=`CDLINVERTEDHAMMER`,
`SHOOTING_STAR`=`CDLSHOOTINGSTAR`, `MARUBOZU`=`CDLMARUBOZU`, `SPINNING_TOP`=`CDLSPINNINGTOP`,
`ENGULFING`=`CDLENGULFING`, `HARAMI`=`CDLHARAMI`, `HARAMI_CROSS`=`CDLHARAMICROSS`,
`PIERCING`=`CDLPIERCING`, `DARK_CLOUD_COVER`=`CDLDARKCLOUDCOVER`, `TWEEZER_TOP`=none,
`TWEEZER_BOTTOM`=none, `MORNING_STAR`=`CDLMORNINGSTAR`, `EVENING_STAR`=`CDLEVENINGSTAR`,
`THREE_WHITE_SOLDIERS`=`CDL3WHITESOLDIERS`, `THREE_BLACK_CROWS`=`CDL3BLACKCROWS`,
`THREE_INSIDE_UP`=`CDL3INSIDE` (its +100), `THREE_INSIDE_DOWN`=`CDL3INSIDE` (its -100).

**Documented deviations from TA-Lib.** TA-Lib measures "long", "short" and "doji" against the
average of the previous 10 bars' bodies/ranges; these are ratios of the bar's own range (stateless,
O(1)). TA-Lib gives `CDLDRAGONFLYDOJI`/`CDLGRAVESTONEDOJI` +100 both; here they follow Nison
(+100 / -100). TA-Lib's hammer family has no trend test (it compares the body with the previous
bar's range instead); here a prior trend of `trend_bars` closes decides hammer vs hanging man and
inverted hammer vs shooting star, and stars, soldiers, crows and tweezers need it too. In 24/7
markets a bar usually opens at the previous close, so `PIERCING`/`DARK_CLOUD_COVER` test
`s.o <= f.c` (`>=`) instead of Nison's gap beyond the prior low (high); stars honour `star_gap`.
The star's third bar must close past the first body's midpoint (TA-Lib: 30% penetration).
"""

import math
import numbers
from collections import deque
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import astuple
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import NamedTuple

from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar


BULLISH = 100
BEARISH = -100
NO_PATTERN = 0
# The longest pattern's bar count: the most bar records a detector ever keeps.
MAX_PATTERN_BARS = 3


class PatternName(StrEnum):
    """The 22 patterns `CandlePattern` detects; each value equals its name (JSON-safe)."""

    DOJI = "DOJI"
    DRAGONFLY_DOJI = "DRAGONFLY_DOJI"
    GRAVESTONE_DOJI = "GRAVESTONE_DOJI"
    HAMMER = "HAMMER"
    HANGING_MAN = "HANGING_MAN"
    INVERTED_HAMMER = "INVERTED_HAMMER"
    SHOOTING_STAR = "SHOOTING_STAR"
    MARUBOZU = "MARUBOZU"
    SPINNING_TOP = "SPINNING_TOP"
    ENGULFING = "ENGULFING"
    HARAMI = "HARAMI"
    HARAMI_CROSS = "HARAMI_CROSS"
    PIERCING = "PIERCING"
    DARK_CLOUD_COVER = "DARK_CLOUD_COVER"
    TWEEZER_TOP = "TWEEZER_TOP"
    TWEEZER_BOTTOM = "TWEEZER_BOTTOM"
    MORNING_STAR = "MORNING_STAR"
    EVENING_STAR = "EVENING_STAR"
    THREE_WHITE_SOLDIERS = "THREE_WHITE_SOLDIERS"
    THREE_BLACK_CROWS = "THREE_BLACK_CROWS"
    THREE_INSIDE_UP = "THREE_INSIDE_UP"
    THREE_INSIDE_DOWN = "THREE_INSIDE_DOWN"


def _require_ratio(name: str, value: float, low: float, high: float, *, low_closed: bool) -> float:
    """
    Return `value` as a `float`, raising `ValueError` unless it is a finite real number in
    `(low, high)` (`[low, high)`). Any `numbers.Real` counts (a numpy scalar from a sweep grid
    too); a `bool` does not.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, was {value!r}")
    above = value >= low if low_closed else value > low
    if not above or not value < high:
        bracket = "[" if low_closed else "("
        raise ValueError(f"{name} must lie in {bracket}{low}, {high}), was {value!r}")
    return float(value)


# (field, low, high, low_closed): each ratio's valid range, `(low, high)` or `[low, high)`.
_RATIO_BOUNDS: tuple[tuple[str, float, float, bool], ...] = (
    ("body_ratio", 0.0, 1.0, False),
    ("shadow_ratio", 0.0, math.inf, False),
    ("doji_body_ratio", 0.0, 1.0, False),
    ("marubozu_shadow_ratio", 0.0, 0.5, True),
    ("tweezer_ratio", 0.0, 1.0, True),
)


@dataclass(frozen=True)
class Thresholds:
    """
    The geometric thresholds every pattern reads (definitions in the module docstring).

    Invariant: validated at construction, so a detector never runs on a threshold that makes a
    definition meaningless -- the ratios lie in their stated ranges and `trend_bars >= 1`, which is
    what keeps a downtrend and an uptrend (hence a mirror pair) exclusive. Frozen: one detector's
    thresholds never change under it.
    """

    body_ratio: float = 0.3
    shadow_ratio: float = 2.0
    doji_body_ratio: float = 0.1
    marubozu_shadow_ratio: float = 0.05
    tweezer_ratio: float = 0.05
    trend_bars: int = 3
    star_gap: bool = True

    def __post_init__(self) -> None:
        # Validated values are stored as plain `float`/`int`, so `asdict` stays JSON-safe even when
        # a sweep passes numpy scalars.
        for name, low, high, low_closed in _RATIO_BOUNDS:
            value = _require_ratio(name, getattr(self, name), low, high, low_closed=low_closed)
            object.__setattr__(self, name, value)
        if self.doji_body_ratio >= self.body_ratio:
            # A doji must be a small body: otherwise SPINNING_TOP (small, not doji) can never fire.
            raise ValueError(
                f"doji_body_ratio ({self.doji_body_ratio}) must be < body_ratio ({self.body_ratio})"
            )
        trend_bars = self.trend_bars
        if isinstance(trend_bars, bool) or not isinstance(trend_bars, numbers.Integral):
            raise ValueError(f"trend_bars must be an int >= 1, was {trend_bars!r}")
        if trend_bars < 1:
            raise ValueError(f"trend_bars must be an int >= 1, was {trend_bars!r}")
        object.__setattr__(self, "trend_bars", int(trend_bars))
        if type(self.star_gap) is not bool:
            raise ValueError(f"star_gap must be a bool, was {self.star_gap!r}")


class _Bar(NamedTuple):
    """One bar record: its OHLC and the prior-trend run lengths as they stood before it."""

    o: float
    h: float
    l: float
    c: float
    rising_before: int
    falling_before: int


# --- shape helpers ---------------------------------------------------------------------------


def _range(b: _Bar) -> float:
    return b.h - b.l


def _body(b: _Bar) -> float:
    return abs(b.c - b.o)


def _upper(b: _Bar) -> float:
    return b.h - max(b.o, b.c)


def _lower(b: _Bar) -> float:
    return min(b.o, b.c) - b.l


def _white(b: _Bar) -> bool:
    return b.c > b.o


def _black(b: _Bar) -> bool:
    return b.c < b.o


def _colour(b: _Bar) -> int:
    return BULLISH if _white(b) else BEARISH


def _small(b: _Bar, t: Thresholds) -> bool:
    return _body(b) <= t.body_ratio * _range(b)


def _doji(b: _Bar, t: Thresholds) -> bool:
    return _body(b) <= t.doji_body_ratio * _range(b)


def _downtrend(b: _Bar, t: Thresholds) -> bool:
    return b.falling_before >= t.trend_bars


def _uptrend(b: _Bar, t: Thresholds) -> bool:
    return b.rising_before >= t.trend_bars


def _hammer_shape(b: _Bar, t: Thresholds) -> bool:
    long_lower = _lower(b) >= t.shadow_ratio * _body(b)
    return _small(b, t) and long_lower and _upper(b) <= t.doji_body_ratio * _range(b)


def _inverted_shape(b: _Bar, t: Thresholds) -> bool:
    long_upper = _upper(b) >= t.shadow_ratio * _body(b)
    return _small(b, t) and long_upper and _lower(b) <= t.doji_body_ratio * _range(b)


def _harami_inside(first: _Bar, second: _Bar, t: Thresholds) -> bool:
    inside = max(second.o, second.c) <= max(first.o, first.c) and min(second.o, second.c) >= min(
        first.o, first.c
    )
    smaller = _body(second) < _body(first)
    return not _small(first, t) and _small(second, t) and inside and smaller


def _covers(outer: _Bar, inner: _Bar) -> bool:
    """`outer`'s body covers `inner`'s, strictly on at least one side (TA-Lib's engulfing rule)."""
    outer_low, outer_high = min(outer.o, outer.c), max(outer.o, outer.c)
    inner_low, inner_high = min(inner.o, inner.c), max(inner.o, inner.c)
    covered = outer_low <= inner_low and outer_high >= inner_high
    return covered and (outer_low < inner_low or outer_high > inner_high)


def _signal(fired: bool, direction: int) -> int:
    return direction if fired else NO_PATTERN


# --- single-bar patterns ---------------------------------------------------------------------


def _doji_pattern(bars: Sequence[_Bar], t: Thresholds) -> int:
    return _signal(_doji(bars[-1], t), BULLISH)


def _dragonfly_doji(bars: Sequence[_Bar], t: Thresholds) -> int:
    b = bars[-1]
    return _signal(_doji(b, t) and _upper(b) <= t.doji_body_ratio * _range(b), BULLISH)


def _gravestone_doji(bars: Sequence[_Bar], t: Thresholds) -> int:
    b = bars[-1]
    return _signal(_doji(b, t) and _lower(b) <= t.doji_body_ratio * _range(b), BEARISH)


def _hammer(bars: Sequence[_Bar], t: Thresholds) -> int:
    return _signal(_hammer_shape(bars[-1], t) and _downtrend(bars[-1], t), BULLISH)


def _hanging_man(bars: Sequence[_Bar], t: Thresholds) -> int:
    return _signal(_hammer_shape(bars[-1], t) and _uptrend(bars[-1], t), BEARISH)


def _inverted_hammer(bars: Sequence[_Bar], t: Thresholds) -> int:
    return _signal(_inverted_shape(bars[-1], t) and _downtrend(bars[-1], t), BULLISH)


def _shooting_star(bars: Sequence[_Bar], t: Thresholds) -> int:
    return _signal(_inverted_shape(bars[-1], t) and _uptrend(bars[-1], t), BEARISH)


def _marubozu(bars: Sequence[_Bar], t: Thresholds) -> int:
    b = bars[-1]
    limit = t.marubozu_shadow_ratio * _range(b)
    return _signal(_upper(b) <= limit and _lower(b) <= limit, _colour(b))


def _spinning_top(bars: Sequence[_Bar], t: Thresholds) -> int:
    b = bars[-1]
    body = _body(b)
    fired = _small(b, t) and not _doji(b, t) and _upper(b) > body and _lower(b) > body
    return _signal(fired, _colour(b))


# --- two-bar patterns ------------------------------------------------------------------------


def _engulfing(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    if _black(first) and _white(second) and _covers(second, first):
        return BULLISH
    if _white(first) and _black(second) and _covers(second, first):
        return BEARISH
    return NO_PATTERN


def _harami(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    return _signal(_harami_inside(first, second, t), -_colour(first))


def _harami_cross(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    return _signal(_harami_inside(first, second, t) and _doji(second, t), -_colour(first))


def _piercing(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    midpoint = (first.o + first.c) / 2
    shape = _black(first) and not _small(first, t) and _white(second)
    return _signal(shape and second.o <= first.c and midpoint < second.c < first.o, BULLISH)


def _dark_cloud_cover(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    midpoint = (first.o + first.c) / 2
    shape = _white(first) and not _small(first, t) and _black(second)
    return _signal(shape and second.o >= first.c and first.o < second.c < midpoint, BEARISH)


def _tweezer_bottom(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    tolerance = t.tweezer_ratio * max(_range(first), _range(second))
    shape = _downtrend(first, t) and _black(first) and _white(second)
    return _signal(shape and abs(first.l - second.l) <= tolerance, BULLISH)


def _tweezer_top(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second = bars
    tolerance = t.tweezer_ratio * max(_range(first), _range(second))
    shape = _uptrend(first, t) and _white(first) and _black(second)
    return _signal(shape and abs(first.h - second.h) <= tolerance, BEARISH)


# --- three-bar patterns ----------------------------------------------------------------------


def _morning_star(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, star, third = bars
    gapped = not t.star_gap or max(star.o, star.c) < first.c
    opening = _downtrend(first, t) and _black(first) and not _small(first, t)
    closing = _white(third) and third.c > (first.o + first.c) / 2
    return _signal(opening and _small(star, t) and gapped and closing, BULLISH)


def _evening_star(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, star, third = bars
    gapped = not t.star_gap or min(star.o, star.c) > first.c
    opening = _uptrend(first, t) and _white(first) and not _small(first, t)
    closing = _black(third) and third.c < (first.o + first.c) / 2
    return _signal(opening and _small(star, t) and gapped and closing, BEARISH)


def _strong_white(b: _Bar, t: Thresholds) -> bool:
    return _white(b) and not _small(b, t) and _upper(b) <= t.body_ratio * _range(b)


def _strong_black(b: _Bar, t: Thresholds) -> bool:
    return _black(b) and not _small(b, t) and _lower(b) <= t.body_ratio * _range(b)


def _three_white_soldiers(bars: Sequence[_Bar], t: Thresholds) -> int:
    steps = pairwise(bars)
    advancing = all(prev.o <= cur.o <= prev.c and cur.c > prev.c for prev, cur in steps)
    strong = all(_strong_white(b, t) for b in bars)
    return _signal(_downtrend(bars[0], t) and strong and advancing, BULLISH)


def _three_black_crows(bars: Sequence[_Bar], t: Thresholds) -> int:
    steps = pairwise(bars)
    declining = all(prev.c <= cur.o <= prev.o and cur.c < prev.c for prev, cur in steps)
    strong = all(_strong_black(b, t) for b in bars)
    return _signal(_uptrend(bars[0], t) and strong and declining, BEARISH)


def _three_inside_up(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second, third = bars
    fired = _black(first) and _harami_inside(first, second, t) and _white(third)
    return _signal(fired and third.c > first.o, BULLISH)


def _three_inside_down(bars: Sequence[_Bar], t: Thresholds) -> int:
    first, second, third = bars
    fired = _white(first) and _harami_inside(first, second, t) and _black(third)
    return _signal(fired and third.c < first.o, BEARISH)


class _PatternSpec(NamedTuple):
    """How many bars a pattern reads, whether it needs a prior trend, and its detector."""

    bars: int
    trend: bool
    detect: Callable[[Sequence[_Bar], Thresholds], int]


_PATTERNS = MappingProxyType(
    {
        PatternName.DOJI: _PatternSpec(1, False, _doji_pattern),
        PatternName.DRAGONFLY_DOJI: _PatternSpec(1, False, _dragonfly_doji),
        PatternName.GRAVESTONE_DOJI: _PatternSpec(1, False, _gravestone_doji),
        PatternName.HAMMER: _PatternSpec(1, True, _hammer),
        PatternName.HANGING_MAN: _PatternSpec(1, True, _hanging_man),
        PatternName.INVERTED_HAMMER: _PatternSpec(1, True, _inverted_hammer),
        PatternName.SHOOTING_STAR: _PatternSpec(1, True, _shooting_star),
        PatternName.MARUBOZU: _PatternSpec(1, False, _marubozu),
        PatternName.SPINNING_TOP: _PatternSpec(1, False, _spinning_top),
        PatternName.ENGULFING: _PatternSpec(2, False, _engulfing),
        PatternName.HARAMI: _PatternSpec(2, False, _harami),
        PatternName.HARAMI_CROSS: _PatternSpec(2, False, _harami_cross),
        PatternName.PIERCING: _PatternSpec(2, False, _piercing),
        PatternName.DARK_CLOUD_COVER: _PatternSpec(2, False, _dark_cloud_cover),
        PatternName.TWEEZER_TOP: _PatternSpec(2, True, _tweezer_top),
        PatternName.TWEEZER_BOTTOM: _PatternSpec(2, True, _tweezer_bottom),
        PatternName.MORNING_STAR: _PatternSpec(3, True, _morning_star),
        PatternName.EVENING_STAR: _PatternSpec(3, True, _evening_star),
        PatternName.THREE_WHITE_SOLDIERS: _PatternSpec(3, True, _three_white_soldiers),
        PatternName.THREE_BLACK_CROWS: _PatternSpec(3, True, _three_black_crows),
        PatternName.THREE_INSIDE_UP: _PatternSpec(3, False, _three_inside_up),
        PatternName.THREE_INSIDE_DOWN: _PatternSpec(3, False, _three_inside_down),
    }
)


def warmup_bars(pattern: PatternName, thresholds: Thresholds) -> int:
    """Return how many bars `pattern` needs before its first verdict (see "Prior trend")."""
    spec = _PATTERNS[pattern]
    return spec.bars + (thresholds.trend_bars + 1 if spec.trend else 0)


def _check_bar(open: float, high: float, low: float, close: float) -> None:
    # `min`/`max` pass a NaN through silently (`min(10, nan)` is 10), so finiteness is checked
    # first: a NaN or infinite price is a malformed bar that raises, never a quiet "no pattern".
    if not all(math.isfinite(v) for v in (open, high, low, close)):
        raise ValueError(f"malformed bar o={open} h={high} l={low} c={close}: needs finite prices")
    if not low <= min(open, close) <= max(open, close) <= high:
        raise ValueError(f"malformed bar o={open} h={high} l={low} c={close}: needs l <= o, c <= h")


# Patterns whose +100 marks that they fired, not a direction (`DOJI`, as TA-Lib's `CDLDOJI`): a
# forward-return "hit rate" in their sign would only measure the market's drift.
NON_DIRECTIONAL: frozenset[PatternName] = frozenset({PatternName.DOJI})


class CandlePattern(Indicator):
    """
    One candlestick pattern over a bar stream: `value` is +100 (bullish), -100 (bearish) or 0.

    Invariant: O(1) state -- at most `MAX_PATTERN_BARS` bar records (a bounded deque), two run
    counters capped at `trend_bars` and a warm-up counter capped at the pattern's warm-up; nothing
    grows with the bars fed. `initialized` turns true only after `warmup_bars` bars, and until then
    `value` stays 0 (a replay shows None, not a verdict). Commands that could break it: `update_raw`
    (a malformed bar raises before any state changes) and `reset`.

    Parameters
    ----------
    pattern : PatternName | str
        The pattern, as a member or its name (an unknown name raises `KeyError`).
    body_ratio, shadow_ratio, doji_body_ratio, marubozu_shadow_ratio, tweezer_ratio, trend_bars,
    star_gap
        The `Thresholds` (validated; a bad one raises `ValueError`).

    """

    def __init__(
        self,
        pattern: PatternName | str,
        *,
        body_ratio: float = 0.3,
        shadow_ratio: float = 2.0,
        doji_body_ratio: float = 0.1,
        marubozu_shadow_ratio: float = 0.05,
        tweezer_ratio: float = 0.05,
        trend_bars: int = 3,
        star_gap: bool = True,
    ) -> None:
        # The keyword defaults restate `Thresholds`' (a module-level instance would be kernel
        # state, AD-D3); `test_the_keyword_defaults_are_the_thresholds_defaults` pins them equal.
        name = pattern if isinstance(pattern, PatternName) else PatternName[pattern]
        thresholds = Thresholds(
            body_ratio=body_ratio,
            shadow_ratio=shadow_ratio,
            doji_body_ratio=doji_body_ratio,
            marubozu_shadow_ratio=marubozu_shadow_ratio,
            tweezer_ratio=tweezer_ratio,
            trend_bars=trend_bars,
            star_gap=star_gap,
        )
        super().__init__(params=[name.value, *astuple(thresholds)])
        self.pattern = name
        self.thresholds = thresholds
        self.value = NO_PATTERN
        self._span = _PATTERNS[name].bars
        self._detect = _PATTERNS[name].detect
        self._warmup = warmup_bars(name, thresholds)
        self._bars: deque[_Bar] = deque(maxlen=MAX_PATTERN_BARS)
        self._rising = 0
        self._falling = 0
        self._seen = 0

    def handle_bar(self, bar: Bar) -> None:
        self.update_raw(
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )

    def update_raw(self, open: float, high: float, low: float, close: float) -> None:
        _check_bar(open, high, low, close)
        previous_close = self._bars[-1].c if self._bars else None
        self._bars.append(_Bar(open, high, low, close, self._rising, self._falling))
        self._advance_trend(previous_close, close)
        self._seen = min(self._seen + 1, self._warmup)
        self._set_has_inputs(True)
        if self._seen < self._warmup:
            return
        self._set_initialized(True)
        recent = tuple(self._bars)[-self._span :]
        flat = any(b.h == b.l for b in recent)
        self.value = NO_PATTERN if flat else self._detect(recent, self.thresholds)

    def _advance_trend(self, previous_close: float | None, close: float) -> None:
        """Extend the rising or falling close run (capped at `trend_bars`), or end both."""
        cap = self.thresholds.trend_bars
        if previous_close is not None and close > previous_close:
            self._rising, self._falling = min(self._rising + 1, cap), 0
        elif previous_close is not None and close < previous_close:
            self._rising, self._falling = 0, min(self._falling + 1, cap)
        else:
            self._rising, self._falling = 0, 0

    def _reset(self) -> None:
        self.value = NO_PATTERN
        self._bars.clear()
        self._rising = 0
        self._falling = 0
        self._seen = 0


class CandlePatternSet:
    """
    Every `PatternName` over one bar stream, for the scanner.

    Invariant: every member sees the identical stream with the same `Thresholds`, so a member's
    value always equals a lone `CandlePattern` fed the same bars; `fired` lists the initialized,
    non-zero members in `PatternName` order.
    """

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        # `None`, not a `Thresholds()` default: a default instance is built at import time, which
        # is module-level kernel state (AD-D3).
        self.thresholds = Thresholds() if thresholds is None else thresholds
        self._detectors = tuple(
            CandlePattern(name, **asdict(self.thresholds)) for name in PatternName
        )

    def update_raw(self, open: float, high: float, low: float, close: float) -> None:
        _check_bar(open, high, low, close)
        for detector in self._detectors:
            detector.update_raw(open, high, low, close)

    def handle_bar(self, bar: Bar) -> None:
        self.update_raw(
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )

    def reset(self) -> None:
        for detector in self._detectors:
            detector.reset()

    def fired(self) -> list[tuple[PatternName, int]]:
        return [
            (detector.pattern, detector.value)
            for detector in self._detectors
            if detector.initialized and detector.value != NO_PATTERN
        ]
