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
Tests for bot_tui.app.BotTuiApp._build_body -- Story 4.1, AC2/AC5.

Widget construction itself needs no real terminal/screen (only MainLoop/draw_screen
do) -- added after a manual smoke check (Task 6) surfaced a real bug this story's
original test boundary missed: an empty-but-received rankings:live message
(ranks: []) was rendered identically to true cold-open (no message ever received).
"""

import urwid

from bot_tui import ranking_state
from bot_tui.app import BotTuiApp
from bot_tui.coins_pane import COLD_OPEN_TEXT


def _reset() -> None:
    ranking_state._LATEST_RANKING = None


def _filler_text(widget: urwid.Widget) -> str | None:
    if isinstance(widget, urwid.Filler) and isinstance(widget.original_widget, urwid.Text):
        return str(widget.original_widget.text)
    return None


def test_build_body_is_cold_open_filler_before_any_message() -> None:
    _reset()
    app = BotTuiApp()
    assert _filler_text(app._build_body()) == COLD_OPEN_TEXT


def test_build_body_is_empty_list_not_cold_open_once_a_message_with_empty_ranks_arrives() -> None:
    _reset()
    ranking_state._handle_rankings_message({"mode": "volume", "updated_at": 1, "ranks": []})
    app = BotTuiApp()
    body = app._build_body()
    assert isinstance(body, urwid.ListBox)
    assert _filler_text(body) is None


def test_build_body_renders_rows_once_ranks_are_present() -> None:
    _reset()
    ranking_state._handle_rankings_message(
        {
            "mode": "volume",
            "updated_at": 1,
            "ranks": [
                {
                    "instrument_id": "BTC-USD-PERP",
                    "rank": 1,
                    "volume24h": 1.0,
                    "volatility_score": 0.0,
                },
            ],
        }
    )
    app = BotTuiApp()
    body = app._build_body()
    assert isinstance(body, urwid.ListBox)
    # body.body is typed as the abstract ListWalker (no __iter__ in the stub), though the
    # concrete SimpleListWalker built in _build_body is iterable at runtime.
    row_texts = [w.text for w in body.body]  # type: ignore[attr-defined]
    assert len(row_texts) == 1
    assert "BTC-USD-PERP" in row_texts[0]


def test_build_body_bots_view_is_stub_placeholder() -> None:
    _reset()
    app = BotTuiApp()
    app._view = "bots"
    assert _filler_text(app._build_body()) == "no bots yet"
