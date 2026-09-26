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
`ArchiveScheduler` over the story's I/O matrix (Story 25.1b), with an injected clock and hand-rolled
fakes for the step runner, the lock probe, the state store and the status bus: no Redis, no child
process. The steps themselves have their own tests (`test_nightly.py`, `test_consolidate_day.py`).
"""

import asyncio
import datetime as dt
import json
import logging
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from observability import error_ledger

from archive.application.nightly import Step
from archive.application.scheduler import JOB_ERROR_EXIT
from archive.application.scheduler import LOCK_TIMEOUT_EXIT
from archive.application.scheduler import MAX_QUEUED_RUNS
from archive.application.scheduler import ArchiveScheduler
from archive.application.scheduler import Chains
from archive.application.scheduler import RunRecord
from archive.application.scheduler import SchedulerConfig
from archive.application.scheduler import SchedulerState
from archive.application.scheduler import StepRecord
from archive.application.scheduler import parse_run_now
from archive.application.scheduler import state_to_json
from archive.domain.schedule import Schedule
from archive.infrastructure.maintenance_lock import maintenance
from archive.infrastructure.maintenance_lock import maintenance_free
from archive.infrastructure.state_store import JsonStateStore
from archive.scheduler import timed_runner


_D = dt.date(2026, 9, 26)
_VENUES = ("DYDX", "BYBIT", "HYPERLIQUID")
_NIGHTLY_STEPS = ("rebuild_seconds", "compare_klines")


def _day(offset: int) -> dt.date:
    return _D + dt.timedelta(days=offset)


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=dt.UTC)


class _Clock:
    def __init__(self, now: dt.datetime) -> None:
        self.current = now

    def now(self) -> dt.datetime:
        return self.current


class _Runner:
    """Records each step's argv; exits 1 for a step whose argv after the job name is in `fail`."""

    def __init__(self, fail: frozenset[tuple[str, ...]] = frozenset()) -> None:
        self.fail = fail
        self.ran: list[tuple[str, ...]] = []

    def __call__(self, argv: list[str]) -> int:
        self.ran.append(tuple(argv))
        return 1 if tuple(argv[1:]) in self.fail else 0


def _chains(backup_enabled: bool = True) -> Chains:
    def nightly(venue: str, day: str, result_file: str) -> list[Step]:
        assert result_file.endswith("rebuild_result.json")
        return [Step(name, ["nightly", venue, day, name]) for name in _NIGHTLY_STEPS]

    return Chains(
        nightly=nightly,
        consolidate=lambda: [Step("consolidate_catalog", ["consolidate"])],
        closed_hours=lambda: [Step("consolidate_closed_hours", ["closed_hours"])],
        backup=(lambda: [Step("backup_catalog", ["backup"])]) if backup_enabled else None,
    )


class _Lock:
    """Held for the first `held` probes (or always), then free."""

    def __init__(self, held: int = 0, always: bool = False) -> None:
        self.held = held
        self.always = always
        self.probes = 0

    def is_free(self) -> bool:
        self.probes += 1
        return not self.always and self.probes > self.held


class _Store:
    def __init__(self, state: SchedulerState | None = None, error: Exception | None = None) -> None:
        self.state = state
        self.error = error
        self.save_error: OSError | None = None
        self.saved: list[dict[str, Any]] = []

    def load(self) -> SchedulerState | None:
        if self.error is not None:
            raise self.error
        return self.state

    def save(self, state: SchedulerState) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(state_to_json(state))
        self.state = state


class _Bus:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, Any]] = []
        self.raw: list[str] = []

    async def publish(self, message: str) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.raw.append(message)
        self.messages.append(json.loads(message))

    async def aclose(self) -> None:
        return None


