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
The store's DDL and write statement are recorded text (spine AD-D12), changed only deliberately.

`candles_<venue>.db` outlives every deploy -- the collectors keep writing the same file across image
rebuilds, and `data_api` reads it live. A reformatted `CREATE TABLE` would not migrate anything (the
`IF NOT EXISTS` means an existing file keeps its old shape while new files get the new one, silently
diverging), so a schema change is a recorded text change *plus* `_migrate`, and a file created
under the previous text must come out with the same columns, in the same order, as a new one. An
added index needs no `_migrate` step: `connect_rw` runs `_SCHEMA` on every writer open, so the
`CREATE INDEX IF NOT EXISTS` statement itself migrates an existing file -- at the cost of a one-time
build on that open, holding the write lock (the `Known limit:` on `sqlite_store.prune`).

The fixtures below are the exact text of the candle store module, first copied out of the tree at
the Story 24.1 baseline revision. Nothing is frozen until prod: a change re-records the fixture in
the same change, as DW-195 did when it added `candles_by_bar_seconds_t` and Story 33.3 when it added
the aggregate columns and the two liquidation tables.

Story 33.3 replaced the SQL `_UPSERT` merge with a Python merge (`domain.fold.merge_buckets`) and
the `_REPLACE` write: the pre-33.3 schema and upsert are kept as fixtures, so the migration is
proven on a file built by exactly the old text. That pre-33.3 schema is also the pre-DW-195 one (no
index), so `test_candle_store.py`'s index-migration tests build their old store from it too.
"""

import sqlite3
from pathlib import Path

import pytest

from candles.application import queries
from candles.domain.fold import AGGREGATE_KEYS
from candles.infrastructure import sqlite_store
from candles.infrastructure.sqlite_store import connect_rw


_FIXTURES = Path(__file__).parent / "fixtures"
_IID = "BTCUSDT-LINEAR.BYBIT"
_BASE_COLUMNS = [
    "instrument_id",
    "bar_seconds",
    "t",
    "o",
    "h",
    "l",
    "c",
    "v",
    "seconds_observed",
]


def test_schema_text_is_byte_identical_to_the_recorded_copy() -> None:
    assert (_FIXTURES / "candle_store_schema.sql").read_text() == sqlite_store._SCHEMA


def test_replace_text_is_byte_identical_to_the_recorded_copy() -> None:
    assert (_FIXTURES / "candle_store_replace.sql").read_text() == sqlite_store._REPLACE


def test_a_fresh_store_has_exactly_the_five_recorded_tables(tmp_path: Path) -> None:
    """The text is what SQLite actually applied, not just a string the module happens to hold."""
    db = connect_rw(str(tmp_path / "c.db"))
    names = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    ]
    db.close()
    assert names == [
        "built_through",
        "candles",
        "liquidation_feed_since",
        "liquidations_applied",
        "verified_days",
    ]


def test_a_fresh_store_has_exactly_the_one_recorded_index_on_candles(tmp_path: Path) -> None:
    """
    Name and definition: `IF NOT EXISTS` skips an index of the same name whatever its columns, so a
    redefined index must take a new name or existing stores keep the old one.
    """
    db = connect_rw(str(tmp_path / "c.db"))
    indexes = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'candles' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    db.close()
    assert indexes == [
        (
            "candles_by_bar_seconds_t",
            "CREATE INDEX candles_by_bar_seconds_t ON candles(bar_seconds, t)",
        )
    ]


def test_the_candles_table_columns_are_the_recorded_ones(tmp_path: Path) -> None:
    db = connect_rw(str(tmp_path / "c.db"))
    columns = [row[1] for row in db.execute("PRAGMA table_info(candles)").fetchall()]
    db.close()
    assert columns == [*_BASE_COLUMNS, *AGGREGATE_KEYS]


def _old_file(path: Path) -> None:
    """Build a store exactly as the pre-33.3 text did, one bar merged by the old upsert."""
    db = sqlite3.connect(path)
    db.executescript((_FIXTURES / "candle_store_schema_pre_33_3.sql").read_text())
    upsert = (_FIXTURES / "candle_store_upsert_pre_33_3.sql").read_text()
    db.execute(upsert, (_IID, 60, 0, 100.0, 101.0, 99.0, 100.5, 1.5, 30))
    db.commit()
    db.close()


def test_a_pre_33_3_file_is_migrated_to_the_new_columns_null_on_old_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_file(path)
    db = connect_rw(str(path))
    columns = [row[1] for row in db.execute("PRAGMA table_info(candles)").fetchall()]
    assert columns == [*_BASE_COLUMNS, *AGGREGATE_KEYS]  # the same order as a fresh file
    (bar,) = queries.window(db, _IID, 60, 1 << 62, 5)
    assert (bar["o"], bar["v"], bar["seconds_observed"]) == (100.0, 1.5, 30)
    assert {key: bar[key] for key in AGGREGATE_KEYS} == dict.fromkeys(AGGREGATE_KEYS)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "liquidations_applied" in tables


def test_migrating_twice_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_file(path)
    connect_rw(str(path)).close()
    db = connect_rw(str(path))
    columns = [row[1] for row in db.execute("PRAGMA table_info(candles)").fetchall()]
    assert columns == [*_BASE_COLUMNS, *AGGREGATE_KEYS]


def test_an_unmigrated_file_read_only_serves_the_new_keys_as_null(tmp_path: Path) -> None:
    """The data_api may open a file before its collector restarted on the new code."""
    path = tmp_path / "old.db"
    _old_file(path)
    with sqlite_store.connect_ro(str(path)) as db:
        assert db is not None
        (bar,) = queries.window(db, _IID, 60, 1 << 62, 5)
        assert queries.flow_totals(db, _IID, 60, 1 << 62) is None
    assert list(bar)[-len(AGGREGATE_KEYS) :] == list(AGGREGATE_KEYS)
    assert all(bar[key] is None for key in AGGREGATE_KEYS)
    assert bar["o"] == 100.0


def test_two_writers_opening_an_unmigrated_file_migrate_it_once(tmp_path: Path) -> None:
    """
    A collector and a rebuild opening one unmigrated file together: the second to take the lock
    re-reads the columns inside it and adds none, instead of failing on "duplicate column name".
    """
    path = tmp_path / "old.db"
    _old_file(path)
    late = sqlite3.connect(path, factory=sqlite_store.CandleConnection)
    stale = sqlite_store.table_columns(late)  # read (and cached) before the other writer migrates
    assert "buy_v" not in stale
    connect_rw(str(path)).close()
    sqlite_store._migrate(late)
    columns = [row[1] for row in late.execute("PRAGMA table_info(candles)").fetchall()]
    late.close()
    assert columns == [*_BASE_COLUMNS, *AGGREGATE_KEYS]


def test_the_migration_waits_for_another_writers_lock(tmp_path: Path) -> None:
    """The column read and the `ADD COLUMN`s run under `BEGIN IMMEDIATE`, never beside a writer."""
    path = tmp_path / "old.db"
    _old_file(path)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    waiter = sqlite3.connect(path, timeout=0.05, factory=sqlite_store.CandleConnection)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            sqlite_store._migrate(waiter)
        columns = [row[1] for row in waiter.execute("PRAGMA table_info(candles)").fetchall()]
        assert columns == _BASE_COLUMNS  # nothing added while the other writer held the lock
    finally:
        holder.rollback()
        holder.close()
        waiter.close()
