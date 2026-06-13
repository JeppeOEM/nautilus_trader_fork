# External Integrations

**Analysis Date:** 2026-06-13

## APIs & External Services

**Cryptocurrency Exchanges:**
- **Binance** - Spot and futures trading
  - SDK/Client: Custom Rust adapter (`crates/adapters/binance`)
  - Protocol: WebSocket (native) + REST
  - Auth: API key/secret (environment variables)

- **Coinbase** - Spot trading
  - SDK/Client: Custom Rust adapter (`crates/adapters/coinbase`)
  - Protocol: WebSocket
  - Auth: API credentials

- **Kraken** - Spot trading
  - SDK/Client: Custom Rust adapter (`crates/adapters/kraken`)
  - Protocol: REST/WebSocket
  - Auth: API key/secret

- **Bybit** - Crypto derivatives
  - SDK/Client: Custom Rust adapter (`crates/adapters/bybit`)
  - Protocol: WebSocket
  - Auth: API credentials

- **OKX** - Crypto derivatives
  - SDK/Client: Custom Rust adapter (`crates/adapters/okx`)
  - Protocol: WebSocket
  - Auth: API credentials

- **Deribit** - Crypto options
  - SDK/Client: Custom Rust adapter (`crates/adapters/deribit`)
  - Protocol: WebSocket
  - Auth: API credentials

- **Bitmex** - Derivatives trading
  - SDK/Client: Custom Rust adapter (`crates/adapters/bitmex`)
  - Protocol: WebSocket
  - Auth: API key/secret

- **Hyperliquid** - Perpetual futures
  - SDK/Client: Custom Rust adapter (`crates/adapters/hyperliquid`)
  - Protocol: HTTP API
  - Auth: Private key signing

- **dYdX** - Perpetual derivatives
  - SDK/Client: Custom Rust adapter (`crates/adapters/dydx`)
  - Protocol: gRPC (via `tonic`)
  - Auth: Cosmos signing

- **Lighter** - Orderbook protocol
  - SDK/Client: Custom Rust adapter (`crates/adapters/lighter`)
  - Protocol: Custom binary protocol
  - Auth: Wallet-based

**Traditional Markets:**
- **Interactive Brokers** - Equities/options/futures
  - SDK/Client: `nautilus-ibapi` 3.0.1 (Python wrapper around C++ API)
  - Protocol: TWS API
  - Auth: Account credentials (localhost IB Gateway)

- **Betfair** - Sports betting
  - SDK/Client: `betfair-parser` 0.19.1 (data parsing) + custom adapter
  - Protocol: REST API
  - Auth: Session token

**Blockchain Networks:**
- **Ethereum/EVM** - Smart contract interactions
  - SDK/Client: `alloy` 2.0.5 (Ethereum SDK)
  - Protocol: JSON-RPC
  - Auth: Private keys (ED25519/ECDSA signing via Alloy)

- **Cosmos** - Cosmos chain interactions
  - SDK/Client: `cosmrs` 0.22.0
  - Protocol: gRPC
  - Auth: Cosmos signing

- **Polymarket** - Prediction markets
  - SDK/Client: `py-clob-client-v2` 1.0.1+ (Python optional dependency)
  - Protocol: REST API
  - Auth: API credentials

**Specialized Providers:**
- **Databento** - Market data
  - SDK/Client: `databento` 0.53.0 crate
  - Features: Historical + live market data feeds
  - Protocol: Binary + HTTP
  - Auth: API key

- **Tardis** - Historical market data
  - SDK/Client: Custom Rust adapter (`crates/adapters/tardis`)
  - Protocol: REST API
  - Auth: API key

- **Hypersync** - Blockchain event indexing
  - SDK/Client: `hypersync-client` 1.3.0
  - Protocol: HTTP API
  - Auth: API key

## Data Storage

**Databases:**
- **PostgreSQL 12+**
  - Connection: `sqlx` 0.9.0 async driver
  - Environment: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
  - Location: `crates/infrastructure/src/sql/pg.rs`
  - Features: Optional (feature flag: `postgres`)
  - Use: Trade persistence, market data storage

- **Redis 6+**
  - Connection: `redis` 1.2.2 (connection manager, Sentinel support)
  - Environment: Host, port, username, password, SSL config
  - Location: `crates/infrastructure/src/redis/mod.rs`
  - Features: Default enabled (feature flag: `redis`)
  - Use: Cache, state management, pub/sub

