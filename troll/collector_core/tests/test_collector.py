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
from collections.abc import Mapping
from pathlib import Path

import pytest
from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns
from ml_signals.catalog_stats import query_second_snapshots

from collector_core import trade_backfill
from collector_core.archive_gaps import ARRIVAL_MARGIN_NS
from collector_core.archive_gaps import load_gaps
from collector_core.collector import _BACKFILL_SETTLE_NS
from collector_core.collector import _IMPOSSIBLE_LOG_EVERY_NS
from collector_core.collector import _WATCHDOG_REMINDER_NS
from collector_core.collector import _WATCHDOG_STARTUP_GRACE_NS
from collector_core.collector import Collector
from collector_core.collector import _BackfillReport
from collector_core.collector import _next_sample_at
from collector_core.collector import quarantine_corrupt_parquet
from collector_core.config import CoreConfig
from collector_core.feed import MAIN_FEED
from collector_core.feed import Feed
from collector_core.rebuild_seconds import rebuild_day
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
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
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    iid: str = _BYBIT,
    ts: int | None = None,
) -> OrderBookDeltas:
    ts = time.time_ns() if ts is None else ts
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


# -- story 22.14: reconnect detection, REST backfill, dual-feed arbitration -------------------------

_LINEAR = Feed("linear", "linear")
_LINEAR_TRADES = Feed("linear-trades", "linear", trades_only=True)
_SPOT_ID = "BTCUSDT-SPOT.BYBIT"


class _FeedStateClient(_ResyncClient):
    """A client with `feed_states()` (Bybit/Hyperliquid shape); the test flips the states."""

    def __init__(self) -> None:
        super().__init__()
        self.states: dict[Feed, bool] = {_LINEAR: True}

    def feed_states(self) -> dict[Feed, bool]:
        return dict(self.states)


def _perp(iid: str) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
    )


def _two_instrument_collector(tmp_path: Path, client: object | None = None) -> Collector:
    os.environ["CANDLES_DB_PATH"] = str(tmp_path / "candles.db")
    cfg = CoreConfig(
        environment="mainnet", catalog_path=str(tmp_path), instruments=(_BYBIT, _SPOT_ID)
    )
    c = Collector(cfg, client if client is not None else _FeedStateClient())
    c._instruments = {iid: _perp(iid) for iid in (_BYBIT, _SPOT_ID)}
    return c


class _FakeFetch:
    """Stands in for `trade_backfill.fetch_trades` (network) and records its calls."""

    def __init__(self, results: Mapping[str, object]) -> None:
        self.results = results
        self.calls: list[tuple[str, int, int, str]] = []

    def __call__(
        self, inst: CryptoPerpetual, since: int, floor: int, env: str, ts_init: int
    ) -> trade_backfill.Fetched:
        self.calls.append((inst.id.value, since, floor, env))
        result = self.results[inst.id.value]
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, trade_backfill.Fetched)
        return result


def _fetched(
    trades: list[TradeTick], reached: bool = True, oldest: int | None = None
) -> trade_backfill.Fetched:
    if oldest is None and trades:
        oldest = trades[0].ts_event
    return trade_backfill.Fetched(trades, reached, oldest)


def _run_backfills(c: Collector, now_ns: int) -> None:
    asyncio.run(c._run_due_backfills(now_ns))


def _run_now(c: Collector, feed_name: str) -> None:
    """Run one backfill for `feed_name` from its current baselines (a detection just now)."""
    c._schedule_backfill(feed_name, 0, "test")
    asyncio.run(c._run_backfill(feed_name, c._backfill_requests.pop(feed_name)))


def _seed_trade(c: Collector, n: int, feed: Feed = _LINEAR, iid: str = _BYBIT) -> TradeTick:
    """Feed a live trade on `feed`: the instrument gets its backfill baseline and feed membership."""
    trade = _trade(100.0, 0.5, AggressorSide.BUYER, n, iid=iid)
    c._process_data(trade, feed)
    return trade


