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
"""Unit tests for MinuteBarBuilder: OHLCV accumulation, enrichment, minute rollover."""

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

from dydx_collector.minute_bars import DydxMinuteBar
from dydx_collector.minute_bars import MinuteBarBuilder


IID = "ETH-USD-PERP.DYDX"
IID_OBJ = InstrumentId.from_str(IID)

_MINUTE_NS = 60_000_000_000
_BASE_TS = 100 * _MINUTE_NS  # minute 100


def _trade(price: float, size: float, ts: int) -> TradeTick:
    return TradeTick(
        instrument_id=IID_OBJ,
        price=Price(price, 2),
        size=Quantity(size, 2),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId("T"),
        ts_event=ts,
        ts_init=ts,
    )


def _delta_batch(side: OrderSide, price: float, size: float, ts: int, action: BookAction = BookAction.ADD) -> OrderBookDeltas:
    order = BookOrder(side=side, price=Price(price, 2), size=Quantity(size, 2), order_id=0)
    delta = OrderBookDelta(
        instrument_id=IID_OBJ, action=action, order=order,
        flags=0, sequence=0, ts_event=ts, ts_init=ts,
    )
    return OrderBookDeltas(instrument_id=IID_OBJ, deltas=[delta])


# ---- OHLCV accumulation ----

def test_single_trade_emitted_on_next_minute() -> None:
    builder = MinuteBarBuilder()
    ts_min0 = _BASE_TS
    ts_min1 = _BASE_TS + _MINUTE_NS

    # Trade in minute 0, flush at minute 1 → bar for minute 0 emitted
    bars = builder.update(IID, trades=[_trade(100.0, 1.0, ts_min0)], delta_batches=[], marks=[], now_ns=ts_min1)
    assert len(bars) == 1
    assert bars[0].open == bars[0].high == bars[0].low == bars[0].close == 100.0
    assert bars[0].volume == 1.0
    assert bars[0].trade_count == 1
    assert bars[0].ts_event == ts_min0


def test_ohlcv_across_multiple_trades_in_one_minute() -> None:
    builder = MinuteBarBuilder()
    ts = _BASE_TS

    trades = [
        _trade(100.0, 1.0, ts),
        _trade(105.0, 2.0, ts + 10),
        _trade(98.0,  1.0, ts + 20),
        _trade(102.0, 3.0, ts + 30),
    ]
    # Flush at next minute to emit
    bars = builder.update(IID, trades=trades, delta_batches=[], marks=[], now_ns=ts + _MINUTE_NS)
    assert len(bars) == 1
    b = bars[0]
    assert b.open == 100.0
    assert b.high == 105.0
    assert b.low == 98.0
    assert b.close == 102.0
    assert abs(b.volume - 7.0) < 1e-9
    assert b.trade_count == 4


def test_two_complete_minutes_in_one_flush() -> None:
    builder = MinuteBarBuilder()
    ts0 = _BASE_TS
    ts1 = _BASE_TS + _MINUTE_NS

    trades = [_trade(100.0, 1.0, ts0), _trade(200.0, 2.0, ts1)]
    # Flush at minute 2 → both minutes complete
    bars = builder.update(IID, trades=trades, delta_batches=[], marks=[], now_ns=ts1 + _MINUTE_NS)
    assert len(bars) == 2
    assert bars[0].close == 100.0
    assert bars[1].close == 200.0


def test_no_bar_without_trades() -> None:
    builder = MinuteBarBuilder()
    # Only deltas, no trades → no bar
    deltas = [_delta_batch(OrderSide.BUY, 100.0, 5.0, _BASE_TS)]
    bars = builder.update(IID, trades=[], delta_batches=deltas, marks=[], now_ns=_BASE_TS + _MINUTE_NS)
    assert bars == []


# ---- Enrichment fields ----

def test_spread_and_microprice_populated_from_deltas() -> None:
    builder = MinuteBarBuilder()
    ts = _BASE_TS

    deltas = [
        _delta_batch(OrderSide.BUY,  100.0, 4.0, ts),   # bid
        _delta_batch(OrderSide.SELL, 102.0, 2.0, ts + 1),  # ask
    ]
    trades = [_trade(101.0, 1.0, ts + 2)]
    bars = builder.update(IID, trades=trades, delta_batches=deltas, marks=[], now_ns=ts + _MINUTE_NS)

    assert len(bars) == 1
    b = bars[0]
    assert b.spread is not None
    assert abs(b.spread - 2.0) < 1e-9   # 102 - 100
    assert b.microprice is not None
    # microprice = (100*2 + 102*4) / (4+2) = (200+408)/6 = 101.33...
    expected_micro = (100.0 * 2.0 + 102.0 * 4.0) / 6.0
    assert abs(b.microprice - expected_micro) < 1e-9


