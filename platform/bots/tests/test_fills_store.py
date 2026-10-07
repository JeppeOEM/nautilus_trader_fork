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
"""Tests for bots.infrastructure.fills_store -- Story 4.6, DW-223/225 (TEST-01: financial)."""

import sqlite3
from pathlib import Path

import pytest

from bots.infrastructure.fills_store import SqliteFillsStore
from bots.tests.support import write_close
from bots.tests.support import write_fill


_NS_PER_DAY = 24 * 3600 * 1_000_000_000
_DAY0 = 10 * _NS_PER_DAY  # arbitrary UTC-day-aligned base timestamp


def test_write_fill_then_recent_trades_round_trips_all_fields(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.5)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.5, closes_pnl=7.5)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert trades == [
        {"ts": _DAY0, "side": "BUY", "price": 100.0, "qty": 1.5, "realized_pnl": None},
        {"ts": _DAY0 + 10, "side": "SELL", "price": 105.0, "qty": 1.5, "realized_pnl": 7.5},
    ]


def test_recent_trades_filters_by_bot_id(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0)
    write_fill(store, "bot-02", _DAY0, "BUY", 200.0, 1.0)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert len(trades) == 1
    assert trades[0]["price"] == 100.0


def test_recent_trades_respects_cutoff(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 110.0, 1.0, closes_pnl=10.0)

    trades = store.recent_trades("bot-01", cutoff_ns=_DAY0 + 1, limit=500)

    assert len(trades) == 1
    assert trades[0]["ts"] == _DAY0 + _NS_PER_DAY


def test_recent_trades_caps_at_limit_keeping_most_recent_ascending(store: SqliteFillsStore) -> None:
    for i in range(600):
        write_fill(store, "bot-01", _DAY0 + i, "BUY", 100.0, 1.0)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert len(trades) == 500
    assert [t["ts"] for t in trades] == sorted(t["ts"] for t in trades)
    assert trades[0]["ts"] == _DAY0 + 100
    assert trades[-1]["ts"] == _DAY0 + 599


def test_a_close_shows_only_on_the_same_bots_fill_with_its_trade_id(
    store: SqliteFillsStore,
) -> None:
    # The blotter join is on (bot_id, trade_id): another bot's identical trade id never borrows it.
    write_fill(store, "bot-01", _DAY0, "SELL", 100.0, 1.0, closes_pnl=4.0, trade_id="T-1")
    write_fill(store, "bot-02", _DAY0, "SELL", 100.0, 1.0, trade_id="T-1")

    assert store.recent_trades("bot-02", None, 10)[0]["realized_pnl"] is None
    assert store.recent_trades("bot-01", None, 10)[0]["realized_pnl"] == 4.0


def test_pnl_by_day_sums_each_utc_day_s_closes(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, closes_pnl=5.0)
    write_fill(store, "bot-01", _DAY0 + 20, "BUY", 105.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + 30, "SELL", 103.0, 1.0, closes_pnl=-2.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 108.0, 1.0, closes_pnl=8.0)

    pnl_series = store.pnl_by_day("bot-01", cutoff_ns=None)

    assert pnl_series == [
        {"period_start": _DAY0, "pnl": 3.0},
        {"period_start": _DAY0 + _NS_PER_DAY, "pnl": 8.0},
    ]


def test_pnl_by_day_attributes_a_round_trip_to_the_day_it_closed(store: SqliteFillsStore) -> None:
    # A partial reduction on day 0 of a round trip that closes on day 1 counts on day 1 only.
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 2.0)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 106.0, 1.0, closes_pnl=11.0)

    assert store.pnl_by_day("bot-01", cutoff_ns=None) == [
        {"period_start": _DAY0 + _NS_PER_DAY, "pnl": 11.0}
    ]


