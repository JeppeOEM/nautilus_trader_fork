<!-- refreshed: 2026-06-26 -->
# Architecture

**Analysis Date:** 2026-06-26

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                      dYdX Collector (troll/)                         │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐   │
│  │  collector.py    │  │  ml_signals/     │  │ open_interest   │   │
│  │  (Main Loop)     │  │ (Backtest &      │  │ (REST Polling)  │   │
│  │  `Collector`     │  │  Analysis)       │  │                 │   │
│  └────────┬─────────┘  └──────────┬───────┘  └────────┬────────┘   │
│           │                       │                     │            │
│           ▼                       ▼                     ▼            │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │              In-Memory Buffer (defaultdict)                    │ │
│  │  Key: (type, id) → Value: list[TradeTick|Bar|DydxOpenInterest]│ │
│  └──────────────────────┬─────────────────────────────────────────┘ │
│                         │                                             │
└─────────────────────────┼─────────────────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────────────────┐
        │    ParquetDataCatalog.write_data()           │
        │    (Nautilus Persistence API)                │
        └──────────────────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────────────────┐
        │  ./catalog/  (Parquet files)                 │
        │  • TradeTick partition (by date)             │
        │  • OrderBookDeltas partition (by date)       │
        │  • Bar partition (by date)                   │
        │  • MarkPriceUpdate, IndexPriceUpdate         │
        │  • DydxOpenInterest                          │
        │  • Instrument definitions                    │
        └──────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **Collector** | Owns asyncio event loop; manages buffer, flush loop, config reload, lifecycle | `troll/dydx_collector/collector.py` |
| **DydxClient** | Thin wrapper around Rust PyO3 HTTP/WS clients; handles message decoding | `troll/dydx_collector/client.py` |
| **Config** | TOML loading and hot-reload diffing for instrument list changes | `troll/dydx_collector/config.py` |
| **DydxOpenInterest** | Custom Data type for open interest; REST polling + Arrow schema registration | `troll/dydx_collector/open_interest.py` |
| **BacktestNode** | Nautilus's deterministic replay engine; streams catalog data to strategies | `nautilus_trader/backtest/node.py` (framework) |
| **Strategy** | User-defined trading logic in ml_signals; responds to bar/trade events | `troll/ml_signals/{ofi_strategy,example_strategy}.py` |
| **BookFeatures** | Order book feature extraction (imbalance, depth, cancellation tracking) | `troll/ml_signals/book_features.py` |
| **Indicators** | Custom indicators (e.g., OrderFlowImbalance) used by strategies | `troll/ml_signals/indicators.py` |

## Pattern Overview

**Overall:** Decoupled data collection from live trading runtime.

**Key Characteristics:**
- **Bypass DataEngine**: Collector owns its own asyncio loop and callback, avoiding the buggy queue-growth path in `TradingNode`/`DataEngine`
- **Stream-first backtest**: Catalog is never fully loaded into memory; `BacktestDataConfig` streams chunks to strategies
- **Precision safety**: Mark/index prices are re-stamped at `FIXED_PRECISION` to ensure consistent Arrow schema labels across ticks
- **Hot-reloadable**: Instrument subscriptions can be added/removed via config changes without restarting

## Layers

**Collector Entry Point:**
- Purpose: Main asyncio event loop and lifecycle management
- Location: `troll/dydx_collector/collector.py`
- Contains: `Collector` class (event loop, buffer, flush logic, config reload)
- Depends on: `DydxClient`, `ParquetDataCatalog`, config loading
- Used by: `main()` function invoked by Docker or CLI

**Client Layer:**
- Purpose: Wrapper around dYdX's Rust PyO3 bindings; message decoding and callback dispatch
- Location: `troll/dydx_collector/client.py`
- Contains: `DydxClient` class, message handler, precision fixing for mark/index prices
- Depends on: `nautilus_pyo3` (Rust bindings), Nautilus model types
- Used by: `Collector` to subscribe/unsubscribe and receive data

**Configuration Layer:**
- Purpose: TOML file loading and hot-reload diffing for instruments
- Location: `troll/dydx_collector/config.py`
- Contains: `CollectorConfig`, `InstrumentEntry`, `load_config()`, `diff_instruments()`
- Depends on: Standard library (tomllib)
- Used by: `Collector` for startup and reload loop

**Open Interest Layer:**
- Purpose: Fetch and serialize open interest data not available in PyO3 bindings
- Location: `troll/dydx_collector/open_interest.py`
- Contains: `DydxOpenInterest(Data)` class with Arrow schema, REST polling, async wrapper
- Depends on: `urllib`, `asyncio`, `pyarrow`, Nautilus serialization
- Used by: `Collector._open_interest_loop()` for periodic polling

**Persistence Layer:**
- Purpose: Columnar storage of market data in Parquet format
- Location: `nautilus_trader/persistence/catalog.py` (Nautilus framework)
- Contains: `ParquetDataCatalog` with `write_data()` API
- Used by: `Collector._flush_once()` to persist buffered data
- Note: Not implemented in troll/; reuses Nautilus's existing API

