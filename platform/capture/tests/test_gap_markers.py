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
"""Capture's own marker writer: the frozen `kernel.archive_markers` line, failures ledgered."""

import errno
import fcntl
import os
import threading
from pathlib import Path

import pytest
from kernel import archive_markers
from observability import error_ledger

from capture.infrastructure import gap_markers
from capture.infrastructure.gap_markers import record_gap


_IID = "BTCUSDT-LINEAR.BYBIT"


def test_a_marker_is_the_frozen_line_and_an_inverted_span_is_ordered(tmp_path: Path) -> None:
    error_ledger.reset()
    record_gap(str(tmp_path), _IID, 20, 10, "write_failed", 3, error_ledger.record)
    line = archive_markers.path_for(tmp_path, _IID).read_text()
    assert line == (
        '{"instrument_id": "BTCUSDT-LINEAR.BYBIT", "from_ns": 10, "to_ns": 20, '
        '"reason": "write_failed", "count": 3}\n'
    )
    assert error_ledger.counts() == {"archive_gaps.inverted_span": 1}


def test_an_unwritable_marker_is_ledgered_never_raised(tmp_path: Path) -> None:
    error_ledger.reset()
    (tmp_path / archive_markers.GAPS_DIRNAME).write_text("a file where the directory should be")
    record_gap(str(tmp_path), _IID, 1, 2, "quarantined", 0, error_ledger.record)
    assert error_ledger.counts() == {"archive_gaps.write": 1}


# -- whole lines or nothing (DW-211) ---------------------------------------------------------------


def _half_then_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the first write call land half its bytes, then find the disk full."""
    real_write = os.write
    calls: list[int] = []

    def half(fd: int, data: bytes | memoryview) -> int:
        calls.append(fd)
        if len(calls) > 1:
            return real_write(fd, data)
        real_write(fd, data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(gap_markers.os, "write", half)


def test_a_write_failing_mid_line_is_truncated_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    record_gap(str(tmp_path), _IID, 1, 2, "write_failed", 1, error_ledger.record)
    path = archive_markers.path_for(tmp_path, _IID)
    before = path.read_bytes()
    _half_then_full(monkeypatch)
    record_gap(str(tmp_path), _IID, 5, 9, "quarantined", 0, error_ledger.record)
    assert path.read_bytes() == before  # the archive's next read is not wedged by a torn line
    assert error_ledger.counts() == {"archive_gaps.write": 1}
    assert "torn" not in error_ledger.last_details()["archive_gaps.write"]


def test_an_fsync_failing_after_the_write_is_truncated_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    record_gap(str(tmp_path), _IID, 1, 2, "write_failed", 1, error_ledger.record)
    path = archive_markers.path_for(tmp_path, _IID)
    before = path.read_bytes()
    real_fsync, calls = os.fsync, []

    def first_fails(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_fsync(fd)

    monkeypatch.setattr(gap_markers.os, "fsync", first_fails)
    record_gap(str(tmp_path), _IID, 5, 9, "quarantined", 0, error_ledger.record)
    assert path.read_bytes() == before
    assert error_ledger.counts() == {"archive_gaps.write": 1}
    assert "torn" not in error_ledger.last_details()["archive_gaps.write"]


def test_a_failed_truncate_back_says_a_torn_line_may_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    _half_then_full(monkeypatch)

    def no_truncate(fd: int, size: int) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(gap_markers.os, "ftruncate", no_truncate)
    record_gap(str(tmp_path), _IID, 5, 9, "quarantined", 0, error_ledger.record)
    assert error_ledger.counts() == {"archive_gaps.write": 1}
    assert "a torn line may remain" in error_ledger.last_details()["archive_gaps.write"]


def test_an_append_waits_for_the_other_writers_lock(tmp_path: Path) -> None:
    """Archive appends to the same file: its truncate-back must never cut a line of ours."""
    record_gap(str(tmp_path), _IID, 1, 2, "write_failed", 1, error_ledger.record)
    path = archive_markers.path_for(tmp_path, _IID)
    done = threading.Event()

    def append() -> None:
        record_gap(str(tmp_path), _IID, 5, 9, "quarantined", 0, error_ledger.record)
        done.set()

    worker = threading.Thread(target=append)
    with path.open("r+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        worker.start()
        assert not done.wait(0.2)
    worker.join(5)
    assert len(path.read_text().splitlines()) == 2
