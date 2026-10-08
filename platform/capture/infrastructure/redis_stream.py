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

Since Story 28.1 the same client also carries capture's per-flush hot-path figures: one pipeline
of `PUBLISH capture:hotpath <json>` and `SET capture:hotpath:<venue> <json>` (the latest record,
pull-readable with `redis-cli GET`; `docs/DATA_DICTIONARY.md` §1.25).

Since Story 33.1 it also publishes `liquidations:raw`: one JSON array of `Liquidation.to_dict`
rows per decoded liquidation frame (integer units, both precisions, the side as `"long"`/`"short"`,
`docs/DATA_DICTIONARY.md` §1.26), the rows the archive buffer took, never floats.

Since Story 33.4 it also publishes `derivs:raw`: one JSON array per sample tick of
`kernel.derivs_wire.to_wire` rows (mark, index, funding, open interest; every value exact text).
"""

import json
import os
import time
from typing import Any

import redis.asyncio as aioredis
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot


CHANNEL = "snapshots:raw"
HOTPATH_CHANNEL = "capture:hotpath"
LIQUIDATIONS_CHANNEL = "liquidations:raw"
DERIVS_CHANNEL = "derivs:raw"


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


async def publish_liquidation_batch(redis_client: aioredis.Redis, rows: list[Liquidation]) -> None:
    """
    Publish liquidation rows to `liquidations:raw` as one JSON array of `Liquidation.to_dict`.

    An empty batch publishes nothing. A failure raises: the liquidation feed ledgers it at
    `collector.liquidation_publish` and carries on -- the rows are already in the flush buffer.
    """
    if not rows:
        return
    payload = json.dumps([Liquidation.to_dict(row) for row in rows])
    await redis_client.publish(LIQUIDATIONS_CHANNEL, payload)


async def publish_derivs_batch(redis_client: aioredis.Redis, rows: list[dict[str, Any]]) -> None:
    """
    Publish one tick's `kernel.derivs_wire` rows to `derivs:raw` as one JSON array.

    An empty batch publishes nothing. A failure raises: the `CaptureService` ledgers it at
    `collector.derivs_publish` with the row count and carries on -- the archive buffer has them.
    """
    if not rows:
        return
    await redis_client.publish(DERIVS_CHANNEL, json.dumps(rows))


def hotpath_key(venue: str) -> str:
    """Return the latest-record key of `venue`'s hot-path figures: `capture:hotpath:<venue>`."""
    return f"{HOTPATH_CHANNEL}:{venue.lower()}"


def hotpath_payload(venue: str, report: dict[str, Any], ts_ns: int) -> str:
    """Return the `capture:hotpath` JSON: the venue, the publish time `ts` (ns), the figures."""
    return json.dumps({"venue": venue, "ts": ts_ns, **report})


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

    def _redis(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.Redis.from_url(
                self._url, socket_connect_timeout=1.0, socket_timeout=1.0
            )
        return self._client

    async def publish(self, snapshots: list[DydxSecondSnapshot]) -> None:
        if not snapshots:
            return
        await publish_snapshot_batch(self._redis(), snapshots)

    async def publish_liquidations(self, rows: list[Liquidation]) -> None:
        """Publish one frame's archived liquidation rows on `liquidations:raw`; raises on failure."""
        await publish_liquidation_batch(self._redis(), rows)

    async def publish_derivs(self, rows: list[dict[str, Any]]) -> None:
        """Publish one tick's derivatives rows on `derivs:raw`; raises on failure."""
        await publish_derivs_batch(self._redis(), rows)

    async def publish_hotpath(self, venue: str, report: dict[str, Any]) -> None:
        """
        Publish one flush window's figures and keep them as the venue's latest record, in one
        round trip (no MULTI: the two commands need not be atomic). Raises on failure: the service
        ledgers it at `collector.hotpath_publish`.
        """
        payload = hotpath_payload(venue, report, time.time_ns())
        pipe = self._redis().pipeline(transaction=False)
        pipe.publish(HOTPATH_CHANNEL, payload)
        pipe.set(hotpath_key(venue), payload)
        await pipe.execute()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
