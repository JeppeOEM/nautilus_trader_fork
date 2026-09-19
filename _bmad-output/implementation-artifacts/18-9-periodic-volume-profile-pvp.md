---
baseline_commit: 5b27e2646a2a6252da39e79d8056907f7050f03f
---

# Story 18.9: Periodic Volume Profile (PVP)

Status: done

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

- [x] Task 1 — Period grouping (AC: #1, #2)
  - [x] Generalize Story 18.8's calendar-day grouping helper to accept a period type (`'daily' | 'weekly' | '4h' | 'monthly'`) rather than writing a second, PVP-specific grouping function — `'daily'` should produce identical results to SVP's own grouping (confirms the generalization is correct, not a re-implementation).
  - [x] Add "Periodic Volume Profile" to the Indicators-style add dialog with the period dropdown; wire the same boundary-recompute trigger Story 18.8 established (in-progress period only, per new bar).

- [x] Task 2 — Reuse multi-period settings (AC: #3)
  - [x] PVP's "sessions to render" reuses Story 18.8's shared settings field, not a duplicate.

- [x] Task 3 — Tests
  - [x] A test confirming weekly/4h/monthly grouping boundaries are computed correctly for a known multi-period candle array, and that `period: 'daily'` produces output identical to Story 18.8's SVP grouping for the same input (the cross-check that proves the generalization didn't silently diverge from SVP's behavior).

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

claude-sonnet-5

### Debug Log References

### Completion Notes List

- Mostly wiring, as the story intends: `lib/sessionProfile.ts` already took a `SessionPeriod` (4h / daily / weekly / monthly, UTC-aligned; weeks Monday 00:00 UTC), so 18.9 adds a `pvp` entry to `SESSION_PRESETS` (default period weekly, 24 rows) and `SESSION_PERIODS` (the fixed dropdown set), a "Profile period" `<select>` in `SessionProfileControl` shown only for PVP, and `changeSessionPeriod` in ChartPage (recomputes the wanted start; `useSessionCandles` switches bar size per period and discards items of another size).
- Same single slot, same `buildSessionProfiles`/cache/boundary trigger as SVP (only the in-progress period rebuilds), and the same shared "Sessions to render" setting -- no second grouping function, no scheduler.
- Long periods are profiled from coarser bars (`sessionBarSeconds`: weekly 5m, monthly 15m) so the bounded fetch (page budget sized to the wanted span, hard cap 80 x 500 bars) can span several periods; if it still falls short, the oldest partly covered period is omitted rather than shown truncated.
- Tests: weekly/4h/monthly boundaries (Sunday/Monday edge, leap February, year end), daily cross-checked against independent floor-to-day arithmetic, last-N, dropdown regrouping and fixed option set, shared sessions setting, period dropdown only for PVP. vitest 240 pass, tsc + oxlint clean. No real-browser check.

### File List

- troll/frontend/src/lib/sessionProfile.ts
- troll/frontend/src/lib/sessionProfile.test.ts
- troll/frontend/src/components/chart/SessionProfileControl.tsx
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx

### Review Findings

- [x] [Review][Patch] The fixed 40-page cap silently dropped the oldest periods (10 weekly periods at 5m bars need ~41 pages; 10 monthly at 15m ~58) [useSessionCandles.ts] — fixed: page budget derived from the wanted span (hard cap 80) + test
- [x] [Review][Patch] The live monthly/weekly profile could lag up to 15 min / 5 min (refresh at the bar size) [useSessionCandles.ts] — fixed: refresh at most every 60s + test
- [x] [Review][Patch] Fewer periods than requested were shown with no explanation [SessionProfileControl.tsx] — fixed: "Showing N of M (history still loading, or beyond the fetch window)" + test
- [x] [Review][Patch] Period switch had no test on the refetch parameters, or on preset switching back to PVP [ChartPage.test.tsx] — added (bar size 300 -> 900, deeper wanted start; PVP -> SVP -> PVP restarts on weekly)
- [x] [Review][Defer] Every period/count change re-pages from now (no debounce/abort of in-flight pages, no loading indicator); `sinceSeconds` fixed at add time (no rollover re-anchor); period dropdown not persisted; `SESSION_PRESETS.period` means "fixed" for SVP but "dropdown default" for PVP; dropdown shows raw values ("4h") — deferred, polish for 18.10

Dismissed as noise/handled: 5 (clicking PVP while active — the button is disabled for the active preset; "no bar exactly at the period start" — any older bar satisfies coverage, exhaustion is reported by `has_more`; label-in-name; etc.).
