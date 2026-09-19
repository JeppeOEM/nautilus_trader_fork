# Story 20.3: Alerts list view

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to see all my alerts and their status,
so that I can review or delete them.

## Acceptance Criteria

1. **Built after Epics 17-19, and after Stories 20.1/20.2** — same deferral rule.
2. **A simple list view (not a full manager UI)** showing each alert's condition, status (active/triggered/expired), and a delete button.
3. **Uses Story 20.1's existing list/delete routes** — no new persistence, no new backend logic beyond what Story 20.1 already built.

## Tasks / Subtasks

- [x] Task 1 — List view (AC: #2, #3)
  - [x] A new page or panel rendering `GET /api/alerts`'s results as a plain list — condition summary (human-readable, e.g. "BTC-USD-PERP.DYDX price crosses 65000"), status derived from Story 20.2's fired/expired state, delete button calling the existing delete route.

- [x] Task 2 — Tests
  - [x] A test rendering a known set of alerts (active, triggered, expired) and confirming each renders its correct status and that delete removes it from the list.

## Dev Notes

- **This is the smallest story in Epic 20** — a thin read/delete view over Story 20.1's already-built persistence. No new backend work beyond exposing "status" (which Story 20.2's evaluation engine already tracks internally) through the existing GET route if it isn't already included in the response shape.

### Project Structure Notes

- New: an alerts list view component/page.
- Not modified: Story 20.1's persistence/routes, Story 20.2's evaluation engine (both consumed, not changed).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 20, Story 20.3] — this story's origin (FR69).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A6] — "an Alerts list view (just a simple list, not the full manager)."
- [Source: _bmad-output/implementation-artifacts/20-1-alert-creation-dialog.md, 20-2-local-alert-evaluation-engine.md] — the persistence and status this story reads.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

`AlertsPage.tsx` at `/alerts` (nav link added) over GET/DELETE /api/alerts; status is derived server-side (active/triggered/expired) in the Story 20.1 response, no new persistence. Test: `AlertsPage.test.tsx`.

### File List

- troll/frontend/src/pages/AlertsPage.tsx
- troll/frontend/src/pages/AlertsPage.test.tsx
- troll/frontend/src/App.tsx

### Review Findings

Code review 2026-09-19 (adversarial + edge-case + acceptance layers, run inline over `git diff 14a459ccd6..HEAD`). 0 decision-needed, 3 patch (all applied), 3 defer, rest dismissed.

Clean — no findings.
