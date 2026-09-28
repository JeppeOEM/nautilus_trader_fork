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
- *Subscribe-time history is never live* (`accept`): a first copy older than `stale_trade_seconds`
  is counted `stale` and dropped (DATA-06); a replay is checked first, since it is mostly older.
- *A trade is never folded into a second that already closed* (`fold`, venue mode): it is counted
  `late` (or `ahead`, when its stamp runs past its arrival by more than the hold-back plus
  `VENUE_AHEAD_NS`), archived by the caller and placed by the nightly rebuild -- never dropped.
- *MEM-02*: the id window is bounded (`seen_trade_ids`), and a second's bucket leaves at its close.
- *Every counter is reported*: `take_counts` hands each one to the flush report and resets it.

Mutated in place: the steady-state accept path creates no object beyond the id string and the
list slot that archiving it needs (spine AD-D5). The first copy's feed is stored as the feed's
own name string; a list exists only for an id some other feed repeated.
"""

from collections import deque
from typing import NamedTuple

from kernel.clocks import NS_PER_S

from nautilus_trader.model.data import TradeTick


S_NS = NS_PER_S

# The first-copy source name of a trade archived by the REST backfill. Never a WS feed: REST data
# never goes through `on_data`, so it never counts as liveness.
REST_FEED_NAME = "rest"

# `accept` outcomes (plain ints: no object per trade).
ACCEPTED = 0  # first copy: archive and fold
REPLAY = 1  # the same feed delivered it before
STALE = 2  # subscribe-time history
DUPLICATE_FEED = 3  # another feed delivered it first
DUPLICATE_FEED_FOLD = 4  # REST archived it first: fold this live copy, do not archive it


class IntakeCounts(NamedTuple):
    stale: int
    duplicate: int
    duplicate_feed: int
    late: int
    ahead: int
    pre_start: int


class TradeIntake:
    """
    One instrument's dedup window, live-second trades and counters (see the module docstring).

    `live`: arrival mode, the trades accepted since the last sample. `buckets`: venue mode, per
    exchange second. `last_trade_ts`: the newest `ts_event` archived, live or backfilled -- a
    reconnect's backfill baseline. `backfilled`: cumulative REST-archived trades.
    """

    __slots__ = (
        "_first",
        "_order",
        "_repeats",
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
    )

    def __init__(self, window: int) -> None:
        self._first: dict[str, str] = {}
        self._repeats: dict[str, list[str]] = {}
        self._order: deque[str] = deque(maxlen=window)
        self.live: list[TradeTick] = []
        self.buckets: dict[int, list[TradeTick]] = {}
        self.last_trade_ts: int | None = None
        self.backfilled = 0
        self.stale = self.duplicate = self.duplicate_feed = 0
        self.late = self.ahead = self.pre_start = 0

    # -- admission -------------------------------------------------------------------------------

    def accept(
        self, trade: TradeTick, trade_id: str, feed_name: str, now_ns: int, stale_ns: float
    ) -> int:
        """Classify one live copy; see the module docstring. Returns an outcome constant."""
        first = self._first.get(trade_id)
        if first is not None and (
            first == feed_name or feed_name in self._repeats.get(trade_id, ())
        ):
            self.duplicate += 1
            return REPLAY
        if now_ns - trade.ts_event > stale_ns:
            self.stale += 1
            return STALE
        if first is None:
            self.register(trade_id, feed_name)
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

    def register(self, trade_id: str, feed_name: str) -> None:
        """Remember a first copy, evicting the oldest id in lockstep with the window (MEM-02)."""
        order = self._order
        if len(order) == order.maxlen:
            evicted = order[0]
            self._first.pop(evicted, None)
            if self._repeats:
                self._repeats.pop(evicted, None)
        order.append(trade_id)
        self._first[trade_id] = feed_name

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
            self.stale, self.duplicate, self.duplicate_feed, self.late, self.ahead, self.pre_start
        )
        self.stale = self.duplicate = self.duplicate_feed = 0
        self.late = self.ahead = self.pre_start = 0
        return counts
