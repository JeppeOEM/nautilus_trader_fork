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
Walk-forward evaluation (Story 27.5): split a window into consecutive in-sample/out-of-sample
folds, pick each fold's grid point on the in-sample runs and run it out of sample, all through the
`BacktestRunner` port (one `sweep` and one `run` per fold -- never a second backtest path).

The out-of-sample (OOS) equity is a convention, not the equity of one continuous run: every fold's
OOS run starts flat from the full `starting_balance`, and its curve is shifted additively by the
cumulative OOS PnL of the folds before it (`concat_equity`), matching the additive `equity += pnl`
of `kernel.performance_metrics.equity_returns`. The OOS and in-sample metrics are
`MetricReport.from_ledger` over the merged trades of every fold (`TradeLedger.of`), never an
average of per-fold statistics.

Known limit: each OOS run restarts the strategy, so its warm-up (`warmup_seconds`, indicator
windows) is spent inside the OOS window and a position still open at the window's end is never
closed (its PnL is absent, as `RunResult`'s own Known limit states); upgrade path: stream each OOS
run's data from `oos_start - warmup` and have the strategy trade only from `oos_start`.
"""

import math
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from itertools import pairwise

import numpy as np

from research.application.ports import BacktestRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import TradeLedger


def check_metric(name: str) -> str:
    """Return `name` if it is a `MetricReport` field (an `all_metrics` key), else `ValueError`."""
    if name not in MetricReport.field_names():
        raise ValueError(f"{name!r} is not a MetricReport field: {MetricReport.field_names()}")
    return name


def defined_metric(report: MetricReport, name: str) -> float | None:
    """Return `report`'s `name` value, or None when it is undefined (None or NaN)."""
    value = getattr(report, name)
    return None if value is None or math.isnan(value) else float(value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Fold:
    """
    One walk-forward fold: the in-sample window `[in_start, in_end)` immediately followed by the
    out-of-sample window `[oos_start, oos_end)`, all integer ns.

    Invariant: `in_start < in_end == oos_start < oos_end` -- both parts non-empty and adjacent.
    Violated by constructing it from non-int bounds, an empty part or a gap/overlap between the two
    -- `__post_init__` raises `ValueError`.
    """

    in_start: int
    in_end: int
    oos_start: int
    oos_end: int

    def __post_init__(self) -> None:
        bounds = (self.in_start, self.in_end, self.oos_start, self.oos_end)
        if not all(_is_int(b) for b in bounds):
            raise ValueError(f"fold bounds must be int ns, got {bounds}")
        if not self.in_start < self.in_end == self.oos_start < self.oos_end:
            raise ValueError(f"a fold needs in_start < in_end == oos_start < oos_end, got {bounds}")


def folds(start_ns: int, end_ns: int, n: int, in_sample_fraction: float) -> list[Fold]:
    """
    Split `[start_ns, end_ns)` into `n` consecutive, non-overlapping segments of equal length
    (segment `k` is `[start + span * k // n, start + span * (k + 1) // n)`, so lengths differ by at
    most 1 ns when `span` is not a multiple of `n`); in each, the first
    `round(length * in_sample_fraction)` ns are in-sample and the rest is out-of-sample.

    Invariant: every returned `Fold` holds, and the folds tile the window in order. `n < 1`, a
    fraction outside `(0, 1)`, an empty window or a segment whose in-sample or OOS part rounds to
    empty raise `ValueError`.
    """
    if not _is_int(n) or n < 1:
        raise ValueError(f"n must be a positive int, got {n!r}")
    if isinstance(in_sample_fraction, bool) or not isinstance(in_sample_fraction, int | float):
        raise ValueError(f"in_sample_fraction must be a number, got {in_sample_fraction!r}")
    fraction = float(in_sample_fraction)
    if not 0.0 < fraction < 1.0:
        raise ValueError(f"in_sample_fraction must lie in (0, 1), got {in_sample_fraction!r}")
    if not (_is_int(start_ns) and _is_int(end_ns)) or end_ns <= start_ns:
        raise ValueError(f"the window must be int ns with end after start: {start_ns}, {end_ns}")
    span = end_ns - start_ns
    bounds = [start_ns + span * k // n for k in range(n + 1)]
    out = []
    for lo, hi in pairwise(bounds):
        split = lo + round((hi - lo) * fraction)
        if not lo < split < hi:
            raise ValueError(
                f"segment [{lo}, {hi}) has an empty in-sample or out-of-sample part at "
                f"fraction {fraction}: widen the window or use fewer folds"
            )
        out.append(Fold(lo, split, split, hi))
    return out


def select_best(results: Sequence[RunResult], select_by: str) -> tuple[int, float | None]:
    """
    Return the index and value of the result with the highest `select_by` metric.

    Invariant: higher is better for every `all_metrics` value (`max_drawdown` and the losses are
    negative), so the maximum wins; an undefined value (None, or NaN) ranks below any number; ties
    go to the earliest result (grid order); when every value is undefined the first result is
    taken and the value returned is None, so the caller can show that no metric decided. An
    unknown `select_by` or no result raises `ValueError`.
    """
    check_metric(select_by)
    if not results:
        raise ValueError("select_best needs at least one result")
    best_index, best = 0, None
    for index, result in enumerate(results):
        value = defined_metric(result.metrics, select_by)
        if value is None:
            continue
        if best is None or value > best:
            best_index, best = index, value
    return best_index, best


def _check_within(curve: EquityCurve, start: int, end: int, part: str) -> None:
    """
    Raise unless every equity stamp lies in `[start, end)`. `NodeRunner` bounds the replay on
    `ts_init` to the spec's window and starts the engine clock at the first replayed row, so the
    account's first event (its opening balance) is stamped inside the window too -- a stamp
    outside it is a runner defect, never a point to drop.
    """
    if int(curve.ts_ns[0]) < start or int(curve.ts_ns[-1]) >= end:
        raise ValueError(
            f"{part} equity spans [{int(curve.ts_ns[0])}, {int(curve.ts_ns[-1])}], outside its "
            f"window [{start}, {end})"
        )


@dataclass(frozen=True, eq=False)
class FoldResult:
    """
    One fold's outcome: the grid `point` picked in-sample, its in-sample run (`in_sample`, the
    winner of the fold's sweep), its out-of-sample run (`oos`) and the metric it was picked on
    (`select_by`).

    Invariant: `in_sample` ran `point` (its merged params hold every value of `point`), `oos` ran
    the winner's exact parameters (`oos.params == in_sample.params`), `select_by` is a
    `MetricReport` field, and each run's equity stamps lie inside its own part of `fold`. Violated
    by pairing runs of different parameters or windows -- `__post_init__` raises `ValueError`.
    `pick_value` is derived, never stored, so it cannot disagree with the winner's metrics.
    """

    fold: Fold
    point: Mapping[str, object]
    in_sample: RunResult
    oos: RunResult
    select_by: str

    def __post_init__(self) -> None:
        check_metric(self.select_by)
        object.__setattr__(self, "point", dict(self.point))
        if any(self.in_sample.params.get(k) != v for k, v in self.point.items()):
            raise ValueError(
                f"the in-sample run's params {dict(self.in_sample.params)} did not run the picked "
                f"point {self.point}"
            )
        if dict(self.oos.params) != dict(self.in_sample.params):
            raise ValueError(
                f"the OOS run's params {dict(self.oos.params)} are not the in-sample winner's "
                f"{dict(self.in_sample.params)}"
            )
        _check_within(self.in_sample.equity, self.fold.in_start, self.fold.in_end, "in-sample")
        _check_within(self.oos.equity, self.fold.oos_start, self.fold.oos_end, "out-of-sample")

    @property
    def pick_value(self) -> float | None:
        """
        The winner's in-sample `select_by` value, None when undefined: `select_best` takes the
        highest defined value, so the winner's value is None only when no grid point had one.
        """
        return defined_metric(self.in_sample.metrics, self.select_by)


def concat_equity(curves: Sequence[EquityCurve], starting_balance: float) -> EquityCurve:
    """
    Join per-fold OOS equity curves into one: fold `k`'s values are shifted by the summed PnL of
    folds `0..k-1` (each prior curve's terminal value minus `starting_balance`), so the joined
    curve reads as if each fold's PnL were added to one account (`equity += pnl`).

    Invariant: every curve starts from `starting_balance` (each fold runs from the full balance),
    else `ValueError`; the stamps must be strictly increasing across folds, else `EquityCurve`'s
    own `ValueError` (overlapping or out-of-order folds). Not the equity of one continuous run:
    the in-sample stretch between two OOS windows holds no point, so a plot must break the line
    there (`evaluation.oos_equity_frame`) and `ReturnSeries.from_equity` over the joined curve would
    read each stretch as flat equity -- the OOS statistics come from the merged trades instead.
    """
    if not curves:
        raise ValueError("concat_equity needs at least one curve")
    offset = 0.0
    stamps: list[np.ndarray] = []
    values: list[np.ndarray] = []
    for curve in curves:
        if curve.starting_balance != starting_balance:
            raise ValueError(
                f"every fold must start from {starting_balance}, got {curve.starting_balance}"
            )
        stamps.append(curve.ts_ns)
        values.append(curve.values + offset)
        offset += float(curve.values[-1]) - starting_balance
    return EquityCurve(np.concatenate(stamps), np.concatenate(values), starting_balance)


def _merged_metrics(
    folds: Sequence["FoldResult"], part: str, starting_balance: float
) -> MetricReport:
    """`MetricReport.from_ledger` over every fold's `part` (`"oos"`/`"in_sample"`) trades merged."""
    ledgers: list[TradeLedger] = [getattr(f, part).trades for f in folds]
    merged = TradeLedger.of(trade for ledger in ledgers for trade in ledger.trades)
    return MetricReport.from_ledger(merged, starting_balance)


def _check_fold_order(folds: Sequence[Fold]) -> None:
    """Raise `ValueError` unless `folds` is non-empty and in time order with disjoint OOS windows."""
    if not folds:
        raise ValueError("a walk-forward needs at least one fold")
    for before, after in pairwise(folds):
        if after.oos_start < before.oos_end:
            raise ValueError("folds must be in time order with non-overlapping OOS windows")


def _check_folds(folds: Sequence[FoldResult], select_by: str) -> None:
    check_metric(select_by)
    _check_fold_order([f.fold for f in folds])
    if any(f.select_by != select_by for f in folds):
        raise ValueError(f"every fold must be picked on {select_by!r}")


@dataclass(frozen=True, eq=False)
class WalkForwardResult:
    """
    Every fold's result plus the joined out-of-sample view.

    Invariant: `folds` are in time order with non-overlapping OOS windows; `oos_equity` is
    `concat_equity` of the folds' OOS curves (the additive convention, not one continuous run);
    `oos_metrics` and `in_sample_metrics` are `MetricReport.from_ledger` over the merged OOS
    trades and the merged in-sample winners' trades; every fold was picked on `select_by`.
    Violated by any other combination -- `__post_init__` re-derives the joined values and raises
    `ValueError` on a mismatch; `of` builds a consistent one.
    """

    select_by: str
    starting_balance: float
    folds: tuple[FoldResult, ...]
    oos_equity: EquityCurve
    oos_metrics: MetricReport
    in_sample_metrics: MetricReport

    def __post_init__(self) -> None:
        object.__setattr__(self, "folds", tuple(self.folds))
        _check_folds(self.folds, self.select_by)
        joined = concat_equity([f.oos.equity for f in self.folds], self.starting_balance)
        if not (
            np.array_equal(joined.ts_ns, self.oos_equity.ts_ns)
            and np.array_equal(joined.values, self.oos_equity.values)
            and joined.starting_balance == self.oos_equity.starting_balance
        ):
            raise ValueError("oos_equity is not the joined OOS equity of the folds")
        if self.oos_metrics != _merged_metrics(self.folds, "oos", self.starting_balance):
            raise ValueError("oos_metrics are not the merged OOS trades' metrics")
        if self.in_sample_metrics != _merged_metrics(
            self.folds, "in_sample", self.starting_balance
        ):
            raise ValueError("in_sample_metrics are not the merged in-sample trades' metrics")

    @classmethod
    def of(
        cls, folds: Sequence[FoldResult], select_by: str, starting_balance: float
    ) -> "WalkForwardResult":
        _check_folds(folds, select_by)
        return cls(
            select_by=select_by,
            starting_balance=starting_balance,
            folds=tuple(folds),
            oos_equity=concat_equity([f.oos.equity for f in folds], starting_balance),
            oos_metrics=_merged_metrics(folds, "oos", starting_balance),
            in_sample_metrics=_merged_metrics(folds, "in_sample", starting_balance),
        )


def _run_fold(
    runner: BacktestRunner,
    spec: RunSpec,
    grid: Sequence[Mapping[str, object]],
    fold: Fold,
    select_by: str,
) -> FoldResult:
    try:
        in_sample = runner.sweep(replace(spec, start=fold.in_start, end=fold.in_end), grid)
        index, _ = select_best(in_sample, select_by)
        winner = in_sample[index]
        oos = runner.run(
            replace(spec, start=fold.oos_start, end=fold.oos_end, params=winner.params)
        )
    except RuntimeError as error:
        raise RuntimeError(f"walk-forward {fold}: {error}") from error
    except ValueError as error:
        raise ValueError(f"walk-forward {fold}: {error}") from error
    return FoldResult(fold, dict(grid[index]), winner, oos, select_by)


def walk_forward(
    runner: BacktestRunner,
    spec: RunSpec,
    grid: Sequence[Mapping[str, object]],
    folds: Sequence[Fold],
    select_by: str,
) -> WalkForwardResult:
    """
    Per fold: `runner.sweep` the grid over the in-sample window (`spec` with only `start`/`end`
    replaced), pick the best point by `select_by` (`select_best`), then `runner.run` the
    out-of-sample window with the winner's merged parameters; join the folds
    (`WalkForwardResult.of`).

    Invariant: every backtest goes through `runner` (one node per sweep or run, each window read
    once per call); `select_by`, a non-empty grid and the folds' order (time order, disjoint OOS
    windows) are checked before any run, and the grid's points by the runner's own `sweep` before
    its first node. A runner error inside a fold (an empty window: the node's `RuntimeError`, or
    the `seconds` kind's `ValueError` for a window with no snapshot) is re-raised as the same type,
    prefixed with the fold it happened in.
    Known limit: that raise discards the folds already run (no partial walk-forward), so a
    collector outage covering one fold's in-sample or OOS window fails the whole walk-forward;
    upgrade path: check each fold's coverage up front (`MarketFrames.bar_coverage`) and report a
    fold with no data as such, next to the folds that ran.
    """
    check_metric(select_by)
    if not grid:
        raise ValueError("a walk-forward needs at least one grid point")
    _check_fold_order(folds)
    results = [_run_fold(runner, spec, grid, fold, select_by) for fold in folds]
    return WalkForwardResult.of(results, select_by, float(spec.starting_balance))
