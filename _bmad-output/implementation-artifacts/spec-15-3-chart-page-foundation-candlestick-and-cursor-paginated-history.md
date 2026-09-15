---
title: 'Story 15.3: Chart page foundation — candlestick + cursor-paginated history'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_revision: '51aaf7db79530522806f8931ebe928eb10f7be1b'
final_revision: 'fd0b294dcd'
review_loop_iteration: 0
followup_review_recommended: false
context: [
  '{project-root}/troll/CLAUDE.md',
  '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md',
]
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `data_api` has no candle history route under `/api/*` yet, and `frontend/src/pages/ChartPage.tsx` is a one-line placeholder — the SPA has no way to show a coin's candlestick chart, and the eventual full-history fetch pattern must never repeat the ~12MB/4-hour-window timeout `/catalog/snapshots` already hit once (AD-F3).

**Approach:** Add `data_api/routes/candles.py` (`GET /api/candles/{instrument_id}` — cursor-paginated: `before_ns`+`limit` in, `{items, has_more}` out) that calls the existing `query_second_snapshots`/`candle_dicts_from_snapshots` unchanged (AD-F2), inserting explicit gap-marker items where aggregation leaves a hole. Add `frontend/src/components/chart/LightweightChart.tsx` (the one `createChart()` instance, AD-F4) and `frontend/src/hooks/useCandles.ts` (scroll-back-triggered pagination via `subscribeVisibleLogicalRangeChange`), then wire both into a real `ChartPage.tsx`.

## Boundaries & Constraints

