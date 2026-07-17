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
Dummy Strategy (Story 3.2): wires every Epic 2 indicator into a live `TradingNode`
strategy, proving the research -> backtest -> live loop closes end to end in paper
mode. Deliberately unsophisticated -- per epics.md's own "Dummy Strategy" framing,
this proves every signal is alive and flowing live, it is not a tuned alpha strategy.

Live data sourcing per indicator (no `DydxSecondSnapshot` exists live -- that type is
built by dydx_collector's own stateful aggregation loop, which this module never
imports per AD-4):

  - Microprice, OrderFlowImbalance (top-of-book): fed from `QuoteTick` via
    `subscribe_quote_ticks`, matching each indicator's own `handle_quote_tick`.
  - MultiLevelOBI, MultiLevelOFI (top-N levels): fed from a periodic 1-second clock
    timer that snapshots `self.cache.order_book(...)` into bid/ask price/size lists.
    This is deliberately NOT fed on every `on_order_book_deltas` callback -- both
    indicators' `window`/`levels` parameters were calibrated in Epic 2's backtests
    against DydxSecondSnapshot's 1-second sampling cadence (troll/CLAUDE.md's
    "Signal Architecture: 1s-Based, Not Event-Driven" rule); feeding on every raw
    delta event (which fires far more than once per second) would silently change
    what `window=N` means in wall-clock time and invalidate that calibration.
  - OnlineLogisticTrend: fed from `Bar` via `subscribe_bars`, using INTERNAL bar
    aggregation (Nautilus's own aggregator over the already-subscribed quote/trade
    ticks). EXTERNAL aggregation was not used: `nautilus_trader/adapters/dydx/data.py`'s
    `_subscribe_bars` forwards straight to dYdX's live candles WS channel, which may
    genuinely deliver native bars (unlike backtest, where -EXTERNAL bar types never
    fire against the catalog -- see deferred-work.md's Story 2.3 finding, which is
    backtest-specific) -- but verifying that live requires a real dYdX connection,
    which this implementation session had no credentials/network access to do.
    INTERNAL aggregation is the safe default that is guaranteed correct regardless;
    revisit EXTERNAL as a lower-latency option once someone can verify it live.

Trading logic is a single, deliberately minimal combination: OnlineLogisticTrend's
trend probability crossing a threshold, confirmed by MultiLevelOFI's direction
agreeing -- mirroring the single-signal threshold-cross pattern already established
in example_strategy.py/snapshot_strategy.py, not ofi_strategy.py's full multi-gate
design (cancellation pressure, cumulative delta, mid-layer confirmation), which stays
backtest-only. Microprice, OrderFlowImbalance (top-of-book), and MultiLevelOBI are
computed, fed, and published (`publish_signal`) on every update for operational
visibility and to satisfy AC1's "consumes all of them" -- they do not gate entries,
consistent with indicators.py's own documented honesty that not every indicator needs
to be a trading gate to be a legitimate consumer.

A reversal (e.g. long -> short) is two steps, not one: `_maybe_trade` only ever submits
one `trade_size`-sized market order per call, so flipping a long position first flattens
it (long -> flat) and only re-enters short on the next signal cycle that still agrees --
deliberately simple, not a same-tick double-sized reversal order.
"""

from decimal import Decimal

import pandas as pd

from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy

from ml_signals.indicators import Microprice
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
from ml_signals.indicators import OnlineLogisticTrend
from ml_signals.indicators import OrderFlowImbalance


_BOOK_SNAPSHOT_TIMER = "dummy_strategy_book_snapshot"
# Must stay at 1 second to match the cadence MultiLevelOBI/MultiLevelOFI were calibrated
# against in backtest (see module docstring) -- a value that must never change is not a
# config field (troll/CLAUDE.md's DESIGN-01), so this is a constant, not tunable.
_BOOK_SNAPSHOT_INTERVAL = pd.Timedelta(seconds=1.0)


class DummyStrategyConfig(StrategyConfig, frozen=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    trade_size : Decimal
    bar_spec : str
        Bar type suffix (without instrument prefix) for the trend indicator's bars,
        e.g. "1-MINUTE-LAST-INTERNAL". Must use INTERNAL aggregation unless EXTERNAL
        has been verified to actually deliver bars against a live dYdX connection
        (see module docstring).
    trend_lookback : int
        `OnlineLogisticTrend` lookback period.
    trend_buy_threshold : float
        Enter long when the trend probability exceeds this (confirmed by mlofi_value > 0).
    trend_sell_threshold : float
        Enter short when the trend probability drops below this (confirmed by mlofi_value < 0).
    ofi_levels : int
        Book depth `MultiLevelOFI` aggregates over.
    ofi_window : int
        Number of trailing snapshot-to-snapshot contributions summed into `MultiLevelOFI`.
    obi_levels : int
        Book depth `MultiLevelOBI` aggregates over.
    ofi_confirm_threshold : float
        `MultiLevelOFI` must exceed +/- this value in the trend's direction to confirm entry.
    """

    instrument_id: InstrumentId
    trade_size: Decimal
    bar_spec: str = "1-MINUTE-LAST-INTERNAL"
    trend_lookback: int = 5
    trend_buy_threshold: float = 0.6
    trend_sell_threshold: float = 0.4
    ofi_levels: int = 10
    ofi_window: int = 20
    obi_levels: int = 10
    ofi_confirm_threshold: float = 0.0


