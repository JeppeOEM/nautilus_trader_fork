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
Tests for live_paper.trade_history -- Story 4.6 (AC1/AC2/AC3/AC4, architecture AD-10).

build_status/compute_history is exercised against a real DummyStrategy run through a
real BacktestEngine (same harness precedent as test_bot_status.py) rather than a mocked
Strategy/Cache/Portfolio -- troll/CLAUDE.md TEST-03 bars mocking Nautilus internals. The
price path here deliberately oscillates (unlike test_bot_status.py's monotonic ramp) so
DummyStrategy produces multiple full open->close round trips on one instrument, which is
exactly the scenario that proves the fix: cache.positions_closed() -- verified directly
against nautilus_trader/execution/engine.pyx + nautilus_trader/cache/cache.pyx -- silently
discards a NETTING position's closed history the instant it reopens (same PositionId,
overwritten), so a strategy that keeps trading can end a run with cache.positions_closed()
reporting 0 or 1 closed positions no matter how many round trips it actually completed.
trade_history.subscribe() sidesteps this entirely by recording each fill into
fills_store's SQLite file as it happens, never reading positions_closed() at all.
"""

from decimal import Decimal

from live_paper import fills_store
from live_paper import trade_history
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


def _run_strategy_with_history(
    db_path: str, bot_id: str = "bot-01"
) -> tuple[BacktestEngine, DummyStrategy]:
    engine = _engine()
    engine.add_data(_quotes_and_deltas(n_seconds=_N_SECONDS, levels_per_side=2))
    strategy = DummyStrategy(_config())
    engine.add_strategy(strategy)
    # Subscribe before run() -- a fill that happens before subscription can never be
    # recorded, same as trade_history.run() subscribing before entering its Redis loop.
    trade_history.subscribe(strategy, bot_id=bot_id, db_path=db_path)
    engine.run()
    return engine, strategy


def test_fills_store_keeps_every_round_trip_that_cache_positions_closed_loses(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy_with_history(db_path)

    # The bug this story fixes, reproduced directly: NETTING position reopening
    # overwrites the same PositionId, so Cache's own closed-positions view loses
    # everything but (at most) the currently-still-closed tail.
    cache_closed = strategy.cache.positions_closed(strategy_id=strategy.id)
    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=None, limit=500)

    assert len(cache_closed) == 0
    assert len(trades) == 7
    closing_fills = [t for t in trades if t["realized_pnl"] is not None]
    assert len(closing_fills) == 3
    assert all(pnl > 0 for pnl in (t["realized_pnl"] for t in closing_fills))

    engine.reset()
    engine.dispose()


def test_compute_history_all_range_pnl_series_matches_sum_of_trades(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, _strategy = _run_strategy_with_history(db_path)

    history = trade_history.compute_history(
        bot_id="bot-01", range_name="all", now_ns=200 * _NS_PER_SECOND, db_path=db_path
    )

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


def test_compute_history_includes_metrics_computed_from_the_same_fills(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, _strategy = _run_strategy_with_history(db_path)

    history = trade_history.compute_history(
        bot_id="bot-01",
        range_name="all",
        now_ns=200 * _NS_PER_SECOND,
        db_path=db_path,
        starting_balance=10_000.0,
    )

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
    tmp_path,
) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, _strategy = _run_strategy_with_history(db_path)

    history = trade_history.compute_history(
        bot_id="bot-01",
        range_name="all",
        now_ns=200 * _NS_PER_SECOND,
        db_path=db_path,
        starting_balance=None,
    )

    metrics = history["metrics"]
    assert metrics["sharpe_ratio"] is None
    assert metrics["max_drawdown"] is None
    # Trade-level stats need no starting balance -- still populated.
    assert metrics["win_rate"] is not None

    engine.reset()
    engine.dispose()


def test_compute_history_day_window_excludes_trades_outside_24h_but_all_does_not(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, _strategy = _run_strategy_with_history(db_path)

    far_future_ns = _TS_START + 30 * 24 * 3600 * _NS_PER_SECOND
    day_history = trade_history.compute_history(
        bot_id="bot-01", range_name="day", now_ns=far_future_ns, db_path=db_path
    )
    all_history = trade_history.compute_history(
        bot_id="bot-01", range_name="all", now_ns=far_future_ns, db_path=db_path
    )

    assert day_history["trades"] == []
    assert day_history["pnl_series"] == []
    assert len(all_history["trades"]) == 7

    engine.reset()
    engine.dispose()


def test_on_order_event_ignores_non_fill_order_events(tmp_path) -> None:
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy_with_history(db_path)

    trade_history._on_order_event(object(), strategy=strategy, bot_id="bot-01", db_path=db_path)

    # No fills.db side effect from a non-OrderFilled event -- the earlier real run's
    # 7 fills are the only rows present.
    trades = fills_store.recent_trades("bot-01", db_path, cutoff_ns=None, limit=500)
    assert len(trades) == 7

    engine.reset()
    engine.dispose()


def test_on_order_event_swallows_a_store_write_failure_instead_of_crashing(tmp_path) -> None:
    """
    Regression for a live incident (2026-09-02): the on-fill handler had no
    try/except, so a fills.db write failure (there: a bind-mounted data dir Docker
    created as root, unwritable by the container's uid 1000) propagated out of this
    synchronous message-bus handler and crashed the whole TradingNode on the first
    real fill. `db_path` here is a directory, not a file -- sqlite3.connect() on it
    reproduces the exact "unable to open database file" error seen live.
    """
    db_path = str(tmp_path / "fills.db")
    engine, strategy = _run_strategy_with_history(db_path)
    # cache.positions_closed() is empty by this test's own sibling assertion above (the
    # NETTING-reopen bug this story works around) -- orders_closed() is unaffected by
    # that bug and gives a real filled Order to pull an OrderFilled event from.
    closed_orders = strategy.cache.orders_closed(strategy_id=strategy.id)
    fill = closed_orders[0].events[-1]

    unwritable_db_path = str(tmp_path)  # a directory, not a file
    trade_history._on_order_event(
        fill, strategy=strategy, bot_id="bot-01", db_path=unwritable_db_path
    )  # must not raise

    engine.reset()
    engine.dispose()
