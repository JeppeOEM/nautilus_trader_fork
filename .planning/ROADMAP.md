# Roadmap: dYdX Research Platform

## Overview

Milestone v1.0 delivers a single artifact: `troll/CLAUDE.md`, a set of actionable rules for working on this codebase. Every rule maps to a real failure mode — fork contamination, memory leaks, over-engineering, silent Nautilus precision bugs — observed in earlier iterations of this project. One phase, one file, done.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Project Guardrails** - Write `troll/CLAUDE.md` encoding all 17 requirements as enforceable rules for working on this codebase (completed 2026-06-27)

## Phase Details

### Phase 1: Project Guardrails

**Goal**: `troll/CLAUDE.md` exists and gives Claude clear, enforceable rules across fork safety, design, memory discipline, testing, readability, and Nautilus usage patterns — preventing the specific failure modes documented in this project's history before any feature work starts
**Depends on**: Nothing (first phase)
**Requirements**: FORK-01, FORK-02, DESIGN-01, DESIGN-02, DESIGN-03, MEM-01, MEM-02, MEM-03, TEST-01, TEST-02, TEST-03, READ-01, READ-02, READ-03, NAUT-01, NAUT-02, NAUT-03
**Success Criteria** (what must be TRUE):

  1. `troll/CLAUDE.md` exists with sections covering all 6 rule categories: fork safety, design principles, memory discipline, testing, code readability, Nautilus usage patterns
  2. The fork safety section explicitly names `nautilus_trader/` and `crates/` as off-limits and prohibits `TradingNode`/`DataEngine` instantiation in troll/ code
  3. The memory section names the specific anti-pattern (`catalog.trade_ticks()` with no time bounds) and mandates `BacktestDataConfig` streaming or time-bounded queries as the alternative
  4. The Nautilus section documents the `Price(decimal, precision)` silent precision bug and mandates `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()` for any re-stamping operation
  5. The testing section clearly distinguishes required tests (financial calculations, Nautilus type integrations) from optional tests (trivial glue, config parsing) so the rule is actionable without judgment calls

**Plans**: 1/1 plans complete

- [x] 01-PLAN.md — Write `troll/CLAUDE.md` with all six rule-category sections (17 requirements)

- [x] **Phase 2: Dashboard Upgrade** - Meaningful metrics table with clickable coins + single-coin view showing all derivable indicators from DydxSecondSnapshot and a real-time line chart (mid, bid, ask, microprice as lines at 1s resolution) (completed 2026-06-28)

### Phase 2: Dashboard Upgrade

**Goal**: The dashboard shows a ranked metrics table (OFI_3/5/10, OBI_3/5/10, CVD, spread, microprice lean, volume delta, buy/sell count) with clickable rows that navigate to a single-coin view showing all derivable indicators plus a real-time line chart with mid price, bid, ask, and microprice as separate lines updating every second from the in-process rolling deque — no Parquet reads for the live path, no bar chart.
**Depends on**: Phase 1
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04
**Success Criteria**:

  1. Main rankings table shows at minimum: OFI_10, OBI_10, CVD (60s), spread, microprice lean, volume delta, buy/sell count per coin
  2. Clicking any coin row navigates to `/coin/{id}` single-coin view
  3. Single-coin view shows a panel of all derivable indicators (OFI_3/5/10, OBI_3/5/10, microprice, spread, CVD, volume imbalance, buy/sell avg size)
  4. Single-coin view includes a real-time line chart with 4 lines: mid, bid, ask, microprice — 1s resolution, rolling 5-min window, updates live
  5. All live data comes from the in-process `_second_rolling` deque — no Parquet reads on the live path

**Plans**: 2/2 plans complete

- [x] 02-01-PLAN.md — Extend live metrics (CVD, volume delta, microprice lean, buy/sell count, avg trade size) + fix `_fast_loop` write path + tests
- [x] 02-02-PLAN.md — Expand rankings columns, clickable rows, deque-backed live `/coin/{id}` view with 1s 4-line chart + `/data/coin/{id}` JSON endpoint

- [x] **Phase 3: Collector/Dashboard Split** - Decouple dashboard from collector process via Redis pub/sub + aiohttp SSE; browser EventSource replaces polling; no shared Python process, no GIL contention (completed 2026-06-29)

### Phase 3: Collector/Dashboard Split

**Goal**: Collector becomes a pure data pipeline (no HTTP server, no dashboard threads). Dashboard becomes a standalone aiohttp service that subscribes to Redis pub/sub for 1s snapshots, maintains its own in-memory metric cache, and pushes updates to the browser via Server-Sent Events — eliminating polling latency and GIL competition between collection and rendering.
**Depends on**: Phase 2
**Requirements**: ARCH-01, ARCH-02, ARCH-03
**Success Criteria**:

  1. Collector process has zero dashboard imports — `serve_in_background` call removed, `_second_loop` publishes JSON snapshot batches to Redis channel `snapshots:1s`
  2. Dashboard runs as a separate Docker service using aiohttp; browser connects via `EventSource('/stream')` not `setInterval`
  3. Rankings table updates arrive in browser within 100ms of the collector publishing a snapshot batch (measurable via browser DevTools EventStream)
  4. Restarting the collector does not crash or hang the dashboard — it reconnects to Redis automatically and resumes streaming
  5. Restarting the dashboard does not affect the collector in any way

**Plans**: 1/1 plans complete

- [x] 03-PLAN.md — Decouple collector/dashboard: Redis pub/sub in _second_loop, aiohttp SSE at /stream, EventSource in browser, redis + dashboard Docker services (6 tasks)

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Project Guardrails | 1/1 | Complete    | 2026-06-27 |
| 2. Dashboard Upgrade | 2/2 | Complete   | 2026-06-28 |
| 3. Collector/Dashboard Split | 1/1 | Complete   | 2026-06-29 |

### Phase 4: Signal Library + Live Dashboard Fix

**Goal:** Fix the dashboard's 1s live updates — the root cause is `_ingest_batch` blocking the event loop (OBI recreated fresh per call instead of kept as persistent state, plus synchronous compute running on the async loop thread). Fix: make OBI persistent like OFI already is, offload `_ingest_batch` to a thread so the event loop stays free for SSE writes. Also fix the slow loop overwriting 1s-fresh OFI/OBI values every 60s by splitting into separate fast (Redis) and slow (Parquet) caches merged at display time.
**Depends on:** Phase 3
**Requirements**: DASH-05, DASH-06, DASH-07, DASH-08
**Success Criteria**:

  1. Rankings table in browser updates visibly every ~1s — SSE frames arrive on schedule with no batching or skipping
  2. `/coin/{id}` page updates every ~1s — no timer drift or missed frames
  3. `_ingest_batch` no longer creates `MultiLevelOBI` instances per call — OBI state is persistent across calls (same pattern as `_OFI_INDS`)
  4. `_slow_loop_task` never overwrites ofi/obi/microprice/spread/cvd/volume_delta keys — slow loop writes only to a separate `_LIVE_SLOW` dict; rankings merge both
  5. SSE drops are logged with a rate counter (not silently discarded)

**Plans:** 1 plan

Plans:

- [ ] 04-01-PLAN.md — Persistent OBI (_OBI_INDS), offload _ingest_batch via asyncio.to_thread, split _LIVE_FAST/_LIVE_SLOW with merge-on-read, log SSE drops (4 tasks)
