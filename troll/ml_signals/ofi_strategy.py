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
OFI moving-average strategy with microstructure filters and trend state.

Signal stack (all must agree to enter):
  1. OFI MA  — rolling mean of `ma_period` non-overlapping OFI batch readings
  2. 5-min cumulative delta  — net buy/sell volume from trade ticks confirms direction
  3. Mid-layer book confirmation  — levels 2-3 imbalance confirms direction
  4. Trend state  — 30m (configurable) EMA cross; counter-trend entries blocked

Exits:
  - MA crosses zero        (exit_on_zero)
  - Trend flips against position  (always — the "closes down" rule)
"""

from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy

from ml_signals.book_features import BookFeatures
from ml_signals.book_features import CancellationTracker
from ml_signals.book_features import compute_features
from ml_signals.indicators import OrderFlowImbalance


class OFIStrategyConfig(StrategyConfig, frozen=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    ofi_window : int
        Number of top-of-book events accumulated per OFI reading.
    ma_period : int
        Number of OFI readings in the moving average (non-overlapping batches).
    buy_threshold / sell_threshold : float
        MA level thresholds for entry.
    trade_size : Decimal
    exit_on_zero : bool
        Close position when MA crosses zero back toward flat.
    min_depth_levels : int
        Minimum visible depth levels required to trade.
    max_cancel_pressure : float
        Reject entry when passive-side cancel pressure exceeds this (0–1).
    min_imbalance_confirm : float | None
        Require aggregate 4-level book imbalance to confirm OFI direction.
    cum_delta_seconds : int
        Rolling window for cumulative delta (seconds). Default 300 = 5 min.
    cum_delta_threshold : float | None
        Require |cumulative delta| to exceed this before entering.
        e.g. 50.0 means net 50 units must have traded in the signal direction.
    mid_layer_confirm : float | None
        Require average imbalance at depth levels 2-3 to confirm direction.
        e.g. 0.55 = levels 2+3 must show >55% bid weight for a long entry.
    trend_bar_type_str : str
        Bar type suffix for trend EMA, without instrument prefix.
        e.g. "30-MINUTE-LAST-EXTERNAL". Must exist in the catalog / data feed.
    trend_ema_fast : int
        Fast EMA period on trend bars.
    trend_ema_slow : int
        Slow EMA period on trend bars. fast > slow = bull = longs only.
    """

    instrument_id: InstrumentId
    warmup_seconds: int = 1800  # seconds of data to consume before trading; 1800 = 30 min
    ofi_window: int = 20
    ma_period: int = 10
    buy_threshold: float = 0.5
    sell_threshold: float = -0.5
    trade_size: Decimal = Decimal("0.01")
    exit_on_zero: bool = True
    # book depth filters
    min_depth_levels: int = 2
    max_cancel_pressure: float = 0.6
    min_imbalance_confirm: float | None = None
    # cumulative delta
    cum_delta_seconds: int = 300
    cum_delta_threshold: float | None = None
    # mid-layer book confirmation (levels 2-3)
    mid_layer_confirm: float | None = None
    # 30m trend state
    trend_bar_type_str: str = "30-MINUTE-LAST-EXTERNAL"
    trend_ema_fast: int = 8
    trend_ema_slow: int = 21


