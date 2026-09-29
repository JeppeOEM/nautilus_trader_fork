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
`TradeIntake`: one instrument's trade admission (DDD spine AD-D6/AD-D7, DATA-06, story 22.14).

Invariants it protects, and the commands that could break them:

- *A trade is archived once and folded live at most once* (`accept`): the bounded id window
  remembers which feed delivered each id first. A repeat on that same feed is a replay
  (`duplicate`); a repeat from another feed -- including the REST backfill source `rest` -- is a
  `duplicate_feed`. Neither is archived again; only a live copy of a trade REST archived first is
  folded (the live second would otherwise miss a trade the live feed delivered).
- *Subscribe-time history is never live* (`accept`): a first copy that was already older than
  `stale_trade_seconds` when it *arrived* (`ts_init - ts_event`, `ts_init` being the Rust client's
  receipt stamp) is counted `stale` and dropped (DATA-06); a replay is checked first, since it is
  mostly older. Age is judged on arrival, never at processing time, so an ingest backlog cannot
  turn a live trade into history (Story 31.2: a 15 s stall dropped every trade of a recorded
  burst). A trade with no receipt stamp (`ts_init == 0`, a hand-built one) keeps the
  processing-time age, `now_ns - ts_event`. Each report carries the stale count, the oldest and
  newest dropped `ts_event` and the oldest and youngest age (`IntakeCounts`).
- *A trade is never folded into a second that already closed* (`fold`, venue mode): it is counted
  `late` (or `ahead`, when its stamp runs past its arrival by more than the hold-back plus
  `VENUE_AHEAD_NS`), archived by the caller and placed by the nightly rebuild -- never dropped.
- *An id is remembered for as long as a later copy of it could still be archived* (`register`):
  the window evicts its oldest id only while it holds more than `seen_trade_ids` ids **and** that
  id was registered more than `DEDUP_HORIZON_NS` before the newest one. A live copy older than
  `stale_trade_seconds` on arrival is dropped as stale and a REST backfill never admits a trade
  older than `MAX_TS_INIT_SKEW_NS` (`admit_backfill`), so no copy that could be archived arrives
  after the horizon. `seed` fills the window at start from the archive's own recent trades (first
  feed `archive`), so a restart does not forget what was archived just before it. A live copy
  of a seeded id is a `duplicate_feed` whatever its age, judged before the stale rule.
- *MEM-02*: the id window is time-bounded (above) and a second's bucket leaves at its close.
  Known limit: within the horizon the window is not count-bounded, and it is per instrument: it
  holds every id of that instrument's last `DEDUP_HORIZON_NS` (6 min). Measured (Python 3.13) at
  about 130 bytes per id -- the id string (a Bybit UUID, ~85 B) plus its `_first` entry and deque
  slot (~47 B) -- and about 100 bytes more for every id a second feed also delivered (its
  `_repeats` list, every id with `trade_feeds = 2`). At a sustained 1000 trades/s that is ~47 MB
  for one instrument, ~84 MB with two feeds, and each busy instrument adds its own. Upgrade path:
  a compact per-feed id encoding (a UUID as 16 bytes) or a bloom-backed tier past
  `seen_trade_ids`.

