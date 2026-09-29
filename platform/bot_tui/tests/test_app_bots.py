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
Tests for bot_tui.app.BotTuiApp's Bots pane -- Story 4.4, AC1/AC2.

Widget-construction-level tests only: `_toggle_bot`'s real publish path is not
unit-tested here since its first side-effecting statement schedules a real asyncio task
(`asyncio.ensure_future(bots_state.publish_control(...))`), which needs a running event
loop -- tests below monkeypatch `_publish_bot_action` instead. Real keypress-triggered
start/stop is covered by the manual smoke check.
"""

import time

import urwid

from bot_tui import bots_pane
from bot_tui import bots_state
from bot_tui.app import BotTuiApp
from bot_tui.app import _SelectableBotRow
from bot_tui.bots_pane import COLD_OPEN_TEXT


def _reset() -> None:
    bots_state._LATEST_STATUSES = {}
    bots_state._LATEST_RECEIVED_AT = {}


def _status(bot_id: str, **overrides: object) -> dict:
    base = {
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
    base.update(overrides)
    return base


def _listbox(app: BotTuiApp) -> urwid.ListBox:
    """Return the populated Bots pane's persistent rows ListBox (under the column header)."""
    assert app._bots_listbox is not None
    return app._bots_listbox


def _bots_app_with_one_row(bot_id: str = "bot-01", **overrides: object) -> BotTuiApp:
    _reset()
    bots_state._handle_status_message(_status(bot_id, **overrides))
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._refresh_bots_body()
    app._body.original_widget = app._bots_body
    return app


def test_cold_open_before_any_bots_status_message() -> None:
    _reset()
    app = BotTuiApp()
    app._refresh_bots_body()
    assert COLD_OPEN_TEXT in app._bots_body.original_widget.text


def test_populated_bots_pane_has_one_row_per_bot() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("bot-02"))
    app = BotTuiApp()
    app._refresh_bots_body()
    assert len(_listbox(app).body) == 2


def test_rows_sorted_by_bot_id_and_selectable() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-02"))
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._refresh_bots_body()
    body = _listbox(app)
    assert isinstance(body.body[0].original_widget, _SelectableBotRow)
    assert body.body[0].original_widget.bot_id == "bot-01"
    assert body.body[1].original_widget.bot_id == "bot-02"


def test_refresh_bots_body_preserves_scroll_position_across_repeated_ticks() -> None:
    """
    Regression: the redraw loop calls _refresh_bots_body() every
    _REDRAW_POLL_SECONDS while this view is active. Before this fix, the body was
    rebuilt as a brand-new ListBox on every single tick, silently resetting scroll/
    focus to the top within half a second of any user scroll -- reported in
    production as "the bots page jumps up" (the same class of bug already found and
    fixed once for the Collector pane, Story 6.1 -- see platform/CLAUDE.md TUI-01).
    """
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("bot-02"))
    bots_state._handle_status_message(_status("bot-03"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._refresh_bots_body()
    app._body.original_widget = app._bots_body
    body_before = app._bots_body
    listbox_before = _listbox(app)
    _listbox(app).focus_position = 2  # scroll to the last row

    # Simulate several more redraw ticks with unchanged status data.
    app._refresh_bots_body()
    app._refresh_bots_body()

    assert app._bots_body is body_before
    assert app._bots_listbox is listbox_before
    assert app._highlighted_bot_id() == "bot-03"


def test_highlighted_bot_id_reads_listbox_focus() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("bot-02"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._refresh_bots_body()
    app._body.original_widget = app._bots_body
    assert app._highlighted_bot_id() == "bot-01"
    _listbox(app).focus_position = 1
    assert app._highlighted_bot_id() == "bot-02"


def test_highlighted_bot_id_none_when_no_bots() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("bots", [])
    assert app._highlighted_bot_id() is None


def test_stale_row_has_marker_fresh_row_does_not() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-fresh"))
    bots_state._LATEST_RECEIVED_AT["bot-stale"] = 0.0
    bots_state._LATEST_STATUSES["bot-stale"] = _status("bot-stale")
    app = BotTuiApp()
    app._refresh_bots_body()
    body = _listbox(app)
    rows = {widget.original_widget.bot_id: widget.original_widget.text for widget in body.body}
    assert rows["bot-stale"].startswith("~")
    assert not rows["bot-fresh"].startswith("~")


def test_stopped_bot_row_shows_off() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=False))
    app = BotTuiApp()
    app._refresh_bots_body()
    assert "off" in _listbox(app).body[0].original_widget.text


def test_bots_pane_footer_hint_switches_on_entry() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("bots", [])
    assert "start/stop" in app._footer_hint.text


def test_leaving_bots_pane_switches_to_the_collector_footer_hint() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._switch_view("collector", [])
    assert "start/stop" not in app._footer_hint.text
    assert "p unpin" in app._footer_hint.text


def test_s_on_running_bot_opens_stop_confirm_without_publishing() -> None:
    # Guards against an accidental `s` stopping a bot outright: pressing `s` on a
    # running bot must open the confirm prompt and must NOT call _publish_bot_action
    # yet (monkeypatched here specifically to avoid _toggle_bot's real asyncio-
    # scheduled publish path -- an established testing-boundary in this file/module,
    # see the module docstring).
    app = _bots_app_with_one_row(running=True)
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]

    app._toggle_bot()

    assert app._stop_confirm_active is True
    assert app._stop_confirm_bot_id == "bot-01"
    assert published == []


