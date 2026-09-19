---
baseline_commit: 9b8243c1bb0bda226965f458491c4c9c237c9334
---

# Story 18.2: Trendline drawing tool

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to click-drag a line between two points on the price/time plane,
so that I can mark a trend.

## Acceptance Criteria

1. **The left toolbar (Story 18.1) gains a "line (trendline)" tool.**
2. **Selecting it and click-dragging between two points creates a custom Primitive holding two `{time, price}` anchors**, rendered as a line between them — no native `lightweight-charts` API covers this (unlike Story 18.1's horizontal line).
3. **The Primitive redraws correctly on pan/zoom/crosshair-move**, via its `updateAllViews` — it must never visually drift from its original `{time, price}` anchors.
4. **Declarative ownership matches Story 18.1's `priceLines` pattern**: a new `drawings?: DrawingSpec[]` prop on `LightweightChart.tsx`, diffed the same way (add/update/remove by id via `series.attachPrimitive()`/`detachPrimitive()`), never a direct `chart`/`series` API call from `ChartPage.tsx`.
5. **`Esc` cancels an in-progress two-click drag** before the second point is placed.

## Tasks / Subtasks

- [x] Task 1 — Trendline Primitive (AC: #2, #3)
  - [x] Implement a `TrendlinePrimitive` class satisfying `lightweight-charts` v5's `ISeriesPrimitive` interface: holds two `{time, price}` anchors, implements `updateAllViews`/`paneViews` to draw a line between the anchors' current screen coordinates (via `series.priceToCoordinate`/`timeScale.timeToCoordinate`) on every redraw trigger.
  - [x] Attach via `series.attachPrimitive(primitive)` on the relevant main-pane series (Candles or Lines mode — unlike Story 18.1's price-line restriction, a trendline's anchors are `{time, price}` pairs independent of which main-pane series is currently displayed, so this tool works in both modes).

- [x] Task 2 — Declarative `drawings` prop on `LightweightChart.tsx` (AC: #4)
  - [x] Add `drawings?: DrawingSpec[]` (a tagged union covering trendline now, measurement in Story 18.3: `{ id: string; kind: "trendline"; anchors: [{time, price}, {time, price}] }`), diffed via an internal `Map<string, ISeriesPrimitive>` registry, same add/update/remove-by-id pattern as `panes`/`priceLines`.

- [x] Task 3 — Two-click drag interaction (AC: #1, #2, #5)
  - [x] While the trendline tool is active: first click records the start `{time, price}` (via `coordinateToPrice`/the chart's time-scale coordinate conversion); a second click (or drag-release, depending on final interaction choice — click-drag per the original spec) records the end point and appends a new `DrawingSpec` to `ChartPage.tsx`'s `drawings` array, then resets the active tool to cursor.
  - [x] `Escape` during the in-progress first-point-placed state cancels it without creating a `DrawingSpec`.

- [x] Task 4 — Tests
  - [x] A test confirming `TrendlinePrimitive`'s coordinate recomputation on a simulated pan/zoom (its `updateAllViews` is called and produces different screen coordinates for the same stored anchors).
  - [x] A test for the `drawings` prop's diffing (add/remove by id).

## Dev Notes

- **This is the first genuinely custom-Primitive-based drawing tool in this codebase** — no prior story built one. Read `lightweight-charts` v5's actual `ISeriesPrimitive`/`attachPrimitive` API surface carefully before implementing; do not assume the v4 plugin API (they differ).
- **Reuse Story 18.1's `activeTool`/toolbar state and `Esc`-cancel wiring in `ChartPage.tsx`** — extend the same state machine (`"cursor" | "hline" | "trendline"`), don't build a second one.
- **Keep `drawings` and `priceLines` as separate props** (per the original spec's own §A7.4-style guidance to keep genuinely different interaction models distinct) — a trendline is a two-anchor custom Primitive; a horizontal line is a native single-value price line. Don't collapse them into one generic "drawing" abstraction prematurely.

### Project Structure Notes

- New: a `TrendlinePrimitive` class, e.g. `troll/frontend/src/components/chart/primitives/TrendlinePrimitive.ts`.
- Modified: `troll/frontend/src/pages/ChartPage.tsx` (toolbar extended, `drawings` state), `troll/frontend/src/components/chart/LightweightChart.tsx` (new `drawings` prop + registry).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.2] — this story's origin (FR57).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A3] — trendline mechanics ("a custom Primitive holding two `{time, price}` anchors, redrawn on each `subscribeCrosshairMove`/pan/zoom via the primitive's `updateAllViews`").
- [Source: troll/frontend/src/components/chart/LightweightChart.tsx] — full file read this session (Story 18.1); the declarative-prop-diffing pattern this story's `drawings` prop follows.
- [Source: _bmad-output/implementation-artifacts/18-1-horizontal-line-drawing-tool.md] — the toolbar/tool-state/`Esc`-cancel foundation this story extends, and the `priceLines` prop this story's `drawings` prop is a sibling to, not a replacement for.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

- `TrendlinePrimitive` (v5 `ISeriesPrimitive`): stores `{time, price}` anchors, recomputes screen points in `updateAllViews` via `timeToCoordinate`/`priceToCoordinate`; skips drawing when an anchor has no coordinate.
- `drawings?: DrawingSpec[]` on `LightweightChart` diffed by id (attach/update/detach). Host is the candlestick series, or the `price` line series in Lines mode; registry is cleared on mode flip and re-attached.
- New `onPointClick({time, price})` shares the existing click subscription/suppression; time falls back to `coordinateToTime` past the last bar.
- ChartPage: `"trendline"` tool (both modes), two-click flow with `pendingAnchor`; Esc/tool change/mode change discards it. Interaction is click-click (no drag preview), per the story's Task 3 wording.
- vitest 113 pass, tsc + oxlint clean. Visual browser check not done (jsdom mock boundary, same as 18.1).

### File List

- troll/frontend/src/components/chart/primitives/TrendlinePrimitive.ts (new)
- troll/frontend/src/components/chart/LightweightChart.tsx
- troll/frontend/src/components/chart/LightweightChart.test.tsx
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx

### Review Findings

- [x] [Review][Patch] No test for the update-in-place leg of the drawings diff [LightweightChart.test.tsx] — fixed
- [x] [Review][Patch] No test that switching tool discards a pending first point [ChartPage.test.tsx] — fixed
- [x] [Review][Patch] Stale comment on click effect ("click behavior untouched") [LightweightChart.tsx] — fixed
- [x] [Review][Defer] Anchor on a time with no bar in the current mode's data (empty area right of last bar, or a 1s Lines-mode anchor viewed in Candles mode) has no coordinate, so the line is not drawn [TrendlinePrimitive.ts] — deferred, needs snapping/extrapolation design
- [x] [Review][Defer] Drawing color resolved once at creation, does not follow theme changes [ChartPage.tsx] — deferred, minor
- [x] [Review][Defer] No rubber-band preview after the first click [ChartPage.tsx] — deferred, story permits two-click; candidate for 18.10 parity pass

Dismissed as noise/handled: 12 (effect ordering, stale-closure, instrument/timeframe change, renderer snapshot, etc.).
