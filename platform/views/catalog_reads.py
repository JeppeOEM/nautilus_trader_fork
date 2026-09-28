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
The catalog series reads every chart view pages through: whole `DydxSecondSnapshot` rows for a
`ts_event` window (`query_second_snapshots`), and the cursor-paging helpers the scroll-back pages
share (`fetch_page`, `has_older_data`), which walk the catalog's own file ranges
(`kernel.catalog_files.data_file_ranges`) rather than fixed-size probe windows.

Moved verbatim from the catalog-stats module and `data_api/routes/paging.py` (Story 24.2).
"""

from collections.abc import Callable

from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.persistence.catalog import ParquetDataCatalog


def query_second_snapshots(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
) -> list[DydxSecondSnapshot]:
    """
    DydxSecondSnapshot rows for `instrument_id` in [start_ns, end_ns], CustomData-unwrapped.

    Shared by dashboard.py's _historical_lines_json and custom_indicators.py's
    _second_snapshots -- both projected different fields off this same query, so only
    the catalog-query + CustomData-unwrap boilerplate lives here.
    """
    catalog = ParquetDataCatalog(catalog_path)
    # `query` bounds on ts_init, the window is ts_event: a venue-timed row (story 22.12) is
    # sampled up to 1 + hold_back s (a catch-up: more) after its ts_event, so the end is widened
    # and the exact ts_event filter decides.
    # Known limit: the start is not widened, so a row whose venue clock ran ahead of ours
    # (`ts_init < ts_event`, the second direction `kernel.clocks.READ_SPAN_MARGIN_NS` documents)
    # is dropped when its ts_event is within READ_SPAN_MARGIN_NS of `start_ns`. The ceiling is one
    # margin's worth of rows at the window's lower edge; `kernel.catalog_files.query_second_ohlc`
    # widens both sides and keeps them, so the two readers can disagree there. The upgrade path is
    # `start=start_ns - READ_SPAN_MARGIN_NS` (the exact ts_event filter below already makes it
    # safe); held back here because this story moves read margins without changing them.
    results = catalog.query(
        data_cls=DydxSecondSnapshot,
        identifiers=[instrument_id],
        start=start_ns,
        end=end_ns + READ_SPAN_MARGIN_NS,
    )
    # query() wraps custom Data subclasses in CustomData -- unwrap via .data to reach the
    # actual DydxSecondSnapshot (confirmed via direct introspection this session).
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    return [s for s in snapshots if start_ns <= s.ts_event <= end_ns]


def has_older_data(ranges: list[tuple[int, int]], ns: int) -> bool:
    return bool(ranges) and ranges[0][0] < ns


def fetch_page[T](
    fetch: Callable[[int, int], list[T]],
    ranges: list[tuple[int, int]],
    before_ns: int,
    span_ns: int,
) -> list[T]:
    """
    First non-empty `fetch(start_ns, end_ns)` walking back from `before_ns` in `span_ns`
    windows, jumping over gaps straight to the last data before each empty window. `[]` only
    when nothing older exists at all.
    """
    end_ns = before_ns
    while True:
        start_ns = end_ns - span_ns
        result = fetch(start_ns, end_ns)
        if result:
            return result
        older_ends = [end for start, end in ranges if start < start_ns]
        if not older_ends:
            return []
        end_ns = min(max(older_ends), start_ns)  # min(): always progress, even mid-file