def test_s_on_stopped_bot_starts_immediately_without_confirm() -> None:
    app = _bots_app_with_one_row(running=False)
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]

    app._toggle_bot()

    assert app._stop_confirm_active is False
    assert published == [("bot-01", "start")]


def test_typing_stop_and_enter_confirms_and_publishes() -> None:
    app = _bots_app_with_one_row(running=True)
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]
    app._toggle_bot()

    app._stop_confirm_edit.set_edit_text("stop")
    app._submit_stop_confirm()

    assert app._stop_confirm_active is False
    assert published == [("bot-01", "stop")]


def test_wrong_text_keeps_confirm_open_without_publishing() -> None:
    app = _bots_app_with_one_row(running=True)
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]
    app._toggle_bot()

    app._stop_confirm_edit.set_edit_text("nope")
    app._submit_stop_confirm()

    assert app._stop_confirm_active is True
    assert app._stop_confirm_bot_id == "bot-01"
    assert published == []


def test_esc_cancels_stop_confirm_without_publishing() -> None:
    app = _bots_app_with_one_row(running=True)
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]
    app._toggle_bot()

    app._close_stop_confirm()

    assert app._stop_confirm_active is False
    assert app._stop_confirm_bot_id is None
    assert published == []


def test_stop_confirm_intercepts_keys_via_unhandled_input() -> None:
    # End-to-end through the real dispatch path, not just direct method calls --
    # confirms _unhandled_input actually routes to the guard while it's active.
    app = _bots_app_with_one_row(running=True)
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]

    app._unhandled_input("s")
    assert app._stop_confirm_active is True

    for ch in "stop":
        app._stop_confirm_edit.insert_text(ch)
    app._unhandled_input("enter")

    assert app._stop_confirm_active is False
    assert published == [("bot-01", "stop")]


# --- Story 29.6: header, entry/sl/tp columns, minimum width ---


def _protected_status(bot_id: str, **overrides: object) -> dict:
    fields = {
        "position_side": "long",
        "stop_loss": "58900.0",
        "take_profit": "61020.5",
        "entry_price": "60000.0",
        "mark_price": "59980.5",
        "position_qty": "0.001",
        "stop_loss_orders": 1,
        "take_profit_orders": 1,
        "open_orders": 2,
        "last_fill_at": None,
    }
    fields.update(overrides)
    return _status(bot_id, **fields)


def test_the_populated_pane_has_the_column_header_above_its_rows() -> None:
    app = _bots_app_with_one_row()
    body = app._bots_body
    assert isinstance(body, urwid.Frame)
    assert body.header.text == bots_pane.bots_header_line()
    assert body.body is app._bots_listbox
    assert body.focus_position == "body"


def test_the_row_markup_is_the_formatted_line() -> None:
    _reset()
    status = _protected_status("bot-01")
    bots_state._handle_status_message(status)
    app = BotTuiApp()
    app._refresh_bots_body()
    row = _listbox(app).body[0].original_widget
    expected = bots_pane.format_bot_line(status, stale=False, now=time.time())
    # Up to the uptime column: the wall clock may tick between the two renders.
    up_column = bots_pane.bots_header_line().index(" up ") + 1
    assert len(row.text) == len(expected)
    assert row.text[:up_column] == expected[:up_column]


def test_an_unprotected_row_colors_its_none_stop_as_a_warning() -> None:
    _reset()
    bots_state._handle_status_message(
        _protected_status("bot-01", stop_loss=None, stop_loss_orders=0)
    )
    app = BotTuiApp()
    app._refresh_bots_body()
    row = _listbox(app).body[0].original_widget
    text, attributes = row.get_text()
    offset = 0
    colored = []
    for attr, length in attributes:
        if attr == bots_pane.WARNING_ATTR:
            colored.append(text[offset : offset + length])
        offset += length
    assert colored == ["none"]


def test_a_row_renders_on_one_line_at_the_minimum_width() -> None:
    _reset()
    bots_state._handle_status_message(
        _protected_status("a-much-too-long-bot-identifier", win_rate=1.0, started_at=0.0)
    )
    app = BotTuiApp()
    app._refresh_bots_body()
    row = _listbox(app).body[0]
    header = app._bots_body.header
    width = bots_pane.BOTS_PANE_MIN_WIDTH
    assert row.rows((width,)) == 1
    assert header.rows((width,)) == 1
    # The minimum is tight: one column fewer and the row wraps.
    assert row.rows((width - 1,)) == 2
