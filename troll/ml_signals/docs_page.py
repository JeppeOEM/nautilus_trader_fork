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
Signal Atlas: the /docs page -- a static indicator reference + engineering knowledge base,
served self-hosted next to Rankings/Live signals in dashboard.py's nav bar.

Entirely static content (no Redis/catalog reads) -- every formula/cadence/file:line below
was verified against the actual source (ml_signals/indicators.py, ranking_engine/engine.py,
ranking_engine/price_series.py, ml_signals/catalog_stats.py, ml_signals/ranking_columns.py,
ml_signals/metrics_computer.py) or the troll/docs/*.md files, as of the bmad branch,
2026-09-13. Client-side hash-routed single page (HEAD + BODY concatenated around
dashboard.py's own _NAV, so this page's top bar stays identical to every other page --
see dashboard.py's docs_handler).

Dark theme only, matching the rest of the app's fixed palette (dashboard.py's _CSS) rather
than the light/adaptive theme this content started life with as a standalone Claude
Artifact -- this is one more page of the same terminal-styled tool, not a separate product.
"""

HEAD = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Signal Atlas</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --border:#21262d; --border-strong:#30363d;
  --ink:#c9d1d9; --muted:#8b949e;
  --accent:#58a6ff; --accent-ink:#79c0ff; --accent-bg:rgba(88,166,255,.14);
  --pos:#3fb950; --pos-bg:rgba(63,185,80,.14);
  --neg:#f85149; --neg-bg:rgba(248,81,73,.14);
  --warn:#e3b341; --warn-bg:rgba(227,179,65,.14);
  --tag-live:#79c0ff; --tag-live-bg:rgba(121,192,255,.14);
  --tag-rolling:#58a6ff; --tag-rolling-bg:rgba(88,166,255,.14);
  --tag-slow:#d2a8ff; --tag-slow-bg:rgba(210,168,255,.14);
  --tag-static:#8b949e; --tag-static-bg:rgba(139,148,158,.14);
  --code-bg:#0d1117; --code-ink:#c9d1d9;
}
*{box-sizing:border-box}
body{ margin:0; background:var(--bg); color:var(--ink); font-family:monospace; font-size:13.5px; line-height:1.55; }
h1,h2,h3,h4{ font-weight:600; letter-spacing:-.01em; color:var(--ink); }
a{ color:var(--accent); text-decoration:none; }
a:hover{ text-decoration:underline; }
::selection{ background:var(--accent-bg); color:var(--accent-ink); }

.shell{ display:flex; min-height:calc(100vh - 40px); }
.side{
  width:280px; flex:0 0 280px; border-right:1px solid var(--border);
  background:var(--panel); padding:16px 14px 40px; position:sticky; top:0;
  align-self:flex-start; max-height:100vh; overflow-y:auto;
}
.main{ flex:1 1 auto; min-width:0; padding:24px 36px 70px; }
@media (max-width:860px){
  .shell{ flex-direction:column; }
  .side{ width:100%; flex:none; position:static; max-height:none; border-right:none; border-bottom:1px solid var(--border); padding:12px 14px 16px; }
  .main{ padding:18px 14px 50px; }
}

.brand{ display:flex; align-items:baseline; gap:8px; margin:2px 2px 4px; }
.brand b{ font-size:14.5px; letter-spacing:.02em; color:var(--ink); }
.brand span{ color:var(--muted); font-size:11px; }
.tagline{ color:var(--muted); font-size:12px; margin:0 2px 14px; line-height:1.5; }

.tabs{ display:flex; gap:6px; margin:0 0 12px; }
.tabbtn{
  flex:1; text-align:center; padding:7px 6px; border-radius:5px; border:1px solid var(--border);
  background:var(--bg); color:var(--muted); font-size:11px; letter-spacing:.03em;
  cursor:pointer; text-transform:uppercase; font-family:monospace;
}
.tabbtn.active{ background:var(--accent-bg); color:var(--accent-ink); border-color:var(--accent); }

.filter{
  width:100%; padding:7px 9px; border-radius:5px; border:1px solid var(--border);
  background:var(--bg); color:var(--ink); font-size:12.5px; margin-bottom:12px; font-family:monospace;
}
.filter:focus{ outline:1px solid var(--accent); outline-offset:1px; }

.navgroup{ margin-bottom:4px; }
.navgroup-h{
  font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
  padding:10px 6px 4px;
}
.navitem{
  display:flex; align-items:center; gap:8px; padding:5px 6px; border-radius:4px;
  cursor:pointer; color:var(--ink); font-size:12.5px;
}
.navitem:hover{ background:var(--bg); }
.navitem.active{ background:var(--accent-bg); color:var(--accent-ink); }
.navdot{ width:7px; height:7px; border-radius:50%; flex:0 0 auto; }

.intro{ max-width:720px; margin-bottom:26px; }
.intro h1{ font-size:22px; margin:0 0 8px; }
.intro p{ color:var(--ink); margin:0 0 6px; }
.intro .meta{ color:var(--muted); font-size:12px; margin-top:10px; }

.grid-group{ margin-bottom:28px; }
.grid-group h2{ font-size:14px; margin:0 0 3px; color:var(--ink); }
.grid-group .gdesc{ color:var(--muted); font-size:12.5px; margin:0 0 12px; max-width:660px; }
.cards{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:10px; }
.card{
  background:var(--panel); border:1px solid var(--border); border-radius:6px;
  padding:13px 13px 11px; cursor:pointer; display:flex; flex-direction:column; gap:7px;
}
.card:hover{ border-color:var(--border-strong); }
.card h3{ font-size:13.5px; margin:0; color:var(--ink); }
.card p{ margin:0; color:var(--muted); font-size:12px; line-height:1.45; flex:1; }

.pill{
  display:inline-flex; align-items:center; gap:5px; font-size:10px; font-weight:600;
  padding:3px 7px; border-radius:99px; letter-spacing:.03em; text-transform:uppercase;
  width:fit-content;
}
.pill.live{ background:var(--tag-live-bg); color:var(--tag-live); }
.pill.rolling{ background:var(--tag-rolling-bg); color:var(--tag-rolling); }
.pill.slow{ background:var(--tag-slow-bg); color:var(--tag-slow); }
.pill.static{ background:var(--tag-static-bg); color:var(--tag-static); }
.pill.status-done{ background:var(--pos-bg); color:var(--pos); }
.pill.status-open{ background:var(--neg-bg); color:var(--neg); }
.pill.status-deferred{ background:var(--warn-bg); color:var(--warn); }

.crumb{ color:var(--muted); font-size:11.5px; margin-bottom:10px; }
.crumb a{ color:var(--muted); }
.crumb a:hover{ color:var(--accent-ink); }
.dhead{ display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:4px; }
.dhead h1{ font-size:21px; margin:0 0 6px; }
.dhead .tagline{ font-size:13px; margin:0; max-width:600px; color:var(--ink); }

.infogrid{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:1px;
  background:var(--border); border:1px solid var(--border); border-radius:6px;
  overflow:hidden; margin:18px 0 24px;
}
.infocell{ background:var(--panel); padding:10px 13px; }
.infocell .k{ font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:4px; }
.infocell .v{ font-size:12.5px; color:var(--ink); }
.chiprow{ display:flex; flex-wrap:wrap; gap:6px; }
.chip{
  font-size:10.5px; padding:3px 7px; border-radius:4px; background:var(--bg);
  border:1px solid var(--border); color:var(--ink);
}

.formula{
  background:var(--code-bg); color:var(--code-ink); border:1px solid var(--border);
  border-radius:6px; padding:13px 15px; font-size:12.5px; overflow-x:auto;
  margin:0 0 20px; white-space:pre;
}

.sec{ margin-bottom:24px; max-width:760px; }
.sec h2{ font-size:12.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin:0 0 9px; }
.sec p{ margin:0 0 9px; color:var(--ink); }
.sec ul{ margin:0 0 9px; padding-left:19px; }
.sec li{ margin-bottom:5px; }
.sec table{ border-collapse:collapse; width:100%; margin:5px 0 15px; font-size:12.5px; }
.sec th,.sec td{ text-align:left; padding:6px 9px; border-bottom:1px solid var(--border); font-size:12px; }
.sec th{ color:var(--muted); font-weight:600; text-transform:uppercase; font-size:10px; letter-spacing:.04em; background:var(--panel); }
.sec .callout{
  border-left:2px solid var(--accent); background:var(--accent-bg); color:var(--accent-ink);
  padding:9px 13px; border-radius:0 5px 5px 0; font-size:12.5px; margin:0 0 15px;
}
.sec code{ background:var(--panel); padding:1px 5px; border-radius:3px; font-size:12px; }
.sec .pos{ color:var(--pos); font-weight:600; }
.sec .neg{ color:var(--neg); font-weight:600; }

.refs{ margin-top:24px; padding-top:14px; border-top:1px solid var(--border); }
.refs h2{ font-size:10.5px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 7px; }
.refline{ display:block; font-size:11.5px; color:var(--muted); margin-bottom:3px; }
.related{ margin-top:16px; display:flex; flex-wrap:wrap; gap:8px; }
.related a{
  font-size:11.5px; text-decoration:none; padding:5px 9px; border-radius:5px;
  border:1px solid var(--border); background:var(--panel); color:var(--ink); cursor:pointer;
}
.related a:hover{ border-color:var(--border-strong); color:var(--accent-ink); }

figure.diagram{ margin:0 0 24px; }
figure.diagram svg{ width:100%; height:auto; display:block; color:var(--muted); }
figure.diagram text{ fill:currentColor; }
figcaption{ color:var(--muted); font-size:11.5px; margin-top:8px; }
</style>
</head>
<body>
"""

BODY = """<div class="shell">
  <nav class="side" id="side"></nav>
  <main class="main" id="main"></main>
</div>

<script>
/* =========================================================================================
   CONTENT DATA -- source-of-truth branch: bmad, as read 2026-09-13.
   ========================================================================================= */

var CADENCE_META = {
  live:    {label:"Live",    sub:"updates every ~1s snapshot"},
  rolling: {label:"Rolling", sub:"updates on a sliding window of recent ticks"},
  slow:    {label:"Slow",    sub:"refreshed on a fixed 60s+ cycle"},
  static:  {label:"Chart-only", sub:"computed on demand, not part of the live cadence"}
};

