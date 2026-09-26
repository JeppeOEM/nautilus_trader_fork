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
Rebuild a closed UTC day's second-snapshot trade columns from the raw trade archive (story 22.13).

Usage:
    python -m archive.rebuild_seconds --catalog /app/catalog --day 2026-09-20 \\
        [--instrument BTC-USD-PERP.DYDX ...] [--venue DYDX] [--apply] [--include-open-day] \\
        [--result-file PATH]

What is rebuilt, and why, is `archive.application.rebuild_day`'s docstring: rows move to their
exchange-time second, rows before the archive (`not covered`) or inside an archive gap (`in gap`)
keep their live values, orphan trades and duplicates are counted, never dropped.

Refusals are per instrument-day (error ledger, that instrument-day untouched, the run continues and
exits 2). Run-level failures exit 1: today (or later) without `--include-open-day` (only safe
with that venue's collector stopped), `--apply` on today at all (`rebuild.open_day`: capture is
that day's writer, so `--include-open-day` can only ever report), a missing catalog, the
maintenance lock held elsewhere.

`--result-file PATH` (needs `--apply`; the nightly saga passes it) writes
`{"venue", "day", "rebuilt": [...], "refused": [...]}` after the run: the rebuild proof the saga
carries to `compare_klines`. `rebuilt` names only instruments with snapshot rows on the day; one
with none is in neither list (nothing was rebuilt, so nothing is proven). Standalone, the run logs
a run id to hand `compare_klines --rebuilt-by` (with `--rebuilt` per rebuilt instrument) instead.
Files are rewritten only through `CatalogFiles` (verified temp-then-rename), under the catalog
maintenance flock; a report-only run writes nothing and takes no lock. Run `python -m
candles.rebuild --day D` afterwards: the candle store is folded from these columns.
"""

import argparse
import json
import logging
import resource
import time
import uuid
from pathlib import Path

from candles.application.rebuild import parse_date_ns
from kernel.venues import VENUE_KINDS
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.rebuild_day import RunResult
from archive.application.rebuild_day import day_text
from archive.application.rebuild_day import instruments
from archive.application.rebuild_day import run
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000
_REFUSED_EXIT = 2  # finished, but some instrument-days were refused (ledgered, untouched)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument(
        "--venue", choices=sorted(VENUE_KINDS), help="only ids of this venue, e.g. DYDX"
    )
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report)")
    parser.add_argument(
        "--include-open-day",
        action="store_true",
        help="report on today too (never with --apply); collector must be stopped",
    )
    parser.add_argument(
        "--result-file", help="write the rebuild result JSON here (the nightly saga's proof)"
    )
    return parser


def _open_day_refused(args: argparse.Namespace, day_start_ns: int) -> bool:
    if day_start_ns < time.time_ns() // _DAY_NS * _DAY_NS:
        return False
    if args.apply:
        detail = f"{args.day} is not a closed UTC day; --apply is refused on it (capture writes it)"
    elif not args.include_open_day:
        detail = f"{args.day} is not a closed UTC day; --include-open-day to report on it"
    else:
        return False
    error_ledger.record("rebuild.open_day", detail)
    return True


def _write_result(path: str, venue: str | None, day: str, result: RunResult) -> None:
    body = {
        "venue": venue,
        "day": day,
        "rebuilt": sorted(result.rebuilt),
        "refused": sorted(result.refused),
    }
    Path(path).write_text(json.dumps(body))


def _run(args: argparse.Namespace, iids: list[str], day_start_ns: int) -> RunResult | None:
    """Apply under the maintenance flock (None when it is held); a report takes no lock."""
    gaps = GapMarkerFiles(args.catalog)
    if not args.apply:
        return run(args.catalog, iids, day_start_ns, None, gaps, apply=False)
    with maintenance(args.catalog) as writer:
        if writer is None:
            logger.error("rebuild: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return None
        return run(args.catalog, iids, day_start_ns, writer, gaps, apply=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 0, 2 when some instrument-days were refused, 1 on a run failure."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.result_file and not args.apply:
        parser.error("--result-file is the proof of a rebuild: it needs --apply")
    if args.result_file and not args.venue:
        parser.error("--result-file is one venue-day's proof: it needs --venue")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = time.monotonic()
    day_start_ns = parse_date_ns(args.day)
    if _open_day_refused(args, day_start_ns):
        return 1
    if catalog_missing("rebuild", args.catalog):
        return 1
    iids = instruments(args.catalog, args.instrument, args.venue)
    result = _run(args, iids, day_start_ns)
    if result is None:
        return 1
    if args.result_file:
        _write_result(args.result_file, args.venue, day_text(day_start_ns), result)
    elif args.apply:
        run_id = uuid.uuid4().hex
        # The proof is the allowlist, not the run id: `--rebuilt-by` alone compares nothing.
        flags = [f"--rebuilt {iid}" for iid in sorted(result.rebuilt)]
        flags += [f"--not-rebuilt {iid}" for iid in sorted(result.refused)]
        logger.info(
            "rebuild %s: run id %s; pass --rebuilt-by %s %s to compare_klines",
            args.day,
            run_id,
            run_id,
            " ".join(flags),
        )
    # ru_maxrss is KiB on Linux (the collector image).
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    logger.info("rebuild: %.1f s wall; peak RSS %.0f MB", time.monotonic() - started, peak_mb)
    return _REFUSED_EXIT if result.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
