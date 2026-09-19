# Story 19.1: Explicit `venue` field in the data model

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a frontend developer,
I want `venue` surfaced as its own field rather than an implicit `instrument_id` suffix,
so that the screener and chart can filter/group/label by exchange without string-parsing IDs.

## Acceptance Criteria

1. **`venue: string` is added to `troll/frontend/src/api/schema.ts` and every `data_api` response shape that already includes an `instrument_id`** (`routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`, `routes/indicator_series.py`, `routes/rankings.py`) — confirmed via this session's grep that none currently has one.
2. **`venue` is derived from `instrument_id`'s existing `"{SYMBOL}.{VENUE}"` suffix (Nautilus's own convention, confirmed in `crates/model/src/identifiers/instrument_id.rs`), not a new independent field requiring its own storage** — a pure derivation (`instrument_id.rsplit(".", 1)[1]`), computed at the API-response-shaping boundary, never persisted as a separate catalog/database column.
3. **Every existing dYdX-only instrument's `venue` reads `"DYDX"`** — purely additive, zero behavior change for today's data.
4. **`rankings:live`'s WS relay (hand-written per AD-F5's exception) also gains `venue`** per rank entry, with its documented-source comment updated to note the derivation.

## Tasks / Subtasks

- [x] Task 1 — Backend: derive and add `venue` (AC: #1, #2, #4)
  - [x] Add a small shared helper (e.g. in a module already common to the route files, or a new tiny `troll/data_api/venue.py`): `venue_of(instrument_id: str) -> str` — a pure `rsplit`, no new dependency.
  - [x] Apply it in every response model listed in AC #1 and in `ranking_engine`'s `_current_ranks()`/`_build_rankings_message()` row-building (the same function Story 17.3 also touches — coordinate to avoid a merge conflict, but these are independent additive fields).

- [x] Task 2 — Frontend: surface `venue` (AC: #1)
  - [x] Add `venue: string` to `IndicatorCatalogEntry`-adjacent response types in `schema.ts` wherever `instrument_id` already appears; regenerate via the existing OpenAPI codegen path (AD-F5) rather than hand-editing generated sections.

- [x] Task 3 — Tests
  - [x] A test confirming `venue_of("BTC-USD-PERP.DYDX") == "DYDX"` and that a malformed/venue-less string (should never occur in practice, since every catalog instrument_id is Nautilus-constructed) fails loudly rather than silently returning something wrong.

## Dev Notes

- **This story is purely additive — it changes no existing field, removes nothing, and requires no data migration.** It exists so Epic 19's later stories (19.3-19.5) have something to key off explicitly rather than parsing `instrument_id` ad hoc in multiple places.
- **Do not build a venue enum/registry in this story** — that's Story 19.6's CEX/DEX registry, a separate concern layered on top of this one's plain string field.

### Project Structure Notes

- Modified: `troll/data_api/routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`, `routes/indicator_series.py`, `routes/rankings.py`, `troll/ranking_engine/engine.py` (`_current_ranks`), `troll/frontend/src/api/schema.ts`/`openapi.json`.
- New: a small `venue_of()` helper (exact placement left to the implementer — avoid a new module if an existing shared one fits).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 19, Story 19.1] — this story's origin (FR61).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#Part D, D1] — the `venue`-as-explicit-field design decision.
- [Source: crates/model/src/identifiers/instrument_id.rs] — confirmed `InstrumentId` = `"{SYMBOL}.{VENUE}"`, `rsplit_once('.')` parsing convention this story's `venue_of()` mirrors.
- [Source: troll/data_api/routes/*.py] — grepped this session; confirmed no `venue` field exists anywhere in current response shapes.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Story premise correction: candles/snapshots/indicator-series/indicator-values responses did NOT include `instrument_id` (it's a path param), so `venue: str` was added as a response-level field on those four models; rank entries (`rankings:live` + `/api/rankings` passthrough) get per-row `venue`.
- `venue_of()` lives in new `ml_signals/venue.py` (shared by data_api + ranking_engine); raises `MalformedInstrumentId` (ValueError) on venue-less ids; data_api maps it to HTTP 400 via an app exception handler.
- Tests run in the troll-data_api image against the worktree: 296 pass; `test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay` needs a live Redis on 127.0.0.1:6379 (unavailable here — environmental).

### File List

- troll/ml_signals/venue.py (new), troll/ml_signals/tests/test_venue.py (new)
- troll/data_api/app.py, routes/{candles,snapshots,indicator_series,indicators}.py, tests/test_candles.py
- troll/ranking_engine/engine.py, tests/test_engine.py
- troll/frontend/openapi.json, troll/frontend/src/api/schema.ts
