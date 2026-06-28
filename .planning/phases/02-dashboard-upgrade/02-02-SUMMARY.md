---
phase: 02-dashboard-upgrade
plan: "02"
subsystem: dashboard
tags: [dashboard, live-chart, ofi, obi, plotly, deque]
status: complete

dependency_graph:
  requires: [02-01]
  provides: [expanded-rankings-table, live-coin-view, coin-chart-json-endpoint]
  affects: [troll/ml_signals/dashboard.py, troll/ml_signals/test_dashboard_metrics.py]

tech_stack:
  added: [json (stdlib), Plotly CDN setInterval polling]
  patterns: [deque-backed JSON endpoint, JS Plotly.react live chart, XSS-safe json.dumps embedding]

key_files:
  created: []
  modified:
    - troll/ml_signals/dashboard.py
    - troll/ml_signals/test_dashboard_metrics.py

decisions:
  - "RANKING_COLS uses plain ASCII labels (no subscript characters) per plan constraint"
  - "microprice_lean label: 'u lean' (ASCII u, not µ/Unicode) per plan ASCII requirement"
  - "_ROLLING set as module-level global in serve_in_background(); handler threads read it without additional locking (GIL-atomic deque snapshot via list())"
  - "Deleted _render_coin_page, _render_footprint_chart, _fmt_intervals and 10 unused imports (DESIGN-03: prefer deletion)"
  - "XSS safety: symbol embedded in JS via json.dumps(symbol) (T-02-03); HTML context via html.escape(symbol) (T-02-04)"

metrics:
  duration: "256s"
  completed: "2026-06-28"
  tasks_completed: 3
  tasks_total: 3
  files_changed: 2
---

# Phase 02 Plan 02: Dashboard Upgrade - Rankings + Live Coin View Summary

**One-liner:** Expanded RANKING_COLS with 12 live metrics, /coin/{id} replaced with deque-backed live indicator panel + 1s-updating 4-line Plotly chart, /data/coin/{id} JSON endpoint.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Expand RANKING_COLS + row link to /coin/{id} | 2acfdba8ab | dashboard.py |
| 2 | Add _coin_chart_json(), _ROLLING, /data/coin/ route | 2acfdba8ab | dashboard.py |
| 3 | Replace /coin/{id} with live deque view + 2 chart JSON tests | 2acfdba8ab | dashboard.py, test_dashboard_metrics.py |

## What Was Built

### Task 1: Expanded Rankings Table (DASH-01, DASH-02)
Replaced 7-column `RANKING_COLS` with 16 columns. New live-data columns (in order):
- `ofi_10`, `ofi_5`, `ofi_3` — format `+.1f`, green/red color
- `obi_10`, `obi_5`, `obi_3` — format `.3f`, color on >0.5 threshold
- `cvd` — format `+.2f`, green/red
- `spread` — format `.6f`, no color
- `microprice_lean` ("u lean") — format `+.6f`, green/red
- `volume_delta` ("Vol d") — format `+.2f`, green/red
- `buy_count` ("Buy#"), `sell_count` ("Sell#") — integer, no color
- Existing slow-loop columns (price, 1h %, 24h %, volatility) appended after

Row link swapped: instrument label now links to `/coin/{id}` (live view); secondary "chart" link goes to `/chart/{id}` (Parquet-backed microstructure view).

### Task 2: _coin_chart_json() + _ROLLING + /data/coin/ (DASH-04)
- `_ROLLING: dict | None = None` added at module level
- `serve_in_background()` sets `global _ROLLING = rolling` so request handlers access the deque
- `_coin_chart_json(iid, rolling)` serializes deque to JSON: timestamps converted ns→ms, mid/bid/ask/microprice arrays, standalone-mode guard (returns empty arrays when rolling is None)
- `/data/coin/{id}` route added in `do_GET` BEFORE `/coin/` branch; returns `application/json`; no Parquet reads, no catalog API calls

