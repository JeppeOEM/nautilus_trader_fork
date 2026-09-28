---
title: 'Story 27.6: Monte Carlo and robustness notebook'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: 'd2bcff0cdfc82a3051c8908d57a41543094a1d72'
final_revision: '077136d598a7817c3a9e56ce8fe530338a8e2d21'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-6-monte-carlo-and-robustness-notebook.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** A backtest gives one equity path and one Sharpe. It does not show the drawdown to expect, the chance of ruin, how uncertain the Sharpe is, or whether the best point of a sweep is only luck.

**Approach:** Add `research/domain/monte_carlo.py`, a set of pure numpy functions with seeded, reproducible results. They cover:
- a trade-order bootstrap
- a stationary block bootstrap of returns
- risk of ruin
- a Sharpe confidence interval
- the probabilistic Sharpe ratio (PSR) and deflated Sharpe ratio (DSR), with the skew and kurtosis they need

A thin `research/application/robustness.py` turns these into frames and plain-language notes. The notebook `research/notebooks/05_monte_carlo.py` + `.ipynb` runs 27.5's strategy through `NodeRunner` and draws every figure. Each figure is labelled with its seed and path count.

## Boundaries & Constraints

**Always:**
- **Dependencies.** `monte_carlo.py` imports only stdlib (`math`, `statistics.NormalDist` for Φ/Φ⁻¹), numpy, `kernel.performance_metrics` and `research.domain`. The domain-layering test must pass unchanged.
- **Seeds and path counts.** Every stochastic function takes `seed: int` and uses `np.random.default_rng(seed)`. The result records `seed` and `n_paths`, the same seed gives identical arrays, and `paths.shape[0] == n_paths`.
- **Sharpe comes from Nautilus.** Every Sharpe goes through `kernel.performance_metrics` (SSOT-02) and is never a local mean/std formula.
  - Trade bootstrap, per path: the resampled PnLs are placed at the ledger's *original* exit stamps, then `pnl_by_day` → `equity_returns(…, starting_balance)` → `return_stats(...)["sharpe_ratio"]`. The observed Sharpe (the unshuffled ledger) therefore equals `MetricReport.from_ledger(ledger, starting_balance).sharpe_ratio`.
  - Block bootstrap, per path: `return_stats(dict(zip(ts, resampled)))`, as `ReturnSeries.rolling_sharpe` does.
  - An undefined Sharpe (None) is NaN in the array.
- **Paths.** Equity paths include step 0 (the starting balance).
  - Trade bootstrap: the steps are `starting_balance + cumsum(resampled pnls)`, giving `n_trades + 1` steps.
  - Block bootstrap: the steps are `starting_balance × cumprod(1 + r)` over the series' *finite* points (NaN gaps dropped, as `as_dict` drops them), giving `n_finite + 1` steps.
- **Path statistics.** A path's max drawdown is `min(equity / running peak − 1)`, with the peak starting at `starting_balance`. This is `EquityCurve.underwater()`'s definition at step granularity. It is *not* the daily-binned `MetricReport.max_drawdown`, and both the docstring and the notebook say so. Terminal wealth is the last step.
- **Observed values.** `MonteCarloResult` also carries the observed (unresampled) path, terminal wealth, max drawdown and Sharpe, computed by the same functions.
- **Degenerate inputs.** An empty ledger, or a series with no finite point, returns a `MonteCarloResult` with a `note` giving the reason. Every path is then the starting balance, and the Sharpe array is all NaN.
  - A zero-variance series makes `sharpe_confidence_interval` and `skew_kurtosis` return None.
  - PSR/DSR return None when `n_obs < 2` or the variance term `1 − γ3·SR + (γ4−1)/4·SR² ≤ 0`.
  - Nothing emits a numpy warning, because notebooks and tests run under `simplefilter("error")`.
- **Sharpe units and formulas.** PSR/DSR take the Sharpe *per observation* (not annualised). Kurtosis is Pearson (normal = 3).
  - PSR = Φ((SR − SR*)·√(n−1) / √(1 − γ3·SR + (γ4−1)/4·SR²)).
  - DSR = PSR against SR₀ = σ₀·((1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))), where γ is the Euler–Mascheroni constant and σ₀ = √(1/(n−1)), the Sharpe estimator's standard error under a zero-Sharpe null.
  - With N = 1, SR₀ = 0, so DSR equals PSR against 0.
  - `σ₀` is a `Known limit:`. The upgrade path is to pass the empirical variance of the Sharpes across trials.
  - The notebook converts the `MetricReport` annualised daily Sharpe into a per-day Sharpe (÷√365). A test pins that this matches mean/std(ddof = 1) of the `equity_returns`.
