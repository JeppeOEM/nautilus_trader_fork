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

# %% [markdown]
# # 08 Strategy gallery
#
# Seventeen ready-made strategies run side by side over one window through the same
# `BacktestRunner` as `04_backtest_evaluation`. Thirteen on one instrument's bars: five of
# Nautilus's own example strategies by string path (`EMACross`, `EMACrossLongOnly`,
# `EMACrossBracket`, `BBMeanReversion` and `EMACrossTWAP` with its `TWAPExecAlgorithm`), three
# `MACrossStrategy` runs (a different moving average and exit each) and five
# `IndicatorSignalStrategy` runs (Bollinger, MACD, RSI, OBV and the fuzzy candle). Four on a Bybit
# LINEAR instrument's liquidations: `LiquidationCascadeStrategy` following and fading liquidation
# cascades, each short only and on both sides (Story 33.14; the derived quotes plus the archived
# liquidations, `data="liquidations"`). The page shows them as a leaderboard and one overlaid
# equity chart, then repeats
# one of them across every simulated-exchange model Nautilus offers (11 fill models, 3 fee models,
# three latency settings) to show how much of a result is the strategy and how much is a modelling
# choice.
#
# Every number is a `RunResult` field (`research.application.gallery`): the statistics are
# `MetricReport`'s, exactly `kernel.performance_metrics.all_metrics`, so no cell sums or averages a
# PnL. A run that raises keeps its row with the error text in the `error` column -- a failure is
# shown, never skipped.
#
# `EMACrossTrailingStop` and `VolatilityMarketMaker` are not in the gallery: they read the latest
# quote and the bars feed carries none, so they would never place an order here (the doc's
# section 3).

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `INSTRUMENTS`,
# `START`, `END`. The rest are set here; each can also be given as JSON in `NOTEBOOK_<NAME>`
# (`_params.setting`), which is how the test harness shrinks them to its ten-minute fixture:
#
# - `INSTRUMENT` -- the instrument backtested, by default the first of `INSTRUMENTS`;
# - `DATA` -- the feed; the gallery needs bars, `"bars:1-MINUTE"` (Nautilus aggregates them from
#   the trade archive);
# - `PERIODS` -- overrides of every period, multiple and threshold, `{}` for each strategy's own
#   defaults. A key is generic (`fast`, `slow`, `atr_period`, `atr_multiple`, `k`, ...) or for one
#   strategy (`"EMACross.fast"`, `"Signal.rsi.low"`, `"MACross.HULL.atr_multiple"`); the spec of
#   each row shows what it resolved to;
# - `CASCADE_INSTRUMENT` -- the instrument of the four cascade runs, by default
#   `BTCUSDT-LINEAR.BYBIT`; one without a liquidation feed (only Bybit LINEAR has one) is stated,
#   and its four runs are left out;
# - `CASCADE_PARAMS` -- overrides of any `LiquidationCascadeStrategyConfig` field for the four runs
#   (`{}` for the strategy's defaults, a 1 % stop); the mode and sides are each run's own;
# - `STARTING_BALANCE` -- in the instrument's settlement currency;
# - `AXIS_SPEC` -- the label of the gallery row §4 repeats across the execution models;
# - `SEED` -- the random seed of every fill model, so a rerun is identical.

# %%
import plotly.graph_objects as go
from _params import Params
from _params import setting
from research.application import gallery
from research.application.backtest_runner import NodeRunner


