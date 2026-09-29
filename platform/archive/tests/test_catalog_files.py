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
`CatalogFiles`, the one rewriter and deleter: a rewrite keeps the schema metadata and row order and
writes zstd, a failed verification leaves the original intact, the open UTC day is refused, stale
temp files are cleaned -- and the `CatalogWriter` port contract it meets. Real catalog files.

Story 30.1: every catalog data type round-trips value-identical through the compact write
settings, reads back identically through `ParquetDataCatalog` and the kernel's projected readers,
and splits a day over the row-group cap into groups that statistics still prune.
"""

import json
import os
import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest
from kernel import catalog_files as kernel_files
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot

from archive.application.ports import CatalogWriter
from archive.application.ports import MergeScope
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteMode
from archive.application.ports import RewriteVerifyError
from archive.infrastructure import catalog_files
from archive.infrastructure import compact_parquet
from archive.infrastructure.catalog_files import TMP_SUFFIX
from archive.infrastructure.catalog_files import CatalogFiles
from archive.infrastructure.catalog_files import encoded_size
from archive.infrastructure.catalog_files import same_schema
from archive.infrastructure.compact_parquet import COMPACT_ZSTD_LEVEL
from archive.infrastructure.compact_parquet import compact_write_options
from archive.infrastructure.compact_parquet import is_compact
from archive.infrastructure.compact_parquet import timestamp_columns
from archive.infrastructure.maintenance_lock import maintenance
from archive.tests import catalog_fixture
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import InstrumentStatus
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_SEC = 1_000_000_000
_DAY_NS = 86_400 * _SEC
_DAY0 = 20_000  # a UTC day long ago
_IID = "ETHUSDT-LINEAR.BYBIT"


def _leaf(tmp_path: Path, day: int = _DAY0, rows: int = 5) -> Path:
    """One real `MarkPriceUpdate` file (its Arrow metadata carries `price_precision`)."""
    catalog = ParquetDataCatalog(str(tmp_path))
    t0 = day * _DAY_NS + 3_600 * _SEC
    catalog.write_data(
        [
            MarkPriceUpdate(
                InstrumentId.from_str(_IID),
                Price(Decimal(f"{2000 + i}.25"), 2),
                t0 + (rows - i) * _SEC,  # written newest first: a sort would show
                t0 + i * _SEC,
            )
            for i in range(rows)
        ]
    )
    (path,) = (tmp_path / "data" / "mark_price_update" / _IID).glob("*.parquet")
    return path


def _later(day: int = _DAY0 + 10) -> CatalogFiles:
    return CatalogFiles(lambda: day * _DAY_NS)


def test_a_rewrite_keeps_schema_metadata_and_row_order_and_writes_zstd(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    before = pq.read_table(path)
    assert b"price_precision" in before.schema.metadata
    _later().rewrite(path, before)
    after = pq.read_table(path)
    assert after.schema.equals(before.schema, check_metadata=True)
    assert after.column("ts_event").to_pylist() == before.column("ts_event").to_pylist()
    assert after.equals(before)
    assert pq.read_metadata(path).row_group(0).column(0).compression == "ZSTD"
    assert not list(path.parent.glob("*" + TMP_SUFFIX))


def test_a_failed_verification_leaves_the_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _leaf(tmp_path)
    before = path.read_bytes()
    table = pq.read_table(path)
    changed = table.replace_schema_metadata({b"price_precision": b"9"})
    monkeypatch.setattr(catalog_files.pq, "read_schema", lambda _p: changed.schema)
    with pytest.raises(RewriteVerifyError, match="schema changed"):
        _later().rewrite(path, table)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*" + TMP_SUFFIX))


def test_a_merge_with_a_different_row_count_writes_nothing(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    table = pq.read_table(path).slice(1, 3)
    with pytest.raises(RewriteVerifyError, match="rows 3, expected 4"):
        _later().write_merged(path.parent, table, 4)
    assert list(path.parent.iterdir()) == [path]


def test_a_merge_never_overwrites_a_file_nor_writes_an_empty_one(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    before = path.read_bytes()
    table = pq.read_table(path)
    with pytest.raises(FileExistsError, match="never overwrites"):
        _later().write_merged(path.parent, table, 5)  # same span: the source's own name
    with pytest.raises(ValueError, match="empty merged file"):
        _later().write_merged(path.parent, table.slice(0, 0), 0)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_the_temp_is_fsynced_before_the_rename_and_the_directory_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _leaf(tmp_path)
    table = pq.read_table(path)
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        events.append("fsync dir" if os.path.isdir(f"/proc/self/fd/{fd}") else "fsync file")
        real_fsync(fd)

    def replace(src: str | Path, dst: str | Path) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(catalog_files.os, "fsync", fsync)
    monkeypatch.setattr(catalog_files.os, "replace", replace)
    _later().rewrite(path, table)
    assert events == ["fsync file", "replace", "fsync dir"]
    events.clear()
    _later().delete(path)
    assert events == ["fsync dir", "fsync dir"]  # durable before and after the unlink


def test_a_write_that_raises_leaves_no_partial_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _leaf(tmp_path)
    before = path.read_bytes()
    real_write = pq.write_table

    def torn_write(table: pa.Table, where: str, **kwargs: object) -> None:
        real_write(table.slice(0, 1), where)  # a partial file lands, then the disk fills
        raise OSError("No space left on device")

    monkeypatch.setattr(catalog_files.pq, "write_table", torn_write)
    with pytest.raises(OSError, match="No space left"):
        _later().rewrite(path, pq.read_table(path))
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_a_merged_file_is_named_by_its_ts_init_span(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    table = pq.read_table(path)
    merged = _later().write_merged(path.parent, table.slice(1, 3), 3)
    ts = table.column("ts_init").to_pylist()
    assert merged.name != path.name
    assert pq.read_table(merged).column("ts_init").to_pylist() == ts[1:4]


def test_every_whole_file_mutation_of_an_open_day_file_is_refused(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    before = path.read_bytes()
    today = CatalogFiles(lambda: _DAY0 * _DAY_NS + 12 * 3_600 * _SEC)  # the file's own day
    table = pq.read_table(path)
    for mutate in (
        lambda: today.rewrite(path, table),
        lambda: today.rewrite(path, table, RewriteMode.WHOLE_FILE),
        lambda: today.write_merged(path.parent, table, 5),
        lambda: today.remove_merged_sources([path]),
        lambda: today.delete(path),
        lambda: today.assert_span_closed(0, _DAY0 * _DAY_NS),
    ):
        with pytest.raises(OpenDayWriteError):
            mutate()
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*" + TMP_SUFFIX))
    today.assert_span_closed(0, _DAY0 * _DAY_NS - 1)  # yesterday's span is closed


def _midnight_leaf(tmp_path: Path) -> Path:
    """One file crossing midnight into `_DAY0 + 1`: rows 23:59:57..00:00:02, one per second."""
    catalog = ParquetDataCatalog(str(tmp_path))
    midnight = (_DAY0 + 1) * _DAY_NS
    catalog.write_data(
        [
            MarkPriceUpdate(
                InstrumentId.from_str(_IID),
                Price(Decimal(f"{2000 + i}.25"), 2),
                midnight + i * _SEC,
                midnight + i * _SEC + _SEC // 2,
            )
            for i in range(-3, 3)
        ]
    )
    (path,) = (tmp_path / "data" / "mark_price_update" / _IID).glob("*.parquet")
    return path


def _with_values(table: pa.Table, values: list[bytes]) -> pa.Table:
    """`table` with its (fixed-size binary) `value` column replaced."""
    index = table.schema.get_field_index("value")
    field = table.schema.field(index)
    return table.set_column(index, field, pa.array(values, type=field.type))


def test_a_row_preserving_rewrite_may_change_closed_day_rows_of_a_file_reaching_today(
    tmp_path: Path,
) -> None:
    path = _midnight_leaf(tmp_path)
    at_0030 = CatalogFiles(lambda: (_DAY0 + 1) * _DAY_NS + 30 * 60 * _SEC)
    table = pq.read_table(path)
    values = table.column("value").to_pylist()
    changed = _with_values(table, [values[1], values[0], *values[2:]])  # two closed-day rows
    at_0030.rewrite(path, changed, RewriteMode.KEEP_OPEN_DAY_ROWS)
    assert pq.read_table(path).equals(changed)
    assert not list(path.parent.glob("*" + TMP_SUFFIX))


@pytest.mark.parametrize(
    "change",
    [
        lambda t: _with_values(t, [*t.column("value").to_pylist()[:5], b"\0" * 16]),  # a value
        lambda t: pa.concat_tables([t.slice(0, 3), t.slice(4, 1), t.slice(3, 1), t.slice(5)]),
        lambda t: t.slice(0, 5),  # an open-day row dropped
    ],
)
def test_a_row_preserving_rewrite_that_would_change_an_open_day_row_is_refused(
    tmp_path: Path, change: Callable[[pa.Table], pa.Table]
) -> None:
    path = _midnight_leaf(tmp_path)
    before = path.read_bytes()
    at_0030 = CatalogFiles(lambda: (_DAY0 + 1) * _DAY_NS + 30 * 60 * _SEC)
    new = change(pq.read_table(path))
    with pytest.raises(OpenDayWriteError, match="rows of the current UTC day"):
        at_0030.rewrite(path, new, RewriteMode.KEEP_OPEN_DAY_ROWS)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*" + TMP_SUFFIX))


def test_remove_merged_sources_tolerates_a_duplicate_and_a_vanished_path(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    gone = path.with_name("2024-10-04T00-00-00-000000000Z_2024-10-04T00-00-01-000000000Z.parquet")
    _later().remove_merged_sources([path, path, gone])
    assert not path.exists()


def test_a_rename_failing_part_way_through_a_staged_set_is_reported_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _leaf(tmp_path)
    second = _leaf(tmp_path / "other")
    writer = _later()
    staged = [
        writer.stage_rewrite(p, pq.read_table(p), RewriteMode.WHOLE_FILE) for p in (first, second)
    ]
    real_replace = os.replace

    def fail_second(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == second:
            raise OSError(5, "Input/output error")
        real_replace(src, dst)

    monkeypatch.setattr(catalog_files.os, "replace", fail_second)
    with pytest.raises(PartialCommitError) as partial:
        writer.commit_rewrites(staged)
    assert (partial.value.committed, partial.value.kept) == ([first], [second])
    assert not list(first.parent.glob("*" + TMP_SUFFIX))
    assert not list(second.parent.glob("*" + TMP_SUFFIX))


def test_stale_temp_files_are_removed_and_nothing_else(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    leftovers = [
        path.with_name(path.name + suffix)
        for suffix in (TMP_SUFFIX, ".rebuild.tmp", ".consolidate.tmp", ".tmp")
    ]
    for leftover in leftovers:
        leftover.write_bytes(b"torn")
    assert sorted(CatalogFiles().remove_stale_tmp(path.parent)) == sorted(leftovers)
    assert list(path.parent.iterdir()) == [path]


def test_only_an_empty_directory_is_removed(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    files = CatalogFiles()
    assert not files.remove_empty_dir(path.parent)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert files.remove_empty_dir(empty)
    assert not empty.exists()
    assert not files.remove_empty_dir(empty)  # gone already: nothing to do


def test_a_racing_rmdir_failure_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    def raced(_path: object) -> None:
        raise OSError(39, "Directory not empty")  # a file landed after the emptiness check

    monkeypatch.setattr(catalog_files.os, "rmdir", raced)
    assert not CatalogFiles().remove_empty_dir(empty)
    assert empty.exists()


def _contract(writer: CatalogWriter, leaf: Path) -> None:
    """Check the `CatalogWriter` contract on a closed file: rewrite, merge, remove, delete."""
    (path,) = leaf.glob("*.parquet")
    table = pq.read_table(path)
    writer.rewrite(path, table)
    assert pq.read_table(path).equals(table)
    staged = writer.stage_rewrite(path, table.slice(0, 4), RewriteMode.KEEP_OPEN_DAY_ROWS)
    writer.discard_rewrites([staged])
    assert pq.read_table(path).equals(table)  # discarded: nothing renamed
    writer.commit_rewrites([writer.stage_rewrite(path, table, RewriteMode.KEEP_OPEN_DAY_ROWS)])
    assert pq.read_table(path).equals(table)
    merged = writer.write_merged(leaf, pa.concat_tables([table.slice(0, 2)]), 2)
    writer.remove_merged_sources([merged])
    assert not merged.exists()
    writer.delete(path)
    assert not path.exists()
    assert writer.remove_empty_dir(leaf)


def test_catalog_files_meet_the_port_contract(tmp_path: Path) -> None:
    path = _leaf(tmp_path)
    with maintenance(tmp_path, now_ns=lambda: (_DAY0 + 10) * _DAY_NS) as writer:
        assert writer is not None
        with maintenance(tmp_path) as second:
            assert second is None  # one maintenance run at a time
        _contract(writer, path.parent)


# -- story 25.1b: the closed-hour scope and the scheduler's state file ----------------------------

_HOUR = 3_600 * _SEC


def _hour_files(tmp_path: Path) -> tuple[Path, Path]:
    """Two one-row files of `_DAY0`: one in 03:00-04:00 (closed at 04:30), one in 04:00-05:00."""
    catalog = ParquetDataCatalog(str(tmp_path))
    iid = InstrumentId.from_str(_IID)
    for ts in (_DAY0 * _DAY_NS + 3 * _HOUR + 10 * _SEC, _DAY0 * _DAY_NS + 4 * _HOUR + 10 * _SEC):
        catalog.write_data([MarkPriceUpdate(iid, Price(Decimal("2000.25"), 2), ts, ts)])
    closed, current = sorted((tmp_path / "data" / "mark_price_update" / _IID).glob("*.parquet"))
    return closed, current


def test_a_closed_hour_merge_refuses_the_current_hour_and_a_crossing_span(tmp_path: Path) -> None:
    closed, current = _hour_files(tmp_path)
    at_0430 = CatalogFiles(lambda: _DAY0 * _DAY_NS + 4 * _HOUR + 30 * 60 * _SEC)
    before = current.read_bytes()
    table = pq.read_table(current)
    both = pa.concat_tables([pq.read_table(closed), table])
    for mutate in (
        lambda: at_0430.write_merged(current.parent, table, 1, scope=MergeScope.CLOSED_HOUR),
        lambda: at_0430.remove_merged_sources([current], scope=MergeScope.CLOSED_HOUR),
        lambda: at_0430.write_merged(current.parent, both, 2, scope=MergeScope.CLOSED_HOUR),
    ):
        with pytest.raises(OpenDayWriteError):
            mutate()
    assert current.read_bytes() == before
    assert not list(current.parent.glob("*" + TMP_SUFFIX))
    with pytest.raises(OpenDayWriteError):  # the default scope still refuses all of today
        at_0430.remove_merged_sources([closed])
    at_0430.remove_merged_sources([closed], scope=MergeScope.CLOSED_HOUR)  # a closed hour: allowed
    assert not closed.exists()


def test_the_state_file_is_replaced_atomically_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    catalog_files.write_json_atomic(path, {"a": 1})
    catalog_files.write_json_atomic(path, {"b": [1, 2]})
    assert json.loads(path.read_text()) == {"b": [1, 2]}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_a_failed_state_write_keeps_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    catalog_files.write_json_atomic(path, {"a": 1})
    with pytest.raises(TypeError):
        catalog_files.write_json_atomic(path, {"a": object()})
    assert json.loads(path.read_text()) == {"a": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


# -- story 30.1: compact write settings for every catalog data type ------------------------------

_DICTIONARY_ENCODINGS = frozenset({"RLE_DICTIONARY", "PLAIN_DICTIONARY"})


@pytest.fixture(scope="module")
def catalogs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """
    Build a fixture day of every data type as Nautilus writes it (`source`), and a copy whose every
    file went through `CatalogFiles.rewrite` (`rewritten`).
    """
    source = tmp_path_factory.mktemp("source")
    catalog_fixture.write_day(source)
    rewritten = tmp_path_factory.mktemp("rewritten") / "catalog"
    shutil.copytree(source, rewritten)
    writer = _later(catalog_fixture.DAY0 + 10)
    for path in catalog_fixture.data_files(rewritten).values():
        writer.rewrite(path, pq.read_table(path))
    return source, rewritten


def _chunks(path: Path) -> list[pq.ColumnChunkMetaData]:
    metadata = pq.read_metadata(path)
    return [
        metadata.row_group(g).column(c)
        for g in range(metadata.num_row_groups)
        for c in range(metadata.num_columns)
    ]


def _default_size(table: pa.Table, where: Path) -> int:
    """Return the size of the pre-Story-30.1 archive write: zstd at default level and encodings."""
    pq.write_table(table, where, compression="zstd")
    return where.stat().st_size


@pytest.mark.parametrize("data_type", catalog_fixture.DATA_TYPES)
def test_every_catalog_type_round_trips_value_identical(
    catalogs: tuple[Path, Path], data_type: str
) -> None:
    source, rewritten = catalogs
    before = pq.read_table(catalog_fixture.data_files(source)[data_type])
    path = catalog_fixture.data_files(rewritten)[data_type]
    after = pq.read_table(path)
    assert same_schema(after.schema, before.schema)  # metadata, e.g. price_precision, included
    assert after.num_rows == before.num_rows > 0
    assert after.equals(before)  # every value, in order
    assert is_compact(path)
    assert not is_compact(catalog_fixture.data_files(source)[data_type])


@pytest.mark.parametrize("data_type", catalog_fixture.DATA_TYPES)
def test_every_catalog_type_is_written_with_the_compact_encodings(
    catalogs: tuple[Path, Path], data_type: str
) -> None:
    path = catalog_fixture.data_files(catalogs[1])[data_type]
    timestamps = set(timestamp_columns(pq.read_schema(path)))
    assert {"ts_event", "ts_init"} <= timestamps
    for chunk in _chunks(path):
        assert chunk.compression == "ZSTD"
        assert chunk.is_stats_set, chunk.path_in_schema  # an all-null column has no min/max
        if chunk.path_in_schema in timestamps:
            assert chunk.statistics.has_min_max, chunk.path_in_schema  # what pushdown prunes on
            assert "DELTA_BINARY_PACKED" in chunk.encodings, chunk.path_in_schema
            assert not _DICTIONARY_ENCODINGS & set(chunk.encodings), chunk.path_in_schema
        elif chunk.physical_type != "BOOLEAN":  # Parquet bit-packs booleans, never a dictionary
            assert _DICTIONARY_ENCODINGS & set(chunk.encodings), chunk.path_in_schema


def test_the_nested_book_columns_are_dictionary_encoded_on_their_leaf(
    catalogs: tuple[Path, Path],
) -> None:
    path = catalog_fixture.data_files(catalogs[1])["custom_dydx_second_snapshot"]
    leaves = {chunk.path_in_schema for chunk in _chunks(path)}
    book = {
        f"{name}.list.element" for name in ("bid_prices", "bid_sizes", "ask_prices", "ask_sizes")
    }
    assert book <= leaves  # covered by the per-column dictionary check above


def test_funding_next_funding_ns_is_a_timestamp_and_counts_are_not(
    catalogs: tuple[Path, Path],
) -> None:
    files = catalog_fixture.data_files(catalogs[1])
    funding = pq.read_schema(files["funding_rate_update"])
    assert timestamp_columns(funding) == ["next_funding_ns", "ts_event", "ts_init"]
    deltas = pq.read_schema(files["order_book_deltas"])
    assert timestamp_columns(deltas) == ["ts_event", "ts_init"]  # sequence, order_id, flags: no


@pytest.mark.parametrize("data_type", catalog_fixture.DATA_TYPES)
def test_every_rewritten_file_is_smaller_than_the_default_write(
    catalogs: tuple[Path, Path], data_type: str, tmp_path: Path
) -> None:
    path = catalog_fixture.data_files(catalogs[1])[data_type]
    table = pq.read_table(catalog_fixture.data_files(catalogs[0])[data_type])
    assert path.stat().st_size < _default_size(table, tmp_path / "default.parquet")


@pytest.mark.parametrize("data_type", catalog_fixture.DATA_TYPES)
def test_encoded_size_is_the_size_of_the_rewritten_file(
    catalogs: tuple[Path, Path], data_type: str
) -> None:
    source, rewritten = catalogs
    table = pq.read_table(catalog_fixture.data_files(source)[data_type])
    assert encoded_size(table) == catalog_fixture.data_files(rewritten)[data_type].stat().st_size


def _as_dicts(rows: list[object]) -> list[tuple[str, dict[str, object]]]:
    """Each object's every field (`to_dict`), a `CustomData` unwrapped to the data it carries."""
    out = []
    for row in rows:
        data = getattr(row, "data", row) if type(row).__name__ == "CustomData" else row
        out.append((type(data).__name__, type(data).to_dict(data)))  # type: ignore[attr-defined]
    return out


