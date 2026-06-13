<!-- refreshed: 2026-06-13 -->
# Architecture

**Analysis Date:** 2026-06-13

## System Overview

NautilusTrader is a production-grade, Rust-native engine for multi-asset, multi-venue trading systems. It spans research, deterministic simulation, and live execution within a single event-driven architecture.

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                            Entry Points (User Layer)                      │
│  CLI                  Strategies                    Python Bindings       │
│ `crates/cli/src`     `crates/trading/src/strategy` `crates/pyo3/src`    │
└─────────────┬─────────────────────────┬──────────────────────┬──────────┘
              │                         │                      │
              ▼                         ▼                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       Kernel Layer (NautilusKernel)                       │
│                         `crates/system/src`                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Trader (Orchestrator) - Lifecycle & Component Registration      │    │
│  │ Controller - Command dispatch                                   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└───────────────┬─────────────────────────────────────────┬────────────────┘
                │                                         │
     ┌──────────▼──────────────────────┐   ┌──────────────▼────────────┐
     │                                  │   │                           │
     ▼                                  ▼   ▼                           ▼
┌────────────────────────┐ ┌─────────────────────────┐ ┌──────────────────────┐
│   DATA LAYER           │ │  EXECUTION LAYER        │ │  SUPPORT SERVICES    │
│ `crates/data/src`      │ │ `crates/execution/src`  │ │                      │
│ ┌────────────────────┐ │ │ ┌────────────────────┐  │ │ Portfolio            │
│ │ DataEngine         │ │ │ │ ExecutionEngine    │  │ │ `crates/portfolio`   │
│ │ ┌──────────────┐   │ │ │ │ ┌──────────────┐   │  │ │                      │
│ │ │Aggregation   │   │ │ │ │ │Order Manager │   │  │ │ Risk Engine          │
│ │ │Handlers      │   │ │ │ │ └──────────────┘   │  │ │ `crates/risk/src`    │
│ │ └──────────────┘   │ │ │ │ ┌──────────────┐   │  │ │                      │
│ │ ┌──────────────┐   │ │ │ │ │Matching Core │   │  │ │ Cache                │
│ │ │DataClients   │   │ │ │ │ │(Order Books) │   │  │ │ `crates/common/src/  │
│ │ │ (Adapters)   │   │ │ │ │ └──────────────┘   │  │ │ cache`               │
│ │ └──────────────┘   │ │ │ │ ┌──────────────┐   │  │ │                      │
│ │                    │ │ │ │ │Order Emulator│   │  │ │ Clock                │
│ │                    │ │ │ │ └──────────────┘   │  │ │ `crates/common/src/  │
│ │                    │ │ │ │ ┌──────────────┐   │  │ │ clock.rs`            │
│ │                    │ │ │ │ │Fee Models    │   │  │ │                      │
│ │                    │ │ │ │ └──────────────┘   │  │ │ Event Store          │
│ │                    │ │ │                      │  │ │ `crates/event_store` │
│ └────────────────────┘ │ └─────────────────────┘  │ │                      │
│                        │                           │ │ Persistence          │
│ Option Chains          │                           │ │ `crates/persistence` │
│ `crates/data/src/      │                           │ │                      │
│ option_chains`         │                           │ └──────────────────────┘
│                        │
│ DeFi Support           │
│ `crates/data/src/defi` │
│                        │
└────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     Message Bus & Event Dispatch                          │
│                  `crates/common/src/msgbus`                               │
│  ┌──────────────────────────────────────────────────────────────┐        │
│  │ TypedRouter/TypedEndpoints - Topic-based message routing    │        │
│  │ Switchboard - High-performance event multiplexing           │        │
│  │ Handlers - Subscribers to market data and events            │        │
│  └──────────────────────────────────────────────────────────────┘        │
└───────────────┬──────────────────────────────────────────┬────────────────┘
                │                                          │
     ┌──────────▼──────────────┐         ┌────────────────▼────────┐
     │                         │         │                         │
     ▼                         ▼         ▼                         ▼
