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
Rebuild the SQLite candle store from the Parquet 1s snapshots (the archive is the source of truth).

Usage:
    python -m dydx_collector.build_candles --catalog /app/catalog --db /app/candles_dir/candles.db \\
        [--instrument BTC-USD-PERP.DYDX ...] [--start 2026-09-01] [--end 2026-09-18] [--include-open-day]

Use it for first population and to repair after a "candle store write failed" error. Idempotent: each
UTC day is deleted and recomputed whole. Today is skipped unless --include-open-day, which is only safe
with the collector stopped (it would otherwise lose seconds the collector applied but the catalog has
not flushed yet). Reads only the OHLC + volume columns (`query_second_ohlc`), one day at a time (MEM-01).
"""

import argparse
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC
from datetime import datetime
from pathlib import Path

from ml_signals import candle_store
from ml_signals.catalog_stats import data_file_ranges
from ml_signals.catalog_stats import query_second_ohlc


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000
_SNAPSHOT_DIR = "custom_dydx_second_snapshot"


def all_instruments(catalog_path: str) -> list[str]:
    root = Path(catalog_path) / "data" / _SNAPSHOT_DIR
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def day_chunks(start_ns: int, end_ns: int) -> Iterator[tuple[int, int]]:
    """Inclusive [a, b] day-aligned windows covering [start_ns, end_ns]."""
    day = start_ns // _DAY_NS
    while day * _DAY_NS <= end_ns:
        yield day * _DAY_NS, (day + 1) * _DAY_NS - 1
        day += 1


def rebuild_instrument(
    db: sqlite3.Connection, catalog_path: str, iid: str, start_ns: int, end_ns: int, allow_open_day: bool = False,
) -> int:
    """Rebuild one instrument day by day; returns the number of seconds applied."""
    seconds = 0
    for a, b in day_chunks(start_ns, end_ns):
        rows = query_second_ohlc(catalog_path, iid, a, b)
        seconds += candle_store.rebuild(db, iid, rows, a // 1_000_000, (b + 1) // 1_000_000, allow_open_day)
    return seconds


def _parse_date_ns(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--db", required=True, help="path of candles.db")
    parser.add_argument("--instrument", action="append", help="repeatable; default: all with snapshots")
    parser.add_argument("--start", help="YYYY-MM-DD (UTC); default: first snapshot")
    parser.add_argument("--end", help="YYYY-MM-DD (UTC, inclusive); default: last snapshot")
    parser.add_argument("--include-open-day", action="store_true", help="also rebuild today; collector must be stopped")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    db = candle_store.connect_rw(args.db)
    for iid in args.instrument or all_instruments(args.catalog):
        ranges = data_file_ranges(args.catalog, iid)
        if not ranges:
            logger.warning("%s: no second snapshots, skipping", iid)
            continue
        start_ns = _parse_date_ns(args.start) if args.start else ranges[0][0]
        end_ns = _parse_date_ns(args.end) + _DAY_NS - 1 if args.end else ranges[-1][1]
        logger.info("%s: %d seconds applied", iid, rebuild_instrument(db, args.catalog, iid, start_ns, end_ns, args.include_open_day))


if __name__ == "__main__":
    main()