def test_on_data_tags_the_queue_with_the_feed(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    sentinel = object()
    c._on_data(sentinel, _LINEAR)
    c._on_data(sentinel)
    assert c._ingest_queue.get_nowait() == (sentinel, _LINEAR)
    assert c._ingest_queue.get_nowait() == (sentinel, MAIN_FEED)


def test_flip_inactive_then_active_schedules_one_backfill_after_the_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    live = _seed_trade(c, 1)
    fetch = _FakeFetch({_BYBIT: _fetched([])})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    now = time.time_ns()
    c._poll_feed_states(now)  # baseline only
    assert c._backfill_requests == {}
    c._client.states[_LINEAR] = False
    c._poll_feed_states(now + 1)
    c._client.states[_LINEAR] = True
    c._poll_feed_states(now + 2)
    assert c._backfill_requests["linear"].reasons == ["feed reconnected (inactive -> active)"]
    _run_backfills(c, now + _BACKFILL_SETTLE_NS)  # not yet due
    assert fetch.calls == []
    _run_backfills(c, now + 2 + _BACKFILL_SETTLE_NS)
    ((iid, since, floor, env),) = fetch.calls
    assert (iid, since, env) == (_BYBIT, live.ts_event - 5 * _S, "mainnet")
    assert now - ARRIVAL_MARGIN_NS <= floor <= time.time_ns() - ARRIVAL_MARGIN_NS
    assert error_ledger.counts() == {"collector.trade_backfill": 1}
    assert c._backfill_requests == {}


def test_first_feed_state_observation_is_only_a_baseline(tmp_path: Path) -> None:
    c = _two_instrument_collector(tmp_path)
    c._client.states[_LINEAR] = True
    c._poll_feed_states(time.time_ns())
    c._poll_feed_states(time.time_ns())
    assert c._backfill_requests == {}
    assert _LINEAR in c._feeds


def test_feed_states_failure_is_ledgered_at_most_once_a_minute(tmp_path: Path) -> None:
    error_ledger.reset()

    class _Broken(_ResyncClient):
        def feed_states(self) -> dict[Feed, bool]:
            raise RuntimeError("socket gone")

    c = _two_instrument_collector(tmp_path, _Broken())
    now = time.time_ns()
    for offset in (0, _S // 10, 59 * _S, 61 * _S):
        c._poll_feed_states(now + offset)
    assert error_ledger.counts() == {"collector.feed_state": 2}


def test_book_feed_silence_then_a_message_schedules_a_backfill(tmp_path: Path) -> None:
    c = _collector(tmp_path)  # feed_stale_seconds unset: stale_book_seconds (5 s)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]), _LINEAR)
    c._feed_last_ns["linear"] = time.time_ns() - 7 * _S
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]), _LINEAR)
    (reason,) = c._backfill_requests["linear"].reasons
    assert reason.startswith("feed silent 7.0")


def test_a_silent_trades_only_feed_schedules_nothing_and_never_feeds_the_book_gate(
    tmp_path: Path,
) -> None:
    c = _collector(tmp_path)
    c._feed_last_ns["linear-trades"] = time.time_ns() - 60 * _S
    c._last_feed_message_ns = 123
    _seed_trade(c, 1, _LINEAR_TRADES)
    assert c._backfill_requests == {}
    assert c._last_feed_message_ns == 123  # a live trades socket says nothing about the book
    assert c._feed_last_ns["linear-trades"] > 123


def test_replayed_trade_id_after_the_grace_is_a_duplicate_and_schedules_a_backfill(
    tmp_path: Path,
) -> None:
    c = _collector(tmp_path)
    c._watchdog_started_ns -= _WATCHDOG_STARTUP_GRACE_NS
    trade = _seed_trade(c, 7)
    c._process_data(trade, _LINEAR)
    assert dict(c._duplicate_trades_dropped) == {_BYBIT: 1}
    assert c._backfill_requests["linear"].reasons == ["replayed trade ids"]