var IND_GROUPS = [
  {id:"book", name:"Live Book State", desc:"Pure functions of the current 1-second DydxSecondSnapshot — no memory of prior ticks. Shown on both the ranking table and the coin-detail page unless noted."},
  {id:"flow", name:"Order Flow", desc:"Derived from a rolling window of recent snapshots or tick-to-tick book changes. ranking_engine is the sole live computer of every one of these — the dashboard and bot_tui only ever read its output (SSOT-02)."},
  {id:"vol", name:"Volatility — three distinct measures, by design", desc:"This system deliberately runs three separate volatility computations with different windows and purposes. None is a scaled copy of another — conflating them is the single most common source of “why does Vol say something different here” confusion."},
  {id:"market", name:"Market", desc:"Price-history fields refreshed on the same slow cycle as catalog volatility, plus 24h USD volume polled independently from the venue."},
  {id:"chart", name:"Chart & Strategy Only", desc:"Computed at read-time for a specific view (the dashboard's per-coin chart, a backtest strategy) — never published to rankings:live, never shown on the ranking table."}
];

var INDICATORS = [
{
  id:"microprice", group:"book", name:"Microprice", cadence:"live", window:"per snapshot (≈ 1s)",
  owner:"stateless — pure function, no shared-owner concern",
  shownIn:["Coin detail (web + bot_tui)", "/data/live/{id} JSON"],
  tagline:"Size-weighted mid price, pulled toward whichever side of the book is thinner.",
  formula:"microprice = (bid_price×ask_size + ask_price×bid_size) / (bid_size + ask_size)",
  notes:[
    "Uses level-0 (best bid/ask) price and size only. When one side has more resting size than the other, microprice sits closer to the <em>thinner</em> side — the side more likely to move first — making it a better short-horizon fair-value estimate than the plain mid.",
    "Two implementations exist on purpose: a stateless function (<code>indicators.py:409</code>) for one-off reads, and a stateful <code>Microprice</code> <code>Indicator</code> class (<code>indicators.py:116</code>) with <code>.initialized</code> semantics for streaming contexts (chart replay, live_paper). Same formula, different call shape — never a second, independently-written formula.",
    "Is <b>not</b> a ranking-table column in either UI — only its derivative, <a data-nav=\\"i:microprice_lean\\">Microprice Lean</a>, appears there indirectly (moved to history-only, see that page). Raw microprice is coin-detail only."
  ],
  refs:["ml_signals/indicators.py:116 (Microprice class)","ml_signals/indicators.py:409 (microprice() function)"],
  related:["microprice_lean","spread","mid_price"]
},
{
  id:"microprice_lean", group:"book", name:"Microprice Lean (“u lean”)", cadence:"live", window:"per snapshot (≈ 1s)",
  owner:"ranking_engine (sole live computer)",
  shownIn:["Coin detail (web + bot_tui)", "31-day history chart (/history/{id})"],
  tagline:"How far the size-weighted fair value has drifted from the plain mid — a directional book-tilt signal.",
  formula:"microprice_lean = microprice − mid_price",
  notes:[
    "Positive = book pressure tilts the fair value above the plain mid (bid side thinner — more supportive of price rising); negative = the opposite.",
    "<span class=\\"callout\\">Moved off the cross-instrument ranking table on 2026-09-13.</span> Before that date it sat on the ranking table as a bare column labeled “u lean” next to a dozen other columns — useful as a per-coin signal, but not something you scan across instruments to rank them, and it crowded the table. It now lives only on the single-coin page (web <code>/coin/{id}</code> and bot_tui's coin detail), plus the 31-day history chart, via <code>ranking_columns.py</code>'s <code>_HISTORY_ONLY_COLS</code> — the same mechanism already used to keep <code>rank</code> off the live table.",
    "Displayed in basis points client-side (<code>bpsFromPriceUnits(raw, price)</code>) — the value stored/published is a raw price-unit delta, not already scaled to bps. A script reading <code>/api/rankings</code> or <code>/data/live/{id}</code> directly must do that scaling itself."
  ],
  refs:["ranking_engine/engine.py:364 (fast metrics)","ml_signals/ranking_columns.py (_HISTORY_ONLY_COLS)","ml_signals/dashboard.py (IND_GROUPS “Live (1s book state)”)"],
  related:["microprice","mid_price"]
},
{
  id:"spread", group:"book", name:"Spread", cadence:"live", window:"per snapshot (≈ 1s)",
  owner:"stateless — pure function",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Best ask minus best bid, in raw price units.",
  formula:"spread = ask_prices[0] − bid_prices[0]",
  notes:[
    "None when either side of the book is empty (thin/no book) — never fabricated as zero.",
    "Displayed in basis points client-side on both the ranking table and coin detail (<code>bpsFromPriceUnits</code>), same normalization as Microprice Lean."
  ],
  refs:["ml_signals/indicators.py:431"],
  related:["microprice","mid_price"]
},
{
  id:"mid_price", group:"book", name:"Mid Price (“Price”)", cadence:"live", window:"per snapshot (≈ 1s)",
  owner:"stateless — pure function",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Plain average of best bid and best ask — the “Price” column shown everywhere.",
  formula:"mid_price = (bid_prices[0] + ask_prices[0]) / 2",
  notes:[
    "This is <em>not</em> microprice — no size weighting. It's the baseline every normalized bps/USD figure on the tables (spread, lean, CVD, volume delta) is scaled against."
  ],
  refs:["ml_signals/indicators.py:440"],
  related:["microprice","spread"]
},
{
  id:"obi", group:"book", name:"Order Book Imbalance (OBI 3 / 5 / 10)", cadence:"live", window:"per snapshot (≈ 1s)",
  owner:"ranking_engine — 3 live MultiLevelOBI instances per instrument (levels 3, 5, 10)",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Fraction of resting size on the bid side, across the top N price levels.",
  formula:"OBI(N) = sum(bid_sizes[:N]) / (sum(bid_sizes[:N]) + sum(ask_sizes[:N]))",
  notes:[
    "Range 0–1: <b>1.0</b> = all visible depth on the bid, <b>0.5</b> = balanced, <b>0.0</b> = all ask. The ranking table colors it green above 0.5, red below.",
    "Three separate instances run per instrument — <code>obi_3</code>/<code>obi_5</code>/<code>obi_10</code> — not one computation resliced three ways. A thin top level can disagree sharply with the 10-level view during a large resting order a few ticks back."
  ],
  refs:["ml_signals/indicators.py:234 (MultiLevelOBI)","ranking_engine/engine.py:348"],
  related:["ofi_raw","ofi_z"]
},
{
  id:"ofi_raw", group:"flow", name:"Order Flow Imbalance — raw (OFI 3 / 5 / 10)", cadence:"rolling", window:"rolling sum over the last 300 updates",
  owner:"ranking_engine — 3 live MultiLevelOFI instances per instrument (levels 3, 5, 10; window=300)",
  shownIn:["Ranking table (web + bot_tui, informational only)","Coin detail"],
  tagline:"Cont–Kukanov–Stoikov order flow imbalance, summed across the top N price levels.",
  formula:"at each level: price improves → count full new size\\nprice unchanged → count the size delta\\nprice worsens → count a full withdrawal (negative)\\ncontribution = bid_term − ask_term, summed across levels and over the rolling window",
  notes:[
    "Positive = net buy-side pressure at the top of book over the window; negative = net sell-side pressure.",
    "Informational column only — <b>OFI never affects an instrument's rank.</b> The ranking table's sort key is exclusively 24h volume or the cross-sectional <a data-nav=\\"i:vol_score\\">Volatility Score</a>, depending on Ranking Mode.",
    "The engine also runs a fourth, differently-configured OFI instance for the z-scored variant — see <a data-nav=\\"i:ofi_z\\">OFI10z</a>, not a rescaling of this one."
  ],
  refs:["ml_signals/indicators.py:269 (MultiLevelOFI)","ranking_engine/engine.py:346"],
  related:["ofi_z","obi","cvd"]
},
{
  id:"ofi_z", group:"flow", name:"OFI10z (z-scored)", cadence:"rolling", window:"window=50, z-scored over the last 3600 readings",
  owner:"ranking_engine — one MultiLevelOFI(levels=10, window=50, zscore_window=3600) instance",
  shownIn:["Ranking table (web + bot_tui, leading column, informational only)","Coin detail"],
  tagline:"Level-10 OFI, standardized against its own recent history so it's comparable across instruments of any scale.",
  formula:"z = (raw_ofi − mean(last 3600 raw readings)) / std(last 3600 raw readings)\\n(returns 0.0 when std is 0)",
  notes:[
    "A genuinely separate indicator instance from <a data-nav=\\"i:ofi_raw\\">ofi_10</a> — different <code>window</code> (50 vs 300) as well as the z-score wrapper. Don't expect ofi_10 and ofi_10_z to move in lockstep.",
    "Colored green above 0 / red below — the sign is what most viewers actually scan for, the magnitude tells you how unusual the current imbalance is versus the last hour or so of readings."
  ],
  refs:["ranking_engine/engine.py:345"],
  related:["ofi_raw","obi"]
},
{
  id:"cvd", group:"flow", name:"CVD (Cumulative Volume Delta)", cadence:"rolling", window:"rolling 300-snapshot buffer",
  owner:"ranking_engine — fed from the shared _SECOND_ROLLING buffer",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Total buy volume minus total sell volume across the engine's rolling 300-snapshot window.",
  formula:"cvd = sum(buy_volume for last 300 snapshots) − sum(sell_volume for last 300 snapshots)",
  notes:[
    "Raw base-token units server-side; normalized to USD client-side via <code>usdFromTokens(raw, price)</code> on both the ranking table and coin detail.",
    "Shares its rolling window with <a data-nav=\\"i:volume_counts\\">buy/sell count and avg trade size</a> — all four come from the same <code>trade_aggregates()</code> reduction over the same 300-entry buffer."
  ],
  refs:["ml_signals/indicators.py:454 (trade_aggregates)","ranking_engine/engine.py:349"],
  related:["volume_delta","volume_counts"]
},
{
  id:"volume_delta", group:"flow", name:"Volume Delta (“Vol d 60s”)", cadence:"live", window:"single latest snapshot only — not a rolling sum",
  owner:"stateless — pure function of the latest snapshot",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Buy minus sell volume for the single most recent 1-second snapshot — despite sitting next to CVD, this one is not a rolling figure.",
  formula:"volume_delta = latest_snapshot.buy_volume − latest_snapshot.sell_volume",
  notes:[
    "<span class=\\"callout\\">Label note:</span> the ranking table's column header reads “Vol d 60s” — that “60s” describes the table's own poll/refresh interval, <em>not</em> the underlying window. The value itself is always exactly one snapshot tick (≈ 1 second), the fastest-updating field on the whole table. Don't confuse this with <a data-nav=\\"i:cvd\\">CVD</a>, which genuinely does sum over 300 snapshots.",
    "Raw base-token units; normalized to USD client-side, same as CVD."
  ],
  refs:["ml_signals/indicators.py:449"],
  related:["cvd"]
},
{
  id:"volume_counts", group:"flow", name:"Buy / Sell Count & Avg Trade Size", cadence:"rolling", window:"rolling 300-snapshot buffer (same as CVD)",
  owner:"ranking_engine",
  shownIn:["Coin detail only"],
  tagline:"Trade counts per side, and mean trade size, over the same rolling window CVD uses.",
  formula:"avg_trade_size = (buy_volume + sell_volume) / (buy_count + sell_count), over the last 300 snapshots",
  notes:["Not shown on the ranking table in either UI — coin-detail only, in the “order flow (~5m rolling)” group."],
  refs:["ml_signals/indicators.py:454 (trade_aggregates)"],
  related:["cvd"]
},
{
  id:"vol_fast", group:"vol", name:"Volatility — Fast", cadence:"rolling", window:"last 300 live ticks (≈ 5 minutes)",
  owner:"ranking_engine, per instrument",
  shownIn:["Coin detail only (“Volatility & Market” group)"],
  tagline:"stdev of mid-price returns over the last 300 live snapshots — the fastest-reacting of the app's three volatility numbers.",
  formula:"volatility_fast = statistics.stdev(pct_returns) over the trailing 300 live-tick mid prices",
  notes:["Distinct from both <a data-nav=\\"i:vol_catalog\\">Vol(catalog)</a> and <a data-nav=\\"i:vol_score\\">Vol Score</a> by explicit design — three separate volatility computations exist in this codebase on purpose, not by accident. See the group note above."],
  refs:["ranking_engine/engine.py:352–359"],
  related:["vol_catalog","vol_score"]
},
{
  id:"vol_catalog", group:"vol", name:"Volatility — Catalog (“Vol(catalog)”)", cadence:"slow", window:"60s refresh; window grows from 0 up to a 25-hour cap",
  owner:"ranking_engine.PriceSeriesStore → ml_signals.catalog_stats.price_stats_from_series(), called every 60s by _slow_loop_task",
  shownIn:["Ranking table (web + bot_tui)","Coin detail","31-day history chart"],
  tagline:"stdev of consecutive per-second return percentages, over an in-memory price series backfilled from the Parquet catalog and retained up to 25 hours.",
  formula:"returns = diff(close_prices) / close_prices[:-1]\\nvolatility = stdev(returns)   — over whatever history is retained (0–25h)",
  notes:[
    "<span class=\\"callout\\">This is the column that used to be genuinely ambiguous.</span> Before 2026-09-13 the ranking table's header for this exact field was a bare <code>“Vol”</code> — one glance away from <code>“Vol Score”</code> and <code>“Vol24h”</code> on the very same row, with no indication of which of the app's three volatility numbers it was or how far back it looked. Relabeled to <b>Vol(catalog)</b> everywhere — ranking table, and matching the label the coin-detail page (both web and bot_tui) already used for this same field, so the name is now identical wherever it appears.",
    "The 25-hour figure is <code>PRICE_LOOKBACK_HOURS</code> (<code>ml_signals/metrics_computer.py</code>) — a memory/backfill cap, not a fixed calendar window like “1h” or “24h”. Right after a fresh start (or for a newly-added instrument) the window is whatever history has accumulated so far, growing toward the 25h cap over time.",
    "Also the field persisted to <code>metrics.db</code>'s <code>volatility</code> column, powering the 31-day per-coin history chart."
  ],
  refs:["ml_signals/catalog_stats.py:222 (price_stats_from_series)","ranking_engine/price_series.py:164 (PriceSeriesStore.stats)","ml_signals/metrics_computer.py:38 (PRICE_LOOKBACK_HOURS = 25.0)"],
  related:["vol_fast","vol_score"]
},
{
  id:"vol_score", group:"vol", name:"Volatility Score", cadence:"slow", window:"age-based rolling window, default 3600s (1h)",
  owner:"ranking_engine.VolatilityTracker (volatility.py) — a fourth, deliberately separate volatility computation",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Cross-sectional stdev of consecutive mid-price % returns, ranked against every other subscribed instrument. The sort key when Ranking Mode = “volatility.”",
  formula:"per instrument: stdev of mid-price % returns over an age-based window (default 3600s)\\n→ rows sorted by volatility_score descending when mode = “volatility”",
  notes:[
    "“Age-based” (not a fixed-length deque) specifically so the effective time span stays ≈ constant even if the live snapshot rate varies — a fixed-length buffer would silently shrink its real time coverage under a faster tick rate.",
    "Lookback is overridable via the <code>RANKING_VOLATILITY_LOOKBACK_SECONDS</code> environment variable without a code change.",
    "Always computed and published regardless of the active Ranking Mode — it's the sort key only when mode is “volatility”; under the default “volume” mode it's purely informational, same as OFI/OBI."
  ],
  refs:["ranking_engine/volatility.py:16–24","ranking_engine/engine.py (RANKING_VOLATILITY_LOOKBACK_SECONDS)"],
  related:["vol_fast","vol_catalog","volume24h"]
},
{
  id:"pct_change", group:"market", name:"pct_1h / pct_24h", cadence:"slow", window:"60s refresh, read from the same 25h price series as Vol(catalog)",
  owner:"ml_signals.catalog_stats.price_stats_from_series()",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"Percent change in mid price over the trailing 1h / 24h.",
  formula:"pct_change(H) = (latest_price − price_at(now − H)) / price_at(now − H) × 100",
  notes:["<code>None</code> — not zero, not extrapolated — until the retained price series genuinely spans that long. A freshly-added instrument shows <code>pct_24h = None</code> for up to 24 hours, by design (DATA-01: never fabricate a value that isn't really known yet)."],
  refs:["ml_signals/catalog_stats.py:222"],
  related:["vol_catalog","volume24h"]
},
{
  id:"volume24h", group:"market", name:"Volume24h (“Vol24h”)", cadence:"slow", window:"independent 60s poll",
  owner:"ranking_engine — direct dYdX indexer poll, deliberately not reused from the collector's own liquidity poll (AD-4: no cross-module network-I/O reuse)",
  shownIn:["Ranking table (web + bot_tui)","Coin detail"],
  tagline:"24-hour USD volume straight from the venue. The sort key when Ranking Mode = “volume” — the default.",
  formula:"polled every 60s from dYdX indexer's /v4/perpetualMarkets, field volume24H (already USD)",
  notes:["This is the actual default ranking order for the whole system: with Ranking Mode left on “volume,” every OFI/OBI/CVD/microprice column on the table is informational only — none of them move an instrument's position in the list."],
  refs:["ranking_engine/engine.py:152–167 (_fetch_volume_24h_json)"],
  related:["vol_score"]
},
{
  id:"book_features", group:"chart", name:"Book Microstructure (depth, imbalance, liquidity distance, cancel pressure)", cadence:"static", window:"computed on demand from a live-replayed OrderBook",
  owner:"ml_signals/book_features.py — chart_data.py's read-time replay",
  shownIn:["Web dashboard per-coin chart page only"],
  tagline:"A second, independent set of L2-derived features, built by replaying raw order-book deltas — not the stored DydxSecondSnapshot fields OBI/OFI use.",
  formula:null,
  notes:[
    "<b>depth_profile</b> — top-N bid/ask prices+sizes from a live <code>OrderBook</code> object (levels 1–10 default).",
    "<b>book_imbalance</b> — the same bid/(bid+ask) formula as OBI, per level plus an aggregate, but computed from a live replayed book object instead of the stored snapshot lists.",
    "<b>liquidity_distance</b> — price distance from best to where cumulative depth reaches a threshold (default 80%) of one side's total. Small = dense support/resistance nearby; large = a liquidity vacuum.",
    "<b>cancel_pressure</b> (<code>CancellationTracker</code>) — <code>(deleted_size − added_size) / (deleted_size + added_size)</code> at the best bid/ask over a rolling 200-event window. +1 = all cancellations, −1 = all additions. Only tracks ADD/DELETE at the <em>current</em> best price — UPDATE is ambiguous-direction and skipped.",
    "None of these reach <code>rankings:live</code> or either ranking table — chart page only."
  ],
  refs:["ml_signals/book_features.py","ml_signals/chart_data.py (compute_chart_series)"],
  related:["footprint","obi"]
},
{
  id:"footprint", group:"chart", name:"Footprint (resting order-book flow)", cadence:"static", window:"per-candle, per-price-band buckets",
  owner:"ml_signals/footprint.py",
  shownIn:["Web dashboard footprint chart only"],
  tagline:"Buckets resting order-book size changes — not executed trades — into per-candle, per-price-band cells.",
  formula:null,
  notes:[
    "dYdX's L2 deltas carry no order IDs, so a shrinking price level can't be told apart from a cancel vs. a fill — this is resting-size flow, explicitly not a trade footprint, by the module's own documented caveat.",
    "Each cell tracks gross <code>bid_added</code> / <code>bid_removed</code> / <code>ask_added</code> / <code>ask_removed</code> size (not just the net), so a churning level stays visible instead of netting to zero.",
    "No ranking/live-tick consumer — dashboard footprint chart only."
  ],
  refs:["ml_signals/footprint.py:16–29"],
  related:["book_features"]
},
{
  id:"ema_trend", group:"chart", name:"EMA Trend Lines (fast/slow)", cadence:"static", window:"computed over internally-aggregated 1-minute candles",
  owner:"ml_signals/chart_data.py, using Nautilus's own ExponentialMovingAverage indicator",
  shownIn:["Web dashboard per-coin chart page only"],
  tagline:"EMA-8 / EMA-21 crossover lines on 1-minute candles — reuses nautilus_trader's built-in indicator, not a custom EMA.",
  formula:null,
  notes:["Entirely a read-time chart-page replay — nothing here is persisted or fed into ranking. Consistent with the project rule to reach for a Nautilus built-in (<code>nautilus_trader.indicators</code>) before writing a custom one."],
  refs:["ml_signals/chart_data.py (compute_chart_series)"],
  related:["book_features"]
},
{
  id:"logistic_trend", group:"chart", name:"OnlineLogisticTrend", cadence:"static", window:"online, one SGD step per bar",
  owner:"strategy-only — fed manually by a Strategy subclass",
  shownIn:["Backtest / live_paper strategies only (example_strategy.py)"],
  tagline:"Online (incremental) logistic regression predicting P(next bar's return > 0) from the last N bar-to-bar returns.",
  formula:"z = weights · features + bias\\nP(up) = 1 / (1 + e⁻ᶻ)\\neach new bar: one SGD step trains on the PREVIOUS prediction now that the true outcome is known, then predicts the next probability",
  notes:[
    "<code>self.value</code> is that probability, 0.5 until <code>initialized</code> (i.e. until <code>lookback</code> returns have accumulated).",
    "Deliberately minimal: a single online SGD step per bar, no batch retraining, no persistence across restarts. Documented in-code as a ponytail shortcut with a named upgrade path (<code>sklearn.linear_model.SGDClassifier</code> + periodic refit) if it drifts on a long-running deployment.",
    "Never published to <code>rankings:live</code> — this is a strategy-internal signal, not a UI metric."
  ],
  refs:["ml_signals/indicators.py:41–113"],
  related:[]
}
];

