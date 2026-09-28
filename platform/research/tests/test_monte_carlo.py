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
`research.domain.monte_carlo` (Story 27.6) on closed-form cases: a one-trade ledger's identical
paths, a constant-return series' zero-width interval, `deflated_sharpe` with one trial equal to
`probabilistic_sharpe`, reproducibility, the degenerate inputs' stated None or note, and the
Sharpe/drawdown agreeing with `MetricReport` and `EquityCurve`.
"""

import math
import warnings
from collections.abc import Iterator

import numpy as np
import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.performance_metrics import equity_returns
from kernel.performance_metrics import return_stats

from research.domain import monte_carlo as mc
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.returns import ReturnSeries
from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger


_DAY0 = 20_000 * NS_PER_DAY  # 2024-10-04 00:00 UTC
_HOUR = 3_600 * NS_PER_S


@pytest.fixture(autouse=True)
def _warnings_are_errors() -> Iterator[None]:
    """TEST-04: a numpy division or invalid-value warning fails the test."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        yield


def _ledger(pnls: list[float], spacing_ns: int = _HOUR) -> TradeLedger:
    return TradeLedger(
        tuple(
            ClosedTrade("BTC-USD-PERP.DYDX", _DAY0 + i * spacing_ns, _DAY0 + i * spacing_ns + 1,
                        "LONG", 0.01, pnl, 0.0)
            for i, pnl in enumerate(pnls)
        )
    )  # fmt: skip


def _two_day_ledger() -> TradeLedger:
    """Seven trades 7 h apart: exits on 2024-10-04 and 2024-10-05, so a daily Sharpe exists."""
    return _ledger([3.0, -1.5, 2.0, -4.0, 1.0, 5.5, -2.0], spacing_ns=7 * _HOUR)


def _series(values: list[float], period_s: int, first_ns: int = _DAY0) -> ReturnSeries:
    ts = first_ns + np.arange(len(values), dtype=np.int64) * period_s * NS_PER_S
    return ReturnSeries(np.array(values, dtype=np.float64), ts, period_s)


def _noisy_hourly(n: int = 96) -> ReturnSeries:
    return _series(list(np.random.default_rng(11).normal(0.0005, 0.01, n)), 3_600)


# --- trade bootstrap ------------------------------------------------------------------------------


def test_one_trade_gives_identical_paths() -> None:
    result = mc.bootstrap_trades(_ledger([5.0]), n_paths=50, seed=3, starting_balance=100.0)
    assert result.paths.shape == (50, 2)
    assert (result.paths == np.array([100.0, 105.0])).all()
    assert list(result.observed_path) == [100.0, 105.0]


def test_the_same_seed_reproduces_every_array() -> None:
    first = mc.bootstrap_trades(_two_day_ledger(), 40, 9, 1_000.0)
    second = mc.bootstrap_trades(_two_day_ledger(), 40, 9, 1_000.0)
    for name in ("paths", "terminal", "max_drawdown", "sharpe"):
        np.testing.assert_array_equal(getattr(first, name), getattr(second, name))
    assert (first.seed, first.n_paths) == (9, 40)


def test_another_seed_draws_other_paths() -> None:
    first = mc.bootstrap_trades(_two_day_ledger(), 40, 9, 1_000.0)
    other = mc.bootstrap_trades(_two_day_ledger(), 40, 10, 1_000.0)
    assert not np.array_equal(first.paths, other.paths)


def test_every_resampled_ledger_keeps_the_trade_count_and_starts_at_the_balance() -> None:
    ledger = _two_day_ledger()
    result = mc.bootstrap_trades(ledger, 25, 1, 1_000.0)
    assert result.paths.shape == (25, len(ledger) + 1)
    assert (result.paths[:, 0] == 1_000.0).all()
    np.testing.assert_array_equal(result.terminal, result.paths[:, -1])


def test_the_paths_draw_only_the_ledgers_pnls() -> None:
    ledger = _two_day_ledger()
    result = mc.bootstrap_trades(ledger, 30, 2, 1_000.0)
    steps = np.diff(result.paths, axis=1)
    assert set(np.round(steps, 9).ravel()) <= set(ledger.realized_pnls())


def test_the_observed_sharpe_is_the_metric_reports() -> None:
    ledger = _two_day_ledger()
    result = mc.bootstrap_trades(ledger, 10, 0, 1_000.0)
    expected = MetricReport.from_ledger(ledger, 1_000.0).sharpe_ratio
    assert expected is not None
    assert result.observed_sharpe == expected


