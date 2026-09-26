---
title: 'Story 26.1: LiveBook, TradeIntake, FeedGroup and a pure SecondSampler, in place'
type: 'refactor'
created: '2026-09-26'
status: 'done'
final_revision: '302356ec3f2499b6f8ed0286d6240100c90fa6cc'
baseline_revision: 'aae75c5707db21b61477116354559a1223dd6107'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-26-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized', 'multiple-goals']
---

<intent-contract>

## Intent

**Problem:** The capture write gate is spread over ~40 per-instrument dicts on `collector_core.collector.Collector` and over four venue hook overrides (`_apply_deltas`, `_handle_crossed_book`, `_clear_book_state`, `apply`) that any venue can use to bend the gate. Its counters have no single owner, the empty-top-of-book rejection is silent (parent-spine Deferred, a DATA-07 gap), and the Parquet/Redis/REST I/O is welded into the gate class.

**Approach:** Refactor in place, without moving packages. The state moves into explicit per-instrument (`LiveBook`, `TradeIntake`) and per-venue (`FeedGroup`) aggregates in `collector_core/domain/`, and one pure `SecondSampler` holds the gate. Venue variance becomes pure, synchronous policy values. `Collector` stays the application service that owns the loops and I/O, executes the requested resyncs and is the only ledger caller. The Parquet/Redis/REST I/O goes behind `ports.py` Protocols with adapters. The existing hot-path replay gates the whole change.

## Boundaries & Constraints

**Always:**
- Frozen published language (AD-D12): Parquet schemas and directories, `snapshots:raw`/`collector:status` payloads, config key sets, compose service names, env vars, bind mounts. The flush-report log formats and every ledger site name stay as they are; `collector.empty_top` is the only new site.
- Hot path (AD-D5): `on_data` stays an O(1) enqueue. `LiveBook` wraps the Nautilus `OrderBook` by reference. No object is created per delta or per trade beyond today's; a verdict or event exists only per sample or per state transition. `test_hotpath.py`'s burst is unchanged: allocations must stay ≤ baseline and wall time ≤ 2× baseline, recorded on D-65.
- Domain modules and every `policies.py` import only the stdlib, `kernel` and Nautilus value types. They never log, ledger, `await` or read the clock (the caller passes `now_ns`).
- A resync is the fallback, never the first response (DATA-03), and every forced resync is ledgered `collector.resync`.
- Every existing behaviour test keeps its scenario and assertions. Only how it reaches moved state or constructs the collector may change.
- Shim rules (MR): the whole-module re-export `collector_core/trade_backfill.py` and the moved name `collector.quarantine_corrupt_parquet` both expire at `26-3-closeout-shims-gone-spines-reconciled`. The `live_paper/` shims expire at this story, so they are deleted here.

**Block If:**
- The hot-path allocation figures exceed the baseline and no allocation can be removed without changing behaviour.

**Never:**
- Moving packages to `capture/` (that is 26.2).
- Re-baselining `hotpath_baseline.json` or changing the burst.
- Touching `nautilus_trader/` or `crates/`.
- Adding a dependency.
- Leaving a venue override of a core method other than `__init__`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Empty top | book with one side empty | sample rejected `EmptyTop`, trades of the second discarded | WARNING + `collector.empty_top`, both once per instrument per minute |
| Core crossed book | Bybit/HL crossed | rejected `Crossed`; after `crossed_resync_seconds`, `ResyncRequested` if the client can resync | `collector.crossed_book` once per episode; `collector.resync` per forced resync |
| dYdX crossed book | both levels tagged | policy drops the older-tagged level(s) (≤ 5 steps) → `Uncrossed`, sample accepted | INFO per dropped level; WARNING if a side is left empty |
| dYdX stuck cross | untagged, or still crossed past the grace window | `StillCrossed`, then `ResyncRequested` | CRITICAL `steady_state_crossed_book` JSON on `collector_core.critical` + `collector.resync` |
| Bybit `u` gap/regress | `u` skips or goes backwards | message dropped, book cleared, resync pending | `collector.book_sequence` |
| Bybit zero-level message | no `u` to read | canary skipped (known false-positive case, DATA-08) | unchanged |
| Venue pending overflow | held > hold_back + 5 s | book cleared, resync pending if the client can resync | `collector.pending_deltas` |
| Resync while `apply` holds the lock | lock held | queued on the `LiveBook`, retried next sample | — |

