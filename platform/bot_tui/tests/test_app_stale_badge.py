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
Tests for bot_tui.app.BotTuiApp's pane-level stale badge -- Story 4.2, AC3/AC5.

Widget-construction-level tests only (urwid.Text markup can be read without a real
screen). The actual palette rendering (does "stale" attribute really show yellow) and
the redraw-loop's periodic re-evaluation over real wall-clock time are covered by the
manual smoke check (Task 6), per this story's Dev Notes "Testing strategy" section.
"""

from bot_tui import coin_detail_state
from bot_tui import ranking_state
from bot_tui.app import BotTuiApp


def _reset() -> None:
    ranking_state._LATEST_RANKING = None
    ranking_state._LATEST_RANKING_RECEIVED_AT = 0.0
    coin_detail_state.close_coin()


def _ranking() -> dict:
    return {"mode": "volume", "updated_at": 1, "ranks": []}


def test_fresh_ranking_breadcrumb_has_no_stale_markup() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._refresh_breadcrumb()
    markup = app._breadcrumb.text
    assert "STALE" not in markup


def test_stale_ranking_breadcrumb_includes_stale_markup() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    ranking_state._LATEST_RANKING_RECEIVED_AT = 0.0  # never-received == always stale
    app = BotTuiApp()
    app._refresh_breadcrumb()
    assert "STALE" in app._breadcrumb.text


def test_stale_badge_never_shows_on_bots_pane() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    ranking_state._LATEST_RANKING_RECEIVED_AT = 0.0
    app = BotTuiApp()
    app._view = "bots"
    app._refresh_breadcrumb()
    assert "STALE" not in app._breadcrumb.text


def test_stale_badge_never_shows_before_cold_open() -> None:
    _reset()
    app = BotTuiApp()
    app._refresh_breadcrumb()
    assert "STALE" not in app._breadcrumb.text


def test_coin_detail_breadcrumb_has_no_stale_markup_before_first_snapshot() -> None:
    # DATA-01 follow-up (Review finding, post-4.3): no snapshot yet is the legitimate
    # "warming up" state, not staleness -- must not show STALE just because
    # _LATEST_SNAPSHOT_RECEIVED_AT is still its 0.0 initial value.
    _reset()
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    assert "STALE" not in app._breadcrumb.text


def test_coin_detail_breadcrumb_includes_stale_markup_once_snapshot_ages_out() -> None:
    _reset()
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    coin_detail_state._handle_snapshot_batch(
        [
            {
                "instrument_id": "BTC-USD-PERP",
                "bid_prices": [100.0],
                "bid_sizes": [1.0],
                "ask_prices": [100.5],
                "ask_sizes": [1.0],
                "buy_volume": 1.0,
                "sell_volume": 1.0,
                "buy_count": 1,
                "sell_count": 1,
                "ts_event": 1,
                "ts_init": 1,
            }
        ]
    )
    coin_detail_state._LATEST_SNAPSHOT_RECEIVED_AT = 0.0  # long-ago received == stale
    app._refresh_breadcrumb()
    assert "STALE" in app._breadcrumb.text
