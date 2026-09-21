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
    python -m collector_core.build_candles --catalog /app/catalog --db /app/candles_dir/candles.db \\
        [--instrument BTC-USD-PERP.DYDX ...] [--venue DYDX] [--start 2026-09-01] [--end 2026-09-18] \\
        [--day 2026-09-20] [--include-open-day]

Use it for first population and to repair after a "candle store write failed" error. Idempotent: each
UTC day is deleted and recomputed whole. Today is skipped unless --include-open-day, which is only safe
with the collector stopped (it would otherwise lose seconds the collector applied but the catalog has
not flushed yet). Reads only the OHLC + volume columns (`query_second_ohlc`), one day at a time (MEM-01).
`--day D` is `--start D --end D` (the nightly job's form, after `rebuild_seconds` rewrote D's trade
columns); `--venue V` keeps only instrument ids ending in `.V`.
"""

import argparse
import glob
import logging
import os
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC
from datetime import datetime
from pathlib import Path

from ml_signals import candle_store
from ml_signals.catalog_stats import _stamp_to_ns
from ml_signals.catalog_stats import data_file_ranges
from ml_signals.catalog_stats import second_ohlc_arrays


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000
_SNAPSHOT_DIR = "custom_dydx_second_snapshot"


def venue_instruments(ids: list[str], venue: str | None) -> list[str]:
    """Keep the ids of one venue (Nautilus id suffix `.VENUE`); all of them when `venue` is None."""
    return ids if venue is None else [i for i in ids if i.endswith(f".{venue}")]


def all_instruments(catalog_path: str) -> list[str]:
    root = Path(catalog_path) / "data" / _SNAPSHOT_DIR
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def data_range_ns(catalog_path: str, iid: str) -> tuple[int, int] | None:
    """(first, last) timestamp covered by an instrument's snapshot files, from filenames only."""
    ranges = data_file_ranges(catalog_path, iid)
    return (ranges[0][0], ranges[-1][1]) if ranges else None


def day_chunks(start_ns: int, end_ns: int) -> Iterator[tuple[int, int]]:
    """Inclusive [a, b] day-aligned windows covering [start_ns, end_ns] (used by repair_catalog)."""
    day = start_ns // _DAY_NS
    while day * _DAY_NS <= end_ns:
        yield day * _DAY_NS, (day + 1) * _DAY_NS - 1
        day += 1


def _files_by_day(catalog_path: str, iid: str, start_ns: int, end_ns: int) -> dict[int, list[str]]:
    """
    UTC day index -> that day's snapshot files, from one directory listing. A file whose span
    crosses midnight is listed under both days; the rebuild filters rows by timestamp.
    """
    days: dict[int, list[str]] = {}
    for path in glob.glob(os.path.join(catalog_path, "data", _SNAPSHOT_DIR, iid, "*.parquet")):
        first, _, last = Path(path).stem.partition("_")
        a, b = _stamp_to_ns(first), _stamp_to_ns(last)
        if b < start_ns or a > end_ns:
            continue
        for day in range(a // _DAY_NS, b // _DAY_NS + 1):
            days.setdefault(day, []).append(path)
    return days


def rebuild_instrument(
    db_path: str,
    catalog_path: str,
    iid: str,
    start_ns: int,
    end_ns: int,
    allow_open_day: bool = False,
) -> int:
    """
    Rebuild one instrument day by day (MEM-01: one day of columns in memory at a time). Opens its
    own connection so it can run in a worker process. Returns the number of seconds applied.
    """
    db = candle_store.connect_rw(db_path)
    seconds = 0
    for day, paths in sorted(_files_by_day(catalog_path, iid, start_ns, end_ns).items()):
        cols = second_ohlc_arrays(paths)
        seconds += candle_store.rebuild_from_arrays(
            db,
            iid,
            cols,
            day * 86_400_000,
            (day + 1) * 86_400_000,
            allow_open_day,
        )
    db.close()
    return seconds


def _parse_date_ns(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def main() -> None:
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
    args = parser.parse_args()
    if args.day and (args.start or args.end):
        parser.error("--day cannot be combined with --start/--end")
    if args.day:
        args.start = args.end = args.day
    logging.basicConfig(level=logging.INFO)

    candle_store.connect_rw(args.db).close()  # create the schema once, before workers race for it
    jobs = []
    for iid in venue_instruments(args.instrument or all_instruments(args.catalog), args.venue):
        span = data_range_ns(args.catalog, iid)
        if span is None:
            logger.warning("%s: no second snapshots, skipping", iid)
            continue
        start_ns = _parse_date_ns(args.start) if args.start else span[0]
        end_ns = _parse_date_ns(args.end) + _DAY_NS - 1 if args.end else span[1]
        jobs.append((args.db, args.catalog, iid, start_ns, end_ns, args.include_open_day))
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for job, seconds in zip(
            jobs, pool.map(rebuild_instrument, *zip(*jobs, strict=True)), strict=True
        ):
            logger.info("%s: %d seconds applied", job[2], seconds)


if __name__ == "__main__":
    main()
