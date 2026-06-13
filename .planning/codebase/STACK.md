# Technology Stack

**Analysis Date:** 2026-06-13

## Languages

**Primary:**
- Rust 1.96.0 - Core trading engine, adapters, and infrastructure
- Python 3.12+ - Bindings and public API layer via PyO3

**Secondary:**
- Cython 3.2.5 - Legacy compatibility for Python extension compilation

## Runtime

**Environment:**
- Rust: Edition 2024 (async-first, tokio-based event loop)
- Python: 3.12, 3.13, 3.14 support

**Package Manager:**
- Cargo (Rust) - Version management via workspace
- UV (Python) - Python dependency management with pinned lockfile
- Poetry (Python) - Build backend for wheel/sdist generation

**Lockfiles:**
- `Cargo.lock` - Present and committed
- `uv.lock` - Managed by UV package manager

## Frameworks

**Core:**
- Tokio 1.52.3 - Async runtime (rt-multi-thread, signals, filesystem, network I/O)
- PyO3 0.29.0 - Rust-to-Python bindings with async support

**Serialization & Data:**
- Arrow 58.3.0 - Data serialization and columnar format
- Parquet 58.3.0 - Columnar storage (async support)
- DataFusion 54.0.0 - Query engine (SQL, regex, unicode expressions)
- Cap'n Proto 0.25.5 - Protocol buffers for RPC
- MessagePack (rmp-serde) - Compact serialization
- Bincode 2.0.1 - Binary encoding

**Networking:**
- Tokio-tungstenite 0.29.0 - WebSocket client (tokio runtime)
- sockudo-ws 1.7.4 - Alternative WebSocket backend (runtime-selectable)
- Reqwest 0.13.4 - HTTP/2 client (TLS via rustls)
- Tonic 0.13.1 - gRPC client (TLS support)

**Testing:**
- Pytest 7.4.4 - Python test runner
- Criterion 0.8.2 - Rust benchmarking
- IAI 0.1.1 - Instruction-level profiling benchmarks
- Proptest 1.11.0 - Property-based testing

**Build/Dev:**
- Poetry Core 2.3.1 - Build backend
- Setuptools 82+ - Build support
- Cython 3.2.5 - C extension compilation
- Madsim 0.2.34 - Deterministic async simulation testing
- Turmoil 0.7.2 - Deterministic network testing

## Key Dependencies

**Critical:**
- Rust Decimal 1.42.1 - Fixed-point decimal arithmetic (serde support)
- Chrono 0.4.45 - Date/time handling (timezone aware)
- Chrono-TZ 0.10.4 - Timezone database
- Tokio-util 0.7.18 - Tokio utilities
- Futures 0.3.32 - Async abstractions

**Cryptography & Security:**
- AWS-LC-RS 1.17.0 - FIPS-capable cryptography (non-FIPS mode)
- ED25519-Dalek 2.2.0 - EdDSA signatures
- Rustls 0.23.40 - TLS with AWS-LC backend
- Zeroize 1.9.0 - Secure memory zeroing

**Blockchain & DeFi:**
- Alloy 2.0.5 - Ethereum SDK (contracts, signers, signing)
- Alloy-primitives 1.6.0 - EVM types and serde support
- Cosmrs 0.22.0 - Cosmos SDK with BIP32
- Hypersync-client 1.3.0 - Blockchain event indexing

**Data Storage:**
- redb 4.1.0 - Embedded key-value store (event sourcing)
- Redis 1.2.2 - In-memory cache (connection pooling, Sentinel, streams)
- SQLx 0.9.0 - Async SQL (PostgreSQL driver)
- Object-store 0.13.2 - Cloud storage abstraction (S3, Azure, GCP support via features)

**Infrastructure & Monitoring:**
- Log 0.4.32 - Structured logging
- Tracing 0.1.44 - Distributed tracing
- Tracing-subscriber 0.3.23 - Tracing output formatting
- Sysinfo 0.39.3 - System metrics

**Command-line & Configuration:**
- Clap 4.6.1 - CLI argument parsing
- Dotenvy 0.15.7 - .env file loading
- TOML 1.1.2 - Configuration parsing

## External Adapters

**Exchanges (18+ adapters):**
- Binance (crypto spot/futures) - native WebSocket + REST
- Coinbase (crypto spot) - WebSocket
- Kraken (crypto spot) - REST/WebSocket
- Bybit (crypto derivatives) - WebSocket
- OKX (crypto derivatives) - WebSocket
- Deribit (crypto options) - WebSocket
- Bitmex (derivatives) - WebSocket
- Hyperliquid (perpetuals) - HTTP API
- dYdX (perpetuals) - gRPC (tonic)
- Lighter (orderbook protocol) - Custom protocol
- Betfair (sports betting) - REST API
- Interactive Brokers (traditional equities) - API

**Data Providers:**
- Databento 0.53.0 - Market data distribution (historical + live)
- Tardis (historical market data) - REST API
- Hypersync-client (blockchain data) - Event indexing

**Blockchain Networks:**
- Alloy (Ethereum/EVM) - Smart contract interactions
- Cosmrs (Cosmos) - Cosmos chain interactions
- Polymarket - Prediction market adapter
- Blockchain generic adapter - DeFi protocol support

**Execution Engines:**
- Architect-AX (algorithmic execution)
- Sandbox (paper trading)
- Derive (parameterized routing)

## Configuration

**Environment:**
- Configuration via TOML files and env variables
- Adapter-specific configs in `config.py` files per adapter
- Database connection strings via env (POSTGRES_*, REDIS_*)

**Build:**
- `build.py` - Poetry-based custom build script (Rust + Python integration)
- `Cargo.toml` - Workspace with 48 crates
- `pyproject.toml` - Python package metadata, dependency groups

**Key Environment Variables:**
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE` - PostgreSQL
- `SCHEMA_DIR` - Database schema location
- `DOCS_RS` - Build documentation mode
- Exchange-specific: API keys, secrets, etc. (adapter-loaded)

## Platform Requirements

**Development:**
- Rust 1.96.0 or later
- Python 3.12+ (3.14 support)
- C compiler (for Cython/build dependencies)
- Cargo and Pip/UV

**Production:**
- Tokio runtime with multi-threaded executor
- Async-aware (no blocking I/O)
- PostgreSQL 12+ (optional, for persistence)
- Redis 6+ (optional, for caching/state)
- Cloud storage (optional, via object_store features)

**Performance Profiles:**
- `dev` - Fast iteration, opt-level=1 for deps
- `test` - Comprehensive checks, overflow detection
- `ci-pr` - Optimized CI builds
- `release` - Full optimization (LTO, 3 codegen units)
- `bench` - Benchmarking with debug symbols
- `bench-lto` - Published benchmarks with LTO

---

*Stack analysis: 2026-06-13*
