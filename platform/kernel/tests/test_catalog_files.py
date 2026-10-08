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
import glob
import math
import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kernel import catalog_files
from kernel.catalog_files import TopOfBook
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import LegacySnapshotLayoutError
from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import price_of
from kernel.second_snapshot import quantity_of
from kernel.tests.snapshot_factory import make_snapshot
from kernel.tests.snapshot_factory import units
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename


_IID = "ETH-USD-PERP.DYDX"
_DAY0 = 20_000 * NS_PER_DAY


def _snapshot(ts_event: int, ts_init: int, close: float | None) -> DydxSecondSnapshot:
    return make_snapshot(
        _IID,
        bid_prices=[1.0],
        bid_sizes=[1.0],
        ask_prices=[2.0],
        ask_sizes=[1.0],
        buy_volume=1.0 if close else 0.0,
        sell_volume=0.5 if close else 0.0,
        buy_count=1 if close else 0,
        sell_count=1 if close else 0,
        ts_event=ts_event,
        ts_init=ts_init,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
    )


@pytest.fixture
def catalog(tmp_path: Path) -> str:
    return _snapshot_catalog(tmp_path)


def _snapshot_catalog(tmp_path: Path) -> str:
    """Four files: day 0 (two of one row each), one straddling midnight, day 1 far later."""
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
        SecondOHLC(
            _DAY0 + 10 * NS_PER_S,
            10.0,
            10.0,
            10.0,
            10.0,
            1.0,
            0.5,
            4,
            4,
            100_000,
            10_000,
            5_000,
            1,
            1,
        ),
        SecondOHLC(_DAY0 + 20 * NS_PER_S, None, None, None, None, 0.0, 0.0, 4, 4, None, 0, 0, 0, 0),
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


def test_second_ohlc_arrays_carry_the_integer_flow_columns(catalog: str) -> None:
    """Story 33.3: the units as int64, a no-trade close read as 0, never through a float."""
    cols = catalog_files.second_ohlc_arrays(sorted(catalog_files.snapshot_files(catalog, _IID)))
    assert cols["close_units"].dtype == np.int64
    assert cols["close_units"].tolist() == [100_000, 0, 110_000, 120_000, 130_000]
    assert cols["buy_units"].tolist() == [10_000, 0, 10_000, 10_000, 10_000]
    assert cols["sell_units"].tolist() == [5_000, 0, 5_000, 5_000, 5_000]
    assert cols["buy_n"].tolist() == [1, 0, 1, 1, 1]
    assert cols["sell_n"].tolist() == [1, 0, 1, 1, 1]
    assert cols["price_precision"].tolist() == [4] * 5
    assert cols["size_precision"].tolist() == [4] * 5
    empty = catalog_files.second_ohlc_arrays([])
    assert all(len(empty[k]) == 0 for k in ("close_units", "buy_n", "size_precision"))


