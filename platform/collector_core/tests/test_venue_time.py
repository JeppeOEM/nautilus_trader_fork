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
Venue-timed sampling (story 22.12): real OrderBookDeltas/TradeTick/catalog objects, no network
(TEST-01/03). Seconds are exchange seconds of a past UTC day, closed by calling `_sample_tick`
with the second, as `_venue_second_loop` does.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from ml_signals import candle_store
from ml_signals.catalog_stats import query_second_snapshots
from observability import error_ledger

from collector_core.collector import Collector
from collector_core.collector import _due_seconds
from collector_core.collector import _next_close_at
from collector_core.config import CoreConfig
from collector_core.config import core_config_from_dict
from collector_core.feed import MAIN_FEED
from collector_core.feed import Feed
from collector_core.rebuild_seconds import rebuild_day
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


_IID = "BTC-USD-PERP.HYPERLIQUID"
_S = 1_000_000_000
_D0 = 1_789_000_000 * _S // (86_400 * _S) * (86_400 * _S)  # a past UTC midnight
_SEC = _D0 // _S + 1000  # the exchange second most tests close


class _ResyncClient:
    """Delta-stream venue shape (Bybit): can force a fresh snapshot."""

    async def resync_orderbook(self, iid: str) -> None:
        pass


class _SnapshotClient:
    """Full-snapshot venue shape (Hyperliquid): no resync_orderbook, on purpose."""


def _collector(
    tmp_path: Path, source: str = "venue", client: object | None = None, hold_back: float = 0.0
) -> Collector:
    os.environ["CANDLES_DB_PATH"] = str(tmp_path / "candles.db")
    cfg = CoreConfig(
        environment="mainnet",
        catalog_path=str(tmp_path),
        instruments=(_IID,),
        stale_trade_seconds=10**9,  # fixed past timestamps must pass the age filter
        book_time_source=source,  # type: ignore[arg-type]
        hold_back_seconds=hold_back,
    )
    return Collector(cfg, client if client is not None else _SnapshotClient())


def _at(second: float) -> int:
    return int(second * _S)


def _book(bid: float, ask: float, event_s: float, init_s: float | None = None) -> OrderBookDeltas:
    """Build a full snapshot (Clear + one level a side) stamped `event_s`, received at `init_s`."""
    ts_event, ts_init = _at(event_s), _at(event_s if init_s is None else init_s)
    inst = InstrumentId.from_str(_IID)
    deltas = [OrderBookDelta.clear(inst, 0, ts_event, ts_init)]
    for side, price in ((OrderSide.BUY, bid), (OrderSide.SELL, ask)):
        order = BookOrder(side, Price(price, 2), Quantity(1.0, 3), 0)
        deltas.append(OrderBookDelta(inst, BookAction.ADD, order, 0, 0, ts_event, ts_init))
    return OrderBookDeltas(inst, deltas)


def _trade(n: int, event_s: float, init_s: float | None = None, price: float = 100.25) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(_IID),
        Price(price, 2),
        Quantity(0.5, 3),
        AggressorSide.BUYER,
        TradeId(str(n)),
        _at(event_s),
        _at(event_s if init_s is None else init_s),
    )


def _close(c: Collector, second: int, wall_s: float | None = None) -> list[DydxSecondSnapshot]:
    """Close `second` at wall `second + 1.2` (or `wall_s`), with a live feed."""
    now = _at(second + 1.2 if wall_s is None else wall_s)
    c._last_feed_message_ns = now
    return asyncio.run(c._sample_tick(now, second))


def test_reordered_deltas_are_applied_in_ts_event_order(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(101.0, 102.0, _SEC + 0.95))
    c._process_data(_book(100.0, 102.0, _SEC + 0.9))  # arrives second, happened first
    (row,) = _close(c, _SEC)
    assert row.bid_prices == [101.0]  # the S+0.95 book is the last one applied


def test_a_delta_after_the_boundary_waits_for_its_own_second(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.5))
    c._process_data(_book(101.0, 102.0, _SEC + 1.2))
    (row,) = _close(c, _SEC)
    assert row.bid_prices == [100.0]
    (next_row,) = _close(c, _SEC + 1)
    assert next_row.bid_prices == [101.0]


