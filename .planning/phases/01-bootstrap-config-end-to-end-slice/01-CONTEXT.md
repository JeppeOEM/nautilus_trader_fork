# Phase 1: Bootstrap, Config & End-to-End Slice - Context

**Gathered:** 2026-06-13
**Status:** Ready for planning

<domain>
## Phase Boundary

A config-driven `TradingNode` connects to Bybit (mainnet), validates configured instruments against the instrument cache, records trade ticks for at least one configured instrument via `StreamingConfig`/`StreamingFeatherWriter`, and a scheduled in-process conversion turns that streamed feather data into the official day-partitioned `ParquetDataCatalog`. This phase proves the entire pipeline end-to-end with a single data type (trades) before Phase 2 widens to all data types.

</domain>

<decisions>
## Implementation Decisions

### Conversion Scheduling
- **D-01:** The feather→`ParquetDataCatalog` conversion runs in-process, triggered by a Nautilus clock timer (`clock.set_timer`) inside the Strategy — no separate cron/systemd-timer process for Phase 1 (single-process constraint).
- **D-02:** The conversion interval is configurable via a `conversion_interval_minutes` field in the `[recorder]` config section (not hardcoded), so it can be tuned per environment.
- **D-03:** The recorder uses a fixed `instance_id` (e.g. derived from a constant `trader_id`/instance name, not timestamped per run) so the streaming feather directory is stable across restarts — required for Phase 3's restart-without-data-loss goal.
- **D-04:** Each scheduled run calls `catalog.convert_stream_to_data()` over all available feather data (including the current/partial UTC day) — no special-casing of "today's incomplete partition." Rely on the catalog's `write_data` de-dup/disjoint-interval handling rather than custom partial-day logic.

### Validation Failure Behavior
- **D-05:** Instrument validation happens in `on_start`, after `InstrumentProviderConfig` has loaded the cache but before any subscriptions are issued. If any configured instrument is missing from `self.cache.instruments()`, the strategy raises (does not silently `stop()`), producing a non-zero exit so systemd `Restart=always` surfaces the failure in journald rather than masking it as a clean exit.
- **D-06:** The error message lists ALL missing instrument IDs in one message (e.g. `"Missing instruments: BTCUSDT-LINEAR.BYBIT, ETHUSDT-SPOT.BYBIT"`) — collect all missing IDs before raising, don't fail on the first one.

### TOML Config Schema
- **D-07:** Per-instrument settings use array-of-tables: `[[instruments.linear]]` and `[[instruments.spot]]`, each entry with `id`, `depth`, `bar_intervals` (and room for future per-instrument fields like funding/OI flags in later phases). Linear and spot are kept as separate sections (not a single unified list with inferred product type).
- **D-08:** Recorder-wide settings (`trader_id`, `catalog_path`, `streaming_path`, `conversion_interval_minutes`, `environment`) live in a single top-level `[recorder]` table, separate from the instrument arrays.
- **D-09:** Bybit API credentials are NOT read from the TOML at all — sourced exclusively via the adapter's existing env var convention (`BYBIT_API_KEY`/`BYBIT_API_SECRET` or `BYBIT_TESTNET_*`). No `api_key`/`api_secret` fields in config.

### Phase 1 Slice Scope
- **D-10:** The end-to-end proof uses Bybit **mainnet** with one liquid linear perpetual (e.g. `BTCUSDT-LINEAR.BYBIT`) — guarantees continuous real trade-tick flow for fast verification; public market-data subscription needs no API keys.

### Claude's Discretion
- Exact `[recorder]` field names beyond those listed above, internal module/file layout for the collector script, and the precise structure of the array-of-tables entries (additional optional fields) are left to planning/implementation as long as the decisions above hold.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `.planning/PROJECT.md` — core value, constraints (no Redis, single process, official ParquetDataCatalog, systemd), key decisions table
- `.planning/REQUIREMENTS.md` — CONF-01/02/03, REC-01, REC-07, REL-01 (this phase's requirement set)
- `.planning/ROADMAP.md` — Phase 1 goal and success criteria

### Reference implementation
- `examples/live/bybit/bybit_options_data_collector.py` — existing Strategy-based Bybit data-collector pattern (TradingNode setup, InstrumentProviderConfig, subscription patterns, file logging via LoggingConfig) — NOT the persistence approach (uses custom pandas parquet, not `ParquetDataCatalog`/`StreamingConfig`)

### Persistence API
- `nautilus_trader/persistence/config.py` — `StreamingConfig` definition
- `nautilus_trader/persistence/catalog/parquet.py` (`convert_stream_to_data`, around line 2523) — feather→parquet conversion method signature and behavior

### Bybit adapter config
- `nautilus_trader/adapters/bybit/config.py` — `BybitDataClientConfig` (env var credential sourcing: `BYBIT_API_KEY`/`BYBIT_API_SECRET`/`BYBIT_TESTNET_*`, `BybitEnvironment`, `BybitProductType`)

No external ADRs/specs beyond PROJECT.md/REQUIREMENTS.md/ROADMAP.md exist for this milestone.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `examples/live/bybit/bybit_options_data_collector.py`: TradingNode bootstrap pattern (`TradingNodeConfig`, `LoggingConfig`, `BybitDataClientConfig`, `BybitLiveDataClientFactory` registration, `node.build()`/`node.run()`) — reuse the node/strategy wiring, replace the custom pandas-parquet storage with `StreamingConfig` + `ParquetDataCatalog`.
- `nautilus_trader/adapters/bybit/config.py`: `BybitDataClientConfig` already supports `environment` (MAINNET/TESTNET/DEMO), `product_types`, and env-var credential sourcing — no new credential-handling code needed.

### Established Patterns
- Strategy validates/discovers instruments via `self.cache.instruments()` in `on_start()` (populated by `InstrumentProviderConfig`), then subscribes — same pattern applies here for CONF-03 validation (D-05).
- `clock.set_timer` / `clock.set_time_alert_ns` (in `nautilus_trader/trading/strategy.pyx` and `nautilus_trader/common/component.pyx`) provide the in-process scheduling primitive for D-01.

### Integration Points
- New collector script lives alongside other Bybit examples (likely a new top-level script/package, not modifying `examples/live/bybit/` in place) — exact location is planner's discretion.
- `TradingNodeConfig.streaming` (a `StreamingConfig`) wires the feather writer; `ParquetDataCatalog(catalog_path)` + `.convert_stream_to_data(instance_id, data_cls=TradeTick, ...)` performs the scheduled conversion (D-01/D-04).

</code_context>

<specifics>
## Specific Ideas

- TOML structure example confirmed during discussion:
  ```toml
  [recorder]
  trader_id = "BYBIT-COLLECTOR-001"
  catalog_path = "catalog"
  streaming_path = "catalog/streaming"
  conversion_interval_minutes = 60
  environment = "mainnet"

  [[instruments.linear]]
  id = "BTCUSDT-LINEAR.BYBIT"
  depth = 50
  bar_intervals = ["1-MINUTE"]

  [[instruments.spot]]
  id = "ETHUSDT-SPOT.BYBIT"
  depth = 50
  bar_intervals = ["1-MINUTE"]
  ```
  (Phase 1 only needs `id` consumed for trade-tick recording + validation; `depth`/`bar_intervals` fields should exist in the schema now since Phase 2 will read them, but are not acted on yet.)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 1-Bootstrap, Config & End-to-End Slice*
*Context gathered: 2026-06-13*
