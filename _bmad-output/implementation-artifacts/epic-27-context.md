# Epic 27 Context: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the researcher six executable notebooks: catalog inspection, microstructure, correlation and cross-venue behaviour, backtest evaluation with sweeps and walk-forward, Monte Carlo robustness, and a candlestick scanner. No notebook holds analysis logic of its own. Every number comes from one typed analysis layer (`research/domain` value objects plus `research/application` ports and services), so a Sharpe ratio or a drawdown is computed one way everywhere. The epic also makes candlestick patterns a first-class signal: one streaming `Indicator` in `kernel/` is shared by the chart picker, the screener's Technicals tab, the scanner notebook, backtests and paper bots, so a pattern found in the scanner is one config file away from a backtest and one more from a paper bot. The stale legacy notebooks (hard-coded paths, runtime `pip install` of TA-Lib/`pandas_ta`, reads of the retired `custom_dydx_minute_bar` directory) are replaced.

## Stories

- Story 27.1: `research/domain` analysis value objects and the `research/application` ports (done)
- Story 27.2: Executable notebooks and the catalog inspection notebook (done)
- Story 27.3: Microstructure notebook (done)
- Story 27.4: Correlation and cross-venue notebook
- Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward
- Story 27.6: Monte Carlo and robustness notebook
- Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook
- Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and paper bots
- Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

## Requirements & Constraints

- **No new dependency.** Only numpy, pandas, pyarrow, plotly and jupytext (already locked). scipy, statsmodels, matplotlib, seaborn, ipywidgets, papermill/nbclient, TA-Lib and `pandas_ta` are out. Bootstrap, correlation, clustering, deflated/probabilistic Sharpe and pattern recognition are hand-rolled on numpy and tested against closed-form cases. A TA-Lib parity check is a one-off local run recorded in the story notes, never CI.
- **Jupyter is a personal tool**, launched locally (`uv run jupyter lab research/notebooks`), never a compose service.
- **Every read is time-bounded**: required `start`/`end`, no defaults. Backtests stream through `BacktestDataConfig`; no window is materialised twice.
- **Each metric has one home.** Portfolio statistics come from `kernel.performance_metrics` (`MetricReport` wraps `all_metrics`, adds nothing). Microstructure values come from `kernel.indicators` classes, never a formula in a cell. Bars come from the candle store, never a pandas resample or third fold. Ranking values are read from ranking's published output, never recomputed.
- **Gaps stay visible**: `None`/NaN, never interpolated or forward-filled; correlation is pairwise-complete.
- **Warnings are failures**: notebooks run under `warnings.simplefilter("error")`.
- **Invariants and seeds documented**: every value object, port and Monte Carlo function docstring names its invariant; Monte Carlo results record seed and path count.
- **Tests use real Nautilus objects**; fixture catalogs are written with `ParquetDataCatalog.write_data()`, no mocks.
- **Docs ship with the code**: docs, dockerfile `COPY` sets and both Makefile test lists (`test`, `test-live-paper`) change in the same commit.
- **VPS steps are deferred, never parked**: 27.8's paper-bot fill check goes to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" and the story still finalizes `done`.

## Technical Decisions

- **Research stays a consumer**: no aggregate, writes only throwaway backtest catalogs. The spine's research row lists the values (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, the 27.3 microstructure result tuples, and `MonteCarloResult`, still to come in 27.6) and the ports (`MarketFrames`, `RankingHistory`, `BacktestRunner`), each with its invariant. A story adding a research module, value or context edge amends the spine's research row, dependency graph and tree in the same commit, as 27.1–27.3 did.
- **Layering.** `research/domain` imports only stdlib, numpy, `kernel/` and `nautilus_trader.model`/`core`; pandas stays in `research/application`.
- **Context edges** are name-allowlisted in `test_boundaries.py`, each with a "still used" check:
  - `research → candles` (`RESEARCH_CANDLES_SERVICES`): `open_store`, `window`, `oldest_t`, `newest_t`, `bucket_starts`, `verified_status`, `BAR_SECONDS`. A window past store coverage raises, never reads as "no trades".
  - `research → archive` (`RESEARCH_ARCHIVE_SERVICES`): only the pure `find_gaps` over timestamps research read itself; never the unbounded `likely_outages`/`coverage`.
  - `research → views`, `ranking`, `data_api` stay forbidden; `RankingHistory` reads over data_api's HTTP API. Depth functions (`DepthProfile`, `snapshot_depth`, `cumulative_depth`, `depth_within_bps`) and `RollingZScore` now live in `kernel.indicators` (moved there in 27.3), so the epic text's `views.book_features.depth_profile` reference means the kernel version.
  - Catalog reads the pinned Nautilus cannot do go through `kernel/catalog_files.py`'s column-projected helpers (`query_top_of_book`, `query_index_prices`, `price_precision_labels`); add new ones there, not in research.
  - A new need extends these tables deliberately with a stated reason, never routes around the test.
