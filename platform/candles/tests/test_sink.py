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
"""The sink adapter, the store factory and the retention loop -- the wiring a venue entrypoint does."""

import asyncio
from pathlib import Path

import pytest
from observability import error_ledger

from candles.application import queries
from candles.application.prune import loop as prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.sqlite_store import store_from_env
from candles.tests.test_candle_store import _IID
from candles.tests.test_candle_store import _second


def _sink(tmp_path: Path) -> tuple[CandleSink, CandleStore]:
    store = CandleStore(str(tmp_path / "candles_dydx.db"))
    return CandleSink(store), store


def test_the_sink_offers_exactly_the_ports_two_methods(tmp_path: Path) -> None:
    """
    `CandleSink` never imports `collector_core.ports.SecondSink` -- candles imports no context but
    kernel and observability, so the port is satisfied structurally (spine AD-D2). The typed
    conformance is asserted from the composition root's side, in
    `dydx_collector/tests/test_candle_feed.py`; here only the shape and the return values.
    """
    sink, _ = _sink(tmp_path)
    assert sink.apply(_IID, [_second(0, 100.0)]) == 1
    assert dict(sink.watermarks()) == {_IID: _second(0, 100.0).ts_event}


def test_applied_seconds_become_bars_and_a_watermark(tmp_path: Path) -> None:
    sink, store = _sink(tmp_path)
    rows = [_second(s, 100.0 + s) for s in range(120)]
    assert sink.apply(_IID, rows) == 120
    assert sink.watermarks() == {_IID: rows[-1].ts_event}
    assert len(queries.window(store.connection, _IID, 60, 1 << 62, 10)) == 2


def test_a_replayed_batch_applies_nothing_and_changes_no_volume(tmp_path: Path) -> None:
    """The `_UPSERT` accumulates `v`, so only the watermark keeps a second from counting twice."""
    sink, store = _sink(tmp_path)
    rows = [_second(s, 100.0 + s) for s in range(60)]
    sink.apply(_IID, rows)
    before = queries.window(store.connection, _IID, 60, 1 << 62, 5)
    assert sink.apply(_IID, rows) == 0
    assert queries.window(store.connection, _IID, 60, 1 << 62, 5) == before


def test_one_instruments_rows_never_reach_another(tmp_path: Path) -> None:
    sink, store = _sink(tmp_path)
    other = "ETH-USD-PERP.DYDX"
    sink.apply(_IID, [_second(0, 100.0)])
    assert queries.window(store.connection, other, 60, 1 << 62, 5) == []
    assert list(sink.watermarks()) == [_IID]


def test_db_path_for_venue_is_the_frozen_filename_formula(tmp_path: Path) -> None:
    assert db_path_for_venue(tmp_path, "BYBIT") == str(tmp_path / "candles_bybit.db")
    assert db_path_for_venue(str(tmp_path), "DYDX") == str(tmp_path / "candles_dydx.db")


def test_store_from_env_prefers_the_variable_and_defaults_beside_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env var and its default are the frozen deployment contract (AD-D12)."""
    catalog = tmp_path / "data" / "catalog"
    monkeypatch.delenv("CANDLES_DB_PATH", raising=False)
    default = store_from_env(str(catalog))
    assert default.path == str(tmp_path / "data" / "candles" / "candles.db")
    default.close()

    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "elsewhere" / "candles_hyperliquid.db"))
    chosen = store_from_env(str(catalog))
    assert chosen.path == str(tmp_path / "elsewhere" / "candles_hyperliquid.db")
    chosen.close()


def test_the_prune_loop_prunes_once_before_it_ever_sleeps(tmp_path: Path) -> None:
    """A collector that restarts more often than hourly must still prune."""
    pruned: list[int | None] = []

    class _Store:
        def prune(self, now_ms: int | None = None) -> None:
            pruned.append(now_ms)

    async def drive() -> None:
        task = asyncio.ensure_future(prune_loop(_Store())())
        await asyncio.sleep(0)  # let it run up to its first await
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(drive())
    assert pruned == [None]


def test_a_failed_prune_is_ledgered_and_the_loop_survives() -> None:
    error_ledger.reset()

    class _Store:
        def prune(self, now_ms: int | None = None) -> None:
            raise OSError("disk I/O error")

    async def drive() -> None:
        task = asyncio.ensure_future(prune_loop(_Store())())
        await asyncio.sleep(0)
        assert not task.done(), "a failed prune must not end the loop"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(drive())
    assert error_ledger.counts() == {"collector.candle_store_prune": 1}
    error_ledger.reset()
