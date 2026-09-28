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
The collection plan: what one venue intends to collect (DDD spine AD-D17).

The plan is the intent, capture's applied set is the fact: every command here returns a `PlanDiff`
that capture applies (`capture.application.capture_service.CaptureService.apply`) and reports back as `Applied`.
The plan itself never subscribes, samples or deletes anything.
"""

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from collection_control.domain.liquidity import LiquidityClassification


@dataclass(frozen=True)
class InstrumentEntry:
    """
    One collected instrument. Every entry is collected and pinned (Story 6.1): there is no
    "listed but not collected" state -- stopping collection removes the entry.

    Invariant: a non-empty id, and `retain_hours` (how long this instrument's raw order-book deltas
    are kept) is None -- unlimited, distinct from the plan's `non_config_retain_hours`, which only
    applies once an instrument is no longer collected -- or >= 0.
    """

    id: str
    store_order_book_deltas: bool = False
    retain_hours: float | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("an instrument entry needs a non-empty id")
        if self.retain_hours is not None and not (0 <= self.retain_hours < math.inf):
            raise ValueError(
                f"retain_hours must be >= 0 for instrument {self.id!r}, got {self.retain_hours}"
            )


class PlanRejected(ValueError):
    """A command the plan's invariants refuse: nothing changes (the caller logs a WARNING)."""


@dataclass(frozen=True)
class PlanDiff:
    """
    The result of one plan command: the plan after it, and the ids it added and removed.

    `store_deltas` is the complete post-change set of ids whose raw deltas are archived, so a
    `PlanDiff` satisfies capture's `capture.application.ports.PlanDiff` port structurally.
    """

    plan: "CollectionPlan"
    added: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()

    @property
    def store_deltas(self) -> frozenset[str]:
        return self.plan.delta_store_ids

    @property
    def changed(self) -> bool:
        """Whether the collected set changed (a subscribe or unsubscribe is due)."""
        return bool(self.added or self.removed)


