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
Which files of the current UTC day the intraday merge groups (Story 25.1b): only whole closed
hours of today with more than one file; nothing reaching the current hour, crossing an hour or of
an earlier day.
"""

from pathlib import Path

from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S

from archive.domain.intraday import INTRADAY_DATA_TYPES
from archive.domain.intraday import NS_PER_HOUR
from archive.domain.intraday import closed_hours_needing_work


_DAY = 20_000 * NS_PER_DAY
_NOW = _DAY + 14 * NS_PER_HOUR + 20 * 60 * NS_PER_S  # 14:20 UTC
_MIN = 60 * NS_PER_S


def _span(hour: int, minute: int, minutes: int = 1) -> tuple[int, int]:
    start = _DAY + hour * NS_PER_HOUR + minute * _MIN
    return start, start + minutes * _MIN - 1


def test_closed_hours_with_several_files_are_grouped() -> None:
    spans = {
        Path("a"): _span(9, 0),
        Path("b"): _span(9, 1),
        Path("c"): _span(13, 58),
        Path("d"): _span(13, 59),
        Path("lonely"): _span(10, 0),
    }
    assert closed_hours_needing_work(spans, _NOW) == {
        (_DAY + 9 * NS_PER_HOUR) // NS_PER_HOUR: [Path("a"), Path("b")],
        (_DAY + 13 * NS_PER_HOUR) // NS_PER_HOUR: [Path("c"), Path("d")],
    }


def test_no_file_reaching_the_current_hour_crossing_an_hour_or_of_another_day_is_grouped() -> None:
    spans = {
        Path("current1"): _span(14, 0),
        Path("current2"): _span(14, 1),
        Path("crossing"): _span(12, 59, 2),
        Path("into_now"): _span(13, 59, 2),
        Path("with_crossing"): _span(12, 30),
        Path("yesterday1"): (_DAY - NS_PER_HOUR, _DAY - NS_PER_HOUR + _MIN),
        Path("yesterday2"): (_DAY - NS_PER_HOUR + 2 * _MIN, _DAY - NS_PER_HOUR + 3 * _MIN),
    }
    assert closed_hours_needing_work(spans, _NOW) == {}


def test_only_the_small_types_are_merged_intraday() -> None:
    assert {
        "mark_price_update",
        "index_price_update",
        "funding_rate_update",
        "custom_open_interest",
        "instrument_status",
    } == INTRADAY_DATA_TYPES
