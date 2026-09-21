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
"""Tests for live_paper.fills_store -- Story 4.6 (financial calculations, TEST-01)."""

from live_paper import fills_store


_NS_PER_DAY = 24 * 3600 * 1_000_000_000
_DAY0 = 10 * _NS_PER_DAY  # arbitrary UTC-day-aligned base timestamp


def _db_path(tmp_path) -> str:
    return str(tmp_path / "fills.db")


def test_write_fill_then_recent_trades_round_trips_all_fields(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 1.5, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 10, "SELL", 105.0, 1.5, 7.5, db_path)

    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=None, limit=500)

    assert trades == [
        {"ts": _DAY0, "side": "BUY", "price": 100.0, "qty": 1.5, "realized_pnl": None},
        {"ts": _DAY0 + 10, "side": "SELL", "price": 105.0, "qty": 1.5, "realized_pnl": 7.5},
    ]


def test_recent_trades_filters_by_bot_id(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 1.0, None, db_path)
    fills_store.write_fill("bot-02", _DAY0, "BUY", 200.0, 1.0, None, db_path)

    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=None, limit=500)

    assert len(trades) == 1
    assert trades[0]["price"] == 100.0


def test_recent_trades_respects_cutoff(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 1.0, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + _NS_PER_DAY, "SELL", 110.0, 1.0, 10.0, db_path)

    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=_DAY0 + 1, limit=500)

    assert len(trades) == 1
    assert trades[0]["ts"] == _DAY0 + _NS_PER_DAY


def test_recent_trades_caps_at_limit_keeping_most_recent_ascending(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    for i in range(600):
        fills_store.write_fill("bot-01", _DAY0 + i, "BUY", 100.0, 1.0, None, db_path)

    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=None, limit=500)

    assert len(trades) == 500
    assert [t["ts"] for t in trades] == sorted(t["ts"] for t in trades)
    assert trades[0]["ts"] == _DAY0 + 100
    assert trades[-1]["ts"] == _DAY0 + 599


def test_pnl_by_day_sums_only_non_null_realized_pnl_per_utc_day(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 1.0, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 5.0, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 20, "BUY", 105.0, 1.0, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 30, "SELL", 103.0, 1.0, -2.0, db_path)
    fills_store.write_fill("bot-01", _DAY0 + _NS_PER_DAY, "SELL", 108.0, 1.0, 8.0, db_path)

    pnl_series = fills_store.pnl_by_day("bot-01", db_path, cutoff_ns=None)

    assert pnl_series == [
        {"period_start": _DAY0, "pnl": 3.0},
        {"period_start": _DAY0 + _NS_PER_DAY, "pnl": 8.0},
    ]


def test_pnl_by_day_matches_sum_of_recent_trades_realized_pnl(tmp_path) -> None:
    # AD-10 correctness invariant: summing a range's pnl_series must equal the sum of
    # that same range's per-fill realized PnL.
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 1.0, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 5.0, db_path)
    fills_store.write_fill("bot-01", _DAY0 + _NS_PER_DAY, "SELL", 108.0, 1.0, 8.0, db_path)

    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=None, limit=500)
    pnl_series = fills_store.pnl_by_day("bot-01", db_path, cutoff_ns=None)

    trades_total = sum(t["realized_pnl"] for t in trades if t["realized_pnl"] is not None)
    series_total = sum(entry["pnl"] for entry in pnl_series)
    assert trades_total == series_total == 13.0


def test_realized_pnls_returns_only_closing_fills_in_chronological_order(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 1.0, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 5.0, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 20, "BUY", 105.0, 1.0, None, db_path)
    fills_store.write_fill("bot-01", _DAY0 + 30, "SELL", 103.0, 1.0, -2.0, db_path)

    assert fills_store.realized_pnls("bot-01", db_path, cutoff_ns=None) == [5.0, -2.0]


def test_realized_pnls_respects_cutoff_and_bot_id(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "SELL", 100.0, 1.0, 3.0, db_path)
    fills_store.write_fill("bot-01", _DAY0 + _NS_PER_DAY, "SELL", 105.0, 1.0, 7.0, db_path)
    fills_store.write_fill("bot-02", _DAY0 + _NS_PER_DAY, "SELL", 105.0, 1.0, 99.0, db_path)

    assert fills_store.realized_pnls("bot-01", db_path, cutoff_ns=_DAY0 + 1) == [7.0]


def test_position_realized_pnls_ignores_per_fill_realized_pnl(tmp_path) -> None:
    # A round trip closed across two reducing fills: both get a per-fill realized_pnl
    # (proportional split), but only the second (the one that actually closed the
    # position) gets position_realized_pnl -- position_realized_pnls() must return
    # exactly one value for this round trip, not two.
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 2.0, None, db_path)
    fills_store.write_fill(
        "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 2.5, db_path, position_realized_pnl=None
    )
    fills_store.write_fill(
        "bot-01", _DAY0 + 20, "SELL", 106.0, 1.0, 3.5, db_path, position_realized_pnl=6.0
    )

    assert fills_store.realized_pnls("bot-01", db_path, cutoff_ns=None) == [2.5, 3.5]
    assert fills_store.position_realized_pnls("bot-01", db_path, cutoff_ns=None) == [6.0]


def test_win_rate_stats_counts_round_trips_not_fills(tmp_path) -> None:
    db_path = _db_path(tmp_path)
    fills_store.write_fill("bot-01", _DAY0, "BUY", 100.0, 2.0, None, db_path)
    fills_store.write_fill(
        "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 2.5, db_path, position_realized_pnl=None
    )
    fills_store.write_fill(
        "bot-01", _DAY0 + 20, "SELL", 106.0, 1.0, 3.5, db_path, position_realized_pnl=6.0
    )
    fills_store.write_fill("bot-01", _DAY0 + 30, "BUY", 106.0, 1.0, None, db_path)
    fills_store.write_fill(
        "bot-01", _DAY0 + 40, "SELL", 100.0, 1.0, -6.0, db_path, position_realized_pnl=-6.0
    )

    closed_trades, wins = fills_store.win_rate_stats("bot-01", db_path)
    assert closed_trades == 2
    assert wins == 1
