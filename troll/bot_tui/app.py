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
bot_tui urwid app shell (Story 4.1, AC1-AC4; Story 4.2, AC1-AC5; Story 4.3, AC1-AC7;
Story 4.4, AC1-AC4; Story 4.7, AC1-AC4): MainLoop wiring, breadcrumb/footer, Coins
pane, the `:` command bar, the `/` inline filter, the `m` Ranking-Mode toggle, a
pane-level stale badge, a full-screen Coin-detail view (live indicators + collapsible
order-book ladder + `o` dashboard deep-link), a Bots pane (live per-bot PnL/status
rows, per-row stale badges, `s` start/stop with a footer-echo confirmation), a
full-screen Bot-detail view (live-snapshot header, `t`-cycled trades blotter + PnL
sparkline sourced from Story 4.6's bots:history:* keys, `o` dashboard deep-link), and
`esc`/`:q` navigation.

`s` on a running bot does not stop it immediately -- it opens a type-to-confirm prompt
(operator must type "stop" + Enter) before the stop command is published, per an
explicit operator request: a bare `s` keypress is too easy to hit by accident to let it
directly stop a live/paper bot. Starting a stopped bot has no such guard -- only
stopping a running one carries real-world consequence.

This is the only file in this story allowed to import urwid and hold live async
state. Command dispatch and view-stack pop-back are implemented as plain, urwid-free
functions (_dispatch_command / _pop_view) so they're unit-testable without a real
screen -- see Story 4.1's Dev Notes "Testing strategy: pure logic vs. urwid wiring."

Story 4.2 registered this product's first real urwid palette entry ("stale", yellow-
on-default). Story 4.3 adds "bid"/"ask" (green/red) for the order-book ladder.

