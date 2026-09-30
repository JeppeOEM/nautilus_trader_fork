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
1-second L2 book snapshot — raw data only, no derived signals (DDD spine AD-D3) — and the one
encoder/decoder of its exact integer layout (Story 30.2).

Invariant: `DydxSecondSnapshot` is registered for Arrow exactly once, under this class name and
this schema. The class name is a persistence identifier -- `nautilus_trader.persistence.funcs.
class_to_filename` derives the catalog directory `custom_dydx_second_snapshot` from `__name__` --
and `to_dict`/`from_dict` are both the Parquet row (`make_dict_serializer`/`make_dict_deserializer`)
and the `snapshots:raw` JSON wire format: `from_dict` is the only parser of either, and this
module is the only place that knows the gap layout below (`platform/tests/test_boundaries.py`).
A list of rows is encoded column-wise by `snapshots_to_record_batch` (Story 28.2, the registered
`batch_encoder` `write_data` takes): the same values as `to_dict`, byte-identical Parquet files,
the same refusals, without a dict per row.

The layout -- every stored price and size is an exact integer, never a float:

- `price_precision`/`size_precision` (`uint8`) per row: the instrument definition's precisions,
  never a value's own digit count. Per row, not per file, so a precision change mid-day can never
  make two files of one instrument disagree.
- A *unit* is `10^-precision`: `units = Price.raw // 10^(FIXED_PRECISION - precision)`, integer
  division asserted exact (`units_of`), so no value ever passes through `float` on its way in.
- `bid_prices`/`ask_prices` (`list<int64>`): element 0 is the best price in units; each later
  element is the strictly positive gap to the level above it (bids `previous - this`, asks
  `this - previous`). Example at p=1: bids 100.5/100.3/99.9 -> `[1005, 2, 4]`, asks 100.7/101.0
  -> `[1007, 3]`.
- `bid_sizes`/`ask_sizes` (`list<int64>`), `buy_volume`/`sell_volume` (`int64`): size units.
- `open_price`..`close_price` (nullable `int64`): price units, null for a second with no trade.
- counts (`uint32`) and timestamps (`uint64`) as before.

A value that cannot be encoded exactly is refused with `SnapshotEncodingError`, never rounded: a
raw finer than the precision, units outside int64, a non-positive book gap, a precision outside
`0..FIXED_PRECISION`. `from_dict` is strict: a missing precision (a float-layout row,
`LegacySnapshotLayoutError`) or a float or bool in an integer field raises. There is no float
read path; a float-layout file is migrated by `archive.tools.migrate_snapshot_ints`.

Decoded objects keep the pre-30.2 attribute names as floats (`bid_prices`, `buy_volume`,
`open_price`, ...), each computed once in `__init__` by the one definition `unit_float` =
`float(units) / 10.0**precision` (the numpy path `unit_floats` uses the same table), plus the
integers (`bid_price_units`, ...) and, on first access, the exact `Price`/`Quantity` values
(`exact`). Signals are derived from the floats in `kernel/indicators.py` (`as_floats()` is their
input) and never stored here (SIGNAL-01):

  spread      = ask_prices[0] - bid_prices[0]
  microprice  = Microprice().update_raw(bid_prices[0], bid_sizes[0], ask_prices[0], ask_sizes[0])
  ofi_N       = MultiLevelOFI(levels=N) replayed over consecutive snapshots
  obi_N       = MultiLevelOBI(levels=N).update_raw(bid_sizes, ask_sizes)

`open_price`..`close_price` are the OHLC of the trades executed within this second, and the
volumes/counts the per-side totals -- all produced by the one exact fold, `kernel.fold.fold_trades`
(`SecondTradeFields.snapshot_units`). The raw `TradeTick`s are archived too (story 22.13) and
`archive.rebuild_seconds` rewrites a closed day's trade columns from them on exchange time.

Known limit: int64 units cap a size at 9.22e18 units (about 9.2e9 at size precision 9) and a
price likewise; a larger value is refused loudly (`SnapshotEncodingError`), never truncated.
Upgrade path: a per-row size exponent, or a decimal128 column.

