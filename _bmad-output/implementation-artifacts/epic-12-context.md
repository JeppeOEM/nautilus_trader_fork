# Epic 12 Context: Local Dashboard + bot_tui, VPS as Data API

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Let the builder run `dashboard.py` and `bot_tui` on their own machine instead of on the (oversubscribed) nifelheim VPS, reaching nifelheim's live data over one SSH tunnel: Redis (unchanged wire protocol) plus a new small read-only HTTP API for the handful of local-disk reads `dashboard.py` currently does directly. This is a new opt-in path — the existing VPS-hosted `collector`/`ranking_engine`/`dashboard`/`bot_tui` services must keep working exactly as today, with zero behavior change when the new env var is unset.

## Stories

- Story 12.1: Read-only `data_api` FastAPI service on the VPS
- Story 12.2: `dashboard.py` remote-data mode + local-machine run docs

## Requirements & Constraints

- `bot_tui` touches nothing but Redis pub/sub and plain-key polling — no direct file access anywhere, so it moves to a local machine with zero code changes once Redis is tunneled.
- `dashboard.py` does exactly 4 direct local-disk reads that need a network equivalent: `ranking_engine/metrics_store.py`'s `history()` and `nearest()` (SQLite), `ml_signals/catalog_stats.py::query_second_snapshots()` and `ml_signals/chart_data.py::compute_chart_series()` (Parquet catalog).
- The new `data_api` service must be a thin wrapper only — it reuses `metrics_store`, `catalog_stats`, and `chart_data` verbatim, never reimplementing their logic (this is required, not just preferred, since those functions are the existing single source of truth for this data).
- New service must expose exactly 4 routes: metrics history, metrics nearest, catalog chart-series, catalog snapshots (the last one replaces both of today's separate catalog reads since they query the same underlying `DydxSecondSnapshot` rows).
- Bound to `127.0.0.1` only via `network_mode: host` — no `ports:` entry, no public port, ever (SEC-01). Read-only volume mounts for catalog/metrics, matching the read-only discipline already used for the `dashboard` service.
- `DATA_API_URL` unset must be byte-for-byte identical to current behavior for the VPS-hosted `dashboard` service — this is the default and gets no new env var.
- When `DATA_API_URL` is set, all 5 affected `dashboard.py` call sites fetch via `aiohttp.ClientSession` (already a dependency) through one shared `_fetch_json(session, url)` helper, not 5 separate HTTP-call implementations.
- `bot_tui` needs no code change for this epic at all — including its existing reverse-tunnel URL hand-off, which already no-ops correctly once both dashboard and bot_tui run locally.
- Tests are required (TEST-01): this is new branching logic and a new catalog-touching integration path, not trivial glue. Never mock Nautilus internals (TEST-03) — seed a real temp `ParquetDataCatalog` with real `DydxSecondSnapshot` rows and a real temp SQLite file via `metrics_store.write()`.

## Technical Decisions

- Service shape was already decided with the user and should not be revisited: one new service, Python, FastAPI, layered onto the existing `troll/collector.dockerfile` shared image (not a Go service, not a Redis→WebSocket bridge) — Redis is already a network service, and the catalog reads must stay Python regardless since `DydxSecondSnapshot`'s Arrow schema is only registered via `nautilus_trader.serialization.arrow` in Python (AD-6/NAUT-02: all catalog access goes through the official `ParquetDataCatalog` API).
- New service name/module: `troll/data_api/app.py`, config via `CATALOG_PATH`/`METRICS_DB_PATH` env vars with the same defaults `dashboard.py` already uses.
- Deployment: add to `troll/docker-compose.yml` as a new `data_api` service built from the existing `collector.dockerfile`, run via `uvicorn data_api.app:app --host 127.0.0.1 --port 9100`, `network_mode: host` (matching every other service).
- Dependencies to add to `troll/troll-requirements.txt`: `fastapi`, `uvicorn[standard]`, `httpx` (needed by FastAPI's `TestClient`); `aiohttp` is already present and covers the client side.
- `troll/Makefile`'s `test:` target gains `data_api/tests` in its pytest module list.
- Architectural context (AD-3, Gatekeeper paradigm): `dashboard`/`bot_tui`/`data_api` are all trusting readers of data the collector's gate already validated — `data_api` performs no re-validation, it's a pure pass-through.
- Local-machine run docs go in `troll/ARCHITECTURE.md`'s deployment-topology section: one SSH tunnel command covering both ports (`ssh -fN -L 6379:localhost:6379 -L 9100:localhost:9100 nifelheim`), plus the two local run commands for `dashboard` and `bot_tui`.
- `troll/.env-example` documents `DATA_API_URL` (default empty = unchanged behavior), following the existing `WEB_PORT` doc-comment style.

## Cross-Story Dependencies

Story 12.2 depends on Story 12.1 — it needs `data_api`'s 4 routes to exist and be stable before wiring `dashboard.py`'s remote-mode branches against them.
