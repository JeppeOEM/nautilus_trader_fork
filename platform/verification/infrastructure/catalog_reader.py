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
Story 31.3's fixture cutter, `read_snapshot_rows`). A file or row
group is skipped only by its own `ts_event` statistics lying wholly outside the window --
independent of the file name, which spans `ts_init`; one without statistics is read.
"""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import CoverageEntry
from verification.domain.conservation import TradeWindow
from verification.domain.conservation import parse_coverage_line
from verification.domain.conservation import parse_gap_marker_line


TRADE_DIR = "trade_tick"
SNAPSHOT_DIR = "custom_dydx_second_snapshot"
GAPS_DIR = "_archive_gaps"
COVERAGE_DIR = "coverage"
_TS_EVENT = "ts_event"


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


def _read_window(path: Path, columns: list[str], start_ns: int, end_ns: int) -> pa.Table | None:
    parquet = pq.ParquetFile(path)
    try:
        groups = _row_groups(parquet, path, start_ns, end_ns)
        if not groups:
            return None
        table = parquet.read_row_groups(groups, columns=columns)
    finally:
        parquet.close()
    ts = table[_TS_EVENT].to_numpy()
    return table.filter(pa.array((ts >= start_ns) & (ts < end_ns)))


def read_day(
    catalog: Path, data_dir: str, instrument_id: str, columns: list[str], window: tuple[int, int]
) -> pa.Table:
    """Every row of one instrument's data type with `ts_event` in `window`, those columns only."""
    files = sorted((catalog / "data" / data_dir / instrument_id).glob("*.parquet"))
    tables = [t for p in files if (t := _read_window(p, columns, *window)) is not None]
    if not tables:
        fields = {"trade_id": pa.string(), _TS_EVENT: pa.uint64()}
        return pa.table({name: pa.array([], fields[name]) for name in columns})
    return pa.concat_tables(tables, promote_options="permissive")


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
        table = _read_window(path, columns, *window)
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
