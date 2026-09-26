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
Byte-identity of the bots' published language across the Story 25.3 move (AD-D12, MR2).

`fixtures/replay_payloads.json` was recorded from the pre-move `live_paper` code (a scratch
recorder run once, before any file moved): one deterministic BacktestEngine run of
`DummyStrategy` with its fills recorded, plus seeded round trips spanning the history windows,
then the `bots:status` string, the four `bots:history` strings (and `all` without a starting
balance), the `bots:incidents` string after each transition of a scripted tick sequence, and the
control decision for each of a fixed message list. The same inputs are replayed here through the
new API and every string must be identical.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import cast

from observability import error_ledger

from bots.application.history import HistoryPublisher
from bots.application.ports import BotRuntime
from bots.application.supervise import Supervisor
from bots.application.supervise import parse_control_message
from bots.domain.bot import Bot
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.strategies.dummy import DummyStrategy
from bots.strategies.dummy import DummyStrategyConfig
from bots.tests.support import record_fills
from bots.tests.support import status_of
from bots.tests.support import unused_connect
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


_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "replay_payloads.json").read_text())
_INPUTS = _FIXTURE["inputs"]
_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_TS_START = 1_000_000_000
_STEP_NS = 1_000_000_000


def _delta(
    action: BookAction, side: OrderSide, price: float, size: float, ts: int, seq: int
) -> OrderBookDelta:
    order = BookOrder(
        side=side,
        price=Price(price, _INSTRUMENT.price_precision),
        size=Quantity(size, _INSTRUMENT.size_precision),
        order_id=0,
    )
    return OrderBookDelta(
        instrument_id=_IID,
        action=action,
        order=order,
        flags=0,
        sequence=seq,
        ts_event=ts,
        ts_init=ts,
    )


def _mid(second: int) -> float:
    cycle_len, amplitude = _INPUTS["cycle_len"], _INPUTS["amplitude"]
    cycle = ((second - 1) // cycle_len) % 2
    ramp = (((second - 1) % cycle_len) / cycle_len) * amplitude
    return 100.0 + ramp if cycle == 0 else 100.0 + amplitude - ramp


def _data() -> list:
    data: list = []
    seq = 0
    for i in range(2):
        data.append(_delta(BookAction.ADD, OrderSide.BUY, 100.0 - i, 10.0, _TS_START, seq))
        seq += 1
    for i in range(2):
        data.append(_delta(BookAction.ADD, OrderSide.SELL, 101.0 + i, 10.0, _TS_START, seq))
        seq += 1
    for i in range(1, _INPUTS["n_seconds"] + 1):
        ts = _TS_START + i * _STEP_NS
        bid, ask = _mid(i) - 0.5, _mid(i) + 0.5
        data.append(
            TestDataStubs.quote_tick(
                instrument=_INSTRUMENT,
                bid_price=bid,
                ask_price=ask,
                bid_size=10.0,
                ask_size=10.0,
                ts_event=ts,
                ts_init=ts,
            )
        )
        data.append(_delta(BookAction.UPDATE, OrderSide.BUY, bid, 10.0, ts, seq))
        seq += 1
        data.append(_delta(BookAction.UPDATE, OrderSide.SELL, ask, 10.0, ts, seq))
        seq += 1
    return data


def _run(store: SqliteFillsStore) -> tuple[BacktestEngine, DummyStrategy]:
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=_IID.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=_INSTRUMENT.quote_currency,
        starting_balances=[Money(10_000, _INSTRUMENT.quote_currency)],
        book_type=BookType.L2_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    engine.add_data(_data())
    strategy = DummyStrategy(
        DummyStrategyConfig(
            instrument_id=_IID,
            trade_size=Decimal("0.001"),
            bar_spec="1-SECOND-MID-INTERNAL",
            trend_lookback=2,
            ofi_levels=2,
            ofi_window=2,
            obi_levels=2,
            trend_buy_threshold=_INPUTS["trend_buy_threshold"],
            trend_sell_threshold=_INPUTS["trend_sell_threshold"],
            ofi_confirm_threshold=-999_999.0,
        )
    )
    engine.add_strategy(strategy)
    record_fills(strategy, store, _INPUTS["bot_id"])
    engine.run()
    for ts, side, price, qty, realized, position_realized in _INPUTS["seeded_fills"]:
        write_fill(store, _INPUTS["bot_id"], ts, side, price, qty, realized, position_realized)
    return engine, strategy


def test_status_history_and_fill_rows_are_byte_identical(store: SqliteFillsStore) -> None:
    engine, strategy = _run(store)
    bot_id = _INPUTS["bot_id"]
    try:
        assert store.recent_trades(bot_id, None, 10_000) == _FIXTURE["fills"]
        status = status_of(
            strategy,
            store,
            bot_id=bot_id,
            mode=_INPUTS["status_mode"],
            started_at=_INPUTS["status_started_at"],
            now=_INPUTS["status_now"],
        )
        assert json.dumps(status) == _FIXTURE["status"]
        runtime = StrategyCacheReader(strategy)
        now_ns = _INPUTS["history_now_ns"]
        history = HistoryPublisher(
            bot_id, runtime, store, unused_connect, _INPUTS["starting_balance"]
        )
        for range_name, expected in _FIXTURE["history"].items():
            assert json.dumps(history.compute(range_name, now_ns)) == expected, range_name
        no_balance = HistoryPublisher(bot_id, runtime, store, unused_connect, None)
        assert json.dumps(no_balance.compute("all", now_ns)) == _FIXTURE["history_no_balance_all"]
    finally:
        engine.reset()
        engine.dispose()


def test_incident_log_is_byte_identical_across_a_scripted_tick_sequence() -> None:
    script = _FIXTURE["incidents"]
    bot = Bot(_INPUTS["bot_id"], "paper", started_at=script["started_at"])
    snapshots: list[str | None] = [json.dumps(bot.start(script["prior"], script["start_now"]))]
    for now, now_ns, last_data_ns, running in script["ticks"]:
        changed = bot.observe(now, now_ns, last_data_ns, running)
        snapshots.append(json.dumps(bot.incidents) if changed else None)
    assert snapshots == script["snapshots"]


class _Runtime:
    is_running = False

    def start(self) -> None:
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False


def test_control_decisions_are_unchanged() -> None:
    control = _FIXTURE["control"]
    decisions: list[str | None] = []
    for message in control["messages"]:
        try:
            payload = json.loads(message)
        except ValueError:
            decisions.append("unparseable")
            continue
        decisions.append(parse_control_message(payload, control["bot_id"]))
    assert decisions == control["decisions"]


def test_an_unparseable_control_message_is_now_ledgered(store: SqliteFillsStore) -> None:
    """The one deliberate change on this path: a bad message is counted, not only logged."""
    error_ledger.reset()
    runtime = cast(BotRuntime, _Runtime())
    supervisor = Supervisor("bot-01", "paper", runtime, store, unused_connect)
    supervisor.handle_control("not json")
    assert error_ledger.counts() == {"bots.control_message": 1}
