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
`research.strategies.candle_pattern_strategy` (Story 27.8), with real Nautilus objects only
(TEST-03).

The planted-hammer rows run end to end through `NodeRunner` -- one `BacktestNode`, one sweep over
`trend_condition` -- on a synthetic trade-tick catalog written with
`ParquetDataCatalog.write_data()`, the bars aggregated by Nautilus (`1-MINUTE-LAST-INTERNAL`).
The opposite-pattern, both-fire, ATR-stop and hole rows drive the strategy through a
`BacktestEngine` fed `EXTERNAL` bars (the matching engine fills against each bar), which is
cheaper than a node per scenario. The session engine in `conftest.py` keeps the Rust logger alive
across these constructions.
"""

import math
import random
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import msgspec
import pandas as pd
import pytest
from kernel.candle_patterns import NO_PATTERN
from kernel.candle_patterns import CandlePatternSet
from kernel.candle_patterns import PatternName
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.tests.test_candle_patterns import CASES

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.events import OrderCanceled
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.model.orders import StopMarketOrder
from nautilus_trader.model.position import Position
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.stubs import (
    events as stub_events,  # a module: pytest collects `Test*` names
)
from nautilus_trader.trading.config import StrategyFactory
from research.application.backtest_runner import NodeRunner
from research.application.patterns import CONDITIONS as SCANNER_CONDITIONS
from research.application.ports import DEFAULT_LATENCY_MS
from research.application.ports import RunSpec
from research.strategies.candle_pattern_strategy import _DIRECTIONS
from research.strategies.candle_pattern_strategy import CONDITIONS
from research.strategies.candle_pattern_strategy import CandlePatternStrategy
from research.strategies.candle_pattern_strategy import CandlePatternStrategyConfig


OHLC = tuple[float, float, float, float]

_IID = InstrumentId(Symbol("BTC-USD-PERP"), Venue("DYDX"))
_MINUTE = 60 * NS_PER_S
_T0 = 19_700 * NS_PER_DAY + 10 * 3_600 * NS_PER_S
_S = "research.strategies.candle_pattern_strategy:"
_EXTERNAL = BarType.from_str(f"{_IID}-1-MINUTE-LAST-EXTERNAL")

# Four closes falling 105 -> 103 (a 3-bar downtrend before the next bar), then a hammer: body 0.2,
# lower shadow 3.8, no upper shadow, closing at 104.0 -- above EMA(3) of the closes (103.73).
_DOWNTREND: list[OHLC] = [
    (105.2, 105.4, 104.9, 105.0),
    (105.0, 105.1, 104.4, 104.5),
    (104.5, 104.6, 103.9, 104.0),
    (104.0, 104.1, 103.4, 103.5),
    (103.5, 103.6, 102.9, 103.0),
]
_HAMMER: OHLC = (103.8, 104.0, 100.0, 104.0)
_HAMMER_MINUTE = len(_DOWNTREND)
_QUIET: list[OHLC] = [(104.0, 104.1, 103.9, 104.0)] * 8
_PLANTED = [*_DOWNTREND, _HAMMER, *_QUIET]
_EXIT_BARS = 3

# The hammer strategy of the planted rows: a short EMA and ATR so they warm up on the downtrend, a
# stop far away (104 - 20 * ATR(3) = 68), shorts off.
_HAMMER_PARAMS: dict[str, Any] = {
    "trade_size": "0.01",
    "long_patterns": ["HAMMER"],
    "short_patterns": [],
    "allow_short": False,
    "trend_ema_period": 3,
    "atr_period": 3,
    "stop_atr_multiple": 20.0,
    "exit_bars": _EXIT_BARS,
}


def _instrument(
    min_quantity: Quantity | None = None, max_quantity: Quantity | None = None
) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=_IID,
        raw_symbol=Symbol("BTC-USD-PERP"),
        base_currency=BTC,
        quote_currency=USDC,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price(0.1, 1),
        size_increment=Quantity(0.001, 3),
        max_quantity=max_quantity,
        min_quantity=min_quantity,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        ts_event=0,
        ts_init=0,
    )


def _close_stamp(minute: int) -> int:
    """Return the close stamp of the 1-minute bar starting `minute` minutes after `_T0`."""
    return _T0 + (minute + 1) * _MINUTE


def _ticks(ohlcs: Sequence[OHLC]) -> list[TradeTick]:
    """Four trades per minute, at its open, high, low and close, alternating aggressor sides."""
    ticks: list[TradeTick] = []
    for minute, ohlc in enumerate(ohlcs):
        for offset_s, price in zip((1, 15, 30, 50), ohlc, strict=True):
            ts = _T0 + minute * _MINUTE + offset_s * NS_PER_S
            side = AggressorSide.BUYER if len(ticks) % 2 else AggressorSide.SELLER
            trade_id = TradeId(str(len(ticks)))
            ticks.append(
                TradeTick(_IID, Price(price, 1), Quantity(0.01, 3), side, trade_id, ts, ts)
            )
    return ticks


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("planted_hammer_catalog")
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_instrument()])
    catalog.write_data(_ticks(_PLANTED))
    return str(root)


def _bar(minute: int, ohlc: OHLC, volume: float = 1.0) -> Bar:
    o, h, low, c = (Price(value, 1) for value in ohlc)
    stamp = _close_stamp(minute)
    return Bar(_EXTERNAL, o, h, low, c, Quantity(volume, 3), stamp, stamp)


def _bars(ohlcs: Sequence[OHLC], first_minute: int = 0) -> list[Bar]:
    return [_bar(first_minute + i, ohlc) for i, ohlc in enumerate(ohlcs)]


def _config(**overrides: Any) -> CandlePatternStrategyConfig:
    params = {**_HAMMER_PARAMS, "bar_type": str(_EXTERNAL), **overrides}
    return CandlePatternStrategyConfig.parse(
        msgspec.json.encode({"instrument_id": str(_IID), **params})
    )


def _engine(
    bars: list[Bar],
    strategy: CandlePatternStrategy,
    instrument: CryptoPerpetual | None = None,
    use_reduce_only: bool = True,
) -> BacktestEngine:
    """Return an engine on one DYDX venue with `bars` and `strategy` added (caller disposes)."""
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    engine.add_venue(
        Venue("DYDX"),
        OmsType.NETTING,
        AccountType.MARGIN,
        [Money(10_000, USDC)],
        base_currency=USDC,
        use_reduce_only=use_reduce_only,
    )
    engine.add_instrument(instrument or _instrument())
    engine.add_data(bars)
    engine.add_strategy(strategy)
    return engine


def _run_bars(bars: list[Bar], **overrides: Any) -> tuple[pd.DataFrame, list[Order]]:
    """Run the strategy over `bars` on a `BacktestEngine`; its positions report and orders."""
    engine = _engine(bars, CandlePatternStrategy(_config(**overrides)))
    try:
        engine.run()
        return engine.trader.generate_positions_report(), list(engine.cache.orders())
    finally:
        engine.dispose()


def _entries(report: pd.DataFrame) -> list[str]:
    return [] if report.empty else list(report["entry"])


def _stamps(report: pd.DataFrame, column: str) -> list[int]:
    return [pd.Timestamp(ts).value for ts in report[column]]


# --- end to end through NodeRunner: the planted hammer (matrix rows 1 and 2) -------------------


def test_a_planted_hammer_enters_at_its_close_and_exits_after_exit_bars(catalog: str) -> None:
    spec = RunSpec(
        catalog_path=catalog,
        instrument_ids=(str(_IID),),
        start=_T0,
        end=_T0 + len(_PLANTED) * _MINUTE,
        strategy_path=_S + "CandlePatternStrategy",
        config_path=_S + "CandlePatternStrategyConfig",
        params=_HAMMER_PARAMS,
        data="trades",
    )
    above, below = NodeRunner().sweep(
        spec, [{"trend_condition": "above"}, {"trend_condition": "below"}]
    )

    (trade,) = above.trades.trades
    assert trade.side == "LONG"
    # Submitted in on_bar of the closed hammer bar, so never inside it (no look-ahead), and filled
    # by the first trade after the order's latency (under 1 s): the next bar's opening trade, 1 s in.
    assert spec.latency_ms == DEFAULT_LATENCY_MS < 1_000
    assert trade.entry_ts == _close_stamp(_HAMMER_MINUTE) + NS_PER_S
    assert trade.exit_ts == trade.entry_ts + _EXIT_BARS * _MINUTE

    assert below.trades.trades == ()
    # One account state, the opening one: no fill ever moved the balance.
    assert below.equity.values.tolist() == [10_000.0]  # noqa: PD011 -- numpy, not pandas


def test_a_failing_trend_filter_submits_no_order() -> None:
    report, orders = _run_bars(_bars(_PLANTED), trend_condition="below")
    assert report.empty
    assert orders == []


# --- driven through a BacktestEngine: exits, both-fire, the stop, holes ------------------------

# After the hammer, three rising closes (an uptrend) and a shooting star: body 0.1, upper shadow
# 1.8, lower 0.1.
_RISE: list[OHLC] = [
    (104.0, 104.6, 103.9, 104.5),
    (104.5, 105.1, 104.4, 105.0),
    (105.0, 105.6, 104.9, 105.5),
]
_SHOOTING_STAR: OHLC = (105.6, 107.5, 105.5, 105.7)
_AFTER_STAR: list[OHLC] = [(105.7, 105.8, 105.6, 105.7)] * 5


def test_an_opposite_pattern_closes_the_long_at_that_bar_without_reversing() -> None:
    bars = _bars([*_DOWNTREND, _HAMMER, *_RISE, _SHOOTING_STAR, *_AFTER_STAR])
    report, orders = _run_bars(
        bars,
        short_patterns=["SHOOTING_STAR"],
        allow_short=True,
        trend_condition="any",
        exit_bars=50,
    )
    star_minute = _HAMMER_MINUTE + 1 + len(_RISE)
    assert _entries(report) == ["BUY"], "one long, and no short opened on the star's bar"
    assert _stamps(report, "ts_opened") == [_close_stamp(_HAMMER_MINUTE)]
    assert _stamps(report, "ts_closed") == [_close_stamp(star_minute)]
    stops = [o for o in orders if o.order_type == OrderType.STOP_MARKET]
    assert [(s.status, s.is_reduce_only) for s in stops] == [(OrderStatus.CANCELED, True)]
    assert len(orders) == 3, "entry, its stop and the close -- nothing else"


# The white bar `_SPINNING` is both a white spinning top (+100) and the small inside second bar of
# a bearish harami of `_WHITE_LONG` (-100).
_WHITE_LONG: OHLC = (100.0, 102.1, 99.9, 102.0)
_SPINNING: OHLC = (100.8, 101.6, 100.3, 101.1)
_FLAT: OHLC = (101.1, 101.1, 101.1, 101.1)


@pytest.mark.parametrize(
    ("short_patterns", "entries"),
    [(["HARAMI"], []), ([], ["BUY"])],
    ids=["both-fire", "long-alone"],
)
def test_long_and_short_on_one_bar_is_ambiguous_and_enters_nothing(
    short_patterns: list[str], entries: list[str]
) -> None:
    bars = _bars([_WHITE_LONG] * 4 + [_SPINNING] + [_FLAT] * 3)
    report, _ = _run_bars(
        bars,
        long_patterns=["SPINNING_TOP"],
        short_patterns=short_patterns,
        allow_short=True,
        trend_condition="any",
        exit_bars=2,
    )
    assert _entries(report) == entries


def test_the_atr_stop_rests_reduce_only_and_closes_the_long_when_hit() -> None:
    crash: OHLC = (104.0, 104.1, 60.0, 61.0)
    report, orders = _run_bars(
        _bars([*_DOWNTREND, _HAMMER, crash, *_QUIET]), stop_atr_multiple=20.0
    )

    atr = AverageTrueRange(3)
    for bar in _bars([*_DOWNTREND, _HAMMER]):
        atr.handle_bar(bar)
    (stop,) = [o for o in orders if o.order_type == OrderType.STOP_MARKET]
    assert stop.side == OrderSide.SELL
    assert stop.is_reduce_only
    assert stop.trigger_price == Price(104.0 - 20.0 * atr.value, 1)
    assert stop.status == OrderStatus.FILLED
    assert _stamps(report, "ts_closed") == [_close_stamp(_HAMMER_MINUTE + 1)]


def test_a_stop_price_at_or_below_zero_closes_the_position_at_once() -> None:
    report, orders = _run_bars(_bars(_PLANTED), stop_atr_multiple=1_000.0)
    assert _stamps(report, "ts_opened") == _stamps(report, "ts_closed")
    assert [o.order_type for o in orders] == [OrderType.MARKET, OrderType.MARKET]


def _with_hole(kind: str) -> list[Bar]:
    before = _bars(_DOWNTREND)
    if kind == "missing minute":
        return [*before, *_bars([_HAMMER, *_QUIET], first_minute=_HAMMER_MINUTE + 1)]
    # A bar with no trade continuing the downtrend: fed, it would keep the trend alive.
    empty = _bar(_HAMMER_MINUTE, (103.0, 103.0, 102.5, 102.5), volume=0.0)
    return [*before, empty, *_bars([_HAMMER, *_QUIET], first_minute=_HAMMER_MINUTE + 1)]


@pytest.mark.parametrize("kind", ["missing minute", "zero-volume bar"])
def test_a_hole_resets_every_indicator_before_the_next_bar(kind: str) -> None:
    report, orders = _run_bars(_with_hole(kind), trend_condition="any")
    assert report.empty, "the hammer's downtrend lies across the hole"
    assert orders == []


def test_the_same_bars_without_the_hole_trade() -> None:
    empty_as_traded = [
        *_bars(_DOWNTREND),
        _bar(_HAMMER_MINUTE, (103.0, 103.0, 102.5, 102.5)),
        *_bars([_HAMMER, *_QUIET], first_minute=_HAMMER_MINUTE + 1),
    ]
    report, _ = _run_bars(empty_as_traded, trend_condition="any")
    assert _entries(report) == ["BUY"]


# --- configuration -----------------------------------------------------------------------------


def _walk_fired(seed: int, count: int) -> set[tuple[PatternName, int]]:
    """Every (pattern, value) the kernel's full set fires over a seeded random walk."""
    rng = random.Random(seed)  # noqa: S311 -- a reproducible walk, not a secret
    patterns = CandlePatternSet()
    fired: set[tuple[PatternName, int]] = set()
    close = 100.0
    for _ in range(count):
        open_ = close + rng.gauss(0, 0.4)
        close = open_ + rng.gauss(0, 1.0)
        high = max(open_, close) + abs(rng.gauss(0, 0.5))
        low = min(open_, close) - abs(rng.gauss(0, 0.5))
        patterns.update_raw(open_, high, low, close)
        fired.update(patterns.fired())
    return fired


