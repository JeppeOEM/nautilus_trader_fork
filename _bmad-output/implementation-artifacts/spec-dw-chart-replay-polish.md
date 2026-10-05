---
title: 'Bar replay polish: follow-scroll, gap-pick feedback, end/start clamps, stable play interval, live marker colour'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: '9603d91a5f34c6f8f21b9d6419e99f1e98f5c81a'
baseline_revision: 'f6d6807deef9f58faf0e07e4be0957ae7b8570f2'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Story 18.4's bar replay left six deferred rough edges (DW-145, DW-146): a revealed bar can sit off-screen right after `setData` (e.g. picking a start bar while scrolled back leaves the view's negative right offset pointing past the trimmed data); clicking a gap while picking silently does nothing; Play at the newest bar silently does nothing; Step back walks past the start marker; the play `setInterval` is torn down and re-armed on every `candles` identity change (a live poll faster than the tick stalls playback); and the replay marker freezes its colour at construction instead of reading the chart token.

**Approach:** Fix each in place: an opt-in `followNewest` prop on `LightweightChart` that scrolls the newest bar into view only when the newest painted time changed and it is off-screen; a `pickMissed` flag plus `atStart`/`atEnd` from `useReplay` driving a status line and disabled controls; step-back clamped at `startTime`; the interval reading candles through a ref; `VerticalMarkerPrimitive` resolving `--chart-marker` at draw time like `PositionPrimitive`.

## Boundaries & Constraints

**Always:** Replay stays purely downstream of `useCandles` (Story 18.4 AC #7) -- positions are times, never indices. Colours come only from `--chart-*` tokens through `chartVar` (chartTheme.test.ts greps for literals). Follow-scroll fires only while a replay is active and only when the newest displayed time changed; a scroll-back prepend (newest time unchanged) never moves the view to the replay head. Existing prepend compensation and the Story 32.6 initial-zoom behaviour are preserved and run before the follow check.

**Block If:** none -- every item is fully specified by the ledger entries.

**Never:** No change to `useCandles.ts`, `nautilus_trader/` or `crates/`. No wrap-around/auto-restart of playback at the newest bar. No new dependencies. Not editing the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Follow on step | `followNewest`, newest bar index beyond visible `to` (or before `from`) after setData | `setVisibleLogicalRange({from: last+0.5-width, to: last+0.5})`, width preserved | none |
| Head already visible | newest index within [from, to] | no range change | none |
| Prepend during replay | older bars prepended, newest time unchanged | only prepend compensation, no follow | none |
| Not replaying | `followNewest` false | no follow ever (current behaviour) | none |
| Gap pick | picking, click on a gap slot / non-bar time | stays picking, `pickMissed` true, status text shown | cleared by next successful pick, startPicking, cancel, exit |
| Play at newest | active, lastTime is the newest real bar | Play and Step forward disabled, "End of loaded data" shown; togglePlay is a no-op | a later bar arriving re-enables them |
| Step back at start | lastTime == startTime (or previous bar older than startTime) | position unchanged, Step back disabled | none |
| Candles churn while playing | `candles` identity changes, same bars | interval not re-armed; next tick on schedule | none |
| Marker colour | `--chart-marker` changes after the marker exists | next draw uses the new value | jsdom: CHART_TOKENS fallback |

</intent-contract>

## Code Map

- `platform/frontend/src/hooks/useReplay.ts` -- replay state machine; add `pickMissed`, `atStart`, `atEnd`, step clamp, ref-based interval.
- `platform/frontend/src/hooks/useReplay.test.ts` -- hook tests.
- `platform/frontend/src/components/chart/LightweightChart.tsx` -- candles `setData` effect (~line 921); `markerTime` effect (~1276); props doc (~239, viewCommand doc says data effects never move the view).
- `platform/frontend/src/components/chart/LightweightChart.test.tsx` -- `replay support (Story 18.4)` block, timeScale mocks (`getVisibleLogicalRangeMock` returns {10,50}).
- `platform/frontend/src/components/chart/primitives/VerticalMarkerPrimitive.ts` -- marker primitive, colour currently a ctor arg.
- `platform/frontend/src/pages/ChartPage.tsx` -- `handlePointClick` picking branch (~576), replay control bar (~1070), `<LightweightChart>` props (~1159).
- `platform/frontend/src/pages/ChartPage.test.tsx` -- `ChartPage bar replay (Story 18.4)` block; its step test currently expects stepping past the marker.

## Tasks & Acceptance

**Execution:**
- [x] `platform/frontend/src/hooks/useReplay.ts` -- add `pickMissed` to state (set by a failed `pick`, reset by successful pick/startPicking/cancelPick/exit); clamp `step(-1)` so the result is never older than `startTime`; expose `atEnd`, `atStart`, `pickMissed`; `togglePlay` does nothing when stopped at the end; keep candles in a ref updated in an effect and drop `candles` from the interval effect deps -- DW-146.
- [x] `platform/frontend/src/components/chart/primitives/VerticalMarkerPrimitive.ts` -- drop the colour ctor param, resolve `chartVar("--chart-marker")` inside `draw` -- DW-146 marker colour.
- [x] `platform/frontend/src/components/chart/LightweightChart.tsx` -- add `followNewest?: boolean` (default false); in the candles data effect, after prepend compensation and initial zoom, when `followNewest` and the newest time differs from the previous setData's newest and its index is outside the visible logical range, move the range to end at `last + 0.5` keeping its width; construct the marker without a colour; update the `viewCommand` doc comment -- DW-145.
- [x] `platform/frontend/src/pages/ChartPage.tsx` -- pass `followNewest={replay.mode === "active"}`; picking prompt becomes a `role="status"` line that switches to a gap message when `pickMissed`; disable Play/Step forward at `atEnd` (while not playing) with an "End of loaded data" note, disable Step back at `atStart` -- DW-146.
- [x] Tests in the three test files above (plus a new `VerticalMarkerPrimitive.test.ts`) covering every I/O matrix row; update the ChartPage step test to the clamped behaviour.

**Acceptance Criteria:**
- Given a replay picked while scrolled back so the head lies right of the view, when the trimmed data is painted, then the view moves so the head bar is the rightmost visible bar.
- Given all edits, when `npx vitest run`, `npx tsc -b` and `npm run lint` run in `platform/frontend`, then all pass with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 1, low 3)
- defer: 0
- reject: 14: (high 0, medium 0, low 14)
- addressed_findings:
  - `[medium]` `[patch]` Follow treated a half-clipped head bar as visible -- `followNewestBar` now requires the whole bar (index ± 0.5) inside the range; test added.
  - `[low]` `[patch]` Nested forward/back ternary in `step` -- split into `stepForwardTime`/`stepBackTime` helpers.
  - `[low]` `[patch]` `viewCommand` doc contradicted itself ("the only place" while listing others) -- reworded.
  - `[low]` `[patch]` `ReplayControls` doc promised every disabled control "says why" but Step back had no explanation -- added a tooltip at the start bar and corrected the doc.

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 0, medium 0, low 2)
- defer: 0
- reject: 25: (high 0, medium 0, low 25)
- addressed_findings:
  - `[low]` `[patch]` `candlesRef` was synced in a passive effect, so a play tick between commit and effect could step over the previous array and stop one bar early -- now a `useLayoutEffect`.
  - `[low]` `[patch]` The gap-pick hint claimed "a data gap" for any missed click, including past the newest bar -- reworded to "No bar at that time -- click a candle" (test updated).

