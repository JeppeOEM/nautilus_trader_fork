---
phase: 03-reliability-for-24-7-operation
plan: 03
subsystem: infra
tags: [recorder, shutdown, config-validation, parquet, gap-closure]

# Dependency graph
requires:
  - phase: 03-reliability-for-24-7-operation (plan 01)
    provides: RecorderStrategy.on_stop()/_run_conversion() graceful-shutdown flush+convert
  - phase: 03-reliability-for-24-7-operation (plan 02)
    provides: heartbeat/stale-threshold fail-fast validation pattern in config.py
provides:
  - "_run_conversion() guards ParquetDataCatalog(...) construction and _funding_writer.flush() with try/except (CR-01 closed)"
  - "load_recorder_config fail-fast ValueError for restart_gap_threshold_seconds <= 0 (WR-01 closed)"
  - "REQUIREMENTS.md REL-02 and REL-04 reconciled to Complete"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "swallow-and-log (logger.exception + early return/continue) applied to catalog construction and funding-writer flush in _run_conversion, mirroring the existing per-type conversion guard"
    - "fail-fast > 0 validation for restart_gap_threshold_seconds mirroring heartbeat_interval_seconds/stale_threshold_* pattern in config.py"

key-files:
  created: []
  modified:
    - scripts/bybit_recorder/strategy.py
    - scripts/bybit_recorder/config.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
    - .planning/REQUIREMENTS.md

key-decisions:
  - "Catalog construction failure returns early from _run_conversion (no catalog -> nothing to convert into), while funding-writer flush failure logs and continues so already-finalized per-type conversion still proceeds"
  - "restart_gap_threshold_seconds validated via a new local variable bound before the ValueError checks, then passed into RecorderConfig(...) construction, mirroring heartbeat_interval_seconds"
  - "REL-02 marked Complete (CR-01 fix) and REL-04 marked Complete (doc-sync gap per 03-VERIFICATION.md, code already verified)"

patterns-established:
  - "Gap-closure plans reconcile REQUIREMENTS.md checkboxes/status table alongside the code fix in the same plan"

requirements-completed: ["REL-02", "REL-04"]

# Metrics
duration: 15min
completed: 2026-06-15
---

# Phase 03 Plan 03: Guard recorder shutdown conversion + validate restart-gap threshold Summary

**Wrapped `_run_conversion()`'s `ParquetDataCatalog(...)` construction and `_funding_writer.flush()` in try/except swallow-and-log guards, added fail-fast `restart_gap_threshold_seconds > 0` validation to `load_recorder_config`, and reconciled REQUIREMENTS.md REL-02/REL-04 to Complete.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2 completed
- **Files modified:** 4

## Accomplishments
- CR-01 (BLOCKER) closed: `on_stop()`/`_run_conversion()` can no longer fault the SIGTERM shutdown path if catalog construction or funding-writer flush raises
- WR-01 (WARNING) closed: `restart_gap_threshold_seconds <= 0` now fails fast at config load with a clear `ValueError`, consistent with sibling thresholds
- REQUIREMENTS.md REL-02 and REL-04 reconciled to Complete, matching verified code status from 03-VERIFICATION.md
- Recorder unit suite grew from 44 to 47 passing tests

## Task Commits

Each task was committed atomically:

1. **Task 1: Guard _run_conversion() catalog construction + funding flush (CR-01, REL-02)** - `4d1df2822a` (fix)
2. **Task 2: Fail-fast validate restart_gap_threshold_seconds > 0 (WR-01) + reconcile REQUIREMENTS.md** - `9da1540a53` (fix)

_Note: Both tasks are TDD (tdd="true") — tests were written first (RED), confirmed failing, then the implementation was added (GREEN), all within a single commit per task._

## Files Created/Modified
- `scripts/bybit_recorder/strategy.py` - `_run_conversion()` now wraps `ParquetDataCatalog(self.config.catalog_path)` construction in try/except (log + early return) and `self._funding_writer.flush()` in try/except (log + continue)
- `scripts/bybit_recorder/config.py` - `load_recorder_config` binds `restart_gap_threshold_seconds` to a local variable and raises `ValueError` if `<= 0`, before passing it to `RecorderConfig(...)`
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - new tests `test_on_stop_swallows_catalog_construction_error`, `test_on_stop_swallows_funding_writer_flush_error`, `test_load_recorder_config_rejects_nonpositive_restart_gap_threshold`; extended `_write_recorder_toml(...)` with optional `restart_gap_threshold_seconds` kwarg
- `.planning/REQUIREMENTS.md` - REL-02 and REL-04 checkboxes and status-table rows changed from Pending to Complete

## Decisions Made
- On catalog construction failure, `_run_conversion()` returns early (no catalog means nothing to convert into) — matches the prescribed fix shape in 03-REVIEW.md CR-01.
- On funding-writer flush failure, `_run_conversion()` logs and continues into the per-type conversion loop — already-finalized files should still convert even if the funding writer's flush failed.
- REL-04 marked Complete based on 03-VERIFICATION.md's confirmation that Truths 4 and 5 were already VERIFIED in code; this plan only closed the documentation-sync gap (no code change needed for REL-04 beyond what 03-01 already delivered).

## Deviations from Plan

None - plan executed exactly as written. The worktree lacked the compiled Nautilus `.so` extension modules (gitignored, absent in a fresh worktree, same as plans 03-01/03-02). All 677 `.so` files were symlinked from the main checkout at `/home/mrqdt/code/nautilus_trader_fork/` into the worktree (gitignored symlinks, not committed) to run the test suite.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 03 reliability gaps (CR-01, WR-01) closed; full recorder unit suite green (47 passed)
- REQUIREMENTS.md REL-02/REL-03/REL-04 all Complete
- No changes under `nautilus_trader/` (fork core untouched, verified via `git diff --stat nautilus_trader/`)

---
*Phase: 03-reliability-for-24-7-operation*
*Completed: 2026-06-15*
