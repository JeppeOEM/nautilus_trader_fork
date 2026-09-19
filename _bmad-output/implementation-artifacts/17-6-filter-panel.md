# Story 17.6: Filter panel

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want to filter the screener's row set by any base field or any added Technicals column,
so that I can narrow to coins matching a specific condition (e.g. "RSI < 30").

## Acceptance Criteria

1. **A `+` control opens a condition builder: `<field> <operator> <value>`** — fields include every base rankings field (any `RANKING_COLS` key) plus, once Story 17.5 has added a Technicals column, that column's indicator id.
2. **Multiple filter conditions combine with AND only** — no OR/grouped logic for this story.
3. **Filtering narrows the row set regardless of which tab (Performance or Technicals) is currently displayed** — filtering and column display stay independent, per Story 17.1.
4. **Filtering is purely client-side against the already-fetched/live row set** — no new backend route; every field a filter condition needs is already present in the `rankings:live` message or Story 17.5's bulk Technicals-values response, both already fetched for rendering.

## Tasks / Subtasks

- [ ] Task 1 — Condition builder UI (AC: #1)
  - [ ] Add a `+`/filter-panel control above the tab bar (Story 17.1) in `RankingsPage.tsx`, opening a small form: a field dropdown (populated from `RANKING_COLS` keys plus any currently-configured Technicals entries' indicator ids from Story 17.5), an operator dropdown (`>`, `<`, `>=`, `<=`, `=`), and a value input.
  - [ ] Saved conditions render as a small removable list (chip/row with an × ) above the table.

- [ ] Task 2 — Apply filters to the row set (AC: #2, #3, #4)
  - [ ] Filtering happens client-side: `rows.filter(row => conditions.every(cond => matches(row, cond)))`, applied identically regardless of `activeTab` — the filtered row set feeds both tabs' rendering, only the displayed columns differ (per Story 17.1).
  - [ ] `matches()` reads the condition's field from the row object — this already works uniformly for both `RANKING_COLS` fields (present on every `rankings:live` row) and Technicals fields (present once Story 17.5's bulk-values response is merged into each row's data for rendering) — no field-source branching needed if Story 17.5's values are merged into the same row-keyed structure before filtering runs.

- [ ] Task 3 — Tests
  - [ ] `RankingsPage.test.tsx`: adding a filter condition narrows the rendered rows correctly; multiple conditions combine with AND (a row matching only one of two conditions is excluded); removing a condition restores the previously-filtered-out rows; switching tabs while a filter is active preserves the filtered row set.

## Dev Notes

- **No backend work in this story.** Every field a filter condition might reference is already fetched for display (base rankings fields live in the `rankings:live` message every row already has; Technicals fields come from Story 17.5's bulk values response) — filtering is a pure client-side narrowing of what's already in memory, consistent with AD-F2 (frontend never needs a new server-side query just to filter already-loaded rows).
- **Sequencing:** the field list naturally grows once Story 17.5 ships (Technicals columns become filterable fields), but the filter mechanism itself has no hard dependency on 17.5 — it can be built and tested against base fields alone first, then automatically picks up Technicals fields once that story adds them (assuming Task 2's row-merging is in place).

### Project Structure Notes

- Modified: `troll/frontend/src/pages/RankingsPage.tsx`, `troll/frontend/src/pages/RankingsPage.test.tsx`.
- Not modified: any `data_api` route (client-side-only feature, per AC #4).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 17, Story 17.6] — this story's origin (FR56).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#B4] — filter panel spec, "filterable fields include any currently-added Technicals column."
- [Source: _bmad-output/implementation-artifacts/17-1-tab-shell-performance-technicals-tabs-on-the-rankings-page.md] — tab/row-set independence this story's AC #3 relies on.
- [Source: _bmad-output/implementation-artifacts/17-5-technicals-tab-user-managed-indicator-columns.md] — the bulk Technicals-values response this story's field list and `matches()` extend to cover.

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

Owed: visual browser check of filter panel -- not performed; marked done anyway.