### Task 3: Live /coin/{id} View + Tests (DASH-03, DASH-04)
- `_render_live_coin_page(symbol, rolling)` added: reads `_LIVE` under lock, renders 14-row indicator panel, includes empty `<div id="live-chart">`, Plotly CDN script, IIFE JS that polls `/data/coin/+encodeURIComponent(iid)` every 1s and calls `Plotly.react()` with 4 traces (mid #aaa, bid #26a69a, ask #ef5350, microprice #f0883e dot)
- `refresh_seconds=86400` so no `<meta refresh>` drives the chart (JS handles updates)
- `/coin/{id}` handler updated to call `_render_live_coin_page(symbol, _ROLLING)` (no timeframe parsing)
- Deleted: `_render_coin_page`, `_render_footprint_chart`, `_fmt_intervals` and 10 now-unused imports
- Added 2 tests: `test_chart_json_no_rolling` (None guard), `test_chart_json_ts_conversion` (ns→ms)
- All 7 tests pass

## Deviations from Plan

### Cleanup Beyond Minimum Scope

**[Rule 1 - Bug / DESIGN-03 Cleanup] Removed all dead code left by _render_coin_page deletion**
- **Found during:** Task 3
- **Issue:** Deleting `_render_coin_page` left 10 imports and 2 helper functions (`_render_footprint_chart`, `_fmt_intervals`) with no remaining callers. Leaving dead imports would also violate DESIGN-03 (prefer deletion).
- **Fix:** Removed `from ml_signals.book_features import top_of_book_series`, `from ml_signals.candles import TIMEFRAMES/build_candles`, `from ml_signals.catalog_stats import coverage/likely_outages`, `from ml_signals.footprint import build_footprint`, `from ml_signals.indicators import Microprice/OrderFlowImbalance`, `from nautilus_trader.model.identifiers import InstrumentId`, `from nautilus_trader.persistence.catalog import ParquetDataCatalog`, plus constants `MAX_FOOTPRINT_CANDLES` and `FOOTPRINT_BANDS_PER_CANDLE`.
- **Files modified:** troll/ml_signals/dashboard.py
- **Commit:** 2acfdba8ab

## Security Notes (Threat Model Compliance)

- **T-02-03** (XSS, script context): `iid_js = json.dumps(symbol)` embeds the instrument ID into the JS IIFE as a properly escaped string literal. `encodeURIComponent(iid)` used in the fetch URL. Verified by `assert 'json.dumps(symbol)' in s` inspection check.
- **T-02-04** (XSS, HTML context): All HTML-context interpolations use `html.escape(symbol)` (header h1, chart link href).
- **T-02-05** (information disclosure): Accepted — dashboard binds 127.0.0.1 only, serves public market data.
- **list_instruments guard** retained in `/coin/` branch for 404 on unknown symbols (directory listing, not a Parquet data read — acceptable per plan).

## Verification Results

All plan verification commands passed:
1. `RANKING_COLS` superset check: PASSED
2. `/coin/{iid}'>{label}` in row source: PASSED
3. `_coin_chart_json('X', None)` empty-arrays guard: PASSED
4. `hasattr(d, '_ROLLING')`: PASSED
5. `/data/coin/` + `application/json` in do_GET source: PASSED
6. No `ParquetDataCatalog`/`trade_ticks` in `_coin_chart_json` source: PASSED
7. `pytest troll/ml_signals/test_dashboard_metrics.py -x -q`: 7 passed
8. No Parquet calls in `_render_live_coin_page` source: PASSED
9. `setInterval`, `Plotly.react`, `json.dumps(symbol)` in source: PASSED
10. `refresh_seconds=86400` in source: PASSED
11. `_render_live_coin_page` in do_GET source: PASSED

## Self-Check: PASSED

- `/home/mrqdt/code/nautilus_trader_fork/troll/ml_signals/dashboard.py` — FOUND
- `/home/mrqdt/code/nautilus_trader_fork/troll/ml_signals/test_dashboard_metrics.py` — FOUND
- Commit 2acfdba8ab — FOUND
