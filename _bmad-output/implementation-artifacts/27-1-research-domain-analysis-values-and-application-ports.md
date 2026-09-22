# Story 27.1: `research/domain` analysis value objects and the `research/application` ports

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Story 24.4 (`platform/research/` exists, `ml_signals` strategies/runners/notebooks moved there). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` (AD-D1 research row, AD-D2 layering, AD-D10 no-recompute). Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.1".

## Story

As a strategy researcher,
I want the numbers every notebook and backtest report shows to be typed values with named invariants, computed by one function each,
so that a Sharpe ratio in a notebook, in a backtest report and in a bot's history page can never disagree, and a notebook cell never carries analysis logic of its own.

## Acceptance Criteria

1. **Given** `platform/research/` as Story 24.4 left it (`strategies/`, `run_backtest.py`, `watchlist.py`, `notebooks/`, `BACKTESTING.md`)
**When** the story ships
**Then** `platform/research/domain/` holds `returns.py` (`ReturnSeries`: a float64 array of simple returns plus `period_seconds`; invariant: one period per series, so annualisation is `sqrt(periods_per_year)` from the stored period and never a caller-supplied constant; constructors `from_prices(prices, ts_ns)` and `from_equity(EquityCurve)`; `resample(period_seconds)` compounds, never averages), `equity.py` (`EquityCurve`: strictly increasing `ts_ns`, finite values, `starting_balance`; `drawdowns()` returns the underwater series and every drawdown episode as `(peak_ts, trough_ts, recovery_ts | None, depth)`; `from_pnl_by_day` matches `performance_metrics.equity_returns` bit-for-bit, proven by a test), `trades.py` (`TradeLedger`: closed trades as `(instrument_id, entry_ts, exit_ts, side, qty, realized_pnl, fees)`; invariant `exit_ts >= entry_ts`; `realized_pnls()` is the exact input `performance_metrics.trade_stats` expects), `report.py` (`MetricReport`: the `performance_metrics.all_metrics` dict frozen into a typed record with `as_table()`; it calls `all_metrics`, it never reimplements a statistic), and `correlation.py` (`correlation_matrix(aligned_returns) -> CorrelationMatrix` over numpy only, pairwise-complete on `None` gaps, `lead_lag(a, b, max_lag)` cross-correlation by lag, and `cluster(matrix) -> list[list[str]]` single-linkage hierarchical clustering on `1 - rho`, numpy only); every class docstring names the invariant it protects (DESIGN-01), and `research/domain/` imports only the standard library, numpy, `kernel/` and `nautilus_trader.model` (the spine AD-D2 layering rule with numpy admitted as pure arithmetic, recorded in `test_boundaries.py`)

2. **Given** the catalog, `metrics.db` and `BacktestNode`
**When** the story ships
**Then** `platform/research/application/` holds `ports.py` (`MarketFrames`, `RankingHistory`, `BacktestRunner` as `typing.Protocol`s) and `frames.py`, `ranking_history.py`, `backtest_runner.py` implementing them: `MarketFrames.seconds(instrument_id, start, end) -> pandas.DataFrame` (the `DydxSecondSnapshot` columns plus derived `mid`, `spread`, `microprice`, `obi_N` computed through `kernel.indicators`' stateless functions and never inline), `MarketFrames.trades(instrument_id, start, end)`, `MarketFrames.bars(instrument_id, bar_seconds, start, end)` (from the candle store via `candles.application.window`, never a third seconds→bars fold), `MarketFrames.funding(...)`, `MarketFrames.open_interest(...)`, `MarketFrames.mark_index(...)`; every read is time-bounded (`start`/`end` are required, no defaults) and goes through `kernel.catalog_files` or the catalog's typed `query` with `start`/`end` (MEM-01); `RankingHistory.history(instrument_id, days)` reads `metrics.db` through the ranking query service and research computes no pct/volatility of its own (AD-D10); `BacktestRunner.run(RunSpec) -> RunResult` wraps `BacktestNode` + `BacktestDataConfig` (NAUT-03), takes strategies by `ImportableStrategyConfig` string path, and returns `EquityCurve`, `TradeLedger` and `MetricReport` built from the engine's `PortfolioAnalyzer` and the fills report, plus the run's `config_id`; `BacktestRunner.sweep(RunSpec, grid) -> list[RunResult]` runs one `BacktestNode` with one `BacktestRunConfig` per grid point and re-attributes results by `config_id` (the Story 2.4 attribution rule, `ml_signals/strategies/backtest_dydx.py:42-56`)

3. **Given** `platform/CLAUDE.md` TEST-01 and the DDD spine's AD-D1 research row ("none (consumer)")
**When** the story ships
**Then** `research/tests/` covers every domain function with hand-computable cases (a 3-point equity curve with a known drawdown, a two-series correlation of exactly `±1`, a lead-lag of a shifted copy equal to the shift, `from_pnl_by_day` equality against `performance_metrics.equity_returns`), `BacktestRunner` against a two-day synthetic catalog built with `ParquetDataCatalog.write_data()` (real Nautilus objects, no mocks, TEST-03), the DDD spine's AD-D1 research row reads "value objects for analysis results (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, `MonteCarloResult`); ports `MarketFrames`, `RankingHistory`, `BacktestRunner`" with a `[amended 2026-..: Story 27.1]` note and the invariant list, `test_boundaries.py`'s research rows admit `research.domain` → numpy and nothing else new, `research/tests` is already in both Makefile lists (24.4) and stays there, `ARCHITECTURE.md`'s module map names the two research layers, and `ml_signals/BACKTESTING.md`'s successor `research/BACKTESTING.md` documents `BacktestRunner` as the one way a notebook runs a backtest

## Tasks / Subtasks

- [ ] Task 1 — `research/domain/` value objects (AC: #1)
  - [ ] `returns.py`: `ReturnSeries` (frozen dataclass: `values: np.ndarray` float64, `ts_ns: np.ndarray` int64, `period_seconds: int`). `from_prices(prices, ts_ns)` (simple returns, `None`/NaN price → NaN return, never forward-filled), `from_equity(curve)`, `resample(period_seconds)` (compound `prod(1 + r) - 1` per bucket keyed on `ts_ns // period_ns`; raise on a period that is not a multiple of the current one), `annualisation_factor` (`sqrt(365 * 86400 / period_seconds)`: crypto trades 24/7, say so in the docstring), `rolling_sharpe(window)` delegating the per-window statistic to `performance_metrics.return_stats` (never a local mean/std formula). Docstring invariant: one period per series.
  - [ ] `equity.py`: `EquityCurve` (`ts_ns` strictly increasing, `values` finite, `starting_balance`); `__post_init__` raises on violation; `drawdowns()` → `(underwater: np.ndarray, episodes: list[DrawdownEpisode])`; `from_pnl_by_day(pnl_by_day, starting_balance)` that reproduces `ml_signals.performance_metrics.equity_returns` exactly (read that function first; it already defines the day-keyed equity semantics `live_paper/trade_history.py` relies on).
  - [ ] `trades.py`: `TradeLedger` of `ClosedTrade` records; `realized_pnls()`, `holding_times_s()`, `by_hour_of_day()`, `by_weekday()`; invariant `exit_ts >= entry_ts`.
  - [ ] `report.py`: `MetricReport.from_ledger_and_days(ledger, pnl_by_day, starting_balance)` = `performance_metrics.all_metrics(...)` frozen into typed fields; `as_table() -> list[tuple[str, float | None]]`; keys are exactly `all_metrics`' keys, asserted by a test so a new statistic must be added in `performance_metrics` first.
  - [ ] `correlation.py`: `align(series_by_id: dict[str, ReturnSeries]) -> AlignedReturns` (outer join on `ts_ns`, NaN where absent), `correlation_matrix(aligned) -> CorrelationMatrix` (pairwise-complete Pearson: for each pair use rows where both are finite; `ids` + `values` ndarray), `lead_lag(a, b, max_lag) -> list[tuple[int, float]]` (correlation of `a[t]` with `b[t+lag]`, positive lag means `a` leads), `cluster(matrix, threshold) -> list[list[str]]` (single-linkage agglomerative on distance `1 - rho`, numpy only, `O(n^3)` fine for `n <= 100`, say so as a `Known limit:` with the upgrade path).
  - [ ] All modules: stdlib + numpy + `kernel` + `nautilus_trader.model` imports only; no pandas in `domain/`.