def test_a_replay_inside_the_startup_grace_schedules_nothing(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    trade = _seed_trade(c, 7)
    c._process_data(trade, _LINEAR)
    assert dict(c._duplicate_trades_dropped) == {_BYBIT: 1}
    assert c._backfill_requests == {}


def test_detections_within_the_settle_coalesce_into_one_backfill_and_one_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    _seed_trade(c, 1)
    monkeypatch.setattr(trade_backfill, "fetch_trades", _FakeFetch({_BYBIT: _fetched([])}))
    now = time.time_ns()
    c._poll_feed_states(now)
    c._client.states[_LINEAR] = False
    c._poll_feed_states(now)
    c._client.states[_LINEAR] = True
    c._poll_feed_states(now)
    c._schedule_backfill("linear", now + _S, "feed silent 7.0s")
    assert c._backfill_requests["linear"].due_ns == now + _BACKFILL_SETTLE_NS  # first one's
    _run_backfills(c, now + _BACKFILL_SETTLE_NS)
    assert error_ledger.counts() == {"collector.trade_backfill": 1}
    detail = error_ledger.last_details()["collector.trade_backfill"]
    assert "feed reconnected (inactive -> active); feed silent 7.0s" in detail


def _backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, results: Mapping[str, object]
) -> tuple[Collector, str]:
    """Seed both instruments on `linear`, run one backfill with `results`; (collector, ledger)."""
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    _seed_trade(c, 1)
    _seed_trade(c, 2, iid=_SPOT_ID)
    monkeypatch.setattr(trade_backfill, "fetch_trades", _FakeFetch(results))
    _run_now(c, "linear")
    return c, error_ledger.last_details()["collector.trade_backfill"]


def _rest(n: int, ts_event: int, iid: str = _BYBIT) -> TradeTick:
    return _clocked_trade(n, ts_event, 0, iid)


def test_instrument_without_an_archived_trade_is_counted_no_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    _seed_trade(c, 1)
    c._feed_instruments["linear"].add(_SPOT_ID)  # seen on the feed (book), never a trade
    fetch = _FakeFetch({_BYBIT: _fetched([])})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    _run_now(c, "linear")
    assert [call[0] for call in fetch.calls] == [_BYBIT]
    assert "no baseline 1" in error_ledger.last_details()["collector.trade_backfill"]


def test_a_rest_trade_already_archived_is_counted_not_buffered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time_ns()
    results = {_BYBIT: _fetched([_rest(1, now)]), _SPOT_ID: _fetched([])}
    c, detail = _backfill(tmp_path, monkeypatch, results)
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]] == ["1"]  # the live copy
    assert "backfilled 0, already archived 1" in detail


def test_a_new_rest_trade_is_archived_with_venue_ts_event_and_our_ts_init_but_never_folded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = time.time_ns()
    missed = _rest(99, before - 2 * _S)
    results = {_BYBIT: _fetched([missed]), _SPOT_ID: _fetched([])}
    c, detail = _backfill(tmp_path, monkeypatch, results)
    live, archived = c._buffer[(TradeTick, _BYBIT)]
    assert (archived.trade_id, archived.ts_event, archived.price) == (
        missed.trade_id,
        missed.ts_event,
        missed.price,
    )
    assert archived.ts_init >= before  # the time it was archived, not the venue's
    assert [t.trade_id.value for t in c._second_trades[_BYBIT]] == [live.trade_id.value]
    assert dict(c._trade_backfill_counts) == {_BYBIT: 1}
    assert c._last_trade_ts[_BYBIT] == max(live.ts_event, missed.ts_event)
    assert c._trade_feeds_seen[_BYBIT]["99"] == ["rest"]
    assert "backfilled 1" in detail


