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
"""The hot-path window's arithmetic (Story 28.1): nearest-rank p99, empty windows, the reset."""

import pytest

from capture.application.hotpath_metrics import HotPathReport
from capture.application.hotpath_metrics import HotPathWindow
from capture.application.hotpath_metrics import nearest_rank_p99


_MS = 1_000_000


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ([7], 7),
        ([3, 1, 2], 3),  # ceil(0.99 * 3) = 3: the largest
        (list(range(1, 101)), 99),  # ceil(99.0) = 99
        (list(range(1, 102)), 100),  # ceil(99.99) = 100
        (list(range(200, 0, -1)), 198),  # unsorted input; ceil(198.0) = 198
    ],
)
def test_p99_is_the_nearest_rank(samples: list[int], expected: int) -> None:
    assert nearest_rank_p99(samples) == expected


def test_p99_of_no_samples_is_none() -> None:
    assert nearest_rank_p99([]) is None


def test_an_empty_window_reports_no_lag_and_no_write() -> None:
    report = HotPathWindow().take(60 * 1_000 * _MS, 0, 0)
    assert report == HotPathReport(
        window_s=60.0,
        queue_depth_max=0,
        messages_processed=0,
        wakes=0,
        lag_max_ms=None,
        lag_p99_ms=None,
        writes=0,
        write_data_ms=None,
        write_data_max_ms=None,
    )


def test_a_window_reports_its_lags_in_ms_and_its_last_and_slowest_write() -> None:
    window = HotPathWindow()
    for lag_ns in [*([1 * _MS] * 59), 3200 * _MS]:
        window.note_lag(lag_ns)
    window.note_write(900 * _MS)  # a slow batch early in the flush must not be hidden
    window.note_write(4_500_000)
    report = window.take(60_001 * _MS, 37, 500)
    assert (report.queue_depth_max, report.messages_processed, report.wakes) == (37, 500, 60)
    assert report.window_s == 60.001
    assert (report.lag_max_ms, report.lag_p99_ms) == (3200.0, 3200.0)
    assert (report.writes, report.write_data_ms, report.write_data_max_ms) == (2, 4.5, 900.0)


def test_p99_departs_from_the_max_only_past_a_hundred_wakes() -> None:
    window = HotPathWindow()
    for lag_ns in [*([1 * _MS] * 200), 3200 * _MS]:
        window.note_lag(lag_ns)
    report = window.take(0, 0, 0)
    assert (report.lag_max_ms, report.lag_p99_ms) == (3200.0, 1.0)


@pytest.mark.parametrize(("wakes", "p99_ms"), [(99, 3200.0), (100, 1.0)])
def test_p99_equals_the_max_up_to_99_wakes(wakes: int, p99_ms: float) -> None:
    """The boundary the docs state: `ceil(0.99 n)` is `n` up to 99 samples, `n - 1` at 100."""
    window = HotPathWindow()
    for lag_ns in [*([1 * _MS] * (wakes - 1)), 3200 * _MS]:
        window.note_lag(lag_ns)
    assert window.take(0, 0, 0).lag_p99_ms == p99_ms


def test_take_starts_a_new_window() -> None:
    window = HotPathWindow()
    window.note_lag(5 * _MS)
    window.note_write(2 * _MS)
    window.take(0, 3, 10)
    assert window.take(0, 0, 0) == HotPathWindow().take(0, 0, 0)


def test_the_record_and_the_log_line_carry_the_same_figures() -> None:
    report = HotPathReport(60.001, 37, 500, 60, 3200.0, 1.25, 0, None, None, 100.0, 200.0)
    assert report.to_dict() == {
        "window_s": 60.001,
        "queue_depth_max": 37,
        "messages_processed": 500,
        "wakes": 60,
        "lag_max_ms": 3200.0,
        "lag_p99_ms": 1.25,
        "writes": 0,
        "write_data_ms": None,
        "write_data_max_ms": None,
        "mem_current_mib": 100.0,
        "mem_limit_mib": 200.0,
    }
    assert report.log_text() == (
        "window_s=60.001 queue_depth_max=37 messages_processed=500 wakes=60 lag_max_ms=3200.0 "
        "lag_p99_ms=1.25 writes=0 write_data_ms=None write_data_max_ms=None "
        "mem_current_mib=100.0 mem_limit_mib=200.0"
    )


def test_take_carries_the_cgroup_memory_point_sample() -> None:
    report = HotPathWindow().take(0, 0, 0, (104_857_600, 209_715_200))
    assert (report.mem_current_mib, report.mem_limit_mib) == (100.0, 200.0)


def test_take_without_a_cgroup_reports_no_memory() -> None:
    report = HotPathWindow().take(0, 0, 0)
    assert (report.mem_current_mib, report.mem_limit_mib) == (None, None)