- **Docstrings.** Every function's docstring names its invariant and cites its source: Bailey & López de Prado (2014), "The Deflated Sharpe Ratio"; Politis & Romano (1994), "The Stationary Bootstrap". The module docstring carries a `Known limit:` that the Monte Carlo uses closed trades only, with the upgrade path of marking open positions to market from the account report.
- **Notebook cells.** Cells hold only calls, loops, prints and plotly. No cell contains `BacktestNode`, `sum(`, `np.` or `.mean(`. The notebook reads its constants through `_params.setting`. Every figure title contains `seed=<SEED> paths=<N_PATHS>`.
- **File standards.** Each file has the LGPL header, full type hints, ruff at line length 100, one import per line, and functions under ~30 lines.

**Block If:**
- The notebook cannot finish under 60 s with `N_PATHS = 200` and a 2 × 2 sweep on the fixture without loosening `RUN_SECONDS_LIMIT`.

**Never:**
- No scipy or other new dependency.
- No edit under `nautilus_trader/` or `crates/`.
- No write to `sprint-status.yaml`.
- No new `RESEARCH_*_SERVICES` boundary entry.
- No second backtest path, and no change to `NodeRunner`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One trade | ledger `[+5]`, balance 100, 50 paths | every path `[100, 105]` | — |
| Reproducible | same ledger, same seed, twice | identical `paths`, `terminal`, `max_drawdown`, `sharpe` | — |
| Monotone ledger | all PnL > 0 | `risk_of_ruin(r, 0.9) == 0.0` | `ruin_level ∉ (0, 1]` → ValueError |
| Empty ledger | `TradeLedger(())` | paths all `[balance]`, `note` set | — |
| Bad sizes | `n_paths < 1` or `block_len < 1` | — | ValueError |
| Constant 8 h returns | r = 0.01 at 8 h steps, starting 16:00 UTC | CI `low == high` == observed Sharpe (daily bins differ, so the Sharpe is defined) | — |
| Zero variance | constant daily returns | `sharpe_confidence_interval` → None, `skew_kurtosis` → None | — |
| DSR, N = 1 | any SR, skew, kurt, n | `deflated_sharpe(sr, 1, …) == probabilistic_sharpe(sr, 0, …)` | `n_trials < 1` → ValueError |
| PSR at benchmark | SR == SR* | 0.5 | `n_obs < 2` → None |

</intent-contract>

## Code Map

