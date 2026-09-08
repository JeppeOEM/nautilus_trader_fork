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
- `/coin/{id}`      live indicator panel (1s poll) -- no chart here, see `/chart/{id}`
- `/chart/{id}`     interactive Candles/Lines/Ticks chart + per-event microstructure panes (Parquet-backed)
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
import math
import os
import time
from collections import defaultdict
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import redis.asyncio as aioredis
from aiohttp import web
from plotly.subplots import make_subplots
from ranking_engine import metrics_store

from ml_signals import chart_data as _chart_data
from ml_signals import chart_indicators as _chart_indicators
from ml_signals import custom_indicators as _custom_indicators
from ml_signals import ranking_columns as _ranking_columns
from ml_signals.catalog_stats import list_instruments
from ml_signals.indicators import microprice as calc_microprice


logger = logging.getLogger(__name__)

CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")

# Path to the shared SQLite metrics store -- ranking_engine is the sole writer (Task
# 7); dashboard only ever reads it (metrics_store.latest/nearest/history). Mounted from
# a dedicated *directory* in docker-compose.yml (not a single-file mount): SQLite WAL
# mode creates metrics.db-wal/metrics.db-shm sidecar files next to the main file, which
# a single-file bind mount can't expose on a path shared with ranking_engine's own
# read-write mount of the same store.
METRICS_DB_PATH: str = os.environ.get(
    "METRICS_DB_PATH", str(Path(CATALOG_PATH).parent / "metrics" / "metrics.db"),
)

# Gap threshold for coin chart: if consecutive snapshots are further apart than this
# (in milliseconds), insert a null data point to break the Plotly line. This converts
# a misleading horizontal "flatline" (Plotly connecting across a gap) into an honest
# visual break. Gaps arise when the collector's staleness guard skips stale books.
_CHART_GAP_THRESHOLD_MS: int = 2500  # 2.5 seconds

# Rankings-table column metadata is now shared with bot_tui's Coins pane (SSOT-03,
# troll/CLAUDE.md) -- see ml_signals/ranking_columns.py for the single definition both
# UIs render from.
RANKING_COLS = _ranking_columns.RANKING_COLS
_HISTORY_ONLY_COLS = _ranking_columns._HISTORY_ONLY_COLS

_SERIES: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=2000))

_INGEST_COUNT: int = 0       # total batches ingested; increments ~1/s; visible in API
_LAST_INGEST_TS: float = 0.0  # wall-clock seconds of last successful ingest

# rankings:live is now the sole source of every rankings-table/coin-panel metric (AD-9,
# troll/CLAUDE.md SSOT-01/02) -- dashboard is a pure reader, never computing OFI/OBI/
# microprice/spread/cvd/pct_1h/pct_24h/volatility/rank/volume24h/volatility_score
# itself. ranking_engine is the sole computer of all of it.
_LATEST_RANKING: dict | None = None
_LATEST_RANKING_RECEIVED_AT: float = 0.0
# 3x ranking_engine's own RANKING_HEARTBEAT_SECONDS default (5s) -- a single missed
# heartbeat shouldn't immediately flag stale, but two consecutive misses should (AD-9).
_RANKING_STALE_SECONDS: float = 15.0

# Rolling 1s snapshots received from Redis (plain dicts from DydxSecondSnapshot.to_dict()).
# Feeds only candle/live-chart rendering below (_live_candles_json/_coin_chart_json) --
# every derived metric that has a rankings:live equivalent now comes from there instead
# of being recomputed from this raw stream a second time.
# 3600 (1hr) rather than a few minutes so live candle mode has enough bars to be
# readable zoomed out -- a short window only ever renders a handful of candles.
_second_rolling: dict[str, deque] = defaultdict(lambda: deque(maxlen=3600))

# Rolling per-second indicator values for the signal chart (same window as
# _second_rolling). Each entry: {"ts": ms, "ofi_10_z": float|None, "obi_10": float|None,
# "lean": float|None} -- fed from each arriving rankings:live message's own per-rank
# fields (_handle_rankings_message below), not computed locally.
_ind_rolling: dict[str, deque] = defaultdict(lambda: deque(maxlen=3600))

_NAV = '<p><a href="/">Rankings</a> | <a href="/live">Live signals</a></p>'


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

