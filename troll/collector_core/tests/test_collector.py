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
Core collector gate: real OrderBook/TradeTick/catalog objects, no network (TEST-03). The
client is a tiny in-test duck type -- it is *our* contract, not a Nautilus internal.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest
from collector_core.second_snapshot import DydxSecondSnapshot
from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.catalog_stats import query_second_snapshots

from collector_core.collector import _IMPOSSIBLE_LOG_EVERY_NS
from collector_core.collector import Collector
from collector_core.collector import quarantine_corrupt_parquet
from collector_core.config import CoreConfig
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_BYBIT = "BTCUSDT-LINEAR.BYBIT"
_HL = "BTC-USD-PERP.HYPERLIQUID"
_S = 1_000_000_000


class _ResyncClient:
    """Delta-stream venue shape (Bybit): can force a fresh snapshot."""

    def __init__(self) -> None:
        self.resynced: list[str] = []

    async def resync_orderbook(self, iid: str) -> None:
        self.resynced.append(iid)


class _FlakyResyncClient(_ResyncClient):
    """Resync whose first attempt fails (WS send error) and second succeeds."""

    async def resync_orderbook(self, iid: str) -> None:
        await super().resync_orderbook(iid)
        if len(self.resynced) == 1:
            raise ConnectionError("ws send failed")


class _SnapshotClient:
    """Full-snapshot venue shape (Hyperliquid): no resync_orderbook, on purpose."""


def _collector(
    tmp_path: Path, iid: str = _BYBIT, client: object | None = None, seen_trade_ids: int = 2000
) -> Collector:
    os.environ["CANDLES_DB_PATH"] = str(
        tmp_path / "candles.db"
    )  # the default dir is tmp_path's shared parent
    cfg = CoreConfig(
        environment="mainnet",
        catalog_path=str(tmp_path),
        instruments=(iid,),
        seen_trade_ids=seen_trade_ids,
    )
    return Collector(cfg, client if client is not None else _ResyncClient())


def _adds(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBookDeltas:
    """Incremental deltas only -- no leading Clear, i.e. not a snapshot."""
    full = _deltas(bids, asks)
    return OrderBookDeltas(full.instrument_id, full.deltas[1:])


def _deltas(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]], iid: str = _BYBIT
) -> OrderBookDeltas:
    ts = time.time_ns()
    inst = InstrumentId.from_str(iid)
    deltas = [OrderBookDelta.clear(inst, 0, ts, ts)]
    for side, levels in ((OrderSide.BUY, bids), (OrderSide.SELL, asks)):
        for price, size in levels:
            order = BookOrder(side, Price(price, 2), Quantity(size, 3), 0)
            deltas.append(OrderBookDelta(inst, BookAction.ADD, order, 0, 0, ts, ts))
    return OrderBookDeltas(inst, deltas)


def _trade(
    price: float, size: float, side: AggressorSide, n: int, ts: int | None = None, iid: str = _BYBIT
) -> TradeTick:
    ts = time.time_ns() if ts is None else ts
    return TradeTick(
        InstrumentId.from_str(iid),
        Price(price, 2),
        Quantity(size, 3),
        side,
        TradeId(str(n)),
        ts,
        ts,
    )


def _tick(c: Collector, now_ns: int | None = None) -> list[DydxSecondSnapshot]:
    return asyncio.run(c._sample_tick(time.time_ns() if now_ns is None else now_ns))


def test_snapshot_from_book_and_trades_then_accumulators_reset(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0), (99.5, 2.0)], [(100.5, 3.0)]))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 1))
    c._process_data(_trade(100.5, 0.25, AggressorSide.SELLER, 2))

    (snap,) = _tick(c)
    assert snap.bid_prices == [100.0, 99.5]
    assert snap.ask_prices == [100.5]
    assert (snap.buy_volume, snap.sell_volume, snap.buy_count, snap.sell_count) == (0.5, 0.25, 1, 1)
    assert (snap.open_price, snap.high_price, snap.low_price, snap.close_price) == (
        100.0,
        100.5,
        100.0,
        100.5,
    )
    assert c._buffer[(DydxSecondSnapshot, _BYBIT)] == [snap]
    # Accumulators reset: a trade-less second has no fabricated prices.
    (next_snap,) = _tick(c)
    assert next_snap.open_price is None
    assert next_snap.buy_volume == 0.0


