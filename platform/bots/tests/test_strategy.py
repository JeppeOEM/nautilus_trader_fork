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
research/tests/test_ofi_strategy.py's established pattern.

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

import pytest

from bots.infrastructure.cache_reader import own_open_orders
from bots.strategies.dummy import DummyStrategy
from bots.strategies.dummy import DummyStrategyConfig
from bots.strategies.signal_log import SIGNAL_LONG
from bots.tests.test_replay import _INPUTS as _REVERSING_INPUTS
from bots.tests.test_replay import _data as _reversing_data
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.events import OrderEvent
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs


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


def test_a_bot_reads_only_its_own_position_never_the_instruments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DW-226 (AD-11): two bots on one instrument share the account-wide portfolio figures, so a
    # flat bot that read them would believe itself long on the other bot's position.
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))
    trading = DummyStrategy(
        _config(
            order_id_tag="A",
            trend_buy_threshold=0.49,
            trend_sell_threshold=0.1,
            ofi_confirm_threshold=-999_999.0,
        ),
    )
    idle = DummyStrategy(
        _config(order_id_tag="B", trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001)
    )
    engine.add_strategy(trading)
    engine.add_strategy(idle)
    engine.run(streaming=True)

    own = trading._own_position()
    assert own is not None
    assert own.is_long
    assert idle.portfolio.is_net_long(_IID), "the account-wide figure blends in bot A"
    assert idle._own_position() is None
    # B's decision on a long signal is a flat entry; the blended read would have held instead.
    monkeypatch.setattr(idle, "_signal", lambda trend, mlofi: SIGNAL_LONG)
    assert idle._wanted_side(idle._own_position()) == OrderSide.BUY

    _close(engine)


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


# --- Story 29.6: bracket exits -------------------------------------------------------------------

_ALWAYS_LONG = {
    "trend_buy_threshold": 0.49,
    "trend_sell_threshold": 0.1,
    "ofi_confirm_threshold": -999_999.0,
}


def _bracket_run(**overrides: object) -> tuple[BacktestEngine, DummyStrategy]:
    """Run to the end of the data but leave the strategy running (no on_stop cancel yet)."""
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))
    strategy = DummyStrategy(_config(**{**_ALWAYS_LONG, **overrides}))
    engine.add_strategy(strategy)
    engine.run(streaming=True)
    return engine, strategy


def _close(engine: BacktestEngine) -> None:
    engine.end()
    engine.dispose()


def test_first_bracket_entry_rests_one_stop_loss_and_one_take_profit() -> None:
    # The book never moves (bid 100.00 / ask 101.00, mid 100.5): 100 bps each side of the mid is
    # 101.505 up to the take-profit's 101.51 and 99.495 down to the stop's 99.49.
    engine, strategy = _bracket_run(take_profit_bps=100, stop_loss_bps=100)
    try:
        resting = own_open_orders(strategy)
        by_type = {order.order_type: order for order in resting}
        assert len(resting) == 2
        assert set(by_type) == {OrderType.STOP_MARKET, OrderType.LIMIT}
        assert str(by_type[OrderType.STOP_MARKET].trigger_price) == "99.49"
        assert str(by_type[OrderType.LIMIT].price) == "101.51"
        assert all(order.is_reduce_only and order.side == OrderSide.SELL for order in resting)
        # Held by the OrderEmulator until the market reaches them (bots.strategies.exits).
        assert all(order.is_emulated for order in resting)
        assert strategy.portfolio.is_net_long(_IID)
        # One entry only: the bot holds its protected long.
        assert len(engine.cache.orders(strategy_id=strategy.id)) == 3
    finally:
        _close(engine)


@pytest.mark.parametrize(
    ("overrides", "expected_type"),
    [
        ({"take_profit_bps": 100}, OrderType.LIMIT),
        ({"stop_loss_bps": 100}, OrderType.STOP_MARKET),
    ],
)
def test_one_configured_leg_rests_alone(overrides: dict, expected_type: OrderType) -> None:
    engine, strategy = _bracket_run(**overrides)
    try:
        resting = own_open_orders(strategy)
        assert [order.order_type for order in resting] == [expected_type]
        assert resting[0].is_reduce_only
    finally:
        _close(engine)


def test_stopping_the_bot_cancels_its_resting_exits() -> None:
    engine, strategy = _bracket_run(take_profit_bps=100, stop_loss_bps=100)
    try:
        engine.end()  # stops the strategy: on_stop cancels what it rests
        assert own_open_orders(strategy) == []
        statuses = {
            order.order_type: order.status
            for order in engine.cache.orders(strategy_id=strategy.id)
            if order.is_reduce_only
        }
        assert statuses == {
            OrderType.STOP_MARKET: OrderStatus.CANCELED,
            OrderType.LIMIT: OrderStatus.CANCELED,
        }
    finally:
        engine.dispose()