def test_a_venue_row_is_stamped_mid_exchange_second_and_sampled_at_wall(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.5))
    (row,) = _close(c, _SEC, wall_s=_SEC + 3.7)
    assert (row.ts_event, row.ts_init) == (_at(_SEC + 0.5), _at(_SEC + 3.7))


def test_trades_are_bucketed_by_exchange_second(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.1))
    c._process_data(_trade(1, _SEC + 0.99, init_s=_SEC + 1.05))  # in S, arrived in S+1
    c._process_data(_trade(2, _SEC + 1.01, init_s=_SEC + 1.02, price=101.0))  # in S+1
    (row,) = _close(c, _SEC)
    assert (row.buy_count, row.close_price) == (1, 100.25)


def test_dual_feed_copies_fold_once_into_their_exchange_second(tmp_path: Path) -> None:
    # 22.12 x 22.14: the trades-only socket's copy of a trade the book socket already delivered
    # is a duplicate_feed -- never a second fold into the venue-timed row.
    c = _collector(tmp_path)
    trades_feed = Feed("main-trades", MAIN_FEED.group, trades_only=True)
    c._process_data(_book(100.0, 102.0, _SEC + 0.1))
    c._process_data(_trade(1, _SEC + 0.5, init_s=_SEC + 0.6), MAIN_FEED)
    c._process_data(_trade(1, _SEC + 0.5, init_s=_SEC + 0.7), trades_feed)
    c._process_data(_trade(2, _SEC + 0.8, init_s=_SEC + 1.1), trades_feed)  # only this feed
    assert dict(c._duplicate_feed_dropped) == {_IID: 1}
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _IID)]] == ["1", "2"]
    (row,) = _close(c, _SEC)
    assert row.buy_count == 2


def test_a_live_copy_of_a_rest_backfilled_trade_folds_into_its_exchange_second(
    tmp_path: Path,
) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.1))
    c._register_trade(_IID, "5", "rest")  # the backfill archived it first
    c._process_data(_trade(5, _SEC + 0.99, init_s=_SEC + 1.05))  # in S, arrived in S+1
    assert c._buffer[(TradeTick, _IID)] == []  # never archived twice
    (row,) = _close(c, _SEC)
    assert (row.buy_count, row.close_price) == (1, 100.25)
    assert c._second_trades == {}  # not in the arrival-second list


