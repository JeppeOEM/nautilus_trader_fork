# Bybit Data Collector

## What This Is

A Python live data collector built on NautilusTrader's `TradingNode`/`Strategy` framework that connects to Bybit (linear perpetuals USDT + spot), subscribes to a configurable list of instruments, and records the full breadth of available market data — trades, quotes, order book depth, bars/klines, funding rate, and open interest — to disk using Nautilus's official `ParquetDataCatalog` format, partitioned by day. Runs 24/7 as a systemd service for ongoing data archival, usable for both backtesting research and live strategy development.

## Core Value

Reliable, continuous capture of Bybit market data into a Nautilus-catalog-compatible parquet archive — so the data can be loaded directly into backtests or analyzed in pandas, with no data loss across restarts/disconnects.

## Current Milestone: v1.1 dYdX Data Collector

**Goal:** Extend the recorder to capture dYdX perpetuals market data into the same `ParquetDataCatalog`, sharing infrastructure with the Bybit recorder.

**Target features:**
- Config-driven dYdX recorder (perpetual instruments, depth, bar intervals)
- Record trades, quotes (synthesized top-of-book), order book deltas, bars, funding rate, mark/index price for configured dYdX perpetuals
- Extract shared exchange-agnostic infra (catalog conversion, hot-reload, heartbeat/reliability) into a common module reused by both recorders
- 24/7 reliability parity with Bybit recorder (reconnect, graceful shutdown, stale-stream heartbeats)

**Out of scope for v1.1:** dYdX spot (doesn't exist on dYdX v4), open interest (not supported by dYdX adapter)

## Requirements

### Validated

- [x] Collector reconnects automatically and resumes recording after disconnects (24/7 reliability) — Validated in Phase 3: Reliability for 24/7 Operation (graceful SIGTERM flush/convert, adapter-driven reconnect, heartbeat/stale-stream warnings, restart-gap logging)

### Active

- [x] User can configure which instruments to collect (linear perpetuals USDT + spot symbols, explicit list)
- [x] Collector subscribes to and records trade ticks for each configured instrument
- [x] Collector subscribes to and records quote ticks (best bid/ask) for each configured instrument
- [x] Collector subscribes to and records order book depth (deltas) for each configured instrument
- [x] Collector subscribes to and records bars/klines (standard intervals) for each configured instrument
- [x] Collector subscribes to and records funding rate updates for linear perpetuals
- [ ] Collector subscribes to and records open interest for linear perpetuals
- [x] All recorded data is written using the official `ParquetDataCatalog` format, partitioned by day
- [ ] A pandas-based utility can load and view slices of the catalog for data inspection
- [ ] systemd unit file provided for process supervision with auto-restart
- [ ] Detailed deployment/run guide documenting setup, configuration, starting/stopping, and monitoring via journald
- [ ] Shared exchange-agnostic recorder infra (catalog conversion, hot-reload, heartbeat/reliability) extracted into a common module
- [ ] dYdX recorder records trades, quotes, order book deltas, bars, funding rate, and mark/index price for configured perpetual instruments
- [ ] dYdX recorder has 24/7 reliability parity with the Bybit recorder (reconnect, graceful shutdown, stale-stream heartbeats)

### Out of Scope

- Historical backfill — v1 is live-streaming only; backfilling gaps is a future enhancement
- Redis-backed message bus / multi-process pipeline — single-process collector is sufficient for the target instrument count; revisit only if scaling beyond one Bybit WS connection's topic limits
- Options market data — only linear perpetuals (USDT) and spot are in scope
- Inverse perpetuals — only linear perpetuals (USDT) and spot are in scope
- Auto-discovery / "all instruments" / top-N selection — user provides an explicit configurable instrument list
- dYdX spot market data — dYdX v4 has no spot market
- dYdX open interest — not supported by the existing dYdX adapter's data client

## Context

- This is a fork of NautilusTrader (Rust-native, multi-asset, multi-venue trading engine with Python bindings)
- A Bybit adapter already exists at `crates/adapters/bybit` (WebSocket protocol, API key/secret auth)
- A dYdX adapter already exists at `nautilus_trader/adapters/dydx/` (data client supports trades, quotes synthesized from top-of-book, order book deltas, bars, funding rates, mark/index prices; no open interest)
- A similar pattern exists at `examples/live/bybit/bybit_options_data_collector.py` — a `Strategy`-based data collector for Bybit options/spot, but it writes custom pandas-based parquet files rather than the official `ParquetDataCatalog` format. This project follows the same general strategy-based approach but targets linear perps + spot, captures more data types, and writes catalog-compatible parquet.
- Bybit's WebSocket allows multiplexing many topic subscriptions per connection; the adapter manages connection/topic allocation internally, so a single process can handle 10+ instruments across multiple data types.

## Constraints

- **Tech stack**: Python script using Nautilus `TradingNode` + `Strategy` + existing Bybit adapter — no new Rust code required
- **Storage format**: Official Nautilus `ParquetDataCatalog` (arrow/parquet), partitioned by day, so data can be loaded directly into Nautilus backtests
- **Runtime**: Linux, run via systemd (`Restart=always`) for 24/7 supervision; journald + Nautilus `LoggingConfig` file logging for observability
- **No Redis**: Single-process design; in-memory message bus and cache are sufficient

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| No Redis / single process | A single Bybit data client can multiplex 10+ instrument subscriptions; Redis only helps multi-process pub/sub or persisted cache state, neither needed here | — Pending |
| Official `ParquetDataCatalog` format (not custom pandas parquet) | Lets the recorded data be reused directly in Nautilus backtests later, unlike the existing example's custom format | — Pending |
| Persistence via `StreamingConfig` + `StreamingFeatherWriter` + `catalog.convert_stream_to_data()` (not a hand-rolled `CatalogWriter`) | User preference: always leverage existing Nautilus framework functionality over custom code, even where the framework path is less individually verified for edge cases — extend later only if it proves insufficient | — Pending |
| Partition by day, retain everything | Keeps disk management simple; old partitions can be manually archived/deleted later if disk fills up | — Pending |
| systemd for process supervision | Low-effort on Linux; journald captures crash/restart logs automatically, Nautilus file logging covers detailed traces | — Pending |
| Live-streaming only for v1 | Keeps scope focused; historical backfill deferred | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-15 after starting milestone v1.1 (dYdX Data Collector)*