var KB_GROUPS = [
  {id:"start", name:"Start Here"},
  {id:"ops", name:"Setup & Operations"},
  {id:"data", name:"Data & Storage"},
  {id:"backtest", name:"Backtesting & Strategies"},
  {id:"postmortem", name:"Postmortems"},
  {id:"fixed", name:"Fixed & Resolved"}
];

var KB = [
{
  id:"architecture", group:"start", name:"System Architecture", tagline:"What each module owns, and exactly how the pieces talk — Redis channels, SQLite, Parquet, HTTP.",
  html: function(){ return ""
  + "<figure class=\\"diagram\\">"
  + svgArchitecture()
  + "<figcaption>The full data path: the collector is the only thing that talks to dYdX for market data; ranking_engine is the only thing that computes ranking; every UI is a pure reader of Redis. live_paper is the one module sanctioned to run its own TradingNode against dYdX directly (AD-8).</figcaption>"
  + "</figure>"
  + "<div class=\\"sec\\"><h2>One paragraph</h2><p>A collector pulls live dYdX market data straight off the Rust adapter and writes it to a Nautilus-native Parquet catalog, publishing a live 1-second snapshot feed to Redis as it goes. A ranking engine reads that feed, scores every coin by volume or volatility, and publishes the result back to Redis. A web dashboard and a terminal UI both read the same two Redis feeds — never recomputing anything themselves — to show live charts and a watchlist. Indicators written once in <code>ml_signals</code> get reused unmodified in Jupyter research, Nautilus backtests, and a live paper-trading bot (<code>live_paper</code>), which publishes its own status to Redis so the TUI can monitor and start/stop it.</p></div>"
  + "<div class=\\"sec\\"><h2>Module map</h2><table><tr><th>Module</th><th>Role</th><th>Talks to</th></tr>"
  + "<tr><td><code>dydx_collector/</code></td><td>Captures live dYdX data → Parquet + <code>snapshots:raw</code></td><td>dYdX WS/REST, Redis (publish), catalog (write)</td></tr>"
  + "<tr><td><code>ml_signals/</code></td><td>Shared indicators + web dashboard + backtesting</td><td>catalog (read), Redis (read snapshots/rankings, publish ranking:control), metrics.db (read)</td></tr>"
  + "<tr><td><code>ranking_engine/</code></td><td>Sole computer of coin ranking</td><td>Redis (read snapshots/control, publish rankings:live), metrics.db (write), dYdX REST</td></tr>"
  + "<tr><td><code>live_paper/</code></td><td>The actual trading bot — TradingNode + Strategy, paper or gated real-money</td><td>dYdX WS/HTTP, Redis (bots:status/control)</td></tr>"
  + "<tr><td><code>bot_tui/</code></td><td>Keyboard-only terminal UI, interactive/on-demand</td><td>Redis (read rankings/snapshots/bots:status, publish ranking:control/bots:control)</td></tr>"
  + "</table><p>Module boundary rule (AD-4): every module downstream of the collector depends only on shared data types and Redis/HTTP contracts — never another module's internal state. <code>dydx_collector</code> never imports from anything downstream of it.</p></div>"
  + "<div class=\\"sec\\"><h2>Redis channel reference</h2><table><tr><th>Channel</th><th>Publisher</th><th>Subscriber</th><th>Payload</th></tr>"
  + "<tr><td><code>snapshots:raw</code></td><td>dydx_collector</td><td>ranking_engine, dashboard, bot_tui</td><td>One DydxSecondSnapshot per instrument per second</td></tr>"
  + "<tr><td><code>rankings:live</code></td><td>ranking_engine</td><td>dashboard, bot_tui</td><td>{mode, ranks:[...], stale_instrument_ids} on change + heartbeat</td></tr>"
  + "<tr><td><code>ranking:control</code></td><td>dashboard, bot_tui</td><td>ranking_engine</td><td>Mode-switch request, last-write-wins</td></tr>"
  + "<tr><td><code>bots:status</code></td><td>live_paper</td><td>bot_tui</td><td>Per-bot PnL/position/mode/heartbeat, every 5s</td></tr>"
  + "<tr><td><code>bots:control</code></td><td>bot_tui</td><td>live_paper</td><td>{bot_id, action: start|stop} — never a mode field</td></tr>"
  + "</table></div>"
  + "<div class=\\"sec\\"><h2>Deployment topology</h2><p>All services bind <code>127.0.0.1</code> only / <code>network_mode: host</code> — nothing is reachable without an SSH tunnel over Tailscale. <code>redis</code>/<code>collector</code>/<code>dashboard</code>/<code>ranking_engine</code>/<code>data_api</code>/<code>dozzle</code> start by default with <code>restart: always</code>. <code>live-paper</code> is explicit opt-in (<code>profiles: [\\"live-paper\\"]</code>, <code>make up-live-paper</code>) with a capped <code>on-failure:5</code> restart policy so a bad config can't crash-loop against dYdX's API. <code>bot_tui</code> is a one-shot interactive tool, never a background daemon.</p></div>"
  + "<div class=\\"sec\\"><h2>What's genuinely not finished</h2><ul>"
  + "<li><b>DummyStrategy is a wiring proof, not a tuned strategy</b> — by design, but means nothing here is validated to make money.</li>"
  + "<li><b>live_paper Cache persistence + TUI trades blotter/PnL chart</b> (backlog) — blocks real trade-history visibility across restarts.</li>"
  + "<li><b>orders_inflight() race guard has no test</b> proving it holds under a real concurrent-fill race.</li>"
  + "<li><b>EXTERNAL vs INTERNAL bar aggregation choice</b> picked defensively, never confirmed better against a real dYdX connection.</li>"
  + "<li><b>Real-money path (RealMoneyConfig)</b> exists and is gated but has never been exercised even once.</li>"
  + "</ul></div>";
  },
  refs:["troll/ARCHITECTURE.md"]
},
{
  id:"getting-started", group:"ops", name:"Getting Started", tagline:"Build the base image, configure instruments, deploy, and reach the dashboard remotely over Tailscale.",
  html: function(){ return ""
  + "<div class=\\"sec\\"><h2>First-time setup</h2><p>From <code>troll/</code>:</p><div class=\\"formula\\">make build-base   # ~15 min, once only — compiles Nautilus from source\\nmake up            # build collector image (seconds) and start collecting\\nmake logs          # tail live collector output\\nmake web           # open Dozzle log viewer (http://localhost:8080)</div></div>"
  + "<div class=\\"sec\\"><h2>Configure instruments</h2><p>Edit <code>troll/dydx_collector/config.toml</code> — hot-reloaded every <code>config_reload_seconds</code> (30s default), no restart needed:</p><div class=\\"formula\\">[[instruments]]\\nid = \\"BTC-USD-PERP.DYDX\\"\\nbar_intervals = [\\"1-MINUTE\\"]</div></div>"
  + "<div class=\\"sec\\"><h2>Run the dashboard</h2><p><code>make dashboard</code> in a separate terminal — <code>http://localhost:8765</code>. Reads directly from the catalog + Redis, no collector restart needed.</p></div>"
  + "<div class=\\"sec\\"><h2>Remote access via Tailscale + SSH tunnel</h2><p>Dashboard, Redis, and Dozzle all bind <code>127.0.0.1</code> only — nothing is reachable from the public internet or even the tailnet directly.</p><div class=\\"formula\\">ssh -N -L 8765:127.0.0.1:8765 -L 8080:127.0.0.1:8080 you@&lt;vps-tailscale-ip&gt;</div><p>Then open <code>http://localhost:8765</code> (dashboard) or <code>:8080</code> (Dozzle) locally. <code>-N</code> holds the tunnel open with no remote shell.</p>"
  + "<p><b>Feed-health alerts:</b> set <code>WATCHDOG_NTFY_URL</code> (e.g. an ntfy.sh topic) on the <code>collector</code> service to get a push notification if every subscribed instrument's book goes stale for 30s+ (OBS-01). Without it, the same condition is only logged.</p></div>"
  + "<div class=\\"sec\\"><h2>Inspect the catalog directly</h2><div class=\\"formula\\">from nautilus_trader.persistence.catalog import ParquetDataCatalog\\ncatalog = ParquetDataCatalog(\\"troll/dydx_collector/catalog\\")\\ncatalog.instruments()\\ncatalog.trade_ticks(instrument_ids=[\\"BTC-USD-PERP.DYDX\\"])</div></div>";
  },
  refs:["troll/README.md"]
},
{
  id:"bot-ops", group:"ops", name:"Bot Operations", tagline:"Starting/stopping the live paper bot, writing a new strategy, and spotting a stale feed without digging through logs.",
  html: function(){ return ""
  + "<div class=\\"sec\\"><h2>Two separate strategy paths — not interchangeable</h2><table><tr><th></th><th>Backtest / research</th><th>Live paper bot</th></tr>"
  + "<tr><td>Where</td><td><code>ml_signals/*_strategy.py</code></td><td><code>live_paper/strategy.py</code></td></tr>"
  + "<tr><td>Runtime</td><td><code>BacktestNode</code></td><td><code>TradingNode</code> (AD-8 exception)</td></tr>"
  + "<tr><td>Wiring</td><td><code>ImportableStrategyConfig</code>, string path</td><td>One strategy hardcoded in <code>node.py</code></td></tr>"
  + "<tr><td>Start/stop</td><td>One-shot process call</td><td>Long-running Docker container, Redis pub/sub or bot_tui</td></tr></table></div>"
  + "<div class=\\"sec\\"><h2>Starting/stopping live_paper</h2><div class=\\"formula\\">make up-live-paper   # start (background, live-paper profile)\\ndocker exec dydx-redis redis-cli PUBLISH bots:control '{\\"bot_id\\":\\"bot-01\\",\\"action\\":\\"stop\\"}'\\ndocker exec dydx-redis redis-cli PUBLISH bots:control '{\\"bot_id\\":\\"bot-01\\",\\"action\\":\\"start\\"}'</div><p>Stopping does <b>not</b> flatten an open position — no auto-flatten logic exists. A message with the wrong <code>bot_id</code> is silently ignored, so this is safe against a shared Redis instance with multiple bots.</p>"
  + "<p>Via <code>bot_tui</code>: <code>make tui</code> → Bots pane (<code>:bots</code>) → highlight → <code>s</code> to start/stop. Stopping a <em>running</em> bot opens a type-to-confirm prompt; starting doesn't. Press <code>v</code> on a bot's detail view to read its strategy source read-only; <code>i</code> to see its incidents log (restarts, feed interruptions), persisted in Redis (<code>bots:incidents:{bot_id}</code>, last 50 kept).</p></div>"
  + "<div class=\\"sec\\"><h2>Writing a new live strategy</h2><ol style=\\"padding-left:20px\\"><li>New <code>Strategy</code> + <code>StrategyConfig</code> pair in <code>live_paper/</code>, following <code>strategy.py</code>'s <code>DummyStrategy</code> as reference.</li><li>Swap the import/instantiation in <code>node.py</code>'s <code>build_node()</code>.</li><li>Add new tunables to both <code>PaperConfig</code>/<code>RealMoneyConfig</code> and <code>config.toml</code>. Never add a <code>mode</code> key — <code>load_paper_config()</code> hard-errors on it by design.</li><li>Rebuild — code is baked into the image, not bind-mounted.</li></ol></div>"
  + "<div class=\\"sec\\"><h2>Spotting downtime without digging through logs</h2><ul><li>A single coin's feed going stale shows as a <code>~ stale feed: SOL-USD-PERP.DYDX</code> banner in both bot_tui's Coins-pane breadcrumb and the web dashboard's status line — same <code>stale_instrument_ids</code> field, both UIs (SSOT-04).</li><li>A bot's own feed going stale, or the process restarting, is in its Incidents log (<code>i</code> key in bot_tui's Bot-detail).</li></ul><p>Neither parses log files — both are computed from data these processes already track.</p></div>";
  },
  refs:["troll/docs/BOT_OPERATIONS.md"]
},
{
  id:"data-dictionary", group:"data", name:"Data Dictionary", tagline:"What the collector actually stores, per raw type — source, cadence, retention, and known dead fields.",
  html: function(){ return ""
  + "<div class=\\"sec\\"><h2>Raw types collected</h2><table><tr><th>Type</th><th>Source</th><th>Cadence</th><th>Downstream use</th></tr>"
  + "<tr><td><code>TradeTick</code></td><td>v4_trades WS</td><td>event-driven</td><td class=\\"neg\\">no longer written</td></tr>"
  + "<tr><td><code>OrderBookDeltas</code></td><td>v4_orderbook WS</td><td>event-driven</td><td>applied to in-memory book; stored only if <code>store_order_book_deltas=true</code> (no instrument opts in today)</td></tr>"
  + "<tr><td><code>Bar</code></td><td>candles WS</td><td>—</td><td class=\\"neg\\">never subscribed — dead capability</td></tr>"
  + "<tr><td><code>MarkPriceUpdate</code> / <code>IndexPriceUpdate</code></td><td>markets WS</td><td>event-driven</td><td class=\\"neg\\">stored, no reader found</td></tr>"
  + "<tr><td><code>FundingRateUpdate</code></td><td>markets WS</td><td>event-driven</td><td class=\\"neg\\">stored, no reader found</td></tr>"
  + "<tr><td><code>DydxSecondSnapshot</code></td><td>built in-process from book + trades</td><td>every <code>snapshot_interval_seconds</code> (0.5s default)</td><td>the core record — feeds every live indicator on this site</td></tr>"
  + "<tr><td><code>DydxOpenInterest</code></td><td>REST poll (stdlib urllib)</td><td>every 300s</td><td class=\\"neg\\">stored field itself unused; the <em>parallel</em> volume24H liquidity classification from the same poll <b>is</b> used</td></tr>"
  + "<tr><td><code>InstrumentStatus</code></td><td>markets WS</td><td>event-driven</td><td class=\\"neg\\">stored, no reader found</td></tr>"
  + "</table><p><code>DydxSecondSnapshot</code> fields: <code>bid_prices/sizes</code>, <code>ask_prices/sizes</code> (top 20 levels), <code>buy_volume/sell_volume</code>, <code>buy_count/sell_count</code>, <code>open/high/low/close_price</code> (this is the collector's only surviving record of traded price now that raw <code>TradeTick</code> is no longer persisted). Every computed indicator on this site's Indicators tab traces back to this one record.</p></div>"
  + "<div class=\\"sec\\"><h2>Guards before a snapshot is ever emitted</h2><p><code>_second_loop</code> skips crossed books (forces a resubscribe after 3s of a persistent cross — see the <a data-nav=\\"kb:pm-crossed-book\\">crossed-book postmortem</a>) and skips stale books with no deltas for 5s — both DATA-01 “flag the gap, never fabricate” implementations.</p></div>"
  + "<div class=\\"sec\\"><h2>Retention: the honest version</h2><table><tr><th>Type</th><th>Pinned instruments</th><th>Any other dYdX market</th></tr>"
  + "<tr><td><code>TradeTick</code></td><td>no longer written</td><td>4h, no longer growing</td></tr>"
  + "<tr><td><code>OrderBookDeltas</code></td><td>not stored (nothing opts in)</td><td>not stored</td></tr>"
  + "<tr><td>Mark/Index/Funding/Status</td><td class=\\"neg\\">unlimited</td><td>4h</td></tr>"
  + "<tr><td><code>DydxSecondSnapshot</code></td><td class=\\"neg\\">unlimited</td><td>not collected</td></tr>"
  + "<tr><td><code>DydxOpenInterest</code></td><td class=\\"neg\\">unlimited</td><td>4h</td></tr>"
  + "</table><p>Every configured instrument is <code>pinned=true</code>, and pinned instruments are permanently exempt from the prune loop — so mark/index price, funding, open interest, instrument status, and 1s book snapshots for every configured coin accumulate forever with no built-in cap today.</p></div>";
  },
  refs:["troll/docs/DATA_DICTIONARY.md"]
},
{
  id:"database-setup", group:"data", name:"Database & Persistence", tagline:"Four separate storage mechanisms — what each is for, who owns writes, and how to reach it remotely.",
  html: function(){ return ""
  + "<div class=\\"sec\\"><h2>Four mechanisms, no overlap</h2><table><tr><th>Mechanism</th><th>For</th><th>Durable?</th><th>Reachable?</th></tr>"
  + "<tr><td>Redis</td><td>Live pub/sub + latest-value cache</td><td>No</td><td>127.0.0.1:6379 only</td></tr>"
  + "<tr><td>metrics.db (SQLite)</td><td>31-day rolling per-instrument history</td><td>Yes</td><td>file only</td></tr>"
  + "<tr><td>fills.db (SQLite)</td><td>Append-only live-paper fill ledger</td><td>Yes</td><td>file only</td></tr>"
  + "<tr><td>Parquet catalog</td><td>Raw market data archive</td><td>Yes</td><td>file only</td></tr>"
  + "</table><p>SEC-01 governs every port here: localhost-only, always. Remote access is SSH tunnel only.</p></div>"
  + "<div class=\\"sec\\"><h2>Redis specifics</h2><p><code>redis:8-alpine</code>, container <code>dydx-redis</code>. <b>No persistence, no TTLs, no complex types</b> — if the container restarts, everything is gone and services just republish on their next cycle. Staleness is judged consumer-side (age of the payload's own timestamp), never a Redis expiry. Only plain <code>GET</code>/<code>SET</code>/<code>PUBLISH</code>/<code>SUBSCRIBE</code> — no hashes, sorted sets, streams.</p></div>"
  + "<div class=\\"sec\\"><h2>metrics.db</h2><p>Owned by <code>ranking_engine/metrics_store.py</code>. Table: <code>snapshots(ts, instrument_id, price, pct_1h, pct_24h, volatility, ofi, microprice, spread, rank, volume24h)</code>, upserted every 60s, pruned to 31 days. <b>Writer:</b> ranking_engine exclusively. <b>Reader:</b> dashboard only, mounted read-only.</p><p>Note: the <code>ofi</code> column here is the <em>top-of-book-only</em> <code>OrderFlowImbalance</code>, a different metric from the multi-level <code>ofi_10_z</code>/<code>ofi_3/5/10</code> fields that live only in <code>rankings:live</code>. The SQLite history and the live ranking table track genuinely different OFI computations — don't expect them to match.</p></div>"
  + "<div class=\\"sec\\"><h2>fills.db</h2><p>Owned by <code>live_paper/fills_store.py</code>. Append-only, event-sourced — one row per <code>OrderFilled</code> event. Exists specifically because Nautilus's <code>Cache.positions_closed()</code> silently discards prior closed positions on a NETTING-mode position reopen; <code>fills.db</code> is the durable source of truth trade history is rebuilt from instead.</p></div>"
  + "<div class=\\"sec\\"><h2>config.toml — mutable runtime state, not static config</h2><p>Easy to mistake for a static file, but it's the one file the running system rewrites on its own: every <code>collector:control</code> action (start/unpin/stop/pin_top_liquid) triggers a full rewrite via <code>save_config()</code> — hand-added comments won't survive it. Hot-reloaded every 30s by the collector, so a hand-edit is picked up without a restart.</p></div>"
  + "<div class=\\"sec\\"><h2>Viewing it remotely</h2><table><tr><th>Command</th><th>What it does</th></tr>"
  + "<tr><td><code>make remote-db</code></td><td>Tunnels Redis to localhost so a GUI client can connect as if local</td></tr>"
  + "<tr><td><code>make remote-web</code></td><td>Tunnels + opens the dashboard</td></tr>"
  + "<tr><td><code>make remote-tui</code></td><td>SSHes in with a real TTY and runs bot_tui interactively</td></tr>"
  + "</table><p>The two SQLite files and the Parquet catalog have no server to tunnel to — <code>rsync</code> a copy down instead.</p></div>";
  },
  refs:["troll/docs/DATABASE_SETUP.md"]
},
{
  id:"backtesting", group:"backtest", name:"Backtesting & Strategies", tagline:"Which existing backtest runner to copy, how to build a strategy, and how to wire it in.",
  html: function(){ return ""
  + "<div class=\\"sec\\"><h2>Which existing backtest to copy</h2><table><tr><th>Data granularity</th><th>Feed</th><th>Copy</th></tr>"
  + "<tr><td>Bars (aggregated from trades)</td><td><code>TradeTick</code> → internal <code>Bar</code></td><td><code>backtest_dydx.py</code></td></tr>"
  + "<tr><td>Raw 1s book snapshots</td><td><code>DydxSecondSnapshot</code></td><td><code>backtest_snapshot.py</code></td></tr>"
  + "<tr><td>Raw deltas + trades + bars</td><td><code>OrderBookDelta</code>, <code>TradeTick</code>, <code>Bar</code></td><td><code>backtest_ofi.py</code></td></tr>"
  + "</table><p>Don't write a new backtest runner from scratch — copy the closest match and swap the <code>strategy_path</code>/<code>config_path</code>/<code>data=[...]</code> list. <code>backtest_dydx.py</code> defaults to backtesting every coin in the live Watchlist (needs <code>make dashboard</code> running); pass <code>symbols=[...]</code> to skip that dependency.</p></div>"
  + "<div class=\\"sec\\"><h2>Strategy conventions (every *_strategy.py follows these)</h2><ul>"
  + "<li><code>frozen=True</code> on the config class.</li>"
  + "<li><code>on_start</code>: look up <code>self.instrument</code> via <code>self.cache.instrument(...)</code>; <code>self.stop()</code> + log an error if missing, never assume it's there.</li>"
  + "<li>Subscribe to exactly the feeds needed — <code>subscribe_bars</code>, <code>subscribe_trade_ticks</code>, <code>subscribe_order_book_deltas</code>, or <code>subscribe_data(DataType(DydxSecondSnapshot), ...)</code> for the pre-computed snapshot.</li>"
  + "<li>Position checks via <code>self.portfolio.is_flat/is_net_long/is_net_short</code>, never hand-rolled tracking.</li>"
  + "<li>Reuse <code>ml_signals/indicators.py</code>/<code>book_features.py</code> — never reimplement OFI/OBI/microprice/spread inline.</li>"
  + "</ul></div>"
  + "<div class=\\"sec\\"><h2>Wiring a strategy into a backtest run</h2><ol style=\\"padding-left:20px\\"><li>Point <code>strategy_path</code>/<code>config_path</code> at the new files.</li><li>List every <code>BacktestDataConfig</code> the strategy subscribes to — missing one means the subscription silently gets no data, no error.</li><li>Custom types (e.g. <code>DydxSecondSnapshot</code>) need an explicit <code>client_id=str(venue)</code> and usually a parallel <code>TradeTick</code> config purely for instrument auto-registration.</li><li>Only <code>1-MINUTE</code> bars are actually in the catalog — anything else needs internal aggregation from <code>TradeTick</code>.</li></ol><p>Trust <code>BacktestResult.stats_pnls</code>/<code>stats_returns</code> for whether trades happened — <code>total_orders</code>/<code>total_positions</code> were found unreliable (sometimes 0 despite real fills) in this pinned nautilus_trader version.</p></div>";
  },
  refs:["troll/ml_signals/BACKTESTING.md"]
},
{
  id:"pm-crossed-book", group:"postmortem", name:"Crossed-Book Root Cause", tagline:"72% of crossed-book episodes proven to be our own pipeline's fault, not the venue's — exact locus still open.", status:"open",
  html: function(){ return ""
  + "<div class=\\"sec\\"><p><b>Standard applied:</b> DATA-02 — detect-and-recover is not sufficient, the exact mechanism must be known. <b>Status: <span class=\\"neg\\">still genuinely open</span></b>, not quietly resolved just because the visible symptom is smaller.</p></div>"
  + "<div class=\\"sec\\"><h2>Symptom</h2><p>The collector's <code>_second_loop</code> frequently logs “Crossed book” (bid ≥ ask) for BTC/ETH and other liquid instruments, escalating to a forced resubscribe if it persists.</p></div>"
  + "<div class=\\"sec\\"><h2>Two timing bugs found and fixed along the way</h2><ul>"
  + "<li><b><code>_CROSSED_RESYNC_NS</code> 15s → 3s.</b> A real desync never self-heals from more deltas alone; waiting 15s before forcing a resubscribe was pure downside.</li>"
  + "<li><b><code>_flush_loop</code>'s periodic Parquet write was blocking the event loop.</b> <code>write_data()</code> ran synchronously on the event-loop thread every <code>flush_interval_seconds</code> (60s), during which crossed-book detection couldn't run at all. <span class=\\"callout\\">An earlier fix attempt was wrong</span> — assumed the cause was delta-processing bursts and built a decoupled ingest queue; that helped but didn't fix it. The staleness canary's suspiciously exact ~69–71s periodicity (matching the 60s flush interval almost exactly) is what pointed at the real cause. Actual fix: <code>_flush_once</code> now offloads the write via <code>asyncio.to_thread</code>.</li>"
  + "</ul></div>"
  + "<div class=\\"sec\\"><h2>Permanent instrumentation kept in production</h2><ul><li>Per-side last-delta timestamps in every crossed-book log line.</li><li><code>_second_loop</code> staleness canary (2s slack) — the thing that caught the wrong first diagnosis above.</li><li><code>nautilus_pyo3.init_logging()</code> now called in <code>main()</code> — this collector never went through TradingNode/Kernel startup, so every Rust-side <code>log::error!</code>/<code>warn!</code> in the whole dYdX adapter was silently dropped, never emitted, before this fix.</li></ul></div>"
  + "<div class=\\"sec\\"><h2>The decisive experiment</h2><p>Built an independent reference WS client — pure Python + aiohttp, zero shared code with nautilus_trader — connecting directly to dYdX's indexer, maintaining its own naive book, and dumping an episode file the instant the real collector logged a crossed book.</p>"
  + "<table><tr><th>Result</th><th>Count</th><th>Meaning</th></tr>"
  + "<tr><td>Reference client matches collector's (wrong) reading</td><td>16</td><td>genuine venue-side crossing — dYdX's own served book really is crossed (expected under dYdX v4's per-validator, pre-consensus book architecture)</td></tr>"
  + "<tr><td class=\\"neg\\">Reference client diverges (shows correct data collector doesn't)</td><td><b>41 (72%)</b></td><td class=\\"neg\\">our own pipeline provably lost/failed to apply data the venue actually sent</td></tr>"
  + "</table><p>Byte-level proof for one BTC-USD episode: dYdX sent an ask-level removal; the reference client correctly moved its ask; the collector's book never reflected it. Narrowed to exactly two possibilities: a connection-specific transport-level loss unique to our WS connection, or a silent failure inside the Rust WS handler (audited — no silent-drop path found in <code>crates/adapters/dydx/src/websocket/handler.rs</code>).</p></div>"
  + "<div class=\\"sec\\"><h2>Open question</h2><p>Pinning down category 2's exact locus. Three options were laid out: (A) TLS-intercept the collector's own connection — blocked, requires patching <code>crates/</code> which FORK-01/02 forbid without an explicit exception; (B) stop here — explicitly rejected given DATA-02; (C) run 2–3 independent reference clients against <em>each other</em>, not just vs. the collector — no crates/ changes needed, ~1 hour of work, <b>recommended next step, not yet built.</b></p></div>";
  },
  refs:["troll/.planning/debug/crossed-book-root-cause.md","troll/CLAUDE.md DATA-02, DATA-04"]
},
{
  id:"pm-nifelheim", group:"postmortem", name:"Nifelheim Resource Exhaustion", tagline:"Stale books across nearly every instrument + ranking_engine's silent restart loop, both traced to one root cause: the VPS is oversubscribed.", status:"deferred",
  html: function(){ return ""
  + "<div class=\\"sec\\"><p><b>Status:</b> mechanism identified (DATA-02 standard met). <b>No fix applied</b> — infra decision deferred by the user.</p></div>"
  + "<div class=\\"sec\\"><h2>Symptom</h2><p>Widespread “Stale book” warnings across nearly every subscribed instrument (BTC/ETH/SOL included) — 31 <code>_second_loop tick arrived Ns late</code> events (2–21.5s late) over a 95-minute window, each followed by a stale-book burst hitting ~27–28 of ~28 instruments simultaneously.</p></div>"
  + "<div class=\\"sec\\"><h2>Root cause: host CPU + memory oversubscription</h2><p>2 vCPU / 3.7GB, 0 swap. <code>uptime</code> load average 10.85 on a 2-core box (4–5× oversubscribed). <code>dydx-collector</code> alone at 105% CPU continuous; <code>dydx-ranking-engine</code> RestartCount=430, cycling every ~2 minutes — caught live via <code>docker events</code>: <code>container oom → die (exitCode=137) → start</code>. This is the <b>host's global OOM-killer</b>, confirmed not a container memory cap (<code>Memory=0</code>, uncapped) and not a code-level exit path (grepped — no <code>sys.exit</code>/unhandled-exception path exists in <code>engine.py</code>).</p><p><code>free -h</code>: 119Mi free / 1.0Gi available out of 3.7Gi — essentially no slack before <code>ranking_engine</code> even accumulates its rolling-window state.</p></div>"
  + "<div class=\\"sec\\"><h2>Why this wasn't caught earlier</h2><p>A prior instance of the same failure class (2026-09-11, <code>compute_all()</code> defaulting to ~300 instruments × 25h lookback × 32-way concurrency) was already fixed once by scoping to only pinned/live instruments — that reduced per-cycle memory but didn't add headroom, and a later commit that bumped the collector's instrument cap re-triggered the same OOM class under a new guise: a silent restart loop instead of an obvious one-time crash.</p></div>"
  + "<div class=\\"sec\\"><h2>Options discussed, none chosen</h2><ol style=\\"padding-left:20px\\"><li>Add swap — quick, reversible, doesn't fix CPU oversubscription, trades OOM-kills for thrashing under sustained pressure.</li><li>Resize the VM — addresses the actual root cause, provider-side action.</li><li>Reduce load — fewer instruments, or move dashboard/bot_tui/ranking_engine off this box.</li><li><b>(Chosen for now) Document and defer.</b></li></ol></div>"
  + "<div class=\\"sec\\"><h2>Addendum (Epic 13, Story 13.1)</h2><p><code>_slow_loop_task</code> now passes <code>max_workers=4</code> to <code>compute_all()</code> (was 32) — bounds concurrent Parquet reads on the 60s cycle. An explicit small mitigation, <b>not</b> a resolution of the underlying CPU/RAM oversubscription. Real before/after <code>docker stats</code> evidence was not collected (no SSH access from this dev environment) — whether this measurably reduces the OOM rate in production is still an open, deferred verification. Story 13.2 is the actual fix: removes the recurring Parquet re-scan from the hot path entirely.</p></div>";
  },
  refs:["troll/.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md"]
},
{
  id:"fix-flatline", group:"fixed", name:"Bid/Ask Flatline on Coin Chart", tagline:"A per-coin chart's bid/ask froze during WS reconnect recovery while the homepage kept moving — root-caused and fixed.", status:"done",
  html: function(){ return ""
  + "<div class=\\"sec\\"><p><b>Symptom:</b> an individual coin's chart in the dashboard would freeze bid or ask (sometimes both) while other data kept updating and the venue's own price was clearly still moving. Silent failure, no log errors.</p></div>"
  + "<div class=\\"sec\\"><h2>Root cause</h2><p><code>_live_books[iid]</code> is frozen during WebSocket reconnect recovery. When the dYdX WS reconnects, the Rust client re-subscribes instruments at a 2/sec rate limit — the last instrument in the sorted subscription queue waits up to N/2 seconds for its fresh snapshot. During that window, <code>_second_loop</code> had no staleness check and repeatedly emitted the pre-reconnect (stale) book state. Plotly's <code>mode=lines</code> then drew a straight, misleading line across that flat stretch.</p></div>"
  + "<div class=\\"sec\\"><h2>Fix (two parts)</h2><ol style=\\"padding-left:20px\\"><li><code>collector._second_loop</code> tracks <code>_last_book_update_ns</code> per instrument and skips snapshot emission once a book hasn't received deltas in &gt;5s — stops stale data from ever reaching Redis.</li><li><code>dashboard._coin_chart_json</code> detects gaps &gt;2.5s between consecutive snapshot timestamps and inserts a null — Plotly then draws an honest break instead of a flat line.</li></ol><p>Verified across 46/46 tests, including 4 new gap-detection tests exercising the null-insertion path directly.</p></div>";
  },
  refs:["troll/.planning/done/bid-ask-flatline-coin-chart.md"]
},
{
  id:"fix-unpin-exclude", group:"fixed", name:"Unpin → config.exclude, with confirm", tagline:"Folded the collector's separate \\"unpinned\\" list into the existing exclude denylist, and added a type-to-confirm guard before unpinning.", status:"done",
  html: function(){ return ""
  + "<div class=\\"sec\\"><h2>What changed</h2><ol style=\\"padding-left:20px\\"><li><code>\\"unpin\\"</code> now writes straight into <code>config.exclude</code> instead of a separate <code>CollectorConfig.unpinned</code> field, which was dropped entirely.</li><li>bot_tui's “unpinned” section now reflects <code>config.exclude</code> as a whole — a coin hand-added to <code>exclude</code> in <code>config.toml</code> shows up there too, no separate “via unpin” distinction any more.</li><li>Pressing <code>p</code> to unpin now opens the same type-to-confirm prompt pattern already used for <code>x</code>/stop, instead of firing immediately.</li></ol></div>"
  + "<div class=\\"sec\\"><h2>Why this is a real behavior change, not just a rename</h2><p><code>classify_liquidity</code> already treats every id in <code>exclude</code> as permanently illiquid regardless of volume — a hand-curated denylist for stablecoins/garbage markets. Folding <code>unpin</code> into it means an unpinned coin is now <em>also</em> permanently labeled “illiquid” in <code>collector:status</code>, not just “not currently collected.” Presumably the intended strength, called out explicitly so it isn't a surprise later.</p></div>"
  + "<div class=\\"sec\\"><h2>Design note taken</h2><p>The confirm-guard implementation was generalized into one shared “pending collector action” state (action-name parameterized) rather than duplicating the stop-confirm's four methods a second time — in line with this repo's deletion-over-addition preference (DESIGN-03) given the two flows would otherwise be near-identical.</p></div>";
  },
  refs:["troll/.planning/done/collector-unpin-exclude-confirm.md"]
},
{
  id:"fix-ranking-clarity", group:"fixed", name:"Ranking Table: Vol Label + Lean Placement", tagline:"Disambiguated the ranking table's bare “Vol” column and moved microprice lean off the cross-instrument table onto the single-coin page.", status:"done",
  html: function(){ return ""
  + "<div class=\\"sec\\"><p><b>Date:</b> 2026-09-13. <b>Trigger:</b> both the web ranking table and bot_tui's Coins pane showed a bare <code>“Vol”</code> header sitting on the same row as <code>“Vol Score”</code> and <code>“Vol24h”</code> — three different numbers, one ambiguous name. The same table also showed <code>“u lean”</code> (microprice lean), a single-coin directional signal, mixed in among the cross-coin ranking columns.</p></div>"
  + "<div class=\\"sec\\"><h2>Change</h2><ul>"
  + "<li>Relabeled the <code>volatility</code> ranking-table column from <code>“Vol”</code> to <code>“Vol(catalog)”</code> in <code>ml_signals/ranking_columns.py</code> (the single SSOT both the web table and bot_tui's Coins pane render from) and in the web dashboard's client-side ranking-table script — matching the label the coin-detail page already used for the same field, so it now reads identically everywhere it appears. See <a data-nav=\\"i:vol_catalog\\">Vol(catalog)</a> for the full definition.</li>"
  + "<li>Moved <code>microprice_lean</code> out of <code>RANKING_COLS</code> (shared by both ranking tables) into <code>_HISTORY_ONLY_COLS</code> — the same mechanism already used to keep <code>rank</code> off the live table. It's still shown on both single-coin detail views (web + bot_tui) and now also still charts correctly on the 31-day history page. See <a data-nav=\\"i:microprice_lean\\">Microprice Lean</a>.</li>"
  + "</ul></div>"
  + "<div class=\\"sec\\"><h2>Why this approach</h2><p><code>_HISTORY_ONLY_COLS</code> already existed for exactly this purpose (“field charted on the coin's history page, not shown as a ranking-table column”) — reusing it instead of introducing a second parallel list keeps the SSOT intact (troll/CLAUDE.md SSOT-04) and required touching only the column metadata, not either UI's rendering code.</p></div>";
  },
  refs:["troll/ml_signals/ranking_columns.py","troll/ml_signals/dashboard.py"]
},
{
  id:"self-hosted-docs", group:"fixed", name:"Signal Atlas moved self-hosted", tagline:"This page itself: moved from a standalone Claude Artifact into dashboard.py as /docs, dark-themed to match the rest of the app.", status:"done",
  html: function(){ return ""
  + "<div class=\\"sec\\"><p>Originally built as a standalone Claude Artifact so it could be reviewed before committing to a permanent home. Moved into <code>ml_signals/docs_page.py</code> and served at <code>/docs</code> — same content model (client-side hash-routed, data-driven indicator/KB pages), retheme only: the light/adaptive-theme CSS was dropped for one fixed dark palette matching <code>dashboard.py</code>'s own <code>_CSS</code> (<code>#0d1117</code>/<code>#161b22</code>/<code>#21262d</code>/<code>#58a6ff</code>, the same GitHub-dark-style tokens already used throughout the ranking table and charts), and the Google-Fonts IBM Plex pairing was dropped for the app's existing plain <code>monospace</code> stack so this page doesn't make an external request the rest of the app doesn't already make.</p></div>";
  },
  refs:["troll/ml_signals/docs_page.py","troll/ml_signals/dashboard.py"]
}
];

