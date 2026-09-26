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
The `archive` service (Story 25.1b): nightly maintenance scheduled in our own code, not the host's
crontab.

`ArchiveScheduler` runs the existing nightly saga, never a copy of it. A *full run* is, per closed
day oldest first and per venue, one `run_steps` of that venue-day's nightly chain; then one
`consolidate_catalog --apply` chain; then one backup chain. It is `;` semantics: a venue's FAILED
saga never skips the next venue, the consolidate or the backup. Every step is its own child process
(MEM-01), and the chain runs in `asyncio.to_thread` so the loop keeps publishing status. Each
step's result comes back to the loop through `loop.call_soon_threadsafe`.

Runs:
- the scheduled nightly at `nightly_at` UTC (`archive.domain.schedule.next_run`), covering each
  venue's missed closed days (`days_to_run`, capped at `catch_up_max_days`);
- a `run_now` from `archive:control`, one day's full run;
- every `intraday_consolidate_hours`, the closed-hour merge of the small types.

Only one job runs at a time. The main loop is the only caller of jobs, and the control listener
only queues a day.

Before each job the scheduler waits for the catalog maintenance lock (`.consolidate.lock`), bounded
by `lock_wait_minutes`, by *probing* it (`LockProbe`). It never holds that lock itself: every child
takes it, and a second open file description in this process would lock them out. It never takes a
capture lock either: every step writes closed days or closed hours only.

State (`StateStore`, `state.json`) is a scheduler cursor, never a data verdict (AD-D9 as amended):
- `last_run_day` is the last day whose scheduled run completed, whatever its outcome, so a standing
  failure never becomes a tight retry loop;
- per venue, `last_success_day` is the last day of an unbroken run of no-FAILED sagas, so the next
  night's run retries a failed day;
- `last_run` and `last_intraday` are republished on start.

Reconcile and prune never read it. `verified_days` stays the only day status.

Known limits:
- The lock wait is a probe, not a held lock: a manual `make nightly` can take the lock between the
  probe and a child's own attempt. That child then refuses and its step fails exactly as it would
  have under cron. Upgrade path: take the flock here and hand the locked fd to the children
  (`pass_fds`), with the tools accepting an inherited lock.
- A standing FAILED venue-day is re-run by every night's run, until it succeeds or falls out of the
  `catch_up_max_days` window (ledgered as `archive.catch_up_capped`). Upgrade path: a per-day attempt
  counter in the state with backoff.
- A stop mid-step (redeploy, `docker compose stop`) kills that step at compose's grace period. Every
  step is crash-safe (temp-then-rename, covering-file recovery), and the watermark does not
  advance, so the next run repeats the venue-day.
