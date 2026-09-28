# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Monte Carlo robustness of a backtest (Story 27.6): resampled equity paths and the Sharpe ratio's
uncertainty, on numpy and the standard library only (no scipy).

- `bootstrap_trades`: trade-order resampling with replacement of a `TradeLedger`'s realized PnLs.
- `block_bootstrap_returns`: Politis & Romano (1994), "The Stationary Bootstrap", JASA 89(428),
  over a `ReturnSeries`, so the returns' autocorrelation survives the resampling.
- `risk_of_ruin`, `sharpe_confidence_interval` (a percentile interval over block-bootstrapped
  Sharpes).
- `probabilistic_sharpe` and `deflated_sharpe`: Bailey & Lopez de Prado (2014), "The Deflated
  Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality", Journal of
  Portfolio Management 40(5), with `skew_kurtosis` for their moment inputs.

Every stochastic function takes `seed` and draws only from `np.random.default_rng(seed)`, and its
result records `seed` and `n_paths`, so the same call gives the same arrays and a figure can say
how it was made. Every Sharpe is `kernel.performance_metrics.return_stats`' (Nautilus's
`SharpeRatio`, daily-binned, annualised by 365; SSOT-02), never a local mean/std formula; an
undefined one (None) is NaN in a result's `sharpe` array. Nothing here emits a numpy warning: a
zero variance, an empty ledger or a series without a finite point returns a stated None or a
`MonteCarloResult` with a `note`.

Known limit: the Monte Carlo sees closed trades only -- a position still open at the window's end
(`TradeLedger` holds round trips) is not in any path, and neither is any trade's adverse excursion
before it closed (the paths are realized PnL or the unmarked account balance), so drawdown and risk
of ruin understate a strategy that rides open losses; upgrade path: mark the open positions to
market from the account report (a final pseudo-trade for the ones open at the end, a
mark-to-market equity for the block bootstrap).
"""

import math
import numbers
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
from kernel.performance_metrics import equity_returns
from kernel.performance_metrics import return_stats

from research.domain.returns import ReturnSeries
from research.domain.trades import TradeLedger


# Euler-Mascheroni constant: the expected-maximum approximation of Bailey & Lopez de Prado (2014).
EULER_GAMMA = 0.5772156649015329
# `kernel.performance_metrics` annualises its Sharpe with Nautilus's `SharpeRatio(period=365)` over
# daily bins; `test_monte_carlo` pins that `per_day_sharpe` undoes exactly that.
SHARPE_DAYS_PER_YEAR = 365
_NORMAL = NormalDist()


def _frozen(array: np.ndarray) -> np.ndarray:
    """Return a read-only float64 copy, so a frozen result cannot be mutated through its arrays."""
    copy = np.array(array, dtype=np.float64, copy=True)
    copy.flags.writeable = False
    return copy


def _check_positive_int(name: str, value: int) -> None:
    """Raise `ValueError` unless `value` is an integer (not a bool; numpy's too) of at least 1."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 1:
        raise ValueError(f"{name} must be a positive int, got {value!r}")


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, numbers.Integral) or seed < 0:
        raise ValueError(f"seed must be a non-negative int, got {seed!r}")


def _check_balance(starting_balance: float) -> None:
    if not (math.isfinite(starting_balance) and starting_balance > 0):
        raise ValueError(f"starting_balance must be positive and finite, got {starting_balance}")


def _sharpe_or_nan(value: float | None) -> float:
    return math.nan if value is None else float(value)


