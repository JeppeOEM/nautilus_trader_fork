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
The collector flushes Parquet on the wall clock and folds exactly what was flushed into the candle
store, so the store is never ahead of the archive; at startup it catches up anything it missed.
"""

import asyncio
from pathlib import Path

import pytest
from collector_core.collector import _seconds_until_next_flush
from kernel.second_snapshot import DydxSecondSnapshot
from ml_signals import candle_store

from dydx_collector.collector import DydxCollector
from dydx_collector.tests.test_collector_trade_ohlc import _make_config
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_T0 = 1_800_000_000 * 1_000_000_000  # a UTC minute boundary


def _snap(i: int) -> DydxSecondSnapshot:
    ts = _T0 + i * 1_000_000_000
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[100.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=100.0 + i,
        high_price=101.0 + i,
        low_price=99.0,
        close_price=100.5 + i,
        ts_event=ts,
        ts_init=ts,
    )


def _collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DydxCollector:
    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "candles.db"))
    return DydxCollector(_make_config(tmp_path / "catalog"))


def test_flush_happens_two_seconds_past_each_interval() -> None:
    at = lambda sec, interval=60.0: round(_seconds_until_next_flush(6000.0 + sec, interval), 6)  # noqa: E731
    assert at(0.0) == 2.0
    assert at(2.0) == 60.0  # exactly on a flush second: wait for the next one
    assert at(1.5) == 0.5
    assert at(59.9) == 2.1
    assert at(0.0, 30.0) == 2.0


def test_flush_writes_parquet_then_applies_exactly_that_to_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = _collector(tmp_path, monkeypatch)
    snaps = [_snap(i) for i in range(30)]
    collector._buffer[(DydxSecondSnapshot, str(snaps[0].instrument_id))] = snaps

    asyncio.run(collector._flush_once())

    bar = candle_store.window(collector._candle_db, _IID, 60, 1 << 62, 5)[0]
    assert (bar["o"], bar["c"], bar["seconds_observed"]) == (100.0, 129.5, 30)
    assert bar["v"] == 30 * 1.5


def test_a_failed_parquet_write_never_reaches_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = _collector(tmp_path, monkeypatch)
    snaps = [_snap(i) for i in range(30)]
    collector._buffer[(DydxSecondSnapshot, str(snaps[0].instrument_id))] = snaps

    def boom(_items: list) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(collector._catalog, "write_data", boom)
    asyncio.run(collector._flush_once())

    assert (
        candle_store.window(collector._candle_db, _IID, 60, 1 << 62, 5) == []
    )  # store never ahead of the archive


def test_startup_catch_up_applies_seconds_the_archive_has_beyond_the_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector(tmp_path, monkeypatch)
    snaps = [_snap(i) for i in range(60)]
    ParquetDataCatalog(str(tmp_path / "catalog")).write_data(snaps)
    candle_store.apply_seconds(
        collector._candle_db, _IID, snaps[:40]
    )  # crashed before the rest was applied
    monkeypatch.setattr("dydx_collector.collector.time.time_ns", lambda: _T0 + 3600 * 1_000_000_000)

    collector._catch_up_candle_store()

    bar = candle_store.window(collector._candle_db, _IID, 60, 1 << 62, 5)[0]
    assert (bar["c"], bar["seconds_observed"]) == (159.5, 60)
