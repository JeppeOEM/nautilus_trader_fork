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
Candlestick pattern strategy (Story 27.8): trades `kernel.candle_patterns.CandlePattern` signals
with the scanner's EMA trend filter, in a backtest (`backtest_candle_pattern.py`, the
`04_backtest_evaluation` notebook) and as a paper bot (`strategy = "candle_pattern"` in
`bots/config.toml`, resolved by string path through `StrategyFactory`, so bots never imports this
module). It imports only `kernel` and `nautilus_trader`.

**Signals.** One `CandlePattern` per distinct configured name (a name in both sets, e.g.
`ENGULFING`, gets one detector), fed every closed bar of `bar_type` together with Nautilus's
`ExponentialMovingAverage(trend_ema_period)` over the closes and
`AverageTrueRange(atr_period)`. A long fires when a `long_patterns` detector reads +100, a short
when a `short_patterns` detector reads -100. Each name must be able to fire in its set's direction
(`_DIRECTIONS`); `DOJI` (non-directional) is refused.

**Trend filter** (exactly `research.application.patterns.filter_hits`): `above` passes when the
fired bar's close is above the EMA, `below` when below, `any` always; the EMA includes the fired
bar; an EMA that is not initialized fails `above` and `below`.

**Entry.** Flat, with no open or in-flight order of this strategy: exactly one direction fired
(both on one bar is ambiguous: no entry, logged at debug), it passes the filter, and the ATR is
initialized and positive. The market order is submitted in `on_bar` of the closed pattern bar --
no look-ahead, the bar is complete -- so it fills at the market price at that bar's close, the
next bar's open (live: the quotes then; a backtest: see the fills Known limit). At most one
position.

**Exit.** After `exit_bars` bars held (a position opened at bar t's close closes at bar
t + exit_bars's close), or at the bar where an opposite pattern fires (a `short_patterns` -100
while long, a `long_patterns` +100 while short; no filter, no same-bar reversal -- the next entry
needs a new pattern), or on the ATR stop: when a position opens, a reduce-only `STOP_MARKET` goes
to `avg_px_open -/+ stop_atr_multiple * ATR`, and it is cancelled when the position closes any
other way. Bars held are derived from the position itself (the bar closes since its `ts_opened`),
so a restarted bot keeps counting from the entry. While a close is working (an order in flight or
an open order other than the stop), nothing else is submitted.

**The stop is kept, not only placed.** On every bar in a position, and at once when our stop is
rejected, denied, cancelled or expires, or the position's quantity changes, `_ensure_stop` checks
for a live reduce-only stop whose unfilled quantity (`leaves_qty`, so a stop part-way through its
own fill still counts) is the position's; without one it places a new stop (then cancels any stale
one) at `avg_px_open -/+` the position's stop distance. That distance is `stop_atr_multiple * ATR`
taken when the entry was submitted (the pattern bar's ATR; after a restart, the ATR when the stop
is first priced) and kept for that position -- keyed by its id and open time, so neither a hole
that resets the ATR before the fill or mid-position, nor a position closed while the strategy was
stopped (Nautilus delivers no events to a stopped strategy), ever prices a stop from another
position's state. A stop price that would be <= 0, or no distance and an ATR that is not
initialized, closes the position at once and logs an error, and so does a second consecutive stop
failure (a venue that refuses the stop twice running will not take it; the count restarts when the
venue accepts a stop, and for each new position).

**Flat means no live order.** An entry needs no open or in-flight order of this strategy; a stop
left open while flat (its cancel lost or refused) is cancelled again, with a warning, on every bar
until it is gone, so a stale trigger never protects -- or blocks -- the next position.

