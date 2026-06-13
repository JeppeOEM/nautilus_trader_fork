# Walking Skeleton — Bybit Data Collector

**Phase:** 1
**Generated:** 2026-06-13

## Capability Proven End-to-End

A config-driven `TradingNode` connects to Bybit mainnet, validates the TOML-configured instruments against the loaded instrument cache (failing fast if any are missing), records trade ticks for at least one configured instrument through Nautilus's native `StreamingFeatherWriter`, and an in-process clock timer converts that streamed feather data into the official day-partitioned `ParquetDataCatalog` — which then reloads as native `TradeTick` objects.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Application shape | Single-process Python entrypoint script wrapping a `TradingNode` + one `Strategy` | Project constraint: single-process, no Redis; adapter multiplexes LINEAR + SPOT over one WS connection |
| Package location | `scripts/bybit_recorder/` (`recorder.py`, `config.py`, `strategy.py`, `recorder.toml`) | RESEARCH recommended layout; keeps the new collector out of `examples/live/bybit/` (which is reference-only) and out of repo tooling under top-level `scripts/*.sh` |
| Config format | TOML parsed with stdlib `tomllib` (binary mode) into frozen dataclasses | Zero new dependency; read-only config; Python ≥3.12 confirmed (`pyproject.toml:25`) |
| Config schema | Top-level `[recorder]` table + `[[instruments.linear]]` / `[[instruments.spot]]` array-of-tables (D-07, D-08) | Linear and spot kept separate; per-instrument `id`/`depth`/`bar_intervals` (depth + bar_intervals parsed now, consumed in Phase 2) |
| Credentials | Env vars only (`BYBIT_API_KEY`/`BYBIT_API_SECRET`/`BYBIT_TESTNET_*`), never in TOML (D-09) | Public market-data subscription needs no keys on mainnet (D-10); avoids secret leakage |
| Persistence | Native `StreamingConfig` → `StreamingFeatherWriter` (kernel auto-wires `"*"` subscription); no custom writer (REC-07) | Project mandate "prefer Nautilus built-ins"; Arrow-schema-correct, catalog-compatible |
| Day partitioning | `StreamingConfig(rotation_mode=RotationMode.SCHEDULED_DATES, rotation_interval=pd.Timedelta(days=1), rotation_time=time(0,0,0), rotation_timezone="UTC")` | Catalog "day partitioning" is a consequence of daily feather rotation, NOT a free catalog property (RESEARCH Pitfall 1 / A1). `SCHEDULED_DATES` pins files to UTC midnight |
| `instance_id` | Hardcoded valid RFC-4122 **v4 UUID** constant shared by node config + strategy config (D-03) | `UUID4.from_str` rejects human labels like "BYBIT-COLLECTOR-001" (RESEARCH Pitfall 4). D-03's "derived from trader_id" is technically impossible — a fixed UUID4 constant gives the stable feather directory required for Phase 3 |
| Conversion scheduling | In-process `self.clock.set_timer(...)` callback inside the Strategy calling `ParquetDataCatalog(...).convert_stream_to_data(instance_id, TradeTick, subdirectory="live")` (D-01, D-04, REL-01) | Single-process constraint; integrates with the single-threaded event loop. `subdirectory="live"` is mandatory (default is `"backtest"` — Pitfall 3) |
| Streaming vs catalog path | `StreamingConfig.catalog_path` and the conversion `ParquetDataCatalog(path)` MUST share one root so conversion finds the feather files under `{root}/live/{instance_id}/` (RESEARCH Pitfall 5 / A4) | Phase 1 reconciles `streaming_path` and `catalog_path` to a single shared root; feather lands at `{root}/live/{instance_id}/`, parquet at `{root}/data/...` |

## Stack Touched in Phase 1

- [x] Project scaffold (new `scripts/bybit_recorder/` package: entrypoint + config + strategy + example TOML + pytest tests)
- [x] Routing equivalent — `TradingNode` + Bybit `LiveDataClientFactory` connecting to the live mainnet WS and routing trades through the DataEngine/MessageBus
- [x] One real write — trade ticks persisted to feather via `StreamingFeatherWriter`, then converted to parquet in the catalog
- [x] One real read — `catalog.trade_ticks(instrument_ids=[...])` reloads native `TradeTick` objects (success criterion 5)
- [x] Documented local full-stack run command — `python scripts/bybit_recorder/recorder.py scripts/bybit_recorder/recorder.toml` (E2E smoke against `BTCUSDT-LINEAR.BYBIT`)

## Out of Scope (Deferred to Later Slices)

- Quotes, order-book deltas, bars, funding rate, mark/index price (Phase 2 — REC-02..06) — `depth`/`bar_intervals` fields exist in the schema now but are NOT acted on in Phase 1
- Graceful SIGTERM flush, stale-stream heartbeats, reconnect resilience (Phase 3 — REL-02..04)
- Open interest custom `Data` type + Arrow registration (Phase 4 — OI-01)
- Pandas inspection utility, systemd unit, deployment guide (Phase 5 — OPS-01..03)
- Any custom reconnect / cron / systemd-timer process (conversion stays in-process for Phase 1 per D-01)

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of this skeleton without altering its architectural decisions (config shape, streaming-to-catalog pipeline, fixed `instance_id`, in-process timer):

- Phase 2: Widen recording to quotes, order-book deltas (per `depth`), bars (per `bar_intervals`), funding rate, mark/index price — all flowing through the proven Phase 1 streaming-to-catalog pipeline
- Phase 3: Survive unattended `Restart=always` — SIGTERM flush, adapter-driven reconnect, per-stream heartbeat logging (relies on the stable `instance_id` from this skeleton)
- Phase 4: Custom open-interest `Data` subclass with Arrow serializer registration, slotted into the existing buffer/streaming machinery
- Phase 5: Pandas catalog-inspection utility, systemd unit (`Restart=always`), deployment guide
