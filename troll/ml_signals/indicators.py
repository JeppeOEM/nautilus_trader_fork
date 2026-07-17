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
Indicators for use as buy/sell signals across strategies: trend, microprice, OFI.

Every indicator here is meant to be reused unmodified across research (Jupyter), backtest,
and live contexts (Story 2.2, FR10) -- never redefined locally in a notebook or strategy file.
Real consuming-context coverage as of Story 2.2 (not a requirement that every indicator be used
everywhere -- FR10 is conditional on actual usage, not a coverage mandate): OrderFlowImbalance
has both a backtest Strategy consumer (ofi_strategy.py) and a direct-replay consumer
(metrics_computer.py/chart_data.py), but no research-notebook demo yet. Microprice has the
direct-replay consumer and the research notebook, but no backtest Strategy consumer yet.
OnlineLogisticTrend is only consumed by a backtest Strategy (example_strategy.py).
MultiLevelOBI/MultiLevelOFI are only consumed by dashboard.py's live monitor loop (not a
Nautilus Strategy). This is an honest
note, not a gap to close here.
"""

from collections import deque

import numpy as np

from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick


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

            # ponytail: single online SGD step per bar, no batch retraining or
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
        bid = sum(bid_sizes[:self.levels])
        ask = sum(ask_sizes[:self.levels])
        total = bid + ask
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
        readings: ``(value - mean) / std``. Returns 0.0 when std is zero.
        Must be >= 2. A window 10–20× the OFI `window` works well in practice.
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
        self._zscore_history: deque[float] | None = (
            deque(maxlen=zscore_window) if zscore_window is not None else None
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
            self._prev_bid_prices = bid_prices[:self.levels]
            self._prev_bid_sizes = bid_sizes[:self.levels]
            self._prev_ask_prices = ask_prices[:self.levels]
            self._prev_ask_sizes = ask_sizes[:self.levels]
            return

        n = min(self.levels, len(bid_prices), len(ask_prices), len(self._prev_bid_prices), len(self._prev_ask_prices))
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

        if self._zscore_history is not None:
            self._zscore_history.append(raw_value)
            if len(self._zscore_history) >= 2:
                arr = np.array(self._zscore_history)
                std = float(arr.std())
                self.value = float((raw_value - float(arr.mean())) / std) if std > 0.0 else 0.0
            else:
                self.value = 0.0
        else:
            self.value = raw_value

        self._set_has_inputs(True)
        self._set_initialized(True)

        self._prev_bid_prices = bid_prices[:self.levels]
        self._prev_bid_sizes = bid_sizes[:self.levels]
        self._prev_ask_prices = ask_prices[:self.levels]
        self._prev_ask_sizes = ask_sizes[:self.levels]

    def clear_prev_state(self) -> None:
        """Clear stale previous-tick state without losing contribution/z-score history.

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
        if self._zscore_history is not None:
            self._zscore_history.clear()
        self._prev_bid_prices = None
        self._prev_bid_sizes = None
        self._prev_ask_prices = None
        self._prev_ask_sizes = None
