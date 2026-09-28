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
# # 03 Correlation and cross-venue behaviour
#
# How the collected instruments move together, and how one asset behaves across dYdX, Bybit and
# Hyperliquid: return correlation at 1 m, 5 m, 1 h and 1 d as clustered heatmaps, a rolling
# correlation against an anchor instrument, the single-linkage clusters with their merge
# distances, the correlation of funding levels and open-interest changes, how to feed a cluster
# into a backtest, and per asset the cross-venue mid basis, which venue leads on 1 s returns, the
# funding differential and each venue's share of the traded volume.
#
# Every number comes from `research.domain.correlation` and `research.application.aligned`; no
# cell holds a formula, a resample or a split of an instrument id. Same-asset matching is
# `kernel.venues.asset_key` (through `CatalogFrames.same_symbol`), never a prefix guess.
#
# Gaps stay gaps (DATA-01). A bar return across a bucket the candle store never observed (a
# collector outage), across an untraded bucket or past the store's edge is blank, and every
# correlation is pairwise-complete: each pair uses only the rows where both returns exist, nothing
# is forward-filled.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `CANDLES_DIR`,
# `INSTRUMENTS`, `START`, `END`. The rest are set here:
#
# - `ANCHOR` -- the instrument the rolling correlation is taken against (the first of
#   `INSTRUMENTS`);
# - `HORIZONS` -- the return horizons (s). Each is a bar size the candle store keeps (60, 300,
#   3600, 86400 are all in `candles.domain.fold.BAR_SECONDS`), so every horizon's bars are the
#   store's own fold of the 1 s snapshots, never a resample here. A daily return needs two whole
#   UTC days of bars: a one-day window has no 1 d return at all, so widen `START`/`END` to read it;
# - `CLUSTER_DISTANCE` -- the single-linkage merge threshold on `1 - rho`;
# - `ROLLING_WINDOW_S` -- the rolling correlation's window (one day of 1 m returns): it needs a
#   window longer than a day to show more than one point;
# - `MAX_LAG_S` -- the cross-venue lead-lag range (±s);
# - `FUNDING_BUCKET_S` -- the bucket funding levels are correlated over (§6); `OI_BUCKET_S` -- the
#   bucket of the OI changes (§6); `VOLUME_BUCKET_S` -- the bucket of the cross-venue funding
#   differential and volume share (§8).
#
# Memory (MEM-01): the bar sections read the candle store only; the cross-venue section reads the
# 1 s snapshots of one asset's legs at a time and keeps only their 1 s grids.

# %%
import plotly.graph_objects as go
from _params import Params
from plotly.subplots import make_subplots
from research.application import aligned
from research.application import inspection
from research.application.frames import CatalogFrames
from research.application.ports import RunSpec
from research.application.ports import window_ns
from research.domain.correlation import cluster
from research.domain.correlation import correlation_matrix
from research.domain.correlation import merge_order


params = Params.from_env()
start_ns, end_ns = window_ns(params.start, params.end)
frames = CatalogFrames(params.catalog_path, params.candles_dir)
ANCHOR = params.instruments[0]
HORIZONS = aligned.HORIZONS_S
CLUSTER_DISTANCE = 0.5
ROLLING_WINDOW_S = 86_400
MAX_LAG_S = 30
FUNDING_BUCKET_S = 3_600
OI_BUCKET_S = 300
VOLUME_BUCKET_S = 3_600
print(params)
print(f"anchor {ANCHOR}, horizons {HORIZONS} s, cluster distance {CLUSTER_DISTANCE}")

# %% [markdown]
# ## 2. Aligned returns per horizon
#
# **Read:** the candle store's bars (`CatalogFrames.bars`), span by span over
# `CatalogFrames.bar_coverage` -- the maximal runs of stored buckets inside the window -- so an
# outage, or a window reaching past what the store holds, is a hole rather than an error.
# **Derived:** close-to-close returns (`ReturnSeries.from_prices`) on the window's complete grid
# per horizon (`aligned.aligned_returns`): one row per period, blank where an instrument has no
# return. The table counts each instrument's returns per horizon.

