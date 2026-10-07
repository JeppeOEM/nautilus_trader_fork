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
Technical indicators `nautilus_trader.indicators` lacks (kernel, DDD spine AD-D3; Story 33.11):
nine streaming `Indicator` subclasses, one definition each, shared by the chart picker
(`views.indicator_picker`), the Technicals columns and research's `IndicatorSignalStrategy`.

Each follows `kernel.candle_patterns`' contract: params checked up front (`PyCondition` or a
`ValueError` naming the param), `update_raw` with bounded state (O(1) per bar: running sums,
monotonic deques and Nautilus's own moving averages), `has_inputs`/`initialized` set as Nautilus's
own indicators do, `_reset` clearing everything, no module-level mutable state, and a `handle_bar`
over a real `Bar` wherever one bar alone is the input (`PivotPoints` takes a session key the bar
does not carry, so it has none). Nautilus pieces are reused, never re-implemented:
`DirectionalMovement`/`AverageTrueRange` with `MovingAverageType.WILDER` (ADX, Supertrend),
`SimpleMovingAverage` (AO) and `MovingAverageFactory`'s Wilder average (ADX's DX smoothing). Their
seeding is Nautilus's: a Wilder average starts at its first input and is `initialized` after
`period` inputs; `DirectionalMovement` feeds 0 for the first bar (no previous bar);
`AverageTrueRange`'s first true range is `h - l`.

**Formulas** (`hl2 = (h + l) / 2`, `tp = (h + l + c) / 3`):

