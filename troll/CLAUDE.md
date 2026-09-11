# troll/ Working Rules

These rules govern all code under `troll/dydx_collector/` and `troll/ml_signals/`.
`nautilus_trader` is consumed as a library only — never as a live runtime — in those two
modules. **Exception:** `troll/live_paper/` is a separate, structurally isolated module
(architecture spine's AD-8 amendment) where `TradingNode`/`Strategy` usage is sanctioned —
it is the one place in `troll/` this file's `TradingNode`/`DataEngine` ban does not apply.
See `troll/live_paper/node.py`'s module docstring for the full rationale.

---

## Fork Safety

- **FORK-01** — Never modify `nautilus_trader/` or `crates/`. All `troll/` code is additive-only. The fork must stay untouched so upstream merges remain possible.
- **FORK-02** — `nautilus_trader` is a library here: use its domain types (`Price`, `Quantity`, `InstrumentId`, …) and `ParquetDataCatalog.write_data()`. Never instantiate `TradingNode` or `DataEngine` in `dydx_collector/`/`ml_signals/` code. **Why:** the live `DataEngine` has a documented unbounded-queue-growth + shutdown-wedge bug under sustained high-message-load that OOM-crashed the earlier `Strategy`/`TradingNode`-based recorder on the `gg` branch. **Does not apply to `live_paper/`** — that module's entire purpose is running an actual (paper or, behind an explicit separate gate, real) trading strategy via `TradingNode`, which AD-8 explicitly carves out as a distinct, sanctioned usage from the recorder-misuse this rule prevents.

---

## Version Control

- **GIT-01** — Never run `git push` (including force-push) without asking the user first and getting explicit confirmation, regardless of permission mode. Always state what would be pushed (branch, commits) before asking.

---

## Network Security

- **SEC-01** — **Every Docker port is localhost-only. No exceptions, ever.** `ports:` entries must be `"127.0.0.1:HOST:CONTAINER"` — never a bare `"HOST:CONTAINER"`/`"PORT"` (that publishes to `0.0.0.0`). If a service needs no host access at all, use `expose:` instead of `ports:`, or nothing. Remote access is via SSH tunnel (`ssh -L`) only, never a publicly reachable port — including reverse proxies. **Why:** Docker inserts its own `iptables` `ACCEPT` rules ahead of ufw's chain, so a bare port mapping is reachable from the public internet even with `ufw deny <port>`/`ufw default deny incoming` active — ufw never sees the connection, since Docker never routes it through ufw's INPUT chain. Enforced unconditionally by the `check-docker-port-binding` pre-commit hook (`.pre-commit-hooks/check_docker_port_binding.sh`) — no escape-comment override. `troll/docker-compose.yml`'s services already follow this: `redis`/`dozzle` bind `127.0.0.1:PORT:PORT`; `collector`/`dashboard`/`ranking_engine`/`bot_tui`/`live-paper` use `network_mode: host` with the app itself binding `127.0.0.1` (e.g. `dashboard.py`'s `web.run_app(..., host="127.0.0.1", ...)`) instead of a `ports:` mapping.

---

## Data Integrity

- **DATA-01** — **Correct data is the #1 priority. Never display stale or fabricated values as live market data.** If data is genuinely unavailable (e.g., during WS reconnect recovery when the Rust client re-subscribes instruments at 2/sec), the gap must be flagged visually rather than papered over with a flatline. **Implementation:** `collector._second_loop` uses `_STALE_BOOK_NS = 5s` to skip snapshot emission when no `OrderBookDeltas` have arrived for an instrument; `dashboard._coin_chart_json` inserts `None` at timestamp gaps > `_CHART_GAP_THRESHOLD_MS = 2.5s` so Plotly renders an honest break instead of a misleading horizontal line. When adding new data sources or display paths, apply the same principle: skip or flag, never silently perpetuate stale state.

