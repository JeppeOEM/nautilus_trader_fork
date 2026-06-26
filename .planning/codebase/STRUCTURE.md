# Codebase Structure

**Analysis Date:** 2026-06-26

## Directory Layout

```
nautilus_trader_fork/
├── crates/                         # Rust core engine and adapters (48 crates)
│   ├── core/                       # Foundation (time, UUID, math, validation)
│   ├── model/                      # Trading domain types (orders, positions, instruments)
│   ├── common/                     # Shared runtime (clock, cache, actor system, msgbus)
│   ├── execution/                  # Order routing and matching
│   ├── data/                       # Market data ingestion
│   ├── portfolio/                  # Position and account state
│   ├── risk/                       # Risk enforcement
│   ├── system/                     # Kernel and trader orchestration
│   ├── live/                       # Tokio-based async runtime for live trading
│   ├── backtest/                   # Deterministic replay engine
│   ├── adapters/                   # Venue integrations (dydx, binance, kraken, etc.)
│   │   └── dydx/                   # dYdX Rust/PyO3 adapter
│   │       ├── src/http.rs         # HTTP client for instruments/markets
│   │       ├── src/websocket.rs    # WebSocket client for live data
│   │       └── src/python/         # PyO3 bindings to Python
│   ├── persistence/                # Parquet catalog, event store
│   ├── serialization/              # Arrow/MessagePack codecs
│   └── ... (other crates)
│
├── nautilus_trader/                # Python package (compiled via Cython + Rust bindings)
│   ├── backtest/                   # BacktestNode, BacktestEngine, BacktestDataConfig
│   ├── adapters/                   # Data/Execution clients for exchanges (Python wrappers)
│   │   └── dydx/                   # dYdX Python adapter (uses PyO3 bindings)
│   ├── persistence/                # ParquetDataCatalog, serialization
│   ├── model/                      # Domain types (Cython-compiled)
│   ├── trading/                    # Strategy base class
│   ├── indicators/                 # Built-in technical indicators
│   └── ... (other modules)
│
├── troll/                          # User code (separate git history, co-located in fork)
│   ├── dydx_collector/             # Standalone market data collector
│   │   ├── collector.py            # Main entry point; Collector class; event loop
│   │   ├── client.py               # DydxClient wrapper; message decoding; precision fixing
│   │   ├── config.py               # Config loading; hot-reload diffing
│   │   ├── open_interest.py        # REST polling; DydxOpenInterest(Data) type; Arrow schema
│   │   ├── test_client.py          # Unit tests for precision fixing
│   │   ├── prune_catalog.py        # Maintenance: delete old order_book_deltas
│   │   ├── config.toml             # TOML configuration file (hot-reloadable)
│   │   ├── collector.dockerfile    # Thin Docker image layered on base
│   │   ├── docker-compose.yml      # Container orchestration (collector + dozzle)
│   │   ├── Makefile                # Build/run/log targets
│   │   ├── catalog/                # Parquet data directory (mounted volume in Docker)
│   │   ├── README.md               # Usage documentation
│   │   └── notebooks/              # Jupyter analysis notebooks
│   │
│   └── ml_signals/                 # Strategy research and analysis
│       ├── example_strategy.py      # Simple LogisticTrendStrategy (backtest example)
│       ├── ofi_strategy.py          # Complex OFI signal stack strategy
│       ├── book_features.py         # Order book feature extraction (imbalance, depth, etc.)
│       ├── indicators.py            # Custom indicators (OrderFlowImbalance, etc.)
│       ├── candles.py               # Candlestick aggregation from ticks
│       ├── footprint.py             # Volume profile / market profile calculation
│       ├── backtest_dydx.py         # Backtest runner for dYdX collector data
│       ├── backtest_ofi.py          # Backtest runner for OFI strategy
│       ├── catalog_stats.py         # Inspect catalog (instruments, time ranges, data counts)
│       ├── metrics_store.py         # Time-series metric storage (SQLite)
│       ├── metrics_computer.py      # Real-time metrics computation (for dashboard)
│       ├── chart_data.py            # Data preparation for dashboard plots
│       ├── dashboard.py             # Streaming Plotly/Dash visualization
│       ├── test_*.py                # Unit tests (pytest)
│       └── __init__.py              # Empty module marker
│
├── .planning/                      # GSD planning documents
│   └── codebase/                   # Analysis outputs
│       ├── ARCHITECTURE.md         # System design and data flow
│       ├── STRUCTURE.md            # Directory layout and file purposes
│       ├── CONVENTIONS.md          # Coding style (not written here; see CLAUDE.md)
│       └── TESTING.md              # Test patterns (not written here)
│
├── .docker/                        # Dockerfile templates
│   └── nautilus_trader.dockerfile  # Multi-stage build: base + application targets
│
├── scripts/                        # Utility scripts (not collector-related)
├── tests/                          # System-wide test suite
├── docs/                           # Project documentation
├── examples/                       # Example strategies and backtests
│
└── pyproject.toml                  # Python package metadata, dependencies, build config

```

