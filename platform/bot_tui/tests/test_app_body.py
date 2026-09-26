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
Tests for bot_tui.app.BotTuiApp._build_body and the app's start state -- Story 4.1,
AC2/AC5; Story 25.1a (the Coins pane is gone: the app opens on the Bots pane).

Widget construction itself needs no real terminal/screen (only MainLoop/draw_screen
do).
"""

import urwid

from bot_tui import bots_state
from bot_tui.app import _BREADCRUMB_LABELS
from bot_tui.app import _FOOTER_HINT_TEXTS
from bot_tui.app import BotTuiApp
from bot_tui.bots_pane import COLD_OPEN_TEXT


def _reset() -> None:
    bots_state._LATEST_STATUSES = {}
    bots_state._LATEST_RECEIVED_AT = {}


def _filler_text(widget: urwid.Widget) -> str | None:
    if isinstance(widget, urwid.Filler) and isinstance(widget.original_widget, urwid.Text):
        return str(widget.original_widget.text)
    return None


def test_app_starts_on_the_bots_pane() -> None:
    _reset()
    app = BotTuiApp()
    assert app._view == "bots"
    assert app._breadcrumb.text == "Bots"
    assert "start/stop" in app._footer_hint.text


def test_only_bots_collector_and_help_are_top_level_views() -> None:
    assert set(_BREADCRUMB_LABELS) == {"bots", "collector", "help"}


def test_build_body_is_bots_cold_open_filler_before_any_bots_status() -> None:
    _reset()
    app = BotTuiApp()
    assert _filler_text(app._build_body()) == COLD_OPEN_TEXT


def test_build_body_help_view_shows_control_reference() -> None:
    app = BotTuiApp()
    app._view = "help"
    text = _filler_text(app._build_body())
    assert text is not None
    assert "BOTS PANE" in text
    assert "COLLECTOR PANE" in text


def test_help_text_lists_no_retired_coins_pane_keys() -> None:
    app = BotTuiApp()
    app._view = "help"
    text = _filler_text(app._build_body())
    assert text is not None
    assert "COINS PANE" not in text
    assert "COIN DETAIL" not in text
    assert "ranking mode (volume" not in text


def test_no_footer_advertises_a_retired_coins_pane_key() -> None:
    for view, footer in _FOOTER_HINT_TEXTS.items():
        for retired in ("/ filter", "m mode", "space dashboard", "d expand book"):
            assert retired not in footer, f"{view} footer still advertises {retired!r}"


def test_retired_coins_pane_keys_are_no_ops_on_the_bots_pane() -> None:
    _reset()
    app = BotTuiApp()
    footer_before = app._footer_hint.text
    for key in ("/", "m", " ", "enter"):
        app._handle_global_key(key)
    assert app._view == "bots"
    assert app._stack == []
    assert app._footer_hint.text == footer_before
