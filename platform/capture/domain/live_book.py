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
`LiveBook`: the local L2 replica of one collected instrument (DDD spine AD-D6).

Invariants it protects, and the commands that could break them:

- *A delta applies at most once, and only after a snapshot baseline* (`apply`, `drain`): a message
  that does not start with a Clear while there is no book is dropped and counted (`before_snapshot`),
  never built into a shallow book that looks uncrossed and passes the gate; a held venue-mode
  message is taken out of `pending` before it is applied, so a book dropped mid-drain cannot
  re-apply it.
- *A book is sampled only while it is present, two-sided, uncrossed and fresh* (`snapshot_top`):
  the four gate checks, the crossed one delegated to the venue's `CrossedBookPolicy`.
- *A resync is the fallback, never the first response* (DATA-03): only a policy verdict, a sequence
  break or a pending overflow marks `resync_pending`; `resync()` drops every piece of local state
  a stale book could leave behind (crossed-since, level tags, last `u`, pending deltas).
- *MEM-02*: venue-mode pending deltas are bounded by `hold_back + VENUE_AHEAD_NS`
  (`check_overflow`), after which the book is dropped.

The Nautilus `OrderBook` is held by reference and mutated in place; the class adds no per-delta
object (spine AD-D5): `apply` returns an event only on a sequence break. It is the one place
capture names the concrete order-book class (the Epic 28 swap point). Time is always passed in.
"""

import bisect
from collections.abc import Callable
from typing import NamedTuple

from kernel.clocks import NS_PER_S

from capture.domain.events import BookUncrossed
from capture.domain.events import PendingOverflow
from capture.domain.events import SequenceBroken
from capture.domain.policies import CrossedBookPolicy
from capture.domain.policies import LevelTagger
from capture.domain.policies import LevelTags
from capture.domain.policies import SequenceCanary
from capture.domain.verdicts import EMPTY_TOP
from capture.domain.verdicts import NO_BOOK
from capture.domain.verdicts import Accepted
from capture.domain.verdicts import Crossed
from capture.domain.verdicts import DroppedLevel
from capture.domain.verdicts import Rejected
from capture.domain.verdicts import ResyncRequested
from capture.domain.verdicts import SampleVerdict
from capture.domain.verdicts import Stale
from capture.domain.verdicts import StillCrossed
from capture.domain.verdicts import Uncrossed
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide


S_NS = NS_PER_S
# A delta held (or a trade stamped) this much beyond the hold-back after its arrival means the
# venue clock runs ahead of ours: bounds the pending list and the trade buckets (MEM-02).
VENUE_AHEAD_NS = 5_000_000_000

_BUY = OrderSide.BUY
_SELL = OrderSide.SELL


class BookCounts(NamedTuple):
    """A `LiveBook`'s per-report counters (`take_counts`)."""

    before_snapshot: int
    late_deltas: int


