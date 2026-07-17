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

Only ONE test function/engine construction in this file, deliberately: the same fatal native
abort documented in deferred-work.md ("Story 2.2 implementation") for cross-module BacktestEngine
construction was found here to ALSO occur across two engine constructions within this single
file when a custom Data type (DydxSecondSnapshot, via register_arrow/CustomData) is involved --
more severe than the Story 2.2 finding, which only manifested across module boundaries. Not
investigated further given the Story 2.2 investigation's own conclusion (a pre-existing, unknown
native fragility, not something introduced by any one test's content). Logged as an addendum to
that same deferred-work.md entry.
"""

from decimal import Decimal

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

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.snapshot_strategy import SnapshotStrategy
from ml_signals.snapshot_strategy import SnapshotStrategyConfig


_IID = InstrumentId(Symbol("BTC-USD-PERP"), Venue("DYDX"))

_INSTRUMENT = CryptoPerpetual(
    instrument_id=_IID, raw_symbol=Symbol("BTC-USD-PERP"),
    base_currency=BTC, quote_currency=USDC, settlement_currency=USDC, is_inverse=False,
    price_precision=1, size_precision=3,
    price_increment=Price(0.1, 1), size_increment=Quantity(0.001, 3),
    max_quantity=None, min_quantity=None, max_notional=None, min_notional=None,
    max_price=None, min_price=None,
    margin_init=Decimal("0.1"), margin_maint=Decimal("0.05"),
    maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.0005"),
    ts_event=0, ts_init=0,
)


def _snapshot(ts: int, bid_size: float) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=_IID,
        bid_prices=[100.0 - j * 0.1 for j in range(10)],
        bid_sizes=[bid_size - j * 0.1 for j in range(10)],
        ask_prices=[100.1 + j * 0.1 for j in range(10)],
        ask_sizes=[5.0 - j * 0.1 for j in range(10)],
        buy_volume=1.0, sell_volume=1.0, buy_count=1, sell_count=1,
        ts_event=ts, ts_init=ts,
    )


def _trade(ts: int, seq: int) -> TradeTick:
    return TradeTick(
        instrument_id=_IID, price=Price(100.1, 1), size=Quantity(1.0, 3),
        aggressor_side=AggressorSide.BUYER, trade_id=TradeId(str(seq)), ts_event=ts, ts_init=ts,
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


def _growing_bid_imbalance_data(n: int = 100) -> tuple[list, list]:
    """n snapshots (1/s) with steadily growing bid size (constant ask) -- strong positive OFI --
    plus a matching TradeTick per snapshot so the exchange has a market to fill against.

    Each trade's ts_event is offset +0.5s from its paired snapshot, so BacktestEngine's
    timestamp-ordered merge always processes the snapshot (and any resulting order) before the
    trade that would fill it -- this ordering is relied on, not incidental."""
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


def test_snapshot_strategy_generates_long_entry_on_bid_side_imbalance() -> None:
    """Growing bid-side imbalance -> MultiLevelOFI crosses buy_threshold -> long entry fills."""
    engine = _engine()
    snapshots, trades = _growing_bid_imbalance_data()
    engine.add_data(trades)
    engine.add_data(snapshots, client_id=ClientId("DYDX"))

    config = SnapshotStrategyConfig(
        instrument_id=_IID,
        trade_size=Decimal("0.01"),
        ofi_levels=10,
        ofi_window=20,
        buy_threshold=1.0,
        sell_threshold=-1.0,
    )
    engine.add_strategy(SnapshotStrategy(config))
    engine.run()

    fills = engine.trader.generate_order_fills_report()
    assert len(fills) >= 1, "expected at least one fill from the engineered bid-side imbalance"
    assert (fills["side"] == "BUY").any(), "expected a BUY fill from bid-side imbalance"

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    test_snapshot_strategy_generates_long_entry_on_bid_side_imbalance()
    print("ok")
