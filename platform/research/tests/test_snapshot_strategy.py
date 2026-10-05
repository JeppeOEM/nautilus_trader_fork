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
Integration test: SnapshotStrategy in BacktestEngine with synthetic DydxSecondSnapshot data
(Story 2.3, AC1) -- proves raw HFT-granularity data drives a real strategy to a real fill.

Uses a raw BacktestEngine directly (matching test_ofi_strategy.py's pattern), not BacktestNode:
BacktestNode's post-run node.get_engines()[0] introspection was found, during this story's
verification, to return an engine whose cache/report generation reflects an empty state even
though the run's own event log clearly showed a real OrderFilled event -- a real, unexplained
BacktestNode-specific quirk (not investigated further; backtest_snapshot.py's actual run() path
was independently confirmed correct via direct log inspection, see the Dev Agent Record). A raw
BacktestEngine sidesteps this and is the established, reliable pattern this test suite already
uses for strategy-level integration tests.

Two real, non-obvious things a custom (non-Nautilus) Data type needs to be usable in
BacktestEngine, found while writing this test:
  1. Each object must be wrapped in `CustomData(DataType(DydxSecondSnapshot), obj)` before
     `engine.add_data(...)` -- passing raw DydxSecondSnapshot instances directly makes
     DataEngine reject them ("Cannot handle data: unrecognized type"), since only recognized
     Nautilus built-in types or explicitly-wrapped CustomData reach the custom-data dispatch
     path (`DataEngine._handle_custom_data`).
  2. `engine.add_data(..., client_id=ClientId(...))` is required for a non-Nautilus data type
     (`nautilus_trader.core.inspect.is_nautilus_class()` returns False for DydxSecondSnapshot,
     since its __module__ doesn't match any recognized prefix) -- this is a bookkeeping label
     only, not a real live data client.

Also: the SimulatedExchange has no concept of DydxSecondSnapshot as market data (it's a custom
type, invisible to order matching) -- a MARKET order against an instrument with no real
TradeTick/QuoteTick/OrderBookDelta stream rejects with "no market for <symbol>". A parallel
TradeTick stream (unused by the strategy itself, only by the exchange for fill pricing) is
required alongside the DydxSecondSnapshot stream that drives the strategy's actual signal.

Several engines per file are safe: conftest.py's session-scoped BacktestEngine
(`_keep_nautilus_log_guard_alive`) keeps Nautilus's logging guard alive, which root-caused the
native abort that once limited this file to a single engine construction (Stories 2.2/2.3).
Run as a script (`__main__`, outside pytest) the same guard is held by hand.
"""

from decimal import Decimal

from kernel.indicators import OFI_GAP_NS
from kernel.indicators import MultiLevelOFI
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from research.strategies.snapshot_strategy import SnapshotStrategy
from research.strategies.snapshot_strategy import SnapshotStrategyConfig


_IID = InstrumentId(Symbol("BTC-USD-PERP"), Venue("DYDX"))

_INSTRUMENT = CryptoPerpetual(
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


def _snapshot(ts: int, bid_size: float) -> DydxSecondSnapshot:
    return make_snapshot(
        instrument_id=_IID,
        bid_prices=[round(100.0 - j * 0.1, 1) for j in range(10)],
        bid_sizes=[round(bid_size - j * 0.1, 3) for j in range(10)],
        ask_prices=[round(100.1 + j * 0.1, 1) for j in range(10)],
        ask_sizes=[round(5.0 - j * 0.1, 3) for j in range(10)],
        buy_volume=1.0,
        sell_volume=1.0,
        buy_count=1,
        sell_count=1,
        ts_event=ts,
        ts_init=ts,
        price_precision=1,  # the instrument definition's
        size_precision=3,
    )


def _trade(ts: int, seq: int) -> TradeTick:
    return TradeTick(
        instrument_id=_IID,
        price=Price(100.1, 1),
        size=Quantity(1.0, 3),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId(str(seq)),
        ts_event=ts,
        ts_init=ts,
    )


def _engine() -> BacktestEngine:
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=_IID.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDC,
        starting_balances=[Money(10_000, USDC)],
        book_type=BookType.L1_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    return engine


_Data = tuple[list[CustomData], list[TradeTick]]
_NS_PER_S = 1_000_000_000
_OFI_LEVELS = 10
_OFI_WINDOW = 20


def _growing_bid_imbalance_data(n: int = 100) -> _Data:
    """
    N snapshots (1/s) with steadily growing bid size (constant ask) -- strong positive OFI --
    plus a matching TradeTick per snapshot so the exchange has a market to fill against.

    Each trade's ts_event is offset +0.5s from its paired snapshot, so BacktestEngine's
    timestamp-ordered merge always processes the snapshot (and any resulting order) before the
    trade that would fill it -- this ordering is relied on, not incidental.
    """
    ts = 1_000_000_000
    step = 1_000_000_000
    snapshots = []
    trades = []
    for i in range(n):
        snapshots.append(_snapshot(ts, bid_size=5.0 + i * 0.5))
        trades.append(_trade(ts + step // 2, i))
        ts += step
    wrapped = [CustomData(DataType(DydxSecondSnapshot), s) for s in snapshots]
    return wrapped, trades


def _run(
    buy_threshold: float,
    sell_threshold: float,
    data: _Data | None = None,
    ofi_window: int = _OFI_WINDOW,
) -> BacktestEngine:
    """Run SnapshotStrategy over `data` (default: the growing-bid data) with the given thresholds."""
    engine = _engine()
    snapshots, trades = data if data is not None else _growing_bid_imbalance_data()
    engine.add_data(trades)
    engine.add_data(snapshots, client_id=ClientId("DYDX"))

    config = SnapshotStrategyConfig(
        instrument_id=_IID,
        trade_size=Decimal("0.01"),
        ofi_levels=_OFI_LEVELS,
        ofi_window=ofi_window,
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
    )
    engine.add_strategy(SnapshotStrategy(config))
    engine.run()
    return engine


def _dispose(engine: BacktestEngine) -> None:
    engine.reset()
    engine.dispose()


def test_snapshot_strategy_generates_long_entry_on_bid_side_imbalance() -> None:
    """Growing bid-side imbalance -> MultiLevelOFI crosses buy_threshold -> long entry fills."""
    engine = _run(buy_threshold=1.0, sell_threshold=-1.0)

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) >= 1, "expected at least one fill from the engineered bid-side imbalance"
    assert (fills["side"] == "BUY").any(), "expected a BUY fill from bid-side imbalance"

    _dispose(engine)


def _ofi_extremes(snapshots: list[CustomData]) -> tuple[float, float]:
    """Replay the strategy's own MultiLevelOFI over the snapshots: (min, max) of its readings."""
    ofi = MultiLevelOFI(levels=_OFI_LEVELS, window=_OFI_WINDOW)
    readings = []
    for wrapped in snapshots:
        s = wrapped.data
        ofi.update_raw(s.bid_prices, s.bid_sizes, s.ask_prices, s.ask_sizes)
        if ofi.initialized:
            readings.append(ofi.value)
    return min(readings), max(readings)


def test_snapshot_strategy_no_trade_below_threshold() -> None:
    """
    Thresholds just beyond the data's own extreme OFI readings on both sides -> no fills. Tight
    rather than unreachable, so the test fails if the strategy trades at a reading it never
    crossed, not only if it trades at all.
    """
    snapshots, trades = _growing_bid_imbalance_data()
    lowest, highest = _ofi_extremes(snapshots)
    epsilon = 1e-6
    assert lowest < highest, "fixture produced constant OFI readings: nothing to bracket"
    engine = _run(highest + epsilon, lowest - epsilon, data=(snapshots, trades))

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) == 0, "expected no fills when no reading crosses either threshold"

    _dispose(engine)


# The t=3 s row plus a hole 4 s wider than OFI_GAP_NS, whatever that threshold is set to.
_GAP_ROW_S = 3 + OFI_GAP_NS // _NS_PER_S + 4


def _round_trip_then_gap_data() -> _Data:
    """
    With `ofi_window=1` each reading is one row's contribution (10 levels x the bid-size step):
    t=2 s reads +10 (long entry), t=3 s reads -10 (long exit, flat again), then the row after a
    gap wider than `OFI_GAP_NS` only re-baselines, leaving the stale -10 below sell_threshold
    while flat. A trade 0.5 s after each row fills any order before the next row.
    """
    sizes = {1: 5.0, 2: 6.0, 3: 5.0, _GAP_ROW_S: 5.0}
    snapshots = [
        CustomData(DataType(DydxSecondSnapshot), _snapshot(t * _NS_PER_S, bid_size=size))
        for t, size in sizes.items()
    ]
    trades = [_trade(t * _NS_PER_S + _NS_PER_S // 2, i) for i, t in enumerate(sizes)]
    return snapshots, trades


def test_snapshot_strategy_does_not_trade_the_pre_gap_reading_on_the_post_gap_row() -> None:
    """
    The DW-248 rule for SnapshotStrategy: the post-gap row's MultiLevelOFI value is still the
    pre-gap reading, so evaluating it would open a short on a reading the new book never made.
    """
    engine = _run(1.0, -1.0, data=_round_trip_then_gap_data(), ofi_window=1)

    orders = engine.cache.orders(instrument_id=_IID)
    assert [o.side_string() for o in orders] == ["BUY", "SELL"], "expected one round trip"
    assert all(o.ts_init < _GAP_ROW_S * _NS_PER_S for o in orders), "order on the post-gap row"

    _dispose(engine)


if __name__ == "__main__":
    # Outside pytest there is no conftest: hold one engine (and its logging guard) for the run.
    _log_guard = _engine()
    test_snapshot_strategy_generates_long_entry_on_bid_side_imbalance()
    test_snapshot_strategy_no_trade_below_threshold()
    test_snapshot_strategy_does_not_trade_the_pre_gap_reading_on_the_post_gap_row()
    print("ok")