@dataclass(frozen=True, eq=False)
class MonteCarloResult:
    """
    `n_paths` resampled equity paths, their statistics, and the observed (unresampled) path's.

    Invariant: `paths` has shape `(n_paths, n_steps)` with `n_steps >= 1`, every path's step 0 is
    `starting_balance`; `terminal`, `max_drawdown` and `sharpe` have shape `(n_paths,)` (NaN in
    `sharpe` where the statistic is undefined); `observed_path` has shape `(n_steps,)` and starts at
    `starting_balance`; `seed` and `n_paths` are the ones the paths were drawn with, so the same
    call reproduces them. `period_seconds` is the return series' period (None for a trade
    bootstrap); `note` says why the paths are flat (an empty input), else None. Violated by
    mis-shaped arrays or a path not starting at the balance -- `__post_init__` raises
    `ValueError`. The arrays are stored as read-only copies.
    """

    seed: int
    n_paths: int
    starting_balance: float
    period_seconds: int | None
    paths: np.ndarray
    terminal: np.ndarray
    max_drawdown: np.ndarray
    sharpe: np.ndarray
    observed_path: np.ndarray
    observed_terminal: float
    observed_max_drawdown: float
    observed_sharpe: float | None
    note: str | None = None

    def __post_init__(self) -> None:
        _check_seed(self.seed)
        _check_positive_int("n_paths", self.n_paths)
        _check_balance(self.starting_balance)
        paths = np.asarray(self.paths, dtype=np.float64)
        if paths.ndim != 2 or paths.shape[0] != self.n_paths or paths.shape[1] < 1:
            raise ValueError(
                f"paths must be (n_paths={self.n_paths}, n_steps>=1), got {paths.shape}"
            )
        for name in ("terminal", "max_drawdown", "sharpe"):
            if np.shape(getattr(self, name)) != (self.n_paths,):
                raise ValueError(f"{name} must have shape ({self.n_paths},)")
        if np.shape(self.observed_path) != (paths.shape[1],):
            raise ValueError(f"observed_path must have shape ({paths.shape[1]},)")
        if (paths[:, 0] != self.starting_balance).any() or (
            self.observed_path[0] != self.starting_balance
        ):
            raise ValueError("every path must start at starting_balance (step 0)")
        for name in ("paths", "terminal", "max_drawdown", "sharpe", "observed_path"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    @property
    def n_steps(self) -> int:
        """Steps per path, step 0 (the starting balance) included."""
        return int(self.paths.shape[1])


def path_max_drawdown(paths: np.ndarray) -> np.ndarray:
    """
    Max drawdown of each row of `paths` (or of one 1-D path): `min(equity / running peak - 1)`,
    0 for a path that never falls below its peak, negative otherwise.

    Invariant: step 0 is the starting balance, so the running peak starts there -- the definition
    of `EquityCurve.underwater()`, at the path's step granularity. It is not
    `MetricReport.max_drawdown`, which Nautilus computes over *daily* returns: an intraday dip
    recovered by the day's close shows here and not there. A peak is always positive (at least
    the positive starting balance), so the division never warns.
    """
    peak = np.maximum.accumulate(paths, axis=-1)
    return np.min(paths / peak - 1.0, axis=-1)


def _flat_result(
    seed: int, n_paths: int, starting_balance: float, period_seconds: int | None, note: str
) -> MonteCarloResult:
    """Return the result of an input with nothing to resample: flat paths at the balance."""
    return MonteCarloResult(
        seed=seed,
        n_paths=n_paths,
        starting_balance=starting_balance,
        period_seconds=period_seconds,
        paths=np.full((n_paths, 1), starting_balance),
        terminal=np.full(n_paths, starting_balance),
        max_drawdown=np.zeros(n_paths),
        sharpe=np.full(n_paths, math.nan),
        observed_path=np.array([starting_balance]),
        observed_terminal=starting_balance,
        observed_max_drawdown=0.0,
        observed_sharpe=None,
        note=note,
    )


def _evaluated(
    seed: int,
    starting_balance: float,
    period_seconds: int | None,
    paths: np.ndarray,
    sharpe: np.ndarray,
    observed: tuple[np.ndarray, float | None],
) -> MonteCarloResult:
    """Attach each path's terminal wealth and max drawdown, and the observed path's."""
    observed_path, observed_sharpe = observed
    return MonteCarloResult(
        seed=seed,
        n_paths=int(paths.shape[0]),
        starting_balance=starting_balance,
        period_seconds=period_seconds,
        paths=paths,
        terminal=paths[:, -1],
        max_drawdown=path_max_drawdown(paths),
        sharpe=sharpe,
        observed_path=observed_path,
        observed_terminal=float(observed_path[-1]),
        observed_max_drawdown=float(path_max_drawdown(observed_path)),
        observed_sharpe=observed_sharpe,
    )


# --- trade-order bootstrap ------------------------------------------------------------------------


def _pnl_paths(pnls: np.ndarray, starting_balance: float) -> np.ndarray:
    """`starting_balance + cumsum(pnls)` per row, with step 0 (the balance) prepended."""
    steps = starting_balance + np.cumsum(pnls, axis=-1)
    start = np.full((*pnls.shape[:-1], 1), starting_balance)
    return np.concatenate([start, steps], axis=-1)


def _trade_sharpe(
    pnls: np.ndarray, day_index: np.ndarray, days: list[int], starting_balance: float
) -> float | None:
    """
    Return the Sharpe of `pnls` at the ledger's own exit stamps: summed per UTC exit day in trade
    order (`np.bincount` adds sequentially from 0.0, as `TradeLedger.pnl_by_day` does), then
    `equity_returns` -> `return_stats` -- `MetricReport.from_ledger`'s path exactly.
    """
    by_day = np.bincount(day_index, weights=pnls, minlength=len(days)).tolist()
    pnl_by_day = [{"period_start": d, "pnl": p} for d, p in zip(days, by_day, strict=True)]
    return return_stats(equity_returns(pnl_by_day, starting_balance))["sharpe_ratio"]


def bootstrap_trades(
    ledger: TradeLedger, n_paths: int, seed: int, starting_balance: float
) -> MonteCarloResult:
    """
    Resample the ledger's realized PnLs with replacement, `n_paths` times, into equity paths.

    Invariant: `paths.shape == (n_paths, len(ledger) + 1)` -- every resampled ledger has the
    ledger's trade count, and step 0 is `starting_balance`; each path is `starting_balance +
    cumsum(resampled pnls)`. The PnLs are drawn *with replacement*, so a path is another order
    and another mix of the same trades (a trade may repeat or be absent), not a permutation. A
    path's Sharpe keeps the ledger's *original* exit stamps and replaces only the PnLs, so it is
    the `MetricReport` Sharpe of that resampled ledger on the same calendar, and `observed_sharpe`
    equals `MetricReport.from_ledger(ledger, starting_balance).sharpe_ratio`. An empty ledger
    returns flat paths with a `note`. `n_paths < 1` raises `ValueError`. Source: the i.i.d.
    bootstrap of Efron (1979), "Bootstrap Methods: Another Look at the Jackknife".

    Known limit: a path is additive PnL at a fixed trade size, so one whose equity reaches zero
    keeps trading (and may recover) instead of stopping; `risk_of_ruin` still counts it from its
    first crossing. Such a path's max drawdown can fall below -100% (the equity is negative), and
    its Sharpe covers only the days before its equity first reached zero (`equity_returns` has no
    return from a non-positive balance). Upgrade path: an absorbing floor once position sizing
    scales with equity.
    Memory is `n_paths x (n_trades + 1)` float64 (16 MB at 2000 x 1000) plus one resampled copy,
    and one `return_stats` call per path (about 0.06 ms at 1000 trades).
    """
    _check_positive_int("n_paths", n_paths)
    _check_seed(seed)
    _check_balance(starting_balance)
    if len(ledger) == 0:
        note = "The ledger has no closed trade: there is nothing to resample."
        return _flat_result(seed, n_paths, starting_balance, None, note)
    pnls = np.array(ledger.realized_pnls(), dtype=np.float64)
    days, day_index = np.unique(ledger.exit_day_starts(), return_inverse=True)
    day_list = days.tolist()
    resampled = pnls[np.random.default_rng(seed).integers(0, len(pnls), size=(n_paths, len(pnls)))]
    sharpe = np.array(
        [
            _sharpe_or_nan(_trade_sharpe(row, day_index, day_list, starting_balance))
            for row in resampled
        ]
    )
    observed = (
        _pnl_paths(pnls, starting_balance),
        _trade_sharpe(pnls, day_index, day_list, starting_balance),
    )
    return _evaluated(
        seed, starting_balance, None, _pnl_paths(resampled, starting_balance), sharpe, observed
    )


# --- stationary block bootstrap -------------------------------------------------------------------


def stationary_indices(n: int, block_len: int, n_paths: int, seed: int) -> np.ndarray:
    """
    `(n_paths, n)` indices into a series of length `n`, drawn by the stationary bootstrap of
    Politis & Romano (1994): each block starts at a uniform index, its length is geometric with
    mean `block_len` (a new block starts at each step with probability `1 / block_len`), and a
    block running past the end wraps around to index 0.

    Invariant: every index is in `[0, n)` and each row has exactly `n` entries; `block_len == 1`
    is the i.i.d. bootstrap. `n`, `block_len` or `n_paths` below 1 raises `ValueError`.
    """
    for name, value in (("n", n), ("block_len", block_len), ("n_paths", n_paths)):
        _check_positive_int(name, value)
    _check_seed(seed)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(n_paths, n))
    new_block = rng.random((n_paths, n)) < 1.0 / block_len
    new_block[:, 0] = True
    steps = np.arange(n)
    # The step at which each position's block began, so its offset into the block is the gap.
    block_began = np.maximum.accumulate(np.where(new_block, steps, 0), axis=1)
    rows = np.arange(n_paths)[:, None]
    return (starts[rows, block_began] + (steps - block_began)) % n


