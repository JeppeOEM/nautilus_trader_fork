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
Story 15.2: one shared Redis subscriber feeding both `GET /api/rankings`'s cache and
every `/ws/live` connection's per-listener queue (AD-F2: relay only, never recompute).

Mirrors `ml_signals/dashboard.py`'s `_handle_rankings_message`/`_redis_listener`
validation and reconnect discipline exactly (platform/CLAUDE.md's SSOT rules: one
computer/publisher of rankings, every reader mirrors the same guard rather than
inventing its own).

`RankingsBus` is a plain class, not a baked-in singleton -- the module-level `bus`
instance below is what the running app uses, but tests construct their own isolated
`RankingsBus()` so a malformed-payload/503-before-cache unit test never shares state
with another test or with a live subscriber task.
"""

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
RANKINGS_CHANNEL = "rankings:live"

# Every relayed message is a complete snapshot/bar, so a stalled consumer only needs the
# newest ones -- bounding the queues keeps it from growing memory without limit.
QUEUE_MAX = 1000


def put_drop_oldest(queue: "asyncio.Queue[dict]", item: dict) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


class RankingsBus:
    """Caches the latest `rankings:live` message and fans it out to WS listeners.

    `.latest` is `None` until the first valid message arrives -- `GET /api/rankings`
    reads this directly to decide whether to 503 (I/O matrix: "cache empty is an
    honest transient state, not a fabricated empty snapshot").
    """

    def __init__(self) -> None:
        self.latest: dict | None = None
        self._listeners: set[asyncio.Queue] = set()

    def subscribe(self) -> "asyncio.Queue[dict]":
        """Register a new per-listener queue (one per `/ws/live` connection)."""
        queue: asyncio.Queue = asyncio.Queue(QUEUE_MAX)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict]") -> None:
        """Deregister a listener queue -- called on `WebSocketDisconnect`."""
        self._listeners.discard(queue)

    def handle_message(self, message: object) -> None:
        """
        Validate and cache one `rankings:live` payload, fanning it out verbatim.

        A non-dict payload, or a dict missing a well-typed "mode"/"updated_at" or a
        list-of-dicts "ranks", is logged and skipped -- the previous good cache is kept
        unmodified (mirrors `dashboard.py:_handle_rankings_message` exactly). Checked in
        full here so `GET /api/rankings`'s plain-index reads (`latest["mode"]`, etc.)
        never see a partially-shaped cached message and raise a KeyError -> 500 instead
        of the honest 503/skip this story's design calls for.
        """
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("mode"), str)
            or not isinstance(message.get("updated_at"), int)
            or not isinstance(message.get("ranks"), list)
            or not all(isinstance(row, dict) for row in message["ranks"])
        ):
            logger.warning("Malformed rankings:live message, skipping (cache unchanged): %r", message)
            return
        self.latest = message
        for queue in self._listeners:
            put_drop_oldest(queue, message)

    async def run(self, redis_url: str) -> None:
        """
        Subscribe to `rankings:live` forever, reconnecting on any error.

        Mirrors `dashboard.py:_redis_listener`'s discipline (2280-2312): reconnect
        forever, 2s sleep between attempts, last-cached snapshot keeps serving
        `GET /api/rankings` through an outage.
        """
        logger.info("RankingsBus starting, url=%s", redis_url)
        while True:
            try:
                logger.info("RankingsBus connecting...")
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(RANKINGS_CHANNEL)
                    logger.info("RankingsBus subscribed to %s", RANKINGS_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            payload = json.loads(message["data"])
                        except Exception as exc:
                            logger.warning("rankings:live message parse error: %s", exc)
                            continue
                        self.handle_message(payload)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly (app shutdown)
            except Exception as exc:
                logger.warning("RankingsBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)


# The instance the running app actually uses -- `app.py`'s lifespan starts `bus.run()`,
# `routes/rankings.py` and `ws/live.py` both read/subscribe against this same object.
bus = RankingsBus()
