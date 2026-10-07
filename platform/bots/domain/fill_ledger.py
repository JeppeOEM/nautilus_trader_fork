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
`FillLedger`: a bot's fills and round-trip closes as they happened, and the `bots:history:*`
windows (AD-10).

Event-sourced from the strategy's own `OrderFilled` and `PositionClosed` events, never
reconstructed from Nautilus's Cache: under `OmsType.NETTING` a position's id is fixed as
`{instrument_id}-{strategy_id}` for the strategy's whole life, and the ExecutionEngine replaces
the Cache's position under that id the moment it reopens -- or, in a flip, inside the very fill
that closed it (`_flip_position` closes the original, then `_open_position` adds the new one,
both before either event is published; `nautilus_trader/execution/engine.pyx`). So
`cache.positions_closed()` only ever holds a bot's latest round trip (reproduced in
`bots/tests/test_trade_history.py`), and a closed position read back from the Cache after a flip
is already the new one. A round trip's realized PnL is therefore taken from its `PositionClosed`
event alone, exactly as delivered (DW-223); a fill carries no PnL of its own. A row, once stored,
is never overwritten.

The history windows are rolling from `now` (not calendar-aligned): `day`/`week`/`month` = the last
24 h / 7 d / 30 d, `all` = no cutoff. `trades` is capped at the most recent `MAX_TRADES`;
`pnl_series` is bucketed per UTC day of each round trip's close; every timestamp is ns.
"""

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType

from kernel import performance_metrics

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import PositionClosed


MAX_TRADES = 500
# How many of a bot's latest fills a close may link to (`FillLedger.record_close`). Known limit:
# a strategy that, inside the closing fill's own dispatch, submits orders that fill synchronously
# (Sandbox/backtest) publishes those nested fills before the outer `PositionClosed`; more than
# `RECENT_FILLS - 1` of them push the closing fill out and the close is stored unlinked (its PnL
# still counted, `bots.close_unlinked` ledgered). Upgrade path: key the window by client order
# id, holding each order's last fill until its position events are published.
RECENT_FILLS = 16
_NS_PER_SECOND = 1_000_000_000
# Window length per `bots:history:{bot_id}:{range}` key; None = all time. Order is publish order.
RANGE_WINDOW_NS: MappingProxyType[str, int | None] = MappingProxyType(
    {
        "day": 24 * 3600 * _NS_PER_SECOND,
        "week": 7 * 24 * 3600 * _NS_PER_SECOND,
        "month": 30 * 24 * 3600 * _NS_PER_SECOND,
        "all": None,
    }
)


def cutoff_ns(range_name: str, now_ns: int) -> int | None:
    window_ns = RANGE_WINDOW_NS[range_name]
    return now_ns - window_ns if window_ns is not None else None


@dataclass(frozen=True)
class FillRecord:
    """
    One `fills.db` fill row: what traded, never what it earned (a round trip's PnL is a
    `PositionCloseRecord`). `trade_id` is the venue's trade id, with `bot_id` the store's
    idempotency key: a fill re-delivered in a later process life is never stored twice.
    """

    bot_id: str
    ts: int
    side: str
    price: float
    qty: float
    trade_id: str


@dataclass(frozen=True)
class PositionCloseRecord:
    """
    One `position_closes` row: one completed round trip, its `realized_pnl` exactly the
    `PositionClosed` event's (Nautilus's own total for the trip, commissions included).
    `trade_id` names the fill that closed it (the trades blotter shows the PnL on that row) and,
    with `bot_id`, is the store's idempotency key; None when no fill could be linked -- the PnL
    still counts, keyed by `(bot_id, position_id, ts_closed)` instead.
    """

    bot_id: str
    position_id: str
    ts_closed: int
    realized_pnl: float
    trade_id: str | None


class FillLedger:
    """
    Turns one bot's `OrderFilled`/`PositionClosed` events into store rows.

    Invariants: a close's PnL is its event's `realized_pnl`, never a Cache read-back or an
    estimate accumulated across fills, so a process restart mid-position cannot double count
    (nothing is carried across events but the last `RECENT_FILLS` fills, for linking only); a
    close links only to the fill that caused it: the newest recent fill with its position id,
    its closing order (`client_order_id == closing_order_id`), its close time and its last price
    (the engine publishes the `OrderFilled`, then the position events it caused -- but a fill
    nested inside that dispatch, from an order a strategy handler submitted, is published in
    between, so "the last fill" alone would mislink). Quantity is deliberately not compared: a
    flip's close carries the closed position's quantity, not the fill's. Commands:
    `record_fill`, `record_close`.
    """

    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self._recent_fills: deque[OrderFilled] = deque(maxlen=RECENT_FILLS)

    def record_fill(self, fill: OrderFilled) -> FillRecord:
        """Return the row for one fill and remember it as a candidate closing fill."""
        record = FillRecord(
            bot_id=self.bot_id,
            ts=fill.ts_event,
            side="BUY" if fill.order_side == OrderSide.BUY else "SELL",
            price=fill.last_px.as_double(),
            qty=fill.last_qty.as_double(),
            trade_id=fill.trade_id.value,
        )
        self._recent_fills.append(fill)
        return record

    def record_close(self, event: PositionClosed) -> PositionCloseRecord:
        """
        Return the row for one closed round trip. In a flip the event covers the closed leg only
        (the engine closes the original position with the flip fill's share), so the fill that
        flipped is this close's fill and the new leg's PnL arrives with its own later close.
        """
        if event.realized_pnl is None:
            raise ValueError(f"PositionClosed carries no realized_pnl: {event}")
        closing_fill = self._closing_fill(event)
        return PositionCloseRecord(
            bot_id=self.bot_id,
            position_id=event.position_id.value,
            ts_closed=event.ts_closed,
            realized_pnl=event.realized_pnl.as_double(),
            trade_id=closing_fill.trade_id.value if closing_fill is not None else None,
        )

    def _closing_fill(self, event: PositionClosed) -> OrderFilled | None:
        for fill in reversed(self._recent_fills):
            if (
                fill.position_id == event.position_id
                and fill.client_order_id == event.closing_order_id
                and fill.ts_event == event.ts_closed
                and fill.last_px == event.last_px
            ):
                return fill
        return None


def history_payload(
    bot_id: str,
    range_name: str,
    now_ns: int,
    trades: list[dict],
    pnl_series: list[dict],
    position_realized_pnls: list[float],
    all_time_pnl_by_day: list[dict],
    starting_balance: float | None,
) -> dict:
    """
    One `bots:history:{bot_id}:{range}` blob, field order frozen (AD-10). `metrics` is
    `kernel.performance_metrics.all_metrics` -- the one shared implementation (SSOT-02) -- over one
    value per completed round trip, with the equity curve always anchored on the all-time daily
    PnL (a window's returns are measured against true account history, never a fabricated
    in-window balance); a None `starting_balance` (a real account) skips the return-based stats.
    """
    return {
        "bot_id": bot_id,
        "range": range_name,
        "updated_at": now_ns,
        "trades": trades,
        "pnl_series": pnl_series,
        "metrics": performance_metrics.all_metrics(
            realized_pnls=position_realized_pnls,
            pnl_by_day=all_time_pnl_by_day,
            starting_balance=starting_balance,
            cutoff_ns=cutoff_ns(range_name, now_ns),
        ),
    }
