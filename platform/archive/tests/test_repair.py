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
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from candles.application import queries
from candles.application.rebuild import rebuild_instrument
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import connect_rw
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.verified_days import VerifiedDaysDir
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


def _candles_dir(tmp_path: Path) -> str:
    """
    Return a directory holding a DYDX candle store for `--candles-dir` (the CLI refuses a
    missing directory, or one with no store in it).
    """
    (tmp_path / "candles").mkdir(exist_ok=True)
    connect_rw(db_path_for_venue(tmp_path / "candles", "DYDX")).close()
    return str(tmp_path / "candles")


def _verified(tmp_path: Path) -> VerifiedDaysDir:
    """Return the candle-store directory whose verdicts a repaired day loses (`--candles-dir`)."""
    return VerifiedDaysDir(tmp_path / "candles")


def _snap(
    second: int,
    high: float | None = None,
    low: float | None = None,
    t0: int = _T0,
    init_second: float | None = None,
) -> DydxSecondSnapshot:
    """Build a row at `t0 + second`; `init_second` (default `second`) sets its `ts_init` apart."""
    traded = high is not None
    init = second if init_second is None else init_second
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
        ts_init=t0 + int(init * _SEC),
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

    repair_instrument(catalog, catalog_path, _IID, flagged, _verified(tmp_path), db_path)

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC) == []
    rows = second_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    assert len(rows) == 180
    assert all(r.high_price is None for r in rows)
    assert queries.window(store.connection, _IID, 60, 1 << 62, 10) == []  # no trade left
    store.close()


def test_a_venue_ahead_row_at_the_read_start_is_returned(tmp_path: Path) -> None:
    """A venue clock ahead of ours stamps `ts_init` before the window: the start is widened too."""
    catalog_path = str(tmp_path / "cat")
    ParquetDataCatalog(catalog_path).write_data([_snap(10, init_second=-5)])
    rows = second_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [r.ts_event for r in rows] == [_T0 + 10 * _SEC]


def _second_events(catalog_path: str, n: int) -> list[int]:
    rows = second_snapshots(catalog_path, _IID, _T0 - 10 * _SEC, _T0 + (n + 10) * _SEC)
    return sorted(r.ts_event for r in rows)


def test_a_row_whose_ts_init_trails_its_ts_event_is_replaced_not_duplicated(
    tmp_path: Path,
) -> None:
    """The catalog deletes by `ts_init`: deleting the row's `ts_event` would hit another row."""
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    snaps = [_snap(s, init_second=s + 3) for s in range(10)]
    snaps[5] = _snap(5, high=150.0, low=50.0, init_second=8)
    catalog.write_data(snaps)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [f.ts_init for f in flagged] == [_T0 + 8 * _SEC]

    repair_instrument(catalog, catalog_path, _IID, flagged, _verified(tmp_path))

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC) == []
    assert _second_events(catalog_path, 10) == [_T0 + s * _SEC for s in range(10)]


def test_an_unflagged_row_sharing_the_flagged_ts_init_survives_unchanged(tmp_path: Path) -> None:
    """Caught-up seconds share their wake-up's `ts_init`; deleting it removes all of them."""
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    snaps = [_snap(s) for s in range(5)]
    sibling = _snap(6, high=100.5, low=100.5, init_second=7)  # a real trade inside the book
    snaps += [_snap(5, high=150.0, low=50.0, init_second=7), sibling]
    catalog.write_data(snaps)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [f.ts_event for f in flagged] == [_T0 + 5 * _SEC]

    repair_instrument(catalog, catalog_path, _IID, flagged, _verified(tmp_path))

    rows = {r.ts_event: r for r in second_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)}
    assert _second_events(catalog_path, 7) == [_T0 + s * _SEC for s in range(7)]
    assert rows[_T0 + 5 * _SEC].high_price is None
    assert DydxSecondSnapshot.to_dict(rows[sibling.ts_event]) == DydxSecondSnapshot.to_dict(sibling)


