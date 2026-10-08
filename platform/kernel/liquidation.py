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
One forced liquidation, shared by every venue that publishes them (kernel, DDD spine AD-D3; Story
33.1, `docs/DATA_DICTIONARY.md` §1.26). Hyperliquid writes no rows: it has no market-wide feed and
Story 33.2 refuted both public-data hypotheses (§1.26).

Invariant: `Liquidation` is registered for Arrow exactly once, under this class name (the catalog
directory `custom_liquidation` derives from `__name__`) and this schema; its price and size are
exact integer units at the row's own definition precisions, never a float.

Side: `LiquidatedSide` is the side of the *position* the venue force-closed. `LONG` means a long
was liquidated, so the forced order that hit the book was a **sell**; `SHORT` means a forced
**buy**. Bybit's wire `S` names the liquidated position: `S == "Buy"` is `LONG`, `"Sell"` is
`SHORT` (audit D-147; a test pins it on a recorded frame).

Units, as the 1 s snapshot's (`kernel.second_snapshot`): `price_units` counts `10^-price_precision`
and `size_units` `10^-size_precision`, the precisions taken from the instrument definition, never
from the value's digits (DATA-04). `from_wire_text` goes wire text -> `Decimal` -> raw at
`FIXED_PRECISION` (`Decimal.scaleb`, NAUT-01) -> `units_of`, which refuses a value finer than the
precision with `SnapshotEncodingError` instead of rounding it.

Price: Bybit's `p` is the position's **bankruptcy price**, not the price the forced order filled
at (audit D-148). `notional_units()` is size x bankruptcy price, an approximation of the fill
notional, never the fill itself.

