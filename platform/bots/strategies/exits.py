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
Bracket exits for `DummyStrategy` (Story 29.6): where a take-profit and a stop-loss rest, and the
entry `OrderList` that carries them.

Exit prices are computed in `Decimal` from the entry-time mid and rounded to the instrument's
price increment *away from the entry*, so the distance is never smaller than configured, then
built with `Price.from_raw` at the instrument's precision -- never `Price(decimal, precision)`,
which silently mis-stamps some values (platform/CLAUDE.md NAUT-01), and never through `float`.

Both exit legs are emulated (`EXIT_EMULATION_TRIGGER`): the node's `OrderEmulator` holds them
until the entry fills, then until the market reaches them, and only then releases them to the
venue. Resting them at the venue as plain OTO children does not work live: the Sandbox matching
engine processes an OTO child synchronously when it fills the parent (or at once, while the
parent's `OrderAccepted` is still queued), but the live `ExecEngine` applies that fill to the
Cache asynchronously, so the child's reduce-only check finds no position yet and rejects it
("REDUCE_ONLY ... would have increased position", seen on the first `make bots-churn-check`
run). The emulator releases a leg only after the `ExecEngine` has opened the position, so its
reduce-only check passes.

The emulator triggers them on the last trade price, not bid/ask: a `BID_ASK` (or `DEFAULT`)
emulation trigger also reads the best bid/ask of the node's Cache order book, and on the dYdX
feed that book's best bid sat about 2% under the real market while quotes and Sandbox fills
agreed with each other (second churn-check run: a stop at 83,792 released at a "bid" of 82,133
and filled at 83,832, milliseconds after the entry), so every stop fired at once. Trades are
real prints.

Known limit: an emulated exit lives in the node's process, not at the venue -- it protects
nothing while the node is down (a restart does not re-arm it either, see `DummyStrategy`).
The emulator releases a triggered exit as a MARKET order -- a stop as designed, but a matched
take-profit LIMIT too (`OrderEmulator._fill_limit_order`), so either fills at the book, not
at its price, and on a thin book (dYdX BTC-USD rests 0.0001 dust levels $20-30 apart) only
partly, the rest waiting for liquidity. It triggers only on a trade, so on an instrument that
rarely trades an exit can lag a book that has already moved through it. The exits are
anchored to the entry-time mid, not the fill: a market entry that slips further than the
configured distance (a few bps on that dYdX book) leaves the take-profit on the wrong side of
the entry, and it exits on the next trade. The legs are built for the entry's full quantity;
how a partly filled entry resizes them is left to Nautilus's own contingency handling and is not
checked here, which is why the churn fixture trades the book's minimum size step (its entries
fill whole). Upgrade path: venue-resting exits (no emulation)
re-anchored on the entry's fill, once the exec path applies a fill to the Cache before the
venue sees its OTO children, verified by `make bots-churn-check`; bid/ask triggering once the
Cache book's integrity on dYdX is traced.
"""

from decimal import ROUND_CEILING
from decimal import ROUND_FLOOR
from decimal import Decimal

from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import ContingencyType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.model.orders import Order
from nautilus_trader.model.orders import OrderList
from nautilus_trader.model.orders import StopMarketOrder


_BPS = Decimal(10_000)

# The tags `OrderFactory.bracket` gives its three orders, so a one-legged entry reads the same.
_ENTRY_TAG = "ENTRY"
_STOP_LOSS_TAG = "STOP_LOSS"
_TAKE_PROFIT_TAG = "TAKE_PROFIT"

# What the emulated exits trigger on (see the module docstring for why emulated, and why trades).
EXIT_EMULATION_TRIGGER = TriggerType.LAST_PRICE


def _round_to(value: Decimal, increment: Decimal, rounding: str) -> Decimal:
    return (value / increment).to_integral_value(rounding=rounding) * increment


def exit_prices(
    side: OrderSide,
    mid: Decimal,
    increment: Decimal,
    take_profit_bps: int | None,
    stop_loss_bps: int | None,
) -> tuple[Decimal | None, Decimal | None]:
    """
    Return `(take_profit, stop_loss)` for an entry on `side` at `mid`, None for an unset leg.

    Long: the take-profit is `mid * (1 + bps)` rounded up, the stop-loss `mid * (1 - bps)`
    rounded down; short is the mirror image. Rounding away from the entry keeps each exit at
    least its configured distance away.
    """
    up = ROUND_CEILING
    down = ROUND_FLOOR
    long = side == OrderSide.BUY
    take_profit = None
    stop_loss = None
    if take_profit_bps is not None:
        factor = Decimal(take_profit_bps) / _BPS
        target = mid * (1 + factor) if long else mid * (1 - factor)
        take_profit = _round_to(target, increment, up if long else down)
    if stop_loss_bps is not None:
        factor = Decimal(stop_loss_bps) / _BPS
        target = mid * (1 - factor) if long else mid * (1 + factor)
        stop_loss = _round_to(target, increment, down if long else up)
    return take_profit, stop_loss


def make_price(value: Decimal, precision: int) -> Price:
    """`value` (already on the instrument's tick grid) as a `Price` at `precision`, exactly."""
    return Price.from_raw(int(value.scaleb(FIXED_PRECISION)), precision)


def entry_with_exits(
    factory: OrderFactory,
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: Quantity,
    take_profit: Price | None,
    stop_loss: Price | None,
    ts_init: int,
) -> OrderList:
    """
    Build a MARKET entry carrying its reduce-only exits: `OrderFactory.bracket` when both legs
    are set (STOP_MARKET stop-loss, LIMIT take-profit, one-updates-the-other), otherwise the
    entry plus the one leg, built exactly as `bracket` builds them
    (nautilus_trader/common/factories.pyx): the entry `OTO`-linked to the leg, the leg a
    reduce-only child of the entry. Every leg is emulated (`EXIT_EMULATION_TRIGGER`, see the
    module docstring); `bracket` never emulates the MARKET entry itself.

    The take-profit is never post-only (bracket's default is): when the market has already
    crossed it by the time the entry fills, the exit must fill, not be rejected and leave the
    position without a take-profit.
    """
    if take_profit is not None and stop_loss is not None:
        return factory.bracket(
            instrument_id,
            side,
            quantity,
            sl_trigger_price=stop_loss,
            tp_price=take_profit,
            tp_post_only=False,
            emulation_trigger=EXIT_EMULATION_TRIGGER,
        )
    if take_profit is None and stop_loss is None:
        raise ValueError("entry_with_exits needs a take-profit, a stop-loss or both")
    order_list_id = factory.generate_order_list_id()
    entry_id = factory.generate_client_order_id()
    leg_id = factory.generate_client_order_id()
    entry = MarketOrder(
        trader_id=factory.trader_id,
        strategy_id=factory.strategy_id,
        instrument_id=instrument_id,
        client_order_id=entry_id,
        order_side=side,
        quantity=quantity,
        init_id=UUID4(),
        ts_init=ts_init,
        contingency_type=ContingencyType.OTO,
        order_list_id=order_list_id,
        linked_order_ids=[leg_id],
        tags=[_ENTRY_TAG],
    )
    leg = _exit_leg(entry, leg_id, take_profit, stop_loss, ts_init)
    return OrderList(order_list_id=order_list_id, orders=[entry, leg])


def _exit_leg(
    entry: MarketOrder,
    leg_id: ClientOrderId,
    take_profit: Price | None,
    stop_loss: Price | None,
    ts_init: int,
) -> Order:
    common = {
        "trader_id": entry.trader_id,
        "strategy_id": entry.strategy_id,
        "instrument_id": entry.instrument_id,
        "client_order_id": leg_id,
        "order_side": Order.opposite_side(entry.side),
        "quantity": entry.quantity,
        "init_id": UUID4(),
        "ts_init": ts_init,
        "reduce_only": True,
        "order_list_id": entry.order_list_id,
        "parent_order_id": entry.client_order_id,
        "emulation_trigger": EXIT_EMULATION_TRIGGER,
    }
    if stop_loss is not None:
        return StopMarketOrder(
            trigger_price=stop_loss,
            trigger_type=TriggerType.DEFAULT,
            tags=[_STOP_LOSS_TAG],
            **common,
        )
    return LimitOrder(price=take_profit, post_only=False, tags=[_TAKE_PROFIT_TAG], **common)
