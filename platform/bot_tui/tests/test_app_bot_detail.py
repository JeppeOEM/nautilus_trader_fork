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
test_app_bots.py. `_toggle_bot`'s actual asyncio-scheduled publish call is not
independently re-tested here beyond confirming `_active_bot_id()` returns the right
target -- same established precedent as test_app_bots.py's own docstring: the first
side-effecting statement needs a running event loop, left to the manual smoke check.
`webbrowser.open()`'s actual behavior is a manual-smoke-check exception too -- only the
`o` key's footer echo and the local-listener hand-off (a real loopback socket) are
asserted here.
"""

import socket

import urwid

from bot_tui import app as app_module
from bot_tui import bot_history_state
from bot_tui import bot_incidents_state
from bot_tui import bots_pane
from bot_tui import bots_state
from bot_tui.app import BotTuiApp


def _reset() -> None:
    bots_state._LATEST_STATUSES = {}
    bots_state._LATEST_RECEIVED_AT = {}
    bot_incidents_state._LATEST_INCIDENTS = {}
    bot_incidents_state._LATEST_RECEIVED_AT = {}
    bot_incidents_state._TRACKED_BOT_ID = None


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
    app._refresh_bots_body()
    app._body.original_widget = app._bots_body
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
    app._refresh_bots_body()
    app._body.original_widget = app._bots_body
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
    # Story 4.7: h/l (range)/o (dashboard) are now real Bot-detail keys, unlike Story
    # 4.5 which had neither yet.
    assert "h/l range" in app._footer_hint.text
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


def test_build_bot_detail_body_has_four_stacked_bordered_regions() -> None:
    # AC1: snapshot, trades blotter, PnL sparkline, performance metrics -- stacked
    # top-to-bottom.
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    body = app._build_bot_detail_body()
    assert isinstance(body, urwid.ListBox)
    boxes = list(body.body)
    assert len(boxes) == 4
    snapshot_box, blotter_box, sparkline_box, metrics_box = boxes
    assert isinstance(snapshot_box, urwid.LineBox)
    assert isinstance(blotter_box, urwid.LineBox)
    assert isinstance(sparkline_box, urwid.LineBox)
    assert isinstance(metrics_box, urwid.LineBox)
    assert snapshot_box.title_widget.text.strip() == "bot-01  snapshot"
    assert blotter_box.title_widget.text.strip() == "trades"
    assert sparkline_box.title_widget.text.strip() == "pnl (day)"


def test_right_key_steps_range_forward_and_echoes_footer() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    assert app._bot_history_range == "day"
    app._handle_bot_detail_key("right")
    assert app._bot_history_range == "week"
    assert app._footer_hint.text == "range: week"


def test_l_key_steps_range_forward_same_as_right() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("l")
    assert app._bot_history_range == "week"


def test_left_and_h_key_step_range_backward() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("left")
    assert app._bot_history_range == "all"
    app._handle_bot_detail_key("h")
    assert app._bot_history_range == "month"


def test_right_key_full_cycle_returns_to_day() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    for _ in range(4):
        app._handle_bot_detail_key("right")
    assert app._bot_history_range == "day"


def test_o_key_sets_footer_to_bot_dashboard_url(monkeypatch) -> None:
    monkeypatch.delenv("BOT_TUI_OPEN_URL_PORT", raising=False)
    _reset()
    bots_state._handle_status_message(_status("bot-07"))
    app = BotTuiApp()
    app._open_bot_detail("bot-07")
    app._handle_bot_detail_key("o")
    assert (
        app._footer_hint.text == "dashboard (copied to clipboard): http://127.0.0.1:9100/bot/bot-07"
    )


# --- BOT_TUI_OPEN_URL_PORT: hand off to a local open_listener.go instead of
# webbrowser.open()/OSC52, when troll-tui's reverse SSH tunnel is up ---


def _listening_socket() -> tuple[socket.socket, int]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    # accept() raises instead of hanging the suite if the app never connects.
    server.settimeout(5.0)
    return server, server.getsockname()[1]


def test_open_via_local_listener_sends_url_and_returns_true(monkeypatch) -> None:
    server, port = _listening_socket()
    monkeypatch.setenv("BOT_TUI_OPEN_URL_PORT", str(port))

    with server:
        result = BotTuiApp._open_via_local_listener("http://127.0.0.1:9100/bot/bot-07")
        conn, _ = server.accept()
        with conn:
            received = conn.recv(4096)

    assert result is True
    assert received == b"http://127.0.0.1:9100/bot/bot-07"


def test_open_via_local_listener_false_when_port_unset(monkeypatch) -> None:
    monkeypatch.delenv("BOT_TUI_OPEN_URL_PORT", raising=False)
    assert BotTuiApp._open_via_local_listener("http://127.0.0.1:9100/bot/bot-07") is False


def test_open_via_local_listener_false_when_nothing_listening(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TUI_OPEN_URL_PORT", "1")  # privileged/unused port, connect refused
    assert BotTuiApp._open_via_local_listener("http://127.0.0.1:9100/bot/bot-07") is False


def test_o_key_uses_local_listener_when_port_set(monkeypatch) -> None:
    server, port = _listening_socket()
    monkeypatch.setenv("BOT_TUI_OPEN_URL_PORT", str(port))
    _reset()
    bots_state._handle_status_message(_status("bot-07"))
    app = BotTuiApp()
    app._open_bot_detail("bot-07")

    with server:
        app._handle_bot_detail_key("o")
        conn, _ = server.accept()
        conn.close()
    assert app._footer_hint.text == "dashboard: http://127.0.0.1:9100/bot/bot-07"


def test_v_key_opens_strategy_view_with_breadcrumb(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("v")
    assert app._view == "strategy"
    assert app._breadcrumb.text == "Bots > bot-01 > strategy"


def _source_lines(body: urwid.Widget) -> list[str]:
    assert isinstance(body, urwid.ListBox)
    return [widget.text for widget in body.body]  # type: ignore[attr-defined]


def _filler_text(body: urwid.Widget) -> str:
    assert isinstance(body, urwid.Filler)
    return body.original_widget.text


def test_build_strategy_body_renders_file_lines_scrollable(monkeypatch, tmp_path) -> None:
    (tmp_path / "DummyStrategy.py").write_text("class DummyStrategy:\n    pass\n")
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("v")
    assert _source_lines(app._build_strategy_body()) == ["class DummyStrategy:", "    pass"]


def test_each_bot_shows_the_source_of_the_strategy_it_runs(monkeypatch, tmp_path) -> None:
    (tmp_path / "DummyStrategy.py").write_text("class DummyStrategy: ...\n")
    (tmp_path / "CandlePatternStrategy.py").write_text("class CandlePatternStrategy: ...\n")
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    bots_state._handle_status_message(_status("candle-01", strategy="CandlePatternStrategy"))
    app = BotTuiApp()
    app._open_bot_detail("candle-01")
    app._handle_bot_detail_key("v")
    assert _source_lines(app._build_strategy_body()) == ["class CandlePatternStrategy: ..."]


def test_build_strategy_body_handles_missing_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    assert _filler_text(app._build_strategy_body()).startswith(
        f"could not read {tmp_path / 'DummyStrategy.py'}"
    )


def test_build_strategy_body_names_a_bot_with_no_status_yet(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    app = BotTuiApp()
    app._open_bot_detail("bot-09")
    assert _filler_text(app._build_strategy_body()) == (
        "no bots:status received yet for bot-09: strategy unknown"
    )


def test_a_strategy_name_that_is_not_a_class_name_reads_no_file(monkeypatch, tmp_path) -> None:
    (tmp_path / "secret.py").write_text("never shown\n")
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path / "sources")
    _reset()
    bots_state._handle_status_message(_status("bot-01", strategy="../secret"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    assert "not a class name" in _filler_text(app._build_strategy_body())


def test_a_source_that_is_not_utf8_is_reported_not_raised(monkeypatch, tmp_path) -> None:
    (tmp_path / "DummyStrategy.py").write_bytes(b"\xff\xfe\xfa")
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    assert _filler_text(app._build_strategy_body()).startswith("could not read")


def test_esc_from_strategy_view_returns_to_bot_detail_without_clearing_it(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(app_module, "_STRATEGY_SOURCE_DIR", tmp_path)
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("v")
    app._handle_global_key("esc")
    assert app._view == "bot_detail"
    assert app._bot_detail_bot_id == "bot-01"


def test_i_key_opens_incidents_view_with_breadcrumb() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("i")
    assert app._view == "incidents"
    assert app._breadcrumb.text == "Bots > bot-01 > incidents"


def test_build_incidents_body_renders_fetched_incidents() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    bot_incidents_state._handle_incidents_payload(
        "bot-01", [{"type": "process_start", "started_at": 100.0, "ended_at": 100.0}]
    )
    app._handle_bot_detail_key("i")
    body = app._build_incidents_body()
    assert isinstance(body, urwid.ListBox)


def test_build_incidents_body_before_any_fetch_shows_unavailable() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    body = app._build_incidents_body()
    assert body.body[0].text == bots_pane.INCIDENTS_UNAVAILABLE_TEXT


def test_esc_from_incidents_view_returns_to_bot_detail_without_clearing_it() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("i")
    app._handle_global_key("esc")
    assert app._view == "bot_detail"
    assert app._bot_detail_bot_id == "bot-01"


def test_esc_from_bot_detail_also_stops_tracking_incidents() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    app._handle_bot_detail_key("esc")
    assert bot_incidents_state._TRACKED_BOT_ID is None


# --- Story 29.6: the position and exit lines ---


def _snapshot_texts(app: BotTuiApp) -> list[str]:
    body = app._build_bot_detail_body()
    assert isinstance(body, urwid.ListBox)
    snapshot_box = body.body[0]
    assert isinstance(snapshot_box, urwid.LineBox)
    pile = snapshot_box.original_widget
    assert isinstance(pile, urwid.Pile)
    return [widget.text for widget, _options in pile.contents]


def test_bot_detail_snapshot_shows_the_position_and_exit_lines() -> None:
    _reset()
    status = _status(
        "bot-01",
        position_side="long",
        stop_loss="58900.0",
        take_profit="61020.5",
        entry_price="60000.0",
        mark_price="59980.5",
        position_qty="0.001",
        stop_loss_orders=1,
        take_profit_orders=2,
        open_orders=3,
        last_fill_at=None,
    )
    bots_state._handle_status_message(status)
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    texts = _snapshot_texts(app)
    assert texts[3:] == bots_pane.bot_detail_lines(status, now=0.0)[3:]
    assert "take profit 61,020.5 (2 orders)" in texts[4]
    assert "no fills yet" in texts[4]


def test_bot_detail_snapshot_of_a_pre_story_message_says_n_a() -> None:
    _reset()
    bots_state._handle_status_message(_status("bot-01"))
    app = BotTuiApp()
    app._open_bot_detail("bot-01")
    texts = _snapshot_texts(app)
    assert len(texts) == 5
    assert texts[3].startswith("quantity   n/a")
    assert texts[4].startswith("stop loss  n/a")