def _return_sharpe(ts: list[int], returns: np.ndarray) -> float | None:
    """`return_stats` over `returns` at the series' own stamps (`ReturnSeries.rolling_sharpe`'s)."""
    return return_stats(dict(zip(ts, returns.tolist(), strict=True)))["sharpe_ratio"]


def _return_paths(returns: np.ndarray, starting_balance: float) -> np.ndarray:
    """
    `starting_balance * cumprod(max(1 + r, 0))` per row, with step 0 (the balance) prepended.
    Invariant: every step is `>= 0` -- a return of -100% or worse wipes the account out and it
    stays at 0 (absorbing ruin), never a negative factor flipping a later step's sign.
    """
    steps = starting_balance * np.cumprod(np.maximum(1.0 + returns, 0.0), axis=-1)
    start = np.full((*returns.shape[:-1], 1), starting_balance)
    return np.concatenate([start, steps], axis=-1)


def block_bootstrap_returns(
    series: ReturnSeries,
    block_len: int,
    n_paths: int,
    seed: int,
    starting_balance: float = 1.0,
) -> MonteCarloResult:
    """
    Resample the series' finite returns with the stationary bootstrap (`stationary_indices`,
    Politis & Romano 1994), `n_paths` times, into compounded equity paths.

    Invariant: `paths.shape == (n_paths, n_finite + 1)` -- a NaN gap is dropped (as `as_dict`
    drops it, so the finite points join across it), each path keeps the number of finite points,
    and step 0 is `starting_balance`; each path is `starting_balance * cumprod(1 + r)`, held at 0
    from a return of -100% or worse on (`_return_paths`). The resampled returns sit at the
    series' own finite stamps, so a path's Sharpe is
    `return_stats` of that dict and `observed_sharpe` is `return_stats(series.as_dict())`'s. A
    series with no finite point returns flat paths with a `note`. `block_len` or `n_paths` below
    1 raises `ValueError`.

    Known limit: every path is held in memory, and `stationary_indices` builds a handful of
    transient `(n_paths, n_finite)` arrays, so the peak is roughly ten times `n_paths x n_finite x
    8` bytes (about 1.4 GB for 2000 paths of a year of hourly returns); each path's Sharpe is one
    `return_stats` call (about 0.45 ms at 720 points). Keep `RETURN_PERIOD_S` coarse for long
    windows. Upgrade path: draw and evaluate the paths in chunks, keeping only the fan's quantiles
    and the per-path statistics.
    """
    _check_positive_int("block_len", block_len)
    _check_positive_int("n_paths", n_paths)
    _check_seed(seed)
    _check_balance(starting_balance)
    finite = np.isfinite(series.values)
    returns = series.values[finite]
    if len(returns) == 0:
        note = "The return series has no finite point: there is nothing to resample."
        return _flat_result(seed, n_paths, starting_balance, series.period_seconds, note)
    ts = series.ts_ns[finite].tolist()
    resampled = returns[stationary_indices(len(returns), block_len, n_paths, seed)]
    sharpe = np.array([_sharpe_or_nan(_return_sharpe(ts, row)) for row in resampled])
    observed = (_return_paths(returns, starting_balance), _return_sharpe(ts, returns))
    paths = _return_paths(resampled, starting_balance)
    return _evaluated(seed, starting_balance, series.period_seconds, paths, sharpe, observed)