class LiveBook:
    """
    One instrument's book and its tracking state (see the module docstring for the invariants).

    `last_update_ns`: arrival of the last book message (liveness, never `ts_event`). `book_event_ns`:
    venue mode, the newest applied delta's `ts_event`. `tags`/`last_bid_delta_ns`/
    `last_ask_delta_ns`: kept only with a `LevelTagger`. `last_u`: the canary's baseline.
    """

    __slots__ = (
        "_canary",
        "_pending",
        "_pending_seq",
        "_tagger",
        "before_snapshot",
        "book",
        "book_event_ns",
        "crossed_prices",
        "crossed_since_ns",
        "last_ask_delta_ns",
        "last_bid_delta_ns",
        "last_u",
        "last_update_ns",
        "late_deltas",
        "resync_pending",
        "tags",
    )

    def __init__(
        self, tagger: LevelTagger | None = None, canary: SequenceCanary | None = None
    ) -> None:
        self._tagger = tagger
        self._canary = canary
        self.book: OrderBook | None = None
        self.last_update_ns: int | None = None
        self.book_event_ns = 0
        self.crossed_since_ns: int | None = None
        self.crossed_prices: tuple[float, float] | None = None
        self.resync_pending = False
        self.tags: LevelTags = {}
        self.last_u: int | None = None
        self.last_bid_delta_ns: int | None = None
        self.last_ask_delta_ns: int | None = None
        # (ts_event, arrival seq, ts_init, deltas), sorted; the seq makes every key unique, so
        # deltas are never compared.
        self._pending: list[tuple[int, int, int, OrderBookDeltas]] = []
        self._pending_seq = 0
        self.before_snapshot = 0  # deltas dropped for want of a baseline, since the last report
        self.late_deltas = 0  # venue mode: messages held after their second closed

    # -- commands ------------------------------------------------------------------------------

    def apply(self, deltas: OrderBookDeltas, now_ns: int) -> SequenceBroken | None:
        """Apply one message (arrival mode, or a drained venue-mode one); see the invariants."""
        items = deltas.deltas
        if not items:
            return None  # a content-less update carries nothing to apply or to judge
        if self._canary is not None:
            broken = self._check_sequence(self._canary, deltas, items[0].is_clear)
            if broken is not None:
                return broken
        book = self.book
        if book is None:
            if not items[0].is_clear:
                self.before_snapshot += 1
                return None
            book = self.book = OrderBook(deltas.instrument_id, BookType.L2_MBP)
            # A fresh snapshot is the baseline a queued resync was asking for: without this the
            # flag outlives its reason and fires an unwarranted DATA-03 resync at the next drop.
            self.resync_pending = False
        self.last_update_ns = now_ns
        if self._tagger is None:
            for delta in items:
                book.apply_delta(delta)
        else:
            self._apply_tagged(book, self._tagger, items, now_ns)
        return None

    def _apply_tagged(self, book: OrderBook, tagger: LevelTagger, items: list, now_ns: int) -> None:
        """Apply and tag each level (DATA-04), stamping when each side last changed."""
        for delta in items:
            book.apply_delta(delta)
            side = tagger.tag(self.tags, delta)
            if side != _SELL:
                self.last_bid_delta_ns = now_ns
            if side != _BUY:
                self.last_ask_delta_ns = now_ns

    def _check_sequence(
        self, canary: SequenceCanary, deltas: OrderBookDeltas, is_snapshot: bool
    ) -> SequenceBroken | None:
        """Run the canary: a gap or regress drops the book and queues a resync (DATA-08)."""
        key = canary.message_key(deltas)
        if key is None:
            if is_snapshot:
                # A snapshot always re-baselines, even one carrying no level (Bybit's adapter
                # emits a lone `Clear` for a zero-level snapshot, `crates/adapters/bybit/src/
                # websocket/parse.rs:259`, so no `u` can be read): the old baseline would read
                # the next delta as a gap on a healthy stream (DATA-08, audit D-96). With none, the next message sets it (`sequence_verdict`: "ok").
                # Known limit: that next delta is therefore unjudged -- no baseline, exactly as
                # after a connect or a resync. Upgrade path: the adapter carries the snapshot's
                # `u` on its `Clear`, so the snapshot itself sets the baseline.
                self.last_u = None
            return None
        verdict = canary.verdict(self.last_u, key, is_snapshot)
        if verdict == "gap" or verdict == "regress":
            broken = SequenceBroken(key, self.last_u, verdict)
            self.clear()  # also forgets last_u: the fresh snapshot re-baselines it
            self.resync_pending = True
            return broken
        self.last_u = key
        return None

    def hold(self, deltas: OrderBookDeltas, now_ns: int, closed_second: int | None) -> None:
        """Venue mode: hold a message until its second closes, in `ts_event` order."""
        # Arrival, not ts_event: the OBS-01 watchdog must never read a hold-back as a dead feed.
        self.last_update_ns = now_ns
        if closed_second is not None and deltas.ts_event < (closed_second + 1) * S_NS:
            # Its second already closed: applied at the next close (a book cannot be rewound).
            self.late_deltas += 1
        self._pending_seq += 1
        bisect.insort(self._pending, (deltas.ts_event, self._pending_seq, deltas.ts_init, deltas))

    def drain(
        self, boundary_ns: int, now_ns: int, on_applied: Callable[[], None] | None = None
    ) -> list[SequenceBroken] | None:
        """
        Apply every held message with `ts_event < boundary_ns`, oldest first. The due ones are
        taken out first: a sequence break mid-drain clears the rest of the pending list.
        `on_applied` runs after each applied message (an armed REST cross-check's capture).
        """
        pending = self._pending
        cut = bisect.bisect_left(pending, (boundary_ns,)) if pending else 0
        if not cut:
            return None
        due = pending[:cut]
        del pending[:cut]
        broken: list[SequenceBroken] | None = None
        for ts_event, _, _, deltas in due:
            event = self.apply(deltas, now_ns)
            if event is not None:
                broken = [*(broken or []), event]
            if self.book is not None:
                if ts_event > self.book_event_ns:
                    # max: a late message applied after newer ones must not age the book.
                    self.book_event_ns = ts_event
                if on_applied is not None and deltas.deltas:
                    on_applied()
        return broken

    def check_overflow(self, now_ns: int, limit_ns: int) -> PendingOverflow | None:
        """MEM-02: drop the book when a held delta is older than `limit_ns` (venue clock ahead)."""
        pending = self._pending
        if not pending:
            return None
        held_ns = now_ns - min(item[2] for item in pending)
        if held_ns <= limit_ns:
            return None
        overflow = PendingOverflow(len(pending), held_ns)
        self.clear()
        return overflow

    def clear(self) -> None:
        """
        Drop the book and every piece of state a stale book leaves behind: a later snapshot must
        not inherit a crossed-since (a false escalation), level tags or a sequence baseline. Held
        messages belong to the dropped book and are counted with the pre-snapshot drops.
        """
        self.book = None
        self.crossed_since_ns = None
        self.crossed_prices = None
        self.tags.clear()
        self.last_u = None
        self.book_event_ns = 0
        if self._pending:
            self.before_snapshot += len(self._pending)
            self._pending = []

    def resync(self) -> None:
        """Run the DATA-03 fallback's local half: drop the book; the fresh snapshot rebuilds it."""
        self.clear()

    def forget(self) -> None:
        """Forget everything of an instrument that left the collected set (AD-D17)."""
        self.clear()
        self.last_update_ns = None
        self.last_bid_delta_ns = None
        self.last_ask_delta_ns = None
        self.resync_pending = False

    def take_counts(self) -> "BookCounts":
        """Return the per-report counters since the last call, and reset them."""
        counts = BookCounts(self.before_snapshot, self.late_deltas)
        self.before_snapshot = self.late_deltas = 0
        return counts

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # -- the gate ------------------------------------------------------------------------------

    def snapshot_top(
        self,
        depth: int,
        now_ns: int,
        second: int | None,
        stale_ns: float,
        feed_dead: Stale | None,
        crossed: CrossedBookPolicy,
    ) -> tuple[SampleVerdict, BookUncrossed | None]:
        """
        Judge the book for one sample: missing, empty-topped, crossed (per `crossed`) or stale --
        the feed-wide `feed_dead` verdict first, then this instrument's own silence (arrival
        time, or in venue mode the `ts_event` gap to the end of `second`). Returns the verdict and
        the end of a crossed episode, if this sample saw one.
        """
        book = self.book
        if book is None:
            return NO_BOOK, None
        bid_price, ask_price = book.best_bid_price(), book.best_ask_price()
        if bid_price is None or ask_price is None:
            return EMPTY_TOP, None
        event: BookUncrossed | None = None
        if bid_price.as_double() >= ask_price.as_double():
            rejected, event = self._judge_cross(book, crossed, now_ns)
            if rejected is not None:
                return rejected, event
        elif self.crossed_since_ns is not None:
            event = self._end_episode(())
        if feed_dead is not None:
            return feed_dead, event
        stale = self._staleness(now_ns, second, stale_ns)
        if stale is not None:
            return stale, event
        return Accepted(book.bids()[:depth], book.asks()[:depth]), event

    def _judge_cross(
        self, book: OrderBook, crossed: CrossedBookPolicy, now_ns: int
    ) -> tuple[Rejected | None, BookUncrossed | None]:
        """
        Run the venue's crossed policy on a crossed book: (rejection or None, episode end or None).
        The book is re-read after the step (it may have deleted levels) and checked here, never
        trusted from the policy's verdict alone -- a crossed book is never sampled.
        """
        verdict = crossed.step(book, self.tags, self.crossed_since_ns, now_ns)
        bid_price, ask_price = book.best_bid_price(), book.best_ask_price()
        if bid_price is None or ask_price is None:  # the step left a side empty
            return EMPTY_TOP, self._end_episode(verdict.dropped)
        bid, ask = bid_price.as_double(), ask_price.as_double()
        if bid >= ask:
            if isinstance(verdict, Uncrossed):
                since = now_ns if self.crossed_since_ns is None else self.crossed_since_ns
                verdict = StillCrossed(since, verdict.dropped)
            return self._crossed(bid, ask, verdict), None
        return None, self._end_episode(verdict.dropped)

    def _crossed(self, bid: float, ask: float, verdict: StillCrossed | ResyncRequested) -> Crossed:
        first_seen = self.crossed_since_ns is None
        self.crossed_since_ns = verdict.since_ns
        if self.crossed_prices is None:
            self.crossed_prices = (bid, ask)
        return Crossed(
            bid,
            ask,
            verdict.since_ns,
            first_seen,
            isinstance(verdict, ResyncRequested),
            self.last_bid_delta_ns,
            self.last_ask_delta_ns,
            verdict.dropped,
        )

    def _end_episode(self, dropped: tuple[DroppedLevel, ...]) -> BookUncrossed:
        book = self.book
        assert book is not None
        bid, ask = book.best_bid_price(), book.best_ask_price()
        event = BookUncrossed(
            self.crossed_since_ns,
            self.crossed_prices,
            None if bid is None else bid.as_double(),
            None if ask is None else ask.as_double(),
            dropped,
        )
        self.crossed_since_ns = None
        self.crossed_prices = None
        return event

    def _staleness(self, now_ns: int, second: int | None, stale_ns: float) -> Stale | None:
        if second is None:
            age_ns = now_ns - (self.last_update_ns or 0)
            if age_ns > stale_ns:
                return Stale(
                    "instrument_silent",
                    f"instrument silent: no OrderBookDeltas for {age_ns / 1e9:.1f}s, feed alive",
                )
            return None
        age_ns = (second + 1) * S_NS - self.book_event_ns
        if age_ns > stale_ns:
            return Stale(
                "instrument_silent",
                f"instrument silent: no delta stamped in the {age_ns / 1e9:.1f}s before the "
                f"end of second {second}, feed alive",
            )
        return None
