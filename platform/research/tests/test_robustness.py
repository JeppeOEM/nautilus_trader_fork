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
`research.application.robustness` (Story 27.6) on hand-built results: the fan quantiles ordered,
the ruin table's levels and values, the titles naming seed and paths, the interval sentence, and
the deflated Sharpe check picking the sweep's best defined Sharpe with a verdict for both outcomes
and for no check at all.
"""

import dataclasses
import math

import numpy as np
import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S

from research.application import evaluation
from research.application import robustness
from research.application.ports import RunResult
from research.domain import monte_carlo as mc
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger


_DAY0 = 20_000 * NS_PER_DAY
_BALANCE = 1_000.0


def _ledger(pnls: list[float]) -> TradeLedger:
    """One trade per UTC day, so every trade is its own daily return."""
    return TradeLedger(
        tuple(
            ClosedTrade(
                "BTC-USD-PERP.DYDX", _DAY0 + i * NS_PER_DAY, _DAY0 + i * NS_PER_DAY + NS_PER_S,
                "LONG", 0.01, pnl, 0.0,
            )
            for i, pnl in enumerate(pnls)
        )
    )  # fmt: skip


def _result(point: dict[str, object], pnls: list[float], config_id: str) -> RunResult:
    ledger = _ledger(pnls)
    return RunResult(
        config_id=config_id,
        params=point,
        equity=EquityCurve(np.array([_DAY0], dtype=np.int64), np.array([_BALANCE]), _BALANCE),
        trades=ledger,
        metrics=MetricReport.from_ledger(ledger, _BALANCE),
        pnl_by_day=ledger.pnl_by_day(),
        nautilus_stats={},
        iterations=1,
        wall_seconds=0.0,
    )


def _sweep(ledgers: list[list[float]]) -> evaluation.Sweep:
    points = evaluation.grid_points({"a": [1, 2], "b": [3, 4]})
    results = [
        _result(dict(p), pnls, f"c{i}")
        for i, (p, pnls) in enumerate(zip(points, ledgers, strict=True))
    ]
    return evaluation.Sweep(tuple(points), tuple(results), 0.5)


def _trade_mc() -> mc.MonteCarloResult:
    return mc.bootstrap_trades(_ledger([5.0, -8.0, 3.0, -2.0, 6.0]), 64, 3, 100.0)


# --- frames and titles ----------------------------------------------------------------------------


def test_the_fan_quantiles_are_ordered_at_every_step() -> None:
    result = _trade_mc()
    fan = robustness.fan_frame(result, [0.05, 0.5, 0.95])
    assert fan.index.name == "step"
    assert len(fan) == result.n_steps
    bands = fan[[0.05, 0.5, 0.95]].to_numpy()
    assert (np.diff(bands, axis=1) >= 0).all()
    assert (fan.loc[0, [0.05, 0.5, 0.95]] == 100.0).all()
    np.testing.assert_array_equal(fan["observed"], result.observed_path)


@pytest.mark.parametrize("quantiles", [[], [0.5, 0.5], [0.9, 0.1], [-0.1, 0.5], [0.5, 1.1]])
def test_bad_fan_quantiles_raise(quantiles: list[float]) -> None:
    with pytest.raises(ValueError, match="quantile"):
        robustness.fan_frame(_trade_mc(), quantiles)


def test_a_numpy_quantile_array_is_accepted_and_an_empty_one_raises() -> None:
    fan = robustness.fan_frame(_trade_mc(), np.array([0.1, 0.9]))
    assert list(fan.columns) == [0.1, 0.9, "observed"]
    with pytest.raises(ValueError, match="at least one quantile"):
        robustness.fan_frame(_trade_mc(), np.array([]))


def test_the_ruin_frame_lists_each_level_with_its_threshold_and_risk() -> None:
    result = mc.bootstrap_trades(_ledger([-20.0]), 10, 0, 100.0)  # every path is [100, 80]
    frame = robustness.ruin_frame(result, [0.9, 0.75, 0.5])
    assert list(frame.index) == [0.9, 0.75, 0.5]
    assert frame.index.name == "ruin_level"
    assert list(frame["threshold"]) == [90.0, 75.0, 50.0]
    assert list(frame["risk_of_ruin"]) == [1.0, 0.0, 0.0]


def test_the_title_states_seed_and_paths() -> None:
    assert robustness.mc_title("fan", _trade_mc()) == "fan, seed=3 paths=64"


def test_an_interval_title_also_states_the_defined_paths() -> None:
    interval = mc.SharpeInterval(-0.5, 1.5, 0.95, seed=3, n_paths=64, n_defined=60)
    assert robustness.mc_title("CI", interval) == "CI, seed=3 paths=64 defined=60"


def test_the_interval_note_states_the_interval_or_why_there_is_none() -> None:
    interval = mc.SharpeInterval(low=-1.0, high=2.5, level=0.9, seed=4, n_paths=50, n_defined=48)
    note = robustness.interval_note(interval, 0.9)
    assert "90% of the 48 defined" in note
    assert "[-1.000, 2.500]" in note
    assert "seed 4" in note
    assert robustness.interval_note(None, 0.95).startswith("No 95% Sharpe interval")


def test_a_missing_interval_names_both_reasons_and_levels_are_not_rounded_away() -> None:
    note = robustness.interval_note(None, 0.995)
    assert note.startswith("No 99.5% Sharpe interval")
    assert "return series' Sharpe ratio is undefined" in note
    assert "no bootstrapped path's is" in note
    interval = mc.SharpeInterval(low=0.0, high=1.0, level=0.995, seed=1, n_paths=9, n_defined=9)
    assert robustness.interval_note(interval, 0.995).startswith("99.5% of the 9 defined")


# --- deflated Sharpe ------------------------------------------------------------------------------


def test_the_check_picks_the_best_defined_sharpe_and_counts_every_trial() -> None:
    sweep = _sweep(
        [
            [1.0, -1.0, 2.0, 0.5],
            [4.0, 3.0, 5.0, 4.5],  # the best
            [1.0],  # one day: Sharpe undefined
            [2.0, -3.0, 1.0, 0.0],
        ]
    )
    sharpes = [r.metrics.sharpe_ratio for r in sweep.results]
    assert sharpes[2] is None
    check = robustness.deflated_check(sweep, _BALANCE)
    assert check is not None
    assert check.config_id == "c1"
    assert check.point == {"a": 1, "b": 4}
    assert check.sharpe == max(s for s in sharpes if s is not None)
    assert check.sharpe_per_day == mc.per_day_sharpe(check.sharpe)
    assert (check.n_trials, check.n_obs) == (4, 4)
    assert check.skew is not None
    assert check.kurtosis is not None
    assert check.dsr == mc.deflated_sharpe(check.sharpe_per_day, 4, check.skew, check.kurtosis, 4)
    assert check.psr is not None
    assert check.dsr is not None
    assert check.dsr < check.psr


def test_ties_go_to_the_earlier_grid_point() -> None:
    same = [1.0, 2.0, 3.0]
    check = robustness.deflated_check(_sweep([same, same, [1.0], [1.0]]), _BALANCE)
    assert check is not None
    assert check.config_id == "c0"


def test_no_defined_sharpe_is_no_check_and_says_so() -> None:
    check = robustness.deflated_check(_sweep([[1.0]] * 4), _BALANCE)
    assert check is None
    assert robustness.deflated_verdict(None, 0.95).startswith("No grid point has a defined Sharpe")


def _check(dsr: float | None) -> robustness.DeflatedCheck:
    return robustness.DeflatedCheck(
        point={"a": 1}, config_id="c", sharpe=3.0, sharpe_per_day=3.0 / math.sqrt(365),
        n_trials=8, n_obs=30, skew=0.1, kurtosis=3.5, benchmark=0.2,
        psr=None if dsr is None else 0.99, dsr=dsr,
    )  # fmt: skip


def test_the_verdict_covers_both_outcomes_and_an_undefined_dsr() -> None:
    passed = robustness.deflated_verdict(_check(0.97), 0.95)
    assert " is distinguishable from zero after 8 trials" in passed
    failed = robustness.deflated_verdict(_check(0.40), 0.95)
    assert "is not distinguishable from zero after 8 trials" in failed
    assert "deflated Sharpe is undefined" in robustness.deflated_verdict(_check(None), 0.95)
    assert "over 30 daily returns" in passed
    assert "the 99.5% bar" in robustness.deflated_verdict(_check(0.97), 0.995)


def test_a_check_with_only_one_of_psr_and_dsr_raises() -> None:
    with pytest.raises(ValueError, match="must both be set or both None"):
        dataclasses.replace(_check(0.5), psr=None)


def test_the_deflated_frame_rows_are_psr_and_dsr() -> None:
    frame = robustness.deflated_frame(_check(0.6))
    assert list(frame["probability"]) == [0.99, 0.6]
    assert list(frame["benchmark_per_day"]) == [0.0, 0.2]


def test_a_balance_other_than_the_runs_raises() -> None:
    sweep = _sweep([[5.0, -1.0, 3.0], [1.0, 2.0, 1.5], [4.0, -3.0, 2.0], [-1.0, 1.0, 0.5]])
    with pytest.raises(ValueError, match="starting_balance"):
        robustness.deflated_check(sweep, _BALANCE * 2)


def test_an_undefined_dsr_has_no_frame() -> None:
    with pytest.raises(ValueError, match="undefined"):
        robustness.deflated_frame(_check(None))
