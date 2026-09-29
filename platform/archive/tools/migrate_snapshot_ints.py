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
Rewrite the catalog's closed-day float-layout `DydxSecondSnapshot` files in the exact integer
layout (Story 30.2; `kernel/second_snapshot.py`'s module docstring is the layout).

Usage:
    python -m archive.tools.migrate_snapshot_ints --catalog /app/catalog           # report only
    python -m archive.tools.migrate_snapshot_ints --catalog /app/catalog --apply [--venue BYBIT]

Scope: every `data/custom_dydx_second_snapshot/<instrument>` leaf (narrowed by `--venue`) and in
it every file without a `price_precision` column whose span does not reach the current UTC day;
an integer file is counted and skipped (a rerun is a no-op), a file reaching today is counted and
skipped (capture still writes it; a later run takes it -- DATA-05).

Per file (one in memory at a time, MEM-01):

1. Each row's precisions come from the catalog's own instrument definitions: of every stored
   definition of the instrument, the one with the greatest `ts_init <= ` the row's `ts_event`. A
   row older than every definition refuses the file (`migrate_snapshot_ints.error`): the precision
   is never guessed, and never read off a value's own digits.
2. Every float is snapped to its nearest unit `n = rint(v * 10^p)` and accepted only when it lies
   within float noise of `unit_float(n, p)`: `SNAP_NOISE_ULPS` (1024) ULP of the value, and never
   more than `SNAP_RESIDUAL_UNITS` (0.001 unit, the bar `archive.domain.reconciliation.
   float_units` already sets). Capture stored `Price.as_double()`/`BookLevel.size()` (within 2 ULP
   of the exact value) and, before Story 22.13, volumes summed as floats (at most one ULP of drift
   per trade), so a genuine value passes, e.g. `85891.90000000001` -> 858919 at p=1; a value
   carrying real digits finer than the precision (`100.00001` at p=1 is 1e-5 away, about 7e8 ULP)
   does not. A value further off, non-finite, or so large that a double cannot resolve 0.001 unit
   of it (two ULP wider than that bar: about 2.2e12 units) refuses the whole file, ledgered once
   as `migrate_snapshot_ints.off_grid` naming the file and the value -- never rounded. "Snapped"
   counts the values whose stored float was not already `unit_float(n, p)`, the float noise the
   migration removes.
3. Book prices are gap-encoded by the kernel (`encode_book_prices`, which refuses a non-positive
   gap); a pre-OHLC file (no `open_price`..`close_price` columns) gets null OHLC -- what those
   seconds hold, the job `normalize_snapshot_schema` did before this tool subsumed it.
4. Before anything is replaced, the new table is decoded back row by row through the kernel's
   decoder (`DydxSecondSnapshot.from_dict`): every book, OHLC and volume value must lie within the
   snap residual of the original float and every other column (instrument id, counts, both
   timestamps) must be identical; then `CatalogFiles.rewrite` writes it with the compact settings
   (`compact_write_options`) as a verified temp-then-rename under the maintenance flock.

Report-only without `--apply`: no lock, nothing written; the same conversion and checks run in
memory and the "after" bytes are projected (`catalog_files.encoded_size`). Both modes end with one
line per venue -- files, rows, snapped values, bytes before -> after -- and a total. Every per-file
failure is ledgered and the run goes on; exit codes follow `archive.tools.recompress`: 0 done,
1 the catalog is missing or another maintenance run holds the lock, 2 any file refused or failed.

Known limit: a value of more than about 2.2e12 units (e.g. a volume above 2.2e6 at size precision
6) cannot be verified from its float and refuses its file, even when it is genuine; digits finer
than float noise (1024 ULP) cannot be told apart from noise and are snapped, as any reader of the
float always did. Upgrade path: re-derive such a file's trade columns from the raw trade archive
(`archive.rebuild_seconds`) and its book from nothing -- the float is all that exists of it.
"""

import argparse
import logging
import time
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from itertools import pairwise
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from kernel.catalog_files import SNAPSHOT_DIRNAME
from kernel.clocks import NS_PER_DAY
from kernel.clocks import CatalogFileSpan
from kernel.second_snapshot import OHLC_UNIT_COLUMNS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import Side
from kernel.second_snapshot import SnapshotEncodingError
from kernel.second_snapshot import encode_book_prices
from kernel.second_snapshot import unit_floats
from kernel.venues import venue_of
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.consolidate_day import leaf_dirs
from archive.application.ports import CatalogWriter
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteVerifyError
from archive.infrastructure.catalog_files import encoded_size
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

TOOL = "migrate_snapshot_ints"
# How far a stored float may sit from its exact unit: capture's `as_double()` is within 2 ULP, a
# pre-22.13 float-summed volume within one ULP per trade folded into it.
SNAP_NOISE_ULPS = 1024
# Never wider than this, whatever the ULP count says (`archive.domain.reconciliation.
# _FLOAT_RESIDUAL`'s bar): noise, never a real digit.
SNAP_RESIDUAL_UNITS = 0.001
_MB = 1024 * 1024
# Rows converted and decoded back per step: the Python objects of one step (gap lists, decoded
# snapshots) stay bounded while a full day (86,400 rows x 40 levels) is converted; only the two
# Arrow tables are whole-file (MEM-01: one file in memory at a time).
_BATCH_ROWS = 8_192
_BOOK_PRICES = ("bid_prices", "ask_prices")
_SIDES: tuple[tuple[str, Side], ...] = (("bid_prices", "bid"), ("ask_prices", "ask"))
_BOOK_SIZES = ("bid_sizes", "ask_sizes")
_VOLUMES = ("buy_volume", "sell_volume")
_UNCHANGED = ("instrument_id", "buy_count", "sell_count", "ts_event", "ts_init")
# Column -> the decoded snapshot's attribute holding it as units (lists extend, scalars append).
_DECODED_LISTS = (
    ("bid_prices", "bid_price_units"),
    ("ask_prices", "ask_price_units"),
    ("bid_sizes", "bid_size_units"),
    ("ask_sizes", "ask_size_units"),
)
_DECODED_SCALARS = (
    *((name, f"{name}_units") for name in OHLC_UNIT_COLUMNS),
    ("buy_volume", "buy_volume_units"),
    ("sell_volume", "sell_volume_units"),
    ("buy_count", "buy_count"),
    ("sell_count", "sell_count"),
    ("ts_event", "ts_event"),
    ("ts_init", "ts_init"),
)
_FILE_ERRORS = (RewriteVerifyError, PartialCommitError, OSError, pa.ArrowException, ValueError)


class OffGridError(ValueError):
    """A stored float that is not within float noise of a unit (`snap`): the file is refused."""


class NoDefinitionError(ValueError):
    """A row older than every stored instrument definition: its precision is unknown."""


class DefinitionsUnreadableError(ValueError):
    """The catalog's instrument definitions of one instrument could not be read."""


@dataclass
class VenueTotals:
    """The files of one venue a run migrated (or would), with their rows and bytes."""

    files: int = 0
    rows: int = 0
    snapped: int = 0
    bytes_before: int = 0
    bytes_after: int = 0

    def add(self, other: "VenueTotals") -> None:
        for name in ("files", "rows", "snapped", "bytes_before", "bytes_after"):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def line(self) -> str:
        saved = 100 * (1 - self.bytes_after / self.bytes_before) if self.bytes_before else 0.0
        return (
            f"{self.files} file(s), {self.rows} row(s), {self.snapped} snapped value(s), "
            f"{self.bytes_before / _MB:.2f} -> {self.bytes_after / _MB:.2f} MB "
            f"({self.bytes_before} -> {self.bytes_after} bytes, {saved:.1f} % saved)"
        )


@dataclass
class MigrateStats:
    """What one run did, for its report and exit code."""

    by_venue: dict[str, VenueTotals] = field(default_factory=dict)
    already_integer: int = 0
    open_day: int = 0
    vanished: int = 0
    off_grid: int = 0
    failed: int = 0

    def total(self) -> VenueTotals:
        total = VenueTotals()
        for totals in self.by_venue.values():
            total.add(totals)
        return total


# -- precisions --------------------------------------------------------------------------------


@dataclass(frozen=True)
class DefinitionHistory:
    """An instrument's stored definitions, oldest first: when each became valid, its precisions."""

    ts_init: np.ndarray
    price_precision: np.ndarray
    size_precision: np.ndarray

    def at(self, ts_event: np.ndarray, iid: str) -> tuple[np.ndarray, np.ndarray]:
        """Per row, the precisions of the latest definition with `ts_init <= ts_event`."""
        index = np.searchsorted(self.ts_init, ts_event, side="right") - 1
        if len(index) and index.min() < 0:
            first = int(ts_event[index < 0][0])
            raise NoDefinitionError(
                f"{iid}: a row at ts_event {first} predates every stored instrument definition "
                f"(the first is valid from ts_init {self.ts_init[0] if len(self.ts_init) else None})"
            )
        return self.price_precision[index], self.size_precision[index]


def definition_history(catalog_path: str, iid: str) -> DefinitionHistory:
    """Every stored definition of `iid` (`ParquetDataCatalog.instruments`), sorted by `ts_init`."""
    found = ParquetDataCatalog(catalog_path).instruments(instrument_ids=[iid])
    ordered = sorted(found, key=lambda i: i.ts_init)
    return DefinitionHistory(
        np.array([i.ts_init for i in ordered], dtype=np.uint64),
        np.array([i.price_precision for i in ordered], dtype=np.intp),
        np.array([i.size_precision for i in ordered], dtype=np.intp),
    )


# -- snapping ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapped:
    """Float values as units, and how many of them were float-noisy."""

    units: np.ndarray
    noisy: int


def off_grid(values: np.ndarray, units: np.ndarray, precisions: np.ndarray) -> np.ndarray:
    """
    Per value, whether it is NOT within float noise of `unit_float(units, precision)`: further than
    `SNAP_NOISE_ULPS` ULP, further than `SNAP_RESIDUAL_UNITS` unit, or too large for a double to
    resolve that bar at all. Measured in value space, where `values - exact` is exact (Sterbenz).
    """
    with np.errstate(invalid="ignore", over="ignore"):
        exact = unit_floats(units, precisions)
        ulp = np.spacing(np.abs(exact))
        bar = SNAP_RESIDUAL_UNITS / np.power(10.0, precisions)
        unresolvable = 2 * ulp > bar
        return unresolvable | (np.abs(values - exact) > np.minimum(SNAP_NOISE_ULPS * ulp, bar))


def snap(values: np.ndarray, precisions: np.ndarray, what: str) -> Snapped:
    """
    Snap floats to int64 units at per-value precisions; `OffGridError` names the first value that
    is non-finite or not within float noise of its nearest unit (`off_grid`).
    """
    values = np.asarray(values, dtype=np.float64)
    with np.errstate(invalid="ignore", over="ignore"):
        rounded = np.rint(values * np.power(10.0, precisions))
        # 2^62 keeps the int64 cast defined; `off_grid` refuses far smaller values already.
        bad = ~np.isfinite(rounded) | (np.abs(rounded) >= 2.0**62)
    units = np.where(bad, 0, rounded).astype(np.int64)
    bad |= off_grid(values, units, precisions)
    if bad.any():
        i = int(np.argmax(bad))
        raise OffGridError(
            f"{what} value {values[i]!r} is not a whole number of 1e-{int(precisions[i])} units "
            f"(within float noise, at most {SNAP_RESIDUAL_UNITS} unit) at element {i}"
        )
    noisy = int(np.count_nonzero(values != unit_floats(units, precisions)))
    return Snapped(units, noisy)


# -- one file ------------------------------------------------------------------------------------


@dataclass
class Converted:
    """One file's integer table and how many of its floats were snapped."""

    table: pa.Table
    snapped: int


def _list_column(table: pa.Table, name: str) -> pa.ListArray:
    column = table.column(name).combine_chunks()
    if column.null_count:
        raise ValueError(f"{name}: {column.null_count} null book list(s); refusing to guess")
    return column


def _offsets(column: pa.ListArray) -> np.ndarray:
    """Row boundaries into `column.flatten()` (a sliced array's offsets do not start at 0)."""
    offsets = column.offsets.to_numpy()
    return offsets - offsets[0]


def _flat_precisions(column: pa.ListArray, row_precisions: np.ndarray) -> np.ndarray:
    parents = pc.list_parent_indices(column).to_numpy()
    return row_precisions[parents]


def _book_sizes(table: pa.Table, name: str, size_p: np.ndarray, path: Path) -> tuple[pa.Array, int]:
    column = _list_column(table, name)
    flat = column.flatten().to_numpy(zero_copy_only=False)
    snapped = snap(flat, _flat_precisions(column, size_p), f"{path} {name}")
    offsets = pa.array(_offsets(column), pa.int32())
    units = pa.ListArray.from_arrays(offsets, pa.array(snapped.units, pa.int64()))
    return units, snapped.noisy


def _book_prices(
    table: pa.Table, name: str, price_p: np.ndarray, path: Path
) -> tuple[pa.Array, int]:
    """Snap one side's level prices, then gap-encode each row through the kernel."""
    column = _list_column(table, name)
    flat = column.flatten().to_numpy(zero_copy_only=False)
    snapped = snap(flat, _flat_precisions(column, price_p), f"{path} {name}")
    offsets = _offsets(column)
    side = dict(_SIDES)[name]
    encoded = [
        encode_book_prices(snapped.units[start:end].tolist(), side)
        for start, end in pairwise(offsets)
    ]
    return pa.array(encoded, pa.list_(pa.int64())), snapped.noisy


def _scalar_units(
    table: pa.Table, name: str, precisions: np.ndarray, path: Path, nullable: bool
) -> tuple[pa.Array, int]:
    if name not in table.column_names:  # a pre-OHLC file: nothing was recorded for those seconds
        return pa.nulls(table.num_rows, pa.int64()), 0
    column = table.column(name).combine_chunks()
    if column.null_count and not nullable:
        raise ValueError(f"{name}: {column.null_count} null value(s) in a non-null column")
    present = column.is_valid().to_numpy(zero_copy_only=False)
    values = column.fill_null(0.0).to_numpy(zero_copy_only=False)
    snapped = snap(values[present], precisions[present], f"{path} {name}")
    units = np.zeros(table.num_rows, dtype=np.int64)
    units[present] = snapped.units
    return pa.array(units, pa.int64(), mask=~present), snapped.noisy


def convert(table: pa.Table, price_p: np.ndarray, size_p: np.ndarray, path: Path) -> Converted:
    """Return the float-layout `table` in the integer layout (`DydxSecondSnapshot.schema()`)."""
    target = DydxSecondSnapshot.schema()
    unexpected = set(table.column_names) - set(target.names)
    if unexpected:
        raise ValueError(f"{path}: columns outside the snapshot layout: {sorted(unexpected)}")
    required = {*_BOOK_PRICES, *_BOOK_SIZES, *_VOLUMES, *_UNCHANGED}
    if missing := required - set(table.column_names):  # only OHLC may be absent (pre-OHLC)
        raise ValueError(f"{path}: float-layout file without {sorted(missing)}; refusing to guess")
    columns: dict[str, pa.Array | pa.ChunkedArray] = {
        "price_precision": pa.array(price_p, pa.uint8()),
        "size_precision": pa.array(size_p, pa.uint8()),
    }
    snapped = 0
    for name in _BOOK_PRICES:
        columns[name], noisy = _book_prices(table, name, price_p, path)
        snapped += noisy
    for name in _BOOK_SIZES:
        columns[name], noisy = _book_sizes(table, name, size_p, path)
        snapped += noisy
    for name in OHLC_UNIT_COLUMNS:
        columns[name], noisy = _scalar_units(table, name, price_p, path, nullable=True)
        snapped += noisy
    for name in _VOLUMES:
        columns[name], noisy = _scalar_units(table, name, size_p, path, nullable=False)
        snapped += noisy
    for name in _UNCHANGED:
        columns[name] = table.column(name)
    arrays = [columns[f.name] for f in target]
    return Converted(pa.Table.from_arrays(arrays, schema=target).cast(target), snapped)


# -- the decode-back check -----------------------------------------------------------------------


def _decoded_batches(table: pa.Table) -> Iterator[list[DydxSecondSnapshot]]:
    for batch in table.to_batches(max_chunksize=_BATCH_ROWS):
        yield [DydxSecondSnapshot.from_dict(row) for row in batch.to_pylist()]


def _decoded_columns(table: pa.Table) -> dict[str, list]:
    """Every column as the kernel's decoder returns it (book prices absolute, units)."""
    out: dict[str, list] = {name: [] for name, _ in (*_DECODED_LISTS, *_DECODED_SCALARS)}
    ids: list[str] = []
    for snapshots in _decoded_batches(table):
        for s in snapshots:
            for name, attr in _DECODED_LISTS:
                out[name].extend(getattr(s, attr))
            for name, attr in _DECODED_SCALARS:
                out[name].append(getattr(s, attr))
            ids.append(s.instrument_id.value)
    out["instrument_id"] = ids
    return out


def _check_close(
    name: str, original: np.ndarray, decoded: list, precisions: np.ndarray, path: Path
) -> None:
    units = np.array([0 if u is None else u for u in decoded], dtype=np.int64)
    if len(units) != len(original) or off_grid(original, units, precisions).any():
        raise RewriteVerifyError(f"{path}: {name} decodes away from the original floats")


def _check_scalar(
    name: str, original: pa.Table, decoded: list, row_p: np.ndarray, path: Path
) -> None:
    """One OHLC or volume column: the same nulls, every value within the snap bar."""
    if name not in original.column_names:  # a pre-OHLC file: every decoded value must be null
        if any(v is not None for v in decoded):
            raise RewriteVerifyError(f"{path}: {name} was absent but decodes non-null")
        return
    column = original.column(name).combine_chunks()
    present = column.is_valid().to_numpy(zero_copy_only=False)
    if present.tolist() != [v is not None for v in decoded]:
        raise RewriteVerifyError(f"{path}: {name} nulls moved")
    values = column.fill_null(0.0).to_numpy(zero_copy_only=False)
    kept = [v for v in decoded if v is not None]
    _check_close(name, values[present], kept, row_p[present], path)


def verify(
    original: pa.Table, new: pa.Table, price_p: np.ndarray, size_p: np.ndarray, path: Path
) -> None:
    """Decode `new` through the kernel and hold every value against `original` (step 4)."""
    decoded = _decoded_columns(new)
    for name in (*_BOOK_PRICES, *_BOOK_SIZES):
        column = _list_column(original, name)
        row_p = price_p if name in _BOOK_PRICES else size_p
        flat = column.flatten().to_numpy(zero_copy_only=False)
        _check_close(name, flat, decoded[name], _flat_precisions(column, row_p), path)
    for name in OHLC_UNIT_COLUMNS:
        _check_scalar(name, original, decoded[name], price_p, path)
    for name in _VOLUMES:
        _check_scalar(name, original, decoded[name], size_p, path)
    for name in _UNCHANGED:
        if original.column(name).to_pylist() != decoded[name]:
            raise RewriteVerifyError(f"{path}: {name} changed")


# -- the run -------------------------------------------------------------------------------------


@dataclass
class _Run:
    writer: CatalogWriter | None
    catalog_path: str
    today_start_ns: int
    stats: MigrateStats
    histories: dict[str, DefinitionHistory] = field(default_factory=dict)

    def history(self, iid: str) -> DefinitionHistory:
        """
        `iid`'s definitions, read once. `ParquetDataCatalog.instruments()` re-raises whatever its
        query raises (a DataFusion `Exception`, an `AssertionError` other than "no rows"), so any
        failure becomes this file's refusal (ledgered, the run goes on) -- never the run's end.
        """
        if iid not in self.histories:
            try:
                self.histories[iid] = definition_history(self.catalog_path, iid)
            except Exception as e:
                raise DefinitionsUnreadableError(f"{iid}: definitions unreadable: {e!r}") from e
        return self.histories[iid]


def _in_scope(path: Path, run: _Run) -> bool:
    """Whether `path` is a closed float-layout file; integer and open-day files are counted."""
    span = CatalogFileSpan.from_path(path)
    if max(span.start_ns, span.end_ns) >= run.today_start_ns:
        run.stats.open_day += 1
        return False
    if "price_precision" in pq.read_schema(path).names:
        run.stats.already_integer += 1
        return False
    return True


def converted_file(
    original: pa.Table, price_p: np.ndarray, size_p: np.ndarray, path: Path
) -> Converted:
    """Convert and verify `original` `_BATCH_ROWS` rows at a time (steps 2-4), one table out."""
    parts, snapped = [], 0
    for start in range(0, max(original.num_rows, 1), _BATCH_ROWS):
        end = min(start + _BATCH_ROWS, original.num_rows)
        part = original.slice(start, end - start)
        converted = convert(part, price_p[start:end], size_p[start:end], path)
        verify(part, converted.table, price_p[start:end], size_p[start:end], path)
        parts.append(converted.table)
        snapped += converted.snapped
    return Converted(pa.concat_tables(parts), snapped)


def migrate_file(path: Path, iid: str, run: _Run, totals: VenueTotals) -> None:
    """Convert, verify and (with a writer) replace one file; only a success is counted."""
    before = path.stat().st_size
    original = pq.read_table(path)
    ts_event = original.column("ts_event").to_numpy().astype(np.uint64)
    price_p, size_p = run.history(iid).at(ts_event, iid)
    converted = converted_file(original, price_p, size_p, path)
    if run.writer is None:
        after = encoded_size(converted.table)
    else:
        run.writer.rewrite(path, converted.table)
        after = path.stat().st_size
    totals.files += 1
    totals.rows += original.num_rows
    totals.snapped += converted.snapped
    totals.bytes_before += before
    totals.bytes_after += after


def _refused(site: str, path: Path, error: Exception, run: _Run) -> None:
    error_ledger.record(f"{TOOL}.{site}", f"{path}: {error}; left as it was", exc=error)
    if site == "off_grid":
        run.stats.off_grid += 1
    else:
        run.stats.failed += 1


def _migrate_one(path: Path, iid: str, run: _Run, totals: VenueTotals) -> None:
    """Migrate (or report) one file, every failure of its own confined to it."""
    try:
        if _in_scope(path, run):
            migrate_file(path, iid, run, totals)
    except OpenDayWriteError:  # the writer's own guard: the day opened during the run
        run.stats.open_day += 1
    except OffGridError as e:
        _refused("off_grid", path, e, run)
    except FileNotFoundError as e:
        if run.writer is not None or path.exists():
            _refused("error", path, e, run)
        else:  # merged away by a consolidation since the listing (a report holds no lock)
            run.stats.vanished += 1
    except (*_FILE_ERRORS, SnapshotEncodingError) as e:
        _refused("error", path, e, run)


def _migrate_leaf(leaf: Path, run: _Run) -> None:
    try:
        if run.writer is not None:
            run.writer.remove_stale_tmp(leaf)
        paths = sorted(leaf.glob("*.parquet"))
    except OSError as e:
        error_ledger.record(f"{TOOL}.error", f"{leaf}: leaf skipped: {e!r}", exc=e)
        run.stats.failed += 1
        return
    iid = leaf.name
    totals = run.stats.by_venue.setdefault(venue_of(iid), VenueTotals())
    for path in paths:
        _migrate_one(path, iid, run, totals)


def run(
    writer: CatalogWriter | None, catalog_path: str, venue: str | None, now_ns: int
) -> MigrateStats:
    """Migrate (with `writer`) or report (None) every closed float-layout snapshot file."""
    state = _Run(writer, catalog_path, now_ns // NS_PER_DAY * NS_PER_DAY, MigrateStats())
    for leaf in leaf_dirs(catalog_path, [SNAPSHOT_DIRNAME], venue):
        _migrate_leaf(leaf, state)
    return state.stats


def _report(stats: MigrateStats, apply: bool) -> None:
    for venue, totals in sorted(stats.by_venue.items()):
        logger.info("%s: %s", venue, totals.line())
    verb = "migrated" if apply else "would be migrated (report only, nothing changed)"
    logger.info(
        "%s: %s %s; %d already integer, %d skipped (current UTC day), %d vanished, "
        "%d refused off grid, %d failed",
        TOOL,
        stats.total().line(),
        verb,
        stats.already_integer,
        stats.open_day,
        stats.vanished,
        stats.off_grid,
        stats.failed,
    )


def _run(args: argparse.Namespace, now_ns: Callable[[], int]) -> MigrateStats | None:
    """Apply under the maintenance flock (None when it is held); a report takes no lock."""
    if not args.apply:
        return run(None, args.catalog, args.venue, now_ns())
    with maintenance(args.catalog) as writer:
        if writer is None:
            logger.error("%s: another run holds %s; not starting", TOOL, MAINTENANCE_LOCK_NAME)
            return None
        return run(writer, args.catalog, args.venue, now_ns())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; 0 done, 1 the catalog is missing or the lock is held, 2 a file refused."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report only)")
    parser.add_argument("--venue", help="only instruments of this venue, e.g. BYBIT")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if catalog_missing(TOOL, args.catalog):
        return 1
    stats = _run(args, time.time_ns)
    if stats is None:
        return 1
    _report(stats, args.apply)
    if stats.failed or stats.off_grid:
        logger.error(
            "%s: %d file(s) refused off grid, %d failed -- see the %s.* ledger entries (DATA-07)",
            TOOL,
            stats.off_grid,
            stats.failed,
            TOOL,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
