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
bot_tui urwid app shell (Story 4.1, AC1-AC4; Story 4.2, AC1-AC5; Story 4.3, AC1-AC7):
MainLoop wiring, breadcrumb/footer, Coins pane, a Bots-pane stub, the `:` command bar,
the `/` inline filter, the `m` Ranking-Mode toggle, a pane-level stale badge, a
full-screen Coin-detail view (live indicators + collapsible order-book ladder + `o`
dashboard deep-link), and `esc`/`:q` navigation.

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

_PALETTE = [
    ("stale", "yellow", "default"),
    ("bid", "dark green", "default"),
    ("ask", "dark red", "default"),
]

_LADDER_COLLAPSED_LEVELS = 1
_LADDER_EXPANDED_LEVELS = 20

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
            # Stub placeholder (Story 4.4 builds real content) -- deliberately distinct
            # from the "Bots" breadcrumb label so the pane doesn't look like a rendering
            # bug (bare repeat of the pane name).
            return urwid.Filler(urwid.Text("no bots yet"), valign="top")

        if self._view == "coin_detail":
            return self._build_coin_detail_body()

        # "coins" -- returned as-is, whatever it currently holds. Never rebuilt here;
        # only _refresh_coins_body() (called when the Coins pane's own data actually
        # changes, not on a mere view switch) ever changes what this holds.
        return self._coins_body

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
            if key == "enter":
                self._submit_command()
            elif key == "esc":
                self._close_command_bar()
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
                    if self._main_loop is not None:
                        self._main_loop.draw_screen()
                elif self._view == "coin_detail":
                    # Breadcrumb refreshed every tick too (Review finding, post-4.3) --
                    # same reasoning as the Coins-pane breadcrumb above: its stale badge
                    # must keep re-evaluating from the passage of time alone, even once
                    # snapshots:raw stops arriving and _LATEST_SNAPSHOT's identity never
                    # changes again.
                    self._refresh_breadcrumb()
                    self._body.original_widget = self._build_body()
                    if self._main_loop is not None:
                        self._main_loop.draw_screen()
            except Exception:
                logger.exception("redraw loop iteration failed")
            await asyncio.sleep(_REDRAW_POLL_SECONDS)

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
        redraw_task = loop.create_task(self._redraw_loop())
        try:
            self._main_loop.run()
        finally:
            listener_task.cancel()
            snapshot_listener_task.cancel()
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
