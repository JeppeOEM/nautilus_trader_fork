# Requirements: dYdX Research Platform

**Defined:** 2026-06-27
**Core Value:** Continuous, reliable collection of all dYdX market data into a Nautilus-catalog-compatible Parquet archive — feeding a live rankings dashboard and event chart explorer, with the same data powering production-quality backtests.

## v1.0 Requirements — Project Guardrails

### Fork Safety

- [ ] **FORK-01**: CLAUDE.md states that `nautilus_trader/` and `crates/` must never be modified — all troll/ code is additive-only
- [ ] **FORK-02**: CLAUDE.md states that `nautilus_trader` is used as a library only (types + catalog API); never instantiate `TradingNode`/`DataEngine` in troll/ code

### Design Principles

- [ ] **DESIGN-01**: CLAUDE.md enforces YAGNI — no abstractions, interfaces, factories, or config for values that don't change; no scaffolding for hypothetical future use
- [ ] **DESIGN-02**: CLAUDE.md enforces decoupling at natural component seams (collector / book features / strategy / backtest) — components depend on data types, not each other's internals
- [ ] **DESIGN-03**: CLAUDE.md states that deletion is preferred over addition when simplification is possible; boring over clever

### Memory Discipline

- [ ] **MEM-01**: CLAUDE.md prohibits loading full catalog slices into memory (e.g. `catalog.trade_ticks()` with no time bounds) — always use time-bounded queries or `BacktestDataConfig` streaming
- [ ] **MEM-02**: CLAUDE.md states that non-configured coins are in-memory rolling-window only; no unbounded accumulation
- [ ] **MEM-03**: CLAUDE.md requires generator/iterator patterns for large data pipelines; no materializing full instrument sets into lists

### Testing

- [ ] **TEST-01**: CLAUDE.md defines when tests are required: any function doing financial calculations (OFI, imbalance, microprice, precision conversion) must have unit tests; integration paths that touch Nautilus types or catalog must have integration tests
- [ ] **TEST-02**: CLAUDE.md states that trivial glue code (config parsing, logging setup, simple data routing) does not require tests — YAGNI applies to tests too
- [ ] **TEST-03**: CLAUDE.md specifies test style: no mocking Nautilus internals (real objects or skip the test); pytest, minimal fixtures, one assertion per logical claim

### Code Readability

- [ ] **READ-01**: CLAUDE.md enforces readable Python: functions under ~30 lines, names that explain intent, no clever one-liners that need decoding
- [ ] **READ-02**: CLAUDE.md states comments explain WHY (hidden constraint, subtle invariant, workaround) — not WHAT; well-named code explains itself
- [ ] **READ-03**: CLAUDE.md enforces type hints on all function signatures in troll/ code

### Nautilus Usage Patterns

- [ ] **NAUT-01**: CLAUDE.md documents correct Price/Quantity construction: always `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()` — never `Price(decimal, precision)` for re-stamping (known silent precision bug)
- [ ] **NAUT-02**: CLAUDE.md states all data written via `ParquetDataCatalog.write_data()` — no hand-rolled Parquet schemas
- [ ] **NAUT-03**: CLAUDE.md states backtests use `BacktestNode` + `BacktestDataConfig` — no custom simulation engine; `ImportableStrategyConfig` by string path for sweeps

## v2 Requirements (deferred to future milestones)

### Feature Work

- Rankings table with full microstructure metrics and sortable columns
- Configurable rolling window per coin (config.toml, global + per-coin override)
- Chart explorer: signal threshold search + event density search + coin selector
- Full parameter sweep across OFI window / thresholds, multi-strategy comparison
- Fix `test_ofi_strategy.py` fatal abort (Nautilus kernel crash in test context)
- Dashboard runnable standalone (`python -m ml_signals.dashboard`)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Live trading execution | Future milestone after strategy validation |
| WebSocket/SSE live chart streaming | Time-window explorer covers current need |
| Custom exchange adapters beyond dYdX | Separate milestone |
| DuckDB / SQL query UI | Pandas via Nautilus catalog API covers same need |
| Linting/formatting config (ruff etc.) | Already handled by repo-level pyproject.toml |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FORK-01 | Phase 1 | Pending |
| FORK-02 | Phase 1 | Pending |
| DESIGN-01 | Phase 1 | Pending |
| DESIGN-02 | Phase 1 | Pending |
| DESIGN-03 | Phase 1 | Pending |
| MEM-01 | Phase 1 | Pending |
| MEM-02 | Phase 1 | Pending |
| MEM-03 | Phase 1 | Pending |
| TEST-01 | Phase 1 | Pending |
| TEST-02 | Phase 1 | Pending |
| TEST-03 | Phase 1 | Pending |
| READ-01 | Phase 1 | Pending |
| READ-02 | Phase 1 | Pending |
| READ-03 | Phase 1 | Pending |
| NAUT-01 | Phase 1 | Pending |
| NAUT-02 | Phase 1 | Pending |
| NAUT-03 | Phase 1 | Pending |

**Coverage:**
- v1.0 requirements: 17 total
- Mapped to phases: 17
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-27*
*Last updated: 2026-06-27 after initial definition*