def test_the_directions_table_is_the_kernels() -> None:
    # The kernel's hand-drawn cases draw every direction each pattern has (its own tests pin that).
    drawn: dict[PatternName, set[int]] = {name: set() for name in PatternName}
    for pattern, _, value in CASES:
        if value != NO_PATTERN:
            drawn[pattern].add(value)
    assert {name: frozenset(values) for name, values in drawn.items()} == dict(_DIRECTIONS)
    for seed in (1, 2, 3):
        for name, value in _walk_fired(seed, 2000):
            assert value in _DIRECTIONS[name], (name, value)


def test_the_trend_conditions_are_the_scanners() -> None:
    assert CONDITIONS == SCANNER_CONDITIONS


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"long_patterns": ["HAMMERR"]}, "unknown pattern"),
        ({"long_patterns": ["DOJI"]}, "non-directional"),
        ({"short_patterns": ["HAMMER"]}, "never fires bearish"),
        ({"long_patterns": ["SHOOTING_STAR"]}, "never fires bullish"),
        ({"bar_type": f"{_IID}-100-TICK-LAST-INTERNAL"}, "time-aggregated"),
        ({"bar_type": "ETH-USD-PERP.DYDX-1-MINUTE-LAST-INTERNAL"}, "is not of instrument"),
        ({"bar_type": f"{_IID}-1-MONTH-LAST-EXTERNAL"}, "no fixed step"),
        ({"bar_type": f"{_IID}-1-YEAR-LAST-EXTERNAL"}, "no fixed step"),
        ({"exit_bars": 0}, "exit_bars"),
        ({"stop_atr_multiple": 0.0}, "stop_atr_multiple"),
        ({"stop_atr_multiple": -1.0}, "stop_atr_multiple"),
        ({"trend_condition": "up"}, "trend_condition"),
        ({"trade_size": "0"}, "trade_size"),
        ({"trade_size": "-0.01"}, "trade_size"),
        ({"trade_size": "NaN"}, "trade_size must be finite"),
        ({"trade_size": "Infinity"}, "trade_size must be finite"),
        ({"trend_ema_period": 0}, "trend_ema_period must be an int >= 1"),
        ({"atr_period": 0}, "atr_period must be an int >= 1"),
        ({"long_patterns": [], "allow_short": False}, "no pattern can open"),
    ],
)
def test_a_bad_config_raises_at_construction(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CandlePatternStrategy(_config(**overrides))


def test_an_unknown_parameter_fails_the_string_path_build() -> None:
    config = ImportableStrategyConfig(
        strategy_path=_S + "CandlePatternStrategy",
        config_path=_S + "CandlePatternStrategyConfig",
        config={"instrument_id": str(_IID), "exit_bar": 5},
    )
    with pytest.raises(msgspec.ValidationError, match="unknown field `exit_bar`"):
        StrategyFactory.create(config)


def test_the_default_bar_type_is_one_minute_internal_and_a_shared_name_one_detector() -> None:
    config = CandlePatternStrategyConfig(instrument_id=_IID)
    strategy = CandlePatternStrategy(config)
    assert str(strategy._bar_type) == f"{_IID}-1-MINUTE-LAST-INTERNAL"
    assert list(strategy._detectors) == [
        PatternName.HAMMER,
        PatternName.ENGULFING,
        PatternName.MORNING_STAR,
        PatternName.SHOOTING_STAR,
        PatternName.EVENING_STAR,
    ]


@pytest.mark.parametrize("multiple", [math.inf, math.nan])
def test_a_non_finite_stop_multiple_raises_at_construction(multiple: float) -> None:
    config = CandlePatternStrategyConfig(instrument_id=_IID, stop_atr_multiple=multiple)
    with pytest.raises(ValueError, match="stop_atr_multiple must be finite"):
        CandlePatternStrategy(config)


def test_a_trade_size_below_the_size_increment_stops_the_strategy_at_start() -> None:
    strategy = CandlePatternStrategy(_config(trade_size="0.0004"))  # size_increment is 0.001
    engine = _engine(_bars(_PLANTED), strategy)
    try:
        engine.run()
        assert strategy.instrument is None, "on_start refused the instrument"
        assert list(engine.cache.orders()) == []
    finally:
        engine.dispose()


# --- the stop is kept, not only placed ---------------------------------------------------------

# Four closes rising 100 -> 101.5 (an uptrend before the next bar), then a shooting star.
_UPTREND: list[OHLC] = [
    (99.5, 100.2, 99.4, 100.0),
    (100.0, 100.6, 99.9, 100.5),
    (100.5, 101.1, 100.4, 101.0),
    (101.0, 101.6, 100.9, 101.5),
]
_STAR_TOP: OHLC = (101.6, 103.5, 101.5, 101.7)


def test_a_short_rests_its_buy_stop_above_the_entry() -> None:
    bars = _bars([*_UPTREND, _STAR_TOP, *[(101.7, 101.8, 101.6, 101.7)] * 8])
    report, orders = _run_bars(
        bars,
        long_patterns=[],
        short_patterns=["SHOOTING_STAR"],
        allow_short=True,
        trend_condition="any",
        stop_atr_multiple=2.0,
    )
    assert _entries(report) == ["SELL"]
    (stop,) = [o for o in orders if isinstance(o, StopMarketOrder)]
    assert stop.side == OrderSide.BUY
    assert stop.is_reduce_only
    assert stop.trigger_price.as_double() > report["avg_px_open"].iloc[0]


def _cancel_stop_then_finish(bars: list[Bar], cancel_after_minute: int) -> list[Order]:
    """
    Run to the close of `cancel_after_minute`, cancel the resting stop through the strategy's own
    `cancel_order` (as an operator or a venue would), then run to the end; return every order.
    """
    strategy = CandlePatternStrategy(_config(trend_condition="any", exit_bars=8))
    engine = _engine(bars, strategy)
    try:
        engine.run(end=_close_stamp(cancel_after_minute), streaming=True)
        (stop,) = [o for o in engine.cache.orders() if isinstance(o, StopMarketOrder)]
        strategy.cancel_order(stop)
        engine.run(start=_close_stamp(cancel_after_minute) + 1)
        return list(engine.cache.orders())
    finally:
        engine.dispose()


def test_a_cancelled_stop_is_placed_again() -> None:
    orders = _cancel_stop_then_finish(_bars(_PLANTED), _HAMMER_MINUTE + 1)
    stops = [o for o in orders if isinstance(o, StopMarketOrder)]
    assert len(stops) == 2, "the cancelled stop and its replacement"
    assert stops[0].status == OrderStatus.CANCELED
    assert stops[1].quantity == stops[0].quantity
    # Re-priced from the ATR when it was placed again, still below the 104.0 entry.
    assert stops[1].trigger_price < Price(104.0, 1)
    closes = [o for o in orders if o.order_type == OrderType.MARKET and o.side == OrderSide.SELL]
    assert len(closes) == 1, "the time exit closed the protected position"


def test_a_lost_stop_after_a_hole_is_placed_again_at_the_entrys_distance() -> None:
    # A hole right after the entry resets the ATR(3); the stop is then cancelled one bar later,
    # before the ATR warms up again: the entry's stop distance still prices the replacement.
    after_hole = _HAMMER_MINUTE + 2
    bars = [*_bars([*_DOWNTREND, _HAMMER]), *_bars(_QUIET, first_minute=after_hole)]
    orders = _cancel_stop_then_finish(bars, after_hole)
    assert [(o.order_type, o.side) for o in orders] == [
        (OrderType.MARKET, OrderSide.BUY),
        (OrderType.STOP_MARKET, OrderSide.SELL),
        (OrderType.STOP_MARKET, OrderSide.SELL),
        (OrderType.MARKET, OrderSide.SELL),
    ], "the stop is placed again and the time exit closes the protected position"
    first, again = orders[1], orders[2]
    assert first.status == OrderStatus.CANCELED
    assert again.trigger_price == first.trigger_price


def _stop_event(engine: BacktestEngine, order: Order, event_type: type, ts: int) -> None:
    """Feed the execution engine a venue's `OrderCanceled`/`OrderRejected` for `order` at `ts`."""
    common = {
        "trader_id": order.trader_id,
        "strategy_id": order.strategy_id,
        "instrument_id": order.instrument_id,
        "client_order_id": order.client_order_id,
        "event_id": UUID4(),
        "ts_event": ts,
        "ts_init": ts,
    }
    account_id = AccountId("DYDX-001")
    if event_type is OrderCanceled:
        event = OrderCanceled(venue_order_id=order.venue_order_id, account_id=account_id, **common)
    else:
        event = OrderRejected(account_id=account_id, reason="test", **common)
    engine.kernel.exec_engine.process(event)


def test_a_stop_refused_twice_running_closes_the_position_at_any_timestamps() -> None:
    strategy = CandlePatternStrategy(_config(trend_condition="any", exit_bars=8))
    engine = _engine(_bars(_PLANTED), strategy)
    try:
        after_entry = _close_stamp(_HAMMER_MINUTE + 1)
        engine.run(end=after_entry, streaming=True)
        (stop,) = [o for o in engine.cache.orders() if isinstance(o, StopMarketOrder)]
        _stop_event(engine, stop, OrderCanceled, after_entry + 1)  # failure 1: placed again
        replacement = [o for o in engine.cache.orders_open() if isinstance(o, StopMarketOrder)]
        assert [o.status for o in engine.cache.orders_inflight()] == [OrderStatus.SUBMITTED]
        assert replacement == [], "the replacement is still in flight, not yet accepted"
        (again,) = engine.cache.orders_inflight()
        # Failure 2 at another instant (live every event has its own ts): no third stop.
        _stop_event(engine, again, OrderRejected, after_entry + 2 * NS_PER_S)
        submitted = list(engine.cache.orders())
        assert [(o.order_type, o.side) for o in submitted] == [
            (OrderType.MARKET, OrderSide.BUY),
            (OrderType.STOP_MARKET, OrderSide.SELL),
            (OrderType.STOP_MARKET, OrderSide.SELL),
            (OrderType.MARKET, OrderSide.SELL),
        ], "the second failure running closes the position instead"
    finally:
        engine.end()  # close the streaming run before disposing
        engine.dispose()


def test_an_accepted_stop_restarts_the_failure_count() -> None:
    strategy = CandlePatternStrategy(_config(trend_condition="any", exit_bars=8))
    engine = _engine(_bars(_PLANTED), strategy)
    try:
        after_entry = _close_stamp(_HAMMER_MINUTE + 1)
        engine.run(end=after_entry, streaming=True)
        for minute in (after_entry, after_entry + _MINUTE):
            (stop,) = [o for o in engine.cache.orders_open() if isinstance(o, StopMarketOrder)]
            _stop_event(engine, stop, OrderCanceled, minute + 1)  # the venue drops it
            engine.run(start=minute + 1, end=minute + _MINUTE, streaming=True)  # accepts anew
        stops = [o for o in engine.cache.orders() if isinstance(o, StopMarketOrder)]
        assert [o.status for o in stops] == [
            OrderStatus.CANCELED,
            OrderStatus.CANCELED,
            OrderStatus.ACCEPTED,
        ], "each loss after an accepted stop is a first failure: the stop is placed again"
        assert [
            o
            for o in engine.cache.orders()
            if o.side == OrderSide.SELL and o.order_type == OrderType.MARKET
        ] == []
    finally:
        engine.end()  # close the streaming run before disposing
        engine.dispose()


def test_an_off_grid_trade_size_stops_the_strategy_at_start() -> None:
    strategy = CandlePatternStrategy(_config(trade_size="0.0015"))  # size_increment is 0.001
    engine = _engine(_bars(_PLANTED), strategy)
    try:
        engine.run()
        assert strategy.instrument is None, "make_qty would have rounded it to 0.002"
        assert list(engine.cache.orders()) == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "limits",
    [{"min_quantity": Quantity(0.02, 3)}, {"max_quantity": Quantity(0.005, 3)}],
    ids=["below-min", "above-max"],
)
def test_a_trade_size_outside_the_quantity_limits_stops_the_strategy_at_start(
    limits: dict[str, Quantity],
) -> None:
    strategy = CandlePatternStrategy(_config(trade_size="0.01"))
    engine = _engine(_bars(_PLANTED), strategy, _instrument(**limits))
    try:
        engine.run()
        assert strategy.instrument is None, "every entry would be denied by the risk engine"
        assert list(engine.cache.orders()) == []
    finally:
        engine.dispose()