# Candles/Lines/Ticks interactive chart widget -- lives only on _render_chart_page's
# /chart/{id} page (consolidated there by Story 8.1; previously also duplicated into
# /coin/{id}, which now has no chart at all -- see /coin/{id}'s ind-groups/price-ticker/
# sig-chart for what remains there). Keeps the drag-to-pan/scroll-to-zoom state machine
# (Story 7.1) and its test coverage (test_dashboard_chart_pan_js.py) in exactly one place.
# Depends on globals the embedding page must declare: _chartState, _relayoutTimer,
# _coinPanning, _coinMode, _coinHistStart, _coinBarSeconds, _coinIid, timer, plus a
# setStatus(s) function, a #live-chart div, and a #diff-box div (Lines-mode click-to-diff,
# Story 8.1 -- _diffA/_diffB/_diffBoxHTML are declared inside this module, not by the
# embedder). Lines mode is backed by /data/coin/{id}/lines (_live_lines_json/
# _historical_lines_json, Story 8.1) -- a different data source than Candles/Ticks
# (order-book bid/ask/mid/micro/price snapshots, not trade-tick OHLC/prints).
# Story 8.4 (indicator picker, Candles-mode only) additionally needs the embedder to
# declare #ind-picker/#ind-picker-list/#ind-picker-note/#ind-table/#ind-panel divs and
# call _fetchIndicatorCatalog() once in its init script; _activeIndicators/
# _indicatorCatalog/_indSeq/_lastCandles/_syncingXRange are declared inside this module,
# same pattern as _diffA/_diffB above. Backed by Story 8.2's /data/indicators/catalog and
# /data/coin/{id}/indicators verbatim (SSOT-03) -- no new Python endpoint. Each add-list
# click creates a fresh {id,name,params} instance so the same indicator can appear more
# than once with different settings; #ind-table renders one editable row per instance.
_LIVE_CHART_JS = """
function _fmtDTL(d){var p=function(n){return n<10?'0'+n:String(n);};return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes());}
function setCoinMode(m){
  _coinMode=m;_chartState=null;_updateModeButtons();
  if(_coinHistStart)_fetchHistCoin(_coinIid,_coinHistStart,_coinHistEnd);
  else if(_coinPanning)_refreshPanningWindow();
}
function _updateModeButtons(){
  var bl=document.getElementById('btn-lines'),bc=document.getElementById('btn-candles'),bt=document.getElementById('btn-ticks');
  if(bl)bl.style.borderColor=_coinMode==='lines'?'#58a6ff':'#444';
  if(bc)bc.style.borderColor=_coinMode==='candles'?'#58a6ff':'#444';
  if(bt)bt.style.borderColor=_coinMode==='ticks'?'#58a6ff':'#444';
  _updateIndicatorPickerEnabled();
}
function onBarChange(){
  var sel=document.getElementById('bar-sel');
  if(sel)_coinBarSeconds=parseInt(sel.value);
  _chartState=null;
  if(_coinHistStart)_fetchHistCoin(_coinIid,_coinHistStart,_coinHistEnd);
  else if(_coinPanning)_refreshPanningWindow();
}
// Bar/mode change while frozen from a drag (no explicit Load range): load a fresh,
// coherent default window instead of leaving the stale pan-extended trace on screen.
function _refreshPanningWindow(){
  _fetchLiveWindow(_coinIid,_coinMode,_coinBarSeconds)
    .then(function(rows){_setChartRows(_coinIid,_coinMode,_coinBarSeconds,rows);})
    .catch(function(err){setStatus('Error: '+err);});
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
  _coinHistStart=null;_coinHistEnd=null;_coinPanning=false;_chartState=null;
  var b=document.getElementById('btn-live');
  if(b){b.style.color='#3fb950';b.style.borderColor='#3fb950';}
  clearInterval(timer);
  pollCoin(_coinIid);
  timer=setInterval(function(){if(!_coinHistStart&&!_coinPanning)pollCoin(_coinIid);},1000);
}
// Resolve to {rows,truncated} -- truncated means the server clamped the query window
// and/or the row cap, so the caller must not advance its paging cursor past what it
// actually got back (silently skipping data otherwise -- see _loadOlderChunk).
function _fetchCandlesWindow(iid,startMs,endMs,barSeconds){
  return fetch('/data/coin/'+encodeURIComponent(iid)+'/candles?start='+encodeURIComponent(_fmtDTL(new Date(startMs)))+'&end='+encodeURIComponent(_fmtDTL(new Date(endMs)))+'&bar='+barSeconds)
    .then(function(r){return r.json();}).then(function(d){return {rows:d.candles,truncated:false};});
}
function _fetchTicksWindow(iid,startMs,endMs){
  return fetch('/data/coin/'+encodeURIComponent(iid)+'/ticks?start='+encodeURIComponent(_fmtDTL(new Date(startMs)))+'&end='+encodeURIComponent(_fmtDTL(new Date(endMs))))
    .then(function(r){return r.json();}).then(function(d){return {rows:d.ticks,truncated:!!d.truncated};});
}
function _fetchLinesWindow(iid,startMs,endMs){
  return fetch('/data/coin/'+encodeURIComponent(iid)+'/lines?start='+encodeURIComponent(_fmtDTL(new Date(startMs)))+'&end='+encodeURIComponent(_fmtDTL(new Date(endMs))))
    .then(function(r){return r.json();}).then(function(d){return {rows:d.rows,truncated:!!d.truncated};});
}
function _fetchModeWindow(mode,iid,startMs,endMs,barSeconds){
  if(mode==='ticks')return _fetchTicksWindow(iid,startMs,endMs);
  if(mode==='lines')return _fetchLinesWindow(iid,startMs,endMs);
  return _fetchCandlesWindow(iid,startMs,endMs,barSeconds);
}
// Plain-array fetch for the "live" (unfrozen or just-unfroze) default window -- used by
// renderCoin's poll loop and by a bar/mode change that happens mid-pan (_refreshPanningWindow).
function _fetchLiveWindow(iid,mode,bar){
  if(mode==='ticks'){
    var nowMs=Date.now();
    return _fetchTicksWindow(iid,nowMs-30*60*1000,nowMs).then(function(r){return r.rows;});
  }
  if(mode==='lines'){
    return fetch('/data/coin/'+encodeURIComponent(iid)+'/lines')
      .then(function(r){return r.json();}).then(function(d){return d.rows;});
  }
  return fetch('/data/coin/'+encodeURIComponent(iid)+'/candles?bar='+bar)
    .then(function(r){return r.json();}).then(function(d){return d.candles;});
}
function _fetchHistCoin(iid,start,end){
  setStatus('Loading…');
  var bar=_coinBarSeconds;
  var startMs=new Date(start).getTime(),endMs=new Date(end).getTime();
  _fetchModeWindow(_coinMode,iid,startMs,endMs,bar)
    .then(function(result){
      _setChartRows(iid,_coinMode,bar,result.rows);
      setStatus('Loaded '+result.rows.length+' rows'+(result.truncated?' (truncated -- window too wide)':''));
    })
    .catch(function(err){setStatus('Error: '+err);});
}
// -- Shared pan-to-load-more state (candles + ticks) --------------------------------------
function _setChartRows(iid,mode,barSeconds,rows){
  _chartState={iid:iid,mode:mode,barSeconds:barSeconds,rows:rows.slice(),
    cursorStart:rows.length?rows[0].t:null,exhaustedLeft:false,loading:false};
  _renderChartRows();
}
function _renderChartRows(){
  if(!_chartState)return;
  if(_chartState.mode==='ticks')_renderTickChart(_chartState.rows);
  else if(_chartState.mode==='lines')_renderLineChart(_chartState.rows);
  else _renderCandleChart(_chartState.rows);
}
function _chunkSpanMs(state){
  var s=state||_chartState;
  if(s.mode==='ticks'||s.mode==='lines')return 15*60*1000;
  return Math.min(Math.max(s.barSeconds*1000*200,3600*1000),_MAX_CHUNK_MS);
}
function _loadOlderChunk(){
  if(!_chartState||_chartState.exhaustedLeft||_chartState.cursorStart==null||_chartState.loading)return;
  var state=_chartState;
  state.loading=true;
  var span=_chunkSpanMs(state);
  var newEnd=state.cursorStart,newStart=newEnd-span;
  return _fetchModeWindow(state.mode,state.iid,newStart,newEnd,state.barSeconds)
    .then(function(result){
      // A coin/mode/bar switch (or another pan session) may have replaced _chartState
      // with a new object while this fetch was in flight -- never mutate/render a chart
      // this response no longer belongs to.
      if(_chartState!==state)return;
      state.loading=false;
      var rows=result.rows;
      if(!rows||!rows.length){state.exhaustedLeft=true;setStatus('Reached start of history');return;}
      state.rows=rows.concat(state.rows);
      // A truncated response (server clamped the window or hit its row cap) didn't cover
      // the full [newStart,newEnd) span -- advance the cursor only to the oldest row
      // actually returned, never past data we never received (silent permanent skip).
      state.cursorStart=result.truncated?rows[0].t:newStart;
      if(result.truncated)setStatus('Busy window truncated -- pan again to keep loading');
      _renderChartRows();
    })
    .catch(function(err){if(_chartState===state)state.loading=false;setStatus('Error: '+err);});
}
function _relayoutXRange(ev){
  if(!ev)return null;
  if(ev['xaxis.range[0]']!=null&&ev['xaxis.range[1]']!=null)return[ev['xaxis.range[0]'],ev['xaxis.range[1]']];
  if(ev['xaxis.range'])return ev['xaxis.range'];
  return null;
}
function _onChartRelayout(ev){
  var rng=_relayoutXRange(ev);
  if(!rng)return;  // not a real pan/zoom (e.g. legend click, resize/autosize)
  _syncChartXRange('live-chart',rng);
  if(!_coinPanning){
    // First drag/zoom: freeze live polling exactly like clicking "Load" does, so the
    // 1s poll loop stops overwriting the candle/tick trace out from under the user. A
    // dedicated flag (not _coinHistStart, which onBarChange/setCoinMode read as a real
    // Load date-range string) so bar/mode changes during a pan session stay coherent.
    _coinPanning=true;
    clearInterval(timer);
    var b=document.getElementById('btn-live');
    if(b){b.style.color='#8b949e';b.style.borderColor='#444';}
    _reconcilePriceBasisOnFirstPan();
  }
  if(_relayoutTimer)clearTimeout(_relayoutTimer);
  _relayoutTimer=setTimeout(function(){_maybeLoadOlder(rng);},200);
}
// Live candles are mid-price based (_live_candles_json); historical/paginated candles
// are trade-price based (_historical_candles_json). Panning left on a live (never
// clicked "Load") candles session would otherwise concat trade-price rows onto a
// mid-price series with a visible seam at the join. Reload the currently-shown window
// from the trade-price source once, on the first pan, so the whole series is consistent
// going forward. Ticks mode has no such mismatch -- live and historical ticks both come
// from the same raw-trade endpoint.
function _reconcilePriceBasisOnFirstPan(){
  if(_coinMode!=='candles'||_coinHistStart||!_chartState||!_chartState.rows.length)return;
  var state=_chartState;
  var firstT=state.rows[0].t,lastT=state.rows[state.rows.length-1].t+state.barSeconds*1000;
  _fetchCandlesWindow(state.iid,firstT,lastT,state.barSeconds).then(function(result){
    if(_chartState!==state||!result.rows.length)return;
    state.rows=result.rows;
    state.cursorStart=result.rows[0].t;
    _renderChartRows();
  });
}
function _maybeLoadOlder(rng){
  if(!_chartState||_chartState.exhaustedLeft||_chartState.cursorStart==null)return;
  var leftMs=new Date(rng[0]).getTime();
  if(isNaN(leftMs))return;
  var margin=_chunkSpanMs(_chartState)*0.5;
  if(leftMs<=_chartState.cursorStart+margin)_loadOlderChunk();
}
function _wireChartRelayout(){
  var liveEl=document.getElementById('live-chart');
  if(liveEl){
    liveEl.removeAllListeners&&liveEl.removeAllListeners('plotly_relayout');
    liveEl.on('plotly_relayout',_onChartRelayout);
  }
}
// -- Indicator picker (Story 8.4, extended for multi-instance): catalog-driven add-list,
// each added instance gets its own id so the same indicator can be added more than once
// with different settings; per-instance settings render as an editable table below the
// chart. Overlay traces are added directly to live-chart's candlestick trace array, a
// dedicated oscillator panel synced to live-chart's x-axis. Reuses Story 8.2's
// /data/indicators/catalog and /data/coin/{id}/indicators verbatim (SSOT-03) -- no new
// Python endpoint for this.
var _activeIndicators=[];  // [{id,name,params}]
var _indicatorCatalog=null;  // {name: {params:{...defaults}, panel:'overlay'|'oscillator'}}
var _indSeq=0;
var _lastCandles=null;  // candles most recently rendered on live-chart (candles mode only)
var _syncingXRange=false;
function _fetchIndicatorCatalog(){
  fetch('/data/indicators/catalog').then(function(r){return r.json();}).then(function(d){
    _indicatorCatalog=d;
    _renderIndicatorPicker();
    _updateIndicatorPickerEnabled();
  }).catch(function(err){setStatus('Indicator catalog error: '+err);});
}
function _toggleIndicatorPicker(){
  var box=document.getElementById('ind-picker');
  if(box)box.style.display=box.style.display==='none'?'block':'none';
}
function _escAttr(s){
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}
function _paramInputHTML(id,key,val){
  var elId='ip_'+id+'_'+key;
  if(typeof val==='boolean')
    return '<label style="margin-right:8px"><input type="checkbox" id="'+elId+'" '+(val?'checked':'')
      +' onchange="_updateIndicatorParam('+id+',\\''+key+'\\',this.checked)"> '+key+'</label>';
  return '<label style="margin-right:8px">'+key+' <input type="'+(typeof val==='number'?'number':'text')+'" id="'+elId+'" value="'+_escAttr(val)
    +'" style="width:56px" onchange="_updateIndicatorParam('+id+',\\''+key+'\\',this.value)"></label>';
}
// Add-only list -- unlike a checkbox toggle, clicking an entry always adds a fresh
// instance so the same indicator can be added more than once (each gets its own id and
// default params, edited afterwards in the settings table).
// One row per indicator, grouped under a "Nautilus Indicators"/"Custom Indicators"
// header by the merged catalog's own `category` tag (dashboard.py's
// _merged_indicator_catalog -- neither catalog module knows about the other,
// DESIGN-02). Both headers always render, even when a group is currently empty, so
// the picker's shape doesn't visibly change the moment the first custom indicator
// (Story 10.2+) is added.
function _indicatorGroupHTML(label,names){
  var rows=names.map(function(name){
    var spec=_indicatorCatalog[name];
    return '<div style="padding:2px 0"><a href="#" onclick="_addIndicator(\\''+name+'\\');return false" style="color:#58a6ff;text-decoration:none">+ <b>'+name+'</b></a> <span style="color:#8b949e">('+spec.panel+')</span></div>';
  }).join('');
  return '<div style="font-weight:bold;color:#8b949e;margin-top:6px;font-size:11px">'+label+'</div>'+rows;
}
function _renderIndicatorPicker(){
  var box=document.getElementById('ind-picker-list');
  if(!box||!_indicatorCatalog)return;
  var search=document.getElementById('ind-picker-search');
  var q=search?search.value.trim().toLowerCase():'';
  var names=Object.keys(_indicatorCatalog).sort().filter(function(n){return !q||n.toLowerCase().indexOf(q)!==-1;});
  var groups={native:[],custom:[]};
  names.forEach(function(n){groups[_indicatorCatalog[n].category==='custom'?'custom':'native'].push(n);});
  box.innerHTML=_indicatorGroupHTML('Nautilus Indicators',groups.native)+_indicatorGroupHTML('Custom Indicators',groups.custom);
}
function _addIndicator(name){
  var spec=_indicatorCatalog&&_indicatorCatalog[name];
  if(!spec)return;
  var params={};
  for(var k in spec.params)params[k]=spec.params[k];
  _activeIndicators.push({id:++_indSeq,name:name,params:params});
  _renderIndicatorTable();
  _refreshActiveIndicators();
}
function _removeIndicator(id){
  _activeIndicators=_activeIndicators.filter(function(a){return a.id!==id;});
  _renderIndicatorTable();
  _refreshActiveIndicators();
}
function _indicatorLabel(a){
  var vals=Object.keys(a.params).map(function(k){return a.params[k];});
  return vals.length?a.name+'('+vals.join(',')+')':a.name;
}
function _renderIndicatorTable(){
  var box=document.getElementById('ind-table');
  if(!box)return;
  if(!_activeIndicators.length){box.innerHTML='';return;}
  var rows=_activeIndicators.map(function(a){
    var spec=_indicatorCatalog[a.name];
    var params=Object.keys(spec.params).map(function(k){return _paramInputHTML(a.id,k,a.params[k]);}).join(' ');
    return '<tr><td style="padding:3px 6px;border-bottom:1px solid #21262d;white-space:nowrap"><b>'+a.name+'</b> <span style="color:#8b949e">('+spec.panel+')</span></td>'
      +'<td style="padding:3px 6px;border-bottom:1px solid #21262d">'+params+'</td>'
      +'<td style="padding:3px 6px;border-bottom:1px solid #21262d"><button onclick="_removeIndicator('+a.id+')" style="background:#21262d;color:#f85149;border:1px solid #444;cursor:pointer;padding:1px 6px">✕</button></td></tr>';
  }).join('');
  box.innerHTML='<table style="width:100%;border-collapse:collapse;font-size:12px">'+rows+'</table>';
}
function _updateIndicatorParam(id,key,rawVal){
  var entry=_activeIndicators.filter(function(a){return a.id===id;})[0];
  if(!entry)return;
  var defaultVal=_indicatorCatalog[entry.name].params[key];
  entry.params[key]=typeof defaultVal==='boolean'?!!rawVal:typeof defaultVal==='number'?Number(rawVal):rawVal;
  _refreshActiveIndicators();
}
function _updateIndicatorPickerEnabled(){
  var box=document.getElementById('ind-picker'),note=document.getElementById('ind-picker-note');
  if(!box)return;
  var enabled=_coinMode==='candles';
  var inputs=box.getElementsByTagName('input');
  for(var i=0;i<inputs.length;i++)inputs[i].disabled=!enabled;
  var tbl=document.getElementById('ind-table');
  if(tbl){
    var tInputs=tbl.getElementsByTagName('input');
    for(var j=0;j<tInputs.length;j++)tInputs[j].disabled=!enabled;
  }
  if(note)note.style.display=enabled?'none':'block';
  if(!enabled)_clearIndicatorPanel();
}
function _buildIndicatorSpecString(){
  return _activeIndicators.map(function(a){
    var keys=Object.keys(a.params);
    return keys.length?a.name+':'+keys.map(function(k){return k+'='+a.params[k];}).join(','):a.name;
  }).join('|');
}
// Always fetches via the historical (explicit start/end) indicator endpoint, derived from
// _lastCandles' own actual window -- /chart/{id} has no live 1s poll (unlike /coin/{id}'s
// pollCoin: this page's init_script fetches its window once and only refetches on an
// explicit Load/pan/mode/bar change), so there's no separate "live" indicator case here.
function _fetchIndicatorSeriesForCurrentWindow(){
  var specStr=_buildIndicatorSpecString();
  if(!specStr||!_lastCandles||!_lastCandles.length)return Promise.resolve({});
  var startMs=_lastCandles[0].t,endMs=_lastCandles[_lastCandles.length-1].t+_coinBarSeconds*1000;
  var url='/data/coin/'+encodeURIComponent(_coinIid)+'/indicators?bar='+_coinBarSeconds
    +'&start='+encodeURIComponent(_fmtDTL(new Date(startMs)))
    +'&end='+encodeURIComponent(_fmtDTL(new Date(endMs)))
    +'&spec='+encodeURIComponent(specStr);
  return fetch(url).then(function(r){
    return r.json().then(function(body){
      if(!r.ok)throw new Error((body&&body.error)||('HTTP '+r.status));
      return body;
    });
  });
}
// Response keys are the backend's own _indicator_id(name, coerced_params). Two instances
// of the same indicator (different settings) can be active at once, so a bare name-prefix
// match is ambiguous -- instead parse each candidate key's own param string and compare
// typed values against this instance's params (Number()/lower-cased-string, not a
// straight string equals) so Python's str() formatting of a coerced value (e.g. "20.0"
// for a float) never has to match JS's own stringification byte-for-byte.
function _paramsMatch(paramStr,params){
  var keys=Object.keys(params);
  if(!paramStr)return keys.length===0;
  var pairs=paramStr.split(',');
  if(pairs.length!==keys.length)return false;
  return pairs.every(function(pair){
    var eq=pair.indexOf('='),k=pair.slice(0,eq),v=pair.slice(eq+1);
    if(!(k in params))return false;
    var want=params[k];
    if(typeof want==='number')return Number(v)===want;
    if(typeof want==='boolean')return v.toLowerCase()===String(want);
    return v===String(want);
  });
}
function _seriesForIndicator(data,a){
  var keys=Object.keys(a.params);
  if(!keys.length)return data[a.name]||null;
  var prefix=a.name+'_';
  for(var key in data){
    if(key.indexOf(prefix)===0&&_paramsMatch(key.slice(prefix.length),a.params))return data[key];
  }
  return null;
}
function _clearIndicatorPanel(){
  var panel=document.getElementById('ind-panel');
  if(panel){Plotly.purge(panel);panel.style.height='0px';}
}
var _indicatorFetchGen=0;
function _refreshActiveIndicators(){
  if(_lastCandles)_renderCandleTraces();  // paint base immediately; overlays follow async below
  if(_coinMode!=='candles'||!_activeIndicators.length||!_lastCandles||!_lastCandles.length){
    _clearIndicatorPanel();
    return;
  }
  var gen=++_indicatorFetchGen,candlesAtRequest=_lastCandles;
  _fetchIndicatorSeriesForCurrentWindow().then(function(data){
    if(gen!==_indicatorFetchGen||_coinMode!=='candles'||_lastCandles!==candlesAtRequest)return;  // superseded
    _renderCandleTraces(data);
    _renderOscillatorPanel(data);
  }).catch(function(err){setStatus('Indicator error: '+err);});
}
function _candleBaseTraces(candles){
  var x=candles.map(function(c){return new Date(c.t);});
  return [{
    type:'candlestick',x:x,
    open:candles.map(function(c){return c.o;}),
    high:candles.map(function(c){return c.h;}),
    low:candles.map(function(c){return c.l;}),
    close:candles.map(function(c){return c.c;}),
    name:'price',
    increasing:{line:{color:'#26a69a'}},
    decreasing:{line:{color:'#ef5350'}},
  }];
}
function _renderCandleTraces(indicatorData){
  var traces=_candleBaseTraces(_lastCandles);
  if(indicatorData){
    var x=_lastCandles.map(function(c){return new Date(c.t);});
    _activeIndicators.forEach(function(a){
      var spec=_indicatorCatalog&&_indicatorCatalog[a.name];
      if(!spec||spec.panel!=='overlay')return;
      var series=_seriesForIndicator(indicatorData,a);
      if(!series)return;
      Object.keys(series).forEach(function(attr){
        traces.push({type:'scattergl',mode:'lines',x:x,
          y:series[attr].map(function(p){return p.value;}),
          name:_indicatorLabel(a)+(Object.keys(series).length>1?'.'+attr:''),line:{width:1}});
      });
    });
  }
  Plotly.react('live-chart',traces,{height:300,template:'plotly_dark',dragmode:'pan',
    xaxis:{type:'date',rangeslider:{visible:false}},
    margin:{t:10,b:30,l:60,r:10},legend:{orientation:'h'}},{scrollZoom:true});
  _wireChartRelayout();
}
// Each active oscillator indicator instance beyond the first gets its own overlaid,
// hidden y-axis so it autoscales independently -- a price-scale indicator (e.g.
// LinearRegression) and a 0-1-scale one (e.g. RelativeStrengthIndex) sharing one
// auto-ranging axis otherwise flattens the smaller-range one to an invisible line near
// zero. The first instance keeps Plotly's default visible axis (readable ticks for the
// common single-indicator case); axes 2+ are overlaid/hidden -- Plotly addresses its
// first y-axis as plain 'y'/'yaxis', never 'y1'/'yaxis1', so axis 1 never goes through
// this helper. Real y-values are untouched (no normalization), so hover tooltips still
// show each indicator's true value.
function _oscillatorOverlayAxis(){
  return {visible:false,overlaying:'y'};
}
function _renderOscillatorPanel(data){
  var x=_lastCandles.map(function(c){return new Date(c.t);}),traces=[],axisN=0;
  // barmode:'overlay' is required, not cosmetic -- Plotly's default ('group') offsets
  // each bar trace sideways at every x tick to sit them side by side, which misplaces a
  // histogram indicator's bars away from their true timestamp on this shared date axis
  // the moment a second bar trace (a second histogram instance, or one with >1 output
  // attribute) is active.
  var layout={height:180,template:'plotly_dark',dragmode:'pan',barmode:'overlay',
    xaxis:{type:'date',rangeslider:{visible:false}},
    margin:{t:10,b:20,l:60,r:10},legend:{orientation:'h'}};
  _activeIndicators.forEach(function(a){
    var spec=_indicatorCatalog&&_indicatorCatalog[a.name];
    if(!spec||(spec.panel!=='oscillator'&&spec.panel!=='histogram'))return;
    var series=_seriesForIndicator(data,a);
    if(!series)return;
    axisN++;
    var axisKey=axisN===1?'y':'y'+axisN;
    if(axisN>1)layout['yaxis'+axisN]=_oscillatorOverlayAxis();
    var isHistogram=spec.panel==='histogram';
    Object.keys(series).forEach(function(attr){
      var trace={type:isHistogram?'bar':'scattergl',x:x,yaxis:axisKey,
        y:series[attr].map(function(p){return p.value;}),
        name:_indicatorLabel(a)+'.'+attr};
      if(isHistogram)trace.marker={opacity:0.7};else{trace.mode='lines';trace.line={width:1};}
      traces.push(trace);
    });
  });
  var panel=document.getElementById('ind-panel');
  if(!panel)return;
  if(!traces.length){_clearIndicatorPanel();return;}
  panel.style.height='180px';
  Plotly.react('ind-panel',traces,layout,{scrollZoom:true});
  var el=document.getElementById('ind-panel');
  if(el){
    el.removeAllListeners&&el.removeAllListeners('plotly_relayout');
    el.on('plotly_relayout',function(ev){var rng=_relayoutXRange(ev);if(rng)_syncChartXRange('ind-panel',rng);});
  }
}
// Guarded against reentrancy: a programmatic Plotly.relayout on the target panel would
// otherwise re-fire that panel's own plotly_relayout listener and ping-pong forever.
function _syncChartXRange(sourceId,rng){
  if(_syncingXRange||!rng)return;
  var targetId=sourceId==='live-chart'?'ind-panel':'live-chart';
  var targetEl=document.getElementById(targetId);
  if(!targetEl||!targetEl.data||!targetEl.data.length)return;
  _syncingXRange=true;
  try{Plotly.relayout(targetId,{'xaxis.range':rng});}finally{_syncingXRange=false;}
}
function _renderCandleChart(candles){
  if(!candles||!candles.length)return;
  _lastCandles=candles;
  _refreshActiveIndicators();
}
// Click-to-diff (bid/ask/mid/micro/price A/B comparison) -- Lines-mode-only, moved here
// from /coin/{id}'s old bespoke renderCoin implementation (Story 8.1) so it works
// wherever Lines mode is rendered (/chart/{id} now, previously /coin/{id} only).
var _diffA=null,_diffB=null,_diffBoxHTML='';
function clearDiff(){
  _diffA=null;_diffB=null;_diffBoxHTML='';
  var db=document.getElementById('diff-box');
  if(db)db.innerHTML='';
}
function _setDiffWaiting(snap){
  _diffA=snap;_diffB=null;
  _diffBoxHTML='<span style="color:#8b949e;font-size:11px">&#9679; A: '+new Date(snap.t).toLocaleTimeString()+' — click second point  <a onclick="clearDiff();return false" href="#" style="color:#8b949e">[cancel]</a></span>';
  var db=document.getElementById('diff-box');
  if(db)db.innerHTML=_diffBoxHTML;
}
function buildDiff(a,b){
  var metrics=[['bid','Bid'],['ask','Ask'],['mid','Mid'],['micro','Microprice'],['price','Eff Price']];
  var parts=metrics.map(function(m){
    var va=a[m[0]],vb=b[m[0]];
    if(va==null||vb==null||va===0)return'';
    var pct=(vb-va)/Math.abs(va)*100;
    var sign=pct>=0?'+':'';
    var col=pct>0?'#3fb950':pct<0?'#f85149':'#8b949e';
    return '<span style="margin-right:16px"><b>'+m[1]+'</b> <span style="color:'+col+'">'+sign+pct.toFixed(4)+'%</span></span>';
  }).filter(Boolean).join('');
  var tA=new Date(a.t).toLocaleTimeString(),tB=new Date(b.t).toLocaleTimeString();
  return '<span style="color:#8b949e;font-size:11px">'+tA+' → '+tB+'</span>  '+parts
    +'<a style="color:#8b949e;font-size:11px;margin-left:8px" onclick="clearDiff();return false" href="#">×</a>';
}
function handleChartClick(data){
  if(!data.points.length||!_chartState||_chartState.mode!=='lines')return;
  var pt=data.points[0];
  var idx=pt.pointIndex;
  if(idx==null||idx>=_chartState.rows.length)return;
  var snap=_chartState.rows[idx];
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
function _wireChartClick(){
  var liveEl=document.getElementById('live-chart');
  if(liveEl){
    liveEl.removeAllListeners&&liveEl.removeAllListeners('plotly_click');
    liveEl.on('plotly_click',handleChartClick);
  }
}
function _renderLineChart(rows){
  if(!rows||!rows.length)return;
  var x=rows.map(function(r){return new Date(r.t);});
  Plotly.react('live-chart',[
    {type:'scattergl',mode:'lines',x:x,y:rows.map(function(r){return r.bid;}),
      line:{color:'#26a69a',width:1},name:'bid'},
    {type:'scattergl',mode:'lines',x:x,y:rows.map(function(r){return r.ask;}),
      line:{color:'#ef5350',width:1},name:'ask'},
    {type:'scattergl',mode:'lines',x:x,y:rows.map(function(r){return r.mid;}),
      line:{color:'#aaa',width:1,dash:'dot'},name:'mid'},
    {type:'scattergl',mode:'lines',x:x,y:rows.map(function(r){return r.micro;}),
      line:{color:'#f0883e',width:1.5,dash:'dot'},name:'microprice'},
    {type:'scattergl',mode:'lines',x:x,y:rows.map(function(r){return r.price;}),
      line:{color:'#e3b341',width:1.5},name:'price'},
  ],{height:300,template:'plotly_dark',dragmode:'pan',
    xaxis:{type:'date',rangeslider:{visible:false}},
    margin:{t:10,b:30,l:60,r:10},legend:{orientation:'h'}},{scrollZoom:true});
  _wireChartRelayout();
  _wireChartClick();
}
function _renderTickChart(ticks){
  if(!ticks||!ticks.length)return;
  var x=ticks.map(function(t){return new Date(t.t);});
  var colors=ticks.map(function(t){return t.side==='BUYER'?'#26a69a':t.side==='SELLER'?'#ef5350':'#8b949e';});
  Plotly.react('live-chart',[{
    type:'scattergl',mode:'markers',x:x,y:ticks.map(function(t){return t.price;}),
    marker:{color:colors,size:4},name:'trades',
  }],{height:300,template:'plotly_dark',dragmode:'pan',
    xaxis:{type:'date',rangeslider:{visible:false}},
    margin:{t:10,b:30,l:60,r:10}},{scrollZoom:true});
  _wireChartRelayout();
}
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
  ["cvd","CVD($)"],["spread","Spread(bps)"],["microprice_lean","u lean(bps)"],
  ["volume_delta","Vol d($)"],
  ["price","Price"],["pct_1h","1h %"],["pct_24h","24h %"],["volatility","Vol"],
  ["volume24h","Vol24h"]
];
// Keys normalized client-side from raw token/price-unit deltas -- see usdFromTokens/
// bpsFromPriceUnits. The backend's own computation of these values is untouched --
// the one backend change this feature needed was exposing "price" on the
// /data/live/{id} endpoint (live_coin_json_handler), which already computed it but
// didn't send it. Never add "price" itself to either list below (self-referential).
var USD_KEYS=["cvd","volume_delta"];
var BPS_KEYS=["spread","microprice_lean"];
var currentSort=null;  // {key,dir} | null. null = server's default volume-sorted order.
// Indicators grouped by update cadence (matches bot_tui's Coin-detail grouping) --
// the box a value sits in tells you how often it can actually change without reading
// ranking_engine.py. "Volatility & Market" is a cadence grouping by convention, not
// strictly: pct_1h/pct_24h/volume24h aren't volatility, but they update on the same
// 60s-or-slower cadence as the volatility fields.
var IND_GROUPS=[
  ["Live (1s book state)",[
    ["microprice","Microprice"],["microprice_lean","u lean(bps)"],
    ["spread","Spread(bps)"],
    ["obi_10","OBI10"],["obi_5","OBI5"],["obi_3","OBI3"],
    ["price","Price"]
  ]],
  ["Order Flow (~5m rolling)",[
    ["ofi_10","OFI10"],["ofi_5","OFI5"],["ofi_3","OFI3"],["ofi_10_z","OFI10z"],
    ["cvd","CVD($)"],["volume_delta","Vol delta($)"],
    ["buy_count","Buy#"],["sell_count","Sell#"],["avg_trade_size","Avg size"]
  ]],
  ["Volatility & Market (60s-1h)",[
    ["volatility_fast","Vol (fast)"],["volatility","Vol (catalog)"],
    ["volatility_score","Vol score"],
    ["pct_1h","1h %"],["pct_24h","24h %"],["volume24h","Vol24h"]
  ]]
];
var timer=null;
var _coinIid=null;

function esc(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function setStatus(s){document.getElementById("status").innerHTML=s;}
function setApp(h){document.getElementById("app").innerHTML=h;}

function fmtP(v){if(v==null)return'—';return v>100?v.toFixed(2):v>1?v.toFixed(4):v.toFixed(6);}

// Raw values (token-unit deltas, price-unit deltas) stay raw in the backend --
// normalized here so they're comparable across instruments of wildly different
// price scale (a token-count delta or price-unit spread means very different
// things for a $100k coin vs. a sub-cent one). Guard against non-finite inputs
// (NaN/Infinity) explicitly -- `==null` alone doesn't catch NaN, and a JSON payload
// (esp. /data/live/{id}, which has no NaN-scrubbing today) could in principle carry one.
function usdFromTokens(raw,price){
  if(typeof raw!=='number'||!Number.isFinite(raw))return null;
  if(typeof price!=='number'||!Number.isFinite(price)||price<=0)return null;
  var v=raw*price;
  return Number.isFinite(v)?v:null;
}
function bpsFromPriceUnits(raw,price){
  if(typeof raw!=='number'||!Number.isFinite(raw))return null;
  if(typeof price!=='number'||!Number.isFinite(price)||price<=0)return null;
  var v=raw/price*10000;
  return Number.isFinite(v)?v:null;
}
// Rounds at the display precision *before* deciding the sign prefix, so a value that
// rounds to zero (e.g. -0.0000016) never renders the confusing "-0.00"/"-0" that
// plain toFixed()/toLocaleString() would otherwise produce for a tiny negative input.
function fmtSigned(v,decimals){
  if(v==null)return'—';
  var scale=Math.pow(10,decimals);
  var rounded=Math.round(v*scale)/scale;
  if(rounded===0)rounded=0;  // normalizes -0 to 0
  var text=decimals===0
    ? Math.abs(rounded).toLocaleString(undefined,{maximumFractionDigits:0})
    : Math.abs(rounded).toFixed(decimals);
  return (rounded<0?'-':rounded>0?'+':'')+text;
}
function fmtUsd(v){
  if(v==null)return'—';
  // Sub-$1 notional (common for micro-cap tokens) would otherwise round to a
  // meaningless "$0" at 0 decimal places -- show more precision below $1. An exact
  // zero is exempted so it renders as a plain "0", not a verbose "0.0000".
  return fmtSigned(v,(v!==0&&Math.abs(v)<1)?4:0);
}
function fmtBps(v){return fmtSigned(v,2);}
// Shared by renderRankings (rankings table) and renderCoin (coin-detail Indicators
// table) so the USD/BPS-key dispatch logic lives in exactly one place. Returns null
// for a key that isn't normalized (caller should fall back to its own passthrough
// text), or the formatted string ("—" included) for one that is.
function fmtNormalizedCell(key,raw,price){
  if(USD_KEYS.indexOf(key)>=0)return fmtUsd(usdFromTokens(raw,price));
  if(BPS_KEYS.indexOf(key)>=0)return fmtBps(bpsFromPriceUnits(raw,price));
  return null;
}

function showRankings(){
  clearInterval(timer);
  history.pushState({},"","/");
  pollRankings();
  timer=setInterval(pollRankings,2000);
}

var _lastRankings=null;  // {rows,ingestCount,ageS,stale,staleIds} from the last /api/rankings poll

function pollRankings(){
  fetch("/api/rankings")
    .then(function(r){return r.json();})
    .then(function(data){
      _lastRankings={rows:data.rows,ingestCount:data.ingest_count,ageS:data.age_s,stale:data.stale,staleIds:data.stale_instrument_ids||[]};
      renderRankings(sortRows(data.rows),data.ingest_count,data.age_s,data.stale,data.stale_instrument_ids||[]);
    })
    .catch(function(err){setStatus("Fetch error: "+err);});
}

function sortRows(rows){
  // null currentSort = keep the server's order (already volume-sorted descending).
  if(!currentSort)return rows;
  var key=currentSort.key,dir=currentSort.dir;
  return rows.slice().sort(function(a,b){
    var va=a.cells[key]?a.cells[key].raw:null,vb=b.cells[key]?b.cells[key].raw:null;
    if(va==null&&vb==null)return 0;
    if(va==null)return 1;
    if(vb==null)return -1;
    var cmp=va<vb?-1:va>vb?1:0;
    return dir==="asc"?cmp:-cmp;
  });
}

function setSort(key){
  if(currentSort&&currentSort.key===key){
    currentSort.dir=currentSort.dir==="desc"?"asc":"desc";
  }else{
    currentSort={key:key,dir:"desc"};
  }
  if(_lastRankings)
    renderRankings(sortRows(_lastRankings.rows),_lastRankings.ingestCount,_lastRankings.ageS,_lastRankings.stale,_lastRankings.staleIds);
}

function renderRankings(rows,ingestCount,ageS,stale,staleIds){
  var hdr="<tr><th>#</th><th>Instrument</th>"
    +COLS.map(function(c){
      var arrow=currentSort&&currentSort.key===c[0]?(currentSort.dir==="desc"?" &#9660;":" &#9650;"):"";
      return "<th style=\\"cursor:pointer\\" onclick=\\"setSort('"+c[0]+"');return false\\">"+esc(c[1])+arrow+"</th>";
    }).join("")
    +"<th>History</th></tr>";
  var tbody=rows.map(function(row,i){
    var iid=row.instrument_id;
    var label=iid.split("-").slice(0,2).join("-");
    var cells=COLS.map(function(c){
      var key=c[0];
      var v=row.cells[key];
      if(!v||!v.text||v.text==="\\u2014")return "<td>&mdash;</td>";
      var col=v.color?' style="color:'+esc(v.color)+'"':"";
      // "!" (row-level fetch error) and "ERR" (this column's own format_fn raised) are
      // both pre-formatted error markers from the server -- show them as-is, never
      // route them through normalization (their .raw is always null either way).
      if(v.text==="!"||v.text==="ERR")return "<td"+col+">"+esc(v.text)+"</td>";
      var norm=fmtNormalizedCell(key,v.raw,row.cells['price']?row.cells['price'].raw:null);
      return "<td"+col+">"+esc(norm!=null?norm:v.text)+"</td>";
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
  // Per-coin companion to the pane-level staleTxt above (OBS-01/OBS-02): an instrument
  // ranking_engine has silently dropped from rows[] for having gone stale on its own
  // (rows[] only ever contains currently-fresh instruments) -- surfaced here instead
  // of the coin just vanishing from the table with no trace (DATA-02).
  var staleIdsTxt=(staleIds&&staleIds.length)?" — <span style=\\"color:#f85149\\">stale feed: "+esc(staleIds.slice(0,3).join(", "))+(staleIds.length>3?" (+"+(staleIds.length-3)+" more)":"")+"</span>":"";
  setStatus("Updated "+new Date().toLocaleTimeString()+" — "+rows.length+" instruments — <span style=\\""+col+"\\">"+esc(staleTxt)+"</span>"+staleIdsTxt);
}

function showCoin(iid){
  clearInterval(timer);
  _coinIid=iid;
  history.pushState({iid:iid},"","/coin/"+encodeURIComponent(iid));
  var label=iid.split("-").slice(0,2).join("-");
  setApp(
    "<h1>"+esc(label)+" <small><a href=\\"/chart/"+esc(iid)+"\\">chart</a>"
    +" | <a onclick=\\"showRankings();return false\\" href=\\"/\\">back</a></small></h1>"
    +"<div id='ind-groups'></div>"
    +"<div id='price-ticker' style=\\"padding:6px 0 2px;font-size:14px;font-family:monospace;letter-spacing:0.04em;border-top:1px solid #21262d;margin-top:10px\\"></div>"
    +"<div id='sig-chart' style=\\"height:180px;margin-top:8px\\"></div>"
  );
  pollCoin(iid);
  timer=setInterval(function(){pollCoin(iid);},1000);
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

// /coin/{id}'s live indicator panel: ind-groups table, price-ticker, sig-chart (OFI10z/
// OBI10). The Candles/Lines/Ticks chart widget moved to /chart/{id} only (Story 8.1) --
// this function no longer touches chart mode, pagination, or click-to-diff state.
function renderCoin(iid,ind,chart){
  var groupsHTML=IND_GROUPS.map(function(g){
    var title=g[0],specs=g[1];
    var rows=specs.map(function(k){
      var key=k[0];
      var v=ind[key];
      var norm=fmtNormalizedCell(key,v,ind['price']);
      var cell=norm!=null?esc(norm):(v!=null?esc(String(v)):"&mdash;");
      return "<tr><td>"+esc(k[1])+"</td><td>"+cell+"</td></tr>";
    }).join("");
    return "<h2>"+esc(title)+"</h2><table>"+rows+"</table>";
  }).join("");
  var box=document.getElementById('ind-groups');
  if(box)box.innerHTML=groupsHTML;
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
    rows = metrics_store.history(symbol, METRICS_DB_PATH, days=31)

    if not rows:
        return _page(symbol, f"<h1>{html.escape(symbol)}</h1><p>No history yet.</p>")

    ts = [pd.Timestamp(r["ts"], unit="ns", tz="UTC") for r in rows]
    body = f"<h1>{html.escape(symbol)} — 31-day history</h1>"
    plotlyjs = "cdn"

    for field, label, *_ in [*RANKING_COLS, *_HISTORY_ONLY_COLS]:
        vals = [r.get(field) for r in rows]
        if all(v is None for v in vals):
            continue
        fig = go.Figure(go.Scatter(x=ts, y=vals, mode="lines", connectgaps=False))
        fig.update_layout(title=label, height=250, margin={"t": 40, "b": 20})
        body += fig.to_html(full_html=False, include_plotlyjs=plotlyjs)
        plotlyjs = False  # type: ignore[assignment]

    return _page(f"{symbol} history", body, refresh_seconds=30)


def _render_chart_page(symbol: str, start_ms: int, end_ms: int) -> str:
    """
    Per-event microstructure chart page.

    Price pane is the interactive Candles/Lines/Ticks drag-to-pan widget (_LIVE_CHART_JS)
    -- the sole home for this widget since Story 8.1 consolidated it here from /coin/{id}
    -- fed by /data/coin/{id}/candles|ticks|lines. The remaining OFI/imbalance/depth/
    cancel/spread panes stay server-rendered Plotly subplots from
    _chart_data.compute_chart_series for the same [start_ms, end_ms) window picked by
    the date-range form below. CVD moved to the indicator picker as a custom indicator
    (Story 10.2) -- OFI/cancel pressure are slated to follow (Stories 10.3/10.4).
    """
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
        rows=6, cols=1, shared_xaxes=True,
        row_heights=[0.16, 0.16, 0.16, 0.17, 0.17, 0.18],
        vertical_spacing=0.02,
        subplot_titles=[
            "OFI", "Book imbalance L1 agg (4-level)", "Mid-layer imbalance (L2-3)",
            "Depth (4-level)", "Cancel pressure", "Spread",
        ],
    )

    def _add(series_key: str, row: int, name: str, color: str) -> None:
        if data.get(series_key):
            fig.add_trace(go.Scattergl(
                x=ts(data[series_key]), y=vals(data[series_key]),
                mode="lines", name=name, line_color=color, line_width=1,
            ), row=row, col=1)

    # Row 1: OFI
    _add("ofi", 1, "OFI", "#f0883e")
    if data.get("ofi"):
        fig.add_hline(y=0, line_color="#555", line_width=1, row=1, col=1)

    # Row 2: 4-level aggregate imbalance
    _add("imbalance", 2, "imbalance", "#ab71ff")
    if data.get("imbalance"):
        fig.add_hline(y=0.5, line_color="#555", line_width=1, row=2, col=1)

    # Row 3: mid-layer imbalance (levels 2-3)
    _add("mid_imbalance", 3, "mid imbalance", "#c792ea")
    if data.get("mid_imbalance"):
        fig.add_hline(y=0.5, line_color="#555", line_width=1, row=3, col=1)

    # Row 4: depth
    _add("bid_depth", 4, "bid depth", "#26a69a")
    _add("ask_depth", 4, "ask depth", "#ef5350")

    # Row 5: cancel pressure
    _add("bid_cancel", 5, "bid cancel", "#26a69a")
    _add("ask_cancel", 5, "ask cancel", "#ef5350")

    # Row 6: spread
    _add("spread", 6, "spread", "#78909c")

    n = len(data.get("ofi", []))
    fig.update_layout(
        height=1250, template="plotly_dark",
        title=f"{symbol} — {n:,} delta events",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend={"orientation": "h", "y": 1.01},
    )
    # Datetime pickers above the chart — submits as GET params, also seeds the
    # interactive price widget's initial historical window (below).
    sym = html.escape(symbol)
    import datetime as _dt
    fmt = "%Y-%m-%dT%H:%M"
    start_val = _dt.datetime.fromtimestamp(start_ms / 1000).strftime(fmt)
    end_val   = _dt.datetime.fromtimestamp(end_ms   / 1000).strftime(fmt)
    form = (
        f"<form method='get' style='margin:8px 0'>"
        f"From <input type='datetime-local' id='coin-start' name='start' value='{start_val}'> &nbsp;"
        f"To <input type='datetime-local' id='coin-end' name='end' value='{end_val}'> &nbsp;"
        f"<button type='submit'>Load</button>"
        f"</form>"
    )
    widget = (
        '<div style="display:flex;gap:8px;align-items:center;padding:6px 0;flex-wrap:wrap">'
        "<button id=\"btn-lines\" onclick=\"setCoinMode('lines')\" style=\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px 10px;cursor:pointer\">Lines</button>"
        "<button id=\"btn-candles\" onclick=\"setCoinMode('candles')\" style=\"background:#21262d;color:#c9d1d9;border:1px solid #58a6ff;padding:3px 10px;cursor:pointer\">Candles</button>"
        "<button id=\"btn-ticks\" onclick=\"setCoinMode('ticks')\" style=\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px 10px;cursor:pointer\">Ticks</button>"
        "<select id='bar-sel' onchange='onBarChange()' style=\"background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px\">"
        "<option value='5'>5s</option><option value='15'>15s</option>"
        "<option value='30'>30s</option><option value='60' selected>1m</option><option value='300'>5m</option>"
        "<option value='900'>15m</option><option value='3600'>1h</option>"
        "<option value='14400'>4h</option><option value='86400'>1d</option><option value='604800'>1w</option></select>"
        '<button id="btn-indicators" onclick="_toggleIndicatorPicker()" style="background:#21262d;color:#c9d1d9;border:1px solid #444;padding:3px 10px;cursor:pointer">Indicators</button>'
        "</div>"
        "<div id='ind-picker' style='display:none;padding:6px 4px;margin-bottom:6px;border:1px solid #21262d;font-size:12px;max-height:220px;overflow-y:auto'>"
        "<div id='ind-picker-note' style='display:none;color:#f0883e;margin-bottom:4px'>Indicators require Candles mode</div>"
        "<input id='ind-picker-search' type='text' placeholder='Search indicators…' oninput='_renderIndicatorPicker()' "
        "style=\"width:100%;box-sizing:border-box;margin-bottom:4px;background:#0d1117;color:#c9d1d9;border:1px solid #444;padding:3px 6px\">"
        "<div id='ind-picker-list'></div>"
        "</div>"
        "<div id='status' style='color:#8b949e;font-size:11px;margin:4px 0'></div>"
        "<div id='live-chart' style='height:300px;margin-bottom:8px'></div>"
        "<div id='ind-table' style='margin-bottom:8px'></div>"
        "<div id='diff-box' style='min-height:22px;padding:5px 2px;border-top:1px solid #21262d;font-size:12px;font-family:monospace'></div>"
        "<div id='ind-panel' style='height:0px;overflow:hidden;margin-bottom:8px'></div>"
    )
    init_script = (
        "<script>"
        f"var _coinIid={json.dumps(symbol)};var _coinMode='candles';var _coinBarSeconds=60;"
        f"var _coinHistStart={json.dumps(start_val)};var _coinHistEnd={json.dumps(end_val)};"
        "var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;"
        "var _MAX_CHUNK_MS=30*24*3600*1000;"
        "function setStatus(s){var el=document.getElementById('status');if(el)el.innerHTML=s;}"
        "_updateModeButtons();_fetchHistCoin(_coinIid,_coinHistStart,_coinHistEnd);_fetchIndicatorCatalog();"
        "</script>"
    )
    body = (
        form + widget
        + fig.to_html(full_html=False, include_plotlyjs="cdn")
        + f"<script>{_LIVE_CHART_JS}</script>" + init_script
    )
    return _page(f"{sym} chart", body, refresh_seconds=86400)  # no auto-refresh; user controls via form


def _live_candles_json(iid: str, bar_seconds: int) -> str:
    """Build OHLC candles from the rolling 1s snapshot buffer (mid price)."""
    snaps = list(_second_rolling.get(iid, []))
    if not snaps:
        return json.dumps({"candles": []})
    buckets: dict[int, list[float]] = {}
    volumes: dict[int, float] = defaultdict(float)
    for s in snaps:
        if not s["bid_prices"] or not s["ask_prices"]:
            continue
        bp, ap = s["bid_prices"][0], s["ask_prices"][0]
        if bp >= ap:
            continue
        mid = (bp + ap) / 2
        bucket = (s["ts_event"] // 1_000_000_000 // bar_seconds) * bar_seconds
        buckets.setdefault(bucket, []).append(mid)
        volumes[bucket] += s["buy_volume"] + s["sell_volume"]
    # The oldest bucket is necessarily partial: snapshots older than it have already
    # aged out of the rolling deque (maxlen), so its member count keeps shrinking every
    # second as more of them evict, which changes its open/high/low live. That reads as
    # the candle "moving" even though it's not the currently-forming (rightmost) bar.
    # Drop it -- every other bucket has its full complement of seconds.
    if len(buckets) > 1:
        del buckets[min(buckets)]
    candles = [
        {"t": t * 1000, "o": mids[0], "h": max(mids), "l": min(mids), "c": mids[-1], "v": volumes[t]}
        for t, mids in sorted(buckets.items())
    ]
    return json.dumps({"candles": candles})


def _historical_candles_json(iid: str, start_ms: int, end_ms: int, bar_seconds: int) -> str:
    """Build OHLC candles from trade_ticks in the Parquet catalog."""
    from ml_signals.candles import build_candles as _build
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    catalog = ParquetDataCatalog(CATALOG_PATH)
    start_ns = start_ms * 1_000_000
    end_ns = end_ms * 1_000_000
    trades = catalog.trade_ticks(instrument_ids=[iid], start=start_ns, end=end_ns)
    raw = [(t.ts_event, t.price.as_double(), t.size.as_double()) for t in trades]
    if not raw:
        return json.dumps({"candles": []})
    candle_data = _build(raw, period_seconds=bar_seconds)
    candles = [
        {"t": c.ts_open // 1_000_000, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
        for c in candle_data
    ]
    return json.dumps({"candles": candles})


_MAX_TICK_WINDOW_NS = 6 * 3600 * 1_000_000_000  # 6h -- Ticks mode is a zoomed-in view only (AC #8)


def _historical_ticks_json(iid: str, start_ms: int, end_ms: int, max_rows: int = 20_000) -> str:
    """
    Return individual trade prints from the catalog -- the raw data candles are built from.

    Bounded two ways (MEM-01): the query window itself is clamped to _MAX_TICK_WINDOW_NS
    *before* hitting the catalog (never materialize an unbounded read just to slice it
    afterward), and max_rows caps the response as a second defensive backstop. Either
    clamp sets "truncated" so the client's pagination cursor never advances past data it
    didn't actually receive.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    catalog = ParquetDataCatalog(CATALOG_PATH)
    start_ns = start_ms * 1_000_000
    end_ns = end_ms * 1_000_000
    window_clamped = end_ns - start_ns > _MAX_TICK_WINDOW_NS
    if window_clamped:
        start_ns = end_ns - _MAX_TICK_WINDOW_NS
    trades = catalog.trade_ticks(instrument_ids=[iid], start=start_ns, end=end_ns)
    row_capped = len(trades) > max_rows
    ticks = [
        {
            "t": t.ts_event // 1_000_000,
            "price": t.price.as_double(),
            "size": t.size.as_double(),
            "side": t.aggressor_side.name,
        }
        for t in trades[:max_rows]
    ]
    return json.dumps({"ticks": ticks, "truncated": window_clamped or row_capped})


