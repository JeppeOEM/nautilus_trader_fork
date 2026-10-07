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
Port contracts (DDD spine AD-D2): the `FillsStore` behaviour the application relies on, run
against its adapter `SqliteFillsStore`, and the bus adapter's shape against `BusConnection` --
plus its ownership-lease scripts against a real Redis, skipped when none answers at `REDIS_URL`.
"""

import asyncio
import inspect
import os
import uuid
from pathlib import Path

import pytest
import redis
import redis.asyncio as aioredis

from bots.application.ports import BusConnection
from bots.application.ports import FillsStore
from bots.application.ports import owner_key
from bots.domain.fill_ledger import FillRecord
from bots.domain.fill_ledger import PositionCloseRecord
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.infrastructure.redis import RedisBus
from bots.infrastructure.redis import connect


_DAY = 24 * 3600 * 1_000_000_000


def _fills_store_contract(store: FillsStore) -> None:
    store.write_fill(FillRecord("b1", 3 * _DAY, "SELL", 105.0, 1.0, "T-3"))
    store.write_fill(FillRecord("b1", 1 * _DAY, "BUY", 100.0, 1.0, "T-1"))
    store.write_fill(FillRecord("b2", 2 * _DAY, "SELL", 1.0, 1.0, "T-2"))
    assert store.write_position_close(PositionCloseRecord("b1", "P-1", 3 * _DAY, 5.0, "T-3"))
    assert store.write_position_close(PositionCloseRecord("b2", "P-1", 2 * _DAY, -1.0, "T-2"))
    # a linked close is keyed on (bot, trade_id): a re-delivery is a no-op
    assert not store.write_position_close(PositionCloseRecord("b1", "P-1", 3 * _DAY, 5.0, "T-3"))

    # scoped to one bot, ascending by ts, every row kept
    assert [t["ts"] for t in store.recent_trades("b1", None, 10)] == [1 * _DAY, 3 * _DAY]
    # cutoff is inclusive
    assert [t["ts"] for t in store.recent_trades("b1", 3 * _DAY, 10)] == [3 * _DAY]
    # a fill shows the PnL of the round trip it closed, nothing otherwise
    assert [t["realized_pnl"] for t in store.recent_trades("b1", None, 10)] == [None, 5.0]
    assert store.position_realized_pnls("b2", None) == [-1.0]
    assert store.total_realized_pnl("b1") == 5.0
    assert store.total_realized_pnl("nobody") == 0.0
    assert store.win_rate_stats("b1") == (1, 1)
    assert store.win_rate_stats("nobody") == (0, 0)
    # the latest fill of that bot only, None before its first
    assert store.last_fill_ns("b1") == 3 * _DAY
    assert store.last_fill_ns("nobody") is None
    assert store.pnl_by_day("b1", None) == [{"period_start": 3 * _DAY, "pnl": 5.0}]


def test_sqlite_fills_store_satisfies_the_fills_store_contract(store: SqliteFillsStore) -> None:
    _fills_store_contract(store)


def test_a_reopened_store_still_holds_every_row(tmp_path: Path) -> None:
    path = str(tmp_path / "fills.db")
    first = SqliteFillsStore(path)
    first.write_fill(FillRecord("b1", 1, "BUY", 1.0, 1.0, "T-1"))
    first.close()
    second = SqliteFillsStore(path)
    try:
        assert len(second.recent_trades("b1", None, 10)) == 1
    finally:
        second.close()


def test_redis_bus_implements_every_bus_connection_method() -> None:
    wanted = {
        name
        for name, member in inspect.getmembers(BusConnection)
        if not name.startswith("_") and callable(member)
    }
    assert wanted == {"publish", "get", "set", "hold", "release", "control_messages"}
    assert all(callable(getattr(RedisBus, name, None)) for name in wanted)


def _redis_url_or_skip() -> str:
    url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
    # Short timeouts: a box without Redis skips at once instead of hanging the suite.
    client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
    try:
        client.ping()
    except (redis.RedisError, OSError) as exc:
        pytest.skip(f"no Redis answers at {url}: {exc!r}")
    finally:
        client.close()
    return url


async def _lease_contract(url: str, key: str) -> list[object]:
    """Every observable step of one lease's life, as the scripts leave it on the server."""
    async with connect(url) as bus, aioredis.Redis.from_url(url, decode_responses=True) as raw:
        try:
            claimed = await bus.hold(key, "ours", 5_000)
            claimed_ttl = await raw.pttl(key)
            foreign = await bus.hold(key, "theirs", 60_000)
            renewed = await bus.hold(key, "ours", 60_000)
            renewed_ttl = await raw.pttl(key)
            await bus.release(key, "theirs")
            kept = await raw.get(key)
            await bus.release(key, "ours")
            gone = await raw.exists(key)
            renewed_longer = renewed_ttl > 5_000
            return [claimed, 0 < claimed_ttl <= 5_000, foreign, renewed, renewed_longer, kept, gone]
        finally:
            await raw.delete(key)


async def _expiry_contract(url: str, key: str) -> list[object]:
    """Let an unrenewed lease lapse on its PX, then try both holders on it."""
    async with connect(url) as bus, aioredis.Redis.from_url(url, decode_responses=True) as raw:
        try:
            await bus.hold(key, "ours", 50)
            await asyncio.sleep(0.2)
            taken = await bus.hold(key, "theirs", 60_000)
            ours_again = await bus.hold(key, "ours", 60_000)
            await bus.release(key, "ours")
            return [taken, ours_again, await raw.get(key)]
        finally:
            await raw.delete(key)


def test_an_expired_redis_lease_is_claimable_by_another_holder() -> None:
    url = _redis_url_or_skip()
    key = owner_key(f"test-dw78-{uuid.uuid4().hex}")

    steps = asyncio.run(_expiry_contract(url, key))

    # the PX is milliseconds: after 200 ms a 50 ms lease is gone; its old holder can neither
    # renew nor delete the new one
    assert steps == [True, False, "theirs"]


def test_the_redis_lease_scripts_claim_renew_refuse_and_compare_and_delete() -> None:
    url = _redis_url_or_skip()
    key = owner_key(f"test-dw78-{uuid.uuid4().hex}")

    steps = asyncio.run(_lease_contract(url, key))

    # claimed with a PX expiry; a foreign value refused; renewed by its holder only; a foreign
    # release keeps it, the holder's deletes it
    assert steps == [True, True, False, True, True, "ours", 0]
