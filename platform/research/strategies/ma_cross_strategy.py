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
"""
Moving-average cross strategy: two moving averages of one `MovingAverageType` on a time-bar
stream, an entry when the fast one crosses the slow one, and one of three exit styles. Every
`MovingAverageType` is reachable by name through `ma_type`, so a notebook compares them with one
parameter. It imports only `nautilus_trader` (and its sibling `_bars`).

**Averages.** `MovingAverageFactory.create(period, ma_type)` builds each, except `ADAPTIVE`, which
the factory has no branch for (an upstream gap): it is built explicitly as
`AdaptiveMovingAverage(period_er=period, period_alpha_fast=2, period_alpha_slow=max(3, period))`:
the efficiency-ratio window and the slow smoothing both scale with `period`, so a fast and a slow
average differ in more than the ER window (a fixed slow alpha made their cross degenerate). An
`AverageTrueRange(atr_period)` is registered too, in every mode, so `indicators_initialized()` also
waits for it (a stop mode needs it; the cross mode pays the same few warm-up bars for one rule).

**Entry.** On the bar where the fast average goes from not-above to above the slow one (long) or
the reverse (short, unless `long_only`): flat, with no live order, every indicator initialized. The
first initialized bar has no previous state, so it is never a cross. Market order, submitted in
`on_bar` of the closed bar (no look-ahead). At most one position.

**Exit.** The opposite cross closes the position (`cross`, always active). `atr_stop` and
`trailing_atr` add protection placed when the position opens, at the ATR distance
`atr_multiple * ATR` of the entry bar: a reduce-only STOP_MARKET at `avg_px_open -/+ distance`, or
a reduce-only TRAILING_STOP_MARKET with `trailing_offset = distance` (`TrailingOffsetType.PRICE`,
`TriggerType.LAST_PRICE`). The protective order follows the position's quantity (a partial fill
replaces it), and is cancelled when the position closes -- by the stop itself, by the opposite cross
or otherwise (`on_position_closed` cancels every order of the instrument). A stop the venue rejects
or denies closes the position at once (logged at error), never leaves it unprotected.

**Holes.** A bar more than one bar step after the previous, or with zero volume, resets every
indicator and the cross state, as `CandlePatternStrategy` does: a hole bar is fed to the fresh
indicators as their first bar, and never enters; a zero-volume bar is not fed (with Nautilus's
default `time_bars_build_with_no_updates=True` a tradeless interval would otherwise appear as a
flat bar). A bar not after the previous one is skipped with a warning.

Known limit: after the opposite cross closes a position the strategy is flat while the averages
stay crossed, so it re-enters only on the next cross (it is not always in the market); upgrade path:
an `entry="state"` option entering on the averages' order instead of the cross.

Known limit: the trailing offset and the stop price are on the instrument's price grid
(`make_price`), so a distance below one tick rounds to zero and closes the position instead.
"""

import math
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AdaptiveMovingAverage
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import MovingAverage
from nautilus_trader.indicators import MovingAverageFactory
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import TrailingOffsetType
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.events import PositionChanged
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy
from research.strategies._bars import BarSequence
from research.strategies._bars import check_count
from research.strategies._bars import check_trade_size
from research.strategies._bars import resolve_bar_type
from research.strategies._bars import size_problem


EXITS = ("cross", "atr_stop", "trailing_atr")
_STOP_TYPES = (OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET)