def _price_series_rows(snaps: list[dict]) -> list[dict]:
    """
    Build bid/ask/mid/micro/price rows from a snapshot list, in time order.

    price = CVD-weighted effective trade price: skews from mid toward ask on net buying,
    toward bid on net selling. Equals mid when no trades occurred in that second.
    Timestamps are milliseconds for Plotly. Shared by `_coin_chart_json` (live poll,
    parallel-array response for /coin/{id}'s indicator table/ticker/sig-chart) and
    `_live_lines_json`/`_historical_lines_json` (row-dict response for /chart/{id}'s
    Lines mode, Story 8.1) -- the math must never be reimplemented a second time (SSOT-03).
    """
    rows: list[dict] = []
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
            rows.append({"t": curr_ts_ms - 1, "bid": None, "ask": None, "mid": None,
                         "micro": None, "price": None})
        prev_ts_ms = curr_ts_ms
        mid = (bp + ap) / 2
        micro_value = calc_microprice(s)
        micro = micro_value if micro_value is not None else mid
        tv = s["buy_volume"] + s["sell_volume"]
        if tv > 0:
            price = mid + ((s["buy_volume"] - s["sell_volume"]) / tv) * (ap - bp) * 0.5
        else:
            price = mid
        rows.append({"t": curr_ts_ms, "bid": bp, "ask": ap, "mid": mid,
                     "micro": micro, "price": price})
    return rows


