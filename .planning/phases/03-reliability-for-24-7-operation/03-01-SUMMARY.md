---
phase: 03-reliability-for-24-7-operation
plan: 01
subsystem: persistence/recorder
tags: [reliability, graceful-shutdown, gap-visibility, reconnect, REL-02, REL-04, D-06]
requires:
  - "RecorderStrategy._convert_finalized_feather_files (Approach B, commit 69219cca51)"
  - "StreamingFeatherWriter / ParquetDataCatalog (Nautilus framework)"
provides:
  - "RecorderStrategy.on_stop() final flush+convert on SIGTERM (REL-02)"
  - "RecorderStrategy._run_conversion() shared by the periodic timer + on_stop"
  - "RecorderStrategy._log_restart_gaps() on_start gap-log WARNING (D-06)"
  - "RecorderStrategyConfig.restart_gap_threshold_seconds + RecorderConfig threading"
  - "restart-shaped (two-writer) conversion regression test (REL-02)"
  - "REL-04 no-reconnect-code structural proof"
affects:
  - "scripts/bybit_recorder/strategy.py"
  - "scripts/bybit_recorder/config.py"
  - "scripts/bybit_recorder/recorder.py"
  - "scripts/bybit_recorder/recorder.toml"
tech-stack:
  added: []
  patterns:
    - "Graceful-shutdown flush+convert via Strategy.on_stop() (runs before kernel _close_writer)"
    - "Shared conversion body (_run_conversion) reused by timer callback and on_stop"
    - "Per-instrument/per-type try/except swallow so one failure cannot block others or startup"
    - "Restart-shaped test: two StreamingFeatherWriter instances against one instance_id"
key-files:
  created:
    - "tests/unit_tests/persistence/recorder/test_recorder_strategy.py (extended; on_stop + gap-log tests)"
    - ".planning/phases/03-reliability-for-24-7-operation/deferred-items.md"
  modified:
    - "scripts/bybit_recorder/strategy.py"
    - "scripts/bybit_recorder/config.py"
    - "scripts/bybit_recorder/recorder.py"
    - "scripts/bybit_recorder/recorder.toml"
    - "tests/unit_tests/persistence/recorder/test_recorder_conversion.py"
decisions:
  - "on_stop() converts only already-finalized feather files; this session's active-file tail converts on the NEXT restart's first cycle (D-02/D-03, accepted one-cycle visibility delay)"
  - "on_stop never references/flushes/closes the kernel '*' writer (Pitfall 1); only the strategy-owned funding writer is flushed"
  - "Gap-log reads max(ts_init) from catalog.trade_ticks per instrument; per-instrument try/except so a read error cannot block startup"
  - "restart_gap_threshold_seconds is a PositiveInt config field (default 60), threaded recorder.toml -> RecorderConfig -> RecorderStrategyConfig"
metrics:
  duration: ~30min
  completed: 2026-06-14
  tasks_completed: 3
  tasks_total: 4
  files_changed: 6
---

# Phase 3 Plan 01: Graceful-shutdown flush+convert, restart gap visibility, adapter-driven reconnect Summary

Adds `on_stop()` final flush+convert (REL-02), an `on_start` restart-gap WARNING (D-06), and a restart-shaped conversion regression test plus a structural REL-04 no-reconnect-code proof — all using existing Nautilus hooks, with `nautilus_trader/` core untouched.

## What Was Built

**Task 1 (TDD) — `on_stop()` + `_run_conversion()` (REL-02):**
- Extracted the existing `_convert_stream` body into a shared `_run_conversion()` method (constructs the catalog, flushes the strategy-owned funding writer when present, runs the per-type `_convert_finalized_feather_files` loop with per-type try/except swallow).
- `_convert_stream(event)` is now a thin delegate to `_run_conversion()` (REL-01 timer path unchanged).
- Added `on_stop()` override that calls `_run_conversion()`. The kernel orders `on_stop()` before `_close_writer()` and `Strategy._stop()` runs `on_stop()` before cancelling timers, so the feather files are still on disk. `on_stop()` mainly catches files finalized mid-session by a `SCHEDULED_DATES` rotation; this session's active-file tail converts on the next restart's first cycle (D-02/D-03). `on_stop()` never touches the kernel `"*"` writer (Pitfall 1).