## Design Notes

Follow-scroll is a guard rather than an unconditional `scrollToPosition`: lightweight-charts keeps the right offset relative to the last bar, so at the realtime edge the head already stays visible and the guard is a no-op; it only acts when the head is off-screen (scrolled back before picking, or after the user panned away while paused). Comparing against the previous setData's newest time is what keeps scroll-back refills (prepends) from yanking the view.

## Verification

**Commands:**
- `cd platform/frontend && npx vitest run` -- expected: all pass
- `cd platform/frontend && npx tsc -b` -- expected: no errors
- `cd platform/frontend && npm run lint` -- expected: clean

**Manual checks (if no CLI):**
- Real-browser check of follow-scroll is not possible unattended; the guard is idempotent with any library-side follow, so it is correct either way.

## Auto Run Result

**Summary:** Follow-up review of the DW-145/DW-146 bar replay polish (originally `7a4d13fc46`). The replay head now scrolls into view after `setData`. A missed pick shows a hint. Play and Step forward are disabled at the newest bar. Step back is clamped at the start marker. The play interval no longer re-arms when candles churn. The marker reads `--chart-marker` at draw time. This pass applied two low-severity patches.

**Files changed (this pass):**
- `platform/frontend/src/hooks/useReplay.ts`: `candlesRef` is now synced in a layout effect, and the `pickMissed` doc is corrected.
- `platform/frontend/src/pages/ChartPage.tsx`: the gap-pick hint now reads "No bar at that time".
- `platform/frontend/src/pages/ChartPage.test.tsx`: the hint expectation is updated to match.

**Review:** 2 patches applied (both low). 0 deferred. 25 rejected:
- The "playback restarts by itself" finding is wrong: the interval already clears `playing` on the newest bar.
- The stale `togglePlay` closure was rejected in the previous pass.
- lightweight-charts range get/set is synchronous.
- Following on pick or step back is the intended behaviour (the AC).
- The two `role="status"` spans are in mutually exclusive modes.
- The remaining findings were theoretical, cosmetic or style-only.

**Verification:** in `platform/frontend`, `npx vitest run` passed 39 files and 761 tests. `npx tsc -b` exited 0. `npm run lint` showed 0 errors and the same 3 warnings as before, all in untouched files.

**Residual risk:** follow-scroll is still unchecked in a real browser.