- `Supertrend(period=10, multiplier=3.0)` (Olivier Seban; TradingView's `ta.supertrend`):
  `ATR = AverageTrueRange(period, WILDER)`; basic bands `hl2 +- multiplier * ATR`; the final upper
  band is the basic one when it is below the previous final upper or the previous close was above
  it, else the previous final upper (the lower band mirrors it). From the first bar ATR is
  initialized, direction starts at -1 (down, `value` the upper band), as TradingView's Pine
  `ta.supertrend` does (its `direction := 1` on the first bar is its *down* trend, `superTrend =
  upperBand`; Pine's sign convention is the opposite of ours, +1 up here); an up trend (+1) turns
  -1 when the close is below the final lower band, a down trend (-1) turns +1 when the close is
  above the final upper band. `value` is the final lower band in an up trend, the final upper band
  in a down trend.
- `ParabolicSAR(step=0.02, max_step=0.2)` (Wilder 1978, "New Concepts in Technical Trading
  Systems"; the bar-for-bar rules of TA-Lib's `TA_SAR`): seeded at the second bar -- short when the
  second bar's down move `l1 - l2` is positive and larger than its up move `h2 - h1`, else long; a
  long starts with `SAR = l1`, `EP = h2`, a short with `SAR = h1`, `EP = l2`; `AF = step`. On each
  bar from the second: a long whose low reaches the SAR (`l <= SAR`) reverses -- that bar's SAR is
  the old EP raised to at least the previous and the current high, `EP = l`, `AF = step` --;
  otherwise the bar's SAR is the carried one and a new high moves `EP` and adds `step` to `AF`
  (capped at `max_step`). The next SAR is `SAR + AF * (EP - SAR)`, never above the previous or the
  current low in a long (never below the highs in a short). A short mirrors it (`h >= SAR`).
- `AverageDirectionalIndex(period=14)` (Wilder 1978): `+DI = 100 * pos / ATR`,
  `-DI = 100 * neg / ATR` from `DirectionalMovement(period, WILDER)` and
  `AverageTrueRange(period, WILDER)` (both 0 when `ATR` is 0); `DX = 100 * |+DI - -DI| /
  (+DI + -DI)`, 0 when the sum is 0; `adx` is the Wilder average of DX over `period`, fed once
  both inputs are initialized, and the indicator is initialized when that average is (bar
  `2 * period - 1`).
- `WilliamsPercentR(period=14)` (Larry Williams): `-100 * (HH - c) / (HH - LL)` over the last
  `period` highs and lows, -50 when `HH == LL`.
- `PivotPoints(kind)`, `update_raw(h, l, c, session)` (StockCharts ChartSchool, "Pivot Points"):
  from the completed session's high `H`, low `L` and last close `C`, `PP = (H + L + C) / 3`;
  *standard* `R1 = 2PP - L`, `S1 = 2PP - H`, `R2 = PP + (H - L)`, `S2 = PP - (H - L)`,
  `R3 = H + 2(PP - L)`, `S3 = L - 2(H - PP)`; *fibonacci* `R/S n = PP +- f_n (H - L)` with
  `f = 0.382, 0.618, 1.0`; *camarilla* `R/S n = C +- (H - L) * 1.1 / d_n` with `d = 12, 6, 4, 2`
  for n = 1..4. `r4`/`s4` exist for camarilla only (None otherwise, never NaN). `session` is an
  opaque integer key from the caller (the picker passes `candles.domain.fold.bucket_start_ms`):
  a new key completes the previous session, so the levels are initialized after the first change;
  a key below the running one is refused (`ValueError`). Whether the new session directly follows
  the completed one is the caller's to check (the picker resets on a skipped session).
- `MoneyFlowIndex(period=14)` (Gene Quong and Avrum Soudack; StockCharts): raw flow `tp * v`,
  positive when `tp` rose from the previous bar, negative when it fell, neither when unchanged;
  `100 * Σpos / (Σpos + Σneg)` over the last `period` flows, 50 when both sums are 0. The first bar
  only seeds `tp`, so it is initialized at bar `period + 1`.
- `ChaikinMoneyFlow(period=20)` (Marc Chaikin): `CLV = ((c - l) - (h - c)) / (h - l)`, 0 when
  `h == l`; `Σ(CLV * v) / Σv` over the last `period` bars, 0 when `Σv == 0`.
- `AwesomeOscillator(fast=5, slow=34)` (Bill Williams): `SMA(hl2, fast) - SMA(hl2, slow)`, Nautilus
  `SimpleMovingAverage`s, initialized with the slow one.
- `ZigZag(deviation_pct=5.0)` (StockCharts ChartSchool, "ZigZag"): over highs and lows, it holds
  the running extreme of the current leg (the highest high of an up leg, the lowest low of a down
  leg). On each bar an extension (a new extreme) is checked first; only a bar that does not extend
  can reverse: in an up leg, `l <= extreme * (1 - d)` (`d = deviation_pct / 100`) confirms the
  extreme as a top pivot (`pivot_price`/`pivot_bar`, `confirmed` true for that update) and starts a
  down leg at this bar's low; a down leg mirrors it with `h >= extreme * (1 + d)`. Before the first
  pivot both the highest high and the lowest low are tracked, and the top is tested first.
  `extreme_price`/`extreme_bar` are the unconfirmed last leg: they repaint until a reversal
  confirms them (audit D-215). Bars are counted per update from 0. Prices must be positive (the
  reversal is a percent of the extreme).
"""

from collections import deque
from collections.abc import Callable
from math import fsum
from math import isfinite
from operator import ge
from operator import le

from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import DirectionalMovement
from nautilus_trader.indicators import Indicator
from nautilus_trader.indicators import MovingAverageFactory
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar


PIVOT_KINDS: tuple[str, ...] = ("standard", "fibonacci", "camarilla")
PIVOT_LEVELS: tuple[str, ...] = ("pp", "r1", "r2", "r3", "r4", "s1", "s2", "s3", "s4")
_FIBONACCI = (0.382, 0.618, 1.0)
_CAMARILLA_DIVISORS = (12, 6, 4, 2)


class _RollingExtreme:
    """
    The maximum (or minimum) of the last `period` values, amortised O(1): a monotonic deque of
    `(index, value)` holding only values that can still become the extreme.

    Invariant: at most `period` entries, every one inside the window, values non-strictly ordered
    by `keeps` (each entry beats or ties every later one: an equal value is kept, not popped), so
    the front is the window's extreme.
    """

    def __init__(self, period: int, keeps: Callable[[float, float], bool]) -> None:
        self._period = period
        self._keeps = keeps  # `ge` keeps a maximum, `le` a minimum
        self._entries: deque[tuple[int, float]] = deque()
        self._index = 0

    def push(self, value: float) -> None:
        while self._entries and not self._keeps(self._entries[-1][1], value):
            self._entries.pop()
        self._entries.append((self._index, value))
        if self._entries[0][0] <= self._index - self._period:
            self._entries.popleft()
        self._index += 1

    @property
    def value(self) -> float:
        return self._entries[0][1]

    @property
    def full(self) -> bool:
        return self._index >= self._period

    def clear(self) -> None:
        self._entries.clear()
        self._index = 0


class _RollingSum:
    """
    The sum of the last `period` values, amortised O(1) per update.

    Invariant: `total` is the held values' sum to within a few ulps of the *window's* magnitude,
    however long the stream ran -- never the drift of `total -= old; total += new` over a history
    of far larger values (200 000 pushes of ~1e12 then a window of thirteen zeros and 1.0 read
    1.0002 that way). The running sum is a TwoSum pair (`_hi` plus the exact rounding error of
    every add and remove, carried in `_lo`), re-normalised every `period` pushes to
    `math.fsum(window)` and its residual (`fsum` over the window and `-_hi`), an O(period) pass
    once per `period` pushes; and it is exactly 0.0 whenever every held value is 0 (a count of the
    non-zero ones resets it), so the zero-sum branches of MFI and CMF read exactly.
    """

    def __init__(self, period: int) -> None:
        self._values: deque[float] = deque(maxlen=period)
        self._clear_sums()

    def _clear_sums(self) -> None:
        self._hi = 0.0
        self._lo = 0.0
        self._nonzero = 0
        self._since_fsum = 0

    def _add(self, value: float) -> None:
        """Knuth's TwoSum: `_hi + _lo` gains exactly `value`, up to `_lo`'s own rounding."""
        total = self._hi + value
        virtual = total - self._hi
        self._lo += (self._hi - (total - virtual)) + (value - virtual)
        self._hi = total

    def push(self, value: float) -> None:
        if len(self._values) == self._values.maxlen:
            old = self._values[0]
            self._add(-old)
            self._nonzero -= old != 0.0
        self._values.append(value)
        self._add(value)
        self._nonzero += value != 0.0
        self._since_fsum += 1
        if self._nonzero == 0:
            self._hi = self._lo = 0.0
        elif self._since_fsum >= len(self._values):
            self._hi = fsum(self._values)
            self._lo = fsum([*self._values, -self._hi])
            self._since_fsum = 0

    @property
    def total(self) -> float:
        return self._hi + self._lo

    @property
    def full(self) -> bool:
        return len(self._values) == self._values.maxlen

    def clear(self) -> None:
        self._values.clear()
        self._clear_sums()


def _check_finite(**values: float) -> None:
    """Refuse a NaN or infinite input: a malformed bar raises before any state changes (DATA-07)."""
    for name, value in values.items():
        if not isfinite(value):
            raise ValueError(f"{name}={value!r} must be finite")


def _check_bar(high: float, low: float, **others: float) -> None:
    """
    Refuse a malformed bar before any state changes (DATA-07): a non-finite value, a low above the
    high, or a negative `volume` (when one is passed) -- every class taking a high and a low calls
    this one check.
    """
    _check_finite(high=high, low=low, **others)
    if low > high:
        raise ValueError(f"malformed bar h={high} l={low}: needs l <= h")
    if others.get("volume", 0.0) < 0:
        raise ValueError(f"malformed bar volume={others['volume']!r}: needs volume >= 0")


class Supertrend(Indicator):
    """
    Supertrend: an ATR band that follows the trend (formula in the module docstring).

    Invariant: O(1) state -- one Nautilus ATR, the two final bands, the previous close and the
    direction (+1 up or -1 down, starting at -1 as TradingView's does). `value`/`direction` are
    meaningful only once `initialized` (the ATR is).

    Known limit: Nautilus's WILDER `AverageTrueRange` seeds its average with the first true range,
    while Pine's `ta.atr` (`ta.rma`) seeds with the SMA of the first `period` true ranges, so the
    first values differ from TradingView's until the seed decays (a factor `(1 - 1/period)` per
    bar). Upgrade path: an SMA-seeded Wilder average in `kernel` (or upstream) fed the true range.

    Parameters
    ----------
    period : int
        The ATR period (> 0).
    multiplier : float
        The band width in ATRs (> 0).

    """

    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        PyCondition.positive_int(period, "period")
        PyCondition.positive(multiplier, "multiplier")
        super().__init__(params=[period, multiplier])
        self.period = period
        self.multiplier = multiplier
        self._atr = AverageTrueRange(period, MovingAverageType.WILDER)
        self.value = 0.0
        self.direction = -1
        self.upper = 0.0
        self.lower = 0.0
        self._prev_close = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.high.as_double(), bar.low.as_double(), bar.close.as_double())

    def update_raw(self, high: float, low: float, close: float) -> None:
        _check_bar(high, low, close=close)
        self._atr.update_raw(high, low, close)
        self._set_has_inputs(True)
        if self._atr.initialized:
            self._advance((high + low) / 2.0, close)
        self._prev_close = close

    def _advance(self, hl2: float, close: float) -> None:
        width = self.multiplier * self._atr.value
        upper, lower = hl2 + width, hl2 - width
        if not self.initialized:
            self.upper, self.lower, self.direction = upper, lower, -1
            self._set_initialized(True)
        else:
            if not (upper < self.upper or self._prev_close > self.upper):
                upper = self.upper
            if not (lower > self.lower or self._prev_close < self.lower):
                lower = self.lower
            self.upper, self.lower = upper, lower
            self.direction = self._next_direction(close)
        self.value = self.lower if self.direction == 1 else self.upper

    def _next_direction(self, close: float) -> int:
        if self.direction == 1:
            return -1 if close < self.lower else 1
        return 1 if close > self.upper else -1

    def _reset(self) -> None:
        self._atr.reset()
        self.value = 0.0
        self.direction = -1
        self.upper = 0.0
        self.lower = 0.0
        self._prev_close = 0.0


class ParabolicSAR(Indicator):
    """
    Wilder's Parabolic SAR (formula in the module docstring): `value` is the stop for the current
    bar, computed from the bars before it (on a reversal, the reversal stop).

    Invariant: O(1) state -- the trend side, the next SAR, the extreme point, the acceleration
    factor (always within `[step, max_step]`) and the previous bar's high and low.

    Parameters
    ----------
    step : float
        The acceleration factor's start and increment (> 0).
    max_step : float
        The acceleration factor's cap (step <= max_step <= 1).

    """

    def __init__(self, step: float = 0.02, max_step: float = 0.2) -> None:
        PyCondition.positive(step, "step")
        PyCondition.positive(max_step, "max_step")
        if max_step < step:
            raise ValueError(f"max_step={max_step!r} must be at least step={step!r}")
        if max_step > 1.0:
            # A factor above 1 moves the SAR past the extreme point it chases, so the prior-bar
            # clamp would pin it to the previous bar's low/high: no parabolic stop at all.
            raise ValueError(f"max_step={max_step!r} must be at most 1")
        super().__init__(params=[step, max_step])
        self.step = step
        self.max_step = max_step
        self.value = 0.0
        self.is_long = True
        self._sar = 0.0
        self._ep = 0.0
        self._af = step
        self._prev_high = 0.0
        self._prev_low = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.high.as_double(), bar.low.as_double())

    def update_raw(self, high: float, low: float) -> None:
        _check_bar(high, low)
        if not self.has_inputs:
            self._prev_high, self._prev_low = high, low
            self._set_has_inputs(True)
            return
        if not self.initialized:
            self._seed(high, low)
            self._set_initialized(True)
        if self.is_long:
            self._long_bar(high, low)
        else:
            self._short_bar(high, low)
        self._prev_high, self._prev_low = high, low

    def _seed(self, high: float, low: float) -> None:
        """Seed from the first two bars, then step the second bar against itself (TA-Lib's)."""
        down_move = self._prev_low - low
        self.is_long = not (down_move > 0 and down_move > high - self._prev_high)
        self._sar = self._prev_low if self.is_long else self._prev_high
        self._ep = high if self.is_long else low
        self._af = self.step
        self._prev_high, self._prev_low = high, low

    def _long_bar(self, high: float, low: float) -> None:
        ceiling = max(self._prev_high, high)
        if low <= self._sar:
            self.is_long = False
            self.value = max(self._ep, ceiling)
            self._af, self._ep = self.step, low
            self._sar = max(self.value + self._af * (self._ep - self.value), ceiling)
            return
        self.value = self._sar
        if high > self._ep:
            self._ep, self._af = high, min(self._af + self.step, self.max_step)
        floor = min(self._prev_low, low)
        self._sar = min(self._sar + self._af * (self._ep - self._sar), floor)

    def _short_bar(self, high: float, low: float) -> None:
        floor = min(self._prev_low, low)
        if high >= self._sar:
            self.is_long = True
            self.value = min(self._ep, floor)
            self._af, self._ep = self.step, high
            self._sar = min(self.value + self._af * (self._ep - self.value), floor)
            return
        self.value = self._sar
        if low < self._ep:
            self._ep, self._af = low, min(self._af + self.step, self.max_step)
        ceiling = max(self._prev_high, high)
        self._sar = max(self._sar + self._af * (self._ep - self._sar), ceiling)

    def _reset(self) -> None:
        self.value = 0.0
        self.is_long = True
        self._sar = 0.0
        self._ep = 0.0
        self._af = self.step
        self._prev_high = 0.0
        self._prev_low = 0.0


class AverageDirectionalIndex(Indicator):
    """
    Wilder's ADX with its two directional indicators (formula in the module docstring).

    Invariant: O(1) state -- Nautilus's `DirectionalMovement` and `AverageTrueRange` (WILDER) and
    one Wilder average of DX; `initialized` exactly when that average is.

    Parameters
    ----------
    period : int
        The smoothing period of +-DM, ATR and DX (> 0).

    """

    def __init__(self, period: int = 14) -> None:
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period])
        self.period = period
        self._dm = DirectionalMovement(period, MovingAverageType.WILDER)
        self._atr = AverageTrueRange(period, MovingAverageType.WILDER)
        self._dx = MovingAverageFactory.create(period, MovingAverageType.WILDER)
        self.adx = 0.0
        self.plus_di = 0.0
        self.minus_di = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.high.as_double(), bar.low.as_double(), bar.close.as_double())

    def update_raw(self, high: float, low: float, close: float) -> None:
        _check_bar(high, low, close=close)
        self._dm.update_raw(high, low)
        self._atr.update_raw(high, low, close)
        self._set_has_inputs(True)
        if not (self._dm.initialized and self._atr.initialized):
            return
        atr = self._atr.value
        self.plus_di = 100.0 * self._dm.pos / atr if atr > 0 else 0.0
        self.minus_di = 100.0 * self._dm.neg / atr if atr > 0 else 0.0
        total = self.plus_di + self.minus_di
        dx = 100.0 * abs(self.plus_di - self.minus_di) / total if total > 0 else 0.0
        self._dx.update_raw(dx)
        self.adx = self._dx.value
        if self._dx.initialized:
            self._set_initialized(True)

    def _reset(self) -> None:
        self._dm.reset()
        self._atr.reset()
        self._dx.reset()
        self.adx = 0.0
        self.plus_di = 0.0
        self.minus_di = 0.0


class WilliamsPercentR(Indicator):
    """
    Williams %R over the last `period` bars (formula in the module docstring), in `[-100, 0]`.

    Invariant: two monotonic deques of at most `period` entries each; initialized after `period`
    bars.

    Parameters
    ----------
    period : int
        The lookback in bars (> 0).

    """

    def __init__(self, period: int = 14) -> None:
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period])
        self.period = period
        self._highs = _RollingExtreme(period, ge)
        self._lows = _RollingExtreme(period, le)
        self.value = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.high.as_double(), bar.low.as_double(), bar.close.as_double())

    def update_raw(self, high: float, low: float, close: float) -> None:
        _check_bar(high, low, close=close)
        self._highs.push(high)
        self._lows.push(low)
        highest, lowest = self._highs.value, self._lows.value
        span = highest - lowest
        self.value = -50.0 if span == 0 else -100.0 * (highest - close) / span
        self._set_has_inputs(True)
        if self._highs.full:
            self._set_initialized(True)

    def _reset(self) -> None:
        self._highs.clear()
        self._lows.clear()
        self.value = 0.0


