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
# # 06 Candlestick scanner
#
# Scans the collected instruments for the classic candlestick patterns at several timeframes, lists
# every hit in one table tagged by instrument, timeframe and direction, draws the candles around a
# chosen hit with its EMA, and measures what price did 1, 5 and 20 bars after each hit: the hit
# rate and mean forward return per pattern, which is this notebook's research output.
#
# The patterns are `kernel.candle_patterns` (`CandlePatternSet`) -- the same detector the chart's
# picker, the screener's Technicals columns and a strategy use, so a pattern found here reads the
# same everywhere. The EMA is `nautilus_trader.indicators.ExponentialMovingAverage`. Bars come only
# from the candle store's own fold through `MarketFrames.bars` (never a resample here), and every
# frame and number comes from `research.application.patterns` and `research.domain.events`: no cell
# holds a formula. No third-party TA library -- the detector was checked against TA-Lib once,
# outside the repo (Story 27.7's completion notes).
#
# Gaps stay gaps (DATA-01). Each timeframe's bars sit on the complete bucket grid: a bucket the
# collector never observed, an untraded bucket or a `partial` bar is a blank row. The pattern set
# and the EMA restart after every blank row -- a two- or three-bar pattern never spans a gap -- and
# a forward return that would cross one, or run past `END`, is blank rather than filled.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `CANDLES_DIR`,
# `INSTRUMENTS`, `START`, `END`. The rest are set here; each can also be given as JSON in
# `NOTEBOOK_<NAME>` (`_params.setting`), which is how the test harness shrinks them to its
# ten-minute fixture:
#
# - `TIMEFRAMES` -- bar sizes in seconds; a size the candle store does not keep is skipped with a
#   line (the store keeps 1 m, 5 m, 15 m, 1 h, 4 h and 1 d);
# - `EMA_LEN` -- the EMA's period, in bars of each timeframe;
# - `CONDITION` -- `"above"` keeps hits closing above their EMA, `"below"` below it, `"any"` all (a
#   hit whose EMA is still warming up fails `"above"` and `"below"`);
# - `PATTERN_FILTER` -- blank for every pattern, else comma-separated `PatternName`s;
# - `HIT_INDEX` -- the row of the filtered hits table §4 draws. Known limit: choosing a hit or a
#   filter means editing this cell and re-running, since there is no widget to click through hits
#   (`ipywidgets` was rejected as a dependency, NFR12). Upgrade path: a dependency decision for a
#   widget library, or the web chart's indicator picker for point-and-click browsing;
# - `WINDOW_BARS` -- bars drawn either side of that hit;
# - `HORIZONS` -- forward-return horizons, in bars;
# - `THRESHOLDS` -- overrides of the detector's `Thresholds` (`body_ratio`, `shadow_ratio`,
#   `doji_body_ratio`, `marubozu_shadow_ratio`, `tweezer_ratio`, `trend_bars`, `star_gap`), `{}`
#   for the documented defaults.

# %%
import plotly.graph_objects as go
from _params import Params
from _params import setting
from kernel.candle_patterns import Thresholds
from research.application import patterns
from research.application.frames import CatalogFrames
from research.application.ports import window_ns


params = Params.from_env()
TIMEFRAMES = setting("TIMEFRAMES", [60, 300, 900, 3600, 14400, 86400])
EMA_LEN = setting("EMA_LEN", 50)
CONDITION = setting("CONDITION", "any")
PATTERN_FILTER = setting("PATTERN_FILTER", "")
HIT_INDEX = setting("HIT_INDEX", 0)
WINDOW_BARS = setting("WINDOW_BARS", 30)
HORIZONS = setting("HORIZONS", [1, 5, 20])
THRESHOLDS = Thresholds(**setting("THRESHOLDS", {}))
frames = CatalogFrames(params.catalog_path, params.candles_dir)
window = window_ns(params.start, params.end)
print(f"{len(params.instruments)} instruments, {params.start} -> {params.end}")
print(f"timeframes {TIMEFRAMES}, EMA({EMA_LEN}), condition {CONDITION!r}, horizons {HORIZONS}")
print(THRESHOLDS)

# %% [markdown]
# ## 2. Bars per timeframe
#
# One grid per instrument and kept timeframe (`patterns.bar_grids`): every bucket whose whole bar
# lies in `START`-`END`, read span by span over the store's coverage. The summary counts each grid's
# buckets, the traded bars among them, the blank rows (holes) and the partial bars blanked with
# them.

# %%
kept, skipped = patterns.split_timeframes(TIMEFRAMES)
for line in skipped:
    print(line)
grids = patterns.bar_grids(frames, params.instruments, kept, window)
print(patterns.grid_summary(grids).to_string(index=False))

# %% [markdown]
# ## 3. Scan
#
# `CandlePatternSet(THRESHOLDS)` streamed over every grid, with the EMA beside it
# (`patterns.scan_grids`): one row per pattern fired on a bar, with its direction (+100 bullish,
# -100 bearish; `DOJI` is non-directional and reads +100, TA-Lib's convention), the bar and its EMA.
# `CONDITION` and `PATTERN_FILTER` then narrow the table (`patterns.filter_hits`).

