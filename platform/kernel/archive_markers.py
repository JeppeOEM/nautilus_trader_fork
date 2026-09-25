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
The archive-gap marker format (DDD spine AD-D3, AD-D18; story 22.13): pure encode/decode only.

A marker records a span where the raw trade archive is known to miss trades the live fold used,
so the nightly rebuild keeps those rows' live values. One JSON line per gap in
`<catalog>/_archive_gaps/<iid>.jsonl`: `instrument_id`, `from_ns`, `to_ns` (inclusive), `reason`,
`count`, in that key order.

Invariant: the line bytes are the published format shared by capture (writes `write_failed`,
`quarantined`), archive (writes `pruned`, reads all) -- `encode` must keep producing exactly what
the original writer produced, and `decode` refuses a malformed line (`ValueError`) rather than
guess a span. The file I/O and its failure ledgering live with the writers
(`collector_core.archive_gaps`), never here.
"""

import json
from dataclasses import dataclass
from pathlib import Path


GAPS_DIRNAME = "_archive_gaps"


@dataclass(frozen=True)
class ArchiveGap:
    """One marker: trades of `iid` with `ts_event` in `[from_ns, to_ns]` may be missing."""

    iid: str
    from_ns: int
    to_ns: int
    reason: str
    count: int

    @property
    def span(self) -> tuple[int, int]:
        return (self.from_ns, self.to_ns)


def path_for(catalog_path: str | Path, iid: str) -> Path:
    """Return the instrument's marker file under the catalog root."""
    return Path(catalog_path) / GAPS_DIRNAME / f"{iid}.jsonl"


def encode(gap: ArchiveGap) -> str:
    """
    One marker line, without the newline (byte-identical to the story 22.13 writer). An inverted
    span is refused (`ValueError`): `decode` would refuse the line, so it must never be written.
    """
    if gap.from_ns > gap.to_ns:
        raise ValueError(f"inverted archive-gap span {gap!r}")
    return json.dumps(
        {
            "instrument_id": gap.iid,
            "from_ns": gap.from_ns,
            "to_ns": gap.to_ns,
            "reason": gap.reason,
            "count": gap.count,
        }
    )


def decode(line: str) -> ArchiveGap:
    """
    Parse one marker line; `ValueError` naming the text for anything malformed. The span must be
    two JSON integers with `from_ns <= to_ns`: a float would be truncated and an inverted span
    would match no row, so the rebuild would overwrite exactly the rows the marker protects.
    """
    try:
        entry = json.loads(line)
        gap = ArchiveGap(
            iid=str(entry["instrument_id"]),
            from_ns=_exact_int(entry["from_ns"]),
            to_ns=_exact_int(entry["to_ns"]),
            reason=str(entry["reason"]),
            count=_exact_int(entry["count"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f"malformed archive-gap marker {line!r}") from e
    if gap.from_ns > gap.to_ns:
        raise ValueError(f"inverted archive-gap marker span {line!r}")
    return gap


def _exact_int(value: object) -> int:
    if type(value) is not int:  # a bool is an int subclass, a float truncates: both refused
        raise TypeError(f"{value!r} is not a JSON integer")
    return value


def in_gap(ts_ns: int, gaps: list[tuple[int, int]]) -> bool:
    """Return whether `ts_ns` falls inside any inclusive `(from_ns, to_ns)` span."""
    return any(lo <= ts_ns <= hi for lo, hi in gaps)