def pivot_levels(kind: str, high: float, low: float, close: float) -> dict[str, float | None]:
    """Return one session's levels (`PIVOT_LEVELS`) of `kind` (formulas in the module docstring)."""
    pp = (high + low + close) / 3.0
    span = high - low
    offsets: list[float]
    if kind == "standard":
        resistances = [2 * pp - low, pp + span, high + 2 * (pp - low)]
        supports = [2 * pp - high, pp - span, low - 2 * (high - pp)]
    elif kind == "fibonacci":
        offsets = [f * span for f in _FIBONACCI]
        resistances = [pp + offset for offset in offsets]
        supports = [pp - offset for offset in offsets]
    elif kind == "camarilla":
        offsets = [span * 1.1 / d for d in _CAMARILLA_DIVISORS]
        resistances = [close + offset for offset in offsets]
        supports = [close - offset for offset in offsets]
    else:
        raise ValueError(f"kind={kind!r} is not one of {list(PIVOT_KINDS)}")
    levels: dict[str, float | None] = dict.fromkeys(PIVOT_LEVELS)
    levels["pp"] = pp
    for n, (r, s) in enumerate(zip(resistances, supports, strict=True), start=1):
        levels[f"r{n}"], levels[f"s{n}"] = r, s
    return levels


class PivotPoints(Indicator):
    """
    Floor pivot levels from the previous completed session (formulas in the module docstring).

    Invariant: O(1) state -- the running session's key, high, low and last close, and the nine
    levels of the session before it. A level is never computed from a session not fed from its
    start: the caller decides where a session starts (`session`), and the levels stay None until a
    session has been completed. Commands that could break it: `update_raw` (a malformed bar or a
    session key below the running one raises before any state changes) and `reset`.

    Parameters
    ----------
    kind : str
        One of `PIVOT_KINDS`.

    """

    def __init__(self, kind: str = "standard") -> None:
        if kind not in PIVOT_KINDS:
            raise ValueError(f"kind={kind!r} is not one of {list(PIVOT_KINDS)}")
        super().__init__(params=[kind])
        self.kind = kind
        self._session: int | None = None
        self._high = 0.0
        self._low = 0.0
        self._close = 0.0
        self._clear_levels()

    def _clear_levels(self) -> None:
        self.pp: float | None = None
        self.r1: float | None = None
        self.r2: float | None = None
        self.r3: float | None = None
        self.r4: float | None = None
        self.s1: float | None = None
        self.s2: float | None = None
        self.s3: float | None = None
        self.s4: float | None = None

    @property
    def session(self) -> int | None:
        """The running session's key; None before the first update."""
        return self._session

    def update_raw(self, high: float, low: float, close: float, session: int) -> None:
        _check_bar(high, low, close=close)
        if self._session is not None and session < self._session:
            # A key going back would complete the running session out of order and mix two
            # sessions' bars: the caller's feed is malformed (DATA-07), never re-ordered here.
            raise ValueError(f"session {session} is before the running session {self._session}")
        if session != self._session:
            if self._session is not None:
                for name, level in pivot_levels(
                    self.kind, self._high, self._low, self._close
                ).items():
                    setattr(self, name, level)
                self._set_initialized(True)
            self._session, self._high, self._low = session, high, low
        else:
            self._high, self._low = max(self._high, high), min(self._low, low)
        self._close = close
        self._set_has_inputs(True)

    def levels(self) -> dict[str, float | None]:
        """Return the nine levels by name (`PIVOT_LEVELS`); r4/s4 None unless camarilla."""
        return {name: getattr(self, name) for name in PIVOT_LEVELS}

    def _reset(self) -> None:
        self._session = None
        self._high = 0.0
        self._low = 0.0
        self._close = 0.0
        self._clear_levels()


