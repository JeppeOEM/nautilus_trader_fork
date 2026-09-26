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
Venue variance as policy *values* (DDD spine AD-D6), passed to the gate's aggregates.

Invariant: a policy is pure and synchronous -- it returns a verdict and never logs, ledgers,
awaits or reads the clock (`now_ns` is passed in), so no venue can change what the gate does with
a verdict, only what the verdict is. That is what replaced the venue `Collector` subclasses' hook
overrides (`_apply_deltas`, `_handle_crossed_book`): a subclass now overrides nothing but
`__init__`, where it builds these values. `platform/tests/test_boundaries.py` holds every
`policies.py` to the domain import rule and to no `logging`/`await`.

The core defaults here serve a central-book venue (Bybit, Hyperliquid); dYdX's live in
`dydx_collector/policies.py`, Bybit's sequence canary in `bybit_collector/policies.py`.
"""

from dataclasses import dataclass
from typing import ClassVar
from typing import Literal
from typing import Protocol

from collector_core.domain.verdicts import CrossVerdict
from collector_core.domain.verdicts import ResyncRequested
from collector_core.domain.verdicts import StillCrossed
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import OrderSide


# Per price level: the message id of the update that last touched it (dYdX, DATA-04).
LevelTags = dict[tuple[OrderSide, float], int]

SequenceVerdict = Literal["ok", "gap", "regress", "snapshot"]

BookTimeSource = Literal["arrival", "venue"]


class CrossedBookPolicy(Protocol):
    """
    What a crossed book means at this venue and what to do about it.

    `crossing_is_corruption`: True for a central book, where any cross is our (or the venue's) loss
    and each episode is ledgered `collector.crossed_book`; False where a cross is architectural
    (dYdX, DATA-04). `step` may repair `book` in place (dYdX's stale-level deletes) and returns
    `Uncrossed`, `StillCrossed(since)` or -- past the grace window, and only where the client can
    resync -- `ResyncRequested(since)`. `since_ns` is None at an episode's first crossed sample.
    """

    @property
    def crossing_is_corruption(self) -> bool: ...

    def step(
        self, book: OrderBook, tags: LevelTags, since_ns: int | None, now_ns: int
    ) -> CrossVerdict: ...


class LevelTagger(Protocol):
    """
    Tags each applied level with the update that last touched it (dYdX, DATA-04), for a crossed
    policy to arbitrate on. Returns the side the delta touched (`NO_ORDER_SIDE` for a Clear), so
    the book can stamp per-side delta times.
    """

    def tag(self, tags: LevelTags, delta: OrderBookDelta) -> OrderSide: ...


class SequenceCanary(Protocol):
    """
    A venue's per-topic book sequence check (Bybit `u`, DATA-08). `message_key` returns the
    message's sequence, or None when it carries none (then nothing is checked); `verdict` judges it
    against the last one. A `gap` or `regress` drops the message and the book (DATA-02 canary).
    """

    def message_key(self, deltas: OrderBookDeltas) -> int | None: ...

    def verdict(self, last: int | None, key: int, is_snapshot: bool) -> SequenceVerdict: ...


@dataclass(frozen=True)
class CentralBookCrossPolicy:
    """
    The core default: a cross on a central book is corruption. Skip every crossed sample; past
    `resync_after_ns`, and only when `can_resync` (the client has `resync_orderbook`), ask for the
    DATA-03 fallback -- never the first response. A full-snapshot venue (Hyperliquid) only ever
    skips: its next message replaces the book.
    """

    resync_after_ns: int
    can_resync: bool
    crossing_is_corruption: ClassVar[bool] = True

    def step(
        self, book: OrderBook, tags: LevelTags, since_ns: int | None, now_ns: int
    ) -> CrossVerdict:
        since = now_ns if since_ns is None else since_ns
        if self.can_resync and now_ns - since > self.resync_after_ns:
            return ResyncRequested(since)
        return StillCrossed(since)


@dataclass(frozen=True)
class CapturePolicies:
    """
    One venue's policy values. `crossed` None = the core default, built by the `Collector` from
    its config and client (it alone knows whether the client can resync). `tagger`/`canary` None
    = the venue has none, and the hot path skips the call entirely (AD-D5).
    """

    crossed: CrossedBookPolicy | None = None
    tagger: LevelTagger | None = None
    canary: SequenceCanary | None = None