def test_a_late_trade_is_archived_counted_excluded_live_and_rebuilt_into_its_second(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC - 0.5))
    c._process_data(_trade(1, _SEC - 0.5))  # on time: the archive starts before second S
    _close(c, _SEC - 1)
    _close(c, _SEC)
    c._process_data(_book(100.0, 102.0, _SEC + 1.5))
    c._process_data(_trade(2, _SEC + 0.7, init_s=_SEC + 1.9))  # second S already closed
    assert c._late_trades[_IID] == 1
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _IID)]] == ["1", "2"]
    (live,) = _close(c, _SEC + 1)
    assert live.buy_count == 0  # not folded into the arrival second either
    c._report_stale_trades()
    assert error_ledger.counts()["collector.late_trade"] == 1
    asyncio.run(c._flush_once(final=True))
    rebuild_day(str(tmp_path), _IID, _D0, apply=True)
    rows = {s.ts_event // _S: s for s in query_second_snapshots(str(tmp_path), _IID, 0, 1 << 62)}
    assert (rows[_SEC].buy_count, rows[_SEC].close_price) == (1, 100.25)


def test_a_trade_stamped_far_ahead_of_arrival_is_archived_and_counted(tmp_path: Path) -> None:
    c = _collector(tmp_path, hold_back=1.0)
    c._process_data(_trade(1, _SEC + 6.5, init_s=_SEC))  # 6.5 s > hold-back 1 s + 5 s
    assert c._ahead_trades[_IID] == 1
    assert _IID not in c._venue_trades
    assert len(c._buffer[(TradeTick, _IID)]) == 1


def test_a_held_delta_past_the_bound_drops_the_book_and_queues_a_resync(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path, client=_ResyncClient())
    c._process_data(_book(100.0, 102.0, _SEC + 0.5))
    _close(c, _SEC)
    c._process_data(_book(101.0, 102.0, _SEC + 30.0, init_s=_SEC + 1.3))  # venue clock ahead
    _close(c, _SEC + 1)
    c._check_pending_overflow(_at(_SEC + 6.4))  # held 5.1 s
    assert error_ledger.counts() == {"collector.pending_deltas": 1}
    assert (_IID in c._pending_deltas, _IID in c._live_books) == (False, False)
    assert c._resync_pending == {_IID}


def test_a_held_delta_within_the_bound_is_kept(tmp_path: Path) -> None:
    c = _collector(tmp_path, hold_back=2.0)
    c._process_data(_book(101.0, 102.0, _SEC + 3.0, init_s=_SEC + 1.3))
    c._check_pending_overflow(_at(_SEC + 8.2))  # held 6.9 s <= 2 + 5 s
    assert len(c._pending_deltas[_IID]) == 1


def test_overflow_on_a_snapshot_venue_only_clears_the_book(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(101.0, 102.0, _SEC + 30.0, init_s=_SEC))
    c._check_pending_overflow(_at(_SEC + 6))
    assert (_IID in c._pending_deltas, c._resync_pending) == (False, set())


def test_venue_book_age_is_judged_on_exchange_time(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC - 5.5))  # 6.5 s before the end of S (> 5 s)
    c._process_data(_trade(1, _SEC + 0.5))
    assert _close(c, _SEC) == []
    assert _SEC not in c._venue_trades.get(_IID, {})  # the skipped second's trades go too
    assert c._late_trades[_IID] == 0


