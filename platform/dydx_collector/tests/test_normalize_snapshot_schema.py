"""Audit D-24: mixed-schema snapshot files made the catalog drop OHLC for the new files."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dydx_collector.normalize_snapshot_schema import files_needing_migration
from dydx_collector.normalize_snapshot_schema import migrate_file
from collector_core.second_snapshot import DydxSecondSnapshot
from ml_signals.catalog_stats import query_second_snapshots
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


IID = "BTC-USD-PERP.DYDX"
_SEC = 1_000_000_000


def _snap(ts: int, price: float) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(IID), bid_prices=[price - 1], bid_sizes=[1.0],
        ask_prices=[price + 1], ask_sizes=[1.0], buy_volume=1.0, sell_volume=0.5, buy_count=1, sell_count=1,
        open_price=price, high_price=price, low_price=price, close_price=price, ts_event=ts, ts_init=ts,
    )


def _catalog_with_old_and_new(tmp_path: Path) -> tuple[str, int, int]:
    """One old-schema file (no OHLC columns) BEFORE one new-schema file, like the real catalog."""
    catalog = tmp_path / "catalog"
    new_start = 1_800_000_000 * _SEC
    ParquetDataCatalog(str(catalog)).write_data([_snap(new_start + i * _SEC, 100.0 + i) for i in range(3)])
    d = catalog / "data" / "custom_dydx_second_snapshot" / IID
    (new_file,) = d.glob("*.parquet")
    table = pq.read_table(new_file).drop_columns(["open_price", "high_price", "low_price", "close_price"])
    old_start = new_start - 3600 * _SEC
    table = table.set_column(table.schema.get_field_index("ts_event"), "ts_event",
                             pa.array([old_start + i * _SEC for i in range(3)], pa.uint64()))
    table = table.set_column(table.schema.get_field_index("ts_init"), "ts_init",
                             pa.array([old_start + i * _SEC for i in range(3)], pa.uint64()))
    stamp = "2027-01-15T07-{m:02d}-{s:02d}-000000000Z"  # sorts before the new file's name
    pq.write_table(table, d / f"{stamp.format(m=0, s=0)}_{stamp.format(m=0, s=2)}.parquet")
    return str(catalog), old_start, new_start


def test_mixed_schema_loses_ohlc_then_migration_restores_it(tmp_path: Path) -> None:
    catalog, old_start, new_start = _catalog_with_old_and_new(tmp_path)
    lo, hi = old_start - _SEC, new_start + 10 * _SEC

    before = [r for r in query_second_snapshots(catalog, IID, lo, hi) if r.ts_event >= new_start]
    assert before and all(r.open_price is None for r in before)  # the D-24 bug, reproduced

    (old_file,) = files_needing_migration(catalog)
    migrate_file(catalog, old_file, str(tmp_path / "backup"))

    after = [r for r in query_second_snapshots(catalog, IID, lo, hi) if r.ts_event >= new_start]
    assert [r.open_price for r in after] == [100.0, 101.0, 102.0]
    assert files_needing_migration(catalog) == []  # idempotent
    assert (tmp_path / "backup" / old_file.relative_to(catalog)).exists()  # original preserved
    old_rows = [r for r in query_second_snapshots(catalog, IID, lo, hi) if r.ts_event < new_start]
    assert len(old_rows) == 3 and all(r.open_price is None and r.buy_volume == 1.0 for r in old_rows)


def test_migration_refuses_unknown_columns(tmp_path: Path) -> None:
    catalog, _, _ = _catalog_with_old_and_new(tmp_path)
    (old_file,) = files_needing_migration(catalog)
    table = pq.read_table(old_file).append_column("mystery", pa.array([1, 2, 3]))
    pq.write_table(table, old_file)
    with pytest.raises(ValueError, match="mystery"):
        migrate_file(catalog, old_file, str(tmp_path / "backup"))
    assert pq.read_table(old_file).num_rows == 3  # untouched
