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
Venue-neutral market-data collector core (Story 22.1): own asyncio loop, ingest queue,
buffer and flush timer over a duck-typed venue client, writing to the shared
`ParquetDataCatalog` via Nautilus's own `write_data()`. No TradingNode/Strategy/DataEngine.

Extracted from the (identical) Bybit and Hyperliquid collectors, plus dYdX's venue-neutral
integrity guards: stale-trade age filter + bounded trade_id dedup (DATA-06), stale-book
skip with accumulator discard (DATA-01), crossed-book skip + ledger with resync as a
fallback only (DATA-03), `ohlc_outside_book` canary, candle-store feed, `snapshots:raw` Redis
publish, the `_second_loop` lag canary and the OBS-01 watchdog.

Trades (story 22.13): every accepted `TradeTick` is both kept for the live second (folded once
per sample by `collector_core.fold.fold_trades`) and archived raw to `data/trade_tick/<iid>/`
with both clocks untouched (`ts_event` = venue, `ts_init` = arrival). The live snapshot is
provisional and arrival-timed; `collector_core.rebuild_seconds` re-derives closed days from the
archive on exchange time.

Two clocks per mode (story 22.12, `CoreConfig.book_time_source`):
  * "arrival" (dYdX -- its book deltas carry no venue timestamp, D-49): a row is sampled at
    mid-second from the book as received and holds the trades that *arrived* since the last
    row; `ts_event == ts_init` = the sample time.
  * "venue" (Bybit, Hyperliquid): exchange second S is closed at wall S + 1 + hold_back_seconds.
    Deltas are held in `ts_event` order and applied up to S+1 (`_drain_pending_deltas`), trades
    are bucketed by `ts_event // 1 s`; a trade processed after its second closed is archived and
    counted (`collector.late_trade`) but never folded live -- the nightly rebuild places it. The
    row's `ts_event` is S + 0.5 s (same floor second and phase as an arrival row) and `ts_init`
    is when it was actually sampled, so a backtest replaying on `ts_init` never sees a book
    before it could have been known.

Client contract (duck-typed -- this docstring is the contract, there is no base class):

    fetch_instruments() -> list          raw pyo3 instruments; written to the catalog via
                                         `instruments_from_pyo3` and passed to connect()
    connect(loop, instruments) -> None   open the WS; deliver every decoded message to the
                                         `on_data(data, feed)` callable the client was built with
    disconnect() -> None
    subscribe(iid: str) -> None          trades + book (+ whatever else the venue offers)
    unsubscribe(iid: str) -> None        one WS unsubscribe per topic subscribed
    subscribe_global() -> None           OPTIONAL: venue-wide channels (e.g. dYdX markets)
    fetch_book_snapshot(iid: str)        OPTIONAL: the venue's REST book as a
        -> BookSnapshot                  `collector_core.book_check.BookSnapshot` carrying the
                                         key the live book aligns on (Bybit `sequence`,
                                         Hyperliquid `ts_event_ns`); enables the periodic
                                         aligned cross-check (22.5, audit D-64).
    resync_orderbook(iid: str) -> None   OPTIONAL: force a fresh book snapshot. Only a
                                         venue whose local book can drift (delta stream)
                                         should expose it; a full-snapshot venue must not.
    feed_states() -> dict[Feed, bool]    OPTIONAL, *synchronous*: each WS connection's
                                         `is_active()`; polled every 0.1 s for reconnects.

The client must call `on_data` from the event loop (call_soon_threadsafe) and `on_data`
itself is O(1): it only enqueues, `_ingest_loop` does the real work. `on_data(data, feed)` tags
the message with the connection (`collector_core.feed.Feed`) it came on; a client with one
connection may omit `feed` (`MAIN_FEED`).

Trade gap closure (story 22.14). The Rust WS clients reconnect and resubscribe silently (none
passes `Reconnected` to Python), so trades executed while a socket was down would be missing from
the archive with nothing reporting it (D-47). A reconnect is detected per feed on evidence:

