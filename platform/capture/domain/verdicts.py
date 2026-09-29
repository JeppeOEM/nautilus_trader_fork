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
The write gate's verdicts (DDD spine AD-D6): what `LiveBook.snapshot_top` says about one book at
one sample, and what a `CrossedBookPolicy` says about a crossed one.

Invariant: a sample is written only on `Accepted`; every other verdict names the one reason the
second was skipped (`NoBook`, `EmptyTop`, `Crossed`, `Stale`, and -- after the book passed --
`Unencodable` when the row cannot be stored exactly), so the application can log and ledger it
without re-deriving anything. Verdicts exist per sampled instrument per second, never
per message (AD-D5).
"""

from dataclasses import dataclass
from typing import Literal

from nautilus_trader.model.book import BookLevel
from nautilus_trader.model.enums import OrderSide


@dataclass(frozen=True, slots=True)
class NoBook:
    """No local book: never subscribed, awaiting its first snapshot, or dropped for a resync."""


@dataclass(frozen=True, slots=True)
class EmptyTop:
    """The book has no best bid or no best ask, so no top of book can be written."""


NO_BOOK = NoBook()
EMPTY_TOP = EmptyTop()


@dataclass(frozen=True, slots=True)
class DroppedLevel:
    """One stale level a policy deleted to uncross a book (DATA-04), for the application's log."""

    side: OrderSide
    price: float
    stale_seq: int
    other_seq: int
    side_now_empty: bool


@dataclass(frozen=True, slots=True)
class Crossed:
    """
    Best bid >= best ask after the venue's crossed-book policy ran. `first_seen`: the episode
    started at this sample. `resync`: the policy asked for the DATA-03 fallback.
    `last_bid_delta_ns`/`last_ask_delta_ns`: when each side last changed, where the venue tracks it
    (dYdX's level tagging); None otherwise. `dropped`: levels the policy deleted at this sample
    without uncrossing the book.
    """

    bid: float
    ask: float
    since_ns: int
    first_seen: bool
    resync: bool
    last_bid_delta_ns: int | None = None
    last_ask_delta_ns: int | None = None
    dropped: tuple[DroppedLevel, ...] = ()


StaleKind = Literal["feed_dead", "instrument_silent"]


@dataclass(frozen=True, slots=True)
class Stale:
    """The book may be old: the whole feed is silent, or this one instrument is (DATA-01)."""

    kind: StaleKind
    detail: str


@dataclass(frozen=True, slots=True)
class Unencodable:
    """
    The book passed every check but its row cannot be stored exactly (Story 30.2): no instrument
    definition to take the precisions from, or a value `kernel.second_snapshot` refuses (finer than
    the precision, outside int64, an unsorted level). Never rounded or guessed; `reason` says which.
    """

    reason: str


Rejected = NoBook | EmptyTop | Crossed | Stale | Unencodable


@dataclass(frozen=True, slots=True)
class Accepted:
    """The top `depth` levels per side, best first, of a book that passed every check."""

    bids: list[BookLevel]
    asks: list[BookLevel]


SampleVerdict = Accepted | Rejected


@dataclass(frozen=True, slots=True)
class Uncrossed:
    """The policy resolved the cross itself; `dropped` lists what it deleted to do so."""

    dropped: tuple[DroppedLevel, ...] = ()


@dataclass(frozen=True, slots=True)
class StillCrossed:
    """Still crossed; the episode began at `since_ns`. `dropped`: levels deleted this step."""

    since_ns: int
    dropped: tuple[DroppedLevel, ...] = ()


@dataclass(frozen=True, slots=True)
class ResyncRequested:
    """Still crossed past the grace window: the application must force a fresh snapshot."""

    since_ns: int
    dropped: tuple[DroppedLevel, ...] = ()


CrossVerdict = Uncrossed | StillCrossed | ResyncRequested
