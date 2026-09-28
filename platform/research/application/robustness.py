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
The robustness service (Story 27.6): the frames, titles and sentences `research/notebooks/
05_monte_carlo` shows, built from `research.domain.monte_carlo` results and a sweep's `RunResult`s
-- nothing here resamples or computes a Sharpe of its own.

Paths, drawdowns, risk of ruin and the Sharpe interval are `monte_carlo`'s; the best grid point's
Sharpe is its `MetricReport`'s (`kernel.performance_metrics`, SSOT-02), deflated by
`monte_carlo.deflated_sharpe`. Frames only reshape those values. Where there is nothing to show (an
undefined Sharpe, no sweep) a function returns the sentence the notebook prints instead of a
figure.
"""

import itertools
import math
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.application.evaluation import Sweep
from research.domain.monte_carlo import MonteCarloResult
from research.domain.monte_carlo import SharpeInterval
from research.domain.monte_carlo import daily_returns
from research.domain.monte_carlo import deflated_sharpe
from research.domain.monte_carlo import expected_max_sharpe
from research.domain.monte_carlo import per_day_sharpe
from research.domain.monte_carlo import probabilistic_sharpe
from research.domain.monte_carlo import risk_of_ruin
from research.domain.monte_carlo import skew_kurtosis


def mc_title(label: str, result: MonteCarloResult | SharpeInterval) -> str:
    """
    Return a figure title that states how the figure was drawn: `"<label>, seed=S paths=N"`, plus
    ` defined=K` for a `SharpeInterval` (the paths whose Sharpe the percentiles were taken over).
    Invariant: every Monte Carlo figure's title ends with its result's own seed and path count, so
    a figure can be reproduced from its title alone.
    """
    title = f"{label}, seed={result.seed} paths={result.n_paths}"
    if isinstance(result, SharpeInterval):
        title += f" defined={result.n_defined}"
    return title


def _percent(fraction: float) -> str:
    """`fraction` as a percentage without rounding a level away: 0.95 -> `95%`, 0.995 -> `99.5%`."""
    return f"{fraction * 100:g}%"


def _check_quantiles(quantiles: Sequence[float]) -> None:
    if len(quantiles) == 0:
        raise ValueError("at least one quantile is needed")
    if any(not (0.0 <= q <= 1.0) for q in quantiles):
        raise ValueError(f"every quantile must be in [0, 1], got {list(quantiles)}")
    if any(b <= a for a, b in itertools.pairwise(quantiles)):
        raise ValueError(f"quantiles must be strictly increasing, got {list(quantiles)}")


def fan_frame(result: MonteCarloResult, quantiles: Sequence[float]) -> pd.DataFrame:
    """
    Return the fan chart's bands: the `quantiles` of the paths' equity at every step (index `step`,
    from 0 = the starting balance; one column per quantile, named by it) plus the `observed` path.

    Invariant: the quantiles are strictly increasing in `[0, 1]` (else `ValueError`), so at every
    step the columns are ordered low to high; one row per path step.
    """
    _check_quantiles(quantiles)
    bands = np.quantile(result.paths, list(quantiles), axis=0).T
    frame = pd.DataFrame(
        bands,
        index=pd.RangeIndex(result.n_steps, name="step"),
        columns=pd.Index(list(quantiles), name="quantile"),
    )
    frame["observed"] = result.observed_path
    return frame


def ruin_frame(result: MonteCarloResult, levels: Sequence[float]) -> pd.DataFrame:
    """
    Return the risk of ruin at each level (index `ruin_level`, in the given order): the equity
    `threshold` (`level * starting_balance`) and `risk_of_ruin`, the fraction of paths ever below
    it. Invariant: one row per level; a level outside `(0, 1]` raises `monte_carlo`'s `ValueError`.
    """
    return pd.DataFrame(
        {
            "threshold": [level * result.starting_balance for level in levels],
            "risk_of_ruin": [risk_of_ruin(result, level) for level in levels],
        },
        index=pd.Index(list(levels), name="ruin_level"),
    )


def interval_note(interval: SharpeInterval | None, level: float) -> str:
    """
    Return the sentence stating the Sharpe confidence interval, or why there is none, so the
    notebook prints one in either case. A None interval names both of
    `sharpe_confidence_interval`'s reasons (the observed Sharpe undefined, or every resampled
    path's), since the None alone does not say which.
    """
    if interval is None:
        return (
            f"No {_percent(level)} Sharpe interval: either the return series' Sharpe ratio is "
            "undefined, or no bootstrapped path's is (Nautilus's statistic needs returns on two "
            "UTC days whose daily sums differ)."
        )
    return (
        f"{_percent(interval.level)} of the {interval.n_defined} defined bootstrapped Sharpe ratios "
        f"(of {interval.n_paths} paths, seed {interval.seed}) lie in "
        f"[{interval.low:.3f}, {interval.high:.3f}] (annualised)."
    )


@dataclass(frozen=True)
class DeflatedCheck:
    """
    The deflated Sharpe of a sweep's best grid point.

    Invariant: `point`/`config_id` name the sweep result with the highest defined `MetricReport`
    Sharpe (ties to the earlier grid point); `sharpe` is that report's annualised value and
    `sharpe_per_day` it de-annualised (`monte_carlo.per_day_sharpe`); `n_trials` is the number of
    grid points tried (at least 1), `n_obs` the best run's daily returns; `skew`/`kurtosis`
    (Pearson) are those returns' moments, and `psr`/`dsr` are None where the moments or the
    formula are undefined (both or neither: they share the variance term). Built by
    `deflated_check`; `__post_init__` raises `ValueError` on a count below its floor or on only
    one of `psr`/`dsr` set.
    """

    point: Mapping[str, object]
    config_id: str
    sharpe: float
    sharpe_per_day: float
    n_trials: int
    n_obs: int
    skew: float | None
    kurtosis: float | None
    benchmark: float | None
    psr: float | None
    dsr: float | None

    def __post_init__(self) -> None:
        if self.n_trials < 1 or self.n_obs < 0:
            raise ValueError(f"n_trials {self.n_trials} < 1 or n_obs {self.n_obs} < 0")
        if (self.psr is None) != (self.dsr is None):
            raise ValueError(f"psr {self.psr} and dsr {self.dsr} must both be set or both None")


def _best(sweep: Sweep) -> tuple[int, float] | None:
    """Return the first grid position with the highest defined Sharpe and that Sharpe, or None."""
    best: tuple[int, float] | None = None
    for position, result in enumerate(sweep.results):
        value = result.metrics.sharpe_ratio
        if value is not None and math.isfinite(value) and (best is None or value > best[1]):
            best = (position, float(value))
    return best


def _deflation(
    per_day: float, n_trials: int, returns: np.ndarray
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """Return `(skew, kurtosis, benchmark, psr, dsr)`, each None where undefined."""
    moments = skew_kurtosis(returns)
    if moments is None or len(returns) < 2:
        return None, None, None, None, None
    n_obs = len(returns)
    return (
        *moments,
        expected_max_sharpe(n_trials, n_obs),
        probabilistic_sharpe(per_day, 0.0, n_obs, *moments),
        deflated_sharpe(per_day, n_trials, *moments, n_obs),
    )


def deflated_check(sweep: Sweep, starting_balance: float) -> DeflatedCheck | None:
    """
    Deflate the sweep's best Sharpe by the number of grid points tried: PSR against 0 and DSR
    (`monte_carlo.deflated_sharpe`) over the best run's daily returns (`monte_carlo.daily_returns`,
    the series its `MetricReport` Sharpe is computed over at `starting_balance`).

    Invariant: `n_trials == len(sweep.results)` -- every point tried counts, defined or not; None
    when no grid point has a defined Sharpe (nothing to deflate). `starting_balance` must be the
    one the best run was made with (its `equity.starting_balance`), else `ValueError`: the moments
    would come from another return series than its `MetricReport` Sharpe.
    """
    best = _best(sweep)
    if best is None:
        return None
    position, sharpe = best
    result = sweep.results[position]
    if result.equity.starting_balance != starting_balance:
        raise ValueError(
            f"starting_balance {starting_balance} is not the run's "
            f"{result.equity.starting_balance}: the PSR/DSR moments would not match its Sharpe"
        )
    per_day = per_day_sharpe(sharpe)
    returns = daily_returns(result.trades, starting_balance)
    skew, kurtosis, benchmark, psr, dsr = _deflation(per_day, len(sweep.results), returns)
    return DeflatedCheck(
        point=dict(sweep.points[position]),
        config_id=result.config_id,
        sharpe=sharpe,
        sharpe_per_day=per_day,
        n_trials=len(sweep.results),
        n_obs=len(returns),
        skew=skew,
        kurtosis=kurtosis,
        benchmark=benchmark,
        psr=psr,
        dsr=dsr,
    )


def deflated_verdict(check: DeflatedCheck | None, confidence: float) -> str:
    """
    Return the plain-language verdict on the best grid point: distinguishable from zero after
    `n_trials` trials when `dsr >= confidence`, not distinguishable otherwise, and a stated reason
    when there is no check (no defined Sharpe) or the DSR is undefined. The verdict states
    `n_obs`, the daily returns it rests on, since a handful of days makes it fragile.
    """
    if check is None:
        return (
            "No grid point has a defined Sharpe ratio (every return statistic needs realized PnL "
            "on two UTC days), so there is no best point to deflate."
        )
    if check.dsr is None:
        return (
            f"The best grid point {dict(check.point)} has Sharpe {check.sharpe:.3f}, but its "
            f"deflated Sharpe is undefined ({check.n_obs} daily returns: the formula needs two, "
            "a non-zero variance and a positive variance term)."
        )
    outcome = "is" if check.dsr >= confidence else "is not"
    return (
        f"The best grid point {dict(check.point)} (Sharpe {check.sharpe:.3f} annualised) {outcome} "
        f"distinguishable from zero after {check.n_trials} trials: deflated Sharpe "
        f"{check.dsr:.3f} vs the {_percent(confidence)} bar (PSR against zero {check.psr:.3f}), "
        f"over {check.n_obs} daily returns."
    )


def deflated_frame(check: DeflatedCheck) -> pd.DataFrame:
    """
    Return `PSR` (against zero) and `DSR` (against the expected best of `n_trials`) as rows.
    Invariant: both probabilities are defined; a check whose PSR or DSR is None raises `ValueError`
    (`deflated_verdict` states why instead).
    """
    if check.psr is None or check.dsr is None:
        raise ValueError("the PSR or DSR is undefined: print deflated_verdict instead")
    return pd.DataFrame(
        {
            "benchmark_per_day": [0.0, check.benchmark],
            "probability": [check.psr, check.dsr],
        },
        index=pd.Index(["PSR vs 0", f"DSR, {check.n_trials} trials"], name="test"),
    )
