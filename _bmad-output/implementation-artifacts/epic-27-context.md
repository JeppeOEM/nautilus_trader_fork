# Epic 27 Context: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the researcher six executable notebooks: catalog inspection, microstructure, correlation and cross-venue behaviour, backtest evaluation with sweeps and walk-forward, Monte Carlo robustness, and a candlestick scanner. None of them holds analysis logic of its own. Every number comes from one typed analysis layer (`research/domain` value objects plus `research/application` ports), so a Sharpe ratio or a drawdown is computed one way everywhere. The epic also makes candlestick patterns a first-class signal. One streaming `Indicator` in `kernel/` is shared by the chart picker, the screener's Technicals tab, the scanner notebook, backtests and paper bots. A pattern found in the scanner is then one config file away from a backtest, and one more from a paper bot. The three stale legacy notebooks are replaced; they have hard-coded paths, `pip install` TA-Lib/`pandas_ta` at runtime and read the retired `custom_dydx_minute_bar` directory.

## Stories

- Story 27.1: `research/domain` analysis value objects and the `research/application` ports (done)
- Story 27.2: Executable notebooks and the catalog inspection notebook
- Story 27.3: Microstructure notebook
- Story 27.4: Correlation and cross-venue notebook
- Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward
- Story 27.6: Monte Carlo and robustness notebook
- Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook
- Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and `live_paper`
- Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

## Requirements & Constraints

- **No new dependency.** Use numpy, pandas, pyarrow, plotly and jupytext, which are already locked. scipy, statsmodels, matplotlib, seaborn, ipywidgets, papermill/nbclient, TA-Lib and `pandas_ta` are all out. Bootstrap, correlation, clustering, deflated/probabilistic Sharpe and pattern recognition are hand-rolled on numpy and tested against closed-form cases. A TA-Lib parity check is a one-off local run whose results are recorded in the story notes. It is never run in CI.
- **Jupyter stays a personal tool.** It is launched locally (`uv run jupyter lab research/notebooks`) and is never a compose service.
- **Every read is time-bounded.** `start`/`end` are required with no defaults. Backtests stream through `BacktestDataConfig`, and no window is materialised twice.
- **Each metric has one home.**
  - Portfolio statistics come from `kernel.performance_metrics`. `MetricReport` wraps `all_metrics` and adds nothing.
  - Microstructure values come from `kernel.indicators` classes, never from a formula in a notebook cell.
  - Bars come from the candle store, never from a third seconds→bars fold or a pandas resample.
  - Ranking values are read from ranking's published output and never recomputed.
- **Gaps stay visible.** Gaps and defects show as `None`/NaN gaps and are never interpolated or forward-filled. Correlation is pairwise-complete.
- **Warnings are failures.** A `DeprecationWarning` or `FutureWarning` during a notebook run fails the test, so notebooks run under `warnings.simplefilter("error")`.
- **Invariants and seeds are documented.** Every value object, port and Monte Carlo function docstring names its invariant. Monte Carlo results record their seed and path count.
- **Tests use real Nautilus objects.** Fixture catalogs are built with `ParquetDataCatalog.write_data()`, with no mocks.
- **Docs ship with the code.** Docs, dockerfile `COPY` sets and both Makefile test lists (`test`, `test-live-paper`) change in the same commit as the code.
- **VPS steps are deferred, never parked.** 27.8's paper-bot fill check goes to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions", and the story still finalizes `done`.

## Technical Decisions

- **Research stays a consumer.** It owns no aggregate and writes only throwaway backtest catalogs. The spine's research row already lists the values (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, plus `MonteCarloResult`, which is still to come in 27.6) and the ports (`MarketFrames`, `RankingHistory`, `BacktestRunner`), each with its invariant. A new value must be added to that row.
- **Layering and boundaries (settled in 27.1).**
  - `research/domain` imports only the stdlib, numpy, `kernel/` and `nautilus_trader.model`/`core`. pandas stays in `research/application`.
  - The one extra context edge is `research → candles`. It is limited to the read-only query services listed in `test_boundaries.py`'s `RESEARCH_CANDLES_SERVICES`, and a service that falls out of use must be removed from that list.
  - `research → views`, `ranking` and `data_api` stay forbidden. `RankingHistory` reads over data_api's HTTP API.
  - A new need is met by extending these tables deliberately with a stated reason, never by routing around the test.
