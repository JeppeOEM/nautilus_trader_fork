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
Exercises the candle/lines pan-to-load-more JS state machine (Story 7.1, extended by
Story 8.1) that lives inside dashboard.py's _LIVE_CHART_JS module -- the sole home for this
widget since Story 8.1 consolidated it onto /chart/{id} (previously also duplicated inline
in _INDEX_HTML for /coin/{id}, which has no chart at all anymore).

No JS test framework exists in this repo (Python is the only tested layer per project
convention) -- this extracts the real _LIVE_CHART_JS source, stubs document/Plotly/fetch
with plain objects plus the globals a real embedding page (_build_chart_page_html) would
declare, and runs a small Node harness (built-in `assert` only, no framework) against the
actual functions to cover the branching/race logic a review found untested:
request-generation guard (_loadOlderChunk vs. a superseding coin/mode/bar switch),
exhaustion, truncated-response cursor handling, the _coinPanning/_coinHistStart sentinel
fix, and the _chunkSpanMs cap for large bar sizes.
"""

import shutil
import subprocess

import pytest

import ml_signals.dashboard as dashboard


def _extract_inline_script() -> str:
    return dashboard._LIVE_CHART_JS


_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

var _els={};
function _dummyEl(id){
  if(!_els[id])_els[id]={innerHTML:"",style:{},value:"",on:function(){},removeAllListeners:function(){}};
  return _els[id];
}
global.document = { getElementById: function(id){ return _dummyEl(id); } };
global.window = {};
global.history = { pushState: function(){} };
global.location = { pathname: "/", search: "" };
var _relayoutCalls = [];
// react/newPlot stamp .data on the target div, purge clears it -- mirrors real Plotly.js
// closely enough for _syncChartXRange/_loadDefaultCandleWindow's "has this div actually
// been plotted yet" guards (el.data) to behave the same as they do in a real browser.
global.Plotly = {
  react: function(id,traces){ _dummyEl(id).data=traces; },
  newPlot: function(id,traces){ _dummyEl(id).data=traces; },
  purge: function(id){ _dummyEl(id).data=null; },
  relayout: function(id, upd){ _relayoutCalls.push({id: id, upd: upd}); }
};
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({rows:[], candles:[], ticks:[]}); } }); };

// Globals _LIVE_CHART_JS expects its embedding page to declare (matches _build_chart_page_html's
// init_script, dashboard.py) -- previously these lived in _INDEX_HTML's own script, removed
// in Story 8.1 since /coin/{id} no longer embeds this widget.
var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
function setStatus(s){}

__DASHBOARD_JS__

(async function(){
  // -- _chunkSpanMs: cap for large bar sizes (the new 1d/1w options) --------------------
  var span1w = _chunkSpanMs({mode:"candles", barSeconds:604800});
  assert.ok(span1w <= _MAX_CHUNK_MS, "1-week bar chunk span must be capped, got " + span1w);
  var span1m = _chunkSpanMs({mode:"candles", barSeconds:60});
  assert.ok(span1m >= 3600*1000, "1-minute bar chunk span must still have the 1h floor");

  // -- _onChartRelayout: _coinPanning sentinel fix ----------------------------------------
  _coinPanning=false; _coinHistStart=null; _chartState=null; _coinMode="lines";
  _onChartRelayout("live-chart", {"xaxis.range[0]":"2024-01-01","xaxis.range[1]":"2024-01-02"});
  assert.strictEqual(_coinPanning, true, "first genuine relayout must set _coinPanning");
  assert.strictEqual(_coinHistStart, null, "_coinHistStart must not be repurposed as a panning sentinel");
  clearInterval(timer);

  // -- _loadOlderChunk: exhaustion on an empty response -----------------------------------
  _chartState = {iid:"A", mode:"candles", barSeconds:60, rows:[{t:1000,o:1,h:1,l:1,c:1}], cursorStart:1000, exhaustedLeft:false, loading:false};
  global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({candles:[]}); } }); };
  await _loadOlderChunk();
  assert.strictEqual(_chartState.exhaustedLeft, true, "empty response must mark exhaustedLeft");

  // -- _loadOlderChunk: truncated response advances the cursor only to what was returned -
  _chartState = {iid:"A", mode:"lines", barSeconds:60, rows:[{t:100000,bid:1,ask:1,mid:1,micro:1,price:1}], cursorStart:100000, exhaustedLeft:false, loading:false};
  global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({rows:[{t:90000,bid:1,ask:1,mid:1,micro:1,price:1}], truncated:true}); } }); };
  await _loadOlderChunk();
  assert.strictEqual(_chartState.cursorStart, 90000, "a truncated response must not advance the cursor past data it didn't return");
  assert.strictEqual(_chartState.exhaustedLeft, false, "a truncated-but-nonempty response must not be treated as exhausted");

  // -- _loadOlderChunk: request-generation guard against a superseding chart switch -------
  _chartState = {iid:"A", mode:"candles", barSeconds:60, rows:[{t:1000,o:1,h:1,l:1,c:1}], cursorStart:1000, exhaustedLeft:false, loading:false};
  var staleState = _chartState;
  var resolveFetch;
  global.fetch = function(){ return new Promise(function(res){ resolveFetch = res; }); };
  var pending = _loadOlderChunk();
  var newState = {iid:"B", mode:"candles", barSeconds:60, rows:[{t:5000,o:2,h:2,l:2,c:2}], cursorStart:5000, exhaustedLeft:false, loading:false};
  _chartState = newState;  // simulate a coin switch while the older-chunk fetch is in flight
  resolveFetch({ ok:true, json: function(){ return Promise.resolve({candles:[{t:500,o:9,h:9,l:9,c:9}]}); } });
  await pending;
  assert.strictEqual(_chartState, newState, "stale in-flight fetch must not clobber a superseding chart state");
  assert.strictEqual(_chartState.rows.length, 1, "stale fetch result must not have been spliced into the new state");

  // -- _maybeLoadOlder: candles use a bar-count refill margin (_CANDLE_REFILL_MARGIN_BARS),
  // not the lines' proportional half-chunk margin -- must not fire early, must fire once
  // the pan reaches within that many bars of the buffered edge.
  var fetchCalls = 0;
  global.fetch = function(){ fetchCalls++; return new Promise(function(){}); };  // never resolves
  var barMs = 60 * 1000, marginMs = barMs * _CANDLE_REFILL_MARGIN_BARS;
  _chartState = {iid:"A", mode:"candles", barSeconds:60, rows:[{t:5000000,o:1,h:1,l:1,c:1}], cursorStart:5000000, exhaustedLeft:false, loading:false};
  _maybeLoadOlder([new Date(5000000 + marginMs + 1000).toISOString(), new Date(6000000).toISOString()]);
  assert.strictEqual(fetchCalls, 0, "must not refill while more than the bar-count margin remains");
  _maybeLoadOlder([new Date(5000000 + marginMs - 1000).toISOString(), new Date(6000000).toISOString()]);
  assert.strictEqual(fetchCalls, 1, "must refill once the pan reaches within the bar-count margin of the buffered edge");

  // -- _loadDefaultCandleWindow: bare-visit default is a catalog-backed window sized in
  // bars (_CANDLE_VISIBLE_BARS + _CANDLE_BUFFER_BARS back from now), zoomed to just the
  // visible tail -- this replaces the old resetCoinLive() default that showed near-zero
  // candles right after a dashboard restart (empty _second_rolling buffer).
  var fixedNowMs = 1700000000000;
  var realDateNow = Date.now;
  Date.now = function(){ return fixedNowMs; };
  _coinBarSeconds = 60; _coinMode = "candles"; _coinIid = "test-coin";
  _coinHistStart = null; _coinHistEnd = null;
  _relayoutCalls.length = 0;
  global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({candles:[{t:1,o:1,h:1,l:1,c:1}]}); } }); };
  await _loadDefaultCandleWindow();
  Date.now = realDateNow;
  assert.ok(_coinHistStart, "default load must set a fixed historical window, not stay live/null");
  var expectedStartMs = fixedNowMs - barMs * (_CANDLE_VISIBLE_BARS + _CANDLE_BUFFER_BARS);
  assert.ok(Math.abs(new Date(_coinHistStart).getTime() - expectedStartMs) < 60000,
    "default window must reach back _CANDLE_VISIBLE_BARS+_CANDLE_BUFFER_BARS bars, got start=" + _coinHistStart);
  assert.strictEqual(_relayoutCalls.length, 1, "must zoom the chart to the visible tail after the default load resolves");
  assert.strictEqual(_relayoutCalls[0].id, "live-chart");
  var visRange = _relayoutCalls[0].upd["xaxis.range"];
  var expectedVisStartMs = fixedNowMs - barMs * _CANDLE_VISIBLE_BARS;
  assert.ok(Math.abs(visRange[0].getTime() - expectedVisStartMs) < 1000, "visible range must start _CANDLE_VISIBLE_BARS bars back");
  assert.ok(Math.abs(visRange[1].getTime() - fixedNowMs) < 1000, "visible range must end at now");

  // -- _loadDefaultCandleWindow: an EMPTY window (no candles in range) must not call
  // Plotly.relayout at all -- _renderCandleChart never calls Plotly.react on 'live-chart'
  // when there's no data, so the div has no internal Plotly state yet; relaying it out
  // anyway throws inside plotly.js itself (real repro: "Cannot read properties of
  // undefined (reading '_guiEditing')"), an uncaught promise rejection that silently kills
  // the load -- the actual root cause of the reported "candlestick chart never appears".
  _relayoutCalls.length = 0;
  delete _els["live-chart"];  // simulate a div that has genuinely never been plotted
  global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({candles:[]}); } }); };
  Date.now = function(){ return fixedNowMs; };
  await _loadDefaultCandleWindow();
  Date.now = realDateNow;
  assert.strictEqual(_relayoutCalls.length, 0,
    "must not call Plotly.relayout on a live-chart div that was never plotted (empty window)");

  console.log("OK");
})().then(function(){ process.exit(0); }).catch(function(e){ console.error(e); process.exit(1); });
"""


