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
subscribed to, and (Story 24.3) hands the same bar to every attached `BarObserver` for the pairs
it watches, whether or not a chart is open on them.

Unlike `RankingsBus` (which relays an already-fully-formed upstream message verbatim),
`LiveCandleBus` recomputes each pair's forming bar itself -- but only ever via
`candles.application.forming.forming_bar` (AD-F7/AD-F2: never a second, independent
aggregation -- it is the same `fold_arrays` the store writes), replayed over a small
in-progress-bucket buffer instead of a full history query. A `(instrument_id, bar_seconds)`
buffer exists only while a `/ws/live` listener or an observer's `watched_bars()` names the pair: it
is created lazily on the first `subscribe()` or observed tick and dropped once neither holds it
(MEM-02: never accumulate state for an unwatched pair).

Story 24.2 moved this module here from `data_api/live_candles.py`: the catalog path is the
constructor's argument and the one running instance is built by `data_api.buses`, because views
reads no interface settings and holds no module state. The module also declares `BarObserver`, the
port through which a consumer (alerting, attached by `data_api`'s composition root) sees the same
forming bar the chart sees.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections import deque
from collections.abc import Callable
from typing import Protocol

import redis.asyncio as aioredis
from candles.application.forming import forming_bar
from candles.domain.fold import bucket_start_ms
from kernel.catalog_files import query_second_ohlc
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from observability import error_ledger

from views.rankings_bus import QUEUE_MAX
from views.rankings_bus import put_drop_oldest


logger = logging.getLogger(__name__)

SNAPSHOTS_CHANNEL = "snapshots:raw"

_BufferKey = tuple[str, int]  # (instrument_id, bar_seconds)

# The collector flushes to the catalog every flush_interval_seconds (60s), so a history read
# right after a refresh misses the newest unflushed seconds. Keeping the last few minutes of
# traded seconds here (a handful of tiny rows per coin, time-bounded: MEM-02) lets the candles
# route serve them until the catalog has them.
RECENT_SECONDS = 600

# The widest bar an observer may have folded: one day, the widest chart width
# (`candles.domain.candle`'s "1d") and `/api/alerts`' own `bar_seconds` cap. Bounds an observed
# pair's buffer to one day of `SecondOHLC` rows (MEM-02).
MAX_OBSERVED_BAR_SECONDS = 86_400


class BarObserver(Protocol):
    """
    A consumer of the live forming bar: called with the same `{t, o, h, l, c, v}` bar, for the same
    `(instrument_id, bar_seconds)` pair and at the same tick, that `LiveCandleBus` publishes to the
    chart's `/ws/live` channel.

    Invariant: an observer (alerting: `data_api`'s composition root attaches `AlertEngine` here)
    evaluates exactly the bar the chart shows -- one fold, `forming_bar` -- without importing views'
    internals or opening a second `snapshots:raw` subscription, so an alert's bar close can never
    disagree with the candle on screen. `ts_ns` is the `ts_event` of the second that produced this
    bar update. `on_bar` runs on the event loop: blocking work (a network send) belongs on a
    thread. Only live ticks reach it -- a subscription's seed republishes the bucket for the chart
    but is not a new tick, so between a seed and the pair's next tick the chart's `o`/`h`/`l`/`v`
    may already include catalog seconds the observer's last bar lacked; `c` is the same in both.

    `watched_bars()` names the `(instrument_id, bar_seconds)` pairs the observer needs folded; the
    bus asks it once per `snapshots:raw` batch and calls `on_bar` only for those pairs, so no pair
    is folded for nobody and an observer needs no chart open to see its bar.

    Known limit: `bar` is the frozen `{t, o, h, l, c, v}` dict `candles.application.forming.
    forming_bar` returns (the `/ws/live` wire shape), not a Nautilus `Bar`. Upgrade path: pass a
    `nautilus_trader.model.data.Bar` once views owns the wire format and serializes it at the
    WebSocket boundary (see `forming_bar`'s own `Known limit:`).
    """

    def watched_bars(self) -> frozenset[tuple[str, int]]:
        """Return the (instrument_id, bar_seconds) pairs to fold for it, asked once per batch."""
        ...

    def on_bar(self, instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None:
        """Receive the forming bar just published for one watched pair."""
        ...


def _bucket_of(ts_ns: int, bar_seconds: int) -> int:
    """Return the bucket start (ms) of a ns stamp (`candles.domain.fold.bucket_start_ms`)."""
    return bucket_start_ms(ts_ns // 1_000_000, bar_seconds)


def _second_row(snapshot: DydxSecondSnapshot) -> SecondOHLC:
    """Project a snapshot onto the fold's 7 per-second fields, not its 20-level book."""
    return SecondOHLC(
        snapshot.ts_event,
        snapshot.open_price,
        snapshot.high_price,
        snapshot.low_price,
        snapshot.close_price,
        snapshot.buy_volume,
        snapshot.sell_volume,
    )


def _ask_watched(observer: BarObserver) -> frozenset[_BufferKey]:
    """
    Return the observer's watched pairs; one that raises is ledgered and watches nothing.

    A pair that is not an `(instrument_id, width)` 2-tuple with a width in
    1..`MAX_OBSERVED_BAR_SECONDS` is ledgered and dropped here. The bus indexes and divides by it on
    every tick, so one bad entry (e.g. a hand-edited `alerts.toml` with `bar_seconds = 0`) would
    otherwise raise out of `handle_batch` and stall every chart's live candle, not just that alert;
    and a huge width never rolls its bucket over, so its buffer would grow for as long as the pair
    is watched (MEM-02).
    """
    try:
        watched = frozenset(observer.watched_bars())
    except Exception as exc:
        error_ledger.record(
            "live_candles.observer",
            f"{type(observer).__name__}.watched_bars failed; it observes nothing this batch",
            exc,
        )
        return frozenset()
    invalid = {pair for pair in watched if not _is_valid_pair(pair)}
    if invalid:
        error_ledger.record(
            "live_candles.observer",
            f"{type(observer).__name__}.watched_bars named invalid pairs, SKIPPED: "
            f"{sorted(invalid, key=repr)!r}",
        )
    return watched - invalid


def _is_valid_pair(pair: object) -> bool:
    if not (isinstance(pair, tuple) and len(pair) == 2 and isinstance(pair[0], str)):
        return False
    width = pair[1]
    return (
        isinstance(width, int)
        and not isinstance(width, bool)
        and 0 < width <= MAX_OBSERVED_BAR_SECONDS
    )


def _catalog_rows_for_seed(
    catalog_path: str, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
) -> list[SecondOHLC]:
    """
    Read the current bucket's traded rows straight from the raw 1s archive.

    A wide bucket (4H, 1D) is up to ~1,440 small files, read once per subscription off the event loop.
    """
    return query_second_ohlc(catalog_path, instrument_id, start_ns, end_ns)


class LiveCandleBus:
    """
    Maintains one current-bucket buffer of second rows per `(instrument_id, bar_seconds)` pair
    that a listener subscribed to or an attached observer watches, and on each matching
    `snapshots:raw` tick fans out that pair's recomputed forming bar to every listener queue and
    then to every observer watching the pair -- the identical `bar` object to both.

    A plain class, not a baked-in singleton -- like `RankingsBus`, the one instance the
    running app uses is `data_api.buses.live_candle_bus`, but tests construct their own
    isolated `LiveCandleBus(catalog_path)` so a buffer-lifecycle/convergence unit test
    never shares state with another test or a live subscriber task. `catalog_path` is the
    archive the seed reads the current bucket's earlier seconds from. `clock` (wall time, ns) is
    the one "now" the bus reads, to place the seed's bucket; a test pins it so a seed never
    straddles a bucket edge.

    Known limit: a pair only an observer watches is never seeded (the seed reads the catalog for a
    listener's first paint), so its first bar after the pair becomes watched -- at attach, on
    alert creation, or after a restart -- has `o`/`h`/`l`/`v` from the first observed second only;
    `c`, the one field alerting reads, is exact from the first traded tick. Upgrade path: run
    `seed` for newly observed pairs too, once an observer needs more than the close.

    Known limit: each tick refolds the pair's whole current bucket through `forming_bar`, O(bucket
    seconds) -- up to 86,400 rows per tick for a 1D pair an alert keeps always-on. Upgrade path: an
    incremental forming-bar fold in `candles.domain` that applies one second to the running bar,
    kept equal to `fold_arrays` by the candles equivalence test.
    """

    def __init__(self, catalog_path: str, *, clock: Callable[[], int] = time.time_ns) -> None:
        self._catalog_path = catalog_path
        self._clock = clock
        # `SecondOHLC` rows, not snapshots: an always-on 1D alert keeps up to 86,400 seconds
        # buffered, ~10-15 MB as the fold's 7-field projection versus hundreds as 20-level books.
        self._buffers: dict[_BufferKey, list[SecondOHLC]] = {}
        self._listeners: dict[_BufferKey, set[asyncio.Queue[dict]]] = {}
        # Keyed by id(): an observer need not be hashable. Each keeps the pairs its last
        # `watched_bars()` returned, so `on_bar` reaches it only for pairs it asked for.
        self._observers: dict[int, tuple[BarObserver, frozenset[_BufferKey]]] = {}
        self._observed: set[_BufferKey] = set()
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
        rows.append(_second_row(snapshot))
        while rows[0].ts_event < snapshot.ts_event - RECENT_SECONDS * 1_000_000_000:
            rows.popleft()

    def attach(self, observer: BarObserver) -> None:
        """
        Start handing `observer` the forming bar of every pair it watches. Its `watched_bars()` is
        first asked at the next batch. Attaching an already-attached observer is a no-op.
        """
        self._observers.setdefault(id(observer), (observer, frozenset()))

    def detach(self, observer: BarObserver) -> None:
        """Stop calling `observer`; buffers only it watched are dropped at the next batch."""
        self._observers.pop(id(observer), None)
        self._observed = set().union(*(watched for _, watched in self._observers.values()))

    def _refresh_observed(self) -> None:
        """Re-ask every observer which pairs it watches, then drop buffers nobody holds."""
        for observer_id, (observer, _previous) in list(self._observers.items()):
            self._observers[observer_id] = (observer, _ask_watched(observer))
        self._observed = set().union(*(watched for _, watched in self._observers.values()))
        unheld = [k for k in self._buffers if k not in self._listeners and k not in self._observed]
        for key in unheld:
            del self._buffers[key]

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
        down the pair's listener set once the last listener leaves, and its buffer too unless an
        observer still watches the pair, so an unwatched pair never keeps accumulating rows
        (MEM-02) while an observed one keeps its bucket.
        """
        key = (instrument_id, bar_seconds)
        listeners = self._listeners.get(key)
        if listeners is None:
            return
        listeners.discard(queue)
        if not listeners:
            del self._listeners[key]
            if key not in self._observed:
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
        Fill the current bucket's buffer with the second rows that pre-date this subscription.

        Without it the forming bar only covers ticks seen since subscribe, so its open/high/low/
        volume miss the start of the bucket (D-18). Runs once per pair; the catalog read is one
        bucket, off the event loop, and unions the unflushed tail (`recent_rows`) so the seconds
        since the collector's last flush are covered too.
        """
        key = (instrument_id, bar_seconds)
        if key in self._seeded or key not in self._listeners:
            return
        self._seeded.add(key)
        now_ns = self._clock()
        start_ns = _bucket_of(now_ns, bar_seconds) * 1_000_000
        try:
            rows = await asyncio.to_thread(
                _catalog_rows_for_seed,
                self._catalog_path,
                instrument_id,
                bar_seconds,
                start_ns,
                now_ns,
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
        target = _bucket_of(buffer[0].ts_event if buffer else start_ns, bar_seconds)
        older = [
            r
            for r in rows
            if _bucket_of(r.ts_event, bar_seconds) == target
            and (not buffer or r.ts_event < buffer[0].ts_event)
        ]
        self._buffers[key] = older + buffer
        if self._buffers[key]:
            self._publish(key, instrument_id, bar_seconds, self._buffers[key])

    def handle_batch(self, payload: object) -> None:
        """
        Decode and apply one `snapshots:raw` batch (a JSON list of
        `DydxSecondSnapshot.to_dict()` results -- the integer layout, Story 30.2 -- per capture's
        `publish_snapshot_batch`); each entry is decoded by the kernel's strict `from_dict`, and
        only its decoded floats reach the fold.
        A malformed payload is logged and skipped, never raised. The observed pairs are refreshed
        once per batch, before any of its seconds is folded.
        """
        self._refresh_observed()
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
        self._remember(snapshot)
        instrument_id = snapshot.instrument_id.value
        widths = {bs for (iid, bs) in self._listeners if iid == instrument_id}
        widths.update(bs for (iid, bs) in self._observed if iid == instrument_id)
        # Every second is buffered, traded or not, so a chart queue's publish cadence is the same
        # whichever of listener/observer created the buffer.
        row = _second_row(snapshot)
        for bar_seconds in sorted(widths):
            self._apply_to_buffer(instrument_id, bar_seconds, row)

    def _apply_to_buffer(self, instrument_id: str, bar_seconds: int, row: SecondOHLC) -> None:
        """
        Append to (or reset) `(instrument_id, bar_seconds)`'s current-bucket buffer,
        then recompute and publish its forming bar, to the listeners and then the observers.
        """
        key = (instrument_id, bar_seconds)
        buffer = self._buffers.setdefault(key, [])
        if buffer and row.ts_event <= buffer[-1].ts_event:
            return  # out-of-order/duplicate (e.g. around a Redis reconnect) -- never fold in
        if buffer and _bucket_of(buffer[-1].ts_event, bar_seconds) != _bucket_of(
            row.ts_event, bar_seconds
        ):
            buffer = [row]  # bucket boundary crossed -- previous bar's last publish stands
            self._buffers[key] = buffer
        else:
            # In place: an always-on 1D pair holds up to 86,400 rows, so copying the list on every
            # tick would add a second O(bucket) pass to the fold's.
            buffer.append(row)
        bar = self._publish(key, instrument_id, bar_seconds, buffer)
        if bar is not None:
            self._notify_observers(key, bar, row.ts_event)

    def _notify_observers(self, key: _BufferKey, bar: dict, ts_ns: int) -> None:
        instrument_id, bar_seconds = key
        for observer, watched in list(self._observers.values()):
            if key not in watched:
                continue
            try:
                observer.on_bar(instrument_id, bar_seconds, bar, ts_ns)
            except Exception as exc:
                # One failing observer must not stop the others or the chart's next tick.
                error_ledger.record(
                    "live_candles.observer",
                    f"{type(observer).__name__}.on_bar failed for {instrument_id}/{bar_seconds}s",
                    exc,
                )

    def _publish(
        self,
        key: _BufferKey,
        instrument_id: str,
        bar_seconds: int,
        buffer: list[SecondOHLC],
    ) -> dict | None:
        # The one and only aggregation call (AD-F7/AD-F2) -- `buffer` holds only the
        # current bucket's seconds, so the candles context's forming bar over it *is* this
        # pair's forming bar. None means no trade occurred in this bucket yet (close_price is
        # None for every buffered second) -- a no-op, not an error.
        bar = forming_bar(buffer, bar_seconds)
        if bar is None:
            return None
        message = {"channel": f"candles:{instrument_id}:{bar_seconds}", "bar": bar}
        for queue in self._listeners.get(key, ()):
            put_drop_oldest(queue, message)
        return bar

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
