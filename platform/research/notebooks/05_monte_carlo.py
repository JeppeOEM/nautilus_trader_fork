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
# # 05 Monte Carlo and robustness
#
# A backtest is one equity path and one Sharpe ratio. This notebook asks what else the same
# record could have produced: it resamples the closed trades (their order) and the equity's
# returns (in blocks, keeping their autocorrelation) into many paths, and shows the spread of
# terminal wealth and drawdown, the chance of falling below a ruin level, a confidence interval on
# the Sharpe ratio, and whether the best point of a parameter sweep is distinguishable from luck
# once the number of points tried is accounted for (the deflated Sharpe ratio).
#
# Every backtest runs through the `BacktestRunner` port (`NodeRunner`), with the strategy and
# parameters of `04_backtest_evaluation`. Every number comes from `research.domain.monte_carlo`
# (seeded numpy resampling, each Sharpe `kernel.performance_metrics.return_stats`') or the
# `research.application.robustness` frames; no cell resamples, sums or averages. Every figure's
# title states its seed and path count, so the same `SEED` and `N_PATHS` redraw it exactly.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `INSTRUMENTS`,
# `START`, `END`. The rest are set here; the ones marked *setting* can also be given as JSON in
# `NOTEBOOK_<NAME>` (`_params.setting`), which is how the test harness shrinks them to its
# ten-minute fixture:
#
# - `STRATEGY`, `STRATEGY_CONFIG`, `INSTRUMENT` (*setting*), `PARAMS` (*setting*), `DATA`,
#   `STARTING_BALANCE`, `GRID` (*setting*) -- as in `04_backtest_evaluation`;
# - `N_PATHS` (*setting*) -- resampled paths per bootstrap;
# - `SEED` (*setting*) -- the one seed every bootstrap draws from;
# - `RETURN_PERIOD_S` (*setting*) -- the equity's return period for the block bootstrap and the
#   Sharpe interval (the Sharpe itself is Nautilus's, over UTC-daily bins of these returns);
# - `BLOCK_LEN` (*setting*) -- the stationary bootstrap's mean block length, in periods (24 x 1 h
#   keeps a day of dependence together);
# - `RUIN_LEVELS` (*setting*) -- equity floors as fractions of `STARTING_BALANCE`;
# - `CONFIDENCE` -- the Sharpe interval's level and the deflated Sharpe's bar;
# - `SWEEP` (*setting*) -- also run `GRID` for the deflated Sharpe of its best point;
# - `FAN_QUANTILES` -- the fan chart's bands.

# %%
import plotly.graph_objects as go
from _params import Params
from _params import setting
from plotly.subplots import make_subplots
from research.application import evaluation
from research.application import robustness
from research.application.backtest_runner import NodeRunner
from research.application.ports import RunSpec
from research.domain.monte_carlo import block_bootstrap_returns
from research.domain.monte_carlo import bootstrap_trades
from research.domain.monte_carlo import sharpe_confidence_interval
from research.domain.returns import ReturnSeries


params = Params.from_env()
STRATEGY = "research.strategies.ofi_strategy:OFIStrategy"
STRATEGY_CONFIG = "research.strategies.ofi_strategy:OFIStrategyConfig"
INSTRUMENT = setting("INSTRUMENT", params.instruments[0])
PARAMS = setting("PARAMS", {"trade_size": "0.01", "ofi_threshold": 2.0, "warmup_seconds": 600})
DATA = "seconds"
STARTING_BALANCE = 10_000
GRID = setting("GRID", {"ofi_threshold": [1.5, 2.0], "ofi_window": [20, 40]})
N_PATHS = setting("N_PATHS", 2_000)
SEED = setting("SEED", 7)
RETURN_PERIOD_S = setting("RETURN_PERIOD_S", 3_600)
BLOCK_LEN = setting("BLOCK_LEN", 24)
RUIN_LEVELS = setting("RUIN_LEVELS", [0.9, 0.75, 0.5])
CONFIDENCE = 0.95
SWEEP = setting("SWEEP", True)
FAN_QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
evaluation.grid_points(GRID)
runner = NodeRunner()
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
print(spec)
print(f"{N_PATHS} paths, seed {SEED}; returns every {RETURN_PERIOD_S} s, mean block {BLOCK_LEN}")