┌──────────────────┐  ┌──────────────┐ ┌───────────────┐  ┌─────────────────┐
│ RUNTIME MODES    │  │ INTEGRATION  │ │ INFRASTRUCTURE│  │ CORE UTILITIES  │
│                  │  │              │ │               │  │                 │
│ Live Trading:    │  │ Venue        │ │ SQL Backends  │  │ Data Types      │
│ `crates/live`    │  │ Adapters:    │ │ Redis Cache   │  │ `crates/model`  │
│                  │  │              │ │               │  │                 │
│ Backtesting:     │  │ • Binance    │ │ Event Store   │  │ Identifiers     │
│ `crates/backtest`│  │ • Bybit      │ │ Backends      │  │ Time/Math       │
│                  │  │ • Kraken     │ │               │  │ `crates/core`   │
│ Analysis:        │  │ • Deribit    │ │ Serialization │  │                 │
│ `crates/analysis`│  │ • DYdX       │ │               │  │ Common Types    │
│                  │  │ • And 16+ more               │  │ `crates/common` │
│ Indicators:      │  │              │ │               │  │                 │
│ `crates/        │  │ Sandbox for  │ │ Cryptography  │  │ Testing         │
│ indicators`      │  │ Paper Trading│ │ `crates/      │  │ `crates/testkit`│
│                  │  │              │ │ cryptography` │  │                 │
│ System:          │  └──────────────┘ └───────────────┘  └─────────────────┘
│ `crates/system`  │
│                  │
│ Plugin:          │
│ `crates/plugin`  │
└──────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **NautilusKernel** | Central orchestrator for kernel startup, shutdown, and lifecycle | `crates/system/src/kernel.rs` |
| **Trader** | Manages component registration, lifecycle, and coordination with kernel | `crates/system/src/trader.rs` |
| **DataEngine** | Processes market data feeds, aggregates bars, manages data clients | `crates/data/src/engine` |
| **ExecutionEngine** | Routes orders, manages order lifecycle, coordinates with venues | `crates/execution/src/engine` |
| **RiskEngine** | Calculates position risk, portfolio margin, enforcement limits | `crates/risk/src/engine.rs` |
| **Portfolio** | Maintains account and position state, P&L tracking | `crates/portfolio/src/portfolio.rs` |
| **MessageBus** | Routes events between components via typed topics/endpoints | `crates/common/src/msgbus` |
| **Cache** | In-memory store for market data, instruments, accounts, positions | `crates/common/src/cache` |
| **Clock** | Provides system timestamps with atomic synchronization | `crates/common/src/clock.rs` |
| **Strategy** | User-defined trading logic with event handlers | `crates/trading/src/strategy` |
| **VenueAdapter** | Connects to exchanges, transforms orders, processes fills | `crates/adapters/{venue}/src` |
| **DataAdapter** | Connects to data providers, streams market data | `crates/adapters/{provider}/src` |
| **OrderEmulator** | Emulates advanced order types not supported by venue | `crates/execution/src/order_emulator` |
| **MatchingEngine** | Simulates order matching for backtesting | `crates/execution/src/matching_engine` |
| **EventStore** | Persists and replays events for determinism | `crates/event_store/src` |

## Pattern Overview

**Overall:** Event-driven actor model with typed message routing

**Key Characteristics:**
- **Deterministic:** All trading logic produces identical results when replayed with same inputs
- **Single-threaded event loop:** All component callbacks execute synchronously in sequence
- **Zero-copy messaging:** Event references passed through message bus without cloning
- **Shared reference semantics:** Components use `Rc<RefCell<T>>` for internal state sharing
- **Component lifecycle:** Consistent state machine (PreInitialized → Ready → Running → Stopped/Disposed)
- **Trait-based abstractions:** Adapters implement common interfaces for venues and data providers

## Layers

**Core Primitives Layer:**
- Purpose: Foundational types, time, math, UUID, serialization
- Location: `crates/core/src`
- Contains: Time handling, UUID generation, math functions, serialization traits, correctness validators
- Depends on: Rust std
- Used by: All other crates

**Domain Model Layer:**
- Purpose: Trading domain types with type safety and validation
- Location: `crates/model/src`
- Contains: Events, orders, positions, instruments, accounts, identifiers
- Depends on: `nautilus-core`, `nautilus-serialization`
- Used by: Data, execution, portfolio, risk engines

**Common Services Layer:**
- Purpose: Shared runtime services (clock, cache, actor system, message bus)
- Location: `crates/common/src`
- Contains: Clock, cache, component lifecycle, actor registry, message routing, timers, logging
- Depends on: `nautilus-core`, `nautilus-model`
- Used by: Kernel, engines, strategies, adapters

**Execution Layer:**
- Purpose: Order routing, matching, fill processing, risk-constrained execution
- Location: `crates/execution/src`
- Contains: ExecutionEngine, OrderManager, MatchingEngine, OrderEmulator, order books, fee models
- Depends on: Common, model, serialization
- Used by: Kernel, backtest, live engines

**Data Layer:**
- Purpose: Market data ingestion, aggregation, bar generation, option chains
- Location: `crates/data/src`
- Contains: DataEngine, DataClients, aggregators, bar builders, option chain handlers
- Depends on: Common, model, serialization
- Used by: Kernel, backtest, live engines

**Portfolio & Risk Layer:**
- Purpose: Position tracking, P&L calculation, risk enforcement, margin management
- Location: `crates/portfolio/src`, `crates/risk/src`
- Contains: Portfolio state machine, position manager, risk calculator, margin enforcement
- Depends on: Common, model
- Used by: Kernel, execution engine

