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
"""Tests for bots.infrastructure.fills_store -- Story 4.6 (financial calculations, TEST-01)."""

import sqlite3
from pathlib import Path

import pytest

from bots.infrastructure.fills_store import SqliteFillsStore
from bots.tests.support import write_fill


_NS_PER_DAY = 24 * 3600 * 1_000_000_000
_DAY0 = 10 * _NS_PER_DAY  # arbitrary UTC-day-aligned base timestamp


def test_write_fill_then_recent_trades_round_trips_all_fields(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.5, None)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.5, 7.5)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert trades == [
        {"ts": _DAY0, "side": "BUY", "price": 100.0, "qty": 1.5, "realized_pnl": None},
        {"ts": _DAY0 + 10, "side": "SELL", "price": 105.0, "qty": 1.5, "realized_pnl": 7.5},
    ]


def test_recent_trades_filters_by_bot_id(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0, None)
    write_fill(store, "bot-02", _DAY0, "BUY", 200.0, 1.0, None)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert len(trades) == 1
    assert trades[0]["price"] == 100.0


def test_recent_trades_respects_cutoff(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 110.0, 1.0, 10.0)

    trades = store.recent_trades("bot-01", cutoff_ns=_DAY0 + 1, limit=500)

    assert len(trades) == 1
    assert trades[0]["ts"] == _DAY0 + _NS_PER_DAY


def test_recent_trades_caps_at_limit_keeping_most_recent_ascending(store: SqliteFillsStore) -> None:
    for i in range(600):
        write_fill(store, "bot-01", _DAY0 + i, "BUY", 100.0, 1.0, None)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert len(trades) == 500
    assert [t["ts"] for t in trades] == sorted(t["ts"] for t in trades)
    assert trades[0]["ts"] == _DAY0 + 100
    assert trades[-1]["ts"] == _DAY0 + 599


def test_pnl_by_day_sums_only_non_null_realized_pnl_per_utc_day(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 5.0)
    write_fill(store, "bot-01", _DAY0 + 20, "BUY", 105.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + 30, "SELL", 103.0, 1.0, -2.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 108.0, 1.0, 8.0)

    pnl_series = store.pnl_by_day("bot-01", cutoff_ns=None)

    assert pnl_series == [
        {"period_start": _DAY0, "pnl": 3.0},
        {"period_start": _DAY0 + _NS_PER_DAY, "pnl": 8.0},
    ]


def test_pnl_by_day_matches_sum_of_recent_trades_realized_pnl(store: SqliteFillsStore) -> None:
    # AD-10 correctness invariant: summing a range's pnl_series must equal the sum of
    # that same range's per-fill realized PnL.
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 5.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 108.0, 1.0, 8.0)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)
    pnl_series = store.pnl_by_day("bot-01", cutoff_ns=None)

    trades_total = sum(t["realized_pnl"] for t in trades if t["realized_pnl"] is not None)
    series_total = sum(entry["pnl"] for entry in pnl_series)
    assert trades_total == series_total == 13.0


def test_realized_pnls_returns_only_closing_fills_in_chronological_order(
    store: SqliteFillsStore,
) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 5.0)
    write_fill(store, "bot-01", _DAY0 + 20, "BUY", 105.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + 30, "SELL", 103.0, 1.0, -2.0)

    assert store.realized_pnls("bot-01", cutoff_ns=None) == [5.0, -2.0]


def test_realized_pnls_respects_cutoff_and_bot_id(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "SELL", 100.0, 1.0, 3.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 105.0, 1.0, 7.0)
    write_fill(store, "bot-02", _DAY0 + _NS_PER_DAY, "SELL", 105.0, 1.0, 99.0)

    assert store.realized_pnls("bot-01", cutoff_ns=_DAY0 + 1) == [7.0]


