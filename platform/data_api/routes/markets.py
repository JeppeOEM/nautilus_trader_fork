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
Story 33.9: `GET /api/markets`, every live venue's market names for the chart's Compare picker
(and, Story 33.12, its symbol search), served from `buses.markets_bus` (the process's one
`markets:live` subscriber). The listing -- validation, staleness, expiry and the same-asset
ordering -- is `views.markets_bus.MarketsBus`'s, each item's market type and 24 h volume
`MarketsBus.with_market_details` over `buses.bus`'s cached `rankings:live`; this route only
transports it.
"""

import time

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel

from data_api import buses


router = APIRouter()


class MarketItem(BaseModel):
    """One market; `same_asset` is `kernel.venues.same_asset` against the request's id."""

    instrument_id: str
    symbol: str
    venue: str
    same_asset: bool
    # Story 33.12 (added fields): `kernel.venues.market_kind` (`perp`/`spot`/`unknown`), and the USD
    # 24 h volume from the cached `rankings:live` row, None when that id has no row or no value, the
    # message marks it stale, or the message is older than the listing's staleness horizon.
    market: str
    volume24h: float | None


class MarketsResponse(BaseModel):
    """
    `items` omit the requested `instrument_id` itself, same-asset markets first, then by venue and
    id. `stale_venues` names each listed venue whose list arrived over 180 s ago.
    """

    items: list[MarketItem]
    stale_venues: list[str]


@router.get(
    "/api/markets",
    responses={
        400: {"description": "`instrument_id` has no `.VENUE` suffix"},
        503: {"description": "No venue's `markets:live` list received in the last 900 s"},
    },
)
def get_markets(instrument_id: str | None = None) -> MarketsResponse:
    """
    503 when no venue is live -- an empty list would pose as "no markets" while the ranking engine
    is down. A malformed `instrument_id` raises `MalformedInstrumentId`, the app's 400 handler.
    Read through `buses` (not an imported name) so tests can swap the bus.
    """
    listing = buses.markets_bus.listing(instrument_id, time.monotonic())
    if listing is None:
        raise HTTPException(status_code=503, detail="No venue's market list is live")
    return MarketsResponse.model_validate(
        buses.markets_bus.with_market_details(listing, buses.bus.latest, time.time_ns())
    )
