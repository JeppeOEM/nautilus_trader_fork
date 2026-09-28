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
`build_capture_from_file`, Hyperliquid's composition root (Story 29.2): the static plan publishes
`collector:status` through `StatusPublisher.loop`, read-only (`accepts_commands` false), and no
control plane is wired -- no `ControlService`, no plan reload (Story 29.4 adds them).
"""

import functools
import json
from pathlib import Path
from types import MethodType
from typing import cast

import pytest
from collection_control.application.status import STATIC_PLAN_STATUS_SECONDS
from collection_control.application.status import StatusPublisher

from capture.application.capture_service import CaptureService
from capture.venues.hyperliquid.__main__ import build_capture_from_file


_IDS = ("BTC-USD-PERP.HYPERLIQUID", "ETH-USD-PERP.HYPERLIQUID")
_PLAN = """
environment = "testnet"
catalog_path = "{catalog}"
instruments = {ids}
"""


def _built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CaptureService:
    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "candles.db"))
    path = tmp_path / "config.toml"
    text = _PLAN.replace("{catalog}", str(tmp_path / "catalog"))
    path.write_text(text.replace("{ids}", json.dumps(list(_IDS))))
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


def test_the_static_plan_publishes_status_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_loop = _wired(_built(tmp_path, monkeypatch))["StatusPublisher.loop"]
    publisher = cast(MethodType, status_loop.func).__self__
    assert isinstance(publisher, StatusPublisher)
    current, interval = status_loop.args
    assert current().collected == _IDS
    assert interval == STATIC_PLAN_STATUS_SECONDS
    assert publisher._accepts_commands is False
    assert publisher._markets is None


def test_no_control_plane_is_wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wired = _wired(_built(tmp_path, monkeypatch))
    assert "ControlService.control_loop" not in wired
    assert "reload_loop" not in wired
