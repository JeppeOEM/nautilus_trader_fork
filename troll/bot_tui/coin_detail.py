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
Pure Coin-detail row/URL-formatting functions (Story 4.3, AC1/AC4/AC5) -- no urwid
import, no I/O.

Every live indicator (microprice, OFI, OBI, spread, cvd, ...) shown in Coin-detail is
now read straight off ranking_engine's rankings:live rank entry for the open
instrument via rank_row_for() below -- bot_tui itself computes none of it (SSOT-02,
troll/CLAUDE.md: this used to be a 3rd independent computation of the same numbers,
alongside ranking_engine's own and dashboard's now-since-deleted copy). Only the raw
order-book ladder (bid/ask price/size arrays, no scalar-message equivalent) still comes
from coin_detail_state.py's own snapshots:raw subscription.
"""

import base64

from ml_signals.indicators import mid_price


# Verbatim UX copy (EXPERIENCE.md State Patterns: "Thin order book") -- keep exact,
# same discipline as coins_pane.py's COLD_OPEN_TEXT/NO_MATCHES_TEXT.
NO_BIDS_TEXT = "no bids"
NO_ASKS_TEXT = "no asks"


def rank_row_for(ranking: dict | None, instrument_id: str) -> dict | None:
    """The rankings:live rank entry matching instrument_id, or None if no rankings:live
    message has arrived yet or this instrument isn't (yet) in it -- e.g. a coin just
    opened before its first live tick has propagated through ranking_engine.
    """
    if ranking is None:
        return None
    for row in ranking.get("ranks", []):
        if isinstance(row, dict) and row.get("instrument_id") == instrument_id:
            return row
    return None


def format_indicator(value: float | None, decimals: int = 8) -> str:
    """
    Fixed-precision numeric string, or a quiet "warming up…" sentinel when the value
    isn't there yet (mirrors COLD_OPEN_TEXT's tone, per EXPERIENCE.md's Voice and Tone
    discipline) -- ranking_engine returns None for any indicator its own tracker hasn't
    initialized yet (e.g. a coin whose first tick just arrived), which is the entire
    signal Coin-detail needs; there's no separate "initialized" flag to track here now
    that the indicator classes themselves live only inside ranking_engine.
    """
    if value is None:
        return "warming up…"
    return f"{value:.{decimals}f}"


# Extra chars reserved on top of whatever the widest value actually needs this render,
# both for the order-book ladder and the live-indicators list (see WIDTH_MARGIN's use
# in app.py).
WIDTH_MARGIN = 3


def ratchet_width(needed: int, min_w: int) -> int:
    """
    A column width that only grows, never shrinks or regrows unnecessarily: stays at
    `min_w` (the previous tick's width, per the caller) as long as that already fits
    `needed` (this tick's actual longest value); only grows -- to `needed +
    WIDTH_MARGIN` fresh headroom -- once `min_w` is actually too narrow.

    Shared by order_book_lines (ladder size/price columns) and app.py (indicator value
    column) so both get the same "no left/right jump" behavior from one definition.
    Unconditionally computing `needed + WIDTH_MARGIN` every tick (an earlier version)
    still jumped the layout the instant a value grew by one digit (e.g. "9.70" ->
    "10.60"): a value that fits comfortably inside an already-reserved margin would
    still force a wider column under that formula, since the margin gets re-added to
    the new max instead of staying fixed while the value grows into the reserved slack.
    """
    return min_w if needed <= min_w else needed + WIDTH_MARGIN


def order_book_lines(
    bid_prices: list[float],
    bid_sizes: list[float],
    ask_prices: list[float],
    ask_sizes: list[float],
    levels: int,
    min_size_w: int = 0,
    min_price_w: int = 0,
) -> tuple[list[str], str, list[str], int, int]:
    """
    Classic centered ladder: returns (ask_lines, mid_line, bid_lines, size_w, price_w)
    for the caller to stack asks above / mid marker / bids below.

    ask_lines is ordered worst-to-best top-to-bottom (best ask last, nearest the middle
    marker); bid_lines is best-to-worst top-to-bottom (best bid first, nearest the
    middle marker) -- this is the reverse of dYdX's own best-first wire order for asks,
    reordered here (not in app.py) so the ordering stays covered by this module's own
    tests rather than only visible in the urwid tree.

    All three lines -- asks, mid, bids -- share one right-justified price column: a
    real order book's price axis is one column every row lines up against, not a
    screen-centered label, so mid_line is built from the same size_w/price_w as the
    levels rather than independently centered. Mid is read from the same book snapshot
    the levels themselves come from (not ranking_engine's own mid), so it can never
    disagree with the best bid/ask shown directly above/below it -- computed via
    ml_signals.indicators.mid_price on that same local snapshot, not a reimplemented
    formula (SSOT-01: mid price is a pure, single-snapshot derivation and must have
    exactly one implementation).

    size_w/price_w are a ratchet_width() of this render's actual values against
    min_size_w/min_price_w -- see that function's docstring. The caller (app.py) is
    expected to pass back the previous call's returned widths as the next call's
    min_size_w/min_price_w, so a column only ever grows over a Coin-detail session,
    never shrinks, and never regrows unless actually needed.
    """
    n_bid = min(levels, len(bid_prices))
    n_ask = min(levels, len(ask_prices))
    sizes = bid_sizes[:n_bid] + ask_sizes[:n_ask]
    prices = bid_prices[:n_bid] + ask_prices[:n_ask]
    size_strs = [f"{s:.6f}" for s in sizes]
    price_strs = [f"{p:.2f}" for p in prices]
    size_w = ratchet_width(max((len(s) for s in size_strs), default=0), min_size_w)
    price_w = ratchet_width(max((len(p) for p in price_strs), default=0), min_price_w)

    def _rows(side_prices: list[float], side_sizes: list[float], n: int) -> list[str]:
        return [f"{side_sizes[i]:>{size_w}.6f}  {side_prices[i]:>{price_w}.2f}" for i in range(n)]

    bid_rows = _rows(bid_prices, bid_sizes, n_bid) if bid_prices else [NO_BIDS_TEXT]
    ask_rows = _rows(ask_prices, ask_sizes, n_ask) if ask_prices else [NO_ASKS_TEXT]

    mid = mid_price({"bid_prices": bid_prices, "ask_prices": ask_prices})
    mid_str = f"{mid:.2f}" if mid is not None else "—"
    mid_line = f"{'':>{size_w}}  {mid_str:>{price_w}}"

    return list(reversed(ask_rows)), mid_line, bid_rows, size_w, price_w


def dashboard_chart_url(base_url: str, instrument_id: str) -> str:
    """
    `/chart/{id}` (dashboard.py's chart_handler) -- the one dashboard route with an
    actual time-window/zoom concept (`?start=`/`?end=`, defaulting to the trailing
    4h). `/coin/{id}` was considered and rejected: it serves a live-only, 5s-polled
    indicator panel with no time-window concept at all, which cannot satisfy AC5's
    "same time-window/zoom context" wording regardless of how it's linked to.

    No `start`/`end` query params are ever appended here -- AC7 forbids Coin-detail
    from tracking any time-window state of its own, so there is nothing truthful to
    encode; chart_handler's own default (current, trailing 4h) is the closest honest
    analog to "current" for a view that only ever shows current state.
    """
    return f"{base_url.rstrip('/')}/chart/{instrument_id}"


def osc52_copy_sequence(text: str) -> str:
    """
    OSC 52 terminal escape sequence that sets the system clipboard to `text` -- a
    native terminal feature (iTerm2, kitty, wezterm, most VTE/xterm-derived
    terminals, tmux) rather than a Python clipboard dependency. Works over SSH with
    no clipboard utility needed on either end, since the terminal emulator itself
    (running on the user's machine) intercepts the sequence. Caller writes this
    straight to stdout, bypassing urwid's widget rendering -- the same technique
    tmux/vim/fzf use to reach the real terminal underneath urwid's raw_display screen.
    """
    payload = base64.b64encode(text.encode()).decode()
    return f"\x1b]52;c;{payload}\x07"