</intent-contract>

## Code Map

- `platform/collector_core/collector.py`: today's gate. It becomes the application service; the state and logic move out.
- `platform/dydx_collector/{collector,uncross}.py`: the overrides and the uncross ladder. The ladder becomes `dydx_collector/policies.py` and `uncross.py` is deleted.
- `platform/bybit_collector/collector.py`: the `u` canary, which becomes `bybit_collector/policies.py`.
- `platform/hyperliquid_collector/collector.py`: full-snapshot venue. It gets no policies module; it uses the core defaults and has no resync.
- `platform/collector_core/trade_backfill.py`: split. The fetch and parse half goes to the venue `trade_history.py` modules plus the shared `collector_core/domain/trade_history.py`. The scheduling half and the report go to `collector_core/application/trade_backfill.py`.
- `platform/collector_core/{ports,feed,gap_markers,capture_lock}.py`: ports; the ledger calls are routed through a `Ledger` callback.
- `platform/tests/{test_hotpath,test_boundaries,test_images,test_namespace}.py`: the gates and guardrails.
- `platform/docs/DATA_INTEGRITY_AUDIT.md` D-65, and the parent spine's Deferred entry "Empty top-of-book skip is silent".

## Tasks & Acceptance

**Execution:**
- [x] `collector_core/domain/{__init__,verdicts,events,policies,live_book,trade_intake,feed_group,sampler,flush_batch,trade_history}.py`: aggregates, verdicts, events and core-default policies. Every docstring names its invariant (DESIGN-01). `trade_history.py` holds the exact-conversion and parse helpers plus `BackfillCapability`.
- [x] `collector_core/ports.py`: add `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier`, `Ledger`, and `Fetched`.
- [x] `collector_core/infrastructure/{__init__,parquet_writer,redis_stream}.py`: the catalog write, quarantine, instrument definitions, gap marks, capture lock and the `apply_zstd_default` patch; the `snapshots:raw` publish.
- [x] `collector_core/application/{__init__,trade_backfill}.py`: `BackfillReport`, and applying a fetch to a `TradeIntake`.
- [x] `collector_core/sites.py`: every capture ledger site as a constant.
- [x] `collector_core/collector.py`: rewrite over the aggregates. It owns the loops, executes `ResyncRequested` after the sample, ledgers through `sites`, and reports every counter at flush in today's format. It takes the `archive`, `live_stream`, `policies`, `trade_history` and `notifier` injections.
- [x] `collector_core/{feed,gap_markers,capture_lock}.py`: ledger through the injected `Ledger`.
- [x] `{dydx,bybit,hyperliquid}_collector/trade_history.py`, and `policies.py` for dYdX and Bybit. The venue `collector.py` modules build the adapters and policies in `__init__` and override nothing else. Delete `dydx_collector/uncross.py`.
- [x] `collector_core/trade_backfill.py`: the re-export shim. Delete `live_paper/`, together with its dockerfile `COPY` and its test and boundary entries.
- [x] Tests: repoint the collector tests at the new state (assertions unchanged). Split `test_trade_backfill.py` into venue `test_trade_history.py` files and move their fixtures with them. Add aggregate, policy and sampler invariant tests. `test_boundaries.py`: venue `policies.py` counts as domain; no logging or `await` in capture domain/policies; the sites grep test.
- [x] Docs: `platform/CLAUDE.md` citations (DATA-01/03/04/06/08, Adding a venue), `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md` if it cites moved code, D-65 run numbers, the parent spine's Deferred strike, and the DDD spine `[amended]` notes where adopted. Append an entry to `DEPLOY_CHECKLIST.md`'s "Deferred operator actions" (OPS-01).

**Acceptance Criteria:**
- Given the refactored tree, when the collector, venue, boundary, namespace, images and hotpath suites run, then they pass with no new failures against the pre-story baseline and no new warnings.
- Given `grep error_ledger.record` over the capture packages (non-test), when checked, then the only hit is `collector_core/collector.py`, and every site string lives in `collector_core/sites.py` and is used.
- Given `DydxCollector`, `BybitCollector` and `HyperliquidCollector`, when inspected, then none defines a method that the base `Collector` also defines, other than `__init__`.

