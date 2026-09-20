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
    python -m dydx_collector.consolidate_catalog --catalog /app/catalog --apply [--days 3] [--data-type custom_dydx_second_snapshot ...]

The collector flushes once a minute, so every coin grows ~1,440 files per data type per day;
reads, backups and inode counts all scale with that file count, not with the data (audit D-36).
This job rewrites closed days only -- today's files, still being written, are never touched.

It is plain pyarrow, not `ParquetDataCatalog.consolidate_data_by_period`: that built-in round-trips
every row through Python objects and `ArrowSerializer`, which raises NotImplementedError for the
Rust-native types here (MarkPriceUpdate, IndexPriceUpdate, FundingRateUpdate), decodes a coin-day of
20-level books into ~100 MB of objects, reads through the first-file-schema dataset that caused
D-24, and deletes the originals before anything can be verified. Here a day's files are required to
share one schema (mixed = refused, D-24), concatenated as-is, written to a temp file, read back and
row-counted against the sources, renamed into place, and only then are the sources deleted. A crash
between rename and delete leaves the sources covered by the big file; the next run detects that,
re-verifies the count and finishes the delete, so rows are never duplicated or lost. One (type,
instrument, day) in memory at a time (MEM-01). Report-only unless --apply.
"""

import argparse
import logging
import os
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns

from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000


def leaf_dirs(catalog_path: str, data_types: list[str] | None = None) -> list[Path]:
    """Every data/<type>/<instrument> directory holding parquet files."""
    root = Path(catalog_path) / "data"
    if not root.exists():
        return []
    return sorted(
        d
        for t in root.iterdir()
        if t.is_dir() and (not data_types or t.name in data_types)
        for d in t.iterdir()
        if d.is_dir() and any(d.glob("*.parquet"))
    )


def _file_span(path: Path) -> tuple[int, int]:
    first, _, last = path.stem.partition("_")
    return _stamp_to_ns(first), _stamp_to_ns(last)


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
    return len({pq.read_schema(str(f)).names.__repr__() for f in files}) == 1


def _row_count(files: list[Path]) -> int:
    return sum(pq.read_metadata(str(f)).num_rows for f in files)


def _covering_file(files: list[Path]) -> Path | None:
    """
    Return the file whose span contains every other file's span: an earlier run interrupted
    between renaming the merged file into place and deleting its sources.
    """
    spans = {f: _file_span(f) for f in files}
    lo, hi = min(a for a, _ in spans.values()), max(b for _, b in spans.values())
    covering = [f for f, (a, b) in spans.items() if (a, b) == (lo, hi)]
    return covering[0] if len(covering) == 1 else None


def _merge(directory: Path, files: list[Path]) -> Path:
    """Concatenate `files` (one schema) into one file named by its real span; returns the temp path."""
    table = pa.concat_tables([pq.read_table(str(f)) for f in files])
    order = pa.compute.sort_indices(table, sort_keys=[("ts_init", "ascending")])
    table = table.take(order)
    ts = table.column("ts_init")
    name = _timestamps_to_filename(int(pa.compute.min(ts).as_py()), int(pa.compute.max(ts).as_py()))
    tmp = directory / (name + ".tmp")
    pq.write_table(table, str(tmp), compression="zstd")
    return tmp


def consolidate_directory(directory: Path, now_ns: int, max_days: int | None, apply: bool) -> int:
    """Consolidate one leaf directory; return the number of days done (or reported without --apply)."""
    done = 0
    for day, files in sorted(closed_days_needing_work(directory, now_ns, max_days).items()):
        label = f"{directory.parent.name}/{directory.name} {pd.Timestamp(day * _DAY_NS, unit='ns').date()}"
        if not _schemas_agree(files):
            error_ledger.record(
                "consolidate.mixed_schema", f"{label}: files with differing schemas, refused (D-24)"
            )
            continue
        big = _covering_file(files)
        sources = [f for f in files if f is not big] if big else files
        expected = _row_count(sources)
        if not apply:
            logger.info("%s: %d files, %d rows (report only)", label, len(files), expected)
            done += 1
            continue
        if big is None:
            tmp = _merge(directory, sources)
            big = tmp.with_suffix("")  # strip .tmp -> the final .parquet name
            if pq.read_metadata(str(tmp)).num_rows != expected:
                tmp.unlink()
                error_ledger.record(
                    "consolidate.row_count",
                    f"{label}: merged file holds a different row count; sources kept",
                )
                continue
            os.replace(tmp, big)
        elif pq.read_metadata(str(big)).num_rows != expected:
            error_ledger.record(
                "consolidate.row_count",
                f"{label}: covering file {big.name} does not match its sources; nothing deleted",
            )
            continue
        for f in sources:
            f.unlink()
        logger.info("%s: %d files -> 1, %d rows", label, len(files), expected)
        done += 1
    return done


def main() -> None:
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    now_ns = time.time_ns()
    total = 0
    for directory in leaf_dirs(args.catalog, args.data_type):
        total += consolidate_directory(directory, now_ns, args.days, args.apply)
    logger.info("%d day(s) %s", total, "consolidated" if args.apply else "would be consolidated")


if __name__ == "__main__":
    main()
