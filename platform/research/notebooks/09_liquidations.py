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
# # 09 Liquidations and forced flow
#
# What the archived liquidations say about one Bybit LINEAR instrument: the cascade episodes the
# strategies trade and what the price did after each, how levered the liquidated crowd was, how
# much of the traded volume was forced, how many liquidations the trade archive shows as their
# forced trade, the trade delta with and without the forced flow, the liquidations against the
# open-interest change, and whether another venue's cascades lead or lag.
#
# Every number is `research.application.liquidations.liquidation_study`'s (Story 33.13): the
# cascades are the one `kernel.indicators.LiquidationCascade` replayed (never a second
# definition), the organic delta is `kernel.indicators.organic_delta_units` (the chart's 33.6
# formula at second resolution) and the forced share `kernel.indicators.units_ratio`; every sum is
# exact in integer units. No cell computes anything.
#
# **Inputs and their limits.** Liquidations exist for Bybit LINEAR only (Hyperliquid has no
# market-wide feed, Story 33.2), and every stored price is the **bankruptcy** price, not the fill
# price (audit D-148). An instrument without the feed says so and shows no row -- never a zero.
# Gaps stay gaps (DATA-01): a second, a mark or an OI bucket that was never observed is blank.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`): `CATALOG_PATH`, `CANDLES_DIR`,
# `START`, `END`. The rest are set here; each can also be given as JSON in `NOTEBOOK_<NAME>`
# (`_params.setting`):
#
# - `INSTRUMENT` -- the instrument studied, by default `BTCUSDT-LINEAR.BYBIT`;
# - `STUDY` -- overrides of `LiquidationStudyConfig`'s fields (`{}` for its defaults): the cascade
#   detector's `window_s`, `baseline_s`, `intensity_threshold` and `decay_ratio` (the cascade
#   strategy's defaults), `max_mark_age_s` (the oldest mark an implied leverage takes),
#   `match_tol_s` (the trade match's window), `oi_bucket_s` and `max_lag_s` (the cross-venue
#   pairing).
#
# Memory (MEM-01): the study reads one UTC day at a time -- the day's snapshots, liquidations,
# trades and marks -- and keeps only the reduced columns.

# %%
import plotly.graph_objects as go
from _params import Params
from _params import setting
from research.application.frames import CatalogFrames
from research.application.liquidations import FORWARD_HORIZONS_S
from research.application.liquidations import LiquidationStudyConfig
from research.application.liquidations import liquidation_study
from research.application.ports import window_ns


params = Params.from_env()
start_ns, end_ns = window_ns(params.start, params.end)
frames = CatalogFrames(params.catalog_path, params.candles_dir)
INSTRUMENT = setting("INSTRUMENT", "BTCUSDT-LINEAR.BYBIT")
STUDY = setting("STUDY", {})
config = LiquidationStudyConfig(**STUDY)
study = liquidation_study(frames, INSTRUMENT, start_ns, end_ns, config)
print(f"{INSTRUMENT}, {params.start} -> {params.end}, {config}")
print("\n".join(study.lines()))

# %% [markdown]
# ## 2. Cascade episodes and what followed
#
# One row per episode of the replayed detector: its start and end, its direction (`side` names the
# liquidated position: `long` is a cascade of forced sells, the price falling), its length, its
# peak rate and notional (quote currency, at the bankruptcy price), and the 1 s mid's return 1, 5,
# 15 and 60 minutes after it ended (`fwd_*`). An episode still open at the window's end, an end
# second without a mid or a horizon past the window has a blank return.
#
# **Read it as a sample, not a law:** a handful of episodes is a handful of observations; a
# return after one cascade says what happened that time.

# %%
episodes = study.episodes
horizons = list(FORWARD_HORIZONS_S)
print(episodes.to_string(index=False))
episode_fig = go.Figure(
    [
        go.Bar(x=horizons, y=list(episodes.loc[k, horizons]), name=f"episode {k}")
        for k in episodes.index
    ]
)
episode_fig.update_layout(title="Mid return after each episode's end", barmode="group")
episode_fig.show()

# %% [markdown]
# ## 3. Implied leverage
#
# Each liquidation's `mark / |mark - bankruptcy price|` against the latest mark at most
# `max_mark_age_s` before it, and its distribution per UTC day (`count` is every liquidation,
# `finite` those with a leverage, `wrong_side` those whose bankruptcy price is not on the loss side
# of the mark -- a long at or above it, a short at or below it). A liquidation without a fresh
# mark or on the wrong side has no leverage and is counted, never dropped.
#
# **Read:** this is not the leverage the traders chose. A position is liquidated when the mark
# reaches its liquidation price, where what is left is the maintenance margin (plus the closing
# fee), and the bankruptcy price lies that margin further on; so at the trigger this ratio is about
# `1 / (maintenance margin rate + fee rate)` for an isolated position -- it reflects Bybit's risk
# tier for the position's size, not its opening leverage (audit D-221). A cross-margined
# position's distance depends on the whole account.

