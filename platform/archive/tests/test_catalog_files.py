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
"""

import json
import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from archive.application.ports import CatalogWriter
from archive.application.ports import MergeScope
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteMode
from archive.application.ports import RewriteVerifyError
from archive.infrastructure import catalog_files
from archive.infrastructure.catalog_files import TMP_SUFFIX
from archive.infrastructure.catalog_files import CatalogFiles
from archive.infrastructure.maintenance_lock import maintenance
from nautilus_trader.model.data import MarkPriceUpdate
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