class _Rig:
    def __init__(
        self,
        now: dt.datetime,
        state: SchedulerState | None = None,
        *,
        runner: _Runner | None = None,
        lock: _Lock | None = None,
        store: _Store | None = None,
        bus: _Bus | None = None,
        cap: int = 7,
        lock_wait_minutes: int = 60,
        backup_enabled: bool = True,
    ) -> None:
        error_ledger.reset()
        self.clock = _Clock(now)
        self.runner = runner or _Runner()
        self.lock = lock or _Lock()
        self.store = store or _Store(state)
        self.bus = bus or _Bus()
        self.slept: list[float] = []
        config = SchedulerConfig(
            Schedule(dt.time(3, 7), 4),
            _VENUES,
            cap,
            lock_wait_minutes,
            backup_enabled=backup_enabled,
        )
        self.scheduler = ArchiveScheduler(
            config,
            _chains(backup_enabled),
            self.runner,
            self.lock,
            self.store,
            self.bus,
            self.clock,
            sleep=self._sleep,
        )
        self.scheduler.start()

    async def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.current += dt.timedelta(seconds=seconds)

    def tick(self) -> bool:
        async def go() -> bool:
            ran = await self.scheduler.tick()
            await self.scheduler.publish_pending()
            return ran

        return asyncio.run(go())

    def nightly_ran(self) -> list[tuple[str, str]]:
        """(venue, day) of each saga, in order."""
        return [
            (r[1], r[2]) for r in self.runner.ran if r[0] == "nightly" and r[3] == "rebuild_seconds"
        ]


def _state(last_run_day: dt.date | None, watermark: dt.date | None) -> SchedulerState:
    marks = dict.fromkeys(_VENUES, watermark) if watermark else {}
    return SchedulerState(last_run_day=last_run_day, last_success_day=marks)


def _sequence(runner: _Runner) -> list[str]:
    return [r[0] if r[0] != "nightly" else f"{r[1]}:{r[3]}" for r in runner.ran]


def test_the_normal_night_runs_each_venue_then_consolidate_then_backup() -> None:
    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)))
    assert rig.tick()
    assert _sequence(rig.runner) == [
        *(f"{venue}:{step}" for venue in _VENUES for step in _NIGHTLY_STEPS),
        "consolidate",
        "backup",
    ]
    assert rig.nightly_ran() == [(venue, "2026-09-25") for venue in _VENUES]
    assert rig.scheduler.state.last_run_day == _day(-1)
    assert rig.scheduler.state.last_success_day == {venue: _day(-1) for venue in _VENUES}
    assert rig.store.saved[-1]["last_run_day"] == "2026-09-25"


def test_status_is_published_after_every_step_in_the_wire_shape() -> None:
    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)))
    rig.tick()
    running_counts = [len(m["running"]["steps"]) for m in rig.bus.messages if m["running"]]
    assert running_counts == list(range(3 * len(_NIGHTLY_STEPS) + 2 + 1))  # begin, every step
    final = rig.bus.messages[-1]
    assert list(final) == [
        "next_run",
        "next_intraday",
        "running",
        "last_run",
        "last_intraday",
        "backup",
    ]
    assert final["backup"] == "enabled"
    assert final["running"] is None
    assert final["next_run"] == "2026-09-27T03:07:00Z"
    assert final["next_intraday"] == "2026-09-26T04:07:00Z"
    last = final["last_run"]
    assert list(last) == ["run_id", "kind", "day", "days", "started", "finished", "steps"]
    assert (last["kind"], last["day"], last["days"]) == ("nightly", "2026-09-25", ["2026-09-25"])
    assert last["started"] == last["finished"] == "2026-09-26T03:07:00Z"
    assert last["steps"][0] == {
        "venue": "DYDX",
        "name": "rebuild_seconds",
        "exit": 0,
        "duration_s": last["steps"][0]["duration_s"],
    }
    assert [s["venue"] for s in last["steps"][-2:]] == [None, None]
    assert [s["name"] for s in last["steps"][-2:]] == ["consolidate_catalog", "backup_catalog"]