def test_an_unseen_rest_trade_older_than_the_arrival_margin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _rest(99, time.time_ns() - ARRIVAL_MARGIN_NS - _S)
    results = {_BYBIT: _fetched([old]), _SPOT_ID: _fetched([])}
    c, detail = _backfill(tmp_path, monkeypatch, results)
    assert len(c._buffer[(TradeTick, _BYBIT)]) == 1  # only the live seed
    assert "refused 1 (older than the 300 s arrival margin)" in detail
    assert "99" not in c._trade_feeds_seen[_BYBIT]


def test_a_shallow_venue_reports_the_uncovered_seconds_as_unrecoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    seed = _seed_trade(c, 1)
    oldest = seed.ts_event + 12 * _S
    fetch = _FakeFetch({_BYBIT: _fetched([_rest(5, oldest)], reached=False, oldest=oldest)})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    _run_now(c, "linear")
    detail = error_ledger.last_details()["collector.trade_backfill"]
    assert f"unrecoverable seconds {{'{_BYBIT}': 12.0}}" in detail


def test_a_fetch_failure_is_listed_under_errors_and_the_others_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missed = _rest(99, time.time_ns() - _S, _SPOT_ID)
    results = {_BYBIT: OSError("connection reset"), _SPOT_ID: _fetched([missed])}
    c, detail = _backfill(tmp_path, monkeypatch, results)
    assert "OSError('connection reset')" in detail
    assert dict(c._trade_backfill_counts) == {_SPOT_ID: 1}


def test_an_inexact_venue_value_lists_the_instrument_under_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = trade_backfill.Fetched([], True, None, ["{'px': '84765.05'}: not representable"])
    c, detail = _backfill(tmp_path, monkeypatch, {_BYBIT: rejected, _SPOT_ID: _fetched([])})
    assert f"errors {{'{_BYBIT}': \"1 inexact trade(s) skipped" in detail


def test_a_backfill_request_covers_only_that_feeds_configured_instruments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _two_instrument_collector(tmp_path)
    _seed_trade(c, 1)
    _seed_trade(c, 2, Feed("spot", "spot"), _SPOT_ID)
    _seed_trade(c, 3, iid=_HL)  # on the feed, but not configured
    fetch = _FakeFetch({_BYBIT: _fetched([])})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    _run_now(c, "linear")
    assert [call[0] for call in fetch.calls] == [_BYBIT]


def test_mark_price_does_not_teach_feed_instruments(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    mark = MarkPriceUpdate(InstrumentId.from_str(_HL), Price.from_str("1.0"), 1, 1)
    c._process_data(mark, _LINEAR)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]), _LINEAR)
    assert c._feed_instruments["linear"] == {_BYBIT}


def test_dual_feed_archives_the_first_copy_and_counts_the_second_as_duplicate_feed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    c = _collector(tmp_path)
    trade = _seed_trade(c, 7, _LINEAR)
    c._process_data(trade, _LINEAR_TRADES)
    _seed_trade(c, 8, _LINEAR_TRADES)  # only the trades socket saw this one
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]] == ["7", "8"]
    assert dict(c._duplicate_feed_dropped) == {_BYBIT: 1}
    assert dict(c._duplicate_trades_dropped) == {}
    assert dict(c._feed_overlap) == {("linear", "linear-trades"): 1}
    with caplog.at_level("INFO", logger="collector_core.collector"):
        c._report_stale_trades()
    lines = [r.message for r in caplog.records]
    assert any("duplicate_feed" in m and _BYBIT in m for m in lines)
    (arbitration,) = [m for m in lines if m.startswith("Trade feed arbitration (cumulative)")]
    assert "linear: first 1, only-this-feed 0" in arbitration
    assert "linear-trades: first 1, only-this-feed 1" in arbitration
    assert "both {'linear+linear-trades': 1}" in arbitration
    assert c._duplicate_feed_dropped == {}  # per-flush counter cleared, cumulative kept
    assert c._feed_first == {"linear": 1, "linear-trades": 1}


