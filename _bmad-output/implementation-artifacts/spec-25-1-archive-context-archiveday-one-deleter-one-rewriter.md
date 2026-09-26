---
title: 'Story 25.1: archive/ context: ArchiveDay, one deleter, one rewriter, one writer per leaf'
type: 'refactor'
created: '2026-09-25'
status: ready-for-dev
baseline_revision: f3f17560e3a7161063dd9d7af2591de1f8a78cac
final_revision: '3a0d680d24a5a123fad46257f9c88b5cfc304576'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/25-1-archive-context-archiveday-one-deleter-one-rewriter.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-25-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The nightly archive maintenance is spread over eleven `collector_core`/`dydx_collector` modules. It has four hand-rolled in-place Parquet rewriters and a second catalog pruner inside `DydxCollector._prune_loop`, which runs under no lock. Nothing stops `compare_klines` from verifying a day that was never rebuilt, and nothing stops `repair_catalog` from writing while that venue's collector runs. The boundary guard also expires two legacy edges at this story: `(CAPTURE, ARCHIVE)` and `(ARCHIVE, VIEWS)`.

**Approach:** Create the `platform/archive/` bounded context with four layers: `domain/`, `application/`, `infrastructure/` and `tools/`. Behind it sit thin CLI composition roots, one per operator tool (`python -m archive.<tool>`). They enforce the `ArchiveDay` state machine and a saga-carried rebuild proof, have one deleter (`RetentionPolicy` executed through `CatalogFiles.delete`) and one rewriter (`CatalogFiles.rewrite`), and hold a flock-based capture lock. Every old module path is left as a pure re-export shim, and every in-repo caller, doc, image and Makefile line is updated in the same change.

## Boundaries & Constraints

**Always:**
- The published language stays byte-identical. That covers Parquet schemas, catalog dir/file names, the `_archive_gaps/*.jsonl` line format, the `verified_days` schema, TOML key sets, compose service names and env vars, and ledger site names that already exist. The only deliberate additions are the new ledger sites, the lock file and the CLI flags this spec names.
- Every existing operator CLI keeps every existing argument. Flags are only added.
- Layering (AD-D2) holds throughout:
  - `archive/domain/` imports only the stdlib, `kernel`, and `nautilus_trader.model`/`core`.
  - `archive/application/` declares `typing.Protocol` ports, and never imports `archive.infrastructure` or `candles.infrastructure`.
  - Infrastructure is imported only by the composition roots, which are the top-level `archive/<tool>.py` CLIs and `archive/tools/*.py`, and by tests.
  - There is no module-level mutable state.
  - Every aggregate and port docstring names the invariant it protects (DESIGN-01).
- Archive → candles goes only through `candles.application.*` (the `VerifiedDays` port, `queries.window`/`open_store`, `rebuild.*`). Only the composition roots touch `candles.infrastructure`.
- Tolerated failures are recorded with `observability.error_ledger.record`, one site per event.
- There are no mocks of Nautilus internals, no class-based tests, and each test returns `-> None`.
- A `DeprecationWarning` in the test run is a failure (TEST-04).
- Deliberate simplifications carry a `Known limit:` comment that names the ceiling and the upgrade path.
- License header on every new file. Line length is 100, every function signature has type hints, one import per line.

