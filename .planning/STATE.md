---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 6 code complete; HOT-01 live mainnet smoke deferred
last_updated: "2026-06-15T17:54:00.000Z"
last_activity: 2026-06-15 -- Phase 06 Wave 1 + Wave 2 (Tasks 1-3) executed and merged; Task 4 live smoke deferred
progress:
  total_phases: 6
  completed_phases: 3
  total_plans: 11
  completed_plans: 11
  percent: 50
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-13)

**Core value:** Reliable, continuous capture of Bybit market data into a Nautilus-catalog-compatible parquet archive — no data loss across restarts/disconnects.
**Current focus:** Phase 06 — hot-reload-new-instruments (code complete; HOT-01 live smoke pending)

## Current Position

Phase: 06 (hot-reload-new-instruments) — CODE COMPLETE, AWAITING LIVE VERIFICATION
Plan: 2 of 2 (both code-complete and merged; 06-02 Task 4 live mainnet smoke deferred)
Status: Phase 06 implementation done; HOT-01 cannot be marked complete in REQUIREMENTS.md until the live hot-add smoke (06-02 Task 4) is run and approved
Last activity: 2026-06-15 -- Phase 06 Wave 1 + Wave 2 (Tasks 1-3) executed, merged to gg; Task 4 live smoke deferred

Progress: [██████████] 100% (of Phases 1-2; milestone has 5 phases total)

## Performance Metrics

**Velocity:**

- Total plans completed: 9
- Average duration: ~32 min
- Total execution time: ~2.7 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 4 | ~2.1h | ~31min |
| 02 | 2 | ~1.1h | ~33min |
| 03 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: 01-04 (45min), 02-01 (~25min), 02-02 (~40min)
- Trend: —

*Updated after each plan completion*

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 01 | P04 | 45min | 3 tasks | 4 files |
| 02 | P01 | ~25min | 3 tasks | 5 files |
| 02 | P02 | ~40min | 3 tasks | 6 files |
| Phase 03-reliability-for-24-7-operation P02 | 25min | 2 tasks | 5 files |

## Accumulated Context

### Roadmap Evolution

- Phase 6 added: Hot-Reload Config Changes (retitled from "Hot-Reload New Instruments" after discuss-phase broadened scope to the full diff): periodically detect changes to recorder.toml's instrument list/params while running -- additions, removals, and depth/bar_interval changes -- and apply them live (load+subscribe new instruments, unsubscribe removed, clean-swap changed params) without restarting the process or disrupting recording for unaffected instruments. New requirement HOT-01 added.

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Persistence: `StreamingConfig` + `StreamingFeatherWriter` + `catalog.convert_stream_to_data()` (no custom CatalogWriter) — decided in research, shapes Phases 1-3
- Single-process / no Redis — one Bybit data client multiplexes LINEAR + SPOT subscriptions
- Official `ParquetDataCatalog` format, partitioned by UTC day
- [Phase 1]: A2: double-conversion of an un-rotated feather file is idempotent (already-exists skip, stable row count) - no ValueError handling needed
- [Phase 1]: A3: on_start missing-instrument RuntimeError propagates to non-zero exit via node.run(raise_exception=True); never collapses into self.stop()
- [Phase 1]: Bybit pyo3-native enums (BybitProductType, BybitEnvironment) must be registered in CUSTOM_ENCODINGS for streaming-enabled TradingNodeConfig.json() to succeed
- [Phase 2-01]: include_types widened to [TradeTick, QuoteTick, OrderBookDeltas, Bar, MarkPriceUpdate, IndexPriceUpdate]; FundingRateUpdate deliberately excluded pending dedup design in 02-02
- [Phase 2-01]: D-03 honored literally for spot (depth > 50 raises); linear adds discrete-set {1,50,200,1000} defense-in-depth, validated fail-fast in load_recorder_config
- [Phase 2-01]: Mark/index price subscriptions iterate linear_instrument_ids only (D-04)
- [Phase 2-02]: Deduped FundingRateUpdate persists via a strategy-owned second StreamingFeatherWriter (include_types=[FundingRateUpdate]), separate from the kernel "*" writer; reads back natively via catalog.funding_rates()
- [Phase 2-02]: Live mainnet smoke (Task 3) PASSED -- all 7 feeds (trade/quote/order_book_deltas/bar/mark/index/funding) landed in catalog/streaming for both LINEAR and SPOT instruments
- [Phase 2]: catalog_path in recorder.toml is dead/unused; streaming_path ("catalog/streaming") is the real catalog root -- any inspection tooling must point there
- [Phase 3-01]: Conversion is Approach B (`_convert_finalized_feather_files`); `on_stop()` flush+convert + per-process timestamped feather filenames give a one-cycle visibility delay on restart but never lose data (D-02/D-03)
- [Phase 3-01]: `_log_restart_gaps()` on_start WARNING (D-06, `restart_gap_threshold_seconds` config, default 60) verified live on real Bybit mainnet: gap-log fires correctly, no data loss/non-disjoint-interval error across SIGTERM restarts
- [Phase 3-01]: REL-04 (reconnect) verified structurally only -- zero custom reconnect/resubscribe code in scripts/bybit_recorder/, fully adapter-driven; forced-disconnect live test deferred (treated as satisfied)
- [Phase 03-reliability-for-24-7-operation]: on_funding_rate records _last_seen BEFORE its dedup early-return (Pitfall 4)
- [Phase 03-reliability-for-24-7-operation]: Per-data-type stale thresholds (trade/quote/bar=90s, deltas=60s, mark/index/funding=30s) with 90s default, validated positive at load

