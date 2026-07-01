<!-- GSD:project-start source:PROJECT.md -->

## Project

**dYdX Market Data Collector** (`troll/dydx_collector/`)

A standalone Python asyncio service that connects directly to dYdX's Rust/PyO3-backed HTTP and WebSocket clients (`nautilus_pyo3.DydxHttpClient`/`DydxWebSocketClient`) — bypassing `TradingNode`/`Strategy`/`DataEngine` entirely — and continuously archives the full breadth of available market data (trades, order book deltas, bars, mark/index price, funding rate, open interest, instrument definitions) to a `ParquetDataCatalog` in the exact format Nautilus expects, with zero conversion step. Runs in Docker alongside Dozzle for live log visibility.

**Core Value:** Reliable, continuous capture of dYdX market data into a Nautilus-catalog-compatible Parquet archive, decoupled from Nautilus's live-trading runtime — which has a documented unbounded-queue-growth + shutdown-wedge bug under sustained high-message-load (see `nautilus_trader/live/data_engine.py` + `live/enqueue.py`'s `ThrottledEnqueuer`) that previously OOM-crashed an earlier `Strategy`/`TradingNode`-based recorder on the `gg` branch. The collector reuses the dYdX adapter's Rust connection/reconnect/throttle/decode logic directly (own asyncio loop, own callback) so it gets that battle-tested networking for free without inheriting the buggy `DataEngine` layer.

### Constraints

