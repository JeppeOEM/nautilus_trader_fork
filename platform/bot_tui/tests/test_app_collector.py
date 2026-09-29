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

import asyncio
import time

import pytest
import urwid

from bot_tui import archive_state
from bot_tui import collector_state
from bot_tui.app import BotTuiApp
from bot_tui.app import _SelectableCollectorRow
from bot_tui.collector_pane import COLD_OPEN_TEXT


def _reset() -> None:
    collector_state._LATEST_COLLECTOR_STATUS = {}
    collector_state._LATEST_RECEIVED_AT = {}
    collector_state._LATEST_PLANS = {}
    collector_state._PLAN_RECEIVED_AT = {}
    collector_state._REPUBLISHED_SINCE_PLAN = {}


def _instrument_rows(body: urwid.ListBox) -> int:
    """Count the selectable instrument rows only, not the section and trailing Text lines."""
    return len(_row_positions(body))


def _row_positions(body: urwid.ListBox) -> list[int]:
    """Walker positions of the instrument rows (each venue section starts with Text lines)."""
    return [i for i, w in enumerate(body.body) if isinstance(w, urwid.AttrMap)]


def _row_ids(body: urwid.ListBox) -> list[str]:
    return [body.body[i].original_widget.instrument_id for i in _row_positions(body)]


def _status(iid: str, **overrides: object) -> dict:
    base = {
        "id": iid,
        "liquid": True,
        "last_trade_ts": 1_000_000_000,
    }
    base.update(overrides)
    return base


def _recording_publishes(app: BotTuiApp) -> list[tuple[str, str | None]]:
    """Replace the real publish (it needs a running loop) with a recording list."""
    published: list[tuple[str, str | None]] = []
    app._publish_collector_action = lambda action, instrument_id, *_: published.append(  # type: ignore[method-assign]
        (action, instrument_id)
    )
    return published


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
    assert _instrument_rows(app._collector_body) == 2


def test_rows_sorted_by_id_and_selectable() -> None:
    _reset()
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._refresh_collector_body()
    body = app._collector_body
    assert all(
        isinstance(body.body[i].original_widget, _SelectableCollectorRow)
        for i in _row_positions(body)
    )
    assert _row_ids(body) == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]