**Backtest/Analysis Layer:**
- Purpose: Strategy research and performance simulation using historical catalog data
- Location: `troll/ml_signals/`
- Contains: Strategies (`ofi_strategy.py`, `example_strategy.py`), features (`book_features.py`), indicators, backtests (`backtest_dydx.py`, `backtest_ofi.py`)
- Depends on: Nautilus `BacktestNode`, `BacktestDataConfig`, `ParquetDataCatalog`
- Used by: Researchers to test signal hypotheses against recorded market data

## Data Flow

### Primary Collection Path (WebSocket Market Event)

1. **WebSocket message arrives** (`client.py:DydxClient._handle_message()`)
   - Rust PyO3 client emits decoded message (TradeTick, OrderBookDelta, Bar, or markets-channel update)

2. **Message classification and dispatch** (`client.py:_handle_message()`)
   - If `PyCapsule`: Decode via `capsule_to_data()` → Nautilus type
   - If `MarkPriceUpdate` or `IndexPriceUpdate`: Convert, re-stamp at `FIXED_PRECISION` via `_at_fixed_precision()`
   - If `FundingRateUpdate` or `InstrumentStatus`: Convert directly
   - Call `self._on_data(message)`

3. **Buffer append** (`collector.py:Collector._on_data()`)
   - `self._buffer[_buffer_key(data)].append(data)` where key = `(type, instrument_id/bar_type)`
   - O(1) operation; runs on event loop via `call_soon_threadsafe`

4. **Periodic flush** (`collector.py:Collector._flush_loop()`)
   - Every `flush_interval_seconds` (default 60s), call `_flush_once()`
   - Iterate buffer; for each key with items: `catalog.write_data(items)`, then clear

5. **Catalog write** (`ParquetDataCatalog.write_data()`)
   - Validates data types match catalog schema
   - Appends to existing Parquet partition (e.g., `trade_ticks/20260626.parquet`)
   - Logs on write failure; items are dropped (no retry queue)

### Open Interest Polling Path

1. **Timer fires** (`collector.py:Collector._open_interest_loop()`)
   - Every `open_interest_poll_seconds` (default 300s), call `fetch_open_interest(network)`

2. **REST request** (`open_interest.py:fetch_open_interest()`)
   - `asyncio.to_thread()` call to `_fetch_markets_json()` (blocking urllib)
   - Parses markets JSON response; extracts `{ticker, openInterest}` per market

3. **Create DydxOpenInterest items** (`open_interest.py:parse_open_interest()`)
   - For each market: `DydxOpenInterest(instrument_id, open_interest, ts_event, ts_init)`
   - Returns list → `_on_data()` for each → buffer → same flush path

### Configuration Hot-Reload Path

1. **Timer fires** (`collector.py:Collector._reload_config_loop()`)
   - Every `config_reload_seconds` (default 30s), load new config from disk

2. **Diff instruments** (`config.py:diff_instruments()`)
   - Compare old active set vs. new config; compute `added`, `removed` lists

3. **Subscribe/unsubscribe** (`collector.py:_subscribe()` / `_unsubscribe()`)
   - For added entries: await `client.subscribe_trades()`, `client.subscribe_orderbook()`, `client.subscribe_bars()`
   - For removed entries: await `client.unsubscribe_*()` for each subscription type
   - Throttling handled by Rust WebSocket client's built-in 2/sec limit

### Backtest Data Flow

1. **User configures BacktestNode** (`troll/ml_signals/backtest_dydx.py`)
   - `BacktestDataConfig(catalog_path, data_cls=TradeTick, instrument_id=...)`
   - `BacktestRunConfig(engine=..., venues=..., data=[...])`

2. **Node runs deterministic replay** (`BacktestNode.run()`)
   - Opens `ParquetDataCatalog` from disk
   - Streams `TradeTick` or `Bar` in chronological order
   - For each event: injects into engine, triggers strategy callbacks

3. **Strategy processes events** (`troll/ml_signals/ofi_strategy.py:OFIStrategy.on_trade()` / `on_bar()`)
   - Accumulates order flow imbalance from trades
   - Applies moving average filter and depth filters
   - Calls `submit_order()` to send `MarketOrder` to simulated venue

4. **Results accumulation and reporting**
   - Engine records fills, positions, P&L
   - User calls `engine.trader.generate_*_report()` for analysis

## Key Abstractions

**Collector:**
- Purpose: Owns event loop, buffer management, and async task coordination
- Examples: `troll/dydx_collector/collector.py:Collector`
- Pattern: Single-threaded asyncio with multiple concurrent tasks (flush, reload, open interest)

**DydxClient:**
- Purpose: Bridge between Rust PyO3 bindings and Python asyncio
- Examples: `troll/dydx_collector/client.py:DydxClient`
- Pattern: Thin wrapper with callback-driven message handling; zero internal state beyond HTTP/WS clients

