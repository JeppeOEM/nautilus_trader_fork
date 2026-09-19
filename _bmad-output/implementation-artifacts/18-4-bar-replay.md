---
baseline_commit: 379ebd246f02f373cbdcfe4b535878ebf5c1c758
---

# Story 18.4: Bar Replay

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to pick a start bar and replay the chart bar-by-bar,
so that I can review how price action unfolded without seeing future bars.

## Acceptance Criteria

1. **A "Replay" control (top toolbar area) puts the chart into "pick a start bar" mode**: cursor becomes a crosshair with a vertical guide line following it.
2. **Clicking a candle sets that bar as the replay start** — a vertical marker line is drawn there, and every bar after it is hidden from the chart.
3. **A replay control bar appears** (Play/Pause, Step-back, Step-forward, a speed selector cycling a fixed set — e.g. 0.5×/1×/2×/5× — a "Go to…" re-picker, Exit).
4. **Play reveals one additional bar at a fixed interval scaled by speed** (base interval ÷ speed); Step-forward/back move exactly one bar each and pause autoplay if running.
5. **Drawing tools (Stories 18.1-18.3) and indicators keep working and recalculating live as bars are revealed during replay** — not special-cased out.
6. **Exiting replay restores the full dataset and removes the control bar and vertical marker.**
7. **`useCandles`' own older-history scroll-back refill is unaffected by replay** — replay only ever hides bars from the *newest* end of the already-loaded array; it never touches the pagination logic that loads *older* bars at the far-left edge.

## Tasks / Subtasks

