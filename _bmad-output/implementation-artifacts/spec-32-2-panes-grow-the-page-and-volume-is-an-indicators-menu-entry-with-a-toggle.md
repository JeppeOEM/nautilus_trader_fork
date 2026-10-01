---
title: 'Story 32.2: Panes grow the page instead of shrinking each other, and volume is an Indicators-menu entry with a toggle'
type: 'feature'
created: '2026-09-30'
status: 'done'
final_revision: 'e1ec264c8a4ee165ede13d560355b450dfeaf988'
baseline_revision: 'a47f3c5f6127ef3b3adcaea6b2647148e8b01048'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `LightweightChart.tsx` creates the chart at a fixed `height: 500` and only the width follows the container, so every pane added by an indicator shares those 500 px and the candles shrink. Volume is hard-wired as the first pane (`DEFAULT_PANE_IDS = ["volume"]` in `ChartPage.tsx`) with no way to switch it off, and its comments call it an "overlay", which it is not.

**Approach:** The chart's height is computed from its panes (price pane height plus one default height per extra pane) and re-applied whenever the pane registry changes, so adding a pane makes the page taller and the operator scrolls. Volume becomes the first entry of the Indicators dialog, added and removed like any indicator, on by default, persisted per instrument.

## Boundaries & Constraints

**Always:**
- **Height arithmetic.** `PRICE_PANE_PX = 500` (kept), `VOLUME_PANE_PX = 120`, `INDICATOR_PANE_PX = 160`, named constants in `LightweightChart.tsx`. Total height = price pane + Σ extra panes, applied with `chart.applyOptions({ height })` and per-pane sizing (`setStretchFactor`, or `setHeight` if lightweight-charts 5.2.1 offers it; check the installed API, never a newer one).
- **Existing panes keep their pixel size** when a pane is added or removed, including a size the operator set by dragging a divider (§A8.2). Only the total changes.
- **Page scrolls.** No `overflow: hidden` and no fixed height on any ancestor between the chart container and `body`; `frontend/scripts/chart-layout.test.mjs` pins this.
- **Volume in the dialog.** "Volume" is pinned above the catalog categories in `IndicatorPicker`'s dialog, has no params, is on by default, and its on/off state lives in `localStorage` under `chart-volume:{iid}` next to `chart-timeframe:{iid}` (provisional: Story 32.6 moves both into the server-side layout and imports them once; keep the read/write in one small helper so 32.6 replaces one place).
- **`fullVolume` is always fetched** and still feeds FRVP, VRVP, SVP/SVP HD, PVP and the measurement tool when the volume pane is off. The live `series.update("volume")` path is a no-op when the pane is absent, never an error.
- **Order.** When switched back on, the volume pane returns first under the price pane, before every indicator pane.
- **Comments.** The "overlay" wording at `ChartPage.tsx` (`DEFAULT_PANE_IDS` and the `panes` builder) is corrected: volume is a pane; an overlay is `placement: "overlay"`.
- Fit, Latest, legend placement, crosshair sync, the replay marker, the gap primitives of Story 32.1 and every drawing primitive behave as before; the existing `LightweightChart.test.tsx` pane tests keep passing.
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- lightweight-charts 5.2.1 offers no way to hold an existing pane's pixel size while another pane is added (neither `setStretchFactor` arithmetic nor `setHeight` achieves it in a test).

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Persist the volume toggle server-side in this story (32.6 does), or add it to `chart_indicators.toml`.
- Change legend buttons, the light palette or the gap rendering (Stories 32.3, 32.4, 32.1).
- Make the price pane resize with the window height; only the width follows the container.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Default chart | candles + volume on | height 620 px (500 + 120), volume first under price | none |
| Add RSI pane | volume on, one indicator pane added | height 780 px; price and volume panes unchanged in px | none |
| Remove RSI | pane removed | height back to 620 px | none |
| Dragged divider then add | operator dragged volume to 200 px, then adds MACD | height 860 px; volume stays 200 px | none |
| Volume off | toggle off in dialog | volume pane absent, height 500 px + indicator panes; `chart-volume:{iid}` = off | none |
| Volume off, FRVP drawn | toggle off | profile rows identical to volume-on | none |
| Live bar with volume off | `update()` for the volume series | no call, no error | none |
| Reload with volume off | `chart-volume:{iid}` = off | pane absent on mount | none |
| Storage blocked | `localStorage` throws | volume on (default), one `console.error` | caught, default applied |

</intent-contract>

## Code Map

