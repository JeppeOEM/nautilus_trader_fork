# CLAUDE.md

## Project

**Multi-Venue Market Data Collector** — `troll/collector_core/` plus `troll/dydx_collector/`,
`troll/bybit_collector/`, `troll/hyperliquid_collector/`.

Standalone Python asyncio services, one per venue, each subclassing the shared
`collector_core.collector.Collector` (Epic 22). Each connects directly to its venue's
Rust/PyO3 HTTP and WebSocket clients (`nautilus_pyo3.DydxHttpClient`/`DydxWebSocketClient`
and the Bybit/Hyperliquid equivalents, wrapped by that venue package's duck-typed
`client.py`), bypassing `TradingNode`/`Strategy`/`DataEngine` entirely, and archives market
data continuously into one shared `ParquetDataCatalog` in the exact format Nautilus expects.
Runs in Docker alongside Dozzle for live log visibility.

**Core value:** reliable, continuous capture of dYdX, Bybit and Hyperliquid market data into
one Nautilus-catalog-compatible Parquet archive, decoupled from Nautilus's live-trading
runtime. That runtime has a documented unbounded-queue-growth + shutdown-wedge bug under
sustained high message load (`nautilus_trader/live/data_engine.py`, `live/enqueue.py`'s
`ThrottledEnqueuer`) that OOM-crashed an earlier `Strategy`/`TradingNode` recorder on the
`gg` branch. Each collector reuses the venue adapter's Rust connection/reconnect/throttle/
decode logic directly, so it gets battle-tested networking without the buggy `DataEngine`.

### What is collected

- 1-second book/trade snapshots (top-20 levels plus per-side volume), mark/index price,
  funding rate, open interest, instrument definitions.
- Individual trades are folded into the second's snapshot rather than stored raw (audit D-45).
- Raw order book deltas only for instruments opted in via `store_order_book_deltas`. This is
  **dYdX-only**: only `DydxConfig`'s per-instrument entry has it. Bybit's and Hyperliquid's
  `instruments` are a flat `tuple[str, ...]`.
- Bybit **spot** yields trades and book only. `bybit_collector/client.py` subscribes the
  ticker for `LINEAR` alone, so a spot id produces no mark/index price and no funding rate,
  and `bybit_collector/open_interest.py` builds `-LINEAR.BYBIT` ids only.

### Constraints

- **Language:** Python. Pivoted from an earlier Go rebuild, still present and untouched on
  the `go` branch, at the user's request.
- **Engineering standard:** this is a professional HFT trading platform and is built the
  right way, no corner-cutting. Correctness, tests, operational robustness and documented
  limits take priority over the shortest diff. A deliberate simplification is documented
  in-code as a `Known limit:` comment naming the ceiling and the upgrade path, never left
  implicit.
