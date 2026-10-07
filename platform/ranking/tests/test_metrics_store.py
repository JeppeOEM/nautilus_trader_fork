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
Unit tests for `ranking.infrastructure.metrics_store`: the writer's write/latest/history/nearest
roundtrip, pruning and migration, and the read-only readers other processes use.
"""

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from ranking.infrastructure.metrics_store import COLS
from ranking.infrastructure.metrics_store import NEAREST_TOLERANCE_S
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from ranking.infrastructure.metrics_store import _read_only
from ranking.infrastructure.metrics_store import read_history
from ranking.infrastructure.metrics_store import read_nearest


_NOW = time.time_ns()
_DAY_NS = 86_400 * 1_000_000_000


_OPENED: list[SqliteMetricsStore] = []


@pytest.fixture(autouse=True)
def _close_stores() -> Iterator[None]:
    yield
    while _OPENED:
        _OPENED.pop().close()


def _store(path: str) -> SqliteMetricsStore:
    store = SqliteMetricsStore(path)
    _OPENED.append(store)
    return store


def _row(ts: int, instrument_id: str = "BTC-USD-PERP.DYDX", **kwargs: object) -> dict:
    base = {
        "ts": ts,
        "instrument_id": instrument_id,
        "price": None,
        "pct_1h": None,
        "pct_24h": None,
        "volatility": None,
        "ofi": None,
        "microprice": None,
        "spread": None,
        "rank": None,
        "volume24h": None,
    }
    return {**base, **kwargs}


def test_write_and_latest_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW, price=42.0)])
    rows = store.latest()
    assert len(rows) == 1
    assert rows[0]["instrument_id"] == "BTC-USD-PERP.DYDX"
    assert rows[0]["price"] == 42.0


def test_all_metric_columns_persisted(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write(
        [
            _row(
                _NOW,
                price=1.0,
                pct_1h=2.0,
                pct_24h=3.0,
                volatility=4.0,
                ofi=5.0,
                microprice=6.0,
                spread=7.0,
            )
        ],
    )
    rows = store.latest()
    r = rows[0]
    assert r["price"] == 1.0
    assert r["ofi"] == 5.0
    assert r["spread"] == 7.0


def test_latest_returns_most_recent_per_instrument(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW - 1000, price=1.0), _row(_NOW, price=2.0)])
    rows = store.latest()
    assert len(rows) == 1
    assert rows[0]["price"] == 2.0


def test_latest_multiple_instruments(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write(
        [
            _row(_NOW, "BTC-USD-PERP.DYDX", price=10.0),
            _row(_NOW, "ETH-USD-PERP.DYDX", price=20.0),
        ],
    )
    by_iid = {r["instrument_id"]: r for r in store.latest()}
    assert by_iid["BTC-USD-PERP.DYDX"]["price"] == 10.0
    assert by_iid["ETH-USD-PERP.DYDX"]["price"] == 20.0


def test_history_filters_by_days(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    old = _NOW - 40 * _DAY_NS  # 40 days ago — outside the 31-day window
    store.write([_row(old, price=0.0), _row(_NOW, price=99.0)])
    rows = store.history("BTC-USD-PERP.DYDX", days=31)
    assert len(rows) == 1
    assert rows[0]["price"] == 99.0


def test_history_ordered_by_ts(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW, price=2.0), _row(_NOW - 1000, price=1.0)])
    rows = store.history("BTC-USD-PERP.DYDX", days=1)
    assert rows[0]["ts"] < rows[1]["ts"]


def test_history_includes_ts_column(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW, price=1.0)])
    rows = store.history("BTC-USD-PERP.DYDX", days=1)
    assert "ts" in rows[0]
    assert rows[0]["ts"] == _NOW


def test_write_prunes_rows_older_than_retain_days(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    ancient = _NOW - 40 * _DAY_NS
    store.write([_row(ancient, price=0.0)], retain_days=31)
    store.write([_row(_NOW, price=1.0)], retain_days=31)
    rows = store.latest()
    assert len(rows) == 1
    assert rows[0]["price"] == 1.0


def test_upsert_replaces_same_ts_and_instrument(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW, price=1.0)])
    store.write([_row(_NOW, price=2.0)])  # same primary key → replace
    rows = store.latest()
    assert len(rows) == 1
    assert rows[0]["price"] == 2.0


def test_write_empty_list_is_noop(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([])
    assert store.latest() == []


def test_rank_and_volume24h_persisted(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW, rank=1, volume24h=123.0)])
    rows = store.latest()
    assert rows[0]["rank"] == 1
    assert rows[0]["volume24h"] == 123.0


def test_nearest_returns_closest_row(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write(
        [
            _row(_NOW - 3_000_000_000, rank=2),
            _row(_NOW - 1_000_000_000, rank=1),
        ],
    )
    earlier = store.nearest("BTC-USD-PERP.DYDX", _NOW - 2_500_000_000)
    later = store.nearest("BTC-USD-PERP.DYDX", _NOW)
    assert earlier is not None
    assert later is not None
    assert (earlier["rank"], later["rank"]) == (2, 1)


def test_nearest_is_bounded_by_the_tolerance(tmp_path: Path) -> None:
    """Story 31.3: a row more than NEAREST_TOLERANCE_S away is another time -- None, as for none."""
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    edge_ns = NEAREST_TOLERANCE_S * 1_000_000_000
    store.write([_row(_NOW, rank=1)])
    assert store.nearest("BTC-USD-PERP.DYDX", _NOW + edge_ns) is not None
    assert store.nearest("BTC-USD-PERP.DYDX", _NOW - edge_ns) is not None
    assert store.nearest("BTC-USD-PERP.DYDX", _NOW + edge_ns + 1) is None
    assert read_nearest(path, "BTC-USD-PERP.DYDX", _NOW - edge_ns - 1) is None


def test_nearest_at_the_int64_edges_is_none_not_an_overflow(tmp_path: Path) -> None:
    """Review P12: `ts +- tolerance` is clamped to int64, SQLite's only integer binding."""
    store = _store(str(tmp_path / "metrics.db"))
    store.write([_row(_NOW, rank=1)])
    assert store.nearest("BTC-USD-PERP.DYDX", 2**63 - 1) is None
    assert store.nearest("BTC-USD-PERP.DYDX", -(2**63)) is None
    assert store.nearest("BTC-USD-PERP.DYDX", 2**64) is None
    assert store.nearest("BTC-USD-PERP.DYDX", -(2**64)) is None