def _book(ts_event: int, bids: list[float], asks: list[float]) -> DydxSecondSnapshot:
    """Build a book row whose sizes are each price's tenth, so every level is traceable."""
    return make_snapshot(
        _IID,
        bid_prices=bids,
        bid_sizes=[p / 10 for p in bids],
        ask_prices=asks,
        ask_sizes=[p / 10 for p in asks],
        ts_event=ts_event,
        ts_init=ts_event + NS_PER_S // 2,
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
    """Return the exact level 0 of a `_book` row (default factory precisions: 4 and 4)."""
    return TopOfBook(
        ts_event,
        ts_event + NS_PER_S // 2,
        price_of(units(bid, 4), 4),
        quantity_of(units(bid / 10, 4), 4),
        price_of(units(ask, 4), 4),
        quantity_of(units(ask / 10, 4), 4),
    )


def test_top_of_book_values_are_exact_at_the_stored_precision(book_catalog: str) -> None:
    (top,) = catalog_files.query_top_of_book(book_catalog, _IID, _DAY0, _DAY0)
    assert (str(top.bid_price), str(top.bid_size)) == ("100.0000", "10.0000")
    assert top.ask_price.precision == 4


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


# -- Story 33.6: the two-pass per-bar book read ------------------------------------------------


def test_query_snapshot_times_lists_every_row_ascending_across_files(book_catalog: str) -> None:
    times = catalog_files.query_snapshot_times(book_catalog, _IID, _DAY0, _DAY0 + NS_PER_DAY)
    assert times.dtype == np.int64
    assert times.tolist() == [_DAY0 + k * NS_PER_S for k in range(6)]


def test_query_snapshot_times_bounds_are_inclusive_ts_event(book_catalog: str) -> None:
    lo, hi = _DAY0 + NS_PER_S, _DAY0 + 2 * NS_PER_S
    assert catalog_files.query_snapshot_times(book_catalog, _IID, lo, hi).tolist() == [lo, hi]


def test_query_snapshot_times_empty_window_and_unknown_instrument(book_catalog: str) -> None:
    after = _DAY0 + 10 * NS_PER_S
    assert catalog_files.query_snapshot_times(book_catalog, _IID, after, after + NS_PER_S).size == 0
    assert catalog_files.query_snapshot_times(book_catalog, "NOPE.DYDX", 0, 2 * _DAY0).size == 0


def test_query_books_at_decodes_only_the_requested_rows(book_catalog: str) -> None:
    """Gap-encoded prices come back absolute, best first, at the stored precisions (4 and 4)."""
    at = [_DAY0 + k * NS_PER_S for k in range(6)]
    books = catalog_files.query_books_at(book_catalog, _IID, [at[3], at[0], at[4]])
    assert sorted(books) == [at[0], at[3], at[4]]
    assert books[at[3]] == {
        "ts_event": at[3],
        "price_precision": 4,
        "size_precision": 4,
        "bid_prices": [103.0, 102.0],
        "bid_sizes": [10.3, 10.2],
        "ask_prices": [104.0, 105.0],
        "ask_sizes": [10.4, 10.5],
    }
    assert (books[at[4]]["bid_prices"], books[at[4]]["ask_prices"]) == ([], [106.0])


def test_query_books_at_omits_a_stamp_no_row_carries(book_catalog: str) -> None:
    missing = _DAY0 + NS_PER_S // 3
    assert catalog_files.query_books_at(book_catalog, _IID, [missing]) == {}
    assert catalog_files.query_books_at(book_catalog, _IID, []) == {}


def test_module_never_writes_or_builds_a_catalog() -> None:
    """AD-D3: read helpers only -- no `ParquetDataCatalog`, no write/rename/delete call."""
    tree = ast.parse(Path(catalog_files.__file__).read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "ParquetDataCatalog" not in names | attrs
    forbidden = {"write_table", "write_data", "rename", "replace", "unlink", "remove", "rmtree"}
    assert attrs & forbidden == set()


def test_the_projected_columns_all_exist_in_the_snapshot_schema() -> None:
    """A projected column the files do not hold would fail every read: they must all exist."""
    schema_names = set(DydxSecondSnapshot.schema().names)
    assert set(catalog_files._TRADE_READ_COLUMNS) <= schema_names


def _legacy_file(tmp_path: Path) -> Path:
    """Write a float-layout (pre-30.2) snapshot file: the old schema, no precision columns."""
    leaf = tmp_path / "data" / catalog_files.SNAPSHOT_DIRNAME / _IID
    leaf.mkdir(parents=True)
    path = leaf / _timestamps_to_filename(_DAY0, _DAY0)
    columns = {
        "instrument_id": pa.array([_IID]).dictionary_encode(),
        "bid_prices": pa.array([[1.0]], pa.list_(pa.float64())),
        "ts_event": pa.array([_DAY0], pa.uint64()),
        "ts_init": pa.array([_DAY0], pa.uint64()),
        "open_price": pa.array([1.5]),
        "buy_volume": pa.array([0.0]),
    }
    pq.write_table(pa.table(columns), path)
    return path


@pytest.mark.parametrize(
    "read",
    [
        lambda root: catalog_files.query_second_ohlc(root, _IID, 0, 2 * _DAY0),
        lambda root: catalog_files.query_top_of_book(root, _IID, 0, 2 * _DAY0),
        lambda root: catalog_files.query_snapshot_times(root, _IID, 0, 2 * _DAY0),
        lambda root: catalog_files.query_books_at(root, _IID, [_DAY0]),
        lambda root: catalog_files.second_ohlc_arrays(catalog_files.snapshot_files(root, _IID)),
    ],
)
def test_every_projected_reader_refuses_a_float_layout_file(tmp_path: Path, read: object) -> None:
    path = _legacy_file(tmp_path)
    with pytest.raises(LegacySnapshotLayoutError, match="migrate_snapshot_ints") as raised:
        read(str(tmp_path))  # type: ignore[operator]
    assert str(path) in str(raised.value)


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


def test_query_price_columns_reads_the_exact_units_at_each_files_label(tmp_path: Path) -> None:
    """
    The two files carry labels 2 and 5: 100.25 is 10 025 units of 0.01 and 61090.59855 is
    6 109 059 855 units of 0.00001 (the value `Price(Decimal, 16)` mis-stamps), inclusive bounds.
    """
    catalog = _index_catalog(tmp_path)
    rows = catalog_files.query_price_columns(
        catalog, catalog_files.INDEX_PRICE_DIRNAME, _IID, _DAY0 + NS_PER_S, _DAY0 + 2 * NS_PER_S
    )
    assert (rows.ts_event - _DAY0).tolist() == [NS_PER_S, 2 * NS_PER_S]
    assert rows.units.tolist() == [10_025, 6_109_059_855]
    assert rows.precision.tolist() == [2, 5]


def test_query_price_columns_keeps_a_copy_once_and_refuses_a_disagreeing_one(
    tmp_path: Path,
) -> None:
    """A row in two files: one row when the copies agree (100.25 and 100.250 agree), else refused."""
    iid = InstrumentId.from_str(_IID)
    writer = ParquetDataCatalog(str(tmp_path))
    writer.write_data([IndexPriceUpdate(iid, Price.from_str("100.25"), _DAY0, _DAY0)])
    writer.write_data(
        [
            IndexPriceUpdate(iid, Price.from_str("100.250"), _DAY0, _DAY0),
            IndexPriceUpdate(iid, Price.from_str("100.300"), _DAY0 + NS_PER_S, _DAY0 + NS_PER_S),
        ],
        skip_disjoint_check=True,
    )
    dirname = catalog_files.INDEX_PRICE_DIRNAME
    rows = catalog_files.query_price_columns(str(tmp_path), dirname, _IID, 0, 1 << 62)
    assert (rows.units.tolist(), rows.precision.tolist()) == ([10_025, 100_300], [2, 3])
    late = _DAY0 + 2 * NS_PER_S  # a third file name, so the write is not skipped
    writer.write_data(
        [
            IndexPriceUpdate(iid, Price.from_str("100.26"), _DAY0, _DAY0),
            IndexPriceUpdate(iid, Price.from_str("100.30"), late, late),
        ],
        skip_disjoint_check=True,
    )
    with pytest.raises(ValueError, match="stored twice with different values"):
        catalog_files.query_price_columns(str(tmp_path), dirname, _IID, 0, 1 << 62)


def test_query_price_columns_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    rows = catalog_files.query_price_columns(
        str(tmp_path), catalog_files.MARK_PRICE_DIRNAME, _IID, 0, 1 << 62
    )
    assert [len(column) for column in rows] == [0, 0, 0, 0]


def test_newest_ts_event_before_is_the_newest_row_strictly_before_the_bound(
    tmp_path: Path,
) -> None:
    """Rows at seconds 0..3 over two files: before second 3 -> 2, before 0 -> None."""
    catalog = _index_catalog(tmp_path)
    dirnames = (catalog_files.INDEX_PRICE_DIRNAME, catalog_files.MARK_PRICE_DIRNAME)
    newest = catalog_files.newest_ts_event_before(catalog, _IID, dirnames, _DAY0 + 3 * NS_PER_S)
    assert newest == _DAY0 + 2 * NS_PER_S
    assert catalog_files.newest_ts_event_before(catalog, _IID, dirnames, _DAY0) is None


def _losing_listing(monkeypatch: pytest.MonkeyPatch, losing: int) -> None:
    """Make the first `losing` listings also name a file the consolidation already removed."""
    real = glob.glob
    calls = {"n": 0}

    def listing(pattern: str) -> list[str]:
        paths = real(pattern)
        calls["n"] += 1
        if calls["n"] > losing or not paths:
            return paths
        return [str(Path(paths[0]).parent / "removed" / Path(paths[0]).name), *paths]

    monkeypatch.setattr(catalog_files.glob, "glob", listing)


def test_the_derivatives_readers_list_again_after_a_consolidation_removed_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One listing names a file gone by the read: the read lists again and returns every row."""
    catalog = _index_catalog(tmp_path)
    dirname = catalog_files.INDEX_PRICE_DIRNAME
    whole = catalog_files.query_price_columns(catalog, dirname, _IID, 0, 1 << 62)
    _losing_listing(monkeypatch, 1)
    rows = catalog_files.query_price_columns(catalog, dirname, _IID, 0, 1 << 62)
    assert rows.units.tolist() == whole.units.tolist()
    assert rows.ts_event.tolist() == whole.ts_event.tolist()
    _losing_listing(monkeypatch, 1)
    newest = catalog_files.newest_ts_event_before(catalog, _IID, (dirname,), _DAY0 + 3 * NS_PER_S)
    assert newest == _DAY0 + 2 * NS_PER_S


def test_a_derivatives_listing_that_keeps_losing_files_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _index_catalog(tmp_path)
    _losing_listing(monkeypatch, 1 << 30)
    with pytest.raises(ValueError, match="kept disappearing"):
        catalog_files.query_price_columns(
            catalog, catalog_files.INDEX_PRICE_DIRNAME, _IID, 0, 1 << 62
        )


def test_price_precision_labels_read_each_overlapping_files_label(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    labels = catalog_files.price_precision_labels(catalog, IndexPriceUpdate, _IID, 0, 1 << 62)
    assert {label.price_precision for label in labels} == {2, 5}


def test_price_precision_labels_skip_files_outside_the_window(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    far = _DAY0 + NS_PER_DAY
    labels = catalog_files.price_precision_labels(catalog, IndexPriceUpdate, _IID, far, far + 1)
    assert labels == []


def test_price_precision_labels_report_a_missing_label_as_none(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    path = sorted((tmp_path / "data" / "index_price_update" / _IID).glob("*.parquet"))[0]
    pq.write_table(pq.read_table(path).replace_schema_metadata(None), path)
    labels = catalog_files.price_precision_labels(catalog, IndexPriceUpdate, _IID, 0, 1 << 62)
    assert None in {label.price_precision for label in labels}


def test_price_precision_labels_name_the_file_of_a_malformed_label(tmp_path: Path) -> None:
    catalog = _index_catalog(tmp_path)
    path = sorted((tmp_path / "data" / "index_price_update" / _IID).glob("*.parquet"))[0]
    table = pq.read_table(path)
    pq.write_table(table.replace_schema_metadata({b"price_precision": b"x"}), path)
    with pytest.raises(ValueError, match=path.name):
        catalog_files.price_precision_labels(catalog, IndexPriceUpdate, _IID, 0, 1 << 62)


# -- raw trades (Story 32.8) -------------------------------------------------------------------------


def _tick(
    price: str, size: str, side: AggressorSide, trade_id: str, ts_event: int, ts_init: int
) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str(price),
        Quantity.from_str(size),
        side,
        TradeId(trade_id),
        ts_event,
        ts_init,
    )


def _write_trades(root: Path | str, ticks: list[TradeTick]) -> str:
    ParquetDataCatalog(str(root)).write_data(ticks)
    return str(root)


def _trade_rows(columns: catalog_files.TradeColumns) -> list[tuple[int, int, bool, int]]:
    return [(int(p), int(s), bool(b), int(t) - _DAY0) for p, s, b, t in zip(*columns, strict=True)]


def test_trade_dir_is_what_the_catalog_writes(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("1.5", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    assert catalog_files.TRADE_DIRNAME == "trade_tick"
    assert len(catalog_files.trade_files(root, _IID)) == 1


def test_trade_units_are_exact_at_precision_2(tmp_path: Path) -> None:
    root = _write_trades(
        tmp_path,
        [
            _tick("100.25", "0.003", AggressorSide.BUYER, "a", _DAY0, _DAY0),
            _tick("99.50", "1.000", AggressorSide.SELLER, "b", _DAY0 + 1, _DAY0 + 1),
            _tick("99.00", "0.500", AggressorSide.NO_AGGRESSOR, "c", _DAY0 + 2, _DAY0 + 2),
        ],
    )
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_S, 2, 3)
    assert _trade_rows(columns) == [
        (10025, 3, True, 0),
        (9950, 1000, False, 1),
        (9900, 500, False, 2),
    ]
    assert columns.price.dtype == np.int64
    assert columns.size.dtype == np.int64


def test_trade_units_are_exact_at_precision_6(tmp_path: Path) -> None:
    root = _write_trades(
        tmp_path, [_tick("0.123457", "1234.5", AggressorSide.SELLER, "a", _DAY0, _DAY0)]
    )
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 6, 1)
    assert _trade_rows(columns) == [(123457, 12345, False, 0)]


def test_trade_units_take_the_requested_precision_not_the_files_label(tmp_path: Path) -> None:
    """A file labelled 1 decimal read at the definition's 4: one grid for mixed-label history."""
    root = _write_trades(tmp_path, [_tick("100.5", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 4, 2)
    assert _trade_rows(columns) == [(1_005_000, 200, True, 0)]


def test_a_raw_finer_than_the_precision_is_refused_never_rounded(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("100.25", "1", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    with pytest.raises(catalog_files.TradeDecodeError, match="column price is not exact"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 1, 0)


def test_units_outside_int64_are_refused(tmp_path: Path) -> None:
    root = _write_trades(
        tmp_path, [_tick("1", "1000000000.000000", AggressorSide.BUYER, "a", _DAY0, _DAY0)]
    )
    with pytest.raises(catalog_files.TradeDecodeError, match="column size"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 16)


def test_the_trade_window_is_half_open_on_ts_event(tmp_path: Path) -> None:
    root = _write_trades(
        tmp_path,
        [
            _tick("1", "1", AggressorSide.BUYER, str(k), _DAY0 + k * NS_PER_S, _DAY0 + k * NS_PER_S)
            for k in range(4)
        ],
    )
    columns = catalog_files.query_trade_columns(
        root, _IID, _DAY0 + NS_PER_S, _DAY0 + 3 * NS_PER_S, 0, 0
    )
    assert [int(t) - _DAY0 for t in columns.ts_event] == [NS_PER_S, 2 * NS_PER_S]


def test_a_file_starting_after_the_window_within_the_skew_margin_is_read(tmp_path: Path) -> None:
    late = _DAY0 + 200 * NS_PER_S  # arrived 200 s after its venue time (a backfill)
    root = _write_trades(tmp_path, [_tick("1", "1", AggressorSide.BUYER, "a", _DAY0, late)])
    assert MAX_TS_INIT_SKEW_NS > 200 * NS_PER_S
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_S, 0, 0)
    assert len(columns.ts_event) == 1


def test_a_file_beyond_the_skew_margin_is_not_opened(tmp_path: Path) -> None:
    far = _DAY0 + MAX_TS_INIT_SKEW_NS + 2 * NS_PER_S
    root = _write_trades(tmp_path, [_tick("1", "1", AggressorSide.BUYER, "a", _DAY0, far)])
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_S, 0, 0)
    assert len(columns.ts_event) == 0


def test_a_replayed_trade_is_counted_once(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("1", "3", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    replay = _DAY0 + 5 * NS_PER_S  # the same trade again after a restart, in a later file
    _write_trades(
        root,
        [
            _tick("1", "3", AggressorSide.BUYER, "a", _DAY0, replay),
            _tick("1", "4", AggressorSide.SELLER, "b", _DAY0, replay),
        ],
    )
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_S, 0, 0)
    assert sorted(_trade_rows(columns)) == [(1, 3, True, 0), (1, 4, False, 0)]


def test_two_copies_of_a_trade_that_disagree_are_refused(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("1", "3", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    replay = _DAY0 + 5 * NS_PER_S
    _write_trades(root, [_tick("1", "4", AggressorSide.BUYER, "a", _DAY0, replay)])
    with pytest.raises(catalog_files.TradeDecodeError, match="'a' is stored twice"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_S, 0, 0)


def test_a_consolidated_day_file_and_minute_files_are_read_together(tmp_path: Path) -> None:
    """A day file beside a minute file (one row shared, as before a consolidation's cleanup)."""
    day = [
        _tick("2", "1", AggressorSide.BUYER, str(k), _DAY0 + k * NS_PER_S, _DAY0 + k * NS_PER_S)
        for k in (0, 60, 3_600)
    ]
    root = _write_trades(tmp_path / "root", day)
    minute = [
        day[1],
        _tick("3", "1", AggressorSide.SELLER, "m", _DAY0 + 61 * NS_PER_S, _DAY0 + 61 * NS_PER_S),
    ]
    other = _write_trades(tmp_path / "other", minute)  # its own catalog: the spans overlap
    (path,) = catalog_files.trade_files(other, _IID)
    leaf = Path(catalog_files.trade_files(root, _IID)[0]).parent
    shutil.copy(path, leaf / Path(path).name)
    assert len(catalog_files.trade_files(root, _IID)) == 2
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_DAY, 0, 0)
    assert [
        (int(p), int(t - _DAY0) // NS_PER_S)
        for p, t in zip(columns.price, columns.ts_event, strict=True)
    ] == [
        (2, 0),
        (2, 60),
        (3, 61),
        (2, 3_600),
    ]


def test_a_trade_file_without_its_precision_labels_is_refused(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("1", "1", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    (path,) = catalog_files.trade_files(root, _IID)
    table = pq.read_table(path)
    pq.write_table(table.replace_schema_metadata({}), path)
    with pytest.raises(catalog_files.TradeDecodeError, match="price_precision, size_precision"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0)


def test_no_trades_is_an_empty_read(tmp_path: Path) -> None:
    columns = catalog_files.query_trade_columns(str(tmp_path), _IID, _DAY0, _DAY0 + 1, 2, 3)
    assert [len(c) for c in columns] == [0, 0, 0, 0]


def test_a_file_removed_by_a_consolidation_mid_read_is_listed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The consolidation wrote the day file and removed its minute source after the listing."""
    root = _write_trades(tmp_path, [_tick("1", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    (path,) = catalog_files.trade_files(root, _IID)
    gone = str(Path(path).parent / "removed" / Path(path).name)  # a span the read wants, no file
    listings = iter([[gone, path], [path]])
    monkeypatch.setattr(catalog_files, "trade_files", lambda *_: next(listings))
    columns = catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0)
    assert _trade_rows(columns) == [(1, 2, True, 0)]


def test_a_listing_that_keeps_losing_files_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_trades(tmp_path, [_tick("1", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    (path,) = catalog_files.trade_files(root, _IID)
    gone = str(Path(path).parent / "removed" / Path(path).name)  # a span the read wants, no file
    monkeypatch.setattr(catalog_files, "trade_files", lambda *_: [gone])
    with pytest.raises(catalog_files.TradeDecodeError, match="kept disappearing"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0)


def test_a_raw_width_other_than_16_bytes_is_refused(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("1", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    (path,) = catalog_files.trade_files(root, _IID)
    table = pq.read_table(path)
    narrow = pa.array([b"\x00" * 8], type=pa.binary(8))
    index = table.schema.get_field_index("price")
    pq.write_table(
        table.set_column(index, "price", narrow).replace_schema_metadata(table.schema.metadata),
        path,
    )
    with pytest.raises(catalog_files.TradeDecodeError, match="column price is fixed_size_binary"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0)


def test_copies_of_a_trade_that_disagree_only_on_seller_or_no_aggressor_are_refused(
    tmp_path: Path,
) -> None:
    """Both fold as a sell, but they are two stored versions of one trade: compared as stored."""
    root = _write_trades(tmp_path, [_tick("1", "3", AggressorSide.SELLER, "a", _DAY0, _DAY0)])
    replay = _DAY0 + 5 * NS_PER_S
    _write_trades(root, [_tick("1", "3", AggressorSide.NO_AGGRESSOR, "a", _DAY0, replay)])
    with pytest.raises(catalog_files.TradeDecodeError, match="'a' is stored twice"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + NS_PER_S, 0, 0)


def test_an_unreadable_trade_file_is_refused_naming_it(tmp_path: Path) -> None:
    root = _write_trades(tmp_path, [_tick("1", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])
    (path,) = catalog_files.trade_files(root, _IID)
    Path(path).write_bytes(Path(path).read_bytes()[:64])  # truncated: no Parquet footer
    with pytest.raises(catalog_files.TradeDecodeError, match="unreadable, refused"):
        catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0)


# -- file faults: foreign names, vanished files, unreadable files (DW-181/183/288/289) ------------

_SNAPSHOT_WINDOW = (0, 2 * _DAY0)
_WHOLE = (0, 1 << 62)


def _trade_catalog(tmp_path: Path) -> str:
    return _write_trades(tmp_path, [_tick("1", "2", AggressorSide.BUYER, "a", _DAY0, _DAY0)])


def _leaf(root: str, dirname: str) -> Path:
    return Path(root) / "data" / dirname / _IID


def _real_files(root: str, dirname: str) -> list[str]:
    return sorted(str(p) for p in _leaf(root, dirname).glob("*.parquet"))


def _add_foreign(root: str, dirname: str) -> Path:
    """Copy a real catalog file under a name the catalog never writes: read, it would add rows."""
    path = _leaf(root, dirname) / "notes.parquet"
    shutil.copy(_real_files(root, dirname)[0], path)
    return path


def _gone(path: str) -> str:
    """Return a path the read wants (a real file's span) that no longer exists."""
    return str(Path(path).parent / "removed" / Path(path).name)


_INDEX = catalog_files.INDEX_PRICE_DIRNAME
_SNAP = catalog_files.SNAPSHOT_DIRNAME
# (builder, data directory, the listing function a reader calls, read(root, on_foreign))
_SELF_LISTING_READERS = [
    pytest.param(
        _snapshot_catalog,
        _SNAP,
        "snapshot_files",
        lambda root, hook: catalog_files.query_second_ohlc(
            root, _IID, *_SNAPSHOT_WINDOW, on_foreign=hook
        ),
        id="query_second_ohlc",
    ),
    pytest.param(
        _snapshot_catalog,
        _SNAP,
        "snapshot_files",
        lambda root, hook: catalog_files.query_top_of_book(
            root, _IID, *_SNAPSHOT_WINDOW, on_foreign=hook
        ),
        id="query_top_of_book",
    ),
    pytest.param(
        _index_catalog,
        _INDEX,
        "_leaf_files",
        lambda root, hook: catalog_files.query_index_prices(root, _IID, *_WHOLE, on_foreign=hook),
        id="query_index_prices",
    ),
    pytest.param(
        _index_catalog,
        _INDEX,
        "_leaf_files",
        lambda root, hook: catalog_files.price_precision_labels(
            root, IndexPriceUpdate, _IID, *_WHOLE, on_foreign=hook
        ),
        id="price_precision_labels",
    ),
]
# The self-listing readers plus the trade reader, which lists again too but refuses exhaustion with
# its own `TradeDecodeError` (covered by its own tests above).
_RELISTING_READERS = [
    *_SELF_LISTING_READERS,
    pytest.param(
        _trade_catalog,
        catalog_files.TRADE_DIRNAME,
        "trade_files",
        lambda root, hook: _trade_rows(
            catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0, on_foreign=hook)
        ),
        id="query_trade_columns",
    ),
]
_NAME_PARSING_READERS = [
    pytest.param(
        _snapshot_catalog,
        _SNAP,
        lambda root, hook: catalog_files.data_file_ranges(root, _IID, on_foreign=hook),
        id="data_file_ranges",
    ),
    pytest.param(
        _snapshot_catalog,
        _SNAP,
        lambda root, hook: catalog_files.files_by_day(
            root, _IID, *_SNAPSHOT_WINDOW, on_foreign=hook
        ),
        id="files_by_day",
    ),
    pytest.param(
        _trade_catalog,
        catalog_files.TRADE_DIRNAME,
        lambda root, hook: _trade_rows(
            catalog_files.query_trade_columns(root, _IID, _DAY0, _DAY0 + 1, 0, 0, on_foreign=hook)
        ),
        id="query_trade_columns",
    ),
    *(pytest.param(*p.values[:2], p.values[3], id=p.id) for p in _SELF_LISTING_READERS),
]


@pytest.mark.parametrize(("build", "dirname", "read"), _NAME_PARSING_READERS)
def test_a_foreign_file_name_is_reported_once_and_skipped(
    tmp_path: Path, build: Callable[[Path], str], dirname: str, read: Callable[..., object]
) -> None:
    root = build(tmp_path)
    expected = read(root, None)
    foreign = _add_foreign(root, dirname)
    reported: list[tuple[str, str]] = []
    assert read(root, lambda site, detail: reported.append((site, detail))) == expected
    assert len(reported) == 1
    site, detail = reported[0]
    assert site == catalog_files.FOREIGN_FILE_SITE == "catalog.foreign_file"
    assert detail.startswith(f"{foreign}: not a catalog file name (")
    assert detail.endswith("); skipped")


@pytest.mark.parametrize(("build", "dirname", "read"), _NAME_PARSING_READERS)
def test_a_foreign_file_name_without_a_hook_is_refused(
    tmp_path: Path, build: Callable[[Path], str], dirname: str, read: Callable[..., object]
) -> None:
    root = build(tmp_path)
    _add_foreign(root, dirname)
    with pytest.raises(ValueError) as raised:
        read(root, None)
    assert not isinstance(raised.value, catalog_files.CatalogReadError)


@pytest.mark.parametrize(("build", "dirname", "listing", "read"), _RELISTING_READERS)
def test_a_file_vanished_since_the_listing_is_read_again_from_a_fresh_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[Path], str],
    dirname: str,
    listing: str,
    read: Callable[..., object],
) -> None:
    """The consolidation wrote the day file and removed a minute source after the listing."""
    root = build(tmp_path)
    expected = read(root, None)
    real = _real_files(root, dirname)
    listings = iter([[_gone(real[0]), *real], real])
    monkeypatch.setattr(catalog_files, listing, lambda *_: next(listings))
    assert read(root, None) == expected


@pytest.mark.parametrize(("build", "dirname", "listing", "read"), _RELISTING_READERS)
def test_a_foreign_file_met_by_both_listings_of_one_read_is_reported_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[Path], str],
    dirname: str,
    listing: str,
    read: Callable[..., object],
) -> None:
    root = build(tmp_path)
    expected = read(root, None)
    real = _real_files(root, dirname)
    foreign = str(_add_foreign(root, dirname))
    listings = iter([[_gone(real[0]), foreign, *real], [foreign, *real]])
    monkeypatch.setattr(catalog_files, listing, lambda *_: next(listings))
    reported: list[tuple[str, str]] = []
    assert read(root, lambda site, detail: reported.append((site, detail))) == expected
    assert [detail.split(":")[0] for _, detail in reported] == [foreign]


@pytest.mark.parametrize(("build", "dirname", "listing", "read"), _SELF_LISTING_READERS)
def test_a_listing_that_keeps_losing_files_is_refused_naming_the_instrument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[Path], str],
    dirname: str,
    listing: str,
    read: Callable[..., object],
) -> None:
    root = build(tmp_path)
    gone = [_gone(path) for path in _real_files(root, dirname)]
    monkeypatch.setattr(catalog_files, listing, lambda *_: gone)
    with pytest.raises(catalog_files.CatalogReadError, match="kept disappearing") as raised:
        read(root, None)
    assert f"{_IID}:" in str(raised.value)
    assert f"{catalog_files._LISTING_ATTEMPTS} listings" in str(raised.value)


_SNAPSHOT_READERS = [p for p in _SELF_LISTING_READERS if p.values[1] == _SNAP]


@pytest.mark.parametrize(("build", "dirname", "listing", "read"), _SNAPSHOT_READERS)
def test_a_relist_meeting_the_day_file_beside_its_sources_returns_each_second_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[Path], str],
    dirname: str,
    listing: str,
    read: Callable[..., object],
) -> None:
    """The relist lands after the day file's rename, before its sources are all removed."""
    root = build(tmp_path)
    expected = read(root, None)
    real = _real_files(root, dirname)
    day_file = str(_leaf(root, dirname) / _timestamps_to_filename(_DAY0, _DAY0 + NS_PER_DAY - 1))
    shutil.copy(real[0], day_file)  # the merged copy of the first source's rows
    listings = iter([[_gone(real[0]), *real], [day_file, *real]])
    monkeypatch.setattr(catalog_files, listing, lambda *_: next(listings))
    assert read(root, None) == expected


@pytest.mark.parametrize(("build", "dirname", "listing", "read"), _SNAPSHOT_READERS)
def test_a_second_stored_twice_with_different_values_is_refused(
    tmp_path: Path,
    build: Callable[[Path], str],
    dirname: str,
    listing: str,
    read: Callable[..., object],
) -> None:
    root = build(tmp_path)
    second = _DAY0 + 10 * NS_PER_S
    ParquetDataCatalog(root).write_data([_snapshot(second, second + 2 * NS_PER_S, 99.0)])
    with pytest.raises(catalog_files.CatalogReadError, match="stored twice") as raised:
        read(root, None)
    assert str(raised.value).startswith(f"{_IID}: second {second} ")


def _truncate(path: str) -> None:
    Path(path).write_bytes(Path(path).read_bytes()[:64])  # no Parquet footer


@pytest.mark.parametrize(("build", "dirname", "listing", "read"), _SELF_LISTING_READERS)
def test_an_unreadable_file_is_refused_naming_it(
    tmp_path: Path,
    build: Callable[[Path], str],
    dirname: str,
    listing: str,
    read: Callable[..., object],
) -> None:
    root = build(tmp_path)
    path = _real_files(root, dirname)[0]
    _truncate(path)
    with pytest.raises(catalog_files.CatalogReadError, match="unreadable, refused") as raised:
        read(root, None)
    assert str(raised.value).startswith(f"{path}: ")


def test_second_ohlc_arrays_refuses_an_unreadable_file_naming_it(catalog: str) -> None:
    paths = _real_files(catalog, _SNAP)
    _truncate(paths[1])
    with pytest.raises(catalog_files.CatalogReadError, match="unreadable, refused") as raised:
        catalog_files.second_ohlc_arrays(paths)
    assert str(raised.value).startswith(f"{paths[1]}: ")


def test_second_ohlc_arrays_refuses_a_vanished_file_never_reads_partially(catalog: str) -> None:
    """The caller's list is stale: it writes candles, so it must refuse, never drop the file."""
    paths = _real_files(catalog, _SNAP)
    gone = _gone(paths[0])
    with pytest.raises(catalog_files.CatalogReadError, match="gone before it was read") as raised:
        catalog_files.second_ohlc_arrays([gone, *paths[1:]])
    assert str(raised.value).startswith(f"{gone}: ")


def test_second_ohlc_arrays_reads_a_day_file_beside_its_source_once(catalog: str) -> None:
    """The rebuild's read keeps one copy per second, as every snapshot reader does."""
    real = _real_files(catalog, _SNAP)
    expected = catalog_files.second_ohlc_arrays(real)
    day_file = str(_leaf(catalog, _SNAP) / _timestamps_to_filename(_DAY0, _DAY0 + NS_PER_DAY - 1))
    shutil.copy(real[0], day_file)  # the merged copy of the first source's rows
    cols = catalog_files.second_ohlc_arrays([day_file, *real])
    assert set(cols) == set(expected)
    for key, column in expected.items():
        np.testing.assert_array_equal(cols[key], column, err_msg=key)


def test_second_ohlc_arrays_refuses_a_second_stored_twice_with_different_values(
    catalog: str,
) -> None:
    second = _DAY0 + 10 * NS_PER_S
    ParquetDataCatalog(catalog).write_data([_snapshot(second, second + 2 * NS_PER_S, 99.0)])
    with pytest.raises(catalog_files.CatalogReadError, match="stored twice") as raised:
        catalog_files.second_ohlc_arrays(_real_files(catalog, _SNAP))
    assert str(raised.value).startswith(f"second {second} ")


@pytest.mark.parametrize("column", ["buy_volume", "sell_volume", "buy_count", "sell_count"])
def test_second_ohlc_arrays_refuses_a_null_flow_column(
    catalog: str, tmp_path: Path, column: str
) -> None:
    """
    A null volume, count or precision is never read as 0 (no fabricated "nothing traded"): the
    rebuild read raises naming the file and the column. Only the close may be null (no trade).
    """
    source = sorted(catalog_files.snapshot_files(catalog, _IID))[0]
    table = pq.read_table(source)
    index = table.schema.get_field_index(column)
    field = table.schema.field(index).with_nullable(True)
    nulls = pa.nulls(table.num_rows, field.type)
    broken = tmp_path / Path(source).name
    pq.write_table(table.set_column(index, field, nulls), broken)
    with pytest.raises(ValueError, match=f"null value.*{column!r}") as raised:
        catalog_files.second_ohlc_arrays([str(broken)])
    assert str(broken) in str(raised.value)


@pytest.mark.parametrize(
    "column",
    ["buy_volume", "sell_volume", "buy_count", "sell_count", "price_precision", "size_precision"],
)
def test_the_second_ohlc_rows_refuse_a_null_flow_column(
    catalog: str, tmp_path: Path, column: str
) -> None:
    """`query_second_ohlc`'s per-file read refuses the same nulls, naming the file and column."""
    source = sorted(catalog_files.snapshot_files(catalog, _IID))[0]
    table = pq.read_table(source)
    index = table.schema.get_field_index(column)
    field = table.schema.field(index).with_nullable(True)
    broken = tmp_path / Path(source).name
    pq.write_table(table.set_column(index, field, pa.nulls(table.num_rows, field.type)), broken)
    with pytest.raises(ValueError, match=f"null value.*{column!r}") as raised:
        catalog_files._ohlc_rows(str(broken), 0, 1 << 62)
    assert str(broken) in str(raised.value)


def _liquidation_at(ts_event: int, ts_init: int, key: str) -> Liquidation:
    iid = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
    return Liquidation.from_wire_text(
        iid, LiquidatedSide.LONG, "0.001", "84000.00", (2, 3), key, ts_event, ts_init
    )


def test_liquidation_feed_since_is_none_without_an_archived_liquidation(tmp_path: Path) -> None:
    assert catalog_files.liquidation_feed_since_ns(str(tmp_path), "BTCUSDT-LINEAR.BYBIT") is None


def test_liquidation_feed_since_is_the_earliest_ts_event_across_files(tmp_path: Path) -> None:
    """
    Three files: the first (by name span) starts at ts_init T+100 s and holds an event at T+90 s;
    the second's span starts at T+200 s, within the 300 s skew bound of T+90 s, and holds an event
    at T+10 s (its ts_init trails by 190 s): the earliest, T+10 s. The third starts at T+1,000 s,
    past T+10 s + 300 s, so no row of it can be earlier: it is never opened (made unreadable).
    """
    t = _DAY0
    writer = ParquetDataCatalog(str(tmp_path))
    writer.write_data([_liquidation_at(t + 90 * NS_PER_S, t + 100 * NS_PER_S, "a")])
    writer.write_data([_liquidation_at(t + 10 * NS_PER_S, t + 200 * NS_PER_S, "b")])
    writer.write_data([_liquidation_at(t + 900 * NS_PER_S, t + 1_000 * NS_PER_S, "c")])
    directory = tmp_path / "data" / "custom_liquidation" / "BTCUSDT-LINEAR.BYBIT"
    files = sorted(directory.glob("*.parquet"))
    assert len(files) == 3
    files[-1].write_bytes(b"not parquet")  # opened only if the walk failed to stop
    since = catalog_files.liquidation_feed_since_ns(str(tmp_path), "BTCUSDT-LINEAR.BYBIT")
    assert since == t + 10 * NS_PER_S
    assert MAX_TS_INIT_SKEW_NS == 300 * NS_PER_S
