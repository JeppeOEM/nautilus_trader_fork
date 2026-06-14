---
phase: 03-reliability-for-24-7-operation
plan: 02
subsystem: infra
tags: [nautilus, bybit, recorder, observability, heartbeat, logging, toml-config]

# Dependency graph
requires:
  - phase: 03-01
    provides: RecorderStrategy on_start/on_stop/_run_conversion structure, _convert_finalized_feather_files, restart-gap-log pattern, _build_strategy test helper
provides:
  - Per-stream last-seen tracking keyed by (stream, instrument_id) updated in every on_* handler
  - Native "heartbeat" Clock.set_timer + _heartbeat callback logging periodic INFO heartbeats and WARNINGs for stale streams
  - _stale_threshold_s per-data-type threshold lookup with configurable default
  - heartbeat_interval_seconds / stale_threshold_default_seconds / stale_threshold_seconds config fields, validated positive at load (fail-fast)
affects: [phase-03-future-plans, operations/runbook]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Second native Clock.set_timer (heartbeat) mirroring the existing convert-stream timer pattern"
    - "Per-stream last-seen dict keyed by (stream, instrument_id) tuple, recorded BEFORE any dedup early-return"
    - "Per-data-type stale thresholds via dict config field with a positive-validated default fallback"

key-files:
  created: []
  modified:
    - scripts/bybit_recorder/strategy.py
    - scripts/bybit_recorder/config.py
    - scripts/bybit_recorder/recorder.py
    - scripts/bybit_recorder/recorder.toml
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py

key-decisions:
  - "on_funding_rate records _last_seen BEFORE its dedup early-return so a rarely-changing funding stream is not falsely flagged stale (Pitfall 4)"
  - "Per-data-type stale thresholds (trade/quote/bar=90s, deltas=60s, mark/index/funding=30s) configured via recorder.toml [recorder.stale_threshold_seconds] sub-table with a 90s default fallback"
  - "heartbeat_interval_seconds, stale_threshold_default_seconds, and each value in stale_threshold_seconds validated > 0 at load time (raise ValueError, T-3-05 DoS-of-logs mitigation)"

patterns-established:
  - "Pattern: heartbeat timer + _heartbeat callback as the per-stream-liveness analog to the existing convert-stream timer"

requirements-completed: [REL-03]

# Metrics
duration: 25min
completed: 2026-06-14
---

# Phase 03 Plan 02: Heartbeat / Stale-Stream Visibility Summary

**Per-stream last-seen tracking and a native heartbeat timer that logs INFO heartbeats and WARNINGs for any subscribed stream that goes quiet beyond its configurable, per-data-type stale threshold.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-06-14T19:00:00Z (approx)
- **Completed:** 2026-06-14T19:20:00Z (approx)
- **Tasks:** 2 completed
- **Files modified:** 4 (strategy.py, config.py, recorder.py, recorder.toml) + 1 test file

## Accomplishments
- Every `on_*` handler (`on_trade_tick`, `on_quote_tick`, `on_order_book_deltas`, `on_bar`, `on_mark_price`, `on_index_price`, `on_funding_rate`) now records a per-stream `_last_seen[(stream, instrument_id)] = self.clock.timestamp_ns()` timestamp.
- `on_funding_rate` records last-seen BEFORE its dedup early-return, so the rarely-changing funding stream is not falsely flagged stale just because most updates are deduped (Pitfall 4).
- A second native `Clock.set_timer(name="heartbeat", ...)` (mirroring the existing `"convert-stream"` timer) fires `_heartbeat`, which logs `Heartbeat: N active streams` at INFO and `Stale stream: <stream> <instrument_id> idle <Xs> (> <Ys> threshold)` at WARNING for any stream whose idle time exceeds its `_stale_threshold_s`.
- `heartbeat_interval_seconds`, `stale_threshold_default_seconds`, and per-stream `stale_threshold_seconds` are now configurable end-to-end: `recorder.toml` -> `RecorderConfig` (validated positive, fail-fast `ValueError`) -> `recorder.py` pass-through -> `RecorderStrategyConfig` -> consumed by `_stale_threshold_s`/`_heartbeat`.

## Task Commits

Each task was committed atomically (TDD RED/GREEN for Task 1):

