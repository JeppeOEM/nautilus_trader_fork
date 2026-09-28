# Epic 27 Context: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the researcher six executable notebooks: catalog inspection, microstructure, correlation and cross-venue behaviour, backtest evaluation with sweeps and walk-forward, Monte Carlo robustness, and a candlestick scanner. No notebook holds analysis logic of its own. Every number comes from one typed analysis layer (`research/domain` value objects plus `research/application` ports and services), so a Sharpe ratio or a drawdown is computed one way in a notebook, a backtest report or a later bot report. The epic also makes candlestick patterns a first-class signal. One streaming `Indicator` in `kernel/` is shared by the chart picker, the screener's Technicals tab, the scanner notebook, backtests and paper bots, so a pattern found in the scanner is one config file away from a backtest and one more from a paper bot. The legacy notebooks, with their hard-coded paths, runtime `pip install` of TA-Lib/`pandas_ta` and reads of the retired `custom_dydx_minute_bar` directory, are replaced.

## Stories

- Story 27.1: `research/domain` analysis value objects and the `research/application` ports (done)
- Story 27.2: Executable notebooks and the catalog inspection notebook (done)
- Story 27.3: Microstructure notebook (done)
- Story 27.4: Correlation and cross-venue notebook (done)
- Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward (done)
- Story 27.6: Monte Carlo and robustness notebook
- Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook
- Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and paper bots
- Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

## Requirements & Constraints

- **No new dependency.** Only numpy, pandas, pyarrow, plotly and jupytext, which are already locked. scipy, statsmodels, matplotlib, seaborn, ipywidgets, papermill/nbclient, TA-Lib and `pandas_ta` are all out.
  - Bootstrap, deflated/probabilistic Sharpe and pattern recognition are hand-rolled on numpy and tested against closed-form cases.
  - The TA-Lib parity check is a one-off local run recorded in the story notes, never CI.
- **Jupyter is a personal tool.** It is launched locally (`uv run jupyter lab research/notebooks`) and is never a compose service.
- **Every read is time-bounded.** `start`/`end` are required with no defaults. Backtests stream through `BacktestDataConfig`, and no window is materialised twice.
- **Each metric has one home.**
  - Portfolio statistics come from `kernel.performance_metrics`. `MetricReport` wraps `all_metrics` and adds nothing.
  - Microstructure values come from `kernel.indicators` classes, never from a formula in a cell.
  - Bars come from the candle store, never from a pandas resample or a third fold.
  - Ranking values are read from ranking's published output, never recomputed.
- **Gaps stay visible.** They are `None`/NaN, never interpolated or forward-filled, and correlation is pairwise-complete.
- **Warnings are failures.** Notebooks run under `warnings.simplefilter("error")`.
- **Invariants and seeds are documented.** Every value object, port and Monte Carlo function docstring names its invariant and cites any formula's source. Monte Carlo results record their seed and path count, and a zero-variance input returns a stated `None`, never a division error.
- **Tests use real Nautilus objects.** Fixture catalogs are written with `ParquetDataCatalog.write_data()`, with no mocks.
- **Docs ship with the code.** Docs, dockerfile `COPY` sets and both Makefile test lists (`test`, `test-live-paper`) change in the same commit.
- **VPS steps are deferred, never parked.** 27.8's paper-bot fill check is appended to `platform/docs/DEPLOY_CHECKLIST.md` under "Deferred operator actions", and the story still finalizes `done`.

## Technical Decisions

- **Research stays a consumer.** It has no aggregate and writes only throwaway backtest catalogs.
  - The spine's research row lists the values: `ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, the microstructure result tuples, and `MonteCarloResult` (still to come, in 27.6). It also lists the ports: `MarketFrames`, `RankingHistory` and `BacktestRunner`. Each entry carries its invariant.
  - A story that adds a research module, value or context edge amends the spine in the same commit: the research row, the dependency graph and the tree. It uses a `[amended <date>: Story 27.x — ...]` note, as 27.1–27.5 did.
- **Layering.** `research/domain` imports only stdlib, numpy, `kernel/` and `nautilus_trader.model`/`core`. pandas stays in `research/application`.
- **Context edges** are name-allowlisted in `test_boundaries.py`, and each has a "still used" check.
  - `research → candles` (`RESEARCH_CANDLES_SERVICES`): `open_store`, `window`, `oldest_t`, `newest_t`, `bucket_starts`, `verified_status` and `BAR_SECONDS`. A window past the store's coverage raises; it never reads as "no trades".
  - `research → archive` (`RESEARCH_ARCHIVE_SERVICES`): only the pure `find_gaps`, over timestamps research read itself.
  - `research → views`, `research → ranking` and `research → data_api` stay forbidden, and `RankingHistory` reads over HTTP.
  - The depth functions and `RollingZScore` live in `kernel.indicators`. Same-asset matching lives in `kernel.venues` (`asset_key`, `same_asset`, `USD_QUOTES`); research never splits an id itself.
  - Catalog reads the pinned Nautilus cannot do go through `kernel/catalog_files.py`'s column-projected helpers. New helpers are added there.
  - A new need extends these tables deliberately, with a stated reason, and never routes around the test.
