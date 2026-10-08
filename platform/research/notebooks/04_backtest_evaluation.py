# %% [raw]
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

# %% [markdown]
# # 04 Backtest evaluation, parameter sweeps and walk-forward
#
# The one standard page every strategy is judged on: a single backtest's equity with its
# underwater series and drawdown episodes, the portfolio statistics, a rolling Sharpe, the trade
# distributions (PnL, holding time, PnL by UTC hour and weekday) and the trade list; then a
# two-parameter sweep drawn as a heatmap with its best runs; then a walk-forward that picks each
# fold's parameters in-sample and reports the joined out-of-sample result next to the in-sample
# one.
#
# Every backtest runs through the `BacktestRunner` port (`research.application.backtest_runner
# .NodeRunner`: Nautilus's backtest node fed by `BacktestDataConfig`, the strategy by string
# path); this notebook never builds a node or reads the catalog itself. Every number is a
# `RunResult` field, a `research.domain` value or a `research.application.evaluation` /
# `walk_forward` frame: the statistics are `MetricReport`'s (exactly
# `kernel.performance_metrics.all_metrics`), so no cell sums, averages or subtracts a PnL.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `INSTRUMENTS`,
# `START`, `END` and `BACKTEST_REPORTS_DIR` (where the single run's report is saved). The rest
# are set here; the ones marked *setting* can also be given as JSON in `NOTEBOOK_<NAME>`
# (`_params.setting`), which is how the test harness shrinks them to its ten-minute fixture:
#
# - `STRATEGY`, `STRATEGY_CONFIG` -- the strategy and its config class by string path
#   (`research.strategies.<module>:<Class>`). The first worked example is `OFIStrategy` on 1 s
#   snapshots; the second, commented out under it, is `CandlePatternStrategy` (Story 27.8) on the
#   trade archive -- `HAMMER`/`ENGULFING` entries above the trend EMA, 1-minute bars aggregated
#   from the trades, its grid over `exit_bars` x `stop_atr_multiple`. Swap the two blocks to
#   evaluate it (a candlestick scanner hit is one config away from this page);
# - `INSTRUMENT` (*setting*) -- the instrument backtested, by default the first of `INSTRUMENTS`;
# - `PARAMS` (*setting*) -- the strategy parameters of the single run, and the base every grid
#   point is merged over;
# - `DATA` -- the feed (`"seconds"`: 1 s snapshots with quotes from their top of book; `"trades"`;
#   `"bars:1-MINUTE"`);
# - `STARTING_BALANCE` -- in the instrument's settlement currency;
# - `ROLLING_PERIOD_S`, `ROLLING_WINDOW` -- the rolling Sharpe's return period and its window in
#   periods (48 x 1 h: Nautilus bins returns into UTC days, so a window must span two days);
# - `GRID` (*setting*) -- the sweep, two parameters (the heatmap's axes), every combination run;
# - `N_FOLDS` (*setting*), `IN_SAMPLE_FRACTION` -- the walk-forward folds, split here
#   (`FOLDS`) so a window too short for them fails before any backtest runs;
# - `SELECT_BY` (*setting*) -- the `MetricReport` field the heatmap shows and each fold's pick
#   maximises (every statistic is higher-is-better; `max_drawdown` is negative). The default is
#   `expectancy`, defined on any closed trade: the return statistics (Sharpe, Sortino, Calmar,
#   max drawdown) need realized PnL on two UTC days *inside each in-sample window*, so pick one
#   only once `START`-`END` gives every fold several days;
# - `TOP_N` -- how many of the sweep's best runs to list.

# %%
import plotly.graph_objects as go
from _params import Params
from _params import setting
from plotly.subplots import make_subplots
from research.application import evaluation
from research.application.backtest_runner import NodeRunner
from research.application.inspection import plot_axis
from research.application.ports import RunSpec
from research.application.ports import window_ns
from research.application.walk_forward import folds
from research.application.walk_forward import walk_forward


