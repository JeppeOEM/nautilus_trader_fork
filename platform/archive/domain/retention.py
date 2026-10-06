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
Which catalog files leave the archive (story 22.13, Story 25.1): the one retention decision.

Four rules, each naming itself on every deletion it makes:

- `trade` -- a `trade_tick/` file goes only when every UTC day its name spans is older than the
  trade retention window **and** that instrument-day is `verified` (its `verified_days` row is
  `pass`). The skew between a trade's `ts_event` and the `ts_init` the name spans runs either way,
  so a file starting within `MAX_TS_INIT_SKEW_NS` after midnight also needs the previous day
  proven, and one ending within it before midnight the next day: its trades' `ts_event` can
  belong to them. Each old day still unproven is kept and reported (`unverified` or `failed`).
- `age` -- the `--types T --days N` plain age retention: files of those types ending over N days
  ago. Never `trade_tick` (refused by the CLI). It cedes to the plan (DW-215): an
  `order_book_deltas` file of a dYdX instrument the plan gives a delta retention entry (finite or
  `None`) is never chosen by it -- `delta_retention` alone decides those, so an unlimited entry's
  deltas are never cut at N days, nor a finite entry longer than N days.
- `dropped_instrument` -- a dYdX leaf whose instrument the collection plan no longer collects:
  every type except `trade_tick` (the set `prune_instrument` covered), ending more than
  `non_config_retain_hours` ago (MEM-02: an uncollected coin's data ages out). Its
  instrument-definition files (`kernel.catalog_files.DEFINITION_DIRNAMES`) wait for its trades
  (DW-208): kept while any `trade_tick` file of the instrument is in the listing -- parsable or
  not, and including one the `trade` rule deletes in the same run -- because a trade day still
  stored may yet need reconciling (a rebuild or repair can clear its verdict), and that needs
  the definition. They go on the first run after every trade day is released. A trade file the
  trade rule never releases (a `failed` day, a name that does not parse, or a run without the
  trade rule, e.g. `make prune`) holds them as long as it stays: the fail-safe direction, a few
  small files per coin, reported `definition_held_by_trades` on every run.
- `delta_retention` -- a collected dYdX instrument with `store_order_book_deltas` and a finite
  `retain_hours`: its `order_book_deltas` files ending more than that many hours ago. `None`
  means unlimited, never pruned.

Invariant: a file whose span reaches the current UTC day is never chosen (capture is still
writing that day; the next nightly takes it), and a file whose name does not parse as a catalog
span is never chosen either -- it is kept and reported. The policy is pure: the application lists
the leaves, reads day statuses through the `VerifiedDays` port and executes the deletions.

Known limit: retention runs only in the nightly, on closed UTC days, so the effective floor of a
`retain_hours`/`non_config_retain_hours` is "the day closes, plus the next nightly" -- about 24 h
worst case -- where the deleted 15-minute `DydxCollector._prune_loop` pruned intra-day. Upgrade
path: a second, capture-exclusive retention pass over closed files, run more often than nightly.
"""

import math
import time
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field

from kernel.catalog_files import DEFINITION_DIRNAMES
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.venues import has_venue

from archive.domain.archive_day import ArchiveDay
from archive.domain.archive_day import DayStatus


TRADE_TICK = "trade_tick"
ORDER_BOOK_DELTAS = "order_book_deltas"
_HOUR_NS = 3_600 * NS_PER_S
# The kept reason of a dropped coin's old definition file held back by its trade files (DW-208).
DEFINITION_HELD = "definition_held_by_trades"


@dataclass(frozen=True)
class CatalogFile:
    """One `data/<type>/<iid>/<name>.parquet` file; `span` is None when the name did not parse."""

    data_type: str
    iid: str
    path: str
    span: tuple[int, int] | None


@dataclass(frozen=True)
class PlanRetention:
    """
    The dYdX collection plan's retention attributes (read from its `config.toml`).

    `collected` are the ids the plan collects; `delta_retain_hours` maps each instrument storing
    raw order-book deltas to its `retain_hours` (None: unlimited).
    """

    collected: frozenset[str]
    non_config_retain_hours: float
    delta_retain_hours: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a window that is not a finite, non-negative number of hours (`ValueError`)."""
        windows = {"non_config_retain_hours": self.non_config_retain_hours}
        windows |= {
            f"{iid} retain_hours": h for iid, h in self.delta_retain_hours.items() if h is not None
        }
        bad = {name: h for name, h in windows.items() if not (math.isfinite(h) and h >= 0)}
        if bad:
            raise ValueError(f"retention windows must be finite hours >= 0: {bad}")


@dataclass(frozen=True)
class Deletion:
    """One file to delete, the rule that chose it and why."""

    file: CatalogFile
    rule: str  # "trade" | "age" | "dropped_instrument" | "delta_retention"
    reason: str


@dataclass(frozen=True)
class RetentionDecision:
    """What leaves the archive, and the old (instrument, day, reason) entries kept."""

    delete: list[Deletion]
    kept: list[tuple[str, str, str]]
    unparsable: list[CatalogFile]


def day_text(day: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day * 86_400))