# --- statistics over the paths --------------------------------------------------------------------


def risk_of_ruin(result: MonteCarloResult, ruin_level: float) -> float:
    """
    Return the fraction of paths whose equity ever falls below `ruin_level * starting_balance` (at any
    step, not only at the end).

    Invariant: a value in `[0, 1]`; a path that never falls (e.g. every PnL positive) is never
    ruined, so a monotone ledger scores 0.0. `ruin_level` must lie in `(0, 1]` (1 = any loss of the
    starting capital), else `ValueError`.
    """
    if not (0.0 < ruin_level <= 1.0):
        raise ValueError(f"ruin_level must be in (0, 1], got {ruin_level}")
    ruined = (result.paths < ruin_level * result.starting_balance).any(axis=1)
    return float(np.count_nonzero(ruined) / result.n_paths)


@dataclass(frozen=True)
class SharpeInterval:
    """
    A percentile confidence interval of the Sharpe ratio from a block bootstrap.

    Invariant: `low <= high`, both finite; `level` in `(0, 1)`; `n_defined` (the path Sharpes the
    percentiles were taken over) is in `[1, n_paths]`; `seed` and `n_paths` are the bootstrap's.
    Violated by constructing it out of order -- `__post_init__` raises `ValueError`.
    """

    low: float
    high: float
    level: float
    seed: int
    n_paths: int
    n_defined: int

    def __post_init__(self) -> None:
        if not (math.isfinite(self.low) and math.isfinite(self.high) and self.low <= self.high):
            raise ValueError(f"need finite low <= high, got [{self.low}, {self.high}]")
        if not (0.0 < self.level < 1.0):
            raise ValueError(f"level must be in (0, 1), got {self.level}")
        if not (1 <= self.n_defined <= self.n_paths):
            raise ValueError(f"n_defined {self.n_defined} outside [1, n_paths={self.n_paths}]")


