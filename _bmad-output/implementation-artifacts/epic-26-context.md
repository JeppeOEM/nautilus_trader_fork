# Epic 26 Context: The gate as an aggregate

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Finish the DDD migration by moving capture last. The collector's single fail-closed write gate becomes one pure domain service (`SecondSampler`) over explicit aggregates (`LiveBook`, `TradeIntake`, `FeedGroup`). Venue differences come in as pure policy values rather than `Collector` subclass hook overrides, so no venue can override the gate and every counter has a name. The work runs in three steps: the domain shape is built in place, behind the hot-path budget. Then it moves into `platform/capture/` with `capture/venues/<v>/` packages and new entrypoints. Last, a closeout deletes every remaining shim and rewrites both architecture spines so they describe the code that actually runs. A small archive-only story (26.1b) rides in the same queue to make the off-site backup an explicit, visible setting.

## Stories

- Story 26.1: `LiveBook`, `TradeIntake`, `FeedGroup` and a pure `SecondSampler`, in place
- Story 26.1b: The off-site catalog backup is an explicit setting, off until a storage target exists
- Story 26.2: `capture/` package and `capture/venues/<v>/` with new entrypoints
- Story 26.3: Closeout: last shims gone, spines reconciled, guardrails permanent

## Requirements & Constraints

- **Hot-path budget.** The refactored ingest path must be measured against the recorded baseline (`platform/tests/fixtures/hotpath_baseline.json`) using stdlib `tracemalloc`/`perf_counter_ns`, never pytest-benchmark. Allocations per message must not exceed the baseline. Wall time per message must stay within 2x the baseline. Results are recorded in `docs/DATA_INTEGRITY_AUDIT.md`, and a story that misses the budget does not merge. `on_data` stays an O(1) enqueue. There is no per-delta or per-trade wrapper, event or verdict object. Domain events exist only per sampled second and per state transition.
- **Deployable alone, published language frozen.** Every story ships on its own. These are all frozen: Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `collector:status`, `collector:control`, ...), SQLite and TOML store schemas, compose service names, env vars and the `platform/data/` bind mounts. The class names `DydxSecondSnapshot` and `OpenInterest` are persistence identifiers and do not change.
- **Shims.** When a module moves, its old import path keeps a pure re-export shim: `from <new> import <names>` plus a `DeprecationWarning` plus `REMOVE_AFTER = "<story key>"`, and nothing else is defined there. A shim is deleted no later than two stories after it appears. `test_namespace.py` enforces `old.X is new.X` and exactly one Arrow registration per kernel class.
- **Same-commit doc and packaging updates.** Every move updates all of these in the same commit: `platform/CLAUDE.md` citations, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, and both Makefile test lists (`test`, `test-live-paper`).
- **Parent-spine Deferred items.** When a story resolves one, it strikes it in the parent spine with an amendment. Items still open for this epic: the empty-top skip (struck by 26.1), `capture_hl_ws.py`'s venue branch, and the resolved writer-to-reader and namespace items.
- **Data integrity.** Fail closed: a rejected sample is dropped, logged and ledgered, and no validity flag is ever added. The empty-top-of-book rejection gets a rate-limited WARNING and the ledger site `collector.empty_top`. Nothing is silently dropped or muted (DATA-07). Root cause comes before mitigation (DATA-02). The DATA-04/DATA-08 tests must keep passing against the policies: the dYdX uncross ladder, the Bybit `u` canary (including the zero-level message case) and the Hyperliquid full-snapshot tests. The 22.12, 22.13 and 22.14 tests must pass unchanged.
- **Deferred operator actions.** VPS steps never park a story. They go to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with the story key and commit. The story still finalizes `done` through normal review, with no `awaiting-operator` status and no `operator_actions`.
- **Backup setting (26.1b).** The archive `config.toml` gets a required `backup_enabled` key, committed as `false` because there is no off-site storage yet. When it is off:
  - no `backup_catalog` step runs;
  - the service logs one WARNING at start;
  - `archive:status` shows `"backup": "disabled"`.

  When it is on, a missing `RCLONE_REMOTE`/`RCLONE_BUCKET` refuses start with exit 1. A manual `make backup-catalog` with no remote still exits 1, unchanged. Audit D-33 stays open, now as an explicit, visible choice.

## Technical Decisions

- **Hexagonal layering inside the context.**
  - `domain/` imports only the stdlib, `kernel/` and `nautilus_trader.model`/`nautilus_trader.core`. It never touches I/O, asyncio, Redis, SQLite or Parquet.
  - `application/` holds the `typing.Protocol` ports, the services and the asyncio loops, and may import domain, kernel and observability.
  - `infrastructure/` holds the adapters and is imported only by the composition root (`__main__.py`).
  - `capture` depends only on `kernel` and `observability`. Candles enter only through the `SecondSink` port, which is injected at the venue entrypoint.
  - There is no `platform.` import prefix anywhere. There is no DI container or event bus, and all wiring is explicit.
