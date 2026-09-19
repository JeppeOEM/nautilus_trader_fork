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
Read-only FastAPI service exposing catalog/metrics data over the network.

`dashboard.py` reads catalog/metrics data straight off local disk, which only
works when it runs on the same box as the collector's data. This service is a
thin network-reachable wrapper around the same existing functions
`dashboard.py` already calls -- no reimplemented query/aggregation logic here
(NAUT-02). Every route is a plain sync `def`; FastAPI runs sync handlers in
its own threadpool automatically, which offloads the blocking SQLite/Parquet
reads without hand-rolled `asyncio.to_thread`.

Bound to 127.0.0.1 only (SEC-01) -- see docker-compose.yml's `data_api`
service (`network_mode: host` + `uvicorn --host 127.0.0.1`), no `ports:` entry.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from data_api import alerts, live_candles, redis_bus
from data_api.routes import alerts as alerts_routes
from data_api.routes import candles as candles_routes
from data_api.routes import indicator_series as indicator_series_routes
from data_api.routes import indicators as indicators_routes
from data_api.routes import metrics as metrics_routes
from data_api.routes import rankings as rankings_routes
from data_api.routes import snapshots as snapshots_routes
from data_api.settings import CATALOG_PATH
from data_api.ws import live as live_ws
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals import catalog_stats as _catalog_stats
from ml_signals import chart_data as _chart_data
from ml_signals.candles import candle_dicts_for_window
from ml_signals.venue import MalformedInstrumentId
from ranking_engine import metrics_store


# Default mirrors dashboard.py:85-86 exactly.
METRICS_DB_PATH: str = os.environ.get(
    "METRICS_DB_PATH", str(Path(CATALOG_PATH).parent / "metrics" / "metrics.db"),
)

# Where troll/data_api.dockerfile's Node build stage COPYs troll/frontend/dist -- keep this
# default in sync with that Dockerfile's COPY destination (Story 15.1 AC #5).
FRONTEND_DIST_PATH: str = os.environ.get("FRONTEND_DIST_PATH", "frontend_dist")

