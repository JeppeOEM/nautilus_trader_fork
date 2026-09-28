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
Replay of `collector:status` as the collector publishes it (Story 25.4): the strings recorded from
the pre-move `DydxCollector._publish_status`/`_publish_removed` (`collection_control/tests/fixtures/
status_payloads.json`, inlined here: `bot_tui` imports no collector context) plus the one addition,
a pending row, go through the TUI's own reader and renderer unchanged -- `bot_tui` needs no change
for the control plane's move.

Story 29.2 appends the plan facts to the aggregate (`_AGGREGATE`); the recorded one without them
still reads as dYdX's. `publish_control` stays byte-identical to its recording
(`collection_control/tests/fixtures/control_payloads.json`).
"""

import asyncio
import json
from pathlib import Path
from typing import Self

import pytest

from bot_tui import collector_pane
from bot_tui import collector_state


_RECORDED = [
    '{"id": "BTC-USD-PERP.DYDX", "liquid": true, "last_trade_ts": 1758888000123456789, '
    '"trade_backfill": 0}',
    '{"id": "ETH-USD-PERP.DYDX", "liquid": false, "last_trade_ts": 1758888001000000000, '
    '"trade_backfill": 17}',
    '{"id": "SOL-USD-PERP.DYDX", "liquid": true, "last_trade_ts": 0, "trade_backfill": 0}',
    '{"unpinned_ids": ["AAA-USD-PERP.DYDX", "ZZZ-USD-PERP.DYDX"]}',
]
_TOMBSTONE = '{"id": "ETH-USD-PERP.DYDX", "removed": true}'
# Story 25.4's one addition: a planned instrument capture has not applied (yet).
_PENDING = (
    '{"id": "NEW-USD-PERP.DYDX", "liquid": false, "last_trade_ts": 0, "trade_backfill": 0, '
    '"pending": true}'
)
# Story 29.2's aggregate: the recorded one's bytes, then the appended plan facts.
_AGGREGATE = _RECORDED[3].removesuffix("}") + (
    ', "venue": "DYDX", "cap": 30, "accepts_commands": true, "min_liquidity_usd": 20000.0, '
    '"last_apply": {"ts": 1758888000000000000, "subscribed": ["SOL-USD-PERP.DYDX"], '
    '"unsubscribed": [], "failed": []}}'
)
_BYBIT_AGGREGATE = (
    '{"unpinned_ids": [], "venue": "BYBIT", "cap": 4, "accepts_commands": false, '
    '"min_liquidity_usd": null, "last_apply": null}'
)
_CONTROL_FIXTURE = Path(__file__).parents[2] / (
    "collection_control/tests/fixtures/control_payloads.json"
)


def _replay(*messages: str) -> list[str]:
    for message in messages:
        collector_state._handle_status_message(json.loads(message))
    rows = collector_pane.collector_rows(collector_state._LATEST_COLLECTOR_STATUS)
    return [collector_pane.format_collector_line(row, stale=False) for row in rows]


def test_the_recorded_status_renders_every_row_and_the_unpinned_ids() -> None:
    assert _replay(*_RECORDED) == [
        "  BTC-USD-PERP.DYDX            liquid  ",
        "  ETH-USD-PERP.DYDX            illiquid",
        "  SOL-USD-PERP.DYDX            liquid  ",
    ]
    # The recorded aggregate has no `venue`: it is dYdX's, and dYdX keeps taking commands.
    dydx_plan = collector_state._LATEST_PLANS["DYDX"]
    assert dydx_plan["unpinned_ids"] == ["AAA-USD-PERP.DYDX", "ZZZ-USD-PERP.DYDX"]
    assert collector_state.command_refusal("DYDX") is None
    assert collector_state.plan_cap("DYDX") is None


def test_the_29_2_aggregate_is_kept_per_venue() -> None:
    _replay(*_RECORDED[:3], _AGGREGATE, _BYBIT_AGGREGATE)
    assert set(collector_state._LATEST_PLANS) == {"DYDX", "BYBIT"}
    assert collector_state._LATEST_PLANS["DYDX"]["unpinned_ids"] == [
        "AAA-USD-PERP.DYDX",
        "ZZZ-USD-PERP.DYDX",
    ]
    assert collector_state.plan_cap("DYDX") == 30
    assert collector_state.command_refusal("DYDX") is None
    assert collector_state.command_refusal("BYBIT") == (
        "BYBIT: static plan: edit platform/capture/venues/bybit/config.toml"
    )
    apply_line = collector_pane.format_last_apply_line(collector_state._LATEST_PLANS["DYDX"])
    assert apply_line.endswith("1 subscribed, 0 unsubscribed, 0 failed")


def test_the_recorded_tombstone_drops_its_row() -> None:
    lines = _replay(*_RECORDED, _TOMBSTONE)
    assert [line.split()[0] for line in lines] == ["BTC-USD-PERP.DYDX", "SOL-USD-PERP.DYDX"]


def test_a_pending_row_is_read_and_rendered_with_its_pending_marker() -> None:
    lines = _replay(*_RECORDED, _PENDING)
    assert "  NEW-USD-PERP.DYDX            illiquid pending" in lines
    assert collector_state._LATEST_COLLECTOR_STATUS["NEW-USD-PERP.DYDX"]["pending"] is True


def test_publish_control_is_byte_identical_to_the_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = json.loads(_CONTROL_FIXTURE.read_text())
    published: list[list[str]] = []

    class _Client:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def publish(self, channel: str, message: str) -> None:
            published.append([channel, message])

    monkeypatch.setattr(collector_state.aioredis.Redis, "from_url", lambda *_a, **_k: _Client())

    async def _send() -> None:
        for action, instrument_id in recording["calls"]:
            await collector_state.publish_control("redis://unused", action, instrument_id)

    asyncio.run(_send())
    assert published == recording["published"]


def test_the_inlined_strings_are_the_recording() -> None:
    """The inlined copy cannot drift from the fixture the publisher is proven against."""
    fixture = Path(__file__).parents[2] / "collection_control/tests/fixtures/status_payloads.json"
    published = [message for _channel, message in json.loads(fixture.read_text())["published"]]
    assert published == [*_RECORDED, _TOMBSTONE]