"""

import asyncio
import contextlib
import datetime as dt
import json
import logging
import re
import tempfile
import uuid
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Protocol

from observability import error_ledger

from archive.application.nightly import Step
from archive.application.nightly import StepResult
from archive.application.nightly import run_steps
from archive.application.nightly import summary_line
from archive.domain.schedule import Schedule
from archive.domain.schedule import advance_watermark
from archive.domain.schedule import assumed_last_run_day
from archive.domain.schedule import days_to_run
from archive.domain.schedule import next_intraday
from archive.domain.schedule import next_run


logger = logging.getLogger(__name__)

STATUS_CHANNEL = "archive:status"
CONTROL_CHANNEL = "archive:control"

HEARTBEAT_SECONDS = 30.0
LOCK_POLL_SECONDS = 30.0
MAX_SLEEP_SECONDS = 60.0
CONTROL_RECONNECT_SECONDS = 2.0
# The exit code of a step that did not complete within a bound -- the maintenance lock stayed held
# past `lock_wait_minutes`, or the child outran `step_timeout_minutes` and was killed (timeout(1)'s
# convention): no real exit code exists.
LOCK_TIMEOUT_EXIT = 124
# A job that raised (not a step that failed: e.g. /tmp full for the saga's scratch directory) is
# recorded as one failed step with this exit code.
JOB_ERROR_EXIT = 1
# A bound on queued `run_now` days: each is a full run of every venue, so a flood of distinct days
# would keep the box busy for days. Past it a request is rejected (ledgered).
MAX_QUEUED_RUNS = 8
# How long the job loop backs off after an unexpected error escaped a run, so a persistent one is
# ledgered once per backoff instead of in a tight loop (the run was not recorded as completed).
JOB_ERROR_BACKOFF_SECONDS = 300.0

NIGHTLY = "nightly"
CATCH_UP = "catch_up"
RUN_NOW = "run_now"
INTRADAY = "intraday"

_ONE_DAY = dt.timedelta(days=1)
_DAY_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")


# --- ports ---------------------------------------------------------------------------------------


class Clock(Protocol):
    """Wall time. Invariant: `now()` is a tz-aware UTC instant (the domain refuses anything else)."""

    def now(self) -> dt.datetime: ...


class JobRunner(Protocol):
    """
    Runs one step's argv as a child process and returns its exit code. Invariant: synchronous and
    blocking (called on a worker thread), and the child's memory is returned to the OS when it
    exits (MEM-01).
    """

    def __call__(self, argv: list[str]) -> int: ...


class LockProbe(Protocol):
    """
    Whether the catalog maintenance lock is free right now. Invariant: the probe never keeps the
    lock -- it is released before `is_free` returns, so a child started next can take it.
    """

    def is_free(self) -> bool: ...


class StateStore(Protocol):
    """
    The persisted scheduler cursor. Invariant: `save` replaces the file atomically (a crash leaves
    the old state or the new one); `load` returns None when there is no state yet and raises
    `ValueError` (or `OSError`) when it is unreadable -- never a guessed state.
    """

    def load(self) -> "SchedulerState | None": ...

    def save(self, state: "SchedulerState") -> None: ...


class StatusBus(Protocol):
    """The `archive:status` publisher. Invariant: publishes each message verbatim, in call order."""

    async def publish(self, message: str) -> None: ...

    async def aclose(self) -> None: ...


class ControlChannel(Protocol):
    """
    The `archive:control` subscription. Invariant: `listen` yields every payload in arrival order
    and raises (never ends quietly) when the connection is lost.
    """

    def listen(self) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class Chains:
    """
    Builds each job's chain. Injected by the composition root so this module never imports the
    CLI modules (`archive.nightly.steps` is the nightly chain).

    Invariant: every step is its own child process (MEM-01). `nightly(venue, day, result_file)`
    is the venue-day saga whose rebuild writes its proof to `result_file`.
    """

    nightly: Callable[[str, str, str], list[Step]]
    consolidate: Callable[[], list[Step]]
    closed_hours: Callable[[], list[Step]]
    backup: Callable[[], list[Step]]


@dataclass(frozen=True)
class SchedulerConfig:
    """
    `archive/config.toml`, validated by its loader (`archive.infrastructure.scheduler_config`).

    Invariant: `venues` is a non-empty subset of `archive.domain.reconciliation.VENUES` with no
    duplicate, `catch_up_max_days >= 1`, `lock_wait_minutes >= 0` and `step_timeout_minutes >= 1`.
    """

    schedule: Schedule
    venues: tuple[str, ...]
    catch_up_max_days: int
    lock_wait_minutes: int
    step_timeout_minutes: int = 360


# --- state and status ----------------------------------------------------------------------------


def iso_z(instant: dt.datetime) -> str:
    """`2026-09-26T03:07:00Z`: seconds precision, UTC, the status wire format."""
    return instant.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_z(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


@dataclass(frozen=True)
class StepRecord:
    """One finished step as the status shows it (`venue` None for consolidate and backup)."""

    venue: str | None
    name: str
    exit: int
    duration_s: float

    def to_json(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "name": self.name,
            "exit": self.exit,
            "duration_s": self.duration_s,
        }


@dataclass
class RunRecord:
    """
    One run as `archive:status` shows it: `day` is the run's last day, `days` every venue-day
    date it ran (empty for a nightly run whose venues were all up to date).
    """

    run_id: str
    kind: str
    day: dt.date
    days: tuple[dt.date, ...]
    started: dt.datetime
    finished: dt.datetime | None = None
    steps: list[StepRecord] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "run_id": self.run_id,
            "kind": self.kind,
            "day": self.day.isoformat(),
            "days": [d.isoformat() for d in self.days],
            "started": iso_z(self.started),
        }
        if self.finished is not None:
            body["finished"] = iso_z(self.finished)
        body["steps"] = [step.to_json() for step in self.steps]
        return body


def run_from_json(body: Any) -> RunRecord:
    """Parse a persisted run back; `ValueError` when it is not one."""
    try:
        return RunRecord(
            run_id=str(body["run_id"]),
            kind=str(body["kind"]),
            day=dt.date.fromisoformat(body["day"]),
            days=tuple(dt.date.fromisoformat(d) for d in body["days"]),
            started=_parse_iso_z(body["started"]),
            finished=_parse_iso_z(body["finished"]) if body.get("finished") else None,
            steps=[
                StepRecord(s["venue"], str(s["name"]), int(s["exit"]), float(s["duration_s"]))
                for s in body["steps"]
            ],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"not a persisted run: {body!r}") from e


@dataclass
class SchedulerState:
    """
    The scheduler cursor (`state.json`). Invariant: never a data verdict -- nothing outside this
    service reads it; `verified_days` is the only day status (AD-D9).
    """

    last_run_day: dt.date | None = None
    last_success_day: dict[str, dt.date] = field(default_factory=dict)
    last_run: RunRecord | None = None
    last_intraday: RunRecord | None = None


def state_to_json(state: SchedulerState) -> dict[str, Any]:
    return {
        "last_run_day": state.last_run_day.isoformat() if state.last_run_day else None,
        "venues": {
            venue: {"last_success_day": day.isoformat()}
            for venue, day in sorted(state.last_success_day.items())
        },
        "last_run": state.last_run.to_json() if state.last_run else None,
        "last_intraday": state.last_intraday.to_json() if state.last_intraday else None,
    }


def state_from_json(body: Any) -> SchedulerState:
    """Parse `state.json`'s content; `ValueError` when it is not a scheduler state."""
    try:
        last_run_day = body["last_run_day"]
        return SchedulerState(
            last_run_day=dt.date.fromisoformat(last_run_day) if last_run_day else None,
            last_success_day={
                str(venue): dt.date.fromisoformat(entry["last_success_day"])
                for venue, entry in body["venues"].items()
            },
            last_run=run_from_json(body["last_run"]) if body.get("last_run") else None,
            last_intraday=(
                run_from_json(body["last_intraday"]) if body.get("last_intraday") else None
            ),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"not a scheduler state: {e!r}") from e


