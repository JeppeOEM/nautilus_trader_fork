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

Same-asset matching (Story 27.4): `asset_key` reads an id's base, quote class and kind, so the
same asset on dYdX, Bybit and Hyperliquid is found without research ever splitting an id.

Base symbol (Story 29.1): `base_symbol` reads the coin an id trades (`BTCUSDT-LINEAR.BYBIT` ->
`BTC`), so the web rankings line the same coin up across exchanges without a second id parse.
"""

from types import MappingProxyType
from typing import NamedTuple


VENUE_KINDS = MappingProxyType({"DYDX": "dex", "HYPERLIQUID": "dex", "BYBIT": "cex"})

_PERP_SUFFIXES = frozenset({"PERP", "LINEAR", "INVERSE"})
_BYBIT_CATEGORIES = MappingProxyType({"LINEAR": "linear", "INVERSE": "inverse", "SPOT": "spot"})

# Quotes that are one quote class for same-asset matching (Story 27.4): the dollar and the two
# dollar stablecoins. Matching only -- a basis between a USDT and a USDC leg still carries the
# USDT/USDC spread, which this table deliberately does not remove.
USD_QUOTES = frozenset({"USD", "USDC", "USDT"})
# The quote a Bybit symbol head ends with (`BTCUSDT`), per the head's suffix. `PERP` is Bybit's
# USDC perpetual (`BTCPERP-LINEAR`), valid only on `-LINEAR`. Any other suffix (`BTCUSD1`,
# `ETHBTC`, `BBSOLSOL`) is not read: no guess at where the base ends.
_BYBIT_QUOTE_SUFFIXES = MappingProxyType({"USDT": "USDT", "USDC": "USDC", "PERP": "USDC"})
_BYBIT_ASSET_KINDS = MappingProxyType({"LINEAR": "perp", "SPOT": "spot"})
# The quotes `base_symbol` strips from a Bybit symbol head (Story 29.1), first match wins.
# Ordered so `USDT`/`USDC` are tried before their `USD` prefix.
BYBIT_QUOTES = ("USDT", "USDC", "PERP", "USD")
# Venues whose symbols are exactly `BASE-QUOTE-PERP` (dYdX v4 markets, Hyperliquid perps).
_DASHED_PERP_VENUES = frozenset({"DYDX", "HYPERLIQUID"})


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
    symbol with no `-` is read whole, as the pre-kernel `market_kind` always did (`PERP.X` ->
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


class AssetKey(NamedTuple):
    """
    What an instrument trades, venue aside: `base` (`BTC`), `quote` class (`USD` for every
    `USD_QUOTES` member) and `kind` (`perp` | `spot`). Invariant: two ids are the same asset
    exactly when their keys are equal and neither is None (`same_asset`).
    """

    base: str
    quote: str
    kind: str


def _quote_class(quote: str) -> str:
    return "USD" if quote in USD_QUOTES else quote


def _dashed_perp(symbol: str) -> AssetKey | None:
    """`BASE-QUOTE-PERP` exactly (three segments, an alphanumeric base and quote), else None."""
    parts = symbol.split("-")
    if len(parts) != 3 or parts[2] != "PERP":
        return None
    base, quote = parts[0], parts[1]
    if not (base.isalnum() and quote.isalnum()):
        return None
    return AssetKey(base, _quote_class(quote), "perp")


def _bybit_asset(symbol: str) -> AssetKey | None:
    """`<head>-LINEAR|SPOT` exactly, the head ending in a `_BYBIT_QUOTE_SUFFIXES` key, else None."""
    head, dash, suffix = symbol.partition("-")
    kind = _BYBIT_ASSET_KINDS.get(suffix)
    if not dash or kind is None:
        return None
    quote = _BYBIT_QUOTE_SUFFIXES.get(head[-4:])
    base = head[:-4]
    if quote is None or not base.isalnum() or (head.endswith("PERP") and kind != "perp"):
        return None
    return AssetKey(base, _quote_class(quote), kind)


def asset_key(instrument_id: str) -> AssetKey | None:
    """
    Return the id's `AssetKey`, or None when these tables cannot read it (never raises, never
    guesses): dYdX and Hyperliquid take exactly `BASE-QUOTE-PERP` (so Hyperliquid spot and
    builder-dex ids such as `km:US500-USD-PERP` are None); Bybit takes exactly `<head>-LINEAR` or
    `<head>-SPOT` whose head ends in `USDT`, `USDC` or (linear only) `PERP` after a non-empty
    alphanumeric base, so a dated future (`BTCUSDT-25SEP26-LINEAR`), an inverse contract and an
    odd spot quote (`ETHBTC`, `BTCUSD1`) are None.

    Known limit: a base named with a multiplier (`1000PEPEUSDT` on Bybit vs `kPEPE` on
    Hyperliquid) never matches its other venues' base; upgrade path: a base-alias table here.
    """
    try:
        venue = venue_of(instrument_id)
    except MalformedInstrumentId:
        return None
    symbol = instrument_id.rpartition(".")[0]
    if venue in _DASHED_PERP_VENUES:
        return _dashed_perp(symbol)
    if venue == "BYBIT":
        return _bybit_asset(symbol)
    return None


def same_asset(a: str, b: str) -> bool:
    """Whether both ids have an `AssetKey` and it is the same one (never raises)."""
    key = asset_key(a)
    return key is not None and key == asset_key(b)


def _bybit_base(head: str) -> str:
    """Strip the head's first matching `BYBIT_QUOTES` suffix if a base remains, else keep it."""
    for quote in BYBIT_QUOTES:
        if head.endswith(quote) and len(head) > len(quote):
            return head[: -len(quote)]
    return head


def base_symbol(instrument_id: str) -> str:
    """
    Return the base coin the id trades (`MalformedInstrumentId` only for an id with no `.VENUE`
    suffix; an unlisted quote is never guessed at): Bybit takes the symbol head before its first `-`
    (`BTCUSDT-25SEP26-LINEAR` -> `BTCUSDT`) minus the first matching `BYBIT_QUOTES` suffix when a
    non-empty base remains (`1000PEPEUSDT` -> `1000PEPE`), else the head whole (`ETHBTC`);
    dYdX, Hyperliquid and any unknown venue take the symbol's first `-` segment
    (`km:US500-USD-PERP` -> `km:US500`).

    Known limit: the head is split by suffix only, so a head whose base or unlisted quote itself
    ends in a quote name is cut in the wrong place (`ETHBUSD` reads `ETHB`; a base `XUSD` with
    no quote at all reads `X`), and a quote not in `BYBIT_QUOTES` (`ETHBTC`) returns the head
    whole rather than a guessed base. A base named with a multiplier (`1000PEPE` on Bybit,
    `kPEPE` on Hyperliquid) is its own symbol, so those rows do not line up (as `asset_key`);
    likewise a Hyperliquid HIP-3 dex-prefixed base (`xyz:BTC`) never lines up with `BTC`.
    A symbol whose first `-` segment is empty (`-LINEAR`) is returned whole, never as `""`.
    Upgrade path: read the venue's instrument definition, which the catalog stores, and use its
    `base_currency` instead of parsing the id.
    """
    venue = venue_of(instrument_id)
    symbol = instrument_id.rpartition(".")[0]
    head = symbol.partition("-")[0]
    if not head:
        return symbol
    return _bybit_base(head) if venue == "BYBIT" else head
