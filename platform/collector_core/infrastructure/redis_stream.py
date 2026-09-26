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
`RedisLiveStream`: capture's `LiveStream` over Redis pub/sub `snapshots:raw` (parent spine AD-1).
Moved out of `collector_core.collector` in Story 26.1; the payload is frozen (AD-D12).
"""

import json
import logging
import os

import redis.asyncio as aioredis
from kernel.second_snapshot import DydxSecondSnapshot


logger = logging.getLogger(__name__)

CHANNEL = "snapshots:raw"


def redis_url_from_env() -> str:
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")


async def publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """
    Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:raw.

    Empty batches are silently dropped. Publish failures are logged and swallowed --
    missing one tick is acceptable per the architecture (the Parquet write is durable).
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    try:
        await redis_client.publish(CHANNEL, payload)
    except Exception as e:
        logger.warning("Redis publish failed: %s", e)


class RedisLiveStream:
    """
    The live fan-out (see `collector_core.ports.LiveStream`). Bounded socket timeouts: a
    black-holed Redis must not stall the sampler's publish. The client is created lazily on the
    first publish, inside the running loop, so a collector built per `run_forever` attempt owns
    exactly one.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: aioredis.Redis | None = None

    async def publish(self, snapshots: list[DydxSecondSnapshot]) -> None:
        if not snapshots:
            return
        if self._client is None:
            self._client = aioredis.Redis.from_url(
                self._url, socket_connect_timeout=1.0, socket_timeout=1.0
            )
        await publish_snapshot_batch(self._client, snapshots)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