def _coin_chart_json(iid: str) -> str:
    """
    Return JSON with price series (bid/ask/mid/micro/price) and signal series (ofi_10_z/obi_10).

    sig_ts/ofi_10_z/obi_10 come from _ind_rolling (same 3600-point window). Price series
    is built by `_price_series_rows` (shared with /chart/{id}'s Lines mode, Story 8.1).
    """
    snaps = list(_second_rolling.get(iid, []))
    inds = list(_ind_rolling.get(iid, []))
    if not snaps:
        return json.dumps({"ts": [], "mid": [], "bid": [], "ask": [], "micro": [], "price": [],
                           "sig_ts": [], "ofi_10_z": [], "obi_10": []})
    rows = _price_series_rows(snaps)
    return json.dumps({
        "ts": [r["t"] for r in rows],
        "mid": [r["mid"] for r in rows],
        "bid": [r["bid"] for r in rows],
        "ask": [r["ask"] for r in rows],
        "micro": [r["micro"] for r in rows],
        "price": [r["price"] for r in rows],
        "sig_ts": [e["ts"] for e in inds],
        "ofi_10_z": [e["ofi_10_z"] for e in inds],
        "obi_10": [e["obi_10"] for e in inds],
    })


def _live_lines_json(iid: str) -> str:
    """Live bid/ask/mid/micro/price rows from the rolling 1s snapshot buffer."""
    rows = _price_series_rows(list(_second_rolling.get(iid, [])))
    return json.dumps({"rows": rows, "truncated": False})


