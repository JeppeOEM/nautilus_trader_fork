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
"""Shared helpers for the bots tests: store seeding, the application's wiring, a fake bus."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager

from bots.application.history import HistoryPublisher
from bots.application.ports import BusConnection
from bots.application.ports import Connect
from bots.application.supervise import build_status
from bots.domain.bot import Bot
from bots.domain.fill_ledger import FillRecord
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.fills_store import SqliteFillsStore
from nautilus_trader.trading.strategy import Strategy


def write_fill(
    store: SqliteFillsStore,
    bot_id: str,
    ts: int,
    side: str,
    price: float,
    qty: float,
    realized_pnl: float | None,
    position_realized_pnl: float | None = None,
) -> None:
    store.write_fill(FillRecord(bot_id, ts, side, price, qty, realized_pnl, position_realized_pnl))


class FakeBus:
    """An in-memory `BusConnection`: published messages, a key space, queued control bodies."""

    def __init__(self, control: list[str] | None = None) -> None:
        self.published: list[tuple[str, str]] = []
        self.keys: dict[str, str] = {}
        self.control = list(control or [])
        self.fail_set = False

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def get(self, key: str) -> str | None:
        return self.keys.get(key)

    async def set(self, key: str, value: str) -> None:
        if self.fail_set:
            raise ConnectionError("set failed")
        self.keys[key] = value

    async def control_messages(self) -> AsyncIterator[str]:
        for data in self.control:
            yield data
        await asyncio.Event().wait()  # a live subscription never ends by itself


def connect_to(bus: FakeBus) -> Connect:
    """Return a `Connect` handing out the one fake bus."""

    @asynccontextmanager
    async def _open() -> AsyncIterator[BusConnection]:
        yield bus

    def connect() -> AbstractAsyncContextManager[BusConnection]:
        return _open()

    return connect


def unused_connect() -> AbstractAsyncContextManager[BusConnection]:
    raise AssertionError("this test opens no bus connection")


def record_fills(
    strategy: Strategy, store: SqliteFillsStore, bot_id: str = "bot-01"
) -> HistoryPublisher:
    """Record `strategy`'s fills into `store` from now on, exactly as `python3 -m bots` wires it."""
    history = HistoryPublisher(
        bot_id, StrategyCacheReader(strategy), store, unused_connect, starting_balance=None
    )
    history.attach()
    return history


def status_of(
    strategy: Strategy,
    store: SqliteFillsStore,
    bot_id: str = "bot-01",
    mode: str = "paper",
    started_at: float = 0.0,
    now: float = 1.0,
) -> dict:
    return build_status(Bot(bot_id, mode, started_at), StrategyCacheReader(strategy), store, now)
