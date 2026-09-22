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
"""`kernel.archive_markers`: the `_archive_gaps/<iid>.jsonl` line format, pure encode/decode."""

from pathlib import Path

import pytest

from kernel import archive_markers
from kernel.archive_markers import ArchiveGap


def test_round_trip() -> None:
    gap = ArchiveGap("BTCUSDT-LINEAR.BYBIT", 1, 2, "write_failed", 3)
    assert archive_markers.decode(archive_markers.encode(gap)) == gap
    assert gap.span == (1, 2)


def test_encode_keeps_the_writers_key_order() -> None:
    line = archive_markers.encode(ArchiveGap("X.DYDX", 5, 6, "pruned", 0))
    assert (
        line
        == '{"instrument_id": "X.DYDX", "from_ns": 5, "to_ns": 6, "reason": "pruned", "count": 0}'
    )


@pytest.mark.parametrize(
    "line", ["", "not json", "[1, 2]", '{"from_ns": 1, "to_ns": 2}', '{"instrument_id": "X"}']
)
def test_a_malformed_line_raises_naming_it(line: str) -> None:
    with pytest.raises(ValueError, match="malformed archive-gap marker"):
        archive_markers.decode(line)


def test_path_for_and_in_gap() -> None:
    assert archive_markers.path_for("/cat", "X.DYDX") == Path("/cat/_archive_gaps/X.DYDX.jsonl")
    assert archive_markers.in_gap(5, [(1, 5)])
    assert not archive_markers.in_gap(6, [(1, 5), (7, 9)])


@pytest.mark.parametrize(
    "line",
    [
        '{"instrument_id": "X", "from_ns": 5, "to_ns": 4, "reason": "pruned", "count": 0}',
        '{"instrument_id": "X", "from_ns": 1.5, "to_ns": 4, "reason": "pruned", "count": 0}',
        '{"instrument_id": "X", "from_ns": true, "to_ns": 4, "reason": "pruned", "count": 0}',
        '{"instrument_id": "X", "from_ns": 1, "to_ns": 4, "reason": "pruned", "count": "0"}',
    ],
)
def test_a_span_that_would_protect_no_row_is_refused(line: str) -> None:
    """An inverted or truncated span would match no row, so the rebuild would overwrite them."""
    with pytest.raises(ValueError, match="archive-gap marker"):
        archive_markers.decode(line)
