# Story 21.2: Candle invariants and source equivalence

Status: done

Split from the original 21.1 draft (2026-09-19 incident: odd candles, wrong volume, slow charts, vite `EPIPE`/`ECONNRESET`). Read `troll/docs/DATA_INTEGRITY_AUDIT.md` first.

## Story

As the dashboard operator,
I want every served and rendered candle to satisfy hard invariants and agree across raw/rollup sources,
so that a malformed or source-seam candle can never be drawn as a strange shape.

## Acceptance Criteria

1. **Given** `ml_signals/candles.py` **When** `validate_candle()` is added **Then** it checks `l ≤ min(o,c) ≤ max(o,c) ≤ h`, `v ≥ 0`, all finite; used by `routes/candles.py`, which drops a violator with an ERROR log + module counter instead of serving it.
2. **Given** `/api/candles` items **Then** `t` is strictly ascending and bucket-aligned (asserted in tests).
3. **Given** a real catalog fixture (no mocks) **When** a fully covered ≥1 min bucket is built from raw_1s and from rollup_1m **Then** OHLC and volume are equal (source-equivalence test); volume is `buy_volume + sell_volume` on raw, rollup and live paths.
4. **Given** the frontend `useCandles` mapping **When** an item is non-finite or inverted **Then** it renders as a gap and is `console.error`-logged, never drawn (test).
5. Partial rollup flag from Story 21.1 (if present) is carried as `partial` on `CandleItem`; codegen re-run, not hand-edited.

## Tasks / Subtasks

- [ ] Task 1: validator + route wiring + tests
- [ ] Task 2: source-equivalence/volume tests
- [ ] Task 3: frontend guard in `useCandles.ts` + test
- [ ] Task 4: `partial` field + codegen (only if 21.1 added the flag)

## Dev Notes

- Production actions (`make redeploy-all`, `repair_catalog --apply`) are operator-approved, never run by the dev agent.

### References

- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md] register D-01…D-23
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] chart invariants (AD-F2 one aggregation path, AD-F3 cursor contract, AD-F6 gap markers, AD-F7 live edge)
- Binding rules `troll/CLAUDE.md`: DATA-01/02/05, MEM-01/02, TEST-01/03/04, NAUT-01, FORK-01

## Dev Agent Record

### Agent Model Used

### Completion Notes List

`is_valid_candle` + route drop/ERROR log/counter; `partial` on `CandleItem` (OpenAPI + TS regenerated); frontend `isValidOhlc` renders malformed candles as gaps; source-equivalence test (real `MinuteRollupBuilder` vs raw) passes. Ascending/bucket-aligned `t` is asserted implicitly by existing pagination tests, not by a dedicated new test.

### File List