def test_chart_pan_state_machine() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# -- Story 8.4: indicator picker's spec-string builder + response-series lookup -------------

_INDICATOR_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

function _dummyEl(){ return { innerHTML:"", style:{}, value:"", on:function(){}, removeAllListeners:function(){}, getElementsByTagName:function(){ return []; } }; }
global.document = { getElementById: function(){ return _dummyEl(); } };
global.Plotly = { react: function(){}, relayout: function(){}, purge: function(){} };
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({}); } }); };

var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
function setStatus(s){}

__DASHBOARD_JS__

// -- _buildIndicatorSpecString: pipe-separated entries, comma-separated params, matching
// dashboard.py's _parse_indicator_spec contract exactly (verified against the real Python
// parser by the test itself, not just against a hand-written expected string here).
_activeIndicators=[
  {name:"SimpleMovingAverage",params:{period:10}},
  {name:"BollingerBands",params:{period:20,k:2.5}},
  {name:"OnBalanceVolume",params:{}}
];
console.log(_buildIndicatorSpecString());

// -- _seriesForIndicator: matches the backend's own _indicator_id key by name/name_ prefix,
// never cross-matches a different active indicator, returns null (not a throw) when absent.
var data={
  "SimpleMovingAverage_period=10":{value:[1,2,3]},
  "OnBalanceVolume":{value:[4,5,6]}
};
var smaSeries=_seriesForIndicator(data,{name:"SimpleMovingAverage",params:{period:10}});
assert.deepStrictEqual(smaSeries,{value:[1,2,3]},"prefix match must find the SMA series");
var obvSeries=_seriesForIndicator(data,{name:"OnBalanceVolume",params:{}});
assert.deepStrictEqual(obvSeries,{value:[4,5,6]},"bare-name match must find the OBV series");
var missing=_seriesForIndicator(data,{name:"RelativeStrengthIndex",params:{}});
assert.strictEqual(missing,null,"an indicator with no matching response key must return null, not throw");