def test_a_rest_copy_first_then_the_live_copy_is_duplicate_feed(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    trade = _trade(100.0, 0.5, AggressorSide.BUYER, 5)
    c._register_trade(_BYBIT, "5", "rest")
    c._process_data(trade, _LINEAR)
    assert dict(c._duplicate_feed_dropped) == {_BYBIT: 1}
    assert dict(c._feed_overlap) == {("rest", "linear"): 1}


def test_the_dedup_map_stays_bounded_and_evicts_in_lockstep(tmp_path: Path) -> None:
    c = _collector(tmp_path, seen_trade_ids=2)
    for n in (1, 2, 3):
        _seed_trade(c, n)
    assert set(c._trade_feeds_seen[_BYBIT]) == {"2", "3"}
    assert list(c._seen_trade_ids[_BYBIT]) == ["2", "3"]


def _one_sided(c: Collector, now: int) -> list[str]:
    return c._one_sided_messages(now, "BybitClient")


def test_one_sided_outage_alerts_reminds_and_recovers(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    now = time.time_ns()
    c._feeds |= {_LINEAR, _LINEAR_TRADES, Feed("spot", "spot")}  # spot: a group of one
    c._feed_last_trade_ns.update({"linear": now, "linear-trades": now - 31 * _S})
    (down,) = _one_sided(c, now)
    assert "one-sided outage" in down
    assert "linear-trades is 30s+ behind linear in trade arrivals" in down
    assert _one_sided(c, now + _S) == []
    c._feed_last_trade_ns["linear"] = now + _WATCHDOG_REMINDER_NS + _S
    (still,) = _one_sided(c, now + _WATCHDOG_REMINDER_NS + _S)
    assert "still open" in still
    c._feed_last_trade_ns["linear-trades"] = now + _WATCHDOG_REMINDER_NS + 2 * _S
    (recovered,) = _one_sided(c, now + _WATCHDOG_REMINDER_NS + 2 * _S)
    assert "recovered" in recovered
    assert c._one_sided_state["linear"] == (None, 0)


def test_a_feed_that_never_traded_counts_from_the_watchdog_start(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._feeds |= {_LINEAR, _LINEAR_TRADES}
    c._feed_last_trade_ns["linear"] = c._watchdog_started_ns + 40 * _S
    (down,) = _one_sided(c, c._watchdog_started_ns + 40 * _S)
    assert "linear-trades is 30s+ behind" in down


def test_a_healthy_group_is_silent(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    now = time.time_ns()
    c._feeds |= {_LINEAR, _LINEAR_TRADES}
    c._feed_last_trade_ns.update({"linear": now, "linear-trades": now - 29 * _S})
    assert _one_sided(c, now) == []


def test_restamped_backfill_keeps_the_flush_ts_init_order(tmp_path: Path) -> None:
    # A backfilled trade must not land before what a flush already wrote (write_data refuses an
    # overlapping ts_init interval): it is stamped when archived, after the live trades flushed.
    c = _collector(tmp_path)
    c._instruments = {_BYBIT: _perp(_BYBIT)}
    old = time.time_ns() - 20 * _S
    c._buffer[(TradeTick, _BYBIT)] = [_clocked_trade(0, old, old)]
    c._last_trade_ts[_BYBIT] = old
    asyncio.run(c._flush_once())
    c._apply_backfill(_BYBIT, [_rest(1, old - _S)], _BackfillReport("linear", ["test"]))
    asyncio.run(c._flush_once(final=True))
    read = c._catalog.trade_ticks(instrument_ids=[_BYBIT])
    assert [t.trade_id.value for t in read] == ["0", "1"]  # both files landed
    assert read[1].ts_event < read[0].ts_event < read[1].ts_init


def test_the_baseline_is_captured_at_detection_not_when_the_backfill_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Network-cut regression (2026-09-21): trades after the resume advanced `_last_trade_ts`
    # before the settle elapsed, and the backfill fetched from after the outage.
    c = _two_instrument_collector(tmp_path)
    pre_gap = _seed_trade(c, 1)
    fetch = _FakeFetch({_BYBIT: _fetched([])})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    now = time.time_ns()
    c._feed_last_ns["linear"] = now - 60 * _S
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 2), _LINEAR)  # resumes after silence
    _seed_trade(c, 3)
    _run_backfills(c, time.time_ns() + _BACKFILL_SETTLE_NS)
    ((_, since, _, _),) = fetch.calls
    assert since == pre_gap.ts_event - 5 * _S


def test_a_flip_uses_the_baseline_from_when_the_feed_went_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _two_instrument_collector(tmp_path)
    pre_gap = _seed_trade(c, 1)
    fetch = _FakeFetch({_BYBIT: _fetched([])})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    now = time.time_ns()
    c._poll_feed_states(now)
    c._client.states[_LINEAR] = False
    c._poll_feed_states(now)
    c._client.states[_LINEAR] = True
    _seed_trade(c, 2)  # delivered after the reconnect, before the poll saw it active
    c._poll_feed_states(now)
    _run_backfills(c, now + _BACKFILL_SETTLE_NS)
    ((_, since, _, _),) = fetch.calls
    assert since == pre_gap.ts_event - 5 * _S


def test_a_replay_lowers_the_baseline_to_the_replayed_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _two_instrument_collector(tmp_path)
    c._watchdog_started_ns -= _WATCHDOG_STARTUP_GRACE_NS
    old = _clocked_trade(1, time.time_ns() - 8 * _S, 0)
    c._process_data(old, _LINEAR)
    fetch = _FakeFetch({_BYBIT: _fetched([])})
    monkeypatch.setattr(trade_backfill, "fetch_trades", fetch)
    _seed_trade(c, 2)  # a gap trade the replay delivers before the one we already had
    c._process_data(old, _LINEAR)
    _run_backfills(c, time.time_ns() + _BACKFILL_SETTLE_NS)
    ((_, since, _, _),) = fetch.calls
    assert since == old.ts_event - 5 * _S


# -- review pass (story 22.14) ---------------------------------------------------------------------


def test_silence_is_judged_on_arrival_not_on_a_late_processing_time(tmp_path: Path) -> None:
    # A queue backlog: the message arrived 1 s after the previous one but is processed a minute on.
    c = _collector(tmp_path)
    t0 = time.time_ns() - 60 * _S
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=t0), _LINEAR)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=t0 + _S), _LINEAR)
    assert c._backfill_requests == {}
    assert c._feed_last_ns["linear"] == t0 + _S