def parse_run_now(message: str, today: dt.date) -> dt.date:
    """
    Return the day an `archive:control` message asks to run: `{"command": "run_now", "day":
    "YYYY-MM-DD" | null}` (null: yesterday). `ValueError` for anything else, including today or a
    later day.
    """
    body = json.loads(message)  # a JSONDecodeError is a ValueError
    if not isinstance(body, dict) or set(body) != {"command", "day"}:
        raise ValueError("expected exactly the keys command and day")
    if body["command"] != "run_now":
        raise ValueError(f"unknown command {body['command']!r}")
    day = body["day"]
    if day is None:
        return today - _ONE_DAY
    if not isinstance(day, str) or not _DAY_TEXT.fullmatch(day):
        raise ValueError(f"day {day!r} is not YYYY-MM-DD")
    parsed = dt.date.fromisoformat(day)
    if parsed >= today:
        raise ValueError(f"day {day} is not a closed day (today is {today})")
    return parsed


async def _posted_callbacks_ran(loop: asyncio.AbstractEventLoop) -> None:
    """
    Return once every callback a worker thread posted with `call_soon_threadsafe` before it
    finished has run. The task awaiting `asyncio.to_thread` can resume before the worker's last
    posted callback ran (seen in tests: the last step missing from the finished run). A barrier
    posted now lands behind every earlier post, since the loop runs posted callbacks in order.
    """
    barrier = loop.create_future()
    loop.call_soon_threadsafe(barrier.set_result, None)
    await barrier


