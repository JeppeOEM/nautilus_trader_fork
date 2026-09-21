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
Plausibility check for a second's trade OHLC against that same second's order book.

A trade executes against resting liquidity, so its price must lie inside the book's
visible depth: no higher than the deepest ask level and no lower than the deepest bid
level (a sweep that consumes levels only lands prices *between* the pre-trade levels,
which the stored top-20 depth still brackets). A high/low outside that range cannot come
from that second's real trading -- it is the signature of replayed history (dYdX's
`v4_trades` subscribed reply, dropped by `config.stale_trade_seconds`) or another
ingestion bug.
Derived purely from stored fields, so it works identically as a live canary and as a
scan over old catalog data (see repair_catalog.py).
"""

from typing import Protocol


# Book depth moves within the second we sample it; allow 0.1% before calling it impossible.
TOLERANCE = 0.001


class _Snapshot(Protocol):
    bid_prices: list[float]
    ask_prices: list[float]
    high_price: float | None
    low_price: float | None


def ohlc_outside_book(snapshot: _Snapshot, tolerance: float = TOLERANCE) -> bool:
    if snapshot.high_price is None or snapshot.low_price is None:
        return False
    if not snapshot.bid_prices or not snapshot.ask_prices:
        return False  # no book to judge against -- not evidence either way
    deepest_ask = max(snapshot.ask_prices)
    deepest_bid = min(snapshot.bid_prices)
    return snapshot.high_price > deepest_ask * (
        1 + tolerance
    ) or snapshot.low_price < deepest_bid * (1 - tolerance)
