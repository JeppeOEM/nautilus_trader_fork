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
`archive.repair_catalog`: impossible-OHLC seconds cleared in place (staged, verified, renamed),
gated by the capture lock, the open day and the trade archive's coverage.
"""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from candles.application import queries
from candles.application.rebuild import rebuild_instrument
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import connect_rw
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.verified_days import VerifiedDaysDir
from kernel.archive_markers import ArchiveGap
from kernel.archive_markers import capture_lock_path
from kernel.archive_markers import path_for
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from archive.application.ports import RewriteMode
from archive.application.ports import StagedRewrite
from archive.application.repair import closed_rows
from archive.application.repair import coverage_start
from archive.application.repair import find_impossible_snapshots
from archive.application.repair import repair_instrument
from archive.application.repair import second_snapshots
from archive.application.repair import uncovered_rows
from archive.infrastructure import catalog_files
from archive.infrastructure.catalog_files import CatalogFiles
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.maintenance_lock import maintenance
from archive.repair_catalog import main
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename


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

    repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path), db_path)

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
    """The row is located by its own `(ts_init, ts_event)`: matching `ts_event` alone would miss."""
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    snaps = [_snap(s, init_second=s + 3) for s in range(10)]
    snaps[5] = _snap(5, high=150.0, low=50.0, init_second=8)
    catalog.write_data(snaps)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [f.ts_init for f in flagged] == [_T0 + 8 * _SEC]

    repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC) == []
    assert _second_events(catalog_path, 10) == [_T0 + s * _SEC for s in range(10)]


def test_an_unflagged_row_sharing_the_flagged_ts_init_survives_unchanged(tmp_path: Path) -> None:
    """Caught-up seconds share their wake-up's `ts_init`; only the flagged one is cleared."""
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    snaps = [_snap(s) for s in range(5)]
    sibling = _snap(6, high=100.5, low=100.5, init_second=7)  # a real trade inside the book
    snaps += [_snap(5, high=150.0, low=50.0, init_second=7), sibling]
    catalog.write_data(snaps)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [f.ts_event for f in flagged] == [_T0 + 5 * _SEC]

    repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    rows = {r.ts_event: r for r in second_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)}
    assert _second_events(catalog_path, 7) == [_T0 + s * _SEC for s in range(7)]
    assert rows[_T0 + 5 * _SEC].high_price is None
    assert DydxSecondSnapshot.to_dict(rows[sibling.ts_event]) == DydxSecondSnapshot.to_dict(sibling)


def test_a_pre_fix_repairs_leftover_copy_collapses_into_one_cleared_row(tmp_path: Path) -> None:
    """An earlier repair could miss its delete and leave a cleared copy beside the original."""
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    spike = _snap(5, high=150.0, low=50.0, init_second=8)
    leftover = _snap(5, init_second=8)  # the old repair's cleared copy, same clocks
    catalog.write_data([*[_snap(s) for s in range(5)], spike, leftover])
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)

    assert repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert _second_events(catalog_path, 6) == [_T0 + s * _SEC for s in range(6)]
    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC) == []
    assert error_ledger.counts() == {"repair.duplicate": 1}  # collapsing it is never silent


def test_a_flagged_row_no_longer_stored_is_ledgered_and_nothing_is_changed(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data([_snap(s) for s in range(5)])
    gone = _snap(30, high=150.0, low=50.0, init_second=3)  # ts_init 3 holds only second 3

    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, [gone], _verified(tmp_path))

    assert error_ledger.counts() == {"repair.error": 1}
    assert _second_events(catalog_path, 5) == [_T0 + s * _SEC for s in range(5)]
    assert "flagged row not stored" in error_ledger.last_details()["repair.error"]


def _leaf(catalog_path: str) -> Path:
    return Path(catalog_path) / "data" / "custom_dydx_second_snapshot" / _IID


def _files(catalog_path: str) -> dict[str, bytes]:
    """Every snapshot file of the instrument, by name, with its bytes."""
    return {p.name: p.read_bytes() for p in sorted(_leaf(catalog_path).glob("*.parquet"))}


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
    """The repair replaces the whole containing file, and that one is capture's."""
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
    gone = _snap(30, high=150.0, low=50.0, init_second=3)  # ts_init 3 holds only second 3
    monkeypatch.setattr("archive.repair_catalog.find_impossible_snapshots", lambda *_: [gone])
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
    assert repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, verified)
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
    leaf = _leaf(catalog_path)
    before = _files(catalog_path)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _UnwritableVerdicts())
    assert error_ledger.counts() == {"repair.error": 1}
    assert "verdict could not be cleared" in error_ledger.last_details()["repair.error"]
    assert _files(catalog_path) == before  # staged temps discarded, nothing renamed
    assert not list(leaf.glob("*.archive.tmp"))


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
    """The repaired file is written by `CatalogFiles` (compact zstd), whatever it was before."""
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


