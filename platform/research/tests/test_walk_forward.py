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
`research.application.walk_forward` (Story 27.5): the fold split, the in-sample pick, the OOS
equity join and the grid expansion on hand-built values, then one real walk-forward -- two folds,
a two-point grid of `OFIStrategy` through `NodeRunner` -- over the session fixture archive.
"""

import math
import re
from dataclasses import replace

import numpy as np
import pytest

from research.application.backtest_runner import NodeRunner
from research.application.evaluation import grid_points
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.application.walk_forward import Fold
from research.application.walk_forward import FoldResult
from research.application.walk_forward import WalkForwardResult
from research.application.walk_forward import concat_equity
from research.application.walk_forward import folds
from research.application.walk_forward import select_best
from research.application.walk_forward import walk_forward
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import TradeLedger
from research.tests.fixture_catalog import DATA_END_NS
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import FixturePaths


def _report(**values: float | None) -> MetricReport:
    return MetricReport(**(dict.fromkeys(MetricReport.field_names()) | values))


def _curve(ts: list[int], values: list[float], balance: float = 100.0) -> EquityCurve:
    return EquityCurve(np.array(ts, dtype=np.int64), np.array(values), balance)


def _result(
    params: dict[str, object] | None = None,
    metrics: MetricReport | None = None,
    equity: EquityCurve | None = None,
) -> RunResult:
    return RunResult(
        config_id="c",
        params=params or {},
        equity=equity or _curve([1], [100.0]),
        trades=TradeLedger(()),
        metrics=metrics or _report(),
        pnl_by_day=[],
        nautilus_stats={},
        iterations=1,
        wall_seconds=0.0,
    )


# --- folds --------------------------------------------------------------------------------------


def test_folds_split_each_equal_segment_at_the_in_sample_fraction() -> None:
    assert folds(0, 1000, 2, 0.7) == [Fold(0, 350, 350, 500), Fold(500, 850, 850, 1000)]


def test_one_fold_is_the_whole_window() -> None:
    assert folds(10, 20, 1, 0.5) == [Fold(10, 15, 15, 20)]


@pytest.mark.parametrize(
    ("n", "fraction"),
    [(0, 0.7), (-1, 0.7), (True, 0.7), (2, 0.0), (2, 1.0), (2, 1.5), (2, None), (2, "0.5")],
)
def test_folds_reject_a_bad_count_or_fraction(n: int, fraction: float) -> None:
    with pytest.raises(ValueError, match="must"):
        folds(0, 1000, n, fraction)


@pytest.mark.parametrize(("start", "end", "n", "fraction"), [(0, 4, 2, 0.1), (0, 1, 2, 0.5)])
def test_folds_reject_an_empty_in_sample_or_oos_part(
    start: int, end: int, n: int, fraction: float
) -> None:
    with pytest.raises(ValueError, match="empty in-sample or out-of-sample part"):
        folds(start, end, n, fraction)


def test_folds_reject_an_empty_window() -> None:
    with pytest.raises(ValueError, match="end after start"):
        folds(5, 5, 1, 0.5)


def test_a_fold_must_be_adjacent_and_non_empty() -> None:
    with pytest.raises(ValueError, match="in_start < in_end == oos_start < oos_end"):
        Fold(0, 350, 351, 500)


# --- selection ----------------------------------------------------------------------------------


def _ranked(values: list[float | None]) -> list[RunResult]:
    return [_result(metrics=_report(sharpe_ratio=v)) for v in values]


def test_the_highest_value_wins_and_a_tie_goes_to_the_earliest() -> None:
    assert select_best(_ranked([None, 0.3, 0.3, 0.1]), "sharpe_ratio") == (1, 0.3)


def test_every_value_undefined_takes_the_first_point_with_no_value() -> None:
    assert select_best(_ranked([None, None]), "sharpe_ratio") == (0, None)


def test_nan_ranks_below_any_number() -> None:
    assert select_best(_ranked([math.nan, -5.0]), "sharpe_ratio") == (1, -5.0)


def test_a_negative_statistic_is_still_higher_is_better() -> None:
    results = [_result(metrics=_report(max_drawdown=v)) for v in (-0.2, -0.05, -0.1)]
    assert select_best(results, "max_drawdown") == (1, -0.05)


def test_select_best_rejects_an_unknown_metric_and_no_results() -> None:
    with pytest.raises(ValueError, match="not a MetricReport field"):
        select_best(_ranked([0.1]), "sharpe")
    with pytest.raises(ValueError, match="at least one result"):
        select_best([], "sharpe_ratio")


# --- the OOS equity join ------------------------------------------------------------------------


def test_each_fold_is_shifted_by_the_prior_folds_pnl() -> None:
    joined = concat_equity([_curve([1, 2], [100, 110]), _curve([3, 4], [100, 95])], 100.0)
    assert joined.values.tolist() == [100.0, 110.0, 110.0, 105.0]
    assert joined.ts_ns.tolist() == [1, 2, 3, 4]
    assert joined.starting_balance == 100.0


def test_overlapping_fold_stamps_raise() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        concat_equity([_curve([1, 2], [100, 110]), _curve([2, 3], [100, 95])], 100.0)


def test_every_fold_must_start_from_the_same_balance() -> None:
    with pytest.raises(ValueError, match=r"every fold must start from 100\.0"):
        concat_equity([_curve([1], [100]), _curve([2], [90], balance=90.0)], 100.0)


def test_concat_equity_needs_a_curve() -> None:
    with pytest.raises(ValueError, match="at least one curve"):
        concat_equity([], 100.0)


# --- the grid -----------------------------------------------------------------------------------


def test_a_grid_expands_in_product_order() -> None:
    assert grid_points({"a": [1, 2], "b": [3, 4]}) == [
        {"a": 1, "b": 3},
        {"a": 1, "b": 4},
        {"a": 2, "b": 3},
        {"a": 2, "b": 4},
    ]


@pytest.mark.parametrize("grid", [{}, {"a": []}, {"a": [1], "b": []}, {"a": "12"}])
def test_a_grid_needs_a_key_and_non_empty_axes(grid: dict) -> None:
    with pytest.raises(ValueError, match="grid"):
        grid_points(grid)


@pytest.mark.parametrize(
    "axis", [[1, 1.0], [1, True], [2.0, 2.0], ["2", 2], [math.nan], [math.inf], [[1, 2]], [None]]
)
def test_a_grid_axis_holds_distinct_finite_scalars(axis: list) -> None:
    """Equal-but-distinct values or one label would share a heatmap cell; NaN never equals itself."""
    with pytest.raises(ValueError, match="grid axis 'a'"):
        grid_points({"a": axis})


# --- the fold record ----------------------------------------------------------------------------


def test_a_fold_result_must_run_the_winners_params_in_its_windows() -> None:
    fold = Fold(0, 10, 10, 20)
    in_sample = _result({"a": 1}, equity=_curve([1, 5], [100, 101]))
    oos = _result({"a": 1}, equity=_curve([10, 15], [100, 99]))
    FoldResult(fold, {"a": 1}, in_sample, oos, "sharpe_ratio")  # consistent: no raise
    with pytest.raises(ValueError, match="did not run the picked point"):
        FoldResult(fold, {"a": 2}, in_sample, oos, "sharpe_ratio")
    with pytest.raises(ValueError, match="not the in-sample winner's"):
        FoldResult(fold, {"a": 1}, in_sample, replace(oos, params={"a": 2}), "sharpe_ratio")
    bad_oos = replace(oos, equity=_curve([9, 15], [100, 99]))
    with pytest.raises(ValueError, match="out-of-sample equity spans"):
        FoldResult(fold, {"a": 1}, in_sample, bad_oos, "sharpe_ratio")
    bad_in = replace(in_sample, equity=_curve([10], [100]))
    with pytest.raises(ValueError, match="in-sample equity spans"):
        FoldResult(fold, {"a": 1}, bad_in, oos, "sharpe_ratio")
    with pytest.raises(ValueError, match="not a MetricReport field"):
        FoldResult(fold, {"a": 1}, in_sample, oos, "sharpe")


def test_the_pick_value_is_the_winners_own_metric() -> None:
    fold = Fold(0, 10, 10, 20)
    oos = _result({"a": 1}, equity=_curve([10], [100]))
    picked = _result({"a": 1}, _report(sharpe_ratio=0.3), _curve([1], [100]))
    assert FoldResult(fold, {"a": 1}, picked, oos, "sharpe_ratio").pick_value == 0.3
    undefined = _result({"a": 1}, _report(sharpe_ratio=math.nan), _curve([1], [100]))
    assert FoldResult(fold, {"a": 1}, undefined, oos, "sharpe_ratio").pick_value is None


def test_a_walk_forward_rejects_overlapping_oos_windows() -> None:
    first = FoldResult(
        Fold(0, 10, 10, 20),
        {},
        _result(equity=_curve([1], [100])),
        _result(equity=_curve([10], [100])),
        "sharpe_ratio",
    )
    second = FoldResult(
        Fold(5, 15, 15, 25),
        {},
        _result(equity=_curve([5], [100])),
        _result(equity=_curve([16], [100])),
        "sharpe_ratio",
    )
    with pytest.raises(ValueError, match="non-overlapping OOS windows"):
        WalkForwardResult.of([first, second], "sharpe_ratio", 100.0)


class _FailingRunner:
    """A `BacktestRunner` whose every call raises `error` and is counted."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def run(self, spec: RunSpec) -> RunResult:
        self.calls += 1
        raise self.error

    def sweep(self, spec: RunSpec, grid: object) -> list[RunResult]:
        self.calls += 1
        raise self.error


