# Epic 26 Context: The gate as an aggregate

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the last epic of the DDD migration of `platform/`, and capture moves in it. The write gate becomes a set of explicit aggregates: per-instrument `LiveBook` and `TradeIntake`, a per-venue `FeedGroup`, and a pure `SecondSampler`. Venue variance comes in as pure policy values, not as `Collector` subclass hook overrides, so no venue can override the gate and every counter has a name. The hot-path replay test gates all of this so the ingest path is provably no slower. Capture then moves to `platform/capture/` with `capture/venues/<v>/` packages and new entrypoints, which turns "Adding a venue" into a recipe over named files. The closeout deletes every remaining shim and makes both architecture spines describe the code that actually runs.

## Stories

- Story 26.1: `LiveBook`, `TradeIntake`, `FeedGroup` and a pure `SecondSampler`, in place
- Story 26.2: `capture/` package and `capture/venues/<v>/` with new entrypoints
- Story 26.3: Closeout: last shims gone, spines reconciled, guardrails permanent

## Requirements & Constraints

- **Published language is frozen.** Parquet schemas, catalog directory names, every Redis payload (`snapshots:raw`, `collector:status`, `collector:control`, ...), SQLite/TOML schemas and key sets, compose service names, env vars, `platform/data/` bind mounts and the dYdX config bind mount must not change. Each story is deployable on its own to the 24/7 writers.
- **Hot-path gate (26.1 does not merge otherwise).** Run the `test_hotpath.py` replay against the refactored ingest path. It must show allocations per message ≤ the 23.1 baseline and wall time per message ≤ 2× baseline. Record the numbers next to the baseline in `docs/DATA_INTEGRITY_AUDIT.md`. `on_data(data, feed)` stays an O(1) enqueue. `LiveBook` holds the Nautilus `OrderBook` by reference and adds no per-delta copy or event. `TradeIntake` mutates in place. Domain events exist only per sampled second and per state transition.
- **Existing tests must still pass unchanged.** This covers the DATA-04/DATA-08 behaviours, now run against the policies: the dYdX uncross ladder, the Bybit `u` canary including a message with zero levels, and the Hyperliquid full snapshot. It also covers the 22.13/22.14/22.12 tests and the trade-history fixture tests.
- **Shims.** An old import path becomes a pure re-export: `from <new> import <names>`, a `DeprecationWarning` and `REMOVE_AFTER = "<story key>"`. A shim defines nothing, because a copied class body breaks Arrow registration and `is` dispatch. `test_namespace.py` fails once the named story is `done`. Shims that expire in this epic: `live_paper/*` and `dydx_collector/open_interest.py` at 26-1, and `dydx_collector/config.py` and `bybit_collector/config.py` at 26-2. Each story must delete the shims that expire at its own key.
- **Same-commit housekeeping.** Each story updates all of these in the same commit:
  - `platform/CLAUDE.md` citations;
  - `ARCHITECTURE.md`;
  - `docs/DATA_DICTIONARY.md`;
  - the dockerfile `COPY` sets (`test_images.py` walks every compose `command:` and Makefile `-m` entrypoint);
  - compose `command:` lines;
  - both Makefile test lists (`test`, `test-live-paper`).
- **Parent-spine Deferred items.** A story that resolves a parent-spine Deferred item strikes it with an amendment in the same story. Examples: the silent empty-top-of-book skip (26.1), and the `capture_hl_ws.py` and `open_interest`/`volume24h` items.
- **Failures are ledgered.** Every tolerated failure goes through `observability.error_ledger.record`, one site per event type. The empty-top-of-book rejection gains a rate-limited warning and the site `collector.empty_top`.
- **No new dependencies.** Use stdlib tooling (`tracemalloc`, `perf_counter_ns`), not `pytest-benchmark`. A deliberate simplification gets a `Known limit:` comment that names the ceiling and the upgrade path.

## Technical Decisions

- **Layering.**
  - `domain/` imports only the stdlib, `kernel/` and Nautilus model/core types, with no I/O or asyncio.
  - A venue `policies.py` counts as domain code wherever it lives, and `test_boundaries.py` treats it that way.
  - `application/` holds the ports (`typing.Protocol`), the services and the loops.
  - `infrastructure/` is imported only by the composition root.
  - There is no `platform.` import prefix. `capture` depends only on `kernel` and `observability`.