@pytest.mark.parametrize(
    "cls",
    [
        DydxSecondSnapshot,
        TradeTick,
        MarkPriceUpdate,
        FundingRateUpdate,
        OpenInterest,
        InstrumentStatus,
        OrderBookDelta,
    ],
)
def test_the_catalog_returns_identical_objects_from_rewritten_files(
    catalogs: tuple[Path, Path], cls: type
) -> None:
    source, rewritten = (ParquetDataCatalog(str(root)) for root in catalogs)
    iid = [str(catalog_fixture.IID)]
    before = source.query(cls, identifiers=iid)
    assert before
    assert _as_dicts(rewritten.query(cls, identifiers=iid)) == _as_dicts(before)


def test_the_catalog_returns_identical_instruments_from_rewritten_files(
    catalogs: tuple[Path, Path],
) -> None:
    source, rewritten = (ParquetDataCatalog(str(root)).instruments() for root in catalogs)
    assert {type(i).__name__ for i in source} == {"CryptoPerpetual", "CurrencyPair"}
    assert _as_dicts(rewritten) == _as_dicts(source)
    assert rewritten == source


def test_the_kernel_projected_readers_read_rewritten_files_unchanged(
    catalogs: tuple[Path, Path],
) -> None:
    # IndexPriceUpdate has no catalog decoder in this nautilus_trader (NotImplementedError), so
    # `query_index_prices` -- its reader -- is its object-level check.
    iid = str(catalog_fixture.IID)
    lo = catalog_fixture.DAY0 * catalog_fixture.DAY_NS
    hi = lo + catalog_fixture.DAY_NS
    source, rewritten = (str(root) for root in catalogs)
    for reader in (
        kernel_files.query_second_ohlc,
        kernel_files.query_top_of_book,
        kernel_files.query_index_prices,
    ):
        before = reader(source, iid, lo, hi)
        assert before, reader.__name__
        assert reader(rewritten, iid, lo, hi) == before, reader.__name__
    arrays = [
        kernel_files.second_ohlc_arrays(kernel_files.snapshot_files(root, iid))
        for root in (source, rewritten)
    ]
    assert np.isnan(arrays[0]["c"]).any()  # seconds without a trade: the NaN path is compared too
    for key, before_array in arrays[0].items():
        np.testing.assert_array_equal(arrays[1][key], before_array, err_msg=key)


