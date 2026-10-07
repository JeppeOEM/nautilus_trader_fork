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
The bots context's ports (DDD spine AD-D2/AD-D15): every input and output of the supervisor and
the history publisher crosses one of these, implemented in `bots.infrastructure` and wired by
`bots/__main__.py`. The channel and key names are the published language (AD-10, frozen by
AD-D12).
"""

from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from bots.domain.fill_ledger import FillRecord
from bots.domain.fill_ledger import PositionCloseRecord
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import PositionClosed


STATUS_CHANNEL = "bots:status"
CONTROL_CHANNEL = "bots:control"


def history_key(bot_id: str, range_name: str) -> str:
    return f"bots:history:{bot_id}:{range_name}"


def incidents_key(bot_id: str) -> str:
    return f"bots:incidents:{bot_id}"


def owner_key(bot_id: str) -> str:
    """Name the ownership lease (DW-78) of `bot_id`: which live process hosts it."""
    return f"bots:owner:{bot_id}"


@dataclass(frozen=True)
class PositionSnapshot:
    """
    One bot's own position figures, read scoped to its strategy (never instrument-wide), in one
    Cache read per heartbeat so the exits, the entry and the mark always describe the same moment.

    The five price/quantity fields are `str(Price)`/`str(Quantity)` (the instrument's own
    precision, never through `float`) or None when they do not apply: flat, no protective order of
    that kind, no mid yet. `stop_loss`/`take_profit` are the protective order nearest the mid (the
    entry price when there is no mid); the counts are every protective order of that kind;
    `open_orders` is every open or emulated order of the bot, protective or not (Story 29.6).
    No realized PnL: that is the append-only `fills.db`'s `position_closes` (DW-225), never the
    Cache, whose closed positions under NETTING keep only the latest round trip.
    """

    position_side: str  # "flat" | "long" | "short"
    net_exposure: float
    unrealized_pnl: float
    entry_price: str | None = None
    mark_price: str | None = None
    position_qty: str | None = None
    stop_loss: str | None = None
    take_profit: str | None = None
    stop_loss_orders: int = 0
    take_profit_orders: int = 0
    open_orders: int = 0


class BotRuntime(Protocol):
    """
    The one running Nautilus strategy a bot drives -- the application's whole view of Nautilus.

    Invariant: every figure is scoped to this bot's own `strategy_id`, never the portfolio's
    account+instrument aggregates, which blend every bot trading the same instrument on the node
    (AD-11); `on_fill`/`on_position_closed` hand over this strategy's own events only, as the
    ExecutionEngine published them (a fill, then the position events it caused, synchronously),
    never re-read from the Cache.
    """

    @property
    def strategy_name(self) -> str: ...

    @property
    def symbol(self) -> str: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def last_data_ns(self) -> int: ...

    def positions(self) -> PositionSnapshot: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def on_fill(self, handler: Callable[[OrderFilled], None]) -> None: ...

    def on_position_closed(self, handler: Callable[[PositionClosed], None]) -> None: ...

    def on_order_event(self, handler: Callable[[], None]) -> None:
        """Call `handler` after each of this strategy's order events (its position may change)."""
        ...


class FillsStore(Protocol):
    """
    `fills.db`, the append-only fill and round-trip log (one row per fill and one per
    `PositionClosed`, every bot, a `bot_id` column).

    Invariant: a written row is never rewritten or dropped; one bot stores a venue trade id at
    most once and a close at most once (`(bot_id, trade_id)` for a fill and a linked close,
    `(bot_id, position_id, ts_closed)` for an unlinked close, so a re-delivered event is a
    no-op); every realized-PnL figure comes from the closes alone; every query is scoped to one
    bot and returns rows at/after `cutoff_ns` (all time when None) in ascending time order.
    """

    def write_fill(self, record: FillRecord) -> bool:
        """Append one fill: True when inserted, False when this bot already stored its trade id."""
        ...

    def write_position_close(self, record: PositionCloseRecord) -> bool:
        """Append one close: True when inserted, False when this bot already stored it."""
        ...

    def recent_trades(self, bot_id: str, cutoff_ns: int | None, limit: int) -> list[dict]: ...

    def position_realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]: ...

    def total_realized_pnl(self, bot_id: str) -> float:
        """Return the sum of `position_realized_pnls(bot_id, None)`, summed in that order."""
        ...

    def win_rate_stats(self, bot_id: str) -> tuple[int, int]: ...

    def last_fill_ns(self, bot_id: str) -> int | None:
        """UNIX nanoseconds of the bot's latest fill, None before its first."""
        ...

    def pnl_by_day(self, bot_id: str, cutoff_ns: int | None) -> list[dict]: ...


class BusConnection(Protocol):
    """
    One open connection to the bots' Redis bus. Invariant: payloads cross verbatim -- the
    application builds the frozen wire strings, the adapter only transports them.
    """

    async def publish(self, channel: str, message: str) -> None: ...

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...

    async def hold(self, key: str, value: str, ttl_ms: int) -> bool:
        """
        Claim or renew a lease, atomically: when `key` is absent, set it to `value` expiring in
        `ttl_ms` and return True; when it already holds exactly `value`, renew its expiry to
        `ttl_ms` and return True; when it holds anything else, change nothing and return False.
        Never a GET-then-SET: two processes racing for one absent key cannot both win.
        """
        ...

    async def release(self, key: str, value: str) -> None:
        """Delete `key` only while it still holds exactly `value` (compare-and-delete, atomic)."""
        ...

    def control_messages(self) -> AsyncIterator[str]:
        """Every `bots:control` message body, from subscription on, until the connection drops."""
        ...


# Opens a fresh connection; the caller owns the reconnect policy.
type Connect = Callable[[], AbstractAsyncContextManager[BusConnection]]