`ohlc_outside_book` is the one plausibility check of a second's trade OHLC against that same
second's book (shared by capture's live canary and archive's `repair_catalog`).
"""

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import chain
from itertools import pairwise
from typing import Literal
from typing import NamedTuple
from typing import Protocol
from typing import runtime_checkable

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


BOOK_DEPTH = 20
MIGRATION_TOOL = "archive.tools.migrate_snapshot_ints"
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
UINT32_MAX = 2**32 - 1


Side = Literal["bid", "ask"]


class SnapshotEncodingError(ValueError):
    """A value the integer layout cannot hold exactly: refused, never rounded."""


class LegacySnapshotLayoutError(ValueError):
    """A float-layout (pre-30.2) snapshot row or file: there is no float read path."""


def _check_precision(precision: object, name: str = "precision") -> int:
    if type(precision) is not int or not 0 <= precision <= FIXED_PRECISION:
        raise SnapshotEncodingError(
            f"{name} {precision!r} is not an int in 0..{FIXED_PRECISION} (this build's range)"
        )
    return precision


def _check_int64(units: int, what: str) -> int:
    if not INT64_MIN <= units <= INT64_MAX:
        raise SnapshotEncodingError(f"{what} {units} units lie outside int64")
    return units


def _raw_step(precision: int) -> int:
    """Return the `Price.raw`/`Quantity.raw` of one unit at `precision` (raws: `FIXED_PRECISION`)."""
    return 10 ** (FIXED_PRECISION - precision)


def _scale(precision: int) -> float:
    """`10.0**precision`, exact for every precision of this build: the one float divisor."""
    return 10.0**precision


def units_of(raw: int, precision: int) -> int:
    """
    Return `raw` (a `Price.raw`/`Quantity.raw` at `FIXED_PRECISION`) in units of `10^-precision`.

    Integer division, asserted exact: a raw holding more digits than `precision` would be silently
    rounded by a plain `//`, so it raises `SnapshotEncodingError` instead, as do units outside
    int64 (the column type).
    """
    units, remainder = divmod(raw, _raw_step(_check_precision(precision)))
    if remainder:
        raise SnapshotEncodingError(
            f"raw {raw} is not exact at precision {precision}: it has more digits than the "
            "instrument definition allows"
        )
    return _check_int64(units, f"raw {raw} at precision {precision}:")


def unit_float(units: int, precision: int) -> float:
    """Return units as a float, the one definition: `float(units) / 10.0**precision` (display/indicators)."""
    return float(units) / _scale(precision)


def unit_floats(units: np.ndarray, precisions: np.ndarray) -> np.ndarray:
    """`unit_float` over arrays, one precision per element (the per-row precision column)."""
    precisions = np.asarray(precisions)
    if precisions.size and not 0 <= precisions.min() <= precisions.max() <= FIXED_PRECISION:
        raise ValueError(f"a precision outside this build's 0..{FIXED_PRECISION}")
    # The same divisor as `unit_float`, element for element: one definition, two paths.
    scales = np.array([_scale(p) for p in range(FIXED_PRECISION + 1)], dtype=np.float64)
    return np.asarray(units, dtype=np.int64).astype(np.float64) / scales[precisions]


def price_of(units: int, precision: int) -> Price:
    """Return the exact `Price` of `units` at `precision` (`Price.from_raw`, no float step: NAUT-01)."""
    return Price.from_raw(units * _raw_step(precision), precision)


def quantity_of(units: int, precision: int) -> Quantity:
    """Return the exact `Quantity` of `units` at `precision` (`Quantity.from_raw`, no float step)."""
    return Quantity.from_raw(units * _raw_step(precision), precision)


def encode_book_prices(units: Sequence[int], side: Side) -> list[int]:
    """
    Absolute level prices (best first) -> the stored layout: `[best, gap, gap, ...]`, each gap the
    strictly positive distance to the level above (bids descend, asks ascend). A non-positive gap
    (an unsorted or duplicated level) raises `SnapshotEncodingError`.
    """
    if not units:
        return []
    out = [units[0]]
    for above, level in pairwise(units):
        gap = above - level if side == "bid" else level - above
        if gap <= 0:
            raise SnapshotEncodingError(
                f"{side} levels {above} -> {level}: a non-positive gap (levels must be strictly "
                f"{'descending' if side == 'bid' else 'ascending'})"
            )
        out.append(_check_int64(gap, f"{side} gap"))
    return out


def decode_book_prices(encoded: Sequence[int], side: Side) -> list[int]:
    """Decode the stored `[best, gap, ...]` into absolute level prices in units, best first."""
    if not encoded:
        return []
    out = [encoded[0]]
    for gap in encoded[1:]:
        if gap <= 0:
            raise ValueError(f"{side} book: a non-positive stored gap {gap}")
        out.append(out[-1] - gap if side == "bid" else out[-1] + gap)
    return out


class SnapshotTradeUnits(NamedTuple):
    """
    The eight trade columns of a snapshot as it stores them: OHLC in price units (None when no
    trade), volumes in size units (0 when that side did not trade), counts. Field names are the
    column names (`archive.rebuild_seconds` writes them with `_asdict()`).
    """

    open_price: int | None
    high_price: int | None
    low_price: int | None
    close_price: int | None
    buy_volume: int
    sell_volume: int
    buy_count: int
    sell_count: int


@runtime_checkable
class SecondRow(Protocol):
    """
    The per-second fields the seconds -> bars fold reads, whatever object carries them.

    Invariant (one name for the duck-typed row): capture hands rows to its `SecondSink` port and
    `candles` folds them, and the two must mean the same thing by "a second". Three shapes reach
    that fold -- `DydxSecondSnapshot` live, `SecondOHLC` from the catalog read, and a plain stand-in
    in tests -- so the contract cannot be a base class; it is this structural type, declared in the
    kernel because that is the only package both sides may import (AD-D2/AD-D3).

    Read-only properties, not attributes: `SecondOHLC` is a `NamedTuple` (immutable fields) and
    `DydxSecondSnapshot.ts_event` is a property, and a mutable-attribute protocol would reject both.
    `open_price`..`close_price` are `None` for a second in which nothing traded. The values are the
    decoded floats (`unit_float`); candle bars are aggregations and stay floats (Known limit in
    `candles/domain/fold.py`).
    """

    @property
    def ts_event(self) -> int: ...

    @property
    def open_price(self) -> float | None: ...

    @property
    def high_price(self) -> float | None: ...

    @property
    def low_price(self) -> float | None: ...

    @property
    def close_price(self) -> float | None: ...

    @property
    def buy_volume(self) -> float: ...

    @property
    def sell_volume(self) -> float: ...


class SecondOHLC(NamedTuple):
    """The per-second fields candle aggregation needs (duck-types `DydxSecondSnapshot` there)."""

    ts_event: int
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    buy_volume: float
    sell_volume: float


# Book depth moves within the second we sample it; allow 0.1% before calling it impossible.
OHLC_BOOK_TOLERANCE = 0.001


class _BookAndRange(Protocol):
    bid_prices: list[float]
    ask_prices: list[float]
    high_price: float | None
    low_price: float | None


def ohlc_outside_book(snapshot: _BookAndRange, tolerance: float = OHLC_BOOK_TOLERANCE) -> bool:
    """
    Return whether the second's trade high/low lies outside its own top-20 book range.

    A trade executes against resting liquidity, so its price must lie inside the book's visible
    depth: no higher than the deepest ask level and no lower than the deepest bid level (a sweep
    that consumes levels only lands prices *between* the pre-trade levels, which the stored top-20
    depth still brackets). A high/low outside that range cannot come from that second's real
    trading -- it is the signature of replayed history (dYdX's `v4_trades` subscribed reply,
    dropped by `config.stale_trade_seconds`) or another ingestion bug. Derived purely from stored
    fields, so it works identically as a live canary and as a scan over old catalog data
    (`archive.repair_catalog`).
    """
    if snapshot.high_price is None or snapshot.low_price is None:
        return False
    if not snapshot.bid_prices or not snapshot.ask_prices:
        return False  # no book to judge against -- not evidence either way
    deepest_ask = max(snapshot.ask_prices)
    deepest_bid = min(snapshot.bid_prices)
    return snapshot.high_price > deepest_ask * (
        1 + tolerance
    ) or snapshot.low_price < deepest_bid * (1 - tolerance)


@dataclass(frozen=True, slots=True)
class SnapshotExact:
    """A snapshot's book and trade fields as exact Nautilus values (`from_raw`, no float step)."""

    bid_prices: tuple[Price, ...]
    bid_sizes: tuple[Quantity, ...]
    ask_prices: tuple[Price, ...]
    ask_sizes: tuple[Quantity, ...]
    open_price: Price | None
    high_price: Price | None
    low_price: Price | None
    close_price: Price | None
    buy_volume: Quantity
    sell_volume: Quantity


