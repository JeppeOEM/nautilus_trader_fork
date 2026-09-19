---
baseline_commit: ec3dc023add43243fe2286f8a9cc7014edf27b5b
---

# Story 18.6: Fixed Range Volume Profile (FRVP)

Status: done

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

- [x] Task 1 — Toolbar entry + click-drag interaction (AC: #1, #2)
  - [x] Add "Volume Profile (Fixed Range)" to the left toolbar's tool set (extends Stories 18.1-18.3's `activeTool` union).
  - [x] On drag-release, slice the currently-loaded `candles` array (or fetch via `/api/candles` if the selected range extends beyond what's already loaded client-side — per Story 18.5's Task 3 backend verification) between the two clicked timestamps, call `buildVolumeProfile`, and append a `VolumeProfileSpec` (`{ id, profile, xAnchor: <fixed position derived from the selection>, width }`) to `ChartPage.tsx`'s `volumeProfiles` array.

- [x] Task 2 — Edge-drag resize (AC: #3)
  - [x] Dragging either edge of a placed FRVP's visual range updates that spec's underlying candle window and re-runs `buildVolumeProfile`, replacing the existing entry (same id) — never creating a duplicate.
  - [x] Any interaction other than an edge-drag (pan, zoom, adding an indicator) leaves an already-placed FRVP untouched — no live recompute.

- [x] Task 3 — Removal (AC: #4)
  - [x] An FRVP instance is removable (× control, matching the trendline/measurement pattern) independent of any other placed FRVP or the chart's other drawings.

- [x] Task 4 — Tests
  - [x] A test confirming a drag-release computes the profile exactly once (not on every intermediate mouse-move) and that panning/zooming afterward does not trigger a recompute.

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

claude-sonnet-5

### Debug Log References

### Completion Notes List

- Left-toolbar "FRVP" tool (candles-only). Click-drag runs on new shared `rangeDrag.ts` (`attachRangeDrag`), extracted from 18.3's measurement effect (which now uses it too, behavior unchanged): live rectangle preview (the measurement primitive with no label, **no calculation while dragging**), then exactly one `onRangeSelect` on release; a click without a drag reports nothing; Esc/disarm cancels.
- `buildRangeProfile(candles, volume, start, end, settings)` (lib): slice -> join volume -> `buildVolumeProfile`. Range endpoints come from the drag over the loaded chart, so slicing the loaded candles is sufficient; no extra `/api/candles` fetch was needed. During replay the profile sees only revealed bars.
- Placed profiles are stored as `{id, startTime, endTime, profile}` and computed once (release, edge-drag release, or an explicit settings change) -- pan/zoom/new candles never recompute them (test asserts profile identity is unchanged).
- **Deliberate change to 18.5's primitive** (story text says "unchanged", but 18.5's own review deferred exactly this): `VolumeProfilePrimitive` now accepts `xAnchor: {time}` and `width: {toTime}`, re-resolved to pixels on every redraw so a range-pinned profile follows pan/zoom, plus optional dashed range `edges`. Numeric/`"right"` anchors work as before.
- Edge resize: grabbing an edge (6px, within the profile's height) drags a ghost (only geometry moves), and one commit on release rebuilds the profile. Uses a latest-callback ref so parent re-renders mid-drag don't drop the drag; disabled while a range tool is armed.
- Removal: no trendline/measurement precedent existed, so a small "Volume profiles" group with a remove button per FRVP (aria "Remove volume profile frvp-N"), plus the shared 18.5 settings panel (rebuilds placed profiles from stored ranges on change; shown once at least one FRVP exists).
- vitest 189 pass, tsc + oxlint clean. No real-browser visual check.

### File List

- troll/frontend/src/components/chart/rangeDrag.ts (new)
- troll/frontend/src/components/chart/LightweightChart.tsx
- troll/frontend/src/components/chart/LightweightChart.test.tsx
- troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts
- troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.test.ts
- troll/frontend/src/lib/volumeProfile.ts
- troll/frontend/src/lib/volumeProfile.test.ts
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx

### Review Findings

- [x] [Review][Patch] Placed profiles leaked future bars after a replay rewind / stayed truncated after a replay ended [ChartPage.tsx] — fixed: stored profile is built from the full loaded data; while replay is active the display rebuilds from revealed bars; + test
- [x] [Review][Patch] Edge press could steal another armed tool's click [LightweightChart.tsx] — fixed: `profileEdgesEditable` (ChartPage: only with the cursor tool, not while picking a replay start) + test
- [x] [Review][Patch] Lost mouseup (release outside the window) left a range/edge drag stuck to the cursor [rangeDrag.ts, LightweightChart.tsx] — fixed: a move with no button held finishes the drag + test
- [x] [Review][Patch] Both edges of a very narrow range within one tolerance: end edge ungrabbable [LightweightChart.tsx] — fixed, nearer edge wins + test
- [x] [Review][Patch] Settings rebuild could overwrite a profile with an empty one; orphaned comment [ChartPage.tsx] — fixed
- [x] [Review][Defer] Dragging past the last bar (no coordinate->time) drops/stales the endpoint (same root as 18.2/18.3 items); ghost not cancelled if the edge effect is torn down mid-drag; no hover cursor on edges; settings panel only shown once a profile exists; remove control is a text button, not an in-chart x — deferred, polish for 18.10

Dismissed as noise/handled: 6 (instrument/timeframe change — `ChartInner` is keyed by instrument and profile times are UTC seconds independent of bar size; unchecked `Time` casts — repo uses UTC seconds; etc.).
