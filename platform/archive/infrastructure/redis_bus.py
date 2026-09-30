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
The Redis adapters for `archive:status` (`RedisStatusBus`) and `archive:control`
(`RedisControlChannel`), the `collection_control.infrastructure.redis` pattern on the archive
service's own channels.
"""

import logging
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from archive.application.scheduler import CONTROL_CHANNEL
from archive.application.scheduler import STATUS_CHANNEL


logger = logging.getLogger(__name__)


class RedisStatusBus:
    """
    `StatusBus` over one lazily opened client. Invariant: each message crosses verbatim on
    `archive:status`. Bounded socket timeouts: a black-holed Redis fails the publish (ledgered by
    the caller) instead of stalling the loop that also runs the jobs.

    A `ConnectionError` is retried once, on a fresh connection (audit D-136, found on
    `collection_control`'s bus of the same pattern): a Redis restart between two publishes leaves
    the idle pooled connection closed by the server, and `Redis.from_url` gives pool connections
    no retry, so the next publish failed on the dead socket although Redis was back. redis-py
    disconnects the failed connection, so the retry reconnects; a Redis still down fails it too
    (ledgered by the caller). A timeout is not retried. A message delivered twice is harmless:
    each one is the whole status.

    The mechanism is pinned from the 31.10 collectors' tracebacks (redis-py 8.1.0 in the image):
    the publish failed on the write, in `send_packed_command` -> `StreamWriter.drain`, which
    raised `ConnectionResetError('Connection lost')` because the transport had already seen the
    server go away. The pool's checkout check reads, it does not write, so it passed the dead
    connection, and pool connections carry `Retry(retries=0)`, so nothing retried it.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: aioredis.Redis | None = None

    async def publish(self, message: str) -> None:
        if self._client is None:
            self._client = aioredis.Redis.from_url(
                self._url, socket_connect_timeout=1.0, socket_timeout=1.0, decode_responses=True
            )
        try:
            await self._client.publish(STATUS_CHANNEL, message)
        except RedisConnectionError:
            await self._client.publish(STATUS_CHANNEL, message)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class RedisControlChannel:
    """
    `ControlChannel` over a fresh connection per `listen()`. Invariant: every published payload is
    yielded verbatim, and a lost connection raises -- never a quiet end of commands. A half-open
    subscription is probed: after `health_seconds` without traffic a PING goes out, and a second
    idle window with no PONG raises.
    """

    def __init__(self, url: str, health_seconds: float = 30.0) -> None:
        self._url = url
        self._health_seconds = health_seconds

    async def listen(self) -> AsyncIterator[str]:
        async with aioredis.Redis.from_url(
            self._url, decode_responses=True, socket_connect_timeout=1.0, socket_keepalive=True
        ) as client:
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(CONTROL_CHANNEL)
                logger.info("archive:control listener subscribed")
                async for payload in self._messages(pubsub):
                    yield payload
            finally:
                await pubsub.aclose()

    async def _messages(self, pubsub: aioredis.client.PubSub) -> AsyncIterator[str]:
        awaiting_pong = False
        while True:
            message = await pubsub.get_message(timeout=self._health_seconds)
            if message is None:
                if awaiting_pong:
                    raise ConnectionError(
                        f"archive:control: no PONG within {self._health_seconds:.0f}s, "
                        "connection presumed dead"
                    )
                await pubsub.ping()
                awaiting_pong = True
                continue
            awaiting_pong = False  # any traffic proves the connection alive
            if message["type"] == "message":
                yield message["data"]

    async def aclose(self) -> None:
        """Nothing is held between `listen()` calls: each closes its own connection."""
