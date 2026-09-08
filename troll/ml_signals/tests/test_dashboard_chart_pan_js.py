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
Exercises the candle/tick/lines pan-to-load-more JS state machine (Story 7.1, extended by
Story 8.1) that lives inside dashboard.py's _LIVE_CHART_JS module -- the sole home for this
widget since Story 8.1 consolidated it onto /chart/{id} (previously also duplicated inline
in _INDEX_HTML for /coin/{id}, which has no chart at all anymore).

No JS test framework exists in this repo (Python is the only tested layer per project
convention) -- this extracts the real _LIVE_CHART_JS source, stubs document/Plotly/fetch
with plain objects plus the globals a real embedding page (_render_chart_page) would
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

function _dummyEl(){ return { innerHTML:"", style:{}, value:"", on:function(){}, removeAllListeners:function(){} }; }
global.document = { getElementById: function(){ return _dummyEl(); } };
global.window = {};
global.history = { pushState: function(){} };
global.location = { pathname: "/", search: "" };
global.Plotly = { react: function(){}, newPlot: function(){}, purge: function(){} };
global.fetch = function(){ return Promise.resolve({ ok:true, json: function(){ return Promise.resolve({rows:[], candles:[], ticks:[]}); } }); };

// Globals _LIVE_CHART_JS expects its embedding page to declare (matches _render_chart_page's
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
  _coinPanning=false; _coinHistStart=null; _chartState=null; _coinMode="ticks";
  _onChartRelayout({"xaxis.range[0]":"2024-01-01","xaxis.range[1]":"2024-01-02"});
  assert.strictEqual(_coinPanning, true, "first genuine relayout must set _coinPanning");
  assert.strictEqual(_coinHistStart, null, "_coinHistStart must not be repurposed as a panning sentinel");
  clearInterval(timer);

  // -- _loadOlderChunk: exhaustion on an empty response -----------------------------------
  _chartState = {iid:"A", mode:"candles", barSeconds:60, rows:[{t:1000,o:1,h:1,l:1,c:1}], cursorStart:1000, exhaustedLeft:false, loading:false};
  global.fetch = function(){ return Promise.resolve({ json: function(){ return Promise.resolve({candles:[]}); } }); };
  await _loadOlderChunk();
  assert.strictEqual(_chartState.exhaustedLeft, true, "empty response must mark exhaustedLeft");

  // -- _loadOlderChunk: truncated response advances the cursor only to what was returned -
  _chartState = {iid:"A", mode:"ticks", barSeconds:60, rows:[{t:100000,price:1,size:1,side:"BUYER"}], cursorStart:100000, exhaustedLeft:false, loading:false};
  global.fetch = function(){ return Promise.resolve({ json: function(){ return Promise.resolve({ticks:[{t:90000,price:1,size:1,side:"BUYER"}], truncated:true}); } }); };
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
  resolveFetch({ json: function(){ return Promise.resolve({candles:[{t:500,o:9,h:9,l:9,c:9}]}); } });
  await pending;
  assert.strictEqual(_chartState, newState, "stale in-flight fetch must not clobber a superseding chart state");
  assert.strictEqual(_chartState.rows.length, 1, "stale fetch result must not have been spliced into the new state");

  console.log("OK");
})().then(function(){ process.exit(0); }).catch(function(e){ console.error(e); process.exit(1); });
"""


def test_chart_pan_state_machine() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = _extract_inline_script()
    harness = _HARNESS_TEMPLATE.replace("__DASHBOARD_JS__", js)
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30, check=False,
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
        ["node", "-e", harness], capture_output=True, text=True, timeout=30, check=False,
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
        ["node", "-e", harness], capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASSERTIONS_OK" in result.stderr