def test_nearest_returns_none_for_unknown_instrument(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW, rank=1)])
    assert store.nearest("ETH-USD-PERP.DYDX", _NOW) is None


def test_migration_adds_new_columns_to_existing_table(tmp_path: Path) -> None:
    """Simulates a pre-existing metrics.db written before rank/volume24h existed."""
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    old_cols = ("price", "pct_1h", "pct_24h", "volatility", "ofi", "microprice", "spread")
    db = sqlite3.connect(path)
    db.executescript(f"""
        CREATE TABLE snapshots (
            ts INTEGER NOT NULL, instrument_id TEXT NOT NULL,
            {", ".join(f"{c} REAL" for c in old_cols)},
            PRIMARY KEY (ts, instrument_id)
        );
        CREATE INDEX idx_iid_ts ON snapshots(instrument_id, ts);
    """)
    old_ts = _NOW - 1_000_000_000
    db.execute(
        "INSERT INTO snapshots(ts, instrument_id, price) VALUES (?, ?, ?)",
        (old_ts, "BTC-USD-PERP.DYDX", 9.0),
    )
    db.commit()
    db.close()

    store.write([_row(_NOW, rank=3, volume24h=50.0)])
    rows = store.history("BTC-USD-PERP.DYDX", days=1)
    assert len(rows) == 2
    assert rows[0]["ts"] == old_ts
    assert rows[0]["rank"] is None
    assert rows[1]["rank"] == 3


