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
The archive context's ports: how its services touch the catalog files, the gap markers, the
venue's klines and the collection plan. Implemented in `archive.infrastructure` and wired only
by the composition roots (`archive/<tool>.py`, `archive/tools/*.py`).
"""

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pyarrow as pa
from kernel.archive_markers import ArchiveGap

from archive.domain.reconciliation import Kline
from archive.domain.retention import PlanRetention
from nautilus_trader.model.instruments import Instrument


class OpenDayWriteError(Exception):
    """
    A mutation refused because it would touch the current UTC day: a whole-file write or delete of
    a file whose `ts_init` span reaches it, or a row-preserving rewrite that would change, drop,
    add or reorder a row whose `ts_event` lies in it. Nothing was changed.
    """


class RewriteVerifyError(Exception):
    """A rewritten file failed its read-back check; the original is untouched, the temp removed."""


class PartialCommitError(Exception):
    """
    A staged set of rewrites stopped part-way through its renames: `committed` hold the new
    content, `kept` still hold the old one (their temps removed). Each file is whole either way.
    """

    def __init__(self, detail: str, committed: list[Path], kept: list[Path]) -> None:
        super().__init__(detail)
        self.committed = committed
        self.kept = kept


class RewriteMode(enum.Enum):
    """
    How far an in-place rewrite may reach into the current UTC day (capture's).

    `WHOLE_FILE` (the migration tools): the file is replaced as a whole, so its `ts_init` span must
    not reach today at all. `KEEP_OPEN_DAY_ROWS` (the rebuild): any file may be rewritten, but every
    row whose `ts_event` is at or after today's UTC midnight must come out identical in value and
    order -- checked against the original before the rename. Safe because capture writes each file
    once and never reopens it, so rewriting a file that also holds today's rows races with nothing.
    """

    WHOLE_FILE = "whole_file"
    KEEP_OPEN_DAY_ROWS = "keep_open_day_rows"


class MergeScope(enum.Enum):
    """
    Which files a merge may write and remove (`CatalogWriter.write_merged`/`remove_merged_sources`).

    `CLOSED_DAY` (the nightly consolidate, the migrations): no file whose `ts_init` span reaches the
    current UTC day. `CLOSED_HOUR` (the intraday merge, Story 25.1b): files of the current UTC day
    too, but only one lying wholly inside a single hour before the current UTC hour -- capture's
    current hour is its own, exactly as its current day is under `CLOSED_DAY`.
    """

    CLOSED_DAY = "closed_day"
    CLOSED_HOUR = "closed_hour"


@dataclass(frozen=True)
class StagedRewrite:
    """A verified temp file waiting to replace `path` (`CatalogWriter.commit_rewrites`)."""

    path: Path
    tmp: Path


class CatalogWriter(Protocol):
    """
    Every archive mutation of a catalog Parquet file: rewrite in place, write a merge, delete.

    Invariants: (1) one leaf, one writer -- capture writes the current UTC day, so a whole-file
    mutation (`rewrite` in `WHOLE_FILE` mode, `write_merged`, `remove_merged_sources`, `delete`)
    refuses (`OpenDayWriteError`) a file whose span reaches it -- or, for a merge in `CLOSED_HOUR`
    scope, a file reaching the current UTC hour or crossing an hour boundary (`assert_span_closed` lets a caller
    check first), and a `KEEP_OPEN_DAY_ROWS` rewrite refuses to change any row of that day -- a
    check inside the rewrite itself, which no caller can skip; (2) an in-place rewrite is
    temp-then-rename with the table's Arrow schema metadata and row count verified before the
    rename (`RewriteVerifyError` otherwise), so a reader sees the old file or the new one, never a
    torn one; a multi-file change is staged and verified in full before its first rename
    (`stage_rewrite` + `commit_rewrites`); (3) only retention deletes rows (`delete`, called by
    `archive.application.prune` alone) -- `remove_merged_sources` removes files whose rows another
    file already holds. The one rewrite that drops a row is `archive.application.repair`'s: an
    extra stored copy of a flagged second, identical in every column to the cleared copy it keeps
    (no information lost; ledgered `repair.duplicate`). The commands that could violate them are
    exactly these methods.
    """

    def assert_span_closed(self, start_ns: int, end_ns: int) -> None:
        """
        Raise `OpenDayWriteError` when `[start_ns, end_ns]` reaches the current UTC day. Protects
        capture's sole ownership of today's files (whole-file writes and deletes, repair's rows).
        """
        ...

    def rewrite(
        self, path: Path, table: pa.Table, mode: RewriteMode = RewriteMode.WHOLE_FILE
    ) -> None:
        """
        Replace `path` with `table` (compact settings), verified; `path` may not exist yet
        (`WHOLE_FILE`).
        """
        ...

    def stage_rewrite(self, path: Path, table: pa.Table, mode: RewriteMode) -> StagedRewrite:
        """Write and verify `table` as `path`'s temp (every `mode` check included); rename nothing."""
        ...

    def commit_rewrites(self, staged: list[StagedRewrite]) -> None:
        """
        Rename every staged temp over its file, in order; `PartialCommitError` when a rename fails
        part-way (the uncommitted temps are removed).
        """
        ...

    def discard_rewrites(self, staged: list[StagedRewrite]) -> None:
        """Remove staged temps without renaming any: every original stays as it was."""
        ...

    def write_merged(
        self,
        directory: Path,
        table: pa.Table,
        expected_rows: int,
        *,
        scope: MergeScope = MergeScope.CLOSED_DAY,
    ) -> Path:
        """
        Write `table` as one new file named by its `ts_init` span; return its final path. `scope`
        is the open-period guard the new file's span must pass (`MergeScope`).
        """
        ...

    def remove_merged_sources(
        self, paths: list[Path], *, scope: MergeScope = MergeScope.CLOSED_DAY
    ) -> None:
        """
        Delete source files whose rows a verified merged (or migrated) file now holds; a path
        given twice is removed once, one already gone is not an error. Every path must pass
        `scope`'s guard before the first removal.
        """
        ...

    def delete(self, path: Path) -> None:
        """Delete a file retention released (the one row-removing command)."""
        ...

    def remove_stale_tmp(self, leaf: Path) -> list[Path]:
        """Delete the temp files an interrupted rewrite left in `leaf`; return what was removed."""
        ...

    def remove_empty_dir(self, directory: Path) -> bool:
        """Remove `directory` when it is empty; return whether it was removed."""
        ...


class GapMarkers(Protocol):
    """
    The `_archive_gaps/<iid>.jsonl` markers (the frozen `kernel.archive_markers` line format).

    Invariant: a row inside a marker's span keeps its live trade values through every rebuild, so
    a marker is appended durably (fsync) before the event it records -- a `pruned` trade file is
    marked before it is deleted -- and a malformed line refuses the read (`ValueError`) rather
    than guess a span. `record` never raises: a failed append is ledgered and returns False, and
    the event the marker protects must then not happen (the prune keeps that trade file).
    """

    def record(self, gap: ArchiveGap) -> bool:
        """Append one marker durably; True only when it is on disk."""
        ...

    def load(self, iid: str) -> list[tuple[int, int]]: ...


class VenueKlines(Protocol):
    """
    One venue's 1 m klines of a UTC day, as exact integer units.

    Invariant: values reach `archive.domain.reconciliation.compare` without passing through a
    float (the pyo3 kline paths parse through f64, audit D-52), and a minute the venue returned
    twice is refused (`KlineError`), never deduplicated into a pass.
    """

    def fetch(self, inst: Instrument, day_ms: int) -> list[Kline]: ...


class RetentionPlanSource(Protocol):
    """
    The collection plan's retention attributes, read from its file at the moment of the prune.

    Invariant: retention follows the plan capture applies (collection control holds no pruner of
    its own, AD-D17), so the plan is read fresh for each run, never cached across runs.
    """

    def retention(self) -> PlanRetention: ...
