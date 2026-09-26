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
    python -m archive.consolidate_catalog --catalog /app/catalog --apply [--days 3] [--data-type custom_dydx_second_snapshot ...] [--venue BYBIT]

How and why is `archive.application.consolidate_day`'s docstring (audit D-36). One run at a time:
the catalog maintenance flock (`<catalog>/.consolidate.lock`, also taken by `rebuild_seconds`,
`prune_catalog` and the repair/migration tools), so no two catalog-rewriting jobs overlap.
Report-only unless --apply; a report-only run writes nothing and takes no lock. A missing catalog
is `archive.catalog_missing`, exit 1. `--venue V` limits the run to leaves whose instrument directory ends in
`.V` (the nightly job's per-venue form).

Each run ends with one summary line (days consolidated/refused, files and MB before -> after, wall
seconds, peak RSS of this process) and exits 1 when any day was refused (DATA-07): the refusal's
detail is in the error ledger and the log above the summary.
"""

import argparse
import logging
import time
from pathlib import Path

from archive.application.catalog_check import catalog_missing
from archive.application.consolidate_day import RunStats
from archive.application.consolidate_day import run
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)


def _run(args: argparse.Namespace) -> RunStats | None:
    """Apply under the maintenance flock (None when it is held); a report takes no lock."""
    if not args.apply:
        return run(None, args.catalog, args.data_type, args.days, False, time.time_ns(), args.venue)
    catalog = Path(args.catalog)
    with maintenance(catalog) as writer:
        if writer is None:
            logger.error(
                "consolidate: another run holds %s; not starting", catalog / MAINTENANCE_LOCK_NAME
            )
            return None
        return run(
            writer, args.catalog, args.data_type, args.days, True, time.time_ns(), args.venue
        )


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
    if catalog_missing("consolidate", args.catalog):
        return 1
    stats = _run(args)
    if stats is None:
        return 1
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
