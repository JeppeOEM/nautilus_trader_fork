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
One stored second snapshot as Nautilus market data (Story 31.9): the one conversion of a
`DydxSecondSnapshot` row, or its level 0 (`kernel.catalog_files.TopOfBook`), into the book and
quote a backtest replays.

- `snapshot_deltas`: the row's whole stored book as one `OrderBookDeltas` -- a `CLEAR`, then one
  `ADD` per stored level, bids then asks, best first. Every delta carries `F_SNAPSHOT` and the
  final one also `F_LAST`, so a `DataEngine` applies the row as one atomic book replacement.
- `snapshot_quote` / `top_quote`: one `QuoteTick` from the row's top of book. `top_quote` is the
  one per-row quote derivation; `research.application.quotes.derived_quotes` (the snapshot
  backtest's simulated market) and `bots.signal_replay` both build their quotes through it
  (SSOT-01, never a second copy).

Exactness (NAUT-01, Story 30.2): every price and size is built from the row's stored integer units
at the row's own precisions (`kernel.second_snapshot.price_of`/`quantity_of`, i.e.
`Price.from_raw`/`Quantity.from_raw`), never through a float. A row stored at other precisions than
the instrument definition the backtest trades is refused (`ValueError`), never re-rounded: the
simulated exchange itself refuses a delta or quote at another precision.

Timestamps: `snapshot_deltas` and `snapshot_quote` stamp `ts_event = ts_init =` the row's
`ts_init`, the moment the row could first be known and the clock a backtest replays on
(`docs/DATA_DICTIONARY.md` §1.7); stamping the exchange's `ts_event` would let a backtest act on a
book before it could have been seen. `top_quote` takes the `ts_event` to stamp explicitly, because
the research snapshot backtest keeps the row's own `ts_event` on its quotes.

Empty sides: a one-sided row still yields its `CLEAR` plus the present side (the book really held
only that side, and replacing it with the previous second's book would invent levels); a row with
both sides empty yields the `CLEAR` alone, flagged `F_LAST`. A quote needs both sides, so a row
with an empty side has no quote (`snapshot_quote` returns None) -- the same rule
`kernel.second_snapshot.top_of_book_units` applies when it omits such a row.
"""

from kernel.catalog_files import TopOfBook
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import price_of
from kernel.second_snapshot import quantity_of
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import RecordFlag
from nautilus_trader.model.instruments import Instrument


def require_definition_precision(
    instrument: Instrument, price_precision: int, size_precision: int, ts_event: int
) -> None:
    """
    Refuse a row stored at other precisions than the definition the backtest trades: its exact
    values are used as stored (Story 30.2), never re-rounded with `make_price`.
    """
    stored = (price_precision, size_precision)
    defined = (instrument.price_precision, instrument.size_precision)
    if stored != defined:
        raise ValueError(
            f"{instrument.id} snapshot at ts_event {ts_event} is stored at price/size "
            f"precision {stored}, the instrument definition says {defined}"
        )


def top_quote(instrument: Instrument, top: TopOfBook, ts_event: int) -> QuoteTick:
    """
    Return the one per-row quote: `top`'s exact stored level 0, stamped `ts_event` and the row's
    `ts_init`. Refuses a top stored at other precisions than `instrument`'s.
    """
    require_definition_precision(
        instrument, top.bid_price.precision, top.bid_size.precision, top.ts_event
    )
    return QuoteTick(
        instrument_id=instrument.id,
        bid_price=top.bid_price,
        ask_price=top.ask_price,
        bid_size=top.bid_size,
        ask_size=top.ask_size,
        ts_event=ts_event,
        ts_init=top.ts_init,
    )


def top_of_snapshot(row: DydxSecondSnapshot) -> TopOfBook | None:
    """Return the row's exact level 0 on both sides; None when a side is empty (no top)."""
    if not row.bid_price_units or not row.ask_price_units:
        return None
    pp, sp = row.price_precision, row.size_precision
    return TopOfBook(
        row.ts_event,
        row.ts_init,
        price_of(row.bid_price_units[0], pp),
        quantity_of(row.bid_size_units[0], sp),
        price_of(row.ask_price_units[0], pp),
        quantity_of(row.ask_size_units[0], sp),
    )


def snapshot_quote(instrument: Instrument, row: DydxSecondSnapshot) -> QuoteTick | None:
    """Return the row's top-of-book quote stamped at its `ts_init`; None when a side is empty."""
    top = top_of_snapshot(row)
    if top is None:
        return None
    return top_quote(instrument, top, row.ts_init)


def snapshot_deltas(instrument: Instrument, row: DydxSecondSnapshot) -> OrderBookDeltas:
    """
    Return the row's stored book as one atomic replacement: `CLEAR`, then every stored level (bids, then
    asks, best first) as an `ADD` at its exact stored price and size, all stamped at the row's
    `ts_init`; `F_SNAPSHOT` on every delta, `F_LAST` on the final one.
    """
    require_definition_precision(instrument, row.price_precision, row.size_precision, row.ts_event)
    pp, sp = row.price_precision, row.size_precision
    orders = [
        BookOrder(side, price_of(price, pp), quantity_of(size, sp), 0)
        for side, prices, sizes in (
            (OrderSide.BUY, row.bid_price_units, row.bid_size_units),
            (OrderSide.SELL, row.ask_price_units, row.ask_size_units),
        )
        for price, size in zip(prices, sizes, strict=True)
    ]
    actions: list[tuple[BookAction, BookOrder | None]] = [(BookAction.CLEAR, None)]
    actions += [(BookAction.ADD, order) for order in orders]
    last = len(actions) - 1
    deltas = [
        OrderBookDelta(
            instrument.id,
            action,
            order,
            _flags(position == last),
            0,
            row.ts_init,
            row.ts_init,
        )
        for position, (action, order) in enumerate(actions)
    ]
    return OrderBookDeltas(instrument.id, deltas)


def _flags(is_last: bool) -> int:
    snapshot = int(RecordFlag.F_SNAPSHOT)
    return snapshot | int(RecordFlag.F_LAST) if is_last else snapshot
