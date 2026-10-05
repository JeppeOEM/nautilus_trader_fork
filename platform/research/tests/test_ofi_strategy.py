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

import pytest
from kernel.indicators import OFI_GAP_NS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

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
from research.strategies import ofi_strategy
from research.strategies.ofi_strategy import OFIStrategy
from research.strategies.ofi_strategy import OFIStrategyConfig


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_PP = _INSTRUMENT.price_precision
_SP = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency
_NS_PER_S = 1_000_000_000


def _snapshot(ts: int, best_bid_size: float, price_shift: float = 0.0) -> CustomData:
    snapshot = make_snapshot(
        instrument_id=_IID,
        bid_prices=[100.0 + price_shift, 99.0 + price_shift],
        bid_sizes=[best_bid_size, 5.0],
        ask_prices=[101.0 + price_shift, 102.0 + price_shift],
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


_Data = tuple[list[CustomData], list[TradeTick]]


def _engine(data: _Data | None = None) -> BacktestEngine:
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
    snapshots, trades = data if data is not None else _buy_pressure_data()
    engine.add_data(trades)
    engine.add_data(snapshots, client_id=ClientId(str(_IID.venue)))
    return engine


def _buy_pressure_data(n_updates: int = 5) -> _Data:
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


# The t=3 s row plus a hole 4 s wider than OFI_GAP_NS, whatever that threshold is set to.
_GAP_ROW_S = 3 + OFI_GAP_NS // _NS_PER_S + 4


def _pressure_then_gap_data() -> _Data:
    """
    Three growing-bid snapshots at t=1, 2, 3 s (the t=3 s reading is a z-score of +1, as in
    the module docstring), then one snapshot after a hole wider than `OFI_GAP_NS`, each with a
    trade 0.5 s after it so an order on any row has a market to fill against. The last row's
    bid grows by 10, not 5, so diffed as an ordinary row (no gap) it reads 1500 after 1000, a
    z-score of +1 too: the positive control below enters on it.
    """
    sizes = {1: 10.0, 2: 15.0, 3: 20.0, _GAP_ROW_S: 30.0}
    snapshots = [_snapshot(t * _NS_PER_S, best_bid_size=size) for t, size in sizes.items()]
    trades = [_trade(t * _NS_PER_S + _NS_PER_S // 2, i) for i, t in enumerate(sizes)]
    return snapshots, trades


def _run_pressure_then_gap() -> int:
    """Run `_pressure_then_gap_data` with warm-up ending exactly on its last row: the fill count."""
    engine = _engine(_pressure_then_gap_data())

    config = OFIStrategyConfig(
        instrument_id=_IID,
        warmup_seconds=_GAP_ROW_S - 1,  # measured from the first snapshot, at t=1 s
        ofi_window=2,
        ofi_zscore_window=2,
        ofi_threshold=0.5,  # the stale +1 z-score would clear it
        trade_size=Decimal("0.001"),
        min_depth_levels=2,
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    fill_count = len(engine.trader.generate_order_fills_report())
    engine.reset()
    engine.dispose()
    return fill_count


def test_ofi_strategy_does_not_trade_the_pre_gap_reading_on_the_post_gap_row() -> None:
    """
    DW-248: the first row after a gap only re-baselines MultiLevelOFI (its value is still the
    pre-gap reading), so the strategy must not evaluate on it. Warm-up ends exactly on that
    row, so it is the first row evaluation could happen on at all.
    """
    assert _run_pressure_then_gap() == 0, "expected no entry on the stale pre-gap OFI reading"


def test_ofi_strategy_trades_the_same_last_row_when_it_is_not_post_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Positive control for the test above: the same data and config with the gap threshold raised
    past the hole, so the last row is an ordinary diff, does enter. The zero fills above are
    therefore the gap rule, not a filter that blocks every entry on this data.
    """
    monkeypatch.setattr(ofi_strategy, "OFI_GAP_NS", 2 * _GAP_ROW_S * _NS_PER_S)
    assert _run_pressure_then_gap() >= 1, "expected an entry on the fresh last-row reading"


_MINUTE_S = 60


def _long_then_trend_flip_on_gap_row_data() -> tuple[_Data, int]:
    """
    Enter long in minute 0 (the bid-pressure rows at t=1..4 s), then one minute-1 row whose mid
    is 10 lower, then the post-gap row: the first of minute 2 and more than `OFI_GAP_NS` after
    the minute-1 row. Its minute roll feeds that lower mid to the EMAs, so the trend turns bear
    exactly on the post-gap row. Returns the data and that row's ts.
    """
    minute_1_s = 2 * _MINUTE_S - OFI_GAP_NS // _NS_PER_S - 2
    # The minute-1 row must land inside minute 1, after the t=1..4 s rows.
    assert _MINUTE_S <= minute_1_s < 2 * _MINUTE_S, "fixture needs OFI_GAP_NS < 58 s"
    rows = [(t, 10.0 + (t - 1) * 5.0, 0.0) for t in range(1, 5)]
    rows += [(minute_1_s, 30.0, -10.0), (2 * _MINUTE_S, 30.0, -10.0)]
    snapshots = [_snapshot(t * _NS_PER_S, size, shift) for t, size, shift in rows]
    trades = [_trade(t * _NS_PER_S + _NS_PER_S // 2, i) for i, (t, _, _) in enumerate(rows)]
    return (snapshots, trades), 2 * _MINUTE_S * _NS_PER_S


def test_ofi_strategy_trend_flip_exit_still_fires_on_the_post_gap_row() -> None:
    """
    The post-gap row skips only OFI-driven decisions: the trend-flip exit reads the minute
    EMAs, which that row has just updated, so a long is still closed there.
    """
    data, gap_row_ts = _long_then_trend_flip_on_gap_row_data()
    engine = _engine(data)

    config = OFIStrategyConfig(
        instrument_id=_IID,
        warmup_seconds=0,
        ofi_window=2,
        ofi_zscore_window=2,
        ofi_threshold=0.5,
        exit_on_zero=False,  # the trend flip is the only possible exit
        trade_size=Decimal("0.001"),
        min_depth_levels=2,
        trend_ema_fast=1,  # the EMAs warm after two minute rolls: the second is the gap row
        trend_ema_slow=2,
    )
    engine.add_strategy(OFIStrategy(config))
    engine.run()

    orders = engine.cache.orders(instrument_id=_IID)
    assert [o.side_string() for o in orders] == ["BUY", "SELL"], "expected one entry, one exit"
    assert orders[1].ts_init == gap_row_ts, "expected the trend-flip exit on the post-gap row"

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    # Outside pytest there is no conftest: hold one engine (and its logging guard) for the run.
    _log_guard = _engine()
    test_ofi_strategy_generates_long_entry_on_bid_pressure()
    test_ofi_strategy_no_trade_below_threshold()
    test_ofi_strategy_depth_filter_blocks_entry()
    test_ofi_strategy_does_not_trade_the_pre_gap_reading_on_the_post_gap_row()
    test_ofi_strategy_trend_flip_exit_still_fires_on_the_post_gap_row()
    print("ok")
