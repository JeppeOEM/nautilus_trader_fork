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
# # 02 Microstructure
#
# The microstructure of each instrument over one window, before a signal is designed on it:
# spread, depth, imbalance and order flow, the microprice's edge, trade flow and price impact,
# funding/basis/open interest, and the autocorrelation and volatility structure of returns.
#
# The archive stores raw inputs only (SIGNAL-01, `platform/CLAUDE.md`): each second's top-20 book
# (`bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes`) and its folded trades (`buy_volume`,
# `sell_volume`, `buy_count`, `sell_count`), plus the mark, index, funding and open-interest
# streams. Every other number is derived on read by a `kernel.indicators` class or function,
# `research.domain.microstructure` or `research.application.microstructure`; no cell holds a
# formula.
#
# Gaps stay gaps (DATA-01). Every per-second series sits on the window's 1 s grid: a second with
# no snapshot, a second holding two, and -- for a value derived from the book -- a crossed second
# (best bid >= best ask, which the capture gate never writes, so in the archive it is a defect)
# are blank, and no line is drawn across them.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `CANDLES_DIR`,
# `INSTRUMENTS` (comma-separated), `START`, `END` (ISO, naive = UTC). The OFI settings are not
# re-typed here: they come from `OFIStrategyConfig` (`research/strategies/ofi_strategy.py`), built
# once per instrument, so the replay below is the strategy's.
#
# Each instrument is read once, with the window's `start=`/`end=` (MEM-01), and only its derived
# frames are kept: a day of one instrument's 1 s snapshots is ~86 400 rows of four 20-level lists,
# so the default window is one day. Widen `START`/`END` with care -- memory grows with the window,
# and the OFI replay and the OBI z-scores are per-row Python work (seconds per instrument-day).

# %%
import pandas as pd
import plotly.graph_objects as go
from _params import Params
from plotly.subplots import make_subplots
from research.application import inspection
from research.application import microstructure
from research.application.frames import OBI_LEVELS
from research.application.frames import CatalogFrames
from research.application.ports import window_ns
from research.strategies.ofi_strategy import OFIStrategyConfig

from nautilus_trader.model.identifiers import InstrumentId


params = Params.from_env()
start_ns, end_ns = window_ns(params.start, params.end)
frames = CatalogFrames(params.catalog_path, params.candles_dir)
definitions = inspection.instrument_definitions(params.catalog_path)
configs = {
    iid: OFIStrategyConfig(instrument_id=InstrumentId.from_str(iid)) for iid in params.instruments
}
titles = list(params.instruments)
print(params)

# %% [markdown]
# Reading the window: one `read_instrument` call per instrument
# (`research.application.microstructure`) reads the snapshots, marks/indexes, funding and open
# interest once and derives every frame the sections below plot.

# %%
data: dict[str, microstructure.InstrumentMicrostructure] = {}
for iid in params.instruments:
    tick = microstructure.tick_size(definitions, iid)
    data[iid] = microstructure.read_instrument(frames, iid, start_ns, end_ns, configs[iid], tick)
    print(f"{iid}: tick {tick}, {data[iid].snapshot_rows} snapshot rows")

# %% [markdown]
# ## 2. Spread
#
# **Read:** `bid_prices[0]`, `ask_prices[0]`. **Derived:** `spread` and `mid`
# (`kernel.indicators.spread`/`mid_price`), in ticks (the instrument definition's
# `price_increment`) and in basis points of mid (`microstructure.spread_frame`). The box plot is
# the spread's distribution by UTC hour of day (`microstructure.by_hour_utc`) -- a regime change
# or a seasonal pattern shows there first.