def test_a_one_day_ledger_has_no_sharpe_on_any_path() -> None:
    result = mc.bootstrap_trades(_ledger([1.0, -2.0, 3.0]), 10, 0, 100.0)
    assert result.observed_sharpe is None
    assert np.isnan(result.sharpe).all()


def test_the_path_drawdown_is_the_equity_curves_underwater_minimum() -> None:
    ledger = _two_day_ledger()
    result = mc.bootstrap_trades(ledger, 5, 0, 1_000.0)
    curve = EquityCurve(
        np.array([t.exit_ts for t in ledger.trades], dtype=np.int64),
        result.observed_path[1:],
        1_000.0,
    )
    assert result.observed_max_drawdown == float(curve.underwater().min())
    for row, drawdown in zip(result.paths, result.max_drawdown, strict=True):
        assert drawdown == mc.path_max_drawdown(row)


def test_an_empty_ledger_is_flat_paths_with_a_note() -> None:
    result = mc.bootstrap_trades(TradeLedger(()), 20, 4, 100.0)
    assert result.paths.shape == (20, 1)
    assert (result.paths == 100.0).all()
    assert (result.terminal == 100.0).all()
    assert (result.max_drawdown == 0.0).all()
    assert np.isnan(result.sharpe).all()
    assert result.observed_sharpe is None
    assert result.note is not None
    assert "no closed trade" in result.note


def test_numpy_integer_sizes_and_seeds_are_accepted() -> None:
    result = mc.bootstrap_trades(_ledger([1.0, -2.0]), np.int64(6), np.int64(2), 100.0)
    assert result.paths.shape == (6, 3)
    assert result.seed == 2


@pytest.mark.parametrize("n_paths", [0, -1, True, 2.0])
def test_a_bad_path_count_raises(n_paths: object) -> None:
    with pytest.raises(ValueError, match="n_paths"):
        mc.bootstrap_trades(_ledger([1.0]), n_paths, 0, 100.0)


def test_the_result_arrays_are_read_only() -> None:
    result = mc.bootstrap_trades(_ledger([1.0, 2.0]), 3, 0, 100.0)
    with pytest.raises(ValueError, match="read-only"):
        result.paths[0, 0] = 1.0


def test_a_mis_shaped_result_raises() -> None:
    result = mc.bootstrap_trades(_ledger([1.0, 2.0]), 3, 0, 100.0)
    with pytest.raises(ValueError, match="shape"):
        mc.MonteCarloResult(
            seed=0, n_paths=3, starting_balance=100.0, period_seconds=None,
            paths=result.paths, terminal=result.terminal[:2], max_drawdown=result.max_drawdown,
            sharpe=result.sharpe, observed_path=result.observed_path, observed_terminal=103.0,
            observed_max_drawdown=0.0, observed_sharpe=None,
        )  # fmt: skip


# --- risk of ruin ---------------------------------------------------------------------------------


def test_a_monotone_ledger_is_never_ruined() -> None:
    result = mc.bootstrap_trades(_ledger([1.0, 2.0, 0.5, 3.0]), 100, 5, 100.0)
    assert mc.risk_of_ruin(result, 0.9) == 0.0
    assert mc.risk_of_ruin(result, 1.0) == 0.0


def test_ruin_counts_a_path_that_ever_dips_below_the_floor() -> None:
    # One trade of -20: every path is [100, 80], below 90 and above 75.
    result = mc.bootstrap_trades(_ledger([-20.0]), 10, 0, 100.0)
    assert mc.risk_of_ruin(result, 0.9) == 1.0
    assert mc.risk_of_ruin(result, 0.75) == 0.0


@pytest.mark.parametrize("level", [0.0, -0.1, 1.5, math.nan])
def test_a_ruin_level_outside_zero_one_raises(level: float) -> None:
    result = mc.bootstrap_trades(_ledger([1.0]), 2, 0, 100.0)
    with pytest.raises(ValueError, match="ruin_level"):
        mc.risk_of_ruin(result, level)


# --- block bootstrap ------------------------------------------------------------------------------


def test_the_stationary_indices_stay_in_range_and_wrap() -> None:
    indices = mc.stationary_indices(10, 4, 300, 1)
    assert indices.shape == (300, 10)
    assert indices.min() >= 0
    assert indices.max() <= 9
    steps = np.diff(indices, axis=1)
    assert ((steps == 1) | (steps == -9)).any(), "a block continues, wrapping at the end"
    assert np.array_equal(indices, mc.stationary_indices(10, 4, 300, 1))


def test_a_block_length_of_one_starts_a_new_block_at_every_step() -> None:
    rng = np.random.default_rng(1)
    expected = rng.integers(0, 10, size=(5, 10))
    np.testing.assert_array_equal(mc.stationary_indices(10, 1, 5, 1), expected)


