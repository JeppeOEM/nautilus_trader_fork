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
Integration test: backtest_snapshot.run()'s actual production mechanism (Story 2.3 AC1) --
BacktestNode + BacktestDataConfig(data_cls=DydxSecondSnapshot, ...) streaming a real
ParquetDataCatalog, not a raw BacktestEngine with manually-wrapped in-memory data.

test_snapshot_strategy.py already covers SnapshotStrategy's own logic thoroughly via a raw
BacktestEngine (matching test_ofi_strategy.py's established, reliable pattern) -- this file
exists specifically to close the gap that leaves: no committed test previously exercised
BacktestNode + BacktestDataConfig + a real catalog for DydxSecondSnapshot at all, which is the
literal mechanism AC1 requires. Before this file, that mechanism was only proven via an
uncommitted, one-off verification script (see the Dev Agent Record) -- a real coverage gap
found during code review.

Only ONE test function/BacktestNode construction in this file, deliberately -- see
deferred-work.md ("Story 2.2 implementation" and its Story 2.3 addenda) for the confirmed,
reproducible native-crash pattern this avoids. Named to collect alphabetically after
test_ofi_strategy.py ('test_s' > 'test_o') for the same reason.

Asserts on result.iterations and result.stats_pnls (not total_orders/total_positions, which
were found during this story's verification to sometimes read 0 even when stats_pnls clearly
shows real trading activity in the same run -- see backtest_dydx.py's module docstring).
"""

import tempfile
import time
from decimal import Decimal

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals import backtest_snapshot

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


def test_backtest_snapshot_streams_dydx_second_snapshot_via_backtest_node() -> None:
    """Real ParquetDataCatalog, real BacktestNode, real BacktestDataConfig(data_cls=DydxSecondSnapshot)."""
    now_ns = time.time_ns()
    snapshots = []
    trades = []
    for i in range(100):
        ts_snap = now_ns - (100 - i) * 1_000_000_000
        snapshots.append(DydxSecondSnapshot(
            instrument_id=_IID,
            bid_prices=[100.0 - j * 0.1 for j in range(10)],
            bid_sizes=[5.0 + i * 0.5 - j * 0.1 for j in range(10)],
            ask_prices=[100.1 + j * 0.1 for j in range(10)],
            ask_sizes=[5.0 - j * 0.1 for j in range(10)],
            buy_volume=1.0, sell_volume=1.0, buy_count=1, sell_count=1,
            ts_event=ts_snap, ts_init=ts_snap,
        ))
        ts_trade = ts_snap + 500_000_000
        trades.append(TradeTick(
            instrument_id=_IID, price=Price(100.1, 1), size=Quantity(1.0, 3),
            aggressor_side=AggressorSide.BUYER, trade_id=TradeId(str(i)), ts_event=ts_trade, ts_init=ts_trade,
        ))

    with tempfile.TemporaryDirectory() as tmp:
        catalog = ParquetDataCatalog(tmp)
        catalog.write_data([_INSTRUMENT])
        catalog.write_data(snapshots)
        catalog.write_data(trades)

        result = backtest_snapshot.run(
            symbol="BTC-USD-PERP.DYDX", catalog_path=tmp, buy_threshold=1.0, sell_threshold=-1.0,
        )[0]

        assert result.iterations > 0, "expected DydxSecondSnapshot + TradeTick events to be processed"
        assert result.stats_pnls, "expected real trading activity from the engineered bid-side imbalance"


if __name__ == "__main__":
    test_backtest_snapshot_streams_dydx_second_snapshot_via_backtest_node()
    print("ok")