- [x] Task 1 — Replay state and start-bar picker (AC: #1, #2)
  - [x] Add replay state to `ChartPage.tsx`'s `ChartInner`: `replayState: "off" | "picking" | "active"`, `replayIndex: number | null` (an index into the currently-loaded `candles` array marking the last visible bar).
  - [x] While `"picking"`: a crosshair + vertical guide line follows the cursor (`chart.subscribeCrosshairMove`); a click on a candle sets `replayIndex` to that bar's array index and transitions to `"active"`.

- [x] Task 2 — Slice the visible dataset, never touch `useCandles`'s pagination (AC: #2, #7)
  - [x] **Critical design point, confirmed by reading `useCandles.ts`:** its scroll-back refill (`subscribeVisibleLogicalRangeChange`) only triggers on the array's *left* (oldest) edge (`range.from >= REFILL_MARGIN_BARS` — i.e. approaching the earliest loaded bar) and is completely independent of the array's right (newest) end. Replay only needs to trim the *right* end for display — `ChartPage.tsx` computes `const displayedCandles = replayState === "active" && replayIndex !== null ? candles.slice(0, replayIndex + 1) : candles` and passes `displayedCandles` (not raw `candles`) as `LightweightChart`'s `data` prop. `useCandles` itself, and its refill logic, are completely untouched — do not add any replay-awareness inside `useCandles.ts`.
  - [x] The vertical marker line at the replay start bar reuses Story 18.1's `priceLines`-style declarative pattern conceptually, but as a time-based marker rather than a price line — if `lightweight-charts` has no direct "vertical time marker" primitive, implement it as a small custom Primitive (same family as Stories 18.2/18.3) rather than repurposing `createPriceLine`.

- [x] Task 3 — Replay control bar and playback (AC: #3, #4)
  - [x] Render Play/Pause, Step-back, Step-forward, a speed selector (cycling `[0.5, 1, 2, 5]`, not a continuous slider), "Go to…" (re-enters Task 1's picking mode without resetting `replayState` to `"off"` first — must preserve the fact a replay is in progress), and Exit.
  - [x] Play: a `setInterval` at `700ms / speed` incrementing `replayIndex` by 1 each tick, stopping (not wrapping) once `replayIndex` reaches `candles.length - 1`. Step-forward/back: `setReplayIndex(i => i + 1 / - 1)`, clamped to `[0, candles.length - 1]`, and must also clear/pause the Play interval if running.

- [x] Task 4 — Confirm drawing tools/indicators keep working during replay (AC: #5)
  - [x] No special-casing needed in `LightweightChart.tsx`, `panes`, `priceLines`, or `drawings` — they all already operate on whatever `data`/`panes` they're handed, independent of how `ChartPage.tsx` computed that slice. Verify this holds (a real manual/test check that indicator panes and drawings remain interactive and correctly positioned while `replayIndex` advances) rather than assuming it from the architecture alone.

- [x] Task 5 — Exit replay (AC: #6)
  - [x] Resets `replayState` to `"off"`, `replayIndex` to `null` — `displayedCandles` reverts to the full `candles` array, the control bar and vertical marker are removed.

- [x] Task 6 — Tests
  - [x] A test confirming `displayedCandles` correctly slices at `replayIndex` and that `useCandles`'s own refill trigger logic is unaffected by a mocked replay state (i.e. the refill effect's dependencies/behavior are untouched).
  - [x] A test for the speed-cycling and step-forward/back clamping behavior.

## Dev Notes

- **The single most important fact this story depends on:** `useCandles.ts`'s scroll-back refill and replay's forward-hiding operate on opposite ends of the same array and never interact — confirmed by reading the hook's actual refill trigger (`range.from >= REFILL_MARGIN_BARS`, the *left* edge only). Do not add a `replayIndex`/`enabled`-style parameter to `useCandles` itself; the slicing belongs entirely in `ChartPage.tsx`, downstream of the hook.
- **Live edge during replay:** `liveBar` (Story 15.5's forming candle) should not be shown while replay is `"active"` — pass `liveBar={replayState === "active" ? null : liveBar}` to `LightweightChart`, since showing the real-time forming bar during a historical replay would defeat the point (seeing "future" price action).
- **No trade-simulation/PnL tracking, no tick-level replay, no multi-chart sync** — explicitly out of scope, matching real TradingView's own limitations here (nothing to over-build).

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (replay state, control bar, `displayedCandles` slicing, `liveBar` suppression during replay), `troll/frontend/src/components/chart/LightweightChart.tsx` only if a new vertical-marker Primitive needs the same attach/detach registry pattern as `drawings`.
- Not modified: `troll/frontend/src/hooks/useCandles.ts` (confirmed no replay-awareness needed there).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.4] — this story's origin (FR58).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A5] — full Bar Replay mechanics (entry point, start-point selection, control bar, playback, interaction-during-replay, exit).
- [Source: troll/frontend/src/hooks/useCandles.ts] — full file read this session; confirms the left-edge-only refill trigger this story's design depends on.
- [Source: troll/frontend/src/pages/ChartPage.tsx] — full file read this session; `liveBar`/`useLiveCandle` wiring this story suppresses during active replay.
- [Source: _bmad-output/implementation-artifacts/18-1-horizontal-line-drawing-tool.md, 18-2-trendline-drawing-tool.md, 18-3-measurement-tool.md] — AC #5's "drawing tools keep working" requirement; this story adds no special-casing to any of their declarative props.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

- `hooks/useReplay.ts`: state machine (`off|picking|active`) downstream of `useCandles`; replay position is a bar **time**, not an index, because scroll-back refill prepends bars and shifts every index (AC #7). `displayed` trims only the newest end. `useCandles.ts` is untouched.
- Play = `setInterval(700ms / speed)`, speeds cycle `[0.5,1,2,5]`; stops (never wraps) at newest loaded bar (derived `isPlaying`). Step skips gap entries, clamps, pauses autoplay. "Go to..." keeps the replay (Esc/cancel returns to it).
- **Required fix outside the story text:** `LightweightChart`'s scroll-back shift detection used array-length growth, which would treat every revealed replay bar as an older-history prepend and shift the view. It now locates the previous first bar's new index instead; covered by tests (end add/remove no shift; prepend still compensates).
- `VerticalMarkerPrimitive` (dashed full-height line) attached via new `markerTime` prop. `liveBar` suppressed while replay active. Replay is candles-only (button disabled / replay exited on Lines).
- The cursor "vertical guide" while picking is the library's own crosshair vertical line (already on); no extra guide primitive built.
- Task 4 (drawings/indicators during replay): no special-casing; verified by test that a trendline can be placed via the same click path after picking. No real-browser visual check.
- vitest 145 pass, tsc + oxlint clean.

### File List

- troll/frontend/src/hooks/useReplay.ts (new)
- troll/frontend/src/hooks/useReplay.test.ts (new)
- troll/frontend/src/components/chart/primitives/VerticalMarkerPrimitive.ts (new)
- troll/frontend/src/components/chart/LightweightChart.tsx
- troll/frontend/src/components/chart/LightweightChart.test.tsx
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx

### Review Findings

- [x] [Review][Patch] Volume and indicator panes still showed bars after the replay position (lookahead leak) [ChartPage.tsx] — fixed: everything trimmed to the replay cutoff time (identity-preserving when replay is off) + test
- [x] [Review][Patch] `displayed` fell back to the full array if the replay time was missing from the data [useReplay.ts] — fixed: filter by time, never a fallback + test
- [x] [Review][Patch] Start marker vanished while re-picking via "Go to..." [useReplay.ts] — fixed, marker kept while replay in progress
- [x] [Review][Defer] Revealed bars may land off-screen right of the viewport (no follow-scroll after `setData`); needs a real-browser check to see whether the library already follows [LightweightChart.tsx] — deferred
- [x] [Review][Defer] Silent no-op when clicking a gap while picking; Play at the newest bar is a silent no-op; Step back may go before the start marker; play interval restarts when `candles` identity changes; marker color not theme-live — deferred, polish for 18.10

Dismissed as noise/handled: 9 (liveBar reapply on exit — effect re-fires on the prop change; cursor style — library crosshair default; index/`findIndex` perf; etc.).
