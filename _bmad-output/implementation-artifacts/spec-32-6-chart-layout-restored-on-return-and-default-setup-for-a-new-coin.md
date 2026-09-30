---
title: 'Story 32.6: A coin''s chart comes back exactly as it was left, and a new coin opens with your default setup'
type: 'feature'
created: '2026-09-30'
status: 'draft'
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

Filled at plan time from the live code (continuity from the 32.5 spec). Expected anchors: `platform/frontend/src/pages/ChartPage.tsx` (timeframe and volume storage helpers, `mode`, `crosshairOn`, profile state, toolbar clusters), `hooks/useVisibleRange.ts` (visible bars), `components/chart/LightweightChart.tsx` (pane heights from 32.2, divider drag), `api/client.ts`/`schema.ts`, `platform/views/preferences.py`, `platform/data_api/routes/indicators.py` (route pattern), `platform/data_api/settings.py` (`CHART_PREFERENCES_DIR`), `platform/data/preferences/` (tracked TOML files), `pages/docs/kbData.ts`, `platform/CLAUDE.md` SSOT section, tests: `ChartPage.test.tsx`, `views/tests/test_preferences.py`, `data_api/tests`.

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map, ordered: preferences loader/saver + route + tests; `useChartLayout` hook with restore-before-fetch, debounce and the one-time import; wire every field; default template + Layout menu; grep test; docs.

**Acceptance Criteria:**
- Given a coin whose chart the operator set up, when the coin is opened again from any browser, then every listed field is as it was left and the chart shows live data at the remembered zoom.
- Given a coin never opened, when it mounts, then it carries the `[default]` layout and indicator list, and editing it never changes another coin.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.
