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
"""`archive.infrastructure.gap_markers`: the marker file I/O over `kernel.archive_markers`."""

import errno
import fcntl
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from kernel import archive_markers
from observability import error_ledger

from archive.application.ports import GapMarkers
from archive.infrastructure import gap_markers
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.gap_markers import load_gaps
from archive.infrastructure.gap_markers import record_gap


_IID = "BTCUSDT-LINEAR.BYBIT"


def test_record_and_load_round_trip(tmp_path: Path) -> None:
    error_ledger.reset()
    record_gap(str(tmp_path), _IID, 10, 20, "write_failed", 3)
    record_gap(str(tmp_path), _IID, 30, 30, "quarantined", 0)
    assert load_gaps(str(tmp_path), _IID) == [(10, 20), (30, 30)]
    assert load_gaps(str(tmp_path), "OTHER.DYDX") == []
    assert error_ledger.counts() == {}


def test_an_inverted_span_is_recorded_ordered_and_ledgered(tmp_path: Path) -> None:
    """
    A backward wall-clock step between a lost trade's arrival and the flush puts `now` before
    its `ts_init`. `decode` refuses an inverted line, which would wedge every rebuild of the
    instrument until the file is hand-edited; the writer orders the span and ledgers the step.
    """
    error_ledger.reset()
    record_gap(str(tmp_path), _IID, 20, 10, "write_failed", 3)
    assert load_gaps(str(tmp_path), _IID) == [(10, 20)]
    assert error_ledger.counts() == {"archive_gaps.inverted_span": 1}
    assert "clock stepped back" in error_ledger.last_details()["archive_gaps.inverted_span"]
    line = archive_markers.path_for(str(tmp_path), _IID).read_text().rstrip("\n")
    assert archive_markers.decode(line).count == 3


def test_a_malformed_line_refuses_the_instrument_naming_the_line(tmp_path: Path) -> None:
    path = archive_markers.path_for(str(tmp_path), _IID)
    path.parent.mkdir(parents=True)
    path.write_text(
        archive_markers.encode(archive_markers.ArchiveGap(_IID, 1, 2, "pruned", 0))
        + "\n\n"
        + '{"instrument_id": "X", "from_ns": 5, "to_ns": 4, "reason": "pruned", "count": 0}\n'
    )
    with pytest.raises(ValueError, match=rf"{path.name}:3: malformed archive-gap marker"):
        load_gaps(str(tmp_path), _IID)


def _contract(markers: GapMarkers, iid: str) -> None:
    """Check the `GapMarkers` contract: durable round trip, spans per instrument, never raises."""
    assert markers.record(archive_markers.ArchiveGap(iid, 5, 9, "pruned", 0)) is True
    assert markers.record(archive_markers.ArchiveGap(iid, 20, 10, "write_failed", 2))  # inverted
    assert markers.load(iid) == [(5, 9), (10, 20)]
    assert markers.load("OTHER.DYDX") == []


def test_gap_marker_files_meet_the_port_contract(tmp_path: Path) -> None:
    error_ledger.reset()
    _contract(GapMarkerFiles(tmp_path), _IID)
    assert error_ledger.counts() == {"archive_gaps.inverted_span": 1}


def test_a_marker_line_is_byte_identical_to_the_frozen_format(tmp_path: Path) -> None:
    GapMarkerFiles(tmp_path).record(archive_markers.ArchiveGap(_IID, 1, 2, "pruned", 0))
    assert archive_markers.path_for(tmp_path, _IID).read_bytes() == (
        b'{"instrument_id": "BTCUSDT-LINEAR.BYBIT", "from_ns": 1, "to_ns": 2, '
        b'"reason": "pruned", "count": 0}\n'
    )


def test_an_unwritable_marker_is_ledgered_not_raised(tmp_path: Path) -> None:
    error_ledger.reset()
    (tmp_path / archive_markers.GAPS_DIRNAME).write_text("a file where the directory should be")
    assert record_gap(str(tmp_path), _IID, 1, 2, "pruned", 0) is False
    assert (
        GapMarkerFiles(tmp_path).record(archive_markers.ArchiveGap(_IID, 1, 2, "pruned", 0))
        is False
    )
    assert error_ledger.counts() == {"archive_gaps.write": 2}


# -- whole lines or nothing (DW-211) ---------------------------------------------------------------


def _one_marker(tmp_path: Path) -> bytes:
    """Write one line to the marker file: what a failed append must leave byte-identical."""
    assert record_gap(str(tmp_path), _IID, 1, 2, "write_failed", 1)
    return archive_markers.path_for(tmp_path, _IID).read_bytes()