def test_watchdog_tracker_is_stamped_on_arrival_not_ts_event(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    before = time.time_ns()
    c._process_data(_book(100.0, 102.0, _SEC))  # ts_event long ago
    assert c._last_book_update_ns[_IID] >= before


def test_a_catch_up_closes_every_due_second_at_its_own_boundary(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    for k, bid in enumerate((100.0, 101.0, 102.0)):
        c._process_data(_book(bid, 103.0, _SEC + k + 0.5))
    wall = _at(_SEC + 3.1)
    rows = [_close(c, s, wall_s=_SEC + 3.1)[0] for s in _due_seconds(wall, 0, _SEC - 1)]
    assert [r.bid_prices[0] for r in rows] == [100.0, 101.0, 102.0]
    assert {r.ts_init for r in rows} == {wall}


def test_due_seconds_and_next_close_follow_the_hold_back() -> None:
    now = _at(_SEC + 2.6)
    assert list(_due_seconds(now, 0, None)) == [_SEC + 1]
    assert list(_due_seconds(now, _at(0.5), _SEC - 2)) == [_SEC - 1, _SEC, _SEC + 1]
    assert list(_due_seconds(now, _at(1.0), _SEC)) == []  # S+1 closes at S+3.0
    assert _next_close_at(_SEC + 2.6, 1.0, _SEC) == _SEC + 3.0
    assert _next_close_at(_SEC + 2.6, 0.0, None) == _SEC + 3.0


def test_a_stall_catches_up_at_most_thirty_seconds() -> None:
    assert len(_due_seconds(_at(_SEC + 3600), 0, _SEC)) == 30


def test_a_full_minute_of_venue_rows_is_fully_observed_in_the_candle_store(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    minute = _SEC - _SEC % 60
    rows = []
    for s in range(minute, minute + 60):
        c._process_data(_book(100.0, 102.0, s + 0.2))
        c._process_data(_trade(s, s + 0.4))
        rows += _close(c, s)
    candle_store.apply_batch(c._candle_db, {_IID: rows})
    (bar,) = candle_store.window(c._candle_db, _IID, 60, (minute + 60) * 1000, 1)
    assert (bar["t"], bar["seconds_observed"]) == (minute * 1000, 60)


def test_arrival_mode_never_holds_deltas_or_buckets_trades(tmp_path: Path) -> None:
    c = _collector(tmp_path, source="arrival")
    c._process_data(_book(100.0, 102.0, _SEC + 0.5))
    c._process_data(_trade(1, _SEC + 0.6))
    assert (dict(c._pending_deltas), dict(c._venue_trades)) == ({}, {})
    assert _IID in c._live_books
    assert len(c._second_trades[_IID]) == 1


def test_a_venue_row_is_carried_with_a_trade_group_that_arrived_before_it(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    now = time.time_ns()
    second = now // _S - 2
    c._process_data(_book(100.0, 102.0, second + 0.5))
    c._process_data(_trade(1, second + 0.6, init_s=(now - _S // 2) / _S))  # young: carried
    c._last_feed_message_ns = now
    (row,) = asyncio.run(c._sample_tick(now, second))  # ts_event is 1.5 s+ before the trade
    asyncio.run(c._flush_once())
    assert c._buffer[(DydxSecondSnapshot, _IID)] == [row]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"book_time_source": "exchange"}, "book_time_source"),
        ({"book_time_source": "venue", "hold_back_seconds": -0.5}, ">= 0"),
        ({"book_time_source": "venue", "hold_back_seconds": float("nan")}, "finite"),
        ({"book_time_source": "venue", "hold_back_seconds": float("inf")}, "finite"),
        ({"hold_back_seconds": 1.0}, "requires book_time_source"),
        ({"book_time_source": "venue", "snapshot_interval_seconds": 0.5}, "= 1.0"),
    ],
)
def test_invalid_time_source_config_is_refused(raw: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        core_config_from_dict(raw, ("mainnet",))


def test_venue_config_with_a_hold_back_loads() -> None:
    cfg = core_config_from_dict(
        {"book_time_source": "venue", "hold_back_seconds": 1.5}, ("mainnet",)
    )
    assert (cfg.book_time_source, cfg.hold_back_seconds) == ("venue", 1.5)


# -- review patches ---------------------------------------------------------------------------


def test_trades_before_the_first_close_are_logged_not_counted_late(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.5))
    c._process_data(_trade(1, _SEC - 3.5))  # received before the loop closed anything
    _close(c, _SEC)
    assert (c._pre_start_trades[_IID], c._late_trades[_IID]) == (1, 0)


def test_a_catch_up_ledgers_one_crossed_episode_once(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._process_data(_book(103.0, 102.0, _SEC + 0.5))  # crossed
    for second in (_SEC, _SEC + 1, _SEC + 2):
        _close(c, second, wall_s=_SEC + 3.1)  # one wake-up, one now_ns
    assert error_ledger.counts() == {"collector.crossed_book": 1}


def test_a_late_message_does_not_age_the_book(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.8))
    _close(c, _SEC)
    c._process_data(_book(100.5, 102.0, _SEC + 0.6))  # late: its second already closed
    _close(c, _SEC + 1)
    assert c._book_event_ns[_IID] == _at(_SEC + 0.8)
    assert c._late_deltas[_IID] == 1


def test_dropping_the_book_counts_its_held_messages(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_book(100.0, 102.0, _SEC + 0.5))
    c._process_data(_book(100.0, 102.0, _SEC + 0.6))
    c._clear_book_state(_IID)
    assert c._deltas_before_snapshot_dropped[_IID] == 2


def test_snapshot_queries_window_on_ts_event_not_sampling_time(tmp_path: Path) -> None:
    c = _collector(tmp_path, hold_back=2.0)
    for second in (_SEC, _SEC + 1):
        c._process_data(_book(100.0, 102.0, second + 0.5))
        _close(c, second, wall_s=second + 3.1)  # sampled 2.6 s after ts_event
    asyncio.run(c._flush_once(final=True))
    rows = query_second_snapshots(str(tmp_path), _IID, _at(_SEC + 1), _at(_SEC + 2))
    assert [r.ts_event for r in rows] == [_at(_SEC + 1.5)]
