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
Tests for bots.application.supervise and bots.domain.bot -- Story 4.4 (AC1/AC3/AC4, architecture
AD-10), moved in Story 25.3.

build_status is exercised against a real DummyStrategy run through a real BacktestEngine
(identical harness to test_strategy.py), read through the real `StrategyCacheReader`, rather
than a mocked Strategy/Portfolio/Cache -- platform/CLAUDE.md TEST-03 bars mocking Nautilus
internals, and Portfolio/Cache have no lightweight stand-in that would still exercise the real
branching logic (position side, win-rate) correctly.
"""

import asyncio
import contextlib
import json
import math
import threading
import time
from collections.abc import AsyncIterator
from collections.abc import Callable
from decimal import Decimal

import pytest
from observability import error_ledger

from bots.application.ports import BusConnection
from bots.application.ports import FillsStore
from bots.application.ports import PositionSnapshot
from bots.application.supervise import Supervisor
from bots.application.supervise import parse_control_message
from bots.domain.bot import DATA_STALE_NS
from bots.domain.bot import close_orphaned_incident
from bots.domain.bot import incident_transition
from bots.domain.bot import is_feed_stale
from bots.domain.bot import record_process_start
from bots.domain.bot import trim_incidents
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.strategies.dummy import DummyStrategy
from bots.strategies.dummy import DummyStrategyConfig
from bots.tests.support import FakeBus
from bots.tests.support import ThreadRecordingStore
from bots.tests.support import connect_to
from bots.tests.support import record_fills
from bots.tests.support import status_of
from bots.tests.support import write_fill
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


def _run_strategy(
    store: SqliteFillsStore, bot_id: str = "bot-01", **config_overrides: object
) -> tuple[BacktestEngine, DummyStrategy]:
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))
    strategy = DummyStrategy(_config(**config_overrides))
    engine.add_strategy(strategy)
    # closed_trades/win_rate come from fills.db (Story 4.6), not cache.positions_closed() --
    # record fills before run() the same way test_trade_history.py's harness does, or the
    # store stays empty and every status read below would see closed_trades=0.
    record_fills(strategy, store, bot_id)
    engine.run()
    return engine, strategy


def test_strategy_last_data_ns_tracks_the_most_recent_quote_tick(store: SqliteFillsStore) -> None:
    _engine_unused, strategy = _run_strategy(store)
    assert strategy.last_data_ns == _TS_START + 15 * _STEP_NS


def test_build_status_after_a_real_run_has_expected_shape(store: SqliteFillsStore) -> None:
    engine, strategy = _run_strategy(
        store,
        trend_buy_threshold=0.49,
        trend_sell_threshold=0.1,
        ofi_confirm_threshold=-999_999.0,
    )

    status = status_of(strategy, store, mode="paper", started_at=1_000.0, now=2_000.0)

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
    closed_trades, _wins = store.win_rate_stats("bot-01")
    assert status["closed_trades"] == closed_trades

    engine.reset()
    engine.dispose()


def test_build_status_position_side_matches_portfolio(store: SqliteFillsStore) -> None:
    engine, strategy = _run_strategy(
        store,
        trend_buy_threshold=0.49,
        trend_sell_threshold=0.1,
        ofi_confirm_threshold=-999_999.0,
    )

    status = status_of(strategy, store, mode="paper", started_at=0.0, now=1.0)

    if strategy.portfolio.is_net_long(_IID):
        assert status["position_side"] == "long"
    elif strategy.portfolio.is_net_short(_IID):
        assert status["position_side"] == "short"
    else:
        assert status["position_side"] == "flat"

    engine.reset()
    engine.dispose()


def test_build_status_win_rate_none_before_any_closed_position(store: SqliteFillsStore) -> None:
    # Impossible thresholds -> no entries ever fire -> no closed positions exist.
    engine, strategy = _run_strategy(
        store, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )

    status = status_of(strategy, store, mode="paper", started_at=0.0, now=1.0)
    assert status["win_rate"] is None
    assert status["closed_trades"] == 0

    engine.reset()
    engine.dispose()


def test_build_status_mode_is_passed_through_verbatim(store: SqliteFillsStore) -> None:
    engine, strategy = _run_strategy(
        store, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )
    status = status_of(strategy, store, mode="live", started_at=0.0, now=1.0)
    assert status["mode"] == "live"

    engine.reset()
    engine.dispose()


def test_build_status_closed_trades_survives_a_netting_reopen(store: SqliteFillsStore) -> None:
    """
    Regression: cache.positions_closed() silently drops a NETTING position's closed
    history the instant it reopens (see bots.domain.fill_ledger's module docstring for the
    full diagnosis), so a bot with 2 completed round trips could show closed_trades=0
    or 1 -- never 2 -- if build_status() read it directly. Seed fills.db with 2
    synthetic closing fills (mirroring what 2 real round trips would have written) and
    confirm build_status() counts both, using a strategy that itself traded zero times
    (impossible thresholds) so cache.positions_closed() is provably empty here -- any
    non-zero closed_trades/win_rate the status shows can only have come from
    fills.db, not the Cache.
    """
    engine, strategy = _run_strategy(
        store, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )
    assert strategy.cache.positions_closed(strategy_id=strategy.id) == []

    write_fill(store, "bot-01", 1, "BUY", 100.0, 1.0, None)
    write_fill(store, "bot-01", 2, "SELL", 105.0, 1.0, 5.0, position_realized_pnl=5.0)  # win
    write_fill(store, "bot-01", 3, "BUY", 105.0, 1.0, None)
    write_fill(store, "bot-01", 4, "SELL", 103.0, 1.0, -2.0, position_realized_pnl=-2.0)  # loss

    status = status_of(strategy, store, mode="paper", started_at=0.0, now=1.0)
    assert status["closed_trades"] == 2
    assert status["win_rate"] == 0.5

    engine.reset()
    engine.dispose()


def test_build_status_appends_the_position_and_exit_fields(store: SqliteFillsStore) -> None:
    # Story 29.6: a bracket bot's protected long, read while it still runs (streaming leaves it
    # running, so on_stop has not cancelled the exits yet).
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))
    strategy = DummyStrategy(
        _config(
            trend_buy_threshold=0.49,
            trend_sell_threshold=0.1,
            ofi_confirm_threshold=-999_999.0,
            take_profit_bps=100,
            stop_loss_bps=100,
        )
    )
    engine.add_strategy(strategy)
    record_fills(strategy, store)
    engine.run(streaming=True)
    try:
        status = status_of(strategy, store, now=2_000.0)
        assert list(status)[13:] == [
            "stop_loss",
            "take_profit",
            "entry_price",
            "mark_price",
            "position_qty",
            "stop_loss_orders",
            "take_profit_orders",
            "open_orders",
            "last_fill_at",
        ]
        assert status["position_side"] == "long"
        assert (status["stop_loss"], status["take_profit"]) == ("99.49", "101.51")
        assert (status["entry_price"], status["mark_price"]) == ("101.00", "100.500")
        assert status["position_qty"] == "0.001000"
        assert (status["stop_loss_orders"], status["take_profit_orders"]) == (1, 1)
        assert status["open_orders"] == 2
        assert status["last_fill_at"] == store.last_fill_ns("bot-01")
        assert status["last_fill_at"] is not None
    finally:
        engine.end()
        engine.dispose()


def test_build_status_of_a_bot_without_fills_has_no_last_fill(store: SqliteFillsStore) -> None:
    engine, strategy = _run_strategy(
        store, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )
    status = status_of(strategy, store)
    assert status["last_fill_at"] is None
    assert (status["stop_loss"], status["entry_price"], status["position_qty"]) == (
        None,
        None,
        None,
    )
    assert (status["stop_loss_orders"], status["take_profit_orders"], status["open_orders"]) == (
        0,
        0,
        0,
    )

    engine.reset()
    engine.dispose()


def test_parse_control_message_matching_bot_id_and_start_action() -> None:
    assert parse_control_message({"bot_id": "bot-01", "action": "start"}, "bot-01") == "start"


def test_parse_control_message_matching_bot_id_and_stop_action() -> None:
    assert parse_control_message({"bot_id": "bot-01", "action": "stop"}, "bot-01") == "stop"


def test_parse_control_message_different_bot_id_returns_none() -> None:
    assert parse_control_message({"bot_id": "bot-02", "action": "start"}, "bot-01") is None


def test_parse_control_message_invalid_action_returns_none() -> None:
    assert parse_control_message({"bot_id": "bot-01", "action": "pause"}, "bot-01") is None


def test_parse_control_message_missing_bot_id_returns_none() -> None:
    assert parse_control_message({"action": "start"}, "bot-01") is None


def test_incident_transition_opens_a_new_incident_on_stale_start() -> None:
    incidents, changed = incident_transition(now=100.0, is_stale=True, incidents=[])
    assert changed is True
    assert incidents == [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]


def test_incident_transition_does_not_reopen_while_already_stale() -> None:
    open_incident = [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]
    incidents, changed = incident_transition(now=110.0, is_stale=True, incidents=open_incident)
    assert changed is False
    assert incidents == open_incident


def test_incident_transition_closes_open_incident_on_recovery() -> None:
    open_incident = [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]
    incidents, changed = incident_transition(now=135.0, is_stale=False, incidents=open_incident)
    assert changed is True
    assert incidents == [{"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}]


def test_incident_transition_is_a_no_op_while_healthy() -> None:
    closed = [{"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}]
    incidents, changed = incident_transition(now=200.0, is_stale=False, incidents=closed)
    assert changed is False
    assert incidents == closed


def test_is_feed_stale_fires_from_process_start_when_no_data_ever_arrived() -> None:
    # Regression: a WS/API connection that never succeeds at all used to never mark
    # stale, since last_data_ns==0 forever looked identical to "healthy, no tick yet".
    started_at = 100.0
    now_ns = int(started_at * 1e9) + DATA_STALE_NS + 1
    assert is_feed_stale(last_data_ns=0, started_at=started_at, now_ns=now_ns) is True


def test_is_feed_stale_is_not_stale_right_after_process_start_with_no_data_yet() -> None:
    started_at = 100.0
    now_ns = int(started_at * 1e9) + 1_000_000_000  # 1s in, well under the threshold
    assert is_feed_stale(last_data_ns=0, started_at=started_at, now_ns=now_ns) is False


def test_is_feed_stale_uses_last_data_ns_once_data_has_arrived() -> None:
    last_data_ns = 1_000_000_000_000
    now_ns = last_data_ns + DATA_STALE_NS + 1
    # started_at far in the past -- must not be what triggers staleness here.
    assert is_feed_stale(last_data_ns=last_data_ns, started_at=0.0, now_ns=now_ns) is True
    assert (
        is_feed_stale(
            last_data_ns=last_data_ns, started_at=0.0, now_ns=last_data_ns + 1_000_000_000
        )
        is False
    )


def test_close_orphaned_incident_closes_a_dangling_open_span() -> None:
    orphaned = [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]
    closed = close_orphaned_incident(orphaned, now=999.0)
    assert closed[0]["ended_at"] == 999.0
    assert closed[0]["note"] == "closed by restart"


def test_close_orphaned_incident_is_a_no_op_when_nothing_is_open() -> None:
    incidents = [{"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}]
    assert close_orphaned_incident(incidents, now=999.0) == incidents


def test_record_process_start_appends_zero_duration_marker() -> None:
    incidents = record_process_start([], now=50.0)
    assert incidents == [{"type": "process_start", "started_at": 50.0, "ended_at": 50.0}]


def test_trim_incidents_keeps_only_the_most_recent() -> None:
    incidents = [{"i": i} for i in range(60)]
    trimmed = trim_incidents(incidents)
    assert len(trimmed) == 50
    assert trimmed[-1] == {"i": 59}


def test_parse_control_message_ignores_a_mode_field_if_present() -> None:
    # AC4: the control channel must never carry a mode/paper-live parameter -- a
    # message that happens to include one anyway must not change the parsed action.
    payload = {"bot_id": "bot-01", "action": "start", "mode": "real_money"}
    assert parse_control_message(payload, "bot-01") == "start"


class _Runtime:
    """A `BotRuntime` stand-in (the application's own port -- no Nautilus internal is faked)."""

    strategy_name = "DummyStrategy"
    symbol = "BTC-USD-PERP.DYDX"

    def __init__(self, running: bool = True, last_data_ns: int = 0) -> None:
        self.is_running = running
        self.last_data_ns = last_data_ns
        self.fail_positions = 0
        self.calls: list[str] = []
        self.order_event: Callable[[], None] | None = None

    def positions(self) -> PositionSnapshot:
        if self.fail_positions:
            self.fail_positions -= 1
            raise TypeError("float() argument must be a string or a real number, not 'NoneType'")
        return PositionSnapshot("flat", 0.0, 0.0, 0.0)

    def start(self) -> None:
        self.calls.append("start")
        self.is_running = True

    def stop(self) -> None:
        self.calls.append("stop")
        self.is_running = False

    def on_fill(self, handler: object) -> None:
        raise AssertionError("the supervisor never records fills")

    def on_order_event(self, handler: Callable[[], None]) -> None:
        self.order_event = handler


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def time_ns(self) -> int:
        return int(self.now * 1e9)


def _supervisor(runtime: _Runtime, store: FillsStore, bus: FakeBus, clock: _Clock) -> Supervisor:
    return Supervisor(
        "bot-01",
        "paper",
        runtime,
        store,
        connect_to(bus),
        owner="test-owner",
        clock=clock.time,
        clock_ns=clock.time_ns,
        heartbeat_seconds=0.0,
        reconnect_seconds=0.0,
    )


@pytest.fixture(autouse=True)
def _fresh_ledger() -> None:
    error_ledger.reset()


def test_a_failing_status_build_skips_one_tick_and_is_ledgered(store: SqliteFillsStore) -> None:
    """
    Regression for a live startup race (2026-09-02): building the status can briefly raise
    before the first quote lands -- that tick is skipped (and now ledgered, DATA-07), later
    ticks publish, and nothing tears down the connection the control loop shares.
    """
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    runtime.fail_positions = 1
    supervisor = _supervisor(runtime, store, bus, clock)

    asyncio.run(supervisor.heartbeat_tick(bus))
    asyncio.run(supervisor.heartbeat_tick(bus))

    assert error_ledger.counts() == {"bots.status_build": 1}
    assert [channel for channel, _ in bus.published] == ["bots:status"]
    assert json.loads(bus.published[0][1])["bot_id"] == "bot-01"


def test_a_deliberately_stopped_bot_is_not_flagged_data_stale(store: SqliteFillsStore) -> None:
    """A stopped bot legitimately receives no data: never a `data_stale` incident."""
    runtime, bus, clock = _Runtime(running=False), FakeBus(), _Clock(1_000.0)
    supervisor = _supervisor(runtime, store, bus, clock)
    clock.now = 2_000.0  # far past the 30 s threshold, measured from process start

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert supervisor.bot.incidents == []
    assert "bots:incidents:bot-01" not in bus.keys


def test_a_stale_transition_is_written_once_and_closed_on_recovery(store: SqliteFillsStore) -> None:
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    supervisor = _supervisor(runtime, store, bus, clock)
    clock.now = 140.0
    asyncio.run(supervisor.heartbeat_tick(bus))
    opened = bus.keys["bots:incidents:bot-01"]
    clock.now = 141.0
    asyncio.run(supervisor.heartbeat_tick(bus))
    assert bus.keys["bots:incidents:bot-01"] == opened  # no rewrite while still stale

    runtime.last_data_ns = clock.time_ns()
    clock.now = 142.0
    asyncio.run(supervisor.heartbeat_tick(bus))

    assert json.loads(bus.keys["bots:incidents:bot-01"]) == [
        {"type": "data_stale", "started_at": 140.0, "ended_at": 142.0}
    ]


def test_a_failed_incidents_write_is_ledgered_and_the_status_still_publishes(
    store: SqliteFillsStore,
) -> None:
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    supervisor = _supervisor(runtime, store, bus, clock)
    bus.fail_set = True
    clock.now = 140.0

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert error_ledger.counts() == {"bots.incidents_write": 1}
    assert len(bus.published) == 1


def test_seed_adopts_the_previous_log_once(store: SqliteFillsStore) -> None:
    runtime, clock = _Runtime(), _Clock(100.0)
    bus = FakeBus()
    bus.keys["bots:incidents:bot-01"] = json.dumps(
        [{"type": "data_stale", "started_at": 90.0, "ended_at": None}]
    )
    supervisor = _supervisor(runtime, store, bus, clock)

    asyncio.run(supervisor.seed())

    assert json.loads(bus.keys["bots:incidents:bot-01"]) == [
        {"type": "data_stale", "started_at": 90.0, "ended_at": 100.0, "note": "closed by restart"},
        {"type": "process_start", "started_at": 100.0, "ended_at": 100.0},
    ]


def test_a_failed_seed_write_keeps_the_adopted_log_and_is_ledgered(
    store: SqliteFillsStore,
) -> None:
    # The prior life's log was read: a failed write must not replace it with an empty one (the
    # next transition writes the whole adopted log), and this start is still marked.
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    bus.keys["bots:incidents:bot-01"] = json.dumps(
        [{"type": "data_stale", "started_at": 90.0, "ended_at": None}]
    )
    bus.fail_set = True
    supervisor = _supervisor(runtime, store, bus, clock)

    asyncio.run(supervisor.seed())

    assert supervisor.bot.incidents == [
        {"type": "data_stale", "started_at": 90.0, "ended_at": 100.0, "note": "closed by restart"},
        {"type": "process_start", "started_at": 100.0, "ended_at": 100.0},
    ]
    assert error_ledger.counts() == {"bots.incidents_write": 1}


def test_an_unreadable_prior_log_starts_an_empty_one_that_still_marks_the_start(
    store: SqliteFillsStore,
) -> None:
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    bus.keys["bots:incidents:bot-01"] = "{not json"
    supervisor = _supervisor(runtime, store, bus, clock)

    asyncio.run(supervisor.seed())

    assert supervisor.bot.incidents == [
        {"type": "process_start", "started_at": 100.0, "ended_at": 100.0}
    ]
    assert json.loads(bus.keys["bots:incidents:bot-01"]) == supervisor.bot.incidents
    assert error_ledger.counts() == {"bots.incidents_write": 1}
    assert "'{not json'" in error_ledger.last_details()["bots.incidents_write"]


@pytest.mark.parametrize("prior", ['{"not": "a list"}', "[1, 2]", '[{"type": "data_stale"}]'])
def test_a_malformed_prior_log_still_marks_the_start(store: SqliteFillsStore, prior: str) -> None:
    # Valid JSON that is not an incident list: this life's restart must still be logged.
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    bus.keys["bots:incidents:bot-01"] = prior
    supervisor = _supervisor(runtime, store, bus, clock)

    asyncio.run(supervisor.seed())

    assert supervisor.bot.incidents == [
        {"type": "process_start", "started_at": 100.0, "ended_at": 100.0}
    ]
    assert json.loads(bus.keys["bots:incidents:bot-01"]) == supervisor.bot.incidents
    assert error_ledger.counts() == {"bots.incidents_write": 1}


def test_seed_retries_an_unreachable_prior_log_instead_of_overwriting_it(
    store: SqliteFillsStore,
) -> None:
    # DW-228: starting from an empty log while Redis was merely down would overwrite the prior
    # life's log on the first write. The start is still this process's, not the retry's, time.
    runtime, clock = _Runtime(), _Clock(100.0)
    bus = FakeBus()
    bus.keys["bots:incidents:bot-01"] = json.dumps(
        [{"type": "data_stale", "started_at": 90.0, "ended_at": None}]
    )
    bus.fail_get = 2
    supervisor = _supervisor(runtime, store, bus, clock)
    clock.now = 150.0

    asyncio.run(supervisor.seed())

    assert json.loads(bus.keys["bots:incidents:bot-01"]) == [
        {"type": "data_stale", "started_at": 90.0, "ended_at": 100.0, "note": "closed by restart"},
        {"type": "process_start", "started_at": 100.0, "ended_at": 100.0},
    ]
    assert error_ledger.counts() == {"bots.redis": 2}


def test_heartbeat_reads_fill_stats_off_the_event_loop_thread(store: SqliteFillsStore) -> None:
    # DW-222: a sqlite read on the loop would freeze every bot on the node for its duration.
    recording = ThreadRecordingStore(store)
    write_fill(store, "bot-01", 1, "SELL", 1.0, 1.0, 2.0, position_realized_pnl=2.0)
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    supervisor = _supervisor(runtime, recording, bus, clock)
    loop_threads: list[int] = []

    async def _tick() -> None:
        loop_threads.append(threading.get_ident())
        await supervisor.heartbeat_tick(bus)

    asyncio.run(_tick())

    assert recording.read_threads
    assert loop_threads[0] not in recording.read_threads
    status = json.loads(bus.published[0][1])
    assert (status["closed_trades"], status["win_rate"], status["last_fill_at"]) == (1, 1.0, 1)


def test_a_refused_control_action_is_ledgered_not_raised(store: SqliteFillsStore) -> None:
    # A strategy that refuses the transition is a bot fault: it must not tear down the connection.
    class _Refusing(_Runtime):
        def start(self) -> None:
            raise RuntimeError("invalid state trigger")

    runtime = _Refusing(running=False)
    supervisor = _supervisor(runtime, store, FakeBus(), _Clock(100.0))

    supervisor.handle_control('{"bot_id": "bot-01", "action": "start"}')

    assert error_ledger.counts() == {"bots.control_action": 1}


def test_an_ended_control_stream_fails_into_the_reconnect(store: SqliteFillsStore) -> None:
    # An ended subscription would leave the heartbeat publishing while every command went unheard.
    class _EndingBus(FakeBus):
        async def control_messages(self) -> AsyncIterator[str]:
            for data in self.control:
                yield data

    supervisor = _supervisor(_Runtime(), store, _EndingBus(), _Clock(100.0))

    with pytest.raises(ConnectionError, match="subscription ended"):
        asyncio.run(supervisor._control_loop(_EndingBus()))


def test_control_acts_only_on_a_valid_message_for_this_bot(store: SqliteFillsStore) -> None:
    runtime, bus, clock = _Runtime(running=False), FakeBus(), _Clock(100.0)
    supervisor = _supervisor(runtime, store, bus, clock)

    for data in (
        '{"bot_id": "bot-02", "action": "start"}',
        '{"bot_id": "bot-01", "action": "pause"}',
        '{"bot_id": "bot-01", "action": "start", "mode": "real_money"}',
        '{"bot_id": "bot-01", "action": "start"}',  # already running: no second start
        '{"bot_id": "bot-01", "action": "stop"}',
    ):
        supervisor.handle_control(data)

    assert runtime.calls == ["start", "stop"]


@pytest.mark.parametrize("data", ["not json", "[1, 2]", '"start"'])
def test_a_malformed_control_message_is_ledgered_not_acted_on(
    store: SqliteFillsStore, data: str
) -> None:
    runtime = _Runtime(running=False)
    supervisor = _supervisor(runtime, store, FakeBus(), _Clock(100.0))

    supervisor.handle_control(data)

    assert runtime.calls == []
    assert error_ledger.counts() == {"bots.control_message": 1}


def test_run_keeps_the_incident_log_across_a_reconnect(store: SqliteFillsStore) -> None:
    """A Redis drop reconnects (ledgered) without re-seeding: no second `process_start`."""
    runtime, clock = _Runtime(), _Clock(100.0)
    bus = FakeBus(control=['{"bot_id": "bot-01", "action": "stop"}'])
    opened = {"n": 0}
    real_connect = connect_to(bus)

    def flaky_connect():
        opened["n"] += 1
        # The seed's read and write are the first two connections; the run loop's first fails.
        if opened["n"] == 3:
            raise ConnectionError("redis down")
        return real_connect()

    supervisor = Supervisor(
        "bot-01",
        "paper",
        runtime,
        store,
        flaky_connect,
        owner="test-owner",
        clock=clock.time,
        clock_ns=clock.time_ns,
        heartbeat_seconds=0.0,
        reconnect_seconds=0.0,
    )

    async def _run_briefly() -> None:
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run_briefly())

    starts = [i for i in supervisor.bot.incidents if i["type"] == "process_start"]
    assert len(starts) == 1
    assert error_ledger.counts()["bots.redis"] == 1
    assert "stop" in runtime.calls
    assert bus.published


