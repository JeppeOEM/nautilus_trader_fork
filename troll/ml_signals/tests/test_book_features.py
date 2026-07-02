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
"""Unit tests for book_features: depth_profile, imbalance, liquidity_distance, CancellationTracker."""

from ml_signals.book_features import CancellationTracker
from ml_signals.book_features import DepthProfile
from ml_signals.book_features import book_imbalance
from ml_signals.book_features import compute_features
from ml_signals.book_features import depth_profile
from ml_signals.book_features import liquidity_distance
from ml_signals.book_features import top_of_book_series
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


IID = InstrumentId.from_str("TEST-PERP.SIM")


def _delta(action: BookAction, side: OrderSide, price: float, size: float, ts: int = 0) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    return OrderBookDelta(instrument_id=IID, action=action, order=order, flags=0, sequence=0, ts_event=ts, ts_init=ts)


def _book(*bid_levels: tuple[float, float], ask_levels: list[tuple[float, float]] | None = None) -> OrderBook:
    asks = ask_levels or [(b[0] + 1.0, b[1]) for b in bid_levels[:1]]
    book = OrderBook(IID, BookType.L2_MBP)
    for i, (price, size) in enumerate(bid_levels):
        book.apply_delta(_delta(BookAction.ADD, OrderSide.BUY, price, size, ts=i))
    for i, (price, size) in enumerate(asks):
        book.apply_delta(_delta(BookAction.ADD, OrderSide.SELL, price, size, ts=i))
    return book


# ---- depth_profile ----

def test_depth_profile_extracts_correct_levels() -> None:
    book = _book((100.0, 5.0), (99.0, 3.0), ask_levels=[(101.0, 4.0), (102.0, 6.0)])
    profile = depth_profile(book, levels=2)
    assert profile is not None
    assert profile.bid_prices == [100.0, 99.0]
    assert profile.bid_sizes  == [5.0, 3.0]
    assert profile.ask_prices == [101.0, 102.0]
    assert profile.ask_sizes  == [4.0, 6.0]
    assert profile.levels == 2


def test_depth_profile_empty_book_returns_none() -> None:
    assert depth_profile(OrderBook(IID, BookType.L2_MBP)) is None


def test_depth_profile_clips_to_available_levels() -> None:
    book = _book((100.0, 5.0), ask_levels=[(101.0, 4.0)])
    assert depth_profile(book, levels=4).levels == 1


def test_depth_profile_totals() -> None:
    book = _book((100.0, 4.0), (99.0, 6.0), ask_levels=[(101.0, 3.0), (102.0, 7.0)])
    profile = depth_profile(book, levels=2)
    assert profile.total_bid_depth() == 10.0
    assert profile.total_ask_depth() == 10.0


# ---- book_imbalance ----

def test_imbalance_balanced() -> None:
    imb = book_imbalance(DepthProfile([100.0], [5.0], [101.0], [5.0]))
    assert imb.per_level == [0.5]
    assert imb.aggregate == 0.5


def test_imbalance_all_bid() -> None:
    imb = book_imbalance(DepthProfile([100.0], [10.0], [101.0], [0.0]))
    assert imb.per_level == [1.0]
    assert imb.aggregate == 1.0


def test_imbalance_skewed_ask() -> None:
    # bids=2, asks=8 → aggregate=0.2
    imb = book_imbalance(DepthProfile([100.0], [2.0], [101.0], [8.0]))
    assert abs(imb.aggregate - 0.2) < 1e-9


def test_imbalance_two_levels_aggregate() -> None:
    # Each level balanced, aggregate also balanced
    imb = book_imbalance(DepthProfile([100.0, 99.0], [4.0, 2.0], [101.0, 102.0], [4.0, 2.0]))
    assert imb.per_level == [0.5, 0.5]
    assert imb.aggregate == 0.5


# ---- liquidity_distance ----

def test_liquidity_distance_first_level_covers_threshold() -> None:
    # 80% threshold, only 1 level → distance = 0
    dist = liquidity_distance(DepthProfile([100.0], [10.0], [101.0], [10.0]), pct_threshold=0.8)
    assert dist.bid_distance == 0.0
    assert dist.ask_distance == 0.0


def test_liquidity_distance_crosses_at_second_level() -> None:
    # bid: 100 (size=1), 99 (size=9); 80%=8 → need level 99 → distance=1
    dist = liquidity_distance(DepthProfile([100.0, 99.0], [1.0, 9.0], [101.0, 102.0], [1.0, 9.0]))
    assert dist.bid_distance == 1.0
    assert dist.ask_distance == 1.0


def test_liquidity_distance_exact_threshold_at_first_level() -> None:
    # 50% threshold, equal sizes → first level covers exactly 50%
    dist = liquidity_distance(DepthProfile([100.0, 99.0], [5.0, 5.0], [101.0, 102.0], [5.0, 5.0]), pct_threshold=0.5)
    assert dist.bid_distance == 0.0
    assert dist.ask_distance == 0.0


# ---- CancellationTracker ----

def test_cancel_tracker_no_events() -> None:
    rate = CancellationTracker().rate()
    assert rate.bid_pressure == 0.0
    assert rate.ask_pressure == 0.0


def test_cancel_tracker_all_adds_yields_negative_pressure() -> None:
    tracker = CancellationTracker(window=10)
    d = _delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0)
    tracker.update(d, best_bid_price=100.0, best_ask_price=101.0)
    tracker.update(d, best_bid_price=100.0, best_ask_price=101.0)
    # deleted=0, added=10 → (0-10)/10 = -1.0
    assert tracker.rate().bid_pressure == -1.0