**Holes.** A bar whose `ts_event` is more than one bar step after the previous one, or a bar with
zero volume, is a hole: every detector, the EMA and the ATR reset before the next bar is fed, as
the scanner resets at every NaN row -- a multi-bar pattern never compares bars that are not
adjacent. A zero-volume bar is itself not fed (the candle store holds no bar for a bucket with no
trade), because with Nautilus's default `time_bars_build_with_no_updates=True` an internal time bar
with no trade is still emitted, flat at the previous close (measured 2026-09-28 on 1.229.0), so a
gap in trades never shows up as a gap in `ts_event`. A bar not after the previous one (a
duplicate or out-of-order `ts_event`) is skipped with a warning and fed to nothing.

Known limit: the trend filter is direction-independent -- `above` gates shorts too -- because the
scanner's `filter_hits` is, and the two must agree for a scanner hit to be the backtest's entry;
upgrade path: a per-direction condition added to both at once (scanner and strategy).

Known limit: a backtest has only trade ticks, so fills come from trades. Through `NodeRunner` every
order reaches the simulated exchange `RunSpec.latency_ms` after it is sent, so an entry fills at the
first trade after the pattern bar's close plus that latency; with no latency (`latency_ms=0`, or a
bare `BacktestEngine` without a latency model) it fills at the pattern bar's last trade, stamped at
the bar's close. A stop triggers only when a trade moves the matching engine's side it watches
(Nautilus's `trade_execution`: a seller-aggressor trade at or below a long's stop), and fills at
that trade's price, so a gap through the stop fills at the gap price. Live, the Sandbox fills
against the subscribed quotes; upgrade path: replay the collector's quotes as well
(`data="seconds"`-style derived quotes) once a trades+quotes kind exists in `RunSpec`.

Known limit: the detectors run at the kernel's default `Thresholds`; a scanner run with other
thresholds finds hits this strategy does not trade. Upgrade path: threshold fields on the config,
passed to each `CandlePattern`.

Known limit: the first bar after start (or after a live restart) is usually partial -- Nautilus's
default `time_bars_skip_first_non_full_bar=False` -- where the scanner drops a bar under 90%
observed; it warms the indicators up like any other bar. Upgrade path: skip bars before the first
full step here, or set that data-engine option on the node.

