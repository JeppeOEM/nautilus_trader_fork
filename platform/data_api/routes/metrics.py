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
`GET /api/metrics/history/{symbol}` / `GET /api/metrics/nearest/{symbol}` -- relocated
(AD-F2), not reimplemented: both call `views.coin_detail.metrics_history`/`metrics_nearest`, the
views read over the ranking context's query service `history()`/`nearest()` (Story 24.2;
`ranking.application.queries` since Story 25.2), unchanged. Story 17.2 (originally 15.8)'s 31-day metrics-history page fetches
its trailing-31-day window through the first of these; the second exists for any
nearest-value lookup that page (or a future one) needs.

Deliberately NOT cursor-paginated -- a documented exception to AD-F3's "every history
endpoint... without exception" language, not an oversight. The ranking query service's `history()` is
already a small, fixed-size 31-day window (one row per instrument per polling tick, not
per-second order-book depth), fetched once per page load, with no scroll-back concept.

These routes under `/api/metrics/...` coexist with the bare legacy
`/metrics/history/{symbol}`/`/metrics/nearest/{symbol}` routes in `app.py` (the remote-mode
targets of the old `dashboard.py`, retired by Story 15.10, kept as legacy read routes) -- same
"old vs. new namespace coexist" pattern Story 15.3 established for `/catalog/candles` vs.
`/api/candles`.

`METRICS_DB_PATH` is defined once in `data_api.settings` and imported as a module-level name of
this module, so tests keep monkeypatching `routes.metrics.METRICS_DB_PATH`.
"""

from typing import Annotated

from fastapi import APIRouter
from fastapi import Query
from pydantic import BaseModel
from views import coin_detail

from data_api.settings import METRICS_DB_PATH


router = APIRouter()


class MetricHistoryItem(BaseModel):
    """
    One row per `ranking.infrastructure.metrics_store.COLS` (plus its own `ts`) -- a `None` field
    is the real gap signal (DATA-01/AD-F6), never coerced to `0` or dropped from the
    response. Field list mirrors `COLS` verbatim; extend both together if `COLS` changes.
    """

    ts: int
    price: float | None = None
    pct_1h: float | None = None
    pct_24h: float | None = None
    pct_1w: float | None = None
    pct_1m: float | None = None
    volatility: float | None = None
    ofi: float | None = None
    microprice: float | None = None
    spread: float | None = None
    rank: float | None = None
    volume24h: float | None = None


class MetricsHistoryResponse(BaseModel):
    items: list[MetricHistoryItem]


@router.get("/api/metrics/history/{symbol}")
def get_metrics_history(
    symbol: str,
    # Bounded by metrics.db's own retention: a wider window would silently return fewer days than
    # asked, and an unbounded one overflowed SQLite's int64 cutoff into an opaque 500.
    days: Annotated[int, Query(ge=1, le=coin_detail.METRICS_HISTORY_MAX_DAYS)] = (
        coin_detail.METRICS_HISTORY_MAX_DAYS
    ),
) -> MetricsHistoryResponse:
    rows = coin_detail.metrics_history(symbol, METRICS_DB_PATH, days)
    return MetricsHistoryResponse(items=[MetricHistoryItem(**row) for row in rows])


@router.get("/api/metrics/nearest/{symbol}")
def get_metrics_nearest(symbol: str, ts_ns: int) -> MetricHistoryItem | None:
    row = coin_detail.metrics_nearest(symbol, ts_ns, METRICS_DB_PATH)
    return MetricHistoryItem(**row) if row is not None else None
