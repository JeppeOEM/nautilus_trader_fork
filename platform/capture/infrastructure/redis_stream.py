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
Moved out of `capture.application.capture_service` in Story 26.1. The payload is the kernel's wire
dict (`DydxSecondSnapshot.to_dict`): since Story 30.2 exact integer units, both precisions and the
gap-encoded book, the same layout as the Parquet row -- never floats (`docs/DATA_DICTIONARY.md`
§1.7).
"""

import json
import os

import redis.asyncio as aioredis
from kernel.second_snapshot import DydxSecondSnapshot


CHANNEL = "snapshots:raw"


def redis_url_from_env() -> str:
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")


async def publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """
    Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:raw.

    An empty batch publishes nothing. A failed publish raises (Story 31.2): the `CaptureService`
    ledgers it at `collector.snapshot_publish` and carries on -- the tick's live view is lost, the
    Parquet write is durable -- so a Redis outage is counted, never a WARNING nobody reads.
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    await redis_client.publish(CHANNEL, payload)


class RedisLiveStream:
    """
    The live fan-out (see `capture.application.ports.LiveStream`). Bounded socket timeouts: a
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