def test_position_realized_pnls_ignores_per_fill_realized_pnl(store: SqliteFillsStore) -> None:
    # A round trip closed across two reducing fills: both get a per-fill realized_pnl
    # (proportional split), but only the second (the one that actually closed the
    # position) gets position_realized_pnl -- position_realized_pnls() must return
    # exactly one value for this round trip, not two.
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 2.0, None)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 2.5, position_realized_pnl=None)
    write_fill(store, "bot-01", _DAY0 + 20, "SELL", 106.0, 1.0, 3.5, position_realized_pnl=6.0)

    assert store.realized_pnls("bot-01", cutoff_ns=None) == [2.5, 3.5]
    assert store.position_realized_pnls("bot-01", cutoff_ns=None) == [6.0]


def test_win_rate_stats_counts_round_trips_not_fills(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 2.0, None)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, 2.5, position_realized_pnl=None)
    write_fill(store, "bot-01", _DAY0 + 20, "SELL", 106.0, 1.0, 3.5, position_realized_pnl=6.0)
    write_fill(store, "bot-01", _DAY0 + 30, "BUY", 106.0, 1.0, None)
    write_fill(store, "bot-01", _DAY0 + 40, "SELL", 100.0, 1.0, -6.0, position_realized_pnl=-6.0)

    closed_trades, wins = store.win_rate_stats("bot-01")
    assert closed_trades == 2
    assert wins == 1


def test_a_closed_store_refuses_every_call_instead_of_reopening(tmp_path) -> None:
    # A late executor write after shutdown must fail loudly (bots.fill_lost), not open a second,
    # leaked connection.
    store = SqliteFillsStore(str(tmp_path / "fills.db"))
    write_fill(store, "bot-01", 1, "BUY", 100.0, 1.0, None)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        write_fill(store, "bot-01", 2, "SELL", 101.0, 1.0, 1.0)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.recent_trades("bot-01", None, 10)


def test_a_re_delivered_trade_id_is_stored_once(store: SqliteFillsStore) -> None:
    # DW-224: a fill re-delivered into a later process life must not inflate the stats.
    first = write_fill(store, "bot-01", 1, "SELL", 100.0, 1.0, 5.0, 5.0, trade_id="T-1")
    second = write_fill(store, "bot-01", 1, "SELL", 100.0, 1.0, 5.0, 5.0, trade_id="T-1")

    assert (first, second) == (True, False)
    assert len(store.recent_trades("bot-01", None, 10)) == 1
    assert store.win_rate_stats("bot-01") == (1, 1)


def test_the_same_trade_id_is_stored_for_each_bot(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", 1, "BUY", 100.0, 1.0, None, trade_id="T-1")
    write_fill(store, "bot-02", 1, "BUY", 100.0, 1.0, None, trade_id="T-1")

    assert len(store.recent_trades("bot-01", None, 10)) == 1
    assert len(store.recent_trades("bot-02", None, 10)) == 1


def _legacy_db(path: str) -> None:
    # The `fills` table as it stood before the trade_id column (DW-224), with one row.
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE fills (
            ts INTEGER NOT NULL, bot_id TEXT NOT NULL, side TEXT NOT NULL,
            price REAL NOT NULL, qty REAL NOT NULL, realized_pnl REAL,
            position_realized_pnl REAL
        );
        CREATE INDEX idx_bot_ts ON fills(bot_id, ts);
        INSERT INTO fills VALUES (1, 'bot-01', 'SELL', 100.0, 1.0, 5.0, 5.0);
        """
    )
    db.commit()
    db.close()


def test_a_legacy_fills_db_migrates_in_place(tmp_path: Path) -> None:
    path = str(tmp_path / "fills.db")
    _legacy_db(path)
    store = SqliteFillsStore(path)
    try:
        assert store.win_rate_stats("bot-01") == (1, 1)  # the old row, NULL trade id, still read
        assert write_fill(store, "bot-01", 2, "BUY", 101.0, 1.0, None, trade_id="T-2")
        assert not write_fill(store, "bot-01", 2, "BUY", 101.0, 1.0, None, trade_id="T-2")
        assert [t["ts"] for t in store.recent_trades("bot-01", None, 10)] == [1, 2]
    finally:
        store.close()