class MACrossStrategyConfig(StrategyConfig, frozen=True, forbid_unknown_fields=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    bar_type : str | None
        A Nautilus time bar type of the instrument; None is `<instrument_id>-1-MINUTE-LAST-INTERNAL`.
    trade_size : Decimal
        Base-asset quantity per entry.
    ma_type : str
        A `MovingAverageType` name (`SIMPLE`, `EXPONENTIAL`, `DOUBLE_EXPONENTIAL`, `WILDER`,
        `HULL`, `ADAPTIVE`, `WEIGHTED`, `VARIABLE_INDEX_DYNAMIC`).
    fast_period / slow_period : int
        The averages' periods in bars; `fast_period < slow_period`.
    exit : str
        `cross`, `atr_stop` or `trailing_atr` (see the module docstring).
    atr_period : int
        The ATR's period, in bars.
    atr_multiple : float
        The stop / trailing distance in ATRs (> 0).
    long_only : bool
        Whether a downward cross only closes a long instead of opening a short.

    Unknown fields are rejected, so a typo'd parameter fails the build.
    """

    instrument_id: InstrumentId
    bar_type: str | None = None
    trade_size: Decimal = Decimal("0.01")
    ma_type: str = "EXPONENTIAL"
    fast_period: int = 10
    slow_period: int = 20
    exit: str = "cross"
    atr_period: int = 14
    atr_multiple: float = 2.0
    long_only: bool = False


def _check_config(config: MACrossStrategyConfig) -> None:
    if config.ma_type not in MovingAverageType.__members__:
        raise ValueError(
            f"ma_type must be one of {list(MovingAverageType.__members__)}, not {config.ma_type!r}"
        )
    if config.exit not in EXITS:
        raise ValueError(f"exit must be one of {EXITS}, not {config.exit!r}")
    for name in ("fast_period", "slow_period", "atr_period"):
        check_count(name, getattr(config, name))
    if config.fast_period >= config.slow_period:
        raise ValueError(
            f"fast_period {config.fast_period} must be below slow_period {config.slow_period}"
        )
    if not (math.isfinite(config.atr_multiple) and config.atr_multiple > 0):
        raise ValueError(f"atr_multiple must be finite and > 0, was {config.atr_multiple}")
    check_trade_size(config.trade_size)


def _average(ma_type: str, period: int) -> MovingAverage:
    """Return the `ma_type` average; ADAPTIVE explicitly, as the factory has no branch for it."""
    kind = MovingAverageType[ma_type]
    if kind == MovingAverageType.ADAPTIVE:
        return AdaptiveMovingAverage(
            period_er=period, period_alpha_fast=2, period_alpha_slow=max(3, period)
        )
    return MovingAverageFactory.create(period, kind)


def _is_protective(order: Order) -> bool:
    return order.order_type in _STOP_TYPES and order.is_reduce_only


class MACrossStrategy(Strategy):
    """
    Trades the cross of a fast and a slow moving average, with an optional ATR stop.

    Invariant: at most one position; while one is open under a stop exit, every position event
    leaves one live reduce-only protective order whose unfilled quantity is the position's, or
    closes the position (an unpriceable or refused stop). The cross state and every indicator
    restart at a hole, so no cross spans one. A bad config raises `ValueError` before any node runs.
    """

    def __init__(self, config: MACrossStrategyConfig) -> None:
        super().__init__(config)
        _check_config(config)
        self._bar_type = resolve_bar_type(config.instrument_id, config.bar_type)
        self._sequence = BarSequence(int(self._bar_type.spec.timedelta.value))
        self._fast = _average(config.ma_type, config.fast_period)
        self._slow = _average(config.ma_type, config.slow_period)
        self._atr = AverageTrueRange(config.atr_period)
        self._above: bool | None = None
        self._distance: float | None = None
        self.instrument: Instrument | None = None

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return
        problem = size_problem(Decimal(self.config.trade_size), instrument)
        if problem is not None:
            self.log.error(f"trade_size {self.config.trade_size} {problem}")
            self.stop()
            return
        self.instrument = instrument
        for indicator in (self._fast, self._slow, self._atr):
            self.register_indicator_for_bars(self._bar_type, indicator)
        self.subscribe_bars(self._bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self._sequence.in_order(bar):
            self.log.warning(
                f"{bar.bar_type}: bar at {bar.ts_event} is not after the last, skipped"
            )
            return
        traded = bar.volume.raw > 0
        hole = self._sequence.is_hole(bar)
        if hole or not traded:
            self._restart(bar if traded else None)
        if hole or not traded or not self.indicators_initialized():
            return
        signal = self._cross()
        position = self._open_position()
        if position is not None:
            self._maybe_close(position, signal)
        elif signal and self._is_idle():
            self._maybe_enter(signal)

    def _restart(self, first_bar: Bar | None) -> None:
        """Reset every indicator and the cross state; feed `first_bar` to the fresh indicators."""
        for indicator in (self._fast, self._slow, self._atr):
            indicator.reset()
            if first_bar is not None:
                indicator.handle_bar(first_bar)
        self._above = None

    def _cross(self) -> int:
        """Return +1 if the fast average crossed above the slow one on this bar, -1 below, else 0."""
        if self._fast.value == self._slow.value:
            return 0  # a touch is not a cross, and the side before it stays the reference
        above = self._fast.value > self._slow.value
        previous, self._above = self._above, above
        if previous is None or previous == above:
            return 0
        return 1 if above else -1

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
        """Return True with no live order; a protective order left open while flat is cancelled."""
        orders = self._live_orders()
        for order in orders:
            if _is_protective(order) and order.status != OrderStatus.PENDING_CANCEL:
                self.log.warning(f"stop {order.client_order_id} still open while flat, cancelling")
                self.cancel_order(order)
        return not orders

    def _maybe_close(self, position: Position, signal: int) -> None:
        opposite = signal < 0 if position.side == PositionSide.LONG else signal > 0
        closing = any(not _is_protective(o) for o in self._live_orders())
        if opposite and not closing:
            self.close_position(position)

    def _maybe_enter(self, signal: int) -> None:
        if signal < 0 and self.config.long_only:
            return
        if self.config.exit != "cross":
            if not (self._atr.value > 0):
                self.log.warning(
                    f"{self.config.instrument_id}: entry skipped at ts {self.clock.timestamp_ns()}, "
                    f"ATR is {self._atr.value} (no stop distance); the cross is consumed"
                )
                return
            self._distance = self.config.atr_multiple * self._atr.value
        assert self.instrument is not None  # on_start stopped the strategy otherwise
        self.submit_order(
            self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY if signal > 0 else OrderSide.SELL,
                quantity=self.instrument.make_qty(self.config.trade_size),
            )
        )

    # --- the protective order ------------------------------------------------------------------

    def _sync_protection(self, position: Position) -> None:
        """Keep one live protective order of the position's quantity; none or stale places one."""
        if self.config.exit == "cross":
            return
        stale = [o for o in self._live_orders() if _is_protective(o)]
        if any(self._covers(o, position) for o in stale):
            return
        order = self._protective_order(position)
        if order is None:
            self.log.error(f"{position.id}: no protective order can be priced, closing now")
            self.close_position(position)
            return
        self.submit_order(order, position_id=position.id)
        for old in stale:
            if old.status != OrderStatus.PENDING_CANCEL:
                self.cancel_order(old)

    @staticmethod
    def _covers(order: Order, position: Position) -> bool:
        return order.status != OrderStatus.PENDING_CANCEL and order.leaves_qty == position.quantity

    def _protective_order(self, position: Position) -> Order | None:
        """Build the stop (or trailing stop) of `position`; None if its price is not positive."""
        if self.instrument is None or self._distance is None:
            return None
        offset = self.instrument.make_price(self._distance)
        if offset.as_double() <= 0:
            return None
        long = position.side == PositionSide.LONG
        side = OrderSide.SELL if long else OrderSide.BUY
        if self.config.exit == "trailing_atr":
            return self.order_factory.trailing_stop_market(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=position.quantity,
                trailing_offset=offset.as_decimal(),
                trailing_offset_type=TrailingOffsetType.PRICE,
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
        raw = (
            position.avg_px_open - offset.as_double()
            if long
            else position.avg_px_open + offset.as_double()
        )
        if raw <= 0:
            return None
        return self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=position.quantity,
            trigger_price=self.instrument.make_price(raw),
            reduce_only=True,
        )

    def _protection_refused(self, client_order_id: ClientOrderId, what: str) -> None:
        order = self.cache.order(client_order_id)
        position = self._open_position()
        if order is None or not _is_protective(order) or position is None:
            return
        self.log.error(f"{position.id}: protective order {client_order_id} {what}, closing")
        self.close_position(position)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._protection_refused(event.client_order_id, f"rejected: {event.reason}")

    def on_order_denied(self, event: OrderDenied) -> None:
        self._protection_refused(event.client_order_id, f"denied: {event.reason}")

    def on_position_opened(self, event: PositionOpened) -> None:
        position = self.cache.position(event.position_id)
        if position is not None:
            self._sync_protection(position)

    def on_position_changed(self, event: PositionChanged) -> None:
        position = self.cache.position(event.position_id)
        if position is not None and position.is_open:
            self._sync_protection(position)

    def on_position_closed(self, event: PositionClosed) -> None:
        # The stop (or a close still resting) must not outlive the position it protected.
        self.cancel_all_orders(self.config.instrument_id)
        self._distance = None

    def on_reset(self) -> None:
        for indicator in (self._fast, self._slow, self._atr):
            indicator.reset()
        self._above = None
        self._distance = None
        self._sequence.last_ns = None
        self.instrument = None