class DummyStrategy(Strategy):
    """
    Wires every Epic 2 indicator into a live paper-trading strategy. See module
    docstring for the live-data-sourcing design and the entry/exit decision logic.
    """

    def __init__(self, config: DummyStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.microprice = Microprice()
        self.ofi = OrderFlowImbalance()
        self.obi = MultiLevelOBI(levels=config.obi_levels)
        self.mlofi = MultiLevelOFI(levels=config.ofi_levels, window=config.ofi_window)
        self.trend = OnlineLogisticTrend(lookback=config.trend_lookback)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return

        if not self.config.trend_buy_threshold > self.config.trend_sell_threshold:
            self.log.error(
                f"trend_buy_threshold ({self.config.trend_buy_threshold}) must exceed "
                f"trend_sell_threshold ({self.config.trend_sell_threshold}) -- misconfigured "
                f"thresholds would silently disable entries/exits"
            )
            self.stop()
            return

        if "INTERNAL" not in self.config.bar_spec:
            self.log.error(
                f"bar_spec ({self.config.bar_spec!r}) must use INTERNAL aggregation -- "
                f"EXTERNAL has not been verified to actually deliver bars live (see module "
                f"docstring)"
            )
            self.stop()
            return

        for field_name, value in (
            ("trade_size", self.config.trade_size),
            ("ofi_levels", self.config.ofi_levels),
            ("ofi_window", self.config.ofi_window),
            ("obi_levels", self.config.obi_levels),
            ("trend_lookback", self.config.trend_lookback),
        ):
            if not value > 0:
                self.log.error(f"{field_name} ({value}) must be positive")
                self.stop()
                return

        self.subscribe_quote_ticks(self.config.instrument_id)
        self.subscribe_order_book_deltas(self.config.instrument_id, BookType.L2_MBP)

        bar_type = BarType.from_str(f"{self.config.instrument_id}-{self.config.bar_spec}")
        self.subscribe_bars(bar_type)

        self.clock.set_timer(
            name=_BOOK_SNAPSHOT_TIMER,
            interval=_BOOK_SNAPSHOT_INTERVAL,
            callback=self.on_timer,
        )

        self.log.info(f"DummyStrategy started for {self.config.instrument_id}")

    def on_quote_tick(self, tick: QuoteTick) -> None:
        bid_price = tick.bid_price.as_double()
        bid_size = tick.bid_size.as_double()
        ask_price = tick.ask_price.as_double()
        ask_size = tick.ask_size.as_double()

        self.microprice.update_raw(bid_price, bid_size, ask_price, ask_size)
        self.ofi.update_raw(bid_price, bid_size, ask_price, ask_size)

        if self.microprice.initialized:
            self.publish_signal(
                name="microprice", value=self.microprice.value, ts_event=tick.ts_event
            )
        if self.ofi.initialized:
            self.publish_signal(name="ofi", value=self.ofi.value, ts_event=tick.ts_event)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        # MultiLevelOBI/MultiLevelOFI are sampled on the periodic 1s timer (on_timer),
        # not per-delta -- see module docstring for why this cadence is load-bearing.
        pass

    def on_timer(self, event: TimeEvent) -> None:
        if event.name != _BOOK_SNAPSHOT_TIMER:
            return

        book = self.cache.order_book(self.config.instrument_id)
        if book is None:
            return

        bids = book.bids()
        asks = book.asks()
        if not bids or not asks:
            return

        bid_prices = [level.price.as_double() for level in bids]
        bid_sizes = [level.size() for level in bids]
        ask_prices = [level.price.as_double() for level in asks]
        ask_sizes = [level.size() for level in asks]

        self.obi.update_raw(bid_sizes, ask_sizes)
        self.mlofi.update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)

        if self.obi.initialized:
            self.publish_signal(name="obi", value=self.obi.value, ts_event=event.ts_event)
        if self.mlofi.initialized:
            self.publish_signal(name="mlofi", value=self.mlofi.value, ts_event=event.ts_event)

        self._maybe_trade()

    def on_bar(self, bar: Bar) -> None:
        self.trend.update_raw(bar.close.as_double())
        if self.trend.initialized:
            self.publish_signal(name="trend", value=self.trend.value, ts_event=bar.ts_event)
        self._maybe_trade()

    def _maybe_trade(self) -> None:
        if not (self.trend.initialized and self.mlofi.initialized):
            return

        # An order already in flight hasn't updated portfolio state yet -- without this
        # guard, a signal that stays true across successive 1s timer ticks would submit a
        # duplicate order every tick until the first one fills (established idiom, see
        # nautilus_trader/examples/strategies/orderbook_imbalance.py's check_trigger()).
        if self.cache.orders_inflight(strategy_id=self.id):
            return

        is_flat = self.portfolio.is_flat(self.config.instrument_id)
        is_long = self.portfolio.is_net_long(self.config.instrument_id)
        is_short = self.portfolio.is_net_short(self.config.instrument_id)

        long_signal = (
            self.trend.value > self.config.trend_buy_threshold
            and self.mlofi.value > self.config.ofi_confirm_threshold
        )
        short_signal = (
            self.trend.value < self.config.trend_sell_threshold
            and self.mlofi.value < -self.config.ofi_confirm_threshold
        )

        if is_flat:
            if long_signal:
                self._submit(OrderSide.BUY)
            elif short_signal:
                self._submit(OrderSide.SELL)
        elif is_long and short_signal:
            self._submit(OrderSide.SELL)
        elif is_short and long_signal:
            self._submit(OrderSide.BUY)

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
