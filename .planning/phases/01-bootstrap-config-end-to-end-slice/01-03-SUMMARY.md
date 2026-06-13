---
phase: 01-bootstrap-config-end-to-end-slice
plan: 03
subsystem: strategy+entrypoint
tags: [Strategy, StrategyConfig, set_timer, ParquetDataCatalog, TradingNode, Bybit, UUID4]

# Dependency graph
requires:
  - "tests/unit_tests/persistence/recorder/test_recorder_strategy.py (RED tests from 01-01)"
  - "scripts.bybit_recorder.config (RecorderConfig, load_recorder_config, build_streaming_config from 01-02)"
provides:
  - "scripts.bybit_recorder.strategy.RecorderStrategy + RecorderStrategyConfig"
  - "scripts.bybit_recorder.recorder.main(config_path) + RECORDER_INSTANCE_ID"
affects: [01-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "on_start collect-all-then-raise validation (D-05/D-06) — RuntimeError, never self.stop()"
    - "self.clock.set_timer(name=..., interval=pd.Timedelta(...), callback=self._convert_stream) for in-process conversion (D-01/REL-01)"
    - "_convert_stream wraps ParquetDataCatalog.convert_stream_to_data(subdirectory='live') in try/except, logs+swallows transient errors"
    - "Fixed v4 UUID RECORDER_INSTANCE_ID constant shared by TradingNodeConfig.instance_id and RecorderStrategyConfig.instance_id_str (D-03)"
    - "Cython cdef class instance methods (Cache.instrument, TestClock.set_timer) cannot be monkeypatched — use real Cache(database=None) + cache.add_instrument(...) and clock.timer_names instead of mocker.patch.object"

key-files:
  created:
    - scripts/bybit_recorder/strategy.py
    - scripts/bybit_recorder/recorder.py
  modified:
    - tests/unit_tests/persistence/recorder/conftest.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py

key-decisions:
  - "RECORDER_INSTANCE_ID hardcoded as 8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f — matches the value already referenced in 01-PATTERNS.md and the 01-01 test fixtures, so the strategy test config and the entrypoint constant are consistent"
  - "RecorderStrategyConfig.catalog_path is set (in recorder.py) to recorder_cfg.streaming_path, the same root passed to build_streaming_config's StreamingConfig.catalog_path — both the streaming writer and the conversion-side ParquetDataCatalog read/write {root}/live/{instance_id}/ (Pitfall 5/A4)"
  - "_convert_stream logs and swallows exceptions (does not re-raise) so a transient conversion error does not crash the recorder, per plan instruction"

requirements-completed: [CONF-03, REC-01, REC-07, REL-01]

# Metrics
duration: 25min
completed: 2026-06-13
---

# Phase 1 Plan 03: RecorderStrategy + recorder.py Entrypoint Summary

**Implemented `RecorderStrategy`/`RecorderStrategyConfig` (fail-fast collect-all-missing validation, per-instrument trade-tick subscription, in-process conversion timer) and `recorder.py` (TradingNode bootstrap with native streaming and a fixed v4 UUID instance_id shared with the strategy) — turning all of `test_recorder_strategy.py` GREEN and completing the walking-skeleton wiring.**

## Performance

- **Duration:** 25 min
- **Tasks:** 2 completed
- **Files modified:** 2 created, 2 modified (test infra fix)

## Accomplishments
- `RecorderStrategyConfig(StrategyConfig, frozen=True)` with `instrument_ids`, `catalog_path`, `instance_id_str`, `conversion_interval_minutes: PositiveInt = 60`
- `RecorderStrategy.on_start`: collects every configured instrument id missing from `self.cache.instrument(id)`, and if non-empty raises `RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")` listing ALL missing ids — never calls `self.stop()` (D-05/D-06, T-01-06)
- When all instruments are present: subscribes `self.subscribe_trade_ticks(id)` once per configured instrument (REC-01), then `self.clock.set_timer(name="convert-stream", interval=pd.Timedelta(minutes=...), callback=self._convert_stream)` (D-01/REL-01)
- `_convert_stream(event)`: `ParquetDataCatalog(self.config.catalog_path).convert_stream_to_data(instance_id=self.config.instance_id_str, data_cls=TradeTick, subdirectory="live")`, wrapped in try/except that logs and swallows errors (transient-error resilience; Pitfall 2 verified empirically in Plan 04)
- `on_trade_tick` is a debug-log no-op (REC-07 auto-flow via kernel `"*"` subscription)
- `recorder.py`: `RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"` (valid v4 UUID), `main(config_path)` loads TOML, builds `streaming = build_streaming_config(recorder_cfg)`, builds `TradingNodeConfig` with `instance_id=UUID4.from_str(RECORDER_INSTANCE_ID)`, `streaming=streaming`, mainnet Bybit data client (LINEAR+SPOT product types, env-var creds), builds `RecorderStrategyConfig` with the same `RECORDER_INSTANCE_ID` and `catalog_path=recorder_cfg.streaming_path`, then `node.build()` / `try: node.run() finally: node.dispose()`
- All 3 tests in `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` pass GREEN; full recorder test package (8 passed, 1 skipped) passes
- `python -c "import scripts.bybit_recorder.recorder"` and the v4-UUID gate both pass
- `ruff check`, `ruff format --check`, and `mypy` pass cleanly on both new files

## Task Commits

Each task was committed atomically:

1. **Task 1: RecorderStrategy — validate, subscribe, schedule conversion (CONF-03, REC-01, REL-01)** - `79d93356f1` (feat) — also includes a fix to `tests/unit_tests/persistence/recorder/conftest.py` and `test_recorder_strategy.py` (mock_cache fixture)
2. **Task 2: recorder.py entrypoint — build node with streaming + fixed instance_id (REC-07, REL-01 wiring)** - `b42fabab48` (feat)

**Plan metadata:** (pending — recorded by orchestrator after merge)

## Files Created/Modified
- `scripts/bybit_recorder/strategy.py` - `RecorderStrategy` + `RecorderStrategyConfig`
- `scripts/bybit_recorder/recorder.py` - `RECORDER_INSTANCE_ID` + `main(config_path)` entrypoint
- `tests/unit_tests/persistence/recorder/conftest.py` - `mock_cache` fixture now returns a real `Cache(database=None)` (Rule 1 fix)
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - tests updated to use `cache.add_instrument(...)` and `clock.timer_names` instead of monkeypatching Cython cdef-class methods (Rule 1 fix)

## Decisions Made
- `RECORDER_INSTANCE_ID` is the same fixed v4 UUID string already present in 01-PATTERNS.md / the 01-01 test fixtures (`8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f`), kept identical across node, strategy config, and tests (D-03).
- `RecorderStrategyConfig.catalog_path` is wired to `recorder_cfg.streaming_path` in `recorder.py` — the single shared root that both the `StreamingConfig` writer and the conversion-side `ParquetDataCatalog` use (Pitfall 5/A4), matching 01-02's `build_streaming_config` decision.
- `_convert_stream` logs and continues on exception (does not re-raise), per the plan's explicit instruction that a transient conversion error must not kill the recorder.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `mock_cache` fixture passed a plain `Mock` to `Portfolio(cache=...)`, which PyO3 rejects (`TypeError: Argument 'cache' has incorrect type (expected nautilus_trader.cache.base.CacheFacade, got Mock)`)**
- **Found during:** Task 1 verification (first test run)
- **Issue:** The Plan 01 `mock_cache` fixture in `conftest.py` returned `mocker.Mock()` with `cache.instrument` assigned to a `mocker.Mock(return_value=...)`. `Portfolio.__init__` enforces (via PyO3/Cython type checking) that `cache` is an instance of `CacheFacade`, so a plain `Mock` fails at strategy-construction time, before `on_start` is even reached.
- **Fix:** Changed `mock_cache` to return a real `Cache(database=None)` (a `CacheFacade` subclass). Attempting to monkeypatch `cache.instrument` via `mocker.patch.object` then failed with `AttributeError: ... attribute 'instrument' is read-only` because `Cache` is a Cython `cdef class` — its methods cannot be monkeypatched on an instance. Updated the three tests in `test_recorder_strategy.py` to instead call `cache.add_instrument(instrument)` to simulate a "present" instrument (leaving others unregistered to simulate "missing", since `cache.instrument(id)` naturally returns `None` for unregistered ids). Similarly, `mocker.patch.object(clock, "set_timer")` failed for the same cdef-class reason (`TestClock.set_timer` is read-only); the conversion-timer test was changed to assert `"convert-stream" in clock.timer_names` after a real `on_start()` call instead of spying on `set_timer`.
- **Files modified:** `tests/unit_tests/persistence/recorder/conftest.py`, `tests/unit_tests/persistence/recorder/test_recorder_strategy.py`
- **Verification:** `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -q` -> 3 passed; full recorder package `pytest tests/unit_tests/persistence/recorder/ -q` -> 8 passed, 1 skipped
- **Commit:** `79d93356f1` (Task 1 commit)

**2. [Rule 3 - Blocking] Worktree missing compiled Rust extension artifacts (`.so`/`.pyi`) — recurrence of 01-01/01-02 Deviation 1**
- **Found during:** Pre-Task-1 test run (pytest collection failed with `ModuleNotFoundError: No module named 'nautilus_trader.core.data'`)
- **Issue:** This worktree's checkout does not include the gitignored compiled `nautilus_trader.core.*` extension modules present in the main repo checkout, blocking all test collection.
- **Fix:** Recreated symlinks from this worktree's `nautilus_trader/` tree to the corresponding compiled `.so`/`.pyi` artifacts in the main repo checkout (`/home/mrqdt/code/nautilus_trader_fork/nautilus_trader/...`). Gitignored (`*.so`/`*.pyi`), not committed.
- **Files modified:** None tracked (gitignored symlinks only)
- **Verification:** `pytest tests/unit_tests/persistence/recorder/ -q` runs to completion
- **Committed in:** N/A (not a tracked change)

---

**Total deviations:** 2 (1 bug fix to test infra committed with Task 1, 1 environment/blocking recurrence with zero tracked-file impact).
**Impact on plan:** No scope creep; no architectural changes. The test-infra fix was required to even reach the RED-to-GREEN transition this plan was meant to produce.

## Issues Encountered
None beyond the deviations documented above.

## Known Stubs
None. `strategy.py` and `recorder.py` implement the full CONF-03/REC-01/REC-07/REL-01 contracts described in the plan. `node.run()` itself is not exercised here (requires live Bybit connectivity) — that is explicitly Plan 04's E2E smoke test, as noted in the plan's `<verification>` section.

## Threat Flags
None — this plan implements exactly the mitigations specified in the plan's `<threat_model>`: T-01-06 via `on_start` raising (not `self.stop()`), T-01-07 via no credential literals in `recorder.py` (grep gate passes), and T-01-08 via the shared `RECORDER_INSTANCE_ID` constant + hardcoded `subdirectory="live"`. No new network endpoints, auth paths, or schema changes introduced.

## User Setup Required
None - no external service configuration required. (Same worktree-local note as 01-01/01-02: `.so`/`.pyi` symlinks may need recreating in a fresh worktree — see Deviation 2.)

## Next Phase Readiness
- `scripts.bybit_recorder.strategy` and `scripts.bybit_recorder.recorder` now provide the full walking-skeleton wiring: config -> TradingNode -> strategy validation/subscription/conversion timer.
- `RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"` is now defined as a shared module constant in `recorder.py` and referenced by `RecorderStrategyConfig.instance_id_str` — resolves the carried-forward note from 01-01/01-02 SUMMARY.
- Plan 04 can now exercise the empirical E2E smoke test (live Bybit connectivity, conversion roundtrip, Pitfall 2 double-conversion behavior) against this entrypoint.
- No blockers for Wave 3 follow-on (Plan 04).

---
*Phase: 01-bootstrap-config-end-to-end-slice*
*Completed: 2026-06-13*

## Self-Check: PASSED

All created files and commit hashes verified present on disk / in git log.