# %%
spreads = make_subplots(
    rows=len(titles), cols=1, subplot_titles=titles, specs=[[{"secondary_y": True}]] * len(titles)
)
hours = go.Figure()
for row, iid in enumerate(params.instruments, start=1):
    frame = microstructure.spread_frame(data[iid].grid, data[iid].tick)
    x = inspection.plot_axis(frame.index)
    spreads.add_trace(
        go.Scattergl(x=x, y=frame["spread_ticks"], name=f"{iid} ticks", connectgaps=False),
        row,
        1,
        secondary_y=False,
    )
    spreads.add_trace(
        go.Scattergl(x=x, y=frame["spread_bps"], name=f"{iid} bps", connectgaps=False),
        row,
        1,
        secondary_y=True,
    )
    hourly = microstructure.by_hour_utc(frame["spread_bps"])
    hours.add_trace(go.Box(x=hourly["hour"], y=hourly["value"], name=iid, boxpoints=False))
spreads.update_layout(title="Spread: ticks (left), bps (right)", height=250 * len(titles))
spreads.show()
hours.update_layout(title="Spread (bps) by UTC hour", boxmode="group", xaxis_title="hour (UTC)")
hours.show()

# %% [markdown]
# ## 3. Depth
#
# **Read:** the 20 stored levels a side (`bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes`).
# **Derived:** `kernel.indicators.snapshot_depth` -> `DepthProfile`, then `cumulative_depth` (size
# by level) and `depth_within_bps` (size within each distance from mid), on the first two-sided,
# uncrossed snapshot of every minute (`microstructure.depth_summary`), averaged over the window;
# and the total bid/ask size per minute. A distance past the deepest stored level is blank: the
# book beyond it was not stored. Bid above ask depth for long stretches is depth asymmetry.

# %%
by_level = make_subplots(rows=1, cols=2, subplot_titles=["by level", "by distance from mid (bps)"])
totals = make_subplots(rows=len(titles), cols=1, subplot_titles=titles)
for row, iid in enumerate(params.instruments, start=1):
    depth = data[iid].depth
    for side in ("bid", "ask"):
        by_level.add_trace(
            go.Scatter(x=depth.by_level["level"], y=depth.by_level[side], name=f"{iid} {side}"),
            1,
            1,
        )
        by_level.add_trace(
            go.Scatter(
                x=depth.by_bps["bps"], y=depth.by_bps[side], name=f"{iid} {side}", connectgaps=False
            ),
            1,
            2,
        )
    x = inspection.plot_axis(depth.totals.index)
    for column in ("total_bid", "total_ask"):
        totals.add_trace(
            go.Scattergl(x=x, y=depth.totals[column], name=f"{iid} {column}", connectgaps=False),
            row,
            1,
        )
    print(iid)
    print(depth.by_bps.to_string())
by_level.update_layout(title="Mean cumulative size (one snapshot a minute)")
by_level.show()
totals.update_layout(
    title=f"Stored depth (top {microstructure.DEPTH_LEVELS} levels) per minute",
    height=250 * len(titles),
)
totals.show()

# %% [markdown]
# ## 4. Imbalance and OFI
#
# **Read:** the book levels. **Derived:** order-book imbalance at N = 1, 5, 10, 20 levels
# (`kernel.indicators.MultiLevelOBI`, `obi_<N>` of `CatalogFrames.seconds`); order-flow imbalance
# (`kernel.indicators.MultiLevelOFI`) replayed over consecutive seconds exactly as
# `OFIStrategy.on_data` does (its `ofi_levels`/`ofi_window`, USD notional, every two-sided row,
# the previous book cleared after a gap over `OFI_GAP_NS`; `microstructure.ofi_replay`); and the
# z-scores of both over the strategy's `ofi_zscore_window` (`kernel.indicators.RollingZScore`, the
# formula `MultiLevelOFI` itself uses). The dashed lines are the strategy's `±ofi_threshold`; the
# shaded span is its `warmup_seconds` from the first two-sided row, when it evaluates nothing
# (the whole window when the window is shorter). The row right after a gap is blank: the OFI makes
# no new reading there (the strategy still evaluates its pre-gap value on it -- a known limit of
# the strategy, not reproduced here). A z-score that moves while the price is frozen does not
# prove a live feed (OBS-02).

