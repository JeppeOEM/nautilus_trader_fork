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
The catalog tool's oracle reads (Story 31.7): the catalog's files through raw pyarrow and the
candle store through stdlib `sqlite3`, never `ParquetDataCatalog`, `kernel.catalog_files`,
`kernel.clocks` or the candle store's own code -- the reference side never imports the code it
checks (DATA-02).

Every file is read in record batches (`iter_batches`), one batch in memory at a time (MEM-01): the
`ts_init` scan, the snapshot `ts_event`s of the day, and each file's rows digested whole (the
rehearsal) or filtered on `ts_init` (the stored parity leg). A listed file that is gone when read
raises `FileNotFoundError`; the application turns a changed listing into a refusal.
"""

import sqlite3
from collections.abc import Iterator
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from verification.domain.catalog_check import NS_PER_DAY
from verification.domain.catalog_check import NS_PER_HOUR
from verification.domain.catalog_check import NS_PER_MS
from verification.domain.catalog_check import READ_MARGIN_NS
from verification.domain.catalog_check import STORE_BAR_SECONDS
from verification.domain.catalog_check import DayFiles
from verification.domain.catalog_check import Digest
from verification.domain.catalog_check import FileScan
from verification.domain.catalog_check import Span
from verification.domain.catalog_check import StoredBar
from verification.domain.catalog_check import StoredLeg
from verification.domain.catalog_check import parse_file_name
from verification.domain.catalog_check import schema_signature


_TS_INIT = "ts_init"
_TS_EVENT = "ts_event"
# One record batch as Python rows at a time: 16k snapshot rows (20 levels a side) are ~40 MB.
_BATCH_ROWS = 16_384


def _batches(path: Path, columns: list[str] | None) -> Iterator[pa.RecordBatch]:
    parquet = pq.ParquetFile(path)
    try:
        yield from parquet.iter_batches(batch_size=_BATCH_ROWS, columns=columns)
    finally:
        parquet.close()


def _stamps(batch: pa.RecordBatch, name: str) -> tuple[np.ndarray, np.ndarray]:
    """Return a stamp column as int64 nanoseconds and its validity mask (a null is masked out)."""
    column = batch.column(name)
    valid = column.is_valid().to_numpy(zero_copy_only=False)
    values = column.fill_null(0).to_numpy(zero_copy_only=False).astype(np.int64)
    return values, valid


def _statistics_bounds(parquet: pq.ParquetFile, name: str) -> tuple[bool, tuple[int, int] | None]:
    """
    Bound a stamp column by its row groups' statistics: `[min, max]`, and whether they were usable
    (False: a row group has none, so the column must be read). None: no rows, or no such column.
    """
    index = parquet.schema_arrow.get_field_index(name)
    if index < 0 or parquet.metadata.num_rows == 0:
        return True, None
    lows, highs = [], []
    for group in range(parquet.num_row_groups):
        stats = parquet.metadata.row_group(group).column(index).statistics
        if stats is None or not stats.has_min_max:
            return False, None
        lows.append(int(stats.min))
        highs.append(int(stats.max))
    return True, (min(lows), max(highs))


def _column_bounds(path: Path, name: str) -> tuple[int, int] | None:
    stamps = pq.read_table(path, columns=[name]).column(name).drop_null()
    if len(stamps) == 0:
        return None
    values = stamps.to_numpy().astype(np.int64)
    return int(values.min()), int(values.max())


def stamps_meet(path: Path, window: tuple[int, int]) -> bool:
    """
    Whether any `ts_init` or `ts_event` of the file lies in the half-open window, judged on the
    row-group statistics (a column without them is read): a file whose name lies about its rows,
    or holding a row that arrived long after its venue time, is found by its contents.

    A file with no readable footer is not the window's: `write_data` writes in place, so a live
    collector's file of a later day is unreadable while it is being written. Its rows still meet
    a check -- its own day's, which lists it by name and judges its `name_span`, and counts a row
    stamped into this window as `beyond_margin`.
    """
    try:
        parquet = pq.ParquetFile(path)
    except (pa.ArrowInvalid, OSError):
        if not path.exists():
            raise FileNotFoundError(path) from None  # vanished: the caller refuses the run
        return False
    try:
        bounds = [_statistics_bounds(parquet, name) for name in (_TS_INIT, _TS_EVENT)]
    finally:
        parquet.close()
    for name, (usable, found) in zip((_TS_INIT, _TS_EVENT), bounds, strict=True):
        if not usable:
            found = _column_bounds(path, name)
        if found is not None and found[1] >= window[0] and found[0] < window[1]:
            return True
    return False


class CatalogScan:
    """Read-only raw access to one catalog root's `data/<type>/<instrument>/*.parquet` files."""

    def __init__(self, catalog: Path) -> None:
        self._data = catalog / "data"

    def type_dirs(self) -> tuple[str, ...]:
        """Every `data/<type>` directory name, sorted."""
        if not self._data.is_dir():
            return ()
        return tuple(sorted(p.name for p in self._data.iterdir() if p.is_dir()))

    def has_leaf(self, data_type: str, instrument_id: str) -> bool:
        return (self._data / data_type / instrument_id).is_dir()

    def day_files(self, data_type: str, instrument_id: str, window: tuple[int, int]) -> DayFiles:
        """
        List the leaf's day files: a name span meeting `[low, high)`, or stamps meeting it by the
        file's own statistics; bad names apart, and the other files' name spans.

        Known limit: every file whose name misses the window has its footer read (its statistics),
        so a listing costs one footer per file of the leaf's whole history, and the run lists each
        leaf about seven times (scope, three fingerprints, three rehearsal stages). A consolidated
        leaf holds one file per day, so a year is ~365 footers a listing. Upgrade path: read the
        footers once per run and reuse them while the leaf's fingerprint is unchanged.
        """
        leaf = self._data / data_type / instrument_id
        files: list[tuple[Path, Span]] = []
        bad: list[str] = []
        others: list[Span] = []
        for path in sorted(leaf.glob("*.parquet")):
            span = parse_file_name(path.name)
            if span is None:
                bad.append(path.name)
            elif span.intersects(*window) or stamps_meet(path, window):
                files.append((path, span))
            else:
                others.append(span)
        return DayFiles(tuple(files), tuple(bad), tuple(others))

    def fingerprint(self, paths: Sequence[Path]) -> frozenset[tuple[str, int, int, int]]:
        """
        Each file's name, inode, size and modification time: a file rewritten in place or
        replaced under its name changes it, so an unchanged fingerprint is an unchanged file.
        """
        found = set()
        for path in paths:
            stat = path.stat()  # a vanished file raises: the caller refuses the run
            found.add(
                (str(path.relative_to(self._data)), stat.st_ino, stat.st_size, stat.st_mtime_ns)
            )
        return frozenset(found)

    def scan(self, path: Path) -> FileScan:
        """
        Stream a file's stamps: rows, the non-null `ts_init` extremes, decreases and hours, and the
        rows with either stamp null.
        """
        rows, nulls, decreases = 0, 0, 0
        low: int | None = None
        high: int | None = None
        previous: int | None = None
        hours: set[int] = set()
        for batch in _batches(path, [_TS_INIT, _TS_EVENT]):
            rows += batch.num_rows
            column = batch.column(_TS_INIT)
            _, init_ok = _stamps(batch, _TS_INIT)
            _, event_ok = _stamps(batch, _TS_EVENT)
            nulls += int(np.count_nonzero(~(init_ok & event_ok)))
            stamps = column.drop_null().to_numpy(zero_copy_only=False).astype(np.int64)
            if not len(stamps):
                continue
            chain = stamps if previous is None else np.concatenate(([previous], stamps))
            decreases += int(np.count_nonzero(np.diff(chain) < 0))
            low = int(stamps.min()) if low is None else min(low, int(stamps.min()))
            high = int(stamps.max()) if high is None else max(high, int(stamps.max()))
            previous = int(stamps[-1])
            hours.update(np.unique(stamps // NS_PER_HOUR).tolist())
        return FileScan(rows, low, high, decreases, nulls, tuple(sorted(hours)))

    def schema(self, path: Path) -> tuple[str, tuple[str, ...]]:
        """Return the file's schema signature and its column names."""
        schema = pq.read_schema(path)
        fields = [(f.name, str(f.type)) for f in schema]
        keys = [k.decode() for k in (schema.metadata or {})]
        return schema_signature(fields, keys), tuple(schema.names)

    def snapshot_ts_events(self, paths: Sequence[Path], day: range) -> list[int]:
        """Every non-null `ts_event` in the day, across the files (the duplicate count's input)."""
        found: list[int] = []
        for path in paths:
            for batch in _batches(path, [_TS_EVENT]):
                stamps = batch.column(_TS_EVENT).drop_null()
                values = stamps.to_numpy(zero_copy_only=False).astype(np.int64)
                found.extend(values[(values >= day.start) & (values < day.stop)].tolist())
        return found

    def digest(self, paths: Sequence[Path]) -> Digest:
        """Every row of the files, every column (the rehearsal's digest)."""
        total = Digest()
        for path in paths:
            for batch in _batches(path, None):
                total += Digest.of(batch.to_pylist())
        return total

    def stored(self, paths: Sequence[Path], day: range) -> StoredLeg:
        """
        Digest the rows with `ts_init` in the day, every column of their file (the stored leg),
        and the rows -- either stamp in the day -- whose `|ts_init - ts_event|` exceeds the margin.
        A row with a null stamp is in neither (`null_ts` counts it); no null reads as a time.
        """
        total, beyond = Digest(), 0
        for path in paths:
            for batch in _batches(path, None):
                (init, init_ok), (event, event_ok) = (
                    _stamps(batch, _TS_INIT),
                    _stamps(batch, _TS_EVENT),
                )
                in_day = init_ok & (init >= day.start) & (init < day.stop)
                touches = (
                    init_ok & event_ok & (in_day | ((event >= day.start) & (event < day.stop)))
                )
                beyond += int(np.count_nonzero(touches & (np.abs(init - event) > READ_MARGIN_NS)))
                total += Digest.of(batch.filter(pa.array(in_day)).to_pylist())
        return StoredLeg(total, beyond)


class CandleStoreFile:
    """
    The candle store (`candles_<venue>.db`, schema in `docs/DATA_DICTIONARY.md` section 2.5 and
    `candles/infrastructure/sqlite_store.py`), opened read-only (`mode=ro`) per read.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> str:
        return str(self._path)

    def bars(self, instrument_id: str, day_start_ns: int) -> tuple[list[StoredBar], int]:
        """Read the instrument's rows with `t` in the day: known widths, and the others' count."""
        start_ms = day_start_ns // NS_PER_MS
        end_ms = start_ms + NS_PER_DAY // NS_PER_MS
        query = (
            "SELECT bar_seconds, t, o, h, l, c, v, seconds_observed FROM candles "
            "WHERE instrument_id = ? AND t >= ? AND t < ? ORDER BY bar_seconds, t"
        )
        db = sqlite3.connect(f"file:{quote(str(self._path))}?mode=ro", uri=True)
        try:
            rows = db.execute(query, (instrument_id, start_ms, end_ms)).fetchall()
        finally:
            db.close()
        known = [StoredBar(*row) for row in rows if row[0] in STORE_BAR_SECONDS]
        return known, len(rows) - len(known)
