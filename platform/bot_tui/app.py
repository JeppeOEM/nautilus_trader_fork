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
bots:history:* keys), a
Collector pane (Story 6.1: every currently-collected instrument -- always pinned,
there is no "collected but not pinned" state -- with liquid status, `p` to unpin (stop
+ add to config.exclude) and `x` to stop (don't exclude), both behind the same
type-to-confirm guard as Bots-pane's `s`, `:start <ID>`/`:pintop` command-bar actions to
pin one coin by name or fill empty slots with the current top-by-volume coins -- all
published to collector:control, read back via collector:status; since Story 25.1b its last
line is the nightly archive maintenance, read-only from archive:status; since Story 29.2
one section per venue, the actions refused with the reason on a venue whose plan does not
accept commands; since Story 29.5 a `/` market browser that searches every venue's market names
from markets:live and adds the focused one with `a`, behind the same guard), and `esc`/`:q`
navigation.

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
import time
from collections.abc import Coroutine
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
from bot_tui import market_browser
from bot_tui import markets_state


logger = logging.getLogger(__name__)

# Bots is the start view (Story 25.1a: the Coins pane it used to open on is web-only now).
_START_VIEW = "bots"

_BREADCRUMB_LABELS = {"bots": "Bots", "collector": "Collector", "help": "Help"}

# Bots pane's own footer (Story 4.4). Enter (open Bot-detail) and `j`/`k` row-focus
# movement (the pane's `_VimListBox`, DW-258) aren't advertised as distinct features; `:h`'s
# help lists both.
_BOTS_FOOTER_HINT_TEXT = "s start/stop  : command  esc back  :q quit"

# Bot-detail's own footer (Story 4.5 added s/esc; Story 4.7 adds left/right (h/l) for
# the new blotter/PnL-sparkline regions' history range). No up/down (scroll) hint -- the
# blotter's own ListBox scrolling is "free" via urwid.ListBox.
_BOT_DETAIL_FOOTER_HINT_TEXT = "s start/stop  h/l range  v strategy  i incidents  esc back  :q quit"

# Help view's own footer -- nothing to do here but leave (:h/:help got you in).
_HELP_FOOTER_HINT_TEXT = "esc back  :q quit"

# Strategy-source view's own footer -- read-only, nothing to do here but scroll/leave.
_STRATEGY_FOOTER_HINT_TEXT = "esc back  :q quit"

# Incidents-log view's own footer -- also read-only.
_INCIDENTS_FOOTER_HINT_TEXT = "esc back  :q quit"

# Collector pane's own footer (Story 6.1) -- `:start <ID>`/`:pintop` are command-bar-
# only (no single key maps cleanly to "type an instrument id"), so only p/x get key hints.
# `/` opens the market browser (Story 29.5), the key-driven way to find an id to add.
_COLLECTOR_FOOTER_HINT_TEXT = "p unpin  x stop  / markets  : command  esc back  :q quit"

# The market browser's own footer (Story 29.5). Enter (from the search edit to the results) is
# implied by the search edit itself, like the Bots pane's Enter.
_MARKETS_FOOTER_HINT_TEXT = "a add  / search  esc back  :q quit"

# View id -> its footer. Every view has exactly one entry, so an unknown view fails loudly
# (KeyError) instead of falling back to another view's keys.
_FOOTER_HINT_TEXTS = {
    "bots": _BOTS_FOOTER_HINT_TEXT,
    "bot_detail": _BOT_DETAIL_FOOTER_HINT_TEXT,
    "strategy": _STRATEGY_FOOTER_HINT_TEXT,
    "incidents": _INCIDENTS_FOOTER_HINT_TEXT,
    "collector": _COLLECTOR_FOOTER_HINT_TEXT,
    "markets": _MARKETS_FOOTER_HINT_TEXT,
    "help": _HELP_FOOTER_HINT_TEXT,
}

# Read-only bind mounts of each strategy's source, one file per strategy class named
# `<class name>.py` (docker-compose.yml's bot_tui service: `DummyStrategy.py` from
# bots/strategies/dummy.py, `CandlePatternStrategy.py` from
# research/strategies/candle_pattern_strategy.py) -- the `v` key's only way to reach a
# strategy's source, since bot_tui's own image (collector.dockerfile) never COPYs bots/ in (AD-8's
# module isolation stays intact: the mounts are view-only, no import/execution of bots or
# research code happens here). The view picks the file by the bot's own `bots:status` `strategy`
# field (its class name, `StrategyCacheReader.strategy_name`), so each bot shows the strategy it
# actually runs. The container directory lies outside every package directory (Story 26.3).
_STRATEGY_SOURCE_DIR = Path(os.environ.get("STRATEGY_SOURCE_DIR", "/app/strategy_source"))

# Full control reference shown by `:h`/`:help` (see _COMMAND_ALIASES below) -- one
# section per view, listing every key that view's own footer hint above only
# abbreviates. Plain text, not urwid markup: nothing here needs color.
_HELP_TEXT = f"""GLOBAL
  :          command bar (bots / data / help, :q to quit)
  :h, :help  open this help
  esc        back one view
  :q         quit

  Rankings, the ranking mode and coin detail live in the web UI (its home page, /).

BOTS PANE
  j/k, up/down  move selection
  s             start/stop highlighted bot (stop asks for confirmation); refused,
                nothing sent, while the row is stale ("~": no bots:status for
                over {bots_state._BOT_STALE_SECONDS:.0f}s -- its supervisor, the only
                consumer of the command, is down)
  enter         open bot detail

BOT DETAIL
  s          start/stop this bot (stop asks for confirmation); refused while stale,
             as on the Bots pane
  h/l, left/right  step PnL/trades history range back/forward
  v          view this bot's strategy source (read-only, scrollable)
  i          view this bot's incidents log: restarts, WS/data-stale spans
  esc        back to bots

COLLECTOR PANE (:data)
  One section per venue (dYdX, Bybit, Hyperliquid), headed
  "<VENUE>: N collected +P pending · cap C" ("· no cap" on Bybit and Hyperliquid), then
  its last apply (what the collector last subscribed, unsubscribed or failed), its rows
  ("pending" = planned, not yet subscribed) and its "unpinned" line. "~" marks a stale
  row or section. Every venue's plan accepts p, x and :start, each command addressed to
  the id's venue; a venue whose status is over an hour old (collector down?) is refused
  with the reason. :pintop is dYdX's only.
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
                   pinned, immediately (rejected past dYdX's cap; Bybit and
                   Hyperliquid have none)
  :pintop          fill every empty dYdX slot (up to dYdX's cap) with the
                   current top-by-volume coins not already collected, pinned
                   immediately -- never removes or replaces an existing coin, and
                   never re-adds a coin you've explicitly unpinned (that only ever
                   happens via :start <ID>)
  /                open the market browser (below)
  esc              back

  A venue's "unpinned" line is its config.exclude as a whole -- it shows any
  excluded id, whether it got there via p or a hand-edit of config.toml.
  The last line is the nightly archive maintenance (archive:status): the last run's
  day, outcome (ok / findings / FAILED with the failing steps) and times, or the
  running job, and the next run; "~" marks it stale. Run it now from the web UI.
  A venue's "last refusal" line is the latest command its collector refused, and why.

MARKET BROWSER (/ from the Collector pane)
  Every market each venue lists, from markets:live (the ranking engine publishes each
  venue's names once a minute; names only). Before the first message it reads "waiting
  for markets:live…". Results are grouped per venue under that venue's Collector header
  plus "· M matches"; "~" marks a venue whose list is over 3 minutes old, and a venue
  silent for 15 minutes leaves the browser.
  /                search: type part of a symbol or id (case-insensitive, live);
                   empty lists everything
  enter            from the search to the results (the query is kept)
  esc              close the search (query kept); on the results, back to Collector
  j/k, up/down     move selection
  a                add the highlighted market (type 'add' + enter to confirm). Refused
                   with the reason, nothing sent, when the venue's plan takes no
                   commands, the id is already collected or pending, it is excluded
                   (re-add with :start <ID>), an add is still awaiting its answer, or
                   dYdX's cap is reached. Bybit and Hyperliquid have no cap.
  Row markers: collected; pending (in the plan, not yet subscribed -- with the last
  apply's subscribe failure when it names the id); failed: <reason> (the collector
  refused your add); excluded (unpinned); pending (your add, awaiting
  collector:status); no answer from <VENUE> collector (nothing answered within 2 min;
  a may be pressed again). Only collector:status ever makes a row "collected"."""

_PALETTE = [
    ("stale", "yellow", "default"),
    ("pnl-pos", "dark green", "default"),
    ("pnl-neg", "dark red", "default"),
    # Row-focus indicator for the Bots/Collector-pane ListBoxes -- "default,standout"
    # reverses the terminal's own default fg/bg rather than picking a fixed color, so
    # it still reads correctly against any terminal theme (same UX-DR1 "inherit the
    # terminal's own default" discipline the other palette entries already follow).
    ("focus", "default,standout", "default"),
    # An open position with no stop-loss (Story 29.6): the `sl` cell's `none` text carries the
    # meaning, this is the extra cue.
    (bots_pane.WARNING_ATTR, "light red", "default"),
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

# How long a quit waits for in-flight background work (a bots:control/collector:control publish)
# before cancelling it (DW-58): long enough for a healthy Redis round trip, short enough that `:q`
# still feels immediate when Redis is unreachable.
_SHUTDOWN_GRACE_SECONDS = 2.0

# The ledger site of a background task a quit had to cancel: an operator command lost at quit must
# be loud, never silently dropped (DATA-07).
_SHUTDOWN_CANCELLED_SITE = "bot_tui.shutdown_cancelled"
# A task that ignored its cancellation at quit, and one that had ended in an exception.
_SHUTDOWN_STUCK_SITE = "bot_tui.shutdown_stuck"
_SHUTDOWN_FAILED_SITE = "bot_tui.shutdown_failed"


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


def _expect[W](widget: object, cls: type[W]) -> W:
    """
    Return `widget` narrowed to `cls`, raising TypeError naming both types otherwise (DW-92): an
    explicit guard that, unlike `assert`, still holds under `python -O`, so a widget of the wrong
    shape fails loudly instead of being mutated as if it were the right one.
    """
    if not isinstance(widget, cls):
        raise TypeError(f"expected {cls.__name__}, got {type(widget).__name__}")
    return widget


def _segment_markup(segments: list[bots_pane.Segment]) -> list[str | tuple[str, str]]:
    """Return urwid markup for bots_pane's (attr, text) segments: plain text where no attr."""
    return [(attr, text) if attr is not None else text for attr, text in segments]


def _shutdown_tasks(
    loop: asyncio.AbstractEventLoop, tasks: list[asyncio.Task], commands: set[asyncio.Task]
) -> None:
    """
    Drain `loop` at quit and close it (DW-58). `tasks` -- the listeners, the redraw loop (which
    must not draw onto the terminal urwid has just restored) -- are cancelled and awaited first.
    `commands` (bots:control/collector:control publishes) then get up to
    `_SHUTDOWN_GRACE_SECONDS` to finish before the rest are cancelled and awaited too; a publish
    cut short is ledgered, since the operator's command may never have reached Redis. Every
    task's outcome is retrieved, so a failure is ledgered rather than lost; a task that ignores
    its cancellation is ledgered once as stuck and abandoned rather than hanging the quit.
    Async generators and the default executor (bounded by the same grace: a resolver thread stuck
    on a dead DNS must not hold `:q`) are shut down last.
    """
    # urwid's AsyncioEventLoop.run() leaves its exception handler installed, and it stops the loop
    # on any reported exception: a done-callback or async-generator finaliser failing mid-drain
    # would abort a run_until_complete below and skip the ledgering and loop.close(). The drain
    # retrieves every task outcome itself, so asyncio's default handler (log only) is restored.
    loop.set_exception_handler(None)
    commands_at_quit = list(commands)
    for task in tasks:
        task.cancel()
    _await_cancelled(loop, tasks)
    in_flight = [task for task in commands_at_quit if not task.done()]
    if in_flight:
        loop.run_until_complete(asyncio.wait(in_flight, timeout=_SHUTDOWN_GRACE_SECONDS))
    cut_short = [task for task in in_flight if not task.done()]
    for task in cut_short:
        task.cancel()
    _await_cancelled(loop, cut_short)
    for task in cut_short:
        # A stuck one is already ledgered by _await_cancelled; one that finished despite the
        # cancel did send.
        if task.done() and task.cancelled():
            error_ledger.record(
                _SHUTDOWN_CANCELLED_SITE,
                f"{task.get_name()} still running {_SHUTDOWN_GRACE_SECONDS:g}s after quit: "
                "cancelled, it may never have been sent",
            )
    _ledger_failed_tasks([*tasks, *commands_at_quit])
    loop.run_until_complete(loop.shutdown_asyncgens())
    _shutdown_default_executor(loop)
    loop.close()


def _shutdown_default_executor(loop: asyncio.AbstractEventLoop) -> None:
    """
    Join the default executor's threads for at most `_SHUTDOWN_GRACE_SECONDS`, ledgering a join
    that timed out (`loop.close()` then shuts the executor down without waiting). Known limit:
    the interpreter still joins such a thread at exit, so a resolver call stuck past the grace
    delays the process exit (not the drain) until it returns; upgrade path: a resolver with its
    own timeout in redis-py's connect.
    """
    try:
        loop.run_until_complete(
            asyncio.wait_for(loop.shutdown_default_executor(), _SHUTDOWN_GRACE_SECONDS)
        )
    except TimeoutError:
        error_ledger.record(
            _SHUTDOWN_STUCK_SITE,
            f"default executor threads still running {_SHUTDOWN_GRACE_SECONDS:g}s after quit",
        )


def _await_cancelled(loop: asyncio.AbstractEventLoop, tasks: list[asyncio.Task]) -> None:
    """
    Await cancelled `tasks` for at most `_SHUTDOWN_GRACE_SECONDS`, ledgering one still running (a
    cleanup stuck on a dead connection) instead of hanging the quit on it.
    """
    if not tasks:
        return
    loop.run_until_complete(asyncio.wait(tasks, timeout=_SHUTDOWN_GRACE_SECONDS))
    for task in tasks:
        if not task.done():
            error_ledger.record(
                _SHUTDOWN_STUCK_SITE,
                f"{task.get_name()} still running {_SHUTDOWN_GRACE_SECONDS:g}s after its "
                "cancellation at quit",
            )


def _ledger_failed_tasks(tasks: list[asyncio.Task]) -> None:
    """Ledger every finished task that ended in an exception (its outcome retrieved, never lost)."""
    for task in tasks:
        if not task.done() or task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            error_ledger.record(_SHUTDOWN_FAILED_SITE, f"{task.get_name()} failed", exc)


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


class _VimListBox(urwid.ListBox):
    """A ListBox whose j/k move the selection like down/up, as the market browser's help says."""

    _VIM_KEYS = {"j": "down", "k": "up"}

    def keypress(self, size: tuple[int, int], key: str) -> str | None:  # type: ignore[override]
        mapped = self._VIM_KEYS.get(key, key)
        unhandled = super().keypress(size, mapped)
        return key if unhandled == mapped else unhandled


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


def _keep_focus_on_row(listbox: urwid.ListBox, focused_id: str | None) -> None:
    """
    Keep `listbox`'s focus on the row of `focused_id` (the row focused before a refresh) wherever
    it moved. Once that row is gone, focus lands on whichever row now holds its position. A focus
    resting on a section line moves onto a row: a fresh ListBox starts on the first header, and a
    row action would then silently act on nothing. The nearest row at or below the position wins,
    else the last one above it; no rows, no move. Shared by the Collector pane and the market
    browser (Story 29.5).
    """
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


class BotTuiApp:
    """Owns the urwid Frame, the view stack, and the async wiring to the bots/collector state."""

    def __init__(self, redis_url: str = bots_state.REDIS_URL) -> None:
        self._redis_url = redis_url
        self._view = _START_VIEW
        self._stack: list[str] = []
        self._command_active = False
        # In-flight bots:control/collector:control publishes (`_track_background`): a quit gives
        # them a grace to finish.
        self._background_tasks: set[asyncio.Task] = set()

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
        # Populated, the body is `_bots_listbox` under the column header (Story 29.6), both built
        # once per shape change: the ListBox inside the Frame is the persistent object whose
        # walker each tick mutates.
        self._bots_shape: str | None = None
        self._bots_body: urwid.Widget = urwid.Filler(
            urwid.Text(bots_pane.COLD_OPEN_TEXT), valign="top"
        )
        self._bots_listbox: urwid.ListBox | None = None

        # The market browser (Story 29.5): the same persistent-body/shape contract (TUI-01), plus
        # `_markets_key`, the inputs its walker was last built from -- about 1,100 rows (Bybit
        # linear + spot) are rebuilt only when one changes, not on every redraw tick. The query is
        # kept across searches and visits; `_markets_search_active` while the footer's `/` edit
        # owns the keyboard.
        self._markets_shape: str | None = None
        self._markets_body: urwid.Widget = urwid.Filler(
            urwid.Text(market_browser.COLD_OPEN_TEXT), valign="top"
        )
        self._markets_key: tuple | None = None
        self._markets_query = ""
        self._markets_search_active = False
        self._markets_edit = urwid.Edit("/")
        urwid.connect_signal(self._markets_edit, "postchange", self._on_markets_query_change)

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

        if self._view == "markets":
            # Same "never rebuilt here" discipline: only _refresh_markets_body() changes it.
            return self._markets_body

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

        # Two clocks (DW-60): uptime is the wire's wall-clock `started_at`, staleness this TUI's
        # monotonic receive stamps.
        wall_now = time.time()
        monotonic_now = time.monotonic()
        rows = bots_pane.bot_rows(statuses)
        widgets = [
            self._build_bot_row_widget(
                row, bots_state.is_stale(row["bot_id"], now=monotonic_now), wall_now
            )
            for row in rows
        ]
        if self._bots_shape != "rows" or self._bots_listbox is None:
            self._bots_listbox = _VimListBox(urwid.SimpleListWalker(widgets))
            self._bots_body = urwid.Frame(
                self._bots_listbox, header=urwid.Text(bots_pane.bots_header_line())
            )
            self._bots_shape = "rows"
        else:
            self._bots_listbox.body[:] = widgets  # type: ignore[index]

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
            self._collector_body = _VimListBox(urwid.SimpleListWalker(widgets))
            self._collector_shape = "rows"
        else:
            listbox = _expect(self._collector_body, urwid.ListBox)
            listbox.body[:] = widgets  # type: ignore[index]
        self._keep_collector_focus_on_a_row(focused_id)

    def _build_collector_section_widgets(
        self, section: collector_pane.VenueSection
    ) -> list[urwid.Widget]:
        """
        One venue's widgets: the header, the last-apply, last-refusal (Story 29.5) and
        refusal-reason lines, the rows and the unpinned line. Everything but a row is a plain,
        non-selectable urwid.Text, so ListBox's own up/down never lands on it (same precedent as
        _SelectableBotRow's docstring).
        """
        venue = section.venue
        stale_plan = section.plan is not None and collector_state.plan_is_stale(venue)
        lines = [
            collector_pane.format_section_header(section, stale_plan),
            collector_pane.format_last_apply_line(section.plan),
            collector_pane.format_last_refusal_line(section.plan),
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
        Keep the Collector ListBox's focus on the row of `focused_id` (`_keep_focus_on_row`), so
        a refresh never silently moves `p`/`x` to another instrument. Once that row is gone (a
        stop, an unpin, a sweep), focus may land on another venue's row; `p`/`x` still name the
        id in their confirm prompt.
        """
        _keep_focus_on_row(_expect(self._collector_body, urwid.ListBox), focused_id)

    def _set_collector_filler(self, text: str) -> None:
        if self._collector_shape != "cold_open":
            self._collector_body = urwid.Filler(urwid.Text(text), valign="top")
            self._collector_shape = "cold_open"
            return
        # Same shape: update the text in place (the archive line changes while no
        # collector:status has arrived), keeping the widget object per TUI-01.
        _expect(self._collector_body, urwid.Filler).original_widget.set_text(text)

    def _build_collector_row_widget(
        self, row: dict, stale: bool, show_liquidity: bool
    ) -> urwid.Widget:
        markup = collector_pane.format_collector_line(row, stale, show_liquidity)
        return urwid.AttrMap(
            _SelectableCollectorRow(markup, instrument_id=row["id"]), None, focus_map="focus"
        )

    # --- market browser (Story 29.5) ----------------------------------------------------------

    def _refresh_markets_body(self) -> None:
        """
        Rebuild/mutate self._markets_body from markets:live and collector:status, TUI-01's way
        (cold-open <-> populated swaps the widget; a same-shape update slice-assigns the walker),
        and only when `_markets_input_key` changed. Focus follows the focused id across a
        rebuild, as in the Collector pane. `now` is monotonic: every time compared here is one of
        this TUI's own receive/send stamps (DW-60).
        """
        now = time.monotonic()
        markets = markets_state.live_markets(now)
        if not markets:
            self._set_markets_filler(market_browser.COLD_OPEN_TEXT)
            return
        query = self._markets_query.strip().casefold()
        key = (query, self._markets_input_key(markets, now))
        if self._markets_shape == "rows" and key == self._markets_key:
            return
        # A new query starts on its first match: following the old focused id's position would
        # land `a` on whatever row of whichever venue happens to sit there now.
        query_changed = self._markets_key is None or self._markets_key[0] != query
        self._markets_key = key
        widgets = self._build_markets_widgets(markets, now)
        focused_id = (
            _focused_collector_id(self._markets_body) if self._markets_shape == "rows" else None
        )
        if self._markets_shape != "rows":
            self._markets_body = _VimListBox(urwid.SimpleListWalker(widgets))
            self._markets_shape = "rows"
        else:
            _expect(self._markets_body, urwid.ListBox).body[:] = widgets  # type: ignore[index]
        listbox = _expect(self._markets_body, urwid.ListBox)
        if query_changed:
            listbox.focus_position = 0
            focused_id = None
        _keep_focus_on_row(listbox, focused_id)

    def _set_markets_filler(self, text: str) -> None:
        self._markets_key = None
        if self._markets_shape != "cold_open":
            self._markets_body = urwid.Filler(urwid.Text(text), valign="top")
            self._markets_shape = "cold_open"

    @staticmethod
    def _markets_input_key(markets: dict[str, list[tuple[str, str]]], now: float) -> tuple:
        """
        Everything the browser's rows are built from, bar the query (added by the caller): each
        venue's list (its receive time) and stale flag, each status row's presence and pending
        mark, each aggregate (its receive time) and stale flag, each refused add's reason and each
        sent add with its time-driven no-answer flip.
        """
        venues = tuple(markets)
        timeout = market_browser.ADD_ANSWER_TIMEOUT_SECONDS
        statuses = collector_state._LATEST_COLLECTOR_STATUS
        return (
            tuple(
                (v, markets_state.received_at(v), markets_state.venue_markets_stale(v, now))
                for v in venues
            ),
            tuple(sorted((iid, row.get("pending") is True) for iid, row in statuses.items())),
            tuple(sorted(collector_state._PLAN_RECEIVED_AT.items())),
            tuple(collector_state.plan_is_stale(v, now) for v in venues),
            tuple(sorted(collector_state._ADD_REFUSALS.items())),
            tuple(
                sorted(
                    (iid, at, now - at > timeout) for iid, at in collector_state.sent_adds().items()
                )
            ),
        )

    def _build_markets_widgets(
        self, markets: dict[str, list[tuple[str, str]]], now: float
    ) -> list[urwid.Widget]:
        sections = {
            section.venue: section
            for section in collector_pane.venue_sections(
                collector_state._LATEST_COLLECTOR_STATUS, collector_state._LATEST_PLANS
            )
        }
        widgets: list[urwid.Widget] = []
        for group in market_browser.search(markets, self._markets_query):
            section = sections.get(group.venue) or collector_pane.VenueSection(
                group.venue, [], None
            )
            widgets.extend(self._market_group_widgets(group, section, now))
            widgets.append(urwid.Text(""))
        return widgets

    def _market_group_widgets(
        self, group: market_browser.BrowserGroup, section: collector_pane.VenueSection, now: float
    ) -> list[urwid.Widget]:
        """One venue's header (a plain, non-selectable Text) and its selectable result rows."""
        venue = group.venue
        plan_stale = section.plan is not None and collector_state.plan_is_stale(venue, now)
        markets_stale = markets_state.venue_markets_stale(venue, now)
        header = market_browser.format_group_header(group, section, plan_stale, markets_stale)
        widgets: list[urwid.Widget] = [urwid.Text(header)]
        for row in group.rows:
            marker = market_browser.result_marker(self._market_add_context(row.instrument_id), now)
            line = market_browser.format_result_line(row, marker, markets_stale)
            widgets.append(
                urwid.AttrMap(
                    _SelectableCollectorRow(line, instrument_id=row.instrument_id),
                    None,
                    focus_map="focus",
                )
            )
        return widgets

    @staticmethod
    def _market_add_context(instrument_id: str) -> market_browser.AddContext:
        venue = collector_state.venue_of_row(instrument_id)
        return market_browser.AddContext(
            instrument_id=instrument_id,
            venue=venue,
            status_row=collector_state._LATEST_COLLECTOR_STATUS.get(instrument_id),
            plan=collector_state._LATEST_PLANS.get(venue),
            sent_at=collector_state.sent_add_at(instrument_id),
            refused_reason=collector_state.add_refused_reason(instrument_id),
        )

    def _market_add_refusal(self, instrument_id: str) -> str | None:
        """Return why `a` must not add `instrument_id` now (`market_browser.add_refusal`)."""
        ctx = self._market_add_context(instrument_id)
        now = time.monotonic()
        return market_browser.add_refusal(
            ctx,
            now,
            venue_refusal=collector_state.command_refusal(ctx.venue, now),
            cap=collector_state.plan_cap(ctx.venue),
            count=collector_state.collected_count(ctx.venue) + self._adds_in_flight(ctx, now),
        )

    def _adds_in_flight(self, ctx: market_browser.AddContext, now: float) -> int:
        """Return this TUI's other adds on the id's venue still awaiting their answer."""
        return sum(
            1
            for iid in collector_state.sent_adds()
            if iid != ctx.instrument_id
            and collector_state.venue_of_row(iid) == ctx.venue
            and market_browser.add_awaiting(self._market_add_context(iid), now)
        )

    def _show_markets(self) -> None:
        self._refresh_markets_body()
        self._body.original_widget = self._markets_body
        self._refresh_breadcrumb()

    def _open_markets_view(self) -> None:
        """`/` on the Collector pane: push the browser on the view stack and open its search."""
        self._stack = [*self._stack, self._view]
        self._view = "markets"
        self._refresh_footer_hint()
        self._show_markets()
        self._frame.focus_position = "body"
        self._open_markets_search()

    def _open_markets_search(self) -> None:
        self._markets_search_active = True
        self._markets_edit.set_edit_text(self._markets_query)
        self._markets_edit.set_edit_pos(len(self._markets_query))
        self._frame.footer = self._markets_edit
        self._frame.focus_position = "footer"

    def _close_markets_search(self) -> None:
        """Enter or esc in the search edit: back to the results, the query kept."""
        self._markets_search_active = False
        # A `cannot add`/`sent:` echo from before the search reopened is no longer current.
        self._refresh_footer_hint()
        self._frame.footer = self._footer_hint
        self._frame.focus_position = "body"

    def _on_markets_query_change(self, _edit: urwid.Edit, _old_text: str) -> None:
        """Refilter the results at once on every keystroke (the edit's `postchange` signal)."""
        self._markets_query = self._markets_edit.edit_text
        if self._view == "markets":
            self._show_markets()

    def _add_focused_market(self) -> None:
        """
        Handle `a` on a result: refuse with the reason in the footer (nothing sent), else open the
        type-to-confirm guard -- typed word `add`, wire action `start` (`CollectionPlan.add`).
        """
        instrument_id = _focused_collector_id(self._body.original_widget)
        if instrument_id is None:
            return
        refusal = self._market_add_refusal(instrument_id)
        if refusal is not None:
            self._footer_hint.set_text(f"cannot add {instrument_id}: {refusal}")
            return
        self._open_collector_confirm("add", instrument_id)

    def _highlighted_collector_id(self) -> str | None:
        """
        Mirrors _highlighted_bot_id's read-the-ListBox's-own-focus pattern; None when the
        focus rests on a section line rather than an instrument row.
        """
        return _focused_collector_id(self._body.original_widget)

    def _build_bot_row_widget(self, row: dict, stale: bool, now: float) -> urwid.Widget:
        # The markup is bots_pane's own segments -- the same text as format_bot_line, with the
        # PnL sign and an unprotected position's stop colored ("fixed position + color, never
        # color alone").
        markup = _segment_markup(bots_pane.bot_line_segments(row, stale, now))
        return urwid.AttrMap(
            _SelectableBotRow(markup, bot_id=row["bot_id"]), None, focus_map="focus"
        )

    def _highlighted_bot_id(self) -> str | None:
        """Return the Bots-pane row currently focused, read off the ListBox's own focus."""
        body = self._body.original_widget
        if isinstance(body, urwid.Frame):
            # The populated pane: the rows' ListBox under the column header.
            body = body.body
        if not isinstance(body, urwid.ListBox):
            return None
        focus_widget = body.focus
        if focus_widget is None:
            return None
        row = _expect(_expect(focus_widget, urwid.AttrMap).original_widget, _SelectableBotRow)
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
        status = bots_state.latest_status(bot_id) if bot_id is not None else None
        if status is None:
            self._bot_detail_listbox = None
            return urwid.Filler(urwid.Text("no status yet"), valign="top")

        # bots_pane's own segments (DW-85): the PnL segment carries its sign's color -- same
        # "fixed position + color, never color alone" precedent as the Bots-pane row
        # (_build_bot_row_widget). Wall clock: uptime and last fill are wire timestamps.
        line_widgets = [
            urwid.Text(_segment_markup(line))
            for line in bots_pane.bot_detail_segments(status, now=time.time())
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
        source = self._strategy_source_path()
        if isinstance(source, str):
            return urwid.Filler(urwid.Text(source), valign="top")
        try:
            lines = source.read_text().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            return urwid.Filler(urwid.Text(f"could not read {source}: {e}"), valign="top")
        return urwid.ListBox(urwid.SimpleListWalker([urwid.Text(line) for line in lines]))

    def _strategy_source_path(self) -> Path | str:
        """
        Return the mounted source of the open bot's strategy class, from its latest
        `bots:status`, or the line saying why there is none. The class name arrives over Redis,
        so one that is not a plain identifier never gets to pick a path outside
        `_STRATEGY_SOURCE_DIR`.
        """
        bot_id = self._bot_detail_bot_id
        status = bots_state.latest_status(bot_id) if bot_id is not None else None
        if status is None:
            return f"no bots:status received yet for {bot_id}: strategy unknown"
        strategy = status.get("strategy")
        if not isinstance(strategy, str) or not strategy.isidentifier():
            return f"{bot_id} reports strategy {strategy!r}, not a class name: no source to show"
        return _STRATEGY_SOURCE_DIR / f"{strategy}.py"

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
        if self._view == "markets":
            # Special-cased like bot_detail: a `markets` breadcrumb label would make `:markets` a
            # command. The query is shown too, since the results stay filtered by it after the
            # search edit closes.
            query = self._markets_query.strip()
            self._breadcrumb.set_text(f"Collector > markets{f'  /{query}' if query else ''}")
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
        # A stale row has no live supervisor to consume the command, in either direction
        # (bots_state.control_refusal, DW-74): say so instead of sending into the void.
        refusal = bots_state.control_refusal(bot_id)
        if refusal is not None:
            self._footer_hint.set_text(refusal)
            return
        status = bots_state.latest_status(bot_id)
        running = bool(status.get("running")) if status is not None else False
        if running:
            self._open_stop_confirm(bot_id)
            return
        self._publish_bot_action(bot_id, "start")

    def _publish_bot_action(self, bot_id: str, action: str) -> None:
        # Publish-and-wait, never optimistic (AC3): no local running/stopped flip
        # happens here -- the row only reflects the new
        # state once the bot's own next bots:status heartbeat carries it back.
        self._track_background(
            bots_state.publish_control(self._redis_url, bot_id, action),
            f"bots:control {action} {bot_id}",
        )
        # AC3's literal footer-echo format: "sent: start bot-07" -- confirms the
        # command was sent, not that it succeeded.
        self._footer_hint.set_text(f"sent: {action} {bot_id}")

    def _track_background(self, coro: Coroutine[object, object, object], name: str) -> asyncio.Task:
        """
        Schedule `coro` as a background task named `name`, held in `_background_tasks` until it is
        done, so it is never garbage-collected mid-flight and a quit can drain it
        (`_shutdown_tasks`, which names it in the ledger).
        """
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

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

    def _publish_collector_action(
        self, action: str, instrument_id: str | None, add_sent_at: float | None = None
    ) -> None:
        # Publish-and-wait, never optimistic -- same discipline as _publish_bot_action:
        # no local pinned/collected flip happens here, the row only reflects the new
        # state once collector:status's next message (published immediately after the
        # collector applies the action -- see collector.py's _publish_status) arrives.
        # Addressed to the id's venue (Story 29.4); `:pintop` carries no id and pins only dYdX.
        venue = (
            collector_state.LEGACY_PLAN_VENUE
            if instrument_id is None
            else collector_state.venue_of_row(instrument_id)
        )
        label = f" {instrument_id}" if instrument_id is not None else ""
        task = self._track_background(
            collector_state.publish_control(self._redis_url, action, instrument_id, venue),
            f"collector:control {action}{label}",
        )
        self._footer_hint.set_text(f"sent: {action}{label}")

        def _on_published(done: asyncio.Future) -> None:
            if done.cancelled() or done.result() is not False:
                return
            self._footer_hint.set_text(f"failed to send {action}{label}: Redis publish failed")
            if instrument_id is not None and add_sent_at is not None:
                collector_state.forget_sent_add(instrument_id, add_sent_at)

        task.add_done_callback(_on_published)

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
    _COLLECTOR_CONFIRM_VERBS = {"stop": "stop collecting", "unpin": "unpin", "add": "add"}

    # The `collector:control` action a confirmed word sends, where the two differ: the market
    # browser's `add` is the existing `start` verb (`CollectionPlan.add`, Story 29.5), so the wire
    # is unchanged.
    _COLLECTOR_WIRE_ACTIONS = {"add": "start"}

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
        if action is None or instrument_id is None:
            raise RuntimeError("collector confirm submitted with no action/instrument (not open)")
        text = self._stop_confirm_edit.edit_text.strip().lower()
        if text != action:
            verb = self._COLLECTOR_CONFIRM_VERBS[action]
            self._stop_confirm_edit.set_caption(
                f"type '{action}' to confirm -- {verb} {instrument_id}? esc to cancel: "
            )
            self._stop_confirm_edit.set_edit_text("")
            return
        self._close_collector_confirm()
        # Re-checked: the venue's aggregate may have withdrawn `accepts_commands` -- or, for an
        # add, the id been collected or the cap filled -- while the operator was typing.
        refusal = self._collector_action_refusal(action, instrument_id)
        if refusal is not None:
            self._footer_hint.set_text(f"cannot {action} {instrument_id}: {refusal}")
            return
        sent_at = None
        if action == "add":
            # Monotonic, like every add-answer time it is compared with (DW-60).
            sent_at = time.monotonic()
            collector_state.record_sent_add(instrument_id, sent_at)
        self._publish_collector_action(
            self._COLLECTOR_WIRE_ACTIONS.get(action, action), instrument_id, sent_at
        )

    def _collector_action_refusal(self, action: str, instrument_id: str) -> str | None:
        if action == "add":
            return self._market_add_refusal(instrument_id)
        return collector_state.command_refusal(collector_state.venue_of_row(instrument_id))

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
        if bot_id is None:
            raise RuntimeError("stop confirm submitted with no bot (prompt not open)")
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
        # Re-checked, like _submit_collector_confirm's: the bot may have gone stale while the
        # operator was typing (DW-74).
        refusal = bots_state.control_refusal(bot_id)
        if refusal is not None:
            self._footer_hint.set_text(refusal)
            return
        # ...or a fresh heartbeat may already say it stopped: nothing left to stop.
        status = bots_state.latest_status(bot_id)
        if status is None or not status.get("running"):
            self._footer_hint.set_text(f"{bot_id} already stopped: nothing sent")
            return
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
        if self._markets_search_active:
            # Printable keys went into the edit; only these reach here.
            if key in ("enter", "esc"):
                self._close_markets_search()
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
        # Coins-pane keys (`m`, space, Enter-to-coin), which went web-only with Story
        # 25.1a; `/` is the Collector pane's market browser since Story 29.5.
        if key == "esc":
            new_view, new_stack = _pop_view(self._view, self._stack)
            if new_view != self._view or new_stack != self._stack:
                self._switch_view(new_view, new_stack)
        elif self._view == "bots":
            self._handle_bots_pane_key(key)
        elif self._view == "collector":
            self._handle_collector_pane_key(key)
        elif self._view == "markets":
            self._handle_markets_key(key)

    def _handle_markets_key(self, key: str) -> None:
        if key == "a":
            self._add_focused_market()
        elif key == "/":
            self._open_markets_search()

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
        elif key == "/":
            self._open_markets_view()

    def _handle_bot_detail_key(self, key: str) -> None:
        # Extracted from _handle_global_key: keeps each dispatch function under this
        # codebase's cognitive-complexity threshold as more per-view keys accumulate.
        if key == "s":
            self._toggle_bot()
        elif key in ("left", "h"):
            self._bot_history_range_back()
        elif key in ("right", "l"):
            self._bot_history_range_forward()
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
                    # from the passage of time. The body is rebuilt every tick too -- scroll
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
                elif self._view == "markets":
                    # Same persistent ListBox, rebuilt only when its inputs changed (Story 29.5).
                    self._refresh_markets_body()
                    self._body.original_widget = self._markets_body
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

        tasks = [
            loop.create_task(bots_state._redis_listener(self._redis_url)),
            loop.create_task(collector_state._redis_listener(self._redis_url)),
            loop.create_task(archive_state._redis_listener(self._redis_url)),
            loop.create_task(markets_state._redis_listener(self._redis_url)),
            loop.create_task(bot_history_state.poll_loop(self._redis_url)),
            loop.create_task(bot_incidents_state.poll_loop(self._redis_url)),
            loop.create_task(self._redraw_loop()),
        ]
        try:
            self._main_loop.run()
        finally:
            _shutdown_tasks(loop, tasks, self._background_tasks)


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