// -- _seriesForIndicator: two instances of the same indicator with different settings must
// each resolve to their own series, not both collapse onto whichever key comes first.
var multiData={
  "SimpleMovingAverage_period=10":{value:[1,2,3]},
  "SimpleMovingAverage_period=20":{value:[9,9,9]}
};
var fast=_seriesForIndicator(multiData,{name:"SimpleMovingAverage",params:{period:10}});
var slow=_seriesForIndicator(multiData,{name:"SimpleMovingAverage",params:{period:20}});
assert.deepStrictEqual(fast,{value:[1,2,3]},"period=10 instance must not cross-match period=20's series");
assert.deepStrictEqual(slow,{value:[9,9,9]},"period=20 instance must not cross-match period=10's series");
console.error("ASSERTIONS_OK");
"""


def test_indicator_spec_string_matches_python_parser() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _INDICATOR_HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASSERTIONS_OK" in result.stderr

    spec_str = result.stdout.strip()
    assert dashboard._parse_indicator_spec(spec_str) == [
        ("SimpleMovingAverage", {"period": "10"}),
        ("BollingerBands", {"period": "20", "k": "2.5"}),
        ("OnBalanceVolume", {}),
    ]


# -- Story 9.1: oscillator panel per-indicator axis scaling ---------------------------------

_OSCILLATOR_AXIS_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

function _dummyEl(){ return { innerHTML:"", style:{}, value:"", on:function(){}, removeAllListeners:function(){}, getElementsByTagName:function(){ return []; } }; }
global.document = { getElementById: function(){ return _dummyEl(); } };
var _captured=null;
global.Plotly = { react: function(id,traces,layout){ _captured={traces:traces,layout:layout}; }, relayout: function(){}, purge: function(){} };
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({}); } }); };

var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
function setStatus(s){}

__DASHBOARD_JS__

_indicatorCatalog={
  IndA:{params:{period:14},panel:"oscillator"},
  IndB:{params:{period:20},panel:"oscillator"},
  IndC:{params:{period:30},panel:"oscillator"}
};

// -- Single active oscillator indicator: must keep Plotly's default visible axis (the
// common case, and the one that never had the scaling bug -- one trace, nothing to
// squash against). No yaxis2 key should exist at all.
_activeIndicators=[{id:1,name:"IndA",params:{period:14}}];
_lastCandles=[{t:1000},{t:2000},{t:3000}];
_renderOscillatorPanel({"IndA_period=14":{value:[{t:1000,value:0.1},{t:2000,value:0.2},{t:3000,value:0.3}]}});
assert.strictEqual(_captured.traces[0].yaxis, "y", "the sole indicator stays on the default axis");
assert.strictEqual(_captured.layout.yaxis, undefined, "default axis must be left at Plotly's normal visible default, not hidden");
assert.strictEqual(_captured.layout.yaxis2, undefined, "no secondary axis when only one indicator is active");

// -- Three active oscillator indicators with wildly different native ranges (mirrors the
// real LinearRegression-vs-RelativeStrengthIndex disparity found this session) -- each
// beyond the first must land on its own overlaid, hidden axis, and real (unscaled) values
// must survive into the trace so Plotly's hover tooltip still shows the true value.
_activeIndicators=[
  {id:1,name:"IndA",params:{period:14}},
  {id:2,name:"IndB",params:{period:20}},
  {id:3,name:"IndC",params:{period:30}}
];
var data={
  "IndA_period=14":{value:[{t:1000,value:0.1},{t:2000,value:0.2},{t:3000,value:0.3}]},
  "IndB_period=20":{value:[{t:1000,value:60000},{t:2000,value:60010},{t:3000,value:60020}]},
  "IndC_period=30":{value:[{t:1000,value:-5},{t:2000,value:-6},{t:3000,value:-7}]}
};
_renderOscillatorPanel(data);
assert.ok(_captured, "Plotly.react must be called when active oscillator indicators have data");
assert.strictEqual(_captured.traces.length, 3, "one trace per active oscillator indicator");
assert.strictEqual(_captured.traces[0].yaxis, "y", "first indicator stays on the default axis");
assert.strictEqual(_captured.traces[1].yaxis, "y2", "second indicator must get its own overlaid axis");
assert.strictEqual(_captured.traces[2].yaxis, "y3", "third indicator must get its own overlaid axis, not reuse y2");
assert.strictEqual(_captured.layout.yaxis, undefined, "default axis stays at Plotly's normal visible default");
assert.deepStrictEqual(_captured.layout.yaxis2, {visible:false,overlaying:"y"}, "axis 2 must overlay axis 1, hidden");
assert.deepStrictEqual(_captured.layout.yaxis3, {visible:false,overlaying:"y"}, "axis 3 must overlay axis 1, hidden");
assert.deepStrictEqual(_captured.traces[1].y, [60000,60010,60020], "trace values must stay real/unscaled so hover shows the true value, not a normalized one");
console.error("ASSERTIONS_OK");
"""


