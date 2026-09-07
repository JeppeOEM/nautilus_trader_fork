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

import asyncio
import contextlib
import json
import types
from decimal import Decimal

import live_paper.bot_status as bot_status_module
from live_paper import fills_store
from live_paper import trade_history
from live_paper.bot_status import _DATA_STALE_NS
from live_paper.bot_status import _close_orphaned_incident
from live_paper.bot_status import _heartbeat_loop
from live_paper.bot_status import _incident_transition
from live_paper.bot_status import _is_feed_stale
from live_paper.bot_status import _parse_control_message
from live_paper.bot_status import _record_process_start
from live_paper.bot_status import _trim_incidents
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


def _run_strategy(
    db_path: str, bot_id: str = "bot-01", **config_overrides: object
) -> tuple[BacktestEngine, DummyStrategy]:
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=15, levels_per_side=2))
    strategy = DummyStrategy(_config(**config_overrides))
    engine.add_strategy(strategy)
    # closed_trades/win_rate now come from fills_store (Story 4.6), not
    # cache.positions_closed() -- subscribe before run() the same way
    # test_trade_history.py's harness does, or fills_store stays empty and every
    # status read below would see closed_trades=0 regardless of what actually traded.
    trade_history.subscribe(strategy, bot_id=bot_id, db_path=db_path)
    engine.run()
    return engine, strategy