class _FailingControlBus(FakeBus):
    """Every connection's control stream fails at once, as a dropped subscription would."""

    async def control_messages(self) -> AsyncIterator[str]:
        raise ConnectionError("subscription dropped")
        yield ""  # pragma: no cover -- makes this an async generator


def test_a_failed_loop_cancels_its_sibling_before_the_reconnect(store: SqliteFillsStore) -> None:
    """
    Regression: with `gather`, a failed control stream left its heartbeat loop running, so each
    reconnect added one more publisher of bots:status and one more observer of the one `Bot`.
    """
    active, peak = 0, 0

    class _Counting(Supervisor):
        async def _heartbeat_loop(self, connection: BusConnection) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await super()._heartbeat_loop(connection)
            finally:
                active -= 1

    clock = _Clock(100.0)
    supervisor = _Counting(
        "bot-01",
        "paper",
        _Runtime(),
        store,
        connect_to(_FailingControlBus()),
        owner="test-owner",
        clock=clock.time,
        clock_ns=clock.time_ns,
        heartbeat_seconds=0.001,
        reconnect_seconds=0.001,
    )

    async def _run_briefly() -> None:
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run_briefly())

    assert peak == 1
    assert error_ledger.counts()["bots.redis"] >= 2  # it really did reconnect several times


