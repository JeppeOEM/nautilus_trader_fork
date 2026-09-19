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
Multi-level OFI strategy on 1s DydxSecondSnapshot data (the only book data in the catalog).

Entry needs all of:
  1. OFI z-score beyond +/- `ofi_threshold` (MultiLevelOFI, USD-notional so it is comparable
     across symbols; z-scored so the threshold is scale-free)
  2. Aggregate top-N size imbalance agrees (optional)
  3. Rolling cumulative delta (buy_volume - sell_volume) agrees (optional)
  4. Trend agrees: EMA fast/slow on 1-minute mid closes; counter-trend entries are blocked

Exits: OFI crosses zero back (`exit_on_zero`), or trend flips against the position.
"""

from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.indicators import MultiLevelOFI

_NS_PER_S = 1_000_000_000
_MINUTE_NS = 60 * _NS_PER_S
# A hole in the 1s feed (collector restart / WS resubscribe) makes the next OFI delta compare
# against a stale book, so treat it as a fresh start rather than a huge fake imbalance.
_MAX_GAP_NS = 5 * _NS_PER_S


class OFIStrategyConfig(StrategyConfig, frozen=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    warmup_seconds : int
        Data consumed before trading starts (fills the z-score and EMA windows).
    ofi_levels : int
        Book levels MultiLevelOFI sums over.
    ofi_window : int
        Trailing snapshots summed into one OFI reading.
    ofi_zscore_window : int
        Readings used to z-score OFI. Must be >= 2.
    ofi_threshold : float
        Enter long above +threshold, short below -threshold (z-score units).
    exit_on_zero : bool
        Close when OFI crosses zero back toward flat.
    min_depth_levels : int
        Minimum book levels required on each side to trade.
    min_imbalance_confirm : float | None
        Bid share of top-`ofi_levels` size required for a long (and ask share for a short).
        e.g. 0.55.
    cum_delta_seconds / cum_delta_threshold : int / float | None
        Require net buy-minus-sell volume over the window to exceed the threshold in the
        trade direction (base-asset units).
    trend_ema_fast / trend_ema_slow : int
        EMA periods in minutes. fast > slow = bull = longs only.
    """

    instrument_id: InstrumentId
    trade_size: Decimal = Decimal("0.01")
    warmup_seconds: int = 1800
    ofi_levels: int = 10
    ofi_window: int = 20
    ofi_zscore_window: int = 300
    ofi_threshold: float = 1.5
    exit_on_zero: bool = True
    min_depth_levels: int = 2
    min_imbalance_confirm: float | None = None
    cum_delta_seconds: int = 300
    cum_delta_threshold: float | None = None
    trend_ema_fast: int = 8
    trend_ema_slow: int = 21


class OFIStrategy(Strategy):
    """Trades an OFI z-score, gated by book imbalance, cumulative delta and a minute-EMA trend."""

    def __init__(self, config: OFIStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self._first_ts: int | None = None
        self._last_ts: int | None = None
        self._prev_ofi = 0.0
        self._cum_delta_events: deque[tuple[int, float]] = deque()
        self._minute: int | None = None
        self._minute_mid = 0.0
        self._trend_bull: bool | None = None
        self._new_indicators()

    def _new_indicators(self) -> None:
        c = self.config
        self._ofi = MultiLevelOFI(
            levels=c.ofi_levels,
            window=c.ofi_window,
            usd_notional=True,
            zscore_window=c.ofi_zscore_window,
        )
        self._ema_fast = ExponentialMovingAverage(c.trend_ema_fast)
        self._ema_slow = ExponentialMovingAverage(c.trend_ema_slow)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return
        self.subscribe_data(DataType(DydxSecondSnapshot), instrument_id=self.config.instrument_id)

    def on_data(self, data: Data) -> None:
        if not isinstance(data, DydxSecondSnapshot) or not data.bid_prices or not data.ask_prices:
            return
        ts = data.ts_event
        if self._last_ts is not None and ts - self._last_ts > _MAX_GAP_NS:
            self._ofi.clear_prev_state()
        self._last_ts = ts
        self._first_ts = self._first_ts or ts

        self._ofi.update_raw(data.bid_prices, data.bid_sizes, data.ask_prices, data.ask_sizes)
        self._track_cum_delta(ts, data.buy_volume - data.sell_volume)
        self._track_trend(ts, (data.bid_prices[0] + data.ask_prices[0]) / 2)

        ofi = self._ofi.value
        if self._ofi.initialized and ts - self._first_ts >= self.config.warmup_seconds * _NS_PER_S:
            self._evaluate(ofi, data)
        self._prev_ofi = ofi

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _track_cum_delta(self, ts: int, delta: float) -> None:
        self._cum_delta_events.append((ts, delta))
        cutoff = ts - self.config.cum_delta_seconds * _NS_PER_S
        while self._cum_delta_events[0][0] < cutoff:
            self._cum_delta_events.popleft()

    def _track_trend(self, ts: int, mid: float) -> None:
        minute = ts // _MINUTE_NS
        if self._minute is not None and minute != self._minute:
            self._ema_fast.update_raw(self._minute_mid)
            self._ema_slow.update_raw(self._minute_mid)
            if self._ema_slow.initialized:
                self._trend_bull = self._ema_fast.value > self._ema_slow.value
        self._minute, self._minute_mid = minute, mid

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------

    def _filters_pass(self, side: OrderSide, data: DydxSecondSnapshot) -> bool:
        c = self.config
        if min(len(data.bid_prices), len(data.ask_prices)) < c.min_depth_levels:
            return False
        buy = side == OrderSide.BUY

        if c.min_imbalance_confirm is not None:
            bids = sum(data.bid_sizes[: c.ofi_levels])
            asks = sum(data.ask_sizes[: c.ofi_levels])
            bid_share = bids / (bids + asks) if bids + asks else 0.5
            if (bid_share if buy else 1.0 - bid_share) < c.min_imbalance_confirm:
                return False

        if c.cum_delta_threshold is not None:
            cum = sum(d for _, d in self._cum_delta_events)
            if (cum if buy else -cum) < c.cum_delta_threshold:
                return False

        # Blocks counter-trend entries; None (EMAs not warm yet) does not block.
        return self._trend_bull is None or self._trend_bull == buy

    def _evaluate(self, ofi: float, data: DydxSecondSnapshot) -> None:
        iid = self.config.instrument_id
        t = self.config.ofi_threshold
        if self.portfolio.is_flat(iid):
            if ofi > t and self._filters_pass(OrderSide.BUY, data):
                self._submit(OrderSide.BUY)
            elif ofi < -t and self._filters_pass(OrderSide.SELL, data):
                self._submit(OrderSide.SELL)
            return

        long = self.portfolio.is_net_long(iid)
        trend_flipped = self._trend_bull is not None and self._trend_bull != long
        zero_cross = self.config.exit_on_zero and (
            (long and ofi <= 0.0 < self._prev_ofi) or (not long and ofi >= 0.0 > self._prev_ofi)
        )
        if trend_flipped or zero_cross:
            self._submit(OrderSide.SELL if long else OrderSide.BUY)

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None
        self.submit_order(
            self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(self.config.trade_size),
            ),
        )

    def on_reset(self) -> None:
        self._first_ts = self._last_ts = self._minute = self._trend_bull = None
        self._prev_ofi = 0.0
        self._cum_delta_events.clear()
        self._new_indicators()
