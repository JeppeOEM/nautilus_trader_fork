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
    python -m dydx_collector.normalize_snapshot_schema --catalog /app/catalog                        # report only
    python -m dydx_collector.normalize_snapshot_schema --catalog /app/catalog --backup-dir /backup --apply

--apply refuses to run without --backup-dir: each original file is copied there (same relative
path) before it is replaced, and the replacement is written to a temp file then renamed, so an
interrupted run leaves either the old or the new file, never a torn one. File names (which the
catalog and `data_file_ranges` read as time ranges) and all row values are unchanged; re-running
is a no-op. Manually run, like repair_catalog.py; one file in memory at a time (MEM-01).
"""

import argparse
import logging
import os
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from collector_core.second_snapshot import DydxSecondSnapshot


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
    return [
        f
        for d in dirs
        for f in sorted(d.glob("*.parquet"))
        if set(_TARGET.names) - set(pq.read_schema(f).names)
    ]


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


def migrate_file(catalog_path: str, path: Path, backup_dir: str) -> None:
    backup = Path(backup_dir) / path.relative_to(catalog_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    new = _normalized(path)  # validated before anything is touched
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(new, tmp)
    if pq.read_table(tmp).num_rows != new.num_rows:
        raise RuntimeError(
            f"{path}: rewritten file has a different row count; original left in place"
        )
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument("--backup-dir", help="required with --apply")
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report only)")
    args = parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error("--apply requires --backup-dir")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    todo = files_needing_migration(args.catalog, args.instrument)
    logger.info("%d snapshot file(s) lack the current schema", len(todo))
    if not args.apply:
        return
    for i, path in enumerate(todo, 1):
        migrate_file(args.catalog, path, args.backup_dir)
        if i % 500 == 0:
            logger.info("migrated %d/%d", i, len(todo))
    logger.info("done: %d file(s) migrated; originals in %s", len(todo), args.backup_dir)


if __name__ == "__main__":
    main()
