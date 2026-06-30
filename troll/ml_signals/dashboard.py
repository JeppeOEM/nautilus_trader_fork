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
Standalone aiohttp dashboard for the dYdX collector.

Subscribes to Redis channel snapshots:1s (published by the collector every second),
maintains a rolling in-process snapshot window, and pushes live rankings to browsers
via Server-Sent Events at /stream. Reconnects to Redis automatically after collector
restart (ARCH-03).

Pages:
- `/`               rankings table — all coins sortable by any metric (1s poll)
- `/api/rankings`   JSON endpoint polled every 1s by the rankings page JS
- `/coin/{id}`      live indicator panel + 1s-polled mid/bid/ask/microprice chart
- `/chart/{id}`     per-event microstructure chart (Parquet-backed, date-picker controlled)
- `/history/{id}`   31-day metric history charts for one coin
- `/live`           in-process live signal monitor (see `record()` below)

Usage::

    from ml_signals.dashboard import record

    record("logistic_trend", signal.value, signal.ts_event)
"""

import asyncio
import html
import json
import logging
import os
import statistics
import time
from collections import defaultdict
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import plotly.graph_objects as go
import redis.asyncio as aioredis
from aiohttp import web
from plotly.subplots import make_subplots

from ml_signals.catalog_stats import list_instruments
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
from ml_signals import chart_data as _chart_data
from ml_signals import metrics_computer
from ml_signals import metrics_store


logger = logging.getLogger(__name__)

CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")

# How often the slow loop recomputes full snapshot metrics from Parquet.
LIVE_INTERVAL_SECONDS: int = 5
# How often the slow loop flushes to SQLite for historical bookkeeping.
DB_WRITE_INTERVAL_SECONDS: int = 60

# Rankings table columns. Each entry: (store_key, header_label, format_fn, color_fn|None).
# Reorder, add, or remove rows here to control what's shown and how.
# color_fn receives the raw float value and returns a CSS color string.
RANKING_COLS: list[tuple[str, str, object, object]] = [
    ("ofi_10_z",       "OFI10z", lambda v: f"{v:+.2f}",  lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("obi_10",         "OBI10",  lambda v: f"{v:.3f}",   lambda v: "#2a9d2a" if v > 0.5 else "#c0392b"),
    ("obi_5",          "OBI5",   lambda v: f"{v:.3f}",   lambda v: "#2a9d2a" if v > 0.5 else "#c0392b"),
    ("obi_3",          "OBI3",   lambda v: f"{v:.3f}",   lambda v: "#2a9d2a" if v > 0.5 else "#c0392b"),
    ("cvd",            "CVD",    lambda v: f"{v:+.2f}",  lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("spread",         "Spread", lambda v: f"{v:.6f}",   None),
    ("microprice_lean","u lean", lambda v: f"{v:+.6f}",  lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("volume_delta",   "Vol d",  lambda v: f"{v:+.2f}",  lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("buy_count",      "Buy#",   lambda v: f"{int(v)}",  None),
    ("sell_count",     "Sell#",  lambda v: f"{int(v)}",  None),
    ("price",          "Price",  lambda v: f"{v:.4f}",   None),
    ("pct_1h",         "1h %",   lambda v: f"{v:+.2f}%", lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("pct_24h",        "24h %",  lambda v: f"{v:+.2f}%", lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("volatility",     "Vol",    lambda v: f"{v:.6f}",   None),
]

_SERIES: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=2000))

# 1s-fresh metrics from Redis — written by _ingest_batch, wins on overlap.
_LIVE_FAST: dict[str, dict] = {}
# Parquet/SQLite-derived metrics — written by _slow_loop_task and make_app pre-load.
_LIVE_SLOW: dict[str, dict] = {}

_INGEST_COUNT: int = 0       # total batches ingested; increments ~1/s; visible in API
_LAST_INGEST_TS: float = 0.0  # wall-clock seconds of last successful ingest

# Rolling 1s snapshots received from Redis (plain dicts from DydxSecondSnapshot.to_dict()).
_second_rolling: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))

# Persistent per-coin OFI indicators — fed incrementally (1 new snap/sec).
_OFI_ZSCORE_WINDOW = 3600  # 1 hour of 1s readings to establish mean/std
_OFI_INDS: dict[str, MultiLevelOFI] = {}              # OFI10 with z-score
_OFI_RAW_INDS: dict[str, dict[int, MultiLevelOFI]] = {}  # raw OFI at levels 3, 5, 10
_OBI_INDS: dict[str, dict[int, MultiLevelOBI]] = {}   # persistent OBI at levels 3, 5, 10
_LAST_FED: dict[str, int] = {}                        # instrument_id → ts_event of last fed snap

_NAV = '<p><a href="/">Rankings</a> | <a href="/live">Live signals</a></p>'


def _merged_live(iid: str) -> dict:
    """Merge slow and fast caches; fast wins on key overlap."""
    return {**_LIVE_SLOW.get(iid, {}), **_LIVE_FAST.get(iid, {})}


def _merged_rows() -> list[dict]:
    """Only instruments currently being collected (_LIVE_FAST). _LIVE_SLOW enriches but never adds rows."""
    return [_merged_live(iid) for iid in _LIVE_FAST]


def _split_tiers(catalog_path: str) -> tuple[set[str], set[str]]:
    """Return (subscribed_iids, illiquid_iids) by checking trade_tick directory presence."""
    import glob
    import os as _os
    subscribed: set[str] = set()
    for path in glob.glob(_os.path.join(catalog_path, "data", "trade_tick", "*")):
        subscribed.add(Path(path).name)
    all_iids = set(list_instruments(catalog_path))
    return subscribed, all_iids - subscribed


_TIER_CACHE: tuple[set[str], set[str]] | None = None
_TIER_CACHE_TS: float = 0.0
_TIER_TTL: float = 1800.0  # re-scan every 30 min


def _get_tiers() -> tuple[set[str], set[str]]:
    global _TIER_CACHE, _TIER_CACHE_TS
    if _TIER_CACHE is None or time.time() - _TIER_CACHE_TS > _TIER_TTL:
        _TIER_CACHE = _split_tiers(CATALOG_PATH)
        _TIER_CACHE_TS = time.time()
    return _TIER_CACHE


_CSS = """
<style>
body { font-family: monospace; font-size: 13px; margin: 20px; background: #0d1117; color: #c9d1d9; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 6px 12px; text-align: right; border-bottom: 1px solid #21262d; }
th { background: #161b22; position: sticky; top: 0; }
th a { color: #c9d1d9; }
tr:hover td { background: #161b22; }
td:first-child, th:first-child { text-align: left; }
</style>
"""

_INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>dYdX Monitor</title>
<style>
body{font-family:monospace;font-size:13px;margin:20px;background:#0d1117;color:#c9d1d9}
a{color:#58a6ff;cursor:pointer;text-decoration:none}
a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%}
th,td{padding:6px 12px;text-align:right;border-bottom:1px solid #21262d}
th{background:#161b22;position:sticky;top:0}
tr:hover td{background:#161b22}
td:first-child,th:first-child{text-align:left}
#status{color:#8b949e;font-size:11px;margin:4px 0 10px}
</style>
</head>
<body>
<p><a onclick="showRankings();return false" href="/">Rankings</a> | <a href="/live">Live signals</a></p>
<div id="status">Loading…</div>
<div id="app"></div>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
var COLS=[
  ["ofi_10_z","OFI10z"],["obi_10","OBI10"],["obi_5","OBI5"],["obi_3","OBI3"],
  ["cvd","CVD"],["spread","Spread"],["microprice_lean","u lean"],
  ["volume_delta","Vol d"],["buy_count","Buy#"],["sell_count","Sell#"],
  ["price","Price"],["pct_1h","1h %"],["pct_24h","24h %"],["volatility","Vol"]
];
var IND=[
  ["ofi_10","OFI10"],["ofi_5","OFI5"],["ofi_3","OFI3"],
  ["obi_10","OBI10"],["obi_5","OBI5"],["obi_3","OBI3"],
  ["microprice","Microprice"],["microprice_lean","u lean"],
  ["spread","Spread"],["cvd","CVD"],["volume_delta","Vol delta"],
  ["buy_count","Buy#"],["sell_count","Sell#"],["avg_trade_size","Avg size"]
];
var timer=null;

function esc(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function setStatus(s){document.getElementById("status").innerHTML=s;}
function setApp(h){document.getElementById("app").innerHTML=h;}

function showRankings(){
  clearInterval(timer);
  history.pushState({},"","/");
  pollRankings();
  timer=setInterval(pollRankings,2000);
}

function pollRankings(){
  fetch("/api/rankings")
    .then(function(r){return r.json();})
    .then(function(data){renderRankings(data.rows,data.ingest_count,data.age_s,data.stale);})
    .catch(function(err){setStatus("Fetch error: "+err);});
}

function renderRankings(rows,ingestCount,ageS,stale){
  var hdr="<tr><th>#</th><th>Instrument</th>"
    +COLS.map(function(c){return "<th>"+esc(c[1])+"</th>";}).join("")
    +"<th>History</th></tr>";
  var tbody=rows.map(function(row,i){
    var iid=row.instrument_id;
    var label=iid.split("-").slice(0,2).join("-");
    var cells=COLS.map(function(c){
      var v=row.cells[c[0]];
      if(!v||!v.text||v.text==="\\u2014")return "<td>&mdash;</td>";
      var col=v.color?' style="color:'+esc(v.color)+'"':"";
      return "<td"+col+">"+esc(v.text)+"</td>";
    }).join("");
    return "<tr><td>"+(i+1)+"</td>"
      +"<td><a onclick=\\"showCoin('"+esc(iid)+"');return false\\" href=\\"/coin/"+esc(iid)+"\\">"+esc(label)+"</a>"
      +" <small><a href=\\"/chart/"+esc(iid)+"\\">chart</a></small></td>"
      +cells
      +"<td><a href=\\"/history/"+esc(iid)+"\\">31d</a></td></tr>";
  }).join("");
  setApp("<h1>dYdX Monitor</h1>"
    +"<h2>Collecting ("+rows.length+")</h2>"
    +"<table>"+hdr+tbody+"</table>");
  var staleTxt=stale?" STALE (no data "+ageS+"s)":" ↺"+ingestCount;
  var col=stale?"color:#f85149":"color:#3fb950";
  setStatus("Updated "+new Date().toLocaleTimeString()+" — "+rows.length+" instruments — <span style=\\""+col+"\\">"+esc(staleTxt)+"</span>");
}

function showCoin(iid){
  clearInterval(timer);
  history.pushState({iid:iid},"","/coin/"+encodeURIComponent(iid));
  pollCoin(iid);
  timer=setInterval(function(){pollCoin(iid);},2000);
}

function pollCoin(iid){
  Promise.all([
    fetch("/data/live/"+encodeURIComponent(iid)).then(function(r){return r.json();}),
    fetch("/data/coin/"+encodeURIComponent(iid)).then(function(r){return r.json();})
  ]).then(function(res){
    renderCoin(iid,res[0],res[1]);
    setStatus("Updated "+new Date().toLocaleTimeString());
  }).catch(function(err){setStatus("Error: "+err);});
}

function renderCoin(iid,ind,chart){
  var label=iid.split("-").slice(0,2).join("-");
  var rows=IND.map(function(k){
    var v=ind[k[0]];
    return "<tr><td>"+esc(k[1])+"</td><td>"+(v!=null?esc(String(v)):"&mdash;")+"</td></tr>";
  }).join("");
  setApp("<h1>"+esc(label)
    +" <small><a href=\\"/chart/"+esc(iid)+"\\">chart</a>"
    +" | <a onclick=\\"showRankings();return false\\" href=\\"/\\">back</a></small></h1>"
    +"<h2>Indicators</h2><table>"+rows+"</table>"
    +"<div id=\\"live-chart\\" style=\\"height:350px;margin-top:16px\\"></div>");
  if(chart.ts&&chart.ts.length){
    var x=chart.ts.map(function(t){return new Date(t);});
    Plotly.react("live-chart",[
      {x:x,y:chart.mid, name:"mid",        mode:"lines",line:{color:"#aaa",width:1}},
      {x:x,y:chart.bid, name:"bid",        mode:"lines",line:{color:"#26a69a",width:1}},
      {x:x,y:chart.ask, name:"ask",        mode:"lines",line:{color:"#ef5350",width:1}},
      {x:x,y:chart.micro,name:"microprice",mode:"lines",line:{color:"#f0883e",width:1.5,dash:"dot"}}
    ],{height:350,template:"plotly_dark",xaxis:{type:"date"},
       margin:{t:30,b:30},legend:{orientation:"h"}});
  }
}

window.onpopstate=function(){
  if(location.pathname.startsWith("/coin/")){
    showCoin(decodeURIComponent(location.pathname.slice(6)));
  }else{
    showRankings();
  }
};

if(location.pathname.startsWith("/coin/")){
  showCoin(decodeURIComponent(location.pathname.slice(6)));
}else{
  showRankings();
}
</script>
</body>
</html>"""


def record(name: str, value: float, ts_event: int) -> None:
    _SERIES[name].append((ts_event, value))


def _page(title: str, body: str, refresh_seconds: int = 60) -> str:
    return (
        f"<html><head><title>{html.escape(title)}</title>"
        f'<meta http-equiv="refresh" content="{refresh_seconds}">'
        f"{_CSS}</head>"
        f"<body>{_NAV}{body}</body></html>"
    )


def _render_history_page(symbol: str) -> str:
    db_path = str(Path(CATALOG_PATH).parent / "metrics.db")
    rows = metrics_store.history(symbol, db_path, days=31)

    if not rows:
        return _page(symbol, f"<h1>{html.escape(symbol)}</h1><p>No history yet.</p>")

    ts = [pd.Timestamp(r["ts"], unit="ns", tz="UTC") for r in rows]
    body = f"<h1>{html.escape(symbol)} — 31-day history</h1>"
    plotlyjs = "cdn"

    for field, label, *_ in RANKING_COLS:
        vals = [r.get(field) for r in rows]
        if all(v is None for v in vals):
            continue
        fig = go.Figure(go.Scatter(x=ts, y=vals, mode="lines", connectgaps=False))
        fig.update_layout(title=label, height=250, margin={"t": 40, "b": 20})
        body += fig.to_html(full_html=False, include_plotlyjs=plotlyjs)
        plotlyjs = False  # type: ignore[assignment]

    return _page(f"{symbol} history", body, refresh_seconds=30)


def _render_chart_page(symbol: str, start_ms: int, end_ms: int) -> str:
    """Per-event microstructure chart — Plotly subplots with shared x-axis."""
    data = _chart_data.compute_chart_series(
        CATALOG_PATH, symbol,
        start_ns=start_ms * 1_000_000,
        end_ns=end_ms * 1_000_000,
    )

    def ts(series: list[dict]) -> list:
        return pd.to_datetime([p["time"] for p in series], unit="s", utc=True)

    def vals(series: list[dict]) -> list:
        return [p["value"] for p in series]

    fig = make_subplots(
        rows=8, cols=1, shared_xaxes=True,
        row_heights=[0.22, 0.10, 0.10, 0.10, 0.12, 0.12, 0.12, 0.12],
        vertical_spacing=0.015,
        subplot_titles=[
            "Price + 30m EMA trend", "OFI",
            "Book imbalance L1 agg (4-level)", "Mid-layer imbalance (L2-3)",
            "Depth (4-level)", "Cancel pressure",
            "5-min cumulative delta", "Spread",
        ],
    )

    def _add(series_key: str, row: int, name: str, color: str) -> None:
        if data.get(series_key):
            fig.add_trace(go.Scattergl(
                x=ts(data[series_key]), y=vals(data[series_key]),
                mode="lines", name=name, line_color=color, line_width=1,
            ), row=row, col=1)

    # Row 1: price + trend EMAs overlaid
    if data["candles"]:
        c = data["candles"]
        fig.add_trace(go.Candlestick(
            x=pd.to_datetime([p["time"] for p in c], unit="s", utc=True),
            open=[p["open"] for p in c], high=[p["high"] for p in c],
            low=[p["low"] for p in c], close=[p["close"] for p in c],
            name="price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ), row=1, col=1)
    elif data["trades"]:
        _add("trades", 1, "price", "#2962ff")
    _add("trend_fast", 1, f"EMA{8}",  "#ffd700")
    _add("trend_slow", 1, f"EMA{21}", "#ff8c00")

    # Row 2: OFI
    _add("ofi", 2, "OFI", "#f0883e")
    if data.get("ofi"):
        fig.add_hline(y=0, line_color="#555", line_width=1, row=2, col=1)

    # Row 3: 4-level aggregate imbalance
    _add("imbalance", 3, "imbalance", "#ab71ff")
    if data.get("imbalance"):
        fig.add_hline(y=0.5, line_color="#555", line_width=1, row=3, col=1)

    # Row 4: mid-layer imbalance (levels 2-3)
    _add("mid_imbalance", 4, "mid imbalance", "#c792ea")
    if data.get("mid_imbalance"):
        fig.add_hline(y=0.5, line_color="#555", line_width=1, row=4, col=1)

    # Row 5: depth
    _add("bid_depth", 5, "bid depth", "#26a69a")
    _add("ask_depth", 5, "ask depth", "#ef5350")

    # Row 6: cancel pressure
    _add("bid_cancel", 6, "bid cancel", "#26a69a")
    _add("ask_cancel", 6, "ask cancel", "#ef5350")

    # Row 7: 5-min cumulative delta
    _add("cum_delta", 7, "cum delta", "#64b5f6")
    if data.get("cum_delta"):
        fig.add_hline(y=0, line_color="#555", line_width=1, row=7, col=1)

    # Row 8: spread
    _add("spread", 8, "spread", "#78909c")

    n = len(data.get("ofi", []))
    fig.update_layout(
        height=1400, template="plotly_dark",
        title=f"{symbol} — {n:,} delta events",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend={"orientation": "h", "y": 1.01},
    )
    # Datetime pickers above the chart — submits as GET params
    sym = html.escape(symbol)
    import datetime as _dt
    fmt = "%Y-%m-%dT%H:%M"
    start_val = _dt.datetime.fromtimestamp(start_ms / 1000).strftime(fmt)
    end_val   = _dt.datetime.fromtimestamp(end_ms   / 1000).strftime(fmt)
    form = (
        f"<form method='get' style='margin:8px 0'>"
        f"From <input type='datetime-local' name='start' value='{start_val}'> &nbsp;"
        f"To <input type='datetime-local' name='end' value='{end_val}'> &nbsp;"
        f"<button type='submit'>Load</button>"
        f"</form>"
    )
    body = form + fig.to_html(full_html=False, include_plotlyjs="cdn")
    return _page(f"{sym} chart", body, refresh_seconds=86400)  # no auto-refresh; user controls via form


def _coin_chart_json(iid: str) -> str:
    """Return JSON string with ts/mid/bid/ask/micro arrays from the module-level _second_rolling.

    Returns empty arrays when no snapshots exist for this iid.
    Timestamps are converted from nanoseconds to milliseconds for Plotly.
    """
    snaps = list(_second_rolling.get(iid, []))
    if not snaps:
        return json.dumps({"ts": [], "mid": [], "bid": [], "ask": [], "micro": []})
    ts: list[int] = []
    mid_vals: list[float] = []
    bid_vals: list[float] = []
    ask_vals: list[float] = []
    micro_vals: list[float] = []
    for s in snaps:
        if not s["bid_prices"] or not s["ask_prices"]:
            continue
        bp, ap = s["bid_prices"][0], s["ask_prices"][0]
        bs, as_ = s["bid_sizes"][0], s["ask_sizes"][0]
        total = bs + as_
        ts.append(s["ts_event"] // 1_000_000)  # ns → ms for Plotly datetime axis
        mid_vals.append((bp + ap) / 2)
        bid_vals.append(bp)
        ask_vals.append(ap)
        micro_vals.append((bp * as_ + ap * bs) / total if total > 0 else (bp + ap) / 2)
    return json.dumps({"ts": ts, "mid": mid_vals, "bid": bid_vals, "ask": ask_vals, "micro": micro_vals})


def _render_live_page() -> str:
    series = {name: list(points) for name, points in _SERIES.items()}

    fig = go.Figure()
    for name, points in series.items():
        fig.add_trace(
            go.Scatter(
                x=[ts for ts, _ in points],
                y=[value for _, value in points],
                mode="lines",
                name=name,
            ),
        )
    fig.update_layout(title="Live strategy signals", xaxis_title="ts_event (ns)")

    body = "<h1>Live signals</h1>" + fig.to_html(full_html=False, include_plotlyjs="cdn")
    return _page("ml_signals live", body, refresh_seconds=1)


def _trade_aggregates(snaps: list) -> tuple[float, float, int, int]:
    """Return (total_buy_vol, total_sell_vol, total_buy_count, total_sell_count) over window."""
    return (
        sum(s["buy_volume"] for s in snaps),
        sum(s["sell_volume"] for s in snaps),
        sum(s["buy_count"] for s in snaps),
        sum(s["sell_count"] for s in snaps),
    )


def _rankings_json() -> str:
    """Return pre-formatted cell values for all currently-collected instruments as JSON."""
    rows = _merged_rows()
    result = []
    for row in rows:
        iid = row["instrument_id"]
        cells: dict[str, dict] = {}
        err = row.get("_err")
        for key, _, fmt_fn, color_fn in RANKING_COLS:
            v = row.get(key)
            if v is None:
                cells[key] = {"text": "!", "color": "#f85149"} if err else {"text": "—", "color": None}
            else:
                try:
                    cells[key] = {
                        "text": fmt_fn(v),  # type: ignore[operator]
                        "color": color_fn(v) if color_fn else None,  # type: ignore[operator]
                    }
                except Exception:
                    cells[key] = {"text": "ERR", "color": "#f85149"}
        result.append({"instrument_id": iid, "cells": cells})
    age_s = round(time.time() - _LAST_INGEST_TS, 1) if _LAST_INGEST_TS else None
    stale = age_s is None or age_s > 10
    return json.dumps({"rows": result, "ingest_count": _INGEST_COUNT, "age_s": age_s, "stale": stale})


def _ingest_batch(batch: list[dict]) -> None:
    """Ingest a batch of snapshot dicts received from Redis; update rolling state and _LIVE_FAST."""
    global _INGEST_COUNT, _LAST_INGEST_TS
    now_ns = time.time_ns()
    for snap_dict in batch:
        iid = snap_dict["instrument_id"]
        _second_rolling[iid].append(snap_dict)

        # Initialize OFI/OBI indicators on first sight
        if iid not in _OFI_INDS:
            _OFI_INDS[iid] = MultiLevelOFI(levels=10, window=50, zscore_window=_OFI_ZSCORE_WINDOW)
        if iid not in _OFI_RAW_INDS:
            _OFI_RAW_INDS[iid] = {
                3:  MultiLevelOFI(levels=3,  window=300),
                5:  MultiLevelOFI(levels=5,  window=300),
                10: MultiLevelOFI(levels=10, window=300),
            }
        if iid not in _OBI_INDS:
            _OBI_INDS[iid] = {
                3:  MultiLevelOBI(levels=3),
                5:  MultiLevelOBI(levels=5),
                10: MultiLevelOBI(levels=10),
            }

        # Gap detection: >3s gap means collector reconnected — clear stale prev state
        last_fed = _LAST_FED.get(iid, 0)
        if last_fed > 0 and (snap_dict["ts_event"] - last_fed) > 3_000_000_000:
            _OFI_INDS[iid].clear_prev_state()
            for raw_ind in _OFI_RAW_INDS[iid].values():
                raw_ind.clear_prev_state()

        # Feed OFI incrementally (one new snap per tick — O(1), not O(window))
        _OFI_INDS[iid].update_raw(
            snap_dict["bid_prices"], snap_dict["bid_sizes"],
            snap_dict["ask_prices"], snap_dict["ask_sizes"],
        )
        for raw_ind in _OFI_RAW_INDS[iid].values():
            raw_ind.update_raw(
                snap_dict["bid_prices"], snap_dict["bid_sizes"],
                snap_dict["ask_prices"], snap_dict["ask_sizes"],
            )
        _LAST_FED[iid] = snap_dict["ts_event"]

        # Compute window metrics from rolling buffer (plain dict access)
        snaps = list(_second_rolling[iid])
        latest = snap_dict  # already appended above

        ofi_10_z = _OFI_INDS[iid].value if _OFI_INDS[iid].initialized else None
        ofi_3  = _OFI_RAW_INDS[iid][3].value  if _OFI_RAW_INDS[iid][3].initialized  else None
        ofi_5  = _OFI_RAW_INDS[iid][5].value  if _OFI_RAW_INDS[iid][5].initialized  else None
        ofi_10 = _OFI_RAW_INDS[iid][10].value if _OFI_RAW_INDS[iid][10].initialized else None

        if latest["bid_sizes"] and latest["ask_sizes"]:
            for obi_ind in _OBI_INDS[iid].values():
                obi_ind.update_raw(latest["bid_sizes"], latest["ask_sizes"])
        obi_3  = _OBI_INDS[iid][3].value  if _OBI_INDS[iid][3].initialized  else None
        obi_5  = _OBI_INDS[iid][5].value  if _OBI_INDS[iid][5].initialized  else None
        obi_10 = _OBI_INDS[iid][10].value if _OBI_INDS[iid][10].initialized else None

        tb_vol, ts_vol, tb_cnt, ts_cnt = _trade_aggregates(snaps)
        total_count = tb_cnt + ts_cnt
        mid = (
            (latest["bid_prices"][0] + latest["ask_prices"][0]) / 2
            if latest["bid_prices"] and latest["ask_prices"] else None
        )
        has_tob = (
            latest["bid_prices"] and latest["ask_prices"]
            and (latest["bid_sizes"][0] + latest["ask_sizes"][0]) > 0
        )
        microprice = (
            (
                latest["bid_prices"][0] * latest["ask_sizes"][0]
                + latest["ask_prices"][0] * latest["bid_sizes"][0]
            ) / (latest["bid_sizes"][0] + latest["ask_sizes"][0])
            if has_tob else None
        )
        mids = [
            (s["bid_prices"][0] + s["ask_prices"][0]) / 2
            for s in snaps if s["bid_prices"] and s["ask_prices"]
        ]
        rets = [(mids[i] - mids[i - 1]) / mids[i - 1] for i in range(1, len(mids))]
        volatility = statistics.stdev(rets) if len(rets) >= 2 else None

        _LIVE_FAST[iid] = {
            "ts": now_ns,
            "instrument_id": iid,
            "_err": None,
            "ofi": ofi_10,
            "ofi_3": ofi_3,
            "ofi_5": ofi_5,
            "ofi_10": ofi_10,
            "ofi_10_z": ofi_10_z,
            "obi_3": obi_3,
            "obi_5": obi_5,
            "obi_10": obi_10,
            "microprice": microprice,
            "microprice_lean": (microprice - mid) if microprice is not None and mid is not None else None,
            "spread": (
                latest["ask_prices"][0] - latest["bid_prices"][0]
                if latest["ask_prices"] and latest["bid_prices"] else None
            ),
            "price": mid,
            "volatility": volatility,
            "cvd": tb_vol - ts_vol,
            "volume_delta": latest["buy_volume"] - latest["sell_volume"],
            "buy_count": latest["buy_count"],
            "sell_count": latest["sell_count"],
            "avg_trade_size": (tb_vol + ts_vol) / total_count if total_count > 0 else None,
        }
    _INGEST_COUNT += len(batch)
    _LAST_INGEST_TS = time.time()


# --- aiohttp route handlers ---


async def rankings_handler(request: web.Request) -> web.Response:
    return web.Response(text=_INDEX_HTML, content_type="text/html")


async def rankings_json_handler(request: web.Request) -> web.Response:
    return web.Response(text=_rankings_json(), content_type="application/json")


async def debug_handler(request: web.Request) -> web.Response:
    fast_iids = list(_LIVE_FAST.keys())
    sample = {}
    if fast_iids:
        iid = fast_iids[0]
        e = _LIVE_FAST[iid]
        sample = {"iid": iid, "ts": e.get("ts"), "price": e.get("price"),
                  "buy_count": e.get("buy_count"), "ofi_10_z": e.get("ofi_10_z")}
    return web.Response(
        text=json.dumps({
            "live_fast_count": len(_LIVE_FAST),
            "live_slow_count": len(_LIVE_SLOW),
            "ofi_inds_count": len(_OFI_INDS),
            "sample": sample,
        }),
        content_type="application/json",
    )


async def coin_handler(request: web.Request) -> web.Response:
    return web.Response(text=_INDEX_HTML, content_type="text/html")


async def coin_json_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    return web.Response(text=_coin_chart_json(symbol), content_type="application/json")


async def live_coin_json_handler(request: web.Request) -> web.Response:
    """Raw indicator values for /coin/{id} live panel — polled every 5s."""
    symbol = request.match_info["id"]
    m = _merged_live(symbol)
    ind_keys = [
        "ofi_10", "ofi_5", "ofi_3", "obi_10", "obi_5", "obi_3",
        "microprice", "microprice_lean", "spread", "cvd",
        "volume_delta", "buy_count", "sell_count", "avg_trade_size",
    ]
    result = {k: m.get(k) for k in ind_keys}
    return web.Response(text=json.dumps(result), content_type="application/json")


async def chart_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    import datetime as _dt
    now_ms = int(time.time() * 1000)

    def _parse_dt(key: str, default_ms: int) -> int:
        v = qs.get(key)
        if v:
            try:
                return int(_dt.datetime.fromisoformat(v).timestamp() * 1000)
            except ValueError:
                pass
        return default_ms

    start_ms = _parse_dt("start", now_ms - 4 * 3600 * 1000)
    end_ms = _parse_dt("end", now_ms)
    html_str = await asyncio.to_thread(_render_chart_page, symbol, start_ms, end_ms)
    return web.Response(text=html_str, content_type="text/html")


async def history_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    html_str = await asyncio.to_thread(_render_history_page, symbol)
    return web.Response(text=html_str, content_type="text/html")


async def live_handler(request: web.Request) -> web.Response:
    return web.Response(text=_render_live_page(), content_type="text/html")


# --- background tasks ---


@asynccontextmanager
async def redis_subscriber_ctx(app: web.Application):  # type: ignore[type-arg]
    """Lifecycle context: run _redis_listener as a background task."""
    task = asyncio.create_task(_redis_listener(app["redis_url"]))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _redis_listener(redis_url: str) -> None:
    """Subscribe to snapshots:1s and call _ingest_batch on each message.

    Outer while True reconnects on any non-cancellation exception (ARCH-03).
    Malformed JSON is logged and skipped — subscriber always continues (T-03-01).
    """
    logger.info("Redis listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("Redis listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("snapshots:1s")
                logger.info("Redis listener subscribed to snapshots:1s")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        batch = json.loads(message["data"])
                        _ingest_batch(batch)
                        logger.info("Ingested batch: %d instruments, live_fast now %d", len(batch), len(_LIVE_FAST))
                    except Exception as exc:
                        logger.warning("Redis message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise  # propagate cancellation cleanly
        except Exception as exc:
            logger.warning("Redis subscriber error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)


@asynccontextmanager
async def slow_loop_ctx(app: web.Application):  # type: ignore[type-arg]
    """Lifecycle context: run _slow_loop_task as a background task."""
    task = asyncio.create_task(_slow_loop_task(app["catalog_path"]))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _slow_loop_task(catalog_path: str) -> None:
    """Full snapshot (price/pct/vol + book metrics) every DB_WRITE_INTERVAL_SECONDS."""
    db_path = str(Path(catalog_path).parent / "metrics.db")
    while True:
        try:
            snapshots = await asyncio.to_thread(metrics_computer.compute_all, catalog_path)
            if snapshots:
                for s in snapshots:
                    _LIVE_SLOW[s["instrument_id"]] = s
                await asyncio.to_thread(metrics_store.write, snapshots, db_path)
        except Exception:
            logger.exception("Slow metrics loop failed")
        await asyncio.sleep(DB_WRITE_INTERVAL_SECONDS)


def make_app(redis_url: str, catalog_path: str) -> web.Application:
    """Return a configured aiohttp Application with all routes and background tasks."""
    # Pre-populate _LIVE_SLOW from last SQLite snapshot so rankings show immediately
    db_path = str(Path(catalog_path).parent / "metrics.db")
    try:
        rows = metrics_store.latest(db_path)
        for r in rows:
            _LIVE_SLOW[r["instrument_id"]] = r
        if rows:
            logger.info("Pre-loaded %d instruments from metrics.db", len(rows))
    except Exception:
        logger.warning("Could not pre-load metrics.db — starting cold", exc_info=True)

    app = web.Application()
    app["redis_url"] = redis_url
    app["catalog_path"] = catalog_path

    app.cleanup_ctx.append(redis_subscriber_ctx)
    app.cleanup_ctx.append(slow_loop_ctx)

    app.router.add_get("/", rankings_handler)
    app.router.add_get("/api/rankings", rankings_json_handler)
    app.router.add_get("/debug", debug_handler)
    app.router.add_get("/coin/{id}", coin_handler)
    app.router.add_get("/data/coin/{id}", coin_json_handler)
    app.router.add_get("/data/live/{id}", live_coin_json_handler)
    app.router.add_get("/chart/{id}", chart_handler)
    app.router.add_get("/history/{id}", history_handler)
    app.router.add_get("/live", live_handler)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
    catalog_path = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))
    web.run_app(make_app(redis_url, catalog_path), host="0.0.0.0", port=port)
