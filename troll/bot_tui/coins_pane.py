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

Full metric parity with the web dashboard's rankings table (troll/CLAUDE.md SSOT-03):
every rank entry ranking_engine publishes already carries every RANKING_COLS field
(ofi_10_z/obi_10/5/3/cvd/spread/microprice/pct_1h/pct_24h/volatility/... -- see
ml_signals/ranking_columns.py, the single column-metadata definition both this pane and
the dashboard's HTML table render from), so this module does no computation of its own
-- format_coin_row/coin_header_text just re-shape that same shared metadata into urwid
markup/header text.
"""

from ml_signals.ranking_columns import NEGATIVE_COLOR
from ml_signals.ranking_columns import POSITIVE_COLOR
from ml_signals.ranking_columns import RANKING_COLS


# Verbatim UX copy (EXPERIENCE.md State Patterns: Cold open) -- keep exact, per
# UX-DR9's terse/no-marketing-copy discipline.
COLD_OPEN_TEXT = "waiting for rankings:live…"

# Verbatim UX copy (EXPERIENCE.md State Patterns: "Fuzzy-filter no matches") -- keep
# exact, same discipline as COLD_OPEN_TEXT.
NO_MATCHES_TEXT = "no matches"

_MISSING_CELL_TEXT = "—"


def coin_rows(ranking: dict | None) -> list[dict]:
    """
    Every rankings:live rank entry, verbatim, in exact wire order -- never re-sorted
    locally (row order is the wire contract, matching ml_signals.dashboard's post-Story
    -1.8 "render in rankings:live's own order" rule). Each entry already carries every
    RANKING_COLS field; this function does no filtering/reshaping of its own.

    Assumes ranking, if not None, is already validated shape (ranking_state's
    _handle_rankings_message guard runs upstream) -- AD-3's "readers trust the gate"
    applied one layer up.
    """
    if ranking is None:
        return []
    return list(ranking["ranks"])


def stale_feed_banner_text(stale_instrument_ids: list[str]) -> str:
    """
    OBS-01/OBS-02: a per-coin companion to the Coins-pane's own pane-level stale badge
    (which only fires if the whole rankings:live heartbeat itself stops). ranking_engine
    silently drops an individual instrument from the ranked list once its own feed goes
    stale (_current_ranks()'s fresh-only filter) -- this surfaces *which* one, so a dead
    coin never just quietly vanishes with no trace (DATA-02).

    Empty string when nothing is stale -- callers skip rendering entirely in that case.
    """
    if not stale_instrument_ids:
        return ""
    shown = stale_instrument_ids[:3]
    more = len(stale_instrument_ids) - len(shown)
    suffix = f" (+{more} more)" if more else ""
    return f"stale feed: {', '.join(shown)}{suffix}"


def filter_rows(rows: list[dict], filter_text: str) -> list[dict]:
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
    return [row for row in rows if needle in row["instrument_id"].lower()]


def coin_header_text() -> str:
    """
    Column-header row shown above the Coins-pane ranking list -- rank/instrument_id
    columns plus every RANKING_COLS label, same order as format_coin_row's cells below
    (and the same order the web dashboard's own `<th>` row renders in).
    """
    header = f"{'#':>4}  {'INSTRUMENT':<20} "
    header += "".join(f"{label:>10} " for _key, label, _fmt, _color in RANKING_COLS)
    return header.rstrip()


def format_coin_row(row: dict) -> list:
    """
    One full-width Coins-pane row as urwid markup: rank, instrument_id, then every
    RANKING_COLS column in shared order. A column whose value is None (ranking_engine
    hasn't initialized that indicator/field yet) renders "—", matching the web
    dashboard's own missing-cell convention.

    Only two colors are ever used across RANKING_COLS (POSITIVE_COLOR/NEGATIVE_COLOR,
    ml_signals/ranking_columns.py) -- mapped here onto this product's own
    "pnl-pos"/"pnl-neg" palette entries rather than reimplementing each column's own
    sign/threshold rule (e.g. OBI's ">0.5" boundary) a second time. Returns a list of
    plain strings and (attr, text) tuples, ready to hand straight to urwid.Text.
    """
    segments: list = [f"{row['rank']:>4}  {row['instrument_id']:<20} "]
    for key, _label, format_fn, color_fn in RANKING_COLS:
        value = row.get(key)
        if value is None:
            segments.append(f"{_MISSING_CELL_TEXT:>10} ")
            continue
        text = f"{format_fn(value):>10} "  # type: ignore[operator]
        color = color_fn(value) if color_fn else None  # type: ignore[operator]
        if color == POSITIVE_COLOR:
            segments.append(("pnl-pos", text))
        elif color == NEGATIVE_COLOR:
            segments.append(("pnl-neg", text))
        else:
            segments.append(text)
    return segments