1. **Task 1 RED: failing tests for last-seen tracking + heartbeat** - `e5d83a5598` (test)
2. **Task 1 GREEN: implement last-seen tracking + heartbeat timer + stale detection** - `986b2c5ab9` (feat)
3. **Task 2: validated heartbeat-interval + per-type stale-threshold config** - `8591b69011` (feat)

**Plan metadata:** pending (this commit)

## Files Created/Modified
- `scripts/bybit_recorder/strategy.py` - `_last_seen` dict (init), per-handler last-seen one-liners, funding pre-dedup last-seen, second `"heartbeat"` `Clock.set_timer`, `_stale_threshold_s`, `_heartbeat`, three new `RecorderStrategyConfig` fields (`heartbeat_interval_seconds`, `stale_threshold_seconds`, `stale_threshold_default_seconds`)
- `scripts/bybit_recorder/config.py` - `RecorderConfig` gains `heartbeat_interval_seconds`, `stale_threshold_default_seconds`, `stale_threshold_seconds`; `load_recorder_config` parses + validates them positive (fail-fast `ValueError`)
- `scripts/bybit_recorder/recorder.py` - threads the three new fields from `recorder_cfg` into `RecorderStrategyConfig(...)`
- `scripts/bybit_recorder/recorder.toml` - adds `heartbeat_interval_seconds = 30`, `stale_threshold_default_seconds = 90`, and `[recorder.stale_threshold_seconds]` sub-table with per-type A1 defaults (`trade=90, quote=90, deltas=60, bar=90, mark=30, index=30, funding=30`)
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - 7 new tests: `test_on_start_sets_heartbeat_timer`, `test_on_trade_tick_updates_last_seen`, `test_on_funding_rate_updates_last_seen_before_dedup`, `test_heartbeat_warns_when_stream_stale`, `test_heartbeat_no_warn_when_stream_fresh`, `test_load_recorder_config_rejects_nonpositive_heartbeat_interval`, `test_load_recorder_config_accepts_default_heartbeat_config`; `_write_recorder_toml` helper extended with optional `heartbeat_interval_seconds` injection

## Decisions Made
- Stream labels used in `_last_seen` / `stale_threshold_seconds`: `"trade"`, `"quote"`, `"deltas"`, `"bar"`, `"mark"`, `"index"`, `"funding"` (matching the per-handler subscriptions).
- `_stale_threshold_s(stream)` falls back to `stale_threshold_default_seconds` (default 90) for any stream label not present in `stale_threshold_seconds`, so an incomplete TOML table never disables stale-detection for an unlisted stream.
- Default thresholds chosen per A1/RESEARCH guidance: generous (90s) for bursty trade/quote/bar streams, moderate (60s) for order-book deltas, tight (30s) for the ~100ms-driven linear mark/index/funding tickers.

## Deviations from Plan

None - plan executed exactly as written. `.planning/phases/03-reliability-for-24-7-operation/03-PATTERNS.md` referenced in `<read_first>` did not exist on disk; implementation proceeded directly from the plan's `<action>` text (which fully specified the timer/handler/heartbeat code), `03-RESEARCH.md`, and the existing `_convert_stream` timer / `_log_restart_gaps` analog code already in `strategy.py`. No functional gaps resulted — all acceptance criteria and verification commands pass.

## Issues Encountered
- The worktree lacked the compiled Nautilus `.so` extension modules (gitignored). Symlinked all 677 `.so` files from the main checkout at `/home/mrqdt/code/nautilus_trader_fork/` into the worktree (gitignored symlinks, not committed) to run the test suite — same approach as Wave 1.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- REL-03 (heartbeat/stale-stream visibility) is complete and the full recorder test suite (44 tests) plus the wave-merge gate (`tests/unit_tests/persistence/recorder/` + `tests/unit_tests/persistence/test_catalog.py`, 140 passed / 1 skipped) are green.
- `recorder.toml` remains valid TOML with sensible defaults; operators can tune `heartbeat_interval_seconds` and per-stream `stale_threshold_seconds` without code changes.
- No blockers for subsequent Phase 03 plans.

---
*Phase: 03-reliability-for-24-7-operation*
*Completed: 2026-06-14*

## Self-Check: PASSED

- FOUND: .planning/phases/03-reliability-for-24-7-operation/03-02-SUMMARY.md
- FOUND: e5d83a5598 (test: RED)
- FOUND: 986b2c5ab9 (feat: GREEN Task 1)
- FOUND: 8591b69011 (feat: Task 2)