def test_one_venue_failing_skips_nothing_and_its_day_is_retried_next_night() -> None:
    fail = frozenset({("BYBIT", "2026-09-25", "rebuild_seconds")})
    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)), runner=_Runner(fail))
    rig.tick()
    assert _sequence(rig.runner)[-4:] == [
        "HYPERLIQUID:rebuild_seconds",
        "HYPERLIQUID:compare_klines",
        "consolidate",
        "backup",
    ]
    assert "BYBIT:compare_klines" not in _sequence(rig.runner)  # the saga stops at its failure
    state = rig.scheduler.state
    assert state.last_success_day["BYBIT"] == _day(-2)
    assert state.last_success_day["DYDX"] == _day(-1)
    assert state.last_run_day == _day(-1)  # no tight retry loop
    assert not rig.tick()  # same morning: nothing is due again
    rig.clock.current = _at(_day(1), 3, 7)
    rig.runner.ran.clear()
    rig.runner.fail = frozenset()
    rig.tick()
    assert rig.nightly_ran() == [
        ("BYBIT", "2026-09-25"),
        ("DYDX", "2026-09-26"),
        ("BYBIT", "2026-09-26"),
        ("HYPERLIQUID", "2026-09-26"),
    ]
    assert rig.scheduler.state.last_success_day["BYBIT"] == _day(0)
    assert rig.bus.messages[-1]["last_run"]["kind"] == "catch_up"


def test_three_missed_nights_are_caught_up_oldest_first_then_one_consolidate_and_backup() -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-5), _day(-5)))
    rig.tick()
    days = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    assert rig.nightly_ran() == [(venue, day) for day in days for venue in _VENUES]
    assert _sequence(rig.runner)[-2:] == ["consolidate", "backup"]
    assert _sequence(rig.runner).count("consolidate") == 1
    last = rig.bus.messages[-1]["last_run"]
    assert (last["kind"], last["day"], last["days"]) == ("catch_up", "2026-09-25", days)
    assert rig.scheduler.state.last_success_day == {venue: _day(-1) for venue in _VENUES}


def test_a_gap_over_the_cap_runs_the_newest_days_and_is_ledgered_once() -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-11), _day(-11)))
    rig.tick()
    assert sorted({day for _, day in rig.nightly_ran()}) == [
        _day(-i).isoformat() for i in range(7, 0, -1)
    ]
    assert error_ledger.counts() == {"archive.catch_up_capped": 1}
    assert "2026-09-16..2026-09-18" in error_ledger.last_details()["archive.catch_up_capped"]
    # The skipped days are abandoned loudly, so the watermark is not stuck behind them.
    assert rig.scheduler.state.last_success_day == {venue: _day(-1) for venue in _VENUES}


def test_without_state_the_first_run_is_the_next_slot_covering_its_yesterday_only() -> None:
    rig = _Rig(_at(_D, 14, 0), None)
    assert not rig.tick()  # last night's slot is taken as handled before this service (cutover)
    assert rig.bus.messages[-1]["next_run"] == "2026-09-27T03:07:00Z"
    rig.clock.current = _at(_day(1), 3, 7)
    assert rig.tick()
    assert rig.nightly_ran() == [(venue, "2026-09-26") for venue in _VENUES]
    assert error_ledger.counts() == {}


def test_without_state_before_the_slot_the_same_nights_run_covers_yesterday() -> None:
    rig = _Rig(_at(_D, 2, 0), None)
    assert not rig.tick()
    rig.clock.current = _at(_D, 3, 7)
    assert rig.tick()
    assert rig.nightly_ran() == [(venue, "2026-09-25") for venue in _VENUES]


@pytest.mark.parametrize("error", [ValueError("corrupt"), OSError("unreadable")])
def test_an_unreadable_state_is_ledgered_and_treated_as_none(error: Exception) -> None:
    rig = _Rig(_at(_D, 14, 0), store=_Store(error=error))
    assert error_ledger.counts() == {"archive.state_unreadable": 1}
    assert not rig.tick()
    assert rig.scheduler.state.last_run_day == _day(-1)


def test_a_failed_first_night_without_state_is_retried_the_next_night() -> None:
    fail = frozenset({("DYDX", "2026-09-25", "rebuild_seconds")})
    rig = _Rig(_at(_D, 2, 0), None, runner=_Runner(fail))
    rig.clock.current = _at(_D, 3, 7)
    rig.tick()
    assert rig.scheduler.state.last_success_day["DYDX"] == _day(-2)
    rig.clock.current = _at(_day(1), 3, 7)
    rig.runner.ran.clear()
    rig.tick()
    assert ("DYDX", "2026-09-25") in rig.nightly_ran()


