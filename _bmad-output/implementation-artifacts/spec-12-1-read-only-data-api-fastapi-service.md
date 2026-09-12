---
title: 'Story 12.1: Read-only data_api FastAPI service on the VPS'
type: 'feature'
created: '2026-09-12'
status: 'done'
baseline_revision: '7f1496be31b163e9a47f8a570760ab544b56b38e'
final_revision: '01aa846ddc'
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/troll/CLAUDE.md', '{project-root}/CLAUDE.md']
warnings: []
---

<intent-contract>

## Intent

**Problem:** `dashboard.py` currently reads catalog/metrics data straight off local disk (`ranking_engine/metrics_store.py`'s `history()`/`nearest()`, `ml_signals/catalog_stats.py::query_second_snapshots()`, `ml_signals/chart_data.py::compute_chart_series()`), which only works when `dashboard.py` runs on the same box as the collector's data. There is no network-reachable equivalent yet.

**Approach:** Add a new, standalone read-only FastAPI service (`troll/data_api/app.py`) that exposes exactly 4 thin-wrapper routes around those existing functions, verbatim — no reimplemented logic. Layer it onto the existing shared `collector.dockerfile` image and add a new `data_api` compose service bound to `127.0.0.1:9100` only. No existing service's behavior changes.

## Boundaries & Constraints

**Always:** Every route is a thin wrapper calling an existing function with no reimplemented query/aggregation logic (NAUT-02). `CATALOG_PATH`/`METRICS_DB_PATH` env vars default to exactly what `dashboard.py:69,85-86` uses. New Docker port is `127.0.0.1`-only via `network_mode: host` + uvicorn's own `--host 127.0.0.1` bind — never a bare `ports:` mapping (SEC-01). Tests use real `ParquetDataCatalog`/`DydxSecondSnapshot`/SQLite objects, never mocks (TEST-03). `troll/Makefile`'s `test:` target must cover the new tests.

**Block If:** N/A — no undecided architectural choices remain; service shape (Python/FastAPI, one new service) was already confirmed with the user per epics.md and must not be revisited.

**Never:** Do not modify `dashboard.py`, `bot_tui`, `ranking_engine/engine.py`, or any existing service's compose block (that's Story 12.2 and out of scope here). Do not add a `ports:` entry. Do not touch `nautilus_trader/` or `crates/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| History happy path | Seeded metrics DB row for symbol | `GET /metrics/history/{symbol}?days=31` returns JSON list matching `metrics_store.history()` output | No error expected |
| Nearest, no data | Symbol with no rows in metrics DB | `GET /metrics/nearest/{symbol}?ts_ns=<int>` returns JSON `null` | No error expected (real branch: `metrics_store.nearest` returns `None`) |
| Chart series happy path | Seeded catalog snapshots in window | `GET /catalog/chart-series/{symbol}?start_ns=&end_ns=` returns `compute_chart_series()`'s dict verbatim | No error expected |
| Snapshots happy path | Seeded catalog snapshots in window | `GET /catalog/snapshots/{iid}?start_ns=&end_ns=` returns list of dicts with `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes`, `buy_volume`, `sell_volume`, `ts_event`, `open_price`, `high_price`, `low_price`, `close_price` | No error expected |

</intent-contract>

## Code Map

- `troll/data_api/app.py` -- NEW: FastAPI app with the 4 routes.
- `troll/data_api/__init__.py` -- NEW: empty package marker.
- `troll/data_api/tests/test_data_api.py` -- NEW: TestClient integration tests.
- `troll/data_api/tests/__init__.py` -- NEW: empty package marker.
- `troll/troll-requirements.txt` -- add `fastapi`, `uvicorn[standard]`, `httpx`.
- `troll/collector.dockerfile` -- add `COPY troll/data_api ./data_api`.
- `troll/docker-compose.yml` -- add `data_api` service (model on `ranking_engine`'s block).
- `troll/Makefile` -- append `data_api/tests` to the `test:` target's pytest module list.
- `ranking_engine/metrics_store.py:102,114` -- `history()`/`nearest()`, called verbatim.
- `ml_signals/chart_data.py:40` -- `compute_chart_series()`, called verbatim.
- `ml_signals/catalog_stats.py:51` -- `query_second_snapshots()`, called verbatim, then projected to dicts.
- `ml_signals/tests/test_catalog_stats.py:138` -- `_write_snapshot()` pattern to replicate in the new test file.

## Tasks & Acceptance

**Execution:**
- [x] `troll/data_api/app.py` -- create FastAPI app with `CATALOG_PATH`/`METRICS_DB_PATH` module-level env vars (defaults copied from `dashboard.py:69,85-86`) and 4 sync `def` route handlers (FastAPI threadpools sync handlers automatically, avoiding hand-rolled `asyncio.to_thread`) -- delivers the 4 required endpoints
- [x] `troll/data_api/__init__.py`, `troll/data_api/tests/__init__.py` -- empty package markers -- matches sibling module convention
- [x] `troll/data_api/tests/test_data_api.py` -- FastAPI `TestClient` tests seeding a temp `ParquetDataCatalog` (via a `_write_snapshot`-style helper) and a temp SQLite metrics DB (via `metrics_store.write()`), one test per route plus the `nearest`-returns-null case -- proves the wrappers are correct without mocking Nautilus internals
- [x] `troll/troll-requirements.txt` -- append `fastapi`, `uvicorn[standard]`, `httpx` -- new service's runtime + test dependencies
- [x] `troll/collector.dockerfile` -- add `COPY troll/data_api ./data_api` -- bakes the new module into the shared image
- [x] `troll/docker-compose.yml` -- add `data_api` service: `command: uvicorn data_api.app:app --host 127.0.0.1 --port 9100`, `network_mode: host`, env `CATALOG_PATH=/app/catalog`, `METRICS_DB_PATH=/app/metrics_dir/metrics.db`, `user: "1000:1000"`, volumes `./dydx_collector/catalog:/app/catalog:ro` + `./dydx_collector/metrics:/app/metrics_dir:ro`, no `ports:` entry -- exposes the new service per SEC-01
- [x] `troll/Makefile` -- append `data_api/tests` to the `test:` target's module list -- keeps `make test` comprehensive

**Acceptance Criteria:**
- Given the service running with a seeded metrics DB, when `GET /metrics/history/{symbol}?days=31` is called, then it returns the same JSON list `ranking_engine.metrics_store.history()` would return for identical args
- Given a symbol with no stored metrics, when `GET /metrics/nearest/{symbol}?ts_ns=<int>` is called, then it returns JSON `null`
- Given a seeded catalog, when `GET /catalog/chart-series/{symbol}?start_ns=&end_ns=` is called, then it returns `ml_signals.chart_data.compute_chart_series()`'s dict verbatim
- Given a seeded catalog, when `GET /catalog/snapshots/{iid}?start_ns=&end_ns=` is called, then it returns a list of dicts covering `bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes`/`buy_volume`/`sell_volume`/`ts_event`/`open_price`/`high_price`/`low_price`/`close_price`
- Given `docker compose config` after the compose edit, when parsed, then it is valid YAML with no `ports:` key under `data_api`
- Given `cd troll && make test` (inside Docker), when run after this story, then all existing module tests plus `data_api/tests` pass

## Verification

**Commands:**
- `cd troll && docker compose build data_api` -- expected: image builds cleanly with the new `COPY` line and requirements
- `cd troll && docker compose config` -- expected: valid YAML, `data_api` service has no `ports:` key
- `cd troll && make test` -- expected: all pytest modules pass including new `data_api/tests`, run inside the Docker container (local `.venv` lacks `nautilus_pyo3`/`fastapi`)

## Review Triage Log

### 2026-09-12 — Review pass

Note: this pass's Blind Hunter + Edge Case Hunter review, deduplication, and classification were completed by a prior session (evidence: 6 fully-written, well-evidenced entries already present in `deferred-work.md`, each citing the reviewing subagent(s) by name, including one with direct Docker `:ro`-mount reproduction against the real built image). That session was killed by an infrastructure rate-limit error before it could append this log entry to the spec file. This entry reconstructs the triage summary from the already-completed classification; no findings were re-derived or re-reviewed.

- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 7: (high 1, medium 2, low 4)
- reject: 0
- addressed_findings:
  - none

One additional `defer` finding (low severity) was surfaced during this pass's own Verification (not by Blind Hunter/Edge Case Hunter, who review diffs rather than run tests): `make test` emits a new `StarletteDeprecationWarning` from `fastapi.testclient`/`httpx`, recorded in `deferred-work.md` per troll/CLAUDE.md TEST-04 rather than suppressed or silently accepted.

## Auto Run Result

**Summary:** Added `troll/data_api/app.py`, a new standalone read-only FastAPI service exposing 4 thin-wrapper routes (`/metrics/history/{symbol}`, `/metrics/nearest/{symbol}`, `/catalog/chart-series/{symbol}`, `/catalog/snapshots/{iid}`) around `dashboard.py`'s existing catalog/metrics query functions, verbatim, with zero reimplemented query/aggregation logic (NAUT-02). Layered onto the shared `collector.dockerfile` image; new `data_api` compose service bound to `127.0.0.1:9100` only (SEC-01). This run resumed a prior `bmad-dev-auto` session that had reached the review step (Blind Hunter + Edge Case Hunter already run, findings already written to `deferred-work.md`) but was killed by a session rate-limit error before it could record the triage summary in this spec file.

**Files changed:**
- `troll/data_api/app.py` -- new FastAPI app, `CATALOG_PATH`/`METRICS_DB_PATH` env vars, 4 sync route handlers
- `troll/data_api/__init__.py`, `troll/data_api/tests/__init__.py` -- empty package markers
- `troll/data_api/tests/test_data_api.py` -- `TestClient` integration tests, one per route plus the `nearest`-returns-null case, seeding real `ParquetDataCatalog`/SQLite objects (no mocks, TEST-03)
- `troll/troll-requirements.txt` -- added `fastapi==0.141.1`, `uvicorn[standard]==0.52.4`, `httpx==0.28.1`
- `troll/collector.dockerfile` -- added `COPY troll/data_api ./data_api`
- `troll/docker-compose.yml` -- added `data_api` service (`network_mode: host`, uvicorn `--host 127.0.0.1 --port 9100`, `:ro` catalog/metrics mounts, no `ports:` key)
- `troll/Makefile` -- appended `data_api/tests` to the `test:` target's pytest module list
- `_bmad-output/implementation-artifacts/deferred-work.md` -- 7 defer entries logged (6 from the original review pass, 1 found during this session's own test-run verification)
- `_bmad-output/implementation-artifacts/epic-12-context.md` -- compiled epic context (reused from the prior session; still valid)
- This spec file -- reconstructed the missing `## Review Triage Log` entry and this `Auto Run Result` section

