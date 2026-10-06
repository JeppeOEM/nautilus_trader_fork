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
Ranking's Redis adapters: the `rankings:live` publisher (also `markets:live`'s, Story 29.5, on the
same client) and the `snapshots:raw`/`ranking:control` listener (one connection, reconnecting on
any non-cancellation error), which since Story 33.4 also takes `derivs:raw` and `liquidations:raw`.
"""

import asyncio
import logging
from collections.abc import Awaitable
from collections.abc import Callable

import redis.asyncio as aioredis
from observability import error_ledger

from ranking.application.ports import CONTROL_CHANNEL
from ranking.application.ports import DERIVS_CHANNEL
from ranking.application.ports import LIQUIDATIONS_CHANNEL
from ranking.application.ports import RANKINGS_CHANNEL
from ranking.application.ports import SNAPSHOTS_CHANNEL


logger = logging.getLogger(__name__)

# Pause before reconnecting a dropped subscriber connection.
RECONNECT_SECONDS = 2

# Every channel the engine handles, on the one connection.
SUBSCRIBED_CHANNELS = (SNAPSHOTS_CHANNEL, CONTROL_CHANNEL, DERIVS_CHANNEL, LIQUIDATIONS_CHANNEL)


class RedisLivePublisher:
    """`LivePublisher` over one Redis client. Invariant: the message goes out verbatim."""

    def __init__(self, client: aioredis.Redis, channel: str = RANKINGS_CHANNEL) -> None:
        self._client = client
        self._channel = channel

    async def publish(self, message: str) -> None:
        await self._client.publish(self._channel, message)


async def listen(redis_url: str, on_message: Callable[[str, str], Awaitable[None]]) -> None:
    """Subscribe to `SUBSCRIBED_CHANNELS` and hand every message to `on_message`."""
    logger.info("Ranking engine Redis listener starting, url=%s", redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe(*SUBSCRIBED_CHANNELS)
                logger.info("Subscribed to %s", ", ".join(SUBSCRIBED_CHANNELS))
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        await on_message(message["channel"], message["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Every message published while disconnected is lost to the ranking: counted, not
            # only logged (DATA-07).
            error_ledger.record(
                "ranking_engine.redis",
                f"subscriber error, reconnecting in {RECONNECT_SECONDS}s",
                exc,
            )
            await asyncio.sleep(RECONNECT_SECONDS)