def _is_int(value: object) -> bool:
    return type(value) is int  # `bool` and floats are refused: `type(True) is bool`


def _int_list(values: object, name: str) -> list[int]:
    # Only the container is checked here: `__init__`'s `_check_units` refuses any element that is
    # not an int unit (a float, a bool) with the field named, once per row, not twice.
    if not isinstance(values, list | tuple):
        raise ValueError(f"snapshot field {name!r} must be a list of int units, got {values!r}")
    return list(values)


def _int_field(values: Mapping[str, object], name: str) -> int:
    value = values[name]
    if not _is_int(value):
        raise ValueError(f"snapshot field {name!r} must be an int, got {value!r}")
    return value  # type: ignore[return-value]  # narrowed by `_is_int`


def _optional_int_field(values: Mapping[str, object], name: str) -> int | None:
    return None if values[name] is None else _int_field(values, name)


def _floats(units: Sequence[int], scale: float) -> list[float]:
    return [float(u) / scale for u in units]


def _optional_float(units: int | None, scale: float) -> float | None:
    return None if units is None else float(units) / scale


def _check_units(values: Sequence[int], what: str, non_negative: bool) -> None:
    """
    Refuse any value that is not an int unit inside int64 (or is a negative size).

    Runs for every decoded row, so the per-element Python work is kept to builtins.
    """
    if not values:
        return
    if not all(type(u) is int for u in values):
        bad = next(u for u in values if type(u) is not int)
        raise SnapshotEncodingError(f"{what}: {bad!r} is not an int unit")
    low, high = min(values), max(values)
    if non_negative and low < 0:
        raise SnapshotEncodingError(f"{what}: a negative size {low}")
    _check_int64(low, what)
    _check_int64(high, what)


