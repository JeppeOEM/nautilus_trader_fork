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
# # 07 Indicator atlas
#
# Every bar indicator of `nautilus_trader.indicators` -- the eight moving averages, the momentum,
# trend, volatility and volume classes and the fuzzy candlesticks -- replayed over one instrument's
# stored bars and drawn: the overlays on a candlestick, one pane per oscillator, trend or volume
# indicator, the fuzzy-candle vector as a heatmap and the swings as markers. The last section draws
# the platform's own snapshot indicators (`kernel.indicators`: microprice, order flow and book
# imbalance) over the 1 s frames. It is the visual half of `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md`:
# the doc says what each indicator takes and returns, this page shows what it looks like on your
# data, and `IndicatorSignalStrategy` / `MACrossStrategy` turn the same indicators into trades
# (`08_strategy_gallery`).
#
# No cell computes an indicator. Every series is read off the Nautilus object that produced it
# (`research.application.indicator_atlas`), and bars come only from the candle store's own fold
# through `MarketFrames.bars` (never a resample here).
#
# Gaps stay gaps (DATA-01). The bars sit on the complete bucket grid: a bucket the collector never
# observed, an untraded one or a `partial` bar is a blank row. Every indicator is reset at a blank
# row and draws nothing until it has seen enough bars again, so no line is carried across a hole and
# no window spans one.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `CANDLES_DIR`,
# `INSTRUMENTS`, `START`, `END`. The rest are set here; each can also be given as JSON in
# `NOTEBOOK_<NAME>` (`_params.setting`), which is how the test harness shrinks them to its
# ten-minute fixture:
#
# - `INSTRUMENT` -- the instrument drawn, by default the first of `INSTRUMENTS`;
# - `BAR_SECONDS` -- the bar size in seconds; one the candle store keeps (60, 300, 900, 3600,
#   14400 or 86400);
# - `PERIODS` -- overrides of the indicator sizes, `{}` for the sizes meant for the real archive
#   (RSI 14, Bollinger 20 x 2.0, MACD 12/26, Ichimoku 9/26/52/26, ...). A key is `"<key>"` (every
#   indicator that has it: `period`, `fast`, `slow`, `signal`, `k`, `k_multiplier`) or
#   `"<Indicator>.<key>"` for one (`"IchimokuCloud.senkou"`); the keys are the constructor
#   arguments listed in the doc's section 1;
# - `OBI_LEVELS`, `OFI_WINDOW` -- the book depth of the snapshot imbalance and the window, in
#   readings, of the order flow sums (section 11).

# %%
import plotly.graph_objects as go
from _params import Params
from _params import setting
from plotly.subplots import make_subplots
from research.application import indicator_atlas as atlas
from research.application.frames import CatalogFrames
from research.application.inspection import plot_axis
from research.application.ports import window_ns


params = Params.from_env()
INSTRUMENT = setting("INSTRUMENT", params.instruments[0])
BAR_SECONDS = setting("BAR_SECONDS", 60)
PERIODS = setting("PERIODS", {})
OBI_LEVELS = setting("OBI_LEVELS", 10)
OFI_WINDOW = setting("OFI_WINDOW", 50)
frames = CatalogFrames(params.catalog_path, params.candles_dir)
start_ns, end_ns = window_ns(params.start, params.end)
print(f"{INSTRUMENT}, {BAR_SECONDS} s bars, {params.start} -> {params.end}")
print(f"sizes {PERIODS or 'the real-archive defaults'}")

# %% [markdown]
# ## 2. Bars and the catalog
#
# The bars of `START`-`END` on the complete bucket grid (`bar_grid_with_volume`: open, high, low,
# close and volume; a blank row is a bucket with no complete traded bar), and `catalog(PERIODS)`:
# one spec per indicator with its family, whether it draws on the price axis (`overlay`) or needs a
# scale of its own (`pane`), the bar fields it takes and the outputs it exposes. `replay` then feeds
# every spec the grid once; the tables below say how many rows each produced a value for.

