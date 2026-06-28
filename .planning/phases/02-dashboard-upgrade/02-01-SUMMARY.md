---
phase: 02-dashboard-upgrade
plan: "01"
subsystem: ml_signals/dashboard
tags: [live-metrics, ofi, cvd, microprice, dashboard, bug-fix]
dependency_graph:
  requires: []
  provides: [extended-live-metrics, fast-loop-full-merge]
  affects: [troll/ml_signals/dashboard.py]
tech_stack:
  added: []
  patterns: [in-process rolling deque, full-dict-merge pattern]
key_files:
  created:
    - troll/ml_signals/test_dashboard_metrics.py
  modified:
    - troll/ml_signals/dashboard.py
decisions:
  - "Extracted _trade_aggregates() helper to keep _metrics_from_rolling() within ~30-line READ-01 budget"
  - "Used {**_LIVE.get(iid, {}), **m} full-merge so slow-loop fields (price, pct_1h, volatility) are preserved while fast-loop fields are always overwritten"
  - "Named local 'microprice' extracted from inline conditional to allow microprice_lean reference without duplication"
metrics:
  duration: "88 seconds"
  completed: "2026-06-28"
  tasks_completed: 2
  files_changed: 2
status: complete
---

# Phase 02 Plan 01: Extend Live Metrics (CVD, Microprice Lean, Avg Trade Size) + Fix Fast-Loop Write Summary

## One-liner

Extended `_metrics_from_rolling()` with CVD, volume delta, buy/sell count, avg trade size, and microprice lean; fixed `_fast_loop` partial-write bug so all 17 metrics propagate to `_LIVE` every second.

## What Was Built

### Task 1: Extend _metrics_from_rolling() and fix _fast_loop

**File:** `troll/ml_signals/dashboard.py`

Added `_trade_aggregates(snaps)` helper (6 lines) that returns `(total_buy_vol, total_sell_vol, total_buy_count, total_sell_count)` over all snapshots in the window. Keeps `_metrics_from_rolling()` within the ~30-line READ-01 budget.

Extended `_metrics_from_rolling()` with:
- `microprice` extracted to a named local variable (was an inline conditional — needed for `microprice_lean`)
- `mid = (bid_prices[0] + ask_prices[0]) / 2` from latest snapshot
- `microprice_lean = microprice - mid` (None if either is None)
- `cvd = total_buy_vol - total_sell_vol` over the window
- `volume_delta = latest.buy_volume - latest.sell_volume`
- `buy_count = latest.buy_count`, `sell_count = latest.sell_count`
- `avg_trade_size = (total_buy_vol + total_sell_vol) / total_count` or None when count == 0 (T-02-01 guard)

Fixed `_fast_loop` write path: replaced the partial `.update({ts, ofi, microprice, spread})` branch with a single full-merge `_LIVE[m["instrument_id"]] = {**_LIVE.get(m["instrument_id"], {}), **m}` inside the existing `with _METRICS_LOCK:` block (T-02-02 thread safety preserved).

**Commit:** `526998c258`

### Task 2: Create test_dashboard_metrics.py

**File:** `troll/ml_signals/test_dashboard_metrics.py`

5 tests covering all new financial calculations:
- `test_cvd` — cumulative volume delta sums across 2-snapshot window
- `test_microprice_lean` — bid=100/size=1, ask=102/size=3 → microprice=100.5, mid=101.0, lean=-0.5
- `test_avg_trade_size_no_trades` — count==0 returns None, no ZeroDivisionError
- `test_avg_trade_size_value` — weighted average across 2 snaps = 2.0
- `test_metrics_keys` — all 17 keys present in output dict

All tests use real `DydxSecondSnapshot` objects (no Mock/MagicMock).

**Commit:** `a393486715`

## Verification Results

```
python -m pytest troll/ml_signals/test_dashboard_metrics.py -x -q
5 passed in 0.64s

python -c "... assert '{**_LIVE.get(' in src ... assert all(k in m ...)"
Verification passed
```

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-02-01 | `total_count > 0` guard in avg_trade_size — returns None instead of raising ZeroDivisionError; covered by test_avg_trade_size_no_trades |
| T-02-02 | Full-merge write kept inside existing `with _METRICS_LOCK:` block |

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Known Stubs

None — all new fields are computed from in-process deque data, not wired to placeholder values.

## Self-Check: PASSED

- `troll/ml_signals/dashboard.py` modified: FOUND
- `troll/ml_signals/test_dashboard_metrics.py` created: FOUND
- Commit `526998c258` (feat dashboard): FOUND
- Commit `a393486715` (test dashboard): FOUND