# %%
imbalance = make_subplots(rows=len(titles), cols=1, subplot_titles=titles)
flow_z = make_subplots(
    rows=len(titles), cols=1, subplot_titles=titles, specs=[[{"secondary_y": True}]] * len(titles)
)
for row, iid in enumerate(params.instruments, start=1):
    grid, ofi, obi_z = data[iid].grid, data[iid].ofi, data[iid].obi_z
    x = inspection.plot_axis(grid.index)
    for n in OBI_LEVELS:
        imbalance.add_trace(
            go.Scattergl(x=x, y=grid[f"obi_{n}"], name=f"{iid} obi_{n}", connectgaps=False),
            row,
            1,
        )
        flow_z.add_trace(
            go.Scattergl(x=x, y=obi_z[f"obi_{n}_z"], name=f"{iid} obi_{n}_z", connectgaps=False),
            row,
            1,
        )
    flow_z.add_trace(
        go.Scattergl(x=x, y=ofi["ofi_z"], name=f"{iid} ofi_z", connectgaps=False), row, 1
    )
    flow_z.add_trace(
        go.Scattergl(x=x, y=ofi["ofi"], name=f"{iid} ofi (USD)", connectgaps=False),
        row,
        1,
        secondary_y=True,
    )
    for level in microstructure.ofi_threshold_lines(configs[iid]):
        flow_z.add_hline(y=level, line_dash="dash", row=row, col=1)
    span = microstructure.warmup_span(data[iid].warmup_end_ns, start_ns, end_ns)
    warmup = inspection.plot_axis(pd.DatetimeIndex(pd.to_datetime(list(span), utc=True)))
    flow_z.add_vrect(x0=warmup[0], x1=warmup[1], opacity=0.1, line_width=0, row=row, col=1)
imbalance.update_layout(title="Order-book imbalance (bid share)", height=250 * len(titles))
imbalance.show()
flow_z.update_layout(title="OFI and OBI z-scores; raw OFI (right)", height=250 * len(titles))
flow_z.show()

# %% [markdown]
# ## 5. Microprice edge
#
# **Read:** the top of book. **Derived:** `microprice - mid` at second t (`kernel.indicators`'
# `microprice` and `mid_price`) against the next second's mid change (`microstructure
# .microprice_edge`), in predictor deciles: each bin's mean predictor and mean outcome (the binned
# scatter) and the hit rate -- how often the next move has the predictor's sign, over pairs where
# neither is zero (`research.domain.microstructure.hit_rate_by_bin`). A hit rate far from 0.5 in
# the outer bins is an edge.

# %%
edge_fig = make_subplots(
    rows=len(titles),
    cols=2,
    subplot_titles=[f"{iid}: {kind}" for iid in titles for kind in ("binned scatter", "hit rate")],
)
for row, iid in enumerate(params.instruments, start=1):
    table = microstructure.hit_rate_table(microstructure.microprice_edge(data[iid].grid))
    edge_fig.add_trace(
        go.Scatter(
            x=table["mean_predictor"], y=table["mean_outcome"], name=iid, mode="lines+markers"
        ),
        row,
        1,
    )
    edge_fig.add_trace(go.Bar(x=table["mean_predictor"], y=table["rate"], name=iid), row, 2)
    print(iid)
    print(table.to_string() if len(table) else "  no predictor spread to bin")
edge_fig.update_xaxes(title_text="microprice - mid")
edge_fig.update_layout(
    title="Microprice edge on the next-second mid change (price units per instrument)",
    height=250 * len(titles),
)
edge_fig.show()

# %% [markdown]
# ## 6. Trade flow and price impact
#
# **Read:** `buy_volume`, `sell_volume`, `buy_count`, `sell_count`. **Derived:** per-second
# `volume_delta` (`kernel.indicators.volume_delta`) and the CVD over the sampled seconds
# (`microstructure.trade_flow`: blank on a missing second and restarted from 0 after it, since
# the unsampled second's trades are unknown -- never carried across as if nothing traded); and a
# Kyle-lambda style fit of the mid change across each second's trades
# (`mid[t+1] - mid[t-1]`) on its signed volume, per `|signed volume|` decile
# (`research.domain.microstructure.price_impact`, OLS by `numpy.linalg.lstsq`). Known limit: the
# 2 s mid window adds neighbouring seconds' drift and impact, and adjacent windows overlap
# (`microstructure.impact_inputs`): compare slopes, do not read a standard error into them.

