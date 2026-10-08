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
Test helper: build `DydxSecondSnapshot`s and their wire dicts from decimal literals (Story 30.2).

Every test writes a snapshot the way a reader thinks of it (`bid_prices=[100.5, 100.3]`); this
converts each literal to units exactly with `Decimal(str(x)).scaleb(precision)` at the EXPLICIT
precisions given (defaults `DEFAULT_PRICE_PRECISION`/`DEFAULT_SIZE_PRECISION`), never through float
arithmetic. A literal with more digits than its precision raises `ValueError`, so a test can never
silently store a value other than the one it wrote.
"""

from collections.abc import Sequence
from decimal import Decimal

from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import SnapshotTradeUnits
from nautilus_trader.model.identifiers import InstrumentId


DEFAULT_PRICE_PRECISION = 4
DEFAULT_SIZE_PRECISION = 4

Number = int | float | str | Decimal


def units(value: Number, precision: int) -> int:
    """Return `value` (a literal) in units of `10^-precision`, exactly; raises if it has more digits."""
    scaled = Decimal(str(value)).scaleb(precision)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value!r} is not exact at precision {precision}")
    return int(scaled)


def _optional_units(value: Number | None, precision: int) -> int | None:
    return None if value is None else units(value, precision)


def make_snapshot(
    instrument_id: str | InstrumentId = "BTC-USD-PERP.DYDX",
    bid_prices: Sequence[Number] = (),
    bid_sizes: Sequence[Number] = (),
    ask_prices: Sequence[Number] = (),
    ask_sizes: Sequence[Number] = (),
    buy_volume: Number = 0,
    sell_volume: Number = 0,
    buy_count: int = 0,
    sell_count: int = 0,
    ts_event: int = 0,
    ts_init: int | None = None,
    open_price: Number | None = None,
    high_price: Number | None = None,
    low_price: Number | None = None,
    close_price: Number | None = None,
    price_precision: int = DEFAULT_PRICE_PRECISION,
    size_precision: int = DEFAULT_SIZE_PRECISION,
    trades: SnapshotTradeUnits | None = None,
) -> DydxSecondSnapshot:
    """
    Build a snapshot from literals (absolute level prices, best first); `ts_init` defaults to
    `ts_event`.
    `trades` (units, e.g. `fold_trades(...).snapshot_units(p, s)`) replaces the eight trade fields.
    """
    iid = (
        instrument_id
        if isinstance(instrument_id, InstrumentId)
        else InstrumentId.from_str(instrument_id)
    )
    pp, sp = price_precision, size_precision
    if trades is not None:
        return _with_trades(
            iid,
            pp,
            sp,
            bid_prices,
            bid_sizes,
            ask_prices,
            ask_sizes,
            trades,
            ts_event,
            ts_event if ts_init is None else ts_init,
        )
    return DydxSecondSnapshot(
        instrument_id=iid,
        price_precision=pp,
        size_precision=sp,
        bid_price_units=[units(v, pp) for v in bid_prices],
        bid_size_units=[units(v, sp) for v in bid_sizes],
        ask_price_units=[units(v, pp) for v in ask_prices],
        ask_size_units=[units(v, sp) for v in ask_sizes],
        buy_volume_units=units(buy_volume, sp),
        sell_volume_units=units(sell_volume, sp),
        buy_count=buy_count,
        sell_count=sell_count,
        ts_event=ts_event,
        ts_init=ts_event if ts_init is None else ts_init,
        open_price_units=_optional_units(open_price, pp),
        high_price_units=_optional_units(high_price, pp),
        low_price_units=_optional_units(low_price, pp),
        close_price_units=_optional_units(close_price, pp),
    )


def second_of(snapshot: DydxSecondSnapshot) -> SecondOHLC:
    """Project a snapshot onto the fold's per-second fields (its floats, then its units)."""
    return SecondOHLC(
        snapshot.ts_event,
        snapshot.open_price,
        snapshot.high_price,
        snapshot.low_price,
        snapshot.close_price,
        snapshot.buy_volume,
        snapshot.sell_volume,
        snapshot.price_precision,
        snapshot.size_precision,
        snapshot.close_price_units,
        snapshot.buy_volume_units,
        snapshot.sell_volume_units,
        snapshot.buy_count,
        snapshot.sell_count,
    )


def make_second(
    ts_event: int,
    close_price: Number | None = None,
    *,
    open_price: Number | None = None,
    high_price: Number | None = None,
    low_price: Number | None = None,
    buy_volume: Number = 0,
    sell_volume: Number = 0,
    buy_count: int = 0,
    sell_count: int = 0,
    price_precision: int = DEFAULT_PRICE_PRECISION,
    size_precision: int = DEFAULT_SIZE_PRECISION,
) -> SecondOHLC:
    """
    Build one `SecondOHLC` from literals, its units exact at the given precisions (`units`) and its
    floats decoded from them as the catalog read decodes them. `open/high/low` default to the close.
    """
    return second_of(
        make_snapshot(
            ts_event=ts_event,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            buy_count=buy_count,
            sell_count=sell_count,
            open_price=close_price if open_price is None else open_price,
            high_price=close_price if high_price is None else high_price,
            low_price=close_price if low_price is None else low_price,
            close_price=close_price,
            price_precision=price_precision,
            size_precision=size_precision,
        )
    )


def wire(**kwargs: object) -> dict:
    """Return the stored/wire dict (`DydxSecondSnapshot.to_dict`) of `make_snapshot(**kwargs)`."""
    return DydxSecondSnapshot.to_dict(make_snapshot(**kwargs))  # type: ignore[arg-type]


def _with_trades(
    iid: InstrumentId,
    pp: int,
    sp: int,
    bid_prices: Sequence[Number],
    bid_sizes: Sequence[Number],
    ask_prices: Sequence[Number],
    ask_sizes: Sequence[Number],
    trades: SnapshotTradeUnits,
    ts_event: int,
    ts_init: int,
) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=iid,
        price_precision=pp,
        size_precision=sp,
        bid_price_units=[units(v, pp) for v in bid_prices],
        bid_size_units=[units(v, sp) for v in bid_sizes],
        ask_price_units=[units(v, pp) for v in ask_prices],
        ask_size_units=[units(v, sp) for v in ask_sizes],
        buy_volume_units=trades.buy_volume,
        sell_volume_units=trades.sell_volume,
        buy_count=trades.buy_count,
        sell_count=trades.sell_count,
        ts_event=ts_event,
        ts_init=ts_init,
        open_price_units=trades.open_price,
        high_price_units=trades.high_price,
        low_price_units=trades.low_price,
        close_price_units=trades.close_price,
    )
