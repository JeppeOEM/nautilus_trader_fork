---
phase: quick
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - scripts/bybit_recorder/strategy.py
  - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
autonomous: true
requirements: [WR-02]
must_haves:
  truths:
    - "A failure in ParquetDataCatalog construction inside _log_restart_gaps is logged and swallowed, not propagated"
    - "on_start() proceeds to subscriptions even when catalog construction fails during the restart-gap check"
    - "A unit test proves _log_restart_gaps swallows a catalog construction error and the per-instrument loop is not reached"
  artifacts:
    - path: "scripts/bybit_recorder/strategy.py"
      provides: "Guarded ParquetDataCatalog construction in _log_restart_gaps"
      contains: "Failed to open catalog for restart-gap check"
    - path: "tests/unit_tests/persistence/recorder/test_recorder_strategy.py"
      provides: "Unit test for the new guard"
      contains: "_log_restart_gaps"
  key_links:
    - from: "scripts/bybit_recorder/strategy.py:_log_restart_gaps"
      to: "logger.exception"
      via: "try/except around ParquetDataCatalog(self.config.catalog_path)"
      pattern: "logger\\.exception\\(.Failed to open catalog for restart-gap check"
---

<objective>
Fix WR-02 from 03-REVIEW.md: guard the top-level `ParquetDataCatalog`
construction in `_log_restart_gaps` (scripts/bybit_recorder/strategy.py:227)
with a try/except, mirroring the existing guard in `_run_conversion` (lines
452-460). Without this guard, a transient I/O / fsspec error in
`ParquetDataCatalog.__init__` propagates out of `_log_restart_gaps()` and then
out of `on_start()`, aborting before any subscriptions are made — violating the
"a failure here is logged but must not prevent subscriptions" guarantee
documented in `on_start` (lines 173-175) and the docstring (lines 223-225).

Purpose: Restore the documented startup robustness guarantee so a catalog-open
failure degrades gracefully (warning logged, gaps skipped) instead of aborting
the recorder before it subscribes to any data.
Output: Guarded construction in `_log_restart_gaps` + a unit test proving the
swallow behavior.
</objective>

<execution_context>
@/home/mrqdt/code/nautilus_trader_fork/.claude/gsd-core/workflows/execute-plan.md
@/home/mrqdt/code/nautilus_trader_fork/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/phases/03-reliability-for-24-7-operation/03-REVIEW.md
@scripts/bybit_recorder/strategy.py
@tests/unit_tests/persistence/recorder/test_recorder_strategy.py

# CLAUDE.md constraint: this is a NautilusTrader fork. NEVER modify anything
# under nautilus_trader/. All changes are confined to scripts/bybit_recorder/
# and its tests. Prefer mirroring the existing _run_conversion guard pattern
# verbatim over inventing a new one.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Guard _log_restart_gaps catalog construction</name>
  <files>scripts/bybit_recorder/strategy.py</files>
  <behavior>
    - When ParquetDataCatalog construction raises, _log_restart_gaps logs via logger.exception("Failed to open catalog for restart-gap check") and returns early without entering the per-instrument loop.
    - When construction succeeds, the per-instrument gap-check loop runs exactly as before (no behavior change on the happy path).
    - A failure during construction does not propagate out of on_start().
  </behavior>
  <action>
    In `_log_restart_gaps` (scripts/bybit_recorder/strategy.py, around line 227),
    wrap the `catalog = ParquetDataCatalog(self.config.catalog_path)` line in a
    try/except Exception, mirroring `_run_conversion`'s existing guard (lines
    452-460). On exception, call `logger.exception("Failed to open catalog for
    restart-gap check")` and `return` early (before reading `now_ns` and before
    the per-instrument loop). Keep `now_ns = self.clock.timestamp_ns()` and the
    existing per-instrument loop unchanged, after the guard. Add a brief WHY
    comment consistent with the codebase style (explain WHY, not WHAT) noting
    that opening the catalog touches the filesystem and a transient I/O error
    must not propagate out of on_start() and prevent subscriptions (per WR-02 /
    the docstring guarantee). Do NOT alter the existing per-instrument try/except
    body. Do NOT modify anything under nautilus_trader/.
  </action>
  <verify>
    <automated>cd /home/mrqdt/code/nautilus_trader_fork &amp;&amp; grep -n "Failed to open catalog for restart-gap check" scripts/bybit_recorder/strategy.py</automated>
  </verify>
  <done>The `ParquetDataCatalog(self.config.catalog_path)` construction in `_log_restart_gaps` is inside a try/except Exception that logs `logger.exception("Failed to open catalog for restart-gap check")` and returns early; the per-instrument loop body is unchanged.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Unit test that _log_restart_gaps swallows construction error</name>
  <files>tests/unit_tests/persistence/recorder/test_recorder_strategy.py</files>
  <behavior>
    - Test: patch scripts.bybit_recorder.strategy.ParquetDataCatalog with side_effect=RuntimeError("boom"); calling strategy._log_restart_gaps() does NOT raise.
    - Assert the per-instrument loop is not reached: build the strategy with a non-empty instrument_ids list, and confirm no warning records are emitted (the early return means no "Resuming after gap" / per-instrument warnings).
  </behavior>
  <action>
    Add a test `test_log_restart_gaps_swallows_catalog_construction_error(mocker,
    mock_cache, caplog)` mirroring `test_on_stop_swallows_catalog_construction_error`
    (lines 493-510). Build the strategy via `_build_strategy(mocker, mock_cache,
    [<one instrument id>])` using an existing instrument id constant already used
    in this file (e.g. INSTRUMENT_ID_LINEAR — confirm the exact name in the file's
    imports/constants). Patch `scripts.bybit_recorder.strategy.ParquetDataCatalog`
    with `side_effect=RuntimeError("boom")`. Act: call `strategy._log_restart_gaps()`
    and assert it does not raise. Assert the early-return path was taken — capture
    logs with `caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy")`
    and assert no WARNING-level "Resuming after gap" / per-instrument warning
    records were emitted (i.e. the per-instrument loop was skipped). Follow the
    file's AAA + descriptive-name conventions. Do NOT modify nautilus_trader/.
  </action>
  <verify>
    <automated>cd /home/mrqdt/code/nautilus_trader_fork &amp;&amp; uv run pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k "log_restart_gaps_swallows_catalog_construction_error or on_stop_swallows_catalog_construction_error" -q</automated>
  </verify>
  <done>The new test passes, proving `_log_restart_gaps()` does not raise when `ParquetDataCatalog` construction fails and the per-instrument loop is not reached.</done>
</task>

</tasks>

<verification>
- `_log_restart_gaps` catalog construction is guarded, matching `_run_conversion`'s pattern.
- Full recorder strategy test module passes:
  `cd /home/mrqdt/code/nautilus_trader_fork && uv run pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -q`
- No files under `nautilus_trader/` were modified.
</verification>

<success_criteria>
- WR-02 closed: `ParquetDataCatalog(self.config.catalog_path)` in `_log_restart_gaps` is wrapped in try/except that logs `logger.exception("Failed to open catalog for restart-gap check")` and returns early.
- New unit test passes and proves the swallow + early-return behavior.
- All existing tests in `test_recorder_strategy.py` still pass.
- Changes confined to `scripts/bybit_recorder/strategy.py` and `tests/unit_tests/persistence/recorder/test_recorder_strategy.py`.
</success_criteria>

<output>
Create `.planning/quick/260615-acs-fix-wr-02-03-review-md-guard-log-restart/260615-acs-SUMMARY.md` when done
</output>
