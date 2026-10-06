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
The store's DDL and merge statement are recorded text (spine AD-D12), changed only deliberately.

`candles_<venue>.db` outlives every deploy -- the collectors keep writing the same file across image
rebuilds, and `data_api` reads it live. A reformatted `CREATE TABLE` would not migrate anything (the
`IF NOT EXISTS` means an existing file keeps its old shape while new files get the new one, silently
diverging), and a reworded `_UPSERT` could change which of `o`/`c` wins a conflict or whether `v`
accumulates. An added index needs no separate migration: `connect_rw` runs `_SCHEMA` on every writer
open, so the `CREATE INDEX IF NOT EXISTS` statement itself migrates an existing file -- at the cost
of a one-time build on that open, holding the write lock (the `Known limit:` on
`sqlite_store.prune`).

The fixtures below are the exact text of the candle store module, first copied out of the tree at
the Story 24.1 baseline revision. Nothing is frozen until prod: a change re-records the fixture in
the same change, as DW-195 did when it added `candles_by_bar_seconds_t` (the pre-index DDL stays in
`candle_store_schema_pre_index.sql` for the migration test).
"""

from pathlib import Path

from candles.infrastructure import sqlite_store
from candles.infrastructure.sqlite_store import connect_rw


_FIXTURES = Path(__file__).parent / "fixtures"


def test_schema_text_is_byte_identical_to_the_recorded_copy() -> None:
    assert (_FIXTURES / "candle_store_schema.sql").read_text() == sqlite_store._SCHEMA


def test_upsert_text_is_byte_identical_to_the_recorded_copy() -> None:
    assert (_FIXTURES / "candle_store_upsert.sql").read_text() == sqlite_store._UPSERT


def test_a_fresh_store_has_exactly_the_three_recorded_tables(tmp_path: Path) -> None:
    """The text is what SQLite actually applied, not just a string the module happens to hold."""
    db = connect_rw(str(tmp_path / "c.db"))
    names = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    ]
    db.close()
    assert names == ["built_through", "candles", "verified_days"]


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
    assert columns == [
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