def test_a_reversal_leaves_no_orphaned_exit() -> None:
    # The replay fixture's triangular mid flips the trend signal back and forth; the exits sit
    # 50% away so none of them ever fills and every position change is a signal reversal.
    engine = _engine()
    engine.add_data(_reversing_data())
    strategy = DummyStrategy(
        _config(
            trend_buy_threshold=_REVERSING_INPUTS["trend_buy_threshold"],
            trend_sell_threshold=_REVERSING_INPUTS["trend_sell_threshold"],
            ofi_confirm_threshold=-999_999.0,
            take_profit_bps=5_000,
            stop_loss_bps=5_000,
        )
    )
    engine.add_strategy(strategy)
    open_at_flatten: list[int] = []

    def _on_event(event: OrderEvent) -> None:
        if isinstance(event, OrderFilled) and strategy.portfolio.is_flat(_IID):
            open_at_flatten.append(len(own_open_orders(strategy)))

    strategy.msgbus.subscribe(topic=f"events.order.{strategy.id}", handler=_on_event)
    engine.run(streaming=True)
    try:
        assert len(open_at_flatten) >= 2, "expected the data to reverse the bot at least twice"
        assert open_at_flatten == [0] * len(open_at_flatten)
        resting = own_open_orders(strategy)
        # The final position is protected by exactly its own two legs, nothing older.
        assert sorted(order.order_type for order in resting) == [
            OrderType.LIMIT,
            OrderType.STOP_MARKET,
        ]
    finally:
        _close(engine)


def test_a_reversal_whose_signal_fades_still_flattens() -> None:
    # A reversal cancels the exits one cycle before its flatten: if the signal is gone by then,
    # the flatten must still go out, or the long would sit with no stop at all.
    engine, strategy = _bracket_run(take_profit_bps=100, stop_loss_bps=100)
    try:
        assert strategy.portfolio.is_net_long(_IID)
        signals = iter([OrderSide.SELL, None])
        strategy._wanted_side = lambda position: next(signals)
        strategy._trade_with_exits(strategy._own_position())
        assert own_open_orders(strategy) == [], "the reversal cancels the resting exits first"
        strategy._trade_with_exits(strategy._own_position())
        flattens = [
            order
            for order in engine.cache.orders(strategy_id=strategy.id)
            if order.side == OrderSide.SELL and order.order_type == OrderType.MARKET
        ]
        assert len(flattens) == 1
        assert flattens[0].is_reduce_only
    finally:
        _close(engine)


def _more_quotes(first_second: int, n_seconds: int) -> list[QuoteTick]:
    """Quotes on the same unmoving book, continuing after `_quotes_and_deltas`' data."""
    return [
        TestDataStubs.quote_tick(
            instrument=_INSTRUMENT,
            bid_price=100.0,
            ask_price=101.0,
            bid_size=100.0,
            ask_size=10.0,
            ts_event=_TS_START + second * _STEP_NS,
            ts_init=_TS_START + second * _STEP_NS,
        )
        for second in range(first_second, first_second + n_seconds)
    ]


def test_a_reversal_flattens_the_whole_position_not_trade_size() -> None:
    # A position that is not `trade_size` (here grown to twice it; a partly filled flatten leaves
    # the same mismatch) must be closed exactly: a `trade_size` flatten would leave part of the
    # position without exits, or overshoot into an opposite one that has none.
    engine, strategy = _bracket_run(take_profit_bps=100, stop_loss_bps=100)
    try:
        add = strategy.order_factory.market(
            instrument_id=_IID,
            order_side=OrderSide.BUY,
            quantity=_INSTRUMENT.make_qty(Decimal("0.001")),
        )
        strategy.submit_order(add)
        engine.add_data(_more_quotes(16, 1))
        engine.run(streaming=True)
        assert strategy.portfolio.net_position(_IID) == Decimal("0.002")
        signals = iter([OrderSide.SELL])
        strategy._wanted_side = lambda position: next(signals, None)
        strategy._trade_with_exits(strategy._own_position())  # cancels the exits
        strategy._trade_with_exits(strategy._own_position())  # sends the flatten
        engine.add_data(_more_quotes(17, 3))
        engine.run(streaming=True)
        flattens = [
            order
            for order in engine.cache.orders(strategy_id=strategy.id)
            if order.side == OrderSide.SELL and order.order_type == OrderType.MARKET
        ]
        assert [(str(order.quantity), order.is_reduce_only) for order in flattens] == [
            ("0.002000", True)
        ]
        assert strategy.portfolio.is_flat(_IID)
    finally:
        _close(engine)