- **`BacktestRunner` is the one way to backtest**: wraps `BacktestNode` + `BacktestDataConfig`, strategies by `ImportableStrategyConfig` string path, returns `EquityCurve`/`TradeLedger`/`MetricReport` attributed by `BacktestRunConfig.id` (missing result raises). A sweep is one node with one run config per grid point. Notebooks never touch `BacktestNode` or sum PnL.
- **Notebook format.** Jupytext percent-format `<nn>_<name>.py` is the source of truth (ruff/mypy-clean), paired with an output-stripped `.ipynb`; `make notebooks` syncs. The Parameters cell uses `notebooks/_params.py`'s `Params.from_env()` (never re-read env in a notebook). Logic lives in a `research/application` service (`inspection.py`, `microstructure.py` are the precedents), pure arithmetic in `research/domain`; sections read one instrument at a time (MEM-01). `research/tests/test_notebooks.py` runs each notebook via `runpy` against the session fixture catalog (3 venues × 2 instruments; planted gap, provisional day, crossed second), under 60 s each, plotly headless.
- **Candlestick detector.** `kernel/candle_patterns.py`: `CandlePattern(Indicator)`, `update_raw(o, h, l, c)`, `value` ∈ {+100, -100, 0} (TA-Lib convention), O(1) state (last three bars), thresholds as explicit constructor parameters; `CandlePatternSet` runs every pattern for the scanner. Registered through the existing `chart_indicators.INDICATOR_CATALOG`/`IndicatorSpec` and the screener's Technicals path with no TOML key-set change. Hits drawn as histogram spikes (`Known limit:`, upgrade path lightweight-charts markers). 27.7 adds it to the spine's kernel list.
- **Bots paths.** Epic text predates the `live_paper` → `bots/` move: read `live_paper/node.py` as `bots/infrastructure/nautilus_host.py`, `BotConfig` as `bots/domain/config.py` (+ `bots/infrastructure/config.py`), `live_paper.dockerfile` as `bots.dockerfile`, existing strategy as `bots/strategies/dummy.py`. Compose service is still `live-paper`. The strategy resolves by string path via `StrategyFactory.create(ImportableStrategyConfig(...))` (no bots→research import edge); `test_images.py` needs an explicit entry for it; new `strategy`/`params` keys are optional with defaults.
- **Legacy notebooks remaining**: `backtest.ipynb` (27.5 deletes) and `candlestick_pattern_scanner.ipynb` (27.7 deletes).

## Cross-Story Dependencies

- Order 27.1 → 27.9. 27.4–27.6 build on 27.1's values/ports and 27.2's `_params.py`, notebook harness and fixture catalog.
- 27.6 reuses 27.5's `STRATEGY` parameters and sweep results (deflated Sharpe of the best grid point).
- 27.8 depends on 27.7's detector and 27.1's `BacktestRunner`, and becomes the second worked example in 27.5's notebook.
- 27.9 closes the epic: `research/README.md` replaces `research/BACKTESTING.md`; rules NB-01..NB-04 added to `platform/CLAUDE.md`; `pandas_ta`/`talib`/`%pip`/`custom_dydx_minute_bar` gone from the repo; the epic's `Known limit:`s listed in the DDD spine's Deferred section; epic marked done.
