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
built by the capture context's own stateful aggregation loop, which this module never
imports per AD-4):

  - Microprice, OrderFlowImbalance (top-of-book): fed from `QuoteTick` via
    `subscribe_quote_ticks`, matching each indicator's own `handle_quote_tick`.
  - MultiLevelOBI, MultiLevelOFI (top-N levels): fed from a periodic 1-second clock
    timer that snapshots `self.cache.order_book(...)` into bid/ask price/size lists.
    This is deliberately NOT fed on every `on_order_book_deltas` callback -- both
    indicators' `window`/`levels` parameters were calibrated in Epic 2's backtests
    against DydxSecondSnapshot's 1-second sampling cadence (platform/CLAUDE.md's
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
in indicator_signal_strategy.py/snapshot_strategy.py, not ofi_strategy.py's full multi-gate
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

Bracket exits (Story 29.6, paper `BotConfig` only): with `take_profit_bps` and/or
`stop_loss_bps` set, a flat entry is one `OrderList` -- the market entry plus a reduce-only
LIMIT take-profit and/or STOP_MARKET stop-loss, priced from the last quote's mid (see
`bots.strategies.exits`). The strategy's own resting orders are cancelled one cycle before a
reversal's flattening order is sent (so a stop cannot fill while the flatten is in flight), and
whenever the bot is flat (the backstop that no reduce-only order outlives its position; the
one-updates-the-other handling already cancels the sibling of a filled leg). Once a reversal
has cancelled the exits its flatten is sent on the next cycle even if the signal has faded, and
a flat bot's legs whose entry is still working (accepted, fill not yet applied) are left alone. That
flatten is one reduce-only market order sized to the open position, re-sent for any remainder
until flat, so a partial fill never flips the bot into an unprotected position. Both legs are
emulated by the node's `OrderEmulator` (see `bots.strategies.exits` for why, and its Known
limit). `on_stop` cancels them too, so a stopped bot's position honestly shows no stop.
Known limit: a restart does not re-arm the exits of a position it inherits; upgrade path: on
start, place the protective legs for an open position that has none. With both keys unset the
trading logic is unchanged; only `on_stop`'s cancel of any resting order applies to every bot.

Signal log (Story 31.9, opt-in): with `signal_log_path` set, every decision cycle (each 1 s book
timer event and each bar) appends one JSON line through `bots.strategies.signal_log` -- the inputs
exactly as the indicators were fed, every indicator value, the `signal_of` decision and the side
submitted -- and the 1 s timer is started at the very nanosecond the `start` record carries, so a
catalog replay (`bots.signal_replay`) fires its cycles on the same timestamps. Unset (the default),
nothing is written and the strategy behaves exactly as without it. Known limit: see
`bots.strategies.signal_log` (one file per bot growing for the run; upgrade path hourly rotation).

