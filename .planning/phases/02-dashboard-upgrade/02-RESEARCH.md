# Phase 2: Dashboard Upgrade - Research

**Researched:** 2026-06-28
**Domain:** Python stdlib HTTP server, Plotly, in-process deque metrics, dashboard UX
**Confidence:** HIGH — all findings are from direct code inspection of the live codebase

---

## Summary

The existing dashboard is a plain `http.server.HTTPServer` + Plotly rendering pipeline. It already has most of the data infrastructure needed: `_second_rolling` deques are populated every 1s, `_metrics_from_rolling()` already computes OFI_3/5/10 and OBI_3/5/10, and `_fast_loop` drives updates every 1s in a background thread. The gap between what is computed and what is visible is mostly a wiring problem: the `_fast_loop` update path only propagates three fields (`ofi`, `microprice`, `spread`) to `_LIVE[iid]` for existing instruments, silently discarding the rest. Fixing that and expanding `RANKING_COLS` gives the upgraded table.

The live single-coin chart requires one new server route (`/data/coin/{id}` returning JSON) and ~20 lines of inline JavaScript in the `/coin/{id}` page that polls that endpoint every 1s and calls `Plotly.react()`. No WebSockets, no SSE, no new JS libraries — Plotly CDN is already loaded by the existing chart pages.

The current `/coin/{id}` page reads from Parquet (forbidden on the live path). The new `/coin/{id}` must be purely deque-based. The existing Parquet-backed coverage+footprint view can remain accessible via `/chart/{id}` (which already exists and is Parquet-based by design).

**Primary recommendation:** Fix the `_fast_loop` write path, extend `_metrics_from_rolling()` with CVD/volume/count fields, add a `/data/coin/{id}` JSON endpoint, replace the `/coin/{id}` handler with a live-only view, and add click navigation from the rankings table.

---

## Project Constraints (from CLAUDE.md / REQUIREMENTS.md)

- **FORK-01/02** — No modifications to `nautilus_trader/` or `crates/`. No `TradingNode`/`DataEngine`.
- **MEM-01** — No unbounded catalog reads. Live path must not call `catalog.trade_ticks()` or `catalog.order_book_deltas()` without time bounds.
- **MEM-02** — `_second_rolling` deque has `maxlen=300` (5 min). This cap must not be changed upward without deliberate intent.
- **DESIGN-01** — YAGNI. No new frameworks, no SSE/WebSocket, no React, no client-side routing.
- **DESIGN-03** — Prefer deletion over addition when simplification is possible. The existing `/coin/{id}` page can be replaced entirely.
- **READ-01** — Functions under ~30 lines. The new chart endpoint and updated metrics function must respect this.
- **READ-03** — Type hints on all function signatures.
- **TEST-01** — Tests required for any new financial calculations (CVD computation, microprice lean, avg trade size).
- **TEST-02** — Tests NOT required for trivial glue (new HTTP routes, HTML generation).
- **SIGNAL-01** — Do not store derivable values; compute on read. CVD, microprice lean, avg trade size are computed from stored fields.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Live metrics computation (OFI, OBI, CVD…) | Background thread (`_fast_loop`) | Request handler (chart JSON) | Indicator replay is cheap; background thread pre-computes for the table; chart endpoint does per-request arithmetic only |
| Rankings table rendering | HTTP request handler | Background thread (pre-computes `_LIVE`) | Handler reads from `_LIVE` dict; no blocking I/O on request path |
| Single-coin chart data | HTTP request handler (`/data/coin/{id}`) | `_second_rolling` deque | Handler snapshots deque at request time; no replay needed |
| Single-coin chart rendering | Browser (Plotly JS + `setInterval`) | Server (initial HTML page) | Server delivers full initial page; browser polls JSON and calls `Plotly.react()` |
| Historical Parquet exploration | HTTP request handler (`/chart/{id}`) | — | Parquet reads are acceptable here; this is an explicitly historical, user-triggered path |

---

## Existing Dashboard Architecture (VERIFIED: code inspection)

### Pages and Routes

| Route | Current Behavior | Parquet Read? | Source |
|-------|-----------------|---------------|--------|
| `GET /` | Rankings table from `_LIVE` dict; `<meta refresh=5>` | No (live path uses `_LIVE`) | `_render_rankings_page()` |
| `GET /coin/{id}` | Coverage stats + footprint chart + OFI/microprice panel | **Yes** — `catalog.trade_ticks()` + `catalog.order_book_deltas()` | `_render_coin_page()` |
| `GET /chart/{id}` | Full microstructure chart with date pickers | **Yes** — `compute_chart_series()` | `_render_chart_page()` |
| `GET /history/{id}` | 31-day metric history charts from SQLite | No (SQLite) | `_render_history_page()` |
| `GET /live` | Live strategy signals (from `record()`) | No | `_render_live_page()` |

