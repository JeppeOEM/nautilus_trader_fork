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
"""`archive.repair_catalog`: impossible-OHLC seconds cleared, gated by the capture lock and the open day."""

import fcntl
import time
from pathlib import Path

from candles.application import queries
from candles.application.rebuild import rebuild_instrument
from candles.infrastructure.sqlite_store import CandleStore
from kernel.archive_markers import capture_lock_path
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from archive.application.repair import closed_rows
from archive.application.repair import find_impossible_snapshots
from archive.application.repair import repair_instrument
from archive.application.repair import second_snapshots
from archive.infrastructure.catalog_files import CatalogFiles
from archive.infrastructure.maintenance_lock import maintenance
from archive.repair_catalog import main
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_T0 = 1_789_800_000 * 1_000_000_000 // 60_000_000_000 * 60_000_000_000
_SEC = 1_000_000_000


def _snap(
    second: int, high: float | None = None, low: float | None = None, t0: int = _T0
) -> DydxSecondSnapshot:
    traded = high is not None
    return make_snapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[100.0, 99.0],
        bid_sizes=[1.0, 1.0],
        ask_prices=[101.0, 102.0],
        ask_sizes=[1.0, 1.0],
        buy_volume=5.0 if traded else 0.0,
        sell_volume=0.0,
        buy_count=1 if traded else 0,
        sell_count=0,
        open_price=high,
        high_price=high,
        low_price=low,
        close_price=low,
        ts_event=t0 + second * _SEC,
        ts_init=t0 + second * _SEC,
    )


def test_spike_snapshot_is_cleared_and_its_candle_rebuilt(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path / "cat"))
    catalog_path = str(tmp_path / "cat")
    # 3 minutes of 1s data; the spike sits in the middle minute, which is otherwise untraded.
    snaps = [_snap(s) for s in range(180)]
    snaps[70] = _snap(70, high=150.0, low=50.0)
    catalog.write_data(snaps)
    db_path = str(tmp_path / "candles.db")
    rebuild_instrument(db_path, catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    store = CandleStore(db_path)
    assert [c["h"] for c in queries.window(store.connection, _IID, 60, 1 << 62, 10)] == [
        150.0
    ]  # the spike is in the store

    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    assert [f.ts_event for f in flagged] == [_T0 + 70 * _SEC]

    repair_instrument(catalog, catalog_path, _IID, flagged, db_path)

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC) == []
    rows = second_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    assert len(rows) == 180
    assert all(r.high_price is None for r in rows)
    assert queries.window(store.connection, _IID, 60, 1 << 62, 10) == []  # no trade left
    store.close()


def _spiked_catalog(tmp_path: Path) -> str:
    catalog_path = str(tmp_path / "cat")
    snaps = [_snap(s) for s in range(5)]
    snaps[2] = _snap(2, high=150.0, low=50.0)
    ParquetDataCatalog(catalog_path).write_data(snaps)
    return catalog_path


def test_a_running_collector_of_the_venue_blocks_the_repair(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    with capture_lock_path(catalog_path, "DYDX").open("a") as capture:
        fcntl.flock(capture, fcntl.LOCK_SH | fcntl.LOCK_NB)  # what a running collector holds
        assert main(["--catalog", catalog_path, "--apply"]) == 1
    assert error_ledger.counts() == {"repair.capture_running": 1}
    assert len(find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)) == 1
    assert main(["--catalog", catalog_path, "--apply"]) == 0  # collector gone: repaired
    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC) == []


def test_a_report_does_not_need_the_capture_lock(tmp_path: Path) -> None:
    catalog_path = _spiked_catalog(tmp_path)
    with capture_lock_path(catalog_path, "DYDX").open("a") as capture:
        fcntl.flock(capture, fcntl.LOCK_SH | fcntl.LOCK_NB)
        assert main(["--catalog", catalog_path]) == 0


def test_a_row_of_the_current_utc_day_is_skipped_and_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    today = time.time_ns() // 86_400_000_000_000 * 86_400_000_000_000
    flagged = [_snap(2, high=150.0, low=50.0), _snap(3, high=150.0, low=50.0, t0=today)]
    closed = closed_rows(CatalogFiles(), str(tmp_path), _IID, flagged)
    assert closed == [flagged[0]]
    assert error_ledger.counts() == {"repair.open_day": 1}


def test_a_closed_row_in_a_file_crossing_into_today_is_skipped(tmp_path: Path) -> None:
    """`delete_data_range` rewrites the whole containing file, and that one is capture's."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    (written,) = (Path(catalog_path) / "data" / "custom_dydx_second_snapshot" / _IID).glob("*")
    today = time.strftime("%Y-%m-%d", time.gmtime())
    written.rename(
        written.with_name(f"{written.stem.split('_')[0]}_{today}T00-00-01-000000000Z.parquet")
    )
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)
    assert len(flagged) == 1  # the row itself lies on a closed day
    assert closed_rows(CatalogFiles(), catalog_path, _IID, flagged) == []
    assert error_ledger.counts() == {"repair.open_day": 1}


def test_an_instrument_without_a_venue_is_refused_not_repaired_unlocked(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    assert main(["--catalog", catalog_path, "--instrument", "NOVENUE", "--apply"]) == 1
    assert error_ledger.counts() == {"repair.error": 1}


def test_an_unknown_venue_is_ledgered_and_refused(tmp_path: Path) -> None:
    """A venue `kernel.venues` does not know names a lock no collector holds: never trusted."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    assert main(["--catalog", catalog_path, "--instrument", "BTC-USD.NOWHERE", "--apply"]) == 1
    assert error_ledger.counts() == {"repair.error": 1}
    assert "unknown venue 'NOWHERE'" in error_ledger.last_details()["repair.error"]


def test_a_report_takes_no_maintenance_lock(tmp_path: Path) -> None:
    catalog_path = _spiked_catalog(tmp_path)
    with maintenance(catalog_path) as held:
        assert held is not None
        assert main(["--catalog", catalog_path]) == 0
        assert main(["--catalog", catalog_path, "--apply"]) == 1


def test_a_missing_catalog_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    assert main(["--catalog", str(tmp_path / "nope")]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}