**Review findings breakdown:** 0 patches applied, 7 deferred (1 high, 2 medium, 4 low), 0 rejected (none identified with confidence), 0 intent gaps, 0 bad-spec loopbacks. Highest-severity deferred item: `ranking_engine/metrics_store.py`'s `_conn()` requires write access even in WAL mode, which will make `/metrics/*` routes (and, pre-existingly, `dashboard.py`'s history/nearest calls) fail against the `:ro`-mounted metrics directory both this story's and the existing `dashboard` compose service use -- reproduced directly against the real built image; root cause lives outside this story's changed files and is tracked as a separate DATA-02 investigation, not folded into this story or Story 12.2.

**Follow-up review recommendation:** `false` -- no patches or bad-spec changes were made in this pass; all findings were classified `defer` (pre-existing, out of this story's file scope) and logged for separate attention, so there is no new review-driven code change in this pass to independently re-review.

**Verification performed:**
- `docker compose build collector` -- image rebuilt cleanly with the new `COPY troll/data_api` layer (requirements layer was already cached from the prior session, confirming `fastapi`/`uvicorn`/`httpx` were already validated as installable)
- `cd troll && make test` (inside Docker, via `docker compose run --rm --no-deps collector`) -- 619 passed, 5 skipped, 0 failures, including all of `data_api/tests`
- `docker compose config` -- rendered `data_api` service block inspected directly: `network_mode: host`, uvicorn bound to `127.0.0.1:9100` explicitly, no `ports:` key -- SEC-01 confirmed
- Manually reproduced the `httpx2` deprecation warning's exact origin (`starlette.testclient` module-level warning, starlette 1.6.0) and confirmed `httpx2` exists on PyPI (v2.12.0) before logging it as a defer item rather than dismissing it (TEST-04)
- `git diff`/`git status` inspected directly before staging to confirm only this story's own files were committed, excluding a concurrent agent's simultaneous Epic 13 Story 13.2 changes (`ranking_engine/engine.py`, `ranking_engine/price_series.py`, `ranking_engine/tests/test_engine.py`, `ml_signals/catalog_stats.py`, `spec-13-2-*.md`)

**Residual risks:** The `:ro`-mount/WAL SQLite issue above means `/metrics/history` and `/metrics/nearest` will likely fail with `sqlite3.OperationalError` when this service is actually deployed against the production compose mounts, despite passing all local tests (which use a writable `tmp_path` DB, not a `:ro` mount) -- this is a real, not merely theoretical, deployment-time gap that should be prioritized before relying on those two routes in production. The two `/catalog/*` routes accept unbounded `start_ns`/`end_ns` ranges (MEM-01 risk, inherited from the wrapped functions). No route has error handling for malformed/unknown inputs beyond FastAPI's own type coercion.