def file_days(span: tuple[int, int]) -> range:
    """
    UTC day indices whose trades a `trade_tick` file can hold. The name spans `ts_init`, and a
    trade's `ts_event` can differ from it by up to the skew bound in either direction: it trails
    our clock on arrival, and leads it when the venue clock runs ahead. So a file starting within
    that bound after midnight can hold trades of the previous day, one ending within it before
    midnight trades of the next day, and each such day must also be proven. (The ahead side is
    bounded by detection, not refusal: `kernel.clocks.MAX_TS_INIT_SKEW_NS`'s Known limit.)
    """
    start, end = span
    first_day = (start - MAX_TS_INIT_SKEW_NS) // NS_PER_DAY
    last_day = (end + MAX_TS_INIT_SKEW_NS) // NS_PER_DAY
    return range(first_day, last_day + 1)


@dataclass(frozen=True)
class RetentionPolicy:
    """
    Decide which catalog files are deleted (the one retention decision, Story 25.1).

    Invariant: a file is deleted only by one of the four rules of this module, never while its
    span reaches the current UTC day, and a `trade_tick` file only once every day it can hold is
    `verified` (`ArchiveDay.released`). The command that could violate it is `decide`; the only
    executor of its result is `archive.application.prune`.

    `trade_retention_days` None disables the trade rule, `age_types` empty the age rule, `plan`
    None the two plan rules. Two orderings between rules: the age rule never takes a plan-governed
    dYdX delta file (DW-215: the plan wins), and the dropped-instrument rule never takes a
    definition file of an instrument with a `trade_tick` file in the listing (DW-208: definitions
    outlive the trade days that need them).
    """

    now_ns: int
    trade_retention_days: int | None = None
    age_types: frozenset[str] = frozenset()
    age_days: int = 14
    plan: PlanRetention | None = None

    def status_days(self, files: Iterable[CatalogFile]) -> set[tuple[str, int]]:
        """Return the (instrument, day) statuses `decide` needs: each day of each old trade file."""
        return {
            (f.iid, day)
            for f in files
            if f.span is not None and self._old_trade_file(f.data_type, f.span)
            for day in file_days(f.span)
        }

    def decide(
        self, files: Iterable[CatalogFile], days: Mapping[tuple[str, int], ArchiveDay]
    ) -> RetentionDecision:
        """
        Apply the rules to `files`; `days` holds the `ArchiveDay` of each `status_days` entry
        (a missing one is provisional, i.e. unverified).
        """
        listed = list(files)
        live = self._captured_now(listed)
        # Every listed trade file counts, unparsable or deleted by this very run: the definition
        # then goes next run, after the deletion executed (fail-safe if it did not).
        traded = {f.iid for f in listed if f.data_type == TRADE_TICK}
        delete: list[Deletion] = []
        kept: set[tuple[str, str, str]] = set()
        unparsable: list[CatalogFile] = []
        for f in listed:
            span = f.span
            if span is None:
                unparsable.append(f)
                continue
            if self._reaches_today(span):
                continue
            chosen = (
                self._trade(f, span, days, kept)
                if f.data_type == TRADE_TICK
                else self._other(f, span[1], f.iid in live)
            )
            if chosen is not None and not _held(chosen, traded, kept):
                delete.append(chosen)
        return RetentionDecision(delete, sorted(kept), unparsable)

    def _captured_now(self, files: list[CatalogFile]) -> set[str]:
        """
        Instruments with a file of any type reaching the current UTC day: capture writes them now.

        The dropped-instrument rule never applies to these, whatever the plan says: the plan file
        is read while control may be rewriting it (`collection_control`'s `TomlPlanStore.save`
        truncates and rewrites the bind-mounted file in place -- a single-file bind mount cannot be
        renamed over, so the write cannot be made atomic), and a torn or partial read would
        otherwise list a collected instrument as dropped and delete its history.
        """
        return {f.iid for f in files if f.span is not None and self._reaches_today(f.span)}

    def _reaches_today(self, span: tuple[int, int]) -> bool:
        return span[1] // NS_PER_DAY >= self.now_ns // NS_PER_DAY

    def _old_trade_file(self, data_type: str, span: tuple[int, int]) -> bool:
        if data_type != TRADE_TICK or self.trade_retention_days is None:
            return False
        return file_days(span)[-1] < self.now_ns // NS_PER_DAY - self.trade_retention_days

    def _trade(
        self,
        f: CatalogFile,
        span: tuple[int, int],
        days: Mapping[tuple[str, int], ArchiveDay],
        kept: set[tuple[str, str, str]],
    ) -> Deletion | None:
        if not self._old_trade_file(f.data_type, span):
            return None
        # A day the caller did not look up is unverified (provisional), never assumed proven.
        spanned = [
            days.get((f.iid, d)) or ArchiveDay("", f.iid, day_text(d)) for d in file_days(span)
        ]
        unproven = [d for d in spanned if d.status is not DayStatus.VERIFIED]
        for day in unproven:
            reason = "failed" if day.status is DayStatus.MISMATCHED else "unverified"
            kept.add((f.iid, day.day, reason))
        if unproven:
            return None
        for day in spanned:
            day.released()  # every spanned day is verified: raises otherwise
        reason = f"verified and older than {self.trade_retention_days} days"
        return Deletion(f, "trade", reason)

    def _other(self, f: CatalogFile, end: int, captured_now: bool) -> Deletion | None:
        if self._aged(f, end):
            return Deletion(f, "age", f"{f.data_type} older than {self.age_days} days")
        if self.plan is None or not has_venue(f.iid, "DYDX"):
            return None
        if f.iid not in self.plan.collected:
            return None if captured_now else self._dropped(f, end, self.plan)
        return self._delta(f, end, self.plan)

    def _aged(self, f: CatalogFile, end: int) -> bool:
        if f.data_type not in self.age_types or end >= self.now_ns - self.age_days * NS_PER_DAY:
            return False
        return not self._plan_governs_deltas(f)

    def _plan_governs_deltas(self, f: CatalogFile) -> bool:
        """Tell a dYdX delta file whose instrument has a plan delta entry (`delta_retention` only)."""
        return (
            self.plan is not None
            and f.data_type == ORDER_BOOK_DELTAS
            and has_venue(f.iid, "DYDX")
            and f.iid in self.plan.delta_retain_hours
        )

    def _dropped(self, f: CatalogFile, end: int, plan: PlanRetention) -> Deletion | None:
        hours = plan.non_config_retain_hours
        if end < self.now_ns - int(hours * _HOUR_NS):
            return Deletion(f, "dropped_instrument", f"not collected, older than {hours} h")
        return None

    def _delta(self, f: CatalogFile, end: int, plan: PlanRetention) -> Deletion | None:
        hours = plan.delta_retain_hours.get(f.iid)  # None: unlimited
        if f.data_type != ORDER_BOOK_DELTAS or hours is None:
            return None
        if end < self.now_ns - int(hours * _HOUR_NS):
            return Deletion(f, "delta_retention", f"deltas older than {hours} h")
        return None


def _held(chosen: Deletion, traded: set[str], kept: set[tuple[str, str, str]]) -> bool:
    """
    Whether a dropped coin's definition file the window releases is held back by the coin's trade
    files (DW-208: a stored trade day may still need reconciling against it); a held file is
    reported in `kept`, so an operator sees why it stays. Keyed by the file's first day, as
    `archive.application.prune` keys a file it keeps at execution.
    """
    f = chosen.file
    if chosen.rule != "dropped_instrument" or f.data_type not in DEFINITION_DIRNAMES:
        return False
    if f.iid not in traded or f.span is None:
        return False
    kept.add((f.iid, day_text(f.span[0] // NS_PER_DAY), DEFINITION_HELD))
    return True