def test_a_block_path_keeps_the_number_of_finite_points() -> None:
    series = _series([0.01, math.nan, -0.02, 0.005, math.nan, 0.03], 3_600)
    result = mc.block_bootstrap_returns(series, 2, 30, 8, 100.0)
    assert result.paths.shape == (30, 4 + 1)
    assert result.period_seconds == 3_600
    assert (result.paths[:, 0] == 100.0).all()


def test_the_block_observed_path_compounds_the_finite_returns() -> None:
    series = _series([0.1, math.nan, -0.5], 3_600)
    result = mc.block_bootstrap_returns(series, 1, 3, 0, 100.0)
    np.testing.assert_allclose(result.observed_path, [100.0, 110.0, 55.0])
    assert result.observed_max_drawdown == pytest.approx(55.0 / 110.0 - 1.0)


def test_the_block_observed_sharpe_is_return_stats_of_the_series() -> None:
    series = _noisy_hourly()
    result = mc.block_bootstrap_returns(series, 6, 20, 3)
    assert result.observed_sharpe == return_stats(series.as_dict())["sharpe_ratio"]
    assert np.isfinite(result.sharpe).all()


def test_a_series_without_a_finite_point_is_flat_paths_with_a_note() -> None:
    result = mc.block_bootstrap_returns(_series([math.nan, math.nan], 60), 2, 7, 0, 50.0)
    assert result.paths.shape == (7, 1)
    assert (result.paths == 50.0).all()
    assert np.isnan(result.sharpe).all()
    assert result.note is not None
    assert "no finite point" in result.note


@pytest.mark.parametrize("block_len", [0, -3, True])
def test_a_bad_block_length_raises(block_len: object) -> None:
    with pytest.raises(ValueError, match="block_len"):
        mc.block_bootstrap_returns(_noisy_hourly(), block_len, 5, 0)


# --- Sharpe interval ------------------------------------------------------------------------------


def test_constant_eight_hour_returns_give_a_zero_width_interval_at_the_observed_sharpe() -> None:
    # From 16:00 UTC: one return on the first day, three on each later one, so the daily bins
    # differ (the Sharpe is defined) while every resample of identical values is identical.
    series = _series([0.01] * 12, 8 * 3_600, first_ns=_DAY0 + 16 * _HOUR)
    observed = return_stats(series.as_dict())["sharpe_ratio"]
    assert observed is not None
    interval = mc.sharpe_confidence_interval(series, 40, 2, 0.9, 3)
    assert interval is not None
    assert interval.low == interval.high == observed
    assert (interval.n_defined, interval.n_paths, interval.seed, interval.level) == (40, 40, 2, 0.9)


def test_zero_variance_daily_returns_have_no_interval_and_no_moments() -> None:
    series = _series([0.01] * 6, 86_400)
    assert mc.sharpe_confidence_interval(series, 20, 0, 0.95, 2) is None
    assert mc.skew_kurtosis(series.values) is None


def test_a_noisy_series_interval_brackets_its_defined_sharpes() -> None:
    series = _noisy_hourly()
    interval = mc.sharpe_confidence_interval(series, 60, 4, 0.8, 6)
    assert interval is not None
    assert interval.low < interval.high
    assert interval.n_defined == 60


@pytest.mark.parametrize("level", [0.0, 1.0, 1.2])
def test_a_confidence_level_outside_zero_one_raises(level: float) -> None:
    with pytest.raises(ValueError, match="level"):
        mc.sharpe_confidence_interval(_noisy_hourly(), 5, 0, level, 2)


# --- moments, PSR and DSR -------------------------------------------------------------------------


def test_skew_kurtosis_closed_form() -> None:
    symmetric = mc.skew_kurtosis([1.0, 2.0, 3.0, 4.0])
    assert symmetric is not None
    assert symmetric[0] == pytest.approx(0.0, abs=1e-15)
    assert symmetric[1] == pytest.approx(1.64)  # m4 / m2**2 = 2.5625 / 1.5625
    skewed = mc.skew_kurtosis([0.0, 0.0, 0.0, 1.0])
    assert skewed is not None
    assert skewed[0] == pytest.approx(2 / math.sqrt(3))


def test_skew_kurtosis_needs_two_finite_values() -> None:
    assert mc.skew_kurtosis([1.0, math.nan]) is None
    assert mc.skew_kurtosis([]) is None


