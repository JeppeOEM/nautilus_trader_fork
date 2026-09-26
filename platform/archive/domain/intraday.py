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
Which of the current UTC day's files the intraday merge may consolidate (Story 25.1b). Pure.

The small types (mark/index price, funding rate, open interest, instrument status) arrive as ~1-7
row minute files all day; merging each *closed hour* of them keeps the catalog's file count down
between nightly runs. Snapshots, trades and deltas stay nightly-only: their minute files are large
enough that a day-level merge is the right unit, and the rebuild and the reconcile read them. The
nightly consolidate later absorbs the hourly files into the day's one file (an hourly file lies
wholly inside its day, so `closed_days_needing_work` groups it like any minute file).
"""

from collections.abc import Mapping
from pathlib import Path

from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S


NS_PER_HOUR = 3_600 * NS_PER_S

INTRADAY_DATA_TYPES = frozenset(
    {
        "mark_price_update",
        "index_price_update",
        "funding_rate_update",
        "custom_open_interest",
        "instrument_status",
    }
)


def closed_hours_needing_work(
    spans: Mapping[Path, tuple[int, int]], now_ns: int
) -> dict[int, list[Path]]:
    """
    Hour index (ns // `NS_PER_HOUR`) -> the files lying wholly inside that hour, for the hours of
    the current UTC day before the current hour that hold more than one such file.

    A file whose `ts_init` span reaches the current hour, crosses an hour boundary or lies in an
    earlier day is in no group, so it is never read into a merge, written or removed here; the
    `CLOSED_HOUR` scope of `CatalogWriter` refuses such a file again on its own.
    """
    today = now_ns // NS_PER_DAY
    current_hour = now_ns // NS_PER_HOUR
    hours: dict[int, list[Path]] = {}
    for path, (start_ns, end_ns) in spans.items():
        hour = start_ns // NS_PER_HOUR
        inside_one_hour = hour == end_ns // NS_PER_HOUR
        if inside_one_hour and hour < current_hour and start_ns // NS_PER_DAY == today:
            hours.setdefault(hour, []).append(path)
    return {hour: sorted(files) for hour, files in hours.items() if len(files) > 1}
