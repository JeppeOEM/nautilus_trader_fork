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
Shared rankings-table column metadata (troll/CLAUDE.md SSOT-03): the web dashboard and
bot_tui's Coins pane both render one row per instrument from the exact same
ranking_engine-published rankings:live rank entry -- this module is the single place
that says which columns exist, in what order, with what label and text formatting, so
neither UI can drift from the other by adding/reordering/reformatting a column alone.

`color_fn` returns a CSS hex color for the dashboard's HTML rendering (`None` = no
sign-coloring for that column). Only two colors are ever used across this whole table
-- POSITIVE_COLOR (green) and NEGATIVE_COLOR (red) -- so bot_tui's urwid renderer maps
a `color_fn` result back onto its own `pnl-pos`/`pnl-neg` palette entries by comparing
against these same two constants, rather than reimplementing each column's own
sign/threshold rule (e.g. OBI's ">0.5" boundary) a second time.
"""

POSITIVE_COLOR = "#2a9d2a"
NEGATIVE_COLOR = "#c0392b"

# Each entry: (store_key, header_label, format_fn, color_fn|None).
# Reorder, add, or remove rows here to control what's shown and how -- in both UIs.
# color_fn receives the raw float value and returns a CSS color string or None.
# Unit contract for direct consumers of /api/rankings and /data/live/{id}: "cvd" and
# "volume_delta" are raw base-asset-token deltas, "spread"/"microprice_lean" are raw
# price-unit deltas -- neither is scaled by price server-side. The rankings/coin-detail
# HTML pages normalize these client-side (see the inline JS's usdFromTokens/
# bpsFromPriceUnits) using each row's own "price" field; a script hitting the JSON
# endpoints directly must do the same multiplication/division itself to get comparable
# USD/bps units.
RANKING_COLS: list[tuple[str, str, object, object]] = [
    ("ofi_10_z",       "OFI10z", lambda v: f"{v:+.2f}",  lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("obi_10",         "OBI10",  lambda v: f"{v:.3f}",   lambda v: POSITIVE_COLOR if v > 0.5 else NEGATIVE_COLOR),
    ("obi_5",          "OBI5",   lambda v: f"{v:.3f}",   lambda v: POSITIVE_COLOR if v > 0.5 else NEGATIVE_COLOR),
    ("obi_3",          "OBI3",   lambda v: f"{v:.3f}",   lambda v: POSITIVE_COLOR if v > 0.5 else NEGATIVE_COLOR),
    ("cvd",            "CVD",    lambda v: f"{v:+.2f}",  lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("spread",         "Spread", lambda v: f"{v:.6f}",   None),
    ("microprice_lean","u lean", lambda v: f"{v:+.6f}",  lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("volume_delta",   "Vol d",  lambda v: f"{v:+.2f}",  lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("price",          "Price",  lambda v: f"{v:.4f}",   None),
    ("pct_1h",         "1h %",   lambda v: f"{v:+.2f}%", lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("pct_24h",        "24h %",  lambda v: f"{v:+.2f}%", lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("volatility",     "Vol",    lambda v: f"{v:.6f}",   None),
    ("volatility_score", "Vol Score", lambda v: f"{v:.6f}" if v is not None else "—", None),
    ("volume24h",      "Vol24h", lambda v: f"{v / 1e6:.1f}M", None),
]

# History-only column (Story 1.4): plotted on /history/{id} from metrics_store rows, but
# deliberately NOT in RANKING_COLS -- that list is also used to render the *live*
# rankings table straight from rankings:live's rank entries, which never carry a "rank"
# key (row order itself is the live rank). Dashboard-only: bot_tui's Coins pane already
# shows rank as its own leading column, not sourced from this list.
_HISTORY_ONLY_COLS: list[tuple[str, str]] = [
    ("rank", "Rank"),
]