# -- the in-place rewrite: staged, verified, renamed (DW-204) ---------------------------------------


def _rows_by_event(catalog_path: str, n: int) -> dict[int, dict]:
    rows = second_snapshots(catalog_path, _IID, _T0 - 10 * _SEC, _T0 + (n + 10) * _SEC)
    return {r.ts_event: DydxSecondSnapshot.to_dict(r) for r in rows}


def test_the_repaired_row_is_cleared_in_its_own_file_and_every_other_row_kept(
    tmp_path: Path,
) -> None:
    catalog_path = _spiked_catalog(tmp_path)
    names = set(_files(catalog_path))
    before = _rows_by_event(catalog_path, 5)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)

    assert repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert set(_files(catalog_path)) == names  # same file: nothing renamed, split or added
    after = _rows_by_event(catalog_path, 5)
    spike = _T0 + 2 * _SEC
    assert {k: v for k, v in after.items() if k != spike} == {
        k: v for k, v in before.items() if k != spike
    }
    assert after[spike]["high_price"] is None
    assert after[spike]["buy_volume"] == 0
    assert after[spike]["bid_prices"] == before[spike]["bid_prices"]  # the book is untouched


def test_a_leftover_copy_in_its_own_file_is_dropped_and_the_emptied_file_removed(
    tmp_path: Path,
) -> None:
    """Two files can share a `ts_init` only when a tool outside the catalog wrote one."""
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    spike = _snap(5, high=150.0, low=50.0)
    ParquetDataCatalog(catalog_path).write_data([*[_snap(s) for s in range(5)], spike])
    (original,) = _files(catalog_path)
    leftover = pq.read_table(_leaf(catalog_path) / original).slice(5, 1)
    name = _timestamps_to_filename(spike.ts_init, spike.ts_init)
    pq.write_table(leftover, _leaf(catalog_path) / name)  # the spike's own copy, sorted after

    assert repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))

    assert set(_files(catalog_path)) == {original}
    assert error_ledger.counts() == {"repair.duplicate": 1}
    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC) == []


def test_a_copy_that_differs_beyond_the_trade_columns_is_refused_not_collapsed(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    spike = _snap(5, high=150.0, low=50.0, init_second=8)
    other_book = make_snapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[100.0, 99.0],
        bid_sizes=[2.0, 1.0],  # another book at the same clocks: not the same second's row
        ask_prices=[101.0, 102.0],
        ask_sizes=[1.0, 1.0],
        ts_event=spike.ts_event,
        ts_init=spike.ts_init,
    )
    ParquetDataCatalog(catalog_path).write_data([*[_snap(s) for s in range(5)], spike, other_book])
    before = _files(catalog_path)

    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))

    assert error_ledger.counts() == {"repair.error": 1}
    assert "differ beyond the trade columns" in error_ledger.last_details()["repair.error"]
    assert _files(catalog_path) == before


def _two_spiked_files(tmp_path: Path) -> tuple[str, list[DydxSecondSnapshot]]:
    """Two closed files, each holding one flagged row."""
    catalog_path = str(tmp_path / "cat")
    catalog = ParquetDataCatalog(catalog_path)
    first = [_snap(s) for s in range(5)]
    first[2] = _snap(2, high=150.0, low=50.0)
    second = [_snap(s) for s in range(10, 15)]
    second[2] = _snap(12, high=150.0, low=50.0)
    catalog.write_data(first)
    catalog.write_data(second)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert len(_files(catalog_path)) == 2
    assert len(flagged) == 2
    return catalog_path, flagged


def _stored_events(catalog_path: str) -> list[int]:
    return sorted(_rows_by_event(catalog_path, 20))


