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
marker format (`kernel.archive_markers`); archive's side of it (moved from
`collector_core.archive_gaps` in Story 25.1).

`archive.rebuild_seconds` replaces a covered row's trade columns with the fold of the archived
trades. Where the archive lost trades that the live second still holds, that would zero correct
values. The ways it can happen, each recorded as a marker:

- `write_failed`: a trade batch's `write_data` failed while the same instrument's snapshot rows
  landed (capture's flush, written by `collector_core.gap_markers`);
- `quarantined`: `quarantine_corrupt_parquet` moved an unreadable trade file aside (capture too);
- `pruned`: `archive.prune_catalog` deleted a verified trade file, but an older unverified day's
  files remain, so `covered_from` still reaches back past it (written here).

A row whose `ts_event` falls in any marker span keeps its live values. Archive is the only writer
of `pruned` markers and the only reader of the files; it writes under the maintenance lock. A
marker that cannot be written is ledgered (`archive_gaps.write`).
"""

import os
from pathlib import Path

from kernel import archive_markers
from kernel.archive_markers import ArchiveGap
from observability import error_ledger


def record_gap(
    catalog_path: str, iid: str, from_ns: int, to_ns: int, reason: str, count: int
) -> bool:
    """
    Append one gap marker; return True only when it was durably appended (fsynced). A failure is
    ledgered (`archive_gaps.write`), never raised: the caller decides -- the prune keeps a trade
    file whose `pruned` marker could not be written.

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
        with path.open("a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        # A new file (or directory) is durable only once its directory entry is: without this a
        # power loss could keep the prune's unlink and lose the `pruned` marker that allowed it.
        if new_file:
            _fsync_dir(path.parent)
        if new_dir:
            _fsync_dir(path.parent.parent)
    except OSError as e:
        error_ledger.record("archive_gaps.write", f"could not record archive gap {line}", e)
        return False
    return True


def load_gaps(catalog_path: str, iid: str) -> list[tuple[int, int]]:
    """
    Return the instrument's gap spans `(from_ns, to_ns)`, inclusive. A malformed line raises
    `ValueError`: the rebuild then refuses the instrument-day rather than guess.
    """
    path = archive_markers.path_for(catalog_path, iid)
    if not path.exists():
        return []
    spans = []
    for number, text in enumerate(path.read_text().splitlines(), start=1):
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