## Design Notes

- **Resync order.** A resync drops local state first (`LiveBook.resync()`) and then calls `VenueFeed.resync_orderbook`. The spine's "then `LiveBook.resync()`" wording is inverted on purpose: clearing after the await could wipe the fresh snapshot that the ingest loop books during it.
- **CRITICAL logger.** dYdX's CRITICAL steady-state JSON moves from the `dydx_collector.critical` logger to `collector_core.critical`, because the core executes every venue's resync. The incident rules match on the text, not the logger name.
- **dYdX ledger change.** dYdX forced resyncs are now ledgered `collector.resync`, as DATA-03 already requires. dYdX crossed episodes stay unledgered (`CrossedBookPolicy.crossing_is_corruption = False`, DATA-04).
- **Counter lifetime.** Aggregates are created lazily on the collected message path. A removed id's `LiveBook` is reset at removal and dropped after its counters have been reported at the next flush, so no count is lost. A `TradeIntake` outlives removal: its bounded dedup window keeps DATA-06 across a remove followed by a re-add, as today.
- **Allocation headroom.** The dedup map's first-copy value becomes a plain `str` feed name instead of a one-element list. A list is created only on a repeat.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests collection_control/tests tests -q`: expected to pass, except the known pre-existing failures.
- `cd platform && PYTHONHASHSEED=0 PYTHONPATH=. python3 tests/test_hotpath.py --measure`: expected allocations ≤ `tests/fixtures/hotpath_baseline.json`.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 17 (high 0, medium 3, low 14)
- defer: 0
- reject: 15 (high 0, medium 1, low 14)
- addressed_findings:
  - `[medium]` `[patch]` The gate trusted a policy's `Uncrossed` verdict without checking the book, so a buggy policy could get a crossed row written. `LiveBook._judge_cross` now re-reads the book after the step and checks it itself.
  - `[medium]` `[patch]` When REST archived a trade first, every later live copy was folded, so with `trade_feeds = 2` the trade was counted twice in the live row. Only the first live copy is folded now. This bug predates the story; the aggregate claims the invariant, so it was fixed here.
  - `[medium]` `[patch]` A `ResyncRequested` on a client with no `resync_orderbook` became a ledger-flooding retry loop. `_resync` now just settles the flag for a full-snapshot venue.
  - `[low]` `[patch]` A crossed verdict reported the prices from before a policy step had deleted levels. The book is now re-read after the step.
  - `[low]` `[patch]` A stale `resync_pending` flag survived a fresh snapshot rebuilding the book. It is now cleared when a Clear builds a book from none.
  - `[low]` `[patch]` An armed cross-check captured the book after a dropped or empty message. It now captures only after an applied one, both on the arrival path and in `drain`.
  - `[low]` `[patch]` Book disposal ran inside the reporting function. Disposal now runs in `_close_report_cycle`, which the flush loop and the final flush call.
  - `[low]` `[patch]` `_side_ages` read a missing per-side stamp as 0. It now returns None unless both sides have a stamp.
  - `[low]` `[patch]` `forget()` did not reset the per-side delta stamps. It now does.
  - `[low]` `[patch]` The collector's docstring said "Guards, unchanged in behaviour". It now points at audit D-66/D-67.
  - `[low]` `[patch]` The domain rule counted any `<pkg>.policies` module as capture domain. It is now restricted to the `*_collector` packages.
  - `[low]` `[patch]` `FeedGroup.note_first_copy`/`note_overlap` were never called. The collector now uses them.
  - `[low]` `[patch]` The one-second constant was defined in several places. It now comes from `kernel.clocks.NS_PER_S`, and the stale `_VENUE_AHEAD_NS` docstring name is fixed.
  - `[low]` `[patch]` `LiveBook.take_counts` returned a positional tuple. It now returns the named `BookCounts`.
  - `[low]` `[patch]` Loose types: `SequenceBroken.verdict` is now `SequenceVerdict`, `crossing_is_corruption` is now a `ClassVar`, and `ArchiveWriter.acquire_lock` now returns `IO[str] | None`.
  - `[low]` `[patch]` `DydxTradeHistory` rejected an unknown environment only at the first backfill. It now rejects it at construction.
  - `[low]` `[patch]` The dYdX tie-break test had lost its uncrossed assertion. It now asserts `Uncrossed` again.


### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2 (high 0, medium 0, low 2)
- defer: 1 (high 0, medium 1, low 0)
- reject: 15 (high 0, medium 2, low 13)
- addressed_findings:
  - `[low]` `[patch]` When `resync_orderbook` raised after the fresh snapshot had already rebuilt the book during the await, `_resync_wire` set `resync_pending` on a healthy book. The sampler only retries a missing book, so the stale flag stayed in `_resync_pending()` and would fire an unwarranted DATA-03 resync at the next drop. It now sets the flag only when the book is still missing, and the failure is still ledgered (`test_a_failed_resync_after_the_snapshot_arrived_queues_no_retry`, which fails without the fix).
  - `[low]` `[patch]` `BybitTradeHistory` and `HyperliquidTradeHistory` accepted an unknown environment and failed only at the first backfill after a reconnect, as a per-instrument `KeyError`. Both now refuse it at construction, as `DydxTradeHistory` already did. There is a construction test for each of the three venues.

## Auto Run Result

Status: done

**Summary:** Story 26.1 refactored the capture write gate in place into explicit aggregates in `collector_core/domain/`, plus pure venue policy values:
- The aggregates are `LiveBook`, `TradeIntake`, `FeedGroup`, a pure `SecondSampler` and `FlushBatch`.
- The policy values are `CrossedBookPolicy`, `LevelTagger`, `SequenceCanary`, `BookTimeSource` and `BackfillCapability`.
- The I/O sits behind `ports.py` with infrastructure adapters.
- `Collector` is the application service and the only ledger caller.

The first run's result is in commit a8aa4bc92c. This run was a follow-up independent review pass over the whole diff since aae75c5707. It found no intent or spec problems and applied two small, localized patches.

**Files changed in this pass:**
- `platform/collector_core/collector.py`: `_resync_wire`'s failure path queues a retry only while the book is still missing.
- `platform/bybit_collector/trade_history.py` and `platform/hyperliquid_collector/trade_history.py`: an unknown environment is refused at construction.
- `platform/collector_core/tests/test_collector.py`: a regression test for the resync-flag fix.
- `platform/{dydx,bybit,hyperliquid}_collector/tests/test_trade_history.py`: a construction test for the environment check.
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new entry, for the pre-existing backfill-larger-than-dedup-window duplicate.

**Review:** 2 patches applied, 1 item deferred, 15 rejected. Every rejection falls into one of these groups:
- Pre-existing behaviour carried over unchanged:
  - the canary running on messages dropped before the snapshot (both arrival and venue mode);
  - a crossed episode surviving an empty-top interval;
  - id-less dYdX rows collapsing in the page dedup;
  - the synthetic DELETE's size precision;
  - never-pruned per-instrument log dicts.
- A deliberate choice in the spec's Design Notes or matrix:
  - `TradeIntake` outlives removal;
  - `empty_top` is ledgered once per instrument per minute;
  - the `collector_core.critical` logger;
  - the hot path not measuring policies until 26.3.
- Not reachable in production: an empty `OrderBookDeltas`, which both Nautilus constructors refuse.
- Guard tests that are exactly as wide as the spec's acceptance criteria.
- The shim's dropped `fetch_trades`, which has no importer left.

**Follow-up review recommended:** false. The two fixes are low-severity, localized and covered by tests.

**Verification:**
- `python3 -m pytest -o addopts="" --rootdir=. collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests collection_control/tests tests -q -W error::DeprecationWarning`: 607 passed.
- The new resync test fails with the fix stashed.
- `tests/test_hotpath.py --measure` gave 0.7128 / 89.154 / 104.9495 per message, identical to the first run, at 3,175 ns, within the 2× limit.
- `ruff` 0.15.16 (the pre-commit pin) check and format pass on the touched files.

**Residual risks:**
- These are carried from the first run and recorded on D-66/D-67:
  - dYdX forced resyncs are now ledgered;
  - the CRITICAL line has moved logger;
  - a one-sided book left by an uncross is now rejected as an empty top.
- Bybit now also emits the `steady_state_crossed_book` CRITICAL line on a forced resync.
- The hot-path baseline does not yet measure venue policies; 26.3 re-records it.
- A VPS redeploy is owed (DEPLOY_CHECKLIST "Deferred operator actions", 26-1).