def test_strategy_last_data_ns_tracks_the_most_recent_quote_tick(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    _engine_unused, strategy = _run_strategy(db_path)
    assert strategy.last_data_ns == _TS_START + 15 * _STEP_NS


def test_build_status_after_a_real_run_has_expected_shape(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy(
        db_path,
        trend_buy_threshold=0.49,
        trend_sell_threshold=0.1,
        ofi_confirm_threshold=-999_999.0,
    )

    status = build_status(
        strategy, bot_id="bot-01", mode="paper", started_at=1_000.0, now=2_000.0, db_path=db_path
    )

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
    closed_trades, _wins = fills_store.win_rate_stats("bot-01", db_path)
    assert status["closed_trades"] == closed_trades

    engine.reset()
    engine.dispose()


def test_build_status_position_side_matches_portfolio(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy(
        db_path,
        trend_buy_threshold=0.49,
        trend_sell_threshold=0.1,
        ofi_confirm_threshold=-999_999.0,
    )

    status = build_status(
        strategy, bot_id="bot-01", mode="paper", started_at=0.0, now=1.0, db_path=db_path
    )

    if strategy.portfolio.is_net_long(_IID):
        assert status["position_side"] == "long"
    elif strategy.portfolio.is_net_short(_IID):
        assert status["position_side"] == "short"
    else:
        assert status["position_side"] == "flat"

    engine.reset()
    engine.dispose()


def test_build_status_win_rate_none_before_any_closed_position(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    # Impossible thresholds -> no entries ever fire -> no closed positions exist.
    engine, strategy = _run_strategy(
        db_path, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )

    status = build_status(
        strategy, bot_id="bot-01", mode="paper", started_at=0.0, now=1.0, db_path=db_path
    )
    assert status["win_rate"] is None
    assert status["closed_trades"] == 0

    engine.reset()
    engine.dispose()


def test_build_status_mode_is_passed_through_verbatim(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy(
        db_path, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )
    status = build_status(
        strategy, bot_id="bot-01", mode="live", started_at=0.0, now=1.0, db_path=db_path
    )
    assert status["mode"] == "live"

    engine.reset()
    engine.dispose()


def test_build_status_closed_trades_survives_a_netting_reopen(tmp_path) -> None:
    """
    Regression: cache.positions_closed() silently drops a NETTING position's closed
    history the instant it reopens (see trade_history.py's module docstring for the
    full diagnosis), so a bot with 2 completed round trips could show closed_trades=0
    or 1 -- never 2 -- if build_status() read it directly. Seed fills_store with 2
    synthetic closing fills (mirroring what 2 real round trips would have written) and
    confirm build_status() counts both, using a strategy that itself traded zero times
    (impossible thresholds) so cache.positions_closed() is provably empty here -- any
    non-zero closed_trades/win_rate the status shows can only have come from
    fills_store, not the Cache.
    """
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy(
        db_path, trend_buy_threshold=0.999_999, trend_sell_threshold=0.000_001
    )
    assert strategy.cache.positions_closed(strategy_id=strategy.id) == []

    fills_store.write_fill("bot-01", 1, "BUY", 100.0, 1.0, None, db_path)
    fills_store.write_fill(
        "bot-01", 2, "SELL", 105.0, 1.0, 5.0, db_path, position_realized_pnl=5.0
    )  # win
    fills_store.write_fill("bot-01", 3, "BUY", 105.0, 1.0, None, db_path)
    fills_store.write_fill(
        "bot-01", 4, "SELL", 103.0, 1.0, -2.0, db_path, position_realized_pnl=-2.0
    )  # loss

    status = build_status(
        strategy, bot_id="bot-01", mode="paper", started_at=0.0, now=1.0, db_path=db_path
    )
    assert status["closed_trades"] == 2
    assert status["win_rate"] == 0.5

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


def test_incident_transition_opens_a_new_incident_on_stale_start() -> None:
    incidents, changed = _incident_transition(now=100.0, is_stale=True, incidents=[])
    assert changed is True
    assert incidents == [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]


def test_incident_transition_does_not_reopen_while_already_stale() -> None:
    open_incident = [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]
    incidents, changed = _incident_transition(now=110.0, is_stale=True, incidents=open_incident)
    assert changed is False
    assert incidents == open_incident


def test_incident_transition_closes_open_incident_on_recovery() -> None:
    open_incident = [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]
    incidents, changed = _incident_transition(now=135.0, is_stale=False, incidents=open_incident)
    assert changed is True
    assert incidents == [{"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}]


def test_incident_transition_is_a_no_op_while_healthy() -> None:
    closed = [{"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}]
    incidents, changed = _incident_transition(now=200.0, is_stale=False, incidents=closed)
    assert changed is False
    assert incidents == closed


def test_is_feed_stale_fires_from_process_start_when_no_data_ever_arrived() -> None:
    # Regression: a WS/API connection that never succeeds at all used to never mark
    # stale, since last_data_ns==0 forever looked identical to "healthy, no tick yet".
    started_at = 100.0
    now_ns = int(started_at * 1e9) + _DATA_STALE_NS + 1
    assert _is_feed_stale(last_data_ns=0, started_at=started_at, now_ns=now_ns) is True


def test_is_feed_stale_is_not_stale_right_after_process_start_with_no_data_yet() -> None:
    started_at = 100.0
    now_ns = int(started_at * 1e9) + 1_000_000_000  # 1s in, well under the threshold
    assert _is_feed_stale(last_data_ns=0, started_at=started_at, now_ns=now_ns) is False


def test_is_feed_stale_uses_last_data_ns_once_data_has_arrived() -> None:
    last_data_ns = 1_000_000_000_000
    now_ns = last_data_ns + _DATA_STALE_NS + 1
    # started_at far in the past -- must not be what triggers staleness here.
    assert _is_feed_stale(last_data_ns=last_data_ns, started_at=0.0, now_ns=now_ns) is True
    assert (
        _is_feed_stale(
            last_data_ns=last_data_ns, started_at=0.0, now_ns=last_data_ns + 1_000_000_000
        )
        is False
    )


def test_close_orphaned_incident_closes_a_dangling_open_span() -> None:
    orphaned = [{"type": "data_stale", "started_at": 100.0, "ended_at": None}]
    closed = _close_orphaned_incident(orphaned, now=999.0)
    assert closed[0]["ended_at"] == 999.0
    assert closed[0]["note"] == "closed by restart"


def test_close_orphaned_incident_is_a_no_op_when_nothing_is_open() -> None:
    incidents = [{"type": "data_stale", "started_at": 100.0, "ended_at": 135.0}]
    assert _close_orphaned_incident(incidents, now=999.0) == incidents


def test_record_process_start_appends_zero_duration_marker() -> None:
    incidents = _record_process_start([], now=50.0)
    assert incidents == [{"type": "process_start", "started_at": 50.0, "ended_at": 50.0}]


def test_trim_incidents_keeps_only_the_most_recent() -> None:
    incidents = [{"i": i} for i in range(60)]
    trimmed = _trim_incidents(incidents)
    assert len(trimmed) == 50
    assert trimmed[-1] == {"i": 59}


def test_parse_control_message_ignores_a_mode_field_if_present() -> None:
    # AC4: the control channel must never carry a mode/paper-live parameter -- a
    # message that happens to include one anyway must not change the parsed action.
    payload = {"bot_id": "bot-01", "action": "start", "mode": "real_money"}
    assert _parse_control_message(payload, "bot-01") == "start"


class _FakePublishClient:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, _channel: str, message: str) -> None:
        self.published.append(message)


def test_heartbeat_loop_skips_a_failing_build_status_tick_without_crashing(monkeypatch) -> None:
    """
    Regression for a live startup race (2026-09-02): build_status() can briefly raise
    (TypeError from a Cython portfolio call hit before the first quote arrives) --
    _heartbeat_loop must swallow that on a single tick and keep publishing on
    subsequent ticks, not propagate and tear down the whole connection (which would
    also cancel _control_loop's pubsub.listen() via the shared asyncio.gather in run()).
    """
    monkeypatch.setattr(bot_status_module, "_STATUS_HEARTBEAT_SECONDS", 0.0)
    calls = {"n": 0}

    def _flaky_build_status(*_args: object, **_kwargs: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("float() argument must be a string or a real number, not 'NoneType'")
        return {"tick": calls["n"]}

    monkeypatch.setattr(bot_status_module, "build_status", _flaky_build_status)
    client = _FakePublishClient()

    async def _run_briefly() -> None:
        task = asyncio.create_task(
            _heartbeat_loop(
                client,
                strategy=types.SimpleNamespace(last_data_ns=0, is_running=True),
                bot_id="bot-01",
                mode="paper",
                started_at=0.0,
                db_path="unused",
                incidents=[],
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run_briefly())

    assert calls["n"] >= 2  # the failing tick did not stop later ticks from running
    assert len(client.published) >= 1  # at least one tick after the failure published
    assert all(json.loads(msg)["tick"] > 1 for msg in client.published)


def test_heartbeat_loop_does_not_flag_a_deliberately_stopped_bot_as_data_stale(
    monkeypatch,
) -> None:
    """
    Regression: a bot the operator deliberately stopped (strategy.is_running=False)
    legitimately stops receiving fresh data -- last_data_ns=0 from process start plus
    started_at=0.0 would otherwise trip _is_feed_stale on the very first tick,
    misreporting an intentional stop as a WS/feed-health incident.
    """
    monkeypatch.setattr(bot_status_module, "_STATUS_HEARTBEAT_SECONDS", 0.0)
    monkeypatch.setattr(bot_status_module, "build_status", lambda *a, **kw: {})
    client = _FakePublishClient()
    incidents: list[dict] = []

    async def _run_briefly() -> None:
        task = asyncio.create_task(
            _heartbeat_loop(
                client,
                strategy=types.SimpleNamespace(last_data_ns=0, is_running=False),
                bot_id="bot-01",
                mode="paper",
                started_at=0.0,
                db_path="unused",
                incidents=incidents,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run_briefly())

    assert incidents == []
