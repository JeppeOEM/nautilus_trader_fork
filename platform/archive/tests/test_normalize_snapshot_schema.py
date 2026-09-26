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
"""Audit D-24: mixed-schema snapshot files made the catalog drop OHLC for the new files."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from archive.application.repair import second_snapshots as query_second_snapshots
from archive.infrastructure.catalog_files import CatalogFiles
from archive.tools import normalize_snapshot_schema
from archive.tools.normalize_snapshot_schema import files_needing_migration
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


IID = "BTC-USD-PERP.DYDX"
_SEC = 1_000_000_000
_LATER = 1_900_000_000 * _SEC  # "now" for the fixture's 2027 files, so they are closed days


def migrate_file(catalog: str, path: Path, backup_dir: str) -> None:
    normalize_snapshot_schema.migrate_file(CatalogFiles(lambda: _LATER), catalog, path, backup_dir)


def _snap(ts: int, price: float) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(IID),
        bid_prices=[price - 1],
        bid_sizes=[1.0],
        ask_prices=[price + 1],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=price,
        high_price=price,
        low_price=price,
        close_price=price,
        ts_event=ts,
        ts_init=ts,
    )


def _catalog_with_old_and_new(tmp_path: Path) -> tuple[str, int, int]:
    """One old-schema file (no OHLC columns) BEFORE one new-schema file, like the real catalog."""
    catalog = tmp_path / "catalog"
    new_start = 1_800_000_000 * _SEC
    ParquetDataCatalog(str(catalog)).write_data(
        [_snap(new_start + i * _SEC, 100.0 + i) for i in range(3)]
    )
    d = catalog / "data" / "custom_dydx_second_snapshot" / IID
    (new_file,) = d.glob("*.parquet")
    table = pq.read_table(new_file).drop_columns(
        ["open_price", "high_price", "low_price", "close_price"]
    )
    old_start = new_start - 3600 * _SEC
    table = table.set_column(
        table.schema.get_field_index("ts_event"),
        "ts_event",
        pa.array([old_start + i * _SEC for i in range(3)], pa.uint64()),
    )
    table = table.set_column(
        table.schema.get_field_index("ts_init"),
        "ts_init",
        pa.array([old_start + i * _SEC for i in range(3)], pa.uint64()),
    )
    stamp = "2027-01-15T07-{m:02d}-{s:02d}-000000000Z"  # sorts before the new file's name
    pq.write_table(table, d / f"{stamp.format(m=0, s=0)}_{stamp.format(m=0, s=2)}.parquet")
    return str(catalog), old_start, new_start


def test_mixed_schema_loses_ohlc_then_migration_restores_it(tmp_path: Path) -> None:
    catalog, old_start, new_start = _catalog_with_old_and_new(tmp_path)
    lo, hi = old_start - _SEC, new_start + 10 * _SEC

    before = [r for r in query_second_snapshots(catalog, IID, lo, hi) if r.ts_event >= new_start]
    assert before
    assert all(r.open_price is None for r in before)  # the D-24 bug, reproduced

    (old_file,) = files_needing_migration(catalog)
    migrate_file(catalog, old_file, str(tmp_path / "backup"))

    after = [r for r in query_second_snapshots(catalog, IID, lo, hi) if r.ts_event >= new_start]
    assert [r.open_price for r in after] == [100.0, 101.0, 102.0]
    assert files_needing_migration(catalog) == []  # idempotent
    assert (tmp_path / "backup" / old_file.relative_to(catalog)).exists()  # original preserved
    old_rows = [r for r in query_second_snapshots(catalog, IID, lo, hi) if r.ts_event < new_start]
    assert len(old_rows) == 3
    assert all(r.open_price is None and r.buy_volume == 1.0 for r in old_rows)


def test_migration_refuses_unknown_columns(tmp_path: Path) -> None:
    catalog, _, _ = _catalog_with_old_and_new(tmp_path)
    (old_file,) = files_needing_migration(catalog)
    table = pq.read_table(old_file).append_column("mystery", pa.array([1, 2, 3]))
    pq.write_table(table, old_file)
    with pytest.raises(ValueError, match="mystery"):
        migrate_file(catalog, old_file, str(tmp_path / "backup"))
    assert pq.read_table(old_file).num_rows == 3  # untouched


def test_the_rewrite_is_zstd_with_schema_and_rows_unchanged(tmp_path: Path) -> None:
    catalog, _, _ = _catalog_with_old_and_new(tmp_path)
    (old_file,) = files_needing_migration(catalog)
    rows_before = pq.read_table(old_file).num_rows
    migrate_file(catalog, old_file, str(tmp_path / "backup"))
    meta = pq.read_metadata(old_file)
    assert meta.row_group(0).column(0).compression == "ZSTD"  # was snappy before Story 25.1
    assert meta.num_rows == rows_before
    # The catalog's own new-schema file and the migrated one now share one full schema (D-24).
    (new_file,) = [f for f in old_file.parent.glob("*.parquet") if f != old_file]
    assert pq.read_schema(old_file).equals(pq.read_schema(new_file), check_metadata=True)


def test_an_open_day_file_is_skipped_ledgered_and_exit_two(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog, _, _ = _catalog_with_old_and_new(tmp_path)  # 2027: reaches "today" and beyond
    (old_file,) = files_needing_migration(catalog)
    before = old_file.read_bytes()
    args = ["--catalog", catalog, "--backup-dir", str(tmp_path / "bak"), "--apply"]
    assert normalize_snapshot_schema.main(args) == 2
    assert old_file.read_bytes() == before
    assert error_ledger.counts() == {"normalize_snapshot_schema.open_day": 1}


def test_a_failing_file_is_ledgered_and_the_run_goes_on(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog, _, _ = _catalog_with_old_and_new(tmp_path)
    (old_file,) = files_needing_migration(catalog)
    second = old_file.with_name(old_file.name.replace("07-00-", "06-00-"))
    second.write_bytes(old_file.read_bytes())
    table = pq.read_table(old_file).append_column("mystery", pa.array([1, 2, 3]))
    pq.write_table(table, old_file)  # refused on its own: a column outside the schema
    before = old_file.read_bytes()
    todo = files_needing_migration(catalog)
    assert len(todo) == 2
    writer = CatalogFiles(lambda: _LATER)  # the fixture's 2027 files are closed days by then
    outcomes = normalize_snapshot_schema._migrate_all(writer, catalog, todo, str(tmp_path / "bak"))
    assert (outcomes["done"], outcomes["error"]) == (1, 1)
    assert normalize_snapshot_schema._finished(outcomes, str(tmp_path / "bak")) == 2
    assert old_file.read_bytes() == before  # left as it was
    assert files_needing_migration(catalog) == [old_file]  # the other one was migrated
    assert error_ledger.counts() == {"normalize_snapshot_schema.error": 1}
    assert "mystery" in error_ledger.last_details()["normalize_snapshot_schema.error"]


def test_a_missing_catalog_is_ledgered_and_exit_one(tmp_path: Path) -> None:
    error_ledger.reset()
    assert normalize_snapshot_schema.main(["--catalog", str(tmp_path / "nope")]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}
