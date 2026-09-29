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
Give every DydxSecondSnapshot Parquet file the current schema (audit D-24).

Files written before the OHLC fields existed lack `open/high/low/close_price`. The catalog reads
a query's whole file list with the schema of the *first* file, so any query spanning an old and a
new file silently returned None OHLC for every new row. Rewriting the old files with the missing
columns as nulls -- exactly what those seconds hold: no OHLC was ever recorded for them -- makes
every file share one schema, so the catalog's own reader is correct again.

Usage:
    python -m archive.tools.normalize_snapshot_schema --catalog /app/catalog                        # report only
    python -m archive.tools.normalize_snapshot_schema --catalog /app/catalog --backup-dir /backup --apply

--apply refuses to run without --backup-dir: each original file is copied there (same relative
path) before it is replaced, and the replacement goes through `CatalogFiles.rewrite` (a verified
temp-then-rename), so an interrupted run leaves either the old or the new file, never a torn one.
File names (which the catalog and `data_file_ranges` read as time ranges) and all row values are
unchanged; re-running is a no-op. Manually run, like repair_catalog; one file in memory at a time
(MEM-01); holds the catalog maintenance flock. A file whose span reaches the current UTC day is
skipped (`normalize_snapshot_schema.open_day`, with the count; exit 2): capture writes that day.
A file that fails on its own (unreadable, columns outside the current schema, a rewrite failing its
read-back, an I/O error) is ledgered (`normalize_snapshot_schema.error`, one entry per file), left
as it was, and the run goes on to the next (exit 2). A missing catalog is
`archive.catalog_missing`, exit 1.

Deliberate change (Story 25.1, Story 30.1): the rewritten file is written with the archive's
compact settings like every other archive rewrite
(`archive.infrastructure.compact_parquet.compact_write_options`: zstd 16, delta-packed
timestamps), where this tool used to write pyarrow's snappy default -- the bytes differ, the
schema and the rows are identical.
"""

import argparse
import logging
import shutil
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from kernel.clocks import CatalogFileSpan
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.ports import CatalogWriter
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteVerifyError
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)

_TARGET = DydxSecondSnapshot.schema()
_SNAPSHOT_DIR = Path("data") / "custom_dydx_second_snapshot"


def files_needing_migration(catalog_path: str, instruments: list[str] | None = None) -> list[Path]:
    root = Path(catalog_path) / _SNAPSHOT_DIR
    dirs = (
        [root / i for i in instruments]
        if instruments
        else sorted(p for p in root.glob("*") if p.is_dir())
    )
    return [f for d in dirs for f in sorted(d.glob("*.parquet")) if _needs_migration(f)]


def _needs_migration(path: Path) -> bool:
    """Whether `path` lacks a current column; an unreadable file is ledgered and skipped."""
    try:
        names = set(pq.read_schema(path).names)
    except (OSError, pa.ArrowException) as e:
        error_ledger.record(
            "normalize_snapshot_schema.error", f"{path}: {e!r}; unreadable, skipped", exc=e
        )
        return False
    return bool(set(_TARGET.names) - names)


def _normalized(path: Path) -> pa.Table:
    table = pq.read_table(path)
    unexpected = set(table.schema.names) - set(_TARGET.names)
    if unexpected:
        raise ValueError(
            f"{path}: columns not in the current schema, refusing to guess: {sorted(unexpected)}"
        )
    columns = [
        table.column(f.name) if f.name in table.schema.names else pa.nulls(table.num_rows, f.type)
        for f in _TARGET
    ]
    return pa.Table.from_arrays(columns, schema=_TARGET).cast(_TARGET)


def migrate_file(writer: CatalogWriter, catalog_path: str, path: Path, backup_dir: str) -> None:
    """Back up and rewrite one file; `OpenDayWriteError` (nothing touched) for today's."""
    span = CatalogFileSpan.from_path(path)
    writer.assert_span_closed(span.start_ns, span.end_ns)
    backup = Path(backup_dir) / path.relative_to(catalog_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    new = _normalized(path)  # validated before anything is touched
    shutil.copy2(path, backup)
    writer.rewrite(path, new)


def _migrate_one(writer: CatalogWriter, catalog_path: str, path: Path, backup: str) -> str:
    """Rewrite one file; return "done", "open_day" or "error" (ledgered, the file as it was)."""
    try:
        migrate_file(writer, catalog_path, path, backup)
    except OpenDayWriteError:
        return "open_day"  # counted, ledgered once for the run
    except (RewriteVerifyError, PartialCommitError, OSError, pa.ArrowException, ValueError) as e:
        error_ledger.record(
            "normalize_snapshot_schema.error",
            f"{path}: {e!r}; not rewritten, left as it was",
            exc=e,
        )
        return "error"
    return "done"


def _migrate_all(
    writer: CatalogWriter, catalog_path: str, todo: list[Path], backup: str
) -> Counter[str]:
    """Rewrite every closed file, one failure never stopping the rest; count the outcomes."""
    outcomes: Counter[str] = Counter()
    for i, path in enumerate(todo, 1):
        outcomes[_migrate_one(writer, catalog_path, path, backup)] += 1
        if i % 500 == 0:
            logger.info("migrated %d/%d", i, len(todo))
    return outcomes


def _finished(outcomes: Counter[str], backup_dir: str) -> int:
    """Log and ledger the run's end; the exit code (2 when any file was skipped or failed)."""
    logger.info(
        "done: %d file(s) migrated, %d failed; originals in %s",
        outcomes["done"],
        outcomes["error"],
        backup_dir,
    )
    if outcomes["open_day"]:
        error_ledger.record(
            "normalize_snapshot_schema.open_day",
            f"{outcomes['open_day']} file(s) reach the current UTC day and were skipped; "
            "rerun tomorrow",
        )
    return 2 if outcomes["open_day"] or outcomes["error"] else 0


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point; 0 done, 1 the catalog is missing or the lock is held, 2 files skipped for
    the open day or failed on their own (ledgered).
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument("--backup-dir", help="required with --apply")
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report only)")
    args = parser.parse_args(argv)
    if args.apply and not args.backup_dir:
        parser.error("--apply requires --backup-dir")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if catalog_missing("normalize_snapshot_schema", args.catalog):
        return 1
    if not args.apply:
        todo = files_needing_migration(args.catalog, args.instrument)
        logger.info("%d snapshot file(s) lack the current schema", len(todo))
        return 0
    with maintenance(args.catalog) as writer:
        if writer is None:
            logger.error("normalize: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return 1
        todo = files_needing_migration(args.catalog, args.instrument)  # listed under the lock
        logger.info("%d snapshot file(s) lack the current schema", len(todo))
        outcomes = _migrate_all(writer, args.catalog, todo, args.backup_dir)
    return _finished(outcomes, args.backup_dir)


if __name__ == "__main__":
    raise SystemExit(main())
