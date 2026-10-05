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
"""DW-3/DW-234: `RankingsBus`'s throttled malformed-payload warning and heartbeat-silence resubscribe."""

import logging

import pytest

from views import rankings_bus
from views.rankings_bus import RankingsBus


class _Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _FakePubSub:
    """Each poll waits out its timeout on the fake clock, then yields the next scripted message."""

    def __init__(self, clock: _Clock, script: list[str | None]) -> None:
        self._clock = clock
        self._script = list(script)

    async def get_message(self, *, timeout: float, **_: object) -> dict | None:
        self._clock.now += timeout
        data = self._script.pop(0) if self._script else None
        return None if data is None else {"type": "message", "data": data}


def _valid() -> str:
    return '{"mode": "volatility", "updated_at": 1, "ranks": []}'


def _clock(monkeypatch: pytest.MonkeyPatch, start: float = 0.0) -> _Clock:
    clock = _Clock(start)
    monkeypatch.setattr(rankings_bus.time, "monotonic", clock)
    return clock


def test_malformed_payloads_are_counted_and_the_warning_is_rate_limited(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _clock(monkeypatch, start=1000.0)
    bus = RankingsBus()
    with caplog.at_level(logging.WARNING, logger=rankings_bus.logger.name):
        for _ in range(100):
            bus.handle_message({"bad": 1})
        assert bus.malformed_count == 100
        assert len(caplog.records) == 1
        clock.now += rankings_bus.MALFORMED_LOG_INTERVAL_SECONDS
        bus.handle_message("junk")
    assert len(caplog.records) == 2
    assert "99 more malformed suppressed" in caplog.records[1].getMessage()
    assert bus.latest is None


@pytest.mark.asyncio
async def test_suppressed_tail_is_flushed_once_its_window_closes(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _clock(monkeypatch)
    bus = RankingsBus()
    script: list[str | None] = ["not json"] * 3
    with (
        caplog.at_level(logging.WARNING, logger=rankings_bus.logger.name),
        pytest.raises(ConnectionError),
    ):
        await bus._receive(_FakePubSub(clock, script))  # type: ignore[arg-type]
    messages = [record.getMessage() for record in caplog.records]
    assert bus.malformed_count == 3
    assert any(m.startswith("2 more malformed rankings:live messages suppressed") for m in messages)


@pytest.mark.asyncio
async def test_silence_raises_once_the_window_has_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    with pytest.raises(ConnectionError):
        await RankingsBus()._receive(_FakePubSub(clock, []))  # type: ignore[arg-type]
    assert clock.now > rankings_bus.SILENCE_RESUBSCRIBE_SECONDS
    assert clock.now <= rankings_bus.SILENCE_RESUBSCRIBE_SECONDS + rankings_bus._POLL_SECONDS


@pytest.mark.asyncio
async def test_a_steady_stream_keeps_the_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    polls = 3 * int(rankings_bus.SILENCE_RESUBSCRIBE_SECONDS / rankings_bus._POLL_SECONDS)
    bus = RankingsBus()
    with pytest.raises(ConnectionError):
        await bus._receive(_FakePubSub(clock, [_valid()] * polls))  # type: ignore[arg-type]
    last_heard = polls * rankings_bus._POLL_SECONDS
    assert clock.now > last_heard + rankings_bus.SILENCE_RESUBSCRIBE_SECONDS
    assert bus.latest == {"mode": "volatility", "updated_at": 1, "ranks": []}


@pytest.mark.asyncio
async def test_unparseable_message_is_counted_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    bus = RankingsBus()
    with pytest.raises(ConnectionError):
        await bus._receive(_FakePubSub(clock, ["not json", _valid()]))  # type: ignore[arg-type]
    assert bus.malformed_count == 1
    assert bus.latest == {"mode": "volatility", "updated_at": 1, "ranks": []}
