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
"""Self-check: each indicator's loop/branch logic against hand-computed expectations."""

from ml_signals.indicators import Microprice
from ml_signals.indicators import OnlineLogisticTrend
from ml_signals.indicators import OrderFlowImbalance


def test_uptrend_converges_above_half() -> None:
    indicator = OnlineLogisticTrend(lookback=3, learning_rate=0.5)
    price = 100.0
    for _ in range(200):
        price *= 1.01
        indicator.update_raw(price)

    assert indicator.initialized
    assert indicator.value > 0.5


def test_downtrend_converges_below_half() -> None:
    indicator = OnlineLogisticTrend(lookback=3, learning_rate=0.5)
    price = 100.0
    for _ in range(200):
        price *= 0.99
        indicator.update_raw(price)

    assert indicator.initialized
    assert indicator.value < 0.5


def test_microprice_weights_toward_thinner_side() -> None:
    indicator = Microprice()
    indicator.update_raw(bid_price=100.0, bid_size=2.0, ask_price=102.0, ask_size=6.0)

    assert indicator.initialized
    assert indicator.value == 100.5  # (100*6 + 102*2) / 8


def test_ofi_accumulates_known_contributions() -> None:
    indicator = OrderFlowImbalance(window=10)
    indicator.update_raw(bid_price=100.0, bid_size=5.0, ask_price=101.0, ask_size=5.0)
    assert not indicator.initialized  # first update only seeds prev state

    indicator.update_raw(bid_price=100.0, bid_size=8.0, ask_price=101.0, ask_size=5.0)
    assert indicator.value == 3.0  # bid_term=8-5=3, ask_term=5-5=0

    indicator.update_raw(bid_price=101.0, bid_size=8.0, ask_price=101.0, ask_size=3.0)
    assert indicator.initialized
    assert indicator.value == 13.0  # +10: bid_term=8 (price up), ask_term=3-5=-2


if __name__ == "__main__":
    test_uptrend_converges_above_half()
    test_downtrend_converges_below_half()
    test_microprice_weights_toward_thinner_side()
    test_ofi_accumulates_known_contributions()
    print("ok")