_OFFLINE_SPEC = RunSpec(
    catalog_path="/nonexistent",
    instrument_ids=("BTC-USD-PERP.HYPERLIQUID",),
    start=0,
    end=100,
    strategy_path="research.strategies.ofi_strategy:OFIStrategy",
    config_path="research.strategies.ofi_strategy:OFIStrategyConfig",
    params={},
    starting_balance=100,
)


def test_the_fold_order_is_checked_before_any_run() -> None:
    runner = _FailingRunner(AssertionError("no run expected"))
    misordered = [Fold(50, 70, 70, 100), Fold(0, 20, 20, 50)]
    with pytest.raises(ValueError, match="non-overlapping OOS windows"):
        walk_forward(runner, _OFFLINE_SPEC, [{"a": 1}], misordered, "sharpe_ratio")
    assert runner.calls == 0


@pytest.mark.parametrize("error", [RuntimeError("streamed no data"), ValueError("no snapshots")])
def test_a_runner_error_names_its_fold_and_keeps_its_type(error: Exception) -> None:
    fold = Fold(0, 70, 70, 100)
    with pytest.raises(type(error), match=rf"walk-forward {re.escape(str(fold))}: {error}"):
        walk_forward(_FailingRunner(error), _OFFLINE_SPEC, [{"a": 1}], [fold], "sharpe_ratio")


