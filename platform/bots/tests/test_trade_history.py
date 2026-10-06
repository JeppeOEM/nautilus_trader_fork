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
Tests for bots.application.history and bots.domain.fill_ledger -- Story 4.6 (AC1/AC2/AC3/AC4,
architecture AD-10), moved in Story 25.3.

The history is exercised against a real DummyStrategy run through a
real BacktestEngine (same harness precedent as test_bot_status.py) rather than a mocked
Strategy/Cache/Portfolio -- platform/CLAUDE.md TEST-03 bars mocking Nautilus internals. The
price path here deliberately oscillates (unlike test_bot_status.py's monotonic ramp) so
DummyStrategy produces multiple full open->close round trips on one instrument, which is
exactly the scenario that proves the fix: cache.positions_closed() -- verified directly
against nautilus_trader/execution/engine.pyx + nautilus_trader/cache/cache.pyx -- silently
discards a NETTING position's closed history the instant it reopens (same PositionId,
overwritten), so a strategy that keeps trading can end a run with cache.positions_closed()
reporting 0 or 1 closed positions no matter how many round trips it actually completed.
`HistoryPublisher.attach()` sidesteps this entirely by recording each fill into fills.db as
it happens (through `FillLedger`), never reading positions_closed() at all.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from observability import error_ledger

from bots.application.history import HistoryPublisher
from bots.application.ports import BotRuntime
from bots.application.ports import history_key
from bots.domain.fill_ledger import FillRecord
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.strategies.dummy import DummyStrategy
from bots.strategies.dummy import DummyStrategyConfig
from bots.tests.support import FakeBus
from bots.tests.support import ThreadRecordingStore
from bots.tests.support import record_fills
from bots.tests.support import unused_connect
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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.trading.strategy import Strategy


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_PP = _INSTRUMENT.price_precision
_SP = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency

_TS_START = 1_000_000_000
_STEP_NS = 1_000_000_000  # 1 second, matching book_snapshot_interval_secs=1.0
_NS_PER_SECOND = 1_000_000_000

# Two full round trips (open->close->open->close), verified empirically against this
# exact scenario: BUY@18s (open), SELL@56s (close #1), SELL@57s (open short), BUY@85s
# (close #2), BUY@86s (open again, left running at the end of the 120s window).
_TREND_BUY_THRESHOLD = 0.58
_TREND_SELL_THRESHOLD = 0.48
_N_SECONDS = 120
_CYCLE_LEN = 30
_AMPLITUDE = 20.0


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


def _oscillating_mid(second: int) -> float:
    """Triangle wave: up for _CYCLE_LEN seconds, down for _CYCLE_LEN, repeating."""
    cycle = ((second - 1) // _CYCLE_LEN) % 2
    step = (second - 1) % _CYCLE_LEN
    ramp = (step / _CYCLE_LEN) * _AMPLITUDE
    return 100.0 + ramp if cycle == 0 else 100.0 + _AMPLITUDE - ramp


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
        mid = _oscillating_mid(i)
        bid_price = mid - 0.5
        ask_price = mid + 0.5
        data.append(
            TestDataStubs.quote_tick(
                instrument=_INSTRUMENT,
                bid_price=bid_price,
                ask_price=ask_price,
                bid_size=10.0,
                ask_size=10.0,
                ts_event=ts,
                ts_init=ts,
            ),
        )
        data.append(_delta(BookAction.UPDATE, OrderSide.BUY, bid_price, 10.0, ts, seq))
        seq += 1
        data.append(_delta(BookAction.UPDATE, OrderSide.SELL, ask_price, 10.0, ts, seq))
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
        "trend_buy_threshold": _TREND_BUY_THRESHOLD,
        "trend_sell_threshold": _TREND_SELL_THRESHOLD,
        # Permissive enough that mlofi never blocks a signal the trend already gave --
        # this test is about the fills-durability path, not about tuning the mlofi
        # confirmation gate itself (same trick test_bot_status.py's own tests use).
        "ofi_confirm_threshold": -999_999.0,
    }
    defaults.update(overrides)
    return DummyStrategyConfig(**defaults)


def _history(store: SqliteFillsStore, starting_balance: float | None = None) -> HistoryPublisher:
    """Build a publisher over `store` that only computes payloads (never attached/connected)."""
    return HistoryPublisher("bot-01", _UNUSED_RUNTIME, store, unused_connect, starting_balance)


class _UnusedRuntime:
    """A `BotRuntime` the test never reads: the publisher here only computes or writes."""

    def __getattr__(self, name: str) -> object:
        # AttributeError, not AssertionError: `hasattr`/`inspect.unwrap` probing (pytest's
        # doctest collection reads `__wrapped__`) must see a missing attribute, not a failure.
        raise AttributeError(f"unexpected runtime access: {name}")


