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
The `derivs:raw` row (kernel, DDD spine AD-D3; Story 33.4, `docs/DATA_DICTIONARY.md` §1.27): one
mark price, index price, funding rate or open-interest update, as capture publishes it and every
reader (`views.live_derivs`, `ranking`) decodes it. SSOT-02: this is the one definition of the
row; nothing else builds or parses it.

Invariant: the value is exact. `value` is the stored `Price` or `Decimal` as plain positional
decimal text (`exact_text`: never scientific notation, so `0.00000012`, not `1.2E-7`; exact by
construction, never through `float`), and `from_wire` refuses anything that is not a decimal
string, so a reader computes from the same `Decimal` the archive holds (DATA-04).

Row: `{instrument_id, kind: "mark"|"index"|"funding"|"oi", t: ts_event, ts_init, value}`;
a funding row also carries `interval` (seconds, or None when the venue sent none or a 0) and
`next_funding_ns` (or None). `FundingRateUpdate.interval` is in **minutes** (Nautilus's docstring,
and the adapters: Bybit `fundingIntervalHour * 60`, Hyperliquid a fixed 60; a test pins it on a
real object); the wire carries seconds so `kernel.indicators.funding_annualised` takes one unit.

A reader may meet keys added later (AD-D12: published payloads only gain keys), so unknown keys
are ignored, never refused; a known key with a wrong type or value is refused.
"""

from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from kernel.open_interest import OpenInterest
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate


MARK = "mark"
INDEX = "index"
FUNDING = "funding"
OI = "oi"
KINDS = frozenset({MARK, INDEX, FUNDING, OI})
_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class DerivsTick:
    """One decoded `derivs:raw` row; `interval`/`next_funding_ns` are None unless `kind` is funding."""

    instrument_id: str
    kind: str
    t: int
    ts_init: int
    value: Decimal
    interval: int | None = None
    next_funding_ns: int | None = None

    def to_wire(self) -> dict[str, Any]:
        """Return the canonical row (`from_wire(tick.to_wire()) == tick`), the value as exact text."""
        row: dict[str, Any] = {
            "instrument_id": self.instrument_id,
            "kind": self.kind,
            "t": self.t,
            "ts_init": self.ts_init,
            "value": exact_text(self.value),
        }
        if self.kind == FUNDING:
            row["interval"] = self.interval
            row["next_funding_ns"] = self.next_funding_ns
        return row


def exact_text(value: Decimal) -> str:
    """
    Return `value` as plain positional decimal text, exactly: the one Decimal-to-text rule of every
    "exact decimal text" the platform sends (this wire, `views.derivatives`' pages). `str()` of a
    `Decimal` switches to scientific notation for small magnitudes (`1.2E-7`, a zero change
    `0E-8`), and the stored funding rate is `rust_decimal`'s text, which does too (audit D-108).
    `Decimal()` parses either form back to the same value.
    """
    return format(value, "f")


def to_wire(data: object) -> dict[str, Any] | None:
    """
    Return the `derivs:raw` row of a mark, index, funding or open-interest update; None for any
    other type (the capture buffer holds trades, books and snapshots too, which are not this
    channel's).
    """
    tick = to_tick(data)
    return None if tick is None else tick.to_wire()


def to_tick(data: object) -> DerivsTick | None:
    """
    Return the `DerivsTick` of a mark, index, funding or open-interest update (None for any other
    type): the one conversion of a stored update to exact values, funding's interval in seconds.
    `to_wire` encodes it; `views.derivatives` reads archived updates through it, so a page and a
    live frame carry the same value and the same interval unit.
    """
    kind_value = _kind_and_value(data)
    if kind_value is None:
        return None
    kind, value = kind_value
    interval = next_funding_ns = None
    if isinstance(data, FundingRateUpdate):
        # A 0 or absent interval is "the venue sent none" (a funding interval is never 0, and
        # `from_wire` refuses a zero one): None, so every live funding row decodes.
        interval = int(data.interval) * _SECONDS_PER_MINUTE if data.interval else None
        next_funding_ns = data.next_funding_ns
    return DerivsTick(
        instrument_id=data.instrument_id.value,  # type: ignore[attr-defined]
        kind=kind,
        t=data.ts_event,  # type: ignore[attr-defined]
        ts_init=data.ts_init,  # type: ignore[attr-defined]
        value=value,
        interval=interval,
        next_funding_ns=next_funding_ns,
    )


def _kind_and_value(data: object) -> tuple[str, Decimal] | None:
    # `str(Price)` is the exact decimal at the price's own precision, so `Decimal` of it is exact.
    if isinstance(data, MarkPriceUpdate):
        return MARK, Decimal(str(data.value))
    if isinstance(data, IndexPriceUpdate):
        return INDEX, Decimal(str(data.value))
    if isinstance(data, FundingRateUpdate):
        return FUNDING, Decimal(data.rate)
    if isinstance(data, OpenInterest):
        return OI, Decimal(data.open_interest)
    return None


def from_wire(row: object) -> DerivsTick:
    """Decode one `derivs:raw` row; any malformed row raises `ValueError` naming the fault."""
    if not isinstance(row, dict):
        raise ValueError(f"derivs row is not an object: {row!r}")
    kind = row.get("kind")
    if kind not in KINDS:
        raise ValueError(f"derivs row has an unknown kind {kind!r}")
    instrument_id = row.get("instrument_id")
    if not isinstance(instrument_id, str) or not instrument_id:
        raise ValueError(f"derivs row has no instrument_id: {row!r}")
    funding = kind == FUNDING
    return DerivsTick(
        instrument_id=instrument_id,
        kind=kind,
        t=_timestamp(row, "t"),
        ts_init=_timestamp(row, "ts_init"),
        value=_exact_value(row.get("value")),
        interval=_optional_positive(row, "interval") if funding else None,
        next_funding_ns=_optional_timestamp(row, "next_funding_ns") if funding else None,
    )


def _timestamp(row: dict, key: str) -> int:
    value = row.get(key)
    # bool is an int subclass: `True` is no timestamp.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"derivs row has a bad {key}: {value!r}")
    return value


def _optional_timestamp(row: dict, key: str) -> int | None:
    if row.get(key) is None:
        return None
    return _timestamp(row, key)


def _optional_positive(row: dict, key: str) -> int | None:
    value = _optional_timestamp(row, key)
    if value == 0:
        raise ValueError(f"derivs row has a zero {key}")
    return value


def _exact_value(value: object) -> Decimal:
    # Text only: a JSON number would have gone through a float on the way in.
    if not isinstance(value, str):
        raise ValueError(f"derivs row value is not exact text: {value!r}")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"derivs row value is not a decimal: {value!r}") from None
    if not decimal.is_finite():
        raise ValueError(f"derivs row value is not finite: {value!r}")
    return decimal
