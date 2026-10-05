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
`GET /api/coin/{instrument_id}/footprint` -- the volume footprint of the chart's closed bars
(Story 32.8): per bar, the archive's trades in integer price rows (`{p, b, s}` units), its delta,
total and POC, on the candles' own `before_ns`/`limit`/`bar_seconds` cursor contract.

Format + transport only (SSOT-02): the page is `views.chart_series.footprint_page` over the same
`candle_page` the chart draws. This module clamps `limit` to `MAX_FOOTPRINT_BARS` and
`bar_seconds` like the candles route, parses `row_ticks` (`auto` or an integer >= 1, else a 422
naming it), reads the definition's precisions (404 without one) and maps `ImpossibleCandle`,
`TradeDecodeError` and `FootprintOverflow` (each already ledgered by `views`) to a 500.

Every number is an integer: prices are units of `10^-price_precision`, sizes of
`10^-size_precision`, formatted by the frontend's `lib/units.ts`, never through `float` here.
"""

import time

from fastapi import APIRouter
from fastapi import HTTPException
from kernel.catalog_files import TradeDecodeError
from kernel.venues import venue_of
from pydantic import BaseModel
from views import chart_series
from views.catalog_reads import NoInstrumentDefinition
from views.catalog_reads import instrument_precision
from views.preferences import MAX_FOOTPRINT_ROW_TICKS

from data_api import buses
from data_api.settings import CANDLES_DB_DIR
from data_api.settings import CATALOG_PATH


ROW_TICKS_AUTO = "auto"
# The candles route's silent `bar_seconds` clamp (1w, the timeframe selector's widest bar).
_MAX_BAR_SECONDS = 604_800

router = APIRouter()


class FootprintRow(BaseModel):
    p: int  # the row's floor price, units of 10^-price_precision
    b: int  # buy (BUYER aggressor) size units
    s: int  # sell size units: SELLER and NO_AGGRESSOR, the candle fold's rule


class FootprintItem(BaseModel):
    t: int  # bar start, ms
    # None (and `rows` empty, the totals None) on a `no_trades` bar: a gap, never zeros.
    row_ticks: int | None
    rows: list[FootprintRow]
    delta: int | None
    total: int | None
    poc_row: int | None
    no_trades: bool


class FootprintResponse(BaseModel):
    items: list[FootprintItem]
    has_more: bool
    price_precision: int
    size_precision: int


def parse_row_ticks(value: str) -> int | None:
    """Return None for `auto`, else the integer >= 1; anything else is a 422 naming `row_ticks`."""
    if value == ROW_TICKS_AUTO:
        return None
    # The length check first: `int()` refuses a string of over 4300 digits with its own error.
    digits = len(str(MAX_FOOTPRINT_ROW_TICKS))
    if (
        not (value.isascii() and value.isdigit())
        or len(value) > digits
        or not 1 <= int(value) <= MAX_FOOTPRINT_ROW_TICKS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"row_ticks: must be 'auto' or an integer 1..{MAX_FOOTPRINT_ROW_TICKS} (the "
                f"layout's own bound), got {value!r}"
            ),
        )
    return int(value)


@router.get("/api/coin/{instrument_id}/footprint")
def get_footprint(
    instrument_id: str,
    before_ns: int,
    limit: int = 120,
    bar_seconds: int = 60,
    row_ticks: str = ROW_TICKS_AUTO,
) -> FootprintResponse:
    ticks = parse_row_ticks(row_ticks)
    limit = max(1, min(limit, chart_series.MAX_FOOTPRINT_BARS))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    venue_of(instrument_id)  # a malformed id is a 400 (the app's handler), as on every coin route
    try:
        precision = instrument_precision(CATALOG_PATH, instrument_id)
    except NoInstrumentDefinition as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        bars, has_more = chart_series.footprint_page(
            instrument_id,
            before_ns,
            limit,
            bar_seconds,
            ticks,
            catalog_path=CATALOG_PATH,
            candles_dir=CANDLES_DB_DIR,
            recent_rows=buses.live_candle_bus.recent_rows,
            price_precision=precision.price_precision,
            size_precision=precision.size_precision,
            now_ns=time.time_ns(),
        )
    except (
        chart_series.ImpossibleCandle,
        TradeDecodeError,
        chart_series.FootprintOverflow,
    ) as exc:
        # Upstream malfunction (DATA-07), already ledgered by views: fail loud, never serve it.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FootprintResponse(
        items=[FootprintItem(**bar) for bar in bars],
        has_more=has_more,
        price_precision=precision.price_precision,
        size_precision=precision.size_precision,
    )
