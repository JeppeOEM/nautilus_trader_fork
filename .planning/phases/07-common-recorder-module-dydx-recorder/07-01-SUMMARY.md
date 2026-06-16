---
phase: 07-common-recorder-module-dydx-recorder
plan: 01
subsystem: infra
tags: [recorder, parquet-catalog, streaming, hot-reload, dependency-injection, refactor]

# Dependency graph
requires:
  - phase: 02-record-the-full-data-breadth
    provides: RecorderStrategy + build_streaming_config (the recorder infra being extracted)
  - phase: 06-hot-reload-config-changes
    provides: _on_config_reload hot-reload diff machinery (the venue-coupling point resolved here)
provides:
  - scripts.common_recorder package — exchange-agnostic recorder infra
  - scripts.common_recorder.strategy.RecorderStrategy / RecorderStrategyConfig (venue-neutral)
  - scripts.common_recorder.strategy.RecorderStrategy.set_config_loader (injected-loader hook)
  - scripts.common_recorder.config.build_streaming_config / _resolve_catalog_path / _validate_positive_thresholds
  - scripts.bybit_recorder.strategy / config as re-export shims (back-compat)
affects: [dydx-recorder, common-recorder, future venue recorders]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Injected venue config loader (set_config_loader) parallel to set_data_client — common strategy imports no venue package"
    - "Module-level default-loader fallback (set_default_config_loader) registered by a venue re-export shim for back-compat wiring"
    - "Re-export shim modules keep moved symbols importable at their old paths"

key-files:
  created:
    - scripts/common_recorder/__init__.py
    - scripts/common_recorder/config.py
    - scripts/common_recorder/strategy.py
  modified:
    - scripts/bybit_recorder/config.py
    - scripts/bybit_recorder/strategy.py
    - scripts/bybit_recorder/recorder.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py

key-decisions:
  - "Hot-reload config loader is INJECTED (set_config_loader) instead of imported, inverting the only venue dependency so common_recorder imports nothing venue-specific"
  - "Added a module-level set_default_config_loader fallback so the bybit re-export shim (and unit tests built without explicit wiring) resolve the Bybit loader via late binding"
  - "Renamed internal _bybit_client to venue-neutral _data_client (private state; no test asserts the name)"
  - "build_streaming_config / _validate_positive_thresholds are duck-typed against the venue config object, so they serve Bybit and a future dYdX config without importing either"

patterns-established:
  - "Injected-dependency pattern for venue coupling: set_data_client + set_config_loader, both wired by recorder.py after node.build()"
  - "Re-export shim back-compat: venue package re-exports moved symbols (RecorderStrategy, build_streaming_config, load_recorder_config)"

requirements-completed: [DYDX-01]

# Metrics
duration: ~35min
completed: 2026-06-16
---

# Phase 7 Plan 01: Common Recorder Module Extraction Summary

**Extracted the exchange-agnostic RecorderStrategy + shared config helpers into a new scripts/common_recorder/ package, inverting the only venue dependency (the hot-reload config loader) into an injected callable, with the full 62-test recorder suite green as the no-behavior-change gate (DYDX-01).**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-06-16T15:10Z (approx)
- **Completed:** 2026-06-16
- **Tasks:** 3
- **Files modified:** 7 (3 created, 4 modified)

## Accomplishments
- New `scripts/common_recorder/` package holding the venue-neutral `RecorderStrategy` / `RecorderStrategyConfig` plus shared path-resolution, streaming-config, and threshold-validation helpers — importing nothing from any venue recorder package.
- Inverted the only venue coupling: `_on_config_reload` now calls an INJECTED config loader (`set_config_loader`, parallel to `set_data_client`) instead of importing `scripts.bybit_recorder.config.load_recorder_config`.
- `scripts/bybit_recorder/{strategy.py, config.py}` rewritten as re-export shims so every existing import resolves unchanged; `recorder.py` wires the Bybit loader via `set_config_loader(load_recorder_config)`.
- Full 62-test recorder suite passes (62 passed, 0 failed) — the DYDX-01 no-behavior-change gate.

## Task Commits

Each task was committed atomically:

1. **Task 1: Create common_recorder + move shared config helpers** - `81df8bfc56` (feat)
2. **Task 2: Move RecorderStrategy into common_recorder + inject config loader** - `b6033bc39a` (feat)
3. **Task 3: Wire injected loader in recorder.py + prove full suite green** - `86938d258a` (feat)

## Files Created/Modified
- `scripts/common_recorder/__init__.py` - Package marker for the shared exchange-agnostic recorder module.
- `scripts/common_recorder/config.py` - Shared `_resolve_catalog_path`, `build_streaming_config`, `_validate_positive_thresholds` (repo-root anchor recomputed via `parents[2]` for the new path depth; duck-typed against the venue config object).
- `scripts/common_recorder/strategy.py` - Moved `RecorderStrategy` + `RecorderStrategyConfig`; added `set_config_loader` and a module-level `set_default_config_loader` fallback; renamed `_bybit_client` → `_data_client`; `_on_config_reload` uses the injected loader.
- `scripts/bybit_recorder/config.py` - Now imports + re-exports the shared helpers and calls `_validate_positive_thresholds`; keeps Bybit-specific `InstrumentEntry`, `RecorderConfig`, `load_recorder_config` (linear/spot parsing) unchanged.
- `scripts/bybit_recorder/strategy.py` - Re-export shim for `RecorderStrategy` / `RecorderStrategyConfig`; re-exports `load_recorder_config` and registers it as the common strategy's module-level default loader (late-bound).
- `scripts/bybit_recorder/recorder.py` - Added `strategy.set_config_loader(load_recorder_config)` after `set_data_client`.
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - Followed the relocated `ParquetDataCatalog` symbol to its new module path in two construction-error tests (mechanical co-move, no behavior change).

