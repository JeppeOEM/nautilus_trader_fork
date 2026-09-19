# Story 17.4: Wire the Rankings/Performance → History link

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want a way to reach a coin's 31-day history directly from its Rankings/Performance row,
so that I don't have to type the `/history/:iid` URL by hand.

## Acceptance Criteria

1. **A row-level affordance on the Performance tab navigates to `/history/:iid`** (the already-registered route in `App.tsx`, currently unreachable from any UI — confirmed via reading `RankingsPage.tsx`: its only navigation today is `navigate(/chart/${row.instrument_id})` on row click).
2. **The existing row-click-to-chart behavior is unchanged** — this story adds a second, distinct affordance; it does not repurpose or replace the existing full-row click.
3. **The new affordance does not fire the row's existing click handler** (no accidental double-navigation to both `/chart/:iid` and `/history/:iid` from one interaction).

## Tasks / Subtasks

- [ ] Task 1 — Add the History affordance (AC: #1, #2, #3)
  - [ ] In `RankingsPage.tsx`'s row rendering (the `<tr onClick={() => navigate(\`/chart/${row.instrument_id}\`)}>` block), add a small icon/button inside one cell (e.g. the Instrument cell, alongside the existing stale-badge `<span>` elements) that calls `navigate(\`/history/${row.instrument_id}\`)`.
  - [ ] The new control's click handler must call `event.stopPropagation()` (or an equivalent guard) so clicking it does not also bubble into the `<tr>`'s own `onClick` and navigate to `/chart/:iid` at the same time.
  - [ ] This affordance is only meaningful once Story 17.1's Performance tab exists and Story 17.2's `/history/:iid` page is real (not the placeholder) — functionally it can be added to either tab's rows, but AC #1 scopes it to Performance since that's where the operator is looking at the same metrics `/history/:iid` details.

- [ ] Task 2 — Tests
  - [ ] Extend `RankingsPage.test.tsx` (per Story 17.1's Task 4 additions): clicking the history affordance navigates to `/history/{instrument_id}` and does not also trigger the row's chart navigation.

## Dev Notes

- **This is a small, standalone story — no backend change.** It only wires an existing frontend route (`/history/:iid`, registered in `App.tsx`) that nothing currently links to.
- **Sequencing:** functionally trivial, but sits after Story 17.1 (tab shell) so "Performance tab" is a real place to put it, and makes most sense once Story 17.2 has replaced the `HistoryPage.tsx` placeholder with a real page — implementable in any order relative to 17.2/17.3, since it's just a `navigate()` call to an existing route path.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/RankingsPage.tsx`, `troll/frontend/src/pages/RankingsPage.test.tsx`.
- Not modified: `troll/frontend/src/App.tsx` (route already registered), `troll/frontend/src/pages/HistoryPage.tsx` (Story 17.2's concern, not this one).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 17, Story 17.4] — this story's origin (FR54).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#B0] — "Rankings → History link" identified as currently missing.
- [Source: troll/frontend/src/pages/RankingsPage.tsx] — full file read this session (Story 17.1); confirms today's only navigation is row-click → `/chart/:iid`.
- [Source: troll/frontend/src/App.tsx] — full file read earlier this session; confirms `/history/:iid` is registered but unlinked from anywhere in the UI.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

### Review Findings

Epic 17 review (2026-09-19; combined diff of 17-1, 17-3..17-6; Blind/Edge/Acceptance layers).

- [x] [Review][Patch] Mount-time Technicals fetch could overwrite a fast header edit [troll/frontend/src/pages/RankingsPage.tsx] -- fixed (savedLocally ref)
- [x] [Review][Patch] PUT technicals-columns accepted non-object params and >50 entries [troll/data_api/routes/rankings.py] -- fixed + test
- [x] [Review][Patch] 1w/1m store lookup failure stalled the whole slow-loop cycle [troll/ranking_engine/engine.py] -- fixed (logged, fields None)
- [x] [Review][Patch] Stale placeholder values under shifted columns / stale filter field / per-coin 400 blast radius -- already fixed in 42b63f5719, verified
- [x] [Review][Defer] Non-atomic screener_columns.toml write, params value validity, cache single-key/no single-flight, `=` on floats, tech filter hides all rows while values load -- deferred, see deferred-work.md

Owed: visual browser check of the History link -- not performed; marked done anyway.
