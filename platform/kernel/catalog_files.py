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
Files are selected by their name's `ts_init` span (`kernel.clocks.CatalogFileSpan`) and rows by
their exact `ts_event` (MEM-01: callers read one instrument, and the rebuilds one day, at a time).
Only `query_second_ohlc` and `query_top_of_book` widen the span by `READ_SPAN_MARGIN_NS`
(`query_index_prices` by `MAX_TS_INIT_SKEW_NS`);
`files_by_day` and `data_file_ranges` take the span as written, as their pre-kernel originals did --
the rebuild re-reads a whole day, so a row whose `ts_init` lands in the neighbouring file is picked
up there.
"""

import glob
import os
from typing import NamedTuple

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import FIXED_PRECISION_BYTES
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.funcs import class_to_filename


SNAPSHOT_DIRNAME = class_to_filename(DydxSecondSnapshot)  # "custom_dydx_second_snapshot"
_OHLC_COLUMNS = SecondOHLC._fields
INDEX_PRICE_DIRNAME = class_to_filename(IndexPriceUpdate)  # "index_price_update"


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


class TopOfBook(NamedTuple):
    """Level 0 of one second-snapshot row: what a quote needs, without the 20-level book."""

    ts_event: int
    ts_init: int
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float


_BOOK_COLUMNS = ("bid_prices", "bid_sizes", "ask_prices", "ask_sizes")


def query_top_of_book(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[TopOfBook]:
    """
    Level-0 bid/ask price and size of every second-snapshot row in [start_ns, end_ns] (ts_event),
    sorted by `ts_event`; the same file selection as `query_second_ohlc`.

    A row with an empty side is omitted: it has no top of book to quote. Only element 0 of each
    list column leaves Arrow, so a multi-day window never builds the 20-level book in Python
    (MEM-01), which is what the catalog decoder would do.
    """
    rows: list[TopOfBook] = []
    for path in snapshot_files(catalog_path, instrument_id):
        if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, READ_SPAN_MARGIN_NS):
            continue
        rows.extend(_top_rows(path, start_ns, end_ns))
    rows.sort(key=lambda r: r.ts_event)
    return rows


def _top_rows(path: str, start_ns: int, end_ns: int) -> list[TopOfBook]:
    table = pq.read_table(
        path,
        columns=["ts_event", "ts_init", *_BOOK_COLUMNS],
        filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
    )
    # A null or empty list compares to null or False, and `filter` drops both.
    quotable = pc.and_(
        pc.greater(pc.list_value_length(table.column("bid_prices")), 0),
        pc.greater(pc.list_value_length(table.column("ask_prices")), 0),
    )
    table = table.filter(quotable)
    columns: list[pa.ChunkedArray] = [table.column("ts_event"), table.column("ts_init")]
    columns += [pc.list_element(table.column(name), 0) for name in _BOOK_COLUMNS]
    return [TopOfBook(*values) for values in zip(*(c.to_pylist() for c in columns), strict=True)]


class IndexPrice(NamedTuple):
    """One `IndexPriceUpdate` row: the price is a `Price` at its file's own precision label."""

    ts_event: int
    ts_init: int
    price: Price


def query_index_prices(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[IndexPrice]:
    """
    Every `IndexPriceUpdate` row of the instrument in [start_ns, end_ns] (ts_event), sorted by
    `ts_event`. Files are chosen by their `ts_init` span widened by `MAX_TS_INIT_SKEW_NS` (these are
    not snapshot rows, so the whole writer skew bound applies, not `READ_SPAN_MARGIN_NS`).

    `ParquetDataCatalog.query(IndexPriceUpdate, ...)` raises NotImplementedError in the pinned
    nautilus_trader (1.228: no Arrow decoder, and the Rust backend has no such data type), so this
    reads the three columns directly. The Rust writer stores `value` as the fixed-point raw integer
    (little-endian, `FIXED_PRECISION_BYTES` wide in this build) and the precision label in the
    file's `price_precision` metadata; `Price.from_raw` rebuilds the exact value with no float step
    (NAUT-01). A file without that label, whose raw width is not this build's, or holding a raw
    value finer than its label, raises `ValueError` naming it -- never a guessed precision.
    """
    directory = os.path.join(catalog_path, "data", INDEX_PRICE_DIRNAME, instrument_id)
    rows: list[IndexPrice] = []
    for path in glob.glob(os.path.join(directory, "*.parquet")):
        if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, MAX_TS_INIT_SKEW_NS):
            continue
        rows.extend(_index_rows(path, start_ns, end_ns))
    rows.sort(key=lambda r: r.ts_event)
    return rows


def _index_rows(path: str, start_ns: int, end_ns: int) -> list[IndexPrice]:
    table = pq.read_table(
        path,
        columns=["value", "ts_event", "ts_init"],
        filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
    )
    metadata = table.schema.metadata or {}
    if b"price_precision" not in metadata:
        raise ValueError(f"{path}: no price_precision metadata, the raw values cannot be read")
    precision = int(metadata[b"price_precision"])
    if not 0 <= precision <= FIXED_PRECISION:
        raise ValueError(
            f"{path}: price_precision {precision} is outside this build's 0..{FIXED_PRECISION}"
        )
    step = 10 ** (FIXED_PRECISION - precision)
    rows = []
    for raw, ts_event, ts_init in zip(
        table.column("value").to_pylist(),
        table.column("ts_event").to_pylist(),
        table.column("ts_init").to_pylist(),
        strict=True,
    ):
        if raw is None:
            raise ValueError(f"{path}: a null raw price at ts_event {ts_event}")
        if len(raw) != FIXED_PRECISION_BYTES:
            raise ValueError(
                f"{path}: a {len(raw)}-byte raw price, this build reads {FIXED_PRECISION_BYTES}"
            )
        value = int.from_bytes(raw, "little", signed=True)
        if value % step:
            # `Price.from_raw` would accept it and mislabel the value (the mixed-precision history).
            raise ValueError(
                f"{path}: raw price {value} at ts_event {ts_event} has more digits than the "
                f"file's price_precision {precision}"
            )
        price = Price.from_raw(value, precision)
        rows.append(IndexPrice(ts_event, ts_init, price))
    return rows


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