**Block If:**
- Moving `ohlc_outside_book` into `kernel/second_snapshot.py`, or `capture_lock_path` into `kernel/archive_markers.py`, would change a kernel Arrow registration or break `test_kernel_is_pure`, and no pure alternative exists.
- Any frozen byte format (a marker line, a Parquet file's schema or metadata, a `verified_days` row) would have to change to meet an AC.

**Never:**
- Never modify `nautilus_trader/`, `crates/`, or `_bmad-output/implementation-artifacts/sprint-status.yaml`.
- Never widen `LEGACY_EDGES_UNTIL` or `LEGACY_PRIVATE_IMPORTS_UNTIL`, or push an expiry to a later story. Fix the import instead.
- Never give archive a second store of day status. `rebuilt` is never persisted.
- Never add a prune loop anywhere in capture or control.
- Never route `backfill_bars` through the new `VenueKlines` adapters. That would change which bars get written (see Design Notes).
- Never add a new dependency, a DI container or an event bus.
- Never add `platform/__init__.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Saga happy path | `archive.nightly` for one venue-day; rebuild refuses nothing | The rebuild step writes a result file. The saga reads it into its `StepResult` and passes reconcile `--rebuilt-by <run_id>`. Reconcile marks each instrument pass/fail. | none |
| Rebuild refused an instrument | Rebuild refuses X (e.g. `rebuild.duplicate_second`), exit 2 | The reconcile argv carries `--not-rebuilt X`. X is not compared and no `verified_days` row is written for it. | Ledger `reconcile.not_rebuilt` naming X; reconcile exits 2 |
| Standalone reconcile | `python -m archive.compare_klines ...` without `--rebuilt-by` | Nothing is compared or written | Ledger `reconcile.not_rebuilt`; exit 1 |
| Rebuild result missing | Rebuild exits 0/2 but its result file is absent or unparsable | The saga stops before consolidate | Ledger `nightly.rebuild_seconds`; saga exit 1 |
| Row in a gap span | A snapshot row whose `ts_event` lies in an `ArchiveGap` span | Its trade columns are untouched. `DayReport.in_gap` counts it and the report line prints it. | none |
| Open-day whole-file rewrite/merge | `CatalogFiles.write_merged`/`remove_merged_sources`, or a whole-file `rewrite` (the two migration tools), on a file whose `ts_init` span REACHES the current UTC day | Nothing is written or removed | Raises `OpenDayWriteError`. Each caller ledgers `<tool>.open_day` and skips that file or instrument-day. |
| Midnight files in the rebuild (row-preserving rewrite) | The nightly rebuilds closed day D at ~00:30 of D+1. File B (e.g. `ts_init` 23:59:02(D) → 00:00:01(D+1), from capture's 00:00:02 flush) and file C (starts 00:00:02(D+1) or later but holds D rows: venue-time capture closes second S at `S + 1 + hold_back_seconds`, so Hyperliquid's (2.5 s) last D row is always created after the 00:00:02 flush, and Bybit's after a stall or a trade carry) | Both are rewritten. The rebuild may change ONLY rows whose `ts_event` lies in D; every row whose `ts_event` is in the open day is written back identical in value and order, and `CatalogFiles.rewrite` verifies that against the original before `os.replace`. Safe because capture writes each file once and never reopens it. Consolidate is unchanged: `closed_days_needing_work` already leaves a file crossing midnight out of every day's merge. | Must never raise `rebuild.open_day` on the normal nightly path. A row-preserving rewrite that would change an open-day row raises `OpenDayWriteError` (ledger `rebuild.open_day`, instrument-day refused). |
| Open-day delete | `CatalogFiles.delete` of a file whose `ts_init` span REACHES (intersects) the current UTC day, files B and C included | Nothing is deleted; the file is left for a later nightly (a delete destroys rows of a day that is not closed or verified) | Raises `OpenDayWriteError`; prune ledgers `prune.open_day` |
| Rebuild on today | `archive.rebuild_seconds --day <today> --apply` (with or without `--include-open-day`) | Run refused. `--include-open-day` without `--apply` still reports only. | Ledger `rebuild.open_day`; exit 1 |
| Repair while capturing | `archive.repair_catalog` while that venue's collector holds `.capture-<VENUE>.lock` (shared flock) | That venue's instruments are not repaired | Ledger `repair.capture_running`; exit 1 |
| Capture blocked by repair | Collector starts while an archive tool holds the capture lock exclusively | Capture waits: it retries the non-blocking `LOCK_SH` every 1 s and still honours shutdown | Ledger `collector.capture_lock_wait` once per wait |
| Collector killed | SIGKILL/OOM | The kernel drops the flock, so the lock file's presence alone never means "running" | none |
| Dropped dYdX instrument | DYDX leaf not in the plan's `instruments`; closed-day files end before `now - non_config_retain_hours` | `RetentionPolicy` deletes them (every type except `trade_tick`, the same set as `prune_instrument`) | Open-day files are left for the next nightly |
| Delta retention | Plan entry with `store_order_book_deltas` and `retain_hours=h` (None = unlimited) | Closed-day `order_book_deltas` files ending before `now - h` are deleted. None means never. | none |

</intent-contract>

## Code Map

- `platform/collector_core/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,nightly,backfill_bars,migrate_open_interest,measure_lag,archive_gaps,crosscheck_errors,integrity}.py`: the modules that move out. `integrity` goes to the kernel.
  - Four in-place rewrite sites, each `pq.write_table` followed by `os.replace`:
    - `rebuild_seconds._replace_file` (~:318-333)
    - `consolidate_catalog._merge`/`_consolidate_day` (~:210-261)
    - `migrate_open_interest.migrate_file` (~:87-110)
    - `dydx_collector/normalize_snapshot_schema.migrate_file` (~:77-95; it currently writes snappy)
- `platform/dydx_collector/normalize_snapshot_schema.py`: moves to `archive/tools/`.
- `platform/dydx_collector/collector.py:68,146-195,220,608-641` and `:250,403-406`:
  - `prune_instrument` import, `_prune_interval_seconds`, `_prune_candidates`, `_prune_all_instruments`, `_prune_delta_retention`, `_prune_loop` and its `extra_loops` entry. All are deleted.
  - `_known_markets` stays only if another loop still reads it.
  - `_delta_retain_hours` state is deleted if nothing else reads it. `_delta_store` stays.
- `platform/dydx_collector/config.py`: `load_config`/`DydxConfig` (`non_config_retain_hours`, `InstrumentEntry.retain_hours`/`store_order_book_deltas`). This is collection_control's one plan loader. The archive prune root reads the plan through it.
- `platform/ml_signals/catalog_stats.py:81-200`:
  - `find_gaps`, `_overlapping_intervals`, `_load`, `likely_outages` and `coverage` move to `archive/application/diagnostics.py`.
  - Keep `__getattr__` and serve the three public names via `_MOVED_NAMES` + `MOVED_NAMES_REMOVE_AFTER`.
- `platform/collector_core/collector.py`:
  - `:157` `record_gap` becomes a capture-owned marker writer.
  - `:167` `ohlc_outside_book` moves to the kernel.
  - `:1951-1996` `run_forever` takes the capture lock.
- `platform/kernel/archive_markers.py` (add `capture_lock_path`) and `platform/kernel/second_snapshot.py` (add `ohlc_outside_book`, `OHLC_BOOK_TOLERANCE`): pure additions.
- `platform/candles/application/{verified_days,queries,rebuild}.py`: the ports and services archive may call.
- `platform/tests/{test_boundaries,test_images,test_namespace,test_skew_constants}.py`: the guards to update.
- `platform/data_api/alerts.py`: a Story 24.3 shim with `REMOVE_AFTER` = this story. Delete it.
- `platform/{Makefile,collector.dockerfile,README.md,ARCHITECTURE.md,CLAUDE.md}`, `platform/docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT,DEPLOY_CHECKLIST}.md`, `platform/candles/__init__.py` and the kernel docstrings that cite `collector_core.archive_gaps`/`rebuild_seconds`: the docs and build files to update.
- Parent spine `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md` Deferred entry (~line 292). Its last remaining site is `repair_catalog`'s `query_second_snapshots`. Add `[amended 2026-09-25: Story 25.1 — resolved: ...]`.

## Tasks & Acceptance

**Execution:**

- [x] `platform/archive/__init__.py` and `{domain,application,infrastructure,tools,tests}/__init__.py`:
  - The top-level `__init__` carries the context docstring: invariants, layers, composition roots, dependency direction and Known limits, in the style of `candles/__init__.py`.
  - The sub-packages carry only the license header.
- [x] `platform/archive/domain/archive_day.py`: `DayStatus` enum (`provisional|rebuilt|verified|mismatched|released`) and a frozen `ArchiveDay(venue, instrument, day, status)`.
  - `from_verified_status(venue, iid, day, status: str | None)` maps None to provisional, `"pass"` to verified and `"fail"` to mismatched.
  - `rebuilt(proof)` is legal from provisional, mismatched or verified (re-derivation, see Design Notes). It requires the `RebuildProof` to cover (venue, day, iid).
  - `reconciled(result)` is legal from rebuilt only, and goes to verified on an exact match, otherwise to mismatched.
  - `released()` is legal from verified only.
  - An illegal move raises `IllegalTransition`.
  - Also define `RebuildProof(run_id, venue, day, rebuilt: frozenset[str], not_rebuilt: frozenset[str])` with a `covers(iid)` method that is an ALLOWLIST: `covers(iid)` is true only when `iid in rebuilt and iid not in not_rebuilt`. An instrument the rebuild never processed (a leaf that appeared between the steps, an instrument with no snapshot files that `rebuild_day` returned as an empty report) is not covered. An instrument-day for which `rebuild_day` rewrote nothing because it has no snapshot rows is reported as neither rebuilt nor refused.
- [x] `platform/archive/domain/reconciliation.py`: moved from `compare_klines`. `Kline`, `KlineError`, `units`, `float_units`, `seed_with_previous_close`, `mismatch_message`, `InstrumentResult` renamed `ReconciliationResult` (status/minutes/mismatches), and `compare`. All pure and exact, with no tolerance.
- [x] `platform/archive/domain/retention.py`: `RetentionPolicy`, pure. It is given file spans, day statuses, `PlanRetention(collected, non_config_retain_hours, delta_retain_hours)`, `now_ns` and the age rules. It returns the deletions, each with its rule and reason, plus the kept `(iid, day, reason)` entries. The rules:
  - (a) the `trade_tick` rule from `plan_trade_prune`: the day is aged AND every spanned day is `ArchiveDay` verified, with the midnight skew-margin previous-day rule;
  - (b) `--types/--days` age;
  - (c) dropped-instrument (DYDX leaves not in `collected`), every type except `trade_tick`, end older than `non_config_retain_hours`;
  - (d) per-instrument `order_book_deltas` `retain_hours`, where None means unlimited.
  - A file whose span intersects the current UTC day is never chosen. An unparsable name is kept and reported.
- [x] `platform/archive/domain/gaps.py`: `Coverage(start, gaps)` moved from `rebuild_seconds`, with a `covers`/`in_gap` distinction so the rebuild can count gap rows separately. It is built over `kernel.archive_markers.ArchiveGap`/`in_gap`.
- [x] `platform/archive/application/ports.py`:
  - `CatalogWriter` Protocol with `rewrite`, `write_merged`, `remove_merged_sources`, `delete`, `remove_stale_tmp`, `remove_empty_dir` and `assert_closed(start_ns, end_ns)`.
  - `GapMarkers` Protocol with `record(gap)` and `load(iid)`.
  - `VenueKlines` Protocol: `fetch(inst, day_ms) -> list[Kline]`.
  - `RetentionPlanSource` Protocol: `retention() -> PlanRetention`.
  - Each docstring names the invariant it protects.
- [x] `platform/archive/application/rebuild_day.py`: from `rebuild_seconds`, with every mutation going through `CatalogWriter`.
  - Add `DayReport.in_gap` (rows kept because they sit inside an `ArchiveGap` span; printed in `line()`), kept separate from `not_covered` (pre-archive rows).
  - `run()` returns the refused iids, not a count.
  - Keep `RefusedError`, `covered_from`, `trade_files`, `fold_day` and `_TS_INIT_MARGIN_NS = MAX_TS_INIT_SKEW_NS`.
  - File selection for day D: every snapshot file whose `ts_init` span overlaps `[D start, D end + _TS_INIT_MARGIN_NS]` (not only `files_by_day`'s D bucket), because a D row's `ts_init` can land after midnight (venue-time capture closes second S at `S + 1 + hold_back_seconds`). Rows are still chosen by `ts_event` in D. Every write goes through the row-preserving `rewrite`; `_prepare_writes` no longer refuses a file merely for reaching today.
- [x] `platform/archive/application/consolidate_day.py`: from `consolidate_catalog`, logic unchanged. The merge goes through `write_merged`, source removal through `remove_merged_sources`, and the covering-file recovery path is kept. `bar` leaves stay excluded.
- [x] `platform/archive/application/reconcile_day.py`: from `compare_klines`'s `reconcile_instrument`/`run`/`instruments_on_day`/`summary_line`.
  - It takes `proof: RebuildProof | None`, the `VenueKlines` port, the `VerifiedDays` port and the store's read-only connection through `candles.application.queries`.
  - `proof is None` means ledger `reconcile.not_rebuilt` and return 1.
  - An instrument not covered by the proof is ledgered `reconcile.not_rebuilt` and yields a `not_rebuilt` result (a finding, exit 2, no verdict written).
  - Otherwise the `ArchiveDay` transitions decide what `mark_verified` writes. Status strings stay `pass`/`fail`.
- [x] `platform/archive/application/prune.py`: lists leaves, reads statuses through `VerifiedDays`, asks `RetentionPolicy`, then executes.
  - For a `trade_tick` deletion it records the `pruned` marker first, via `GapMarkers`, then calls `CatalogWriter.delete`.
  - This is the only caller of `CatalogWriter.delete`. It reports what it freed and what it kept, exactly as today's logs do.
- [x] `platform/archive/application/repair.py`: from `repair_catalog`.
  - Reads snapshots with its own `ParquetDataCatalog.query` plus the `CustomData` unwrap, the same query as `views.catalog_reads.query_second_snapshots`, which it no longer imports.
  - Uses `kernel.second_snapshot.ohlc_outside_book`.
  - Calls `assert_closed` on each flagged row's `ts_init` and skips and ledgers today's rows.
  - `delete_data_range` + `write_data` stay (Known limit, see Design Notes).
- [x] `platform/archive/application/backfill_bars.py`: moved as-is (`write_data` caller #1). `platform/archive/application/diagnostics.py`: the `catalog_stats` archive half, with ledger site names unchanged. `platform/archive/application/crosscheck.py`: the `crosscheck_errors` logic.
- [x] `platform/archive/application/nightly.py`: `Step`, `StepResult` and `run_steps`.
  - `run_steps` stops at the first failure and ledgers `nightly.<step>` exactly as today.
  - It carries the rebuild step's `RebuildProof` in-process. It reads the rebuild step's `--result-file` JSON (`{"venue","day","rebuilt":[...],"refused":[...]}`) into that step's `StepResult`, and a missing or invalid file is a FAILED step.
  - It carries BOTH lists into the in-process `RebuildProof` (`rebuilt` from the result file, `not_rebuilt` from `refused`); `read_rebuild_result` must not discard `rebuilt`.
  - It appends `--rebuilt-by <run_id>`, one `--rebuilt <iid>` per rebuilt iid and one `--not-rebuilt <iid>` per refused iid to the reconcile step's argv.
  - `run_id = uuid4().hex`. It is logged and printed in the summary line.
  - The result file lives in a `tempfile.TemporaryDirectory` for the run. It is inter-process transport, never persisted.
- [x] `platform/archive/infrastructure/catalog_files.py`: `CatalogFiles`, which implements `CatalogWriter`. It is the only module in `archive/` that calls `pq.write_table`, `os.replace`, `unlink`/`os.remove`/`rmdir`.
  - `rewrite(path, table)`: writes `<file>.archive.tmp` with zstd (via `kernel.parquet_compat.apply_zstd_default()` + `pq.write_table`), preserving the table's own schema metadata. It verifies the read-back schema (`check_metadata=True`) and row count before `os.replace`, and on failure deletes the tmp and raises.
  - `remove_stale_tmp(leaf)` removes `*.archive.tmp` plus the legacy `*.rebuild.tmp`, `*.consolidate.tmp` and `*.parquet.tmp`.
  - Open-day guard, by operation (see the I/O matrix; `now_ns` is injectable, today = `now_ns() // NS_PER_DAY`, spans from `CatalogFileSpan.from_path`):
    - `rewrite` has two modes. Whole-file (the migration tools): raise `OpenDayWriteError` when the file's span REACHES today. Row-preserving (the rebuild): any file may be rewritten, but before `os.replace` it verifies that every row whose `ts_event` is at or after today's UTC midnight is identical in value and order between the original and the new table, and raises `OpenDayWriteError` otherwise. Capture writes each file once and never reopens it, so rewriting a file that also holds today's rows races with nothing.
    - `write_merged`, `remove_merged_sources`, `delete`: raise `OpenDayWriteError` when the file's span REACHES today.
    - `assert_closed(start_ns, end_ns)` is split or parameterised so a caller cannot use the row-preserving rewrite to change or delete an open-day row; name each check after what it protects.
  - Instances are obtained only from `maintenance(catalog) -> ContextManager[CatalogFiles | None]` in `platform/archive/infrastructure/maintenance_lock.py`, which holds today's flock `.consolidate.lock` (name kept).
