# Story 27.6: Monte Carlo and robustness notebook

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 27.1, 27.2 and 27.5 (a `RunResult` and the sweep). Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.6".

## Story

As a strategy researcher,
I want the distribution of outcomes a strategy's trade record implies, not a single equity path,
so that I can see the drawdown I should expect, the probability of ruin, a confidence interval on Sharpe, and whether a sweep-selected parameter set is likely overfit.

## Acceptance Criteria

1. **Given** `research/domain/monte_carlo.py`
**When** the story ships
**Then** it holds, over numpy only and a caller-supplied `numpy.random.Generator` seed recorded in every result (`MonteCarloResult.seed`, so a figure is reproducible): `bootstrap_trades(TradeLedger, n_paths, seed)` (trade-order resampling with replacement, returning terminal-wealth, max-drawdown and Sharpe distributions), `block_bootstrap_returns(ReturnSeries, block_len, n_paths, seed)` (stationary block bootstrap preserving autocorrelation), `risk_of_ruin(paths, ruin_level)`, `sharpe_confidence_interval(ReturnSeries, n_paths, seed, level)`, `deflated_sharpe(observed_sharpe, n_trials, returns_skew, returns_kurtosis, n_obs)` (Bailey & López de Prado's deflated Sharpe ratio, the multiple-testing correction for a sweep), and `probabilistic_sharpe(observed, benchmark, n_obs, skew, kurtosis)`; each function's docstring names its invariant (paths never exceed `n_paths`, a resampled ledger has the same trade count, a zero-variance series returns a stated `None` rather than a division error) and cites the formula's source; tests check closed-form cases (a constant-return series yields an interval of zero width, a ledger of one trade yields identical paths, `deflated_sharpe` with `n_trials = 1` equals `probabilistic_sharpe`)

2. **Given** `BacktestRunner` and the domain functions
**When** `05_monte_carlo` runs
**Then** it takes a `RunResult` (or the notebook's own run with the same `STRATEGY` parameters as 27.5), shows the fan chart of bootstrapped equity paths with the observed path, the max-drawdown and terminal-wealth histograms with the observed values marked, risk of ruin at three ruin levels, the Sharpe confidence interval, and, when a sweep was run, the deflated Sharpe of the best grid point with a plain-language verdict line; every figure states its seed and path count; the smoke test runs it with `n_paths = 200`

## Tasks / Subtasks

- [ ] Task 1 — `research/domain/monte_carlo.py` (AC: #1)
  - [ ] `MonteCarloResult` (frozen: `seed`, `n_paths`, `paths: np.ndarray` shape `(n_paths, n_steps)` of equity, `terminal: np.ndarray`, `max_drawdown: np.ndarray`, `sharpe: np.ndarray`, `period_seconds`); `bootstrap_trades` (resample `realized_pnls()` with replacement, cumulative sum + `starting_balance`), `block_bootstrap_returns` (Politis–Romano stationary bootstrap: geometric block lengths with mean `block_len`, wrap-around), `risk_of_ruin(result, ruin_level) -> float` (fraction of paths whose equity ever falls below `ruin_level × starting_balance`), `sharpe_confidence_interval(series, n_paths, seed, level)` (block bootstrap of the Sharpe statistic computed by `performance_metrics.return_stats`, percentile interval), `probabilistic_sharpe`, `deflated_sharpe` (expected maximum Sharpe over `n_trials` from the Euler–Mascheroni approximation, then PSR against it), `skew_kurtosis(series)` (numpy moments; the inputs the two Sharpe corrections need). Every docstring: invariant + source (Bailey & López de Prado 2014, "The Deflated Sharpe Ratio"; Politis & Romano 1994).
  - [ ] Zero-variance and empty-ledger inputs return `None`/an empty result with a stated reason; never a division warning (TEST-04 turns it into a failure).
  - [ ] `research/tests/test_monte_carlo.py`: the closed-form cases in AC #1 plus reproducibility (same seed → identical arrays), `n_paths` respected, resampled ledger length preserved, `risk_of_ruin` of a strictly increasing ledger `== 0.0`.
- [ ] Task 2 — `05_monte_carlo` (AC: #2)
  - [ ] Sections: (1) Parameters (adds `N_PATHS`, `SEED`, `BLOCK_LEN`, `RUIN_LEVELS`, `CONFIDENCE`); (2) Run or load (`RunResult` from 27.5's parameters; a `SWEEP` toggle to also run the grid); (3) Trade-order bootstrap fan chart with the observed path; (4) Max-drawdown and terminal-wealth histograms with observed values marked; (5) Block bootstrap of returns: the same figures, and the difference explained (trade order vs return dependence); (6) Risk of ruin at three levels; (7) Sharpe CI; (8) Deflated Sharpe of the sweep's best point with a verdict sentence ("the best grid point's Sharpe is not distinguishable from zero after N trials" or the converse); (9) Reading guide.
  - [ ] Every figure title carries `seed=… paths=…`.
- [ ] Task 3 — smoke budget (AC: #2)
  - [ ] Fixture run with `N_PATHS = 200`, `SWEEP = 1` (`2 × 2`) under 60 s.

## Dev Notes

- **numpy only** (NFR12). The block bootstrap, PSR and DSR are ~80 lines; the normal CDF needed by PSR is `math.erf`, no scipy. Cite the formula next to the code.
- **Sharpe is not recomputed here:** the per-path Sharpe uses `performance_metrics.return_stats` on each resampled series so the statistic matches the `MetricReport` (SSOT-02). If that is too slow for 200 paths, vectorise the resampling but still call the one statistic function on each path; record timings.
- **Monte Carlo on closed trades only:** open positions at `END` are excluded by construction (`TradeLedger` is closed trades). `Known limit:` in the module docstring; upgrade path is mark-to-market of open positions from the account report.
- **Project rules:** DESIGN-01 (invariant per function), TEST-01 (financial calculations), TEST-03 (closed-form, no mocks), TEST-04, READ-01 (functions under ~30 lines: split the bootstrap into resample/evaluate), READ-03.
- **Working directory:** `platform/`; `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`.

### Project Structure Notes

- `research/domain/monte_carlo.py`; `research/notebooks/05_monte_carlo.py` + `.ipynb`; `research/tests/test_monte_carlo.py`.

### References

- Epic text: "Story 27.6"; FR76, NFR12
- Code: `ml_signals/performance_metrics.py` (`return_stats`), `research/domain/{equity,returns,trades}.py` (27.1), `research/application/backtest_runner.py` (27.1), `walk_forward.py` (27.5)
- Sources to cite in docstrings: Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"; Politis & Romano (1994) "The Stationary Bootstrap"

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