# %%
returns = {
    h: aligned.aligned_returns(frames, params.instruments, h, start_ns, end_ns) for h in HORIZONS
}
for h, table in returns.items():
    print(f"{h} s: {len(table.ts_ns)} grid rows; returns per instrument:")
    print(aligned.returns_frame(table).count().to_string())

# %% [markdown]
# ## 3. Correlation heatmaps
#
# **Derived:** the pairwise-complete Pearson matrix per horizon (`correlation_matrix`), drawn on
# a diverging scale centred on 0 in clustered order (`aligned.clustered_order`: single linkage at
# `CLUSTER_DISTANCE`, so a cluster is a block on the diagonal). A blank cell is a pair with under
# two overlapping returns or a constant series. The per-venue matrices follow as tables
# (`aligned.by_venue`): the same entries, restricted to one venue's instruments.

# %%
matrices = {h: correlation_matrix(table) for h, table in returns.items()}
venue_matrices = {h: aligned.by_venue(matrix) for h, matrix in matrices.items()}
for h, matrix in matrices.items():
    ordered = aligned.matrix_frame(matrix, aligned.clustered_order(matrix, CLUSTER_DISTANCE))
    heatmap = go.Figure(
        go.Heatmap(
            z=ordered.to_numpy(),
            x=list(ordered.columns),
            y=list(ordered.index),
            colorscale="RdBu",
            zmid=0,
        )
    )
    heatmap.update_layout(title=f"Return correlation, {h} s bars (clustered order)")
    heatmap.show()
    for venue, per_venue in venue_matrices[h].items():
        print(f"{h} s, {venue}:")
        print(aligned.matrix_frame(per_venue).to_string())

# %% [markdown]
# ## 4. Rolling correlation against the anchor
#
# **Derived:** each instrument's correlation with `ANCHOR` over the trailing `ROLLING_WINDOW_S`
# of 1 m returns (`aligned.rolling_vs_anchor` -> `research.domain.correlation
# .rolling_correlation`), pairwise-complete inside the window and blank until the window holds at
# least half its rows as complete pairs. Known limit: the rolling loop is O(rows x window) --
# fine for days of 1 m returns (upgrade path in the function's docstring).

# %%
rolling = aligned.rolling_vs_anchor(returns[HORIZONS[0]], ANCHOR, ROLLING_WINDOW_S)
rolling_fig = go.Figure()
for iid in rolling.columns:
    rolling_fig.add_trace(
        go.Scattergl(
            x=inspection.plot_axis(rolling.index), y=rolling[iid], name=iid, connectgaps=False
        )
    )
rolling_fig.update_layout(
    title=f"Rolling {ROLLING_WINDOW_S} s correlation vs {ANCHOR} (1 m returns)"
)
rolling_fig.show()
print("rolling points per instrument:")
print(rolling.count().to_string())

# %% [markdown]
# ## 5. Clusters and merge distances
#
# **Derived:** per venue, the single-linkage clusters of the 1 m matrix at `CLUSTER_DISTANCE`
# (`cluster`) and the whole dendrogram as an ordered list (`merge_order`): each merge with its
# distance `1 - rho`, closest first; a pair with no correlation never merges, so the list stops
# early. Clusters are per venue because a backtest runs one venue (§7). Numpy-only single linkage,
# O(n^3): fine for a collected universe of up to ~100 instruments.

# %%
clusters = {}
for venue, per_venue in venue_matrices[HORIZONS[0]].items():
    clusters[venue] = cluster(per_venue, CLUSTER_DISTANCE)
    print(f"{venue}: clusters at 1 - rho <= {CLUSTER_DISTANCE}: {clusters[venue]}")
    for step in merge_order(per_venue):
        print(f"  merge {list(step.left)} + {list(step.right)} at 1 - rho = {step.distance:.3f}")