- [x] `platform/archive/infrastructure/maintenance_lock.py`, `gap_markers.py` and `klines_{dydx,bybit,hyperliquid}.py`:
  - `maintenance_lock.py` also provides `capture_exclusive(catalog, venue) -> ContextManager[bool]`: a non-blocking `LOCK_EX` on `kernel.archive_markers.capture_lock_path`, held for the caller's duration. A missing file counts as not running.
  - `gap_markers.py` implements `GapMarkers`. It holds archive's append (fsync; `archive_gaps.inverted_span` and `archive_gaps.write` sites unchanged) and the reader (`ValueError` on a malformed line).
  - The `klines_*.py` modules are the `_fetch_*`/`parse_*` code moved from `compare_klines` over `kernel.venue_http`, each implementing `VenueKlines`. There is also a `catalog_klines` adapter for `--kline-source catalog`.
- [x] `platform/archive/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,nightly,backfill_bars,crosscheck_errors}.py`: thin CLI composition roots. Each keeps every argparse argument of its predecessor and adds only the following:
  - rebuild: `--result-file PATH`. It also logs `rebuild <day>: run id <id>; pass --rebuilt-by <id> to compare_klines` when standalone.
  - compare: `--rebuilt-by RUN_ID`, `--rebuilt IID` (repeatable) and `--not-rebuilt IID` (repeatable). With `--rebuilt-by` it compares only instruments named by `--rebuilt` and not by `--not-rebuilt`; every other instrument on the day is ledgered `reconcile.not_rebuilt`.
  - prune: `--dydx-plan PATH`. It loads the plan through `dydx_collector.config.load_config` and applies rules (c)/(d) to DYDX leaves only, for `--venue DYDX` or no `--venue`. `--dydx-plan` alone now satisfies "nothing to do".
  - nightly: `--dydx-plan PATH`, required when `--venue DYDX` and forwarded to the prune step.
  - `main(argv=None) -> int` everywhere.
  - `nightly.steps()` keeps the literal `Step(name, module("<dotted>", ...))` shape and the step names. Its modules become `archive.rebuild_seconds`, `archive.consolidate_catalog`, `candles.rebuild`, `archive.compare_klines` and `archive.prune_catalog`.
