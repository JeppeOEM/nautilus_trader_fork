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
L2 order book feature extraction for dYdX.

NOTE on what dYdX L2 can and cannot tell you:
  - dYdX sends aggregated price-level data (order_id=0 for every BookOrder).
  - Each BookLevel has exactly one synthetic BookOrder = the total size at that price.
  - Queue composition (how many individual orders make up a level) is NOT available.
  - Large single-order presence within a level is NOT available.
  - Both require L3/MBO data which dYdX does not expose publicly.

What IS available and implemented here:
  - Depth profile levels 1-4 (sizes and prices on both sides)
  - Book imbalance per level and aggregate across levels 1-4
  - Volume-weighted price distance to liquidity (how far 80% of depth sits)
  - Cancellation rate at best levels (tracked via delta ADD/DELETE actions)
"""

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId


# ---------------------------------------------------------------------------
# Existing helper — unchanged
# ---------------------------------------------------------------------------

def top_of_book_series(
    deltas: list[OrderBookDelta],
    instrument_id: InstrumentId,
) -> Iterator[tuple[int, float, float, float, float]]:
    """Yield (ts_event, bid_price, bid_size, ask_price, ask_size) after each delta."""
    book = OrderBook(instrument_id, book_type=BookType.L2_MBP)
    for delta in sorted(deltas, key=lambda d: d.ts_init):
        book.apply_delta(delta)
        bid_price = book.best_bid_price()
        ask_price = book.best_ask_price()
        if bid_price is None or ask_price is None:
            continue
        yield (
            delta.ts_event,
            bid_price.as_double(),
            book.best_bid_size().as_double(),
            ask_price.as_double(),
            book.best_ask_size().as_double(),
        )


# ---------------------------------------------------------------------------
# Depth snapshot
# ---------------------------------------------------------------------------

@dataclass
class DepthProfile:
    """Sizes and prices at the top N levels on both sides."""
    bid_prices: list[float]   # index 0 = best bid
    bid_sizes:  list[float]
    ask_prices: list[float]   # index 0 = best ask
    ask_sizes:  list[float]

    @property
    def levels(self) -> int:
        return len(self.bid_prices)

    def total_bid_depth(self) -> float:
        return sum(self.bid_sizes)

    def total_ask_depth(self) -> float:
        return sum(self.ask_sizes)


def depth_profile(book: OrderBook, levels: int = 4) -> DepthProfile | None:
    """
    Extract size and price at the top `levels` levels on each side.

    Returns None if either side has no quotes (book not yet initialised).
    """
    bids = book.bids()
    asks = book.asks()
    if not bids or not asks:
        return None

    bid_prices = [lv.price.as_double() for lv in bids[:levels]]
    bid_sizes  = [lv.size()            for lv in bids[:levels]]
    ask_prices = [lv.price.as_double() for lv in asks[:levels]]
    ask_sizes  = [lv.size()            for lv in asks[:levels]]

    return DepthProfile(bid_prices, bid_sizes, ask_prices, ask_sizes)


# ---------------------------------------------------------------------------
# Book imbalance
# ---------------------------------------------------------------------------

@dataclass
class BookImbalance:
    """
    Bid-to-total imbalance at each level and aggregated.

    Value of 1.0 = all depth on the bid side. 0.5 = balanced. 0.0 = all ask.
    """
    per_level: list[float]   # one entry per level; index 0 = best
    aggregate: float         # imbalance across all levels combined


def book_imbalance(profile: DepthProfile) -> BookImbalance:
    per = []
    for b, a in zip(profile.bid_sizes, profile.ask_sizes):
        total = b + a
        per.append(b / total if total > 0 else 0.5)

    total_bid = sum(profile.bid_sizes)
    total_ask = sum(profile.ask_sizes)
    total     = total_bid + total_ask
    agg       = total_bid / total if total > 0 else 0.5

    return BookImbalance(per_level=per, aggregate=agg)


# ---------------------------------------------------------------------------
# Volume-weighted price distance to liquidity
# ---------------------------------------------------------------------------

@dataclass
class LiquidityDistance:
    """
    How far from the best price the meaningful liquidity sits.

    `distance_to_pct` is the price distance (in ticks / absolute price units)
    you must travel from the best to capture `pct_threshold` fraction of the
    available depth on that side.

    A small distance means support/resistance is close and dense.
    A large distance means there's a vacuum — price can move fast and far.
    """
    bid_distance: float   # price distance to capture pct_threshold of bid depth
    ask_distance: float


def liquidity_distance(
    profile: DepthProfile,
    pct_threshold: float = 0.8,
) -> LiquidityDistance:
    def _dist(best_price: float, prices: list[float], sizes: list[float]) -> float:
        total = sum(sizes)
        if total == 0:
            return 0.0
        target = total * pct_threshold
        cumulative = 0.0
        for price, size in zip(prices, sizes):
            cumulative += size
            if cumulative >= target:
                return abs(price - best_price)
        # All levels consumed and still below threshold — return distance to deepest level
        return abs(prices[-1] - best_price) if prices else 0.0

    bid_dist = _dist(profile.bid_prices[0], profile.bid_prices, profile.bid_sizes)
    ask_dist = _dist(profile.ask_prices[0], profile.ask_prices, profile.ask_sizes)
    return LiquidityDistance(bid_distance=bid_dist, ask_distance=ask_dist)


# ---------------------------------------------------------------------------
# Cancellation rate tracker
# ---------------------------------------------------------------------------

@dataclass
class CancelRate:
    """
    Ratio of size being pulled vs added at the best bid and ask.

    Positive = size being added net (level building, stable).
    Negative = size being pulled net (level depleting, fragile).

    cancel_pressure = (deleted_size - added_size) / (deleted_size + added_size)
    Range: -1 (all additions) to +1 (all cancellations).
    """
    bid_pressure: float   # positive = bids being cancelled
    ask_pressure: float


class CancellationTracker:
    """
    Stateful tracker for add/delete size events at the best price level.

    Call `update(delta, best_bid_price, best_ask_price)` on every incoming
    delta before applying it to the OrderBook (so you can compare against
    the *current* best price, not the post-delta best).

    Uses a rolling event window so old events expire as market conditions change.
    """

    def __init__(self, window: int = 200) -> None:
        # (side, action, size) for the last `window` events at best levels
        self._events: deque[tuple[str, str, float]] = deque(maxlen=window)

    def update(
        self,
        delta: OrderBookDelta,
        best_bid_price: float | None,
        best_ask_price: float | None,
    ) -> None:
        if delta.action == BookAction.CLEAR:
            self._events.clear()
            return
        # UPDATE = size change at existing level; ambiguous direction — skip.
        # Only track ADD (new level appears at best) and DELETE (level pulled entirely).
        if delta.action not in (BookAction.ADD, BookAction.DELETE):
            return

        delta_price = delta.order.price.as_double()
        delta_size  = delta.order.size.as_double()
        action_str  = "add" if delta.action == BookAction.ADD else "delete"

        if delta.order.side == OrderSide.BUY and best_bid_price is not None:
            if delta_price == best_bid_price:
                self._events.append(("bid", action_str, delta_size))
        elif delta.order.side == OrderSide.SELL and best_ask_price is not None:
            if delta_price == best_ask_price:
                self._events.append(("ask", action_str, delta_size))

    def rate(self) -> CancelRate:
        def _pressure(side: str) -> float:
            added   = sum(sz for s, a, sz in self._events if s == side and a == "add")
            deleted = sum(sz for s, a, sz in self._events if s == side and a == "delete")
            total   = added + deleted
            if total == 0:
                return 0.0
            # ponytail: (deleted - added) / total so positive = net cancellation pressure
            return (deleted - added) / total

        return CancelRate(
            bid_pressure=_pressure("bid"),
            ask_pressure=_pressure("ask"),
        )


# ---------------------------------------------------------------------------
# Convenience: compute all features in one call
# ---------------------------------------------------------------------------

@dataclass
class BookFeatures:
    depth:      DepthProfile
    imbalance:  BookImbalance
    liquidity:  LiquidityDistance
    cancel:     CancelRate


def compute_features(
    book: OrderBook,
    cancel_tracker: CancellationTracker,
    levels: int = 4,
    liquidity_pct: float = 0.8,
) -> BookFeatures | None:
    profile = depth_profile(book, levels)
    if profile is None:
        return None
    return BookFeatures(
        depth=profile,
        imbalance=book_imbalance(profile),
        liquidity=liquidity_distance(profile, liquidity_pct),
        cancel=cancel_tracker.rate(),
    )