def _historical_lines_json(iid: str, start_ms: int, end_ms: int) -> str:
    """Build bid/ask/mid/micro/price rows from DydxSecondSnapshot records in the catalog."""
    from dydx_collector.second_snapshot import DydxSecondSnapshot
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    catalog = ParquetDataCatalog(CATALOG_PATH)
    start_ns = start_ms * 1_000_000
    end_ns = end_ms * 1_000_000
    results = catalog.query(data_cls=DydxSecondSnapshot, identifiers=[iid], start=start_ns, end=end_ns)
    # query() wraps custom Data subclasses in CustomData -- unwrap via .data to reach the
    # actual DydxSecondSnapshot (confirmed via direct introspection this session).
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    snaps = [
        {
            "bid_prices": s.bid_prices, "bid_sizes": s.bid_sizes,
            "ask_prices": s.ask_prices, "ask_sizes": s.ask_sizes,
            "buy_volume": s.buy_volume, "sell_volume": s.sell_volume,
            "ts_event": s.ts_event,
        }
        for s in snapshots
    ]
    rows = _price_series_rows(snaps)
    return json.dumps({"rows": rows, "truncated": False})


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


def _cells_for_row(row: dict) -> dict[str, dict]:
    """Build the pre-formatted {key: {text, color, raw}} cell dict for one rankings row."""
    cells: dict[str, dict] = {}
    err = row.get("_err")
    for key, _, fmt_fn, color_fn in RANKING_COLS:
        v = row.get(key)
        if v is None:
            cells[key] = {"text": "!", "color": "#f85149", "raw": None} if err else {
                "text": "—", "color": None, "raw": None,
            }
        else:
            try:
                cells[key] = {
                    "text": fmt_fn(v),  # type: ignore[operator]
                    "color": color_fn(v) if color_fn else None,  # type: ignore[operator]
                    # NaN/Infinity are valid Python floats but not valid JSON tokens --
                    # json.dumps would emit them literally and break the client's
                    # JSON.parse for the whole payload. Null them out instead.
                    "raw": None if isinstance(v, float) and not math.isfinite(v) else v,
                }
            except Exception:
                cells[key] = {"text": "ERR", "color": "#f85149", "raw": None}
    return cells


