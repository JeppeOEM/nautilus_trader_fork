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
`CatalogFiles`: the archive's one rewriter and one deleter of catalog Parquet files (Story 25.1).

The only module in `archive/` that calls `pq.write_table`, `os.replace` or removes a file or a
directory (`platform/archive/tests/test_one_deleter_one_rewriter.py` asserts it). Every rewrite is
temp-then-rename: `<file>.archive.tmp` is written with the archive's compact settings
(`archive.infrastructure.compact_parquet.compact_write_options`, Story 30.1: zstd 16, delta-packed
timestamps, dictionary leaves, capped row groups with statistics), read back, and renamed over the
file only when its full schema -- Arrow metadata included, e.g. `price_precision`
(`same_schema`) --, its row count and every value in order (`_read_back_mismatch`) equal the
table's; otherwise the temp is deleted and the original stays. The value check is what makes a
new encoding safe to adopt: a lossy option would fail it before any rename. `encoded_size` writes
the same way into memory, for a report that must not touch the disk. The open-day guard is per
operation (`archive.application.ports.RewriteMode`): a whole-file mutation (a `WHOLE_FILE`
rewrite, `write_merged`, `remove_merged_sources`, `delete`) refuses a file whose `ts_init` span
(its name, `kernel.clocks.CatalogFileSpan`) reaches the current UTC day -- capture is its
writer; a `KEEP_OPEN_DAY_ROWS` rewrite (the rebuild) may touch such a file, but only after
proving, against the original, that every row whose `ts_event` lies in the open day comes out
identical in value and order.

A merge in `MergeScope.CLOSED_HOUR` (the intraday merge, Story 25.1b) may write and remove files
of the current UTC day, but only ones lying wholly inside a single hour before the current UTC
hour -- the same guard one period finer, enforced here and not only by the caller's grouping.

Instances come only from `archive.infrastructure.maintenance_lock.maintenance`, which holds the
catalog maintenance flock for their lifetime, so no two archive tools mutate files at once.

`write_json_atomic` is the archive's one writer of a small JSON file outside the catalog (the
`archive` service's `state.json` scheduler cursor): temp, fsync, `Path.replace`, directory fsync.
It lives here so the one-rewriter rule has no second rename site, and takes no maintenance lock:
that file is the scheduler's alone, never a catalog file.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from kernel.clocks import NS_PER_DAY
from kernel.clocks import CatalogFileSpan

from archive.application.ports import MergeScope
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteMode
from archive.application.ports import RewriteVerifyError
from archive.application.ports import StagedRewrite
from archive.domain.intraday import NS_PER_HOUR
from archive.infrastructure.compact_parquet import compact_write_options
from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename


logger = logging.getLogger(__name__)

TMP_SUFFIX = ".archive.tmp"
# What the pre-Story-25.1 rewriters left behind on a crash: rebuild_seconds, consolidate_catalog,
# migrate_open_interest/normalize_snapshot_schema (`<file>.parquet.tmp`).
LEGACY_TMP_SUFFIXES = (".rebuild.tmp", ".consolidate.tmp", ".parquet.tmp")


