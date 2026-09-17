# Story 18.6: Fixed Range Volume Profile (FRVP)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to click-drag between two timestamps and see a persistent volume profile for that exact range,
so that I can analyze a specific historical move.

## Acceptance Criteria

1. **FRVP is a left-toolbar drawing tool** (Story 18.1-18.3's toolbar family), not an Indicators-dialog entry — it confirms into a persistent profile object on drag-release, like the measurement tool's rectangle but persistent rather than transient.
2. **Click-dragging between two points runs `buildVolumeProfile` (Story 18.5) once, on drag-release**, over the candles between those two timestamps.
3. **Dragging an edge of a placed FRVP resizes and recomputes it**; otherwise it stays static — the "confirm once" model, explicitly distinct from Story 18.7 (VRVP)'s always-recompute model even though both share Story 18.5's engine/Primitive.
4. **Multiple FRVP instances can coexist** (each a `VolumeProfileSpec` entry, removable independently), matching the trendline/measurement precedent of a `drawings`-style array rather than a single-slot tool.

## Tasks / Subtasks

- [ ] Task 1 — Toolbar entry + click-drag interaction (AC: #1, #2)
  - [ ] Add "Volume Profile (Fixed Range)" to the left toolbar's tool set (extends Stories 18.1-18.3's `activeTool` union).
  - [ ] On drag-release, slice the currently-loaded `candles` array (or fetch via `/api/candles` if the selected range extends beyond what's already loaded client-side — per Story 18.5's Task 3 backend verification) between the two clicked timestamps, call `buildVolumeProfile`, and append a `VolumeProfileSpec` (`{ id, profile, xAnchor: <fixed position derived from the selection>, width }`) to `ChartPage.tsx`'s `volumeProfiles` array.

- [ ] Task 2 — Edge-drag resize (AC: #3)
  - [ ] Dragging either edge of a placed FRVP's visual range updates that spec's underlying candle window and re-runs `buildVolumeProfile`, replacing the existing entry (same id) — never creating a duplicate.
  - [ ] Any interaction other than an edge-drag (pan, zoom, adding an indicator) leaves an already-placed FRVP untouched — no live recompute.

- [ ] Task 3 — Removal (AC: #4)
  - [ ] An FRVP instance is removable (× control, matching the trendline/measurement pattern) independent of any other placed FRVP or the chart's other drawings.

- [ ] Task 4 — Tests
  - [ ] A test confirming a drag-release computes the profile exactly once (not on every intermediate mouse-move) and that panning/zooming afterward does not trigger a recompute.

## Dev Notes

- **Do not make FRVP and VRVP (Story 18.7) share one "live" component that also handles the fixed-range case dynamically** — this is an explicit anti-pattern called out in the original spec (§A7.4): they have genuinely different UX/trigger lifecycles even though both consume `buildVolumeProfile`/`VolumeProfilePrimitive` from Story 18.5.
- **Settings panel:** reuse Story 18.5's shared settings component (row count, value area %, colors, POC/VA visibility) — no FRVP-specific settings needed beyond what's already shared.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (toolbar entry, `volumeProfiles` state, drag-to-create/resize interaction).
- Not modified: `troll/frontend/src/lib/volumeProfile.ts`, the `VolumeProfilePrimitive` class (both consumed unchanged from Story 18.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.6] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.2 (FRVP row), #A7.4] — placement (left toolbar, not Indicators dialog), confirm-once trigger model, and the explicit anti-pattern warning against merging with VRVP.
- [Source: _bmad-output/implementation-artifacts/18-5-volume-profile-shared-engine-and-rendering-primitive.md] — the shared engine/Primitive/settings this story consumes unchanged.
- [Source: _bmad-output/implementation-artifacts/18-3-measurement-tool.md] — the click-drag-rectangle interaction pattern this story's Task 1 mirrors (but persistent, not transient).

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