def test_oscillator_panel_gives_each_active_indicator_its_own_axis() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _OSCILLATOR_AXIS_HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# -- Story 10.1: category-grouped picker + histogram panel rendering ------------------------

_PICKER_GROUPING_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

var _els={};
function _dummyEl(id){
  if(!_els[id])_els[id]={innerHTML:"",style:{},value:"",on:function(){},removeAllListeners:function(){},getElementsByTagName:function(){return [];}};
  return _els[id];
}
global.document = { getElementById: function(id){ return _dummyEl(id); } };
global.Plotly = { react: function(){}, relayout: function(){}, purge: function(){} };
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({}); } }); };

var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
function setStatus(s){}

__DASHBOARD_JS__

// One native, one custom entry -- the merged catalog's own "category" field (tagged by
// dashboard.py's _merged_indicator_catalog, not by either catalog module itself) is all
// the picker needs to group correctly.
_indicatorCatalog={
  SimpleMovingAverage:{params:{period:20},panel:"overlay",category:"native"},
  PlaceholderCustom:{params:{},panel:"histogram",category:"custom"}
};
_renderIndicatorPicker();
var html=_els["ind-picker-list"].innerHTML;
assert.ok(html.indexOf("Nautilus Indicators")>=0, "native group header must render");
assert.ok(html.indexOf("Custom Indicators")>=0, "custom group header must render");
assert.ok(html.indexOf("Nautilus Indicators")<html.indexOf("SimpleMovingAverage"), "native header must precede native entries");
assert.ok(html.indexOf("SimpleMovingAverage")<html.indexOf("Custom Indicators"), "native group must render before custom group");
assert.ok(html.indexOf("Custom Indicators")<html.indexOf("PlaceholderCustom"), "custom header must precede custom entries");

