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
"""Self-check: footprint cells match a hand-computed add/remove/net example."""

from ml_signals.candles import Candle
from ml_signals.footprint import build_footprint
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


INSTRUMENT_ID = InstrumentId.from_str("TEST-PERP.SIM")
ONE_SECOND = 1_000_000_000


def _delta(
    action: BookAction, side: OrderSide, price: float, size: float, ts_event: int
) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    return OrderBookDelta(
        instrument_id=INSTRUMENT_ID,
        action=action,
        order=order,
        flags=0,
        sequence=0,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def test_matches_worked_example_added_removed_net() -> None:
    # One 60s candle (bucket 1: [60s, 120s)) spanning price [100, 200], split
    # into 4 bands of 25 each: band 0 = [100,125), band 1 = [125,150),
    # band 2 = [150,175), band 3 = [175,200].
    candle = Candle(ts_open=60 * ONE_SECOND, open=150.0, high=200.0, low=100.0, close=150.0)

    deltas = [
        # Pre-existing ask resting size at 160 (band 2), added in the *prior*
        # bucket (no candle there) — only its later removal counts toward this candle.
        _delta(BookAction.ADD, OrderSide.SELL, price=160.0, size=80.0, ts_event=55 * ONE_SECOND),
        # Bid side, band 0: +100 added, then reduced by 40 -> net +60.
        _delta(BookAction.ADD, OrderSide.BUY, price=110.0, size=100.0, ts_event=70 * ONE_SECOND),
        _delta(BookAction.UPDATE, OrderSide.BUY, price=110.0, size=60.0, ts_event=80 * ONE_SECOND),
        # Ask side, band 2: +20 added; the pre-existing 80 gets fully removed -> net -60.
        _delta(BookAction.ADD, OrderSide.SELL, price=165.0, size=20.0, ts_event=68 * ONE_SECOND),
        _delta(BookAction.DELETE, OrderSide.SELL, price=160.0, size=0.0, ts_event=75 * ONE_SECOND),
    ]

    cells = build_footprint(deltas, candles=[candle], period_seconds=60, bands_per_candle=4)
    by_band = {round((c.price_low - 100.0) / 25): c for c in cells}

    bid_band = by_band[0]
    assert bid_band.bid_added == 100.0
    assert bid_band.bid_removed == 40.0
    assert bid_band.bid_net == 60.0
    assert bid_band.ask_added == 0.0

    ask_band = by_band[2]
    assert ask_band.ask_added == 20.0
    assert ask_band.ask_removed == 80.0
    assert ask_band.ask_net == -60.0
    assert ask_band.bid_added == 0.0


def test_clear_resets_state_with_no_contribution() -> None:
    candle = Candle(ts_open=0, open=150.0, high=200.0, low=100.0, close=150.0)
    deltas = [
        _delta(BookAction.ADD, OrderSide.BUY, price=110.0, size=50.0, ts_event=1 * ONE_SECOND),
        OrderBookDelta(
            instrument_id=INSTRUMENT_ID,
            action=BookAction.CLEAR,
            order=BookOrder(
                side=OrderSide.NO_ORDER_SIDE, price=Price(0, 1), size=Quantity(0, 1), order_id=0
            ),
            flags=0,
            sequence=0,
            ts_event=2 * ONE_SECOND,
            ts_init=2 * ONE_SECOND,
        ),
        # Re-added after the clear: prior state is gone, so this is a fresh +30, not a -20 update.
        _delta(BookAction.ADD, OrderSide.BUY, price=110.0, size=30.0, ts_event=3 * ONE_SECOND),
    ]

    cells = build_footprint(deltas, candles=[candle], period_seconds=60, bands_per_candle=4)
    bid_band = next(c for c in cells if c.price_low == 100.0)

    assert bid_band.bid_added == 80.0  # 50 + 30, not 50 + (30 - 50)
    assert bid_band.bid_removed == 0.0


if __name__ == "__main__":
    test_matches_worked_example_added_removed_net()
    test_clear_resets_state_with_no_contribution()
    print("ok")