class MoneyFlowIndex(Indicator):
    """
    The Money Flow Index, a volume-weighted RSI of the typical price (formula in the module
    docstring), in `[0, 100]`.

    Invariant: two rolling sums of at most `period` flows each and the previous typical price;
    initialized once `period` flows are held (bar `period + 1`).

    Parameters
    ----------
    period : int
        The number of flows summed (> 0).

    """

    def __init__(self, period: int = 14) -> None:
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period])
        self.period = period
        self._positive = _RollingSum(period)
        self._negative = _RollingSum(period)
        self._prev_tp: float | None = None
        self.value = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
            bar.volume.as_double(),
        )

    def update_raw(self, high: float, low: float, close: float, volume: float) -> None:
        _check_bar(high, low, close=close, volume=volume)
        tp = (high + low + close) / 3.0
        prev_tp, self._prev_tp = self._prev_tp, tp
        self._set_has_inputs(True)
        if prev_tp is None:
            return
        flow = tp * volume
        self._positive.push(flow if tp > prev_tp else 0.0)
        self._negative.push(flow if tp < prev_tp else 0.0)
        total = self._positive.total + self._negative.total
        self.value = 50.0 if total == 0 else 100.0 * self._positive.total / total
        if self._positive.full:
            self._set_initialized(True)

    def _reset(self) -> None:
        self._positive.clear()
        self._negative.clear()
        self._prev_tp = None
        self.value = 0.0


