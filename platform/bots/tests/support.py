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
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager

from bots.application.history import HistoryPublisher
from bots.application.ports import BusConnection
from bots.application.ports import Connect
from bots.application.supervise import build_status
from bots.application.supervise import read_fill_stats
from bots.domain.bot import Bot
from bots.domain.fill_ledger import FillRecord
from bots.domain.fill_ledger import PositionCloseRecord
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.fills_store import SqliteFillsStore
from nautilus_trader.trading.strategy import Strategy


# A fresh trade id per seeded fill unless a test names one: the store keys on (bot_id, trade_id).
_trade_ids = itertools.count()
# A fresh position id per seeded close: an unlinked close is keyed on (bot_id, position_id, ts).
_position_ids = itertools.count()


def write_fill(
    store: SqliteFillsStore,
    bot_id: str,
    ts: int,
    side: str,
    price: float,
    qty: float,
    closes_pnl: float | None = None,
    trade_id: str | None = None,
) -> bool:
    """
    Seed one fill; with `closes_pnl`, also the round trip it closed (at the fill's ts, linked to
    its trade id), as a real fill followed by its `PositionClosed` records them.
    """
    if trade_id is None:
        trade_id = f"T-{next(_trade_ids)}"
    inserted = store.write_fill(FillRecord(bot_id, ts, side, price, qty, trade_id))
    if closes_pnl is not None:
        write_close(store, bot_id, ts, closes_pnl, trade_id)
    return inserted


def write_close(
    store: SqliteFillsStore,
    bot_id: str,
    ts_closed: int,
    realized_pnl: float,
    trade_id: str | None = None,
    position_id: str | None = None,
) -> bool:
    """Seed one round trip's close."""
    if position_id is None:
        position_id = f"P-{next(_position_ids)}"
    record = PositionCloseRecord(bot_id, position_id, ts_closed, realized_pnl, trade_id)
    return store.write_position_close(record)


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

    def write_position_close(self, record: PositionCloseRecord) -> bool:
        return self._store.write_position_close(record)

    def recent_trades(self, bot_id: str, cutoff_ns: int | None, limit: int) -> list[dict]:
        self._reading()
        return self._store.recent_trades(bot_id, cutoff_ns, limit)

    def position_realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]:
        self._reading()
        return self._store.position_realized_pnls(bot_id, cutoff_ns)

    def total_realized_pnl(self, bot_id: str) -> float:
        self._reading()
        return self._store.total_realized_pnl(bot_id)

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
    """
    An in-memory `BusConnection`: published messages, a key space, queued control bodies, and
    leases (`hold`/`release`) that expire against `now`, a clock in seconds a test may replace.
    """

    def __init__(self, control: list[str] | None = None) -> None:
        self.published: list[tuple[str, str]] = []
        self.keys: dict[str, str] = {}
        self.control = list(control or [])
        self.fail_set = False
        # How many more `get` calls raise, as an unreachable Redis does.
        self.fail_get = 0
        # Kept apart from `keys` so a test reading the plain key space never sees a lease.
        self.leases: dict[str, tuple[str, float]] = {}
        self.now: Callable[[], float] = lambda: 0.0
        # How many more `hold` calls raise, as an unreachable Redis does.
        self.fail_hold = 0

    def lease(self, key: str) -> str | None:
        """Return the lease's value while unexpired, else None (as Redis's PX expiry drops it)."""
        held = self.leases.get(key)
        if held is None or held[1] <= self.now():
            return None
        return held[0]

    def lease_ttl(self, key: str) -> float | None:
        held = self.leases.get(key)
        return None if held is None else held[1] - self.now()

    async def hold(self, key: str, value: str, ttl_ms: int) -> bool:
        if self.fail_hold > 0:
            self.fail_hold -= 1
            raise ConnectionError("hold failed")
        if self.lease(key) not in (None, value):
            return False
        self.leases[key] = (value, self.now() + ttl_ms / 1000)
        return True

    async def release(self, key: str, value: str) -> None:
        if self.lease(key) == value:
            del self.leases[key]

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def get(self, key: str) -> str | None:
        if self.fail_get > 0:
            self.fail_get -= 1
            raise ConnectionError("get failed")
        if key in self.leases:
            return self.lease(key)
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
    """
    Record `strategy`'s fills and closes into `store` from now on, exactly as `python3 -m bots`
    wires it.
    """
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
