---
title: 'Story 32.2: Panes grow the page instead of shrinking each other, and volume is an Indicators-menu entry with a toggle'
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

Filled at plan time from the live code (Story 32.1's Code Map is the continuity input). Expected anchors: `platform/frontend/src/components/chart/LightweightChart.tsx` (`createChart` height, the width-only `ResizeObserver`, the pane registry effect that calls `chart.addPane()`), `platform/frontend/src/pages/ChartPage.tsx` (`DEFAULT_PANE_IDS`, the `panes` builder, the timeframe `localStorage` helpers to mirror), `platform/frontend/src/components/chart/IndicatorPicker.tsx` (the dialog), `platform/frontend/src/index.css` and `frontend/scripts/chart-layout.test.mjs`, `ChartPage.test.tsx` ("shows only candles + a volume pane by default"), `LightweightChart.test.tsx` pane tests.

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map; one task per file, ordered: height arithmetic and tests, CSS/layout test, volume entry in the dialog with its storage helper, `ChartPage` wiring and comment fix, tests for every matrix row.

**Acceptance Criteria:**
- Given any number of non-overlay panes, when one is added or removed, then the chart height equals the price pane plus the sum of the extra panes and no existing pane changes its pixel size.
- Given the Indicators dialog, when Volume is toggled off and the page reloads, then the volume pane is absent, the profiles still compute from the fetched volume, and toggling on restores the pane first under the price pane.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