- **`BacktestRunner` is the one way to backtest.** It wraps `BacktestNode` + `BacktestDataConfig` and takes strategies by `ImportableStrategyConfig` string path. It returns `EquityCurve`/`TradeLedger`/`MetricReport` attributed by `BacktestRunConfig.id`, and a missing result raises. A sweep is one node running one run config per grid point. Notebooks never touch `BacktestNode` directly and never sum PnL themselves.
- **Notebook format.**
  - Each notebook is a jupytext percent-format `<nn>_<name>.py`. It is the source of truth and is ruff/mypy-clean.
  - Each `.py` is paired with an output-stripped `.ipynb`.
  - The first code cell reads `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `INSTRUMENTS`, `START` and `END` from the environment, defaulting to `platform/data/` paths.
  - `make notebooks` syncs the pairs.
  - `research/tests/test_notebooks.py` runs each `.py` notebook with `runpy` against one session fixture catalog, in under 60 s each, with plotly rendering off-screen. The fixture has 3 venues × 2 instruments and planted defects: one gap, one provisional day and one crossed second.
- **Candlestick detector.**
  - `kernel/candle_patterns.py` holds `CandlePattern(Indicator)`. It is fed with `update_raw(o, h, l, c)` and outputs `value` of +100, -100 or 0 (TA-Lib convention).
  - Its state is O(1): only the last three bars. Its thresholds are explicit constructor parameters.
  - `CandlePatternSet` runs every pattern for the scanner.
  - It is registered through the existing `chart_indicators.INDICATOR_CATALOG`/`IndicatorSpec` and the screener's Technicals path, with no TOML key-set change.
  - Hits are drawn as histogram spikes. This is a `Known limit:` whose upgrade path is lightweight-charts markers.
  - 27.7 adds the detector to the spine's kernel list.
- **Bots paths.** The epic text predates the `live_paper` → `bots/` move. Read `live_paper/node.py` as `bots/infrastructure/nautilus_host.py`, `BotConfig` as `bots/domain/config.py` (with `bots/infrastructure/config.py`), `live_paper.dockerfile` as `bots.dockerfile`, and the existing strategy as `bots/strategies/dummy.py`. The compose service is still `live-paper`.
  - The strategy is resolved by string path through `StrategyFactory.create(ImportableStrategyConfig(...))`, so there is no bots→research import edge.
  - `test_images.py` needs an explicit entry for that string-path import.
  - The new `strategy` and `params` keys are optional and have defaults.
- **Legacy notebooks.** Three legacy notebooks sit in `research/notebooks/`. `dydx_catalog_pandas.ipynb` is deleted by 27.2, `backtest.ipynb` by 27.5 and `candlestick_pattern_scanner.ipynb` by 27.7.

## Cross-Story Dependencies

- The stories run in order 27.1 → 27.9. 27.3–27.6 depend only on 27.1 (values and ports) and 27.2 (notebook harness and fixture catalog).
- 27.6 reuses 27.5's `STRATEGY` parameters and sweep results.
- 27.8 depends on 27.7's detector and on 27.1's `BacktestRunner`, and it becomes the second worked example in 27.5's notebook.
- 27.9 closes the epic:
  - It replaces `research/BACKTESTING.md` with `research/README.md` and adds rules NB-01..NB-04 to `platform/CLAUDE.md`.
  - It checks that `pandas_ta`, `talib`, `%pip` and `custom_dydx_minute_bar` no longer appear anywhere in the repo.
  - It lists the epic's `Known limit:`s in the DDD spine's Deferred section and marks the epic done.
