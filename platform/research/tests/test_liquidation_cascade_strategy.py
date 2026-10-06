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
`LiquidationCascadeStrategy` (Story 33.14): config validation, the exact precision rescale of a
liquidation's notional, and one planted backtest through `backtest_liquidation_cascade.run` --
`BacktestNode` streaming real `Liquidation` rows from `custom_liquidation` into the strategy.

The planted catalog (`BTCUSDT-LINEAR.BYBIT`, price precision 2, size 3): one snapshot per second
for 900 s (its top of book is the derived quote the exchange fills against), a background of one
LONG liquidation every 60 s for t = 0..540, a 90 s LONG burst (one per second, t = 660..749, the
mid falling 1.00 a second) and the decay. Every liquidation is received at `t + 0.2 s`.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import msgspec
import pandas as pd
import pytest
from kernel.clocks import NS_PER_DAY
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common import component
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.trading.config import StrategyFactory
from research.application.liquidations import replay_cascade
from research.strategies import backtest_liquidation_cascade
from research.strategies.liquidation_cascade_strategy import _TIMER
from research.strategies.liquidation_cascade_strategy import UNSCALABLE_ROW_SITE
from research.strategies.liquidation_cascade_strategy import LiquidationCascadeStrategy
from research.strategies.liquidation_cascade_strategy import LiquidationCascadeStrategyConfig
from research.strategies.liquidation_cascade_strategy import _SignalLog
from research.strategies.liquidation_cascade_strategy import start_fields


_S = "research.strategies.liquidation_cascade_strategy:"
_IID = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
_NS = 1_000_000_000
_T0 = 1_788_000_000 * _NS
_RECEIVED_NS = 200_000_000  # each liquidation's ts_init after its second
_SECONDS = 900
_BURST = range(660, 750)


def _instrument() -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=_IID,
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=None,
        min_quantity=None,
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


def _config(**overrides: Any) -> LiquidationCascadeStrategyConfig:
    params = {"stop_pct": 0.05, **overrides}
    return LiquidationCascadeStrategyConfig.parse(
        msgspec.json.encode({"instrument_id": str(_IID), **params})
    )