- **DATA-02** — **No mysteries in data ingestion. Maximum assurance that ingested data is correct and lossless is non-negotiable — this is the most critical part of the system.** When ingestion produces wrong or missing data (a desynced book, a dropped message, a stale value), detecting it fast and recovering automatically is necessary but is **never sufficient on its own** — treating fast recovery as "solved" is symptom treatment, not a fix. The investigation is not done until the exact mechanism is known. No guessing, no stopping at "probably the venue's fault" or "probably fine now" without hard evidence.
  - **Standard of proof:** rule out hypotheses with real evidence, not code-reading alone. Cross-check against an independent source of truth before concluding anything — e.g. the venue's own REST API, or a second independent client with zero shared code path with our pipeline (both used in production to prove a local order-book desync was real and not venue-side crossing).
  - **Close observability gaps permanently, don't patch around them.** If a whole class of failure was invisible for lack of instrumentation (e.g. Rust-side `log::error!`/`warn!` calls being silently dropped because `nautilus_pyo3.init_logging()` was never called in a standalone script that doesn't go through `TradingNode`/Kernel startup), fix that gap once, permanently — don't work around not being able to see the failure.
  - **A staleness/health canary on your own detection loop is required, not optional**, wherever a detection-and-recovery mechanism depends on a periodic check running promptly (e.g. `_second_loop`'s wall-clock tick-lag warning) — otherwise the safety net's own reliability becomes exactly the kind of invisible failure this rule exists to prevent.
  - **Only two acceptable end states for an ingestion-correctness question:** (1) the exact mechanism is identified, and either fixed or proven — with hard, independently-verified evidence — to be outside our control (e.g. genuinely lost upstream of our client); or (2) it is still genuinely open, and must be labeled and tracked as such, never quietly treated as resolved just because the visible symptom got smaller, quieter, or faster to recover from.

- **DATA-03** — **A forced resync (unsubscribe/resubscribe to force a fresh snapshot, e.g. `collector._resync_book`) is a destructive, worst-case recovery — never a fix, and never evidence the underlying problem is understood or solved.** It throws away local state and rebuilds from scratch, which hides the actual data loss it's papering over (during the resync window that instrument has no book at all) and, if triggered often, indicates the root cause is still live and recurring, not resolved. Treat a high or rising forced-resync rate as an open DATA-02 incident, not as "the safety net is working." Prefer a resync-avoiding fix at the actual loss point over tuning the resync trigger (e.g. `_CROSSED_RESYNC_NS`) tighter/looser — tuning the trigger changes how fast the worst case is reached, not whether it's needed.

- **DATA-04** — **A crossed dYdX v4 orderbook is expected, architectural, and unpreventable by any client — confirmed via dYdX's own official docs and Indexer source, not inference.** dYdX v4 has no centralized orderbook: each validator holds its own in-memory, off-chain, pre-consensus book, matched against whichever block proposer's mempool is currently active (rotates every block). dYdX's own integration guide states outright that a client cannot prevent seeing a crossed book. Treat a crossed book as a normal, expected condition to correct — not a symptom that, absent other evidence, implies a local pipeline bug (contrast with DATA-02's standard, which still applies to a book that *never* uncrosses at all — see below).
  - **The correct resolution is dYdX's own, from its Indexer's `Roundtable` service (`uncross-orderbook.ts`), not a full resync:** tag every price level, when applied, with the message-id of whichever WS update last touched it (dYdX's own docs: `OrderBookDelta.sequence`/the WS envelope's connection-global `message_id`, applied **per price level**, not per-instrument — a different, valid use of the same field the per-instrument gap-detection removal in commit `944891bbba` correctly ruled out). When crossed, delete only the level with the older message-id (loop until uncrossed), via a synthetic `BookAction.DELETE` — never wipe the whole book for this.
  - **Forced resync (DATA-03) is a fallback only** — for when a level can't be tagged (e.g. immediately after a resubscribe) or a book stays crossed for far longer than active uncrossing should ever take. It is not the first response to an ordinary crossed book.
  - See `_bmad-output/planning-artifacts/research/technical-dydx-v4-orderbook-crossing-resolution-research-2026-09-06.md` for full sourcing (dYdX docs + Indexer source + this repo's own wire captures).

---

## Observability Rules

- **OBS-01** — **Zero book updates across N liquid instruments is a failure mode, not market behavior.** BTC/ETH/SOL perpetuals trade 24/7. If `buy_count == 0` and `sell_count == 0` and bid prices are frozen for all subscribed instruments for more than 30 seconds, the data pipeline has failed — diagnose it, do not accept it as "quiet market." Root cause seen in production: (a) `classify_liquidity` using `openInterest` (token units) instead of `volume24H` (USD), causing BTC=458 tokens to fail a `>= 100_000 USD` threshold; (b) `_bar_builder._books` only updated every 60s at flush time, making `_second_loop` read a 60-snapshot-old book.
- **OBS-02** — **OFI z-score changing while price is frozen does NOT prove the data pipeline is healthy.** The OFI z-score is a rolling 3600-point window mean/std — it evolves as historical values age off even when current OFI input is zero. A changing z-score with a frozen price is consistent with a dead book feed. Check `buy_count`, `sell_count`, and raw bid/ask changes across consecutive snapshots, not derived indicators.
- **OBS-03** — **Liquidity classification must use volume (USD), not open interest (tokens).** `openInterest` from dYdX's indexer is in base-token units. BTC at 458 tokens looks "illiquid" against a $100k threshold. Always classify by `volume24H` (already USD) or by `openInterest × oraclePrice`. The config key `liquidity_min_oi_usd` means "minimum USD liquidity" — enforce it with a USD-denominated field.

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
- **TEST-04** — **Warnings and deprecation notices are not noise — never dismiss them as cosmetic.** A deprecated API gets removed in some future dependency bump (pandas, nautilus_trader, etc.), silently breaking the code at the worst possible time — usually mid-upgrade, far from the original context. A warning is also often the only visible symptom of a real latent bug (a resource never cleaned up, a coroutine never awaited, a type coerced somewhere it shouldn't be). Treat every new warning surfaced by a test run the same as a failing test:
  - Identify its exact source (don't guess "probably from a dependency") and either fix it at that source, or if it's genuinely upstream (a library's own internal deprecated call with no workaround), record it explicitly — file/line, the warning text, and why it can't be fixed here yet — rather than letting it scroll by unacknowledged.
  - Never add a blanket `filterwarnings("ignore")` or similar suppression to make output quieter — that hides the next new warning too, not just the one you're currently looking at.
  - This is what "long-running, easy to maintain" means in practice: the software should still build and run cleanly after routine dependency upgrades, years from now, not accumulate deprecated-API debt that turns every future upgrade into an archaeology project.

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

---

## Signal Architecture: 1s-Based, Not Event-Driven

HFT signals are computed from **1-second sampled snapshots** (`DydxSecondSnapshot`), not from raw order book delta events.

**What is stored in Parquet (`DydxSecondSnapshot`):**
- Top-20 bid/ask levels: `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes` (lists)
- Per-side trade volume: `buy_volume`, `sell_volume`

**What is NOT stored — computed on read via `ml_signals/indicators.py`:**
- `microprice` = `Microprice().update_raw(bp, bs, ap, as_)` — derivable from level 0
- `spread` = `ask_prices[0] - bid_prices[0]` — derivable from level 0
- `ofi_N` = `MultiLevelOFI(levels=N)` replayed over consecutive snapshots
- `obi_N` = `MultiLevelOBI(levels=N).update_raw(bid_sizes, ask_sizes)`

**- SIGNAL-01** — If a value can be derived exactly from stored level data, do not store it. Store raw inputs; compute signals. Keeps the schema minimal and lets strategies try any N-level variant without re-collecting.

---

## Metrics Single Source of Truth (Web Dashboard ↔ bot_tui)

Every metric/indicator shown in both `ml_signals/dashboard.py` and `bot_tui` must trace back to exactly one calculation — never two independent implementations of the same formula, and never two independently-running instances of the same stateful indicator class.

- **SSOT-01** — Stateless, single-snapshot derivations (spread, mid price, CVD, volume_delta, avg_trade_size, microprice) live as plain functions/classes in `ml_signals/indicators.py`. Every UI computes them by calling that shared function on the snapshot it already holds — never a local inline reimplementation of the formula. Safe because the output is a pure function of the current snapshot: two processes calling the same function on the same input can never disagree.
- **SSOT-02** — Stateful/rolling/windowed metrics (OFI/OBI and their z-scores, volatility, pct_1h/pct_24h, EMA trend, `book_features` depth/imbalance/cancellation/cum_delta) must be computed exactly once, by exactly one long-running process, and published (Redis pub/sub or a shared store) for every other reader to consume verbatim. Never let two processes independently subscribe to the same raw feed and run their own copy of a rolling-window indicator, even via the identical shared class — differing startup time, differing window contents, and floating-point accumulation order will silently diverge the two numbers over time. `ranking_engine` is the sole owner of this class of computation (architecture AD-9); `dashboard`/`bot_tui` are pure readers of its published output, never independent computers of it.
- **SSOT-03** — Before adding any new metric to either UI, check whether it already exists in the other first. If so, locate and consolidate its current implementation onto one shared source before displaying it in the new place — never duplicate to move faster.
- **SSOT-04** — The coin ranking page is one feature with two renderers: `ml_signals/dashboard.py` (web) and `bot_tui` (TUI). Any functionality applied to it — new columns/metrics, sort/filter behavior, ranking logic changes — must land in both, backed by the same shared source (per SSOT-01/02). Treat a request to change "the ranking page" as covering both unless the user scopes it to one explicitly.
- **SSOT-05** — Same rule for the single-coin detail view: the metrics/indicators shown must match between web and `bot_tui`, per-coin. The web dashboard additionally has a per-coin page with visual graphs (charts/plots) — that page is web-only and is not duplicated in the TUI; `bot_tui`'s coin detail should instead surface a link/reference to the web graph page rather than reimplementing charting in the terminal.
