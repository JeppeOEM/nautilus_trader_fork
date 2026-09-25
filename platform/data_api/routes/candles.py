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
`GET /api/candles/{instrument_id}` -- cursor-paginated candle history (AD-F3): the
frontend's chart page fetches its initial 120-bar window and every scroll-back page
through this one `before_ns`/`limit` contract, never an unbounded full-range load.

Format + transport only (Story 24.2): the page itself is `views.chart_series.candle_page` (the
candle store first, the archive's one seconds -> bars fold for what it does not cover) and its
gap rows `views.chart_series.with_gap_markers`. This module clamps the query params, passes its
own `CATALOG_PATH`/`CANDLES_DB_DIR` and the live bus's unflushed tail in, builds the response
models, and maps `ImpossibleCandle` to a 500.

`CATALOG_PATH` comes from `data_api.settings` (a leaf module -- routes can't import it from
`app.py`, which imports them).
"""

from fastapi import APIRouter
from fastapi import HTTPException
from kernel.venues import market_kind
from kernel.venues import venue_of
from pydantic import BaseModel
from views import chart_series

from data_api import buses
from data_api.settings import CANDLES_DB_DIR
from data_api.settings import CATALOG_PATH


# Server-enforced upper bound on `limit`, regardless of what the client requests (AC #7,
# MEM-01 extended to the API surface) -- a module constant, not per-request configurable.
_MAX_CANDLES_LIMIT = 500

# Server-enforced bound on `bar_seconds` -- same silent-clamp philosophy as `limit` above
# (AC #7's "no error, silently clamped"), not a 422: a non-positive value would zero or
# invert every window/bucket computation, and an unbounded one would let a client
# blow up the query span this route otherwise keeps deliberately bounded (AD-F3/MEM-01).
_MAX_BAR_SECONDS = 604_800  # 1w -- the timeframe selector's widest bar

router = APIRouter()


class CandleItem(BaseModel):
    t: int
    o: float | None = None
    h: float | None = None
    l: float | None = None
    c: float | None = None
    v: float | None = None
    # Rollup-sourced bucket observed for < 90% of its span (collector gaps, D-15): its
    # high/low/volume are understated. Absent (None) on raw-1s candles and gap markers.
    partial: bool | None = None


class CandlesResponse(BaseModel):
    items: list[CandleItem]
    has_more: bool
    venue: str
    market: str


@router.get("/api/candles/{instrument_id}")
def get_candles(
    instrument_id: str,
    before_ns: int,
    limit: int = 120,
    bar_seconds: int = 60,
) -> CandlesResponse:
    limit = max(1, min(limit, _MAX_CANDLES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    try:
        kept, has_more = chart_series.candle_page(
            instrument_id,
            before_ns,
            limit,
            bar_seconds,
            catalog_path=CATALOG_PATH,
            candles_dir=CANDLES_DB_DIR,
            recent_rows=buses.live_candle_bus.recent_rows,
        )
    except chart_series.ImpossibleCandle as exc:
        # An impossible candle means upstream code malfunctioned (DATA-07): views has already
        # ledgered it; fail the request so the chart shows an error, never serve or drop it.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not kept:
        return CandlesResponse(
            items=[],
            has_more=False,
            venue=venue_of(instrument_id),
            market=market_kind(instrument_id),
        )
    return CandlesResponse(
        items=[CandleItem(**row) for row in chart_series.with_gap_markers(kept, bar_seconds)],
        has_more=has_more,
        venue=venue_of(instrument_id),
        market=market_kind(instrument_id),
    )