def _rankings_json() -> str:
    """
    Return pre-formatted cell values for every currently-ranked instrument as JSON.

    Row order and every column value come entirely from ranking_engine's rankings:live
    message (AD-9, troll/CLAUDE.md SSOT-01/02) -- dashboard never computes or re-sorts
    anything locally anymore.
    """
    result = []
    if _LATEST_RANKING is not None:
        for rank_row in _LATEST_RANKING["ranks"]:
            # Defensive check: one malformed entry (missing key, wrong shape) in an
            # otherwise-valid ranks list must not crash the whole render -- skip just
            # that entry, keep rendering the rest (_handle_rankings_message already
            # validates the list itself, but not every entry inside it).
            if not isinstance(rank_row, dict):
                continue
            iid = rank_row.get("instrument_id")
            if iid is None:
                continue
            result.append({"instrument_id": iid, "cells": _cells_for_row(rank_row)})

    age_s = round(time.time() - _LAST_INGEST_TS, 1) if _LAST_INGEST_TS else None
    stale = age_s is None or age_s > 10
    ranking_age_s = (
        round(time.time() - _LATEST_RANKING_RECEIVED_AT, 1) if _LATEST_RANKING_RECEIVED_AT else None
    )
    ranking_stale = ranking_age_s is None or ranking_age_s > _RANKING_STALE_SECONDS
    stale_instrument_ids = (
        _LATEST_RANKING.get("stale_instrument_ids", []) if _LATEST_RANKING is not None else []
    )
    return json.dumps({
        "rows": result, "ingest_count": _INGEST_COUNT, "age_s": age_s, "stale": stale,
        "ranking_stale": ranking_stale, "ranking_age_s": ranking_age_s,
        "stale_instrument_ids": stale_instrument_ids,
    })