# %%
print(study.leverage.per_day.to_string())
leverage_fig = go.Figure(go.Histogram(x=study.leverage.per_liquidation["leverage"], nbinsx=60))
leverage_fig.update_layout(title="Implied leverage per liquidation", xaxis_title="leverage")
leverage_fig.show()

# %% [markdown]
# ## 4. Forced share of the traded volume
#
# Per minute and per hour, the liquidated size over the size the 1 s snapshots folded
# (`buy_volume + sell_volume`), with the seconds observed: a minute nothing traded in has no share,
# never 0. A share above 1 means the snapshots missed trades in that bucket (a gap), not more
# forced volume than traded.

# %%
print(study.forced_share.per_hour.to_string())
share_fig = go.Figure(
    go.Scatter(
        x=study.forced_share.per_minute.index,
        y=study.forced_share.per_minute["share"],
        mode="lines+markers",
    )
)
share_fig.update_layout(title="Forced share of traded volume per minute", yaxis_tickformat=".1%")
share_fig.show()

# %% [markdown]
# ## 5. Liquidations matched to their forced trade
#
# Each liquidation against the trade archive: a trade of exactly its size, on the forced side (a
# liquidated long is a seller-aggressor trade), within `match_tol_s` of it, each trade used once
# (the verification oracle's rule, restated). `offset_s` is when the trade printed against the
# liquidation's own stamp.
#
# **Read:** an unmatched liquidation is not a missing trade -- a forced order filled in pieces
# matches none of them (audit D-222), so the share is a lower bound.

# %%
print(
    f"matched {study.match.matched} of {study.match.total}"
    f" (share {study.match.share if study.match.share is not None else 'n/a'})"
)
match_fig = go.Figure(go.Histogram(x=study.match.per_liquidation["offset_s"], nbinsx=40))
match_fig.update_layout(title="Forced trade time minus liquidation time (s)")
match_fig.show()

# %% [markdown]
# ## 6. Organic against raw delta
#
# Each minute's trade delta (`buy - sell`) and its organic delta, the same with the liquidated
# size taken out of the side it forced (a long liquidation out of the sells, a short one out of the
# buys). A liquidation is placed in the snapshot second of its own venue time, the capture's rule
# for trades; one in a second without a snapshot is counted `unattributed` (in the sample above),
# never moved to a neighbour.

# %%
minutes = study.organic_minutes
delta_fig = go.Figure(
    [
        go.Scatter(x=minutes.index, y=minutes["delta"], name="delta", line_shape="hv"),
        go.Scatter(x=minutes.index, y=minutes["organic"], name="organic delta", line_shape="hv"),
    ]
)
delta_fig.update_layout(title="Trade delta per minute, raw and without the forced flow")
delta_fig.show()

# %% [markdown]
# ## 7. Liquidations against the open-interest change
#
# Per `oi_bucket_s` bucket: the liquidated size and notional, the open interest's change (its last
# reading against the previous bucket's; blank where either has none), `deleveraging` where the OI
# fell while positions were liquidated, and the liquidated share of that drop.

# %%
print(study.vs_oi.to_string())
oi_fig = go.Figure(
    [
        go.Bar(x=study.vs_oi.index, y=study.vs_oi["liquidation_size"], name="liquidated size"),
        go.Scatter(x=study.vs_oi.index, y=study.vs_oi["oi_change"], name="OI change"),
    ]
)
oi_fig.update_layout(title="Liquidated size and open-interest change per bucket")
oi_fig.show()

# %% [markdown]
# ## 8. Cross venue
#
# The episodes paired with the same asset's leg on another venue by start, within `max_lag_s`
# (positive lag: this instrument's cascade started first). Hyperliquid has no liquidation feed,
# so its side is empty and the line says so; the pairing runs unchanged once a venue gains one.

# %%
print("\n".join(study.cross_venue.lines()))

# %% [markdown]
# ## 9. Reading guide
#
# - **Bankruptcy, not fill.** Every price and notional here is at the bankruptcy price; the
#   forced order filled at the book. Notionals are an approximation (audit D-148) and the
#   leverage reflects the venue's maintenance-margin tiers, not the traders' choice (D-221).
# - **Bybit LINEAR only.** No other venue has a liquidation feed: the cross-venue section is empty
#   by construction, not because the other venue had no cascade.
# - **Episodes are few.** A window of days holds a few cascades; read `fwd_*` as individual cases.
#   The detector starts cold at `START`, so no episode can open in the window's first
#   `baseline_s` (an hour by default).
#   Notebook 08's OFI `fade` and `follow` gates run the same detector with the same parameters,
#   but advance it at each snapshot rather than on whole seconds, so their episodes' edges can
#   differ by about a second and a borderline episode may exist on one side only (audit D-223).
# - **The organic delta is placed by venue time.** A forced trade stamped in the next second
#   nets there (audit D-220); the minute view hides most of that boundary. "Unattributed" counts
#   the liquidations of a second with no snapshot row, or with two.
# - **A gap is blank.** A second without a snapshot, a mark older than `max_mark_age_s` or an OI
#   bucket without a reading is NaN, never a carried value or a zero.