def test_cancel_tracker_all_deletes_yields_positive_pressure() -> None:
    tracker = CancellationTracker(window=10)
    d = _delta(BookAction.DELETE, OrderSide.SELL, 101.0, 5.0)
    tracker.update(d, best_bid_price=100.0, best_ask_price=101.0)
    tracker.update(d, best_bid_price=100.0, best_ask_price=101.0)
    assert tracker.rate().ask_pressure == 1.0


def test_cancel_tracker_mixed_pressure() -> None:
    tracker = CancellationTracker(window=10)
    tracker.update(_delta(BookAction.ADD, OrderSide.BUY, 100.0, 4.0), 100.0, 101.0)
    tracker.update(_delta(BookAction.DELETE, OrderSide.BUY, 100.0, 8.0), 100.0, 101.0)
    # added=4, deleted=8, total=12 → (8-4)/12 = 1/3
    assert abs(tracker.rate().bid_pressure - 1 / 3) < 1e-9


def test_cancel_tracker_ignores_off_best_price() -> None:
    tracker = CancellationTracker(window=10)
    # ADD at 99, but best_bid is 100 — should not register
    tracker.update(_delta(BookAction.ADD, OrderSide.BUY, 99.0, 5.0), best_bid_price=100.0, best_ask_price=101.0)
    assert tracker.rate().bid_pressure == 0.0


def test_cancel_tracker_clear_resets_events() -> None:
    tracker = CancellationTracker(window=10)
    tracker.update(_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0), 100.0, 101.0)
    clear = OrderBookDelta(
        instrument_id=IID,
        action=BookAction.CLEAR,
        order=BookOrder(side=OrderSide.NO_ORDER_SIDE, price=Price(0, 1), size=Quantity(0, 1), order_id=0),
        flags=0, sequence=0, ts_event=0, ts_init=0,
    )
    tracker.update(clear, best_bid_price=None, best_ask_price=None)
    assert tracker.rate().bid_pressure == 0.0


def test_cancel_tracker_window_rolls_off_old_events() -> None:
    tracker = CancellationTracker(window=2)
    # Two ADDs then two DELETEs — first two ADDs fall off the window
    for _ in range(2):
        tracker.update(_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0), 100.0, 101.0)
    for _ in range(2):
        tracker.update(_delta(BookAction.DELETE, OrderSide.BUY, 100.0, 5.0), 100.0, 101.0)
    # Window=2, only the two DELETEs remain → pressure = 1.0
    assert tracker.rate().bid_pressure == 1.0


# ---- compute_features ----

def test_compute_features_populated_book() -> None:
    book = _book((100.0, 5.0), (99.0, 3.0), ask_levels=[(101.0, 4.0), (102.0, 6.0)])
    features = compute_features(book, CancellationTracker())
    assert features is not None
    assert features.depth.levels == 2
    assert 0.0 <= features.imbalance.aggregate <= 1.0
    assert features.cancel.bid_pressure == 0.0


def test_compute_features_empty_book_returns_none() -> None:
    assert compute_features(OrderBook(IID, BookType.L2_MBP), CancellationTracker()) is None


# ---- top_of_book_series crossed-book guard ----

def test_top_of_book_series_skips_crossed_state() -> None:
    """
    A synthetic-correction replay can transiently cross mid-batch (venue delta
    applied before its correction) -- top_of_book_series must not yield that tick,
    mirroring collector._second_loop's guard, or OFI/microprice get fed a negative
    spread.
    """
    deltas = [
        _delta(BookAction.ADD, OrderSide.BUY, 102.0, 1.0, ts=0),
        _delta(BookAction.ADD, OrderSide.SELL, 101.0, 1.0, ts=1),  # crossed: bid > ask
        _delta(BookAction.DELETE, OrderSide.SELL, 101.0, 0.0, ts=2),
        _delta(BookAction.ADD, OrderSide.SELL, 103.0, 1.0, ts=3),  # fixed: ask > bid
    ]
    rows = list(top_of_book_series(deltas, IID))
    assert all(bid_p < ask_p for _, bid_p, _, ask_p, _ in rows), (
        f"crossed tick leaked through: {rows}"
    )
    assert rows[-1][1] == 102.0
    assert rows[-1][3] == 103.0


def test_top_of_book_series_skips_touched_state() -> None:
    deltas = [
        _delta(BookAction.ADD, OrderSide.BUY, 100.0, 1.0, ts=0),
        _delta(BookAction.ADD, OrderSide.SELL, 100.0, 1.0, ts=1),  # zero spread
    ]
    rows = list(top_of_book_series(deltas, IID))
    assert rows == [], "zero-spread tick must be skipped, not yielded"


if __name__ == "__main__":
    test_depth_profile_extracts_correct_levels()
    test_depth_profile_empty_book_returns_none()
    test_depth_profile_clips_to_available_levels()
    test_depth_profile_totals()
    test_imbalance_balanced()
    test_imbalance_all_bid()
    test_imbalance_skewed_ask()
    test_imbalance_two_levels_aggregate()
    test_liquidity_distance_first_level_covers_threshold()
    test_liquidity_distance_crosses_at_second_level()
    test_liquidity_distance_exact_threshold_at_first_level()
    test_cancel_tracker_no_events()
    test_cancel_tracker_all_adds_yields_negative_pressure()
    test_cancel_tracker_all_deletes_yields_positive_pressure()
    test_cancel_tracker_mixed_pressure()
    test_cancel_tracker_ignores_off_best_price()
    test_cancel_tracker_clear_resets_events()
    test_cancel_tracker_window_rolls_off_old_events()
    test_compute_features_populated_book()
    test_compute_features_empty_book_returns_none()
    test_top_of_book_series_skips_crossed_state()
    test_top_of_book_series_skips_touched_state()
    print("ok")
