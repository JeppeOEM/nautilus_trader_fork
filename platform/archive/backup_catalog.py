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
r"""
Copy the catalog's closed-day files to object storage (audit D-33: there is no other copy).

Usage (the `archive` service runs it after every nightly run's consolidate; `make backup-catalog`
runs it by hand in the same image):
    python -m archive.backup_catalog --catalog /app/catalog

The module form of the former host-side `make backup-catalog` recipe (Story 25.1b), with the same
guards and the same command, now run by the `rclone` of the collector image:

    rclone sync <catalog>/data <REMOTE>:<BUCKET>/catalog/data \
        --backup-dir <REMOTE>:<BUCKET>/catalog-replaced/<UTC stamp> \
        --exclude "**/<today>T*" --exclude "*.tmp" --transfers 8 --fast-list

The target is `RCLONE_REMOTE` (the bare remote name; a trailing ':' is tolerated) and
`RCLONE_BUCKET` from the environment (compose passes platform/.env's values); rclone's own config
comes from `RCLONE_CONFIG` (the operator's rclone config directory, mounted read-only). Today's
(UTC) files, still being written, are excluded, as are `*.tmp` leftovers of an interrupted archive
rewrite. `sync` removes on the remote what the local catalog no longer has (every consolidation
replaces minute files); `--backup-dir` moves those objects under a dated `catalog-replaced/`
prefix instead of deleting them, so a wiped local catalog can never erase the only backup. Prune
`catalog-replaced/` only by hand, after checking the remote's `catalog/data`.

The sync runs under the catalog maintenance flock (`<catalog>/.consolidate.lock`, as every
catalog-rewriting tool takes it), so a consolidate can never remove the minute files rclone is
listing or uploading mid-sync; while a backup runs, a manual `make nightly`/`make consolidate`
refuses to start (the `archive` service waits for the lock instead). A held lock is
`archive.backup_failed`, exit 1.

Refusals, each one ledger entry and exit 1: an unset target (`archive.backup_not_configured`); a
missing catalog (`archive.catalog_missing`); no rclone, an absent `data/` or one holding no
closed-day `.parquet` file -- an empty or freshly wiped catalog is never synced over the backup --
and a failed rclone run (`archive.backup_failed`).

Known limit: the rclone config is mounted read-only, so a remote whose OAuth token rclone must
refresh cannot save the new token (R2/B2 use static keys, which need no refresh). Upgrade path: a
writable copy of the config in the `archive` service's state directory.
"""

import argparse
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path

from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)

_DAY_FORMAT = "%Y-%m-%d"
_STAMP_FORMAT = "%Y-%m-%dT%H-%M-%SZ"


def remote_target(remote: str, bucket: str) -> str:
    """`<remote>:<bucket>`, from the bare remote name (one trailing ':' tolerated)."""
    return f"{remote.removesuffix(':')}:{bucket}"


def backup_argv(source: Path, target: str, today: str, stamp: str) -> list[str]:
    """Build the rclone command: `source` (the catalog's `data/`) to `<target>/catalog/data`."""
    return [
        "rclone",
        "sync",
        str(source),
        f"{target}/catalog/data",
        "--backup-dir",
        f"{target}/catalog-replaced/{stamp}",
        "--exclude",
        f"**/{today}T*",
        "--exclude",
        "*.tmp",
        "--transfers",
        "8",
        "--fast-list",
    ]


def _holds_closed_day_file(source: Path, today: str) -> bool:
    """Whether `source` holds any `.parquet` file not of today (stops at the first one)."""
    return any(not p.name.startswith(f"{today}T") for p in source.rglob("*.parquet"))


def _refusal(source: Path, today: str, which: Callable[[str], str | None]) -> str | None:
    """Why the sync must not run (`archive.backup_failed`), or None."""
    if which("rclone") is None:
        return "rclone is not installed in this image"
    if not source.is_dir():
        return f"{source} does not exist -- refusing to sync an absent catalog"
    if not _holds_closed_day_file(source, today):
        return f"{source} holds no closed-day .parquet file -- refusing to sync an empty catalog"
    return None


def backup(
    catalog: str,
    env: Mapping[str, str],
    runner: Callable[[list[str]], int],
    now_ns: int,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    """Run the guarded sync; return 0, or 1 after one ledger entry naming why not."""
    remote, bucket = env.get("RCLONE_REMOTE", "").strip(), env.get("RCLONE_BUCKET", "").strip()
    if not remote or not bucket:
        error_ledger.record(
            "archive.backup_not_configured",
            "RCLONE_REMOTE or RCLONE_BUCKET is not set (platform/.env; README 'Nightly "
            "maintenance'); catalog not backed up",
        )
        return 1
    if catalog_missing("backup", catalog):
        return 1
    seconds = now_ns / 1e9
    today = time.strftime(_DAY_FORMAT, time.gmtime(seconds))
    source = Path(catalog) / "data"
    refusal = _refusal(source, today, which)
    if refusal is not None:
        error_ledger.record("archive.backup_failed", refusal)
        return 1
    stamp = time.strftime(_STAMP_FORMAT, time.gmtime(seconds))
    argv = backup_argv(source, remote_target(remote, bucket), today, stamp)
    logger.info("backup: %s", " ".join(argv))
    code = runner(argv)
    if code != 0:
        error_ledger.record("archive.backup_failed", f"rclone sync exited {code}")
        return 1
    return 0


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode  # noqa: S603 (a fixed rclone argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code (0 synced, 1 refused or failed)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if catalog_missing("backup", args.catalog):
        return 1
    with maintenance(args.catalog) as writer:  # held for exclusion only; the sync writes nothing
        if writer is None:
            error_ledger.record(
                "archive.backup_failed",
                f"another maintenance run holds {Path(args.catalog) / MAINTENANCE_LOCK_NAME}; "
                "catalog not backed up",
            )
            return 1
        return backup(args.catalog, os.environ, _run, time.time_ns())


if __name__ == "__main__":
    raise SystemExit(main())
