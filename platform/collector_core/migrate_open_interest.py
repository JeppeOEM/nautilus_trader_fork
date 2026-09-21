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
Merge the three per-venue open-interest catalog directories into one (Story 22.3, AC #3).

`custom_{dydx,bybit,hyperliquid}_open_interest/` held `DydxOpenInterest` / `BybitOpenInterest` /
`HyperliquidOpenInterest` rows; all three classes are now `collector_core.open_interest.OpenInterest`,
whose catalog directory is `custom_open_interest/`. Until this runs, history in the old directories
is read by nothing (DATA-05: no silent gap).

Each file is moved with its name unchanged (the name encodes the time range the catalog reads), its
Arrow `type` metadata rewritten to `OpenInterest`. Row values are untouched.

Usage:
    python -m collector_core.migrate_open_interest --catalog /app/catalog                        # report only
    python -m collector_core.migrate_open_interest --catalog /app/catalog --backup-dir /backup --apply

--apply refuses to run without --backup-dir: each source file is copied there (same relative path)
before anything is touched, and the new file is written to a temp name then renamed, so an
interrupted run leaves either the source or the target, never a torn file. Idempotent: with no old
directories left it does nothing. A target file that already exists (and is not a finished copy of its source) aborts before anything is
touched (never overwritten). One file in memory at a time (MEM-01).
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


logger = logging.getLogger(__name__)

_DATA = Path("data")
_TARGET_DIR = _DATA / "custom_open_interest"
_SOURCE_DIRS = tuple(
    _DATA / f"custom_{venue}_open_interest" for venue in ("dydx", "bybit", "hyperliquid")
)
_TYPE = b"OpenInterest"


def _is_finished_copy(source: Path, target: Path) -> bool:
    """A previous run crashed between os.replace and source.unlink: same rows, already retyped."""
    try:
        schema = pq.read_schema(target)
        return (schema.metadata or {}).get(b"type") == _TYPE and (
            pq.read_metadata(target).num_rows == pq.read_metadata(source).num_rows
        )
    except (OSError, pa.ArrowInvalid):  # unreadable target: a real clash
        return False


def plan(catalog_path: str) -> list[tuple[Path, Path]]:
    """
    (source, target) for every file still in an old directory.

    Raises FileExistsError if a target exists that is not a finished copy of its source.
    """
    root = Path(catalog_path)
    moves = [
        (f, root / _TARGET_DIR / f.parent.name / f.name)
        for source in _SOURCE_DIRS
        for f in sorted((root / source).glob("*/*.parquet"))
    ]
    clashes = [str(t) for f, t in moves if t.exists() and not _is_finished_copy(f, t)]
    if clashes:
        raise FileExistsError(f"target file(s) already exist, refusing to overwrite: {clashes[:5]}")
    return moves


def migrate_file(catalog_path: str, source: Path, target: Path, backup_dir: str) -> None:
    backup = Path(backup_dir) / source.relative_to(catalog_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():  # finished copy from an interrupted run (checked in plan)
        shutil.copy2(source, backup)
        source.unlink()
        return
    table = pq.read_table(source)
    meta = {**(table.schema.metadata or {}), b"type": _TYPE}
    new = table.replace_schema_metadata(meta)
    shutil.copy2(source, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".parquet.tmp")
    pq.write_table(new, tmp, compression="zstd")
    if pq.read_table(tmp).num_rows != table.num_rows:
        raise RuntimeError(
            f"{source}: rewritten file has a different row count; source left in place"
        )
    os.replace(tmp, target)
    source.unlink()


def _remove_empty_dirs(catalog_path: str) -> None:
    for source in _SOURCE_DIRS:
        root = Path(catalog_path) / source
        if not root.is_dir():
            continue
        for d in sorted(root.glob("*")):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        if not any(root.iterdir()):
            root.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--backup-dir", help="required with --apply")
    parser.add_argument("--apply", action="store_true", help="move files (default: report only)")
    args = parser.parse_args(argv)
    if args.apply and not args.backup_dir:
        parser.error("--apply requires --backup-dir")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        moves = plan(args.catalog)
    except FileExistsError as exc:
        logger.error("%s", exc)
        return 1
    if not moves:
        logger.info("nothing to do: no old open-interest files")
        return 0
    logger.info("%d open-interest file(s) to move into %s", len(moves), _TARGET_DIR)
    if not args.apply:
        return 0
    for i, (source, target) in enumerate(moves, 1):
        migrate_file(args.catalog, source, target, args.backup_dir)
        if i % 500 == 0:
            logger.info("migrated %d/%d", i, len(moves))
    _remove_empty_dirs(args.catalog)
    logger.info("done: %d file(s) migrated; originals in %s", len(moves), args.backup_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
