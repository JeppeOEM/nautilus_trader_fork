# Epic 26 Context: The gate as an aggregate

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the last epic of the DDD migration of `platform/`, and capture moves last. The write gate is turned into explicit aggregates: per-instrument `LiveBook` and `TradeIntake`, a per-venue `FeedGroup`, and a pure `SecondSampler`. Venue variance comes in as pure policy values, not `Collector` hook overrides, so no venue can override the gate and every counter has a name. The hot-path replay test proves the ingest path is no slower. Capture then moves to `platform/capture/` with `capture/venues/<v>/` packages and new entrypoints, which makes "Adding a venue" a recipe over named files. The closeout deletes every remaining shim and makes both architecture spines describe the code that runs. The epic also carries one unrelated archive fix (26.1b): the off-site backup becomes an explicit setting.

## Stories

- Story 26.1: `LiveBook`, `TradeIntake`, `FeedGroup` and a pure `SecondSampler`, in place
- Story 26.1b: The off-site catalog backup is an explicit setting, off until a storage target exists
- Story 26.2: `capture/` package and `capture/venues/<v>/` with new entrypoints
- Story 26.3: Closeout: last shims gone, spines reconciled, guardrails permanent

## Requirements & Constraints

- **Published language stays unchanged during the migration.** That covers Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `collector:status`, `collector:control`, ...), SQLite/TOML schemas and key sets, compose service names, env vars, `platform/data/` bind mounts and the dYdX config bind mount. Each story must be deployable on its own to the 24/7 writers.
- **Hot-path gate.** The `test_hotpath.py` replay must show allocations per message ≤ baseline and wall time per message ≤ 2× baseline. Numbers are recorded in `docs/DATA_INTEGRITY_AUDIT.md`. Rules that keep it there:
  - `on_data(data, feed)` stays an O(1) enqueue.
  - `LiveBook` holds the Nautilus `OrderBook` by reference, with no per-delta copy and no per-delta event.
  - `TradeIntake` mutates in place.
  - Domain events are emitted only per sampled second and per state transition.
  - Measure with stdlib tooling only (`tracemalloc`, `perf_counter_ns`).
