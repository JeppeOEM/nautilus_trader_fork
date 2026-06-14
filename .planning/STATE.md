---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: verifying
stopped_at: Phase 3 replan complete (03-RESEARCH.md + 03-01-PLAN.md amended for Approach B + gap-log D-06)
last_updated: "2026-06-14T19:18:53.600Z"
last_activity: 2026-06-14 -- Phase 03 Plan 01 (Wave 1) complete and merged
progress:
  total_phases: 5
  completed_phases: 3
  total_plans: 8
  completed_plans: 8
  percent: 60
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-13)

**Core value:** Reliable, continuous capture of Bybit market data into a Nautilus-catalog-compatible parquet archive — no data loss across restarts/disconnects.
**Current focus:** Phase 03 — reliability-for-24-7-operation

## Current Position

Phase: 03 (reliability-for-24-7-operation) — EXECUTING
Plan: 2 of 2
Status: Phase complete — ready for verification
Last activity: 2026-06-14 -- Phase 03 Plan 01 (Wave 1) complete and merged

Progress: [██████████] 100% (of Phases 1-2; milestone has 5 phases total)

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: ~32 min
- Total execution time: ~2.7 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 4 | ~2.1h | ~31min |
| 02 | 2 | ~1.1h | ~33min |

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

None yet.

### Blockers/Concerns

- [Phase 4]: Open interest has no native Nautilus type/subscription — extraction path (ticker field vs. REST poll) needs a spike to confirm before scoping
- [Phase 2]: Funding-rate ticker pushes ~100ms but changes rarely — dedup strategy needed to avoid millions of redundant rows
- [Phase 1/3]: Catalog `write_data` contract (monotonic ts_init, disjoint intervals, filename-collision silent skip) must hold across restarts — verify the StreamingConfig path handles this

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-06-14T19:18:29.613Z
Stopped at: Phase 3 replan complete (03-RESEARCH.md + 03-01-PLAN.md amended for Approach B + gap-log D-06)
Resume file: .planning/phases/03-reliability-for-24-7-operation/03-01-PLAN.md
