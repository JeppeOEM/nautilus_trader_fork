# Technology Stack

**Analysis Date:** 2026-06-26

## Languages

**Primary:**
- Rust 1.96.0 (stable edition 2024) - Core trading engine, adapters, infrastructure (`crates/`)
- Python 3.12+ (3.12, 3.13, 3.14 supported) - Public API layer, recorder scripts (`troll/dydx_collector/`, `troll/ml_signals/`)

**Secondary:**
- Cython 3.2.5 - Legacy C extension compilation and compatibility layer

## Runtime

**Environment:**
- Rust: Tokio 1.52.3 async runtime with multi-threaded executor (`rt-multi-thread`)
- Python: CPython 3.12+ with uvloop 0.22.1 event loop on non-Windows platforms (pinned to 0.22.1)

**Package Manager:**
- Rust: Cargo with workspace management (48 crates)
- Python: UV (required version ==0.11.21) with pinned lockfile (`uv.lock`)
- Lockfile: `uv.lock` (present, committed for reproducible builds), `Cargo.lock` (committed)

## Frameworks

**Core Trading:**
- NautilusTrader 0.59.0 (Rust) / 1.229.0 (Python) - Production-grade event-driven trading engine
- BacktestNode/BacktestEngine - Deterministic simulation with historical data replay (`troll/ml_signals/backtest_dydx.py`)
- DataFusion 54.0.0 - SQL query engine with regex/unicode expression support (parquet querying)

**Data Handling:**
- Arrow 58.3.0 - Columnar in-memory format with IPC, CSV, JSON support
- Parquet 58.3.0 - Columnar storage with async read/write (ParquetDataCatalog via `nautilus_trader.persistence.catalog`)
- PyArrow 24.0.0+ - Python bindings for Arrow/Parquet serialization

**Networking/Async:**
- Tokio 1.52.3 - Multi-threaded async runtime with signal handling, filesystem, networking
- Tokio-tungstenite 0.29.0 - WebSocket client with rustls-webpki-roots TLS
- Reqwest 0.13.4 - HTTP/2 client with TLS via rustls, stream support
- asyncio (stdlib) - Async event loop in dYdX collector (`troll/dydx_collector/collector.py`)

**Testing:**
- Pytest 7.4.4 (Python) - Test runner with plugin ecosystem
- Criterion 0.8.2 (Rust) - Benchmarking framework
- pytest-asyncio 0.23.8 (Python) - Async test support

**Build/Dev:**
- Poetry Core 2.3.1 - Python wheel/sdist builder via custom `build.py`
- Setuptools 82+ - Python package installation support
- Cargo clippy - Rust linter
- Ruff 0.15.16 - Python formatter and linter (line-length: 100)
- MyPy 1.20.2 - Python type checking with `disallow_incomplete_defs` enforcement

**Data Analysis (ml_signals):**
- pandas 2.3.3+ - DataFrame manipulation, time-series analysis
- numpy 1.26.4+ - Numerical computing
- plotly 6.8.0+ - Interactive charting (dashboard)

## Key Dependencies

**Critical (Trading Domain):**
- rust_decimal 1.42.1 - Fixed-point decimal arithmetic for financial calculations
- Chrono 0.4.45 - Date/time handling with timezone support
- Chrono-TZ 0.10.4 - IANA timezone database
- Price/Quantity/InstrumentId - Nautilus semantic types (never raw primitives)

**Infrastructure:**
- msgspec 0.21.1+ - Fast MessagePack serialization
- Serde 1.0.228 - Serialization/deserialization framework (Rust)
- Serde JSON 1.0.150 - JSON codec with raw value support
- Bincode 2.0.1 - Fast binary encoding (Rust)
- MessagePack (rmp-serde 1.3.1) - Compact binary serialization

**Security/Crypto:**
- AWS-LC-RS 1.17.0 - FIPS-capable crypto (non-FIPS mode; FIPS blocked by Go toolchain)
- Rustls 0.23.40 - TLS with AWS-LC backend
- ED25519-Dalek 2.2.0 - EdDSA signature scheme
- Zeroize 1.9.0 - Secure memory zeroing

**Data Storage:**
- SQLx 0.9.0 - Async SQL (PostgreSQL driver) with compile-time query checking
- Redb 4.1.0 - Embedded key-value store for event sourcing
- Redis 1.2.2 - In-memory cache with connection pooling, Sentinel, streams support
- Object-store 0.13.2 - Cloud storage abstraction (S3, Azure, GCP via features)

