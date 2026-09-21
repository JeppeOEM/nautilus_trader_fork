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
Tests for bot_tui.bots_state -- Story 4.4 (AC1/AC2 message-ingest and per-bot
staleness). Pure-logic tests only, no real Redis, mirroring test_ranking_state.py's
established no-real-clock-in-tests discipline.
"""

import pytest

from bot_tui import bots_state


def _status(bot_id: str = "bot-01") -> dict:
    return {
        "bot_id": bot_id,
        "strategy": "DummyStrategy",
        "symbol": "BTC-USD-PERP.DYDX",
        "mode": "paper",
        "running": True,
        "position_side": "flat",
        "net_exposure": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "win_rate": None,
        "started_at": 1_000.0,
        "updated_at": 1_000.0,
    }


def _reset() -> None:
    bots_state._LATEST_STATUSES = {}
    bots_state._LATEST_RECEIVED_AT = {}


def test_valid_message_stored_under_its_bot_id() -> None:
    _reset()
    message = _status("bot-07")
    bots_state._handle_status_message(message)
    assert bots_state._LATEST_STATUSES["bot-07"] == message


def test_two_different_bots_tracked_independently() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("bot-02"))
    assert set(bots_state._LATEST_STATUSES) == {"bot-01", "bot-02"}


def test_second_message_for_same_bot_id_overwrites_first() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    second = _status("bot-01")
    second["realized_pnl"] = 42.0
    bots_state._handle_status_message(second)
    assert bots_state._LATEST_STATUSES["bot-01"]["realized_pnl"] == 42.0


def test_malformed_message_missing_bot_id_is_ignored() -> None:
    _reset()
    bots_state._handle_status_message({"realized_pnl": 1.0})
    assert bots_state._LATEST_STATUSES == {}


def test_malformed_message_bot_id_not_a_string_is_ignored() -> None:
    _reset()
    bots_state._handle_status_message({"bot_id": 123})
    assert bots_state._LATEST_STATUSES == {}


def test_malformed_message_empty_bot_id_is_ignored() -> None:
    _reset()
    bots_state._handle_status_message({"bot_id": ""})
    assert bots_state._LATEST_STATUSES == {}


def test_valid_message_updates_received_at_to_current_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    monkeypatch.setattr(bots_state.time, "time", lambda: 12_345.0)
    bots_state._handle_status_message(_status("bot-01"))
    assert bots_state._LATEST_RECEIVED_AT["bot-01"] == 12_345.0


def test_is_stale_true_before_any_message() -> None:
    _reset()
    assert bots_state.is_stale("bot-01", now=1_000.0) is True


def test_is_stale_false_just_after_receipt() -> None:
    _reset()
    bots_state._LATEST_RECEIVED_AT["bot-01"] = 1_000.0
    assert bots_state.is_stale("bot-01", now=1_000.5) is False


def test_is_stale_true_past_threshold() -> None:
    _reset()
    bots_state._LATEST_RECEIVED_AT["bot-01"] = 1_000.0
    assert bots_state.is_stale("bot-01", now=1_000.0 + bots_state._BOT_STALE_SECONDS + 0.1) is True


def test_is_stale_false_exactly_at_threshold_boundary() -> None:
    _reset()
    bots_state._LATEST_RECEIVED_AT["bot-01"] = 1_000.0
    assert bots_state.is_stale("bot-01", now=1_000.0 + bots_state._BOT_STALE_SECONDS) is False


def test_is_stale_for_one_bot_does_not_affect_another() -> None:
    """AC2: a healthy bot next to a crashed one shows exactly one stale row."""
    _reset()
    bots_state._LATEST_RECEIVED_AT["bot-healthy"] = 1_000.0
    # bot-crashed has never received a message at all.
    assert bots_state.is_stale("bot-healthy", now=1_000.5) is False
    assert bots_state.is_stale("bot-crashed", now=1_000.5) is True