// Empty custom group must still render its header (AC: the picker's shape doesn't
// visibly change the moment the first custom indicator is added later).
_indicatorCatalog={SimpleMovingAverage:{params:{period:20},panel:"overlay",category:"native"}};
_renderIndicatorPicker();
var html2=_els["ind-picker-list"].innerHTML;
assert.ok(html2.indexOf("Custom Indicators")>=0, "custom group header must render even when the group is empty");
console.error("ASSERTIONS_OK");
"""


def test_indicator_picker_groups_by_category() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _PICKER_GROUPING_HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASSERTIONS_OK" in result.stderr


_HISTOGRAM_PANEL_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

function _dummyEl(){ return { innerHTML:"", style:{}, value:"", on:function(){}, removeAllListeners:function(){}, getElementsByTagName:function(){ return []; } }; }
global.document = { getElementById: function(){ return _dummyEl(); } };
var _captured=null;
global.Plotly = { react: function(id,traces,layout){ _captured={traces:traces,layout:layout}; }, relayout: function(){}, purge: function(){} };
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({}); } }); };

var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
function setStatus(s){}

__DASHBOARD_JS__

// A "histogram"-classified indicator must render as a Plotly bar trace, sharing the
// oscillator panel's per-instance axis-scaling machinery (Story 9.1) rather than a
// fourth new panel -- alongside an ordinary oscillator (line) indicator in the same call.
_indicatorCatalog={
  IndLine:{params:{period:14},panel:"oscillator",category:"native"},
  IndBar:{params:{},panel:"histogram",category:"custom"}
};
_activeIndicators=[
  {id:1,name:"IndLine",params:{period:14}},
  {id:2,name:"IndBar",params:{}}
];
_lastCandles=[{t:1000},{t:2000},{t:3000}];
var data={
  "IndLine_period=14":{value:[{t:1000,value:1},{t:2000,value:2},{t:3000,value:3}]},
  "IndBar":{value:[{t:1000,value:0.1},{t:2000,value:-0.2},{t:3000,value:0.05}]}
};
_renderOscillatorPanel(data);
assert.strictEqual(_captured.layout.barmode, "overlay", "shared panel must overlay bar traces, not Plotly's default group-offset");
assert.strictEqual(_captured.traces[0].type, "scattergl", "an ordinary oscillator indicator still renders as a line");
assert.strictEqual(_captured.traces[0].mode, "lines", "a line trace must still set mode");
assert.strictEqual(_captured.traces[1].type, "bar", "a histogram-classified indicator must render as a bar trace");
assert.ok(!("mode" in _captured.traces[1]), "a bar trace must not carry a leftover mode key, not even set to undefined");
assert.ok(!("line" in _captured.traces[1]), "a bar trace must not carry a leftover line-style key meant for scattergl traces");
assert.strictEqual(_captured.traces[1].yaxis, "y2", "the histogram trace still gets its own overlaid axis like any other instance");
console.error("ASSERTIONS_OK");
"""


