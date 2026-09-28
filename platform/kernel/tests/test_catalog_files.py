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

import pyarrow.parquet as pq
import pytest

from kernel import catalog_files
from kernel.catalog_files import TopOfBook
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
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


def _book(ts_event: int, bids: list[float], asks: list[float]) -> DydxSecondSnapshot:
    """Build a book row whose sizes are each price's tenth, so every level is traceable."""
    return DydxSecondSnapshot(
        InstrumentId.from_str(_IID),
        bids,
        [p / 10 for p in bids],
        asks,
        [p / 10 for p in asks],
        0.0,
        0.0,
        0,
        0,
        ts_event,
        ts_event + NS_PER_S // 2,
    )


@pytest.fixture
def book_catalog(tmp_path: Path) -> str:
    """Written out of order across two files, with an empty bid side and an empty ask side."""
    writer = ParquetDataCatalog(str(tmp_path))
    at = [_DAY0 + k * NS_PER_S for k in range(6)]
    writer.write_data(
        [
            _book(at[3], [103.0, 102.0], [104.0, 105.0]),
            _book(at[4], [], [106.0]),
            _book(at[5], [107.0], []),
        ]
    )
    writer.write_data(
        [
            _book(at[0], [100.0, 99.0], [101.0, 102.0]),
            _book(at[1], [101.0], [102.0]),
            _book(at[2], [102.0, 101.0], [103.0]),
        ]
    )
    return str(tmp_path)


def _top(ts_event: int, bid: float, ask: float) -> TopOfBook:
    return TopOfBook(ts_event, ts_event + NS_PER_S // 2, bid, bid / 10, ask, ask / 10)


def test_query_top_of_book_returns_level_zero_sorted_by_ts_event(book_catalog: str) -> None:
    rows = catalog_files.query_top_of_book(book_catalog, _IID, _DAY0, _DAY0 + 3 * NS_PER_S)
    assert rows == [
        _top(_DAY0, 100.0, 101.0),
        _top(_DAY0 + NS_PER_S, 101.0, 102.0),
        _top(_DAY0 + 2 * NS_PER_S, 102.0, 103.0),
        _top(_DAY0 + 3 * NS_PER_S, 103.0, 104.0),
    ]


def test_query_top_of_book_window_bounds_are_inclusive_ts_event(book_catalog: str) -> None:
    lo, hi = _DAY0 + NS_PER_S, _DAY0 + 2 * NS_PER_S
    rows = catalog_files.query_top_of_book(book_catalog, _IID, lo, hi)
    assert [r.ts_event for r in rows] == [lo, hi]


def test_query_top_of_book_omits_a_row_with_an_empty_side(book_catalog: str) -> None:
    rows = catalog_files.query_top_of_book(book_catalog, _IID, _DAY0, _DAY0 + NS_PER_DAY)
    assert [r.ts_event for r in rows] == [_DAY0 + k * NS_PER_S for k in range(4)]


def test_query_top_of_book_reads_files_within_the_read_margin(book_catalog: str) -> None:
    """A row whose `ts_init` (file span) lies past the window's end is still found by `ts_event`."""
    rows = catalog_files.query_top_of_book(book_catalog, _IID, _DAY0, _DAY0)
    assert rows == [_top(_DAY0, 100.0, 101.0)]
    assert READ_SPAN_MARGIN_NS > NS_PER_S // 2  # the file's span starts 0.5 s after the window


def test_query_top_of_book_empty_window_and_unknown_instrument(book_catalog: str) -> None:
    after = _DAY0 + 10 * NS_PER_S
    assert catalog_files.query_top_of_book(book_catalog, _IID, after, after + NS_PER_DAY) == []
    assert catalog_files.query_top_of_book(book_catalog, "NOPE.DYDX", 0, 2 * _DAY0) == []


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


def _index_catalog(tmp_path: Path) -> str:
    """Two files at two precision labels; the catalog's own `query` cannot decode either."""
    writer = ParquetDataCatalog(str(tmp_path))
    iid = InstrumentId.from_str(_IID)
    writer.write_data(
        [
            IndexPriceUpdate(
                iid, Price.from_str("61090.59855"), _DAY0 + k * NS_PER_S, _DAY0 + k * NS_PER_S
            )
            for k in (2, 3)
        ]
    )
    writer.write_data(
        [
            IndexPriceUpdate(
                iid, Price.from_str("100.25"), _DAY0 + k * NS_PER_S, _DAY0 + k * NS_PER_S
            )
            for k in (0, 1)
        ]
    )
    return str(tmp_path)


def test_query_index_prices_decodes_the_exact_price_sorted_and_bounded(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    rows = catalog_files.query_index_prices(catalog, _IID, _DAY0 + NS_PER_S, _DAY0 + 2 * NS_PER_S)
    assert [(r.ts_event - _DAY0, str(r.price), r.price.precision) for r in rows] == [
        (NS_PER_S, "100.25", 2),
        (2 * NS_PER_S, "61090.59855", 5),
    ]


def test_query_index_prices_unknown_instrument_is_empty(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    assert catalog_files.query_index_prices(catalog, "BTC-USD-PERP.DYDX", 0, 1 << 62) == []


def test_the_catalog_itself_still_cannot_decode_index_prices(tmp_path: Path) -> None:
    """The reason `query_index_prices` exists: drop it once this upstream gap closes."""
    catalog = ParquetDataCatalog(_index_catalog(tmp_path))
    with pytest.raises(NotImplementedError):
        catalog.query(IndexPriceUpdate, identifiers=[_IID], start=_DAY0, end=_DAY0 + NS_PER_DAY)


def test_query_index_prices_refuses_a_file_without_its_precision_label(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    path = sorted((tmp_path / "data" / "index_price_update" / _IID).glob("*.parquet"))[0]
    pq.write_table(pq.read_table(path).replace_schema_metadata(None), path)
    with pytest.raises(ValueError, match="price_precision"):
        catalog_files.query_index_prices(catalog, _IID, 0, 1 << 62)


def test_query_index_prices_refuses_a_raw_value_finer_than_its_precision_label(
    tmp_path: Path,
) -> None:
    catalog = _index_catalog(tmp_path)
    for path in (tmp_path / "data" / "index_price_update" / _IID).glob("*.parquet"):
        table = pq.read_table(path)
        pq.write_table(table.replace_schema_metadata({b"price_precision": b"2"}), path)
    with pytest.raises(ValueError, match="more digits than"):
        catalog_files.query_index_prices(catalog, _IID, 0, 1 << 62)


def test_query_index_prices_refuses_a_precision_label_this_build_cannot_hold(
    tmp_path: Path,
) -> None:
    catalog = _index_catalog(tmp_path)
    path = sorted((tmp_path / "data" / "index_price_update" / _IID).glob("*.parquet"))[0]
    table = pq.read_table(path)
    pq.write_table(table.replace_schema_metadata({b"price_precision": b"99"}), path)
    with pytest.raises(ValueError, match="outside this build"):
        catalog_files.query_index_prices(catalog, _IID, 0, 1 << 62)