def _fold(k: int, select_by: str = "sharpe_ratio") -> FoldResult:
    base = k * 20
    return FoldResult(
        Fold(base, base + 10, base + 10, base + 20),
        {},
        _result(equity=_curve([base], [100])),
        _result(equity=_curve([base + 10], [101])),
        select_by,
    )


def test_a_walk_forward_result_holds_its_invariant_however_it_is_built() -> None:
    built = WalkForwardResult.of([_fold(0), _fold(1)], "sharpe_ratio", 100.0)
    assert built.oos_equity.values.tolist() == [101.0, 102.0]
    with pytest.raises(ValueError, match="oos_equity is not the joined"):
        replace(built, oos_equity=_curve([10, 30], [101, 101]))
    with pytest.raises(ValueError, match="oos_metrics are not"):
        replace(built, oos_metrics=_report(win_rate=1.0))
    with pytest.raises(ValueError, match="in_sample_metrics are not"):
        replace(built, in_sample_metrics=_report(win_rate=1.0))
    with pytest.raises(ValueError, match="every fold must be picked on 'sharpe_ratio'"):
        WalkForwardResult.of([_fold(0), _fold(1, "expectancy")], "sharpe_ratio", 100.0)


# --- one real walk-forward over the fixture -----------------------------------------------------

