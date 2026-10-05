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
Capture's archive-gap marker writer (story 22.13; capture's own since Story 25.1).

Capture records the two gaps it causes -- `write_failed` (a trade batch's `write_data` failed
while the same instrument's snapshot rows landed) and `quarantined` (`quarantine_corrupt_parquet`
moved an unreadable trade file aside) -- as one line each in `<catalog>/_archive_gaps/<iid>.jsonl`,
in the frozen `kernel.archive_markers` format, so the nightly rebuild keeps those rows' live values.
Archive owns the rest of the file's life (its `pruned` markers, every read:
`archive.infrastructure.gap_markers`); the two share only the kernel's line format and path, so
neither context imports the other. Ledger sites are the pre-move ones: `archive_gaps.write`,
`archive_gaps.inverted_span`, reported through the caller's `Ledger` (the `CaptureService` is capture's
only ledger caller, Story 26.1).

Appends are whole lines or nothing (DW-211): each one takes an exclusive `flock` on the file,
notes its size, writes the line and fsyncs; a write or fsync that fails is truncated back to that
size, so a full disk can never leave a torn line that would refuse the instrument's rebuild every
night. Archive appends to the same files under the same lock and reads them under a shared one.
The few lines of locked append are archive's too (`archive.infrastructure.gap_markers`), repeated
rather than shared: the kernel does no marker file I/O, and neither context imports the other.

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

from capture.application import sites
from capture.application.ports import Ledger


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
    catalog_path: str, iid: str, from_ns: int, to_ns: int, reason: str, count: int, ledger: Ledger
) -> None:
    """
    Append one gap marker; a failure is ledgered, never raised (the flush must carry on). A failed
    append leaves the file as it was (`_append_line`); the ledger line says when it could not be
    restored.

    An inverted span (a backward wall-clock step between a lost trade's arrival and the flush
    puts `now` before its `ts_init`) is written as the ordered span and ledgered
    (`archive_gaps.inverted_span`): `decode` refuses an inverted line, which would wedge the
    rebuild of that instrument until the file is hand-edited, and the ordered span still covers
    the rows the marker protects.
    """
    if from_ns > to_ns:
        ledger(
            sites.ARCHIVE_GAPS_INVERTED_SPAN,
            f"{iid} {reason}: from_ns {from_ns} > to_ns {to_ns} (clock stepped back?); "
            "recorded as the ordered span",
        )
        from_ns, to_ns = to_ns, from_ns
    line = archive_markers.encode(ArchiveGap(iid, from_ns, to_ns, reason, count))
    path = archive_markers.path_for(catalog_path, iid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _append_line(path, line)
    except _TornAppendError as e:
        what = f"could not record archive gap {line}; a torn line may remain in {path}"
        ledger(sites.ARCHIVE_GAPS_WRITE, what, e)
    except OSError as e:
        ledger(sites.ARCHIVE_GAPS_WRITE, f"could not record archive gap {line}", e)
