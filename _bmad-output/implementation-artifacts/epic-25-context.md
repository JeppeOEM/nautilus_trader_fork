# Epic 25 Context: Archive, ranking, bots and collection control as aggregates

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the third epic of the DDD migration of `platform/`. It moves four contexts (`archive/`, `ranking/`, `bots/`, `collection_control/`) out of their legacy packages into bounded contexts, each with an aggregate that enforces a real invariant. After it lands, the nightly saga cannot zero rows over an archive gap or reconcile a day it never rebuilt. The ranking engine has no module globals. The paper/real split for bots is enforced by types. A venue's collected set is what capture actually applied, not what control intended. Rankings live on the web only, and the TUI controls only bots and the collector. The epic ends by replacing the host crontab with an `archive` service of our own, so nightly maintenance survives reboots and redeploys and the operator can see and trigger it. Every move keeps the live wire, file and store contracts byte-identical, so each story can be deployed on its own to the 24/7 writers.

## Stories

- Story 25.1: `archive/` context: `ArchiveDay`, one deleter, one rewriter, one writer per leaf
- Story 25.1a: Rankings web-only: the ranking-mode toggle moves to the web and the TUI's Coins pane is deleted
- Story 25.1b: `archive` service: the nightly maintenance scheduled in our own code, no host cron
- Story 25.2: `ranking/` context: `RankingBoard` replaces the module globals
- Story 25.3: `bots/` context: paper and non-paper as types, Nautilus behind an ACL
- Story 25.4: `collection_control/` context: the plan is the intent, the applied set is the fact

## Requirements & Constraints

- **The published language is frozen.** None of these change:
  - Parquet schemas and catalog directory names.
  - The Redis payloads: `snapshots:raw`, `rankings:live`, `ranking:control`, `bots:*`, `collector:status`, `collector:control`.
  - The SQLite schemas: `candles_<venue>.db`, `metrics.db`, `fills.db`.
  - The key sets of the TOML files.
  - Compose service names, env vars and the `platform/data/` bind mounts.

  A new channel, such as `archive:status`/`archive:control`, is additive, and `docs/DATA_DICTIONARY.md` §3 must record it. Any rewired publisher needs a replay test against recorded messages.
