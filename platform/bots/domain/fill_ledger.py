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
`FillLedger`: a bot's fill history as it happened, and the `bots:history:*` windows (AD-10).

Event-sourced from the strategy's own `OrderFilled` events, never reconstructed from Nautilus's
Cache: under `OmsType.NETTING` a position's id is fixed as `{instrument_id}-{strategy_id}` for the
strategy's whole life, and `Cache._reopen_position()` overwrites that id with a new `Position`,
discarding the closed one -- so `cache.positions_closed()` only ever holds a bot's latest round
trip (confirmed against `nautilus_trader/execution/engine.pyx` + `cache/cache.pyx`, reproduced in
`bots/tests/test_trade_history.py`). A fill, once attributed and stored, is never overwritten.

The history windows are rolling from `now` (not calendar-aligned): `day`/`week`/`month` = the last
24 h / 7 d / 30 d, `all` = no cutoff. `trades` is capped at the most recent `MAX_TRADES`;
`pnl_series` is bucketed per UTC day; every timestamp is ns.
"""

from dataclasses import dataclass
from types import MappingProxyType

from kernel import performance_metrics

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.position import Position


MAX_TRADES = 500
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
    One `fills.db` row. `realized_pnl` is this fill's own share of closing its position (None for
    an opening/adding fill); `position_realized_pnl` is the round trip's true total, set only on
    the fill that closes it -- one value per completed trade, for the per-trade statistics.
    `trade_id` is the venue's trade id, with `bot_id` the store's idempotency key: a fill
    re-delivered in a later process life is never stored twice.
    """

    bot_id: str
    ts: int
    side: str
    price: float
    qty: float
    realized_pnl: float | None
    position_realized_pnl: float | None
    trade_id: str


class FillLedger:
    """
    Attributes realized PnL to each fill of one bot.

    Invariants: across one round trip the fills' `realized_pnl` sum to exactly the position's
    `realized_pnl` (every reducing fill before the close carries a pre-commission estimate; the
    closing fill carries `total - sum(estimates)`), and exactly one fill per round trip -- the
    closing one -- carries `position_realized_pnl`. The pending estimates exist only between a
    position's first reducing fill and its close, so they never outgrow the bot's open, partially
    reduced positions. Command: `attribute`.
    """

    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self._pending_realized_pnl: dict[PositionId, float] = {}

    def attribute(self, fill: OrderFilled, position: Position | None) -> FillRecord:
        """
        Return the row for one fill; `position` is the fill's position as the Cache holds it right
        after the fill applied (None when the fill carries no position id or the Cache has none).

        A single closing *order* can fill across several venue-side partial fills (normal on
        dYdX), so each reducing fill gets its own share rather than the closing fill carrying the
        whole trip's PnL next to null rows.
        """
        realized_pnl, position_realized_pnl = self._pnl(fill, position)
        return FillRecord(
            bot_id=self.bot_id,
            ts=fill.ts_event,
            side="BUY" if fill.order_side == OrderSide.BUY else "SELL",
            price=fill.last_px.as_double(),
            qty=fill.last_qty.as_double(),
            realized_pnl=realized_pnl,
            position_realized_pnl=position_realized_pnl,
            trade_id=fill.trade_id.value,
        )

    def _pnl(
        self, fill: OrderFilled, position: Position | None
    ) -> tuple[float | None, float | None]:
        if fill.position_id is None or position is None or fill.order_side == position.entry:
            return None, None
        if not position.is_closed:
            estimate = position.calculate_pnl(
                position.avg_px_open, fill.last_px.as_double(), fill.last_qty
            ).as_double()
            pending = self._pending_realized_pnl.get(position.id, 0.0)
            self._pending_realized_pnl[position.id] = pending + estimate
            return estimate, None
        if position.realized_pnl is None:
            return None, None
        total = position.realized_pnl.as_double()
        pending = self._pending_realized_pnl.pop(position.id, 0.0)
        return total - pending, total


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