### Background Thread Architecture

Three threads start from `serve_in_background()`:

| Thread | Function | Interval | What it does |
|--------|----------|----------|--------------|
| HTTP server | `server.serve_forever()` | — | Handles requests synchronously |
| Fast loop | `_fast_loop(catalog_path, rolling)` | 1s (rolling mode) or 5s (standalone) | Calls `_metrics_from_rolling()` or `metrics_computer.compute_book_metrics_all()` |
| Slow loop | `_slow_loop(catalog_path)` | 60s | Full snapshot with price stats; writes to SQLite |

### The `_fast_loop` Write Bug [VERIFIED: code inspection]

`_metrics_from_rolling()` returns dicts with keys: `ts, instrument_id, ofi, ofi_3, ofi_5, ofi_10, obi_3, obi_5, obi_10, microprice, spread`.

But `_fast_loop` only propagates a subset when updating an existing `_LIVE` entry:

```python
# Current (dashboard.py lines 644-648) — discards ofi_3/5/10 and obi_*
if iid in _LIVE:
    _LIVE[iid].update({
        "ts":         m["ts"],
        "ofi":        m.get("ofi"),
        "microprice": m.get("microprice"),
        "spread":     m.get("spread"),
    })
else:
    _LIVE[iid] = m  # first time: all fields included
```

Fix: replace the partial dict with a full merge of all fields returned by `_metrics_from_rolling()`.

### Current `RANKING_COLS` [VERIFIED: code inspection]

```python
RANKING_COLS = [
    ("price",      "Price",      ...),
    ("ofi",        "OFI",        ...),
    ("microprice", "Microprice", ...),
    ("spread",     "Spread",     ...),
    ("pct_1h",     "1h %",       ...),
    ("pct_24h",    "24h %",      ...),
    ("volatility", "Volatility", ...),
]
```

Missing from the new spec: `ofi_3`, `ofi_5`, `ofi_10`, `obi_3`, `obi_5`, `obi_10`, `cvd`, `volume_delta`, `buy_count`, `sell_count`.

### Current Table Navigation [VERIFIED: code inspection]

```python
# dashboard.py line 203-204
f"<td><a href='/chart/{iid}'>{label}</a> <small><a href='/coin/{iid}'>cov</a></small></td>"
```

The instrument label links to `/chart/{iid}` (microstructure chart). The `/coin/{id}` link is a secondary "cov" (coverage) link. The new spec wants clicking a row to navigate to `/coin/{id}` as the primary destination.

---

## Data Available in `DydxSecondSnapshot` [VERIFIED: code inspection]

Each snapshot in `_second_rolling[iid]` has:

| Field | Type | Notes |
|-------|------|-------|
| `bid_prices` | `list[float]` | Top-20 bids, index 0 = best bid |
| `bid_sizes` | `list[float]` | Matching sizes |
| `ask_prices` | `list[float]` | Top-20 asks, index 0 = best ask |
| `ask_sizes` | `list[float]` | Matching sizes |
| `buy_volume` | `float` | Accumulated buy (aggressor) volume since last 1s tick |
| `sell_volume` | `float` | Accumulated sell volume since last 1s tick |
| `buy_count` | `int` | Number of buy trades since last 1s tick |
| `sell_count` | `int` | Number of sell trades since last 1s tick |
| `ts_event` | `int` | Nanosecond timestamp |

All new derived metrics can be computed from this without additional data sources.

---

## New Metrics: Computation Formulas [VERIFIED: code inspection of snapshot schema]

