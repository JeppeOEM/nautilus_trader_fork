---
phase: 02-dashboard-upgrade
verified: 2026-06-28T00:00:00Z
status: passed
score: 5/5
behavior_unverified: 0
overrides_applied: 0
---

# Phase 2: Dashboard Upgrade Verification Report

**Phase Goal:** The dashboard shows a ranked metrics table (OFI_3/5/10, OBI_3/5/10, CVD, spread, microprice lean, volume delta, buy/sell count) with clickable rows that navigate to a single-coin view showing all derivable indicators plus a real-time line chart with mid price, bid, ask, and microprice as separate lines updating every second from the in-process rolling deque — no Parquet reads for the live path, no bar chart.
**Verified:** 2026-06-28
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Main rankings table shows OFI_10, OBI_10, CVD, spread, microprice lean, volume delta, buy/sell count per coin | VERIFIED | `RANKING_COLS` (lines 78–95, `dashboard.py`) contains all 12 required live-metric keys: `ofi_10`, `ofi_5`, `ofi_3`, `obi_10`, `obi_5`, `obi_3`, `cvd`, `spread`, `microprice_lean`, `volume_delta`, `buy_count`, `sell_count`. Verified by DASH-01 command returning PASS. |
| 2 | Clicking any coin row navigates to `/coin/{id}` single-coin view | VERIFIED | `_render_rankings_page` line 205: `f"<td><a href='/coin/{iid}'>{label}</a> ..."` — instrument label is the primary hyperlink to the live view. Verified by DASH-02 source-inspection command. |
| 3 | Single-coin view shows a panel of all derivable indicators (OFI_3/5/10, OBI_3/5/10, microprice, spread, CVD, volume imbalance, buy/sell avg size) | VERIFIED | `_render_live_coin_page` (lines 403–419) builds a 14-row indicator table: OFI10/5/3, OBI10/5/3, Microprice, microprice lean ("u lean"), Spread, CVD, Vol delta (volume imbalance), Buy#, Sell#, Avg trade size. All keys come from `_LIVE` (populated by `_metrics_from_rolling`). No Parquet read. |
| 4 | Single-coin view includes a real-time line chart with 4 lines (mid, bid, ask, microprice) — 1s resolution, rolling 5-min window, updates live | VERIFIED | JS IIFE in `_render_live_coin_page` (lines 428–450): `setInterval(poll, 1000)` polls every second; `Plotly.react` called with 4 traces (mid #aaa, bid #26a69a, ask #ef5350, microprice #f0883e dot). Deque maxlen=300 (verified in `collector.py` line 89: `deque(maxlen=300)`) = 300 s = 5 min at 1s cadence. `refresh_seconds=86400` so no `<meta http-equiv="refresh">` interferes. |
| 5 | All live data comes from the in-process `_second_rolling` deque — no Parquet reads on the live path | VERIFIED | `_coin_chart_json` (lines 365–390) reads only from `rolling.get(iid, [])` — no catalog calls. `_render_live_coin_page` reads only from `_LIVE` (under `_METRICS_LOCK`) — no catalog calls. `_fast_loop` populates `_LIVE` exclusively from `_metrics_from_rolling(rolling)` when rolling is not None. `_ROLLING` is assigned in `serve_in_background()` from the collector's own deque. No `ParquetDataCatalog`, `trade_ticks`, or `compute_chart_series` in either live-path function. (`compute_chart_series` exists only in `_render_chart_page()`, the intentionally Parquet-backed `/chart/` route.) |

**Score: 5/5 truths verified**

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `troll/ml_signals/dashboard.py` | Extended `_metrics_from_rolling()`, fixed `_fast_loop`, `RANKING_COLS` upgrade, `_coin_chart_json()`, `_render_live_coin_page()`, `_ROLLING` global, `/data/coin/` route, `/coin/` live route | VERIFIED | File exists, substantive (687 lines), all named functions present and wired into `do_GET`. Commits `526998c258` and `2acfdba8ab` confirmed in git log. |
| `troll/ml_signals/test_dashboard_metrics.py` | 5 original tests + 2 chart-JSON tests (7 total), real `DydxSecondSnapshot` objects, no mocks | VERIFIED | File exists (126 lines). `pytest` runs 7 tests, all pass (0.62s). No `Mock`/`MagicMock` in file. Tests use real `DydxSecondSnapshot` constructor and real `InstrumentId`. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `dashboard.py:_Handler.do_GET` | `dashboard.py:_coin_chart_json` | `/data/coin/` branch calls `_coin_chart_json(symbol, _ROLLING)` | WIRED | Lines 599–607: `elif path.startswith("/data/coin/"):` → `_coin_chart_json(symbol, _ROLLING).encode()` → JSON response with `Content-Type: application/json`. Returns before HTML send block. |
| `dashboard.py:_render_live_coin_page` (JS IIFE) | `dashboard.py:/data/coin/ route` | `fetch("/data/coin/"+encodeURIComponent(iid))` in `setInterval(poll, 1000)` | WIRED | Line 445: `fetch("/data/coin/"+encodeURIComponent(iid))` inside the `poll()` function called every 1000ms. Result flows into `update(d)` which calls `Plotly.react`. |
| `dashboard.py:serve_in_background` | `dashboard.py:_ROLLING` | `global _ROLLING = rolling` at function entry | WIRED | Lines 648–650: `global _ROLLING` declared, `_ROLLING = rolling` assigned. Handler threads read `_ROLLING` directly (GIL-atomic deque snapshot via `list()`). |
| `dashboard.py:_fast_loop` | `dashboard.py:_LIVE` | Full dict merge `_LIVE[iid] = {**_LIVE.get(iid, {}), **m}` inside `_METRICS_LOCK` | WIRED | Line 563: the full-merge pattern inside `with _METRICS_LOCK:`. Preserves slow-loop fields (price, pct_1h) while overwriting all fast-loop fields every second. |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `_render_live_coin_page` | `m` (metrics dict) | `_LIVE.get(symbol, {})` under `_METRICS_LOCK` | Yes — `_LIVE` populated by `_fast_loop` from `_metrics_from_rolling(rolling)` every second | FLOWING |
| `_coin_chart_json` | `snaps` | `list(rolling.get(iid, []))` — in-process deque | Yes — collector appends `DydxSecondSnapshot` to `_second_rolling[iid]` every second | FLOWING |
| `RANKING_COLS` render in `_render_rankings_page` | `_LIVE.values()` | Same `_LIVE` dict as above | Yes | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| DASH-01: RANKING_COLS superset check | `python -c "... assert need<=keys ..."` | PASS | PASS |
| DASH-02: row label links to `/coin/{iid}` | `python -c "... assert \"/coin/{iid}'>{label}\" in r ..."` | PASS | PASS |
| DASH-03/04: live coin page has no Parquet, has setInterval + Plotly.react + json.dumps(symbol) | `python -c "... assert 'ParquetDataCatalog' not in s; assert 'setInterval' in s ..."` | PASS | PASS |
| No Parquet in `_coin_chart_json` | `python -c "... assert 'ParquetDataCatalog' not in s; assert 'trade_ticks' not in s ..."` | PASS | PASS |
| `/data/coin/` route in `do_GET` with `application/json` | `python -c "... assert '/data/coin/' in h; assert 'application/json' in h ..."` | PASS | PASS |
| 7 unit tests pass | `.venv/bin/pytest troll/ml_signals/test_dashboard_metrics.py -x -q` | `7 passed in 0.62s` | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| DASH-01 | 02-01-PLAN, 02-02-PLAN | Rankings table shows OFI_10, OBI_10, CVD, spread, microprice lean, volume delta, buy/sell count | SATISFIED | `RANKING_COLS` contains all required keys; `_metrics_from_rolling()` computes them from the rolling deque |
| DASH-02 | 02-02-PLAN | Clicking coin row navigates to `/coin/{id}` | SATISFIED | Row label is `<a href='/coin/{iid}'>` in `_render_rankings_page` |
| DASH-03 | 02-02-PLAN | Single-coin view indicator panel (OFI_3/5/10, OBI_3/5/10, microprice, spread, CVD, volume imbalance, buy/sell avg size) | SATISFIED | `_render_live_coin_page` renders 14-row panel from `_LIVE` |
| DASH-04 | 02-02-PLAN | Real-time 4-line chart (mid/bid/ask/microprice), 1s resolution, rolling 5-min window | SATISFIED | `setInterval(poll, 1000)` + `Plotly.react` with 4 traces; deque `maxlen=300` = 5 min |

Note: DASH-01 through DASH-04 are defined inline in ROADMAP.md (not in REQUIREMENTS.md, which covers v1.0 guardrail requirements only). All four are satisfied.

---

### Anti-Patterns Found

None.

- No `TBD`, `FIXME`, or `XXX` markers in either modified file.
- No `TODO`, `HACK`, or `PLACEHOLDER` comments.
- No `Mock`/`MagicMock` in the test file (uses real `DydxSecondSnapshot` and `InstrumentId` objects — TEST-03 compliant).
- No `return null`, empty implementations, or stub handlers.
- `_metrics_from_rolling()` is substantive (computes OFI/OBI at 3 levels, CVD, volume delta, microprice lean, avg trade size from real `DydxSecondSnapshot` fields).
- The `compute_chart_series` call in `_render_chart_page` is the intentionally Parquet-backed `/chart/` historical view — explicitly outside the live path.

---

### Human Verification Required

None. All success criteria were verifiable programmatically via source inspection and test execution.

---

### Gaps Summary

No gaps. All 5 success criteria are verified. 7 tests pass. No anti-patterns. No Parquet on the live path.

---

_Verified: 2026-06-28_
_Verifier: Claude (gsd-verifier)_