@dataclass(frozen=True)
class CollectionPlan:
    """
    The aggregate for one venue's collected instruments (`venue` is the `kernel.venues` token).

    Invariants, asserted at construction, so no load, command or reload can produce a plan that
    breaks them (a violation raises `ValueError` naming the ids):
    - ids are unique;
    - `|collected| <= cap` (dYdX: 30, under its 32-per-channel WS limit; a static plan's cap is its
      own size);
    - `excluded & collected` is empty (`exclude` is the permanent denylist an unpin lands in);
    - a pin is admitted only through `pin`, from a USD-volume `LiquidityClassification` made at the
      plan's own `min_liquidity_usd` (OBS-03).
    Commands that could violate them -- `add` past the cap or of a collected id, `pin` from a
    classification at another threshold, an `exclude` that left the id collected -- are refused
    (`PlanRejected`) or keep the invariant by construction.
    """

    venue: str
    instruments: tuple[InstrumentEntry, ...]
    cap: int
    excluded: frozenset[str] = frozenset()
    # USD 24 h volume at which a market is liquid (the config key `liquidity_min_oi_usd`); None for
    # a plan that admits no pins (a static plan).
    min_liquidity_usd: float | None = None
    # How long a no-longer-collected instrument's data is kept (read by `archive.RetentionPolicy`).
    non_config_retain_hours: float | None = None

    def __post_init__(self) -> None:
        ids = [e.id for e in self.instruments]
        if self.cap < 0:
            raise ValueError(f"{self.venue} plan cap must be >= 0, got {self.cap}")
        repeated = sorted({iid for iid in ids if ids.count(iid) > 1})
        if repeated:
            raise ValueError(f"{self.venue} plan lists these ids more than once: {repeated}")
        if len(ids) > self.cap:
            raise ValueError(
                f"{self.venue} plan collects {len(ids)} instruments, above its cap of "
                f"{self.cap}: {ids[self.cap :]} do not fit"
            )
        both = sorted(self.excluded & set(ids))
        if both:
            raise ValueError(f"{self.venue} plan both collects and excludes {both}")
        if self.non_config_retain_hours is not None and not (
            0 <= self.non_config_retain_hours < math.inf
        ):
            raise ValueError(
                f"non_config_retain_hours must be >= 0, got {self.non_config_retain_hours}"
            )

    @property
    def collected(self) -> tuple[str, ...]:
        """The collected ids, in plan order (the order `collector:status` publishes them)."""
        return tuple(e.id for e in self.instruments)

    @property
    def pins(self) -> tuple[str, ...]:
        """Every collected id is pinned (Story 6.1): nothing is collected without being pinned."""
        return self.collected

    @property
    def free_slots(self) -> int:
        return self.cap - len(self.instruments)

    @property
    def delta_store_ids(self) -> frozenset[str]:
        return frozenset(e.id for e in self.instruments if e.store_order_book_deltas)

    @property
    def delta_retain_hours(self) -> Mapping[str, float | None]:
        """Raw-delta retention per delta-storing instrument (None = unlimited)."""
        return MappingProxyType(
            {e.id: e.retain_hours for e in self.instruments if e.store_order_book_deltas}
        )

    # -- commands ------------------------------------------------------------------------------

    def add(self, iid: str) -> PlanDiff:
        """Collect `iid` (collector:control `start`); lifts it out of `excluded`."""
        if iid in self.collected:
            raise PlanRejected(f"Cannot start {iid}: already collected")
        if self.free_slots <= 0:
            raise PlanRejected(f"Cannot start {iid}: at {self.cap}-instrument cap")
        plan = dataclasses.replace(
            self,
            instruments=(*self.instruments, InstrumentEntry(id=iid)),
            excluded=self.excluded - {iid},
        )
        return PlanDiff(plan, added=frozenset({iid}))

    def remove(self, iid: str) -> PlanDiff:
        """Stop collecting `iid` without excluding it (collector:control `stop`)."""
        if iid not in self.collected:
            raise PlanRejected(f"Cannot stop {iid}: not currently collected")
        return PlanDiff(self._without(iid), removed=frozenset({iid}))

    def exclude(self, iid: str) -> PlanDiff:
        """Add `iid` to the permanent denylist, and stop collecting it if it was collected."""
        plan = dataclasses.replace(self._without(iid), excluded=self.excluded | {iid})
        removed = frozenset({iid}) if iid in self.collected else frozenset()
        return PlanDiff(plan, removed=removed)

    def unpin(self, iid: str) -> PlanDiff:
        """Stop collecting `iid` and exclude it (collector:control `unpin`)."""
        if iid not in self.collected:
            raise PlanRejected(f"Cannot unpin {iid}: not currently collected")
        return self.exclude(iid)

    def pin(self, classification: LiquidityClassification) -> PlanDiff:
        """
        Fill the free slots with the classification's top-by-volume liquid ids that are neither
        collected nor excluded (collector:control `pin_top_liquid`). Additive only: never removes
        or replaces an entry. An excluded (e.g. unpinned) id is never re-added here -- that is
        always an explicit `add`.
        """
        if self.min_liquidity_usd is None:
            raise PlanRejected(f"{self.venue} plan has no liquidity threshold: it admits no pins")
        if classification.min_volume_usd != self.min_liquidity_usd:
            raise PlanRejected(
                f"a pin needs a classification at the plan's {self.min_liquidity_usd} USD, "
                f"got one at {classification.min_volume_usd} USD"
            )
        taken = set(self.collected) | self.excluded
        candidates = {
            iid: vol
            for iid, vol in classification.volumes.items()
            if iid in classification.liquid and iid not in taken
        }
        # Lowest volume dropped first, ties in the venue's order: the rule `classify_liquidity`
        # applies with `max_liquid`, so a pre-capped classification selects the same ids.
        dropped = sorted(candidates, key=candidates.__getitem__)[
            : max(0, len(candidates) - self.free_slots)
        ]
        top = sorted(set(candidates) - set(dropped))
        plan = dataclasses.replace(
            self, instruments=self.instruments + tuple(InstrumentEntry(id=iid) for iid in top)
        )
        return PlanDiff(plan, added=frozenset(top))

    def reload(self, new_plan: "CollectionPlan") -> PlanDiff:
        """Adopt a re-read plan (a hand-edited file): the diff is what the edit changed."""
        if new_plan.venue != self.venue:
            raise PlanRejected(f"cannot reload a {new_plan.venue} plan over a {self.venue} one")
        before, after = set(self.collected), set(new_plan.collected)
        return PlanDiff(
            new_plan, added=frozenset(after - before), removed=frozenset(before - after)
        )

    def _without(self, iid: str) -> "CollectionPlan":
        return dataclasses.replace(
            self, instruments=tuple(e for e in self.instruments if e.id != iid)
        )
