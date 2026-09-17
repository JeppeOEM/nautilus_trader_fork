# Story 19.5: Rankings/screener venue column and filter

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want to see and filter by exchange in the screener,
so that I can distinguish a coin's dYdX row from its Bybit or Hyperliquid row.

## Acceptance Criteria

1. **`RankingsPage.tsx` shows a `venue` column**, populated from Story 19.1's explicit `venue` field on each `rankings:live` row.
2. **Story 17.6's filter panel accepts `venue` as a filterable field** (e.g. `venue = BYBIT`), using its existing `<field> <operator> <value>` mechanism — no new filter UI needed, just one more available field.
3. **This is still the Coins model (one row per coin), not a row-per-exchange-pair model** — a coin available on multiple venues still gets one row per `instrument_id` (i.e., per venue-qualified instrument, e.g. `BTC-USD-PERP.DYDX` and `BTCUSDT.BYBIT` are two distinct rows, not merged into one "BTC" row) — confirmed as the correct model per the original spec's own Coins-vs-CEX/DEX-screener distinction, since aggregating across venues into one row is a materially bigger feature (cross-venue aggregation) not in scope here.

## Tasks / Subtasks

- [ ] Task 1 — Venue column (AC: #1, #3)
  - [ ] Add a `venue` column to `RankingsPage.tsx`'s rendering (Performance tab, per Story 17.1's design — this is a base field, not a Technicals indicator column), reading the `venue` field Story 19.1 adds to each row.
- [ ] Task 2 — Filter integration (AC: #2)
  - [ ] Confirm `venue` appears in Story 17.6's field dropdown automatically (it should, if that story's field list is derived generically from row keys) — add it explicitly if the field list is instead a hardcoded subset.
- [ ] Task 3 — Tests
  - [ ] A test with rows from multiple venues confirming the venue column renders correctly and a `venue = X` filter narrows to only that venue's rows.

## Dev Notes

- **This story has no real technical risk** — it's a thin consumer of Story 19.1's field and Story 17.6's filter mechanism, both already built. Its only value is confirming end-to-end that a multi-venue row set actually displays/filters correctly once real Bybit/Hyperliquid data exists (Stories 19.3/19.4).
- **Sequencing:** functionally depends on Story 19.1 (the `venue` field must exist) and benefits from Story 17.6 (filter panel) already existing, though the column itself could ship before the filter integration if sequencing requires it.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/RankingsPage.tsx`.
- Not modified: no backend change (this story is a pure consumer of Story 19.1's field).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 19, Story 19.5] — this story's origin (FR65).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#Part D, D5, #B6] — the venue column/filter design and the "still the Coins model" clarification.
- [Source: _bmad-output/implementation-artifacts/19-1-explicit-venue-field-in-the-data-model.md, 17-6-filter-panel.md] — the two pieces this story combines.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