# --- config ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"stop_pct": None}, "exactly one of stop_pct and stop_atr_multiple"),
        ({"stop_atr_multiple": 2.0}, "exactly one of stop_pct and stop_atr_multiple"),
        ({"stop_pct": 0.0}, "stop_pct must be finite and > 0"),
        ({"stop_pct": 1.0}, "stop_pct must be in \\(0, 1\\)"),
        ({"stop_pct": 1.5}, "stop_pct must be in \\(0, 1\\)"),
        ({"take_profit_r": -1.0}, "take_profit_r must be"),
        ({"stop_pct": None, "stop_atr_multiple": -1.0}, "stop_atr_multiple must be"),
        ({"take_profit_r": 0.0}, "take_profit_r must be"),
        ({"sides": []}, "sides must be a non-empty"),
        ({"sides": ["flat"]}, "sides: unknown"),
        ({"mode": "chase"}, "mode must be one of"),
        ({"trade_size": "0"}, "trade_size"),
        ({"min_episode_notional": "-1"}, "min_episode_notional must be"),
        ({"max_daily_loss": "NaN"}, "max_daily_loss must be"),
        ({"entry_timeout_s": 0}, "entry_timeout_s must be an int >= 1"),
        ({"max_entries_per_episode": 0}, "max_entries_per_episode"),
        ({"cooldown_s": -1}, "cooldown_s must be an int >= 0"),
        ({"decay_ratio": 1.0}, "decay_ratio"),
        (
            {
                "stop_pct": None,
                "stop_atr_multiple": 2.0,
                "bar_type": "ETHUSDT-LINEAR.BYBIT-1-MINUTE-MID-INTERNAL",
            },
            "is not of instrument",
        ),
        # The replay and the backtest feed quotes only: a trade-built or external bar never comes.
        ({"bar_type": "BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-INTERNAL"}, "quotes' mid"),
        ({"bar_type": "BTCUSDT-LINEAR.BYBIT-1-MINUTE-BID-INTERNAL"}, "quotes' mid"),
        ({"bar_type": "BTCUSDT-LINEAR.BYBIT-1-MINUTE-MID-EXTERNAL"}, "aggregated internally"),
        ({"bar_type": "ETHUSDT-LINEAR.BYBIT-1-MINUTE-MID-INTERNAL"}, "is not of instrument"),
    ],
)
def test_a_bad_config_raises_at_construction(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        LiquidationCascadeStrategy(_config(**overrides))


def test_a_quote_built_mid_bar_of_the_instrument_is_accepted() -> None:
    config = _config(stop_pct=None, stop_atr_multiple=2.0, bar_type=f"{_IID}-5-MINUTE-MID-INTERNAL")
    LiquidationCascadeStrategy(config)  # no raise


def test_an_unknown_parameter_fails_the_string_path_build() -> None:
    config = ImportableStrategyConfig(
        strategy_path=_S + "LiquidationCascadeStrategy",
        config_path=_S + "LiquidationCascadeStrategyConfig",
        config={"instrument_id": str(_IID), "stop_pct": 0.05, "stop_percent": 0.05},
    )
    with pytest.raises(msgspec.ValidationError, match="unknown field `stop_percent`"):
        StrategyFactory.create(config)


def test_the_start_fields_are_every_own_field_but_the_log_path() -> None:
    fields = start_fields(_config(signal_log_path="logs/bot.jsonl", sides=["short", "long"]))
    assert "signal_log_path" not in fields
    assert "order_id_tag" not in fields  # a StrategyConfig field, logged as `bot_id`
    assert fields["sides"] == ["short", "long"]
    assert fields["min_episode_notional"] == "0"
    assert fields["instrument_id"] == str(_IID)
    assert fields["stop_pct"] == 0.05


# --- the notional at the definition's precisions -----------------------------------------------


def _row(price_units: int, size_units: int, precisions: tuple[int, int]) -> Liquidation:
    return Liquidation(_IID, LiquidatedSide.LONG, size_units, price_units, *precisions, "k", 0, 0)


def _strategy_on_the_instrument() -> LiquidationCascadeStrategy:
    strategy = LiquidationCascadeStrategy(_config())
    strategy.instrument = _instrument()  # what on_start sets
    return strategy


def test_a_row_at_the_definition_precisions_feeds_its_own_notional() -> None:
    # 0.010 BTC at 60000.10: 10 x 6 000 010 units of 10^-5 USDT (600.0010 USDT).
    assert _strategy_on_the_instrument().units_of(_row(6_000_010, 10, (2, 3))) == 60_000_100


def test_a_finer_row_is_rescaled_exactly_to_the_definition() -> None:
    # 60000.100 at precision 3 x 0.0100 at 4: 60 000 100 x 100 = 6 000 010 000 units of 10^-7,
    # exactly 60 000 100 units of the definition's 10^-5 (600.00100 USDT).
    strategy = _strategy_on_the_instrument()
    assert strategy.units_of(_row(60_000_100, 100, (3, 4))) == 60_000_100
    assert strategy.unscalable_rows == 0


def test_a_coarser_row_is_scaled_up_exactly() -> None:
    # 60000.1 at precision 1 x 0.01 at 2: 600 001 x 1 units of 10^-3 = 60 000 100 at 10^-5.
    assert _strategy_on_the_instrument().units_of(_row(600_001, 1, (1, 2))) == 60_000_100


def test_a_row_the_definition_cannot_hold_is_counted_ledgered_and_not_fed() -> None:
    # 60000.105 x 0.0101 = 606.0010605 USDT: seven decimals, the definition holds five.
    error_ledger.reset()
    try:
        strategy = _strategy_on_the_instrument()
        assert strategy.units_of(_row(60_000_105, 101, (3, 4))) is None
        assert strategy.unscalable_rows == 1
        assert error_ledger.counts() == {UNSCALABLE_ROW_SITE: 1}  # DATA-07: never silent
        assert UNSCALABLE_ROW_SITE == "research.liquidation_cascade.unscalable_row"
    finally:
        error_ledger.reset()


# --- a row older than the window ---------------------------------------------------------------


def _liquidation_at(ts_init: int) -> Liquidation:
    # 0.001 BTC at 10 000.00: 1 000 000 units of 10^-5 USDT.
    return Liquidation(_IID, LiquidatedSide.LONG, 1, 1_000_000, 2, 3, str(ts_init), 0, ts_init)


def test_a_row_received_before_the_window_is_counted_and_not_fed() -> None:
    strategy = _strategy_on_the_instrument()  # window_s 30
    strategy._cascade.advance(1_000 * _NS)  # the detector's clock: 1 000 s
    strategy.on_data(_liquidation_at(970 * _NS))  # exactly clock - window: just expired
    strategy.on_data(_liquidation_at(900 * _NS))
    assert strategy.stale_rows == 2
    assert strategy._cascade.rate_long == 0.0  # neither was placed at the clock
    assert strategy._cascade.clock_ns == 1_000 * _NS


def test_a_stale_row_still_writes_its_record_at_the_clock(tmp_path: Path) -> None:
    # The replay feeds the row (it delivers in `ts_init` order), so the live record must exist
    # for parity to pair it as a late arrival rather than fail it as `replay_only`.
    log = tmp_path / "bot.jsonl"
    strategy = LiquidationCascadeStrategy(_config(signal_log_path=str(log)))
    strategy.instrument = _instrument()
    strategy._signal_log = _SignalLog(str(log))  # what on_start opens
    strategy._cascade.advance(1_000 * _NS)
    strategy.on_data(_liquidation_at(900 * _NS))
    strategy._signal_log.close()
    (record,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert (record["kind"], record["ts_ns"]) == ("liquidation", 1_000 * _NS)
    assert record["venue_event_id"] == str(900 * _NS)
    assert (record["decision"], record["reason"]) == ("none", "stale_row")


def test_a_tick_whose_callback_runs_after_a_later_row_is_recorded_on_its_second(
    tmp_path: Path,
) -> None:
    # Live only: a row received 0.4 s after the 1 000 s tick is fed before the tick's callback
    # runs. The detector's clock stays at the row; the tick still pairs with the replay's by second.
    log = tmp_path / "bot.jsonl"
    strategy = LiquidationCascadeStrategy(_config(signal_log_path=str(log)))
    clock = component.TestClock()  # by its module: pytest would collect the bare name
    bus = MessageBus(TraderId("TESTER-001"), clock)
    cache = Cache()
    strategy.register(TraderId("TESTER-001"), Portfolio(bus, cache, clock), bus, cache, clock)
    strategy.instrument = _instrument()  # what on_start sets
    strategy._signal_log = _SignalLog(str(log))  # what on_start opens
    strategy._cascade.advance(999 * _NS)
    strategy.on_data(_liquidation_at(1_000 * _NS + 400_000_000))
    strategy.on_timer(TimeEvent(_TIMER, UUID4(), 1_000 * _NS, 1_000 * _NS))
    strategy._signal_log.close()
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["kind"], r["ts_ns"]) for r in records] == [
        ("liquidation", 1_000 * _NS + 400_000_000),
        ("tick", 1_000 * _NS),
    ]
    assert records[1]["rate_long"] == records[0]["rate_long"] > 0  # the tick holds the row
    assert strategy._cascade.clock_ns == 1_000 * _NS + 400_000_000