- `platform/research/domain/returns.py` -- `ReturnSeries` (values/ts_ns/period_seconds, `as_dict`, `from_equity`), and the `rolling_sharpe` pattern for `return_stats` per window.
- `platform/research/domain/trades.py`, `equity.py`, `report.py` -- `TradeLedger.realized_pnls/pnl_by_day`, the `EquityCurve.underwater` definition, and `MetricReport.from_ledger`.
- `platform/kernel/performance_metrics.py:105,131` -- `equity_returns` and `return_stats` (Nautilus `SharpeRatio(period=365)`, daily-binned, NaN → None).
- `platform/research/application/evaluation.py` -- `grid_points`, `Sweep`, `timed_sweep` (reused for the DSR sweep), and the note/frame style.
- `platform/research/notebooks/04_backtest_evaluation.py`, `_params.py` -- the Parameters-cell pattern (`setting`), and `STRATEGY`/`PARAMS`/`GRID` to reuse.
- `platform/research/tests/test_notebooks.py:87` -- `NOTEBOOK_ENV`, `_FIXTURE_OFI` and `_run`. `test_notebook_backtest_evaluation.py` is the namespace-test pattern.
- `platform/tests/test_boundaries.py:444-510` -- the domain layering (numpy allowed, pandas not).
- Docs to amend:
  - DDD spine `ARCHITECTURE-SPINE.md:157` (research row) and `:439` (tree)
  - `platform/ARCHITECTURE.md` (the research domain/application lists and the notebooks paragraph, ~:307-370)
  - `platform/research/README.md:17` (the notebook index)
  - `platform/research/BACKTESTING.md` (a pointer to 05)

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/domain/monte_carlo.py`:
  - `MonteCarloResult`, frozen, with the fields `seed`, `n_paths`, `starting_balance`, `period_seconds` (None for trades), `paths`, `terminal`, `max_drawdown`, `sharpe`, `observed_path`, `observed_terminal`, `observed_max_drawdown`, `observed_sharpe`, `note`. Arrays are read-only and shapes are validated.
  - `bootstrap_trades(ledger, n_paths, seed, starting_balance)`.
  - `block_bootstrap_returns(series, block_len, n_paths, seed, starting_balance=1.0)`: Politis–Romano, with geometric block lengths of mean `block_len` and wrap-around.
  - `risk_of_ruin(result, ruin_level)`.
  - `SharpeInterval` (low, high, level, seed, n_paths, n_defined) and `sharpe_confidence_interval(series, n_paths, seed, level, block_len)`: the percentile interval over the defined path Sharpes, None when the observed Sharpe is undefined.
  - `skew_kurtosis(values)`, `daily_returns(ledger, starting_balance)` (the `equity_returns` values), `per_day_sharpe(annualised)`, `probabilistic_sharpe` and `deflated_sharpe`.
  - Split resampling from path evaluation so each function stays under ~30 lines.
- [x] `platform/research/tests/test_monte_carlo.py`: every row of the I/O matrix, plus:
  - `observed_sharpe == MetricReport.from_ledger(...).sharpe_ratio` on a two-day ledger;
  - path max drawdown equals `EquityCurve.underwater().min()` for the observed path;
  - a block-bootstrap path keeps the number of finite points;
  - the per-day Sharpe conversion matches numpy mean/std(ddof=1);
  - PSR against a hand-computed value;
  - DSR < PSR for N > 1.
- [x] `platform/research/application/robustness.py`:
  - `fan_frame(result, quantiles)` (step × quantile) and `mc_title(label, result)` → `"… seed=S paths=N"`;
  - `ruin_frame(result, levels)`;
  - `interval_note(interval, level)`;
  - `DeflatedCheck` (best point, config id, annualised and per-day Sharpe, n_trials, n_obs, skew, kurtosis, psr, dsr) and `deflated_check(sweep, starting_balance)`: best by `sharpe_ratio` among the defined results, `n_trials = len(sweep.results)`, None when no Sharpe is defined;
  - `deflated_verdict(check, confidence)`, a sentence covering both outcomes and the undefined case.
- [x] `platform/research/tests/test_robustness.py` -- the fan quantiles are ordered, the ruin frame's levels and values are correct, the verdict text covers both outcomes and None, and `deflated_check` on a fabricated `Sweep` of real `RunResult`s (or the fixture sweep) picks the max.
- [x] `platform/research/notebooks/05_monte_carlo.py` + `.ipynb` (`make notebooks` / `jupytext --sync`):
  - §1–§9 as in the story's Task 2.
  - Defaults: 04's `STRATEGY`/`PARAMS`/`GRID`/`INSTRUMENT` settings, `N_PATHS=2000`, `SEED=7`, `RETURN_PERIOD_S=3600`, `BLOCK_LEN=24`, `RUIN_LEVELS=[0.9, 0.75, 0.5]`, `CONFIDENCE=0.95`, `SWEEP=True`, `STARTING_BALANCE=10_000`.
  - An empty ledger, an undefined Sharpe or `SWEEP=False` prints a sentence and draws no figure.
- [x] `platform/research/tests/test_notebooks.py` -- add a `NOTEBOOK_ENV["05_monte_carlo.py"]` entry: the data window, `BTC-USD-PERP.HYPERLIQUID`, `_FIXTURE_OFI`, the 2 × 2 grid, `N_PATHS=200`, `RETURN_PERIOD_S=60` and `BLOCK_LEN=3`.
- [x] `platform/research/tests/test_notebook_monte_carlo.py` -- the namespace test:
  - both results have `n_paths == 200` and the recorded seed;
  - every `go.Figure` in the namespace has `seed=` and `paths=` in its title;
  - the ruin frame has 3 rows;
  - the sweep has 4 results;
  - the single run made ≥ 1 trade;
  - no forbidden token appears in a code cell.
- [x] Docs: amend the spine research row and tree (`monte_carlo.py`, `robustness.py`, `05_monte_carlo`, `[amended 2026-09-28: Story 27.6 — …]`, with `MonteCarloResult` no longer marked "arrives in 27.6"), `platform/ARCHITECTURE.md`'s research lists and notebooks paragraph, the `research/README.md` index row, and the `BACKTESTING.md` pointer.

**Acceptance Criteria:**
- Given the fixture, when `05_monte_carlo.py` runs through the harness (`simplefilter("error")`, headless), then it finishes in < 60 s and its `.ipynb` passes the pairing test.
- Given the full platform suite, when it runs, then there are no failures beyond the baseline (the Redis `data_api` tests and the flaky ranking test), and `test_boundaries.py` passes with unchanged allowlists.

## Design Notes

- **Why trade PnLs keep their original exit stamps:** Nautilus bins returns into UTC days. Keeping the stamps and permuting only the PnLs holds the daily-bin structure fixed, so a path's Sharpe is exactly the `MetricReport` Sharpe of an alternative ordering. It is not a statistic on a made-up calendar.
- **The constant-return closed-form case:** constant daily returns have zero variance, so their Sharpe is None. The zero-width interval is therefore shown with 8 h constant returns starting at 16:00 UTC. The first day holds one return and each later day three, so the daily bins differ and the Sharpe is defined, while every resample is identical.
- **Memory and time:** trade paths are `n_paths × (n_trades+1)` float64, about 16 MB at 2000 × 1000. Each path calls `return_stats` once (about 0.4 ms on 720 hourly points, measured). Record the smoke-run timings in the Auto Run Result.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests tests/test_boundaries.py -q` -- expected: all pass
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: only the baseline failures
- `ruff check`, `ruff format --check` and `mypy` on the changed files, plus `uv run jupytext --sync platform/research/notebooks/05_monte_carlo.py` -- expected: clean

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12 (high 0, medium 3, low 9)
- defer: 0
- reject: 7 (high 0, medium 0, low 7)
- addressed_findings:
  - `[medium]` `[patch]` Block-bootstrap paths compounded `1 + r` with no floor, so a return of -100% or worse flipped a later step's sign. `_return_paths` now clips the growth factor at 0: ruin is absorbing and every step is ≥ 0. Test added.
  - `[medium]` `[patch]` Memory and runtime had no stated ceiling: dense `(n_paths, n)` transients and one `return_stats` call per path. Added `Known limit:`s to `block_bootstrap_returns` and `bootstrap_trades`, each with the ceiling, the measured per-path cost and the chunked upgrade path.
  - `[medium]` `[patch]` `deflated_check(sweep, starting_balance)` accepted a balance other than the run's, which would take the PSR/DSR moments from another return series than the `MetricReport` Sharpe. It now raises `ValueError` when the balance differs from `result.equity.starting_balance`. Test added.
  - `[low]` `[patch]` The §8 PSR/DSR bar chart borrowed the trade bootstrap's seed and path count for its title, though PSR/DSR are closed form. It is now a printed table (`deflated_frame`), so every remaining figure's seed/paths title is its own. `deflated_frame` raises on an undefined PSR/DSR. Tests updated.
  - `[low]` `[patch]` The `bootstrap_trades` docstring said the method "permutes only the PnLs", but it samples with replacement. The docstring now says so, and a `Known limit:` notes that additive paths keep trading below zero.
  - `[low]` `[patch]` The §5 markdown cited "a stuck position", an unrealized loss the realized-balance returns cannot see. Reworded to realized losses clustering in time.
  - `[low]` `[patch]` `mc_title` on a `SharpeInterval` now also states `defined=K`, the paths the percentiles were taken over. Test added.
  - `[low]` `[patch]` `skew_kurtosis` could raise `ZeroDivisionError` when `m2 > 0` but `m2**2` underflowed. The guard is now `m2**2 == 0.0`. Test added.
  - `[low]` `[patch]` The notebook test never asserted that §7 and §8 show values on the fixture. It now asserts that the interval, `interval_fig`, the PSR/DSR and the deflated table exist.
  - `[low]` `[patch]` The stationary bootstrap had no behavioural test. A test now checks that the block-break rate is about `1 / block_len`.
  - `[low]` `[patch]` The §8 markdown now says `n_trials` counts only this grid, so the DSR is an upper bound when other configurations were tried.
  - `[low]` `[patch]` `skew_kurtosis` now documents that it uses population (ddof 0) moments.


### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 10 (high 0, medium 1, low 9)
- defer: 0
- reject: 6 (high 0, medium 0, low 6)
- addressed_findings:
  - `[medium]` `[patch]` `interval_note(None, …)` always blamed an undefined observed Sharpe. `sharpe_confidence_interval` also returns None when the observed Sharpe is defined but no resampled path's is, so on that input the sentence was false (reproduced). The sentence now names both reasons, and a test covers it.
  - `[low]` `[patch]` `_check_quantiles` used `if not quantiles`, so `fan_frame` crashed with numpy's "truth value is ambiguous" on a numpy quantile array. It now uses `len(...) == 0`. Test added.
  - `[low]` `[patch]` Levels were formatted with `:.0%`, so 0.995 printed as "100%" in `interval_note` and `deflated_verdict`. A `_percent` helper now prints "99.5%". Test added.
  - `[low]` `[patch]` The `bootstrap_trades` `Known limit:` now says that a path below zero equity can show a max drawdown below -100%, and that its Sharpe covers only the days before equity first reached zero (`equity_returns` skips non-positive balances).
  - `[low]` `[patch]` The module's `Known limit:` and the §9 bullet now also name intra-trade adverse excursion (paths are realized PnL or the unmarked balance), which understates drawdown and ruin for any strategy that rides open losses, not only one still open at `END`.
  - `[low]` `[patch]` §9's "trade vs block bootstrap" bullet attributed a drawdown gap to clustering alone. It now lists the other differences (step granularity, additive vs compounded with a 0 floor, realized PnL vs balance) and presents the gap as a prompt, not a proof.
  - `[low]` `[patch]` `DeflatedCheck.__post_init__` now enforces that `psr` and `dsr` are both set or both None, since they share the variance term. Before, a hand-built check with only `dsr` set crashed `deflated_verdict` with a `TypeError`. Test added, and the test helper was corrected.
  - `[low]` `[patch]` `_check_positive_int`/`_check_seed` rejected numpy integers. They now accept `numbers.Integral` (still not bool). Test added.
  - `[low]` `[patch]` §4 printed the observed Sharpe raw (None or 17 digits), and §8 printed `DeflatedCheck`'s dataclass repr. Both are now formatted sentences.
  - `[low]` `[patch]` `deflated_verdict` now states the number of daily returns it rests on, so a verdict from only 2–3 days reads as fragile. The formula still follows the spec (defined at `n_obs >= 2`). Test added.

