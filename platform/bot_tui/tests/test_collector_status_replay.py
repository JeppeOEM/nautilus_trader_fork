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
"""

import json
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _fresh_collector_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_state, "_LATEST_COLLECTOR_STATUS", {})
    monkeypatch.setattr(collector_state, "_LATEST_RECEIVED_AT", {})
    monkeypatch.setattr(collector_state, "_LATEST_UNPINNED_IDS", [])


def _replay(*messages: str) -> list[str]:
    for message in messages:
        collector_state._handle_status_message(json.loads(message))
    rows = collector_pane.collector_rows(collector_state._LATEST_COLLECTOR_STATUS)
    return [collector_pane.format_collector_line(row, stale=False) for row in rows]


def test_the_recorded_status_renders_every_row_and_the_unpinned_ids() -> None:
    assert _replay(*_RECORDED) == [
        "  BTC-USD-PERP.DYDX      liquid  ",
        "  ETH-USD-PERP.DYDX      illiquid",
        "  SOL-USD-PERP.DYDX      liquid  ",
    ]
    assert collector_state._LATEST_UNPINNED_IDS == ["AAA-USD-PERP.DYDX", "ZZZ-USD-PERP.DYDX"]


def test_the_recorded_tombstone_drops_its_row() -> None:
    lines = _replay(*_RECORDED, _TOMBSTONE)
    assert [line.split()[0] for line in lines] == ["BTC-USD-PERP.DYDX", "SOL-USD-PERP.DYDX"]


def test_a_pending_row_is_read_and_rendered_like_any_other() -> None:
    lines = _replay(*_RECORDED, _PENDING)
    assert "  NEW-USD-PERP.DYDX      illiquid" in lines
    assert collector_state._LATEST_COLLECTOR_STATUS["NEW-USD-PERP.DYDX"]["pending"] is True


def test_the_inlined_strings_are_the_recording() -> None:
    """The inlined copy cannot drift from the fixture the publisher is proven against."""
    fixture = Path(__file__).parents[2] / "collection_control/tests/fixtures/status_payloads.json"
    published = [message for _channel, message in json.loads(fixture.read_text())["published"]]
    assert published == [*_RECORDED, _TOMBSTONE]
