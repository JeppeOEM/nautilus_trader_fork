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
Tests for bot_tui.app.BotTuiApp's market browser (Story 29.5), headless like test_app_collector.py:
the `/` view and its live search, the `a` add flow end to end through a fake Redis client (the
exact `collector:control` bytes), the refusals before any send, the row markers following
`collector:status`, TUI-01's persistent ListBox with focus kept across a refresh, and Esc.
"""

import asyncio
import time
from typing import Self

import pytest
import urwid

from bot_tui import collector_state
from bot_tui import markets_state
from bot_tui.app import BotTuiApp
from bot_tui.app import _SelectableCollectorRow
from bot_tui.market_browser import ADD_ANSWER_TIMEOUT_SECONDS
from bot_tui.market_browser import COLD_OPEN_TEXT


_SOL = "SOLUSDT-LINEAR.BYBIT"
_BYBIT = [
    {"instrument_id": "BTCUSDT-LINEAR.BYBIT", "symbol": "BTC"},
    {"instrument_id": "BTCUSDT-SPOT.BYBIT", "symbol": "BTC"},
    {"instrument_id": _SOL, "symbol": "SOL"},
    {"instrument_id": "SOLUSDT-SPOT.BYBIT", "symbol": "SOL"},
]


def _publish_markets(venue: str, markets: list[dict], received_at: float | None = None) -> None:
    message = {"venue": venue, "ts": 1, "markets": markets}
    markets_state._handle_markets_message(message, now=received_at)


def _aggregate(venue: str = "BYBIT", **overrides: object) -> dict:
    """Return a 29.5 aggregate as `StatusPublisher` publishes it (uncapped Bybit by default)."""
    base: dict = {
        "unpinned_ids": [],
        "venue": venue,
        "cap": None,
        "accepts_commands": True,
        "min_liquidity_usd": None,
        "last_apply": None,
        "last_refusal": None,
    }
    return {**base, **overrides}


def _row(iid: str, **overrides: object) -> dict:
    return {"id": iid, "liquid": False, "last_trade_ts": 0, "trade_backfill": 0, **overrides}


def _status(*messages: dict) -> None:
    for message in messages:
        collector_state._handle_status_message(message)


def _browser(query: str | None = None) -> BotTuiApp:
    """Return an app on the Collector pane with `/` pressed, the search typed and submitted."""
    app = BotTuiApp()
    app._switch_view("collector", ["bots"])
    app._handle_collector_pane_key("/")
    if query is not None:
        app._markets_edit.set_edit_text(query)
        app._unhandled_input("enter")
    return app


def _lines(app: BotTuiApp) -> list[str]:
    body = app._markets_body
    assert isinstance(body, urwid.ListBox)
    return [w.original_widget.text if isinstance(w, urwid.AttrMap) else w.text for w in body.body]


def _row_line(app: BotTuiApp, iid: str) -> str:
    return next(line for line in _lines(app) if line[2:].startswith(iid))


def _focus(app: BotTuiApp, iid: str) -> None:
    body = app._markets_body
    assert isinstance(body, urwid.ListBox)
    for i, widget in enumerate(body.body):
        row = widget.original_widget if isinstance(widget, urwid.AttrMap) else None
        if isinstance(row, _SelectableCollectorRow) and row.instrument_id == iid:
            body.focus_position = i
            return
    raise AssertionError(f"{iid} not listed")


def _focused_id(app: BotTuiApp) -> str:
    body = app._markets_body
    assert isinstance(body, urwid.ListBox)
    return body.focus.original_widget.instrument_id


class _Client:
    """Stands in for `redis.asyncio.Redis`: records every publish, as the replay test does."""

    published: list[tuple[str, str]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(_Client, "published", published)
    monkeypatch.setattr(collector_state.aioredis.Redis, "from_url", lambda *_a, **_k: _Client())
    return published


def _confirm_add(app: BotTuiApp) -> None:
    """Type `add` + enter in the open guard, inside a loop so the publish task runs to the end."""

    async def _run() -> None:
        app._stop_confirm_edit.set_edit_text("add")
        app._unhandled_input("enter")
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(_run())


# -- the view ------------------------------------------------------------------------------------


def test_slash_before_any_markets_message_reads_waiting() -> None:
    app = _browser()
    assert (app._view, app._stack) == ("markets", ["bots", "collector"])
    assert app._markets_body.original_widget.text == COLD_OPEN_TEXT
    assert app._body.original_widget is app._markets_body
    assert app._markets_search_active is True
    assert app._frame.footer is app._markets_edit
    assert app._breadcrumb.text == "Collector > markets"


def test_typing_filters_the_results_live() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _publish_markets(
        "HYPERLIQUID", [{"instrument_id": "SOL-USD-PERP.HYPERLIQUID", "symbol": "SOL"}]
    )
    app = _browser()
    assert len([line for line in _lines(app) if line.startswith("  ")]) == 5

    app._markets_edit.keypress((40,), "s")
    app._markets_edit.keypress((40,), "O")

    lines = _lines(app)
    assert lines[0] == "BYBIT: 0 collected +0 pending · cap ? · 2 matches"
    assert [line.strip() for line in lines[1:3]] == [_SOL, "SOLUSDT-SPOT.BYBIT"]
    assert lines[4] == "HYPERLIQUID: 0 collected +0 pending · cap ? · 1 match"
    assert app._breadcrumb.text == "Collector > markets  /sO"


def test_enter_moves_to_the_results_and_slash_reopens_the_search_with_the_query() -> None:
    _publish_markets("BYBIT", _BYBIT)
    app = _browser("sol")
    assert (app._markets_search_active, app._frame.focus_position) == (False, "body")
    assert app._frame.footer is app._footer_hint
    assert app._footer_hint.text == "a add  / search  esc back  :q quit"

    app._unhandled_input("/")

    assert app._markets_search_active is True
    assert app._markets_edit.edit_text == "sol"


def test_esc_closes_the_search_keeping_the_query_then_pops_back_to_collector() -> None:
    _publish_markets("BYBIT", _BYBIT)
    app = _browser()
    app._markets_edit.set_edit_text("btc")

    app._unhandled_input("esc")

    assert (app._view, app._markets_search_active, app._markets_query) == ("markets", False, "btc")
    app._unhandled_input("esc")
    assert (app._view, app._stack) == ("collector", ["bots"])


# -- the add flow --------------------------------------------------------------------------------


def test_a_then_add_sends_start_with_the_venue_and_the_row_follows_collector_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _fake_redis(monkeypatch)
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)

    app._unhandled_input("a")
    assert (app._collector_confirm_active, app._collector_confirm_action) == (True, "add")
    assert published == []
    _confirm_add(app)

    assert published == [
        ("collector:control", '{"action": "start", "id": "SOLUSDT-LINEAR.BYBIT", "venue": "BYBIT"}')
    ]
    app._refresh_markets_body()
    assert _row_line(app, _SOL).endswith(" pending")
    _status(_row(_SOL), _aggregate())
    app._refresh_markets_body()
    assert _row_line(app, _SOL).endswith(" collected")


def test_a_refusal_from_the_collector_shows_its_reason_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_redis(monkeypatch)
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)
    app._unhandled_input("a")
    _confirm_add(app)

    refusal = {"ts": 5, "action": "start", "id": _SOL, "reason": "Cannot start: raced"}
    _status(_aggregate(last_refusal=refusal))
    app._refresh_markets_body()

    assert _row_line(app, _SOL).endswith(" failed: Cannot start: raced")


def test_an_add_whose_publish_failed_says_so_and_is_not_awaited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never reaching Redis must not read `pending`, then "no answer from BYBIT collector"."""

    def _unreachable(*_a: object, **_k: object) -> _Client:
        raise ConnectionError("redis down")

    monkeypatch.setattr(collector_state.aioredis.Redis, "from_url", _unreachable)
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)
    app._unhandled_input("a")
    _confirm_add(app)

    assert app._footer_hint.text == f"failed to send start {_SOL}: Redis publish failed"
    assert collector_state.sent_adds() == {}
    app._refresh_markets_body()
    assert not _row_line(app, _SOL).endswith(" pending")
    app._unhandled_input("a")
    assert app._collector_confirm_active is True


