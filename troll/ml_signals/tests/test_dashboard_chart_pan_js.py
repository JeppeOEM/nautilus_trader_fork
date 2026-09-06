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
global.Plotly = { react: function(){}, newPlot: function(){} };
global.fetch = function(){ return Promise.resolve({ json: function(){ return Promise.resolve({rows:[], candles:[], ticks:[]}); } }); };

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
