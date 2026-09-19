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
Story 15.5: a second Redis subscriber (own connection, mirrors `redis_bus.py`'s
`RankingsBus` shape) that fans out the currently-forming candle bar for whichever
`(instrument_id, bar_seconds)` pairs at least one `/ws/live` connection has actively
subscribed to.

Unlike `RankingsBus` (which relays an already-fully-formed upstream message verbatim),
`LiveCandleBus` recomputes each pair's forming bar itself -- but only ever via
`ml_signals.candles.candle_dicts_from_snapshots` (AD-F7/AD-F2: never a second,
independent aggregation), replayed over a small in-progress-bucket buffer instead of a
full history query. A `(instrument_id, bar_seconds)` buffer/listener-set is created
lazily on the first `subscribe()` for that pair and torn down on the last matching
`unsubscribe()` (MEM-02 spirit: never accumulate state for an unwatched pair).
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis

from data_api.redis_bus import QUEUE_MAX
from data_api.redis_bus import put_drop_oldest
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.candles import candle_dicts_from_snapshots


logger = logging.getLogger(__name__)

SNAPSHOTS_CHANNEL = "snapshots:raw"

_BufferKey = tuple[str, int]  # (instrument_id, bar_seconds)


class LiveCandleBus:
    """Maintains one current-bucket snapshot buffer per actively-subscribed
    `(instrument_id, bar_seconds)` pair, and fans out that pair's recomputed forming bar
    to every listener queue on each matching `snapshots:raw` tick.

    A plain class, not a baked-in singleton -- like `RankingsBus`, the module-level
    `live_candle_bus` instance below is what the running app uses, but tests construct
    their own isolated `LiveCandleBus()` so a buffer-lifecycle/convergence unit test
    never shares state with another test or a live subscriber task.
    """

    def __init__(self) -> None:
        self._buffers: dict[_BufferKey, list[DydxSecondSnapshot]] = {}
        self._listeners: dict[_BufferKey, set["asyncio.Queue[dict]"]] = {}

    def subscribe(self, instrument_id: str, bar_seconds: int) -> "asyncio.Queue[dict]":
        """Register a new per-listener queue for `(instrument_id, bar_seconds)`,
        creating that pair's buffer on first subscribe (one per `/ws/live` connection's
        live-candle subscription)."""
        key = (instrument_id, bar_seconds)
        queue: "asyncio.Queue[dict]" = asyncio.Queue(QUEUE_MAX)
        self._listeners.setdefault(key, set()).add(queue)
        self._buffers.setdefault(key, [])
        return queue

    def unsubscribe(self, instrument_id: str, bar_seconds: int, queue: "asyncio.Queue[dict]") -> None:
        """Deregister one listener queue -- called on `unsubscribe`/disconnect. Tears
        down the pair's buffer and listener set entirely once the last listener leaves,
        so an unwatched pair never keeps accumulating snapshots (MEM-02)."""
        key = (instrument_id, bar_seconds)
        listeners = self._listeners.get(key)
        if listeners is None:
            return
        listeners.discard(queue)
        if not listeners:
            del self._listeners[key]
            self._buffers.pop(key, None)

    def handle_batch(self, payload: object) -> None:
        """Decode and apply one `snapshots:raw` batch (a JSON list of
        `DydxSecondSnapshot.to_dict()` results, per `collector._publish_snapshot_batch`).
        A malformed payload is logged and skipped, never raised."""
        if not isinstance(payload, list):
            logger.warning("Malformed snapshots:raw payload (not a list), skipping: %r", payload)
            return
        for entry in payload:
            self._handle_snapshot_entry(entry)

    def _handle_snapshot_entry(self, entry: object) -> None:
        if not isinstance(entry, dict):
            logger.warning("Malformed snapshots:raw entry (not a dict), skipping: %r", entry)
            return
        try:
            snapshot = DydxSecondSnapshot.from_dict(entry)
        except Exception as exc:
            logger.warning("snapshots:raw entry failed to decode, skipping: %s", exc)
            return
        instrument_id = snapshot.instrument_id.value
        watched_bar_seconds = [bs for (iid, bs) in self._listeners if iid == instrument_id]
        for bar_seconds in watched_bar_seconds:
            self._apply_to_buffer(instrument_id, bar_seconds, snapshot)

    def _apply_to_buffer(self, instrument_id: str, bar_seconds: int, snapshot: DydxSecondSnapshot) -> None:
        """Append to (or reset) `(instrument_id, bar_seconds)`'s current-bucket buffer,
        then recompute and publish its forming bar."""
        key = (instrument_id, bar_seconds)
        buffer = self._buffers.setdefault(key, [])
        if buffer and snapshot.ts_event <= buffer[-1].ts_event:
            return  # out-of-order/duplicate (e.g. around a Redis reconnect) -- never fold in
        bucket_ns = bar_seconds * 1_000_000_000
        if buffer and buffer[-1].ts_event // bucket_ns != snapshot.ts_event // bucket_ns:
            buffer = [snapshot]  # bucket boundary crossed -- previous bar's last publish stands
        else:
            buffer = [*buffer, snapshot]
        self._buffers[key] = buffer
        self._publish(key, instrument_id, bar_seconds, buffer)

    def _publish(
        self, key: _BufferKey, instrument_id: str, bar_seconds: int, buffer: list[DydxSecondSnapshot],
    ) -> None:
        # The one and only aggregation call (AD-F7/AD-F2) -- `buffer` holds only the
        # current bucket's snapshots, so its one (last) resulting candle is the forming
        # bar. Empty result means no trade occurred in this tick (close_price is None
        # for every buffered snapshot) -- a no-op, not an error.
        candles = candle_dicts_from_snapshots(buffer, bar_seconds)
        if not candles:
            return
        message = {"channel": f"candles:{instrument_id}:{bar_seconds}", "bar": candles[-1]}
        for queue in self._listeners.get(key, ()):
            put_drop_oldest(queue, message)

    async def run(self, redis_url: str) -> None:
        """
        Subscribe to `snapshots:raw` forever, reconnecting on any error.

        Mirrors `RankingsBus.run()`'s discipline exactly: reconnect forever, 2s sleep
        between attempts.
        """
        logger.info("LiveCandleBus starting, url=%s", redis_url)
        while True:
            try:
                logger.info("LiveCandleBus connecting...")
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(SNAPSHOTS_CHANNEL)
                    logger.info("LiveCandleBus subscribed to %s", SNAPSHOTS_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            payload = json.loads(message["data"])
                        except Exception as exc:
                            logger.warning("snapshots:raw message parse error: %s", exc)
                            continue
                        self.handle_batch(payload)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly (app shutdown)
            except Exception as exc:
                logger.warning("LiveCandleBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)


# The instance the running app actually uses -- `app.py`'s lifespan starts
# `live_candle_bus.run()`, `ws/live.py` subscribes/unsubscribes against this same object.
live_candle_bus = LiveCandleBus()