- **Behaviour tests pass unchanged.** This covers DATA-04/DATA-08 (dYdX uncross ladder, Bybit `u` canary including the zero-level message, Hyperliquid full snapshot), the 22.12/22.13/22.14 tests and the trade-history fixture tests.
- **Shims (MR2).** A shim is a pure re-export: `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. It defines nothing, because a copied class breaks Arrow registration and `is` dispatch. `test_namespace.py` fails once the named story is `done`, so a story must delete the shims keyed to it.
- **Same-commit housekeeping (MR4).** Each move updates all of these in one commit:
  - `platform/CLAUDE.md` citations
  - `ARCHITECTURE.md`
  - `docs/DATA_DICTIONARY.md`
  - the dockerfile `COPY` sets (`test_images.py` walks every compose `command:` and Makefile `-m` entrypoint)
  - compose `command:` lines
  - both Makefile test lists
- **Parent-spine Deferred items (MR14).** A story that resolves a parent-spine Deferred item strikes it with an amendment.
- **Failures are ledgered, never muted (DATA-07).** Each tolerated failure calls `observability.error_ledger.record` at one site per event type. The empty-top-of-book rejection has a rate-limited warning and the site `collector.empty_top`.
- **Operator steps are deferred, never parked (OPS-01).** VPS steps go into the "Deferred operator actions" section of `docs/DEPLOY_CHECKLIST.md`. The story still finalizes `done`.
- **Backup setting (26.1b).** `archive/config.toml` gains a required `backup_enabled` key, and the committed value is `false`.
  - When `false`: the chains contain no `backup_catalog` step, the service logs one startup WARNING, and `archive:status` reports `"backup": "disabled"`.
  - When `true` but no rclone remote is configured, the service refuses to start.
  - A manual `make backup-catalog` with no remote still exits 1.
  - Audit D-33 stays open, now as a visible choice.

## Technical Decisions

- **Layering.** `domain/` imports only the stdlib, `kernel/` and Nautilus model/core types, with no I/O and no asyncio.
  - A venue `policies.py` counts as domain code wherever it lives.
  - `application/` holds the `Protocol` ports, the services and the loops.
  - `infrastructure/` is imported only by the composition root.
  - Nothing uses a `platform.` import prefix. `capture` depends only on `kernel` and `observability`.
- **Aggregates.**
  - **`LiveBook`** owns: the book, last-delta ns, crossed-since, resync-pending, level tags, last `u`, and the venue-time pending deltas (bounded; overflow drops, ledgers and resyncs).
    - `snapshot_top` rejects a book that is missing, has an empty top, is crossed or is stale.
    - `resync()` is the fallback, never the first response.
  - **`TradeIntake`** owns: the bounded id window and the stale filter.
    - Per-feed arbitration: a repeat on the same feed counts as `duplicate`; a repeat from another feed or from REST counts as `duplicate_feed`.
    - Late and ahead trades are archived and counted, but kept out of the live second.
  - **`FeedGroup`** owns: `Feed` values, `FeedLiveness`, reconnect detection, the one-sided outage check and `BackfillRequest` scheduling.
    - `trades_only` feeds never count toward book staleness.
    - REST rows never stamp WS liveness.
  - **`SecondSampler`** is pure and is the only place the gate checks live. Under venue time it closes second `S` at `S + 1 + hold_back_seconds`.
  - **`FlushBatch`** carries the newest trade group younger than 5 s, together with that instrument's later snapshot rows.
- **Policies are pure and synchronous.** They never log, ledger or await.
  - `CrossedBookPolicy.step(...) -> Uncrossed | StillCrossed | ResyncRequested`
  - `LevelTagger`, `SequenceCanary`, `BookTimeSource` and `BackfillCapability`
- **Application service.** `Collector` (renamed `CaptureService` after the move) owns the loops and executes `ResyncRequested`. The local book is cleared before `VenueFeed.resync_orderbook`, not after. The service is the only ledger caller, and every ledger site is listed in `sites.py`.
- **Ports.** `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier` and `SecondSink`.
- **Swap points for Epic 28.** Only `LiveBook` names the concrete order-book class, and only `ArchiveWriter` names the batch encoder.
- **Where 26.1 landed (in place).** 26.1 is done.
  - `collector_core/domain/` holds the aggregates.
  - `collector_core/{ports,sites}.py` and `collector_core/infrastructure/{parquet_writer,redis_stream}.py` hold the ports, the site list and the adapters.
  - The venue policies are `dydx_collector/policies.py` and `bybit_collector/policies.py`.
  - Each venue has `<venue>_collector/trade_history.py`.
  - The backfill admission is `collector_core/application/trade_backfill.py`'s `admit_backfill`.
- **Target tree (26.2).**
  - `platform/capture/{domain,application,infrastructure,tests}/`
  - `capture/venues/{dydx,bybit,hyperliquid}/`, each with `client.py`, `trade_history.py`, `policies.py`, an optional `open_interest.py`/`book_snapshot.py`, `config.py` and `__main__.py`.
  - Each `__main__.py` is the composition root: it builds the client, the policies, the adapters and, for dYdX, the collection-control loops, then calls `run_forever`.
  - Compose runs `python -m capture.venues.<venue>`.
  - After the move, no `Collector` subclass exists.

## Cross-Story Dependencies

- **Order.** 26.1 → 26.1b → 26.2 → 26.3. 26.1b touches `archive/` only.
- **Before 26.2.** Epics 23–25 are done, so `kernel/`, `observability/`, `candles/`, `archive/`, `ranking/`, `bots/` and `collection_control/` already exist.
- **26.2.**
  - Must delete the shims keyed to it: `dydx_collector/config.py`, `bybit_collector/config.py` and `dydx_collector/open_interest.py`.
  - Turns the old packages into shims keyed `26-3-...`.
  - Adds `capture` to the collector image's `COPY` set, and adds the `capture` tests to the Makefile lists.
  - `test_boundaries.py` must pass with the full dependency graph.
  - Rewrites the "Adding a venue" recipe in `platform/CLAUDE.md`.
  - Decides whether to rename the `collector` compose service and records the decision.
- **26.3.**
  - No `REMOVE_AFTER` module remains, and a grep for the old package names is empty.
  - Every spine `[TARGET]` becomes `[ADOPTED]` with `path:line` citations, and `lint_spine.py` passes clean.
  - The legacy boundary map is deleted.
  - The hot-path baseline is re-recorded.
  - `sprint-status.yaml` marks Epics 23–26 done.
- **Epic 28** depends on 26.1's swap points.
