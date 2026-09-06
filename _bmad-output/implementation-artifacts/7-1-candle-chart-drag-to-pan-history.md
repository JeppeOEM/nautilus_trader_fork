---
baseline_commit: 944891bbbafa9a2869d7b988910b478dcf647576
---

# Story 7.1: Candle chart drag-to-pan history with tick-level zoom

Status: done

<!-- No epics.md entry exists for this story -- standalone feature story, created by explicit
     user request (bypassing epic/PRD ceremony), same precedent as Stories 5.1 and 6.1. Filed
     as epic-7 since it's unrelated to epic-5 (crossed-book investigation) and epic-6 (collector
     instrument control). Design was worked out interactively with the user across two rounds
     of plan-mode revision -- see Dev Notes for the resulting decisions, especially the
     charting-library choice (user explicitly rejected TradingView Lightweight Charts in favor
     of staying on Plotly with hand-rolled incremental loading). -->

## Story

As a user of the dYdX web dashboard's coin chart,
I want to click-drag the candlestick chart to pan back through history (with older candles streaming in smoothly, no page reload) and zoom in past 1-minute candles down to individual trade prints,
so that browsing a coin's full recorded history feels like a professional charting site (e.g. TradingView) instead of manually typing date ranges into a form and re-clicking "Load" for every window I want to see.

## Acceptance Criteria

