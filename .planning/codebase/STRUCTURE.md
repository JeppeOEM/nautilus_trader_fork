# Codebase Structure

**Analysis Date:** 2026-06-13

## Directory Layout

```
nautilus_trader_fork/
├── .claude/               # Claude agent configuration and skills
├── .github/               # GitHub CI/CD workflows
├── .planning/             # Project planning and analysis documents
├── crates/                # Main Rust workspace root
│   ├── adapters/          # Venue and data provider integrations
│   ├── analysis/          # Trade analysis and statistics
│   ├── backtest/          # Deterministic backtesting engine
│   ├── cli/               # Command-line interface
│   ├── common/            # Shared services and utilities
│   ├── core/              # Foundational primitives
│   ├── cryptography/      # Signing and cryptographic operations
│   ├── data/              # Market data engine and processing
│   ├── event_store/       # Event persistence and replay
│   ├── execution/         # Order execution engine
│   ├── indicators/        # Technical analysis indicators
│   ├── infrastructure/    # Database and caching backends
│   ├── live/              # Real-time trading execution
│   ├── model/             # Domain model types and events
│   ├── network/           # Network utilities and protocols
│   ├── persistence/       # Parquet and data serialization
│   ├── plugin/            # Plugin and adapter loading system
│   ├── portfolio/         # Portfolio accounting and P&L
│   ├── pyo3/              # Python FFI bindings
│   ├── risk/              # Risk engine and limits
│   ├── serialization/     # Message serialization/deserialization
│   ├── system/            # Kernel, trader, controller orchestration
│   ├── testkit/           # Testing utilities and fixtures
│   ├── trading/           # Strategy framework and session utilities
│   ├── lib.rs             # Workspace-level exports
│   └── Cargo.toml         # Workspace manifest
├── Cargo.lock             # Dependency lock file
├── Cargo.toml             # Root workspace manifest
├── Makefile               # Development tasks
└── README.md              # Project documentation
```

## Directory Purposes