# %% [markdown]
# ## 6. Funding and open-interest co-movement
#
# **Read:** `FundingRateUpdate` and `OpenInterest` rows (bounded reads). **Derived:** each
# funding rate per hour, `rate * 60 / interval` (`aligned.funding_per_hour`: dYdX and Hyperliquid
# pay every 60 minutes, Bybit per symbol, e.g. 480; a row without an interval is blank, never assumed), its
# last value per `FUNDING_BUCKET_S` bucket, and the correlation of those levels across
# instruments (`aligned.funding_matrix`); and the relative open-interest change per `OI_BUCKET_S`
# bucket and its correlation (`aligned.oi_change_matrix`). The distinct intervals each instrument
# carried are printed first: a venue that changes its schedule shows two. Bybit spot has no
# funding or open interest: its column is blank.

# %%
funding = {iid: frames.funding(iid, start=start_ns, end=end_ns) for iid in params.instruments}
open_interest = {
    iid: frames.open_interest(iid, start=start_ns, end=end_ns) for iid in params.instruments
}
for iid, rows in funding.items():
    print(f"{iid}: funding intervals (min) {list(aligned.funding_intervals(rows))}")
levels = aligned.funding_levels(funding, FUNDING_BUCKET_S, start_ns, end_ns)
levels_fig = go.Figure()
for iid in levels.columns:
    levels_fig.add_trace(
        go.Scatter(x=inspection.plot_axis(levels.index), y=levels[iid], name=iid, mode="markers")
    )
levels_fig.update_layout(title=f"Funding rate per hour, last per {FUNDING_BUCKET_S} s bucket")
levels_fig.show()
for name, matrix in (
    ("funding level", aligned.funding_matrix(funding, FUNDING_BUCKET_S, start_ns, end_ns)),
    ("OI change", aligned.oi_change_matrix(open_interest, OI_BUCKET_S, start_ns, end_ns)),
):
    frame = aligned.matrix_frame(matrix)
    co_movement = go.Figure(
        go.Heatmap(
            z=frame.to_numpy(),
            x=list(frame.columns),
            y=list(frame.index),
            colorscale="RdBu",
            zmid=0,
        )
    )
    co_movement.update_layout(title=f"{name} correlation")
    co_movement.show()
    print(f"{name}:")
    print(frame.to_string())

# %% [markdown]
# ## 7. Feed a cluster into a backtest
#
# The watchlist idea (Story 1.3), restated on this epic's types: a cluster of instruments that
# move together is one bet taken several times, so a portfolio takes one instrument per cluster;
# a cluster is also a ready-made multi-instrument universe for a strategy that trades
# co-movement. The `BacktestRunner` port (`research.application.backtest_runner.NodeRunner`) runs
# one strategy per instrument of a `RunSpec` over one window. A `RunSpec` is single-venue (its
# Known limit: one simulated venue per run), which is why §5 clusters per venue.
#
# The cell below builds -- and does not run -- the spec for the cluster holding `ANCHOR`
# (`aligned.anchor_cluster`) with the OFI strategy. To run it:
#
# ```python
# from research.application.backtest_runner import NodeRunner
#
# result = NodeRunner().run(spec)  # one RunResult: equity, trades, metrics
# results = NodeRunner().sweep(spec, [{"ofi_threshold": t} for t in (1.5, 2.0, 2.5)])
# ```
#
# The run reads the catalog's snapshots over `START`/`END` on `ts_init` (the backtest's clock),
# while this notebook's frames window on `ts_event`, so the edges can differ by up to
# `kernel.clocks.MAX_TS_INIT_SKEW_NS`.

# %%
spec = RunSpec(
    catalog_path=params.catalog_path,
    instrument_ids=aligned.anchor_cluster(clusters, ANCHOR),
    start=params.start,
    end=params.end,
    strategy_path="research.strategies.ofi_strategy:OFIStrategy",
    config_path="research.strategies.ofi_strategy:OFIStrategyConfig",
)
print(spec)

