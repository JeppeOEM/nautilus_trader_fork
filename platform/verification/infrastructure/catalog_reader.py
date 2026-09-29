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
The conservation tool's catalog reads (Story 31.2): raw Parquet through pyarrow, never through
`ParquetDataCatalog` or `kernel.catalog_files` -- the reference side never imports the code it
checks, and a catalog reader that deduplicated, filtered or re-bucketed rows would hide exactly
what conservation counts. Also the two durable explanation files beside the catalog: capture's
coverage record (`<catalog>/../coverage/<venue>.jsonl`) and the archive-gap markers
(`<catalog>/_archive_gaps/<iid>.jsonl`).

Layout read (Nautilus's own, written by `ParquetDataCatalog.write_data`):
`<catalog>/data/trade_tick/<iid>/*.parquet` (columns `trade_id`, `ts_event` read) and
`<catalog>/data/custom_dydx_second_snapshot/<iid>/*.parquet` (`ts_event` read; every column for
Story 31.3's fixture cutter, `read_snapshot_rows`). Story 31.4's trades tool reads every stored
trade column with each file's `price_precision`/`size_precision` schema metadata
(`trade_values`) and the snapshot rows' eight trade columns (`snapshot_trade_rows`). A file or row
group is skipped only by its own `ts_event` statistics lying wholly outside the window --
independent of the file name, which spans `ts_init`; one without statistics is read.
"""

from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import CoverageEntry
from verification.domain.conservation import TradeWindow
from verification.domain.conservation import parse_coverage_line
from verification.domain.conservation import parse_gap_marker_line
from verification.domain.conservation import parse_gap_marker_span
from verification.domain.trade_check import ArchivedTrade
from verification.domain.trade_check import StoredRow
from verification.domain.trade_check import StoredTrade
from verification.domain.trade_check import TradeColumns
from verification.domain.trade_check import archived_trade


TRADE_DIR = "trade_tick"
SNAPSHOT_DIR = "custom_dydx_second_snapshot"
GAPS_DIR = "_archive_gaps"
COVERAGE_DIR = "coverage"
_TS_EVENT = "ts_event"
_TS_INIT = "ts_init"
_PRICE_PRECISION = "price_precision"
_SIZE_PRECISION = "size_precision"
# The stored trade columns Story 31.4 compares (every one of `trade_tick`'s), and the eight trade
# columns of a snapshot row with the precisions its integers are counted at.
_TRADE_VALUES = ("price", "size", "aggressor_side", "trade_id", _TS_EVENT, _TS_INIT)
_ROW_TRADE_COLUMNS = (
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "buy_volume",
    "sell_volume",
    "buy_count",
    "sell_count",
)
_ROW_COLUMNS = (_PRICE_PRECISION, _SIZE_PRECISION, *_ROW_TRADE_COLUMNS, _TS_EVENT)
# The type of each column read, for the empty table of a day without files.
_EMPTY_TYPES = MappingProxyType(
    {
        "trade_id": pa.string(),
        _TS_EVENT: pa.uint64(),
        _TS_INIT: pa.uint64(),
        "price": pa.binary(16),
        "size": pa.binary(16),
        "aggressor_side": pa.uint8(),
        _PRICE_PRECISION: pa.uint8(),
        _SIZE_PRECISION: pa.uint8(),
        **{name: pa.int64() for name in _ROW_TRADE_COLUMNS},
    }
)


def _row_groups(parquet: pq.ParquetFile, path: Path, start_ns: int, end_ns: int) -> list[int]:
    """Row groups whose `ts_event` can lie in `[start_ns, end_ns)` (all, without statistics)."""
    column = parquet.schema_arrow.get_field_index(_TS_EVENT)
    if column < 0:
        raise ValueError(f"{path}: no `{_TS_EVENT}` column")
    kept = []
    for index in range(parquet.num_row_groups):
        stats = parquet.metadata.row_group(index).column(column).statistics
        if stats is None or not stats.has_min_max or (stats.max >= start_ns and stats.min < end_ns):
            kept.append(index)
    return kept


def read_window(path: Path, columns: list[str], start_ns: int, end_ns: int) -> pa.Table | None:
    """
    Read `columns` of one file's rows with `ts_event` in `[start_ns, end_ns)`, skipping the row
    groups whose statistics lie wholly outside it; None when every row group is skipped. A row
    without `ts_event` is refused (`ValueError`), never filtered out unseen.
    """
    parquet = pq.ParquetFile(path)
    try:
        groups = _row_groups(parquet, path, start_ns, end_ns)
        if not groups:
            return None
        table = parquet.read_row_groups(groups, columns=columns)
    finally:
        parquet.close()
    if table[_TS_EVENT].null_count:
        raise ValueError(f"{path}: a row without `ts_event`")
    ts = table[_TS_EVENT].to_numpy()
    return table.filter(pa.array((ts >= start_ns) & (ts < end_ns)))


def read_day(
    catalog: Path, data_dir: str, instrument_id: str, columns: list[str], window: tuple[int, int]
) -> pa.Table:
    """Every row of one instrument's data type with `ts_event` in `window`, those columns only."""
    files = sorted((catalog / "data" / data_dir / instrument_id).glob("*.parquet"))
    tables = [t for p in files if (t := read_window(p, columns, *window)) is not None]
    if not tables:
        return _empty(columns)
    return pa.concat_tables(tables, promote_options="permissive")


def _empty(columns: list[str]) -> pa.Table:
    return pa.table({name: pa.array([], _EMPTY_TYPES[name]) for name in columns})


def read_snapshot_rows(
    catalog: Path, instrument_id: str, window: tuple[int, int]
) -> list[dict[str, object]]:
    """
    Every stored second-snapshot row of one instrument with `ts_event` in `window`, all columns, as
    plain Python values (the stored integers untouched), in `ts_event` order (Story 31.3's
    fixture cutter).
    """
    files = sorted((catalog / "data" / SNAPSHOT_DIR / instrument_id).glob("*.parquet"))
    rows: list[dict[str, object]] = []
    for path in files:
        columns = pq.read_schema(path).names
        table = read_window(path, columns, *window)
        if table is not None:
            rows += table.to_pylist()
    return sorted(rows, key=lambda row: int(str(row[_TS_EVENT])))


def first_snapshot_ts(catalog: Path, instrument_id: str) -> int | None:
    """
    Return the earliest stored second-snapshot `ts_event` of an instrument (None without a file): the
    minimum of the first file only (files are named by their `ts_init` span, so the name-sorted
    first file holds the earliest rows; one file's column is read, never the whole history).
    """
    files = sorted((catalog / "data" / SNAPSHOT_DIR / instrument_id).glob("*.parquet"))
    if not files:
        return None
    stamps = pq.read_table(files[0], columns=[_TS_EVENT])[_TS_EVENT].to_numpy()
    return int(stamps.min()) if len(stamps) else None


class ArrowArchivedTrades:
    """
    One instrument-day of archived trade ids in Arrow. Invariant: rows are sorted by `ts_event`,
    so an hour is one contiguous slice found by binary search (`hour`), and only that hour ever
    becomes Python objects.
    """

    def __init__(self, table: pa.Table) -> None:
        ordered = table.sort_by(_TS_EVENT)
        self._ids = ordered["trade_id"]
        self._ts = ordered[_TS_EVENT].to_numpy().astype(np.uint64)

    def hour(self, hour: int) -> dict[str, int]:
        low, high = np.searchsorted(self._ts, [hour * NS_PER_HOUR, (hour + 1) * NS_PER_HOUR])
        ids = self._ids.slice(int(low), int(high - low)).to_pylist()
        return dict(zip(ids, self._ts[low:high].tolist(), strict=True))

    def duplicate_ids(self) -> int:
        table = pa.table({"trade_id": self._ids})
        counts = table.group_by("trade_id").aggregate([("trade_id", "count")])
        return int((counts["trade_id_count"].to_numpy() > 1).sum())


def _file_precisions(path: Path) -> tuple[int, int]:
    """Return a trade file's `price_precision`/`size_precision` from its schema metadata."""
    metadata = pq.read_schema(path).metadata or {}
    try:
        return int(metadata[b"price_precision"]), int(metadata[b"size_precision"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{path}: no integer price/size precision in its schema metadata") from exc


def _trade_values_window(path: Path, window: tuple[int, int]) -> pa.Table | None:
    table = read_window(path, list(_TRADE_VALUES), *window)
    if table is None:
        return None
    price_precision, size_precision = _file_precisions(path)
    rows = table.num_rows
    table = table.append_column(_PRICE_PRECISION, pa.array([price_precision] * rows, pa.uint8()))
    return table.append_column(_SIZE_PRECISION, pa.array([size_precision] * rows, pa.uint8()))


def _file_first_ts(path: Path) -> int | None:
    """Return a file's earliest `ts_event`: its row-group statistics, or the column without them."""
    parquet = pq.ParquetFile(path)
    try:
        column = parquet.schema_arrow.get_field_index(_TS_EVENT)
        if column < 0:
            raise ValueError(f"{path}: no `{_TS_EVENT}` column")
        stats = [
            parquet.metadata.row_group(i).column(column).statistics
            for i in range(parquet.num_row_groups)
        ]
        if all(s is not None and s.has_min_max for s in stats):
            return min((int(s.min) for s in stats), default=None)
        values = parquet.read(columns=[_TS_EVENT])[_TS_EVENT].to_numpy()
    finally:
        parquet.close()
    return int(values.min()) if len(values) else None


def first_trade_ts(catalog: Path, instrument_id: str) -> int | None:
    """
    Return the instrument's earliest archived trade `ts_event` over its whole archive (None without
    one): the rebuild keeps live values in every row before its floor second
    (`docs/DATA_DICTIONARY.md` section 6). Only file footers are read where they carry
    statistics. Known limit: one footer read per trade file of the instrument, all history --
    cheap after the nightly consolidation (one file per day), about 1440 reads per unconsolidated
    day. Upgrade path: stop at the name-sorted files whose `ts_init` span starts within the
    stale-trade bound of the first one, as the rebuild's `covered_from` does.
    """
    files = sorted((catalog / "data" / TRADE_DIR / instrument_id).glob("*.parquet"))
    firsts = [ts for path in files if (ts := _file_first_ts(path)) is not None]
    return min(firsts, default=None)


class ArrowTradeValues:
    """
    One instrument's archived trades of a window, every stored column plus each row's file
    precisions, in Arrow. Invariant: rows are sorted by (`ts_event`, `ts_init`), so an hour is one
    contiguous slice found by binary search, and only that hour ever becomes Python objects.
    """

    def __init__(self, table: pa.Table, first_ts_event: int | None) -> None:
        self._table = table.sort_by([(_TS_EVENT, "ascending"), (_TS_INIT, "ascending")])
        self._ts = self._table[_TS_EVENT].to_numpy().astype(np.uint64)
        self.first_ts_event = first_ts_event

    def hour(self, hour: int) -> list[ArchivedTrade]:
        low, high = np.searchsorted(self._ts, [hour * NS_PER_HOUR, (hour + 1) * NS_PER_HOUR])
        rows = self._table.slice(int(low), int(high - low)).to_pylist()
        return [
            archived_trade(StoredTrade(**row), int(low) + offset) for offset, row in enumerate(rows)
        ]

    def duplicate_ids(self) -> int:
        return ArrowArchivedTrades(self._table.select(["trade_id", _TS_EVENT])).duplicate_ids()


def _stored_row(row: dict[str, Any]) -> StoredRow:
    """One snapshot row's trade columns; a row without its precisions cannot be judged: refused."""
    price_precision, size_precision = row[_PRICE_PRECISION], row[_SIZE_PRECISION]
    if price_precision is None or size_precision is None:
        raise ValueError(f"a snapshot row at ts_event {row[_TS_EVENT]} without its precisions")
    return StoredRow(
        price_precision,
        size_precision,
        TradeColumns(**{name: row[name] for name in _ROW_TRADE_COLUMNS}),
        row[_TS_EVENT],
    )


class ArrowSnapshotRows:
    """
    One instrument's snapshot rows of a window, the eight trade columns and the precisions, in
    Arrow. Invariant: rows are sorted by `ts_event`, and only one hour at a time becomes Python
    objects (`hour`: second -> its rows, a second holding two rows keeps both).
    """

    def __init__(self, table: pa.Table) -> None:
        self._table = table.sort_by(_TS_EVENT)
        self._ts = self._table[_TS_EVENT].to_numpy().astype(np.uint64)

    def hour(self, hour: int) -> dict[int, list[StoredRow]]:
        low, high = np.searchsorted(self._ts, [hour * NS_PER_HOUR, (hour + 1) * NS_PER_HOUR])
        seconds: dict[int, list[StoredRow]] = {}
        for row in self._table.slice(int(low), int(high - low)).to_pylist():
            stored = _stored_row(row)
            seconds.setdefault(stored.ts_event // NS_PER_S, []).append(stored)
        return seconds


class ParquetArchive:
    """The catalog's trades and snapshot seconds, read raw (see the module docstring)."""

    def __init__(self, catalog: Path) -> None:
        self._catalog = catalog

    def trades(self, instrument_id: str, start_ns: int, end_ns: int) -> ArrowArchivedTrades:
        columns = ["trade_id", _TS_EVENT]
        return ArrowArchivedTrades(
            read_day(self._catalog, TRADE_DIR, instrument_id, columns, (start_ns, end_ns))
        )

    def second_rows(self, instrument_id: str, start_ns: int, end_ns: int) -> dict[int, int]:
        table = read_day(
            self._catalog, SNAPSHOT_DIR, instrument_id, [_TS_EVENT], (start_ns, end_ns)
        )
        seconds = table[_TS_EVENT].to_numpy().astype(np.uint64) // NS_PER_S
        values, counts = np.unique(seconds, return_counts=True)
        return dict(zip(values.tolist(), counts.tolist(), strict=True))

    def trade_values(self, instrument_id: str, start_ns: int, end_ns: int) -> ArrowTradeValues:
        """Every archived trade with `ts_event` in the window, all values decoded per hour."""
        files = sorted((self._catalog / "data" / TRADE_DIR / instrument_id).glob("*.parquet"))
        window = (start_ns, end_ns)
        tables = [t for p in files if (t := _trade_values_window(p, window)) is not None]
        columns = [*_TRADE_VALUES, _PRICE_PRECISION, _SIZE_PRECISION]
        table = (
            pa.concat_tables(tables, promote_options="permissive") if tables else _empty(columns)
        )
        return ArrowTradeValues(table, first_trade_ts(self._catalog, instrument_id))

    def snapshot_trade_rows(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> ArrowSnapshotRows:
        """Every snapshot row with `ts_event` in the window: its trade columns and precisions."""
        window = (start_ns, end_ns)
        return ArrowSnapshotRows(
            read_day(self._catalog, SNAPSHOT_DIR, instrument_id, list(_ROW_COLUMNS), window)
        )


def _lines(path: Path) -> Iterator[tuple[str, str]]:
    """`(file:line, text)` of every line; a blank line is yielded too, for the parser to refuse."""
    with path.open(encoding="utf-8") as handle:
        for number, text in enumerate(handle, start=1):
            yield f"{path}:{number}", text.rstrip("\n")


class CoverageFiles:
    """
    Capture's coverage record of one venue and the catalog's archive-gap markers. Invariant: a
    line is parsed or refused (`MalformedLine` naming file:line), never skipped; a file that does
    not exist yields nothing (`present` tells the report the coverage record is absent).
    """

    def __init__(self, catalog: Path, venue: str) -> None:
        self._catalog = catalog
        # Beside the catalog root *as resolved*, exactly as capture's writer places it: a plain
        # `<catalog>/..` would differ for a relative, `.`-bearing or symlinked catalog path.
        self._path = catalog.resolve().parent / COVERAGE_DIR / f"{venue.lower()}.jsonl"

    @property
    def path(self) -> str:
        return str(self._path)

    def present(self) -> bool:
        return self._path.is_file()

    def entries(self) -> Iterator[CoverageEntry]:
        if not self._path.exists():
            return
        for where, text in _lines(self._path):
            yield parse_coverage_line(text, where)

    def gap_markers(self, instrument_id: str) -> Iterator[TradeWindow]:
        path = self._catalog / GAPS_DIR / f"{instrument_id}.jsonl"
        if not path.exists():
            return
        for where, text in _lines(path):
            yield parse_gap_marker_line(text, where)

    def gap_marker_spans(self, instrument_id: str) -> Iterator[TradeWindow]:
        """Yield the markers' own `ts_init` spans, unwidened: where the rebuild keeps live rows."""
        path = self._catalog / GAPS_DIR / f"{instrument_id}.jsonl"
        if not path.exists():
            return
        for where, text in _lines(path):
            yield parse_gap_marker_span(text, where)
