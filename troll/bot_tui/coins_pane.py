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
Pure Coins-pane row/state functions (Story 4.1, AC2/AC5; Story 4.2, AC1 filter) -- no
urwid import, no I/O.

Deliberately excludes OFI/OBI/microprice/spread columns: rankings:live only carries
{instrument_id, rank, volume24h, volatility_score} (architecture AD-9), and this
story's ACs only require rendering that data -- see Story 4.1's Dev Notes "No indicator
columns in this story" for the full reasoning (the mockup's extra columns illustrate
the finished pane, not this story's scope).
"""

# Verbatim UX copy (EXPERIENCE.md State Patterns: Cold open) -- keep exact, per
# UX-DR9's terse/no-marketing-copy discipline.
COLD_OPEN_TEXT = "waiting for rankings:live…"

# Verbatim UX copy (EXPERIENCE.md State Patterns: "Fuzzy-filter no matches") -- keep
# exact, same discipline as COLD_OPEN_TEXT.
NO_MATCHES_TEXT = "no matches"


def coin_rows(ranking: dict | None) -> list[tuple[int, str, float]]:
    """
    Extract (rank, instrument_id, score) tuples in the exact order rankings:live
    provides -- never re-sorted locally (row order is the wire contract, matching
    ml_signals.dashboard's post-Story-1.8 "render in rankings:live's own order" rule).

    score is volume24h when mode == "volume", volatility_score otherwise -- both
    fields are always present per AD-9, so this lookup never needs a fallback.

    Assumes ranking, if not None, is already validated shape (ranking_state's
    _handle_rankings_message guard runs upstream) -- AD-3's "readers trust the gate"
    applied one layer up.
    """
    if ranking is None:
        return []

    score_key = "volume24h" if ranking["mode"] == "volume" else "volatility_score"
    return [(row["rank"], row["instrument_id"], row[score_key]) for row in ranking["ranks"]]


def filter_rows(
    rows: list[tuple[int, str, float]], filter_text: str
) -> list[tuple[int, str, float]]:
    """
    Narrow coin_rows()'s output to rows whose instrument_id contains filter_text
    (Story 4.2, AC1) -- case-insensitive substring match, row order preserved exactly
    (same "row order is the wire contract" discipline coin_rows itself documents).

    Case-insensitivity is a deliberate choice: instrument IDs are uppercase by
    convention (BTC-USD-PERP), matching the case-insensitive filter convention of every
    comparable tool this product is modeled on (k9s, fzf) -- there is no legitimate
    reason a builder would want case to matter here, and case-sensitivity would only
    add a pointless failure mode (typing "btc" and getting zero results).

    A leading/trailing space is stripped before matching -- trivial to introduce while
    typing into the filter's Edit widget, and a builder typing " btc" almost certainly
    still means the substring "btc", not a literal leading space that happens to match
    nothing.

    filter_text == "" (after stripping) returns rows unchanged -- no filter active
    means no narrowing.
    """
    needle = filter_text.strip().lower()
    if needle == "":
        return rows
    return [row for row in rows if needle in row[1].lower()]
