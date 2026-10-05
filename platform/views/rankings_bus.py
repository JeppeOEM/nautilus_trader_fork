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

Mirrors the retired dashboard's `_handle_rankings_message`/`_redis_listener`
validation and reconnect discipline exactly (platform/CLAUDE.md's SSOT rules: one
computer/publisher of rankings, every reader mirrors the same guard rather than
inventing its own).

`RankingsBus` is a plain class, not a baked-in singleton -- the one instance the running app
uses is constructed by `data_api.buses` (views holds no module state, Story 24.2 moved this module
here from `data_api/redis_bus.py`), and tests construct their own isolated `RankingsBus()` so a
malformed-payload/503-before-cache unit test never shares state with another test or with a live
subscriber task. The Redis URL is the caller's (`data_api.settings.REDIS_URL`).
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

RANKINGS_CHANNEL = "rankings:live"

# Every relayed message is a complete snapshot/bar, so a stalled consumer only needs the
# newest ones -- bounding the queues keeps it from growing memory without limit.
QUEUE_MAX = 1000

# A publisher emitting malformed messages continuously must not spam the log at message rate: one
# warning per window carries the count suppressed since the last. `RankingsBus.malformed_count`
# keeps the exact running total.
MALFORMED_LOG_INTERVAL_SECONDS = 60.0

# `ranking_engine` heartbeats every `RANKING_HEARTBEAT_SECONDS` (default 5 s), so this much silence
# means the publisher is down or the subscription is dead (a half-open TCP connection never errors
# on its own, `listen()` blocks forever). The bus then resubscribes.
# Known limit: the window is a constant, not derived from `RANKING_HEARTBEAT_SECONDS` (a
# `ranking_engine` env var this process never sees). A heartbeat set to 60 s or more would make the
# bus resubscribe, with a warning, every window; the upgrade path is to carry the cadence in the
# heartbeat payload and size the window from it.
SILENCE_RESUBSCRIBE_SECONDS = 60.0
_POLL_SECONDS = 5.0


def put_drop_oldest(queue: "asyncio.Queue[dict]", item: dict) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


class RankingsBus:
    """
    Caches the latest `rankings:live` message and fans it out to WS listeners.

    `.latest` is `None` until the first valid message arrives -- `GET /api/rankings`
    reads this directly to decide whether to 503 (I/O matrix: "cache empty is an
    honest transient state, not a fabricated empty snapshot").
    """

    def __init__(self) -> None:
        self.latest: dict | None = None
        self._listeners: set[asyncio.Queue] = set()
        self.malformed_count = 0  # every rejected payload since start, logged or not
        self._malformed_logged_at: float | None = None
        self._malformed_suppressed = 0

    def _note_malformed(self, what: str, detail: object) -> None:
        """Count a rejected message; log at most one warning per `MALFORMED_LOG_INTERVAL_SECONDS`."""
        self.malformed_count += 1
        if self._malformed_window_open():
            self._malformed_suppressed += 1
            return
        logger.warning(
            "%s (%d more malformed suppressed since the last warning, %d total): %r",
            what,
            self._malformed_suppressed,
            self.malformed_count,
            detail,
        )
        self._malformed_logged_at = time.monotonic()
        self._malformed_suppressed = 0

    def _flush_suppressed_malformed(self) -> None:
        """
        Log the suppressed tail once its window closes, so a burst that ends inside a window is
        still reported even if no further malformed message ever arrives to carry the count.
        """
        if not self._malformed_suppressed or self._malformed_window_open():
            return
        logger.warning(
            "%d more malformed %s messages suppressed since the last warning (%d total)",
            self._malformed_suppressed,
            RANKINGS_CHANNEL,
            self.malformed_count,
        )
        self._malformed_logged_at = time.monotonic()
        self._malformed_suppressed = 0

    def _malformed_window_open(self) -> bool:
        last = self._malformed_logged_at
        return last is not None and time.monotonic() - last < MALFORMED_LOG_INTERVAL_SECONDS

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
            self._note_malformed(
                "Malformed rankings:live message, skipping (cache unchanged)", message
            )
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
                    await self._receive(pubsub)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly (app shutdown)
            except Exception as exc:
                logger.warning("RankingsBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)

    async def _receive(self, pubsub: aioredis.client.PubSub) -> None:
        """
        Ingest messages until `SILENCE_RESUBSCRIBE_SECONDS` pass without one, then raise so `run`
        resubscribes: a heartbeat-driven liveness check, since `listen()` blocks forever on a
        half-open connection and the cache would freeze until the process restarts.
        """
        heard = time.monotonic()
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_SECONDS
            )
            if message is not None and message["type"] == "message":
                heard = time.monotonic()
                self._ingest(message["data"])
            self._flush_suppressed_malformed()
            if time.monotonic() - heard > SILENCE_RESUBSCRIBE_SECONDS:
                raise ConnectionError(
                    f"no {RANKINGS_CHANNEL} message for {SILENCE_RESUBSCRIBE_SECONDS:.0f}s"
                )

    def _ingest(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except Exception as exc:
            self._note_malformed(f"rankings:live message parse error: {exc}", data)
            return
        self.handle_message(payload)
