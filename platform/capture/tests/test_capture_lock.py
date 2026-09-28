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
The capture lock (Story 25.1): held shared for the collector's life, an exclusive holder makes
capture wait (ledgered once, shutdown still honoured) and then lets it start, and closing releases
it without unlinking the file. The archive side (`capture_exclusive`) is exercised against it in
`platform/tests/test_capture_archive_handoff.py`: capture never imports archive.
"""

import asyncio
import fcntl
import json
import os
from pathlib import Path

from kernel.archive_markers import capture_lock_path
from observability import error_ledger

from capture.infrastructure.capture_lock import acquire_capture_lock


def _exclusive_would_succeed(path: Path) -> bool:
    """Try what an archive tool tries: a non-blocking `LOCK_EX`."""
    with path.open("a") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def test_capture_holds_the_lock_shared_and_records_its_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = await acquire_capture_lock(
            tmp_path, "BYBIT", asyncio.Event(), ledger=error_ledger.record
        )
        second = await acquire_capture_lock(
            tmp_path, "BYBIT", asyncio.Event(), ledger=error_ledger.record
        )  # AD-D18
        assert first is not None
        assert second is not None
        path = capture_lock_path(tmp_path, "BYBIT")
        assert not _exclusive_would_succeed(path)
        assert json.loads(path.read_text())["pid"] == os.getpid()
        first.close()
        second.close()

    asyncio.run(scenario())


def test_an_exclusive_holder_blocks_capture_until_it_releases(tmp_path: Path) -> None:
    error_ledger.reset()
    path = capture_lock_path(tmp_path, "DYDX")

    async def scenario() -> None:
        holder = path.open("a")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)  # e.g. repair_catalog
        waiting = asyncio.create_task(
            acquire_capture_lock(
                tmp_path, "DYDX", asyncio.Event(), retry_seconds=0.01, ledger=error_ledger.record
            )
        )
        await asyncio.sleep(0.05)
        assert not waiting.done()  # capture waits
        holder.close()  # the archive tool finishes
        lock = await asyncio.wait_for(waiting, timeout=1.0)
        assert lock is not None
        assert not _exclusive_would_succeed(path)
        lock.close()

    asyncio.run(scenario())
    assert error_ledger.counts() == {"collector.capture_lock_wait": 1}  # once per wait


def test_a_shutdown_while_waiting_gives_up_the_wait(tmp_path: Path) -> None:
    path = capture_lock_path(tmp_path, "HYPERLIQUID")

    async def scenario() -> None:
        with path.open("a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            shutting_down = asyncio.Event()
            waiting = asyncio.create_task(
                acquire_capture_lock(
                    tmp_path,
                    "HYPERLIQUID",
                    shutting_down,
                    retry_seconds=5.0,
                    ledger=error_ledger.record,
                )
            )
            await asyncio.sleep(0.01)
            shutting_down.set()
            assert await asyncio.wait_for(waiting, timeout=1.0) is None

    asyncio.run(scenario())


def test_closing_releases_the_lock_and_keeps_the_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        lock = await acquire_capture_lock(
            tmp_path / "catalog", "BYBIT", asyncio.Event(), ledger=error_ledger.record
        )
        assert lock is not None
        lock.close()  # the collector exits (a SIGKILL releases it the same way)

    asyncio.run(scenario())
    path = capture_lock_path(tmp_path / "catalog", "BYBIT")
    assert path.exists()  # never unlinked: a later opener must lock this same inode
    assert _exclusive_would_succeed(path)


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_a_cancelled_wait_closes_its_descriptor(tmp_path: Path) -> None:
    path = capture_lock_path(tmp_path, "BYBIT")

    async def scenario() -> None:
        with path.open("a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = _open_fds()
            waiting = asyncio.create_task(
                acquire_capture_lock(
                    tmp_path,
                    "BYBIT",
                    asyncio.Event(),
                    retry_seconds=5.0,
                    ledger=error_ledger.record,
                )
            )
            await asyncio.sleep(0.01)
            assert _open_fds() == before + 1  # the waiter's own descriptor
            waiting.cancel()
            try:
                await waiting
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("the wait was not cancelled")
            assert _open_fds() == before  # closed on cancellation, never leaked

    asyncio.run(scenario())