- **`BacktestRunner` is the one way to backtest.**
  - It wraps `BacktestNode` + `BacktestDataConfig` and takes strategies by `ImportableStrategyConfig` string path.
  - `RunResult` carries an `EquityCurve`, a `TradeLedger` and a `MetricReport`, attributed by `BacktestRunConfig.id`. A missing result raises.
  - A sweep is one node with one run config per grid point. Walk-forward (`walk_forward.py`) and the evaluation frames (`evaluation.py`) exist for 27.6/27.8 to reuse.
  - Notebooks never touch `BacktestNode` and never sum a PnL.
- **Notebook format.**
  - The source of truth is a jupytext percent-format `<nn>_<name>.py`, which must be ruff- and mypy-clean. It is paired with an output-stripped `.ipynb`, and `make notebooks` syncs the pair.
  - The Parameters cell uses `notebooks/_params.py`: `Params.from_env()` for paths and the window, and `_params.setting` for archive-sized constants. The test harness shrinks those constants through `NOTEBOOK_<NAME>` JSON.
  - Logic lives in a `research/application` service; the precedents are `inspection.py`, `microstructure.py`, `aligned.py` and `evaluation.py`. Pure arithmetic lives in `research/domain`.
  - Sections read one instrument at a time.
  - `research/tests/test_notebooks.py` runs each notebook via `runpy` against the session fixture catalog, with plotly headless and a limit of under 60 s each. The fixture has 3 venues × 2 instruments, with a planted gap, a provisional day and a crossed second.
- **Candlestick detector (27.7).**
  - `kernel/candle_patterns.py` holds `CandlePattern(Indicator)` with `update_raw(o, h, l, c)`. Its `value` is +100, -100 or 0, following the TA-Lib convention.
  - State is O(1): the last three bars only. Thresholds are explicit constructor parameters.
  - `CandlePatternSet` runs every pattern for the scanner.
  - It is registered through the existing `chart_indicators.INDICATOR_CATALOG`/`IndicatorSpec` and the screener's Technicals path, with no TOML key-set change.
  - Hits are drawn as histogram spikes. This is a `Known limit:`, and the upgrade path is lightweight-charts markers.
  - 27.7 also adds `candle_patterns` to the spine's kernel list.
- **Bots paths.** The epic text predates the move from `live_paper` to `bots/`. Read its references as follows:
  - `live_paper/node.py` means `bots/infrastructure/nautilus_host.py`.
  - `BotConfig` means `bots/domain/config.py` (plus `bots/infrastructure/config.py`).
  - `live_paper.dockerfile` means `bots.dockerfile`.
  - The existing strategy is `bots/strategies/dummy.py`.
  - The compose service is still named `live-paper`.

  Paper and non-paper configs are distinct types (`PaperConfig`/`PaperFleet` versus `ExecConfig`/`ExecBot`). The new `strategy`/`params` keys must be optional with defaults and must never add a mode to a paper config. The strategy resolves by string path through `StrategyFactory.create(ImportableStrategyConfig(...))`, so there is no bots → research import edge, and `test_images.py` needs an explicit entry for that import.
- **Legacy notebook remaining:** `research/notebooks/candlestick_pattern_scanner.ipynb`, which 27.7 deletes. `backtest.ipynb` is already gone.

## Cross-Story Dependencies

- Stories run in order, 27.6 → 27.9, and all build on 27.1's values and ports and on 27.2's `_params.py`, notebook harness and fixture catalog.
- 27.6 reuses 27.5's `STRATEGY` parameters and sweep results for the deflated Sharpe of the best grid point.
- 27.8 depends on 27.7's detector and on `BacktestRunner`. It becomes the second worked example in 27.5's `04_backtest_evaluation` notebook.
- 27.9 closes the epic:
  - `research/README.md` replaces `research/BACKTESTING.md`, leaving a redirect stub.
  - Rules NB-01..NB-04 are added to `platform/CLAUDE.md`.
  - `pandas_ta`, `talib`, `%pip` and `custom_dydx_minute_bar` are gone from `platform/`.
  - `docs/DATA_DICTIONARY.md` gains a "Research reads" section.
  - The epic's `Known limit:`s are listed in the spine's Deferred section.
  - Epic 27 is marked done.
