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
One instrument-day's archive status (DDD spine AD-D9): provisional -> rebuilt -> verified or
mismatched -> released.

The only persisted status is the candle store's `verified_days` row (`"pass"`/`"fail"`, reached
through `candles.application.verified_days.VerifiedDays`); `rebuilt` is never stored. It exists
only inside one nightly saga run, carried as the rebuild step's `RebuildProof`, so a reconcile can
only ever judge a day this very run rebuilt.

Deliberate refinement of the spine's diagram: `verified -> rebuilt` is legal, exactly like
`mismatched -> rebuilt`. A rerun of the saga re-derives a verified day's seconds, and the stored
`pass` must then be proven again within that same run. A day becomes `verified` -- the only
status `RetentionPolicy` releases trades for -- solely through reconcile's exact verdict
(`reconciled`), and `verified_days` changes only through that verdict or its clear.

Invalidation (DW-203): a writer that changes a closed day's seconds -- the rebuild
(`archive.application.rebuild_day`), the repair (`archive.application.repair`) -- clears that
instrument-day's stored verdict (`VerifiedDays.clear_verified`) *before* the change lands, so the
day is provisional again until a reconcile of a rebuild in one run judges it. Any outcome after
the clear -- a reconcile that errors or is skipped, a stopped saga, a standalone rebuild or repair,
a failed commit -- leaves the day unverified and its raw trades kept, never a stale `pass` the
prune could release them on (save the in-flight reconcile race in the Known limit below). Nothing re-judges such a day automatically (the scheduler's watermark
has passed it): it stays unverified until a rerun of the saga for that day (`make nightly
VENUE=<v> DAY=<day>`, `python -m archive.nightly --day`). A rebuild that changes no row of the day
clears nothing.

Known limit: `compare_klines` writes its verdict without the catalog maintenance lock, so a repair
or rebuild clearing a day while a reconcile of that same day is in flight (an operator repair, or
a standalone `rebuild_seconds --apply`, during a nightly's compare step) can have the reconcile's `pass`, judged on the old bars, land
after the clear. Upgrade path: the reconcile writes its verdict under the maintenance lock, or a
compare-and-set on a per-day rebuild generation.
"""

import enum
from dataclasses import dataclass
from types import MappingProxyType

from archive.domain.reconciliation import ReconciliationResult


class DayStatus(enum.Enum):
    """Where one instrument-day stands; only `verified`/`mismatched` are ever persisted."""

    PROVISIONAL = "provisional"
    REBUILT = "rebuilt"
    VERIFIED = "verified"
    MISMATCHED = "mismatched"
    RELEASED = "released"


# `verified_days.status` <-> the persisted states (the frozen `pass`/`fail` strings).
_FROM_VERIFIED = MappingProxyType({"pass": DayStatus.VERIFIED, "fail": DayStatus.MISMATCHED})
_REBUILDABLE = frozenset({DayStatus.PROVISIONAL, DayStatus.MISMATCHED, DayStatus.VERIFIED})


class IllegalTransition(Exception):
    """A command the day's current status does not allow; nothing changed."""


@dataclass(frozen=True)
class RebuildProof:
    """
    What one saga run's rebuild step proved for (`venue`, `day`): the instruments it `rebuilt`,
    and the ones it refused (`not_rebuilt`).

    Invariant: a reconcile verdict is written only for an instrument this proof `covers`, and
    `covers` is an allowlist -- named in `rebuilt` and not in `not_rebuilt`. An instrument the
    rebuild never processed (a leaf that appeared between the steps, an instrument-day with no
    snapshot row, which the rebuild reports as neither rebuilt nor refused) is not covered, so it
    can never be judged on seconds nobody re-derived. The proof is never persisted -- it crosses
    from the rebuild child to the saga once, as the `--result-file` JSON, and on to the reconcile
    child as `--rebuilt-by`/`--rebuilt`/`--not-rebuilt` argv.
    """

    run_id: str
    venue: str
    day: str  # YYYY-MM-DD (UTC)
    rebuilt: frozenset[str] = frozenset()
    not_rebuilt: frozenset[str] = frozenset()

    def covers(self, iid: str) -> bool:
        return iid in self.rebuilt and iid not in self.not_rebuilt


@dataclass(frozen=True)
class ArchiveDay:
    """
    One instrument-day of the archive and its status.

    Invariant (AD-D9): a day is `verified` only after an exact reconciliation of a rebuild of that
    same (venue, day, instrument) in the same run, and `released` only from `verified`. Commands:
    `rebuilt` (needs a covering `RebuildProof`), `reconciled` (needs `rebuilt`), `released` (needs
    `verified`); any other move raises `IllegalTransition`.
    """

    venue: str
    instrument: str
    day: str  # YYYY-MM-DD (UTC)
    status: DayStatus = DayStatus.PROVISIONAL

    @classmethod
    def from_verified_status(
        cls, venue: str, iid: str, day: str, status: str | None
    ) -> "ArchiveDay":
        """Build the day from its `verified_days` status: None provisional, pass/fail stored."""
        if status is None:
            return cls(venue, iid, day)
        if status not in _FROM_VERIFIED:
            raise ValueError(f"{iid} {day}: unknown verified_days status {status!r}")
        return cls(venue, iid, day, _FROM_VERIFIED[status])

    def rebuilt(self, proof: RebuildProof) -> "ArchiveDay":
        if self.status not in _REBUILDABLE:
            raise IllegalTransition(f"{self._label()}: cannot rebuild a {self.status.value} day")
        if (proof.venue, proof.day) != (self.venue, self.day) or not proof.covers(self.instrument):
            raise IllegalTransition(f"{self._label()}: run {proof.run_id} did not rebuild it")
        return self._to(DayStatus.REBUILT)

    def reconciled(self, result: ReconciliationResult) -> "ArchiveDay":
        if self.status is not DayStatus.REBUILT:
            raise IllegalTransition(
                f"{self._label()}: cannot reconcile a {self.status.value} day (not rebuilt)"
            )
        if result.iid != self.instrument or result.status not in ("pass", "fail"):
            raise IllegalTransition(f"{self._label()}: {result.status} result for {result.iid}")
        exact = result.status == "pass" and not result.mismatches
        return self._to(DayStatus.VERIFIED if exact else DayStatus.MISMATCHED)

    def released(self) -> "ArchiveDay":
        if self.status is not DayStatus.VERIFIED:
            raise IllegalTransition(f"{self._label()}: cannot release a {self.status.value} day")
        return self._to(DayStatus.RELEASED)

    def verified_status(self) -> str:
        """Return the `verified_days` string this status persists as (verified/mismatched only)."""
        if self.status is DayStatus.VERIFIED:
            return "pass"
        if self.status is DayStatus.MISMATCHED:
            return "fail"
        raise IllegalTransition(f"{self._label()}: a {self.status.value} day is never persisted")

    def _to(self, status: DayStatus) -> "ArchiveDay":
        return ArchiveDay(self.venue, self.instrument, self.day, status)

    def _label(self) -> str:
        return f"{self.instrument} {self.day}"