1. **Drag pans, scroll zooms.** On the coin chart (`/coin/{iid}`, `live-chart` div), a left-click-drag moves the visible time window (pan) instead of Plotly's default box-zoom-select; the scroll wheel zooms in/out. (`dragmode:'pan'` + `config:{scrollZoom:true}` on the `live-chart` `Plotly.react`/`newPlot` call.)
2. **Panning back loads older candles smoothly.** When the user drags/zooms such that the visible left edge nears the earliest currently-loaded candle, the client fetches the next older chunk from the existing `/data/coin/{id}/candles?start=&end=&bar=` endpoint (`_historical_candles_json`, dashboard.py:762 — unchanged), prepends it to the currently-held candle array, and re-renders via `Plotly.react` without moving or resetting the user's current view (no jump, no full-page reload, no flash back to the live default window).
3. **First drag/zoom freezes live polling.** The first `plotly_relayout` event on `live-chart` that represents genuine user interaction (not our own `Plotly.react` calls, which don't emit this event) stops the 1s poll `setInterval` from `showCoin()` (dashboard.py:490) — same effect as today's "Load" button — so the indicator table, signal chart, and candle trace all stop being overwritten by live polling while the user is browsing history. The existing "Live" button (`resetCoinLive()`, dashboard.py:346) still returns to the real-time view and must also reset the new pan/pagination state back to the live default window.
4. **Reaching the start of history is visible, not silent.** A backward fetch that returns zero candles marks that (iid, bar_seconds) combination as exhausted-left (no further backward fetches fire on subsequent pans) and shows "Reached start of history" via the existing `setStatus()` helper.
5. **Changing timeframe resets pagination state.** Selecting a different `bar-sel` value (`onBarChange()`, dashboard.py:332) discards any currently-loaded pan-extended candle data and starts a fresh initial window at the new resolution — stale candles from a different bar size must never linger in the trace.
6. **Wider timeframe range.** `bar-sel` (dashboard.py ~476-477, currently 30s/1m/5m/15m/1h) gains `5s`, `15s` below 30s and `4h`, `1d`, `1w` above 1h. No backend change — `bar_seconds` is already a free-form int through `_historical_candles_json`/`build_candles()`.
7. **Tick-level "Ticks" mode.** A third toolbar button next to existing "Lines"/"Candles" (`setCoinMode`, dashboard.py:326; toolbar buttons dashboard.py ~473-474): "Ticks". When active, the chart renders individual trade prints (price vs time, `scattergl` trace, colored by aggressor side — buy vs sell) instead of OHLC bars, backed by a new `GET /data/coin/{id}/ticks?start=&end=` endpoint. Ticks mode uses the *same* drag-to-load-more pagination mechanism as Candles mode (shared fetch/prepend/exhaustion logic — only the fetch URL and response-row parser differ per mode).
8. **Tick endpoint is bounded.** `_historical_ticks_json(iid, start_ms, end_ms)` caps returned rows (e.g. 20,000) rather than returning unbounded results for an accidentally-huge window — a defensive cap, not expected to trigger in normal (zoomed-in) usage.
9. **No regressions.** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q` passes, including the two new tests below and the two pre-existing `_live_candles_json` tests (`test_live_candles_drops_partial_leading_bucket`, `test_live_candles_keeps_only_bucket_when_alone`) added earlier in this same working tree.
10. **Test gap closed.** Neither `_historical_candles_json` nor the catalog trade-tick read path had any test before this story (confirmed by direct inspection — only `_live_candles_json`, the buffer-based live path, was tested). Add:
    - `test_historical_candles_json_builds_from_catalog_trades` — temp `ParquetDataCatalog` + known `TradeTick`s, assert returned candle OHLC values are correct.
    - `test_historical_ticks_json_returns_raw_trades` — same setup, assert returned tick rows match the known trades (price/size/side/order) and that the row cap is respected.

## Tasks / Subtasks

- [x] Task 1 — Tick-level backend endpoint (AC: #7, #8, #10)
  - [x] Add `_historical_ticks_json(iid: str, start_ms: int, end_ms: int, max_rows: int = 20_000) -> str` in dashboard.py next to `_historical_candles_json` (dashboard.py:762) — same lazy-import `ParquetDataCatalog` pattern, `catalog.trade_ticks(instrument_ids=[iid], start=start_ns, end=end_ns)`, map each `TradeTick` to `{"t": ts_event//1_000_000, "price": t.price.as_double(), "size": t.size.as_double(), "side": t.aggressor_side.name}`, truncate to `max_rows`.
  - [x] Add `coin_ticks_handler` mirroring `coin_candles_handler` (dashboard.py:1014) — parse `start`/`end` query params the same way, run `_historical_ticks_json` via `asyncio.to_thread`.
  - [x] Register `app.router.add_get("/data/coin/{id}/ticks", coin_ticks_handler)` next to the existing candles route (dashboard.py:1200).
  - [x] Tests in `ml_signals/tests/test_dashboard_chart.py`: reuse the `_catalog_with_trades`-style helper pattern from `ml_signals/tests/test_timeframe_backtest.py:70-82` (temp dir + `ParquetDataCatalog(tmp).write_data([...])`), monkeypatch `ml_signals.dashboard.CATALOG_PATH` to the temp dir, cover both `_historical_candles_json` (currently untested — AC #10) and `_historical_ticks_json`.

- [x] Task 2 — Chart interaction config: drag-to-pan, scroll-to-zoom (AC: #1)
  - [x] `_renderCandleChart` and the new `_renderTickChart` both pass `dragmode:'pan'` in the layout object and `{scrollZoom:true}` as the `Plotly.react` 4th (config) argument.

- [x] Task 3 — Client-side chart state + pan-triggered pagination (AC: #2, #3, #4, #5)
  - [x] `_chartState = {iid, mode, barSeconds, rows, cursorStart, exhaustedLeft, loading}` — rebuilt by `_setChartRows()` on every fresh fetch (live poll, Load button, mode/bar change all call it or null the state out first).
  - [x] `_loadOlderChunk()` — mode-generic via `_fetchModeWindow(mode,...)` (dispatches to `_fetchCandlesWindow`/`_fetchTicksWindow`); empty result sets `exhaustedLeft=true` + `setStatus('Reached start of history')`; otherwise prepends and re-renders via `Plotly.react` (never `Plotly.relayout`, confirmed empirically via a scratch node harness — see Debug Log).
  - [x] `_onChartRelayout`/`_relayoutXRange`/`_maybeLoadOlder` wired via `_wireChartRelayout()` (called from both `_renderCandleChart` and `_renderTickChart`, mirroring the existing `plotly_click` `removeAllListeners`-then-`.on` pattern). Guards on `xaxis.range[0]`/`xaxis.range[1]`/`xaxis.range` presence so resize/autosize/legend-click relayouts are ignored; 200ms debounce before triggering a fetch; first qualifying event sets `_coinHistStart='panning'` + `clearInterval(timer)` + greys the Live button, exactly mirroring `loadCoinDateRange()`'s existing freeze.
  - [x] `resetCoinLive()`: `_chartState=null`. `onBarChange()`: `_chartState=null`.
  - [x] Race guard: `renderCoin`'s live-poll candle/ticks fetch callbacks check `if(_coinHistStart)return;` before calling `_setChartRows`, so an in-flight live-window fetch started just before the user's first drag can't clobber the pan session after the freeze takes effect.

- [x] Task 4 — Ticks mode (AC: #7)
  - [x] "Ticks" toolbar button + 3-way `_updateModeButtons`.
  - [x] `_renderTickChart`: `scattergl`, marker color by `side` (`BUYER`→green, `SELLER`→red).
  - [x] Wired through the same `_fetchModeWindow`/`_setChartRows`/`_loadOlderChunk` path as Candles — `renderCoin`'s ticks-mode live branch uses a fixed 30-minute default window (`_fetchTicksWindow`), and `_fetchHistCoin` (Load button) now dispatches by `_coinMode` too.

- [x] Task 5 — Wider timeframe options (AC: #6)
  - [x] `bar-sel` gained `5s`/`15s` below 30s and `4h`/`1d`/`1w` above 1h.

## Dev Notes

- **Charting library decision (explicit user call, don't revisit without asking).** Plan-mode session offered TradingView's own Lightweight Charts library (new CDN dependency, native smooth pan/zoom + built-in "load more" event) as the recommended option; user chose to **keep Plotly** (already the only chart dependency here, loaded via `<script src="https://cdn.plot.ly/...">` at dashboard.py:180) and hand-roll the incremental-loading behavior instead. Do not introduce a new charting library for this story.

- **This story's UX is entirely new; the underlying data-fetch endpoints mostly are not.** `_historical_candles_json` (dashboard.py:762) already takes an arbitrary bounded `[start_ms, end_ms]` and is already used by the existing "Load" button flow (`loadCoinDateRange` → `_fetchHistCoin`, dashboard.py:337-359) — this story does not change that function's signature or behavior, it just calls it repeatedly with sliding windows instead of once with a fixed range. Reuse it as-is; do not write a second candle-building code path.

- **Why no "fetch full history in one shot" endpoint.** An earlier draft of this plan considered adding a `/data/coin/{id}/range` endpoint (via Nautilus's `catalog.query_first_timestamp`/`query_last_timestamp`, which are cheap/metadata-only — no data read) plus a "Max" button to jump straight to the full stored range. Dropped in favor of pure drag-driven pagination once the interaction model shifted to continuous pan: you reach the start of history by scrolling to it, same as any real charting site, and the empty-fetch signal (AC #4) already communicates that. If a future story wants a "zoom to fit all data" shortcut, `query_first_timestamp`/`query_last_timestamp` (`nautilus_trader/persistence/catalog/parquet.py:2233,2248`) are the right tool for it — no custom directory/filename parsing needed, Nautilus's own catalog already tracks this via its file-naming scheme.

- **Catalog size context (why no pre-aggregation pipeline).** Per-instrument `TradeTick` history in the catalog is small today (~9MB / ~1160 parquet files for ETH-USD-PERP.DYDX, the busiest instrument) — reading a bounded time slice, even a fairly wide one, is cheap. This story does not build a pre-aggregated `Bar`-storage pipeline. `ponytail:` if a single instrument's tick history ever gets slow to query in one shot (years out, as the collector keeps running), the upgrade path is pre-aggregated `Bar` storage via Nautilus's own `Bar`/`BarType` + `catalog.write_data()` — not needed at current catalog size.

- **Interaction with the existing live-poll loop — the easiest thing to silently break in this story.** `showCoin()` (dashboard.py:464-491) starts `timer = setInterval(function(){if(!_coinHistStart)pollCoin(iid);},1000)`. `pollCoin` refreshes indicators, the signal chart, AND (inside `renderCoin`, dashboard.py:530-535) re-fetches and fully replaces the candle trace every second while in Candles mode and `_coinHistStart` is falsy. If pan-triggered history browsing doesn't also gate on (or repurpose) this same freeze mechanism, the very next 1s poll tick will overwrite the user's pan-loaded, drag-extended candle array with a fresh fetch of the live default window — silently discarding everything the user just scrolled back to see. Reuse the existing freeze primitive (stopping/gating the same `timer`) rather than inventing a second, parallel "are we live" flag that the poll loop doesn't know about.

- **`plotly_relayout` does not fire from our own updates.** Confirmed via Plotly's event model: `plotly_relayout` fires on user-driven pan/zoom/box-select and on explicit `Plotly.relayout()` calls, but NOT on `Plotly.react()` (full redraw) or `Plotly.restyle()` (data-only update) — so using `Plotly.react`/`Plotly.restyle` exclusively for our own prepend-and-redraw updates (never `Plotly.relayout`) avoids a self-triggered feedback loop on the same handler. Verify this empirically against the pinned Plotly version (`plotly-2.35.2.min.js`, dashboard.py:180) during implementation rather than assuming — the plan's design depends on it.

- **Existing "Load" (date-range) flow is untouched.** `loadCoinDateRange`/`_fetchHistCoin`/the `coin-start`/`coin-end` date pickers (dashboard.py:335-359) still work exactly as today, as an alternate way to jump to a specific window — this story adds drag-driven browsing *from wherever the chart currently is*, it doesn't replace the manual date-range entry point.

- **Regression baseline includes an uncommitted fix already in this working tree.** Earlier in this session, `_live_candles_json` (dashboard.py:733) was fixed to drop its unstable partial leading bucket, and `_second_rolling`/`_ind_rolling`'s buffer (dashboard.py:108,114) was bumped from `maxlen=300` to `maxlen=3600`; two tests (`test_live_candles_drops_partial_leading_bucket`, `test_live_candles_keeps_only_bucket_when_alone`) were added to `test_dashboard_chart.py`. These changes are uncommitted but already present in the working tree — do not reintroduce the old buggy behavior and do not duplicate those tests.

### Project Structure Notes

- Single file touched for implementation: `troll/ml_signals/dashboard.py` (backend handler + inline JS/HTML template in the same file, per this codebase's existing convention — there is no separate frontend build step).
- Tests: `troll/ml_signals/tests/test_dashboard_chart.py` (existing file, add to it — matches its current scope of "unit tests for the coin chart JSON-building functions").
- No changes to `troll/dydx_collector/`, `troll/bot_tui/`, `troll/live_paper/`, or `troll/ranking_engine/` — this story is entirely within `ml_signals/dashboard.py`'s coin-chart surface.

### References

- [Source: troll/ml_signals/dashboard.py:105-114] — `_second_rolling`/`_ind_rolling` rolling buffers (already fixed this session, see Dev Notes)
- [Source: troll/ml_signals/dashboard.py:180] — Plotly CDN script tag, pinned version 2.35.2
- [Source: troll/ml_signals/dashboard.py:326-375] — `setCoinMode`, `onBarChange`, `loadCoinDateRange`, `resetCoinLive`, `_fetchHistCoin`, `_renderCandleChart` (all read in full)
- [Source: troll/ml_signals/dashboard.py:464-560] — `showCoin`, `pollCoin`, `renderCoin` (all read in full, including the live-mode candle-refresh branch at 530-535 and the existing `plotly_click` wiring at 559-560 being mirrored for the new `plotly_relayout` handler)
- [Source: troll/ml_signals/dashboard.py:733-769] — `_live_candles_json`, `_historical_candles_json` (candle-building; reused unmodified for the new pagination flow)
- [Source: troll/ml_signals/dashboard.py:1014-1034] — `coin_candles_handler` (pattern to mirror for `coin_ticks_handler`)
- [Source: troll/ml_signals/dashboard.py:1193-1204] — route registration block
- [Source: troll/ml_signals/candles.py] — `build_candles()`/`Candle`, reused unmodified
- [Source: troll/ml_signals/tests/test_dashboard_chart.py] — existing test file and its `_snap`/`_reset` helper conventions to follow for style consistency
- [Source: troll/ml_signals/tests/test_timeframe_backtest.py:70-82] — `_catalog_with_trades`-style temp-catalog-with-real-`TradeTick`s helper pattern to reuse for the new catalog-backed tests
- [Source: nautilus_trader/persistence/catalog/parquet.py:2233,2248] — `query_first_timestamp`/`query_last_timestamp` (considered, not used this story — see Dev Notes "no full-history endpoint")
- [Source: troll/CLAUDE.md#Memory Discipline, #Testing] — MEM-01 (time-bounded catalog queries only — AC #8's row cap on ticks is this story's application of it), TEST-01 (catalog-touching paths require tests — motivates Task 1's test additions)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `_loadOlderChunk` initially didn't `return` its internal fetch-promise chain; caught by a scratch
  node harness (see Completion Notes) that `await`ed it expecting completion — fixed by adding
  `return` before the `_fetchModeWindow(...)` call (dashboard.py). Harmless for the app's own
  fire-and-forget callers, but needed for the promise chain to be awaitable/testable at all.
- Full regression suite showed 23 failures on first run; root-caused (not part of this story):
  (a) `tomli_w` / `pytest-asyncio==0.23.8` were declared in `troll-requirements.txt` (added by
  Story 6.1's in-progress work, running concurrently in this same working tree) but not installed
  in this environment — installed both, which cleared 22 of the 23 failures with no code change;
  (b) the remaining `ml_signals/tests/test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure`
  failure was confirmed via `git stash` (reverting to this story's `baseline_commit`) to fail
  identically before any of this story's changes — pre-existing, matches the documented
  BacktestEngine-construction fragility already tracked as an open Epic 2 action item in
  sprint-status.yaml, unrelated to `dashboard.py`.

### Completion Notes List

- Implemented all 5 tasks: tick-level backend endpoint (`_historical_ticks_json`/`coin_ticks_handler`),
  Plotly `dragmode:'pan'`+`scrollZoom` config, shared candle/tick pan-to-load-more pagination
  (`_chartState`/`_setChartRows`/`_loadOlderChunk`/`_onChartRelayout`), a new "Ticks" mode, and
  wider `bar-sel` timeframe options (5s–1w).
- Python side verified by test: `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q`
  → 29 passed (4 new: 2 catalog-backed candle/tick tests closing the pre-existing AC#10 gap, 1 row-cap
  test, plus the 2 `_live_candles_json` tests from the earlier same-session bugfix already counted
  in that total). Full-repo suite: 565 passed, 1 pre-existing unrelated failure (see Debug Log).
- **JS verification method (no browser/display available in this environment):** (1) extracted the
  dashboard's inline `<script>` block and ran it through `node --check` to confirm valid syntax;
  (2) wrapped the extracted script in a `new Function(...)` harness (stubbing `document`/`window`/
  `Plotly`/`fetch`) to directly exercise `_relayoutXRange`, `_chunkSpanMs`, `_setChartRows`,
  `_loadOlderChunk`, and `_maybeLoadOlder` against representative inputs — confirmed: pan-triggered
  fetches prepend and shift the cursor correctly, an empty response sets `exhaustedLeft` and stops
  further backward fetches, and `_maybeLoadOlder` only fires within the trigger margin of the loaded
  left edge. This is a one-off scratch harness (not committed — no JS test framework/precedent exists
  in this codebase; Python remains the only tested layer per project convention), so it is not a
  substitute for interactively dragging the real chart in a browser, which was **not** performed and
  should be done before/while marking this story fully verified end-to-end.
- **One design point relies on documented-but-unverified-in-browser Plotly behavior:** AC #2's "no
  jump" requirement depends on `Plotly.react()` preserving the user's current zoom/pan across a
  redraw when the layout object doesn't specify an explicit `xaxis.range` (a documented `react()`
  vs `newPlot()` behavior difference) — every render call here passes the same range-less layout
  shape by design, but this should be visually confirmed against the pinned Plotly 2.35.2 the first
  time this runs in a real browser.
- Ticks mode's "live" (unfrozen) view uses a fixed 30-minute default window, refreshed every 1s poll
  tick like Candles — not specified explicitly by an AC, chosen for symmetry with existing Candles
  live behavior; flag if a different default window is preferred.

### File List

- Modified: `troll/ml_signals/dashboard.py`
- Modified: `troll/ml_signals/tests/test_dashboard_chart.py`
- Added: `troll/ml_signals/tests/test_dashboard_chart_pan_js.py` (review round)

### Review Findings

- [x] [Review][Decision→Patch] AC #1 pan/scroll-zoom not wired for Lines mode — **Resolved: extend to Lines too** ("uniform way to zoom and drag"). Fixed: Lines-mode `Plotly.react` now passes `dragmode:'pan'`+`{scrollZoom:true}` and calls `_wireChartRelayout()` [dashboard.py renderCoin, Lines branch].
- [x] [Review][Decision→Defer] AC #2's "Plotly.react preserves zoom on redraw" is unverified in a real browser — **Resolved: deferred to the user**, who will verify directly in a real browser (drag the chart, confirm no view jump) before treating AC #2 as fully confirmed end-to-end. No code change; see `deferred-work.md`.
- [x] [Review][Decision→Patch] Live (mid-price) and historical (trade-price) candles concatenated with no seam handling — **Resolved: fix it.** Added `_reconcilePriceBasisOnFirstPan()`, called from `_onChartRelayout` on the first pan of a live (non-Load) Candles session: refetches the currently-displayed window via `_historical_candles_json`/`_fetchCandlesWindow` (trade-price basis) before any older-chunk prepend, so the whole series is consistent going forward [dashboard.py].
- [x] [Review][Decision→Patch] Zero test coverage for the pan/pagination state machine — **Resolved: add a minimal JS test harness.** New `troll/ml_signals/tests/test_dashboard_chart_pan_js.py`: extracts the real inline `<script>` block, stubs `document`/`Plotly`/`fetch`, runs a plain-`assert` Node harness (no framework) covering the request-generation guard, exhaustion, truncated-cursor handling, and the `_coinPanning` sentinel fix. Skips cleanly if `node` isn't installed.
- [x] [Review][Patch] `_coinHistStart='panning'` sentinel breaks bar/mode change while frozen from a drag [dashboard.py:421] — Fixed: introduced a dedicated `_coinPanning` boolean (never touches `_coinHistStart`); `onBarChange()`/`setCoinMode()` now call `_refreshPanningWindow()` (loads a fresh default window via `_fetchLiveWindow`) when panning-frozen with no explicit Load range, instead of feeding a bogus sentinel into `_fetchHistCoin`.
- [x] [Review][Patch] `_loadOlderChunk` has no request-generation guard [dashboard.py:394-408] — Fixed: captures `_chartState` into a local `state` at call time and checks `_chartState!==state` before mutating/rendering on fetch resolution, so a superseding coin/mode/bar switch can no longer have a stale fetch clobber it. Covered by the new JS harness test.
- [x] [Review][Patch] `_historical_ticks_json` materializes the full unbounded catalog result before applying `max_rows` [dashboard.py:883-904] — Fixed: added `_MAX_TICK_WINDOW_NS` (6h) server-side clamp applied to `[start_ns,end_ns]` *before* the `catalog.trade_ticks()` call, plus a `truncated` flag (window-clamped or row-capped) in the response. New tests: `test_historical_ticks_json_clamps_wide_window`, `test_historical_ticks_json_not_truncated_under_cap`.
- [x] [Review][Patch] `_chunkSpanMs()`'s candle formula blows up for the newly-added 1d/1w bar options [dashboard.py:391-393] — Fixed: capped at `_MAX_CHUNK_MS` (30 days). Covered by the new JS harness test (`span1w <= _MAX_CHUNK_MS`).
- [x] [Review][Patch] Tick row-cap truncation silently drops trades and the cursor still advances past the clamped edge [dashboard.py:883-904, 394-408] — Fixed: `_loadOlderChunk` now advances `cursorStart` to `rows[0].t` (the oldest row actually returned) instead of the full requested `newStart` whenever the response is `truncated`, so no data is silently skipped. Covered by the new JS harness test.
- [x] [Review][Patch] `plotly_relayout` listener never detached when switching back to Lines mode [dashboard.py:657-663] — Resolved as part of the AC #1 fix above: Lines mode now actively wires `_wireChartRelayout()` (which always tears down and re-adds the listener) rather than leaking a stale one.
- [x] [Review][Patch] `_parse_ms` duplicated verbatim across `coin_candles_handler`/`coin_ticks_handler` [dashboard.py:1146-1153, 1169-1176] — Fixed: extracted to a single module-level `_parse_query_ms(qs, key)`.
- [x] [Review][Patch] Tick coloring defaults anything other than `'BUYER'` (including `NO_AGGRESSOR`) to sell-red [dashboard.py:463] — Fixed: 3-way color map (`BUYER`→green, `SELLER`→red, anything else→neutral grey).
- [x] [Review][Patch] Live-candles fetch chain in `renderCoin` has no `.catch`, unlike the sibling ticks fetch added right next to it [dashboard.py:632-634] — Fixed as part of unifying the candles/ticks live-fetch branches into one `_fetchLiveWindow(...).then(...).catch(...)` call.
- [x] [Review][Defer] `_second_rolling`/`_ind_rolling` maxlen bump (300→3600) and oldest-bucket-drop in `_live_candles_json` [dashboard.py:110,116,855-856] — deferred, pre-existing: per this story's own Dev Notes, both changes predate story 7.1's work (bundled into the same uncommitted working tree from an earlier same-session fix, already covered by its own tests).
- [x] [Review][Defer] `size` field returned by `_historical_ticks_json` but unused by `_renderTickChart` [dashboard.py:899] — deferred, low value: harmless unused payload field, plausible future use (tooltip/marker sizing), not worth a diff on its own.