_UNUSED_RUNTIME = cast(BotRuntime, _UnusedRuntime())


def _run_strategy_with_history(
    store: SqliteFillsStore, bot_id: str = "bot-01"
) -> tuple[BacktestEngine, DummyStrategy]:
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=_N_SECONDS, levels_per_side=2))
    strategy = DummyStrategy(_config())
    engine.add_strategy(strategy)
    # Attach before run() -- a fill that happens before attaching can never be recorded, same
    # as HistoryPublisher.run() attaching before entering its Redis loop.
    record_fills(strategy, store, bot_id)
    engine.run()
    return engine, strategy


def test_fills_store_keeps_every_round_trip_that_cache_positions_closed_loses(
    store: SqliteFillsStore,
) -> None:
    engine, strategy = _run_strategy_with_history(store)

    # The bug this story fixes, reproduced directly: NETTING position reopening
    # overwrites the same PositionId, so Cache's own closed-positions view loses
    # everything but (at most) the currently-still-closed tail.
    cache_closed = strategy.cache.positions_closed(strategy_id=strategy.id)
    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)

    assert len(cache_closed) == 0
    assert len(trades) == 7
    closing_fills = [t for t in trades if t["realized_pnl"] is not None]
    assert len(closing_fills) == 3
    assert all(pnl > 0 for pnl in (t["realized_pnl"] for t in closing_fills))

    engine.reset()
    engine.dispose()


def test_compute_history_all_range_pnl_series_matches_sum_of_trades(
    store: SqliteFillsStore,
) -> None:
    engine, _strategy = _run_strategy_with_history(store)

    history = _history(store).compute("all", 200 * _NS_PER_SECOND)

    assert history["bot_id"] == "bot-01"
    assert history["range"] == "all"
    assert len(history["trades"]) == 7
    trades_total = sum(
        t["realized_pnl"] for t in history["trades"] if t["realized_pnl"] is not None
    )
    series_total = sum(entry["pnl"] for entry in history["pnl_series"])
    assert trades_total == series_total

    engine.reset()
    engine.dispose()


def test_compute_history_includes_metrics_computed_from_the_same_fills(
    store: SqliteFillsStore,
) -> None:
    engine, _strategy = _run_strategy_with_history(store)

    history = _history(store, 10_000.0).compute("all", 200 * _NS_PER_SECOND)

    closing_pnls = [t["realized_pnl"] for t in history["trades"] if t["realized_pnl"] is not None]
    expected_win_rate = sum(1 for p in closing_pnls if p > 0) / len(closing_pnls)
    metrics = history["metrics"]
    assert metrics["win_rate"] == expected_win_rate
    assert metrics["expectancy"] == sum(closing_pnls) / len(closing_pnls)
    # Every round trip in this fixture is a win (see _run_strategy_with_history's own
    # sibling assertion) inside one UTC day bucket, so the (single-point) equity curve
    # never dips below its own starting value -- drawdown is exactly zero. This is also
    # the signal that the return-based stats were actually computed (not skipped, as
    # the no-starting-balance test below asserts None instead).
    assert metrics["max_drawdown"] == 0.0

    engine.reset()
    engine.dispose()


def test_compute_history_skips_return_based_metrics_without_a_starting_balance(
    store: SqliteFillsStore,
) -> None:
    engine, _strategy = _run_strategy_with_history(store)

    history = _history(store, None).compute("all", 200 * _NS_PER_SECOND)

    metrics = history["metrics"]
    assert metrics["sharpe_ratio"] is None
    assert metrics["max_drawdown"] is None
    # Trade-level stats need no starting balance -- still populated.
    assert metrics["win_rate"] is not None

    engine.reset()
    engine.dispose()


def test_compute_history_day_window_excludes_trades_outside_24h_but_all_does_not(
    store: SqliteFillsStore,
) -> None:
    engine, _strategy = _run_strategy_with_history(store)

    far_future_ns = _TS_START + 30 * 24 * 3600 * _NS_PER_SECOND
    day_history = _history(store).compute("day", far_future_ns)
    all_history = _history(store).compute("all", far_future_ns)

    assert day_history["trades"] == []
    assert day_history["pnl_series"] == []
    assert len(all_history["trades"]) == 7

    engine.reset()
    engine.dispose()


def test_a_non_fill_order_event_writes_nothing(store: SqliteFillsStore) -> None:
    engine, strategy = _run_strategy_with_history(store)

    # A real, non-fill order event (the order's OrderInitialized), delivered on the strategy's
    # own order-event topic exactly where a live one arrives.
    initialized = strategy.cache.orders_closed(strategy_id=strategy.id)[0].events[0]
    assert type(initialized).__name__ == "OrderInitialized"
    strategy.msgbus.publish(topic=f"events.order.{strategy.id}", msg=initialized)

    # No fills.db side effect from a non-OrderFilled event -- the earlier real run's
    # 7 fills are the only rows present.
    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)
    assert len(trades) == 7

    engine.reset()
    engine.dispose()