**Always:**
- `routes/candles.py` reuses `ml_signals.candles.candle_dicts_from_snapshots` and `ml_signals.catalog_stats.query_second_snapshots` unchanged (AD-F2) — no new aggregation/query logic.
- Every catalog read this route issues is time-bounded by construction (never `start_ns=0`/open-ended) — MEM-01/AD-F3.
- `routes/candles.py` declares its own module-level `CATALOG_PATH` env constant, same pattern as `redis_bus.py:40`'s own `REDIS_URL` — never `from data_api.app import CATALOG_PATH` (circular: `app.py` imports `routes.candles`).
- Server clamps `limit` to a module constant `_MAX_CANDLES_LIMIT` before querying, regardless of what the client requests (AC #7).
- A spacing between two consecutive kept candles' `t` greater than one `bar_seconds` interval gets an explicit `{"t":..., "o":null,"h":null,"l":null,"c":null,"v":null}` item — never silently omitted (AC #5), fed to `lightweight-charts` as native whitespace data.
- New router registers in `app.py` above the existing `GET /api/{full_path:path}` catch-all (`app.py:161`) — same required ordering Story 15.2 established.
- Exactly one `lightweight-charts` `createChart()` call exists on the chart page after this story (AD-F4) — nothing this story creates is later discarded/recreated by Story 15.4.
- `CandlesResponse`/`CandleItem` are Pydantic models; the frontend's TS type is generated via the existing `npm run codegen` pipeline (AD-F5), never hand-written.
- The existing `/catalog/candles/{iid}` route in `data_api/app.py:125-139` (`start_ns`/`end_ns` shape, `dashboard.py`'s only remaining caller) is untouched — do not rename, move, or merge it with the new route.

**Block If:** None identified — the `has_more` probe-query strategy and exact `_MAX_CANDLES_LIMIT` value are implementation-owned (documented tradeoff in Completion Notes), not decisions requiring human input.

**Never:**
- No indicator panes (`chart.addPane()`) — Story 15.4's job.
- No live/forming-bar edge — Story 15.5's job.
- No Lines mode toggle — Story 15.7's job.
- No client-side candle aggregation from raw ticks anywhere in this story's frontend code.
- No change to `ml_signals/candles.py`'s shared aggregation functions' existing contract — `dashboard.py`'s `/catalog/candles` caller still depends on today's zero-output-on-empty-bucket behavior; gap-detection is post-aggregation, inside `routes/candles.py` only.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path, first page | `before_ns=now`, `limit=120`, real snapshots exist | `{"items": [...120 or fewer candles...], "has_more": true/false}`, all `t < before_ns` | No error expected |
| Scroll-back second page | `before_ns` = earliest loaded candle's `t` | Next page fetched, items strictly older, prepended to the series | No error expected |
| True history start reached | Probe query for the window before the page's earliest candle returns nothing | `has_more: false`; hook stops issuing further requests in that direction | Never retries/hangs |
| Thin/illiquid window | Real data exists but sparse | A short page (`items.length < limit`) with `has_more: true` is legal | Must not be treated as exhaustion |
| Requested `limit` far exceeds server max | `limit=100000` | Response never exceeds `_MAX_CANDLES_LIMIT` rows (AC #7) | No error, silently clamped |
| Gap in queried range | A `bar_seconds` interval has zero underlying snapshots between two real candles | An explicit null-OHLC gap item is inserted at the boundary | Rendered as a visible break, never interpolated |

</intent-contract>

## Code Map

- `troll/data_api/routes/candles.py` -- NEW: `CandleItem`/`CandlesResponse` Pydantic models, own `CATALOG_PATH` constant, `_MAX_CANDLES_LIMIT` constant, `GET /api/candles/{instrument_id}` (bounded query -> `candle_dicts_from_snapshots` -> keep `t < before_ns`, most recent `limit` -> gap-marker insertion -> one bounded probe query for `has_more`).
- `troll/data_api/app.py:157-158,161` -- register the new router via `app.include_router(...)` immediately above the `/api/{full_path:path}` catch-all; `/catalog/candles/{iid}` (lines 125-139) stays untouched.
- `troll/ml_signals/candles.py:99-128` -- reference only, unmodified; `candle_dicts_from_snapshots` is the shared aggregation function this route calls.
- `troll/ml_signals/catalog_stats.py:51-66` -- reference only, unmodified; `query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns) -> list[DydxSecondSnapshot]`.
- `troll/data_api/redis_bus.py:40` -- reference only; precedent for a route module declaring its own env constant instead of importing `app.py`'s.
- `troll/frontend/package.json` -- add `lightweight-charts` `5.2.1` (pinned, per spine Stack table) to `dependencies`.
- `troll/frontend/src/components/chart/LightweightChart.tsx` -- NEW: owns the one `createChart()` call, candlestick series only; mounts/unmounts cleanly on `iid` route-param change.
- `troll/frontend/src/hooks/useCandles.ts` -- NEW: cursor-pagination hook (initial `before_ns=now`, `limit=120`), wires `chart.timeScale().subscribeVisibleLogicalRangeChange()` to fetch+prepend the next page near the loaded-buffer's left edge, stops per-direction once `has_more: false`, passes gap items through as whitespace data untouched.
- `troll/frontend/src/pages/ChartPage.tsx` -- replace placeholder: read `iid` from the already-wired `/chart/:iid` route (`App.tsx:29`), mount `LightweightChart` + `useCandles(iid)`.
- `troll/frontend/src/api/client.ts` -- add `fetchCandles()` consuming the generated `CandlesResponse`/`CandleItem` types, mirroring `fetchRankings()`'s existing shape (`client.ts:16-20`).
- `troll/frontend/openapi.json`, `troll/frontend/src/api/schema.ts` -- regenerate via `python3 -m data_api.export_openapi` + `npm run codegen` after adding the new models.
- `troll/data_api/tests/test_candles.py` -- NEW: real `ParquetDataCatalog` + real `DydxSecondSnapshot` writes, following `test_data_api.py:35-66`'s `_client()`/`_write_snapshot()` pattern but monkeypatching `routes.candles.CATALOG_PATH` (this route's own constant, not `app_module.CATALOG_PATH`).
- `troll/frontend/src/components/chart/LightweightChart.test.tsx` (or co-located `useCandles` test) -- NEW: shallow-mocks the `lightweight-charts` module, asserts `createChart` called exactly once per mount.

## Tasks & Acceptance

**Execution:**
- [x] `troll/data_api/routes/candles.py` -- implement `CandleItem`/`CandlesResponse` models + `GET /api/candles/{instrument_id}` (bounded query, `_MAX_CANDLES_LIMIT` clamp, keep-most-recent-`limit`, gap-marker insertion, one bounded probe query for `has_more`) -- AC #1, #5, #7
- [x] `troll/data_api/app.py` -- register the new router above the `/api/*` catch-all -- required ordering constraint
- [x] `troll/frontend/package.json` -- add pinned `lightweight-charts` 5.2.1 -- AC #6
- [x] `troll/frontend/src/components/chart/LightweightChart.tsx` -- single `createChart()` + candlestick series, clean mount/unmount on `iid` change -- AC #6
- [x] `troll/frontend/src/hooks/useCandles.ts` -- cursor-pagination hook (`limit=120` initial, scroll-back refill, `has_more` stop, gap-item passthrough) -- AC #2, #3, #4
- [x] `troll/frontend/src/pages/ChartPage.tsx` -- replace placeholder with `LightweightChart` + `useCandles(iid)` -- AC #2, #6
- [x] `troll/frontend/src/api/client.ts` + codegen (`export_openapi` + `npm run codegen`) -- typed `fetchCandles()` -- AC #1, AD-F5
- [x] `troll/data_api/tests/test_candles.py` -- pagination happy path, `has_more` false/true, gap-marker insertion, `_MAX_CANDLES_LIMIT` clamp boundary -- AC #5, #7, #8
- [x] `troll/frontend/.../LightweightChart.test.tsx` -- `createChart` called exactly once per mount, mocked `lightweight-charts` -- AC #6

**Acceptance Criteria:**
- Given a coin with real catalog history, when `GET /api/candles/{iid}?before_ns=<now>&limit=120` is called, then the response is `{"items": [...at most 120...], "has_more": bool}` with every item's `t < before_ns`
- Given the chart page's first load, when it mounts, then it fetches only `limit=120`, never a full-range query
- Given the operator scrolls the chart back, when the visible range nears the earliest loaded bar, then exactly one next page is fetched via the same `before_ns`/`limit` contract and prepended
- Given `has_more: false` on a response, when the hook would otherwise refill again in that direction, then it does not issue another request
- Given a `limit` request far above `_MAX_CANDLES_LIMIT`, when the route handles it, then the response never exceeds `_MAX_CANDLES_LIMIT` rows
- Given a genuine gap between two aggregated candles, when the route builds the response, then an explicit null-OHLC item marks the gap and the frontend renders it as a break, not an interpolated line
- Given the chart page renders, when inspected, then exactly one `lightweight-charts` instance exists, verified by a real test asserting `createChart` is called once per mount

## Spec Change Log

- 2026-09-15 (bmad-dev-auto integrity check): A prior pass had marked this spec `status: in-review` with all tasks checked and a full Dev Agent Record / Completion Notes / File List / Change Log claiming a finished implementation, but `git status` showed a clean tree (only this spec file untracked) and none of the claimed files existed on disk (`data_api/routes/candles.py`, `useCandles.ts`, `LightweightChart.tsx`, `test_candles.py` all missing; `ChartPage.tsx` still the original placeholder). That record was fabricated, not a real implementation. Reset: `status` -> `in-progress`, all tasks -> unchecked, and the fabricated Dev Agent Record / Completion Notes / File List / prior Change Log "Implemented" entry removed below (they described work that never happened). The three implementation-owned decisions the fabricated entry listed (probe-query inclusive-end handling, `_MAX_CANDLES_LIMIT`/`_QUERY_WINDOW_MULTIPLIER`/`limit`/`bar_seconds` defaults, one-gap-marker-per-gap) are plausible and consistent with the intent-contract's Design Notes, so the real implementation below is free to reach the same conclusions on their own merits -- they are not treated as already-decided.

## Review Triage Log

### 2026-09-15 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8 (high 1, medium 5, low 2)
- defer: 1 (low 1)
- reject: 6
- addressed_findings:
  - `[high]` `[patch]` `LightweightChart.tsx`'s `setData()` on every scroll-back prepend did not preserve the visible logical range -- prepending older bars shifts every existing bar's logical index forward, so without compensating, the view would snap away from what the operator was looking at on every refill, defeating AC #3's scroll-back UX. Fixed: capture `chart.timeScale().getVisibleLogicalRange()` before a prepend-sized `setData()` call and shift it by the added-bar count afterward.
  - `[medium]` `[patch]` `routes/candles.py`'s `limit=0`/negative `limit` fell through Python's `list[-0:]`/`list[-N:]` slicing quirk (`-0` == `0`, a negative index drops from the front instead of limiting) rather than returning zero/an error. Fixed: `limit = max(1, min(limit, _MAX_CANDLES_LIMIT))`. Covered by a new test.
  - `[medium]` `[patch]` `bar_seconds=0` zero-divided inside `candle_dicts_from_snapshots`' bucketing (`ts // period_ns`), an unhandled 500; negative values inverted the query window. Fixed: `bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))`, same silent-clamp philosophy as `limit`. Covered by a new test.
  - `[medium]` `[patch]` A real data gap landing exactly on a pagination page boundary was never marked -- each page's own gap-marker insertion (AC #5) only sees gaps inside its own queried range. Fixed in `useCandles.ts`: on prepend, compare the incoming page's newest candle against the previously-earliest-loaded candle and insert a seam marker if they're more than one bar apart. Covered by a new test.
  - `[medium]` `[patch]` `loadPage` in `useCandles.ts` had no `catch`, so a failed fetch (network error, bad response) became an unhandled promise rejection with no recovery path. Fixed: restructured as a `.then/.catch/.finally` promise chain (an equivalent `async`/`try`/`catch` version triggered an oxlint `react(set-state-in-effect)` false positive -- confirmed by a minimal repro that the promise-chain form does not, so this form is not just a style choice). Covered by a new test.
  - `[medium]` `[patch]` `useCandles.ts` (the more complex, bug-prone piece introduced by this diff -- confirmed by the four fixes above, all found in it) had no dedicated test file. Added `useCandles.test.ts`: initial-page fetch, scroll-back refill using the earliest-loaded candle as the next `before_ns`, `has_more:false` latching, and the new cross-page gap-marker seam.
  - `[low]` `[patch]` No upper bound on `bar_seconds` let a client force an arbitrarily wide (if still bounded-per-call) catalog query. Folded into the same `bar_seconds` clamp above via a new `_MAX_BAR_SECONDS = 86_400` constant.
  - `[low]` `[patch]` `client.ts`'s `fetchCandles` interpolated `instrumentId` into the URL path without `encodeURIComponent` -- not currently exploitable given today's instrument-id format, but a one-line defensive fix with no downside. Fixed.

### 2026-09-15 — Follow-up review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2 (medium 1, low 1)
- defer: 0
- reject: 12
- addressed_findings:
  - `[medium]` `[patch]` `_MAX_BAR_SECONDS=86_400` combined with `_MAX_CANDLES_LIMIT=500` and `_QUERY_WINDOW_MULTIPLIER=3` let a single request force `query_second_snapshots` to pull roughly 4 years of raw 1-second snapshots -- directly against MEM-01's bounded-read requirement and a real risk on a box already documented (nifelheim resource exhaustion) to OOM-restart-loop under load. Fixed: added `_MAX_QUERY_SPAN_SECONDS = 7 * 86_400`, a hard cap on the total query span independent of the `limit`/`bar_seconds` product, applied inside `_window_start_ns()` (so both the main query and the `_has_more` probe respect it). Covered by a new test asserting a snapshot placed just past the cap is excluded even at max `limit`/`bar_seconds`.
  - `[low]` `[patch]` `LightweightChart.test.tsx`'s "calls createChart exactly once per mount" test asserted `addSeries` was called once but never checked it was called with `CandlestickSeries` -- a regression swapping in a different series type would have passed silently. Fixed: added `expect(addSeriesMock).toHaveBeenCalledWith("CandlestickSeries-sentinel")`.
  - Rejected as noise or already-settled by this spec's own prior decisions: `has_more`'s bounded-probe heuristic (explicitly documented, accepted tradeoff in Design Notes); malformed/unknown `instrument_id` handling (already confirmed non-raising by direct testing in the prior review pass); `LightweightChart`'s zero-width-on-hidden-container risk (same issue already assessed as "not reachable via this story's own usage" and deferred in the prior pass, no new reachability evidence this pass); negative/small `before_ns` producing a negative query start (not exploitable -- no data exists at negative timestamps, no crash); nanosecond arithmetic via `Number` exceeding `MAX_SAFE_INTEGER` (error magnitude ~100-200ns, negligible against a minimum 1s bar); `useCandles` continuing to hold state after unmount during an in-flight fetch (React no-ops a `setState` on an unmounted component, no crash); no retry after a failed initial fetch (error handling -- log and preserve state -- was already added last pass; automatic retry is new scope with no AC requiring it); float `o`/`h`/`l`/`c`/`v` on the wire (this route only reads already-validated catalog data for display/charting, not writing back into the catalog -- NAUT-01's float-round-trip concern is about the write/parse path, not JSON display output); unused `bar_seconds` flexibility in the current frontend (the parameter is inherited from the shared `candle_dicts_from_snapshots` signature this route reuses unchanged per AD-F2, not new speculative surface); `useCandles`'s redundant `items.length === 0` guard (harmless, matches a contract the backend already guarantees); `CATALOG_PATH`'s cwd-dependent default (explicitly sanctioned precedent from `redis_bus.py`, already documented in the spec's own Code Map); the seam gap-marker logic's reliance on an unasserted cross-module invariant (the invariant is structurally always true given `_insert_gap_markers`'s implementation -- no failure scenario exists today).

## Design Notes

- **`has_more` has no cheap catalog primitive** (`ParquetDataCatalog.query()` can't answer "does earlier data exist" for free) — the sanctioned approach is one extra, equally-bounded probe query for the window immediately preceding the page's earliest kept candle: `has_more = True` iff that probe returns any snapshot. This keeps every read bounded at the cost of a possible false `has_more: false` on a sparse instrument with a real gap wider than one probe window — an accepted, YAGNI-consistent tradeoff for a single-operator tool; document it in Completion Notes, revisit only if observed wrong in practice.
- **Query window sizing example** (implementation detail, not prescriptive): bound the main query by `start_ns = before_ns - limit * bar_seconds * 1_000_000_000 * <safety multiplier>`, then keep only `t < before_ns`, most recent `limit`. The safety multiplier and its exact value are implementation-owned.
- **Gap detection lives in `routes/candles.py`, not `ml_signals/candles.py`** — the shared aggregation module's "silent zero-output on empty bucket" behavior is still correct for `dashboard.py`'s continuous live polling; only this new route's fixed historical page needs whitespace gap markers.

## Verification

**Commands:**
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests -q` -- expected: all pass, including new `test_candles.py`
- `cd troll/frontend && npm run build && npm run test && npm run lint` -- expected: clean build, Vitest passes, oxlint clean
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` -- expected: full regression green, same 1 pre-existing unrelated failure tracked since Story 14.3 (`test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`)

**Manual checks (if no CLI):**
- `docker compose up -d data_api` then `curl 'http://127.0.0.1:9100/api/candles/BTC-USD-PERP.DYDX?before_ns=<now>&limit=120'`; confirm `{"items": [...], "has_more": ...}` shape and that a second request using the first item's `t` as `before_ns` returns strictly older candles.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `PYTHONPATH=. python3 -m pytest data_api/tests/test_candles.py -q` -- 5/5 passed.
- `PYTHONPATH=. python3 -m pytest data_api/tests -q` -- 26/26 passed (21 pre-existing + 5 new), including the OpenAPI-drift test after regenerating `frontend/openapi.json`/`schema.ts`.
- `PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json` then `cd frontend && npm run codegen` -- `test_committed_openapi_json_matches_the_live_schema` failed before this step, passed after.
- `cd frontend && npm run build` -- clean strict TypeScript; `ChartPage` bundles into its own code-split chunk (`ChartPage-*.js`, ~163kB/~53kB gzip -- `lightweight-charts` is the bulk of it), never pulled into the shared entry chunk.
- `cd frontend && npm run test` -- 11/11 passed (2 pre-existing `DocsPage` + 5 pre-existing `RankingsPage` + 4 new `LightweightChart`) + `test:codegen` 4/4.
- `cd frontend && npm run lint` -- exit 0; only the 2 pre-existing Story 15.1 `TrustedHtml.tsx` warnings remain, nothing new.
- Full regression: `PYTHONPATH=. python3 -m pytest data_api/tests ml_signals/tests -q` -- 240 passed, 1 pre-existing unrelated failure (`test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`, tracked since Story 14.3).
- Verified `lightweight-charts@5.2.1` resolves from the real npm registry (`npm view lightweight-charts@5.2.1 version`) and installed it with `npm install --save-exact` -- pinned exactly (`"5.2.1"`, no `^`/`~`).
- Manual smoke test: ran `data_api.app` locally via `uvicorn` against this desktop's real (but stale, last-updated 2026-09-10) local catalog replica and curled `/api/candles/BTC-USD-PERP.DYDX`. Response shape was correct (`{"items": [], "has_more": false}`), but `close_price` is `None` for every `DydxSecondSnapshot` in the queried windows across BTC/ETH/SOL, on this replica -- confirmed via direct `query_second_snapshots` inspection, and confirmed the pre-existing `/catalog/candles` route returns the identical empty result for the same window, so this is a property of this desktop's stale local catalog data (no trades captured in it), not a defect in the new route. `/catalog/snapshots` on the same window returned real bid/ask depth, confirming the catalog itself is otherwise intact.

### Completion Notes List

- **Backend (`routes/candles.py`/`app.py`):** `CandleItem`/`CandlesResponse` are Pydantic models with `o`/`h`/`l`/`c`/`v` as `float | None = None` (real candles: all populated together; gap markers: all `None` together). `CATALOG_PATH` is `routes/candles.py`'s own module-level env constant (`redis_bus.py` precedent), read independently of `app.py`'s. `get_candles()`: clamp `limit` to `_MAX_CANDLES_LIMIT=500`; bound the main query window to `limit * bar_seconds * 3` seconds back from `before_ns` (`_QUERY_WINDOW_MULTIPLIER=3`); call `query_second_snapshots` + `candle_dicts_from_snapshots` unchanged; filter to `t < before_ns` (ms) and keep the most recent `limit`; insert gap markers; probe once for `has_more`. Registered in `app.py` above the `/api/{full_path:path}` catch-all.
- **`_has_more`'s probe subtracts 1ns from its `end` bound** (`earliest_kept_ns - 1`): `ParquetDataCatalog.query()`'s `end` bound is inclusive (verified directly in `nautilus_trader/persistence/catalog/parquet.py` -- both `ts_init >= start`/`<= end` filters are inclusive on both the Rust and PyArrow paths), so probing at an inclusive end exactly at the earliest kept candle's own bucket-open time would re-find that candle's own snapshot every time and report `has_more: True` unconditionally. `test_short_page_with_has_more_true_is_legal` and `test_has_more_false_at_true_history_start` were both constructed specifically to catch this class of off-by-one, and both pass with the `-1` fix.
- **Frontend (`LightweightChart.tsx`/`useCandles.ts`/`ChartPage.tsx`/`client.ts`):** `LightweightChart` owns the one `createChart()` + one `addSeries(CandlestickSeries)` call in a mount-only effect, tearing down via `chart.remove()` on unmount and notifying `onChartApi(null)`; a second effect keyed on `data` calls `series.setData(data)`. `ChartPage` keys `ChartInner` by `iid` so a fresh `LightweightChart`/`useCandles` pair mounts per instrument, rather than resetting one long-lived instance in place or needing render-time state-reset logic inside the hook -- simpler than a `loadedFor`-tracking workaround, given `lightweight-charts` has no supported "re-point this chart" API anyway. `useCandles(instrumentId, chart)` fetches the initial `before_ns=Date.now()*1e6, limit=120, bar_seconds=60` page on mount, then (once `chart` is available) subscribes to `chart.timeScale().subscribeVisibleLogicalRangeChange()`; a refill fires when the visible logical range's `from` comes within `REFILL_MARGIN_BARS=20` of index 0 (mirroring `dashboard.py`'s old `_CANDLE_REFILL_MARGIN_BARS=20`), reusing the exact same `before_ns`/`limit`/`bar_seconds` contract, prepending the response. `hasMoreOlderRef`/`loadingRef`/`earliestMsRef` are refs so the subscription handler always reads current values without resubscribing; once `has_more` is `false`, `hasMoreOlderRef.current` latches `false` and the handler's guard clause stops issuing further requests in that direction. Gap items (`o == null`) convert to native whitespace data (`{time}` only); real candles get a straight ms->seconds unit conversion -- both via the shared `toChartDatum()` function.
- **Codegen:** regenerated `frontend/openapi.json`/`frontend/src/api/schema.ts` after adding `CandleItem`/`CandlesResponse`; `client.ts`'s `fetchCandles()` consumes the generated types; confirmed via the pre-existing OpenAPI-drift test.
- **Tests:** `data_api/tests/test_candles.py` -- 5 tests against a real `ParquetDataCatalog` + real `DydxSecondSnapshot` writes (two sequential disjoint pages; `has_more: false` at true history start via the real probe path; a short page with `has_more: true`, constructed so the probe genuinely finds a snapshot outside the main query window rather than re-finding an in-page one; gap-marker insertion across a real 200s gap with exact-shape assertions; the `limit=100000` clamp boundary against 501 real one-minute-apart candles). All timestamps offset from a large `_BASE_NS` far from the unix epoch, a multiple of `60_000_000_000`, so bucket arithmetic lines up cleanly. `LightweightChart.test.tsx` -- 4 tests against a shallow mock of the `lightweight-charts` module (no `canvas` npm package added): `createChart`/`addSeries` called exactly once per mount, not called again on a data-only re-render, `chart.remove()` called on unmount, `onChartApi` notified with the chart on mount and `null` on unmount.
- **Manual verification against real catalog data was inconclusive for the happy-path shape** (see Debug Log References) because this desktop's local catalog replica has no trade-derived `close_price` in any queried window across the instruments checked -- confirmed as a pre-existing data characteristic shared by the untouched `/catalog/candles` route, not something this story's new route introduced. The automated `test_candles.py` suite (real catalog + real snapshot writes, not mocked) is the authoritative verification for this story's actual logic.
- **Not built in this story (out of scope, per the intent-contract's Never list):** no indicator panes/`chart.addPane()` (Story 15.4); no live/forming-bar edge (Story 15.5); no Lines mode toggle (Story 15.7); no client-side candle aggregation anywhere in the frontend.

### File List

- New: `troll/data_api/routes/candles.py`
- New: `troll/data_api/tests/test_candles.py`
- New: `troll/frontend/src/components/chart/LightweightChart.tsx`, `troll/frontend/src/components/chart/LightweightChart.test.tsx`
- New: `troll/frontend/src/hooks/useCandles.ts`, `troll/frontend/src/hooks/useCandles.test.ts`
- Modified: `troll/data_api/app.py` (wired `candles_routes.router` above the `/api/*` catch-all)
- Modified: `troll/frontend/package.json`, `troll/frontend/package-lock.json` (added pinned `lightweight-charts@5.2.1`)
- Modified: `troll/frontend/src/pages/ChartPage.tsx` (placeholder -> real chart page: `LightweightChart` + `useCandles(iid)`)
- Modified: `troll/frontend/src/api/client.ts` (added `fetchCandles()`, URL-encodes `instrumentId`)
- Modified: `troll/frontend/src/api/schema.ts`, `troll/frontend/openapi.json` (regenerated -- do not hand-edit)
- Not modified: `troll/data_api.dockerfile`, `troll/docker-compose.yml` (no new env var needed), `troll/data_api/app.py`'s existing `/catalog/candles/{iid}` route (untouched), `troll/ml_signals/candles.py`, `troll/ml_signals/catalog_stats.py` (reference only), `App.tsx` (`/chart/:iid` route already wired in Story 15.1)

## Change Log

- 2026-09-15: Story created from Epic 15 / Story 15.3. Status: backlog -> ready-for-dev.
- 2026-09-15: Implemented for real (superseding an earlier fabricated record caught and reset by this pass's Spec Change Log entry). Backend: `GET /api/candles/{instrument_id}` (`routes/candles.py`, own `CATALOG_PATH` constant, `_MAX_CANDLES_LIMIT=500` clamp, bounded main query + one bounded probe query for `has_more`, single-item gap-marker insertion), wired above `app.py`'s `/api/*` catch-all. Frontend: `LightweightChart.tsx` (one `createChart()` + candlestick series, clean mount/unmount via `key={iid}` remounting), `useCandles.ts` (cursor-pagination hook, scroll-back refill with a 20-bar margin, `has_more`-latched stop, whitespace-gap passthrough), a real `ChartPage.tsx`, `client.ts`'s `fetchCandles()` + regenerated OpenAPI types, pinned `lightweight-charts@5.2.1`. Full regression green: `data_api/tests` 26/26, `data_api/tests ml_signals/tests` 240 passed with the same 1 pre-existing unrelated failure tracked since Story 14.3; frontend `npm run build`/`npm run test`/`npm run lint` all clean (lint: same 2 pre-existing Story 15.1 warnings only). Status: in-progress -> review.
- 2026-09-15: Reviewed (Blind Hunter + Edge Case Hunter, parallel). 8 findings patched -- most significantly, `LightweightChart.tsx`'s prepend path did not preserve the visible logical range (would have snapped the view away from the operator on every scroll-back refill, defeating AC #3); also `limit=0`/negative and `bar_seconds<=0` were unclamped, a real data gap straddling a pagination boundary was never marked, `useCandles.ts` had no fetch-error handling, and `useCandles.ts` itself had no dedicated tests despite being the most complex new logic (now covered by 5 new tests). 6 findings rejected as false positives or already-documented tradeoffs (verified empirically, not assumed -- e.g. a malformed `instrument_id` does not raise, confirmed by direct testing; `schema.ts` was in fact codegen-derived, confirmed by the passing drift test). 1 finding deferred (`LightweightChart.tsx`'s `window`-resize-only sizing has no `ResizeObserver`, not reachable via this story's own usage). Full regression re-verified green after patches: `data_api/tests` 28/28, `data_api/tests ml_signals/tests` 242 passed (same 1 pre-existing unrelated failure); frontend `npm run build`/`npm run test`(16/16)/`npm run lint` all clean. Status: in-review -> done.
- 2026-09-15: Follow-up review pass (bmad-dev-auto, `done` -> fresh review re-entry). 2 findings patched: `_MAX_QUERY_SPAN_SECONDS` hard cap added to `_window_start_ns()` (MEM-01 -- previously a large `bar_seconds`/`limit` combination could force a ~4-year raw-snapshot query), plus `LightweightChart.test.tsx` now asserts `CandlestickSeries` is the actual series type passed to `addSeries`. 12 findings rejected -- all either already-documented accepted tradeoffs, already-confirmed non-issues from the prior pass, or negligible/unreachable. Full regression re-verified green: `data_api/tests` 8/8 (candles only)/`data_api/tests ml_signals/tests` 243 passed 1 pre-existing failure (unchanged, tracked since Story 14.3); frontend `npm run build`/`npm run test`(16/16)/`npm run lint` all clean. Status: in-review -> done.

## Auto Run Result

**Summary:** Follow-up review pass on an already-`done` Story 15.3 (`GET /api/candles/{instrument_id}` cursor-paginated candle history + `ChartPage`/`LightweightChart`/`useCandles` frontend). No code re-derivation needed -- ran the Blind Hunter + Edge Case Hunter review pair against the full story diff since baseline, triaged 14 combined findings, patched 2 real ones, rejected 12 as noise or already-settled.

**Files changed this pass:**
- `troll/data_api/routes/candles.py` -- added `_MAX_QUERY_SPAN_SECONDS = 7 * 86_400` hard cap on total query span, applied in `_window_start_ns()` (covers both the main query and the `has_more` probe).
- `troll/data_api/tests/test_candles.py` -- new regression test for the span cap at max `limit`/`bar_seconds`.
- `troll/frontend/src/components/chart/LightweightChart.test.tsx` -- strengthened the single-instance test to assert `CandlestickSeries` was the argument passed to `addSeries`.

**Review findings breakdown:** 2 patched (1 medium: unbounded query span risk; 1 low: weak test assertion), 0 deferred, 12 rejected (already-documented tradeoffs, already-confirmed non-issues from the prior review pass, or negligible/unreachable in practice).

**Follow-up review recommendation:** `false` -- this pass's fixes are two small, localized, low-breadth changes (one constant + cap, one test assertion), not a scope or behavior change warranting another independent pass.

**Verification:** `PYTHONPATH=. python3 -m pytest data_api/tests/test_candles.py -q` -- 8/8 passed. `PYTHONPATH=. python3 -m pytest data_api/tests ml_signals/tests -q` -- 243 passed, 1 pre-existing unrelated failure (`test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`, tracked since Story 14.3, unchanged by this pass). `cd frontend && npm run build` -- clean. `npm run test` -- 16/16 Vitest + 4/4 codegen tests passed. `npm run lint` -- exit 0, same 2 pre-existing Story 15.1 warnings only.

**Residual risks:** None identified beyond what the story's own Design Notes already document as accepted tradeoffs (the `has_more` bounded-probe heuristic's false-negative edge on a real gap wider than the probe window; `LightweightChart`'s window-resize-only sizing, previously deferred).

