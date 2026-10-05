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
Tests for bot_tui.app's quit drain, `_shutdown_tasks` (DW-58): a real fresh event loop and plain
asyncio tasks stand in for the listeners, the redraw loop and the background publishes.
"""

import asyncio
import time
from collections.abc import Coroutine

import pytest

from bot_tui import app as app_module


def _forever() -> Coroutine[object, object, None]:
    return asyncio.sleep(3600)


def _ledger(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    recorded: list[tuple[str, str]] = []

    def _record(site: str, detail: str = "", exc: BaseException | None = None) -> None:
        recorded.append((site, detail if exc is None else f"{detail}: {exc!r}"))

    monkeypatch.setattr(app_module.error_ledger, "record", _record)
    return recorded


def test_quit_cancels_and_awaits_every_listener_and_closes_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()
    tasks = [loop.create_task(_forever()) for _ in range(3)]

    app_module._shutdown_tasks(loop, tasks, set())

    assert all(task.done() and task.cancelled() for task in tasks)
    assert loop.is_closed()
    assert recorded == []


def test_a_publish_in_flight_gets_the_grace_to_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()
    sent: list[str] = []

    async def _publish() -> None:
        await asyncio.sleep(0.01)
        sent.append("stop bot-01")

    publish = loop.create_task(_publish())

    app_module._shutdown_tasks(loop, [loop.create_task(_forever())], {publish})

    assert sent == ["stop bot-01"]
    assert not publish.cancelled()
    assert recorded == []


def test_a_publish_still_pending_after_the_grace_is_cancelled_and_ledgered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()
    publish = loop.create_task(_forever(), name="bots:control stop bot-01")

    app_module._shutdown_tasks(loop, [], {publish})

    assert publish.cancelled()
    assert [site for site, _detail in recorded] == [app_module._SHUTDOWN_CANCELLED_SITE]
    assert "bots:control stop bot-01" in recorded[0][1]
    assert loop.is_closed()


def test_a_finished_background_task_is_not_ledgered(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()
    done = loop.create_task(asyncio.sleep(0))
    loop.run_until_complete(done)

    app_module._shutdown_tasks(loop, [], {done})

    assert recorded == []
    assert loop.is_closed()


def test_a_task_that_failed_is_ledgered_with_its_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()

    async def _fail() -> None:
        raise ConnectionError("redis down")

    failed = loop.create_task(_fail(), name="bots:control stop bot-01")
    loop.run_until_complete(asyncio.wait([failed]))

    app_module._shutdown_tasks(loop, [], {failed})

    assert recorded == [
        (
            app_module._SHUTDOWN_FAILED_SITE,
            "bots:control stop bot-01 failed: ConnectionError('redis down')",
        )
    ]
    assert loop.is_closed()


def test_a_publish_ignoring_its_cancel_is_ledgered_once_as_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()

    async def _stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                continue

    publish = loop.create_task(_stubborn(), name="bots:control stop bot-01")

    app_module._shutdown_tasks(loop, [], {publish})

    assert [site for site, _detail in recorded] == [app_module._SHUTDOWN_STUCK_SITE]
    assert loop.is_closed()


def test_a_busy_default_executor_does_not_hold_the_quit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()
    loop.run_in_executor(None, time.sleep, 0.3)

    started = time.monotonic()
    app_module._shutdown_tasks(loop, [], set())

    assert time.monotonic() - started < 0.25
    assert [site for site, _detail in recorded] == [app_module._SHUTDOWN_STUCK_SITE]
    assert "default executor" in recorded[0][1]
    assert loop.is_closed()


def test_a_stopping_exception_handler_left_installed_does_not_abort_the_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # urwid's AsyncioEventLoop.run() leaves a handler installed that stops the loop on any
    # reported exception; a done-callback failing mid-drain must not cut the drain short.
    recorded = _ledger(monkeypatch)
    loop = asyncio.new_event_loop()
    loop.set_exception_handler(lambda running, context: running.stop())

    async def _publish() -> None:
        await asyncio.sleep(0.01)

    def _raising_callback(_: asyncio.Future) -> None:
        raise ValueError("callback bug")

    publish = loop.create_task(_publish())
    publish.add_done_callback(_raising_callback)
    listener = loop.create_task(_forever())

    app_module._shutdown_tasks(loop, [listener], {publish})

    assert listener.cancelled()
    assert publish.done()
    assert not publish.cancelled()
    assert loop.is_closed()
    assert recorded == []
