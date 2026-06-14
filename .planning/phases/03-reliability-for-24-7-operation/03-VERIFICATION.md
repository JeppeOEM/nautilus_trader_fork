---
phase: 03-reliability-for-24-7-operation
verified: 2026-06-14T00:00:00Z
status: gaps_found
score: 6/7 must-haves verified
overrides_applied: 0
gaps:
  - truth: "On SIGTERM the recorder flushes/converts buffered data before exiting with no risk of faulting on_stop (REL-02)"
    status: failed
    reason: "_run_conversion() (the shared body for on_stop AND the periodic timer) has an unguarded ParquetDataCatalog(...) construction and an unguarded self._funding_writer.flush() call OUTSIDE the per-type try/except. If either raises during shutdown (e.g. transient I/O error on the funding feather file, or catalog-open failure), the exception propagates out of on_stop(), which Strategy._stop() re-raises, faulting the component during the orderly SIGTERM sequence this phase exists to make reliable (CR-01 from 03-REVIEW.md, confirmed present in code at strategy.py:452-459)."
    artifacts:
      - path: "scripts/bybit_recorder/strategy.py"
        issue: "_run_conversion (lines 452-471): `catalog = ParquetDataCatalog(self.config.catalog_path)` and `if self._funding_writer is not None: self._funding_writer.flush()` are not wrapped in try/except, unlike the per-type conversion loop below them"
    missing:
      - "Wrap the ParquetDataCatalog(...) construction in try/except, logging and returning early on failure"
      - "Wrap self._funding_writer.flush() in try/except, logging and continuing on failure"
---

# Phase 3: Reliability for 24/7 Operation Verification Report

**Phase Goal:** The recorder survives unattended `Restart=always` operation: it flushes buffered data on SIGTERM so restarts lose no recent data, leans entirely on the adapter's built-in reconnect/resubscribe, and surfaces stale streams via heartbeat logging.
**Verified:** 2026-06-14
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | On SIGTERM the recorder runs a final flush + `_run_conversion()` before the kernel closes its writer (REL-02) | ✓ VERIFIED | `on_stop()` (strategy.py:480-503) delegates to `_run_conversion()`; unit tests `test_on_stop_runs_final_conversion_per_type`, `test_on_stop_flushes_funding_writer`, `test_on_stop_swallows_per_type_conversion_error` all pass |
| 2 | A restart-shaped scenario (two writer instances, same instance_id) converts the first process's finalized file with no gap/overlap/non-disjoint-interval error (REL-02) | ✓ VERIFIED | `test_restart_shaped_conversion_converts_finalized_file_without_raise` (test_recorder_conversion.py:358) exists and passes; `parquet.py` unmodified per `git diff` |
| 3 | on_start logs a WARNING when the gap since the last recorded `ts_init` exceeds a configured threshold (REL-02 gap visibility, D-06) | ✓ VERIFIED | `_log_restart_gaps()` (strategy.py:212-249) implemented, called from `on_start`; unit tests `test_on_start_logs_warning_for_restart_gap_exceeding_threshold` and `test_on_start_does_not_warn_when_gap_within_threshold_or_no_prior_data` pass; live-verified in Session 3 (03-01-SUMMARY.md) producing the exact `"Resuming after gap of 160.7s for ..."` WARNING |
| 4 | The recorder contains zero reconnect/resubscribe code — reconnect is entirely adapter-driven (REL-04) | ✓ VERIFIED | `grep -rniE "reconnect\|resubscribe" scripts/bybit_recorder/` (excluding comments) returns empty |
| 5 | After a forced WebSocket disconnect the recorder resumes recording automatically with no custom code (REL-04) | ✓ VERIFIED (via structural proof + accepted deviation) | The live forced-disconnect step (Task 4, step 4) was explicitly skipped per user decision as "too risky in this shared sandbox"; substituted with the structural no-reconnect-code grep (Truth 4) as the proof that any resume must be adapter-owned. This is documented as an accepted deviation in 03-01-SUMMARY.md Checkpoint Status, not a silent gap. |
| 6 | The recorder logs per-stream heartbeats and a WARNING when a stream goes quiet beyond its threshold (REL-03) | ✓ VERIFIED | `_heartbeat`/`_last_seen`/`_stale_threshold_s` implemented (strategy.py:251-395); 7 `_last_seen[...]` assignments across all on_* handlers including funding-before-dedup; `test_on_start_sets_heartbeat_timer`, `test_on_trade_tick_updates_last_seen`, `test_on_funding_rate_updates_last_seen_before_dedup`, `test_heartbeat_warns_when_stream_stale`, `test_heartbeat_no_warn_when_stream_fresh` all pass |
| 7 | on_stop / _run_conversion cannot itself fault the component during graceful shutdown (REL-02 robustness) | ✗ FAILED | CR-01 (03-REVIEW.md): `_run_conversion()`'s `ParquetDataCatalog(...)` construction and `self._funding_writer.flush()` call are unguarded, outside the per-type try/except — an exception here propagates out of `on_stop()` and faults the component on SIGTERM, undermining the reliability guarantee this phase delivers |