- **Shims.** An old import path becomes a pure re-export: `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. A shim defines nothing. `test_namespace.py` fails once the named story is `done`, so that story must delete the shim.
- **Same-commit housekeeping.** A story updates all of these in the same commit:
  - the `platform/CLAUDE.md` citations;
  - `ARCHITECTURE.md` and `docs/DATA_DICTIONARY.md`;
  - the dockerfile `COPY` sets (`test_images.py` walks the imports of every compose `command:` and Makefile `-m` entrypoint);
  - compose `command:` lines;
  - both Makefile test lists (`test`, `test-live-paper`).
- **Failures are ledgered.** Every tolerated failure goes through `observability.error_ledger.record`, one site per event type (DATA-07). A service sets `ERROR_LEDGER_SERVICE` and uses the shared errors mount.
- **Invariants are tested.** Each aggregate command gets one invariant test. Each port gets a contract test that its adapters run.
- **No new dependencies.** Scheduling is plain asyncio, with no APScheduler. The scheduler is Python, not Go, and does not live inside `data_api`: that service mounts the catalog `:ro`, restarts on every frontend redeploy, and needs its memory for serving.
- **Memory.** Each maintenance step runs as a separate subprocess, so its RSS returns to the OS (MEM-01).
- **Binding rules.** The DATA/OBS/MEM/NAUT/SSOT/TEST/DESIGN rules in `platform/CLAUDE.md` apply. A deliberate simplification is written as a `Known limit:` comment that names the ceiling and the upgrade path.

## Technical Decisions

- **Layering in every context.**
  - `domain/` is pure: stdlib, `kernel/` and Nautilus model types only, with no I/O or asyncio.
  - `application/` declares ports as `typing.Protocol` and holds the services and loops.
  - `infrastructure/` implements the ports and is imported only by the composition root (`__main__` or a named entrypoint).
  - There is no DI container, no event bus, no module-level mutable runtime state (a boundary test enforces this) and no `platform.` import prefix.
- **Kernel reuse.** Venue REST goes through `kernel.venue_http`, catalog reads through `kernel.catalog_files` and zstd writes through `kernel.parquet_compat`.
- **Archive invariants (landed in 25.1; 25.1b must run through them, never around them).**
  - The only persisted day status is `verified_days`, reached through the `VerifiedDays` port.
  - `reconcile_day` runs only in a saga run whose `rebuild_day` succeeded for the same (venue, day). Otherwise it refuses with `reconcile.not_rebuilt`.
  - `RetentionPolicy` is the only code that deletes catalog files, and `CatalogFiles.rewrite` is the only in-place rewriter.
  - There is one writer process per catalog leaf. Capture holds `<catalog>/.capture-<venue>.lock` for its whole run. Archive tools write closed days only, under the maintenance lock `.consolidate.lock`. No archive tool writes a file whose `ts_init` span reaches the current UTC day.
  - Within one venue, the saga stops at its first failed step and ledgers it.
- **Scheduler (25.1b).**
  - The composition root is `archive/scheduler.py`, run as `python -m archive.scheduler`.
  - It runs the existing sequence through `archive.application.nightly.run_steps`: `nightly` per venue, then `consolidate`, then `backup-catalog`. Across venues it uses `;` semantics, so one venue's failure never skips the next venue or the backup.
  - Config lives in `platform/archive/config.toml`: `nightly_at` (UTC), `venues`, the backup target, `catch_up_max_days` (7), `lock_wait_minutes` (60) and `intraday_consolidate_hours` (4). Its loader rejects unknown keys.
  - `next_run(now, schedule, last_success)` is a pure function in `archive/domain/`. It is tested across midnight and against a clock that jumps.
  - Per-venue state lives in `platform/data/archive/state.json`, written atomically. A day counts as successful only if `run_steps` had no FAILED step for that venue.
  - On start, the scheduler catches up the missed closed days, oldest first. Past the cap it ledgers `archive.catch_up_capped` once.
  - On lock contention it waits, bounded by `lock_wait_minutes`, and ledgers `archive.lock_wait` or `archive.lock_timeout`. It never runs two of its own jobs at once.
  - Intraday consolidation merges only the *closed hours* of the small types: mark/index, funding, open interest and instrument status. Snapshots, trades and deltas stay nightly-only. A merged hour never includes a file that reaches the current hour.
  - The compose `archive` service uses the collector image, mounts the catalog and candles `rw`, sets `restart: always` and the logging anchor, and is started by `make up`. The `make nightly`/`consolidate`/`backup-catalog` targets remain as manual tools.
- **Ranking (landed).** `RankingBoard` owns the mode, which is global and last-write-wins. The pct/volatility math lives only in `ranking/domain`.
- **Bots (landed).**
  - `PaperFleet` and `ExecBot` are distinct types with distinct loaders, and no config key or control message can promote a bot to real money.
  - Only `bots/infrastructure/nautilus_host.py` imports `TradingNode`.
- **Collection control (landed).**
  - `CollectionPlan` is the intent and `Collector.apply` returns `Applied(subscribed, unsubscribed, failed)`.
  - Control deletes nothing. It sets the retention attributes that `RetentionPolicy` reads.
  - `collector_core/config.py` is the one `config.toml` loader.

## UX & Interaction Patterns

- **Archive status.** The scheduler publishes `archive:status` after every step: `next_run`, `running`, and `last_run` with its run id, day, times, and per-step venue, name, exit code and duration.
- **Archive control.** The scheduler listens on `archive:control` for `{"command": "run_now", "day": "YYYY-MM-DD" | null}`, where `null` means yesterday.
- **Web.** `data_api` adds `GET /api/archive/status` and `POST /api/archive/run`. The POST only publishes the control message, because `data_api` never writes the catalog. The web UI shows the status and a "Run now" button behind a confirm.
- **TUI.** `bot_tui`'s Collector pane shows the same status line. The TUI has two panes, Bots and then Collector, and never subscribes to `rankings:live` or `snapshots:raw`. The ranking-mode switch is on the web only.

## Cross-Story Dependencies

- **Order and status.** The order is 25.1 → 25.1a → 25.2 → 25.3 → 25.4 → 25.1b. All stories except 25.1b are done, as is Epic 24, so `kernel/`, `observability/`, `candles/`, `archive/`, `ranking/`, `bots/` and `collection_control/` all exist.
- **25.1 → 25.1b.** 25.1b reuses 25.1's `nightly` saga, `.consolidate.lock`, the capture lock and `consolidate_day`. The per-hour merge rule and the open-hour exclusion are added to `archive/application/consolidate_day.py`.
- **Deploy docs.**
  - `docs/DEPLOY_CHECKLIST.md` §1 becomes "remove the old cron line", with the command that confirms it is gone.
  - The DDD spine's `archive` row and its "nightly cron" mention are struck with an amendment.
  - `platform/CLAUDE.md` DATA-05/06 name the service as the one place where maintenance is scheduled.
- **Later epics.**
  - `live_paper` shims expire at 26-1 and the `dydx_collector` control shims at 26-2.
  - Epic 26 turns `Collector.apply` into `CaptureService.apply`.
  - Epic 29 (the per-venue Collector pane and runtime control for Bybit/Hyperliquid) builds on 25.4's `CollectionPlan`.
