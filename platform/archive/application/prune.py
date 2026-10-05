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
Execute the retention decision: the one place a catalog file is deleted (Story 25.1).

Lists the leaves the policy's rules can touch, reads each old trade day's status through the
`VerifiedDays` port (AD-D9: never a store connection of its own), asks
`archive.domain.retention.RetentionPolicy`, then executes: a deleted `trade_tick` file is first
recorded as a `pruned` archive gap (`GapMarkers`, over its name span widened by the skew bound:
`pruned_marker_span`) -- the rebuild must keep those rows' live values from then on, since an
older unverified file can keep `covered_from` reaching back past it -- and only then removed
through `CatalogWriter.delete`. This module is the only caller of `delete`
(`platform/archive/tests/test_one_deleter_one_rewriter.py`).

A venue whose status store cannot be read (`sqlite3.DatabaseError`: a corrupt or non-database
`candles_<venue>.db`) does not abort the run: it is ledgered once at `prune.verified_days`, that
venue is not queried again, and its days stay provisional -- kept, reason `status_unreadable` --
while the other venues are decided normally. Keeping is the fail-safe direction, and such a day is
a finding (`PruneReport.has_findings`, exit 2), never a silent keep.
"""

import logging
import sqlite3
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from candles.application.verified_days import VerifiedDays
from kernel.archive_markers import ArchiveGap
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import CatalogFileSpan
from kernel.venues import MalformedInstrumentId
from kernel.venues import has_venue
from kernel.venues import venue_of
from observability import error_ledger

from archive.application.ports import CatalogWriter
from archive.application.ports import GapMarkers
from archive.application.ports import OpenDayWriteError
from archive.domain.archive_day import ArchiveDay
from archive.domain.retention import TRADE_TICK
from archive.domain.retention import CatalogFile
from archive.domain.retention import Deletion
from archive.domain.retention import RetentionDecision
from archive.domain.retention import RetentionPolicy
from archive.domain.retention import day_text


logger = logging.getLogger(__name__)

_MB = 1024 * 1024

# The kept reason of a day whose venue's status store could not be read (DW-188).
STATUS_UNREADABLE = "status_unreadable"


@dataclass
class PruneReport:
    """Files (and bytes) deleted -- or deletable, report only -- per rule, and what was kept."""

    files: Counter[str] = field(default_factory=Counter)
    freed: Counter[str] = field(default_factory=Counter)
    kept: list[tuple[str, str, str]] = field(default_factory=list)
    open_day: int = 0
    errors: int = 0
    marker_failed: int = 0

    def has_findings(self) -> bool:
        """
        Whether a decided deletion did not happen, or a venue's day statuses could not be read
        (both ledgered): the run ends with findings.
        """
        unreadable = any(reason == STATUS_UNREADABLE for _iid, _day, reason in self.kept)
        return bool(self.open_day or self.errors or self.marker_failed or unreadable)


def _span(path: Path) -> tuple[int, int] | None:
    try:
        span = CatalogFileSpan.from_path(path)
    except ValueError:
        return None
    return span.start_ns, span.end_ns


def pruned_marker_span(span: tuple[int, int]) -> tuple[int, int]:
    """
    Return the `ts_event` span a deleted trade file's `pruned` marker covers: its `ts_init` name
    span widened by `MAX_TS_INIT_SKEW_NS` on both sides (the start clamped at 0).

    The marker is read on `ts_event` (`Coverage`), and a trade's `ts_event` can precede its
    `ts_init` by up to the bound -- or, from a venue clock running ahead, follow it -- so the raw
    name span would leave rows just outside it "covered" after their trades were deleted. The
    wider span only keeps more rows' live values; it cannot explain away a missing trade (a
    `pruned` marker's count is 0). The ahead side holds only as far as the bound does
    (`kernel.clocks.MAX_TS_INIT_SKEW_NS`'s Known limit).
    """
    start, end = span
    return max(0, start - MAX_TS_INIT_SKEW_NS), end + MAX_TS_INIT_SKEW_NS


def _wanted(policy: RetentionPolicy, data_type: str, iid: str) -> bool:
    """Whether any rule of `policy` can touch this leaf (the others are not even listed)."""
    dydx_plan = policy.plan is not None and has_venue(iid, "DYDX")
    if data_type == TRADE_TICK:  # listed for the plan too: today's trades mean "captured now"
        return policy.trade_retention_days is not None or dydx_plan
    return data_type in policy.age_types or dydx_plan


def _leaves(root: Path, policy: RetentionPolicy, venue: str | None) -> Iterator[Path]:
    """Every `data/<type>/<iid>` leaf of `venue` (all when None) that a rule of `policy` can touch."""
    type_dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
    for type_dir in type_dirs:
        yield from (
            leaf
            for leaf in sorted(d for d in type_dir.iterdir() if d.is_dir())
            if (venue is None or has_venue(leaf.name, venue))
            and _wanted(policy, type_dir.name, leaf.name)
        )


def list_files(
    catalog_path: str, policy: RetentionPolicy, venue: str | None = None
) -> Iterator[CatalogFile]:
    """Every `.parquet` file of the leaves `policy` can touch, of `venue` when given."""
    root = Path(catalog_path) / "data"
    for data_type in sorted(policy.age_types):
        if not (root / data_type).exists():
            logger.info("  %s: directory not found, skipping", data_type)
    for leaf in _leaves(root, policy, venue):
        for path in sorted(leaf.glob("*.parquet")):
            yield CatalogFile(leaf.parent.name, leaf.name, str(path), _span(path))


def _warn_unparsable_ids(files: list[CatalogFile]) -> None:
    """
    Warn about every trade leaf that is not an instrument id: it has no venue, so no candle store
    can prove its days -- warned up front (a leaf with no old day would otherwise lose the warning), its files retained.
    """
    for iid in sorted({f.iid for f in files if f.data_type == TRADE_TICK}):
        try:
            venue_of(iid)
        except MalformedInstrumentId:
            logger.warning("  skipped %s: not an instrument id, its files are never pruned", iid)


def _read_status(
    verified: VerifiedDays, venue: str, iid: str, day: str, unreadable: set[str]
) -> str | None:
    """
    Return that day's stored status; re-raise `sqlite3.DatabaseError` for a venue whose store
    cannot be read, ledgered and added to `unreadable` so it is not queried again this run.
    """
    try:
        return verified.verified_status(iid, day)
    except sqlite3.DatabaseError as e:  # OperationalError included: corrupt, locked, not a db
        error_ledger.record(
            "prune.verified_days",
            f"{venue} store unreadable ({e!r}); its days stay unverified, files kept",
            exc=e,
        )
        unreadable.add(venue)
        raise


def _resolve(
    verified: VerifiedDays | None, iid: str, text: str, unreadable: set[str]
) -> tuple[ArchiveDay, str | None]:
    """One instrument-day's `ArchiveDay`, and the kept reason when it is provisional by fault."""
    provisional = ArchiveDay("", iid, text)
    try:
        venue = venue_of(iid)
    except MalformedInstrumentId:
        return provisional, None  # warned up front (`_warn_unparsable_ids`)
    if venue in unreadable:
        return provisional, STATUS_UNREADABLE
    try:
        status = (
            _read_status(verified, venue, iid, text, unreadable) if verified is not None else None
        )
    except sqlite3.DatabaseError:
        return provisional, STATUS_UNREADABLE
    try:
        return ArchiveDay.from_verified_status(venue, iid, text, status), None
    except ValueError:
        return provisional, f"unknown_status:{status}"


def day_statuses(
    verified: VerifiedDays | None, needed: set[tuple[str, int]]
) -> tuple[dict[tuple[str, int], ArchiveDay], dict[tuple[str, str], str]]:
    """
    Each needed (instrument, day)'s `ArchiveDay`, from its `verified_status`; plus the (instrument,
    day text) -> kept reason of every day left provisional because its status could not be used:
    `unknown_status:<s>` for a status string the state machine does not know, `status_unreadable`
    for a venue whose store raised (ledgered once per venue at `prune.verified_days`). Either way
    the day's files are kept and the run goes on.
    """
    days: dict[tuple[str, int], ArchiveDay] = {}
    reasons: dict[tuple[str, str], str] = {}
    unreadable: set[str] = set()
    for iid, day in sorted(needed):
        text = day_text(day)
        days[(iid, day)], reason = _resolve(verified, iid, text, unreadable)
        if reason is not None:
            reasons[(iid, text)] = reason
    return days, reasons


def decide(
    catalog_path: str,
    policy: RetentionPolicy,
    verified: VerifiedDays | None,
    venue: str | None = None,
) -> RetentionDecision:
    """List, read the statuses the policy needs, and ask it (nothing is deleted here)."""
    files = list(list_files(catalog_path, policy, venue))
    _warn_unparsable_ids(files)
    days, reasons = day_statuses(verified, policy.status_days(files))
    decision = policy.decide(files, days)
    for skipped in decision.unparsable:
        logger.warning("  skipped %s: not a catalog file name, never pruned", skipped.path)
    kept = [(iid, day, reasons.get((iid, day), reason)) for iid, day, reason in decision.kept]
    return RetentionDecision(decision.delete, kept, decision.unparsable)


def _delete(
    deletion: Deletion, span: tuple[int, int], writer: CatalogWriter, markers: GapMarkers
) -> str | None:
    """Delete one file; return the kept reason when it must stay instead (None: deleted)."""
    f, path = deletion.file, Path(deletion.file.path)
    # Checked before the marker, so a refused file leaves no `pruned` gap behind.
    writer.assert_span_closed(*span)
    if f.data_type == TRADE_TICK and not markers.record(
        ArchiveGap(f.iid, *pruned_marker_span(span), "pruned", 0)
    ):
        # The marker is what keeps a later rebuild from zeroing these rows' live values: without
        # it on disk the file stays (the failure is already ledgered, `archive_gaps.write`).
        return "marker_failed"
    writer.delete(path)
    return None


def _execute_one(
    deletion: Deletion,
    writer: CatalogWriter | None,
    markers: GapMarkers,
    report: PruneReport,
) -> None:
    """Delete (or, without a writer, report) one file, counting it in `report`; never raises."""
    path, f = Path(deletion.file.path), deletion.file
    if f.span is None:  # the policy never decides an unparsable name
        return
    apply = writer is not None
    try:
        size = path.stat().st_size
        kept = _delete(deletion, f.span, writer, markers) if writer is not None else None
    except OpenDayWriteError as e:
        error_ledger.record("prune.open_day", f"{path}: {e}; kept for the next nightly")
        report.open_day += 1
        return
    except OSError as e:  # FileNotFoundError included: gone, unreadable or undeletable
        error_ledger.record("prune.error", f"{path}: {e!r}; skipped", exc=e)
        report.errors += 1
        return
    if kept is not None:
        report.kept.append((f.iid, day_text(f.span[0] // NS_PER_DAY), kept))
        if kept == "marker_failed":
            report.marker_failed += 1
        return
    report.files[deletion.rule] += 1
    report.freed[deletion.rule] += size
    verb = "deleted" if apply else "[report only] would delete"
    logger.info(
        "  %s %s (%d KB) [%s: %s]", verb, path, size // 1024, deletion.rule, deletion.reason
    )


def execute(
    decision: RetentionDecision, writer: CatalogWriter | None, markers: GapMarkers
) -> PruneReport:
    """
    Carry out the decision through `writer` -- or, with None (a report-only run, which takes no
    maintenance lock), only report it; returns what it freed and kept.
    """
    report = PruneReport(kept=list(decision.kept))
    for deletion in decision.delete:
        _execute_one(deletion, writer, markers, report)
    for iid, day, reason in report.kept:
        logger.info("  kept %s %s: %s", iid, day, reason)
    return report


def log_summary(report: PruneReport, policy: RetentionPolicy, apply: bool) -> None:
    """One summary line per active rule, in the shape the operator's logs have always had."""
    done = "deleted" if apply else "deletable (report only)"
    if policy.age_types:
        verb = "freed" if apply else "would free"
        logger.info("%s %.1f MB", verb, report.freed["age"] / _MB)
    if policy.trade_retention_days is not None:
        logger.info(
            "trade retention %d days: %d file(s) %s (%.1f MB), %d old instrument-day(s) kept",
            policy.trade_retention_days,
            report.files["trade"],
            done,
            report.freed["trade"] / _MB,
            len(report.kept),
        )
    if policy.plan is not None:
        logger.info(
            "dropped-instrument retention %.1f h: %d file(s) %s (%.1f MB); per-coin raw-delta "
            "retention: %d file(s) %s (%.1f MB)",
            policy.plan.non_config_retain_hours,
            report.files["dropped_instrument"],
            done,
            report.freed["dropped_instrument"] / _MB,
            report.files["delta_retention"],
            done,
            report.freed["delta_retention"] / _MB,
        )
    logger.info(
        "prune: %d file(s) skipped for the open UTC day, %d per-file error(s), %d kept for a "
        "failed pruned-marker write",
        report.open_day,
        report.errors,
        report.marker_failed,
    )
