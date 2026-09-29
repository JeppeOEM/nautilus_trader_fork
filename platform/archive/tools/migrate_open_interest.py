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
`HyperliquidOpenInterest` rows; all three classes are now `kernel.open_interest.OpenInterest`,
whose catalog directory is `custom_open_interest/`. Until this runs, history in the old directories
is read by nothing (DATA-05: no silent gap).

Each file is moved with its name unchanged (the name encodes the time range the catalog reads), its
Arrow `type` metadata rewritten to `OpenInterest`. Row values are untouched.

Usage:
    python -m archive.tools.migrate_open_interest --catalog /app/catalog                        # report only
    python -m archive.tools.migrate_open_interest --catalog /app/catalog --backup-dir /backup --apply

--apply refuses to run without --backup-dir: each source file is copied there (same relative path)
before anything is touched, and the new file is written through `CatalogFiles.rewrite` (a verified
temp-then-rename, compact settings) and only then is the source removed, so an interrupted run
leaves either the source or the target, never a torn file. Idempotent: with no old directories
left it does nothing. A target file that already exists (and is not a finished copy of its source) aborts
before anything is touched (never overwritten). One file in memory at a time (MEM-01). Holds the
catalog maintenance flock. A file whose span reaches the current UTC day is skipped
(`migrate_open_interest.open_day`, with the count; exit 2): capture writes that day. A file that
fails on its own (unreadable, a rewrite failing its read-back, an I/O error) is ledgered
(`migrate_open_interest.error`, one entry per file), left as it was, and the run goes on to the
next (exit 2). A missing catalog is `archive.catalog_missing`, exit 1.
"""

import argparse
import logging
import shutil
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from kernel.clocks import CatalogFileSpan
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.ports import CatalogWriter
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteVerifyError
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)

_DATA = Path("data")
_TARGET_DIR = _DATA / "custom_open_interest"
_SOURCE_DIRS = tuple(
    _DATA / f"custom_{venue}_open_interest" for venue in ("dydx", "bybit", "hyperliquid")
)
_TYPE = b"OpenInterest"


def _is_finished_copy(source: Path, target: Path) -> bool:
    """Detect a run that crashed between the rename and the source removal: same rows, retyped."""
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


def migrate_file(
    writer: CatalogWriter, catalog_path: str, source: Path, target: Path, backup_dir: str
) -> None:
    """Back up, retype and move one file; `OpenDayWriteError` (nothing touched) for today's."""
    span = CatalogFileSpan.from_path(target)
    writer.assert_span_closed(span.start_ns, span.end_ns)
    backup = Path(backup_dir) / source.relative_to(catalog_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():  # finished copy from an interrupted run (checked in plan)
        shutil.copy2(source, backup)
        writer.remove_merged_sources([source])
        return
    table = pq.read_table(source)
    meta = {**(table.schema.metadata or {}), b"type": _TYPE}
    new = table.replace_schema_metadata(meta)
    shutil.copy2(source, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    writer.rewrite(target, new)
    # The rows now live in `target`: removing the source is a move, not retention.
    writer.remove_merged_sources([source])


def _remove_empty_dirs(writer: CatalogWriter, catalog_path: str) -> None:
    for source in _SOURCE_DIRS:
        root = Path(catalog_path) / source
        if not root.is_dir():
            continue
        for d in sorted(root.glob("*")):
            writer.remove_empty_dir(d)
        writer.remove_empty_dir(root)


def _migrate_one(
    writer: CatalogWriter, catalog_path: str, source: Path, target: Path, backup_dir: str
) -> str:
    """Migrate one file; return "done", "open_day" or "error" (ledgered, the file as it was)."""
    try:
        migrate_file(writer, catalog_path, source, target, backup_dir)
    except OpenDayWriteError:
        return "open_day"  # counted, ledgered once for the run
    except (RewriteVerifyError, PartialCommitError, OSError, pa.ArrowException, ValueError) as e:
        error_ledger.record(
            "migrate_open_interest.error", f"{source}: {e!r}; not migrated, left as it was", exc=e
        )
        return "error"
    return "done"


def _migrate_all(
    writer: CatalogWriter, catalog_path: str, moves: list[tuple[Path, Path]], backup_dir: str
) -> Counter[str]:
    """Migrate every closed file, one failure never stopping the rest; count the outcomes."""
    outcomes: Counter[str] = Counter()
    for i, (source, target) in enumerate(moves, 1):
        outcomes[_migrate_one(writer, catalog_path, source, target, backup_dir)] += 1
        if i % 500 == 0:
            logger.info("migrated %d/%d", i, len(moves))
    _remove_empty_dirs(writer, catalog_path)
    return outcomes


def _planned(catalog_path: str) -> list[tuple[Path, Path]] | None:
    """Plan the moves and log them; None (logged) when a target clashes."""
    try:
        moves = plan(catalog_path)
    except FileExistsError as exc:
        logger.error("%s", exc)
        return None
    if not moves:
        logger.info("nothing to do: no old open-interest files")
    else:
        logger.info("%d open-interest file(s) to move into %s", len(moves), _TARGET_DIR)
    return moves


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
            "migrate_open_interest.open_day",
            f"{outcomes['open_day']} file(s) reach the current UTC day and were skipped; "
            "rerun tomorrow",
        )
    return 2 if outcomes["open_day"] or outcomes["error"] else 0


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point; 0 done, 1 refused (missing catalog, target clash, lock held), 2 files
    skipped for the open day or failed on their own (ledgered).
    """
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
    if catalog_missing("migrate_open_interest", args.catalog):
        return 1
    if not args.apply:
        return 1 if _planned(args.catalog) is None else 0
    # The plan -- the clash check and the file list -- is made under the lock it is executed
    # under, so no other maintenance run can change the directories in between.
    with maintenance(args.catalog) as writer:
        if writer is None:
            logger.error("migrate: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return 1
        moves = _planned(args.catalog)
        if moves is None:
            return 1
        if not moves:
            return 0
        outcomes = _migrate_all(writer, args.catalog, moves, args.backup_dir)
    return _finished(outcomes, args.backup_dir)


if __name__ == "__main__":
    sys.exit(main())
