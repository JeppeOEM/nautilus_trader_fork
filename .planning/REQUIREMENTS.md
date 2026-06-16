# Requirements: Bybit Data Collector

**Defined:** 2026-06-13
**Core Value:** Reliable, continuous capture of Bybit market data into a Nautilus-catalog-compatible parquet archive — usable for both backtesting and live strategy research, with no data loss across restarts/disconnects.

## v1 Requirements

### Configuration

- [ ] **CONF-01**: User can configure which Bybit instruments to record (linear perpetuals USDT + spot symbols) via a TOML config file
- [ ] **CONF-02**: User can configure per-instrument order book depth and bar intervals via the TOML config file
- [ ] **CONF-03**: Recorder validates all configured instruments against Bybit's instrument cache on startup and fails fast with a clear error if any are missing

### Recording

- [ ] **REC-01**: Recorder subscribes to and records trade ticks for each configured instrument
- [ ] **REC-02**: Recorder subscribes to and records quote ticks (best bid/ask) for each configured instrument
- [ ] **REC-03**: Recorder subscribes to and records order book deltas (per configured depth) for each configured instrument
- [ ] **REC-04**: Recorder subscribes to and records bars/klines (per configured intervals) for each configured instrument
- [ ] **REC-05**: Recorder subscribes to and records funding rate updates for linear perpetuals
- [ ] **REC-06**: Recorder subscribes to and records mark price and index price updates for linear perpetuals
- [ ] **REC-07**: All recorded data is persisted via Nautilus's `StreamingConfig`/`StreamingFeatherWriter` (not custom writers)

### Reliability

- [x] **REL-01**: A scheduled job converts streamed data into the partitioned `ParquetDataCatalog` format, partitioned by day
- [x] **REL-02**: On shutdown (SIGTERM), the recorder flushes/converts any buffered data before exiting, so a restart does not lose recent data
- [x] **REL-03**: Recorder logs per-stream heartbeats and warns if any subscribed stream goes quiet beyond an expected threshold
- [x] **REL-04**: Recorder relies entirely on the Bybit adapter's built-in WebSocket reconnect/resubscribe — no custom reconnect logic

### Open Interest (Spike)

- [ ] **OI-01**: Recorder records open interest for linear perpetuals via a custom Nautilus `Data` type with Arrow serializer registration, isolated as its own phase due to no native framework support

### Operations

- [ ] **OPS-01**: A pandas-based utility can load and query slices of the catalog (by instrument, data type, and time range) for ad-hoc inspection
- [ ] **OPS-02**: A systemd unit file is provided for 24/7 process supervision with `Restart=always`
- [ ] **OPS-03**: A deployment guide documents installation, TOML configuration, secrets/API key setup, starting/stopping the service, and monitoring via journald and Nautilus file logs

### Hot-Reload

- [x] **HOT-01**: While running, the recorder periodically detects changes to `recorder.toml`'s instrument list/params (additions, removals, depth/bar_interval changes) and applies them live — loading new instruments and subscribing/unsubscribing the affected feeds — without restarting the process or disrupting recording for unaffected instruments

## v1.1 Requirements — dYdX Data Collector

**Added:** 2026-06-16

### Shared Infra

- [ ] **DYDX-01**: Exchange-agnostic recorder infra (catalog conversion, heartbeat/stale-stream, graceful shutdown, hot-reload) extracted from `scripts/bybit_recorder/` into a `scripts/common_recorder/` module; `scripts/bybit_recorder/` updated to import from it with no behavior change

### dYdX Recorder Wiring

- [ ] **DYDX-02**: A new `scripts/dydx_recorder/` records dYdX perpetual market data using `DydxDataClientConfig` + `DydxLiveDataClientFactory` + `DydxNetwork` (with `CUSTOM_ENCODINGS` registration), TOML-driven instrument list, and `InstrumentProviderConfig(load_ids=...)` for startup instrument validation

### Data Types

- [ ] **DYDX-03**: dYdX recorder subscribes to and records trades, synthesized quotes (top-of-book), L2 order-book deltas (full-depth), bars, funding rate, mark price, and index price for each configured perpetual instrument into the shared `ParquetDataCatalog`

### dYdX-Specific Handling

- [ ] **DYDX-04**: Funding rate updates are deduplicated to actual rate changes before persisting — the dYdX adapter does not dedup, so the recorder must apply the existing Bybit dedup mechanism (verify it keys on instrument_id + rate and is exchange-agnostic)
- [ ] **DYDX-05**: Heartbeat/stale-stream thresholds for dYdX synthesized quote streams are relaxed vs. Bybit defaults — event-driven quotes go quiet on calm markets, so per-type thresholds or underlying book stream monitoring must prevent false stale-stream warnings
- [ ] **DYDX-06**: dYdX bar subscriptions validate configured intervals against the supported resolution set (`1-MINUTE`, `5-MINUTE`, `15-MINUTE`, `30-MINUTE`, `1-HOUR`, `4-HOUR`, `1-DAY`) at config load; unsupported intervals fail fast with a clear error before the node starts

### Reliability

- [ ] **DYDX-07**: dYdX recorder has 24/7 reliability parity with the Bybit recorder: graceful SIGTERM flush+convert, adapter-driven WebSocket reconnect/resubscribe, per-stream stale-stream heartbeat warnings, and restart-gap logging

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Historical Data

- **HIST-01**: Historical backfill of Bybit data for gap-filling and pre-recorder history

### Operations

- **OPS-04**: Disk-usage/retention sweep for old catalog partitions
- **OPS-05**: Reconnect/disconnect counters and explicit gap markers recorded in the catalog

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Redis-backed message bus / multi-process pipeline | Single-process collector handles the target instrument count; adapter multiplexes all subscriptions over one WS connection |
| Options market data | Only linear perpetuals (USDT) and spot are in scope |
| Inverse perpetuals | Only linear perpetuals (USDT) and spot are in scope |
| Auto-discovery / "all instruments" / top-N selection | User provides an explicit configurable instrument list (CONF-01) |
| Historical backfill (v1) | Live-streaming only for v1; deferred to v2 (HIST-01) |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONF-01 | Phase 1 | Pending |
| CONF-02 | Phase 1 | Pending |
| CONF-03 | Phase 1 | Pending |
| REC-01 | Phase 1 | Pending |
| REC-07 | Phase 1 | Pending |
| REL-01 | Phase 1 | Complete |
| REC-02 | Phase 2 | Pending |
| REC-03 | Phase 2 | Pending |
| REC-04 | Phase 2 | Pending |
| REC-05 | Phase 2 | Pending |
| REC-06 | Phase 2 | Pending |
| REL-02 | Phase 3 | Complete |
| REL-03 | Phase 3 | Complete |
| REL-04 | Phase 3 | Complete |
| OI-01 | Phase 4 | Pending |
| OPS-01 | Phase 5 | Pending |
| OPS-02 | Phase 5 | Pending |
| OPS-03 | Phase 5 | Pending |
| HOT-01 | Phase 6 | Complete |
| DYDX-01 | Phase 7 | Pending |
| DYDX-02 | Phase 7 | Pending |
| DYDX-03 | Phase 7 | Pending |
| DYDX-04 | Phase 7 | Pending |
| DYDX-05 | Phase 7 | Pending |
| DYDX-06 | Phase 7 | Pending |
| DYDX-07 | Phase 7 | Pending |

**Coverage:**

- v1 requirements: 19 total (12 complete, 7 pending)
- v1.1 requirements: 7 total (DYDX-01 through DYDX-07)
- Mapped to phases: 26 total
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-13*
*Last updated: 2026-06-13 after roadmap creation*