def _check_levels(units: Sequence[int], side: Side) -> None:
    """Strictly ordered levels whose every gap fits int64; `encode_book_prices` names a failure."""
    ordered = (
        all(a > b for a, b in pairwise(units))
        if side == "bid"
        else all(a < b for a, b in pairwise(units))
    )
    if not ordered or (units and max(units) - min(units) > INT64_MAX):
        encode_book_prices(units, side)  # raises with the offending pair


class DydxSecondSnapshot(Data):
    """
    1-second sampled L2 book snapshot: raw inputs only (SIGNAL-01), every signal is derived on read.

    Holds the precisions and the integer units (absolute level prices, best first; variable-length
    sides, up to BOOK_DEPTH=20); `__init__` refuses anything the stored layout cannot hold
    (`SnapshotEncodingError`: unsorted levels, sizes that are negative or mismatched in count,
    non-int units) and computes the float attributes once under their pre-30.2 names.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        price_precision: int,
        size_precision: int,
        bid_price_units: Sequence[int],
        bid_size_units: Sequence[int],
        ask_price_units: Sequence[int],
        ask_size_units: Sequence[int],
        buy_volume_units: int,
        sell_volume_units: int,
        buy_count: int,
        sell_count: int,
        ts_event: int,
        ts_init: int,
        open_price_units: int | None = None,
        high_price_units: int | None = None,
        low_price_units: int | None = None,
        close_price_units: int | None = None,
    ) -> None:
        self.instrument_id = instrument_id
        self.price_precision = _check_precision(price_precision, "price_precision")
        self.size_precision = _check_precision(size_precision, "size_precision")
        self.bid_price_units = list(bid_price_units)
        self.bid_size_units = list(bid_size_units)
        self.ask_price_units = list(ask_price_units)
        self.ask_size_units = list(ask_size_units)
        self.buy_volume_units = buy_volume_units
        self.sell_volume_units = sell_volume_units
        self.buy_count = buy_count
        self.sell_count = sell_count
        self.open_price_units = open_price_units
        self.high_price_units = high_price_units
        self.low_price_units = low_price_units
        self.close_price_units = close_price_units
        self._ts_event = ts_event
        self._ts_init = ts_init
        self._exact: SnapshotExact | None = None
        self._validate()
        price_scale = _scale(self.price_precision)
        size_scale = _scale(self.size_precision)
        self.bid_prices = _floats(self.bid_price_units, price_scale)
        self.bid_sizes = _floats(self.bid_size_units, size_scale)
        self.ask_prices = _floats(self.ask_price_units, price_scale)
        self.ask_sizes = _floats(self.ask_size_units, size_scale)
        self.buy_volume = float(buy_volume_units) / size_scale
        self.sell_volume = float(sell_volume_units) / size_scale
        self.open_price = _optional_float(open_price_units, price_scale)
        self.high_price = _optional_float(high_price_units, price_scale)
        self.low_price = _optional_float(low_price_units, price_scale)
        self.close_price = _optional_float(close_price_units, price_scale)

    def _validate(self) -> None:
        for side in ("bid", "ask"):
            prices = getattr(self, f"{side}_price_units")
            sizes = getattr(self, f"{side}_size_units")
            if len(prices) != len(sizes):
                raise SnapshotEncodingError(
                    f"{self.instrument_id} {side}: {len(prices)} prices but {len(sizes)} sizes"
                )
            _check_units(prices, f"{self.instrument_id} {side} prices", non_negative=False)
            _check_units(sizes, f"{self.instrument_id} {side} sizes", non_negative=True)
            _check_levels(prices, side)
        volumes = [self.buy_volume_units, self.sell_volume_units]
        _check_units(volumes, f"{self.instrument_id} volumes", non_negative=True)
        counts = [self.buy_count, self.sell_count]
        _check_units(counts, f"{self.instrument_id} counts", non_negative=True)
        if max(counts) > UINT32_MAX:  # the column is uint32: refused here, not at the flush
            raise SnapshotEncodingError(f"{self.instrument_id}: a trade count above uint32")
        ohlc = [
            u
            for u in (
                self.open_price_units,
                self.high_price_units,
                self.low_price_units,
                self.close_price_units,
            )
            if u is not None
        ]
        _check_units(ohlc, f"{self.instrument_id} OHLC", non_negative=False)

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init

    @classmethod
    def from_levels(
        cls,
        instrument_id: InstrumentId,
        price_precision: int,
        size_precision: int,
        bids: Sequence[tuple[Price, Quantity]],
        asks: Sequence[tuple[Price, Quantity]],
        trades: SnapshotTradeUnits,
        ts_event: int,
        ts_init: int,
    ) -> "DydxSecondSnapshot":
        """
        Encode a row from exact values (the encoder): each level's `Price`/`Quantity` raw becomes units at the
        instrument definition's precisions (`units_of`, exact or `SnapshotEncodingError`), the
        trade columns come already in units (`SecondTradeFields.snapshot_units`).
        """
        return cls(
            instrument_id=instrument_id,
            price_precision=price_precision,
            size_precision=size_precision,
            bid_price_units=[units_of(p.raw, price_precision) for p, _ in bids],
            bid_size_units=[units_of(q.raw, size_precision) for _, q in bids],
            ask_price_units=[units_of(p.raw, price_precision) for p, _ in asks],
            ask_size_units=[units_of(q.raw, size_precision) for _, q in asks],
            buy_volume_units=trades.buy_volume,
            sell_volume_units=trades.sell_volume,
            buy_count=trades.buy_count,
            sell_count=trades.sell_count,
            open_price_units=trades.open_price,
            high_price_units=trades.high_price,
            low_price_units=trades.low_price,
            close_price_units=trades.close_price,
            ts_event=ts_event,
            ts_init=ts_init,
        )

    @property
    def exact(self) -> SnapshotExact:
        """
        The book and trade fields as exact `Price`/`Quantity` values, built on first access and
        kept on the instance (most readers need only the floats).
        """
        if self._exact is None:
            self._exact = self._build_exact()
        return self._exact

    def _build_exact(self) -> SnapshotExact:
        pp, sp = self.price_precision, self.size_precision

        def price(units: int | None) -> Price | None:
            return None if units is None else price_of(units, pp)

        return SnapshotExact(
            bid_prices=tuple(price_of(u, pp) for u in self.bid_price_units),
            bid_sizes=tuple(quantity_of(u, sp) for u in self.bid_size_units),
            ask_prices=tuple(price_of(u, pp) for u in self.ask_price_units),
            ask_sizes=tuple(quantity_of(u, sp) for u in self.ask_size_units),
            open_price=price(self.open_price_units),
            high_price=price(self.high_price_units),
            low_price=price(self.low_price_units),
            close_price=price(self.close_price_units),
            buy_volume=quantity_of(self.buy_volume_units, sp),
            sell_volume=quantity_of(self.sell_volume_units, sp),
        )

    def as_floats(self) -> dict[str, object]:
        """
        Return the float view `kernel.indicators`' stateless functions take (absolute level prices, best
        first; the decoded floats computed in `__init__`), plus the row's two precisions (Story
        31.3: `spread` rounds its difference to `price_precision`). Never published or stored: the
        wire and the file carry `to_dict`'s integers.
        """
        return {
            "price_precision": self.price_precision,
            "size_precision": self.size_precision,
            "bid_prices": self.bid_prices,
            "bid_sizes": self.bid_sizes,
            "ask_prices": self.ask_prices,
            "ask_sizes": self.ask_sizes,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
        }

    @classmethod
    def schema(cls) -> pa.Schema:
        return pa.schema(
            {
                "instrument_id": pa.dictionary(pa.int8(), pa.string()),
                "price_precision": pa.uint8(),
                "size_precision": pa.uint8(),
                "bid_prices": pa.list_(pa.int64()),
                "bid_sizes": pa.list_(pa.int64()),
                "ask_prices": pa.list_(pa.int64()),
                "ask_sizes": pa.list_(pa.int64()),
                "buy_volume": pa.int64(),
                "sell_volume": pa.int64(),
                "buy_count": pa.uint32(),
                "sell_count": pa.uint32(),
                "open_price": pa.int64(),
                "high_price": pa.int64(),
                "low_price": pa.int64(),
                "close_price": pa.int64(),
                "ts_event": pa.uint64(),
                "ts_init": pa.uint64(),
            },
            metadata={"type": "DydxSecondSnapshot"},
        )

    @staticmethod
    def to_dict(obj: "DydxSecondSnapshot") -> dict:
        """Return the stored/wire row: integer units, gap-encoded book prices, the two precisions."""
        return {
            "instrument_id": obj.instrument_id.value,
            "price_precision": obj.price_precision,
            "size_precision": obj.size_precision,
            "bid_prices": encode_book_prices(obj.bid_price_units, "bid"),
            "bid_sizes": obj.bid_size_units,
            "ask_prices": encode_book_prices(obj.ask_price_units, "ask"),
            "ask_sizes": obj.ask_size_units,
            "buy_volume": obj.buy_volume_units,
            "sell_volume": obj.sell_volume_units,
            "buy_count": obj.buy_count,
            "sell_count": obj.sell_count,
            "open_price": obj.open_price_units,
            "high_price": obj.high_price_units,
            "low_price": obj.low_price_units,
            "close_price": obj.close_price_units,
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, object]) -> "DydxSecondSnapshot":
        """
        Parse one stored/wire row, strictly: every key present, every unit an int (not a float,
        not a bool), gaps positive. A row without precisions is a float-layout row and raises
        `LegacySnapshotLayoutError` naming the migration -- there is no float fallback. A missing
        key raises `KeyError`: no field has a default here.

        Known limit: the one legitimate absence, a pre-OHLC file (written before the trade
        OHLC columns existed), gets its defaults from `archive.tools.migrate_snapshot_ints`,
        which rewrites such a file before any reader sees it -- readers refuse the legacy layout
        rather than default a field. Upgrade path: none needed while that migration is the only
        producer of such rows; a future column is added the same way (migrate, then read).
        """
        if values.get("price_precision") is None or values.get("size_precision") is None:
            raise LegacySnapshotLayoutError(
                f"snapshot row for {values.get('instrument_id')!r} has no price_precision/"
                f"size_precision: a float-layout row; run `python -m {MIGRATION_TOOL}`"
            )
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            price_precision=_int_field(values, "price_precision"),
            size_precision=_int_field(values, "size_precision"),
            bid_price_units=decode_book_prices(
                _int_list(values["bid_prices"], "bid_prices"), "bid"
            ),
            bid_size_units=_int_list(values["bid_sizes"], "bid_sizes"),
            ask_price_units=decode_book_prices(
                _int_list(values["ask_prices"], "ask_prices"), "ask"
            ),
            ask_size_units=_int_list(values["ask_sizes"], "ask_sizes"),
            buy_volume_units=_int_field(values, "buy_volume"),
            sell_volume_units=_int_field(values, "sell_volume"),
            buy_count=_int_field(values, "buy_count"),
            sell_count=_int_field(values, "sell_count"),
            open_price_units=_optional_int_field(values, "open_price"),
            high_price_units=_optional_int_field(values, "high_price"),
            low_price_units=_optional_int_field(values, "low_price"),
            close_price_units=_optional_int_field(values, "close_price"),
            ts_event=_int_field(values, "ts_event"),
            ts_init=_int_field(values, "ts_init"),
        )

    def __repr__(self) -> str:
        bp = self.bid_prices[0] if self.bid_prices else None
        ap = self.ask_prices[0] if self.ask_prices else None
        return f"DydxSecondSnapshot({self.instrument_id} bid={bp} ask={ap} levels={len(self.bid_prices)})"


# -- column-projected reads (`kernel.catalog_files`): the same decoding, without the row objects --

PRECISION_COLUMNS = ("price_precision", "size_precision")
OHLC_UNIT_COLUMNS = ("open_price", "high_price", "low_price", "close_price")
VOLUME_UNIT_COLUMNS = ("buy_volume", "sell_volume")
_TOP_COLUMNS = ("bid_prices", "bid_sizes", "ask_prices", "ask_sizes")
TOP_OF_BOOK_COLUMNS = ("ts_event", "ts_init", *PRECISION_COLUMNS, *_TOP_COLUMNS)


def require_integer_layout(schema: pa.Schema, path: str) -> None:
    """Refuse a float-layout (pre-30.2) snapshot file: `LegacySnapshotLayoutError` naming it."""
    if "price_precision" not in schema.names:
        raise LegacySnapshotLayoutError(
            f"{path}: a float-layout DydxSecondSnapshot file (no price_precision column); "
            f"run `python -m {MIGRATION_TOOL}`"
        )


def _precisions(table: pa.Table, name: str) -> np.ndarray:
    """Return a precision column; refuse a null or out-of-range one (a corrupt file)."""
    column = table.column(name)
    if column.null_count:
        raise ValueError(f"a null {name} in a snapshot table")
    values = column.to_numpy().astype(np.intp)
    if values.size and not 0 <= values.min() <= values.max() <= FIXED_PRECISION:
        raise ValueError(f"a stored {name} outside this build's 0..{FIXED_PRECISION}")
    return values


def trade_float_columns(table: pa.Table) -> dict[str, np.ndarray]:
    """
    Decode the OHLC (NaN = no trade) and the two volumes of a snapshot table as float arrays, decoded with
    `unit_floats` at each row's precision; `table` holds the precision, OHLC and volume columns.
    """
    price_p = _precisions(table, "price_precision")
    size_p = _precisions(table, "size_precision")
    out = {}
    for name in OHLC_UNIT_COLUMNS:
        column = table.column(name)
        units = column.fill_null(0).to_numpy()
        values = unit_floats(units, price_p)
        values[column.is_null().to_numpy(zero_copy_only=False)] = np.nan
        out[name] = values
    for name in VOLUME_UNIT_COLUMNS:
        out[name] = unit_floats(table.column(name).to_numpy(), size_p)
    return out


class TopOfBookUnits(NamedTuple):
    """Level 0 of one row in units (element 0 of the gap layout is the absolute best price)."""

    ts_event: int
    ts_init: int
    price_precision: int
    size_precision: int
    bid_price: int
    bid_size: int
    ask_price: int
    ask_size: int


def top_of_book_units(table: pa.Table) -> list[TopOfBookUnits]:
    """
    Level 0 of every row of a table holding `TOP_OF_BOOK_COLUMNS` whose both sides are non-empty
    (a row with an empty side has no top of book and is omitted); only element 0 of each list
    leaves Arrow, so a long window never builds the 20-level book in Python (MEM-01).
    """
    # A null or empty list compares to null or False, and `filter` drops both.
    quotable = pc.and_(
        pc.greater(pc.list_value_length(table.column("bid_prices")), 0),
        pc.greater(pc.list_value_length(table.column("ask_prices")), 0),
    )
    table = table.filter(quotable)
    columns = [table.column(name).to_pylist() for name in ("ts_event", "ts_init")]
    columns += [_precisions(table, name).tolist() for name in PRECISION_COLUMNS]
    columns += [pc.list_element(table.column(name), 0).to_pylist() for name in _TOP_COLUMNS]
    return [TopOfBookUnits(*values) for values in zip(*columns, strict=True)]


# -- the columnar batch encoder (Story 28.2): the flush's path into `write_data` ------------------

# The Python-to-Arrow conversion's refusals of a value outside its column type (a count past
# uint32, units past int64, a precision past uint8, a non-numeric value): the dict path's
# `pa.RecordBatch.from_pylist` raises the same ones, and Nautilus's `dicts_to_record_batch` prints
# and swallows them into a `None` batch; here they are raised, named. A float is not refused by
# either path (pyarrow truncates it into an int column): `__init__` refuses non-int units, so only a
# row mutated after construction could carry one.
_CONVERSION_ERRORS = (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError, TypeError)


def _snapshot_columns(rows: Sequence[DydxSecondSnapshot]) -> dict[str, list | pa.Array]:
    """Return every stored column of `rows`: the values `to_dict` stores per row, column-wise."""
    return {
        "instrument_id": [r.instrument_id.value for r in rows],
        "price_precision": [r.price_precision for r in rows],
        "size_precision": [r.size_precision for r in rows],
        "bid_prices": _book_price_column([r.bid_price_units for r in rows], "bid"),
        "bid_sizes": [r.bid_size_units for r in rows],
        "ask_prices": _book_price_column([r.ask_price_units for r in rows], "ask"),
        "ask_sizes": [r.ask_size_units for r in rows],
        "buy_volume": [r.buy_volume_units for r in rows],
        "sell_volume": [r.sell_volume_units for r in rows],
        "buy_count": [r.buy_count for r in rows],
        "sell_count": [r.sell_count for r in rows],
        "open_price": [r.open_price_units for r in rows],
        "high_price": [r.high_price_units for r in rows],
        "low_price": [r.low_price_units for r in rows],
        "close_price": [r.close_price_units for r in rows],
        "ts_event": [r.ts_event for r in rows],
        "ts_init": [r.ts_init for r in rows],
    }


def _book_price_column(units: list[list[int]], side: Side) -> pa.ListArray:
    """
    `encode_book_prices` over a whole column at once: the levels of every row flattened into one
    int64 array (the conversion refuses a non-int or out-of-int64 unit), gaps taken in numpy. When
    any row would be refused, the column is re-encoded row by row through `encode_book_prices`,
    which raises naming the offending pair -- the vectorised path never decides a refusal itself.
    """
    offsets = np.zeros(len(units) + 1, dtype=np.int32)
    np.cumsum([len(row) for row in units], out=offsets[1:])
    flat = pa.array(list(chain.from_iterable(units)), type=pa.int64()).to_numpy()
    encoded = _gap_encoded(flat, offsets, side)
    if encoded is None:
        return pa.array([encode_book_prices(row, side) for row in units], pa.list_(pa.int64()))
    return pa.ListArray.from_arrays(pa.array(offsets), pa.array(encoded, type=pa.int64()))


def _gap_encoded(flat: np.ndarray, offsets: np.ndarray, side: Side) -> np.ndarray | None:
    """
    Return the flattened `[best, gap, ...]` layout of every row, or None when a gap inside a row
    is not strictly positive. Ordered levels bound each true gap to 1..2^64-1, so an int64 gap
    that wrapped past `INT64_MAX` reads as non-positive here: overflow is caught by the same test.
    """
    if flat.size < 2:
        return flat
    starts = np.zeros(flat.size, dtype=bool)
    starts[offsets[:-1][np.diff(offsets) > 0]] = True
    inner = ~starts[1:]  # position i+1 continues the row of position i
    above, level = flat[:-1], flat[1:]
    ordered = above > level if side == "bid" else level > above
    gaps = above - level if side == "bid" else level - above
    if not (ordered[inner].all() and (gaps[inner] > 0).all()):
        return None
    encoded = flat.copy()
    encoded[1:][inner] = gaps[inner]
    return encoded


def snapshots_to_record_batch(rows: Sequence[DydxSecondSnapshot]) -> pa.RecordBatch:
    """
    Encode a batch of snapshots as one `RecordBatch`: one `pa.array` per schema column, in schema
    order and type, holding exactly the values `to_dict` stores (the same gap layout, the same
    units). Registered as the type's `batch_encoder`, so `ParquetDataCatalog.write_data` encodes a
    flush in one pass instead of one dict and one single-row batch per row; the Parquet files are
    byte-identical to the dict path's (`kernel/tests/test_second_snapshot.py`).

    Invariant: never returns `None` and never a partial batch. Every value the dict path refuses
    raises `SnapshotEncodingError`: a non-positive book gap (`encode_book_prices`) and every value
    outside its column type (the conversion's own error, chained).
    """
    schema = DydxSecondSnapshot.schema()
    try:
        columns = _snapshot_columns(rows)
        arrays = [_as_array(columns[field.name], field.type) for field in schema]
        # Inside the try: more distinct instrument ids than the int8 dictionary index holds widen
        # the index to int16, and only the schema check here refuses that.
        return pa.RecordBatch.from_arrays(arrays, schema=schema)
    except _CONVERSION_ERRORS as e:
        raise SnapshotEncodingError(f"a snapshot value does not fit its column: {e}") from e


def _as_array(column: list | pa.Array, column_type: pa.DataType) -> pa.Array:
    return column if isinstance(column, pa.Array) else pa.array(column, type=column_type)


# `encoder` (one row, `to_dict`) stays for Nautilus's single-object `serialize`; a list goes
# through `batch_encoder` (`ArrowSerializer.serialize_batch` prefers it).
register_arrow(
    data_cls=DydxSecondSnapshot,
    schema=DydxSecondSnapshot.schema(),
    encoder=make_dict_serializer(schema=DydxSecondSnapshot.schema()),
    decoder=make_dict_deserializer(DydxSecondSnapshot),
    batch_encoder=snapshots_to_record_batch,
)
