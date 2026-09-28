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
The capture lock (Story 25.1): a running collector holds a shared `flock` on
`<catalog>/.capture-<VENUE>.lock` (`kernel.archive_markers.capture_lock_path`) for its whole
process life, so an archive tool that must not write under a live collector (`repair_catalog`)
can tell by trying the lock exclusively (`archive.infrastructure.maintenance_lock`).

Invariant: while any collector of a venue runs, nobody holds that venue's lock exclusively, and
while an archive tool holds it exclusively, no collector of the venue starts capturing -- it waits,
retrying every second and still honouring shutdown, and ledgers `collector.capture_lock_wait`
once per wait. Shared, not exclusive, so a second partitioned capture process of one venue (AD-D18)
stays legal. The kernel drops a flock when its process dies (SIGKILL, OOM), so the file's presence
alone never means "running"; it is never unlinked, because unlinking a flocked file lets a later
opener lock a different inode. The pid and start time written into it are informational only.
"""

import asyncio
import fcntl
import json
import logging
import os
import time
from pathlib import Path
from typing import IO

from kernel.archive_markers import capture_lock_path

from capture.application import sites
from capture.application.ports import Ledger


logger = logging.getLogger(__name__)

RETRY_SECONDS = 1.0


def _try_shared(lock: IO[str]) -> bool:
    try:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _write_owner(lock: IO[str]) -> None:
    """Informational: the pid and start time of the latest holder (several may share the lock)."""
    lock.seek(0)
    lock.truncate()
    lock.write(json.dumps({"pid": os.getpid(), "started_at": time.time()}))
    lock.flush()


async def acquire_capture_lock(
    catalog_path: str | Path,
    venue: str,
    shutting_down: asyncio.Event,
    retry_seconds: float = RETRY_SECONDS,
    *,
    ledger: Ledger,
) -> IO[str] | None:
    """
    Take the venue's capture lock shared and return the open file (keep it open for the whole
    process: closing it releases the lock); None when shutdown was requested while waiting for an
    exclusive holder to finish.
    """
    path = capture_lock_path(catalog_path, venue)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("a+")
    try:
        acquired = await _wait_shared(lock, venue, shutting_down, retry_seconds, ledger)
        if acquired:
            _write_owner(lock)
    except BaseException:  # CancelledError included: a cancelled start leaks no descriptor
        lock.close()
        raise
    if not acquired:
        lock.close()
        return None
    return lock


async def _wait_shared(
    lock: IO[str], venue: str, shutting_down: asyncio.Event, retry_seconds: float, ledger: Ledger
) -> bool:
    """Retry the shared lock every `retry_seconds`; False when shutdown came first."""
    waited = False
    while not _try_shared(lock):
        if not waited:
            ledger(
                sites.CAPTURE_LOCK_WAIT,
                f"{venue}: an archive tool holds {lock.name} exclusively; capture waits for it",
            )
            waited = True
        if shutting_down.is_set():
            return False
        try:
            await asyncio.wait_for(shutting_down.wait(), timeout=retry_seconds)
        except TimeoutError:
            continue
    if waited:
        logger.info("%s: capture lock free again, starting", venue)
    return True
