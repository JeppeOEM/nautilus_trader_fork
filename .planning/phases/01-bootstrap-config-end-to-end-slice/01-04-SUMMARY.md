---
phase: 01-bootstrap-config-end-to-end-slice
plan: 04
subsystem: testing+reliability
tags: [pytest, ParquetDataCatalog, TradeTick, TradingNode, Bybit, msgspec, CUSTOM_ENCODINGS]

# Dependency graph
requires:
  - "scripts.bybit_recorder.config (01-02)"
  - "scripts.bybit_recorder.strategy + recorder.py (01-03)"
provides:
  - "GREEN test_recorder_conversion.py: feather->parquet->reload roundtrip (REL-01/criterion 5)"
  - "Empirically-pinned A2 behavior: double conversion of an un-rotated feather file is idempotent (already-exists skip)"
  - "Empirically-pinned A3 behavior: on_start missing-instrument RuntimeError propagates as non-zero process exit via node.run(raise_exception=True)"
  - "Verified live E2E: real Bybit trades recorded, converted, and reloaded as native TradeTick from BTCUSDT-LINEAR.BYBIT and ETHUSDT-SPOT.BYBIT"
  - "CUSTOM_ENCODINGS registration for BybitProductType/BybitEnvironment so streaming-enabled TradingNodeConfig.json() succeeds"
affects: [02]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Register pyo3-native adapter enums (BybitProductType, BybitEnvironment) into nautilus_trader.common.config.CUSTOM_ENCODINGS as lambda value: value.name, rather than patching msgspec_encoding_hook"
    - "node.run(raise_exception=True) so TradingNode.run() re-raises on_start exceptions instead of logging-and-swallowing them, giving a non-zero process exit code (D-05)"
    - "A2: convert_stream_to_data on an un-rotated (same-day) feather file a second time is idempotent - second call performs an already-exists skip with stable row count, no ValueError"

key-files:
  created: []
  modified:
    - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
    - scripts/bybit_recorder/recorder.py
    - scripts/bybit_recorder/recorder.toml (touched transiently during live smoke, restored to original value)

key-decisions:
  - "A2 RESULT: double-conversion of the same un-rotated feather file is idempotent (already-exists skip, stable reloaded row count) - NOT a ValueError, so no _convert_stream ValueError handler was needed beyond its existing broad except"
  - "A3 RESULT: proven at unit level that the missing-instrument RuntimeError from on_start is never swallowed into self.stop(); full non-zero exit code is achieved via node.run(raise_exception=True) in recorder.py, with the live-process exit-code path covered by Task 2's unit test plus the standalone driver script referenced in the checkpoint"
  - "Live smoke surfaced a framework gap: NautilusKernel._setup_streaming() calls config.json() which fails on pyo3-native enums (BybitProductType, BybitEnvironment) - fixed via the official CUSTOM_ENCODINGS extension point in scripts/bybit_recorder/recorder.py rather than touching framework code"
  - "conversion_interval_minutes was temporarily set to 1 for the live smoke (commit d34d4b97c0) and restored to 60 immediately afterward (commit 859c5c3d32) - catalog/ and logs/ from the smoke run are left on disk (gitignored) as evidence"

requirements-completed: [REL-01]

# Metrics
duration: 45min
completed: 2026-06-13
---

# Phase 1 Plan 04: Conversion Round-Trip, A2/A3 Empirical Resolution, and Live E2E Smoke Summary

**Turned the feather->parquet->reload conversion test GREEN, empirically pinned both open research assumptions (A2: idempotent double-conversion, A3: non-zero exit on missing-instrument raise), discovered and fixed a framework pyo3-enum encoding gap via `CUSTOM_ENCODINGS`, and verified a live ~2.5-minute Bybit recording that reloaded 304 BTCUSDT-LINEAR and 75 ETHUSDT-SPOT native `TradeTick` objects from the day-partitioned catalog.**

## Performance

- **Duration:** 45 min
- **Started:** 2026-06-13T15:30:00Z
- **Completed:** 2026-06-13T16:15:00Z
- **Tasks:** 3 completed (2 auto + 1 checkpoint:human-verify, approved)
- **Files modified:** 4