def test_a_pre_fix_repairs_leftover_copy_collapses_into_one_cleared_row(tmp_path: Path) -> None:
    """A pre-fix repair could miss its delete and leave a cleared copy beside the original."""
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    spike = _snap(5, high=150.0, low=50.0, init_second=8)
    leftover = _snap(5, init_second=8)  # the old repair's cleared copy, same clocks
    catalog.write_data([*[_snap(s) for s in range(5)], spike, leftover])
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)

    assert repair_instrument(catalog, catalog_path, _IID, flagged, _verified(tmp_path))

    assert _second_events(catalog_path, 6) == [_T0 + s * _SEC for s in range(6)]
    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC) == []
    assert error_ledger.counts() == {"repair.duplicate": 1}  # collapsing it is never silent


def test_a_flagged_row_no_longer_stored_is_ledgered_and_nothing_is_deleted(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data([_snap(s) for s in range(5)])
    gone = _snap(30, high=150.0, low=50.0, init_second=3)  # ts_init 3 holds only second 3

    assert not repair_instrument(catalog, catalog_path, _IID, [gone], _verified(tmp_path))

    assert error_ledger.counts() == {"repair.error": 1}
    assert _second_events(catalog_path, 5) == [_T0 + s * _SEC for s in range(5)]


def test_a_failed_rewrite_after_the_delete_is_ledgered_with_the_lost_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete then write is not atomic: a crash between them must name what it lost."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    catalog = ParquetDataCatalog(catalog_path)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)

    split = catalog.write_data  # `delete_data_range` itself writes the split remainders

    def _fail(*args: Any, **kwargs: Any) -> None:
        if "data" in kwargs:
            return split(*args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(catalog, "write_data", _fail)
    with pytest.raises(OSError):
        repair_instrument(catalog, catalog_path, _IID, flagged, _verified(tmp_path))
    assert error_ledger.counts() == {"repair.error": 1}
    assert f"lost ts_event [{_T0 + 2 * _SEC}]" in error_ledger.last_details()["repair.error"]


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
        assert (
            main(["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)])
            == 1
        )
    assert error_ledger.counts() == {"repair.capture_running": 1}
    assert len(find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)) == 1
    assert (
        main(["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)]) == 0
    )  # collector gone: repaired
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
    assert (
        main(
            [
                "--catalog",
                catalog_path,
                "--instrument",
                "NOVENUE",
                "--apply",
                "--candles-dir",
                _candles_dir(tmp_path),
            ]
        )
        == 1
    )
    assert error_ledger.counts() == {"repair.error": 1}


def test_an_unknown_venue_is_ledgered_and_refused(tmp_path: Path) -> None:
    """A venue `kernel.venues` does not know names a lock no collector holds: never trusted."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    assert (
        main(
            [
                "--catalog",
                catalog_path,
                "--instrument",
                "BTC-USD.NOWHERE",
                "--apply",
                "--candles-dir",
                _candles_dir(tmp_path),
            ]
        )
        == 1
    )
    assert error_ledger.counts() == {"repair.error": 1}
    assert "unknown venue 'NOWHERE'" in error_ledger.last_details()["repair.error"]


def test_a_report_takes_no_maintenance_lock(tmp_path: Path) -> None:
    catalog_path = _spiked_catalog(tmp_path)
    with maintenance(catalog_path) as held:
        assert held is not None
        assert main(["--catalog", catalog_path]) == 0
        assert (
            main(["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)])
            == 1
        )


def test_a_flagged_row_gone_by_repair_time_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row skipped as no longer stored is not repaired: the run must not report success."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    monkeypatch.setattr("archive.application.repair._rows_at", lambda *_: [])
    assert (
        main(["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)]) == 2
    )
    assert error_ledger.counts() == {"repair.error": 1}


def test_a_missing_catalog_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    assert main(["--catalog", str(tmp_path / "nope")]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}


# -- the stored verdict of a repaired day (DW-203) and the repaired files' codec (DW-260) ---------

_DAY = time.strftime("%Y-%m-%d", time.gmtime(_T0 // _SEC))


def test_a_repair_clears_the_verdict_of_the_day_it_changes_only(tmp_path: Path) -> None:
    catalog_path = _spiked_catalog(tmp_path)
    verified = _verified(tmp_path)
    verified.mark_verified(_IID, _DAY, "pass", 0, 1_000)
    verified.mark_verified(_IID, "2020-01-01", "pass", 0, 1_000)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)
    assert repair_instrument(
        ParquetDataCatalog(catalog_path), catalog_path, _IID, flagged, verified
    )
    assert verified.verified_status(_IID, _DAY) is None
    assert verified.verified_status(_IID, "2020-01-01") == "pass"
    verified.close()


class _UnwritableVerdicts:
    """A `VerifiedDays` whose store refuses the write, like a read-only or full volume."""

    def mark_verified(self, instrument_id: str, day: str, s: str, m: int, at: int) -> None:
        raise AssertionError("the repair never writes a verdict")

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        return "pass"

    def clear_verified(self, instrument_id: str, day: str) -> None:
        raise OSError(28, "No space left on device")


def test_a_verdict_that_cannot_be_cleared_leaves_the_instrument_unrepaired(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    leaf = Path(catalog_path) / "data" / "custom_dydx_second_snapshot" / _IID
    before = {p.name: p.read_bytes() for p in leaf.glob("*.parquet")}
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)
    catalog = ParquetDataCatalog(catalog_path)
    assert not repair_instrument(catalog, catalog_path, _IID, flagged, _UnwritableVerdicts())
    assert error_ledger.counts() == {"repair.error": 1}
    assert "verdict could not be cleared" in error_ledger.last_details()["repair.error"]
    assert {p.name: p.read_bytes() for p in leaf.glob("*.parquet")} == before  # nothing deleted


def test_apply_needs_the_candle_store_directory(tmp_path: Path) -> None:
    catalog_path = _spiked_catalog(tmp_path)
    with pytest.raises(SystemExit):
        main(["--catalog", catalog_path, "--apply"])
    with pytest.raises(SystemExit):  # a typo'd directory: every clear would silently find no store
        main(["--catalog", catalog_path, "--apply", "--candles-dir", str(tmp_path / "candels")])
    (tmp_path / "empty").mkdir()  # a real directory but the wrong one: it holds no store
    with pytest.raises(SystemExit):
        main(["--catalog", catalog_path, "--apply", "--candles-dir", str(tmp_path / "empty")])
    elsewhere = str(tmp_path / "elsewhere" / "candles_dydx.db")  # rebuilt here, cleared there
    candles_db = ["--candles-db", elsewhere, "--candles-dir", _candles_dir(tmp_path)]
    with pytest.raises(SystemExit):
        main(["--catalog", catalog_path, "--apply", *candles_db])
    assert len(find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)) == 1


def test_a_repair_in_a_fresh_process_writes_zstd(tmp_path: Path) -> None:
    """The patch must come from the repair's own import, not from another test's in this process."""
    catalog_path = _spiked_catalog(tmp_path)
    leaf = Path(catalog_path) / "data" / "custom_dydx_second_snapshot" / _IID
    for path in leaf.glob("*.parquet"):  # start from snappy, whatever this process defaulted to
        pq.write_table(pq.read_table(path), path, compression="snappy")
    candles = _candles_dir(tmp_path)
    argv = ["-m", "archive.repair_catalog", "--catalog", catalog_path, "--apply"]
    platform_root = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "ERROR_LEDGER_DIR"}
    done = subprocess.run(  # noqa: S603 (this interpreter, our own module)
        [sys.executable, *argv, "--candles-dir", candles],
        cwd=platform_root,
        env=env,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode()
    codecs = {
        pq.ParquetFile(path).metadata.row_group(0).column(0).compression
        for path in leaf.glob("*.parquet")
    }
    assert codecs == {"ZSTD"}
