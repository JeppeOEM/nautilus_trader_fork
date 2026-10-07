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

Usage (the `archive` service runs it after every nightly run and, with --closed-hours, every
`intraday_consolidate_hours`; `make consolidate` is the manual tool):
    python -m archive.consolidate_catalog --catalog /app/catalog --apply [--days 3] [--data-type custom_dydx_second_snapshot ...] [--venue BYBIT]
    python -m archive.consolidate_catalog --catalog /app/catalog --apply --closed-hours [--venue BYBIT]

How and why is `archive.application.consolidate_day`'s docstring (audit D-36). One run at a time:
the catalog maintenance flock (`<catalog>/.consolidate.lock`, also taken by `rebuild_seconds`,
`prune_catalog` and the repair/migration tools), so no two catalog-rewriting jobs overlap.
Report-only unless --apply; a report-only run writes nothing and takes no lock. A missing catalog
is `archive.catalog_missing`, exit 1. `--venue V` limits the run to leaves whose instrument directory ends in
`.V` (the nightly job's per-venue form).

`--closed-hours` (Story 25.1b) merges instead the closed hours of the *current* UTC day of the small
types only (mark/index price, funding rate, open interest, instrument status --
`archive.domain.intraday`): each hour before the current one with more than one file wholly inside
it becomes one file; a file reaching the current hour is never touched. It takes neither `--days`
nor `--data-type`, and `--apply` takes the maintenance flock exactly as a day run does.

Each run ends with one summary line (days consolidated, refused and of those mixed-schema, leaves
failed, files and MB before -> after, wall seconds, peak RSS of this process). Exit code (DW-213):
1 when anything other than a mixed-schema refusal went wrong -- a period refused for another
reason (merged or covering file failing verification, partial commit, open-day write, unreadable
file), an abandoned leaf, a held lock, a missing catalog, a usage error (argparse's own 2 is
mapped to 1, so a bad command line never reads as findings); else 2 (`archive.application.nightly`'s
FINDINGS, so the nightly saga continues) when a period was refused for differing schemas (D-24);
else 0. The same rule holds for --closed-hours and report-only runs. Every refusal's detail is in
the error ledger and the log above the summary (DATA-07).
"""

import argparse
import logging
import time
from pathlib import Path

from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.consolidate_day import RunStats
from archive.application.consolidate_day import run
from archive.application.consolidate_day import run_closed_hours
from archive.application.nightly import FINDINGS
from archive.application.ports import CatalogWriter
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)


def _consolidate(writer: CatalogWriter | None, args: argparse.Namespace) -> RunStats:
    if args.closed_hours:
        return run_closed_hours(writer, args.catalog, time.time_ns(), args.apply, args.venue)
    return run(
        writer, args.catalog, args.data_type, args.days, args.apply, time.time_ns(), args.venue
    )


def _run(args: argparse.Namespace) -> RunStats | None:
    """Apply under the maintenance flock (None when it is held); a report takes no lock."""
    if not args.apply:
        return _consolidate(None, args)
    catalog = Path(args.catalog)
    with maintenance(catalog) as writer:
        if writer is None:
            logger.error(
                "consolidate: another run holds %s; not starting", catalog / MAINTENANCE_LOCK_NAME
            )
            return None
        return _consolidate(writer, args)


def _exit_code(stats: RunStats) -> int:
    """
    1 when a period was refused for another reason than differing schemas or a leaf was abandoned,
    else 2 (findings) when a period was refused for differing schemas, else 0 -- logged loudly
    whenever it is not 0 (DATA-07).
    """
    failures = stats.days_refused - stats.days_mixed_schema + stats.leaves_failed
    if not stats.days_refused and not stats.leaves_failed:
        return 0
    logger.error(
        "consolidate: %d %s(s) refused (%d mixed-schema), %d leaf/leaves failed -- see the "
        "consolidate.* entries above (DATA-07)",
        stats.days_refused,
        stats.unit,
        stats.days_mixed_schema,
        stats.leaves_failed,
    )
    return 1 if failures else FINDINGS


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
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
    parser.add_argument(
        "--closed-hours",
        action="store_true",
        help="merge the current UTC day's closed hours of the small types instead of closed days",
    )
    args = parser.parse_args(argv)
    if args.days is not None and args.days < 1:
        parser.error("--days must be at least 1")
    if args.closed_hours and (args.days is not None or args.data_type):
        parser.error("--closed-hours takes neither --days nor --data-type")
    return args


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point; returns the process exit code: 1 on any failure (a usage error included), 2
    when the only problems were mixed-schema refusals, else 0 (module docstring).
    """
    try:
        args = _parse_args(argv)
    except SystemExit as e:  # argparse exits 2 on a usage error, which the saga reads as FINDINGS
        return 0 if e.code in (0, None) else 1
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Its own durable file (Story 31.8): a child of the nightly saga and the intraday run, whose
    # ledger lines once reached stdout only.
    error_ledger.start(
        service=error_ledger.job_service("consolidate_catalog", "archive", args.venue)
    )
    if catalog_missing("consolidate", args.catalog):
        return 1
    stats = _run(args)
    if stats is None:
        return 1
    logger.info("consolidate: %s", stats.summary(args.apply))
    return _exit_code(stats)


if __name__ == "__main__":
    raise SystemExit(main())
