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
Tests for bot_tui.app's Coin-detail wiring -- Story 4.3, AC1-AC6.

Widget-construction-level tests only (no real screen needed) -- same pattern as
test_app_body.py/test_app_filter.py/test_app_stale_badge.py. Real keyboard-driven
focus movement inside a live terminal, the Coin-detail redraw loop's live indicator
updates over wall-clock time, and webbrowser.open()'s actual behavior are covered by
the manual smoke check, per this story's own Dev Notes.
"""

import urwid

from bot_tui import coin_detail_state
from bot_tui import ranking_state
from bot_tui.app import BotTuiApp
from bot_tui.app import _LADDER_COLLAPSED_LEVELS


def _reset() -> None:
    ranking_state._LATEST_RANKING = None
    ranking_state._LATEST_RANKING_RECEIVED_AT = 0.0
    coin_detail_state.close_coin()


def _ranking() -> dict:
    return {
        "mode": "volume",
        "updated_at": 1,
        "ranks": [
            {"instrument_id": "BTC-USD-PERP", "rank": 1, "volume24h": 1.0, "volatility_score": 0.0},
            {"instrument_id": "ETH-USD-PERP", "rank": 2, "volume24h": 2.0, "volatility_score": 0.0},
            {"instrument_id": "SOL-USD-PERP", "rank": 3, "volume24h": 3.0, "volatility_score": 0.0},
        ],
    }


# --- Task 1: selectable rows + highlighted instrument tracking ---


def test_coins_listbox_moves_focus_on_down_keypress() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    listbox = app._coins_body
    assert isinstance(listbox, urwid.ListBox)
    listbox.keypress((40, 10), "down")
    assert app._highlighted_instrument_id() == "ETH-USD-PERP"


def test_highlighted_instrument_id_is_none_before_any_message() -> None:
    _reset()
    app = BotTuiApp()
    assert app._highlighted_instrument_id() is None


def test_highlighted_instrument_id_is_none_on_no_matches() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("doge")
    app._on_filter_change(app._filter_edit, "doge")
    assert app._highlighted_instrument_id() is None


def test_highlighted_instrument_id_is_none_on_genuinely_empty_ranks() -> None:
    _reset()
    ranking_state._handle_rankings_message({"mode": "volume", "updated_at": 1, "ranks": []})
    app = BotTuiApp()
    assert app._highlighted_instrument_id() is None


# --- Task 4: Enter opens Coin-detail, d toggles the ladder ---


def test_enter_on_highlighted_row_opens_coin_detail() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._handle_global_key("enter")
    assert app._view == "coin_detail"
    assert app._coin_detail_instrument_id == "BTC-USD-PERP"
    assert app._ladder_expanded is False


def test_enter_on_empty_coins_list_is_a_no_op() -> None:
    _reset()
    app = BotTuiApp()
    app._handle_global_key("enter")
    assert app._view == "coins"


def test_ladder_never_remembers_expanded_state_across_visits() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    app._ladder_expanded = True
    # Simulate leaving and re-entering (a second visit) -- must reset to collapsed.
    app._open_coin_detail("ETH-USD-PERP")
    assert app._ladder_expanded is False


def test_d_toggles_ladder_without_touching_view_stack_or_breadcrumb() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    breadcrumb_before = app._breadcrumb.text
    stack_before = list(app._stack)
    app._handle_coin_detail_key("d")
    assert app._ladder_expanded is True
    assert app._view == "coin_detail"
    assert app._stack == stack_before
    assert app._breadcrumb.text == breadcrumb_before


def test_order_book_region_line_count_changes_between_collapsed_and_expanded() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    coin_detail_state._LATEST_SNAPSHOT = {
        "instrument_id": "BTC-USD-PERP",
        "bid_prices": [float(100 - i) for i in range(20)],
        "bid_sizes": [1.0] * 20,
        "ask_prices": [float(100 + i) for i in range(20)],
        "ask_sizes": [1.0] * 20,
        "buy_volume": 1.0,
        "sell_volume": 1.0,
        "buy_count": 1,
        "sell_count": 1,
        "ts_event": 1,
        "ts_init": 1,
    }
    collapsed_body = app._build_coin_detail_body()
    # Body is a ListBox (scrollable -- see _build_coin_detail_body); the ladder box
    # is the last item in its walker, itself Padding(LineBox(Pile(rows))) -- one
    # original_widget hop through the Padding, one through the LineBox.
    collapsed_ladder_rows = list(collapsed_body.body)[-1].original_widget.original_widget.contents
    app._ladder_expanded = True
    expanded_body = app._build_coin_detail_body()
    expanded_ladder_rows = list(expanded_body.body)[-1].original_widget.original_widget.contents
    # Classic ladder rows = ask levels + one mid-price divider + bid levels.
    assert len(expanded_ladder_rows) > len(collapsed_ladder_rows)
    assert len(collapsed_ladder_rows) == 2 * _LADDER_COLLAPSED_LEVELS + 1
    assert len(expanded_ladder_rows) == 2 * 20 + 1


def test_breadcrumb_format_is_coins_gt_instrument_id() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    assert app._breadcrumb.text == "Coins > BTC-USD-PERP"


def test_footer_hint_switches_to_coin_detail_text() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    assert "d expand book" in app._footer_hint.text
    assert "o dashboard" in app._footer_hint.text


# --- Task 5: `o` deep-link ---


def test_o_key_sets_footer_to_dashboard_url() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    app._handle_coin_detail_key("o")
    assert (
        app._footer_hint.text
        == "dashboard (copied to clipboard): http://127.0.0.1:8765/chart/BTC-USD-PERP"
    )


# --- Task 6: esc preserves Coins-pane scroll position and active filter ---


def test_esc_from_coin_detail_preserves_scroll_position() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._coins_body.keypress((40, 10), "down")  # focus row 1 (ETH)
    coins_body_before = app._coins_body
    app._open_coin_detail("ETH-USD-PERP")
    app._handle_coin_detail_key("esc")
    assert app._view == "coins"
    assert app._coins_body is coins_body_before
    assert app._highlighted_instrument_id() == "ETH-USD-PERP"


def test_esc_from_coin_detail_preserves_active_filter() -> None:
    # Drives the real _unhandled_input dispatch path throughout (no private
    # dispatch-method shortcuts) -- confirming a filter via Enter is the only real
    # keyboard route from "narrowed list" to "Coin-detail open", per _confirm_filter's
    # own docstring in app.py.
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("eth")
    app._on_filter_change(app._filter_edit, "eth")
    app._unhandled_input("enter")  # confirms the filter, returns focus to the body
    assert app._filter_active is False
    assert app._filter_text == "eth"
    app._unhandled_input("enter")  # opens Coin-detail for the one filtered row
    assert app._view == "coin_detail"
    app._unhandled_input("esc")
    assert app._view == "coins"
    assert app._filter_text == "eth"
    body = app._coins_body
    assert isinstance(body, urwid.ListBox)
    row_texts = [w.original_widget.text for w in body.body]  # type: ignore[attr-defined]
    assert len(row_texts) == 1
    assert "ETH-USD-PERP" in row_texts[0]


def test_esc_from_coin_detail_calls_close_coin() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_coin_detail("BTC-USD-PERP")
    assert coin_detail_state._CURRENT_INSTRUMENT_ID == "BTC-USD-PERP"
    app._handle_coin_detail_key("esc")
    assert coin_detail_state._CURRENT_INSTRUMENT_ID is None