Coin-detail (Story 4.3) always shows only the single most-recently-received
snapshots:raw row for the open instrument -- there is no stored history, no time-
index, no "as of" state anywhere in this view or in coin_detail_state.py. No future
extension of this view should add a time-range picker, scrub bar, or "go to
timestamp" control (AC7) -- historical/graph analysis for market data stays the web
dashboard's job via the `o` deep-link.
"""

import asyncio
import logging
import os
import time
import webbrowser

import urwid

from bot_tui import bot_history_state
from bot_tui import bots_pane
from bot_tui import bots_state
from bot_tui import coin_detail
from bot_tui import coin_detail_state
from bot_tui import ranking_state
from bot_tui.coins_pane import COLD_OPEN_TEXT
from bot_tui.coins_pane import NO_MATCHES_TEXT
from bot_tui.coins_pane import coin_rows
from bot_tui.coins_pane import filter_rows


logger = logging.getLogger(__name__)

_BREADCRUMB_LABELS = {"coins": "Coins", "bots": "Bots"}

# "/"/"m" (Story 4.2) and now Enter (Story 4.3, opens Coin-detail) do something real
# on the Coins pane -- advertised alongside the Story 4.1 keys. `j`/`k` row-focus
# movement is still "free" via urwid.ListBox and still isn't advertised as a distinct
# feature.
_FOOTER_HINT_TEXT = "/ filter  m mode  : command  esc back  :q quit"

# Coin-detail's own footer -- distinct keys, distinct hints (Story 4.3).
_COIN_DETAIL_FOOTER_HINT_TEXT = "d expand book  o dashboard  esc back  :q quit"

# Bots pane's own footer (Story 4.4) -- `/`/`m`/Enter are Coins-pane-only (see
# _handle_global_key), `s` is Bots-pane-only.
_BOTS_FOOTER_HINT_TEXT = "s start/stop  : command  esc back  :q quit"

# Bot-detail's own footer (Story 4.5 added s/esc; Story 4.7 adds t/o for the new
# blotter/PnL-sparkline regions). No j/k (scroll) hint -- the blotter's own ListBox
# scrolling is "free" the same way the Coins-pane's j/k movement already is.
_BOT_DETAIL_FOOTER_HINT_TEXT = "s start/stop  t range  o dashboard  esc back  :q quit"

_PALETTE = [
    ("stale", "yellow", "default"),
    ("bid", "dark green", "default"),
    ("ask", "dark red", "default"),
    ("pnl-pos", "dark green", "default"),
    ("pnl-neg", "dark red", "default"),
]

_LADDER_COLLAPSED_LEVELS = 1
_LADDER_EXPANDED_LEVELS = 20

# Bot-detail's trades-blotter region (Story 4.7) -- a fixed visible height inside a
# scrollable ListBox (BoxAdapter), not "however many fills happen to exist" (Story
# 4.6 already caps the wire contract at 500 trades; this caps what's visible at once
# on screen, independently, via ordinary urwid scrolling for the rest).
_BOT_DETAIL_BLOTTER_HEIGHT = 8

# Returned by _dispatch_command to signal `:q` -- kept out of urwid's ExitMainLoop so
# this function stays a plain, urwid-free, directly-testable function.
_QUIT_SENTINEL = "__quit__"

_RECOGNIZED_COMMANDS = frozenset(_BREADCRUMB_LABELS)

# Poll interval for picking up a new rankings:live message and redrawing -- urwid does
# not auto-redraw for state changed by a background asyncio.Task (see urwid's Main
# Loop docs: "you must call MainLoop.draw_screen() manually"), so app.py owns a small
# loop that notices a new _LATEST_RANKING object and triggers a redraw.
_REDRAW_POLL_SECONDS = 0.5


def _dispatch_command(
    current_view: str, stack: list[str], text: str
) -> tuple[str, list[str], str | None]:
    """
    Parse a typed command-bar string (AC3).

    Returns (new_view, new_stack, echo_message). echo_message is None on success, or
    "unknown command: {input}" for anything unrecognized -- the caller keeps the
    command bar open in that case rather than closing it (AC3's literal requirement).
    `:q` is reported via _QUIT_SENTINEL rather than raising here, so this function has
    no urwid dependency and can be tested directly.
    """
    command = text.strip()
    if command == "q":
        return _QUIT_SENTINEL, stack, None
    if command in _RECOGNIZED_COMMANDS:
        if command == current_view:
            return current_view, stack, None
        return command, [*stack, current_view], None
    return current_view, stack, f"unknown command: {text}"


def _pop_view(current_view: str, stack: list[str]) -> tuple[str, list[str]]:
    """Esc pops back exactly one level; never raises, no-ops at the root (AC4)."""
    if not stack:
        return current_view, []
    return stack[-1], stack[:-1]


def _format_coin_row(rank: int, instrument_id: str, score: float) -> str:
    return f"{rank:>3}  {instrument_id:<20} {score:>12.4f}"


class _SelectableCoinRow(urwid.Text):
    """
    A coin row that ListBox can move focus to/from, but that never itself consumes a
    keypress (Story 4.3, AC1). Plain urwid.Text.selectable() is False -- verified
    directly against urwid==4.0.6 that a ListBox of plain Text rows never moves
    focus_position on "up"/"down" at all. Returning `key` unchanged from keypress()
    (rather than None) means ListBox handles up/down navigation itself, and Enter
    bubbles all the way to unhandled_input unconsumed, exactly like every other
    currently-unhandled key already does.
    """

    def __init__(self, text: str, instrument_id: str) -> None:
        super().__init__(text)
        self.instrument_id = instrument_id

    def selectable(self) -> bool:
        return True

    def keypress(self, size: object, key: str) -> str:
        return key


class _SelectableBotRow(urwid.Text):
    """
    Same selectable-but-non-consuming shape as _SelectableCoinRow (Story 4.3),
    applied to Bots-pane rows so `s` can act on whichever row is highlighted.
    """

    def __init__(self, markup: object, bot_id: str) -> None:
        # markup is really urwid's own private _TagMarkup union (str | tuple | list),
        # not importable from outside urwid -- same stub-gap precedent Story 4.3's
        # review already established (scoped type: ignore, not a broader Any).
        super().__init__(markup)  # type: ignore[arg-type]
        self.bot_id = bot_id

    def selectable(self) -> bool:
        return True

    def keypress(self, size: object, key: str) -> str:
        return key


class BotTuiApp:
    """Owns the urwid Frame, the view stack, and the async wiring to ranking_state."""

    def __init__(self, redis_url: str = ranking_state.REDIS_URL) -> None:
        self._redis_url = redis_url
        self._view = "coins"
        self._stack: list[str] = []
        self._command_active = False
        self._filter_active = False
        self._filter_text = ""
        self._last_seen_ranking: dict | None = None
        self._background_tasks: set[asyncio.Task] = set()

        # Coin-detail state (Story 4.3).
        self._coin_detail_instrument_id: str | None = None
        self._ladder_expanded = False
        self._dashboard_base_url = os.environ.get("DASHBOARD_BASE_URL", "http://127.0.0.1:8765")

        # Bot-detail state (Story 4.5). No open_bot()/close_bot() lifecycle pair is
        # needed here, unlike coin_detail_state's open_coin()/close_coin() -- bots:status
        # is already accumulated unconditionally for every bot regardless of which view
        # is active (see _open_bot_detail's own comment).
        self._bot_detail_bot_id: str | None = None

        # Bot-detail's history state (Story 4.7). Unlike the bots:status snapshot
        # above, bots:history *does* have an explicit open/close lifecycle
        # (bot_history_state.open_bot()/close_bot()) -- see that module's own
        # docstring for why. Always resets to "day" on a fresh Bot-detail open
        # (_open_bot_detail), never carried over from a previous bot, mirroring
        # _ladder_expanded's identical reset-on-entry discipline for Coin-detail.
        self._bot_history_range: str = "day"

        # Stop-confirmation guard: active while the operator must type "stop" + Enter
        # to actually stop the bot named by _stop_confirm_bot_id (captured at open
        # time so it can't drift if state changes while the prompt is up -- nothing
        # else can happen while it's active, every key is captured below).
        self._stop_confirm_active = False
        self._stop_confirm_bot_id: str | None = None

        # The Coins-pane body is a persistent object, mutated in place rather than
        # rebuilt on every view switch (Story 4.3, AC6 -- see _refresh_coins_body).
        # _coins_shape is None until the first _refresh_coins_body() call below builds
        # it for real; that sentinel guarantees the first call always constructs
        # fresh rather than skipping because "cold_open" happens to already match.
        self._coins_shape: str | None = None
        self._coins_body: urwid.Widget = urwid.Filler(urwid.Text(COLD_OPEN_TEXT), valign="top")
        self._refresh_coins_body()

        self._breadcrumb = urwid.Text(_BREADCRUMB_LABELS[self._view])
        self._footer_hint = urwid.Text(_FOOTER_HINT_TEXT)
        self._command_edit = urwid.Edit(":")
        self._filter_edit = urwid.Edit("/")
        urwid.connect_signal(self._filter_edit, "change", self._on_filter_change)
        self._stop_confirm_edit = urwid.Edit("")
        self._body = urwid.WidgetPlaceholder(self._build_body())
        self._frame = urwid.Frame(
            header=self._breadcrumb,
            body=self._body,
            footer=self._footer_hint,
        )

        self._main_loop: urwid.MainLoop | None = None

    def _refresh_coins_body(self) -> None:
        """
        Rebuild/mutate self._coins_body to reflect the Coins pane's own current data
        (a new rankings:live message, a filter-text change) -- never called merely
        because the active view switched to or from Coins (Story 4.3, AC6). Only the
        three genuinely-different-shape transitions (cold-open Filler <-> no-matches
        Filler <-> populated ListBox) swap self._coins_body's type; a same-shape
        update (new rows, same non-empty ListBox) mutates the existing
        ListBox/SimpleListWalker in place instead, which is what actually preserves
        scroll/focus position across an unrelated view round-trip -- constructing a
        brand-new ListBox object here would reset it every time, silently
        reintroducing the exact regression Story 4.2's review already fixed once for
        the redraw loop's own rebuild.
        """
        ranking = ranking_state._LATEST_RANKING
        if ranking is None:
            # True cold-open (AC5, Story 4.1): no message has ever arrived. Distinct
            # from a received message whose "ranks" happens to be empty -- that
            # renders as an empty ListBox, not cold-open.
            self._set_coins_filler(COLD_OPEN_TEXT, "cold_open")
            return

        rows = coin_rows(ranking)
        if self._filter_text:
            # Narrowing is keyed on filter_text alone, not _filter_active -- a
            # confirmed filter (see _confirm_filter) keeps narrowing the list after
            # focus has already returned to the body, which is exactly what lets
            # Enter reach a filtered row for Story 4.3's Coin-detail hand-off.
            rows = filter_rows(rows, self._filter_text)
            if not rows:
                # Not hidden, just empty (Story 4.2, AC1) -- same Filler shape as
                # cold-open.
                self._set_coins_filler(NO_MATCHES_TEXT, "no_matches")
                return

        self._set_coins_rows(rows)

    def _set_coins_filler(self, text: str, shape: str) -> None:
        if self._coins_shape != shape:
            self._coins_body = urwid.Filler(urwid.Text(text), valign="top")
            self._coins_shape = shape

    def _set_coins_rows(self, rows: list[tuple[int, str, float]]) -> None:
        widgets = [_SelectableCoinRow(_format_coin_row(*row), instrument_id=row[1]) for row in rows]
        if self._coins_shape != "rows":
            self._coins_body = urwid.ListBox(urwid.SimpleListWalker(widgets))
            self._coins_shape = "rows"
        else:
            # Slice-assignment on the existing SimpleListWalker -- verified directly
            # against urwid==4.0.6 that this preserves/auto-clamps focus_position
            # rather than resetting it, unlike constructing a new ListBox.
            listbox = self._coins_body
            assert isinstance(listbox, urwid.ListBox)
            # listbox.body is typed as the abstract ListWalker (no indexed assignment
            # in the stub), though the concrete SimpleListWalker built above supports
            # slice-assignment at runtime (same stub gap as test_app_body.py's
            # ListBox.body iteration).
            listbox.body[:] = widgets  # type: ignore[index]

    def _highlighted_instrument_id(self) -> str | None:
        """Return the Coins-pane row currently focused, or None if empty/not a ListBox."""
        if not isinstance(self._coins_body, urwid.ListBox):
            return None
        focus_widget = self._coins_body.focus
        if focus_widget is None:
            return None
        assert isinstance(focus_widget, _SelectableCoinRow)
        return focus_widget.instrument_id

    def _build_body(self) -> urwid.Widget:
        if self._view == "bots":
            return self._build_bots_body()

        if self._view == "coin_detail":
            return self._build_coin_detail_body()

        if self._view == "bot_detail":
            return self._build_bot_detail_body()

        # "coins" -- returned as-is, whatever it currently holds. Never rebuilt here;
        # only _refresh_coins_body() (called when the Coins pane's own data actually
        # changes, not on a mere view switch) ever changes what this holds.
        return self._coins_body

    def _build_bots_body(self) -> urwid.Widget:
        # Unlike the Coins pane (Story 4.3, AC6), this is rebuilt fresh on every call --
        # a deliberate, scoped-out YAGNI choice: this story has no drill-in/esc round
        # trip to preserve scroll position across yet (Bot-detail's Enter/esc is Story
        # 4.5), so a persistent-body refactor here would be speculative. Known
        # consequence: scroll position resets on every bots:status heartbeat-driven
        # rebuild (~5s) -- acceptable for now, revisit if/when Story 4.5 actually needs
        # a round trip through this pane the way Story 4.3 needed one through Coins.
        statuses = bots_state._LATEST_STATUSES
        if not statuses:
            return urwid.Filler(urwid.Text(bots_pane.COLD_OPEN_TEXT), valign="top")

        now = time.time()
        rows = bots_pane.bot_rows(statuses)
        widgets = [
            self._build_bot_row_widget(row, bots_state.is_stale(row["bot_id"], now=now), now)
            for row in rows
        ]
        return urwid.ListBox(urwid.SimpleListWalker(widgets))

    def _build_bot_row_widget(self, row: dict, stale: bool, now: float) -> urwid.Widget:
        # Color applied only to the PnL segment (sign, not magnitude) -- mirrors Story
        # 4.3's bid/ask ladder-column precedent of "fixed position + color, never color
        # alone"; the sign is also always in the text itself via bots_pane.format_pnl.
        pnl_value = row["realized_pnl"] + row["unrealized_pnl"]
        pnl_color = "pnl-pos" if pnl_value >= 0 else "pnl-neg"
        prefix = "~ " if stale else "  "
        running_text = "run" if row["running"] else "off"
        markup = [
            prefix,
            f"{row['bot_id']:<12} ",
            (pnl_color, bots_pane.format_pnl(pnl_value)),
            f"  {row['symbol']:<18} {row['mode']:<5} {running_text:<3} {row['position_side']:<5} "
            f"{bots_pane.format_exposure(row['net_exposure'])}  "
            f"up {bots_pane.format_uptime(row['started_at'], now)}  "
            f"wr {bots_pane.format_win_rate(row['win_rate'])}",
        ]
        return _SelectableBotRow(markup, bot_id=row["bot_id"])

    def _highlighted_bot_id(self) -> str | None:
        """
        Return the Bots-pane row currently focused, mirroring
        _highlighted_instrument_id's (Story 4.3) same read-the-ListBox's-own-focus
        pattern, applied to the Bots pane.
        """
        body = self._body.original_widget
        if not isinstance(body, urwid.ListBox):
            return None
        focus_widget = body.focus
        if focus_widget is None:
            return None
        assert isinstance(focus_widget, _SelectableBotRow)
        return focus_widget.bot_id

    def _active_bot_id(self) -> str | None:
        """
        Which bot `s` (start/stop) should act on -- the open bot while in Bot-detail,
        otherwise the Bots-pane's currently-highlighted row (Story 4.5, AC3). A single
        seam `_toggle_bot()` reads through, rather than two forked copies of that
        method for the two views it's reachable from.
        """
        if self._view == "bot_detail":
            return self._bot_detail_bot_id
        return self._highlighted_bot_id()

    def _build_bot_detail_body(self) -> urwid.Widget:
        # Guarded defensively (unlike most of this codebase's AD-3 "readers trust the
        # gate" precedent): Enter is only reachable from an already-status-backed row,
        # so status should never actually be None here, but this method has no
        # pre-existing None-threaded shape to inherit that discipline from the way
        # _build_coin_detail_body does (Story 4.5, Dev Notes item 7).
        bot_id = self._bot_detail_bot_id
        status = bots_state._LATEST_STATUSES.get(bot_id) if bot_id is not None else None
        if status is None:
            return urwid.Filler(urwid.Text("no status yet"), valign="top")

        pnl_value = status["realized_pnl"] + status["unrealized_pnl"]
        pnl_color = "pnl-pos" if pnl_value >= 0 else "pnl-neg"
        lines = bots_pane.bot_detail_lines(status, now=time.time())
        # Color only the PnL segment within line 2 -- same "fixed position + color,
        # never color alone" precedent as the Bots-pane row (_build_bot_row_widget)
        # and Coin-detail's bid/ask ladder columns.
        pnl_text = bots_pane.format_pnl(pnl_value)
        pnl_line = lines[1]
        pnl_start = pnl_line.index(pnl_text)
        pnl_end = pnl_start + len(pnl_text)
        line_widgets = [
            urwid.Text(lines[0]),
            urwid.Text(
                [pnl_line[:pnl_start], (pnl_color, pnl_text), pnl_line[pnl_end:]],
            ),
            urwid.Text(lines[2]),
        ]
        snapshot_box = urwid.LineBox(
            urwid.Pile(line_widgets), title=f"{self._bot_detail_bot_id}  snapshot"
        )

        # Story 4.7: two more bordered regions, independent of the snapshot above --
        # bot_history_state.get_history() already folds "never fetched" and "stale"
        # into a single None (AC4: the snapshot region above has no dependency on this
        # read path at all, so it can never be affected by whatever these two do).
        history_entry = bot_history_state.get_history(self._bot_history_range)
        blotter_lines = bots_pane.trades_blotter_lines(history_entry)
        blotter_box = urwid.LineBox(
            urwid.BoxAdapter(
                urwid.ListBox(urwid.SimpleListWalker([urwid.Text(line) for line in blotter_lines])),
                height=_BOT_DETAIL_BLOTTER_HEIGHT,
            ),
            title="trades",
        )
        sparkline_box = urwid.LineBox(
            urwid.Text(bots_pane.pnl_sparkline_text(history_entry)),
            title=f"pnl ({self._bot_history_range})",
        )
        return urwid.Filler(urwid.Pile([snapshot_box, blotter_box, sparkline_box]), valign="top")

    def _build_coin_detail_body(self) -> urwid.Widget:
        # AC7: every value below is always the single most-recently-received
        # snapshots:raw row for the open instrument -- no time-index, no history, no
        # "as of" state anywhere in this method or in coin_detail_state.py. Do not add
        # a time-range picker, scrub bar, or "go to timestamp" control here; that
        # belongs to the web dashboard's own `/chart/{id}` view (the `o` deep-link).
        snapshot = coin_detail_state._LATEST_SNAPSHOT
        microprice = coin_detail_state._MICROPRICE
        ofi = coin_detail_state._OFI
        obi = coin_detail_state._OBI

        spread_value = coin_detail.spread(snapshot) if snapshot is not None else None
        microprice_initialized = microprice is not None and microprice.initialized
        ofi_initialized = ofi is not None and ofi.initialized
        obi_initialized = obi is not None and obi.initialized
        # Each value narrows microprice/ofi/obi's own Optionality inline, in the same
        # conditional expression that reads .value -- mypy cannot connect that back to
        # a separately-stored *_initialized bool (verified: this is the shape the
        # review's mypy pass actually flagged 3 real union-attr errors against).
        microprice_value = (
            microprice.value if microprice is not None and microprice.initialized else None
        )
        ofi_value = ofi.value if ofi is not None and ofi.initialized else None
        obi_value = obi.value if obi is not None and obi.initialized else None

        indicator_lines = [
            urwid.Text(
                "microprice   "
                + coin_detail.format_indicator(microprice_value, microprice_initialized, decimals=3)
            ),
            urwid.Text(
                "spread       "
                + coin_detail.format_indicator(spread_value, spread_value is not None)
            ),
            urwid.Text("ofi(10)      " + coin_detail.format_indicator(ofi_value, ofi_initialized)),
            urwid.Text("obi(10)      " + coin_detail.format_indicator(obi_value, obi_initialized)),
        ]
        indicators_box = urwid.LineBox(urwid.Pile(indicator_lines), title="live indicators")

        bid_prices = snapshot["bid_prices"] if snapshot is not None else []
        bid_sizes = snapshot["bid_sizes"] if snapshot is not None else []
        ask_prices = snapshot["ask_prices"] if snapshot is not None else []
        ask_sizes = snapshot["ask_sizes"] if snapshot is not None else []

        # This one parameter is the entire mechanism behind AC2/AC3's collapse/expand
        # behavior -- no separate "collapsed" vs. "expanded" code path exists.
        levels = _LADDER_EXPANDED_LEVELS if self._ladder_expanded else _LADDER_COLLAPSED_LEVELS
        bids = coin_detail.bid_lines(bid_prices, bid_sizes, levels)
        asks = coin_detail.ask_lines(ask_prices, ask_sizes, levels)

        ladder_rows = []
        for i in range(max(len(bids), len(asks))):
            bid_text = bids[i] if i < len(bids) else ""
            ask_text = asks[i] if i < len(asks) else ""
            ladder_rows.append(
                urwid.Columns([urwid.Text(("bid", bid_text)), urwid.Text(("ask", ask_text))])
            )

        ladder_state_text = (
            "20 lvl  (d: collapse)" if self._ladder_expanded else "top-of-book  (d: expand)"
        )
        ladder_box = urwid.LineBox(
            urwid.Pile(ladder_rows),
            title=f"order book  {self._coin_detail_instrument_id}  {ladder_state_text}",
        )

        return urwid.Filler(urwid.Pile([indicators_box, ladder_box]), valign="top")

    def _stale_badge_active(self) -> bool:
        # Pane-level only (AC3) -- never on the Bots pane, never before cold-open.
        if self._view != "coins" or ranking_state._LATEST_RANKING is None:
            return False
        return ranking_state.is_stale(ranking_state._LATEST_RANKING_RECEIVED_AT, now=time.time())

    def _coin_detail_stale_badge_active(self) -> bool:
        # DATA-01: Coin-detail must never keep showing a frozen snapshot with no
        # visual cue. Reuses ranking_state.is_stale (a plain now-received_at compare,
        # not ranking-specific) against coin_detail_state's own received-at clock --
        # same threshold as the Coins-pane badge, same "never before first snapshot"
        # guard (no snapshot yet is the legitimate warming-up state, not staleness).
        if coin_detail_state._LATEST_SNAPSHOT is None:
            return False
        return ranking_state.is_stale(
            coin_detail_state._LATEST_SNAPSHOT_RECEIVED_AT, now=time.time()
        )

    def _refresh_breadcrumb(self) -> None:
        if self._view == "coin_detail":
            # Verbatim mockup format (mockups/key-coin-detail.html) as the base text;
            # the stale badge (Review finding, post-4.3) is appended the same way the
            # Coins-pane breadcrumb appends it below.
            breadcrumb_text = f"Coins > {self._coin_detail_instrument_id}"
            if self._coin_detail_stale_badge_active():
                self._breadcrumb.set_text([breadcrumb_text, "  ", ("stale", "~ STALE")])
            else:
                self._breadcrumb.set_text(breadcrumb_text)
            return
        if self._view == "bot_detail":
            # Verbatim mockup format (mockups/key-bot-detail.html: "Bots > bot-03").
            # Stale-badge source is bots_state.is_stale (Story 4.4's own per-bot
            # function, a different heartbeat producer/threshold than
            # coin_detail_state's snapshots:raw badge above) -- not ranking_state.is_stale.
            breadcrumb_text = f"Bots > {self._bot_detail_bot_id}"
            if self._bot_detail_bot_id is not None and bots_state.is_stale(self._bot_detail_bot_id):
                self._breadcrumb.set_text([breadcrumb_text, "  ", ("stale", "~ STALE")])
            else:
                self._breadcrumb.set_text(breadcrumb_text)
            return
        label = _BREADCRUMB_LABELS[self._view]
        if self._stale_badge_active():
            # DESIGN.md's stale-badge component: "~" glyph, "STALE" text-suffix,
            # {colors.attention-stale}. Disappears the instant a heartbeat resumes --
            # this is recomputed, never latched.
            self._breadcrumb.set_text([label, "  ", ("stale", "~ STALE")])
        else:
            self._breadcrumb.set_text(label)

    def _refresh_footer_hint(self) -> None:
        if self._view == "coin_detail":
            self._footer_hint.set_text(_COIN_DETAIL_FOOTER_HINT_TEXT)
        elif self._view == "bots":
            self._footer_hint.set_text(_BOTS_FOOTER_HINT_TEXT)
        elif self._view == "bot_detail":
            self._footer_hint.set_text(_BOT_DETAIL_FOOTER_HINT_TEXT)
        else:
            self._footer_hint.set_text(_FOOTER_HINT_TEXT)

    def _switch_view(self, view: str, stack: list[str]) -> None:
        self._view = view
        self._stack = stack
        self._refresh_breadcrumb()
        self._refresh_footer_hint()
        self._body.original_widget = self._build_body()
        self._frame.focus_position = "body"

    def _open_command_bar(self) -> None:
        self._command_active = True
        self._command_edit.set_caption(":")
        self._command_edit.set_edit_text("")
        self._frame.footer = self._command_edit
        self._frame.focus_position = "footer"

    def _close_command_bar(self) -> None:
        self._command_active = False
        self._frame.footer = self._footer_hint
        self._frame.focus_position = "body"

    def _open_filter(self) -> None:
        self._filter_active = True
        self._filter_text = ""
        self._filter_edit.set_caption("/")
        self._filter_edit.set_edit_text("")
        self._frame.footer = self._filter_edit
        self._frame.focus_position = "footer"

    def _close_filter(self) -> None:
        # AC1: clears the filter without leaving the pane -- restores the full,
        # unfiltered row list; does not touch self._view/self._stack.
        self._filter_active = False
        self._filter_text = ""
        self._frame.footer = self._footer_hint
        self._frame.focus_position = "body"
        self._refresh_coins_body()
        self._body.original_widget = self._coins_body

    def _confirm_filter(self) -> None:
        # Enter while filtering: unlike esc (_close_filter), this keeps filter_text
        # narrowing the list -- it only returns focus/footer to the body so j/k/Enter
        # work against the filtered subset. This is what makes "Enter opens
        # Coin-detail while a filter is still active" (Story 4.3, AC6) a real,
        # keyboard-reachable path rather than only true of the unfiltered list.
        self._filter_active = False
        self._frame.footer = self._footer_hint
        self._frame.focus_position = "body"
        self._refresh_coins_body()
        self._body.original_widget = self._coins_body

    def _on_filter_change(self, widget: urwid.Edit, new_text: str) -> None:
        # Live narrowing as the builder types (AC1). No manual draw_screen() needed
        # here -- unlike the background-asyncio.Task-driven redraw below, a keystroke
        # handled while this Edit has focus is itself a synchronous urwid input event,
        # and urwid redraws automatically after processing any input event.
        self._filter_text = new_text
        self._refresh_coins_body()
        self._body.original_widget = self._coins_body

    def _toggle_mode(self) -> None:
        # Publish-and-wait, never optimistic (AC2): no local mode variable is flipped
        # and no re-render happens here. The next rankings:live message carrying the
        # confirmed mode is what actually changes what's on screen, via the existing
        # redraw-loop machinery below.
        current_mode = None
        if ranking_state._LATEST_RANKING is not None:
            current_mode = ranking_state._LATEST_RANKING.get("mode")
        new_mode = ranking_state.toggle_mode(current_mode)
        # Kept in self._background_tasks (with a done-callback to discard it) so the
        # task isn't garbage-collected mid-flight -- a bare asyncio.ensure_future()
        # result with no retained reference is a documented asyncio footgun, and a
        # second `m` press before the first publish completes would otherwise
        # overwrite the only reference to it.
        task = asyncio.ensure_future(ranking_state.publish_mode_toggle(self._redis_url, new_mode))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _toggle_bot(self) -> None:
        # _active_bot_id() (Story 4.5) makes this identical from the Bots pane
        # (highlighted row) and from Bot-detail (the open bot). Starting a stopped bot
        # is low-risk and stays immediate; stopping a running one routes through the
        # type-to-confirm guard instead of publishing directly -- see this module's
        # docstring for why only the stop direction needs it.
        bot_id = self._active_bot_id()
        if bot_id is None:
            return
        status = bots_state._LATEST_STATUSES.get(bot_id)
        running = bool(status.get("running")) if status is not None else False
        if running:
            self._open_stop_confirm(bot_id)
            return
        self._publish_bot_action(bot_id, "start")

    def _publish_bot_action(self, bot_id: str, action: str) -> None:
        # Publish-and-wait, never optimistic (AC3, same discipline as _toggle_mode):
        # no local running/stopped flip happens here -- the row only reflects the new
        # state once live_paper's own next bots:status heartbeat carries it back.
        task = asyncio.ensure_future(bots_state.publish_control(self._redis_url, bot_id, action))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        # AC3's literal footer-echo format: "sent: start bot-07" -- confirms the
        # command was sent, not that it succeeded.
        self._footer_hint.set_text(f"sent: {action} {bot_id}")

    def _open_stop_confirm(self, bot_id: str) -> None:
        self._stop_confirm_active = True
        self._stop_confirm_bot_id = bot_id
        self._stop_confirm_edit.set_caption(f"stop {bot_id}? type 'stop' + enter, esc to cancel: ")
        self._stop_confirm_edit.set_edit_text("")
        self._frame.footer = self._stop_confirm_edit
        self._frame.focus_position = "footer"

    def _close_stop_confirm(self) -> None:
        self._stop_confirm_active = False
        self._stop_confirm_bot_id = None
        self._frame.footer = self._footer_hint
        self._frame.focus_position = "body"

    def _handle_command_bar_key(self, key: str) -> None:
        # Extracted from _unhandled_input, same complexity-threshold reasoning as the
        # other _handle_*_key extractions.
        if key == "enter":
            self._submit_command()
        elif key == "esc":
            self._close_command_bar()

    def _handle_stop_confirm_key(self, key: str) -> None:
        # Extracted from _unhandled_input (same cognitive-complexity-threshold
        # reasoning as _handle_bots_pane_key/_handle_bot_detail_key above).
        if key == "enter":
            self._submit_stop_confirm()
        elif key == "esc":
            self._close_stop_confirm()

    def _submit_stop_confirm(self) -> None:
        bot_id = self._stop_confirm_bot_id
        assert bot_id is not None  # only reachable while _stop_confirm_active is True
        text = self._stop_confirm_edit.edit_text.strip().lower()
        if text != "stop":
            # Same "stay open, echo, let them retry" idiom as _submit_command's
            # unknown-command case -- never silently ignores a wrong answer.
            self._stop_confirm_edit.set_caption(
                f"type 'stop' to confirm -- stop {bot_id}? esc to cancel: "
            )
            self._stop_confirm_edit.set_edit_text("")
            return
        self._close_stop_confirm()
        self._publish_bot_action(bot_id, "stop")

    def _open_coin_detail(self, instrument_id: str) -> None:
        # Same plain-stack mechanism _dispatch_command already uses -- Story 4.1's
        # Dev Notes built this stack expecting exactly this kind of later full-screen
        # detail-view push, so no new navigation machinery is needed here.
        self._stack = [*self._stack, self._view]
        self._view = "coin_detail"
        self._coin_detail_instrument_id = instrument_id
        # Collapsed on every entry, never carried over from a prior visit to this or
        # any other coin (AC2) -- this is the entire mechanism behind that AC.
        self._ladder_expanded = False
        coin_detail_state.open_coin(instrument_id)
        self._refresh_breadcrumb()
        self._refresh_footer_hint()
        self._body.original_widget = self._build_body()
        self._frame.focus_position = "body"

    def _open_bot_detail(self, bot_id: str) -> None:
        # Same plain-stack push _open_coin_detail already uses. No subscription-
        # lifecycle call is needed here (unlike coin_detail_state.open_coin()) --
        # bots_state.py already accumulates every bot's latest status unconditionally,
        # regardless of which view is active (Story 4.4's design has no per-bot
        # subscribe/unsubscribe concept to open/close on entry/exit here).
        self._stack = [*self._stack, self._view]
        self._view = "bot_detail"
        self._bot_detail_bot_id = bot_id
        # Reset to "day" and start tracking this bot's history fresh on every entry
        # (Story 4.7) -- never carries over a previous bot's range or stale data,
        # same reset-on-entry discipline _open_coin_detail applies to _ladder_expanded.
        self._bot_history_range = "day"
        bot_history_state.open_bot(bot_id)
        self._refresh_breadcrumb()
        self._refresh_footer_hint()
        self._body.original_widget = self._build_body()
        self._frame.focus_position = "body"

    def _open_dashboard_chart(self) -> None:
        # Only reachable via the "o" key while self._view == "coin_detail", which
        # _open_coin_detail always sets alongside a real instrument_id -- never None
        # in practice (same stub-gap-narrowing precedent as Story 4.1's own
        # test_app_body.py, applied here to this Optional instance attribute instead).
        assert self._coin_detail_instrument_id is not None
        url = coin_detail.dashboard_chart_url(
            self._dashboard_base_url, self._coin_detail_instrument_id
        )
        # webbrowser.open() is a harmless no-op inside this product's headless
        # Docker/SSH deployment (no DISPLAY reachable) but genuinely opens a real
        # browser when bot_tui is run on-host -- both are real deployment shapes, so
        # the footer text below is shown unconditionally, never gated on this
        # attempt's (unreliable, environment-dependent) outcome.
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("webbrowser.open failed for %s", url)
        self._footer_hint.set_text(f"dashboard: {url}")

    def _cycle_bot_history_range(self) -> None:
        # Footer-echoes the new range and redraws immediately (Story 4.7, AC2) --
        # doesn't wait for the redraw loop's own next tick, same "keystroke handled
        # synchronously" idiom _on_filter_change already uses.
        self._bot_history_range = bots_pane.next_range(self._bot_history_range)
        self._footer_hint.set_text(f"range: {self._bot_history_range}")
        self._body.original_widget = self._build_body()

    def _open_dashboard_bot(self) -> None:
        # Only reachable via "o" while self._view == "bot_detail", which
        # _open_bot_detail always sets alongside a real bot_id -- same never-None-in-
        # practice precedent _open_dashboard_chart's own assert already documents for
        # Coin-detail.
        assert self._bot_detail_bot_id is not None
        url = bots_pane.dashboard_bot_url(self._dashboard_base_url, self._bot_detail_bot_id)
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("webbrowser.open failed for %s", url)
        self._footer_hint.set_text(f"dashboard: {url}")

    def _submit_command(self) -> None:
        text = self._command_edit.edit_text
        new_view, new_stack, echo = _dispatch_command(self._view, self._stack, text)
        if new_view == _QUIT_SENTINEL:
            raise urwid.ExitMainLoop
        if echo is not None:
            # Stays open for correction (AC3) -- echo the message via the caption,
            # keep the bar in editing state so the builder can retype immediately.
            self._command_edit.set_caption(f"{echo}\n:")
            self._command_edit.set_edit_text("")
            return
        self._close_command_bar()
        self._switch_view(new_view, new_stack)

    def _unhandled_input(self, key: str | tuple[str, int, int, int]) -> bool | None:
        # This product is keyboard-only (FR17) -- mouse events (urwid's 4-tuple form)
        # are never handled.
        if not isinstance(key, str):
            return None

        if self._command_active:
            self._handle_command_bar_key(key)
            return None

        if self._stop_confirm_active:
            self._handle_stop_confirm_key(key)
            return None

        if self._filter_active:
            # Third, distinct esc meaning (Story 4.2): clears the filter entirely,
            # never pops the view stack. Enter (Story 4.3) instead *confirms* the
            # filter -- keeps it narrowing the list, only releases footer focus back
            # to the body. Ordinary character keys are consumed by the focused Edit
            # widget itself (see _on_filter_change) and never reach here.
            if key == "esc":
                self._close_filter()
            elif key == "enter":
                self._confirm_filter()
            return None

        if self._view == "coin_detail":
            self._handle_coin_detail_key(key)
            return None

        if self._view == "bot_detail":
            self._handle_bot_detail_key(key)
            return None

        self._handle_global_key(key)
        return None

    def _handle_global_key(self, key: str) -> None:
        # Coins-pane-only keys (`/`, `m`, Enter) are a deliberate no-op on the Bots
        # pane, not an oversight -- EXPERIENCE.md: "`/` is the only pane FR-21
        # specifies filtering for." (Enter/Coin-detail follows the same rule.)
        if key == ":":
            self._open_command_bar()
        elif key == "esc":
            new_view, new_stack = _pop_view(self._view, self._stack)
            if new_view != self._view or new_stack != self._stack:
                self._switch_view(new_view, new_stack)
        elif key == "/" and self._view == "coins":
            self._open_filter()
        elif key == "m" and self._view == "coins":
            self._toggle_mode()
        elif key == "enter" and self._view == "coins":
            instrument_id = self._highlighted_instrument_id()
            if instrument_id is not None:
                self._open_coin_detail(instrument_id)
        elif self._view == "bots":
            self._handle_bots_pane_key(key)

    def _handle_bots_pane_key(self, key: str) -> None:
        # Extracted from _handle_global_key (Story 4.5) -- same cognitive-complexity-
        # threshold reasoning as _handle_coin_detail_key/_handle_bot_detail_key below.
        if key == "enter":
            bot_id = self._highlighted_bot_id()
            if bot_id is not None:
                self._open_bot_detail(bot_id)
        elif key == "s":
            self._toggle_bot()

    def _handle_bot_detail_key(self, key: str) -> None:
        # Extracted from _handle_global_key, same rationale as _handle_coin_detail_key
        # (Story 4.3): keeps each dispatch function under this codebase's cognitive-
        # complexity threshold as more per-view keys accumulate.
        if key == "s":
            self._toggle_bot()
        elif key == "t":
            self._cycle_bot_history_range()
        elif key == "o":
            self._open_dashboard_bot()
        elif key == "esc":
            self._bot_detail_bot_id = None
            bot_history_state.close_bot()
            new_view, new_stack = _pop_view(self._view, self._stack)
            self._switch_view(new_view, new_stack)

    def _handle_coin_detail_key(self, key: str) -> None:
        # Extracted from _handle_global_key (same reason Story 4.2 extracted that
        # method from _unhandled_input): keeps each dispatch function under this
        # codebase's cognitive-complexity threshold as more keys accumulate.
        if key == "d":
            # Scoped entirely to the ladder region (AC3) -- never touches the
            # breadcrumb, the indicators region, self._stack, or what esc does.
            self._ladder_expanded = not self._ladder_expanded
            self._body.original_widget = self._build_body()
        elif key == "o":
            self._open_dashboard_chart()
        elif key == "esc":
            coin_detail_state.close_coin()
            new_view, new_stack = _pop_view(self._view, self._stack)
            self._switch_view(new_view, new_stack)

    async def _redraw_loop(self) -> None:
        # The Coins-pane breadcrumb (and its stale badge) is refreshed every tick,
        # unconditionally, while on the Coins pane -- the badge must keep
        # re-evaluating purely from the passage of time even once ranking_engine's
        # heartbeat has stopped and _LATEST_RANKING's identity never changes again
        # (Story 4.1's original identity-diff-only condition would silently swallow
        # this).
        #
        # The row-list body, however, is only rebuilt on an actual _LATEST_RANKING
        # identity change (back to Story 4.1's original condition) -- rebuilding it
        # unconditionally every tick was tried and reverted during Story 4.2's own
        # review: a real urwid.ListBox/SimpleListWalker is a fresh object each time,
        # so an unconditional rebuild silently resets any scroll/focus position the
        # builder had inside the coins list back to the top twice a second, making a
        # list longer than one screen effectively unscrollable. Gating the body
        # rebuild on identity change avoids that regression while still satisfying the
        # breadcrumb's independent, always-on refresh above.
        #
        # Coin-detail (Story 4.3) rebuilds its indicators/ladder every tick instead --
        # unlike the Coins-pane ListBox, this view has no scroll-position-preservation
        # requirement to protect (there is nothing to scroll: no per-row selection in
        # the ladder), so there's no equivalent regression to gate against here.
        while True:
            try:
                if self._view == "coins":
                    self._refresh_breadcrumb()
                    current = ranking_state._LATEST_RANKING
                    if current is not self._last_seen_ranking:
                        self._last_seen_ranking = current
                        self._refresh_coins_body()
                        self._body.original_widget = self._coins_body
                    self._draw_screen()
                elif self._view == "coin_detail":
                    # Breadcrumb refreshed every tick too (Review finding, post-4.3) --
                    # same reasoning as the Coins-pane breadcrumb above: its stale badge
                    # must keep re-evaluating from the passage of time alone, even once
                    # snapshots:raw stops arriving and _LATEST_SNAPSHOT's identity never
                    # changes again.
                    self._refresh_breadcrumb()
                    self._body.original_widget = self._build_body()
                    self._draw_screen()
                elif self._view == "bots":
                    # Rebuilt unconditionally every tick, same as Coin-detail -- each
                    # row's own stale badge and uptime text must keep advancing purely
                    # from the passage of time, and (per _build_bots_body's own Dev
                    # Notes) this pane has no scroll-position-preservation contract to
                    # protect yet, unlike the Coins pane.
                    self._body.original_widget = self._build_bots_body()
                    self._draw_screen()
                elif self._view == "bot_detail":
                    # Same reasoning as Coin-detail/Bots above: breadcrumb (and its
                    # stale badge) and the header body both need to keep advancing
                    # purely from wall-clock time -- no scroll-position contract to
                    # protect here either (a single-bot header, no per-row selection).
                    self._refresh_breadcrumb()
                    self._body.original_widget = self._build_bot_detail_body()
                    self._draw_screen()
            except Exception:
                logger.exception("redraw loop iteration failed")
            await asyncio.sleep(_REDRAW_POLL_SECONDS)

    def _draw_screen(self) -> None:
        # Extracted from _redraw_loop (Story 4.5, cognitive-complexity fix) -- urwid's
        # MainLoop is unset in widget-construction-level tests, so every redraw-loop
        # branch guards this the same way; factoring the guard out here is what keeps
        # _redraw_loop itself under this codebase's complexity threshold.
        if self._main_loop is not None:
            self._main_loop.draw_screen()

    def run(self) -> None:
        # Python 3.14 raises RuntimeError from asyncio.get_event_loop() when no loop
        # has been set yet for this thread (implicit loop creation was removed) --
        # urwid's own asyncio example assumes get_event_loop() still creates one, which
        # no longer holds on this project's newest pinned Python. Create the loop
        # explicitly and register it first.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        evl = urwid.AsyncioEventLoop(loop=loop)
        self._main_loop = urwid.MainLoop(
            self._frame,
            palette=_PALETTE,
            unhandled_input=self._unhandled_input,
            event_loop=evl,
        )

        listener_task = loop.create_task(ranking_state._redis_listener(self._redis_url))
        snapshot_listener_task = loop.create_task(
            coin_detail_state._redis_listener(self._redis_url)
        )
        bots_listener_task = loop.create_task(bots_state._redis_listener(self._redis_url))
        history_poll_task = loop.create_task(bot_history_state.poll_loop(self._redis_url))
        redraw_task = loop.create_task(self._redraw_loop())
        try:
            self._main_loop.run()
        finally:
            listener_task.cancel()
            snapshot_listener_task.cancel()
            bots_listener_task.cancel()
            history_poll_task.cancel()
            redraw_task.cancel()


_LOG_PATH = os.environ.get("BOT_TUI_LOG_PATH", "bot_tui.log")


def main() -> None:
    # A full-screen urwid app owns the terminal -- logging to stderr/stdout (this
    # project's usual convention, fine for every other non-curses module) corrupts
    # its own display instead. Found via this story's own manual smoke check: a
    # stray log line visibly bled into the footer on startup. Log to a file instead
    # (relative to the container's own WORKDIR, /app -- not a shared /tmp path).
    logging.basicConfig(level=logging.INFO, filename=_LOG_PATH)
    BotTuiApp().run()


if __name__ == "__main__":
    main()