**Data Buffer:**
- Purpose: In-memory accumulation of market data between flushes
- Examples: `defaultdict(list)` keyed by `(type, instrument_id)`
- Pattern: O(1) append, batch flush; simple and lock-free (single-threaded)

**DydxOpenInterest (Custom Data Type):**
- Purpose: Custom Nautilus `Data` subclass with Arrow schema for open interest
- Examples: `troll/dydx_collector/open_interest.py:DydxOpenInterest`
- Pattern: Schema + encoder/decoder methods; registered via `register_arrow()`

**Strategy (Nautilus Framework):**
- Purpose: User-defined trading logic with event handler lifecycle
- Examples: `troll/ml_signals/ofi_strategy.py:OFIStrategy`
- Pattern: Subclass `Strategy`, implement `on_bar()`, `on_trade()`, `on_order_filled()`, etc.

## Entry Points

**Collector Runtime:**
- Location: `troll/dydx_collector/collector.py:main()`
- Triggers: Docker container start or direct Python invocation
- Responsibilities: Load config, create `Collector`, spawn asyncio loop, handle signals (SIGINT/SIGTERM)

**Backtest Runtime:**
- Location: `troll/ml_signals/backtest_dydx.py:run()`
- Triggers: Direct invocation from Jupyter, test suite, or CLI
- Responsibilities: Create `BacktestRunConfig`, instantiate `BacktestNode`, run simulation, return results

**Analysis/Dashboard:**
- Location: `troll/ml_signals/dashboard.py:main()` (streaming visualization)
- Triggers: Manual invocation via Makefile `dashboard` target
- Responsibilities: Open catalog, compute features in real-time, stream to Plotly/Dash UI

## Architectural Constraints

- **Threading:** Single-threaded asyncio event loop; all sync work (buffer append, config reload) is O(1) and lock-free
- **Global state:** No global singletons; `Collector` instance owns all state (client, buffer, catalog, config)
- **Circular imports:** None; troll/ code imports from Nautilus but never vice versa
- **Memory model:** Buffer is ephemeral (cleared every flush); catalog writes are durable (Parquet)
- **Error handling:** Write failures logged and dropped (no retry queue); connection failures logged and handled by Rust client
- **Catalog schema stability:** Mark/index prices must have consistent precision label; enforced via `_at_fixed_precision()` re-stamping

## Anti-Patterns

### Storing Entire Catalog in Memory

**What happens:** Code loads all TradeTicks via `catalog.trade_ticks()` without time bounds, causing OOM on large catalogs.

**Why it's wrong:** The collector runs continuously and the catalog grows unboundedly. Early backtests work fine; after weeks, the script crashes.

**Do this instead:** Use `BacktestDataConfig` streaming or query with explicit time bounds (e.g., `catalog.trade_ticks(start=..., end=...)`)

### Tight Coupling to ParquetDataCatalog Path

**What happens:** Strategy code hardcodes `"troll/dydx_collector/catalog"` path; breaks if catalog moves or multipler users share repo.

**Why it's wrong:** Reduces portability; breaks during team handoffs or when running parallel backtests.

**Do this instead:** Pass catalog path as config parameter; use `BacktestDataConfig` with `catalog_path` field (as shown in `troll/ml_signals/backtest_dydx.py:run()`)

### Appending to Buffer on Network Thread

**What happens:** Message handler does slow work (e.g., database lookup, complex feature calculation) before appending to buffer.

**Why it's wrong:** Blocks the event loop thread; WebSocket client can't receive or send heartbeats; disconnection cascades.

**Do this instead:** Buffer append must be O(1). Slow work happens in separate tasks or during flush (e.g., order flow imbalance accumulates over time, not per message).

## Error Handling

**Strategy:** Log and continue; no retry queue or circuit breaker.

**Patterns:**
- **WebSocket message decode error**: Logged at DEBUG; handler skips message and continues
- **Catalog write failure**: Logged at ERROR; buffer items for that key dropped; flush continues
- **Open interest REST timeout**: Logged at ERROR; poll loop continues on next interval
- **Config reload parse error**: Logged at WARNING; old config retained; reload loop continues

## Cross-Cutting Concerns

**Logging:**
- Framework: Python `logging` module
- Config: Basic `logging.basicConfig(level=INFO)` in `collector.py:main()`
- Example: `logger.info(f"Subscribed {entry.id}")`, `logger.exception("Failed to write ...")`

**Validation:**
- Entry-level: No input validation on collector side; relies on Rust client to reject malformed messages
- Catalog-level: `ParquetDataCatalog` validates type matches schema before writing
- Strategy-level: Strategies validate order parameters before submission

**Configuration:**
- Method: TOML file on disk (`troll/dydx_collector/config.toml`)
- Hot-reload: Config reload loop reads file every 30s; diffs instrument list; subscribes/unsubscribes as needed
- Environment: Network selected via `DydxNetwork` enum (mainnet/testnet); passed to client and open interest fetcher

---

*Architecture analysis: 2026-06-26*
