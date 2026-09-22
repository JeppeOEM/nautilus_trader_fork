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
Read-only catalog file helpers (DDD spine AD-D3): the read twin of `venue_http` -- file-span and
leaf listing plus column-projected Parquet reads of the second-snapshot rows over the catalog root.

Invariant: reading only. Nothing here writes, renames or deletes a catalog file, or constructs a
`ParquetDataCatalog` (the catalog object's decoder turns every row's 20-level book into Python
objects; these helpers read the Parquet files directly with `pyarrow`). The directory names come
from Nautilus's own `class_to_filename`, so they can never drift from what `write_data` writes.
Files are selected by their name's `ts_init` span (`kernel.clocks.CatalogFileSpan`), widened by
`READ_SPAN_MARGIN_NS`, and rows by their exact `ts_event` (MEM-01: callers read one instrument,
and the rebuilds one day, at a time).
"""

import glob
import os

import numpy as np
import pyarrow.parquet as pq

from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from nautilus_trader.persistence.funcs import class_to_filename


SNAPSHOT_DIRNAME = class_to_filename(DydxSecondSnapshot)  # "custom_dydx_second_snapshot"
_OHLC_COLUMNS = SecondOHLC._fields


def snapshot_files(catalog_path: str, instrument_id: str) -> list[str]:
    """Every second-snapshot Parquet file of the instrument (a directory listing, unordered)."""
    return glob.glob(
        os.path.join(catalog_path, "data", SNAPSHOT_DIRNAME, instrument_id, "*.parquet")
    )


def data_file_ranges(catalog_path: str, instrument_id: str) -> list[tuple[int, int]]:
    """
    Return ascending (start_ns, end_ns) of every second-snapshot Parquet file for the instrument.

    Read from the catalog's filenames -- a directory listing, no Parquet I/O. Lets paging routes know
    where data actually exists instead of guessing with fixed-size probe windows (a data gap wider
    than the window otherwise reads as "no more history").
    """
    spans = (
        CatalogFileSpan.from_path(path) for path in snapshot_files(catalog_path, instrument_id)
    )
    return sorted((span.start_ns, span.end_ns) for span in spans)


def files_by_day(catalog_path: str, iid: str, start_ns: int, end_ns: int) -> dict[int, list[str]]:
    """
    UTC day index -> that day's snapshot files, from one directory listing. A file whose span
    crosses midnight is listed under both days; the rebuild filters rows by timestamp.
    """
    days: dict[int, list[str]] = {}
    for path in snapshot_files(catalog_path, iid):
        span = CatalogFileSpan.from_path(path)
        if not span.overlaps(start_ns, end_ns):
            continue
        for day in range(span.start_ns // NS_PER_DAY, span.end_ns // NS_PER_DAY + 1):
            days.setdefault(day, []).append(path)
    return days


def query_second_ohlc(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[SecondOHLC]:
    """
    Per-second OHLC + volume rows in [start_ns, end_ns] (ts_event), read straight from the
    catalog's Parquet files with only the seven columns candles need.

    `catalog_stats.query_second_snapshots` deserialises every row's 20-level book into Python
    objects through the catalog decoder -- ~95% of a candle request's time (Story 21.5 profile)
    for fields candles never look at. Same files, same rows, same values; just a column projection.
    """
    rows: list[SecondOHLC] = []
    for path in snapshot_files(catalog_path, instrument_id):
        if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, READ_SPAN_MARGIN_NS):
            continue
        rows.extend(_ohlc_rows(path, start_ns, end_ns))
    rows.sort(key=lambda r: r.ts_event)
    return rows


def _ohlc_rows(path: str, start_ns: int, end_ns: int) -> list[SecondOHLC]:
    # Files from before the OHLC fields existed lack those columns: they read as None (the
    # candle path skips a second with no close), never as a crash.
    names = pq.read_schema(path).names
    present = [c for c in _OHLC_COLUMNS if c in names]
    table = pq.read_table(
        path,
        columns=present,
        filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
    )
    cols = {c: table.column(c).to_pylist() for c in present}
    return [
        SecondOHLC(
            *(
                cols[c][i] if c in cols else (0.0 if c.endswith("volume") else None)
                for c in _OHLC_COLUMNS
            )
        )
        for i in range(table.num_rows)
    ]


def second_ohlc_arrays(paths: list[str]) -> dict[str, np.ndarray]:
    """
    Read OHLC + volume columns of the given snapshot files as arrays.

    Keys `ts_ms`, `o`, `h`, `l`, `c`, `v` (NaN = no trade that second); each file opened once. The
    rebuild's read path: no per-row Python objects, no per-call directory scan.
    """
    tables = []
    for path in paths:
        pf = pq.ParquetFile(path)
        present = [c for c in _OHLC_COLUMNS if c in pf.schema_arrow.names]
        tables.append(pf.read(columns=present))
    out = {k: np.empty(0, dtype=np.float64) for k in ("o", "h", "l", "c", "v")}
    out["ts_ms"] = np.empty(0, dtype=np.int64)
    if not tables:
        return out
    n = sum(t.num_rows for t in tables)
    ts = np.concatenate([t.column("ts_event").to_numpy() for t in tables]).astype(np.int64)
    out["ts_ms"] = ts // NS_PER_MS

    def col(name: str, default: float) -> np.ndarray:
        parts = [
            t.column(name).to_numpy(zero_copy_only=False).astype(np.float64)
            if name in t.column_names
            else np.full(t.num_rows, default)
            for t in tables
        ]
        return np.concatenate(parts) if parts else np.empty(0)

    out["o"], out["h"], out["l"], out["c"] = (
        col(k, np.nan) for k in ("open_price", "high_price", "low_price", "close_price")
    )
    out["v"] = col("buy_volume", 0.0) + col("sell_volume", 0.0)
    assert len(out["ts_ms"]) == n
    return out