def _failed(results: list[StepResult]) -> bool:
    return any(result.outcome() == "FAILED" for result in results)


# --- the scheduler -------------------------------------------------------------------------------


class ArchiveScheduler:
    """
    The `archive` service's one job loop.

    Invariants: (1) at most one job runs at a time -- only `tick` starts one and the main loop
    awaits it, the control listener only queues; (2) a venue's `last_success_day` advances only
    through contiguous no-FAILED days (`advance_watermark`), and `last_run_day` only when a
    scheduled run completed; (3) the maintenance lock is only ever probed here, never held. The
    commands that could violate them are `tick` (every run) and `handle_control`.
    """

    def __init__(
        self,
        config: SchedulerConfig,
        chains: Chains,
        runner: JobRunner,
        lock: LockProbe,
        store: StateStore,
        bus: StatusBus,
        clock: Clock,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        peak_rss_mb: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self._config = config
        self._schedule = config.schedule
        self._chains = chains
        self._runner = runner
        self._lock = lock
        self._store = store
        self._bus = bus
        self._clock = clock
        self._sleep = sleep
        self._peak_rss_mb = peak_rss_mb
        self._state = SchedulerState()
        self._running: RunRecord | None = None
        self._queued: list[dt.date] = []
        self._next_intraday: dt.datetime | None = None
        self._wake = asyncio.Event()
        self._outbox: asyncio.Queue[str] = asyncio.Queue()

    @property
    def state(self) -> SchedulerState:
        return self._state

    def start(self) -> None:
        """Load the persisted cursor and queue a first status (republishing the last runs)."""
        try:
            state = self._store.load()
        except (OSError, ValueError) as e:
            error_ledger.record(
                "archive.state_unreadable", f"scheduler state unreadable, starting without: {e}", e
            )
            state = None
        self._state = state or SchedulerState()
        if self._state.last_run_day is None:
            # No cursor (first start, or an unreadable one): every slot already past is taken as
            # handled by the regime before this service (the host cron), so a cutover redeploy at
            # 14:00 does not re-run last night's maintenance in the day. A missed night is then
            # one `run_now` away.
            self._state.last_run_day = assumed_last_run_day(self._clock.now(), self._schedule)
        self._notify()

    async def serve(self, control: ControlChannel) -> None:
        """Run until cancelled: the job loop, the control listener and the status publisher."""
        self.start()
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._publisher())
                tasks.create_task(self.control_loop(control))
                tasks.create_task(self._job_loop())
        finally:
            await self._bus.aclose()

    async def _job_loop(self) -> None:
        while True:
            try:
                busy = await self.tick()
            except Exception as e:  # a bug or an environment failure outside any one job
                error_ledger.record(
                    "archive.job_error",
                    f"a run was abandoned; retrying after {JOB_ERROR_BACKOFF_SECONDS:.0f}s",
                    e,
                )
                self._running = None
                self._notify()
                await self._sleep(JOB_ERROR_BACKOFF_SECONDS)
                continue
            if not busy:
                await self._idle()

    async def tick(self) -> bool:
        """Run the one job due now, if any (nightly, then a queued run_now, then intraday)."""
        now = self._clock.now()
        if now >= next_run(now, self._schedule, self._state.last_run_day):
            await self._scheduled_run(now)
            return True
        if self._queued:
            await self._run_now(self._queued.pop(0))
            return True
        if now >= self._intraday_due(now):
            await self._intraday(now)
            return True
        return False

    async def _idle(self) -> None:
        """
        Sleep until the next slot, at most `MAX_SLEEP_SECONDS` (a clock jump is seen within
        one chunk), or until a `run_now` is queued.
        """
        now = self._clock.now()
        due = min(next_run(now, self._schedule, self._state.last_run_day), self._intraday_due(now))
        seconds = min(max((due - now).total_seconds(), 0.0), MAX_SLEEP_SECONDS)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)
        self._wake.clear()

    def _intraday_due(self, now: dt.datetime) -> dt.datetime:
        """Return the pending intraday slot, re-anchored when a clock jumped back past a period."""
        period = dt.timedelta(hours=self._schedule.intraday_every_hours)
        if self._next_intraday is None or self._next_intraday - now > period:
            self._next_intraday = next_intraday(now, self._schedule)
        return self._next_intraday

    # --- runs ------------------------------------------------------------------------------------

    async def _scheduled_run(self, now: dt.datetime) -> None:
        yesterday = now.date() - _ONE_DAY
        plan, bases = self._catch_up_plan(yesterday)
        only_yesterday = all(days in ((), (yesterday,)) for days in plan.values())
        outcomes = await self._full_run(NIGHTLY if only_yesterday else CATCH_UP, plan, yesterday)
        self._advance(outcomes, bases)
        self._state.last_run_day = yesterday
        self._commit()

    def _catch_up_plan(
        self, yesterday: dt.date
    ) -> tuple[dict[str, tuple[dt.date, ...]], dict[str, dt.date | None]]:
        """
        Each venue's days to run and the base its watermark advances from. A venue without a
        watermark starts from yesterday's eve, so a failed first night is retried the next.
        """
        plan: dict[str, tuple[dt.date, ...]] = {}
        bases: dict[str, dt.date | None] = {}
        capped: list[str] = []
        cap = self._config.catch_up_max_days
        for venue in self._config.venues:
            last = self._state.last_success_day.get(venue)
            plan[venue], skipped = days_to_run(last, yesterday, cap)
            bases[venue] = skipped[1] if skipped else (last or yesterday - _ONE_DAY)
            if skipped:
                capped.append(f"{venue} {skipped[0]}..{skipped[1]}")
        if capped:
            error_ledger.record(
                "archive.catch_up_capped",
                f"missed days beyond catch_up_max_days={cap} are not run: {'; '.join(capped)} "
                "(run each by hand: make nightly VENUE=... DAY=...)",
            )
        return plan, bases

    async def _run_now(self, day: dt.date) -> None:
        days: tuple[dt.date, ...] = (day,)
        plan = dict.fromkeys(self._config.venues, days)
        outcomes = await self._full_run(RUN_NOW, plan, day)
        # Only an existing watermark advances (and only when `day` extends it): a run_now of an old
        # day must never *start* a venue's watermark there, or the next night would catch up (and
        # ledger as capped) days that were maintained before this service existed.
        watermarks = dict(self._state.last_success_day)
        self._advance({v: r for v, r in outcomes.items() if v in watermarks}, watermarks)
        self._commit()

    async def _intraday(self, now: dt.datetime) -> None:
        today = now.date()
        run = self._begin(INTRADAY, today, (today,))
        await self._run_job(run, None, self._chains.closed_hours(), today, "intraday merge")
        self._end(run)
        self._next_intraday = next_intraday(self._clock.now(), self._schedule)
        self._commit()

    async def _full_run(
        self, kind: str, plan: dict[str, tuple[dt.date, ...]], day: dt.date
    ) -> dict[str, dict[dt.date, bool]]:
        """
        Every planned venue-day oldest first (all venues of a day before the next day), then the
        consolidate, then the backup -- `;` semantics: nothing is skipped because a saga failed.
        Returns each venue's per-day outcome (True: no FAILED step).
        """
        days = tuple(sorted({d for venue_days in plan.values() for d in venue_days}))
        run = self._begin(kind, days[-1] if days else day, days)
        outcomes: dict[str, dict[dt.date, bool]] = {venue: {} for venue in plan}
        for venue_day in days:
            for venue in self._config.venues:
                if venue_day in plan.get(venue, ()):
                    outcomes[venue][venue_day] = await self._venue_day(run, venue, venue_day)
        await self._run_job(run, None, self._chains.consolidate(), day, "consolidate")
        await self._run_job(run, None, self._chains.backup(), day, "backup")
        self._end(run)
        return outcomes

    async def _venue_day(self, run: RunRecord, venue: str, day: dt.date) -> bool:
        """One venue-day nightly saga, its rebuild proof in a scratch directory of its own."""
        text = day.isoformat()
        saga_id = uuid.uuid4().hex
        try:
            with tempfile.TemporaryDirectory(prefix="archive-nightly-") as scratch:
                chain = self._chains.nightly(
                    venue, text, str(Path(scratch) / "rebuild_result.json")
                )
                results = await self._run_job(
                    run, venue, chain, day, f"nightly {venue} {text}", saga_id
                )
        except OSError as e:  # e.g. /tmp full: this venue-day fails, the run goes on (`;`)
            results = [self._job_error(run, venue, f"nightly {venue} {text}", e)]
        logger.info("%s", summary_line(venue, text, saga_id, results, self._peak_rss_mb()))
        return not _failed(results)

    async def _run_job(
        self,
        run: RunRecord,
        venue: str | None,
        chain: list[Step],
        day: dt.date,
        what: str,
        run_id: str | None = None,
    ) -> list[StepResult]:
        """
        Wait for the maintenance lock, then run `chain` on a worker thread; each step's result is
        added to the run (and published) from the loop as it finishes. Past the lock bound the job
        is one failed `lock_timeout` step.
        """
        loop = asyncio.get_running_loop()

        def on_step(result: StepResult) -> None:
            loop.call_soon_threadsafe(self._step_done, run, venue, result)

        saga_id = run_id or uuid.uuid4().hex
        try:
            if not await self._await_lock(what):
                timeout = StepResult("lock_timeout", LOCK_TIMEOUT_EXIT, 0.0)
                self._step_done(run, venue, timeout)
                return [timeout]
            results = await asyncio.to_thread(
                run_steps, chain, self._runner, saga_id, venue or "", day.isoformat(), on_step
            )
        except Exception as e:  # e.g. the lock probe's open() or a child that cannot start
            await _posted_callbacks_ran(loop)
            return [self._job_error(run, venue, what, e)]
        await _posted_callbacks_ran(loop)
        return results

    def _job_error(
        self, run: RunRecord, venue: str | None, what: str, error: BaseException
    ) -> StepResult:
        """Record a job that raised as one failed `error` step; the run goes on (`;`)."""
        error_ledger.record("archive.job_error", f"{what}: job abandoned", error)
        result = StepResult("error", JOB_ERROR_EXIT, 0.0)
        self._step_done(run, venue, result)
        return result

    async def _await_lock(self, what: str) -> bool:
        """
        Wait for the maintenance lock: True once it is free (probed every `LOCK_POLL_SECONDS`, never
        held), False when it is still held after `lock_wait_minutes`. Each wait is ledgered once.
        """
        if self._lock.is_free():
            return True
        bound = dt.timedelta(minutes=self._config.lock_wait_minutes)
        error_ledger.record(
            "archive.lock_wait", f"{what}: the maintenance lock is held; waiting up to {bound}"
        )
        deadline = self._clock.now() + bound
        while (remaining := (deadline - self._clock.now()).total_seconds()) > 0:
            await self._sleep(min(LOCK_POLL_SECONDS, remaining))
            if self._lock.is_free():
                return True
        error_ledger.record(
            "archive.lock_timeout",
            f"{what}: the maintenance lock was still held after {bound}; job not run",
        )
        return False

    def _advance(
        self, outcomes: dict[str, dict[dt.date, bool]], bases: Mapping[str, dt.date | None]
    ) -> None:
        for venue, results in outcomes.items():
            watermark = advance_watermark(bases.get(venue), results)
            if watermark is not None:
                self._state.last_success_day[venue] = watermark

    def _begin(self, kind: str, day: dt.date, days: tuple[dt.date, ...]) -> RunRecord:
        run = RunRecord(uuid.uuid4().hex, kind, day, days, self._clock.now())
        self._running = run
        logger.info("archive %s run %s: days %s", kind, run.run_id, [d.isoformat() for d in days])
        self._notify()
        return run

    def _end(self, run: RunRecord) -> None:
        run.finished = self._clock.now()
        self._running = None
        if run.kind == INTRADAY:
            self._state.last_intraday = run
        else:
            self._state.last_run = run
        failed = [s.name for s in run.steps if s.exit not in (0, 2)]
        logger.info("archive %s run %s finished; failed steps: %s", run.kind, run.run_id, failed)

    def _step_done(self, run: RunRecord, venue: str | None, result: StepResult) -> None:
        run.steps.append(StepRecord(venue, result.name, result.code, round(result.seconds, 3)))
        self._notify()

    def _commit(self) -> None:
        """Persist the cursor (a failed write is ledgered; the next run retries) and publish."""
        try:
            self._store.save(self._state)
        except OSError as e:
            error_ledger.record("archive.state_write", f"scheduler state not written: {e!r}", e)
        self._notify()

    # --- control ---------------------------------------------------------------------------------

    def handle_control(self, message: str) -> None:
        """Queue a valid `run_now` (a day already queued is dropped); ledger anything else."""
        try:
            day = parse_run_now(message, self._clock.now().date())
        except ValueError as e:
            error_ledger.record(
                "archive.control_rejected", f"archive:control message {message!r} ignored: {e}"
            )
            return
        if day in self._queued or self._covers(day):
            logger.info("archive:control run_now %s is already queued or running; dropped", day)
            return
        if len(self._queued) >= MAX_QUEUED_RUNS:
            error_ledger.record(
                "archive.control_rejected",
                f"run_now {day} ignored: {MAX_QUEUED_RUNS} runs already queued",
            )
            return
        self._queued.append(day)
        logger.info("archive:control run_now %s queued", day)
        self._wake.set()

    def _covers(self, day: dt.date) -> bool:
        """Whether the run in progress already maintains `day` (every venue, then the backup)."""
        running = self._running
        return running is not None and running.kind != INTRADAY and day in running.days

    async def control_loop(self, channel: ControlChannel) -> None:
        """
        Serve every `archive:control` message; a lost connection is ledgered and re-subscribed
        after `CONTROL_RECONNECT_SECONDS`. The channel is closed when the loop ends.
        """
        try:
            while True:
                try:
                    async for message in channel.listen():
                        self.handle_control(message)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    error_ledger.record(
                        "archive.control_redis",
                        f"archive:control listener error, reconnecting in "
                        f"{CONTROL_RECONNECT_SECONDS:.0f}s",
                        e,
                    )
                    await self._sleep(CONTROL_RECONNECT_SECONDS)
        finally:
            await channel.aclose()

    # --- status ----------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Build the `archive:status` payload (its key order is the published language)."""
        now = self._clock.now()
        last_run, last_intraday = self._state.last_run, self._state.last_intraday
        return {
            "next_run": iso_z(next_run(now, self._schedule, self._state.last_run_day)),
            "next_intraday": iso_z(self._intraday_due(now)),
            "running": self._running.to_json() if self._running else None,
            "last_run": last_run.to_json() if last_run else None,
            "last_intraday": last_intraday.to_json() if last_intraday else None,
        }

    def _notify(self) -> None:
        """Queue the status as it is now; the publisher sends each queued message in order."""
        self._outbox.put_nowait(json.dumps(self.status()))

    async def publish_pending(self) -> None:
        """Publish every queued status message now (the publisher's work, callable directly)."""
        while not self._outbox.empty():
            await self._publish(self._outbox.get_nowait())

    async def _publisher(self) -> None:
        """
        Publish each queued status, and the current one after `HEARTBEAT_SECONDS` without any.

        Known limit: the queue is unbounded, and holds one message per step or run event while
        Redis is slow (each publish is bounded by the bus's 1 s socket timeout) -- dozens a night,
        never a growth that lasts. Upgrade path: keep only the newest message when full.
        """
        while True:
            try:
                message = await asyncio.wait_for(self._outbox.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                message = json.dumps(self.status())
            await self._publish(message)

    async def _publish(self, message: str) -> None:
        try:
            await self._bus.publish(message)
        except Exception as e:  # any Redis failure: the next step or heartbeat publishes again
            error_ledger.record("archive.status_publish", "archive:status not published", e)
