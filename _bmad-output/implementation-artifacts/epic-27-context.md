# Epic 27 Context: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the researcher six executable notebooks that cover catalog inspection, microstructure, correlation and cross-venue behaviour, backtest evaluation with sweeps and walk-forward, and Monte Carlo robustness. None of the notebooks holds analysis logic of its own. Every number comes from one typed analysis layer (`research/domain` value objects plus `research/application` ports), so a Sharpe ratio or drawdown is computed one way everywhere. The epic also makes candlestick patterns a first-class signal: one streaming `Indicator` in `kernel/` is shared by the chart picker, the screener's Technicals tab, a scanner notebook, backtests and paper bots. A pattern found in the scanner is then one config file away from a backtest, and one more from a paper bot. The three stale legacy notebooks, with their hard-coded paths, runtime `pip install` of TA-Lib/`pandas_ta` and retired `custom_dydx_minute_bar` directory, are replaced.

## Stories

- Story 27.1: `research/domain` analysis value objects and the `research/application` ports
- Story 27.2: Executable notebooks and the catalog inspection notebook
- Story 27.3: Microstructure notebook
- Story 27.4: Correlation and cross-venue notebook
- Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward
- Story 27.6: Monte Carlo and robustness notebook
- Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook
- Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and `live_paper`
- Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

## Requirements & Constraints