def _no_space() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device")


def _half_then_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the first write call land half its bytes, then find the disk full."""
    real_write = os.write
    calls: list[int] = []

    def half(fd: int, data: bytes | memoryview) -> int:
        calls.append(fd)
        if len(calls) > 1:
            return real_write(fd, data)
        real_write(fd, data[: len(data) // 2])
        raise _no_space()

    monkeypatch.setattr(gap_markers.os, "write", half)


def test_a_write_failing_mid_line_is_truncated_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    before = _one_marker(tmp_path)
    _half_then_full(monkeypatch)
    assert record_gap(str(tmp_path), _IID, 5, 9, "pruned", 0) is False
    assert archive_markers.path_for(tmp_path, _IID).read_bytes() == before
    assert load_gaps(str(tmp_path), _IID) == [(1, 2)]  # the next rebuild is not wedged
    assert error_ledger.counts() == {"archive_gaps.write": 1}
    assert "torn" not in error_ledger.last_details()["archive_gaps.write"]


def test_an_fsync_failing_after_the_write_is_truncated_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    before = _one_marker(tmp_path)
    real_fsync, calls = os.fsync, []

    def first_fails(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 1:
            raise _no_space()
        real_fsync(fd)

    monkeypatch.setattr(gap_markers.os, "fsync", first_fails)
    assert record_gap(str(tmp_path), _IID, 5, 9, "pruned", 0) is False
    assert archive_markers.path_for(tmp_path, _IID).read_bytes() == before
    assert load_gaps(str(tmp_path), _IID) == [(1, 2)]
    assert error_ledger.counts() == {"archive_gaps.write": 1}


def test_a_failed_truncate_back_says_a_torn_line_may_remain_and_the_read_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    _one_marker(tmp_path)
    _half_then_full(monkeypatch)

    def no_truncate(fd: int, size: int) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(gap_markers.os, "ftruncate", no_truncate)
    assert record_gap(str(tmp_path), _IID, 5, 9, "pruned", 0) is False
    assert "a torn line may remain" in error_ledger.last_details()["archive_gaps.write"]
    with pytest.raises(ValueError, match="malformed archive-gap marker"):  # never healed silently
        load_gaps(str(tmp_path), _IID)


def _blocked_until_released(tmp_path: Path, held: int, action: Callable[[], object]) -> None:
    """Run `action` in a thread while `held` (a lock on the marker file) is held: it must wait."""
    path = archive_markers.path_for(tmp_path, _IID)
    done = threading.Event()

    def act() -> None:
        action()
        done.set()

    worker = threading.Thread(target=act)
    with path.open("r+") as f:
        fcntl.flock(f.fileno(), held)
        worker.start()
        assert not done.wait(0.2)  # still waiting on the lock
    worker.join(5)
    assert done.is_set()


def test_an_append_waits_for_another_writers_lock(tmp_path: Path) -> None:
    _one_marker(tmp_path)
    _blocked_until_released(
        tmp_path, fcntl.LOCK_EX, lambda: record_gap(str(tmp_path), _IID, 5, 9, "pruned", 0)
    )
    assert load_gaps(str(tmp_path), _IID) == [(1, 2), (5, 9)]


def test_a_read_waits_for_an_append_in_progress(tmp_path: Path) -> None:
    _one_marker(tmp_path)
    read: list[list[tuple[int, int]]] = []
    _blocked_until_released(
        tmp_path, fcntl.LOCK_EX, lambda: read.append(load_gaps(str(tmp_path), _IID))
    )
    assert read == [[(1, 2)]]


def test_a_write_making_no_progress_fails_instead_of_spinning_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    before = _one_marker(tmp_path)
    monkeypatch.setattr(gap_markers.os, "write", lambda fd, data: 0)
    assert record_gap(str(tmp_path), _IID, 5, 9, "pruned", 0) is False
    assert archive_markers.path_for(tmp_path, _IID).read_bytes() == before
    assert error_ledger.counts() == {"archive_gaps.write": 1}  # EIO, ledgered with its traceback


def test_an_interrupt_mid_append_is_truncated_back_and_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _one_marker(tmp_path)
    real_write = os.write

    def interrupted(fd: int, data: bytes | memoryview) -> int:
        real_write(fd, data[:10])
        raise KeyboardInterrupt

    monkeypatch.setattr(gap_markers.os, "write", interrupted)
    with pytest.raises(KeyboardInterrupt):
        record_gap(str(tmp_path), _IID, 5, 9, "pruned", 0)
    monkeypatch.undo()
    assert archive_markers.path_for(tmp_path, _IID).read_bytes() == before
