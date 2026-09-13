---
baseline_commit: 82dbc7606ee296c823376febaf9235a5128baad2
---

<!-- Standalone bypass-epic story, no epics.md entry -- same precedent as 14.1 (this file's
     sibling in the same bypass epic-14) and epics 5/6/7/9/11 before it. Created, dev'd, and
     self-reviewed in one pass at the user's explicit request for full end-to-end autonomy. -->

# Story 14.2: Async, non-blocking `/chart/{id}` page load with per-panel loading spinners

Status: done

## Story

As a user of the `/chart/{id}` page,
I want the page to appear immediately with the candlestick chart and the imbalance/mid-imbalance/depth/spread panel loading independently (each showing a spinner until its own data arrives), rather than the whole page waiting on a slow catalog read before anything shows,
so that opening a chart feels fast regardless of how much history the page needs to fetch, and I can see the page is working instead of staring at a blank tab.

## Acceptance Criteria

1. **`/chart/{id}`'s HTML response no longer depends on any chart-series computation.** `chart_handler` returns the page shell via `_build_chart_page_html(symbol, start_ms, end_ms, explicit_range)` with no `data` argument and no `compute_chart_series`/data_api chart-series fetch on this path, in both local and `DATA_API_URL` remote mode (previously both modes fetched chart-series data before building HTML at all). Proven by a test that makes `compute_chart_series` raise if called from `_build_chart_page_html`.
2. **The imbalance/mid-imbalance/depth/spread panel ("micro-panel") is now a client-fetched, client-rendered panel**, not a server-rendered Plotly `make_subplots` figure embedded in the initial HTML. A new endpoint, `GET /data/coin/{id}/microfeatures?start=&end=`, returns the same `compute_chart_series` output as JSON (decimated to the same `_CHART_MAX_POINTS=2000`-point cap the old server-side code used, plus a `count` field carrying the true pre-decimation event count) — branching on `DATA_API_URL` exactly like the other three remote-capable endpoints (`/api/rank_history/{id}`, `/data/coin/{id}/lines`, `/history/{id}`).
3. **The page shows a visible loading spinner for both data-bearing panels (`#live-chart`, `#micro-panel`) until each has real data**, not a blank box. Each is wrapped in a `position:relative` div with a sibling `.chart-spinner` (CSS-animated), present in the page's initial static HTML (since nothing blocks the HTML response anymore) and hidden by JS the first time that panel actually renders data — never before.
4. **Both panels fetch concurrently, independently, right after the page shell loads** — `init_script` calls `_loadMicroPanel(_coinIid, start_ms, end_ms)` and either `_loadDefaultCandleWindow()`/`_fetchHistCoin(...)` in the same `<script>` block, with no ordering dependency between them; one panel's fetch/render never blocks or waits on the other's.
5. **Micro-panel's client-side rendering reproduces the retired server-side figure's structure**: 4 stacked rows (imbalance, mid-imbalance, depth, spread) sharing pan/zoom via the x-axis-suffix convention Plotly itself uses (`x`/`xaxis`, `x2`/`xaxis2`, …), same row titles, same colors, the same 0.5 reference line on the imbalance and mid-imbalance rows when those rows have data, and the same y-axis domain split (mirrors the retired `make_subplots(rows=4, row_heights=[0.24,0.24,0.25,0.27], vertical_spacing=0.02)` call). A row with no data in the fetched window contributes no trace and no hline (matches the old server-side `_add`'s `if data.get(series_key):` guard).
6. **Cross-panel pan/zoom sync is preserved.** `micro-panel` remains in `_SYNCED_CHART_IDS`, and `_renderMicroPanel` wires the same `plotly_relayout` → `_onChartRelayout('micro-panel', ev)` listener the old server-rendered version used (`_wireMicroPanelRelayout`, now redundant and removed, since the listener is attached fresh on every `_renderMicroPanel` call instead of once at page load against a static figure) — dragging or zooming any one of `live-chart`/`ind-panel`/`micro-panel` still moves all three's x-axis range together, unchanged from before this story.
7. **No regressions.** `cd troll && python -m pytest ml_signals/tests -q` passes, including: a rewritten `test_dashboard_chart.py` test (the old `test_render_chart_page_figure_row_counts_stay_in_lockstep`, which asserted a now-deleted Python-side `make_subplots` row-count invariant, replaced with one proving the page-render path never touches `compute_chart_series`), new tests for `_microfeatures_json`'s decimation/count behavior, a new `test_dashboard_remote_mode.py` test proving `/data/coin/{id}/microfeatures` is the 4th DATA_API_URL-capable call site (replacing the old chart-page spy test, since `/chart/{id}` itself no longer touches `DATA_API_URL` at all), and a new JS harness test covering `_renderMicroPanel`'s row/axis/hline construction and spinner-hide behavior.

## Tasks / Subtasks

- [x] Task 1 — Remove chart-series data from the page-render path (AC: #1)
  - [x] `_build_chart_page_html` no longer takes a `data` parameter or builds a `make_subplots` figure; it returns the page shell (form, toolbar, empty `#live-chart`/`#micro-panel` divs + spinners, `_LIVE_CHART_JS`, `init_script`) unconditionally.
  - [x] `chart_handler` no longer branches on `DATA_API_URL` at all — it computes `start_ms`/`end_ms`/`explicit_range` and calls `_build_chart_page_html` directly, nothing else.
  - [x] Deleted the now-redundant `_render_chart_page` (its only job — fetch local chart-series data, then call `_build_chart_page_html` — no longer exists).
- [x] Task 2 — New `coin_microfeatures_handler` + `/data/coin/{id}/microfeatures` route (AC: #2)
  - [x] Extracted `_decimate_series`/`_microfeatures_json` (moved verbatim from the old inline `_build_chart_page_html` closures, `_CHART_MAX_POINTS=2000` unchanged) as module-level functions.
  - [x] `coin_microfeatures_handler`: parses `start`/`end` via the existing `_parse_query_ms` (400 if either missing — this endpoint has no "live" branch, matching the old code's always-windowed behavior), branches `DATA_API_URL` (proxies to data_api's existing `/catalog/chart-series/{symbol}` route, same as the code this replaces) vs. local `_chart_data.compute_chart_series` (off-loop via `asyncio.to_thread`, same as before), returns `_microfeatures_json(data)`.
  - [x] Registered `app.router.add_get("/data/coin/{id}/microfeatures", coin_microfeatures_handler)`.
- [x] Task 3 — Spinners (AC: #3, #4)
  - [x] `.chart-spinner` CSS class + `@keyframes chart-spin` added to `_CSS`.
  - [x] `#live-chart` and `#micro-panel` each wrapped in a `position:relative` div with a sibling `#<id>-spinner` div.
  - [x] `_hideSpinner(id)` JS helper; called from `_renderCandleTraces`, `_renderLineChart` (both candlestick-panel render paths), and `_renderMicroPanel`.
  - [x] `init_script` fires `_loadMicroPanel(_coinIid, start_ms, end_ms)` alongside the existing `_fetchHistCoin`/`_loadDefaultCandleWindow()` call — both async, no ordering dependency.
- [x] Task 4 — Client-side micro-panel renderer (AC: #5, #6)
  - [x] `_fetchMicroFeatures`/`_loadMicroPanel`/`_renderMicroPanel` added to `_LIVE_CHART_JS`, mirroring `_renderOscillatorPanel`'s existing "build traces + layout from fetched JSON, `Plotly.react`, re-wire relayout listener" pattern.
  - [x] Row y-axis domains hardcoded to mirror the retired `make_subplots(row_heights=[0.24,0.24,0.25,0.27], vertical_spacing=0.02)` call exactly (computed once by hand, documented in a comment rather than recomputed at runtime — DESIGN-01, these never change).
  - [x] Removed the now-dead `_wireMicroPanelRelayout` (superseded by `_renderMicroPanel`'s own inline wiring, same "re-wire after every `Plotly.react`" pattern `ind-panel`/`live-chart` already use).
- [x] Task 5 — Tests (AC: #7)
  - [x] `test_dashboard_chart.py`: replaced the stale `make_subplots`-row-count test with `test_build_chart_page_html_never_touches_compute_chart_series`; added `test_microfeatures_json_decimates_and_reports_true_pre_decimation_count` and `test_microfeatures_json_under_cap_is_not_decimated`.
  - [x] `test_dashboard_remote_mode.py`: replaced `test_remote_chart_page_uses_data_api_data_matching_local` (spied `_build_chart_page_html`, no longer meaningful since that function takes no data) with `test_remote_microfeatures_matches_local_response` (plain byte-for-byte JSON comparison, same pattern as the existing lines/rank_history remote tests — simpler than the old spy-based test since JSON has no non-deterministic div-id problem); updated `test_data_api_url_unset_never_calls_fetch_json` to also hit the new endpoint; updated docstrings describing the 4 remote call sites.
  - [x] `test_dashboard_chart_pan_js.py`: new `_MICRO_PANEL_HARNESS_TEMPLATE`/`test_micro_panel_renders_client_side_from_fetched_series_and_hides_spinner`, covering row/axis assignment, hline-only-when-data-present, y-domain values, spinner hide-on-render (including the empty-series case), `_loadMicroPanel`'s fetch URL, and `_SYNCED_CHART_IDS` still including `micro-panel`.

## Dev Notes

- **Why this was worth doing as a real architecture change, not a smaller patch:** the old code computed `compute_chart_series` (a full `DydxSecondSnapshot` catalog read for the requested window, potentially several hours of 1s snapshots) synchronously inside the HTTP handler, before returning *any* HTML — so even the candlestick chart above it, which needs none of that data, was stuck behind it. Moving that computation to its own endpoint and fetching it client-side (exactly the same pattern already used for `live-chart`/`ind-panel`/Lines mode) removes that bottleneck from the page-load critical path entirely, not just makes it feel faster.
- **`chart_handler` simplification side-effect:** since neither local nor remote mode needs chart-series data for the page shell anymore, the local/remote branch in `chart_handler` disappears completely — one code path for both modes now, not two. `/chart/{id}` dropped out of the 4 `DATA_API_URL`-capable call sites; `/data/coin/{id}/microfeatures` took its place (still 4 total).
- **Scope deliberately excludes pan-triggered data extension for the micro-panel** (i.e., panning micro-panel/live-chart back past the initially-fetched `[start_ms, end_ms)` window does not fetch more microfeatures data, unlike live-chart's own `_loadOlderChunk`). The x-axis *view* still syncs across all three panels either way (AC #6) — only the *data* stays fixed to the original window. Adding pan-triggered extension for micro-panel would require giving it its own `_chartState`-equivalent buffer/cursor machinery, which is real added complexity beyond what was asked ("moving one moves the others" — already true; the user did not ask for infinite-scroll-back data on the micro-panel specifically). Flagged here explicitly per DESIGN-01/ponytail (skip until asked, not silently declared "solved") rather than silently building it.
- **`_microfeatures_json`/`_decimate_series` are moved, not duplicated** — same decimation constant (`_CHART_MAX_POINTS=2000`, was `_MAX_POINTS` as a local var) and count semantics as the retired inline code, just relocated to module scope so `coin_microfeatures_handler` can call them (SSOT — one implementation, not two).
- **`troll/CLAUDE.md` constraints that apply:** DESIGN-01 (no pan-triggered-extension scaffolding for micro-panel until actually asked for), DESIGN-03 (deleted `_render_chart_page`/`_wireMicroPanelRelayout`/`make_subplots` import rather than leaving them unreachable), TEST-01 (new `_microfeatures_json` does real data shaping, has tests), MEM-01 (unaffected — decimation cap unchanged from before).
- **`make_subplots` import removed** from `dashboard.py` — no longer used anywhere in the file after this story (confirmed via `grep`).
- **Verification limitation (consistent with every prior chart-page story):** not verified in a real browser, no display in this environment. Recommend a manual pass: open `/chart/{id}`, confirm the page renders immediately with both spinners visible, confirm both panels populate within roughly the time their own fetch takes (not gated on each other), and confirm dragging any one of the three panels still moves the other two in lockstep.

### Project Structure Notes

- Modified: `troll/ml_signals/dashboard.py` (removed `_render_chart_page`, `make_subplots` import, `_wireMicroPanelRelayout`; rewrote `_build_chart_page_html`/`chart_handler`; added `_decimate_series`, `_microfeatures_json`, `coin_microfeatures_handler`, route registration, spinner CSS/HTML/JS, `_renderMicroPanel`/`_fetchMicroFeatures`/`_loadMicroPanel`/`_hideSpinner`).
- Modified: `troll/ml_signals/tests/test_dashboard_chart.py`, `troll/ml_signals/tests/test_dashboard_remote_mode.py`, `troll/ml_signals/tests/test_dashboard_chart_pan_js.py`.
- No changes to `dydx_collector/`, `bot_tui/`, `live_paper/`, `ranking_engine/`, `chart_data.py` (the underlying `compute_chart_series` computation itself is untouched — only how/when it's invoked changed).

### References

- [Source: troll/ml_signals/dashboard.py] — full module read and edited this session: `_build_chart_page_html`, `chart_handler`, `_LIVE_CHART_JS` (`_renderOscillatorPanel`/`_renderCandleTraces`/`_syncChartXRange`/`_SYNCED_CHART_IDS`/`_MICRO_PANEL_XAXES` studied as the patterns this story's new code mirrors), `coin_candles_handler`/`coin_lines_handler`/`coin_indicators_handler` (parsing/DATA_API_URL-branch patterns mirrored for the new handler).
- [Source: troll/ml_signals/chart_data.py] — `compute_chart_series`'s signature/return shape, untouched by this story.
- [Source: troll/data_api/app.py:61-63] — `/catalog/chart-series/{symbol}` route, the existing remote-mode data source `coin_microfeatures_handler` proxies to (unchanged).
- [Source: _bmad-output/implementation-artifacts/12-1-*, 12-2-*, spec-12-1-*, spec-12-2-*] — Story 12.2's `DATA_API_URL` remote-mode pattern (`_fetch_json`, `_get_http_session`, the 4-call-site convention) this story's new endpoint follows.
- [Source: _bmad-output/implementation-artifacts/11-1-*] — precedent for a standalone bypass-epic story against a shipped chart-page feature, self-reviewed in one pass.
- [Source: troll/ml_signals/tests/test_dashboard_remote_mode.py] — full file read; rewrote its chart-page-specific test, updated the others' docstrings/lists.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `.venv/bin/ruff check --fix troll/ml_signals/dashboard.py troll/ml_signals/tests/test_dashboard_chart.py troll/ml_signals/tests/test_dashboard_chart_pan_js.py troll/ml_signals/tests/test_dashboard_remote_mode.py` — 22 auto-fixed (import ordering, docstring formatting); 29 remaining, all confirmed pre-existing (PT019 fixture-injection style, S603/S607 subprocess-call style already used by every prior JS-harness test in this file, D401 imperative-mood on an untouched fixture docstring) via `git diff --unified=0` hunk-range cross-check — none in code this story added or changed.
- `.venv/bin/ruff format` applied to the same 4 files.
- `.venv/bin/mypy troll/ml_signals/dashboard.py` — 6 pre-existing errors (lines 1617/1618/1949/2199 in the post-format file), none inside any hunk this story (or 14.1) touches — confirmed via `git diff --unified=0`; these belong to other already-uncommitted work in this working tree (a docs page / ranking_columns.py relabeling), not this story.
- `docker compose run --rm --no-deps collector python3 -m pytest ml_signals/tests -q` — 203 passed, 5 skipped (JS-harness tests, `node` not installed in the collector image).
- All 6 JS harness templates (including the 2 new ones from Stories 14.1/14.2) verified separately via direct `node -e` execution against `_LIVE_CHART_JS` extracted without importing `dashboard.py` (this sandbox's host Python lacks `redis`/`uvicorn`, which only exist inside the Docker image) — all pass.

### Completion Notes List

- `/chart/{id}`'s HTML response no longer depends on `compute_chart_series` in either local or `DATA_API_URL` mode — proven by a test that makes `compute_chart_series` raise if called from the page-render path.
- Micro-panel (imbalance/mid-imbalance/depth/spread) is now a client-fetched JSON panel (`/data/coin/{id}/microfeatures`), client-rendered via a new `_renderMicroPanel`, replacing the old server-side `make_subplots` figure embedded directly in page HTML.
- Both `#live-chart` and `#micro-panel` show a CSS spinner until their first real render; both panels fetch concurrently on page load, no ordering dependency.
- Cross-panel pan/zoom sync (`_SYNCED_CHART_IDS`/`_syncChartXRange`) preserved unchanged — micro-panel still participates, now wired per-render instead of once at page load.
- Pan-triggered data *extension* for the micro-panel (beyond the view-sync already covered) was deliberately scoped out — see Dev Notes.
- Self-review (Blind Hunter/Edge Case Hunter/Acceptance Auditor pass) checked: empty-series micro-panel render (no crash, spinner still hides), concurrent-fetch independence (no shared mutable state between `_loadMicroPanel` and the candlestick fetch path), date-range "Load" form still triggers a full page reload (so micro-panel does get a fresh window on explicit Load, just not on in-place bar/mode changes — unchanged from before this story), and that `onBarChange`/`setCoinMode`/`resetCoinLive` never touched micro-panel before this story either (no regression). No issues found requiring a fix.
- Not verified in a real browser — no display available in this environment, consistent with every prior chart-page story's documented limitation.

### File List

- Modified: `troll/ml_signals/dashboard.py`
- Modified: `troll/ml_signals/tests/test_dashboard_chart.py`
- Modified: `troll/ml_signals/tests/test_dashboard_remote_mode.py`
- Modified: `troll/ml_signals/tests/test_dashboard_chart_pan_js.py`

## Change Log

- 2026-09-13: Moved the chart page's imbalance/mid-imbalance/depth/spread panel off the page-render critical path onto a new client-fetched endpoint (`/data/coin/{id}/microfeatures`), added loading spinners to both data-bearing chart panels, and confirmed cross-panel pan/zoom sync is preserved. `/chart/{id}`'s HTML response no longer depends on any chart-series computation in either local or remote (`DATA_API_URL`) mode. Rewrote the stale Python-side row-count test (its underlying risk — `make_subplots` row/title-length mismatches — no longer exists after this story, since that construction moved to a data-driven JS loop) and the remote-mode test that used to spy on `_build_chart_page_html`'s data argument. Added JS harness coverage for the new client-side panel renderer. Full `ml_signals` test suite passes (203 passed, 5 skipped — node unavailable in the test container; the 5 JS-harness tests including 2 new ones verified separately via direct Node execution, all pass). Self-review pass found no issues requiring a fix. Status: backlog → done.