def test_histogram_indicator_renders_as_bar_trace_in_oscillator_panel() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _HISTOGRAM_PANEL_HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASSERTIONS_OK" in result.stderr


# -- Story 14.2: client-fetched micro-panel + loading spinners ------------------------------

_MICRO_PANEL_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

var _els={};
function _dummyEl(id){
  if(!_els[id])_els[id]={innerHTML:"",style:{},value:"",on:function(){},removeAllListeners:function(){}};
  return _els[id];
}
global.document = { getElementById: function(id){ return _dummyEl(id); } };
var _captured=null;
global.Plotly = { react: function(id,traces,layout){ _captured={id:id,traces:traces,layout:layout}; }, relayout: function(){}, purge: function(){} };
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({}); } }); };

var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
function setStatus(s){}

__DASHBOARD_JS__

(async function(){

// -- _renderMicroPanel: row 1 (imbalance) present, row 2 (mid_imbalance) absent, row 3 has
// both bid_depth and ask_depth sharing one axis, row 4 (spread) present.
_renderMicroPanel({
  imbalance:[{time:1,value:0.6}],
  bid_depth:[{time:1,value:10}],
  ask_depth:[{time:1,value:8}],
  spread:[{time:1,value:0.02}],
});
assert.strictEqual(_captured.id, "micro-panel");
assert.strictEqual(_captured.traces.length, 4, "imbalance + bid_depth + ask_depth + spread, mid_imbalance skipped (no data)");
var byName={};
_captured.traces.forEach(function(t){byName[t.name]=t;});
assert.strictEqual(byName["imbalance"].xaxis, "x", "row 1 uses the bare (first) x-axis");
assert.strictEqual(byName["imbalance"].yaxis, "y", "row 1 uses the bare (first) y-axis");
assert.strictEqual(byName["bid depth"].xaxis, "x3", "row 3 (depth) uses the third x-axis");
assert.strictEqual(byName["ask depth"].xaxis, "x3", "bid/ask depth share the same row-3 axis");
assert.strictEqual(byName["spread"].xaxis, "x4", "row 4 uses the fourth x-axis");
// Only imbalance (row 1) has data, so only its 0.5 hline shape should exist -- the absent
// mid_imbalance row must not contribute a dangling hline against an empty row.
assert.strictEqual(_captured.layout.shapes.length, 1, "hline only for rows that actually have data");
assert.strictEqual(_captured.layout.shapes[0].yref, "y", "the one hline belongs to row 1");
assert.deepStrictEqual(_captured.layout.yaxis4.domain, [0, 0.2538], "row 4's y-domain mirrors the retired make_subplots row_heights/vertical_spacing layout");
assert.strictEqual(_els["micro-panel-spinner"].style.display, "none", "spinner must be hidden once real data has rendered");

// -- _renderMicroPanel: a fully empty series must not crash and must still hide the spinner
// (an instrument with no snapshots in the requested window is a normal, not exceptional, case).
_els["micro-panel-spinner"].style.display = "";
_renderMicroPanel({});
assert.strictEqual(_captured.traces.length, 0, "no series data means no traces");
assert.strictEqual(_els["micro-panel-spinner"].style.display, "none", "spinner must still hide even when the panel ends up empty");

// -- _loadMicroPanel: fetches the microfeatures endpoint and renders the result's "series".
global.fetch = function(url){
  assert.ok(url.indexOf("/data/coin/test-coin/microfeatures") === 0, "must hit the microfeatures endpoint for the given iid");
  return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({series:{spread:[{time:1,value:0.05}]},count:1}); } });
};
await _loadMicroPanel("test-coin", 0, 1000);
assert.strictEqual(_captured.traces.length, 1, "_loadMicroPanel must render whatever series the endpoint returns");
assert.strictEqual(_captured.traces[0].name, "spread");

