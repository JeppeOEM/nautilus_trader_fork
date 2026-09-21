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
Tests for bot_tui.ranking_state -- Story 4.1 (AC2/AC5) and Story 4.2 (AC5, staleness;
AC2, mode-toggle).

Pure-logic tests only (message validation, cold-open state, staleness detection,
mode-toggle). `is_stale`/`toggle_mode` are pure and always given an explicit `now`/
`current_mode` in tests -- no real clock, no real Redis, matching this codebase's
established no-real-clock-in-tests discipline.
"""

import pytest

from bot_tui import ranking_state


def _valid_message() -> dict:
    return {
        "mode": "volume",
        "updated_at": 1_700_000_000_000_000_000,
        "ranks": [
            {"instrument_id": "BTC-USD-PERP", "rank": 1, "volume24h": 1.0, "volatility_score": 0.1},
        ],
    }


def _reset() -> None:
    ranking_state._LATEST_RANKING = None


def test_cold_open_before_any_message() -> None:
    _reset()
    assert ranking_state._LATEST_RANKING is None


def test_valid_message_updates_latest_ranking() -> None:
    _reset()
    message = _valid_message()
    ranking_state._handle_rankings_message(message)
    assert message == ranking_state._LATEST_RANKING


def test_malformed_message_missing_ranks_key_is_ignored() -> None:
    _reset()
    ranking_state._handle_rankings_message({"mode": "volume", "updated_at": 1})
    assert ranking_state._LATEST_RANKING is None


def test_malformed_message_ranks_not_a_list_is_ignored() -> None:
    _reset()
    ranking_state._handle_rankings_message({"mode": "volume", "ranks": "not-a-list"})
    assert ranking_state._LATEST_RANKING is None


def test_malformed_message_after_a_valid_one_preserves_previous_state() -> None:
    _reset()
    good = _valid_message()
    ranking_state._handle_rankings_message(good)
    ranking_state._handle_rankings_message({"ranks": None})
    assert good == ranking_state._LATEST_RANKING


def test_valid_message_updates_received_at_to_current_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    ranking_state._LATEST_RANKING_RECEIVED_AT = 0.0
    monkeypatch.setattr(ranking_state.time, "time", lambda: 12_345.0)
    ranking_state._handle_rankings_message(_valid_message())
    assert ranking_state._LATEST_RANKING_RECEIVED_AT == 12_345.0


def test_malformed_message_leaves_received_at_unchanged() -> None:
    _reset()
    ranking_state._handle_rankings_message(_valid_message())
    received_at = ranking_state._LATEST_RANKING_RECEIVED_AT
    ranking_state._handle_rankings_message({"ranks": None})
    assert received_at == ranking_state._LATEST_RANKING_RECEIVED_AT


def test_is_stale_false_just_after_receipt() -> None:
    assert ranking_state.is_stale(received_at=1_000.0, now=1_000.5) is False


def test_is_stale_true_past_threshold() -> None:
    assert (
        ranking_state.is_stale(
            received_at=1_000.0, now=1_000.0 + ranking_state._RANKING_STALE_SECONDS + 0.1
        )
        is True
    )


def test_is_stale_false_exactly_at_threshold_boundary() -> None:
    # Strictly-greater-than semantics, matching dashboard.py's own `> _RANKING_STALE_SECONDS`.
    assert (
        ranking_state.is_stale(
            received_at=1_000.0, now=1_000.0 + ranking_state._RANKING_STALE_SECONDS
        )
        is False
    )


def test_is_stale_true_when_never_received() -> None:
    assert ranking_state.is_stale(received_at=0.0, now=1_000.0) is True


def test_toggle_mode_volume_to_volatility() -> None:
    assert ranking_state.toggle_mode("volume") == "volatility"


def test_toggle_mode_volatility_to_volume() -> None:
    assert ranking_state.toggle_mode("volatility") == "volume"


def test_toggle_mode_none_defaults_to_volatility() -> None:
    # No message ever received -- ranking_engine's own default starting mode is
    # "volume" (engine.py's _ACTIVE_MODE), so toggling from "unknown" is equivalent
    # to toggling from "volume".
    assert ranking_state.toggle_mode(None) == "volatility"
