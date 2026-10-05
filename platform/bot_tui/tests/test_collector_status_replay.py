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
(`collection_control/tests/fixtures/control_payloads.json`), and Story 29.4's `venue` is appended
after those bytes. Story 29.5 appends `last_refusal` after `last_apply` (`_AGGREGATE_29_5`).
"""

import asyncio
import itertools
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


def test_publish_control_appends_the_venue_after_the_recorded_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 29.4: `venue` is appended last, so the recorded `{action, id}` is the prefix."""
    recording = json.loads(_CONTROL_FIXTURE.read_text())
    published: list[str] = []

    class _Client:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def publish(self, _channel: str, message: str) -> None:
            published.append(message)

    monkeypatch.setattr(collector_state.aioredis.Redis, "from_url", lambda *_a, **_k: _Client())

    async def _send() -> None:
        for action, instrument_id in recording["calls"]:
            await collector_state.publish_control("redis://unused", action, instrument_id, "DYDX")

    asyncio.run(_send())
    recorded = [message for _channel, message in recording["published"]]
    assert published == [m.removesuffix("}") + ', "venue": "DYDX"}' for m in recorded]


def test_the_inlined_strings_are_the_recording() -> None:
    """The inlined copy cannot drift from the fixture the publisher is proven against."""
    fixture = Path(__file__).parents[2] / "collection_control/tests/fixtures/status_payloads.json"
    published = [message for _channel, message in json.loads(fixture.read_text())["published"]]
    assert published == [*_RECORDED, _TOMBSTONE]


# Story 29.5's aggregate: the 29.2 one's bytes, then `last_refusal`.
_REFUSAL = (
    '{"ts": 1758888005000000000, "action": "start", "id": "NEW-USD-PERP.DYDX", '
    '"reason": "Cannot start NEW-USD-PERP.DYDX: at 30-instrument cap"}'
)
_AGGREGATE_29_5 = _AGGREGATE.removesuffix("}") + f', "last_refusal": {_REFUSAL}}}'


def test_the_29_5_aggregate_keeps_its_refusal_stamped_once_on_arrival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = itertools.count(100.0)
    monkeypatch.setattr(collector_state.time, "monotonic", lambda: next(clock))
    _replay(*_RECORDED[:3], _AGGREGATE_29_5)
    first = collector_state.latest_refusal("DYDX")
    _replay(*_RECORDED[:3], _AGGREGATE_29_5)  # every publish repeats it: not a new refusal
    assert collector_state.latest_refusal("DYDX") == first
    assert first is not None
    assert first[0] == json.loads(_REFUSAL)
    assert collector_pane.format_last_refusal_line(collector_state._LATEST_PLANS["DYDX"]) == (
        "last refusal 2025-09-26 12:00:05Z: start NEW-USD-PERP.DYDX: "
        "Cannot start NEW-USD-PERP.DYDX: at 30-instrument cap"
    )


def test_a_null_refusal_after_a_restart_clears_the_kept_one() -> None:
    _replay(*_RECORDED[:3], _AGGREGATE_29_5)
    _replay(*_RECORDED[:3], _AGGREGATE.removesuffix("}") + ', "last_refusal": null}')
    assert collector_state.latest_refusal("DYDX") is None
    assert collector_pane.format_last_refusal_line(collector_state._LATEST_PLANS["DYDX"]) == ""


def test_a_row_answers_this_tui_s_sent_add() -> None:
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 1.0)
    _replay(_PENDING)
    assert collector_state.sent_add_at("NEW-USD-PERP.DYDX") is None


def _aggregate_refusing(action: str, iid: str, reason: str, ts: int = 1) -> str:
    refusal = json.dumps({"ts": ts, "action": action, "id": iid, "reason": reason})
    return _AGGREGATE.removesuffix("}") + f', "last_refusal": {refusal}}}'


def test_a_new_refusal_of_a_sent_add_is_recorded_as_its_answer() -> None:
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 1.0)
    _replay(_aggregate_refusing("start", "NEW-USD-PERP.DYDX", "Cannot start: at cap"))
    assert collector_state.add_refused_reason("NEW-USD-PERP.DYDX") == "Cannot start: at cap"


def test_a_refusal_kept_before_the_add_was_sent_does_not_answer_it() -> None:
    _replay(_aggregate_refusing("start", "NEW-USD-PERP.DYDX", "old refusal"))
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 1.0)
    _replay(_aggregate_refusing("start", "NEW-USD-PERP.DYDX", "old refusal"))  # republished
    assert collector_state.add_refused_reason("NEW-USD-PERP.DYDX") is None


def test_a_refused_stop_or_another_id_does_not_answer_the_add() -> None:
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 1.0)
    _replay(_aggregate_refusing("stop", "NEW-USD-PERP.DYDX", "Cannot stop: not collected"))
    _replay(_aggregate_refusing("start", "BTC-USD-PERP.DYDX", "Cannot start: taken", ts=2))
    assert collector_state.add_refused_reason("NEW-USD-PERP.DYDX") is None


def test_a_later_refusal_or_a_restart_keeps_the_add_s_answer() -> None:
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 1.0)
    collector_state.record_sent_add("OTHER-USD-PERP.DYDX", 1.0)
    _replay(_aggregate_refusing("start", "NEW-USD-PERP.DYDX", "Cannot start: at cap"))
    _replay(_aggregate_refusing("start", "OTHER-USD-PERP.DYDX", "Cannot start: at cap", ts=2))
    _replay(_AGGREGATE.removesuffix("}") + ', "last_refusal": null}')  # a restarted collector
    assert collector_state.add_refused_reason("NEW-USD-PERP.DYDX") == "Cannot start: at cap"
    assert collector_state.add_refused_reason("OTHER-USD-PERP.DYDX") == "Cannot start: at cap"


def test_a_re_sent_add_drops_the_earlier_answer() -> None:
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 1.0)
    _replay(_aggregate_refusing("start", "NEW-USD-PERP.DYDX", "Cannot start: at cap"))
    collector_state.record_sent_add("NEW-USD-PERP.DYDX", 2.0)
    assert collector_state.add_refused_reason("NEW-USD-PERP.DYDX") is None


# --- DW-60: every local stamp is monotonic, a wall-clock step changes nothing ---


@pytest.mark.parametrize("step", [-3600.0, 7200.0])
def test_a_wall_clock_step_does_not_change_row_or_plan_staleness(
    monkeypatch: pytest.MonkeyPatch, step: float
) -> None:
    _replay(*_RECORDED)
    wall = collector_state.time.time()
    monkeypatch.setattr(collector_state.time, "time", lambda: wall + step)
    row_id = next(iter(collector_state._LATEST_COLLECTOR_STATUS))
    assert collector_state.is_stale(row_id) is False
    assert collector_state.plan_is_stale("DYDX") is False