def test_ofi_accumulated_from_deltas() -> None:
    builder = MinuteBarBuilder()
    ts = _BASE_TS

    # First batch seeds prev state; second increases bid size → positive OFI contribution
    deltas = [
        _delta_batch(OrderSide.BUY,  100.0, 5.0, ts),
        _delta_batch(OrderSide.SELL, 101.0, 5.0, ts + 1),
        _delta_batch(OrderSide.BUY,  100.0, 8.0, ts + 2, BookAction.UPDATE),  # bid size up by 3
    ]
    trades = [_trade(100.5, 1.0, ts + 3)]
    bars = builder.update(IID, trades=trades, delta_batches=deltas, marks=[], now_ns=ts + _MINUTE_NS)

    assert len(bars) == 1
    assert bars[0].ofi is not None
    assert bars[0].ofi > 0  # bid side grew → positive OFI


def _sell_trade(price: float, size: float, ts: int) -> TradeTick:
    return TradeTick(
        instrument_id=IID_OBJ,
        price=Price(price, 2),
        size=Quantity(size, 2),
        aggressor_side=AggressorSide.SELLER,
        trade_id=TradeId("T"),
        ts_event=ts,
        ts_init=ts,
    )


# ---- buy/sell volume ----

def test_buy_sell_volume_split() -> None:
    builder = MinuteBarBuilder()
    ts = _BASE_TS + 1
    trades = [_trade(100.0, 3.0, ts), _sell_trade(100.0, 1.0, ts)]
    bars = builder.update(IID, trades, [], [], now_ns=_BASE_TS + _MINUTE_NS)
    assert len(bars) == 1
    assert bars[0].buy_volume == 3.0
    assert bars[0].sell_volume == 1.0
    assert bars[0].volume == 4.0


# ---- absorption ----

def test_buy_absorption_when_ask_holds() -> None:
    # Buy trade followed by a delta that doesn't move the ask → absorbed
    builder = MinuteBarBuilder()
    ts = _BASE_TS + 1
    # Seed the book with a bid and ask
    bid_delta = _delta_batch(OrderSide.BUY, 99.0, 10.0, ts)
    ask_delta = _delta_batch(OrderSide.SELL, 101.0, 5.0, ts)
    # Delta that doesn't move the ask (ADD on bid side)
    bid_delta2 = _delta_batch(OrderSide.BUY, 99.5, 2.0, ts + 2)

    trade = _trade(101.0, 2.0, ts + 1)

    bars = builder.update(
        IID,
        trades=[trade],
        delta_batches=[bid_delta, ask_delta, bid_delta2],
        marks=[],
        now_ns=_BASE_TS + _MINUTE_NS,
    )
    assert len(bars) == 1
    assert bars[0].buy_absorption == 2.0  # ask held → buy was absorbed
    assert bars[0].sell_absorption == 0.0


def test_no_absorption_when_ask_moves() -> None:
    # Ask moves after buy trade → not absorbed
    builder = MinuteBarBuilder()
    ts = _BASE_TS + 1
    bid_delta = _delta_batch(OrderSide.BUY, 99.0, 10.0, ts)
    ask_delta = _delta_batch(OrderSide.SELL, 101.0, 5.0, ts)
    # Delta that removes the ask level → ask will move
    ask_remove = _delta_batch(OrderSide.SELL, 101.0, 5.0, ts + 2, action=BookAction.DELETE)

    trade = _trade(101.0, 2.0, ts + 1)

    bars = builder.update(
        IID,
        trades=[trade],
        delta_batches=[bid_delta, ask_delta, ask_remove],
        marks=[],
        now_ns=_BASE_TS + _MINUTE_NS,
    )
    assert len(bars) == 1
    assert bars[0].buy_absorption == 0.0


if __name__ == "__main__":
    test_single_trade_emitted_on_next_minute()
    test_ohlcv_across_multiple_trades_in_one_minute()
    test_two_complete_minutes_in_one_flush()
    test_no_bar_without_trades()
    test_spread_and_microprice_populated_from_deltas()
    test_ofi_accumulated_from_deltas()
    print("ok")