# docs_url/redoc_url=None: FastAPI's own built-in Swagger UI defaults to serving at /docs,
# which would otherwise collide with this app's own /docs SPA route (Signal Atlas, FR45) --
# confirmed via a real TestClient request during Story 15.1 (not assumed), see that story's
# Dev Agent Record.


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Start the two shared Redis subscribers for the app's whole lifetime: `RankingsBus`
    (Story 15.2) and `LiveCandleBus` (Story 15.5). Every `GET /api/rankings` request and
    every `/ws/live` connection read/subscribe against these same `redis_bus.bus` /
    `live_candles.live_candle_bus` instances, never opening a per-request or
    per-websocket Redis connection of their own.
    """
    rankings_task = asyncio.create_task(redis_bus.bus.run(redis_bus.REDIS_URL))
    live_candles_task = asyncio.create_task(live_candles.live_candle_bus.run(redis_bus.REDIS_URL))
    try:
        yield
    finally:
        rankings_task.cancel()
        live_candles_task.cancel()
        for task in (rankings_task, live_candles_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)

live_candles.live_candle_bus.observers.append(alerts.engine.on_snapshot)


@app.get("/metrics/history/{symbol}")
def metrics_history(symbol: str, days: int = 31) -> list[dict]:
    return metrics_store.history(symbol, METRICS_DB_PATH, days)


@app.get("/metrics/nearest/{symbol}")
def metrics_nearest(symbol: str, ts_ns: int) -> dict | None:
    return metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)


@app.get("/catalog/chart-series/{symbol}")
def catalog_chart_series(symbol: str, start_ns: int, end_ns: int) -> dict[str, list[dict]]:
    return _chart_data.compute_chart_series(CATALOG_PATH, symbol, start_ns, end_ns)


def _snapshot_to_dict(snapshot: DydxSecondSnapshot) -> dict:
    return {
        "bid_prices": snapshot.bid_prices,
        "bid_sizes": snapshot.bid_sizes,
        "ask_prices": snapshot.ask_prices,
        "ask_sizes": snapshot.ask_sizes,
        "buy_volume": snapshot.buy_volume,
        "sell_volume": snapshot.sell_volume,
        "ts_event": snapshot.ts_event,
        "open_price": snapshot.open_price,
        "high_price": snapshot.high_price,
        "low_price": snapshot.low_price,
        "close_price": snapshot.close_price,
    }


@app.get("/catalog/snapshots/{iid}")
def catalog_snapshots(iid: str, start_ns: int, end_ns: int) -> list[dict]:
    snapshots = _catalog_stats.query_second_snapshots(CATALOG_PATH, iid, start_ns, end_ns)
    return [_snapshot_to_dict(s) for s in snapshots]


@app.get("/catalog/candles/{iid}")
def catalog_candles(
    iid: str, start_ns: int, end_ns: int, bar_seconds: int = 60,
) -> dict[str, list[dict]]:
    """
    Dedicated lean candles route -- not a client of /catalog/snapshots above.

    That route returns full order-book depth (bid/ask price+size arrays, up to 20
    levels each) needed for Lines mode; a candlestick pane only needs 4 scalar OHLC
    fields per second. For a 4-hour window /catalog/snapshots serializes to ~12MB,
    which took 30-60s over an SSH tunnel -- this route aggregates to candle bars here,
    on the box that holds the catalog, so only a few KB crosses the network.
    """
    candles = candle_dicts_for_window(
        iid,
        start_ns,
        end_ns,
        bar_seconds,
        snapshot_rows_fn=lambda i, a, b: _catalog_stats.query_second_snapshots(CATALOG_PATH, i, a, b),
        rollup_rows_fn=lambda i, a, b: _catalog_stats.query_minute_rollups(CATALOG_PATH, i, a, b),
    )
    return {"candles": candles}


class HealthResponse(BaseModel):
    status: str


@app.get("/api/health")
def health() -> HealthResponse:
    """Pilot route for the OpenAPI->TypeScript codegen pipeline (Story 15.1 AC #3) --
    also a real liveness check going forward, not throwaway scaffolding."""
    return HealthResponse(status="ok")


# Story 15.2: rankings REST + WS relay. Story 15.3: candles REST. Story 15.7: snapshots
# (Lines mode) REST. Story 17.2/15.8: metrics history/nearest REST. All must register
# above the /api/* catch-all below -- a route registered after it would silently 404
# (confirmed failure mode from Story 15.1's own SPA-fallback investigation; the same
# "declared routes win over the catch-all" rule applies here).


@app.exception_handler(MalformedInstrumentId)
async def _malformed_instrument_id(_: Request, exc: MalformedInstrumentId) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(alerts_routes.router)
app.include_router(rankings_routes.router)
app.include_router(candles_routes.router)
app.include_router(indicator_series_routes.router)
app.include_router(indicators_routes.router)
app.include_router(snapshots_routes.router)
app.include_router(metrics_routes.router)
app.include_router(live_ws.router)


@app.get("/api/{full_path:path}")
def api_not_found(full_path: str) -> None:
    """
    Catches every unmatched `/api/*` GET request and returns a JSON 404, unconditionally.

    Without this, `app.frontend()`'s `fallback="auto"` SPA-serving behavior silently
    returns the built `index.html` (200 text/html) for an unmatched `/api/*` path whenever
    the request carries an `Accept: text/html` header (a real browser's default for
    top-level navigation) -- confirmed via a real TestClient request this story, not
    assumed. This route is a normal FastAPI path operation, so it always takes priority
    over `app.frontend()`'s low-priority fallback routes regardless of Accept header
    (spine Consistency Conventions: "an unmatched /api/* path returns a JSON 404, never
    SPA HTML"). GET-only for now (data_api is a Read-Only Facade, AD-F1/AD-F2) -- if a
    future story adds a `PUT`/`POST` route under /api/*, it needs its own such catch-all
    per method, registered ABOVE this block (Starlette matches routes in registration
    order; a route registered after this catch-all for the SAME method would never be
    reached, since this wildcard would already have matched first).
    """
    raise HTTPException(status_code=404, detail="Not Found")


# Must stay the LAST route registered: every specific route above (existing 5 + /api/health,
# plus any /api/* route a future story adds) must be registered above this line, and
# app.frontend() itself must come after every normal path operation -- FastAPI only falls
# through to it when nothing above matched (AD-F1a).
app.frontend("/", directory=FRONTEND_DIST_PATH, check_dir=False)
