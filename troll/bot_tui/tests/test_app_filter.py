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
Tests for bot_tui.app.BotTuiApp's `/` inline filter -- Story 4.2, AC1.

Widget-construction-level tests only (no real screen needed to construct/read
urwid.ListBox/Filler contents) -- same pattern as test_app_body.py. Live
keystroke-by-keystroke narrowing (urwid.Edit's "change" signal) and the actual
MainLoop/draw_screen wiring are covered by the manual smoke check (Task 6), per this
story's Dev Notes "Testing strategy" section.
"""

import urwid

from bot_tui import ranking_state
from bot_tui.app import BotTuiApp
from bot_tui.coins_pane import NO_MATCHES_TEXT


def _reset() -> None:
    ranking_state._LATEST_RANKING = None
    ranking_state._LATEST_RANKING_RECEIVED_AT = 0.0


def _ranking() -> dict:
    return {
        "mode": "volume",
        "updated_at": 1,
        "ranks": [
            {"instrument_id": "BTC-USD-PERP", "rank": 1, "volume24h": 1.0, "volatility_score": 0.0},
            {"instrument_id": "ETH-USD-PERP", "rank": 2, "volume24h": 2.0, "volatility_score": 0.0},
        ],
    }


def _filler_text(widget: urwid.Widget) -> str | None:
    if isinstance(widget, urwid.Filler) and isinstance(widget.original_widget, urwid.Text):
        return str(widget.original_widget.text)
    return None


def test_open_filter_activates_and_swaps_footer() -> None:
    _reset()
    app = BotTuiApp()
    app._open_filter()
    assert app._filter_active is True
    assert app._frame.footer is app._filter_edit


def test_filter_change_narrows_body_to_matching_rows() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("eth")
    app._on_filter_change(app._filter_edit, "eth")
    body = app._body.original_widget
    assert isinstance(body, urwid.ListBox)
    row_texts = [w.text for w in body.body]  # type: ignore[attr-defined]
    assert len(row_texts) == 1
    assert "ETH-USD-PERP" in row_texts[0]


def test_filter_with_no_matches_renders_no_matches_text() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("doge")
    app._on_filter_change(app._filter_edit, "doge")
    assert _filler_text(app._body.original_widget) == NO_MATCHES_TEXT


def test_close_filter_restores_full_unfiltered_list_and_clears_active() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("eth")
    app._on_filter_change(app._filter_edit, "eth")
    app._close_filter()
    assert app._filter_active is False
    assert app._frame.footer is app._footer_hint
    body = app._body.original_widget
    assert isinstance(body, urwid.ListBox)
    row_texts = [w.text for w in body.body]  # type: ignore[attr-defined]
    assert len(row_texts) == 2


def test_esc_while_filtering_never_pops_view_stack() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._view = "bots"
    app._stack = ["coins"]
    app._open_filter()
    app._unhandled_input("esc")
    assert app._filter_active is False
    assert app._view == "bots"
    assert app._stack == ["coins"]


def test_confirm_filter_keeps_narrowing_and_returns_focus_to_body() -> None:
    _reset()
    ranking_state._handle_rankings_message(_ranking())
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("eth")
    app._on_filter_change(app._filter_edit, "eth")
    app._unhandled_input("enter")
    assert app._filter_active is False
    assert app._filter_text == "eth"
    assert app._frame.footer is app._footer_hint
    body = app._body.original_widget
    assert isinstance(body, urwid.ListBox)
    row_texts = [w.text for w in body.body]  # type: ignore[attr-defined]
    assert len(row_texts) == 1
    assert "ETH-USD-PERP" in row_texts[0]


def test_cold_open_wins_over_filter_when_no_message_ever_arrived() -> None:
    _reset()
    app = BotTuiApp()
    app._open_filter()
    app._filter_edit.set_edit_text("btc")
    app._on_filter_change(app._filter_edit, "btc")
    from bot_tui.coins_pane import COLD_OPEN_TEXT

    assert _filler_text(app._body.original_widget) == COLD_OPEN_TEXT