# %%
grid = atlas.bar_grid_with_volume(frames, INSTRUMENT, BAR_SECONDS, start_ns, end_ns)
specs = atlas.catalog(PERIODS)
by_family = atlas.families(specs)
replayed = atlas.replay(grid, specs)
print(f"{len(grid)} buckets, {atlas.count_present(grid['c'])} with a complete traded bar")
for family, members in by_family.items():
    print(f"{family}: {', '.join(spec.name for spec in members)}")

# %% [markdown]
# ## 3. Helpers
#
# Two figure builders used by every section below, so each section is one call: a candlestick with
# the overlay indicators of one family drawn on it, and a stack of panes, one per indicator, each
# showing every output of that indicator. They only plot what `replayed` holds.


# %%
def candles() -> go.Figure:
    figure = go.Figure(
        go.Candlestick(
            x=grid["timestamp"],
            open=grid["o"],
            high=grid["h"],
            low=grid["l"],
            close=grid["c"],
            name="bars",
        )
    )
    figure.update_layout(xaxis_rangeslider_visible=False, height=520)
    return figure


def overlay_figure(title: str, members: list) -> go.Figure:
    figure = candles()
    for spec in members:
        for column in replayed[spec.name].columns:
            figure.add_trace(
                go.Scatter(
                    x=grid["timestamp"],
                    y=replayed[spec.name][column],
                    mode="lines",
                    name=f"{spec.name} {column}",
                )
            )
    figure.update_layout(title=title)
    return figure


def pane_figure(title: str, members: list) -> go.Figure:
    figure = make_subplots(
        rows=len(members), cols=1, shared_xaxes=True, subplot_titles=[s.name for s in members]
    )
    for row, spec in enumerate(members, start=1):
        for column in replayed[spec.name].columns:
            figure.add_trace(
                go.Scatter(
                    x=grid["timestamp"],
                    y=replayed[spec.name][column],
                    mode="lines",
                    name=f"{spec.name} {column}",
                ),
                row,
                1,
            )
    figure.update_layout(title=title, height=atlas.pane_figure_height(len(members)))
    return figure


def overlays(family: str) -> list:
    return [s for s in by_family[family] if s.placement == atlas.OVERLAY]


def panes(family: str) -> list:
    return [s for s in by_family[family] if s.placement == atlas.PANE]


# %% [markdown]
# ## 4. Moving averages
#
# The eight moving averages on the bars, each at the same `period` (the adaptive one's efficiency
# period; its fast and slow smoothing are 2 and 30). **Read it as lag:** the simple and weighted
# averages trail price most, the exponential, double-exponential and Wilder ones less, the Hull and
# the adaptive (Kaufman) one least; the adaptive average also flattens when price chops sideways
# and speeds up in a trend, and the variable-index one does the same driven by momentum. A cross of
# two of them is `MACrossStrategy`'s entry (`ma_type`, `fast_period`, `slow_period`).

# %%
overlay_figure("Moving averages", overlays("moving average")).show()

# %% [markdown]
# ## 5. Bands and channels
#
# Bollinger bands (the typical price's mean plus and minus `k` standard deviations), the Donchian
# channel (the highest high and lowest low of the period) and the Keltner channel (an EMA plus and
# minus `k_multiplier` ATRs). **Read it as range:** price at or past a band is stretched relative to
# the period; Bollinger widens with variance, Keltner with true range, Donchian only with new
# extremes, so a Bollinger squeeze inside a Keltner channel marks very low variance. `bollinger`,
# `donchian` and `keltner` are `IndicatorSignalStrategy` signals (long at the lower band, short at
# the upper, out at the middle).

# %%
overlay_figure("Bands and channels", overlays("volatility")).show()