Known limit (audit D-82, decision deferred to the operator by Story 31.9): the book this strategy
reads is ungated -- no stale-book, crossed-book or gap check before `MultiLevelOBI`/`MultiLevelOFI`
are fed -- and `MultiLevelOFI` is never `clear_prev_state()`-ed across a gap longer than
`kernel.indicators.OFI_GAP_NS`, unlike `SnapshotStrategy`, `OFIStrategy` and the ranking engine,
so a contribution can span a gap and a stale or crossed book is traded on. Measured divergence
(Story 31.9, audit D-129, `verification.bot_parity` over the verify fleet's 66.5 min against its
catalog replay): 0 `gap` cycles and 0 decision disagreements on all 5 bots, so none observed in
that window (every decision was `none`/`not_ready`). The trading behaviour is left unchanged until
the operator decides (`docs/DEPLOY_CHECKLIST.md`, deferred operator actions "31-9").
Upgrade path: skip (and log) a stale, crossed or gapped book, and `clear_prev_state()` the OFI when
consecutive fed books are more than `OFI_GAP_NS` apart.
"""

from decimal import Decimal

import pandas as pd
from kernel.indicators import Microprice
from kernel.indicators import MultiLevelOBI
from kernel.indicators import MultiLevelOFI
from kernel.indicators import OnlineLogisticTrend
from kernel.indicators import OrderFlowImbalance

from bots.domain.config import MAX_EXIT_BPS
from bots.strategies.exits import entry_with_exits
from bots.strategies.exits import exit_prices
from bots.strategies.exits import make_price
from bots.strategies.signal_log import SIGNAL_LONG
from bots.strategies.signal_log import SIGNAL_SHORT
from bots.strategies.signal_log import SKIP_NO_BOOK
from bots.strategies.signal_log import SKIP_ONE_SIDED
from bots.strategies.signal_log import SignalLog
from bots.strategies.signal_log import signal_of
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


_BOOK_SNAPSHOT_TIMER = "dummy_strategy_book_snapshot"
# Must stay at 1 second to match the cadence MultiLevelOBI/MultiLevelOFI were calibrated
# against in backtest (see module docstring) -- a value that must never change is not a
# config field (platform/CLAUDE.md's DESIGN-01), so this is a constant, not tunable. A plain
# number, turned into a Timedelta in on_start: the bots context builds no object at import time.
_BOOK_SNAPSHOT_INTERVAL_SECONDS = 1.0


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
    take_profit_bps : int, optional
        When set, every entry rests a reduce-only LIMIT take-profit this many basis points
        beyond the entry-time mid.
    stop_loss_bps : int, optional
        When set, every entry rests a reduce-only STOP_MARKET stop-loss this many basis points
        against the entry-time mid.
    signal_log_path : str, optional
        When set, one JSON line per decision cycle is appended to this file
        (`bots.strategies.signal_log`); None (the default) writes nothing.
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
    take_profit_bps: int | None = None
    stop_loss_bps: int | None = None
    signal_log_path: str | None = None


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
        # ts_event (ns) of the last QuoteTick received -- read externally by
        # bots.application.supervise's heartbeat loop as a proxy for "is this bot's WS feed
        # alive" (OBS-01: 30s+ silence on a live instrument is a pipeline failure,
        # same doctrine the capture watchdog already applies). Deliberately a
        # plain public attribute set from a market-data callback (subscriptions stay
        # active regardless of Strategy.is_running), not a Strategy-internal clock
        # timer -- see bots.application.supervise's module docstring for why a Strategy-internal
        # timer is the wrong home for anything that must keep working while stopped.
        self.last_data_ns: int = 0
        # The side of a reversal whose exits were cancelled, flattened on a later cycle whether
        # or not the signal still holds, and kept until flat so a partly filled flatten is
        # finished: a position whose exits are gone is never left holding.
        self._reversal_side: OrderSide | None = None
        # The opt-in signal log (None when off) and the side submitted in the current cycle.
        self._signal_log: SignalLog | None = None
        self._cycle_action: OrderSide | None = None

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

        if not self._exits_valid():
            self.stop()
            return

        self.subscribe_quote_ticks(self.config.instrument_id)
        self.subscribe_order_book_deltas(self.config.instrument_id, BookType.L2_MBP)

        bar_type = BarType.from_str(f"{self.config.instrument_id}-{self.config.bar_spec}")
        self.subscribe_bars(bar_type)

        self.clock.set_timer(
            name=_BOOK_SNAPSHOT_TIMER,
            interval=pd.Timedelta(seconds=_BOOK_SNAPSHOT_INTERVAL_SECONDS),
            start_time=self._open_signal_log(),
            callback=self.on_timer,
        )

        self.log.info(f"DummyStrategy started for {self.config.instrument_id}")

    def _exits_valid(self) -> bool:
        # The same rule `BotConfig` enforces at load, repeated for a config built directly.
        for field_name, bps in (
            ("take_profit_bps", self.config.take_profit_bps),
            ("stop_loss_bps", self.config.stop_loss_bps),
        ):
            if bps is None:
                continue
            if isinstance(bps, bool) or not isinstance(bps, int) or not 0 < bps <= MAX_EXIT_BPS:
                self.log.error(
                    f"{field_name} ({bps!r}) must be an integer in 1..{MAX_EXIT_BPS}: an exit "
                    f"10_000 bps below the entry sits at or below zero"
                )
                return False
        return True

    def _open_signal_log(self) -> pd.Timestamp | None:
        """
        Open the signal log and write its `start` record, returning the timer's start time (the
        record's `ts_ns`, so every cycle lands on start + k s); None, the timer's default, when off.
        """
        if self.config.signal_log_path is None:
            return None
        # Whole microseconds: the live clock's timers fire on microsecond ticks (a start of
        # ...519479442 ns fires at ...520519479000), so an unrounded start would put every live
        # cycle a few hundred ns off start + k s and the replay could never pair them exactly.
        start_ns = self.clock.timestamp_ns() // 1_000 * 1_000
        self._signal_log = SignalLog(self.config.signal_log_path)
        config = self.config
        self._signal_log.write(
            {
                "kind": "start",
                "bot_id": config.order_id_tag,
                "instrument_id": str(config.instrument_id),
                "ts_ns": start_ns,
                "trade_size": str(config.trade_size),
                "bar_spec": config.bar_spec,
                "trend_lookback": config.trend_lookback,
                "trend_buy_threshold": config.trend_buy_threshold,
                "trend_sell_threshold": config.trend_sell_threshold,
                "ofi_levels": config.ofi_levels,
                "ofi_window": config.ofi_window,
                "obi_levels": config.obi_levels,
                "ofi_confirm_threshold": config.ofi_confirm_threshold,
            },
        )
        return pd.Timestamp(start_ns, tz="UTC")

    def on_stop(self) -> None:
        # A stopped bot must not leave resting orders the TUI would still show as protection
        # it no longer manages (see module docstring).
        if self._has_resting_orders():
            self.cancel_all_orders(self.config.instrument_id)
        if self._signal_log is not None:
            self._signal_log.close()
            self._signal_log = None

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.last_data_ns = tick.ts_event

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
        # Reset first: a skipped cycle submits nothing, and must not log the last cycle's side.
        self._cycle_action = None

        book = self.cache.order_book(self.config.instrument_id)
        if book is None:
            self._log_cycle("book_skipped", event.ts_event, {"reason": SKIP_NO_BOOK})
            return

        bids = book.bids()
        asks = book.asks()
        if not bids or not asks:
            skipped = {"book_ts_ns": book.ts_last, "reason": SKIP_ONE_SIDED}
            self._log_cycle("book_skipped", event.ts_event, skipped)
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
        if self._signal_log is not None:
            fed = {
                "book_ts_ns": book.ts_last,
                "bids": self._top_levels(bid_prices, bid_sizes),
                "asks": self._top_levels(ask_prices, ask_sizes),
            }
            self._log_cycle("book", event.ts_event, fed)

    def _top_levels(self, prices: list[float], sizes: list[float]) -> list[list[float]]:
        """Return the top `max(ofi_levels, obi_levels)` fed levels as [price, size] pairs."""
        levels = max(self.config.ofi_levels, self.config.obi_levels)
        return [[price, size] for price, size in zip(prices[:levels], sizes[:levels], strict=True)]

    def on_bar(self, bar: Bar) -> None:
        self._cycle_action = None
        close = bar.close.as_double()
        self.trend.update_raw(close)
        if self.trend.initialized:
            self.publish_signal(name="trend", value=self.trend.value, ts_event=bar.ts_event)
        self._maybe_trade()
        self._log_cycle("bar", bar.ts_event, {"bar": {"ts_event": bar.ts_event, "close": close}})

    def _log_cycle(self, kind: str, ts_ns: int, fields: dict[str, object]) -> None:
        """Append one decision cycle's record to the signal log; nothing when the log is off."""
        if self._signal_log is None:
            return
        values = {
            name: indicator.value if indicator.initialized else None
            for name, indicator in (
                ("microprice", self.microprice),
                ("ofi", self.ofi),
                ("obi", self.obi),
                ("mlofi", self.mlofi),
                ("trend", self.trend),
            )
        }
        record: dict[str, object] = {
            "kind": kind,
            "bot_id": self.config.order_id_tag,
            "instrument_id": str(self.config.instrument_id),
            "ts_ns": ts_ns,
            **fields,
            **values,
            "signal": self._signal(values["trend"], values["mlofi"]),
            "action": self._cycle_action.name if self._cycle_action is not None else None,
        }
        self._signal_log.write(record)

    def _signal(self, trend: float | None, mlofi: float | None) -> str:
        config = self.config
        return signal_of(
            trend,
            mlofi,
            config.trend_buy_threshold,
            config.trend_sell_threshold,
            config.ofi_confirm_threshold,
        )

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
        if self._uses_exits:
            self._trade_with_exits(is_flat)
            return
        side = self._wanted_side(is_flat)
        if side is not None:
            self._submit(side)

    def _trade_with_exits(self, is_flat: bool) -> None:
        """
        One bracket-mode cycle: resting exits are cancelled before a reversal and whenever flat,
        and the trade waits for the next cycle (see module docstring).
        """
        if is_flat:
            self._reversal_side = None
            if self._entry_working():
                return
            if self._has_resting_orders():
                self.cancel_all_orders(self.config.instrument_id)
                return
            side = self._wanted_side(is_flat)
            if side is not None:
                self._enter(side)
            return
        side = (
            self._reversal_side if self._reversal_side is not None else self._wanted_side(is_flat)
        )
        if side is None:
            return
        self._reversal_side = side
        if self._has_resting_orders():
            self.cancel_all_orders(self.config.instrument_id)
            return
        self._flatten(side)

    def _wanted_side(self, is_flat: bool) -> OrderSide | None:
        """Return the side of the one order this cycle's signals call for, None to hold."""
        signal = self._signal(self.trend.value, self.mlofi.value)
        long_signal = signal == SIGNAL_LONG
        short_signal = signal == SIGNAL_SHORT
        if is_flat:
            if long_signal:
                return OrderSide.BUY
            return OrderSide.SELL if short_signal else None
        if self.portfolio.is_net_long(self.config.instrument_id) and short_signal:
            return OrderSide.SELL
        if self.portfolio.is_net_short(self.config.instrument_id) and long_signal:
            return OrderSide.BUY
        return None

    @property
    def _uses_exits(self) -> bool:
        return self.config.take_profit_bps is not None or self.config.stop_loss_bps is not None

    def _has_resting_orders(self) -> bool:
        instrument_id = self.config.instrument_id
        return bool(
            self.cache.orders_open(instrument_id=instrument_id, strategy_id=self.id)
            or self.cache.orders_emulated(instrument_id=instrument_id, strategy_id=self.id)
        )

    def _entry_working(self) -> bool:
        """
        Whether an entry of this bot is still working while it reads flat: the entry itself is
        resting, or a leg's parent entry has not closed yet (accepted, fill not yet applied).
        Its legs are not orphans, and cancelling them would leave the fill unprotected.
        """
        instrument_id = self.config.instrument_id
        for order in (
            *self.cache.orders_open(instrument_id=instrument_id, strategy_id=self.id),
            *self.cache.orders_emulated(instrument_id=instrument_id, strategy_id=self.id),
        ):
            if not order.is_reduce_only:
                return True
            parent = self.cache.order(order.parent_order_id) if order.parent_order_id else None
            if parent is not None and not parent.is_closed:
                return True
        return False

    def _enter(self, side: OrderSide) -> None:
        assert self.instrument is not None
        quote = self.cache.quote_tick(self.config.instrument_id)
        if quote is None:
            # No mid to price the exits from: an entry without them would be unprotected.
            return
        mid = (quote.bid_price.as_decimal() + quote.ask_price.as_decimal()) / 2
        take_profit, stop_loss = exit_prices(
            side,
            mid,
            self.instrument.price_increment.as_decimal(),
            self.config.take_profit_bps,
            self.config.stop_loss_bps,
        )
        if any(price is not None and price <= 0 for price in (take_profit, stop_loss)):
            # Bounded bps keep an exit above zero unless one bps step is below one tick.
            self.log.error(
                f"exit at or below zero from mid {mid} (take-profit {take_profit}, stop-loss "
                f"{stop_loss}): no entry"
            )
            return
        precision = self.instrument.price_precision
        order_list = entry_with_exits(
            self.order_factory,
            self.config.instrument_id,
            side,
            self.instrument.make_qty(self.config.trade_size),
            make_price(take_profit, precision) if take_profit is not None else None,
            make_price(stop_loss, precision) if stop_loss is not None else None,
            self.clock.timestamp_ns(),
        )
        self.submit_order_list(order_list)
        self._cycle_action = side

    def _flatten(self, side: OrderSide) -> None:
        """
        Close the whole open position with one reduce-only market order. Sized to the position,
        not `trade_size`: a partly filled flatten's remainder is cancelled on the next cycle
        (`_reversal_side` holds until flat) and re-sent for what is left, so it can never
        overshoot into an opposite position that has no exits.
        """
        assert self.instrument is not None
        quantity = abs(self.portfolio.net_position(self.config.instrument_id))
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            reduce_only=True,
        )
        self.submit_order(order)
        self._cycle_action = side

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
        self._cycle_action = side
