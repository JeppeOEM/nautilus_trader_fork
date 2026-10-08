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
`GET /api/snapshots/{instrument_id}` -- cursor-paginated Lines-mode history (AD-F3): the
frontend chart page's Lines mode fetches its initial window and every scroll-back page
through this one `before_ns`/`limit` contract, mirroring `routes/candles.py`'s exact
cursor-pagination shape on a new route (no `bar_seconds` -- snapshots are per-second rows,
there is no bar/aggregation concept here).

Format + transport only (Story 24.2): the page is `views.chart_series.snapshot_series_page`
(best bid/ask as exact integer units with their `price_precision` -- formatted for display only
by the frontend's `lib/units.ts`, Story 30.2 -- and the derived float mid/microprice/CVD-weighted
price per archived second, and the gap-marker rendering rule; a gap row is all nulls). Every second the capture gate wrote is returned as written -- a crossed one included; the
reader-side empty-top and crossed-book skips this route used to apply (the AD-3 deviation) are
gone. An archived second with an empty side is a malfunction upstream: views ledgers it and this
route answers 500 naming the instrument and `ts_event`.

`CATALOG_PATH` comes from `data_api.settings` (a leaf module -- routes can't import it from
`app.py`, which imports them).
"""

from fastapi import APIRouter
from fastapi import HTTPException
from kernel.venues import market_kind
from kernel.venues import venue_of
from pydantic import BaseModel
from views import chart_series

from data_api.settings import CATALOG_PATH


# Server-enforced upper bound on `limit` (MEM-01/AD-F3) -- sized in rows-per-second terms,
# much larger than a candle limit (`_MAX_CANDLES_LIMIT = 500` bars) since there is no
# bar-aggregation step here: 5000 one-second rows is a bit under 1.5 hours of raw snapshot
# history in one response, comfortably above the ~15-minute/900-row default page while
# still bounding the per-request read/serialization cost. Public: app.py's legacy /catalog routes
# bound their raw-second window by the same ceiling.
MAX_SNAPSHOTS_LIMIT = 5_000

router = APIRouter()


class SnapshotSeriesPoint(BaseModel):
    t: int
    bid_units: int | None = None
    ask_units: int | None = None
    price_precision: int | None = None
    mid: float | None = None
    micro: float | None = None
    price: float | None = None


class SnapshotSeriesResponse(BaseModel):
    items: list[SnapshotSeriesPoint]
    has_more: bool
    venue: str
    market: str


@router.get("/api/snapshots/{instrument_id}")
def get_snapshots(instrument_id: str, before_ns: int, limit: int = 900) -> SnapshotSeriesResponse:
    limit = max(1, min(limit, MAX_SNAPSHOTS_LIMIT))
    try:
        kept, has_more = chart_series.snapshot_series_page(
            CATALOG_PATH, instrument_id, before_ns, limit
        )
    except (chart_series.EmptyTopOfBook, chart_series.DuplicateSecond) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SnapshotSeriesResponse(
        items=[SnapshotSeriesPoint(**row) for row in kept],
        has_more=has_more,
        venue=venue_of(instrument_id),
        market=market_kind(instrument_id),
    )
