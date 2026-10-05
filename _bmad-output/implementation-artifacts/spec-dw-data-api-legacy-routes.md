---
title: 'Harden data_api legacy /catalog and /metrics routes (DW-99, DW-100, DW-101, DW-102, DW-130)'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
baseline_revision: '3a4913ea081af7db74c76440eefcee955d96957f'
final_revision: '80681b7315'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** The legacy routes in `platform/data_api/app.py` (`/catalog/chart-series/{symbol}`, `/catalog/snapshots/{iid}`, `/metrics/history/{symbol}`, `/metrics/nearest/{symbol}`) pass caller input straight to `views`: an unbounded `start_ns..end_ns` window loads any number of archived seconds into memory (MEM-01), `start_ns > end_ns` and `days <= 0` are silently accepted, a huge `days` overflows SQLite's int64 into an opaque 500, any catalog read failure (Arrow schema/precision conflict, I/O) surfaces as a detail-less 500 that is never ledgered, and `METRICS_DB_PATH` is computed twice (app.py and routes/metrics.py) behind a comment citing `dashboard.py:85-86` — a file retired by Story 15.10.

**Approach:** Validate inputs at the route (422 for a reversed or over-wide window and for `days` outside `1..metrics.db retention`, 400 for an id with no venue via the existing `MalformedInstrumentId` handler), translate unexpected catalog read failures into a ledgered 500 with a meaningful detail, and define `METRICS_DB_PATH` once in `data_api/settings.py` with a test pinning it to the ranking writer's default.

## Boundaries & Constraints

**Always:** Routes format/transport only; views keep computing values. Every rejected request is a 4xx with a `detail` naming the bad parameter. A catalog read failure is `error_ledger.record`ed (DATA-07) and answered 500 with `failed to read catalog: <exc>`; `EmptyTopOfBook` keeps its existing 500 (views already ledgers it). Over-wide windows are rejected, never silently clamped (a truncated answer would be fabricated completeness). The `days` bound comes from the one retention constant ranking writes with, not a new literal in data_api.

**Block If:** Enforcing the bound requires changing a `views`/`ranking` function's computed output, or a frontend/research caller already sends `days` outside `1..31` or a window wider than the cap.

**Never:** Do not delete the legacy routes; do not edit the deferred-work ledger; do not touch `nautilus_trader/` or `crates/`; no blanket `except` that swallows (every caught failure is re-raised as an HTTPException and ledgered).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy window | `start_ns=0&end_ns=3e9`, data present | 200, body equals the wrapped views call (unchanged) | none |
| Reversed window | `start_ns > end_ns` on either /catalog route | 422, detail names `start_ns`/`end_ns` | no catalog read |
| Over-wide window | `end_ns - start_ns > MAX_SNAPSHOTS_LIMIT s` | 422, detail states the cap in seconds | no catalog read |
| Max-wide window | span exactly the cap | 200 | none |
| Unknown id | `ZZZ-USD-PERP.DYDX`, never captured | 200 with empty result (`[]` / all-empty series) | none |
| Id without venue | `garbage` | 400 via `MalformedInstrumentId` handler | no catalog read |
| Catalog read fails | views raises e.g. `OSError`/`ArrowInvalid` | 500, `detail` starts `failed to read catalog:` | `error_ledger.record("data_api.catalog_read", ...)` |
| Empty top of book | views raises `EmptyTopOfBook` | 500 with its message (unchanged) | views ledgers it |
| days out of range | `days=0`, `-1`, `32`, `10**12` on `/metrics/history` and `/api/metrics/history` | 422 (FastAPI validation) | none |
| days in range | `days=1`, `31` | 200 | none |

</intent-contract>

## Code Map

- `platform/data_api/app.py` -- legacy routes, stale `dashboard.py` docstring/comment, own `METRICS_DB_PATH`
- `platform/data_api/routes/metrics.py` -- `/api/metrics/*`, duplicate `METRICS_DB_PATH` + stale citation in comment and docstring
- `platform/data_api/routes/snapshots.py` -- `_MAX_SNAPSHOTS_LIMIT` (5000 one-second rows), the per-request raw-second ceiling to reuse
- `platform/data_api/settings.py` -- leaf settings module; `CATALOG_PATH` lives here
- `platform/views/coin_detail.py` -- `metrics_history`/`catalog_snapshot_rows`; views' only path to ranking's query service
- `platform/ranking/application/queries.py` + `platform/ranking/infrastructure/metrics_store.py` -- `write(retain_days=31)`, the retention literal
- `platform/ranking/__main__.py:66` -- `settings_from_env()`, the writer's `METRICS_DB_PATH` default
- `platform/tests/test_boundaries.py:1440` -- `VIEWS_QUERY_SERVICES` allow-list (views → ranking names)
- `platform/data_api/tests/test_data_api.py`, `test_metrics.py`, `test_snapshots.py:234` -- route tests
- `platform/frontend/openapi.json` + `src/api/schema.ts` -- committed schema, checked by `test_app_frontend.py:103`

