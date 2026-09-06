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
from ml_signals.indicators import mid_price
from ml_signals.indicators import spread
from ml_signals.indicators import trade_aggregates
from ml_signals.indicators import volume_delta


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


def test_multilevel_ofi_usd_notional_scales_by_price() -> None:
    ofi_raw = MultiLevelOFI(levels=1, window=10)
    ofi_usd = MultiLevelOFI(levels=1, window=10, usd_notional=True)
    # seed both
    ofi_raw.update_raw([100.0], [5.0], [101.0], [4.0])
    ofi_usd.update_raw([100.0], [5.0], [101.0], [4.0])
    # bid price 100→101 (up), new size=6:  raw bid_term=6,      usd bid_term=6*101=606
    # ask price same 101,  size 4→3:       raw ask_term=3-4=-1, usd ask_term=(3-4)*101=-101
    # raw contribution: 6 - (-1) = 7
    # usd contribution: 606 - (-101) = 707
    ofi_raw.update_raw([101.0], [6.0], [101.0], [3.0])
    ofi_usd.update_raw([101.0], [6.0], [101.0], [3.0])
    assert ofi_raw.value == 7.0
    assert ofi_usd.value == 707.0


def test_multilevel_ofi_zscore_zero_for_constant_signal() -> None:
    # When all history entries are identical, std=0 → z-score returns 0.0
    ofi = MultiLevelOFI(levels=1, window=1, zscore_window=5)
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])  # seed
    for _ in range(10):
        # no price change: bid_term=5-5=0, ask_term=5-5=0 → contribution=0 every time
        ofi.update_raw([100.0], [5.0], [101.0], [5.0])
    assert ofi.value == 0.0


def test_multilevel_ofi_zscore_direction_matches_signal() -> None:
    # Build history with alternating +5/-5 so mean=0, std=5.
    # A bid-up update should give z ≈ +1.0, a bid-down update z ≈ -1.0.
    ofi = MultiLevelOFI(levels=1, window=1, zscore_window=10)
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])  # seed
    for _ in range(5):
        ofi.update_raw([101.0], [5.0], [101.0], [5.0])  # bid up   → contribution +5
        ofi.update_raw([100.0], [5.0], [101.0], [5.0])  # bid down → contribution -5
    # zscore_history is now full: [+5,-5,+5,-5,+5,-5,+5,-5,+5,-5], mean=0, std=5
    ofi.update_raw([101.0], [5.0], [101.0], [5.0])   # bid up → raw=+5 → z=(5-0)/5=+1.0
    assert abs(ofi.value - 1.0) < 0.01
    ofi.update_raw([100.0], [5.0], [101.0], [5.0])   # bid down → raw=-5 → z≈-1.0
    assert ofi.value < 0.0


def _snap(bid_prices=None, ask_prices=None, buy_volume=0.0, sell_volume=0.0, buy_count=0, sell_count=0) -> dict:
    return {
        "bid_prices": bid_prices if bid_prices is not None else [100.0],
        "ask_prices": ask_prices if ask_prices is not None else [101.0],
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "buy_count": buy_count,
        "sell_count": sell_count,
    }


def test_spread_is_ask_minus_bid() -> None:
    assert spread(_snap(bid_prices=[100.0], ask_prices=[101.0])) == 1.0


def test_spread_none_on_thin_book() -> None:
    assert spread(_snap(bid_prices=[], ask_prices=[101.0])) is None
    assert spread(_snap(bid_prices=[100.0], ask_prices=[])) is None


def test_mid_price_averages_top_of_book() -> None:
    assert mid_price(_snap(bid_prices=[100.0], ask_prices=[102.0])) == 101.0


def test_mid_price_none_on_thin_book() -> None:
    assert mid_price(_snap(bid_prices=[], ask_prices=[101.0])) is None


def test_volume_delta_is_buy_minus_sell() -> None:
    assert volume_delta(_snap(buy_volume=5.0, sell_volume=2.0)) == 3.0


def test_trade_aggregates_sums_across_snapshots() -> None:
    snaps = [
        _snap(buy_volume=1.0, sell_volume=2.0, buy_count=1, sell_count=3),
        _snap(buy_volume=4.0, sell_volume=0.0, buy_count=2, sell_count=0),
    ]
    assert trade_aggregates(snaps) == (5.0, 2.0, 3, 3)


def test_trade_aggregates_empty_list() -> None:
    assert trade_aggregates([]) == (0.0, 0.0, 0, 0)


if __name__ == "__main__":
    test_uptrend_converges_above_half()
    test_downtrend_converges_below_half()
    test_microprice_weights_toward_thinner_side()
    test_ofi_accumulates_known_contributions()
    test_multilevel_obi_bid_heavy()
    test_multilevel_obi_levels_capped()
    test_multilevel_ofi_two_level_known_contribution()
    test_multilevel_ofi_respects_window()
    test_multilevel_ofi_usd_notional_scales_by_price()
    test_multilevel_ofi_zscore_zero_for_constant_signal()
    test_multilevel_ofi_zscore_direction_matches_signal()
    test_spread_is_ask_minus_bid()
    test_spread_none_on_thin_book()
    test_mid_price_averages_top_of_book()
    test_mid_price_none_on_thin_book()
    test_volume_delta_is_buy_minus_sell()
    test_trade_aggregates_sums_across_snapshots()
    test_trade_aggregates_empty_list()
    print("ok")
