---
title: 'Every Nautilus indicator and backtest pattern reachable from Jupyter'
type: 'feature'
created: '2026-10-01'
status: 'done'
review_loop_iteration: 0
baseline_commit: '15768f3c9de3f60969234c8335cd7fc914352d29'
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/platform/research/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The research context has the backtest plumbing (`NodeRunner`, sweeps, walk-forward, Monte Carlo, notebook 04) but uses only EMA and ATR of the ~40 `nautilus_trader.indicators` classes, runs none of the 17 upstream example strategies, and `RunSpec` exposes none of the execution models (11 fill models, 3 fee models, exec algorithms, insert/update/cancel latency) or the orders/fills reports the upstream backtest docs show.

**Approach:** A minimal direct build (operator decision 2026-10-01: no epic, **no new tests**): (1) a reference document mapping every upstream indicator, example strategy and backtest pattern to how it runs here; (2) `RunSpec` fields for fill/fee/latency models and exec algorithms, `RunResult` orders/fills frames and the full analyzer statistics; (3) two parameterised family strategies that reach every bar indicator by config name; (4) notebooks `07_indicator_atlas` and `08_strategy_gallery`. Upstream example strategies run as-is by string path (verified: `EMACrossConfig` et al. take exactly the `instrument_id`/`bar_type`/`trade_size`/`order_id_tag` the runner injects).

## Boundaries & Constraints

**Always:** FORK-01 (nothing under `nautilus_trader/`, `crates/`); NAUT-03 (every backtest through `NodeRunner`, strategies by string path); NB-01..04 (no formula token in a notebook cell -- `platform/tests/test_notebook_rules.py` `FORMULA_TOKENS`; every read bounded; jupytext pair via `make notebooks`; each notebook runs on the fixture in < 60 s under `warnings.simplefilter("error")` with a `NOTEBOOK_ENV` entry in `research/tests/test_notebooks.py`); `test_boundaries.py` (strategy modules import only `kernel` + `nautilus_trader`; research never imports `views`/`ranking`/`data_api`); NAUT-01 (no float round-trip into `Price`/`Quantity`); `MetricReport` stays exactly `kernel.performance_metrics.all_metrics`; LGPL header on every new file; ruff/mypy clean; DESIGN-01 docstrings naming the invariant; deliberate simplifications as `Known limit:` with an upgrade path.

**Ask First:** adding any dependency; changing `kernel/`; changing an existing notebook's outputs beyond the README index rows.

**Never:** new test files (existing suites must still pass); low-level `BacktestEngine` scripts (`NodeRunner.sweep` is the documented equivalent of `engine.reset()` loops); an L2 `book` data kind, `store_bars` kind, `fills="quotes"`, `chunk_size` streaming, paper-bot registration (all listed as follow-ups in the doc); porting `ema_cross_hedge_mode` (HEDGING OMS), `market_maker`/`orderbook_imbalance` (need L2), `market_buy_on_start`/`signal_strategy`/`subscribe`/`blank`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fill model | `RunSpec(fill_model={"name": "probabilistic", "prob_fill_on_limit": 0.2, "prob_slippage": 0.5, "random_seed": 42})` | `BacktestVenueConfig.fill_model` is an `ImportableFillModelConfig` for `ProbabilisticFillModel` | unknown `name` or key → `ValueError` in `__post_init__` naming the valid names |
| Fee model | `fee_model={"name": "fixed", "commission": "0.50 USDT"}` | `ImportableFeeModelConfig` for `FixedFeeModel` (`maker_taker` = `MakerTakerFeeModel`, no params; `per_contract` = commission) | unknown name / missing commission → `ValueError` |
| Latency | `latency={"base_latency_nanos": 0, "insert_latency_nanos": 300_000_000}` | replaces the `latency_ms` shortcut; all four `LatencyModelConfig` fields allowed | unknown key / negative → `ValueError` |
| Exec algorithm | `exec_algorithms=("nautilus_trader.examples.algorithms.twap:TWAPExecAlgorithm",)` + `EMACrossTWAP` | engine config carries an `ImportableExecAlgorithmConfig` (config path `...twap:TWAPExecAlgorithmConfig`, config `{}`); the run fills | a path without `:` → `ValueError` |
| Reports | any run | `RunResult.orders` / `.fills` are the engine's `generate_orders_report()` / `generate_order_fills_report()` frames (possibly empty); `nautilus_stats["general"]` present; `stats_returns` includes CAGR, Calmar, MaxDrawdown etc. (registered after `node.build()`, before `node.run()`) | missing engine → `RuntimeError` (as today) |
| Family strategy, bad axis | `MACrossStrategyConfig(ma_type="FOO")` or `IndicatorSignalStrategyConfig(signal="foo")` | `ValueError` listing the valid names, before any node runs | — |
| Family strategy, hole | a bar more than one step after the previous, or zero volume | every indicator reset, no entry on that bar (as `CandlePatternStrategy`) | — |
| Gallery, failing spec | a spec whose run raises | one leaderboard row with the error text in an `error` column, other rows unaffected | never skipped silently (DATA-07) |
| Atlas, fixture | 10 one-minute bars | every indicator spec in `catalog()` replayed; outputs NaN until initialized; no warning raised | — |