## Accomplishments
- `test_convert_stream_to_data_roundtrips_trade_ticks` is GREEN: writes `sample_trade_ticks` to a feather stream via `StreamingFeatherWriter`, converts with `ParquetDataCatalog.convert_stream_to_data(subdirectory="live")`, and reloads `catalog.trade_ticks(...)` returning non-empty `TradeTick` instances (criterion 5 / REL-01)
- `test_double_conversion_same_day_behavior` is active (no longer skipped): converts the same un-rotated feather file twice and asserts the second call is an idempotent already-exists skip with a stable reloaded row count (A2 RESULT)
- A3 resolved: a unit test in `test_recorder_strategy.py` proves the missing-instrument `RuntimeError` raised from `on_start` is never swallowed into `self.stop()`; `recorder.py`'s `main()` now calls `node.run(raise_exception=True)` so this propagates to a non-zero process exit
- Live smoke (Task 3, human-verify, **approved**): full walking-skeleton pipeline ran against Bybit mainnet for ~2.5 minutes, recording 304 `TradeTick`s for `BTCUSDT-LINEAR.BYBIT` and 75 for `ETHUSDT-SPOT.BYBIT` via the native `StreamingFeatherWriter`, converted to parquet by the in-process timer, and reloaded as native `TradeTick` objects from `ParquetDataCatalog('catalog/streaming')`
- Discovered and fixed a framework gap: `NautilusKernel._setup_streaming()` -> `config.json()` -> `msgspec_encoding_hook` has no branch for pyo3-native adapter enums (`BybitProductType`, `BybitEnvironment`), raising `TypeError` as soon as streaming is enabled with a Bybit data client config. Fixed via the official `CUSTOM_ENCODINGS` extension point in `nautilus_trader.common.config`
- `pytest tests/unit_tests/persistence/recorder/ -q` -> 10 passed (all GREEN, no skips)
- `ruff check`, `ruff format --check`, `mypy` all pass on `recorder.py`

## Task Commits

Each task was committed atomically:

1. **Task 1: GREEN conversion round-trip + empirical A2 double-conversion test (REL-01)** - `da148e2b37` (test)
2. **Task 2: Verify non-zero exit on missing-instrument raise (A3 / D-05)** - `c8d759f552` (fix)
3. **Task 3: Live E2E smoke (checkpoint:human-verify, approved)** - resolved via:
   - `d34d4b97c0` (chore) - temporarily set `conversion_interval_minutes=1` for fast smoke
   - `9b7fd7b281` (fix) - registered `BybitProductType`/`BybitEnvironment` into `CUSTOM_ENCODINGS` (framework gap discovered during live run)
   - `859c5c3d32` (chore) - restored `conversion_interval_minutes=60` after smoke

**Plan metadata:** (pending — recorded by this summary commit)

## Files Created/Modified
- `tests/unit_tests/persistence/recorder/test_recorder_conversion.py` - GREEN roundtrip test + active A2 double-conversion test with `# A2 RESULT:` comment
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - A3 unit test proving missing-instrument raise never calls `self.stop()`
- `scripts/bybit_recorder/recorder.py` - `node.run(raise_exception=True)`; `CUSTOM_ENCODINGS[BybitProductType]`/`CUSTOM_ENCODINGS[BybitEnvironment]` registration
- `scripts/bybit_recorder/recorder.toml` - `conversion_interval_minutes` temporarily set to 1 and restored to 60 (net no-op vs. plan baseline)

