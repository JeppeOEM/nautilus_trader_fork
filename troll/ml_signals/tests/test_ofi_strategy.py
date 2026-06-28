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
Integration test: OFIStrategy in BacktestEngine with synthetic L2 book data.

Uses ofi_window=2, ma_period=2 so only 5 delta batches are needed to
trigger the first MA evaluation.  Bid size grows each batch → positive OFI
→ MA > buy_threshold → long entry.
"""

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from ml_signals.ofi_strategy import OFIStrategy
from ml_signals.ofi_strategy import OFIStrategyConfig


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID  = _INSTRUMENT.id
_PP   = _INSTRUMENT.price_precision
_SP   = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency


def _delta(action: BookAction, side: OrderSide, price: float, size: float, ts: int, seq: int) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, _PP), size=Quantity(size, _SP), order_id=0)
    return OrderBookDelta(instrument_id=_IID, action=action, order=order, flags=0, sequence=seq, ts_event=ts, ts_init=ts)


def _batch(deltas: list[OrderBookDelta]) -> OrderBookDeltas:
    return OrderBookDeltas(instrument_id=_IID, deltas=deltas)


def _engine() -> BacktestEngine:
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=_IID.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=_USDT,
        starting_balances=[Money(10_000, _USDT)],
        book_type=BookType.L2_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    return engine


def _buy_pressure_data(n_update_batches: int = 5) -> list[OrderBookDeltas]:
    """
    Initial snapshot (ADD 2 bid + 2 ask levels), then n update batches where
    the best bid size increases.  Growing bid size → positive OFI contributions.
    """
    ts = 1_000_000_000
    step = 1_000_000_000

    snapshot = _batch([
        _delta(BookAction.ADD, OrderSide.BUY,  100.0, 10.0, ts, 0),
        _delta(BookAction.ADD, OrderSide.BUY,   99.0,  5.0, ts, 1),
        _delta(BookAction.ADD, OrderSide.SELL, 101.0, 10.0, ts, 2),
        _delta(BookAction.ADD, OrderSide.SELL, 102.0,  5.0, ts, 3),
    ])
    batches = [snapshot]
    for i in range(1, n_update_batches + 1):
        ts += step
        batches.append(_batch([
            _delta(BookAction.UPDATE, OrderSide.BUY, 100.0, 10.0 + i * 5.0, ts, i * 10),
        ]))
    return batches


def test_ofi_strategy_generates_long_entry_on_bid_pressure() -> None:
    """Positive OFI MA above threshold → strategy opens a long position."""
    engine = _engine()
    engine.add_data(_buy_pressure_data())

    config = OFIStrategyConfig(
        instrument_id=_IID,
        ofi_window=2,
        ma_period=2,
        buy_threshold=0.5,
        sell_threshold=-0.5,
        trade_size=Decimal("0.001"),
        min_depth_levels=2,   # 2 bid + 2 ask levels are added in snapshot
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) >= 1, "expected at least one filled order"
    assert (fills["side"] == "BUY").any(), "expected a BUY fill from bid pressure signal"

    engine.reset()
    engine.dispose()


def test_ofi_strategy_no_trade_below_ma_threshold() -> None:
    """With a very high buy_threshold, the positive OFI MA should not trigger entry."""
    engine = _engine()
    engine.add_data(_buy_pressure_data())

    config = OFIStrategyConfig(
        instrument_id=_IID,
        ofi_window=2,
        ma_period=2,
        buy_threshold=999_999.0,   # impossibly high → no entry
        sell_threshold=-999_999.0,
        trade_size=Decimal("0.001"),
        min_depth_levels=2,
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) == 0, "expected no fills when threshold is unreachable"

    engine.reset()
    engine.dispose()


def test_ofi_strategy_depth_filter_blocks_entry() -> None:
    """Setting min_depth_levels > available levels blocks all entries."""
    engine = _engine()
    engine.add_data(_buy_pressure_data())

    config = OFIStrategyConfig(
        instrument_id=_IID,
        ofi_window=2,
        ma_period=2,
        buy_threshold=0.5,
        sell_threshold=-0.5,
        trade_size=Decimal("0.001"),
        min_depth_levels=10,   # only 2 levels exist → always filtered
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) == 0, "expected depth filter to block all entries"

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    test_ofi_strategy_generates_long_entry_on_bid_pressure()
    test_ofi_strategy_no_trade_below_ma_threshold()
    test_ofi_strategy_depth_filter_blocks_entry()
    print("ok")
