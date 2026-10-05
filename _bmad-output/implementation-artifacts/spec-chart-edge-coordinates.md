---
title: 'Chart drawing tools at the last-bar edge (DW-143, DW-144, DW-150)'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: '7abea6728803e362b5845a5ebbd3fe9961bf864c'
baseline_revision: 'ee388913870c46e6207146618180b3a60cb84bc6'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** The measurement, FRVP range-select and FRVP edge drags read time via `timeScale().coordinateToTime()`, which is null past the last bar (and in the left margin), so a drag there freezes at the last valid point (price included) or drops the FRVP endpoint. Around that: a measure press on the price/time axis strips starts a measurement, the range drag is mouse-only, the measurement label is drawn unclamped off the pane, `computeMeasurement` is O(n) per mousemove and ignores the forming live bar, an FRVP edge ghost survives the edge effect's teardown mid-drag, edges show no hover cursor, and the FRVP settings panel only appears after a profile is placed.

**Approach:** A move whose `coordinateToTime` is null falls back to `gridRef.current.timeAtLogical(coordinateToLogical(x))` (clamped to the loaded bars + forming bar, the same mapping drawing drags use). Presses outside the main pane's plot area are ignored. The range drag gains touch events. The label is clamped inside the pane. Measurement reads a prefix-sum index built once per data/volume/liveBar change. The edge effect reports a cancel on teardown and sets an `ew-resize` cursor on hover.

## Boundaries & Constraints

**Always:** Pointer-down still only starts a range drag when `coordinateToTime` resolves (a press in empty margin keeps panning). Fallback only on move, never replacing a non-null `coordinateToTime`. Touch drag stops the library pan (`stopPropagation`) and page scroll (`preventDefault`, non-passive listener) only while a drag is active. A lost release (touchcancel, mouse moved with no button) finishes the drag exactly as today. Edge cancel never commits. A live bar counts once: only when its time is newer than the last candle/volume entry.

**Block If:** none.

**Never:** No in-chart "x" removal control for FRVP (DW-150 item stays a text button; out of the bundle intent, needs UX design). No touch support for the FRVP edge grab or the drawing-handle drag. No change to `nautilus_trader/` or `crates/`. No new dependency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Drag past last bar | measure/FRVP/fib drag, pointer in right margin | end time = last grid slot (forming bar if any), price follows pointer | — |
| Press on axis strip | mousedown/touchstart x ≥ left-scale + plot width, or y ≥ price pane height | no drag starts, event untouched | — |
| Chart not laid out | price pane height 0 | press ignored | — |
| Touch drag | touchstart → touchmove → touchend | same onMove/onRelease sequence as the mouse | touchcancel finishes |
| Edge drag past last bar | grabbed edge moved into right margin | ghost and commit carry the last grid slot time | — |
| Teardown mid edge drag | tool armed / mode flip while grabbed | `onProfileEdgeCancel` once, ghost cleared, no commit | — |
| Live bar in range | range end ≥ forming bar time | bars +1, volume + live volume | not double-counted once promoted into data |
| Label near pane edge | rect at right/bottom edge | label fully inside the pane | — |

</intent-contract>

## Code Map

- `platform/frontend/src/components/chart/rangeDrag.ts` -- shared range-drag plumbing (measure, FRVP select, fib)
- `platform/frontend/src/components/chart/LightweightChart.tsx` -- effects ~1280-1440 (range select, edge drag, measurement, fib); `gridRef`/`barTimes` ~604-630; `measureDataRef` ~1211
- `platform/frontend/src/components/chart/primitives/drawingPrimitive.ts` -- `BarGrid.timeAtLogical`
- `platform/frontend/src/components/chart/primitives/MeasurementPrimitive.ts` -- `computeMeasurement`, label drawing
- `platform/frontend/src/pages/ChartPage.tsx` -- `EdgeGhost`, `handleEdgeDrag/Commit`, FRVP panel (~1196)
- `platform/frontend/src/components/chart/LightweightChart.test.tsx`, `primitives/MeasurementPrimitive.test.ts` -- module-mocked chart tests

## Tasks & Acceptance

