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
(AD-F2), not reimplemented: both call straight into `ranking_engine.metrics_store.history()`/
`nearest()` unchanged. Story 17.2 (originally 15.8)'s 31-day metrics-history page fetches
its trailing-31-day window through the first of these; the second exists for any
nearest-value lookup that page (or a future one) needs.

Deliberately NOT cursor-paginated -- a documented exception to AD-F3's "every history
endpoint... without exception" language, not an oversight. `metrics_store.history()` is
already a small, fixed-size 31-day window (one row per instrument per polling tick, not
per-second order-book depth), fetched once per page load, with no scroll-back concept.

These are NEW routes under `/api/metrics/...`, coexisting with the existing bare
`/metrics/history/{symbol}`/`/metrics/nearest/{symbol}` routes in `app.py`
(`dashboard.py`'s remote-mode call targets, untouched) -- same "old vs. new namespace
coexist" pattern Story 15.3 established for `/catalog/candles` vs. `/api/candles`.

Own module-level `METRICS_DB_PATH` constant, same pattern as `routes/candles.py`'s own
`CATALOG_PATH` -- `routes/metrics.py` cannot `from data_api.app import METRICS_DB_PATH`
without a circular import, since `app.py` imports this module.
"""

import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from data_api.settings import CATALOG_PATH
from ranking_engine import metrics_store


# Default mirrors data_api/app.py's own METRICS_DB_PATH default exactly (dashboard.py:85-86).
METRICS_DB_PATH: str = os.environ.get(
    "METRICS_DB_PATH",
    str(Path(CATALOG_PATH).parent / "metrics" / "metrics.db"),
)

router = APIRouter()


class MetricHistoryItem(BaseModel):
    """
    One row per `ranking_engine.metrics_store.COLS` (plus its own `ts`) -- a `None` field
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
def get_metrics_history(symbol: str, days: int = 31) -> MetricsHistoryResponse:
    rows = metrics_store.history(symbol, METRICS_DB_PATH, days)
    return MetricsHistoryResponse(items=[MetricHistoryItem(**row) for row in rows])


@router.get("/api/metrics/nearest/{symbol}")
def get_metrics_nearest(symbol: str, ts_ns: int) -> MetricHistoryItem | None:
    row = metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)
    return MetricHistoryItem(**row) if row is not None else None
