---
title: 'Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: 'b931190458a137392f837cad70002a3955ef6075'
final_revision: 'e97cd0b51de021847b54480898a1edff33284bae'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-5-backtest-evaluation-notebook-sweeps-and-walk-forward.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** There is no standard way to judge a strategy. The legacy `research/notebooks/backtest.ipynb` prints Nautilus's raw stats for one run. It has no equity or drawdown view, no sweep and no out-of-sample check, so every strategy gets judged by hand in a different way.

**Approach:** Ship `research/notebooks/04_backtest_evaluation.py` + `.ipynb` on the 27.2 harness. Every number comes from `BacktestRunner` (`NodeRunner`), a `RunResult` field or a `research.domain` value. A new `research/application/evaluation.py` turns them into frames, a sweep result and a heatmap pivot. A new `research/application/walk_forward.py` builds the folds, picks each fold's parameters in-sample and joins the out-of-sample (OOS) equity. The old notebook is deleted, and `BACKTESTING.md` points at the new one.

## Boundaries & Constraints

**Always:**
- The notebook never imports `BacktestNode` or reads the catalog itself. Every read happens inside `NodeRunner` through `BacktestDataConfig` (the runner's `chunk_size` Known limit stands). No cell has `sum(`, `mean`, `np.`, or arithmetic on PnL. Cells hold only calls, loops, prints and plotly.
- Metrics come only from `MetricReport` (`performance_metrics.all_metrics`, SSOT-02). The table shows exactly `MetricReport.field_names()`. Rolling Sharpe is `ReturnSeries.from_equity(...).rolling_sharpe(...)`.
- PnL by hour/weekday comes from `RunResult.pnl_by_hour_of_day()`/`pnl_by_weekday()`. These delegate to `TradeLedger.by_hour_of_day()`/`by_weekday()` and add no arithmetic.
- Walk-forward folds are `n` consecutive, equal, non-overlapping segments of `[start, end)`. Each segment's first `in_sample_fraction` is in-sample and the rest is OOS, and all bounds are integer ns. `select_by` must be a `MetricReport` field. The highest value wins, because every `all_metrics` value is higher-is-better (`max_drawdown` and the losses are negative). None ranks below any number, and ties go to the earliest grid point. When every point is None, the first grid point is taken, and the fold records `pick_value=None` so the table shows it.
- The OOS equity convention: each fold runs from the full `starting_balance`. Its curve is shifted additively by the cumulative OOS PnL of the prior folds (prior terminal − `starting_balance`), which matches `equity_returns`' additive `equity += pnl`. The docstring and the notebook both state that this is not the equity of one continuous run. OOS metrics are `MetricReport.from_ledger` over all OOS trades merged (`TradeLedger.of`). In-sample metrics are the same over the winners' in-sample trades.
- A zero-trade run, an equity curve too short for the rolling window, or a grid whose metric is None everywhere each print a sentence. None of them raises or draws an empty figure.
- Every new function's docstring names its invariant. Each file carries the LGPL header, full type hints, ruff at 100 and one import per line.
- Notebook constants that the harness must shrink to fit the fixture (`INSTRUMENT`, `PARAMS`, `GRID`, `N_FOLDS`, `START`/`END` via env) are read through one `_params.py` helper, `setting(name, default)`. It returns the JSON in env `NOTEBOOK_<NAME>` when set, else the default, and raises `ValueError` on a type mismatch. The notebook never reads env itself.

**Block If:**
- `OFIStrategy` makes no round trip on any fixture instrument at any reasonable override. The smoke test would then prove nothing about the trade sections.
- The notebook plus the namespace test cannot finish under 60 s per run without loosening `RUN_SECONDS_LIMIT`.

**Never:**
- No new dependency, and no edit under `nautilus_trader/` or `crates/`. Never write `sprint-status.yaml`.
- No change to `NodeRunner`'s attribution or disposal semantics, and no second backtest path.
- No new `RESEARCH_*_SERVICES` boundary entry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Folds | `[0, 1000)`, n=2, fraction 0.7 | `(0,350,350,500)`, `(500,850,850,1000)` | n<1, fraction ∉ (0,1), or an empty in/OOS part → ValueError |
| Selection | metric values `[None, 0.3, 0.3, 0.1]` | index 1, pick 0.3 | unknown `select_by` → ValueError |
| All None | `[None, None]` | index 0, `pick_value=None` | — |
| OOS concat | fold equities `[100→110]`, `[100→95]`, start 100 | `[100,110,110,105]` (fold 2 shifted +10) | overlapping stamps → EquityCurve ValueError |
| Grid | `{"a":[1,2],"b":[3,4]}` | 4 points in product order | an empty axis or no key → ValueError |
| Heatmap | 2-key grid, one metric None | pivot with NaN in that cell | grid keys ≠ 2 → ValueError |
| Rolling Sharpe | 10 min of equity, 48 × 1 h window | all NaN → a sentence | window < 2 days → the domain's ValueError |

</intent-contract>

## Code Map

- `platform/research/application/backtest_runner.py` -- `NodeRunner.run`/`sweep` (one node per call, results in grid order, raises on an empty window).
- `platform/research/application/ports.py` -- `RunSpec` (frozen, `window_ns`), `RunResult` (add the two delegating methods), `BacktestRunner` Protocol.
- `platform/research/domain/{equity,trades,report,returns}.py` -- `EquityCurve.drawdowns()`/`underwater()`, `TradeLedger.holding_times_s/by_hour_of_day/by_weekday/realized_pnls`, `MetricReport.as_table/field_names/from_ledger`, `ReturnSeries.from_equity/rolling_sharpe`.
- `platform/research/notebooks/_params.py`, `03_correlation.py` -- the Parameters pattern and the cell style.
- `platform/research/tests/test_notebooks.py` -- `_run` harness, `LEGACY_NOTEBOOKS_UNTIL` (drop `backtest.ipynb`).
- `platform/research/tests/test_backtest_runner.py` -- the OFI params that trade on synthetic data (`warmup_seconds 0`, small windows).
- `platform/research/tests/fixture_catalog.py` -- data window `DATA_START_NS`…+600 s. The dYdX BTC outage is seconds 200–290, so the smoke test uses a Bybit/Hyperliquid instrument over the data window.
- Docs: `platform/research/BACKTESTING.md:81`, `platform/research/README.md:14-19`, `platform/ARCHITECTURE.md:343-357`, DDD spine `ARCHITECTURE-SPINE.md:157,439`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/application/ports.py` -- add `RunResult.pnl_by_hour_of_day()` and `pnl_by_weekday()`, which delegate to `self.trades`.
- [x] `platform/research/application/walk_forward.py` -- `Fold` (frozen; `in_start`, `in_end`, `oos_start`, `oos_end`, validated), `folds(start, end, n, in_sample_fraction)`, `select_best(results, select_by) -> (index, value | None)`, `FoldResult` (fold, in-sample winner, OOS result, pick value), `WalkForwardResult` (folds, `oos_equity`, `oos_metrics`, `in_sample_metrics`), `concat_equity(curves, starting_balance)`, and `walk_forward(runner, spec, grid, folds, select_by)`. The last does an in-sample `sweep` per fold via `dataclasses.replace(spec, start=, end=)`, then `run`s the OOS window with the winner's point.
- [x] `platform/research/application/evaluation.py` -- `grid_points(grid)`, `Sweep` (results, points, elapsed_s) and `timed_sweep(runner, spec, grid)`, `metric_grid(sweep, metric) -> DataFrame` (the pivot over the two grid keys), `sweep_frame(sweep)` (params + every metric + config_id), `top_runs(sweep, metric, n)`, `drawdown_frame(curve)`, `equity_frame(curve)` (ts, equity, underwater), `rolling_sharpe_frame(curve, period_s, window)`, `trade_frame(ledger)`, `pnl_by_hour_frame`/`pnl_by_weekday_frame(result)` (only the buckets with an exit; an hour/day with none stays absent, never a 0), `fold_frame(wf)`.
- [x] `platform/research/notebooks/_params.py` -- `setting(name, default)` as in Always.
- [x] `platform/research/notebooks/04_backtest_evaluation.py` + `.ipynb` -- sections §1–§9 as the story's Task 2 lists them. Defaults: `INSTRUMENT` = first of `INSTRUMENTS`; OFIStrategy; `PARAMS = {"trade_size": "0.01", "ofi_threshold": 2.0, "warmup_seconds": 600}`; `GRID = {"ofi_threshold": [1.5, 2.0], "ofi_window": [20, 40]}`; `STARTING_BALANCE = 10_000`; `ROLLING_PERIOD_S = 3600`; `ROLLING_WINDOW = 48`; `N_FOLDS = 3`; `IN_SAMPLE_FRACTION = 0.7`; `SELECT_BY = setting("SELECT_BY", "expectancy")` (see Spec Change Log); `DATA = "seconds"`. §9 is the reading guide, handing off to 27.6's deflated Sharpe.
- [x] `platform/research/tests/test_walk_forward.py` -- the I/O matrix rows for folds, selection, concat and grid (pure), plus one fixture run: two folds, a two-point grid, and a winner equal to the in-sample sweep's argmax. OOS stamps lie in the OOS windows.
- [x] `platform/research/tests/test_evaluation.py` -- `metric_grid` pivot and NaN, `top_runs` order, the frames' columns, `pnl_by_*` equal to the ledger's dicts.
- [x] `platform/research/tests/test_notebooks.py` -- `NOTEBOOK_ENV` per notebook name, applied by `_run`. For `04_backtest_evaluation.py`: the data window, a non-outage instrument, OFI params that trade, `N_FOLDS=2`. Drop the `backtest.ipynb` exemption. `test_notebook_backtest_evaluation.py` checks the namespace: the metric table keys equal `all_metrics` keys, the sweep has 4 results and a 2×2 heatmap, there are 2 folds, the run made ≥ 1 trade, and no forbidden token (`BacktestNode`, `sum(`, `np.`, `.mean(`) appears in any code cell.
- [ ] Delete `platform/research/notebooks/backtest.ipynb`. Update the docs: the `BACKTESTING.md` "Run from a notebook" section, the `README.md` index row with the legacy line trimmed, the `ARCHITECTURE.md` notebooks paragraph, and the spine research row/tree (`evaluation.py`, `walk_forward.py`, `04_backtest_evaluation`, `[amended 2026-09-28: Story 27.5]`).

**Acceptance Criteria:**
- Given the fixture, when `04_backtest_evaluation.py` runs through the harness (`simplefilter("error")`, headless), then it finishes in < 60 s with a single run, a 2 × 2 sweep and 2 walk-forward folds, and its `.ipynb` passes the pairing test.
- Given the full platform suite, when run, then there are no failures beyond the baseline, and `test_boundaries.py` passes with unchanged allowlists.

## Design Notes

- **Why `setting()`:** the defaults must match the story for real data (warmup 600 s, a one-day window). The fixture is 10 minutes, so the harness overrides them, which is the story's "shrink via its Parameters". Env-as-JSON keeps the notebook free of env reads and needs no runtime `if`.
- **Memory:** a single run, a sweep and the walk-forward each build one `BacktestNode` per call. Each grid point loads its own window (the runner's `chunk_size` Known limit), and this story does not change that. Record the smoke run's peak RSS in the Auto Run Result.

## Verification

**Commands:**
- `cd platform && ../.venv/bin/python -m pytest -o addopts="" --rootdir=. research/tests tests/test_boundaries.py -q` -- expected: all pass
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: only the baseline Redis `data_api` failures plus the known flaky ranking test
- ruff check/format and mypy on the changed files; `make notebooks` (or `uv run jupytext --sync`) -- expected: clean beyond the pre-existing errors

## Spec Change Log

### 2026-09-28 — follow-up review: `SELECT_BY` default
- **Trigger:** the follow-up review found the notebook's `SELECT_BY` default is `expectancy`, where this spec's Tasks line said `"sharpe_ratio"`.
- **Amended:** the Tasks line for `04_backtest_evaluation`: `SELECT_BY = setting("SELECT_BY", "expectancy")`. Nothing inside `<intent-contract>` changed.
- **Known-bad state avoided:** a `sharpe_ratio` default. Nautilus bins returns into UTC days, so every return statistic needs realized PnL on two UTC days inside each in-sample window. On the default one-day window split into 3 folds, Sharpe is undefined at every grid point. Every fold would then silently take the first point, and the heatmap would be empty.
- **KEEP:** `SELECT_BY` stays a `setting`, validated before any run by `check_heatmap_sweep`. The Parameters markdown says when to switch to a return statistic.

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9 (high 0, medium 1, low 8)
- defer: 0
- reject: 16 (high 0, medium 0, low 16)
- addressed_findings:
  - `[medium]` `[patch]` `_run_fold` added the fold's name only to a `RuntimeError`. The `seconds` kind's empty window raises `ValueError` (`write_derived_quotes`), which lost the fold, and the docstring claimed `RuntimeError`. Both types now get the fold prefix and keep their type. The docstring was corrected, and a parametrized test was added.
  - `[low]` `[patch]` `walk_forward` claimed the folds were checked before any run, but their order and overlap were only checked after every backtest. A new `_check_fold_order` runs before the first run and is shared with `_check_folds`. A test asserts zero runner calls.
  - `[low]` `[patch]` The notebook split the folds in §8, after the single run and the sweep, so a window too short for `N_FOLDS` failed late. `FOLDS` is now built in the Parameters cell.
  - `[low]` `[patch]` The `SELECT_BY` default (`expectancy`) did not match the spec's `sharpe_ratio`. The spec was corrected to the implementation (see Spec Change Log), and the code is unchanged.
  - `[low]` `[patch]` The page did not say that the §7 sweep is fitted on the whole window, including §8's out-of-sample stretches. The §7 markdown now says its best cell is picked with hindsight.
  - `[low]` `[patch]` `_check_axis` accepted `["2", 2]`, which share one heatmap label. Distinct `str()` labels are now required, and a test case was added.
  - `[low]` `[patch]` `setting()` accepted `NaN`/`Infinity`. `parse_constant` now refuses them, with tests. The docstring now says a default must be a JSON type (a `tuple`/`None` default could never be overridden) and is re-wrapped to 100 columns.
  - `[low]` `[patch]` `fold_frame` wrote `in_sample_<select_by>` twice (the first write was dead). It is now inserted once, as an object column, with a comment on why.
  - `[low]` `[patch]` The notebook test's `_code_cells` skipped a code cell whose header carries metadata (`# %% tags=[...]`). Now only a `[markdown]`/`[raw]` suffix is skipped.

## Auto Run Result

Status: done

**Summary:** This was a follow-up review of Story 27.5 (the backtest evaluation notebook, sweeps and walk-forward, first committed at `6962a715f5`). It applied 9 patches, all localized hardening of error paths, validation and documentation. No behaviour or API changed on the success path. It also corrected the spec's stale `SELECT_BY` default to match the implementation.

**Files changed (this pass):**
- `platform/research/application/walk_forward.py`: `_check_fold_order`, run before any backtest. A fold's `ValueError` is prefixed like its `RuntimeError`, and the docstring is now accurate.
- `platform/research/application/evaluation.py`: an axis label must be unique by `str()`. `fold_frame` writes its pick column once.
- `platform/research/notebooks/_params.py`: `setting()` refuses non-finite JSON numbers. The docstring was re-wrapped and documents that a default must be a JSON type.
- `platform/research/notebooks/04_backtest_evaluation.py` / `.ipynb`: `FOLDS` in the Parameters cell and the §7 hindsight sentence. Re-paired with `jupytext --sync`.
- `platform/research/tests/test_walk_forward.py`, `test_notebook_params.py`, `test_notebook_backtest_evaluation.py`: new tests for the fold order, the fold-prefixed errors, the label collision and non-finite settings. The code-cell scan is hardened.

**Review findings:** 9 patches applied (1 medium, 8 low), 0 deferred, 16 rejected. The main rejections, checked against the code:
- Infinite metrics: Nautilus returns NaN, which `_clean` maps to None. This was verified by running `ProfitFactor`/`SortinoRatio`/`CalmarRatio` on returns with no losses.
- The disjoint fold layout and the rolling-Sharpe `ValueError` under two days: both are mandated by the intent contract.
- Duplicate `Sweep` points: `NodeRunner.sweep` refuses them, and a pandas pivot raises on duplicates.
- Style or test-cost observations: the notebook runs twice (the 03 precedent), the fixture OFI params are copied, `_utc` loops, and the docs repeat each other.

**Follow-up review recommended:** false. The fixes are localized, mostly low severity, and covered by new tests.

**Verification:**
- `../.venv/bin/python -m pytest -o addopts="" --rootdir=. research/tests tests/test_boundaries.py -q`: 461 passed (453 + 8 new).
- Full platform suite (`python3`): 2448 passed, 1 skipped, 10 failed. The failures are exactly the baseline: 9 Redis-dependent `data_api` tests and the flaky `ranking/tests/test_metrics_store.py` test.
- `ruff check` and `ruff format --check` are clean on every changed file, and `mypy` is clean on `walk_forward.py`, `evaluation.py` and `_params.py`. `jupytext --sync` re-paired the notebook.

**Residual risks (unchanged from the first run):**
- A data gap inside any fold aborts the whole walk-forward (a `Known limit:`).
- Each OOS run spends its warm-up inside its own window.
- The equity is not marked to market.
- Each grid point loads its own window (the runner's `chunk_size` Known limit).
