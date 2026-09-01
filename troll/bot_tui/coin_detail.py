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
Pure Coin-detail indicator-orchestration + row/URL-formatting functions (Story 4.3,
AC1/AC4/AC5) -- no urwid import, no I/O.

Wraps ml_signals.indicators' real Microprice/MultiLevelOFI/MultiLevelOBI classes
directly (AD-4's sanctioned pure-utility cross-namespace import) rather than
reimplementing OFI/OBI/microprice locally -- AC1's literal "never reimplemented
locally". There is no Spread class in ml_signals.indicators (confirmed by reading that
module in full) -- spread stays inline arithmetic, matching both indicators.py's own
module docstring and dashboard.py's identical precedent (there is nothing to wrap).
"""

from ml_signals.indicators import Microprice
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI


# Verbatim UX copy (EXPERIENCE.md State Patterns: "Thin order book") -- keep exact,
# same discipline as coins_pane.py's COLD_OPEN_TEXT/NO_MATCHES_TEXT.
NO_BIDS_TEXT = "no bids"
NO_ASKS_TEXT = "no asks"


def update_indicators(
    microprice: Microprice,
    ofi: MultiLevelOFI,
    obi: MultiLevelOBI,
    snapshot: dict,
) -> None:
    """
    Feed one DydxSecondSnapshot.to_dict()-shaped snapshot into all three real
    indicator instances, mirroring dashboard.py's own _ingest_batch call shape
    exactly (dashboard.py:1000-1008 OFI, :1020-1022 OBI):

    - microprice.update_raw(...) is guarded on both sides having at least one level
      (needed to index [0] safely) -- Microprice's own update_raw additionally
      no-ops if total size is zero.
    - ofi.update_raw(...) is called unconditionally, matching dashboard.py's own
      precedent exactly (MultiLevelOFI.update_raw handles empty lists internally,
      contributing zero rather than raising).
    - obi.update_raw(...) is guarded on both sides being non-empty, matching
      dashboard.py's own `if latest["bid_sizes"] and latest["ask_sizes"]:` guard.
    """
    bid_prices = snapshot["bid_prices"]
    bid_sizes = snapshot["bid_sizes"]
    ask_prices = snapshot["ask_prices"]
    ask_sizes = snapshot["ask_sizes"]

    if bid_prices and ask_prices:
        microprice.update_raw(bid_prices[0], bid_sizes[0], ask_prices[0], ask_sizes[0])

    ofi.update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)

    if bid_sizes and ask_sizes:
        obi.update_raw(bid_sizes, ask_sizes)


def spread(snapshot: dict) -> float | None:
    """ask_prices[0] - bid_prices[0]; None if either side is empty (thin/no book)."""
    bid_prices = snapshot["bid_prices"]
    ask_prices = snapshot["ask_prices"]
    if not bid_prices or not ask_prices:
        return None
    return ask_prices[0] - bid_prices[0]


def format_indicator(value: float | None, initialized: bool, decimals: int = 4) -> str:
    """
    Fixed-precision numeric string once `initialized`, else a quiet "warming up…"
    sentinel (mirrors COLD_OPEN_TEXT's tone, per EXPERIENCE.md's Voice and Tone
    discipline) -- MultiLevelOFI.update_raw doesn't set initialized on its first call
    (it only records previous-tick state to diff against), so a coin just opened in
    Coin-detail genuinely has no valid OFI value for one snapshot interval (~1s).
    Microprice/MultiLevelOBI initialize on their first valid snapshot instead.
    """
    if not initialized:
        return "warming up…"
    return f"{value:.{decimals}f}"


def bid_lines(bid_prices: list[float], bid_sizes: list[float], levels: int) -> list[str]:
    """
    Up to `levels` bid rows, most-aggressive-first (index 0 = best bid), each side
    formatted independently of the other (AC4: "the ladder simply ends short... a side
    with zero levels renders `no bids`"). Never pads to match the other side's length.
    """
    if not bid_prices:
        return [NO_BIDS_TEXT]
    n = min(levels, len(bid_prices))
    return [f"{bid_sizes[i]:>10.3f}  {bid_prices[i]:>12.2f}" for i in range(n)]


def ask_lines(ask_prices: list[float], ask_sizes: list[float], levels: int) -> list[str]:
    """Mirror of bid_lines for the ask side -- see its docstring."""
    if not ask_prices:
        return [NO_ASKS_TEXT]
    n = min(levels, len(ask_prices))
    return [f"{ask_sizes[i]:>10.3f}  {ask_prices[i]:>12.2f}" for i in range(n)]


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