</frozen-after-approval>

## Code Map

- `research/application/ports.py` -- `RunSpec`/`RunResult` dataclasses (`__post_init__` validation, `RESERVED_PARAMS`, `DEFAULT_LATENCY_MS`); add the new fields + validation here.
- `research/application/backtest_runner.py` -- `NodeRunner.sweep` (one node, `build_run_config` per grid point, `_result` reads reports before dispose); `_latency_model` is the pattern for the fill/fee/latency factories; add `node.build()` + statistic registration before `node.run()`.
- `research/application/evaluation.py` -- frame helpers for notebook 04 (`metric_frame`, `trade_frame`, `timed_sweep`, `Sweep`); add `orders_frame`, `fills_frame`, `nautilus_stats_frame`.
- `nautilus_trader/backtest/config.py:457-700` -- `FillModelConfig`, `ImportableFillModelConfig`, `LatencyModelConfig`, `MakerTaker/Fixed/PerContractFeeModelConfig`, `ImportableFeeModelConfig`; `FillModelFactory` shows how a model's own config class is resolved (`MarketHours`, `VolumeSensitive`, `CompetitionAware` have extra ctor params at `backtest/models/fill.pyx:695/785/871` -- verify each model's config class there).
- `nautilus_trader/execution/config.py:131` -- `ImportableExecAlgorithmConfig(exec_algorithm_path, config_path, config)`; `nautilus_trader/examples/algorithms/twap.py:35` -- `TWAPExecAlgorithmConfig`.
- `nautilus_trader/backtest/node.py:241` -- `build()` creates every engine; `get_engine(id).portfolio.analyzer.register_statistic(...)`; `nautilus_trader/analysis/__init__.py` exports `CAGR, Alpha, BetaRatio, CalmarRatio, InformationRatio, MaxDrawdown, TrackingError, TreynorRatio` (not registered by `portfolio.pyx:173-189`).
- `nautilus_trader/trading/trader.py:843-876` -- `generate_orders_report`, `generate_order_fills_report`, `generate_positions_report`.
- `research/strategies/candle_pattern_strategy.py` -- the conventions and helpers to lift (`_resolve_bar_type`, `_size_problem`, `_check_count`, `_check_trade_size`, hole rule `_is_hole`, `_reset_signals`, stop handling); `research/strategies/example_strategy.py` -- `LogisticTrendStrategy` (replaced by the signal strategy's `logistic_trend`); `research/strategies/backtest_dydx.py` -- repoint its `strategy_path`/`config_path` to the new strategy.
- `nautilus_trader/indicators/{averages,momentum,trend,volatility,volume,fuzzy_candlesticks}.pyx` -- constructors and `update_raw` signatures (see Design Notes); `nautilus_trader/indicators/averages.pyx:898` `MovingAverageFactory.create` (no ADAPTIVE branch).
- `research/application/patterns.py:87-140` -- `bar_grid` (bars on the complete bucket grid, NaN at holes) and `ema_values` (replay-with-reset pattern) to reuse in the atlas service.
- `research/application/frames.py` -- `CatalogFrames.bars/seconds` (`BARS_COLUMNS`: `t` ms, `o,h,l,c,v`, `partial`; `SECONDS_COLUMNS` with decoded `bid_prices`.. lists).
- `research/notebooks/04_backtest_evaluation.py`, `06_candlestick_scanner.py`, `_params.py` -- the notebook shape (raw LGPL cell, Parameters cell via `Params.from_env()` + `setting()`, plotly, `plot_axis`); `research/tests/test_notebooks.py:84` `NOTEBOOK_ENV` + `_FIXTURE_OFI`.
- `research/README.md` -- notebook index table (add rows 07/08), "Backtesting & Strategy Development" (add the doc pointer, the new `RunSpec` fields, the family strategies); `research/BACKTESTING.md` -- redirect stub to delete.
- `platform/docs/NAUTILUS_UPSTREAM_REFERENCE.md` -- the existing upstream reference's tone; the new doc sits beside it.

## Tasks & Acceptance

**Execution:**
- [ ] `platform/docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` -- write the four tables (Nautilus indicators by family: constructor, `update_raw` inputs, outputs, "reached here by" column; kernel indicators + `CandlePattern`; 17 upstream example strategies with data needs and the string path + `RunSpec` kind to run each, or why not; backtest patterns of `docs/concepts/backtesting.md` + `examples/backtest/` mapped to `RunSpec` fields or the NAUT-03 exclusion), plus the two upstream pitfalls (factory ADAPTIVE gap; `examples/backtest/tardis_option_chain.py` passes `data_type=` for `data_cls`) and the follow-up list -- the user's requested knowledge overview, kept in the repo.
- [x] `research/application/ports.py` -- add `fill_model`, `fee_model`, `latency`, `exec_algorithms` to `RunSpec` with `__post_init__` validation against closed name tables (`FILL_MODELS`: `fill` (base `FillModel`), `best_price`, `one_tick_slippage`, `two_tier`, `three_tier`, `probabilistic`, `size_aware`, `limit_order_partial`, `market_hours`, `volume_sensitive`, `competition_aware`; `FEE_MODELS`: `maker_taker`, `fixed`, `per_contract`); add `orders`, `fills` to `RunResult` -- the spec is the one place a notebook names an execution model.
- [x] `research/application/backtest_runner.py` -- `_fill_model`/`_fee_model`/`_latency_model` factories building the importable configs; `exec_algorithms` on `BacktestEngineConfig`; in `sweep`, `node.build()` then register the 8 missing statistics on each engine's `portfolio.analyzer`, then `node.run()`; `_result` reads orders/fills reports and `get_performance_stats_general()` -- closes the execution-model and report gaps.
- [x] `research/application/evaluation.py` -- `orders_frame(result)`, `fills_frame(result)` (UTC `ts` index, same style as `trade_frame`), `nautilus_stats_frame(result)` (one row per statistic across `pnls`/`returns`/`general`).
- [x] `research/strategies/_bars.py` -- lift `_resolve_bar_type`, `_size_problem`, `_check_count`, `_check_trade_size` and a `BarSequence` helper (tracks last bar ns, step ns; `is_hole(bar)`, `in_order(bar)`) from `candle_pattern_strategy.py`; `candle_pattern_strategy.py` imports them (behaviour unchanged).
- [x] `research/strategies/ma_cross_strategy.py` -- `MACrossStrategyConfig` (`instrument_id`, `bar_type: str | None`, `trade_size`, `ma_type` (every `MovingAverageType` name), `fast_period`, `slow_period`, `exit`: `cross` | `atr_stop` | `trailing_atr`, `atr_period`, `atr_multiple`, `long_only`) + `MACrossStrategy`: two MAs via `MovingAverageFactory` except ADAPTIVE built explicitly (`period_er=period, period_alpha_fast=2, period_alpha_slow=30`), registered with `register_indicator_for_bars`; entry on cross (flat + `indicators_initialized()`), exit on opposite cross, or a reduce-only STOP_MARKET at `atr_multiple` ATR (`atr_stop`), or a TRAILING_STOP_MARKET with `trailing_offset = atr_multiple * ATR` (`trailing_atr`); hole resets.
- [x] `research/strategies/indicator_signal_strategy.py` -- `IndicatorSignalStrategyConfig` (`instrument_id`, `bar_type`, `trade_size`, `signal` name, `signal_params: dict`, `filter`: `none` | `vhf` | `volatility_ratio` (+ `filter_params`, `filter_min`), `exit`: `opposite` | `bars:<n>`, `allow_short`) + `IndicatorSignalStrategy` with a `_SIGNALS` table: name → (constructor from params, feed function taking a `Bar`, rule returning +1/0/-1). Signals: `bollinger`/`keltner`/`donchian` (close ≤ lower → +1, ≥ upper → -1, exit when back past middle), `rsi`/`stochastics`/`cci`/`cmo`/`rvi`/`psl` (below/above thresholds), `macd` (sign), `aroon` (sign), `directional_movement` (pos>neg), `ichimoku` (close vs cloud), `linear_regression` (slope sign), `bias` (sign), `archer` (long_run/short_run), `roc`/`efficiency_ratio` (sign / above threshold with ROC sign), `obv`/`kvo`/`pressure` (sign of value), `vwap` (close vs value), `fuzzy_candle` (direction × size), `logistic_trend` (kernel `OnlineLogisticTrend`, thresholds), `swings` (direction). Every Nautilus bar indicator appears once; the doc's "reached here by" column cites this table.
- [x] `research/strategies/example_strategy.py`, `research/strategies/backtest_dydx.py` -- delete `example_strategy.py`; repoint `backtest_dydx.py` to `IndicatorSignalStrategy` with `signal="logistic_trend"` (keep its `run()` signature; check `research/tests/test_watchlist_multi_coin_backtest.py` / `test_timeframe_backtest.py` still pass).
- [x] `research/application/indicator_atlas.py` -- `catalog() -> list[IndicatorSpec]` (name, family, overlay|pane, constructor kwargs sized for the real archive), `replay(grid, specs) -> dict[str, pd.DataFrame]` (one frame per spec, every output property, NaN until initialized, reset at NaN rows: the `ema_values` pattern), `snapshot_indicators(seconds) -> pd.DataFrame` (`Microprice`, `OrderFlowImbalance`, `MultiLevelOFI`, `MultiLevelOBI` over `CatalogFrames.seconds`), `fuzzy_frame`.
- [x] `research/application/gallery.py` -- `default_specs(params, instrument, data) -> list[tuple[str, RunSpec]]` (upstream `EMACross`, `EMACrossLongOnly`, `EMACrossBracket`, `BBMeanReversion`, `EMACrossTWAP` (with `exec_algorithms`), plus `MACrossStrategy` × {ema cross, hull atr_stop, kama trailing} and `IndicatorSignalStrategy` × {bollinger, macd, rsi, obv, fuzzy_candle}), `leaderboard(runner, specs) -> pd.DataFrame` (one row per spec: metrics columns + `trades`, `error`), `equity_frames(results)`, `execution_axes(spec)` (the same spec across fill models (seeded), fee models, latency variants) and `axis_table(runner, axes)`. `VolatilityMarketMaker` and `EMACrossTrailingStop` are excluded (they read `cache.quote_tick`, which the `bars:` kind never provides).
- [x] `research/notebooks/07_indicator_atlas.py` + `.ipynb` -- Parameters (`INSTRUMENT`, `BAR_SECONDS`, `SPECS` overrides via `setting`), candlestick with overlays, one pane per oscillator/volume family, fuzzy vector, snapshot indicators; markdown per section; no formula tokens.
- [x] `research/notebooks/08_strategy_gallery.py` + `.ipynb` -- leaderboard table + overlaid equity, execution-axes tables, slippage distribution from `fills_frame`, a reading guide saying how to copy a row's spec into 04.
- [x] `research/tests/test_notebooks.py` -- `NOTEBOOK_ENV` entries for 07 (fixture window, `BTC-USD-PERP.HYPERLIQUID`, periods ≤ 5) and 08 (fixture window, instrument, `NOTEBOOK_PERIODS` shrunk, `data="bars:1-MINUTE"`); harness wiring only, no new test.
- [x] `research/README.md`, `research/BACKTESTING.md` -- index rows 07/08, doc pointer, `RunSpec` fields and family strategies documented; delete the stub; run `make notebooks`.

**Acceptance Criteria:**
- Given the fixture archive, when `research/tests/test_notebooks.py` runs, then 07 and 08 pass in < 60 s each and every earlier notebook still passes.
- Given `python3 -m pytest -o addopts="" --rootdir=. research/tests tests -q`, when run on the host python, then it passes (boundary, notebook-rule, research-read and existing strategy tests included).
- Given `ruff check research docs`, `ruff format --check research`, `mypy research`, when run, then all clean.
- Given the fixture, when `NodeRunner().run(RunSpec(..., strategy_path="nautilus_trader.examples.strategies.ema_cross:EMACross", config_path="...:EMACrossConfig", params={"trade_size": "0.01", "fast_ema_period": 2, "slow_ema_period": 3}, data="bars:1-MINUTE"))`, then it returns a `RunResult` with non-empty `orders`.
- Given the doc, when every class name in `nautilus_trader.indicators.__all__` is grepped for, then each appears.

## Design Notes

- Indicator inputs (for `_SIGNALS` and the atlas): MAs/`MACD`/`RSI`/`ROC`/`CMO`/`RVI`/`PSL`/`EfficiencyRatio`/`VHF`/`Bias`/`Archer`/`LinearRegression`: `update_raw(close)`; `ATR`/`Bollinger`/`Keltner`/`KeltnerPosition`/`CCI`/`Stochastics`/`Ichimoku`/`VolatilityRatio`: `(high, low, close)`; `Aroon`/`DirectionalMovement`/`Donchian`: `(high, low)`; `Swings`: `(high, low, datetime)`; `OBV`: `(open, close, volume)`; `VWAP`: `(price, volume, datetime)`; `KVO`/`Pressure`: `(high, low, close, volume)`; `FuzzyCandlesticks`: `(open, high, low, close)`. Prefer `handle_bar` where the class has it; the explicit table is the fallback.
- Fill-model table shape: `{"probabilistic": ("nautilus_trader.backtest.models:ProbabilisticFillModel", "nautilus_trader.backtest.config:FillModelConfig"), ...}`; models with their own config class use it. Verify each pairing against `FillModelFactory`.
- Statistics after `node.build()`: `for engine in node.get_engines(): for stat in (CAGR(), ...): engine.portfolio.analyzer.register_statistic(stat)`.
- Gallery `leaderboard` catches `Exception` per spec into the `error` column: a visible failure, not a silenced one.

## Verification

**Commands:**
- `cd platform && make notebooks` -- expected: 07/08 `.ipynb` twins written, no diff on 01-06.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests tests -q` -- expected: all pass.
- `cd platform && ruff format --check research && ruff check research && mypy research` -- expected: clean.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests/test_notebooks.py -k runs_against_the_fixture --durations=0 -q` -- expected: 07/08 under 60 s.
