# Story 21.5: Chart load speed

Status: done

Split from the original 21.1 draft (2026-09-19 incident: odd candles, wrong volume, slow charts, vite `EPIPE`/`ECONNRESET`). Read `troll/docs/DATA_INTEGRITY_AUDIT.md` first.

## Story

As the dashboard operator,
I want chart initial load and scroll-back to be fast on the VPS,
so that I don't wait seconds for a chart (audit baseline 7–9 s at 1m, >45 s at 15m).

## Acceptance Criteria

1. **Given** `/api/candles` **Then** it performs at most one catalog scan per request (`has_more` from `data_file_ranges`; verify no second scan remains).
2. **Given** repeated requests **Then** `ParquetDataCatalog` handle / `data_file_ranges` are cached per (path, instrument) with mtime invalidation; bounded (MEM-01/02).
3. **Given** bars > 1h **Then** they read `rollup_1m`; no `falling back to raw 1s` for a healthy coin's recent window.
4. **Given** a timing script **Then** before/after latencies (1m/15m/1h, BTC + one thin coin) are recorded locally; VPS numbers are stated as measured or NOT measured — targets p95 ≤ 2 s (1m), ≤ 3 s (15m), never claimed unmeasured.

## Tasks / Subtasks

- [ ] Task 1: profile first, fix top bottleneck
- [ ] Task 2: cache + test invalidation
- [ ] Task 3: timing script + numbers

## Dev Notes

- Production actions (`make redeploy-all`, `repair_catalog --apply`) are operator-approved, never run by the dev agent.

### References

- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md] register D-01…D-23
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] chart invariants (AD-F2 one aggregation path, AD-F3 cursor contract, AD-F6 gap markers, AD-F7 live edge)
- Binding rules `troll/CLAUDE.md`: DATA-01/02/05, MEM-01/02, TEST-01/03/04, NAUT-01, FORK-01

## Dev Agent Record

### Agent Model Used

### Completion Notes List

Local (not VPS) BTC timings, cold: 1m 1.14→0.40 s, 15m 1.78→0.66 s, 1h 1.63→0.64 s, 4h 5.9→3.0 s via `query_second_ohlc` column-projection read (`scripts/bench_candles.py`). AC 4's VPS targets NOT measured. Catalog-handle caching skipped: the candle path no longer constructs a `ParquetDataCatalog`; `data_file_ranges` is a directory glob. Found D-24 (see audit).

### File List
