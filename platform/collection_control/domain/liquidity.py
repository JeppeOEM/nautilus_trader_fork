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
Liquidity classification by 24-hour USD volume: the one rule that admits a pin (OBS-03).

Uses `volume24H` (already USD), never `openInterest` (base-token units): BTC's 458 tokens would
fail a USD threshold and read as illiquid (OBS-01's production root cause).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class LiquidityTier(StrEnum):
    LIQUID = "liquid"
    ILLIQUID = "illiquid"


@dataclass(frozen=True)
class LiquidityClassification:
    """
    One markets snapshot split by USD volume at `min_volume_usd`.

    Invariant: an id is in at most one of `liquid`, `illiquid` and `unparseable` -- a market whose
    volume cannot be read is left unclassified, never reclassified as illiquid (no volume is not
    zero volume, DATA-01). `volumes` holds every parsed, non-excluded market's volume in the
    venue's order, which is also the tie order the liquid cap uses.
    """

    liquid: frozenset[str]
    illiquid: frozenset[str]
    volumes: Mapping[str, float]
    unparseable: Mapping[str, str]  # id -> the raw volume24H value's repr
    min_volume_usd: float

    def tier(self, iid: str) -> LiquidityTier | None:
        """Return the id's tier, or None when it was not classified (unparseable, not listed)."""
        if iid in self.liquid:
            return LiquidityTier.LIQUID
        if iid in self.illiquid:
            return LiquidityTier.ILLIQUID
        return None


def _volume(market: Mapping[str, Any], iid: str, unparseable: dict[str, str]) -> float | None:
    """
    Return the USD volume (missing reads as 0), or None -- recorded -- when unparseable. `float`
    accepts "NaN", "inf" and negatives, none of which is a volume: a NaN would read as illiquid
    (every comparison is False) and an infinity would win every pin, so they are unparseable too.
    """
    raw = market.get("volume24H")
    try:
        volume = float(raw or 0)
    except (ValueError, TypeError):
        volume = math.nan
    if not math.isfinite(volume) or volume < 0:
        unparseable[iid] = repr(raw)
        return None
    return volume


def classify_liquidity(
    markets_json: Mapping[str, Any],
    min_volume_usd: float,
    exclude: frozenset[str] | None = None,
    max_liquid: int | None = None,
) -> LiquidityClassification:
    """
    Split every dYdX market (`{ticker}-PERP.DYDX`) into liquid and illiquid by `volume24H`.

    An id in `exclude` is illiquid regardless of volume. `max_liquid`, if given, keeps only the
    highest-volume markets liquid and demotes the rest (lowest first, ties in the venue's order):
    the caller sizes it to the plan's free slots, which keep dYdX under its per-connection
    subscription limit. A missing volume reads as 0; one that cannot be parsed is recorded in
    `unparseable` for the caller to ledger.
    """
    excluded = exclude or frozenset()
    liquid: dict[str, float] = {}
    illiquid: set[str] = set()
    volumes: dict[str, float] = {}
    unparseable: dict[str, str] = {}
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        if ticker is None:
            continue
        iid = f"{ticker}-PERP.DYDX"
        if iid in excluded:
            illiquid.add(iid)
            continue
        vol = _volume(market, iid, unparseable)
        if vol is None:
            continue
        volumes[iid] = vol
        if vol >= min_volume_usd:
            liquid[iid] = vol
        else:
            illiquid.add(iid)
    _cap(liquid, illiquid, max_liquid)
    return LiquidityClassification(
        liquid=frozenset(liquid),
        illiquid=frozenset(illiquid),
        volumes=MappingProxyType(volumes),
        unparseable=MappingProxyType(unparseable),
        min_volume_usd=float(min_volume_usd),
    )


def _cap(liquid: dict[str, float], illiquid: set[str], max_liquid: int | None) -> None:
    """Demote the lowest-volume liquid markets past `max_liquid` (stable: ties keep venue order)."""
    if max_liquid is None or len(liquid) <= max_liquid:
        return
    for iid in sorted(liquid, key=liquid.__getitem__)[: len(liquid) - max_liquid]:
        illiquid.add(iid)
        del liquid[iid]
