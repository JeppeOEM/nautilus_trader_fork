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
The bots' Redis adapter: one `BusConnection` per `connect()` -- publish/GET/SET plus, for the
supervisor's connection only, the `bots:control` subscription -- over `redis.asyncio`.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from bots.application.ports import CONTROL_CHANNEL
from bots.application.ports import BusConnection


class RedisBus:
    """`BusConnection` over one open client. Invariant: payloads cross verbatim."""

    def __init__(self, client: aioredis.Redis, pubsub: aioredis.client.PubSub | None) -> None:
        self._client = client
        self._pubsub = pubsub

    async def publish(self, channel: str, message: str) -> None:
        await self._client.publish(channel, message)

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        # decode_responses=True (see `connect`): a present value is always `str`.
        return None if value is None else str(value)

    async def set(self, key: str, value: str) -> None:
        await self._client.set(key, value)

    async def control_messages(self) -> AsyncIterator[str]:
        if self._pubsub is None:
            raise RuntimeError("this connection was opened without the bots:control subscription")
        async for message in self._pubsub.listen():
            if message["type"] == "message":
                yield message["data"]


def connect(
    redis_url: str, *, subscribe_control: bool = False
) -> AbstractAsyncContextManager[BusConnection]:
    """
    Open one connection, closed on exit. With `subscribe_control` it is already subscribed to
    `bots:control` when yielded, so no command published after the connection is up can be missed.
    Only the supervisor's connection subscribes: a subscription nobody reads would buffer every
    control message for the connection's whole life.
    """

    @asynccontextmanager
    async def _open() -> AsyncIterator[BusConnection]:
        async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
            if not subscribe_control:
                yield RedisBus(client, None)
                return
            pubsub = client.pubsub()
            try:
                # Inside the `try`: a subscribe that fails mid-handshake still closes the pubsub.
                await pubsub.subscribe(CONTROL_CHANNEL)
                yield RedisBus(client, pubsub)
            finally:
                await pubsub.aclose()

    return _open()
