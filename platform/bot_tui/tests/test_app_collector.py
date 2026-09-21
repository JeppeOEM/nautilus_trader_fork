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
Tests for bot_tui.app.BotTuiApp's Collector pane -- Story 6.1.

Widget-construction tests, plus the p/x confirm-flow state transitions -- the latter
follow test_app_bots.py's own established pattern for _toggle_bot/stop-confirm:
_publish_collector_action is monkeypatched to a plain list-append rather than called for
real, since its first statement schedules a real asyncio task that needs a running loop.
"""

import urwid

from bot_tui import collector_state
from bot_tui.app import BotTuiApp
from bot_tui.app import _SelectableCollectorRow
from bot_tui.collector_pane import COLD_OPEN_TEXT


def _reset() -> None:
    collector_state._LATEST_COLLECTOR_STATUS = {}
    collector_state._LATEST_RECEIVED_AT = {}
    collector_state._LATEST_UNPINNED_IDS = []


def _status(iid: str, **overrides: object) -> dict:
    base = {
        "id": iid,
        "liquid": True,
        "last_trade_ts": 1_000_000_000,
    }
    base.update(overrides)
    return base


def test_cold_open_before_any_collector_status_message() -> None:
    _reset()
    app = BotTuiApp()
    app._refresh_collector_body()
    assert COLD_OPEN_TEXT in app._collector_body.original_widget.text


def test_populated_collector_pane_has_one_row_per_instrument() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._refresh_collector_body()
    assert len(app._collector_body.body) == 2


def test_rows_sorted_by_id_and_selectable() -> None:
    _reset()
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._refresh_collector_body()
    body = app._collector_body
    assert isinstance(body.body[0].original_widget, _SelectableCollectorRow)
    assert body.body[0].original_widget.instrument_id == "BTC-USD-PERP.DYDX"
    assert body.body[1].original_widget.instrument_id == "ETH-USD-PERP.DYDX"


def test_highlighted_collector_id_reads_listbox_focus() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    assert app._highlighted_collector_id() == "BTC-USD-PERP.DYDX"
    app._collector_body.focus_position = 1
    assert app._highlighted_collector_id() == "ETH-USD-PERP.DYDX"


def test_highlighted_collector_id_none_when_empty() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    assert app._highlighted_collector_id() is None


def _collector_app_with_one_row(instrument_id: str = "BTC-USD-PERP.DYDX") -> BotTuiApp:
    _reset()
    collector_state._handle_status_message(_status(instrument_id))
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    return app


def test_p_opens_unpin_confirm_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]

    app._toggle_pin()

    assert app._collector_confirm_active is True
    assert app._collector_confirm_action == "unpin"
    assert app._collector_confirm_id == "BTC-USD-PERP.DYDX"
    assert published == []


def test_typing_unpin_and_enter_confirms_and_publishes() -> None:
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]
    app._toggle_pin()

    app._stop_confirm_edit.set_edit_text("unpin")
    app._submit_collector_confirm()

    assert app._collector_confirm_active is False
    assert published == [("unpin", "BTC-USD-PERP.DYDX")]


def test_x_opens_stop_confirm_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]

    app._handle_collector_pane_key("x")

    assert app._collector_confirm_active is True
    assert app._collector_confirm_action == "stop"
    assert app._collector_confirm_id == "BTC-USD-PERP.DYDX"
    assert published == []


def test_typing_stop_and_enter_confirms_and_publishes() -> None:
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]
    app._handle_collector_pane_key("x")

    app._stop_confirm_edit.set_edit_text("stop")
    app._submit_collector_confirm()

    assert app._collector_confirm_active is False
    assert published == [("stop", "BTC-USD-PERP.DYDX")]


def test_wrong_text_keeps_collector_confirm_open_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]
    app._toggle_pin()

    app._stop_confirm_edit.set_edit_text("nope")
    app._submit_collector_confirm()

    assert app._collector_confirm_active is True
    assert app._collector_confirm_action == "unpin"
    assert published == []


def test_esc_cancels_collector_confirm_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]
    app._toggle_pin()

    app._close_collector_confirm()

    assert app._collector_confirm_active is False
    assert app._collector_confirm_action is None
    assert app._collector_confirm_id is None
    assert published == []


def test_collector_confirm_intercepts_keys_via_unhandled_input() -> None:
    # End-to-end through the real dispatch path (Story 6.1's own precedent for this,
    # see test_app_bots.py's test_stop_confirm_intercepts_keys_via_unhandled_input).
    app = _collector_app_with_one_row()
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, iid: published.append((action, iid))  # type: ignore[method-assign]

    app._unhandled_input("p")
    assert app._collector_confirm_active is True

    for ch in "unpin":
        app._stop_confirm_edit.insert_text(ch)
    app._unhandled_input("enter")

    assert app._collector_confirm_active is False
    assert published == [("unpin", "BTC-USD-PERP.DYDX")]


def test_refresh_collector_body_preserves_scroll_position_across_repeated_ticks() -> None:
    """
    Regression: the redraw loop calls _refresh_collector_body() every
    _REDRAW_POLL_SECONDS while this view is active. Before this fix, the body was
    rebuilt as a brand-new ListBox on every single tick, silently resetting scroll/focus
    to the top within half a second of any user scroll -- reported in production as
    "can't scroll up and down on the collector list, it jumps [back]".
    """
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("SOL-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    body_before = app._collector_body
    app._collector_body.focus_position = 2  # scroll to the last row

    # Simulate several more redraw ticks with unchanged status data.
    app._refresh_collector_body()
    app._refresh_collector_body()

    assert app._collector_body is body_before
    assert app._highlighted_collector_id() == "SOL-USD-PERP.DYDX"


def test_collector_body_shows_unpinned_ids_trailing_line() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message({"unpinned_ids": ["ETH-USD-PERP.DYDX"]})
    app = BotTuiApp()
    app._refresh_collector_body()
    plain_texts = [w.text for w in app._collector_body.body if isinstance(w, urwid.Text)]
    assert any("ETH-USD-PERP.DYDX" in t for t in plain_texts)


def test_collector_body_removed_message_drops_row_immediately() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    collector_state._handle_status_message({"id": "BTC-USD-PERP.DYDX", "removed": True})
    app = BotTuiApp()
    app._refresh_collector_body()
    assert len(app._collector_body.body) == 1


def test_refresh_collector_body_rebuilds_on_cold_open_to_populated_transition() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    filler_before = app._collector_body

    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    app._refresh_collector_body()

    assert app._collector_body is not filler_before
    assert len(app._collector_body.body) == 1