def test_a_held_lock_is_waited_for_by_probing_and_ledgered_once() -> None:
    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)), lock=_Lock(held=2))
    rig.tick()
    assert error_ledger.counts() == {"archive.lock_wait": 1}
    assert rig.slept == [30.0, 30.0]
    assert _sequence(rig.runner)[-2:] == ["consolidate", "backup"]


def test_a_lock_held_past_the_bound_fails_each_job_as_lock_timeout() -> None:
    rig = _Rig(
        _at(_D, 3, 7), _state(_day(-2), _day(-2)), lock=_Lock(always=True), lock_wait_minutes=1
    )
    rig.tick()
    assert rig.runner.ran == []
    counts = error_ledger.counts()
    assert counts["archive.lock_wait"] == counts["archive.lock_timeout"] == 5
    steps = rig.bus.messages[-1]["last_run"]["steps"]
    assert {(s["name"], s["exit"]) for s in steps} == {("lock_timeout", LOCK_TIMEOUT_EXIT)}
    assert [s["venue"] for s in steps] == [*_VENUES, None, None]
    assert rig.scheduler.state.last_success_day == {venue: _day(-2) for venue in _VENUES}
    assert rig.scheduler.state.last_run_day == _day(-1)


def test_a_clock_jumping_forward_catches_up_and_one_jumping_back_never_reruns() -> None:
    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)))
    rig.tick()
    rig.runner.ran.clear()
    rig.clock.current = _at(_day(-1), 4, 0)  # back a day
    assert not rig.tick()
    rig.clock.current = _at(_day(3), 10, 0)  # forward three days
    assert rig.tick()
    assert sorted({day for _, day in rig.nightly_ran()}) == [
        "2026-09-26",
        "2026-09-27",
        "2026-09-28",
    ]


def test_run_now_runs_a_days_full_sequence_after_the_running_job() -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-1), _day(-2)))
    rig.scheduler.handle_control('{"command": "run_now", "day": null}')
    rig.scheduler.handle_control('{"command": "run_now", "day": "2026-09-25"}')  # duplicate
    rig.scheduler.handle_control('{"command": "run_now", "day": "2026-09-20"}')
    assert rig.tick()
    assert rig.nightly_ran() == [(venue, "2026-09-25") for venue in _VENUES]
    assert _sequence(rig.runner)[-2:] == ["consolidate", "backup"]
    assert rig.bus.messages[-1]["last_run"]["kind"] == "run_now"
    assert rig.scheduler.state.last_success_day["DYDX"] == _day(-1)
    assert rig.scheduler.state.last_run_day == _day(-1)  # a run_now is not the schedule's run
    rig.runner.ran.clear()
    assert rig.tick()
    assert rig.nightly_ran() == [(venue, "2026-09-20") for venue in _VENUES]
    assert rig.scheduler.state.last_success_day["DYDX"] == _day(-1)  # an old day moves nothing
    rig.runner.ran.clear()
    assert not rig.tick()
    assert error_ledger.counts() == {}


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        "[]",
        '{"command": "stop", "day": null}',
        '{"command": "run_now"}',
        '{"command": "run_now", "day": null, "extra": 1}',
        '{"command": "run_now", "day": "2026-09-2"}',
        '{"command": "run_now", "day": "2026-02-30"}',
        '{"command": "run_now", "day": 20260925}',
        '{"command": "run_now", "day": "2026-09-26"}',
        '{"command": "run_now", "day": "2026-10-01"}',
    ],
)
def test_a_bad_control_message_is_ledgered_and_ignored(message: str) -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-1), _day(-1)))
    rig.scheduler.handle_control(message)
    assert error_ledger.counts() == {"archive.control_rejected": 1}
    assert not rig.tick()


def test_run_now_never_starts_a_watermark_on_an_old_day() -> None:
    rig = _Rig(_at(_D, 14, 0), SchedulerState(last_run_day=_day(-1)))
    rig.scheduler.handle_control('{"command": "run_now", "day": "2026-09-06"}')
    assert rig.tick()
    assert rig.scheduler.state.last_success_day == {}


