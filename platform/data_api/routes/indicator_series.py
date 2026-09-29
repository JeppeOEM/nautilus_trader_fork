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
`GET /api/indicator-series/{instrument_id}` -- cursor-paginated OFI/OBI/microprice/spread
history (AD-F3): the frontend's indicator sub-panes fetch their initial window and every
scroll-back page through this one `before_ns`/`limit`/`bar_seconds` contract, mirroring
`routes/candles.py`'s own contract exactly so both routes can be co-paged from the same
scroll-back trigger.

Format + transport only (Story 24.2): the page -- the bounded historical OFI/OBI replay, the
per-bucket microprice/spread and the bar gap rows -- is `views.chart_series.indicator_series_page`.
This module clamps the query params, passes its own `CATALOG_PATH` in and builds the response.

Deliberately its own file, not folded into `routes/indicators.py` (Story 15.6's
`/api/indicators/catalog` + config-persistence concern).

`CATALOG_PATH` comes from `data_api.settings` (a leaf module -- routes can't import it from
`app.py`, which imports them).
"""

from fastapi import APIRouter
from kernel.venues import market_kind
from kernel.venues import venue_of
from pydantic import BaseModel
from views import chart_series

from data_api.settings import CATALOG_PATH


# This route's own clamps (MEM-01 extended to this route, independently of candles.py's values).
_MAX_INDICATOR_SERIES_LIMIT = 500
# 1W, the candles route's own bound (Story 31.3: was a silent clamp to 1D). The replay's raw-second
# read is capped at `chart_series.MAX_QUERY_SPAN_SECONDS` (7 days) whatever the bar size. Known limit:
# a 1W pane therefore holds at most two Monday-anchored buckets per page, the older one computed from
# only the days inside the read; upgrade path: a stored per-bar OFI/OBI series (like the candle
# store), paged like candles instead of replayed from raw seconds.
_MAX_BAR_SECONDS = 604_800

router = APIRouter()


class IndicatorSeriesPoint(BaseModel):
    t: int
    ofi: float | None = None
    obi: float | None = None
    microprice: float | None = None
    spread: float | None = None


class IndicatorSeriesResponse(BaseModel):
    items: list[IndicatorSeriesPoint]
    has_more: bool
    venue: str
    market: str


@router.get("/api/indicator-series/{instrument_id}")
def get_indicator_series(
    instrument_id: str,
    before_ns: int,
    limit: int = 120,
    bar_seconds: int = 60,
) -> IndicatorSeriesResponse:
    limit = max(1, min(limit, _MAX_INDICATOR_SERIES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    rows, has_more = chart_series.indicator_series_page(
        CATALOG_PATH, instrument_id, before_ns, limit, bar_seconds
    )
    return IndicatorSeriesResponse(
        items=[IndicatorSeriesPoint(**row) for row in rows],
        has_more=has_more,
        venue=venue_of(instrument_id),
        market=market_kind(instrument_id),
    )
