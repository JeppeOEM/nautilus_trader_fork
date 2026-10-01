---
title: 'Story 32.6: A coin''s chart comes back exactly as it was left, and a new coin opens with your default setup'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '4386b0fe9681a2be3f0fbcb25c69f7462c5c5279'
final_revision: '827d05c2011c045c4572b1e3793d9958d2634182'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** A coin's chart setup is scattered: timeframe and the volume toggle in `localStorage`, Candles/Lines mode, crosshair, dragged pane heights, zoom and the volume-profile settings in React state that dies on navigation, indicators and drawings on the server. Nothing restores the chart as it was, and a first-opened coin starts empty.

**Approach:** One server-side layout file per instrument (`chart_layouts.toml` in Story 32.5's preferences directory) behind `GET`/`PUT /api/coin/{instrument_id}/layout`, one `useChartLayout(iid)` hook that saves every listed field debounced and restores them before the first fetch, and a `[default]` template that seeds a coin opened for the first time, with "Save as default" and "Reset to default" in a small Layout menu.

## Boundaries & Constraints

**Always:**
- **Fields** per instrument table: `bar_seconds`, `mode` (`candles`|`lines`), `volume` (bool), `crosshair` (bool), `pane_heights` (pane id → px, only panes the operator dragged), `visible_bars` (bars on screen, restored as the zoom while scrolling to the latest bar; never the absolute scroll position), volume-profile settings (kind, rows, value-area %, session, HD flag, the fixed range's anchors; Story 32.7's auto-anchored/TPO settings and Story 32.8's footprint on/off and settings join this table when they land). `v = 1` and a `[default]` table with the same fields plus `default_indicators` (an array of indicator entries).
- **Validation.** The PUT route validates every key's allowed values and returns 422 naming the bad key. A saved `bar_seconds` not in `TIMEFRAMES` or an unknown `mode` falls back to the built-in default for that field with one `console.error` (visible in the ErrorBar), never a blank chart.
- **One file per concern.** Indicators stay in `chart_indicators.toml`, drawings in `chart_drawings.toml`; the layout never duplicates them.
- **Restore before fetch.** The layout is loaded on mount before the first candle request, so that request already uses the saved timeframe.
- **Save discipline.** Debounced, one PUT per burst, on every change of any listed field; the same explicit-save discipline as indicators. Storage-blocked or offline saves are ledgered through the error bar, never silent.
- **No browser state.** `chart-timeframe:{iid}` and `chart-volume:{iid}` are imported once on the first load and removed; a grep test over `pages/ChartPage.tsx` and `hooks/` fails any `localStorage` read of a `chart-` key.
- **First open.** No table for the instrument → seeded from `[default]` (layout fields and `default_indicators` copied into `chart_indicators.toml` for that coin; drawings never copied), then saved under the coin so later edits are its own. `[default]` empty → built-in defaults: 1m, candles, volume on, crosshair on, no indicators.
- **Layout menu** in the top toolbar next to Indicators: "Save as default" (copies this coin's layout and indicator list into `[default]` after a confirm naming what it overwrites) and "Reset to default" (replaces this coin's layout and indicators with `[default]` after a confirm; drawings untouched).
- **Live edge unchanged.** Live candle and websocket paths behave as before; a second coin is never affected by the first coin's edits.
- Docs: `views/preferences.py` docstring, DocsPage chart section and `platform/CLAUDE.md`'s SSOT notes describe the three preference files and the layout's field list.
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- Story 32.5's preferences directory is not in place (the layout file would need its own mount); block rather than add a per-file mount.

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Restore the absolute scroll position.
- Store rankings, history or alerts page state; this is the chart page only.
- Copy drawings into or out of `[default]`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Return to coin | saved 1H, lines, volume off, RSI pane 220 px, 80 bars | mounts in lines at 1H, no volume pane, RSI 220 px, 80 bars visible, latest bar at right | none |
| First open, default set | `[default]` = 15m, candles, SMA(20) | coin seeded 15m with SMA(20), saved under coin | none |
| First open, no default | empty `[default]` | 1m candles, volume on, crosshair on, no indicators | none |
| Save as default | confirm | `[default]` = this coin's layout + indicator list | none |
| Reset to default | confirm | coin's layout + indicators replaced; drawings kept | none |
| Stale timeframe | saved `bar_seconds` = 30 | falls back to 1m, one `console.error` | fallback |
| Bad PUT | `mode: "bars"` | 422 naming `mode` | 422 |
| Old browser keys | `chart-timeframe:{iid}` = 300 | imported once (bar_seconds 300), key removed | none |
| Burst of changes | 5 zoom events in 300 ms | one PUT | none |
| Second coin | ETH edited | BTC's table untouched | none |

</intent-contract>

## Code Map

Verified against the live code at plan time (32.5 is merged; the preferences directory exists).

- Backend pattern to copy: `platform/views/preferences.py` (`load_chart_drawings`/`save_chart_drawings`/`validate_drawing`/`DrawingError`, `_write_atomic`; docstring lines 18-39), `platform/data_api/routes/drawings.py` (GET 500 on corrupt, PUT 400 bad JSON / 422 naming the field, `_path()` read per call), registered in `data_api/app.py` next to `indicators_routes`; `data_api/settings.py` gains `CHART_LAYOUTS_PATH`; `data/preferences/` gains a tracked `chart_layouts.toml`; no new mount (check `docker-compose.verify.yml`).
- Default indicators: `data_api/routes/indicators.py` PUT (`_parse_config_entry`, catalog + `_check_sources`) is factored into one shared helper so seeding/reset validate identically; `save_chart_indicators` writes the coin's list.
- Frontend: `api/client.ts` (`fetchCoinDrawings`/`saveCoinDrawings` pattern), `api/schema.ts`, `hooks/useChartDrawings.ts` (template: 600 ms debounce, never PUT before first GET, flush on unmount/pagehide, `saveError` rendered as `role="alert"`), `lib/chartVolume.ts` (legacy volume key; deleted), `pages/ChartPage.tsx` (`ChartForCoin` owns `barSeconds`/`volumeOn` and remounts `ChartInner` on timeframe change, so layout state lives in `ChartForCoin`; `ChartInner` holds `mode`, `crosshairOn`, `frvpSettings`, `vrvpSettings`, `sessionCfg`), `components/chart/LightweightChart.tsx` (`layoutPaneHeights`, `remembered`/`collapsedHeightsRef`; needs `initialPaneHeights` + `onPaneHeights` props), `components/IndicatorPicker.tsx` (refetches on `reloadKey`; Reset bumps it).
- Tests: `views/tests/test_chart_drawings.py` and `data_api/tests/test_drawings.py` (style), `data_api/tests/test_settings.py` (path derivation), `pages/ChartPage.test.tsx` (hoisted `vi.mock("../api/client")`, add `fetchCoinLayout`/`saveCoinLayout`), `components/chart/chartTheme.test.ts` (glob + `stripComments` grep pattern).
- Docs: `views/preferences.py` docstring, `pages/docs/kbData.ts` chart group, `platform/CLAUDE.md` SSOT-06.

## Tasks & Acceptance

**Execution:**
- [x] `platform/views/preferences.py` -- `load_chart_layouts`/`save_chart_layouts`/`validate_layout` (`LayoutError` naming the key; `v = 1`; `[default]` with `default_indicators`), atomic write; `views/tests/test_chart_layouts.py`.
- [x] `platform/data_api/settings.py`, `routes/layout.py`, `app.py`, `data/preferences/chart_layouts.toml` -- `GET`/`PUT /api/coin/{iid}/layout` plus default endpoints (seed on first GET, save-as-default, reset); shared indicator-entry validation helper; `data_api/tests/test_layout.py`, `test_settings.py`.
- [x] `frontend/src/api/{client,schema}.ts`, `hooks/useChartLayout.ts` -- restore before first fetch, debounced save, one-time import and removal of `chart-timeframe`/`chart-volume`, fallback with one `console.error`.
- [x] `ChartPage.tsx`, `LightweightChart.tsx`, `IndicatorPicker.tsx` -- wire every field (timeframe, mode, volume, crosshair, pane heights, visible bars, profile settings), Layout menu (Save as default / Reset to default with confirms), delete `lib/chartVolume.ts`.
- [x] Tests: `ChartPage.test.tsx` matrix rows, grep test failing any `localStorage` read of a `chart-` key over `pages/ChartPage.tsx` and `hooks/`.
- [x] Docs: `preferences.py` docstring, `kbData.ts`, `platform/CLAUDE.md` SSOT-06.

**Acceptance Criteria:**
- Given a coin whose chart the operator set up, when the coin is opened again from any browser, then every listed field is as it was left and the chart shows live data at the remembered zoom.
- Given a coin never opened, when it mounts, then it carries the `[default]` layout and indicator list, and editing it never changes another coin.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.

## Review Triage Log

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 1, medium 5, low 1)
- defer: 3
- reject: 8
- addressed_findings:
  - `[high]` `[patch]` Seeding on first GET wiped a pre-existing coin's indicator list; now seeds indicators only when the coin has none.
  - `[medium]` `[patch]` Built-in session "day" not in the closed set; now "daily", validated server-side with a mirror test against the frontend list.
  - `[medium]` `[patch]` Indicators PUT bypassed the layout lock; one shared `PREFERENCES_LOCK`.
  - `[medium]` `[patch]` Blocking I/O in async handlers; moved to threadpool.
  - `[medium]` `[patch]` Save-as-default stored an unvalidated indicator list; validated first.
  - `[medium]` `[patch]` Fragile error-string slicing and KeyError/TypeError mapped to "corrupt"; `.reason` attribute, narrowed handler.
  - `[low]` `[patch]` Save-as-default during a running reset now refused.

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 1, medium 6, low 7)
- defer: 1
- reject: 10
- addressed_findings:
  - `[high]` `[patch]` Pane ids were capped at 64 characters, but 11 of 38 catalog indicator ids are longer (MACD 100, CandlePattern 156), so one divider drag made the coin's layout unsaveable for good (every PUT a 422, held until the next edit); cap raised to `MAX_PANE_ID_LENGTH = 512`, tested with the real CandlePattern id.
  - `[medium]` `[patch]` Saved pane heights of removed indicators were never pruned; the next drag now keeps only `price`, `volume` and the coin's current indicator instance ids.
  - `[medium]` `[patch]` A stale `[default]` indicator made the first GET 500 even for a coin whose own indicator list it would not touch; `keep_existing` is now checked before the template is validated.
  - `[medium]` `[patch]` `_file_errors` did not map `KeyError`/`TypeError` from a malformed `chart_indicators.toml`, so seed/reset failed with an opaque 500; now a 500 with a detail.
  - `[medium]` `[patch]` The server accepted `kind = "fixed"` without both anchors or with `start > end`, which the client then falls back on every open; now a 422 naming `volume_profile.start`.
  - `[medium]` `[patch]` The legacy `chart-timeframe`/`chart-volume` keys overrode a layout already on the server (saved from another browser); they are now applied only on a seeded (first) open and otherwise just removed.
  - `[medium]` `[patch]` A restored fixed range outside the first loaded window stayed empty for good (hydrated once); it is now re-profiled on every candle load until it has rows, as the docs already said.
  - `[low]` `[patch]` Reset to default bumped the chart key in a separate update from the reset layout; the hook now returns a `revision` bumped in the same render.
  - `[low]` `[patch]` A cancelled pointer press left the drag baseline set; `pointercancel` now clears it.
  - `[low]` `[patch]` `LAYOUT_BAR_SECONDS` had no mirror test against `timeframes.ts`; added.
  - `[low]` `[patch]` A missing `pane_heights` was defaulted without being reported as a fallback; the `session` type comment contradicted the server.
  - `[low]` `[patch]` The failed-load alert promised a retry that a 4xx never gets; reworded.
  - `[low]` `[patch]` The restore test keyed `pane_heights` by a series key, not the instance id the chart reports.
  - `[low]` `[patch]` New lines over 100 characters (docstrings/comments, one test string) wrapped.

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3: (high 0, medium 2, low 1)
- defer: 2
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` A change reported after the hook's own flush was lost: React runs a deleted tree's cleanups parent-first, so a zoom the chart reported from its unmount cleanup hit an unmounted hook, and `pagehide` did not drain the chart's 300 ms zoom debounce; `latestRef` also lagged a commit behind (an effect). `update` now writes `latestRef` synchronously and, once the hook is unmounted or the page is hiding, saves at once (keepalive while hiding, reset on `pageshow`); the chart flushes a pending zoom on `pagehide`.
  - `[medium]` `[patch]` A GET (or reset/PUT) for any well-formed id with no instrument definition created permanent tables in `chart_layouts.toml` and `chart_indicators.toml`; a table is now only created for an id the catalog defines (404 otherwise, nothing written), checked outside the lock and only when the coin has no table.
  - `[low]` `[patch]` `sameLayout` and the saved-state check compared `JSON.stringify` output, so the same `pane_heights` in another key order counted as a change and sent a PUT; both now use `layoutKey` (keys sorted).

## Auto Run Result

**Summary:** Second follow-up review of Story 32.6 (per-coin server-side chart layout, `[default]` template, Layout menu, one-time legacy key import). This pass fixed 3 findings. The main one: a zoom or edit made just before leaving the chart or closing the tab could be lost, because the chart reports it after the layout hook has already flushed.

**Files (this pass):**
- `platform/frontend/src/hooks/useChartLayout.ts`: `update` writes `latestRef` synchronously and saves at once after unmount or `pagehide`. Also adds a `pageshow` reset.
- `platform/frontend/src/components/chart/LightweightChart.tsx`: flushes a pending zoom report on `pagehide`.
- `platform/frontend/src/lib/chartLayout.ts`: `layoutKey`, a key-order-independent comparison.
- `platform/data_api/routes/layout.py`: 404 and no write for an id with no instrument definition.
- Tests: `hooks/useChartLayout.test.ts` (+3, one adjusted for `pageshow`), `components/chart/LightweightChart.test.tsx` (+1), `data_api/tests/test_layout.py` (+1, fixture stubs the catalog lookup).

**Review:** 3 patches applied (medium 2, low 1). 2 deferred:
- A stale `[default]` indicator blocks every new coin's first open.
- The session count and Periodic preset are not in the layout's field list.

13 rejected. These were spec-conformant behaviour (Save as default copies the coin's fixed range, pane heights and zoom), already-ledgered items (one-bad-table blast radius, indicators PUT 400 vs 500), unreachable inputs (anchors over 1e11, pane ids over 512 characters, heights over 10000 px) and cosmetic issues.

**Verification:**
- Frontend: `npm test` passed (vitest 692, node 7). `npm run lint` shows 3 warnings, all pre-existing and unchanged. `npm run build` is ok.
- Backend: `python3 -m pytest views/tests data_api/tests -q` gave 538 passed and 9 failed. The 9 are the known Redis-dependent `test_archive`/`test_rankings*` tests.
- `ruff check` and `ruff format` are clean on the touched Python files.
- The 4 new frontend tests were confirmed to fail against the pre-fix code.

**Residual risks:**
- The `pagehide` path is still untested in a real browser. It relies on the chart's listener firing after the hook's, which holds because the chart mounts after the layout has loaded.
- A first open now reads the catalog once per coin (only when the coin has no table).
- Divider drag and the restored zoom are also still untested in a real browser.

