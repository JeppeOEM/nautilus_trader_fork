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
"""The shared heartbeat-silence receive loop: raise on silence, refresh only on a "message"."""

import time
from collections.abc import Callable
from typing import Any

import pytest

from observability.pubsub_liveness import receive_until_silent


_CHANNEL = "test:channel"
_SILENCE = 30.0
_POLL = 5.0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _FakePubSub:
    """Each poll waits out its timeout on the fake clock, then yields the next scripted frame."""

    def __init__(self, clock: _Clock, script: list[dict[str, Any] | None]) -> None:
        self._clock = clock
        self._script = list(script)
        self.polls = 0

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float) -> Any:
        self.polls += 1
        self._clock.now += timeout
        return self._script.pop(0) if self._script else None


def _clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    return clock


def _message(data: str) -> dict[str, Any]:
    return {"type": "message", "data": data}


async def _run(
    pubsub: _FakePubSub, ingested: list[str], after_poll: Callable[[], None] | None = None
) -> ConnectionError:
    with pytest.raises(ConnectionError) as raised:
        await receive_until_silent(
            pubsub, _CHANNEL, _SILENCE, ingested.append, poll_seconds=_POLL, after_poll=after_poll
        )
    return raised.value


@pytest.mark.asyncio
async def test_silence_raises_only_after_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    error = await _run(_FakePubSub(clock, []), [])
    assert clock.now > _SILENCE
    assert clock.now <= _SILENCE + _POLL
    assert str(error) == "no test:channel message for 30s"


@pytest.mark.asyncio
async def test_a_message_is_ingested_and_restarts_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _clock(monkeypatch)
    ingested: list[str] = []
    script: list[dict[str, Any] | None] = [None] * 5 + [_message("a")]
    await _run(_FakePubSub(clock, script), ingested)
    last_heard = 6 * _POLL
    assert ingested == ["a"]
    assert clock.now > last_heard + _SILENCE
    assert clock.now <= last_heard + _SILENCE + _POLL


@pytest.mark.asyncio
async def test_a_message_after_the_threshold_is_ingested_not_raised_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _clock(monkeypatch)
    ingested: list[str] = []
    # Poll 7 (t=35) is past the window, but it brought a message: ingest it, start a new window.
    script: list[dict[str, Any] | None] = [None] * 6 + [_message("late")]
    await _run(_FakePubSub(clock, script), ingested)
    assert ingested == ["late"]
    assert clock.now == 7 * _POLL + _SILENCE + _POLL


@pytest.mark.asyncio
async def test_a_non_message_frame_is_not_ingested_and_does_not_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _clock(monkeypatch)
    ingested: list[str] = []
    script: list[dict[str, Any] | None] = [None] * 5 + [{"type": "pong", "data": "x"}]
    await _run(_FakePubSub(clock, script), ingested)
    assert ingested == []
    assert clock.now <= _SILENCE + _POLL


@pytest.mark.asyncio
async def test_after_poll_runs_once_per_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    pubsub = _FakePubSub(clock, [_message("a"), None])
    calls: list[int] = []
    await _run(pubsub, [], after_poll=lambda: calls.append(pubsub.polls))
    assert calls == list(range(1, pubsub.polls + 1))
