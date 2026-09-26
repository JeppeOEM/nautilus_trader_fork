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
Venue-neutral market-data collector (Story 22.1): capture's application service. It owns the
asyncio loops (ingest, flush, sample, watchdog, feed states, trade backfill, subscription retry,
REST cross-check), executes what the domain decides and does all of capture's I/O through the
ports. No TradingNode/Strategy/DataEngine.

Since Story 26.1 the gate's state and rules live in explicit aggregates (`collector_core/domain/`,
DDD spine AD-D6): per applied instrument a `LiveBook` (the Nautilus `OrderBook`, crossed-since,
resync-pending, level tags, last `u`, venue-mode pending deltas) and a `TradeIntake` (the bounded
`trade_id` window, per-feed first-copy arbitration, the live second's trades, every trade
counter); per venue a `FeedGroup` (feed liveness, reconnect evidence, backfill requests,
arbitration counters); and one pure `SecondSampler`, the only place the four gate checks run
(missing, empty top, crossed, stale). Venue variance is a `CapturePolicies` value
(`CrossedBookPolicy`, `LevelTagger`, `SequenceCanary`), pure and synchronous: a venue subclass
overrides nothing but `__init__`. This class executes every `ResyncRequested` after the sample
(`LiveBook.resync()`, then `VenueFeed.resync_orderbook`), and is capture's only error-ledger
caller (`_ledger`; every site in `collector_core.sites`). The client contract is
`collector_core.ports.VenueFeed`; the Parquet archive is an `ArchiveWriter`, the `snapshots:raw`
publish a `LiveStream`, the reconnect backfill's REST a `VenueTradeHistory`, the operator push a
`Notifier`, the candle store a `SecondSink` -- each injected by the venue's composition root.

Guards (behaviour changes are listed on audit D-66/D-67): stale-trade age filter + bounded trade_id dedup (DATA-06),
stale-book skip with accumulator discard (DATA-01), crossed-book skip with resync as a fallback
only (DATA-03), the empty-top rejection (now a rate-limited warning + `collector.empty_top`),
the `ohlc_outside_book` canary, the `_second_loop` lag canary and the OBS-01 watchdog.

Trades (story 22.13): every accepted `TradeTick` is both kept for the live second (folded once
per sample by `kernel.fold.fold_trades`) and archived raw to `data/trade_tick/<iid>/` with both
clocks untouched (`ts_event` = venue, `ts_init` = arrival). The live snapshot is provisional and
arrival-timed; `archive.rebuild_seconds` re-derives closed days from the archive on exchange time.
`run_forever` holds the venue's capture lock for the process lifetime.

Two clocks per mode (story 22.12, `CoreConfig.book_time_source`):
  * "arrival" (dYdX -- its book deltas carry no venue timestamp, D-49): a row is sampled at
    mid-second from the book as received and holds the trades that *arrived* since the last
    row; `ts_event == ts_init` = the sample time.
  * "venue" (Bybit, Hyperliquid): exchange second S is closed at wall S + 1 + hold_back_seconds.
    Deltas are held in `ts_event` order and applied up to S+1 (`LiveBook.drain`), trades are
    bucketed by `ts_event // 1 s`; a trade processed after its second closed is archived and
    counted (`collector.late_trade`) but never folded live -- the nightly rebuild places it. The
    row's `ts_event` is S + 0.5 s and `ts_init` is when it was actually sampled.

`on_data(data, feed)` is O(1): it only enqueues, `_ingest_loop` does the real work, tagging each
message with the connection (`Feed`) it came on (a one-connection client may omit it: `MAIN_FEED`).