- `platform/frontend/src/components/chart/LightweightChart.tsx` -- `createChart` at :408 (`height: 500`), `handleResize` :452-461 (width only), pane registry effect :627-707 (`addPane`/`removePane`, volume is the spec id `"volume"`), live volume update :728-755 (already `?.` no-op), `PaneEntry` :215, `panesRef` :362.
- `platform/frontend/src/pages/ChartPage.tsx` -- `DEFAULT_PANE_IDS` :51 and "overlay" comments :48-50, :270; `timeframeStorageKey`/`loadTimeframe` :54-59 (mirror for volume); `panes` useMemo :273-301; `fullVolume` :212 (feeds profiles :392, :414); `<IndicatorPicker>` :816-825.
- `platform/frontend/src/components/chart/IndicatorPicker.tsx` -- dialog ~:332, results list; owns no chart state, so a volume row needs new props (`volumeOn`, `onVolumeChange`).
- `platform/frontend/scripts/chart-layout.test.mjs` reads `src/index.css`; `.chart-workspace` (:206), `.term-box` (theme.css:128) set no overflow/height.
- lightweight-charts 5.2.1 `IPaneApi`: `getHeight`, `setHeight`, `getStretchFactor`, `setStretchFactor` all exist.
- Tests: `ChartPage.test.tsx:287` and :614; `LightweightChart.test.tsx` mock `makePaneMock()` :55 lacks `getHeight`/`setHeight` (extend it).

## Tasks & Acceptance

**Execution:**
- [x] `platform/frontend/src/components/chart/LightweightChart.tsx` -- add `PRICE_PANE_PX`, `VOLUME_PANE_PX`, `INDICATOR_PANE_PX`; after each pane registry change pin every existing pane to its current `getHeight()` via `setHeight`, give the new pane its default, apply total height via `chart.applyOptions({ height })`; resize observer keeps width only.
- [x] `platform/frontend/src/components/chart/LightweightChart.test.tsx` -- extend the pane mock; tests for the add/remove/dragged-divider/volume-off/live-no-op matrix rows.
- [x] `platform/frontend/scripts/chart-layout.test.mjs` (+ CSS if needed) -- assert no `overflow: hidden`/fixed height on chart ancestors.
- [x] `platform/frontend/src/pages/ChartPage.tsx` -- volume storage helper (`chart-volume:{iid}`, try/catch, default on, one `console.error`), conditional volume pane first, fix "overlay" comments, pass props to the picker.
- [x] `platform/frontend/src/components/chart/IndicatorPicker.tsx` -- pinned "Volume" row above catalog categories with toggle.
- [x] `ChartPage.test.tsx`, `IndicatorPicker` tests -- default on, off persisted and restored on reload, storage blocked, `fullVolume` still feeds profiles.

**Acceptance Criteria:**
- Given any number of non-overlay panes, when one is added or removed, then the chart height equals the price pane plus the sum of the extra panes and no existing pane changes its pixel size.
- Given the Indicators dialog, when Volume is toggled off and the page reloads, then the volume pane is absent, the profiles still compute from the fetched volume, and toggling on restores the pane first under the price pane.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.

## Review Triage Log

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 2, low 2)
- defer: 0
- reject: 12
- addressed_findings:
  - `[medium]` `[patch]` `laidOutRef` stale across chart recreation: reset when the chart is created.
  - `[medium]` `[patch]` first layout could run before the time axis was measured and never correct: the resize observer's first frame now completes the layout.
  - `[low]` `[patch]` Volume checkbox ignored the picker's `disabled`: now honours it.
  - `[low]` `[patch]` unbounded page height undocumented: `Known limit:` comment added.


### 2026-10-01 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 2, low 2)
- defer: 0
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` before the time axis was measured, extra panes were pinned at their axis-less pre-paint height (volume ~115 px instead of 120): defaults are used for every pane until the axis is known; test added.
  - `[medium]` `[patch]` every data-only `panes` refresh re-pinned all stretch factors and re-applied the height, fighting a divider mid-drag: layout now runs only when a pane is added, removed or moved (or the first layout is still incomplete); test added.
  - `[low]` `[patch]` `IndicatorPicker` rendered an uncheckable Volume box if `onVolumeChange` came without `volumeOn`: the row now needs both.
  - `[low]` `[patch]` `chart-layout.test.mjs` missed `overflow: auto/scroll` and `max-height` on chart ancestors: guard widened.

## Auto Run Result

- Summary: follow-up review of Story 32.2 (pane heights computed from the pane set, volume as a pinned, per-instrument Indicators-dialog toggle). Four review patches applied.
- Files: `LightweightChart.tsx` (axis-less heights ignored before first measured layout; relayout only on pane-set change), `LightweightChart.test.tsx` (two tests), `IndicatorPicker.tsx` (Volume row needs both props), `scripts/chart-layout.test.mjs` (wider ancestor guard).
- Review: 4 patches applied, 0 deferred, 13 rejected (e.g. 1 px separator assumption verified in lightweight-charts 5.2.1 source, StrictMode dev double-log, volume drag size reset on re-toggle is the spec'd default).
- Verification: `npm test` (447 vitest + 7 node tests pass), `npm run lint` (the same 3 pre-existing warnings, none new), `npm run build` pass.
- Residual risk: stretch-factor sizing is still verified against a mock, not a real browser layout.