def test_crossed_book_skipped_and_ledgered_once_per_episode(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)]))
    now = time.time_ns()
    assert _tick(c, now) == []
    assert _tick(c, now + _S) == []
    assert error_ledger.counts()["collector.crossed_book"] == 1
    assert c._client.resynced == []


def test_crossed_past_threshold_resyncs_exactly_once_when_client_can(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)]))
    now = time.time_ns()
    assert _tick(c, now) == []
    assert _tick(c, now + int(c._config.crossed_resync_seconds * _S) + 1) == []
    assert c._client.resynced == [_BYBIT]
    assert _BYBIT not in c._live_books  # book dropped: nothing to sample until the fresh snapshot
    assert _tick(c, now + 20 * _S) == []
    assert c._client.resynced == [_BYBIT]


def test_book_that_comes_back_crossed_is_resynced_again_per_window(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)]))
    now = time.time_ns()
    _tick(c, now)
    _tick(c, now + 11 * _S)
    assert c._client.resynced == [_BYBIT]
    # The fresh snapshot arrives still crossed: a new episode, ledgered again, resynced only
    # after another full crossed_resync_seconds window -- not on every tick.
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)]))
    assert _tick(c, now + 12 * _S) == []
    assert _tick(c, now + 20 * _S) == []
    assert c._client.resynced == [_BYBIT]
    assert _tick(c, now + 23 * _S) == []
    assert c._client.resynced == [_BYBIT, _BYBIT]
    assert error_ledger.counts() == {"collector.crossed_book": 2, "collector.resync": 2}


def test_failed_resync_is_ledgered_and_retried_next_tick(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path, client=_FlakyResyncClient())
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)]))
    now = time.time_ns()
    _tick(c, now)
    _tick(c, now + 11 * _S)
    assert c._client.resynced == [_BYBIT]
    assert c._resync_pending == {_BYBIT}
    assert error_ledger.counts()["collector.resync"] == 2  # forced + failed
    _tick(c, now + 12 * _S)  # no book yet -> retry, succeeds
    assert c._client.resynced == [_BYBIT, _BYBIT]
    assert c._resync_pending == set()


def test_deltas_before_a_snapshot_never_create_a_book(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_adds([(100.0, 1.0)], [(100.5, 1.0)]))
    assert _BYBIT not in c._live_books
    assert dict(c._deltas_before_snapshot_dropped) == {_BYBIT: 1}
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))  # the snapshot (Clear + levels)
    (snap,) = _tick(c)
    assert snap.bid_prices == [100.0]
    # After a resync dropped the book, in-flight incremental deltas are dropped the same way.
    c._clear_book_state(_BYBIT)
    c._process_data(_adds([(99.0, 1.0)], [(99.5, 1.0)]))
    assert _BYBIT not in c._live_books
    assert _tick(c) == []


def test_no_book_warning_is_rate_limited(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    c = _collector(tmp_path)
    now = time.time_ns()
    with caplog.at_level("WARNING", logger="collector_core.collector"):
        for offset in (0, _S, 2 * _S, _IMPOSSIBLE_LOG_EVERY_NS):
            assert _tick(c, now + offset) == []
    assert len([r for r in caplog.records if "No book" in r.message]) == 2


def test_crossed_book_without_resync_client_only_skips_then_recovers(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path, _HL, _SnapshotClient())
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)], _HL))
    now = time.time_ns()
    assert _tick(c, now) == []
    assert _tick(c, now + int(c._config.crossed_resync_seconds * _S) + 1) == []
    assert error_ledger.counts()["collector.crossed_book"] == 1
    # Every l2Book message is a full snapshot (Clear + levels): the next one replaces the book.
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], _HL))
    (snap,) = _tick(c)
    assert (snap.bid_prices, snap.ask_prices) == ([100.0], [100.5])
    assert _HL not in c._crossed_since_ns


def test_stale_book_skipped_and_its_trades_discarded(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 1))
    assert _tick(c, time.time_ns() + 60 * _S) == []
    # The outage's trades must not be stamped onto the next valid second.
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    (snap,) = _tick(c)
    assert snap.open_price is None
    assert snap.buy_volume == 0.0