def test_a_replay_older_than_the_age_filter_is_still_replay_evidence(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._watchdog_started_ns -= _WATCHDOG_STARTUP_GRACE_NS
    now = time.time_ns()
    c._process_data(_clocked_trade(7, now - 5 * _S, 0), _LINEAR)
    c._process_data(_clocked_trade(7, now - 30 * _S, 0), _LINEAR)  # replayed, past 10 s
    assert dict(c._duplicate_trades_dropped) == {_BYBIT: 1}
    assert dict(c._stale_trades_dropped) == {}
    assert c._backfill_requests["linear"].reasons == ["replayed trade ids"]


def test_a_live_copy_after_its_rest_copy_is_folded_live_but_not_archived_again(
    tmp_path: Path,
) -> None:
    c = _collector(tmp_path)
    c._register_trade(_BYBIT, "5", "rest")
    trade = _trade(100.0, 0.5, AggressorSide.BUYER, 5)
    c._process_data(trade, _LINEAR)
    assert [t.trade_id.value for t in c._second_trades[_BYBIT]] == ["5"]
    assert c._buffer[(TradeTick, _BYBIT)] == []
    assert c._trade_feeds_seen[_BYBIT]["5"] == ["rest", "linear"]


def test_a_secondary_feed_replaying_an_id_is_one_overlap_and_one_replay(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    trade = _seed_trade(c, 7, _LINEAR)
    c._process_data(trade, _LINEAR_TRADES)
    c._process_data(trade, _LINEAR_TRADES)
    assert dict(c._feed_overlap) == {("linear", "linear-trades"): 1}
    assert dict(c._duplicate_trades_dropped) == {_BYBIT: 1}


def test_an_unexpected_error_for_one_instrument_is_reported_and_the_others_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missed = _rest(99, time.time_ns() - _S, _SPOT_ID)
    results = {
        _BYBIT: AttributeError("'list' object has no attribute 'get'"),
        _SPOT_ID: _fetched([missed]),
    }
    c, detail = _backfill(tmp_path, monkeypatch, results)
    assert "AttributeError" in detail
    assert dict(c._trade_backfill_counts) == {_SPOT_ID: 1}


def test_a_cancelled_backfill_still_writes_its_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    _seed_trade(c, 1)
    _seed_trade(c, 2, iid=_SPOT_ID)
    monkeypatch.setattr(
        trade_backfill, "fetch_trades", _FakeFetch({_BYBIT: asyncio.CancelledError()})
    )
    c._schedule_backfill("linear", 0, "test")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(c._run_backfill("linear", c._backfill_requests.pop("linear")))
    assert (
        "interrupted after 0 instruments" in error_ledger.last_details()["collector.trade_backfill"]
    )


def test_the_unrecoverable_span_reaches_through_trades_refused_at_the_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # dYdX stopped at the 300 s floor: [oldest, floor) was fetched but refused, so nothing
    # before the newest refused trade reached the archive.
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    now = time.time_ns()
    last = now - 400 * _S
    refused = [_rest(1, now - 360 * _S), _rest(2, now - 320 * _S)]
    fetched = _fetched([*refused, _rest(3, now - 100 * _S)], reached=False)
    monkeypatch.setattr(trade_backfill, "fetch_trades", _FakeFetch({_BYBIT: fetched}))
    report = _BackfillReport("main", ["test"])
    asyncio.run(c._backfill_instrument(_BYBIT, last, report))
    assert (report.refused, report.backfilled) == (2, 1)
    assert report.unrecoverable == {_BYBIT: 80.0}


def test_a_subscribed_instrument_without_a_definition_is_an_error_not_no_baseline(
    tmp_path: Path,
) -> None:
    c = _two_instrument_collector(tmp_path)
    c._instruments = {}
    report = _BackfillReport("linear", ["test"])
    asyncio.run(c._backfill_instrument(_BYBIT, time.time_ns(), report))
    assert report.errors == {_BYBIT: "no instrument definition from the venue"}
    assert report.no_baseline == 0


def test_a_pending_backfill_at_shutdown_is_ledgered_as_abandoned(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    _seed_trade(c, 1)
    c._schedule_backfill("linear", time.time_ns(), "feed silent 7.0s")
    c._ledger_abandoned_backfills()
    detail = error_ledger.last_details()["collector.trade_backfill"]
    assert "feed linear (feed silent 7.0s): abandoned at shutdown, 1 instruments" in detail
    assert c._backfill_requests == {}


def test_a_failed_connect_closes_what_did_connect_and_re_raises(tmp_path: Path) -> None:
    class _HalfConnects(_ResyncClient):
        closed = False

        async def connect(self, loop: object, instruments: list) -> None:
            raise OSError("second socket refused")

        async def disconnect(self) -> None:
            self.closed = True

    client = _HalfConnects()
    c = _collector(tmp_path, client=client)
    with pytest.raises(OSError, match="second socket refused"):
        asyncio.run(c._connect([]))
    assert client.closed
