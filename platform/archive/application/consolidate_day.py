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
Merge each closed UTC day's many small Parquet files into one file per (data type, instrument).

Every collector (dYdX, Bybit, Hyperliquid) flushes once a minute into the one shared catalog, so
every instrument grows ~1,440 files per data type per day; reads, backups and inode counts all scale
with that file count, not with the data (audit D-36). This walks every `data/<type>/<instrument>`
leaf -- it has no venue-specific code -- and rewrites closed days only: today's files, still being
written, are never touched (and `CatalogWriter` refuses them).

It is plain pyarrow, not `ParquetDataCatalog.consolidate_data_by_period`: that built-in round-trips
every row through Python objects and `ArrowSerializer`, which raises NotImplementedError for the
Rust-native types here (MarkPriceUpdate, IndexPriceUpdate, FundingRateUpdate), decodes a coin-day of
20-level books into ~100 MB of objects, reads through the first-file-schema dataset that caused
D-24, and deletes the originals before anything can be verified. Here a day's files are required to
share one full schema -- names, types, nullability *and* Arrow metadata (mixed = refused, D-24).
Metadata matters: catalog files carry e.g. `price_precision`, and `pa.concat_tables` silently keeps
the first table's metadata, so merging two precisions would relabel part of the rows. The files are
concatenated as-is and handed to `CatalogWriter.write_merged` (temp file, read back and row-counted
against the sources, renamed into place), and only then are the sources removed
(`remove_merged_sources`: their rows remain, in the merged file -- this is not retention). A crash
between rename and removal leaves the sources covered by the big file; the next run detects that,
re-verifies the count and finishes the removal, so rows are never duplicated or lost. A crash
inside the write leaves a temp file, which the next --apply run deletes before touching that leaf.
One (type, instrument, day) in memory at a time (MEM-01). Report-only unless `apply`.

`bar` leaves are never consolidated: `backfill_bars` writes one file per contiguous run of venue
bars and plans its next fetch from the catalog's *file intervals*
(`get_missing_intervals_for_request`), so merging two runs of a day across a missing range would
name one file over the hole and seal it as covered forever (DATA-05). They are also few (one file
per backfill window), so they are not the D-36 problem.