def test_trade_older_than_stale_trade_seconds_dropped(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 1, ts=time.time_ns() - 11 * _S))
    (snap,) = _tick(c)
    assert snap.open_price is None
    assert dict(c._stale_trades_dropped) == {_BYBIT: 1}


def test_duplicate_trade_id_dropped(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 7))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 7))
    (snap,) = _tick(c)
    assert (snap.buy_volume, snap.buy_count) == (0.5, 1)
    assert dict(c._duplicate_trades_dropped) == {_BYBIT: 1}


def test_duplicate_window_evicts_oldest_trade_id(tmp_path: Path) -> None:
    c = _collector(tmp_path, seen_trade_ids=2)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    for n in (1, 2, 3, 1):  # id 1 was evicted by 3, so its reappearance is a new trade
        c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, n))
    (snap,) = _tick(c)
    assert snap.buy_count == 4
    assert dict(c._duplicate_trades_dropped) == {}


def test_quarantine_only_touches_this_collectors_instrument_dirs(tmp_path: Path) -> None:
    error_ledger.reset()
    mine = tmp_path / "data" / "custom_dydx_second_snapshot" / _BYBIT
    theirs = tmp_path / "data" / "custom_dydx_second_snapshot" / _HL
    for d in (mine, theirs):
        d.mkdir(parents=True)
        (d / "x.parquet").write_bytes(b"not parquet")  # e.g. a sibling mid-write
    quarantine_corrupt_parquet(str(tmp_path), [_BYBIT])
    assert not (mine / "x.parquet").exists()
    assert (
        tmp_path / "_quarantine" / "data" / "custom_dydx_second_snapshot" / _BYBIT / "x.parquet"
    ).exists()
    assert (theirs / "x.parquet").exists()
    assert error_ledger.counts() == {"collector.corrupt_parquet": 1}


def test_ohlc_outside_book_canary_fires_once_per_minute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    c = _collector(tmp_path)
    now = time.time_ns()
    with caplog.at_level("ERROR", logger="collector_core.collector"):
        for offset in (0, _S, _IMPOSSIBLE_LOG_EVERY_NS):
            c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
            c._process_data(_trade(150.0, 0.5, AggressorSide.BUYER, offset))  # impossible price
            c._last_book_update_ns[_BYBIT] = now + offset
            (snap,) = _tick(c, now + offset)
            assert snap.high_price == 150.0  # canary, not a filter (DATA-07)
    assert len([r for r in caplog.records if "IMPOSSIBLE" in r.message]) == 2


def test_flush_feeds_the_candle_store_with_exactly_the_flushed_seconds(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    for sec in (0, 1, 2, 60):
        now = sec * _S
        c._last_book_update_ns[_BYBIT] = now
        c._process_data(_trade(100.0 + sec, 1.0, AggressorSide.BUYER, sec))
        (snap,) = _tick(c, now)
        assert snap.ts_event == now
    assert (
        candle_store.window(c._candle_db, _BYBIT, 60, 1 << 62, 5) == []
    )  # nothing until the flush

    asyncio.run(c._flush_once())

    bars = candle_store.window(c._candle_db, _BYBIT, 60, 1 << 62, 5)
    assert [(b["t"], b["o"], b["c"], b["seconds_observed"]) for b in bars] == [
        (0, 100.0, 102.0, 3),
        (60_000, 160.0, 160.0, 1),
    ]
    assert candle_store.window(c._candle_db, _BYBIT, 3600, 1 << 62, 5)[0]["seconds_observed"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("iid", [_BYBIT, _HL])
async def test_snapshot_round_trips_through_catalog(tmp_path: Path, iid: str) -> None:
    c = _collector(tmp_path, iid, _SnapshotClient())
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], iid))
    (snap,) = await c._sample_tick(time.time_ns())
    await c._flush_once()
    # Read back through data_api's own reader: proves these ids need no per-route code.
    (read,) = query_second_snapshots(str(tmp_path), iid, 0, time.time_ns() + _S)
    assert str(read.instrument_id) == iid
    assert read.bid_prices == [100.0]
    assert read.ts_event == snap.ts_event