- [x] `platform/archive/tools/{measure_lag,migrate_open_interest,normalize_snapshot_schema}.py`: moved.
  - The two migrations rewrite through `CatalogFiles` under `maintenance()`. They skip open-day files: ledger `<tool>.open_day` with the count and exit 2.
  - migrate: the `unlink`/`rmdir` go through CatalogFiles.
  - normalize: it now writes zstd, a documented deliberate change from snappy, bytes differ but schema and rows are identical.
  - Backups are still `shutil.copy2` into `--backup-dir`.
- [x] Shims (`REMOVE_AFTER = "25-3-bots-context-paper-and-exec-types-nautilus-acl"`):
  - `collector_core/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,nightly,backfill_bars,migrate_open_interest,measure_lag,crosscheck_errors,archive_gaps,integrity}.py` and `dydx_collector/normalize_snapshot_schema.py`. Each is a pure re-export in the exact `ml_signals/run_backtest.py` shape.
  - Each re-exports `main` plus every public name that still exists with the same contract, and has `if __name__ == "__main__": raise SystemExit(main())` for CLIs.
  - `archive/infrastructure/gap_markers.py` keeps the module functions `record_gap(catalog_path, iid, from_ns, to_ns, reason, count)` and `load_gaps(catalog_path, iid)` with the same signatures, and `GapMarkers` wraps them. The `archive_gaps` shim re-exports both functions.
  - Every other shim drops any name that no longer exists, rather than adding an adapter.
  - Delete `data_api/alerts.py` and fix every reference to it.
