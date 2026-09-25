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
`candles.application.forming.forming_bar` (AD-F7/AD-F2: never a second, independent
aggregation -- it is the same `fold_arrays` the store writes), replayed over a small
in-progress-bucket buffer instead of a full history query. A `(instrument_id, bar_seconds)` buffer/listener-set is created
lazily on the first `subscribe()` for that pair and torn down on the last matching
`unsubscribe()` (MEM-02 spirit: never accumulate state for an unwatched pair).
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections import deque
from typing import Callable

import redis.asyncio as aioredis
from candles.application.forming import forming_bar
from kernel.catalog_files import query_second_ohlc
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from observability import error_ledger

from data_api import settings
from data_api.redis_bus import QUEUE_MAX
from data_api.redis_bus import put_drop_oldest


logger = logging.getLogger(__name__)

SNAPSHOTS_CHANNEL = "snapshots:raw"

_BufferKey = tuple[str, int]  # (instrument_id, bar_seconds)

# The collector flushes to the catalog every flush_interval_seconds (60s), so a history read
# right after a refresh misses the newest unflushed seconds. Keeping the last few minutes of
# traded seconds here (a handful of tiny rows per coin, time-bounded: MEM-02) lets the candles
# route serve them until the catalog has them.
RECENT_SECONDS = 600


def _catalog_rows_for_seed(
    instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
) -> list[SecondOHLC]:
    """
    Read the current bucket's traded rows straight from the raw 1s archive.

    A wide bucket (4H, 1D) is up to ~1,440 small files, read once per subscription off the event loop.
    """
    return query_second_ohlc(settings.CATALOG_PATH, instrument_id, start_ns, end_ns)


