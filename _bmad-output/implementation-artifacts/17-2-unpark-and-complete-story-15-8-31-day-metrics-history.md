# Story 17.2: Unpark and complete Story 15.8 — 31-day metrics history

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want the parked 31-day metrics history page finished,
so that Story 17.3's Performance-tab multi-window % change has a real, shared historical-data source instead of a second implementation, and so I can decide whether a coin merits opt-in raw-delta capture.

## Acceptance Criteria

This story executes Story 15.8's original scope verbatim (`_bmad-output/implementation-artifacts/15-8-31-day-metrics-history-page.md`, fully scoped, zero code written, deliberately parked in-progress 2026-09-16 at the user's request — not blocked, just deferred). Restated here so this file is self-sufficient:

1. **`GET /api/metrics/history/{symbol}` returns the same underlying `metrics_store` query (relocated, not reimplemented) shaped as JSON for `HistoryPage`**, via a new `troll/data_api/routes/metrics.py`.
2. **`troll/frontend/src/pages/HistoryPage.tsx` (currently the Story 15.1-era placeholder — confirmed by reading it) renders each ranking-input metric (`price`, `pct_1h`, `pct_24h`, `volatility`, `ofi`, `microprice`, `spread`, `volume24h` — `metrics_store.COLS`) as a small time-series chart covering the trailing 31 days.**
3. **A metric with no data for part of the 31-day window shows a visible gap, never an interpolated flat line** (DATA-01/AD-F6).
4. **`GET /api/metrics/nearest/{symbol}` is relocated alongside history for any nearest-value lookup the page needs.**
5. **On completion, this story's status AND Story 15.8's own `sprint-status.yaml` entry both move to `done`** — 15.8's scope is being executed here, not duplicated as separate work; do not implement it twice.
6. **Test:** a single integration test covers the route returning real `metrics_store` rows, including a deliberate gap case (TEST-01: touches the catalog-adjacent metrics store).

## Tasks / Subtasks

