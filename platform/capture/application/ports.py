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
The ports capture declares (DDD spine AD-D2/AD-D6): what a venue client, a downstream context and
each I/O adapter must offer for the `CaptureService` to drive them. All `typing.Protocol`,
satisfied structurally and wired explicitly by a venue's composition root
(`capture/venues/<v>/__main__.py`'s `build_capture`).
"""

import asyncio
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from typing import IO
from typing import Any
from typing import NamedTuple
from typing import Protocol

from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondRow

from capture.domain.feed_group import Feed
from capture.domain.trade_history import Fetched
from nautilus_trader.model.instruments import Instrument


class Ledger(Protocol):
    """
    Where capture reports a failure it continues past (DATA-07). Invariant: the `CaptureService`
    is the only caller of `observability.error_ledger.record` in capture; an adapter or client that
    must report one is handed the service's own `_ledger` and names a `capture.application.sites`
    site.
    """

    def __call__(self, site: str, detail: str, exc: BaseException | None = None) -> None: ...


class OnData(Protocol):
    """
    The callback a `VenueFeed` pushes every decoded market-data message to: the `CaptureService`'s
    own `_on_data`, handed to the client's factory. Invariant: it only enqueues (O(1), the hot
    path's first step), tagging the message with the connection (`Feed`) it came on; a
    one-connection client may omit the feed.
    """

    def __call__(self, data: Any, feed: Feed = ...) -> None: ...


class VenueFeed(Protocol):
    """
    A venue's WebSocket/REST client (was the `collector.py` module docstring's duck-typed contract).

    Invariant: every decoded market-data message reaches the service through the `on_data(data,
    feed)` callable the client was built with, from the event loop (`call_soon_threadsafe`), and
    `on_data` only enqueues. All methods are coroutines. `subscribe`/`unsubscribe` are called only
    by `CaptureService.apply` and its retry loop, and must be idempotent per channel and pace every
    wire call under the venue's measured limit (Bybit, Hyperliquid: `wire_channels.WireChannels`).

    Optional capabilities, found by `hasattr`: `subscribe_global()` (venue-wide channels, e.g.
    dYdX markets); `fetch_book_snapshot(iid) -> BookSnapshot` (the aligned REST cross-check,
    22.5/D-64); `resync_orderbook(iid)` (force a fresh snapshot -- only a venue whose local book can
    drift; a full-snapshot venue must not have it); `feed_states() -> dict[Feed, bool]`
    (*synchronous*: each connection's `is_active()`, polled every 0.1 s for reconnects). Story
    33.1, both synchronous: `liquidation_state() -> str | None` (the liquidation socket's
    `connected`/`reconnecting`/`down`, carried on `CaptureStatus.liquidations`; None for a client
    built without the feed) and
    `note_liquidation_restart(iid, from_ns, to_ns)` (an id's `restart` span on its first verdict in
    the process: the client's liquidation feed writes it as a `not_running` window when the id has
    a liquidation topic, held yet or not). A `subscribe` may raise
    `capture.application.feed.ChannelRetry` after its required channels are held: the id stays
    applied and is retried.
    """

    async def fetch_instruments(self) -> list: ...

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None: ...

    async def disconnect(self) -> None: ...

    async def subscribe(self, iid: str) -> None: ...

    async def unsubscribe(self, iid: str) -> None: ...


class VenueTradeHistory(Protocol):
    """
    A venue's REST trades for the reconnect backfill (story 22.14).

    Invariant: `fetch` returns exact `TradeTick`s (`capture.domain.trade_history`), oldest
    first, at or after `since_ns`, and says whether the venue's depth covered `since_ns`
    (`reached_since`); it pages no further back than `floor_ns`. Synchronous (stdlib REST): the
    `CaptureService` runs it off the event loop. It may raise; the failure is named per instrument in
    that backfill's one ledger entry.
    """

    def fetch(
        self, instrument: Instrument, since_ns: int, floor_ns: int, ts_init: int
    ) -> Fetched: ...


class ArchiveWriter(Protocol):
    """
    The one live writer of this venue's catalog leaves (AD-D18): the Parquet batch write, the
    instrument definitions, the archive-gap markers capture causes, the startup quarantine of
    unreadable files and the capture lock.

    Invariant: every catalog write goes through `ParquetDataCatalog.write_data` (NAUT-02), so the
    schema and partitioning are Nautilus's own; a batch whose write raised is reported to the caller
    (which marks the gap), never retried silently. The batch encoder is not named here: since
    Story 28.2 the columnar `kernel.second_snapshot.snapshots_to_record_batch` is registered with
    the kernel type itself (`register_arrow(..., batch_encoder=)`), so `write_data` takes it and no
    caller changed. Synchronous methods run off the event loop where they do disk I/O on the hot
    path (`write`).
    """

    @property
    def catalog_path(self) -> str: ...

    def write(self, items: list) -> None: ...

    def write_instruments(self, instruments: list) -> None: ...

    def mark_gap(
        self, iid: str, from_ns: int, to_ns: int, reason: str, count: int, ledger: Ledger
    ) -> None: ...

    def quarantine_corrupt(self, instrument_ids: Sequence[str], ledger: Ledger) -> None: ...

    async def acquire_lock(
        self, venue: str, shutting_down: asyncio.Event, ledger: Ledger
    ) -> IO[str] | None: ...

    def recent_trades(self, iid: str, start_ns: int, end_ns: int, ledger: Ledger) -> "RecentTrades":
        """
        Return `(trade_id, ts_init)` of every archived trade of `iid` whose `ts_init` lies in
        `[start_ns, end_ns]` -- the dedup window's seed -- and the newest `ts_event` among them,
        the restart backfill's baseline (D-61). Bounded by the span (MEM-01). A file it cannot
        read is ledgered (`collector.dedup_seed`) and skipped, never fatal to the seed.
        """
        ...

    def last_snapshot_second(self, iid: str) -> int | None:
        """
        Return the newest archived snapshot second (`ts_event // 1 s`) of `iid`, or None.
        Reads only the newest file(s), never the instrument's whole history.
        """
        ...

    def append_coverage(self, venue: str, lines: Sequence[str]) -> int:
        """
        Append the coverage record's lines (`capture.domain.coverage`) durably (fsync'd), in
        order, serialized across threads. Raises on failure with the file as it was before the
        call: the service ledgers it and keeps the lines. Returns the bytes of a torn tail (a
        killed process's unfinished last line) cut before this process's first append -- or
        before the first append after a failed one, whose rollback may itself have failed -- 0 if
        none, for the service to ledger; a cut whose append then failed is returned by the next
        successful one.
        """
        ...


class RecentTrades(NamedTuple):
    """
    The archive's trades of one instrument over the dedup horizon: `(trade_id, ts_init)` pairs,
    and the newest `ts_event` among them (None: no archived trade in the horizon).
    """

    ids: list[tuple[str, int]]
    newest_ts_event: int | None


class PolledRows(NamedTuple):
    """
    One REST poll round (`CaptureService.poll_loop`): the rows it parsed and the ones it could not.

    Invariant: a venue row the parser cannot turn into a `Data` row is never dropped silently --
    it is named in `malformed` as `(instrument id or None when even the id is unreadable,
    reason)`, and `poll_loop` ledgers them at its site each round (the planned or unidentifiable
    ones under `plan_only`). The command that could break it is a parser that `continue`s past a
    row without appending it here.
    """

    rows: list[Any]
    malformed: list[tuple[str | None, str]]


def finite_decimal(value: object) -> Decimal | None:
    """
    Return a polled venue value as an exact finite `Decimal`, or None when it is not one: not a
    str, an int or a `Decimal` (a `float` is refused too: `Decimal(0.1)` is its binary expansion,
    and a market value never round-trips through `float`), not numeric text
    (`InvalidOperation`), or `NaN`/`Infinity` -- a `PolledRows.malformed` row, never archived
    and never raised.
    """
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


class LiveStream(Protocol):
    """
    Capture's live Redis output: the `snapshots:raw` fan-out (parent spine AD-1) and the
    per-flush `capture:hotpath` record. Invariant: `publish` sends exactly the batch the gate
    accepted, the same objects the archive buffer holds; a failed publish loses
    that tick's live view only (the Parquet write is durable) and never stalls the sampler.
    `publish` raises on failure; the service ledgers it (`collector.snapshot_publish`) and
    carries on, so a Redis outage is counted, never a quiet WARNING (DATA-07).

    Since Story 28.1 it also carries capture's own per-flush hot-path figures:
    `publish_hotpath(venue, report)` sends one flush window's `HotPathReport.to_dict()` (queue
    depth, messages, sample-loop lag, write time; `docs/DATA_DICTIONARY.md` §1.25). It raises on
    failure too; the service ledgers it (`collector.hotpath_publish`), never touching Parquet.

    Since Story 33.4 `publish_derivs(rows)` sends one sample tick's mark, index, funding and
    open-interest rows on `derivs:raw`, each already a `kernel.derivs_wire.to_wire` row (the
    archive buffer holds the same objects). It raises on failure; the service ledgers it
    (`collector.derivs_publish`) with the row count, never touching Parquet.
    """

    async def publish(self, snapshots: list[DydxSecondSnapshot]) -> None: ...

    async def publish_hotpath(self, venue: str, report: dict[str, Any]) -> None: ...

    async def publish_derivs(self, rows: list[dict[str, Any]]) -> None: ...

    async def close(self) -> None: ...


class Notifier(Protocol):
    """The operator push (OBS-01); `observability.notify` satisfies it as a module."""

    def notify(self, channel: str, title: str, body: str) -> Any: ...


class SecondSink(Protocol):
    """
    Where capture hands the 1 s rows it has just archived.

    Invariant (a derived reader is never ahead of the archive): `apply` is called from `_flush_once`
    only with the rows whose `ParquetDataCatalog.write_data` returned, never with the buffer -- so
    nothing downstream can hold a second the Parquet archive does not. `watermarks` closes the other
    half: at startup capture reads each instrument's last applied `ts_event` and replays the archive
    from there, so a crash between a flush and its sink write is filled rather than lost forever.
    The commands that would violate it are applying `self._buffer` instead of `flushed_seconds`, and
    skipping the catch-up on the argument that the live feed will fill in anyway.

    Declared here, in capture, and satisfied structurally: the implementation (today
    `candles.application.sink.CandleSink`) never imports this module, so the dependency arrow runs
    capture -> port <- candles and no capture -> candles import exists (spine AD-D2/AD-D8).

    `apply` returns the number of seconds it actually took (rows at or before its own watermark are
    a replay and count 0). It may raise: capture ledgers the failure per instrument and carries on.

    `apply_liquidations` (Story 33.3) takes the same flush's archived `Liquidation` rows of one
    instrument with the feed, under the same never-ahead-of-the-archive rule, and capture calls it
    **before** that flush's `apply`: the liquidations lower the store's persisted feed start
    (`liquidation_feed_since`) first, so the seconds folded next read 0 `liq_*` for every bucket
    starting at or after it -- the other order would leave an id's very first flush with a
    liquidation null where the rebuild of the same day stores 0 (audit D-160). The sink applies
    each venue event once (`venue_event_id`), so the startup catch-up may replay a whole day of
    them. It returns how many were new and may raise like `apply`; a failed call is not retried
    live, only re-applied by the next start's catch-up (the last day) or the nightly rebuild.
    """

    def apply(self, instrument_id: str, rows: Sequence[SecondRow]) -> int: ...

    def apply_liquidations(self, instrument_id: str, rows: Sequence[Liquidation]) -> int: ...

    def watermarks(self) -> Mapping[str, int]: ...


class PlanDiff(Protocol):
    """
    What `CaptureService.apply` is asked to change in the collected set (spine AD-D17).

    Invariant (capture never re-derives the plan): `added` and `removed` are disjoint and name
    exactly the ids whose planned status changed, and `store_deltas` is the complete post-change
    set of ids whose raw `OrderBookDeltas` are archived -- not a delta of it -- so applying one
    diff is enough to know the whole delta-storage rule. The command that could violate it is a
    caller building a diff by hand from two id lists that were read at different times; the one
    producer is `collection_control.domain.plan.CollectionPlan`'s commands (and, for capture's own
    initial apply and the static venues, `PlanChange`). Declared here, in capture, so capture never
    imports `collection_control`: the plan's `PlanDiff` satisfies it structurally.
    """

    @property
    def added(self) -> frozenset[str]: ...

    @property
    def removed(self) -> frozenset[str]: ...

    @property
    def store_deltas(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class PlanChange:
    """
    Capture's own concrete `PlanDiff`: the initial apply of a plan's ids at `run()`, before any
    control plane produces one (every later change arrives as `collection_control`'s `PlanDiff`).
    """

    added: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()
    store_deltas: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Applied:
    """
    What one `CaptureService.apply` actually did on the wire: the ids now subscribed, the ids now
    unsubscribed, and the ids whose subscribe or unsubscribe failed (ledgered, retried by capture's
    own retry loop, or -- an id the venue does not list -- never retried). The three are disjoint.
    """

    subscribed: frozenset[str] = frozenset()
    unsubscribed: frozenset[str] = frozenset()
    failed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CaptureStatus:
    """
    Capture's read-only view for `collector:status` (AD-D17: the plan is the intent, the applied
    set is the fact).

    Invariant: `applied` is only ids whose subscribe succeeded and that are still planned, and
    `pending` is the planned ids that are not applied -- so a status row can never show as
    collected an id the feed never subscribed. `lingering` is the reverse gap: ids no longer
    planned that are still subscribed because their unsubscribe failed (retried) -- they hold venue
    wire slots the plan's cap cannot see. Built by `CaptureService.capture_status()` from its own sets
    at one instant; the counters are copies, so a reader cannot mutate capture's state.

    `last_applied` is the most recent `apply`'s result (startup or command), `None` before the
    first, and `last_applied_ns` its wall-clock time (0 before the first). It is history, not the
    live truth: an id it lists as failed may since have been subscribed by the retry loop, which
    `pending` reflects.
    """

    applied: frozenset[str]
    pending: frozenset[str]
    last_book_update_ns: Mapping[str, int]
    trade_backfill: Mapping[str, int]
    lingering: frozenset[str] = frozenset()
    last_applied: Applied | None = None
    last_applied_ns: int = 0
    # Story 33.1: the liquidation socket's state, `connected`/`reconnecting`/`down`, from the
    # client's `liquidation_state()`; None for a client without the feed (a venue without one).
    liquidations: str | None = None
