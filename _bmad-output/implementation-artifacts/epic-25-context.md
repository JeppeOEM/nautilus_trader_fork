# Epic 25 Context: Archive, ranking, bots and collection control as aggregates

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the third epic of the DDD migration of `platform/`. It moves four contexts (`archive/`, `ranking/`, `bots/`, `collection_control/`) out of their legacy packages into their own bounded contexts, and gives each one an aggregate that enforces a real invariant. After it lands, the nightly saga cannot zero rows over an archive gap or reconcile a day that was never rebuilt. The ranking engine has no module globals. The bots' paper/real split is enforced by types. A venue's collected set is the plan capture actually applied, not the plan control intended. Before the ranking move, rankings became web-only: the ranking-mode switch moved to the web and the TUI's Coins pane was deleted, so no later story refactors code that no longer exists. The contexts touch disjoint files. Every move keeps the live wire, file and store contracts byte-identical, so each story can be deployed on its own to the three 24/7 writers.

## Stories

- Story 25.1: `archive/` context: `ArchiveDay`, one deleter, one rewriter, one writer per leaf
- Story 25.1a: Rankings web-only: the ranking-mode toggle moves to the web and the TUI's Coins pane is deleted
- Story 25.2: `ranking/` context: `RankingBoard` replaces the module globals
- Story 25.3: `bots/` context: paper and non-paper as types, Nautilus behind an ACL
- Story 25.4: `collection_control/` context: the plan is the intent, the applied set is the fact

## Requirements & Constraints