/* =========================================================================================
   DIAGRAM — top-to-bottom pipeline column with side branches; every connector was
   checked by hand so no line passes through a box it doesn't terminate at.
   ========================================================================================= */
function svgArchitecture(){
  return ''
  + '<svg viewBox="0 0 700 640" role="img" aria-label="Data flows from dYdX via the collector into the Parquet catalog and a live Redis snapshots:raw feed. ranking_engine subscribes to that feed and publishes rankings:live back to Redis, plus writes metrics.db. The web dashboard and bot_tui both read snapshots:raw and rankings:live directly and never compute either themselves. Separately, live_paper runs its own TradingNode connection straight to dYdX and exchanges bots:status and bots:control with bot_tui over Redis.">'
  + '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>'
  + box(260,20,260,46,"dYdX WS / REST")
  + box(260,110,260,46,"Collector")
  + box(20,110,180,46,"Parquet catalog")
  + box(260,200,260,46,"Redis: snapshots:raw")
  + box(260,290,260,46,"ranking_engine")
  + box(20,290,180,46,"metrics.db (SQLite)")
  + box(260,380,260,46,"Redis: rankings:live")
  + box(260,470,260,60,"dashboard (web) +\\nbot_tui (terminal)")
  + box(20,470,180,46,"live_paper\\n(TradingNode)")
  + box(20,560,300,46,"Redis: bots:status / bots:control")
  + line(390,66,390,110,"WS / HTTP")
  + line(390,156,390,200,"publishes ~1/s")
  + line(390,246,390,290,"subscribes")
  + line(390,336,390,380,"publishes on change\\n+ 5s heartbeat")
  + line(390,426,390,470,"reads")
  + line(260,133,200,133,"writes")
  + line(260,313,200,313,"writes, 60s")
  + poly([[520,223],[620,223],[620,500],[520,500]], "subscribes\\n(direct)", [636,360])
  + poly([[260,43],[6,43],[6,493],[20,493]], "WS / HTTP", [26,270])
  + line(110,516,110,560,"status")
  + line(150,560,150,516,"control")
  + poly([[320,583],[380,583],[380,530]], "bots:status /\\nbots:control", [350,571])
  + '</svg>';
}
function box(x,y,w,h,label){
  var lines = label.split("\\n");
  var ty = y + h/2 - (lines.length-1)*7;
  var text = lines.map(function(l,i){ return '<text x="'+(x+w/2)+'" y="'+(ty+i*15)+'" text-anchor="middle" font-size="12" font-family="monospace">'+esc(l)+'</text>'; }).join('');
  return '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="6" fill="none" stroke="currentColor" stroke-width="1.4"/>' + text;
}
function line(x1,y1,x2,y2,label){
  var out = '<line x1="'+x1+'" y1="'+y1+'" x2="'+x2+'" y2="'+y2+'" stroke="currentColor" stroke-width="1.3" marker-end="url(#arrow)"/>';
  if(label){
    var horiz = y1===y2;
    var lx = horiz ? (x1+x2)/2 : x1+42;
    var ly = horiz ? y1-8 : (y1+y2)/2;
    out += linelabel(lx, ly, label, !horiz);
  }
  return out;
}
function poly(points, label, labelPos){
  var pointStr = points.map(function(p){ return p[0]+','+p[1]; }).join(' ');
  var out = '<polyline points="'+pointStr+'" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="3 3" marker-end="url(#arrow)"/>';
  if(label && labelPos){ out += linelabel(labelPos[0], labelPos[1], label, true); }
  return out;
}
function linelabel(x,y,label,vertical){
  var lines = String(label).split("\\n");
  var dy = vertical ? -6*(lines.length-1) : 0;
  return lines.map(function(l,i){ return '<text x="'+x+'" y="'+(y+dy+i*11)+'" text-anchor="middle" font-size="9.5" font-family="monospace" fill="currentColor" opacity="0.8">'+esc(l)+'</text>'; }).join('');
}
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