def _ingest_batch(batch: list[dict]) -> None:
    """
    Append each snapshot to the rolling window feeding candle/live-chart rendering.

    Every derived metric (OFI/OBI/microprice/spread/cvd/volume_delta/etc) now comes
    from ranking_engine's rankings:live message instead (SSOT-02, troll/CLAUDE.md) --
    this loop no longer computes any of them itself, only keeps the raw tick history
    _live_candles_json/_coin_chart_json need (there is no scalar-message equivalent for
    a full depth-array chart series).
    """
    global _INGEST_COUNT, _LAST_INGEST_TS
    for snap_dict in batch:
        _second_rolling[snap_dict["instrument_id"]].append(snap_dict)
    _INGEST_COUNT += len(batch)
    _LAST_INGEST_TS = time.time()


# --- aiohttp route handlers ---


async def rankings_handler(request: web.Request) -> web.Response:
    return web.Response(text=_INDEX_HTML, content_type="text/html")


async def rankings_json_handler(request: web.Request) -> web.Response:
    return web.Response(text=_rankings_json(), content_type="application/json")


async def watchlist_json_handler(request: web.Request) -> web.Response:
    """
    FR-7: the live, queryable Watchlist coin-set -- see ml_signals.watchlist.fetch_watchlist.

    Thin proxy over rankings:live's own ranked instrument-id list (Task 8) --
    ranking_engine already applies the freshness gate before including an instrument,
    so this avoids a second live Redis call/subscription just to reconstruct what
    rankings:live already tells us.
    """
    ranks = _LATEST_RANKING["ranks"] if _LATEST_RANKING else []
    # Defensive .get(): a malformed entry (missing instrument_id) is skipped rather
    # than crashing this handler -- same guard as _rankings_json's ranks[] iteration.
    instrument_ids = [
        iid for r in ranks if isinstance(r, dict) and (iid := r.get("instrument_id")) is not None
    ]
    return web.Response(
        text=json.dumps({"instrument_ids": instrument_ids}), content_type="application/json",
    )


async def rank_history_json_handler(request: web.Request) -> web.Response:
    """FR-8: historical rank/volume24h nearest a timestamp -- see ml_signals.rank_history."""
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    import datetime as _dt

    ts_v = qs.get("ts")
    if ts_v:
        try:
            dt = _dt.datetime.fromisoformat(ts_v)
            dt = dt.replace(tzinfo=dt.tzinfo or _dt.UTC)
            ts_ns = int(dt.timestamp() * 1_000_000_000)
        except (ValueError, OverflowError, OSError):
            ts_ns = time.time_ns()
    else:
        ts_ns = time.time_ns()

    row = await asyncio.to_thread(metrics_store.nearest, symbol, ts_ns, METRICS_DB_PATH)
    return web.Response(text=json.dumps(row or {}), content_type="application/json")


async def debug_handler(request: web.Request) -> web.Response:
    ranks = _LATEST_RANKING["ranks"] if _LATEST_RANKING is not None else []
    sample = ranks[0] if ranks else {}
    return web.Response(
        text=json.dumps({
            "ranked_count": len(ranks),
            "second_rolling_count": len(_second_rolling),
            "sample": sample,
        }),
        content_type="application/json",
    )


async def coin_handler(request: web.Request) -> web.Response:
    return web.Response(text=_INDEX_HTML, content_type="text/html")


async def coin_json_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    return web.Response(text=_coin_chart_json(symbol), content_type="application/json")


def _parse_query_ms(qs: dict[str, str], key: str) -> int | None:
    v = qs.get(key)
    if v:
        import datetime as _dt
        try:
            return int(_dt.datetime.fromisoformat(v).timestamp() * 1000)
        except ValueError:
            pass
    return None


async def coin_candles_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    bar_seconds = max(1, int(qs.get("bar", "60")))
    start_ms = _parse_query_ms(qs, "start")
    end_ms = _parse_query_ms(qs, "end")
    if start_ms is not None and end_ms is not None:
        data = await asyncio.to_thread(_historical_candles_json, symbol, start_ms, end_ms, bar_seconds)
    else:
        data = _live_candles_json(symbol, bar_seconds)
    return web.Response(text=data, content_type="application/json")


async def coin_lines_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    start_ms = _parse_query_ms(qs, "start")
    end_ms = _parse_query_ms(qs, "end")
    if start_ms is not None and end_ms is not None:
        data = await asyncio.to_thread(_historical_lines_json, symbol, start_ms, end_ms)
    else:
        data = _live_lines_json(symbol)
    return web.Response(text=data, content_type="application/json")


async def coin_ticks_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    start_ms = _parse_query_ms(qs, "start")
    end_ms = _parse_query_ms(qs, "end")
    if start_ms is None or end_ms is None:
        return web.Response(text=json.dumps({"ticks": [], "truncated": False}), content_type="application/json")
    data = await asyncio.to_thread(_historical_ticks_json, symbol, start_ms, end_ms)
    return web.Response(text=data, content_type="application/json")


def _parse_indicator_spec(raw: str) -> list[tuple[str, dict[str, str]]]:
    """
    Parse `Name:param=val,param2=val2|Name2:param=val` into [(name, {param: val}), ...].

    Entries are pipe-separated, not comma-separated -- a single indicator can have more
    than one param (e.g. Stochastics' period_k/period_d), so comma must stay reserved for
    separating an entry's own params.
    """
    specs = []
    for entry in raw.split("|"):
        entry = entry.strip()
        if not entry:
            continue
        name, _, param_str = entry.partition(":")
        params = dict(pair.split("=", 1) for pair in param_str.split(",") if pair)
        specs.append((name, params))
    return specs


def _coerce_indicator_params(
    spec: _chart_indicators.IndicatorSpec | _custom_indicators.CustomIndicatorSpec, raw: dict[str, str],
) -> dict[str, object]:
    """
    Cast query-string param values to match each param's default type (bool checked
    before int since bool is an int subclass in Python).
    """
    coerced: dict[str, object] = {}
    for key, val in raw.items():
        default = spec.params.get(key)
        if key not in spec.params:
            continue
        if isinstance(default, bool):
            coerced[key] = val.lower() in ("1", "true", "yes")
        elif isinstance(default, int):
            coerced[key] = int(val)
        elif isinstance(default, float):
            coerced[key] = float(val)
        else:
            coerced[key] = val
    return coerced


def _indicator_id(name: str, params: dict[str, object]) -> str:
    if not params:
        return name
    return name + "_" + ",".join(f"{k}={v}" for k, v in sorted(params.items()))


