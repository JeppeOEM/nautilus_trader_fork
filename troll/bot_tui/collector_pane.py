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
Pure collection-overview-pane row/formatting functions (Story 6.1) -- no urwid import,
no I/O, mirroring bots_pane.py's separation of concerns.

collector:status is published one message per instrument (like bots:status is one
message per bot) -- collector_state.py accumulates the latest message per instrument id
into a dict; this module turns that dict into a deterministic, orderable row list and
formats individual fields for display.
"""

COLD_OPEN_TEXT = "waiting for collector:status…"


def collector_rows(statuses: dict[str, dict]) -> list[dict]:
    """
    Latest known status dict per instrument, sorted by id.

    Every currently-collected instrument is pinned by definition (there is no more
    "collected but not pinned" state -- see collector.py's module docstring), so
    row["pinned"] is always True here; a stale row from a config.toml saved before
    that change could still say False, sorting harmlessly to the same place.
    """
    return sorted(statuses.values(), key=lambda row: (not row.get("pinned", False), row["id"]))


def format_collector_line(row: dict, stale: bool) -> str:
    """
    One full plain-text row: id, pinned marker, liquid/illiquid label.
    Used directly by tests and as the source of truth app.py's urwid.Text must match.
    """
    stale_marker = "~ " if stale else "  "
    pinned_text = "pinned" if row.get("pinned", False) else "      "
    liquid_text = "liquid" if row.get("liquid", False) else "illiquid"
    return f"{stale_marker}{row['id']:<22} {pinned_text}  {liquid_text:<8}"


def format_unpinned_line(unpinned_ids: list[str]) -> str:
    """
    One trailing informational line listing every id in collector.py's config.exclude --
    whether it landed there via the TUI's "unpin" control action or a hand-edit of
    config.toml. These aren't currently collected, so they have no row of their own
    above this line. Empty string (render nothing) if none.
    """
    if not unpinned_ids:
        return ""
    return "unpinned (add back with :start <ID>): " + ", ".join(sorted(unpinned_ids))
