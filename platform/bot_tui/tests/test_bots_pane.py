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

from pathlib import Path

import pytest

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


def test_fit_pads_a_short_string_to_exact_width() -> None:
    assert bots_pane.fit("bot-01", 12) == "bot-01      "
    assert len(bots_pane.fit("bot-01", 12)) == 12


def test_fit_truncates_with_ellipsis_when_over_width() -> None:
    # Regression: plain f"{text:<N}" only pads, never truncates -- a longer-than-
    # expected value (an operator-chosen bot_id, or a longer ticker like
    # RENDER-USD-PERP.DYDX at 20 chars) silently overflowed its column and pushed
    # every field after it out of alignment for that row only (platform/CLAUDE.md TUI-02).
    fitted = bots_pane.fit("a-much-too-long-bot-id", 12)
    assert len(fitted) == 12
    assert fitted.endswith("…")


def test_fit_exact_width_no_truncation_no_padding() -> None:
    assert bots_pane.fit("AVAX-USD-PERP.DYDX", 18) == "AVAX-USD-PERP.DYDX"


def test_format_bot_line_every_row_has_identical_length_regardless_of_symbol_length() -> None:
    # The actual regression this fix targets: before it, a row for a longer-ticker
    # instrument (e.g. RENDER-USD-PERP.DYDX, 20 chars, overflowing the old 18-char
    # field by 2) was two characters longer than a BTC row, so every column after
    # the symbol landed in a different terminal column depending on the row.
    short_line = bots_pane.format_bot_line(
        _status("bot-01", symbol="BTC-USD-PERP.DYDX"), stale=False, now=1_005.0
    )
    long_line = bots_pane.format_bot_line(
        _status("bot-02", symbol="RENDER-USD-PERP.DYDX"), stale=False, now=1_005.0
    )
    assert len(short_line) == len(long_line)


def test_format_win_rate_detail_none_is_not_available() -> None:
    assert bots_pane.format_win_rate_detail(None, closed_trades=0) == "n/a"


def test_format_win_rate_detail_formats_percent_and_trade_count() -> None:
    assert bots_pane.format_win_rate_detail(0.41, closed_trades=63) == "41% (63 trades)"


def test_format_win_rate_detail_zero_is_a_real_value_not_n_a() -> None:
    # A real 0% win rate (at least one closed trade, all losses) must be
    # distinguishable from "no trades yet" (None) -- never collapse the two.
    assert bots_pane.format_win_rate_detail(0.0, closed_trades=0) == "0% (0 trades)"


def test_bot_detail_lines_returns_five_lines() -> None:
    # Story 29.6 added the position line and the exits line to the original three.
    row = _status("bot-03", strategy="microprice_rev", symbol="SOL-USD-PERP.DYDX")
    lines = bots_pane.bot_detail_lines(row, now=1_005.0)
    assert len(lines) == 5


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


def test_bot_detail_segments_are_exactly_the_lines_text() -> None:
    row = _status("bot-03", realized_pnl=10.0, unrealized_pnl=-2.0, position_side="short")
    segments = bots_pane.bot_detail_segments(row, now=1_005.0)
    joined = ["".join(text for _attr, text in line) for line in segments]
    assert joined == bots_pane.bot_detail_lines(row, now=1_005.0)


def _pnl_segments(row: dict) -> list[tuple[str | None, str]]:
    line = bots_pane.bot_detail_segments(row, now=1_005.0)[1]
    return [(attr, text) for attr, text in line if attr is not None]


def test_bot_detail_segments_tag_a_gain_positive() -> None:
    row = _status("bot-03", realized_pnl=0.0, unrealized_pnl=0.0)
    assert _pnl_segments(row) == [("pnl-pos", bots_pane.format_pnl(0.0))]


def test_bot_detail_segments_tag_a_loss_negative() -> None:
    row = _status("bot-03", realized_pnl=1.0, unrealized_pnl=-3.5)
    assert _pnl_segments(row) == [("pnl-neg", bots_pane.format_pnl(-2.5))]