- **Language**: Python — pivoted from an earlier Go rebuild (still present, untouched, on the `go` branch) at the user's request; "ponytail" (lazy/minimal) style, no GSD ceremony for this rebuild
- **Architecture**: No `TradingNode`/`Strategy`/`DataEngine` — a plain asyncio script (`troll/dydx_collector/collector.py`) owning its own loop, buffer, and flush timer. `nautilus_trader` is used purely as a library (domain types + `ParquetDataCatalog.write_data()`), never as a live runtime.
- **Repo location**: Lives inside `nautilus_trader_fork`, in `troll/dydx_collector/`, on the `pony` branch — co-located with this repo's git history rather than a separate repo
- **Catalog compatibility**: Output Parquet matches Nautilus's `ParquetDataCatalog` schema/partitioning exactly, since it's written via the catalog's own `write_data()` API, not a hand-rolled schema — loads directly into backtests with zero conversion step
- **Deployment**: Docker / Docker Compose, split into two images: a durable `nautilus-trader-base` image built from `.docker/nautilus_trader.dockerfile`'s `application` target (rebuilt rarely — only when `nautilus_trader` core/deps change), and a thin `troll/dydx_collector/collector.dockerfile` layered on top that just bakes in `troll/dydx_collector/` (rebuilds in seconds). Plus a Dozzle container for log viewing (`troll/dydx_collector/docker-compose.yml`).
- **Fork safety**: Never modify `nautilus_trader/` or `crates/` — the collector is new, additive code only
- **Open interest**: The one field dropped by the Rust/PyO3 bindings on both the REST and WS markets-channel paths — fetched separately via a plain stdlib `urllib` poll against dYdX's public indexer REST endpoint (`troll/dydx_collector/open_interest.py`), wrapped in a custom `DydxOpenInterest(Data)` type registered for Arrow/Parquet serialization
- **Rate limits**: Relies on the Rust WebSocket client's existing built-in subscribe-throttle (dYdX's 2/sec limit) and reconnect handling — no custom throttling code needed
- **Price/quantity integrity**: Never derive a value's stored precision from its own digit count (e.g. via `Decimal.normalize()`), and never round-trip a market data value through `float` before it's safely inside a `Price`/`Quantity`. Real incident: dYdX's mark/index price feed derives each tick's `Price.precision` from however many decimals that specific value has after stripping trailing zeros (`crates/adapters/dydx/src/common/parse.rs`'s `parse_price`), so consecutive ticks for one instrument can carry different precision labels — `ParquetDataCatalog` correctly refuses to read/merge files whose labels disagree. Fixed in `troll/dydx_collector/client.py`'s `_at_fixed_precision()`, which re-stamps every mark/index price at `nautilus_pyo3.FIXED_PRECISION` via `Decimal.scaleb()` + `Price.from_raw()` (exact integer arithmetic, no float). Also watch for: `Price(decimal, precision)` has a real bug in this nautilus_trader version for some decimal/precision combinations — `Price(Decimal("61090.59855"), 16)` silently returns `61090.5985500000026624`. Use `Decimal.scaleb()` + `Price.from_raw()`/`Quantity.from_raw()` instead whenever re-stamping a value at a different precision.

### Development Philosophy

- **This is a full project, not a one-off script.** The catalog grows continuously; strategies will multiply; live trading via `TradingNode` is the eventual destination. Every component should be written to survive that growth.
- **Use Nautilus built-ins first.** Before writing custom data loading, scheduling, reporting, or aggregation code, check whether `BacktestNode`, `BacktestDataConfig`, `TradingNode`, `DataClient`, or another Nautilus primitive already covers it. Wrapping Nautilus is correct; duplicating it is not.
- **Future-proof the data pipeline.** Avoid loading entire catalog slices into memory (e.g. `catalog.trade_ticks()` with no time bounds). Prefer `BacktestDataConfig` streaming, which also enables parameter sweeps and time-range filtering without code changes.
- **Strategies are the product.** The collector and backtest plumbing exist to serve strategy research and eventually live execution. Keep the strategy code clean and portable — `StrategyConfig` + `Strategy` subclass, importable by string path, no hard-wired catalog paths.

### Signal Architecture: 1s-Based, Not Event-Driven

HFT signals (OFI, OBI, microprice, spread) are computed from **1-second sampled snapshots** (`DydxSecondSnapshot`), not from raw order book delta events. This is a deliberate architectural pivot.

**What is stored in Parquet (`DydxSecondSnapshot`):**
- Top-20 bid/ask levels: `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes` (lists)
- Per-side trade volume: `buy_volume`, `sell_volume`

**What is NOT stored — computed on read via `ml_signals/indicators.py`:**
- `microprice` = `Microprice().update_raw(bp, bs, ap, as_)` — derivable from level 0
- `spread` = `ask_prices[0] - bid_prices[0]` — derivable from level 0
- `ofi_N` = `MultiLevelOFI(levels=N)` replayed over consecutive snapshots
- `obi_N` = `MultiLevelOBI(levels=N).update_raw(bid_sizes, ask_sizes)`

**Rule:** If a value can be derived exactly from the stored level data, do not store it. Store raw inputs; compute signals. This keeps the schema minimal and lets strategies experiment with any N-level variant without re-collecting data.

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Rust 1.96.0 (stable edition 2024) - Core trading engine, adapters, infrastructure
- Python 3.12+ (3.12, 3.13, 3.14 supported) - Public API layer, bindings, and recorder scripts
- Cython 3.2.5 - Legacy C extension compilation and compatibility layer
- Cap'n Proto - Protocol buffers for RPC serialization

## Runtime

- Rust: Tokio 1.52.3 async runtime with multi-threaded executor (`rt-multi-thread`)
- Python: CPython 3.12+ with uvloop 0.22.1 event loop on non-Windows platforms
- WebSocket: Dual-backend support (tokio-tungstenite default, sockudo-ws selectable at runtime)
- Rust: Cargo with workspace management (48 crates)
- Python: UV (required version ==0.11.21) with pinned lockfile (`uv.lock`)
- Build Backend: Poetry Core 2.3.1 (Python wheel/sdist builder via custom `build.py`)

## Frameworks

- NautilusTrader 0.59.0 (Rust) / 1.229.0 (Python) - Production-grade event-driven trading engine
- Arrow 58.3.0 - Columnar in-memory format with IPC, CSV, JSON support
- Parquet 58.3.0 - Columnar storage with async read/write
- DataFusion 54.0.0 - SQL query engine with regex/unicode expression support
- MessagePack (rmp-serde 1.3.1) - Compact binary serialization
- Bincode 2.0.1 - Fast binary encoding for Rust
- Cap'n Proto 0.25.5 - RPC protocol buffers
- Tokio 1.52.3 - Multi-threaded async runtime with signal handling, filesystem, networking
- Futures 0.3.32 - Async trait implementations and combinators
- Tokio-tungstenite 0.29.0 - WebSocket client with rustls-webpki-roots TLS
- sockudo-ws 1.7.4 - Alternative WebSocket backend (runtime-selectable)
- Reqwest 0.13.4 - HTTP/2 client with TLS via rustls, stream support, query serialization
- Tonic 0.13.1 - gRPC client with TLS/AWS-LC and webpki-roots
- Tokio-rustls 0.26.4 - Async TLS networking
- Rustls 0.23.40 - TLS with AWS-LC cryptography provider
- Pytest 7.4.4 (Python) - Test runner with plugin ecosystem
- Criterion 0.8.2 (Rust) - Benchmarking framework
- Proptest 1.11.0 - Property-based testing
- Madsim 0.2.34 - Deterministic async simulation testing
- Turmoil 0.7.2 - Deterministic network testing
- IAI 0.1.1 - Instruction-level profiling benchmarks
- Setuptools 82+ - Python package installation support
- Cython 3.2.5 - C extension compilation for performance-critical paths
- MyPy 1.20.2 - Python type checking with `disallow_incomplete_defs` enforcement
- Ruff 0.15.16 - Python formatter and linter (line-length: 100)
- Cargo clippy - Rust linter with 100+ custom rules configured in `clippy.toml`
- Cargo fmt - Rust code formatter (via `cargo +nightly fmt`)

## Key Dependencies

- AWS-LC-RS 1.17.0 - FIPS-capable crypto (non-FIPS mode; FIPS blocked by Go toolchain)
- ED25519-Dalek 2.2.0 - EdDSA signature scheme
- Zeroize 1.9.0 - Secure memory zeroing
- Blake3 1.8.5 - Cryptographic hash function
- Rustls 0.23.40 - TLS with AWS-LC backend
- Webpki-roots 1.0.7 - Mozilla's certificate root store
- Rust Decimal 1.42.1 - Fixed-point decimal arithmetic for financial calculations
- Chrono 0.4.45 - Date/time handling with timezone support
- Chrono-TZ 0.10.4 - IANA timezone database
- Alloy 2.0.5 - Ethereum SDK with contract interactions, signers, signing
- Alloy-primitives 1.6.0 - EVM types with serde support
- Cosmrs 0.22.0 - Cosmos SDK with BIP32 support
- Hypersync-client 1.3.0 - Blockchain event indexing
- Redis 1.2.2 - In-memory cache with connection pooling, Sentinel, streams support
- SQLx 0.9.0 - Async SQL (PostgreSQL driver) with compile-time query checking
- Redb 4.1.0 - Embedded key-value store for event sourcing
- Object-store 0.13.2 - Cloud storage abstraction (S3, Azure, GCP via features)
- Clap 4.6.1 - CLI argument parsing with derive macros
- Dotenvy 0.15.7 - `.env` file loading
- TOML 1.1.2 - Configuration file parsing
- Serde 1.0.228 - Serialization/deserialization framework
- Serde JSON 1.0.150 - JSON codec with raw value support
- Log 0.4.32 - Structured logging facade
- Tracing 0.1.44 - Distributed tracing with dynamic filtering
- Tracing-subscriber 0.3.23 - Tracing output formatting and registry
- Sysinfo 0.39.3 - System metrics collection
- UUID 1.23.3 - UUID v4 generation with serde support
- Indexmap 2.14.0 - Ordered hashmap with serde
- Dashmap 6.2.1 - Concurrent hashmap
- Futures-util 0.3.32 - Async stream and sink combinators
- Thiserror 2.0.18 - Error type derivation
- Databento 0.53.0 - Market data distribution (historical + live feeds)

## Configuration

- Rust: Configured via Cargo workspace with feature flags
- Python: Via `pyproject.toml` dependency groups (dev, test, docs, optional)
- Exchange Credentials: Env variables (BYBIT_API_KEY, etc.) or config objects
- Database: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
- Redis: Connection string via env or config
- `Cargo.toml` (workspace root, 48 crates)
- `pyproject.toml` (Python package metadata, dependencies, build backend)
- `build.py` - Custom Poetry build script for Rust + Python integration
- `.cargo/config.toml` - Cargo workspace settings
- `Cargo.lock` - Committed for reproducible builds
- `uv.lock` - Managed by UV (excluded from `uv.lock` sdist building for third-party packages)
- `rust-toolchain.toml` - Rust 1.96.0 stable
- `clippy.toml` - Clippy linter configuration (cognitive complexity: 10)
- `rustfmt.toml` - Rust formatter configuration
- `.pre-commit-config.yaml` - Git hooks for style/security checks
- `dev` - Fast iteration (opt-level=1 for deps)
- `test` - Comprehensive checks with overflow detection
- `ci-pr` - Optimized CI builds
- `release` - Full optimization (LTO, 3 codegen units)
- `bench` - Benchmarking with debug symbols
- `bench-lto` - Published benchmarks with LTO

## Platform Requirements

- Rust 1.96.0+ (via rustup)
- Python 3.12+
- C compiler (for Cython/build dependencies)
- Cargo and Pip/UV package managers
- CMake/build-essential for native extensions
- Tokio runtime (multi-threaded async executor)
- Linux, macOS, Windows (uvloop unavailable on Windows)
- PostgreSQL 12+ (optional, for persistence)
- Redis 6.0+ (optional, for caching/state)
- Cloud storage (optional, via object_store feature flags)
- Interactive Brokers: Native ibapi library (nautilus-ibapi 10.45.1)
- Betfair: betfair-parser 0.19.1
- Polymarket: py-clob-client-v2 1.0.1+
- Docker: docker 7.1.0+ (optional, for containerized deployments)

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- snake_case for all files: `correctness.rs`, `stack_str.rs`, `test_market_data_capnp.rs`
- Integration test files: `tests/test_*.rs` format
- Unit tests: inline in module with `mod tests { ... }` block at module end
- snake_case for all files: `config.py`, `transformers.py`, `strategy.py`
- Test files: `test_*.py` prefix format
- Config files: `*config.py` suffix (e.g., `risk_engine_config.py`)
- snake_case: `check_predicate_true()`, `decode_array()`, `nanos_since_unix_epoch()`
- Test functions: descriptive snake_case with `test_` prefix: `test_check_predicate_true()`
- Async functions: same convention with `async fn` keyword
- snake_case: `from_str()`, `with_logging()`, `to_pydict()`
- Test functions: `test_` prefix followed by descriptive name
- Private/internal: underscore prefix when needed
- snake_case for local variables and struct fields
- Constants: UPPERCASE_WITH_UNDERSCORES
- Example: `quote_tick`, `bid_price`, `ts_event`
- snake_case for variables
- PascalCase for classes
- UPPERCASE for module-level constants
- Example: `instrument_id`, `max_notional_per_order`
- PascalCase for structs/enums: `QuoteTick`, `CorrectnessError`, `OrderBookDelta`
- PascalCase for traits: `FromCapnp`, `ToCapnp`, `ExecutionClient`
- Type aliases: PascalCase: `CorrectnessResult<T>`
- PascalCase for classes: `RiskEngineConfig`, `MACDStrategy`, `BacktestEngine`
- PascalCase for exceptions

## Code Style

- Tool: `cargo +nightly fmt` with rustfmt
- Line length: 100 characters (configured in Cargo.toml workspace)
- Enforced via pre-commit hook: `check-formatting-rs`
- All Rust code must pass `cargo fmt --all -- --check`
- Tool: `ruff format` (official formatter)
- Line length: 100 characters (configured in `pyproject.toml`)
- Enforced via pre-commit hook: `check-formatting-py`
- Secondary: `isort` for import organization (line-length: 120)
- Tool: `cargo clippy`
- Config: `clippy.toml` (cognitive complexity threshold: 10)
- Over 100 Clippy rules enforced via workspace lints in `Cargo.toml`
- Key denies: `unreachable_pub`, `unexpected_cfgs`, `unsafe_code`, `nonstandard_style`
- Key warns: redundant clones, needless code, performance issues, code simplification
- Disallowed methods: `getrandom::fill`, `getrandom::u32`, `getrandom::u64` (DST seed control)
- Disallowed types: `tokio::task::LocalSet` (use `nautilus_common::live::dst::task::spawn_local` instead)
- Tool: `ruff` with extensive rule set (Python 3.12+)
- Selected rules: `C4, E, F, W, C90, D, DTZ, UP, S, T10, ICN, PIE, PT, PYI, Q, I, RSE, TID, SIM, B, PERF, FURB, ISC, FLY, LOG, ASYNC, PD, PGH, PLE, PLW, NPY, RUF`
- Cognitive complexity limit: 10 (mccabe)
- Type checking: `mypy` with `disallow_incomplete_defs = true`
- Enforced via pre-commit hooks: `ruff` and `ruff-format`

## Import Organization

- Enforced by `ruff` isort plugin and `isort` itself
- Order:
- Lines after imports: 2 blank lines
- Force single line imports per import statement
- Known first-party: `nautilus_trader`
- Use absolute imports from the `nautilus_trader` package root
- Example: `from nautilus_trader.model.identifiers import InstrumentId`
- Never use relative imports for cross-package navigation
- All public exports explicitly declared in module
- Re-export from parent: `pub use self::core::*;`
- Example from `crates/lib.rs`:
- Use `__all__` for public API (shown in `__init__.py` files)
- Example from `nautilus_trader/risk/__init__.py`:

## Error Handling

- Primary: `anyhow::Result<T>` for most errors
- Custom: `CorrectnessResult<T>` (alias for `Result<T, CorrectnessError>`) for validation
- Custom error enum: `CorrectnessError` with typed variants
- Extension trait: `CorrectnessResultExt<T>` with `expect_display()` method
- Validation functions: Return `Result<()>` with descriptive errors
- Functions: `check_predicate_true()`, `check_nonempty_string()`, `check_valid_string_ascii()`
- Panic usage: Allowed in tests (configured in `clippy.toml`); use `.expect()` with message in production code
- Return error-aware values or raise exceptions
- Logging: Log errors at appropriate level before raising
- Type hints required for exception paths
- Use built-in exceptions when appropriate
- Custom exceptions: inherit from appropriate base (e.g., `ValueError`, `RuntimeError`)
- Rust: `crates/core/src/correctness.rs`
- Pre-commit hook: `.pre-commit-hooks/check_error_conventions.sh` (enforces `err_` or `error_` prefixes)

## Logging

- Framework: `log` crate with macros `log::debug!()`, `log::warn!()`, `log::info!()`
- Module-level logger pattern: use module path (crate infers from context)
- Log levels: DEBUG, INFO, WARN, ERROR
- Example: `log::info!("Market data received: {:?}", quote_tick);`
- Pre-commit hook: `check-logging-conventions` enforces proper usage
- Framework: `logging` module with standard levels
- Module-level logger: `logger = logging.getLogger(__name__)`
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Config: `LoggingConfig` in `nautilus_trader.config`
- Example:

## Comments

- Code already shows WHAT; comments should explain WHY and provide context
- Document non-obvious algorithmic decisions
- Mark safety justifications for `unsafe` code
- Use `// SAFETY:` prefix for unsafe code explanations
- Example:
- Use `//!` for module-level docs with examples
- Example from `crates/core/src/correctness.rs`:
- Format: NumPy/Google style with multiline docstrings
- Sections: Parameters, Returns, Raises, Examples
- Example from `RiskEngineConfig`:

## Function Design

- Rust: Maximum threshold is 10 (configured in `clippy.toml`)
- Python: Maximum 10 (mccabe via ruff)
- Break into smaller functions when exceeding threshold
- Use semantic types over primitives: `Price`, `Quantity`, `InstrumentId` from `nautilus_model`
- Generic constraints required: `T: AsRef<str>`
- Lifetime annotations on borrowed data
- Derive traits: `Debug` (required), `Clone`, `Copy`, `Eq`, `PartialEq` as appropriate
- Example:
- Type hints required for all parameters: `def process(data: Bar) -> Price:`
- Type hints on return values: `-> Optional[Price]` or `-> Price | None`
- Use `typing.Union` or `|` syntax for unions (Python 3.10+)
- Use `Optional[T]` or `T | None` for optional values
- PEP 484 syntax enforced by mypy with `disallow_incomplete_defs`
- Use for complex configuration: `LiveNodeBuilder`, `BacktestEngineConfig`
- Example from backtest:
- Rust: Prefer `Result<T>` for fallible operations over panics
- Python: Return `None` for void operations, use type hints for return types

## Module Design

- All public exports explicitly declared in module declaration
- Re-export from parent crate when appropriate
- Use `pub use` for re-exports
- Example:
- Use `__all__` for public API in `__init__.py` files
- Example from `nautilus_trader/risk/__init__.py`:
- Expose only intended public interfaces

## Special Conventions

- Enforce via pre-commit hook: `check-dst-conventions`
- Ban: `getrandom::fill`, direct `tokio::task::LocalSet` usage
- Use: `nautilus_common::live::dst::task::spawn_local` instead
- Rationale: Allows deterministic testing with seeded RNG and madsim runtime
- Enforce via pre-commit hook: `check-pyo3-conventions`
- Name Rust types matching Python exposure pattern
- Use `#[pyo3(...)]` attributes for FFI
- Keep Rust and Python type names aligned
- Enforce via pre-commit hook: `check-tokio-usage`
- DST runtime compatibility required
- Avoid OS RNG directly; use seeded alternatives
- Use async-first patterns with `tokio::spawn` for concurrent work
- Enforce via pre-commit hook: `check-anyhow-usage`
- Use `anyhow::Result<T>` for error context and message formatting
- Propagate with `?` operator
- Avoid bare `unwrap()`; use `.expect()` with messages
- Enforce via pre-commit hook: `check-nautilus-conventions`
- Use semantic types: `Price`, `Quantity`, `InstrumentId` from `nautilus_model`
- Never use raw primitives (f64, u64) for domain values
- Example:

## Copyright and License

- All source files must include LGPL-3.0 copyright header
- Format:
- Enforced via pre-commit hook: `check-copyright-year` (verifies year is current)

## Pre-Commit Hooks Summary

- `check-formatting-rs` - Rust formatting via `cargo fmt`
- `check-formatting-py` - Python formatting via `ruff format`
- `cargo-clippy` - Rust linting
- `ruff` and `ruff-format` - Python linting and formatting
- `mypy` - Python type checking
- `check-dst-conventions` - DST compliance
- `check-pyo3-conventions` - PyO3 naming
- `check-tokio-usage` - Tokio patterns
- `check-anyhow-usage` - Error handling
- `check-nautilus-conventions` - Semantic type usage
- `check-logging-conventions` - Logging patterns
- `check-error-conventions` - Error variable naming
- `check-copyright-year` - License headers
- `check-testing-conventions` - Test standardization

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## System Overview

```text

```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **NautilusKernel** | Central orchestrator for kernel startup, shutdown, and lifecycle management | `crates/system/src/kernel.rs` |
| **Trader** | Manages actor/strategy registration, lifecycle, and command execution | `crates/system/src/trader.rs` |
| **DataEngine** | Processes market data feeds, aggregates bars, manages subscriptions | `crates/data/src/engine` |
| **ExecutionEngine** | Routes orders, manages order lifecycle, coordinates with venues | `crates/execution/src/engine` |
| **RiskEngine** | Calculates position risk, portfolio margin, enforcement limits | `crates/risk/src/engine.rs` |
| **Portfolio** | Maintains account and position state, P&L tracking | `crates/portfolio/src/portfolio.rs` |
| **MessageBus** | Routes events between components via typed topics/endpoints | `crates/common/src/msgbus` |
| **Cache** | In-memory store for market data, instruments, accounts, positions | `crates/common/src/cache` |
| **Clock** | Provides system timestamps with atomic synchronization | `crates/common/src/clock.rs` |
| **Strategy** | User-defined trading logic with event handlers | `crates/trading/src/strategy` |
| **ExecutionClient** | Connects to exchanges, transforms orders, processes fills | `crates/adapters/{venue}/src/execution.rs` |
| **DataClient** | Connects to data providers, streams market data | `crates/adapters/{venue}/src/data.rs` |
| **OrderEmulator** | Emulates advanced order types not supported by venue | `crates/execution/src/order_emulator` |
| **MatchingEngine** | Simulates order matching for backtesting | `crates/execution/src/matching_engine` |
| **EventStore** | Persists and replays events for determinism | `crates/event_store/src` |

## Pattern Overview

- **Deterministic**: All trading logic produces identical results when replayed with same inputs (enables backtesting research parity)
- **Single-threaded event loop**: All component callbacks execute synchronously in sequence; live mode uses tokio async runtime for network I/O, but trading logic executes synchronously
- **Zero-copy messaging**: Event references passed through message bus without cloning
- **Shared reference semantics**: Components use `Rc<RefCell<T>>` for internal state sharing (single-threaded safety)
- **Component lifecycle**: Consistent state machine (PreInitialized → Ready → Running → Stopped/Disposed)
- **Trait-based abstractions**: Adapters implement common interfaces for venues and data providers via `ExecutionClient` and `DataClient` traits

## Layers

- Purpose: Foundational types, time, math, UUID, serialization, correctness validation
- Location: `crates/core/src`
- Contains: Time handling, UUID generation, math functions, serialization traits, correctness validators (with `CorrectnessResult<T>` error type)
- Depends on: Rust std, external crates (decimal, chrono)
- Used by: All other crates
- Purpose: Trading domain types with type safety and validation
- Location: `crates/model/src`
- Contains: Events (`crates/model/src/events`), orders, positions, instruments, accounts, identifiers, currencies, venues
- Depends on: `nautilus-core`, `nautilus-serialization`
- Used by: Data, execution, portfolio, risk engines
- Purpose: Shared runtime services (clock, cache, actor system, message bus, component lifecycle)
- Location: `crates/common/src`
- Contains: Clock, cache (`crates/common/src/cache`), component trait (`crates/common/src/component.rs`), actor registry (`crates/common/src/actor`), message bus (`crates/common/src/msgbus`), timers, logging
- Depends on: `nautilus-core`, `nautilus-model`
- Used by: Kernel, engines, strategies, adapters
- Purpose: Order routing, matching, fill processing, risk-constrained execution
- Location: `crates/execution/src`
- Contains: ExecutionEngine, OrderManager, MatchingEngine, OrderEmulator, order books, fee models, reconciliation
- Depends on: Common, model, serialization
- Used by: Kernel, backtest, live engines
- Purpose: Market data ingestion, aggregation, bar generation, option chains
- Location: `crates/data/src`
- Contains: DataEngine, DataClients, aggregators, bar builders, option chain handlers
- Depends on: Common, model, serialization
- Used by: Kernel, backtest, live engines
- Purpose: Position tracking, P&L calculation, risk enforcement, margin management
- Location: `crates/portfolio/src`, `crates/risk/src`
- Contains: Portfolio state machine, position manager, risk calculator, margin enforcement
- Depends on: Common, model
- Used by: Kernel, execution engine
- Purpose: Central orchestration of kernel, trader, controller, event store integration
- Location: `crates/system/src`
- Contains: NautilusKernel, Trader, Controller, event store registration, startup/shutdown
- Depends on: All engine layers, common, model
- Used by: Live, backtest, CLI
- **Live:** `crates/live/src` - Async runner for real-time trading via tokio runtime; provides `LiveNode` builder and configuration
- **Backtest:** `crates/backtest/src` - Deterministic simulation with historical data replay via `BacktestEngine`
- **CLI:** `crates/cli/src` - Command-line interface for system operations (database, event replay, analysis)
- Purpose: Venue-specific order/trade integration and data provider connections
- Location: `crates/adapters/{venue}/src`
- Contains: ExecutionClient impl, DataClient impl, venue-specific transformations (HTTP, WebSocket)
- Depends on: Common, model, execution, data, network
- Used by: Live and backtest modes
- Purpose: Persistence backends (SQL, Redis), serialization, cryptography
- Location: `crates/infrastructure/src`, `crates/serialization/src`, `crates/cryptography/src`
- Contains: SQL cache databases, Redis clients, serde implementations, signing, parquet catalog
- Depends on: Common, model
- Used by: Kernel, persistence layer

## Data Flow

### Primary Request Path (Live Market Event)

### Backtesting Event Path

### Order Lifecycle State Machine

```

```

- `OrderEventAny` union type in `crates/model/src/events`
- Message bus routing via `get_event_orders_topic()` endpoint
- Portfolio and position tracking updates
- OrderManager maintains local state independent of venue (for reconciliation)
- Portfolio maintains atomic account and position state
- Cache maintains current order snapshot for quick lookups
- Risk engine caches enforcement limits updated per fill

## Key Abstractions

- Purpose: Defines unified lifecycle for all system entities
- Examples: `crates/common/src/component.rs` implements `Component` trait
- Pattern: State machine (PreInitialized → Ready → Running → Stopped/Disposed) with trigger-based transitions
- Purpose: Lightweight message-processing entities in global registry (strategies, custom actors)
- Examples: `crates/common/src/actor/registry.rs`
- Pattern: Registry-based lookup with `Box<dyn Actor>`, downcasting via `as_any()`, type-erased closures for lifecycle
- Purpose: Abstract interface for order submission and fill reception
- Examples: `crates/adapters/{venue}/src/execution.rs` implementations for Binance, Bybit, Kraken, etc.
- Pattern: Trait object implementing unified protocol; venue-specific details hidden behind `ExecutionClient` interface
- Purpose: Abstract interface for market data subscription and reception
- Examples: `crates/adapters/{venue}/src/data.rs` implementations for various data providers
- Pattern: Trait object implementing unified subscription model; adapters handle WebSocket/HTTP transport
- Purpose: Typed subscribers to message bus topics
- Examples: Strategy callbacks registered via `msgbus.subscribe(topic, handler)`
- Pattern: Function closures matching handler signature, registered to typed message bus endpoints
- Purpose: Single source of truth for market state and order state
- Examples: `crates/common/src/cache` provides getters/setters for instruments, orders, positions, quotes, bars
- Pattern: HashMap-based key-value store with get/set/delete methods; used throughout system for fast lookups

## Entry Points

- Location: `crates/system/src/kernel.rs:NautilusKernel::builder()`
- Triggers: Direct instantiation via builder pattern (`NautilusKernelBuilder`)
- Responsibilities: Initialization of all engines, trader registration, startup/shutdown orchestration
- Location: `crates/live/src/node.rs:LiveNode::run()`
- Triggers: Invoked from Python `LiveNode` class or directly from Rust
- Responsibilities: Real-time data streaming, order execution, async event handling via tokio runtime
- Location: `crates/backtest/src/engine.rs:BacktestEngine::run()`
- Triggers: Invoked from backtest CLI or Python `BacktestNode` API
- Responsibilities: Deterministic replay of historical data, accumulation of results, performance analysis
- Location: `crates/trading/src/strategy/core.rs:Strategy::on_bar()`, `Strategy::on_order_filled()`
- Triggers: Message bus event subscriptions
- Responsibilities: User-defined trading logic in response to data/fill events; calls kernel methods to submit orders
- Location: `crates/cli/src/bin/cli.rs`
- Triggers: Command-line invocation
- Responsibilities: System administration (database operations, event replay, analysis)

## Architectural Constraints

- **Threading:** Single-threaded event loop per instance. Live mode uses tokio async runtime for network I/O, but trading logic executes synchronously on the main thread. Backtest is purely synchronous with no async.
- **Global state:** Component registry (actors, strategies, algorithms) uses thread-unsafe `Rc<RefCell<T>>` and assumes single-threaded access. Global message bus stored in thread-local storage (`thread_local!` macro).
- **Circular imports:** None enforced; module structure prevents cycles via unidirectional dependencies (core → model → common → engines → system).
- **Memory model:** Strategies and components use `Rc<RefCell<T>>` for shared ownership. `RefCell` provides interior mutability but panics on borrow conflicts at runtime (by design for determinism detection).
- **Error handling:** Most functions return `anyhow::Result<T>`. Panics allowed in tests and for invariant violations (e.g., mutex poisoning, component state violations).
- **DST Runtime Compatibility:** Rust code must avoid direct OS RNG (`getrandom::fill`), use seeded alternatives instead. Tokio tasks must use `nautilus_common::live::dst::task::spawn_local` instead of `tokio::task::spawn_local`.

## Anti-Patterns

### Direct Mutation Without Message Bus

### Synchronous Blocking I/O in Live Mode

### Storing References to Global State Without Rc

## Error Handling

- Order submission validates risk constraints; if violated, returns `Err(...)` and order is not submitted
- Venue adapter connection failures logged and component transitioned to `ComponentState::Degraded` state
- Event store replay stops on corrupted entry with clear `anyhow::Result` error message
- Cache lookups return `Option<T>`; handlers check for instrument existence before trading
- Internal errors use `anyhow::Result<T>` with context via `.context("description")`
- Validation failures return `CorrectnessResult<T>` from `nautilus_core::correctness` (with `CorrectnessError` variants)
- User-facing errors logged before returning to strategy via message bus (via `on_error()` callbacks or exceptions)

## Cross-Cutting Concerns

- Rust: `log` crate with macros (`log::debug!()`, `log::warn!()`, `log::info!()`)
- Python: `logging` module with standard levels
- Centralized configuration via `LoggerConfig` passed to kernel
- File output to journald or rotating file logs
- Core domain types validate invariants in constructors (e.g., `Price > 0`, `Quantity > 0`)
- Returns `CorrectnessResult<T>` with descriptive error messages
- Validation failures caught early before being stored in cache or portfolio
- Adapter-specific API keys/secrets loaded from environment variables
- Passed to `ExecutionClient` and `DataClient` constructors
- Stored in adapter config structs, never logged or printed
- RiskEngine checks position limits, portfolio margin before order submission
- Returns veto if constraints violated; order not sent to venue
- Limits configured per account via `RiskEngineConfig`

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

