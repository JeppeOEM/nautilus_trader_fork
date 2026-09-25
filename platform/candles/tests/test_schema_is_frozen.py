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
The store's DDL and merge statement are frozen text (spine AD-D12).

`candles_<venue>.db` outlives every deploy -- the collectors keep writing the same file across image
rebuilds, and `data_api` reads it live. A reformatted `CREATE TABLE` would not migrate anything (the
`IF NOT EXISTS` means an existing file keeps its old shape while new files get the new one, silently
diverging), and a reworded `_UPSERT` could change which of `o`/`c` wins a conflict or whether `v`
accumulates. The fixtures below are the exact pre-move text of `ml_signals/candle_store.py`, copied
out of the tree at the Story 24.1 baseline revision.
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