// -- Every synced chart id from _SYNCED_CHART_IDS still includes micro-panel (Story 14.2
// only changed how it's populated, not its participation in cross-panel pan/zoom sync).
assert.ok(_SYNCED_CHART_IDS.indexOf("micro-panel") >= 0, "micro-panel must remain a synced chart id");

// -- _loadMicroPanel: a non-OK HTTP response must surface the real status/body via
// setStatus, not a cryptic "unexpected end of data" JSON.parse crash from r.json() on an
// error page (the actual bug this guarded against) -- and must hide the spinner, not leave
// it spinning forever over a panel that will never render (the "chart never appears" bug).
var _statusMsgs=[];
setStatus=function(s){_statusMsgs.push(s);};
_els["micro-panel-spinner"].style.display = "";
global.fetch = function(){
  return Promise.resolve({ ok:false, status:500, text: function(){ return Promise.resolve("boom"); } });
};
await _loadMicroPanel("test-coin", 0, 1000);
assert.ok(_statusMsgs.some(function(s){return s.indexOf("500") >= 0 && s.indexOf("boom") >= 0;}),
  "non-OK response must report HTTP status + body, not a JSON parse error: " + JSON.stringify(_statusMsgs));
assert.strictEqual(_els["micro-panel-spinner"].style.display, "none",
  "spinner must be hidden on load failure too, not just on success");

console.error("ASSERTIONS_OK");

})().then(function(){ process.exit(0); }).catch(function(e){ console.error(e); process.exit(1); });
"""


def test_micro_panel_renders_client_side_from_fetched_series_and_hides_spinner() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _MICRO_PANEL_HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASSERTIONS_OK" in result.stderr


_CANDLE_LOAD_FAILURE_HARNESS_TEMPLATE = r"""
"use strict";
const assert = require("assert");

var _els={};
function _dummyEl(id){
  if(!_els[id])_els[id]={innerHTML:"",style:{},value:"",on:function(){},removeAllListeners:function(){}};
  return _els[id];
}
global.document = { getElementById: function(id){ return _dummyEl(id); } };
global.window = {};
global.history = { pushState: function(){} };
global.location = { pathname: "/", search: "" };
global.Plotly = { react: function(){}, relayout: function(){}, purge: function(){}, newPlot: function(){} };
global.fetch = function(){
  return Promise.resolve({ ok:false, status:500, text: function(){ return Promise.resolve("catalog unavailable"); } });
};

var _coinIid="test-coin";var _coinMode="candles";var _coinBarSeconds=60;
var _coinHistStart=null,_coinHistEnd=null;
var _coinPanning=false,_chartState=null,_relayoutTimer=null,timer=null;
var _MAX_CHUNK_MS=30*24*3600*1000;
var _statusMsgs=[];
function setStatus(s){_statusMsgs.push(s);}

__DASHBOARD_JS__

// The HTML shell renders the spinner visible before any fetch resolves (see
// _build_chart_page_html) -- simulate that starting state.
_dummyEl("live-chart-spinner").style.display = "";

(async function(){
// -- _loadDefaultCandleWindow (the bare-page-visit path, _fetchHistCoin underneath): a
// failed candle fetch must hide the live-chart spinner and report the real HTTP
// status/body via setStatus -- not silently leave the spinner covering a permanently
// blank chart while the only trace of the failure is a JSON.parse crash nobody sees
// (the actual "candlestick chart never appears" bug this guards against).
await _loadDefaultCandleWindow();
assert.ok(_statusMsgs.some(function(s){return s.indexOf("500")>=0 && s.indexOf("catalog unavailable")>=0;}),
  "must report real HTTP status/body, not a cryptic JSON parse error: "+JSON.stringify(_statusMsgs));
assert.strictEqual(_els["live-chart-spinner"].style.display, "none",
  "spinner must be hidden on load failure -- otherwise it masks the chart forever");
console.error("ASSERTIONS_OK");
})().then(function(){ process.exit(0); }).catch(function(e){ console.error(e); process.exit(1); });
"""


def test_candlestick_load_failure_hides_spinner_and_reports_real_error() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _CANDLE_LOAD_FAILURE_HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASSERTIONS_OK" in result.stderr