## Directory Purposes

**`crates/` (Rust core engine and adapters):**
- Purpose: High-performance trading engine in Rust with async runtime and type safety
- Contains: 26+ crates covering core types, orchestration, execution, data, adapters
- Key adapter: `crates/adapters/dydx/` (HTTP + WebSocket client for dYdX)
- Note: Compiled to Rust binary and exposed to Python via PyO3 bindings (`crates/pyo3/`)

**`nautilus_trader/` (Python package):**
- Purpose: Python public API for strategies, backtests, configuration
- Contains: Cython-compiled performance paths + pure Python wrappers
- Key modules:
  - `nautilus_trader/backtest/` - `BacktestNode`, `BacktestEngine`, `BacktestDataConfig`
  - `nautilus_trader/persistence/` - `ParquetDataCatalog` for catalog R/W
  - `nautilus_trader/trading/` - `Strategy` base class for user code
  - `nautilus_trader/adapters/dydx/` - dYdX adapter (Python wrapper around Rust client via PyO3)
- Installed: Via `pip install nautilus-trader` or local build

**`troll/dydx_collector/` (Market data collection):**
- Purpose: Standalone asyncio service collecting dYdX market data into Parquet catalog
- Contains: Core collector logic, WebSocket wrapper, config management, Docker setup
- Entry point: `collector.py:main()` invoked by Docker or CLI
- Data output: `./catalog/` directory (Parquet files) mounted to host or Docker volume
- Lifecycle: Runs continuously; graceful shutdown on SIGINT/SIGTERM; periodic flushing every 60s

**`troll/ml_signals/` (Strategy research and backtesting):**
- Purpose: Algorithms, indicators, and backtests using collected catalog data
- Contains: Strategy implementations, feature extraction, analysis tools, visualization
- Execution: Runs locally via `backtest_dydx.py` (replay) or `dashboard.py` (live metrics)
- Data input: Reads from `../dydx_collector/catalog/` via `ParquetDataCatalog`
- Note: Code is portable; strategies importable via string path in `BacktestNode` config

**`.planning/codebase/` (GSD analysis documents):**
- Purpose: Machine-readable architecture and structure for future planning/execution agents
- Contains: ARCHITECTURE.md (system design), STRUCTURE.md (file layout), and optional CONVENTIONS.md/TESTING.md
- Generated by: `gsd:map-codebase` agent on demand
- Used by: `gsd:plan-phase` and `gsd:execute-phase` agents to reference during work

## Key File Locations

**Entry Points:**
- `troll/dydx_collector/collector.py`: Collector runtime (asyncio main loop)
- `troll/ml_signals/backtest_dydx.py`: Backtest entry point for dYdX data
- `troll/ml_signals/dashboard.py`: Live metrics dashboard (Plotly/Dash)
- `troll/ml_signals/example_strategy.py:LogisticTrendStrategy`: Simple strategy template

**Configuration:**
- `troll/dydx_collector/config.toml`: Collector settings (network, instruments, flush interval)
- `troll/dydx_collector/docker-compose.yml`: Container orchestration
- `troll/dydx_collector/collector.dockerfile`: Application image layer
- `.docker/nautilus_trader.dockerfile`: Base image (Rust build + Python runtime)

**Core Logic:**
- `troll/dydx_collector/collector.py`: Buffer management, flush loop, reload loop, lifecycle
- `troll/dydx_collector/client.py`: PyO3 client wrapper, message dispatch, precision fixing
- `troll/dydx_collector/open_interest.py`: REST polling, custom Data type, Arrow schema
- `troll/ml_signals/book_features.py`: Order book imbalance, depth, cancellation tracking
- `troll/ml_signals/indicators.py`: Custom indicators (OrderFlowImbalance, etc.)
- `troll/ml_signals/ofi_strategy.py`: Complex multi-signal strategy (OFI + trend + filters)

**Testing:**
- `troll/dydx_collector/test_client.py`: Precision fixing validation
- `troll/ml_signals/test_*.py`: Unit tests for indicators, strategies, features (pytest)
- `tests/` (repo-wide): System-level tests (not collector-specific)

**Data & Persistence:**
- `troll/dydx_collector/catalog/`: Parquet files (TradeTick, OrderBookDeltas, Bar, etc.)
- `troll/dydx_collector/metrics.db`: SQLite metrics store (used by `metrics_computer.py`)

## Naming Conventions