- `feed_states()` shows the feed go inactive -> active (Bybit/Hyperliquid `is_active()`;
  dYdX's `is_connected()` stays true through a reconnect, so dYdX has no `feed_states`);
- a book-carrying feed is silent for longer than `feed_stale_seconds or stale_book_seconds`
  and then delivers a message (the net for long outages, every venue);
- the same feed re-delivers a `trade_id` it already delivered (a subscribe replay -- dYdX
  replays recent trades on every trades subscribe), after the startup grace.

Detections coalesce into one pending backfill per feed, run `_BACKFILL_SETTLE_NS` after the first
one: for every instrument that feed delivered trades or book for, `trade_backfill.fetch_trades`
reads the venue's trades of `[last archived ts_event - 5 s, now]` over stdlib REST and archives
only unseen ids (`ts_init` = the time they were archived), never into the live second: the
nightly rebuild places them. One `collector.trade_backfill` ledger entry per backfill states what
was recovered, already archived, refused, unrecoverable (venue depth) and failed.

Dual feed (`trade_feeds = 2`, Bybit and Hyperliquid): a second, trades-only connection per feed
group. Both deliver into the same bounded `trade_id` dedup, which remembers the first copy's
feed: a copy from the same feed is a replay (`duplicate`), from another feed (including the REST
source `rest`) a `duplicate_feed`. Cumulative per-feed first copies and pairwise overlaps are
logged each flush ("Trade feed arbitration"), and a feed whose last trade is more than 30 s
behind a sibling's in its group raises an OBS-01 one-sided-outage notification.

Known limit: a crash or restart gap is not backfilled, because `_last_trade_ts` lives in memory.
Upgrade path: seed it from the newest archived trade per instrument at startup.
Known limit: seconds the stale-book gate skipped during an outage have no snapshot row, so their
backfilled trades are rebuild orphans and those minutes can still mismatch the venue's klines.
Known limit: a backfill never archives a trade older than `ARRIVAL_MARGIN_NS` (the rebuild's and
prune's `ts_init` window), so a dYdX outage longer than 5 minutes stays partly unrecovered and is
reported as such. Upgrade path: a backfill-span marker the rebuild and prune read.
Known limit: the one-sided alert compares trade arrival within a group only; a group where every
feed is silent is the book watchdog's case.
Known limit: a backfill fetches its instruments one after another (each floor taken when its own
fetch starts), so on a long dYdX list the last instruments get a little less of the 5-minute
window and one slow backfill delays the next feed's. Upgrade path: a small bounded fetch pool.

Feed liveness (`_feed_last_ns`, `_feed_last_trade_ns`) runs on arrival time, the Rust client's
`ts_init` at receipt, so an ingest backlog is not mistaken for a silent socket. A redundant
trades-only socket that fails to connect or subscribe is ledgered (`collector.trade_feed`) and
dropped, never allowed to take the primary data down (`collector_core.feed.optional_feed_step`).

Snapshots are `DydxSecondSnapshot` (a venue-neutral schema despite its name -- moved in
story 22.3) so data_api serves every venue's ids with zero per-route code.
"""

import asyncio
import bisect
import http.client
import json
import logging
import math
import os
import shutil
import signal
import time
import urllib.request
from collections import defaultdict
from collections import deque
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import redis.asyncio as aioredis
from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns
from ml_signals.catalog_stats import query_second_ohlc

from collector_core import trade_backfill
from collector_core.archive_gaps import ARRIVAL_MARGIN_NS
from collector_core.archive_gaps import record_gap
from collector_core.book_check import EXACT_PRICE_TOLERANCE_LEVELS
from collector_core.book_check import EXACT_SIZE_REL_TOLERANCE
from collector_core.book_check import BookSnapshot
from collector_core.book_check import persistent
from collector_core.book_check import top_levels_mismatch
from collector_core.config import CoreConfig
from collector_core.feed import MAIN_FEED
from collector_core.feed import REST_FEED_NAME
from collector_core.feed import Feed
from collector_core.fold import fold_trades
from collector_core.integrity import ohlc_outside_book
from collector_core.second_snapshot import BOOK_DEPTH
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.instruments import instruments_from_pyo3
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_QUARANTINE_DIRNAME = "_quarantine"
_INGEST_YIELD_EVERY = 64
_IMPOSSIBLE_LOG_EVERY_NS = 60_000_000_000  # one ERROR per instrument per minute, not per second

# OBS-01: zero book updates across all instruments for 30s+ is a pipeline failure, not a
# quiet market. Deployed unattended, so this pushes a notification rather than relying on
# someone noticing a frozen chart.
_WATCHDOG_CHECK_SECONDS: float = 30.0
_WATCHDOG_STALE_NS: int = 30_000_000_000
_WATCHDOG_STARTUP_GRACE_NS: int = 60_000_000_000  # subscriptions need time to establish
_WATCHDOG_REMINDER_NS: int = 600_000_000_000  # re-notify at most every 10 min while down
# One-sided outage (story 22.14): a feed whose last trade is this far behind a sibling's in the
# same group has lost its connection while the other kept delivering.
_ONE_SIDED_NS: int = 30_000_000_000

# Trade backfill (story 22.14). The settle lets a reconnect's resubscribes and replays land first,
# so one reconnect gets one backfill; the lookback re-reads a little before the last archived
# trade (ids already archived are counted, not written twice).
_BACKFILL_SETTLE_NS: int = 3_000_000_000
_BACKFILL_LOOKBACK_NS: int = 5_000_000_000
_BACKFILL_POLL_SECONDS: float = 0.5
# Faster than the Rust clients' 250 ms minimum reconnect delay, so an inactive spell is seen.
_FEED_STATE_POLL_SECONDS: float = 0.1
_FEED_STATE_ERROR_EVERY_NS: int = 60_000_000_000
# Every failure one instrument's fetch can raise (urllib's URLError is an OSError): ledgered under
# that instrument in the backfill's entry, the other instruments continue.
_BACKFILL_FETCH_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    http.client.HTTPException,
    json.JSONDecodeError,
    trade_backfill.BackfillError,
)

# A flush carries back a TradeTick batch's newest-`ts_init` group while it is younger than this:
# every adapter stamps one `ts_init` per WS message, so a message's trades can straddle the flush,
# and `write_data` refuses a file whose `[first, last]` `ts_init` interval touches an existing one
# -- the next flush would lose its whole batch on the tie (audit, flush tie guard).
_TRADE_CARRY_NS: int = 5_000_000_000

_S_NS = 1_000_000_000
_HALF_S_NS = 500_000_000
# Venue mode: a delta held (or a trade stamped) this much beyond the hold-back after its arrival
# means the venue clock runs ahead of ours -- bounds the pending list and trade buckets (MEM-02).
_VENUE_AHEAD_NS: int = 5_000_000_000
# Venue mode: after a stall, at most this many overdue seconds are closed in one wake-up.
# Known limit: a longer stall (host suspend) leaves the older seconds without a live row; their
# trades are counted late and placed by the nightly rebuild. Upgrade path: close them from the
# archive instead of from memory. Kept well under `catalog_stats._FILE_MARGIN_NS` (60 s): every
# caught-up row gets the wake-up's `ts_init`, so its `ts_init` trails its `ts_event` by up to
# this + 1 + hold_back seconds, and readers only widen file spans by that margin.
_MAX_CATCH_UP_SECONDS = 30

# _second_loop staleness canary: if its wakeup arrives this much later than the configured
# interval, the event loop was busy and the crossed-book detection/resync guard was silently
# not running for that gap -- surface it rather than let it look like a quiet market.
_SECOND_LOOP_LAG_WARN_NS: int = 2_000_000_000

# Workaround: ParquetDataCatalog.write_data() (pinned nautilus_trader 1.229.0) has no
# compression passthrough -- it calls pq.write_table() with pyarrow's "snappy" default,
# and nautilus_trader/persistence/catalog/parquet.py can't be modified (fork rule). Patch
# pyarrow's default here instead. `pq` is a shared module object (Python caches modules in
# sys.modules), so this reaches nautilus's `import pyarrow.parquet as pq` call site too.
# Ceiling: if a future nautilus_trader version passes `compression=` explicitly, this patch
# is silently ignored -- revisit on version bump.
_orig_write_table = pq.write_table


def _write_table_zstd(*args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("compression", "zstd")
    _orig_write_table(*args, **kwargs)


pq.write_table = _write_table_zstd


def quarantine_corrupt_parquet(catalog_path: str, instrument_ids: Iterable[str]) -> None:
    """
    Move any unreadable .parquet file (e.g. left by a mid-write crash) for *this collector's*
    instruments out of the way.

    Runs once at process start, before any new writes. A half-written file from a killed
    process would otherwise sit forever next to good data and can break catalog reads or
    consolidation. Scoped to `data/<type>/<iid>/` for the given ids: every venue's collector
    shares one catalog root, and `ParquetDataCatalog` writes straight to the final path, so
    scanning a sibling's directories would quarantine a file that is merely mid-write. Only
    this process writes its own ids' directories, and it is not writing yet when this runs
    (the shared instrument-definition tables are left alone for the same reason).
    Known limit: full scan of this venue's files on every start; if that grows into the
    hundreds of thousands of files, switch to only checking files newer than the last clean
    shutdown.
    """
    root = Path(catalog_path).resolve()
    quarantine_root = root / _QUARANTINE_DIRNAME
    for iid in instrument_ids:
        for path in root.glob(f"data/*/{iid}/*.parquet"):
            if _is_readable_parquet(path):
                continue
            dest = quarantine_root / path.relative_to(root)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
            except OSError as e:
                # Must not escape: this runs before run_forever's restart loop.
                error_ledger.record("collector.corrupt_parquet", f"could not quarantine {path}", e)
                continue
            error_ledger.record(
                "collector.corrupt_parquet", f"corrupt parquet file quarantined: {path} -> {dest}"
            )
            if path.parent.parent.name == "trade_tick":
                _mark_quarantined_trades(str(root), iid, path)


def _mark_quarantined_trades(catalog_path: str, iid: str, path: Path) -> None:
    """
    Record the quarantined trade file's span as an archive gap, so the nightly rebuild keeps the
    live values of the rows those trades were folded into. The name spans the batch's `ts_init`;
    a row folding them is sampled after arrival, so `to` is widened by the arrival margin. An
    unparseable name marks nothing and is ledgered: that file was never written by the collector.
    """
    try:
        first, _, last = path.stem.partition("_")
        from_ns, to_ns = _stamp_to_ns(first), _stamp_to_ns(last) + ARRIVAL_MARGIN_NS
    except ValueError as e:
        error_ledger.record(
            "collector.corrupt_parquet", f"no span in {path.name}; no gap marked", e
        )
        return
    record_gap(catalog_path, iid, from_ns, to_ns, "quarantined", 0)


def _is_readable_parquet(path: Path) -> bool:
    try:
        pq.ParquetFile(path)
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class AlertTexts:
    """
    An alert's three messages. `still` and `recovered` are `str.format` templates receiving
    `down_for_s` (seconds since the alert opened).
    """

    down: str
    still: str
    recovered: str


def _alert_transition(
    now_ns: int,
    is_down: bool,
    down_since_ns: int | None,
    last_reminder_ns: int,
    texts: AlertTexts,
) -> tuple[str | None, int | None, int]:
    """
    Pure state-machine step for an OBS-01 alert: (message_or_None, down_since_ns, last_reminder_ns).

    Alert once on the transition to down, remind at most every `_WATCHDOG_REMINDER_NS` while it
    stays down, and notify once on recovery. Kept separate from the asyncio loop and the notify
    transport so the alerting/debounce logic is unit-testable without mocking network calls.
    """
    if is_down:
        if down_since_ns is None:
            return (texts.down, now_ns, now_ns)
        if now_ns - last_reminder_ns > _WATCHDOG_REMINDER_NS:
            down_for_s = (now_ns - down_since_ns) / 1e9
            return (texts.still.format(down_for_s=down_for_s), down_since_ns, now_ns)
        return (None, down_since_ns, last_reminder_ns)

    if down_since_ns is not None:
        down_for_s = (now_ns - down_since_ns) / 1e9
        return (texts.recovered.format(down_for_s=down_for_s), None, 0)

    return (None, None, last_reminder_ns)


def _watchdog_transition(
    now_ns: int,
    is_stale: bool,
    down_since_ns: int | None,
    last_reminder_ns: int,
    name: str = "collector",
) -> tuple[str | None, int | None, int]:
    """Step the every-book-stale watchdog (see `_alert_transition`)."""
    texts = AlertTexts(
        down=(
            f"{name}: all live instruments' order books have gone stale "
            "(no OrderBookDeltas for 30s+) — feed may be down"
        ),
        still=f"{name}: still down, no book updates for {{down_for_s:.0f}}s",
        recovered=f"{name}: recovered after {{down_for_s:.0f}}s",
    )
    return _alert_transition(now_ns, is_stale, down_since_ns, last_reminder_ns, texts)


def _one_sided_texts(name: str, group: str, behind: list[str], live: list[str]) -> AlertTexts:
    return AlertTexts(
        down=(
            f"{name}: one-sided outage in feed group {group!r}: {', '.join(behind)} is 30s+ "
            f"behind {', '.join(live)} in trade arrivals — that connection is down or stalled"
        ),
        still=(
            f"{name}: one-sided outage in feed group {group!r} still open after "
            f"{{down_for_s:.0f}}s: {', '.join(behind)} silent"
        ),
        recovered=(
            f"{name}: one-sided outage in feed group {group!r} recovered after {{down_for_s:.0f}}s"
        ),
    )


def _notify(message: str, title: str = "collector") -> None:
    """POST to a ntfy.sh-compatible topic URL. Log CRITICAL if WATCHDOG_NTFY_URL isn't set."""
    url = os.environ.get("WATCHDOG_NTFY_URL")
    if not url:
        logger.critical(message)
        return
    request = urllib.request.Request(  # noqa: S310 (fixed, operator-configured URL)
        url,
        data=message.encode(),
        headers={"Title": title},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass
    except Exception:  # a malformed URL raises ValueError; the watchdog loop must not die
        logger.exception("Watchdog notification failed")


# Parquet is flushed this many seconds past each interval boundary (:02 for the default 60 s), so a
# minute that just closed is in the archive -- and, right after it, in the candle store -- ~2 s later.
_FLUSH_PHASE_S = 2.0
_CATCH_UP_MAX_NS = 86_400 * 1_000_000_000


def _seconds_until_next_flush(now: float, interval: float) -> float:
    """Return seconds from `now` (epoch) to the next wall-clock flush; always in (0, interval]."""
    return interval - (now - _FLUSH_PHASE_S) % interval


def _next_sample_at(now: float, interval: float, last_tick: float | None) -> float:
    """
    Next sample time (epoch seconds): the first `k * interval + interval / 2` strictly after
    `now`, and never in the interval bucket (`floor(t / interval)`) `last_tick` already sampled.

    A plain `sleep(interval)` after the work drifts by the work's duration every tick and skips a
    whole floor second every few hundred seconds, orphaning that second's trades. The mid-interval
    phase leaves half an interval of margin both ways, so an early wake-up still lands in its own
    bucket and a late one is followed by the next free bucket, never a second tick in the same one.
    """
    k = math.floor((now - interval / 2) / interval) + 1
    if last_tick is not None:
        k = max(k, math.floor(last_tick / interval) + 1)
    return k * interval + interval / 2


def _split_open_ts_init_group(
    items: list[Any], now_ns: int, queue_pending: bool
) -> tuple[list[Any], list[Any]]:
    """
    Split a `ts_init`-sorted batch into (write now, carry to the next flush): the group sharing
    the newest `ts_init` is carried while it is younger than `_TRADE_CARRY_NS`, or while the
    ingest queue still holds messages (under lag, the rest of that WS message can sit there for
    longer than any age bound).
    """
    newest = items[-1].ts_init
    if now_ns - newest >= _TRADE_CARRY_NS and not queue_pending:
        return items, []
    cut = bisect.bisect_left([d.ts_init for d in items], newest)
    return items[:cut], items[cut:]


def _due_seconds(now_ns: int, hold_back_ns: int, last_closed: int | None) -> range:
    """
    Exchange seconds whose close time (`S + 1 + hold_back`) has passed and that are not closed
    yet, oldest first; the first call closes only the latest one.
    """
    latest = (now_ns - hold_back_ns) // _S_NS - 1
    first = latest if last_closed is None else last_closed + 1
    return range(max(first, latest - _MAX_CATCH_UP_SECONDS + 1), latest + 1)


def _next_close_at(now: float, hold_back_s: float, last_closed: int | None) -> float:
    """Wall time (epoch s) at which the next unclosed exchange second is due; may be <= `now`."""
    second = math.floor(now - hold_back_s) if last_closed is None else last_closed + 1
    return second + 1 + hold_back_s


async def _publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """
    Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:raw.

    Empty batches are silently dropped. Publish failures are logged and swallowed —
    missing one tick is acceptable per the architecture (the Parquet write is durable).
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    try:
        await redis_client.publish("snapshots:raw", payload)
    except Exception as e:
        logger.warning("Redis publish failed: %s", e)


_CROSSCHECK_CONFIRM_SECONDS = 2.0  # gap before re-comparing a sequence-aligned mismatch (22.5)
# Consecutive rounds that could not be aligned before the ledger says so: an hour at the 300 s
# default. Unaligned rounds are skipped, never judged against the wall clock (audit D-64).
_CROSSCHECK_UNALIGNED_STREAK = 12
# A live capture: (OrderBook.sequence, OrderBook.ts_last, (bids, asks) top-20) after one apply.
_Capture = tuple[int, int, tuple[list, list]]


def _restamped(trade: TradeTick, ts_init: int) -> TradeTick:
    return TradeTick(
        trade.instrument_id,
        trade.price,
        trade.size,
        trade.aggressor_side,
        trade.trade_id,
        trade.ts_event,
        ts_init,
    )


@dataclass
class _BackfillRequest:
    """
    One feed's pending backfill: every detection until it runs coalesces into it.

    `since` is each instrument's pre-gap baseline (the newest archived trade `ts_event` before the
    outage), captured at the earliest evidence and only ever lowered: by the time the backfill
    runs, trades after the resume have advanced `_last_trade_ts`, and reading it then would skip
    the whole outage (network-cut test, 2026-09-21: 368 of 382 ADAUSDT trades not fetched).
    """

    due_ns: int
    reasons: list[str]
    since: dict[str, int]

    def lower(self, baselines: Mapping[str, int]) -> None:
        for iid, ts in baselines.items():
            self.since[iid] = min(self.since.get(iid, ts), ts)


@dataclass
class _BackfillReport:
    """What one backfill did, for its single `collector.trade_backfill` ledger entry."""

    feed: str
    reasons: list[str]
    instruments: int = 0
    backfilled: int = 0
    already: int = 0
    refused: int = 0
    no_baseline: int = 0
    unrecoverable: dict[str, float] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def message(self) -> str:
        unrecoverable = {iid: round(s, 3) for iid, s in self.unrecoverable.items()}
        return (
            f"feed {self.feed} ({'; '.join(self.reasons)}): {self.instruments} instruments, "
            f"backfilled {self.backfilled}, already archived {self.already}, refused "
            f"{self.refused} (older than the {ARRIVAL_MARGIN_NS // 1_000_000_000} s arrival "
            f"margin), unrecoverable seconds {unrecoverable}, no baseline {self.no_baseline}, "
            f"errors {self.errors}"
        )


class Collector:
    """
    One venue's collector: `config` thresholds, a duck-typed `client` (contract in the
    module docstring) and `extra_loops` -- no-arg coroutine functions started as tasks
    alongside the core loops (e.g. a REST open-interest poll).
    """

    def __init__(
        self,
        config: CoreConfig,
        client: Any,
        extra_loops: tuple[Callable[[], Awaitable[None]], ...] = (),
    ) -> None:
        self._config = config
        self._client = client
        self._extra_loops = extra_loops
        catalog_path = Path(config.catalog_path).resolve()
        catalog_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(str(catalog_path))
        # Derived candle store for the UI (the catalog stays the archive): this collector is its
        # single writer, one file per venue (compose sets CANDLES_DB_PATH). Rebuildable via build_candles.
        self._candle_db = candle_store.connect_rw(
            os.environ.get("CANDLES_DB_PATH", str(catalog_path.parent / "candles" / "candles.db"))
        )

        self._buffer: dict[tuple[type, str], list[Any]] = defaultdict(list)
        # Unbounded: a real overflow would mean the process can't keep up with the
        # exchange at all -- revisit with a maxsize + drop policy only if observed.
        self._ingest_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._redis: aioredis.Redis | None = None
        self._stop = asyncio.Event()

        self._live_books: dict[str, OrderBook] = {}
        self._last_book_update_ns: dict[str, int] = {}
        # Any WS message at all (story 22.5's feed-liveness gate) -- REST-polled data must
        # therefore go straight to `_buffer`, never through `_on_data`/`_process_data`.
        self._last_feed_message_ns: int = 0
        # First-observed-crossed time per instrument; cleared once seen uncrossed.
        self._crossed_since_ns: dict[str, int] = {}
        # Instruments whose forced resync failed after the local book was dropped: retried
        # from _sample_tick, otherwise they would stay bookless forever (the Rust client's
        # reconnect only resubscribes what it still holds).
        self._resync_pending: set[str] = set()
        self._last_no_book_log_ns: dict[str, int] = {}
        self._book_crosscheck_mismatches: defaultdict[str, int] = defaultdict(int)
        # Armed cross-checks only (audit D-64): captures of the live top-20 per applied message,
        # the message-arrival count since arming, and the event both bump. Empty otherwise, so
        # the hot path pays one dict lookup per book message.
        self._crosscheck_captures: dict[str, list[_Capture]] = {}
        self._crosscheck_arrivals: dict[str, int] = {}
        self._crosscheck_events: dict[str, asyncio.Event] = {}
        self._crosscheck_unaligned: defaultdict[str, int] = defaultdict(int)

        # This sample interval's accepted trades per instrument, folded once by `_sample_tick`
        # (`fold_trades`: exact integer sums). An empty/absent list means no trade this second,
        # distinct from a trade at price 0 -- the fold yields None OHLC, never a fabricated price.
        self._second_trades: defaultdict[str, list[TradeTick]] = defaultdict(list)

        # Venue mode (story 22.12) only -- every structure below stays empty in arrival mode.
        self._venue_time = config.book_time_source == "venue"
        self._hold_back_ns = int(config.hold_back_seconds * 1e9)
        # Held deltas per instrument, sorted by (ts_event, arrival seq); ts_init kept for the
        # overflow bound. `_pending_seq` makes every key unique, so deltas are never compared.
        self._pending_deltas: defaultdict[str, list[tuple[int, int, int, OrderBookDeltas]]] = (
            defaultdict(list)
        )
        self._pending_seq = 0
        self._book_event_ns: dict[str, int] = {}  # ts_event of the last applied delta
        self._venue_trades: defaultdict[str, dict[int, list[TradeTick]]] = defaultdict(dict)
        self._last_closed_second: int | None = None
        self._late_trades: defaultdict[str, int] = defaultdict(int)
        self._ahead_trades: defaultdict[str, int] = defaultdict(int)
        self._late_deltas: defaultdict[str, int] = defaultdict(int)
        self._pre_start_trades: defaultdict[str, int] = defaultdict(int)

        # DATA-06 guards: subscribe-time history dropped by age; reconnect replays (still
        # "fresh" after a short outage) dropped by bounded trade_id dedup. Both counted and
        # reported every flush by _report_stale_trades -- never silent (DATA-05).
        self._stale_trades_dropped: defaultdict[str, int] = defaultdict(int)
        self._duplicate_trades_dropped: defaultdict[str, int] = defaultdict(int)
        self._deltas_before_snapshot_dropped: defaultdict[str, int] = defaultdict(int)
        # Bounded per instrument (MEM-02): the id order evicts in lockstep with the map, which
        # remembers the feeds that delivered each id, first one first (story 22.14's
        # arbitration; a feed repeating an id is a replay, counted once per feed).
        self._seen_trade_ids: defaultdict[str, deque[str]] = defaultdict(
            lambda: deque(maxlen=config.seen_trade_ids)
        )
        self._trade_feeds_seen: defaultdict[str, dict[str, list[str]]] = defaultdict(dict)
        self._duplicate_feed_dropped: defaultdict[str, int] = defaultdict(int)
        self._last_impossible_log_ns: dict[str, int] = {}

        self._last_second_loop_tick_ns: int | None = None
        if config.snapshot_interval_seconds != 1.0:
            # Not refused (config validation is unchanged), but loud: the nightly rebuild maps
            # trades to rows by floor second, which assumes one row per second.
            error_ledger.record(
                "collector.cadence",
                f"snapshot_interval_seconds={config.snapshot_interval_seconds}, not 1.0: "
                "rebuild_seconds' floor-second mapping assumes 1 s rows",
            )

        self._watchdog_started_ns: int = time.time_ns()
        self._watchdog_down_since_ns: int | None = None
        self._watchdog_last_reminder_ns: int = 0

        # -- story 22.14: per-feed liveness, reconnect detection, backfill, arbitration --------
        # All keyed by feed name or instrument id: bounded by the connections and instruments.
        self._instruments: dict[str, Instrument] = {}  # Cython instruments, kept by run()
        self._feeds: set[Feed] = set()
        self._feed_last_ns: dict[str, int] = {}
        self._feed_last_trade_ns: dict[str, int] = {}
        # Only TradeTick/OrderBookDeltas teach it: dYdX's markets channel carries every market.
        self._feed_instruments: defaultdict[str, set[str]] = defaultdict(set)
        self._feed_active: dict[Feed, bool] = {}
        # A feed's baselines, captured when it was first seen inactive (the flip's pre-gap state).
        self._inactive_baselines: dict[str, dict[str, int]] = {}
        self._feed_state_error_ns: int = 0
        self._last_trade_ts: dict[str, int] = {}  # max ts_event archived, live or backfilled
        self._backfill_requests: dict[str, _BackfillRequest] = {}
        self._trade_backfill_counts: defaultdict[str, int] = defaultdict(int)  # cumulative
        self._feed_first: defaultdict[str, int] = defaultdict(int)  # cumulative first copies
        self._feed_overlap: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._one_sided_state: dict[str, tuple[int | None, int]] = {}

    # -- hooks a venue subclass may override -------------------------------------------------

    def _instrument_ids(self) -> Iterable[str]:
        return self._config.instruments

    def _clear_book_state(self, iid: str) -> None:
        """
        Drop per-instrument book tracking state (on resync/unsubscribe).

        Otherwise a later resubscribe reads a stale `_crossed_since_ns` and fires an
        unwarranted resync.
        """
        self._live_books.pop(iid, None)
        self._crossed_since_ns.pop(iid, None)
        # Venue mode: held messages belong to the dropped book; counted with the other deltas
        # dropped in a subscribe/resync window, never silently.
        held = self._pending_deltas.pop(iid, None)
        if held:
            self._deltas_before_snapshot_dropped[iid] += len(held)
        self._book_event_ns.pop(iid, None)

    def _apply_deltas(self, iid: str, deltas: OrderBookDeltas) -> None:
        if not deltas.deltas:
            return
        book = self._live_books.get(iid)
        if book is None:
            if not deltas.deltas[0].is_clear:
                # A book must start from a snapshot (Clear + levels -- both the Bybit and
                # Hyperliquid adapters emit one). After a resync, or before the first
                # snapshot, in-flight incremental deltas would otherwise build a shallow
                # book that looks uncrossed and passes every gate. Counted and reported
                # each flush by _report_stale_trades -- never silent.
                self._deltas_before_snapshot_dropped[iid] += 1
                return
            book = self._live_books[iid] = OrderBook(deltas.instrument_id, BookType.L2_MBP)
        for delta in deltas.deltas:
            book.apply_delta(delta)
        self._last_book_update_ns[iid] = time.time_ns()
        if iid in self._crosscheck_captures:
            self._capture_book(iid)

    async def _handle_crossed_book(self, iid: str, book: OrderBook, now_ns: int) -> bool:
        """
        Return True when the sample must be skipped.

        A cross on a central-book venue is *our* local corruption (or the venue's), never normal: ledger it once per episode, skip every
        tick, and only if the client can resync and the book stayed crossed longer than
        `crossed_resync_seconds`, force a fresh snapshot (DATA-03: a fallback, never the fix
        -- a rising resync count is an open DATA-02 incident). A client without
        `resync_orderbook` (full-snapshot venue) only ever skips; its next message replaces
        the book.
        """
        bid, ask = book.best_bid_price().as_double(), book.best_ask_price().as_double()
        if bid < ask:
            since = self._crossed_since_ns.pop(iid, None)
            if since is not None:
                logger.info(
                    "Crossed book for %s resolved after %.2fs (now bid=%s ask=%s)",
                    iid,
                    (now_ns - since) / 1e9,
                    bid,
                    ask,
                )
            return False
        # Membership, not `since == now_ns`: a venue-mode catch-up closes several seconds with
        # one `now_ns`, which must not ledger one episode once per overdue second.
        first_seen = iid not in self._crossed_since_ns
        since = self._crossed_since_ns.setdefault(iid, now_ns)
        if first_seen:
            error_ledger.record("collector.crossed_book", f"{iid} bid={bid} ask={ask}")
        logger.warning(
            "Crossed book for %s (bid=%s >= ask=%s) for %.1fs — skipping sample",
            iid,
            bid,
            ask,
            (now_ns - since) / 1e9,
        )
        if hasattr(self._client, "resync_orderbook") and (
            now_ns - since > self._config.crossed_resync_seconds * 1e9
        ):
            # Ledgered even when it succeeds (DATA-03): a forced resync is a fallback, never
            # a fix, and a rising count is an open DATA-02 incident that must show in
            # /api/errors, not only in Dozzle. Repeats per crossed_resync_seconds window
            # while the book keeps coming back crossed -- an unresolved incident must keep
            # alerting, not go quiet after one line.
            error_ledger.record(
                "collector.resync",
                f"forced resync for {iid}: crossed for {(now_ns - since) / 1e9:.0f}s "
                "(DATA-03 fallback, not a fix)",
            )
            await self._resync(iid)
        return True

    async def _resync(self, iid: str) -> None:
        """Drop the local book and ask the venue for a fresh snapshot; retried on failure."""
        self._clear_book_state(iid)
        try:
            await self._client.resync_orderbook(iid)
            self._resync_pending.discard(iid)
        except Exception as e:
            # The unsubscribe half may have gone through: retry from _sample_tick rather
            # than leave the instrument bookless forever.
            self._resync_pending.add(iid)
            error_ledger.record("collector.resync", f"resync failed for {iid}, retrying", e)

    # -- ingest ------------------------------------------------------------------------------

    def _on_data(self, data: Any, feed: Feed = MAIN_FEED) -> None:
        # Runs on the event loop from the Rust callback: O(1) only, _ingest_loop does the work.
        try:
            self._ingest_queue.put_nowait((data, feed))
        except Exception as e:
            error_ledger.record(
                "collector.enqueue", f"failed to enqueue {type(data).__name__}, DROPPED", e
            )

    async def _ingest_loop(self) -> None:
        # Yields every _INGEST_YIELD_EVERY messages so a burst can't starve _second_loop.
        processed = 0
        while not self._stop.is_set():
            try:
                data, feed = await asyncio.wait_for(self._ingest_queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                self._process_data(data, feed)
            except Exception as e:
                error_ledger.record(
                    "collector.process", f"failed to process {type(data).__name__}, DROPPED", e
                )
            processed += 1
            if processed % _INGEST_YIELD_EVERY == 0:
                await asyncio.sleep(0)

    def _process_data(self, data: Any, feed: Feed = MAIN_FEED) -> None:
        now_ns = time.time_ns()
        # Feed liveness runs on arrival (the Rust client's `ts_init` at receipt), not on when the
        # ingest loop gets to the message: a queue backlog or loop stall is not a silent socket.
        arrival_ns = getattr(data, "ts_init", 0) or now_ns
        self._note_feed_message(feed, now_ns, arrival_ns)
        if isinstance(data, OrderBookDeltas):
            iid = str(data.instrument_id)
            self._feed_instruments[feed.name].add(iid)
            if iid in self._crosscheck_arrivals:  # armed: REST is fetched right after a push
                self._crosscheck_arrivals[iid] += 1
                self._crosscheck_events[iid].set()
            if self._venue_time:
                self._hold_deltas(iid, data, now_ns)
            else:
                self._apply_deltas(iid, data)
        elif isinstance(data, TradeTick):
            self._accept_live_trade(data, feed, now_ns, arrival_ns)
        elif isinstance(data, QuoteTick):
            pass  # derivable from the snapshots; not persisted
        else:  # mark/index price, funding rate, open interest, ... -> catalog as-is
            self._buffer[(type(data), str(data.instrument_id))].append(data)

    def _hold_deltas(self, iid: str, deltas: OrderBookDeltas, now_ns: int) -> None:
        """Venue mode: hold a message until its second closes, in `ts_event` order."""
        # Arrival, not ts_event: the OBS-01 watchdog must never read a hold-back as a dead feed.
        self._last_book_update_ns[iid] = now_ns
        closed = self._last_closed_second
        if closed is not None and deltas.ts_event < (closed + 1) * _S_NS:
            # Its second already closed: applied at the next close (a book cannot be rewound).
            self._late_deltas[iid] += 1
        self._pending_seq += 1
        bisect.insort(
            self._pending_deltas[iid], (deltas.ts_event, self._pending_seq, deltas.ts_init, deltas)
        )

    def _bucket_venue_trade(self, iid: str, trade: TradeTick) -> None:
        """
        Venue mode: put an accepted trade into its exchange second, unless that second already
        closed (late) or its stamp runs implausibly ahead of its arrival (venue clock ahead).
        Either way it is archived by the caller and counted -- never dropped, never folded live.
        """
        second = trade.ts_event // _S_NS
        if self._last_closed_second is not None and second <= self._last_closed_second:
            self._late_trades[iid] += 1
        elif trade.ts_event - trade.ts_init > self._hold_back_ns + _VENUE_AHEAD_NS:
            self._ahead_trades[iid] += 1
        else:
            self._venue_trades[iid].setdefault(second, []).append(trade)

    def _drain_pending_deltas(self, boundary_ns: int) -> None:
        """
        Apply every held delta with `ts_event < boundary_ns`, in `ts_event` order, through the
        `_apply_deltas` hook (so a venue's own checks -- Bybit's `u` canary -- run on it). The due
        items are taken out first: the hook may drop the book state, pending list included.
        """
        for iid in list(self._pending_deltas):
            pending = self._pending_deltas[iid]
            cut = bisect.bisect_left(pending, (boundary_ns,))
            due = pending[:cut]
            del pending[:cut]
            for ts_event, _, _, deltas in due:
                self._apply_deltas(iid, deltas)
                if iid in self._live_books:
                    # max: a late message applied after newer ones must not age the book.
                    self._book_event_ns[iid] = max(self._book_event_ns.get(iid, 0), ts_event)
            if not self._pending_deltas.get(iid):
                self._pending_deltas.pop(iid, None)

    def _check_pending_overflow(self, now_ns: int) -> None:
        """
        MEM-02 bound: a delta still held `hold_back + 5 s` after it arrived has a `ts_event` that
        far ahead of our clock -- the book cannot be venue-timed. Drop it (ledgered) and resync
        where the client can; a full-snapshot venue's next message rebuilds the book.
        """
        limit = self._hold_back_ns + _VENUE_AHEAD_NS
        for iid, pending in list(self._pending_deltas.items()):
            held_ns = now_ns - min(item[2] for item in pending)
            if held_ns <= limit:
                continue
            can_resync = hasattr(self._client, "resync_orderbook")
            error_ledger.record(
                "collector.pending_deltas",
                f"{iid}: {len(pending)} book messages held {held_ns / 1e9:.1f}s > "
                f"hold_back_seconds + {_VENUE_AHEAD_NS / 1e9:.0f}s (venue clock ahead of "
                "arrival?): book dropped" + (", resync queued" if can_resync else ""),
            )
            self._clear_book_state(iid)
            if can_resync:
                self._resync_pending.add(iid)

    def _note_feed_message(self, feed: Feed, now_ns: int, arrival_ns: int) -> None:
        """
        Per-feed liveness, on arrival time. Only a book-carrying feed moves the feed-level
        staleness gate (`_last_feed_message_ns`, processing time as before): a trades-only socket
        being alive says nothing about the book. A book feed that was silent past the feed-stale
        bound and speaks again has reconnected.
        """
        self._feeds.add(feed)
        previous = self._feed_last_ns.get(feed.name)
        self._feed_last_ns[feed.name] = max(previous or 0, arrival_ns)
        if feed.trades_only:
            return
        self._last_feed_message_ns = now_ns
        silent_ns = arrival_ns - previous if previous is not None else 0
        if silent_ns > (self._config.feed_stale_seconds or self._config.stale_book_seconds) * 1e9:
            self._schedule_backfill(feed.name, now_ns, f"feed silent {silent_ns / 1e9:.1f}s")

    def _accept_live_trade(self, data: TradeTick, feed: Feed, now_ns: int, arrival_ns: int) -> None:
        iid = str(data.instrument_id)
        # Any trade message is liveness for the one-sided check, replayed or not.
        self._feed_instruments[feed.name].add(iid)
        self._feed_last_trade_ns[feed.name] = max(
            self._feed_last_trade_ns.get(feed.name, 0), arrival_ns
        )
        trade_id = str(data.trade_id)
        feeds = self._trade_feeds_seen[iid].get(trade_id)
        if feeds is not None and feed.name in feeds:
            # Checked before the age filter: a replay is mostly older than it (dYdX replays up
            # to 1000 trades), and an id this feed already delivered is the evidence either way.
            self._on_replayed_trade(iid, data, feed, now_ns)
            return
        if now_ns - data.ts_event > self._config.stale_trade_seconds * 1e9:
            self._stale_trades_dropped[iid] += 1
            return
        if feeds is None:
            self._register_trade(iid, trade_id, feed.name)
            self._feed_first[feed.name] += 1
            self._fold_live(iid, data)
            # Archived as received (both clocks), so the nightly rebuild can re-derive the
            # second from exchange time and correct what the live fold got wrong (D-45).
            self._buffer[(TradeTick, iid)].append(data)
            self._advance_last_trade(iid, data.ts_event)
            return
        feeds.append(feed.name)  # bounded by the number of feeds
        self._duplicate_feed_dropped[iid] += 1
        self._feed_overlap[(feeds[0], feed.name)] += 1
        if feeds[0] == REST_FEED_NAME:
            # The backfill archived it before this live copy was processed: fold the live copy
            # (never archived twice), or the live second misses a trade the live feed delivered.
            self._fold_live(iid, data)

    def _fold_live(self, iid: str, trade: TradeTick) -> None:
        """
        Put an accepted live trade into the live row: its exchange second in venue mode (story
        22.12, which may count it late or ahead instead), its arrival second otherwise.
        """
        if self._venue_time:
            self._bucket_venue_trade(iid, trade)
        else:
            self._second_trades[iid].append(trade)

    def _on_replayed_trade(self, iid: str, data: TradeTick, feed: Feed, now_ns: int) -> None:
        self._duplicate_trades_dropped[iid] += 1
        if now_ns - self._watchdog_started_ns >= _WATCHDOG_STARTUP_GRACE_NS:
            # The replay may deliver the gap's new trades before this one, advancing the
            # baseline past the gap; a trade we already had predates it.
            self._schedule_backfill(feed.name, now_ns, "replayed trade ids", {iid: data.ts_event})

    def _first_copy_feed(self, iid: str, trade_id: str) -> str | None:
        """Return the feed that delivered `trade_id` first, while it is inside the dedup window."""
        feeds = self._trade_feeds_seen[iid].get(trade_id)
        return feeds[0] if feeds else None

    def _register_trade(self, iid: str, trade_id: str, feed_name: str) -> None:
        seen, order = self._trade_feeds_seen[iid], self._seen_trade_ids[iid]
        if len(order) == order.maxlen:
            seen.pop(order[0], None)
        order.append(trade_id)
        seen[trade_id] = [feed_name]

    def _advance_last_trade(self, iid: str, ts_event: int) -> None:
        self._last_trade_ts[iid] = max(self._last_trade_ts.get(iid, ts_event), ts_event)

    def _report_stale_trades(self) -> None:
        if self._stale_trades_dropped:
            logger.info(f"Dropped subscribe-time trade history: {dict(self._stale_trades_dropped)}")
            self._stale_trades_dropped.clear()
        if self._duplicate_trades_dropped:
            logger.warning(
                f"Dropped duplicate trades (replayed after reconnect?): {dict(self._duplicate_trades_dropped)}"
            )
            self._duplicate_trades_dropped.clear()
        if self._duplicate_feed_dropped:
            logger.warning(
                "Dropped duplicate_feed trades (first copy archived from another feed): "
                f"{dict(self._duplicate_feed_dropped)}"
            )
            self._duplicate_feed_dropped.clear()
        if self._deltas_before_snapshot_dropped:
            logger.warning(
                "Dropped order-book deltas that arrived before a snapshot (subscribe/resync "
                f"window): {dict(self._deltas_before_snapshot_dropped)}"
            )
            self._deltas_before_snapshot_dropped.clear()
        self._report_venue_counts()
        self._report_trade_sources()

    def _report_trade_sources(self) -> None:
        """Cumulative REST-backfilled counts and, with more than one live feed, arbitration."""
        if self._trade_backfill_counts:
            logger.info(
                f"Trades backfilled over REST (cumulative): {dict(self._trade_backfill_counts)}"
            )
        if len(self._feed_first) > 1:
            logger.info(f"Trade feed arbitration (cumulative): {self._arbitration_summary()}")

    def _arbitration_summary(self) -> str:
        """Per feed: first copies, first copies no other feed delivered, and pairwise overlaps."""
        parts = []
        for feed_name in sorted(self._feed_first):
            first = self._feed_first[feed_name]
            shared = sum(n for (a, _), n in self._feed_overlap.items() if a == feed_name)
            parts.append(f"{feed_name}: first {first}, only-this-feed {first - shared}")
        both = {f"{a}+{b}": n for (a, b), n in sorted(self._feed_overlap.items())}
        return f"{'; '.join(parts)}; both {both}"

    def _report_venue_counts(self) -> None:
        """Venue mode: one ledger entry per instrument per report cycle (every flush)."""
        for site, counts, what in (
            ("collector.late_trade", self._late_trades, "arrived after their second closed"),
            ("collector.venue_clock_ahead", self._ahead_trades, "were stamped ahead of arrival"),
        ):
            for iid, n in counts.items():
                error_ledger.record(
                    site,
                    f"{iid}: {n} trades {what} (hold_back_seconds="
                    f"{self._config.hold_back_seconds}): archived, not in the live row; the "
                    "nightly rebuild places them",
                )
            counts.clear()
        if self._pre_start_trades:
            logger.info(
                "Trades received before the first venue second closed (archived, placed by the "
                f"nightly rebuild): {dict(self._pre_start_trades)}"
            )
            self._pre_start_trades.clear()
        if self._late_deltas:
            logger.warning(
                "Book messages applied after their second closed (venue mode, counted into "
                f"the next second): {dict(self._late_deltas)}"
            )
            self._late_deltas.clear()

    def _discard_second_accumulators(self, iid: str, second: int | None = None) -> None:
        """
        Drop this tick's trades for `iid` from the live second without emitting them. Called on
        every skipped sample (no book, crossed, stale): otherwise an outage's trades sit in the
        list until the next *valid* tick folds them, stamping the whole span's price range onto
        one second -- a giant-range candle at recovery instead of an honest gap. They stay in the
        raw archive: the nightly rebuild counts them as orphan trades (no snapshot row).
        `second` (venue mode): the exchange second being closed.
        """
        if second is None:
            self._second_trades.pop(iid, None)
        else:
            self._venue_trades.get(iid, {}).pop(second, None)

    # -- flush -------------------------------------------------------------------------------

    async def _flush_once(self, final: bool = False) -> None:
        """
        Write every buffered batch through `write_data`, each sorted by `ts_init` (stable).

        Unless `final` (shutdown: everything must go), a `TradeTick` batch keeps back the trades
        sharing its newest `ts_init` while that group is younger than `_TRADE_CARRY_NS`: the rest
        of that WS message may still be in the ingest queue (see `_TRADE_CARRY_NS`).
        """
        flushed_seconds: dict[str, list[DydxSecondSnapshot]] = {}
        now_ns = time.time_ns()
        batches = self._take_batches(now_ns, final)
        for key, items in batches:
            try:
                # Real disk I/O -- off the event loop so _second_loop isn't stalled.
                await asyncio.to_thread(self._catalog.write_data, items)
            except Exception as e:
                error_ledger.record(
                    "collector.flush_write", f"failed to write {key}, {len(items)} items LOST", e
                )
                if key[0] is TradeTick:
                    self._mark_lost_trades(key[1], items, now_ns)
                continue
            if key[0] is DydxSecondSnapshot:
                flushed_seconds[key[1]] = items
        self._apply_to_candle_store(flushed_seconds)

    def _take_batches(self, now_ns: int, final: bool) -> list[tuple[tuple[type, str], list[Any]]]:
        """
        Swap every non-empty buffer out as a `ts_init`-sorted batch, leaving any carried items.

        When a trade group is carried, that instrument's snapshot rows sampled after the group's
        `ts_init` are carried with it: they are the only rows that can hold those trades, so a
        crash before the next flush loses both together -- an honest gap -- instead of leaving
        rows whose trades the archive never received (the rebuild would zero them).
        """
        carried_from: dict[str, int] = {}
        batches: list[tuple[tuple[type, str], list[Any]]] = []
        pending = not self._ingest_queue.empty()
        # Trades first: their carry decides what the snapshot batches keep back.
        keys = sorted(self._buffer, key=lambda k: k[0] is not TradeTick)
        for key in keys:
            items = sorted(self._buffer[key], key=lambda d: d.ts_init)
            carry: list[Any] = []
            if key[0] is TradeTick and items and not final:
                items, carry = _split_open_ts_init_group(items, now_ns, pending)
                if carry:
                    carried_from[key[1]] = carry[0].ts_init
            elif key[0] is DydxSecondSnapshot and key[1] in carried_from:
                # By sample time: a venue row's ts_event is its exchange second, not when it was sampled.
                cut = bisect.bisect_right([d.ts_init for d in items], carried_from[key[1]])
                items, carry = items[:cut], items[cut:]
            self._buffer[key] = carry
            if items:
                batches.append((key, items))
        return batches

    def _mark_lost_trades(self, iid: str, lost: list[TradeTick], now_ns: int) -> None:
        """
        Record an archive gap for a trade batch that failed to write while this instrument's
        snapshots (holding those trades) may have landed, so the nightly rebuild keeps those
        rows' live values.
        """
        catalog_path = str(Path(self._config.catalog_path).resolve())
        record_gap(catalog_path, iid, lost[0].ts_init, now_ns, "write_failed", len(lost))

    def _apply_to_candle_store(self, flushed: dict[str, list[DydxSecondSnapshot]]) -> None:
        """
        Fold what just reached Parquet into the candle store, in one transaction.

        Only flushed seconds are applied, so the store is never ahead of the archive. A failure must
        not stop ingestion: it is loud (DATA-07), and the next start's catch-up or `build_candles`
        repairs it.
        """
        try:
            candle_store.apply_batch(self._candle_db, flushed)
        except Exception as e:
            error_ledger.record(
                "collector.candle_store",
                f"candle store write failed for {len(flushed)} instruments",
                e,
            )

    def _catch_up_candle_store(self) -> None:
        """
        Apply, at startup, the seconds the archive holds beyond each instrument's watermark.

        A crash between a Parquet flush and its store write (or a failed store write) leaves the
        store behind the archive, and nothing else would ever fill that hole. A gap wider than a day
        is left to `build_candles` (it would read too much here) and logged.
        """
        catalog_path = str(Path(self._config.catalog_path).resolve())
        now_ns = time.time_ns()
        for iid, mark in candle_store.watermarks(self._candle_db).items():
            if now_ns - mark > _CATCH_UP_MAX_NS:
                logger.warning(
                    f"Candle store for {iid} is more than a day behind: run build_candles"
                )
                continue
            try:
                candle_store.apply_seconds(
                    self._candle_db, iid, query_second_ohlc(catalog_path, iid, mark + 1, now_ns)
                )
            except Exception as e:
                error_ledger.record(
                    "collector.candle_store_catch_up", f"candle store catch-up failed for {iid}", e
                )

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(
                _seconds_until_next_flush(time.time(), self._config.flush_interval_seconds)
            )
            await self._flush_once()
            self._report_stale_trades()

    async def _candle_prune_loop(self) -> None:
        """Hourly: drop 1m/5m bars past their retention (`candle_store.RETAIN_DAYS`); wide bars are kept."""
        while not self._stop.is_set():
            try:
                candle_store.prune(self._candle_db)
            except Exception as e:
                error_ledger.record("collector.candle_store_prune", "candle store prune failed", e)
            await asyncio.sleep(3600)

    # -- sample ------------------------------------------------------------------------------

    async def _sample_tick(
        self, now_ns: int, second: int | None = None
    ) -> list[DydxSecondSnapshot]:
        """
        Run the single write gate (AD-1): validate each book, then build one snapshot that
        feeds the catalog buffer and (via the caller) Redis -- same object, same
        iteration. A rejected instrument is skipped, its trades discarded, the reason logged.

        `second` (venue mode): the exchange second to close; the held deltas are applied up to
        its end first, and its trade bucket is folded.
        """
        if second is not None:
            self._drain_pending_deltas((second + 1) * _S_NS)
        batch: list[DydxSecondSnapshot] = []
        sampled = list(self._instrument_ids())
        for iid in sampled:
            snapshot = await self._sample_instrument(iid, now_ns, second)
            if snapshot is None:
                self._discard_second_accumulators(iid, second)
                continue
            self._buffer[(DydxSecondSnapshot, iid)].append(snapshot)
            batch.append(snapshot)
        self._drop_unsampled_trades(sampled)
        if second is not None:
            self._close_venue_second(second)
        return batch

    async def _sample_instrument(
        self, iid: str, now_ns: int, second: int | None
    ) -> DydxSecondSnapshot | None:
        """One instrument through the gate: its snapshot, or None when the sample is skipped."""
        book = self._live_books.get(iid)
        if book is None:
            await self._handle_missing_book(iid, now_ns)
            return None
        if book.best_bid_price() is None or book.best_ask_price() is None:
            return None
        if await self._handle_crossed_book(iid, book, now_ns):
            return None
        reason = (
            self._stale_reason(iid, now_ns, *self._stale_bounds())
            if second is None
            else self._venue_stale_reason(iid, second, now_ns, *self._stale_bounds())
        )
        if reason:
            logger.warning("Stale book for %s (%s) — skipping snapshot", iid, reason)
            return None
        snapshot = self._build_snapshot(iid, book, now_ns, second)
        self._check_impossible_ohlc(iid, snapshot, now_ns)
        return snapshot

    def _stale_bounds(self) -> tuple[float, float]:
        """(feed_stale_ns, stale_ns)."""
        feed_stale_s = self._config.feed_stale_seconds or self._config.stale_book_seconds
        return feed_stale_s * 1e9, self._config.stale_book_seconds * 1e9

    def _build_snapshot(
        self, iid: str, book: OrderBook, now_ns: int, second: int | None
    ) -> DydxSecondSnapshot:
        if second is None:
            trade_list, ts_event = self._second_trades.pop(iid, []), now_ns
        else:
            trade_list = self._venue_trades.get(iid, {}).pop(second, [])
            ts_event = second * _S_NS + _HALF_S_NS
        bids, asks = book.bids()[:BOOK_DEPTH], book.asks()[:BOOK_DEPTH]
        trades = fold_trades(trade_list).snapshot_values()
        return DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(iid),
            bid_prices=[lv.price.as_double() for lv in bids],
            bid_sizes=[lv.size() for lv in bids],
            ask_prices=[lv.price.as_double() for lv in asks],
            ask_sizes=[lv.size() for lv in asks],
            buy_volume=trades.buy_volume,
            sell_volume=trades.sell_volume,
            buy_count=trades.buy_count,
            sell_count=trades.sell_count,
            open_price=trades.open_price,
            high_price=trades.high_price,
            low_price=trades.low_price,
            close_price=trades.close_price,
            ts_event=ts_event,
            ts_init=now_ns,
        )

    def _check_impossible_ohlc(self, iid: str, snapshot: DydxSecondSnapshot, now_ns: int) -> None:
        if (
            ohlc_outside_book(snapshot)
            and now_ns - self._last_impossible_log_ns.get(iid, 0) >= _IMPOSSIBLE_LOG_EVERY_NS
        ):
            self._last_impossible_log_ns[iid] = now_ns
            # Unreachable after the stale/duplicate trade filters; if it fires, an
            # ingestion bug is writing impossible prices (DATA-02/DATA-06 canary).
            logger.error(
                f"IMPOSSIBLE trade OHLC for {iid}: high={snapshot.high_price} "
                f"low={snapshot.low_price} outside book "
                f"[{min(snapshot.bid_prices)}, {max(snapshot.ask_prices)}]"
            )

    def _close_venue_second(self, second: int) -> None:
        """
        Mark `second` closed. A bucket older than it never reached a live row and is left to the
        rebuild (its trades are archived): counted late after a stall, but on the very first close
        it only holds trades that arrived before the loop started closing -- not transport
        lateness, so they are logged (`_pre_start_trades`) rather than ledgered.
        """
        counts = self._pre_start_trades if self._last_closed_second is None else self._late_trades
        self._last_closed_second = second
        for iid, buckets in self._venue_trades.items():
            for old in [s for s in buckets if s <= second]:
                counts[iid] += len(buckets.pop(old))

    def _drop_unsampled_trades(self, sampled: list[str]) -> None:
        """
        MEM-02: a trade for an instrument this collector does not sample (unsubscribed, or not
        configured) would otherwise sit in `_second_trades` forever. It is still archived.
        """
        for iid in set(self._second_trades) - set(sampled):
            del self._second_trades[iid]
        for iid in set(self._venue_trades) - set(sampled):
            del self._venue_trades[iid]

    def _stale_reason(self, iid: str, now_ns: int, feed_stale_ns: float, stale_ns: float) -> str:
        """
        Why this book must not be sampled ('' = fresh), naming the gap (DATA-01).

        Feed-level silence (no WS message for any instrument: dead socket, or a reconnect that
        has not yet delivered a fresh snapshot) is told apart from one quiet instrument on a
        live feed, whose book is simply unchanged.
        """
        feed_dead = self._feed_dead_reason(now_ns, feed_stale_ns)
        if feed_dead:
            return feed_dead
        book_age_ns = now_ns - self._last_book_update_ns.get(iid, 0)
        if book_age_ns > stale_ns:
            return f"instrument silent: no OrderBookDeltas for {book_age_ns / 1e9:.1f}s, feed alive"
        return ""

    def _feed_dead_reason(self, now_ns: int, feed_stale_ns: float) -> str:
        feed_age_ns = now_ns - self._last_feed_message_ns
        if feed_age_ns > feed_stale_ns:
            return f"feed dead: no WS message of any kind for {feed_age_ns / 1e9:.1f}s"
        return ""

    def _venue_stale_reason(
        self, iid: str, second: int, now_ns: int, feed_stale_ns: float, stale_ns: float
    ) -> str:
        """
        Venue mode: feed liveness is judged on arrival (now), the book's age on exchange time --
        from the last applied delta's `ts_event` to the end of the second being closed.
        """
        feed_dead = self._feed_dead_reason(now_ns, feed_stale_ns)
        if feed_dead:
            return feed_dead
        book_age_ns = (second + 1) * _S_NS - self._book_event_ns.get(iid, 0)
        if book_age_ns > stale_ns:
            return (
                f"instrument silent: no delta stamped in the {book_age_ns / 1e9:.1f}s before the "
                f"end of second {second}, feed alive"
            )
        return ""

    async def _handle_missing_book(self, iid: str, now_ns: int) -> None:
        if iid in self._resync_pending:
            await self._resync(iid)
        if now_ns - self._last_no_book_log_ns.get(iid, 0) >= _IMPOSSIBLE_LOG_EVERY_NS:
            # Rate-limited (one per minute): an instrument that never gets a book (not on
            # the venue, or awaiting a fresh snapshot) must not be a quiet gap (DATA-01) --
            # the watchdog only fires when *all* books are dead.
            self._last_no_book_log_ns[iid] = now_ns
            logger.warning("No book for %s — skipping snapshot", iid)

    async def _second_loop(self) -> None:
        """
        Sample on a drift-free wall-clock schedule (`_next_sample_at`): one sample per interval
        bucket, at mid-interval, so every floor second gets exactly one snapshot row -- the
        nightly rebuild maps trades to rows by `ts_event // 1 s` (audit, sampling drift).
        Venue mode runs `_venue_second_loop` instead.
        """
        if self._venue_time:
            await self._venue_second_loop()
            return
        interval = self._config.snapshot_interval_seconds
        last_tick_s: float | None = None
        while not self._stop.is_set():
            now = time.time()
            await asyncio.sleep(_next_sample_at(now, interval, last_tick_s) - now)
            now_ns = time.time_ns()
            last_tick_s = now_ns / 1e9
            self._warn_if_late(now_ns)
            batch = await self._sample_tick(now_ns)
            if self._redis is not None:
                await _publish_snapshot_batch(self._redis, batch)

    async def _venue_second_loop(self) -> None:
        """
        Close every exchange second at wall `S + 1 + hold_back_seconds`, in order. After a late
        wake-up every overdue second is closed (each drained to its own end), so no floor second
        loses its row to a busy event loop; the lag canary still reports the stall.
        """
        hold_back_s = self._config.hold_back_seconds
        while not self._stop.is_set():
            now = time.time()
            await asyncio.sleep(
                max(0.0, _next_close_at(now, hold_back_s, self._last_closed_second) - now)
            )
            now_ns = time.time_ns()
            due = _due_seconds(now_ns, self._hold_back_ns, self._last_closed_second)
            if not due:
                continue  # woke a hair early
            self._warn_if_late(now_ns)
            for second in due:
                batch = await self._sample_tick(now_ns, second)
                if self._redis is not None:
                    await _publish_snapshot_batch(self._redis, batch)
            self._check_pending_overflow(now_ns)

    def _warn_if_late(self, now_ns: int) -> None:
        """
        `_second_loop` staleness canary (DATA-02): a wake-up far behind schedule means the event
        loop was busy and the crossed-book detection/resync guard was not running.
        """
        if self._last_second_loop_tick_ns is not None:
            expected_ns = int(self._config.snapshot_interval_seconds * 1e9)
            lag_ns = now_ns - self._last_second_loop_tick_ns - expected_ns
            if lag_ns > _SECOND_LOOP_LAG_WARN_NS:
                logger.warning(
                    "_second_loop tick arrived %.1fs late (expected every %.1fs) -- "
                    "event loop was busy; crossed-book detection/resync was not "
                    "running during this gap",
                    lag_ns / 1e9,
                    self._config.snapshot_interval_seconds,
                )
        self._last_second_loop_tick_ns = now_ns

    def _live_top(self, iid: str) -> tuple[list, list] | None:
        book = self._live_books.get(iid)
        if book is None:
            return None
        return (
            [(lv.price.as_double(), lv.size()) for lv in book.bids()[:BOOK_DEPTH]],
            [(lv.price.as_double(), lv.size()) for lv in book.asks()[:BOOK_DEPTH]],
        )

    def _capture_book(self, iid: str) -> None:
        """Record the live top-20 with its alignment keys while a cross-check is armed."""
        captures = self._crosscheck_captures.get(iid)
        book = self._live_books.get(iid)
        top = self._live_top(iid)
        if captures is not None and book is not None and top is not None:
            captures.append((book.sequence, book.ts_last, top))
            self._crosscheck_events[iid].set()

    async def _wait_book_event(self, iid: str, ready: Callable[[], bool], timeout_s: float) -> bool:
        """Wait until `ready()` after an armed book event for `iid`; False when `timeout_s` passes."""
        event = self._crosscheck_events[iid]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while not ready():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except TimeoutError:
                return False
        return True

    @staticmethod
    def _mismatches(live: tuple[list, list], snap: BookSnapshot) -> list[str]:
        def side(name: str, live_side: list, rest_side: list) -> list[str]:
            found = top_levels_mismatch(
                live_side,
                rest_side,
                depth=BOOK_DEPTH,
                price_tolerance_levels=EXACT_PRICE_TOLERANCE_LEVELS,
                size_rel_tolerance=EXACT_SIZE_REL_TOLERANCE,
            )
            return [f"{name} {m}" for m in found]

        return [*side("bids", live[0], snap.bids), *side("asks", live[1], snap.asks)]

    def _align_timeout_s(self) -> float:
        # Venue mode applies a message up to 1 + hold_back after arrival; _VENUE_AHEAD_NS is the
        # bound after which a held message is dropped, so a capture cannot take longer.
        return 1.0 + self._config.hold_back_seconds + _VENUE_AHEAD_NS / 1e9

    async def _align_sequence(
        self, iid: str, captures: list[_Capture], snap: BookSnapshot
    ) -> list[str] | None:
        """
        Bybit: REST `seq` and every WS delta's `sequence` are the same counter. The REST state
        lies between the last capture with `sequence <= seq` and the first with `sequence > seq`,
        so a level is wrong only when it disagrees with *both* brackets. None = not bracketed
        (REST answered from behind every capture, or the stream did not pass `seq` in time).
        """
        seq = snap.sequence
        assert seq is not None
        if not await self._wait_book_event(
            iid, lambda: captures[-1][0] > seq, self._align_timeout_s()
        ):
            return None
        before = next((c for c in reversed(captures) if c[0] <= seq), None)
        if before is None:
            return None
        after = next(c for c in captures if c[0] > seq)
        return persistent(self._mismatches(before[2], snap), self._mismatches(after[2], snap))

    @staticmethod
    def _venue_ms(ts_ns: int) -> int:
        """
        Round a Hyperliquid `ts_event` back to the venue's millisecond: the adapter converts
        the integer-ms `time` through f64 (audit D-62), so the live stamp can sit up to 128 ns off
        the exact multiple that REST's `time * 1e6` gives. Rounding, not flooring: the error is
        two-sided and floor would cross the ms boundary on a negative one.
        """
        return (ts_ns + 500_000) // 1_000_000

    async def _align_ts_event(
        self, iid: str, captures: list[_Capture], snap: BookSnapshot
    ) -> list[str] | None:
        """
        Hyperliquid: every push is a full snapshot stamped with the venue's `time`, and REST
        fetched right after a push answers with the same `time` (18 of 21 measured, D-64) --
        then the two are the same state and must be identical. None = REST answered from a
        later block than any push, or no push with that `time` was applied in time.
        """
        assert snap.ts_event_ns is not None
        ms = self._venue_ms(snap.ts_event_ns)
        if not await self._wait_book_event(
            iid, lambda: self._venue_ms(captures[-1][1]) >= ms, self._align_timeout_s()
        ):
            return None
        match = next((c for c in captures if self._venue_ms(c[1]) == ms), None)
        return None if match is None else self._mismatches(match[2], snap)

    async def _crosscheck_round(self, iid: str) -> tuple[list[str], str] | None:
        """
        One aligned live-vs-REST comparison: (mismatches, alignment key), or None when the
        round could not be aligned and was skipped (never judged against the wall clock).

        Arms capture for `iid` (current state first, then every applied message), waits for the
        next book message to arrive so REST is fetched from the same venue state as the push,
        then aligns on the key the snapshot carries (audit D-64).
        """
        captures: list[_Capture] = []
        self._crosscheck_captures[iid] = captures
        self._crosscheck_arrivals[iid] = 0
        self._crosscheck_events[iid] = asyncio.Event()
        try:
            self._capture_book(iid)
            if not captures or not await self._wait_book_event(
                iid, lambda: self._crosscheck_arrivals[iid] > 0, self._config.stale_book_seconds
            ):
                return None
            snap: BookSnapshot = await self._client.fetch_book_snapshot(iid)
            if snap.sequence is not None:
                found = await self._align_sequence(iid, captures, snap)
                return None if found is None else (found, "sequence")
            if snap.ts_event_ns is not None:
                found = await self._align_ts_event(iid, captures, snap)
                return None if found is None else (found, "ts_event")
            return None
        finally:
            self._crosscheck_captures.pop(iid, None)
            self._crosscheck_arrivals.pop(iid, None)
            self._crosscheck_events.pop(iid, None)

    async def _crosscheck_one(self, iid: str) -> None:
        """
        Diff the live top-20 with a REST snapshot at the same venue state (DATA-02's independent
        source of truth). A sequence-bracketed mismatch is ledgered only when the *same* level is
        still wrong on a second aligned round `_CROSSCHECK_CONFIRM_SECONDS` later (a level that
        changed twice between two WS frames does not repeat; a missed delta does). A time-aligned
        mismatch is ledgered at once: same venue, same `time`, so any difference is a finding.
        """
        if iid not in self._live_books:
            logger.debug("Book cross-check skipped for %s: no live book", iid)
            return
        first = await self._crosscheck_round(iid)
        if first is None:
            self._note_unaligned(iid)
            return
        self._crosscheck_unaligned[iid] = 0
        mismatches, key = first
        confirmed = mismatches
        if mismatches and key == "sequence":
            await asyncio.sleep(_CROSSCHECK_CONFIRM_SECONDS)
            second = await self._crosscheck_round(iid)
            if second is None:
                logger.warning(
                    "Book cross-check %s: mismatch unconfirmed, second round not aligned", iid
                )
                return
            confirmed = persistent(mismatches, second[0])
        if confirmed:
            self._book_crosscheck_mismatches[iid] += 1
            error_ledger.record(
                "collector.book_crosscheck",
                f"{iid} live book != REST snapshot at the same {key} "
                f"(mismatch #{self._book_crosscheck_mismatches[iid]}): " + "; ".join(confirmed[:5]),
            )
        else:
            logger.debug(
                "Book cross-check %s clean (depth %d, %s-aligned, %d unconfirmed)",
                iid,
                BOOK_DEPTH,
                key,
                len(mismatches),
            )

    def _note_unaligned(self, iid: str) -> None:
        self._crosscheck_unaligned[iid] += 1
        n = self._crosscheck_unaligned[iid]
        if n % _CROSSCHECK_UNALIGNED_STREAK == 0:
            error_ledger.record(
                "collector.book_crosscheck_unaligned",
                f"{iid}: {n} consecutive cross-check rounds could not be aligned with the live "
                f"book (REST behind or ahead of the stream, or no book message within the wait): "
                f"the book has gone {n * self._config.book_crosscheck_seconds / 60:.0f} min unverified",
            )

    async def _crosscheck_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.book_crosscheck_seconds)
            for iid in self._instrument_ids():
                try:
                    await self._crosscheck_one(iid)
                except Exception as e:
                    error_ledger.record(
                        "collector.book_crosscheck", f"cross-check failed for {iid}", e
                    )

    # -- trade gap closure (story 22.14) ------------------------------------------------------

    def _baselines(self, feed_name: str) -> dict[str, int]:
        """Return the feed's instruments' current `_last_trade_ts` (those with an archived trade)."""
        return {
            iid: self._last_trade_ts[iid]
            for iid in self._feed_instruments.get(feed_name, set())
            if iid in self._last_trade_ts
        }

    def _schedule_backfill(
        self,
        feed_name: str,
        now_ns: int,
        reason: str,
        earlier: Mapping[str, int] | None = None,
    ) -> None:
        """
        Coalesce a reconnect detection into the feed's one pending backfill (due time kept).

        A new request snapshots the baselines now -- the silence signal fires before the resuming
        message is processed, so nothing post-gap is in them yet. `earlier` lowers them further
        with evidence captured before the resume (the flip's inactive-time snapshot, a replay).
        """
        request = self._backfill_requests.get(feed_name)
        if request is None:
            request = _BackfillRequest(
                now_ns + _BACKFILL_SETTLE_NS, [reason], self._baselines(feed_name)
            )
            self._backfill_requests[feed_name] = request
            logger.info(
                f"Reconnect detected on feed {feed_name} ({reason}): trade backfill scheduled"
            )
        elif reason not in request.reasons:
            request.reasons.append(reason)
        request.lower(earlier or {})

    def _poll_feed_states(self, now_ns: int) -> None:
        """One `feed_states()` poll: an inactive -> active transition is a reconnect."""
        try:
            states = self._client.feed_states()
        except Exception as e:
            if now_ns - self._feed_state_error_ns >= _FEED_STATE_ERROR_EVERY_NS:
                self._feed_state_error_ns = now_ns  # a broken poll must not ledger 10x a second
                error_ledger.record("collector.feed_state", "feed_states() failed", e)
            return
        for feed, active in states.items():
            self._feeds.add(feed)
            previous = self._feed_active.get(feed)
            self._feed_active[feed] = active
            if not active:
                # Nothing arrives while inactive: this is the pre-gap state the flip resumes from.
                self._inactive_baselines.setdefault(feed.name, self._baselines(feed.name))
            elif previous is False and active:
                self._schedule_backfill(
                    feed.name,
                    now_ns,
                    "feed reconnected (inactive -> active)",
                    self._inactive_baselines.pop(feed.name, {}),
                )

    async def _feed_state_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(_FEED_STATE_POLL_SECONDS)
            self._poll_feed_states(time.time_ns())

    async def _trade_backfill_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(_BACKFILL_POLL_SECONDS)
            try:
                await self._run_due_backfills(time.time_ns())
            except Exception as e:  # the loop must survive to serve the next reconnect
                error_ledger.record("collector.trade_backfill", "trade backfill failed", e)

    async def _run_due_backfills(self, now_ns: int) -> None:
        """Run every request whose settle has passed, one at a time (sequential REST)."""
        due = [name for name, r in self._backfill_requests.items() if r.due_ns <= now_ns]
        for feed_name in due:
            request = self._backfill_requests.pop(feed_name)
            await self._run_backfill(feed_name, request)

    async def _run_backfill(self, feed_name: str, request: _BackfillRequest) -> None:
        """Backfill every instrument this feed carried, then ledger exactly one entry."""
        report = _BackfillReport(feed_name, request.reasons)
        wanted = set(self._instrument_ids())
        instruments = sorted(self._feed_instruments.get(feed_name, set()) & wanted)
        report.instruments = len(instruments)
        done = 0
        try:
            for iid in instruments:
                try:
                    await self._backfill_instrument(iid, request.since.get(iid), report)
                except Exception as e:  # one instrument's surprise must not cost the others
                    report.errors[iid] = repr(e)
                done += 1
        finally:
            # Always one entry, also when shutdown cancels the fetch mid-way: what was archived
            # so far is buffered (the final flush writes it) and the rest is named, never silent.
            if done < len(instruments):
                report.reasons = [*report.reasons, f"interrupted after {done} instruments"]
            error_ledger.record("collector.trade_backfill", report.message())

    async def _backfill_instrument(
        self, iid: str, last: int | None, report: _BackfillReport
    ) -> None:
        """`last`: the instrument's pre-gap baseline from the request (None: no archived trade)."""
        if last is None:
            report.no_baseline += 1  # the rebuild's coverage also starts at the first trade
            return
        instrument = self._instruments.get(iid)
        if instrument is None:
            report.errors[iid] = "no instrument definition from the venue"
            return
        fetch_ns = time.time_ns()
        try:
            fetched = await asyncio.to_thread(
                trade_backfill.fetch_trades,
                instrument,
                last - _BACKFILL_LOOKBACK_NS,
                fetch_ns - ARRIVAL_MARGIN_NS,
                self._config.environment,
                fetch_ns,
            )
        except _BACKFILL_FETCH_ERRORS as e:
            report.errors[iid] = repr(e)
            return
        lost_until = self._apply_backfill(iid, fetched.trades, report) or last
        if not fetched.reached_since:
            # Nothing between `last` and the venue's oldest returned trade could be checked.
            lost_until = max(lost_until, fetched.oldest_ns or fetch_ns)
        if lost_until > last:
            report.unrecoverable[iid] = (lost_until - last) / 1e9
        if fetched.rejected:
            report.errors[iid] = (
                f"{len(fetched.rejected)} inexact trade(s) skipped: {fetched.rejected[0]}"
            )

    def _apply_backfill(
        self, iid: str, trades: list[TradeTick], report: _BackfillReport
    ) -> int | None:
        """
        Archive the unseen trades, oldest first -- never into the live second (the nightly
        rebuild places them). `ts_init` is restamped to now: the flush needs every new trade's
        `ts_init` at or after what it already wrote (`_TRADE_CARRY_NS`), and live trades may
        have been flushed while the fetch ran. The arrival bound is checked on that stamp.
        Returns the newest refused trade's `ts_event` (known lost up to there), or None.
        """
        now_ns = time.time_ns()
        newest_refused: int | None = None
        for trade in trades:
            trade_id = str(trade.trade_id)
            if self._first_copy_feed(iid, trade_id) is not None:
                report.already += 1
                continue
            if now_ns - trade.ts_event > ARRIVAL_MARGIN_NS:
                report.refused += 1  # invisible to the rebuild/prune window (archive invariant)
                newest_refused = trade.ts_event  # oldest first: the last one is the newest
                continue
            self._register_trade(iid, trade_id, REST_FEED_NAME)
            self._buffer[(TradeTick, iid)].append(_restamped(trade, now_ns))
            self._advance_last_trade(iid, trade.ts_event)
            self._trade_backfill_counts[iid] += 1
            report.backfilled += 1
        return newest_refused

    def _one_sided_messages(self, now_ns: int, name: str) -> list[str]:
        """
        Per feed group with two or more feeds: a feed is behind when its last trade is more than
        `_ONE_SIDED_NS` older than the group's newest (a feed that never delivered one counts
        from the watchdog's start). Returns the alert transitions' messages.
        """
        groups: defaultdict[str, list[str]] = defaultdict(list)
        for feed in self._feeds:
            groups[feed.group].append(feed.name)
        messages = []
        for group, names in sorted(groups.items()):
            if len(names) < 2:
                continue
            last = {n: self._feed_last_trade_ns.get(n, self._watchdog_started_ns) for n in names}
            newest = max(last.values())
            behind = sorted(n for n in names if newest - last[n] > _ONE_SIDED_NS)
            live = sorted(set(names) - set(behind))
            down_since, reminder = self._one_sided_state.get(group, (None, 0))
            message, down_since, reminder = _alert_transition(
                now_ns,
                bool(behind),
                down_since,
                reminder,
                _one_sided_texts(name, group, behind, live),
            )
            self._one_sided_state[group] = (down_since, reminder)
            if message is not None:
                messages.append(message)
        return messages

    def _book_watchdog_message(self, now_ns: int, name: str) -> str | None:
        live = list(self._instrument_ids())
        if not live:
            return None
        is_stale = all(
            now_ns - self._last_book_update_ns.get(iid, 0) > _WATCHDOG_STALE_NS for iid in live
        )
        message, down_since_ns, reminder_ns = _watchdog_transition(
            now_ns,
            is_stale,
            self._watchdog_down_since_ns,
            self._watchdog_last_reminder_ns,
            name,
        )
        self._watchdog_down_since_ns = down_since_ns
        self._watchdog_last_reminder_ns = reminder_ns
        return message

    async def _watchdog_loop(self) -> None:
        """OBS-01: page someone when every book has gone stale, or one feed of a group has."""
        name = type(self._client).__name__
        while not self._stop.is_set():
            await asyncio.sleep(_WATCHDOG_CHECK_SECONDS)
            now_ns = time.time_ns()
            if now_ns - self._watchdog_started_ns < _WATCHDOG_STARTUP_GRACE_NS:
                continue
            book = self._book_watchdog_message(now_ns, name)
            for message in ([book] if book else []) + self._one_sided_messages(now_ns, name):
                await asyncio.to_thread(_notify, message, name)

    # -- lifecycle ---------------------------------------------------------------------------

    async def run(self) -> None:
        # Bounded socket timeouts: a black-holed Redis must not stall _second_loop's publish.
        self._redis = aioredis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"),
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )

        instruments = await self._client.fetch_instruments()
        by_id = {i.id.value: i for i in instruments}
        converted = instruments_from_pyo3(list(by_id.values()))
        self._instruments = {str(i.id): i for i in converted}  # the trade backfill's precisions
        self._catalog.write_data(converted)

        await self._connect(list(by_id.values()))
        if hasattr(self._client, "subscribe_global"):
            await self._client.subscribe_global()
        configured = set(self._instrument_ids())
        unknown = configured - set(by_id)
        if unknown:
            logger.warning(
                "Configured instruments not found on the venue, skipping: %s", sorted(unknown)
            )
        self._catch_up_candle_store()
        subscribed = sorted(configured & set(by_id))
        for iid in subscribed:
            await self._client.subscribe(iid)
            logger.info(f"Subscribed {iid}")
        logger.info(f"Started: {len(subscribed)} subscribed")

        loops: tuple[Callable[[], Awaitable[None]], ...] = (
            self._ingest_loop,
            self._flush_loop,
            self._candle_prune_loop,
            self._second_loop,
            self._watchdog_loop,
            self._trade_backfill_loop,
            *((self._feed_state_loop,) if hasattr(self._client, "feed_states") else ()),
            *(
                (self._crosscheck_loop,)
                if self._config.book_crosscheck_seconds > 0
                and hasattr(self._client, "fetch_book_snapshot")
                else ()
            ),
            *self._extra_loops,
        )
        # ensure_future (not create_task): extra_loops are typed as Awaitable, not Coroutine.
        tasks: list[asyncio.Future[Any]] = [asyncio.ensure_future(loop()) for loop in loops]
        stop_task: asyncio.Future[Any] = asyncio.ensure_future(self._stop.wait())
        try:
            # A loop dying is an unexpected bug: surface it so run_forever() does a clean restart.
            done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stop_task:
                    task.result()
        finally:
            stop_task.cancel()
            for task in tasks:
                task.cancel()
            # Let every loop unwind first: an interrupted backfill ledgers itself and leaves what
            # it archived in the buffer, which the final flush below then writes.
            await asyncio.gather(*tasks, return_exceptions=True)
            self._ledger_abandoned_backfills()
            await self._disconnect()
            await self._flush_once(final=True)
            self._report_stale_trades()
            await self._redis.aclose()

    async def _connect(self, instruments: list) -> None:
        """
        Connect the client; on failure close what did connect (a client may own several
        sockets), or the Rust clients keep reconnecting and feeding a collector that is gone.
        """
        try:
            await self._client.connect(asyncio.get_running_loop(), instruments)
        except BaseException:
            await self._disconnect()
            raise

    async def _disconnect(self) -> None:
        try:
            await self._client.disconnect()
        except Exception as e:  # never skip the final flush over a closing WS
            error_ledger.record("collector.disconnect", "disconnect failed", e)

    def _ledger_abandoned_backfills(self) -> None:
        """Ledger every reconnect detected but not yet backfilled at shutdown (a named gap, DATA-05)."""
        for feed_name, request in sorted(self._backfill_requests.items()):
            error_ledger.record(
                "collector.trade_backfill",
                f"feed {feed_name} ({'; '.join(request.reasons)}): abandoned at shutdown, "
                f"{len(request.since)} instruments not fetched",
            )
        self._backfill_requests.clear()

    def stop(self) -> None:
        self._stop.set()


async def run_forever(build: Callable[[], Collector], *, init_rust_logging: bool = True) -> None:
    """
    Process entrypoint: build a collector per attempt, restart with backoff on crash, stop
    cleanly on SIGINT/SIGTERM. `init_rust_logging=False` is for an entrypoint that installs
    its own richer `init_logging` first (dYdX's WS_RAW file sink, story 22.2).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Rust's `log` crate is a no-op until a logger is installed: without this every
    # `log::warn!`/`error!` inside the Rust WS client (including a failed
    # call_soon_threadsafe, i.e. a message silently never reaching `_on_data`) is invisible.
    # The returned LogGuard MUST stay referenced for this coroutine's whole lifetime --
    # dropping the last guard shuts Rust logging down (see dydx_collector/collector.py main()).
    _log_guard = (
        nautilus_pyo3.init_logging(
            trader_id=nautilus_pyo3.TraderId("COLLECTOR-001"),
            instance_id=nautilus_pyo3.UUID4(),
            level_stdout=nautilus_pyo3.LogLevel.WARNING,
        )
        if init_rust_logging
        else None
    )

    shutting_down = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutting_down.set)
    loop.add_signal_handler(signal.SIGTERM, shutting_down.set)

    backoff_seconds = 1.0
    first = True
    while not shutting_down.is_set():
        collector = build()
        if first:
            quarantine_corrupt_parquet(collector._config.catalog_path, collector._instrument_ids())
            first = False
        watcher = asyncio.create_task(_stop_on_shutdown(shutting_down, collector))
        try:
            await collector.run()
            backoff_seconds = 1.0  # clean stop (signal) -- reset for any future crash
        except Exception:
            logger.exception(f"Collector crashed, restarting in {backoff_seconds:.0f}s")
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60.0)
        finally:
            watcher.cancel()
    del _log_guard


async def _stop_on_shutdown(shutting_down: asyncio.Event, collector: Collector) -> None:
    await shutting_down.wait()
    collector.stop()