def sharpe_confidence_interval(
    series: ReturnSeries, n_paths: int, seed: int, level: float, block_len: int
) -> SharpeInterval | None:
    """
    Return the central `level` percentile interval of the Sharpe ratios of
    `block_bootstrap_returns(series, block_len, n_paths, seed)`, over the paths whose Sharpe is
    defined.

    Invariant: None -- a stated "no interval" -- when the observed Sharpe is undefined (under two
    UTC days of returns, or a zero-variance series: Nautilus's statistic is then None) or no path's
    is; otherwise `n_defined` counts the paths used. A series whose resamples are all identical
    (constant returns on a calendar whose daily bins differ) gives `low == high == observed`.
    `level` outside `(0, 1)` raises `ValueError`. Source: the percentile bootstrap interval of
    Efron & Tibshirani (1993), over Politis & Romano's (1994) stationary bootstrap.
    """
    if not (0.0 < level < 1.0):
        raise ValueError(f"level must be in (0, 1), got {level}")
    result = block_bootstrap_returns(series, block_len, n_paths, seed)
    defined = result.sharpe[np.isfinite(result.sharpe)]
    if result.observed_sharpe is None or len(defined) == 0:
        return None
    tail = (1.0 - level) / 2.0
    low, high = np.quantile(defined, [tail, 1.0 - tail])
    return SharpeInterval(
        low=float(low),
        high=float(high),
        level=level,
        seed=seed,
        n_paths=n_paths,
        n_defined=len(defined),
    )


# --- probabilistic and deflated Sharpe ------------------------------------------------------------


def skew_kurtosis(values: np.ndarray | list[float]) -> tuple[float, float] | None:
    """
    `(skewness, kurtosis)` of the finite `values`: population moments `m3 / m2**1.5` and
    `m4 / m2**2`, kurtosis Pearson's (a normal distribution has 3, not 0). Population (biased,
    ddof 0) moments, as Bailey & Lopez de Prado (2014) use; the difference from the bias-corrected
    ones vanishes as the count grows, and matters only for a handful of daily returns.

    Invariant: None -- never a division warning -- when fewer than two finite values or all of
    them equal (zero variance, detected exactly, not by a rounded variance). These are the moment
    inputs of `probabilistic_sharpe` and `deflated_sharpe` (Bailey & Lopez de Prado 2014, eq. for
    the Sharpe estimator's variance under non-normal returns).
    """
    x = np.asarray(values, dtype=np.float64)
    x = x[np.isfinite(x)]
    if len(x) < 2 or np.all(x == x[0]):
        return None
    deviations = x - np.mean(x)
    m2 = float(np.mean(deviations**2))
    if m2**2 == 0.0:  # distinct values whose deviations (or their square) underflow
        return None
    m3 = float(np.mean(deviations**3))
    m4 = float(np.mean(deviations**4))
    return m3 / m2**1.5, m4 / m2**2


