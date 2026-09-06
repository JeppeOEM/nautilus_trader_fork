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
    row_texts = [w.original_widget.text for w in body.body]  # type: ignore[attr-defined]
    assert len(row_texts) == 1
    assert "BTC-USD-PERP" in row_texts[0]


def test_column_header_shows_full_column_set_regardless_of_mode() -> None:
    # Full metric parity (SSOT-03, troll/CLAUDE.md): volume24h/volatility_score are
    # both always-present regular columns now, not a single mode-dependent "score"
    # column -- the header never changes when the active Ranking Mode toggles.
    _reset()
    app = BotTuiApp()
    header_before = str(app._coins_column_header.text)
    assert "Vol24h" in header_before
    assert "Vol Score" in header_before

    ranking_state._handle_rankings_message({"mode": "volatility", "updated_at": 1, "ranks": []})
    app._refresh_breadcrumb()

    assert str(app._coins_column_header.text) == header_before


def test_column_header_is_blank_off_the_coins_pane() -> None:
    _reset()
    app = BotTuiApp()
    app._view = "bots"
    app._refresh_breadcrumb()
    assert str(app._coins_column_header.text) == ""


def test_build_body_help_view_shows_control_reference() -> None:
    app = BotTuiApp()
    app._view = "help"
    body = app._build_body()
    assert _filler_text(body) is not None
    assert "COINS PANE" in _filler_text(body)  # type: ignore[operator]


def test_breadcrumb_shows_stale_feed_banner_for_recently_stale_instrument() -> None:
    _reset()
    ranking_state._handle_rankings_message(
        {
            "mode": "volume",
            "updated_at": 1,
            "ranks": [],
            "stale_instrument_ids": ["SOL-USD-PERP.DYDX"],
        }
    )
    app = BotTuiApp()
    app._refresh_breadcrumb()
    assert app._breadcrumb.text == "Coins  ~ stale feed: SOL-USD-PERP.DYDX"


def test_breadcrumb_omits_stale_feed_banner_when_nothing_stale() -> None:
    _reset()
    ranking_state._handle_rankings_message(
        {"mode": "volume", "updated_at": 1, "ranks": [], "stale_instrument_ids": []}
    )
    app = BotTuiApp()
    app._refresh_breadcrumb()
    assert app._breadcrumb.text == "Coins"


def test_build_body_bots_view_cold_open_before_any_bots_status() -> None:
    # Story 4.4 replaced this pane's stub placeholder with real bots:status-driven
    # content -- see test_app_bots.py for the full Bots-pane test suite; this test is
    # kept here only to confirm _build_body's own "bots" dispatch branch still routes
    # to it correctly.
    _reset()
    app = BotTuiApp()
    app._view = "bots"
    assert _filler_text(app._build_body()) == "waiting for bots:status…"
