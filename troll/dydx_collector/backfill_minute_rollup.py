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
Backfill DydxMinuteRollup rows from existing DydxSecondSnapshot history.

Usage:
    python -m dydx_collector.backfill_minute_rollup --catalog /app/catalog \\
        [--instrument BTC-USD-PERP.DYDX ...] [--start 2026-09-01] [--end 2026-09-18]

Defaults: every instrument that has second-snapshot data, over that data's full range.
Manually run, like prune_catalog.py; reads raw 1s in day-sized chunks (MEM-01) through one
long-lived MinuteRollupBuilder per instrument, so OFI carries across chunk boundaries.

The catalog is append-only: re-running over an already-backfilled range writes duplicate
rows. Clear `<catalog>/data/custom_dydx_minute_rollup/` first (same wipe-and-rebuild
pattern as scripts/wipe_data.sh). Also do not run it over a range the live collector is
already writing rollups for.
"""

import argparse
import logging
import re
from collections.abc import Iterator
from datetime import UTC
from datetime import datetime
from pathlib import Path

from dydx_collector.minute_rollup import DydxMinuteRollup
from dydx_collector.minute_rollup import MinuteRollupBuilder
from ml_signals.catalog_stats import query_second_snapshots
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000
_SNAPSHOT_DIR = "custom_dydx_second_snapshot"
_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-(\d{9})Z_(.+)-(\d{9})Z\.parquet$")


def _filename_ns(stamp: str, nanos: str) -> int:
    dt = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=UTC)
    return int(dt.timestamp()) * 1_000_000_000 + int(nanos)


def data_range_ns(catalog_path: str, iid: str) -> tuple[int, int] | None:
    """(first, last) ts_event covered by an instrument's snapshot files, from filenames only."""
    starts, ends = [], []
    for f in (Path(catalog_path) / "data" / _SNAPSHOT_DIR / iid).glob("*.parquet"):
        m = _FILENAME_RE.match(f.name)
        if m:
            starts.append(_filename_ns(m.group(1), m.group(2)))
            ends.append(_filename_ns(m.group(3), m.group(4)))
    return (min(starts), max(ends)) if starts else None


def all_instruments(catalog_path: str) -> list[str]:
    root = Path(catalog_path) / "data" / _SNAPSHOT_DIR
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def day_chunks(start_ns: int, end_ns: int) -> Iterator[tuple[int, int]]:
    """Inclusive [a, b] day-aligned windows covering [start_ns, end_ns]."""
    day = start_ns // _DAY_NS
    while day * _DAY_NS <= end_ns:
        yield day * _DAY_NS, (day + 1) * _DAY_NS - 1
        day += 1


def backfill_instrument(
    catalog: ParquetDataCatalog, catalog_path: str, iid: str, start_ns: int, end_ns: int
) -> int:
    builder = MinuteRollupBuilder()  # one per instrument, reused across every chunk
    written = 0
    for a, b in day_chunks(start_ns, end_ns):
        rollups: list[DydxMinuteRollup] = []
        for snap in query_second_snapshots(catalog_path, iid, a, min(b, end_ns)):
            rollup = builder.update(iid, snap)
            if rollup is not None:
                rollups.append(rollup)
        if rollups:
            catalog.write_data(rollups)
            written += len(rollups)
    return written


def _parse_date_ns(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument("--start", help="YYYY-MM-DD (UTC); default: start of data")
    parser.add_argument("--end", help="YYYY-MM-DD (UTC, inclusive); default: end of data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    catalog = ParquetDataCatalog(args.catalog)
    for iid in args.instrument or all_instruments(args.catalog):
        found = data_range_ns(args.catalog, iid)
        if found is None:
            logger.warning("%s: no second-snapshot data, skipping", iid)
            continue
        start_ns = _parse_date_ns(args.start) if args.start else found[0]
        end_ns = _parse_date_ns(args.end) + _DAY_NS - 1 if args.end else found[1]
        logger.info("%s: %d rollups written", iid, backfill_instrument(catalog, args.catalog, iid, start_ns, end_ns))


if __name__ == "__main__":
    main()