**Networking & Protocol:**
- Cap'n Proto 0.25.5 - RPC protocol buffers
- Tonic 0.13.1 - gRPC client with TLS/AWS-LC
- dydx-proto 0.4.0 - dYdX protocol buffer definitions

**Blockchain/Exchange:**
- Alloy 2.0.5 - Ethereum SDK with contract interactions, signers
- Cosmrs 0.22.0 - Cosmos SDK with BIP32 support
- Databento 0.53.0 - Market data distribution (historical + live feeds)
- Betfair-parser 0.19.1 - Betfair API parsing (optional)
- py-clob-client-v2 1.0.1+ - Polymarket CLOB client (optional)

**Utilities:**
- Click 8.4.1+ - CLI argument parsing
- Dotenvy 0.15.7 - `.env` file loading
- TOML 1.1.2 - Configuration file parsing
- tqdm 4.68.1+ - Progress bars
- Indexmap 2.14.0 - Ordered hashmap with serde
- UUID 1.23.3 - UUID v4 generation with serde

**Monitoring:**
- Log 0.4.32 - Structured logging facade (Rust)
- Tracing 0.1.44 - Distributed tracing with dynamic filtering (Rust)
- Python logging - Standard logging module (Python)

**Testing Additions:**
- Madsim 0.2.34 - Deterministic async simulation testing
- Turmoil 0.7.2 - Deterministic network testing
- Proptest 1.11.0 - Property-based testing
- IAI 0.1.1 - Instruction-level profiling benchmarks

## Configuration

**Environment:**
- Rust toolchain via `rust-toolchain.toml` (1.96.0 stable)
- Python version constraint: `requires-python = ">=3.12,<3.15"`
- UV version lock: `==0.11.21` (required for reproducibility)
- Environment variables: `PYTHONUNBUFFERED`, `PYTHONDONTWRITEBYTECODE`, `PIP_NO_CACHE_DIR`, `PYO3_PYTHON`, `CARGO_HOME`, `BUILD_MODE` (set in Docker)

**Build:**
- `pyproject.toml` - Python package metadata, dependencies, build backend, tool configs (ruff, mypy, pytest, coverage)
- `Cargo.toml` - Rust workspace with 48 crates, feature flags, profiles (dev, test, release, bench)
- `.cargo/config.toml` - Cargo workspace settings
- `build.py` - Custom Poetry build script for Rust + Python integration
- `clippy.toml` - Clippy linter configuration (cognitive complexity: 10)
- `rustfmt.toml` - Rust formatter configuration
- `.pre-commit-config.yaml` - Git hooks for style/security checks

**Collector-Specific:**
- `troll/dydx_collector/config.toml` - Runtime configuration (network, catalog_path, flush_interval_seconds, open_interest_poll_seconds)
- `troll/dydx_collector/config.py` - Config loading and hot-reload diffing via TOML

## Platform Requirements

**Development:**
- Rust 1.96.0+ (via rustup)
- Python 3.12+
- C compiler (for Cython/build dependencies)
- Cargo and UV package managers
- CMake/build-essential for native extensions
- Make, pkg-config, capnproto, libcapnp-dev (build-essential deps)
- Linux, macOS preferred (uvloop unavailable on Windows)

**Production/Docker:**
- Docker 7.1.0+ (optional, for containerized deployments)
- Base image: Python 3.13 slim (Debian Bookworm) with Rust 1.96.0 toolchain
- Collector runs as `user: 1000:1000` (host UID/GID to avoid root-owned catalog)
- PostgreSQL 12+ (optional, for persistence)
- Redis 6.0+ (optional, for caching/state)

**dYdX Collector Deployment:**
- Docker Compose with two services:
  1. `collector` - Rebuilt from `troll/dydx_collector/collector.dockerfile` (layers on base)
  2. `dozzle` - amir20/dozzle:latest (log viewer, port 127.0.0.1:8080)
- Volume mounts: `./config.toml` (read-only), `./catalog` (persistent data)
- Restart policy: `unless-stopped`

**Optional Integrations:**
- PostgreSQL - for event store persistence
- Redis - for distributed caching
- Cloud storage (S3, Azure, GCP) - via object_store features
- Interactive Brokers - Native ibapi library (nautilus-ibapi 10.45.1)

---

*Stack analysis: 2026-06-26*