- **Aggregates (`collector_core/domain/` in 26.1, `capture/domain/` in 26.2).**
  - **`LiveBook`, one per applied instrument.** It owns the book, the last-delta ns, crossed-since, resync-pending, level message-id tags and last `u`. Under `book_time_source = "venue"` it also holds the `ts_event`-ordered pending deltas, bounded to `hold_back + _VENUE_AHEAD_NS`. On overflow it drops, ledgers `collector.pending_deltas` and resyncs.
    - `snapshot_top(depth, now, policies) -> SampleVerdict` rejects a book that is missing, has an empty top, is crossed or is stale.
    - A delta applies at most once, and only after a snapshot baseline.
    - `resync()` is the fallback, never the first response (DATA-03).
  - **`TradeIntake`, one per instrument.** It owns the bounded id window and the stale-history filter.
    - Per-feed first-copy arbitration: a repeat on the same feed counts as `duplicate`; a repeat from another feed or from REST counts as `duplicate_feed`.
    - Late and ahead trades are archived and counted, and excluded from the live second, never dropped.
    - Every counter is reported at flush.
  - **`FeedGroup`, one per venue.** It owns the `Feed` values (`name`, `group`, `trades_only`), `FeedLiveness`, reconnect detection, the one-sided outage comparison, and `BackfillRequest` scheduling and abandonment.
    - `trades_only` feeds never count toward book staleness.
    - REST rows never stamp WS liveness.
  - **`SecondSampler.sample(...)` is pure.** It returns accepted snapshots, `SampleRejected`s and requested actions, and it is the only place the four gate checks live. Under `venue` time it closes second `S` at `S + 1 + hold_back_seconds`.
  - **`FlushBatch`** carries the newest `ts_init` trade group while it is younger than `_TRADE_CARRY_NS` (5 s), along with that instrument's later snapshot rows.
  - `verdicts.py` and `events.py` hold the verdict and event types.
- **Policies are pure and synchronous.** They never log, ledger or await.
  - `CrossedBookPolicy.step(book, tags, now_ns) -> Uncrossed | StillCrossed(since) | ResyncRequested`. dYdX uses the uncross ladder; Bybit and Hyperliquid use the core default.
  - The other policies are `LevelTagger` (dYdX), `SequenceCanary` (Bybit `u`), `BookTimeSource` (`arrival` | `venue`) and `BackfillCapability` (dYdX paged; Bybit last 1000 linear / 60 spot; Hyperliquid last 10).
  - After 26.1, the venue `Collector` subclasses override nothing but `__init__`. After 26.2, no `Collector` subclass exists at all.
- **Application service.** `Collector`, later `CaptureService`, owns the loops. It executes `ResyncRequested` after the sample through `VenueFeed.resync_orderbook` and then `LiveBook.resync()`. It is the only ledger caller in capture, and every site is listed in one `sites.py`. `apply(plan_diff)` returns `Applied(subscribed, unsubscribed, failed)`. The sampler iterates `applied ∩ plan`, and a `LiveBook` is created on `subscribed` and disposed on `unsubscribed`.
- **Ports (`ports.py`).** `VenueFeed` (the former client-contract docstring), `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier` and the existing `SecondSink`.
  - The fetch/parse half of `trade_backfill.py` moves to the per-venue `trade_history.py` modules. The scheduling half stays in `application`.
  - `ArchiveWriter` owns the Parquet write, quarantine, instrument-definition writes and the capture lock. `LiveStream` owns the Redis publish.
- **Swap points.** Only `LiveBook` names the concrete order-book class, and only `ArchiveWriter` names the batch encoder (`make_dict_serializer`). This way Epic 28 can swap in `nautilus_pyo3.OrderBook` or a columnar encoder without touching any caller.
- **Target tree (26.2).**
  - `platform/capture/{domain,application,infrastructure,tests}/`, with `infrastructure/` holding `parquet_writer.py`, `redis_stream.py` and `config.py` (the one `config.toml` loader).
  - `capture/venues/{dydx,bybit,hyperliquid}/`, each with `client.py`, `trade_history.py`, `policies.py`, an optional `open_interest.py`/`book_snapshot.py`, `config.py` and `__main__.py`.
  - Each `__main__.py` is the composition root. It builds the client, the policies and the adapters (`ArchiveWriter`, `LiveStream`, candles `SecondSink`, `Notifier`), plus the collection-control loops for dYdX, and then calls `run_forever`.
  - Compose runs `python -m capture.venues.<venue>`.

## Cross-Story Dependencies

- **Order.** The order is fixed: 26.1 → 26.2 → 26.3.
  - Epics 23–25 are done, apart from 25.1b, which is awaiting operator. So `kernel/`, `observability/`, `candles/`, `archive/`, `ranking/`, `bots/` and `collection_control/` already exist.
  - 26.1 refactors in place. 26.2 moves the result.
- **26.2.**
  - The old packages (`collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector`) become shims with `REMOVE_AFTER = "26-3-..."`.
  - The collector image `COPY`s `capture`, and the Makefile test lists gain `capture/tests` and `capture/venues/*/tests`.
  - `test_boundaries.py` passes with the full dependency graph active.
  - The `platform/CLAUDE.md` "Adding a venue" recipe is rewritten over the new files.
  - The story decides and records whether the compose `collector` service is renamed.
  - VPS redeploy steps go into the "Deferred operator actions" section of `docs/DEPLOY_CHECKLIST.md`. The story never parks as `awaiting-operator`.
- **26.3.**
  - No `REMOVE_AFTER` module remains under `platform/`, and a grep for the old package names returns nothing.
  - Every DDD-spine `[TARGET]` becomes `[ADOPTED]` with `path:line` citations, the `Today` columns are replaced by the target paths, and `troll/` citations are re-pointed to `platform/`. `lint_spine.py` must come back clean.
  - The `test_boundaries.py` legacy map is deleted, and the hot-path baseline is re-recorded from the final tree.
  - `sprint-status.yaml` marks Epics 23–26 `done`.
- **Epic 28.** Epic 28 depends on 26.1's order-book and encoder swap points.