# %% [markdown]
# ## 6. Trend overlays: Ichimoku, regression and VWAP
#
# The Ichimoku cloud (Tenkan, Kijun, the two Senkou spans and the Chikou span, the spans displaced
# by `displacement` bars as Nautilus defines them), the rolling linear-regression line and the
# volume-weighted average price (fed each bar's typical price; it restarts at every UTC day).
# **Read it as trend:** price above the cloud is bullish and below it bearish, the cloud's width is
# how much room price has to travel before the trend is challenged, the regression line is the
# least-squares fit of the last `period` closes, and the VWAP is the volume-weighted fair price of
# the day so far.

# %%
trend_overlays = [s for s in overlays("trend") if s.name != "Swings"]
volume_overlays = overlays("volume")
overlay_figure("Ichimoku, linear regression and VWAP", [*trend_overlays, *volume_overlays]).show()

# %% [markdown]
# ## 7. Momentum
#
# One pane per momentum oscillator: RSI (0..1, Nautilus's scale), rate of change, Chande momentum,
# stochastics (%K and %D), the commodity channel index, the efficiency ratio, the relative
# volatility index and the psychological line. **Read it as overbought / oversold and speed:** a
# bounded oscillator near its top says price rose faster than usual, near its bottom the opposite;
# the efficiency ratio is 1 for a straight line and near 0 for noise. Those thresholds are
# `IndicatorSignalStrategy`'s `rsi`, `stochastics`, `cci`, `cmo`, `rvi`, `psl`, `roc` and
# `efficiency_ratio` signals.

# %%
pane_figure("Momentum", panes("momentum")).show()

# %% [markdown]
# ## 8. Trend strength
#
# The Archer moving-average trend flags (`long_run`, `short_run`, each 0 or 1), the Aroon oscillator
# (up, down and their difference), the directional movement (+DI, -DI and their difference), MACD
# (the fast minus the slow average) and the bias (price against its average, a fraction).
# **Read it as direction and strength:** a positive MACD or Aroon oscillator is an up-trend, a
# widening gap between +DI and -DI a strengthening one, a bias far from 0 a stretched one.

# %%
pane_figure("Trend strength", panes("trend")).show()

# %% [markdown]
# ## 9. Volatility and regime
#
# The average true range, the Keltner position (where the close sits in the Keltner channel, -1
# at the lower band, +1 at the upper), the vertical horizontal filter and the volatility ratio
# (fast over slow range). **Read it as regime:** a high VHF means a trending market and a low one a
# ranging one, a volatility ratio above 1 means ranges are expanding. `vhf` and `volatility_ratio`
# are `IndicatorSignalStrategy`'s entry filters (`filter`, `filter_min`).

# %%
pane_figure("Volatility and regime", panes("volatility")).show()

# %% [markdown]
# ## 10. Volume
#
# On-balance volume (the running signed volume), the Klinger volume oscillator and the pressure
# indicator (its value and its cumulative sum). A bar with no volume is a blank row for these, so
# they restart after it. **Read it as confirmation:** a rising price with a falling OBV is a move
# volume does not back, and the reverse; the Klinger and pressure values changing sign mark the
# volume flow turning.

# %%
pane_figure("Volume", panes("volume")).show()

# %% [markdown]
# ## 11. Fuzzy candlesticks and swings
#
# The fuzzy-candle vector per bar (`fuzzy_frame`): direction (-1 bear, 0 none, 1 bull), then the
# candle's size, body, upper-wick and lower-wick size, each a small integer scale from very small
# to very large relative to the recent bars (`FuzzyCandlesticks`, the period's standard deviations).
# **Read it as a classification:** every bar is one row of five integers, which is what
# `IndicatorSignalStrategy`'s `fuzzy_candle` signal thresholds on (direction times a minimum
# size). Below it, the swing highs and lows (`Swings`) as markers on the candles: a marker is drawn
# at the bar that set a new swing price, so a held swing is drawn once.