- [x] `platform/kernel/second_snapshot.py`: add `ohlc_outside_book` + `OHLC_BOOK_TOLERANCE`, moved verbatim from `collector_core.integrity`. `platform/kernel/archive_markers.py`: add `capture_lock_path(catalog_path, venue) -> Path` (`<catalog>/.capture-<VENUE>.lock`, where VENUE is the `kernel.venues` code). Add kernel tests for both.
- [x] `platform/collector_core/capture_lock.py` (capture context), `collector.py` and the three venue `collector.py` files:
  - Each venue subclass declares `VENUE: ClassVar[str]` ("DYDX"/"BYBIT"/"HYPERLIQUID").
  - `run_forever` takes a shared `fcntl.flock(LOCK_SH|LOCK_NB)` on `capture_lock_path` after the first `build()` and holds it for the process lifetime. It writes pid + started_at as informational JSON, and never unlinks the file.
  - While the lock is held exclusively, it waits, retrying every 1 s and checking `shutting_down`, and ledgers `collector.capture_lock_wait` once per wait.
  - Capture's own marker writer for `write_failed`/`quarantined` goes in `collector_core/gap_markers.py` over `kernel.archive_markers.encode`/`path_for`, with ledger site names unchanged. `collector.py` stops importing `collector_core.archive_gaps`/`integrity`.
  - Test: `collector_core/tests/test_capture_lock.py` checks the shared hold, that an exclusive holder blocks capture and then releases it, that the lock is released on exit, and that `capture_exclusive` sees a running capture.