| Metric | Formula | Source Fields |
|--------|---------|---------------|
| `ofi_3` | `MultiLevelOFI(levels=3)` replayed over snaps | bid/ask prices+sizes |
| `ofi_5` | `MultiLevelOFI(levels=5)` replayed over snaps | bid/ask prices+sizes |
| `ofi_10` | `MultiLevelOFI(levels=10)` replayed over snaps | bid/ask prices+sizes |
| `obi_3` | `MultiLevelOBI(levels=3).update_raw(bid_sizes, ask_sizes)` on latest snap | bid/ask sizes |
| `obi_5` | `MultiLevelOBI(levels=5).update_raw(bid_sizes, ask_sizes)` | bid/ask sizes |
| `obi_10` | `MultiLevelOBI(levels=10).update_raw(bid_sizes, ask_sizes)` | bid/ask sizes |
| `cvd` | `sum(s.buy_volume - s.sell_volume for s in snaps)` | buy/sell volume |
| `volume_delta` | `latest.buy_volume - latest.sell_volume` | buy/sell volume |
| `buy_count` | `latest.buy_count` | buy_count |
| `sell_count` | `latest.sell_count` | sell_count |
| `avg_trade_size` | `(total_buy_vol + total_sell_vol) / (total_buy_count + total_sell_count)` if count > 0 | buy/sell volume + counts |
| `microprice` | `(bp * as_ + ap * bs) / (bs + as_)` from level 0 | bid/ask prices+sizes |
| `microprice_lean` | `microprice - (bid_prices[0] + ask_prices[0]) / 2` | derived |
| `spread` | `ask_prices[0] - bid_prices[0]` | bid/ask prices |
| `mid` | `(ask_prices[0] + bid_prices[0]) / 2` | bid/ask prices |

All computations are pure Python arithmetic — no Nautilus type construction involved, no precision concerns (working with raw floats that were already stored as floats in the snapshot).

---

## Chart Approach: JS `setInterval` + JSON Endpoint + `Plotly.react()` [VERIFIED: code inspection]

### Why Not Full Page `<meta refresh>`

The existing rankings table uses `<meta http-equiv="refresh" content="5">`. At 1s resolution this would:
- Re-render the full Plotly chart HTML on every request (slow, flickers)
- Transfer the full Plotly figure JSON (~100KB for 300 points × 4 traces) on every reload
- Cause visible chart re-initialization flicker every second

### Why Not Plotly `animate()`

Plotly's `animate()` API requires pre-defined frames registered at figure creation time. It is designed for exploratory animation of pre-computed datasets, not live streaming. It does not support appending new points to a live feed.

### Recommended Pattern: `setInterval` + `/data/coin/{id}` + `Plotly.react()`

The Plotly JS library is already loaded from CDN on pages that include charts (`include_plotlyjs="cdn"`). The single-coin page includes a chart, so Plotly JS is already present.

Server side — new JSON endpoint (no Parquet read):
```python
def _coin_chart_json(iid: str, rolling: dict) -> dict:
    snaps = list(rolling.get(iid, []))  # snapshot deque atomically
    ts, mid_vals, bid_vals, ask_vals, micro_vals = [], [], [], [], []
    for s in snaps:
        if not s.bid_prices or not s.ask_prices:
            continue
        bp, ap = s.bid_prices[0], s.ask_prices[0]
        bs, as_ = s.bid_sizes[0], s.ask_sizes[0]
        total = bs + as_
        ts.append(s.ts_event // 1_000_000)  # ms for Plotly datetime axis
        mid_vals.append((bp + ap) / 2)
        bid_vals.append(bp)
        ask_vals.append(ap)
        micro_vals.append((bp * as_ + ap * bs) / total if total > 0 else (bp + ap) / 2)
    return {"ts": ts, "mid": mid_vals, "bid": bid_vals, "ask": ask_vals, "micro": micro_vals}
```

Client side — embedded in the `/coin/{id}` HTML page:
```javascript
(function() {
  var iid = "{iid_escaped}";
  var layout = {
    height: 350, template: "plotly_dark",
    xaxis: {type: "date"}, yaxis: {title: "Price"},
    legend: {orientation: "h"},
    margin: {t: 30, b: 30}
  };

  function update(data) {
    var traces = [
      {x: data.ts.map(function(t){return new Date(t);}), y: data.mid,   name: "mid",        mode: "lines", line: {color: "#aaa", width: 1}},
      {x: data.ts.map(function(t){return new Date(t);}), y: data.bid,   name: "bid",        mode: "lines", line: {color: "#26a69a", width: 1}},
      {x: data.ts.map(function(t){return new Date(t);}), y: data.ask,   name: "ask",        mode: "lines", line: {color: "#ef5350", width: 1}},
      {x: data.ts.map(function(t){return new Date(t);}), y: data.micro, name: "microprice", mode: "lines", line: {color: "#f0883e", width: 1.5, dash: "dot"}}
    ];
    Plotly.react("live-chart", traces, layout);
  }

  function poll() {
    fetch("/data/coin/" + encodeURIComponent(iid))
      .then(function(r){return r.json();})
      .then(update)
      .catch(function(){});  // silent on network error
  }

  poll();
  setInterval(poll, 1000);
})();
```

