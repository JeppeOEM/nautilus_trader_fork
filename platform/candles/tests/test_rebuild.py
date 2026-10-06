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
`python -m candles.rebuild` over a real catalog: the repair that keeps bars rebuildable.

A whole UTC day is deleted and recomputed, so running it twice must leave the store bit for bit the
same -- the property `make nightly` depends on, since it rebuilds yesterday every night.

Driven as a real subprocess, the way `nightly.py` runs it: it is an entrypoint, its argparse errors
are exit codes, and its `ProcessPoolExecutor` must fork from a process of its own (forking out of
the multi-threaded pytest process is a `DeprecationWarning`, which TEST-04 makes a failure).
"""

import json
import os
import subprocess
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

from candles.application import queries
from candles.application.rebuild import parse_date_ns
from candles.domain.fold import BAR_SECONDS
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import connect_ro
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_BYBIT_IID = "BTCUSDT-LINEAR.BYBIT"
_DAY = "2026-01-15"
_SECOND_NS = 1_000_000_000


def _snapshot(ts_event: int, price: float | None, iid: str = _IID) -> DydxSecondSnapshot:
    traded = price is not None
    return make_snapshot(
        instrument_id=InstrumentId.from_str(iid),
        bid_prices=[99.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=1.0 if traded else 0.0,
        sell_volume=0.5 if traded else 0.0,
        buy_count=1 if traded else 0,
        sell_count=1 if traded else 0,
        open_price=price,
        high_price=None if price is None else price + 0.5,
        low_price=None if price is None else price - 0.5,
        close_price=None if price is None else price + 0.1,
        ts_event=ts_event,
        ts_init=ts_event,
    )


@pytest.fixture
def catalog(tmp_path: Path) -> str:
    """Write an hour of 1 s snapshots on a closed UTC day, every third second traded."""
    day_ns = parse_date_ns(_DAY)
    rows = [
        _snapshot(day_ns + s * _SECOND_NS, 100.0 + s if s % 3 == 0 else None) for s in range(3600)
    ]
    ParquetDataCatalog(str(tmp_path / "catalog")).write_data(rows)
    return str(tmp_path / "catalog")


_PLATFORM_DIR = Path(__file__).resolve().parents[2]


def _run(argv: list[str]) -> int:
    """Run the CLI as its own process; returns the exit code."""
    return subprocess.run(  # noqa: S603 (this interpreter, our own module)
        [sys.executable, "-m", "candles.rebuild", *argv],
        cwd=str(_PLATFORM_DIR),
        env={**os.environ, "PYTHONPATH": str(_PLATFORM_DIR)},
        check=False,
        capture_output=True,
    ).returncode


def _all_bars(db_path: str) -> dict[int, list[dict]]:
    with connect_ro(db_path) as db:
        assert db is not None
        return {bar: queries.window(db, _IID, bar, 1 << 62, 10_000) for bar in BAR_SECONDS}


def test_a_day_rebuilt_twice_holds_identical_bars(catalog: str, tmp_path: Path) -> None:
    db = str(tmp_path / "candles_dydx.db")
    args = ["--catalog", catalog, "--db", db, "--day", _DAY, "--venue", "DYDX", "--workers", "1"]
    assert _run(args) == 0
    once = _all_bars(db)
    assert _run(args) == 0
    assert _all_bars(db) == once


def test_the_rebuilt_bars_carry_the_traded_seconds_of_the_day(catalog: str, tmp_path: Path) -> None:
    db = str(tmp_path / "candles_dydx.db")
    assert (
        _run(["--catalog", catalog, "--db", db, "--day", _DAY, "--venue", "DYDX", "--workers", "1"])
        == 0
    )
    minutes = _all_bars(db)[60]
    assert len(minutes) == 60
    assert minutes[0]["t"] == parse_date_ns(_DAY) // 1_000_000
    assert minutes[0]["seconds_observed"] == 60
    assert minutes[0]["o"] == 100.0


def test_the_open_day_is_refused_unless_asked_for(tmp_path: Path) -> None:
    """Rebuilding today would delete seconds the running collector applied but has not flushed."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    day_ns = parse_date_ns(today)
    catalog_path = str(tmp_path / "catalog")
    ParquetDataCatalog(catalog_path).write_data(
        [_snapshot(day_ns + s * _SECOND_NS, 100.0 + s) for s in range(120)]
    )
    db = str(tmp_path / "candles_dydx.db")
    args = [
        "--catalog",
        catalog_path,
        "--db",
        db,
        "--day",
        today,
        "--venue",
        "DYDX",
        "--workers",
        "1",
    ]
    assert _run(args) == 0
    assert _all_bars(db)[60] == []
    assert _run([*args, "--include-open-day"]) == 0
    assert len(_all_bars(db)[60]) == 2


def test_a_venue_filter_keeps_only_that_venues_ids(catalog: str, tmp_path: Path) -> None:
    db = str(tmp_path / "candles_bybit.db")
    assert (
        _run(
            ["--catalog", catalog, "--db", db, "--day", _DAY, "--venue", "BYBIT", "--workers", "1"]
        )
        == 0
    )
    store = CandleStore(db)
    assert dict(store.watermarks()) == {}
    store.close()


