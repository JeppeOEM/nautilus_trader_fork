# Story 23.2: `kernel/` shared kernel

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 23). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 23.2".

## Story

As a strategy developer and collector maintainer,
I want the one snapshot schema, the one fold, the one venue-id parser, the one skew constant, the one REST transport and the one Parquet compression patch to live in a package that imports nothing else,
So that no two contexts can ever hold two copies of a shared type or a shared number.

## Acceptance Criteria

1. **Given** `collector_core/{second_snapshot,open_interest,fold,venue_http,archive_gaps}.py`, `common/venues.py`, `ml_signals/{venue,indicators,performance_metrics}.py`, `catalog_stats.SecondOHLC`/`_stamp_to_ns`/`data_file_ranges`/`second_ohlc_arrays`/`query_second_ohlc` and the zstd `pq.write_table` patch duplicated in `collector.py` and `backfill_bars.py`
**When** the story ships
**Then** `platform/kernel/` holds exactly `second_snapshot.py` (`DydxSecondSnapshot` + `SecondOHLC`), `open_interest.py`, `fold.py`, `indicators.py` (pure `Indicator` classes and stateless snapshot functions only), `performance_metrics.py`, `venues.py`, `clocks.py`, `archive_markers.py`, `venue_http.py`, `catalog_files.py`, `parquet_compat.py` and nothing else; `test_boundaries.py` asserts kernel imports no context and holds no module-level mutable state, no store and no config loader; `common/` and the moved `ml_signals`/`collector_core` modules become pure re-export shims (`REMOVE_AFTER = "24-2-..."`) and every in-repo caller is updated

2. **Given** the catalog directory names derive from the class names (`nautilus_trader.persistence.funcs.class_to_filename`)
**When** `DydxSecondSnapshot` and `OpenInterest` move
**Then** their class names and Arrow schemas are byte-identical, `register_arrow` runs exactly once per class (`test_namespace.py` asserts one `_SCHEMAS` key per kernel class `__name__` and `old.X is new.X` for every shim name), a fixture test proves a `snapshots:raw` payload and a catalog row written before the move are read back unchanged after it, and `DydxSecondSnapshot.from_dict` is the only `snapshots:raw` parser left in `data_api/live_candles.py` (ranking and bot_tui parsers are chased in their own stories)

3. **Given** three `InstrumentId` parsers today (`common.venues.market_kind`, `ml_signals.venue.venue_of`, `venue_http.bybit_category`)
**When** `kernel/venues.py` lands
**Then** it is the only module that parses an `InstrumentId` string, exposing `venue_of`, `venue_kind`, `market_kind`, `bybit_category` (defined over `market_kind`, raising `MalformedInstrumentId` for a non-Bybit id) and `MalformedInstrumentId`, with the existing tests of all three sources passing against it and a table test over every id shape the three venues produce (`-USD-PERP.DYDX`, `-LINEAR.BYBIT`, `-SPOT.BYBIT`, `-USD-PERP.HYPERLIQUID`)

4. **Given** `ARRIVAL_MARGIN_NS` (300 s) and the five other skew-related constants (`_FILE_MARGIN_NS`, `_TS_INIT_MARGIN_NS`, `_MAX_CATCH_UP_SECONDS`, `hold_back_seconds + _VENUE_AHEAD_NS`, the backfill refusal)
**When** `kernel/clocks.py` lands
**Then** it holds `TwoClocks`, ns helpers, `CatalogFileSpan` (the former `_stamp_to_ns` stem parse plus `covers(ts_event)`) and the single `MAX_TS_INIT_SKEW_NS`; all six `collector_core` modules that imported `_stamp_to_ns` use `CatalogFileSpan`; every other constant is defined as an expression of, or asserted ≤, `MAX_TS_INIT_SKEW_NS` by a kernel test; `kernel/archive_markers.py` holds the `ArchiveGap` value object and the pure encode/decode of `_archive_gaps/<iid>.jsonl`, with `collector.py` and the archive tools reading and writing through it (writers unchanged: capture `write_failed`/`quarantined`, prune `pruned`)