def test_pnl_by_day_matches_sum_of_recent_trades_realized_pnl(store: SqliteFillsStore) -> None:
    # AD-10 correctness invariant: summing a range's pnl_series must equal the sum of
    # that same range's per-trade realized PnL.
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0, closes_pnl=5.0)
    write_fill(store, "bot-01", _DAY0 + _NS_PER_DAY, "SELL", 108.0, 1.0, closes_pnl=8.0)

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)
    pnl_series = store.pnl_by_day("bot-01", cutoff_ns=None)

    trades_total = sum(t["realized_pnl"] for t in trades if t["realized_pnl"] is not None)
    series_total = sum(entry["pnl"] for entry in pnl_series)
    assert trades_total == series_total == 13.0


def test_position_realized_pnls_is_one_per_round_trip_in_close_order(
    store: SqliteFillsStore,
) -> None:
    # Written out of order: the read orders by close time, not by insertion.
    write_close(store, "bot-01", _DAY0 + 30, -2.0)
    write_close(store, "bot-01", _DAY0 + 10, 5.0)

    assert store.position_realized_pnls("bot-01", cutoff_ns=None) == [5.0, -2.0]


def test_position_realized_pnls_respects_cutoff_and_bot_id(store: SqliteFillsStore) -> None:
    write_close(store, "bot-01", _DAY0, 3.0)
    write_close(store, "bot-01", _DAY0 + _NS_PER_DAY, 7.0)
    write_close(store, "bot-02", _DAY0 + _NS_PER_DAY, 99.0)

    assert store.position_realized_pnls("bot-01", cutoff_ns=_DAY0 + 1) == [7.0]


def test_total_realized_pnl_is_the_sum_of_every_round_trip(store: SqliteFillsStore) -> None:
    write_close(store, "bot-01", _DAY0, 0.1)
    write_close(store, "bot-01", _DAY0 + 1, 0.2)
    write_close(store, "bot-01", _DAY0 + 2, -0.05)
    write_close(store, "bot-02", _DAY0, 99.0)

    total = store.total_realized_pnl("bot-01")

    # Bit-identical to the per-trip list's own sum (the bots:status == bots:history invariant).
    assert total == sum(store.position_realized_pnls("bot-01", None))
    assert total == pytest.approx(0.25)


def test_a_multi_fill_close_is_one_round_trip(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 2.0)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + 20, "SELL", 106.0, 1.0, closes_pnl=6.0)

    trades = store.recent_trades("bot-01", None, 10)

    assert [t["realized_pnl"] for t in trades] == [None, None, 6.0]
    assert store.position_realized_pnls("bot-01", cutoff_ns=None) == [6.0]


def test_win_rate_stats_counts_round_trips_not_fills(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "BUY", 100.0, 2.0)
    write_fill(store, "bot-01", _DAY0 + 10, "SELL", 105.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + 20, "SELL", 106.0, 1.0, closes_pnl=6.0)
    write_fill(store, "bot-01", _DAY0 + 30, "BUY", 106.0, 1.0)
    write_fill(store, "bot-01", _DAY0 + 40, "SELL", 100.0, 1.0, closes_pnl=-6.0)

    closed_trades, wins = store.win_rate_stats("bot-01")
    assert closed_trades == 2
    assert wins == 1


def test_an_unlinked_close_counts_but_shows_on_no_trade_row(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "SELL", 100.0, 1.0)
    write_close(store, "bot-01", _DAY0, 4.0, trade_id=None)

    assert store.recent_trades("bot-01", None, 10)[0]["realized_pnl"] is None
    assert store.win_rate_stats("bot-01") == (1, 1)
    assert store.total_realized_pnl("bot-01") == 4.0


def test_a_re_delivered_unlinked_close_is_stored_once(store: SqliteFillsStore) -> None:
    first = write_close(store, "bot-01", _DAY0, 5.0, position_id="P-1")
    second = write_close(store, "bot-01", _DAY0, 5.0, position_id="P-1")
    # One NETTING position id, a later round trip: a different close, never a duplicate.
    third = write_close(store, "bot-01", _DAY0 + 1, -1.0, position_id="P-1")

    assert (first, second, third) == (True, False, True)
    assert store.position_realized_pnls("bot-01", None) == [5.0, -1.0]


