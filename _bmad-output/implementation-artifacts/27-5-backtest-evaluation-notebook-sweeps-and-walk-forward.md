# Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 27.1 and 27.2. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.5".

## Story

As a strategy researcher,
I want to run any strategy by string path over any bounded window and read one standard evaluation page,
so that every strategy is judged by the same equity, drawdown, metric, sweep and out-of-sample views, with nothing recomputed by hand.

## Acceptance Criteria

1. **Given** `BacktestRunner` and `research/strategies/*`
**When** `04_backtest_evaluation` runs with `STRATEGY`, `STRATEGY_CONFIG`, `PARAMS` (the same three parameters `ml_signals/backtest.ipynb` had)
**Then** it shows the equity curve with the underwater (drawdown) series and each drawdown episode listed, the `MetricReport` table (Sharpe, Sortino, Calmar, max drawdown, profit factor, expectancy, win rate, avg/max win and loss, returns volatility, exactly `performance_metrics.all_metrics`' keys, no additions), rolling Sharpe over a configurable window from `ReturnSeries`, the per-trade PnL distribution and holding-time distribution from `TradeLedger`, PnL by hour of day and by day of week, and the trade list; the notebook never touches `BacktestNode` directly and never sums a PnL itself

2. **Given** `BacktestRunner.sweep`
**When** the sweep section runs with a two-parameter grid
**Then** it renders a heatmap of the chosen metric over the grid (plotly), lists the top runs with their full `MetricReport`, and states the grid size and total runtime; the walk-forward section splits `START`–`END` into `N` consecutive in-sample/out-of-sample folds, picks each fold's parameters on the in-sample metric and reports the concatenated out-of-sample equity and metrics next to the in-sample ones; both sections stream the catalog through `BacktestDataConfig` and never materialise the window twice

3. **Given** `ml_signals/backtest.ipynb` (moved to `research/notebooks/` by 24.4)
**When** the story ships
**Then** it is deleted, `research/BACKTESTING.md` points at this notebook as the one way to evaluate a strategy interactively, and the smoke test runs the notebook against the fixture catalog with `OFIStrategy` and a `2 × 2` grid in under 60 s

## Tasks / Subtasks

- [ ] Task 1 — application additions (AC: #1, #2)
  - [ ] `research/application/walk_forward.py`: `folds(start, end, n, in_sample_fraction) -> list[Fold]`, `walk_forward(runner, spec, grid, folds, select_by) -> WalkForwardResult` (per fold: `sweep` on the in-sample window, pick `select_by` metric's best `config_id`, `run` the out-of-sample window with those params; concatenate out-of-sample `EquityCurve`s by re-basing each fold to the prior fold's terminal equity, documented as the convention). Tests on the fixture: two folds, a grid of two points, the selection picks the deterministic winner.
  - [ ] `RunResult` gains `pnl_by_hour_of_day()`/`pnl_by_weekday()` via `TradeLedger` methods from 27.1 (no new arithmetic in the notebook).
- [ ] Task 2 — `04_backtest_evaluation` (AC: #1, #2)
  - [ ] Sections: (1) Parameters (`STRATEGY`, `STRATEGY_CONFIG`, `PARAMS`, `DATA`, `STARTING_BALANCE`, `ROLLING_WINDOW`, `GRID`, `N_FOLDS`); (2) Single run → `RunResult`; (3) Equity + underwater + drawdown episodes table; (4) `MetricReport.as_table()`; (5) Rolling Sharpe; (6) Trade distributions (PnL, holding time), PnL by hour and weekday, trade list; (7) Sweep heatmap + top-5 table + grid size/runtime line; (8) Walk-forward: fold table (in-sample pick, out-of-sample metrics), concatenated OOS equity vs the single-run equity; (9) Reading guide prose (what a large in/out gap means, why the grid's best is optimistic — hand-off to 27.6's deflated Sharpe).
  - [ ] Default example: `research.strategies.ofi_strategy:OFIStrategy` / `OFIStrategyConfig`, `PARAMS` as the old notebook had (`trade_size`, `ofi_threshold`, `warmup_seconds`), `GRID = {"ofi_threshold": [1.5, 2.0], "ofi_window": [20, 40]}`.
- [ ] Task 3 — retire the old notebook and docs (AC: #3)
  - [ ] Delete `research/notebooks/backtest.ipynb` (post-24.4 path); `research/BACKTESTING.md` "Run from a notebook" section names `04_backtest_evaluation` and `BacktestRunner`.
  - [ ] Smoke test budget: the fixture is ~10 minutes of data; single run + `2 × 2` sweep + 2 folds must finish in < 60 s. If it does not, shrink the fixture window for this notebook via its Parameters, never loosen the assertion.

## Dev Notes

- **Nothing new in the notebook:** every number is a `RunResult` field or a `research.domain` call. The notebook has no `sum`, no `mean`, no `np.` call of its own (NB-01 as drafted in 27.9).
- **`BacktestNode` idioms** are in `BacktestRunner` (27.1): config-id attribution, one node per sweep, `LoggingConfig(bypass_logging=True)`. Read `ml_signals/strategies/backtest_dydx.py:19-60` before touching the runner.
- **Walk-forward equity concatenation** is a convention (re-base each OOS fold to the prior terminal equity). State it in the docstring and the notebook. Do not pretend it is the equity of one continuous run.
- **Sweep memory:** one `BacktestNode` with N `BacktestRunConfig`s shares the streamed data configs; N separate nodes would read the window N times (MEM-01). Keep it one node; record the peak RSS of the smoke run in Completion Notes.
- **Project rules:** NAUT-03, MEM-01, SSOT-02 (metrics only via `performance_metrics`), TEST-01/03/04, READ-03.
- **Working directory:** `platform/`; `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`.

### Project Structure Notes

- `research/notebooks/04_backtest_evaluation.py` + `.ipynb`; `research/application/walk_forward.py`; `research/tests/test_walk_forward.py`; delete `research/notebooks/backtest.ipynb`.

### References

- Epic text: "Story 27.5"; FR75
- Code: `ml_signals/backtest.ipynb` (the three parameters to keep), `ml_signals/strategies/backtest_dydx.py`, `ml_signals/strategies/snapshot_backtest.py`, `ml_signals/performance_metrics.py`
- Sprint action items (epic 2/3): `BacktestNode` result attribution and the native-crash mitigation (one engine construction per test file) — respect both in `research/tests`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
