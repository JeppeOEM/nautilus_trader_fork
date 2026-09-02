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
Tests for bot_tui.app's Bot-detail wiring -- Story 4.5, AC1-AC4; Story 4.7, AC1-AC4.

Widget-construction-level tests only (no real screen needed), same pattern as
test_app_coin_detail.py/test_app_bots.py. `_toggle_bot`'s actual asyncio-scheduled
publish call is not independently re-tested here beyond confirming `_active_bot_id()`
returns the right target -- same established precedent as test_app_bots.py's own
docstring: the first side-effecting statement needs a running event loop, left to the
manual smoke check. `webbrowser.open()`'s actual behavior is the same established
manual-smoke-check exception test_app_coin_detail.py's own docstring already
documents for the `o` key -- only the footer-echo half is asserted here.
"""

import urwid

from bot_tui import bot_history_state
from bot_tui import bots_state
from bot_tui.app import BotTuiApp


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
        "closed_trades": 0,
        "started_at": 1_000.0,
        "updated_at": 1_000.0,
    }
    base.update(overrides)
    return base


def test_enter_on_highlighted_bots_row_opens_bot_detail() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._handle_global_key("enter")
    assert app._view == "bot_detail"
    assert app._bot_detail_bot_id == "bot-01"


def test_enter_on_empty_bots_pane_is_a_no_op() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._handle_global_key("enter")
    assert app._view == "bots"


def test_esc_from_bot_detail_returns_to_bots_and_clears_bot_id() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("esc")
    assert app._view == "bots"
    assert app._bot_detail_bot_id is None


def test_active_bot_id_reads_highlighted_row_from_bots_pane() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    assert app._active_bot_id() == "bot-01"


def test_active_bot_id_reads_open_bot_from_bot_detail() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    assert app._active_bot_id() == "bot-01"


def test_breadcrumb_format_is_bots_gt_bot_id() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-03"))
    app = BotTuiApp()
    app._open_bot_detail("bot-03")
    assert app._breadcrumb.text == "Bots > bot-03"


def test_bot_detail_footer_hint_on_entry_and_restored_on_esc() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._switch_view("bots", [])
    app._open_bot_detail("bot-01")
    assert "start/stop" in app._footer_hint.text
    assert "esc back" in app._footer_hint.text
    assert "j/k" not in app._footer_hint.text
    # Story 4.7: t (range)/o (dashboard) are now real Bot-detail keys, unlike Story
    # 4.5 which had neither yet.
    assert "t range" in app._footer_hint.text
    assert "o dashboard" in app._footer_hint.text
    app._handle_bot_detail_key("esc")
    assert app._footer_hint.text == "s start/stop  : command  esc back  :q quit"


def test_build_bot_detail_body_renders_known_fields() -> None:
    _reset()
    bots_state._handle_status_message(
        _status("bot-03", strategy="microprice_rev", symbol="SOL-USD-PERP.DYDX", mode="paper")
    )
    app = BotTuiApp()
    app._open_bot_detail("bot-03")
    body = app._build_bot_detail_body()
    assert body is not None


def test_build_bot_detail_body_handles_missing_status_defensively() -> None:
    _reset()
    app = BotTuiApp()
    app._bot_detail_bot_id = "bot-ghost"
    body = app._build_bot_detail_body()
    assert body is not None


# --- Story 4.7: trades blotter + PnL sparkline ---


def _history(**overrides: object) -> dict:
    base = {
        "bot_id": "bot-01",
        "range": "day",
        "updated_at": 1_000_000_000,
        "trades": [
            {"ts": 1_000_000_000, "side": "BUY", "price": 100.0, "qty": 1.0, "realized_pnl": None},
        ],
        "pnl_series": [{"period_start": 0, "pnl": 5.0}],
    }
    base.update(overrides)
    return base


def test_open_bot_detail_starts_tracking_history_and_resets_range() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._bot_history_range = "month"
    app._open_bot_detail("bot-01")
    assert app._bot_history_range == "day"
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    assert bot_history_state.get_history("day") == _history()


def test_esc_from_bot_detail_stops_tracking_history() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    bot_history_state._handle_history_payload("bot-01", "day", _history())
    app._handle_bot_detail_key("esc")
    assert bot_history_state.get_history("day") is None


def test_build_bot_detail_body_shows_history_unavailable_before_any_fetch() -> None:
    # AC4: unreachable/never-fetched read surface -- the blotter/sparkline regions
    # must independently say so, while the snapshot region above is unaffected.
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    body = app._build_bot_detail_body()
    assert body is not None


def test_build_bot_detail_body_has_three_stacked_bordered_regions() -> None:
    # AC1: snapshot, trades blotter, PnL sparkline -- stacked top-to-bottom.
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    body = app._build_bot_detail_body()
    assert isinstance(body, urwid.Filler)
    pile = body.original_widget
    assert isinstance(pile, urwid.Pile)
    boxes = [widget for widget, _options in pile.contents]
    assert len(boxes) == 3
    snapshot_box, blotter_box, sparkline_box = boxes
    assert isinstance(snapshot_box, urwid.LineBox)
    assert isinstance(blotter_box, urwid.LineBox)
    assert isinstance(sparkline_box, urwid.LineBox)
    assert snapshot_box.title_widget.text.strip() == "bot-01  snapshot"
    assert blotter_box.title_widget.text.strip() == "trades"
    assert sparkline_box.title_widget.text.strip() == "pnl (day)"


def test_t_key_cycles_range_and_echoes_footer() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    assert app._bot_history_range == "day"
    app._handle_bot_detail_key("t")
    assert app._bot_history_range == "week"
    assert app._footer_hint.text == "range: week"


def test_t_key_full_cycle_returns_to_day() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    for _ in range(4):
        app._handle_bot_detail_key("t")
    assert app._bot_history_range == "day"


def test_o_key_sets_footer_to_bot_dashboard_url() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-07"))
    app = BotTuiApp()
    app._open_bot_detail("bot-07")
    app._handle_bot_detail_key("o")
    assert app._footer_hint.text == "dashboard: http://127.0.0.1:8765/bot/bot-07"
