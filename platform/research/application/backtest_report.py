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
Backtest reports: one folder per backtest run, so a run can be reopened and compared after the
notebook that ran it has stopped.

`save_backtest_report(result, spec, root)` writes `<root>/<StrategyClass>_<YYYY-MM-DDTHH-MM-SSZ>/`
(UTC, the save time; `-2`, `-3`, ... when that name is taken) holding:

- `tearsheet.html` -- Nautilus's own tearsheet: `create_tearsheet(engine, ...)` when the engine is
  still alive (`NodeRunner(report_root=...)` calls this before `node.dispose()`), else
  `create_tearsheet_from_stats` over the `RunResult`'s `nautilus_stats` and `returns`. Nautilus's
  panels (run information, account summary, statistics, equity, drawdown, monthly and yearly
  returns, distribution, rolling Sharpe) plus three of this module's: a "Run Configuration" table
  (the `RunSpec`, its execution models, the strategy file's digest, the git revision and the
  versions), realized PnL by UTC exit hour and by UTC exit weekday (`evaluation.pnl_by_hour_frame`
  / `pnl_by_weekday_frame`, the trade ledger's own buckets), and for a `bars:<spec>` run one
  `bars_with_fills` panel per instrument with, on a one-instrument run, a buy-and-hold benchmark
  of the same bars (daily returns of each UTC day's last close, `ReturnSeries.from_prices`).
- `strategy.py` -- the strategy class's module file, verbatim (`inspect.getsourcefile`), and
  `strategy_config.py` when the config class lives in another file.
- `record.json` -- everything the run computed: the `RunSpec`, the `MetricReport`, Nautilus's
  `stats_pnls`/`stats_returns`/`stats_general`, the closed trades, the equity curve, the PnL by
  day/hour/weekday, the run ids, counts and engine time, the benchmark, the git revision and the
  versions. Written last: a folder without it is a save that failed (and is removed).

and appends one line to `<root>/index.jsonl` (`list_backtest_reports` reads it back as a frame
for cross-run comparison; `load_backtest_record` reads one folder's record).

Reports live beside the archive, never in it: a root inside the spec's catalog is refused, and the
research notebooks still never write the archive. A save that fails at any step raises and removes
its folder (DATA-07). Nautilus reports an undefined statistic (no winner, one daily return) as NaN;
that one documented conversion stores it as JSON `null`, and any other non-finite or non-JSON value
anywhere in the record raises `ValueError` naming where (`allow_nan=False`).

Known limit: the three extra panels register through `nautilus_trader.analysis.tearsheet.
_register_tearsheet_chart`, the route Nautilus's `docs/concepts/visualization.md` gives for
tearsheet integration but marks internal (`register_chart` alone feeds only the standalone
registry, and the tearsheet skips a name it cannot render without a word); the build therefore
refuses a configured panel the spec registry lacks, and `test_backtest_report.py` reads every panel
title back from the HTML, so an upstream rename fails loudly. Upgrade path: a public tearsheet
registration API upstream.
Known limit: each `tearsheet.html` embeds plotly.js (~4.7 MB) because Nautilus's writer does, so a
report opens offline and survives being moved; upgrade path: one shared bundle at the reports root.
Known limit: `strategy.py` pins the strategy's own file only; the code it imports (indicators,
`kernel`, exec algorithms, Nautilus) is pinned by the git revision alone, and a `-dirty` revision
means uncommitted changes existed when the run was saved.
Known limit: the bars panel and the benchmark read the engine cache, which keeps the last
`CacheConfig.bar_capacity` bars (10 000 by default); a run with more is shown as its last bars
(said in the panel title) and gets no benchmark (said in the record and the run table); upgrade
path: a larger `CacheConfig.bar_capacity` on the run's engine.
Known limit: the engine's orders and fills reports are counted, not stored; upgrade path: write
them beside `record.json` as Parquet.
Known limit: `strategy.py` is the file as it was on disk when the run started; a notebook kernel
that imported the module before the file was edited runs the old code while the copy shows the
new (restart the kernel after editing a strategy); upgrade path: copy the imported module's code
object source at import time.
Known limit: nothing prunes the reports root: every saved run keeps its ~5 MB folder (notebook 08
saves 21 per execution) until deleted by hand -- delete the folder and its `index.jsonl` line
together, as `list_backtest_reports` lists what the index says and a hand-deleted folder would
still be listed; a line torn by a crash makes the listing raise naming it (delete that line).
Upgrade path: a retention setting and a `prune_backtest_reports(root, keep=...)`.
"""

import hashlib
import importlib
import inspect
import itertools
import json
import math
import os
import platform as python_platform
import shutil
import subprocess
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import fields
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from kernel.clocks import NS_PER_S

import nautilus_trader
from nautilus_trader.analysis import GridLayout
from nautilus_trader.analysis import TearsheetBarsWithFillsChart
from nautilus_trader.analysis import TearsheetChart
from nautilus_trader.analysis import TearsheetConfig
from nautilus_trader.analysis import TearsheetCustomChart
from nautilus_trader.analysis import TearsheetDistributionChart
from nautilus_trader.analysis import TearsheetDrawdownChart
from nautilus_trader.analysis import TearsheetEquityChart
from nautilus_trader.analysis import TearsheetMonthlyReturnsChart
from nautilus_trader.analysis import TearsheetRollingSharpeChart
from nautilus_trader.analysis import TearsheetRunInfoChart
from nautilus_trader.analysis import TearsheetStatsTableChart
from nautilus_trader.analysis import TearsheetYearlyReturnsChart
from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.analysis import create_tearsheet_from_stats
from nautilus_trader.analysis import tearsheet as nautilus_tearsheet
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from research.application.evaluation import pnl_by_hour_frame
from research.application.evaluation import pnl_by_weekday_frame
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.domain.report import MetricReport
from research.domain.returns import ReturnSeries


REPORT_SCHEMA = 1
INDEX_FILE = "index.jsonl"
TEARSHEET_FILE = "tearsheet.html"
STRATEGY_FILE = "strategy.py"
STRATEGY_CONFIG_FILE = "strategy_config.py"
RECORD_FILE = "record.json"
# The engine cache's default bar capacity: `NodeRunner.build_run_config` sets no `CacheConfig`.
DEFAULT_BAR_CAPACITY = CacheConfig().bar_capacity

RUN_CONFIG_CHART = "research_run_configuration"
PNL_BY_HOUR_CHART = "research_pnl_by_exit_hour"
PNL_BY_WEEKDAY_CHART = "research_pnl_by_exit_weekday"
RUN_CONFIG_TITLE = "Run Configuration"
PNL_BY_HOUR_TITLE = "Realized PnL by Exit Hour (UTC)"
PNL_BY_WEEKDAY_TITLE = "Realized PnL by Exit Weekday (UTC)"
BARS_TITLE = "Bars with Order Fills"
# The title of every Nautilus panel a report shows, as Nautilus registers it.
NAUTILUS_PANEL_TITLES = (
    "Run Information",
    "Performance Statistics",
    "Equity Curve",
    "Drawdown",
    "Monthly Returns",
    "Returns Distribution",
    "Rolling Sharpe Ratio (60-day)",
    "Yearly Returns",
)
_BENCHMARK_NAME = "Buy and hold"
_PNL_TOTAL = "PnL (total)"
# The columns `list_backtest_reports` gives, metrics and PnL totals aside, empty root or not.
INDEX_COLUMNS = (
    "folder",
    "saved_at",
    "strategy",
    "strategy_path",
    "config_id",
    "run_id",
    "instruments",
    "data",
    "start",
    "end",
    "closed_trades",
    "git_revision",
)
_SECONDS_PER_DAY = 86_400


# -- the three extra tearsheet panels ---------------------------------------------------------


def _render_table(
    fig: go.Figure,
    row: int,
    col: int,
    theme_config: dict[str, Any],
    rows: Sequence[Sequence[str]] = (),
    **kwargs: Any,
) -> None:
    """Draw `rows` (`(name, value)` pairs) as a two-column table in Nautilus's table style."""
    colors = theme_config["colors"]
    stripes = [colors["table_row_odd"], colors["table_row_even"]]
    fill = [stripes[i % 2] for i in range(len(rows))]
    fig.add_trace(
        go.Table(
            header={
                "values": ["<b>Setting</b>", "<b>Value</b>"],
                "fill_color": colors["primary"],
                "font": {"color": "white", "size": 12},
                "align": "left",
            },
            cells={
                "values": [[name for name, _ in rows], [value for _, value in rows]],
                "fill_color": [fill, fill],
                "align": "left",
                "font": {"size": 11, "color": colors["table_text"]},
            },
        ),
        row=row,
        col=col,
    )


def _render_pnl_bars(
    fig: go.Figure,
    row: int,
    col: int,
    theme_config: dict[str, Any],
    labels: Sequence[str] = (),
    values: Sequence[float] = (),
    axis_title: str = "",
    **kwargs: Any,
) -> None:
    """Draw one bar per bucket that had an exit; an empty bucket is absent, never a 0 bar."""
    colors = theme_config["colors"]
    fig.add_trace(
        go.Bar(
            x=list(labels),
            y=list(values),
            marker_color=[colors["positive"] if v >= 0 else colors["negative"] for v in values],
            showlegend=False,
            name="Realized PnL",
        ),
        row=row,
        col=col,
    )
    fig.update_xaxes(title_text=axis_title, type="category", row=row, col=col)
    fig.update_yaxes(title_text="Realized PnL", row=row, col=col)


nautilus_tearsheet._register_tearsheet_chart(
    RUN_CONFIG_CHART, "table", RUN_CONFIG_TITLE, _render_table
)
nautilus_tearsheet._register_tearsheet_chart(
    PNL_BY_HOUR_CHART, "bar", PNL_BY_HOUR_TITLE, _render_pnl_bars
)
nautilus_tearsheet._register_tearsheet_chart(
    PNL_BY_WEEKDAY_CHART, "bar", PNL_BY_WEEKDAY_TITLE, _render_pnl_bars
)


# -- values carried into the record and the tearsheet -----------------------------------------


@dataclass(frozen=True)
class BarsPanel:
    """
    One `bars_with_fills` panel. Invariant: `count` is the cached bars of `bar_type`, and
    `truncated` is true exactly when the cache was full (`count >= capacity`): the cache cannot
    tell a run of exactly `capacity` bars from one that dropped its oldest, so a full cache never
    claims the whole run (the panel says "last N bars", and there is no benchmark).
    """

    bar_type: str
    count: int
    truncated: bool

    @property
    def title(self) -> str:
        if self.truncated:
            return f"{BARS_TITLE}: {self.bar_type} (last {self.count:_} bars: the cache was full)"
        return f"{BARS_TITLE}: {self.bar_type}"


@dataclass(frozen=True)
class Benchmark:
    """
    The buy-and-hold benchmark of a one-instrument bars run, or why there is none.

    Invariant: exactly one of `returns` (daily returns, finite, UTC-indexed) and `omitted` (the
    reason) is set; `bar_type` names the bars it was built from when it was built from any.
    """

    returns: pd.Series | None
    omitted: str | None
    bar_type: str | None = None

    def as_record(self) -> dict[str, object]:
        if self.returns is None:
            return {"name": _BENCHMARK_NAME, "omitted": self.omitted}
        return {
            "name": _BENCHMARK_NAME,
            "bar_type": self.bar_type,
            "returns": _series_record(self.returns),
        }

    def describe(self) -> str:
        if self.returns is None:
            return f"none: {self.omitted}"
        return f"{_BENCHMARK_NAME} of {self.bar_type} ({len(self.returns)} daily returns)"


def _bar_types(spec: RunSpec) -> list[BarType]:
    """Return the bar type `NodeRunner` injects per instrument of a `bars:<spec>` run, else none."""
    if spec.bar_spec is None:
        return []
    return [BarType.from_str(f"{iid}-{spec.bar_spec}-LAST-INTERNAL") for iid in spec.instrument_ids]


def bars_panels(engine: BacktestEngine, spec: RunSpec, capacity: int) -> list[BarsPanel]:
    """One panel per bar type of the run that has cached bars (a type with none has no panel)."""
    panels = []
    for bar_type in _bar_types(spec):
        count = len(engine.cache.bars(bar_type))
        if count:
            panels.append(BarsPanel(str(bar_type), count, count >= capacity))
    return panels


def buy_and_hold(bars: Sequence[Bar]) -> pd.Series:
    """
    Daily returns of holding the instrument: each UTC day's last close against the previous
    day's (`ReturnSeries.from_prices` on the day grid, so a day with no bar is a gap, never
    bridged, and is left out). `bars` in replay order, as the cache holds them reversed.
    """
    closes: dict[int, float] = {}
    for bar in sorted(bars, key=lambda b: b.ts_event):
        closes[bar.ts_event // (_SECONDS_PER_DAY * NS_PER_S)] = bar.close.as_double()
    days = sorted(closes)
    series = ReturnSeries.from_prices(
        [closes[d] for d in days], [d * _SECONDS_PER_DAY * NS_PER_S for d in days], _SECONDS_PER_DAY
    )
    points = series.as_dict()
    return pd.Series(
        list(points.values()),
        index=pd.DatetimeIndex(pd.to_datetime(list(points), unit="ns", utc=True)),
        dtype="float64",
    )


def benchmark_of(
    engine: BacktestEngine | None, spec: RunSpec, panels: Sequence[BarsPanel]
) -> Benchmark:
    """Return the buy-and-hold benchmark when the run's one instrument's bars are all cached."""
    if spec.bar_spec is None:
        return Benchmark(None, f"data {spec.data!r} has no bars")
    if engine is None:
        return Benchmark(None, "saved without the engine: its bars are gone")
    if len(spec.instrument_ids) != 1:
        return Benchmark(None, "a buy-and-hold of several instruments is not one series")
    if not panels:
        return Benchmark(None, "the engine cached no bar")
    panel = panels[0]
    if panel.truncated:
        return Benchmark(
            None,
            f"the cache was full ({panel.count:_} bars): older bars may be gone",
            panel.bar_type,
        )
    returns = buy_and_hold(engine.cache.bars(BarType.from_str(panel.bar_type)))
    if returns.empty:
        return Benchmark(None, "the bars span fewer than two adjacent UTC days", panel.bar_type)
    return Benchmark(returns, None, panel.bar_type)


# -- JSON values ------------------------------------------------------------------------------


def _json_value(value: object, where: str) -> object:
    """
    Return `value` as plain JSON: str/int/bool/None as they are, a finite float, a `Decimal` as
    its exact text, a mapping (str keys) or sequence recursively; anything else, and a non-finite
    float, raises naming `where`.
    """
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float | np.floating):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{where}: {number} is not a finite number")
        return number
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {_key(k, where): _json_value(v, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(v, f"{where}[{i}]") for i, v in enumerate(value)]
    raise ValueError(f"{where}: {type(value).__name__} {value!r} is not a JSON value")


def _key(key: object, where: str) -> str:
    if not isinstance(key, str):
        raise ValueError(f"{where}: key {key!r} is not a str")
    return key


def _stats(stats: Mapping[str, object], where: str) -> dict[str, object]:
    """
    Nautilus statistics as JSON: NaN is Nautilus's "undefined" (no winner, one daily return), the
    one value stored as `null`; an infinity or a non-number raises naming the statistic.
    """
    out: dict[str, object] = {}
    for name, value in stats.items():
        undefined = isinstance(value, float | np.floating) and math.isnan(value)
        out[name] = None if undefined else _json_value(value, f"{where}.{name}")
    return out


def _nautilus_stats(result: RunResult) -> dict[str, object]:
    stats = result.nautilus_stats
    return {
        "pnls": {
            currency: _stats(values, f"nautilus_stats.pnls.{currency}")
            for currency, values in stats["pnls"].items()
        },
        "returns": _stats(stats["returns"], "nautilus_stats.returns"),
        "general": _stats(stats["general"], "nautilus_stats.general"),
    }


def _series_record(series: pd.Series) -> dict[str, list]:
    return {
        "ts": [pd.Timestamp(ts).isoformat() for ts in series.index],
        "values": [_json_value(v, "series") for v in series.tolist()],
    }


def spec_record(spec: RunSpec) -> dict[str, object]:
    """Return the `RunSpec` field by field as JSON (`params` and the models as plain objects)."""
    return {f.name: _json_value(getattr(spec, f.name), f"spec.{f.name}") for f in fields(spec)}


# -- the strategy file and the environment ----------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    """
    A class's module file as read once. Invariant: `sha256` is the digest of `content`, and
    `content` is exactly what the report copies -- one read, so the two can never disagree.
    """

    cls_name: str
    path: Path
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def source_file(import_path: str) -> SourceFile:
    """Return the module file defining `module:Class`; `ValueError` when it has no source file."""
    module_name, _, cls_name = import_path.partition(":")
    cls = getattr(importlib.import_module(module_name), cls_name)
    found = inspect.getsourcefile(cls)
    if found is None:
        raise ValueError(f"{import_path} has no Python source file to copy (a compiled module?)")
    path = Path(found)
    return SourceFile(cls_name, path, path.read_bytes())


@dataclass(frozen=True)
class StrategySource:
    """
    The strategy's and its config's module files, read when the run starts.

    Invariant: `config` is None exactly when the config class lives in the strategy's own file.
    `NodeRunner` reads it before `node.build()`, so the copy is the file the engine imported, not
    one edited while the run went on (see the module's Known limit for a kernel's stale import).
    """

    strategy: SourceFile
    config: SourceFile | None

    @classmethod
    def read(cls, spec: RunSpec) -> "StrategySource":
        strategy = source_file(spec.strategy_path)
        config = source_file(spec.config_path)
        return cls(strategy, None if config.path == strategy.path else config)


def _git(*args: str) -> str | None:
    """Return git's stdout in this module's checkout; None when git is absent or refuses."""
    try:
        found = subprocess.run(  # noqa: S603 -- fixed git arguments, no user input
            ["git", *args],  # noqa: S607
            # The checkout this module lives in, whatever the caller's cwd.
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # no git binary (the slim images `make test` runs the notebooks in)
        return None
    return found.stdout.strip() if found.returncode == 0 else None


def git_revision() -> str:
    """
    Return the checkout's revision, `-dirty` when its tree has uncommitted changes (as
    `verification.tools.cut_snapshot_fixtures` records its own), `unknown` when there is no git
    or no checkout, and `-unknown-status` when git names the commit but cannot say whether the tree
    is clean -- never a clean-looking revision it could not check.
    """
    revision = _git("rev-parse", "--short=10", "HEAD")
    if not revision:
        return "unknown"
    status = _git("status", "--porcelain")
    if status is None:
        return f"{revision}-unknown-status"
    return f"{revision}-dirty" if status else revision


def environment() -> dict[str, str]:
    return {
        "git_revision": git_revision(),
        "nautilus_trader": nautilus_trader.__version__,
        "python": python_platform.python_version(),
    }


# -- the tearsheet ----------------------------------------------------------------------------


def _currency(result: RunResult) -> str:
    """
    Return the run's one PnL currency (`NodeRunner` runs one settlement currency per run); a
    result with several raises rather than label one balance with all of them.
    """
    currencies = list(result.nautilus_stats["pnls"])
    if len(currencies) > 1:
        raise ValueError(f"one PnL currency per report, got {currencies}")
    return currencies[0] if currencies else "unknown (no PnL statistics)"


def _latency_text(spec: RunSpec) -> str:
    if spec.latency is not None:
        return json.dumps(dict(spec.latency), sort_keys=True)
    if spec.latency_ms == 0:
        return "none"
    return f"{spec.latency_ms} ms fixed, every order command"


def _model_text(model: Mapping[str, object] | None) -> str:
    return "venue default" if model is None else json.dumps(_json_value(model, "model"))


def run_configuration(
    result: RunResult,
    spec: RunSpec,
    strategy: SourceFile,
    env: Mapping[str, str],
    benchmark: Benchmark,
) -> list[tuple[str, str]]:
    """Return the "Run Configuration" panel's rows: the spec, its models, ids and code version."""
    currency = _currency(result)
    rows = [
        ("Strategy", spec.strategy_path),
        ("Strategy config", spec.config_path),
        ("Strategy file sha256", strategy.sha256),
        ("Instruments", ", ".join(spec.instrument_ids)),
        ("Window", f"{spec.start} -> {spec.end}"),
        ("Data", spec.data),
        ("Starting balance", f"{spec.starting_balance} {currency}"),
        ("Latency", _latency_text(spec)),
        ("Fill model", _model_text(spec.fill_model)),
        ("Fee model", _model_text(spec.fee_model)),
        ("Exec algorithms", ", ".join(spec.exec_algorithms) or "none"),
    ]
    rows += [
        (f"Param {name}", json.dumps(_json_value(value, f"params.{name}")))
        for name, value in sorted(spec.params.items())
    ]
    rows += [
        ("Config ID", result.config_id),
        ("Instance ID", result.instance_id or "unknown"),
        ("Benchmark", benchmark.describe()),
        ("Git revision", env["git_revision"]),
        ("NautilusTrader", env["nautilus_trader"]),
        ("Python", env["python"]),
    ]
    return rows


def _pnl_charts(result: RunResult) -> list[TearsheetChart]:
    hours = pnl_by_hour_frame(result)
    weekdays = pnl_by_weekday_frame(result)
    none = " -- no closed trade" if not len(result.trades) else ""
    return [
        TearsheetCustomChart(
            chart=PNL_BY_HOUR_CHART,
            title=PNL_BY_HOUR_TITLE + none,
            args={
                "labels": [f"{h:02d}" for h in hours.index],
                "values": hours["pnl"].tolist(),
                "axis_title": "Exit hour (UTC)",
            },
        ),
        TearsheetCustomChart(
            chart=PNL_BY_WEEKDAY_CHART,
            title=PNL_BY_WEEKDAY_TITLE + none,
            args={
                "labels": weekdays["day"].tolist(),
                "values": weekdays["pnl"].tolist(),
                "axis_title": "Exit weekday (UTC)",
            },
        ),
    ]


def _layout(count: int) -> tuple[GridLayout, int]:
    """
    Two columns, one row per pair of panels: the two table rows taller. Explicit, because
    Nautilus's automatic layout has room for eight panels and drops the rest.
    """
    rows = math.ceil(count / 2)
    units = [3.0, 3.0, *([1.2] * (rows - 2))]
    layout = GridLayout(
        rows=rows,
        cols=2,
        heights=[u / sum(units) for u in units],
        vertical_spacing=0.25 / rows,
        horizontal_spacing=0.08,
    )
    return layout, int(300 * sum(units))


def tearsheet_config(
    result: RunResult, run_rows: Sequence[tuple[str, str]], panels: Sequence[BarsPanel]
) -> TearsheetConfig:
    """Nautilus's panels, the run configuration, the PnL buckets and the bars panels, in order."""
    charts: list[TearsheetChart] = [
        TearsheetRunInfoChart(),
        TearsheetStatsTableChart(),
        TearsheetCustomChart(chart=RUN_CONFIG_CHART, args={"rows": [list(r) for r in run_rows]}),
        TearsheetEquityChart(),
        TearsheetDrawdownChart(),
        TearsheetMonthlyReturnsChart(),
        TearsheetDistributionChart(),
        TearsheetRollingSharpeChart(),
        TearsheetYearlyReturnsChart(),
        *_pnl_charts(result),
        *(TearsheetBarsWithFillsChart(bar_type=p.bar_type, title=p.title) for p in panels),
    ]
    unknown = [c.name for c in charts if c.name not in nautilus_tearsheet._TEARSHEET_CHART_SPECS]
    if unknown:
        # The tearsheet would draw these as empty panels without a word (the module Known limit).
        raise RuntimeError(f"Nautilus's tearsheet cannot render the panels {unknown}")
    layout, height = _layout(len(charts))
    return TearsheetConfig(charts=charts, layout=layout, height=height)


def default_title(result: RunResult, spec: RunSpec) -> str:
    cls = spec.strategy_path.rpartition(":")[2]
    return (
        f"<b>{cls}</b> backtest<br><sub>{', '.join(spec.instrument_ids)} | {spec.data} | "
        f"{spec.start} -> {spec.end} | config {result.config_id[:12]}</sub>"
    )


def _offline_info(result: RunResult, spec: RunSpec) -> tuple[dict[str, str], dict[str, str]]:
    """Return the run and account tables without an engine, from what the `RunResult` kept."""
    run_info = {
        "Run ID": result.run_id or "unknown",
        "Iterations": f"{result.iterations:_}",
        "Engine time": f"{result.wall_seconds:.3f} s",
        "Total orders": f"{len(result.orders):_}",
        "Total fills": f"{len(result.fills):_}",
        "Closed trades": f"{len(result.trades):_}",
    }
    currency = _currency(result)
    account_info = {
        f"Starting balance ({currency})": f"{spec.starting_balance:_}",
        f"Ending balance ({currency})": f"{result.equity.values[-1]:_.8f}",  # noqa: PD011 -- numpy
    }
    return run_info, account_info


def tearsheet_html(
    result: RunResult,
    spec: RunSpec,
    config: TearsheetConfig,
    title: str,
    engine: BacktestEngine | None,
    benchmark: Benchmark,
) -> str:
    """Nautilus's tearsheet as HTML: engine-driven when the engine is alive, else from stats."""
    if engine is not None:
        html = create_tearsheet(
            engine,
            output_path=None,
            title=title,
            config=config,
            benchmark_returns=benchmark.returns,
            benchmark_name=_BENCHMARK_NAME,
        )
    else:
        run_info, account_info = _offline_info(result, spec)
        html = create_tearsheet_from_stats(
            stats_pnls=result.nautilus_stats["pnls"],
            stats_returns=result.nautilus_stats["returns"],
            stats_general=result.nautilus_stats["general"],
            returns=result.returns,
            output_path=None,
            title=title,
            config=config,
            run_info=run_info,
            account_info=account_info,
        )
    if not isinstance(html, str):
        raise RuntimeError("Nautilus's tearsheet returned no HTML")
    return html


# -- the record and the index -----------------------------------------------------------------


def _benchmark_stats(
    engine: BacktestEngine | None, result: RunResult, benchmark: Benchmark
) -> dict[str, object] | None:
    """
    Nautilus's benchmark-relative statistics against the run's returns, as the engine-driven
    tearsheet adds them to its table; None without a benchmark or on a Nautilus before 1.229
    (no such method: the `EXTRA_STATISTICS` Known limit of `backtest_runner`).
    """
    if engine is None or benchmark.returns is None:
        return None
    analyzer = engine.portfolio.analyzer
    versus = getattr(analyzer, "get_performance_stats_returns_vs_benchmark", None)
    if versus is None:
        return None
    return _stats(versus(benchmark.returns, returns=result.returns), "benchmark.stats")


def build_record(
    result: RunResult,
    spec: RunSpec,
    *,
    folder: str,
    title: str,
    saved_at: datetime,
    strategy: SourceFile,
    config_source: SourceFile | None,
    env: Mapping[str, str],
    benchmark: Benchmark,
    benchmark_stats: object,
    panels: Sequence[BarsPanel],
) -> dict[str, object]:
    """Everything the run computed, as one JSON object (`load_backtest_record` reads it back)."""
    record: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "folder": folder,
        "title": title,
        "saved_at": saved_at.isoformat(),
        "strategy": {
            "class": strategy.cls_name,
            "source": str(strategy.path),
            "sha256": strategy.sha256,
            "config_source": None if config_source is None else str(config_source.path),
            "config_sha256": None if config_source is None else config_source.sha256,
        },
        "spec": spec_record(spec),
        "run": {
            "config_id": result.config_id,
            "run_id": result.run_id,
            "instance_id": result.instance_id,
            "iterations": result.iterations,
            "wall_seconds": result.wall_seconds,
            "closed_trades": len(result.trades),
            "orders": len(result.orders),
            "fills": len(result.fills),
        },
        "params": dict(result.params),
        "metrics": asdict(result.metrics),
        "nautilus_stats": _nautilus_stats(result),
        "returns": _series_record(result.returns),
        "trades": [asdict(t) for t in result.trades.trades],
        "equity": {
            "ts_ns": result.equity.ts_ns.tolist(),
            "values": result.equity.values.tolist(),  # noqa: PD011 -- numpy
            "starting_balance": result.equity.starting_balance,
        },
        "pnl_by_day": result.pnl_by_day,
        "pnl_by_hour_utc": {f"{h:02d}": v for h, v in result.pnl_by_hour_of_day().items()},
        "pnl_by_weekday": {str(d): v for d, v in result.pnl_by_weekday().items()},
        "benchmark": {**benchmark.as_record(), "stats": benchmark_stats},
        "bars_panels": [asdict(p) for p in panels],
        "environment": dict(env),
        "files": {
            "tearsheet": TEARSHEET_FILE,
            "strategy": STRATEGY_FILE,
            "strategy_config": None if config_source is None else STRATEGY_CONFIG_FILE,
        },
    }
    checked = _json_value(record, "record")
    assert isinstance(checked, dict)  # a dict in, a dict out
    return checked


def index_line(record: Mapping[str, Any]) -> dict[str, object]:
    """Return the record's `index.jsonl` summary: where, what, when, and the metrics to rank by."""
    spec = record["spec"]
    return {
        "schema": REPORT_SCHEMA,
        "folder": record["folder"],
        "saved_at": record["saved_at"],
        "strategy": record["strategy"]["class"],
        "strategy_path": spec["strategy_path"],
        "config_id": record["run"]["config_id"],
        "run_id": record["run"]["run_id"],
        "instruments": spec["instrument_ids"],
        "data": spec["data"],
        "start": spec["start"],
        "end": spec["end"],
        "closed_trades": record["run"]["closed_trades"],
        "metrics": record["metrics"],
        "pnl_total": {
            currency: _pnl_total(stats, currency)
            for currency, stats in record["nautilus_stats"]["pnls"].items()
        },
        "git_revision": record["environment"]["git_revision"],
    }


def _pnl_total(stats: Mapping[str, object], currency: str) -> object:
    """Nautilus's `PnL (total)`; a Nautilus that renamed it fails the save, never a silent None."""
    if _PNL_TOTAL not in stats:
        raise KeyError(
            f"Nautilus's {currency} PnL statistics have no {_PNL_TOTAL!r}: {sorted(stats)}"
        )
    return stats[_PNL_TOTAL]


def _dumps(value: object) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` durably and atomically (temp, fsync, rename): never a torn file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _append_index(root: Path, line: Mapping[str, object]) -> None:
    """
    Append one line in one `write` on an `O_APPEND` descriptor (so two processes never interleave
    inside a line); any failure truncates the index back to its size before, so it never names a
    folder the failed save removes, nor keeps a torn line.
    """
    data = (_dumps(line) + "\n").encode("utf-8")
    fd = os.open(root / INDEX_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    size = os.fstat(fd).st_size
    try:
        if os.write(fd, data) != len(data):
            raise OSError(f"short write to {root / INDEX_FILE}")
        os.fsync(fd)
        _fsync_dir(root)
    except BaseException:
        os.ftruncate(fd, size)
        raise
    finally:
        os.close(fd)


def _new_folder(root: Path, cls_name: str, saved_at: datetime) -> Path:
    """Create `<cls>_<UTC time>` exclusively; `-2`, `-3`, ... while that name is taken."""
    root.mkdir(parents=True, exist_ok=True)
    base = f"{cls_name}_{saved_at:%Y-%m-%dT%H-%M-%SZ}"
    for n in itertools.count(1):
        folder = root / (base if n == 1 else f"{base}-{n}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder
    raise AssertionError("unreachable")


def _check_root(root: Path, spec: RunSpec) -> None:
    """Refuse a root inside the archive: reports sit beside the catalog, never in it."""
    catalog = Path(spec.catalog_path).resolve()
    if root.resolve().is_relative_to(catalog):
        raise ValueError(f"reports root {root} is inside the catalog {catalog}: keep it beside")


def check_report(spec: RunSpec, root: str | Path) -> StrategySource:
    """
    Check before a run that its report can be saved, and read the strategy files: the root is
    beside the catalog and writable, the spec (its params included) is plain JSON, and both
    classes have a source file. `NodeRunner` calls it per grid point before `node.build()`, so a
    report that could never be written refuses the run up front instead of discarding it after.
    """
    root = Path(root)
    _check_root(root, spec)
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK):
        raise PermissionError(f"reports root {root} is not writable")
    spec_record(spec)
    return StrategySource.read(spec)


# -- the API ----------------------------------------------------------------------------------


def save_backtest_report(
    result: RunResult,
    spec: RunSpec,
    root: str | Path,
    *,
    engine: BacktestEngine | None = None,
    title: str | None = None,
    bar_capacity: int = DEFAULT_BAR_CAPACITY,
    source: StrategySource | None = None,
) -> Path:
    """
    Save `result` (the run of `spec`) as a new report folder under `root` and return its path.

    `engine` is the run's own `BacktestEngine`, not yet disposed (`NodeRunner(report_root=...)`
    passes it): the tearsheet is then Nautilus's engine-driven one with the bars panels and the
    benchmark, and `bar_capacity` is that engine's `CacheConfig.bar_capacity`. Without it the
    tearsheet is built from the result's statistics and returns. `title` replaces the default
    (strategy, instruments, data, window, config id). `source` is the strategy files as
    `check_report` read them when the run started; without it they are read now. `spec.params`
    must be the run's own (a sweep point's merged params, as `NodeRunner` passes it). Any failure
    raises and leaves no folder and no index line.
    """
    root = Path(root)
    source = source or check_report(spec, root)
    _check_root(root, spec)
    strategy, config_source = source.strategy, source.config
    env = environment()
    panels = [] if engine is None else bars_panels(engine, spec, bar_capacity)
    benchmark = benchmark_of(engine, spec, panels)
    title = title or default_title(result, spec)
    saved_at = datetime.now(UTC).replace(microsecond=0)
    folder = _new_folder(root, strategy.cls_name, saved_at)
    try:
        record = build_record(
            result,
            spec,
            folder=folder.name,
            title=title,
            saved_at=saved_at,
            strategy=strategy,
            config_source=config_source,
            env=env,
            benchmark=benchmark,
            benchmark_stats=_benchmark_stats(engine, result, benchmark),
            panels=panels,
        )
        run_rows = run_configuration(result, spec, strategy, env, benchmark)
        config = tearsheet_config(result, run_rows, panels)
        html = tearsheet_html(result, spec, config, title, engine, benchmark)
        _write_atomic(folder / TEARSHEET_FILE, html.encode("utf-8"))
        _write_atomic(folder / STRATEGY_FILE, strategy.content)
        if config_source is not None:
            _write_atomic(folder / STRATEGY_CONFIG_FILE, config_source.content)
        text = json.dumps(record, allow_nan=False, indent=2, sort_keys=True) + "\n"
        _write_atomic(folder / RECORD_FILE, text.encode("utf-8"))
        _fsync_dir(folder)
        _append_index(root, index_line(record))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def _refuse_constant(constant: str) -> object:
    raise ValueError(f"{constant} is not a finite JSON number")


def load_backtest_record(path: str | Path) -> dict[str, Any]:
    """
    Read one report's `record.json` (`path` is the report folder or the file itself); a record of
    another schema version, or one holding NaN/Infinity, raises `ValueError`.
    """
    path = Path(path)
    file = path / RECORD_FILE if path.is_dir() else path
    record = json.loads(file.read_text(encoding="utf-8"), parse_constant=_refuse_constant)
    if not isinstance(record, dict) or record.get("schema") != REPORT_SCHEMA:
        raise ValueError(f"{file}: not a schema-{REPORT_SCHEMA} backtest record")
    return record


def list_backtest_reports(root: str | Path) -> pd.DataFrame:
    """
    One row per saved report, in save order, from `<root>/index.jsonl`: `folder` (the report's
    full path), `saved_at`, `strategy`, `strategy_path`, `config_id`, `run_id`, `instruments`,
    `data`, `start`, `end`, `closed_trades`, `git_revision`, one `metrics.<name>` column per
    `MetricReport` field (NaN where undefined) and one `pnl_total.<currency>` column per
    currency. A root with no report yet gives the empty frame; a root that does not exist, or a
    line that is not a schema-1 JSON object, raises (never skipped).
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"no reports root at {root}")
    index = root / INDEX_FILE
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    rows = []
    for number, text in enumerate(lines, start=1):
        row = json.loads(text, parse_constant=_refuse_constant)
        if not isinstance(row, dict) or row.get("schema") != REPORT_SCHEMA:
            raise ValueError(f"{index}:{number}: not a schema-{REPORT_SCHEMA} index line")
        rows.append({**row, "folder": str(root / row["folder"])})
    if not rows:
        return pd.DataFrame(
            columns=[*INDEX_COLUMNS, *(f"metrics.{m}" for m in MetricReport.field_names())]
        )
    frame = pd.json_normalize(rows).drop(columns="schema")
    # An undefined metric is NaN in a numeric column (None in the JSON), never 0 and never object.
    numeric = [c for c in frame.columns if c.startswith(("metrics.", "pnl_total."))]
    return frame.astype(dict.fromkeys(numeric, "float64"))
