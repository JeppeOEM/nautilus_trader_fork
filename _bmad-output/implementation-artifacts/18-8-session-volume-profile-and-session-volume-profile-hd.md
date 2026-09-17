# Story 18.8: Session Volume Profile and Session Volume Profile HD

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want a volume profile computed per calendar session (day), with a higher-resolution variant available,
so that I can compare volume distribution session-over-session.

## Acceptance Criteria

1. **SVP groups candles by calendar day (session), one profile per day, using the base/finest timeframe data regardless of the chart's current bar size** — Indicators dialog, `overlay: true`.
2. **Recompute trigger: once per session boundary; only the current (in-progress) session's profile updates as new bars arrive** — not a full recompute of every rendered session on each new bar.
3. **SVP HD is a config preset of the *same* component as SVP, not a separate code path**: a higher default `rowCount` (100+ vs SVP's ~24) and a `respondsToZoom: true` flag that redraws (not recomputes) the Primitive on zoom-level changes so bar thickness stays legible.
4. **Settings include "number of past sessions to render"** (e.g. "show last 5"), each session keeping its own independent POC/VAH/VAL — never merged across sessions.

## Tasks / Subtasks

- [ ] Task 1 — Calendar-day session grouping (AC: #1)
  - [ ] Group the finest-available candle data by UTC calendar day (confirm which day boundary/timezone convention the rest of this codebase already uses for daily rollups — e.g. `troll/ranking_engine`'s or `ml_signals`' existing daily-window logic — and reuse it rather than introducing a second day-boundary convention).
  - [ ] Add "Session Volume Profile" to the Indicators-style add dialog; each session's candle group feeds its own `buildVolumeProfile` call (Story 18.5), producing one `VolumeProfileSpec` per rendered session.

- [ ] Task 2 — Session-boundary recompute (AC: #2)
  - [ ] On a new bar, only the in-progress (current) session's `VolumeProfileSpec` is recomputed/updated; already-closed sessions' specs are untouched until the next session boundary closes and a new in-progress one begins.

- [ ] Task 3 — HD preset (AC: #3)
  - [ ] Implement as a settings/config variant of the same SVP component (e.g. an `hd: boolean` flag defaulting `rowCount` to 100+ and setting `respondsToZoom: true`) — not a second component or a duplicated recompute-trigger implementation.
  - [ ] `respondsToZoom: true` wires a zoom-level subscription that calls the Primitive's redraw (not `buildVolumeProfile` again) — bar thickness/legibility only, no new calculation.

- [ ] Task 4 — Multi-session settings (AC: #4)
  - [ ] Extend Story 18.5's shared settings panel with a "sessions to render" count specific to SVP/PVP (Story 18.9); each rendered session is an independent `VolumeProfileSpec` entry with its own POC/VAH/VAL, never merged.

- [ ] Task 5 — Tests
  - [ ] A test confirming session grouping produces one profile per calendar day for a known multi-day candle array, and that only the most recent (in-progress) session's profile changes when a new bar is appended.

## Dev Notes

- **Find and reuse this project's existing calendar-day grouping convention before writing a new one** — daily rollups already exist somewhere in `ranking_engine`/`ml_signals` (per the multi-exchange spec's own guidance); a second, subtly different day-boundary definition would be a real (if quiet) correctness bug (e.g. UTC vs a different day-start convention causing off-by-one session assignment near midnight).
- **HD is explicitly a preset, not a fork** — if implementing SVP HD ever requires copy-pasting SVP's component, that's a signal the abstraction is wrong; go back and parameterize instead.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (SVP/SVP-HD add-dialog entries, session grouping + boundary-recompute logic).
- New (if no existing shared day-boundary utility is found): a small calendar-day-grouping helper, placed alongside `troll/frontend/src/lib/volumeProfile.ts`.
- Not modified: `VolumeProfilePrimitive`, `buildVolumeProfile` (consumed unchanged from Story 18.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.8] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.2 (SVP/SVP-HD rows), #A7.3] — session-boundary trigger, HD-as-preset guidance, multi-session settings.
- [Source: _bmad-output/implementation-artifacts/18-5-volume-profile-shared-engine-and-rendering-primitive.md] — shared engine/Primitive/settings this story extends.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