- [x] `platform/dydx_collector/collector.py` and its tests: delete `_prune_loop` and its helpers, the `prune_instrument` import and the `extra_loops` entry. Delete or move their tests (`test_collector_snapshot.py:255-292`, `test_collector_control.py:32,367`) into `archive/tests/test_retention.py` as policy tests with the same cases.
- [x] Tests:
  - Move every archive test file to `platform/archive/tests/test_<new module>.py` with the imports repointed: `collector_core/tests/test_{archive_gaps,backfill_bars,compare_klines,consolidate_catalog,crosscheck_errors,measure_lag,migrate_open_interest,nightly,prune_catalog,rebuild_seconds}.py`, `dydx_collector/tests/test_{normalize_snapshot_schema,repair_catalog}.py`, and the `catalog_stats` gap/outage tests from `ml_signals/tests/test_catalog_stats.py`.
  - Move the two capture tests that call `rebuild_day`/`load_gaps` (`test_collector.py:805`, `test_venue_time.py:178`) into `platform/tests/test_capture_archive_handoff.py`, a cross-cutting contract test. `test_collector.py:866` decodes the markers with `kernel.archive_markers.decode`.
  - Add these tests:
    - `test_archive_day.py`: one invariant test per command, plus illegal moves.
    - `test_retention.py`: every rule and reason, the open-day exclusion, None retention, the midnight margin.
    - `test_catalog_files.py`: after a rewrite the schema metadata and row order are unchanged, the codec is zstd, a failed verify leaves the original intact, open-day refusal, stale tmp cleanup. Open-day cases: a whole-file rewrite, `write_merged` and `delete` of a file reaching today are refused; a row-preserving rewrite of such a file succeeds when only closed-day rows change and is refused (original intact) when an open-day row would change.
    - Midnight rebuild test (in `test_rebuild_day.py`, injected `now_ns` at 00:30 of D+1): file B 23:59:02(D) → 00:00:01(D+1) and file C 00:00:02.5(D+1) → 00:01:01(D+1) holding the D row for 23:59:59. `rebuild_day` for D raises no `rebuild.open_day`, rebuilds the D rows in both files (the 23:59:59 row included), and leaves every D+1 row value- and order-identical.
    - Retention test: files B and C are never chosen for deletion on the night they are written.
    - `test_one_deleter_one_rewriter.py` (AST over the `archive/` non-test modules):
      - only `catalog_files.py` calls `write_table`/`replace`/`unlink`/`remove`/`rmdir`/`rmtree`;
      - only `application/prune.py` calls `.delete(`;
      - `archive.infrastructure`/`candles.infrastructure` are imported only by the composition roots;
      - no `_prune_loop` exists in any venue collector.
    - Port contract tests run against each adapter: `CatalogFiles`, `GapMarkers`, each `VenueKlines` over fixture payloads.
    - Reconcile refusal tests: no proof; an instrument named by `--not-rebuilt`; an instrument on the day that is in neither list (not in `rebuilt`) gets no verdict and `reconcile.not_rebuilt`.
    - Saga test: `rebuilt` from the result file reaches the reconcile argv as `--rebuilt`.
    - Saga tests with a fake runner: result-file carried through; missing file means FAILED; `--not-rebuilt` forwarded; `--dydx-plan` forwarded for DYDX only.
    - A rebuild `in_gap` count test.
    - Repair: `repair.capture_running` and an open-day skip.
