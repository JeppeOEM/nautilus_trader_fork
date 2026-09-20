# Story 22.11: Nightly catalog consolidation for every venue

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want each closed UTC day's minute-sized Parquet files merged into one file per (data type, instrument) for every venue,
so that the archive's file count grows by hundreds a day, not hundreds of thousands (audit D-36: inodes, backup and read cost all scale with files, not bytes).

## Acceptance Criteria

1. **One job, every venue.** `consolidate_catalog` (today `troll/dydx_collector/consolidate_catalog.py`, moved to `troll/collector_core/consolidate_catalog.py` here if 22.3 has not already moved it) runs over every `data/<type>/<instrument>/` leaf of the shared catalog — `.DYDX`, `.BYBIT` and `.HYPERLIQUID` ids alike, every data type present — and after a run every closed UTC day of every leaf holds exactly one file plus at most one midnight-crossing file. Today's files are never touched. Idempotent: a second run reports `0 day(s)`.
2. **Safety guards stay and are tested.** One schema per day or the day is refused with `error_ledger.record("consolidate.mixed_schema", ...)` (D-24); row count of the merged file is verified against the sources *before* the sources are deleted (`consolidate.row_count` on mismatch, nothing deleted); an interrupted run (merged file renamed, sources still present) is finished on the next run without duplicating rows. Readers keep working on merged files unchanged: `catalog_stats.data_file_ranges`, `catalog_stats.query_second_ohlc`, `build_candles.rebuild_instrument`, and Nautilus `ParquetDataCatalog.query(DydxSecondSnapshot, ...)` all return the same rows for a consolidated day as for the original small files (tested on a tmp catalog).
3. **Scheduled and measured.** `make consolidate` exists (it does) and a cron line is documented next to it; the first full run on the VPS and one nightly run are timed (wall, peak RSS via `/usr/bin/time -v`, files and MB before/after) and written into `troll/docs/DATA_INTEGRITY_AUDIT.md` D-36 as **measured**, or the entry says NOT measured. The run must stay inside the box's memory headroom (MEM-01: one (type, instrument, day) in memory at a time — keep it that way).
4. **Backup target exists.** A `make backup-catalog` target copies closed-day files (never today's) to object storage with `rclone sync` (or `restic`); remote name and bucket come from `.env`/env vars, never committed. Documented next to `make consolidate` with the reasoning: consolidation first, or the upload is millions of tiny objects (D-33 still has no backup).

## Tasks / Subtasks

- [ ] Task 1 — move + generalise (AC: #1)
  - [ ] `git mv troll/dydx_collector/consolidate_catalog.py troll/collector_core/consolidate_catalog.py` (and its test to `collector_core/tests/`) unless 22.3 already did; update `troll/Makefile` `consolidate` target and the audit runbook line. The module already has no dYdX-specific code: `leaf_dirs` walks every `data/<type>/<instrument>` directory, so Bybit/Hyperliquid leaves are covered by construction — prove it with a test that seeds a `.BYBIT` and a `.HYPERLIQUID` instrument (`DydxSecondSnapshot` rows, same class for all venues) plus one `MarkPriceUpdate` leaf and asserts one file per closed day in each.
  - [ ] Keep `--data-type` and `--days` flags; default remains all types, all closed days.
- [ ] Task 2 — reader equivalence tests (AC: #2)
  - [ ] Extend `test_consolidate_catalog.py`: after consolidation, `ParquetDataCatalog(path).query(data_cls=DydxSecondSnapshot, identifiers=[iid])` returns the same `ts_event` sequence as before (the catalog's dataset reader must accept the merged file: same schema, same Arrow metadata — `pa.concat_tables` keeps the first table's schema metadata; assert `pq.read_schema(merged).metadata == pq.read_schema(source).metadata`).
  - [ ] `build_candles.rebuild_instrument` over a consolidated day equals the pre-consolidation rebuild (compare `candle_store.window` output for every bar size).
  - [ ] Midnight-crossing file: seed a batch spanning 23:59:30–00:00:30, assert it is left alone and both neighbouring days still consolidate their wholly-inside files.
- [ ] Task 3 — cron + measurement (AC: #3)
  - [ ] Makefile comment already carries `7 3 * * * cd /path/to/troll && make consolidate >> consolidate.log 2>&1`; add the same to `troll/README.md`'s operations section.
  - [ ] Operator (not the agent): first VPS run with `/usr/bin/time -v`, record numbers in D-36. Until then the story is `awaiting-operator` with that as the owed action.
- [ ] Task 4 — backup target (AC: #4)
  - [ ] `backup-catalog:` in `troll/Makefile`: `rclone sync ./dydx_collector/catalog/data "$(RCLONE_REMOTE):$(RCLONE_BUCKET)/catalog/data" --exclude "*/$(shell date -u +%Y-%m-%dT)*" --transfers 8 --fast-list`; refuse (exit 1 with a message) when `RCLONE_REMOTE`/`RCLONE_BUCKET` are unset. No credentials in the repo (SEC-01 spirit; `.env` is gitignored).
  - [ ] Document R2/B2 as the cheap options (free tiers cover a few GB; prices change — say so) and that `candles_*.db` needs no backup (derived, `make build-candles` rebuilds it).

## Dev Notes

### Why plain pyarrow and not `ParquetDataCatalog.consolidate_data_by_period`

Tried first (Nautilus built-in, "use built-ins first"). It raises `NotImplementedError` from `ArrowSerializer._deserialize_rust` for `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate` (three of the five per-coin data types), round-trips every row through Python objects (~100 MB per coin-day of 20-level books), reads through the first-file-schema `pds.dataset` that caused D-24, and deletes the source files before anything can be verified. The pyarrow path in `consolidate_catalog.py` merges tables as-is (schema + metadata preserved), writes a `.tmp`, verifies the row count, `os.replace`s into the catalog filename convention (`_timestamps_to_filename(min ts_init, max ts_init)` imported from `nautilus_trader.persistence.catalog.parquet`), and only then unlinks the sources. Do not switch back.

### Measured so far (local, 2026-09-20)

BTC copy, 5 data types, 97 closed coin-type-days: 7.7 s wall, 9,007 → 351 files, 61 → 21 MB; closed-day rows identical before/after (168,771 = 168,771, every field). Per-file cost is what matters (~0.5 ms/file regardless of rows); a day of 30 coins × 5 types ≈ 216k files ≈ 2–4 min per nightly run — an estimate, not a VPS measurement.

### What the collector is doing meanwhile

It writes today's files only (flush at :02 past each minute, `_seconds_until_next_flush`), so a nightly run never races it. The catalog's disjoint-interval check on write is per file interval; a merged closed-day file never overlaps today's writes.

### Project Structure Notes

- New/moved: `troll/collector_core/consolidate_catalog.py`, `troll/collector_core/tests/test_consolidate_catalog.py`.
- Modified: `troll/Makefile` (`consolidate`, new `backup-catalog`), `troll/README.md`, `troll/docs/DATA_INTEGRITY_AUDIT.md` (D-36 numbers, D-33 backup note), `troll/dydx_collector/repair_catalog.py` only if its import path changes.
- Unchanged: `ml_signals/catalog_stats.py` readers (they parse `start_end` stems, which merged files keep), `candle_store`, all collectors.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.11] — ACs.
- [Source: troll/dydx_collector/consolidate_catalog.py] — the job as built; [Source: troll/dydx_collector/tests/test_consolidate_catalog.py] — the three existing tests (closed days → one file; mixed schema refused; interrupted run finished).
- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md D-24, D-33, D-36] — mixed-schema hazard, missing backup, file-count register entry.
- [Source: nautilus_trader/persistence/catalog/parquet.py `consolidate_data_by_period`, `_timestamps_to_filename`] — why the built-in was rejected; the filename contract.
- [Source: troll/CLAUDE.md DATA-05, DATA-07, MEM-01, SEC-01, TEST-01/03] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