Known limit: after a paper bot's restart, a stop the Redis-backed Cache still lists as open does
not exist on the Sandbox's fresh simulated exchange, so `_ensure_stop` counts that phantom order
and places none; flat, the phantom's cancel is refused on every bar (a warning each bar) and the
bot never enters again. Upgrade path: in `on_start`, reconcile the cached open orders against the
venue (mark the unknown ones closed) and let `_ensure_stop` place a live stop.
"""

import math
from decimal import Decimal
from decimal import InvalidOperation
from types import MappingProxyType

from kernel.candle_patterns import BEARISH
from kernel.candle_patterns import BULLISH
from kernel.candle_patterns import NON_DIRECTIONAL
from kernel.candle_patterns import CandlePattern
from kernel.candle_patterns import PatternName

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarAggregation
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.events import OrderAccepted
from nautilus_trader.model.events import OrderCanceled
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.events import OrderExpired
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.events import PositionChanged
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.orders import Order
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy


# The scanner's filter vocabulary, restated: a strategy module imports only `kernel` and
# `nautilus_trader`. `test_candle_pattern_strategy.py` pins it equal to
# `research.application.patterns.CONDITIONS`.
CONDITIONS = ("any", "above", "below")

# The directions each pattern can fire in, as `kernel/candle_patterns.py`'s docstring defines them
# (`DOJI` is +100 but non-directional). `test_candle_pattern_strategy.py` replays the kernel's
# hand-drawn cases to confirm every entry.
_DIRECTIONS: MappingProxyType[PatternName, frozenset[int]] = MappingProxyType(
    {
        PatternName.DOJI: frozenset({BULLISH}),
        PatternName.DRAGONFLY_DOJI: frozenset({BULLISH}),
        PatternName.GRAVESTONE_DOJI: frozenset({BEARISH}),
        PatternName.HAMMER: frozenset({BULLISH}),
        PatternName.HANGING_MAN: frozenset({BEARISH}),
        PatternName.INVERTED_HAMMER: frozenset({BULLISH}),
        PatternName.SHOOTING_STAR: frozenset({BEARISH}),
        PatternName.MARUBOZU: frozenset({BULLISH, BEARISH}),
        PatternName.SPINNING_TOP: frozenset({BULLISH, BEARISH}),
        PatternName.ENGULFING: frozenset({BULLISH, BEARISH}),
        PatternName.HARAMI: frozenset({BULLISH, BEARISH}),
        PatternName.HARAMI_CROSS: frozenset({BULLISH, BEARISH}),
        PatternName.PIERCING: frozenset({BULLISH}),
        PatternName.DARK_CLOUD_COVER: frozenset({BEARISH}),
        PatternName.TWEEZER_TOP: frozenset({BEARISH}),
        PatternName.TWEEZER_BOTTOM: frozenset({BULLISH}),
        PatternName.MORNING_STAR: frozenset({BULLISH}),
        PatternName.EVENING_STAR: frozenset({BEARISH}),
        PatternName.THREE_WHITE_SOLDIERS: frozenset({BULLISH}),
        PatternName.THREE_BLACK_CROWS: frozenset({BEARISH}),
        PatternName.THREE_INSIDE_UP: frozenset({BULLISH}),
        PatternName.THREE_INSIDE_DOWN: frozenset({BEARISH}),
    }
)


class CandlePatternStrategyConfig(StrategyConfig, frozen=True, forbid_unknown_fields=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    bar_type : str | None
        A Nautilus bar type string of this instrument with time aggregation. None means
        `<instrument_id>-1-MINUTE-LAST-INTERNAL` (bars aggregated from trade ticks, so every venue
        with a trade archive works); an `EXTERNAL` bar type is used as given.
    long_patterns / short_patterns : tuple[str, ...]
        `PatternName` names whose +100 opens a long / whose -100 opens a short (and closes a long).
    trend_ema_period : int
        The trend EMA's period, in bars.
    trend_condition : str
        `above`, `below` or `any` (the scanner's `CONDITIONS`).
    trade_size : Decimal
        Base-asset quantity per entry.
    exit_bars : int
        Bars held before a time exit (>= 1).
    atr_period : int
        The ATR's period, in bars.
    stop_atr_multiple : float
        The stop's distance from the entry price in ATRs (> 0).
    allow_short : bool
        Whether `short_patterns` open shorts; they close longs either way.

    Unknown fields are rejected (`forbid_unknown_fields`), so a typo'd parameter fails the build.
    """

    instrument_id: InstrumentId
    bar_type: str | None = None
    long_patterns: tuple[str, ...] = ("HAMMER", "ENGULFING", "MORNING_STAR")
    short_patterns: tuple[str, ...] = ("SHOOTING_STAR", "ENGULFING", "EVENING_STAR")
    trend_ema_period: int = 50
    trend_condition: str = "above"
    trade_size: Decimal = Decimal("0.01")
    exit_bars: int = 10
    atr_period: int = 14
    stop_atr_multiple: float = 2.0
    allow_short: bool = True


def _resolve_bar_type(config: CandlePatternStrategyConfig) -> BarType:
    """Return the config's bar type, or `ValueError` if it is not a time bar of its instrument."""
    text = config.bar_type or f"{config.instrument_id}-1-MINUTE-LAST-INTERNAL"
    bar_type = BarType.from_str(text)
    if bar_type.instrument_id != config.instrument_id:
        raise ValueError(f"bar_type {text!r} is not of instrument {config.instrument_id}")
    if not bar_type.spec.is_time_aggregated():
        raise ValueError(f"bar_type {text!r} must be time-aggregated (a hole is a time gap)")
    if bar_type.spec.aggregation in (BarAggregation.MONTH, BarAggregation.YEAR):
        # Nautilus gives them a nominal 30/365-day step: every 31-day month would read as a hole.
        raise ValueError(f"bar_type {text!r} has no fixed step (a hole is a gap over one step)")
    return bar_type


