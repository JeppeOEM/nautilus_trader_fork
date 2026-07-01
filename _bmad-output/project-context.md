---
project_name: 'nautilus_trader_fork'
user_name: 'Mrqdt'
date: '2026-07-01'
sections_completed: ['Technology Stack & Versions', 'Language-Specific Rules', 'Framework-Specific Rules', 'Testing Rules', 'Code Quality & Style Rules', 'Development Workflow Rules', 'Critical Don''t-Miss Rules']
status: 'complete'
rule_count: 35
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- Python 3.12–3.14, `nautilus_trader` 1.229.0 used strictly as a **library** (never `TradingNode`/`Strategy`/`DataEngine` runtime) in `troll/`
- Dashboard stack: aiohttp (SSE server) + plotly (charts) + redis (pub/sub for live 1s data)
- Deployment: two-image Docker split — `nautilus-trader-base` (rebuilt rarely, core/deps only) + thin `collector.dockerfile` layered on top (bakes in `troll/dydx_collector/`, rebuilds in seconds)
- **Rebuild-order constraint**: rebuild the base image before the thin image whenever `nautilus_trader` core/deps change, or the thin image silently layers onto a stale base
- **Version-pin discipline**: `nautilus_trader` is pinned at 1.229.0 deliberately; bumping requires re-validating the PyO3 dYdX client bindings (precision bugs are version-sensitive — see Language-Specific Rules below)

## Critical Implementation Rules

### Language-Specific Rules

- **Precision re-stamping**: Never use `Price(decimal, precision)` / `Quantity(decimal, precision)` to change a value's precision — silently corrupts values via internal float64 round-trip for some inputs (e.g. `Price(Decimal("61090.59855"), 16)` → `61090.5985500000026624`). Always use `Decimal.scaleb(new_precision)` + `Price.from_raw()` / `Quantity.from_raw()`. Reference implementation: `troll/dydx_collector/client.py:45` `_at_fixed_precision()`.
- **Never derive precision from digit count** — don't use `Decimal.normalize()` on an incoming market value to infer its precision; dYdX's mark/index feed has inconsistent trailing-zero stripping per tick, which caused `ParquetDataCatalog` to reject mixed-precision files in production.
- **Never round-trip a market data value through `float`** before it's inside a `Price`/`Quantity` — exact `Decimal`/raw-integer arithmetic only.
- Type hints required on **all** function signatures in `troll/` — `mypy` runs with `disallow_incomplete_defs = true`. Use `X | None`, not `Optional[X]`.
- Imports: absolute only (`from nautilus_trader.model.objects import Price`), one import per line (isort `force single line`), 2 blank lines after the import block.

### Framework-Specific Rules

- **Never instantiate `TradingNode` or `DataEngine` in `troll/` code.** Live `DataEngine` has a documented unbounded-queue-growth + shutdown-wedge bug under sustained high-message-load that OOM-crashed an earlier `Strategy`/`TradingNode`-based recorder (see `gg` branch history). The collector (`dydx_collector/collector.py`) owns its own asyncio loop, buffer, and flush timer instead, calling the Rust `DydxHttpClient`/`DydxWebSocketClient` directly.
- **All data writes go through `ParquetDataCatalog.write_data()`** — no hand-rolled Parquet schemas. Working around the catalog API breaks catalog reads.
- **Backtests use `BacktestNode` + `BacktestDataConfig`** (see `ml_signals/backtest_dydx.py:48`), not a custom simulation loop — this streams the catalog in time-bounded chunks rather than loading it fully into memory.
- **Reference strategies via `ImportableStrategyConfig` by string path** (`ml_signals/backtest_dydx.py:57`), not by direct class import — enables parameter sweeps/time-range filtering with no code changes.
- **1s-snapshot signal architecture**: HFT signals (OFI, OBI, microprice, spread) are computed on read from `DydxSecondSnapshot` (top-20 book levels + per-side trade volume), never stored pre-computed. If a value is exactly derivable from stored level data (e.g. `microprice`, `spread`), it must not be persisted — see `ml_signals/indicators.py`.

### Testing Rules

