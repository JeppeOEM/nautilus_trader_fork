# Story 25.1: `archive/` context: `ArchiveDay`, one deleter, one rewriter, one writer per leaf

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 25). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 25.1".

## Story

As the platform operator,
I want the nightly maintenance to be one saga over an explicit day state machine, with one code path that deletes files and one that rewrites them,
So that a rebuild can never zero rows over an archive gap, a reconcile can never pass on an unrebuilt day, and a repair can never collide with a running collector.

## Acceptance Criteria

1. **Given** `collector_core/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,nightly,backfill_bars,migrate_open_interest,measure_lag}.py`, `dydx_collector/normalize_snapshot_schema.py`, the `catalog_stats` diagnostics (`data_file_ranges`, `find_gaps`, `likely_outages`, `coverage`) and `DydxCollector._prune_loop`
**When** the story ships
**Then** `platform/archive/` holds `domain/` (`ArchiveDay` with `DayStatus`, `RetentionPolicy`, `ReconciliationResult`, `ArchiveGap` handling over `kernel.archive_markers`), `application/` (`rebuild_day`, `consolidate_day`, `reconcile_day`, `prune`, `backfill_bars`, `diagnostics`, the `nightly` saga with `Step`/`StepResult`), `infrastructure/` (`catalog_files.py` — the `CatalogFiles` adapter, `klines_<venue>.py` over `kernel.venue_http`) and `tools/` (`measure_lag`, `migrate_open_interest`, `normalize_snapshot_schema`); every operator CLI keeps its arguments under `python -m archive.<tool>` with the old module paths forwarding; `make nightly`, `make consolidate`, `make prune`, `make backup-catalog` and the cron line in the README are updated

2. **Given** four in-place `pq.write_table` rewrite sites and two `write_data()` callers in the tools
**When** the story ships
**Then** `CatalogFiles.rewrite(path, table)` (temp-then-rename, Arrow metadata preserved, zstd via `kernel.parquet_compat`) is the only in-place rewriter and the four sites call it; `backfill_bars` and `repair_catalog` remain the only offline `write_data()` callers and are listed in the data dictionary; a test proves a rewritten file's schema metadata and row order are unchanged

3. **Given** AD-D9's state machine and AD-D18's leaf rule
**When** the story ships
**Then** `verified_days` (through the `VerifiedDays` port from 24.1) is the only persisted day status; `reconcile_day` refuses, ledgering `reconcile.not_rebuilt`, unless invoked by a saga run whose `rebuild_day` for the same (venue, day) succeeded (`StepResult` passed in-process; standalone `compare_klines` requires `--rebuilt-by <run id>`); `rebuild_day` leaves every row inside an `ArchiveGap` span untouched and reports the count; `RetentionPolicy` is the only code that deletes a catalog file and covers verified-and-aged `trade_tick/`, dropped-instrument retention (`non_config_retain_hours`, read from the venue plan file) and per-instrument `order_book_deltas` retention, so `DydxCollector._prune_loop` is deleted; the collector writes `<catalog>/.capture-<venue>.lock` for its whole run (the one capture edit in this story, with a test), `repair_catalog` refuses with `repair.capture_running` while that lock is held, and no archive tool writes a file whose `ts_init` span intersects the current UTC day (asserted in `CatalogFiles`)

4. **Given** MR2 and MR4
**When** the story is merged
**Then** the moved modules are pure re-export shims with `REMOVE_AFTER = "25-3-..."`, the collector image `COPY`s `archive`, the Makefile test lists include `archive/tests`, `docs/DATA_DICTIONARY.md` §6 and `DATA_INTEGRITY_AUDIT.md` D-36/D-45..D-51 cite the new paths, and `platform/CLAUDE.md` DATA-05/DATA-06 name `archive.` tools

## Tasks / Subtasks

