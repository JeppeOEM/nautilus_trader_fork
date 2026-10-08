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
`build_capture_from_file`, Hyperliquid's composition root: collection control's three loops -- plan
reload, `collector:status` (`accepts_commands` true, Story 29.2's publish) and `collector:control`
-- are wired into the capture service (Story 29.4), and a command addressed to HYPERLIQUID is saved to
the config file, applied through the real `CaptureService.apply` and published.
"""

import asyncio
import functools
import json
from pathlib import Path
from types import MethodType
from typing import Any
from typing import cast

import pytest
from collection_control.application.control import ControlService
from collection_control.application.reload import PLAN_RELOAD_SECONDS
from collection_control.application.status import PLAN_STATUS_SECONDS
from collection_control.application.status import StatusPublisher

from capture.application.capture_service import CaptureService
from capture.infrastructure.config import load_venue_config
from capture.venues.hyperliquid.__main__ import VENUE
from capture.venues.hyperliquid.__main__ import build_capture_from_file


_IDS = ("BTC-USD-PERP.HYPERLIQUID", "ETH-USD-PERP.HYPERLIQUID")
_NEW = "SOL-USD-PERP.HYPERLIQUID"
_PLAN = """
environment = "testnet"
catalog_path = "{catalog}"
instruments = {ids}
"""


class _Client:
    """Records the wire calls capture's `apply` makes (our own client contract)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def subscribe(self, iid: str) -> None:
        self.calls.append(f"subscribe {iid}")

    async def unsubscribe(self, iid: str) -> None:
        self.calls.append(f"unsubscribe {iid}")


class _Bus:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, message: str) -> None:
        self.published.append(message)

    async def aclose(self) -> None:
        pass


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "candles.db"))
    path = tmp_path / "config.toml"
    text = _PLAN.replace("{catalog}", str(tmp_path / "catalog"))
    path.write_text(text.replace("{ids}", json.dumps(list(_IDS))))
    return path


def _built(path: Path) -> CaptureService:
    collector = build_capture_from_file(path)
    # No `run()` here to own the candle store's lifetime.
    collector._second_sink._store.close()  # type: ignore[union-attr]
    return collector


def _wired(collector: CaptureService) -> dict[str, functools.partial]:
    return {
        loop.func.__qualname__: loop
        for loop in collector._extra_loops
        if isinstance(loop, functools.partial)
    }


def _owner(loop: functools.partial) -> Any:
    return cast(MethodType, loop.func).__self__


def test_collection_controls_three_loops_are_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wired = _wired(_built(_config(tmp_path, monkeypatch)))
    assert {"reload_loop", "StatusPublisher.loop", "ControlService.control_loop"} <= set(wired)
    control, interval = wired["reload_loop"].args
    assert isinstance(control, ControlService)
    assert interval == PLAN_RELOAD_SECONDS


def test_the_plan_publishes_status_accepting_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_loop = _wired(_built(_config(tmp_path, monkeypatch)))["StatusPublisher.loop"]
    publisher = _owner(status_loop)
    assert isinstance(publisher, StatusPublisher)
    current, interval = status_loop.args
    assert current().collected == _IDS
    assert interval == PLAN_STATUS_SECONDS
    assert publisher._accepts_commands is True
    assert publisher._markets is None


def _rigged(path: Path) -> tuple[ControlService, _Client, _Bus]:
    collector = _built(path)
    wired = _wired(collector)
    client, bus = _Client(), _Bus()
    collector._client = client
    collector._listed = frozenset((*_IDS, _NEW))  # as `run()`'s `fetch_instruments` leaves it
    _owner(wired["StatusPublisher.loop"])._bus = bus
    return _owner(wired["ControlService.control_loop"]), client, bus


def test_a_start_addressed_to_hyperliquid_is_saved_applied_and_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _config(tmp_path, monkeypatch)
    control, client, bus = _rigged(path)
    message = json.dumps({"action": "start", "id": _NEW, "venue": VENUE})
    asyncio.run(control._handle_message(message))
    assert load_venue_config(path, VENUE)[1].collected == (*_IDS, _NEW)
    assert client.calls == [f"subscribe {_NEW}"]
    aggregate = json.loads(bus.published[-1])
    assert (aggregate["cap"], aggregate["accepts_commands"]) == (None, True)
    assert aggregate["last_apply"]["subscribed"] == [_NEW]


def test_a_legacy_message_without_a_venue_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _config(tmp_path, monkeypatch)
    before = path.read_text()
    control, client, bus = _rigged(path)
    asyncio.run(control._handle_message(json.dumps({"action": "start", "id": _NEW})))
    assert (path.read_text(), client.calls, bus.published) == (before, [], [])