def same_schema(written: pa.Schema, table: pa.Schema) -> bool:
    """
    Whether a read-back file schema is `table`'s: names, types, nullability, and every schema- and
    field-level metadata entry (e.g. `price_precision`). Not `equals(check_metadata=True)`:
    Parquet names a list's child field `element` where an in-memory list type may say `item`, a
    naming difference that holds no value and that check would count.
    """
    return (
        written.equals(table)
        and (written.metadata or {}) == (table.metadata or {})
        and all(
            (a.metadata or {}) == (b.metadata or {}) for a, b in zip(written, table, strict=True)
        )
    )


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(directory: Path) -> None:
    """Make the directory's entries (a rename, an unlink) durable."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_back_mismatch(path: Path, table: pa.Table) -> str | None:
    """
    Where the file at `path` first differs from `table`'s values, in order (`ChunkedArray.equals`),
    or None when it holds exactly them; read back one column of one row group at a time: the check
    never holds a second copy of the table, only its largest column chunk (MEM-01; measured +140 MB
    peak RSS on a snapshot day for a whole-table read-back).

    Known limit: `equals` counts NaN unequal to itself, so a file holding a NaN would be refused
    every time (loud, original kept). No catalog writer stores one: a price, size or rate is never
    NaN, and a missing value is a null. Upgrade path: a NaN-aware per-column comparison if a type
    ever carries NaN legitimately.
    """
    offset = 0
    with pq.ParquetFile(str(path)) as written:
        for group in range(written.num_row_groups):
            rows = written.metadata.row_group(group).num_rows
            expected = table.slice(offset, rows)
            for name in table.column_names:
                column = written.read_row_group(group, columns=[name]).column(0)
                if not column.equals(expected.column(name)):
                    return f"column {name!r}, row group {group} (rows {offset}..{offset + rows})"
            offset += rows
    return None if offset == table.num_rows else f"{offset} rows read, {table.num_rows} written"


def _unlink_if_present(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


class CatalogFiles:
    """
    `CatalogWriter` over the files of one catalog; `now_ns` is injectable so tests can move
    "today".

    Invariant: see `archive.application.ports.CatalogWriter` -- no whole-file write or delete
    reaches a file of the current UTC day, no rewrite changes a row of it, a rewrite is verified
    before its rename, and `delete` is the only method that removes rows from the catalog.
    """

    def __init__(self, now_ns: Callable[[], int] = time.time_ns) -> None:
        self._now_ns = now_ns

    def _open_day_start_ns(self) -> int:
        return self._now_ns() // NS_PER_DAY * NS_PER_DAY

    def assert_span_closed(self, start_ns: int, end_ns: int) -> None:
        """
        Protects capture's sole ownership of today's files: no whole-file mutation (and no repair
        of a row) reaches the current UTC day.
        """
        if max(start_ns, end_ns) >= self._open_day_start_ns():
            raise OpenDayWriteError(
                f"span [{start_ns}, {end_ns}] reaches the current UTC day: capture writes it"
            )

    def _assert_span_in_closed_hour(self, start_ns: int, end_ns: int) -> None:
        """
        Protects capture's sole ownership of the current UTC hour through an intraday merge: the
        span must lie wholly inside one hour, and that hour must be before the current one.
        """
        hour = start_ns // NS_PER_HOUR
        if hour != end_ns // NS_PER_HOUR:
            raise OpenDayWriteError(
                f"span [{start_ns}, {end_ns}] crosses an hour boundary: not a closed-hour merge"
            )
        if max(start_ns, end_ns) >= self._now_ns() // NS_PER_HOUR * NS_PER_HOUR:
            raise OpenDayWriteError(
                f"span [{start_ns}, {end_ns}] reaches the current UTC hour: capture writes it"
            )

    def _assert_file_closed(self, path: Path, scope: MergeScope = MergeScope.CLOSED_DAY) -> None:
        span = CatalogFileSpan.from_path(path)
        if scope is MergeScope.CLOSED_HOUR:
            self._assert_span_in_closed_hour(span.start_ns, span.end_ns)
        else:
            self.assert_span_closed(span.start_ns, span.end_ns)

    def _open_day_rows(self, path: Path, midnight_ns: int) -> pa.Table | None:
        """Return the file's rows whose `ts_event` is at or after `midnight_ns`, in order; or None."""
        ts = pq.read_table(str(path), columns=["ts_event"]).column("ts_event")
        in_open_day = pc.greater_equal(ts, pa.scalar(midnight_ns, ts.type))
        if not pc.any(in_open_day).as_py():
            return None
        return pq.read_table(str(path)).filter(in_open_day)

    def _assert_open_day_rows_kept(self, original: Path, tmp: Path) -> None:
        """
        Protects the open day's rows through a row-preserving rewrite: every row whose `ts_event`
        is at or after today's UTC midnight must be in the written temp exactly as in the
        original -- same values, same order, none added or dropped. Compared on the read-back
        temp, i.e. on what the rename would publish.
        """
        midnight = self._open_day_start_ns()
        before = self._open_day_rows(original, midnight) if original.exists() else None
        after = self._open_day_rows(tmp, midnight)
        if before is None and after is None:
            return
        if before is None or after is None or not after.equals(before):
            raise OpenDayWriteError(
                f"{original}: the rewrite would change rows of the current UTC day "
                f"(ts_event >= {midnight}); original kept"
            )

    def rewrite(
        self, path: Path, table: pa.Table, mode: RewriteMode = RewriteMode.WHOLE_FILE
    ) -> None:
        self.commit_rewrites([self.stage_rewrite(path, table, mode)])

    def stage_rewrite(self, path: Path, table: pa.Table, mode: RewriteMode) -> StagedRewrite:
        if mode is RewriteMode.WHOLE_FILE:
            self._assert_file_closed(path)
        tmp = path.with_name(path.name + TMP_SUFFIX)
        self._write_verified_tmp(table, tmp, table.num_rows, path)
        if mode is RewriteMode.KEEP_OPEN_DAY_ROWS:
            try:
                self._assert_open_day_rows_kept(path, tmp)
            except BaseException:
                _unlink_if_present(tmp)
                raise
        return StagedRewrite(path, tmp)

    def commit_rewrites(self, staged: list[StagedRewrite]) -> None:
        for i, item in enumerate(staged):
            try:
                os.replace(item.tmp, item.path)
            except OSError as e:
                self.discard_rewrites(staged[i:])
                committed = [s.path for s in staged[:i]]
                raise PartialCommitError(
                    f"rename of {item.path} failed ({e!r}) after {i} of {len(staged)} "
                    f"file(s) were replaced; the rest keep their old content",
                    committed,
                    [s.path for s in staged[i:]],
                ) from e
        for directory in sorted({item.path.parent for item in staged}):
            _fsync_dir(directory)  # the renames are durable before anything relies on them

    def discard_rewrites(self, staged: list[StagedRewrite]) -> None:
        for item in staged:
            _unlink_if_present(item.tmp)

    def write_merged(
        self,
        directory: Path,
        table: pa.Table,
        expected_rows: int,
        *,
        scope: MergeScope = MergeScope.CLOSED_DAY,
    ) -> Path:
        """
        Write a merge as a new file; `ValueError` for an empty table, `FileExistsError` when the
        span's name is taken -- a merge never overwrites a file, least of all one of its sources
        (an interrupted earlier merge is recovered by the caller's covering-file path instead).
        """
        if table.num_rows == 0:
            raise ValueError(f"{directory}: refusing to write an empty merged file")
        ts = table.column("ts_init")
        name = _timestamps_to_filename(int(pc.min(ts).as_py()), int(pc.max(ts).as_py()))
        final = directory / name
        self._assert_file_closed(final, scope)
        if final.exists():
            raise FileExistsError(f"{final} exists: a merge never overwrites a file")
        staged = StagedRewrite(final, directory / (name + TMP_SUFFIX))
        self._write_verified_tmp(table, staged.tmp, expected_rows, final)
        self.commit_rewrites([staged])
        return final

    def _write_verified_tmp(
        self, table: pa.Table, tmp: Path, expected_rows: int, final: Path
    ) -> None:
        """
        Write `table` to `tmp` with the compact settings and verify its read-back -- schema, row
        count, then every value in order; the temp is gone on failure.
        """
        try:
            pq.write_table(table, str(tmp), **compact_write_options(table.schema))
            kept = same_schema(pq.read_schema(str(tmp)), table.schema)
            rows = pq.read_metadata(str(tmp)).num_rows
            # A wrong schema or count already refuses the file: no value read-back for it.
            mismatch = _read_back_mismatch(tmp, table) if kept and rows == expected_rows else None
            _fsync_file(tmp)
        except BaseException:
            _unlink_if_present(tmp)  # a partial or unverifiable temp never outlives the attempt
            raise
        if rows != expected_rows or not kept:
            os.unlink(tmp)
            raise RewriteVerifyError(
                f"{final}: rewritten file failed verification (rows {rows}, expected "
                f"{expected_rows}; schema {'kept' if kept else 'changed'}); original kept"
            )
        if mismatch is not None:
            os.unlink(tmp)
            raise RewriteVerifyError(
                f"{final}: rewritten file failed verification (values changed on read-back: "
                f"{mismatch}); original kept"
            )

    def remove_merged_sources(
        self, paths: list[Path], *, scope: MergeScope = MergeScope.CLOSED_DAY
    ) -> None:
        unique = list(dict.fromkeys(paths))  # a path listed twice is removed once
        for path in unique:  # all checked before the first removal
            self._assert_file_closed(path, scope)
        directories = sorted({path.parent for path in unique})
        for directory in directories:
            if directory.is_dir():
                _fsync_dir(directory)  # the merged file's rename is on disk before its sources go
        for path in unique:
            _unlink_if_present(
                path
            )  # already gone (e.g. a recovered earlier run): its rows are too
        for directory in directories:
            if directory.is_dir():
                _fsync_dir(directory)

    def delete(self, path: Path) -> None:
        self._assert_file_closed(path)
        _fsync_dir(path.parent)  # earlier renames in the leaf are durable before the unlink
        os.unlink(path)
        _fsync_dir(path.parent)

    def remove_stale_tmp(self, leaf: Path) -> list[Path]:
        """
        Delete the temp files an interrupted rewrite left in `leaf`. No span check: a temp is
        never a catalog data file (the catalog reads `*.parquet` only), only archive tools create
        one (capture's writer never does), and the rewrite reruns from the original.
        """
        removed = []
        for suffix in (TMP_SUFFIX, *LEGACY_TMP_SUFFIXES):
            for tmp in sorted(leaf.glob("*" + suffix)):
                logger.warning("%s: removing leftover %s from an interrupted run", leaf, tmp.name)
                os.unlink(tmp)
                removed.append(tmp)
        return removed

    def remove_empty_dir(self, directory: Path) -> bool:
        if not directory.is_dir() or any(directory.iterdir()):
            return False
        try:
            os.rmdir(directory)
        except OSError:  # a file appeared (or the directory went) since the check: leave it
            return False
        return True


def encoded_size(table: pa.Table) -> int:
    """
    Return the byte size `table` would have as a file written by `CatalogFiles` (the same compact
    options), measured in memory: a report-only run projects its savings without writing a file.
    """
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, **compact_write_options(table.schema))
    return int(sink.getvalue().size)


def write_json_atomic(path: Path, obj: object) -> None:
    """
    Replace `path` with `obj` as JSON, durably and atomically: a reader (or a crash) sees the old
    file or the new one, never a torn one. The temp is removed when anything before the rename
    fails.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except BaseException:
        _unlink_if_present(tmp)
        raise
    _fsync_dir(path.parent)
