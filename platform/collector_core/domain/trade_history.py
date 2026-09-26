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
Exact REST trades for the reconnect backfill (story 22.14, audit D-47/D-48): the venue-neutral
half the per-venue `trade_history.py` modules (`VenueTradeHistory`) build on.

Invariant: a backfilled `TradeTick` is built exactly as the live WS parser builds it -- the same
trade id, `ts_event` from the venue's trade time as an exact integer count of nanoseconds, the
aggressor from the taker side, and price/size from the venue's decimal strings at the instrument's
own precisions via `Price.from_str`/`Quantity.from_str` of a string first proven exact
(`exact_text`) -- never through `float`, never rounded. A value not representable at the
precision is an error (`BackfillError`), never a rounded trade.

WS id == REST id, wire-verified 2026-09-21 (live pyo3 WS clients vs the three venues' endpoints,
same interval): Bybit linear BTCUSDT 999/999 and spot ETHUSDT 60/60, Hyperliquid 19/19, dYdX 3/3 --
same ids, and price, size and aggressor equal on every one; so the dedup takes the union across
sources exactly. Hyperliquid's live `ts_event` alone differed (<= 128 ns, the adapter's f64 path,
audit D-62); the Hyperliquid client re-stamps it to the exact millisecond.
"""

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_MS_NS = 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class BackfillError(Exception):
    """A venue response or value that cannot be turned into an exact `TradeTick`."""


@dataclass(frozen=True)
class Fetched:
    """
    One instrument's backfill fetch.

    `trades`: oldest first, `ts_event >= since_ns`. `reached_since`: the venue's history covered
    `since_ns` (oldest trade at or before it, or a response that was not cut by the venue's
    depth). `oldest_ns`: the oldest `ts_event` the venue returned, before the `since` filter.
    `rejected`: trades skipped for a value that is not exact (the instrument is then an error).
    """

    trades: list[TradeTick]
    reached_since: bool
    oldest_ns: int | None
    rejected: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackfillCapability:
    """
    How deep a venue's REST trade history reaches (`docs/DATA_DICTIONARY.md` §1.1).

    Invariant: a response of `rows_per_page` rows may have been cut by the venue's depth, so it
    covers `since` only when its oldest trade is at or before it -- otherwise the uncovered span
    is reported `unrecoverable`, never assumed recovered. `max_pages` > 1 only for a venue that
    can page backwards (dYdX); the others return their last `rows_per_page` trades, once.
    """

    rows_per_page: int
    max_pages: int = 1

    def cut(self, rows: int) -> bool:
        """Whether a page this long may have been truncated by the venue's depth."""
        return rows >= self.rows_per_page


# -- exact conversions ----------------------------------------------------------------------------


def exact_text(text: str, precision: int) -> str:
    """Return the decimal string at exactly `precision` places; `BackfillError` if that rounds."""
    try:
        value = Decimal(text)
        quantized = value.quantize(Decimal(1).scaleb(-precision))
    except (InvalidOperation, TypeError) as e:
        raise BackfillError(f"{text!r} is not a decimal at precision {precision}") from e
    if not value.is_finite() or quantized != value:
        raise BackfillError(f"{text!r} is not representable at precision {precision}")
    return format(quantized, "f")


def _price(text: str, instrument: Instrument) -> Price:
    price = Price.from_str(exact_text(text, instrument.price_precision))
    if price.precision != instrument.price_precision:
        raise BackfillError(f"price {text!r} parsed at precision {price.precision}")
    return price


def _size(text: str, instrument: Instrument) -> Quantity:
    exact = exact_text(text, instrument.size_precision)
    if Decimal(exact) <= 0:
        raise BackfillError(f"size {text!r} is not positive")
    size = Quantity.from_str(exact)
    if size.precision != instrument.size_precision:
        raise BackfillError(f"size {text!r} parsed at precision {size.precision}")
    return size


def iso_to_ns(text: str) -> int:
    """
    Convert an ISO-8601 UTC timestamp (dYdX's `createdAt`, millisecond precision) to integer
    nanoseconds: the value chrono's `timestamp_nanos_opt` gives the WS parser. Integer arithmetic.
    """
    try:
        moment = datetime.fromisoformat(text)
    except (ValueError, TypeError) as e:
        raise BackfillError(f"createdAt {text!r} is not ISO-8601") from e
    if moment.tzinfo is None:
        raise BackfillError(f"createdAt {text!r} has no timezone")
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def ms_to_ns(value: Any) -> int:
    """Convert a venue millisecond timestamp (JSON integer or integer string) to nanoseconds."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise BackfillError(f"time {value!r} is not an integer millisecond timestamp")
    try:
        return int(value) * _MS_NS
    except ValueError as e:
        raise BackfillError(f"time {value!r} is not an integer millisecond timestamp") from e


def side_of(sides: dict[str, AggressorSide], value: Any) -> AggressorSide:
    """Return the aggressor for a venue's taker-side token; `BackfillError` for an unknown one."""
    side = sides.get(value) if isinstance(value, str) else None
    if side is None:
        raise BackfillError(f"unknown taker side {value!r}")
    return side


def tick(
    instrument: Instrument,
    values: tuple[str, str, AggressorSide, str, int],
    ts_init: int,
) -> TradeTick:
    """Build one exact trade from (price text, size text, aggressor, trade id, ts_event ns)."""
    price, size, side, trade_id, ts_event = values
    return TradeTick(
        instrument.id,
        _price(price, instrument),
        _size(size, instrument),
        side,
        TradeId(trade_id),
        ts_event,
        ts_init,
    )


# -- collecting a venue's rows --------------------------------------------------------------------


@dataclass(frozen=True)
class RowReader:
    """How to read one venue's trade row: its venue time, and the exact `TradeTick` it is."""

    time_ns: Callable[[dict], int]
    trade: Callable[[dict, Instrument, int], TradeTick]

    def time_or_none(self, row: Any) -> int | None:
        """Return the row's venue time, or None for a malformed row (rejected alone, not the fetch)."""
        try:
            return self.time_ns(row)
        except (BackfillError, KeyError, TypeError, ValueError):
            return None


def collect(
    rows: list[dict],
    reader: RowReader,
    instrument: Instrument,
    since_ns: int,
    ts_init: int,
    reached: bool,
) -> Fetched:
    """
    Build the trades at or after `since_ns`, oldest first; a malformed or inexact one is rejected.
    `reached`: the venue said its history covered `since_ns` (a short page); otherwise it did when
    the oldest row is at or before `since_ns`.
    """
    trades: list[TradeTick] = []
    rejected: list[str] = []
    timed: list[tuple[int, dict]] = []
    for row in reversed(rows):  # reversed: oldest first on ties
        ts_event = reader.time_or_none(row)
        if ts_event is None:
            rejected.append(f"{str(row)[:120]}: no valid trade time")
        else:
            timed.append((ts_event, row))
    for ts_event, row in sorted(timed, key=lambda pair: pair[0]):
        if ts_event < since_ns:
            continue
        try:
            trades.append(reader.trade(row, instrument, ts_init))
        except (BackfillError, KeyError, TypeError, ValueError) as e:
            rejected.append(f"{str(row)[:120]}: {e}")
    oldest_ns = min((t for t, _ in timed), default=None)
    reached = reached or (oldest_ns is not None and oldest_ns <= since_ns)
    return Fetched(trades, reached, oldest_ns, rejected)