**Execution:**
- [x] `rangeDrag.ts` -- export `plotPoint(container, chart, clientX, clientY)` (local x/y inside pane 0's plot area, else null: uses `priceScale("left").width()`, `timeScale().width()`, `panes()[0].getHeight()`) and `timeAtX(chart, grid, x)` (coordinateToTime ?? grid.timeAtLogical(coordinateToLogical)); `attachRangeDrag` takes the `BarGrid`, guards presses with `plotPoint`, uses `timeAtX` on move, and adds touchstart(capture)/touchmove(window, passive:false)/touchend/touchcancel paths -- one drag model for both inputs.
- [x] `MeasurementPrimitive.ts` -- `buildMeasurementIndex(candles, volume, liveBar)` (bar times, volume times + prefix sums) and `computeMeasurement(start, end, index)` by binary search; export a pure `labelOrigin(rect, textWidth, textHeight, paneWidth, paneHeight)` and use it in the renderer with `measureText`.
- [x] `LightweightChart.tsx` -- pass `gridRef.current` to every `attachRangeDrag`; build the measurement index in the `measureDataRef` effect (data, volume, liveBar); edge effect: `plotPoint` guard, `timeAtX` on move, hover `ew-resize` cursor via container mousemove when not grabbed, new prop `onProfileEdgeCancel` called from cleanup when grabbed (cursor reset too).
- [x] `ChartPage.tsx` -- pass `onProfileEdgeCancel={() => setEdgeGhost(null)}`; render the FRVP settings panel when `frvps.length > 0 || activeTool === "frvp"` (remove buttons still only for placed profiles).
- [x] tests -- extend the chart mock (`timeScale().width`, `priceScale()`), set a laid-out price pane in drag suites; add cases for every matrix row; update `MeasurementPrimitive.test.ts` to the index API plus live-bar and `labelOrigin` cases.

**Acceptance Criteria:**
- Given an armed measure/FRVP/fib tool, when the drag moves into the right margin, then the preview end sits on the last grid slot rather than freezing.
- Given the FRVP tool armed with no profile placed, when the page renders, then the FRVP settings panel is visible.
- Given `npm test`, `npm run lint` and `npm run build` in `platform/frontend`, when run, then all pass with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 4, low 5)
- defer: 0
- reject: 5: (high 0, medium 1, low 4)
- addressed_findings:
  - `[medium]` `[patch]` `useRef(buildMeasurementIndex(...))` ran an O(n) build on every render -- the ref now starts at a module-level empty index and the effect fills it.
  - `[medium]` `[patch]` The index was rebuilt on every live-bar tick -- it is built from `data`/`volume` only, and `computeMeasurement(start, end, index, liveBar)` folds the forming bar in at O(1) (read from `liveBarRef`).
  - `[medium]` `[patch]` Touch drags ignored finger identity (a second finger pinched under the drag, `touches[0]` could be the wrong finger, any lift ended the drag) -- the starting finger's `identifier` is tracked, a second finger mid-drag is blocked, only its own `changedTouches` entry ends the drag.
  - `[medium]` `[patch]` The FRVP edge effect stayed live with Fibonacci armed (both drags started; the new cursor advertised it) -- the effect is now off while `fibActive`.
  - `[low]` `[patch]` Window `touchmove`/`touchend` were non-passive for as long as a tool was armed -- they are attached at a touch drag's start and removed in `finish()`.
  - `[low]` `[patch]` A lost `touchend` left `start` set forever -- a new touchstart without the tracked finger finishes the stale drag first.
  - `[low]` `[patch]` The resize cursor stuck after a release outside the chart and was rewritten on every move -- written only on change, cleared on `mouseleave` and on release.
  - `[low]` `[patch]` Test variables `startNotPrevented`/`moveNotPrevented` meant the opposite -- renamed `*Dispatched` with a note on `fireEvent`'s return value.
  - `[low]` `[patch]` Coverage gaps -- tests added for second finger, other-finger lift, lost touchend, cursor reset on leave/release, edges with Fibonacci armed, live bar outside the range.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 2, low 4)
