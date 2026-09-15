# Story 15.8: 31-day metrics history page

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want to see a coin's ranking-input metrics (volume, volatility) plotted over the trailing 31 days,
so that I can decide whether a coin merits opt-in raw-delta capture.

## Acceptance Criteria

1. **`GET /api/metrics/history/{symbol}` returns the same underlying `metrics_store` query (relocated, not reimplemented) shaped as JSON for the new `HistoryPage`**, via `data_api/routes/metrics.py`.
2. **`frontend/src/pages/HistoryPage.tsx` renders each ranking-input metric (volume, volatility, etc.) as a small time-series chart covering the trailing 31 days.**
3. **A metric with no data for part of the 31-day window shows a visible gap, never an interpolated flat line** (FR44, restates AD-F6 for this page).
4. **`GET /api/metrics/nearest/{symbol}` is relocated alongside history for any nearest-value lookup the page needs.**
5. **Test:** a single integration test covers the route returning real `metrics_store` rows, including a deliberate gap case (TEST-01: touches the catalog-adjacent metrics store).

## Tasks / Subtasks

- [ ] Task 1 — `data_api/routes/metrics.py`: relocated history/nearest routes (AC: #1, #4)
  - [ ] `GET /api/metrics/history/{symbol}?days=31`: calls `ranking_engine.metrics_store.history(symbol, METRICS_DB_PATH, days)` unchanged (`ranking_engine/metrics_store.py:100-108`) — returns `list[dict]`, one row per `(ts, instrument_id)` with `COLS` = `price, pct_1h, pct_24h, volatility, ofi, microprice, spread, rank, volume24h` (any of which may be `None` for a given row — that's the gap signal, not an error). Wrap in a Pydantic response model (AD-F5).
  - [ ] `GET /api/metrics/nearest/{symbol}?ts_ns=<int>`: calls `metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)` unchanged (`ranking_engine/metrics_store.py:111-120`) — `None` when nothing has ever been stored for that instrument (an honest empty state, not an error).
  - [ ] **These are new routes under `/api/metrics/...` — do not touch or rename the existing `data_api/app.py:88-95` `@app.get("/metrics/history/{symbol}")`/`@app.get("/metrics/nearest/{symbol}")` routes.** Those are `dashboard.py`'s existing remote-mode (`DATA_API_URL`) call targets (`ml_signals/dashboard.py:1752-1756,2193-2200`) and must keep working verbatim until Story 15.10's cutover deletes `dashboard.py` — same "old vs. new namespace coexist" pattern Story 15.3 established for `/catalog/candles` vs. `/api/candles`.
  - [ ] **No cursor pagination on this route, and this is a deliberate, documented exception to AD-F3's "every history endpoint... without exception" language, not an oversight** — see Dev Notes for why.
  - [ ] Register above the `/api/{full_path:path}` catch-all.

- [ ] Task 2 — `frontend/src/pages/HistoryPage.tsx`: small-multiples 31-day charts (AC: #2, #3)
  - [ ] Replace the Story 15.1-era placeholder. Fetch `GET /api/metrics/history/{iid}` (React Query), then render one small chart per metric column (`price`, `pct_1h`, `pct_24h`, `volatility`, `ofi`, `microprice`, `spread`, `volume24h`) — a metric whose column is entirely `None` across the whole response is skipped (matches today's `_history_page_from_rows`' own "skip if all values are None" behavior, `ml_signals/dashboard.py:1271-1272`).
  - [ ] **Use `lightweight-charts` (already a frontend dependency from Story 15.3) for these small charts too — do not introduce a second charting library** for what is otherwise the same "time series with gaps" rendering problem this epic already solved twice (candles, Lines mode). One independent, small `createChart()` per metric tile is fine here and does **not** violate AD-F4 — that rule is scoped to a single coin's synced chart+indicator-pane page (Story 15.4), which has a real cross-pane sync requirement; these are independent small-multiples tiles with no sync requirement between them.
  - [ ] Feed a `None` value as a `lightweight-charts` whitespace data point (no value field) at that timestamp — same gap discipline as every other chart in this epic (AD-F6), not a value of `0` or an omitted row (which a line series would otherwise interpolate across).

- [ ] Task 3 — Codegen + Tests (AC: #1, #4, #5)
  - [ ] Add Pydantic response models, regenerate `openapi.json`/`schema.ts`, add `fetchMetricsHistory()`/`fetchMetricsNearest()` to `client.ts`.
  - [ ] `data_api/tests/test_metrics.py` (new): write real rows via `metrics_store.write()` to a temp SQLite path (TEST-01/03 — real store, no mocking), including at least one row with a `None` metric column (the deliberate gap case, AC #5) and one row with every column populated; assert the route's JSON reflects both faithfully (the `None` stays `None`, never coerced to `0` or dropped).
  - [ ] Run the full backend + frontend test/build/lint commands per prior stories' convention.

## Dev Notes

- **Why this route is exempt from AD-F3's cursor-pagination contract, stated explicitly rather than silently skipped:** AD-F3 binds `/api/candles`, `/api/snapshots`, and "any future `/api/*` route the frontend adds for scroll-back." This route has no scroll-back — `metrics_store.history()` is already a small, fixed 31-day window (one row per instrument per polling tick, not per-second order-book depth), fetched once per page load, matching today's `dashboard.py` behavior exactly (`_render_history_page` calls `metrics_store.history(..., days=31)` with no further paging, `ml_signals/dashboard.py:1281-1284`). It is bounded by construction (31 days × a modest per-tick row size), not by a pagination cursor — this is a scope judgment, not a loophole, and should not be copied as precedent for a route that *does* scroll further back than a fixed window.
- **This is the first page in the epic charting genuinely independent (non-synced) small time series** — resist importing any of Story 15.4's pane-sync machinery here; it solves a different problem (N series on one shared time axis, panned/zoomed together) that this page doesn't have.
- **`troll/CLAUDE.md` constraints that apply:** AD-F2 (relocates `metrics_store.history`/`nearest` unchanged, no reimplementation), AD-F6/DATA-01 (gap honesty, AC #3), DESIGN-01 (no pagination machinery this route doesn't need).

### Project Structure Notes

- New: `troll/data_api/routes/metrics.py`, `troll/data_api/tests/test_metrics.py`.
- Modified: `troll/data_api/app.py` (wire the new router — the existing bare `/metrics/...` functions at lines 88-95 stay exactly as they are), `troll/frontend/src/pages/HistoryPage.tsx` (placeholder → real), `troll/frontend/src/api/client.ts`/`schema.ts`/`openapi.json`.
- Not modified: `troll/ranking_engine/metrics_store.py` (reused unchanged), `troll/ml_signals/dashboard.py`'s `_render_history_page`/`_history_page_from_rows`/`history_handler` (untouched until Story 15.10).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 15.8, lines 1471-1497] — this story's origin.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md] — Structural Seed's `routes/metrics.py` entry; AD-F3's exact binding language ("every history endpoint bound above" — `/api/candles`/`/api/snapshots`/future scroll-back routes only, informing this story's documented exemption).
- [Source: troll/ranking_engine/metrics_store.py] — full file read this session; `history()`/`nearest()`/`COLS`, the exact functions this story's routes wrap unchanged.
- [Source: troll/ml_signals/dashboard.py:1260-1284,1752-1756,2193-2200] — `_history_page_from_rows`/`_render_history_page`/`history_handler`, confirms today's `connectgaps=False` Plotly gap behavior (the precedent AC #3 carries forward) and the existing `/metrics/history`/`/metrics/nearest` remote-mode call sites this story's new routes must not disturb.
- [Source: troll/data_api/app.py:88-95] — the existing (untouched) old-namespace `/metrics/history/{symbol}`/`/metrics/nearest/{symbol}` routes.
- [Source: troll/frontend/src/pages/HistoryPage.tsx] — confirms it is still the Story 15.1-era placeholder as of this story's creation.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