## Tasks & Acceptance

**Execution:**
- [x] `platform/ranking/infrastructure/metrics_store.py` -- add `RETAIN_DAYS = 31` (comment: metrics.db keeps this many days) and use it as `write`'s `retain_days` default; `platform/ranking/application/queries.py` re-exports it as `HISTORY_MAX_DAYS` -- one retention source for readers
- [x] `platform/views/coin_detail.py` -- expose `METRICS_HISTORY_MAX_DAYS` from `ranking.application.queries.HISTORY_MAX_DAYS`; add the name to `tests/test_boundaries.py`'s `VIEWS_QUERY_SERVICES["ranking.application.queries"]`
- [x] `platform/data_api/settings.py` -- `default_metrics_db_path(catalog_path)` + `METRICS_DB_PATH` (env override) defined once, comment citing `ranking/__main__.py`'s `settings_from_env` as the writer it must match
- [x] `platform/data_api/routes/snapshots.py` -- rename `_MAX_SNAPSHOTS_LIMIT` to public `MAX_SNAPSHOTS_LIMIT` (update `data_api/tests/test_snapshots.py`)
- [x] `platform/data_api/routes/metrics.py` -- import `METRICS_DB_PATH` from settings (drop own expression, fix docstring's dashboard/circular-import text); `days: Annotated[int, Query(ge=1, le=METRICS_HISTORY_MAX_DAYS)] = 31`
- [x] `platform/data_api/app.py` -- import `METRICS_DB_PATH` from settings; same `days` constraint; `_MAX_CATALOG_SPAN_NS = MAX_SNAPSHOTS_LIMIT * 1e9` (int); a window check (422 reversed / over-wide) and `venue_of(iid)` before each catalog read; one helper that maps `EmptyTopOfBook`→500 (as today), lets `MalformedInstrumentId`/`HTTPException` propagate, and ledgers + 500s anything else; rewrite the stale `dashboard.py` docstring/comment (dashboard retired, Story 15.10) -- keep each function under ~30 lines
- [x] `platform/data_api/tests/test_data_api.py` -- tests for every I/O matrix row (catalog-read failure via monkeypatched views function raising `OSError`, asserting 500 detail and a ledger count); metrics_db default parity test vs `ranking.__main__.settings_from_env()` with `CATALOG_PATH` set and `METRICS_DB_PATH` unset
- [x] `platform/data_api/tests/test_metrics.py` -- `days` out-of-range 422 on `/api/metrics/history`
- [x] `platform/frontend/openapi.json`, `platform/frontend/src/api/schema.ts` -- regenerate (`export_openapi`, `npm run codegen`)

**Acceptance Criteria:**
- Given the patched tree, when `grep -rn "dashboard.py:85\|METRICS_DB_PATH: str = os.environ" platform/data_api` runs, then it finds only `settings.py`'s single definition and no `dashboard.py:85-86` citation.
- Given a catalog read raising inside views, when the legacy route is called, then the response is 500 with a non-empty `failed to read catalog:` detail and `error_ledger.counts()["data_api.catalog_read"] == 1`.
- Given the full platform suite, when it runs, then it passes with no new warnings, and the committed `openapi.json` matches the live schema.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6 (high 0, medium 2, low 4)
- defer: 0
- reject: 15 (high 0, medium 0, low 15)
- addressed_findings:
  - `[medium]` `[patch]` `start_ns`/`end_ns` outside int64 passed the window check and would overflow inside the catalog read, which then ledgered the client's mistake as a server malfunction (500). Both params are now `Query(ge=0, le=2**63-1-READ_SPAN_MARGIN_NS)` (422); tested for -1, near 2**63 and 10**30.
  - `[medium]` `[patch]` Off-by-one on the cap: a closed window exactly `MAX_SNAPSHOTS_LIMIT` s wide holds 5001 seconds. The check now rejects `span >= cap`, and the comment records the read's transient +60 s margin as a `Known limit:` with its upgrade path.
  - `[low]` `[patch]` Removed the dead `except (HTTPException, MalformedInstrumentId): raise` in `_read_catalog`, so a `MalformedInstrumentId` raised inside a read (after `venue_of` already passed) is ledgered as a malfunction rather than passed off as a client 400.
  - `[low]` `[patch]` The app.py docstring claimed "same input bounds and error mapping" as the `/api/*` routes. It now states the actual bounds and that no caller of these routes is left in the repo.
  - `[low]` `[patch]` The ledger tests now use a `_clean_ledger` fixture instead of reset calls at start and end, so a failing assertion cannot leak counts into later tests.
  - `[low]` `[patch]` The `days` test bounds in test_data_api.py and test_metrics.py are built from `METRICS_HISTORY_MAX_DAYS`, not the literals 31/32.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 0, low 3)