- defer: 1
- reject: 13: (high 0, medium 1, low 12)
- addressed_findings:
  - `[medium]` `[patch]` A pinch whose second finger landed in a separate `touchstart` (the normal case) was blocked by an unmoved range drag, so pinch-zoom never worked with a range tool armed -- a second finger before the first moved now drops the unmoved drag (a plain click to every caller) and the event reaches the library's pinch, matching its own "move before the second touch prevents the pinch" rule; a drag that already moved still blocks it.
  - `[medium]` `[patch]` The measurement volume was a difference of plain running totals, so a short range after a large history printed the history's rounding error (`(1e12+0.1+0.2)-(1e12+0.1)` = 0.19995) -- the index carries Neumaier compensation terms per prefix and the difference adds them back; the incorrect `Known limit:` comment is replaced.
  - `[low]` `[patch]` The FRVP edge hover set `ew-resize` while a button was held (a library pan crossing an edge) -- hover only advertises with no button down.
  - `[low]` `[patch]` `MeasurementIndex`'s doc said it was rebuilt per live-bar change, contradicting the build site -- corrected.
  - `[low]` `[patch]` `EMPTY_MEASUREMENT_INDEX` sat between `LightweightChart`'s JSDoc and the component, stealing its doc -- moved above the JSDoc.
  - `[low]` `[patch]` The chart mock's left price scale was always 0 px wide, so the new scale-width subtraction was untested -- the width is a per-test variable, with a test that a press on a visible left scale is ignored and plot x is offset by it.

## Verification

**Commands:**
- `cd platform/frontend && npx vitest run` -- expected: all pass
- `cd platform/frontend && npm run lint && npx tsc -b` -- expected: clean

## Auto Run Result

**Summary:** Range tools (measurement, FRVP range select, Fibonacci placement) and FRVP edge drags no longer freeze or drop their endpoint past the newest bar: a move whose `coordinateToTime` is null falls back to `BarGrid.timeAtLogical(coordinateToLogical(x))`, clamped to the loaded bars plus the forming bar. Presses on the price/time axis strips (or before layout) are ignored. The range drag has a touch path that tracks its own finger and hands an early second finger to the library's pinch. The measurement label is clamped inside the pane. Measurement reads an O(log n) compensated prefix-sum index built per history change, with the forming bar counted once. An FRVP edge drag torn down mid-drag reports `onProfileEdgeCancel` (no commit). Edges show an `ew-resize` hover cursor when no button is held. The FRVP settings panel shows as soon as the FRVP tool is armed.

**Files changed** (under `platform/frontend/src/`):
- `components/chart/rangeDrag.ts` -- `localPoint`/`plotPoint`/`timeAtX`; grid fallback on move; plot-area press guard; identifier-tracked touch drag (window listeners only during the drag; an unmoved drag yields to a pinch).
- `components/chart/primitives/MeasurementPrimitive.ts` -- `buildMeasurementIndex` (Neumaier-compensated prefix sums) + binary-search `computeMeasurement(start, end, index, liveBar)`; pure `labelOrigin` used by the renderer.
- `components/chart/LightweightChart.tsx` -- grid passed to all range drags; index ref + `liveBarRef`; edge effect: guard, fallback, hover cursor (no button held), `onProfileEdgeCancel`, off while Fibonacci is armed.
- `pages/ChartPage.tsx` -- `handleEdgeCancel`; FRVP settings panel visible while the tool is armed.
- `components/chart/LightweightChart.test.tsx`, `components/chart/primitives/MeasurementPrimitive.test.ts`, `pages/ChartPage.test.tsx` -- coverage for every matrix row and every review patch (incl. sequential-finger pinch, left-scale offset, held-button hover, long-history precision).

**Review findings (follow-up pass):** 6 patches applied (2 medium, 4 low), 1 deferred (FRVP ending on the forming bar omits it from the profile and is never rebuilt: pre-existing, appended to the ledger), 13 rejected (touchcancel commits: spec-mandated; grid passed by value: `BarGrid` is mutated in place; other cursor writers: none exist; y not clamped on move: pre-existing; cancel on an unmoved grab / during unmount: harmless; `hr` vs `vr` text width: bitmap-space measurement is correct; stale live-bar label while holding still and unsorted input: by contract; FRVP panel test: the condition does gate the settings group; "release outside" test: window-targeted events never reach the container hover; edge touch support: spec Never; drawing tools vs edge grab: edges are only editable with the cursor tool; drags left of history: clamped to the first bar).

**Ledger coverage, not resolved by this bundle:** DW-150's "removal is a text button outside the chart rather than an in-chart x" is not built (spec Never: needs a UX design). Every other sub-item of DW-143, DW-144 and DW-150 is addressed.

**Verification:** `npx vitest run` in `platform/frontend` -- 38 files, 741 tests passed. `npx tsc -b` -- clean. `npm run lint` -- 3 warnings, all pre-existing (`TrustedHtml.tsx`, `useCandles.ts`), none in touched files. `npm run build` -- succeeded.

**Residual risks:** Touch handling (including the pinch hand-off) is verified in jsdom only, against the library's documented pinch logic in `lightweight-charts` 5.2.1; no real-device run.