def test_run_now_of_a_day_the_running_job_covers_is_dropped() -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-1), _day(-1)))
    rig.scheduler._running = RunRecord("r", "nightly", _day(-1), (_day(-1),), _at(_D, 3, 7))
    rig.scheduler.handle_control('{"command": "run_now", "day": null}')
    rig.scheduler._running = None
    assert not rig.tick()
    assert error_ledger.counts() == {}


def test_run_now_past_the_queue_bound_is_ledgered() -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-1), _day(-1)))
    for offset in range(2, 2 + MAX_QUEUED_RUNS + 1):
        day = _day(-offset).isoformat()
        rig.scheduler.handle_control(f'{{"command": "run_now", "day": "{day}"}}')
    assert error_ledger.counts() == {"archive.control_rejected": 1}


def test_a_job_that_raises_is_one_failed_step_and_the_run_goes_on() -> None:
    class _BrokenLock(_Lock):
        def is_free(self) -> bool:
            raise OSError("catalog unmounted")

    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)), lock=_BrokenLock())
    assert rig.tick()
    last = rig.bus.messages[-1]["last_run"]
    assert [(s["name"], s["exit"]) for s in last["steps"]] == [("error", JOB_ERROR_EXIT)] * 5
    assert error_ledger.counts() == {"archive.job_error": 5}
    assert rig.scheduler.state.last_run_day == _day(-1)
    assert rig.scheduler.state.last_success_day == {venue: _day(-2) for venue in _VENUES}


def test_a_step_outrunning_its_timeout_is_killed_and_exits_124() -> None:
    error_ledger.reset()
    run = timed_runner(0.2)
    assert run([sys.executable, "-c", "import time; time.sleep(30)"]) == LOCK_TIMEOUT_EXIT
    assert error_ledger.counts() == {"archive.step_timeout": 1}
    assert run([sys.executable, "-c", "raise SystemExit(3)"]) == 3


def test_parse_run_now_defaults_to_yesterday() -> None:
    assert parse_run_now('{"command": "run_now", "day": null}', _D) == _day(-1)
    assert parse_run_now('{"day": "2026-09-01", "command": "run_now"}', _D) == dt.date(2026, 9, 1)


def test_the_intraday_merge_runs_at_each_grid_slot() -> None:
    rig = _Rig(_at(_D, 14, 20), _state(_day(-1), _day(-1)))
    assert not rig.tick()
    rig.clock.current = _at(_D, 16, 7)
    assert rig.tick()
    assert rig.runner.ran == [("closed_hours",)]
    final = rig.bus.messages[-1]
    assert final["next_intraday"] == "2026-09-26T20:07:00Z"
    assert final["last_run"] is None
    intraday = final["last_intraday"]
    assert (intraday["kind"], intraday["day"]) == ("intraday", "2026-09-26")
    assert intraday["steps"] == [
        {
            "venue": None,
            "name": "consolidate_closed_hours",
            "exit": 0,
            "duration_s": intraday["steps"][0]["duration_s"],
        }
    ]
    assert not rig.tick()


def test_a_failing_backup_is_a_failed_step_in_the_status() -> None:
    rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)), runner=_Runner())
    rig.runner.fail = frozenset({()})  # argv ["backup"] -> argv[1:] == ()
    rig.tick()
    steps = rig.bus.messages[-1]["last_run"]["steps"]
    assert steps[-1]["name"] == "backup_catalog"
    assert steps[-1]["exit"] == 1
    assert steps[-2]["exit"] == 1  # consolidate, argv ["consolidate"], fails the same way
    assert error_ledger.counts() == {"nightly.consolidate_catalog": 1, "nightly.backup_catalog": 1}


def test_with_the_backup_off_a_full_run_ends_at_the_consolidate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="archive.application.scheduler"):
        rig = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)), backup_enabled=False)
        rig.tick()
    assert _sequence(rig.runner) == [
        *(f"{venue}:{step}" for venue in _VENUES for step in _NIGHTLY_STEPS),
        "consolidate",
    ]
    assert rig.bus.messages[-1]["last_run"]["steps"][-1]["name"] == "consolidate_catalog"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["off-site backup disabled: the catalog has no copy off this host"]
    assert {m["backup"] for m in rig.bus.messages} == {"disabled"}
    assert error_ledger.counts() == {}


