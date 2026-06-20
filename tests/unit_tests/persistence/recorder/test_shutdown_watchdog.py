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
Unit tests for the MEM-04 recorder-side shutdown watchdog / force-exit safety valve.

WHAT IS (and ISN'T) covered here — be explicit, per the debug session norm:

These tests prove the watchdog's TESTABLE logic in isolation: arming/idempotency,
the deadline decision (`should_force_exit`), that completion disarms the
force-exit, that the daemon thread invokes the injected exit_func on a real
timeout, and that it does NOT exit on a clean (completed-in-time) shutdown. The
`exit_func`, `monotonic` clock, and `should_force_exit` predicate are all injected
/ pure so none of this kills the test process or sleeps for real.

What these tests CANNOT cover (acknowledged): the ACTUAL live wedge — the
nautilus-core data-queue sentinel-loss race that hangs the event loop so
node.run() never returns — is a load-dependent race in core code and is not
reproducible in CI. Only a live Ctrl+C under load can confirm the watchdog fires
against a genuinely wedged loop. That live verification is requested via the
debug-session checkpoint.
"""

import threading

import pytest

from scripts.common_recorder.shutdown_watchdog import DEFAULT_GRACE_SECONDS
from scripts.common_recorder.shutdown_watchdog import WATCHDOG_EXIT_CODE
from scripts.common_recorder.shutdown_watchdog import ShutdownWatchdog


class _FakeClock:
    """A manually-advanced monotonic clock for deterministic deadline tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_rejects_non_positive_grace_seconds():
    # A non-positive grace period is a config error: reject it at construction so a
    # misconfiguration cannot disable (or instantly trip) the safety valve.
    with pytest.raises(ValueError, match="grace_seconds must be positive"):
        ShutdownWatchdog(grace_seconds=0)
    with pytest.raises(ValueError, match="grace_seconds must be positive"):
        ShutdownWatchdog(grace_seconds=-5)


def test_should_force_exit_false_before_armed():
    # Not armed (no Ctrl+C yet) -> never force-exit, regardless of the clock.
    clock = _FakeClock()
    watchdog = ShutdownWatchdog(grace_seconds=20.0, monotonic=clock)

    assert watchdog.should_force_exit(clock()) is False


def test_should_force_exit_false_before_deadline_when_armed():
    # Armed but still WITHIN the grace period -> do NOT force-exit (the first Ctrl+C
    # must get the full grace period to shut down gracefully).
    clock = _FakeClock()
    watchdog = ShutdownWatchdog(grace_seconds=20.0, monotonic=clock)
    watchdog.arm()

    clock.advance(19.999)
    assert watchdog.should_force_exit(clock()) is False


def test_should_force_exit_true_after_deadline_when_armed_and_not_completed():
    # Armed, grace period elapsed, node NOT disposed -> force-exit. This is the
    # wedged-loop case (core sentinel-loss race).
    clock = _FakeClock()
    watchdog = ShutdownWatchdog(grace_seconds=20.0, monotonic=clock)
    watchdog.arm()

    clock.advance(20.0)
    assert watchdog.should_force_exit(clock()) is True
    clock.advance(100.0)
    assert watchdog.should_force_exit(clock()) is True


def test_should_force_exit_false_after_completion_even_past_deadline():
    # Clean disposal (mark_completed) disarms the force-exit, even if the deadline
    # has since passed (a slow-but-healthy shutdown must never be force-killed).
    clock = _FakeClock()
    watchdog = ShutdownWatchdog(grace_seconds=20.0, monotonic=clock)
    watchdog.arm()
    watchdog.mark_completed()

    clock.advance(1000.0)
    assert watchdog.should_force_exit(clock()) is False


def test_arm_is_idempotent_and_counts_from_first_signal():
    # A second arm() (e.g. a second Ctrl+C) must NOT reset/shorten the deadline:
    # the grace period always counts from the FIRST shutdown signal.
    clock = _FakeClock()
    watchdog = ShutdownWatchdog(grace_seconds=20.0, monotonic=clock)

    watchdog.arm()  # deadline = 1020.0
    clock.advance(10.0)  # now 1010.0
    watchdog.arm()  # MUST NOT move the deadline to 1030.0

    clock.advance(10.0)  # now 1020.0 -> at the ORIGINAL deadline
    assert watchdog.should_force_exit(clock()) is True


def test_thread_force_exits_on_timeout_via_injected_exit_func():
    # End-to-end on the real daemon thread (with a tiny grace so the test is fast):
    # arm without ever completing -> the watcher thread calls exit_func exactly once
    # with the watchdog exit code. exit_func is injected so the test process lives.
    exited = threading.Event()
    captured_codes: list[int] = []

    def _fake_exit(code: int) -> None:
        captured_codes.append(code)
        exited.set()

    watchdog = ShutdownWatchdog(grace_seconds=0.05, exit_func=_fake_exit)
    watchdog.start()
    watchdog.arm()

    # The watcher should fire well within this generous wait.
    assert exited.wait(timeout=2.0), "watchdog did not force-exit on timeout"
    assert captured_codes == [WATCHDOG_EXIT_CODE]


def test_thread_does_not_exit_on_clean_completion():
    # The healthy path: armed, then mark_completed() BEFORE the deadline -> the
    # watcher thread must NOT call exit_func. Uses a longer grace so completion
    # comfortably wins the race.
    exit_called = threading.Event()

    def _fake_exit(code: int) -> None:  # pragma: no cover - must never run
        exit_called.set()

    watchdog = ShutdownWatchdog(grace_seconds=5.0, exit_func=_fake_exit)
    watchdog.start()
    watchdog.arm()
    watchdog.mark_completed()
    watchdog.stop()

    # Give the watcher thread a moment; it must have exited WITHOUT force-exiting.
    assert not exit_called.wait(timeout=0.5)


def test_stop_without_arm_does_not_exit():
    # The node disposed cleanly before ever being armed (no Ctrl+C / a non-SIGINT
    # exit): stop() must join the watcher thread without any force-exit.
    exit_called = threading.Event()

    def _fake_exit(code: int) -> None:  # pragma: no cover - must never run
        exit_called.set()

    watchdog = ShutdownWatchdog(grace_seconds=5.0, exit_func=_fake_exit)
    watchdog.start()
    watchdog.stop()

    assert not exit_called.is_set()


def test_log_sink_invoked_before_force_exit():
    # When the watchdog fires, it must emit a bare diagnostic line (via the injected
    # log sink) BEFORE os._exit so the operator can see WHY the process was killed —
    # the asyncio-routed logger may itself be wedged at that point.
    logged: list[str] = []
    exited = threading.Event()

    def _fake_exit(code: int) -> None:
        exited.set()

    watchdog = ShutdownWatchdog(
        grace_seconds=0.05,
        exit_func=_fake_exit,
        log=logged.append,
    )
    watchdog.start()
    watchdog.arm()

    assert exited.wait(timeout=2.0)
    assert len(logged) == 1
    assert "force-exit" in logged[0].lower()


def test_default_grace_seconds_is_a_sane_bounded_value():
    # Sanity: the default grace is positive and bounded (not absurdly large) so the
    # safety valve is meaningful out of the box.
    assert 0 < DEFAULT_GRACE_SECONDS <= 120
    watchdog = ShutdownWatchdog()  # default construction must succeed
    assert watchdog.should_force_exit(0.0) is False


def test_force_exit_writes_durable_timestamped_line_to_record_file(tmp_path):
    # MEM-04 (cycle 4c): on force-exit the watchdog must leave a PERMANENT, durable
    # on-disk record so the operator can correlate the kill — and the resulting data
    # gap — with the catalog AFTER the fact (stderr is ephemeral). Inject a tmp_path
    # file and a fake exit_func so the test process survives; assert the line lands
    # with the expected content + a timestamp, without ever calling os._exit.
    record_file = tmp_path / "dydx_recorder_watchdog.log"
    exited = threading.Event()

    def _fake_exit(code: int) -> None:
        exited.set()

    watchdog = ShutdownWatchdog(
        grace_seconds=0.05,
        exit_func=_fake_exit,
        record_file=record_file,
    )
    watchdog.start()
    watchdog.arm()

    assert exited.wait(timeout=2.0), "watchdog did not force-exit on timeout"

    # The durable record must exist and contain a single force-exit line with both a
    # timestamp and the diagnostic content (so a future restart can read it).
    assert record_file.exists()
    contents = record_file.read_text(encoding="utf-8")
    assert "force-exit" in contents.lower()
    assert "data gap" in contents.lower()
    # ISO-8601 UTC timestamp prefix (YYYY-MM-DDTHH:MM:SSZ) — wall-clock, not monotonic.
    timestamp_token = contents.split(" ", 1)[0]
    assert timestamp_token.endswith("Z")
    assert "T" in timestamp_token
    assert contents.endswith("\n")


def test_force_exit_appends_across_separate_watchdog_instances(tmp_path):
    # MEM-04 (cycle 4c): the record must PERSIST across process restarts — modelled
    # here by two separate watchdog instances writing to the same file. A second
    # force-exit must APPEND, never truncate, so the kill history is preserved.
    record_file = tmp_path / "dydx_recorder_watchdog.log"

    for _ in range(2):
        exited = threading.Event()

        def _fake_exit(code: int, _exited: threading.Event = exited) -> None:
            _exited.set()

        watchdog = ShutdownWatchdog(
            grace_seconds=0.05,
            exit_func=_fake_exit,
            record_file=record_file,
        )
        watchdog.start()
        watchdog.arm()
        assert exited.wait(timeout=2.0)

    lines = record_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_record_file_write_failure_does_not_block_exit(tmp_path):
    # MEM-04 (cycle 4c): the durable write is best-effort — a write failure (here an
    # UNWRITABLE path: the record_file points INTO a regular file, so open() raises
    # NotADirectoryError) must NEVER prevent the exit_func kill. The suppress guard
    # must swallow the error and still force-exit; guaranteed termination is the
    # whole point of the watchdog.
    not_a_dir = tmp_path / "regular_file"
    not_a_dir.write_text("i am a file, not a directory")
    unwritable_record = not_a_dir / "watchdog.log"  # open() on this path will raise

    exited = threading.Event()

    def _fake_exit(code: int) -> None:
        exited.set()

    watchdog = ShutdownWatchdog(
        grace_seconds=0.05,
        exit_func=_fake_exit,
        record_file=unwritable_record,
    )
    watchdog.start()
    watchdog.arm()

    # Despite the unwritable record path, the force-exit must still fire.
    assert exited.wait(timeout=2.0), "write failure blocked the force-exit"
    # And the bad path must NOT have produced a file.
    assert not unwritable_record.exists()