def _pattern_names(names: tuple[str, ...], direction: int, field: str) -> tuple[PatternName, ...]:
    """Return `names` as `PatternName`s (each once), each able to fire in `direction`."""
    if isinstance(names, str):
        raise ValueError(f"{field} must be a sequence of pattern names, not the str {names!r}")
    return tuple(dict.fromkeys(_pattern_name(name, direction, field) for name in names))


def _pattern_name(name: str, direction: int, field: str) -> PatternName:
    """Return `name`'s `PatternName`; `ValueError` if it is unknown or never fires `direction`."""
    if name not in PatternName.__members__:
        raise ValueError(f"{field}: unknown pattern {name!r}; known: {list(PatternName)}")
    pattern = PatternName[name]
    if pattern in NON_DIRECTIONAL:
        raise ValueError(f"{field}: {name} is non-directional and cannot open a position")
    if direction not in _DIRECTIONS[pattern]:
        side = "bullish" if direction == BULLISH else "bearish"
        raise ValueError(f"{field}: {name} never fires {side} ({direction:+d})")
    return pattern


def _check_numbers(config: CandlePatternStrategyConfig) -> None:
    if config.trend_condition not in CONDITIONS:
        condition = config.trend_condition
        raise ValueError(f"trend_condition must be one of {CONDITIONS}, not {condition!r}")
    for name in ("exit_bars", "trend_ema_period", "atr_period"):
        _check_count(name, getattr(config, name))
    multiple = config.stop_atr_multiple
    if not (math.isfinite(multiple) and multiple > 0):
        raise ValueError(f"stop_atr_multiple must be finite and > 0, was {multiple}")
    _check_trade_size(config.trade_size)
    if not config.long_patterns and not (config.allow_short and config.short_patterns):
        raise ValueError("no pattern can open a position: set long_patterns or short_patterns")


def _check_count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an int >= 1, was {value!r}")


def _check_trade_size(trade_size: Decimal) -> None:
    try:
        size = Decimal(trade_size)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"trade_size must be a decimal number, was {trade_size!r}") from exc
    # `is_finite` first: comparing a Decimal NaN raises `InvalidOperation`, not `ValueError`.
    if not (size.is_finite() and size > 0):
        raise ValueError(f"trade_size must be finite and > 0, was {trade_size}")


def _is_stop(order: Order) -> bool:
    return order.order_type == OrderType.STOP_MARKET and order.is_reduce_only


def _size_problem(size: Decimal, instrument: Instrument) -> str | None:
    """Return why `size` cannot be traded on `instrument` as given; None if it can."""
    increment = instrument.size_increment.as_decimal()
    if size % increment != 0:
        # `make_qty` would round it, silently trading another amount (DATA-07).
        return f"is not a multiple of {instrument.id}'s size_increment {increment}"
    low, high = instrument.min_quantity, instrument.max_quantity
    if low is not None and size < low.as_decimal():
        return f"is below {instrument.id}'s min_quantity {low}"
    if high is not None and size > high.as_decimal():
        return f"is above {instrument.id}'s max_quantity {high}"
    return None