- [x] `platform/tests/test_boundaries.py`, `test_images.py`, `test_namespace.py`, `test_skew_constants.py`:
  - test_boundaries:
    - `THIS_STORY` = this story's key.
    - Drop the `(CAPTURE, ARCHIVE)` and `(ARCHIVE, VIEWS)` edges.
    - Drop the stale map entries for moved tests and `data_api.alerts`, and the `catalog_stats` archive symbol entries.
    - Add `COMPOSITION_ROOTS["archive.prune_catalog"] = {COLLECTION_CONTROL}`.
    - `ALERTING_INFRASTRUCTURE_IMPORTERS` becomes just `{"data_api.alert_wiring"}`.
    - Repoint the checker examples that name `data_api.alerts`.
  - test_images:
    - The expected entrypoint set becomes `archive.nightly`/`archive.rebuild_seconds`.
    - `_CHILD_PROCESSES = {"archive.nightly": ...}`, reading the module that holds the `Step(...)` calls.
    - Repoint the shim-closure and `import_module` tests to archive modules.
  - test_skew_constants: repoint to the archive modules.
- [x] `platform/Makefile`:
  - `consolidate`/`nightly`/`prune`/`prune-dry` run `python3 -m archive.*`, and `nightly` adds `--dydx-plan /app/dydx_collector/config.toml`.
  - `test` adds `archive/tests`.
  - `test-live-paper` adds `--ignore=tests/test_capture_archive_handoff.py`, because that image ships neither context.
  - Update the comments of `backup-catalog`/`build-candles` that cite old paths.
- [x] `platform/collector.dockerfile`: add `COPY platform/archive ./archive`.
- [x] Update the docs: `platform/README.md` (tool usage lines and the nightly/cron section) and `platform/docs/DEPLOY_CHECKLIST.md`:
  - Update the module paths.
  - Remove the "`_prune_loop` takes no lock" limitation.
  - Document the capture lock and `--rebuilt-by`.
  - Keep the one cron line made of make targets only.
- [x] Update the docs: `docs/DATA_DICTIONARY.md` §6 and `DATA_INTEGRITY_AUDIT.md` D-36/D-45..D-51, citing the new paths.
  - The data dictionary lists `backfill_bars` and `repair_catalog` as the only offline `write_data()` callers.
  - It documents `.capture-<VENUE>.lock`, `*.archive.tmp` and the new ledger sites.
  - It moves the `_prune_*` entries (~:113, :709-776) to `RetentionPolicy`.
- [x] Update the docs: `platform/CLAUDE.md` DATA-05/06 name the `archive.` tools. `ARCHITECTURE.md`, `candles/__init__.py` and the kernel docstrings cite the new paths. The parent spine Deferred entry gets its amendment.

**Acceptance Criteria:**
- Given the moved modules, when `python -m archive.<tool> --help` runs, then every predecessor argument is listed. Running `python -m collector_core.<tool>` still works and emits one `DeprecationWarning` naming the new path.
- Given the full `make test` path list plus `archive/tests`, when run from `platform/` with `python3 -m pytest -o addopts="" --rootdir=. ... -q`, then everything passes except the known redis-dependent `data_api/tests/test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay`, and the run has no `DeprecationWarning`.
- Given the tree after the change, when searched, then:
  - `grep -rn "pq.write_table\|os.replace" platform/archive --include='*.py'` hits only `infrastructure/catalog_files.py` (and tests);
  - no module under `platform/` imports a `collector_core` shim path;
  - `DydxCollector` has no prune loop.
- Given a nightly run for DYDX, when the rebuild step refuses an instrument, then that instrument gets no `verified_days` row that night, and `reconcile.not_rebuilt` is in the ledger.
- Given a nightly run for yesterday at 00:30 UTC, when every instrument has the usual midnight-crossing file (and, for Hyperliquid, its last row of yesterday in a file starting today), then no instrument is refused with `rebuild.open_day`, every row of yesterday is rebuilt, reconcile writes verdicts for the rebuilt instruments, and every row of today is unchanged.
- Given an instrument present on the day but absent from the rebuild result's `rebuilt` list, when reconcile runs under the saga, then it gets no `verified_days` row and `reconcile.not_rebuilt` is ledgered.

## Design Notes

- **Why a result file.** The steps stay subprocesses because of MEM-01: each step's RSS is returned to the OS. So the proof crosses the process boundary once, from the rebuild child to the saga, as a JSON file inside the saga's own temp dir. After that it lives in the saga's `StepResult`, and the saga hands it to the reconcile child as argv.
  - Standalone `--rebuilt-by` is an operator attestation. Known limit: it cannot be checked, because `rebuilt` is never persisted by rule. The upgrade path is running the reconcile in-process in the saga once MEM-01 allows it.
