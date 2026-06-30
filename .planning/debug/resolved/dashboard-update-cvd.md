---
status: root_cause_found
trigger: "why is the dashboard not updating every second, is it heavy in python to do this or what is the problem? and why is most CVD at 0"
created: 2026-06-29
updated: 2026-06-29
---

## Current Focus

hypothesis: Two separate issues — (1) O(n²) hotspot makes _metrics_from_rolling slower than 1s; (2) CVD=0 is correct for thin coins but the interval bug can also kill the fast_loop thread
next_action: Apply fixes directly — persistent OFI indicators + interval ordering fix
reasoning_checkpoint: Diagnosed from source without agent spawn — deep context available

## Root Causes

### Bug 1: Dashboard not updating every second

**File:** `troll/ml_signals/dashboard.py` — `_compute_multilevel()` + `_fast_loop()`

**Root cause (performance):** `_compute_multilevel(snaps, levels)` creates fresh `MultiLevelOFI` indicators
and replays ALL snapshots every second. Called 3× per instrument (levels 3, 5, 10).
Inside `MultiLevelOFI.update_raw` (indicators.py:342): `raw_value = float(sum(self._contributions))`
— that's O(window) on every single update call.

With maxlen=300 rolling window and N subscribed instruments:
- Per second: N × 3 calls × 300 replays × O(300) sum = N × 270,000 ops
- For 60 instruments: 16.2M Python ops/second → easily 2–5+ seconds
- Result: `time.sleep(interval)` waits AFTER a 3-5s computation → effective rate is 4–6s, not 1s

**Secondary (crash risk):** In `_fast_loop`, `interval = 1` is set AFTER `_metrics_from_rolling(rolling)`.
If the first call throws, `interval` is undefined → `time.sleep(interval)` NameError → thread dies silently.

**Fix:** 
- Move `interval = 1` BEFORE the rolling call
- Replace `_compute_multilevel` with persistent OFI indicators (already done for OFI10_z), fed incrementally
- OBI is stateless — compute once from `latest` snapshot only (not by replaying all 300)
- Per-second cost drops from O(300² × levels × N) to O(window × new_snaps × levels × N) ≈ O(300 × 1 × N)

### Bug 2: CVD at 0 for most coins

**File:** `troll/ml_signals/dashboard.py` — `_metrics_from_rolling()`

**Root cause:** CVD = `sum(buy_volume) - sum(sell_volume)` over the 300-second rolling window.
For thin/mid-tier coins (most subscribed coins beyond BTC/ETH): 0–5 trades per minute →
most 1s snapshots have buy_volume=sell_volume=0 → CVD ≈ 0. This is CORRECT behavior.
For BTC/ETH class: buy ≈ sell over a 5-min window in a ranging market → CVD ≈ 0. Also correct.

`volume_delta = latest.buy_volume - latest.sell_volume` (single second) — almost always 0
because most seconds have no trades even on liquid coins.

**Not a bug** — but the 5-minute window is too wide for CVD to show signal. CVD is more useful
as a shorter rolling window (e.g., 60s) or as dollar-normalized (CVD / total_vol).

## Evidence

- `indicators.py:342` — `float(sum(self._contributions))` is O(window) on every update_raw call
- `dashboard.py:513` — `_compute_multilevel` creates fresh indicators and replays all snaps
- `dashboard.py:624` — `interval = 1` set AFTER `_metrics_from_rolling(rolling)` call (crash risk)
- `collector.py:89` — rolling deque maxlen=300 (5 minutes of 1s snapshots)
- CVD math is correct; 0 for thin coins is expected behavior

## Resolution

root_cause: O(n²) OFI replay in _compute_multilevel + interval ordering bug
fix: Persistent incremental OFI indicators (same pattern as OFI10_z); OBI from latest only; fix interval ordering
files_changed: troll/ml_signals/dashboard.py