def _indicators_json(candles: list[dict], spec_str: str, window: _custom_indicators.ReplayWindow) -> tuple[str, int]:
    """Compute {t, value} point series for every requested indicator spec entry.

    spec_str is untrusted (a raw query-string value): a malformed entry (missing "=",
    a non-numeric value for a numeric param, an out-of-range param a specific
    indicator's own replay rejects) must degrade to a 400 with a message, the same
    contract the "Unknown indicator" case below already has -- not an uncaught 500 out
    of this handler. This is a system boundary (troll/CLAUDE.md: "only validate at
    system boundaries"), so the catch is intentionally broad -- there is no fixed set
    of exception types every current and future indicator's replay_indicator() might
    raise on bad input.

    A name is looked up in the native catalog first, then the custom one -- `window` is
    only ever passed to a custom replay (chart_indicators.replay_indicator's signature and
    call site stay exactly as Story 8.2 shipped them, since every native indicator is a
    pure function of the candle list alone).
    """
    result: dict[str, dict[str, list[dict]]] = {}
    try:
        for name, raw_params in _parse_indicator_spec(spec_str):
            if name in _chart_indicators.INDICATOR_CATALOG:
                spec = _chart_indicators.INDICATOR_CATALOG[name]
                params = _coerce_indicator_params(spec, raw_params)
                outputs = _chart_indicators.replay_indicator(candles, name, params)
            elif name in _custom_indicators.CUSTOM_INDICATOR_CATALOG:
                spec = _custom_indicators.CUSTOM_INDICATOR_CATALOG[name]
                params = _coerce_indicator_params(spec, raw_params)
                outputs = _custom_indicators.replay_indicator(candles, name, params, window)
            else:
                return json.dumps({"error": f"Unknown indicator: {name}"}), 400
            result[_indicator_id(name, params)] = {
                attr: [{"t": c["t"], "value": v} for c, v in zip(candles, values, strict=True)]
                for attr, values in outputs.items()
            }
    except Exception as exc:
        logger.info("Malformed indicator spec %r rejected: %s", spec_str, exc)
        return json.dumps({"error": f"Invalid indicator spec: {exc}"}), 400
    return json.dumps(result), 200


def _indicator_replay_window(
    symbol: str, bar_seconds: int, start_ms: int | None, end_ms: int | None,
) -> _custom_indicators.ReplayWindow:
    """Build the ReplayWindow for an indicator request, keeping start_ms/end_ms in lockstep
    with which candle path (live vs. historical) actually ran.

    A query string with only one of start/end parseable falls through to the live candle
    path (the `and` below) -- ReplayWindow's bounds must fall through with it (both None),
    or a future custom replay's own live-vs-historical check (`window.start_ms is None`)
    would see a half-set window and misread live candles as a bounded historical one.
    """
    is_historical = start_ms is not None and end_ms is not None
    return _custom_indicators.ReplayWindow(
        instrument_id=symbol, bar_seconds=bar_seconds,
        start_ms=start_ms if is_historical else None,
        end_ms=end_ms if is_historical else None,
    )


async def coin_indicators_handler(request: web.Request) -> web.Response:
    symbol = request.match_info["id"]
    qs = dict(request.rel_url.query)
    spec_str = qs.get("spec", "")
    if not spec_str:
        return web.Response(text=json.dumps({}), content_type="application/json")
    bar_seconds = max(1, int(qs.get("bar", "60")))
    start_ms = _parse_query_ms(qs, "start")
    end_ms = _parse_query_ms(qs, "end")
    window = _indicator_replay_window(symbol, bar_seconds, start_ms, end_ms)
    if window.start_ms is not None and window.end_ms is not None:
        candles_json = await asyncio.to_thread(_historical_candles_json, symbol, start_ms, end_ms, bar_seconds)
    else:
        candles_json = _live_candles_json(symbol, bar_seconds)
    candles = json.loads(candles_json)["candles"]
    # replay_indicator() is CPU-bound and runs once per active indicator instance --
    # off the event loop, or a request with several indicators active stalls every other
    # concurrent request this single-process aiohttp server is serving (other tabs' 1s
    # poll loops included), not just this one.
    body, status = await asyncio.to_thread(_indicators_json, candles, spec_str, window)
    return web.Response(text=body, content_type="application/json", status=status)


def _merged_indicator_catalog() -> dict[str, dict]:
    """Native + custom catalogs, each entry tagged with which one it came from.

    Tagging happens here, not inside either catalog module -- chart_indicators.py and
    custom_indicators.py stay unaware of each other (DESIGN-02); only this call site
    knows both exist. A name registered in both catalogs is a real bug (whichever one
    the picker lists would silently disagree with the native-first dispatch order in
    _indicators_json) -- raise immediately rather than let the two catalogs silently
    diverge (DATA-02: no mysteries).
    """
    collisions = set(_chart_indicators.INDICATOR_CATALOG) & set(_custom_indicators.CUSTOM_INDICATOR_CATALOG)
    if collisions:
        raise ValueError(f"Indicator name(s) registered in both catalogs: {sorted(collisions)}")
    merged: dict[str, dict] = {}
    for name, entry in _chart_indicators.catalog_json().items():
        merged[name] = {**entry, "category": "native"}
    for name, entry in _custom_indicators.catalog_json().items():
        merged[name] = {**entry, "category": "custom"}
    return merged


async def indicators_catalog_handler(request: web.Request) -> web.Response:
    return web.Response(text=json.dumps(_merged_indicator_catalog()), content_type="application/json")


async def live_coin_json_handler(request: web.Request) -> web.Response:
    """
    Raw indicator values for /coin/{id} live panel — polled every 1s.

    Sourced entirely from ranking_engine's rankings:live message (SSOT-02,
    troll/CLAUDE.md) -- dashboard computes none of this itself. Full field parity with
    bot_tui's Coin-detail (SSOT-05) -- every metric ranking_engine publishes for the
    open instrument, not just the fast-tick subset this endpoint used to expose.
    """
    symbol = request.match_info["id"]
    ranks = _LATEST_RANKING["ranks"] if _LATEST_RANKING is not None else []
    m = next(
        (r for r in ranks if isinstance(r, dict) and r.get("instrument_id") == symbol), {},
    )
    ind_keys = [
        "ofi_10", "ofi_5", "ofi_3", "ofi_10_z", "obi_10", "obi_5", "obi_3",
        "microprice", "microprice_lean", "spread", "cvd",
        "volume_delta", "buy_count", "sell_count", "avg_trade_size", "price",
        "volatility_fast", "volatility", "volatility_score", "pct_1h", "pct_24h", "volume24h",
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


def _handle_rankings_message(message: dict) -> None:
    """
    Record the latest rankings:live message and its receipt time (for staleness).

    Validates the message has a list-shaped "ranks" before storing it -- a malformed
    payload (unexpected shape, wrong producer, truncated JSON that still parses) must
    not corrupt _LATEST_RANKING. The previous valid ranking is kept instead, mirroring
    T-03-01's "one bad message never takes down the rest of the subscriber" discipline.

    Also feeds _ind_rolling (the /coin/{id} signal chart's rolling window) from each
    rank entry's own ofi_10_z/obi_10/microprice_lean fields -- these are computed by
    ranking_engine now (SSOT-02, troll/CLAUDE.md), not locally; this just re-shapes
    them into that chart's existing per-message window format.
    """
    global _LATEST_RANKING, _LATEST_RANKING_RECEIVED_AT
    if not isinstance(message.get("ranks"), list):
        logger.warning("rankings:live message missing list-shaped 'ranks', ignoring: %r", message)
        return
    _LATEST_RANKING = message
    _LATEST_RANKING_RECEIVED_AT = time.time()

    ts_ms = message["updated_at"] // 1_000_000
    for rank_row in message["ranks"]:
        if not isinstance(rank_row, dict):
            continue
        iid = rank_row.get("instrument_id")
        if iid is None:
            continue
        _ind_rolling[iid].append({
            "ts": ts_ms,
            "ofi_10_z": rank_row.get("ofi_10_z"),
            "obi_10": rank_row.get("obi_10"),
            "lean": rank_row.get("microprice_lean"),
        })


async def _redis_listener(redis_url: str) -> None:
    """
    Subscribe to snapshots:raw and rankings:live on one connection, branching on
    message["channel"] -- one subscriber, two channels, per AD-9 (dashboard never opens
    a second live Redis connection).

    Outer while True reconnects on any non-cancellation exception (ARCH-03).
    Malformed JSON is logged and skipped — subscriber always continues (T-03-01).
    """
    logger.info("Redis listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("Redis listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("snapshots:raw", "rankings:live")
                logger.info("Redis listener subscribed to snapshots:raw, rankings:live")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        if message["channel"] == "snapshots:raw":
                            _ingest_batch(payload)
                        elif message["channel"] == "rankings:live":
                            _handle_rankings_message(payload)
                    except Exception as exc:
                        logger.warning("Redis message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise  # propagate cancellation cleanly
        except Exception as exc:
            logger.warning("Redis subscriber error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)


def make_app(redis_url: str, catalog_path: str) -> web.Application:
    """
    Return a configured aiohttp Application with all routes and background tasks.

    No slow-loop/metrics_store-polling task here anymore -- ranking_engine folds
    price/pct_1h/pct_24h/volatility into rankings:live directly now (SSOT-02,
    troll/CLAUDE.md), so dashboard has nothing left to periodically re-poll for live
    values. metrics_store is still read directly (not cached) by
    rank_history_json_handler/_render_history_page for the historical-only views.
    """
    app = web.Application()
    app["redis_url"] = redis_url
    app["catalog_path"] = catalog_path

    app.cleanup_ctx.append(redis_subscriber_ctx)

    app.router.add_get("/", rankings_handler)
    app.router.add_get("/api/rankings", rankings_json_handler)
    app.router.add_get("/api/watchlist", watchlist_json_handler)
    app.router.add_get("/api/rank_history/{id}", rank_history_json_handler)
    app.router.add_get("/debug", debug_handler)
    app.router.add_get("/coin/{id}", coin_handler)
    app.router.add_get("/data/coin/{id}", coin_json_handler)
    app.router.add_get("/data/coin/{id}/candles", coin_candles_handler)
    app.router.add_get("/data/coin/{id}/ticks", coin_ticks_handler)
    app.router.add_get("/data/coin/{id}/lines", coin_lines_handler)
    app.router.add_get("/data/coin/{id}/indicators", coin_indicators_handler)
    app.router.add_get("/data/indicators/catalog", indicators_catalog_handler)
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
    # loopback-only: network_mode: host means this container shares the host's real
    # network stack, so 127.0.0.1 here is 127.0.0.1 on the host -- nothing remote
    # (LAN, Tailscale, public internet) can reach it, matching Dozzle's existing
    # 127.0.0.1-only port binding in docker-compose.yml.
    web.run_app(make_app(redis_url, catalog_path), host="127.0.0.1", port=port)