class CandlePatternStrategy(Strategy):
    """
    Trades configured candlestick patterns with an EMA trend filter, a bar-count exit, an
    opposite-pattern exit and an ATR stop (the rules and limits are in the module docstring).

    Invariant: at most one position; while one is open, every bar and every stop or position
    event either finds a live reduce-only stop whose unfilled quantity is the position's, places
    one, or closes the position (an unpriceable stop, or a stop refused twice running) -- so a
    position lacks its stop only between a stop event and the venue's answer, or while its close
    is working, and after a restart as the phantom-order Known limit says. Stop state (distance,
    failure count) belongs to one position and is never carried to the next. Every indicator restarts at a hole, so no
    pattern spans one. A bad config raises `ValueError` here, before any node runs it.
    """

    def __init__(self, config: CandlePatternStrategyConfig) -> None:
        super().__init__(config)
        self._bar_type = _resolve_bar_type(config)
        self._long = _pattern_names(config.long_patterns, BULLISH, "long_patterns")
        self._short = _pattern_names(config.short_patterns, BEARISH, "short_patterns")
        _check_numbers(config)
        self._step_ns = int(self._bar_type.spec.timedelta.value)
        self._detectors = {
            name: CandlePattern(name) for name in dict.fromkeys((*self._long, *self._short))
        }
        self._ema = ExponentialMovingAverage(config.trend_ema_period)
        self._atr = AverageTrueRange(config.atr_period)
        self.instrument: Instrument | None = None
        # ts of the last market data seen, read by the bots' heartbeat (`StrategyCacheReader`).
        self.last_data_ns: int = 0
        self._last_bar_ns: int | None = None
        # Stop failures (rejected, denied, cancelled, expired) since the venue last accepted one of
        # our stops: the second closes the position instead of placing the stop again. A count,
        # not a timestamp -- live, every event carries its own wall-clock `ts_event`.
        self._stop_failures: int = 0
        # The stop distance of the last entry submitted (`stop_atr_multiple * ATR` of the pattern
        # bar), taken over by the position it opens.
        self._entry_distance: float | None = None
        # The position the stop state below belongs to (its id and open time: a NETTING reopen
        # keeps the id) and its stop distance, so a re-placed stop never depends on an ATR a hole
        # has since reset, nor on another position's distance.
        self._stop_key: tuple[PositionId, int] | None = None
        self._stop_distance: float | None = None

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return
        if not self._tradeable(instrument):
            self.stop()
            return
        self.instrument = instrument
        self.subscribe_bars(self._bar_type)
        # Quotes keep the heartbeat fresh between bars and feed the Sandbox fill engine live.
        self.subscribe_quote_ticks(self.config.instrument_id)

    def _tradeable(self, instrument: Instrument) -> bool:
        """
        Return whether `trade_size` is on the instrument's size grid and inside its quantity
        limits; otherwise every entry would be rounded or denied, bar after bar.
        """
        problem = _size_problem(Decimal(self.config.trade_size), instrument)
        if problem is not None:
            self.log.error(f"trade_size {self.config.trade_size} {problem}")
            return False
        return True

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.last_data_ns = max(self.last_data_ns, tick.ts_event)

    def on_bar(self, bar: Bar) -> None:
        self.last_data_ns = max(self.last_data_ns, bar.ts_event)
        if not self._in_order(bar):
            return
        traded = bar.volume.raw > 0
        if self._is_hole(bar.ts_event) or not traded:
            self._reset_signals()
        if traded:
            self._feed(bar)
        position = self._open_position()
        if position is not None:
            self._manage(position, bar)
        elif self._is_idle() and traded:
            self._maybe_enter(bar)

    def _in_order(self, bar: Bar) -> bool:
        """Return False, with a warning, for a bar not after the previous one (duplicate, late)."""
        last = self._last_bar_ns
        if last is None or bar.ts_event > last:
            return True
        self.log.warning(f"{bar.bar_type}: bar at {bar.ts_event} is not after {last}, skipped")
        return False

    def _is_hole(self, ts_event: int) -> bool:
        """Record `ts_event` as the last bar's; True if it is over one step after the previous."""
        last, self._last_bar_ns = self._last_bar_ns, ts_event
        return last is not None and ts_event - last > self._step_ns

    def _feed(self, bar: Bar) -> None:
        for detector in self._detectors.values():
            detector.handle_bar(bar)
        self._ema.handle_bar(bar)
        self._atr.handle_bar(bar)

    def _reset_signals(self) -> None:
        for detector in self._detectors.values():
            detector.reset()
        self._ema.reset()
        self._atr.reset()

    def _fired(self, patterns: tuple[PatternName, ...], direction: int) -> bool:
        return any(
            self._detectors[p].initialized and self._detectors[p].value == direction
            for p in patterns
        )

    def _open_position(self) -> Position | None:
        positions = self.cache.positions_open(
            instrument_id=self.config.instrument_id, strategy_id=self.id
        )
        return positions[0] if positions else None

    def _live_orders(self) -> list[Order]:
        """Return this strategy's open and in-flight orders on the instrument, each once."""
        iid = self.config.instrument_id
        open_orders = self.cache.orders_open(instrument_id=iid, strategy_id=self.id)
        inflight = self.cache.orders_inflight(instrument_id=iid, strategy_id=self.id)
        return list({o.client_order_id: o for o in (*open_orders, *inflight)}.values())

    def _is_idle(self) -> bool:
        """Flat: True with no live order; a stop left open while flat is cancelled again first."""
        orders = self._live_orders()
        for order in orders:
            if _is_stop(order) and order.status != OrderStatus.PENDING_CANCEL:
                self.log.warning(f"stop {order.client_order_id} still open while flat, cancelling")
                self.cancel_order(order)
        return not orders

    def _closing(self) -> bool:
        """Return True while a close is working: any live order that is not a stop."""
        return any(not _is_stop(order) for order in self._live_orders())

    def _bars_held(self, position: Position, bar: Bar) -> int:
        """Return the bar closes since the position opened (a fill just after a close counts it)."""
        return -(-(bar.ts_event - position.ts_opened) // self._step_ns)

    def _manage(self, position: Position, bar: Bar) -> None:
        if self._closing():
            return
        if position.side == PositionSide.LONG:
            opposite = self._fired(self._short, BEARISH)
        else:
            opposite = self._fired(self._long, BULLISH)
        if opposite or self._bars_held(position, bar) >= self.config.exit_bars:
            self.close_position(position)
            return
        self._ensure_stop(position)

    def _maybe_enter(self, bar: Bar) -> None:
        side = self._signal_side(bar)
        if side is None or not self._trend_passes(bar.close.as_double()):
            return
        distance = self._atr_distance()
        if distance is None:
            return
        self._entry_distance = distance
        self._submit(side)

    def _signal_side(self, bar: Bar) -> OrderSide | None:
        """Return the side only one direction's patterns fired for; None if neither or both did."""
        long = self._fired(self._long, BULLISH)
        short = self._fired(self._short, BEARISH)
        if long and short:
            self.log.debug(f"{bar.bar_type} {bar.ts_event}: long and short fired, no entry")
            return None
        if long:
            return OrderSide.BUY
        return OrderSide.SELL if short and self.config.allow_short else None

    def _trend_passes(self, close: float) -> bool:
        condition = self.config.trend_condition
        if condition == "any":
            return True
        if not self._ema.initialized:
            return False
        return close > self._ema.value if condition == "above" else close < self._ema.value

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None  # on_start stopped the strategy otherwise
        self.submit_order(
            self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(self.config.trade_size),
            )
        )

    # --- the stop ------------------------------------------------------------------------------

    def _ensure_stop(self, position: Position) -> None:
        """
        Keep one live reduce-only stop of the position's quantity: none (or only stale ones)
        places a new stop first and then cancels the stale ones -- in that order, so a cancel
        confirmed synchronously already finds the new stop -- and an unpriceable stop closes the
        position.
        """
        self._track(position)
        stops = [order for order in self._live_orders() if _is_stop(order)]
        if any(self._covers(stop, position) for stop in stops):
            return
        price = self._stop_price(position)
        if price is None:
            self.log.error(
                f"{position.id}: no stop price (distance={self._stop_distance}, ATR "
                f"initialized={self._atr.initialized}, value={self._atr.value}), closing now"
            )
            self.close_position(position)
            return
        long = position.side == PositionSide.LONG
        stop = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL if long else OrderSide.BUY,
            quantity=position.quantity,
            trigger_price=price,
            reduce_only=True,
        )
        self.submit_order(stop, position_id=position.id)
        for stale in stops:
            if stale.status != OrderStatus.PENDING_CANCEL:
                self.cancel_order(stale)

    @staticmethod
    def _covers(stop: Order, position: Position) -> bool:
        return stop.status != OrderStatus.PENDING_CANCEL and stop.leaves_qty == position.quantity

    def _stop_price(self, position: Position) -> Price | None:
        """`avg_px_open -/+` the stop distance on the tick grid; None if it cannot be one."""
        distance = self._distance()
        if self.instrument is None or distance is None:
            return None
        long = position.side == PositionSide.LONG
        raw = position.avg_px_open - distance if long else position.avg_px_open + distance
        if raw <= 0:
            return None
        price = self.instrument.make_price(raw)
        return price if price.as_double() > 0 else None

    def _track(self, position: Position) -> None:
        """Start `position`'s own stop state if the state held is another position's."""
        key = (position.id, position.ts_opened)
        if key != self._stop_key:
            self._stop_key, self._stop_distance = key, self._entry_distance
            self._entry_distance = None
            self._stop_failures = 0

    def _distance(self) -> float | None:
        """Return the tracked position's stop distance, fixed on first use; None if none yet."""
        if self._stop_distance is None:
            self._stop_distance = self._atr_distance()
        return self._stop_distance

    def _atr_distance(self) -> float | None:
        if self._atr.initialized and self._atr.value > 0:
            return self.config.stop_atr_multiple * self._atr.value
        return None

    def _restore_stop(self) -> None:
        position = self._open_position()
        if position is not None and not self._closing():
            self._ensure_stop(position)

    def _stop_failed(self, client_order_id: ClientOrderId, what: str, ts_event: int) -> None:
        order = self.cache.order(client_order_id)
        if order is None or not _is_stop(order):
            return
        position = self._open_position()
        if position is None:
            return  # the position closed and its stop was cancelled with it
        if any(self._covers(o, position) for o in self._live_orders() if _is_stop(o)):
            return  # a stale stop we replaced, not the one protecting the position
        self._track(position)
        self.log.warning(f"stop {client_order_id} {what} at {ts_event}")
        self._stop_failures += 1
        if self._stop_failures >= 2:
            self.log.error(f"{position.id}: stop {what} again, closing the position")
            if not self._closing():
                self.close_position(position)
            return
        self._restore_stop()

    def on_order_accepted(self, event: OrderAccepted) -> None:
        order = self.cache.order(event.client_order_id)
        if order is not None and _is_stop(order):
            self._stop_failures = 0  # the venue takes our stops again

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._stop_failed(event.client_order_id, f"rejected: {event.reason}", event.ts_event)

    def on_order_denied(self, event: OrderDenied) -> None:
        self._stop_failed(event.client_order_id, f"denied: {event.reason}", event.ts_event)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._stop_failed(event.client_order_id, "canceled", event.ts_event)

    def on_order_expired(self, event: OrderExpired) -> None:
        self._stop_failed(event.client_order_id, "expired", event.ts_event)

    def on_position_opened(self, event: PositionOpened) -> None:
        position = self.cache.position(event.position_id)
        if position is None:
            self.log.error(f"{event.position_id} opened but is not in the cache; using ours")
            position = self._open_position()
        if position is None:
            self.log.error(f"{event.position_id}: no open position found to protect")
            return
        self._ensure_stop(position)

    def on_position_changed(self, event: PositionChanged) -> None:
        # A partial fill changed the quantity: the stop follows it.
        self._restore_stop()

    def on_position_closed(self, event: PositionClosed) -> None:
        # The stop (or a close still resting) must not outlive the position it protected.
        self.cancel_all_orders(self.config.instrument_id)
        self._clear_stop_state()

    def on_reset(self) -> None:
        self._reset_signals()
        self.instrument = None
        self.last_data_ns = 0
        self._last_bar_ns = None
        self._entry_distance = None
        self._clear_stop_state()

    def _clear_stop_state(self) -> None:
        self._stop_failures = 0
        self._stop_key = None
        self._stop_distance = None