## Decisions Made
- **Injected loader over import:** resolving the inverted dependency (RESEARCH Pitfall 1) via `set_config_loader` keeps `common_recorder` free of any venue import, exactly mirroring the existing `set_data_client` pattern.
- **Module-level default-loader fallback:** the existing hot-reload unit tests patch `scripts.bybit_recorder.strategy.load_recorder_config` and build strategies via a helper that does NOT call `set_config_loader`. To keep those tests passing unchanged WITHOUT coupling common to a venue, the bybit shim registers a late-bound default loader (`set_default_config_loader`) that resolves `scripts.bybit_recorder.strategy.load_recorder_config` at call time — so `mocker.patch` on that module attribute is honored. The per-instance `set_config_loader` from `recorder.py` always takes precedence.
- **`_data_client` rename:** the internal client attribute is private state with no test asserting its name (confirmed via grep), so renaming to a venue-neutral name is safe.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Module-level default-loader fallback added to keep hot-reload tests green**
- **Found during:** Task 2 (move RecorderStrategy + inject loader)
- **Issue:** The plan's `_on_config_reload` spec returns early (debug log) when `self._config_loader is None`. But the shared test helper `_build_strategy` never calls `set_config_loader`, and 5 hot-reload tests patch `scripts.bybit_recorder.strategy.load_recorder_config` expecting it to be invoked. With only the per-instance injected loader, those tests would hit the no-loader return path and fail — yet the plan requires they pass unchanged, NOT by editing the test.
- **Fix:** Added `set_default_config_loader` / `_resolve_config_loader` to `common_recorder/strategy.py` (a module-level fallback, set by the venue package, never imported by common). The bybit shim registers a late-bound default that looks up `scripts.bybit_recorder.strategy.load_recorder_config` at call time, so the test patch is honored. Common stays venue-agnostic; the per-instance `set_config_loader` (used by `recorder.py`) takes precedence.
- **Files modified:** scripts/common_recorder/strategy.py, scripts/bybit_recorder/strategy.py
- **Verification:** All 11 hot-reload tests + full 62-test suite pass with `_build_strategy` and the hot-reload tests unedited.
- **Committed in:** b6033bc39a (Task 2 commit)

**2. [Rule 3 - Blocking] Followed relocated `ParquetDataCatalog` patch target in two strategy tests**
- **Found during:** Task 2
- **Issue:** `test_on_stop_swallows_catalog_construction_error` and `test_log_restart_gaps_swallows_catalog_construction_error` patch `scripts.bybit_recorder.strategy.ParquetDataCatalog`. That module-global moved with `RecorderStrategy` to `scripts.common_recorder.strategy`, so the old patch path no longer exists (`AttributeError`). Unlike `load_recorder_config` (kept patchable via re-export + default-loader late binding), `ParquetDataCatalog` is a module-global the strategy constructs directly — it cannot be made patchable at the old path without coupling common to the bybit module.
- **Fix:** Updated the two `mocker.patch` targets (and one `caplog.at_level` logger name) to the new module path `scripts.common_recorder.strategy`. This is a mechanical co-move following the relocated symbol — no assertion or behavior change.
- **Files modified:** tests/unit_tests/persistence/recorder/test_recorder_strategy.py
- **Verification:** Both tests pass; full 62-test suite green.
- **Committed in:** b6033bc39a (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 3 - blocking)
**Impact on plan:** Both deviations were necessary to satisfy the plan's own no-behavior-change / unchanged-test gate while preserving the venue-agnostic design. The module-level default-loader is a minimal, documented addition (the plan's `set_config_loader` remains the primary path). The patch-target co-move is mechanical. No scope creep, no behavior change to the recorder.

## Issues Encountered
- **Test execution environment (worktree):** The worktree has no compiled `nautilus_trader` extensions (only tracked source), and `uv run --no-sync` created an empty venv. Resolved by running tests with the main repo's built `.venv` from a temp shim directory that symlinks the worktree's `scripts/` and `tests/` — so `nautilus_trader` resolves to the main compiled build while `scripts.*`/`tests.*` resolve to the worktree. This is a test-runner setup detail only; no repository files were changed for it.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `scripts.common_recorder` is ready to be reused by the dYdX recorder plans in this phase: a new venue recorder constructs `RecorderStrategy`, then injects its own data client (`set_data_client`) and config loader (`set_config_loader`).
- No blockers. The duck-typed `build_streaming_config` / `_validate_positive_thresholds` already accept a non-Bybit config object, so the dYdX config can reuse them without modification.

---
*Phase: 07-common-recorder-module-dydx-recorder*
*Completed: 2026-06-16*

## Self-Check: PASSED

All created files exist on disk and all task + metadata commits exist in git history:
- Files: scripts/common_recorder/{__init__.py, config.py, strategy.py}, 07-01-SUMMARY.md — all FOUND.
- Commits: 81df8bfc56 (Task 1), b6033bc39a (Task 2), 86938d258a (Task 3), 30c2277337 (SUMMARY) — all FOUND.
- Gate: full recorder suite 62 passed / 0 failed.