def test_next_range_cycles_day_week_month_all_day() -> None:
    assert bots_pane.next_range("day") == "week"
    assert bots_pane.next_range("week") == "month"
    assert bots_pane.next_range("month") == "all"
    assert bots_pane.next_range("all") == "day"


def test_previous_range_cycles_the_reverse_of_next_range() -> None:
    assert bots_pane.previous_range("day") == "all"
    assert bots_pane.previous_range("all") == "month"
    assert bots_pane.previous_range("month") == "week"
    assert bots_pane.previous_range("week") == "day"


def _history_entry(**overrides: object) -> dict:
    base = {
        "bot_id": "bot-01",
        "range": "day",
        "updated_at": 1_000_000_000,
        "trades": [
            {"ts": 1_000_000_000, "side": "BUY", "price": 100.0, "qty": 1.0, "realized_pnl": None},
            {"ts": 2_000_000_000, "side": "SELL", "price": 105.0, "qty": 1.0, "realized_pnl": 5.0},
        ],
        "pnl_series": [{"period_start": 0, "pnl": 5.0}],
    }
    base.update(overrides)
    return base


def test_trades_blotter_lines_none_entry_is_unavailable() -> None:
    assert bots_pane.trades_blotter_lines(None) == [bots_pane.HISTORY_UNAVAILABLE_TEXT]


def test_trades_blotter_lines_empty_trades_is_no_trades_yet() -> None:
    entry = _history_entry(trades=[])
    assert bots_pane.trades_blotter_lines(entry) == [bots_pane.NO_TRADES_YET_TEXT]


def test_trades_blotter_lines_one_line_per_fill_in_wire_order() -> None:
    lines = bots_pane.trades_blotter_lines(_history_entry())
    assert len(lines) == 2
    assert "BUY" in lines[0]
    assert "SELL" in lines[1]


def test_trades_blotter_lines_non_closing_fill_has_no_pnl_number() -> None:
    lines = bots_pane.trades_blotter_lines(_history_entry())
    assert bots_pane.format_pnl(5.0) not in lines[0]


def test_trades_blotter_lines_closing_fill_shows_its_pnl() -> None:
    lines = bots_pane.trades_blotter_lines(_history_entry())
    assert bots_pane.format_pnl(5.0) in lines[1]


def test_pnl_sparkline_text_none_entry_is_unavailable() -> None:
    assert bots_pane.pnl_sparkline_text(None) == bots_pane.HISTORY_UNAVAILABLE_TEXT


def test_pnl_sparkline_text_empty_series_is_no_trades_yet() -> None:
    entry = _history_entry(pnl_series=[])
    assert bots_pane.pnl_sparkline_text(entry) == bots_pane.NO_TRADES_YET_TEXT


def test_pnl_sparkline_text_one_char_per_bucket() -> None:
    entry = _history_entry(
        pnl_series=[
            {"period_start": 0, "pnl": -5.0},
            {"period_start": 1, "pnl": 0.0},
            {"period_start": 2, "pnl": 10.0},
        ]
    )
    text = bots_pane.pnl_sparkline_text(entry)
    assert len(text) == 3
    # Monotonically increasing pnl must map to non-decreasing bar height.
    assert text[0] <= text[1] <= text[2]


def test_pnl_sparkline_text_flat_series_does_not_divide_by_zero() -> None:
    entry = _history_entry(pnl_series=[{"period_start": 0, "pnl": 3.0}] * 4)
    text = bots_pane.pnl_sparkline_text(entry)
    assert len(text) == 4
    assert len(set(text)) == 1


def test_dashboard_bot_url_shape() -> None:
    assert bots_pane.dashboard_bot_url("http://127.0.0.1:8765", "bot-01") == (
        "http://127.0.0.1:8765/bot/bot-01"
    )


def test_dashboard_bot_url_strips_trailing_slash_on_base() -> None:
    assert bots_pane.dashboard_bot_url("http://127.0.0.1:8765/", "bot-01") == (
        "http://127.0.0.1:8765/bot/bot-01"
    )