def test_a_day_over_the_row_group_cap_splits_into_groups_that_still_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _leaf(tmp_path, rows=3_500)
    monkeypatch.setattr(compact_parquet, "_MAX_ROW_GROUP_ROWS", 1_000)
    table = pq.read_table(path)
    _later().rewrite(path, table)
    assert pq.read_table(path).equals(table)
    metadata = pq.read_metadata(path)
    assert [metadata.row_group(g).num_rows for g in range(4)] == [1_000, 1_000, 1_000, 500]
    ts_init = pq.read_schema(path).get_field_index("ts_init")
    for g in range(metadata.num_row_groups):
        stats = metadata.row_group(g).column(ts_init).statistics
        assert stats.has_min_max
    target = table.column("ts_init")[2_500].as_py()  # a row of the third group
    (fragment,) = ds.dataset(path, format="parquet").get_fragments()
    ts = ds.field("ts_init")
    hit = pa.scalar(target, pa.uint64())
    pruned = fragment.split_by_row_group(filter=(ts >= hit) & (ts <= hit))
    assert [[group.id for group in part.row_groups] for part in pruned] == [[2]]


def test_a_value_changed_on_read_back_fails_verification_and_keeps_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _leaf(tmp_path)
    before = path.read_bytes()
    table = pq.read_table(path)
    values = table.column("value").to_pylist()
    corrupted = _with_values(table, [*values[:-1], values[0]])  # schema and count intact
    real_write = pq.write_table

    def lossy_write(written: pa.Table, where: str, **kwargs: object) -> None:
        real_write(corrupted, where, **kwargs)  # what a lossy encoding would put on disk

    monkeypatch.setattr(catalog_files.pq, "write_table", lossy_write)
    with pytest.raises(RewriteVerifyError, match="values changed on read-back: column 'value'"):
        _later().rewrite(path, table)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*" + TMP_SUFFIX))


