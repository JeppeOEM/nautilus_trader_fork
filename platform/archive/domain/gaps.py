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
Which snapshot rows the raw trade archive can rebuild (story 22.13), over the kernel's
`ArchiveGap` spans (`kernel.archive_markers`).
"""

from dataclasses import dataclass

from kernel.archive_markers import in_gap


@dataclass(frozen=True)
class Coverage:
    """
    Which rows the archive can rebuild: from `start` on, outside every archive-gap span.

    Invariant: a row this does not `cover` keeps its live trade columns -- the archive is known not
    to hold the trades they were folded from, so replacing them would zero correct values. The
    two reasons are told apart for the report: `before_start` (the archive did not exist yet) and
    `in_gap` (inside an `ArchiveGap` span after the archive began).
    """

    start: int | None
    gaps: tuple[tuple[int, int], ...] = ()

    def before_start(self, ts_ns: int) -> bool:
        return self.start is None or ts_ns < self.start

    def in_gap(self, ts_ns: int) -> bool:
        """Kept because of a gap marker: the archive had begun, but a marker spans the row."""
        return not self.before_start(ts_ns) and in_gap(ts_ns, list(self.gaps))

    def covers(self, ts_ns: int) -> bool:
        return not self.before_start(ts_ns) and not in_gap(ts_ns, list(self.gaps))
