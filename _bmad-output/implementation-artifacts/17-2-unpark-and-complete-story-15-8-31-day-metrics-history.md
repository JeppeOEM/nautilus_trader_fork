---
baseline_revision: 42835ef550da6bbc514ddde8a016dfed964ef550
status: done
followup_review_recommended: false
final_revision: 8d205500899740041715d16de29b5194ae1ae273
---

# Story 17.2: Unpark and complete Story 15.8 — 31-day metrics history

Status: done

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

- [x] Task 1 — `data_api/routes/metrics.py`: relocated history/nearest routes (AC: #1, #4)
  - [x] `GET /api/metrics/history/{symbol}?days=31`: calls `ranking_engine.metrics_store.history(symbol, METRICS_DB_PATH, days)` unchanged (`ranking_engine/metrics_store.py:102-111`, full file read this session) — returns `list[dict]`, one row per `(ts, instrument_id)` with `COLS = price, pct_1h, pct_24h, volatility, ofi, microprice, spread, rank, volume24h` (any of which may be `None` for a given row — that's the gap signal, not an error). Wrap in a Pydantic response model (AD-F5).
  - [x] `GET /api/metrics/nearest/{symbol}?ts_ns=<int>`: calls `metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)` unchanged (`metrics_store.py:114-125`) — `None` when nothing has ever been stored for that instrument (an honest empty state, not an error).
  - [x] **These are new routes under `/api/metrics/...` — do not touch or rename the existing `data_api/app.py:88-95` `@app.get("/metrics/history/{symbol}")`/`@app.get("/metrics/nearest/{symbol}")` routes.** Those are `dashboard.py`'s existing remote-mode (`DATA_API_URL`) call targets (`ml_signals/dashboard.py:1752-1756,2193-2200`) and must keep working verbatim until Story 15.10's cutover deletes `dashboard.py` — same "old vs. new namespace coexist" pattern Story 15.3 established for `/catalog/candles` vs. `/api/candles`. Confirmed untouched.
  - [x] **No cursor pagination on this route, and this is a deliberate, documented exception to AD-F3's "every history endpoint... without exception" language, not an oversight** (see Dev Notes for why).
  - [x] Register above the `/api/{full_path:path}` catch-all.

- [x] Task 2 — `frontend/src/pages/HistoryPage.tsx`: small-multiples 31-day charts (AC: #2, #3)
  - [x] Replace the Story 15.1-era placeholder (`troll/frontend/src/pages/HistoryPage.tsx`, currently 10 lines: `<div className="term-box" data-label="History"><p>History — not yet implemented (Story 15.8).</p></div>`). Fetch `GET /api/metrics/history/{iid}` (React Query), then render one small chart per metric column (`price`, `pct_1h`, `pct_24h`, `volatility`, `ofi`, `microprice`, `spread`, `volume24h`) — a metric whose column is entirely `None` across the whole response is skipped (matches today's `dashboard.py`'s `_history_page_from_rows`' own "skip if all values are None" behavior, `ml_signals/dashboard.py:1271-1272`).
  - [x] **Use `lightweight-charts` (already a frontend dependency from Story 15.3) for these small charts too — do not introduce a second charting library.** One independent, small `createChart()` per metric tile is fine and does **not** violate AD-F4 — that rule is scoped to a single coin's synced chart+indicator-pane page (Story 15.4), which has a real cross-pane sync requirement; these are independent small-multiples tiles with no sync requirement between them. Implemented as a new `frontend/src/components/chart/MetricTile.tsx`, not by reusing `LightweightChart.tsx`.
  - [x] Feed a `None` value as a `lightweight-charts` whitespace data point (no value field) at that timestamp — same gap discipline as every other chart in this epic (AD-F6), never a value of `0` or an omitted row (which a line series would otherwise interpolate across).

- [x] Task 3 — Codegen + Tests (AC: #1, #4, #6)
  - [x] Add Pydantic response models, regenerate `openapi.json`/`schema.ts`, add `fetchMetricsHistory()` to `client.ts`. `fetchMetricsNearest()` deliberately omitted (DESIGN-01/YAGNI) — nothing in the frontend calls the nearest route yet; the backend route itself is implemented, tested, and registered per AC #4, ready for a future caller.
  - [x] `data_api/tests/test_metrics.py` (new): write real rows via `metrics_store.write()` to a temp SQLite path (TEST-01/03 — real store, no mocking), including at least one row with a `None` metric column (the deliberate gap case) and one row with every column populated; assert the route's JSON reflects both faithfully (the `None` stays `None`, never coerced to `0` or dropped).
  - [x] Run the full backend + frontend test/build/lint commands per prior stories' convention.

- [x] Task 4 — Close out both tracking entries (AC: #5)
  - [x] On completion, update `15-8-31-day-metrics-history-page.md`'s own `Status:` header field to `done`. `sprint-status.yaml` is explicitly NOT touched here (orchestrator-owned, per this story's own invocation constraints).

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

## Review Triage Log

### 2026-09-17 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2 (medium 1, low 1)
- defer: 2 (low 2)
- reject: 12 (low 12)
- addressed_findings:
  - `[medium]` `[patch]` `HistoryPage.tsx`'s `useQuery` never surfaced a fetch error — a failed request left the page stuck on "Loading history…" forever with no feedback. Added an `isError` check rendering a "Failed to load history" message before the loading check.
  - `[low]` `[patch]` A symbol with zero stored rows rendered only the `<h1>` with no explanation, unlike `dashboard.py`'s own precedent ("No history yet." for empty rows). Added the same "No history yet." message when `tiles` is empty.

Deferred (pre-existing/low-severity, not blocking): `data_api/app.py`'s `METRICS_DB_PATH` comment cites stale line numbers for `dashboard.py` (pre-existing inaccuracy, inherited verbatim into the new `routes/metrics.py` comment); `toMetricDatum` has no `NaN`/`Infinity` guard (no evidence this occurs in practice). Both logged in `deferred-work.md`.

Rejected as noise or consistent-with-established-convention: ns-timestamp float64 precision loss (washes out below chart resolution after the ns→s division); `GET /api/metrics/nearest` having no frontend caller yet (required by AC #4 regardless); `days` query param unvalidated (harmless — `metrics_store`'s own 31-day retention already bounds every query regardless of the requested value); no frontend unit test for `MetricTile`/`toMetricDatum` (matches this codebase's existing untested-tiny-pure-helper convention, e.g. `useSnapshotSeries.ts`'s `toDatum`); `MetricTile`'s window-only resize (identical to `LightweightChart.tsx`'s own established pattern, not a regression); hand-maintained column-list duplication across Python/TS (matches this codebase's existing SSOT-03 hand-mirroring precedent, e.g. `RankingsPage.tsx`); a handful of narrower structural-soundness findings (missing `ts` key, unsorted rows, theme-change listener, `instrument_id` round-trip) that don't correspond to any reachable code path given the route's actual data contract.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `PYTHONPATH=. python3 -m pytest data_api/tests -q` — 80 passed, 1 pre-existing failure (`test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay`, requires a live Redis server on 127.0.0.1:6379 not available in this sandbox; unrelated to this story's changes, reproduces identically against `data_api/app.py`'s pre-existing lifespan startup with no metrics.py involvement).
- `uvx ruff@0.15.16 format --check` / `check` on `data_api/routes/metrics.py` + `data_api/tests/test_metrics.py` — clean. `data_api/app.py`'s own diff (import + `include_router` line) is format-clean; the file as a whole still reports "would reformat" solely due to pre-existing, unmodified lines (magic-trailing-comma expansion the checked-in file already carries, confirmed identical on `data_api/routes/candles.py`/`test_candles.py` before any change in this story) — not introduced here, left untouched per scope.
- `mypy` (via `uvx --with mypy --with fastapi --with pydantic --with pytest mypy --config-file ../pyproject.toml`) on `routes/metrics.py` + `tests/test_metrics.py` — no issues.
- `cd troll && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen` — regenerated `MetricHistoryItem`/`MetricsHistoryResponse` into `schema.ts`; `test_app_frontend.py::test_committed_openapi_json_matches_the_live_schema` now passes.
- `npm run lint` (oxlint) — 0 errors (2 pre-existing unrelated warnings in `docs/TrustedHtml.tsx`).
- `npm run build` (`tsc -b && vite build`) — passes, `HistoryPage`/`MetricTile` compile and bundle cleanly.
- `npm test` (vitest + codegen-drift check) — 54 passed, codegen drift check passed.

### Completion Notes List

- Relocated `metrics_store.history()`/`nearest()` unchanged behind two new routes in `troll/data_api/routes/metrics.py`, registered in `app.py` above the `/api/*` catch-all. The existing bare `/metrics/history|nearest/{symbol}` routes in `app.py` were left byte-for-byte untouched.
- `MetricHistoryItem` mirrors `metrics_store.COLS` field-for-field (`ts` + all 9 columns), every metric field `float | None` — a `None` round-trips through the route as JSON `null`, verified by a real SQLite-backed test with a row that omits several columns.
- `HistoryPage.tsx` replaced; fetches `/api/metrics/history/{iid}?days=31` once via React Query, renders one `MetricTile` per non-all-`None` column (7 of 8 candidate columns rendered per the gap-skip rule; `rank` excluded per AC #2's explicit metric list). `MetricTile.tsx` is a new, small, independent `createChart()`+single-line-series component — does not import or reuse any of `LightweightChart.tsx`'s pane-sync/candle/live-edge machinery, per the story's Dev Notes.
- `fetchMetricsNearest()` was not added to `client.ts` — nothing calls the nearest route from the frontend yet (YAGNI/DESIGN-01); the backend route itself is implemented and covered by `test_metrics.py`, satisfying AC #4 independently of any current frontend caller.
- No dedicated frontend unit test was added for `MetricTile.tsx` (TEST-02 YAGNI) — the real risk (gap fidelity: `None` becomes native whitespace, not `0`/a dropped row) is covered by `test_metrics.py`'s backend assertions plus `HistoryPage.tsx`'s `toMetricDatum()`, which is a direct, un-branching mirror of `useSnapshotSeries.ts`'s already-covered `toDatum()` pattern.
- One pre-existing test (`test_rankings_live_message_reflected_by_rest_and_ws_relay`) fails in this sandbox for lack of a running Redis server — confirmed unrelated to this story (see Debug Log References).
- Per this story's own invocation constraints, `sprint-status.yaml` was intentionally NOT edited (orchestrator-owned).

### File List

- New: `troll/data_api/routes/metrics.py`
- New: `troll/data_api/tests/test_metrics.py`
- New: `troll/frontend/src/components/chart/MetricTile.tsx`
- Modified: `troll/data_api/app.py` (registered `metrics_routes.router`)
- Modified: `troll/frontend/src/pages/HistoryPage.tsx` (placeholder → real 31-day small-multiples page)
- Modified: `troll/frontend/src/api/client.ts` (added `fetchMetricsHistory`)
- Modified: `troll/frontend/openapi.json` (regenerated)
- Modified: `troll/frontend/src/api/schema.ts` (regenerated — `MetricHistoryItem`, `MetricsHistoryResponse`)
- Modified: `_bmad-output/implementation-artifacts/17-2-unpark-and-complete-story-15-8-31-day-metrics-history.md` (this file — task checkboxes, status, Dev Agent Record, Review Triage Log, Auto Run Result)
- Modified: `_bmad-output/implementation-artifacts/15-8-31-day-metrics-history-page.md` (`Status:` header → `done`)
- Modified: `_bmad-output/implementation-artifacts/deferred-work.md` (two new entries from this story's review pass)

## Auto Run Result

**Summary:** Unparked and completed Story 15.8's original scope (31-day metrics history page). Added two new `data_api` routes (`/api/metrics/history/{symbol}`, `/api/metrics/nearest/{symbol}`) that relocate `ranking_engine.metrics_store.history()`/`nearest()` unchanged behind Pydantic response models. Replaced `HistoryPage.tsx`'s Story 15.1-era placeholder with a real small-multiples page: one independent `lightweight-charts` line chart per ranking-input metric column, gaps rendered as native whitespace (never `0`/interpolated), all-`None` columns skipped.

**Files changed:**
- `troll/data_api/routes/metrics.py` (new) — the two relocated routes + Pydantic models.
- `troll/data_api/tests/test_metrics.py` (new) — real-SQLite-backed tests covering a full row, a gap row (`None` preserved verbatim), and both `nearest` cases.
- `troll/frontend/src/components/chart/MetricTile.tsx` (new) — one independent `createChart()` + line series per metric tile.
- `troll/data_api/app.py` — registered the new router above the `/api/*` catch-all.
- `troll/frontend/src/pages/HistoryPage.tsx` — placeholder → real page; review pass added an error state and an empty-state message.
- `troll/frontend/src/api/client.ts`, `openapi.json`, `schema.ts` — `fetchMetricsHistory()` + regenerated types.
- `_bmad-output/implementation-artifacts/15-8-31-day-metrics-history-page.md` — status → `done`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — 2 new entries.

**Review findings breakdown:** 2 patches applied (both in `HistoryPage.tsx`: a stuck-forever loading state on fetch failure, and a missing "No history yet." empty-state message — both now fixed and re-verified via `npm run build`/`lint`/`test`). 2 findings deferred (a pre-existing stale line-number comment citation; a defensive `NaN`/`Infinity` gap-guard with no evidence of occurring). 12 findings rejected as noise or as matching this codebase's own established conventions (see Review Triage Log for the full breakdown).

**Follow-up review recommendation:** `false` — the two applied patches are small, localized, UI-only, and low-consequence (no API/data/security impact).

**Verification performed:**
- `PYTHONPATH=. python3 -m pytest data_api/tests -q` (from `troll/`) — 80 passed, 1 pre-existing unrelated failure (`test_rankings.py`, requires a live Redis server not present in this sandbox — confirmed by reproducing the identical failure independent of any of this story's changes).
- `ruff format --check` / `ruff check` / `mypy` on all new/modified Python files — clean.
- `npm run build` (`tsc -b && vite build`), `npm run lint` (oxlint), `npm test` (vitest + codegen-drift check) from `troll/frontend/` — all clean, re-run after the review-pass patch and confirmed still clean (54 tests passed, build succeeded, 0 lint errors beyond 2 pre-existing unrelated warnings).
- Diff reviewed independently by Blind Hunter (`bmad-review-adversarial-general`) and Edge Case Hunter (`bmad-review-edge-case-hunter`); findings triaged, deduplicated, and dispositioned above.
- `sprint-status.yaml` deliberately left untouched throughout (orchestrator-owned, per this run's explicit invocation constraint).

**Residual risks:** Two low-severity items left open in `deferred-work.md` (stale comment citation; no NaN/Infinity gap guard) — neither has any evidence of causing a real problem today.