**Task 2 — restart-shaped conversion regression + REL-04 structural proof:**
- Added `test_restart_shaped_conversion_converts_finalized_file_without_raise`: models two process starts as two `StreamingFeatherWriter` instances against the same `instance_id` (process 2's writer, with a strictly-later creation timestamp, finalizes process 1's file). Process 2's first `_convert_finalized_feather_files` cycle converts process 1's finalized file in full and skips process 2's still-active file — no raise, no data loss, no non-disjoint-interval error (Approach B). `parquet.py` is unmodified.
- REL-04 structural assertion holds: no non-comment `reconnect`/`resubscribe` code anywhere in `scripts/bybit_recorder/` (reconnect is fully adapter-driven).

**Task 3 (TDD) — `on_start` restart-gap WARNING (D-06):**
- Added `restart_gap_threshold_seconds: PositiveInt = 60` to `RecorderStrategyConfig` and `RecorderConfig`, threaded `recorder.toml` → `RecorderConfig` → `recorder.py` → `RecorderStrategyConfig`, and documented a commented example line in `recorder.toml`.
- Added `_log_restart_gaps()`: for each instrument, reads `max(ts_init)` from `catalog.trade_ticks(...)`; if no prior data, continues; otherwise logs a WARNING when `gap_s > restart_gap_threshold_seconds`. Per-instrument try/except so a catalog-read error for one instrument cannot block startup. Called from `on_start()` after instrument validation and before the subscription loop.

## Verification

- `python -m pytest tests/unit_tests/persistence/recorder/ -q` → 37 passed.
- `python -m pytest tests/unit_tests/persistence/recorder/ tests/unit_tests/persistence/test_catalog.py -q` (wave-merge gate) → 133 passed, 1 skipped (development_only).
- `grep -rniE "reconnect|resubscribe" scripts/bybit_recorder/ | grep -vE "^\s*[A-Za-z0-9_/.]+:\s*#"` → empty (REL-04 no-custom-code proof).
- `ruff check scripts/bybit_recorder/strategy.py scripts/bybit_recorder/config.py scripts/bybit_recorder/recorder.py tests/unit_tests/persistence/recorder/` → All checks passed.
- `nautilus_trader/` core unmodified (no files under `nautilus_trader/` in the diff).

## TDD Gate Compliance

- Task 1: RED commit `0ff488d7e4` (failing on_stop tests) → GREEN commit `f12a2b4386` (on_stop implementation). Confirmed RED failed (0 == 7 assertion) before GREEN.
- Task 3: RED commit `5faa32f81b` (failing gap-log tests) → GREEN commit `e22a6f064c` (gap-log implementation). Confirmed RED failed (TypeError: unexpected kwarg) before GREEN.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed pre-existing C420 in the `_build_strategy` test helper**
- **Found during:** Task 3 verification (running `ruff check tests/unit_tests/persistence/recorder/`).
- **Issue:** A pre-existing dict comprehension `{instrument_id: 50 for instrument_id in instrument_ids}` in the `_build_strategy` helper (present at the base commit, not introduced by this plan) triggered ruff C420, failing the plan's ruff verification gate.
- **Fix:** Replaced with `dict.fromkeys(instrument_ids, 50)`. This is the helper Task 3 extended (added `catalog_path`/`restart_gap_threshold_seconds` params), so the trivial autofix is in a file already in scope.
- **Files modified:** tests/unit_tests/persistence/recorder/test_recorder_strategy.py
- **Commit:** d500b0618e

### Out-of-scope (deferred, NOT fixed)

- Pre-existing ruff D213 + I001 in `scripts/bybit_recorder/inspect_catalog.py` (a file untouched by this plan). Logged to `deferred-items.md`. The plan's exact ruff verification command does not include `inspect_catalog.py`, so these do not affect the gate.

## Environment note (for the merge/orchestrator)

This worktree did not contain the compiled Nautilus `.so` artifacts (gitignored, absent in a fresh worktree). To run the test suite, the 111 compiled `.so` files were symlinked from the main checkout (`/home/mrqdt/code/nautilus_trader_fork/`) into the worktree. These symlinks are gitignored and are NOT part of any commit.

## Checkpoint Status

Task 4 is a `checkpoint:human-verify` (gate="blocking") — a live Bybit mainnet integration proof (same-day SIGTERM restart no-loss + gap-log WARNING on restart + forced-disconnect auto-resume). It cannot be unit-tested and was NOT auto-approved per the plan instruction. Awaiting human verification.

## Self-Check: PASSED

- Files: all 6 plan files present (verified on disk).
- Commits: all 6 commits present in `git log 728ac54..HEAD` (0ff488d7e4, f12a2b4386, b26e50e79b, 5faa32f81b, e22a6f064c, d500b0618e).