Trade gap closure (story 22.14). The Rust WS clients reconnect and resubscribe silently, so a
reconnect is detected per feed on evidence: `feed_states()` inactive -> active (Bybit/Hyperliquid;
dYdX's `is_connected()` stays true through a reconnect); a book feed silent past `feed_stale_seconds
or stale_book_seconds` that speaks again; the same feed re-delivering a `trade_id` after the startup
grace. Detections coalesce into one `FeedGroup` request per feed, run `_BACKFILL_SETTLE_NS` after the
first: for every instrument that feed carried, the venue's `VenueTradeHistory` reads `[last archived
ts_event - 5 s, now]` and only unseen ids are archived (`ts_init` = archive time), never into the
live second. One `collector.trade_backfill` ledger entry per backfill states what was recovered,
already archived, refused, unrecoverable and failed. Dual feed (`trade_feeds = 2`): both deliver
into one `TradeIntake`, whose window remembers the first copy's feed (`duplicate` vs
`duplicate_feed`); per-feed arbitration is logged each flush and a feed 30 s behind its group's
sibling raises an OBS-01 one-sided-outage notification.

Known limit: a crash or restart gap is not backfilled, because `TradeIntake.last_trade_ts` lives in
memory. Upgrade path: seed it from the newest archived trade per instrument at startup.
Known limit: seconds the stale-book gate skipped during an outage have no snapshot row, so their
backfilled trades are rebuild orphans and those minutes can still mismatch the venue's klines.
Known limit: a backfill never archives a trade older than `MAX_TS_INIT_SKEW_NS` (the rebuild's and
prune's `ts_init` window), so a dYdX outage longer than 5 minutes stays partly unrecovered and is
reported as such. Upgrade path: a backfill-span marker the rebuild and prune read.
Known limit: the one-sided alert compares trade arrival within a group only; a group where every
feed is silent is the book watchdog's case.
Known limit: a backfill fetches its instruments one after another, so on a long dYdX list the last
instruments get a little less of the 5-minute window and one slow backfill delays the next feed's.
Upgrade path: a small bounded fetch pool.

Snapshots are `DydxSecondSnapshot` (a venue-neutral schema despite its name -- moved in story 22.3)
so data_api serves every venue's ids with zero per-route code.
"""

import asyncio
import functools
import http.client
import importlib
import json
import logging
import math
import signal
import time
import warnings
from collections import Counter
from collections import defaultdict
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from typing import ClassVar

from kernel.catalog_files import query_second_ohlc
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import BOOK_DEPTH
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import ohlc_outside_book
from observability import error_ledger
from observability import notify
from observability import watchdog

from collector_core import sites
from collector_core.application.trade_backfill import BACKFILL_LOOKBACK_NS
from collector_core.application.trade_backfill import BackfillReport
from collector_core.application.trade_backfill import admit_backfill
from collector_core.book_check import EXACT_PRICE_TOLERANCE_LEVELS
from collector_core.book_check import EXACT_SIZE_REL_TOLERANCE
from collector_core.book_check import BookSnapshot
from collector_core.book_check import persistent
from collector_core.book_check import top_levels_mismatch
from collector_core.config import CoreConfig
from collector_core.domain.events import BookUncrossed
from collector_core.domain.events import SequenceBroken
from collector_core.domain.feed_group import MAIN_FEED
from collector_core.domain.feed_group import BackfillRequest
from collector_core.domain.feed_group import Feed
from collector_core.domain.feed_group import FeedGroup
from collector_core.domain.flush_batch import FlushBatch
from collector_core.domain.live_book import S_NS
from collector_core.domain.live_book import VENUE_AHEAD_NS
from collector_core.domain.live_book import LiveBook
from collector_core.domain.policies import CapturePolicies
from collector_core.domain.policies import CentralBookCrossPolicy
from collector_core.domain.sampler import SecondSampler
from collector_core.domain.trade_history import BackfillError
from collector_core.domain.trade_intake import ACCEPTED
from collector_core.domain.trade_intake import DUPLICATE_FEED_FOLD
from collector_core.domain.trade_intake import REPLAY
from collector_core.domain.trade_intake import STALE
from collector_core.domain.trade_intake import TradeIntake
from collector_core.domain.verdicts import Crossed
from collector_core.domain.verdicts import DroppedLevel
from collector_core.domain.verdicts import EmptyTop
from collector_core.domain.verdicts import NoBook
from collector_core.domain.verdicts import Rejected
from collector_core.domain.verdicts import Stale
from collector_core.ports import Applied
from collector_core.ports import ArchiveWriter
from collector_core.ports import CaptureStatus
from collector_core.ports import LiveStream
from collector_core.ports import Notifier
from collector_core.ports import PlanChange
from collector_core.ports import PlanDiff
from collector_core.ports import SecondSink
from collector_core.ports import VenueTradeHistory
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.instruments import instruments_from_pyo3


logger = logging.getLogger(__name__)
# A steady-state crossed book forced into the DATA-03 fallback: a distinct logger (not a new file
# or volume) so the escalation is separable from routine WARNING lines; the incident rules match
# its `steady_state_crossed_book` JSON reason.
critical_logger = logging.getLogger("collector_core.critical")

# Moved names (Story 26.1): served from their new home with a DeprecationWarning until then.
MOVED_NAMES_REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"
_MOVED_NAMES: dict[str, str] = {
    "quarantine_corrupt_parquet": (
        "collector_core.infrastructure.parquet_writer.quarantine_corrupt_parquet"
    ),
}


def __getattr__(name: str) -> object:
    if name in _MOVED_NAMES:
        warnings.warn(
            f"collector_core.collector.{name} moved to {_MOVED_NAMES[name]} (Story 26.1); "
            f"it is served here until {MOVED_NAMES_REMOVE_AFTER}",
            DeprecationWarning,
            stacklevel=2,
        )
        # A literal module name, so `platform/tests/test_images.py` follows it into the image check.
        module = importlib.import_module("collector_core.infrastructure.parquet_writer")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_INGEST_YIELD_EVERY = 64
_IMPOSSIBLE_LOG_EVERY_NS = 60_000_000_000  # one line per instrument per minute, not per second

# OBS-01: zero book updates across all instruments for 30s+ is a pipeline failure, not a
# quiet market. Deployed unattended, so this pushes a notification rather than relying on
# someone noticing a frozen chart.
_WATCHDOG_CHECK_SECONDS: float = 30.0
_WATCHDOG_STALE_NS: int = 30_000_000_000
_WATCHDOG_STARTUP_GRACE_NS: int = 60_000_000_000  # subscriptions need time to establish
_WATCHDOG_REMINDER_NS: int = watchdog.DEFAULT_REMINDER_NS  # re-notify at most every 10 min
# One-sided outage (story 22.14): a feed whose last trade is this far behind a sibling's in the
# same group has lost its connection while the other kept delivering.
_ONE_SIDED_NS: int = 30_000_000_000

# Trade backfill (story 22.14). The settle lets a reconnect's resubscribes and replays land first,
# so one reconnect gets one backfill.
_BACKFILL_SETTLE_NS: int = 3_000_000_000
_BACKFILL_POLL_SECONDS: float = 0.5
# Faster than the Rust clients' 250 ms minimum reconnect delay, so an inactive spell is seen.
_FEED_STATE_POLL_SECONDS: float = 0.1
_FEED_STATE_ERROR_EVERY_NS: int = 60_000_000_000
# Every failure one instrument's fetch can raise (urllib's URLError is an OSError): named under
# that instrument in the backfill's entry, the other instruments continue.
_BACKFILL_FETCH_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    http.client.HTTPException,
    json.JSONDecodeError,
    BackfillError,
)

# Venue mode: after a stall, at most this many overdue seconds are closed in one wake-up.
# Known limit: a longer stall (host suspend) leaves the older seconds without a live row; their
# trades are counted late and placed by the nightly rebuild. Upgrade path: close them from the
# archive instead of from memory. Kept well under `kernel.clocks.READ_SPAN_MARGIN_NS` (60 s): every
# caught-up row gets the wake-up's `ts_init`, so its `ts_init` trails its `ts_event` by up to
# this + 1 + hold_back seconds, and readers only widen file spans by that margin.
_MAX_CATCH_UP_SECONDS = 30


def _check_skew_budget(hold_back_ns: int) -> None:
    """
    Refuse a hold-back whose rows' `ts_init` could sit further from their `ts_event` than the
    readers widen a file span (`READ_SPAN_MARGIN_NS`): those rows would be silently skipped by
    every reader. The skew has two independent directions -- a caught-up row trails by up to
    catch-up + 1 s + hold-back, and a venue clock runs ahead by up to hold-back +
    `VENUE_AHEAD_NS` -- and the widening is symmetric, so each alone is the binding limit; the
    sum is checked as a deliberately conservative ceiling on both. Checked at construction because
    deployed configs are bind-mounted, so the committed `config.toml`s that
    `platform/tests/test_skew_constants.py` reads are not the whole story.
    """
    worst = (_MAX_CATCH_UP_SECONDS + 1) * NS_PER_S + hold_back_ns + VENUE_AHEAD_NS
    if worst > READ_SPAN_MARGIN_NS:
        raise ValueError(
            f"hold_back_seconds {hold_back_ns / NS_PER_S} is too large: a row's ts_init could sit "
            f"up to {worst / NS_PER_S} s from its ts_event (trailing plus leading skew), beyond the "
            f"{READ_SPAN_MARGIN_NS // NS_PER_S} s the catalog readers widen a file span by "
            "(kernel.clocks.READ_SPAN_MARGIN_NS)"
        )


# _second_loop staleness canary: if its wakeup arrives this much later than the configured
# interval, the event loop was busy and the crossed-book detection/resync guard was silently
# not running for that gap -- surface it rather than let it look like a quiet market.
_SECOND_LOOP_LAG_WARN_NS: int = 2_000_000_000


def _watchdog_transition(
    now_ns: int,
    is_stale: bool,
    down_since_ns: int | None,
    last_reminder_ns: int,
    name: str = "collector",
) -> tuple[str | None, int | None, int]:
    """Step the every-book-stale watchdog (the generic `observability.watchdog.transition`)."""
    texts = watchdog.AlertTexts(
        down=(
            f"{name}: all live instruments' order books have gone stale "
            "(no OrderBookDeltas for 30s+) — feed may be down"
        ),
        still=f"{name}: still down, no book updates for {{down_for_s:.0f}}s",
        recovered=f"{name}: recovered after {{down_for_s:.0f}}s",
    )
    return watchdog.transition(
        now_ns, is_stale, down_since_ns, last_reminder_ns, texts, _WATCHDOG_REMINDER_NS
    )


def _one_sided_texts(
    name: str, group: str, behind: list[str], live: list[str]
) -> watchdog.AlertTexts:
    return watchdog.AlertTexts(
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


# Parquet is flushed this many seconds past each interval boundary (:02 for the default 60 s), so a
# minute that just closed is in the archive -- and, right after it, in the second sink -- ~2 s later.
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


def _due_seconds(now_ns: int, hold_back_ns: int, last_closed: int | None) -> range:
    """
    Exchange seconds whose close time (`S + 1 + hold_back`) has passed and that are not closed
    yet, oldest first; the first call closes only the latest one.
    """
    latest = (now_ns - hold_back_ns) // S_NS - 1
    first = latest if last_closed is None else last_closed + 1
    return range(max(first, latest - _MAX_CATCH_UP_SECONDS + 1), latest + 1)


def _next_close_at(now: float, hold_back_s: float, last_closed: int | None) -> float:
    """Wall time (epoch s) at which the next unclosed exchange second is due; may be <= `now`."""
    second = math.floor(now - hold_back_s) if last_closed is None else last_closed + 1
    return second + 1 + hold_back_s


_CROSSCHECK_CONFIRM_SECONDS = 2.0  # gap before re-comparing a sequence-aligned mismatch (22.5)
# Consecutive rounds that could not be aligned before the ledger says so: an hour at the 300 s
# default. Unaligned rounds are skipped, never judged against the wall clock (audit D-64).
_CROSSCHECK_UNALIGNED_STREAK = 12
# A live capture: (OrderBook.sequence, OrderBook.ts_last, (bids, asks) top-20) after one apply.
_Capture = tuple[int, int, tuple[list, list]]


def _price_text(value: float | None) -> str:
    return "none" if value is None else f"{value:.6f}"


class Collector:
    """
    One venue's collector, capture's application service (see the module docstring).

    `config` thresholds; `client` the `VenueFeed`; `extra_loops` no-arg coroutine functions
    started as tasks alongside the core loops (e.g. a REST open-interest poll). The composition
    root injects `archive` (`ArchiveWriter`), `live_stream` (`LiveStream`; None only in a capture
    test), `second_sink` (the candle store; None only in a capture test, then
    `collector.no_second_sink` is ledgered at start), `policies` (the venue's `CapturePolicies`),
    `trade_history` (`VenueTradeHistory`; without one a backfill names the gap per instrument) and
    `notifier` (`observability.notify` by default).

    `VENUE` is the `kernel.venues` code each venue subclass declares; `run_forever` takes that
    venue's capture lock with it.

    `plan` is the collection plan's ids (spine AD-D17: the plan is the intent). Nothing is
    subscribed until `run()` applies them through `apply`, and from then on the applied set is the
    fact: the sampler, the watchdog, the cross-check and the trade backfill iterate
    `applied & plan` (`_instrument_ids`), and a book or trade message of any other instrument is
    counted (`collector.unplanned_message`), never booked, folded or archived. A `LiveBook` exists
    only for such a collected instrument: created by its first message, reset when the id leaves
    the plan and dropped once its counters are reported. A subscribe or unsubscribe that fails on
    the wire is ledgered and retried by `_subscription_retry_loop`. `store_deltas` are the ids whose
    raw `OrderBookDeltas` are archived (dYdX's `store_order_book_deltas`), replaced by every `apply`.
    Known limit: mark/index/funding/open-interest data is archived for every instrument it arrives
    for (venue-wide channels and polls carry every market), so an id whose unsubscribe failed keeps
    its per-instrument ticker data archived until the retry succeeds: the client does not tag a
    message with the subscription that produced it. Upgrade path: provenance-tagged messages.
    """

    VENUE: ClassVar[str]
    # How often `_subscription_retry_loop` retries the wire-failed subscribes and unsubscribes.
    _SUBSCRIBE_RETRY_SECONDS: ClassVar[float] = 30.0

    def __init__(
        self,
        config: CoreConfig,
        client: Any,
        extra_loops: tuple[Callable[[], Awaitable[None]], ...] = (),
        *,
        plan: Iterable[str],
        archive: ArchiveWriter,
        live_stream: LiveStream | None,
        second_sink: SecondSink | None = None,
        policies: CapturePolicies | None = None,
        trade_history: VenueTradeHistory | None = None,
        notifier: Notifier = notify,
        store_deltas: Iterable[str] = (),
    ) -> None:
        self._config = config
        self._client = client
        self._extra_loops = extra_loops
        self._archive = archive
        self._live_stream = live_stream
        self._second_sink = second_sink
        self._trade_history = trade_history
        self._notifier = notifier
        # AD-D17: `_plan_ids` mirrors the plan (the intent), `_applied` what the wire accepted (the
        # fact). An id stays applied after a failed unsubscribe until the retry succeeds, so the
        # two are intersected wherever capture reads "collected".
        self._plan_ids: set[str] = set(plan)
        self._applied: set[str] = set()
        self._retry_subscribe: set[str] = set()
        self._retry_unsubscribe: set[str] = set()
        self._delta_store: set[str] = set(store_deltas)
        self._unplanned_messages: defaultdict[str, int] = defaultdict(int)
        # Serializes every wire change (`apply`, the retry round and a resync): without it a retry
        # awaiting one subscribe could subscribe an id a concurrent `apply` just removed, leaking a
        # wire subscription nothing tracks, or unsubscribe an id `apply` just re-added.
        self._subscription_lock = asyncio.Lock()
        # The ids `fetch_instruments` returned at `run()`; None before it (then every id is tried).
        # Known limit: fetched once per run, so a market the venue lists after startup stays
        # pending until the next restart -- the WS client is also connected with that instrument
        # list (dYdX parses by it), so a refetch alone would not be enough. Upgrade path: on an
        # unlisted add, refetch the instruments and reconnect the client with the new list.
        self._listed: frozenset[str] | None = None

        self._buffer = FlushBatch()
        # Unbounded: a real overflow would mean the process can't keep up with the
        # exchange at all -- revisit with a maxsize + drop policy only if observed.
        self._ingest_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._stop = asyncio.Event()

        # -- the aggregates (spine AD-D6) and the venue's policies ------------------------------
        policies = policies or CapturePolicies()
        self._tagger = policies.tagger
        self._canary = policies.canary
        self._crossed_policy = policies.crossed or CentralBookCrossPolicy(
            int(config.crossed_resync_seconds * 1e9), hasattr(client, "resync_orderbook")
        )
        self._books: dict[str, LiveBook] = {}
        self._intakes: dict[str, TradeIntake] = {}
        feed_stale_s = config.feed_stale_seconds or config.stale_book_seconds
        self._feed_stale_ns = feed_stale_s * 1e9
        self._feeds = FeedGroup(self._feed_stale_ns, _BACKFILL_SETTLE_NS)
        self._venue_time = config.book_time_source == "venue"
        self._hold_back_ns = int(config.hold_back_seconds * 1e9)
        _check_skew_budget(self._hold_back_ns)
        # Venue mode: a trade stamped this far past its arrival is `ahead` (MEM-02); None = arrival.
        self._ahead_ns = self._hold_back_ns + VENUE_AHEAD_NS if self._venue_time else None
        self._stale_trade_ns = config.stale_trade_seconds * 1e9
        self._sampler = SecondSampler(
            BOOK_DEPTH,
            config.stale_book_seconds * 1e9,
            self._crossed_policy,
            config.book_time_source,
        )
        self._last_closed_second: int | None = None

        self._last_no_book_log_ns: dict[str, int] = {}
        self._last_empty_top_log_ns: dict[str, int] = {}
        self._last_impossible_log_ns: dict[str, int] = {}
        self._book_sequence_errors: defaultdict[str, int] = defaultdict(int)
        self._book_crosscheck_mismatches: defaultdict[str, int] = defaultdict(int)
        # Armed cross-checks only (audit D-64): captures of the live top-20 per applied message,
        # the message-arrival count since arming, and the event both bump. Empty otherwise, so
        # the hot path pays one dict lookup per book message.
        self._crosscheck_captures: dict[str, list[_Capture]] = {}
        self._crosscheck_arrivals: dict[str, int] = {}
        self._crosscheck_events: dict[str, asyncio.Event] = {}
        self._crosscheck_unaligned: defaultdict[str, int] = defaultdict(int)

        self._last_second_loop_tick_ns: int | None = None
        if config.snapshot_interval_seconds != 1.0:
            # Not refused (config validation is unchanged), but loud: the nightly rebuild maps
            # trades to rows by floor second, which assumes one row per second.
            self._ledger(
                sites.CADENCE,
                f"snapshot_interval_seconds={config.snapshot_interval_seconds}, not 1.0: "
                "rebuild_seconds' floor-second mapping assumes 1 s rows",
            )

        self._watchdog_started_ns: int = time.time_ns()
        self._watchdog_down_since_ns: int | None = None
        self._watchdog_last_reminder_ns: int = 0
        self._instruments: dict[str, Instrument] = {}  # Cython instruments, kept by run()
        self._feed_state_error_ns: int = 0
        self._one_sided_state: dict[str, tuple[int | None, int]] = {}

    # -- the one ledger caller -----------------------------------------------------------------

    def _ledger(self, site: str, detail: str = "", exc: BaseException | None = None) -> None:
        """Record on the error ledger: capture's only call to it (DATA-07); `site` is a `sites` constant."""
        error_ledger.record(site, detail, exc)

    # -- the collected set and its aggregates -------------------------------------------------

    def _instrument_ids(self) -> list[str]:
        """Return the collected instruments: planned *and* subscribed on the wire (AD-D17)."""
        return sorted(self._applied & self._plan_ids)

    def _is_collected(self, iid: str) -> bool:
        return iid in self._applied and iid in self._plan_ids

    def _store_deltas(self) -> frozenset[str]:
        """Return the ids whose raw deltas are archived, for `run()`'s initial apply."""
        return frozenset(self._delta_store)

    def _book(self, iid: str) -> LiveBook:
        book = self._books.get(iid)
        if book is None:
            book = self._books[iid] = LiveBook(self._tagger, self._canary)
        return book

    def _intake(self, iid: str) -> TradeIntake:
        intake = self._intakes.get(iid)
        if intake is None:
            intake = self._intakes[iid] = TradeIntake(self._config.seen_trade_ids)
        return intake

    def _clear_book_state(self, iid: str) -> None:
        """Drop the instrument's book and its tracking state (resync, a failed subscribe)."""
        book = self._books.get(iid)
        if book is not None:
            book.clear()

    def _resync_pending(self) -> set[str]:
        """Return the ids whose forced resync is queued (a failed or lock-deferred one, a canary's)."""
        return {iid for iid, book in self._books.items() if book.resync_pending}

    def _apply_deltas(self, iid: str, deltas: OrderBookDeltas) -> None:
        """Apply one message to the instrument's `LiveBook` now (arrival mode's path)."""
        self._apply_book(iid, self._book(iid), deltas, time.time_ns())

    def _apply_book(self, iid: str, book: LiveBook, deltas: OrderBookDeltas, now_ns: int) -> None:
        broken = book.apply(deltas, now_ns)
        if broken is not None:
            self._on_sequence_broken(iid, broken)
        if iid in self._crosscheck_captures and book.book is not None and deltas.deltas:
            self._capture_book(iid)

    def _on_sequence_broken(self, iid: str, broken: SequenceBroken) -> None:
        """DATA-08: a `SequenceCanary` break dropped the message and the book; a resync is queued."""
        self._book_sequence_errors[iid] += 1
        detail = (
            f"u={broken.u} <= last={broken.last} (replayed/reordered)"
            if broken.verdict == "regress"
            else f"u={broken.u} skips {broken.u - (broken.last or 0) - 1} message(s) after "
            f"last={broken.last}"
        )
        self._ledger(
            sites.BOOK_SEQUENCE,
            f"{iid} {detail} (#{self._book_sequence_errors[iid]}): book dropped, resync queued",
        )

    async def _resync(self, iid: str) -> None:
        """
        Execute a `ResyncRequested` (DATA-03 fallback): drop the local book first, then ask the
        venue for a fresh snapshot; retried on failure. Local state goes first on purpose: the
        fresh snapshot can be booked by the ingest loop while the wire call is awaited, and
        clearing after it would wipe that snapshot.

        The wire half runs under `_subscription_lock`: a resync is an unsubscribe plus a
        subscribe, so interleaved with `apply` removing the id it would re-subscribe a book
        nothing tracks. While a wire change holds the lock the resync is queued on the `LiveBook`
        for the next sample instead, so the sampler never stalls behind an `apply`.
        """
        book = self._books.get(iid)
        if not hasattr(self._client, "resync_orderbook"):
            # A full-snapshot venue (no `resync_orderbook`, DATA-08): its next message is the fresh
            # baseline, so there is nothing to ask the wire for -- never a failing retry loop.
            if book is not None:
                book.resync_pending = False
            return
        logger.warning("Resyncing desynced order book for %s", iid)
        if book is not None:
            book.resync()
        if self._subscription_lock.locked():
            if self._is_collected(iid):
                self._book(iid).resync_pending = True
            return
        async with self._subscription_lock:
            await self._resync_wire(iid)

    async def _resync_wire(self, iid: str) -> None:
        """Run the wire half of `_resync`; the caller holds `_subscription_lock`."""
        if not self._is_collected(iid):
            # Removed while the resync was queued: its subscription is `apply`'s to end.
            book = self._books.get(iid)
            if book is not None:
                book.resync_pending = False
            return
        try:
            await self._client.resync_orderbook(iid)
            self._book(iid).resync_pending = False
        except Exception as e:
            # The unsubscribe half may have gone through: retry from the next sample rather
            # than leave the instrument bookless forever. A snapshot booked during the await
            # already rebuilt the book, and the flag must not outlive it (see `LiveBook.apply`).
            live = self._book(iid)
            live.resync_pending = live.book is None
            self._ledger(sites.RESYNC, f"resync failed for {iid}, retrying", e)

    # -- ingest ------------------------------------------------------------------------------

    def _on_data(self, data: Any, feed: Feed = MAIN_FEED) -> None:
        # Runs on the event loop from the Rust callback: O(1) only, _ingest_loop does the work.
        try:
            self._ingest_queue.put_nowait((data, feed))
        except Exception as e:
            self._ledger(sites.ENQUEUE, f"failed to enqueue {type(data).__name__}, DROPPED", e)

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
                self._ledger(sites.PROCESS, f"failed to process {type(data).__name__}, DROPPED", e)
            processed += 1
            if processed % _INGEST_YIELD_EVERY == 0:
                await asyncio.sleep(0)

    def _process_data(self, data: Any, feed: Feed = MAIN_FEED) -> None:
        now_ns = time.time_ns()
        # Feed liveness runs on arrival (the Rust client's `ts_init` at receipt), not on when the
        # ingest loop gets to the message: a queue backlog or loop stall is not a silent socket.
        arrival_ns = getattr(data, "ts_init", 0) or now_ns
        silent = self._feeds.note_message(feed, now_ns, arrival_ns)
        if silent is not None:
            self._schedule_backfill(feed.name, now_ns, silent)
        if isinstance(data, OrderBookDeltas):
            iid = str(data.instrument_id)
            if not self._is_collected(iid):
                self._unplanned_messages[iid] += 1
                return
            self._feeds.instruments[feed.name].add(iid)
            if iid in self._crosscheck_arrivals:  # armed: REST is fetched right after a push
                self._crosscheck_arrivals[iid] += 1
                self._crosscheck_events[iid].set()
            if iid in self._delta_store:
                self._buffer[(OrderBookDeltas, iid)].append(data)
            book = self._books.get(iid) or self._book(iid)
            if self._venue_time:
                book.hold(data, now_ns, self._last_closed_second)
            else:
                self._apply_book(iid, book, data, now_ns)
        elif isinstance(data, TradeTick):
            iid = str(data.instrument_id)
            if not self._is_collected(iid):
                self._unplanned_messages[iid] += 1
                return
            self._accept_live_trade(iid, data, feed, now_ns, arrival_ns)
        elif isinstance(data, QuoteTick):
            pass  # derivable from the snapshots; not persisted
        else:  # mark/index price, funding rate, open interest, ... -> catalog as-is
            self._buffer[(type(data), str(data.instrument_id))].append(data)

    def _accept_live_trade(
        self, iid: str, data: TradeTick, feed: Feed, now_ns: int, arrival_ns: int
    ) -> None:
        self._feeds.note_trade(feed.name, iid, arrival_ns)
        intake = self._intakes.get(iid) or self._intake(iid)
        trade_id = str(data.trade_id)
        outcome = intake.accept(data, trade_id, feed.name, now_ns, self._stale_trade_ns)
        if outcome == ACCEPTED:
            self._feeds.note_first_copy(feed.name)
            intake.fold(data, self._last_closed_second, self._ahead_ns)
            # Archived as received (both clocks), so the nightly rebuild can re-derive the
            # second from exchange time and correct what the live fold got wrong (D-45).
            self._buffer[(TradeTick, iid)].append(data)
            intake.advance(data.ts_event)
        elif outcome == REPLAY:
            self._on_replayed_trade(iid, data, feed, now_ns)
        elif outcome != STALE:
            first = intake.first_feed(trade_id)
            self._feeds.note_overlap(first or "", feed.name)
            if outcome == DUPLICATE_FEED_FOLD:
                # The backfill archived it before this live copy was processed: fold the live
                # copy (never archived twice), or the live second misses a trade the feed delivered.
                intake.fold(data, self._last_closed_second, self._ahead_ns)

    def _on_replayed_trade(self, iid: str, data: TradeTick, feed: Feed, now_ns: int) -> None:
        if now_ns - self._watchdog_started_ns >= _WATCHDOG_STARTUP_GRACE_NS:
            # The replay may deliver the gap's new trades before this one, advancing the
            # baseline past the gap; a trade we already had predates it.
            self._schedule_backfill(feed.name, now_ns, "replayed trade ids", {iid: data.ts_event})

    def _drain_pending_deltas(self, boundary_ns: int) -> None:
        """Venue mode: apply every held delta with `ts_event < boundary_ns` (`LiveBook.drain`)."""
        now_ns = time.time_ns()
        for iid, book in self._books.items():
            if not book.has_pending:
                continue
            armed = iid in self._crosscheck_captures
            capture = functools.partial(self._capture_book, iid) if armed else None
            broken = book.drain(boundary_ns, now_ns, capture)
            for event in broken or ():
                self._on_sequence_broken(iid, event)

    def _check_pending_overflow(self, now_ns: int) -> None:
        """
        MEM-02 bound: a delta still held `hold_back + 5 s` after it arrived has a `ts_event` that
        far ahead of our clock -- the book cannot be venue-timed. Drop it (ledgered) and resync
        where the client can; a full-snapshot venue's next message rebuilds the book.
        """
        limit = self._hold_back_ns + VENUE_AHEAD_NS
        can_resync = hasattr(self._client, "resync_orderbook")
        for iid, book in self._books.items():
            overflow = book.check_overflow(now_ns, limit)
            if overflow is None:
                continue
            self._ledger(
                sites.PENDING_DELTAS,
                f"{iid}: {overflow.held} book messages held {overflow.held_ns / 1e9:.1f}s > "
                f"hold_back_seconds + {VENUE_AHEAD_NS / 1e9:.0f}s (venue clock ahead of "
                "arrival?): book dropped" + (", resync queued" if can_resync else ""),
            )
            if can_resync:
                book.resync_pending = True

    # -- the flush report ----------------------------------------------------------------------

    def _report_stale_trades(self) -> None:
        """Report every counter the aggregates hold since the last report, then reset them."""
        per_intake = {iid: intake.take_counts() for iid, intake in self._intakes.items()}
        per_book = {iid: book.take_counts() for iid, book in self._books.items()}

        def nonzero(values: Mapping[str, int]) -> dict[str, int]:
            return {iid: n for iid, n in values.items() if n}

        stale = nonzero({iid: c.stale for iid, c in per_intake.items()})
        duplicate = nonzero({iid: c.duplicate for iid, c in per_intake.items()})
        duplicate_feed = nonzero({iid: c.duplicate_feed for iid, c in per_intake.items()})
        before_snapshot = nonzero({iid: c.before_snapshot for iid, c in per_book.items()})
        if stale:
            logger.info(f"Dropped subscribe-time trade history: {stale}")
        if duplicate:
            logger.warning(f"Dropped duplicate trades (replayed after reconnect?): {duplicate}")
        if duplicate_feed:
            logger.warning(
                "Dropped duplicate_feed trades (first copy archived from another feed): "
                f"{duplicate_feed}"
            )
        if before_snapshot:
            logger.warning(
                "Dropped order-book deltas that arrived before a snapshot (subscribe/resync "
                f"window): {before_snapshot}"
            )
        self._report_venue_counts(
            nonzero({iid: c.late for iid, c in per_intake.items()}),
            nonzero({iid: c.ahead for iid, c in per_intake.items()}),
            nonzero({iid: c.pre_start for iid, c in per_intake.items()}),
            nonzero({iid: c.late_deltas for iid, c in per_book.items()}),
        )
        self._report_trade_sources()
        self._report_unplanned_messages()

    def _close_report_cycle(self) -> None:
        """End one flush cycle: report every counter, then drop the books that left the set."""
        self._report_stale_trades()
        self._dispose_uncollected_books()

    def _dispose_uncollected_books(self) -> None:
        """Drop the books that left the collected set, once their counters have been reported."""
        for iid in [iid for iid in self._books if not self._is_collected(iid)]:
            del self._books[iid]

    def _report_unplanned_messages(self) -> None:
        """
        One ledger entry per flush for every book/trade message of an instrument outside
        `applied & plan` (in flight across an unsubscribe, or a wire-failed one still delivering).
        """
        if self._unplanned_messages:
            self._ledger(
                sites.UNPLANNED_MESSAGE,
                "book/trade messages for instruments not both planned and subscribed, counted and "
                f"never booked, folded or archived: {dict(sorted(self._unplanned_messages.items()))}",
            )
            self._unplanned_messages.clear()

    def _report_trade_sources(self) -> None:
        """Cumulative REST-backfilled counts and, with more than one live feed, arbitration."""
        backfilled = {iid: i.backfilled for iid, i in self._intakes.items() if i.backfilled}
        if backfilled:
            logger.info(f"Trades backfilled over REST (cumulative): {backfilled}")
        if len(self._feeds.first_copies) > 1:
            logger.info(f"Trade feed arbitration (cumulative): {self._feeds.arbitration_summary()}")

    def _report_venue_counts(
        self,
        late: Mapping[str, int],
        ahead: Mapping[str, int],
        pre_start: Mapping[str, int],
        late_deltas: Mapping[str, int],
    ) -> None:
        """Venue mode: one ledger entry per instrument per report cycle (every flush)."""
        for site, counts, what in (
            (sites.LATE_TRADE, late, "arrived after their second closed"),
            (sites.VENUE_CLOCK_AHEAD, ahead, "were stamped ahead of arrival"),
        ):
            for iid, n in counts.items():
                self._ledger(
                    site,
                    f"{iid}: {n} trades {what} (hold_back_seconds="
                    f"{self._config.hold_back_seconds}): archived, not in the live row; the "
                    "nightly rebuild places them",
                )
        if pre_start:
            logger.info(
                "Trades received before the first venue second closed (archived, placed by the "
                f"nightly rebuild): {dict(pre_start)}"
            )
        if late_deltas:
            logger.warning(
                "Book messages applied after their second closed (venue mode, counted into "
                f"the next second): {dict(late_deltas)}"
            )

    # -- flush -------------------------------------------------------------------------------

    async def _flush_once(self, final: bool = False) -> None:
        """
        Write every buffered batch through the `ArchiveWriter`, each sorted by `ts_init` (stable).
        Unless `final` (shutdown: everything must go), a `TradeTick` batch keeps back its open
        `ts_init` group (`FlushBatch`'s carry rule).
        """
        flushed_seconds: dict[str, list[DydxSecondSnapshot]] = {}
        now_ns = time.time_ns()
        batches = self._buffer.take(now_ns, final, not self._ingest_queue.empty())
        for key, items in batches:
            try:
                # Real disk I/O -- off the event loop so _second_loop isn't stalled.
                await asyncio.to_thread(self._archive.write, items)
            except Exception as e:
                self._ledger(
                    sites.FLUSH_WRITE, f"failed to write {key}, {len(items)} items LOST", e
                )
                if key[0] is TradeTick:
                    self._mark_lost_trades(key[1], items, now_ns)
                continue
            if key[0] is DydxSecondSnapshot:
                flushed_seconds[key[1]] = items
        self._apply_to_candle_store(flushed_seconds)

    def _mark_lost_trades(self, iid: str, lost: list[TradeTick], now_ns: int) -> None:
        """
        Record an archive gap for a trade batch that failed to write while this instrument's
        snapshots (holding those trades) may have landed, so the nightly rebuild keeps those
        rows' live values.
        """
        self._archive.mark_gap(
            iid, lost[0].ts_init, now_ns, "write_failed", len(lost), self._ledger
        )

    def _apply_to_candle_store(self, flushed: dict[str, list[DydxSecondSnapshot]]) -> None:
        """
        Hand what just reached Parquet to the second sink, one instrument at a time.

        Only flushed seconds are applied, so the store is never ahead of the archive. A failure must
        not stop ingestion or the other instruments: it is loud (DATA-07), and the next start's
        catch-up or `python -m candles.rebuild` repairs it.

        One instrument's failure is isolated but the flush is ledgered once, naming every instrument
        that failed. A store-wide fault (disk full, a locked file) fails every subscribed instrument
        in the same flush, and one line each would exceed the ledger's 60-lines-per-site-per-minute
        cap and suppress the very detail this is here to record.
        """
        if self._second_sink is None:
            return
        failed: list[str] = []
        by_type: dict[str, Exception] = {}
        hits: Counter[str] = Counter()
        for iid, rows in flushed.items():
            try:
                self._second_sink.apply(iid, rows)
            except Exception as e:
                failed.append(iid)
                # One line carries one traceback, so keep one exception per distinct type and name
                # them all in the detail: a store-wide fault plus an incidental second cause must
                # not reduce to whichever instrument `flushed` happened to yield first.
                by_type.setdefault(type(e).__name__, e)
                hits[type(e).__name__] += 1
        if failed:
            # The traceback goes to the cause that hit the most instruments, not the alphabetically
            # first one: a store-wide `OSError` on 29 coins must not be masked by an incidental
            # `AttributeError` on the thirtieth. Ties break on the name, so the line is reproducible.
            worst = min(hits, key=lambda name: (-hits[name], name))
            self._ledger(
                sites.CANDLE_STORE,
                f"candle store write failed for {len(failed)} instruments "
                f"({', '.join(f'{n} on {hits[n]}' for n in sorted(hits))}): "
                + ", ".join(sorted(failed)),
                by_type[worst],
            )

    def _catch_up_candle_store(self) -> None:
        """
        Apply, at startup, the seconds the archive holds beyond each instrument's watermark.

        A crash between a Parquet flush and its store write (or a failed store write) leaves the
        store behind the archive, and nothing else would ever fill that hole. Runs before the first
        subscribe, so no live second can reach the sink first and push the watermark past the gap. A
        gap wider than a day is left to the rebuild CLI (it would read too much here) and logged.

        No sink is a legal construction (the collector's own tests build one), but in a deployed
        process it means a venue entrypoint forgot `second_sink=` and every bar is silently missing.
        This is the one place that runs exactly once per start, so the report belongs here, on the
        error ledger (DATA-07), not a bare `logger.warning`.
        """
        if self._second_sink is None:
            self._ledger(
                sites.NO_SECOND_SINK,
                "no SecondSink injected: this collector archives Parquet but builds no candles. "
                "A venue entrypoint must pass second_sink=CandleSink(store_from_env(...)).",
            )
            return
        catalog_path = self._archive.catalog_path
        now_ns = time.time_ns()
        for iid, mark in self._second_sink.watermarks().items():
            if now_ns - mark > _CATCH_UP_MAX_NS:
                logger.warning(
                    f"Candle store for {iid} is more than a day behind: "
                    "run python -m candles.rebuild"
                )
                continue
            try:
                self._second_sink.apply(iid, query_second_ohlc(catalog_path, iid, mark + 1, now_ns))
            except Exception as e:
                self._ledger(
                    sites.CANDLE_STORE_CATCH_UP, f"candle store catch-up failed for {iid}", e
                )

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(
                _seconds_until_next_flush(time.time(), self._config.flush_interval_seconds)
            )
            await self._flush_once()
            self._close_report_cycle()

    # -- sample ------------------------------------------------------------------------------

    async def _sample_tick(
        self, now_ns: int, second: int | None = None
    ) -> list[DydxSecondSnapshot]:
        """
        One pass of the single write gate (AD-1): `SecondSampler.sample` judges every collected
        book; each accepted row goes to the catalog buffer and (via the caller) Redis -- same
        object. Rejections are logged and ledgered here, then every requested resync is executed.

        `second` (venue mode): the exchange second to close; the held deltas are applied up to
        its end first, and its trade bucket is folded.
        """
        if second is not None:
            self._drain_pending_deltas((second + 1) * S_NS)
        result = self._sampler.sample(
            self._instrument_ids(),
            self._books,
            self._intakes,
            now_ns,
            second,
            self._feeds.feed_dead(now_ns, self._feed_stale_ns),
        )
        for iid, event in result.uncrossed:
            self._log_uncrossed(iid, event, now_ns)
        for iid, verdict in result.rejected:
            self._report_rejection(iid, verdict, now_ns)
        for snapshot in result.accepted:
            iid = str(snapshot.instrument_id)
            self._buffer[(DydxSecondSnapshot, iid)].append(snapshot)
            self._check_impossible_ohlc(iid, snapshot, now_ns)
        if second is not None:
            self._sampler.close_second(self._intakes, second, self._last_closed_second is None)
            self._last_closed_second = second
        for iid in result.resyncs:
            await self._resync(iid)
        return result.accepted

    def _report_rejection(self, iid: str, verdict: Rejected, now_ns: int) -> None:
        if isinstance(verdict, NoBook):
            if self._rate_limited(self._last_no_book_log_ns, iid, now_ns):
                # One per minute: an instrument that never gets a book (not on the venue, or
                # awaiting a fresh snapshot) must not be a quiet gap (DATA-01) -- the watchdog
                # only fires when *all* books are dead.
                logger.warning("No book for %s — skipping snapshot", iid)
        elif isinstance(verdict, EmptyTop):
            if self._rate_limited(self._last_empty_top_log_ns, iid, now_ns):
                # Was silent until Story 26.1 (parent spine Deferred, a DATA-07 gap): a book that
                # never presents both sides is a permanent gap nothing else surfaces.
                logger.warning("Empty top of book for %s (a side has no level) — skipping", iid)
                self._ledger(
                    sites.EMPTY_TOP,
                    f"{iid}: book has no best bid or no best ask; seconds skipped (one line per "
                    "instrument per minute while it lasts)",
                )
        elif isinstance(verdict, Crossed):
            self._report_crossed(iid, verdict, now_ns)
        elif isinstance(verdict, Stale):
            logger.warning("Stale book for %s (%s) — skipping snapshot", iid, verdict.detail)

    @staticmethod
    def _rate_limited(last_log: dict[str, int], iid: str, now_ns: int) -> bool:
        if now_ns - last_log.get(iid, 0) < _IMPOSSIBLE_LOG_EVERY_NS:
            return False
        last_log[iid] = now_ns
        return True

    def _report_crossed(self, iid: str, verdict: Crossed, now_ns: int) -> None:
        """
        Report a crossed sample: ledgered once per episode where a cross is corruption (a central book),
        a WARNING every sample, and -- when the policy asked for the DATA-03 fallback -- a
        `collector.resync` entry (even when it then succeeds: a rising count is an open DATA-02
        incident) and a CRITICAL `steady_state_crossed_book` line for the incident reports.
        """
        for level in verdict.dropped:
            self._log_dropped(iid, level)
        if verdict.first_seen and self._crossed_policy.crossing_is_corruption:
            self._ledger(sites.CROSSED_BOOK, f"{iid} bid={verdict.bid} ask={verdict.ask}")
        sides = self._side_ages(verdict, now_ns)
        logger.warning(
            "Crossed book for %s (bid=%s >= ask=%s) for %.1fs — skipping sample%s",
            iid,
            verdict.bid,
            verdict.ask,
            (now_ns - verdict.since_ns) / 1e9,
            ""
            if sides is None
            else f" [last bid delta {sides[0]:.1f}s ago, last ask delta {sides[1]:.1f}s ago]",
        )
        if not verdict.resync:
            return
        self._ledger(
            sites.RESYNC,
            f"forced resync for {iid}: crossed for {(now_ns - verdict.since_ns) / 1e9:.0f}s "
            "(DATA-03 fallback, not a fix)",
        )
        critical_logger.critical(
            json.dumps(
                {
                    "instrument_id": iid,
                    "reason": "steady_state_crossed_book",
                    "best_bid": verdict.bid,
                    "best_ask": verdict.ask,
                    "crossed_duration_ns": now_ns - verdict.since_ns,
                    "bid_delta_stale_s": None if sides is None else sides[0],
                    "ask_delta_stale_s": None if sides is None else sides[1],
                    "ts_event_ns": now_ns,
                }
            )
        )

    @staticmethod
    def _side_ages(verdict: Crossed, now_ns: int) -> tuple[float, float] | None:
        """How long ago each side last changed (a venue with level tagging), else None."""
        bid_ns, ask_ns = verdict.last_bid_delta_ns, verdict.last_ask_delta_ns
        if bid_ns is None or ask_ns is None:
            return None  # an untagged venue, or a side never stamped: no age to report
        return (now_ns - bid_ns) / 1e9, (now_ns - ask_ns) / 1e9

    def _log_uncrossed(self, iid: str, event: BookUncrossed, now_ns: int) -> None:
        for level in event.dropped:
            self._log_dropped(iid, level)
        if event.since_ns is None:
            return
        was = event.was or (math.nan, math.nan)
        logger.info(
            "Crossed book for %s resolved after %.2fs (was bid=%.6f/ask=%.6f, now bid=%s/ask=%s)",
            iid,
            (now_ns - event.since_ns) / 1e9,
            was[0],
            was[1],
            _price_text(event.bid),
            _price_text(event.ask),
        )

    @staticmethod
    def _log_dropped(iid: str, level: DroppedLevel) -> None:
        """
        DATA-04's active uncross. DATA-02: a one-sided book after uncrossing is a real data-loss
        event, not routine self-healing, so it must not be logged like the benign case.
        """
        if level.side_now_empty:
            logger.warning(
                "Crossed book for %s uncrossed by dropping the LAST remaining %s level "
                "@ %.6f (msg_id %d, surviving side msg_id %d) -- book is now one-sided",
                iid,
                level.side.name,
                level.price,
                level.stale_seq,
                level.other_seq,
            )
        else:
            logger.info(
                "Crossed book for %s actively uncrossed: dropped stale %s @ %.6f "
                "(msg_id %d, surviving side msg_id %d)",
                iid,
                level.side.name,
                level.price,
                level.stale_seq,
                level.other_seq,
            )

    def _check_impossible_ohlc(self, iid: str, snapshot: DydxSecondSnapshot, now_ns: int) -> None:
        if ohlc_outside_book(snapshot) and self._rate_limited(
            self._last_impossible_log_ns, iid, now_ns
        ):
            # Unreachable after the stale/duplicate trade filters; if it fires, an
            # ingestion bug is writing impossible prices (DATA-02/DATA-06 canary).
            logger.error(
                f"IMPOSSIBLE trade OHLC for {iid}: high={snapshot.high_price} "
                f"low={snapshot.low_price} outside book "
                f"[{min(snapshot.bid_prices)}, {max(snapshot.ask_prices)}]"
            )

    async def _publish(self, batch: list[DydxSecondSnapshot]) -> None:
        if self._live_stream is not None:
            await self._live_stream.publish(batch)

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
            await self._publish(await self._sample_tick(now_ns))

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
                await self._publish(await self._sample_tick(now_ns, second))
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

    # -- REST book cross-check (story 22.5, audit D-64) ----------------------------------------

    def _live_book(self, iid: str) -> OrderBook | None:
        book = self._books.get(iid)
        return None if book is None else book.book

    def _live_top(self, iid: str) -> tuple[list, list] | None:
        book = self._live_book(iid)
        if book is None:
            return None
        return (
            [(lv.price.as_double(), lv.size()) for lv in book.bids()[:BOOK_DEPTH]],
            [(lv.price.as_double(), lv.size()) for lv in book.asks()[:BOOK_DEPTH]],
        )

    def _capture_book(self, iid: str) -> None:
        """Record the live top-20 with its alignment keys while a cross-check is armed."""
        captures = self._crosscheck_captures.get(iid)
        book = self._live_book(iid)
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
        # Venue mode applies a message up to 1 + hold_back after arrival; VENUE_AHEAD_NS is the
        # bound after which a held message is dropped, so a capture cannot take longer.
        return 1.0 + self._config.hold_back_seconds + VENUE_AHEAD_NS / 1e9

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
        if self._live_book(iid) is None:
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
            self._ledger(
                sites.BOOK_CROSSCHECK,
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
            self._ledger(
                sites.BOOK_CROSSCHECK_UNALIGNED,
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
                    self._ledger(sites.BOOK_CROSSCHECK, f"cross-check failed for {iid}", e)

    # -- trade gap closure (story 22.14) ------------------------------------------------------

    def _baselines(self, feed_name: str) -> dict[str, int]:
        """Return the feed's instruments' newest archived `ts_event` (those with an archived trade)."""
        baselines = {}
        for iid in self._feeds.instruments.get(feed_name, ()):
            intake = self._intakes.get(iid)
            if intake is not None and intake.last_trade_ts is not None:
                baselines[iid] = intake.last_trade_ts
        return baselines

    def _schedule_backfill(
        self,
        feed_name: str,
        now_ns: int,
        reason: str,
        earlier: Mapping[str, int] | None = None,
    ) -> None:
        """
        Coalesce a reconnect detection into the feed's one pending backfill (`FeedGroup.schedule`).
        A new request snapshots the baselines now -- the silence signal fires before the resuming
        message is processed, so nothing post-gap is in them yet.
        """
        baselines = {} if feed_name in self._feeds.requests else self._baselines(feed_name)
        if self._feeds.schedule(feed_name, now_ns, reason, baselines, earlier):
            logger.info(
                f"Reconnect detected on feed {feed_name} ({reason}): trade backfill scheduled"
            )

    def _poll_feed_states(self, now_ns: int) -> None:
        """One `feed_states()` poll: an inactive -> active transition is a reconnect."""
        try:
            states = self._client.feed_states()
        except Exception as e:
            if now_ns - self._feed_state_error_ns >= _FEED_STATE_ERROR_EVERY_NS:
                self._feed_state_error_ns = now_ns  # a broken poll must not ledger 10x a second
                self._ledger(sites.FEED_STATE, "feed_states() failed", e)
            return
        for feed_name, earlier in self._feeds.observe_states(states, self._baselines):
            self._schedule_backfill(
                feed_name, now_ns, "feed reconnected (inactive -> active)", earlier
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
                self._ledger(sites.TRADE_BACKFILL, "trade backfill failed", e)

    async def _run_due_backfills(self, now_ns: int) -> None:
        """Run every request whose settle has passed, one at a time (sequential REST)."""
        for feed_name, request in self._feeds.due(now_ns):
            await self._run_backfill(feed_name, request)

    async def _run_backfill(self, feed_name: str, request: BackfillRequest) -> None:
        """Backfill every instrument this feed carried, then ledger exactly one entry."""
        report = BackfillReport(feed_name, request.reasons)
        wanted = set(self._instrument_ids())
        instruments = sorted(self._feeds.instruments.get(feed_name, set()) & wanted)
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
            self._ledger(sites.TRADE_BACKFILL, report.message())

    async def _backfill_instrument(
        self, iid: str, last: int | None, report: BackfillReport
    ) -> None:
        """`last`: the instrument's pre-gap baseline from the request (None: no archived trade)."""
        if last is None:
            report.no_baseline += 1  # the rebuild's coverage also starts at the first trade
            return
        instrument = self._instruments.get(iid)
        if instrument is None:
            report.errors[iid] = "no instrument definition from the venue"
            return
        if self._trade_history is None:
            report.errors[iid] = "no VenueTradeHistory injected: nothing can be backfilled"
            return
        fetch_ns = time.time_ns()
        try:
            fetched = await asyncio.to_thread(
                self._trade_history.fetch,
                instrument,
                last - BACKFILL_LOOKBACK_NS,
                fetch_ns - MAX_TS_INIT_SKEW_NS,
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
        self, iid: str, trades: list[TradeTick], report: BackfillReport
    ) -> int | None:
        """
        Archive the unseen trades (`admit_backfill`), never into the live second. Returns the
        newest refused trade's `ts_event` (known lost up to there), or None.
        """
        archive, newest_refused = admit_backfill(self._intake(iid), trades, time.time_ns(), report)
        if archive:
            self._buffer[(TradeTick, iid)].extend(archive)
        return newest_refused

    # -- watchdog ------------------------------------------------------------------------------

    def _one_sided_messages(self, now_ns: int, name: str) -> list[str]:
        """
        Per feed group with two or more feeds: a feed is behind when its last trade is more than
        `_ONE_SIDED_NS` older than the group's newest (a feed that never delivered one counts
        from the watchdog's start). Returns the alert transitions' messages.
        """
        messages = []
        for group, behind, live in self._feeds.groups_behind(
            _ONE_SIDED_NS, self._watchdog_started_ns
        ):
            down_since, reminder = self._one_sided_state.get(group, (None, 0))
            message, down_since, reminder = watchdog.transition(
                now_ns,
                bool(behind),
                down_since,
                reminder,
                _one_sided_texts(name, group, behind, live),
                _WATCHDOG_REMINDER_NS,
            )
            self._one_sided_state[group] = (down_since, reminder)
            if message is not None:
                messages.append(message)
        return messages

    def _last_book_update_ns(self, iid: str) -> int:
        book = self._books.get(iid)
        return 0 if book is None or book.last_update_ns is None else book.last_update_ns

    def _book_watchdog_message(self, now_ns: int, name: str) -> str | None:
        live = self._instrument_ids()
        if not live:
            return None
        is_stale = all(now_ns - self._last_book_update_ns(iid) > _WATCHDOG_STALE_NS for iid in live)
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
                await asyncio.to_thread(self._notifier.notify, notify.OPERATOR, name, message)

    # -- the applied set (spine AD-D17) -------------------------------------------------------

    async def apply(self, diff: PlanDiff) -> Applied:
        """
        Apply one plan change on the wire and report what actually happened.

        Adopts the diff's complete raw-delta storage set first. Removed ids leave the plan and
        lose their book state first, whether or not the wire unsubscribe then succeeds (a book
        exists only for `applied & plan`). Added ids are marked applied *before* the subscribe is
        awaited, so the venue's subscribe-time snapshot is booked rather than counted unplanned; a
        failed subscribe un-marks it. Every wire failure is ledgered once per attempt and retried
        by `_subscription_retry_loop`; an id the venue does not list is ledgered and never retried.
        Never raises for a per-instrument failure. Runs under `_subscription_lock`, so it never
        interleaves with a retry round.
        """
        self._delta_store = set(diff.store_deltas)
        subscribed: set[str] = set()
        unsubscribed: set[str] = set()
        failed: set[str] = set()
        async with self._subscription_lock:
            for iid in sorted(diff.removed):
                # A failed subscribe may have sent part of the id's channels, or left them for the
                # client's reconnect replay: its removal unsubscribes it too, and it lingers (holds
                # a wire slot, messages counted) until that unsubscribe succeeds.
                on_wire = iid in self._applied or iid in self._retry_subscribe
                self._plan_ids.discard(iid)
                self._retry_subscribe.discard(iid)
                # Its book, last book time and queued resync must not outlive it: a later re-add
                # would otherwise report (and judge staleness from) a book the removal left behind.
                book = self._books.get(iid)
                if book is not None:
                    book.forget()
                if on_wire:
                    self._applied.add(iid)
                    (unsubscribed if await self._unsubscribe_one(iid) else failed).add(iid)
            for iid in sorted(diff.added):
                self._plan_ids.add(iid)
                (subscribed if await self._subscribe_added(iid) else failed).add(iid)
        return Applied(frozenset(subscribed), frozenset(unsubscribed), frozenset(failed))

    async def _subscribe_added(self, iid: str) -> bool:
        if iid in self._applied:
            # Still (partly) subscribed from a failed unsubscribe: cancel that retry, re-subscribe
            # any channel the failed unsubscribe did end (a no-op for the channels still held),
            # and force a fresh baseline, since the book was cleared when the id left the plan.
            self._retry_unsubscribe.discard(iid)
            if not await self._subscribe_one(iid):
                return False
            if hasattr(self._client, "resync_orderbook"):
                await self._resync_wire(iid)  # `apply` already holds the lock
            return True
        if self._listed is not None and iid not in self._listed:
            self._ledger(
                sites.SUBSCRIBE_FAILED,
                f"{iid} is not listed on the venue: pending, not subscribed and not retried",
            )
            return False
        return await self._subscribe_one(iid)

    async def _subscribe_one(self, iid: str) -> bool:
        """
        Subscribe one id, marking it applied first (its subscribe-time snapshot must be booked).

        Known limit: "applied" means the subscribe frames were sent -- the venue clients raise
        only on a local send failure, so a venue that rejects a subscription asynchronously
        (dYdX's per-connection limit, an unknown market) leaves the id applied, not pending; the
        rejection shows only in the Rust client's log and as the book watchdog's silence. The
        client's `subscribe`/`unsubscribe` must also be idempotent per channel, because a retry,
        a re-add of a lingering id and the removal of a pending one repeat them after a partial
        failure: `DydxClient`'s are (it mirrors the Rust client's per-topic reference and its
        reconnect replay of a failed subscribe); Bybit's and Hyperliquid's are not, harmless
        while their plans are static (only the startup subscribe and its retry ever run).
        Upgrade path: surface the venue's subscribe acknowledgement from the Rust clients
        (outside this fork's `platform/` boundary) and track per-channel state in every client.
        """
        self._applied.add(iid)
        try:
            await self._client.subscribe(iid)
        except Exception as e:
            self._applied.discard(iid)
            # A message booked during the await must not show a pending id with a book or a
            # book time.
            book = self._books.get(iid)
            if book is not None:
                book.forget()
            self._retry_subscribe.add(iid)
            self._ledger(
                sites.SUBSCRIBE_FAILED,
                f"subscribe {iid} failed on the wire: pending, retried every "
                f"{self._SUBSCRIBE_RETRY_SECONDS:.0f}s",
                e,
            )
            return False
        self._retry_subscribe.discard(iid)
        logger.info(f"Subscribed {iid}")
        return True

    async def _unsubscribe_one(self, iid: str) -> bool:
        try:
            await self._client.unsubscribe(iid)
        except Exception as e:
            self._retry_unsubscribe.add(iid)
            self._ledger(
                sites.UNSUBSCRIBE_FAILED,
                f"unsubscribe {iid} failed on the wire: its book and trade messages are counted, "
                f"not sampled; retried every {self._SUBSCRIBE_RETRY_SECONDS:.0f}s",
                e,
            )
            return False
        self._applied.discard(iid)
        self._retry_unsubscribe.discard(iid)
        logger.info(f"Unsubscribed {iid}")
        return True

    async def _retry_subscriptions(self) -> None:
        """
        One retry round: planned ids whose subscribe failed, unplanned ids still subscribed. Under
        `_subscription_lock`, so the sets it iterates cannot change under it.
        """
        async with self._subscription_lock:
            self._retry_subscribe &= self._plan_ids
            for iid in sorted(self._retry_subscribe):
                await self._subscribe_one(iid)
            self._retry_unsubscribe -= self._plan_ids
            self._retry_unsubscribe &= self._applied
            for iid in sorted(self._retry_unsubscribe):
                await self._unsubscribe_one(iid)

    async def _subscription_retry_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._SUBSCRIBE_RETRY_SECONDS)
            await self._retry_subscriptions()

    def capture_status(self) -> CaptureStatus:
        """Return capture's read-only counters for `collector:status`, copied at one instant."""
        return CaptureStatus(
            applied=frozenset(self._applied & self._plan_ids),
            pending=frozenset(self._plan_ids - self._applied),
            lingering=frozenset(self._applied - self._plan_ids),
            last_book_update_ns={
                iid: book.last_update_ns
                for iid, book in self._books.items()
                if book.last_update_ns is not None
            },
            trade_backfill={iid: i.backfilled for iid, i in self._intakes.items() if i.backfilled},
        )

    # -- lifecycle ---------------------------------------------------------------------------

    async def run(self) -> None:
        instruments = await self._client.fetch_instruments()
        by_id = {i.id.value: i for i in instruments}
        converted = instruments_from_pyo3(list(by_id.values()))
        self._instruments = {str(i.id): i for i in converted}  # the trade backfill's precisions
        self._archive.write_instruments(converted)

        await self._connect(list(by_id.values()))
        if hasattr(self._client, "subscribe_global"):
            await self._client.subscribe_global()
        self._listed = frozenset(by_id)
        unknown = self._plan_ids - self._listed
        if unknown:
            logger.warning(
                "Configured instruments not found on the venue, skipping: %s", sorted(unknown)
            )
        self._catch_up_candle_store()
        applied = await self.apply(
            PlanChange(added=frozenset(self._plan_ids), store_deltas=self._store_deltas())
        )
        logger.info(f"Started: {len(applied.subscribed)} subscribed")

        loops: tuple[Callable[[], Awaitable[None]], ...] = (
            self._ingest_loop,
            self._flush_loop,
            self._second_loop,
            self._watchdog_loop,
            self._trade_backfill_loop,
            self._subscription_retry_loop,
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
            self._close_report_cycle()
            if self._live_stream is not None:
                await self._live_stream.close()

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
            self._ledger(sites.DISCONNECT, "disconnect failed", e)

    def _ledger_abandoned_backfills(self) -> None:
        """Ledger every reconnect detected but not yet backfilled at shutdown (a named gap, DATA-05)."""
        for feed_name, request in self._feeds.abandon():
            self._ledger(
                sites.TRADE_BACKFILL,
                f"feed {feed_name} ({'; '.join(request.reasons)}): abandoned at shutdown, "
                f"{len(request.since)} instruments not fetched",
            )

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
    # Durable error ledger (story 23.3): a no-op unless ERROR_LEDGER_DIR is set, and the one
    # place this process writes its `process_start` marker.
    error_ledger.start()
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
    capture_lock = None
    try:
        while not shutting_down.is_set():
            collector = build()
            if first:
                # Held (shared) for the process lifetime: `repair_catalog` refuses to write under
                # a running collector, and an archive tool holding it exclusively makes capture
                # wait. Released by the `finally` below however this loop ends.
                capture_lock = await collector._archive.acquire_lock(
                    type(collector).VENUE, shutting_down, collector._ledger
                )
                if capture_lock is None or shutting_down.is_set():  # shut down while waiting
                    break
                # The plan, not `_instrument_ids()`: nothing is applied before `run()`.
                collector._archive.quarantine_corrupt(
                    sorted(collector._plan_ids), collector._ledger
                )
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
    finally:
        if capture_lock is not None:
            capture_lock.close()  # releases the flock; the file itself is never unlinked
    del _log_guard


async def _stop_on_shutdown(shutting_down: asyncio.Event, collector: Collector) -> None:
    await shutting_down.wait()
    collector.stop()
