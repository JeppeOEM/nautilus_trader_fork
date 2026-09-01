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
Tests for live_paper.bot_status -- Story 4.4 (AC1/AC3/AC4, architecture AD-10).

build_status is exercised against a real DummyStrategy run through a real
BacktestEngine (identical harness to test_strategy.py) rather than a mocked
Strategy/Portfolio/Cache -- troll/CLAUDE.md TEST-03 bars mocking Nautilus internals,
and Portfolio/Cache have no lightweight stand-in that would still exercise this
function's real branching logic (position side, win-rate) correctly.
"""

from decimal import Decimal

from live_paper.bot_status import _parse_control_message
from live_paper.bot_status import build_status
from live_paper.strategy import DummyStrategy
from live_paper.strategy import DummyStrategyConfig
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


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_PP = _INSTRUMENT.price_precision
_SP = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency

_TS_START = 1_000_000_000
_STEP_NS = 1_000_000_000  # 1 second, matching book_snapshot_interval_secs=1.0


def _delta(
    action: BookAction, side: OrderSide, price: float, size: float, ts: int, seq: int
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


def _config(**overrides: object) -> DummyStrategyConfig:
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


def _run_strategy(**config_overrides: object) -> tuple[BacktestEngine, DummyStrategy]:
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))
    strategy = DummyStrategy(_config(**config_overrides))
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy


def test_build_status_after_a_real_run_has_expected_shape() -> None:
    engine, strategy = _run_strategy(
        trend_buy_threshold=0.49, trend_sell_threshold=0.1, ofi_confirm_threshold=-999_999.0
    )

    status = build_status(strategy, bot_id="bot-01", mode="paper", started_at=1_000.0, now=2_000.0)

    assert status["bot_id"] == "bot-01"
    assert status["strategy"] == "DummyStrategy"
    assert status["symbol"] == str(_IID)
    assert status["mode"] == "paper"
    assert status["running"] == strategy.is_running
    assert status["position_side"] in ("flat", "long", "short")
    assert isinstance(status["net_exposure"], float)
    assert isinstance(status["realized_pnl"], float)
    assert isinstance(status["unrealized_pnl"], float)
    assert status["started_at"] == 1_000.0
    assert status["updated_at"] == 2_000.0
    closed_positions = strategy.cache.positions_closed(strategy_id=strategy.id)
    assert status["closed_trades"] == len(closed_positions)

    engine.reset()
    engine.dispose()


def test_build_status_position_side_matches_portfolio() -> None:
    engine, strategy = _run_strategy(
        trend_buy_threshold=0.49, trend_sell_threshold=0.1, ofi_confirm_threshold=-999_999.0
    )

    status = build_status(strategy, bot_id="bot-01", mode="paper", started_at=0.0, now=1.0)

    if strategy.portfolio.is_net_long(_IID):
        assert status["position_side"] == "long"
    elif strategy.portfolio.is_net_short(_IID):
        assert status["position_side"] == "short"
    else:
        assert status["position_side"] == "flat"

    engine.reset()
    engine.dispose()


def test_build_status_win_rate_none_before_any_closed_position() -> None:
    # Impossible thresholds -> no entries ever fire -> no closed positions exist.
    engine, strategy = _run_strategy(trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001)

    status = build_status(strategy, bot_id="bot-01", mode="paper", started_at=0.0, now=1.0)
    assert status["win_rate"] is None
    assert status["closed_trades"] == 0

    engine.reset()
    engine.dispose()


def test_build_status_mode_is_passed_through_verbatim() -> None:
    engine, strategy = _run_strategy(trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001)
    status = build_status(strategy, bot_id="bot-01", mode="live", started_at=0.0, now=1.0)
    assert status["mode"] == "live"

    engine.reset()
    engine.dispose()


def test_parse_control_message_matching_bot_id_and_start_action() -> None:
    assert _parse_control_message({"bot_id": "bot-01", "action": "start"}, "bot-01") == "start"


def test_parse_control_message_matching_bot_id_and_stop_action() -> None:
    assert _parse_control_message({"bot_id": "bot-01", "action": "stop"}, "bot-01") == "stop"


def test_parse_control_message_different_bot_id_returns_none() -> None:
    assert _parse_control_message({"bot_id": "bot-02", "action": "start"}, "bot-01") is None


def test_parse_control_message_invalid_action_returns_none() -> None:
    assert _parse_control_message({"bot_id": "bot-01", "action": "pause"}, "bot-01") is None


def test_parse_control_message_missing_bot_id_returns_none() -> None:
    assert _parse_control_message({"action": "start"}, "bot-01") is None


def test_parse_control_message_ignores_a_mode_field_if_present() -> None:
    # AC4: the control channel must never carry a mode/paper-live parameter -- a
    # message that happens to include one anyway must not change the parsed action.
    payload = {"bot_id": "bot-01", "action": "start", "mode": "real_money"}
    assert _parse_control_message(payload, "bot-01") == "start"