**crates/**
- Purpose: Root of monorepo containing all Rust components
- Contains: 26 crates organized by domain and function
- Key files: `Cargo.toml` (workspace manifest), `README.md` (crate architecture guide), `lib.rs` (re-exports)

**crates/core/**
- Purpose: Foundational primitives used by all other crates
- Contains: Time handling, UUID, math, serialization traits, correctness validators, FFI bindings
- Key files: `crates/core/src/lib.rs`, `crates/core/src/time.rs`, `crates/core/src/nanos.rs`

**crates/model/**
- Purpose: Type-safe domain model for trading (orders, positions, events, instruments)
- Contains: Event types (OrderSubmitted, OrderFilled, PositionOpened), order/position state, identifiers
- Key files: `crates/model/src/lib.rs`, `crates/model/src/events/`, `crates/model/src/orders/`

**crates/common/**
- Purpose: Shared runtime services for all components
- Contains: Clock, cache, actor registry, message bus, component lifecycle, logging, timers
- Key files: `crates/common/src/clock.rs`, `crates/common/src/cache/`, `crates/common/src/msgbus/`
- Subdirectories: `actor/` (actor system), `cache/` (in-memory storage), `messages/` (message types), `msgbus/` (routing)

**crates/data/**
- Purpose: Market data ingestion, aggregation, and processing
- Contains: DataEngine, aggregators, bar builders, option chain handlers, DeFi data adapters
- Key files: `crates/data/src/engine/`, `crates/data/src/aggregation.rs`, `crates/data/src/defi/`

**crates/execution/**
- Purpose: Order execution, routing, matching, and fill processing
- Contains: ExecutionEngine, OrderManager, MatchingEngine, OrderEmulator, order books, fee models
- Key files: `crates/execution/src/engine/`, `crates/execution/src/matching_engine/`, `crates/execution/src/order_emulator/`

**crates/portfolio/**
- Purpose: Position tracking, account state, and P&L calculation
- Contains: Portfolio state machine, position manager, account ledger, margin calculator
- Key files: `crates/portfolio/src/portfolio.rs`, `crates/portfolio/src/manager.rs`

**crates/risk/**
- Purpose: Risk calculation, enforcement, and margin management
- Contains: RiskEngine, position risk calculator, margin enforcer, limits configuration
- Key files: `crates/risk/src/engine.rs`, `crates/risk/src/sizing.rs`

**crates/system/**
- Purpose: Central kernel orchestration, trader lifecycle, event store integration
- Contains: NautilusKernel, Trader (component orchestrator), Controller (command dispatch)
- Key files: `crates/system/src/kernel.rs`, `crates/system/src/trader.rs`, `crates/system/src/builder.rs`

**crates/backtest/**
- Purpose: Deterministic backtesting with historical data replay
- Contains: BacktestEngine, simulated exchange, data client, results accumulation
- Key files: `crates/backtest/src/engine.rs`, `crates/backtest/src/exchange.rs`, `crates/backtest/src/accumulator.rs`

**crates/live/**
- Purpose: Real-time trading execution with async I/O
- Contains: LiveNode, async runner, plugin loader, execution manager
- Key files: `crates/live/src/node.rs`, `crates/live/src/runner.rs`, `crates/live/src/manager.rs`

**crates/adapters/**
- Purpose: Integration with venue and data providers
- Contains: 19+ exchange adapters (Binance, Kraken, Deribit, etc.), data provider adapters
- Key files: `crates/adapters/{venue}/src/execution.rs`, `crates/adapters/{venue}/src/config.rs`
- Venues: `architect_ax/`, `betfair/`, `binance/`, `bitmex/`, `bybit/`, `coinbase/`, `deribit/`, `dydx/`, `hyperliquid/`, `kraken/`, `lighter/`, `okx/`, and more

**crates/event_store/**
- Purpose: Event persistence and deterministic replay
- Contains: EventStore, event capture, replay engine, snapshot management
- Key files: `crates/event_store/src/kernel.rs`, `crates/event_store/src/replay.rs`

**crates/infrastructure/**
- Purpose: Persistence backends and system infrastructure
- Contains: SQL cache database (Redis, PostgreSQL), query builders
- Key files: `crates/infrastructure/src/sql/`, `crates/infrastructure/src/redis/`

**crates/persistence/**
- Purpose: Data serialization and Parquet export
- Contains: Parquet format handlers, data export utilities
- Key files: `crates/persistence/src/parquet.rs`

**crates/serialization/**
- Purpose: Message serialization/deserialization
- Contains: serde implementations, Arrow schema registries, custom serialization
- Key files: `crates/serialization/src/`

**crates/trading/**
- Purpose: Strategy framework and trading utilities
- Contains: Strategy trait, session/timezone calculations
- Key files: `crates/trading/src/strategy/`, `crates/trading/src/sessions.rs`

**crates/analysis/**
- Purpose: Trade analysis and performance statistics
- Contains: Statistics calculator, analyzer, P&L metrics
- Key files: `crates/analysis/src/analyzer.rs`, `crates/analysis/src/statistic.rs`

**crates/indicators/**
- Purpose: Technical analysis indicators
- Contains: Built-in indicators (EMA, SMA, MACD, etc.)
- Key files: `crates/indicators/src/lib.rs`

**crates/plugin/**
- Purpose: Plugin and adapter loading system
- Contains: Plugin loader, venue adapter discovery
- Key files: `crates/plugin/src/`

**crates/cli/**
- Purpose: Command-line interface
- Contains: CLI subcommands (event replay, analytics, data management)
- Key files: `crates/cli/src/bin/`, `crates/cli/src/opt.rs`

**crates/testkit/**
- Purpose: Testing utilities and fixtures
- Contains: Mock builders, test data generators, assertion helpers
- Key files: `crates/testkit/src/files.rs`, `crates/testkit/src/common.rs`

**crates/pyo3/**
- Purpose: Python FFI bindings
- Contains: PyO3 wrapper types for Python interop
- Key files: `crates/pyo3/src/`

## Key File Locations

**Entry Points:**
- Kernel startup: `crates/system/src/kernel.rs:NautilusKernel::builder()`
- Backtest entry: `crates/backtest/src/engine.rs:BacktestEngine::run()`
- Live entry: `crates/live/src/node.rs:LiveNode::run()`
- CLI entry: `crates/cli/src/bin/` (binary crates)

**Configuration:**
- Kernel config: `crates/system/src/config.rs`
- Backtest config: `crates/backtest/src/config.rs`
- Live config: `crates/live/src/config.rs`
- Adapter configs: `crates/adapters/{venue}/src/config.rs`

**Core Logic:**
- Data processing: `crates/data/src/engine/`
- Order execution: `crates/execution/src/engine/`
- Position management: `crates/portfolio/src/portfolio.rs`
- Risk enforcement: `crates/risk/src/engine.rs`

**Message Bus:**
- Message routing: `crates/common/src/msgbus/`
- Typed handlers: `crates/common/src/msgbus/typed_handler.rs`
- Switchboard: `crates/common/src/msgbus/switchboard.rs`

**Component Lifecycle:**
- Component trait: `crates/common/src/component.rs`
- Trader orchestrator: `crates/system/src/trader.rs`
- Actor registry: `crates/common/src/actor/registry.rs`

**Testing:**
- Test fixtures: `crates/testkit/src/`
- Integration tests: `crates/*/tests/` (sibling to `src/`)

## Naming Conventions

**Files:**
- Modules: `snake_case.rs` (e.g., `execution_engine.rs`, `order_manager.rs`)
- Binary entry points: `main.rs` or named files in `bin/` subdirectory
- Tests: `#[cfg(test)] mod tests { ... }` in same file, or `tests/` directory

