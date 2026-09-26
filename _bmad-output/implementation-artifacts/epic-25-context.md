# Epic 25 Context: Archive, ranking, bots and collection control as aggregates

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the third epic of the DDD migration of `platform/`. It moves four supporting and core contexts (`archive/`, `ranking/`, `bots/`, `collection_control/`) out of their legacy packages into their own bounded contexts, and gives each one an aggregate that enforces a real invariant. After it lands, the nightly saga cannot zero rows over an archive gap or reconcile a day that was never rebuilt. The ranking engine has no module globals. The bots' paper/real split is enforced by types. A venue's collected set is the plan capture actually applied, not the plan control intended. The contexts touch disjoint files. Every move keeps the live wire, file and store contracts byte-identical, so each story can be deployed on its own to the three 24/7 writers.

## Stories

- Story 25.1: `archive/` context: `ArchiveDay`, one deleter, one rewriter, one writer per leaf
- Story 25.2: `ranking/` context: `RankingBoard` replaces the module globals
- Story 25.3: `bots/` context: paper and non-paper as types, Nautilus behind an ACL
- Story 25.4: `collection_control/` context: the plan is the intent, the applied set is the fact

## Requirements & Constraints

- **Deployable alone, published language frozen.** For the whole migration these stay unchanged: every Parquet schema and catalog directory name; the Redis payloads (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`); the `candles_<venue>.db`/`metrics.db`/`fills.db` schemas; the TOML key sets, including the venue `config.toml`; compose service names; env vars; and the `platform/data/` bind mounts. Replay or byte-identity tests prove this wherever a story rewires a publisher.
- **Shims.** Every old import path becomes a pure re-export shim: `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. A shim defines nothing, and it is deleted no later than two stories after it appears. `test_namespace.py` asserts `old.X is new.X` and fails once the `REMOVE_AFTER` story is `done`.
- **Same-commit housekeeping.** Each move updates, in the same commit: the `platform/CLAUDE.md` citations, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, the dockerfile `COPY` sets (`test_images.py` proves the import closure), compose `command:` lines and both Makefile test lists (`test`, `test-live-paper`).
- **Parent-spine Deferred items.** When a story resolves one, it strikes it with an amendment. 25.2 resolves "`open_interest` vs `volume24h` polling in different namespaces".
- **Failures are ledgered.** Every tolerated failure goes through `observability.error_ledger.record` at one site per event type. New sites named in this epic: `reconcile.not_rebuilt`, `repair.capture_running`, `ranking_engine.volume24h`, `collector.subscribe_failed`, `collector.unplanned_message`.
- **Invariant tests.** Every aggregate ships one invariant test per command, and every port ships a contract test that its adapters run.
- **Binding rules.** The DATA/OBS/MEM/NAUT/SSOT/TEST rules in `platform/CLAUDE.md` still apply. Deliberate simplifications are written as `Known limit:` comments that name the ceiling and the upgrade path.

## Technical Decisions

- **Layering in every context.** `domain/` imports only the stdlib, `kernel/` and Nautilus model/core types, with no I/O, asyncio, Redis, SQLite or Parquet. `application/` declares ports as `typing.Protocol` and holds services and loops. `infrastructure/` implements the ports and is imported only by the composition root (`__main__`). There is no DI container and no event bus. Contexts are top-level packages and are never imported with a `platform.` prefix. `test_boundaries.py` enforces the legal edges: archive → candles (`mark_verified`, `verified_status`, `window`, `rebuild_day`), and collection_control → capture (`apply` plus read-only counters). No private names are imported across contexts.
- **No module-level mutable runtime state** in any context (boundary-tested). State lives in aggregates or in service instances built at the composition root.
- **Kernel reuse.** Every venue REST call goes through `kernel.venue_http`, which means no literal venue URLs outside the kernel. Catalog reads go through `kernel.catalog_files`, zstd writes through `kernel.parquet_compat`, the skew window comes from `kernel.clocks.MAX_TS_INIT_SKEW_NS` (never a second literal), gap markers use `kernel.archive_markers.ArchiveGap`, and `snapshots:raw` is parsed only via `DydxSecondSnapshot.from_dict`.
- **Archive (AD-D9/AD-D18).**
  - `ArchiveDay` states run provisional → rebuilt → verified or mismatched → released. The only persisted status is the `verified_days` row, owned by candles and reached through the `VerifiedDays` port.
  - `rebuilt` is carried in-process as a `StepResult` within one saga run. The saga stops at the first failure and ledgers it.
  - `RetentionPolicy` is the only code that deletes catalog files. `CatalogFiles.rewrite` is the only in-place rewriter: temp-then-rename, Arrow metadata preserved.
  - `backfill_bars` and `repair_catalog` are the only offline `write_data()` callers, and `backfill_bars` shares the `VenueKlines` ACL with `compare_klines`.
  - Each catalog leaf has one writer. Capture holds `.capture-<venue>.lock` for its whole run, and archive tools never write a file whose `ts_init` span intersects the current UTC day.
  - Archive is the only writer of `pruned` markers and the only reader of the `_archive_gaps/` markers.
- **Ranking (AD-D10).**
  - `RankingBoard` owns `mode`, per-instrument `InstrumentMetrics` (indicators, rolling windows, price series, last-seen, `VolumeReading(value_usd, observed_ns)`) and the publisher.
  - The pct/volatility math is `ranking/domain`'s alone. Views and research read those values from `rankings:live` or `metrics.db` through the ranking query service and never recompute them.
  - Each venue's `parse_*_volume_24h` stays pure and fixture-tested.
- **Bots (AD-D15).**
  - `PaperFleet` and `ExecBot` are distinct aggregate types, built by distinct loaders from distinct files. `PaperConfig` rejects a `mode` key. `ExecConfig.mode` selects only between the non-Sandbox modes, validated against `environment`.
  - Known limit: demo vs real inside `ExecConfig` is a validated value, not a type.
  - `Bot.id == order_id_tag`. The `Incident` list is bounded at 50.
  - Only `nautilus_host.py` imports `TradingNode`, and cache reads are filtered by `strategy_id`.
- **Collection control (AD-D17).**
  - `CollectionPlan` has these invariants: dYdX cap = 30, `exclude ∩ collected = ∅`, and pins admitted only by USD-volume `classify_liquidity`. Its commands return plan diffs.
  - Capture's `apply(plan_diff)` returns `Applied(subscribed, unsubscribed, failed)`. The sampler iterates `applied ∩ plan`. A book or `LiveBook` exists only for a subscribed instrument.
  - Control holds no prune loop. Instead, retention is expressed as plan attributes that the nightly `RetentionPolicy` reads.
  - There is one `config.toml` loader. Plan saves are full rewrites (Known limit: comments are lost).
  - Known limit: only dYdX has a live plan. Bybit and Hyperliquid have static tuples, applied once at start.

## Cross-Story Dependencies

- **Fixed order: 25.1 → 25.2 → 25.3 → 25.4.** This follows the migration order (…research → archive → ranking → bots → collection_control → capture). Epic 24 is done, so `kernel/`, `observability/`, `candles/` (with the `VerifiedDays` port and `forming_bar`), `views/` and `research/` already exist.
- **Shim expiry chains.** Archive shims expire at 25-3, `ranking_engine` at 25-4, `live_paper` at 26-1 and the moved `dydx_collector` modules at 26-2. The `ml_signals` package is deleted in 25.2.
- **25.1 → 25.4.** 25.1 deletes `DydxCollector._prune_loop` and moves dropped-instrument/delta retention into `RetentionPolicy`, which reads `non_config_retain_hours` from the venue plan file. 25.4 must not reintroduce a pruner. 25.1 also adds the collector's capture lock, which is the only capture edit in that story.
- **25.4 → Epic 26.** 25.4 adds `Collector.apply` on the legacy capture class. Epic 26 (the capture move) later owns it as `CaptureService.apply`.
- **Frozen consumers.** `bot_tui` must need no change for 25.3 or 25.4, since its `bots:*` and `collector:status` payloads are frozen.
