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
import math
import os
import random
import time
from pathlib import Path

import pytest
from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns
from ml_signals.catalog_stats import query_second_snapshots

from collector_core.archive_gaps import ARRIVAL_MARGIN_NS
from collector_core.archive_gaps import load_gaps
from collector_core.collector import _IMPOSSIBLE_LOG_EVERY_NS
from collector_core.collector import Collector
from collector_core.collector import _next_sample_at
from collector_core.collector import quarantine_corrupt_parquet
from collector_core.config import CoreConfig
from collector_core.rebuild_seconds import rebuild_day
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig
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
from nautilus_trader.test_kit.providers import TestInstrumentProvider


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
            c._last_feed_message_ns = now + offset
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


# -- story 22.13: raw trade archive, exact fold, drift-free sampling --------------------------------


def test_accepted_trade_is_folded_live_and_buffered_for_the_archive(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    trade = _trade(100.0, 0.5, AggressorSide.BUYER, 1)
    c._process_data(trade)
    assert c._second_trades[_BYBIT] == [trade]
    assert c._buffer[(TradeTick, _BYBIT)] == [trade]


def test_rejected_trades_are_neither_folded_nor_archived(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 1, ts=time.time_ns() - 11 * _S))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 2))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 2))  # duplicate trade_id
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]] == ["2"]


def _clocked_trade(n: int, ts_event: int, ts_init: int, iid: str = _BYBIT) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(iid),
        Price(100.0 + n, 1),
        Quantity(0.001 * (n + 1), 3),
        AggressorSide.SELLER,
        TradeId(str(n)),
        ts_event,
        ts_init,
    )


def test_flushed_trades_round_trip_with_both_clocks_and_load_as_backtest_data(
    tmp_path: Path,
) -> None:
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    iid = instrument.id.value
    c = _collector(tmp_path, iid, _SnapshotClient())
    c._catalog.write_data([instrument])
    now = time.time_ns()
    trades = [
        _clocked_trade(n, now - (3 - n) * _S, now - (3 - n) * _S + 7_000_000, iid) for n in range(3)
    ]
    for trade in trades:
        c._process_data(trade)
    asyncio.run(c._flush_once(final=True))

    read = c._catalog.trade_ticks(instrument_ids=[iid])
    assert [(t.trade_id, t.ts_event, t.ts_init) for t in read] == [
        (t.trade_id, t.ts_event, t.ts_init) for t in trades
    ]
    assert [(t.price, t.size) for t in read] == [(t.price, t.size) for t in trades]
    config = BacktestDataConfig(
        catalog_path=str(tmp_path), data_cls=TradeTick, instrument_ids=[iid]
    )
    loaded = BacktestNode.load_data_config(config).data
    assert [(t.trade_id, t.ts_event, t.ts_init) for t in loaded] == [
        (t.trade_id, t.ts_event, t.ts_init) for t in trades
    ]