5. **Given** `venue_http` is used by `trade_backfill.py` and `compare_klines.py`, and `catalog_stats` read helpers by capture, archive, views and ranking
**When** `kernel/venue_http.py` and `kernel/catalog_files.py` land
**Then** every stdlib REST request in `collector_core/` is built through `kernel.venue_http` (a literal venue URL in a moved context is a boundary-test failure; `ranking_engine`'s duplicate maps stay until Story 25.2 and are listed in that story), `catalog_files.py` exposes `data_file_ranges`, `second_ohlc_arrays`, `query_second_ohlc`, `files_by_day` with no write path and no `ParquetDataCatalog` construction, `rebuild_seconds.py` no longer imports `build_candles._files_by_day`, and `parquet_compat.py` is the one place the zstd `write_table` patch is applied, imported by `collector.py` and `backfill_bars.py`

6. **Given** MR4
**When** the story is merged
**Then** all three dockerfiles `COPY` `kernel`, `test_images.py` and `test_boundaries.py` pass, `platform/CLAUDE.md`'s "Adding a venue" step 5 points at `kernel/venues.py`, `ARCHITECTURE.md` and `docs/DATA_DICTIONARY.md` cite the kernel paths, and the parent spine's Deferred entry "Writer→reader imports contradict AD-4" is amended to record that `error_ledger` (23.1) and the shared types, clocks and read helpers (23.2) are resolved, with `candle_store` remaining for Story 24.1

## Tasks / Subtasks

- [ ] Task 1 — `platform/kernel/` package with exactly the AD-D3 members (AC: #1)
  - [ ] Move `collector_core/second_snapshot.py` → `kernel/second_snapshot.py` and add `SecondOHLC` (from `ml_signals/catalog_stats.py:74`); `collector_core/open_interest.py` → `kernel/open_interest.py`; `collector_core/fold.py` → `kernel/fold.py`; pure `Indicator` classes + stateless functions of `ml_signals/indicators.py` → `kernel/indicators.py`; `ml_signals/performance_metrics.py` → `kernel/performance_metrics.py`. Nothing else enters; the boundary test asserts kernel imports no context and holds no module-level mutable state.
  - [ ] Shims at every old path (`REMOVE_AFTER = "24-2-views-read-models-and-reader-side-revalidation-removed"`); chase every caller (`grep -rn "from collector_core.second_snapshot\|from collector_core.open_interest\|from collector_core.fold\|from common\|from ml_signals.venue\|from ml_signals.indicators\|from ml_signals.performance_metrics\|from ml_signals.catalog_stats import" platform`).
- [ ] Task 2 — class-name and Arrow-registration proof (AC: #2)
  - [ ] Extend `platform/tests/test_namespace.py`: `old.DydxSecondSnapshot is kernel.second_snapshot.DydxSecondSnapshot`; `nautilus_trader.serialization.arrow.serializer._SCHEMAS` holds exactly one key whose `__name__` is `DydxSecondSnapshot` and one `OpenInterest`.
  - [ ] Fixture test: write one `DydxSecondSnapshot` and one `OpenInterest` to a tmp catalog with the pre-move code (record the parquet bytes/rows as a fixture before moving), read them back after the move; serialise a `snapshots:raw` batch and compare to the recorded JSON. Replace `data_api/live_candles.py:183`'s parsing with `DydxSecondSnapshot.from_dict` if it is not already.
- [ ] Task 3 — `kernel/venues.py`, the only `InstrumentId` parser (AC: #3)
  - [ ] Merge `common/venues.py` (`VENUE_KINDS`, `venue_kind`, `market_kind`) and `ml_signals/venue.py` (`venue_of`, `MalformedInstrumentId`); move `collector_core/venue_http.py:48` `bybit_category` here, defined over `market_kind` (`-LINEAR.BYBIT`/`-INVERSE.BYBIT` → `linear`/`inverse`, `-SPOT.BYBIT` → `spot`, anything else `MalformedInstrumentId`). Table test over every id shape. `common/` becomes a shim package.
- [ ] Task 4 — `kernel/clocks.py` and `kernel/archive_markers.py` (AC: #4)
  - [ ] `clocks.py`: `NS_PER_S`, `TwoClocks(ts_event, ts_init)` (frozen dataclass), `CatalogFileSpan.from_stem(stem)` (the `_stamp_to_ns` parse, `ml_signals/catalog_stats.py:158`) with `covers(ts_event, margin=MAX_TS_INIT_SKEW_NS)`, and `MAX_TS_INIT_SKEW_NS = 300 * NS_PER_S` (today `archive_gaps.ARRIVAL_MARGIN_NS`). Replace the six `_stamp_to_ns` imports (`collector.py:141`, `build_candles.py:42`, `consolidate_catalog.py:72`, `prune_catalog.py:51`, `compare_klines.py:91`, `rebuild_seconds.py:80`) and `ARRIVAL_MARGIN_NS`. Kernel test asserts `catalog_stats._FILE_MARGIN_NS <= MAX`, `rebuild_seconds._TS_INIT_MARGIN_NS == MAX`, `collector._MAX_CATCH_UP_SECONDS*NS <= MAX`, `max(hold_back_seconds over the three config.toml) * NS + collector._VENUE_AHEAD_NS <= MAX`, backfill refusal == MAX.
  - [ ] `archive_markers.py`: `ArchiveGap(iid, from_ns, to_ns, reason, count)` frozen dataclass, `GAPS_DIRNAME = "_archive_gaps"`, `encode(gap) -> str`, `decode(line) -> ArchiveGap`, `path_for(catalog, iid)`. `collector_core/archive_gaps.py` keeps `record_gap`/`load_gaps`/`in_gap` over these and becomes a thin module (moves to archive in 25.1).
- [ ] Task 5 — `kernel/venue_http.py`, `kernel/catalog_files.py`, `kernel/parquet_compat.py` (AC: #5)
  - [ ] Move `collector_core/venue_http.py` (minus `bybit_category`) to `kernel/venue_http.py`; `trade_backfill.py` and `compare_klines.py` import it; `ranking_engine/engine.py:146-162` stays until 25.2 (note it in the boundary test's `LEGACY_EDGES_UNTIL`).
  - [ ] `catalog_files.py`: move `data_file_ranges`, `second_ohlc_arrays`, `query_second_ohlc` from `catalog_stats.py` and `files_by_day` from `build_candles.py` (`_files_by_day`), read-only, no `ParquetDataCatalog` construction (use `pyarrow.parquet` + `CatalogFileSpan`); `rebuild_seconds.py` imports `kernel.catalog_files.files_by_day`.
  - [ ] `parquet_compat.py`: `apply_zstd_default()` installing the `pq.write_table` wrapper once (idempotent, guarded by a module flag); `collector.py:231-243` and `backfill_bars.py:137-148` call it.
- [ ] Task 6 — docs, images, lists (AC: #6)
  - [ ] All three dockerfiles `COPY platform/kernel ./kernel`; Makefile lists add `kernel/tests`; `platform/CLAUDE.md` "Adding a venue" step 5 → `kernel/venues.py`; `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md` §1.7/§1.8/§2.1 cite kernel; amend the parent spine's Deferred "Writer→reader imports" entry (partially resolved: ledger 23.1; types/clocks/read helpers 23.2; `candle_store` pending 24.1).

## Dev Notes

### Start from the prior attempt (operator decision, 2026-09-22, revised 18:55)

A complete, twice-reviewed attempt at this story exists as six commits on branch
`bmad-loop/20260921-181822-125a/23-2-kernel-shared-kernel` (tip `d7477299ac`, based on
`77730e2995`, 143 files, +4293/-1689). Its spec ends at `status: done` with
`followup_review_recommended: false`. The orchestrator rejected it only because the spec's
`baseline_revision` (`7bd64952fd`, carried over from a cherry-pick) did not equal the baseline the
orchestrator recorded for the worktree; the code was never faulted. Use it, do not rebuild it:

1. Before touching anything, record the worktree's starting commit: `BASE=$(git rev-parse HEAD)`.
   This is the baseline the orchestrator recorded for this attempt.
2. Cherry-pick, in this order: `692af555ea` (kernel/ package, shims, caller repointing,
   `test_boundaries.py` guards), `692becfde1` (review triage), `638aca661e` (17 follow-up
   patches), `49406f9a27` (follow-up triage), `df50abab0f` (second follow-up triage),
   `d7477299ac` (final-revision stamp). They apply cleanly onto `77730e2995`; if `troll` has moved,
   conflicts can only be in `_bmad-output/` and are resolved by keeping both sides.
3. Then set the spec frontmatter of `spec-23-2-kernel-shared-kernel.md` to `baseline_revision:
   '<BASE>'` (the cherry-picked value is stale and is exactly what deferred the previous attempt),
   `status: 'in-progress'` and `review_loop_iteration: 0`, and commit that as its own commit. From
   here follow the skill as normal: the diff since `<BASE>` is the whole story.
4. Verify rather than redesign: run the full `platform/` suite and `test_boundaries.py`, check each
   AC against the spec's Verification section, fix what fails, and stamp `final_revision` to the
   last commit when finalising. Record in Completion Notes what was kept, changed and dropped.

The kernel is the one package every context imports, so nothing stateful, no store and no config loader may enter (AD-D3). `DydxSecondSnapshot`'s class name is a persistence identifier: `nautilus_trader.persistence.funcs.class_to_filename` derives the catalog directory `custom_dydx_second_snapshot` from `__name__`. `register_arrow` keys by class object — a shim must re-export the class, never copy it. `MAX_TS_INIT_SKEW_NS` is the coupling constant between capture's carry/backfill rules and archive's rebuild/prune windows (adversary review C1/H1 in `reviews/review-adversary.md`); expressing every related constant against it is the point of `clocks.py`.

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

- Spine sections: AD-D3, AD-D7, AD-D18
- Review findings that shaped this story: reviews/review-adversary.md C1, H1, H4, M4, M5, M7, M8; reviews/review-versions.md M-4
- Code: `collector_core/{second_snapshot,open_interest,fold,venue_http,archive_gaps}.py`, `common/venues.py`, `ml_signals/{venue,indicators,performance_metrics,catalog_stats}.py`, `nautilus_trader/persistence/funcs.py:39-51`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