# %%
flow = make_subplots(
    rows=len(titles), cols=1, subplot_titles=titles, specs=[[{"secondary_y": True}]] * len(titles)
)
counts = make_subplots(rows=len(titles), cols=1, subplot_titles=titles)
impact = make_subplots(rows=len(titles), cols=1, subplot_titles=titles)
for row, iid in enumerate(params.instruments, start=1):
    trades = microstructure.trade_flow(data[iid].grid)
    x = inspection.plot_axis(trades.index)
    for column in ("buy_volume", "sell_volume"):
        flow.add_trace(
            go.Scattergl(x=x, y=trades[column], name=f"{iid} {column}", connectgaps=False), row, 1
        )
    flow.add_trace(
        go.Scattergl(x=x, y=trades["cvd"], name=f"{iid} CVD", connectgaps=False),
        row,
        1,
        secondary_y=True,
    )
    for column in ("buy_count", "sell_count"):
        counts.add_trace(
            go.Scattergl(x=x, y=trades[column], name=f"{iid} {column}", connectgaps=False), row, 1
        )
    fit = microstructure.impact_table(microstructure.impact_inputs(data[iid].grid))
    impact.add_trace(
        go.Scatter(x=fit["upper"], y=fit["slope"], name=iid, mode="lines+markers"), row, 1
    )
    print(iid)
    print(fit.to_string() if len(fit) else "  no traded volume to bucket")
flow.update_layout(title="Buy/sell volume; CVD (right)", height=250 * len(titles))
flow.show()
counts.update_layout(title="Trade counts per second", height=250 * len(titles))
counts.show()
impact.update_layout(
    title="Price impact: mid change per unit signed volume, by |signed volume| bucket upper edge",
    height=250 * len(titles),
)
impact.show()

# %% [markdown]
# ## 7. Funding, basis and open interest on price
#
# **Read:** `MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`, `OpenInterest` (bounded
# reads). **Derived:** the basis `mark - index` per second from each stream's last value in that
# second (`microstructure.basis_frame`; blank where either is absent, never carried across
# seconds). Funding and open interest are event rows at their own stamps, drawn as markers. A
# stream a venue does not publish for an instrument (Bybit spot has no mark, index, funding or
# open interest) prints "not collected in this window" and draws nothing.

# %%
for iid in params.instruments:
    item = data[iid]
    for stream, frame in (
        ("mark/index", item.mark_index),
        ("funding", item.funding),
        ("open interest", item.open_interest),
    ):
        print(microstructure.coverage_line(iid, stream, frame))
    overlay = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        subplot_titles=["basis", "funding rate", "open interest"],
        specs=[[{"secondary_y": True}]] * 3,
    )
    x = inspection.plot_axis(item.grid.index)
    for row in (1, 2, 3):
        overlay.add_trace(
            go.Scattergl(x=x, y=item.grid["mid"], name="mid", connectgaps=False), row, 1
        )
    overlay.add_trace(
        go.Scattergl(
            x=inspection.plot_axis(item.basis.index),
            y=item.basis["basis"],
            name="mark - index",
            connectgaps=False,
        ),
        1,
        1,
        secondary_y=True,
    )
    overlay.add_trace(
        go.Scatter(
            x=inspection.plot_axis(item.funding.index),
            y=item.funding["rate"],
            name="funding rate",
            mode="markers",
        ),
        2,
        1,
        secondary_y=True,
    )
    overlay.add_trace(
        go.Scatter(
            x=inspection.plot_axis(item.open_interest.index),
            y=item.open_interest["open_interest"],
            name="open interest",
            mode="markers",
        ),
        3,
        1,
        secondary_y=True,
    )
    overlay.update_layout(title=f"{iid}: mid (left) with basis, funding, OI (right)", height=750)
    overlay.show()

