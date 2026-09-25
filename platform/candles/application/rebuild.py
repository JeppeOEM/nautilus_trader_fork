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
Rebuilding the store from the archive: the repair that makes every bar rebuildable from seconds.

The Parquet 1s snapshots are the source of truth, so any bucket can be recomputed from them. Whole
UTC days only, so no bucket is ever half-rebuilt, and idempotent, so a re-run changes nothing.
"""

import logging
from collections.abc import Iterator
from datetime import UTC
from datetime import datetime
from pathlib import Path

from kernel.catalog_files import SNAPSHOT_DIRNAME
from kernel.catalog_files import data_file_ranges
from kernel.catalog_files import files_by_day
from kernel.catalog_files import second_ohlc_arrays
from kernel.venues import has_venue

from candles.domain.fold import DAY_MS
from candles.infrastructure.sqlite_store import CandleStore


logger = logging.getLogger(__name__)

DAY_NS = 86_400 * 1_000_000_000


def venue_instruments(ids: list[str], venue: str | None) -> list[str]:
    """Keep the ids of one venue (Nautilus id suffix `.VENUE`); all of them when `venue` is None."""
    return ids if venue is None else [i for i in ids if has_venue(i, venue)]


def all_instruments(catalog_path: str) -> list[str]:
    root = Path(catalog_path) / "data" / SNAPSHOT_DIRNAME
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def data_range_ns(catalog_path: str, iid: str) -> tuple[int, int] | None:
    """(first, last) timestamp covered by an instrument's snapshot files, from filenames only."""
    ranges = data_file_ranges(catalog_path, iid)
    return (ranges[0][0], ranges[-1][1]) if ranges else None


def day_chunks(start_ns: int, end_ns: int) -> Iterator[tuple[int, int]]:
    """Inclusive [a, b] day-aligned windows covering [start_ns, end_ns] (used by repair_catalog)."""
    day = start_ns // DAY_NS
    while day * DAY_NS <= end_ns:
        yield day * DAY_NS, (day + 1) * DAY_NS - 1
        day += 1


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
    own store so it can run in a worker process. Returns the number of seconds applied.
    """
    store = CandleStore(db_path)
    seconds = 0
    try:
        for day, paths in sorted(files_by_day(catalog_path, iid, start_ns, end_ns).items()):
            cols = second_ohlc_arrays(paths)
            seconds += store.rebuild_day(iid, cols, day * DAY_MS, allow_open_day)
    finally:
        # A pool worker outlives the job that raised (`pool.map` surfaces the error only when the
        # result is consumed), so an unclosed read-write handle per failed instrument would pile up
        # inside a process that keeps taking work.
        store.close()
    return seconds


def parse_date_ns(text: str) -> int:
    """UTC midnight of a YYYY-MM-DD date, in nanoseconds."""
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1_000_000_000