class ChaikinMoneyFlow(Indicator):
    """
    Chaikin Money Flow (formula in the module docstring), in `[-1, 1]`.

    Invariant: two rolling sums of at most `period` values each; initialized after `period` bars.

    Parameters
    ----------
    period : int
        The number of bars summed (> 0).

    """

    def __init__(self, period: int = 20) -> None:
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period])
        self.period = period
        self._flow = _RollingSum(period)
        self._volume = _RollingSum(period)
        self.value = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
            bar.volume.as_double(),
        )

    def update_raw(self, high: float, low: float, close: float, volume: float) -> None:
        _check_bar(high, low, close=close, volume=volume)
        span = high - low
        clv = 0.0 if span == 0 else ((close - low) - (high - close)) / span
        self._flow.push(clv * volume)
        self._volume.push(volume)
        total = self._volume.total
        self.value = 0.0 if total == 0 else self._flow.total / total
        self._set_has_inputs(True)
        if self._volume.full:
            self._set_initialized(True)

    def _reset(self) -> None:
        self._flow.clear()
        self._volume.clear()
        self.value = 0.0


class AwesomeOscillator(Indicator):
    """
    Bill Williams' Awesome Oscillator: `SMA(hl2, fast) - SMA(hl2, slow)`.

    Invariant: two Nautilus `SimpleMovingAverage`s of at most `slow` inputs; initialized with the
    slow one.

    Parameters
    ----------
    fast : int
        The fast SMA period (> 0).
    slow : int
        The slow SMA period (> fast).

    """

    def __init__(self, fast: int = 5, slow: int = 34) -> None:
        PyCondition.positive_int(fast, "fast")
        PyCondition.positive_int(slow, "slow")
        if slow <= fast:
            raise ValueError(f"slow={slow!r} must be above fast={fast!r}")
        super().__init__(params=[fast, slow])
        self.fast = fast
        self.slow = slow
        self._fast = SimpleMovingAverage(fast)
        self._slow = SimpleMovingAverage(slow)
        self.value = 0.0

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.high.as_double(), bar.low.as_double())

    def update_raw(self, high: float, low: float) -> None:
        _check_bar(high, low)
        hl2 = (high + low) / 2.0
        self._fast.update_raw(hl2)
        self._slow.update_raw(hl2)
        self.value = self._fast.value - self._slow.value
        self._set_has_inputs(True)
        if self._slow.initialized:
            self._set_initialized(True)

    def _reset(self) -> None:
        self._fast.reset()
        self._slow.reset()
        self.value = 0.0