### Pending Todos

- [Phase 6]: HOT-01 live mainnet hot-add smoke (06-02 Task 4, `checkpoint:human-verify`) is DEFERRED — not yet run. Run: `uv run python scripts/bybit_recorder/recorder.py scripts/bybit_recorder/recorder.toml` against mainnet, then edit `recorder.toml` to add a new LINEAR instrument while running. Confirm: (1) "Config reload: 1 added..." INFO then the two-phase load/subscribe on the following poll; (2) pre-existing instruments keep producing trades/deltas (cache stays additive, A1); (3) the new instrument's feeds land in the streaming catalog; (optional) a bogus id logs one ERROR and is not retried (D-07/D-11), and a removal/depth-change clean-swaps correctly. Full steps in `.planning/phases/06-hot-reload-new-instruments/06-02-SUMMARY.md` and `06-02-PLAN.md` Task 4. Once approved, mark HOT-01 complete in `.planning/REQUIREMENTS.md` and update this entry.
- [Phase 3]: Gap-closure for 03-VERIFICATION.md (status=gaps_found, 6/7 must-haves). CR-01 from 03-REVIEW.md: `_run_conversion()` in `scripts/bybit_recorder/strategy.py` (~lines 452-459) has an unguarded `ParquetDataCatalog(self.config.catalog_path)` construction and an unguarded `self._funding_writer.flush()` call OUTSIDE the per-type try/except that protects the rest of the function. If either raises during `on_stop()` (SIGTERM), the exception propagates and faults the strategy component mid-shutdown, undermining REL-02's "no data loss on restart" guarantee. Fix: wrap catalog construction in try/except (log + return early on failure); wrap `self._funding_writer.flush()` in its own try/except (log + continue), matching the existing per-type swallow pattern just below. Next step: `/gsd:plan-phase 03 --gaps` to create the gap-closure plan, then re-run execute-phase.
- [Phase 3, lower priority]: WR-01 from 03-REVIEW.md: `restart_gap_threshold_seconds` (config.py ~line 287) lacks the same fail-fast `> 0` validation applied to `heartbeat_interval_seconds`/`stale_threshold_*`. A `0`/negative value silently passes through and triggers a restart-gap WARNING on nearly every startup. Worth fixing alongside the CR-01 gap-closure.

### Blockers/Concerns

- [Phase 4]: Open interest has no native Nautilus type/subscription — extraction path (ticker field vs. REST poll) needs a spike to confirm before scoping
- [Phase 2]: Funding-rate ticker pushes ~100ms but changes rarely — dedup strategy needed to avoid millions of redundant rows
- [Phase 1/3]: Catalog `write_data` contract (monotonic ts_init, disjoint intervals, filename-collision silent skip) must hold across restarts — verify the StreamingConfig path handles this

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260615-acs | Fix WR-02 (03-REVIEW.md): guard _log_restart_gaps ParquetDataCatalog construction in scripts/bybit_recorder/strategy.py with try/except, mirroring _run_conversion's pattern, plus a unit test | 2026-06-15 | 8342b92024 | [260615-acs-fix-wr-02-03-review-md-guard-log-restart](./quick/260615-acs-fix-wr-02-03-review-md-guard-log-restart/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-06-15T11:41:37.690Z
Stopped at: Phase 6 context gathered
Resume file: .planning/phases/06-hot-reload-new-instruments/06-CONTEXT.md