`Plotly.react()` is a full redraw using the new data arrays. With 300 points × 4 traces this is fast (< 10ms). No point-appending bookkeeping needed. The deque already provides a rolling 5-minute window.

---

## Architecture Patterns

### System Architecture Diagram

```
Collector asyncio loop
  └── _second_loop() [1s]
        └── DydxSecondSnapshot → _second_rolling[iid] (deque, maxlen=300)
                                      │
              ┌───────────────────────┤
              │                       │
  Dashboard background threads        │
    ├── _fast_loop [1s]               │
    │     └── _metrics_from_rolling() ◄── reads deque
    │           └── _LIVE[iid] (dict, all metric fields)
    │                   │
    │                   ▼
    │     HTTP GET /              ← reads _LIVE (no I/O)
    │     HTTP GET /data/coin/{id} ← reads _second_rolling (no I/O)
    │
    └── _slow_loop [60s]
          └── metrics_computer.compute_all()  ← Parquet reads (historical)
                └── SQLite metrics.db (for /history/{id})

  Browser
    └── /coin/{id} HTML + JS
          └── setInterval(1000) → fetch /data/coin/{id} → Plotly.react()
```

### Recommended Project Structure (changed files only)

```
troll/ml_signals/
├── dashboard.py          # Main changes here — see below
└── test_dashboard_metrics.py  # NEW — tests for new financial calculations
```

No new files needed beyond the test file. All changes are in `dashboard.py`.

### Changes to `dashboard.py`

**1. Fix `_fast_loop` write path** — propagate all fields from `_metrics_from_rolling()`:
```python
# Replace the partial update with a full merge
with _METRICS_LOCK:
    for m in book_metrics:
        _LIVE[m["instrument_id"]] = {**_LIVE.get(m["instrument_id"], {}), **m}
```

**2. Extend `_metrics_from_rolling()`** — add CVD, volume delta, buy/sell count, microprice lean:
```python
total_buy_vol = sum(s.buy_volume for s in snaps)
total_sell_vol = sum(s.sell_volume for s in snaps)
total_buy_count = sum(s.buy_count for s in snaps)
total_sell_count = sum(s.sell_count for s in snaps)
total_count = total_buy_count + total_sell_count
result.append({
    ...existing fields...,
    "cvd":           total_buy_vol - total_sell_vol,
    "volume_delta":  latest.buy_volume - latest.sell_volume,
    "buy_count":     latest.buy_count,
    "sell_count":    latest.sell_count,
    "avg_trade_size": (total_buy_vol + total_sell_vol) / total_count if total_count > 0 else None,
    "microprice_lean": (microprice - (latest.bid_prices[0] + latest.ask_prices[0]) / 2)
                       if microprice is not None and latest.bid_prices and latest.ask_prices else None,
})
```

**3. Expand `RANKING_COLS`**:
```python
RANKING_COLS = [
    ("ofi_10",        "OFI₁₀",     lambda v: f"{v:+.1f}",  _green_red),
    ("ofi_5",         "OFI₅",      lambda v: f"{v:+.1f}",  _green_red),
    ("ofi_3",         "OFI₃",      lambda v: f"{v:+.1f}",  _green_red),
    ("obi_10",        "OBI₁₀",     lambda v: f"{v:.3f}",   _obi_color),
    ("obi_5",         "OBI₅",      lambda v: f"{v:.3f}",   _obi_color),
    ("obi_3",         "OBI₃",      lambda v: f"{v:.3f}",   _obi_color),
    ("cvd",           "CVD",       lambda v: f"{v:+.2f}",  _green_red),
    ("spread",        "Spread",    lambda v: f"{v:.6f}",   None),
    ("microprice_lean","μ lean",   lambda v: f"{v:+.6f}",  _green_red),
    ("volume_delta",  "Vol Δ",     lambda v: f"{v:+.2f}",  _green_red),
    ("buy_count",     "Buy#",      lambda v: f"{int(v)}",  None),
    ("sell_count",    "Sell#",     lambda v: f"{int(v)}",  None),
]
```

**4. New `/data/coin/{id}` route** — JSON endpoint for chart polling:
- Route: `elif path.startswith("/data/coin/"):`
- Response: `Content-Type: application/json`
- Body: `json.dumps(_coin_chart_json(iid, rolling))`
- Guard: if rolling is None (standalone mode), return `{"ts":[],"mid":[],"bid":[],"ask":[],"micro":[]}`