def test_a_rename_failing_part_way_keeps_every_file_whole_and_every_second_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    catalog_path, flagged = _two_spiked_files(tmp_path)
    events = _stored_events(catalog_path)
    first, second = sorted(_files(catalog_path))
    before = _files(catalog_path)
    renames = 0
    real_replace = os.replace

    def _second_rename_fails(src: str, dst: str) -> None:
        nonlocal renames
        renames += 1
        if renames == 2:
            raise OSError(5, "Input/output error")
        real_replace(src, dst)

    monkeypatch.setattr("archive.infrastructure.catalog_files.os.replace", _second_rename_fails)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert _stored_events(catalog_path) == events  # every second still stored, cleared or not
    after = _files(catalog_path)
    assert after[second] != before[second]  # renamed first: reverse path order
    assert after[first] == before[first]  # kept whole, old content
    assert not list(_leaf(catalog_path).glob("*.archive.tmp"))
    assert error_ledger.counts() == {"repair.error": 1}
    assert "PARTIALLY repaired" in error_ledger.last_details()["repair.error"]
    monkeypatch.undo()
    assert len(find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)) == 1
    rest = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert repair_instrument(CatalogFiles(), catalog_path, _IID, rest, _verified(tmp_path))
    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC) == []


def _copies(catalog_path: str, ts_event: int) -> int:
    rows = second_snapshots(catalog_path, _IID, _T0 - 10 * _SEC, _T0 + 30 * _SEC)
    return sum(1 for r in rows if r.ts_event == ts_event)


def _catalog_with_a_leftover_beside_a_later_row(tmp_path: Path) -> tuple[str, DydxSecondSnapshot]:
    """
    Return a catalog whose original file holds the spike and whose later-named file holds an
    earlier repair's cleared copy of it beside an unrelated row (the drop rewrites, not empties).
    """
    catalog_path = str(tmp_path / "cat")
    spike = _snap(5, high=150.0, low=50.0)
    ParquetDataCatalog(catalog_path).write_data([*[_snap(s) for s in range(5)], spike])
    other = str(tmp_path / "other")
    ParquetDataCatalog(other).write_data([_snap(5), _snap(6)])
    (written,) = _leaf(other).glob("*.parquet")
    written.rename(_leaf(catalog_path) / written.name)
    return catalog_path, spike


def test_a_partial_commit_leaves_the_flagged_original_so_a_rerun_completes_the_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The kept copy's file is renamed last: had it been renamed first, its cleared row and the
    leftover's would both be unflagged, and no rerun would ever find the duplicate.
    """
    error_ledger.reset()
    catalog_path, spike = _catalog_with_a_leftover_beside_a_later_row(tmp_path)
    original, later = sorted(_files(catalog_path))
    before = _files(catalog_path)
    renames = 0
    real_replace = os.replace

    def _second_rename_fails(src: str, dst: str) -> None:
        nonlocal renames
        renames += 1
        if renames == 2:
            raise OSError(5, "Input/output error")
        real_replace(src, dst)

    monkeypatch.setattr("archive.infrastructure.catalog_files.os.replace", _second_rename_fails)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))

    after = _files(catalog_path)
    assert after[later] != before[later]  # the leftover dropped
    assert after[original] == before[original]  # the flagged original still stored
    monkeypatch.undo()
    rest = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [r.ts_event for r in rest] == [spike.ts_event]
    assert repair_instrument(CatalogFiles(), catalog_path, _IID, rest, _verified(tmp_path))
    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC) == []
    assert _copies(catalog_path, spike.ts_event) == 1
    assert _stored_events(catalog_path) == [_T0 + s * _SEC for s in range(7)]


def test_an_emptied_file_that_cannot_be_removed_leaves_every_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    spike = _snap(5, high=150.0, low=50.0)
    ParquetDataCatalog(catalog_path).write_data([*[_snap(s) for s in range(5)], spike])
    (original,) = _files(catalog_path)
    leftover = pq.read_table(_leaf(catalog_path) / original).slice(5, 1)
    name = _timestamps_to_filename(spike.ts_init, spike.ts_init)
    pq.write_table(leftover, _leaf(catalog_path) / name)
    before = _files(catalog_path)

    real_unlink = catalog_files._unlink_if_present

    def _cannot_unlink_a_data_file(path: Path) -> None:
        if path.name.endswith(".parquet"):  # the emptied file; a temp still unlinks
            raise OSError(13, "Permission denied")
        real_unlink(path)

    monkeypatch.setattr(catalog_files, "_unlink_if_present", _cannot_unlink_a_data_file)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))

    assert _files(catalog_path) == before  # nothing renamed before the removal
    assert not list(_leaf(catalog_path).glob("*.archive.tmp"))
    assert error_ledger.counts() == {"repair.duplicate": 1, "repair.error": 1}
    assert "nothing renamed" in error_ledger.last_details()["repair.error"]


def test_an_interrupt_during_the_candle_rebuild_is_ledgered_with_the_days_to_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rows are committed: a rerun no longer flags them, so the ledger is the only trace."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)

    def _interrupted(*args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("archive.application.repair.rebuild_instrument", _interrupted)
    db = str(tmp_path / "candles" / "candles_dydx.db")
    with pytest.raises(KeyboardInterrupt):
        repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path), db)

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC) == []
    assert error_ledger.counts() == {"repair.error": 1}
    detail = error_ledger.last_details()["repair.error"]
    assert "candles.rebuild" in detail
    assert _DAY in detail


def test_a_foreign_named_snapshot_file_keeps_the_repair_incomplete(tmp_path: Path) -> None:
    """The catalog's query (so the detector) reads the stray copy; the repair cannot reach it."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    (path,) = _leaf(catalog_path).glob("*.parquet")
    (_leaf(catalog_path) / "stray.parquet").write_bytes(path.read_bytes())
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)

    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert pq.read_table(path).column("high_price").to_pylist()[2] is None  # named copy cleared
    assert error_ledger.counts() == {"catalog.foreign_file": 1}