/* =========================================================================================
   ROUTER + RENDER
   ========================================================================================= */
var byId = {};
INDICATORS.forEach(function(x){ byId["i:"+x.id] = x; });
KB.forEach(function(x){ byId["kb:"+x.id] = x; });

function currentTab(){
  var h = location.hash.slice(1);
  if(h==="home-kb" || h.indexOf("kb:")===0) return "kb";
  return "ind";
}
function currentItem(){
  var h = location.hash.slice(1);
  if(h.indexOf("i:")===0 || h.indexOf("kb:")===0) return h;
  return null;
}
function navTo(key){ location.hash = key; }
document.addEventListener("click", function(e){
  var el = e.target.closest("[data-nav]");
  if(el){ e.preventDefault(); navTo(el.getAttribute("data-nav")); window.scrollTo({top:0}); }
});
window.addEventListener("hashchange", render);

function renderSide(){
  var tab = currentTab();
  var item = currentItem();
  var q = (document.getElementById("filterbox") && document.getElementById("filterbox").value || "").toLowerCase();
  var html = '<div class="brand"><b>Signal Atlas</b><span>troll/</span></div>';
  html += '<p class="tagline">Indicator reference + engineering knowledge base for the dYdX collector, ranking engine, dashboard, bot_tui and live_paper.</p>';
  html += '<div class="tabs">'
        + '<div class="tabbtn'+(tab==="ind"?" active":"")+'" data-nav="home-ind">Indicators</div>'
        + '<div class="tabbtn'+(tab==="kb"?" active":"")+'" data-nav="home-kb">Knowledge Base</div>'
        + '</div>';
  html += '<input class="filter" id="filterbox" placeholder="Filter\\u2026" value="'+esc(q)+'" oninput="onFilter()">';

  var groups = tab==="ind" ? IND_GROUPS : KB_GROUPS;
  var items = tab==="ind" ? INDICATORS : KB;
  groups.forEach(function(g){
    var members = items.filter(function(it){ return it.group===g.id && (!q || (it.name+" "+it.tagline).toLowerCase().indexOf(q)>=0); });
    if(!members.length) return;
    html += '<div class="navgroup"><div class="navgroup-h">'+esc(g.name)+'</div>';
    members.forEach(function(it){
      var key = (tab==="ind"?"i:":"kb:")+it.id;
      var active = item===key;
      var dotColor = tab==="ind" ? cadenceColor(it.cadence) : "var(--accent)";
      html += '<div class="navitem'+(active?" active":"")+'" data-nav="'+key+'"><span class="navdot" style="background:'+dotColor+'"></span>'+esc(it.name)+'</div>';
    });
    html += '</div>';
  });
  document.getElementById("side").innerHTML = html;
}
function cadenceColor(c){
  return c==="live" ? "var(--tag-live)" : c==="rolling" ? "var(--tag-rolling)" : c==="slow" ? "var(--tag-slow)" : "var(--tag-static)";
}
window.onFilter = function(){ renderSide(); };

