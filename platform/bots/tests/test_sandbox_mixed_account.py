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
Story 22.6 AC3 evidence: one MARGIN account (what `[venues.BYBIT]` configures for its single
Sandbox client) fills both a spot `CurrencyPair` and a linear `CryptoPerpetual`.

`SandboxExecutionClient` wraps the same `SimulatedExchange` the backtest engine drives, so
this runs the identical matching/account code against a real Binance-shaped spot + perp
pair with Nautilus's own test instruments (no mocks, no network).
"""

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


class _BuyOnce(Strategy):
    def __init__(self, instrument) -> None:
        super().__init__(StrategyConfig(strategy_id=f"buy-{instrument.id.symbol}"))
        self._instrument = instrument

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self._instrument.id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if self.cache.orders_total_count(strategy_id=self.id):
            return
        self.submit_order(
            self.order_factory.market(
                self._instrument.id, OrderSide.BUY, self._instrument.make_qty(0.01)
            )
        )


def _quote(instrument) -> QuoteTick:
    return QuoteTick(
        instrument_id=instrument.id,
        bid_price=instrument.make_price(50_000),
        ask_price=instrument.make_price(50_001),
        bid_size=instrument.make_qty(5),
        ask_size=instrument.make_qty(5),
        ts_event=1,
        ts_init=1,
    )


def test_margin_account_fills_spot_and_linear_perp_on_one_venue() -> None:
    spot = TestInstrumentProvider.btcusdt_binance()
    perp = TestInstrumentProvider.btcusdt_perp_binance()
    engine = BacktestEngine(BacktestEngineConfig(logging=None))
    try:
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(10_000, USDT)],
        )
        for instrument in (spot, perp):
            engine.add_instrument(instrument)
            engine.add_data([_quote(instrument)])
            engine.add_strategy(_BuyOnce(instrument))
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        assert set(fills["instrument_id"]) == {str(spot.id), str(perp.id)}
        assert len(engine.cache.positions_open()) == 2
        assert engine.portfolio.account(Venue("BINANCE")).balance_total(USDT) is not None
    finally:
        engine.dispose()
