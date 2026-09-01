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

Widget-construction-level tests only, same pattern/scope as test_app_stale_badge.py:
`_toggle_bot` itself is not unit-tested here since its first side-effecting statement
schedules a real asyncio task (`asyncio.ensure_future(bots_state.publish_control(...))`),
which needs a running event loop -- this codebase's own established precedent already
leaves the equivalent `_toggle_mode` untested at this level for the identical reason
(only the pure `ranking_state.toggle_mode` is unit-tested; see test_ranking_state.py).
Real keypress-triggered start/stop is covered by the manual smoke check instead.
"""

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


def test_cold_open_before_any_bots_status_message() -> None:
    _reset()
    app = BotTuiApp()
    body = app._build_bots_body()
    assert COLD_OPEN_TEXT in body.original_widget.text


def test_populated_bots_pane_has_one_row_per_bot() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("bot-02"))
    app = BotTuiApp()
    body = app._build_bots_body()
    assert len(body.body) == 2


def test_rows_sorted_by_bot_id_and_selectable() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-02"))
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    body = app._build_bots_body()
    assert isinstance(body.body[0], _SelectableBotRow)
    assert body.body[0].bot_id == "bot-01"
    assert body.body[1].bot_id == "bot-02"


def test_highlighted_bot_id_reads_listbox_focus() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("bot-02"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    assert app._highlighted_bot_id() == "bot-01"
    body = app._body.original_widget
    body.focus_position = 1
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
    body = app._build_bots_body()
    rows = {widget.bot_id: widget.text for widget in body.body}
    assert rows["bot-stale"].startswith("~")
    assert not rows["bot-fresh"].startswith("~")


def test_stopped_bot_row_shows_off() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=False))
    app = BotTuiApp()
    body = app._build_bots_body()
    assert "off" in body.body[0].text


def test_bots_pane_footer_hint_switches_on_entry() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("bots", [])
    assert "start/stop" in app._footer_hint.text


def test_leaving_bots_pane_restores_default_footer_hint() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._switch_view("coins", [])
    assert "start/stop" not in app._footer_hint.text


def test_s_on_running_bot_opens_stop_confirm_without_publishing() -> None:
    # Guards against an accidental `s` stopping a bot outright: pressing `s` on a
    # running bot must open the confirm prompt and must NOT call _publish_bot_action
    # yet (monkeypatched here specifically to avoid _toggle_bot's real asyncio-
    # scheduled publish path -- an established testing-boundary in this file/module,
    # see the module docstring).
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=True))
    app = BotTuiApp()
    app._switch_view("bots", [])
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]

    app._toggle_bot()

    assert app._stop_confirm_active is True
    assert app._stop_confirm_bot_id == "bot-01"
    assert published == []


def test_s_on_stopped_bot_starts_immediately_without_confirm() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=False))
    app = BotTuiApp()
    app._switch_view("bots", [])
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]

    app._toggle_bot()

    assert app._stop_confirm_active is False
    assert published == [("bot-01", "start")]


def test_typing_stop_and_enter_confirms_and_publishes() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=True))
    app = BotTuiApp()
    app._switch_view("bots", [])
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]
    app._toggle_bot()

    app._stop_confirm_edit.set_edit_text("stop")
    app._submit_stop_confirm()

    assert app._stop_confirm_active is False
    assert published == [("bot-01", "stop")]


def test_wrong_text_keeps_confirm_open_without_publishing() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=True))
    app = BotTuiApp()
    app._switch_view("bots", [])
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]
    app._toggle_bot()

    app._stop_confirm_edit.set_edit_text("nope")
    app._submit_stop_confirm()

    assert app._stop_confirm_active is True
    assert app._stop_confirm_bot_id == "bot-01"
    assert published == []


def test_esc_cancels_stop_confirm_without_publishing() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=True))
    app = BotTuiApp()
    app._switch_view("bots", [])
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
    _reset()
    bots_state._handle_status_message(_status("bot-01", running=True))
    app = BotTuiApp()
    app._switch_view("bots", [])
    published: list[tuple[str, str]] = []
    app._publish_bot_action = lambda bot_id, action: published.append((bot_id, action))  # type: ignore[method-assign]

    app._unhandled_input("s")
    assert app._stop_confirm_active is True

    for ch in "stop":
        app._stop_confirm_edit.insert_text(ch)
    app._unhandled_input("enter")

    assert app._stop_confirm_active is False
    assert published == [("bot-01", "stop")]