params = Params.from_env()
STRATEGY = "research.strategies.ofi_strategy:OFIStrategy"
STRATEGY_CONFIG = "research.strategies.ofi_strategy:OFIStrategyConfig"
INSTRUMENT = setting("INSTRUMENT", params.instruments[0])
PARAMS = setting("PARAMS", {"trade_size": "0.01", "ofi_threshold": 2.0, "warmup_seconds": 600})
DATA = "seconds"
STARTING_BALANCE = 10_000
ROLLING_PERIOD_S = 3_600
ROLLING_WINDOW = 48
GRID = setting("GRID", {"ofi_threshold": [1.5, 2.0], "ofi_window": [20, 40]})
# Second worked example -- candlestick patterns on the trade archive (Story 27.8):
# STRATEGY = "research.strategies.candle_pattern_strategy:CandlePatternStrategy"
# STRATEGY_CONFIG = "research.strategies.candle_pattern_strategy:CandlePatternStrategyConfig"
# PARAMS = setting(
#     "PARAMS",
#     {"trade_size": "0.01", "long_patterns": ["HAMMER", "ENGULFING"], "trend_condition": "above"},
# )
# DATA = "trades"
# GRID = setting("GRID", {"exit_bars": [5, 10], "stop_atr_multiple": [1.5, 2.0]})
N_FOLDS = setting("N_FOLDS", 3)
IN_SAMPLE_FRACTION = 0.7
SELECT_BY = setting("SELECT_BY", "expectancy")
TOP_N = 5
evaluation.check_heatmap_sweep(GRID, SELECT_BY)
runner = NodeRunner()
# The single run is saved as a report; the sweep and walk-forward runs are not.
report_runner = NodeRunner(report_root=params.backtest_reports_dir)
spec = RunSpec(
    catalog_path=params.catalog_path,
    instrument_ids=(INSTRUMENT,),
    start=params.start,
    end=params.end,
    strategy_path=STRATEGY,
    config_path=STRATEGY_CONFIG,
    params=PARAMS,
    starting_balance=STARTING_BALANCE,
    data=DATA,
)
start_ns, end_ns = window_ns(params.start, params.end)
FOLDS = folds(start_ns, end_ns, N_FOLDS, IN_SAMPLE_FRACTION)
print(spec)
print(f"grid {GRID}; {N_FOLDS} folds, in-sample fraction {IN_SAMPLE_FRACTION}, by {SELECT_BY}")

# %% [markdown]
# ## 2. Single run
#
# One `RunResult` for `PARAMS` over `START`-`END`: the closed trades (`TradeLedger`), the account
# equity after every account event (`EquityCurve`) and the `MetricReport`, all attributed to the
# run's config id. The equity is the account balance: realized PnL and every fee, not marked to
# market (the `RunResult` Known limit), so an adverse move held open does not show until it closes.
#
# The run is saved as a report folder under `BACKTEST_REPORTS_DIR`
# (`research.application.backtest_report`): Nautilus's tearsheet, the strategy's source file and
# `record.json`; the sweep and walk-forward runs below are compared here and not saved.

# %%
result = report_runner.run(spec)
print(f"saved to {result.report_dir}")
print(
    f"config {result.config_id}: {len(result.trades)} closed trades, {len(result.equity)} equity "
    f"points, {result.iterations} data events, engine {result.wall_seconds:.2f} s"
)

# %% [markdown]
# ## 3. Equity, underwater and drawdown episodes
#
# **Derived:** the underwater series `equity / running peak - 1` (`EquityCurve.underwater`; the
# peak starts at `STARTING_BALANCE`) and every drawdown episode (`EquityCurve.drawdowns`): its peak
# (blank while the peak is the starting balance), trough, recovery (blank if the run ends under
# water) and depth as a fraction of the peak.

# %%
equity = evaluation.equity_frame(result.equity)
equity_fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True, subplot_titles=["equity", "underwater (fraction of peak)"]
)
equity_fig.add_trace(
    go.Scatter(x=plot_axis(equity.index), y=equity["equity"], name="equity", line_shape="hv"), 1, 1
)
equity_fig.add_trace(
    go.Scatter(
        x=plot_axis(equity.index),
        y=equity["underwater"],
        name="underwater",
        line_shape="hv",
        fill="tozeroy",
    ),
    2,
    1,
)
equity_fig.update_layout(title=f"{INSTRUMENT}: equity and underwater", height=600)
equity_fig.show()
episodes = evaluation.drawdown_frame(result.equity)
print(evaluation.no_drawdown_note(episodes) or episodes.to_string())

