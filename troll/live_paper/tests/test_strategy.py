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
Integration test: DummyStrategy in a raw BacktestEngine (Story 3.2), mirroring
ml_signals/tests/test_ofi_strategy.py's established pattern.

Thresholds are deliberately set so entries are guaranteed/blocked independent of whether
OnlineLogisticTrend's online SGD has actually "learned" a direction yet -- it starts at
value=0.5 (neutral) until enough bars accumulate, and this story is about proving the
signal-wiring/order-submission plumbing works end to end (AC1/AC2), not about signal
quality (explicitly out of scope per the story's Dev Notes -- "Dummy Strategy" is
deliberately unsophisticated).

bar_spec uses "-MID-INTERNAL" rather than the module's live default ("-LAST-INTERNAL") so
the trend indicator's bars can be driven entirely from the QuoteTick stream already being
fed for Microprice/OrderFlowImbalance, without needing a separate TradeTick stream.
"""

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs

from live_paper.strategy import DummyStrategy
from live_paper.strategy import DummyStrategyConfig


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_PP = _INSTRUMENT.price_precision
_SP = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency

_TS_START = 1_000_000_000
_STEP_NS = 1_000_000_000  # 1 second, matching book_snapshot_interval_secs=1.0


def _delta(
    action: BookAction,
    side: OrderSide,
    price: float,
    size: float,
    ts: int,
    seq: int,
) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, _PP), size=Quantity(size, _SP), order_id=0)
    return OrderBookDelta(
        instrument_id=_IID,
        action=action,
        order=order,
        flags=0,
        sequence=seq,
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
        book_type=BookType.L2_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    return engine


def _quotes_and_deltas(n_seconds: int, levels_per_side: int) -> list:
    """
    n_seconds of QuoteTicks (growing bid size -> positive OFI) each paired with a matching
    L2 book update (growing best-bid size -> positive MultiLevelOFI/OBI), one per second.
    """
    data: list = []
    seq = 0
    for i in range(levels_per_side):
        data.append(_delta(BookAction.ADD, OrderSide.BUY, 100.0 - i, 10.0, _TS_START, seq))
        seq += 1
    for i in range(levels_per_side):
        data.append(_delta(BookAction.ADD, OrderSide.SELL, 101.0 + i, 10.0, _TS_START, seq))
        seq += 1

    for i in range(1, n_seconds + 1):
        ts = _TS_START + i * _STEP_NS
        bid_size = 10.0 + i * 5.0
        data.append(
            TestDataStubs.quote_tick(
                instrument=_INSTRUMENT,
                bid_price=100.0,
                ask_price=101.0,
                bid_size=bid_size,
                ask_size=10.0,
                ts_event=ts,
                ts_init=ts,
            ),
        )
        data.append(_delta(BookAction.UPDATE, OrderSide.BUY, 100.0, bid_size, ts, seq))
        seq += 1
    return data


def _config(**overrides) -> DummyStrategyConfig:
    defaults = {
        "instrument_id": _IID,
        "trade_size": Decimal("0.001"),
        "bar_spec": "1-SECOND-MID-INTERNAL",
        "trend_lookback": 2,
        "ofi_levels": 2,
        "ofi_window": 2,
        "obi_levels": 2,
    }
    defaults.update(overrides)
    return DummyStrategyConfig(**defaults)


def test_dummy_strategy_all_indicators_initialize_and_enters_position() -> None:
    """
    trend_buy_threshold=0.49 is below OnlineLogisticTrend's neutral starting value (0.5),
    so the long signal fires as soon as trend initializes, regardless of learned direction.
    ofi_confirm_threshold is set arbitrarily low so MultiLevelOFI always confirms.
    """
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))

    strategy = DummyStrategy(
        _config(
            trend_buy_threshold=0.49, trend_sell_threshold=0.1, ofi_confirm_threshold=-999_999.0
        ),
    )
    engine.add_strategy(strategy)
    engine.run()

    assert strategy.microprice.initialized, "Microprice never initialized"
    assert strategy.ofi.initialized, "OrderFlowImbalance never initialized"
    assert strategy.obi.initialized, "MultiLevelOBI never initialized"
    assert strategy.mlofi.initialized, "MultiLevelOFI never initialized"
    assert strategy.trend.initialized, "OnlineLogisticTrend never initialized"

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) >= 1, "expected at least one filled order once all signals agree"

    engine.reset()
    engine.dispose()


def test_dummy_strategy_no_trade_when_thresholds_unreachable() -> None:
    """Impossible thresholds -> signals stay alive (indicators initialize) but no entry fires."""
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))

    strategy = DummyStrategy(_config(trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001))
    engine.add_strategy(strategy)
    engine.run()

    assert strategy.trend.initialized, "trend should still initialize even without an entry"
    fills = engine.trader.generate_order_fills_report()
    assert len(fills) == 0, "expected no fills when thresholds are unreachable"

    engine.reset()
    engine.dispose()


def test_dummy_strategy_stops_on_invalid_thresholds() -> None:
    """
    trend_sell_threshold >= trend_buy_threshold must stop the strategy in on_start,
    before any subscription happens -- no indicator should ever initialize.
    """
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=5, levels_per_side=2))

    strategy = DummyStrategy(_config(trend_buy_threshold=0.4, trend_sell_threshold=0.6))
    engine.add_strategy(strategy)
    engine.run()  # must not raise -- on_start calls self.stop(), not an exception

    assert not strategy.microprice.initialized, "no data should ever reach a stopped strategy"
    assert not strategy.trend.initialized, "no data should ever reach a stopped strategy"

    engine.reset()
    engine.dispose()


def test_dummy_strategy_stops_on_non_internal_bar_spec() -> None:
    """
    A bar_spec without INTERNAL aggregation must stop the strategy in on_start (see
    module docstring: EXTERNAL has not been verified to actually deliver bars live).
    """
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=5, levels_per_side=2))

    strategy = DummyStrategy(_config(bar_spec="1-SECOND-MID-EXTERNAL"))
    engine.add_strategy(strategy)
    engine.run()  # must not raise

    assert not strategy.microprice.initialized, "no data should ever reach a stopped strategy"

    engine.reset()
    engine.dispose()


def test_dummy_strategy_thin_book_does_not_crash() -> None:
    """
    A book thinner than the configured obi_levels/ofi_levels (1 level vs. 2 configured)
    must not raise -- MultiLevelOBI/MultiLevelOFI already slice defensively for length
    (see indicators.py), and the strategy's own book-snapshot-building code must too.
    """
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=10, levels_per_side=1))

    strategy = DummyStrategy(_config())
    engine.add_strategy(strategy)
    engine.run()  # must not raise

    engine.reset()
    engine.dispose()