# %% [markdown]
# ## 8. Returns: autocorrelation, volatility signature, realised volatility
#
# **Derived:** the grid mid's 1 s simple returns (`ReturnSeries.from_prices`, blank across every
# gap), compounded to 10 s, 1 m, 5 m and 1 h by `ReturnSeries.resample` -- not bars from the candle
# store -- so every horizon comes from the same series; a bucket holding a gap is blank. From them:
# the autocorrelation at each horizon and lag (pairwise-complete Pearson,
# `research.domain.microstructure.autocorrelation`; a horizon the window cannot fill has 0
# pairs), the volatility signature (realised variance per second at each sampling interval,
# `volatility_signature`: flat for i.i.d. returns, bent at short intervals by microstructure
# noise) and a rolling realised volatility (`realised_volatility`, annualised over a 365-day
# year).
#
# This volatility is a research measure over this window only. The screener's volatility is
# ranking's (`ranking/domain/metrics.py`, published by `ranking_engine`, SSOT-02): a different
# number from a different window, never recomputed or compared as the same metric here.

# %%
acf: dict[str, pd.DataFrame] = {}
acf_fig = make_subplots(rows=len(titles), cols=1, subplot_titles=titles)
signature = go.Figure()
volatility = go.Figure()
for row, iid in enumerate(params.instruments, start=1):
    returns = microstructure.second_returns(data[iid].grid)
    acf[iid] = table = microstructure.autocorrelation_table(returns)
    for horizon, rows in microstructure.by_horizon(table).items():
        acf_fig.add_trace(go.Bar(x=rows["lag"], y=rows["rho"], name=f"{iid} {horizon}s"), row, 1)
    curve = microstructure.signature_table(returns)
    signature.add_trace(
        go.Scatter(
            x=curve["interval_s"], y=curve["variance_per_second"], name=iid, mode="lines+markers"
        )
    )
    rolling = microstructure.rolling_volatility(returns)
    volatility.add_trace(
        go.Scattergl(x=inspection.plot_axis(rolling.index), y=rolling, name=iid, connectgaps=False)
    )
    print(iid)
    print(table.to_string())
    print(curve.to_string())
acf_fig.update_xaxes(title_text="lag (horizon periods)")
acf_fig.update_layout(title="Return autocorrelation by horizon", height=250 * len(titles))
acf_fig.show()
signature.update_xaxes(type="log", title_text="sampling interval (s)")
signature.update_layout(title="Volatility signature: realised variance per second")
signature.show()
volatility.update_layout(
    title=f"Rolling realised volatility ({microstructure.RV_WINDOW} s window, annualised)"
)
volatility.show()

# %% [markdown]
# ## 9. Observations
#
# What this notebook is designed to surface, per instrument, and where each is used:
#
# - **Spread regime changes** -- the spread series and its hourly medians (§2): a jump that
#   persists is a regime change (venue, fee or liquidity-provider change), not noise.
# - **Depth asymmetry** -- `depth_bid_over_ask` and the per-minute totals (§3): a book
#   persistently heavier on one side.
# - **Seasonality** -- the widest hour of day (§2's box plot): when trading costs peak.
# - **Autocorrelation sign at each horizon** (§8): `+` is momentum (trend-following horizons),
#   `-` mean reversion (fade or market-make), each only outside the white-noise band
#   `±2/√pairs`; `~0` inside it (no evidence either way), `n/a` under 10 pairs -- a one-day
#   window holds ~23 hourly pairs, so the 1 h sign is rarely more than `~0`.
# - **Order-flow imbalance** (§4) is traded by `research/strategies/ofi_strategy.py`
#   (`OFIStrategy`): its z-score threshold is the dashed line; this notebook shows how often the
#   z-score crosses it after the strategy's warm-up (the shaded span). A crossing is a candidate
#   entry only: the strategy's own filters (`min_depth_levels`, imbalance and cumulative-delta
#   confirmation, trend) decide the trade -- backtest it to count trades.
# - **Microprice edge** (§5) and **price impact** (§6) have no strategy yet: an edge beside a
#   small impact is a candidate for a quoting strategy, sized by the impact slope.

# %%
overview = pd.DataFrame(
    [microstructure.observations(data[iid], acf[iid]) for iid in params.instruments]
)
print(overview.to_string())