function renderHome(tab){
  var groups = tab==="ind" ? IND_GROUPS : KB_GROUPS;
  var items = tab==="ind" ? INDICATORS : KB;
  var html = '<div class="intro">';
  if(tab==="ind"){
    html += '<h1>Indicator Reference</h1><p>Every live and derived signal this system computes — where it lives, how often it actually updates, and which process owns computing it. Click any card for the full definition, formula, and source references.</p>';
    html += '<p class="meta">'+INDICATORS.length+' indicators across '+IND_GROUPS.length+' groups · verified against the <code>bmad</code> branch, 2026-09-13</p>';
  } else {
    html += '<h1>Knowledge Base</h1><p>Architecture, setup, operations, data storage, backtesting, and the postmortems/fixes that shaped this system’s current behavior — collected in one place instead of scattered across a dozen markdown files.</p>';
    html += '<p class="meta">'+KB.length+' documents across '+KB_GROUPS.length+' groups</p>';
  }
  html += '</div>';
  groups.forEach(function(g){
    var members = items.filter(function(it){ return it.group===g.id; });
    if(!members.length) return;
    html += '<div class="grid-group"><h2>'+esc(g.name)+'</h2>';
    if(g.desc) html += '<p class="gdesc">'+g.desc+'</p>';
    html += '<div class="cards">';
    members.forEach(function(it){
      var key = (tab==="ind"?"i:":"kb:")+it.id;
      html += '<div class="card" data-nav="'+key+'">';
      if(tab==="ind"){
        var cm = CADENCE_META[it.cadence];
        html += '<span class="pill '+it.cadence+'">'+cm.label+'</span>';
      } else if(it.status){
        html += '<span class="pill status-'+it.status+'">'+esc(it.status)+'</span>';
      }
      html += '<h3>'+esc(it.name)+'</h3><p>'+it.tagline+'</p></div>';
    });
    html += '</div></div>';
  });
  document.getElementById("main").innerHTML = html;
}