def test_highlighted_collector_id_reads_listbox_focus() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message(_status("ETH-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    assert app._highlighted_collector_id() == "BTC-USD-PERP.DYDX"
    app._collector_body.focus_position = _row_positions(app._collector_body)[1]
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
    # A pre-29.2 aggregate (no `venue`: dYdX's): with none cached, every action is refused.
    collector_state._handle_status_message({"unpinned_ids": []})
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    return app


def test_p_opens_unpin_confirm_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published = _recording_publishes(app)

    app._toggle_pin()

    assert app._collector_confirm_active is True
    assert app._collector_confirm_action == "unpin"
    assert app._collector_confirm_id == "BTC-USD-PERP.DYDX"
    assert published == []


def test_typing_unpin_and_enter_confirms_and_publishes() -> None:
    app = _collector_app_with_one_row()
    published = _recording_publishes(app)
    app._toggle_pin()

    app._stop_confirm_edit.set_edit_text("unpin")
    app._submit_collector_confirm()

    assert app._collector_confirm_active is False
    assert published == [("unpin", "BTC-USD-PERP.DYDX")]


def test_x_opens_stop_confirm_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published = _recording_publishes(app)

    app._handle_collector_pane_key("x")

    assert app._collector_confirm_active is True
    assert app._collector_confirm_action == "stop"
    assert app._collector_confirm_id == "BTC-USD-PERP.DYDX"
    assert published == []


def test_typing_stop_and_enter_confirms_and_publishes() -> None:
    app = _collector_app_with_one_row()
    published = _recording_publishes(app)
    app._handle_collector_pane_key("x")

    app._stop_confirm_edit.set_edit_text("stop")
    app._submit_collector_confirm()

    assert app._collector_confirm_active is False
    assert published == [("stop", "BTC-USD-PERP.DYDX")]


def test_wrong_text_keeps_collector_confirm_open_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published = _recording_publishes(app)
    app._toggle_pin()

    app._stop_confirm_edit.set_edit_text("nope")
    app._submit_collector_confirm()

    assert app._collector_confirm_active is True
    assert app._collector_confirm_action == "unpin"
    assert published == []


def test_esc_cancels_collector_confirm_without_publishing() -> None:
    app = _collector_app_with_one_row()
    published = _recording_publishes(app)
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
    published = _recording_publishes(app)

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
    app._collector_body.focus_position = _row_positions(body_before)[-1]  # scroll to the last row

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
    assert _instrument_rows(app._collector_body) == 1


def test_refresh_collector_body_rebuilds_on_cold_open_to_populated_transition() -> None:
    _reset()
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    filler_before = app._collector_body

    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    app._refresh_collector_body()

    assert app._collector_body is not filler_before
    assert _instrument_rows(app._collector_body) == 1


def _archive_status() -> dict:
    return {
        "next_run": "2099-01-02T03:07:00Z",
        "running": None,
        "last_run": {
            "run_id": "r-1",
            "kind": "nightly",
            "day": "2098-12-31",
            "started": "2099-01-01T03:07:00Z",
            "finished": "2099-01-01T03:41:00Z",
            "steps": [{"venue": "BYBIT", "name": "reconcile", "exit": 0, "duration_s": 1.0}],
        },
    }


def test_collector_body_shows_archive_status_trailing_line() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    collector_state._handle_status_message({"unpinned_ids": ["ETH-USD-PERP.DYDX"]})
    archive_state._handle_status_message(_archive_status())
    app = BotTuiApp()
    app._refresh_collector_body()
    plain_texts = [w.text for w in app._collector_body.body if isinstance(w, urwid.Text)]
    assert plain_texts[-1].startswith("archive: last 2098-12-31 ok 03:07-03:41Z")
    assert any("ETH-USD-PERP.DYDX" in t for t in plain_texts[:-1])


def test_collector_body_says_no_archive_status_yet_before_any_message() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    app = BotTuiApp()
    app._refresh_collector_body()
    assert app._collector_body.body[-1].text == "archive: no status yet"


def test_archive_line_never_takes_focus_from_the_instrument_rows() -> None:
    _reset()
    collector_state._handle_status_message(_status("BTC-USD-PERP.DYDX"))
    archive_state._handle_status_message(_archive_status())
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    assert app._highlighted_collector_id() == "BTC-USD-PERP.DYDX"


def test_cold_open_shows_the_archive_line_and_updates_it_in_place() -> None:
    _reset()
    app = BotTuiApp()
    app._refresh_collector_body()
    filler_before = app._collector_body
    assert "archive: no status yet" in filler_before.original_widget.text

    archive_state._handle_status_message(_archive_status())
    app._refresh_collector_body()

    assert app._collector_body is filler_before
    text = app._collector_body.original_widget.text
    assert COLD_OPEN_TEXT in text
    assert "archive: last 2098-12-31 ok" in text


def test_archive_line_refresh_keeps_the_listbox_and_focus() -> None:
    _reset()
    for iid in ("AAA-USD-PERP.DYDX", "BTC-USD-PERP.DYDX"):
        collector_state._handle_status_message(_status(iid))
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    body_before = app._collector_body
    app._collector_body.focus_position = _row_positions(body_before)[1]

    archive_state._handle_status_message(_archive_status())
    app._refresh_collector_body()

    assert app._collector_body is body_before
    assert app._highlighted_collector_id() == "BTC-USD-PERP.DYDX"


# --- Story 29.2: one section per venue; actions only where the plan accepts commands ---

_BYBIT_ROW = "BTCUSDT-LINEAR.BYBIT"


def _plan(venue: str, **overrides: object) -> dict:
    base: dict = {
        "unpinned_ids": [],
        "venue": venue,
        "cap": 4,
        "accepts_commands": False,
        "min_liquidity_usd": None,
        "last_apply": None,
    }
    base.update(overrides)
    return base


def _dydx_plan(**overrides: object) -> dict:
    return _plan(
        "DYDX", **{"cap": 30, "accepts_commands": True, "min_liquidity_usd": 20000.0, **overrides}
    )


def _collector_app(*messages: dict) -> BotTuiApp:
    _reset()
    for message in messages:
        collector_state._handle_status_message(message)
    app = BotTuiApp()
    app._switch_view("collector", [])
    app._refresh_collector_body()
    app._body.original_widget = app._collector_body
    return app


def _texts(app: BotTuiApp) -> list[str]:
    """Every line of the pane in order, rows included (a row's Text sits inside its AttrMap)."""
    return [
        (w.original_widget if isinstance(w, urwid.AttrMap) else w).text
        for w in app._collector_body.body
    ]


def _submit(app: BotTuiApp, text: str) -> str:
    """Submit `text` on the command bar; return the caption it left (the refusal, if any)."""
    app._open_command_bar()
    app._command_edit.set_edit_text(text)
    app._submit_command()
    return app._command_edit.caption


def test_every_venue_gets_a_section_sorted_with_the_archive_line_last() -> None:
    app = _collector_app(
        _status("BTC-USD-PERP.DYDX", liquid=True),
        _status(_BYBIT_ROW, liquid=False),
        _status("SOL-USD-PERP.HYPERLIQUID", liquid=False, pending=True),
        _dydx_plan(unpinned_ids=["AAA-USD-PERP.DYDX"]),
        _plan("BYBIT"),
        _plan("HYPERLIQUID", cap=1),
    )
    texts = _texts(app)
    headers = [t for t in texts if ": " in t and "collected" in t]
    assert headers == [
        "BYBIT: 1 collected +0 pending · cap 4",
        "DYDX: 1 collected +0 pending · cap 30",
        "HYPERLIQUID: 0 collected +1 pending · cap 1",
    ]
    assert "  BTC-USD-PERP.DYDX            liquid  " in texts
    assert f"  {_BYBIT_ROW:<28}" in texts  # no liquidity label for a static plan
    assert "  SOL-USD-PERP.HYPERLIQUID     pending" in texts
    assert "unpinned (add back with :start <ID>): AAA-USD-PERP.DYDX" in texts
    assert "BYBIT: static plan: edit platform/capture/venues/bybit/config.toml" in texts
    assert texts[-1] == "archive: no status yet"


def test_p_and_x_on_a_static_plan_row_are_refused_in_the_footer() -> None:
    app = _collector_app(_status(_BYBIT_ROW), _plan("BYBIT"))
    published = _recording_publishes(app)
    for key in ("p", "x"):
        app._handle_collector_pane_key(key)
        assert app._collector_confirm_active is False
        assert "BYBIT: static plan: edit platform/capture/venues/bybit/config.toml" in (
            app._footer_hint.text
        )
    assert published == []


def test_start_of_a_static_plan_id_is_refused() -> None:
    app = _collector_app(_status(_BYBIT_ROW), _plan("BYBIT"))
    published = _recording_publishes(app)
    caption = _submit(app, "start ETHUSDC-SPOT.BYBIT")
    assert caption == (
        "cannot start ETHUSDC-SPOT.BYBIT: BYBIT: static plan: edit "
        "platform/capture/venues/bybit/config.toml\n:"
    )
    assert published == []


def test_start_at_the_dydx_cap_counts_only_dydx_rows() -> None:
    dydx_rows = [_status(f"C{i}-USD-PERP.DYDX") for i in range(29)]
    app = _collector_app(*dydx_rows, _status(_BYBIT_ROW), _dydx_plan(), _plan("BYBIT"))
    published = _recording_publishes(app)
    _submit(app, "start NEW-USD-PERP.DYDX")  # 29 of 30: the Bybit row does not count
    assert published == [("start", "NEW-USD-PERP.DYDX")]

    collector_state._handle_status_message(_status("NEW-USD-PERP.DYDX", pending=True))
    caption = _submit(app, "start MORE-USD-PERP.DYDX")
    assert caption == "cannot start MORE-USD-PERP.DYDX: at 30-instrument cap\n:"
    assert published == [("start", "NEW-USD-PERP.DYDX")]


def test_an_older_aggregate_is_dydx_s_with_an_unknown_cap_and_actions_allowed() -> None:
    app = _collector_app(_status("BTC-USD-PERP.DYDX"), {"unpinned_ids": ["X-USD-PERP.DYDX"]})
    texts = _texts(app)
    assert texts[0] == "DYDX: 1 collected +0 pending · cap ?"
    assert "unpinned (add back with :start <ID>): X-USD-PERP.DYDX" in texts
    published = _recording_publishes(app)
    _submit(app, "start NEW-USD-PERP.DYDX")  # no cap known: the collector checks it
    assert published == [("start", "NEW-USD-PERP.DYDX")]
    app._toggle_pin()
    assert app._collector_confirm_active is True


def test_pintop_is_refused_when_the_dydx_plan_does_not_accept_commands() -> None:
    app = _collector_app(_dydx_plan(accepts_commands=False))
    published = _recording_publishes(app)
    caption = _submit(app, "pintop")
    assert caption.startswith("cannot pintop: DYDX: static plan")
    assert published == []


def test_a_venue_with_only_an_aggregate_shows_its_empty_section() -> None:
    app = _collector_app(_plan("HYPERLIQUID", cap=0))
    assert _texts(app)[0] == "HYPERLIQUID: 0 collected +0 pending · cap 0"
    assert app._highlighted_collector_id() is None


def test_rows_without_their_venue_s_aggregate_wait_for_it() -> None:
    app = _collector_app(_status(_BYBIT_ROW))
    assert "waiting for BYBIT plan on collector:status" in _texts(app)
    published = _recording_publishes(app)
    app._toggle_pin()
    assert app._collector_confirm_active is False
    assert published == []


def test_a_malformed_id_is_grouped_under_unknown_and_refused() -> None:
    app = _collector_app(_status("BTCUSDT"))
    assert _texts(app)[0] == "UNKNOWN: 1 collected +0 pending · cap ?"
    published = _recording_publishes(app)
    app._toggle_pin()
    assert app._collector_confirm_active is False
    assert "unknown venue" in app._footer_hint.text
    assert _submit(app, "start BTCUSDT").startswith("cannot start BTCUSDT: unknown venue")
    assert published == []


def test_a_stale_aggregate_marks_its_section_header() -> None:
    app = _collector_app(_plan("BYBIT"))
    collector_state._PLAN_RECEIVED_AT["BYBIT"] -= collector_state._STATUS_STALE_SECONDS + 1
    app._refresh_collector_body()
    assert _texts(app)[0].startswith("~ BYBIT: ")


def test_the_last_apply_line_shows_under_its_header() -> None:
    last_apply = {"ts": 1_790_000_000_000_000_000, "subscribed": [], "unsubscribed": []}
    app = _collector_app(_plan("BYBIT", last_apply={**last_apply, "failed": [_BYBIT_ROW]}))
    assert _texts(app)[1] == (
        f"last apply 2026-09-21 14:13:20Z: 0 subscribed, 0 unsubscribed, 1 failed ({_BYBIT_ROW})"
    )


def test_focus_on_a_section_line_highlights_no_row() -> None:
    app = _collector_app(_status("BTC-USD-PERP.DYDX"))
    app._collector_body.focus_position = 0  # the DYDX header
    assert app._highlighted_collector_id() is None


def test_focus_follows_its_row_when_a_section_line_appears_above_it() -> None:
    app = _collector_app(_status(_BYBIT_ROW), _status("BTC-USD-PERP.DYDX"), _dydx_plan())
    body = app._collector_body
    body.focus_position = _row_positions(body)[1]
    assert app._highlighted_collector_id() == "BTC-USD-PERP.DYDX"
    collector_state._handle_status_message(_plan("BYBIT"))  # its "waiting" line becomes two
    app._refresh_collector_body()
    assert app._highlighted_collector_id() == "BTC-USD-PERP.DYDX"


def test_a_row_its_venue_did_not_republish_is_dropped_by_the_next_aggregate() -> None:
    app = _collector_app(_status(_BYBIT_ROW), _status("ETHUSDT-LINEAR.BYBIT"), _plan("BYBIT"))
    # The collector restarted from an edited plan: only ETH is republished, then the aggregate.
    collector_state._handle_status_message(_status("ETHUSDT-LINEAR.BYBIT"))
    collector_state._handle_status_message(_plan("BYBIT"))
    app._refresh_collector_body()
    assert _row_ids(app._collector_body) == ["ETHUSDT-LINEAR.BYBIT"]


def test_the_row_sweep_orders_by_arrival_not_by_the_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _collector_app(_status(_BYBIT_ROW), _plan("BYBIT"))
    # NTP steps the clock back between the republished row and its aggregate.
    monkeypatch.setattr(collector_state.time, "time", lambda: 1_000.0)
    collector_state._handle_status_message(_status(_BYBIT_ROW))
    monkeypatch.setattr(collector_state.time, "time", lambda: 999.0)
    collector_state._handle_status_message(_plan("BYBIT"))
    app._refresh_collector_body()
    assert _row_ids(app._collector_body) == [_BYBIT_ROW]


def test_a_confirm_is_refused_when_the_plan_stops_accepting_commands_meanwhile() -> None:
    app = _collector_app(_status("BTC-USD-PERP.DYDX"), _dydx_plan())
    published = _recording_publishes(app)
    app._handle_collector_pane_key("x")
    collector_state._handle_status_message(_dydx_plan(accepts_commands=False))
    app._stop_confirm_edit.set_edit_text("stop")
    app._submit_collector_confirm()
    assert published == []
    assert "DYDX: static plan" in app._footer_hint.text


# -- Story 29.4: Bybit/Hyperliquid take commands, every command carries its venue ----------------


def _bybit_live_plan() -> dict:
    return _plan("BYBIT", cap=None, accepts_commands=True)


def test_an_uncapped_commandable_plan_publishes_a_start_with_no_cap_check() -> None:
    rows = [_status(f"C{i}USDT-LINEAR.BYBIT") for i in range(40)]
    app = _collector_app(*rows, _bybit_live_plan())
    assert _texts(app)[0] == "BYBIT: 40 collected +0 pending · no cap"
    published = _recording_publishes(app)
    _submit(app, "start SOLUSDT-LINEAR.BYBIT")
    assert published == [("start", "SOLUSDT-LINEAR.BYBIT")]


def test_a_published_action_is_addressed_to_its_ids_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    sent: list[tuple[str, str | None, str | None]] = []

    async def _publish(
        _url: str, action: str, instrument_id: str | None = None, venue: str | None = None
    ) -> None:
        sent.append((action, instrument_id, venue))

    monkeypatch.setattr(collector_state, "publish_control", _publish)
    app = BotTuiApp()

    async def _run() -> None:
        app._publish_collector_action("start", "SOL-USD-PERP.HYPERLIQUID")
        app._publish_collector_action("unpin", "BTCUSDT-SPOT.BYBIT")
        app._publish_collector_action("pin_top_liquid", None)
        await asyncio.gather(*list(app._background_tasks))

    asyncio.run(_run())
    assert sent == [
        ("start", "SOL-USD-PERP.HYPERLIQUID", "HYPERLIQUID"),
        ("unpin", "BTCUSDT-SPOT.BYBIT", "BYBIT"),
        ("pin_top_liquid", None, "DYDX"),
    ]


def test_a_plan_whose_status_went_stale_refuses_commands() -> None:
    app = _collector_app(_status(_BYBIT_ROW), _bybit_live_plan())
    collector_state._PLAN_RECEIVED_AT["BYBIT"] = time.time() - 3601
    published = _recording_publishes(app)
    caption = _submit(app, "start SOLUSDT-LINEAR.BYBIT")
    assert caption == (
        "cannot start SOLUSDT-LINEAR.BYBIT: BYBIT: no collector:status for over 60 min "
        "(collector down?)\n:"
    )
    assert published == []


def test_a_venue_with_no_aggregate_cached_refuses_commands_even_for_dydx() -> None:
    """After `make down-dydx`, a TUI started later has no dYdX aggregate: nothing is sent."""
    app = _collector_app(_status("BTC-USD-PERP.DYDX"))
    published = _recording_publishes(app)
    caption = _submit(app, "start SOL-USD-PERP.DYDX")
    assert caption == (
        "cannot start SOL-USD-PERP.DYDX: waiting for DYDX plan on collector:status\n:"
    )
    assert published == []


def test_a_venue_token_no_venue_registers_is_refused_as_unknown() -> None:
    """A lowercase suffix is not a venue that could still publish: no "waiting for" reason."""
    app = _collector_app(_bybit_live_plan())
    published = _recording_publishes(app)
    caption = _submit(app, "start eth-usd-perp.hyperliquid")
    assert caption.startswith("cannot start eth-usd-perp.hyperliquid: unknown venue 'hyperliquid'")
    assert published == []