# %% [markdown]
# ## 8. Cross-venue
#
# For each asset in `INSTRUMENTS` (once per asset), `CatalogFrames.same_symbol` finds its legs on
# every venue through `kernel.venues.asset_key`: the base, the quote class (USD, USDC and USDT are
# one class, a documented table in `kernel.venues`) and perp vs spot. An id the table cannot read
# (a dated future, an odd spot quote) matches nothing. A venue with no leg, or whose legs hold no
# snapshot in the window, prints "not collected in this window"; nothing is drawn for it.
#
# **Derived** (`aligned.cross_venue`), per pair of legs on the window's 1 s grid:
#
# - the mid basis in basis points (`basis_bps`: `(a / b - 1) * 1e4`, blank where either mid is
#   missing or crossed). USD, USDC and USDT are one class for matching only: the basis still
#   carries the USDT/USDC spread;
# - the lead-lag cross-correlation of 1 s mid returns for lags ±1..±`MAX_LAG_S` (`lead_lag`,
#   pairwise-complete) and its peak in words -- "BYBIT leads DYDX by 2 s" means Bybit's return at
#   t correlates most with dYdX's at t + 2 s. A lag needs 30 overlapping returns of its own
#   (`MIN_LEAD_LAG_PAIRS`), and a peak is stated only above its noise band, 2 / sqrt(pairs)
#   (`NOISE_Z`); otherwise the sentence says no lead is stated;
# - the funding differential per hour (per-hour rate of `a` minus `b`, per `VOLUME_BUCKET_S`);
# - each venue's share of the asset's traded volume (base units) per `VOLUME_BUCKET_S` bucket --
#   blank in a bucket where some venue observed no second, so a missing venue never reads as 0 %.
#
# **Two clocks.** The 1 s grid is keyed on each snapshot's `ts_event`, which is the venue's time
# on Bybit and Hyperliquid but our arrival time on dYdX (its book carries no venue time;
# `platform/CLAUDE.md` DATA-01). A lead or lag against dYdX therefore also contains the latency
# between the venue and our collector: a venue that "leads dYdX by 1 s" may simply be stamped
# before dYdX's data reached us. Compare two venue-timed legs (Bybit vs Hyperliquid) for a
# clock-clean lead.

# %%
cross = {}
for iid in aligned.distinct_assets(frames, params.instruments):
    view = cross[iid] = aligned.cross_venue(
        frames, iid, start_ns, end_ns, MAX_LAG_S, VOLUME_BUCKET_S
    )
    for line in aligned.summary_lines(view):
        print(line)
    for pair in view.pairs:
        pair_fig = make_subplots(
            rows=3,
            cols=1,
            subplot_titles=["basis (bps)", "lead-lag rho by lag (s)", "funding differential / h"],
        )
        pair_fig.add_trace(
            go.Scattergl(
                x=inspection.plot_axis(pair.basis_bps.index),
                y=pair.basis_bps,
                name="basis",
                connectgaps=False,
            ),
            1,
            1,
        )
        pair_fig.add_trace(
            go.Bar(x=[lag for lag, _ in pair.lead_lag], y=[rho for _, rho in pair.lead_lag]), 2, 1
        )
        pair_fig.add_trace(
            go.Scatter(
                x=inspection.plot_axis(pair.funding_diff.index),
                y=pair.funding_diff,
                mode="markers",
            ),
            3,
            1,
        )
        pair_fig.update_layout(title=f"{pair.a} vs {pair.b}: {pair.sentence}", height=750)
        pair_fig.show()
    if view.pairs:
        share = go.Figure()
        for venue in view.venues:
            share.add_trace(
                go.Bar(
                    x=inspection.plot_axis(view.volume_share.index),
                    y=view.volume_share[venue],
                    name=venue,
                )
            )
        share.update_layout(title=f"{iid}: traded volume share per venue", barmode="stack")
        share.show()
        print(view.volume_share.to_string())