def test_the_options_name_nested_leaves_and_refuse_an_unknown_nesting() -> None:
    schema = pa.schema(
        [
            ("ts_event", pa.uint64()),
            ("book", pa.large_list(pa.float64())),
            ("pair", pa.list_(pa.float64(), 2)),
            ("quote", pa.struct([("bid", pa.float64()), ("sizes", pa.list_(pa.float64()))])),
        ]
    )
    options = compact_write_options(schema)
    assert options["use_dictionary"] == [
        "book.list.element",
        "pair.list.element",
        "quote.bid",
        "quote.sizes.list.element",
    ]
    assert options["column_encoding"] == {"ts_event": "DELTA_BINARY_PACKED"}
    assert (options["compression"], options["compression_level"]) == ("zstd", COMPACT_ZSTD_LEVEL)
    with pytest.raises(ValueError, match="nested type"):
        compact_write_options(pa.schema([("m", pa.map_(pa.string(), pa.float64()))]))


def test_a_schema_without_a_timestamp_column_keeps_every_dictionary() -> None:
    options = compact_write_options(pa.schema([("count", pa.uint32()), ("name", pa.string())]))
    assert "column_encoding" not in options
    assert options["use_dictionary"] == ["count", "name"]


def test_the_value_check_compares_every_row_group_of_a_multi_group_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _leaf(tmp_path, rows=250)
    monkeypatch.setattr(compact_parquet, "_MAX_ROW_GROUP_ROWS", 100)
    table = pq.read_table(path)
    _later().rewrite(path, table)
    assert pq.read_metadata(path).num_row_groups == 3
    assert catalog_files._read_back_mismatch(path, table) is None
    values = table.column("value").to_pylist()
    changed = _with_values(table, [*values[:-1], values[0]])  # only the last group differs
    assert catalog_files._read_back_mismatch(path, changed) == (
        "column 'value', row group 2 (rows 200..250)"
    )
    assert catalog_files._read_back_mismatch(path, table.slice(0, 200)) is not None  # rows beyond
    longer = pa.concat_tables([table, table.slice(0, 1)])
    assert catalog_files._read_back_mismatch(path, longer) == "250 rows read, 251 written"