def test_a_first_rename_failure_leaves_every_file_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    catalog_path, flagged = _two_spiked_files(tmp_path)
    before = _files(catalog_path)

    def _no_rename(src: str, dst: str) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("archive.infrastructure.catalog_files.os.replace", _no_rename)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert _files(catalog_path) == before
    assert not list(_leaf(catalog_path).glob("*.archive.tmp"))
    assert error_ledger.counts() == {"repair.error": 1}
    assert "the first rename failed" in error_ledger.last_details()["repair.error"]


def test_a_first_rename_failure_after_an_emptied_file_was_removed_names_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The leftover's own file is removed before the renames: the ledger must not claim it kept."""
    error_ledger.reset()
    catalog_path = str(tmp_path / "cat")
    spike = _snap(5, high=150.0, low=50.0)
    ParquetDataCatalog(catalog_path).write_data([*[_snap(s) for s in range(5)], spike])
    (original,) = _files(catalog_path)
    leftover = pq.read_table(_leaf(catalog_path) / original).slice(5, 1)
    name = _timestamps_to_filename(spike.ts_init, spike.ts_init)
    pq.write_table(leftover, _leaf(catalog_path) / name)
    before = _files(catalog_path)

    def _no_rename(src: str, dst: str) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("archive.infrastructure.catalog_files.os.replace", _no_rename)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))

    assert _files(catalog_path) == {original: before[original]}  # the flagged original kept
    detail = error_ledger.last_details()["repair.error"]
    assert "every file kept as it was" not in detail
    assert f"{name}'] were already removed" in detail
    monkeypatch.undo()
    assert repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))
    assert _copies(catalog_path, spike.ts_event) == 1


def test_a_failed_directory_fsync_after_the_renames_is_ledgered_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)

    def _no_fsync(directory: Path) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(catalog_files, "_fsync_dir", _no_fsync)
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert error_ledger.counts() == {"repair.error": 1}
    assert "durability unproven" in error_ledger.last_details()["repair.error"]


def test_a_failing_candle_rebuild_is_ledgered_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception, unlike an interrupt, must not stop the run's later instruments."""
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)

    def _locked(*args: object) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr("archive.application.repair.rebuild_instrument", _locked)
    db = str(tmp_path / "candles" / "candles_dydx.db")
    assert not repair_instrument(
        CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path), db
    )

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC) == []
    assert error_ledger.counts() == {"repair.error": 1}
    detail = error_ledger.last_details()["repair.error"]
    assert "candles.rebuild" in detail
    assert _DAY in detail


def test_a_flagged_row_only_in_a_foreign_named_file_says_so(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    (path,) = _leaf(catalog_path).glob("*.parquet")
    path.rename(_leaf(catalog_path) / "stray.parquet")
    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 10 * _SEC)
    assert len(flagged) == 1  # the catalog's query still reads the stray file

    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert error_ledger.counts() == {"catalog.foreign_file": 1, "repair.error": 1}
    assert "foreign-named file(s) skipped" in error_ledger.last_details()["repair.error"]


