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

Story 33.3: the bus also subscribes `liquidations:raw` (one JSON array of `Liquidation.to_dict`
rows per frame). For an instrument with the feed (`kernel.liquidation.has_liquidation_feed`) each
decoded row joins a `RECENT_SECONDS`-bounded tail (`recent_liquidations`, the chart's unflushed
tail) and the current bucket of every buffer of its instrument, deduped by `venue_event_id`, so the
forming bar's `liq_*` columns are the same fold's as the stored bar's; a no-feed instrument's bar
carries them null. The forming bar is bounded by the one feed-start rule
(`candles.domain.fold.LiquidationArrays.since_ns`): its `liq_*` are known only if its bucket starts
at or after the instrument's feed start, the earliest of the archive's first liquidation
(`kernel.catalog_files.liquidation_feed_since_ns`, never read on the event loop: in each seed's
thread, and otherwise refreshed at most every `FEED_SINCE_REFRESH_SECONDS` by a background
`asyncio.to_thread` read) and the earliest liquidation seen live. That is the rule the store and
the rebuild apply, but not necessarily the same bound: the store's is its persisted
`liquidation_feed_since`, the bus's the archive's (possibly not read yet) and its own live rows, so
the forming bar can read null where the stored bar of the same bucket reads a known value, and,
until a rebuild lowers the store's start to the archive's, known where the stored bar is null. The
bound never makes a false 0 (every source is a liquidation the feed actually delivered); a
liquidation the bus never received can -- see `LiveCandleBus`'s Known limits (Story 33.3 review
loop 2 and the follow-up review).
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections import deque
from typing import NamedTuple
from typing import Protocol

import redis.asyncio as aioredis
from candles.application.forming import forming_bar
from candles.domain.fold import bucket_start_ms
from kernel.catalog_files import liquidation_feed_since_ns
from kernel.catalog_files import query_liquidations
from kernel.catalog_files import query_second_ohlc
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from observability import error_ledger

from views.rankings_bus import QUEUE_MAX
from views.rankings_bus import put_drop_oldest


logger = logging.getLogger(__name__)

SNAPSHOTS_CHANNEL = "snapshots:raw"
# Capture's liquidation channel (`capture.infrastructure.redis_stream.LIQUIDATIONS_CHANNEL`, the
# published language: views never imports capture, AD-D2).
LIQUIDATIONS_CHANNEL = "liquidations:raw"

_BufferKey = tuple[str, int]  # (instrument_id, bar_seconds)

# `_try_publish`'s "this publish failed and was ledgered", distinct from None ("no trade yet").
_PUBLISH_FAILED = object()

# The collector flushes to the catalog every flush_interval_seconds (60s), so a history read
# right after a refresh misses the newest unflushed seconds. Keeping the last few minutes of
# traded seconds here (a handful of tiny rows per coin, time-bounded: MEM-02) lets the candles
# route serve them until the catalog has them.
RECENT_SECONDS = 600

# The widest bar an observer may have folded: one day, the widest chart width
# (`candles.domain.candle`'s "1d") and `/api/alerts`' own `bar_seconds` cap. Bounds an observed
# pair's buffer to one day of `SecondOHLC` rows (MEM-02).
MAX_OBSERVED_BAR_SECONDS = 86_400

# How long an archive feed-start read stands before a publish schedules another (in a thread, never
# on the event loop): the archive's first liquidation only ever moves earlier when older history is
# archived (a backfill), so a few minutes' lag only keeps a bucket null a little longer.
FEED_SINCE_REFRESH_SECONDS = 300


