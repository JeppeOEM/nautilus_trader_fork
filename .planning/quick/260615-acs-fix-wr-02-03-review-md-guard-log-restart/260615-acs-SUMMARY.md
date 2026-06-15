---
phase: quick
plan: 01
subsystem: testing
tags: [bybit-recorder, parquet-catalog, error-handling, startup-robustness, tdd]

# Dependency graph
requires:
  - phase: 03-reliability-for-24-7-operation
    provides: "_log_restart_gaps restart-gap WARNING (D-06) and _run_conversion catalog guard (CR-01)"
provides:
  - "Guarded ParquetDataCatalog construction in _log_restart_gaps (WR-02 closed)"
  - "Unit test proving _log_restart_gaps swallows a catalog construction error and skips the per-instrument loop"
affects: [reliability-for-24-7-operation, recorder-startup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Top-level catalog construction wrapped in try/except + logger.exception + early return, mirroring _run_conversion"

key-files:
  created: []
  modified:
    - scripts/bybit_recorder/strategy.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py

key-decisions:
  - "Mirrored the existing _run_conversion guard verbatim (try/except Exception -> logger.exception -> return) rather than inventing a new pattern, per CLAUDE.md prefer-built-in / no-new-pattern guidance"

patterns-established:
  - "Filesystem-touching catalog construction inside a startup/shutdown hook is always guarded so a transient I/O error degrades gracefully instead of aborting on_start()/on_stop()"

requirements-completed: [WR-02]

# Metrics
duration: ~12min
completed: 2026-06-15
---

# Phase quick Plan 01: Guard _log_restart_gaps Catalog Construction Summary

**Wrapped the top-level `ParquetDataCatalog` construction in `_log_restart_gaps` in a try/except (mirroring `_run_conversion`) so a transient catalog-open failure is logged and swallowed instead of propagating out of `on_start()` and aborting the recorder before any subscriptions are made.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-06-15T07:40:00Z
- **Completed:** 2026-06-15T07:52:12Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- WR-02 closed: `ParquetDataCatalog(self.config.catalog_path)` in `_log_restart_gaps` is now inside a `try/except Exception` that calls `logger.exception("Failed to open catalog for restart-gap check")` and returns early before the per-instrument loop.
- The documented startup-robustness guarantee ("a failure here is logged but must not prevent subscriptions", strategy.py:173-175 / docstring 223-225) is restored — a catalog-open error now degrades gracefully (warning logged, gaps skipped) rather than aborting startup.
- Added a TDD unit test proving the swallow + early-return behavior; full recorder suite (48 tests) green.

## Task Commits

Each task was committed atomically (TDD RED then GREEN):

1. **Task 1 (RED): failing test for the guard** - `17318d9943` (test)
2. **Task 1 (GREEN): guard catalog construction in _log_restart_gaps** - `e2a1db6abc` (fix)

_Note: The plan ordered the guard as Task 1 and the test as Task 2, but both tasks carried `tdd="true"`. Following the TDD execution flow, the test was authored and committed first (RED) to prove the unguarded code propagates the error, then the implementation was committed (GREEN). Net effect and final state are identical to the plan's intent; the per-task content is unchanged._

## Files Created/Modified
- `scripts/bybit_recorder/strategy.py` - Wrapped `ParquetDataCatalog(...)` in `_log_restart_gaps` in a `try/except Exception` with a WHY comment; on failure logs `logger.exception("Failed to open catalog for restart-gap check")` and returns early. The per-instrument gap-check loop body is unchanged.
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - Added `test_log_restart_gaps_swallows_catalog_construction_error`, mirroring `test_on_stop_swallows_catalog_construction_error`: patches `scripts.bybit_recorder.strategy.ParquetDataCatalog` with `side_effect=RuntimeError("boom")`, builds the strategy with a non-empty instrument list, calls `_log_restart_gaps()` (asserts no raise), and asserts no WARNING records were emitted (the per-instrument loop was skipped).

## Decisions Made
- Mirrored `_run_conversion`'s existing guard pattern verbatim (try/except Exception -> `logger.exception(...)` -> `return`) instead of inventing a new one — consistent with the codebase style and the plan's explicit instruction.

## Deviations from Plan

None - plan executed exactly as written. (The only ordering note is that, because both tasks are `tdd="true"`, the test was committed before the implementation per the mandated TDD RED->GREEN flow; this is the documented TDD execution behavior, not a scope or content deviation.)

## Issues Encountered
- The worktree lacked the compiled Nautilus `.so` extension modules (gitignored) and `uv run pytest` failed mid-sync trying to reinstall `jupytext` (`Directory not empty`). Resolved without modifying any tracked files: (1) symlinked the 111 `.so` files from the main checkout into the worktree (gitignored symlinks, not committed), and (2) ran the suite via the main checkout's venv pytest (`/home/mrqdt/code/nautilus_trader_fork/.venv/bin/python -m pytest`) with `PYTHONPATH` set to the worktree root so worktree `scripts/` and `nautilus_trader/` take precedence. This is the same `.so` symlink approach used by prior plans in this phase.

## Verification
- `grep -n "Failed to open catalog for restart-gap check" scripts/bybit_recorder/strategy.py` -> match at line 236 (Task 1 automated verify).
- New + sibling guard test: `2 passed, 30 deselected`.
- Full recorder suite: `tests/unit_tests/persistence/recorder/ -> 48 passed`.
- `git diff --name-only HEAD~2 HEAD` -> only `scripts/bybit_recorder/strategy.py` and `tests/unit_tests/persistence/recorder/test_recorder_strategy.py`; no files under `nautilus_trader/` modified; no untracked files left behind.

## Known Stubs
None - no stubs introduced.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- WR-02 is closed. CR-01 (the `_run_conversion` `on_stop` guard) was already addressed in Phase 03; WR-01 (`restart_gap_threshold_seconds > 0` fail-fast validation) remains an open lower-priority todo in STATE.md and is out of scope for this quick task.

## Self-Check: PASSED

- FOUND: scripts/bybit_recorder/strategy.py
- FOUND: tests/unit_tests/persistence/recorder/test_recorder_strategy.py
- FOUND: .planning/quick/260615-acs-fix-wr-02-03-review-md-guard-log-restart/260615-acs-SUMMARY.md
- FOUND commit 17318d9943 (test RED)
- FOUND commit e2a1db6abc (fix GREEN)

---
*Phase: quick*
*Completed: 2026-06-15*
