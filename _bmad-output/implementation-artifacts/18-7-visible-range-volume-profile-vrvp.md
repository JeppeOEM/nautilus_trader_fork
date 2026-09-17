# Story 18.7: Visible Range Volume Profile (VRVP)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want a volume profile that always reflects whatever's currently visible,
so that I get an at-a-glance profile without manually selecting a range.

## Acceptance Criteria

1. **VRVP is an Indicators-dialog entry** (§A4.1's add-dialog, reusing Story 15.6/17.5's existing indicator catalog mechanics as the placement pattern — though VRVP itself is a chart-only feature, not part of the 37-entry TA catalog; it uses the *same dialog interaction*, "click a result → added immediately"), `overlay: true` since it renders on the main price pane.
2. **`buildVolumeProfile` (Story 18.5) runs over whatever candles are currently in the visible time-scale range**, via `chart.timeScale().subscribeVisibleTimeRangeChange()`.
3. **Recomputes on every pan/zoom** — the "always recompute" model, explicitly distinct from Story 18.6 (FRVP)'s confirm-once model, never sharing one merged component with it (§A7.4).
4. **Only one VRVP instance is meaningful per chart** (unlike FRVP's multiple independent instances) — adding it again while already active updates/replaces the existing one rather than stacking a second.

## Tasks / Subtasks

- [ ] Task 1 — Add-dialog entry (AC: #1)
  - [ ] Add a "Visible Range Volume Profile" entry to wherever the chart's Indicators-style add dialog lives (Story 15.6/17.5's existing add mechanism) — clicking it immediately creates a `VolumeProfileSpec` with `xAnchor` set to auto-track the right edge of the current visible range.

- [ ] Task 2 — Recompute on visible-range change (AC: #2, #3)
  - [ ] Subscribe to `chart.timeScale().subscribeVisibleTimeRangeChange()`; on each fire, re-slice `candles` to the new visible range, re-run `buildVolumeProfile`, and update the existing `VolumeProfileSpec` entry (same id, new `profile`) — never appending a new entry per pan/zoom event.
  - [ ] Unsubscribe cleanly when VRVP is removed or the chart unmounts.

- [ ] Task 3 — Tests
  - [ ] A test confirming a simulated visible-range change triggers exactly one profile recompute and updates (not duplicates) the existing spec entry.

## Dev Notes

- **Reuses Story 18.5's engine/Primitive/settings verbatim** — the only new logic here is the recompute trigger and its single-instance-per-chart semantics.
- **Do not merge with FRVP (Story 18.6) into one dynamic component** — explicit anti-pattern per §A7.4, restated here since it's the most likely shortcut a dev agent might reach for given how similar the two look superficially.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (VRVP add-dialog wiring, visible-range subscription, single-instance replace logic).
- Not modified: `troll/frontend/src/lib/volumeProfile.ts`, `VolumeProfilePrimitive` (consumed unchanged from Story 18.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.7] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.2 (VRVP row), #A7.4] — placement (Indicators dialog, `overlay: true`), always-recompute trigger, anti-merge-with-FRVP warning.
- [Source: _bmad-output/implementation-artifacts/18-5-volume-profile-shared-engine-and-rendering-primitive.md, 18-6-fixed-range-volume-profile-frvp.md] — the shared engine this story consumes and the sibling variant it must stay structurally distinct from.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
