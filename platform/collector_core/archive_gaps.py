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
marker format (`kernel.archive_markers`); it moves to the `archive` context in Story 25.1.

`rebuild_seconds` replaces a covered row's trade columns with the fold of the archived trades.
Where the archive lost trades that the live second still holds, that would zero correct values.
The ways it can happen, each recorded here:

- `write_failed`: a trade batch's `write_data` failed while the same instrument's snapshot rows
  landed (the collector's flush);
- `quarantined`: `quarantine_corrupt_parquet` moved an unreadable trade file aside;
- `pruned`: `prune_catalog` deleted a verified trade file, but an older unverified day's files
  remain, so `covered_from` still reaches back past it.

A row whose `ts_event` falls in any marker span is `not covered` and keeps its live values. Only
one collector writes a given instrument's file (the same single-writer rule as its catalog
leaves), and the maintenance tools write it under the maintenance lock. A marker that cannot be
written is ledgered (`archive_gaps.write`).
"""

import os
import warnings

from kernel import archive_markers
from kernel import clocks
from kernel.archive_markers import ArchiveGap
from observability import error_ledger


# Story 23.2 moved the marker format and the skew bound to the kernel. The old names are served,
# as the same objects, with a DeprecationWarning until the story below is done.
MOVED_NAMES_REMOVE_AFTER = "24-2-views-read-models-and-reader-side-revalidation-removed"
_MOVED_NAMES: dict[str, str] = {
    "ARRIVAL_MARGIN_NS": "kernel.clocks.MAX_TS_INIT_SKEW_NS",
    "GAPS_DIRNAME": "kernel.archive_markers.GAPS_DIRNAME",
    "in_gap": "kernel.archive_markers.in_gap",
}

_TARGET_MODULES = {"kernel.clocks": clocks, "kernel.archive_markers": archive_markers}


def __getattr__(name: str) -> object:
    if name not in _MOVED_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    target = _MOVED_NAMES[name]
    warnings.warn(
        f"collector_core.archive_gaps.{name} moved to {target} (Story 23.2); "
        f"removed after {MOVED_NAMES_REMOVE_AFTER}",
        DeprecationWarning,
        stacklevel=2,
    )
    module, _, attr = target.rpartition(".")
    return getattr(_TARGET_MODULES[module], attr)  # a KeyError is a table typo: loud


def record_gap(
    catalog_path: str, iid: str, from_ns: int, to_ns: int, reason: str, count: int
) -> None:
    """Append one gap marker; a failure is ledgered, never raised (callers must carry on)."""
    line = archive_markers.encode(ArchiveGap(iid, from_ns, to_ns, reason, count))
    path = archive_markers.path_for(catalog_path, iid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        error_ledger.record("archive_gaps.write", f"could not record archive gap {line}", e)


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
