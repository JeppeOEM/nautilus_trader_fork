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
"""
Rewrite the catalog's closed-day files with the archive's compact write settings (Story 30.1).

Since Story 30.1 every file the archive merges or rewrites gets
`archive.infrastructure.compact_parquet.compact_write_options` (zstd 16, delta-packed
timestamps, dictionary leaves; 29-56 % fewer bytes per row measured at level 19 on one real
Bybit day, level 16 within 2 % of level 19's size on the story's measurement; DATA_DICTIONARY.md
§6). Files consolidated before it keep pyarrow's default encodings; this one-off tool rewrites
them.

Usage:
    python -m archive.tools.recompress --catalog /app/catalog        # report only
    python -m archive.tools.recompress --catalog /app/catalog --apply \
        [--venue BYBIT] [--type trade_tick ...]

Scope: every `data/<type>/<instrument>` leaf (`archive.application.consolidate_day.leaf_dirs`;
`bar` leaves are excluded there, as from consolidation), narrowed by `--venue` and by `--type`
(repeatable, a directory name under `data/`). In scope is every file that is not already compact
(`compact_parquet.is_compact`) and whose span (`kernel.clocks.CatalogFileSpan`) does not reach the
current UTC day: capture still writes that day, so its files are skipped and counted, and a later
run takes them. An already-compact file is counted too, so a second run finds nothing to do.

Report-only without `--apply`: no lock, nothing written; per data type it prints the files in
scope, their bytes now and the bytes they would take (`catalog_files.encoded_size`, an in-memory
write with the same settings). With `--apply` it holds the catalog maintenance flock, removes the
temp files an interrupted run left in each leaf, and rewrites each file through
`CatalogFiles.rewrite` -- a verified temp-then-rename that keeps the file's name, schema,
metadata, rows and every value (compared on the read-back temp before the rename), so an
interrupted run leaves the old or the new file, never a torn one. One file in memory at a time
(MEM-01). Both modes end with one line per data type and a total: files, MB before -> after,
% saved, wall seconds and peak RSS.

A file that fails on its own (unreadable, an unparseable name, a failed read-back verification,
an I/O error) is ledgered (`recompress.error`, one entry per file), left as it was, and the run
goes on to the next; a leaf that cannot even be listed or cleaned of its temps is ledgered the
same way and skipped. In a report, a file gone between the listing and its read (the report
runs without the lock, so a consolidation may merge it away meanwhile) is counted as vanished,
not as a failure: its rows are in the merged file, which the next run sees. Under `--apply` the
lock excludes every other remover, so a file gone mid-run is ledgered as a failure. Exit codes:
0 done, 1 the catalog is missing (`archive.catalog_missing`) or another maintenance run holds
the lock, 2 any file failed.

Known limit: consolidation merges only a closed day holding more than one file, and never a file
crossing midnight (`consolidate_day.closed_days_needing_work`), so a closed day that is already
one file, or a midnight-crossing file, keeps Nautilus's encoding until the next recompress run.
Upgrade path: have the `archive` service run recompress over the closed days after its nightly
consolidation. `bar` leaves (`archive.backfill_bars`, written through Nautilus's `write_data`)
are never merged nor recompressed: their file intervals are a coverage record
(`consolidate_day`'s docstring), and they are few; they keep Nautilus's encoding.
"""

import argparse
import logging
import resource
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from kernel.clocks import NS_PER_DAY
from kernel.clocks import CatalogFileSpan
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.consolidate_day import leaf_dirs
from archive.application.ports import CatalogWriter
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteVerifyError
from archive.infrastructure.catalog_files import encoded_size
from archive.infrastructure.compact_parquet import is_compact
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)

_MB = 1024 * 1024
# Every failure one file can raise on its own; anything else is a bug and stops the run.
_FILE_ERRORS = (RewriteVerifyError, PartialCommitError, OSError, pa.ArrowException, ValueError)


@dataclass
class TypeTotals:
    """The files of one data type a run rewrote (or would), and their bytes before and after."""

    files: int = 0
    bytes_before: int = 0
    bytes_after: int = 0

    def line(self) -> str:
        saved = 100 * (1 - self.bytes_after / self.bytes_before) if self.bytes_before else 0.0
        return (
            f"{self.files} file(s), {self.bytes_before / _MB:.2f} -> "
            f"{self.bytes_after / _MB:.2f} MB ({self.bytes_before} -> {self.bytes_after} bytes, "
            f"{saved:.1f} % saved)"
        )


@dataclass
class RecompressStats:
    """What one run did, for its summary and exit code."""

    by_type: dict[str, TypeTotals] = field(default_factory=dict)
    already_compact: int = 0
    open_day: int = 0
    vanished: int = 0
    failed: int = 0
    wall_seconds: float = 0.0
    peak_rss_mb: float = 0.0

    def total(self) -> TypeTotals:
        totals = self.by_type.values()
        return TypeTotals(
            sum(t.files for t in totals),
            sum(t.bytes_before for t in totals),
            sum(t.bytes_after for t in totals),
        )


