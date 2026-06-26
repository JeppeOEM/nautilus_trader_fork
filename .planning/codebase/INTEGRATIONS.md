# External Integrations

**Analysis Date:** 2026-06-26

## APIs & External Services

**dYdX Market Data (Primary Integration):**
- dYdX Chain REST Indexer API - Market data, instruments, perpetual markets, open interest
  - SDK/Client: Rust-native PyO3 bindings (`nautilus_pyo3.DydxHttpClient`, `nautilus_pyo3.DydxWebSocketClient`)
  - Endpoint: Dynamic via `get_dydx_http_url(network)` and `get_dydx_ws_url(network)`
  - Network configuration: `DydxNetwork` enum (MAINNET, TESTNET)
  - Auth: Public endpoints, no API key required
  - Implementation: `troll/dydx_collector/client.py` (thin wrapper around PyO3 bindings)
  - Data types: TradeTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate, FundingRateUpdate, InstrumentStatus
  - Rate limits: 2/sec WebSocket subscribe limit (built-in throttle via Rust client)

**dYdX Open Interest (Supplementary):**
- dYdX public indexer REST endpoint (`/v4/perpetualMarkets`)
  - SDK/Client: Python stdlib `urllib.request` (no external dependency needed)
  - Endpoint: `{get_dydx_http_url(network)}/v4/perpetualMarkets`
  - Auth: Public endpoint, requires User-Agent header (dYdX rejects stdlib default)
  - Fetched via: `troll/dydx_collector/open_interest.py::fetch_open_interest()`
  - Frequency: Configurable poll interval (default 300 seconds via `open_interest_poll_seconds`)
  - Custom data type: `DydxOpenInterest(Data)` with Arrow/Parquet serialization registration
  - Reason: Open interest dropped by Rust/PyO3 bindings on both REST and WebSocket paths (parsing-level bug)

## Data Storage

**Databases:**
- None in production (collector is stateless)
- Optional PostgreSQL 12+ via nautilus_trader adapters (for event store, not collector)
  - Connection: Environment variable config (future integration)
  - Client: SQLx 0.9.0 (async, compile-time query checking)

**File Storage:**
- ParquetDataCatalog (Local filesystem only)
  - Location: Configurable via `config.toml` `catalog_path` setting (default: `./catalog`)
  - Format: Parquet columnar files with Arrow schema and metadata
  - Partitioning: Per-data-type, per-instrument, per-date
  - Client: `nautilus_trader.persistence.catalog.ParquetDataCatalog`
  - Lifecycle: Continuous appends during collection, prunable via `troll/dydx_collector/prune_catalog.py`
  - Custom types registered: `DydxOpenInterest` with make_dict_serializer/make_dict_deserializer

**Metrics Storage (ml_signals):**
- SQLite 3 (embedded)
  - Location: `metrics.db` (created/managed in-process)
  - Schema: `snapshots` table with rolling 31-day retention
  - Purpose: Store rolling metric snapshots (price, pct_1h, pct_24h, volatility, ofi, microprice, spread)
  - Client: Python stdlib `sqlite3`
  - Configuration: WAL mode, thread-safe via lock (`metrics_store.py`)
  - Lifecycle: Auto-pruned on every write (rows older than `retain_days`)

**Caching:**
- None (collector is stateless, no Redis integration)
- In-memory only: defaultdict buffer in collector flushed periodically to Parquet

## Authentication & Identity

**Auth Provider:**
- None required (all integrations are public endpoints)
- dYdX: Public WebSocket and REST indexer (no authentication)
- Custom credentials management: Via environment variables (for optional future integrations)

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry/Rollbar integration)
- Local logging via Python `logging` module
- Log level: INFO (configurable in collector main)

**Logs:**
- Python `logging` module with basicConfig
- Output: STDOUT (captured by Docker and viewable via Dozzle)
- Dozzle container: `amir20/dozzle:latest` on port 127.0.0.1:8080 (optional log viewer)
- Patterns: Exception logging with `logger.exception()`, info logging for subscriptions/unsubscriptions

**Tracing:**
- Rust: `tracing` crate (optional, for future instrumentation)
- Python collector: Not integrated

## CI/CD & Deployment

**Hosting:**
- Docker containers (Docker Compose orchestration)
- Base image: `nautilus-trader-base:1.229.0` (rebuilt only on core nautilus_trader changes)
- Collector image: `troll/dydx_collector/collector.dockerfile` (thin layer, rebuilds in seconds)

**CI Pipeline:**
- None (collector is a standalone service, not part of main CI)
- Pre-commit hooks: Rust linting (clippy), Python linting (ruff), formatting checks
- Manual deployment: `make up` (build and start), `make down`, `make logs`

**Build Process:**
- Docker multi-stage: rust-toolchain → builder → application
- UV lock ensures reproducible Python environment
- Cargo release profile for optimized Rust code
- Output: Containerized application running on Python 3.13 with compiled Rust extensions

