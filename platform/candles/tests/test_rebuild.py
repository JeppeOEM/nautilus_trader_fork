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

import os
import subprocess
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest
from kernel.second_snapshot import DydxSecondSnapshot

from candles.application import queries
from candles.application.rebuild import parse_date_ns
from candles.domain.fold import BAR_SECONDS
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import connect_ro
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_DAY = "2026-01-15"
_SECOND_NS = 1_000_000_000


def _snapshot(ts_event: int, price: float | None) -> DydxSecondSnapshot:
    traded = price is not None
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
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


def test_day_cannot_be_combined_with_a_range(catalog: str, tmp_path: Path) -> None:
    argv = ["--catalog", catalog, "--db", str(tmp_path / "c.db"), "--day", _DAY, "--start", _DAY]
    assert _run(argv) == 2  # argparse's usage error