## Auto Run Result

Status: done

**Summary:** A follow-up review pass on Story 27.6, the Monte Carlo robustness analysis:
- `research/domain/monte_carlo.py`: bootstraps, ruin, the Sharpe interval, PSR/DSR.
- `research/application/robustness.py`: the frames and verdicts.
- The `05_monte_carlo` notebook.

A fresh Blind Hunter and Edge Case Hunter pass found 16 distinct findings: 10 patched, 6 rejected.

**Files changed (this pass):**
- `platform/research/application/robustness.py`: an honest two-reason no-interval note, `_percent` level formatting, numpy-safe quantile check, the PSR/DSR both-or-none invariant, and `n_obs` in the verdict.
- `platform/research/domain/monte_carlo.py`: accepts numpy integers, and has sharper `Known limit:`s (intra-trade excursion; negative-equity drawdown and Sharpe prefix).
- `platform/research/notebooks/05_monte_carlo.py` / `.ipynb`: formatted §4/§8 prints, and a softened §9 trade-vs-block bullet and closed-trades bullet.
- `platform/research/tests/test_robustness.py`, `test_monte_carlo.py`: tests for each patch.

**Review findings:** 10 patches applied (1 medium, 9 low), 0 deferred, 6 rejected. Rejections:
- A `cumprod` overflow only on returns of about 1e6 (unrealistic, as in the previous pass).
- §7 re-running the block bootstrap: mandated by the story signature (previous pass).
- Selection bias from dropping undefined path Sharpes: `n_defined` is reported and put in the figure title.
- Making the PSR/DSR arguments keyword-only: the spec fixes those signatures.
- §3–§7 analysing `PARAMS` while §8 deflates the grid's best point: by design. The trial-count upper bound is already documented.
- `DeflatedCheck` unhashable because of its `dict` point: never hashed.

**Follow-up review recommended:** false. The fixes are localized wording, validation and docs, each covered by a test. The only behaviour changes are a stricter `DeflatedCheck` invariant and accepting numpy integers.

**Verification:**
- `python3 -m pytest -o addopts="" --rootdir=. research/tests tests/test_boundaries.py -q`: 541 passed, 1 skipped. The first run had 2 failures (a notebook cell's `+` string concatenation, and a test regex), both fixed and re-run green.
- Full platform suite: 2529 passed, 1 skipped, 10 failed. The 10 are exactly the baseline: 9 Redis-dependent `data_api` tests and the flaky `ranking/tests/test_metrics_store.py` test.
- `ruff check` and `ruff format --check` are clean on the changed files, and `mypy` is clean on `monte_carlo.py`/`robustness.py`. `uv run jupytext --sync` re-paired the `.ipynb`.

**Residual risks:**
- The previous pass's risks still stand: DSR σ₀ is the null standard error, the trial count is only this grid's, the analysis covers closed trades only, and memory is O(n_paths × n) with no chunking.
- PSR/DSR remain defined from 2 daily returns, where the moments are degenerate. The verdict now states `n_obs`, but no minimum is enforced (the spec sets `n_obs < 2` as the only floor).
