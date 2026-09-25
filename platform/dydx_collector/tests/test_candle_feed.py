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
The wiring this venue entrypoint does, end to end (Story 24.1).

It is the composition root: it builds the `CandleStore`, hands capture a `CandleSink` for the
`SecondSink` port and starts the retention loop. So this is where the port and its adapter are
exercised together -- the collector flushes Parquet on the wall clock and folds exactly what was
flushed into the store, so the store is never ahead of the archive, and at startup it catches up
anything it missed.
"""

import asyncio
from pathlib import Path

import pytest
from candles.application import queries
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import connect_ro
from collector_core.collector import _seconds_until_next_flush
from collector_core.ports import SecondSink
from kernel.second_snapshot import DydxSecondSnapshot

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


def _pruned_store(loops: object) -> object:
    """
    Return the store `candles.application.prune.loop`'s closure captured, or None if absent.

    Matching only on `__name__` would pass a venue that started *another* venue's prune loop (or two
    venues sharing one store object) -- the copy-paste mistake this test exists to catch. The
    captured cell is the only evidence of which file the loop actually prunes.
    """
    for loop in loops:  # type: ignore[attr-defined]
        if getattr(loop, "__name__", "") != "prune_loop":
            continue
        names = loop.__code__.co_freevars
        return loop.__closure__[names.index("store")].cell_contents
    return None


def _collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[DydxCollector, str]:
    db_path = str(tmp_path / "candles.db")
    monkeypatch.setenv("CANDLES_DB_PATH", db_path)
    return DydxCollector(_make_config(tmp_path / "catalog")), db_path


def _bars(db_path: str, bar_seconds: int = 60) -> list[dict]:
    """Read the store back the way `data_api` does: through its own read-only connection."""
    with connect_ro(db_path) as db:
        return [] if db is None else queries.window(db, _IID, bar_seconds, 1 << 62, 5)


def test_the_entrypoint_injects_a_sink_that_satisfies_the_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The typed half of the port contract: `CandleSink` never imports `SecondSink`, so this
    assignment is where mypy checks that it structurally satisfies it (spine AD-D2).
    """
    collector, _ = _collector(tmp_path, monkeypatch)
    sink = collector._second_sink
    assert isinstance(sink, CandleSink)
    port: SecondSink = sink
    assert port.watermarks() == {}


def test_the_entrypoint_starts_the_candle_retention_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention is candles' own process manager now, started through `extra_loops`."""
    collector, _ = _collector(tmp_path, monkeypatch)
    assert _pruned_store(collector._extra_loops) is not None


def test_the_retention_loop_prunes_this_venues_own_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop must hold the same store the sink writes, not another venue's."""
    collector, _ = _collector(tmp_path, monkeypatch)
    sink = collector._second_sink
    assert isinstance(sink, CandleSink)
    assert _pruned_store(collector._extra_loops) is sink._store


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
    collector, db_path = _collector(tmp_path, monkeypatch)
    snaps = [_snap(i) for i in range(30)]
    collector._buffer[(DydxSecondSnapshot, str(snaps[0].instrument_id))] = snaps

    asyncio.run(collector._flush_once())

    bar = _bars(db_path)[0]
    assert (bar["o"], bar["c"], bar["seconds_observed"]) == (100.0, 129.5, 30)
    assert bar["v"] == 30 * 1.5
    # One flush lands in every stored width at once, so a partial hour records the seconds it
    # actually saw -- the property capture's own tests used to assert before the sink moved out.
    hour = _bars(db_path, 3600)[0]
    assert (hour["o"], hour["c"], hour["seconds_observed"]) == (100.0, 129.5, 30)
    assert hour["partial"] is True


def test_a_failed_parquet_write_never_reaches_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector, db_path = _collector(tmp_path, monkeypatch)
    snaps = [_snap(i) for i in range(30)]
    collector._buffer[(DydxSecondSnapshot, str(snaps[0].instrument_id))] = snaps

    def boom(_items: list) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(collector._catalog, "write_data", boom)
    asyncio.run(collector._flush_once())

    assert _bars(db_path) == []  # store never ahead of the archive


def test_startup_catch_up_applies_seconds_the_archive_has_beyond_the_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, db_path = _collector(tmp_path, monkeypatch)
    snaps = [_snap(i) for i in range(60)]
    ParquetDataCatalog(str(tmp_path / "catalog")).write_data(snaps)
    assert collector._second_sink is not None
    collector._second_sink.apply(_IID, snaps[:40])  # crashed before the rest was applied
    monkeypatch.setattr("dydx_collector.collector.time.time_ns", lambda: _T0 + 3600 * 1_000_000_000)

    collector._catch_up_candle_store()

    bar = _bars(db_path)[0]
    assert (bar["c"], bar["seconds_observed"]) == (159.5, 60)
