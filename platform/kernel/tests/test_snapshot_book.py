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
`kernel.snapshot_book` (Story 31.9): one stored snapshot row as `OrderBookDeltas` and a
`QuoteTick`, exact from the stored integers, applied to a real Nautilus `OrderBook`.
"""

import pytest

from kernel.catalog_files import TopOfBook
from kernel.clocks import NS_PER_S
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.snapshot_book import snapshot_deltas
from kernel.snapshot_book import snapshot_quote
from kernel.snapshot_book import top_quote
from kernel.tests.snapshot_factory import make_snapshot
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import RecordFlag
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider


_T_EVENT = 1_790_000_000 * NS_PER_S
_T_INIT = _T_EVENT + 1_300_000_000  # sampled 1.3 s after the exchange's second
_F_SNAPSHOT = int(RecordFlag.F_SNAPSHOT)
_F_LAST = int(RecordFlag.F_LAST)


def _instrument() -> Instrument:
    return TestInstrumentProvider.btcusdt_perp_binance()  # price precision 1, size precision 3


def _row(
    bids: tuple[tuple[str, str], ...] = (("61090.5", "1.250"), ("61090.4", "0.003")),
    asks: tuple[tuple[str, str], ...] = (("61090.6", "2.000"), ("61091.0", "12.345")),
    price_precision: int = 1,
) -> DydxSecondSnapshot:
    return make_snapshot(
        instrument_id="BTCUSDT-PERP.BINANCE",
        bid_prices=[p for p, _ in bids],
        bid_sizes=[s for _, s in bids],
        ask_prices=[p for p, _ in asks],
        ask_sizes=[s for _, s in asks],
        ts_event=_T_EVENT,
        ts_init=_T_INIT,
        price_precision=price_precision,
        size_precision=3,
    )


def _book(row: DydxSecondSnapshot) -> OrderBook:
    instrument = _instrument()
    book = OrderBook(instrument.id, BookType.L2_MBP)
    book.apply_deltas(snapshot_deltas(instrument, row))
    return book


def test_the_deltas_are_a_clear_then_every_stored_level_best_first() -> None:
    deltas = snapshot_deltas(_instrument(), _row()).deltas

    assert [(d.action, d.order.side) for d in deltas] == [
        (BookAction.CLEAR, OrderSide.NO_ORDER_SIDE),
        (BookAction.ADD, OrderSide.BUY),
        (BookAction.ADD, OrderSide.BUY),
        (BookAction.ADD, OrderSide.SELL),
        (BookAction.ADD, OrderSide.SELL),
    ]
    assert [(str(d.order.price), str(d.order.size)) for d in deltas[1:]] == [
        ("61090.5", "1.250"),
        ("61090.4", "0.003"),
        ("61090.6", "2.000"),
        ("61091.0", "12.345"),
    ]


def test_every_delta_is_a_snapshot_and_only_the_final_one_is_last() -> None:
    deltas = snapshot_deltas(_instrument(), _row()).deltas

    assert [d.flags for d in deltas] == [_F_SNAPSHOT] * 4 + [_F_SNAPSHOT | _F_LAST]


def test_the_deltas_and_the_quote_are_stamped_at_the_rows_ts_init() -> None:
    instrument, row = _instrument(), _row()
    quote = snapshot_quote(instrument, row)

    assert {(d.ts_event, d.ts_init) for d in snapshot_deltas(instrument, row).deltas} == {
        (_T_INIT, _T_INIT)
    }
    assert quote is not None
    assert (quote.ts_event, quote.ts_init) == (_T_INIT, _T_INIT)


def test_prices_and_sizes_are_the_stored_integers_exactly() -> None:
    row = _row()
    deltas = snapshot_deltas(_instrument(), row).deltas[1:]
    price_step = 10 ** (FIXED_PRECISION - row.price_precision)
    size_step = 10 ** (FIXED_PRECISION - row.size_precision)

    assert [d.order.price.raw for d in deltas] == [
        u * price_step for u in row.bid_price_units + row.ask_price_units
    ]
    assert [d.order.size.raw for d in deltas] == [
        u * size_step for u in row.bid_size_units + row.ask_size_units
    ]
    assert {(d.order.price.precision, d.order.size.precision) for d in deltas} == {(1, 3)}


def test_the_applied_book_holds_exactly_the_stored_levels() -> None:
    row = _row()
    book = _book(row)
    exact = row.exact

    # Compared as Nautilus values, not as the row's `unit_float`s: `Price.as_double()` (what a
    # strategy reads off the book) may differ from `units / 10**p` in the last bit.
    assert [(level.price, level.size()) for level in book.bids()] == [
        (p, q.as_double()) for p, q in zip(exact.bid_prices, exact.bid_sizes, strict=True)
    ]
    assert [(level.price, level.size()) for level in book.asks()] == [
        (p, q.as_double()) for p, q in zip(exact.ask_prices, exact.ask_sizes, strict=True)
    ]


def test_a_later_row_replaces_the_whole_book() -> None:
    instrument = _instrument()
    book = _book(_row())
    book.apply_deltas(snapshot_deltas(instrument, _row(bids=(("61089.0", "0.500"),))))

    assert [str(level.price) for level in book.bids()] == ["61089.0"]
    assert [str(level.price) for level in book.asks()] == ["61090.6", "61091.0"]


def test_a_one_sided_row_yields_its_side_and_no_quote() -> None:
    instrument, row = _instrument(), _row(asks=())
    book = _book(row)

    assert [str(level.price) for level in book.bids()] == ["61090.5", "61090.4"]
    assert book.asks() == []
    assert snapshot_quote(instrument, row) is None


def test_an_empty_row_is_a_last_clear_alone() -> None:
    (clear,) = snapshot_deltas(_instrument(), _row(bids=(), asks=())).deltas

    assert (clear.action, clear.flags) == (BookAction.CLEAR, _F_SNAPSHOT | _F_LAST)


def test_the_quote_is_the_rows_exact_top_of_book() -> None:
    quote = snapshot_quote(_instrument(), _row())

    assert quote is not None
    assert (quote.bid_price, quote.bid_size, quote.ask_price, quote.ask_size) == (
        Price.from_str("61090.5"),
        Quantity.from_str("1.250"),
        Price.from_str("61090.6"),
        Quantity.from_str("2.000"),
    )


def test_top_quote_keeps_the_given_ts_event_and_the_tops_ts_init() -> None:
    size = Quantity.from_str("1.000")
    top = TopOfBook(_T_EVENT, _T_INIT, Price.from_str("1.0"), size, Price.from_str("1.1"), size)

    quote = top_quote(_instrument(), top, _T_EVENT)

    assert (quote.ts_event, quote.ts_init) == (_T_EVENT, _T_INIT)


def test_a_row_at_another_precision_than_the_definition_is_refused() -> None:
    finer = _row(bids=(("61090.55", "1.000"),), asks=(("61090.65", "1.000"),), price_precision=2)

    with pytest.raises(ValueError, match="precision"):
        snapshot_deltas(_instrument(), finer)
    with pytest.raises(ValueError, match="precision"):
        snapshot_quote(_instrument(), finer)