def daily_returns(ledger: TradeLedger, starting_balance: float) -> np.ndarray:
    """
    Return the ledger's daily returns in day order: the values of
    `equity_returns(ledger.pnl_by_day(), starting_balance)`, the series `MetricReport`'s Sharpe is
    computed over (one point per UTC day with an exit). Invariant: empty for an empty ledger or a
    non-positive balance, as `equity_returns` is.
    """
    returns = equity_returns(ledger.pnl_by_day(), starting_balance)
    return np.array(list(returns.values()), dtype=np.float64)


def per_day_sharpe(annualised: float) -> float:
    """
    Undo `kernel.performance_metrics`' annualisation: `annualised / sqrt(365)`, the Sharpe per
    daily observation that `probabilistic_sharpe` and `deflated_sharpe` take. Invariant: for a
    `MetricReport.sharpe_ratio` this is `mean / std(ddof=1)` of `daily_returns` (pinned by a
    test), so a Nautilus change of the annualisation fails loudly rather than skewing a PSR.
    """
    return annualised / math.sqrt(SHARPE_DAYS_PER_YEAR)


def probabilistic_sharpe(
    observed: float, benchmark: float, n_obs: int, skew: float, kurtosis: float
) -> float | None:
    """
    Return the probability that the true Sharpe exceeds `benchmark`, given the `observed` one over
    `n_obs` returns with that skewness and Pearson kurtosis: `PSR = Phi((SR - SR*) * sqrt(n - 1) /
    sqrt(1 - skew * SR + (kurtosis - 1) / 4 * SR**2))`, Bailey & Lopez de Prado (2014), eq. 2 (and
    Bailey & Lopez de Prado 2012, "The Sharpe Ratio Efficient Frontier").

    Invariant: both Sharpes are *per observation* (not annualised; see `per_day_sharpe`); a value
    in `[0, 1]`, 0.5 when `observed == benchmark`. None when `n_obs < 2` or the variance term is
    not positive (the formula is undefined there, never a division warning).
    """
    if n_obs < 2:
        return None
    variance = 1.0 - skew * observed + (kurtosis - 1.0) / 4.0 * observed**2
    if not variance > 0.0:
        return None
    z = (observed - benchmark) * math.sqrt(n_obs - 1) / math.sqrt(variance)
    return _NORMAL.cdf(z)


def expected_max_sharpe(n_trials: int, n_obs: int) -> float:
    """
    `SR0 = sigma0 * ((1 - gamma) * PhiInv(1 - 1/N) + gamma * PhiInv(1 - 1/(N e)))`: the expected
    maximum per-observation Sharpe of `N = n_trials` independent zero-Sharpe strategies, Bailey &
    Lopez de Prado (2014), eq. 1 (gamma: Euler-Mascheroni).

    Invariant: 0 for one trial (no selection, so nothing to deflate), increasing in `n_trials`.
    `n_trials < 1` raises `ValueError`; `n_obs < 2` too (sigma0 needs it).
    Known limit: `sigma0 = sqrt(1 / (n_obs - 1))`, the Sharpe estimator's standard error under a
    zero-Sharpe null, stands in for the variance of the Sharpes across the trials; upgrade path:
    pass the empirical variance of the sweep's per-trial Sharpes when every trial's is defined.
    """
    _check_positive_int("n_trials", n_trials)
    if n_obs < 2:
        raise ValueError(f"n_obs must be at least 2, got {n_obs}")
    if n_trials == 1:
        return 0.0
    sigma0 = math.sqrt(1.0 / (n_obs - 1))
    first = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    second = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return sigma0 * ((1.0 - EULER_GAMMA) * first + EULER_GAMMA * second)


def deflated_sharpe(
    observed: float, n_trials: int, skew: float, kurtosis: float, n_obs: int
) -> float | None:
    """
    Return the deflated Sharpe ratio: `probabilistic_sharpe` against `expected_max_sharpe(n_trials,
    n_obs)` instead of 0, the multiple-testing correction for picking the best of `n_trials`
    backtests, Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", eq. 3.

    Invariant: `deflated_sharpe(sr, 1, ...) == probabilistic_sharpe(sr, 0, ...)`, and for more
    trials the DSR is lower (the bar rises); `observed` is per observation. `n_trials < 1` raises
    `ValueError`; None where `probabilistic_sharpe` is (`n_obs < 2`, a non-positive variance
    term).
    """
    _check_positive_int("n_trials", n_trials)
    if n_obs < 2:
        return None
    benchmark = expected_max_sharpe(n_trials, n_obs)
    return probabilistic_sharpe(observed, benchmark, n_obs, skew, kurtosis)
