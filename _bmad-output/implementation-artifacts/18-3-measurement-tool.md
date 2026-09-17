# Story 18.3: Measurement tool

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to click-drag a rectangle across two points and see price delta, bar count, and volume sum,
so that I can quickly measure a move without manual calculation.

## Acceptance Criteria

1. **The left toolbar (Stories 18.1/18.2) gains a "measurement" tool.**
2. **Click-dragging a rectangle across the main pane overlays a label showing price delta (absolute + %) and the number of bars/time spanned.**
3. **If the same selection spans the volume pane, the label additionally shows summed volume across the selected bars** — reading from `useCandles`' already-fetched `volume` array (`VolumeDatum[]`), never a new query.
4. **This is a custom Primitive** (no native `lightweight-charts` equivalent), following the same `drawings`-prop declarative pattern Story 18.2 established.
5. **The measurement is transient by default** (a live rectangle updates while dragging) but Task 1 below decides whether it persists after drag-release as a `DrawingSpec` entry (removable like a trendline) or clears immediately — call this out as a decision the story makes explicitly, not an ambiguity left for the dev agent to guess.

## Tasks / Subtasks

- [ ] Task 1 — Decide persistence model (AC: #5)
  - [ ] **Decision for this story: the measurement is transient** — it disappears when the mouse button is released (matching a typical ruler-tool UX and the original spec's "overlay a small label" wording, which describes an in-progress readout rather than a saved annotation). This avoids adding a "remove a stale measurement" affordance nobody asked for. If persistent measurements are wanted later, that's a follow-up decision, not silently assumed here.

- [ ] Task 2 — Measurement Primitive (AC: #2, #3, #4)
  - [ ] Implement a `MeasurementPrimitive` (or reuse `TrendlinePrimitive`'s rectangle-anchor logic where structurally similar) drawing a rectangle between two `{time, price}` points plus a text label; computed values: `priceDelta = end.price - start.price`, `priceDeltaPct = priceDelta / start.price`, bar count from the number of `data` entries between the two `time` values.
  - [ ] Volume sum (AC #3): when the drag rectangle's vertical extent covers the volume pane too (or always, if the tool doesn't visually distinguish "over the volume pane" — a UX simplification worth flagging as acceptable per the original spec's "if over the volume pane" being a nice-to-have, not a hard gate), sum `volume` entries (`VolumeDatum[]` from `useCandles`) whose `time` falls within the selected range, skipping whitespace/gap entries.

- [ ] Task 3 — Click-drag interaction, transient render (AC: #1, #5)
  - [ ] While active, mouse-down records the start point, mouse-move updates a live-rendered rectangle + label via the Primitive's own state (not React re-renders per pixel), mouse-up clears it (per Task 1's decision) and resets the active tool to cursor.
  - [ ] `Escape` mid-drag cancels without leaving any residual rectangle.

- [ ] Task 4 — Tests
  - [ ] A test for the price-delta/percent/bar-count computation given known start/end anchors and a known candle array.
  - [ ] A test for the volume-sum computation given a known `VolumeDatum[]` slice, including correct skipping of gap/whitespace entries.

## Dev Notes

- **Reuses Stories 18.1/18.2's toolbar/tool-state machine** (`"cursor" | "hline" | "trendline" | "measure"`) — extend, don't rebuild.
- **Since this tool is transient (Task 1), it does not need a `drawings`-array entry at all** — unlike the trendline, its state can live entirely inside the Primitive/interaction handler for the duration of the drag, never touching `ChartPage.tsx`'s persisted `drawings` state. This is simpler than Story 18.2's design, not an inconsistency — a persistent tool and a transient one legitimately have different state lifetimes.
- **`troll/CLAUDE.md` DESIGN-01 applies directly to Task 1's decision** — no "remove a measurement" UI is built because no measurement ever outlives the drag.

### Project Structure Notes

- New: a `MeasurementPrimitive` class, e.g. `troll/frontend/src/components/chart/primitives/MeasurementPrimitive.ts`.
- Modified: `troll/frontend/src/pages/ChartPage.tsx` (toolbar extended), `troll/frontend/src/components/chart/LightweightChart.tsx` only if the transient Primitive still needs to be attached/detached through it (recommended, to preserve the "sole owner" invariant even for a transient overlay) rather than manipulated by `ChartPage.tsx` directly.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.3] — this story's origin (FR57).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A3] — measurement tool mechanics (price delta, bar count, volume sum "if over the volume pane").
- [Source: troll/frontend/src/hooks/useCandles.ts] — full file read this session; `VolumeDatum[]`/`ChartDatum[]` shapes this story's computations read directly, no new query.
- [Source: _bmad-output/implementation-artifacts/18-2-trendline-drawing-tool.md] — the Primitive-attachment pattern and toolbar/tool-state machine this story extends.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