# %% [markdown]
# ## 2. Run
#
# One `RunResult` for `PARAMS` over `START`-`END` (closed trades, account equity, `MetricReport`)
# and, with `SWEEP`, every point of `GRID` (`evaluation.timed_sweep`, one node, results matched by
# config id). The equity's returns per `RETURN_PERIOD_S` (`ReturnSeries.from_equity`) feed the
# block bootstrap.

# %%
result = runner.run(spec)
returns = ReturnSeries.from_equity(result.equity, RETURN_PERIOD_S)
print(
    f"config {result.config_id}: {len(result.trades)} closed trades, {len(result.equity)} equity "
    f"points, {len(returns)} returns of {RETURN_PERIOD_S} s"
)
sweep = evaluation.timed_sweep(runner, spec, GRID) if SWEEP else None
if sweep is None:
    print("SWEEP is off: no grid was run, so §8 has no best point to deflate.")
else:
    print(f"{len(sweep.results)} runs over the grid {GRID} in {sweep.elapsed_s:.1f} s")

# %% [markdown]
# ## 3. Trade-order bootstrap
#
# **Derived:** `bootstrap_trades` draws each path's trades from the run's closed trades with
# replacement (same count), so a path is one other order, and mix, in which the same edge could
# have arrived; its equity is `STARTING_BALANCE` plus the running PnL, step 0 being the starting
# balance. The fan shows the paths' quantiles at every trade; the observed path is the run's own.

# %%
trade_mc = bootstrap_trades(result.trades, N_PATHS, SEED, STARTING_BALANCE)
trade_fan = robustness.fan_frame(trade_mc, FAN_QUANTILES)
if trade_mc.note:
    print(trade_mc.note)
else:
    trade_fan_fig = go.Figure()
    for quantile in FAN_QUANTILES:
        trade_fan_fig.add_trace(
            go.Scatter(
                x=trade_fan.index,
                y=trade_fan[quantile],
                name=f"q{quantile:g}",
                line={"width": 1},
                fill=None if quantile == FAN_QUANTILES[0] else "tonexty",
            )
        )
    trade_fan_fig.add_trace(
        go.Scatter(x=trade_fan.index, y=trade_fan["observed"], name="observed", line={"width": 3})
    )
    trade_fan_fig.update_layout(
        title=robustness.mc_title(f"{INSTRUMENT}: trade-order bootstrap fan", trade_mc),
        xaxis_title="closed trades",
        yaxis_title="equity",
    )
    trade_fan_fig.show()

# %% [markdown]
# ## 4. Max drawdown and terminal wealth
#
# Each path's terminal equity and max drawdown (`min(equity / running peak - 1)`, the peak
# starting at the balance: `EquityCurve.underwater`'s definition at trade granularity, **not**
# the `MetricReport` max drawdown, which Nautilus computes over daily returns), with the observed
# run's marked. An observed value in a tail means the run's order was unusually lucky or unlucky.

# %%
if trade_mc.note:
    print(trade_mc.note)
else:
    trade_hist_fig = make_subplots(
        rows=1, cols=2, subplot_titles=["terminal equity", "max drawdown"]
    )
    trade_hist_fig.add_trace(go.Histogram(x=trade_mc.terminal, name="terminal"), 1, 1)
    trade_hist_fig.add_trace(go.Histogram(x=trade_mc.max_drawdown, name="max drawdown"), 1, 2)
    trade_hist_fig.add_vline(x=trade_mc.observed_terminal, line_dash="dash", row=1, col=1)
    trade_hist_fig.add_vline(x=trade_mc.observed_max_drawdown, line_dash="dash", row=1, col=2)
    trade_hist_fig.update_layout(
        title=robustness.mc_title("Trade bootstrap: observed (dashed) vs resampled", trade_mc)
    )
    trade_hist_fig.show()
