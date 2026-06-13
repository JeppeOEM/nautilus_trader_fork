---
phase: 01-bootstrap-config-end-to-end-slice
plan: 01
subsystem: testing
tags: [pytest, tomllib, StreamingConfig, ParquetDataCatalog, TradeTick, nautilus-test-kit]

# Dependency graph
requires: []
provides:
  - "tests/unit_tests/persistence/recorder/ test package (package marker + conftest fixtures)"
  - "RED unit tests encoding CONF-01/02/03, REC-01/07, REL-01 behavioral contracts for scripts.bybit_recorder"
  - "Working roundtrip example of StreamingFeatherWriter -> ParquetDataCatalog.convert_stream_to_data(subdirectory='live') -> catalog.trade_ticks()"
affects: [01-02, 01-03, 01-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "pytest fixtures defined as bare `def <fixture_name>(...)` (no name= kwarg) under tests/unit_tests/persistence/recorder/conftest.py"
    - "Recorder modules imported inside test bodies (not at module top) so collection succeeds before scripts.bybit_recorder exists"
    - "Cache.add_instrument(...) required before StreamingFeatherWriter.write() will create a per-instrument trade_tick writer (cache.instrument() lookup gate)"

key-files:
  created:
    - tests/unit_tests/persistence/recorder/__init__.py
    - tests/unit_tests/persistence/recorder/conftest.py
    - tests/unit_tests/persistence/recorder/test_recorder_config.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
    - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
  modified: []

key-decisions:
  - "Fixtures named directly as def catalog_dir/sample_toml/mock_cache/sample_trade_ticks (no @pytest.fixture(name=...) indirection) to satisfy the plan's literal grep-based acceptance check"
  - "test_recorder_conversion.py's primary test exercises only existing Nautilus framework code (StreamingFeatherWriter + ParquetDataCatalog) and passes today — it is the REL-01 contract baseline that Plan 04's strategy timer callback must reproduce"
  - "Strategy tests import scripts.bybit_recorder.strategy inside a shared _build_strategy helper so all three tests fail uniformly with ModuleNotFoundError until Plan 03 creates the module"

patterns-established:
  - "Recorder test conftest fixtures: catalog_dir (tmp_path-based shared streaming+catalog root), sample_toml (CONTEXT-shaped TOML string), mock_cache (pytest-mock Mock with configurable .instrument()), sample_trade_ticks (monotonic ts_init TradeTick list via TestDataStubs + TestInstrumentProvider.btcusdt_perp_binance)"

requirements-completed: []

# Metrics
duration: 35min
completed: 2026-06-13
---

# Phase 1 Plan 01: Recorder Test Scaffold (Wave 0 RED tests) Summary

**Created `tests/unit_tests/persistence/recorder/` with shared fixtures and three RED test files encoding the CONF-01/02/03, REC-01/07, and REL-01 behavioral contracts for the not-yet-built `scripts.bybit_recorder` package.**

## Performance

- **Duration:** 35 min
- **Started:** 2026-06-13T14:50:00Z
- **Completed:** 2026-06-13T15:25:00Z
- **Tasks:** 2 completed
- **Files modified:** 5 created

## Accomplishments
- New `tests/unit_tests/persistence/recorder/` package collects cleanly (9 tests discovered, 0 collection errors)
- `conftest.py` provides `catalog_dir`, `sample_toml`, `mock_cache`, `sample_trade_ticks` fixtures matching the CONTEXT TOML shape (one `[[instruments.linear]]` + one `[[instruments.spot]]` entry)
- `test_recorder_config.py` (4 tests) and `test_recorder_strategy.py` (3 tests) all fail RED with `ModuleNotFoundError: No module named 'scripts.bybit_recorder'` — the intended failure mode, not a collection error
- `test_recorder_conversion.py` (1 active + 1 skipped) demonstrates the full feather-write -> `convert_stream_to_data(subdirectory="live")` -> `catalog.trade_ticks()` roundtrip against existing framework code, and PASSES today — this is the REL-01 baseline the strategy's timer callback (Plan 04) must reproduce
- Skipped placeholder `test_double_conversion_same_day_behavior` documents the A2 partial-day re-conversion open question for Plan 04

## Task Commits

Each task was committed atomically:

1. **Task 1: Create recorder test package and shared fixtures** - `550511e6f5` (test)
2. **Task 2: Write RED tests for config, strategy, and conversion behaviors** - `6c2ef24d35` (test)

**Plan metadata:** (pending — recorded by orchestrator after merge)

## Files Created/Modified
- `tests/unit_tests/persistence/recorder/__init__.py` - Package marker (empty, 14-line copyright header)
- `tests/unit_tests/persistence/recorder/conftest.py` - Shared fixtures: `catalog_dir`, `sample_toml`, `mock_cache`, `sample_trade_ticks`
- `tests/unit_tests/persistence/recorder/test_recorder_config.py` - RED tests for `load_recorder_config` (CONF-01/02) and `build_streaming_config` (REC-07)
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - RED tests for `RecorderStrategy.on_start` fail-fast validation (CONF-03) and per-instrument `subscribe_trade_ticks` (REC-01) + conversion timer (D-01)
- `tests/unit_tests/persistence/recorder/test_recorder_conversion.py` - Integration test for feather -> parquet -> reload roundtrip (REL-01); skipped A2 placeholder for double-conversion behavior

## Decisions Made
- Fixture functions are named exactly `catalog_dir`, `sample_toml`, `mock_cache`, `sample_trade_ticks` (plain `def`, no `@pytest.fixture(name=...)` indirection used by the sibling `tests/unit_tests/persistence/conftest.py`) so the plan's literal `grep -E "def (catalog_dir|...)"` acceptance check passes directly.
- `sample_trade_ticks` uses `TestInstrumentProvider.btcusdt_perp_binance()` (a `CryptoPerpetual`) rather than the default FX instrument from `TestDataStubs.trade_tick`'s default, to keep the fixture aligned with the Bybit linear-perpetual domain this project targets.
- `test_recorder_strategy.py` constructs a real `RecorderStrategy`/`RecorderStrategyConfig` registered against a real `MessageBus`/`Portfolio`/`TestClock` but with a mocked `Cache`, following the `tests/unit_tests/trading/test_strategy.py` harness pattern minus the full engine stack (not needed for `on_start` unit tests).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree missing compiled Rust extension artifacts (`.so`/`.pyi`)**
- **Found during:** Task 1 verification (pytest collection)
- **Issue:** The git worktree checkout does not include the gitignored compiled `nautilus_trader.core.*` extension modules (`.so`/`.pyi` files) present in the main repo checkout. `nautilus_trader/__init__.py` failed to import (`ModuleNotFoundError: No module named 'nautilus_trader.core.data'`), blocking all test collection — not just this plan's new files.
- **Fix:** Created symlinks from the worktree's `nautilus_trader/` tree to the corresponding compiled artifacts in the main repo checkout (`/home/mrqdt/code/nautilus_trader_fork/nautilus_trader/...`). These symlinks are gitignored (`*.so` pattern) and were not committed.
- **Files modified:** None tracked (gitignored symlinks only, outside git's view)
- **Verification:** `python -m pytest tests/unit_tests/persistence/recorder/ --collect-only -q` now succeeds (9 tests collected)
- **Committed in:** N/A (not a tracked change)

**2. [Rule 3 - Blocking] `pytest-asyncio` and `pytest-mock` not synced into the worktree's `.venv`**
- **Found during:** Task 2 verification (running the test suite)
- **Issue:** The repo's `tests/conftest.py` has an autouse `cleanup_event_loop_tasks(event_loop)` fixture requiring `pytest-asyncio`, and the strategy tests use the `mocker` fixture from `pytest-mock`. Neither package was installed in `.venv`, even though both are pinned exactly in `pyproject.toml`/`uv.lock` (`pytest-asyncio==0.23.8`, `pytest-mock>=3.15.1,<4.0.0`) as committed dev dependencies.
- **Fix:** Ran `uv pip install pytest-asyncio==0.23.8` and `uv pip install "pytest-mock>=3.15.1,<4.0.0"` to sync the already-pinned/committed lockfile entries into the venv. No new/unverified packages were introduced — both were already declared and pinned in the committed `pyproject.toml`/`uv.lock`.
- **Files modified:** None tracked (venv-only install)
- **Verification:** `python -m pytest tests/unit_tests/persistence/recorder/ -q` runs to completion (7 RED failures + 1 pass + 1 skip, no fixture-resolution errors)
- **Committed in:** N/A (not a tracked change)

**3. [Rule 1 - Bug] `test_convert_stream_to_data_roundtrips_trade_ticks` initially produced 0 written feather rows**
- **Found during:** Task 2 implementation/verification
- **Issue:** `StreamingFeatherWriter.write()` routes `TradeTick` through a per-instrument writer that is only created if `cache.instrument(instrument_id)` returns a non-`None` instrument. `TestComponentStubs.cache()` returns an empty `Cache(database=None)`, so `cache.instrument()` returned `None`, the writer was never created, `write()` silently no-op'd, and `convert_stream_to_data` found zero feather files (the assertion `len(trades) > 0` failed as `0 > 0`).
- **Fix:** Added `cache.add_instrument(TestInstrumentProvider.btcusdt_perp_binance())` before constructing the `StreamingFeatherWriter` so the per-instrument writer is created.
- **Files modified:** tests/unit_tests/persistence/recorder/test_recorder_conversion.py
- **Verification:** Test now passes — feather file written, converted to parquet, reloaded as 3 `TradeTick` objects
- **Committed in:** 6c2ef24d35 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 environment/blocking, 1 bug)
**Impact on plan:** Deviations 1-2 are local-environment fixes only (gitignored symlinks, venv package sync from already-pinned lockfile entries) with zero tracked-file impact. Deviation 3 is a necessary correctness fix to the new test file, committed as part of Task 2. No scope creep; no architectural changes.

## Issues Encountered
None beyond the deviations documented above.

## Known Stubs
None — this plan creates only test files (no production stubs). The `scripts.bybit_recorder` package referenced by the new tests intentionally does not exist yet; it is the target of Plans 02-04.

## User Setup Required
None - no external service configuration required. (Note: if running tests in a fresh worktree, `pytest-asyncio` and `pytest-mock` may need `uv pip install` if not already synced — see Deviation 2.)

## Next Phase Readiness
- Plans 02-04 now have a concrete, collectible, RED test target for `scripts.bybit_recorder.config`, `scripts.bybit_recorder.strategy`, and the conversion pipeline.
- The conversion test (`test_recorder_conversion.py::test_convert_stream_to_data_roundtrips_trade_ticks`) is GREEN today against pure framework code — Plan 04's `_convert_stream` timer callback should produce an equivalent on-disk result using the fixed `RECORDER_INSTANCE_ID` constant (currently hardcoded identically in the test as `"8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"` per PATTERNS.md D-03 — Plan 03/04 must define this as a shared module constant).
- No blockers for Wave 1 plans.

---
*Phase: 01-bootstrap-config-end-to-end-slice*
*Completed: 2026-06-13*