**5. Replace `/coin/{id}` handler** — new live view:
- Render indicator panel from `_LIVE[iid]` (all metric fields)
- Render initial Plotly chart (via `_coin_chart_json()` → `go.Figure`) as static fallback
- Embed JS `setInterval` polling block (no `<meta refresh>` on this page)
- No Parquet reads

**6. Update rankings table row links** — make the instrument label link to `/coin/{id}`:
```python
f"<td><a href='/coin/{iid}'>{label}</a> <small><a href='/chart/{iid}'>chart</a></small></td>"
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Live chart data streaming | WebSocket/SSE server | `setInterval` + JSON endpoint | 1s poll is simpler; deque has only 300 points; no persistent connection needed |
| Chart rendering | D3.js / custom canvas | `Plotly.react()` | Plotly already loaded on page; handles axes, hover, legend automatically |
| Multi-coin aggregated table | Custom data pipeline | `_LIVE` dict + background thread | Already exists and works |
| Sliding-window indicators | Custom ring buffer | `deque(maxlen=300)` + indicator replay | Already in place; replay over 300 snaps is fast |

---

## Indicator Computation: Per-Request Replay is Acceptable [VERIFIED: code inspection]

`_compute_multilevel(snaps, levels)` creates fresh `MultiLevelOFI` and `MultiLevelOBI` and replays all snaps:
- 300 snaps × 6 multilevel calls (OFI_3/5/10, OBI_3/5/10) per coin
- OBI: single iteration over `bid_sizes[:levels]` and `ask_sizes[:levels]` per snap — O(levels × snaps) = 10 × 300 = 3000 additions
- OFI: inner loop over `min(levels, len(bid_prices), prev_len)` per snap — similar
- `_fast_loop` already does this for all ~50 subscribed coins every 1s in a background thread — runtime is under 100ms total [ASSUMED based on code structure; no profiling data]

The chart JSON endpoint (`/data/coin/{id}`) does NOT replay indicators — it only serializes raw deque fields with per-snapshot arithmetic. No multilevel replay on the request handler thread.

**Conclusion:** No caching of computed indicator values needed. Per-second background computation is the right model.

---

## Thread Safety of `_second_rolling` Reads [VERIFIED: code inspection]

`_second_rolling` is a `defaultdict(lambda: deque(maxlen=300))` written by the asyncio `_second_loop` task and read by the HTTP server threads. CPython's GIL makes individual deque operations atomic. The pattern `snaps = list(dq)` (snapshot the deque at one point in time) is already used in `_metrics_from_rolling()` and is safe. The chart JSON endpoint uses the same pattern.

No additional locking is needed for deque reads. The `_METRICS_LOCK` protects `_LIVE` writes/reads (dict mutation is not atomic).

---

## Common Pitfalls

### Pitfall 1: Partial `_LIVE` Update Silently Drops Multilevel Metrics
**What goes wrong:** `_fast_loop` updates `_LIVE[iid]` with only 3 fields; rankings table shows `None` for ofi_3/obi_3/etc. even though `_metrics_from_rolling()` computes them correctly.
**Root cause:** The `if iid in _LIVE: _LIVE[iid].update({...})` call (dashboard.py line ~644) explicitly lists only ofi/microprice/spread.
**How to avoid:** Replace with `_LIVE[iid] = {**_LIVE.get(iid, {}), **m}` to merge all fields.
**Warning sign:** ofi_3/obi columns show `—` (None) in the table despite collector running.

### Pitfall 2: Plotting Timestamps as Nanoseconds
**What goes wrong:** `ts_event` is nanoseconds since epoch. Passing raw nanoseconds to Plotly's datetime axis produces dates in the year ~2262 or crashes.
**How to avoid:** Always divide by `1_000_000` to get milliseconds before serializing to JSON; in JS, wrap with `new Date(ms)` for the x-axis.
**Warning sign:** Chart x-axis shows year 2262 or NaN.

### Pitfall 3: `include_plotlyjs` Budget on Multi-Chart Pages
**What goes wrong:** If the new `/coin/{id}` page generates multiple `fig.to_html()` calls, each defaults to `include_plotlyjs="cdn"`, resulting in multiple CDN `<script>` tags.
**How to avoid:** Pass `include_plotlyjs="cdn"` only on the first chart; `include_plotlyjs=False` on all subsequent. For the live chart page, the JS `setInterval` approach bypasses this: the div is empty on load; the chart is created by `Plotly.newPlot()` on first poll, not by `to_html()`.
**Warning sign:** Page source contains multiple `<script src="https://cdn.plot.ly/...">` tags.

### Pitfall 4: Replacing `/coin/{id}` Breaks the Coverage Link in the Table
**What goes wrong:** The current table row renders `<a href='/coin/{iid}'>cov</a>`. If `/coin/{id}` becomes the live view, the "cov" label is misleading.
**How to avoid:** Change the secondary link to `<a href='/chart/{iid}'>chart</a>` (the existing Parquet-backed microstructure view).

### Pitfall 5: `_second_rolling` is None in Standalone Mode
**What goes wrong:** Dashboard can be run standalone (`python -m ml_signals.dashboard`). In that mode, `rolling` is `None`. The `/data/coin/{id}` endpoint and new `/coin/{id}` page must guard against `rolling is None`.
**How to avoid:** At the top of `_coin_chart_json()`: `if rolling is None: return {"ts": [], ...}`. Render the indicator panel with `—` for all values.

---

## Code Examples

### `_metrics_from_rolling()` — Full Extended Version
```python
# Source: dashboard.py (to be rewritten)
def _metrics_from_rolling(rolling: dict) -> list[dict]:
    """Compute live metrics from in-process 1s rolling snapshots — no Parquet read."""
    now_ns = time.time_ns()
    result = []
    for iid, dq in list(rolling.items()):
        if not dq:
            continue
        snaps = list(dq)
        latest = snaps[-1]

        ofi_3, obi_3 = _compute_multilevel(snaps, levels=3)
        ofi_5, obi_5 = _compute_multilevel(snaps, levels=5)
        ofi_10, obi_10 = _compute_multilevel(snaps, levels=10)

        microprice: float | None = None
        if latest.bid_prices and latest.ask_prices:
            bp, ap = latest.bid_prices[0], latest.ask_prices[0]
            bs, as_ = latest.bid_sizes[0], latest.ask_sizes[0]
            total = bs + as_
            if total > 0:
                microprice = (bp * as_ + ap * bs) / total

        mid: float | None = None
        if latest.bid_prices and latest.ask_prices:
            mid = (latest.bid_prices[0] + latest.ask_prices[0]) / 2

        total_buy_vol = sum(s.buy_volume for s in snaps)
        total_sell_vol = sum(s.sell_volume for s in snaps)
        total_buy_count = sum(s.buy_count for s in snaps)
        total_sell_count = sum(s.sell_count for s in snaps)
        total_count = total_buy_count + total_sell_count

        result.append({
            "ts":              now_ns,
            "instrument_id":   iid,
            "ofi":             ofi_10,
            "ofi_3":           ofi_3,
            "ofi_5":           ofi_5,
            "ofi_10":          ofi_10,
            "obi_3":           obi_3,
            "obi_5":           obi_5,
            "obi_10":          obi_10,
            "microprice":      microprice,
            "microprice_lean": (microprice - mid) if microprice is not None and mid is not None else None,
            "spread":          (latest.ask_prices[0] - latest.bid_prices[0])
                               if latest.ask_prices and latest.bid_prices else None,
            "cvd":             total_buy_vol - total_sell_vol,
            "volume_delta":    latest.buy_volume - latest.sell_volume,
            "buy_count":       latest.buy_count,
            "sell_count":      latest.sell_count,
            "avg_trade_size":  (total_buy_vol + total_sell_vol) / total_count if total_count > 0 else None,
        })
    return result
