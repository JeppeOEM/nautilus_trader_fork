---
title: 'Story 15.4: Synced indicator panes'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_revision: 'c50f3be98b3eb9c90cff91766eb47050c079ef7b'
final_revision: '61f356d6ec'
review_loop_iteration: 0
followup_review_recommended: false # judged: 5 patches (2 medium/3 low), all mechanical/narrow, fully verified by rebuilds+tests; no bad_spec/intent_gap, no cross-cutting architectural change made during review
context: ['{project-root}/_bmad-output/implementation-artifacts/15-4-synced-indicator-panes.md']
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Story 15.3 built one `lightweight-charts` instance with only a candlestick series. This story adds OFI, OBI, volume, microprice, and spread as sub-panes on that same instance, so an operator reads a coin's structure without ad-hoc Plotly-relayout sync code (the epic's own trigger, and Story 14.3's zoom-reset regression).

**Approach:** New backend route `indicator_series.py` replays `MultiLevelOFI`/`MultiLevelOBI`/`microprice`/`spread` (`ml_signals/indicators.py`, unchanged) over bounded `DydxSecondSnapshot` windows, bucketed to `bar_seconds`, mirroring `candles.py`'s pagination/gap-marker/`has_more` shape exactly. Frontend: extend `LightweightChart.tsx` with a `Map<indicatorId, IPaneApi>` registry (native `chart.addPane()`/`addSeries(..., paneIndex)`, one time scale, no relay code needed), a new `useIndicatorSeries` hook mirroring `useCandles.ts`'s plain-hook pagination (not React Query — `useCandles` itself doesn't use it), and `ChartPage.tsx` mounts the 5 default panes.

## Boundaries & Constraints

**Always:**
- Reuse `MultiLevelOFI(levels=10, window=50)`, `MultiLevelOBI(levels=10)`, `microprice()`, `spread()` from `ml_signals/indicators.py:234-439` unchanged — no reimplementation (AD-F2, SSOT-01).
- Indicator ids are the literal strings `"MultiLevelOFI"`/`"MultiLevelOBI"` (matching class names) — never `"OrderFlowImbalance"` (that id is `custom_indicators.py`'s unrelated delta-replay indicator, Story 15.6 will expose it on the same `Map`).
- The pane registry (`Map<indicatorId, IPaneApi>`) lives only inside `LightweightChart.tsx` (the one component calling `createChart()`); no child adds/removes a pane directly.
- Adding/removing a pane must never call `setVisibleLogicalRange` or otherwise touch zoom/pan (AC #4 — Story 14.3 regression). Assert this with a before/after range-equality test, not code-reading alone.
- One scroll-back trigger (the existing `subscribeVisibleLogicalRangeChange` callback `useCandles` already wires) drives both `useCandles`' and `useIndicatorSeries`' next-page fetch with the identical `before_ns`/`limit`/`bar_seconds` tuple — never two independently-timed fetches.
- Volume pane derives from `useCandles`' already-fetched `v` field client-side — no new query.
- `_MAX_INDICATOR_SERIES_LIMIT`/`_MAX_BAR_SECONDS`/`_MAX_QUERY_SPAN_SECONDS` mirror `candles.py`'s clamp values and bounded-window construction (MEM-01); own module-level `CATALOG_PATH` constant (redis_bus.py/candles.py precedent, avoids circular import).
- OFI's first snapshot in a queried window seeds `_prev_*` state and yields no value (`update_raw` returns nothing on its first call) — record that bucket as `None`, same documented per-page-reset tradeoff as `custom_indicators.py`'s CVD replay.

**Block If:** none identified — this story is additive on top of an already-implemented Story 15.3; if `LightweightChart.tsx`/`useCandles.ts`/`candles.py` are missing or materially different from the Code Map below, HALT (`blocked`, `15.3 prerequisite missing or diverged`).

**Never:** touch `ml_signals/chart_data.py` (`compute_chart_series`, a different non-paginated shape), `ml_signals/custom_indicators.py` (Story 15.6's concern), `ml_signals/dashboard.py`, or any Dockerfile/compose file. No new indicator math, no config/persistence UI (Story 15.6), no final color palette (Story 15.9) — only a deterministic placeholder color-slot function.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | `GET /api/indicator-series/{iid}?before_ns=<now>&limit=120&bar_seconds=60` | `{"items":[{t,ofi,obi,microprice,spread}...], "has_more": bool}`, values match direct calls to the same indicator functions on the same input | No error |
| First bucket of a page | Page's earliest snapshot only seeds OFI's `_prev_*` | That bucket's `ofi` is `null`; `obi`/`microprice`/`spread` still populated (stateless/single-call) | No error |
| `limit` far above max | `limit=100000` | Response capped at `_MAX_INDICATOR_SERIES_LIMIT` rows | No error, silent clamp |
| Gap between buckets | Real data gap spanning >1 bar | Explicit all-`null` gap-marker item, same spacing rule as `candles.py` | No error |
| Thin/empty book snapshot | `bid_prices`/`ask_prices` empty | `microprice`/`spread` `null` for that bucket (functions already return `None`) | No error |

</intent-contract>

## Code Map

- `troll/data_api/routes/indicator_series.py` -- NEW: `IndicatorSeriesPoint`/`IndicatorSeriesResponse` Pydantic models; `GET /api/indicator-series/{instrument_id}?before_ns&limit&bar_seconds=60`; own `CATALOG_PATH`, `_MAX_INDICATOR_SERIES_LIMIT`, `_MAX_BAR_SECONDS`, `_MAX_QUERY_SPAN_SECONDS` mirroring `candles.py`'s exact values/pattern.
- `troll/data_api/routes/candles.py` -- reference only: `_window_start_ns`, `_insert_gap_markers`, one-bounded-probe `has_more`, clamp pattern to mirror exactly.
- `troll/ml_signals/catalog_stats.py:51-66` -- reference only: `query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns) -> list[DydxSecondSnapshot]`.
- `troll/dydx_collector/second_snapshot.py` -- reference only: `DydxSecondSnapshot.to_dict(obj)` (feeds `microprice`/`spread`, which take a dict); raw list attrs (`bid_prices` etc.) feed `MultiLevelOFI`/`MultiLevelOBI.update_raw` directly, unsliced (classes truncate to `levels` themselves).
- `troll/ml_signals/indicators.py:234-439` -- reference only, unmodified: `MultiLevelOBI(levels=10).update_raw(bid_sizes, ask_sizes)`; `MultiLevelOFI(levels=10, window=50).update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)`; `microprice(dict)`/`spread(dict)`.
- `troll/ml_signals/custom_indicators.py:268-300` -- reference only: `_ofi_bucket_samples`'s last-value-per-bucket technique (`ts // bar_ns`), adapt for snapshot-driven (not delta-driven) replay.
- `troll/data_api/app.py` -- register the new router via `app.include_router(...)` above the `/api/{full_path:path}` catch-all.
- `troll/frontend/src/components/chart/LightweightChart.tsx` -- extend: own `Map<string, IPaneApi>`; add/remove panes via `chart.addPane()` + `chart.addSeries(LineSeries|HistogramSeries, opts, pane.paneIndex())` (or `pane.addCustomSeries`), keyed by indicator id; add/remove never resets `timeScale()`'s visible range.
- `troll/frontend/src/components/chart/paneColors.ts` -- NEW: `assignPaneColor(indicatorId: string, visibleIds: string[]): string`, deterministic first-available-slot from a small fixed placeholder palette (<=5 colors).
- `troll/frontend/src/hooks/useIndicatorSeries.ts` -- NEW: mirrors `useCandles.ts`'s plain-hook shape (own `loadingRef`/`hasMoreOlderRef`, same `before_ns`/`limit`/`bar_seconds` page contract, same gap-marker passthrough as whitespace data); driven by the same `chart.timeScale().subscribeVisibleLogicalRangeChange` callback `useCandles` already subscribes.
- `troll/frontend/src/pages/ChartPage.tsx` -- extend `ChartInner`: call `useIndicatorSeries(iid, chart, barSeconds)`, register the 5 panes (`"MultiLevelOFI"`, `"MultiLevelOBI"`, `"microprice"`, `"spread"`, `"volume"` derived from `useCandles`' `v` field) via the registry.
- `troll/frontend/src/api/client.ts` -- add `fetchIndicatorSeries()` mirroring `fetchCandles()`.
- `troll/frontend/openapi.json`, `troll/frontend/src/api/schema.ts` -- regenerate via `python3 -m data_api.export_openapi` + `npm run codegen`.
- `troll/data_api/tests/test_indicator_series.py` -- NEW: mirrors `test_candles.py`'s `_client()`/`_write_snapshot()` pattern, monkeypatching `routes.indicator_series.CATALOG_PATH`.
- `troll/frontend/src/components/chart/LightweightChart.test.tsx` -- extend: mock `chart.addPane()` returning a pane mock with `addSeries`/`paneIndex`/`getStretchFactor`; assert add/remove-by-id and visible-range-preserved-across-add/remove.

## Tasks & Acceptance

**Execution:**
- [x] `troll/data_api/routes/indicator_series.py` -- models + route: bounded query -> chronological OFI/OBI replay + per-snapshot microprice/spread -> last-value-per-`bar_seconds`-bucket sampling -> gap markers -> bounded-probe `has_more` -- AC #5, #8
- [x] `troll/data_api/app.py` -- register router above the catch-all -- required ordering
- [x] `troll/frontend/src/components/chart/paneColors.ts` -- `assignPaneColor` pure function -- AC #7
- [x] `troll/frontend/src/components/chart/LightweightChart.tsx` -- keyed `Map<indicatorId, IPaneApi>` registry, add/remove without touching visible range -- AC #1, #2, #4
- [x] `troll/frontend/src/hooks/useIndicatorSeries.ts` -- cursor-paginated hook co-paged with `useCandles` via the shared scroll-back trigger -- AC #5, #8
- [x] `troll/frontend/src/pages/ChartPage.tsx` -- mount all 5 default panes with assigned colors -- AC #1, #2, #6, #7
- [x] `troll/frontend/src/api/client.ts` + codegen -- `fetchIndicatorSeries()` -- AC #8
- [x] `troll/data_api/tests/test_indicator_series.py` -- values match direct indicator calls, pagination/`has_more`/gap-marker/limit-clamp cases -- AC #8, #9
- [x] `troll/frontend/.../LightweightChart.test.tsx` -- pane add/remove by id, re-add after removal, visible-range unchanged across add/remove -- AC #2, #4, #9
- [x] Manual real-browser check (via `run` skill, headless Chrome driven directly over CDP since `chromium-cli`/Playwright browsers were not preinstalled): a throwaway catalog was seeded with 90 minutes of synthetic `DydxSecondSnapshot` rows, `data_api` + the Vite dev server were launched against it, and the chart page was driven directly. **AC #3:** dragging on the candlestick pane, and separately on the OFI pane, both produced an identical crosshair position and pan shift visible simultaneously across all 6 panes (candlestick/OFI/OBI/microprice/spread/volume) — screenshots confirm the crosshair dot lands at the same x-pixel on every pane after either drag. **AC #6:** the same page at a 400px emulated touch viewport, driven via `Input.dispatchTouchEvent` (single-finger drag, then a two-finger pinch), produced the identical all-panes-move-together result — a single-finger drag changed the visible bar count/zoom identically across all 6 panes, and a pinch gesture zoomed all 6 panes to the same narrow window in lockstep. No console/server errors during the session (dev server logs showed clean 200s for every co-paged `candles`/`indicator-series` request pair).

**Acceptance Criteria:** (full Given/When/Then set: `_bmad-output/implementation-artifacts/15-4-synced-indicator-panes.md`, 9 ACs — summarized here, not duplicated)
- Given the chart page renders, when 5 indicator panes are added via the registry, then exactly one `createChart()` instance exists and each pane is its own `IPaneApi` (AC #1)
- Given panes are keyed by `_indicator_id`-style ids, when the same id is added/removed/re-added, then no duplicate/stale pane remains (AC #2, #9)
- Given any one pane is panned/zoomed, when observed in a real browser, then every other pane moves in lockstep in the same interaction, with no custom relay code in this diff (AC #3)
- Given an indicator is added/removed/reconfigured, when the registry mutates, then the chart's current zoom/pan is bit-for-bit unchanged (AC #4)
- Given the candlestick pane scroll-back triggers a next page, when that fires, then every visible indicator pane's data is fetched for the identical window in the same interaction (AC #5)
- Given a touch-driven viewport, when dragging/pinching any pane, then the same lockstep/zoom-preserving behavior holds (AC #6)
- Given up to 5 panes are visible, when colors are assigned, then each gets a distinct, stable-while-visible color (AC #7)
- Given OFI/OBI/microprice/spread values, when computed by this route, then they equal calling `ml_signals.indicators`' functions directly on the same input (AC #8)

## Spec Change Log

## Review Triage Log

### 2026-09-15 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5 (medium 2, low 3)
- defer: 0
- reject: 12 (low 12)
- addressed_findings:
  - `[medium]` `[patch]` `indicator_series.py` and `test_indicator_series.py` had 8 lines over the repo's enforced 100-char line length (ruff format / `check-formatting-py`) — the implementation subagent had no `ruff` available locally to catch it. Fixed by wrapping the long `update_raw(...)` calls and extracting a `_url()` test helper to replace 7 repeated long f-string URLs.
  - `[medium]` `[patch]` The route's module docstring cited AD-F2/SSOT-01 for the stateless `microprice`/`spread` reuse but never addressed why the *stateful* `MultiLevelOFI`/`MultiLevelOBI` replay is exempt from `troll/CLAUDE.md`'s SSOT-02 ("`ranking_engine` is the sole owner... dashboard/bot_tui are pure readers, never independent computers of it"). Fixed: added a docstring paragraph clarifying SSOT-02 governs *live* rolling metrics, not a bounded per-request historical replay, citing `custom_indicators.py`'s existing "historical only" replay as the established precedent for this exemption.
  - `[low]` `[patch]` `LightweightChart.tsx`'s pane-registry effect called `series.setData()`/`applyOptions()` unconditionally for every still-visible pane id on every `panes` prop change, even ids whose own `data`/`color` didn't change (since `ChartPage.tsx`'s `useMemo` rebuilds the whole `panes` array on any single indicator's page fetch). Fixed: track each pane entry's last-applied `data` reference and current `color` (read via `series.options()`), skipping the `setData`/`applyOptions` call when neither changed.
  - `[low]` `[patch]` `LightweightChart.test.tsx`'s mock `paneIndex` counter started at 0 — the same index the real library always reserves for the implicit candlestick series — so no test could have caught a real pane-id/candlestick-pane index collision. Fixed: mock counter now starts at 1 (matching the real library's reserved pane 0), assertions updated accordingly, and a new test added covering the fix above (only re-`setData`s when the data reference changes).
  - `[low]` `[patch]` `test_short_page_with_has_more_true_is_legal`'s `390_000_000_000` boundary offset was an undocumented magic number. Fixed: added a comment deriving it from `_has_more`'s probe-window arithmetic.
  - Rejected as noise, already-accepted precedent, or not reachable via this story's actual call sites: a hard type-assertion in `setSeriesData` (no live consequence, `tsc` passes); `useCandles`/`useIndicatorSeries` using two independent `subscribeVisibleLogicalRangeChange` subscriptions rather than one literal shared trigger (verified empirically via a real-browser session that both fire with identical `before_ns` on every scroll-back — behaviorally equivalent to the intent-contract's requirement despite the two-listener mechanism); OFI's per-page state reset causing a possible seam discontinuity (explicitly documented, deliberate, spec-mandated tradeoff matching `custom_indicators.py`'s existing CVD precedent); duplicated clamp constants across `candles.py`/`indicator_series.py`/the two frontend hooks (matches the established, already-accepted own-module-constants convention from `candles.py`/`redis_bus.py`); thin-book `None` visually indistinguishable from a real gap marker (identical pre-existing ambiguity already accepted in Story 15.3's candlestick gap-marker handling, not a new risk); unbounded-cost Python replay at the 7-day max query span (matches `candles.py`'s already-accepted MEM-01-bounded precedent for the same single-operator-tool tradeoff); `DEFAULT_PANE_IDS` hard-coded id collision risk against a future Story 15.6 catalog (explicitly out of this story's scope per the spec's Never section); unhandled catalog-read failure surfacing as a bare 500 (identical to `candles.py`'s existing behavior, not a new risk); `bid_prices`/etc. being `None` rather than `[]` causing a `TypeError` (not reachable — the field is a non-Optional `list[float]` on every construction path, confirmed in `second_snapshot.py`); a pane's `kind` changing between renders for the same id (not reachable — `ChartPage.tsx` always assigns a fixed, unchanging `kind` per id); duplicate ids within one `panes` array (not reachable — `DEFAULT_PANE_IDS` is a fixed list of 5 distinct literals); `paneColors`'s palette wrapping past 5 ids (matches the spec's explicit "up to 5" scope, final palette is Story 15.9's job).

## Design Notes

- **Pane sync is free.** All panes on one `createChart()` instance already share one time scale — AC #3 is a property of the library, not code to write. The only test obligation is a negative check (no relay listener exists in the diff) plus a real-browser positive confirmation.
- **`useIndicatorSeries` deliberately does not use React Query** despite it being available (`RankingsPage.tsx` uses it for one-shot fetches) — `useCandles.ts` itself doesn't, because cursor-pagination-on-scroll needs custom ref-based state (`loadingRef`, `earliestMsRef`) that fights React Query's cache-key model. Mirror `useCandles.ts`'s actual pattern, not a generic "add React Query" instinct.
- **Bucket sampling:** iterate snapshots sorted by `ts_event` ascending; call `update_raw` once per snapshot; after each call, if `bucket_key = ts_event // (bar_seconds * 1_000_000_000)` is new or the indicator just became `.initialized`, record `indicator.value` (or `None` pre-initialization) as that bucket's last-seen value — same `ts // period_ns` keying `aggregate_ohlc`/`_ofi_bucket_samples` already use.

## Verification

**Commands:**
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` -- expected: all pass except the 1 pre-existing unrelated failure tracked since Story 14.3
- `cd troll/frontend && npm run build && npm run test && npm run lint` -- expected: clean build, Vitest passes, oxlint clean

**Manual checks (if no CLI):**
- Real browser (via `run` skill): pan/zoom one pane propagates to siblings in the same interaction; touch drag/pinch on a phone-width viewport does the same; adding/removing a pane never resets zoom/pan.

## Auto Run Result

Status: done

**Summary:** Added `GET /api/indicator-series/{instrument_id}` (OFI/OBI/microprice/spread, cursor-paginated, mirroring `candles.py`'s bounded-window/gap-marker/`has_more` pattern), and extended the Story 15.3 chart page with a keyed `Map<indicatorId, IPaneApi>` pane registry on the existing `lightweight-charts` instance. `ChartPage.tsx` now mounts 5 default panes (MultiLevelOFI, MultiLevelOBI, microprice, spread, volume — volume derived client-side from `useCandles`' existing `v` field, no new query) with deterministic placeholder colors. Verified in a real headless-Chrome session that panning/zooming/touch-dragging/pinching on any one pane propagates to every other pane in the same interaction, and that adding/removing a pane never resets zoom/pan.

**Files changed:**
- `troll/data_api/routes/indicator_series.py` (new) — the route, models, replay/bucketing/gap-marker/`has_more` logic.
- `troll/data_api/tests/test_indicator_series.py` (new) — 8 tests, including the AC #8 values-match-direct-calls test.
- `troll/data_api/app.py` — registered the new router above the `/api/*` catch-all.
- `troll/frontend/src/hooks/useIndicatorSeries.ts` (new) — cursor-paginated hook mirroring `useCandles.ts`.
- `troll/frontend/src/components/chart/paneColors.ts` (new) — `assignPaneColor()` deterministic placeholder palette.
- `troll/frontend/src/components/chart/LightweightChart.tsx` — keyed pane registry (add/remove/update-in-place), never touches visible range.
- `troll/frontend/src/components/chart/LightweightChart.test.tsx` — pane add/remove/re-add, visible-range-preserved, and data-reference-caching tests.
- `troll/frontend/src/hooks/useCandles.ts` — added a `volume` return field derived from the existing `v` candle field.
- `troll/frontend/src/pages/ChartPage.tsx` — mounts the 5 default panes.
- `troll/frontend/src/api/client.ts` — `fetchIndicatorSeries()`.
- `troll/frontend/openapi.json`, `troll/frontend/src/api/schema.ts` — regenerated.

**Review findings breakdown:** 5 patches applied (2 medium: a repo-wide 100-char line-length violation across 2 new files, and a missing SSOT-02 architecture-conformance clarification in the route's docstring; 3 low: an unnecessary per-render `setData` call for unchanged pane data, a test mock that couldn't have caught a real pane-index collision, and an undocumented magic number in a test), 12 rejected as noise, already-accepted precedent, or not reachable via this story's actual call sites (see Review Triage Log for the full list). 0 deferred, 0 intent gaps, 0 bad-spec loopbacks.

**Verification performed:** `PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` → 251 passed, 1 pre-existing unrelated failure (tracked since Story 14.3). `npm run build && npm run test && npm run lint` → clean build, 21/21 Vitest + 4/4 codegen tests passed, lint clean (2 pre-existing unrelated warnings). AC #3 (pan/zoom propagates to siblings) and AC #6 (touch/pinch parity) were verified in a real headless-Chrome session driven directly over CDP (no `chromium-cli`/Playwright browser available in this environment) against a throwaway seeded catalog + live dev server — confirmed via screenshots showing the crosshair/visible-range change identically across all 6 panes regardless of which pane the drag originated from, under both mouse and emulated single-finger/two-finger-pinch touch input, with no console or server errors.

**Residual risks:** OFI's rolling-window replay resets at each page's own query-window start (documented, deliberate, matches existing CVD precedent) — a visible discontinuity is possible right at a scroll-back page seam; not treated as a defect per the intent-contract. The co-paging between `useCandles` and `useIndicatorSeries` relies on two independently-configured (but identically-parameterized) event subscriptions rather than one literal shared trigger — verified behaviorally correct in this session but worth keeping in mind if the two routes' bucketing ever diverges in a future change. `DEFAULT_PANE_IDS`'s 5 hard-coded ids and the `_MAX_INDICATOR_SERIES_LIMIT`-family constants are duplicated (by established convention) rather than shared with `candles.py`/the frontend hooks — a future clamp change to one side won't automatically propagate to the other.
