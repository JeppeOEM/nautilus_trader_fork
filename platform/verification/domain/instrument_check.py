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
Instrument definitions proven against every venue poll of the day (Story 31.6), pure: what each
venue's own definition endpoint says an instrument is, written from the venue docs (never from the
adapters' parsers, `crates/adapters/*/src/*/parse.rs`, which are what is checked), and the
judgement of every poll against the stored definition in force.

- Bybit linear (`/v5/market/instruments-info?category=linear`): `priceFilter.tickSize` is the
  price increment and `priceScale` the price precision; `lotSizeFilter.qtyStep` is the size
  increment and the lot size, its written decimals the size precision; `minOrderQty` the minimum
  quantity; the multiplier is 1 (a linear contract is one unit of the base coin).
- Bybit spot (`category=spot`): the same, with `lotSizeFilter.basePrecision` in place of `qtyStep`
  and the price precision the written decimals of `tickSize` (spot has no `priceScale`).
- Hyperliquid perp (`metaAndAssetCtxs` `universe`): `szDecimals` is the size precision, so the
  size increment is 10^-szDecimals; prices carry at most `6 - szDecimals` decimals ("Tick and lot
  size" in the venue docs), so that is the price precision and 10^-it the increment; there is no
  minimum quantity (the venue's minimum is a notional) and the multiplier is 1. The venue declares
  no lot size: the stored `1` is Nautilus's default, shown and never compared (audit D-106).

Invariant: increments compare as `Decimal` values (`0.10` equals `0.1`), precisions as ints;
every poll is judged against the definition in force when it was received -- the latest stored
row with `ts_init` at or before its `recv_ns`.
"""

from collections import Counter
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from verification.domain.conservation import EXAMPLES


PRICE_PRECISION = "price_precision"
SIZE_PRECISION = "size_precision"
PRICE_INCREMENT = "price_increment"
SIZE_INCREMENT = "size_increment"
LOT_SIZE = "lot_size"
MIN_QUANTITY = "min_quantity"
MULTIPLIER = "multiplier"
FIELDS = (
    PRICE_PRECISION,
    SIZE_PRECISION,
    PRICE_INCREMENT,
    SIZE_INCREMENT,
    LOT_SIZE,
    MIN_QUANTITY,
    MULTIPLIER,
)

# Hyperliquid perps: a price carries at most this many decimals minus `szDecimals` (venue docs).
HYPERLIQUID_PERP_MAX_DECIMALS = 6

AGREE = "agree"
DIFFERS = "differs"
BEFORE_FIRST_DEFINITION = "before_first_definition"
FAILED = "failed"
POLL_CLASSES = (AGREE, DIFFERS, BEFORE_FIRST_DEFINITION, FAILED)

FieldValue = Decimal | int | None


class OddVenueDefinition(ValueError):
    """
    A venue poll whose definition fields are missing or not the documented shape: a venue oddity,
    counted as a `failed` poll by the caller -- never a refusal of the run.
    """


@dataclass(frozen=True)
class VenueDefinition:
    """
    What one venue poll says: every field of `FIELDS` except `not_declared` (fields the venue has
    no counterpart for, never compared).
    """

    recv_ns: int
    fields: Mapping[str, FieldValue]
    not_declared: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredDefinition:
    """One stored definition row: when capture wrote it (`ts_init`) and its fields."""

    ts_init: int
    fields: Mapping[str, FieldValue]


def _text(item: Mapping[str, Any], path: tuple[str, ...], where: str) -> str:
    value: Any = item
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    if not isinstance(value, str) or not value:
        raise OddVenueDefinition(f"{where}: `{'.'.join(path)}` is {value!r:.80}, not a string")
    return value


def _step(item: Mapping[str, Any], path: tuple[str, ...], where: str) -> Decimal:
    """Return a positive venue step (`tickSize`, `qtyStep`, ...): ASCII digits and a fraction."""
    text = _text(item, path, where)
    whole, _, fraction = text.partition(".")
    if not (text.isascii() and whole.isdigit() and (not fraction or fraction.isdigit())):
        raise OddVenueDefinition(f"{where}: `{'.'.join(path)}` {text!r} is not a decimal step")
    value = Decimal(text)
    if value <= 0:
        raise OddVenueDefinition(f"{where}: `{'.'.join(path)}` {text!r} is not positive")
    return value


def written_decimals(step: Decimal) -> int:
    """Return the decimals a step is written with (`0.10` -> 2, `1` -> 0), the venues' precision rule."""
    exponent = step.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _bybit(
    item: Mapping[str, Any], size_key: str, price_precision: int, recv_ns: int, where: str
) -> VenueDefinition:
    tick = _step(item, ("priceFilter", "tickSize"), where)
    step = _step(item, ("lotSizeFilter", size_key), where)
    fields: dict[str, FieldValue] = {
        PRICE_PRECISION: price_precision,
        SIZE_PRECISION: written_decimals(step),
        PRICE_INCREMENT: tick,
        SIZE_INCREMENT: step,
        LOT_SIZE: step,
        MIN_QUANTITY: _step(item, ("lotSizeFilter", "minOrderQty"), where),
        MULTIPLIER: Decimal(1),
    }
    return VenueDefinition(recv_ns, MappingProxyType(fields))


def bybit_linear_definition(item: Mapping[str, Any], recv_ns: int, where: str) -> VenueDefinition:
    """Return a Bybit linear `instruments-info` item's definition."""
    scale = _text(item, ("priceScale",), where)
    if not (scale.isascii() and scale.isdigit()):
        raise OddVenueDefinition(f"{where}: `priceScale` {scale!r} is not an integer")
    return _bybit(item, "qtyStep", int(scale), recv_ns, where)


def bybit_spot_definition(item: Mapping[str, Any], recv_ns: int, where: str) -> VenueDefinition:
    """Return a Bybit spot `instruments-info` item's definition."""
    tick = _step(item, ("priceFilter", "tickSize"), where)
    return _bybit(item, "basePrecision", written_decimals(tick), recv_ns, where)


def hyperliquid_definition(asset: Mapping[str, Any], recv_ns: int, where: str) -> VenueDefinition:
    """Return a Hyperliquid perp `universe` entry's definition (`lot_size` not declared)."""
    decimals = asset.get("szDecimals") if isinstance(asset, dict) else None
    if not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0:
        raise OddVenueDefinition(
            f"{where}: `szDecimals` {decimals!r} is not a non-negative integer"
        )
    price_decimals = max(0, HYPERLIQUID_PERP_MAX_DECIMALS - decimals)
    fields: dict[str, FieldValue] = {
        PRICE_PRECISION: price_decimals,
        SIZE_PRECISION: decimals,
        PRICE_INCREMENT: Decimal(1).scaleb(-price_decimals),
        SIZE_INCREMENT: Decimal(1).scaleb(-decimals),
        MIN_QUANTITY: None,
        MULTIPLIER: Decimal(1),
    }
    return VenueDefinition(recv_ns, MappingProxyType(fields), not_declared=(LOT_SIZE,))


def in_force(stored: Sequence[StoredDefinition], ts_ns: int) -> StoredDefinition | None:
    """Return the latest stored definition (sorted by `ts_init`) written at or before `ts_ns`."""
    found = None
    for definition in stored:
        if definition.ts_init > ts_ns:
            break
        found = definition
    return found


def _differences(venue: VenueDefinition, stored: StoredDefinition) -> list[str]:
    return [
        f"{name}: stored {stored.fields.get(name)}, venue {venue.fields.get(name)}"
        for name in FIELDS
        if name not in venue.not_declared and stored.fields.get(name) != venue.fields.get(name)
    ]


def _changes(previous: VenueDefinition, current: VenueDefinition) -> list[str]:
    return [
        f"{current.recv_ns} {name}: {previous.fields.get(name)} -> {current.fields.get(name)}"
        for name in FIELDS
        if previous.fields.get(name) != current.fields.get(name)
    ]


@dataclass(frozen=True)
class DefinitionReport:
    """
    One instrument's definition verdicts over the day's venue polls. It fails on any `differs`
    poll, when no definition is stored at all (`no_definition`), or when no poll was judged
    against a definition (nothing verified).
    """

    polls: Mapping[str, int]
    differs: tuple[str, ...]
    venue_changes: tuple[str, ...]
    definitions: tuple[int, ...]
    not_declared: tuple[str, ...]
    no_definition: bool

    @property
    def verified(self) -> int:
        return self.polls.get(AGREE, 0) + self.polls.get(DIFFERS, 0)

    @property
    def failing(self) -> int:
        """The `differs` polls, plus 1 when no definition is stored (nothing verified is not one)."""
        return self.polls.get(DIFFERS, 0) + int(self.no_definition)

    @property
    def passed(self) -> bool:
        return not self.no_definition and not self.polls.get(DIFFERS, 0) and self.verified > 0


def _not_declared(
    polls: Sequence[VenueDefinition], stored: Sequence[StoredDefinition]
) -> tuple[str, ...]:
    names = sorted({name for poll in polls for name in poll.not_declared})
    shown = {name: sorted({str(d.fields.get(name)) for d in stored}) for name in names}
    return tuple(
        f"{name}: not_venue_declared (stored {', '.join(shown[name]) or 'none'}: Nautilus default)"
        for name in names
    )


def judge_polls(
    polls: Sequence[VenueDefinition], failed: int, stored: Sequence[StoredDefinition]
) -> DefinitionReport:
    """Judge every good venue poll (receipt order) against the stored definition in force."""
    ordered = sorted(stored, key=lambda d: d.ts_init)
    classes: Counter[str] = Counter({FAILED: failed})
    differs: list[str] = []
    changes: list[str] = []
    previous: VenueDefinition | None = None
    for poll in sorted(polls, key=lambda p: p.recv_ns):
        if previous is not None:
            changes += _changes(previous, poll)
        previous = poll
        definition = in_force(ordered, poll.recv_ns)
        found = _differences(poll, definition) if definition is not None else []
        verdict = BEFORE_FIRST_DEFINITION if definition is None else (DIFFERS if found else AGREE)
        classes[verdict] += 1
        differs += [f"{poll.recv_ns} {text}" for text in found][: EXAMPLES - len(differs)]
    return DefinitionReport(
        polls=MappingProxyType({name: classes.get(name, 0) for name in POLL_CLASSES}),
        differs=tuple(differs),
        venue_changes=tuple(changes),
        definitions=tuple(d.ts_init for d in ordered),
        not_declared=_not_declared(polls, ordered),
        no_definition=not ordered,
    )
