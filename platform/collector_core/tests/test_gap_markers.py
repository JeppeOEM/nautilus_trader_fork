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

from pathlib import Path

from kernel import archive_markers
from observability import error_ledger

from collector_core.gap_markers import record_gap


_IID = "BTCUSDT-LINEAR.BYBIT"


def test_a_marker_is_the_frozen_line_and_an_inverted_span_is_ordered(tmp_path: Path) -> None:
    error_ledger.reset()
    record_gap(str(tmp_path), _IID, 20, 10, "write_failed", 3)
    line = archive_markers.path_for(tmp_path, _IID).read_text()
    assert line == (
        '{"instrument_id": "BTCUSDT-LINEAR.BYBIT", "from_ns": 10, "to_ns": 20, '
        '"reason": "write_failed", "count": 3}\n'
    )
    assert error_ledger.counts() == {"archive_gaps.inverted_span": 1}


def test_an_unwritable_marker_is_ledgered_never_raised(tmp_path: Path) -> None:
    error_ledger.reset()
    (tmp_path / archive_markers.GAPS_DIRNAME).write_text("a file where the directory should be")
    record_gap(str(tmp_path), _IID, 1, 2, "quarantined", 0)
    assert error_ledger.counts() == {"archive_gaps.write": 1}
