# Story 18.9: Periodic Volume Profile (PVP)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want a volume profile grouped by a recurring period I choose (weekly, 4-hourly, monthly),
so that I can see volume distribution over a period longer or shorter than one session.

## Acceptance Criteria

1. **Indicators dialog, `overlay: true`, with a `period: 'daily' | 'weekly' | '4h' | 'monthly'` settings field** (a simple dropdown, not a cron-like scheduler).
2. **Candles are grouped by the chosen recurring period; a profile is computed per period, recomputed on each period boundary** — same trigger pattern as Story 18.8's SVP (only the in-progress period's profile updates on new bars), not a separate scheduling mechanism.
3. **Reuses Story 18.8's "sessions/periods to render" setting** for how many past periods are shown, each independent.

## Tasks / Subtasks

- [ ] Task 1 — Period grouping (AC: #1, #2)
  - [ ] Generalize Story 18.8's calendar-day grouping helper to accept a period type (`'daily' | 'weekly' | '4h' | 'monthly'`) rather than writing a second, PVP-specific grouping function — `'daily'` should produce identical results to SVP's own grouping (confirms the generalization is correct, not a re-implementation).
  - [ ] Add "Periodic Volume Profile" to the Indicators-style add dialog with the period dropdown; wire the same boundary-recompute trigger Story 18.8 established (in-progress period only, per new bar).

- [ ] Task 2 — Reuse multi-period settings (AC: #3)
  - [ ] PVP's "sessions to render" reuses Story 18.8's shared settings field, not a duplicate.

- [ ] Task 3 — Tests
  - [ ] A test confirming weekly/4h/monthly grouping boundaries are computed correctly for a known multi-period candle array, and that `period: 'daily'` produces output identical to Story 18.8's SVP grouping for the same input (the cross-check that proves the generalization didn't silently diverge from SVP's behavior).

## Dev Notes

- **This story should mostly be "wire a dropdown onto Story 18.8's generalized grouping helper," not new calculation work** — if implementing PVP requires writing a second period-boundary algorithm from scratch, that's a sign Story 18.8's helper wasn't generalized correctly; go back and fix the helper instead of duplicating.
- **No full cron-like scheduler** — the period set is the fixed four values in AC #1, nothing user-defined beyond that dropdown.

### Project Structure Notes

- Modified: the calendar-grouping helper introduced/used in Story 18.8 (generalized to accept a period type), `troll/frontend/src/pages/ChartPage.tsx` (PVP add-dialog entry + period dropdown).
- Not modified: `VolumeProfilePrimitive`, `buildVolumeProfile` (consumed unchanged from Story 18.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.9] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.2 (PVP row)] — period dropdown, boundary-recompute trigger "same pattern as SVP."
- [Source: _bmad-output/implementation-artifacts/18-8-session-volume-profile-and-session-volume-profile-hd.md] — the grouping helper and boundary-recompute pattern this story generalizes and reuses, respectively.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