`run_closed_hours` (Story 25.1b, the `archive` service's intraday merge) is the same
schema/covering/merge/verify path one period finer: over the small types only
(`archive.domain.intraday.INTRADAY_DATA_TYPES`), it merges each *closed hour* of the current UTC
day (`closed_hours_needing_work`) in `MergeScope.CLOSED_HOUR`, so a file reaching the current hour
is never read, written or removed. The nightly run later merges those hourly files into the day's
one file like any other files of a closed day.

Known limit: a file capture writes into an hour *after* that hour was merged (a flush delayed past
the intraday slot) lies inside the merged file's span, so the covering-file check refuses that
hour (`consolidate.row_count`, loud, nothing deleted). The nightly day merge usually absorbs it
(the day's covering span is then some other file's), but not when the hourly file alone spans the
whole day -- a sparse leaf, e.g. `instrument_status`, whose every row of the day lies in that one
hour: then the day is refused the same way every night, and the standalone consolidate fails
until an operator merges the late file by hand. The intraday slot runs at `nightly_at`'s minute
past the hour, several flush periods after the hour closed, so this needs a flush delayed by
minutes. Upgrade path: merge a late file into the existing merged file (a verified rewrite of it
plus the late one) instead of refusing, at both the hour and the day grain.
"""

import logging
import resource
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from kernel.clocks import CatalogFileSpan
from kernel.venues import has_venue
from observability import error_ledger

from archive.application.ports import CatalogWriter
from archive.application.ports import MergeScope
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteVerifyError
from archive.domain.intraday import INTRADAY_DATA_TYPES
from archive.domain.intraday import NS_PER_HOUR
from archive.domain.intraday import closed_hours_needing_work


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000
_MB = 1024 * 1024
# Data types whose file intervals are a coverage record, not just a listing (see module docstring).
_NEVER_CONSOLIDATED = frozenset({"bar"})


@dataclass
class RunStats:
    """What one run did, for its summary line and exit code."""

    days_done: int = 0
    days_refused: int = 0
    leaves_failed: int = 0
    files_before: int = 0
    files_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    wall_seconds: float = 0.0
    peak_rss_mb: float = 0.0
    unit: str = "day"  # what `days_done`/`days_refused` count: a day, or an hour (closed hours)

    def summary(self, apply: bool) -> str:
        verb = "consolidated" if apply else "would be consolidated (report only, nothing changed)"
        return (
            f"{self.days_done} {self.unit}(s) {verb}, {self.days_refused} refused, "
            f"{self.leaves_failed} leaf/leaves failed; "
            f"files {self.files_before} -> {self.files_after}; "
            f"{self.bytes_before / _MB:.1f} -> {self.bytes_after / _MB:.1f} MB; "
            f"{self.wall_seconds:.1f} s wall; peak RSS {self.peak_rss_mb:.0f} MB"
        )


def leaf_dirs(
    catalog_path: str, data_types: list[str] | None = None, venue: str | None = None
) -> list[Path]:
    """
    Every data/<type>/<instrument> directory holding parquet files, `bar` leaves excepted; with
    `venue`, only instruments whose id ends in `.VENUE`.
    """
    root = Path(catalog_path) / "data"
    if not root.exists():
        return []
    return sorted(
        d
        for t in root.iterdir()
        if t.is_dir()
        and t.name not in _NEVER_CONSOLIDATED
        and (not data_types or t.name in data_types)
        for d in t.iterdir()
        if d.is_dir() and (venue is None or has_venue(d.name, venue)) and any(d.glob("*.parquet"))
    )


def file_span(path: Path) -> tuple[int, int]:
    span = CatalogFileSpan.from_path(path)
    return span.start_ns, span.end_ns


def closed_days_needing_work(
    directory: Path, now_ns: int, max_days: int | None
) -> dict[int, list[Path]]:
    """
    Day index -> files lying wholly inside that closed day (before today, UTC), for days that still
    hold more than one such file. A file crossing midnight belongs to no single day and is left alone.
    `max_days` limits how far back (None: everything).
    """
    today = now_ns // _DAY_NS
    days: dict[int, list[Path]] = {}
    for path in directory.glob("*.parquet"):
        a, b = file_span(path)
        day = a // _DAY_NS
        if day == b // _DAY_NS and day < today and (max_days is None or day >= today - max_days):
            days.setdefault(day, []).append(path)
    return {d: sorted(fs) for d, fs in days.items() if len(fs) > 1}


def _schemas_agree(files: list[Path]) -> bool:
    """Report whether every file has the same full schema, Arrow metadata included (see above)."""
    first = pq.read_schema(str(files[0]))
    return all(pq.read_schema(str(f)).equals(first, check_metadata=True) for f in files[1:])


def _row_count(files: list[Path]) -> int:
    return sum(pq.read_metadata(str(f)).num_rows for f in files)


def _covering_file(files: list[Path]) -> Path | None:
    """
    Return the file whose span contains every other file's span: a candidate for an earlier run
    interrupted between renaming the merged file into place and deleting its sources. Only a
    candidate -- `_is_merge_of` decides.
    """
    spans = {f: file_span(f) for f in files}
    lo, hi = min(a for a, _ in spans.values()), max(b for _, b in spans.values())
    covering = [f for f, (a, b) in spans.items() if (a, b) == (lo, hi)]
    return covering[0] if len(covering) == 1 else None


def _is_merge_of(big: Path, sources: list[Path]) -> bool:
    """
    Report whether `big` holds exactly the sources' rows, by their sorted `ts_init` column. A row
    count alone is not enough: a file written into an already-consolidated day by another writer
    can have as many rows as the merged file, and would then be deleted as "already covered".
    """
    merged = pq.read_table(str(big), columns=["ts_init"]).column("ts_init")
    parts = pa.concat_arrays(
        [
            pq.read_table(str(f), columns=["ts_init"]).column("ts_init").combine_chunks()
            for f in sources
        ]
    )
    return merged.combine_chunks().equals(pc.take(parts, pc.sort_indices(parts)))


def merged_table(files: list[Path]) -> pa.Table:
    """Concatenate `files` (one schema), sorted by `ts_init`: the day's one file."""
    table = pa.concat_tables([pq.read_table(str(f)) for f in files])
    order = pc.sort_indices(table, sort_keys=[("ts_init", "ascending")])
    return table.take(order)


def _merge(
    writer: CatalogWriter, directory: Path, sources: list[Path], label: str, scope: MergeScope
) -> bool:
    """Write the merged file; False (ledgered, sources kept) when its read-back row count differs."""
    try:
        writer.write_merged(directory, merged_table(sources), _row_count(sources), scope=scope)
    except RewriteVerifyError as e:
        error_ledger.record(
            "consolidate.row_count", f"{label}: merged file failed verification ({e}); sources kept"
        )
        return False
    except PartialCommitError as e:  # the merged file's rename failed: nothing landed
        error_ledger.record("consolidate.error", f"{label}: {e}; sources kept", exc=e)
        return False
    return True


def _consolidate_day(
    writer: CatalogWriter | None,
    directory: Path,
    label: str,
    files: list[Path],
    apply: bool,
    scope: MergeScope = MergeScope.CLOSED_DAY,
) -> bool:
    """
    Consolidate one closed period's files (a day, or an hour in `CLOSED_HOUR` scope); return False
    when it was refused (recorded in the error ledger, nothing deleted), True when done (or
    reported without --apply).
    """
    # Known limit: a refused day stays refused -- every nightly run records it again and exits 1
    # until an operator rewrites that day's odd file(s) to the common schema (as
    # `archive.tools.migrate_snapshot_ints` does for the float-layout snapshot files, D-24's
    # pre-OHLC ones included). Upgrade path: a
    # per-day re-stamp/split tool for a mid-day precision change (exact `Price.from_raw`, never
    # float), which no venue has produced yet.
    if not _schemas_agree(files):
        error_ledger.record(
            "consolidate.mixed_schema", f"{label}: files with differing schemas, refused (D-24)"
        )
        return False
    big = _covering_file(files)
    sources = [f for f in files if f is not big] if big else files
    expected = _row_count(sources)
    if not apply or writer is None:
        logger.info("%s: %d files, %d rows (report only)", label, len(files), expected)
        return True
    if big is None:
        if not _merge(writer, directory, sources, label, scope):
            return False
    elif pq.read_metadata(str(big)).num_rows != expected or not _is_merge_of(big, sources):
        error_ledger.record(
            "consolidate.row_count",
            f"{label}: covering file {big.name} does not match its sources; nothing deleted",
        )
        return False
    writer.remove_merged_sources(sources, scope=scope)
    logger.info("%s: %d files -> 1, %d rows", label, len(files), expected)
    return True


def _consolidate_period(
    writer: CatalogWriter | None,
    directory: Path,
    label: str,
    files: list[Path],
    apply: bool,
    scope: MergeScope,
) -> bool:
    """One period's consolidation, with every tolerated failure confined to it and ledgered."""
    try:
        return _consolidate_day(writer, directory, label, files, apply, scope)
    except OpenDayWriteError as e:  # never for a closed period; refused loudly if it ever is
        error_ledger.record("consolidate.open_day", f"{label}: {e}; sources kept")
    except (OSError, pa.ArrowException) as e:  # e.g. a truncated file: this period only
        error_ledger.record("consolidate.error", f"{label}: {e!r}; sources kept", exc=e)
    return False


def _consolidate_groups(
    writer: CatalogWriter | None,
    directory: Path,
    groups: dict[int, list[Path]],
    scope: MergeScope,
    apply: bool,
    stats: RunStats | None,
) -> int:
    """
    Consolidate each period's group of one leaf, oldest first; return how many were done. A group
    is keyed by its day index in `CLOSED_DAY` scope, by its hour index in `CLOSED_HOUR` scope.
    """
    period_ns = NS_PER_HOUR if scope is MergeScope.CLOSED_HOUR else _DAY_NS
    done = 0
    for period, files in sorted(groups.items()):
        stamp = pd.Timestamp(period * period_ns, unit="ns")
        when = stamp.date() if scope is MergeScope.CLOSED_DAY else stamp.strftime("%Y-%m-%d %H:00")
        label = f"{directory.parent.name}/{directory.name} {when}"
        if _consolidate_period(writer, directory, label, files, apply, scope):
            done += 1
        elif stats is not None:
            stats.days_refused += 1
    if stats is not None:
        stats.days_done += done
    return done


def consolidate_directory(
    writer: CatalogWriter | None,
    directory: Path,
    now_ns: int,
    max_days: int | None,
    apply: bool,
    stats: RunStats | None = None,
) -> int:
    """
    Consolidate one leaf directory; return the number of days done (or reported without --apply).
    Refused days are recorded in the error ledger and, when `stats` is given, counted there.
    """
    groups = closed_days_needing_work(directory, now_ns, max_days)
    return _consolidate_groups(writer, directory, groups, MergeScope.CLOSED_DAY, apply, stats)


def consolidate_closed_hours(
    writer: CatalogWriter | None,
    directory: Path,
    now_ns: int,
    apply: bool,
    stats: RunStats | None = None,
) -> int:
    """
    Consolidate one leaf's closed hours of the current UTC day (`closed_hours_needing_work`);
    return the number of hours done (or reported without --apply).
    """
    spans = {path: file_span(path) for path in directory.glob("*.parquet")}
    groups = closed_hours_needing_work(spans, now_ns)
    return _consolidate_groups(writer, directory, groups, MergeScope.CLOSED_HOUR, apply, stats)


def _leaf_size(directory: Path) -> tuple[int, int]:
    files, size = 0, 0
    for f in directory.glob("*.parquet"):
        try:
            size += f.stat().st_size
        except FileNotFoundError:  # removed between listing and stat; not this job's concern
            continue
        files += 1
    return files, size


def _consolidate_leaf(
    writer: CatalogWriter | None,
    directory: Path,
    now_ns: int,
    max_days: int | None,
    apply: bool,
    stats: RunStats,
    scope: MergeScope = MergeScope.CLOSED_DAY,
) -> None:
    """Consolidate one leaf: its closed days, or in `CLOSED_HOUR` scope today's closed hours."""
    files, size = _leaf_size(directory)
    stats.files_before += files
    stats.bytes_before += size
    try:
        if apply and writer is not None:
            writer.remove_stale_tmp(directory)
        if scope is MergeScope.CLOSED_HOUR:
            consolidate_closed_hours(writer, directory, now_ns, apply, stats)
        else:
            consolidate_directory(writer, directory, now_ns, max_days, apply, stats)
    except (OSError, ValueError, pa.ArrowException) as e:  # e.g. an unparseable file name
        error_ledger.record("consolidate.error", f"{directory}: leaf abandoned: {e!r}", exc=e)
        stats.leaves_failed += 1
    files, size = _leaf_size(directory)
    stats.files_after += files
    stats.bytes_after += size


def run(
    writer: CatalogWriter | None,
    catalog_path: str,
    data_types: list[str] | None,
    max_days: int | None,
    apply: bool,
    now_ns: int,
    venue: str | None = None,
) -> RunStats:
    """
    Consolidate every leaf of the catalog, one at a time; return what was done. `writer` None (with
    `apply` False) reports only: a report-only run takes no maintenance lock. A failure is
    recorded and confined to its day (or, for a listing failure, its leaf): the other venues'
    leaves still run and the summary is still printed.
    """
    return _run_leaves(
        writer, catalog_path, data_types, max_days, apply, now_ns, venue, MergeScope.CLOSED_DAY
    )


def run_closed_hours(
    writer: CatalogWriter | None,
    catalog_path: str,
    now_ns: int,
    apply: bool = True,
    venue: str | None = None,
) -> RunStats:
    """
    Merge the closed hours of the current UTC day of the small types (`INTRADAY_DATA_TYPES`), one
    leaf at a time, in `MergeScope.CLOSED_HOUR`; return what was done (`unit` "hour"). Failures
    are confined and ledgered exactly as in `run`.
    """
    types = sorted(INTRADAY_DATA_TYPES)
    return _run_leaves(
        writer, catalog_path, types, None, apply, now_ns, venue, MergeScope.CLOSED_HOUR
    )


def _run_leaves(
    writer: CatalogWriter | None,
    catalog_path: str,
    data_types: list[str] | None,
    max_days: int | None,
    apply: bool,
    now_ns: int,
    venue: str | None,
    scope: MergeScope,
) -> RunStats:
    started = time.monotonic()
    stats = RunStats(unit="hour" if scope is MergeScope.CLOSED_HOUR else "day")
    for directory in leaf_dirs(catalog_path, data_types, venue):
        _consolidate_leaf(writer, directory, now_ns, max_days, apply, stats, scope)
    stats.wall_seconds = time.monotonic() - started
    # ru_maxrss is KiB on Linux (the only platform this runs on: the collector image).
    stats.peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return stats