class OFIStrategy(Strategy):
    """
    Trades on a moving average of event-driven OFI readings, gated by
    cumulative delta, mid-layer book confirmation, and a 30m EMA trend state.

    Publishes signals "ofi_raw", "ofi_ma", "cum_delta", "trend_bull".
    """

    def __init__(self, config: OFIStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self._book: OrderBook | None = None
        self._warmup_complete: bool = False
        self._first_ts_ns: int | None = None
        self._ofi = OrderFlowImbalance(window=config.ofi_window)
        self._ofi_history: deque[float] = deque(maxlen=config.ma_period)
        self._prev_ma: float = 0.0
        self._event_count: int = 0
        self._cancel_tracker = CancellationTracker(window=config.ofi_window * 5)
        self._last_features: BookFeatures | None = None
        # 5-min cumulative delta: deque of (ts_ns, signed_size)
        self._cum_delta_events: deque[tuple[int, float]] = deque()
        # 30m trend EMA state
        self._trend_fast = ExponentialMovingAverage(config.trend_ema_fast)
        self._trend_slow = ExponentialMovingAverage(config.trend_ema_slow)
        self._trend_bull: bool | None = None   # None = not enough bars yet

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return

        self._book = OrderBook(self.config.instrument_id, BookType.L2_MBP)
        self.subscribe_order_book_deltas(self.config.instrument_id)
        self.subscribe_trade_ticks(self.config.instrument_id)

        trend_bar_type = BarType.from_str(
            f"{self.config.instrument_id}-{self.config.trend_bar_type_str}"
        )
        self.subscribe_bars(trend_bar_type)
        self.log.info(
            f"OFIStrategy started — ofi_window={self.config.ofi_window} "
            f"ma_period={self.config.ma_period} "
            f"trend={self.config.trend_bar_type_str} "
            f"ema={self.config.trend_ema_fast}/{self.config.trend_ema_slow}"
        )

    def _check_warmup(self, ts_ns: int) -> bool:
        if self._warmup_complete:
            return True
        if self._first_ts_ns is None:
            self._first_ts_ns = ts_ns
        if ts_ns - self._first_ts_ns >= self.config.warmup_seconds * 1_000_000_000:
            self._warmup_complete = True
            self.log.info(f"Warmup complete after {self.config.warmup_seconds}s")
        return self._warmup_complete

    # ------------------------------------------------------------------
    # Trade ticks — feed cumulative delta
    # ------------------------------------------------------------------

    def on_trade_tick(self, tick: TradeTick) -> None:
        signed = tick.size.as_double()
        if tick.aggressor_side == AggressorSide.SELLER:
            signed = -signed
        self._cum_delta_events.append((tick.ts_event, signed))
        # Prune entries outside the rolling window
        cutoff_ns = tick.ts_event - self.config.cum_delta_seconds * 1_000_000_000
        while self._cum_delta_events and self._cum_delta_events[0][0] < cutoff_ns:
            self._cum_delta_events.popleft()
        self.publish_signal("cum_delta", self._cum_delta(), tick.ts_event)

    def _cum_delta(self) -> float:
        return sum(sz for _, sz in self._cum_delta_events)

    # ------------------------------------------------------------------
    # Trend bars — EMA cross state
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        close = bar.close.as_double()
        self._trend_fast.update_raw(close)
        self._trend_slow.update_raw(close)
        if not (self._trend_fast.initialized and self._trend_slow.initialized):
            return

        prev_bull = self._trend_bull
        self._trend_bull = self._trend_fast.value > self._trend_slow.value
        self.publish_signal("trend_bull", float((self._trend_bull), bar.ts_event)

        # Trend flipped — close any position that's now counter-trend
        if prev_bull is not None and self._trend_bull != prev_bull and self._warmup_complete:
            self.log.info(f"Trend flipped → {'BULL' if self._trend_bull else 'BEAR'}, closing counter-trend positions")
            if self._trend_bull and self.portfolio.is_net_short(self.config.instrument_id):
                self._submit(OrderSide.BUY)
            elif not self._trend_bull and self.portfolio.is_net_long(self.config.instrument_id):
                self._submit(OrderSide.SELL)

    # ------------------------------------------------------------------
    # Order book deltas — OFI + book features
    # ------------------------------------------------------------------

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        assert self._book is not None

        for delta in deltas.deltas:
            best_bid = self._book.best_bid_price()
            best_ask = self._book.best_ask_price()
            self._cancel_tracker.update(
                delta,
                best_bid.as_double() if best_bid else None,
                best_ask.as_double() if best_ask else None,
            )
            self._book.apply_delta(delta)

        bid_price = self._book.best_bid_price()
        ask_price = self._book.best_ask_price()
        if bid_price is None or ask_price is None:
            return

        self._ofi.update_raw(
            bid_price.as_double(),
            self._book.best_bid_size().as_double(),
            ask_price.as_double(),
            self._book.best_ask_size().as_double(),
        )
        self._last_features = compute_features(self._book, self._cancel_tracker)

        if not self._ofi.initialized:
            return

        self._event_count += 1
        self.publish_signal("ofi_raw", self._ofi.value, deltas.ts_event)

        if self._event_count % self.config.ofi_window != 0:
            return

        self._ofi_history.append(self._ofi.value)
        if len(self._ofi_history) < self.config.ma_period:
            return

        ma = sum(self._ofi_history) / len(self._ofi_history)
        self.publish_signal("ofi_ma", ma, deltas.ts_event)
        if self._check_warmup(deltas.ts_event):
            self._evaluate_signal(ma, deltas.ts_event)
        self._prev_ma = ma

    # ------------------------------------------------------------------
    # Signal evaluation + filters
    # ------------------------------------------------------------------

    def _all_filters_pass(self, direction: OrderSide) -> bool:
        f = self._last_features

        # --- Book depth ---
        if f is None or f.depth.levels < self.config.min_depth_levels:
            self.log.debug("Filtered: insufficient depth")
            return False

        # --- Cancel pressure at best ---
        pressure = f.cancel.bid_pressure if direction == OrderSide.BUY else f.cancel.ask_pressure
        if pressure > self.config.max_cancel_pressure:
            self.log.debug(f"Filtered: cancel pressure {pressure:.2f}")
            return False

        # --- Aggregate book imbalance ---
        if self.config.min_imbalance_confirm is not None:
            imb = f.imbalance.aggregate
            thr = self.config.min_imbalance_confirm
            if direction == OrderSide.BUY and imb < thr:
                self.log.debug(f"Filtered: agg imbalance {imb:.2f} < {thr}")
                return False
            if direction == OrderSide.SELL and imb > (1.0 - thr):
                self.log.debug(f"Filtered: agg imbalance {imb:.2f} > {1-thr:.2f}")
                return False

        # --- Mid-layer (levels 2-3) confirmation ---
        if self.config.mid_layer_confirm is not None and f.depth.levels >= 3:
            mid_imb = (f.imbalance.per_level[1] + f.imbalance.per_level[2]) / 2
            thr = self.config.mid_layer_confirm
            if direction == OrderSide.BUY and mid_imb < thr:
                self.log.debug(f"Filtered: mid-layer imbalance {mid_imb:.2f} < {thr}")
                return False
            if direction == OrderSide.SELL and mid_imb > (1.0 - thr):
                self.log.debug(f"Filtered: mid-layer imbalance {mid_imb:.2f} > {1-thr:.2f}")
                return False

        # --- 5-min cumulative delta ---
        if self.config.cum_delta_threshold is not None:
            cd = self._cum_delta()
            thr = self.config.cum_delta_threshold
            if direction == OrderSide.BUY and cd < thr:
                self.log.debug(f"Filtered: cum delta {cd:.1f} < {thr}")
                return False
            if direction == OrderSide.SELL and cd > -thr:
                self.log.debug(f"Filtered: cum delta {cd:.1f} > {-thr:.1f}")
                return False

        # --- 30m trend state — blocks counter-trend entries entirely ---
        if self._trend_bull is not None:
            if direction == OrderSide.BUY and not self._trend_bull:
                self.log.debug("Filtered: bearish trend, no longs")
                return False
            if direction == OrderSide.SELL and self._trend_bull:
                self.log.debug("Filtered: bullish trend, no shorts")
                return False

        return True

    def _evaluate_signal(self, ma: float, ts_event: int) -> None:
        is_flat  = self.portfolio.is_flat(self.config.instrument_id)
        is_long  = self.portfolio.is_net_long(self.config.instrument_id)
        is_short = self.portfolio.is_net_short(self.config.instrument_id)

        if is_flat:
            if ma > self.config.buy_threshold and self._all_filters_pass(OrderSide.BUY):
                self._submit(OrderSide.BUY)
            elif ma < self.config.sell_threshold and self._all_filters_pass(OrderSide.SELL):
                self._submit(OrderSide.SELL)
        elif self.config.exit_on_zero:
            if is_long and ma <= 0.0 < self._prev_ma:
                self._submit(OrderSide.SELL)
            elif is_short and ma >= 0.0 > self._prev_ma:
                self._submit(OrderSide.BUY)

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)

    def on_reset(self) -> None:
        self._warmup_complete = False
        self._first_ts_ns = None
        self._ofi._reset()
        self._ofi_history.clear()
        self._prev_ma = 0.0
        self._event_count = 0
        self._cancel_tracker = CancellationTracker(window=self.config.ofi_window * 5)
        self._last_features = None
        self._cum_delta_events.clear()
        self._trend_fast = ExponentialMovingAverage(self.config.trend_ema_fast)
        self._trend_slow = ExponentialMovingAverage(self.config.trend_ema_slow)
        self._trend_bull = None
