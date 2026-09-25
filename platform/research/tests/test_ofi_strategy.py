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
Integration test: OFIStrategy in BacktestEngine with synthetic DydxSecondSnapshot data.

Six 1 s snapshots with 2 bid and 2 ask levels, the best bid's size growing 5 per second: every
update contributes +500 USD of OFI. `ofi_window=2` and `ofi_zscore_window=2` make the third
snapshot's OFI reading (1000 after 500) a z-score of exactly +1, so `ofi_threshold=0.5` enters
long and 999_999 never does. A TradeTick 0.5 s after each snapshot gives the simulated exchange a
market to fill against (see test_snapshot_strategy.py's docstring for why a custom data type
needs `CustomData` wrapping, a `client_id` and a parallel trade stream).

Story 24.4 repaired this file: commit 1031008cdd rewrote OFIStrategy from an OrderBookDeltas +
OrderFlowImbalance moving-average design (`ma_period`, `buy_threshold`, `sell_threshold`) into
the DydxSecondSnapshot + MultiLevelOFI z-score design (`ofi_zscore_window`, `ofi_threshold`) and
changed only these tests' import lines, so every test failed with `TypeError: Unexpected keyword
argument 'ma_period'`. Each test keeps its claim; only the input and the config field names
moved to the real API.

The session-scoped BacktestEngine in conftest.py keeps Nautilus's logging guard alive, so
several engines per module and per session are safe (it root-caused the old ordering abort).
"""

from decimal import Decimal

from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from research.strategies.ofi_strategy import OFIStrategy
from research.strategies.ofi_strategy import OFIStrategyConfig


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_PP = _INSTRUMENT.price_precision
_SP = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency
_NS_PER_S = 1_000_000_000


def _snapshot(ts: int, best_bid_size: float) -> CustomData:
    snapshot = DydxSecondSnapshot(
        instrument_id=_IID,
        bid_prices=[100.0, 99.0],
        bid_sizes=[best_bid_size, 5.0],
        ask_prices=[101.0, 102.0],
        ask_sizes=[10.0, 5.0],
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        ts_event=ts,
        ts_init=ts,
    )
    return CustomData(DataType(DydxSecondSnapshot), snapshot)


def _trade(ts: int, seq: int) -> TradeTick:
    return TradeTick(
        instrument_id=_IID,
        price=Price(101.0, _PP),
        size=Quantity(1.0, _SP),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId(str(seq)),
        ts_event=ts,
        ts_init=ts,
    )


def _engine() -> BacktestEngine:
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=_IID.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=_USDT,
        starting_balances=[Money(10_000, _USDT)],
        book_type=BookType.L1_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    snapshots, trades = _buy_pressure_data()
    engine.add_data(trades)
    engine.add_data(snapshots, client_id=ClientId(str(_IID.venue)))
    return engine


def _buy_pressure_data(n_updates: int = 5) -> tuple[list[CustomData], list[TradeTick]]:
    """
    Build a first snapshot, then `n_updates` more whose best-bid size grows: positive OFI. Each
    trade is 0.5 s after its snapshot, so the engine's time-ordered merge handles the snapshot
    (and the order it causes) before the trade that fills it.
    """
    snapshots = []
    trades = []
    for i in range(n_updates + 1):
        ts = (i + 1) * _NS_PER_S
        snapshots.append(_snapshot(ts, best_bid_size=10.0 + i * 5.0))
        trades.append(_trade(ts + _NS_PER_S // 2, i))
    return snapshots, trades


def test_ofi_strategy_generates_long_entry_on_bid_pressure() -> None:
    """Positive OFI z-score above threshold -> strategy opens a long position."""
    engine = _engine()

    config = OFIStrategyConfig(
        instrument_id=_IID,
        warmup_seconds=0,  # synthetic data spans ~6s; default 1800s warmup would block every entry
        ofi_window=2,
        ofi_zscore_window=2,
        ofi_threshold=0.5,
        trade_size=Decimal("0.001"),
        min_depth_levels=2,  # each snapshot holds 2 bid + 2 ask levels
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) >= 1, "expected at least one filled order"
    assert (fills["side"] == "BUY").any(), "expected a BUY fill from bid pressure signal"

    engine.reset()
    engine.dispose()


def test_ofi_strategy_no_trade_below_threshold() -> None:
    """With a very high ofi_threshold, the positive OFI z-score should not trigger entry."""
    engine = _engine()

    config = OFIStrategyConfig(
        instrument_id=_IID,
        warmup_seconds=0,  # isolate the threshold filter -- don't let warmup mask it
        ofi_window=2,
        ofi_zscore_window=2,
        ofi_threshold=999_999.0,  # impossibly high -> no entry
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

    config = OFIStrategyConfig(
        instrument_id=_IID,
        warmup_seconds=0,  # isolate the depth filter -- don't let warmup mask it
        ofi_window=2,
        ofi_zscore_window=2,
        ofi_threshold=0.5,
        trade_size=Decimal("0.001"),
        min_depth_levels=10,  # only 2 levels exist -> always filtered
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) == 0, "expected depth filter to block all entries"

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    test_ofi_strategy_generates_long_entry_on_bid_pressure()
    test_ofi_strategy_no_trade_below_threshold()
    test_ofi_strategy_depth_filter_blocks_entry()
    print("ok")