def test_j_and_k_move_the_selection_as_the_help_says() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)

    assert app._markets_body.keypress((80, 20), "j") is None
    assert _focused_id(app) == "SOLUSDT-SPOT.BYBIT"
    assert app._markets_body.keypress((80, 20), "k") is None
    assert _focused_id(app) == _SOL


def test_a_row_whose_subscribe_failed_says_it_is_retrying() -> None:
    _publish_markets("BYBIT", _BYBIT)
    last_apply = {"ts": 1_800_000_000_000_000_000, "subscribed": [], "unsubscribed": []}
    _status(_row(_SOL, pending=True), _aggregate(last_apply={**last_apply, "failed": [_SOL]}))
    app = _browser("sol")
    assert _row_line(app, _SOL).endswith(
        "pending · failed: subscribe failed in last apply 2027-01-15 08:00:00Z, retrying"
    )


def test_an_add_nothing_answers_reads_no_answer_and_may_be_sent_again() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    collector_state.record_sent_add(_SOL, time.monotonic() - ADD_ANSWER_TIMEOUT_SECONDS - 1)
    app = _browser("sol")
    _focus(app, _SOL)
    assert _row_line(app, _SOL).endswith(" no answer from BYBIT collector")

    app._unhandled_input("a")

    assert app._collector_confirm_active is True


