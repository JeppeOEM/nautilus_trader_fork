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
Capture's domain events (DDD spine AD-D5/AD-D6): state transitions an aggregate reports to the
`Collector`, which alone logs and ledgers them.

Invariant: an event exists only per state transition (a sequence break, a pending-delta overflow,
a crossed episode ending), never per delta or trade, so the hot path allocates none in the steady
state. Every type here has a consumer in `collector_core.collector`.
"""

from dataclasses import dataclass

from collector_core.domain.policies import SequenceVerdict
from collector_core.domain.verdicts import DroppedLevel


@dataclass(frozen=True, slots=True)
class SequenceBroken:
    """A `SequenceCanary` found a gap or regress: the message was dropped, the book cleared."""

    u: int
    last: int | None
    verdict: SequenceVerdict


@dataclass(frozen=True, slots=True)
class PendingOverflow:
    """Venue mode: held deltas outlived the MEM-02 bound; the book and its pending were dropped."""

    held: int
    held_ns: int


@dataclass(frozen=True, slots=True)
class BookUncrossed:
    """
    A crossed episode ended: by itself (`dropped` empty) or because the policy deleted stale
    levels. `since_ns`/`was` are None when the policy uncrossed a book at its first crossed sample.
    """

    since_ns: int | None
    was: tuple[float, float] | None
    bid: float | None
    ask: float | None
    dropped: tuple[DroppedLevel, ...] = ()