def test_a_late_row_inside_the_window_is_not_stale() -> None:
    strategy = _strategy_on_the_instrument()
    strategy._cascade.advance(1_000 * _NS)
    assert not strategy._stale(_liquidation_at(970 * _NS + 1))
    assert strategy.stale_rows == 0


# --- a refused entry gives its count back ------------------------------------------------------


def _with_a_working_entry() -> LiquidationCascadeStrategy:
    strategy = _strategy_on_the_instrument()
    strategy._episode_key = 601 * _NS
    strategy._entries = 1
    strategy._entry_order_id = ClientOrderId("O-1")
    strategy._entry_episode = 601 * _NS
    strategy._entry_distance = Decimal(50)
    return strategy


def _denied(order_id: str) -> OrderDenied:
    return OrderDenied(
        TraderId("TESTER-001"),
        StrategyId("LiquidationCascadeStrategy-0"),
        _IID,
        ClientOrderId(order_id),
        "MAX_NOTIONAL_PER_ORDER",
        UUID4(),
        0,
    )


def _rejected(order_id: str) -> OrderRejected:
    return OrderRejected(
        TraderId("TESTER-001"),
        StrategyId("LiquidationCascadeStrategy-0"),
        _IID,
        ClientOrderId(order_id),
        AccountId("BYBIT-001"),
        "INSUFFICIENT_MARGIN",
        UUID4(),
        0,
        0,
    )


def test_a_denied_entry_does_not_use_up_the_episode() -> None:
    strategy = _with_a_working_entry()
    strategy.on_order_denied(_denied("O-1"))
    assert strategy._entries == 0
    assert strategy._entry_distance is None
    assert strategy._entry_order_id is None


