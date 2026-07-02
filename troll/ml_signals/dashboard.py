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

Subscribes to Redis channel snapshots:raw (published by the collector every second),
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
# Gap threshold for coin chart: if consecutive snapshots are further apart than this
# (in milliseconds), insert a null data point to break the Plotly line. This converts
# a misleading horizontal "flatline" (Plotly connecting across a gap) into an honest
# visual break. Gaps arise when the collector's staleness guard skips stale books.
_CHART_GAP_THRESHOLD_MS: int = 2500  # 2.5 seconds

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

# Rolling per-second indicator values for the signal chart (same window as _second_rolling).
# Each entry: {"ts": ms, "ofi_10_z": float|None, "obi_10": float|None, "lean": float|None}
_ind_rolling: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))

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
var _chartData=null,_diffA=null,_diffB=null,_diffBoxHTML='';
var _coinIid=null,_coinMode='lines',_coinBarSeconds=60,_coinHistStart=null,_coinHistEnd=null;

function esc(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function setStatus(s){document.getElementById("status").innerHTML=s;}
function setApp(h){document.getElementById("app").innerHTML=h;}

function clearDiff(){
  _diffA=null;_diffB=null;_diffBoxHTML='';
  var db=document.getElementById('diff-box');
  if(db)db.innerHTML='';
}
function _setDiffWaiting(snap){
  _diffA=snap;_diffB=null;
  _diffBoxHTML='<span style="color:#8b949e;font-size:11px">&#9679; A: '+new Date(snap.ts).toLocaleTimeString()+' — click second point  <a onclick="clearDiff();return false" href="#" style="color:#8b949e">[cancel]</a></span>';
  var db=document.getElementById('diff-box');
  if(db)db.innerHTML=_diffBoxHTML;
}
function handleChartClick(data){
  if(!data.points.length||!_chartData)return;
  var pt=data.points[0];
  var idx=pt.pointIndex;
  if(idx==null||idx>=_chartData.ts.length)return;
  var snap={ts:_chartData.ts[idx],bid:_chartData.bid[idx],ask:_chartData.ask[idx],
            mid:_chartData.mid[idx],micro:_chartData.micro[idx],price:_chartData.price[idx]};
  if(_diffB){
    _setDiffWaiting(snap);
  }else if(!_diffA){
    _setDiffWaiting(snap);
  }else{
    _diffB=snap;
    _diffBoxHTML=buildDiff(_diffA,snap);
    var db=document.getElementById('diff-box');
    if(db)db.innerHTML=_diffBoxHTML;
  }
}
function fmtP(v){if(v==null)return'—';return v>100?v.toFixed(2):v>1?v.toFixed(4):v.toFixed(6);}
function buildDiff(a,b){
  var metrics=[['bid','Bid'],['ask','Ask'],['mid','Mid'],['micro','Microprice'],['price','Eff Price']];
  var parts=metrics.map(function(m){
    var va=a[m[0]],vb=b[m[0]];
    if(va==null||vb==null||va===0)return'';
    var pct=(vb-va)/Math.abs(va)*100;
    var sign=pct>=0?'+':'';
    var col=pct>0?'#3fb950':pct<0?'#f85149':'#8b949e';
    return '<span style="margin-right:16px"><b>'+m[1]+'</b> <span style="color:'+col+'">'+sign+pct.toFixed(4)+'%</span></span>';
  }).filter(Boolean).join('');
  var tA=new Date(a.ts).toLocaleTimeString(),tB=new Date(b.ts).toLocaleTimeString();
  return '<span style="color:#8b949e;font-size:11px">'+tA+' → '+tB+'</span>  '+parts
    +'<a style="color:#8b949e;font-size:11px;margin-left:8px" onclick="clearDiff();return false" href="#">×</a>';
}

function _fmtDTL(d){var p=function(n){return n<10?'0'+n:String(n);};return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes());}
function setCoinMode(m){_coinMode=m;_updateModeButtons();if(_coinHistStart)_fetchHistCoin(_coinIid,_coinHistStart,_coinHistEnd);}
function _updateModeButtons(){
  var bl=document.getElementById('btn-lines'),bc=document.getElementById('btn-candles');
  if(bl)bl.style.borderColor=_coinMode==='lines'?'#58a6ff':'#444';
  if(bc)bc.style.borderColor=_coinMode==='candles'?'#58a6ff':'#444';
}
function onBarChange(){
  var sel=document.getElementById('bar-sel');
  if(sel)_coinBarSeconds=parseInt(sel.value);
  if(_coinHistStart)_fetchHistCoin(_coinIid,_coinHistStart,_coinHistEnd);
}
function loadCoinDateRange(){
  var s=document.getElementById('coin-start'),e=document.getElementById('coin-end');
  if(!s||!e)return;
  _coinHistStart=s.value;_coinHistEnd=e.value;
  clearInterval(timer);
  var b=document.getElementById('btn-live');
  if(b){b.style.color='#8b949e';b.style.borderColor='#444';}
  _fetchHistCoin(_coinIid,_coinHistStart,_coinHistEnd);
}
function resetCoinLive(){
  _coinHistStart=null;_coinHistEnd=null;
  var b=document.getElementById('btn-live');
  if(b){b.style.color='#3fb950';b.style.borderColor='#3fb950';}
  clearInterval(timer);
  pollCoin(_coinIid);
  timer=setInterval(function(){if(!_coinHistStart)pollCoin(_coinIid);},1000);
}
function _fetchHistCoin(iid,start,end){
  setStatus('Loading…');
  var bar=_coinBarSeconds;
  fetch('/data/coin/'+encodeURIComponent(iid)+'/candles?start='+encodeURIComponent(start)+'&end='+encodeURIComponent(end)+'&bar='+bar)
    .then(function(r){return r.json();})
    .then(function(d){_renderCandleChart(d.candles);setStatus('Loaded '+d.candles.length+' candles');})
    .catch(function(err){setStatus('Error: '+err);});
}
function _renderCandleChart(candles){
  if(!candles||!candles.length)return;
  var x=candles.map(function(c){return new Date(c.t);});
  Plotly.react('live-chart',[{
    type:'candlestick',x:x,
    open:candles.map(function(c){return c.o;}),
    high:candles.map(function(c){return c.h;}),
    low:candles.map(function(c){return c.l;}),
    close:candles.map(function(c){return c.c;}),
    name:'price',
    increasing:{line:{color:'#26a69a'}},
    decreasing:{line:{color:'#ef5350'}},
  }],{height:300,template:'plotly_dark',
    xaxis:{type:'date',rangeslider:{visible:false}},
    margin:{t:10,b:30,l:60,r:10}});
}