def _failed(path: Path, error: Exception, stats: RecompressStats) -> None:
    error_ledger.record("recompress.error", f"{path}: {error!r}; left as it was", exc=error)
    stats.failed += 1


def _in_scope(path: Path, today_start_ns: int, stats: RecompressStats) -> bool:
    """Whether `path` is a closed-day file still to recompress; the rest are counted."""
    span = CatalogFileSpan.from_path(path)
    if max(span.start_ns, span.end_ns) >= today_start_ns:
        stats.open_day += 1
        return False
    if is_compact(path):
        stats.already_compact += 1
        return False
    return True


def _recompress_file(writer: CatalogWriter | None, path: Path, totals: TypeTotals) -> None:
    """Rewrite one file (or, with no writer, project its size); only a success is counted."""
    before = path.stat().st_size
    table = pq.read_table(path)
    if writer is None:
        after = encoded_size(table)
    else:
        writer.rewrite(path, table)
        after = path.stat().st_size
    totals.files += 1
    totals.bytes_before += before
    totals.bytes_after += after


def _recompress_one(
    writer: CatalogWriter | None,
    path: Path,
    today_start_ns: int,
    totals: TypeTotals,
    stats: RecompressStats,
) -> None:
    """Recompress (or project) one file, every failure of its own confined to it."""
    try:
        if _in_scope(path, today_start_ns, stats):
            _recompress_file(writer, path, totals)
    except OpenDayWriteError:  # the writer's own guard: the day opened during the run
        stats.open_day += 1
    except FileNotFoundError as e:
        # Only a report can race a consolidation (it holds no lock); under --apply the lock
        # excludes every other remover, so a file gone mid-run is an anomaly (DATA-07).
        if writer is not None or path.exists():
            _failed(path, e, stats)
        else:  # merged away by a consolidation since the listing: its rows are in the merge
            stats.vanished += 1
    except _FILE_ERRORS as e:
        _failed(path, e, stats)


def _recompress_leaf(
    writer: CatalogWriter | None, leaf: Path, today_start_ns: int, stats: RecompressStats
) -> None:
    try:
        if writer is not None:
            writer.remove_stale_tmp(leaf)
        paths = sorted(leaf.glob("*.parquet"))
    except OSError as e:  # e.g. a temp that cannot be removed: this leaf only
        error_ledger.record("recompress.error", f"{leaf}: leaf skipped: {e!r}", exc=e)
        stats.failed += 1
        return
    totals = stats.by_type.setdefault(leaf.parent.name, TypeTotals())
    for path in paths:
        _recompress_one(writer, path, today_start_ns, totals, stats)


def run(
    writer: CatalogWriter | None,
    catalog_path: str,
    data_types: list[str] | None,
    venue: str | None,
    now_ns: int,
) -> RecompressStats:
    """
    Recompress (with `writer`) or report (None) every closed non-compact file in scope, one leaf
    and one file at a time; every per-file failure is ledgered and the run goes on.
    """
    started = time.monotonic()
    stats = RecompressStats()
    today_start_ns = now_ns // NS_PER_DAY * NS_PER_DAY
    for leaf in leaf_dirs(catalog_path, data_types, venue):
        _recompress_leaf(writer, leaf, today_start_ns, stats)
    stats.wall_seconds = time.monotonic() - started
    # ru_maxrss is KiB on Linux (the only platform this runs on: the collector image).
    stats.peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return stats


def _report(stats: RecompressStats, apply: bool) -> None:
    for data_type, totals in sorted(stats.by_type.items()):
        if totals.files:
            logger.info("%s: %s", data_type, totals.line())
    verb = "rewritten" if apply else "would be rewritten (report only, nothing changed)"
    logger.info(
        "recompress: %s %s; %d already compact, %d skipped (current UTC day), %d vanished "
        "(merged since the listing), %d failed; %.1f s wall; peak RSS %.0f MB",
        stats.total().line(),
        verb,
        stats.already_compact,
        stats.open_day,
        stats.vanished,
        stats.failed,
        stats.wall_seconds,
        stats.peak_rss_mb,
    )


def _run(args: argparse.Namespace) -> RecompressStats | None:
    """Apply under the maintenance flock (None when it is held); a report takes no lock."""
    if not args.apply:
        return run(None, args.catalog, args.type, args.venue, time.time_ns())
    with maintenance(args.catalog) as writer:
        if writer is None:
            logger.error("recompress: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return None
        return run(writer, args.catalog, args.type, args.venue, time.time_ns())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; 0 done, 1 the catalog is missing or the lock is held, 2 a file failed."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report only)")
    parser.add_argument("--venue", help="only instruments of this venue, e.g. BYBIT")
    parser.add_argument(
        "--type", action="append", help="repeatable directory name under data/; default: all"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if catalog_missing("recompress", args.catalog):
        return 1
    stats = _run(args)
    if stats is None:
        return 1
    _report(stats, args.apply)
    if stats.failed:
        logger.error(
            "recompress: %d file(s) failed -- see the recompress.error entries above (DATA-07)",
            stats.failed,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
