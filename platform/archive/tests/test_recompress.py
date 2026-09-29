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
"""
Story 30.1: `archive.tools.recompress` rewrites every closed non-compact file with the compact
settings under the maintenance lock, value-identically; skips the current UTC day's files and
already-compact ones; reports without writing; and ledgers a file that fails on its own.
"""

import time
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from observability import error_ledger

from archive.infrastructure.compact_parquet import is_compact
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance
from archive.tests import catalog_fixture
from archive.tools import recompress
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_BYBIT = "ETHUSDT-LINEAR.BYBIT"
_TODAY_IID = "SOLUSDT-PERP.BINANCE"  # its own leaf, so the fixture's leaves keep one file each
_NOW = time.time_ns()


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    One "now" for the current-day file's stamp and for the tool's own clock: a test straddling
    UTC midnight would otherwise see that file as closed and rewrite it.
    """
    monkeypatch.setattr(recompress.time, "time_ns", lambda: _NOW)


def _mark_file(root: Path, iid: str, ts: int) -> Path:
    """Write one real one-row `MarkPriceUpdate` file of `iid` at `ts`; return its path."""
    leaf = root / "data" / "mark_price_update" / iid
    existing = set(leaf.glob("*.parquet"))
    ParquetDataCatalog(str(root)).write_data(
        [MarkPriceUpdate(InstrumentId.from_str(iid), Price(Decimal("2000.25"), 2), ts, ts)]
    )
    (path,) = set(leaf.glob("*.parquet")) - existing
    return path


def _catalog(tmp_path: Path) -> tuple[Path, Path]:
    """Build a closed fixture day of every type plus one file of the current UTC day (returned)."""
    root = tmp_path / "catalog"
    catalog_fixture.write_day(root, seconds=300)
    today = _mark_file(root, _TODAY_IID, time.time_ns())
    return root, today


def _closed_files(root: Path) -> list[Path]:
    return list(catalog_fixture.data_files(root).values())


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_report_writes_nothing_takes_no_lock_and_projects_the_real_sizes(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    root, _ = _catalog(tmp_path)
    before = _snapshot(root)
    report = recompress.run(None, str(root), None, None, time.time_ns())
    assert _snapshot(root) == before
    assert not (root / MAINTENANCE_LOCK_NAME).exists()
    assert recompress.main(["--catalog", str(root)]) == 0
    assert _snapshot(root) == before
    with maintenance(root) as writer:
        assert writer is not None
        applied = recompress.run(writer, str(root), None, None, time.time_ns())
    assert applied.by_type == report.by_type  # projected bytes are the bytes written
    assert report.total().files == len(catalog_fixture.DATA_TYPES)
    assert report.total().bytes_after < report.total().bytes_before
    assert error_ledger.counts() == {}


def test_apply_rewrites_every_closed_file_value_identically_and_skips_today(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    root, today = _catalog(tmp_path)
    tables = {path: pq.read_table(path) for path in _closed_files(root)}
    today_bytes = today.read_bytes()
    assert recompress.main(["--catalog", str(root), "--apply"]) == 0
    for path, table in tables.items():
        after = pq.read_table(path)  # same name: the rewrite keeps the file's span
        assert after.equals(table), path
        assert after.schema.metadata == table.schema.metadata, path
        assert is_compact(path), path
    assert today.read_bytes() == today_bytes  # the current UTC day belongs to capture
    assert error_ledger.counts() == {}


def test_a_second_run_finds_nothing_to_do(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    with maintenance(root) as writer:
        assert writer is not None
        first = recompress.run(writer, str(root), None, None, time.time_ns())
        second = recompress.run(writer, str(root), None, None, time.time_ns())
    assert (first.total().files, first.open_day) == (len(catalog_fixture.DATA_TYPES), 1)
    assert second.total().files == 0
    assert (second.already_compact, second.open_day) == (len(catalog_fixture.DATA_TYPES), 1)


def test_venue_and_type_narrow_the_run(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    bybit = _mark_file(root, _BYBIT, catalog_fixture.DAY0 * catalog_fixture.DAY_NS)
    files = catalog_fixture.data_files(root)
    args = ["--catalog", str(root), "--apply", "--venue", "BINANCE"]
    assert recompress.main([*args, "--type", "mark_price_update", "--type", "trade_tick"]) == 0
    assert is_compact(files["mark_price_update"])
    assert is_compact(files["trade_tick"])
    assert not is_compact(bybit)  # another venue
    assert not is_compact(files["index_price_update"])  # another type


def test_a_held_lock_refuses_to_start(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    with maintenance(root) as holder:
        assert holder is not None
        before = _snapshot(root)
        assert recompress.main(["--catalog", str(root), "--apply"]) == 1
        assert _snapshot(root) == before


def test_a_failing_file_is_ledgered_and_the_run_goes_on(tmp_path: Path) -> None:
    error_ledger.reset()
    root, _ = _catalog(tmp_path)
    good = catalog_fixture.data_files(root)["mark_price_update"]
    torn = good.parent.with_name("XRPUSDT-PERP.BINANCE") / good.name  # a leaf of its own
    torn.parent.mkdir()
    torn.write_bytes(good.read_bytes()[:100])  # truncated: unreadable
    assert recompress.main(["--catalog", str(root), "--apply"]) == 2
    assert torn.stat().st_size == 100  # left as it was
    assert all(is_compact(path) for path in _closed_files(root))
    assert error_ledger.counts() == {"recompress.error": 1}
    assert torn.name in error_ledger.last_details()["recompress.error"]


def test_a_missing_catalog_is_ledgered_and_exit_one(tmp_path: Path) -> None:
    error_ledger.reset()
    assert recompress.main(["--catalog", str(tmp_path / "nope")]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}


def test_a_file_merged_away_since_the_listing_is_counted_not_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    root, _ = _catalog(tmp_path)
    gone = catalog_fixture.data_files(root)["mark_price_update"]
    real = recompress._recompress_file

    def consolidated_meanwhile(writer: object, path: Path, totals: object) -> None:
        if path == gone:
            path.unlink()  # a lock-free report races the intraday merge's source removal
        real(writer, path, totals)  # type: ignore[arg-type]

    monkeypatch.setattr(recompress, "_recompress_file", consolidated_meanwhile)
    stats = recompress.run(None, str(root), None, None, time.time_ns())
    assert (stats.vanished, stats.failed) == (1, 0)
    assert error_ledger.counts() == {}


def test_a_file_gone_under_the_lock_is_ledgered_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    root, _ = _catalog(tmp_path)
    gone = catalog_fixture.data_files(root)["mark_price_update"]
    real = recompress._recompress_file

    def removed_meanwhile(writer: object, path: Path, totals: object) -> None:
        if path == gone:
            path.unlink()  # nothing else removes files while the maintenance lock is held
        real(writer, path, totals)  # type: ignore[arg-type]

    monkeypatch.setattr(recompress, "_recompress_file", removed_meanwhile)
    with maintenance(root) as writer:
        assert writer is not None
        stats = recompress.run(writer, str(root), None, None, time.time_ns())
    assert (stats.vanished, stats.failed) == (0, 1)
    assert error_ledger.counts() == {"recompress.error": 1}
    assert gone.name in error_ledger.last_details()["recompress.error"]


def test_a_leaf_that_cannot_be_cleaned_is_ledgered_and_the_rest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    root, _ = _catalog(tmp_path)
    stuck = catalog_fixture.data_files(root)["trade_tick"]
    with maintenance(root) as writer:
        assert writer is not None
        real = writer.remove_stale_tmp

        def unremovable(leaf: Path) -> list[Path]:
            if leaf == stuck.parent:
                raise PermissionError(13, "Permission denied")
            return real(leaf)

        monkeypatch.setattr(writer, "remove_stale_tmp", unremovable)
        stats = recompress.run(writer, str(root), None, None, time.time_ns())
    assert stats.failed == 1
    assert not is_compact(stuck)  # its leaf skipped
    assert stats.total().files == len(catalog_fixture.DATA_TYPES) - 1
    assert error_ledger.counts() == {"recompress.error": 1}
