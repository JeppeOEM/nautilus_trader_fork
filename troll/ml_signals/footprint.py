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
Footprint chart cells: per-candle, per-price-band order-book flow.

For each candle, the candle's own [low, high] range is split into
`bands_per_candle` horizontal bands. Each band accumulates the gross resting
size added and removed on the bid side and ask side during that candle's
time window, from order_book_deltas.

This is *resting-order* flow, not executed trade volume. dYdX's deltas are L2
market-by-price with no order IDs (every `BookOrder.order_id` is 0), so a
level shrinking looks identical whether it was canceled or filled by a trade
— there's no way to tell those apart from deltas alone. "Removed" means
"gross resting-size decrease," not a confirmed cancel. Gross added/removed
are tracked separately (not just net) so a churning level (e.g. +100/-40)
doesn't look identical to a quiet one (+60/0) when both net to +60.

ponytail: bands are sized relative to each candle's own high-low range, so
adjacent candles' bands don't line up at the same absolute price (a textbook
footprint chart usually fixes one global price step instead, so rows align
across the whole chart). Switch `bands_per_candle` for a shared `price_step`
if cross-candle price alignment turns out to matter.
"""

from dataclasses import dataclass

from ml_signals.candles import Candle
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide


@dataclass
class FootprintCell:
    ts_open: int
    price_low: float
    price_high: float
    bid_added: float = 0.0
    bid_removed: float = 0.0
    ask_added: float = 0.0
    ask_removed: float = 0.0

    @property
    def bid_net(self) -> float:
        return self.bid_added - self.bid_removed

    @property
    def ask_net(self) -> float:
        return self.ask_added - self.ask_removed


def build_footprint(
    deltas: list[OrderBookDelta],
    candles: list[Candle],
    period_seconds: int,
    bands_per_candle: int = 4,
) -> list[FootprintCell]:
    period_ns = period_seconds * 1_000_000_000
    candle_by_bucket = {candle.ts_open // period_ns: candle for candle in candles}
    cells: dict[tuple[int, int], FootprintCell] = {}

    bid_levels: dict[float, float] = {}
    ask_levels: dict[float, float] = {}

    for delta in sorted(deltas, key=lambda d: d.ts_init):
        if delta.action == BookAction.CLEAR:
            bid_levels.clear()
            ask_levels.clear()
            continue

        levels = bid_levels if delta.order.side == OrderSide.BUY else ask_levels
        price = delta.order.price.as_double()
        prev = levels.get(price, 0.0)

        if delta.action == BookAction.DELETE:
            new = 0.0
            levels.pop(price, None)
        else:  # ADD or UPDATE: order.size is the absolute new resting size
            new = delta.order.size.as_double()
            levels[price] = new

        change = new - prev
        if change == 0.0:
            continue

        candle = candle_by_bucket.get(delta.ts_event // period_ns)
        if candle is None or candle.high == candle.low:
            continue
        if not (candle.low <= price <= candle.high):
            continue

        band_height = (candle.high - candle.low) / bands_per_candle
        band_index = int((price - candle.low) / band_height)
        band_index = min(band_index, bands_per_candle - 1)
        price_low = candle.low + band_index * band_height
        cell_key = (candle.ts_open, band_index)
        cell = cells.setdefault(
            cell_key,
            FootprintCell(
                ts_open=candle.ts_open, price_low=price_low, price_high=price_low + band_height
            ),
        )

        is_bid = delta.order.side == OrderSide.BUY
        if change > 0.0:
            if is_bid:
                cell.bid_added += change
            else:
                cell.ask_added += change
        else:
            if is_bid:
                cell.bid_removed += -change
            else:
                cell.ask_removed += -change

    return sorted(cells.values(), key=lambda cell: (cell.ts_open, cell.price_low))