def test_a_close_re_delivered_with_the_other_link_state_is_stored_once(
    store: SqliteFillsStore,
) -> None:
    # One process life stored it unlinked, a later one re-delivers it linked (or the reverse):
    # the two partial indexes cannot see each other, so the write checks across them.
    unlinked = write_close(store, "bot-01", _DAY0, 5.0, trade_id=None, position_id="P-1")
    relinked = write_close(store, "bot-01", _DAY0, 5.0, trade_id="T-1", position_id="P-1")
    linked = write_close(store, "bot-01", _DAY0 + 1, -1.0, trade_id="T-2", position_id="P-1")
    unlinked_again = write_close(store, "bot-01", _DAY0 + 1, -1.0, trade_id=None, position_id="P-1")

    assert (unlinked, relinked, linked, unlinked_again) == (True, False, True, False)
    assert store.position_realized_pnls("bot-01", None) == [5.0, -1.0]


def test_a_linked_close_is_keyed_on_its_trade_id_whatever_its_position_id(
    store: SqliteFillsStore,
) -> None:
    first = write_close(store, "bot-01", _DAY0, 5.0, trade_id="T-1", position_id="P-1")
    again = write_close(store, "bot-01", _DAY0, 5.0, trade_id="T-1", position_id="P-other")
    other_bot = write_close(store, "bot-02", _DAY0, 5.0, trade_id="T-1", position_id="P-1")

    assert (first, again, other_bot) == (True, False, True)
    assert store.position_realized_pnls("bot-01", None) == [5.0]


def test_two_round_trips_closing_in_the_same_ns_on_one_position_id_are_both_kept(
    store: SqliteFillsStore,
) -> None:
    # dYdX fills within one block share a timestamp: a NETTING id can close twice in one ns.
    first = write_close(store, "bot-01", _DAY0, 5.0, trade_id="T-1", position_id="P-1")
    second = write_close(store, "bot-01", _DAY0, -2.0, trade_id="T-2", position_id="P-1")

    assert (first, second) == (True, True)
    assert store.win_rate_stats("bot-01") == (2, 1)
    assert store.total_realized_pnl("bot-01") == 3.0