**System Kernel Layer:**
- Purpose: Central orchestration of kernel, trader, controller, event store integration
- Location: `crates/system/src`
- Contains: NautilusKernel, Trader, Controller, event store registration, startup/shutdown
- Depends on: All engine layers, common, model
- Used by: Live, backtest, CLI

**Runtime Modes:**
- **Live:** `crates/live/src` - Async runner for real-time trading
- **Backtest:** `crates/backtest/src` - Deterministic simulation with historical data replay
- **CLI:** `crates/cli/src` - Command-line interface for system operations

**Adapter Layer:**
- Purpose: Venue-specific order/trade integration and data provider connections
- Location: `crates/adapters/{venue}/src`
- Contains: ExecutionClient, DataClient implementations, venue-specific transformations
- Depends on: Common, model, execution, data
- Used by: Live and backtest modes

**Infrastructure Layer:**
- Purpose: Persistence backends (SQL, Redis), serialization, cryptography
- Location: `crates/infrastructure/src`, `crates/serialization/src`, `crates/cryptography/src`
- Contains: SQL cache databases, Redis clients, serde implementations, signing
- Depends on: Common, model
- Used by: Kernel, persistence layer

## Data Flow

### Primary Request Path (Live Market Event)

1. **Data Ingestion** (`crates/data/src/engine.rs:DataEngine::on_data()`)
   - Venue adapter receives market data (quote, trade, bar)
   - Publishes to data engine via message bus

2. **Data Processing** (`crates/data/src/engine.rs`)
   - DataEngine processes through aggregation handlers
   - Bar builders generate bar events
   - Publishes bars and quotes to cache and strategies

3. **Strategy Reaction** (`crates/trading/src/strategy/core.rs`)
   - Strategy receives data event (on_bar, on_quote)
   - Computes signal, decides to trade
   - Generates trading command (submit order)

4. **Order Submission** (`crates/execution/src/engine.rs:ExecutionEngine::submit_order()`)
   - ExecutionEngine receives TradingCommand
   - Creates Order, validates risk constraints via RiskEngine
   - Routes to venue adapter or matching engine

5. **Order Routing** (`crates/adapters/{venue}/src/execution.rs`)
   - ExecutionClient sends order to venue
   - Maintains local order state in OrderManager
   - Processes venue responses (accepted, rejected, filled)

6. **Fill Processing** (`crates/execution/src/engine.rs:ExecutionEngine::on_fill()`)
   - Receives ExecutionReport from venue
   - Updates OrderManager state
   - Updates Portfolio positions and cash
   - Publishes OrderFilled event to message bus

7. **Portfolio Update** (`crates/portfolio/src/portfolio.rs`)
   - Receives OrderFilled event
   - Updates position state
   - Recalculates P&L, margin usage
   - Risk engine updates enforcement limits

**State Management:**
- Cache maintains current market data (quotes, bars, orderbook snapshot)
- OrderManager maintains local order state independent of venue (for synchronization)
- Portfolio maintains atomic account and position state
- Risk engine caches enforcement limits updated per fill

### Backtesting Event Path

1. **Historical Data Loading** (`crates/backtest/src/data_client.rs`)
   - DataIterator loads venue data from disk
   - Converts to market data events

2. **Exchange Simulation** (`crates/backtest/src/exchange.rs`)
   - Simulated exchange receives orders
   - Uses MatchingEngine to match against historical data
   - Generates ExecutionReports with realistic latency/fills

3. **Event Loop** (`crates/backtest/src/engine.rs`)
   - Processes events chronologically (deterministic replay)
   - All timestamps from historical data
   - Strategy logic identical to live

4. **Results Accumulation** (`crates/backtest/src/accumulator.rs`)
   - Collects final results: trades, positions, P&L, statistics
   - Enables performance analysis

### Order Lifecycle State Machine

```
SUBMITTED (OrderManager) → ACCEPTED (Venue ACK)
                        → FILLED (Full execution)
                        → PARTIALLY_FILLED (Partial execution)
                        → CANCELED (User or venue cancel)
                        → REJECTED (Venue rejection)
```

Each state transition generates an OrderEvent published to message bus and cached.

## Key Abstractions

**Component:**
- Purpose: Defines unified lifecycle for all system entities
- Examples: `crates/common/src/component.rs`
- Pattern: State machine (PreInitialized → Ready → Running → Stopped/Disposed)

**Actor:**
- Purpose: Lightweight message-processing entities in the global registry
- Examples: Strategies, data handlers
- Pattern: Registry-based lookup with `Box<dyn Actor>`, downcasting via `as_any()`