def test_a_rejected_entry_does_not_use_up_the_episode() -> None:
    strategy = _with_a_working_entry()
    strategy.on_order_rejected(_rejected("O-1"))
    assert strategy._entries == 0
    assert strategy._entry_distance is None


def test_a_refused_exit_order_leaves_the_entry_count() -> None:
    strategy = _with_a_working_entry()
    strategy.on_order_denied(_denied("O-2"))  # not the entry
    assert strategy._entries == 1
    assert strategy._entry_distance == Decimal(50)


def test_a_refusal_after_the_episode_changed_does_not_touch_the_new_one() -> None:
    strategy = _with_a_working_entry()
    strategy._episode_key, strategy._entries = 700 * _NS, 0  # a new episode, nothing entered
    strategy.on_order_rejected(_rejected("O-1"))
    assert strategy._entries == 0


# --- the daily realised PnL never moves back a day ---------------------------------------------


def _closed(ts_closed: int, pnl: str) -> PositionClosed:
    return PositionClosed(
        TraderId("TESTER-001"),
        StrategyId("LiquidationCascadeStrategy-0"),
        _IID,
        PositionId("P-1"),
        AccountId("BYBIT-001"),
        ClientOrderId("O-1"),
        ClientOrderId("O-2"),
        OrderSide.SELL,
        PositionSide.FLAT,
        0.0,
        Quantity.from_str("0.000"),
        Quantity.from_str("0.010"),
        Quantity.from_str("0.010"),
        Price.from_str("10100.00"),
        USDT,
        10_000.0,
        10_100.0,
        -0.01,
        Money(Decimal(pnl), USDT),
        UUID4(),
        ts_closed - _NS,
        ts_closed,
        _NS,
        ts_closed,
    )


def test_a_close_stamped_on_an_earlier_day_keeps_todays_day_and_sum() -> None:
    strategy = _strategy_on_the_instrument()
    today = 20_000 * NS_PER_DAY
    strategy.on_position_closed(_closed(today + 3_600 * _NS, "-40.00"))
    assert (strategy._day, strategy._day_realized) == (20_000, Decimal("-40.00"))
    strategy.on_position_closed(_closed(today - _NS, "-100.00"))  # yesterday's last second
    assert (strategy._day, strategy._day_realized) == (20_000, Decimal("-40.00"))
    strategy.on_position_closed(_closed(today + 7_200 * _NS, "15.00"))
    assert (strategy._day, strategy._day_realized) == (20_000, Decimal("-25.00"))
    strategy.on_position_closed(_closed(today + NS_PER_DAY, "-5.00"))  # the next day starts at 0
    assert (strategy._day, strategy._day_realized) == (20_001, Decimal("-5.00"))


# --- the planted backtest ----------------------------------------------------------------------


def _mid_units(second: int) -> int:
    """Return the mid in price units: 10 000.00, falling 1.00 a second through the burst."""
    if second < _BURST.start:
        return 1_000_000
    return 1_000_000 - 100 * (min(second, _BURST.stop - 1) - (_BURST.start - 1))


def _snapshot(second: int) -> Any:
    mid = _mid_units(second)
    return make_snapshot(
        _IID,
        bid_prices=[str(Decimal(mid - 1).scaleb(-2))],
        bid_sizes=["5"],
        ask_prices=[str(Decimal(mid + 1).scaleb(-2))],
        ask_sizes=["5"],
        ts_event=_T0 + second * _NS + 500_000_000,
        price_precision=2,
        size_precision=3,
    )


def _liquidation(second: int) -> Liquidation:
    # 0.001 BTC at the bankruptcy price 10 000.00: 1 x 1 000 000 units of 10^-5 USDT.
    ts = _T0 + second * _NS
    return Liquidation(
        _IID, LiquidatedSide.LONG, 1, 1_000_000, 2, 3, str(second), ts, ts + _RECEIVED_NS
    )


def _planted_rows() -> list[Liquidation]:
    background = [_liquidation(s) for s in range(0, 600, 60)]
    return background + [_liquidation(s) for s in _BURST]