# %% [markdown]
# ## 4. Portfolio statistics
#
# `MetricReport.as_table()`: exactly the keys of `kernel.performance_metrics.all_metrics` (win
# rate, expectancy, average and largest win and loss, Sharpe, Sortino, Calmar, max drawdown,
# profit factor), nothing added. The return statistics are over realized PnL per UTC day of exit,
# so a run shorter than two days leaves them None.

# %%
metric_table = evaluation.metric_frame(result.metrics)
print(metric_table.to_string())

# %% [markdown]
# ## 5. Rolling Sharpe
#
# **Derived:** the equity's returns per `ROLLING_PERIOD_S` bucket (`ReturnSeries.from_equity`: the
# last equity at or before each bucket's end) and the Sharpe of each trailing `ROLLING_WINDOW`
# returns (`ReturnSeries.rolling_sharpe`, Nautilus's `SharpeRatio`, daily-binned). A curve shorter
# than the window prints why nothing is drawn.

# %%
rolling = evaluation.rolling_sharpe_frame(result.equity, ROLLING_PERIOD_S, ROLLING_WINDOW)
rolling_note = evaluation.rolling_sharpe_note(rolling, ROLLING_PERIOD_S, ROLLING_WINDOW)
if rolling_note:
    print(rolling_note)
else:
    rolling_fig = go.Figure(
        go.Scatter(x=plot_axis(rolling.index), y=rolling["rolling_sharpe"], connectgaps=False)
    )
    rolling_fig.update_layout(
        title=f"Rolling Sharpe, {ROLLING_WINDOW} x {ROLLING_PERIOD_S} s returns"
    )
    rolling_fig.show()

# %% [markdown]
# ## 6. Trades
#
# Per closed trade (`TradeLedger`): the realized PnL (net of fees) and holding-time distributions,
# the PnL by UTC hour and by UTC weekday of exit (`RunResult.pnl_by_hour_of_day` /
# `pnl_by_weekday`: only the buckets with an exit, never a 0 for an empty one) and the trade list.

# %%
trades = evaluation.trade_frame(result.trades)
trades_note = evaluation.no_trades_note(result.trades)
if trades_note:
    print(trades_note)
else:
    by_hour = evaluation.pnl_by_hour_frame(result)
    by_weekday = evaluation.pnl_by_weekday_frame(result)
    trade_fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[
            "realized PnL per trade",
            "holding time (s)",
            "PnL by UTC hour of exit",
            "PnL by UTC weekday of exit",
        ],
    )
    trade_fig.add_trace(go.Histogram(x=trades["realized_pnl"], name="PnL"), 1, 1)
    trade_fig.add_trace(go.Histogram(x=trades["holding_s"], name="holding"), 1, 2)
    trade_fig.add_trace(go.Bar(x=by_hour.index, y=by_hour["pnl"], name="by hour"), 2, 1)
    trade_fig.add_trace(go.Bar(x=by_weekday["day"], y=by_weekday["pnl"], name="by weekday"), 2, 2)
    trade_fig.update_layout(title=f"{INSTRUMENT}: {len(trades)} closed trades", height=700)
    trade_fig.show()
    print(trades.to_string())

# %% [markdown]
# ## 7. Parameter sweep
#
# Every combination of `GRID` merged over `PARAMS`, run as one sweep (`NodeRunner.sweep`: one node,
# one run config per point, each result matched by its config id). The heatmap shows `SELECT_BY`
# per point (blank where undefined); the table lists the `TOP_N` points by it with every
# statistic. The runtime covers loading, the node and every run. Memory: the runner loads the
# window once per grid point (its `chunk_size` Known limit), so a sweep's cost grows with the grid.
# The sweep is fitted on the whole `START`-`END` window, every out-of-sample stretch of §8
# included: its best cell is picked with hindsight, an in-sample number by construction.