## Environment Configuration

**Required env vars (for Docker runtime):**
- None (all configuration in `config.toml`)
- Optional future additions: dYdX network override, catalog path override

**Optional env vars (Docker base image defaults):**
- `PYTHONUNBUFFERED=1` - Ensures logs flush immediately
- `PYTHONDONTWRITEBYTECODE=1` - Skip bytecode caching
- `PYO3_PYTHON=/usr/local/bin/python3` - PyO3 Python location
- `BUILD_MODE=release` - Rust optimization profile
- `CC=clang` - C compiler (build-time only)

**Configuration file:**
- `troll/dydx_collector/config.toml`:
  - `network` - dYdX network (mainnet/testnet, string, default: "mainnet")
  - `catalog_path` - ParquetDataCatalog root directory (string, default: "catalog")
  - `flush_interval_seconds` - How often to flush in-memory buffer to Parquet (int, default: 60)
  - `config_reload_seconds` - How often to check config.toml for instrument changes (int, default: 30)
  - `open_interest_poll_seconds` - How often to fetch open interest (int, default: 300)
  - `[[instruments]]` - Optional list of specific instruments to collect (id, bar_intervals)
    - Empty list = auto-subscribe to all dYdX perpetuals

**Secrets location:**
- Not applicable (all integrations public)
- Future: Use environment variables or mounted secrets files for optional integrations

## Webhooks & Callbacks

**Incoming:**
- None (collector is pull-based, not event-driven from external webhooks)

**Outgoing:**
- None (collector writes only to local ParquetDataCatalog)

**WebSocket Connections (Bidirectional Streaming):**
- dYdX public WebSocket (inbound only):
  - Connection: `nautilus_pyo3.DydxWebSocketClient` (Rust-backed)
  - Subscriptions: Trades, order book deltas, bars, market status updates
  - Heartbeat: 20 seconds (built-in, configurable in client constructor)
  - Reconnection: Automatic via Rust client's built-in reconnect logic
  - Rate limiting: 2 subscriptions per second (dYdX-enforced, transparent to collector)

## Data Pipeline

**Collector Input Path:**
1. `DydxHttpClient.request()` - Fetch instruments at startup
2. `DydxWebSocketClient.subscribe()` - Market data stream inbound
3. `DydxOpenInterest.fetch_open_interest()` - Poll REST endpoint every `open_interest_poll_seconds`

**Collector Processing:**
- Callback-based: `on_data(data)` receives decoded data (O(1) operation)
- Buffering: In-memory `defaultdict(list)` grouped by (data_type, identifier)
- Flushing: Periodic flush every `flush_interval_seconds` via `ParquetDataCatalog.write_data()`

**Catalog Output:**
- `ParquetDataCatalog.write_data(items)` - Atomic batch write to Parquet
- Partitioning: By data type, instrument ID, and date
- Schema: Arrow schema with metadata (type labels, precision)

## ml_signals Dependencies

**Catalog Reading:**
- `ParquetDataCatalog.trade_ticks()` - Fetch trade data
- `ParquetDataCatalog.instruments()` - Fetch instrument definitions
- Streaming via `BacktestDataConfig` (preferred over loading entire catalog into memory)

**Data Analysis:**
- pandas - DataFrame operations, time-series aggregation
- numpy - Numerical operations
- plotly - Interactive charts (dashboard)

**Backtest Engine:**
- `BacktestNode` - High-level backtest runner
- `BacktestRunConfig` - Backtest configuration
- `ImportableStrategyConfig` - Strategy loader by string path
- `BacktestDataConfig` - Streaming data configuration (avoids full load)

**Dashboard:**
- Python stdlib `http.server.HTTPServer` - Lightweight polling server
- plotly.to_html() - Generate interactive charts
- Port: 8765 (default, configurable)
- No client-side JS, no WebSocket, no push notifications (polling-based)

## Supply Chain / Security

**Dependency Pinning:**
- Python: `uv.lock` (committed, blocks unexpected version changes)
- Rust: `Cargo.lock` (committed, reproducible builds)
- Build tools: Pinned versions in `pyproject.toml` (poetry-core, cython, setuptools)
- UV: Required version ==0.11.21 (enforced in pyproject.toml)

**Pre-commit Hooks (via `.pre-commit-config.yaml`):**
- Rust formatting: `cargo fmt`
- Python formatting: `ruff format`
- Rust linting: `cargo clippy`
- Python linting: `ruff`, `mypy`
- Copyright headers: Year validation
- DST conventions: Seeded RNG, no direct OS RNG
- PyO3 naming: Type alignment between Rust and Python
- Anyhow usage: Error context propagation

**License Compliance:**
- LGPL-3.0-or-later (all source files include header)
- Enforced via pre-commit hook: `check-copyright-year`

---

*Integration audit: 2026-06-26*
