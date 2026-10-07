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
Indicators for use as buy/sell signals across strategies: trend, microprice, OFI (kernel,
DDD spine AD-D3).

Invariant: pure. Each `Indicator` holds only its own rolling state and each function is
stateless -- no I/O, no store, no config, no module-level state -- so research, backtest, live
paper and the ranking/views readers all compute a signal with the one implementation.

Every indicator here is meant to be reused unmodified across research (Jupyter), backtest,
and live contexts (Story 2.2, FR10) -- never redefined locally in a notebook or strategy file.
Real consuming-context coverage as of Story 2.2 (not a requirement that every indicator be used
everywhere -- FR10 is conditional on actual usage, not a coverage mandate): OrderFlowImbalance
has both a backtest Strategy consumer (ofi_strategy.py) and a direct-replay consumer
(metrics_computer.py/chart_data.py), but no research-notebook demo yet. Microprice has the
direct-replay consumer and the research notebook, but no backtest Strategy consumer yet.
OnlineLogisticTrend is only consumed by a backtest Strategy (indicator_signal_strategy.py, signal `logistic_trend`).
MultiLevelOBI/MultiLevelOFI were then consumed only by the web dashboard's live monitor loop
(retired in Story 15.10); today `ranking_engine`, `data_api`'s indicator series, `bots` and
the snapshot/OFI strategies consume them. This is an honest note, not a gap to close here.
Story 27.3 added `RollingZScore` (the one z-score formula, which `MultiLevelOFI` delegates to)
and moved `DepthProfile` here with the snapshot depth functions, for views and research alike.
Story 33.4 added `basis_bps` and `funding_annualised`, the one derivatives formulas behind both the
`views.derivatives` read model and the ranking row, and moved `liquidity_distance` here.
Story 33.14 added `LiquidationCascade`, the one cascade definition: the research cascade strategy
(`research.strategies.liquidation_cascade_strategy`, backtest and live paper bot alike) feeds it,
and `research.application.liquidations.replay_cascade` replays it for episodes -- 33.13's
`cascade_episodes` extends that replay rather than redefining a cascade.
Story 33.6 added `organic_delta_units`, `units_ratio` and `bar_vwap`, the per-bar order-flow
formulas behind the chart's stored-aggregate indicators (`views.indicator_picker`).
"""

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

import numpy as np

from kernel.clocks import NS_PER_S
from kernel.liquidation import LiquidatedSide
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick


# The one OFI gap rule (Story 31.3): when consecutive snapshots' `ts_event`s differ by strictly more
# than this, the caller clears the OFI tracker's previous book (`MultiLevelOFI.clear_prev_state`)
# before the next update, so a post-gap book is never diffed against a stale pre-gap one. 3 s: the
# live ranking's published and documented value (DATA_DICTIONARY §2.2; research used 5 s before).
# Every OFI replay imports it -- `ranking`, `views.chart_series`, the research OFI/snapshot
# strategies and `research.application.microstructure` -- none keeps a threshold of its own.
OFI_GAP_NS: int = 3_000_000_000


class OnlineLogisticTrend(Indicator):
    """
    Online (incremental) logistic regression predicting P(next bar's return > 0).

    Each bar, the model first trains one SGD step on the *previous* prediction
    now that the true outcome is known, then predicts the next probability
    from the most recent `lookback` returns. `self.value` is that probability,
    0.5 until `initialized`.

    Parameters
    ----------
    lookback : int
        Number of trailing bar-to-bar returns used as features (> 0).
    learning_rate : float
        SGD step size (> 0).

    """

    def __init__(self, lookback: int = 5, learning_rate: float = 0.05):
        PyCondition.positive_int(lookback, "lookback")
        PyCondition.positive(learning_rate, "learning_rate")
        super().__init__(params=[lookback, learning_rate])

        self.lookback = lookback
        self.learning_rate = learning_rate
        self.value = 0.5
        self._weights = np.zeros(lookback)
        self._bias = 0.0
        self._returns: deque[float] = deque(maxlen=lookback)
        self._pending_features: np.ndarray | None = None
        self._last_close: float | None = None

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        self.update_raw(bar.close.as_double())

    def update_raw(self, close: float) -> None:
        if self._last_close is not None:
            ret = (close - self._last_close) / self._last_close

            # Known limit: a single online SGD step per bar, no batch retraining or
            # persistence across restarts. Swap for sklearn.linear_model.SGDClassifier
            # + periodic refit if this drifts on long-running deployments.
            if self._pending_features is not None:
                self._sgd_step(self._pending_features, label=1.0 if ret > 0.0 else 0.0)

            self._returns.append(ret)
            if len(self._returns) == self.lookback:
                features = np.array(self._returns)
                self.value = self._predict(features)
                self._pending_features = features
                if not self.initialized:
                    self._set_has_inputs(True)
                    self._set_initialized(True)

        self._last_close = close

    def _predict(self, features: np.ndarray) -> float:
        z = float(np.dot(self._weights, features) + self._bias)
        return float(1.0 / (1.0 + np.exp(-z)))

    def _sgd_step(self, features: np.ndarray, label: float) -> None:
        error = self._predict(features) - label
        self._weights -= self.learning_rate * error * features
        self._bias -= self.learning_rate * error

    def _reset(self) -> None:
        self.value = 0.5
        self._weights = np.zeros(self.lookback)
        self._bias = 0.0
        self._returns.clear()
        self._pending_features = None
        self._last_close = None


class Microprice(Indicator):
    """
    Size-weighted mid price: pulls the mid toward whichever side of the book
    is thinner, a better fair-value estimate than the plain mid for
    order-placement and short-horizon direction signals.

    `self.value` is `(bid_price * ask_size + ask_price * bid_size) / (bid_size + ask_size)`.
    """

    def __init__(self) -> None:
        super().__init__(params=[])
        self.value = 0.0

    def handle_quote_tick(self, tick: QuoteTick) -> None:
        PyCondition.not_none(tick, "tick")
        self.update_raw(
            tick.bid_price.as_double(),
            tick.bid_size.as_double(),
            tick.ask_price.as_double(),
            tick.ask_size.as_double(),
        )

    def update_raw(
        self,
        bid_price: float,
        bid_size: float,
        ask_price: float,
        ask_size: float,
    ) -> None:
        total_size = bid_size + ask_size
        if total_size == 0.0:
            return

        self.value = (bid_price * ask_size + ask_price * bid_size) / total_size
        self._set_has_inputs(True)
        self._set_initialized(True)

    def _reset(self) -> None:
        self.value = 0.0


class OrderFlowImbalance(Indicator):
    """
    Rolling sum of the Cont-Kukanov-Stoikov order flow imbalance computed from
    best bid/ask price and size changes between consecutive top-of-book updates.

    Positive `self.value` indicates net buying pressure at the top of book,
    negative indicates net selling pressure, over the trailing `window` updates.

    Parameters
    ----------
    window : int
        Number of trailing per-update contributions summed into `self.value` (> 0).

    """

    def __init__(self, window: int = 50) -> None:
        PyCondition.positive_int(window, "window")
        super().__init__(params=[window])

        self.window = window
        self.value = 0.0
        self._contributions: deque[float] = deque(maxlen=window)
        self._prev_bid_price: float | None = None
        self._prev_bid_size: float | None = None
        self._prev_ask_price: float | None = None
        self._prev_ask_size: float | None = None

    def handle_quote_tick(self, tick: QuoteTick) -> None:
        PyCondition.not_none(tick, "tick")
        self.update_raw(
            tick.bid_price.as_double(),
            tick.bid_size.as_double(),
            tick.ask_price.as_double(),
            tick.ask_size.as_double(),
        )

    def update_raw(
        self,
        bid_price: float,
        bid_size: float,
        ask_price: float,
        ask_size: float,
    ) -> None:
        if self._prev_bid_price is not None:
            if bid_price > self._prev_bid_price:
                bid_term = bid_size
            elif bid_price == self._prev_bid_price:
                bid_term = bid_size - self._prev_bid_size
            else:
                bid_term = -self._prev_bid_size

            if ask_price < self._prev_ask_price:
                ask_term = ask_size
            elif ask_price == self._prev_ask_price:
                ask_term = ask_size - self._prev_ask_size
            else:
                ask_term = -self._prev_ask_size

            self._contributions.append(bid_term - ask_term)
            self.value = float(sum(self._contributions))
            self._set_has_inputs(True)
            self._set_initialized(True)

        self._prev_bid_price = bid_price
        self._prev_bid_size = bid_size
        self._prev_ask_price = ask_price
        self._prev_ask_size = ask_size

    def _reset(self) -> None:
        self.value = 0.0
        self._contributions.clear()
        self._prev_bid_price = None
        self._prev_bid_size = None
        self._prev_ask_price = None
        self._prev_ask_size = None


class RollingZScore(Indicator):
    """
    Z-score of each reading against the last `window` readings (itself included): the one
    z-score formula in `platform/` (Story 27.3) -- `MultiLevelOFI(zscore_window=...)` delegates to
    it, and research z-scores OBI with it.

    `value = (x - mean) / std` over the trailing `window` readings, `std` the population standard
    deviation (numpy's default, ``ddof=0``); 0.0 while fewer than 2 readings are held or when the
    readings are all equal (compared exactly, so float rounding in the mean cannot fake a
    spread), never a division by zero. Invariant: at most `window` readings are held (a `deque`
    with `maxlen`), so the oldest ages out as each new one arrives. The caller decides what a
    reading is: a non-finite one (a gap) raises `ValueError` -- held, it would poison every
    z-score until it aged out.

    Parameters
    ----------
    window : int
        Readings the mean and std are taken over (>= 2).

    """

    def __init__(self, window: int) -> None:
        PyCondition.positive_int(window, "window")
        if window < 2:
            raise ValueError("window must be >= 2")
        super().__init__(params=[window])
        self.window = window
        self.value = 0.0
        self._history: deque[float] = deque(maxlen=window)

    def update_raw(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError(f"a z-score reading must be finite, got {value!r}")
        self._history.append(value)
        arr = np.array(self._history)
        # Equal readings are flat by comparison, not by `std == 0`: their mean can round one ulp
        # off the value (300 x 0.1), leaving a ~1e-17 std that turns a flat window into z = +-1.
        # A zero std with unequal readings is possible too (their squared deviations underflow,
        # e.g. [0.0, 1e-170]): still flat, never a division by zero.
        std = float(arr.std())
        # Known limit (DATA_DICTIONARY §2.2, audit D-89): an undefined z-score (under 2 readings,
        # or a flat window) is published as 0.0, indistinguishable from "at the mean". Upgrade
        # path: publish None (a gap) and let every reader show it.
        if len(arr) < 2 or arr.max() == arr.min() or std == 0.0:
            self.value = 0.0
        else:
            self.value = (value - float(arr.mean())) / std
        self._set_has_inputs(True)
        if len(self._history) == self.window:
            self._set_initialized(True)

    def _reset(self) -> None:
        self.value = 0.0
        self._history.clear()


class MultiLevelOBI(Indicator):
    """
    Order Book Imbalance across the top N price levels.

    value = sum(bid_sizes[:levels]) / (sum(bid_sizes[:levels]) + sum(ask_sizes[:levels]))

    1.0 = all depth on the bid side. 0.5 = balanced. 0.0 = all ask.
    Feed with `update_raw(bid_sizes, ask_sizes)` from DydxSecondSnapshot.

    Parameters
    ----------
    levels : int
        Number of price levels to include (> 0).
    """

    def __init__(self, levels: int = 10) -> None:
        PyCondition.positive_int(levels, "levels")
        super().__init__(params=[levels])
        self.levels = levels
        self.value = 0.5

    def update_raw(self, bid_sizes: list[float], ask_sizes: list[float]) -> None:
        bid = sum(bid_sizes[: self.levels])
        ask = sum(ask_sizes[: self.levels])
        total = bid + ask
        # Known limit (DATA_DICTIONARY §2.3, audit D-89): a zero total is undefined, but the
        # previous value is kept, so a stateful reader publishes the last defined OBI for that
        # second. Upgrade path: set the value to None (and callers publish a gap).
        if total == 0.0:
            return
        self.value = bid / total
        self._set_has_inputs(True)
        self._set_initialized(True)

    def _reset(self) -> None:
        self.value = 0.5


class MultiLevelOFI(Indicator):
    """
    Order Flow Imbalance summed across the top N price levels.

    Applies the Cont-Kukanov-Stoikov delta formula independently at each level
    and sums the contributions, giving a richer signal than top-of-book OFI alone.

    Feed with `update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)` from
    DydxSecondSnapshot — the lists are the full depth profile up to 20 levels.

    Parameters
    ----------
    levels : int
        Number of price levels to include (> 0).
    window : int
        Rolling window of per-update contributions summed into `value` (> 0).
    usd_notional : bool
        If True, each size term is multiplied by its price level before summing,
        so `value` is in dollars rather than native token units. Makes the signal
        comparable across instruments with very different price scales (e.g. BTC
        vs FLOKI).
    zscore_window : int | None
        If set, normalises `value` to a z-score over the last `zscore_window`
        readings through `RollingZScore` (the one z-score formula): ``(value - mean) / std``.
        Returns 0.0 when std is zero. Must be >= 2. A window 10-20x the OFI `window` works
        well in practice.
    """

    def __init__(
        self,
        levels: int = 10,
        window: int = 50,
        usd_notional: bool = False,
        zscore_window: int | None = None,
    ) -> None:
        PyCondition.positive_int(levels, "levels")
        PyCondition.positive_int(window, "window")
        if zscore_window is not None:
            PyCondition.positive_int(zscore_window, "zscore_window")
            if zscore_window < 2:
                raise ValueError("zscore_window must be >= 2")
        super().__init__(params=[levels, window])
        self.levels = levels
        self.window = window
        self.usd_notional = usd_notional
        self.zscore_window = zscore_window
        self.value = 0.0
        self._contributions: deque[float] = deque(maxlen=window)
        self._zscore: RollingZScore | None = (
            RollingZScore(zscore_window) if zscore_window is not None else None
        )
        self._prev_bid_prices: list[float] | None = None
        self._prev_bid_sizes: list[float] | None = None
        self._prev_ask_prices: list[float] | None = None
        self._prev_ask_sizes: list[float] | None = None

    def update_raw(
        self,
        bid_prices: list[float],
        bid_sizes: list[float],
        ask_prices: list[float],
        ask_sizes: list[float],
    ) -> None:
        if self._prev_bid_prices is None:
            self._prev_bid_prices = bid_prices[: self.levels]
            self._prev_bid_sizes = bid_sizes[: self.levels]
            self._prev_ask_prices = ask_prices[: self.levels]
            self._prev_ask_sizes = ask_sizes[: self.levels]
            return

        n = min(
            self.levels,
            len(bid_prices),
            len(ask_prices),
            len(self._prev_bid_prices),
            len(self._prev_ask_prices),
        )
        contribution = 0.0
        for i in range(n):
            bp, bs = bid_prices[i], bid_sizes[i]
            pbp, pbs = self._prev_bid_prices[i], self._prev_bid_sizes[i]
            ap, as_ = ask_prices[i], ask_sizes[i]
            pap, pas = self._prev_ask_prices[i], self._prev_ask_sizes[i]

            if self.usd_notional:
                bid_term = bs * bp if bp > pbp else ((bs - pbs) * bp if bp == pbp else -pbs * pbp)
                ask_term = as_ * ap if ap < pap else ((as_ - pas) * ap if ap == pap else -pas * pap)
            else:
                bid_term = bs if bp > pbp else (bs - pbs if bp == pbp else -pbs)
                ask_term = as_ if ap < pap else (as_ - pas if ap == pap else -pas)

            contribution += bid_term - ask_term

        self._contributions.append(contribution)
        raw_value = float(sum(self._contributions))

        if self._zscore is not None:
            self._zscore.update_raw(raw_value)
            self.value = self._zscore.value
        else:
            self.value = raw_value

        self._set_has_inputs(True)
        self._set_initialized(True)

        self._prev_bid_prices = bid_prices[: self.levels]
        self._prev_bid_sizes = bid_sizes[: self.levels]
        self._prev_ask_prices = ask_prices[: self.levels]
        self._prev_ask_sizes = ask_sizes[: self.levels]

    def clear_prev_state(self) -> None:
        """
        Clear stale previous-tick state without losing contribution/z-score history.

        Call this when the book has been rebuilt after a reconnect so the next
        update_raw is treated as the first observation rather than computing a
        delta against pre-disconnect prices.
        """
        self._prev_bid_prices = None
        self._prev_bid_sizes = None
        self._prev_ask_prices = None
        self._prev_ask_sizes = None

    def _reset(self) -> None:
        self.value = 0.0
        self._contributions.clear()
        if self._zscore is not None:
            self._zscore.reset()
        self._prev_bid_prices = None
        self._prev_bid_sizes = None
        self._prev_ask_prices = None
        self._prev_ask_sizes = None


# The floor under `LiquidationCascade.baseline`, in notional units per second (Story 33.14). It
# only prevents a division by zero: a market with no liquidation for a whole `baseline_s` has an EMA
# decaying towards 0, and an intensity divided by it would become infinite. It is NOT below every
# real rate (one unit in a `window_s > 1` window is `1 / window_s` units/s, under the floor), but a
# unit is `10^-(price_precision + size_precision)` of the quote, so one unit per second is
# negligible against any real liquidation flow. What it does not do is stop
# a tiny liquidation after a long silence from reading as a huge intensity and opening an episode:
# `min_episode_notional` (the strategy's rule) is the guard against acting on one.
# Known limit: the floor is a fixed count of units, so its quote value depends on the instrument's
# precisions; upgrade path: a floor in quote currency, converted at the definition's precisions by
# the caller.
BASELINE_FLOOR: float = 1.0


class LiquidationCascade(Indicator):
    """
    A liquidation cascade detector (Story 33.14): the rate of forced notional over a trailing
    `window_s`, per liquidated side, against its own exact continuous-time EMA over `baseline_s`.

    Inputs: `update_liquidation(side, notional_units, ts_ns)` for each liquidation (the row's
    `ts_init`, the receive time a live bot knows, and `Liquidation.notional_units()` at one fixed
    `price_precision + size_precision` for the whole replay) and `advance(ts_ns)` to move the clock
    without an event. Both are O(1) amortised. The clock never moves backwards: an event stamped
    before it is placed at it.

    Outputs, recomputed at every update:

    - `rate_long` / `rate_short`: notional units per second of `LONG` (a forced sell) / `SHORT`
      liquidations placed in the trailing `window_s` (an entry counts while `now < placed +
      window_s`);
    - `baseline`: `max(ema / (1 - exp(-elapsed / baseline_s)), BASELINE_FLOOR)`, `ema` the
      continuous-time EMA (time constant `baseline_s`) of the piecewise-constant total window rate,
      started at 0 at the first update, and `elapsed` the time since that update. Between two
      breakpoints (an arrival, or an entry's expiry) the rate `r` is constant, so `ema <- r + (ema -
      r) * exp(-dt / baseline_s)` integrates it analytically; every expiry is integrated in order,
      so the value does not depend on how often `advance` is called (equal up to float rounding).
      The division is the exact continuous-time bias correction: the EMA's weights over
      `[first, now]` sum to `1 - exp(-elapsed / baseline_s)`, so the corrected value is the
      weighted mean rate since the first update, not ~63 % of it at the end of the warm-up (which
      would inflate intensity ~1.6x and open false episodes after every start or restart);
    - `intensity`: total rate / `baseline` (finite: the floor);
    - `initialized`: once `baseline_s` has passed since the first update;
    - `active`: initialized and `intensity >= intensity_threshold`;
    - `direction`: -1 when longs are being liquidated (`rate_long > rate_short`, the price is
      falling), +1 for shorts, 0 when the two are equal or not `active` (audit D-147);
    - an **episode** starts at the first `active` update while none is open: `episode_start_ns`
      (the clock then), `episode_direction` (the direction then, or the first non-zero one after),
      `episode_notional_units` (the window's notional at the start plus every event fed while it
      is open) and `peak_rate` (the highest total rate since it started);
    - `rising`: the total rate is the open episode's peak, or above the previous update's;
    - `spent`: latched at the first update of the episode whose total rate is below
      `peak_rate * decay_ratio`;
    - `episode_ended`: True at the first update of an episode that is both `spent` and not
      `active`. That update still shows the episode (spent, its direction, start and notional), so
      a consumer always sees how an episode ends; the next update resets `peak_rate`, `spent` and
      every `episode_*` field before it evaluates.

    Invariant: the window holds exactly the events placed in the trailing `window_s` of the
    clock, in placement order, and the per-side sums are their sums; the clock is monotonic. A bad
    parameter raises at construction; a non-positive or non-int notional raises at update.

    Parameters
    ----------
    window_s : int
        The trailing window of the rates, in seconds (> 0).
    baseline_s : int
        The baseline EMA's time constant and the warm-up before `initialized`, in seconds (> 0).
    intensity_threshold : float
        The intensity at or above which the detector is `active` (> 0, finite).
    decay_ratio : float
        The share of the episode's peak rate below which it is `spent` (0 < decay_ratio < 1).

    """

    def __init__(
        self,
        window_s: int,
        baseline_s: int,
        intensity_threshold: float,
        decay_ratio: float,
    ) -> None:
        PyCondition.positive_int(window_s, "window_s")
        PyCondition.positive_int(baseline_s, "baseline_s")
        PyCondition.positive(intensity_threshold, "intensity_threshold")
        if not math.isfinite(intensity_threshold):
            raise ValueError(f"intensity_threshold must be finite, was {intensity_threshold}")
        if not 0.0 < decay_ratio < 1.0:
            raise ValueError(f"decay_ratio must be in (0, 1), was {decay_ratio}")
        super().__init__(params=[window_s, baseline_s, intensity_threshold, decay_ratio])
        self.window_s = window_s
        self.baseline_s = baseline_s
        self.intensity_threshold = intensity_threshold
        self.decay_ratio = decay_ratio
        self._window_ns = window_s * NS_PER_S
        self._baseline_ns = baseline_s * NS_PER_S
        # (placed_ns, is_long, notional_units), in placement (= clock) order.
        self._events: deque[tuple[int, bool, int]] = deque()
        self._clear()

    def _clear(self) -> None:
        self._events.clear()
        self._units_long = 0
        self._units_short = 0
        self._ema = 0.0
        self._clock_ns: int | None = None
        self._first_ns: int | None = None
        self._previous_rate = 0.0
        self.rate_long = 0.0
        self.rate_short = 0.0
        self.baseline = BASELINE_FLOOR
        self.intensity = 0.0
        self.direction = 0
        self.active = False
        self.rising = False
        self._clear_episode()

    def _clear_episode(self) -> None:
        self.peak_rate = 0.0
        self.spent = False
        self.episode_ended = False
        self.episode_direction = 0
        self.episode_start_ns: int | None = None
        self.episode_notional_units = 0

    @property
    def clock_ns(self) -> int | None:
        """The detector's clock: the latest update's time (None before the first)."""
        return self._clock_ns

    def update_liquidation(self, side: LiquidatedSide, notional_units: int, ts_ns: int) -> None:
        """Feed one liquidation of `notional_units` received at `ts_ns` (its `ts_init`)."""
        if isinstance(notional_units, bool) or not isinstance(notional_units, int):
            raise TypeError(f"notional_units must be an int, was {notional_units!r}")
        if notional_units <= 0:
            raise ValueError(f"notional_units must be > 0, was {notional_units}")
        placed = self._move_clock(ts_ns)
        is_long = side == LiquidatedSide.LONG
        self._events.append((placed, is_long, notional_units))
        self._add(is_long, notional_units)
        if self.episode_start_ns is not None and not self.episode_ended:
            self.episode_notional_units += notional_units
        self._evaluate()

    def advance(self, ts_ns: int) -> None:
        """Move the clock to `ts_ns` (never backwards) and re-evaluate without an event."""
        self._move_clock(ts_ns)
        self._evaluate()

    def _add(self, is_long: bool, units: int) -> None:
        if is_long:
            self._units_long += units
        else:
            self._units_short += units

    def _move_clock(self, ts_ns: int) -> int:
        """Integrate the EMA up to `max(ts_ns, clock)` through every expiry on the way."""
        if self._clock_ns is None:
            self._clock_ns = self._first_ns = ts_ns
            self._set_has_inputs(True)
            return ts_ns
        target = max(ts_ns, self._clock_ns)
        while self._events and self._events[0][0] + self._window_ns <= target:
            placed, is_long, units = self._events[0]
            self._integrate(placed + self._window_ns)
            self._events.popleft()
            self._add(is_long, -units)
        self._integrate(target)
        return target

    def _integrate(self, to_ns: int) -> None:
        assert self._clock_ns is not None  # set by the first update
        elapsed = to_ns - self._clock_ns
        if elapsed > 0:
            rate = (self._units_long + self._units_short) / self.window_s
            self._ema = rate + (self._ema - rate) * math.exp(-elapsed / self._baseline_ns)
            self._clock_ns = to_ns

    def _evaluate(self) -> None:
        assert self._clock_ns is not None  # set by the first update
        assert self._first_ns is not None
        if self.episode_ended:
            self._clear_episode()
        self.rate_long = self._units_long / self.window_s
        self.rate_short = self._units_short / self.window_s
        rate = self.rate_long + self.rate_short
        self.baseline = max(self._debiased_ema(), BASELINE_FLOOR)
        self.intensity = rate / self.baseline
        if not self.initialized and self._clock_ns - self._first_ns >= self._baseline_ns:
            self._set_initialized(True)
        self.active = self.initialized and self.intensity >= self.intensity_threshold
        self.direction = self._direction() if self.active else 0
        if self.episode_start_ns is None and self.active:
            self.episode_start_ns = self._clock_ns
            self.episode_notional_units = self._units_long + self._units_short
        if self.episode_start_ns is None:
            self.rising = rate > self._previous_rate
        else:
            self._track_episode(rate)
        self._previous_rate = rate

    def _debiased_ema(self) -> float:
        """Return the EMA over the weight it has accumulated since the first update."""
        assert self._clock_ns is not None  # set by the first update
        assert self._first_ns is not None
        elapsed = self._clock_ns - self._first_ns
        if elapsed == 0:
            return 0.0  # no time integrated yet: no rate observed, the floor applies
        return self._ema / -math.expm1(-elapsed / self._baseline_ns)

    def _direction(self) -> int:
        if self._units_long > self._units_short:
            return -1
        return 1 if self._units_short > self._units_long else 0

    def _track_episode(self, rate: float) -> None:
        if self.episode_direction == 0:
            self.episode_direction = self.direction
        self.rising = rate >= self.peak_rate or rate > self._previous_rate
        self.peak_rate = max(self.peak_rate, rate)
        if not self.spent and rate < self.peak_rate * self.decay_ratio:
            self.spent = True
        self.episode_ended = self.spent and not self.active

    def _reset(self) -> None:
        self._clear()


# -----------------------------------------------------------------------------------
# Plain, stateless single-snapshot derivations (SSOT-01, platform/CLAUDE.md) -- pure
# functions of one `DydxSecondSnapshot.as_floats()`-shaped dict (the decoded floats; the stored
# and wire `to_dict()` holds integer units since Story 30.2), no window/history state.
# Every caller (ranking_engine, data_api via views) must call these rather than
# reimplementing the formula locally, so two processes fed the same snapshot can never
# compute a different number for it.
# -----------------------------------------------------------------------------------


def microprice(snapshot: dict) -> float | None:
    """
    (bid_price * ask_size + ask_price * bid_size) / (bid_size + ask_size); None if
    either side is empty or total top-of-book size is zero.

    Pure/stateless companion to the `Microprice` indicator class above -- use this for
    a one-off point-in-time read (a historical chart point, a per-instrument snapshot
    with no meaningful "previous tick" to track state against). The class's own
    update_raw is itself memoryless per call, but silently *keeps* its last value on a
    zero-total tick rather than resetting -- this function returns None instead, which
    is the honest answer for a stateless read (never silently reports a stale value).
    """
    bid_prices = snapshot["bid_prices"]
    ask_prices = snapshot["ask_prices"]
    if not bid_prices or not ask_prices:
        return None
    bid_size, ask_size = snapshot["bid_sizes"][0], snapshot["ask_sizes"][0]
    total = bid_size + ask_size
    if total <= 0:
        return None
    return (bid_prices[0] * ask_size + ask_prices[0] * bid_size) / total


def spread(snapshot: dict) -> float | None:
    """
    ask_prices[0] - bid_prices[0]; None if either side is empty (thin/no book).

    Rounded to the row's `price_precision` when the dict carries it (`as_floats()` does, Story
    31.3): both prices are exact at that precision, so their difference is too, but subtracting two
    decoded doubles cancels most of their digits -- a one-tick 0.000001 spread at 8.578755 came out
    1.0000000010279564e-06, 1e-9 off, and every ratio of it (spread in ticks or bps) inherited the
    error. Rounding returns the double nearest the exact difference: the subtraction's error (about
    2 ulp of the price) stays far under half a unit for any price under 2e15 units (BTC is 8.4e6).
    A dict without the precision (a hand-built one) gets the plain difference.
    """
    bid_prices = snapshot["bid_prices"]
    ask_prices = snapshot["ask_prices"]
    if not bid_prices or not ask_prices:
        return None
    difference = ask_prices[0] - bid_prices[0]
    precision = snapshot.get("price_precision")
    return difference if precision is None else round(difference, precision)


def mid_price(snapshot: dict) -> float | None:
    """(bid_prices[0] + ask_prices[0]) / 2; None if either side is empty."""
    bid_prices = snapshot["bid_prices"]
    ask_prices = snapshot["ask_prices"]
    if not bid_prices or not ask_prices:
        return None
    return (bid_prices[0] + ask_prices[0]) / 2.0


def volume_delta(snapshot: dict) -> float:
    """buy_volume - sell_volume for one snapshot (single-tick, not a rolling window)."""
    return snapshot["buy_volume"] - snapshot["sell_volume"]


def trade_aggregates(snapshots: list[dict]) -> tuple[float, float, int, int]:
    """
    Sum (buy_volume, sell_volume, buy_count, sell_count) across a list of snapshots --
    feeds CVD (buy_vol - sell_vol) and avg_trade_size ((buy_vol + sell_vol) / total
    count). Pure reduction over whatever list it's given; the rolling-window *buffer*
    is the caller's own state (exactly one process should own it -- SSOT-02), not
    anything this function keeps itself.
    """
    return (
        sum(s["buy_volume"] for s in snapshots),
        sum(s["sell_volume"] for s in snapshots),
        sum(s["buy_count"] for s in snapshots),
        sum(s["sell_count"] for s in snapshots),
    )


# -----------------------------------------------------------------------------------
# Per-bar order flow (Story 33.6, SSOT-01): the one formula per stored per-bar aggregate, over the
# candle store's exact integer units (`candles.domain.fold`, `docs/DATA_DICTIONARY.md` §2.15). The
# caller maps a null input to None before calling (DATA-01: null is unknown, never 0); these take
# known integers only and turn a value into a `float` at the return, never before (DATA-04).
# -----------------------------------------------------------------------------------


def organic_delta_units(buy_v: int, sell_v: int, liq_long_v: int, liq_short_v: int) -> int:
    """
    Return the bar's delta without its forced flow, in the bar's `10^-size_precision` units:
    `(buy_v - liq_short_v) - (sell_v - liq_long_v)`.

    Sign mapping: a *long* liquidation closes a long position by selling, so `liq_long_v` is forced
    sell volume and is taken out of `sell_v`; a *short* liquidation closes a short by buying, so
    `liq_short_v` is forced buy volume and is taken out of `buy_v`. All four share the bar's one
    `size_precision` (the fold stores the bucket's finest and rescales every part to it). A bar
    with unknown flow or unknown liquidations has no organic delta: the caller returns None.
    """
    return (buy_v - liq_short_v) - (sell_v - liq_long_v)


def units_ratio(
    num_units: int, num_precision: int, den_units: int, den_precision: int
) -> float | None:
    """
    Return `(num_units * 10^-num_precision) / (den_units * 10^-den_precision)` as one exact
    `Fraction` rounded to a float once; None when the denominator is 0 (a quiet bar has no share
    and no average, never a division error or an infinity). `ForcedShare` passes
    `liq_long_v + liq_short_v` over `buy_v + sell_v` (one size precision), `AverageTradeSize`
    `buy_v + sell_v` at the size precision over `buy_n + sell_n` at precision 0.
    """
    if den_units == 0:
        return None
    return float(Fraction(num_units * 10**den_precision, den_units * 10**num_precision))


def bar_vwap(pv_units: int, volume_units: int, price_precision: int) -> float | None:
    """
    Return the volume-weighted price `pv / (volume * 10^price_precision)`, an exact `Fraction`
    rounded to a float once; None at 0 volume (no trade, no price).

    `pv_units` is in `10^-(price_precision + size_precision)` and `volume_units` in
    `10^-size_precision`, so the size scale cancels and only the price scale remains: e.g.
    `pv = 1_000_050` at `pp = 2, sp = 1` over 10 units of volume is `1_000_050 / (10 * 100)` =
    1000.05. A sum over bars of mixed precisions is rescaled by the caller to the finest price and
    size precision present first, so the same identity holds. The stored `pv` weights each second's
    traded volume at that second's close (`candles.domain.fold`'s Known limit), so this is the
    second-close VWAP, not every trade at its own price.
    """
    if volume_units == 0:
        return None
    return float(Fraction(pv_units, volume_units * 10**price_precision))


# -----------------------------------------------------------------------------------
# Book depth (Story 27.3: moved from `views/chart_series.py`, SSOT-01 -- one home for the
# snapshot -> depth derivation, shared by the chart and research).
# -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class DepthProfile:
    """Sizes and prices at the top N levels on both sides (a value: never mutated after build)."""

    bid_prices: list[float]  # index 0 = best bid
    bid_sizes: list[float]
    ask_prices: list[float]  # index 0 = best ask
    ask_sizes: list[float]

    @property
    def levels(self) -> int:
        return len(self.bid_prices)

    def total_bid_depth(self) -> float:
        return sum(self.bid_sizes)

    def total_ask_depth(self) -> float:
        return sum(self.ask_sizes)


def snapshot_depth(snapshot: dict, levels: int) -> DepthProfile | None:
    """
    Return the top `levels` levels of each side of one `DydxSecondSnapshot.as_floats()`-shaped
    snapshot; None when either side is empty (like `mid_price`/`spread`). A side holding fewer
    levels keeps what it has -- never padded. `levels` < 1 raises `ValueError`: an empty profile
    has no touch to measure from.
    """
    if levels < 1:
        raise ValueError(f"levels must be >= 1, got {levels}")
    if not snapshot["bid_prices"] or not snapshot["ask_prices"]:
        return None
    return DepthProfile(
        bid_prices=list(snapshot["bid_prices"][:levels]),
        bid_sizes=list(snapshot["bid_sizes"][:levels]),
        ask_prices=list(snapshot["ask_prices"][:levels]),
        ask_sizes=list(snapshot["ask_sizes"][:levels]),
    )


def cumulative_depth(profile: DepthProfile) -> tuple[list[float], list[float]]:
    """
    Cumulative size from the touch outward, per side: element i is the size of levels 0..i.
    Each side is as long as its own levels.
    """
    return (
        np.cumsum(profile.bid_sizes, dtype=np.float64).tolist(),
        np.cumsum(profile.ask_sizes, dtype=np.float64).tolist(),
    )


# A level exactly on an edge (BTC 100 000 with a 10 tick is 1 bp) computes as edge +- a few ulps;
# this relative slack keeps it on one side of the edge in every snapshot instead of flapping.
# Known limit (DATA_DICTIONARY §2.12): a level genuinely within 1e-9 past an edge counts as on it
# (the exact reference counts such a case as EDGE_SLACK). Upgrade path: compare exact unit
# distances (`DydxSecondSnapshot.exact`) instead of floats, with no slack.
_BPS_EDGE_SLACK = 1e-9


def _within(distances: np.ndarray, sizes: np.ndarray, edge: float) -> float:
    slack = edge * _BPS_EDGE_SLACK
    if not len(distances) or edge - slack > distances[-1]:
        return math.nan
    return float(sizes[distances <= edge + slack].sum())


def depth_within_bps(
    profile: DepthProfile, bps_edges: Sequence[float]
) -> tuple[list[float], list[float]]:
    """
    Per side, the cumulative size of the levels whose price lies within each `bps_edges` distance
    of the mid (`(best bid + best ask) / 2`, distance `|price - mid| / mid * 1e4`).

    An edge past the side's deepest stored level is NaN: the book beyond it was not stored, so the
    size within that distance is unknown, never the stored levels' total passed off as it.
    Known limit: a side genuinely thinner than the stored depth reads NaN there too (the snapshot
    does not say whether the book ended or the storage did); upgrade path: store the side's level
    count with the snapshot.
    """
    mid = (profile.bid_prices[0] + profile.ask_prices[0]) / 2.0
    bid_distance = (mid - np.asarray(profile.bid_prices, dtype=np.float64)) / mid * 1e4
    ask_distance = (np.asarray(profile.ask_prices, dtype=np.float64) - mid) / mid * 1e4
    bid_sizes = np.asarray(profile.bid_sizes, dtype=np.float64)
    ask_sizes = np.asarray(profile.ask_sizes, dtype=np.float64)
    return (
        [_within(bid_distance, bid_sizes, edge) for edge in bps_edges],
        [_within(ask_distance, ask_sizes, edge) for edge in bps_edges],
    )


@dataclass(frozen=True)
class LiquidityDistance:
    """
    How far from the best price the meaningful liquidity sits, per side: the absolute price
    distance from the touch to the level at which the cumulative size first reaches
    `pct_threshold` of that side's stored depth. Small: dense support/resistance close by; large:
    a vacuum price can move through fast.
    """

    bid_distance: float
    ask_distance: float


def _distance_to_share(prices: list[float], sizes: list[float], pct_threshold: float) -> float:
    total = sum(sizes)
    if total == 0:
        return 0.0
    target = total * pct_threshold
    cumulative = 0.0
    for price, size in zip(prices, sizes, strict=False):
        cumulative += size
        if cumulative >= target:
            return abs(price - prices[0])
    # Float rounding can leave the last cumulative a hair under the target: the deepest level.
    return abs(prices[-1] - prices[0])


def liquidity_distance(profile: DepthProfile, pct_threshold: float = 0.8) -> LiquidityDistance:
    """
    Return each side's `LiquidityDistance` over the profile's stored levels (Story 33.4 moved it
    here from `views.chart_series`, where nothing called it any more, so the screener and research
    have one copy to reach for).
    """
    return LiquidityDistance(
        bid_distance=_distance_to_share(profile.bid_prices, profile.bid_sizes, pct_threshold),
        ask_distance=_distance_to_share(profile.ask_prices, profile.ask_sizes, pct_threshold),
    )


# -----------------------------------------------------------------------------------
# Derivatives (Story 33.4, SSOT-02: the one basis and annualised-funding formulas, called by
# `views.derivatives` and `ranking` alike; exact `Decimal` in and out, a float only at the edge).
# -----------------------------------------------------------------------------------

_BPS = 10_000  # an int: Decimal arithmetic with it stays exact
_SECONDS_PER_YEAR = 31_536_000  # 365 days: the simple (non-compounded) annualisation convention


def basis_bps(mark: Decimal, ref: Decimal) -> Decimal | None:
    """
    Return `(mark - ref) / ref` in basis points as a `Decimal` division at the default
    28-significant-digit context (the quotient is rounded there, never through a `float`; not
    exact for a non-terminating ratio); None when `ref <= 0` (no meaningful basis against a
    non-positive reference, never a division error or an infinity).
    `ref` is the index (mark-index basis) or the last traded close (mark-last basis).
    """
    if ref <= 0:
        return None
    return (mark - ref) / ref * _BPS


def funding_annualised(rate: Decimal, interval_s: int | None) -> Decimal | None:
    """
    Return the per-interval funding `rate` scaled to a 365-day year (`rate * 31_536_000 /
    interval_s`), simple, not compounded; None when the interval is unknown or not positive -- an
    annualised rate is never guessed from a default interval. `interval_s` is in seconds
    (`kernel.derivs_wire` converts `FundingRateUpdate.interval`'s minutes).
    """
    if interval_s is None or interval_s <= 0:
        return None
    return rate * _SECONDS_PER_YEAR / interval_s


def pct_change(base: Decimal, latest: Decimal) -> Decimal | None:
    """
    Return `(latest - base) / base * 100`, the percent change from `base` to `latest`, as a
    `Decimal` division at the default 28-significant-digit context (never through a `float`); None
    when `base` is 0 (a percent of nothing is undefined, never 0 or an infinity).

    SSOT-02: the one percent-change formula of a derivatives or price series -- ranking's
    `oi_change_*_pct` (`ranking.domain.derivs.OpenInterestSeries`) and alerting's `pct_move` and
    `oi_change` conditions (`alerting.domain.conditions`, Story 33.8) all call it.
    """
    if base == 0:
        return None
    return (latest - base) / base * 100