**Files:**
- **Collector**: `collector.py` (main entry point, not `main.py`)
- **Clients**: `client.py` (DydxClient wrapper, not `clients.py` or `dydx_client.py`)
- **Configuration**: `config.py` (loading logic), `config.toml` (data file)
- **Tests**: `test_*.py` prefix (e.g., `test_client.py`, `test_indicators.py`)
- **Strategies**: `*_strategy.py` suffix (e.g., `ofi_strategy.py`, `example_strategy.py`)
- **Utilities**: `*_computer.py`, `*_store.py` for specific domains

**Directories:**
- **Lowercase snake_case**: `dydx_collector`, `ml_signals`, `book_features` (in imports)
- **Package marker**: `__init__.py` files present in all importable directories
- **No hyphenated names**: Use underscores only (e.g., `open_interest.py` not `open-interest.py`)

## Where to Add New Code

**New Market Data Type (e.g., custom funding events):**
1. Define type in `troll/dydx_collector/` (e.g., `custom_data.py`)
2. Implement Arrow schema (`.schema()` classmethod) and codec (`.to_dict()`, `.from_dict()`)
3. Register via `register_arrow()`
4. Add to buffer key logic in `collector.py:_buffer_key()` if needed
5. Wire into `client.py:DydxClient._handle_message()` to populate type
6. Test with `test_client.py` or new test file

**New Feature/Indicator for Strategies:**
1. Create file in `troll/ml_signals/` (e.g., `new_feature.py`)
2. Implement feature function: takes `OrderBook` or list of `OrderBookDeltas` → numeric value
3. Add unit tests in `troll/ml_signals/test_new_feature.py` (pytest)
4. Import and use in strategy (e.g., `ofi_strategy.py:OFIStrategy.on_orderbook_deltas()`)
5. Backtest via `backtest_dydx.py` or new backtest runner

**New Strategy:**
1. Create file in `troll/ml_signals/` (e.g., `my_strategy.py`)
2. Subclass `Strategy`; implement config dataclass (inherit from `StrategyConfig`)
3. Implement event handlers: `on_bar()`, `on_trade()`, `on_order_filled()`, etc.
4. Create backtest runner (similar to `backtest_dydx.py`) or use existing runner with string path
5. Example: `BacktestRunConfig` → `strategy_path="ml_signals.my_strategy:MyStrategy"`
6. Test with unit tests in `troll/ml_signals/test_my_strategy.py`

**New Analysis/Dashboard Widget:**
1. Add chart function to `troll/ml_signals/chart_data.py` or new file
2. Call from `troll/ml_signals/dashboard.py:create_*_figure()` or similar
3. Add Plotly `dcc.Graph` to Dash `app.layout` in `dashboard.py`
4. Test locally: `python -m ml_signals.dashboard` then visit `http://localhost:8765`

**Backtest Parameter Sweep:**
1. Modify `troll/ml_signals/backtest_dydx.py:run()` to accept parameter ranges
2. Loop over parameter combinations; collect results
3. Store results in `troll/ml_signals/metrics_store.py` or CSV
4. Visualize in Jupyter notebook under `troll/ml_signals/notebooks/`

## Special Directories

**`troll/dydx_collector/catalog/`:**
- Purpose: Parquet data files (market data archive)
- Generated: Yes (written by `ParquetDataCatalog.write_data()`)
- Committed: No (ignored in `.gitignore`; too large; user-specific)
- Structure: Flat list of Parquet files named by data type and date (e.g., `trade_ticks_20260626.parquet`)

**`troll/ml_signals/notebooks/`:**
- Purpose: Jupyter notebooks for exploratory analysis
- Generated: Yes (user-created)
- Committed: No (typically ignored; large cell outputs)
- Usage: Interactive exploration; not imported by other code

**`troll/dydx_collector/__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes (by Python interpreter)
- Committed: No (Git-ignored)
- Cleaned: Via `find . -name __pycache__ -exec rm -rf {} \;` or IDE tools

**`.planning/codebase/`:**
- Purpose: GSD agent-readable analysis documents
- Generated: Yes (by `gsd:map-codebase` agent)
- Committed: Yes (part of planning history)
- Regenerated on demand; old versions are tracked in git history

## Relative Imports and Module Paths

**Absolute imports (always use):**
- From `troll/` code: `from ml_signals.indicators import OrderFlowImbalance`
- From Nautilus: `from nautilus_trader.backtest.node import BacktestNode`
- Never use relative imports across package boundaries

**Working Directory Requirements:**
- Collector: Run from `troll/dydx_collector/` or set `PYTHONPATH=$REPO_ROOT`
- Backtest: Run from `troll/` root or set `PYTHONPATH=$REPO_ROOT`
- Example: `cd $REPO_ROOT && python -m ml_signals.backtest_dydx` or `cd troll && python -m ml_signals.backtest_dydx`

**Docker Working Directory:**
- Collector image: WORKDIR `/app/` (root of repo in container)
- Imports resolve correctly via container `PYTHONPATH`
- Config path: `/app/dydx_collector/config.toml` (bound from host)

---

*Structure analysis: 2026-06-26*
