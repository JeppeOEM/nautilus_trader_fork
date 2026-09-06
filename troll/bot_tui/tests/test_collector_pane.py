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
"""Tests for bot_tui.collector_pane -- Story 6.1. Pure-logic only, no urwid/I/O."""

from bot_tui import collector_pane


def _status(iid: str, **overrides: object) -> dict:
    base = {
        "id": iid,
        "pinned": False,
        "liquid": True,
        "last_trade_ts": 1_000_000_000,
    }
    base.update(overrides)
    return base


def test_collector_rows_sorted_pinned_first_then_by_id() -> None:
    statuses = {
        "ETH-USD-PERP.DYDX": _status("ETH-USD-PERP.DYDX"),
        "BTC-USD-PERP.DYDX": _status("BTC-USD-PERP.DYDX", pinned=True),
        "AAA-USD-PERP.DYDX": _status("AAA-USD-PERP.DYDX"),
    }
    rows = collector_pane.collector_rows(statuses)
    assert [row["id"] for row in rows] == [
        "BTC-USD-PERP.DYDX",
        "AAA-USD-PERP.DYDX",
        "ETH-USD-PERP.DYDX",
    ]


def test_collector_rows_empty_dict_returns_empty_list() -> None:
    assert collector_pane.collector_rows({}) == []


def test_format_collector_line_pinned_and_liquid() -> None:
    row = _status("BTC-USD-PERP.DYDX", pinned=True, liquid=True)
    line = collector_pane.format_collector_line(row, stale=False)
    assert "BTC-USD-PERP.DYDX" in line
    assert "pinned" in line
    assert "illiquid" not in line
    assert "liquid" in line


def test_format_collector_line_not_pinned_and_illiquid() -> None:
    row = _status("BTC-USD-PERP.DYDX", pinned=False, liquid=False)
    line = collector_pane.format_collector_line(row, stale=False)
    assert "illiquid" in line


def test_format_collector_line_stale_has_marker() -> None:
    row = _status("BTC-USD-PERP.DYDX")
    stale_line = collector_pane.format_collector_line(row, stale=True)
    fresh_line = collector_pane.format_collector_line(row, stale=False)
    assert stale_line.startswith("~ ")
    assert fresh_line.startswith("  ")


def test_format_unpinned_line_empty_is_blank() -> None:
    assert collector_pane.format_unpinned_line([]) == ""


def test_format_unpinned_line_lists_sorted_ids() -> None:
    line = collector_pane.format_unpinned_line(["ETH-USD-PERP.DYDX", "AAA-USD-PERP.DYDX"])
    assert line == "unpinned (add back with :start <ID>): AAA-USD-PERP.DYDX, ETH-USD-PERP.DYDX"