# %%
sweep = evaluation.timed_sweep(runner, spec, GRID)
print(f"{len(sweep.results)} runs over the grid {GRID} in {sweep.elapsed_s:.1f} s")
heat = evaluation.metric_grid(sweep, SELECT_BY)
heat_note = evaluation.metric_grid_note(heat, SELECT_BY)
if heat_note:
    print(heat_note)
else:
    heat_fig = go.Figure(
        go.Heatmap(
            z=heat.to_numpy(),
            x=[str(c) for c in heat.columns],
            y=[str(r) for r in heat.index],
            colorscale="Viridis",
        )
    )
    heat_fig.update_layout(
        title=f"{SELECT_BY} over the grid",
        xaxis_title=heat.columns.name,
        yaxis_title=heat.index.name,
    )
    heat_fig.show()
print(evaluation.top_runs(sweep, SELECT_BY, TOP_N).to_string())

# %% [markdown]
# ## 8. Walk-forward
#
# `START`-`END` is split into `N_FOLDS` consecutive, equal folds (`walk_forward.folds`); the first
# `IN_SAMPLE_FRACTION` of each is in-sample, the rest out-of-sample (OOS). Per fold the grid is
# swept in-sample, the point with the highest `SELECT_BY` is picked (undefined ranks last, ties
# go to the earlier point; when no point has a value the first is taken and the table shows the
# pick as None), and that point is run on the OOS window.
#
# **The OOS equity is a convention, not one continuous run:** each fold's OOS run starts flat from
# the full `STARTING_BALANCE` and its curve is shifted by the OOS PnL of the folds before it
# (`walk_forward.concat_equity`); each OOS run spends its strategy's warm-up inside its own window.
# The OOS and in-sample statistics are `MetricReport`s over all the folds' trades merged. Each
# fold's OOS equity is its own line (`evaluation.oos_equity_frame`), so the in-sample stretch
# between two OOS windows is a break, never a flat line; the single run's equity is drawn beside
# them for scale.

# %%
wf = walk_forward(runner, spec, sweep.points, FOLDS, SELECT_BY)
print(evaluation.fold_frame(wf).to_string())
for line in evaluation.fold_notes(wf):
    print(line)
print(evaluation.walk_forward_metrics_frame(wf).to_string())
oos_equity = evaluation.oos_equity_frame(wf)
wf_fig = go.Figure()
wf_fig.add_trace(
    go.Scatter(x=plot_axis(equity.index), y=equity["equity"], name="single run", line_shape="hv")
)
for fold_number, fold_equity in oos_equity.groupby("fold"):
    wf_fig.add_trace(
        go.Scatter(
            x=plot_axis(fold_equity.index),
            y=fold_equity["equity"],
            name=f"out-of-sample, fold {fold_number}",
            line_shape="hv",
        )
    )
wf_fig.update_layout(title=f"{INSTRUMENT}: joined OOS equity ({len(wf.folds)} folds) vs single run")
wf_fig.show()

# %% [markdown]
# ## 9. Reading guide
#
# - **In-sample vs out-of-sample.** The in-sample statistics are the best the grid could do on data
#   it was fitted to; the OOS ones are what the picked parameters did next. A large gap (a good
#   in-sample Sharpe, a flat or negative OOS one) means the pick fits noise: the edge did not
#   survive new data. Similar numbers on both sides are the first sign of a real effect, not proof
#   of one.
# - **The grid's best is optimistic.** The heatmap's top cell is the maximum of several noisy
#   estimates, so it overstates what that point will do: the more points tried, the higher the
#   best one scores by luck alone. A plateau of similar cells is more believable than one bright
#   cell among dark neighbours. `05_monte_carlo` quantifies this with the deflated Sharpe ratio
#   of the best grid point (and Monte Carlo resampling of the trades).
# - **Few trades, short windows.** Every statistic here rests on the closed trades: a handful of
#   trades or a window under two UTC days leaves the return statistics undefined (None) rather than
#   invented. Widen `START`/`END` before trusting a number.
# - **What the equity is not.** The equity is the account balance after fills and fees, not marked
#   to market, and the joined OOS equity is several independent runs laid end to end (§8).
