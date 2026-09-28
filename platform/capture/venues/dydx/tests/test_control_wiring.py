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
`build_capture_from_file`, dYdX's composition root (Story 25.4): the plan comes through the one
loader, a file that breaks a plan invariant refuses start, and collection control's three loops
are wired into the capture service through `add_loops` (Story 26.2).
"""

import functools
from pathlib import Path

import pytest

from capture.application.capture_service import CaptureService
from capture.venues.dydx.__main__ import build_capture_from_file


_PLAN = """
network = "testnet"
catalog_path = "{catalog}"
exclude = ["BAD-USD-PERP.DYDX"]

[[instruments]]
id = "BTC-USD-PERP.DYDX"
store_order_book_deltas = true

[[instruments]]
id = "ETH-USD-PERP.DYDX"
"""


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "candles.db"))
    path = tmp_path / "config.toml"
    path.write_text(text.replace("{catalog}", str(tmp_path / "catalog")))
    return path


def _closed(collector: CaptureService) -> CaptureService:
    """Close the candle store the collector opened (no `run()` here to own its lifetime)."""
    collector._second_sink._store.close()  # type: ignore[union-attr]
    return collector


def test_the_plan_is_capture_s_plan_and_nothing_is_applied_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = _closed(build_capture_from_file(_config(tmp_path, monkeypatch, _PLAN)))
    assert collector._plan_ids == {"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}
    assert collector._delta_store == {"BTC-USD-PERP.DYDX"}
    assert collector.capture_status().pending == {"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}


def test_collection_controls_three_loops_are_extra_loops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = _closed(build_capture_from_file(_config(tmp_path, monkeypatch, _PLAN)))
    wired = {
        loop.func.__qualname__
        for loop in collector._extra_loops
        if isinstance(loop, functools.partial)
    }
    assert {"reload_loop", "StatusPublisher.loop", "ControlService.control_loop"} <= wired


def test_a_plan_over_the_cap_refuses_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = 'network = "testnet"\n' + "".join(
        f'[[instruments]]\nid = "C{i}-USD-PERP.DYDX"\n' for i in range(31)
    )
    with pytest.raises(ValueError, match="above its cap of 30"):
        build_capture_from_file(_config(tmp_path, monkeypatch, text))