- **Verified → rebuilt.** This transition is a deliberate refinement of the spine diagram. A rerun of the saga re-derives a verified day, and the persisted `pass` must then be re-proven in the same run, exactly like mismatched → rebuilt. Retention cannot be weakened by it: `verified_days` changes only through reconcile's exact verdict. Say so in the `archive_day.py` docstring.
- **One deleter.** Retention deletions (rows leaving the catalog) are decided only by `RetentionPolicy` and executed only by `CatalogFiles.delete`, from `application/prune.py`. Consolidate removing sources already merged into a verified file is not a retention decision, since the rows remain. It uses the separately named `remove_merged_sources`.
- **Repair's official API.** `repair_catalog` keeps `ParquetDataCatalog.delete_data_range` + `write_data` (AD-6's official path), gated by the maintenance lock, the capture-exclusive lock and the open-day check. Known limit: Nautilus rewrites the file there, not `CatalogFiles`.
- **Backfill keeps its f64 path.** `backfill_bars` keeps its PyO3 f64 fetch path. Known limit: the spine wants it on the `VenueKlines` ACL, but moving it would change the written `Bar` values (D-52). The upgrade path is an exact-Decimal bar backfill over `archive.infrastructure.klines_*`.
- **Retention granularity.** Deletions happen only for closed UTC days, so the effective floor for `retain_hours`/`non_config_retain_hours` is "the day closes, plus the next nightly", about 24 h worst case, where the old 15-minute loop pruned intra-day. Put this in a `Known limit:` comment and in the deploy notes.
- **Dropped instruments.** They are now found as DYDX leaves not in the plan, rather than as known indexer markets not in the plan. A delisted market's leftovers therefore also age out, which is the rule's documented intent. Flag it in the deploy notes.
- **Capture lock.** A shared flock, not presence plus unlink. A SIGKILL/OOM always releases a flock, and unlinking a flocked file lets a later opener lock a different inode. So the file persists, which deliberately replaces "removes it on clean shutdown". A shared mode keeps AD-D18's partitioned second capture process legal.

## Prior Attempt (re-drive after resolution 2026-09-26)

A complete implementation of the pre-amendment spec exists at commit `3a0d680d24`, preserved on branch `25-1-prior-attempt` (reviewed once, 21 patches applied). Do not re-implement from scratch:

1. Record `git rev-parse HEAD` of the fresh branch first and restamp this spec's `baseline_revision` with it (the bmad-loop gate rejects a stale baseline).
2. Bring the prior code over while keeping THIS spec: `git diff 8378923332 25-1-prior-attempt -- . ':(exclude)_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md' | git apply --index` (8378923332 is the prior attempt's baseline; the diff carries deletions and renames, which `git checkout <ref> -- .` would not).
3. Then implement the delta this resolution introduced: the row-preserving rewrite mode and per-operation open-day guard, the rebuild's widened file selection, the allowlist `RebuildProof` + `--rebuilt` flag, and the new tests/ACs.
4. The same review pass also raised these (provisional) findings on the prior attempt; fix them in this pass: `reconcile_day.instruments_on_day` aborts the whole compare on an unparsable `*.parquet` name; `rebuild_day` can leave a multi-file day half rewritten yet ledger it "untouched"; `prune_catalog`'s exit code ignores `report.errors`/`open_day`/`marker_failed`; the two migration tools abort on a per-file `RewriteVerifyError`/`OSError` with no ledger entry; report-only runs (`prune-dry`, report-only rebuild) take the exclusive maintenance lock; `remove_merged_sources` does not tolerate duplicate/vanished paths; `prune_catalog --venue` lacks `choices`; `repair_catalog` does not ledger an unknown venue; tools do not check the catalog dir exists.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -W error::DeprecationWarning`. Expected: only the redis `test_rankings` failure (baseline: 1 failed / 1577 passed).
- `cd platform && for m in rebuild_seconds consolidate_catalog prune_catalog compare_klines nightly backfill_bars crosscheck_errors repair_catalog tools.measure_lag tools.migrate_open_interest tools.normalize_snapshot_schema; do python3 -m archive.$m --help >/dev/null || echo FAIL $m; done`. Expected: no FAIL.
- A lint gate in a scratch venv (`ruff==0.15.16`, `mypy==1.20.2`) over `platform/archive platform/collector_core platform/kernel`. Expected: no new findings compared with a `git archive HEAD` control run.


## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 1: (high 1)
- bad_spec: 1: (high 1)
- patch: 9: (high 0, medium 5, low 4)
- defer: 3: (medium 3)
- reject: 13: (low 13)
- addressed_findings:
  - none

Resolution (2026-09-26, human-decided via bmad-loop-resolve): intent_gap resolved by making the rebuild's open-day rule row-scoped (a row-preserving rewrite may touch any file but changes only closed-day rows, verified by `CatalogFiles`; merge, whole-file rewrite and delete stay refused for files reaching today) and by widening the rebuild's file selection by `MAX_TS_INIT_SKEW_NS` so yesterday's rows in a file starting today are rebuilt too; bad_spec resolved by making `RebuildProof.covers` an allowlist over `rebuilt`. Both are encoded in the intent contract, tasks, tests and ACs above.

Note: this was a follow-up review (a fresh pass over the committed `3a0d680d24`). Under the cascade, the intent_gap makes every lower finding moot, so the bad_spec, patch and defer counts are provisional. No deferred-work entries were written and no code was changed in this pass.