def test_a_flat_bot_keeps_the_legs_of_an_entry_still_working() -> None:
    # Right after the entry list is sent the bot still reads flat and its legs already rest in
    # the emulator: they belong to a working entry, not to a closed position.
    engine, strategy = _bracket_run(
        take_profit_bps=100, stop_loss_bps=100, trend_buy_threshold=2.0, trend_sell_threshold=-1.0
    )
    try:
        assert strategy.portfolio.is_flat(_IID), "thresholds out of reach: no entry of its own"
        strategy._enter(OrderSide.BUY)
        legs = [
            order for order in engine.cache.orders(strategy_id=strategy.id) if order.is_reduce_only
        ]
        assert len(legs) == 2, "the entry list's legs rest before its fill is applied"
        cancels: list[object] = []
        strategy.cancel_all_orders = lambda *args, **kwargs: cancels.append(args)
        strategy._trade_with_exits(None)
        assert cancels == []
    finally:
        _close(engine)


def _with_trades_at_mid(data: list) -> list:
    """Add a trade at each quote's mid: the emulated exits trigger on the last trade price."""
    with_trades: list = []
    for item in data:
        with_trades.append(item)
        if isinstance(item, QuoteTick):
            mid = (item.bid_price.as_double() + item.ask_price.as_double()) / 2
            with_trades.append(
                TestDataStubs.trade_tick(
                    instrument=_INSTRUMENT,
                    price=mid,
                    trade_id=str(len(with_trades)),
                    ts_event=item.ts_event,
                    ts_init=item.ts_init,
                )
            )
    return with_trades


def test_a_filled_exit_leaves_no_orphan_into_the_next_position() -> None:
    # Exits 1 bp away on a market that moves 20.0: positions end at a stop or take-profit, and the
    # OUO sibling cancel (or the flat-with-orders guard) clears the other leg before re-entry.
    engine = _engine()
    engine.add_data(_with_trades_at_mid(_reversing_data()))
    strategy = DummyStrategy(
        _config(
            trend_buy_threshold=_REVERSING_INPUTS["trend_buy_threshold"],
            trend_sell_threshold=_REVERSING_INPUTS["trend_sell_threshold"],
            ofi_confirm_threshold=-999_999.0,
            take_profit_bps=1,
            stop_loss_bps=1,
        )
    )
    engine.add_strategy(strategy)
    open_at_entry: list[int] = []

    def _on_event(event: OrderEvent) -> None:
        if not isinstance(event, OrderFilled):
            return
        order = engine.cache.order(event.client_order_id)
        if order is not None and "ENTRY" in (order.tags or []):
            # The entry's own legs are emulated the moment it fills; anything else is an orphan.
            orphans = [
                resting
                for resting in own_open_orders(strategy)
                if resting.parent_order_id != order.client_order_id
            ]
            open_at_entry.append(len(orphans))

    strategy.msgbus.subscribe(topic=f"events.order.{strategy.id}", handler=_on_event)
    engine.run(streaming=True)
    try:
        exits_filled = [
            order
            for order in engine.cache.orders(strategy_id=strategy.id)
            if order.is_reduce_only and order.status == OrderStatus.FILLED
        ]
        assert len(exits_filled) >= 2, "expected exits to fill"
        assert len(open_at_entry) >= 2
        assert open_at_entry == [0] * len(open_at_entry)
        # Whatever rests at the end belongs to the last entry: flat leaves nothing behind.
        entries = [
            order
            for order in engine.cache.orders(strategy_id=strategy.id)
            if "ENTRY" in (order.tags or [])
        ]
        last_entry = max(entries, key=lambda order: order.ts_init)
        resting = own_open_orders(strategy)
        assert all(order.parent_order_id == last_entry.client_order_id for order in resting)
        if strategy.portfolio.is_flat(_IID):
            assert resting == []
    finally:
        _close(engine)


@pytest.mark.parametrize(
    "overrides",
    [
        {"take_profit_bps": 0},
        {"stop_loss_bps": -5},
        {"stop_loss_bps": 10_000},
        {"take_profit_bps": 10_000},
        {"stop_loss_bps": True},
    ],
)
def test_dummy_strategy_stops_on_invalid_bps(overrides: dict) -> None:
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=5, levels_per_side=2))
    strategy = DummyStrategy(_config(**{**_ALWAYS_LONG, **overrides}))
    engine.add_strategy(strategy)
    engine.run()  # must not raise -- on_start calls self.stop()

    assert not strategy.microprice.initialized, "no data should ever reach a stopped strategy"
    assert engine.trader.generate_order_fills_report().empty

    engine.reset()
    engine.dispose()