def test_price_near_days_ago_returns_price_at_or_before_target_per_instrument(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    week = 7 * _DAY_NS
    # The store reads its own clock: stamp from *now*, not the import-time `_NOW` -- a suite that
    # reaches this test more than 60 s after collection would otherwise move the target past the
    # "after target" row (Story 31.3's longer comparator tests made that happen).
    now = time.time_ns()
    store.write(
        [
            _row(now - week - 60 * 1_000_000_000, "BTC-USD-PERP.DYDX", price=90.0),
            _row(
                now - week - 10 * 1_000_000_000, "BTC-USD-PERP.DYDX", price=100.0
            ),  # closest before
            _row(now - week + 60 * 1_000_000_000, "BTC-USD-PERP.DYDX", price=110.0),  # after target
            _row(now - week - 5 * 1_000_000_000, "ETH-USD-PERP.DYDX", price=7.0),
        ],
    )

    assert store.price_near_days_ago(7) == {
        "BTC-USD-PERP.DYDX": 100.0,
        "ETH-USD-PERP.DYDX": 7.0,
    }


def test_price_near_days_ago_omits_instruments_without_history_that_old(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write(
        [
            _row(_NOW - 10 * _DAY_NS, "OLD-USD-PERP.DYDX", price=5.0),
            _row(_NOW - 2 * _DAY_NS, "NEW-USD-PERP.DYDX", price=9.0),  # only 2 days of history
            _row(_NOW - 20 * _DAY_NS, "GAP-USD-PERP.DYDX", price=1.0),  # nothing near the 7d mark
        ],
    )

    assert store.price_near_days_ago(10) == {"OLD-USD-PERP.DYDX": 5.0}
    assert store.price_near_days_ago(7) == {}


def test_readers_see_what_the_writer_wrote(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW - 1_000, price=1.0), _row(_NOW, price=2.0)])

    assert read_history(path, "BTC-USD-PERP.DYDX", days=1) == store.history("BTC-USD-PERP.DYDX", 1)
    assert read_nearest(path, "BTC-USD-PERP.DYDX", _NOW) == store.nearest("BTC-USD-PERP.DYDX", _NOW)


def test_readers_of_a_store_never_written_return_nothing_and_create_no_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metrics.db"

    assert read_history(str(path), "BTC-USD-PERP.DYDX") == []
    assert read_nearest(str(path), "BTC-USD-PERP.DYDX", _NOW) is None
    assert not path.exists()


def test_readers_before_the_writer_creates_its_table_return_nothing(tmp_path: Path) -> None:
    """The file can exist before the table does (another process opened it first)."""
    path = tmp_path / "metrics.db"
    sqlite3.connect(path).close()
    assert path.exists()

    assert read_history(str(path), "BTC-USD-PERP.DYDX") == []
    assert read_nearest(str(path), "BTC-USD-PERP.DYDX", _NOW) is None