observed_sharpe = (
    "undefined" if trade_mc.observed_sharpe is None else f"{trade_mc.observed_sharpe:.3f}"
)
print(
    f"observed: terminal {trade_mc.observed_terminal:.2f}, max drawdown "
    f"{trade_mc.observed_max_drawdown:.4f}, Sharpe {observed_sharpe}"
)

# %% [markdown]
# ## 5. Block bootstrap of returns
#
# **Derived:** `block_bootstrap_returns` resamples the equity's `RETURN_PERIOD_S` returns in
# blocks of random (geometric) length with mean `BLOCK_LEN`, wrapping at the end (Politis & Romano's
# stationary bootstrap), and compounds each path from `STARTING_BALANCE`. A gap in the returns is
# dropped. **The difference from §3:** the trade bootstrap treats trades as independent and asks
# what their order did; the block bootstrap keeps runs of consecutive returns together, so a
# strategy whose realized losses cluster in time (volatility regimes, a losing streak) shows the
# deeper drawdowns that clustering causes, which a trade shuffle breaks apart. The equity is the
# account balance (not marked to market), so a flat stretch between fills is a run of zero returns.

# %%
return_mc = block_bootstrap_returns(returns, BLOCK_LEN, N_PATHS, SEED, STARTING_BALANCE)
return_fan = robustness.fan_frame(return_mc, FAN_QUANTILES)
if return_mc.note:
    print(return_mc.note)
else:
    return_fig = make_subplots(
        rows=2,
        cols=2,
        specs=[[{"colspan": 2}, None], [{}, {}]],
        subplot_titles=["equity fan", "terminal equity", "max drawdown"],
    )
    for quantile in FAN_QUANTILES:
        return_fig.add_trace(
            go.Scatter(
                x=return_fan.index,
                y=return_fan[quantile],
                name=f"q{quantile:g}",
                line={"width": 1},
                fill=None if quantile == FAN_QUANTILES[0] else "tonexty",
            ),
            1,
            1,
        )
    return_fig.add_trace(
        go.Scatter(
            x=return_fan.index, y=return_fan["observed"], name="observed", line={"width": 3}
        ),
        1,
        1,
    )
    return_fig.add_trace(go.Histogram(x=return_mc.terminal, name="terminal"), 2, 1)
    return_fig.add_trace(go.Histogram(x=return_mc.max_drawdown, name="max drawdown"), 2, 2)
    return_fig.add_vline(x=return_mc.observed_terminal, line_dash="dash", row=2, col=1)
    return_fig.add_vline(x=return_mc.observed_max_drawdown, line_dash="dash", row=2, col=2)
    return_fig.update_layout(
        title=robustness.mc_title(f"{INSTRUMENT}: block bootstrap of returns", return_mc),
        height=700,
    )
    return_fig.show()

# %% [markdown]
# ## 6. Risk of ruin
#
# The fraction of paths whose equity *ever* falls below each `RUIN_LEVELS` floor
# (`monte_carlo.risk_of_ruin`), for both bootstraps. Zero means no path of the `N_PATHS` did,
# not that it cannot happen: the resolution is one in `N_PATHS`.

# %%
ruin = robustness.ruin_frame(trade_mc, RUIN_LEVELS)
return_ruin = robustness.ruin_frame(return_mc, RUIN_LEVELS)
print(robustness.mc_title("Trade bootstrap", trade_mc))
print(ruin.to_string())
print(robustness.mc_title("Block bootstrap", return_mc))
print(return_ruin.to_string())

# %% [markdown]
# ## 7. Sharpe confidence interval
#
# **Derived:** the central `CONFIDENCE` interval of the block-bootstrapped Sharpe ratios
# (`monte_carlo.sharpe_confidence_interval`: the same `SEED`, `N_PATHS` and `BLOCK_LEN` as §5, so
# the histogram below is exactly the sample the interval is taken over). Each Sharpe is Nautilus's
# (annualised, over UTC-daily bins of the returns), so a window under two UTC days, or returns
# whose daily sums never differ, has none; the notebook then says so instead of drawing.

