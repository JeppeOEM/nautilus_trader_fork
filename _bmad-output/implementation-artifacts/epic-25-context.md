# Epic 25 Context: Archive, ranking, bots and collection control as aggregates

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the third epic of the DDD migration of `platform/`. It moves four contexts (`archive/`, `ranking/`, `bots/`, `collection_control/`) out of their legacy packages into their own bounded contexts, and gives each one an aggregate that enforces a real invariant. After it lands, the nightly saga cannot zero rows over an archive gap or reconcile a day that was never rebuilt. The ranking engine has no module globals. The bots' paper/real split is enforced by types, so no config key, control message or list reorder can promote a bot to real money. A venue's collected set is the plan capture actually applied, not the plan control intended. Rankings are web-only, and the TUI controls only bots and the collector. Every move keeps the live wire, file and store contracts byte-identical, so each story can be deployed on its own to the three 24/7 writers.

## Stories

- Story 25.1: `archive/` context: `ArchiveDay`, one deleter, one rewriter, one writer per leaf
- Story 25.1a: Rankings web-only: the ranking-mode toggle moves to the web and the TUI's Coins pane is deleted
- Story 25.2: `ranking/` context: `RankingBoard` replaces the module globals
- Story 25.3: `bots/` context: paper and non-paper as types, Nautilus behind an ACL
- Story 25.4: `collection_control/` context: the plan is the intent, the applied set is the fact

## Requirements & Constraints

- **Deployable alone, published language frozen.** These stay unchanged for the whole migration:
  - every Parquet schema and catalog directory name;
  - the Redis payloads `snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status` and `collector:control`;
  - the `candles_<venue>.db`, `metrics.db` and `fills.db` schemas;
  - the TOML key sets, including the venue `config.toml` and the paper/exec bot configs;
  - compose service names, env vars and the `platform/data/` bind mounts, including the `live-paper` container path `/app/live_paper/data`.

  Wherever a story rewires a publisher, replay or byte-identity tests against recorded messages prove the payload is unchanged.
