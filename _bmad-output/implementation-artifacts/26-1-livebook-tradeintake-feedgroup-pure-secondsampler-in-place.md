# Story 26.1: `LiveBook`, `TradeIntake`, `FeedGroup` and a pure `SecondSampler`, in place

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 26). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 26.1".

## Story

As a collector maintainer,
I want the write gate to be one pure function over explicit per-instrument and per-venue aggregates, with venue variance supplied as pure policy values,
So that no venue can override the gate, every counter has a name, and the hot path is provably no slower than before.

## Acceptance Criteria

1. **Given** `collector_core/collector.py`'s per-instrument dicts (`_live_books`, `_last_book_update_ns`, `_crossed_since_ns`, `_resync_pending`, `_level_msg_id`, `_last_u`, pending deltas), trade dicts (`_second_trades`, `_seen_trade_ids`, the duplicate/stale/late/ahead counters, per-feed baselines), feed state (`_last_feed_message_ns`, feed states, backfill requests) and `_sample_tick`
**When** the story ships
**Then** `collector_core/domain/` holds `live_book.py` (`LiveBook` wrapping the Nautilus `OrderBook` by reference; `apply`, `clear`, `resync`, `snapshot_top(depth, now, policies) -> SampleVerdict`; pending `ts_event`-ordered deltas with the `hold_back + _VENUE_AHEAD_NS` overflow bound), `trade_intake.py` (`TradeIntake`: bounded id window, stale-history filter, per-feed first-copy arbitration with `duplicate` vs `duplicate_feed`, late/ahead counters, every counter reported at flush), `feed_group.py` (`FeedGroup`: `Feed` values, per-feed `FeedLiveness`, reconnect detection, one-sided outage comparison, `BackfillRequest` scheduling and abandonment), `sampler.py` (`SecondSampler.sample(...)` — pure, returns accepted snapshots, `SampleRejected`s and requested actions; closes second `S` at `S + 1 + hold_back_seconds` under `venue` time), `flush_batch.py` (`FlushBatch` with the `_TRADE_CARRY_NS` carry rule), `verdicts.py`, `events.py`; `Collector` becomes the application service that owns the loops, executes `ResyncRequested` through the client, and is the only ledger caller with every site listed in `collector_core/sites.py`; the empty-top-of-book rejection gains a rate-limited warning and the ledger site `collector.empty_top` (parent Deferred struck)

2. **Given** the venue hook overrides (`_apply_deltas`, `_handle_crossed_book`, `_clear_book_state`, `_instrument_ids`) in the three venue collectors and `dydx_collector/uncross.py`
**When** the story ships
**Then** venue variance is supplied as policy values passed to the aggregates: `CrossedBookPolicy.step(book, tags, now_ns) -> Uncrossed | StillCrossed(since) | ResyncRequested` (dYdX: the uncross ladder, synchronous; Bybit/HL: core default), `LevelTagger`, `SequenceCanary`, `BookTimeSource`, `BackfillCapability`; every policy is pure and synchronous (no logging, ledgering or `await`; `test_boundaries.py` treats policy modules as `domain/`), the three venue `Collector` subclasses override no core method other than `__init__`, and the DATA-04/DATA-08 tests (uncross ladder, Bybit `u` canary incl. the zero-level message case, Hyperliquid full-snapshot) pass against the policies

3. **Given** `test_hotpath.py`'s baseline from Story 23.1
**When** the refactored ingest path runs the same replay
**Then** allocations per message ≤ baseline and wall time per message ≤ 2× baseline; the numbers are recorded next to the baseline in `docs/DATA_INTEGRITY_AUDIT.md`; the story does not merge otherwise

4. **Given** the client contract docstring (`collector_core/collector.py:32-50`)
**When** the story ships
**Then** `collector_core/ports.py` declares `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier` as `Protocol`s alongside `SecondSink`; `trade_backfill.py`'s fetch/parse half moves into per-venue `trade_history.py` modules implementing `VenueTradeHistory` (fixture tests unchanged) and its scheduling half stays in the application layer; the Parquet write, quarantine, instrument-definition write and capture lock live in an `ArchiveWriter` adapter and the Redis publish in a `LiveStream` adapter; all 22.13/22.14/22.12 tests pass unchanged

