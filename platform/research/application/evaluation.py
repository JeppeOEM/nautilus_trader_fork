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
The backtest evaluation service (Story 27.5): every table and series `research/notebooks/
04_backtest_evaluation` shows, built from a `RunResult`, a sweep's results or a
`WalkForwardResult` and the `research.domain` values -- nothing here computes a statistic.

Metrics are `MetricReport`'s (`kernel.performance_metrics.all_metrics`, SSOT-02), the underwater
series and drawdown episodes `EquityCurve`'s, the rolling Sharpe `ReturnSeries.rolling_sharpe`'s,
holding times and PnL buckets `TradeLedger`'s. Frames only reshape those values; an undefined
metric stays NaN (or None where a table must show it), never 0. Where there is nothing to draw (no
trade, a rolling window longer than the curve, a metric undefined across the grid) a `*_note`
function returns the sentence the notebook prints instead of an empty figure.
"""

import itertools
import math
import time
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from research.application.ports import BacktestRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.application.walk_forward import WalkForwardResult
from research.application.walk_forward import check_metric
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.returns import ReturnSeries
from research.domain.trades import TradeLedger


def _utc(ts_ns: Iterable[int | None]) -> pd.DatetimeIndex:
    """Return UTC timestamps for int ns, NaT for None."""
    return pd.DatetimeIndex(
        [pd.NaT if ns is None else pd.Timestamp(ns, unit="ns", tz="UTC") for ns in ts_ns],
        dtype="datetime64[ns, UTC]",
    )


def _float(value: float | None) -> float:
    """Return a metric value for a numeric column: None (undefined) is NaN, never 0."""
    return math.nan if value is None else float(value)


# English weekday labels, fixed: `calendar.day_abbr` follows the process locale.
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _check_axis(key: str, axis: object) -> None:
    """
    Raise unless `axis` is a non-empty list of distinct scalar values: a heatmap axis label and a
    `==` comparison must identify each value, so `1`, `1.0` and `True` on one axis (equal, but
    distinct runs), `"2"` and `2` (distinct runs, one label) and NaN (never equal to itself) are
    refused.
    """
    if isinstance(axis, str | bytes) or not isinstance(axis, Sequence) or not axis:
        raise ValueError(f"grid axis {key!r} must be a non-empty list of values, got {axis!r}")
    for value in axis:
        if not isinstance(value, str | int | float) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError(f"grid axis {key!r}: {value!r} is not a finite str/int/float/bool")
    for i, value in enumerate(axis):
        if any(value == other for other in axis[i + 1 :]):
            raise ValueError(f"grid axis {key!r} lists {value!r} twice (as equal values)")
    if len({str(value) for value in axis}) != len(axis):
        raise ValueError(f"grid axis {key!r}: two values share one label in {list(axis)!r}")


def grid_points(grid: Mapping[str, Sequence[object]]) -> list[dict[str, object]]:
    """
    Expand `{param: [values]}` into one `{param: value}` point per combination, in
    `itertools.product` order over the keys' order (the last key varies fastest).

    Invariant: at least one key and every axis a non-empty list of distinct, finite scalars (a
    `str` is one value, not an axis; see `_check_axis`), else `ValueError`; every point has the
    grid's keys in the grid's order.
    """
    if not grid:
        raise ValueError("a grid needs at least one parameter")
    for key, axis in grid.items():
        _check_axis(key, axis)
    keys = list(grid)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*grid.values())]


@dataclass(frozen=True, eq=False)
class Sweep:
    """
    A parameter sweep's results with the grid points that produced them and its total wall time.

    Invariant: `results[i]` ran `points[i]` (`len(results) == len(points)`, and each result's
    merged params hold its point's values), every point has the same keys in the same order, and
    `elapsed_s` is finite and non-negative. Violated by pairing results with another grid --
    `__post_init__` raises `ValueError`.
    """

    points: tuple[Mapping[str, object], ...]
    results: tuple[RunResult, ...]
    elapsed_s: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(dict(p) for p in self.points))
        object.__setattr__(self, "results", tuple(self.results))
        if not self.points or len(self.points) != len(self.results):
            raise ValueError(f"{len(self.results)} results for {len(self.points)} grid points")
        if len({tuple(p) for p in self.points}) != 1:
            raise ValueError("every grid point must have the same keys in the same order")
        for position, (point, result) in enumerate(zip(self.points, self.results, strict=True)):
            if any(result.params.get(k) != v for k, v in point.items()):
                raise ValueError(f"result {position} did not run grid point {point}")
        if not (math.isfinite(self.elapsed_s) and self.elapsed_s >= 0):
            raise ValueError(f"elapsed_s must be finite and non-negative, got {self.elapsed_s}")

    @property
    def keys(self) -> tuple[str, ...]:
        """The swept parameters, in grid order."""
        return tuple(self.points[0])


def check_heatmap_sweep(grid: Mapping[str, Sequence[object]], metric: str) -> None:
    """
    Raise `ValueError` before any run unless `grid` expands (`grid_points`) with exactly two keys
    (a heatmap's two axes) and `metric` is a `MetricReport` field -- so a bad parameter never costs
    a whole sweep first.
    """
    check_metric(metric)
    grid_points(grid)
    if len(grid) != 2:
        raise ValueError(f"a heatmap needs a two-key grid, got keys {tuple(grid)}")


def timed_sweep(
    runner: BacktestRunner, spec: RunSpec, grid: Mapping[str, Sequence[object]]
) -> Sweep:
    """
    `runner.sweep(spec, grid_points(grid))` and its wall time (data loading, the node build and
    every run: the runtime the notebook states), as a `Sweep`.
    """
    points = grid_points(grid)
    started = time.monotonic()
    results = runner.sweep(spec, points)
    return Sweep(tuple(points), tuple(results), time.monotonic() - started)


def metric_grid(sweep: Sweep, metric: str) -> pd.DataFrame:
    """
    Return the heatmap pivot: `metric` per grid point, the first swept key as rows and the second as
    columns, each axis in grid order.

    Invariant: exactly two swept keys (else `ValueError`: a heatmap has two axes) and a
    `MetricReport` field; a cell whose metric is undefined is NaN, never 0.
    """
    check_metric(metric)
    if len(sweep.keys) != 2:
        raise ValueError(f"a heatmap needs a two-key grid, got keys {sweep.keys}")
    row_key, col_key = sweep.keys
    rows = list(dict.fromkeys(p[row_key] for p in sweep.points))
    cols = list(dict.fromkeys(p[col_key] for p in sweep.points))
    table = pd.DataFrame(
        math.nan,
        index=pd.Index(rows, name=row_key),
        columns=pd.Index(cols, name=col_key),
        dtype=float,
    )
    for point, result in zip(sweep.points, sweep.results, strict=True):
        table.loc[point[row_key], point[col_key]] = _float(getattr(result.metrics, metric))
    return table


def metric_grid_note(table: pd.DataFrame, metric: str) -> str | None:
    """Return the sentence printed instead of a heatmap when `metric` is undefined everywhere."""
    if not table.isna().to_numpy().all():
        return None
    return (
        f"{metric} is undefined at every one of the {table.size} grid points, so there is no "
        "heatmap to draw (`all_metrics` returns None, e.g. with no closed trade, or for a return "
        "statistic without realized PnL on two UTC days)."
    )


def sweep_frame(sweep: Sweep) -> pd.DataFrame:
    """
    One row per grid point (index `point`, its grid position): the swept parameters, every
    `MetricReport` field (NaN where undefined) and the run's `config_id`.

    Invariant: the metric columns are exactly `MetricReport.field_names()`, in that order; a
    swept key named like a metric or `config_id` raises `ValueError` rather than shadow it.
    """
    names = MetricReport.field_names()
    clash = set(sweep.keys) & {*names, "config_id"}
    if clash:
        raise ValueError(f"swept keys {sorted(clash)} collide with the frame's metric columns")
    rows = [
        {
            **point,
            **{name: _float(getattr(result.metrics, name)) for name in names},
            "config_id": result.config_id,
        }
        for point, result in zip(sweep.points, sweep.results, strict=True)
    ]
    return pd.DataFrame(rows, index=pd.RangeIndex(len(rows), name="point"))


def top_runs(sweep: Sweep, metric: str, n: int) -> pd.DataFrame:
    """
    Return the `n` best rows of `sweep_frame` by `metric`, highest first (every `all_metrics` value is
    higher-is-better). Invariant: undefined values sort last and ties keep grid order (a stable
    sort), matching `walk_forward.select_best`; `n` must be a positive int, else `ValueError`.
    """
    check_metric(metric)
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive int, got {n!r}")
    frame = sweep_frame(sweep)
    return frame.sort_values(metric, ascending=False, na_position="last", kind="stable").head(n)


def metric_frame(report: MetricReport) -> pd.DataFrame:
    """
    `MetricReport.as_table()` as a frame: index `metric` (exactly `all_metrics`' keys, in its
    order), column `value` -- None where a statistic is undefined, shown as such.
    """
    table = report.as_table()
    return pd.DataFrame(
        {"value": pd.Series([value for _, value in table], dtype=object).to_numpy()},
        index=pd.Index([name for name, _ in table], name="metric"),
    )


def equity_frame(curve: EquityCurve) -> pd.DataFrame:
    """
    Return the equity curve and its underwater series (`EquityCurve.underwater`: `value / running
    peak - 1`, 0 at a peak) on a UTC `DatetimeIndex` named `ts`, one row per curve point.
    """
    values = curve.values  # noqa: PD011 -- a numpy array on EquityCurve, not pandas
    return pd.DataFrame(
        {"equity": values, "underwater": curve.underwater()},
        index=_utc(curve.ts_ns.tolist()).rename("ts"),
    )


def drawdown_frame(curve: EquityCurve) -> pd.DataFrame:
    """
    Every drawdown episode (`EquityCurve.drawdowns`), oldest first: `peak` (NaT while the peak is
    the starting balance), `trough`, `recovery` (NaT if the curve ends under water) and `depth`
    (the trough's fractional loss from the peak). No episode -> no row.
    """
    _, episodes = curve.drawdowns()
    return pd.DataFrame(
        {
            "peak": _utc(e.peak_ts for e in episodes),
            "trough": _utc(e.trough_ts for e in episodes),
            "recovery": _utc(e.recovery_ts for e in episodes),
            "depth": [e.depth for e in episodes],
        },
        index=pd.RangeIndex(len(episodes), name="episode"),
    )


def no_drawdown_note(episodes: pd.DataFrame) -> str | None:
    """Return the sentence printed instead of an empty episode table."""
    if len(episodes):
        return None
    return "The equity never fell below its running peak: no drawdown episode."


def rolling_sharpe_frame(curve: EquityCurve, period_s: int, window: int) -> pd.DataFrame:
    """
    `ReturnSeries.from_equity(curve, period_s).rolling_sharpe(window)` on a UTC `DatetimeIndex`
    named `ts` (the bucket starts), column `rolling_sharpe`: NaN until the window fills or where
    Nautilus's statistic is undefined. A window spanning under two days raises the domain's
    `ValueError`.
    """
    returns = ReturnSeries.from_equity(curve, period_s)
    return pd.DataFrame(
        {"rolling_sharpe": returns.rolling_sharpe(window)},
        index=_utc(returns.ts_ns.tolist()).rename("ts"),
    )


def rolling_sharpe_note(frame: pd.DataFrame, period_s: int, window: int) -> str | None:
    """Return the sentence printed instead of a rolling-Sharpe line with no value on it."""
    if frame["rolling_sharpe"].notna().to_numpy().any():
        return None
    return (
        f"No rolling Sharpe to draw: the equity spans {len(frame)} bucket(s) of {period_s} s, and "
        f"a value needs {window} consecutive returns (a window of at least two UTC days) with "
        "realized PnL on two of those days."
    )


def trade_frame(ledger: TradeLedger) -> pd.DataFrame:
    """
    Return the trade list, oldest exit first: instrument, side, entry/exit (UTC), `holding_s`
    (`TradeLedger.holding_times_s`), peak `qty`, `realized_pnl` (net of fees) and `fees`
    (informational, already inside `realized_pnl`).
    """
    trades = ledger.trades
    return pd.DataFrame(
        {
            "instrument_id": [t.instrument_id for t in trades],
            "side": [t.side for t in trades],
            "entry": _utc(t.entry_ts for t in trades),
            "exit": _utc(t.exit_ts for t in trades),
            "holding_s": ledger.holding_times_s(),
            "qty": [t.qty for t in trades],
            "realized_pnl": ledger.realized_pnls(),
            "fees": [t.fees for t in trades],
        },
        index=pd.RangeIndex(len(trades), name="trade"),
    )


def _report_frame(report: pd.DataFrame) -> pd.DataFrame:
    """
    Return an engine report with every `ts_*` column a UTC timestamp (NaT where unset), indexed by
    `ts` (its `ts_init`, the order's creation / the fill's own `ts_init`) and sorted on it.
    """
    frame = report.reset_index()
    for column in [c for c in frame.columns if str(c).startswith("ts_")]:
        values = pd.to_numeric(frame[column], errors="coerce")
        frame[column] = pd.to_datetime(values, unit="ns", utc=True)
    if "ts_init" not in frame.columns:
        return frame
    return frame.set_index(frame["ts_init"].rename("ts")).sort_index(kind="stable")


def orders_frame(result: RunResult) -> pd.DataFrame:
    """
    Return the run's orders, exactly the engine's `generate_orders_report()` (one row per order,
    every column kept) with its `ts_*` columns as UTC timestamps and a `ts` index; empty when the
    strategy sent none.
    """
    return _report_frame(result.orders)


def fills_frame(result: RunResult) -> pd.DataFrame:
    """
    Return the run's fills, exactly the engine's `generate_order_fills_report()` (one row per
    order that filled, with its `avg_px`, `slippage` and `commissions`) with its `ts_*` columns as
    UTC timestamps and a `ts` index; empty when nothing filled.
    """
    return _report_frame(result.fills)


def nautilus_stats_frame(result: RunResult) -> pd.DataFrame:
    """
    Return one row per Nautilus statistic across `pnls` (one group per currency), `returns` and
    `general`: `group`, `statistic`, `value`. An undefined value is NaN, never 0.
    """
    rows: list[tuple[str, str, float]] = []
    for group, stats in result.nautilus_stats.items():
        scoped = (
            {f"{group}[{k}]": v for k, v in stats.items()} if group == "pnls" else {group: stats}
        )
        for name, values in scoped.items():
            rows += [(name, stat, _stat_value(value)) for stat, value in values.items()]
    return pd.DataFrame(rows, columns=["group", "statistic", "value"])


def _stat_value(value: object) -> float:
    """Return a Nautilus statistic as a float; None or a non-number is NaN."""
    return (
        float(value) if isinstance(value, int | float) and not isinstance(value, bool) else math.nan
    )


def no_trades_note(ledger: TradeLedger) -> str | None:
    """Return the sentence printed instead of empty trade distributions and trade list."""
    if len(ledger):
        return None
    return (
        "The run closed no trade: there is no PnL or holding-time distribution, no PnL by hour "
        "or weekday and no trade list to show."
    )


def pnl_by_hour_frame(result: RunResult) -> pd.DataFrame:
    """
    `RunResult.pnl_by_hour_of_day()` as a frame (index `hour_utc`, column `pnl`): only the hours
    with an exit -- an hour with none stays absent, never a 0 row.
    """
    buckets = result.pnl_by_hour_of_day()
    return pd.DataFrame(
        {"pnl": list(buckets.values())}, index=pd.Index(list(buckets), name="hour_utc")
    )


def pnl_by_weekday_frame(result: RunResult) -> pd.DataFrame:
    """
    `RunResult.pnl_by_weekday()` as a frame (index `weekday`, 0 = Monday; columns `day` and `pnl`):
    only the weekdays with an exit -- a day with none stays absent, never a 0 row.
    """
    buckets = result.pnl_by_weekday()
    return pd.DataFrame(
        {"day": [WEEKDAYS[d] for d in buckets], "pnl": list(buckets.values())},
        index=pd.Index(list(buckets), name="weekday"),
    )


def oos_equity_frame(wf: WalkForwardResult) -> pd.DataFrame:
    """
    `equity_frame(wf.oos_equity)` plus a `fold` column (from 1), so a plot draws one line per fold
    and never bridges the in-sample stretch between two OOS windows, where no OOS run traded.
    """
    frame = equity_frame(wf.oos_equity)
    frame["fold"] = [k for k, fr in enumerate(wf.folds, start=1) for _ in range(len(fr.oos.equity))]
    return frame


def fold_frame(wf: WalkForwardResult) -> pd.DataFrame:
    """
    One row per walk-forward fold (index `fold`, from 1): its four bounds (UTC), the grid point
    picked in-sample (`pick`), the value it was picked on (`in_sample_<select_by>`, None when the
    metric was undefined at every grid point -- shown, not hidden), both runs' trade counts and
    every out-of-sample metric (`oos_<field>`, NaN where undefined).
    """
    rows = []
    for fr in wf.folds:
        fold = fr.fold
        rows.append(
            {
                "in_start": pd.Timestamp(fold.in_start, unit="ns", tz="UTC"),
                "in_end": pd.Timestamp(fold.in_end, unit="ns", tz="UTC"),
                "oos_start": pd.Timestamp(fold.oos_start, unit="ns", tz="UTC"),
                "oos_end": pd.Timestamp(fold.oos_end, unit="ns", tz="UTC"),
                "pick": dict(fr.point),
                "in_sample_trades": len(fr.in_sample.trades),
                "oos_trades": len(fr.oos.trades),
                **{
                    f"oos_{name}": _float(getattr(fr.oos.metrics, name))
                    for name in MetricReport.field_names()
                },
            }
        )
    frame = pd.DataFrame(rows, index=pd.RangeIndex(1, len(rows) + 1, name="fold"))
    # An object column, so a pick no metric decided stays None: a dict row would coerce it to NaN.
    frame.insert(
        frame.columns.tolist().index("pick") + 1,
        f"in_sample_{wf.select_by}",
        pd.Series([fr.pick_value for fr in wf.folds], index=frame.index, dtype=object),
    )
    return frame


def fold_notes(wf: WalkForwardResult) -> list[str]:
    """One sentence per fold whose pick no metric decided (every in-sample value undefined)."""
    return [
        f"Fold {k}: {wf.select_by} is undefined at every in-sample grid point, so the first "
        f"point {dict(fr.point)} was taken by default."
        for k, fr in enumerate(wf.folds, start=1)
        if fr.pick_value is None
    ]


def walk_forward_metrics_frame(wf: WalkForwardResult) -> pd.DataFrame:
    """
    Return the merged in-sample and out-of-sample `MetricReport`s side by side (index `metric`, exactly
    `all_metrics`' keys; columns `in_sample`, `out_of_sample`; NaN where undefined).
    """
    names = MetricReport.field_names()
    return pd.DataFrame(
        {
            "in_sample": [_float(getattr(wf.in_sample_metrics, n)) for n in names],
            "out_of_sample": [_float(getattr(wf.oos_metrics, n)) for n in names],
        },
        index=pd.Index(names, name="metric"),
    )
