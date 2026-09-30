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
`archive.infrastructure.redis_bus`'s `RedisStatusBus` survives a Redis restart between two
publishes (audit D-136, Story 31.10's `redis_stop`): the first publish on the pooled connection
the server closed raises `ConnectionError` (`Redis.from_url` gives pool connections no retry),
and the bus retries it once on a fresh connection. A Redis still down, or a timeout, fails the
publish as before.
"""

import asyncio

import pytest
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from archive.application.scheduler import STATUS_CHANNEL
from archive.infrastructure.redis_bus import RedisStatusBus


class _Client:
    """Stands in for `redis.asyncio.Redis`: each publish raises the next planned error, if any."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.published: list[tuple[str, str]] = []
        self.attempts = 0

    async def publish(self, channel: str, message: str) -> int:
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        self.published.append((channel, message))
        return 1


def _bus(monkeypatch: pytest.MonkeyPatch, client: _Client) -> RedisStatusBus:
    monkeypatch.setattr(aioredis.Redis, "from_url", staticmethod(lambda *a, **k: client))
    return RedisStatusBus("redis://127.0.0.1:6379")


def test_a_connection_the_server_closed_is_retried_once_and_the_message_crosses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client([RedisConnectionError("Error UNKNOWN while writing to socket.")])
    asyncio.run(_bus(monkeypatch, client).publish("row"))
    assert client.published == [(STATUS_CHANNEL, "row")]


def test_a_redis_still_down_fails_the_publish_after_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client([RedisConnectionError("refused"), RedisConnectionError("refused")])
    with pytest.raises(RedisConnectionError):
        asyncio.run(_bus(monkeypatch, client).publish("row"))
    assert (client.attempts, client.published) == (2, [])


def test_a_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client([RedisTimeoutError("Timeout writing to socket")])
    with pytest.raises(RedisTimeoutError):
        asyncio.run(_bus(monkeypatch, client).publish("row"))
    assert client.attempts == 1