- **Shims.** Every old import path becomes a pure re-export shim: `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. A shim defines nothing. `test_namespace.py` asserts `old.X is new.X` and fails once the `REMOVE_AFTER` story is `done`, so the story named there must delete its shims.
- **Same-commit housekeeping.** Each move updates these in the same commit: the `platform/CLAUDE.md` citations, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, the dockerfile `COPY` sets (`test_images.py` proves the import closure), compose `command:` lines and both Makefile test lists (`test`, `test-live-paper`).
- **Failures are ledgered.** Every tolerated failure goes through `observability.error_ledger.record`, with one site per event type. The sites still to add in this epic are `collector.subscribe_failed` and `collector.unplanned_message`.
- **Invariant tests.** Every aggregate ships one invariant test per command. Every port ships a contract test that its adapters run.
- **Rankings stay web-only.** Nothing under `bot_tui/` subscribes to `rankings:live` or `snapshots:raw`. The TUI reads only `bots:*` and `collector:status`, and it must need no change for 25.3 or 25.4.
- **Binding rules.** The DATA/OBS/MEM/NAUT/SSOT/TEST rules in `platform/CLAUDE.md` apply. A deliberate simplification is written as a `Known limit:` comment that names the ceiling and the upgrade path.

## Technical Decisions

- **Layering in every context.**
  - `domain/` imports only the stdlib, `kernel/` and Nautilus model/core types. It does no I/O and uses no asyncio, Redis, SQLite or Parquet.
  - `application/` declares ports as `typing.Protocol` and holds the services and loops.
  - `infrastructure/` implements the ports and is imported only by the composition root (`__main__`).
  - There is no DI container and no event bus, and no private names are imported across contexts.
  - Module-level mutable runtime state is forbidden; a boundary test enforces this. State lives in aggregates or in service instances built at the composition root.
  - Contexts are top-level packages, never imported with a `platform.` prefix.
- **Kernel reuse.**
  - Every venue REST call goes through `kernel.venue_http`, and a boundary test fails any literal venue URL or `urllib` request outside it.
  - Catalog reads go through `kernel.catalog_files` and zstd writes through `kernel.parquet_compat`.
  - `snapshots:raw` is parsed only through `DydxSecondSnapshot.from_dict`.
- **Archive (landed; later stories must not bypass it).**
  - `archive.RetentionPolicy` is the only code that deletes catalog files. That includes dropped-instrument (`non_config_retain_hours`) and per-instrument `order_book_deltas` retention, so no venue package or control loop may hold a prune loop.
  - `CatalogFiles.rewrite` is the only in-place rewriter.
  - Capture holds `.capture-<venue>.lock` for its whole run.
- **Ranking (landed).**
  - `RankingBoard` owns the mode and per-instrument metrics.
  - The pct/volatility math lives in `ranking/domain` alone.
  - Volume is polled per venue through `VolumeSource` adapters.
- **Bots (Story 25.3).**
  - `PaperFleet` and `ExecBot` are distinct aggregate types, built by distinct loaders from distinct files.
    - `PaperFleet` has many `Bot`s sharing one Sandbox balance pool per venue. `PaperConfig` has no mode field, and its loader rejects a `mode` key.
    - `ExecBot` is one bot on one subaccount, one bot per file, never the paper `[[bots]]` shape. `ExecConfig.mode` selects only between `exchange_demo` (demo/testnet) and `real_money` (mainnet), is validated against `environment`, and can never select paper.
    - Known limit: demo vs real inside `ExecConfig` is a validated value, not a type. The upgrade path is `ExchangeDemoBot`/`RealMoneyBot`, with the value used only as the loader's discriminator.
  - `Bot.id == order_id_tag`, pinned explicitly and never auto-assigned by insertion order. A `Bot` carries its heartbeat state and a bounded `Incident` list (max 50, `bots:incidents:*`). The node's `TraderId` is fleet-level and bot-agnostic.
  - A `FillLedger` per bot keeps per-fill realized PnL and the rolling `day`/`week`/`month`/`all` buckets, all timestamps in ns:
    - `trades[].realized_pnl` is per fill and `pnl_series[].pnl` is per bucket; neither is cumulative.
    - Buckets are rolling from now, not calendar-aligned.
    - `trades` is capped at 500, and `all` is daily-bucketed.
    - The four keys are refreshed independently on a timer and on each fill.
  - `bots:control` carries only `{bot_id, action: "start" | "stop"}` and never a mode. `bots:status` publishes on change and on a heartbeat, and a missing heartbeat must read as stale. Readers only `GET` the history keys and never read Nautilus's Cache encoding.
  - The Nautilus ACL:
    - Only `bots/infrastructure/nautilus_host.py` imports `TradingNode`, and `test_boundaries.py` asserts this.
    - It builds one node per process, with one data client and one Sandbox exec client per venue, taken from the `VENUES` table.
    - `cache_reader.py` computes each bot's PnL and exposure from `cache.positions_open/closed(strategy_id=...)`, never from the portfolio's instrument-scoped aggregates, because those blend bots that share an instrument.
    - The Nautilus `Cache`, persisted in Redis, remains the durable order/position store.
- **Collection control (Story 25.4).**
  - `CollectionPlan(venue)` owns `instruments` (with per-instrument delta-storage and retention entries), `exclude`, pins and `cap` (30 for dYdX).
    - Its invariants are `exclude ∩ collected = ∅` and `|collected| ≤ cap`, and a pin is admitted only by `classify_liquidity` on USD volume.
    - The commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` return plan diffs.
  - `Collector.apply(plan_diff)` returns `Applied(subscribed, unsubscribed, failed)`. It is one new method on the legacy capture class.
    - The sampler iterates `applied ∩ plan`.
    - A `failed` instrument shows as `pending` on `collector:status`, is ledgered once per attempt and is retried by capture.
    - A book or `LiveBook` exists only for a subscribed instrument and is cleared on unsubscribe.
    - An unsolicited message is counted, not booked.
  - `ControlService` consumes `collector:control`. `StatusPublisher` publishes `collector:status` from the plan plus capture's read-only counters.
  - `InstrumentRemoved` sets the retention attributes that the nightly `RetentionPolicy` reads; control deletes nothing.
  - `collector_core/config.py` is the one `config.toml` loader and returns `(CoreConfig, CollectionPlan)`. Control validates through it before `CollectionPlanStore.save`.
  - Known limit: saves are full rewrites, so comments in the file are lost.
  - Known limit: only dYdX has a live plan. Bybit and Hyperliquid have static tuples, applied once at start through the same `apply`.

## Cross-Story Dependencies

- **Fixed order: 25.1 → 25.1a → 25.2 → 25.3 → 25.4.** 25.1, 25.1a and 25.2 are done, and Epic 24 is done, so `kernel/`, `observability/`, `candles/`, `views/`, `research/`, `archive/` and `ranking/` already exist.
- **Shim expiry chain.**
  - 25.3 must delete the archive shims left in `collector_core/` and `dydx_collector/normalize_snapshot_schema.py`, all marked `REMOVE_AFTER = "25-3-..."`.
  - 25.4 must delete the `ranking_engine` shims, marked `REMOVE_AFTER = "25-4-..."`.
  - 25.3 leaves a `live_paper` shim package with `REMOVE_AFTER = "26-1-..."`. 25.4 leaves the moved `dydx_collector` modules as shims with `REMOVE_AFTER = "26-2-..."`.
- **25.1 → 25.4.** `DydxCollector._prune_loop` is already gone. 25.4 must not reintroduce a pruner, and the plan must keep exposing the retention attributes that `RetentionPolicy` reads.
- **25.4 → Epic 26.** Epic 26's capture move later takes over `Collector.apply` as `CaptureService.apply`.
- **Epic 29 builds on this epic.** 29.2 (per-venue Collector pane) and 29.4 (runtime control for Bybit/Hyperliquid) need 25.4's per-venue `CollectionPlan` and applied-set report.