def test_the_blotter_join_never_multiplies_a_fill_row(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", _DAY0, "SELL", 100.0, 1.0, closes_pnl=4.0, trade_id="T-1")
    # A second close naming the same trade id (another position id) is refused, not joined twice.
    assert not write_close(store, "bot-01", _DAY0 + 1, 9.0, trade_id="T-1", position_id="P-x")

    trades = store.recent_trades("bot-01", None, 10)

    assert [t["realized_pnl"] for t in trades] == [4.0]


def test_a_closed_store_refuses_every_call_instead_of_reopening(tmp_path) -> None:
    # A late executor write after shutdown must fail loudly (bots.fill_lost), not open a second,
    # leaked connection.
    store = SqliteFillsStore(str(tmp_path / "fills.db"))
    write_fill(store, "bot-01", 1, "BUY", 100.0, 1.0)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        write_fill(store, "bot-01", 2, "SELL", 101.0, 1.0)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        write_close(store, "bot-01", 2, 1.0)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.recent_trades("bot-01", None, 10)


def test_a_re_delivered_trade_id_is_stored_once(store: SqliteFillsStore) -> None:
    # DW-224: a fill re-delivered into a later process life must not inflate the stats.
    first = write_fill(store, "bot-01", 1, "SELL", 100.0, 1.0, trade_id="T-1")
    second = write_fill(store, "bot-01", 1, "SELL", 100.0, 1.0, trade_id="T-1")

    assert (first, second) == (True, False)
    assert len(store.recent_trades("bot-01", None, 10)) == 1


def test_the_same_trade_id_is_stored_for_each_bot(store: SqliteFillsStore) -> None:
    write_fill(store, "bot-01", 1, "BUY", 100.0, 1.0, trade_id="T-1")
    write_fill(store, "bot-02", 1, "BUY", 100.0, 1.0, trade_id="T-1")

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
        assert write_fill(store, "bot-01", 2, "BUY", 101.0, 1.0, trade_id="T-2")
        assert not write_fill(store, "bot-01", 2, "BUY", 101.0, 1.0, trade_id="T-2")
        trades = store.recent_trades("bot-01", None, 10)
        assert [t["ts"] for t in trades] == [1, 2]
        # A NULL trade id cannot join its copied close: the legacy column still shows the PnL.
        assert [t["realized_pnl"] for t in trades] == [5.0, None]
    finally:
        store.close()


# The `fills` table as it stood right before `position_closes` (DW-223): two round trips, the
# first closed across two reducing fills, each carrying a per-fill share in `realized_pnl`.
_PRE_CLOSES_ROWS = (
    (_DAY0, "bot-01", "BUY", 100.0, 2.0, None, None, "T-1"),
    (_DAY0 + 10, "bot-01", "SELL", 105.0, 1.0, 2.5, None, "T-2"),
    (_DAY0 + 20, "bot-01", "SELL", 106.0, 1.0, 3.5, 6.0, "T-3"),
    (_DAY0 + 30, "bot-01", "BUY", 106.0, 1.0, None, None, "T-4"),
    (_DAY0 + _NS_PER_DAY, "bot-01", "SELL", 100.0, 1.0, -6.0, -6.0, "T-5"),
)


def _pre_closes_db(path: str) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE fills (
            ts INTEGER NOT NULL, bot_id TEXT NOT NULL, side TEXT NOT NULL,
            price REAL NOT NULL, qty REAL NOT NULL, realized_pnl REAL,
            position_realized_pnl REAL, trade_id TEXT
        );
        CREATE INDEX idx_bot_ts ON fills(bot_id, ts);
        CREATE UNIQUE INDEX idx_bot_trade ON fills(bot_id, trade_id);
        """
    )
    db.executemany("INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?)", _PRE_CLOSES_ROWS)
    db.commit()
    db.close()


def _close_rows(path: str) -> int:
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT COUNT(*) FROM position_closes").fetchone()[0]
    finally:
        db.close()


def test_legacy_round_trips_migrate_once_and_read_as_before(tmp_path: Path) -> None:
    path = str(tmp_path / "fills.db")
    _pre_closes_db(path)
    store = SqliteFillsStore(path)
    try:
        # The values the pre-DW-223 queries returned over `position_realized_pnl`.
        assert store.win_rate_stats("bot-01") == (2, 1)
        assert store.position_realized_pnls("bot-01", None) == [6.0, -6.0]
        assert store.pnl_by_day("bot-01", None) == [
            {"period_start": _DAY0, "pnl": 6.0},
            {"period_start": _DAY0 + _NS_PER_DAY, "pnl": -6.0},
        ]
        # The closing fills show their round trip; the partial reduction's share is gone.
        trades = store.recent_trades("bot-01", None, 10)
        assert [t["realized_pnl"] for t in trades] == [None, None, 6.0, None, -6.0]
    finally:
        store.close()

    reopened = SqliteFillsStore(path)
    try:
        assert reopened.win_rate_stats("bot-01") == (2, 1)
    finally:
        reopened.close()
    assert _close_rows(path) == 2  # never copied a second time


def test_a_migrated_close_re_delivered_under_its_real_position_id_is_not_counted_twice(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "fills.db")
    _pre_closes_db(path)
    store = SqliteFillsStore(path)
    try:
        # T-3 closed the first legacy round trip, migrated as `legacy-<rowid>`.
        redelivered = write_close(
            store, "bot-01", _DAY0 + 20, 6.0, trade_id="T-3", position_id="BTCUSDT.BINANCE-S-001"
        )

        assert not redelivered
        assert store.position_realized_pnls("bot-01", None) == [6.0, -6.0]
    finally:
        store.close()