def test_format_stat_none_is_na() -> None:
    assert bots_pane.format_stat(None) == "n/a"


def test_format_stat_formats_with_given_fmt() -> None:
    assert bots_pane.format_stat(1.5, "{:.2f}") == "1.50"
    assert bots_pane.format_stat(-0.042, "{:.2%}") == "-4.20%"


def test_metrics_lines_none_entry_is_unavailable() -> None:
    assert bots_pane.metrics_lines(None) == [bots_pane.HISTORY_UNAVAILABLE_TEXT]


def test_metrics_lines_missing_metrics_key_renders_every_stat_as_na() -> None:
    entry = _history_entry()  # base fixture has no "metrics" key
    lines = bots_pane.metrics_lines(entry)
    assert "n/a" in lines[0]
    assert "n/a" in lines[1]


def test_metrics_lines_renders_every_stat_value() -> None:
    entry = _history_entry(
        metrics={
            "sharpe_ratio": 1.23,
            "sortino_ratio": 2.34,
            "calmar_ratio": 0.56,
            "max_drawdown": -0.042,
            "profit_factor": 1.87,
            "expectancy": 6.4,
            "avg_win": 15.0,
            "avg_loss": -6.5,
            "max_win": 20.0,
            "max_loss": -8.0,
            "win_rate": 0.6,
        }
    )
    lines = bots_pane.metrics_lines(entry)
    assert "1.23" in lines[0]
    assert "2.34" in lines[0]
    assert "0.56" in lines[0]
    assert "-4.20%" in lines[0]
    assert "1.87" in lines[1]
    assert "6.40" in lines[1]
    assert "15.00" in lines[1]
    assert "-6.50" in lines[1]


def test_incidents_lines_none_is_unavailable() -> None:
    assert bots_pane.incidents_lines(None, now=1_000.0) == [bots_pane.INCIDENTS_UNAVAILABLE_TEXT]


def test_incidents_lines_empty_is_no_incidents_recorded() -> None:
    assert bots_pane.incidents_lines([], now=1_000.0) == [bots_pane.NO_INCIDENTS_TEXT]


def test_incidents_lines_most_recent_first() -> None:
    incidents = [
        {"type": "process_start", "started_at": 100.0, "ended_at": 100.0},
        {"type": "data_stale", "started_at": 200.0, "ended_at": 235.0},
    ]
    lines = bots_pane.incidents_lines(incidents, now=1_000.0)
    assert "stale feed" in lines[0]
    assert "restarted" in lines[1]


def test_format_incident_line_process_start_has_no_duration() -> None:
    incident = {"type": "process_start", "started_at": 100.0, "ended_at": 100.0}
    line = bots_pane.format_incident_line(incident, now=1_000.0)
    assert "restarted" in line
    assert "ongoing" not in line


def test_format_incident_line_open_incident_shows_ongoing_duration() -> None:
    incident = {"type": "data_stale", "started_at": 100.0, "ended_at": None}
    line = bots_pane.format_incident_line(incident, now=160.0)
    assert "ongoing (1m00s)" in line