def test_a_failed_commit_still_rebuilds_the_candles_of_the_rows_it_may_have_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerun only rebuilds the days of rows still flagged: a renamed file's days are now."""
    error_ledger.reset()
    catalog_path, flagged = _two_spiked_files(tmp_path)
    rebuilt: list[tuple[int, int]] = []
    real_replace = os.replace
    renames = 0

    def _second_rename_fails(src: str, dst: str) -> None:
        nonlocal renames
        renames += 1
        if renames == 2:
            raise OSError(5, "Input/output error")
        real_replace(src, dst)

    def _record(db: str, catalog: str, iid: str, start: int, end: int) -> None:
        rebuilt.append((start, end))

    monkeypatch.setattr("archive.infrastructure.catalog_files.os.replace", _second_rename_fails)
    monkeypatch.setattr("archive.application.repair.rebuild_instrument", _record)
    db = str(tmp_path / "candles" / "candles_dydx.db")
    assert not repair_instrument(
        CatalogFiles(), catalog_path, _IID, flagged, _verified(tmp_path), db
    )

    assert rebuilt == [(_T0 + 2 * _SEC, _T0 + 12 * _SEC)]
    assert error_ledger.counts() == {"repair.error": 1}


def test_a_temp_failing_its_read_back_leaves_every_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second file's temp fails verification: the first, already staged, is discarded too."""
    error_ledger.reset()
    catalog_path, flagged = _two_spiked_files(tmp_path)
    before = _files(catalog_path)
    verified = _verified(tmp_path)
    verified.mark_verified(_IID, _DAY, "pass", 0, 1_000)
    checks = 0

    def _second_mismatches(path: Path, table: pa.Table) -> str | None:
        nonlocal checks
        checks += 1
        return "column 'high_price', row group 0" if checks == 2 else None

    monkeypatch.setattr(
        "archive.infrastructure.catalog_files._read_back_mismatch", _second_mismatches
    )
    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, flagged, verified)

    assert _files(catalog_path) == before
    assert not list(_leaf(catalog_path).glob("*.archive.tmp"))
    assert error_ledger.counts() == {"repair.error": 1}
    assert "RewriteVerifyError" in error_ledger.last_details()["repair.error"]
    assert verified.verified_status(_IID, _DAY) == "pass"  # nothing changed: the verdict stands
    verified.close()


class _InterruptedStaging(CatalogFiles):
    """A `CatalogWriter` interrupted (Ctrl-C) while staging the second file."""

    def __init__(self) -> None:
        super().__init__()
        self.staged = 0

    def stage_rewrite(self, path: Path, table: pa.Table, mode: RewriteMode) -> StagedRewrite:
        self.staged += 1
        if self.staged == 2:
            raise KeyboardInterrupt
        return super().stage_rewrite(path, table, mode)