- defer: 0
- reject: 16 (high 0, medium 0, low 16)
- addressed_findings:
  - `[low]` `[patch]` The int64 test never probed the real bound. It now also rejects `_MAX_TS_NS + 1` (renamed `..._outside_the_readable_range`), and a new test reads a seeded catalog at exactly `_MAX_TS_NS` and gets 200 `[]`, which proves the read's `+60 s` margin does not overflow.
  - `[low]` `[patch]` The widest-window test asserted only `200`. It now asserts the seeded row is served (`_served_rows`), so a silently empty answer at the cap would fail.
  - `[low]` `[patch]` `_CAP_NS` in the tests uses `kernel.clocks.NS_PER_S`, as the route does, instead of the literal `1_000_000_000`.

## Design Notes

DW-102's other sub-items: `/api/health` already exists (Story 15.1), so the "no health check" point is resolved. Its NaN/Infinity point was checked during implementation (FastAPI 0.141.1, Starlette 1.6.0). A bare `JSONResponse` refuses NaN (`ValueError`), but every data_api route declares a return type, so pydantic serialises the response and quietly turns NaN into `null`. That is not the non-standard token the ledger feared, but it is not a loud failure either. It is app-wide, not specific to these legacy routes, so this bundle leaves it open. It needs its own fix, recorded in the Auto Run Result.

The span cap reuses `/api/snapshots`' 5000-row ceiling: both routes return one archived row per second, so the same per-request memory bound applies. `chart_series.MAX_QUERY_SPAN_SECONDS` (7 days) is too wide for these routes, because they materialise every raw second as dicts.

## Verification

**Commands:**
- `cd platform && python3 -m pytest data_api/tests views/tests ranking/tests tests/test_boundaries.py -q -W error` -- expected: all pass
- `cd platform && ruff check data_api views ranking && ruff format --check data_api views ranking && mypy data_api/app.py data_api/settings.py data_api/routes/metrics.py` -- expected: clean

## Auto Run Result

Status: done

**Summary:** This was a follow-up review of the shipped hardening (`54a887f3ed`; DW-99, DW-100, DW-101, DW-130, and DW-102 except its NaN point). Two reviewers (Blind Hunter and Edge Case Hunter) checked the full diff since `3a4913ea08` and found no defects in the shipped behaviour. Three test-precision patches were applied (commit `80681b7315`).

**Files changed this pass:**
- `platform/data_api/tests/test_data_api.py`:
  - the int64 test also probes `_MAX_TS_NS + 1`;
  - a new test reads at exactly `_MAX_TS_NS`;
  - the widest-window test checks that the row is served;
  - `_CAP_NS` uses `NS_PER_S`.

**Review:** 3 patches applied (low), 0 deferred, 16 rejected. The main reasons for rejecting:
- **Two findings did not reproduce when run:**
  - The claim that a venue-suffixed but unparsable id is ledgered as a 500 is false. `a.b.X`, `a b.DYDX`, `..DYDX` and `BTC*.DYDX` all return 200 with an empty result against a seeded catalog, and nothing is ledgered.
  - The claim that `/metrics/nearest`'s unbounded `ts_ns` overflows is false: `metrics_store._nearest_row` already clamps `ts` to int64.
- **The rest are required by the spec or outside its scope:**
  - The parity test that imports `ranking.__main__` is required by the spec.
  - The 500 detail carries the raw exception text because the spec requires `failed to read catalog: <exc>`.
  - The routes are hardened, not deleted, because the intent says harden.
  - `ports.py`'s Protocol default of 31 has no runtime effect.
  - Two 422 shapes, three names for the retention constant, and per-row duplicates under the cap's `Known limit:` are design remarks.
  - The `MAX_SNAPSHOTS_LIMIT` cross-reference the reviewer asked for already exists at the definition.
  - A `%2F` in the id gives a 500, but that comes from the dev environment's missing `frontend_dist` StaticFiles mount and was already there.

**Follow-up review recommended:** false. The pass touched three test lines and no production code.

**Verification:**
- `python3 -m pytest data_api/tests views/tests ranking/tests tests/test_boundaries.py -o addopts="" -W error::DeprecationWarning -W error::ResourceWarning`, with a throwaway redis on 6379: 849 passed.
- `uv run ruff check`, `ruff format --check` and `mypy` on the changed test file: clean.
- No production code or OpenAPI schema changed, so `openapi.json` was not regenerated.

**Residual risks:**
- DW-102's NaN sub-item is still open. pydantic serialises NaN/Infinity as `null` across the whole app; this needs its own entry or fix.
- The legacy `/catalog/*` and `/metrics/*` routes have no caller in the repo. Retiring them is an operator decision.
- One request can transiently load up to cap + 60 rows (documented as a `Known limit:`).
