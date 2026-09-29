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
"""`WireChannels` (Story 29.4): one held reference per channel, the undo on failure, pacing."""

import asyncio
import itertools

import pytest

from capture.application.wire_channels import Send
from capture.application.wire_channels import WireChannels


_KEY = ("trades", "BTCUSDT-LINEAR.BYBIT")


def _sender(log: list[str], name: str, *, fail: bool = False) -> Send:
    async def send() -> None:
        log.append(name)
        if fail:
            raise RuntimeError(f"{name} failed")

    return send


def _wire(fps: float = 1_000_000.0) -> WireChannels:
    return WireChannels(fps)


def test_a_repeated_hold_sends_once_and_a_release_sends_once() -> None:
    wire, log = _wire(), list[str]()

    async def run() -> None:
        await wire.hold(_KEY, _sender(log, "sub"))
        await wire.hold(_KEY, _sender(log, "sub"))
        await wire.release(_KEY, _sender(log, "unsub"))
        await wire.release(_KEY, _sender(log, "unsub"))

    asyncio.run(run())
    assert log == ["sub", "unsub"]


def test_a_release_of_a_never_held_channel_sends_nothing() -> None:
    wire, log = _wire(), list[str]()
    asyncio.run(wire.release(_KEY, _sender(log, "unsub")))
    assert log == []


def test_a_failed_hold_runs_its_undo_stays_unheld_and_a_retry_sends_again() -> None:
    wire, log = _wire(), list[str]()
    with pytest.raises(RuntimeError, match="sub failed"):
        asyncio.run(wire.hold(_KEY, _sender(log, "sub", fail=True), _sender(log, "undo")))
    assert not wire.is_held(_KEY)
    asyncio.run(wire.hold(_KEY, _sender(log, "sub"), _sender(log, "undo")))
    assert log == ["sub", "undo", "sub"]
    assert wire.is_held(_KEY)


def test_a_failed_hold_without_an_undo_only_re_raises() -> None:
    wire, log = _wire(), list[str]()
    with pytest.raises(RuntimeError):
        asyncio.run(wire.hold(_KEY, _sender(log, "sub", fail=True)))
    assert (log, wire.is_held(_KEY)) == (["sub"], False)


def test_a_failed_release_runs_its_undo_and_stays_held_so_a_retry_sends_again() -> None:
    wire, log = _wire(), list[str]()
    asyncio.run(wire.hold(_KEY, _sender(log, "sub")))
    with pytest.raises(RuntimeError, match="unsub failed"):
        asyncio.run(wire.release(_KEY, _sender(log, "unsub", fail=True), _sender(log, "resub")))
    assert wire.is_held(_KEY)
    asyncio.run(wire.release(_KEY, _sender(log, "unsub")))
    assert log == ["sub", "unsub", "resub", "unsub"]
    assert not wire.is_held(_KEY)


def test_a_failing_undo_is_suppressed_and_the_original_failure_raised() -> None:
    wire, log = _wire(), list[str]()
    with pytest.raises(RuntimeError, match="sub failed"):
        asyncio.run(
            wire.hold(_KEY, _sender(log, "sub", fail=True), _sender(log, "undo", fail=True))
        )
    assert (log, wire.is_held(_KEY)) == (["sub", "undo"], False)


def test_a_cancellation_during_the_pacing_wait_sends_and_records_nothing() -> None:
    wire, log = _wire(fps=1.0), list[str]()  # the second call waits ~1 s

    async def run() -> None:
        await wire.hold(("trades", "a"), _sender(log, "a"))
        task = asyncio.create_task(wire.hold(_KEY, _sender(log, "b")))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert (log, wire.is_held(_KEY)) == (["a"], False)


def test_a_cancellation_after_the_send_started_records_the_key() -> None:
    wire = _wire()

    async def run() -> None:
        started = asyncio.Event()

        async def send() -> None:
            started.set()
            await asyncio.sleep(10)

        task = asyncio.create_task(wire.hold(_KEY, send))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert wire.is_held(_KEY)


def test_consecutive_sends_are_spaced_by_the_frame_rate() -> None:
    wire = _wire(fps=50.0)  # 20 ms apart
    starts: list[float] = []

    async def run() -> None:
        loop = asyncio.get_running_loop()

        async def send() -> None:
            starts.append(loop.time())

        await asyncio.gather(*(wire.hold(("trades", str(i)), send) for i in range(4)))

    asyncio.run(run())
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert len(starts) == 4
    assert min(gaps) >= 0.02 - 1e-3


def test_the_send_is_built_only_after_the_pacer_wait() -> None:
    # A pyo3 call starts its Rust future when called, so the call itself must be paced.
    wire = _wire(fps=20.0)  # 50 ms apart
    called_at: list[float] = []

    async def run() -> None:
        loop = asyncio.get_running_loop()

        def build() -> asyncio.Future[None]:
            called_at.append(loop.time())
            future: asyncio.Future[None] = loop.create_future()
            future.set_result(None)
            return future

        await wire.hold(("trades", "a"), build)
        await wire.hold(("trades", "b"), build)

    asyncio.run(run())
    assert called_at[1] - called_at[0] >= 0.05 - 1e-3


def test_a_non_positive_frame_rate_is_refused() -> None:
    with pytest.raises(ValueError, match="frames_per_second"):
        WireChannels(0.0)