**ExecutionClient:**
- Purpose: Abstract interface for order submission and fill reception
- Examples: `crates/adapters/{venue}/src/execution.rs`
- Pattern: Trait object implementing `ExecutionClient` protocol

**DataClient:**
- Purpose: Abstract interface for market data subscription and reception
- Examples: `crates/adapters/{provider}/src/data.rs`
- Pattern: Trait object implementing `DataClient` protocol

**MessageHandler:**
- Purpose: Typed subscribers to message bus topics
- Examples: `crates/common/src/msgbus/typed_handler.rs`
- Pattern: Function closures matching handler signature, registered to topic

**Cache:**
- Purpose: Single source of truth for market state and order state
- Examples: `crates/common/src/cache`
- Pattern: HashMap-based key-value store with get/set/delete methods

## Entry Points

**NautilusKernel:**
- Location: `crates/system/src/kernel.rs:NautilusKernel::builder()`
- Triggers: Direct instantiation via builder pattern
- Responsibilities: Initialization of all engines, trader registration, startup/shutdown orchestration

**Backtest Engine:**
- Location: `crates/backtest/src/engine.rs:BacktestEngine::run()`
- Triggers: Invoked from backtest CLI or Python API
- Responsibilities: Deterministic replay of historical data, accumulation of results

**LiveNode:**
- Location: `crates/live/src/node.rs:LiveNode::run()`
- Triggers: Invoked from live runner or Python API
- Responsibilities: Real-time data streaming, order execution, async event handling

**Strategy Entry:**
- Location: `crates/trading/src/strategy/core.rs:Strategy::on_bar()`, `Strategy::on_order_filled()`
- Triggers: Message bus event subscriptions
- Responsibilities: User-defined trading logic in response to data/fill events

**CLI Entry:**
- Location: `crates/cli/src/bin/`
- Triggers: Command-line invocation
- Responsibilities: System administration (database operations, event replay, analysis)

## Architectural Constraints

- **Threading:** Single-threaded event loop per instance. Live mode uses tokio async runtime for network I/O, but trading logic executes synchronously. Backtest is purely synchronous.
- **Global state:** Component registry (actors, strategies, algorithms) uses thread-unsafe `Rc<RefCell<T>>` and assumes single-threaded access. Global message bus uses `thread_local!` for unsafe access.
- **Circular imports:** None enforced; module structure prevents cycles via unidirectional dependencies (core → model → common → engines → system).
- **Memory model:** Strategies and components use `Rc<RefCell<T>>` for shared ownership. RefCell provides interior mutability but panics on borrow conflicts at runtime.
- **Error handling:** Most functions return `anyhow::Result<T>`. Panics used sparingly for invariant violations (e.g., mutex poisoning, component state violations).

## Anti-Patterns

### Direct Mutation Without Message Bus

**What happens:** Code mutates cache or portfolio state directly instead of publishing events.
**Why it's wrong:** Breaks event ordering, prevents event store replay, bypasses risk validation.
**Do this instead:** Publish event to message bus; handlers update state. Example: `ExecutionEngine::on_fill()` publishes `OrderFilled`, which triggers portfolio update via message handler.

### Synchronous Blocking I/O in Live Mode

**What happens:** Adapter calls blocking network I/O in message handler.
**Why it's wrong:** Blocks entire event loop, prevents other orders/data from processing.
**Do this instead:** Use tokio async tasks in `LiveNode` runner. Adapters queue I/O operations; runner schedules completions back to message bus.

### Storing References to Global State Without Rc

**What happens:** Function takes `&mut self` and stores pointer to cache/clock in local struct.
**Why it's wrong:** Violates Rust's borrow checker; causes dangling references.
**Do this instead:** Store `Rc<RefCell<Clock>>` or `Rc<RefCell<Cache>>` in component struct, obtained during registration.

## Error Handling

**Strategy:** Errors propagated via `anyhow::Result<T>`. Component state transitions fail gracefully with error logging.

**Patterns:**
- Order submission validates risk constraints; if violated, returns error (order not submitted)
- Venue adapter connection failures logged and component transitioned to Degraded state
- Event store replay stops on corrupted entry with clear error message
- Cache lookups return `Option<T>`; handlers check for instrument existence before trading

## Cross-Cutting Concerns

**Logging:** Via `slog` crate with structured logging. Each component logs state transitions and errors. Configuration in `crates/common/src/logging/`.

**Validation:** Correctness checkers in `crates/core/src/correctness.rs` validate prices, quantities, identifiers. Called at order submission, fill processing.

**Authentication:** Adapters implement auth via API keys/secrets from environment. No centralized auth; each venue adapter handles its credentials.

**Event Ordering:** Message bus guarantees FIFO delivery within topic. Backtest engine guarantees chronological replay by sorting events.

---

*Architecture analysis: 2026-06-13*
