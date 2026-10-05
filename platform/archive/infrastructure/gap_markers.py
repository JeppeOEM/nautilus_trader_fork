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
Reading and writing the archive-gap markers (story 22.13) -- the file I/O over the kernel's
marker format (`kernel.archive_markers`); archive's side of it (moved here from capture in
Story 25.1).

`archive.rebuild_seconds` replaces a covered row's trade columns with the fold of the archived
trades. Where the archive lost trades that the live second still holds, that would zero correct
values. The ways it can happen, each recorded as a marker:

- `write_failed`: a trade batch's `write_data` failed while the same instrument's snapshot rows
  landed (capture's flush, written by `capture.infrastructure.gap_markers`);
- `quarantined`: `quarantine_corrupt_parquet` moved an unreadable trade file aside (capture too);
- `pruned`: `archive.prune_catalog` deleted a verified trade file, but an older unverified day's
  files remain, so `covered_from` still reaches back past it (written here).

A row whose `ts_event` falls in any marker span keeps its live values. Archive is the only writer
of `pruned` markers and the only reader of the files; it writes under the maintenance lock. A
marker that cannot be written is ledgered (`archive_gaps.write`).

Appends are whole lines or nothing (DW-211): each one takes an exclusive `flock` on the file,
notes its size, writes the line and fsyncs; a write or fsync that fails is truncated back to that
size, so a full disk can never leave a torn line that would refuse the instrument's rebuild every
night. Capture appends to the same files (`capture.infrastructure.gap_markers`, the same lock),
and `load_gaps` reads under a shared lock, so it never sees an append half-written.

Known limit: a torn tail left by a crash mid-append (a kill or power loss: no exception to catch)
is not healed, and still refuses that instrument's rebuild every night until the line is
hand-edited. Upgrade path: heal it under the same exclusive lock the way
`capture.infrastructure.coverage_file.repair_torn_tail` does -- cutting only a tail that was never
acknowledged durable (no newline), never a whole line.
"""

import errno
import fcntl
import os
from pathlib import Path

from kernel import archive_markers
from kernel.archive_markers import ArchiveGap
from observability import error_ledger


class _TornAppendError(OSError):
    """An append failed and so did truncating it back: a partial line may remain in the file."""


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:  # no progress: looping would spin forever while holding the lock
            raise OSError(errno.EIO, f"write made no progress with {len(view)} byte(s) left")
        view = view[written:]


def _truncate_back(fd: int, size: int, error: BaseException) -> None:
    """
    Cut a failed append off again. When even that fails, an `OSError` append raises
    `_TornAppendError` (ledgered by the caller as a possible torn line); any other exception (an
    interrupt) returns so the caller re-raises it unchanged -- turning it into an `OSError` would
    let `record_gap` swallow it, and the torn line it leaves still refuses the next read loudly.
    """
    try:
        os.ftruncate(fd, size)
        os.fsync(fd)
    except OSError as e:
        if isinstance(error, OSError):
            what = f"append failed ({error!r}), truncating back failed ({e!r})"
            raise _TornAppendError(what) from e


def _append_line(path: Path, line: str) -> None:
    """
    Durably append `line` (plus a newline) to `path`, or leave the file as it was; raises
    `OSError` on failure (`_TornAppendError` when the file could not be restored).

    The exclusive lock is what makes the truncate-back safe: capture and archive append to the
    same file, and without serialising them one writer's truncate could cut a line the other had
    already appended and fsynced -- a `pruned` marker acknowledged durable, whose trade file the
    prune then deletes.
    """
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        try:
            _write_all(fd, (line + "\n").encode())
            os.fsync(fd)
        except BaseException as e:  # whatever stopped the append, the partial line must go
            _truncate_back(fd, size, e)
            raise
    finally:
        os.close(fd)  # releases the lock


def record_gap(
    catalog_path: str, iid: str, from_ns: int, to_ns: int, reason: str, count: int
) -> bool:
    """
    Append one gap marker; return True only when it was durably appended (fsynced). A failure is
    ledgered (`archive_gaps.write`), never raised: the caller decides -- the prune keeps a trade
    file whose `pruned` marker could not be written. A failed append leaves the file as it was
    (`_append_line`); the ledger line says when it could not be restored.

    An inverted span (a backward wall-clock step between a lost trade's arrival and the flush
    puts `now` before its `ts_init`) is written as the ordered span and ledgered
    (`archive_gaps.inverted_span`): `decode` refuses an inverted line, which would wedge the
    rebuild of that instrument until the file is hand-edited, and the ordered span still covers
    the rows the marker protects.
    """
    if from_ns > to_ns:
        error_ledger.record(
            "archive_gaps.inverted_span",
            f"{iid} {reason}: from_ns {from_ns} > to_ns {to_ns} (clock stepped back?); "
            "recorded as the ordered span",
        )
        from_ns, to_ns = to_ns, from_ns
    line = archive_markers.encode(ArchiveGap(iid, from_ns, to_ns, reason, count))
    path = archive_markers.path_for(catalog_path, iid)
    try:
        new_dir = not path.parent.is_dir()
        new_file = new_dir or not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        _append_line(path, line)
        # A new file (or directory) is durable only once its directory entry is: without this a
        # power loss could keep the prune's unlink and lose the `pruned` marker that allowed it.
        if new_file:
            _fsync_dir(path.parent)
        if new_dir:
            _fsync_dir(path.parent.parent)
    except _TornAppendError as e:
        what = f"could not record archive gap {line}; a torn line may remain in {path}"
        error_ledger.record("archive_gaps.write", what, e)
        return False
    except OSError as e:
        error_ledger.record("archive_gaps.write", f"could not record archive gap {line}", e)
        return False
    return True


def load_gaps(catalog_path: str, iid: str) -> list[tuple[int, int]]:
    """
    Return the instrument's gap spans `(from_ns, to_ns)`, inclusive. A malformed line raises
    `ValueError`: the rebuild then refuses the instrument-day rather than guess. Read under a
    shared lock, so an append in progress (capture's or archive's) is never seen half-written.
    """
    path = archive_markers.path_for(catalog_path, iid)
    if not path.exists():
        return []
    with path.open("r") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        text_lines = f.read().splitlines()
    spans = []
    for number, text in enumerate(text_lines, start=1):
        if not text.strip():
            continue
        try:
            spans.append(archive_markers.decode(text).span)
        except ValueError as e:
            raise ValueError(f"{path}:{number}: malformed archive-gap marker {text!r}") from e
    return spans


class GapMarkerFiles:
    """
    `GapMarkers` over one catalog's `_archive_gaps/` directory.

    Invariant: see `archive.application.ports.GapMarkers` -- durable appends in the frozen line
    format, a malformed line refuses the read.
    """

    def __init__(self, catalog_path: str | Path) -> None:
        self._catalog_path = str(catalog_path)

    def record(self, gap: ArchiveGap) -> bool:
        return record_gap(
            self._catalog_path, gap.iid, gap.from_ns, gap.to_ns, gap.reason, gap.count
        )

    def load(self, iid: str) -> list[tuple[int, int]]:
        return load_gaps(self._catalog_path, iid)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