params = Params.from_env()
INSTRUMENT = setting("INSTRUMENT", params.instruments[0])
DATA = setting("DATA", "bars:1-MINUTE")
PERIODS = setting("PERIODS", {})
STARTING_BALANCE = setting("STARTING_BALANCE", 10_000)
AXIS_SPEC = setting("AXIS_SPEC", "EMACross")
SEED = setting("SEED", 42)
CASCADE_INSTRUMENT = setting("CASCADE_INSTRUMENT", "BTCUSDT-LINEAR.BYBIT")
CASCADE_PARAMS = setting("CASCADE_PARAMS", {})
runner = NodeRunner()
bar_specs = gallery.default_specs(
    params.catalog_path,
    INSTRUMENT,
    params.start,
    params.end,
    DATA,
    PERIODS,
    STARTING_BALANCE,
)
cascade_specs = gallery.cascade_specs(
    params.catalog_path,
    CASCADE_INSTRUMENT,
    params.start,
    params.end,
    CASCADE_PARAMS,
    STARTING_BALANCE,
)
sample = gallery.cascade_sample(
    params.catalog_path, CASCADE_INSTRUMENT, params.start, params.end, CASCADE_PARAMS
)
specs = [*bar_specs, *cascade_specs]
print(f"{INSTRUMENT}, {DATA}, {params.start} -> {params.end}: {len(bar_specs)} strategies")
print(f"{CASCADE_INSTRUMENT}, liquidations: {len(cascade_specs)} cascade runs; {sample}")
print(f"sizes {PERIODS or 'each strategy default'}; execution axes repeat {AXIS_SPEC!r}")

# %% [markdown]
# ## 2. Leaderboard
#
# Every spec run once (`gallery.run_specs`), one row each: the `MetricReport` statistics (win rate,
# expectancy, Sharpe, Sortino, Calmar, max drawdown, profit factor, ... -- None where undefined, e.g.
# a return statistic on a window shorter than two UTC days), the closed `trades`, the engine's
# `orders` and `fills`, the engine's run time (`wall_seconds`), the whole call's time
# (`total_seconds`, data loading included) and the `error`. The default order is the gallery's,
# not a ranking; sort on the column you care about.
#
# **The equity is the account balance:** realized PnL and every fee, not marked to market (the
# `RunResult` Known limit), so a position held open at the end of the window shows only its entry
# fee.
#
# A cascade row's label names its instrument and data kind (`Cascade follow short only
# (BTCUSDT-LINEAR.BYBIT, liquidations)`): it runs on another instrument and feed than the bar rows
# above it, and its sample is in section 3.

# %%
outcomes = gallery.run_specs(runner, specs)
board = gallery.leaderboard(outcomes)
print(board.to_string(index=False))
gallery.check_outcomes(outcomes)  # the board shows a failed row first, then the run fails loudly
equity = gallery.equity_frame(outcomes)
equity_fig = go.Figure(
    [
        go.Scatter(x=rows["ts"], y=rows["equity"], name=label, line_shape="hv")
        for label, rows in equity.groupby("label")
    ]
)
equity_fig.update_layout(title="Account equity per strategy", height=560)
equity_fig.show()

# %% [markdown]
# ## 3. Liquidation cascades
#
# The four `LiquidationCascadeStrategy` runs alone (`gallery.cascade_outcomes`): their leaderboard
# rows and equity. **Read the sample first** (printed beside the chart): the window's UTC days, its
# archived liquidations and the episodes the strategy's own detector finds in them
# (`research.application.liquidations.replay_cascade` of the one
# `kernel.indicators.LiquidationCascade`).
# A handful of episodes is a handful of trades: no statistic of these rows means anything until
# the window holds many. Follow sells into a cascade of long liquidations while it rises (and buys
# into short liquidations) and closes when it is spent; fade waits for the spent state and takes
# the other side. The notional is priced at the bankruptcy price (audit D-148), and exits are
# judged once a second with market orders, never resting stops (the strategy's Known limits).

# %%
cascades = gallery.cascade_outcomes(outcomes)
print(gallery.leaderboard(cascades).to_string(index=False))
cascade_equity = gallery.equity_frame(cascades)
cascade_fig = go.Figure(
    [
        go.Scatter(x=rows["ts"], y=rows["equity"], name=label, line_shape="hv")
        for label, rows in cascade_equity.groupby("label")
    ]
)
cascade_fig.update_layout(title=f"{CASCADE_INSTRUMENT}: liquidation cascade equity", height=420)
cascade_fig.show()
print(sample)

