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
import itertools
import threading
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager

from bots.application.history import HistoryPublisher
from bots.application.ports import BusConnection
from bots.application.ports import Connect
from bots.application.supervise import build_status
from bots.application.supervise import read_fill_stats
from bots.domain.bot import Bot
from bots.domain.fill_ledger import FillRecord
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.fills_store import SqliteFillsStore
from nautilus_trader.trading.strategy import Strategy


# A fresh trade id per seeded fill unless a test names one: the store keys on (bot_id, trade_id).
_trade_ids = itertools.count()


def write_fill(
    store: SqliteFillsStore,
    bot_id: str,
    ts: int,
    side: str,
    price: float,
    qty: float,
    realized_pnl: float | None,
    position_realized_pnl: float | None = None,
    trade_id: str | None = None,
) -> bool:
    if trade_id is None:
        trade_id = f"T-{next(_trade_ids)}"
    record = FillRecord(bot_id, ts, side, price, qty, realized_pnl, position_realized_pnl, trade_id)
    return store.write_fill(record)


class ThreadRecordingStore:
    """
    A `FillsStore` over a real one that records which thread ran each read, for the tests that
    prove no sqlite read runs on the node's event-loop thread (DW-222).
    """

    def __init__(self, store: SqliteFillsStore) -> None:
        self._store = store
        self.read_threads: set[int] = set()

    def _reading(self) -> None:
        self.read_threads.add(threading.get_ident())

    def write_fill(self, record: FillRecord) -> bool:
        return self._store.write_fill(record)

    def recent_trades(self, bot_id: str, cutoff_ns: int | None, limit: int) -> list[dict]:
        self._reading()
        return self._store.recent_trades(bot_id, cutoff_ns, limit)

    def realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]:
        self._reading()
        return self._store.realized_pnls(bot_id, cutoff_ns)

    def position_realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]:
        self._reading()
        return self._store.position_realized_pnls(bot_id, cutoff_ns)

    def win_rate_stats(self, bot_id: str) -> tuple[int, int]:
        self._reading()
        return self._store.win_rate_stats(bot_id)

    def last_fill_ns(self, bot_id: str) -> int | None:
        self._reading()
        return self._store.last_fill_ns(bot_id)

    def pnl_by_day(self, bot_id: str, cutoff_ns: int | None) -> list[dict]:
        self._reading()
        return self._store.pnl_by_day(bot_id, cutoff_ns)


class FakeBus:
    """An in-memory `BusConnection`: published messages, a key space, queued control bodies."""

    def __init__(self, control: list[str] | None = None) -> None:
        self.published: list[tuple[str, str]] = []
        self.keys: dict[str, str] = {}
        self.control = list(control or [])
        self.fail_set = False
        # How many more `get` calls raise, as an unreachable Redis does.
        self.fail_get = 0

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def get(self, key: str) -> str | None:
        if self.fail_get > 0:
            self.fail_get -= 1
            raise ConnectionError("get failed")
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
    stats = read_fill_stats(store, bot_id)
    return build_status(Bot(bot_id, mode, started_at), StrategyCacheReader(strategy), stats, now)