- [ ] Task 1 — `data_api/routes/metrics.py`: relocated history/nearest routes (AC: #1, #4)
  - [ ] `GET /api/metrics/history/{symbol}?days=31`: calls `ranking_engine.metrics_store.history(symbol, METRICS_DB_PATH, days)` unchanged (`ranking_engine/metrics_store.py:102-111`, full file read this session) — returns `list[dict]`, one row per `(ts, instrument_id)` with `COLS = price, pct_1h, pct_24h, volatility, ofi, microprice, spread, rank, volume24h` (any of which may be `None` for a given row — that's the gap signal, not an error). Wrap in a Pydantic response model (AD-F5).
  - [ ] `GET /api/metrics/nearest/{symbol}?ts_ns=<int>`: calls `metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)` unchanged (`metrics_store.py:114-125`) — `None` when nothing has ever been stored for that instrument (an honest empty state, not an error).
  - [ ] **These are new routes under `/api/metrics/...` — do not touch or rename the existing `data_api/app.py:88-95` `@app.get("/metrics/history/{symbol}")`/`@app.get("/metrics/nearest/{symbol}")` routes.** Those are `dashboard.py`'s existing remote-mode (`DATA_API_URL`) call targets (`ml_signals/dashboard.py:1752-1756,2193-2200`) and must keep working verbatim until Story 15.10's cutover deletes `dashboard.py` — same "old vs. new namespace coexist" pattern Story 15.3 established for `/catalog/candles` vs. `/api/candles`.
  - [ ] **No cursor pagination on this route, and this is a deliberate, documented exception to AD-F3's "every history endpoint... without exception" language, not an oversight** (see Dev Notes for why).
  - [ ] Register above the `/api/{full_path:path}` catch-all.

- [ ] Task 2 — `frontend/src/pages/HistoryPage.tsx`: small-multiples 31-day charts (AC: #2, #3)
  - [ ] Replace the Story 15.1-era placeholder (`troll/frontend/src/pages/HistoryPage.tsx`, currently 10 lines: `<div className="term-box" data-label="History"><p>History — not yet implemented (Story 15.8).</p></div>`). Fetch `GET /api/metrics/history/{iid}` (React Query), then render one small chart per metric column (`price`, `pct_1h`, `pct_24h`, `volatility`, `ofi`, `microprice`, `spread`, `volume24h`) — a metric whose column is entirely `None` across the whole response is skipped (matches today's `dashboard.py`'s `_history_page_from_rows`' own "skip if all values are None" behavior, `ml_signals/dashboard.py:1271-1272`).
  - [ ] **Use `lightweight-charts` (already a frontend dependency from Story 15.3) for these small charts too — do not introduce a second charting library.** One independent, small `createChart()` per metric tile is fine and does **not** violate AD-F4 — that rule is scoped to a single coin's synced chart+indicator-pane page (Story 15.4), which has a real cross-pane sync requirement; these are independent small-multiples tiles with no sync requirement between them.
  - [ ] Feed a `None` value as a `lightweight-charts` whitespace data point (no value field) at that timestamp — same gap discipline as every other chart in this epic (AD-F6), never a value of `0` or an omitted row (which a line series would otherwise interpolate across).

- [ ] Task 3 — Codegen + Tests (AC: #1, #4, #6)
  - [ ] Add Pydantic response models, regenerate `openapi.json`/`schema.ts`, add `fetchMetricsHistory()`/`fetchMetricsNearest()` to `client.ts`.
  - [ ] `data_api/tests/test_metrics.py` (new): write real rows via `metrics_store.write()` to a temp SQLite path (TEST-01/03 — real store, no mocking), including at least one row with a `None` metric column (the deliberate gap case) and one row with every column populated; assert the route's JSON reflects both faithfully (the `None` stays `None`, never coerced to `0` or dropped).
  - [ ] Run the full backend + frontend test/build/lint commands per prior stories' convention.

- [ ] Task 4 — Close out both tracking entries (AC: #5)
  - [ ] On completion, update `sprint-status.yaml`: `15-8-31-day-metrics-history-page: done` and `17-2-unpark-and-complete-story-15-8-31-day-metrics-history: done`. Also update `15-8-31-day-metrics-history-page.md`'s own `Status:` header field to `done` (it currently reads `ready-for-dev`, pre-dating the 2026-09-16 park — `sprint-status.yaml` was the source of truth for the parked state, but the file's own header should not be left stale once real work completes it).

## Dev Notes

- **This story IS Story 15.8 — Epic 17 unparks it rather than re-scoping it.** Nothing about the original scope changed; the only thing new is the explicit dependency this creates for Story 17.3 (Performance tab % change), which reuses `metrics_store`'s query path built here.
- **Why this route is exempt from AD-F3's cursor-pagination contract, stated explicitly rather than silently skipped:** AD-F3 binds `/api/candles`, `/api/snapshots`, and "any future `/api/*` route the frontend adds for scroll-back." This route has no scroll-back — `metrics_store.history()` is already a small, fixed 31-day window (one row per instrument per polling tick, not per-second order-book depth), fetched once per page load, matching today's `dashboard.py` behavior exactly (`_render_history_page` calls `metrics_store.history(..., days=31)` with no further paging, `ml_signals/dashboard.py:1281-1284`). It is bounded by construction (31 days × a modest per-tick row size), not by a pagination cursor.
- **This is the first genuinely independent (non-synced) small-time-series charting page in this epic** — resist importing any of Story 15.4's pane-sync machinery here; it solves a different problem (N series on one shared time axis, panned/zoomed together) this page doesn't have.
- **Confirmed already multi-venue-safe:** `metrics_store`'s `PRIMARY KEY (ts, instrument_id)` keys every row by the *full* instrument_id (e.g. `BTC-USD-PERP.DYDX`), not a bare symbol — no schema change needed when Epic 19 adds Bybit/Hyperliquid.
- **`troll/CLAUDE.md` constraints that apply:** AD-F2 (relocates `metrics_store.history`/`nearest` unchanged, no reimplementation), AD-F6/DATA-01 (gap honesty), DESIGN-01 (no pagination machinery this route doesn't need).

### Project Structure Notes

- New: `troll/data_api/routes/metrics.py`, `troll/data_api/tests/test_metrics.py`.
- Modified: `troll/data_api/app.py` (wire the new router — the existing bare `/metrics/...` functions at lines 88-95 stay exactly as they are), `troll/frontend/src/pages/HistoryPage.tsx` (placeholder → real), `troll/frontend/src/api/client.ts`/`schema.ts`/`openapi.json`.
- Not modified: `troll/ranking_engine/metrics_store.py` (reused unchanged), `troll/ml_signals/dashboard.py`'s `_render_history_page`/`_history_page_from_rows`/`history_handler` (untouched until Story 15.10).

### References

- [Source: _bmad-output/implementation-artifacts/15-8-31-day-metrics-history-page.md] — this story's full original scope, restated here verbatim per Epic 17's unparking.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 17, Story 17.2] — this story's origin (FR52).
- [Source: _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md] — AD-F3's exact binding language (informing this story's documented exemption); Structural Seed's `routes/metrics.py` entry.
- [Source: troll/ranking_engine/metrics_store.py] — full file read this session; `history()`/`nearest()`/`COLS`/`write()`'s `retain_days=31` default, the exact functions this story's routes wrap unchanged.
- [Source: troll/frontend/src/pages/HistoryPage.tsx] — full file read this session; confirms it is still the Story 15.1-era 10-line placeholder.
- [Source: troll/ml_signals/dashboard.py:1260-1284,1752-1756,2193-2200] — `_history_page_from_rows`/`_render_history_page`/`history_handler`, confirms today's gap behavior (the precedent AC #3 carries forward) and the existing `/metrics/history`/`/metrics/nearest` remote-mode call sites this story's new routes must not disturb.
- [Source: troll/data_api/app.py:88-95] — the existing (untouched) old-namespace `/metrics/history/{symbol}`/`/metrics/nearest/{symbol}` routes.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
