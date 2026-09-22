# Epic 23 Context: Migration guardrails, observability and the shared kernel

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

First epic of the DDD migration of `platform/` into eleven bounded contexts plus one shared kernel. The `troll/` → `platform/` rename, with durable stores moved under `platform/data/`, is already done. This epic installs the enforcement that catches every later context move in `make test`: import-boundary, image-closure and hot-path tests. It also extracts the two contexts everything else depends on. `observability/` gets one error ledger, one outbound notifier, the generic watchdog transition and the incident handler. `kernel/` gets the single copy of each shared type, fold, parser, constant and transport. Finally the error ledger becomes durable, so that a day-long clean run can be proven with a cross-check against the archive. Together these close two live Docker image gaps now and make Epics 24–26 safe to run as small, independently deployable moves.

## Stories

- Story 23.1: `observability/` context, one notifier, and the migration's enforcement tests
- Story 23.2: `kernel/` shared kernel
- Story 23.3: Durable error ledger and the day-long data/error cross-check

## Requirements & Constraints

- **Each story deploys alone and the published language is frozen.** For the whole migration, none of the following may change:
  - Parquet schemas and catalog directory names
  - every Redis channel payload (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:*`, `collector:*`)
  - the SQLite schemas (`candles_<venue>.db`, `metrics.db`, `fills.db`) and the TOML key sets
  - compose service names, existing env vars (including `WATCHDOG_NTFY_URL`, `TELEGRAM_*`, `CATALOG_PATH`) and the `platform/data/` bind mounts

  New env vars and mounts are allowed only as additions.
- **Shims:** a shim at an old import path is exactly `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. It defines nothing itself, because a copied class body would register a second Arrow class and break `is` dispatch. A shim is deleted within two stories. Every in-repo caller is updated in the same story, and a `DeprecationWarning` during the test run counts as a failure (TEST-04).
- **Same-commit doc/build updates:** each move updates all of these in the same commit:
  - `platform/CLAUDE.md` citations, `ARCHITECTURE.md` and `docs/DATA_DICTIONARY.md`
  - the `COPY` sets of all three thin dockerfiles (`collector`, `data_api`, `live_paper`)
  - compose `command:` lines
  - both Makefile test lists (`test`, `test-live-paper`)
- **Parent-spine Deferred items:** when a story resolves one, it strikes the item in the parent spine with an amendment.
- **Stdlib-only tooling:** enforcement tests are plain pytest over `ast`, `tracemalloc` and `time.perf_counter_ns`. Add no new dependency (`pytest-benchmark` is not in the collector image).
- **Hot-path budget:** the refactor may add no per-message allocation or wrapper. The replay baseline is recorded once in 23.1. Every later run asserts allocations ≤ baseline and wall time ≤ 2× baseline, and results must be deterministic across three consecutive runs on the same host.
- **Failure handling (DATA-07):** every site that continues past a failure calls `error_ledger.record(site, detail, exc)`, one site per event type. Failures are never swallowed or filtered, and ledger write failures are counted and logged too.
- **Memory (MEM-01):** readers load no unbounded catalog slices and work one day and one instrument at a time.
- **Simplifications:** any deliberate simplification is a `Known limit:` comment that names the ceiling and the upgrade path.

## Technical Decisions

- **Contexts are top-level packages** with `platform/` on `sys.path`. Never import anything with a `platform.` prefix, and never create `platform/__init__.py`, because `platform` is the stdlib module name.
- **Target context graph.** Every context may import `kernel` and `observability`. Beyond that, only these edges are legal:
  - `collection_control → capture`
  - `archive → candles`
  - `views → candles` and `views → ranking`
  - `data_api` may import `views`, `alerting`, `kernel` and `observability`
  - `bot_tui` may import `views`, `kernel` and `observability`

  `kernel` imports no context, and `observability` imports only the standard library.
- **Rules inside a context:**
  - `domain/` may import only the stdlib, `kernel` and `nautilus_trader.model`/`core` types.
  - `application/` holds `Protocol` ports and the asyncio loops.
  - `infrastructure/` is imported only by the composition root.
  - A venue `policies.py` counts as `domain/`.
- **`test_boundaries.py`:**
  - Judges each import edge by the target contexts of both ends, using a static `LEGACY_MODULE_TO_CONTEXT` map. An unmapped module is a failure.
  - Bans `_private` imports across contexts.
  - Exempts only edges inside one unmoved package.
  - Asserts that `research` imports nothing from `data_api`.
  - Skips `frontend/`, `node_modules/` and `data/`.
- **`test_images.py`:** for every compose `command:` and every Makefile/cron `-m` module, computes the top-level-package import closure and requires each package to be in that service's dockerfile `COPY` set. A shim counts as its target.
- **`test_namespace.py`:**
  - asserts `old.X is new.X` for every shim name
  - asserts one `_SCHEMAS` key per kernel class `__name__`
  - fails when a shim's `REMOVE_AFTER` story is `done` in `sprint-status.yaml`
- **`observability/` modules:** `error_ledger`, `notify`, `watchdog` and `incidents`.
  - `notify(channel, title, body)` is the only outbound transport. Its ntfy, Telegram and webhook adapters are chosen by env. An `Alert` names a channel, never a transport.
  - The watchdog is only the generic `(down_since, reminder)` transition over a boolean. Whether a feed counts as silent is still decided by the collector.
  - The incident handler gets its instrument-id pattern from the dYdX entrypoint and holds no venue token.
- **`kernel/` membership is exactly the following, nothing more:**
  - `second_snapshot` (`DydxSecondSnapshot`, `SecondOHLC`), `open_interest`, `fold`, `indicators` (pure only), `performance_metrics`
  - `venues`: the only `InstrumentId` parser, providing `venue_of`, `venue_kind`, `market_kind`, `bybit_category` and `MalformedInstrumentId`
  - `clocks`: `TwoClocks`, `CatalogFileSpan` and the single `MAX_TS_INIT_SKEW_NS` = 300 s. Every other skew margin is defined as ≤ this value, and a test asserts it.
  - `archive_markers`: `ArchiveGap` plus the encode/decode of `_archive_gaps/<iid>.jsonl`
  - `venue_http`: every stdlib venue REST request. A literal venue URL anywhere outside the kernel is a failure.
  - `catalog_files`: read-only helpers, with no `ParquetDataCatalog` construction
  - `parquet_compat`: the one zstd `write_table` patch

  The kernel holds no module-level mutable state, no store, no config loader and no ledger calls.
- **Class names are persistence identifiers:** `DydxSecondSnapshot` and `OpenInterest` keep their names because catalog directories are derived from `__name__`. Arrow schemas stay byte-identical and `register_arrow` runs exactly once per class.
- **Ledger Known limit (23.1):** the ledger is per process, so `/api/errors` shows only `data_api`'s own sites. The upgrade path is an `errors:ledger` channel. Story 23.3 replaces this limit with durable JSONL files:
  - Location: `<ERROR_LEDGER_DIR>/<service>.jsonl`, one file per service.
  - Record fields: `ts_ns`, `service`, `pid`, `site`, `detail`, `exc_type`, `suppressed`.
  - Each process writes a `process_start` line when its ledger initialises.
  - Writes are capped per site per minute and carry exact suppressed counts.
  - Rotation is by size: 20 MB × 10 by default.
  - With the env var unset, behaviour is unchanged.
- **Durable stores live only under `platform/data/`.**

## Cross-Story Dependencies

- **Order is fixed: 23.1 → 23.2 → 23.3.** Epics 24–26 depend on all three. Context moves then follow this order: observability → kernel → candles → views → alerting → research → archive → ranking → bots → collection_control → capture.
- **23.1** creates the boundary, image and namespace-style guardrails and the hot-path baseline. Every later story must keep them passing. 23.1 also closes the `data_api` image gap (missing `collector_core`, `common`) and the `live_paper` image gap, and strikes the parent Deferred entry for the image gap.
- **23.2** amends the parent Deferred entry "Writer→reader imports contradict AD-4": the `error_ledger` (23.1) and the shared types, clocks and read helpers (23.2) are resolved, and `candle_store` remains for Story 24.1. The duplicate venue URL maps in `ranking_engine` stay until Story 25.2.
- **23.3** builds on 23.1's `error_ledger.record` and 23.2's `kernel.catalog_files` and `MAX_TS_INIT_SKEW_NS`. Its `collector_core.crosscheck_errors` maps to the `archive` context and moves with it in Story 25.1. It closes Epic 22 operator checks 22.5 #1, 22.1 #2, 22.10 #4 and 22.12 #5 through a "Day-long clean-run check" section in `DEPLOY_CHECKLIST.md`. It also amends the spine's AD-D16 Known limit.
- **`notify`** from 23.1 is consumed later by the `alerting` context's `Deliverer` in Epic 24.