- **redb** - Embedded key-value
  - Client: `redb` 4.1.0
  - Location: `crates/event_store`
  - Use: Event sourcing, local event log

**File Storage:**
- **Cloud Storage (Optional)**
  - Provider: `object_store` 0.13.2
  - Backends: AWS S3, Azure Blob, Google Cloud Storage
  - Features: HTTP support
  - Location: `crates/persistence`
  - Activation: Feature flag `cloud`

- **Local filesystem**
  - Parquet format via `arrow` 58.3.0
  - Used for market data snapshots and trade journals

**Caching:**
- Redis (see above)
- In-process: DashMap for concurrent state

## Authentication & Identity

**Auth Providers:**
- Custom wallet-based signing (ED25519, ECDSA via `ed25519-dalek`, Alloy)
- Exchange API key/secret pairs (stored in environment or config files)
- Cosmos signing (`cosmrs`)
- Interactive Brokers TWS session
- Betfair session tokens

**Credential Management:**
- Environment variables (`nautilus_trader/adapters/env.py`: `get_env_key`, `get_env_key_or`)
- TOML configuration files
- No built-in secret vault integration (consumers must manage secrets)

**Signature Implementation:**
- `aws-lc-rs` 1.17.0 - FIPS-capable crypto (ED25519, ECDSA)
- `ed25519-dalek` 2.2.0 - EdDSA signing for exchanges
- `zeroize` 1.9.0 - Secure memory clearing after use

## Monitoring & Observability

**Structured Logging:**
- Framework: `log` 0.4.32 crate
- Implementation: `tracing` 0.1.44 + `tracing-subscriber` 0.3.23
- Configuration: Environment-based filters
- Location: Used throughout codebase

**Error Tracking:**
- No built-in integration (consumers must implement)
- Uses `anyhow` for error context chaining

**System Metrics:**
- `sysinfo` 0.39.3 - CPU, memory, process monitoring
- Benchmarking: Criterion, IAI for performance tracking

**Distributed Tracing:**
- Tracing framework available
- No built-in collector integration (OpenTelemetry integration via consumers)

## CI/CD & Deployment

**Hosting:**
- Cloud-agnostic (runs on any Linux/macOS with Rust toolchain)
- Tested on: Linux 6.8.0+ (github runners implied)
- Docker support: Optional via `docker` 7.1.0+ Python dependency

**CI Pipeline:**
- GitHub Actions (implied from git history)
- Pre-commit hooks with Clippy, formatting checks
- Build tools: `cargo-nextest` 0.9.137, `cargo-deny`, `cargo-audit`, `cargo-vet`
- Coverage: `cargo-llvm-cov` 0.8.7
- Documentation: Link checker `lychee` 0.24.2

**Build Artifacts:**
- Python wheels (`.whl`) - Compiled extensions for CPython
- Source distributions (`.sdist`) - Cython + Rust sources
- Cargo crates - Published to crates.io

## Environment Configuration

**Required env vars (runtime):**
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE` - For PostgreSQL persistence
- `SCHEMA_DIR` - Path to database schema files (optional, defaults to cwd)
- Exchange-specific: API_KEY, SECRET, etc. (loaded by adapter factories)

**Optional env vars (testing/debugging):**
- `NAUTILUS_TURMOIL_SOAK_START` - Deterministic test seed
- `NAUTILUS_TURMOIL_SOAK_COUNT` - Test iteration count
- `NAUTILUS_WS_LATENCY_MESSAGES` - WebSocket latency benchmark messages
- `NAUTILUS_WS_LATENCY_PAYLOADS` - WebSocket latency payload count
- `DOCS_RS` - Documentation build mode

**Secrets location:**
- Loaded from environment (no default file locations)
- Consumers must implement `.env` file loading via `dotenvy` 0.15.7
- No built-in secrets manager

## Webhooks & Callbacks

**Incoming:**
- **Exchange callbacks** - Market data and order updates via WebSocket streams
- **Event stream subscriptions** - Tokio-based async channels
- No HTTP webhook receivers (data is pull-based via adapters)

**Outgoing:**
- **Order execution** - REST/WebSocket sends to exchanges
- **Data publication** - Event bus (tokio channels) to trading strategies
- No webhook dispatcher

**Event Architecture:**
- Event-driven via tokio channels
- Deterministic event ordering in backtest/simulation
- Madsim for deterministic async testing
- Turmoil for deterministic network testing

---

*Integration audit: 2026-06-13*