def test_the_per_day_sharpe_undoes_the_metric_reports_annualisation() -> None:
    ledger = _ledger([3.0, -1.0, 5.0, -2.0, 4.0], spacing_ns=NS_PER_DAY)
    annualised = MetricReport.from_ledger(ledger, 1_000.0).sharpe_ratio
    assert annualised is not None
    daily = np.array(list(equity_returns(ledger.pnl_by_day(), 1_000.0).values()))
    np.testing.assert_array_equal(mc.daily_returns(ledger, 1_000.0), daily)
    expected = np.mean(daily) / np.std(daily, ddof=1)
    assert mc.per_day_sharpe(annualised) == pytest.approx(expected, rel=1e-12)


def test_psr_matches_a_hand_computed_value() -> None:
    # variance term 1 + 0.5 * 0.2 + 0.75 * 0.04 = 1.13; z = 0.2 * 7 / sqrt(1.13) = 1.31702
    z = 0.2 * 7 / math.sqrt(1.13)
    expected = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    psr = mc.probabilistic_sharpe(0.2, 0.0, 50, -0.5, 4.0)
    assert psr == pytest.approx(expected, rel=1e-12)
    assert psr == pytest.approx(0.90612, abs=1e-4)


def test_psr_at_the_benchmark_is_one_half() -> None:
    assert mc.probabilistic_sharpe(0.3, 0.3, 20, 0.4, 5.0) == 0.5


def test_psr_is_undefined_below_two_observations_or_a_non_positive_variance_term() -> None:
    assert mc.probabilistic_sharpe(0.1, 0.0, 1, 0.0, 3.0) is None
    # 1 - 2 * 1 + (3 - 1) / 4 * 1 = -0.5
    assert mc.probabilistic_sharpe(1.0, 0.0, 30, 2.0, 3.0) is None


@pytest.mark.parametrize(
    ("sr", "skew", "kurtosis", "n_obs"), [(0.1, 0.0, 3.0, 30), (-0.2, 1.0, 6.0, 9)]
)
def test_dsr_with_one_trial_is_psr_against_zero(
    sr: float, skew: float, kurtosis: float, n_obs: int
) -> None:
    assert mc.deflated_sharpe(sr, 1, skew, kurtosis, n_obs) == mc.probabilistic_sharpe(
        sr, 0.0, n_obs, skew, kurtosis
    )


def test_dsr_falls_below_psr_as_trials_grow() -> None:
    psr = mc.probabilistic_sharpe(0.15, 0.0, 100, -0.3, 4.0)
    dsr_10 = mc.deflated_sharpe(0.15, 10, -0.3, 4.0, 100)
    dsr_100 = mc.deflated_sharpe(0.15, 100, -0.3, 4.0, 100)
    assert psr is not None
    assert dsr_10 is not None
    assert dsr_100 is not None
    assert dsr_100 < dsr_10 < psr


def test_the_expected_max_sharpe_closed_form() -> None:
    # sigma0 = sqrt(1/100) = 0.1; PhiInv(0.9) = 1.28155, PhiInv(1 - 1/(10e)) = 1.78940
    assert mc.expected_max_sharpe(10, 101) == pytest.approx(0.1574, abs=1e-3)
    assert mc.expected_max_sharpe(1, 101) == 0.0


def test_dsr_needs_a_trial_and_two_observations() -> None:
    with pytest.raises(ValueError, match="n_trials"):
        mc.deflated_sharpe(0.1, 0, 0.0, 3.0, 30)
    assert mc.deflated_sharpe(0.1, 5, 0.0, 3.0, 1) is None


# --- review hardening -----------------------------------------------------------------------------


def test_the_stationary_blocks_have_the_mean_length() -> None:
    """A block breaks where an index does not follow its predecessor: about one in `block_len`."""
    n, block_len = 5_000, 8
    indices = mc.stationary_indices(n, block_len, 20, 5)
    follows = indices[:, 1:] == (indices[:, :-1] + 1) % n
    breaks = np.count_nonzero(~follows) / follows.size
    # A new block may land on the next index by chance (1/n), so the rate is a hair under 1/8.
    assert breaks == pytest.approx(1.0 / block_len, rel=0.05)


def test_a_wipe_out_return_holds_the_path_at_zero() -> None:
    result = mc.block_bootstrap_returns(_series([0.1, -1.5, 0.2], 3_600), 1, 50, 2, 100.0)
    assert (result.paths >= 0.0).all()
    np.testing.assert_allclose(result.observed_path, [100.0, 110.0, 0.0, 0.0])
    assert result.observed_max_drawdown == -1.0


def test_skew_kurtosis_of_underflowing_deviations_is_none() -> None:
    assert mc.skew_kurtosis([0.0, 1e-160, 2e-160]) is None
