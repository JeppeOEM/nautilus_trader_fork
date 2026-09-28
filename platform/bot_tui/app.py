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
bot_tui urwid app shell (Story 4.1, AC1-AC4; Story 4.4, AC1-AC4; Story 4.7, AC1-AC4;
Story 25.1a): MainLoop wiring, breadcrumb/footer, the `:` command bar, a Bots pane (the
start view: live per-bot PnL/status rows, per-row stale badges, `s` start/stop with a
footer-echo confirmation), a full-screen Bot-detail view (live-snapshot header,
left/right- (or h/l-) stepped trades blotter + PnL sparkline sourced from Story 4.6's
bots:history:* keys, `o` dashboard deep-link), a
Collector pane (Story 6.1: every currently-collected instrument -- always pinned,
there is no "collected but not pinned" state -- with liquid status, `p` to unpin (stop
+ add to config.exclude) and `x` to stop (don't exclude), both behind the same
type-to-confirm guard as Bots-pane's `s`, `:start <ID>`/`:pintop` command-bar actions to
pin one coin by name or fill empty slots with the current top-by-volume coins -- all
published to collector:control, read back via collector:status; since Story 25.1b its last
line is the nightly archive maintenance, read-only from archive:status; since Story 29.2
one section per venue, the actions refused with the reason on a venue whose plan does not
accept commands), and `esc`/`:q` navigation.

The TUI is a control surface for bots and the collector only (Story 25.1a, operator
decision 2026-09-26): rankings, the ranking-mode switch and the single-coin view are
web-only, so nothing here subscribes to the rankings or raw-snapshot feeds
(tests/test_no_rankings_feed.py).

`s` on a running bot does not stop it immediately -- it opens a type-to-confirm prompt
(operator must type "stop" + Enter) before the stop command is published, per an
explicit operator request: a bare `s` keypress is too easy to hit by accident to let it
directly stop a live/paper bot. Starting a stopped bot has no such guard -- only
stopping a running one carries real-world consequence.

This is the only file in this package allowed to import urwid and hold live async
state. Command dispatch and view-stack pop-back are implemented as plain, urwid-free
functions (_dispatch_command / _pop_view) so they're unit-testable without a real
screen -- see Story 4.1's Dev Notes "Testing strategy: pure logic vs. urwid wiring."
"""

import asyncio
import datetime as dt
import logging
import os
import socket
import sys
import time
import webbrowser
from pathlib import Path

import urwid
from observability import error_ledger

from bot_tui import archive_state
from bot_tui import bot_history_state
from bot_tui import bot_incidents_state
from bot_tui import bots_pane
from bot_tui import bots_state
from bot_tui import collector_pane
from bot_tui import collector_state


logger = logging.getLogger(__name__)

# Bots is the start view (Story 25.1a: the Coins pane it used to open on is web-only now).
_START_VIEW = "bots"

_BREADCRUMB_LABELS = {"bots": "Bots", "collector": "Collector", "help": "Help"}

# Bots pane's own footer (Story 4.4). Enter (open Bot-detail) and `j`/`k` row-focus
# movement are "free" via urwid.ListBox and aren't advertised as distinct features.
_BOTS_FOOTER_HINT_TEXT = "s start/stop  : command  esc back  :q quit"

# Bot-detail's own footer (Story 4.5 added s/esc; Story 4.7 adds left/right (h/l) for
# the new blotter/PnL-sparkline regions' history range). No j/k (scroll) hint -- the
# blotter's own ListBox scrolling is "free" the same way the Bots-pane's j/k movement
# already is.
_BOT_DETAIL_FOOTER_HINT_TEXT = (
    "s start/stop  h/l range  o dashboard  v strategy  i incidents  esc back  :q quit"
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

# View id -> its footer. Every view has exactly one entry, so an unknown view fails loudly
# (KeyError) instead of falling back to another view's keys.
_FOOTER_HINT_TEXTS = {
    "bots": _BOTS_FOOTER_HINT_TEXT,
    "bot_detail": _BOT_DETAIL_FOOTER_HINT_TEXT,
    "strategy": _STRATEGY_FOOTER_HINT_TEXT,
    "incidents": _INCIDENTS_FOOTER_HINT_TEXT,
    "collector": _COLLECTOR_FOOTER_HINT_TEXT,
    "help": _HELP_FOOTER_HINT_TEXT,
}

# Read-only bind mount of bots/strategies/dummy.py (docker-compose.yml's bot_tui service) -- the
# `v` key's only way to reach the strategy's source, since bot_tui's own image
# (collector.dockerfile) never COPYs bots/ in (AD-8's module isolation stays
# intact: this mount is view-only, no import/execution of bots code happens
# here). The container path lies outside every package directory (Story 26.3).
# Hardcoded to the one strategy this system currently runs -- see
# bots.infrastructure.nautilus_host's build_node(), which attaches DummyStrategy directly rather
# than by string path; revisit as a per-bot lookup (bots:status already carries a
# `strategy` class-name field) if a second strategy is ever added.
_STRATEGY_SOURCE_PATH = Path(
    os.environ.get("STRATEGY_SOURCE_PATH", "/app/strategy_source/strategy.py")
)

# Full control reference shown by `:h`/`:help` (see _COMMAND_ALIASES below) -- one
# section per view, listing every key that view's own footer hint above only
# abbreviates. Plain text, not urwid markup: nothing here needs color.
_HELP_TEXT = """GLOBAL
  :          command bar (bots / data / help, :q to quit)
  :h, :help  open this help
  esc        back one view
  :q         quit

  Rankings, the ranking mode and coin detail live in the web UI (its home page, /).

BOTS PANE
  j/k, up/down  move selection
  s             start/stop highlighted bot (stop asks for confirmation)
  enter         open bot detail

BOT DETAIL
  s          start/stop this bot (stop asks for confirmation)
  h/l, left/right  step PnL/trades history range back/forward
  o          open dashboard bot page in browser
  v          view this bot's strategy source (read-only, scrollable)
  i          view this bot's incidents log: restarts, WS/data-stale spans
  esc        back to bots

COLLECTOR PANE (:data)
  One section per venue (dYdX, Bybit, Hyperliquid), headed
  "<VENUE>: N collected +P pending · cap C", then its last apply (what the collector
  last subscribed, unsubscribed or failed), its rows ("pending" = planned, not yet
  subscribed) and its "unpinned" line. "~" marks a stale row or section.
  Only a venue whose plan accepts commands (today: dYdX) can be changed here. On the
  others (Bybit, Hyperliquid: a static plan) p, x and :start are refused with the
  reason -- edit that venue's config.toml and restart its collector instead.
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
                   pinned, immediately (rejected past the venue's cap)
  :pintop          fill every empty dYdX slot (up to dYdX's cap) with the
                   current top-by-volume coins not already collected, pinned
                   immediately -- never removes or replaces an existing coin, and
                   never re-adds a coin you've explicitly unpinned (that only ever
                   happens via :start <ID>)
  esc              back

  A venue's "unpinned" line is its config.exclude as a whole -- it shows any
  excluded id, whether it got there via p or a hand-edit of config.toml.
  The last line is the nightly archive maintenance (archive:status): the last run's
  day, outcome (ok / findings / FAILED with the failing steps) and times, or the
  running job, and the next run; "~" marks it stale. Run it now from the web UI."""

_PALETTE = [
    ("stale", "yellow", "default"),
    ("pnl-pos", "dark green", "default"),
    ("pnl-neg", "dark red", "default"),
    # Row-focus indicator for the Bots/Collector-pane ListBoxes -- "default,standout"
    # reverses the terminal's own default fg/bg rather than picking a fixed color, so
    # it still reads correctly against any terminal theme (same UX-DR1 "inherit the
    # terminal's own default" discipline the other palette entries already follow).
    ("focus", "default,standout", "default"),
]

# Bot-detail's trades-blotter region (Story 4.7) -- a fixed visible height inside a
# scrollable ListBox (BoxAdapter), not "however many fills happen to exist" (Story
# 4.6 already caps the wire contract at 500 trades; this caps what's visible at once
# on screen, independently, via ordinary urwid scrolling for the rest).
_BOT_DETAIL_BLOTTER_HEIGHT = 8

# Returned by _dispatch_command to signal `:q` -- kept out of urwid's ExitMainLoop so
# this function stays a plain, urwid-free, directly-testable function.
_QUIT_SENTINEL = "__quit__"

# Command-bar word -> view id. Defaults to each view id typing itself (":bots" ->
# "bots"), except "collector" -- the command bar uses ":data" for that view instead,
# so its own view id is deliberately absent from the left-hand side here.
_RECOGNIZED_COMMANDS = {v: v for v in _BREADCRUMB_LABELS if v != "collector"}
_RECOGNIZED_COMMANDS["data"] = "collector"

# Shorthand accepted by the command bar in addition to the full names above --
# resolved before the _RECOGNIZED_COMMANDS check in _dispatch_command, so ":h" behaves
# exactly like ":help".
_COMMAND_ALIASES = {"h": "help"}

# Poll interval for picking up new bots:status/collector:status/archive:status state and
# redrawing -- urwid does not auto-redraw for state changed by a background asyncio.Task (see
# urwid's Main Loop docs: "you must call MainLoop.draw_screen() manually"), so app.py owns a
# small loop that re-renders the active view and triggers a redraw.
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


def _collector_command_refusal(action: str, instrument_id: str | None) -> str | None:
    """
    Return why a `:start <ID>`/`:pintop` must not be published, or None to publish it.

    `:start` targets its id's venue; `:pintop` carries no id and only dYdX's plan can pin by
    liquidity. The cap check is instant feedback against the venue's published cap (skipped
    while unknown) -- the collector refuses past its cap regardless.
    """
    venue = (
        collector_state.LEGACY_PLAN_VENUE
        if instrument_id is None
        else collector_state.venue_of_row(instrument_id)
    )
    target = "pintop" if instrument_id is None else f"{action} {instrument_id}"
    refusal = collector_state.command_refusal(venue)
    if refusal is not None:
        return f"cannot {target}: {refusal}"
    cap = collector_state.plan_cap(venue)
    if action == "start" and cap is not None and collector_state.collected_count(venue) >= cap:
        return f"cannot {target}: at {cap}-instrument cap"
    return None


def _pop_view(current_view: str, stack: list[str]) -> tuple[str, list[str]]:
    """Esc pops back exactly one level; never raises, no-ops at the root (AC4)."""
    if not stack:
        return current_view, []
    return stack[-1], stack[:-1]


class _SelectableBotRow(urwid.Text):
    """
    A Bots-pane row that ListBox can move focus to/from, but that never itself consumes
    a keypress, so `s`/Enter act on whichever row is highlighted. Plain
    urwid.Text.selectable() is False -- verified directly against urwid==4.0.6 that a
    ListBox of plain Text rows never moves focus_position on "up"/"down" at all.
    Returning `key` unchanged from keypress() (rather than None) means ListBox handles
    up/down navigation itself, and Enter bubbles all the way to unhandled_input
    unconsumed, exactly like every other currently-unhandled key already does.
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


def _focused_collector_id(body: urwid.Widget) -> str | None:
    """Return the id of the Collector ListBox's focused row; None on a section line or no list."""
    if not isinstance(body, urwid.ListBox):
        return None
    focus_widget = body.focus
    if not isinstance(focus_widget, urwid.AttrMap):
        return None
    row = focus_widget.original_widget
    if not isinstance(row, _SelectableCollectorRow):
        return None
    return row.instrument_id


class BotTuiApp:
    """Owns the urwid Frame, the view stack, and the async wiring to the bots/collector state."""

    def __init__(self, redis_url: str = bots_state.REDIS_URL) -> None:
        self._redis_url = redis_url
        self._view = _START_VIEW
        self._stack: list[str] = []
        self._command_active = False
        self._background_tasks: set[asyncio.Task] = set()
        self._dashboard_base_url = os.environ.get("DASHBOARD_BASE_URL", "http://127.0.0.1:9100")

        # Bot-detail state (Story 4.5). No open_bot()/close_bot() lifecycle pair is
        # needed for the snapshot -- bots:status is already accumulated unconditionally
        # for every bot regardless of which view is active (see _open_bot_detail's own
        # comment).
        self._bot_detail_bot_id: str | None = None

        # Bot-detail's history state (Story 4.7). Unlike the bots:status snapshot
        # above, bots:history *does* have an explicit open/close lifecycle
        # (bot_history_state.open_bot()/close_bot()) -- see that module's own
        # docstring for why. Always resets to "day" on a fresh Bot-detail open
        # (_open_bot_detail), never carried over from a previous bot.
        self._bot_history_range: str = "day"
        # The ListBox itself persists across the redraw loop's every-tick rebuild (only
        # its SimpleListWalker's contents are replaced in place) so mid-scroll position
        # survives a data refresh (TUI-01). None until _open_bot_detail creates it fresh
        # for the newly-opened bot.
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

        # Collector pane's body: a persistent object, mutated in place rather than rebuilt
        # on every redraw tick or view switch (TUI-01, Story 6.1 fix -- see
        # _refresh_collector_body). _collector_shape is None until the first refresh
        # builds it for real; that sentinel guarantees the first call always constructs
        # fresh rather than skipping because "cold_open" happens to already match.
        self._collector_shape: str | None = None
        self._collector_body: urwid.Widget = urwid.Filler(
            urwid.Text(collector_pane.COLD_OPEN_TEXT), valign="top"
        )

        # Bots pane's body, same persistent-object/scroll-preservation contract as
        # Collector above (see _refresh_bots_body) -- previously rebuilt fresh
        # every redraw tick (_build_bots_body), which reset scroll/focus to the top
        # within _REDRAW_POLL_SECONDS of any user scroll. Confirmed in production as
        # "the bots page jumps up" -- the same bug already found and fixed once for
        # the Collector pane (Story 6.1); platform/CLAUDE.md now has a standing rule
        # against reintroducing it a third time in a future pane.
        self._bots_shape: str | None = None
        self._bots_body: urwid.Widget = urwid.Filler(
            urwid.Text(bots_pane.COLD_OPEN_TEXT), valign="top"
        )

        # The start view's body holds real rows from the first draw, not a cold-open
        # placeholder that only the redraw loop's first tick would replace.
        self._refresh_bots_body()

        self._breadcrumb = urwid.Text(_BREADCRUMB_LABELS[self._view])
        self._refresh_breadcrumb()
        self._footer_hint = urwid.Text("")
        self._refresh_footer_hint()
        self._command_edit = urwid.Edit(":")
        self._stop_confirm_edit = urwid.Edit("")
        self._body = urwid.WidgetPlaceholder(self._build_body())
        self._frame = urwid.Frame(
            header=self._breadcrumb,
            body=self._body,
            footer=self._footer_hint,
        )

        self._main_loop: urwid.MainLoop | None = None

    def _build_body(self) -> urwid.Widget:
        if self._view == "bots":
            # Returned as-is, whatever it currently holds -- same "never rebuilt
            # here" discipline as Collector below; only _refresh_bots_body()
            # (called by the redraw loop while this view is active) ever changes
            # what this holds.
            return self._bots_body

        if self._view == "bot_detail":
            return self._build_bot_detail_body()

        if self._view == "strategy":
            return self._build_strategy_body()

        if self._view == "incidents":
            return self._build_incidents_body()

        if self._view == "collector":
            # Returned as-is, whatever it currently holds -- same "never rebuilt here"
            # discipline as the Bots pane above; only _refresh_collector_body() (called
            # by the redraw loop while this view is active) ever changes what this holds.
            return self._collector_body

        if self._view != "help":
            raise KeyError(f"unknown view {self._view!r}")
        return urwid.Filler(urwid.Text(_HELP_TEXT), valign="top")

    def _refresh_bots_body(self) -> None:
        """
        Rebuild/mutate self._bots_body to reflect current bots:status data.

        Follows _refresh_collector_body's fix (TUI-01): only the two genuinely-
        different-shape transitions (cold-open <-> populated) swap self._bots_body's
        type; a same-shape update mutates the existing ListBox's SimpleListWalker in
        place via slice-assignment, which is what actually preserves scroll/focus
        position. Each row's stale badge and uptime
        text must keep advancing purely from the passage of time, so the redraw loop
        still calls this every _REDRAW_POLL_SECONDS while this view is active --
        without the in-place mutation, that would silently wipe a user's scroll
        position within half a second on every single redraw tick, not just on a
        real bots:status change. This was the exact previously-reported "the bots
        page jumps up" bug -- see platform/CLAUDE.md for the standing rule this and
        _refresh_collector_body's identical prior fix are both now recorded under.
        """
        statuses = bots_state._LATEST_STATUSES
        if not statuses:
            if self._bots_shape != "cold_open":
                self._bots_body = urwid.Filler(urwid.Text(bots_pane.COLD_OPEN_TEXT), valign="top")
                self._bots_shape = "cold_open"
            return

        now = time.time()
        rows = bots_pane.bot_rows(statuses)
        widgets = [
            self._build_bot_row_widget(row, bots_state.is_stale(row["bot_id"], now=now), now)
            for row in rows
        ]
        if self._bots_shape != "rows":
            self._bots_body = urwid.ListBox(urwid.SimpleListWalker(widgets))
            self._bots_shape = "rows"
        else:
            listbox = self._bots_body
            assert isinstance(listbox, urwid.ListBox)
            listbox.body[:] = widgets  # type: ignore[index]

    def _refresh_collector_body(self) -> None:
        """
        Rebuild/mutate self._collector_body to reflect current collector:status data.

        TUI-01's reference implementation (also mirrored by _refresh_bots_body): only
        the two genuinely-different-shape transitions (cold-open <-> populated) swap
        self._collector_body's type; a same-shape update mutates the existing ListBox's
        SimpleListWalker in place via slice-assignment, which is what actually preserves
        scroll/focus position. Collector rows update far less often than bots rows
        (collector:status republishes every liquidity_check_seconds, default 30 min,
        plus immediately after a control action) but the redraw loop still calls this
        every _REDRAW_POLL_SECONDS while this view is active (so a row's stale marker
        still appears promptly once collector_state.is_stale() flips) -- without this
        fix, a user's up/down scroll would be silently wiped within half a second on
        every single redraw tick, not just on a real data change. Confirmed in
        production: this was exactly the reported bug.
        """
        statuses = collector_state._LATEST_COLLECTOR_STATUS
        plans = collector_state._LATEST_PLANS
        archive_line = collector_pane.format_archive_line(
            archive_state._LATEST_ARCHIVE_STATUS,
            dt.datetime.now(dt.UTC),
            stale=archive_state.is_stale(),
        )
        if not statuses and not plans:
            self._set_collector_filler(f"{collector_pane.COLD_OPEN_TEXT}\n\n{archive_line}")
            return

        widgets: list[urwid.Widget] = []
        for section in collector_pane.venue_sections(statuses, plans):
            widgets.extend(self._build_collector_section_widgets(section))
            widgets.append(urwid.Text(""))
        # Same non-selectable Text rule as the section lines; always shown, so a missing
        # archive service reads "no status yet" rather than nothing.
        widgets.append(urwid.Text(archive_line))
        # Taken before the walker changes: a section line appearing or vanishing above the
        # focused row shifts every row below it, so focus follows the id, not the position.
        focused_id = (
            _focused_collector_id(self._collector_body) if self._collector_shape == "rows" else None
        )
        if self._collector_shape != "rows":
            self._collector_body = urwid.ListBox(urwid.SimpleListWalker(widgets))
            self._collector_shape = "rows"
        else:
            listbox = self._collector_body
            assert isinstance(listbox, urwid.ListBox)
            listbox.body[:] = widgets  # type: ignore[index]
        self._keep_collector_focus_on_a_row(focused_id)

    def _build_collector_section_widgets(
        self, section: collector_pane.VenueSection
    ) -> list[urwid.Widget]:
        """
        One venue's widgets: the header, the last-apply and refusal-reason lines, the rows and
        the unpinned line. Everything but a row is a plain, non-selectable urwid.Text, so
        ListBox's own up/down never lands on it (same precedent as _SelectableBotRow's
        docstring).
        """
        venue = section.venue
        stale_plan = section.plan is not None and collector_state.plan_is_stale(venue)
        lines = [
            collector_pane.format_section_header(section, stale_plan),
            collector_pane.format_last_apply_line(section.plan),
            collector_state.command_refusal(venue) or "",
        ]
        widgets: list[urwid.Widget] = [urwid.Text(line) for line in lines if line]
        show_liquidity = collector_pane.shows_liquidity(venue, section.plan)
        for row in section.rows:
            stale = collector_state.is_stale(row["id"])
            widgets.append(self._build_collector_row_widget(row, stale, show_liquidity))
        unpinned = (section.plan or {}).get("unpinned_ids", [])
        unpinned_line = collector_pane.format_unpinned_line(unpinned)
        if unpinned_line:
            widgets.append(urwid.Text(unpinned_line))
        return widgets

    def _keep_collector_focus_on_a_row(self, focused_id: str | None = None) -> None:
        """
        Keep the ListBox's focus on the row of `focused_id` (the row focused before the
        refresh) wherever it moved, so a refresh never silently moves `p`/`x` to another
        instrument. Once that row is gone (a stop, an unpin, a sweep), focus lands on whichever
        row now holds its position, possibly another venue's; `p`/`x` still name the id in their
        confirm prompt. A focus resting on a section line moves onto a row: a fresh ListBox starts
        on the first header, and `p`/`x` would then silently act on nothing. The nearest row at or
        below the position wins, else the last one above it; no rows, no move.
        """
        listbox = self._collector_body
        assert isinstance(listbox, urwid.ListBox)
        walker = listbox.body
        for i, widget in enumerate(walker):
            row = widget.original_widget if isinstance(widget, urwid.AttrMap) else None
            if isinstance(row, _SelectableCollectorRow) and row.instrument_id == focused_id:
                listbox.focus_position = i
                return
        position = listbox.focus_position
        if isinstance(walker[position], urwid.AttrMap):
            return
        rows = [i for i, w in enumerate(walker) if isinstance(w, urwid.AttrMap)]
        if rows:
            below = [i for i in rows if i > position]
            listbox.focus_position = below[0] if below else rows[-1]

    def _set_collector_filler(self, text: str) -> None:
        if self._collector_shape != "cold_open":
            self._collector_body = urwid.Filler(urwid.Text(text), valign="top")
            self._collector_shape = "cold_open"
            return
        # Same shape: update the text in place (the archive line changes while no
        # collector:status has arrived), keeping the widget object per TUI-01.
        filler = self._collector_body
        assert isinstance(filler, urwid.Filler)
        filler.original_widget.set_text(text)

    def _build_collector_row_widget(
        self, row: dict, stale: bool, show_liquidity: bool
    ) -> urwid.Widget:
        markup = collector_pane.format_collector_line(row, stale, show_liquidity)
        return urwid.AttrMap(
            _SelectableCollectorRow(markup, instrument_id=row["id"]), None, focus_map="focus"
        )

    def _highlighted_collector_id(self) -> str | None:
        """
        Mirrors _highlighted_bot_id's read-the-ListBox's-own-focus pattern; None when the
        focus rests on a section line rather than an instrument row.
        """
        return _focused_collector_id(self._body.original_widget)

    def _build_bot_row_widget(self, row: dict, stale: bool, now: float) -> urwid.Widget:
        # Color applied only to the PnL segment (sign, not magnitude) -- "fixed position +
        # color, never color alone"; the sign is also always in the text itself via
        # bots_pane.format_pnl.
        pnl_value = row["realized_pnl"] + row["unrealized_pnl"]
        pnl_color = "pnl-pos" if pnl_value >= 0 else "pnl-neg"
        prefix = "~ " if stale else "  "
        running_text = "run" if row["running"] else "off"
        markup = [
            prefix,
            f"{bots_pane.fit(row['bot_id'], bots_pane.BOT_ID_WIDTH)} ",
            (pnl_color, bots_pane.format_pnl(pnl_value)),
            f"  {bots_pane.fit(row['symbol'], bots_pane.SYMBOL_WIDTH)} "
            f"{row['mode']:<5} {running_text:<3} {row['position_side']:<5} "
            f"{bots_pane.format_exposure(row['net_exposure'])}  "
            f"up {bots_pane.format_uptime(row['started_at'], now)}  "
            f"wr {bots_pane.format_win_rate(row['win_rate'])}",
        ]
        return urwid.AttrMap(
            _SelectableBotRow(markup, bot_id=row["bot_id"]), None, focus_map="focus"
        )

    def _highlighted_bot_id(self) -> str | None:
        """Return the Bots-pane row currently focused, read off the ListBox's own focus."""
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
        # pre-existing None-threaded shape to inherit that discipline from (Story 4.5,
        # Dev Notes item 7).
        bot_id = self._bot_detail_bot_id
        status = bots_state._LATEST_STATUSES.get(bot_id) if bot_id is not None else None
        if status is None:
            self._bot_detail_listbox = None
            return urwid.Filler(urwid.Text("no status yet"), valign="top")

        pnl_value = status["realized_pnl"] + status["unrealized_pnl"]
        pnl_color = "pnl-pos" if pnl_value >= 0 else "pnl-neg"
        lines = bots_pane.bot_detail_lines(status, now=time.time())
        # Color only the PnL segment within line 2 -- same "fixed position + color,
        # never color alone" precedent as the Bots-pane row (_build_bot_row_widget).
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
            # Slice-assignment on the existing SimpleListWalker, not a fresh ListBox
            # (TUI-01): preserves scroll/focus position across the redraw loop's
            # every-tick rebuild.
            self._bot_detail_listbox.body[:] = widgets  # type: ignore[index]
        return self._bot_detail_listbox

    def _build_strategy_body(self) -> urwid.Widget:
        # A plain ListBox (not Filler+Text, unlike the static Help view) -- a
        # strategy source file can easily exceed one screen's height, and ListBox
        # gets free up/down/page scrolling the same way the Bots-pane row list
        # already does, with no extra wiring.
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

    def _refresh_breadcrumb(self) -> None:
        if self._view == "bot_detail":
            # Verbatim mockup format (mockups/key-bot-detail.html: "Bots > bot-03").
            # Stale-badge source is bots_state.is_stale (Story 4.4's own per-bot
            # heartbeat function).
            breadcrumb_text = f"Bots > {self._bot_detail_bot_id}"
            if self._bot_detail_bot_id is not None and bots_state.is_stale(self._bot_detail_bot_id):
                self._breadcrumb.set_text([breadcrumb_text, "  ", ("stale", "~ STALE")])
            else:
                self._breadcrumb.set_text(breadcrumb_text)
            return
        if self._view == "strategy":
            # No stale badge here -- a source file's own contents have no heartbeat
            # concept, unlike the live bots:status feed Bot-detail badges.
            self._breadcrumb.set_text(f"Bots > {self._bot_detail_bot_id} > strategy")
            return
        if self._view == "incidents":
            # No stale badge either -- bots:incidents only changes on a real
            # transition (bot_status._incident_transition), so a long-unchanged read
            # is the expected healthy state, not staleness.
            self._breadcrumb.set_text(f"Bots > {self._bot_detail_bot_id} > incidents")
            return
        # Top-level panes: per-row stale markers live in the rows themselves (Bots-pane
        # "~ " prefix, Collector-pane stale marker), so the breadcrumb is the plain label.
        self._breadcrumb.set_text(_BREADCRUMB_LABELS[self._view])

    def _refresh_footer_hint(self) -> None:
        self._footer_hint.set_text(_FOOTER_HINT_TEXTS[self._view])

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
        # Publish-and-wait, never optimistic (AC3): no local running/stopped flip
        # happens here -- the row only reflects the new
        # state once the bot's own next bots:status heartbeat carries it back.
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
        self._confirm_row_action("unpin")

    def _confirm_row_action(self, action: str) -> None:
        """
        Open the type-to-confirm guard for `action` on the focused row -- unless its venue's
        plan cannot take commands, when the footer shows why and nothing opens or publishes.
        """
        instrument_id = self._highlighted_collector_id()
        if instrument_id is None:
            return
        refusal = collector_state.command_refusal(collector_state.venue_of_row(instrument_id))
        if refusal is not None:
            self._footer_hint.set_text(f"cannot {action} {instrument_id}: {refusal}")
            return
        self._open_collector_confirm(action, instrument_id)

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
        # Both are set only while the confirm is active.
        assert action is not None
        assert instrument_id is not None
        text = self._stop_confirm_edit.edit_text.strip().lower()
        if text != action:
            verb = self._COLLECTOR_CONFIRM_VERBS[action]
            self._stop_confirm_edit.set_caption(
                f"type '{action}' to confirm -- {verb} {instrument_id}? esc to cancel: "
            )
            self._stop_confirm_edit.set_edit_text("")
            return
        self._close_collector_confirm()
        # Re-checked: the venue's aggregate may have withdrawn `accepts_commands` while the
        # operator was typing.
        refusal = collector_state.command_refusal(collector_state.venue_of_row(instrument_id))
        if refusal is not None:
            self._footer_hint.set_text(f"cannot {action} {instrument_id}: {refusal}")
            return
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

    def _open_bot_detail(self, bot_id: str) -> None:
        # Plain-stack push, the same mechanism _dispatch_command uses. No bots:status
        # subscription-lifecycle call is needed here -- bots_state.py already
        # accumulates every bot's latest status unconditionally, regardless of which
        # view is active (Story 4.4's design has no per-bot subscribe/unsubscribe
        # concept to open/close on entry/exit here).
        self._stack = [*self._stack, self._view]
        self._view = "bot_detail"
        self._bot_detail_bot_id = bot_id
        # Reset to "day" and start tracking this bot's history fresh on every entry
        # (Story 4.7) -- never carries over a previous bot's range or stale data.
        self._bot_history_range = "day"
        self._bot_detail_listbox = None
        bot_history_state.open_bot(bot_id)
        bot_incidents_state.open_bot(bot_id)
        self._refresh_breadcrumb()
        self._refresh_footer_hint()
        self._body.original_widget = self._build_body()
        self._frame.focus_position = "body"

    def _open_strategy_view(self) -> None:
        # Same plain-stack push _open_bot_detail already uses --
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

    def _open_url(self, url: str) -> None:
        """
        Open `url` on the operator's machine: the local open_listener hand-off first,
        else webbrowser.open() plus an OSC 52 clipboard copy. Bot-detail's `o` is the
        only caller since Story 25.1a deleted the Coins-pane/Coin-detail chart links
        that used to share it.
        """
        if self._open_via_local_listener(url):
            self._footer_hint.set_text(f"dashboard: {url}")
            return
        # webbrowser.open() is a harmless no-op inside this product's headless
        # Docker/SSH deployment (no DISPLAY reachable) but genuinely opens a real
        # browser when bot_tui is run on-host -- both are real deployment shapes, so
        # the footer text below is shown unconditionally, never gated on this
        # attempt's (unreliable, environment-dependent) outcome.
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("webbrowser.open failed for %s", url)
        sys.stdout.write(bots_pane.osc52_copy_sequence(url))
        sys.stdout.flush()
        self._footer_hint.set_text(f"dashboard (copied to clipboard): {url}")

    @staticmethod
    def _open_via_local_listener(url: str) -> bool:
        """
        Best-effort hand-off to platform/scripts/open_listener.go running on the
        operator's own machine (see troll-tui's -R reverse SSH tunnel in
        ~/.zshrc) -- lets an `o` press on a VPS-hosted bot_tui actually pop a
        Firefox tab locally, which webbrowser.open() alone can't do with no
        DISPLAY on the remote host.

        BOT_TUI_OPEN_URL_PORT unset (a local, non-SSH bot_tui run, or troll-tui
        without the listener running) short-circuits to False immediately --
        same fast, silent fallthrough to the existing webbrowser.open()+OSC52
        path as a refused/timed-out connection.
        """
        port = os.environ.get("BOT_TUI_OPEN_URL_PORT")
        if not port:
            return False
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3) as sock:
                sock.sendall(url.encode("utf-8"))
        except OSError:
            return False
        return True

    def _bot_history_range_back(self) -> None:
        self._set_bot_history_range(bots_pane.previous_range(self._bot_history_range))

    def _bot_history_range_forward(self) -> None:
        self._set_bot_history_range(bots_pane.next_range(self._bot_history_range))

    def _set_bot_history_range(self, range_name: str) -> None:
        # Footer-echoes the new range and redraws immediately (Story 4.7, AC2) --
        # doesn't wait for the redraw loop's own next tick: a keystroke is itself a
        # synchronous urwid input event, redrawn automatically after it is processed.
        self._bot_history_range = range_name
        self._footer_hint.set_text(f"range: {self._bot_history_range}")
        self._body.original_widget = self._build_body()

    def _open_dashboard_bot(self) -> None:
        # Only reachable via "o" while self._view == "bot_detail", which
        # _open_bot_detail always sets alongside a real bot_id -- never None in
        # practice; the assert narrows the Optional for the type checker.
        assert self._bot_detail_bot_id is not None
        self._open_url(
            bots_pane.dashboard_bot_url(self._dashboard_base_url, self._bot_detail_bot_id)
        )

    def _submit_command(self) -> None:
        text = self._command_edit.edit_text
        parsed = _parse_collector_command(text)
        if parsed is not None:
            action, instrument_id = parsed
            refusal = _collector_command_refusal(action, instrument_id)
            if refusal is not None:
                self._command_edit.set_caption(f"{refusal}\n:")
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

        # `:` opens the command bar from any page (every footer hint advertises
        # ":q quit") -- checked ahead of the view-specific branches below, which
        # otherwise return early and never reach _handle_global_key's own dispatch.
        if key == ":":
            self._open_command_bar()
            return None

        if self._view == "bot_detail":
            self._handle_bot_detail_key(key)
            return None

        self._handle_global_key(key)
        return None

    def _handle_global_key(self, key: str) -> None:
        # Any key not handled below is a deliberate no-op -- including the retired
        # Coins-pane keys (`/`, `m`, space, Enter-to-coin), which went web-only with
        # Story 25.1a.
        if key == "esc":
            new_view, new_stack = _pop_view(self._view, self._stack)
            if new_view != self._view or new_stack != self._stack:
                self._switch_view(new_view, new_stack)
        elif self._view == "bots":
            self._handle_bots_pane_key(key)
        elif self._view == "collector":
            self._handle_collector_pane_key(key)

    def _handle_bots_pane_key(self, key: str) -> None:
        # Extracted from _handle_global_key (Story 4.5) -- same cognitive-complexity-
        # threshold reasoning as _handle_bot_detail_key below.
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
            self._confirm_row_action("stop")

    def _handle_bot_detail_key(self, key: str) -> None:
        # Extracted from _handle_global_key: keeps each dispatch function under this
        # codebase's cognitive-complexity threshold as more per-view keys accumulate.
        if key == "s":
            self._toggle_bot()
        elif key in ("left", "h"):
            self._bot_history_range_back()
        elif key in ("right", "l"):
            self._bot_history_range_forward()
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

    async def _redraw_loop(self) -> None:
        # Every live view is re-rendered every tick so time-driven text (stale badges,
        # uptimes, an open incident's duration) keeps advancing. Bots, Collector and
        # Bot-detail protect their scroll position by mutating a persisted ListBox's
        # walker in place (TUI-01: _refresh_bots_body/_refresh_collector_body/
        # _build_bot_detail_body) instead of handing back a fresh ListBox every call;
        # Incidents does not yet (see its NOTE below).
        while True:
            try:
                if self._view == "bots":
                    # Unlike the old _build_bots_body, this pane now preserves scroll
                    # position the same way Collector does (see _refresh_bots_body) --
                    # each row's stale badge/uptime text is still rebuilt every tick,
                    # but mutates the existing ListBox in place rather than the redraw
                    # loop assigning a fresh one.
                    self._refresh_bots_body()
                    self._body.original_widget = self._bots_body
                    self._draw_screen()
                elif self._view == "bot_detail":
                    # Breadcrumb (and its stale badge) needs to keep advancing purely
                    # from wall-clock time. The body is rebuilt every tick too -- scroll
                    # position is protected by _build_bot_detail_body itself (see the
                    # comment on self._bot_detail_listbox), not by gating this call.
                    self._refresh_breadcrumb()
                    self._body.original_widget = self._build_bot_detail_body()
                    self._draw_screen()
                elif self._view == "incidents":
                    # An open incident's "ongoing (Nm..)" duration must keep advancing
                    # purely from wall-clock time, same as Bot-detail's own uptime text
                    # above -- rebuilt unconditionally every tick for that reason.
                    # NOTE: this still constructs a fresh ListBox every tick, same
                    # class of bug _refresh_bots_body/_refresh_collector_body were
                    # fixed for (platform/CLAUDE.md's standing rule) -- not fixed here
                    # since it wasn't reported, but a future scroll-jump complaint on
                    # this pane has the same known cause and fix shape.
                    self._body.original_widget = self._build_incidents_body()
                    self._draw_screen()
                elif self._view == "collector":
                    # Like Bots above (and unlike Incidents), this pane preserves scroll
                    # position (Story 6.1 fix) -- _refresh_collector_body mutates the existing
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

        bots_listener_task = loop.create_task(bots_state._redis_listener(self._redis_url))
        collector_listener_task = loop.create_task(collector_state._redis_listener(self._redis_url))
        archive_listener_task = loop.create_task(archive_state._redis_listener(self._redis_url))
        history_poll_task = loop.create_task(bot_history_state.poll_loop(self._redis_url))
        incidents_poll_task = loop.create_task(bot_incidents_state.poll_loop(self._redis_url))
        redraw_task = loop.create_task(self._redraw_loop())
        try:
            self._main_loop.run()
        finally:
            bots_listener_task.cancel()
            collector_listener_task.cancel()
            archive_listener_task.cancel()
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
    error_ledger.start()  # durable error ledger (story 23.3); no-op without ERROR_LEDGER_DIR
    BotTuiApp().run()


if __name__ == "__main__":
    main()