def test_an_interrupt_before_the_commit_discards_the_temps_and_propagates(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    catalog_path, flagged = _two_spiked_files(tmp_path)
    before = _files(catalog_path)

    with pytest.raises(KeyboardInterrupt):
        repair_instrument(_InterruptedStaging(), catalog_path, _IID, flagged, _verified(tmp_path))

    assert _files(catalog_path) == before
    assert not list(_leaf(catalog_path).glob("*.archive.tmp"))


def test_a_float_layout_file_refuses_the_instrument(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    (path,) = _leaf(catalog_path).glob("*.parquet")
    pq.write_table(pq.read_table(path).drop_columns(["price_precision", "size_precision"]), path)
    before = _files(catalog_path)
    spike = _snap(2, high=150.0, low=50.0)

    assert not repair_instrument(CatalogFiles(), catalog_path, _IID, [spike], _verified(tmp_path))

    assert error_ledger.counts() == {"repair.error": 1}
    assert "migrate_snapshot_ints" in error_ledger.last_details()["repair.error"]
    assert _files(catalog_path) == before


# -- only pre-archive rows are repaired (DW-206) ----------------------------------------------------


def test_uncovered_rows_keeps_the_pre_archive_rows_and_ledgers_the_rest_once() -> None:
    error_ledger.reset()
    flagged = [_snap(s, high=150.0, low=50.0) for s in (1, 4, 5, 9)]

    kept = uncovered_rows(_IID, flagged, _T0 + 5 * _SEC)

    assert [s.ts_event for s in kept] == [_T0 + 1 * _SEC, _T0 + 4 * _SEC]
    assert error_ledger.counts() == {"repair.covered": 1}
    detail = error_ledger.last_details()["repair.covered"]
    assert f"2 flagged row(s), ts_event {_T0 + 5 * _SEC}..{_T0 + 9 * _SEC}" in detail


def test_the_coverage_start_is_the_earliest_of_the_stored_trades_and_the_markers() -> None:
    assert coverage_start(None, []) is None
    assert coverage_start(500, []) == 500
    assert coverage_start(500, [(800, 900), (300, 400)]) == 300  # a pruned day stays covered
    assert coverage_start(None, [(300, 400)]) == 300  # every trade file pruned
    assert coverage_start(200, [(300, 400)]) == 200


def test_no_archive_leaves_every_flagged_row_repairable() -> None:
    error_ledger.reset()
    flagged = [_snap(s, high=150.0, low=50.0) for s in (1, 9)]
    assert uncovered_rows(_IID, flagged, None) == flagged
    assert error_ledger.counts() == {}


def _archive_trade(catalog_path: str, second: int) -> None:
    """One real archived trade of the instrument at `_T0 + second` (its coverage start)."""
    trade = TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str("100.5"),
        Quantity.from_str("0.1"),
        AggressorSide.BUYER,
        TradeId("t-1"),
        _T0 + second * _SEC,
        _T0 + second * _SEC,
    )
    ParquetDataCatalog(catalog_path).write_data([trade])


def _partially_covered_catalog(tmp_path: Path) -> str:
    """Spikes at seconds 2 and 7; the trade archive starts at second 5."""
    catalog_path = str(tmp_path / "cat")
    snaps = [_snap(s) for s in range(10)]
    snaps[2] = _snap(2, high=150.0, low=50.0)
    snaps[7] = _snap(7, high=150.0, low=50.0)
    ParquetDataCatalog(catalog_path).write_data(snaps)
    _archive_trade(catalog_path, 5)
    return catalog_path


def test_a_covered_row_is_refused_and_the_pre_archive_one_repaired(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog_path = _partially_covered_catalog(tmp_path)
    before = _rows_by_event(catalog_path, 10)

    assert main(["--catalog", catalog_path]) == 0  # the report ledgers nothing
    assert error_ledger.counts() == {}
    applied = ["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)]
    assert main(applied) == 2

    assert error_ledger.counts() == {"repair.covered": 1}
    assert "1 flagged row(s)" in error_ledger.last_details()["repair.covered"]
    after = _rows_by_event(catalog_path, 10)
    assert after[_T0 + 2 * _SEC]["high_price"] is None  # pre-archive: repaired
    assert after[_T0 + 7 * _SEC] == before[_T0 + 7 * _SEC]  # covered: untouched
    remaining = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 20 * _SEC)
    assert [s.ts_event for s in remaining] == [_T0 + 7 * _SEC]


def test_an_unreadable_trade_archive_refuses_the_instrument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    before = _files(catalog_path)

    def _unreadable(*_: object) -> int | None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("archive.repair_catalog.covered_from", _unreadable)
    assert main(["--catalog", catalog_path]) == 0  # a report only warns
    assert error_ledger.counts() == {}
    applied = ["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)]
    assert main(applied) == 2

    assert error_ledger.counts() == {"repair.error": 1}
    assert "trade archive could not be read" in error_ledger.last_details()["repair.error"]
    assert _files(catalog_path) == before


def test_a_pruned_days_rows_stay_covered_after_its_trades_are_gone(tmp_path: Path) -> None:
    """Prune deletes the earliest trade files: their `pruned` marker keeps the start in place."""
    error_ledger.reset()
    catalog_path = _partially_covered_catalog(tmp_path)  # stored trades now start at second 5
    pruned = ArchiveGap(_IID, _T0 + 1 * _SEC, _T0 + 3 * _SEC, "pruned", 0)
    assert GapMarkerFiles(catalog_path).record(pruned)
    before = _rows_by_event(catalog_path, 10)

    applied = ["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)]
    assert main(applied) == 2

    assert error_ledger.counts() == {"repair.covered": 1}
    assert "2 flagged row(s)" in error_ledger.last_details()["repair.covered"]
    assert _rows_by_event(catalog_path, 10) == before  # second 2 lies in the pruned span


def test_a_malformed_marker_file_refuses_the_instrument(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog_path = _spiked_catalog(tmp_path)
    marker = path_for(catalog_path, _IID)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{not json\n")
    before = _files(catalog_path)

    applied = ["--catalog", catalog_path, "--apply", "--candles-dir", _candles_dir(tmp_path)]
    assert main(applied) == 2

    assert error_ledger.counts() == {"repair.error": 1}
    assert _files(catalog_path) == before