@pytest.mark.parametrize(
    ("statuses", "reason"),
    [
        ([_row(_SOL), _aggregate()], "already collected"),
        ([_row(_SOL, pending=True), _aggregate()], "already in the plan (pending)"),
        ([_aggregate(unpinned_ids=[_SOL])], "excluded (unpinned): re-add with :start <ID>"),
        ([], "waiting for BYBIT plan on collector:status"),
    ],
)
def test_a_refused_add_shows_the_reason_and_sends_nothing(
    statuses: list[dict], reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = _fake_redis(monkeypatch)
    _publish_markets("BYBIT", _BYBIT)
    _status(*statuses)
    app = _browser("sol")
    _focus(app, _SOL)

    app._unhandled_input("a")

    assert app._collector_confirm_active is False
    assert app._footer_hint.text == f"cannot add {_SOL}: {reason}"
    assert published == []


def test_a_second_a_while_the_first_add_awaits_its_answer_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _fake_redis(monkeypatch)
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)
    app._unhandled_input("a")
    _confirm_add(app)

    app._unhandled_input("a")

    assert app._footer_hint.text == (
        f"cannot add {_SOL}: add already sent, waiting for collector:status"
    )
    assert len(published) == 1


def test_a_full_dydx_cap_refuses_the_add_before_any_send(monkeypatch: pytest.MonkeyPatch) -> None:
    published = _fake_redis(monkeypatch)
    _publish_markets("DYDX", [{"instrument_id": "NEW-USD-PERP.DYDX", "symbol": "NEW"}])
    _status(*(_row(f"C{i}-USD-PERP.DYDX") for i in range(30)), _aggregate("DYDX", cap=30))
    app = _browser("new")
    _focus(app, "NEW-USD-PERP.DYDX")

    app._unhandled_input("a")

    assert app._footer_hint.text == "cannot add NEW-USD-PERP.DYDX: cap reached (30)"
    assert published == []


def test_the_add_is_rechecked_when_the_confirm_is_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _fake_redis(monkeypatch)
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)
    app._unhandled_input("a")
    _status(_row(_SOL), _aggregate())  # collected from another TUI while typing

    _confirm_add(app)

    assert app._footer_hint.text == f"cannot add {_SOL}: already collected"
    assert published == []


# -- TUI-01: one persistent ListBox --------------------------------------------------------------


def test_the_header_count_follows_collector_status_without_a_reload() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_row("BTCUSDT-LINEAR.BYBIT"), _aggregate())
    app = _browser("sol")
    listbox = app._markets_body
    assert _lines(app)[0] == "BYBIT: 1 collected +0 pending · no cap · 2 matches"

    _status(_row("BTCUSDT-LINEAR.BYBIT"), _row(_SOL, pending=True), _aggregate())
    app._refresh_markets_body()

    assert app._markets_body is listbox
    assert _lines(app)[0] == "BYBIT: 1 collected +1 pending · no cap · 2 matches"