# %%
hits = patterns.scan_grids(grids, THRESHOLDS, EMA_LEN)
shown = patterns.filter_hits(hits, CONDITION, PATTERN_FILTER)
print(
    f"{len(hits)} hits, {len(shown)} after CONDITION={CONDITION!r} PATTERN_FILTER={PATTERN_FILTER!r}"
)
print(shown.drop(columns=["ts_ns"]).to_string(max_rows=60))

# %% [markdown]
# ## 4. A hit on the chart
#
# The candles around hit `HIT_INDEX` of the filtered table, `WINDOW_BARS` either side, with the EMA
# and the hit marked (a triangle under the bar for a bullish hit, over it for a bearish one). A
# blank row is a gap in the candles, as it is in the scan. No hit prints a sentence instead.

# %%
note = patterns.no_hits_note(shown, CONDITION, PATTERN_FILTER)
if note is not None:
    print(note)
else:
    hit = patterns.select_hit(shown, HIT_INDEX)
    grid = patterns.with_ema(grids[(hit.instrument_id, int(hit.timeframe))], EMA_LEN)
    around = patterns.hit_window(grid, int(hit.bar_index), WINDOW_BARS)
    bullish = hit.direction > 0
    hit_fig = go.Figure(
        [
            go.Candlestick(
                x=around["timestamp"],
                open=around["o"],
                high=around["h"],
                low=around["l"],
                close=around["c"],
                name="bars",
            ),
            go.Scatter(
                x=around["timestamp"], y=around["ema"], mode="lines", name=f"EMA({EMA_LEN})"
            ),
            go.Scatter(
                x=[hit.timestamp],
                y=[hit.low if bullish else hit.high],
                mode="markers",
                marker={"symbol": "triangle-up" if bullish else "triangle-down", "size": 14},
                name=f"{hit.pattern} {hit.direction:+d}",
            ),
        ]
    )
    hit_fig.update_layout(
        title=f"{hit.instrument_id} {hit.timeframe} s: {hit.pattern} at {hit.timestamp}",
        xaxis_rangeslider_visible=False,
    )
    hit_fig.show()

# %% [markdown]
# ## 5. Forward returns and hit rate
#
# **Derived:** for every hit in the filtered table, the simple return from its close to the close
# `h` bars later on its own grid (`research.domain.events.forward_returns`), blank across a hole or
# past `END`; then per timeframe, pattern and direction, and per horizon, the share of measurable
# returns in the hit's direction, their mean and their count `n`
# (`research.domain.events.hit_rate`; a flat return is not a hit). A group with `n` 0 is shown,
# never dropped. `DOJI` is non-directional, so it has a mean and `n` but no hit rate.

# %%
forward = patterns.forward_table(grids, shown, HORIZONS)
if forward.empty:
    print("No hit to measure: the forward table is empty.")
else:
    print(forward.to_string(index=False, max_rows=120))
    forward_fig = go.Figure(
        [
            go.Bar(
                x=[rows["pattern"], rows["direction"].astype(str)],
                y=rows["hit_rate"],
                name=f"{timeframe} s, {horizon} bars",
            )
            for (timeframe, horizon), rows in forward.groupby(["timeframe", "horizon"])
        ]
    )
    forward_fig.update_layout(
        title="Hit rate after each pattern, per timeframe and horizon",
        yaxis={"title": "hit rate", "range": [0, 1]},
        barmode="group",
    )
    forward_fig.show()

# %% [markdown]
# ## 6. Reading guide
#
# - **A hit is the bar that completes the pattern.** Its forward return starts at that bar's close,
#   so nothing after the hit leaks into it; a strategy acting on the hit enters one bar later.
# - **`n` before rate.** A hit rate over a handful of hits is noise: read `n` first, and widen
#   `START`/`END` or add instruments before trusting a rate. The groups overlap (several patterns
#   can fire on one bar, one instrument trades on three venues), so the rows are not independent.
# - **Drift sets the bar.** In a rising window most bullish patterns look good and most bearish
#   ones bad; compare a pattern's rate with its mirror's, and with the same horizon's rate over all
#   bars, before calling it an edge.
# - **Thresholds are the definition.** Every pattern is an inequality on the bar's own range
#   (`kernel/candle_patterns.py`'s docstring); a different `THRESHOLDS` is a different pattern.
#   Hammer/hanging man, stars, soldiers/crows and tweezers also need `trend_bars` closes in one
#   direction before them, so they need `trend_bars + 1` bars of history after every gap.
# - **Stars need a gap.** With `star_gap` (the default) a star's body must gap from the first
#   bar's, which 24/7 markets rarely print; `THRESHOLDS={"star_gap": false}` drops that condition.
# - **Next: trade it.** Story 27.8 wraps the same `CandlePattern` in `CandlePatternStrategy`: a
#   pattern and horizon that hold up here become one config for `04_backtest_evaluation`'s
#   `BacktestRunner`, then a paper bot, with no second pattern definition.
