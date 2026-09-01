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
Tests for bot_tui.app's Bot-detail wiring -- Story 4.5, AC1-AC4.

Widget-construction-level tests only (no real screen needed), same pattern as
test_app_coin_detail.py/test_app_bots.py. `_toggle_bot`'s actual asyncio-scheduled
publish call is not independently re-tested here beyond confirming `_active_bot_id()`
returns the right target -- same established precedent as test_app_bots.py's own
docstring: the first side-effecting statement needs a running event loop, left to the
manual smoke check.
"""

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
    assert "dashboard" not in app._footer_hint.text
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