def _filled(order: Order, qty: str, px: float = 104.0, ts: int = 0) -> OrderFilled:
    return stub_events.TestEventStubs.order_filled(
        order,
        _instrument(),
        position_id=PositionId("P-1"),
        last_qty=Quantity.from_str(qty),
        last_px=Price(px, 1),
        ts_event=ts,
    )


def _long(ts_opened: int) -> tuple[Position, OrderFactory]:
    """Return a real 0.010 long opened at `ts_opened`, and the factory its orders come from."""
    factory = OrderFactory(TraderId("T-001"), StrategyId("S-001"), LiveClock())
    entry = factory.market(_IID, OrderSide.BUY, Quantity.from_str("0.010"))
    return Position(_instrument(), _filled(entry, "0.010", ts=ts_opened)), factory


def test_a_partly_filled_stop_still_covers_the_rest_of_the_position() -> None:
    position, factory = _long(0)
    stop = factory.stop_market(
        _IID, OrderSide.SELL, Quantity.from_str("0.010"), Price(90.0, 1), reduce_only=True
    )
    stop.apply(stub_events.TestEventStubs.order_submitted(stop))
    stop.apply(stub_events.TestEventStubs.order_accepted(stop))
    partial = _filled(stop, "0.004", px=90.0)
    stop.apply(partial)
    position.apply(partial)
    assert stop.status == OrderStatus.PARTIALLY_FILLED
    assert position.quantity == Quantity.from_str("0.006")
    assert CandlePatternStrategy._covers(stop, position), "never replaced while it executes"


