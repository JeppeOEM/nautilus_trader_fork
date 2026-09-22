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
The only parser of an `InstrumentId` string (DDD spine AD-D3; stories 19.1, 19.6, 22.4).

Nautilus ids are `"{SYMBOL}.{VENUE}"`, and every venue this platform collects carries its market
type as the symbol's last `-` segment (`BTC-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`,
`BTCUSDT-SPOT.BYBIT`, `BTC-USD-PERP.HYPERLIQUID`). `venue` is derived from the id, never stored
as a separate column (19.1).

Invariant: every venue dispatch and market-kind decision reads the id through this module, so
two contexts can never disagree about what an id means. A new venue is one more line in
`VENUE_KINDS`.

`venue_kind` is deliberately not Nautilus's `Venue.is_dex()`: that only fires on a
`"<Chain>:<DexType>"` venue string behind the `defi` feature, and none of dYdX/Bybit/Hyperliquid
use that format, so it would answer wrongly for all three (19.6).
"""

from types import MappingProxyType


VENUE_KINDS = MappingProxyType({"DYDX": "dex", "HYPERLIQUID": "dex", "BYBIT": "cex"})

_PERP_SUFFIXES = frozenset({"PERP", "LINEAR", "INVERSE"})
_BYBIT_CATEGORIES = MappingProxyType({"LINEAR": "linear", "INVERSE": "inverse", "SPOT": "spot"})


class MalformedInstrumentId(ValueError):
    """An instrument id this platform cannot interpret (no `.VENUE` suffix, or the wrong shape)."""


def venue_of(instrument_id: str) -> str:
    """Return the id's venue (`BTC-USD-PERP.DYDX` -> `DYDX`); `MalformedInstrumentId` if none."""
    symbol, dot, venue = instrument_id.rpartition(".")
    if not (symbol and dot and venue):
        raise MalformedInstrumentId(f"instrument_id has no venue suffix: {instrument_id!r}")
    return venue


def has_venue(instrument_id: str, venue: str) -> bool:
    """Return whether the id belongs to `venue` (False for a malformed id; never raises)."""
    try:
        return venue_of(instrument_id) == venue
    except MalformedInstrumentId:
        return False


def venue_kind(venue: str) -> str:
    """Return "cex" | "dex", or "unknown" for a venue not yet registered (never raises)."""
    return VENUE_KINDS.get(venue, "unknown")


def market_suffix(instrument_id: str) -> str | None:
    """
    Return the symbol's market-type segment (`BTCUSDT-LINEAR.BYBIT` -> `LINEAR`), or None when the id
    has no venue suffix or its symbol has no `-` segment (never raises).
    """
    symbol, dot, _venue = instrument_id.rpartition(".")
    if not dot:
        return None
    head, dash, suffix = symbol.rpartition("-")
    return suffix if head and dash and suffix else None


def market_kind(instrument_id: str) -> str:
    """
    Return "perp" | "spot" | "unknown" from the Nautilus id's symbol suffix (never raises). A
    symbol with no `-` is read whole, as `common.venues.market_kind` always did (`PERP.X` ->
    "perp"); `market_suffix` is the strict form.
    """
    symbol, dot, _venue = instrument_id.rpartition(".")
    if not dot:
        return "unknown"
    suffix = symbol.rpartition("-")[2]
    if suffix in _PERP_SUFFIXES:
        return "perp"
    return "spot" if suffix == "SPOT" else "unknown"


def bybit_category(instrument_id: str) -> str:
    """
    Bybit's REST `category` for a Nautilus Bybit id: `-LINEAR`/`-INVERSE` (both `market_kind`
    "perp") -> `linear`/`inverse`, `-SPOT` -> `spot`; `MalformedInstrumentId` for anything else.
    """
    suffix = market_suffix(instrument_id)
    if not has_venue(instrument_id, "BYBIT") or market_kind(instrument_id) == "unknown":
        raise MalformedInstrumentId(f"{instrument_id}: no Bybit category for this id")
    category = _BYBIT_CATEGORIES.get(suffix or "")
    if category is None:
        raise MalformedInstrumentId(f"{instrument_id}: no Bybit category for this id suffix")
    return category
