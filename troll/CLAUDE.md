# troll/ Working Rules

These rules govern all code under `troll/` (`dydx_collector/` and `ml_signals/`).
`nautilus_trader` is consumed as a library only — never as a live runtime.

---

## Fork Safety

- **FORK-01** — Never modify `nautilus_trader/` or `crates/`. All `troll/` code is additive-only. The fork must stay untouched so upstream merges remain possible.
- **FORK-02** — `nautilus_trader` is a library here: use its domain types (`Price`, `Quantity`, `InstrumentId`, …) and `ParquetDataCatalog.write_data()`. Never instantiate `TradingNode` or `DataEngine` in `troll/` code. **Why:** the live `DataEngine` has a documented unbounded-queue-growth + shutdown-wedge bug under sustained high-message-load that OOM-crashed the earlier `Strategy`/`TradingNode`-based recorder on the `gg` branch.

---

## Design Principles

- **DESIGN-01** — YAGNI. No abstractions, interfaces, factories, or config for values that don't change. No scaffolding for hypothetical future use. Build the simplest thing that solves the present problem.
- **DESIGN-02** — Decouple at natural seams: `collector` / `book_features` / strategy / backtest. Components depend on shared data types, not each other's internals. Never import a collector class from a strategy file.
- **DESIGN-03** — Prefer deletion over addition when simplification is possible. Prefer boring over clever. A function you can read in 10 seconds is better than one that needs a comment.

---

## Memory Discipline

- **MEM-01** — Never load full catalog slices into memory. The anti-pattern is `catalog.trade_ticks()` with no time bounds — this will OOM on a large catalog. Always use time-bounded queries or `BacktestDataConfig` streaming instead.
- **MEM-02** — Non-configured coins are in-memory rolling-window only. No unbounded accumulation. If a coin is not in the config, its data must age out.
- **MEM-03** — Use generator/iterator patterns for large data pipelines. Do not materialize full instrument sets into lists when iteration suffices.

---

## Testing

- **TEST-01** — Tests are **required** for:
  - Any function doing financial calculations: OFI, imbalance, microprice, precision conversion.
  - Integration paths that touch Nautilus types or the catalog.
- **TEST-02** — Tests are **not required** for trivial glue code: config parsing, logging setup, simple data routing. YAGNI applies to tests too. If the function has no branching logic and no arithmetic, skip the test.
- **TEST-03** — Test style:
  - Never mock Nautilus internals — use real `Price`, `Quantity`, `OrderBook`, etc. objects, or skip the test entirely.
  - Use `pytest`; no class-based tests; minimal fixtures (helper functions with `_` prefix).
  - One assertion per logical claim. Return type `-> None` on all test functions.

---

## Code Readability

- **READ-01** — Functions should be under ~30 lines. Names explain intent (`depth_profile`, not `dp`). No clever one-liners that require decoding.
- **READ-02** — Comments explain **WHY**: hidden constraints, subtle invariants, workarounds. Well-named code explains itself — don't add comments that restate what the code does.
- **READ-03** — Type hints required on all function signatures in `troll/` code. Use `| None` syntax (Python 3.10+). `mypy` with `disallow_incomplete_defs = true` is enforced.

---

## Nautilus Usage Patterns

- **NAUT-01** — **Known silent bug:** `Price(decimal, precision)` silently returns a wrong value for some inputs. Example: `Price(Decimal("61090.59855"), 16)` returns `61090.5985500000026624`. **Never use this constructor for re-stamping.** Always re-stamp via:
  ```python
  raw = int(value.scaleb(new_precision))   # Decimal.scaleb() — exact integer arithmetic
  price = Price.from_raw(raw, new_precision)
  qty   = Quantity.from_raw(raw, new_precision)
  ```
  Use `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()` whenever setting a value at a different precision. Never round-trip a market-data value through `float` before it is safely inside a `Price`/`Quantity`.

- **NAUT-02** — All data written via `ParquetDataCatalog.write_data()`. No hand-rolled Parquet schemas. The catalog API owns the Arrow schema and partitioning; working around it breaks catalog reads.

- **NAUT-03** — Backtests use `BacktestNode` + `BacktestDataConfig`. No custom simulation engine. Reference strategies via `ImportableStrategyConfig` by string path so parameter sweeps and time-range filtering require no code changes.