- [ ] Task 2 — `research/application/` ports and services (AC: #2)
  - [ ] `ports.py`: `MarketFrames`, `RankingHistory`, `BacktestRunner` as `typing.Protocol`; `RunSpec` (catalog_path, instrument_ids, start, end, strategy_path, config_path, params, starting_balance, data: `"seconds" | "trades" | "bars:<spec>"`), `RunResult` (`config_id`, `EquityCurve`, `TradeLedger`, `MetricReport`, `pnl_by_day`, `iterations`, wall seconds).
  - [ ] `frames.py`: `CatalogFrames(MarketFrames)`. `seconds()` reads through `kernel.catalog_files` (the column projection of `query_second_ohlc`, extended to the 20-level arrays) or `ParquetDataCatalog.query(DydxSecondSnapshot, identifiers=[...], start=..., end=...)` — never without `start`/`end`; derived columns via `kernel.indicators.mid_price/spread/microprice` and `MultiLevelOBI`. `trades()` via `catalog.query(TradeTick, ...)`. `bars()` via `candles.application.window` (24.1) over `CANDLES_DIR`. `funding()`, `open_interest()` (`kernel.open_interest.OpenInterest`), `mark_index()` via typed `query` with bounds.
  - [ ] `ranking_history.py`: `MetricsDbHistory(RankingHistory)` over `ranking_engine.metrics_store.history/nearest` (until 25.2 moves it to `ranking/application`; list the legacy edge in `test_boundaries.py` the way 24.4 did).
  - [ ] `backtest_runner.py`: `NodeRunner(BacktestRunner)`. `run()` builds one `BacktestRunConfig` (`LoggingConfig(bypass_logging=True)` — the Rust logger initialises once per process; copy `strategies/snapshot_backtest.py`'s comment), `BacktestDataConfig` per requested data kind, `ImportableStrategyConfig(strategy_path, config_path, params)`. After `node.run()`, take results by `config_id` (never list position: `backtest_dydx.py:42-56`), pull `stats_pnls`, `stats_returns`, the fills/positions report from `node.get_engine(config_id).trader.generate_positions_report()` into `TradeLedger`, the account report into `EquityCurve`, and `MetricReport` through `performance_metrics`. `sweep()` = one node, N configs, results re-attributed by id; a dropped result is an error, not a silent gap (DATA-07).
- [ ] Task 3 — tests, spine amendment, boundaries, docs (AC: #3)
  - [ ] `research/tests/test_returns.py`, `test_equity.py`, `test_trades.py`, `test_report.py`, `test_correlation.py`: closed-form cases listed in AC #3; `test_backtest_runner.py`: two-day synthetic catalog (`ParquetDataCatalog.write_data()` of a `CryptoPerpetual` + `DydxSecondSnapshot` rows, reuse `research/tests`' existing helpers from 24.4's repaired tests where present) running `OFIStrategy` by string path, asserting `RunResult.config_id` matches and `MetricReport` keys equal `all_metrics` keys.
  - [ ] Spine: AD-D1 research row amended (`[amended <date>: Story 27.1]`), the "no aggregates" note replaced by the value-object list and invariants; AD-D2 gains the sentence "`research/domain` may import numpy (pure arithmetic, no I/O)".
  - [ ] `platform/tests/test_boundaries.py`: `research.domain` rows, numpy allowance, the `ranking_engine.metrics_store` legacy edge.
  - [ ] `ARCHITECTURE.md` module map; `research/BACKTESTING.md` "Run from a notebook" section rewritten over `BacktestRunner`.

## Dev Notes

- **What already exists and must be wrapped, not duplicated:** `ml_signals/performance_metrics.py` (`trade_stats`, `equity_returns`, `return_stats`, `all_metrics` — thin wrappers over Nautilus's PyO3 `PortfolioStatistic` classes; after 23.2 it is `kernel.performance_metrics`), `ml_signals/catalog_stats.py` (`query_second_ohlc`, `second_ohlc_arrays`, `data_file_ranges`, `find_gaps`, `coverage` — after 23.2 partly `kernel.catalog_files`, the rest `views`), `ml_signals/indicators.py` (`Microprice`, `MultiLevelOBI`, `MultiLevelOFI`, `mid_price`, `spread`, `microprice` — after 23.2 `kernel.indicators`), `ml_signals/candle_store.py` `window/latest` (after 24.1 `candles.application`), `ml_signals/strategies/backtest_dydx.py` (the `BacktestNode` idioms: config-id attribution, `LoggingConfig(bypass_logging=True)`, symbol de-duplication), `ml_signals/strategies/snapshot_backtest.py` (the snapshots→`QuoteTick` derived catalog trick, in a `TemporaryDirectory`), `live_paper/trade_history.py` (the only other caller of `all_metrics`; read how it builds `pnl_by_day` so `EquityCurve.from_pnl_by_day` matches).
- **Why a domain layer in a consumer context:** the spine made research a consumer so it cannot leak a computation into the live path. That still holds: nothing here is imported by capture, ranking or bots. The value objects exist because three consumers (notebooks, backtest reports, and later `bot_tui`'s history page) need the same drawdown/return/correlation arithmetic, and SSOT-02 forbids three copies.
- **Layering (AD-D2):** `domain/` = stdlib + numpy + `kernel` + `nautilus_trader.model`; no pandas, no I/O, no catalog. `application/` = `typing.Protocol` ports + services; pandas lives here (DataFrames are the notebook-facing shape). Notebooks import `research.application` and `research.domain` only.
- **MEM-01:** `start`/`end` are required positional parameters everywhere. A notebook that wants "everything" must say a window. `sweep()` streams through `BacktestDataConfig`; it must not materialise the window once per grid point when a single `BacktestNode` can share it.
- **Nautilus pin:** `nautilus_trader` 1.229.0. `BacktestNode.run()` returns `list[BacktestResult]`; `node.get_engine(run_config_id)` exists in this version — verify before use and record the exact accessor in Completion Notes (the 2.x action item about `BacktestNode` result attribution is the reason).
- **Project rules:** `platform/CLAUDE.md` DESIGN-01 (invariant in every docstring), DESIGN-02 (research depends on shared types only), MEM-01..03, NAUT-01 (never `Price(decimal, precision)` re-stamps; the frames layer converts to float only after the value is inside a `Price`), NAUT-03, TEST-01..04, READ-03 (mypy `disallow_incomplete_defs`), SSOT-02, FORK-01.
- **Working directory:** `platform/` (`cd platform`); tests run as `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`; `make test` runs the Makefile list inside the collector image.

### Project Structure Notes

- Target tree: `platform/research/{domain,application,strategies,notebooks,tests}/`, `research/BACKTESTING.md` (renamed to `README.md` in 27.9). `platform/` is a namespace directory: never add `platform/__init__.py`; never import with a `platform.` prefix.
- No new dependency (NFR12). numpy, pandas, pyarrow are already locked.

### References

- Spine: AD-D1 (research row), AD-D2 (layering), AD-D10 (no recompute outside ranking)
- Epic text: `_bmad-output/planning-artifacts/epics.md` "Story 27.1"; requirements FR70, NFR12
- Code: `ml_signals/performance_metrics.py`, `ml_signals/catalog_stats.py`, `ml_signals/strategies/{backtest_dydx,snapshot_backtest}.py`, `live_paper/trade_history.py`, `ml_signals/candle_store.py`
- Rules: `platform/CLAUDE.md`; `platform/docs/DATA_DICTIONARY.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