def test_a_reader_connection_is_read_only(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    _store(path).write([_row(_NOW, price=1.0)])

    with closing(_read_only(path)) as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.execute("DELETE FROM snapshots")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only directory mode")
def test_readers_on_a_read_only_mount_still_work_after_the_writer_closes(tmp_path: Path) -> None:
    """data_api's `:ro` mount: a clean writer close must not strand the reader without sidecars."""
    mount = tmp_path / "metrics_dir"
    mount.mkdir()
    path = str(mount / "metrics.db")
    store = SqliteMetricsStore(path)
    store.write([_row(_NOW, price=1.0)])
    store.close()
    mount.chmod(0o555)
    try:
        assert [r["price"] for r in read_history(path, "BTC-USD-PERP.DYDX", days=1)] == [1.0]
        assert read_nearest(path, "BTC-USD-PERP.DYDX", _NOW) is not None
    finally:
        mount.chmod(0o755)


def test_a_failed_write_rolls_back_and_leaves_no_open_transaction(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    store = _store(path)
    store.write([_row(_NOW - 40 * _DAY_NS, price=1.0)])

    with pytest.raises(sqlite3.IntegrityError):
        store.write([_row(_NOW, price=2.0), _row(_NOW, instrument_id=None, price=3.0)])  # type: ignore[arg-type]

    assert store.history("BTC-USD-PERP.DYDX", days=60) != []  # the prune was rolled back too
    assert store._db is not None
    assert not store._db.in_transaction


# The columns metrics.db had before Story 33.4, in their deployed order.
_PRE_33_4_COLS = (
    "price",
    "pct_1h",
    "pct_24h",
    "pct_1w",
    "pct_1m",
    "volatility",
    "ofi",
    "microprice",
    "spread",
    "rank",
    "volume24h",
)


def test_story_33_4_columns_are_appended_after_every_existing_one() -> None:
    assert COLS[: len(_PRE_33_4_COLS)] == _PRE_33_4_COLS
    assert COLS[len(_PRE_33_4_COLS) :] == (
        "funding_rate",
        "funding_annualised",
        "open_interest",
        "oi_change_1h",
        "oi_change_24h",
        "basis_mi_bps",
        "basis_ml_bps",
        "liq_long_1h",
        "liq_short_1h",
        "liq_notional_1h",
        "liq_ratio_1h",
        "forced_share_1h",
        "relative_volume",
        "high_24h",
        "low_24h",
        "range_position_24h",
        "oi_change_1h_pct",
        "oi_change_24h_pct",
    )


def test_migration_adds_the_story_33_7_columns_nullable_to_a_33_4_table(tmp_path: Path) -> None:
    path = str(tmp_path / "metrics.db")
    pre_33_7 = COLS[: COLS.index("oi_change_1h_pct")]
    db = sqlite3.connect(path)
    db.executescript(f"""
        CREATE TABLE snapshots (
            ts INTEGER NOT NULL, instrument_id TEXT NOT NULL,
            {", ".join(f"{c} REAL" for c in pre_33_7)},
            PRIMARY KEY (ts, instrument_id)
        );
    """)
    db.execute(
        "INSERT INTO snapshots(ts, instrument_id, oi_change_1h) VALUES (?, ?, ?)",
        (_NOW - 1_000_000_000, "BTC-USD-PERP.DYDX", 10.0),
    )
    db.commit()
    db.close()

    _store(path).write([_row(_NOW, oi_change_1h=10.0, oi_change_1h_pct=5.0)])

    with closing(sqlite3.connect(path)) as check:
        columns = [r[1] for r in check.execute("PRAGMA table_info(snapshots)")]
    assert columns == ["ts", "instrument_id", *COLS]
    old, new = _store(path).history("BTC-USD-PERP.DYDX", days=1)
    assert (old["oi_change_1h"], old["oi_change_1h_pct"], old["oi_change_24h_pct"]) == (
        10.0,
        None,
        None,
    )
    assert (new["oi_change_1h_pct"], new["oi_change_24h_pct"]) == (5.0, None)


def test_migration_adds_the_story_33_4_columns_nullable_to_a_pre_33_4_table(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "metrics.db")
    db = sqlite3.connect(path)
    db.executescript(f"""
        CREATE TABLE snapshots (
            ts INTEGER NOT NULL, instrument_id TEXT NOT NULL,
            {", ".join(f"{c} REAL" for c in _PRE_33_4_COLS)},
            PRIMARY KEY (ts, instrument_id)
        );
    """)
    old_ts = _NOW - 1_000_000_000
    db.execute(
        "INSERT INTO snapshots(ts, instrument_id, price) VALUES (?, ?, ?)",
        (old_ts, "BTC-USD-PERP.DYDX", 9.0),
    )
    db.commit()
    db.close()

    _store(path).write([_row(_NOW, funding_rate=0.0001, range_position_24h=0.25)])

    with closing(sqlite3.connect(path)) as check:
        columns = [r[1] for r in check.execute("PRAGMA table_info(snapshots)")]
    assert columns == ["ts", "instrument_id", *COLS]
    old, new = _store(path).history("BTC-USD-PERP.DYDX", days=1)
    assert (old["price"], old["funding_rate"], old["range_position_24h"]) == (9.0, None, None)
    assert (new["funding_rate"], new["range_position_24h"]) == (0.0001, 0.25)