def test_format_incident_line_closed_incident_shows_fixed_duration() -> None:
    incident = {"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}
    line = bots_pane.format_incident_line(incident, now=999.0)
    assert "0m35s" in line
    assert "ongoing" not in line


def test_osc52_copy_sequence_wraps_base64_payload_in_escape_codes() -> None:
    seq = bots_pane.osc52_copy_sequence("hello")
    assert seq == "\x1b]52;c;aGVsbG8=\x07"


# --- Story 29.6: entry / sl / tp columns, the header, the position detail lines -----------------


def _protected(**overrides: object) -> dict:
    """Return a long with both exits resting, as a post-Story-29.6 producer publishes it."""
    fields = {
        "stop_loss": "58900.0",
        "take_profit": "61020.5",
        "entry_price": "60000.0",
        "mark_price": "59980.5",
        "position_qty": "0.001",
        "stop_loss_orders": 1,
        "take_profit_orders": 1,
        "open_orders": 2,
        "last_fill_at": 900_000_000_000,
    }
    fields.update(overrides)
    bot_id = str(fields.pop("bot_id", "bot-01"))
    return _status(bot_id, **fields)


def _cells(row: dict) -> dict[str, str]:
    """Each column's text in a formatted row, cut at the header's column starts."""
    line = bots_pane.format_bot_line(row, stale=False, now=5_000.0)
    cells: dict[str, str] = {}
    start = 0
    for label, width in bots_pane.BOTS_COLUMNS:
        cells[label] = line[start : start + width].strip()
        start += width
    return cells


def test_a_protected_long_shows_entry_and_both_exits_with_their_distance_from_mark() -> None:
    cells = _cells(_protected())
    assert cells["entry"] == "60,000.0"
    # (58900.0 - 59980.5) / 59980.5 = -1.80%; (61020.5 - 59980.5) / 59980.5 = +1.73%
    assert cells["sl"] == "58,900.0    -1.8%"
    assert cells["tp"] == "61,020.5    +1.7%"


def test_an_unprotected_position_shows_none_in_the_warning_attr() -> None:
    row = _protected(stop_loss=None, stop_loss_orders=0)
    assert _cells(row)["sl"] == "none"
    segments = bots_pane.bot_line_segments(row, stale=False, now=5_000.0)
    assert (bots_pane.WARNING_ATTR, "none") in segments


def test_a_missing_take_profit_is_none_without_the_warning() -> None:
    row = _protected(take_profit=None, take_profit_orders=0)
    assert _cells(row)["tp"] == "none"
    segments = bots_pane.bot_line_segments(row, stale=False, now=5_000.0)
    assert [attr for attr, _text in segments if attr == bots_pane.WARNING_ATTR] == []


def test_a_counted_but_unpriced_stop_is_armed() -> None:
    assert _cells(_protected(stop_loss=None))["sl"] == "armed"


def test_a_flat_bot_shows_blank_exit_cells() -> None:
    cells = _cells(
        _protected(position_side="flat", stop_loss=None, take_profit=None, entry_price=None)
    )
    assert (cells["entry"], cells["sl"], cells["tp"]) == ("", "", "")


def test_a_pre_story_message_shows_n_a_never_a_fabricated_value() -> None:
    cells = _cells(_status("bot-01"))
    assert (cells["entry"], cells["sl"], cells["tp"]) == ("n/a", "n/a", "n/a")


def test_no_mark_leaves_the_distance_blank() -> None:
    cells = _cells(_protected(mark_price=None))
    assert cells["sl"] == "58,900.0"
    assert cells["tp"] == "61,020.5"


def test_format_price_keeps_the_instruments_precision_with_separators() -> None:
    assert bots_pane.format_price("58900.0") == "58,900.0"
    assert bots_pane.format_price("0.00001234") == "0.00001234"
    assert bots_pane.format_price("105234.50") == "105,234.50"


def test_format_price_never_switches_to_scientific_notation() -> None:
    assert bots_pane.format_price("0.0000001234") == "0.0000001234"
    assert bots_pane.format_price("0.00000001") == "0.00000001"


@pytest.mark.parametrize("text", ["garbage", "NaN", "Infinity", ""])
def test_a_malformed_price_renders_n_a_instead_of_raising(text: str) -> None:
    assert bots_pane.format_price(text) == "n/a"
    assert bots_pane.format_distance(text, "100") == ""
    assert bots_pane.format_distance("100", text) == ""


def test_format_distance_is_signed_to_one_decimal() -> None:
    assert bots_pane.format_distance("101", "100") == "+1.0%"
    assert bots_pane.format_distance("98.2", "100") == "-1.8%"
    assert bots_pane.format_distance("100", None) == ""


def test_format_distance_of_100_percent_or_more_fits_its_cell() -> None:
    assert bots_pane.format_distance("199.9", "100") == "+99.9%"
    assert bots_pane.format_distance("199.96", "100") == "+100%"
    assert bots_pane.format_distance("350", "100") == "+250%"
    assert bots_pane.format_distance("0.01", "100") == "-100%"
    assert all(
        len(bots_pane.format_distance(price, "100")) <= bots_pane.DISTANCE_WIDTH
        for price in ("199.94", "199.96", "999", "0.01")
    )


def test_header_labels_start_where_their_columns_start() -> None:
    # A long bot_id and a long price must not push any column out of line with the header.
    header = bots_pane.bots_header_line()
    row = _protected(
        bot_id="a-much-too-long-bot-identifier",
        entry_price="123456789.123456",
        stop_loss="123456000.000001",
        take_profit="123457000.999999",
        mark_price="123456789.5",
    )
    line = bots_pane.format_bot_line(row, stale=False, now=5_000.0)
    assert len(line) == len(header) == bots_pane.BOTS_PANE_MIN_WIDTH
    start = 0
    for label, width in bots_pane.BOTS_COLUMNS:
        if label:
            assert header[start : start + len(label)] == label
            assert header[start - 1] == " "
        start += width
    starts = {label: header.index(f" {label} ") + 1 for label in ("entry", "sl", "tp")}
    assert line[starts["entry"] :].startswith("123,456,78…")
    assert line[starts["sl"] :].startswith("123,456,00…")
    assert line[starts["tp"] :].startswith("123,457,00…")


def test_every_row_shape_is_exactly_the_minimum_width() -> None:
    rows = [
        _protected(),
        _protected(stop_loss=None, stop_loss_orders=0),
        _protected(position_side="flat"),
        _status("bot-01"),
        _protected(win_rate=1.0, started_at=0.0),
    ]
    for row in rows:
        for stale in (False, True):
            line = bots_pane.format_bot_line(row, stale=stale, now=999 * 3_600.0 + 3_599.0)
            assert len(line) == bots_pane.BOTS_PANE_MIN_WIDTH


def test_detail_lines_show_the_position_and_its_exits() -> None:
    lines = bots_pane.bot_detail_lines(_protected(), now=1_000.0)
    assert lines[3] == "quantity   0.001   entry 60,000.0   mark 59,980.5   open orders 2"
    assert lines[4] == (
        "stop loss  58,900.0 (1 order)   take profit 61,020.5 (1 order)   last fill 1m40s ago"
    )


def test_detail_lines_count_scaled_exits_and_a_missing_stop() -> None:
    row = _protected(stop_loss=None, stop_loss_orders=0, take_profit_orders=2, last_fill_at=None)
    assert bots_pane.bot_detail_lines(row, now=1_000.0)[4] == (
        "stop loss  none   take profit 61,020.5 (2 orders)   last fill no fills yet"
    )


def test_detail_lines_of_a_flat_bot() -> None:
    row = _protected(
        position_side="flat",
        entry_price=None,
        mark_price=None,
        position_qty=None,
        stop_loss=None,
        take_profit=None,
        stop_loss_orders=0,
        take_profit_orders=0,
        open_orders=0,
    )
    lines = bots_pane.bot_detail_lines(row, now=1_000.0)
    assert lines[3] == "quantity   -   entry -   mark -   open orders 0"
    assert lines[4] == "stop loss  -   take profit -   last fill 1m40s ago"


def test_detail_lines_of_a_pre_story_message_are_n_a() -> None:
    lines = bots_pane.bot_detail_lines(_status("bot-01"), now=1_000.0)
    assert lines[3] == "quantity   n/a   entry n/a   mark n/a   open orders n/a"
    assert lines[4] == "stop loss  n/a   take profit n/a   last fill n/a"


def test_bot_operations_doc_states_the_bots_pane_minimum_width() -> None:
    doc = (Path(__file__).resolve().parents[2] / "docs" / "BOT_OPERATIONS.md").read_text()
    assert f"at least {bots_pane.BOTS_PANE_MIN_WIDTH} columns" in doc
