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
The consolidation rehearsal's subject side (Story 31.7): the archive's real intraday and nightly
consolidation (`archive.application.consolidate_day.run_closed_hours` and `run`, the writer from
`archive.infrastructure.maintenance_lock.maintenance`) run on a scratch copy of the day's files,
never on the catalog itself. Subject side (`verification.subject`): the archive's merge is the
code under test; the oracle digests the copy between the stages.

The copy keeps the `data/<type>/<instrument>/` layout under a `TemporaryDirectory` of the scratch
directory. Files are hard-linked (the archive never rewrites a file in place: a merge writes a new
file and unlinks its sources, so a link's shared inode is never changed), falling back to a copy
where a link is refused (another filesystem, permissions). The writer is injectable for the planted
lossy-writer test; it takes the scratch root and the stage's clock and yields a `CatalogWriter`
(None: the maintenance lock is held, which the rehearsal treats as a failed leaf).

Known limit: intraday merges are rehearsed only while unmerged hours remain -- run on a day the
live `archive` service already merged hour by hour (the small types) or the nightly already merged
(every type), a stage finds nothing to do and reads `not_exercised`. Story 31.11 runs the tool
before the nightly consolidates the day. Upgrade path: a planted split of an already merged file
(the archive refuses nothing then, so the rehearsal would test the merge on synthetic input).
"""

import contextlib
import shutil
import tempfile
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from archive.application.consolidate_day import RunStats
from archive.application.consolidate_day import run
from archive.application.consolidate_day import run_closed_hours
from archive.application.ports import CatalogWriter
from archive.infrastructure.maintenance_lock import maintenance

from verification.domain.catalog_check import ConsolidationRun


WriterFactory = Callable[[Path, Callable[[], int]], AbstractContextManager[CatalogWriter | None]]


def maintenance_writer(
    root: Path, now_ns: Callable[[], int]
) -> AbstractContextManager[CatalogWriter | None]:
    """Open the archive's own writer under its maintenance lock, clocked at `now_ns`."""
    return maintenance(root, now_ns=now_ns)


def _link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.hardlink_to(source)
    except FileNotFoundError:
        raise
    except OSError:  # another filesystem, or a link the kernel refuses: copy the bytes
        shutil.copy2(source, target)


class Scratch:
    """
    One rehearsal's scratch catalog. Invariant: only `archive`'s own runs mutate it, and nothing
    under the source catalog is written (a link shares an inode the archive never rewrites).
    """

    def __init__(self, root: Path, writer: WriterFactory) -> None:
        self.root = root
        self._writer = writer

    def _stage(self, now_ns: int, job: Callable[[CatalogWriter], RunStats]) -> ConsolidationRun:
        with self._writer(self.root, lambda: now_ns) as writer:
            if writer is None:
                return ConsolidationRun(leaves_failed=1, days_refused=0)
            stats = job(writer)
        return ConsolidationRun(stats.leaves_failed, stats.days_refused)

    def intraday(self, now_ns: int) -> ConsolidationRun:
        """Run the archive service's intraday merge of the closed hours of `now_ns`'s day."""
        root = str(self.root)
        return self._stage(now_ns, lambda w: run_closed_hours(w, root, now_ns=now_ns, apply=True))

    def nightly(self, now_ns: int) -> ConsolidationRun:
        """Run the nightly consolidation of every closed day, every type."""
        root = str(self.root)
        return self._stage(now_ns, lambda w: run(w, root, None, None, True, now_ns=now_ns))


class Rehearsals:
    """Scratch copies under one scratch directory, with the archive's writer (or a planted one)."""

    def __init__(self, scratch_dir: Path, writer: WriterFactory = maintenance_writer) -> None:
        self._scratch_dir = scratch_dir
        self._writer = writer

    @contextlib.contextmanager
    def scratch(self, catalog: Path, files: Sequence[Path]) -> Iterator[Scratch]:
        """
        Link (or copy) `files`, all under `catalog`, into a fresh scratch catalog, removed on exit.
        A file gone since it was listed raises `FileNotFoundError`.
        """
        with tempfile.TemporaryDirectory(dir=self._scratch_dir, prefix="catalog-") as tmp:
            root = Path(tmp)
            for path in files:
                _link(path, root / path.relative_to(catalog))
            yield Scratch(root, self._writer)
