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
`research.application.evaluation` (Story 27.5) on hand-built results: the heatmap pivot and its NaN
cell, the top-runs order, every frame's columns, the PnL buckets equal to the ledger's own, and the
sentences printed where there is nothing to draw.
"""

from collections.abc import Mapping
from datetime import UTC
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from kernel.clocks import NS_PER_S
from kernel.performance_metrics import all_metrics

from research.application import evaluation
from research.application.ports import RunResult
from research.application.walk_forward import Fold
from research.application.walk_forward import FoldResult
from research.application.walk_forward import WalkForwardResult
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger


_METRICS = tuple(all_metrics([], [], 1.0))
# 2026-09-01 is a Tuesday (weekday 1).
_DAY = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()) * NS_PER_S
_HOUR = 3_600 * NS_PER_S


def _report(**values: float | None) -> MetricReport:
    return MetricReport(**(dict.fromkeys(MetricReport.field_names()) | values))


def _curve(ts: list[int], values: list[float], balance: float = 100.0) -> EquityCurve:
    return EquityCurve(np.array(ts, dtype=np.int64), np.array(values), balance)


def _trade(entry_ts: int, exit_ts: int, pnl: float) -> ClosedTrade:
    return ClosedTrade("BTC-USD-PERP.DYDX", entry_ts, exit_ts, "LONG", 0.01, pnl, 0.1)


def _result(
    params: Mapping[str, object] | None = None,
    metrics: MetricReport | None = None,
    equity: EquityCurve | None = None,
    trades: TradeLedger | None = None,
    config_id: str = "c",
) -> RunResult:
    ledger = trades or TradeLedger(())
    return RunResult(
        config_id=config_id,
        params=params or {},
        equity=equity or _curve([_DAY], [100.0]),
        trades=ledger,
        metrics=metrics or _report(),
        pnl_by_day=ledger.pnl_by_day(),
        nautilus_stats={},
        iterations=1,
        wall_seconds=0.0,
    )


def _sweep(values: list[float | None], grid: dict | None = None) -> evaluation.Sweep:
    points = evaluation.grid_points(grid or {"a": [1, 2], "b": [3, 4]})
    results = [
        _result(dict(point), _report(sharpe_ratio=value), config_id=f"c{i}")
        for i, (point, value) in enumerate(zip(points, values, strict=True))
    ]
    return evaluation.Sweep(tuple(points), tuple(results), 1.5)


# --- the sweep ----------------------------------------------------------------------------------


def test_the_heatmap_pivots_the_two_keys_with_nan_for_an_undefined_cell() -> None:
    table = evaluation.metric_grid(_sweep([0.1, None, 0.3, 0.4]), "sharpe_ratio")
    assert (table.index.name, table.columns.name) == ("a", "b")
    assert table.index.tolist() == [1, 2]
    assert table.columns.tolist() == [3, 4]
    assert table.loc[1, 3] == 0.1
    assert bool(table.isna().loc[1, 4])
    assert list(table.loc[2]) == [0.3, 0.4]
    assert evaluation.metric_grid_note(table, "sharpe_ratio") is None


def test_a_metric_undefined_everywhere_is_a_sentence_not_a_heatmap() -> None:
    table = evaluation.metric_grid(_sweep([None] * 4), "sharpe_ratio")
    assert table.shape == (2, 2)
    note = evaluation.metric_grid_note(table, "sharpe_ratio")
    assert note is not None
    assert note.startswith("sharpe_ratio is undefined at every one of the 4 grid points")


@pytest.mark.parametrize("grid", [{"a": [1, 2]}, {"a": [1], "b": [2], "c": [3]}])
def test_a_heatmap_needs_exactly_two_keys(grid: dict) -> None:
    sweep = _sweep([0.1] * len(evaluation.grid_points(grid)), grid)
    with pytest.raises(ValueError, match="two-key grid"):
        evaluation.metric_grid(sweep, "sharpe_ratio")


def test_the_heatmap_metric_must_be_a_report_field() -> None:
    with pytest.raises(ValueError, match="not a MetricReport field"):
        evaluation.metric_grid(_sweep([0.1] * 4), "pnl")


def test_top_runs_are_highest_first_undefined_last_ties_in_grid_order() -> None:
    top = evaluation.top_runs(_sweep([0.3, None, 0.5, 0.3]), "sharpe_ratio", 3)
    assert top.index.tolist() == [2, 0, 3]
    everything = evaluation.top_runs(_sweep([0.3, None, 0.5, 0.3]), "sharpe_ratio", 10)
    assert everything.index.tolist() == [2, 0, 3, 1]
    with pytest.raises(ValueError, match="positive int"):
        evaluation.top_runs(_sweep([0.3] * 4), "sharpe_ratio", 0)


def test_the_sweep_frame_holds_the_params_every_metric_and_the_config_id() -> None:
    frame = evaluation.sweep_frame(_sweep([0.1, None, 0.3, 0.4]))
    assert frame.columns.tolist() == ["a", "b", *_METRICS, "config_id"]
    assert frame.index.name == "point"
    assert frame["config_id"].tolist() == ["c0", "c1", "c2", "c3"]
    assert bool(frame["sharpe_ratio"].isna().loc[1])


def test_a_sweep_must_pair_each_result_with_its_point() -> None:
    sweep = _sweep([0.1] * 4)
    with pytest.raises(ValueError, match="did not run grid point"):
        evaluation.Sweep(sweep.points, tuple(reversed(sweep.results)), 1.0)
    with pytest.raises(ValueError, match="3 results for 4 grid points"):
        evaluation.Sweep(sweep.points, sweep.results[:3], 1.0)


# --- one run's frames ---------------------------------------------------------------------------


def test_the_metric_table_is_exactly_all_metrics() -> None:
    table = evaluation.metric_frame(_report(sharpe_ratio=1.2))
    assert tuple(table.index) == _METRICS
    assert table.loc["sharpe_ratio", "value"] == 1.2
    assert table.loc["win_rate", "value"] is None


def test_the_equity_frame_carries_the_underwater_series() -> None:
    curve = _curve([_DAY, _DAY + _HOUR, _DAY + 2 * _HOUR], [110.0, 99.0, 121.0])
    frame = evaluation.equity_frame(curve)
    assert frame.columns.tolist() == ["equity", "underwater"]
    assert frame.index.name == "ts"
    assert frame.index[0] == pd.Timestamp(_DAY, unit="ns", tz="UTC")
    assert frame["underwater"].tolist() == curve.underwater().tolist()


def test_the_drawdown_frame_lists_each_episode() -> None:
    curve = _curve([_DAY, _DAY + _HOUR, _DAY + 2 * _HOUR], [110.0, 99.0, 121.0])
    episodes = evaluation.drawdown_frame(curve)
    assert episodes.columns.tolist() == ["peak", "trough", "recovery", "depth"]
    assert len(episodes) == 1
    row = episodes.iloc[0]
    assert row["peak"] == pd.Timestamp(_DAY, unit="ns", tz="UTC")
    assert row["recovery"] == pd.Timestamp(_DAY + 2 * _HOUR, unit="ns", tz="UTC")
    assert row["depth"] == pytest.approx(0.1)
    assert evaluation.no_drawdown_note(episodes) is None


def test_no_drawdown_is_a_sentence() -> None:
    episodes = evaluation.drawdown_frame(_curve([_DAY, _DAY + _HOUR], [100.0, 105.0]))
    assert len(episodes) == 0
    assert evaluation.no_drawdown_note(episodes) is not None


def test_ten_minutes_of_equity_have_no_rolling_sharpe_and_say_so() -> None:
    curve = _curve([_DAY + 60 * NS_PER_S * k for k in range(10)], [100.0 + k for k in range(10)])
    frame = evaluation.rolling_sharpe_frame(curve, 3_600, 48)
    assert frame.columns.tolist() == ["rolling_sharpe"]
    assert frame.index.name == "ts"
    assert frame["rolling_sharpe"].isna().all()
    note = evaluation.rolling_sharpe_note(frame, 3_600, 48)
    assert note is not None
    assert note.startswith("No rolling Sharpe to draw")


def test_a_rolling_window_under_two_days_is_the_domains_error() -> None:
    curve = _curve([_DAY], [100.0])
    with pytest.raises(ValueError, match="spans under two days"):
        evaluation.rolling_sharpe_frame(curve, 3_600, 47)


def _ledger() -> TradeLedger:
    # Exits: Tue 00:30, Tue 00:45, Tue 05:10, Wed 00:10.
    return TradeLedger.of(
        [
            _trade(_DAY, _DAY + 30 * 60 * NS_PER_S, 1.0),
            _trade(_DAY, _DAY + 45 * 60 * NS_PER_S, -0.25),
            _trade(_DAY + 5 * _HOUR, _DAY + 5 * _HOUR + 600 * NS_PER_S, 2.0),
            _trade(_DAY + 24 * _HOUR, _DAY + 24 * _HOUR + 600 * NS_PER_S, -1.0),
        ]
    )


def test_the_trade_frame_lists_each_trade_with_its_holding_time() -> None:
    ledger = _ledger()
    frame = evaluation.trade_frame(ledger)
    assert frame.columns.tolist() == [
        "instrument_id",
        "side",
        "entry",
        "exit",
        "holding_s",
        "qty",
        "realized_pnl",
        "fees",
    ]
    assert frame["holding_s"].tolist() == ledger.holding_times_s().tolist()
    assert frame["realized_pnl"].tolist() == ledger.realized_pnls()
    assert evaluation.no_trades_note(ledger) is None


def test_a_zero_trade_run_is_a_sentence() -> None:
    assert len(evaluation.trade_frame(TradeLedger(()))) == 0
    assert evaluation.no_trades_note(TradeLedger(())) is not None


def test_pnl_by_hour_and_weekday_are_the_ledgers_own_buckets() -> None:
    result = _result(trades=_ledger())
    assert result.pnl_by_hour_of_day() == result.trades.by_hour_of_day() == {0: -0.25, 5: 2.0}
    assert result.pnl_by_weekday() == result.trades.by_weekday() == {1: 2.75, 2: -1.0}
    by_hour = evaluation.pnl_by_hour_frame(result)
    assert by_hour.index.name == "hour_utc"
    assert dict(zip(by_hour.index, by_hour["pnl"], strict=True)) == {0: -0.25, 5: 2.0}
    by_weekday = evaluation.pnl_by_weekday_frame(result)
    assert by_weekday.index.tolist() == [1, 2]
    assert by_weekday["day"].tolist() == ["Tue", "Wed"]
    assert by_weekday["pnl"].tolist() == [2.75, -1.0]


# --- the walk-forward frames --------------------------------------------------------------------


def _walk_forward(first_pick: float | None) -> WalkForwardResult:
    def fold(k: int, pick: float | None) -> FoldResult:
        base = _DAY + k * 10 * _HOUR
        params = {"a": k}
        return FoldResult(
            Fold(base, base + 7 * _HOUR, base + 7 * _HOUR, base + 10 * _HOUR),
            params,
            _result(params, _report(sharpe_ratio=pick), _curve([base + 1], [100.0])),
            _result(params, _report(win_rate=0.5), _curve([base + 7 * _HOUR], [101.0])),
            "sharpe_ratio",
        )

    return WalkForwardResult.of([fold(0, first_pick), fold(1, 0.2)], "sharpe_ratio", 100.0)


def test_the_fold_frame_shows_each_pick_and_the_oos_metrics() -> None:
    frame = evaluation.fold_frame(_walk_forward(None))
    assert frame.columns.tolist() == [
        "in_start",
        "in_end",
        "oos_start",
        "oos_end",
        "pick",
        "in_sample_sharpe_ratio",
        "in_sample_trades",
        "oos_trades",
        *(f"oos_{name}" for name in _METRICS),
    ]
    assert frame.index.tolist() == [1, 2]
    assert frame["in_sample_sharpe_ratio"].tolist() == [None, 0.2]
    assert frame["pick"].tolist() == [{"a": 0}, {"a": 1}]
    assert frame["oos_win_rate"].tolist() == [0.5, 0.5]


def test_a_pick_no_metric_decided_is_named() -> None:
    notes = evaluation.fold_notes(_walk_forward(None))
    assert len(notes) == 1
    assert notes[0].startswith("Fold 1: sharpe_ratio is undefined")
    assert evaluation.fold_notes(_walk_forward(0.1)) == []


def test_the_walk_forward_metrics_sit_side_by_side() -> None:
    frame = evaluation.walk_forward_metrics_frame(_walk_forward(0.1))
    assert tuple(frame.index) == _METRICS
    assert frame.columns.tolist() == ["in_sample", "out_of_sample"]


def test_the_oos_equity_frame_numbers_each_folds_points() -> None:
    frame = evaluation.oos_equity_frame(_walk_forward(0.1))
    assert frame["fold"].tolist() == [1, 2]
    assert frame["equity"].tolist() == [101.0, 102.0]


@pytest.mark.parametrize(
    ("grid", "metric", "match"),
    [
        ({"a": [1, 2]}, "sharpe_ratio", "two-key grid"),
        ({"a": [1], "b": [2], "c": [3]}, "sharpe_ratio", "two-key grid"),
        ({"a": [1, 1.0], "b": [2]}, "sharpe_ratio", "grid axis 'a'"),
        ({"a": [1], "b": [2]}, "sharpe", "not a MetricReport field"),
    ],
)
def test_a_heatmap_sweep_is_checked_before_any_run(grid: dict, metric: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        evaluation.check_heatmap_sweep(grid, metric)
    evaluation.check_heatmap_sweep({"a": [1], "b": [2]}, "sharpe_ratio")  # a valid pair passes