_S = "research.strategies.ofi_strategy:"
# The OFI parameters that trade on the fixture's ten minutes (`test_backtest_runner.py`'s).
_PARAMS = {
    "warmup_seconds": 0,
    "ofi_window": 2,
    "ofi_zscore_window": 5,
    "ofi_threshold": 0.5,
    "trade_size": "0.01",
    "trend_ema_fast": 2,
    "trend_ema_slow": 3,
}
_GRID = [{"ofi_window": 2}, {"ofi_window": 3}]
# Every return statistic needs realized PnL on two UTC days, which a 3.5-minute in-sample window
# never has: expectancy is defined on any closed trade.
_SELECT_BY = "expectancy"
_BALANCE = 10_000


@pytest.fixture(scope="module")
def spec(fixture_archive: FixturePaths) -> RunSpec:
    """Return the fixture's data window on a venue-timed instrument clear of the dYdX BTC outage."""
    return RunSpec(
        catalog_path=fixture_archive.catalog_path,
        instrument_ids=("BTC-USD-PERP.HYPERLIQUID",),
        start=DATA_START_NS,
        end=DATA_END_NS,
        strategy_path=_S + "OFIStrategy",
        config_path=_S + "OFIStrategyConfig",
        params=_PARAMS,
        starting_balance=_BALANCE,
    )


@pytest.fixture(scope="module")
def walked(spec: RunSpec) -> WalkForwardResult:
    fixture_folds = folds(DATA_START_NS, DATA_END_NS, 2, 0.7)
    return walk_forward(NodeRunner(), spec, _GRID, fixture_folds, _SELECT_BY)


def test_the_fixture_walk_forward_has_two_trading_folds(walked: WalkForwardResult) -> None:
    assert [fr.fold for fr in walked.folds] == folds(DATA_START_NS, DATA_END_NS, 2, 0.7)
    for fr in walked.folds:
        assert len(fr.in_sample.trades) > 0
        assert len(fr.oos.trades) > 0


def test_each_fold_picks_the_in_sample_argmax(spec: RunSpec, walked: WalkForwardResult) -> None:
    """The in-sample sweep re-run on its own: the pick is its argmax, and not by a tie."""
    for fr in walked.folds:
        again = NodeRunner().sweep(replace(spec, start=fr.fold.in_start, end=fr.fold.in_end), _GRID)
        values = [r.metrics.expectancy for r in again if r.metrics.expectancy is not None]
        assert len(values) == len(_GRID)
        assert len(set(values)) == len(values), "a tie would pick by grid order, not by metric"
        best = values.index(max(values))
        assert fr.point == _GRID[best]
        assert fr.pick_value == values[best]
        assert fr.in_sample.params == {**_PARAMS, **_GRID[best]}
        assert fr.oos.params == fr.in_sample.params


def test_the_oos_equity_lies_in_the_oos_windows(walked: WalkForwardResult) -> None:
    joined = walked.oos_equity.ts_ns.tolist()
    per_fold = [fr.oos.equity.ts_ns.tolist() for fr in walked.folds]
    assert joined == [ts for stamps in per_fold for ts in stamps]
    for fr, stamps in zip(walked.folds, per_fold, strict=True):
        assert all(fr.fold.oos_start <= ts < fr.fold.oos_end for ts in stamps)


def test_the_second_fold_is_shifted_by_the_first_folds_pnl(walked: WalkForwardResult) -> None:
    first, second = (fr.oos.equity for fr in walked.folds)
    shift = float(first.values[-1]) - _BALANCE
    assert walked.oos_equity.values[len(first) :].tolist() == (second.values + shift).tolist()


def test_the_metrics_are_over_every_folds_trades_merged(walked: WalkForwardResult) -> None:
    oos = TradeLedger.of(t for fr in walked.folds for t in fr.oos.trades.trades)
    in_sample = TradeLedger.of(t for fr in walked.folds for t in fr.in_sample.trades.trades)
    assert walked.oos_metrics == MetricReport.from_ledger(oos, float(_BALANCE))
    assert walked.in_sample_metrics == MetricReport.from_ledger(in_sample, float(_BALANCE))
