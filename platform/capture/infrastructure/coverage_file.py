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
The coverage record's file (Story 31.2): `<catalog>/../coverage/<venue lower>.jsonl`, one JSON
object per line in the format `capture.domain.coverage` encodes, appended by the one collector of
that venue and read by `python -m verification.conservation`.

The append is `gap_markers.record_gap`'s pattern (open for append, write, flush, `fsync`) with three
differences, so the file only ever holds whole lines, each once:

- a failure raises instead of being ledgered here, so the `CaptureService` ledgers it
  (`collector.coverage_write`) and keeps the lines for its next flush;
- a failed append first truncates the file back to its size before the write, so the retry
  neither duplicates a line nor leaves a torn one;
- a torn tail a killed process left (the last line without its newline) is cut back to the last
  newline before the first append of a process (`repair_torn_tail`), and the caller reports how
  many bytes it cut: a strict reader refuses a torn line, so it must not stay in the file forever.
"""

import contextlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO


COVERAGE_DIRNAME = "coverage"
_TAIL_CHUNK_BYTES = 64 * 1024


def coverage_path(catalog_path: str, venue: str) -> Path:
    """Return the venue's coverage file next to (not inside) the catalog root."""
    return Path(catalog_path).parent / COVERAGE_DIRNAME / f"{venue.lower()}.jsonl"


def repair_torn_tail(path: Path) -> int:
    """Cut a last line without its newline off `path`; return the bytes removed (0: none)."""
    if not path.exists():
        return 0
    with path.open("r+b") as f:
        size = f.seek(0, os.SEEK_END)
        if size == 0:
            return 0
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return 0
        keep = _after_last_newline(f, size)
        f.truncate(keep)
        f.flush()
        os.fsync(f.fileno())
        return size - keep


def _after_last_newline(f: BinaryIO, size: int) -> int:
    """
    Offset just past the last newline before `size` (0: none), read back from the end one chunk
    at a time: the record is never rotated, so it is never read whole (MEM-01).
    """
    end = size
    while end > 0:
        start = max(0, end - _TAIL_CHUNK_BYTES)
        f.seek(start)
        index = f.read(end - start).rfind(b"\n")
        if index >= 0:
            return start + index + 1
        end = start
    return 0


def append_lines(path: Path, lines: Sequence[str]) -> None:
    """
    Append `lines` in order and `fsync`; raises (`OSError`, ...) with the file truncated back to
    its size before this call. Each line is one JSON object (`json.dumps` escapes every newline),
    so a line never spans two.
    """
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = memoryview("".join(line + "\n" for line in lines).encode())
    # Unbuffered: after a failure no buffered bytes may reach the file behind the truncate.
    with path.open("ab", buffering=0) as f:
        start = f.seek(0, os.SEEK_END)
        try:
            while data:
                data = data[f.write(data) or 0 :]
            os.fsync(f.fileno())
        except BaseException:
            _truncate_quietly(f, start)
            raise


def _truncate_quietly(f: BinaryIO, size: int) -> None:
    """Undo a partial append; if even that fails, the original error is the one to report."""
    with contextlib.suppress(OSError):
        f.truncate(size)
        os.fsync(f.fileno())
