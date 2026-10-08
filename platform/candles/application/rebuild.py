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
from kernel.catalog_files import liquidation_feed_since_ns
from kernel.catalog_files import query_liquidations
from kernel.catalog_files import second_ohlc_arrays
from kernel.liquidation import has_liquidation_feed
from kernel.venues import has_venue
from observability import error_ledger

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
    ranges = data_file_ranges(catalog_path, iid, on_foreign=error_ledger.record)
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

    Every day [start_ns, end_ns] touches is rebuilt whole, so its files are listed over the whole
    day, never over the range alone: `files_by_day` lists a day's files only where they overlap
    the window, and a rebuild replaces the whole day's bars with what it is given. A range not on
    midnights (`repair_instrument`'s flagged rows) would otherwise rebuild its days from their
    files inside it only. A file crossing the window's first or last midnight is also listed under
    the day outside it, with that one file only, so that day is skipped: rebuilding it would
    replace its bars with the few seconds the crossing file holds (audit D-145: a nightly
    `--day D` emptied D-1).

    An instrument with a liquidation feed (`has_liquidation_feed`) folds each day's archived
    liquidations too (`query_liquidations`, the whole day, each venue event once), bounded by the
    feed start: the id's first archived liquidation (`liquidation_feed_since_ns`, read once per
    instrument), lowered by the store to its persisted start and the day's rows. A bucket that
    does not start at or after it -- a day archived before the feed existed, or the bucket
    straddling the start, at every width -- stays null, never 0, and with no start known every
    bucket does (audit D-160). One without the feed passes None and its `liq_*` columns stay null
    (Story 33.3).
    """
    store = CandleStore(db_path)
    seconds = 0
    first_day, last_day = start_ns // DAY_NS, end_ns // DAY_NS
    window = (first_day * DAY_NS, (last_day + 1) * DAY_NS - 1)
    feed = has_liquidation_feed(iid)
    since_ns = liquidation_feed_since_ns(catalog_path, iid) if feed else None
    try:
        days = files_by_day(catalog_path, iid, *window, on_foreign=error_ledger.record)
        for day, paths in sorted(days.items()):
            if not first_day <= day <= last_day:
                continue
            cols = second_ohlc_arrays(paths)
            liquidations = None
            if feed:
                day_ns = day * DAY_NS
                liquidations = query_liquidations(catalog_path, iid, day_ns, day_ns + DAY_NS - 1)
            seconds += store.rebuild_day(
                iid, cols, day * DAY_MS, allow_open_day, liquidations, since_ns
            )
    finally:
        # A pool worker outlives the job that raised (`pool.map` surfaces the error only when the
        # result is consumed), so an unclosed read-write handle per failed instrument would pile up
        # inside a process that keeps taking work.
        store.close()
    return seconds


def parse_date_ns(text: str) -> int:
    """UTC midnight of a YYYY-MM-DD date, in nanoseconds."""
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1_000_000_000
