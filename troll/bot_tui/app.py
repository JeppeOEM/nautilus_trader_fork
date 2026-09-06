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
sparkline sourced from Story 4.6's bots:history:* keys, `o` dashboard deep-link), a
Collector pane (Story 6.1: every currently-collected dYdX instrument -- always pinned,
there is no "collected but not pinned" state -- with liquid status, `p` to unpin (stop
+ add to config.exclude) and `x` to stop (don't exclude), both behind the same
type-to-confirm guard as Bots-pane's `s`, `:start <ID>`/`:pintop` command-bar actions to
pin one coin by name or fill empty slots with the current top-by-volume coins -- all
published to collector:control, read back via collector:status), and `esc`/`:q`
navigation.

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
import sys
import time
import webbrowser
from pathlib import Path

import urwid

from bot_tui import bot_history_state
from bot_tui import bot_incidents_state
from bot_tui import bots_pane
from bot_tui import bots_state
from bot_tui import coin_detail
from bot_tui import coin_detail_state
from bot_tui import collector_pane
from bot_tui import collector_state
from bot_tui import ranking_state
from bot_tui.coins_pane import COLD_OPEN_TEXT
from bot_tui.coins_pane import NO_MATCHES_TEXT
from bot_tui.coins_pane import coin_header_text
from bot_tui.coins_pane import coin_rows
from bot_tui.coins_pane import filter_rows
from bot_tui.coins_pane import format_coin_row
from bot_tui.coins_pane import stale_feed_banner_text


logger = logging.getLogger(__name__)

_BREADCRUMB_LABELS = {"coins": "Coins", "bots": "Bots", "collector": "Collector", "help": "Help"}

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
_BOT_DETAIL_FOOTER_HINT_TEXT = (
    "s start/stop  t range  o dashboard  v strategy  i incidents  esc back  :q quit"
)

# Help view's own footer -- nothing to do here but leave (:h/:help got you in).
_HELP_FOOTER_HINT_TEXT = "esc back  :q quit"

# Strategy-source view's own footer -- read-only, nothing to do here but scroll/leave.
_STRATEGY_FOOTER_HINT_TEXT = "esc back  :q quit"

# Incidents-log view's own footer -- also read-only.
_INCIDENTS_FOOTER_HINT_TEXT = "esc back  :q quit"

# Collector pane's own footer (Story 6.1) -- `:start <ID>`/`:pintop` are command-bar-
# only (no single key maps cleanly to "type an instrument id"), so only p/x get key hints.
_COLLECTOR_FOOTER_HINT_TEXT = "p unpin  x stop  : command  esc back  :q quit"

# This collector's own operating cap (Story 6.1) -- must match dydx_collector/
# collector.py's _MAX_COLLECTED_INSTRUMENTS. Duplicated rather than imported: bot_tui
# and dydx_collector are separate module boundaries (troll/CLAUDE.md DESIGN-02),
# coordinating only via Redis, never by importing each other's internals. This is a
# client-side check for instant feedback only -- the collector re-validates regardless.
_MAX_COLLECTED_INSTRUMENTS = 29

# Read-only bind mount from live_paper/ (docker-compose.yml's bot_tui service) -- the
# `v` key's only way to reach live_paper's source, since bot_tui's own image
# (collector.dockerfile) never COPYs live_paper/ in (AD-8's module isolation stays
# intact: this mount is view-only, no import/execution of live_paper code happens
# here). Hardcoded to the one strategy this system currently runs -- see
# live_paper/node.py's build_node(), which attaches DummyStrategy directly rather
# than by string path; revisit as a per-bot lookup (bots:status already carries a
# `strategy` class-name field) if a second strategy is ever added.
_STRATEGY_SOURCE_PATH = Path(
    os.environ.get("STRATEGY_SOURCE_PATH", "/app/live_paper/strategy.py")
)

# Full control reference shown by `:h`/`:help` (see _COMMAND_ALIASES below) -- one
# section per view, listing every key that view's own footer hint above only
# abbreviates. Plain text, not urwid markup: nothing here needs color.
_HELP_TEXT = """GLOBAL
  :          command bar (coins / bots / help, :q to quit)
  :h, :help  open this help
  esc        back one view
  :q         quit

COINS PANE
  j/k, up/down  move selection
  /             filter by instrument id (enter confirms, esc clears)
  m             toggle ranking mode (volume <-> volatility)
  enter         open coin detail

COIN DETAIL
  d          expand/collapse order book ladder
  o          open dashboard chart in browser
  esc        back to coins

BOTS PANE
  j/k, up/down  move selection
  s             start/stop highlighted bot (stop asks for confirmation)
  enter         open bot detail

BOT DETAIL
  s          start/stop this bot (stop asks for confirmation)
  t          cycle PnL/trades history range
  o          open dashboard bot page in browser
  v          view this bot's strategy source (read-only, scrollable)
  i          view this bot's incidents log: restarts, WS/data-stale spans
  esc        back to bots

COLLECTOR PANE
  Every action here writes straight through to config.toml on the collector -- it's
  the permanent record of what's collected, and it's what the collector re-reads if
  it restarts. Nothing here is temporary or TUI-only. Every currently-collected
  instrument is pinned by definition -- there is no "collected but not pinned" state.

  j/k, up/down     move selection
  p                unpin highlighted instrument: stops collecting it, and adds its id
                   to config.exclude (shown at the bottom of this pane as "unpinned")
                   so :start <ID> can add it straight back later (asks for confirmation)
  x                stop collecting highlighted instrument, same as p but does NOT
                   exclude it (asks for confirmation)
  :start <ID>      pin a coin by name: adds it if new, or re-adds it if it's in the
                   unpinned/excluded list -- either way it starts collecting,
                   pinned, immediately (rejected past the 29-coin cap)
  :pintop          fill every empty collector slot (up to the 29-coin cap) with the
                   current top-by-volume coins not already collected, pinned
                   immediately -- never removes or replaces an existing coin, and
                   never re-adds a coin you've explicitly unpinned (that only ever
                   happens via :start <ID>)
  esc              back

  The "unpinned" list at the bottom of this pane is config.exclude as a whole -- it
  shows any excluded id, whether it got there via p or a hand-edit of config.toml."""

_PALETTE = [
    ("stale", "yellow", "default"),
    ("bid", "dark green", "default"),
    ("ask", "dark red", "default"),
    ("pnl-pos", "dark green", "default"),
    ("pnl-neg", "dark red", "default"),
    # Row-focus indicator for the Coins/Bots-pane ListBoxes -- "default,standout"
    # reverses the terminal's own default fg/bg rather than picking a fixed color, so
    # it still reads correctly against any terminal theme (same UX-DR1 "inherit the
    # terminal's own default" discipline the other palette entries already follow).
    ("focus", "default,standout", "default"),
    # Mid-price divider row between the classic ladder's ask/bid halves.
    ("mid", "yellow,bold", "default"),
]

_LADDER_COLLAPSED_LEVELS = 4
_LADDER_EXPANDED_LEVELS = 20

# Every value column gets at least this many decimals -- user wants enough visible
# precision that a small tick registers as a changing digit instead of a static
# number. buy_count/sell_count are the one deliberate exception below (they're
# integer counts, not decimal quantities -- "7.00000000" would be noise, not
# precision).
_MIN_INDICATOR_DECIMALS = 8

# Coin-detail's full-parity indicator list (label, rankings:live rank-entry key,
# display decimals) -- every metric ranking_engine publishes for the open instrument,
# same SSOT-02 source the Coins pane's own columns come from. Not reusing
# ml_signals.ranking_columns.RANKING_COLS' own format_fn/color_fn here: those assume a
# non-None value (they'd raise on the "warming up" case this vertical list needs to
# handle for every row) and are tuned for compact table cells, not a labeled list --
# coin_detail.format_indicator already owns the None-safe formatting this view needs.
#
# Grouped into one box per update cadence (matches ml_signals/dashboard.py's
# /coin/{id} grouping) rather than one flat list -- a value's box tells you how often
# it can actually change without reading engine.py. "volatility & market" is a cadence
# group by convention, not strictly: pct_1h/pct_24h/volume24h aren't volatility, but
# they update on the same 60s-or-slower cadence as the volatility fields and would be
# noise scattered elsewhere.
_COIN_DETAIL_INDICATOR_GROUPS: list[tuple[str, list[tuple[str, str, int]]]] = [
    ("live  (1s book state)", [
        ("microprice", "microprice", _MIN_INDICATOR_DECIMALS),
        ("microprice lean", "microprice_lean", _MIN_INDICATOR_DECIMALS),
        ("spread", "spread", _MIN_INDICATOR_DECIMALS),
        ("obi(3)", "obi_3", _MIN_INDICATOR_DECIMALS),
        ("obi(5)", "obi_5", _MIN_INDICATOR_DECIMALS),
        ("obi(10)", "obi_10", _MIN_INDICATOR_DECIMALS),
        ("price", "price", _MIN_INDICATOR_DECIMALS),
    ]),
    ("order flow  (~5m rolling)", [
        ("ofi(3)", "ofi_3", _MIN_INDICATOR_DECIMALS),
        ("ofi(5)", "ofi_5", _MIN_INDICATOR_DECIMALS),
        ("ofi(10)", "ofi_10", _MIN_INDICATOR_DECIMALS),
        ("ofi(10) z", "ofi_10_z", _MIN_INDICATOR_DECIMALS),
        ("cvd", "cvd", _MIN_INDICATOR_DECIMALS),
        ("volume delta", "volume_delta", _MIN_INDICATOR_DECIMALS),
        ("buy count", "buy_count", 0),
        ("sell count", "sell_count", 0),
        ("avg trade size", "avg_trade_size", _MIN_INDICATOR_DECIMALS),
    ]),
    ("volatility & market  (60s-1h)", [
        ("volatility (fast)", "volatility_fast", _MIN_INDICATOR_DECIMALS),
        ("volatility (catalog)", "volatility", _MIN_INDICATOR_DECIMALS),
        ("volatility score", "volatility_score", _MIN_INDICATOR_DECIMALS),
        ("pct 1h", "pct_1h", _MIN_INDICATOR_DECIMALS),
        ("pct 24h", "pct_24h", _MIN_INDICATOR_DECIMALS),
        ("volume 24h", "volume24h", _MIN_INDICATOR_DECIMALS),
    ]),
]

# Bot-detail's trades-blotter region (Story 4.7) -- a fixed visible height inside a
# scrollable ListBox (BoxAdapter), not "however many fills happen to exist" (Story
# 4.6 already caps the wire contract at 500 trades; this caps what's visible at once
# on screen, independently, via ordinary urwid scrolling for the rest).
_BOT_DETAIL_BLOTTER_HEIGHT = 8

# Returned by _dispatch_command to signal `:q` -- kept out of urwid's ExitMainLoop so
# this function stays a plain, urwid-free, directly-testable function.
_QUIT_SENTINEL = "__quit__"

# Command-bar word -> view id. Defaults to each view id typing itself (":coins" ->
# "coins"), except "collector" -- the command bar uses ":data" for that view instead,
# so its own view id is deliberately absent from the left-hand side here.
_RECOGNIZED_COMMANDS = {v: v for v in _BREADCRUMB_LABELS if v != "collector"}
_RECOGNIZED_COMMANDS["data"] = "collector"

# Shorthand accepted by the command bar in addition to the full names above --
# resolved before the _RECOGNIZED_COMMANDS check in _dispatch_command, so ":h" behaves
# exactly like ":help".
_COMMAND_ALIASES = {"h": "help"}

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
    command = _COMMAND_ALIASES.get(command, command)
    if command in _RECOGNIZED_COMMANDS:
        view = _RECOGNIZED_COMMANDS[command]
        if view == current_view:
            return current_view, stack, None
        return view, [*stack, current_view], None
    return current_view, stack, f"unknown command: {text}"


def _parse_collector_command(text: str) -> tuple[str, str | None] | None:
    """
    Parse ":start <ID>" / ":pintop" from the command bar (Story 6.1). Kept as its own
    small pure parser rather than folded into _dispatch_command: these take an argument
    and trigger a Redis publish side effect, unlike that function's view-navigation-only
    contract (no argument, no side effect beyond switching views). Returns None for
    anything else, so the caller falls through to _dispatch_command unchanged.
    """
    command = text.strip()
    if command == "pintop":
        return ("pin_top_liquid", None)
    if command.startswith("start "):
        instrument_id = command[len("start ") :].strip()
        if instrument_id:
            return ("start", instrument_id)
    return None


def _pop_view(current_view: str, stack: list[str]) -> tuple[str, list[str]]:
    """Esc pops back exactly one level; never raises, no-ops at the root (AC4)."""
    if not stack:
        return current_view, []
    return stack[-1], stack[:-1]


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

    def __init__(self, markup: object, instrument_id: str) -> None:
        # markup is coins_pane.format_coin_row's own return shape (str | tuple | list
        # of either) -- same urwid._TagMarkup stub-gap precedent as _SelectableBotRow.
        super().__init__(markup)  # type: ignore[arg-type]
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


class _SelectableCollectorRow(urwid.Text):
    """Same selectable-but-non-consuming shape as _SelectableBotRow (Story 6.1)."""

    def __init__(self, markup: object, instrument_id: str) -> None:
        super().__init__(markup)  # type: ignore[arg-type]
        self.instrument_id = instrument_id

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
        # Ladder/indicator column-width ratchets (only grow, never shrink) so a value
        # crossing a digit boundary (9.xx -> 10.xx) doesn't visibly reflow the layout --
        # see coin_detail.order_book_lines' docstring. Reset to 0 on every fresh coin
        # open (_open_coin_detail) same as _ladder_expanded, so one coin's wide numbers
        # don't force a wide box on the next, unrelated coin.
        self._ladder_size_w = 0
        self._ladder_price_w = 0
        self._indicator_value_w = 0
        self._dashboard_base_url = os.environ.get("DASHBOARD_BASE_URL", "http://127.0.0.1:8765")
        # The ListBox itself persists across the redraw loop's every-tick rebuild
        # (only its SimpleListWalker's contents are replaced in place) so mid-scroll
        # position survives a data refresh -- same fix as Story 4.2's Coins-pane
        # regression (_refresh_coins_body's docstring). None until _open_coin_detail
        # creates it fresh for the newly-opened coin.
        self._coin_detail_listbox: urwid.ListBox | None = None

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
        # Same persist-the-ListBox-across-ticks fix as _coin_detail_listbox above.
        self._bot_detail_listbox: urwid.ListBox | None = None

        # Stop-confirmation guard: active while the operator must type "stop" + Enter
        # to actually stop the bot named by _stop_confirm_bot_id (captured at open
        # time so it can't drift if state changes while the prompt is up -- nothing
        # else can happen while it's active, every key is captured below).
        self._stop_confirm_active = False
        self._stop_confirm_bot_id: str | None = None

        # Same type-to-confirm guard as the Bots pane's stop, applied to the Collector
        # pane's `x` (stop) and `p` (unpin) actions -- a separate flag/action/id triple
        # rather than reusing the bot one (that guards a genuinely different action:
        # stop a bot, not stop collecting an instrument). Unlike the bots-vs-collector
        # split, stop and unpin share this one implementation rather than each getting
        # their own copy: same pane, same instrument id, same widget, differing only in
        # the action word and the confirm keyword -- worth collapsing once there were
        # two near-identical copies of the exact same pattern in the same view.
        self._collector_confirm_active = False
        self._collector_confirm_action: str | None = None
        self._collector_confirm_id: str | None = None

        # The Coins-pane body is a persistent object, mutated in place rather than
        # rebuilt on every view switch (Story 4.3, AC6 -- see _refresh_coins_body).
        # _coins_shape is None until the first _refresh_coins_body() call below builds
        # it for real; that sentinel guarantees the first call always constructs
        # fresh rather than skipping because "cold_open" happens to already match.
        self._coins_shape: str | None = None
        self._coins_body: urwid.Widget = urwid.Filler(urwid.Text(COLD_OPEN_TEXT), valign="top")
        self._refresh_coins_body()

        # Collector pane's body, same persistent-object/scroll-preservation contract as
        # the Coins pane above (Story 6.1 fix -- see _refresh_collector_body).
        self._collector_shape: str | None = None
        self._collector_body: urwid.Widget = urwid.Filler(
            urwid.Text(collector_pane.COLD_OPEN_TEXT), valign="top"
        )

        self._breadcrumb = urwid.Text(_BREADCRUMB_LABELS[self._view])
        # Coins-only column-header row ("#  INSTRUMENT  VOLUME24H"/"VOLATILITY"),
        # stacked under the breadcrumb rather than folded into the Coins-pane body --
        # keeps _build_body/_coins_body exactly what Story 4.3's AC6 comment already
        # documents (Filler <-> ListBox, mutated in place for scroll preservation),
        # so this doesn't disturb that logic or its tests at all. Blank on every other
        # view; _refresh_breadcrumb sets/clears it alongside the breadcrumb text since
        # both need the same "runs every redraw tick while on this view" treatment.
        self._coins_column_header = urwid.Text("")
        self._refresh_breadcrumb()
        self._footer_hint = urwid.Text(_FOOTER_HINT_TEXT)
        self._command_edit = urwid.Edit(":")
        self._filter_edit = urwid.Edit("/")
        urwid.connect_signal(self._filter_edit, "change", self._on_filter_change)
        self._stop_confirm_edit = urwid.Edit("")
        self._body = urwid.WidgetPlaceholder(self._build_body())
        self._frame = urwid.Frame(
            header=urwid.Pile([self._breadcrumb, self._coins_column_header]),
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

    def _set_coins_rows(self, rows: list[dict]) -> None:
        widgets = [
            urwid.AttrMap(
                _SelectableCoinRow(format_coin_row(row), instrument_id=row["instrument_id"]),
                None,
                focus_map="focus",
            )
            for row in rows
        ]
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
        assert isinstance(focus_widget, urwid.AttrMap)
        row = focus_widget.original_widget
        assert isinstance(row, _SelectableCoinRow)
        return row.instrument_id

    def _build_body(self) -> urwid.Widget:
        if self._view == "bots":
            return self._build_bots_body()

        if self._view == "coin_detail":
            return self._build_coin_detail_body()

        if self._view == "bot_detail":
            return self._build_bot_detail_body()

        if self._view == "strategy":
            return self._build_strategy_body()

        if self._view == "incidents":
            return self._build_incidents_body()

        if self._view == "collector":
            # Returned as-is, whatever it currently holds -- same "never rebuilt here"
            # discipline as the Coins pane below; only _refresh_collector_body() (called
            # by the redraw loop while this view is active) ever changes what this holds.
            return self._collector_body

        if self._view == "help":
            return urwid.Filler(urwid.Text(_HELP_TEXT), valign="top")

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

    def _refresh_collector_body(self) -> None:
        """
        Rebuild/mutate self._collector_body to reflect current collector:status data.

        Unlike _build_bots_body (a deliberate, documented YAGNI gap that resets scroll
        position on every rebuild), this follows _refresh_coins_body's fix instead: only
        the three genuinely-different-shape transitions (cold-open <-> populated) swap
        self._collector_body's type; a same-shape update mutates the existing ListBox's
        SimpleListWalker in place via slice-assignment, which is what actually preserves
        scroll/focus position. Collector rows update far less often than bots/coins rows
        (collector:status republishes every liquidity_check_seconds, default 30 min,
        plus immediately after a control action) but the redraw loop still calls this
        every _REDRAW_POLL_SECONDS while this view is active (so a row's stale marker
        still appears promptly once collector_state.is_stale() flips) -- without this
        fix, a user's up/down scroll would be silently wiped within half a second on
        every single redraw tick, not just on a real data change. Confirmed in
        production: this was exactly the reported bug.
        """
        statuses = collector_state._LATEST_COLLECTOR_STATUS
        if not statuses:
            self._set_collector_filler(collector_pane.COLD_OPEN_TEXT)
            return

        rows = collector_pane.collector_rows(statuses)
        widgets = [
            self._build_collector_row_widget(row, collector_state.is_stale(row["id"]))
            for row in rows
        ]
        unpinned_line = collector_pane.format_unpinned_line(collector_state._LATEST_UNPINNED_IDS)
        if unpinned_line:
            # Plain, non-selectable urwid.Text -- ListBox never lands focus on it (same
            # precedent as _SelectableCoinRow's docstring), so _highlighted_collector_id
            # only ever sees a real _SelectableCollectorRow.
            widgets.append(urwid.Text(""))
            widgets.append(urwid.Text(unpinned_line))
        if self._collector_shape != "rows":
            self._collector_body = urwid.ListBox(urwid.SimpleListWalker(widgets))
            self._collector_shape = "rows"
        else:
            listbox = self._collector_body
            assert isinstance(listbox, urwid.ListBox)
            listbox.body[:] = widgets  # type: ignore[index]

    def _set_collector_filler(self, text: str) -> None:
        if self._collector_shape != "cold_open":
            self._collector_body = urwid.Filler(urwid.Text(text), valign="top")
            self._collector_shape = "cold_open"

    def _build_collector_row_widget(self, row: dict, stale: bool) -> urwid.Widget:
        markup = collector_pane.format_collector_line(row, stale)
        return urwid.AttrMap(
            _SelectableCollectorRow(markup, instrument_id=row["id"]), None, focus_map="focus"
        )

    def _highlighted_collector_id(self) -> str | None:
        """Mirrors _highlighted_bot_id's read-the-ListBox's-own-focus pattern."""
        body = self._body.original_widget
        if not isinstance(body, urwid.ListBox):
            return None
        focus_widget = body.focus
        if focus_widget is None:
            return None
        assert isinstance(focus_widget, urwid.AttrMap)
        row = focus_widget.original_widget
        assert isinstance(row, _SelectableCollectorRow)
        return row.instrument_id

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
        return urwid.AttrMap(
            _SelectableBotRow(markup, bot_id=row["bot_id"]), None, focus_map="focus"
        )

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
        assert isinstance(focus_widget, urwid.AttrMap)
        row = focus_widget.original_widget
        assert isinstance(row, _SelectableBotRow)
        return row.bot_id

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
            self._bot_detail_listbox = None
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
        metrics_lines = bots_pane.metrics_lines(history_entry)
        metrics_box = urwid.LineBox(
            urwid.Pile([urwid.Text(line) for line in metrics_lines]),
            title=f"performance ({self._bot_history_range})",
        )
        widgets = [snapshot_box, blotter_box, sparkline_box, metrics_box]
        if self._bot_detail_listbox is None:
            self._bot_detail_listbox = urwid.ListBox(urwid.SimpleListWalker(widgets))
        else:
            # Same slice-assignment-not-fresh-ListBox fix as _coin_detail_listbox
            # above / _set_coins_rows: preserves scroll/focus position across the
            # redraw loop's every-tick rebuild.
            self._bot_detail_listbox.body[:] = widgets  # type: ignore[index]
        return self._bot_detail_listbox

    def _build_strategy_body(self) -> urwid.Widget:
        # A plain ListBox (not Filler+Text, unlike the static Help view) -- a
        # strategy source file can easily exceed one screen's height, and ListBox
        # gets free up/down/page scrolling the same way the Coins/Bots-pane row
        # lists already do, with no extra wiring.
        try:
            lines = _STRATEGY_SOURCE_PATH.read_text().splitlines()
        except OSError as e:
            return urwid.Filler(
                urwid.Text(f"could not read {_STRATEGY_SOURCE_PATH}: {e}"), valign="top"
            )
        return urwid.ListBox(urwid.SimpleListWalker([urwid.Text(line) for line in lines]))

    def _build_incidents_body(self) -> urwid.Widget:
        bot_id = self._bot_detail_bot_id
        incidents = bot_incidents_state.get_incidents(bot_id) if bot_id is not None else None
        lines = bots_pane.incidents_lines(incidents, now=time.time())
        return urwid.ListBox(urwid.SimpleListWalker([urwid.Text(line) for line in lines]))

    def _build_coin_detail_body(self) -> urwid.Widget:
        # AC7: the order-book ladder below is always the single most-recently-received
        # snapshots:raw row for the open instrument -- no time-index, no history, no
        # "as of" state anywhere in this method or in coin_detail_state.py. Do not add
        # a time-range picker, scrub bar, or "go to timestamp" control here; that
        # belongs to the web dashboard's own `/chart/{id}` view (the `o` deep-link).
        #
        # Every indicator below (SSOT-02, troll/CLAUDE.md) comes from the matching
        # rankings:live rank entry instead of a local instance -- ranking_engine is the
        # sole computer of all of it now; this view is a pure reader, same as the Coins
        # pane and the web dashboard's /coin/{id} panel.
        # Only reachable while self._view == "coin_detail", which _open_coin_detail
        # always sets alongside a real instrument_id -- same never-None-in-practice
        # precedent _open_dashboard_chart's own assert already documents.
        assert self._coin_detail_instrument_id is not None
        snapshot = coin_detail_state._LATEST_SNAPSHOT
        row = coin_detail.rank_row_for(ranking_state._LATEST_RANKING, self._coin_detail_instrument_id)
        row = row or {}

        # Right-justify values to the widest one ever seen this Coin-detail session,
        # not just this tick -- self._indicator_value_w is a ratchet (only grows, reset
        # on a fresh coin open below), same fix as the ladder's size_w/price_w: a plain
        # per-tick max + margin still visibly reflows the column the instant a value's
        # digit count crosses a boundary (e.g. 9.xx -> 10.xx), because the margin gets
        # re-added on top of a fresh max each time instead of held fixed while a value
        # grows into previously-reserved space. A long label ("volatility (catalog)")
        # or a long value (a meme-coin OFI in the trillions) would otherwise also push
        # just that row's number out of line with the rest. Widths are shared across all
        # three boxes (computed over every group's pairs together) so the boxes still
        # line up with each other, not just internally.
        all_pairs = [
            (label, coin_detail.format_indicator(row.get(key), decimals))
            for _, specs in _COIN_DETAIL_INDICATOR_GROUPS
            for label, key, decimals in specs
        ]
        label_w = max(len(label) for label, _ in all_pairs)
        self._indicator_value_w = coin_detail.ratchet_width(
            max(len(value) for _, value in all_pairs), self._indicator_value_w
        )
        value_w = self._indicator_value_w

        def _group_box(title: str, specs: list[tuple[str, str, int]]) -> urwid.LineBox:
            lines = [
                urwid.Text(
                    f"{label:<{label_w}}  "
                    f"{coin_detail.format_indicator(row.get(key), decimals):>{value_w}}",
                )
                for label, key, decimals in specs
            ]
            return urwid.LineBox(urwid.Pile(lines), title=title)

        indicator_boxes = [
            _group_box(title, specs) for title, specs in _COIN_DETAIL_INDICATOR_GROUPS
        ]

        bid_prices = snapshot["bid_prices"] if snapshot is not None else []
        bid_sizes = snapshot["bid_sizes"] if snapshot is not None else []
        ask_prices = snapshot["ask_prices"] if snapshot is not None else []
        ask_sizes = snapshot["ask_sizes"] if snapshot is not None else []

        # This one parameter is the entire mechanism behind AC2/AC3's collapse/expand
        # behavior -- no separate "collapsed" vs. "expanded" code path exists.
        levels = _LADDER_EXPANDED_LEVELS if self._ladder_expanded else _LADDER_COLLAPSED_LEVELS
        ask_lines, mid_line, bid_lines, size_w, price_w = coin_detail.order_book_lines(
            bid_prices,
            bid_sizes,
            ask_prices,
            ask_sizes,
            levels,
            min_size_w=self._ladder_size_w,
            min_price_w=self._ladder_price_w,
        )
        self._ladder_size_w = size_w
        self._ladder_price_w = price_w

        # Classic centered ladder: asks stacked above (worst-to-best, best ask nearest
        # the middle), a mid-price divider, then bids below (best-to-worst, best bid
        # nearest the middle). mid_line is pre-padded by order_book_lines to the same
        # size/price columns as every level row (not centered across the box), so the
        # price digits of asks/mid/bids all land in the same column -- a real ladder's
        # price axis is one line straight down the middle, not a caption floating over
        # two stacked tables.
        all_lines = [*ask_lines, mid_line, *bid_lines]
        ladder_rows = [urwid.Text(("ask", line)) for line in ask_lines]
        ladder_rows.append(urwid.Text(("mid", mid_line)))
        ladder_rows.extend(urwid.Text(("bid", line)) for line in bid_lines)

        ladder_state_text = (
            "20 lvl  (d: collapse)"
            if self._ladder_expanded
            else f"{_LADDER_COLLAPSED_LEVELS} lvl  (d: expand)"
        )
        ladder_title = f"order book  {self._coin_detail_instrument_id}  {ladder_state_text}"
        ladder_box = urwid.LineBox(urwid.Pile(ladder_rows), title=ladder_title)
        # Sized to its own content (longest row + the 2 border columns) instead of
        # stretching to the terminal's full width -- a real order-book widget is a
        # narrow column, not a full-width panel. Never narrower than the title needs
        # (LineBox wraps the title onto a second line rather than truncating it once
        # the box is too narrow to fit "┌─ title ┐"), so a long instrument id/state
        # string still sets the floor when it's wider than the levels themselves.
        content_width = max((len(line) for line in all_lines), default=0) + 2
        # +10, not the border's own +4: urwid.LineBox's centered title has a narrow
        # "vanishes entirely" zone a few chars above its true minimum (a rendering
        # quirk of its centering math, confirmed empirically -- +4 sometimes rendered
        # a blank border), so this leaves real slack rather than hugging the edge.
        title_width = len(ladder_title) + 10
        ladder_box = urwid.Padding(ladder_box, align="left", width=max(content_width, title_width))

        widgets = [*indicator_boxes, ladder_box]
        if self._coin_detail_listbox is None:
            self._coin_detail_listbox = urwid.ListBox(urwid.SimpleListWalker(widgets))
        else:
            # Slice-assignment on the existing SimpleListWalker, not a fresh ListBox --
            # same fix, same reason as _set_coins_rows: preserves scroll/focus position
            # across the redraw loop's every-tick rebuild instead of resetting it to
            # the top.
            self._coin_detail_listbox.body[:] = widgets  # type: ignore[index]
        return self._coin_detail_listbox

    def _stale_badge_active(self) -> bool:
        # Pane-level only (AC3) -- never on the Bots pane, never before cold-open.
        if self._view != "coins" or ranking_state._LATEST_RANKING is None:
            return False
        return ranking_state.is_stale(ranking_state._LATEST_RANKING_RECEIVED_AT, now=time.time())

    def _stale_feed_banner(self) -> str:
        # Coins-pane only -- reads rankings:live's own stale_instrument_ids field
        # verbatim (ranking_engine._recently_stale_iids), no local staleness
        # computation here.
        if self._view != "coins" or ranking_state._LATEST_RANKING is None:
            return ""
        stale_ids = ranking_state._LATEST_RANKING.get("stale_instrument_ids", [])
        return stale_feed_banner_text(stale_ids)

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
        # Column header lives here, not in its own refresh path -- every call site
        # below already runs on exactly the occasions the header needs to update too
        # (view switch, and every redraw-loop tick while the Coins pane is active).
        if self._view == "coins":
            # No longer mode-dependent (unlike the old single-score-column header):
            # full column parity means volume24h/volatility_score are both always-
            # present regular columns now, same as every other RANKING_COLS field.
            self._coins_column_header.set_text(coin_header_text())
        else:
            self._coins_column_header.set_text("")

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
        if self._view == "strategy":
            # No stale badge here -- a source file's own contents have no heartbeat
            # concept, unlike the live bots:status/snapshots:raw feeds the other two
            # detail views badge.
            self._breadcrumb.set_text(f"Bots > {self._bot_detail_bot_id} > strategy")
            return
        if self._view == "incidents":
            # No stale badge either -- bots:incidents only changes on a real
            # transition (bot_status._incident_transition), so a long-unchanged read
            # is the expected healthy state, not staleness.
            self._breadcrumb.set_text(f"Bots > {self._bot_detail_bot_id} > incidents")
            return
        label = _BREADCRUMB_LABELS[self._view]
        if self._stale_badge_active():
            # DESIGN.md's stale-badge component: "~" glyph, "STALE" text-suffix,
            # {colors.attention-stale}. Disappears the instant a heartbeat resumes --
            # this is recomputed, never latched. Takes priority over the per-coin
            # banner below: if the whole rankings:live heartbeat is down, its
            # stale_instrument_ids payload is itself frozen/stale data, not worth
            # showing alongside a "the feed itself is dead" badge.
            self._breadcrumb.set_text([label, "  ", ("stale", "~ STALE")])
            return
        banner = self._stale_feed_banner()
        if banner:
            self._breadcrumb.set_text([label, "  ", ("stale", f"~ {banner}")])
        else:
            self._breadcrumb.set_text(label)

    def _refresh_footer_hint(self) -> None:
        if self._view == "coin_detail":
            self._footer_hint.set_text(_COIN_DETAIL_FOOTER_HINT_TEXT)
        elif self._view == "bots":
            self._footer_hint.set_text(_BOTS_FOOTER_HINT_TEXT)
        elif self._view == "bot_detail":
            self._footer_hint.set_text(_BOT_DETAIL_FOOTER_HINT_TEXT)
        elif self._view == "strategy":
            self._footer_hint.set_text(_STRATEGY_FOOTER_HINT_TEXT)
        elif self._view == "incidents":
            self._footer_hint.set_text(_INCIDENTS_FOOTER_HINT_TEXT)
        elif self._view == "collector":
            self._footer_hint.set_text(_COLLECTOR_FOOTER_HINT_TEXT)
        elif self._view == "help":
            self._footer_hint.set_text(_HELP_FOOTER_HINT_TEXT)
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

    def _publish_collector_action(self, action: str, instrument_id: str | None) -> None:
        # Publish-and-wait, never optimistic -- same discipline as _publish_bot_action:
        # no local pinned/collected flip happens here, the row only reflects the new
        # state once collector:status's next message (published immediately after the
        # collector applies the action -- see collector.py's _publish_status) arrives.
        task = asyncio.ensure_future(
            collector_state.publish_control(self._redis_url, action, instrument_id)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        label = f" {instrument_id}" if instrument_id is not None else ""
        self._footer_hint.set_text(f"sent: {action}{label}")

    def _toggle_pin(self) -> None:
        # Every listed row is pinned by definition (Story 6.1) -- p always means unpin.
        instrument_id = self._highlighted_collector_id()
        if instrument_id is None:
            return
        self._open_collector_confirm("unpin", instrument_id)

    # Verb shown in the confirm prompt for each collector action -- "stop" and "unpin"
    # are also the exact word the operator must type, so this only supplies the extra
    # "collecting" stop wants ("stop collecting X?" reads better than "stop X?", which
    # could be misread as stopping something else; unpin needs no such qualifier).
    _COLLECTOR_CONFIRM_VERBS = {"stop": "stop collecting", "unpin": "unpin"}

    def _open_collector_confirm(self, action: str, instrument_id: str) -> None:
        self._collector_confirm_active = True
        self._collector_confirm_action = action
        self._collector_confirm_id = instrument_id
        verb = self._COLLECTOR_CONFIRM_VERBS[action]
        self._stop_confirm_edit.set_caption(
            f"{verb} {instrument_id}? type '{action}' + enter, esc to cancel: "
        )
        self._stop_confirm_edit.set_edit_text("")
        self._frame.footer = self._stop_confirm_edit
        self._frame.focus_position = "footer"

    def _close_collector_confirm(self) -> None:
        self._collector_confirm_active = False
        self._collector_confirm_action = None
        self._collector_confirm_id = None
        self._frame.footer = self._footer_hint
        self._frame.focus_position = "body"

    def _handle_collector_confirm_key(self, key: str) -> None:
        if key == "enter":
            self._submit_collector_confirm()
        elif key == "esc":
            self._close_collector_confirm()

    def _submit_collector_confirm(self) -> None:
        action = self._collector_confirm_action
        instrument_id = self._collector_confirm_id
        assert action is not None and instrument_id is not None  # only while confirm active
        text = self._stop_confirm_edit.edit_text.strip().lower()
        if text != action:
            verb = self._COLLECTOR_CONFIRM_VERBS[action]
            self._stop_confirm_edit.set_caption(
                f"type '{action}' to confirm -- {verb} {instrument_id}? esc to cancel: "
            )
            self._stop_confirm_edit.set_edit_text("")
            return
        self._close_collector_confirm()
        self._publish_collector_action(action, instrument_id)

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
        self._ladder_size_w = 0
        self._ladder_price_w = 0
        self._indicator_value_w = 0
        self._coin_detail_listbox = None
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
        self._bot_detail_listbox = None
        bot_history_state.open_bot(bot_id)
        bot_incidents_state.open_bot(bot_id)
        self._refresh_breadcrumb()
        self._refresh_footer_hint()
        self._body.original_widget = self._build_body()
        self._frame.focus_position = "body"

    def _open_strategy_view(self) -> None:
        # Same plain-stack push _open_bot_detail/_open_coin_detail already use --
        # esc pops back to Bot-detail via _handle_global_key's generic _pop_view
        # mechanism (no dedicated "strategy" key handler needed: no key other than
        # esc/":" does anything in this view, and both of those are already handled
        # generically there).
        self._stack = [*self._stack, self._view]
        self._view = "strategy"
        self._refresh_breadcrumb()
        self._refresh_footer_hint()
        self._body.original_widget = self._build_body()
        self._frame.focus_position = "body"

    def _open_incidents_view(self) -> None:
        # Same plain-stack push _open_strategy_view already uses -- same reasoning:
        # esc pops back to Bot-detail generically, no dedicated key handler needed.
        # No open_bot()-equivalent call here: bot_incidents_state is already tracking
        # this bot_id (started by _open_bot_detail alongside bot_history_state), so
        # entering this view just needs a fresh redraw of what it already has.
        self._stack = [*self._stack, self._view]
        self._view = "incidents"
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
        sys.stdout.write(coin_detail.osc52_copy_sequence(url))
        sys.stdout.flush()
        self._footer_hint.set_text(f"dashboard (copied to clipboard): {url}")

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
        sys.stdout.write(coin_detail.osc52_copy_sequence(url))
        sys.stdout.flush()
        self._footer_hint.set_text(f"dashboard (copied to clipboard): {url}")

    def _submit_command(self) -> None:
        text = self._command_edit.edit_text
        parsed = _parse_collector_command(text)
        if parsed is not None:
            action, instrument_id = parsed
            if action == "start" and len(collector_state._LATEST_COLLECTOR_STATUS) >= (
                _MAX_COLLECTED_INSTRUMENTS
            ):
                self._command_edit.set_caption(
                    f"cannot start {instrument_id}: at {_MAX_COLLECTED_INSTRUMENTS}-instrument "
                    "cap\n:"
                )
                self._command_edit.set_edit_text("")
                return
            self._publish_collector_action(action, instrument_id)
            self._close_command_bar()
            return
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

    def _handle_modal_guard_key(self, key: str) -> bool:
        # Extracted from _unhandled_input (Story 6.1, cognitive-complexity fix): the
        # three mutually-exclusive "a modal prompt owns input right now" cases, each
        # already delegating to its own _handle_*_key. Returns whether one consumed
        # the key, so the caller still returns None uniformly either way.
        if self._command_active:
            self._handle_command_bar_key(key)
            return True
        if self._stop_confirm_active:
            self._handle_stop_confirm_key(key)
            return True
        if self._collector_confirm_active:
            self._handle_collector_confirm_key(key)
            return True
        return False

    def _unhandled_input(self, key: str | tuple[str, int, int, int]) -> bool | None:
        # This product is keyboard-only (FR17) -- mouse events (urwid's 4-tuple form)
        # are never handled.
        if not isinstance(key, str):
            return None

        if self._handle_modal_guard_key(key):
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

        # `:` opens the command bar from any page (every footer hint advertises
        # ":q quit") -- checked ahead of the view-specific branches below, which
        # otherwise return early and never reach _handle_global_key's own dispatch.
        if key == ":":
            self._open_command_bar()
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
        if key == "esc":
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
        elif self._view == "collector":
            self._handle_collector_pane_key(key)

    def _handle_bots_pane_key(self, key: str) -> None:
        # Extracted from _handle_global_key (Story 4.5) -- same cognitive-complexity-
        # threshold reasoning as _handle_coin_detail_key/_handle_bot_detail_key below.
        if key == "enter":
            bot_id = self._highlighted_bot_id()
            if bot_id is not None:
                self._open_bot_detail(bot_id)
        elif key == "s":
            self._toggle_bot()

    def _handle_collector_pane_key(self, key: str) -> None:
        # No Enter/drill-down here -- the Collector pane has no detail sub-view
        # (Story 6.1 scoped that out, unlike Bots pane's Bot-detail).
        if key == "p":
            self._toggle_pin()
        elif key == "x":
            instrument_id = self._highlighted_collector_id()
            if instrument_id is not None:
                self._open_collector_confirm("stop", instrument_id)

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
        elif key == "v":
            self._open_strategy_view()
        elif key == "i":
            self._open_incidents_view()
        elif key == "esc":
            self._bot_detail_bot_id = None
            bot_history_state.close_bot()
            bot_incidents_state.close_bot()
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
        # Coin-detail (and Bot-detail below) also rebuild every tick, unlike the
        # Coins-pane's identity-gated body -- but now that both are a scrollable
        # ListBox too (small-terminal fix), _build_coin_detail_body/
        # _build_bot_detail_body protect scroll position themselves, the same way
        # _set_coins_rows does: they mutate the persisted ListBox's SimpleListWalker
        # in place (self._coin_detail_listbox/self._bot_detail_listbox) instead of
        # handing back a fresh ListBox object every call.
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
                    # Breadcrumb (and its stale badge) needs to keep advancing purely
                    # from wall-clock time, same as Coin-detail above. The body is
                    # rebuilt every tick too -- scroll position is protected by
                    # _build_bot_detail_body itself (see the comment on
                    # self._coin_detail_listbox above), not by gating this call.
                    self._refresh_breadcrumb()
                    self._body.original_widget = self._build_bot_detail_body()
                    self._draw_screen()
                elif self._view == "incidents":
                    # An open incident's "ongoing (Nm..)" duration must keep advancing
                    # purely from wall-clock time, same as Bot-detail's own uptime text
                    # above -- rebuilt unconditionally every tick for that reason (same
                    # scroll-position tradeoff _build_bots_body's docstring accepts).
                    self._body.original_widget = self._build_incidents_body()
                    self._draw_screen()
                elif self._view == "collector":
                    # Unlike Bots/Incidents above, this pane preserves scroll position
                    # (Story 6.1 fix) -- _refresh_collector_body mutates the existing
                    # ListBox in place rather than the redraw loop assigning a fresh one.
                    self._refresh_collector_body()
                    self._body.original_widget = self._collector_body
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
        collector_listener_task = loop.create_task(
            collector_state._redis_listener(self._redis_url)
        )
        history_poll_task = loop.create_task(bot_history_state.poll_loop(self._redis_url))
        incidents_poll_task = loop.create_task(bot_incidents_state.poll_loop(self._redis_url))
        redraw_task = loop.create_task(self._redraw_loop())
        try:
            self._main_loop.run()
        finally:
            listener_task.cancel()
            snapshot_listener_task.cancel()
            bots_listener_task.cancel()
            collector_listener_task.cancel()
            history_poll_task.cancel()
            incidents_poll_task.cancel()
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