def test_an_order_event_publishes_the_status_before_the_next_heartbeat(
    store: SqliteFillsStore,
) -> None:
    """
    Story 29.6: a bracket exit can close a position and the strategy re-enter within a second,
    so the status is published on the bot's own order events, not only every heartbeat.
    """
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    supervisor = Supervisor(
        "bot-01",
        "paper",
        runtime,
        store,
        connect_to(bus),
        owner="test-owner",
        clock=clock.time,
        clock_ns=clock.time_ns,
        heartbeat_seconds=3_600.0,
        min_publish_spacing=0.0,
        reconnect_seconds=0.0,
    )

    async def _run_with_one_order_event() -> int:
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.02)
        published_before = len(bus.published)
        assert runtime.order_event is not None
        runtime.order_event()
        await asyncio.sleep(0.02)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return published_before

    published_before = asyncio.run(_run_with_one_order_event())
    assert published_before == 1  # the first heartbeat tick only: the next is an hour away
    assert len(bus.published) == 2


def test_an_order_event_burst_is_coalesced_by_the_publish_spacing(
    store: SqliteFillsStore,
) -> None:
    # A trailing stop emits an OrderUpdated per trail step: a burst of 100 events over ~0.1 s
    # must not become 100 publishes (each a Cache read and two fills.db queries).
    runtime, bus, clock = _Runtime(), FakeBus(), _Clock(100.0)
    supervisor = Supervisor(
        "bot-01",
        "paper",
        runtime,
        store,
        connect_to(bus),
        owner="test-owner",
        clock=clock.time,
        clock_ns=clock.time_ns,
        heartbeat_seconds=3_600.0,
        min_publish_spacing=0.05,
        reconnect_seconds=0.0,
    )

    async def _run_with_an_event_burst() -> float:
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.01)
        assert runtime.order_event is not None
        started = time.monotonic()
        for _ in range(100):
            runtime.order_event()
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.1)
        elapsed = time.monotonic() - started
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return elapsed

    elapsed = asyncio.run(_run_with_an_event_burst())
    # The first tick, then at most one per 50 ms however long the burst took on this box.
    assert len(bus.published) >= 2
    assert len(bus.published) <= 2 + math.ceil(elapsed / 0.05)
