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
The liquidation self-check (Story 33.1), pure: each archived liquidation matched against the raw
trade archive, and the day's unrecoverable liquidation windows measured.

The mechanism: Bybit's forced order executes on the book, so a liquidation is also a
`publicTrade`. A liquidation is *matched* by a trade of the same instrument with the same size, on
the forced side (a liquidated long is a forced **sell**, so a trade whose aggressor is the
seller; a liquidated short a buyer-aggressor trade), whose `ts_event` lies within
`MATCH_WINDOW_NS` of the liquidation's; each trade matches at most one liquidation (the earliest
eligible one, which maximises the matches).

Written from `docs/DATA_DICTIONARY.md` §1.26 alone: the reference side never imports the
`Liquidation` type it checks, and reads the stored columns raw.

Known limit: the match is a self-check, never an oracle, and never asserts 100 %. A liquidation
order that fills against several resting orders prints several smaller trades, none of the full
size, and the bankruptcy price is not the fill price, so an unmatched liquidation is evidence to
look at, not a proven loss; the share is reported, never gated (`archive.verify_day` keeps it
beside the verdict, outside it). Upgrade path: a sum-of-fills match over the same window, once a
recorded day shows how Bybit splits a forced order.
"""

from bisect import bisect_left
from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType

from verification.domain.conservation import instrument_category
from verification.domain.verdict import LIQUIDATION_VENUES


NS_PER_S = 1_000_000_000
MATCH_WINDOW_NS = 2 * NS_PER_S
# The archive's fixed-point raws carry 16 decimals (`trade_check.FIXED_PRECISION`), restated.
FIXED_PRECISION = 16
# Nautilus's `AggressorSide` codes as stored in `trade_tick.aggressor_side`.
BUYER = 1
SELLER = 2
# The liquidated side as stored (`side` column) -> the aggressor side of its forced order.
FORCED_AGGRESSOR = MappingProxyType({"long": SELLER, "short": BUYER})
UNMATCHED_SHOWN = 20


def has_liquidation_feed(venue: str, instrument_id: str) -> bool:
    """
    Whether the platform captures this instrument's liquidations, restated from
    `docs/DATA_DICTIONARY.md` §1.26/§2.15 (never imported from the kernel): a venue of
    `LIQUIDATION_VENUES` (Bybit) and its `linear` category. The candle oracle folds such an
    instrument's liquidations (0 in a quiet bucket) and leaves every other's null.
    `platform/tests/test_liquidation_feed_predicates.py` holds it equal to the production
    predicate on a table of ids, without this module importing it.
    """
    return venue in LIQUIDATION_VENUES and instrument_category(venue, instrument_id) == "linear"


@dataclass(frozen=True)
class StoredLiquidation:
    """One archived liquidation row, read raw: the columns the match needs."""

    venue_event_id: str
    side: str
    size_units: int
    size_precision: int
    ts_event: int

    def size_raw(self) -> int:
        """Return the size as a fixed-point raw at 16 decimals, the trade archive's encoding."""
        if not 0 <= self.size_precision <= FIXED_PRECISION:
            raise ValueError(f"{self.venue_event_id}: size precision {self.size_precision}")
        return self.size_units * 10 ** (FIXED_PRECISION - self.size_precision)

    def forced_aggressor(self) -> int:
        aggressor = FORCED_AGGRESSOR.get(self.side)
        if aggressor is None:
            raise ValueError(f"{self.venue_event_id}: stored side {self.side!r} is not long/short")
        return aggressor


@dataclass(frozen=True)
class Fill:
    """One archived trade, reduced to what a match compares (size as a 16-decimal raw)."""

    size_raw: int
    aggressor: int
    ts_event: int


@dataclass(frozen=True)
class InstrumentMatch:
    """One instrument-day: the liquidations, the matched ones, the unmatched ids, the gaps."""

    instrument_id: str
    total: int
    matched: int
    unmatched_ids: tuple[str, ...]
    unrecoverable_seconds: int

    @property
    def share(self) -> float | None:
        """Matched / total (None without a liquidation): a reader's figure, never stored."""
        return None if self.total == 0 else self.matched / self.total


def _candidates(fills: Iterable[Fill]) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Index fills by (aggressor, size raw), each list of `(ts_event, index)` sorted by time."""
    index: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for position, fill in enumerate(fills):
        index.setdefault((fill.aggressor, fill.size_raw), []).append((fill.ts_event, position))
    for entries in index.values():
        entries.sort()
    return index


def _earliest_unused(
    entries: Sequence[tuple[int, int]], ts_event: int, used: set[int]
) -> tuple[int, int] | None:
    """Return the earliest unused entry in `[ts_event - window, ts_event + window]`, or None."""
    low = bisect_left(entries, (ts_event - MATCH_WINDOW_NS, -1))
    for ts, position in entries[low:]:
        if ts > ts_event + MATCH_WINDOW_NS:
            return None
        if position not in used:
            return ts, position
    return None


def match_liquidations(
    liquidations: Sequence[StoredLiquidation], fills: Iterable[Fill]
) -> tuple[int, tuple[str, ...]]:
    """
    Return how many liquidations a fill matches and the ids of the rest, in `ts_event` order.

    Each liquidation, earliest first, takes the *earliest* unused fill of its size on its forced
    side within the window. With every window the same width this is a maximum matching (the
    interval-scheduling exchange argument: a later liquidation's window reaches at least as far
    right, so leaving it the later fills never costs a match) -- the greedy "nearest" choice
    could take the fill a later liquidation needed.
    """
    index = _candidates(fills)
    used: set[int] = set()
    matched = 0
    unmatched: list[str] = []
    for row in sorted(liquidations, key=lambda r: (r.ts_event, r.venue_event_id)):
        key = (row.forced_aggressor(), row.size_raw())
        found = _earliest_unused(index.get(key, ()), row.ts_event, used)
        if found is None:
            unmatched.append(row.venue_event_id)
        else:
            used.add(found[1])
            matched += 1
    return matched, tuple(unmatched)


def covered_seconds(windows: Iterable[tuple[int, int]], start_ns: int, end_ns: int) -> int:
    """
    Count the whole seconds of `[start_ns, end_ns)` any inclusive `[from_ns, to_ns]` window touches,
    overlaps counted once: how long the day's liquidations were unrecorded.
    """
    seconds: set[int] = set()
    for low, high in windows:
        first, last = max(low, start_ns), min(high, end_ns - 1)
        if first <= last:
            seconds.update(range(first // NS_PER_S, last // NS_PER_S + 1))
    return len(seconds)


def instrument_match(
    instrument_id: str,
    liquidations: Sequence[StoredLiquidation],
    fills: Iterable[Fill],
    unrecoverable_seconds: int,
) -> InstrumentMatch:
    matched, unmatched = match_liquidations(liquidations, fills)
    return InstrumentMatch(
        instrument_id, len(liquidations), matched, unmatched, unrecoverable_seconds
    )