- **No new dependency.** The whole toolkit is numpy, pandas, pyarrow, plotly and jupytext, all already in `uv.lock`. scipy, statsmodels, matplotlib, seaborn, ipywidgets, nbclient/papermill, TA-Lib and `pandas_ta` are rejected. Bootstrap, correlation, clustering, deflated/probabilistic Sharpe and pattern recognition are hand-rolled on numpy and tested against closed-form cases.
- **Jupyter stays a personal tool.** It is launched locally (`uv run jupyter lab research/notebooks`) and never runs as a compose service.
- **Every read is time-bounded.** `start`/`end` are required with no defaults. Backtests stream through `BacktestDataConfig`, and no window is materialised twice.
- **Each metric has one home.** Portfolio statistics come from `kernel.performance_metrics` (Nautilus `PortfolioStatistic`); `MetricReport` wraps `all_metrics` and never reimplements a statistic. Microstructure values come from `kernel.indicators` classes, never a formula in a notebook cell. Bars come from the candle store, never a third seconds-to-bars fold or a pandas resample. Ranking pct/volatility is read from ranking's published output and never recomputed.
- **Gaps stay visible.** Gaps and defects appear as `None` gaps, never interpolated or forward-filled. Correlation is pairwise-complete.
- **Warnings are failures.** A `DeprecationWarning`/`FutureWarning` in a notebook run fails the test, so notebooks are executed under `warnings.simplefilter("error")`.
- **Invariants are documented.** Every value object, port and Monte Carlo function docstring names the invariant it protects. Monte Carlo results record their seed, so every figure is reproducible.
- **Tests use real Nautilus objects.** Fixture catalogs are built with `ParquetDataCatalog.write_data()`; no mocks.
- **Docs ship with the code.** Docs, dockerfile `COPY` sets and both Makefile test lists (`test`, `test-live-paper`) are updated in the same commit as the code that needs them.
- **VPS steps are deferred, never parked.** A VPS step (27.8's paper-bot fill check) goes to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions". The story still finalizes `done` and never parks `awaiting-operator`.

## Technical Decisions

- **Research stays a consumer.** `research/` owns no aggregates and writes only throwaway backtest catalogs. It reads the catalog, `metrics.db` and `/api/rankings` (HTTP, never a `data_api` import). Story 27.1 amends the spine's research row to list the analysis value objects (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, `MonteCarloResult`) and the ports (`MarketFrames`, `RankingHistory`, `BacktestRunner`).
- **Layering.** `research/domain/` imports only the stdlib, numpy (admitted as pure arithmetic and recorded in `test_boundaries.py`), `kernel/` and `nautilus_trader.model`/`core`. It does no I/O. `research/application/` declares ports as `typing.Protocol` and implements the readers. Analysis logic lives in `research/domain`; reads go through `MarketFrames`.
- **Boundary graph conflict.** The live graph in `platform/tests/test_boundaries.py` gives `research` only the `kernel`/`observability` edges. It also has an explicit test forbidding `research` from importing `views` or `ranking`. Story 27.1's `MarketFrames.bars` (via `candles.application.window`) and `RankingHistory` (via the ranking query service) therefore need a deliberate graph change, recorded with its reason. Otherwise they must be reshaped to fit the existing rule. Do not route around the test.
- **`BacktestRunner`.** It wraps `BacktestNode` + `BacktestDataConfig`, takes strategies by `ImportableStrategyConfig` string path, and returns `EquityCurve`/`TradeLedger`/`MetricReport` plus `config_id`. Sweeps are one `BacktestNode` with one `BacktestRunConfig` per grid point, and results are re-attributed by `config_id`. Notebooks never touch `BacktestNode` directly.
- **Notebook format.**
  - Each notebook is a jupytext percent-format `<nn>_<name>.py` (the source of truth, ruff/mypy-clean) paired with an output-stripped `.ipynb`.
  - The first code cell is a Parameters cell reading `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `INSTRUMENTS`, `START` and `END` from the environment, with defaults under `platform/data/`.
  - `make notebooks` syncs the pairs.
  - `research/tests/test_notebooks.py` runs every `.py` notebook with `runpy` against one session fixture catalog, in under 60 s each. The fixture has three venues × two instruments with a deliberate gap, a provisional day and a crossed second.
- **Candlestick detector.** `kernel/candle_patterns.py` holds `CandlePattern(Indicator)`.
  - It is fed with `update_raw(o, h, l, c)` and outputs TA-Lib-convention `value` ±100/0.
  - It keeps O(1) state (the last three bars only), and its thresholds are explicit constructor parameters.
  - `CandlePatternSet` serves the scanner.
  - Story 27.7 amends the spine's kernel membership list to add it: "one pattern definition, shared by views, research and bots".
  - UI registration reuses the existing `chart_indicators.INDICATOR_CATALOG`/`IndicatorSpec` and screener Technicals path, with no TOML key-set change.
  - Hits are drawn as histogram spikes. This is a `Known limit:`, and the upgrade path is lightweight-charts markers.
- **Bots.** The epic text predates the `live_paper` → `bots/` move (Story 25.3). Read "`live_paper/node.py`/`BotConfig`/`live_paper.dockerfile`" as `bots/` (`bots/infrastructure/nautilus_host.py`, `bots/domain/config.py`, `bots.dockerfile`, compose service `live-paper`). The strategy is resolved by string path through `StrategyFactory.create(ImportableStrategyConfig(...))`, with no bots→research import edge. The new config keys `strategy`/`params` are optional with defaults.
- **Legacy notebooks.** The legacy notebooks already sit in `research/notebooks/` (`backtest.ipynb`, `candlestick_pattern_scanner.ipynb`, `dydx_catalog_pandas.ipynb`) and are deleted by Stories 27.5, 27.7 and 27.2 respectively.

## Cross-Story Dependencies

- The epic depends on Story 24.4 (the `research/` context exists) and is otherwise independent of capture, archive, ranking and the bots' aggregates.
- The order is 27.1 → 27.9. Stories 27.3–27.6 depend only on 27.1 (domain and ports) and 27.2 (notebook harness and fixture catalog).
- Story 27.6 reuses 27.5's `STRATEGY` parameters and sweep results.
- Story 27.8 depends on 27.7's detector, on 27.5's notebook (it becomes that notebook's second worked example) and on 27.1's `BacktestRunner`.
- Story 27.9 closes the epic. It adds `research/README.md` (replacing `BACKTESTING.md`), `platform/CLAUDE.md` rules NB-01..NB-04, the grep-clean check for `pandas_ta`/`talib`/`%pip`/`custom_dydx_minute_bar`, and lists the epic's `Known limit:`s in the spine's Deferred section.
