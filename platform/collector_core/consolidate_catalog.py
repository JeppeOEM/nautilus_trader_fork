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

Usage (nightly, e.g. from cron via `make consolidate`):
    python -m collector_core.consolidate_catalog --catalog /app/catalog --apply [--days 3] [--data-type custom_dydx_second_snapshot ...] [--venue BYBIT]

Every collector (dYdX, Bybit, Hyperliquid) flushes once a minute into the one shared catalog, so
every instrument grows ~1,440 files per data type per day; reads, backups and inode counts all scale
with that file count, not with the data (audit D-36). This job walks every `data/<type>/<instrument>`
leaf -- it has no venue-specific code -- and rewrites closed days only: today's files, still being
written, are never touched.

It is plain pyarrow, not `ParquetDataCatalog.consolidate_data_by_period`: that built-in round-trips
every row through Python objects and `ArrowSerializer`, which raises NotImplementedError for the
Rust-native types here (MarkPriceUpdate, IndexPriceUpdate, FundingRateUpdate), decodes a coin-day of
20-level books into ~100 MB of objects, reads through the first-file-schema dataset that caused
D-24, and deletes the originals before anything can be verified. Here a day's files are required to
share one full schema -- names, types, nullability *and* Arrow metadata (mixed = refused, D-24).
Metadata matters: catalog files carry e.g. `price_precision`, and `pa.concat_tables` silently keeps
the first table's metadata, so merging two precisions would relabel part of the rows. The files are
concatenated as-is, written to a temp file, read back and row-counted against the sources, renamed
into place, and only then are the sources deleted. A crash between rename and delete leaves the
sources covered by the big file; the next run detects that, re-verifies the count and finishes the
delete, so rows are never duplicated or lost. A crash inside the write leaves a
`*.parquet.consolidate.tmp` (its own suffix: `migrate_open_interest`/`normalize_snapshot_schema`
write `*.parquet.tmp` in the same leaves), which the next --apply run deletes before touching that
leaf. One run at a time (an flock on the catalog root); one (type, instrument, day) in memory at a
time (MEM-01). Report-only unless --apply. `--venue V` limits the run to leaves whose instrument
directory ends in `.V` (the nightly job's per-venue form). The flock is `MAINTENANCE_LOCK_NAME`,
also taken by `rebuild_seconds` and `prune_catalog`, so no two catalog-rewriting jobs overlap.

`bar` leaves are never consolidated: `backfill_bars` writes one file per contiguous run of venue
bars and plans its next fetch from the catalog's *file intervals*
(`get_missing_intervals_for_request`), so merging two runs of a day across a missing range would
name one file over the hole and seal it as covered forever (DATA-05). They are also few (one file
per backfill window), so they are not the D-36 problem.

Each run ends with one summary line (days consolidated/refused, files and MB before -> after, wall
seconds, peak RSS of this process) and exits 1 when any day was refused (DATA-07): the refusal's
detail is in the error ledger and the log above the summary.
"""

import argparse
import contextlib
import fcntl
import logging
import os
import resource
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from kernel.clocks import CatalogFileSpan
from kernel.venues import has_venue
from observability import error_ledger

from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000
_MB = 1024 * 1024
_TMP_SUFFIX = ".consolidate.tmp"
MAINTENANCE_LOCK_NAME = ".consolidate.lock"
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

    def summary(self, apply: bool) -> str:
        verb = "consolidated" if apply else "would be consolidated (report only, nothing changed)"
        return (
            f"{self.days_done} day(s) {verb}, {self.days_refused} refused, "
            f"{self.leaves_failed} leaf/leaves failed; "
            f"files {self.files_before} -> {self.files_after}; "
            f"{self.bytes_before / _MB:.1f} -> {self.bytes_after / _MB:.1f} MB; "
            f"{self.wall_seconds:.1f} s wall; peak RSS {self.peak_rss_mb:.0f} MB"
        )


@contextlib.contextmanager
def maintenance_lock(catalog: Path) -> Iterator[bool]:
    """
    Hold the catalog-maintenance flock for the block; yields False (lock not taken) when another
    maintenance run holds it -- the caller must then refuse to start.
    """
    with (catalog / MAINTENANCE_LOCK_NAME).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


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


def _file_span(path: Path) -> tuple[int, int]:
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
        a, b = _file_span(path)
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
    spans = {f: _file_span(f) for f in files}
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
    return merged.combine_chunks().equals(pa.compute.take(parts, pa.compute.sort_indices(parts)))


def _merge(directory: Path, files: list[Path]) -> Path:
    """Concatenate `files` (one schema) into one file named by its real span; returns the temp path."""
    table = pa.concat_tables([pq.read_table(str(f)) for f in files])
    order = pa.compute.sort_indices(table, sort_keys=[("ts_init", "ascending")])
    table = table.take(order)
    ts = table.column("ts_init")
    name = _timestamps_to_filename(int(pa.compute.min(ts).as_py()), int(pa.compute.max(ts).as_py()))
    tmp = directory / (name + _TMP_SUFFIX)
    pq.write_table(table, str(tmp), compression="zstd")
    return tmp


def _consolidate_day(directory: Path, label: str, files: list[Path], apply: bool) -> bool:
    """
    Consolidate one closed day's files; return False when the day was refused (recorded in the
    error ledger, nothing deleted), True when done (or reported without --apply).
    """
    # Known limit: a refused day stays refused -- every nightly run records it again and exits 1
    # until an operator rewrites that day's odd file(s) to the common schema (as
    # `dydx_collector.normalize_snapshot_schema` does for D-24's OHLC columns). Upgrade path: a
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
    if not apply:
        logger.info("%s: %d files, %d rows (report only)", label, len(files), expected)
        return True
    if big is None:
        tmp = _merge(directory, sources)
        big = tmp.with_name(tmp.name.removesuffix(_TMP_SUFFIX))  # the final .parquet name
        if pq.read_metadata(str(tmp)).num_rows != expected:
            tmp.unlink()
            error_ledger.record(
                "consolidate.row_count",
                f"{label}: merged file holds a different row count; sources kept",
            )
            return False
        os.replace(tmp, big)
    elif pq.read_metadata(str(big)).num_rows != expected or not _is_merge_of(big, sources):
        error_ledger.record(
            "consolidate.row_count",
            f"{label}: covering file {big.name} does not match its sources; nothing deleted",
        )
        return False
    for f in sources:
        f.unlink()
    logger.info("%s: %d files -> 1, %d rows", label, len(files), expected)
    return True


def consolidate_directory(
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
    done = 0
    for day, files in sorted(closed_days_needing_work(directory, now_ns, max_days).items()):
        date = pd.Timestamp(day * _DAY_NS, unit="ns").date()
        label = f"{directory.parent.name}/{directory.name} {date}"
        try:
            ok = _consolidate_day(directory, label, files, apply)
        except (OSError, pa.ArrowException) as e:  # e.g. a truncated file: this day only
            error_ledger.record("consolidate.error", f"{label}: {e!r}; sources kept", exc=e)
            ok = False
        if ok:
            done += 1
        elif stats is not None:
            stats.days_refused += 1
    if stats is not None:
        stats.days_done += done
    return done


def _remove_stale_tmp(directory: Path) -> None:
    """
    Delete `*.parquet.consolidate.tmp` left by a crash inside `_merge`; only this job writes that
    suffix, and the merge it belonged to reruns from the sources, so nothing is lost.
    """
    for tmp in directory.glob("*" + _TMP_SUFFIX):
        logger.warning("%s: removing leftover %s from an interrupted run", directory, tmp.name)
        tmp.unlink()


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
    directory: Path, now_ns: int, max_days: int | None, apply: bool, stats: RunStats
) -> None:
    files, size = _leaf_size(directory)
    stats.files_before += files
    stats.bytes_before += size
    try:
        if apply:
            _remove_stale_tmp(directory)
        consolidate_directory(directory, now_ns, max_days, apply, stats)
    except (OSError, ValueError, pa.ArrowException) as e:  # e.g. an unparseable file name
        error_ledger.record("consolidate.error", f"{directory}: leaf abandoned: {e!r}", exc=e)
        stats.leaves_failed += 1
    files, size = _leaf_size(directory)
    stats.files_after += files
    stats.bytes_after += size


def run(
    catalog_path: str,
    data_types: list[str] | None,
    max_days: int | None,
    apply: bool,
    now_ns: int,
    venue: str | None = None,
) -> RunStats:
    """
    Consolidate every leaf of the catalog, one at a time; return what was done. A failure is
    recorded and confined to its day (or, for a listing failure, its leaf): the other venues'
    leaves still run and the summary is still printed.
    """
    started = time.monotonic()
    stats = RunStats()
    for directory in leaf_dirs(catalog_path, data_types, venue):
        _consolidate_leaf(directory, now_ns, max_days, apply, stats)
    stats.wall_seconds = time.monotonic() - started
    # ru_maxrss is KiB on Linux (the only platform this runs on: the collector image).
    stats.peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code (1 when any day was refused)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report only)")
    parser.add_argument(
        "--days", type=int, help="only the last N closed days (default: all closed days)"
    )
    parser.add_argument(
        "--data-type", action="append", help="repeatable directory name under data/; default: all"
    )
    parser.add_argument("--venue", help="only instruments of this venue, e.g. BYBIT")
    args = parser.parse_args(argv)
    if args.days is not None and args.days < 1:
        parser.error("--days must be at least 1")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    catalog = Path(args.catalog)
    if not catalog.is_dir():
        logger.error("consolidate: catalog %s does not exist (wrong mount?)", catalog)
        return 1
    with maintenance_lock(catalog) as locked:
        if not locked:
            logger.error(
                "consolidate: another run holds %s; not starting", catalog / MAINTENANCE_LOCK_NAME
            )
            return 1
        stats = run(args.catalog, args.data_type, args.days, args.apply, time.time_ns(), args.venue)
    logger.info("consolidate: %s", stats.summary(args.apply))
    if stats.days_refused or stats.leaves_failed:
        logger.error(
            "consolidate: %d day(s) refused, %d leaf/leaves failed -- see the consolidate.* "
            "entries above (DATA-07)",
            stats.days_refused,
            stats.leaves_failed,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