- **Architecture:** no `TradingNode`/`Strategy`/`DataEngine`. A plain asyncio class
  (`troll/collector_core/collector.py`'s `Collector`, subclassed per venue) owns its own
  loop, buffer and flush timer. A venue package supplies a duck-typed client plus at most a
  few hook overrides; the write gate itself is never overridden. `nautilus_trader` is used
  purely as a library (domain types + `ParquetDataCatalog.write_data()`), never as a live
  runtime. The one sanctioned exception is `troll/live_paper/`; see `troll/CLAUDE.md`.
- **Repo location:** lives inside `nautilus_trader_fork` under `troll/`, co-located with
  this repo's git history rather than in a separate repo.
- **Fork safety:** never modify `nautilus_trader/` or `crates/`. The collector is new,
  additive code only.
- **Catalog compatibility:** output Parquet matches Nautilus's `ParquetDataCatalog`
  schema/partitioning exactly because it is written via the catalog's own `write_data()`,
  not a hand-rolled schema. It loads directly into backtests with zero conversion.
- **Deployment:** Docker Compose (`troll/docker-compose.yml`). One durable base image,
  `nautilus-trader-base`, built from `.docker/nautilus_trader.dockerfile`'s `application`
  target and rebuilt only when `nautilus_trader` core/deps change. Three thin layers rebuild
  in seconds: `troll/collector.dockerfile` (one image, one service per collector via
  different `command:`, all writing the same catalog root), `troll/data_api.dockerfile`
  (separate because it runs a Node frontend-build stage) and `troll/live_paper.dockerfile`.
  Plus a Dozzle container for logs.
- **Open interest** arrives differently per venue and is a per-venue investigation, never
  an assumption:
  - dYdX: dropped by the PyO3 bindings on both REST and WS markets-channel paths, so it is
    fetched by a stdlib `urllib` poll against the public indexer
    (`troll/dydx_collector/open_interest.py`).
  - Bybit: dropped on the linear-ticker WS path, so likewise a REST poll
    (`troll/bybit_collector/open_interest.py`). Bybit spot has no open interest at all.
  - Hyperliquid: forwarded over the WebSocket (`subscribe_open_interest`), so no poll.
  - All land in the one shared `collector_core.open_interest.OpenInterest` custom `Data`
    type (story 22.3), registered for Arrow/Parquet serialization.
- **Rate limits:** dYdX relies on the Rust WebSocket client's built-in subscribe throttle
  (2/sec) and reconnect handling; no custom throttling. Bybit and Hyperliquid needed no cap
  for the instrument counts collected (stories 19.3/19.4). A new venue's limits are part of
  the wire-behaviour investigation in `troll/CLAUDE.md`'s "Adding a venue", not inherited.
- **Price/quantity integrity:** never derive a value's stored precision from its own digit
  count (e.g. `Decimal.normalize()`), and never round-trip a market data value through
  `float` before it is safely inside a `Price`/`Quantity`.
  - Real incident: dYdX's mark/index feed derives each tick's `Price.precision` from that
    value's decimal count after stripping trailing zeros (`crates/adapters/dydx/src/common/
    parse.rs`'s `parse_price`), so consecutive ticks for one instrument can carry different
    precision labels, and `ParquetDataCatalog` correctly refuses to read/merge files whose
    labels disagree. Fixed in `troll/dydx_collector/client.py`'s `_at_fixed_precision()`,
    which re-stamps every mark/index price at `nautilus_pyo3.FIXED_PRECISION` via
    `Decimal.scaleb()` + `Price.from_raw()`. dYdX-only: Bybit and Hyperliquid parse at the
    instrument's constant precision.
  - `Price(decimal, precision)` has a real bug in this nautilus_trader version for some
    combinations: `Price(Decimal("61090.59855"), 16)` silently returns
    `61090.5985500000026624`. Use `Decimal.scaleb()` + `Price.from_raw()`/
    `Quantity.from_raw()` whenever re-stamping at a different precision.

### Development philosophy

- **This is a full project, not a one-off script.** The catalog grows continuously,
  strategies will multiply, and live trading via `TradingNode` is the eventual destination.
  Write every component to survive that growth.
- **Use Nautilus built-ins first.** Before writing custom data loading, scheduling,
  reporting, aggregation or indicator code, check whether `BacktestNode`,
  `BacktestDataConfig`, `TradingNode`, `DataClient` or another primitive already covers it.
  Wrapping Nautilus is correct; duplicating it is not.
  - Indicators: `nautilus_trader.indicators` is a full streaming Cython TA library (SMA/EMA/
    WMA/Hull/Adaptive MA, RSI, MACD, Stochastics, CCI, ATR, Bollinger/Donchian/Keltner, OBV,
    VWAP, Ichimoku, more), `O(1)` per event via `update_raw()`. Reach for these before
    `pandas-ta` or a hand-rolled version. Only write a custom `Indicator` subclass (as
    `troll/ml_signals/indicators.py` does for OFI/OBI/microprice) when no built-in covers it.
- **Future-proof the data pipeline.** Avoid loading entire catalog slices into memory
  (e.g. `catalog.trade_ticks()` with no time bounds). Prefer `BacktestDataConfig`
  streaming, which also enables parameter sweeps and time-range filtering.
- **Strategies are the product.** The collector and backtest plumbing exist to serve
  strategy research and eventually live execution. Keep strategy code clean and portable:
  `StrategyConfig` + `Strategy` subclass, importable by string path, no hard-wired paths.

### Signal architecture

Signals are computed from 1-second sampled snapshots (`DydxSecondSnapshot`, now shared by
every venue from `troll/collector_core/second_snapshot.py`), not from raw delta events. Store
raw inputs, compute signals on read. The full rule (SIGNAL-01) and the stored/derived field
split live in `troll/CLAUDE.md`.

## Working rules

`troll/CLAUDE.md` holds the binding rules for all code under `troll/`: fork safety, data
integrity, observability, testing, memory discipline, adding a venue, Nautilus usage
patterns. Read it before changing anything there.

## Tooling

- Python 3.12+, `uv` with the pinned `uv.lock`.
- `ruff format` + `ruff` at line length 100, `mypy` with `disallow_incomplete_defs`,
  enforced by pre-commit. Cognitive complexity limit 10.
- Absolute imports, one import per line, `nautilus_trader` as known first-party.
- All source files carry the LGPL-3.0 header; the pre-commit hook checks the year.

## Upstream reference

Conventions, crate layout, engine architecture and version pins for the upstream
`nautilus_trader`/`crates/` codebase are in `troll/docs/NAUTILUS_UPSTREAM_REFERENCE.md`. It is
not loaded automatically. Read it only when work requires understanding upstream source,
such as tracing adapter wire behaviour in `crates/adapters/` or a bug in
`nautilus_trader/live/`.
