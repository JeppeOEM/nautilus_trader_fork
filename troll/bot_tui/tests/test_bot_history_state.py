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
Tests for bot_tui.bot_history_state -- Story 4.7 (AC1/AC4). Pure-logic tests only, no
real Redis -- mirroring test_bots_state.py's established no-real-network discipline;
poll_loop()'s actual GET/reconnect behavior is left to a manual smoke check, same
established precedent as every other *_state.py module's own listener/poll loop.
"""

from bot_tui import bot_history_state


def _history(**overrides: object) -> dict:
    base = {
        "bot_id": "bot-01",
        "range": "day",
        "updated_at": 1_000_000_000,
        "trades": [{"ts": 1, "side": "BUY", "price": 100.0, "qty": 1.0, "realized_pnl": None}],
        "pnl_series": [{"period_start": 0, "pnl": 5.0}],
    }
    base.update(overrides)
    return base


def test_get_history_none_before_any_bot_is_open() -> None:
    assert bot_history_state.get_history("day") is None


def test_open_bot_then_valid_payload_is_retrievable() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    assert bot_history_state.get_history("day") == _history()


def test_close_bot_clears_everything() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    bot_history_state.close_bot()
    assert bot_history_state.get_history("day") is None


def test_open_bot_clears_the_previous_bots_data() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    bot_history_state.open_bot("bot-02")
    assert bot_history_state.get_history("day") is None


def test_malformed_payload_missing_trades_is_ignored() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", {"pnl_series": []})
    assert bot_history_state.get_history("day") is None


def test_is_stale_true_when_never_fetched() -> None:
    bot_history_state.open_bot("bot-01")
    assert bot_history_state.is_stale("day") is True


def test_is_stale_false_immediately_after_a_fresh_payload() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    assert bot_history_state.is_stale("day", now=None) is False


def test_is_stale_true_past_the_90_second_timeout() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    received_at = bot_history_state._LATEST_RECEIVED_AT["day"]
    assert bot_history_state.is_stale("day", now=received_at + 91.0) is True


def test_get_history_returns_none_once_stale() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    received_at = bot_history_state._LATEST_RECEIVED_AT["day"]
    # get_history() has no `now` parameter (it always uses real time internally via
    # is_stale()'s default) -- directly manipulate the stored received-at timestamp
    # instead, the same technique the staleness test above uses.
    bot_history_state._LATEST_RECEIVED_AT["day"] = received_at - 1_000.0
    assert bot_history_state.get_history("day") is None


def test_ranges_are_tracked_independently() -> None:
    bot_history_state.open_bot("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history(range="day"))
    assert bot_history_state.get_history("day") is not None
    assert bot_history_state.get_history("week") is None


def test_stale_payload_from_a_bot_switched_away_from_is_dropped() -> None:
    """
    Regression: a GET in flight for bot-01 must not land in the store once the
    operator has switched Bot-detail to bot-02 while that GET was still pending --
    otherwise bot-02's view would briefly show bot-01's trades/PnL. The in-flight
    response arrives "late" here by simply calling _handle_history_payload with the
    stale bot_id after open_bot("bot-02") has already run, exactly mirroring how
    _poll_once's captured bot_id argument can outlive a since-changed _TRACKED_BOT_ID.
    """
    bot_history_state.open_bot("bot-01")
    bot_history_state.open_bot("bot-02")
    bot_history_state._handle_history_payload("bot-01", "day", _history(bot_id="bot-01"))
    assert bot_history_state.get_history("day") is None
