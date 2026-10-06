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
Unit tests for `views.chart_series.CancellationTracker`, what remains of the book-features module
(Story 33.4 deleted its caller-less depth/imbalance/feature code and moved `liquidity_distance`'s
tests to `kernel/tests/test_indicators_zscore_depth.py` with the function).
"""

from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from views.chart_series import CancellationTracker


IID = InstrumentId.from_str("TEST-PERP.SIM")


def _delta(
    action: BookAction, side: OrderSide, price: float, size: float, ts: int = 0
) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    return OrderBookDelta(
        instrument_id=IID, action=action, order=order, flags=0, sequence=0, ts_event=ts, ts_init=ts
    )


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
    tracker.update(
        _delta(BookAction.ADD, OrderSide.BUY, 99.0, 5.0), best_bid_price=100.0, best_ask_price=101.0
    )
    assert tracker.rate().bid_pressure == 0.0


def test_cancel_tracker_clear_resets_events() -> None:
    tracker = CancellationTracker(window=10)
    tracker.update(_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0), 100.0, 101.0)
    clear = OrderBookDelta(
        instrument_id=IID,
        action=BookAction.CLEAR,
        order=BookOrder(
            side=OrderSide.NO_ORDER_SIDE, price=Price(0, 1), size=Quantity(0, 1), order_id=0
        ),
        flags=0,
        sequence=0,
        ts_event=0,
        ts_init=0,
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
