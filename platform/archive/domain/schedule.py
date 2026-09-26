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
When the `archive` service's nightly and intraday runs are due, and which closed days a run covers
(Story 25.1b). Pure: every instant is a tz-aware UTC `datetime`, every day a `date`, no I/O.

Two cursors, deliberately apart (the story's deviation 1): `next_run` keys on `last_run_day`, the
last day whose *scheduled run completed* whatever its outcome, so a standing failure never turns
into a tight retry loop; `days_to_run` keys on a venue's `last_success_day`, the last day of an
unbroken run of no-FAILED sagas, so the failed day is retried by the next night's run. Neither is a
data verdict: the only persisted day status is `verified_days` (AD-D9).
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass


_ONE_DAY = dt.timedelta(days=1)
_HOURS_PER_DAY = 24


@dataclass(frozen=True)
class Schedule:
    """
    The nightly slot (`nightly_at`, UTC wall time) and the intraday merge period in hours.

    Invariant: the intraday grid tiles the UTC day exactly (`24 % intraday_every_hours == 0`), so
    every day has the same slots and a slot never drifts across midnight; `nightly_at` carries no
    timezone of its own (it is always read as UTC). Violated only by construction, which refuses.
    """

    nightly_at: dt.time
    intraday_every_hours: int

    def __post_init__(self) -> None:
        if self.nightly_at.tzinfo is not None:
            raise ValueError("nightly_at is a naive UTC wall time")
        hours = self.intraday_every_hours
        if isinstance(hours, bool) or hours < 1 or _HOURS_PER_DAY % hours:
            raise ValueError(f"intraday_every_hours must divide 24, got {hours!r}")


def _require_utc(now: dt.datetime) -> None:
    if now.utcoffset() != dt.timedelta(0):
        raise ValueError(f"{now!r} is not a tz-aware UTC instant")


def slot(day: dt.date, schedule: Schedule) -> dt.datetime:
    """Return when `day`'s nightly run is due: the next UTC day at `nightly_at`."""
    return dt.datetime.combine(day + _ONE_DAY, schedule.nightly_at, tzinfo=dt.UTC)


def next_run(now: dt.datetime, schedule: Schedule, last_run_day: dt.date | None) -> dt.datetime:
    """
    When the next scheduled nightly run is due: `max(now, slot(due_day))`, where `due_day` is the
    day after `last_run_day` (yesterday when there is none). A clock that jumps forward makes it
    `now` (the missed days are then covered by `days_to_run`); one that jumps back leaves it at
    the slot after `last_run_day`, so the schedule never runs the same day twice.
    """
    _require_utc(now)
    due_day = last_run_day + _ONE_DAY if last_run_day is not None else now.date() - _ONE_DAY
    return max(now, slot(due_day, schedule))


def assumed_last_run_day(now: dt.datetime, schedule: Schedule) -> dt.date:
    """
    Return the `last_run_day` a scheduler without a cursor assumes: the latest day whose slot is
    at or before `now`, so the next run is the next slot still ahead (never "now").
    """
    _require_utc(now)
    yesterday = now.date() - _ONE_DAY
    return yesterday if now >= slot(yesterday, schedule) else yesterday - _ONE_DAY


def next_intraday(now: dt.datetime, schedule: Schedule) -> dt.datetime:
    """Return the first intraday slot after `now`: hours `h % N == 0`, at `nightly_at`'s minute."""
    _require_utc(now)
    every = schedule.intraday_every_hours
    candidate = now.replace(
        hour=now.hour - now.hour % every,
        minute=schedule.nightly_at.minute,
        second=0,
        microsecond=0,
    )
    while candidate <= now:
        candidate += dt.timedelta(hours=every)
    return candidate


def days_to_run(
    last_success_day: dt.date | None, yesterday: dt.date, cap: int
) -> tuple[tuple[dt.date, ...], tuple[dt.date, dt.date] | None]:
    """
    Return the closed days one venue's next run covers, oldest first, and what the cap skipped.

    From the day after `last_success_day` (yesterday alone when there is none) through `yesterday`;
    past `cap` days only the newest `cap` run and `(first, last)` of the older ones is returned
    so the caller can ledger it. A skipped day is abandoned, not retried: the caller advances the
    watermark from the skipped range's end (`advance_watermark`'s `base`).
    """
    if cap < 1:
        raise ValueError(f"cap must be at least 1, got {cap}")
    first = last_success_day + _ONE_DAY if last_success_day is not None else yesterday
    if first > yesterday:
        return (), None
    start = max(first, yesterday - (cap - 1) * _ONE_DAY)
    days = tuple(start + i * _ONE_DAY for i in range((yesterday - start).days + 1))
    skipped = (first, start - _ONE_DAY) if start > first else None
    return days, skipped


def advance_watermark(
    base: dt.date | None, results_by_day: Mapping[dt.date, bool]
) -> dt.date | None:
    """
    Return a venue's new `last_success_day`: from `base`, forward through each next day whose saga
    had no FAILED step, stopping at the first day that failed or was not run. Without a `base` the
    first day run starts the chain. A success after a gap never moves the watermark past the gap.
    """
    if not results_by_day:
        return base
    watermark = base
    day = base + _ONE_DAY if base is not None else min(results_by_day)
    while results_by_day.get(day) is True:
        watermark = day
        day += _ONE_DAY
    return watermark
