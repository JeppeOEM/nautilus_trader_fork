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
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
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


def test_multilevel_obi_bid_heavy() -> None:
    obi = MultiLevelOBI(levels=3)
    # bid levels: 10, 8, 6  ask levels: 2, 2, 2  → bid_sum=24, ask_sum=6, obi=0.8
    obi.update_raw(bid_sizes=[10.0, 8.0, 6.0], ask_sizes=[2.0, 2.0, 2.0])
    assert obi.initialized
    assert abs(obi.value - 0.8) < 1e-9


def test_multilevel_obi_levels_capped() -> None:
    # levels=2 should only use first 2 levels
    obi = MultiLevelOBI(levels=2)
    obi.update_raw(bid_sizes=[10.0, 10.0, 999.0], ask_sizes=[10.0, 10.0, 999.0])
    assert abs(obi.value - 0.5) < 1e-9


def test_multilevel_ofi_two_level_known_contribution() -> None:
    ofi = MultiLevelOFI(levels=2, window=10)
    # Seed state
    ofi.update_raw([100.0, 99.0], [5.0, 3.0], [101.0, 102.0], [4.0, 2.0])
    assert not ofi.initialized
    # Bid L1 price up → bid_term=6; ask L1 price same → ask_term=3-4=-1; L1 contrib=7
    # Bid L2 price same → bid_term=4-3=1; ask L2 price same → ask_term=2-2=0; L2 contrib=1
    # total = 8
    ofi.update_raw([101.0, 99.0], [6.0, 4.0], [101.0, 102.0], [3.0, 2.0])
    assert ofi.initialized
    assert ofi.value == 8.0


def test_multilevel_ofi_respects_window() -> None:
    ofi = MultiLevelOFI(levels=1, window=2)
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])  # seed
    ofi.update_raw([101.0], [5.0], [101.0], [5.0])  # contrib: bid up → 5, ask same → 0 → +5
    ofi.update_raw([101.0], [5.0], [101.0], [5.0])  # contrib: 0
    ofi.update_raw([99.0], [5.0], [101.0], [5.0])   # contrib: bid down → -5, ask same → 0 → -5
    # window=2: last two contribs are 0 and -5 → sum = -5
    assert ofi.value == -5.0


if __name__ == "__main__":
    test_uptrend_converges_above_half()
    test_downtrend_converges_below_half()
    test_microprice_weights_toward_thinner_side()
    test_ofi_accumulates_known_contributions()
    test_multilevel_obi_bid_heavy()
    test_multilevel_obi_levels_capped()
    test_multilevel_ofi_two_level_known_contribution()
    test_multilevel_ofi_respects_window()
    print("ok")