function renderIndicatorDetail(it){
  var cm = CADENCE_META[it.cadence];
  var html = '<div class="crumb"><a data-nav="home-ind">Indicators</a> / '+esc(IND_GROUPS.filter(function(g){return g.id===it.group;})[0].name)+' / '+esc(it.name)+'</div>';
  html += '<div class="dhead"><div><h1>'+esc(it.name)+'</h1><p class="tagline">'+it.tagline+'</p></div><span class="pill '+it.cadence+'" style="font-size:11px;padding:5px 11px">'+cm.label+'</span></div>';
  html += '<div class="infogrid">'
        + '<div class="infocell"><div class="k">Cadence</div><div class="v">'+cm.sub+'</div></div>'
        + '<div class="infocell"><div class="k">Window</div><div class="v">'+esc(it.window)+'</div></div>'
        + '<div class="infocell"><div class="k">Computed by</div><div class="v">'+esc(it.owner)+'</div></div>'
        + '<div class="infocell"><div class="k">Shown in</div><div class="v"><div class="chiprow">'+it.shownIn.map(function(s){return '<span class="chip">'+esc(s)+'</span>';}).join('')+'</div></div></div>'
        + '</div>';
  if(it.formula){ html += '<div class="formula">'+esc(it.formula)+'</div>'; }
  if(it.notes && it.notes.length){
    html += '<div class="sec"><h2>Notes</h2>' + it.notes.map(function(p){ return '<p>'+p+'</p>'; }).join('') + '</div>';
  }
  html += '<div class="refs"><h2>Source</h2>' + it.refs.map(function(r){ return '<span class="refline">'+esc(r)+'</span>'; }).join('') + '</div>';
  if(it.related && it.related.length){
    html += '<div class="related">' + it.related.map(function(r){
      var t = byId["i:"+r]; if(!t) return "";
      return '<a data-nav="i:'+r+'">'+esc(t.name)+' →</a>';
    }).join('') + '</div>';
  }
  document.getElementById("main").innerHTML = html;
}

function renderKbDetail(it){
  var html = '<div class="crumb"><a data-nav="home-kb">Knowledge Base</a> / '+esc(KB_GROUPS.filter(function(g){return g.id===it.group;})[0].name)+' / '+esc(it.name)+'</div>';
  html += '<div class="dhead"><div><h1>'+esc(it.name)+'</h1><p class="tagline">'+it.tagline+'</p></div>';
  if(it.status){
    html += '<span class="pill status-'+it.status+'" style="font-size:11px;padding:5px 11px">'+esc(it.status)+'</span>';
  }
  html += '</div>';
  html += it.html();
  html += '<div class="refs"><h2>Source</h2>' + it.refs.map(function(r){ return '<span class="refline">'+esc(r)+'</span>'; }).join('') + '</div>';
  document.getElementById("main").innerHTML = html;
}

function render(){
  var tab = currentTab();
  var item = currentItem();
  renderSide();
  if(item && byId[item]){
    if(item.indexOf("i:")===0) renderIndicatorDetail(byId[item]);
    else renderKbDetail(byId[item]);
  } else {
    renderHome(tab);
  }
}
if(!location.hash) location.hash = "home-ind";
render();
</script>
</body>
</html>
"""
