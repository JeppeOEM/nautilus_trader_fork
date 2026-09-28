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
`FeedGroup`: one venue's WebSocket connections (DDD spine AD-D6, story 22.14).

A `Feed` names one connection (`name`) and the group of connections that carry the same trades
(`group`: a primary socket and its optional trades-only twin, `trade_feeds = 2`).

Invariants it protects, and the commands that could break them:

- *Liveness runs on arrival* (`note_message`): the Rust client's `ts_init` at receipt, so an
  ingest backlog is not mistaken for a silent socket; REST rows never pass through here, so they
  never stamp WS liveness.
- *A trades-only feed never counts toward book staleness*: only a book-carrying feed moves
  `last_book_message_ns` (the feed-dead gate) or the silence-based reconnect detection.
- *One pending backfill per feed* (`schedule`): every reconnect detection until it runs coalesces
  into it, its due time is kept and its per-instrument baselines are only ever lowered -- a
  baseline read when the backfill runs, after live trades advanced it, skips the whole outage.
- *Nothing detected is lost silently* (`abandon`): a request not yet run at shutdown is handed back
  to be ledgered.

Arbitration counters (`first_copies`, `overlap`) are cumulative per feed, for the flush report.
"""

from collections import defaultdict
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field

from capture.domain.verdicts import Stale


@dataclass(frozen=True)
class Feed:
    name: str
    group: str
    trades_only: bool = False


MAIN_FEED = Feed("main", "main")


@dataclass
class BackfillRequest:
    """
    One feed's pending backfill: every detection until it runs coalesces into it.

    `since` is each instrument's pre-gap baseline (the newest archived trade `ts_event` before the
    outage), captured at the earliest evidence and only ever lowered: by the time the backfill
    runs, trades after the resume have advanced the intake's `last_trade_ts`, and reading it then
    would skip the whole outage (network-cut test, 2026-09-21: 368 of 382 ADAUSDT trades not
    fetched).
    """

    due_ns: int
    reasons: list[str]
    since: dict[str, int] = field(default_factory=dict)

    def lower(self, baselines: Mapping[str, int]) -> None:
        for iid, ts in baselines.items():
            self.since[iid] = min(self.since.get(iid, ts), ts)


class FeedGroup:
    """
    Per-feed liveness, reconnect evidence, backfill requests and arbitration counters for one
    venue (see the module docstring). `silence_ns`: a book feed silent longer than this and then
    speaking again has reconnected (`feed_stale_seconds or stale_book_seconds`).
    """

    def __init__(self, silence_ns: float, settle_ns: int) -> None:
        self._silence_ns = silence_ns
        self._settle_ns = settle_ns
        self.feeds: set[Feed] = set()
        self.last_ns: dict[str, int] = {}
        self.last_trade_ns: dict[str, int] = {}
        # Only TradeTick/OrderBookDeltas teach it: dYdX's markets channel carries every market.
        self.instruments: defaultdict[str, set[str]] = defaultdict(set)
        self.active: dict[Feed, bool] = {}
        # A feed's baselines, captured when it was first seen inactive (the flip's pre-gap state).
        self.inactive_baselines: dict[str, dict[str, int]] = {}
        self.last_book_message_ns = 0  # processing time of the last book-feed message
        self.requests: dict[str, BackfillRequest] = {}
        self.first_copies: defaultdict[str, int] = defaultdict(int)  # cumulative
        self.overlap: defaultdict[tuple[str, str], int] = defaultdict(int)

    # -- liveness --------------------------------------------------------------------------------

    def note_message(self, feed: Feed, now_ns: int, arrival_ns: int) -> str | None:
        """
        Record one message's arrival. Returns a reconnect reason when a book-carrying feed was
        silent past the silence bound and speaks again, else None.
        """
        self.feeds.add(feed)
        name = feed.name
        previous = self.last_ns.get(name)
        self.last_ns[name] = arrival_ns if previous is None or arrival_ns > previous else previous
        if feed.trades_only:
            return None
        self.last_book_message_ns = now_ns
        if previous is not None and arrival_ns - previous > self._silence_ns:
            return f"feed silent {(arrival_ns - previous) / 1e9:.1f}s"
        return None

    def note_trade(self, feed_name: str, iid: str, arrival_ns: int) -> None:
        """Any trade message is liveness for the one-sided check, replayed or not."""
        self.instruments[feed_name].add(iid)
        previous = self.last_trade_ns.get(feed_name, 0)
        if arrival_ns > previous:
            self.last_trade_ns[feed_name] = arrival_ns

    def feed_dead(self, now_ns: int, feed_stale_ns: float) -> Stale | None:
        """Judge the feed-level half of DATA-01: no book-feed message at all for `feed_stale_ns`."""
        age_ns = now_ns - self.last_book_message_ns
        if age_ns > feed_stale_ns:
            detail = f"feed dead: no WS message of any kind for {age_ns / 1e9:.1f}s"
            return Stale("feed_dead", detail)
        return None

    def observe_states(
        self, states: Mapping[Feed, bool], baselines: Callable[[str], dict[str, int]]
    ) -> list[tuple[str, dict[str, int]]]:
        """
        One `feed_states()` poll: returns (feed name, pre-gap baselines) for every inactive ->
        active transition. `baselines(feed_name)`: that feed's current baselines, read only for a
        feed seen inactive for the first time.
        """
        reconnected = []
        for feed, is_active in states.items():
            self.feeds.add(feed)
            previous = self.active.get(feed)
            self.active[feed] = is_active
            if not is_active:
                if feed.name not in self.inactive_baselines:
                    self.inactive_baselines[feed.name] = baselines(feed.name)
            elif previous is False:
                reconnected.append((feed.name, self.inactive_baselines.pop(feed.name, {})))
        return reconnected

    def groups_behind(
        self, behind_ns: int, default_ns: int
    ) -> list[tuple[str, list[str], list[str]]]:
        """
        Per feed group with two or more feeds: (group, feeds whose last trade is more than
        `behind_ns` older than the group's newest, the rest). A feed that never delivered a
        trade counts from `default_ns` (the watchdog's start).
        """
        groups: defaultdict[str, list[str]] = defaultdict(list)
        for feed in self.feeds:
            groups[feed.group].append(feed.name)
        result = []
        for group, names in sorted(groups.items()):
            if len(names) < 2:
                continue
            last = {n: self.last_trade_ns.get(n, default_ns) for n in names}
            newest = max(last.values())
            behind = sorted(n for n in names if newest - last[n] > behind_ns)
            result.append((group, behind, sorted(set(names) - set(behind))))
        return result

    # -- backfill requests -----------------------------------------------------------------------

    def schedule(
        self,
        feed_name: str,
        now_ns: int,
        reason: str,
        baselines: Mapping[str, int],
        earlier: Mapping[str, int] | None = None,
    ) -> bool:
        """
        Coalesce a detection into the feed's one pending request; True when it created one.
        `baselines`: the feed's current ones (a new request's starting point); `earlier` lowers
        them further with evidence captured before the resume (the flip's inactive-time snapshot, a
        replay).
        """
        request = self.requests.get(feed_name)
        created = request is None
        if request is None:
            request = BackfillRequest(now_ns + self._settle_ns, [reason], dict(baselines))
            self.requests[feed_name] = request
        elif reason not in request.reasons:
            request.reasons.append(reason)
        request.lower(earlier or {})
        return created

    def due(self, now_ns: int) -> list[tuple[str, BackfillRequest]]:
        """Take every request whose settle has passed, in scheduling order."""
        names = [name for name, r in self.requests.items() if r.due_ns <= now_ns]
        return [(name, self.requests.pop(name)) for name in names]

    def abandon(self) -> list[tuple[str, BackfillRequest]]:
        """Take every request still pending (shutdown), sorted by feed."""
        abandoned = sorted(self.requests.items())
        self.requests.clear()
        return abandoned

    # -- arbitration -----------------------------------------------------------------------------

    def note_first_copy(self, feed_name: str) -> None:
        self.first_copies[feed_name] += 1

    def note_overlap(self, first: str, feed_name: str) -> None:
        self.overlap[(first, feed_name)] += 1

    def arbitration_summary(self) -> str:
        """Per feed: first copies, first copies no other feed delivered, and pairwise overlaps."""
        parts = []
        for feed_name in sorted(self.first_copies):
            first = self.first_copies[feed_name]
            shared = sum(n for (a, _), n in self.overlap.items() if a == feed_name)
            parts.append(f"{feed_name}: first {first}, only-this-feed {first - shared}")
        both = {f"{a}+{b}": n for (a, b), n in sorted(self.overlap.items())}
        return f"{'; '.join(parts)}; both {both}"
