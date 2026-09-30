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
Rebuild the SQLite candle store from the Parquet 1s snapshots (the archive is the source of truth).

Usage:
    python -m candles.rebuild --catalog /app/catalog --db /app/candles_dir/candles.db \\
        [--instrument BTCUSDT-LINEAR.BYBIT ...] [--venue BYBIT] \\
        [--start 2026-09-01] [--end 2026-09-18] [--day 2026-09-20] [--include-open-day]

Use it for first population and to repair after a "candle store write failed" error. Idempotent: each
UTC day is deleted and recomputed whole. Today is skipped unless --include-open-day, which is only safe
with the collector stopped (it would otherwise lose seconds the collector applied but the catalog has
not flushed yet). Reads only the OHLC + volume columns (`query_second_ohlc`), one day at a time (MEM-01).
`--day D` is `--start D --end D` (the nightly job's form, after `rebuild_seconds` rewrote D's trade
columns); `--venue V` keeps only instrument ids ending in `.V`.

A `--catalog` that is not an existing directory is a wrong mount or a typo, never an empty catalog:
listed, it would read as "no instruments" and exit 0 over nothing (DATA-07). It is refused at the
archive tools' one site, `archive.catalog_missing`, naming this tool, exit 1, before the store is
opened (audit D-123). An existing catalog without snapshots is a real "nothing to rebuild" (exit 0).
"""

import argparse
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from observability import error_ledger

from candles.application.rebuild import DAY_NS
from candles.application.rebuild import all_instruments
from candles.application.rebuild import data_range_ns
from candles.application.rebuild import parse_date_ns
from candles.application.rebuild import rebuild_instrument
from candles.application.rebuild import venue_instruments
from candles.infrastructure.sqlite_store import CandleStore


logger = logging.getLogger(__name__)

# The archive tools' one missing-catalog site (`archive.application.catalog_check`), restated: this
# context never imports `archive` (`tests/test_boundaries.py`), but the nightly step it runs as
# refuses a missing catalog at the same site as its siblings.
CATALOG_MISSING_SITE = "archive.catalog_missing"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--db", required=True, help="path of candles.db")
    parser.add_argument(
        "--instrument", action="append", help="repeatable; default: all with snapshots"
    )
    parser.add_argument("--venue", help="only ids of this venue, e.g. DYDX")
    parser.add_argument("--start", help="YYYY-MM-DD (UTC); default: first snapshot")
    parser.add_argument("--end", help="YYYY-MM-DD (UTC, inclusive); default: last snapshot")
    parser.add_argument("--day", help="YYYY-MM-DD (UTC): shorthand for --start D --end D")
    parser.add_argument(
        "--include-open-day",
        action="store_true",
        help="also rebuild today; collector must be stopped",
    )
    parser.add_argument(
        "--workers", type=int, default=os.cpu_count() or 1, help="coins rebuilt in parallel"
    )
    return parser


def _jobs(args: argparse.Namespace) -> list[tuple[str, str, str, int, int, bool]]:
    jobs = []
    for iid in venue_instruments(args.instrument or all_instruments(args.catalog), args.venue):
        span = data_range_ns(args.catalog, iid)
        if span is None:
            logger.warning("%s: no second snapshots, skipping", iid)
            continue
        start_ns = parse_date_ns(args.start) if args.start else span[0]
        end_ns = parse_date_ns(args.end) + DAY_NS - 1 if args.end else span[1]
        jobs.append((args.db, args.catalog, iid, start_ns, end_ns, args.include_open_day))
    return jobs


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.day and (args.start or args.end):
        parser.error("--day cannot be combined with --start/--end")
    if args.day:
        args.start = args.end = args.day
    logging.basicConfig(level=logging.INFO)
    # Its own durable file (Story 31.8): the nightly saga's `build_candles` step runs this as a
    # child process, whose ledger lines once reached stdout only.
    # Known limit: the sink is opened here, before the ProcessPoolExecutor forks, so every worker
    # inherits it and counts its own size toward rotation. Each process appends whole lines, so no
    # line is torn, but one process's rotation can leave another still writing to the renamed
    # backup, and the per-site write cap is per process. The nightly runs `--workers 1`, so only
    # one process writes at a time there; a manual multi-worker run can split a run's lines across
    # the live file and a backup. Upgrade path: a per-worker sink opened in a pool initializer (its
    # own job name), or one writer fed by the workers through a queue.
    error_ledger.start(service=error_ledger.job_service("candles_rebuild", "archive", args.venue))
    if not Path(args.catalog).is_dir():
        error_ledger.record(
            CATALOG_MISSING_SITE,
            f"candles_rebuild: catalog {args.catalog} does not exist or is not a directory "
            "(wrong mount?); nothing done",
        )
        raise SystemExit(1)

    CandleStore(args.db).close()  # create the schema once, before workers race for it
    jobs = _jobs(args)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for job, seconds in zip(
            jobs, pool.map(rebuild_instrument, *zip(*jobs, strict=True)), strict=True
        ):
            logger.info("%s: %d seconds applied", job[2], seconds)


if __name__ == "__main__":
    main()