**Score:** 6/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `scripts/bybit_recorder/strategy.py` | `on_stop`, `_run_conversion`, `_log_restart_gaps`, `_last_seen`, `_heartbeat`, `_stale_threshold_s` | ✓ VERIFIED (with CR-01 caveat) | All symbols present and wired; `_run_conversion` has an unguarded section (CR-01) |
| `scripts/bybit_recorder/config.py` | `restart_gap_threshold_seconds`, `heartbeat_interval_seconds`, `stale_threshold_seconds`, `stale_threshold_default_seconds` + validation | ⚠️ PARTIAL | All fields present and threaded through; `heartbeat_interval_seconds`/`stale_threshold_*` validated `> 0` at load (lines 264-279), but `restart_gap_threshold_seconds` (line 287) has NO validation (WR-01 from 03-REVIEW.md) — inconsistent with the V5 fail-fast pattern applied to its siblings |
| `scripts/bybit_recorder/recorder.py` | thread new config fields into `RecorderStrategyConfig(...)` | ✓ VERIFIED | grep confirms `heartbeat_interval_seconds`, `restart_gap_threshold_seconds`, `stale_threshold_seconds` all pass through |
| `scripts/bybit_recorder/recorder.toml` | new config keys present, valid TOML | ✓ VERIFIED | `heartbeat_interval_seconds`, `stale_threshold_default_seconds`, `[recorder.stale_threshold_seconds]` sub-table present; `python -c "import tomllib; tomllib.load(...)"` succeeds |
| `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` | on_stop, gap-log, heartbeat, config-validation tests | ✓ VERIFIED | All target test functions present and passing |
| `tests/unit_tests/persistence/recorder/test_recorder_conversion.py` | restart-shaped two-writer test | ✓ VERIFIED | `test_restart_shaped_conversion_converts_finalized_file_without_raise` present and passing |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `strategy.py on_stop` | `strategy.py _convert_finalized_feather_files` | shared `_run_conversion()` loop | ✓ WIRED | `on_stop()` body is `self._run_conversion()`; loop calls `_convert_finalized_feather_files` once per type |
| `strategy.py _convert_stream` | `strategy.py _run_conversion` | timer callback delegates to shared body | ✓ WIRED | `_convert_stream(event)` body is `self._run_conversion()` |
| `strategy.py on_start` | `strategy.py _log_restart_gaps` | startup gap-log check (D-06) | ✓ WIRED | called at on_start line 175, before subscription loop |
| `strategy.py on_start` | `self.clock.set_timer name="heartbeat"` | second native timer | ✓ WIRED | `set_timer(name="heartbeat", ...)` present at line 206-210 |
| `strategy.py _heartbeat` | `self._last_seen / self._stale_threshold_s` | idle-time comparison | ✓ WIRED | `_heartbeat` iterates `_last_seen.items()`, compares against `_stale_threshold_s(stream)` |
| `recorder.py` | `RecorderStrategyConfig` heartbeat/threshold fields | config pass-through | ✓ WIRED | grep confirms all three new fields threaded |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full recorder unit test suite | `python -m pytest tests/unit_tests/persistence/recorder/ -q` | `44 passed in 0.41s` | ✓ PASS |
| REL-04 no-reconnect-code structural proof | `grep -rniE "reconnect\|resubscribe" scripts/bybit_recorder/ \| grep -vE "...#"` | empty | ✓ PASS |
| No debt markers in modified files | `grep -n "TBD\|FIXME\|XXX\|TODO\|HACK\|PLACEHOLDER"` over strategy.py/config.py/recorder.py/recorder.toml | empty | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| REL-02 | 03-01 | On shutdown (SIGTERM), the recorder flushes/converts buffered data before exiting, so a restart does not lose recent data | ⚠️ PARTIAL | Core mechanism (`on_stop` → `_run_conversion` → `_convert_finalized_feather_files`) is implemented, unit-tested, and live-verified (Sessions 1-3). However CR-01 means `_run_conversion`'s catalog-open / funding-flush can itself raise unguarded and fault `on_stop()`, which is a direct robustness gap in the "survives SIGTERM" guarantee this requirement makes. REQUIREMENTS.md still marks REL-02 as "Pending" (line 27, unchecked) |
| REL-03 | 03-02 | Recorder logs per-stream heartbeats and warns if any subscribed stream goes quiet beyond an expected threshold | ✓ SATISFIED | `_heartbeat`/`_last_seen`/`_stale_threshold_s` fully implemented, tested, and config-validated. REQUIREMENTS.md marks REL-03 "Complete" (line 28/84) — consistent with code |
| REL-04 | 03-01 | Recorder relies entirely on the Bybit adapter's built-in WebSocket reconnect/resubscribe — no custom reconnect logic | ✓ SATISFIED | Structural grep proof empty; live forced-disconnect step substituted with this structural proof per documented user decision. REQUIREMENTS.md still marks REL-04 as "Pending" (line 29, unchecked) — this appears to be a documentation-sync gap, not a code gap |