def test_a_store_write_failure_is_ledgered_instead_of_crashing(
    store: SqliteFillsStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Regression for a live incident (2026-09-02): the on-fill handler had no
    try/except, so a fills.db write failure (there: a bind-mounted data dir Docker
    created as root, unwritable by the container's uid 1000) propagated out of this
    synchronous message-bus handler and crashed the whole TradingNode on the first
    real fill. The store path here is a directory, not a file -- sqlite3.connect() on it
    reproduces the exact "unable to open database file" error seen live.

    Also covers a second review finding: a lost fill must be escalated (ERROR, with
    the full fill payload, and since Story 25.3 the `bots.fill_lost` ledger site) rather
    than silently dropped at WARNING with no way to recover it -- see
    HistoryPublisher._write's own comment.
    """
    engine, strategy = _run_strategy_with_history(store)
    # cache.positions_closed() is empty by this test's own sibling assertion above (the
    # NETTING-reopen bug this story works around) -- orders_closed() is unaffected by
    # that bug and gives a real filled Order to pull an OrderFilled event from.
    closed_orders = strategy.cache.orders_closed(strategy_id=strategy.id)
    fill = closed_orders[0].events[-1]

    error_ledger.reset()
    unwritable = SqliteFillsStore(str(tmp_path))  # a directory, not a file
    history = HistoryPublisher("bot-01", _UNUSED_RUNTIME, unwritable, unused_connect, None)
    try:
        with caplog.at_level("ERROR"):
            history.record_fill(fill, strategy.cache.position(fill.position_id))  # must not raise
    finally:
        unwritable.close()

    assert error_ledger.counts() == {"bots.fill_lost": 1}
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "ERROR"
    assert "permanently lost" in caplog.records[0].message
    assert "bot-01" in caplog.records[0].message

    engine.reset()
    engine.dispose()


class _MultiFillCloseStrategy(Strategy):
    """
    Test-only strategy (not DummyStrategy): submits one opening BUY, then two separate
    reducing SELL orders that together close the position. Models a single closing
    intent venue-side-fragmented into multiple fills without needing to reproduce
    dYdX's own partial-fill matching inside BacktestEngine -- from
    FillLedger.attribute's point of view, two reducing OrderFilled events for one
    round trip is the same shape either way.
    """

    def __init__(self, instrument_id: InstrumentId, open_qty: Decimal, reduce_qty: Decimal) -> None:
        super().__init__()
        self._instrument_id = instrument_id
        self._open_qty = open_qty
        self._reduce_qty = reduce_qty
        self._tick_count = 0

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self._instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self._tick_count += 1
        if self._tick_count == 1:
            self._submit(OrderSide.BUY, self._open_qty)
        elif self._tick_count == 5 or self._tick_count == 10:
            self._submit(OrderSide.SELL, self._reduce_qty)

    def _submit(self, side: OrderSide, qty: Decimal) -> None:
        instrument = self.cache.instrument(self._instrument_id)
        order = self.order_factory.market(
            instrument_id=self._instrument_id,
            order_side=side,
            quantity=instrument.make_qty(qty),
        )
        self.submit_order(order)


def test_fill_pnl_splits_a_multi_fill_close_proportionally_and_sums_to_the_total(
    store: SqliteFillsStore,
) -> None:
    """
    Regression for a review finding: a single closing intent that fills across two
    separate reducing fills (dYdX can fragment one order this way) must give each
    reducing fill its own realized_pnl share, not dump the whole round trip's PnL onto
    only the fill that happens to close the position -- and position_realized_pnl must
    land on exactly the closing fill, holding the round trip's true total.
    """
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=20, levels_per_side=2))
    strategy = _MultiFillCloseStrategy(_IID, open_qty=Decimal("0.002"), reduce_qty=Decimal("0.001"))
    engine.add_strategy(strategy)
    history = record_fills(strategy, store)
    engine.run()

    trades = store.recent_trades("bot-01", cutoff_ns=None, limit=500)
    assert len(trades) == 3  # 1 opening fill + 2 reducing fills

    opening, first_reduce, closing_reduce = trades
    assert opening["realized_pnl"] is None

    position = strategy.cache.positions_closed(strategy_id=strategy.id)[0]
    total_realized_pnl = position.realized_pnl.as_double()

    # Both reducing fills carry their own non-null share, and they sum to the true total.
    assert first_reduce["realized_pnl"] is not None
    assert closing_reduce["realized_pnl"] is not None
    assert first_reduce["realized_pnl"] != closing_reduce["realized_pnl"]
    assert first_reduce["realized_pnl"] + closing_reduce["realized_pnl"] == total_realized_pnl

    # position_realized_pnl (the round-trip total, for win_rate_stats()) lands on
    # exactly the closing fill -- never the earlier partial reduce.
    assert store.position_realized_pnls("bot-01", cutoff_ns=None) == [total_realized_pnl]
    closed_trades, _wins = store.win_rate_stats("bot-01")
    assert closed_trades == 1  # one round trip, not two "trades" for its two fills
    # FillLedger's pending estimates self-clean: nothing is left once the position closed.
    assert history.ledger._pending_realized_pnl == {}

    engine.reset()
    engine.dispose()


class _GatedStore:
    """A `FillsStore` whose writes block until the gate opens, so later ones queue behind them."""

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.written: list[FillRecord] = []

    def write_fill(self, record: FillRecord) -> bool:
        self.gate.wait(timeout=5.0)
        self.written.append(record)
        return True


class _FixedLedger:
    """Attributes every fill to one fixed record: this test is about the write, not the PnL."""

    def __init__(self, record: FillRecord) -> None:
        self.record = record

    def attribute(self, fill: object, position: object) -> FillRecord:
        return self.record


_A_FILL = FillRecord("bot-01", 1, "BUY", 1.0, 1.0, None, None, "T-1")


def test_drain_waits_out_queued_writes_before_the_executor_shuts_down() -> None:
    """
    Regression: `TradingNode.dispose` shuts the loop's executor down with `cancel_futures=True`,
    so a fill write still queued then never ran -- lost without a `bots.fill_lost` entry. The
    composition root drains every publisher first; here a one-worker executor queues the second
    write behind a blocked first one, exactly the shutdown window.
    """
    gate = threading.Event()
    store = _GatedStore(gate)
    history = HistoryPublisher("bot-01", _UNUSED_RUNTIME, store, unused_connect, None)  # type: ignore[arg-type]
    history.ledger = _FixedLedger(_A_FILL)  # type: ignore[assignment]

    async def _shutdown() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        asyncio.get_running_loop().set_default_executor(executor)
        history.record_fill(None, None)  # type: ignore[arg-type]
        history.record_fill(None, None)  # type: ignore[arg-type]
        asyncio.get_running_loop().call_later(0.05, gate.set)
        await history.drain()
        executor.shutdown(wait=True, cancel_futures=True)  # what dispose does

    asyncio.run(_shutdown())

    assert len(store.written) == 2


class _FailingLedger:
    """A `FillLedger` whose attribution raises, as a bug in it would."""

    def attribute(self, fill: object, position: object) -> FillRecord:
        raise ValueError("attribution failed")


def test_a_failed_attribution_is_ledgered_and_writes_nothing(store: SqliteFillsStore) -> None:
    # DW-230: the exception must not escape into the message bus's dispatch, and no row with a
    # fabricated PnL may be written in its place.
    error_ledger.reset()
    history = _history(store)
    history.ledger = _FailingLedger()  # type: ignore[assignment]

    history.record_fill("<the OrderFilled>", None)

    assert store.recent_trades("bot-01", None, 10) == []
    assert error_ledger.counts() == {"bots.fill_lost": 1}
    assert "<the OrderFilled>" in error_ledger.last_details()["bots.fill_lost"]


def test_a_re_delivered_fill_is_logged_and_stored_once(
    store: SqliteFillsStore, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    history = _history(store)
    history.ledger = _FixedLedger(_A_FILL)  # type: ignore[assignment]

    with caplog.at_level("WARNING"):
        history.record_fill(None, None)
        history.record_fill(None, None)

    assert len(store.recent_trades("bot-01", None, 10)) == 1
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert "T-1" in caplog.records[0].message
    assert error_ledger.counts() == {}


def test_refresh_reads_fills_off_the_event_loop_and_sets_every_range_in_order(
    store: SqliteFillsStore,
) -> None:
    # DW-222: the full-history scans on the loop would freeze every bot on the node.
    recording = ThreadRecordingStore(store)
    history = HistoryPublisher("bot-01", _UNUSED_RUNTIME, recording, unused_connect, None)
    bus = FakeBus()
    loop_threads: list[int] = []

    async def _refresh() -> None:
        loop_threads.append(threading.get_ident())
        await history.refresh(bus)

    asyncio.run(_refresh())

    assert recording.read_threads
    assert loop_threads[0] not in recording.read_threads
    assert list(bus.keys) == [history_key("bot-01", r) for r in ("day", "week", "month", "all")]