def test_focus_stays_on_its_id_across_a_refresh() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser()
    _focus(app, "SOLUSDT-SPOT.BYBIT")

    _publish_markets("BYBIT", [{"instrument_id": "AAAUSDT-SPOT.BYBIT", "symbol": "AAA"}, *_BYBIT])
    app._refresh_markets_body()

    assert _focused_id(app) == "SOLUSDT-SPOT.BYBIT"


def test_an_unchanged_refresh_keeps_every_row_widget() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser()
    body = app._markets_body
    assert isinstance(body, urwid.ListBox)
    before = list(body.body)

    app._refresh_markets_body()

    assert all(a is b for a, b in zip(before, body.body, strict=True))


def test_a_stale_venue_is_marked_and_an_expired_one_dropped() -> None:
    _publish_markets("BYBIT", _BYBIT, received_at=time.monotonic() - 200)
    app = _browser("sol")
    assert _row_line(app, _SOL).startswith("~ ")

    _publish_markets("BYBIT", _BYBIT, received_at=time.monotonic() - 1000)
    app._refresh_markets_body()

    assert app._markets_body.original_widget.text == COLD_OPEN_TEXT


def test_the_collector_pane_shows_the_last_refusal_line() -> None:
    refusal = {"ts": 1_800_000_000_000_000_000, "action": "start", "id": _SOL, "reason": "no"}
    _status(_aggregate(last_refusal=refusal))
    app = BotTuiApp()
    app._refresh_collector_body()
    texts = [w.text for w in app._collector_body.body if isinstance(w, urwid.Text)]
    assert f"last refusal 2027-01-15 08:00:00Z: start {_SOL}: no" in texts


def test_a_new_query_starts_on_its_first_match_not_the_old_position() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser()
    _focus(app, "SOLUSDT-SPOT.BYBIT")  # the last row

    app._unhandled_input("/")
    app._markets_edit.set_edit_text("btc")

    assert _focused_id(app) == "BTCUSDT-LINEAR.BYBIT"


def test_closing_the_search_clears_an_old_footer_echo() -> None:
    _publish_markets("BYBIT", _BYBIT)
    _status(_row(_SOL), _aggregate())
    app = _browser("sol")
    _focus(app, _SOL)
    app._unhandled_input("a")  # refused: already collected
    assert app._footer_hint.text.startswith("cannot add")

    app._unhandled_input("/")
    app._unhandled_input("esc")

    assert app._footer_hint.text == "a add  / search  esc back  :q quit"


def test_an_add_still_in_flight_counts_toward_dydx_s_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    published = _fake_redis(monkeypatch)
    new = [
        {"instrument_id": "NEW-USD-PERP.DYDX", "symbol": "NEW"},
        {"instrument_id": "NEXT-USD-PERP.DYDX", "symbol": "NEXT"},
    ]
    _publish_markets("DYDX", new)
    _status(*(_row(f"C{i}-USD-PERP.DYDX") for i in range(29)), _aggregate("DYDX", cap=30))
    app = _browser("ne")
    _focus(app, "NEW-USD-PERP.DYDX")
    app._unhandled_input("a")
    _confirm_add(app)  # 29 rows + this add in flight fill the cap

    _focus(app, "NEXT-USD-PERP.DYDX")
    app._unhandled_input("a")

    assert app._footer_hint.text == "cannot add NEXT-USD-PERP.DYDX: cap reached (30)"
    assert len(published) == 1


def test_a_wall_clock_jump_never_turns_a_pending_add_into_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DW-60: the add's send time and the browser's `now` are both monotonic."""
    _fake_redis(monkeypatch)
    _publish_markets("BYBIT", _BYBIT)
    _status(_aggregate())
    app = _browser("sol")
    _focus(app, _SOL)
    app._unhandled_input("a")
    _confirm_add(app)
    wall = time.time()
    monkeypatch.setattr(time, "time", lambda: wall + 3600.0)

    app._refresh_markets_body()

    assert _row_line(app, _SOL).endswith(" pending")