def test_a_candles_dir_rebuild_writes_the_venues_own_store(catalog: str, tmp_path: Path) -> None:
    """DW-194: `make build-candles VENUE=V` folds only V's ids, into `candles_<v>.db`."""
    day_ns = parse_date_ns(_DAY)
    ParquetDataCatalog(catalog).write_data(
        [_snapshot(day_ns + s * _SECOND_NS, 50.0, _BYBIT_IID) for s in range(120)]
    )
    candles_dir = tmp_path / "candles_dir"
    candles_dir.mkdir()
    argv = ["--catalog", catalog, "--candles-dir", str(candles_dir), "--day", _DAY]

    assert _run([*argv, "--venue", "DYDX", "--workers", "1"]) == 0
    assert _run([*argv, "--venue", "BYBIT", "--workers", "1"]) == 0

    assert sorted(path.name for path in candles_dir.glob("*.db")) == [
        "candles_bybit.db",
        "candles_dydx.db",
    ]
    dydx = CandleStore(str(candles_dir / "candles_dydx.db"))
    assert list(dydx.watermarks()) == [_IID]
    dydx.close()
    bybit = CandleStore(str(candles_dir / "candles_bybit.db"))
    assert list(bybit.watermarks()) == [_BYBIT_IID]
    bybit.close()


def test_a_candles_dir_without_a_venue_is_refused(catalog: str, tmp_path: Path) -> None:
    candles_dir = tmp_path / "candles_dir"
    candles_dir.mkdir()
    argv = ["--catalog", catalog, "--candles-dir", str(candles_dir), "--day", _DAY]

    assert _run(argv) == 2  # argparse's usage error, before any store is opened
    assert list(candles_dir.iterdir()) == []


def test_a_candles_dir_that_does_not_exist_is_refused(catalog: str, tmp_path: Path) -> None:
    """A wrong mount must not create a fresh store under a path nothing reads."""
    missing = tmp_path / "no_such_dir"
    argv = ["--catalog", catalog, "--candles-dir", str(missing), "--venue", "DYDX", "--day", _DAY]

    assert _run(argv) == 2
    assert not missing.exists()


def test_an_empty_candles_dir_is_refused_not_the_cwd(
    catalog: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    argv = ["--catalog", catalog, "--candles-dir", "", "--venue", "DYDX", "--day", _DAY]

    assert _run(argv) == 2
    assert list(tmp_path.glob("*.db")) == []


@pytest.mark.parametrize("given", ["neither", "both"])
def test_exactly_one_store_option_is_required(given: str, catalog: str, tmp_path: Path) -> None:
    both = ["--db", str(tmp_path / "c.db"), "--candles-dir", str(tmp_path)]
    store = both if given == "both" else []
    argv = ["--catalog", catalog, *store, "--venue", "DYDX", "--day", _DAY]

    assert _run(argv) == 2  # argparse's mutually exclusive, required group
    assert list(tmp_path.glob("*.db")) == []


@pytest.mark.parametrize("venue", ["Bybit", "BINANCE"])
def test_an_unknown_venue_is_refused(venue: str, catalog: str, tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    argv = ["--catalog", catalog, "--db", str(db), "--day", _DAY, "--venue", venue]

    assert _run(argv) == 2  # argparse's choices error, not a run that rebuilds nothing
    assert not db.exists()


def test_day_cannot_be_combined_with_a_range(catalog: str, tmp_path: Path) -> None:
    argv = ["--catalog", catalog, "--db", str(tmp_path / "c.db"), "--day", _DAY, "--start", _DAY]
    assert _run(argv) == 2  # argparse's usage error


def test_a_rebuild_opens_its_own_durable_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Story 31.8: the nightly saga runs this as a child process that never opened a durable ledger,
    so its failures reached stdout only. It ledgers under `archive.candles_rebuild`, never the
    scheduler's own `archive.jsonl` (whose since-restart window a child's start would reset).
    """
    errors = tmp_path / "errors"
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(errors))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "archive")  # the scheduler's, inherited
    (tmp_path / "empty").mkdir()
    argv = ["--catalog", str(tmp_path / "empty"), "--db", str(tmp_path / "c.db"), "--day", _DAY]

    assert _run(argv) == 0  # an existing catalog with nothing to rebuild: the cheapest real run

    lines = (errors / "archive.candles_rebuild.jsonl").read_text().splitlines()
    assert [json.loads(line)["site"] for line in lines] == ["process_start"]
    assert not (errors / "archive.jsonl").exists()


def test_a_missing_catalog_is_refused_not_an_empty_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A catalog root that does not exist (a wrong mount, a typo) once listed as "no instruments" and
    exited 0 over nothing, creating an empty store. It is refused at the archive tools' site, exit
    1, and no store is created (audit D-123).
    """
    errors = tmp_path / "errors"
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(errors))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "archive")
    db = tmp_path / "c.db"
    argv = ["--catalog", str(tmp_path / "absent"), "--db", str(db), "--day", _DAY]

    assert _run(argv) == 1

    lines = [
        json.loads(line)
        for line in (errors / "archive.candles_rebuild.jsonl").read_text().splitlines()
    ]
    assert [line["site"] for line in lines] == ["process_start", "archive.catalog_missing"]
    assert "candles_rebuild" in lines[1]["detail"]
    assert not db.exists()
