# Epic 27 Context: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the researcher six executable notebooks: catalog inspection, microstructure, correlation and cross-venue behaviour, backtest evaluation with sweeps and walk-forward, Monte Carlo robustness, and a candlestick scanner. No notebook holds analysis logic of its own. Every number comes from one typed analysis layer (`research/domain` value objects plus `research/application` ports and services), so a Sharpe ratio or a drawdown is computed one way in a notebook, a backtest report or a later bot report. Candlestick patterns become a first-class signal: one streaming `Indicator` in `kernel/` is shared by the chart picker, the screener's Technicals tab, the scanner notebook, backtests and paper bots, so a pattern found in the scanner is one config file away from a backtest and one more from a paper bot. The legacy notebooks (hard-coded paths, runtime `pip install` of TA-Lib/`pandas_ta`, reads of the retired `custom_dydx_minute_bar` directory) are replaced, and the research context ends documented with binding notebook rules.

## Stories

- Story 27.1: `research/domain` analysis value objects and the `research/application` ports (done)
- Story 27.2: Executable notebooks and the catalog inspection notebook (done)
- Story 27.3: Microstructure notebook (done)
- Story 27.4: Correlation and cross-venue notebook (done)
- Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward (done)
- Story 27.6: Monte Carlo and robustness notebook (done)
- Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook (done)
- Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and paper bots (done)
- Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

## Requirements & Constraints

- **No new dependency.** The toolkit is numpy, pandas, pyarrow, plotly and jupytext, all already locked. scipy, statsmodels, matplotlib, seaborn, ipywidgets, papermill/nbclient, TA-Lib and `pandas_ta` are rejected. Jupyter is a personal tool launched locally (`uv run jupyter lab research/notebooks`), never a compose service.
- **Every read is time-bounded.** `start`/`end` are required with no defaults; backtests stream through `BacktestDataConfig`.
- **Each metric has one home.** Portfolio statistics come from `kernel.performance_metrics` (`MetricReport` wraps `all_metrics` and adds nothing); microstructure values from `kernel.indicators`; bars from the candle store, never a pandas resample; ranking values from ranking's published output. A formula in a notebook cell is a review failure.
- **Gaps stay visible.** `None`/NaN, never interpolated or forward-filled.
- **Warnings are failures.** Notebooks run under `warnings.simplefilter("error")`.
- **Docstrings name invariants** on every value object, port and Monte Carlo function.
- **Tests use real Nautilus objects**, fixture catalogs written with `ParquetDataCatalog.write_data()`.
- **Docs ship with the code.** Docs, dockerfile `COPY` sets and Makefile test lists change in the same commit.
- **Closeout success criteria (27.9):**
  - `research/README.md` replaces `research/BACKTESTING.md` (content moved unchanged, a redirect stub kept for one release) and holds the notebook index (number, purpose, inputs, domain functions called, fixture run time), the "write a notebook" recipe, the "add a metric" recipe (`performance_metrics` or `research/domain` first, then the notebook) and the local launch; `platform/README.md` and the frontend docs page link to it.
  - `platform/CLAUDE.md` gains a "Research notebooks" section with NB-01 (no analysis logic in a notebook), NB-02 (jupytext-paired, output-stripped, env-parameterised, executed by `make test`), NB-03 (no `%pip`/`!pip`, nothing outside `uv.lock`) and NB-04 (every read bounded by `START`/`END`); its indicator note names `kernel/candle_patterns.py` as the second custom-`Indicator` precedent.
  - `git grep` for `pandas_ta`, `talib`, `%pip`, `custom_dydx_minute_bar` under `platform/` is empty outside `docs/` history notes and `.planning/`; no `.ipynb` exists outside `research/notebooks/`.
  - `ARCHITECTURE.md` shows `research/{domain,application,notebooks,strategies}` and `kernel/candle_patterns.py`; `docs/DATA_DICTIONARY.md` gains "Research reads" (stored fields read and derived values computed per notebook).
  - The epic's `Known limit:`s (histogram-not-marker pattern display, no `ipywidgets` interactivity, Monte Carlo on closed trades only) go into the DDD spine's Deferred section with upgrade paths.
  - `sprint-status.yaml` marks Epic 27 `done`; 27.8's deferred VPS step does not hold it open.

## Technical Decisions

- **Research stays a consumer**: no aggregate, writes only throwaway backtest catalogs. The DDD spine's research row lists the value objects (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, microstructure result tuples, `MonteCarloResult`) and ports (`MarketFrames`, `RankingHistory`, `BacktestRunner`) with their invariants. Spine changes use `[amended <date>: Story 27.x — ...]` notes.
- **Layering.** `research/domain` imports only stdlib, numpy, `kernel/` and `nautilus_trader.model`/`core`; pandas stays in `research/application`.
- **Context edges are name-allowlisted** in `platform/tests/test_boundaries.py`: `research → candles` (`RESEARCH_CANDLES_SERVICES`), `research → archive` (`RESEARCH_ARCHIVE_SERVICES`, pure `find_gaps` only); `research → views/ranking/data_api` forbidden. `bots → research` is a runtime string-path dependency only (`StrategyFactory`), never an import edge; `test_images.py`'s `_STRING_PATH_IMPORTS` covers the image closure.
- **`BacktestRunner` is the one way to backtest** (`BacktestNode` + `BacktestDataConfig`, strategies by `ImportableStrategyConfig` string path, results attributed by `BacktestRunConfig.id`).
- **Notebook format.** Source of truth is a jupytext percent-format `research/notebooks/<nn>_<name>.py` (ruff/mypy-clean), paired with an output-stripped `.ipynb`; `make notebooks` syncs pairs. Parameters via `notebooks/_params.py` (`Params.from_env()`, `_params.setting` shrunk by `NOTEBOOK_<NAME>` JSON in tests). Logic lives in a `research/application` service per notebook (`inspection`, `microstructure`, `aligned`, `evaluation`, `robustness`, `patterns`). `research/tests/test_notebooks.py` runs each via `runpy` against a session fixture catalog (3 venues x 2 instruments, planted gap, provisional day, crossed second), under 60 s each.
- **Candlestick detector.** `kernel/candle_patterns.py`: `PatternName` (22 names), frozen `Thresholds`, `CandlePattern(Indicator)` (+100/-100/0, O(1) state), `CandlePatternSet`. Consumed by `views.indicator_picker`, `research.application.patterns` and `research/strategies/candle_pattern_strategy.py`.
- **Paper bots.** The epic text's `live_paper` references map to `bots/` (`bots/infrastructure/nautilus_host.py`, `bots/domain/config.py`, `bots.dockerfile`; compose service still `live-paper`). Bot configs carry optional `strategy`/`params` keys; `strategy = "candle_pattern"` resolves by string path.

## Cross-Story Dependencies

- 27.1–27.8 are done; only 27.9 remains. It documents and closes what 27.1–27.8 built and adds no analysis code.
- 27.9 depends on the six notebooks, the analysis layer, `kernel/candle_patterns.py` and `CandlePatternStrategy` being in place, and on the `Known limit:` comments those stories left in code (collect them from the tree, not from memory).
- 27.8's VPS paper-bot fill check already sits in `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions"; 27.9 leaves it there.