- **Never mock Nautilus internals** — use real `Price`, `Quantity`, `OrderBook`, `OrderBookDelta`, etc., or skip the test entirely. Confirmed pattern: `ml_signals/tests/test_book_features.py` builds real `OrderBookDelta`/`BookOrder` objects via a `_delta()` helper, no mocks.
- `pytest` only, **no class-based tests**, minimal fixtures — prefer small helper functions with a leading underscore (e.g. `_delta(...)`).
- **One assertion per logical claim.** All test functions return `-> None`.
- Tests are **required** for: financial calculations (OFI, imbalance, microprice, precision conversion) and integration paths touching Nautilus types or the catalog.
- Tests are **not required** for trivial glue (config parsing, logging setup, simple routing) — skip if the function has no branching logic and no arithmetic.
- Test file naming: `test_*.py`, one file per module under test (`dydx_collector/tests/`, `ml_signals/tests/`).

### Code Quality & Style Rules

- **Function length**: keep functions under ~30 lines. Names must explain intent (`depth_profile`, not `dp`) — no clever one-liners that need decoding.
- **Comments explain WHY, not WHAT** — hidden constraints, subtle invariants, workarounds. Well-named code should not need comments restating its behavior.
- **YAGNI**: no abstractions, interfaces, factories, or config for values that don't change. No scaffolding for hypothetical future use.
- **Prefer deletion over addition** when simplifying — boring over clever; a function readable in 10 seconds beats one needing a comment.
- **Module boundaries are load-bearing**: `collector` / `book_features` / strategy / backtest depend on shared data types only, never on each other's internals. Never import a collector class from a strategy file.
- **Memory discipline**: never load unbounded catalog slices (`catalog.trade_ticks()` with no time bounds → OOM). Use time-bounded queries or `BacktestDataConfig` streaming. Non-configured coins are rolling-window only in memory — no unbounded accumulation.
- Formatting/linting: `ruff format` (line length 100), `ruff` linter, `mypy --disallow-incomplete-defs` — all enforced via pre-commit.

### Development Workflow Rules

- **Commit style**: Conventional-commits-ish — `type(scope): description`, e.g. `fix(collector+dashboard): guard against crossed book`, `feat(dashboard): add live price ticker`, `chore: add BMad Method tooling`. Scope names match the touched area (`collector`, `dashboard`, `03-01` for BMad story IDs).
- **Branch layout is meaningful, not disposable**: `develop` = main/PR target; `pony` = current `troll/dydx_collector` Python rebuild lineage; `go` = earlier Go rebuild attempt, left untouched; `gg` = prior `Strategy`/`TradingNode`-based recorder that hit the `DataEngine` OOM bug — historical reference, do not resume work there without reading why it was abandoned; `worktree-agent-*` = ephemeral BMad/agent worktrees.
- **Fork boundary**: `nautilus_trader/` and `crates/` are never modified — all `troll/` work is additive so upstream merges stay possible.
- Docker rebuild order matters: `make build-base` (rare, on core/deps change) must run before `make up` (`--build`, fast) picks up thin-image code changes — a stale base silently ships old core behavior.

### Critical Don't-Miss Rules

- **Crossed/touched book snapshots must be dropped, not displayed.** During WS reconnect, dYdX sends a CLEAR delta then replays the book level-by-level — sampling mid-replay can produce `best_bid >= best_ask`. Both `collector._second_loop` and `dashboard._coin_chart_json` must skip (not clamp or average) any snapshot where `bp >= ap`. Fixed 2026-06-30; regression tests in `test_collector_snapshot.py` / `test_dashboard_chart.py`.
- **A frozen/flat price line is not a bug to "fix" by interpolating** — if no `OrderBookDeltas` arrive, the book is genuinely stale and flatness is the correct signal. Never paper over a gap with a fabricated flatline; flag it visually instead (`_STALE_BOOK_NS = 5s` skip in collector, `_CHART_GAP_THRESHOLD_MS = 2.5s` → `None` gap in dashboard chart JSON).
- **Zero book updates across BTC/ETH/SOL-class instruments for >30s is a pipeline failure, not "quiet market"** — these trade 24/7. Diagnose (check `buy_count`/`sell_count`/raw bid-ask deltas across consecutive snapshots), don't accept it.
- **A changing OFI z-score does NOT prove the feed is alive** — it's a rolling 3600-point mean/std that drifts as history ages off even with zero current input. Frozen price + moving z-score = suspect a dead book, not healthy data.
- **Liquidity classification must use `volume24H` (USD) or `openInterest × oraclePrice`, never raw `openInterest`** — dYdX's indexer reports OI in base-token units, so e.g. BTC at 458 tokens looks "illiquid" against a naive USD threshold.

---

## Usage Guidelines

**For AI Agents:**

- Read this file before implementing any code in `troll/`
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

**For Humans:**

- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

Last Updated: 2026-07-01