class LiveCandleBus:
    """
    Maintains one current-bucket snapshot buffer per actively-subscribed
    `(instrument_id, bar_seconds)` pair, and fans out that pair's recomputed forming bar
    to every listener queue on each matching `snapshots:raw` tick.

    A plain class, not a baked-in singleton -- like `RankingsBus`, the module-level
    `live_candle_bus` instance below is what the running app uses, but tests construct
    their own isolated `LiveCandleBus()` so a buffer-lifecycle/convergence unit test
    never shares state with another test or a live subscriber task.
    """

    def __init__(self) -> None:
        self._buffers: dict[_BufferKey, list[DydxSecondSnapshot]] = {}
        self._listeners: dict[_BufferKey, set[asyncio.Queue[dict]]] = {}
        # Called with every decoded snapshot (e.g. the alert engine) so a second consumer
        # never needs its own `snapshots:raw` subscription.
        self.observers: list[Callable[[DydxSecondSnapshot], None]] = []
        self._seeded: set[_BufferKey] = set()
        # One seed per pair, owned here (not by a connection): it serves every listener of the
        # pair, and asyncio holds only a weak reference to a task, so an unheld one can vanish.
        self._seed_tasks: dict[_BufferKey, asyncio.Task[None]] = {}
        self._recent: defaultdict[str, deque[SecondOHLC]] = defaultdict(deque)

    def recent_rows(self, instrument_id: str, start_ns: int, end_ns: int) -> list[SecondOHLC]:
        """Traded seconds seen live in [start_ns, end_ns], oldest first (see RECENT_SECONDS)."""
        return [r for r in self._recent.get(instrument_id, ()) if start_ns <= r.ts_event <= end_ns]

    def _remember(self, snapshot: DydxSecondSnapshot) -> None:
        if snapshot.close_price is None:
            return
        rows = self._recent[snapshot.instrument_id.value]
        if rows and snapshot.ts_event <= rows[-1].ts_event:
            return  # duplicate/out-of-order (Redis reconnect)
        rows.append(
            SecondOHLC(
                snapshot.ts_event,
                snapshot.open_price,
                snapshot.high_price,
                snapshot.low_price,
                snapshot.close_price,
                snapshot.buy_volume,
                snapshot.sell_volume,
            )
        )
        while rows[0].ts_event < snapshot.ts_event - RECENT_SECONDS * 1_000_000_000:
            rows.popleft()

    def subscribe(self, instrument_id: str, bar_seconds: int) -> "asyncio.Queue[dict]":
        """
        Register a new per-listener queue for `(instrument_id, bar_seconds)`,
        creating that pair's buffer on first subscribe (one per `/ws/live` connection's
        live-candle subscription).
        """
        key = (instrument_id, bar_seconds)
        queue: asyncio.Queue[dict] = asyncio.Queue(QUEUE_MAX)
        self._listeners.setdefault(key, set()).add(queue)
        self._buffers.setdefault(key, [])
        return queue

    def unsubscribe(
        self, instrument_id: str, bar_seconds: int, queue: "asyncio.Queue[dict]"
    ) -> None:
        """
        Deregister one listener queue -- called on `unsubscribe`/disconnect. Tears
        down the pair's buffer and listener set entirely once the last listener leaves,
        so an unwatched pair never keeps accumulating snapshots (MEM-02).
        """
        key = (instrument_id, bar_seconds)
        listeners = self._listeners.get(key)
        if listeners is None:
            return
        listeners.discard(queue)
        if not listeners:
            del self._listeners[key]
            self._buffers.pop(key, None)
            self._seeded.discard(key)
            task = self._seed_tasks.pop(key, None)
            if task is not None:
                task.cancel()  # nobody is left to seed for

    def start_seed(self, instrument_id: str, bar_seconds: int) -> None:
        """Run `seed` for the pair as a bus-held task, once per pair while it has listeners."""
        key = (instrument_id, bar_seconds)
        if key in self._seed_tasks or key in self._seeded or key not in self._listeners:
            return
        task = asyncio.create_task(self.seed(instrument_id, bar_seconds))
        self._seed_tasks[key] = task
        task.add_done_callback(lambda done: self._seed_done(key, done))

    def _seed_done(self, key: _BufferKey, task: "asyncio.Task[None]") -> None:
        if self._seed_tasks.get(key) is task:
            del self._seed_tasks[key]
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            error_ledger.record(
                "live_candles.seed",
                f"forming-bar seed for {key[0]}/{key[1]}s failed; the bar misses the bucket start",
                exc,
            )

    async def seed(self, instrument_id: str, bar_seconds: int) -> None:
        """
        Fill the current bucket's buffer with the snapshots that pre-date this subscription.

        Without it the forming bar only covers ticks seen since subscribe, so its open/high/low/
        volume miss the start of the bucket (D-18). Runs once per pair; the catalog read is one
        bucket, off the event loop, and unions the unflushed tail (`recent_rows`) so the seconds
        since the collector's last flush are covered too.
        """
        key = (instrument_id, bar_seconds)
        if key in self._seeded or key not in self._listeners:
            return
        self._seeded.add(key)
        bucket_ns = bar_seconds * 1_000_000_000
        now_ns = time.time_ns()
        start_ns = now_ns // bucket_ns * bucket_ns
        try:
            rows = await asyncio.to_thread(
                _catalog_rows_for_seed, instrument_id, bar_seconds, start_ns, now_ns
            )
        except BaseException:  # a cancel too: never leave the pair marked seeded but unseeded
            self._seeded.discard(key)
            raise
        if key not in self._listeners:
            return  # everyone left while the read ran
        have = {r.ts_event for r in rows}
        rows += [
            r for r in self.recent_rows(instrument_id, start_ns, now_ns) if r.ts_event not in have
        ]
        rows.sort(key=lambda r: r.ts_event)
        buffer = self._buffers.setdefault(key, [])
        # Live ticks own their bucket: if one rolled over while the read ran, only rows of that
        # same bucket may be prepended, never the previous bucket's.
        target = (buffer[0].ts_event if buffer else start_ns) // bucket_ns
        older = [
            r
            for r in rows
            if r.ts_event // bucket_ns == target and (not buffer or r.ts_event < buffer[0].ts_event)
        ]
        self._buffers[key] = older + buffer
        if self._buffers[key]:
            self._publish(key, instrument_id, bar_seconds, self._buffers[key])

    def handle_batch(self, payload: object) -> None:
        """
        Decode and apply one `snapshots:raw` batch (a JSON list of
        `DydxSecondSnapshot.to_dict()` results, per `collector._publish_snapshot_batch`).
        A malformed payload is logged and skipped, never raised.
        """
        if not isinstance(payload, list):
            error_ledger.record(
                "live_candles.payload", f"snapshots:raw payload is not a list, SKIPPED: {payload!r}"
            )
            return
        for entry in payload:
            self._handle_snapshot_entry(entry)

    def _handle_snapshot_entry(self, entry: object) -> None:
        if not isinstance(entry, dict):
            error_ledger.record(
                "live_candles.entry", f"snapshots:raw entry is not a dict, SKIPPED: {entry!r}"
            )
            return
        try:
            snapshot = DydxSecondSnapshot.from_dict(entry)
        except Exception as exc:
            error_ledger.record(
                "live_candles.decode", "snapshots:raw entry failed to decode, SKIPPED", exc
            )
            return
        for observer in self.observers:
            try:
                observer(snapshot)
            except Exception:
                error_ledger.record("live_candles.observer", "snapshot observer failed")
        self._remember(snapshot)
        instrument_id = snapshot.instrument_id.value
        watched_bar_seconds = [bs for (iid, bs) in self._listeners if iid == instrument_id]
        for bar_seconds in watched_bar_seconds:
            self._apply_to_buffer(instrument_id, bar_seconds, snapshot)

    def _apply_to_buffer(
        self, instrument_id: str, bar_seconds: int, snapshot: DydxSecondSnapshot
    ) -> None:
        """
        Append to (or reset) `(instrument_id, bar_seconds)`'s current-bucket buffer,
        then recompute and publish its forming bar.
        """
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
        self,
        key: _BufferKey,
        instrument_id: str,
        bar_seconds: int,
        buffer: list[DydxSecondSnapshot],
    ) -> None:
        # The one and only aggregation call (AD-F7/AD-F2) -- `buffer` holds only the
        # current bucket's snapshots, so the candles context's forming bar over it *is* this
        # pair's forming bar. None means no trade occurred in this tick (close_price is None
        # for every buffered snapshot) -- a no-op, not an error.
        bar = forming_bar(buffer, bar_seconds)
        if bar is None:
            return
        message = {"channel": f"candles:{instrument_id}:{bar_seconds}", "bar": bar}
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
                            error_ledger.record(
                                "live_candles.parse",
                                "snapshots:raw message is not JSON, SKIPPED",
                                exc,
                            )
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