```

### `_coin_chart_json()` — New JSON Endpoint Helper
```python
import json

def _coin_chart_json(iid: str, rolling: dict | None) -> str:
    if rolling is None:
        return json.dumps({"ts": [], "mid": [], "bid": [], "ask": [], "micro": []})
    snaps = list(rolling.get(iid, []))
    ts, mid_vals, bid_vals, ask_vals, micro_vals = [], [], [], [], []
    for s in snaps:
        if not s.bid_prices or not s.ask_prices:
            continue
        bp, ap = s.bid_prices[0], s.ask_prices[0]
        bs, as_ = s.bid_sizes[0], s.ask_sizes[0]
        total = bs + as_
        ts.append(s.ts_event // 1_000_000)
        mid_vals.append((bp + ap) / 2)
        bid_vals.append(bp)
        ask_vals.append(ap)
        micro_vals.append((bp * as_ + ap * bs) / total if total > 0 else (bp + ap) / 2)
    return json.dumps({"ts": ts, "mid": mid_vals, "bid": bid_vals, "ask": ask_vals, "micro": micro_vals})
```

### New `/coin/{id}` Live Indicator Panel
```python
def _render_live_coin_page(symbol: str, rolling: dict | None) -> str:
    iid = html.escape(symbol)
    with _METRICS_LOCK:
        m = _LIVE.get(symbol, {})

    def _row(label: str, key: str, fmt: str = ".4f") -> str:
        v = m.get(key)
        val = f"{v:{fmt}}" if v is not None else "—"
        return f"<tr><td>{label}</td><td>{val}</td></tr>"

    panel = (
        "<h2>Indicators (live)</h2>"
        "<table>"
        + _row("OFI₁₀", "ofi_10", "+.2f")
        + _row("OFI₅",  "ofi_5",  "+.2f")
        + _row("OFI₃",  "ofi_3",  "+.2f")
        + _row("OBI₁₀", "obi_10", ".4f")
        + _row("OBI₅",  "obi_5",  ".4f")
        + _row("OBI₃",  "obi_3",  ".4f")
        + _row("Microprice", "microprice", ".6f")
        + _row("μ lean",     "microprice_lean", "+.6f")
        + _row("Spread",     "spread", ".6f")
        + _row("CVD (5min)", "cvd",    "+.4f")
        + _row("Vol Δ (1s)", "volume_delta", "+.4f")
        + _row("Buy#",  "buy_count",  ".0f")
        + _row("Sell#", "sell_count", ".0f")
        + _row("Avg trade size", "avg_trade_size", ".4f")
        + "</table>"
    )

    js_block = f"""
<div id="live-chart" style="height:350px"></div>
<script>
(function(){{
  var iid="{iid}";
  var layout={{height:350,template:"plotly_dark",xaxis:{{type:"date"}},
               margin:{{t:30,b:30}},legend:{{orientation:"h"}}}};
  function update(d){{
    var x=d.ts.map(function(t){{return new Date(t);}});
    Plotly.react("live-chart",[
      {{x:x,y:d.mid,  name:"mid",        mode:"lines",line:{{color:"#aaa",    width:1}}}},
      {{x:x,y:d.bid,  name:"bid",        mode:"lines",line:{{color:"#26a69a",width:1}}}},
      {{x:x,y:d.ask,  name:"ask",        mode:"lines",line:{{color:"#ef5350",width:1}}}},
      {{x:x,y:d.micro,name:"microprice", mode:"lines",line:{{color:"#f0883e",width:1.5,dash:"dot"}}}}
    ],layout);
  }}
  function poll(){{fetch("/data/coin/"+encodeURIComponent(iid))
    .then(function(r){{return r.json();}}).then(update).catch(function(){{}});}}
  poll(); setInterval(poll,1000);
}})();
</script>"""

    body = (
        f"<h1>{iid} &nbsp;<small><a href='/chart/{iid}'>historical chart</a></small></h1>"
        + panel
        + "<h2>Price (5-min rolling)</h2>"
        + js_block
    )
    # No <meta refresh> — JS handles live updates
    return _page(symbol, body, refresh_seconds=86400)
```

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.4.4 |
| Config file | `pyproject.toml` (repo root) |
| Quick run command | `pytest troll/ml_signals/test_dashboard_metrics.py -x` |
| Full suite command | `pytest troll/ -x` |

### Phase Requirements → Test Map

| Req | Behavior | Test Type | Automated Command | File Exists? |
|-----|----------|-----------|-------------------|-------------|
| DASH-01 | `_metrics_from_rolling()` computes CVD correctly | unit | `pytest troll/ml_signals/test_dashboard_metrics.py::test_cvd -x` | No — Wave 0 |
| DASH-01 | `_metrics_from_rolling()` computes microprice_lean correctly | unit | `pytest troll/ml_signals/test_dashboard_metrics.py::test_microprice_lean -x` | No — Wave 0 |
| DASH-01 | `_metrics_from_rolling()` computes avg_trade_size zero-division safety | unit | `pytest troll/ml_signals/test_dashboard_metrics.py::test_avg_trade_size_no_trades -x` | No — Wave 0 |
| DASH-01 | `_metrics_from_rolling()` returns all required keys | unit | `pytest troll/ml_signals/test_dashboard_metrics.py::test_metrics_keys -x` | No — Wave 0 |
| DASH-03 | `_coin_chart_json()` returns empty arrays when rolling is None | unit | `pytest troll/ml_signals/test_dashboard_metrics.py::test_chart_json_no_rolling -x` | No — Wave 0 |
| DASH-03 | `_coin_chart_json()` converts ts_event ns to ms correctly | unit | `pytest troll/ml_signals/test_dashboard_metrics.py::test_chart_json_ts_conversion -x` | No — Wave 0 |
| DASH-02,04 | Clicking instrument in rankings table links to `/coin/{id}` | manual | Open browser, verify | — |
| DASH-04 | Live chart updates every 1s | manual | Watch chart for 10s | — |

### Wave 0 Gaps
- [ ] `troll/ml_signals/test_dashboard_metrics.py` — covers DASH-01, DASH-03 (new financial calculations and timestamp conversion)

Existing test infrastructure in `troll/ml_signals/test_indicators.py` already covers `MultiLevelOFI` and `MultiLevelOBI` — no new tests needed for those.

---

## Environment Availability

Step 2.6: All changes are Python in-process — no external tools, databases, or services beyond what the collector already runs. `plotly` is already installed (used by existing dashboard). `json` is stdlib. No new dependencies.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `plotly` | Chart rendering | Already installed | — | — |
| `json` (stdlib) | `/data/coin/{id}` endpoint | Always | — | — |

---

## Open Questions

1. **`RANKING_COLS` ordering** — What column order best serves the use case? Suggested: OFI_10 first (primary signal), then OBI variants, CVD, spread, microprice lean, volume delta, buy/sell count. Price/pct/vol from the slow loop could be kept as secondary columns or moved to the per-coin page.
   - What we know: the spec says "at minimum OFI_10, OBI_10, CVD, spread, microprice lean, volume delta, buy/sell count"
   - What's unclear: whether to keep pct_1h/24h/volatility in the main table or drop them
   - Recommendation: keep pct_1h/24h/vol from slow loop (useful context for ranking); place after OFI/OBI columns

2. **Standalone mode** — When `rolling is None` (dashboard runs without collector), `/coin/{id}` has no live data. Should it silently show `—` for all indicators, or redirect to `/chart/{id}`?
   - Recommendation: Show `—` with a message "Live data unavailable — running standalone. See historical chart."

3. **`_LIVE` pre-population on startup** — `serve_in_background()` pre-populates `_LIVE` from SQLite (metrics.db) which only has: `price, pct_1h, pct_24h, volatility, ofi, microprice, spread`. The new multilevel fields will show `—` until the first `_fast_loop` cycle (1s). This is acceptable behavior.

---

## Sources

### Primary (HIGH confidence)
- Direct code inspection of `troll/ml_signals/dashboard.py` — all behavioral findings
- Direct code inspection of `troll/dydx_collector/second_snapshot.py` — schema fields
- Direct code inspection of `troll/dydx_collector/collector.py` — `_second_rolling` population
- Direct code inspection of `troll/ml_signals/indicators.py` — `MultiLevelOFI`, `MultiLevelOBI`, `Microprice`
- Direct code inspection of `troll/ml_signals/metrics_computer.py` — standalone fallback path
- Direct code inspection of `troll/ml_signals/metrics_store.py` — SQLite schema
- `troll/CLAUDE.md` — all project constraints

### Secondary (MEDIUM confidence)
- [ASSUMED] Plotly `Plotly.react()` API — 1s full-redraw of 300 × 4 traces is under 10ms. Verified from Plotly documentation pattern but not benchmarked in this environment.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `_fast_loop` background thread completes per-second indicator replay for ~50 coins in under 1s | Indicator Computation | If computation takes > 1s, the loop drifts and `_LIVE` becomes stale |
| A2 | `Plotly.react()` redraw of 300 points × 4 traces completes in < 100ms in the browser | Chart Approach | Visible lag on each poll; degrade to 2s interval if needed |

## Metadata

**Confidence breakdown:**
- Existing architecture: HIGH — verified by direct code inspection
- New metrics formulas: HIGH — trivial arithmetic from verified schema fields
- Chart approach: HIGH — Plotly CDN already present; `setInterval` + `Plotly.react()` is standard Plotly live-update pattern
- Indicator replay performance: MEDIUM — structure-based estimate, no profiling

**Research date:** 2026-06-28
**Valid until:** 2026-09-28 (stable codebase, no fast-moving dependencies)