## Decisions Made
- A2: double-conversion of an un-rotated feather file is idempotent (already-exists skip, stable row count) — confirmed empirically, no `_convert_stream` `ValueError` handler needed beyond its existing broad `except`.
- A3: `node.run(raise_exception=True)` is the mechanism that turns an `on_start` `RuntimeError` into a non-zero process exit; the unit test proves the raise is never converted to a clean `stop()`, and the live smoke's non-bogus-instrument run exiting cleanly with exit 0 is consistent with this (instruments present -> no raise -> normal SIGTERM exit).
- Framework gap (pyo3 enum encoding): fixed via `CUSTOM_ENCODINGS`, the framework's documented/official extension point for `msgspec_encoding_hook`, rather than patching `nautilus_trader/common/config.py` directly — keeps the fix local to `scripts/bybit_recorder/recorder.py`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `config.json()` raised `TypeError` for pyo3-native Bybit enums during streaming setup**
- **Found during:** Task 3 (live E2E smoke, first run)
- **Issue:** `NautilusKernel._setup_streaming()` calls `config.json()` to persist the node config alongside the streaming catalog. `msgspec_encoding_hook` in `nautilus_trader/common/config.py` only has branches for certain `nautilus_trader.model.enums` types, not for pyo3-native adapter enums like `BybitProductType`/`BybitEnvironment` used in the Bybit data client config. The first live run failed immediately with `TypeError: Encoding objects of type <class 'nautilus_trader.core.nautilus_pyo3.bybit.BybitProductType'> is unsupported`.
- **Fix:** Registered both enums in `scripts/bybit_recorder/recorder.py` via the official extension point: `CUSTOM_ENCODINGS[BybitProductType] = lambda value: value.name` and `CUSTOM_ENCODINGS[BybitEnvironment] = lambda value: value.name`.
- **Files modified:** `scripts/bybit_recorder/recorder.py`
- **Verification:** Second live run succeeded — node started, streamed trades for ~2.5 minutes, converted feather to parquet, exited cleanly (exit 0)
- **Commit:** `9b7fd7b281`

---

**Total deviations:** 1 auto-fixed (Rule 1 - framework encoding bug, fixed at the application layer via the official `CUSTOM_ENCODINGS` extension point)
**Impact on plan:** Necessary correctness fix discovered only during the live smoke (no streaming-enabled Bybit config was previously exercised end-to-end). No scope creep; no architectural changes; fix is fully contained to `scripts/bybit_recorder/recorder.py`.

## Issues Encountered
None beyond the deviation documented above.

## Known Stubs
None. All three production-facing behaviors (conversion roundtrip, A2 idempotent re-conversion, A3 non-zero exit) are implemented and exercised by GREEN tests and/or the live smoke.

## Threat Flags
None — this plan's fix (`CUSTOM_ENCODINGS` registration) only affects how the existing `TradingNodeConfig` is serialized for the streaming sidecar file; it introduces no new network endpoints, auth paths, file-access patterns, or schema changes at trust boundaries beyond what 01-01/01-02/01-03 already declared.

## User Setup Required
None - no external service configuration required. The live smoke connected to Bybit mainnet for public market data only (no API keys, per D-10). `catalog/` and `logs/` directories from the live smoke run remain on disk (gitignored) as evidence of the successful E2E run.

## Next Phase Readiness
- Phase 1 (Bootstrap, Config & End-to-End Slice) is complete: all 4 plans done, `pytest tests/unit_tests/persistence/recorder/ -q` is fully GREEN (10 passed, no skips), and the full pipeline (config -> TradingNode -> Bybit subscribe -> StreamingFeatherWriter -> ParquetDataCatalog conversion -> native TradeTick reload) is proven live against real Bybit data for two instruments (one linear perpetual, one spot).
- A2 and A3 — the two open research assumptions blocking confident Phase 2 planning — are both empirically resolved and documented.
- The `CUSTOM_ENCODINGS` pattern for pyo3-native adapter enums should be reused/extended in Phase 2 if additional Bybit-specific enum types appear in config (e.g., for order-book depth or bar aggregation configs).
- No blockers for Phase 2 (Full Data-Type Coverage).

---
*Phase: 01-bootstrap-config-end-to-end-slice*
*Completed: 2026-06-13*

## Self-Check: PASSED

All referenced commit hashes (`da148e2b37`, `c8d759f552`, `d34d4b97c0`, `9b7fd7b281`, `859c5c3d32`) and modified files verified present in git log / on disk.