**Note:** REQUIREMENTS.md (lines 27-29, 83-85) shows REL-02 and REL-04 as unchecked/"Pending" while REL-03 is "Complete". The code-level evidence for REL-04 (Truths 4 and 5) is solid. REL-02's code-level shortfall (CR-01) is a legitimate reason for it to remain "Pending" until fixed.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `scripts/bybit_recorder/strategy.py` | 452-459 | Unguarded `ParquetDataCatalog(...)` construction and `self._funding_writer.flush()` outside the per-type try/except in `_run_conversion()` (CR-01) | 🛑 BLOCKER | An exception here propagates out of `on_stop()`, faulting the component during SIGTERM shutdown — directly undermines REL-02's "survives unattended Restart=always" goal |
| `scripts/bybit_recorder/config.py` | 287 | `restart_gap_threshold_seconds` read from TOML with no fail-fast positive-value validation (WR-01), inconsistent with sibling `heartbeat_interval_seconds`/`stale_threshold_*` checks at lines 264-279 | ⚠️ WARNING | A `0` or negative value silently passes through and would cause the gap-log WARNING to fire on every restart (opposite of intended "reduce noise" behavior) |

### Human Verification Required

None — all remaining items are code-fixable and verifiable by re-running the existing unit test suite plus targeted new tests for the CR-01/WR-01 fixes.

### Gaps Summary

The phase delivers the large majority of its goal: `on_stop()` final flush+convert is implemented and live-verified across 3 mainnet sessions with no data loss and no non-disjoint-interval errors (REL-02 core mechanism), the restart-gap WARNING (D-06) fires correctly, REL-04's "zero reconnect code" structural guarantee holds, and REL-03's heartbeat/stale-stream detection is fully implemented and tested (44/44 tests passing).

However, the code review (03-REVIEW.md) identified CR-01: the shared `_run_conversion()` body — which `on_stop()` depends on for its graceful-shutdown guarantee — has two statements (catalog construction and funding-writer flush) that sit OUTSIDE the per-type try/except protection that the rest of the function relies on. If either of these raises during a SIGTERM-triggered `on_stop()`, the exception propagates, `Strategy._stop()` re-raises it, and the component faults during the very shutdown sequence this phase exists to make reliable. This is a direct, observable gap against the phase goal ("survives unattended Restart=always operation ... flushes buffered data on SIGTERM").

A secondary issue (WR-01) is that `restart_gap_threshold_seconds` lacks the same fail-fast positive-value validation applied to its sibling REL-03 threshold fields, allowing a misconfigured `0`/negative value to silently invert the intended "reduce restart-gap-warning noise" behavior. This is a config-robustness gap, not a functional blocker, but is grouped with CR-01 as both stem from the same "swallow/validate consistently" principle that the rest of `_run_conversion`/config validation already follows.

Both issues are localized, small fixes (wrap two statements in try/except; add one `if <= 0: raise ValueError` check) that do not require new architecture — they bring `_run_conversion()` and `restart_gap_threshold_seconds` validation up to the same robustness standard already established elsewhere in this same phase's code.

---

_Verified: 2026-06-14_
_Verifier: Claude (gsd-verifier)_
