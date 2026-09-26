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
The `archive` service's pure schedule (Story 25.1b): when the nightly and intraday runs are due --
across midnight and against a clock that jumps -- which days a run covers, and how the per-venue
watermark advances.
"""

import datetime as dt

import pytest

from archive.domain.schedule import Schedule
from archive.domain.schedule import advance_watermark
from archive.domain.schedule import assumed_last_run_day
from archive.domain.schedule import days_to_run
from archive.domain.schedule import next_intraday
from archive.domain.schedule import next_run
from archive.domain.schedule import slot


_SCHEDULE = Schedule(dt.time(3, 7), 4)
_D = dt.date(2026, 9, 26)


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=dt.UTC)


def _day(offset: int) -> dt.date:
    return _D + dt.timedelta(days=offset)


def test_a_day_is_due_the_next_utc_day_at_nightly_at() -> None:
    assert slot(_day(-1), _SCHEDULE) == _at(_D, 3, 7)


def test_the_normal_night_waits_for_the_slot_then_is_due() -> None:
    assert next_run(_at(_D, 1, 0), _SCHEDULE, _day(-2)) == _at(_D, 3, 7)
    assert next_run(_at(_D, 3, 7), _SCHEDULE, _day(-2)) == _at(_D, 3, 7)


def test_after_a_run_the_next_is_tomorrows_slot_across_midnight() -> None:
    assert next_run(_at(_D, 3, 8), _SCHEDULE, _day(-1)) == _at(_day(1), 3, 7)
    assert next_run(_at(_day(1), 0, 30), _SCHEDULE, _day(-1)) == _at(_day(1), 3, 7)


def test_without_state_yesterday_is_due_at_todays_slot() -> None:
    assert next_run(_at(_D, 1, 0), _SCHEDULE, None) == _at(_D, 3, 7)
    now = _at(_D, 14, 0)
    assert next_run(now, _SCHEDULE, None) == now


def test_a_clock_jumping_forward_makes_the_run_due_now() -> None:
    now = _at(_day(3), 10, 0)
    assert next_run(now, _SCHEDULE, _day(-1)) == now


def test_a_clock_jumping_back_never_reruns_the_last_day() -> None:
    # The last run covered yesterday; the clock is then set back a whole day.
    now = _at(_day(-1), 4, 0)
    assert next_run(now, _SCHEDULE, _day(-1)) == _at(_day(1), 3, 7)


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(ValueError):
        next_run(dt.datetime(2026, 9, 26, 3, 7), _SCHEDULE, None)  # noqa: DTZ001


def test_the_intraday_slot_is_the_next_grid_point_strictly_after_now() -> None:
    assert next_intraday(_at(_D, 14, 20), _SCHEDULE) == _at(_D, 16, 7)
    assert next_intraday(_at(_D, 16, 7), _SCHEDULE) == _at(_D, 20, 7)
    assert next_intraday(_at(_D, 16, 5), _SCHEDULE) == _at(_D, 16, 7)
    assert next_intraday(_at(_D, 22, 30), _SCHEDULE) == _at(_day(1), 0, 7)


def test_a_period_that_does_not_divide_the_day_is_refused() -> None:
    for hours in (0, 5, 7, 25):
        with pytest.raises(ValueError):
            Schedule(dt.time(3, 7), hours)
    with pytest.raises(ValueError):
        Schedule(dt.time(3, 7, tzinfo=dt.UTC), 4)


def test_days_to_run_covers_the_missed_days_oldest_first() -> None:
    assert days_to_run(_day(-2), _day(-1), 7) == ((_day(-1),), None)
    assert days_to_run(_day(-5), _day(-1), 7) == (
        (_day(-4), _day(-3), _day(-2), _day(-1)),
        None,
    )
    assert days_to_run(None, _day(-1), 7) == ((_day(-1),), None)
    assert days_to_run(_day(-1), _day(-1), 7) == ((), None)


def test_a_gap_over_the_cap_runs_the_newest_days_and_names_the_skipped_range() -> None:
    days, skipped = days_to_run(_day(-11), _day(-1), 7)
    assert days == tuple(_day(-i) for i in range(7, 0, -1))
    assert skipped == (_day(-10), _day(-8))


def test_the_watermark_advances_only_through_contiguous_successes() -> None:
    results = {_day(-3): True, _day(-2): False, _day(-1): True}
    assert advance_watermark(_day(-4), results) == _day(-3)
    assert advance_watermark(_day(-4), {_day(-2): True}) == _day(-4)  # a gap stops it
    assert advance_watermark(_day(-4), {}) == _day(-4)
    assert advance_watermark(None, {_day(-1): True}) == _day(-1)
    assert advance_watermark(None, {_day(-1): False}) is None


def _on_26th(hour: int, minute: int) -> dt.datetime:
    return dt.datetime.combine(dt.date(2026, 9, 26), dt.time(hour, minute), tzinfo=dt.UTC)


def test_without_a_cursor_every_slot_already_past_is_assumed_run() -> None:
    at = _on_26th
    assert assumed_last_run_day(at(3, 7), _SCHEDULE) == dt.date(2026, 9, 25)
    assert assumed_last_run_day(at(3, 6), _SCHEDULE) == dt.date(2026, 9, 24)
    assert next_run(at(3, 6), _SCHEDULE, assumed_last_run_day(at(3, 6), _SCHEDULE)) == at(3, 7)
    after = assumed_last_run_day(at(14, 0), _SCHEDULE)
    assert next_run(at(14, 0), _SCHEDULE, after) == at(3, 7) + dt.timedelta(days=1)
