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
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.position import Position


STATUS_CHANNEL = "bots:status"
CONTROL_CHANNEL = "bots:control"


def history_key(bot_id: str, range_name: str) -> str:
    return f"bots:history:{bot_id}:{range_name}"


def incidents_key(bot_id: str) -> str:
    return f"bots:incidents:{bot_id}"


@dataclass(frozen=True)
class PositionSnapshot:
    """One bot's own position figures, read scoped to its strategy (never instrument-wide)."""

    position_side: str  # "flat" | "long" | "short"
    net_exposure: float
    realized_pnl: float
    unrealized_pnl: float


class BotRuntime(Protocol):
    """
    The one running Nautilus strategy a bot drives -- the application's whole view of Nautilus.

    Invariant: every figure is scoped to this bot's own `strategy_id`, never the portfolio's
    account+instrument aggregates, which blend every bot trading the same instrument on the node
    (AD-11); `on_fill` hands over this strategy's fills only, with the position as the Cache holds
    it right after the fill.
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

    def on_fill(self, handler: Callable[[OrderFilled, Position | None], None]) -> None: ...


class FillsStore(Protocol):
    """
    `fills.db`, the append-only fill log (one row per fill, every bot, a `bot_id` column).

    Invariant: a written row is never rewritten or dropped; every query is scoped to one bot and
    returns rows at/after `cutoff_ns` (all time when None) in ascending `ts` order.
    """

    def write_fill(self, record: FillRecord) -> None: ...

    def recent_trades(self, bot_id: str, cutoff_ns: int | None, limit: int) -> list[dict]: ...

    def realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]: ...

    def position_realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]: ...

    def win_rate_stats(self, bot_id: str) -> tuple[int, int]: ...

    def pnl_by_day(self, bot_id: str, cutoff_ns: int | None) -> list[dict]: ...


class BusConnection(Protocol):
    """
    One open connection to the bots' Redis bus. Invariant: payloads cross verbatim -- the
    application builds the frozen wire strings, the adapter only transports them.
    """

    async def publish(self, channel: str, message: str) -> None: ...

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...

    def control_messages(self) -> AsyncIterator[str]:
        """Every `bots:control` message body, from subscription on, until the connection drops."""
        ...


# Opens a fresh connection; the caller owns the reconnect policy.
type Connect = Callable[[], AbstractAsyncContextManager[BusConnection]]