- **Aggregates.**
  - `LiveBook` wraps the Nautilus `OrderBook` by reference, with no copy. It holds last-delta ns, crossed-since, resync-pending, level tags and last `u`. Under venue time it also holds `ts_event`-ordered pending deltas, bounded by `hold_back + _VENUE_AHEAD_NS`; on overflow it drops, ledgers `collector.pending_deltas` and resyncs. `snapshot_top` returns `Rejected` when the book is missing, empty-topped, crossed or stale. Resync is the fallback, never the first response.
  - `TradeIntake` holds the bounded id window and the stale-history filter. It arbitrates per feed: `duplicate` means a same-feed replay, and `duplicate_feed` means the trade arrived via another feed or REST. It also keeps the late/ahead counters. Every counter is reported at flush.
  - `FeedGroup` holds the `Feed` values and per-feed `FeedLiveness`, detects reconnects, compares one-sided outages, and schedules and abandons `BackfillRequest`s. `trades_only` feeds and REST rows never stamp WS liveness.
- **`SecondSampler`** is pure. It returns accepted snapshots, `SampleRejected`s and requested actions. Under venue time it closes second `S` at `S + 1 + hold_back_seconds`.
- **`FlushBatch`** carries the newest `ts_init` trade group, and that instrument's later snapshot rows with it, while the group is younger than `_TRADE_CARRY_NS` (5 s). `SecondSink.apply` receives the flushed batch, never the sampled one.
- **Policies.** These are pure and synchronous: no logging, ledgering or `await`, and `test_boundaries.py` treats policy files as domain code.
  - `CrossedBookPolicy.step(book, tags, now_ns) -> Uncrossed | StillCrossed(since) | ResyncRequested` (dYdX: the uncross ladder; Bybit/HL: core default)
  - `LevelTagger` (dYdX)
  - `SequenceCanary` (Bybit `u`)
  - `BookTimeSource` (`arrival` or `venue`)
  - `BackfillCapability`

  Venue `Collector` subclasses override nothing except `__init__`. After 26.2, no `Collector` subclass exists at all.
- **Application service.** `CaptureService` (today `Collector`) owns the loops and executes `ResyncRequested`. It drops the local book before awaiting `VenueFeed.resync_orderbook`, a deliberate reading of the rule. It is the only ledger caller in capture, and every ledger site is listed in `sites.py`.
- **Ports.** `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier` and `SecondSink` are declared in `ports.py`.
  - `ArchiveWriter` (parquet adapter) owns the Parquet write, quarantine, instrument-definition writes and the capture lock.
  - `LiveStream` (redis adapter) owns the Redis publish.
  - `LiveBook` is the only module that names the concrete order-book class, and `ArchiveWriter` is the only one that names the batch encoder. Epic 28 can then swap either one without touching any caller.
- **Venue package shape (26.2).** Each `capture/venues/{dydx,bybit,hyperliquid}/` package contains `client.py` (the `VenueFeed` ACL), `trade_history.py`, `policies.py`, optional `open_interest.py`/`book_snapshot.py`, `config.py` and `__main__.py`. `__main__.py` is the composition root: it builds the client, policies and adapters, adds the dYdX collection-control loops, and calls `run_forever`. Compose commands become `python -m capture.venues.<venue>`. Renaming compose service `collector` to `dydx_collector` is decided in the story and recorded there; it is allowed only if the operator checklist records the container-name change.
- **Target tree** after 26.2: `capture/domain/{live_book,trade_intake,feed_group,sampler,verdicts,events,flush_batch}.py`, `capture/application/{ports,sites,capture_service,trade_backfill}.py`, and `capture/infrastructure/{parquet_writer,redis_stream,config}.py`.
- **Guardrails.** `test_boundaries.py` walks AST imports against the context graph, with a legacy map that 26.3 deletes. `test_images.py` checks that each image's `COPY` set covers its entrypoint's import closure. `test_hotpath.py` and `test_namespace.py` run in `make test`. The spine is linted with `lint_spine.py` plus the version lens.
- **Spine reconciliation (26.3).** Each `[TARGET]` marker is either re-verified and rewritten as `[ADOPTED]` with `path:line` citations, or kept with a reason and a Deferred entry. The `Today` columns are replaced by target paths, and `troll/...` citations are re-pointed to `platform/...`.

## Cross-Story Dependencies

- 26.1 → 26.2 → 26.3 is a fixed order. 26.1 builds the seams in place (`collector_core/domain/`, `ports.py`, `sites.py`, `infrastructure/`, per-venue `policies.py`/`trade_history.py`). 26.2 moves them into `capture/`. After 26.2, `collector_core` and the three venue packages become pure shims with `REMOVE_AFTER = "26-3-..."`.
- 26.1b touches `archive/` only and is independent of the capture stories.
- 26.3 removes every shim created in Stories 23.1–26.2. Afterwards, `git grep` over `platform/` for `ml_signals|collector_core|dydx_collector|bybit_collector|hyperliquid_collector|ranking_engine|live_paper|common\.venues` must return nothing, excluding `docs/` history notes and `.planning/`. 26.3 also re-records the hot-path baseline from the final tree and marks Epics 23–26 `done` in `sprint-status.yaml`.
- Upstream: this epic depends on Epics 23–25 (kernel, observability, candles `SecondSink`, archive, and collection_control's `CollectionPlan`/`CaptureService.apply`). Downstream: Epic 28 runs after 26.2 and relies on the `LiveBook` and `ArchiveWriter` seams.
