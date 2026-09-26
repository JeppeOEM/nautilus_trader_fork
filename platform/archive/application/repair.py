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
Find and repair second snapshots whose trade OHLC is impossible given their own book.

Detection is `kernel.second_snapshot.ohlc_outside_book` -- see there for why it is sound. It
finds the rows written by the pre-fix collector, which counted dYdX's subscribed-reply trade
history as live trades (before the `stale_trade_seconds` filter in `collector_core/config.py`).

Repair, per flagged second: replace the snapshot with a copy whose trade fields are cleared (OHLC
None, volumes/counts 0 -- the real trades in that second cannot be told apart from the replayed
ones, so "no trade recorded" is the honest value, DATA-01). A flagged row of the current UTC day is
skipped and ledgered (`repair.open_day`): capture is that day's writer.

Known limit: the rewrite is Nautilus's own `ParquetDataCatalog.delete_data_range` + `write_data`
(AD-6's official path), so Nautilus -- not `CatalogFiles` -- rewrites the file; the maintenance
flock, the venue's capture lock and the open-day check are what gate it. Upgrade path: a
row-replacing `CatalogFiles.rewrite` of the affected file once a second such tool needs it.
"""

import logging

from candles.application.rebuild import day_chunks
from candles.application.rebuild import rebuild_instrument
from kernel.catalog_files import snapshot_files
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import ohlc_outside_book
from observability import error_ledger

from archive.application.ports import CatalogWriter
from archive.application.ports import OpenDayWriteError
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)


def second_snapshots(
    catalog_path: str, iid: str, start_ns: int, end_ns: int
) -> list[DydxSecondSnapshot]:
    """
    `DydxSecondSnapshot` rows of `iid` with `ts_event` in [start_ns, end_ns], CustomData-unwrapped.

    The same query as `views.catalog_reads.query_second_snapshots` (archive imports no views):
    `query` bounds on `ts_init` and the window is `ts_event`, so the end is widened by the read
    margin and the exact `ts_event` filter decides.
    """
    results = ParquetDataCatalog(catalog_path).query(
        data_cls=DydxSecondSnapshot,
        identifiers=[iid],
        start=start_ns,
        end=end_ns + READ_SPAN_MARGIN_NS,
    )
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    return [s for s in snapshots if start_ns <= s.ts_event <= end_ns]


def find_impossible_snapshots(
    catalog_path: str, iid: str, start_ns: int, end_ns: int
) -> list[DydxSecondSnapshot]:
    return [
        snap
        for a, b in day_chunks(start_ns, end_ns)
        for snap in second_snapshots(catalog_path, iid, a, min(b, end_ns))
        if ohlc_outside_book(snap)
    ]


def _cleared_copy(snap: DydxSecondSnapshot) -> DydxSecondSnapshot:
    values = DydxSecondSnapshot.to_dict(snap)
    values.update(
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        open_price=None,
        high_price=None,
        low_price=None,
        close_price=None,
    )
    return DydxSecondSnapshot.from_dict(values)


def _containing_spans(catalog_path: str, iid: str, ts_init: int) -> list[tuple[int, int]]:
    """Return the name spans of every snapshot file of `iid` that can hold a row with this `ts_init`."""
    spans = []
    for path in snapshot_files(catalog_path, iid):
        try:
            span = CatalogFileSpan.from_path(path)
        except ValueError:
            continue  # not a catalog-written name: the catalog never reads it as data
        if span.start_ns <= ts_init <= span.end_ns:
            spans.append((span.start_ns, span.end_ns))
    return spans


def closed_rows(
    writer: CatalogWriter, catalog_path: str, iid: str, flagged: list[DydxSecondSnapshot]
) -> list[DydxSecondSnapshot]:
    """
    Keep the flagged rows archive may rewrite; ledger and skip each one that touches the current
    UTC day. `delete_data_range` rewrites the whole file holding the row, so every file of the
    leaf whose name span contains the row's `ts_init` must be closed too -- one crossing midnight
    into today is capture's.
    """
    closed = []
    for snap in flagged:
        spans = [(snap.ts_init, snap.ts_init), *_containing_spans(catalog_path, iid, snap.ts_init)]
        try:
            for start, end in spans:
                writer.assert_span_closed(start, end)
        except OpenDayWriteError as e:
            error_ledger.record("repair.open_day", f"{iid} ts={snap.ts_event}: {e}; not repaired")
            continue
        closed.append(snap)
    return closed


def repair_instrument(
    catalog: ParquetDataCatalog,
    catalog_path: str,
    iid: str,
    flagged: list[DydxSecondSnapshot],
    candles_db_path: str | None = None,
) -> None:
    """
    Replace every flagged row with its cleared copy; with `candles_db_path`, rebuild the
    candle-store days they touched from the corrected raw 1 s.
    """
    # Build every replacement first so a bad row fails before anything is deleted.
    replacements = [(snap, _cleared_copy(snap)) for snap in flagged]
    for snap, cleared in replacements:
        catalog.delete_data_range(DydxSecondSnapshot, iid, snap.ts_event, snap.ts_event)
        catalog.write_data([cleared])
    if candles_db_path is not None and flagged:
        first, last = min(s.ts_event for s in flagged), max(s.ts_event for s in flagged)
        rebuild_instrument(candles_db_path, catalog_path, iid, first, last)