class ZigZag(Indicator):
    """
    ZigZag swing points over highs and lows (rules in the module docstring).

    Invariant: O(1) state -- the leg direction (+1 up, -1 down, 0 before the first pivot), the
    running extreme(s) with their bar numbers, the last confirmed pivot and the update count. A
    pivot is confirmed only by a later bar reversing at least `deviation_pct` from it, and a
    confirmed pivot never moves; only `extreme_price`/`extreme_bar` repaint. Initialized at the
    first confirmed pivot.

    Parameters
    ----------
    deviation_pct : float
        The reversal that confirms a pivot, in percent of the extreme (0 < deviation_pct < 100).

    """

    def __init__(self, deviation_pct: float = 5.0) -> None:
        PyCondition.in_range(deviation_pct, 0.0, 100.0, "deviation_pct")
        if deviation_pct in (0.0, 100.0):
            raise ValueError(f"deviation_pct={deviation_pct!r} must lie strictly in (0, 100)")
        super().__init__(params=[deviation_pct])
        self.deviation_pct = deviation_pct
        self._d = deviation_pct / 100.0
        self._clear()

    def _clear(self) -> None:
        self.direction = 0
        self.confirmed = False
        self.pivot_price: float | None = None
        self.pivot_bar: int | None = None
        self.extreme_price: float | None = None
        self.extreme_bar: int | None = None
        # The tracked extremes: before the first pivot the highest high and the lowest low; after
        # it, the current leg's extreme is `_high` in an up leg and `_low` in a down leg.
        self._high = 0.0
        self._high_bar = 0
        self._low = 0.0
        self._low_bar = 0
        self._bar = -1

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.high.as_double(), bar.low.as_double())

    def update_raw(self, high: float, low: float) -> None:
        _check_bar(high, low)
        if low <= 0:
            # The reversal is a percent of the extreme: a zero or negative price has none.
            raise ValueError(f"ZigZag needs positive prices, got l={low}")
        self._bar += 1
        self.confirmed = False
        if not self.has_inputs:
            self._high, self._low = high, low
            self._set_has_inputs(True)
        elif self.direction == 0:
            self._undecided(high, low)
        elif self.direction == 1:
            self._up_leg(high, low)
        else:
            self._down_leg(high, low)

    def _undecided(self, high: float, low: float) -> None:
        if high > self._high:
            self._high, self._high_bar = high, self._bar
        if low < self._low:
            self._low, self._low_bar = low, self._bar
        if self._high_bar != self._bar and low <= self._high * (1 - self._d):
            self._confirm_top(low)
        elif self._low_bar != self._bar and high >= self._low * (1 + self._d):
            self._confirm_bottom(high)

    def _up_leg(self, high: float, low: float) -> None:
        if high > self._high:
            self._high, self._high_bar = high, self._bar
            self.extreme_price, self.extreme_bar = high, self._bar
        elif low <= self._high * (1 - self._d):
            self._confirm_top(low)

    def _down_leg(self, high: float, low: float) -> None:
        if low < self._low:
            self._low, self._low_bar = low, self._bar
            self.extreme_price, self.extreme_bar = low, self._bar
        elif high >= self._low * (1 + self._d):
            self._confirm_bottom(high)

    def _confirm_top(self, low: float) -> None:
        """Confirm the tracked high as a top; a down leg starts at this bar's `low`."""
        self._confirm(self._high, self._high_bar, -1)
        self._low, self._low_bar = low, self._bar
        self.extreme_price, self.extreme_bar = low, self._bar

    def _confirm_bottom(self, high: float) -> None:
        """Confirm the tracked low as a bottom; an up leg starts at this bar's `high`."""
        self._confirm(self._low, self._low_bar, 1)
        self._high, self._high_bar = high, self._bar
        self.extreme_price, self.extreme_bar = high, self._bar

    def _confirm(self, price: float, bar: int, direction: int) -> None:
        self.pivot_price, self.pivot_bar, self.confirmed = price, bar, True
        self.direction = direction
        self._set_initialized(True)

    def _reset(self) -> None:
        self._clear()