`venue_event_id` is the venue's own key when it has one; Bybit has none, so it is the dedup key
`"{T}:{S}:{v}:{p}"` of the wire texts, with `#k` for the k-th identical entry of one frame (audit
D-150).
"""

from decimal import Context
from decimal import Decimal
from decimal import Inexact
from enum import Enum

import pyarrow as pa

from kernel.second_snapshot import SnapshotEncodingError
from kernel.second_snapshot import price_of
from kernel.second_snapshot import units_of
from kernel.venues import has_venue
from kernel.venues import market_suffix
from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


# The data-client id a strategy subscribes `Liquidation` through (Story 33.14), shared by research
# and bots: live, the bots' Redis bridge (`liquidations:raw`) registers under it, so a custom-data
# subscription is not routed by the instrument's venue to the venue adapter, which has none; in a
# backtest it is the `BacktestDataConfig.client_id` label of the archived rows.
LIQUIDATION_CLIENT_ID = "LIQUIDATIONS"


def _exact() -> Context:
    """
    Return a context wide enough for any wire text an int64 unit count can hold, trapping `Inexact`, so
    `scaleb` never rounds silently (the default context keeps 28 digits). Built per call: the
    kernel holds no module-level mutable state.
    """
    return Context(prec=80, traps=[Inexact])


def has_liquidation_feed(instrument_id: str) -> bool:
    """
    Whether the platform captures this instrument's liquidations: Bybit `-LINEAR` ids only.

    The one predicate behind the candle store's null-vs-0 rule (Story 33.3): an instrument with the
    feed reads 0 liquidations in a bucket none landed in, one without reads null (unknown), never
    0. Bybit spot has no liquidation stream; Hyperliquid has no market-wide feed and Story 33.2
    refuted both public-data hypotheses (`docs/DATA_DICTIONARY.md` §1.26), and the operator
    declined a node feed; dYdX's `LIQUIDATED` trade type is dropped by the adapter and not
    deployed. A venue that gains a feed is added here, and only here -- and in its independent
    restatement on the verification oracle's side, `verification.domain.liquidation_check.
    has_liquidation_feed` (DATA-02: the oracle never imports the code it checks), which
    `platform/tests/test_liquidation_feed_predicates.py` holds equal to this one on a table of ids.
    """
    return has_venue(instrument_id, "BYBIT") and market_suffix(instrument_id) == "LINEAR"


class LiquidatedSide(Enum):
    """The liquidated position's side: `LONG` is a forced sell, `SHORT` a forced buy."""

    LONG = "long"
    SHORT = "short"


def _raw(text: str, what: str) -> int:
    """Return wire decimal text as a raw at `FIXED_PRECISION`, exactly, or refuse it."""
    try:
        value = Decimal(text)
    except ArithmeticError as exc:  # decimal.InvalidOperation
        raise SnapshotEncodingError(f"{what} {text!r} is not decimal text") from exc
    if not value.is_finite() or value <= 0:
        raise SnapshotEncodingError(f"{what} {text!r} is not a positive finite decimal")
    try:
        scaled = value.scaleb(FIXED_PRECISION, context=_exact())
    except Inexact as exc:
        raise SnapshotEncodingError(f"{what} {text!r} has too many digits to hold exactly") from exc
    if scaled != scaled.to_integral_value():
        raise SnapshotEncodingError(f"{what} {text!r} has more than {FIXED_PRECISION} decimals")
    return int(scaled)


class Liquidation(Data):
    """One forced liquidation of one instrument (see the module docstring for every field)."""

    def __init__(
        self,
        instrument_id: InstrumentId,
        side: LiquidatedSide,
        size_units: int,
        price_units: int,
        price_precision: int,
        size_precision: int,
        venue_event_id: str,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.side = side
        self.size_units = size_units
        self.price_units = price_units
        self.price_precision = price_precision
        self.size_precision = size_precision
        self.venue_event_id = venue_event_id
        self._ts_event = ts_event
        self._ts_init = ts_init

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init

    @classmethod
    def from_wire_text(
        cls,
        instrument_id: InstrumentId,
        side: LiquidatedSide,
        size_text: str,
        price_text: str,
        precisions: tuple[int, int],
        venue_event_id: str,
        ts_event: int,
        ts_init: int,
    ) -> "Liquidation":
        """
        Build a row from the venue's decimal texts at the definition's `(price, size)` precisions.
        `SnapshotEncodingError` when a value is not positive decimal text or is finer than its
        precision: the caller ledgers it (`collector.unencodable`), never rounds it.
        """
        price_precision, size_precision = precisions
        return cls(
            instrument_id=instrument_id,
            side=side,
            size_units=units_of(_raw(size_text, "size"), size_precision),
            price_units=units_of(_raw(price_text, "price"), price_precision),
            price_precision=price_precision,
            size_precision=size_precision,
            venue_event_id=venue_event_id,
            ts_event=ts_event,
            ts_init=ts_init,
        )

    @property
    def price(self) -> Price:
        """The exact bankruptcy price (`Price.from_raw`, no float step)."""
        return price_of(self.price_units, self.price_precision)

    @property
    def size(self) -> Quantity:
        """The exact liquidated size (`Quantity.from_raw`, no float step)."""
        step = 10 ** (FIXED_PRECISION - self.size_precision)
        return Quantity.from_raw(self.size_units * step, self.size_precision)

    def notional_units(self) -> int:
        """
        Size x bankruptcy price in units of `10^-(price_precision + size_precision)` of the quote:
        an approximation of the fill notional, since the forced order fills at the book, not at
        the bankruptcy price (audit D-148).
        """
        return self.size_units * self.price_units

    def notional_units_at(self, price_precision: int, size_precision: int) -> int:
        """
        Return `notional_units()` rescaled exactly to units of `10^-(price_precision +
        size_precision)` (Story 33.14: a consumer feeding one instrument's rows into one sum takes
        the definition's precisions, never each row's). `SnapshotEncodingError` when a coarser
        target cannot hold the value exactly: the caller logs and counts it, never rounds it.
        """
        shift = (price_precision + size_precision) - (self.price_precision + self.size_precision)
        notional = self.notional_units()
        if shift >= 0:
            return notional * 10**shift
        quotient, remainder = divmod(notional, 10**-shift)
        if remainder:
            raise SnapshotEncodingError(
                f"{self.venue_event_id}: notional {notional} at 10^-"
                f"{self.price_precision + self.size_precision} is not exact at 10^-"
                f"{price_precision + size_precision}"
            )
        return quotient

    @classmethod
    def schema(cls) -> pa.Schema:
        return pa.schema(
            {
                "instrument_id": pa.dictionary(pa.int8(), pa.string()),
                "side": pa.dictionary(pa.int8(), pa.string()),
                "size_units": pa.int64(),
                "price_units": pa.int64(),
                "price_precision": pa.uint8(),
                "size_precision": pa.uint8(),
                "venue_event_id": pa.string(),
                "ts_event": pa.uint64(),
                "ts_init": pa.uint64(),
            },
            metadata={"type": "Liquidation"},
        )

    @staticmethod
    def to_dict(obj: "Liquidation") -> dict[str, object]:
        """Return the stored row and the `liquidations:raw` payload: integers, precisions, side."""
        return {
            "instrument_id": obj.instrument_id.value,
            "side": obj.side.value,
            "size_units": obj.size_units,
            "price_units": obj.price_units,
            "price_precision": obj.price_precision,
            "size_precision": obj.size_precision,
            "venue_event_id": obj.venue_event_id,
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> "Liquidation":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            side=LiquidatedSide(values["side"]),
            size_units=int(values["size_units"]),  # type: ignore[call-overload]
            price_units=int(values["price_units"]),  # type: ignore[call-overload]
            price_precision=int(values["price_precision"]),  # type: ignore[call-overload]
            size_precision=int(values["size_precision"]),  # type: ignore[call-overload]
            venue_event_id=str(values["venue_event_id"]),
            ts_event=int(values["ts_event"]),  # type: ignore[call-overload]
            ts_init=int(values["ts_init"]),  # type: ignore[call-overload]
        )

    def __repr__(self) -> str:
        return (
            f"Liquidation(instrument_id={self.instrument_id}, side={self.side.value}, "
            f"size_units={self.size_units}, price_units={self.price_units}, "
            f"venue_event_id={self.venue_event_id})"
        )


register_arrow(
    data_cls=Liquidation,
    schema=Liquidation.schema(),
    encoder=make_dict_serializer(schema=Liquidation.schema()),
    decoder=make_dict_deserializer(Liquidation),
)
