# Roadmap: Bybit Data Collector

## Overview

This roadmap delivers a 24/7 Bybit market-data recorder as a vertical MVP: first prove the entire pipeline end-to-end with a single data type (config → subscribe → `StreamingConfig` → daily catalog conversion), then widen to all data types, harden reliability for unattended operation, tackle the one no-paved-path requirement (open interest) in isolation, and finish with the operator-facing inspection and deployment artifacts. Each phase leaves a runnable, verifiable collector — never plumbing that only works once everything is wired up at the end.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Bootstrap, Config & End-to-End Slice** - Config-driven node records trades for one instrument all the way into the day-partitioned catalog (completed 2026-06-13)
- [x] **Phase 2: Full Data-Type Coverage** - Quotes, order-book deltas, bars, funding, and mark/index price recorded across all configured instruments (completed 2026-06-14)
- [x] **Phase 3: Reliability for 24/7 Operation** - Graceful shutdown flush, stale-stream heartbeats, and adapter-driven reconnect resilience (completed 2026-06-14)
- [ ] **Phase 4: Open Interest Spike** - Custom open-interest `Data` type recorded into the catalog via Arrow registration
- [ ] **Phase 5: Inspection & Deployment** - Pandas catalog-inspection utility, systemd unit, and deployment guide

## Phase Details

### Phase 1: Bootstrap, Config & End-to-End Slice

**Goal**: A config-driven `TradingNode` connects to Bybit, validates the configured instruments, records trade ticks for at least one instrument via `StreamingConfig`, and a scheduled conversion lands that data in the day-partitioned `ParquetDataCatalog`.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: CONF-01, CONF-02, CONF-03, REC-01, REC-07, REL-01
**Success Criteria** (what must be TRUE):

  1. User can list instruments (linear-USDT + spot) plus per-instrument depth and bar intervals in a TOML config file and the recorder loads them
  2. On startup the recorder validates every configured instrument against Bybit's instrument cache and fails fast with a clear error naming any missing instrument
  3. With the recorder running, trade ticks for a configured instrument are persisted via Nautilus `StreamingConfig`/`StreamingFeatherWriter` (no custom writer)
  4. A scheduled conversion step turns the streamed feather data into the official `ParquetDataCatalog`, partitioned by UTC day
  5. The resulting catalog can be opened by Nautilus and the recorded trades load back as native trade tick objects

**Plans**: TBD

### Phase 2: Full Data-Type Coverage

**Goal**: The recorder subscribes to and records the remaining market-data types — quotes, order-book deltas (per configured depth), bars (per configured intervals), funding rate, and mark/index price — for every configured instrument, all flowing through the proven Phase 1 streaming-to-catalog pipeline.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: REC-02, REC-03, REC-04, REC-05, REC-06
**Success Criteria** (what must be TRUE):

  1. Quote ticks (best bid/ask) are recorded for each configured instrument
  2. Order-book deltas are recorded at the per-instrument configured depth, respecting per-product-type depth limits (spot capped at 50)
  3. Bars/klines are recorded for each configured instrument at every configured interval
  4. Funding-rate updates are recorded for linear perpetuals without flooding the catalog with redundant rows (deduped to actual changes)
  5. Mark price and index price updates are recorded for linear perpetuals

**Plans**: 2 plans
**Wave 1**

- [x] 02-01-PLAN.md — Record quotes, order-book deltas, bars, and mark/index prices (5 auto-written types) with fail-fast depth validation

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 02-02-PLAN.md — Funding-rate dedup (linear-only) with empirical persistence spike, recorder wiring, and all-six-feeds live smoke

### Phase 3: Reliability for 24/7 Operation

**Goal**: The recorder survives unattended `Restart=always` operation: it flushes buffered data on SIGTERM so restarts lose no recent data, leans entirely on the adapter's built-in reconnect/resubscribe, and surfaces stale streams via heartbeat logging.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: REL-02, REL-03, REL-04
**Success Criteria** (what must be TRUE):

  1. On SIGTERM the recorder flushes/converts buffered data before exiting, and a stop-then-start in the same UTC day produces no data loss and no disjoint-interval errors
  2. After a forced WebSocket disconnect the recorder resumes recording automatically with no custom reconnect code (adapter-driven resubscribe)
  3. The recorder logs per-stream heartbeats and emits a warning when any subscribed stream goes quiet beyond its expected threshold

**Plans**: 3 plans

**Wave 1**

- [x] 03-01-PLAN.md — Graceful-shutdown flush+convert via on_stop (REL-02) and adapter-driven reconnect proof (REL-04)

**Wave 2** *(blocked on Wave 1 completion — shares strategy.py)*

- [x] 03-02-PLAN.md — Per-stream heartbeat + stale-stream WARNING with configurable per-type thresholds (REL-03)

**Wave 3** *(gap closure — blocked on Wave 1/2; shares strategy.py + config.py)*

- [ ] 03-03-PLAN.md — Guard _run_conversion() catalog/flush (CR-01, REL-02) + fail-fast restart_gap_threshold validation (WR-01) + reconcile REQUIREMENTS.md

### Phase 4: Open Interest Spike

**Goal**: Open interest for linear perpetuals is recorded into the catalog via a custom Nautilus `Data` subclass with Arrow serializer registration, slotted into the existing buffer/streaming machinery — isolated because there is no native framework support.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: OI-01
**Success Criteria** (what must be TRUE):

  1. A custom open-interest `Data` type is defined and registered with the Arrow serializer so it round-trips through the catalog
  2. Open-interest values for linear perpetuals are captured (from the linear ticker or REST polling) and recorded for each configured linear instrument
  3. Recorded open-interest data loads back from the catalog as the custom `Data` type for inspection

**Plans**: TBD

### Phase 5: Inspection & Deployment

**Goal**: Operator-facing deliverables: a pandas-based utility to load and query catalog slices, a systemd unit for 24/7 supervision, and a deployment guide covering install, config, secrets, run/stop, and journald monitoring.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: OPS-01, OPS-02, OPS-03
**Success Criteria** (what must be TRUE):

  1. A user can run the pandas-based utility to load and query catalog slices filtered by instrument, data type, and time range
  2. A systemd unit file is provided that supervises the recorder with `Restart=always` and an absolute venv path
  3. A deployment guide documents installation, TOML configuration, API-key/secrets setup, starting/stopping the service, and monitoring via journald and Nautilus file logs
  4. Following the guide on a clean Linux host brings up a running, recording service

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Bootstrap, Config & End-to-End Slice | 4/4 | Complete   | 2026-06-13 |
| 2. Full Data-Type Coverage | 1/2 | In Progress|  |
| 3. Reliability for 24/7 Operation | 2/3 | In Progress | 2026-06-14 |
| 4. Open Interest Spike | 0/TBD | Not started | - |
| 5. Inspection & Deployment | 0/TBD | Not started | - |