# %%
interval = sharpe_confidence_interval(returns, N_PATHS, SEED, CONFIDENCE, BLOCK_LEN)
print(robustness.interval_note(interval, CONFIDENCE))
if interval is not None:
    interval_fig = go.Figure(go.Histogram(x=return_mc.sharpe, name="bootstrapped Sharpe"))
    interval_fig.add_vline(x=interval.low, line_dash="dot")
    interval_fig.add_vline(x=interval.high, line_dash="dot")
    interval_fig.add_vline(x=return_mc.observed_sharpe, line_dash="dash")
    interval_fig.update_layout(
        title=robustness.mc_title(
            f"Sharpe ratio: observed (dashed), {CONFIDENCE:.0%} interval (dotted)", interval
        ),
        xaxis_title="annualised Sharpe",
    )
    interval_fig.show()

# %% [markdown]
# ## 8. Deflated Sharpe of the sweep's best point
#
# The grid point with the highest `MetricReport` Sharpe (`robustness.deflated_check`) is the
# maximum of `len(GRID points)` noisy estimates, so it overstates itself. The probabilistic Sharpe
# ratio (PSR) is the probability that its true Sharpe exceeds zero, given its daily returns' count,
# skewness and kurtosis; the deflated Sharpe ratio (DSR, Bailey & Lopez de Prado 2014) raises that
# bar to the Sharpe expected from the best of that many zero-edge trials. Both use the Sharpe per
# day (the annualised one divided by the square root of 365). The trial count is this grid's
# only: configurations already tried elsewhere (§7 of `04_backtest_evaluation`, earlier ideas) are
# trials too, so the DSR here is an upper bound. PSR and DSR are closed form (no resampling), so
# they are a table, not a seeded figure. No sweep, or no defined Sharpe on the grid, prints the
# reason instead.

# %%
check = None
if sweep is None:
    print("SWEEP is off: there is no grid, so no best point to deflate.")
else:
    check = robustness.deflated_check(sweep, STARTING_BALANCE)
    print(robustness.deflated_verdict(check, CONFIDENCE))
if check is not None and check.psr is not None and check.dsr is not None:
    print(
        f"config {check.config_id}: Sharpe {check.sharpe_per_day:.4f} per day over {check.n_obs} "
        f"daily returns, skew {check.skew:.3f}, kurtosis {check.kurtosis:.3f}"
    )
    deflated = robustness.deflated_frame(check)
    print(deflated.to_string())

# %% [markdown]
# ## 9. Reading guide
#
# - **The fan is not a forecast.** It resamples the one record the run made; if the record came
#   from a single regime, every path inherits it. A fan whose lower bands still end above the
#   starting balance says the result does not depend on trade order, not that it will repeat.
# - **Drawdown here is path drawdown.** It is measured at every trade (§4) or return period (§5)
#   against the running peak from the starting balance, so it is deeper than the `MetricReport`
#   max drawdown over daily returns.
# - **Trade vs block bootstrap.** Block-bootstrap drawdowns clearly deeper than the trade
#   bootstrap's *suggest* losses cluster in time, but the two differ in more than dependence: steps
#   per trade vs per return period, added PnL vs compounded returns (floored at 0), realized PnL
#   vs account balance. Treat the gap as a prompt to look at the losing streaks, and size for the
#   deeper of the two.
# - **Risk of ruin at 0 of N.** No path crossing a floor bounds the risk only below about one in
#   `N_PATHS`; raise `N_PATHS` before quoting a small probability.
# - **A wide Sharpe interval** (one spanning zero) means the window cannot tell the strategy from
#   noise, whatever the point estimate. Widen `START`/`END`.
# - **Deflated Sharpe.** A DSR under the bar means the best grid point is explained by the number
#   of points tried; a grid of similar neighbours is more credible than one bright cell. The
#   benchmark uses the zero-Sharpe standard error, not the grid's own spread of Sharpes (the
#   `expected_max_sharpe` Known limit).
# - **Closed trades only.** A position open at `END` is in no path, and a trade's loss before it
#   closed is in none either, so drawdown and ruin are understated for a strategy that rides open
#   losses (the `monte_carlo` Known limit).