# %% [markdown]
# ## 4. Execution axes
#
# The `AXIS_SPEC` row repeated with exactly one thing changed (`gallery.execution_axes`): each of
# the 11 fill models (probabilities 0.5 / 0.5, seeded with `SEED`), each of the 3 fee models (a flat
# 0.1 commission for the fixed and per-contract ones, in the settlement currency) and three latency
# settings (none, separate insert / update / cancel delays, and the baseline's own fixed 300 ms,
# shown as the baseline row rather than run again). The tables are the
# same as the leaderboard with the `axis` and `value` columns; the figure then plots, per setting,
# how far each fill's price is from the same order's price in the baseline run (the gallery row,
# i.e. the venue defaults and a 300 ms delay), in basis points (`gallery.slippage_by_axis`: orders
# matched on id, side and quantity; an order the baseline did not fill is left out, not zero).
#
# **Read it as sensitivity:** a result that survives every row is robust to the exchange model; one
# that flips sign under a fee or a latency setting was never an edge. A fill model is a
# hypothesis about the venue until it is measured (the doc's section 5): the table shows what the
# hypotheses do, not what the venue does.

# %%
baseline = gallery.find_outcome(outcomes, AXIS_SPEC)
axes = gallery.execution_axes(baseline.gallery, SEED)
axis_outcomes = gallery.run_specs(runner, axes)
axis_board = gallery.axis_table(axis_outcomes, baseline)
for axis in ("fill", "fee", "latency"):
    print(
        axis_board[axis_board["axis"] == axis]
        .drop(columns=["label", "axis", "strategy"])
        .to_string(index=False)
    )
differences = gallery.slippage_by_axis(axis_outcomes, baseline)
difference_fig = go.Figure(
    [
        go.Histogram(x=rows["difference_bps"], name=f"{axis}: {value}", opacity=0.6)
        for (axis, value), rows in differences.groupby(["axis", "value"])
    ]
)
difference_fig.update_layout(
    title=f"{AXIS_SPEC}: fill price against the baseline run (bps)", barmode="overlay"
)
difference_fig.show()
print(f"{len(differences)} matched fills across {len(axes)} settings")

# %% [markdown]
# ## 5. Reading guide
#
# - **Copy a row into `04_backtest_evaluation`.** A row is a `RunSpec`: in notebook 04 set
#   `STRATEGY` and `STRATEGY_CONFIG` to the string paths of the row's strategy (an upstream example
#   is `nautilus_trader.examples.strategies.<file>:<Class>`, a family strategy is
#   `research.strategies.ma_cross_strategy:MACrossStrategy` or
#   `research.strategies.indicator_signal_strategy:IndicatorSignalStrategy`, each with its
#   `...Config`), `PARAMS` to the row's parameters (`print(specs[i].spec)` shows them, resolved from
#   `PERIODS`), `DATA` to `"bars:1-MINUTE"`; then the single run, the sweep and the walk-forward
#   judge it.
# - **Everything here is in-sample.** Fixed, untuned parameters on one window: the leaderboard says
#   what each strategy did on this stretch of data, not what it will do. A top row over a few
#   trades is luck until a longer window, a sweep and a walk-forward say otherwise.
# - **Few trades, short windows.** The return statistics need realized PnL on two UTC days; read
#   `trades` before any rate. An empty column is an undefined statistic, never a zero.
# - **A fill model is a hypothesis.** The fill, fee and latency rows change the simulated exchange,
#   not the market: until the venue's real slippage, fees and order round trip are measured on the
#   VPS, treat the spread between rows as the uncertainty of the result. Identical fill-model rows
#   are the same modelling choice at this order size, not independent hypotheses: at `trade_size`
#   0.01 on an L1 venue the tiered and size-aware models never reach a second tier (the catalog's
#   section 5, pitfall 6).
# - **A cascade row is only as good as its sample.** The cascade runs trade episodes, not bars: the
#   sample line says how many the window holds. They run on the derived 1 s quotes, so a fill is
#   the next second's top of book; copy one into notebook 04 with `DATA` `"liquidations"` and
#   `INSTRUMENT` a Bybit LINEAR id.
# - **One strategy per row, one position at a time.** Sizes are fixed (`trade_size` 0.01), there
#   is no portfolio sizing, and the family strategies hold at most one position.