**Directories:**
- Crates: `snake_case` (e.g., `crates/data`, `crates/execution`)
- Modules: `snake_case` (e.g., `crates/data/src/engine/`, `crates/common/src/msgbus/`)
- Adapter venues: `snake_case` (e.g., `crates/adapters/binance/`)

**Functions:**
- Public: `snake_case` (e.g., `submit_order()`, `on_bar()`)
- Builders: `builder()`, fluent methods like `with_config()`
- Event handlers: `on_{event}()` (e.g., `on_bar()`, `on_order_filled()`)

**Types:**
- Structs: `PascalCase` (e.g., `OrderManager`, `DataEngine`)
- Traits: `PascalCase` (e.g., `Component`, `ExecutionClient`)
- Enums: `PascalCase` (e.g., `ComponentState`, `OrderSide`)
- Type aliases: `PascalCase` (e.g., `UnixNanos`)

## Where to Add New Code

**New Feature (within existing subsystem):**
- Primary code: Extend relevant crate (e.g., new order type in `crates/model/src/orders/`)
- Tests: Add to same file via `#[cfg(test)]` or `tests/{feature_name}.rs`
- Example: Adding trailing stop order → `crates/model/src/orders/trailing_stop.rs`

**New Component/Module:**
- If core domain: `crates/model/src/{component}/`
- If engine logic: `crates/{engine}/src/{component}/`
- If utility: `crates/common/src/{component}/`
- Example: New portfolio analytics → `crates/portfolio/src/analytics/` with submodule in `portfolio.rs`

**New Venue Adapter:**
- Create: `crates/adapters/{venue_name}/`
- Copy template from similar venue (e.g., `crates/adapters/binance/` for spot exchange)
- Implement: `crates/adapters/{venue_name}/src/execution.rs`, `data.rs`, `config.rs`

**New Data Provider Adapter:**
- Create: `crates/adapters/{provider_name}/`
- Implement: `crates/adapters/{provider_name}/src/data.rs`
- Register: Add to workspace `Cargo.toml` members list

**Utilities and Helpers:**
- Shared helpers: `crates/common/src/` (factories, generators, clients)
- Type conversions: `crates/serialization/src/`
- Testing helpers: `crates/testkit/src/`

**Python Bindings:**
- Wrapper types: `crates/pyo3/src/`
- Module-specific bindings: `crates/{module}/src/python/` (e.g., `crates/backtest/src/python/`)

## Special Directories

**crates/.cargo/:**
- Purpose: Build configuration
- Generated: No (checked in)
- Committed: Yes

**crates/adapters/derive:**
- Purpose: Proc-macro for generating adapter traits
- Generated: No
- Committed: Yes
- Special: Compile-time code generation for data type conversions

**crates/persistence/macros:**
- Purpose: Proc-macros for persistence annotations
- Generated: No
- Committed: Yes
- Special: Code generation for Parquet schema derivation

**.planning/codebase/**
- Purpose: Architecture and structure documentation
- Generated: Yes (by `/gsd-map-codebase` agent)
- Committed: Yes

**crates/*/tests/**
- Purpose: Integration tests per crate
- Generated: No (hand-written)
- Committed: Yes

**crates/*/python/**
- Purpose: Python FFI wrapper code
- Generated: No (hand-written)
- Committed: Yes

---

*Structure analysis: 2026-06-13*
