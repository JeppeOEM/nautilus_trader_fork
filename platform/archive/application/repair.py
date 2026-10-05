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
history as live trades (before the `stale_trade_seconds` filter, now in `capture/application/config.py`).

Repair, per flagged second: replace the snapshot with a copy whose trade fields are cleared (OHLC
None, volumes/counts 0 -- the real trades in that second cannot be told apart from the replayed
ones, so "no trade recorded" is the honest value, DATA-01). The delete bounds on `ts_init`, the
clock the catalog splits files on, so every row sharing a flagged row's `ts_init` (caught-up
seconds) is written back with it, unflagged ones unchanged. A flagged row of the current UTC day is
skipped and ledgered (`repair.open_day`): capture is that day's writer.

Known limit: the rewrite is Nautilus's own `ParquetDataCatalog.delete_data_range` + `write_data`
(AD-6's official path), so Nautilus -- not `CatalogFiles` -- rewrites the file; the maintenance
flock, the venue's capture lock and the open-day check are what gate it. Upgrade path: a
row-replacing `CatalogFiles.rewrite` of the affected file once a second such tool needs it. The
same path is why one `ts_init` group is deleted then written, not replaced atomically (a crash
between the two loses that group, siblings included -- a failed write is ledgered `repair.error`
with the group's `ts_event`s before it is re-raised), and why each group costs one catalog query
and one file split; the rewrite would replace a file once, whatever its group count.
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
    `query` bounds on `ts_init` and the window is `ts_event`, and a row's skew runs either way
    (`kernel.clocks.READ_SPAN_MARGIN_NS`), so both ends are widened by the read margin (the start
    clamped at 0) and the exact `ts_event` filter decides.
    """
    results = ParquetDataCatalog(catalog_path).query(
        data_cls=DydxSecondSnapshot,
        identifiers=[iid],
        start=max(0, start_ns - READ_SPAN_MARGIN_NS),
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
    """Return the row with its trade columns emptied (units: 0 volumes and counts, null OHLC)."""
    values = DydxSecondSnapshot.to_dict(snap)
    values.update(
        buy_volume=0,
        sell_volume=0,
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


def _rows_at(catalog: ParquetDataCatalog, iid: str, ts_init: int) -> list[DydxSecondSnapshot]:
    """Every stored `DydxSecondSnapshot` row of `iid` stamped exactly `ts_init`, unwrapped."""
    results = catalog.query(
        data_cls=DydxSecondSnapshot, identifiers=[iid], start=ts_init, end=ts_init
    )
    rows = [r.data if hasattr(r, "data") else r for r in results]
    return [r for r in rows if r.ts_init == ts_init]


def _replacement_groups(
    catalog: ParquetDataCatalog, iid: str, flagged: list[DydxSecondSnapshot]
) -> dict[int, list[DydxSecondSnapshot]]:
    """
    Map each flagged row's `ts_init` to every row stored at it, flagged ones as cleared copies.

    `delete_data_range` bounds on `ts_init` (inclusive), so deleting one row deletes every row
    sharing its `ts_init` -- caught-up seconds do (`CaptureService`'s `_MAX_CATCH_UP_SECONDS`).
    The whole group is written back, so an unflagged sibling survives unchanged. Every stored row
    of a flagged second collapses into its one cleared copy: a pre-fix repair, deleting by
    `ts_event` on this `ts_init` axis, could miss and leave its cleared copy beside the original.
    Each such leftover collapsed is ledgered (`repair.duplicate`). A group whose flagged row is no
    longer stored is ledgered (`repair.error`) and left untouched.
    """
    cleared_by_init: dict[int, dict[int, DydxSecondSnapshot]] = {}
    for snap in flagged:
        cleared_by_init.setdefault(snap.ts_init, {})[snap.ts_event] = _cleared_copy(snap)
    groups = {}
    for ts_init, cleared in cleared_by_init.items():
        stored = _rows_at(catalog, iid, ts_init)
        missing = set(cleared) - {r.ts_event for r in stored}
        if missing:
            what = f"{iid} ts_init={ts_init}: flagged ts_event {sorted(missing)} not stored"
            error_ledger.record("repair.error", f"{what}; not repaired")
            continue
        kept = [r for r in stored if r.ts_event not in cleared]
        if extra := len(stored) - len(kept) - len(cleared):
            error_ledger.record(
                "repair.duplicate",
                f"{iid} ts_init={ts_init}: {extra} extra stored row(s) of flagged ts_event "
                f"{sorted(cleared)} collapsed into the cleared copy",
            )
        groups[ts_init] = sorted([*kept, *cleared.values()], key=lambda r: r.ts_event)
    return groups


def repair_instrument(
    catalog: ParquetDataCatalog,
    catalog_path: str,
    iid: str,
    flagged: list[DydxSecondSnapshot],
    candles_db_path: str | None = None,
) -> bool:
    """
    Replace every flagged row with its cleared copy, keeping every other row at the same
    `ts_init`; with `candles_db_path`, rebuild the candle-store days they touched from the
    corrected raw 1 s. False when a flagged row was not repaired (no longer stored, ledgered).
    """
    # Build every replacement first so a bad row fails before anything is deleted.
    groups = _replacement_groups(catalog, iid, flagged)
    for ts_init, rows in groups.items():
        catalog.delete_data_range(DydxSecondSnapshot, iid, ts_init, ts_init)
        try:
            catalog.write_data(rows)
        except Exception as e:
            events = [r.ts_event for r in rows]
            what = f"{iid} ts_init={ts_init}: deleted, rewrite failed; lost ts_event {events}"
            error_ledger.record("repair.error", what, exc=e)
            raise
    if candles_db_path is not None and flagged:
        first, last = min(s.ts_event for s in flagged), max(s.ts_event for s in flagged)
        rebuild_instrument(candles_db_path, catalog_path, iid, first, last)
    return len(groups) == len({s.ts_init for s in flagged})
