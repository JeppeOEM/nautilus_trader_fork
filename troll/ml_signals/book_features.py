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
Replay L2 order book deltas into top-of-book snapshots.

dYdX (like most venues nautilus_trader supports via deltas rather than a
native L1 feed) has no QuoteTick stream, so Microprice/OrderFlowImbalance
need their bid/ask inputs reconstructed by replaying deltas through an
OrderBook. A live Strategy does the same thing in `on_order_book_deltas`.
"""

from collections.abc import Iterator

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId


def top_of_book_series(
    deltas: list[OrderBookDelta],
    instrument_id: InstrumentId,
) -> Iterator[tuple[int, float, float, float, float]]:
    """Yield (ts_event, bid_price, bid_size, ask_price, ask_size) after each delta, once both sides exist."""
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
