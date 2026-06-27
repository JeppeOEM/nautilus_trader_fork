# dYdX Research Platform

## What This Is

A standalone research platform for dYdX perpetual markets running on the `pony` branch inside `nautilus_trader_fork`. It continuously collects live market data from all dYdX instruments into a Nautilus-compatible Parquet catalog, provides a live rankings dashboard showing configurable microstructure metrics per coin over a rolling window, an event chart explorer for time-window-based microstructure analysis with signal and density search, and a Nautilus-native backtest framework for OFI and other microstructure strategies. Never touches `TradingNode`/`DataEngine` for collection — uses the dYdX Rust/PyO3 clients directly in a standalone asyncio loop to avoid the documented unbounded-queue OOM bug.

## Core Value

Continuous, reliable collection of all dYdX market data into a Nautilus-catalog-compatible Parquet archive — feeding a live rankings dashboard and event chart explorer that make it immediately obvious which coins deserve attention, with the same data powering production-quality backtests via Nautilus's native BacktestNode.

## Current Milestone: v1.0 Project Guardrails

**Goal:** Write a `troll/CLAUDE.md` that gives Claude clear, enforceable rules for working on this codebase — preventing over-engineering, memory leaks, and Nautilus core contamination before any feature work starts.

**Target features:**
- Fork safety rule: never touch `nautilus_trader/` or `crates/`
- YAGNI / no over-engineering rule
- Memory discipline rule: streaming/windowed patterns, no bulk catalog loads
- Test rule: unit + integration when calculations must be correct; skip for trivial glue
- Readable Python rule: short functions, clear names, no clever tricks
- Decoupling rule: separate concerns at natural seams so components change independently

## Requirements

### Validated

- ✓ Data collector (`troll/dydx_collector/`) captures trades, order book deltas, bars, mark/index prices, funding rates, and open interest for all dYdX instruments using `nautilus_pyo3` clients directly — existing
- ✓ Collector writes to `ParquetDataCatalog` via Nautilus's own `write_data()` API (zero custom schema, catalog-compatible by construction) — existing
- ✓ Docker split: durable `nautilus-trader-base` image + thin `collector.dockerfile` app layer (rebuilds in seconds) — existing
- ✓ All-instruments mode: empty config instruments list = subscribe to every dYdX perpetual — existing
- ✓ Config-driven instrument list with per-coin bar intervals; hot-reload without restart — existing
- ✓ Open interest collected via custom `DydxOpenInterest(Data)` type + Arrow/Parquet registration — existing
- ✓ Book feature computation: `DepthProfile`, `BookImbalance`, `CancellationTracker`, `BookFeatures` from L2 deltas — existing
- ✓ OFI strategy (`OFIStrategy` + `OFIStrategyConfig`) with cumulative delta, mid-layer confirmation, trend EMA filters — existing
- ✓ Basic backtest runner: `BacktestNode` with `OrderBookDelta`, `TradeTick`, `Bar` via `BacktestDataConfig` streaming — existing
- ✓ Dashboard skeleton: rankings, history, chart pages with background fast/slow compute threads — existing

### Active

- [ ] Configurable rolling window per coin (default 5 min, global override + per-coin override in config.toml) for live metrics compute
- [ ] Rankings table: sortable by any column (click header), all microstructure metrics (OFI, microprice, spread, depth, imbalance, cancel pressure, pct 1h/24h, volatility, funding rate, OI where available)
- [ ] All non-configured coins use rolling-window in-memory data only; configured coins accumulate forever in Parquet
- [ ] Chart explorer: search by signal threshold (jump to events where OFI/imbalance crossed a configurable level) and by event density (find busy periods)
- [ ] Chart explorer: coin/instrument selector for any collected instrument
- [ ] Full Nautilus backtest capabilities: parameter sweep across OFI window / thresholds, multi-strategy comparison, `ImportableStrategyConfig` by string path
- [ ] Fix `test_ofi_strategy.py` fatal abort (Nautilus kernel `__init__` crash in test context)
- [ ] Dashboard runnable standalone (`python -m ml_signals.dashboard`) against any catalog path

### Out of Scope

- Live trading execution — future milestone after strategy validation
- WebSocket/SSE live chart streaming — time-window explorer covers current need
- Custom exchange adapters beyond dYdX (Bybit etc.) — separate milestone
- DuckDB / SQL query UI — removed earlier; pandas/Jupyter via catalog API covers querying

## Context

- Repo: `nautilus_trader_fork`, branch `pony`, personal code under `troll/` alongside `ml_signals/`
- `troll/dydx_collector/`: standalone data collector (asyncio, no TradingNode)
- `troll/ml_signals/`: analytics, dashboard, strategies, backtests
- Fork safety: never modify `nautilus_trader/` or `crates/` — collector and strategies are additive-only
- The `TradingNode`/`DataEngine` path was deliberately rejected (documented OOM bug under high load at 50-100 instruments, confirmed on `gg` branch after 5 debug cycles). The PyO3 Rust client is used directly instead.
- Price/quantity integrity: always use `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()` — never `Price(decimal, precision)` for re-stamping (known silent precision bug in this Nautilus version)
- dYdX L2 is aggregated (no L3/MBO) — queue composition and single-order presence are not available

## Constraints

- **Language**: Python only — no new Rust or Go
- **Fork safety**: Never modify `nautilus_trader/` or `crates/`
- **Catalog format**: All data written via `ParquetDataCatalog.write_data()` — no hand-rolled Parquet schemas
- **Style**: "ponytail" (lazy/minimal) — no GSD ceremony for one-liners, no premature abstraction
- **Backtest runtime**: Nautilus `BacktestNode` only — no custom simulation engine

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Standalone asyncio collector, no TradingNode | Avoids documented OOM/wedge bug in DataEngine under 50-100 instrument load | ✓ Good — live-verified |
| PyO3 dYdX clients used directly | Gets Rust reconnect/throttle/decode for free without DataEngine | ✓ Good |
| ParquetDataCatalog.write_data() for all types | Zero conversion step, catalog-compatible by construction | ✓ Good |
| Rolling-window in-memory for non-config coins | Avoids unbounded Parquet growth for coins not under study | — Pending |
| BacktestNode + BacktestDataConfig (not BacktestEngine) | Streaming from catalog, supports parameter sweeps and time-range filtering | ✓ Good |
| DuckDB removed | Pandas via Nautilus catalog API covers same need without extra service | ✓ Good |

---
*Last updated: 2026-06-27 after milestone v1.0 start*

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state