def test_stop_state_belongs_to_one_position_and_the_entrys_atr() -> None:
    strategy = CandlePatternStrategy(_config(stop_atr_multiple=2.0))
    for bar in _bars([*_DOWNTREND, _HAMMER]):
        strategy._atr.handle_bar(bar)
    entry_distance = 2.0 * strategy._atr.value
    strategy._entry_distance = entry_distance  # as `_maybe_enter` sets it at the submit
    strategy._atr.reset()  # a hole between the submit and the fill

    first, _ = _long(ts_opened=1)
    strategy._track(first)
    strategy._stop_failures = 1
    assert strategy._distance() == entry_distance, "the pattern bar's ATR, not the reset one"

    strategy._track(first)
    assert (strategy._distance(), strategy._stop_failures) == (entry_distance, 1)

    # The same NETTING id reopened (say while the strategy was stopped and got no close event).
    reopened, _ = _long(ts_opened=2)
    strategy._track(reopened)
    assert (strategy._distance(), strategy._stop_failures) == (None, 0), "nothing carried over"


def test_a_stop_left_open_while_flat_is_cancelled_before_any_entry() -> None:
    strategy = CandlePatternStrategy(_config(trend_condition="any"))
    # The venue keeps a reduce-only order resting while flat, as a live one whose cancel was lost.
    engine = _engine(_bars([*_PLANTED, *_QUIET]), strategy, use_reduce_only=False)
    try:
        flat = _close_stamp(_HAMMER_MINUTE + _EXIT_BARS)
        engine.run(end=flat, streaming=True)
        assert engine.cache.positions_open() == []
        leftover = strategy.order_factory.stop_market(
            _IID, OrderSide.SELL, Quantity.from_str("0.010"), Price(50.0, 1), reduce_only=True
        )
        strategy.submit_order(leftover)  # as if the close's cancel of the stop had been lost
        assert not leftover.is_closed
        engine.run(start=flat + 1)
        assert leftover.status == OrderStatus.CANCELED
    finally:
        engine.end()  # close the streaming run before disposing
        engine.dispose()


def test_a_duplicate_or_late_bar_is_skipped_and_fed_to_nothing() -> None:
    last_down = len(_DOWNTREND) - 1
    duplicate = _bar(last_down, _DOWNTREND[-1])
    # Replayed after the last downtrend bar but stamped two minutes earlier, closing far higher.
    late = Bar(
        _EXTERNAL,
        Price(110.0, 1),
        Price(110.5, 1),
        Price(109.5, 1),
        Price(110.0, 1),
        Quantity(1.0, 3),
        _close_stamp(last_down - 2),
        _close_stamp(last_down),
    )
    bars = [*_bars(_DOWNTREND), duplicate, late, *_bars([_HAMMER, *_QUIET], _HAMMER_MINUTE)]
    report, _ = _run_bars(bars, trend_condition="any")
    # Fed, either bar would break the downtrend (a flat or a higher close) and no hammer would fire.
    assert _entries(report) == ["BUY"]
