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

**Forced flow** (Story 33.13, both off by default; on, they need a Bybit LINEAR id and subscribe to
its `Liquidation` rows through `LIQUIDATION_CLIENT_ID`, the backtest's `seconds_liquidations` kind):

- `forced_flow_filter`: the cumulative delta (3.) is organic -- each snapshot pushes
  `kernel.indicators.organic_delta_units` of its exact `buy_volume_units`/`sell_volume_units` and
  the long/short sizes of the liquidations received so far whose venue second (`ts_event // 1 s`)
  is the snapshot's own, rescaled exactly to the snapshot's `size_precision`
  (`kernel.second_snapshot.units_of`), decoded once. A size that
  precision cannot hold is recorded at `FORCED_FLOW_SITE` and that second's delta is NaN, which
  blocks a cum-delta-gated entry while it is in the window, never a value with the row dropped
  (DATA-07).
- `liquidation_cascade_mode` (`follow` | `fade` | `off`): `kernel.indicators.LiquidationCascade`
  fed as `LiquidationCascadeStrategy` feeds it (`definition_units`: the notional at the
  definition's precisions, at the row's `ts_init`) and advanced at every snapshot's `ts_init`, its
  updates folded into `cascade_rules.next_phase`; an entry is submitted only when
  `cascade_rules.cascade_allows` passes it. OFI still decides every entry; the mode only gates.

**Attribution** (audit D-220): a liquidation is netted in the snapshot of its own venue second,
research's `organic_delta` rule (the capture's for trades: a second `S` holds the trades with
`ts_event` in `[S, S + 1 s)` and is stamped `S + 0.5 s`), and only once received (`ts_init`
order, what a live strategy knows): one received before the previous second's snapshot is held
for its own. A liquidation whose second has no usable snapshot when it is settled -- the
snapshot is one-sided, the second fell in a feed gap or before the first snapshot, or the row
arrived after its second's snapshot -- is not netted anywhere else but discarded, counted in
`unattributed_liquidations` and logged at WARNING (the research side's `unattributed`). Known limit:
a liquidation's stamp and its forced trade's may fall in adjacent seconds, research's
`organic_delta` limit (the same rule, the same upgrade path).

Known limit: the modes are research-only: no bot runs `OFIStrategy` with them, and live delivery
order is not checked (a row received before the detector's clock is placed at it, as a backtest
never delivers one); upgrade path: `LiquidationCascadeStrategy`'s stale-row rule, before a bot
wiring.
"""

import math
from collections import deque
from decimal import Decimal

from kernel.indicators import OFI_GAP_NS
from kernel.indicators import LiquidationCascade
from kernel.indicators import MultiLevelOFI
from kernel.indicators import organic_delta_units
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SnapshotEncodingError
from kernel.second_snapshot import unit_float
from kernel.second_snapshot import units_of
from observability import error_ledger

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy
from research.strategies.cascade_rules import MODE_OFF
from research.strategies.cascade_rules import MODES
from research.strategies.cascade_rules import QUIET
from research.strategies.cascade_rules import CascadeView
from research.strategies.cascade_rules import cascade_allows
from research.strategies.cascade_rules import next_phase
from research.strategies.liquidation_cascade_strategy import definition_units


_NS_PER_S = 1_000_000_000
_MINUTE_NS = 60 * _NS_PER_S
# `liquidation_cascade_mode`'s values: off, or one of the cascade rules' modes.
CASCADE_MODES = (MODE_OFF, *MODES)
# The error-ledger site of a liquidation size the snapshot's size precision cannot hold exactly.
FORCED_FLOW_SITE = "research.ofi.unscalable_forced_flow"


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
    forced_flow_filter : bool
        Take the forced (liquidation) flow out of the cumulative delta (Story 33.13).
    liquidation_cascade_mode : str
        `off`, `follow` or `fade`: gate entries on the liquidation cascade's phase.
    cascade_window_s / cascade_baseline_s / cascade_intensity_threshold / cascade_decay_ratio
        The `LiquidationCascade` detector's parameters (`LiquidationCascadeStrategy`'s defaults);
        `cascade_window_s` is also the fade window's length.
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
    forced_flow_filter: bool = False
    liquidation_cascade_mode: str = MODE_OFF
    cascade_window_s: int = 30
    cascade_baseline_s: int = 3_600
    cascade_intensity_threshold: float = 3.0
    cascade_decay_ratio: float = 0.5


def _check_forced_flow(config: OFIStrategyConfig) -> None:
    """Refuse an unknown cascade mode, and either forced-flow option on an id without the feed."""
    mode = config.liquidation_cascade_mode
    if mode not in CASCADE_MODES:
        raise ValueError(f"liquidation_cascade_mode must be one of {CASCADE_MODES}, not {mode!r}")
    if (config.forced_flow_filter or mode != MODE_OFF) and not has_liquidation_feed(
        str(config.instrument_id)
    ):
        raise ValueError(
            f"forced_flow_filter and liquidation_cascade_mode need a liquidation feed (Bybit "
            f"LINEAR), {config.instrument_id} has none"
        )


class OFIStrategy(Strategy):
    """
    Trades an OFI z-score, gated by book imbalance, cumulative delta and a minute-EMA trend, and
    optionally by the forced flow (the module docstring).

    Invariant: with `forced_flow_filter=False` and `liquidation_cascade_mode="off"` it subscribes
    to nothing but the snapshots and decides exactly as before Story 33.13. A bad forced-flow
    config raises `ValueError` here, before any node runs it.
    """

    def __init__(self, config: OFIStrategyConfig) -> None:
        super().__init__(config)
        _check_forced_flow(config)
        self.instrument: Instrument | None = None
        # Liquidation rows whose notional the definition's precisions cannot hold (not fed).
        self.unscalable_rows = 0
        # The received liquidations not yet settled, per venue second (`ts_event // 1 s`):
        # [long `Quantity.raw` sum, short sum, rows]. Only seconds after the latest snapshot's
        # remain once it is settled, so this holds about one second of rows.
        self._pending: dict[int, list[int]] = {}
        # This instrument's liquidation rows received, and those discarded unattributed: their
        # second had no usable snapshot (the module docstring's attribution).
        self.delivered_liquidations = 0
        self.unattributed_liquidations = 0
        self._phase = QUIET
        self._cascade = (
            None
            if config.liquidation_cascade_mode == MODE_OFF
            else LiquidationCascade(
                config.cascade_window_s,
                config.cascade_baseline_s,
                config.cascade_intensity_threshold,
                config.cascade_decay_ratio,
            )
        )
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
        if self.config.forced_flow_filter or self._cascade is not None:
            self.subscribe_data(
                DataType(Liquidation),
                client_id=ClientId(LIQUIDATION_CLIENT_ID),
                instrument_id=self.config.instrument_id,
            )

    def on_data(self, data: Data) -> None:
        if isinstance(data, Liquidation):
            self._on_liquidation(data)
            return
        if isinstance(data, DydxSecondSnapshot):
            self._on_snapshot(data)

    def _on_snapshot(self, data: DydxSecondSnapshot) -> None:
        # Every snapshot closes the forced-flow accumulation and moves the detector (and with it
        # the cascade phase, before any entry is gated), a one-sided one included.
        self._advance_cascade(data.ts_init)
        ts = data.ts_event
        one_sided = not data.bid_prices or not data.ask_prices
        # A hole in the 1s feed (collector restart / WS resubscribe) makes the next OFI delta
        # compare against a stale book: clear it first, at the one platform-wide threshold
        # (`kernel.indicators.OFI_GAP_NS`, 3 s since Story 31.3 -- this strategy used 5 s).
        after_gap = self._last_ts is not None and ts - self._last_ts > OFI_GAP_NS
        delta = self._second_delta(data, usable=not one_sided)
        if one_sided:
            return
        if after_gap:
            self._ofi.clear_prev_state()
        self._last_ts = ts
        self._first_ts = self._first_ts or ts

        self._ofi.update_raw(data.bid_prices, data.bid_sizes, data.ask_prices, data.ask_sizes)
        self._track_cum_delta(ts, delta)
        self._track_trend(ts, (data.bid_prices[0] + data.ask_prices[0]) / 2)

        # The post-gap row only re-baselines MultiLevelOFI and leaves its value at the pre-gap
        # reading, so no OFI-driven decision (entry, zero-cross exit) is taken on it, as
        # `microstructure.ofi_readings` skips it; the trend-flip exit does not read OFI and
        # `_track_trend` is current, so it still fires there. `_prev_ofi` deliberately stays the
        # last real pre-gap reading (assigning the unchanged value is a no-op): the first
        # post-gap reading's zero-cross is judged against it, consistent with MultiLevelOFI
        # keeping its window and z-score history across the gap.
        ofi = self._ofi.value
        if self._ofi.initialized and self._warmed_up(ts):
            self._evaluate(ofi, data, fresh_ofi=not after_gap)
        self._prev_ofi = ofi

    def _warmed_up(self, ts: int) -> bool:
        assert self._first_ts is not None
        return ts - self._first_ts >= self.config.warmup_seconds * _NS_PER_S

    # ------------------------------------------------------------------
    # Forced flow (Story 33.13)
    # ------------------------------------------------------------------

    def _on_liquidation(self, row: Liquidation) -> None:
        if row.instrument_id != self.config.instrument_id or self.instrument is None:
            return
        self.delivered_liquidations += 1
        if self.config.forced_flow_filter:
            held = self._pending.setdefault(row.ts_event // _NS_PER_S, [0, 0, 0])
            held[0 if row.side == LiquidatedSide.LONG else 1] += row.size.raw
            held[2] += 1
        if self._cascade is None:
            return
        units = definition_units(row, self.instrument, self.unscalable_rows)
        if units is None:
            self.unscalable_rows += 1
            return
        self._cascade.update_liquidation(row.side, units, row.ts_init)
        self._update_phase()

    def _advance_cascade(self, ts_init: int) -> None:
        if self._cascade is not None:
            self._cascade.advance(ts_init)
            self._update_phase()

    def _update_phase(self) -> None:
        cascade = self._cascade
        assert cascade is not None  # only called with a detector
        assert cascade.clock_ns is not None  # just updated
        view = CascadeView(
            episode_start_ns=cascade.episode_start_ns,
            episode_ended=cascade.episode_ended,
            episode_direction=cascade.episode_direction,
            rising=cascade.rising,
            spent=cascade.spent,
            total_rate=cascade.rate_long + cascade.rate_short,
            baseline=cascade.baseline,
        )
        self._phase = next_phase(self._phase, view, cascade.clock_ns, self.config.cascade_window_s)

    def _take_forced(self, data: DydxSecondSnapshot, usable: bool) -> tuple[int, int]:
        """
        Settle the held liquidations up to the snapshot's second and return the (long, short)
        `Quantity.raw` sums of its own second ((0, 0) when `usable` is False: it is one-sided).
        Every other settled row -- an earlier second's, which has no snapshot left to take it, or
        this second's when unusable -- is discarded, counted in `unattributed_liquidations` and
        logged once at WARNING, never netted against a second whose volume does not hold its
        trade. A later second's rows stay held for their own snapshot.
        """
        second = data.ts_event // _NS_PER_S
        own = self._pending.pop(second, None) if usable else None
        settled = [s for s in self._pending if s <= second]
        rows = sum(self._pending.pop(s)[2] for s in settled)
        if rows:
            self.unattributed_liquidations += rows
            self.log.warning(
                f"{rows} liquidation(s) settled at the snapshot of {data.ts_event} without a "
                f"usable snapshot of their own second not netted: unattributed "
                f"({self.unattributed_liquidations} so far)"
            )
        return (0, 0) if own is None else (own[0], own[1])

    def _second_delta(self, data: DydxSecondSnapshot, usable: bool) -> float:
        """
        Return the snapshot's delta: `buy_volume - sell_volume` without the filter, else its
        organic delta (the module docstring) over the liquidations `_take_forced` attributes to
        it, NaN when a liquidation size is not exact at the snapshot's precision. With the
        filter, the held rows are settled either way.
        """
        if not self.config.forced_flow_filter:
            return data.buy_volume - data.sell_volume
        long_raw, short_raw = self._take_forced(data, usable)
        try:
            liq_long = units_of(long_raw, data.size_precision)
            liq_short = units_of(short_raw, data.size_precision)
        except SnapshotEncodingError as exc:
            error_ledger.record(
                FORCED_FLOW_SITE,
                f"{data.instrument_id} forced flow at {data.ts_event} not exact at size "
                f"precision {data.size_precision}: the second's delta is NaN",
                exc,
            )
            return math.nan
        organic = organic_delta_units(
            data.buy_volume_units, data.sell_volume_units, liq_long, liq_short
        )
        return unit_float(organic, data.size_precision)

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
            # `not >=`, so an unknown (NaN) organic delta in the window blocks the entry.
            if not (cum if buy else -cum) >= c.cum_delta_threshold:
                return False

        # Blocks counter-trend entries; None (EMAs not warm yet) does not block.
        return self._trend_bull is None or self._trend_bull == buy

    def _evaluate(self, ofi: float, data: DydxSecondSnapshot, fresh_ofi: bool) -> None:
        """`fresh_ofi` is False on a post-gap baseline row, where `ofi` is the stale reading."""
        iid = self.config.instrument_id
        if self.portfolio.is_flat(iid):
            if fresh_ofi:
                self._enter(ofi, data)
            return

        long = self.portfolio.is_net_long(iid)
        trend_flipped = self._trend_bull is not None and self._trend_bull != long
        zero_cross = (
            fresh_ofi
            and self.config.exit_on_zero
            and (
                (long and ofi <= 0.0 < self._prev_ofi) or (not long and ofi >= 0.0 > self._prev_ofi)
            )
        )
        if trend_flipped or zero_cross:
            self._submit(OrderSide.SELL if long else OrderSide.BUY)

    def _enter(self, ofi: float, data: DydxSecondSnapshot) -> None:
        side = self._entry_side(ofi, data)
        if side is not None and cascade_allows(
            self.config.liquidation_cascade_mode, side, self._phase
        ):
            self._submit(side)

    def _entry_side(self, ofi: float, data: DydxSecondSnapshot) -> OrderSide | None:
        t = self.config.ofi_threshold
        if ofi > t and self._filters_pass(OrderSide.BUY, data):
            return OrderSide.BUY
        if ofi < -t and self._filters_pass(OrderSide.SELL, data):
            return OrderSide.SELL
        return None

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
        self.unscalable_rows = 0
        self._pending = {}
        self.delivered_liquidations = 0
        self.unattributed_liquidations = 0
        self._phase = QUIET
        if self._cascade is not None:
            self._cascade.reset()
