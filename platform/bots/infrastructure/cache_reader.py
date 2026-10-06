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
The Nautilus anti-corruption layer's read side: one bot's view of its own strategy.

`StrategyCacheReader` is the `BotRuntime` port over a live `Strategy` -- the only way supervision
and history see Nautilus. Every figure is scoped to the strategy's own `strategy_id`: the
Portfolio's `net_exposure`/`realized_pnl`/`unrealized_pnl` are account+instrument scoped in the
Rust core (`cache.positions_open(strategy_id=None, ...)`), so under one node hosting many bots they
would silently blend two bots the moment they share an instrument (AD-11).

Protective orders (Story 29.6) are classified by the order itself -- its reduce-only flag, its
type and its side against the open position -- never by the strategy's class, so any strategy's
resting stop shows up (a reduce-only order on the opening side, or any non-reduce-only order, is
not protection). An emulated order is held by the `OrderEmulator` rather than open at the venue,
so it is read from `orders_emulated` as well as `orders_open`.
"""

from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from bots.application.ports import PositionSnapshot
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.objects import Price
from nautilus_trader.model.orders import Order
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy


STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"

# A stop on the closing side triggers when the market moves against the position; a trailing
# stop's `trigger_price` is its current trigger, so the reported value follows the trail.
_STOP_TYPES = frozenset(
    {
        OrderType.STOP_MARKET,
        OrderType.STOP_LIMIT,
        OrderType.TRAILING_STOP_MARKET,
        OrderType.TRAILING_STOP_LIMIT,
    }
)
_IF_TOUCHED_TYPES = frozenset({OrderType.LIMIT_IF_TOUCHED, OrderType.MARKET_IF_TOUCHED})


def protective_exit(order: Order, closing_side: OrderSide) -> tuple[str, Price | None] | None:
    """
    Return `(kind, price)` when `order` protects a position closed by `closing_side`, else None.

    `price` is the level the exit acts at (a stop's or if-touched order's trigger, a limit's
    price); it is None for a trailing stop whose trigger is not calculated yet.
    """
    if not order.is_reduce_only or order.side != closing_side:
        return None
    if order.order_type in _STOP_TYPES:
        return STOP_LOSS, order.trigger_price
    if order.order_type == OrderType.LIMIT:
        return TAKE_PROFIT, order.price
    if order.order_type in _IF_TOUCHED_TYPES:
        return TAKE_PROFIT, order.trigger_price
    return None


@dataclass(frozen=True)
class ExitSummary:
    """One kind of protective order: the one nearest the reference price, and how many rest."""

    nearest: Price | None
    count: int


def summarize_exits(
    orders: Iterable[Order], closing_side: OrderSide, reference: Decimal | None
) -> dict[str, ExitSummary]:
    """
    Classify `orders` into stop-loss and take-profit and pick the one of each kind nearest
    `reference` (ties broken by client order id, so the pick is deterministic). An order whose
    price is not known yet is counted but never picked; with no reference nothing is picked.
    """
    priced: dict[str, list[tuple[Price, str]]] = {STOP_LOSS: [], TAKE_PROFIT: []}
    counts = {STOP_LOSS: 0, TAKE_PROFIT: 0}
    for order in orders:
        exit_ = protective_exit(order, closing_side)
        if exit_ is None:
            continue
        kind, price = exit_
        counts[kind] += 1
        if price is not None:
            priced[kind].append((price, str(order.client_order_id)))
    return {kind: ExitSummary(_nearest(priced[kind], reference), counts[kind]) for kind in counts}


def _nearest(candidates: list[tuple[Price, str]], reference: Decimal | None) -> Price | None:
    if not candidates or reference is None:
        return None
    price, _ = min(candidates, key=lambda item: (abs(item[0].as_decimal() - reference), item[1]))
    return price


def own_open_orders(strategy: Strategy) -> list[Order]:
    """
    Return the strategy's open and emulated orders on its instrument, each once: an order the
    `OrderEmulator` holds is not open at the venue, so it is only in `orders_emulated`.
    """
    cache = strategy.cache
    instrument_id = strategy.config.instrument_id
    orders: dict[str, Order] = {}
    for order in (
        *cache.orders_open(instrument_id=instrument_id, strategy_id=strategy.id),
        *cache.orders_emulated(instrument_id=instrument_id, strategy_id=strategy.id),
    ):
        orders.setdefault(str(order.client_order_id), order)
    return list(orders.values())


class FeedStatus(Protocol):
    """
    A data feed's connection state outside the strategy (Story 33.14: the liquidation bridge's
    `LiquidationFeedStatus`): None while connected, else when it went down.
    """

    @property
    def disconnected_since_ns(self) -> int | None: ...


class StrategyCacheReader:
    """
    `BotRuntime` over one strategy. Invariant: reads `cache.positions_open/closed` filtered by
    this strategy's id only, and hands `on_fill` handlers only this strategy's `OrderFilled`s; with
    a `feed_status`, `last_data_ns` never passes the moment that feed went down while it is down.
    """

    def __init__(self, strategy: Strategy, feed_status: FeedStatus | None = None) -> None:
        self._strategy = strategy
        self._feed_status = feed_status

    @property
    def strategy_name(self) -> str:
        return type(self._strategy).__name__

    @property
    def symbol(self) -> str:
        return str(self._strategy.config.instrument_id)

    @property
    def is_running(self) -> bool:
        return self._strategy.is_running

    @property
    def last_data_ns(self) -> int:
        # The ts_event of the strategy's last market data, set from a market-data callback that
        # keeps firing whether or not the strategy runs (see `DummyStrategy`; every hosted
        # strategy keeps one, e.g. research's `CandlePatternStrategy`). A strategy fed by a second
        # feed (the liquidation bridge) reads stale while that feed is down, even though its quotes
        # keep arriving: a quiet market never reads as a dead feed, only the connection does.
        last: int = self._strategy.last_data_ns
        down_since = None if self._feed_status is None else self._feed_status.disconnected_since_ns
        return last if down_since is None else min(last, down_since)

    def start(self) -> None:
        self._strategy.start()

    def stop(self) -> None:
        self._strategy.stop()

    def positions(self) -> PositionSnapshot:
        strategy = self._strategy
        instrument_id = strategy.config.instrument_id
        positions_open = strategy.cache.positions_open(
            instrument_id=instrument_id, strategy_id=strategy.id
        )
        positions_closed = strategy.cache.positions_closed(
            instrument_id=instrument_id, strategy_id=strategy.id
        )
        realized_pnl = sum(
            position.realized_pnl.as_double()
            for position in (*positions_open, *positions_closed)
            if position.realized_pnl is not None
        )
        open_orders = own_open_orders(strategy)
        if not positions_open:
            return PositionSnapshot("flat", 0.0, realized_pnl, 0.0, open_orders=len(open_orders))
        # NETTING (the only OMS type here): at most one open position per strategy+instrument.
        return self._open_position(positions_open[0], realized_pnl, open_orders)

    def _open_position(
        self, position: Position, realized_pnl: float, open_orders: list[Order]
    ) -> PositionSnapshot:
        strategy = self._strategy
        instrument_id = strategy.config.instrument_id
        position_side = "long" if position.is_long else "short" if position.is_short else "flat"
        mid = strategy.cache.price(instrument_id, PriceType.MID)
        net_exposure = 0.0
        unrealized_pnl = 0.0
        if mid is not None:
            # notional_value() (not signed_qty * price) matches the Portfolio's own
            # net_exposure (crates/portfolio/src/portfolio.rs), which scales by the
            # instrument's multiplier.
            sign = 1.0 if position.is_long else -1.0
            net_exposure = sign * position.notional_value(mid).as_double()
            unrealized_pnl = position.unrealized_pnl(mid).as_double()
        entry = self._entry_price(position)
        reference = mid if mid is not None else entry
        closing_side = OrderSide.SELL if position.is_long else OrderSide.BUY
        exits = summarize_exits(
            open_orders, closing_side, reference.as_decimal() if reference is not None else None
        )
        return PositionSnapshot(
            position_side,
            net_exposure,
            realized_pnl,
            unrealized_pnl,
            entry_price=_text(entry),
            mark_price=_text(mid),
            position_qty=str(position.quantity),
            stop_loss=_text(exits[STOP_LOSS].nearest),
            take_profit=_text(exits[TAKE_PROFIT].nearest),
            stop_loss_orders=exits[STOP_LOSS].count,
            take_profit_orders=exits[TAKE_PROFIT].count,
            open_orders=len(open_orders),
        )

    def _entry_price(self, position: Position) -> Price | None:
        """
        Return the position's average open price at the instrument's precision.

        Known limit: `Position.avg_px_open` is an f64 in the Nautilus core, so this is that
        float stamped at the instrument's price precision: exact for a single-fill entry, but a
        position opened by fills at different prices shows its average rounded to the price
        precision. Upgrade path: recompute the average from the position's own `OrderFilled`
        events' `last_px`/`last_qty` in `Decimal` and publish it at a wider precision.
        """
        instrument = self._strategy.cache.instrument(position.instrument_id)
        if instrument is None:
            return None
        return instrument.make_price(position.avg_px_open)

    def on_fill(self, handler: Callable[[OrderFilled, Position | None], None]) -> None:
        """
        Subscribe to this strategy's order-event topic (`events.order.{strategy_id}`, published by
        the ExecutionEngine for every order event) and hand over each fill with its position.
        """
        strategy = self._strategy

        def _on_order_event(event: object) -> None:
            if not isinstance(event, OrderFilled):
                return
            position = (
                strategy.cache.position(event.position_id)
                if event.position_id is not None
                else None
            )
            handler(event, position)

        strategy.msgbus.subscribe(topic=f"events.order.{strategy.id}", handler=_on_order_event)

    def on_order_event(self, handler: Callable[[], None]) -> None:
        """Call `handler` after every event on this strategy's order-event topic."""
        strategy = self._strategy
        strategy.msgbus.subscribe(
            topic=f"events.order.{strategy.id}", handler=lambda _event: handler()
        )


def _text(value: Price | None) -> str | None:
    return str(value) if value is not None else None
