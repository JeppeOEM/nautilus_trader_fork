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
"""DW-234: the collector:status listener resubscribes after heartbeat silence."""

import logging

import pytest

from bot_tui import collector_state


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _FakePubSub:
    """Each poll waits out its timeout on the fake clock, then yields the next scripted message."""

    def __init__(self, clock: _Clock, script: list[str]) -> None:
        self._clock = clock
        self._script = list(script)

    async def get_message(self, *, timeout: float, **_: object) -> dict | None:
        self._clock.now += timeout
        return {"type": "message", "data": self._script.pop(0)} if self._script else None


def _clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(collector_state.time, "monotonic", clock)
    return clock


@pytest.mark.asyncio
async def test_silence_raises_once_the_window_has_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    with pytest.raises(ConnectionError):
        await collector_state._receive(_FakePubSub(clock, []))  # type: ignore[arg-type]
    window = collector_state._SILENCE_RESUBSCRIBE_SECONDS
    assert window < clock.now <= window + collector_state._POLL_SECONDS


@pytest.mark.asyncio
async def test_a_message_restarts_the_silence_window(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    # Any message proves the connection alive, even one rejected without touching module state.
    script = ["not json", '{"unpinned_ids": "not a list"}']
    with pytest.raises(ConnectionError):
        await collector_state._receive(_FakePubSub(clock, script))  # type: ignore[arg-type]
    last_heard = 2 * collector_state._POLL_SECONDS
    assert clock.now > last_heard + collector_state._SILENCE_RESUBSCRIBE_SECONDS


def test_ingest_logs_a_malformed_message_and_keeps_going(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=collector_state.logger.name):
        collector_state._ingest("not json")
    assert "parse/ingest error" in caplog.text