- **Deployable alone, published language frozen.** For the whole migration these stay unchanged: every Parquet schema and catalog directory name; the Redis payloads (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`); the `candles_<venue>.db`/`metrics.db`/`fills.db` schemas; the TOML key sets, including the venue `config.toml`; compose service names; env vars; and the `platform/data/` bind mounts. Replay or byte-identity tests prove this wherever a story rewires a publisher.
- **Shims.** Every old import path becomes a pure re-export shim: `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. A shim defines nothing, and it is deleted no later than two stories after it appears. `test_namespace.py` asserts `old.X is new.X` and fails once the `REMOVE_AFTER` story is `done`.
- **Same-commit housekeeping.** Each move updates, in the same commit: the `platform/CLAUDE.md` citations, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, the dockerfile `COPY` sets (`test_images.py` proves the import closure), compose `command:` lines and both Makefile test lists (`test`, `test-live-paper`).
- **Parent-spine Deferred items.** The story that resolves one strikes it with an amendment. 25.2 resolves "`open_interest` vs `volume24h` polling in different namespaces".
- **Failures are ledgered.** Every tolerated failure goes through `observability.error_ledger.record`, with one site per event type. The new sites this epic names are `reconcile.not_rebuilt`, `repair.capture_running`, `ranking_engine.volume24h`, `collector.subscribe_failed` and `collector.unplanned_message`.
- **Invariant tests.** Every aggregate ships one invariant test per command, and every port ships a contract test that its adapters run.
- **Rankings stay web-only.** The TUI has only the Bots and Collector panes and reads only `bots:*` and `collector:status`. Nothing under `bot_tui/` may subscribe to `rankings:live` or `snapshots:raw`. The ranking mode is global and last-write-wins, and it is set only by the web's `PUT /api/rankings/mode`. That endpoint publishes the exact message the old TUI toggle sent. `RANKING_COLS` in `views/ranking_columns.py` is the web's single column source.
- **Binding rules.** The DATA/OBS/MEM/NAUT/SSOT/TEST rules in `platform/CLAUDE.md` still apply. A deliberate simplification is written as a `Known limit:` comment that names the ceiling and the upgrade path.

## Technical Decisions

- **Layering in every context.** `domain/` imports only the stdlib, `kernel/` and Nautilus model/core types. It does no I/O and uses no asyncio, Redis, SQLite or Parquet. `application/` declares ports as `typing.Protocol` and holds the services and loops. `infrastructure/` implements the ports and is imported only by the composition root (`__main__`). There is no DI container and no event bus. Contexts are top-level packages, never imported with a `platform.` prefix. `test_boundaries.py` enforces the legal edges: archive → candles (`mark_verified`, `verified_status`, `window`, `rebuild_day`) and collection_control → capture (`apply` plus read-only counters). No private names are imported across contexts.
- **No module-level mutable runtime state** in any context. A boundary test enforces this. State lives in aggregates or in service instances built at the composition root.
- **Kernel reuse.** These go through the kernel:
  - every venue REST call, via `kernel.venue_http` (no literal venue URLs outside the kernel);
  - catalog reads, via `kernel.catalog_files`;
  - zstd writes, via `kernel.parquet_compat`;
  - the skew window, via `kernel.clocks.MAX_TS_INIT_SKEW_NS` (never a second literal);
  - gap markers, via `kernel.archive_markers.ArchiveGap`;
  - `snapshots:raw` parsing, via `DydxSecondSnapshot.from_dict` only.
- **Archive (landed in 25.1; later stories must not bypass it).**
  - `ArchiveDay` runs provisional → rebuilt → verified or mismatched → released. The only persisted day status is the `verified_days` row, reached through candles' `VerifiedDays` port.
  - The rebuilt state lives in-process as a `StepResult` inside one saga run. `reconcile_day` refuses unless the same run rebuilt that day.
  - `RetentionPolicy` is the only code that deletes catalog files. This covers dropped-instrument and `order_book_deltas` retention too, so no venue package or control loop may hold a prune loop.
  - `CatalogFiles.rewrite` is the only in-place rewriter.
  - There is one writer per catalog leaf. Capture holds `.capture-<venue>.lock` for its whole run.
  - Open-day rule: rewrites are row-scoped, so only closed-day rows may change. Merging, whole-file rewrites and deletes are refused for any file that reaches today.
- **Ranking (AD-D10).**
  - `RankingBoard` owns `mode`, the per-instrument `InstrumentMetrics` (indicators, rolling windows, price series, last-seen, `VolumeReading(value_usd, observed_ns)`) and the publisher.
  - The ports are `VolumeSource` (one adapter per venue), `PriceHistory`, `RankingHistory` and `LivePublisher`.
  - The pct/volatility math belongs to `ranking/domain` alone. Views and research read those values from `rankings:live` or `metrics.db` through the ranking query service and never recompute them.
  - Each venue's `parse_*_volume_24h` stays pure and fixture-tested.
- **Bots (AD-D15).**
  - `PaperFleet` and `ExecBot` are distinct aggregate types, built by distinct loaders from distinct files. `PaperConfig` rejects a `mode` key. `ExecConfig.mode` selects only among the non-Sandbox modes and is validated against `environment`.
  - Known limit: demo vs real inside `ExecConfig` is a validated value, not a type.
  - `Bot.id == order_id_tag`, and the `Incident` list is bounded at 50.
  - Only `nautilus_host.py` imports `TradingNode`, and cache reads are filtered by `strategy_id`.
- **Collection control (AD-D17).**
  - `CollectionPlan` has these invariants: the dYdX cap is 30, `exclude ∩ collected = ∅`, and pins are admitted only by `classify_liquidity` on USD volume. Its commands return plan diffs.
  - Capture's `apply(plan_diff)` returns `Applied(subscribed, unsubscribed, failed)`. The sampler iterates `applied ∩ plan`. A book or `LiveBook` exists only for a subscribed instrument.
  - Retention is expressed as plan attributes that the nightly `RetentionPolicy` reads.
  - There is one `config.toml` loader, and it returns `(CoreConfig, CollectionPlan)`.
  - Known limit: plan saves are full rewrites, so comments in the file are lost.
  - Known limit: only dYdX has a live plan. Bybit and Hyperliquid have static tuples, applied once at start through the same `apply`.

## Cross-Story Dependencies

- **Fixed order: 25.1 → 25.1a → 25.2 → 25.3 → 25.4.** 25.1 and 25.1a are done. Epic 24 is done, so `kernel/`, `observability/`, `candles/`, `views/`, `research/` and `archive/` already exist.
- **Shim expiry chain.** 25.3 must delete the archive shims. 25.4 must delete the `ranking_engine` shim. The `live_paper` shim expires at 26-1 and the moved `dydx_collector` modules at 26-2. 25.2 deletes the `ml_signals` package outright.
- **25.1a → 25.2.** `RankingBoard.switch_mode` must accept the mode message that the web endpoint now publishes, byte for byte.
- **25.1 → 25.4.** `DydxCollector._prune_loop` is already gone, and `RetentionPolicy` reads `non_config_retain_hours` from the venue plan file. 25.4 must not reintroduce a pruner, and the plan must keep exposing those retention attributes.
- **25.4 → Epic 26.** `Collector.apply` goes on the legacy capture class. The capture move in Epic 26 later owns it as `CaptureService.apply`.
- **Epic 29 builds on this epic.** Several of its stories use this epic's outputs:
  - 29.2 (per-venue Collector pane) and 29.4 (runtime control for Bybit/Hyperliquid) need 25.4's per-venue `CollectionPlan` and applied-set report.
  - 29.5 (market browser) needs `ranking/`'s per-venue market polls from 25.2.
- **Frozen consumers.** `bot_tui` must need no change for 25.3 or 25.4, since its `bots:*` and `collector:status` payloads are frozen.