class BarObserver(Protocol):
    """
    A consumer of the live forming bar: called with the same `{t, o, h, l, c, v}` bar, for the same
    `(instrument_id, bar_seconds)` pair and at the same tick, that `LiveCandleBus` publishes to the
    chart's `/ws/live` channel.

    Invariant: an observer (alerting: `data_api`'s composition root attaches `AlertEngine` here)
    evaluates exactly the bar the chart shows -- one fold, `forming_bar` -- without importing views'
    internals or opening a second `snapshots:raw` subscription, so an alert's bar close can never
    disagree with the candle on screen. `ts_ns` is the `ts_event` of the second that produced this
    bar update; it reaches the observer once per second tick, `ts_ns` monotonic per pair -- a
    liquidation-driven republish goes to the chart's listeners only, and the observer sees that
    liquidation in its next tick's bar. `on_bar` runs on the event loop: blocking work (a network send) belongs on a
    thread. Only live ticks reach it -- a subscription's seed republishes the bucket for the chart
    but is not a new tick, so between a seed and the pair's next tick the chart's `o`/`h`/`l`/`v`
    may already include catalog seconds the observer's last bar lacked; `c` is the same in both.

    `watched_bars()` names the `(instrument_id, bar_seconds)` pairs the observer needs folded; the
    bus asks it once per `snapshots:raw` batch and calls `on_bar` only for those pairs, so no pair
    is folded for nobody and an observer needs no chart open to see its bar.

    Known limit: `bar` is the frozen `{t, o, h, l, c, v, ...}` dict `candles.application.forming.
    forming_bar` returns (the `/ws/live` wire shape, with Story 33.3's ten order-flow and
    liquidation keys appended), not a Nautilus `Bar`. Upgrade path: pass a
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
    """Project a snapshot onto the fold's per-second fields (floats and units), not its book."""
    return SecondOHLC(
        snapshot.ts_event,
        snapshot.open_price,
        snapshot.high_price,
        snapshot.low_price,
        snapshot.close_price,
        snapshot.buy_volume,
        snapshot.sell_volume,
        snapshot.price_precision,
        snapshot.size_precision,
        snapshot.close_price_units,
        snapshot.buy_volume_units,
        snapshot.sell_volume_units,
        snapshot.buy_count,
        snapshot.sell_count,
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


class _SeedRows(NamedTuple):
    """One seed's archive read: the bucket's seconds, its liquidations, the id's feed start."""

    seconds: list[SecondOHLC]
    liquidations: list[Liquidation]
    feed_since_ns: int | None


def _catalog_rows_for_seed(
    catalog_path: str, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
) -> _SeedRows:
    """
    Read the current bucket's rows straight from the raw 1s archive, and for an instrument with the
    feed its archived liquidations and its first archived one (`liquidation_feed_since_ns`).

    A wide bucket (4H, 1D) is up to ~1,440 small files, read once per subscription off the event loop.
    """
    seconds = query_second_ohlc(catalog_path, instrument_id, start_ns, end_ns)
    if not has_liquidation_feed(instrument_id):
        return _SeedRows(seconds, [], None)
    return _SeedRows(
        seconds,
        query_liquidations(catalog_path, instrument_id, start_ns, end_ns),
        liquidation_feed_since_ns(catalog_path, instrument_id),
    )


def _liquidation_rows(payload: object) -> list[Liquidation]:
    """
    Decode one `liquidations:raw` frame: a JSON array of `Liquidation.to_dict` rows, each decoded
    by the kernel's own `from_dict`. A malformed frame or row is ledgered at
    `live_candles.liquidation` and skipped, never folded and never silently dropped (DATA-07).
    """
    if not isinstance(payload, list):
        error_ledger.record(
            "live_candles.liquidation",
            f"liquidations:raw payload is not a list, SKIPPED: {payload!r}",
        )
        return []
    rows = []
    for entry in payload:
        try:
            if not isinstance(entry, dict):
                raise TypeError(f"entry is not a dict: {entry!r}")
            rows.append(Liquidation.from_dict(entry))
        except Exception as exc:
            error_ledger.record(
                "live_candles.liquidation", "liquidations:raw entry failed to decode, SKIPPED", exc
            )
    return rows


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
    archive the seed reads the current bucket's earlier seconds from.

    Known limit: a pair only an observer watches is never seeded (the seed reads the catalog for a
    listener's first paint), so its first bar after the pair becomes watched -- at attach, on
    alert creation, or after a restart -- has `o`/`h`/`l`/`v` from the first observed second only;
    `c`, the one field alerting reads, is exact from the first traded tick. Upgrade path: run
    `seed` for newly observed pairs too, once an observer needs more than the close.

    Known limit: an observer-only pair's buffer misses the liquidations of its bucket older than the
    `RECENT_SECONDS` tail: its bucket is not seeded (above), and a fresh or rolled buffer's first
    tick restores the bucket's liquidations only from that tail (`_roll_liquidations`), so a wide
    pair first watched late in its bucket starts without the earlier ones. Upgrade path: the same
    seed for observed pairs, which reads the bucket's archived ones.

    Known limit (feed start, bus vs store): the archive's feed start is read in the seed's thread
    or, for an instrument no seed has read (an observer-only pair), by a background
    `asyncio.to_thread` refresh its first publish schedules, then at most every
    `FEED_SINCE_REFRESH_SECONDS` (a failed read is ledgered at `live_candles.feed_since` once per
    refresh and leaves the previous value). Until the first read lands, and whenever the store's
    persisted start (`liquidation_feed_since`, lowered by capture's own flushes) is earlier than
    both the archive's and the earliest row this process saw live, the forming bar's `liq_*` read
    null where the stored bar of the same bucket reads known. The reverse holds until a rebuild
    lowers the store's start to the archive's (the store's start is the earliest liquidation the
    live sink applied, so after a deploy it can be later than the archive's, DEPLOY_CHECKLIST
    33-3): a bucket between the two reads known here and null in the store, the stored null being
    the conservative side. The bound never makes a false 0, since every start the bus uses is a
    liquidation the feed delivered. The chart's next history read shows the stored value. Upgrade
    path: the store's persisted start published with the `liquidations:raw` frames (or read
    through candles' query service), so both bounds are one.

    Known limit (unreceived liquidations): `liquidations:raw` is Redis pub/sub, at most once. A
    frame dropped across a reconnect, or published before this process subscribed and not yet
    flushed to the archive when the seed read it (up to the collector's flush interval after a
    data_api restart), is missing from the forming bar, which then undercounts -- 0 where the
    bucket holds a liquidation -- until the bucket rolls; the stored bar holds it, and the chart's
    next history read shows it. Upgrade path: a re-seed of the bucket's archived liquidations one
    flush interval after the seed, or a sequence number on the frames so a gap is detected.

    Known limit: each tick refolds the pair's whole current bucket through `forming_bar`, O(bucket
    seconds) -- up to 86,400 rows per tick for a 1D pair an alert keeps always-on. Upgrade path: an
    incremental forming-bar fold in `candles.domain` that applies one second to the running bar,
    kept equal to `fold_arrays` by the candles equivalence test.
    """

    def __init__(self, catalog_path: str) -> None:
        self._catalog_path = catalog_path
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
        # Story 33.3: each buffer's liquidations of its current bucket, keyed by `venue_event_id`
        # (one venue event once), reset when the bucket rolls; and the unflushed tail per
        # instrument, `RECENT_SECONDS`-bounded like `_recent` (MEM-02).
        self._buffer_liquidations: dict[_BufferKey, dict[str, Liquidation]] = {}
        # The bucket (ms) each pair's held liquidations belong to: a fresh buffer's first tick into
        # the bucket a seed already filled keeps them; only a changed bucket resets them.
        self._liquidations_bucket: dict[_BufferKey, int] = {}
        self._recent_liquidations: defaultdict[str, deque[Liquidation]] = defaultdict(deque)
        # The tail's ids (its dedup) and newest `ts_event` (its horizon), kept incrementally so a
        # liquidation cascade costs O(1) per row, not an O(tail) scan.
        self._recent_liquidation_ids: defaultdict[str, set[str]] = defaultdict(set)
        self._newest_liquidation_ns: dict[str, int] = {}
        # The feed-start rule's two sources: the archive's first liquidation per instrument (None:
        # nothing archived; absent: not read yet) and the earliest liquidation seen live. Each
        # archive read's `time.monotonic()` and the pending background refresh, one per instrument.
        self._archive_feed_since: dict[str, int | None] = {}
        self._archive_feed_read_at: dict[str, float] = {}
        self._feed_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._live_feed_since: dict[str, int] = {}

    def recent_rows(self, instrument_id: str, start_ns: int, end_ns: int) -> list[SecondOHLC]:
        """Traded seconds seen live in [start_ns, end_ns], oldest first (see RECENT_SECONDS)."""
        return [r for r in self._recent.get(instrument_id, ()) if start_ns <= r.ts_event <= end_ns]

    def recent_liquidations(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> list[Liquidation]:
        """Liquidations seen live with `ts_event` in [start_ns, end_ns] (see RECENT_SECONDS)."""
        return [
            row
            for row in self._recent_liquidations.get(instrument_id, ())
            if start_ns <= row.ts_event <= end_ns
        ]

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
            self._drop_liquidations(key)

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
                self._drop_liquidations(key)
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

        The seed's publish goes through the tick path's isolation (`_try_publish`: ledgered at
        `live_candles.publish`, never raised). Any failure -- the read, the merge, a cancel or that
        publish -- un-marks the pair, so it is never left seeded-but-unseeded and a later
        `start_seed` (the next subscribe) seeds it again.
        """
        key = (instrument_id, bar_seconds)
        if key in self._seeded or key not in self._listeners:
            return
        self._seeded.add(key)
        try:
            published = await self._seed_bucket(key)
        except BaseException:  # a cancel too: never leave the pair marked seeded but unseeded
            self._seeded.discard(key)
            raise
        if not published:
            self._seeded.discard(key)

    async def _seed_bucket(self, key: _BufferKey) -> bool:
        """Read and merge the seed's rows; False only when its publish failed (ledgered)."""
        instrument_id, bar_seconds = key
        now_ns = time.time_ns()
        start_ns = _bucket_of(now_ns, bar_seconds) * 1_000_000
        read = await asyncio.to_thread(
            _catalog_rows_for_seed, self._catalog_path, instrument_id, bar_seconds, start_ns, now_ns
        )
        if key not in self._listeners:
            return True  # everyone left while the read ran (`unsubscribe` already un-marked it)
        if has_liquidation_feed(instrument_id):
            self._set_archive_feed_since(instrument_id, read.feed_since_ns)  # refreshed per seed
        liquidations = read.liquidations + self.recent_liquidations(instrument_id, start_ns, now_ns)
        rows = read.seconds
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
        self._add_buffer_liquidations(key, target, liquidations)
        if not self._buffers[key]:
            return True
        return self._try_publish(key, self._buffers[key]) is not _PUBLISH_FAILED

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
        bucket = _bucket_of(row.ts_event, bar_seconds)
        if not buffer or _bucket_of(buffer[-1].ts_event, bar_seconds) != bucket:
            # A fresh buffer, or a bucket boundary crossed (the previous bar's last publish
            # stands): the bucket's liquidations are those already held for it (a seed's) plus
            # those the recent tail holds.
            buffer = [row]
            self._buffers[key] = buffer
            self._roll_liquidations(key, bucket)
        else:
            # In place: an always-on 1D pair holds up to 86,400 rows, so copying the list on every
            # tick would add a second O(bucket) pass to the fold's.
            buffer.append(row)
        bar = self._safe_publish(key, buffer)
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

    def _safe_publish(self, key: _BufferKey, buffer: list[SecondOHLC]) -> dict | None:
        """`_try_publish`, its failure read as "no bar" (the tick and liquidation paths)."""
        bar = self._try_publish(key, buffer)
        return bar if isinstance(bar, dict) else None

    def _try_publish(self, key: _BufferKey, buffer: list[SecondOHLC]) -> object:
        """
        `_publish`, isolated per pair: an exception folding or fanning out one pair's bar (a
        malformed row) is ledgered at `live_candles.publish` and that pair skips this publish
        (`_PUBLISH_FAILED`), so it never escapes `handle_batch`, `handle_liquidations` or a seed
        into `run()`, whose reconnect would drop every pair's subscription.
        """
        try:
            return self._publish(key, key[0], key[1], buffer)
        except Exception as exc:
            error_ledger.record(
                "live_candles.publish",
                f"forming bar of {key[0]}/{key[1]}s failed; this publish is SKIPPED",
                exc,
            )
            return _PUBLISH_FAILED

    def _feed_since_ns(self, instrument_id: str) -> int | None:
        """
        Return the instrument's liquidation feed start: the earliest of its first archived
        liquidation (as last read, `_refresh_feed_since`) and the earliest one seen live (None:
        neither known, `liq_*` null). Never reads the archive itself: this runs per tick on the
        event loop.
        """
        self._refresh_feed_since(instrument_id)
        starts = [
            since
            for since in (
                self._archive_feed_since.get(instrument_id),
                self._live_feed_since.get(instrument_id),
            )
            if since is not None
        ]
        return min(starts) if starts else None

    def _set_archive_feed_since(self, instrument_id: str, since_ns: int | None) -> None:
        self._archive_feed_since[instrument_id] = since_ns
        self._archive_feed_read_at[instrument_id] = time.monotonic()

    def _refresh_feed_since(self, instrument_id: str) -> None:
        """
        Schedule a background archive read of the feed start when none ran in the last
        `FEED_SINCE_REFRESH_SECONDS` and none is pending. Without a running loop (a synchronous
        caller) nothing is scheduled and the last value stands.
        """
        read_at = self._archive_feed_read_at.get(instrument_id)
        if instrument_id in self._feed_refresh_tasks or (
            read_at is not None and time.monotonic() - read_at < FEED_SINCE_REFRESH_SECONDS
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._read_feed_since(instrument_id))
        self._feed_refresh_tasks[instrument_id] = task

    async def _read_feed_since(self, instrument_id: str) -> None:
        """One refresh, in a thread; a failure is ledgered once and keeps the previous value."""
        try:
            since = await asyncio.to_thread(
                liquidation_feed_since_ns, self._catalog_path, instrument_id
            )
        except Exception as exc:
            error_ledger.record(
                "live_candles.feed_since",
                f"archive feed-start read for {instrument_id} failed; the previous value "
                f"{self._archive_feed_since.get(instrument_id)!r} stands until the next refresh "
                f"in {FEED_SINCE_REFRESH_SECONDS}s",
                exc,
            )
            self._archive_feed_read_at[instrument_id] = time.monotonic()
        else:
            self._set_archive_feed_since(instrument_id, since)
        finally:
            self._feed_refresh_tasks.pop(instrument_id, None)

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
        # None for every buffered second) -- a no-op, not an error. The bucket's liquidations go
        # in with it, bounded by the feed start; None for an instrument without the feed, or one
        # whose start is known nowhere yet (null `liq_*`, never 0).
        liquidations, since_ns = None, None
        if has_liquidation_feed(instrument_id):
            since_ns = self._feed_since_ns(instrument_id)
            if since_ns is not None:
                liquidations = list(self._buffer_liquidations.get(key, {}).values())
        bar = forming_bar(buffer, bar_seconds, liquidations, liquidations_since_ns=since_ns)
        if bar is None:
            return None
        message = {"channel": f"candles:{instrument_id}:{bar_seconds}", "bar": bar}
        for queue in self._listeners.get(key, ()):
            put_drop_oldest(queue, message)
        return bar

    def handle_liquidations(self, payload: object) -> None:
        """
        Apply one decoded `liquidations:raw` frame: each row of an instrument with the feed joins
        the recent tail and the current bucket of every buffer of its instrument whose bucket holds
        its `ts_event` (once per `venue_event_id`); then each changed buffer republishes its bar
        once for the whole frame -- a cascade of N rows is one fold per pair, not N -- to its
        listeners (not its observers: `BarObserver`), each pair isolated (`_safe_publish`). A row
        of an instrument without the feed is not folded (its bar's `liq_*` stay null) -- capture
        publishes none, so one arriving is ledgered, never silently dropped.
        """
        changed: set[_BufferKey] = set()
        for row in _liquidation_rows(payload):
            instrument_id = row.instrument_id.value
            if not has_liquidation_feed(instrument_id):
                error_ledger.record(
                    "live_candles.liquidation",
                    f"liquidations:raw row for {instrument_id}, which has no liquidation feed, "
                    "SKIPPED",
                )
                continue
            self._remember_liquidation(row)
            for key in [k for k in self._buffers if k[0] == instrument_id]:
                if self._apply_liquidation(key, row):
                    changed.add(key)
        for key in sorted(changed):
            buffer = self._buffers.get(key)
            if buffer:
                self._safe_publish(key, buffer)

    def _remember_liquidation(self, row: Liquidation) -> None:
        """
        Add a row to its instrument's recent tail, once per `venue_event_id`, and lower the live
        feed start to it. The tail's newest `ts_event` and ids are kept incrementally: O(1) per
        row. A row already older than the tail's horizon is not kept (the tail serves the
        unflushed recent seconds only), though it still lowers the feed start.
        """
        instrument_id = row.instrument_id.value
        since = self._live_feed_since.get(instrument_id)
        if since is None or row.ts_event < since:
            self._live_feed_since[instrument_id] = row.ts_event
        ids = self._recent_liquidation_ids[instrument_id]
        if row.venue_event_id in ids:
            return  # a replayed frame: the tail holds it once
        newest = max(self._newest_liquidation_ns.get(instrument_id, row.ts_event), row.ts_event)
        self._newest_liquidation_ns[instrument_id] = newest
        horizon = newest - RECENT_SECONDS * 1_000_000_000
        if row.ts_event < horizon:
            return
        tail = self._recent_liquidations[instrument_id]
        tail.append(row)
        ids.add(row.venue_event_id)
        while tail and tail[0].ts_event < horizon:
            ids.discard(tail.popleft().venue_event_id)

    def _apply_liquidation(self, key: _BufferKey, row: Liquidation) -> bool:
        """
        Add one liquidation to the buffer's forming bucket; True when it changed the bucket (the
        caller republishes the bar to the listeners once per frame).

        Known limit: a liquidation arriving after its bucket rolled (published late, or delayed in
        Redis) is in the store (capture applies it at its flush) but not in the last forming bar
        published for that bucket, which no later tick republishes; the chart's next history read
        shows it. Upgrade path: republish the previous bucket's bar on a late liquidation, as a
        correction frame the client merges.
        """
        bar_seconds = key[1]
        buffer = self._buffers.get(key, [])
        if not buffer or _bucket_of(row.ts_event, bar_seconds) != _bucket_of(
            buffer[-1].ts_event, bar_seconds
        ):
            # Not the bucket being formed: an older one is the store's (closed), a newer one's
            # first second has not arrived (its first tick pulls it from the recent tail).
            return False
        held = self._held_liquidations(key, _bucket_of(row.ts_event, bar_seconds))
        if row.venue_event_id in held:
            return False  # the same venue event again (a replayed frame): counted once
        held[row.venue_event_id] = row
        # Listeners only: an observer keeps exactly one `on_bar` per second tick, with a monotonic
        # `ts_ns` (a liquidation's `ts_event` may precede the tick that already reached it). The
        # observer's next tick carries the liquidation in its bar.
        return True

    def _held_liquidations(self, key: _BufferKey, bucket_ms: int) -> dict[str, Liquidation]:
        """Return the pair's liquidations of `bucket_ms`, emptied first only if the bucket changed."""
        if self._liquidations_bucket.get(key) != bucket_ms:
            self._buffer_liquidations[key] = {}
            self._liquidations_bucket[key] = bucket_ms
        return self._buffer_liquidations[key]

    def _drop_liquidations(self, key: _BufferKey) -> None:
        self._buffer_liquidations.pop(key, None)
        self._liquidations_bucket.pop(key, None)

    def _add_buffer_liquidations(
        self, key: _BufferKey, bucket_ms: int, rows: list[Liquidation]
    ) -> None:
        """Merge liquidations of the buffer's bucket in, each venue event once."""
        held = self._held_liquidations(key, bucket_ms)
        for row in rows:
            if _bucket_of(row.ts_event, key[1]) == bucket_ms:
                held.setdefault(row.venue_event_id, row)

    def _roll_liquidations(self, key: _BufferKey, bucket_ms: int) -> None:
        """
        At a fresh buffer's first tick or a bucket roll: union the recent tail's liquidations of
        `bucket_ms` into the held ones, which are reset only when the bucket actually changed -- a
        first tick into the bucket a seed already filled keeps the seed's archived liquidations,
        including those older than the `RECENT_SECONDS` tail.
        """
        tail = self.recent_liquidations(key[0], bucket_ms * 1_000_000, 1 << 62)
        self._add_buffer_liquidations(key, bucket_ms, tail)

    async def run(self, redis_url: str) -> None:
        """
        Subscribe to `snapshots:raw` and `liquidations:raw` forever, reconnecting on any error.

        Mirrors `RankingsBus.run()`'s discipline exactly: reconnect forever, 2s sleep
        between attempts.
        """
        logger.info("LiveCandleBus starting, url=%s", redis_url)
        handlers = {
            SNAPSHOTS_CHANNEL: self.handle_batch,
            LIQUIDATIONS_CHANNEL: self.handle_liquidations,
        }
        while True:
            try:
                logger.info("LiveCandleBus connecting...")
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(*handlers)
                    logger.info("LiveCandleBus subscribed to %s", ", ".join(handlers))
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        self._dispatch(handlers, message)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly (app shutdown)
            except Exception as exc:
                logger.warning("LiveCandleBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)

    def _dispatch(self, handlers: dict, message: dict) -> None:
        """
        Parse one pub/sub message and hand it to its channel's handler; bad JSON is ledgered, and
        so is a channel the bus did not subscribe (`live_candles.payload`), never read as a
        `snapshots:raw` batch by default.
        """
        channel = message.get("channel")
        handler = handlers.get(channel)
        if handler is None:
            error_ledger.record(
                "live_candles.payload", f"message on unexpected channel {channel!r}, SKIPPED"
            )
            return
        try:
            payload = json.loads(message["data"])
        except Exception as exc:
            site = (
                "live_candles.liquidation"
                if channel == LIQUIDATIONS_CHANNEL
                else "live_candles.parse"
            )
            error_ledger.record(site, f"{channel} message is not JSON, SKIPPED", exc)
            return
        handler(payload)
