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

A thin network-reachable wrapper around the catalog/metrics reads -- no reimplemented
query/aggregation logic here (NAUT-02). The bare `/metrics/*` and `/catalog/*` routes were the
remote-mode targets of the old `dashboard.py` (retired by Story 15.10) and have no caller left
in this repo; they are kept as legacy read routes beside their `/api/*` successors (Story 33.4
deleted the legacy book-feature series route, which nothing drew any more). Their inputs
are bounded (a `/catalog` window is under `/api/snapshots`' row cap, rejected rather than
clamped; `days` is metrics.db's retention), and a failed catalog read is ledgered and answered
500 with its cause.
Every route is a plain sync `def`; FastAPI runs sync handlers in its own threadpool
automatically, which offloads the blocking SQLite/Parquet reads without hand-rolled
`asyncio.to_thread`.

Every value served is computed by the `views` context (Story 24.2): this app and its routes
format and transport only -- they pass their own env-derived paths in, build the pydantic
response models, and map views' exceptions to HTTP status codes. The two Redis buses are
constructed once in `data_api.buses`, the alerting instances once in `data_api.alert_wiring`.

Bound to 127.0.0.1 only (SEC-01) -- see docker-compose.yml's `data_api`
service (`network_mode: host` + `uvicorn --host 127.0.0.1`), no `ports:` entry.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.venues import MalformedInstrumentId
from kernel.venues import venue_of
from observability import error_ledger
from pydantic import BaseModel
from views import coin_detail

from data_api import alert_wiring
from data_api import buses
from data_api.routes import alerts as alerts_routes
from data_api.routes import archive as archive_routes
from data_api.routes import candles as candles_routes
from data_api.routes import derivatives as derivatives_routes
from data_api.routes import drawings as drawings_routes
from data_api.routes import footprint as footprint_routes
from data_api.routes import indicators as indicators_routes
from data_api.routes import layout as layout_routes
from data_api.routes import markets as markets_routes
from data_api.routes import metrics as metrics_routes
from data_api.routes import rankings as rankings_routes
from data_api.routes import snapshots as snapshots_routes
from data_api.routes.snapshots import MAX_SNAPSHOTS_LIMIT
from data_api.settings import CATALOG_PATH
from data_api.settings import ERROR_LEDGER_DIR
from data_api.settings import METRICS_DB_PATH
from data_api.settings import REDIS_URL
from data_api.ws import live as live_ws


# The legacy /catalog/snapshots route materialises one dict per archived second of the window, so
# it takes the same per-request bound as `/api/snapshots` (MEM-01): the closed window `[start_ns, end_ns]`
# must be shorter than the cap, so it holds at most `MAX_SNAPSHOTS_LIMIT` whole seconds. A wider
# window is rejected, never clamped: a truncated answer would be passed off as the complete window.
# Known limit: `views.catalog_reads.query_second_snapshots` widens the read's end by
# `READ_SPAN_MARGIN_NS` (60 s) before its exact ts_event filter, so one request transiently loads
# up to cap + 60 rows; upgrade path: a reader that filters on ts_event inside the catalog query.
_MAX_CATALOG_SPAN_NS = MAX_SNAPSHOTS_LIMIT * NS_PER_S
# The read adds `READ_SPAN_MARGIN_NS` to `end_ns` and the catalog filters in int64, so a larger
# bound would overflow inside the read and be misreported as a server fault, not a bad request.
_MAX_TS_NS = 2**63 - 1 - READ_SPAN_MARGIN_NS
_Timestamp = Annotated[int, Query(ge=0, le=_MAX_TS_NS)]

# Where platform/data_api.dockerfile's Node build stage COPYs platform/frontend/dist -- keep this
# default in sync with that Dockerfile's COPY destination (Story 15.1 AC #5).
FRONTEND_DIST_PATH: str = os.environ.get("FRONTEND_DIST_PATH", "frontend_dist")

# docs_url/redoc_url=None: FastAPI's own built-in Swagger UI defaults to serving at /docs,
# which would otherwise collide with this app's own /docs SPA route (Signal Atlas, FR45) --
# confirmed via a real TestClient request during Story 15.1 (not assumed), see that story's
# Dev Agent Record.


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Start the five shared Redis subscribers for the app's whole lifetime: `RankingsBus`
    (Story 15.2), `LiveCandleBus` (Story 15.5), `ArchiveStatusBus` (Story 25.1b),
    `LiveDerivsBus` (Story 33.4, `derivs:raw`) and `MarketsBus` (Story 33.9, `markets:live`). Every
    `GET /api/rankings`/`GET /api/archive/status` request and every `/ws/live` connection
    read/subscribe against these same `buses.bus` / `buses.live_candle_bus` /
    `buses.archive_bus` instances, never opening a per-request or per-websocket Redis
    subscription of their own.

    It is also the one place the alert engine is wired to the candle bus (Story 24.3): attached as
    a `BarObserver` before the bus task starts, so no batch is folded without it, and detached on
    shutdown. The derivs bus is attached the same way as the candle bus's liquidation listener
    (Story 33.4): the candle bus stays the one `liquidations:raw` subscriber. The engine is also
    the derivs bus's `DerivsObserver` (Story 33.8), attached and detached with the rest, so a
    funding, open-interest or liquidation alert fires with no `/ws/live` listener open.
    """
    error_ledger.start()
    # Bound once so shutdown detaches exactly what startup attached, even if a test swaps them.
    live_candle_bus, alert_engine = buses.live_candle_bus, alert_wiring.engine
    live_derivs_bus = buses.live_derivs_bus
    live_candle_bus.attach(alert_engine)
    live_candle_bus.attach_liquidations(live_derivs_bus)
    live_derivs_bus.attach(alert_engine)
    alert_engine.bind_loop()  # a `forget` before the first batch runs on this loop too
    rankings_task = asyncio.create_task(buses.bus.run(REDIS_URL))
    live_candles_task = asyncio.create_task(live_candle_bus.run(REDIS_URL))
    live_derivs_task = asyncio.create_task(live_derivs_bus.run(REDIS_URL))
    archive_status_task = asyncio.create_task(buses.archive_bus.run(REDIS_URL))
    markets_task = asyncio.create_task(buses.markets_bus.run(REDIS_URL))
    tasks = (rankings_task, live_candles_task, live_derivs_task, archive_status_task, markets_task)
    try:
        yield
    finally:
        live_candle_bus.detach(alert_engine)
        live_candle_bus.detach_liquidations(live_derivs_bus)
        live_derivs_bus.detach(alert_engine)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/metrics/history/{symbol}")
def metrics_history(
    symbol: str,
    # metrics.db's own retention (see routes/metrics.py's twin): wider would silently shorten.
    days: Annotated[int, Query(ge=1, le=coin_detail.METRICS_HISTORY_MAX_DAYS)] = (
        coin_detail.METRICS_HISTORY_MAX_DAYS
    ),
) -> list[dict]:
    return coin_detail.metrics_history(symbol, METRICS_DB_PATH, days)


@app.get("/metrics/nearest/{symbol}")
def metrics_nearest(symbol: str, ts_ns: int) -> dict | None:
    return coin_detail.metrics_nearest(symbol, ts_ns, METRICS_DB_PATH)


def _check_catalog_window(instrument_id: str, start_ns: int, end_ns: int) -> None:
    """Refuse a request before any catalog read: no venue (400), reversed or over-wide (422)."""
    venue_of(instrument_id)
    if start_ns > end_ns:
        raise HTTPException(
            status_code=422, detail=f"start_ns ({start_ns}) is after end_ns ({end_ns})"
        )
    if end_ns - start_ns >= _MAX_CATALOG_SPAN_NS:
        raise HTTPException(
            status_code=422,
            detail=f"end_ns - start_ns must be under the {MAX_SNAPSHOTS_LIMIT} s window cap",
        )


def _read_catalog[T](route: str, instrument_id: str, read: Callable[[], T]) -> T:
    """
    Run one views catalog read, mapping its failures to HTTP: anything unexpected -- an Arrow
    schema/precision conflict, an I/O error -- is ledgered here (DATA-07) and answered 500 with its
    cause, never a detail-less 500.
    """
    try:
        return read()
    except Exception as exc:
        error_ledger.record(
            "data_api.catalog_read", f"{route} read failed for {instrument_id}", exc
        )
        raise HTTPException(
            status_code=500, detail=f"failed to read catalog: {type(exc).__name__}: {exc}"
        ) from exc


@app.get("/catalog/snapshots/{iid}")
def catalog_snapshots(iid: str, start_ns: _Timestamp, end_ns: _Timestamp) -> list[dict]:
    _check_catalog_window(iid, start_ns, end_ns)
    return _read_catalog(
        "/catalog/snapshots",
        iid,
        lambda: coin_detail.catalog_snapshot_rows(CATALOG_PATH, iid, start_ns, end_ns),
    )


class HealthResponse(BaseModel):
    status: str


@app.get("/api/health")
def health() -> HealthResponse:
    """
    Pilot route for the OpenAPI->TypeScript codegen pipeline (Story 15.1 AC #3) --
    also a real liveness check going forward, not throwaway scaffolding.
    """
    return HealthResponse(status="ok")


class ServiceErrorSummary(BaseModel):
    last_start_ns: int | None
    since_start: dict[str, int]
    since: dict[str, int] | None


class ErrorsResponse(BaseModel):
    # site -> how many times this process recorded a failure there since it started (DATA-07).
    counts: dict[str, int]
    last: dict[str, str]
    # service name -> its durable ledger's summary (story 23.3); empty when ERROR_LEDGER_DIR
    # has no files yet (unset, or nothing has called observability.error_ledger.start()).
    services: dict[str, ServiceErrorSummary]


@app.get("/api/errors")
def errors(since_ns: int | None = None) -> ErrorsResponse:
    """
    Every failure this process carried on past (`observability.error_ledger`). `counts`/`last`
    are this process's own in-memory tallies, unchanged since Story 23.1; `services` is every
    other process's durable ledger (story 23.3), read back from `ERROR_LEDGER_DIR` -- so a
    collector's failure is visible here too, not only in its own container's log. Empty means
    none since start; the frontend's error bar polls this so a malfunction cannot go unseen.
    For a one-shot job's ledger (`error_ledger.is_job_service`, e.g. the nightly's
    `archive.<step>_<venue>`), every run writes a `process_start`, so its `since_start` is the
    latest run's errors only (one file per venue, so one venue's run never hides another's); the
    full history stays in its file (`?since_ns=` reads further back).
    """
    services = {
        name: ServiceErrorSummary(
            **error_ledger.service_summary(ERROR_LEDGER_DIR, name, since_ns=since_ns)
        )
        for name in error_ledger.services(ERROR_LEDGER_DIR)
    }
    return ErrorsResponse(
        counts=error_ledger.counts(), last=error_ledger.last_details(), services=services
    )


# Story 15.2: rankings REST + WS relay. Story 15.3: candles REST. Story 33.4: the derivatives
# and liquidations read models (`routes/derivatives.py`). Story 15.7: snapshots
# (Lines mode) REST. Story 17.2/15.8: metrics history/nearest REST. Story 25.1b: archive
# maintenance status + run-now. Story 33.9: the markets list (Compare picker). All must register
# above the /api/* catch-all below -- a route registered after it would silently 404
# (confirmed failure mode from Story 15.1's own SPA-fallback investigation; the same
# "declared routes win over the catch-all" rule applies here).


@app.exception_handler(MalformedInstrumentId)
async def _malformed_instrument_id(_: Request, exc: MalformedInstrumentId) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(alerts_routes.router)
app.include_router(archive_routes.router)
app.include_router(rankings_routes.router)
app.include_router(candles_routes.router)
app.include_router(derivatives_routes.router)
app.include_router(footprint_routes.router)
app.include_router(drawings_routes.router)
app.include_router(indicators_routes.router)
app.include_router(layout_routes.router)
app.include_router(markets_routes.router)
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