Mutated in place: the steady-state accept path creates no object beyond the id string and the list
slot that archiving it needs (spine AD-D5); registration times are one mark per second. The first
copy's feed is stored as the feed's own name string; a list exists only for an id some other feed
repeated.
"""

from collections import deque
from typing import NamedTuple

from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS

from nautilus_trader.model.data import TradeTick


S_NS = NS_PER_S

# The first-copy source name of a trade archived by the REST backfill. Never a WS feed: REST data
# never goes through `on_data`, so it never counts as liveness.
REST_FEED_NAME = "rest"
# The first-copy source name of an id seeded from the archive at start (`seed`): a live copy of it
# is a `duplicate_feed`, never archived or folded again, and never a feed-arbitration overlap.
ARCHIVE_FEED_NAME = "archive"

# How long an id must stay in the window after its registration. Every later copy that could be
# archived arrives within it: a live copy is checked by arrival age (older than
# `stale_trade_seconds`, at most 10 s by config, is dropped as stale) and a REST backfill never
# admits a trade whose `ts_event` is more than `MAX_TS_INIT_SKEW_NS` before its fetch. An id's
# registration time is at or after its own `ts_event`, so a copy arriving later than
# `MAX_TS_INIT_SKEW_NS` after it can never be admitted. `READ_SPAN_MARGIN_NS` covers the skew
# between the clocks the registration times come from (receipt, fetch and archived `ts_init`) and
# the venue-ahead / hold-back slack of a venue-timed stamp.
DEDUP_HORIZON_NS = MAX_TS_INIT_SKEW_NS + READ_SPAN_MARGIN_NS

# `accept` outcomes (plain ints: no object per trade).
ACCEPTED = 0  # first copy: archive and fold
REPLAY = 1  # the same feed delivered it before
STALE = 2  # subscribe-time history
DUPLICATE_FEED = 3  # another feed delivered it first
DUPLICATE_FEED_FOLD = 4  # REST archived it first: fold this live copy, do not archive it


class StaleSpan(NamedTuple):
    """The stale trades of one report: oldest/newest `ts_event` and oldest/youngest arrival age."""

    first_ns: int
    last_ns: int
    oldest_age_ns: int
    youngest_age_ns: int

    def widened(self, ts_event: int, age_ns: int) -> "StaleSpan":
        return StaleSpan(
            min(self.first_ns, ts_event),
            max(self.last_ns, ts_event),
            max(self.oldest_age_ns, age_ns),
            min(self.youngest_age_ns, age_ns),
        )


class IntakeCounts(NamedTuple):
    stale: int
    duplicate: int
    duplicate_feed: int
    late: int
    ahead: int
    pre_start: int
    stale_span: StaleSpan | None = None


class TradeIntake:
    """
    One instrument's dedup window, live-second trades and counters (see the module docstring).

    `live`: arrival mode, the trades accepted since the last sample. `buckets`: venue mode, per
    exchange second. `last_trade_ts`: the newest `ts_event` archived, live or backfilled -- a
    reconnect's backfill baseline. `backfilled`: cumulative REST-archived trades.
    """

    __slots__ = (
        "_evicted",
        "_first",
        "_marks",
        "_order",
        "_registered",
        "_repeats",
        "_window",
        "ahead",
        "backfilled",
        "buckets",
        "duplicate",
        "duplicate_feed",
        "last_trade_ts",
        "late",
        "live",
        "pre_start",
        "stale",
        "stale_span",
    )

    def __init__(self, window: int) -> None:
        self._first: dict[str, str] = {}
        self._repeats: dict[str, list[str]] = {}
        # The ids in registration order, and when they were registered: one mark per registration
        # second, `(sequence number of its first id, an upper bound on every registration it
        # covers)`. Per second, not per id, so the hot path allocates no object per trade for it
        # (AD-D5); the bound makes eviction at most 1 s late, never early.
        self._order: deque[str] = deque()
        self._marks: deque[tuple[int, int]] = deque()
        self._registered = 0  # ids ever registered: the next id's sequence number
        self._evicted = 0  # ids ever evicted: the oldest remembered id's sequence number
        self._window = window
        self.live: list[TradeTick] = []
        self.buckets: dict[int, list[TradeTick]] = {}
        self.last_trade_ts: int | None = None
        self.backfilled = 0
        self.stale = self.duplicate = self.duplicate_feed = 0
        self.late = self.ahead = self.pre_start = 0
        self.stale_span: StaleSpan | None = None

    # -- admission -------------------------------------------------------------------------------

    def accept(
        self, trade: TradeTick, trade_id: str, feed_name: str, now_ns: int, stale_ns: float
    ) -> int:
        """
        Classify one live copy; see the module docstring. Returns an outcome constant. `now_ns` is
        the processing time: the registration time when the trade has no receipt stamp.
        """
        first = self._first.get(trade_id)
        if first is not None and (
            first == feed_name or feed_name in self._repeats.get(trade_id, ())
        ):
            self.duplicate += 1
            return REPLAY
        if first == ARCHIVE_FEED_NAME:
            # Already archived by an earlier process (the seed): a copy, whatever its age -- it
            # must never count as stale and widen a `trades_dropped` window it is not missing from.
            self._note_repeat(trade_id, feed_name)
            return DUPLICATE_FEED
        arrival_ns = trade.ts_init or now_ns
        age_ns = arrival_ns - trade.ts_event
        if age_ns > stale_ns:
            self._count_stale(trade.ts_event, age_ns)
            return STALE
        if first is None:
            self.register(trade_id, feed_name, arrival_ns)
            return ACCEPTED
        repeats = self._repeats.get(trade_id)
        self.duplicate_feed += 1
        if repeats is None:
            self._repeats[trade_id] = [feed_name]
            # REST archived it first and this is the first live copy: fold it once. A second
            # live copy (`trade_feeds = 2`) is a plain duplicate -- folding it too would double
            # the trade's volume in the live row.
            return DUPLICATE_FEED_FOLD if first == REST_FEED_NAME else DUPLICATE_FEED
        repeats.append(feed_name)  # bounded by the number of feeds
        return DUPLICATE_FEED

    def _note_repeat(self, trade_id: str, feed_name: str) -> None:
        """Count a `duplicate_feed` copy and remember its feed (a later copy on it is a replay)."""
        self.duplicate_feed += 1
        repeats = self._repeats.get(trade_id)
        if repeats is None:
            self._repeats[trade_id] = [feed_name]
        else:
            repeats.append(feed_name)  # bounded by the number of feeds

    def _count_stale(self, ts_event: int, age_ns: int) -> None:
        self.stale += 1
        span = self.stale_span
        self.stale_span = (
            StaleSpan(ts_event, ts_event, age_ns, age_ns)
            if span is None
            else span.widened(ts_event, age_ns)
        )

    def register(self, trade_id: str, feed_name: str, registered_ns: int) -> None:
        """
        Remember a first copy registered at `registered_ns` (arrival `ts_init`, a REST fetch's
        time, or an archived `ts_init`), then evict what the time-bounded window lets go (MEM-02).
        """
        marks = self._marks
        if not marks or registered_ns >= marks[-1][1]:
            marks.append((self._registered, registered_ns + S_NS))
        # else: inside the newest mark's second, or earlier (a clock step, a seed older than a
        # live id) -- that mark's bound is later, so the id is kept at least as long, never less.
        self._registered += 1
        self._order.append(trade_id)
        self._first[trade_id] = feed_name
        self._evict(max(registered_ns, marks[-1][1] - S_NS))

    def _evict(self, newest_ns: int) -> None:
        """Drop the oldest ids while the window is over size and they are past the horizon."""
        order, marks = self._order, self._marks
        oldest_kept = newest_ns - DEDUP_HORIZON_NS
        while len(order) > self._window:
            while len(marks) > 1 and marks[1][0] <= self._evicted:
                marks.popleft()  # every id of the oldest mark is gone
            if marks[0][1] > oldest_kept:
                return
            evicted = order.popleft()
            self._evicted += 1
            self._first.pop(evicted, None)
            if self._repeats:
                self._repeats.pop(evicted, None)

    def seed(self, ids_with_ts_init: list[tuple[str, int]]) -> int:
        """
        Remember ids the archive already holds (first feed `archive`), oldest `ts_init` first;
        an id already in the window keeps its entry. Returns how many were added.
        """
        added = 0
        for trade_id, ts_init in sorted(ids_with_ts_init, key=lambda pair: pair[1]):
            if trade_id not in self._first:
                self.register(trade_id, ARCHIVE_FEED_NAME, ts_init)
                added += 1
        return added

    def first_feed(self, trade_id: str) -> str | None:
        """Return the feed that delivered `trade_id` first, while it is inside the window."""
        return self._first.get(trade_id)

    def advance(self, ts_event: int) -> None:
        last = self.last_trade_ts
        if last is None or ts_event > last:
            self.last_trade_ts = ts_event

    # -- the live second -------------------------------------------------------------------------

    def fold(self, trade: TradeTick, closed_second: int | None, ahead_ns: int | None) -> None:
        """
        Put an accepted trade into the live row: its arrival second (`ahead_ns` None), or in venue
        mode its exchange second -- unless that second closed (`late`) or its stamp runs more
        than `ahead_ns` past its arrival (`ahead`).
        """
        if ahead_ns is None:
            self.live.append(trade)
            return
        second = trade.ts_event // S_NS
        if closed_second is not None and second <= closed_second:
            self.late += 1
        elif trade.ts_event - trade.ts_init > ahead_ns:
            self.ahead += 1
        else:
            bucket = self.buckets.get(second)
            if bucket is None:
                self.buckets[second] = [trade]
            else:
                bucket.append(trade)

    def take_second(self, second: int | None) -> list[TradeTick]:
        """Hand the sampled second's trades to the row (arrival: everything since the last one)."""
        if second is None:
            trades, self.live = self.live, []
            return trades
        return self.buckets.pop(second, [])

    def discard(self, second: int | None) -> None:
        """
        Drop a skipped sample's trades from the live row: otherwise an outage's trades would pile
        onto the first valid second (a giant-range candle). They stay archived; the nightly rebuild
        counts them as orphans.
        """
        if second is None:
            self.live.clear()
        else:
            self.buckets.pop(second, None)

    def drop_live(self) -> None:
        """MEM-02: the instrument is not sampled; its unfolded trades must not accumulate."""
        self.live.clear()
        self.buckets.clear()

    def close(self, second: int, first_close: bool) -> None:
        """
        Venue mode: second `second` closed. Older buckets never reached a row: `late` after a
        stall, or -- at the very first close -- `pre_start` (they arrived before closing began).
        """
        stale = [s for s in self.buckets if s <= second]
        for old in stale:
            n = len(self.buckets.pop(old))
            if first_close:
                self.pre_start += n
            else:
                self.late += n

    def take_counts(self) -> IntakeCounts:
        """Return every per-report counter and reset it (`backfilled` is cumulative, kept)."""
        counts = IntakeCounts(
            self.stale,
            self.duplicate,
            self.duplicate_feed,
            self.late,
            self.ahead,
            self.pre_start,
            self.stale_span,
        )
        self.stale = self.duplicate = self.duplicate_feed = 0
        self.late = self.ahead = self.pre_start = 0
        self.stale_span = None
        return counts