def test_with_the_backup_on_no_warning_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archive.application.scheduler"):
        _Rig(_at(_D, 14, 0), _state(_day(-1), _day(-1)))
    assert not [r for r in caplog.records if "off-site backup" in r.getMessage()]


@pytest.mark.parametrize("backup_enabled", [True, False])
def test_a_config_and_chains_that_disagree_on_the_backup_are_refused(
    backup_enabled: bool,
) -> None:
    config = SchedulerConfig(
        Schedule(dt.time(3, 7), 4), _VENUES, 7, 60, backup_enabled=backup_enabled
    )
    with pytest.raises(ValueError, match="backup"):
        ArchiveScheduler(
            config,
            _chains(not backup_enabled),
            _Runner(),
            _Lock(),
            _Store(),
            _Bus(),
            _Clock(_at(_D, 0)),
        )


def test_the_last_runs_are_persisted_and_republished_on_start() -> None:
    first = _Rig(_at(_D, 3, 7), _state(_day(-2), _day(-2)))
    first.tick()
    second = _Rig(_at(_D, 9, 0), store=first.store)
    asyncio.run(second.scheduler.publish_pending())
    assert second.bus.messages[0]["last_run"] == first.bus.messages[-1]["last_run"]
    assert second.scheduler.state.last_run_day == _day(-1)


def test_a_failed_publish_and_a_failed_state_write_are_ledgered() -> None:
    store = _Store(_state(_day(-2), _day(-2)))
    store.save_error = OSError("disk full")
    rig = _Rig(_at(_D, 3, 7), store=store, bus=_Bus(fail=True))
    rig.tick()
    counts = error_ledger.counts()
    assert counts["archive.state_write"] == 1
    assert counts["archive.status_publish"] == 3 * len(_NIGHTLY_STEPS) + 2 + 3  # start/begin/commit


class _Channel:
    """Fails its first subscription, then yields `messages` and waits."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.listens = 0
        self.closed = False

    async def listen(self) -> AsyncIterator[str]:
        self.listens += 1
        if self.listens == 1:
            raise ConnectionError("redis down")
        for message in self.messages:
            yield message
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


def test_the_control_listener_reconnects_after_a_lost_connection() -> None:
    rig = _Rig(_at(_D, 14, 0), _state(_day(-1), _day(-1)))
    channel = _Channel(['{"command": "run_now", "day": "2026-09-24"}'])

    async def go() -> None:
        task = asyncio.create_task(rig.scheduler.control_loop(channel))
        for _ in range(20):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert error_ledger.counts() == {"archive.control_redis": 1}
    assert channel.listens == 2
    assert channel.closed
    assert rig.tick()
    assert rig.nightly_ran() == [(venue, "2026-09-24") for venue in _VENUES]


def test_the_json_state_store_round_trips_and_refuses_a_corrupt_file(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state")
    assert store.load() is None
    run = RunRecord(
        "abc",
        "catch_up",
        _day(-1),
        (_day(-2), _day(-1)),
        _at(_D, 3, 7),
        _at(_D, 4, 0),
        [
            StepRecord("BYBIT", "rebuild_seconds", 0, 1.5),
            StepRecord(None, "backup_catalog", 1, 2.0),
        ],
    )
    state = SchedulerState(_day(-1), {"BYBIT": _day(-2)}, run, None)
    store.save(state)
    assert store.load() == state
    assert not list(store.path.parent.glob("*.tmp"))
    store.path.write_text('{"last_run_day": "2026-13-01"}')
    with pytest.raises(ValueError):
        store.load()
    store.path.write_text("{")
    with pytest.raises(ValueError):
        store.load()


def test_the_lock_probe_sees_a_holder_and_never_keeps_the_lock(tmp_path: Path) -> None:
    assert maintenance_free(tmp_path)
    with maintenance(tmp_path) as writer:
        assert writer is not None  # the probe released it
        assert not maintenance_free(tmp_path)
    assert maintenance_free(tmp_path)
    assert maintenance_free(tmp_path / "absent")