def test_flush_carries_a_split_ts_init_group_so_both_batches_land(tmp_path: Path) -> None:
    # One WS message = one ts_init. Its trades straddle the flush: without the carry, the second
    # batch's file would start at the first file's last ts_init and write_data would refuse it.
    c = _collector(tmp_path)
    now = time.time_ns()
    message = now - _S
    c._process_data(_clocked_trade(0, now - 3 * _S, now - 3 * _S))
    c._process_data(_clocked_trade(1, now - 2 * _S, message))
    asyncio.run(c._flush_once())
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]] == ["1"]  # carried
    c._process_data(_clocked_trade(2, now - 2 * _S, message))  # the rest of that message
    c._process_data(_clocked_trade(3, now - _S // 2, now - _S // 2))
    asyncio.run(c._flush_once(final=True))

    read = c._catalog.trade_ticks(instrument_ids=[_BYBIT])
    assert [t.trade_id.value for t in read] == ["0", "1", "2", "3"]
    assert c._buffer[(TradeTick, _BYBIT)] == []


def test_flush_writes_an_old_ts_init_group_without_carrying_it(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    old = time.time_ns() - 6 * _S  # past the 5 s carry window: nothing of that message is left
    c._buffer[(TradeTick, _BYBIT)] = [_clocked_trade(n, old, old) for n in range(3)]
    asyncio.run(c._flush_once())
    assert c._buffer[(TradeTick, _BYBIT)] == []
    assert len(c._catalog.trade_ticks(instrument_ids=[_BYBIT])) == 3


def test_flush_sorts_a_batch_by_ts_init_before_writing(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    old = time.time_ns() - 20 * _S
    c._buffer[(TradeTick, _BYBIT)] = [
        _clocked_trade(1, old + _S, old + _S),
        _clocked_trade(0, old, old),
    ]
    asyncio.run(c._flush_once())
    assert [t.trade_id.value for t in c._catalog.trade_ticks(instrument_ids=[_BYBIT])] == ["0", "1"]


def test_trades_of_an_unsampled_instrument_do_not_accumulate(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 1, iid=_HL))
    _tick(c)
    assert _HL not in c._second_trades  # MEM-02
    assert len(c._buffer[(TradeTick, _HL)]) == 1  # still archived


def test_live_second_is_the_exact_fold_of_its_trades(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(0.1, 1.0)], [(0.9, 1.0)]))
    for n, size in enumerate((0.1, 0.2)):
        c._process_data(
            TradeTick(
                InstrumentId.from_str(_BYBIT),
                Price(0.5, 2),
                Quantity.from_str(f"{size:.8f}"),
                AggressorSide.BUYER,
                TradeId(str(n)),
                time.time_ns(),
                time.time_ns(),
            )
        )
    (snap,) = _tick(c)
    assert snap.buy_volume == 0.3  # the float fold gave 0.30000000000000004


def test_next_sample_at_is_the_next_mid_interval_strictly_after_now() -> None:
    assert _next_sample_at(100.2, 1.0, None) == 100.5
    assert _next_sample_at(100.5, 1.0, None) == 101.5
    assert _next_sample_at(100.7, 1.0, None) == 101.5


def test_next_sample_at_never_ticks_twice_in_one_bucket() -> None:
    # Woke a hair early (still in bucket 100): the next target is bucket 101, not 100 again.
    assert _next_sample_at(100.4995, 1.0, 100.4995) == 101.5
    # Woke 1.2 s late (in bucket 101): bucket 100 is lost (lag canary), next is 102.
    assert _next_sample_at(101.75, 1.0, 101.7) == 102.5


def test_next_sample_at_hits_every_bucket_under_jitter() -> None:
    rng = random.Random(7)  # noqa: S311 -- a deterministic fixture, not cryptography
    for interval in (1.0, 0.01):
        now, last, buckets = 1_000.0, None, []
        for _ in range(2_000):
            target = _next_sample_at(now, interval, last)
            woke = target + rng.uniform(-0.4, 0.4) * interval
            buckets.append(math.floor(woke / interval))
            last = woke
            now = woke + rng.uniform(0.0, 0.05) * interval  # the sample's own work
        assert buckets == list(range(buckets[0], buckets[0] + len(buckets)))


# -- review patches: archive gaps, carry with rows, queue lag, cadence --------------------------------


def _day_collector(tmp_path: Path, interval: float = 1.0) -> Collector:
    """Build a collector for a past UTC day: no age filter, so fixed timestamps pass."""
    os.environ["CANDLES_DB_PATH"] = str(tmp_path / "candles.db")
    cfg = CoreConfig(
        environment="mainnet",
        catalog_path=str(tmp_path),
        instruments=(_BYBIT,),
        stale_trade_seconds=10**9,
        snapshot_interval_seconds=interval,
    )
    return Collector(cfg, _ResyncClient())


_D0 = 1_789_000_000 * _S // (86_400 * _S) * (86_400 * _S)  # a past UTC midnight


def _sample_at(c: Collector, second: float) -> DydxSecondSnapshot:
    now = _D0 + int(second * _S)
    c._last_book_update_ns[_BYBIT] = now
    c._last_feed_message_ns = now
    (snap,) = _tick(c, now)
    return snap


def test_a_failed_trade_write_marks_a_gap_and_the_rebuild_keeps_live_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _day_collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    c._process_data(_clocked_trade(1, _D0 + 1000 * _S, _D0 + 1000 * _S))
    _sample_at(c, 1000.5)
    asyncio.run(c._flush_once(final=True))  # the archive exists from second 1000 on
    c._process_data(_clocked_trade(2, _D0 + 2000 * _S, _D0 + 2000 * _S))
    live = _sample_at(c, 2000.5)
    write = c._catalog.write_data

    def trades_fail(items: list) -> None:
        if isinstance(items[0], TradeTick):
            raise OSError("disk full")
        write(items)

    monkeypatch.setattr(c._catalog, "write_data", trades_fail)
    asyncio.run(c._flush_once(final=True))  # the snapshot lands, its trade does not

    (gap,) = load_gaps(str(tmp_path), _BYBIT)
    assert gap[0] == _D0 + 2000 * _S
    report = rebuild_day(str(tmp_path), _BYBIT, _D0, apply=True)
    (row,) = [
        s
        for s in query_second_snapshots(str(tmp_path), _BYBIT, 0, 1 << 62)
        if s.ts_event == live.ts_event
    ]
    assert (row.close_price, row.sell_count) == (live.close_price, 1)  # not zeroed
    assert (report.not_covered, report.rebuilt) == (1, 1)


def test_rows_sampled_after_a_carried_trade_group_are_carried_with_it(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    now = time.time_ns()
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    before = asyncio.run(c._sample_tick(now - 2 * _S))[0]
    c._process_data(_clocked_trade(0, now - _S, now - _S))  # young: carried
    after = asyncio.run(c._sample_tick(now - _S // 2))[0]
    asyncio.run(c._flush_once())
    assert c._buffer[(DydxSecondSnapshot, _BYBIT)] == [after]
    assert [s.ts_event for s in query_second_snapshots(str(tmp_path), _BYBIT, 0, 1 << 62)] == [
        before.ts_event
    ]
    # The candle store only saw what was written: the carried row is still to come, in order.
    assert candle_store.watermarks(c._candle_db)[_BYBIT] == before.ts_event
    asyncio.run(c._flush_once(final=True))
    assert candle_store.watermarks(c._candle_db)[_BYBIT] == after.ts_event


def test_an_old_trade_group_is_carried_while_the_ingest_queue_lags(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    old = time.time_ns() - 30 * _S
    c._buffer[(TradeTick, _BYBIT)] = [_clocked_trade(0, old, old - _S), _clocked_trade(1, old, old)]
    c._ingest_queue.put_nowait(object())  # the rest of that WS message may still be in here
    asyncio.run(c._flush_once())
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]] == ["1"]


def test_quarantining_a_trade_file_marks_its_span_as_an_archive_gap(tmp_path: Path) -> None:
    leaf = tmp_path / "data" / "trade_tick" / _BYBIT
    leaf.mkdir(parents=True)
    name = "2026-09-10T00-01-00-000000000Z_2026-09-10T00-02-00-000000000Z"
    (leaf / f"{name}.parquet").write_bytes(b"torn write")
    quarantine_corrupt_parquet(str(tmp_path), [_BYBIT])
    first, last = (_stamp_to_ns(x) for x in name.split("_"))
    assert load_gaps(str(tmp_path), _BYBIT) == [(first, last + ARRIVAL_MARGIN_NS)]


def test_a_non_one_second_cadence_is_ledgered_once_at_start(tmp_path: Path) -> None:
    error_ledger.reset()
    _day_collector(tmp_path, interval=0.5)
    assert error_ledger.counts() == {"collector.cadence": 1}
