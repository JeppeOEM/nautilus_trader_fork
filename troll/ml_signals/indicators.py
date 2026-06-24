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
"""Indicators for use as buy/sell signals across strategies: trend, microprice, OFI."""

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
