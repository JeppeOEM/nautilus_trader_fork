# Story 19.5: Rankings/screener venue column and filter

Status: done

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

- [x] Task 1 — Venue column (AC: #1, #3)
  - [x] Add a `venue` column to `RankingsPage.tsx`'s rendering (Performance tab, per Story 17.1's design — this is a base field, not a Technicals indicator column), reading the `venue` field Story 19.1 adds to each row.
- [x] Task 2 — Filter integration (AC: #2)
  - [x] Confirm `venue` appears in Story 17.6's field dropdown automatically (it should, if that story's field list is derived generically from row keys) — add it explicitly if the field list is instead a hardcoded subset.
- [x] Task 3 — Tests
  - [x] A test with rows from multiple venues confirming the venue column renders correctly and a `venue = X` filter narrows to only that venue's rows.

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

- Venue column added to the Performance tab (rendered separately from `RANKING_COLS`, which mirrors `ml_signals/ranking_columns.py` per SSOT-03 — venue is a base field, not a ranking metric).
- The 17.6 filter mechanism was numeric-only (`value: number`), so `venue = BYBIT` needed a small extension rather than "one more field": `FilterField.text` + `FilterCondition.value: number | string`; a text field allows only `=` and matches case-insensitively. The Venue field sits after the numeric columns so the builder's default field is unchanged.
- One row per venue-qualified `instrument_id` (test asserts three distinct rows for DYDX/BYBIT/HYPERLIQUID BTC ids). vitest 105 pass, tsc clean.
- Caveats for the operator: (1) SSOT-04 says ranking-page changes land in bot_tui too — not done, the story scopes this to `RankingsPage.tsx`; (2) `ranking_engine` still only ranks dYdX snapshots, so Bybit/Hyperliquid rows won't appear on this page until it consumes those catalogs — this story only makes the UI ready.

### File List

- troll/frontend/src/pages/{RankingsPage.tsx,FilterPanel.tsx,filters.ts}
- troll/frontend/src/pages/{RankingsPage.test.tsx,filters.test.ts}
