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
"""The ports capture declares: what a downstream context must offer for capture to feed it."""

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from kernel.second_snapshot import SecondRow


class SecondSink(Protocol):
    """
    Where capture hands the 1 s rows it has just archived.

    Invariant (a derived reader is never ahead of the archive): `apply` is called from `_flush_once`
    only with the rows whose `ParquetDataCatalog.write_data` returned, never with the buffer -- so
    nothing downstream can hold a second the Parquet archive does not. `watermarks` closes the other
    half: at startup capture reads each instrument's last applied `ts_event` and replays the archive
    from there, so a crash between a flush and its sink write is filled rather than lost forever.
    The commands that would violate it are applying `self._buffer` instead of `flushed_seconds`, and
    skipping the catch-up on the argument that the live feed will fill in anyway.

    Declared here, in capture, and satisfied structurally: the implementation (today
    `candles.application.sink.CandleSink`) never imports this module, so the dependency arrow runs
    capture -> port <- candles and no capture -> candles import exists (spine AD-D2/AD-D8).

    `apply` returns the number of seconds it actually took (rows at or before its own watermark are
    a replay and count 0). It may raise: capture ledgers the failure per instrument and carries on.
    """

    def apply(self, instrument_id: str, rows: Sequence[SecondRow]) -> int: ...

    def watermarks(self) -> Mapping[str, int]: ...


class PlanDiff(Protocol):
    """
    What `Collector.apply` is asked to change in the collected set (spine AD-D17).

    Invariant (capture never re-derives the plan): `added` and `removed` are disjoint and name
    exactly the ids whose planned status changed, and `store_deltas` is the complete post-change
    set of ids whose raw `OrderBookDeltas` are archived -- not a delta of it -- so applying one
    diff is enough to know the whole delta-storage rule. The command that could violate it is a
    caller building a diff by hand from two id lists that were read at different times; the one
    producer is `collection_control.domain.plan.CollectionPlan`'s commands (and, for capture's own
    initial apply and the static venues, `PlanChange`). Declared here, in capture, so capture never
    imports `collection_control`: the plan's `PlanDiff` satisfies it structurally.
    """

    @property
    def added(self) -> frozenset[str]: ...

    @property
    def removed(self) -> frozenset[str]: ...

    @property
    def store_deltas(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class PlanChange:
    """
    Capture's own concrete `PlanDiff`: the initial apply of a plan's ids at `run()`, and the static
    Bybit/Hyperliquid plans, which have no control plane to produce one.
    """

    added: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()
    store_deltas: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Applied:
    """
    What one `Collector.apply` actually did on the wire: the ids now subscribed, the ids now
    unsubscribed, and the ids whose subscribe or unsubscribe failed (ledgered, retried by capture's
    own retry loop, or -- an id the venue does not list -- never retried). The three are disjoint.
    """

    subscribed: frozenset[str] = frozenset()
    unsubscribed: frozenset[str] = frozenset()
    failed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CaptureStatus:
    """
    Capture's read-only view for `collector:status` (AD-D17: the plan is the intent, the applied
    set is the fact).

    Invariant: `applied` is only ids whose subscribe succeeded and that are still planned, and
    `pending` is the planned ids that are not applied -- so a status row can never show as
    collected an id the feed never subscribed. `lingering` is the reverse gap: ids no longer
    planned that are still subscribed because their unsubscribe failed (retried) -- they hold venue
    wire slots the plan's cap cannot see. Built by `Collector.capture_status()` from its own sets
    at one instant; the counters are copies, so a reader cannot mutate capture's state.
    """

    applied: frozenset[str]
    pending: frozenset[str]
    last_book_update_ns: Mapping[str, int]
    trade_backfill: Mapping[str, int]
    lingering: frozenset[str] = frozenset()
