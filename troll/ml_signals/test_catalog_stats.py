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
"""Self-check: gap/outage detection finds obvious cases and ignores regular spacing."""

from ml_signals.catalog_stats import _overlapping_intervals
from ml_signals.catalog_stats import find_gaps


def test_finds_a_single_obvious_gap() -> None:
    one_second = 1_000_000_000
    regular = [i * one_second for i in range(10)]  # 0s, 1s, 2s, ... 9s
    after_gap = [t + 600 * one_second for t in range(10, 15)]  # resumes 10 minutes later
    ts_ns = regular + after_gap

    gaps = find_gaps(ts_ns)

    assert len(gaps) == 1
    assert gaps[0] == (regular[-1], after_gap[0])


def test_no_gaps_in_regularly_spaced_series() -> None:
    one_second = 1_000_000_000
    ts_ns = [i * one_second for i in range(20)]

    assert find_gaps(ts_ns) == []


def test_overlapping_intervals_finds_only_shared_outage() -> None:
    # Mark price gapped 100-200 and 500-600; book deltas gapped 150-300.
    # Only the 150-200 overlap represents both streams being silent at once.
    mark_gaps = [(100, 200), (500, 600)]
    book_gaps = [(150, 300)]

    overlaps = _overlapping_intervals(mark_gaps, book_gaps)

    assert overlaps == [(150, 200)]


def test_overlapping_intervals_empty_when_no_shared_window() -> None:
    assert _overlapping_intervals([(100, 200)], [(300, 400)]) == []


if __name__ == "__main__":
    test_finds_a_single_obvious_gap()
    test_no_gaps_in_regularly_spaced_series()
    test_overlapping_intervals_finds_only_shared_outage()
    test_overlapping_intervals_empty_when_no_shared_window()
    print("ok")