# %%
fuzzy = atlas.fuzzy_frame(replayed)
fuzzy_fig = go.Figure(
    go.Heatmap(
        z=fuzzy.to_numpy(dtype=float, na_value=float("nan")).T,
        x=grid["timestamp"],
        y=list(fuzzy.columns),
        colorscale="RdBu",
    )
)
fuzzy_fig.update_layout(title="Fuzzy candlestick vector", height=320)
fuzzy_fig.show()
points = atlas.swing_points(replayed["Swings"])
swing_fig = candles()
for kind, rows in points.groupby("kind"):
    swing_fig.add_trace(
        go.Scatter(
            x=rows["timestamp"],
            y=rows["price"],
            mode="markers",
            marker={"symbol": "triangle-down" if kind == "high" else "triangle-up", "size": 11},
            name=f"swing {kind}",
        )
    )
swing_fig.update_layout(title="Swing highs and lows")
swing_fig.show()

# %% [markdown]
# ## 12. Snapshot indicators
#
# The platform's own indicators (`kernel.indicators`, the ones `OFIStrategy` and the ranking use)
# over the 1 s snapshots of `START`-`END` (`CatalogFrames.seconds`): the mid with the microprice
# (the mid weighted toward the thinner side: it leans toward the side about to move), the order flow
# imbalance at the top of book and over `OBI_LEVELS` levels (a sum over the last `OFI_WINDOW`
# readings), and the book imbalance over `OBI_LEVELS` levels (0..1, bid share of the visible size).
# **Read it as pressure:** positive order flow and a bid-heavy book say buyers are adding or
# lifting, and the microprice above the mid says the same one tick earlier. A gap of more than 3 s
# discards the previous book (a missing second never makes a fake flow spike), so the first reading
# after a gap is blank.

# %%
seconds = frames.seconds(INSTRUMENT, start=start_ns, end=end_ns)
snapshot = atlas.snapshot_indicators(seconds, OBI_LEVELS, OFI_WINDOW)
snapshot_axis = plot_axis(snapshot.index)
snapshot_fig = make_subplots(
    rows=3,
    cols=1,
    shared_xaxes=True,
    subplot_titles=["mid and microprice", "order flow imbalance", "book imbalance"],
)
for column in ("mid", "microprice"):
    snapshot_fig.add_trace(go.Scatter(x=snapshot_axis, y=snapshot[column], name=column), 1, 1)
for column in ("ofi_top", "ofi_levels"):
    snapshot_fig.add_trace(go.Scatter(x=snapshot_axis, y=snapshot[column], name=column), 2, 1)
snapshot_fig.add_trace(go.Scatter(x=snapshot_axis, y=snapshot["obi_levels"], name="obi"), 3, 1)
snapshot_fig.update_layout(title=f"{INSTRUMENT}: snapshot indicators", height=720)
snapshot_fig.show()
print(
    f"{len(seconds)} snapshots, {atlas.count_present(snapshot['ofi_levels'])} order flow readings"
)

# %% [markdown]
# ## 13. Reading guide
#
# - **An indicator is a transform of price, not a prediction.** Everything above is computed from
#   past bars only; whether a shape predicts anything is a backtest question
#   (`04_backtest_evaluation`, `08_strategy_gallery`).
# - **Warm-up is blank, not zero.** An indicator draws nothing until Nautilus reports it
#   initialized (a period of bars, more for the chained ones), and again after every hole in the
#   bars. A late start of a line is its warm-up, an early end of one is a hole.
# - **Sizes are the definition.** `PERIODS` changes the indicator, not just its speed: Bollinger's
#   `k` and the thresholds a strategy reads off an oscillator are its entry rule. Defaults are the
#   textbook sizes for minute bars, not tuned ones.
# - **Not every indicator is here.** `SpreadAnalyzer` needs quote ticks and
#   `BookImbalanceRatio` an order book, which the bars kind does not stream (the doc's section 6
#   lists them as follow-ups); the platform's snapshot equivalents are section 12.
# - **Next: trade it.** `IndicatorSignalStrategy(signal=<name>)` runs any indicator here as a
#   rule, `MACrossStrategy` the moving averages, both through `04_backtest_evaluation`.