def _planted_catalog(path: Path) -> None:
    catalog = ParquetDataCatalog(str(path))
    catalog.write_data([_instrument()])
    catalog.write_data([_snapshot(s) for s in range(_SECONDS)])
    catalog.write_data(_planted_rows())


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_planted_cascade_is_shorted_and_closed_when_spent(tmp_path: Path) -> None:
    _planted_catalog(tmp_path / "catalog")
    log = tmp_path / "signal.jsonl"
    result = backtest_liquidation_cascade.run(
        symbol=str(_IID),
        start=_T0,
        end=_T0 + _SECONDS * _NS,
        catalog_path=str(tmp_path / "catalog"),
        window_s=30,
        baseline_s=600,
        intensity_threshold=3.0,
        decay_ratio=0.5,
        stop_pct=0.05,
        cooldown_s=3_600,
        max_hold_s=3_600,
        trade_size="0.01",
        signal_log_path=str(log),
    )
    fills = result.fills.sort_values("ts_last")
    assert list(fills["side"]) == ["SELL", "BUY"]
    sell, buy = fills.iloc[0], fills.iloc[1]
    # The bias-corrected baseline is ~13 930 units/s at 660.2 s (the background's mean, one 1e6-unit
    # entry in the window half of each minute), so the first burst liquidation reads intensity
    # ~2.39 < 3; the second (661.2 s, two entries: 66 667 units/s against ~13 980) reads ~4.77 and
    # activates the detector. The 300 ms order latency fills it at 661.5 s's bid: the mid has
    # fallen 2.00 to 9 998.00, so 9 997.99.
    assert pd.Timestamp(sell["ts_last"]).value == _T0 + 661 * _NS + 500_000_000
    assert _T0 + _BURST.start * _NS <= pd.Timestamp(sell["ts_last"]).value < _T0 + 750 * _NS
    assert Decimal(str(sell["avg_px"])) == Decimal("9997.99")
    # Spent at the tick of 766 s: the window holds the burst entries placed after 736 s, 780 - 766
    # = 14 of them (466.7/s), under half the 1 000/s peak; the BUY fills at 766.5 s's ask.
    assert pd.Timestamp(buy["ts_last"]).value == _T0 + 766 * _NS + 500_000_000
    assert Decimal(str(buy["avg_px"])) == Decimal("9910.01")
    acted = [
        (r["kind"], r["ts_ns"], r["decision"], r["reason"])
        for r in _records(log)[1:]  # after the start record
        if r["decision"] not in ("none", "not_ready")
    ]
    assert acted == [
        ("liquidation", _T0 + 661 * _NS + _RECEIVED_NS, "enter_short", "follow"),
        ("tick", _T0 + 766 * _NS, "exit", "spent"),
    ]
    # The notebook's sample and the strategy share one detector: `replay_cascade` over the same
    # rows and parameters finds the episode the strategy's log shows, starting on the same update.
    first_active = next(r["ts_ns"] for r in _records(log)[1:] if r["active"])
    (episode,) = replay_cascade(
        _planted_rows(), 30, 600, 3.0, 0.5, _T0 + _SECONDS * _NS, start_ns=_T0, precisions=(2, 3)
    )
    assert episode.direction == -1
    assert episode.start_ns == first_active


def test_the_signal_log_starts_with_the_strategy_and_every_config_field(tmp_path: Path) -> None:
    _planted_catalog(tmp_path / "catalog")
    log = tmp_path / "signal.jsonl"
    backtest_liquidation_cascade.run(
        symbol=str(_IID),
        start=_T0,
        end=_T0 + 120 * _NS,
        catalog_path=str(tmp_path / "catalog"),
        baseline_s=600,
        signal_log_path=str(log),
    )
    records = _records(log)
    start = records[0]
    assert start["kind"] == "start"
    assert start["strategy"] == "liquidation_cascade"
    assert start["bot_id"] == "0"  # the runner's order_id_tag
    assert start["ts_ns"] % 1_000 == 0
    assert {k: start[k] for k in ("instrument_id", "baseline_s", "mode")} == {
        "instrument_id": str(_IID),
        "baseline_s": 600,
        "mode": "follow",
    }
    ticks = [r for r in records if r["kind"] == "tick"]
    assert all(r["ts_ns"] % _NS == 0 for r in ticks)  # whole UTC seconds
    liquidations = [r for r in records if r["kind"] == "liquidation"]
    assert [r["venue_event_id"] for r in liquidations] == ["0", "60"]
    assert all("venue_event_id" not in r for r in ticks)
    assert {r["decision"] for r in records[1:]} == {"not_ready"}  # 120 s < baseline_s