- [ ] Task 1 — `platform/archive/` (AC: #1)
  - [ ] `domain/`: `archive_day.py` (`ArchiveDay(venue, instrument, day)`, `DayStatus` enum `provisional|rebuilt|verified|mismatched|released`, transitions as the spine's state diagram), `retention.py` (`RetentionPolicy` covering `trade_tick/` (age AND verified), dropped-instrument retention `non_config_retain_hours`, per-instrument `order_book_deltas` `retain_hours` — read from the venue plan file), `reconciliation.py` (`ReconciliationResult`, exact compare), gap handling over `kernel.archive_markers`.
  - [ ] `application/`: `rebuild_day.py` (from `rebuild_seconds.py`), `consolidate_day.py`, `reconcile_day.py` (from `compare_klines.py`), `prune.py` (from `prune_catalog.py` + the dYdX `_prune_loop` cases), `repair.py`, `backfill_bars.py`, `diagnostics.py` (`find_gaps`, `likely_outages`, `coverage` from `catalog_stats`), `nightly.py` (saga; `Step`/`StepResult`; carries `rebuild_day`'s result to `reconcile_day`). `infrastructure/`: `catalog_files.py` (`CatalogFiles.rewrite`, `.delete`, leaf/lock helpers), `klines_<venue>.py`, `maintenance_lock.py`. `tools/`: `measure_lag`, `migrate_open_interest`, `normalize_snapshot_schema`.
  - [ ] Old module paths forward `main` (shims); `Makefile` targets and README cron line → `python -m archive.<tool>`.
- [ ] Task 2 — one rewriter (AC: #2)
  - [ ] Replace the four `pq.write_table` sites (`rebuild_seconds.py:329`, `consolidate_catalog.py:219`, `migrate_open_interest.py:98`, `normalize_snapshot_schema.py:77`) with `CatalogFiles.rewrite`; zstd via `kernel.parquet_compat`; metadata/row-order preservation test.
- [ ] Task 3 — state machine and leaf rule (AC: #3)
  - [ ] `reconcile_day` requires a `rebuilt_by: RunId` argument produced by the saga's successful `rebuild_day` step (or `--rebuilt-by` on the CLI); otherwise ledger `reconcile.not_rebuilt` and exit non-zero. `rebuild_day` skips rows inside `ArchiveGap` spans and reports the count. `RetentionPolicy` is the only deleter: remove `DydxCollector._prune_loop` (`dydx_collector/collector.py:595-625`) and `_prune_candidates`/`_prune_all_instruments`/`_prune_delta_retention`; the nightly `prune` step reads the dYdX plan file for the retention attributes.
  - [ ] Capture lock: `Collector.run()` creates `<catalog>/.capture-<venue>.lock` (pid + started_at) and removes it on clean shutdown; `repair.py` refuses with `repair.capture_running` while it exists for that venue; `CatalogFiles.rewrite`/`delete` assert the file's `ts_init` span does not intersect the current UTC day.
- [ ] Task 4 — shims, images, lists, docs (AC: #4)
  - [ ] Shims (`REMOVE_AFTER = "25-3-bots-context-paper-and-exec-types-nautilus-acl"`); `collector.dockerfile` `COPY platform/archive ./archive`; Makefile lists add `archive/tests`; `docs/DATA_DICTIONARY.md` §6, `DATA_INTEGRITY_AUDIT.md` D-36/D-45..D-51 paths, `platform/CLAUDE.md` DATA-05/06.

## Dev Notes

Archive is where the adversary review found the most holes (C2, C3, H2, H4, M9): the day-status store, the second pruner in `DydxCollector`, the four rewriters, the leaf-writer rule. Read those sections of `reviews/review-adversary.md` before starting. The capture lock and the `repair_catalog` refusal are the one deliberate capture edit here — small and tested. Nightly is a saga: `reconcile_day` must receive `rebuild_day`'s success in-process.

### Migration rules that bind every story (spine AD-D12, MR1/MR2/MR4/MR14)

- **Deployable alone.** Frozen for the whole migration: Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`), the SQLite/TOML store schemas, compose service names, env vars, the `platform/data/` bind mounts. A replay/fixture test proving a payload or file is byte-identical before and after the move is the standard evidence.
- **Shims.** The old import path stays as a pure re-export: `from <new> import <names>` + `warnings.warn(..., DeprecationWarning)` + `REMOVE_AFTER = "<story key>"`. It defines nothing (a copied class body would register a second Arrow class and break `is` dispatch). Update every in-repo caller in the same story; a `DeprecationWarning` in the test run is a failure (TEST-04). `platform/tests/test_namespace.py` asserts `old.X is new.X`.
- **Same commit:** `platform/CLAUDE.md` citations, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, both Makefile test lists (`test`, `test-live-paper`). `platform/tests/test_images.py` and `test_boundaries.py` (from 23.1) must pass.
- **Layering (AD-D2):** `domain/` imports only stdlib, `kernel/` and `nautilus_trader.model`/`core` types — no I/O, asyncio, Redis, SQLite, Parquet or the Nautilus runtime; `application/` holds `typing.Protocol` ports, services and the asyncio loops; `infrastructure/` implements ports and is imported only by the composition root. No module-level mutable runtime state (AD-D10). Every aggregate/port docstring names the invariant it protects (DESIGN-01).
- **Parent spine:** when this story resolves one of its Deferred items, strike it there with a `[amended <date>: Story <n>]` note (MR14).
- **Project rules:** `platform/CLAUDE.md` DATA-01..08, DATA-07 (no silent skips; `observability.error_ledger.record`), TEST-01..04 (real Nautilus objects, no mocks of internals, warnings are failures), READ-03 (type hints, mypy), SSOT-01..05, MEM-01..03, NAUT-01..03, FORK-01 (never touch `nautilus_trader/` or `crates/`).
- **Working directory:** `platform/` (`cd platform`); tests run as `python3 -m pytest -o addopts="" --rootdir=. <paths> -q`; `make test` runs the Makefile list inside the collector image.

### Project Structure Notes

- Target tree: spine "Structural Seed". Working dir `platform/` (renamed from `troll/` on 2026-09-21; historical docs cite `troll/`). Durable stores under `platform/data/`, never under a package.
- `platform/` is a namespace directory: never add `platform/__init__.py`; never import with a `platform.` prefix (contexts are top-level packages with `platform/` on `sys.path`).

### References

- Spine sections: AD-D7, AD-D9, AD-D18, AD-6 (inherited)
- Review findings that shaped this story: reviews/review-adversary.md C2, C3, H2, M9; reviews/review-rubric.md H5, M9
- Code: `collector_core/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,nightly,backfill_bars,migrate_open_interest,measure_lag,archive_gaps}.py`, `dydx_collector/collector.py:595-625`, `dydx_collector/normalize_snapshot_schema.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
