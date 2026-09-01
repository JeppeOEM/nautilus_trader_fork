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
Tests for bot_tui.bots_pane -- Story 4.4, AC1/AC2, and Story 4.5, AC1 (Bot-detail's
snapshot-header formatting). Pure-logic only, no urwid/I/O.
"""

from bot_tui import bots_pane


def _status(bot_id: str, **overrides: object) -> dict:
    base = {
        "bot_id": bot_id,
        "strategy": "DummyStrategy",
        "symbol": "BTC-USD-PERP.DYDX",
        "mode": "paper",
        "running": True,
        "position_side": "long",
        "net_exposure": 123.45,
        "realized_pnl": 10.0,
        "unrealized_pnl": -2.0,
        "win_rate": 0.6,
        "closed_trades": 5,
        "started_at": 1_000.0,
        "updated_at": 1_005.0,
    }
    base.update(overrides)
    return base


def test_bot_rows_sorted_by_bot_id() -> None:
    statuses = {"bot-02": _status("bot-02"), "bot-01": _status("bot-01")}
    rows = bots_pane.bot_rows(statuses)
    assert [row["bot_id"] for row in rows] == ["bot-01", "bot-02"]


def test_bot_rows_empty_dict_returns_empty_list() -> None:
    assert bots_pane.bot_rows({}) == []


def test_format_pnl_positive_has_leading_plus() -> None:
    assert bots_pane.format_pnl(8.0) == "+     8.00"


def test_format_pnl_negative_has_leading_minus() -> None:
    assert bots_pane.format_pnl(-2.0) == "-     2.00"


def test_format_pnl_zero_treated_as_non_negative() -> None:
    assert bots_pane.format_pnl(0.0).startswith("+")


def test_format_uptime_under_an_hour() -> None:
    assert bots_pane.format_uptime(started_at=1_000.0, now=1_000.0 + 125.0) == "2m05s"


def test_format_uptime_an_hour_or_more() -> None:
    assert bots_pane.format_uptime(started_at=0.0, now=3_600.0 + 60.0) == "1h01m"


def test_format_uptime_never_negative_for_clock_skew() -> None:
    # now < started_at should not raise or produce a negative duration string.
    assert bots_pane.format_uptime(started_at=1_000.0, now=500.0) == "0m00s"


def test_format_win_rate_none_is_not_available() -> None:
    assert bots_pane.format_win_rate(None) == "n/a"


def test_format_win_rate_formats_as_percent() -> None:
    assert bots_pane.format_win_rate(0.6) == "60%"


def test_format_bot_line_contains_all_fields() -> None:
    row = _status("bot-07")
    line = bots_pane.format_bot_line(row, stale=False, now=1_005.0)
    assert "bot-07" in line
    assert "BTC-USD-PERP.DYDX" in line
    assert "paper" in line
    assert "run" in line
    assert "long" in line
    assert "60%" in line


def test_format_bot_line_stale_row_has_marker() -> None:
    row = _status("bot-07")
    stale_line = bots_pane.format_bot_line(row, stale=True, now=1_005.0)
    fresh_line = bots_pane.format_bot_line(row, stale=False, now=1_005.0)
    assert stale_line.startswith("~ ")
    assert not fresh_line.startswith("~ ")


def test_format_bot_line_stopped_bot_shows_off() -> None:
    row = _status("bot-07", running=False)
    line = bots_pane.format_bot_line(row, stale=False, now=1_005.0)
    assert "off" in line


def test_format_win_rate_detail_none_is_not_available() -> None:
    assert bots_pane.format_win_rate_detail(None, closed_trades=0) == "n/a"


def test_format_win_rate_detail_formats_percent_and_trade_count() -> None:
    assert bots_pane.format_win_rate_detail(0.41, closed_trades=63) == "41% (63 trades)"


def test_format_win_rate_detail_zero_is_a_real_value_not_n_a() -> None:
    # A real 0% win rate (at least one closed trade, all losses) must be
    # distinguishable from "no trades yet" (None) -- never collapse the two.
    assert bots_pane.format_win_rate_detail(0.0, closed_trades=0) == "0% (0 trades)"


def test_bot_detail_lines_returns_three_lines() -> None:
    row = _status("bot-03", strategy="microprice_rev", symbol="SOL-USD-PERP.DYDX")
    lines = bots_pane.bot_detail_lines(row, now=1_005.0)
    assert len(lines) == 3


def test_bot_detail_lines_combines_strategy_and_symbol() -> None:
    row = _status("bot-03", strategy="microprice_rev", symbol="SOL-USD-PERP.DYDX")
    lines = bots_pane.bot_detail_lines(row, now=1_005.0)
    assert "microprice_rev / SOL-USD-PERP.DYDX" in lines[0]


def test_bot_detail_lines_first_line_has_mode() -> None:
    row = _status("bot-03", mode="paper")
    lines = bots_pane.bot_detail_lines(row, now=1_005.0)
    assert "paper" in lines[0]


def test_bot_detail_lines_second_line_has_pnl_and_position() -> None:
    row = _status("bot-03", realized_pnl=10.0, unrealized_pnl=-2.0, position_side="short")
    lines = bots_pane.bot_detail_lines(row, now=1_005.0)
    assert bots_pane.format_pnl(8.0) in lines[1]
    assert "short" in lines[1]


def test_bot_detail_lines_third_line_has_uptime_and_win_rate() -> None:
    row = _status("bot-03", started_at=1_000.0, win_rate=0.41, closed_trades=63)
    lines = bots_pane.bot_detail_lines(row, now=1_005.0)
    assert "5s" in lines[2] or "0m05s" in lines[2]
    assert "41% (63 trades)" in lines[2]
