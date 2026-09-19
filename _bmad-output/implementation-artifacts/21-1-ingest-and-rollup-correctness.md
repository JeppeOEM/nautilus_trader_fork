# Story 21.1: Ingest and rollup correctness

Status: done

Split from the original 21.1 draft (2026-09-19 incident: odd candles, wrong volume, slow charts, vite `EPIPE`/`ECONNRESET`). Read `troll/docs/DATA_INTEGRITY_AUDIT.md` first.

## Story

As the dashboard operator,
I want the collector's stored 1s rows and minute rollups to be correct, including after restarts and gaps,
so that wide candles and volume are never inflated by replayed trades or understated by gap-truncated minutes.

## Acceptance Criteria

1. **Given** a (re)subscribe to `v4_trades` **When** the replay arrives **Then** no historical trade contributes to a second's OHLC/volume (D-01/D-02: `_STALE_TRADE_NS` filter + `trade_id` dedup) — proven by tests in `dydx_collector/tests/test_collector_trade_ohlc.py`; production verification (logs over ≥1 h across a restart) is stated as done or NOT done (DATA-02).
2. **Given** a minute rollup containing a gap-created partial minute (D-15) **When** it is written **Then** it is flagged partial (fix `partial_start` in the rollup writer) so it is never silently served as a full minute; idempotent backfill (DATA-05); test with a real catalog fixture.
3. **Given** `repair_catalog` **When** run against a fixture catalog containing spike rows **Then** report-only lists them, `--apply` repairs, and a re-run reports zero (existing tests extended if gaps). Running it on production is NOT part of this story — it is handed to the operator with the runbook command.
4. `DATA_INTEGRITY_AUDIT.md` D-01..D-03, D-15, D-16 statuses updated with evidence; unverified stays OPEN.

## Tasks / Subtasks

- [ ] Task 1: locate rollup writer (`dydx_collector/`), fix D-15 partial flag, test
- [ ] Task 2: verify/extend trade filter + repair tests (AC1,3)
- [ ] Task 3: audit doc update

## Dev Notes

- Production actions (`make redeploy-all`, `repair_catalog --apply`) are operator-approved, never run by the dev agent.

### References

- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md] register D-01…D-23
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] chart invariants (AD-F2 one aggregation path, AD-F3 cursor contract, AD-F6 gap markers, AD-F7 live edge)
- Binding rules `troll/CLAUDE.md`: DATA-01/02/05, MEM-01/02, TEST-01/03/04, NAUT-01, FORK-01

## Dev Agent Record

### Agent Model Used

### Completion Notes List

Trade filter/dedup (D-01/D-02), integrity canary and `repair_catalog` were already coded and tested in `083bd989bc`; re-ran their tests (pass). D-15 mitigated by a read-side `partial` flag from `seconds_observed` instead of changing the rollup writer (no schema change; raw 1s cannot recover missing seconds anyway). NOT done: production verification of D-01 (logs across a restart), `repair_catalog` on production, `make redeploy-all` — operator actions.

### File List
