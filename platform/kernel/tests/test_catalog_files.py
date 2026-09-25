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
"""`kernel.catalog_files`: read-only helpers over the catalog's second-snapshot files."""

import ast
import math
from pathlib import Path

import pytest

from kernel import catalog_files
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "ETH-USD-PERP.DYDX"
_DAY0 = 20_000 * NS_PER_DAY


def _snapshot(ts_event: int, ts_init: int, close: float | None) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        InstrumentId.from_str(_IID),
        [1.0],
        [1.0],
        [2.0],
        [1.0],
        1.0 if close else 0.0,
        0.5 if close else 0.0,
        1 if close else 0,
        1 if close else 0,
        ts_event,
        ts_init,
        close,
        close,
        close,
        close,
    )


@pytest.fixture
def catalog(tmp_path: Path) -> str:
    """Three files: day 0 (two rows), one straddling midnight, day 1 far later."""
    writer = ParquetDataCatalog(str(tmp_path))
    writer.write_data([_snapshot(_DAY0 + 10 * NS_PER_S, _DAY0 + 11 * NS_PER_S, 10.0)])
    writer.write_data([_snapshot(_DAY0 + 20 * NS_PER_S, _DAY0 + 21 * NS_PER_S, None)])
    late = _DAY0 + NS_PER_DAY - NS_PER_S
    writer.write_data(
        [
            _snapshot(late, late + NS_PER_S // 2, 11.0),
            _snapshot(late + NS_PER_S, late + 2 * NS_PER_S, 12.0),
        ]
    )
    far = _DAY0 + NS_PER_DAY + 3_600 * NS_PER_S
    writer.write_data([_snapshot(far, far + NS_PER_S, 13.0)])
    return str(tmp_path)


def test_snapshot_dir_is_what_the_catalog_writes(catalog: str) -> None:
    assert catalog_files.SNAPSHOT_DIRNAME == "custom_dydx_second_snapshot"
    assert (Path(catalog) / "data" / catalog_files.SNAPSHOT_DIRNAME / _IID).is_dir()
    assert len(catalog_files.snapshot_files(catalog, _IID)) == 4
    assert catalog_files.snapshot_files(catalog, "NOPE.DYDX") == []


def test_data_file_ranges_ascending_from_names(catalog: str) -> None:
    ranges = catalog_files.data_file_ranges(catalog, _IID)
    assert ranges == sorted(ranges)
    assert ranges[0] == (_DAY0 + 11 * NS_PER_S, _DAY0 + 11 * NS_PER_S)
    assert len(ranges) == 4


def test_query_second_ohlc_filters_on_ts_event(catalog: str) -> None:
    rows = catalog_files.query_second_ohlc(catalog, _IID, _DAY0, _DAY0 + 20 * NS_PER_S)
    assert rows == [
        SecondOHLC(_DAY0 + 10 * NS_PER_S, 10.0, 10.0, 10.0, 10.0, 1.0, 0.5),
        SecondOHLC(_DAY0 + 20 * NS_PER_S, None, None, None, None, 0.0, 0.0),
    ]


def test_query_second_ohlc_reads_files_within_the_read_margin(catalog: str) -> None:
    """A row whose `ts_init` (file span) lies past the window's end is still found by `ts_event`."""
    lo = _DAY0 + 10 * NS_PER_S
    rows = catalog_files.query_second_ohlc(catalog, _IID, lo, lo)
    assert [r.ts_event for r in rows] == [lo]
    assert READ_SPAN_MARGIN_NS > NS_PER_S  # the file's span starts 1 s after the window


def test_files_by_day_lists_a_midnight_file_under_both_days(catalog: str) -> None:
    days = catalog_files.files_by_day(catalog, _IID, 0, 2 * _DAY0)
    day0, day1 = _DAY0 // NS_PER_DAY, _DAY0 // NS_PER_DAY + 1
    assert sorted(days) == [day0, day1]
    assert len(days[day0]) == 3
    assert len(days[day1]) == 2
    only_day1 = catalog_files.files_by_day(
        catalog, _IID, _DAY0 + NS_PER_DAY + 60 * NS_PER_S, 2 * _DAY0
    )
    assert sorted(only_day1) == [day1]
    assert len(only_day1[day1]) == 1


def test_second_ohlc_arrays(catalog: str) -> None:
    paths = sorted(catalog_files.snapshot_files(catalog, _IID))
    cols = catalog_files.second_ohlc_arrays(paths)
    assert list(cols["ts_ms"]) == sorted(cols["ts_ms"])
    assert cols["c"][0] == 10.0
    assert math.isnan(cols["c"][1])
    assert list(cols["v"]) == [1.5, 0.0, 1.5, 1.5, 1.5]
    assert len(catalog_files.second_ohlc_arrays([])["ts_ms"]) == 0


def test_module_never_writes_or_builds_a_catalog() -> None:
    """AD-D3: read helpers only -- no `ParquetDataCatalog`, no write/rename/delete call."""
    tree = ast.parse(Path(catalog_files.__file__).read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "ParquetDataCatalog" not in names | attrs
    forbidden = {"write_table", "write_data", "rename", "replace", "unlink", "remove", "rmtree"}
    assert attrs & forbidden == set()


def test_the_projected_columns_all_exist_in_the_snapshot_schema() -> None:
    """
    `_OHLC_COLUMNS` is `SecondOHLC._fields`, so a field rename would silently project columns the
    Parquet files do not hold -- and `_ohlc_rows`/`second_ohlc_arrays` substitute `None`/`0.0` for
    an absent column, turning the whole catalog's candles into nulls with no error.
    """
    schema_names = set(DydxSecondSnapshot.schema().names)
    assert set(catalog_files._OHLC_COLUMNS) <= schema_names


def test_the_array_reader_projects_the_same_columns_it_reads() -> None:
    """`second_ohlc_arrays` names its columns literally; they must be the projected ones."""
    read_literally = {
        "ts_event",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "buy_volume",
        "sell_volume",
    }
    assert read_literally == set(catalog_files._OHLC_COLUMNS)