function showRankings(){
  clearDiff();
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
  clearDiff();
  clearInterval(timer);
  _coinIid=iid;_coinHistStart=null;_coinHistEnd=null;_coinMode='lines';
  history.pushState({iid:iid},"","/coin/"+encodeURIComponent(iid));
  var label=iid.split("-").slice(0,2).join("-");
  var now=new Date(),endV=_fmtDTL(now),startV=_fmtDTL(new Date(now.getTime()-4*3600*1000));
  setApp(
    "<h1>"+esc(label)+" <small><a href=\\"/chart/"+esc(iid)+"\\">chart</a>"
    +" | <a onclick=\\"showRankings();return false\\" href=\\"/\\">back</a></small></h1>"
    +"<div style=\\"display:flex;gap:8px;align-items:center;padding:6px 0;flex-wrap:wrap;border-bottom:1px solid #21262d;margin-bottom:6px\\">"
    +"<button id=\\"btn-lines\\" onclick=\\"setCoinMode('lines')\\" style=\\"background:#21262d;color:#c9d1d9;border:1px solid #58a6ff;padding:3px 10px;cursor:pointer\\">Lines</button>"
    +"<button id=\\"btn-candles\\" onclick=\\"setCoinMode('candles')\\" style=\\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px 10px;cursor:pointer\\">Candles</button>"
    +"<select id='bar-sel' onchange='onBarChange()' style=\\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px\\">"
    +"<option value='30'>30s</option><option value='60' selected>1m</option><option value='300'>5m</option>"
    +"<option value='900'>15m</option><option value='3600'>1h</option></select>"
    +" &nbsp;|&nbsp; From <input type='datetime-local' id='coin-start' value='"+startV+"' style=\\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:2px\\">"
    +" To <input type='datetime-local' id='coin-end' value='"+endV+"' style=\\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:2px\\">"
    +" <button onclick='loadCoinDateRange()' style=\\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px 10px;cursor:pointer\\">Load</button>"
    +" <button id='btn-live' onclick='resetCoinLive()' style=\\"background:#21262d;color:#3fb950;border:1px solid #3fb950;padding:3px 10px;cursor:pointer\\">&#9679; Live</button>"
    +"</div>"
    +"<h2>Indicators</h2><table id='ind-tbl'></table>"
    +"<div id='price-ticker' style=\\"padding:6px 0 2px;font-size:14px;font-family:monospace;letter-spacing:0.04em;border-top:1px solid #21262d;margin-top:10px\\"></div>"
    +"<div id='sig-chart' style=\\"height:180px;margin-top:8px\\"></div>"
    +"<div id='live-chart' style=\\"height:300px;margin-top:4px\\"></div>"
    +"<div id='diff-box' style=\\"min-height:22px;padding:5px 2px;border-top:1px solid #21262d;font-size:12px;font-family:monospace\\"></div>"
  );
  pollCoin(iid);
  timer=setInterval(function(){if(!_coinHistStart)pollCoin(iid);},1000);
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
  _chartData=chart;
  var rows=IND.map(function(k){
    var v=ind[k[0]];
    return "<tr><td>"+esc(k[1])+"</td><td>"+(v!=null?esc(String(v)):"&mdash;")+"</td></tr>";
  }).join("");
  var tbl=document.getElementById('ind-tbl');
  if(tbl)tbl.innerHTML=rows;
  if(chart.sig_ts&&chart.sig_ts.length){
    var sx=chart.sig_ts.map(function(t){return new Date(t);});
    var sigTraces=[];
    if(chart.ofi_10_z&&chart.ofi_10_z.some(function(v){return v!=null;}))
      sigTraces.push({x:sx,y:chart.ofi_10_z,name:"OFI10z",mode:"lines",line:{color:"#79c0ff",width:1.5}});
    if(chart.obi_10&&chart.obi_10.some(function(v){return v!=null;}))
      sigTraces.push({x:sx,y:chart.obi_10,name:"OBI10",mode:"lines",line:{color:"#d2a8ff",width:1.5}});
    if(sigTraces.length)
      Plotly.react("sig-chart",sigTraces,{height:180,template:"plotly_dark",
        xaxis:{type:"date"},yaxis:{zeroline:true,zerolinecolor:"#444"},
        margin:{t:20,b:20,l:50,r:10},legend:{orientation:"h",y:1.15}});
  }
  if(_coinMode==='candles'){
    var sel=document.getElementById('bar-sel');
    var bar=sel?sel.value:'60';
    fetch('/data/coin/'+encodeURIComponent(iid)+'/candles?bar='+bar)
      .then(function(r){return r.json();})
      .then(function(d){_renderCandleChart(d.candles);});
  }else if(chart.ts&&chart.ts.length){
    var x=chart.ts.map(function(t){return new Date(t);});
    var traces=[
      {x:x,y:chart.bid,   name:"bid",        mode:"lines",line:{color:"#26a69a",width:1}},
      {x:x,y:chart.ask,   name:"ask",        mode:"lines",line:{color:"#ef5350",width:1}},
      {x:x,y:chart.mid,   name:"mid",        mode:"lines",line:{color:"#aaa",width:1,dash:"dot"}},
      {x:x,y:chart.micro, name:"microprice", mode:"lines",line:{color:"#f0883e",width:1.5,dash:"dot"}},
      {x:x,y:chart.price, name:"price",      mode:"lines",line:{color:"#e3b341",width:1.5}}
    ];
    if(_diffA&&_diffA.price!=null)
      traces.push({x:[new Date(_diffA.ts)],y:[_diffA.price],name:"A",mode:"markers+text",
        text:["A"],textposition:"top center",showlegend:false,
        marker:{color:"#ffffff",size:10,symbol:"circle",line:{color:"#e3b341",width:2}}});
    if(_diffB&&_diffB.price!=null)
      traces.push({x:[new Date(_diffB.ts)],y:[_diffB.price],name:"B",mode:"markers+text",
        text:["B"],textposition:"top center",showlegend:false,
        marker:{color:"#e3b341",size:10,symbol:"circle",line:{color:"#ffffff",width:2}}});
    Plotly.react("live-chart",traces,{height:300,template:"plotly_dark",xaxis:{type:"date"},
       margin:{t:10,b:30,l:60,r:10},legend:{orientation:"h"}});
    var liveEl=document.getElementById('live-chart');
    if(liveEl){
      liveEl.removeAllListeners&&liveEl.removeAllListeners('plotly_click');
      liveEl.on('plotly_click',handleChartClick);
    }
  }
  var tickerEl=document.getElementById('price-ticker');
  if(tickerEl&&chart.ts&&chart.ts.length){
    var n=chart.ts.length-1;
    tickerEl.innerHTML=
      '<span style="color:#26a69a">Bid</span> '+fmtP(chart.bid[n])
      +'&emsp;<span style="color:#ef5350">Ask</span> '+fmtP(chart.ask[n])
      +'&emsp;<span style="color:#aaa">Mid</span> '+fmtP(chart.mid[n])
      +'&emsp;<span style="color:#f0883e">Micro</span> '+fmtP(chart.micro[n])
      +'&emsp;<span style="color:#e3b341">Price</span> '+fmtP(chart.price[n]);
  }
  var diffEl=document.getElementById('diff-box');
  if(diffEl)diffEl.innerHTML=_diffBoxHTML;
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


def _live_candles_json(iid: str, bar_seconds: int) -> str:
    """Build OHLC candles from the rolling 1s snapshot buffer (mid price)."""
    snaps = list(_second_rolling.get(iid, []))
    if not snaps:
        return json.dumps({"candles": []})
    buckets: dict[int, list[float]] = {}
    for s in snaps:
        if not s["bid_prices"] or not s["ask_prices"]:
            continue
        bp, ap = s["bid_prices"][0], s["ask_prices"][0]
        if bp >= ap:
            continue
        mid = (bp + ap) / 2
        bucket = (s["ts_event"] // 1_000_000_000 // bar_seconds) * bar_seconds
        buckets.setdefault(bucket, []).append(mid)
    candles = [
        {"t": t * 1000, "o": mids[0], "h": max(mids), "l": min(mids), "c": mids[-1]}
        for t, mids in sorted(buckets.items())
    ]
    return json.dumps({"candles": candles})


def _historical_candles_json(iid: str, start_ms: int, end_ms: int, bar_seconds: int) -> str:
    """Build OHLC candles from trade_ticks in the Parquet catalog."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from ml_signals.candles import build_candles as _build
    catalog = ParquetDataCatalog(CATALOG_PATH)
    start_ns = start_ms * 1_000_000
    end_ns = end_ms * 1_000_000
    trades = catalog.trade_ticks(instrument_ids=[iid], start=start_ns, end=end_ns)
    raw = [(t.ts_event, t.price.as_double()) for t in trades]
    if not raw:
        return json.dumps({"candles": []})
    candle_data = _build(raw, period_seconds=bar_seconds)
    candles = [
        {"t": c.ts_open // 1_000_000, "o": c.open, "h": c.high, "l": c.low, "c": c.close}
        for c in candle_data
    ]
    return json.dumps({"candles": candles})


def _coin_chart_json(iid: str) -> str:
    """Return JSON with price series (bid/ask/mid/micro/price) and signal series (ofi_10_z/obi_10).

    price = CVD-weighted effective trade price: skews from mid toward ask on net buying,
    toward bid on net selling. Equals mid when no trades occurred in that second.
    sig_ts/ofi_10_z/obi_10 come from _ind_rolling (same 300-point window).
    Timestamps are milliseconds for Plotly.
    """
    snaps = list(_second_rolling.get(iid, []))
    inds = list(_ind_rolling.get(iid, []))
    if not snaps:
        return json.dumps({"ts": [], "mid": [], "bid": [], "ask": [], "micro": [], "price": [],
                           "sig_ts": [], "ofi_10_z": [], "obi_10": []})
    ts: list[int] = []
    mid_vals: list[float | None] = []
    bid_vals: list[float | None] = []
    ask_vals: list[float | None] = []
    micro_vals: list[float | None] = []
    price_vals: list[float | None] = []
    prev_ts_ms: int | None = None
    for s in snaps:
        if not s["bid_prices"] or not s["ask_prices"]:
            continue
        bp, ap = s["bid_prices"][0], s["ask_prices"][0]
        if bp >= ap:  # crossed/touched snapshot — skip (stale data from reconnect)
            continue
        curr_ts_ms = s["ts_event"] // 1_000_000
        # Gap detection: if consecutive valid snapshots are further apart than the
        # threshold, insert a null data point to break the Plotly line.  Without this,
        # Plotly mode="lines" draws a horizontal line across the gap, producing a
        # misleading flatline at the last-known price.  Gaps arise when the collector's
        # staleness guard (_STALE_BOOK_NS) skips stale books during WS reconnect recovery.
        if prev_ts_ms is not None and (curr_ts_ms - prev_ts_ms) > _CHART_GAP_THRESHOLD_MS:
            ts.append(curr_ts_ms - 1)
            bid_vals.append(None)
            ask_vals.append(None)
            mid_vals.append(None)
            micro_vals.append(None)
            price_vals.append(None)
        prev_ts_ms = curr_ts_ms
        bs, as_ = s["bid_sizes"][0], s["ask_sizes"][0]
        total = bs + as_
        mid = (bp + ap) / 2
        micro = (bp * as_ + ap * bs) / total if total > 0 else mid
        tv = s["buy_volume"] + s["sell_volume"]
        if tv > 0:
            price = mid + ((s["buy_volume"] - s["sell_volume"]) / tv) * (ap - bp) * 0.5
        else:
            price = mid
        ts.append(curr_ts_ms)
        mid_vals.append(mid)
        bid_vals.append(bp)
        ask_vals.append(ap)
        micro_vals.append(micro)
        price_vals.append(price)
    return json.dumps({
        "ts": ts, "mid": mid_vals, "bid": bid_vals, "ask": ask_vals,
        "micro": micro_vals, "price": price_vals,
        "sig_ts": [e["ts"] for e in inds],
        "ofi_10_z": [e["ofi_10_z"] for e in inds],
        "obi_10": [e["obi_10"] for e in inds],
    })


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
        # Append per-second indicator snapshot for the signal chart.
        lean = (microprice - mid) if microprice is not None and mid is not None else None
        _ind_rolling[iid].append({
            "ts": latest["ts_event"] // 1_000_000,
            "ofi_10_z": ofi_10_z,
            "obi_10": obi_10,
            "lean": lean,
        })
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


async def coin_candles_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    import datetime as _dt
    bar_seconds = max(1, int(qs.get("bar", "60")))

    def _parse_ms(key: str) -> int | None:
        v = qs.get(key)
        if v:
            try:
                return int(_dt.datetime.fromisoformat(v).timestamp() * 1000)
            except ValueError:
                pass
        return None

    start_ms = _parse_ms("start")
    end_ms = _parse_ms("end")
    if start_ms is not None and end_ms is not None:
        data = await asyncio.to_thread(_historical_candles_json, symbol, start_ms, end_ms, bar_seconds)
    else:
        data = _live_candles_json(symbol, bar_seconds)
    return web.Response(text=data, content_type="application/json")


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
    """Subscribe to snapshots:raw and call _ingest_batch on each message.

    Outer while True reconnects on any non-cancellation exception (ARCH-03).
    Malformed JSON is logged and skipped — subscriber always continues (T-03-01).
    """
    logger.info("Redis listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("Redis listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("snapshots:raw")
                logger.info("Redis listener subscribed to snapshots:raw")
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
    app.router.add_get("/data/coin/{id}/candles", coin_candles_handler)
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