## Tasks / Subtasks

- [ ] Task 1 — domain aggregates in `collector_core/domain/` (AC: #1)
  - [ ] `live_book.py`, `trade_intake.py`, `feed_group.py`, `sampler.py`, `flush_batch.py`, `verdicts.py` (`SampleVerdict`, `Rejected` reasons `NoBook|EmptyTop|Crossed|Stale(kind)`), `events.py` — moving state out of `Collector.__init__` (`collector.py:542-660`) and logic out of `_apply_deltas`, `_accept_live_trade`, `_first_copy_feed`, `_register_trade`, `_hold_deltas`, `_drain_pending_deltas`, `_check_pending_overflow`, `_sample_tick`/`_sample_instrument`, `_take_batches`, `_split_open_ts_init_group`, `_poll_feed_states`, `_schedule_backfill`. Every docstring names its invariants (AD-D6). `Collector` keeps the loops and the I/O; `collector_core/sites.py` lists every ledger site and `Collector` is the only caller (grep test).
  - [ ] Empty-top rejection: rate-limited WARNING + `collector.empty_top` ledger site; strike the parent Deferred entry.
- [ ] Task 2 — policies as values (AC: #2)
  - [ ] `collector_core/domain/policies.py` (`CrossedBookPolicy`, `LevelTagger`, `SequenceCanary`, `BookTimeSource`, `BackfillCapability` protocols + core defaults); venue implementations in `dydx_collector/policies.py` (uncross ladder from `uncross.py`, synchronous, returns `ResyncRequested` instead of calling resync; `LevelTagger`), `bybit_collector/policies.py` (`_sequence_verdict`/`_message_u`), `hyperliquid_collector/policies.py` (full-snapshot: no resync). `Collector` executes `ResyncRequested` after the sample via `VenueFeed.resync_orderbook` then `LiveBook.resync()`; the three subclasses override nothing but `__init__`. Boundary test treats `*/policies.py` as domain (no logging/ledger/await imports).
- [ ] Task 3 — hot-path gate (AC: #3)
  - [ ] Run `platform/tests/test_hotpath.py`; if over the bound, profile (`tracemalloc` top diffs, `cProfile`) and remove per-message allocations (no dataclass per delta/trade; reuse verdict singletons; keep the queue O(1)). Record numbers in `docs/DATA_INTEGRITY_AUDIT.md`.
- [ ] Task 4 — ports and adapters (AC: #4)
  - [ ] `collector_core/ports.py` gains `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier`; `collector_core/infrastructure/parquet_writer.py` (`_flush_once`'s write, quarantine, instrument-definition write, capture lock), `redis_stream.py` (`_publish_snapshot_batch`); `trade_backfill.py` split: `dydx_collector/trade_history.py`, `bybit_collector/trade_history.py`, `hyperliquid_collector/trade_history.py` (fetch + parse + `BackfillCapability`; fixtures under `tests/fixtures` move with them) and `collector_core/application/trade_backfill.py` (scheduling + report). All 22.12/22.13/22.14 tests pass.

## Dev Notes

The gate refactor in place, without moving packages, so the diff is reviewable and the hot-path test (23.1) can gate it. Policies must be pure and synchronous (adversary H5/M6): the uncross ladder returns `ResyncRequested` and `Collector` performs the resync after the sample. Every counter that exists today (`duplicate`, `duplicate_feed`, `late`, `ahead`, `orphan`, `refused`, `unrecoverable`, stale, pending-overflow) must survive with the same flush report format (`collector_core/tests/test_collector.py` is the regression net — ~630 lines added by 22.14 alone).

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

- Spine sections: AD-D5, AD-D6, AD-D7, AD-D16, AD-1/AD-2 (inherited)
- Review findings that shaped this story: reviews/review-adversary.md H5, M3, M6; reviews/review-rubric.md H5, M1, M3
- Code: `collector_core/collector.py` (whole file), `collector_core/{feed,trade_backfill,book_check,integrity}.py`, `dydx_collector/uncross.py`, `bybit_collector/collector.py:116-130`, `hyperliquid_collector/client.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
