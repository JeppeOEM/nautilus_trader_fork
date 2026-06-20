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
Recorder-side shutdown watchdog / force-exit safety valve (MEM-04).

WHY this exists — a CONFIRMED race in nautilus_trader CORE (off-limits to edit):

``nautilus_trader/live/data_engine.py`` ``_enqueue_sentinels`` uses
``queue.put_nowait(sentinel)``, which raises ``asyncio.QueueFull`` (silently
swallowed by asyncio's default loop exception handler) when the bounded data
queue is AT ``maxsize`` at the stop instant — exactly the condition this recorder
hits under the dYdX full-depth L2 flood (see the debug session, finding B/C). If
the data-queue sentinel is lost, ``_run_data_queue`` never breaks, so
``TradingNode.run_async``'s ``asyncio.gather`` never completes and ``node.run()``
never returns — the process is WEDGED. Worse, ``nautilus_trader/system/kernel.py``
replaces the asyncio-loop SIGINT handler with a no-op lambda the instant the FIRST
Ctrl+C is processed, so every subsequent Ctrl+C does nothing — the user literally
"CANNOT on multiple ctrl c even close the process AT ALL".

Because the race lives in core, the recorder cannot fix it directly. Instead this
watchdog provides an INDEPENDENT escape hatch that does NOT depend on the asyncio
loop making any progress: a daemon thread that, once shutdown is initiated, waits
a bounded grace period and then calls ``os._exit()`` regardless of whether the
loop is wedged. ``os._exit`` is deliberate — it bypasses atexit handlers, buffer
flushes, and the (possibly wedged) interpreter shutdown machinery, guaranteeing
termination.

Design constraints honored here:

- The watchdog does NOT fire on the first Ctrl+C. It only ARMS on the first
  shutdown signal; graceful shutdown still gets the full grace period to complete.
- It fires ONLY if the node has not finished disposing within ``grace_seconds``
  of shutdown being initiated. A clean shutdown disarms it (sets ``completed``),
  so the force-exit never runs on a healthy stop.
- The grace period is configurable (recorder-side config) with a sane default.

This module touches NO core files — it is pure recorder-side composition.
"""

import contextlib
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path


# Default grace period (seconds) between shutdown being initiated and the
# force-exit. Generous enough for a healthy graceful shutdown (kernel post-stop
# sleep + adapter disconnect + conversion offload all complete in a few seconds),
# but bounded so a wedged loop cannot hang the process indefinitely.
DEFAULT_GRACE_SECONDS: float = 20.0

# Exit code used by the force-exit path so an operator / systemd can distinguish a
# watchdog-forced kill from a clean exit (0) in journald.
WATCHDOG_EXIT_CODE: int = 75


class ShutdownWatchdog:
    """
    A daemon-thread force-exit safety valve for the recorder process (MEM-04).

    Lifecycle::

        watchdog = ShutdownWatchdog(grace_seconds=20.0)
        watchdog.start()  # daemon thread begins watching (idle until armed)
        ...  # node runs
        watchdog.arm()  # called from on_stop hook at FIRST Ctrl+C
        ...  # node.run() returns (clean) OR loop wedges
        watchdog.mark_completed()  # disarms — clean disposal happened in time
        watchdog.stop()  # join the thread

    If ``arm()`` is called and ``mark_completed()`` is NOT called within
    ``grace_seconds``, the watcher thread calls the injected ``exit_func``
    (default ``os._exit``) with ``WATCHDOG_EXIT_CODE``.

    Parameters
    ----------
    grace_seconds : float, default ``DEFAULT_GRACE_SECONDS``
        Seconds to wait after ``arm()`` before force-exiting, unless
        ``mark_completed()`` is called first.
    exit_func : callable, default ``os._exit``
        The force-exit callable (``(code: int) -> None``). Injected so the
        force-exit decision path is unit-testable without killing the test
        process.
    monotonic : callable, default ``time.monotonic``
        The clock source (injected for deterministic deadline tests).
    log : callable | None, default None
        Optional ``(str) -> None`` sink for the pre-exit warning line. The
        watchdog runs on its own thread and cannot rely on the (possibly wedged)
        asyncio-routed logger, so it writes a bare line via this sink (typically
        a stderr print) right before ``exit_func`` so the operator sees WHY the
        process was killed.
    record_file : str | Path | None, default None
        Optional path to a DURABLE on-disk record of the force-exit event. When
        the watchdog fires, it appends a single timestamped line to this file via
        a bare-stdlib direct write (open/write/flush/``os.fsync``/close) — NOT
        through the pyo3/Nautilus logging pipeline, which may itself be wedged at
        the exact moment of a force-exit. Unlike the ephemeral ``log`` sink
        (typically stderr, lost unless the process runs under journald), this file
        PERSISTS across process restarts so the operator can later correlate the
        force-exit timestamp with a likely data gap in the catalog. Use a DEDICATED
        sibling file (e.g. ``logs/dydx_recorder_watchdog.log``), NOT the file the
        pyo3 writer owns, to avoid file-handle contention with Nautilus's own log
        rotation/management. The write is wrapped in the same suppress-Exception
        guard as ``log`` so a write failure never blocks the ``exit_func`` kill.

    """

    def __init__(
        self,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        exit_func: Callable[[int], None] = os._exit,
        monotonic: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
        record_file: str | Path | None = None,
    ) -> None:
        if grace_seconds <= 0:
            raise ValueError(f"grace_seconds must be positive, was {grace_seconds}")
        self._grace_seconds = grace_seconds
        self._exit_func = exit_func
        self._monotonic = monotonic
        self._log = log
        self._record_file = Path(record_file) if record_file is not None else None

        # Set when shutdown is initiated (first Ctrl+C / on_stop hook). The watcher
        # thread blocks on this until armed, so it consumes no CPU while idle.
        self._armed = threading.Event()
        # Set when the node finished disposing cleanly (disarms the force-exit).
        self._completed = threading.Event()
        # Set to ask the watcher thread to exit (clean shutdown of the watchdog
        # itself, e.g. when the node disposed before ever being armed).
        self._stopping = threading.Event()

        # The monotonic deadline, computed at arm() time. None until armed.
        self._deadline: float | None = None

        self._thread = threading.Thread(
            target=self._run,
            name="recorder-shutdown-watchdog",
            daemon=True,
        )

    def start(self) -> None:
        """Start the daemon watcher thread (idle until ``arm()``)."""
        self._thread.start()

    def arm(self) -> None:
        """
        Arm the watchdog — called at the FIRST shutdown signal (on_stop hook).

        Idempotent: a second call (e.g. a second Ctrl+C, or both the signal
        handler and the on_stop hook firing) does NOT reset the deadline, so the
        grace period always counts from the FIRST shutdown signal.
        """
        if self._armed.is_set():
            return
        self._deadline = self._monotonic() + self._grace_seconds
        self._armed.set()

    def mark_completed(self) -> None:
        """
        Signal that the node disposed cleanly — disarms the force-exit.

        Called after ``node.run()`` returns and ``node.dispose()`` completes. Once
        set, the watcher thread will NOT force-exit even if it has not yet
        observed the deadline.
        """
        self._completed.set()

    def stop(self) -> None:
        """
        Stop the watcher thread (used when the node disposed cleanly).

        Sets the completion + stopping flags and joins the thread. Safe to call
        even if the watchdog was never armed.
        """
        self._completed.set()
        self._stopping.set()
        # Nudge the thread out of its pre-arm wait.
        self._armed.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def should_force_exit(self, now: float) -> bool:
        """
        Pure decision predicate: should the process be force-exited at ``now``?

        Factored out so the deadline logic is unit-testable without the thread or
        ``os._exit``. Returns True only when the watchdog is armed, the deadline
        has passed, and the node has NOT completed disposal.

        Parameters
        ----------
        now : float
            A monotonic timestamp (same clock source as ``arm()``).

        Returns
        -------
        bool

        """
        if not self._armed.is_set():
            return False
        if self._completed.is_set():
            return False
        if self._deadline is None:
            return False
        return now >= self._deadline

    def _run(self) -> None:
        """
        Watcher-thread body.

        Blocks until armed, then polls the completion flag with a deadline. If the
        grace period elapses without completion, force-exits the process.
        """
        # Idle (no CPU) until shutdown is initiated or the watchdog is stopped.
        self._armed.wait()

        if self._stopping.is_set() and not self._deadline:
            # stop() was called before any real arm — nothing to guard.
            return

        # Wait for clean completion, bounded by the grace deadline. ``Event.wait``
        # with a timeout returns False on timeout, True if completed.
        assert self._deadline is not None  # set by arm() before _armed.set()
        remaining = self._deadline - self._monotonic()
        completed_in_time = self._completed.wait(timeout=max(remaining, 0.0))

        if completed_in_time or not self.should_force_exit(self._monotonic()):
            return

        # The loop is wedged (core sentinel-loss race) — escape via os._exit. Emit
        # the diagnostic to BOTH sinks first; never let a sink error block the kill.
        message = self._force_exit_message()

        # Ephemeral stderr line (lost unless run under journald). The asyncio-routed
        # logger may be wedged too, so this is a bare write via the injected sink.
        if self._log is not None:
            with contextlib.suppress(Exception):
                self._log(message)

        # DURABLE on-disk record — persists across restarts so a force-exit (and the
        # data gap it implies) is traceable AFTER the fact, even when stderr is lost.
        self._write_durable_record(message)

        self._exit_func(WATCHDOG_EXIT_CODE)

    def _force_exit_message(self) -> str:
        """Build the diagnostic line written to BOTH the stderr sink and the file."""
        return (
            f"Shutdown watchdog: node did not dispose within "
            f"{self._grace_seconds:.0f}s of shutdown — force-exiting "
            f"(code {WATCHDOG_EXIT_CODE}). This indicates a wedged event "
            f"loop (see debug session: data-queue sentinel-loss race). The catalog "
            f"likely has a data gap starting around this time."
        )

    def _write_durable_record(self, message: str) -> None:
        """
        Append a timestamped force-exit line to ``record_file`` durably.

        Uses ONLY the stdlib (open/write/flush/``os.fsync``/close) — deliberately
        NOT the pyo3/Nautilus logging pipeline, which may itself be wedged at the
        exact moment of a force-exit (the same reason the stderr line is a bare
        ``print``). ``os.fsync`` flushes the OS buffer to the physical disk so the
        record survives even if the process is killed immediately after.

        Wrapped in ``contextlib.suppress(Exception)`` so a write failure (e.g. an
        unwritable path, a full disk) can NEVER block the ``exit_func`` kill — the
        whole point of the watchdog is guaranteed termination.
        """
        if self._record_file is None:
            return

        # Wall-clock ISO-8601 timestamp (UTC) so the operator can correlate the
        # force-exit with a catalog gap; monotonic time (used for the deadline) is
        # not wall-clock and would be useless for that correlation.
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = f"{timestamp} {message}\n"

        # Open in append mode so concurrent restarts never truncate the history.
        # Intentional bare-stdlib write: must NOT route through any framework helper
        # at kill time (the pyo3 pipeline may be wedged — the whole point here).
        with (
            contextlib.suppress(Exception),
            open(
                self._record_file,
                "a",
                encoding="utf-8",
            ) as f,
        ):
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
