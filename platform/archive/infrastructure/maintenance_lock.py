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
The two catalog locks an archive tool takes: the maintenance flock and the capture lock.

`maintenance` holds `<catalog>/.consolidate.lock` (the name predates this context and is kept, so
a pre-25.1 tool still running during a deploy excludes a new one) exclusively and hands out the
`CatalogFiles` writer for exactly that block: one catalog-rewriting job at a time.

`capture_exclusive` tries a non-blocking exclusive `flock` on the venue's capture lock
(`kernel.archive_markers.capture_lock_path`), which every running collector holds shared for its
whole life (`capture.infrastructure.capture_lock`). Taken, it proves no collector of that venue runs and
keeps one from starting (capture waits and retries) until the block ends; refused, a collector is
running. A flock dies with its process, so a SIGKILL/OOM never leaves a stale "running": the
file's presence alone means nothing, and it is never unlinked (a later opener would lock another
inode).

`maintenance_free` only *probes* the maintenance flock (take non-blocking, release at once): the
`archive` service waits on it before each job without ever holding it while its children run --
they take it themselves, and this process holding a second open file description on it would
lock them out (Story 25.1b).
"""

import contextlib
import fcntl
import time
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

from kernel.archive_markers import capture_lock_path

from archive.infrastructure.catalog_files import CatalogFiles


MAINTENANCE_LOCK_NAME = ".consolidate.lock"


@contextlib.contextmanager
def _exclusive(path: Path) -> Iterator[bool]:
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


@contextlib.contextmanager
def maintenance(
    catalog: str | Path, now_ns: Callable[[], int] = time.time_ns
) -> Iterator[CatalogFiles | None]:
    """
    Hold the catalog-maintenance flock for the block and yield the writer; yield None (lock not
    taken) when another maintenance run holds it -- the caller must then refuse to start.
    """
    with _exclusive(Path(catalog) / MAINTENANCE_LOCK_NAME) as locked:
        yield CatalogFiles(now_ns) if locked else None


def maintenance_free(catalog: str | Path) -> bool:
    """
    Whether no maintenance run holds the catalog's flock right now; the probe's own hold is
    released before this returns. A catalog that does not exist has no holder (True): the job's
    steps then refuse it loudly themselves (`archive.catalog_missing`).
    """
    root = Path(catalog)
    if not root.is_dir():
        return True
    with _exclusive(root / MAINTENANCE_LOCK_NAME) as locked:
        return locked


class MaintenanceLockProbe:
    """`LockProbe` over one catalog's maintenance flock. Invariant: never keeps the lock."""

    def __init__(self, catalog: str | Path) -> None:
        self._catalog = catalog

    def is_free(self) -> bool:
        return maintenance_free(self._catalog)


@contextlib.contextmanager
def capture_exclusive(catalog: str | Path, venue: str) -> Iterator[bool]:
    """
    Yield True while holding the venue's capture lock exclusively (no collector of `venue` runs,
    and none can start inside the block); False when a collector holds it.
    """
    with _exclusive(capture_lock_path(catalog, venue)) as locked:
        yield locked
