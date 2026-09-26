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

from pathlib import Path

import pytest
from kernel import archive_markers
from observability import error_ledger

from archive.application.ports import GapMarkers
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
