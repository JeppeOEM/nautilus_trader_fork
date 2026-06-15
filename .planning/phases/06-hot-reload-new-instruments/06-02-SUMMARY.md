---
phase: 06-hot-reload-new-instruments
plan: 02
subsystem: data
tags: [nautilus, bybit, recorder, hot-reload, instrument-provider, parquet-catalog]

# Dependency graph
requires:
  - phase: 06-hot-reload-new-instruments (Plan 01)
    provides: config-reload timer, diff engine, RecorderConfig.max_hot_added_instruments, bookkeeping fields (_subscribed_params, _failed_instrument_ids, _hot_added_count, _bybit_client, _pending_loads placeholders)
provides:
  - "Live Bybit data client injected into RecorderStrategy after node.build() (Pattern 3 wiring)"
  - "_subscribe_instrument shared helper extracted from on_start (no behavior drift)"
  - "ADD branch: two-phase runtime instrument load (provider.load -> find -> cache.add_instrument -> _cache_instruments -> subscribe), D-07/D-11 failure tracking, D-08 count warning"
affects: [phase-06-validation, future-phases-touching-recorder-strategy-or-recorder-py]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-phase runtime instrument load: poll-N schedules provider.load(id) fire-and-forget via _pending_loads; poll-N+1 confirms via provider.find + cache.add_instrument + _cache_instruments, then subscribes"
    - "Shared _subscribe_instrument(instrument_id, depth, bar_intervals, is_linear) helper reused by on_start and the ADD branch — single source of truth for per-instrument subscribe calls"
    - "Per-id try/except in the ADD branch so one bad instrument id cannot abort the rest of the reload cycle"

key-files:
  created: []
  modified:
    - scripts/bybit_recorder/recorder.py
    - scripts/bybit_recorder/strategy.py
    - tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py
    - tests/unit_tests/persistence/recorder/conftest.py
    - .planning/phases/06-hot-reload-new-instruments/deferred-items.md

key-decisions:
  - "Live Bybit data client is injected into RecorderStrategy via node.kernel.data_engine._clients[ClientId(BYBIT)] after node.build() — there is no public DataEngine.get_client accessor (A2 confirmed)"
  - "request_instrument() is non-functional for Bybit (NotImplementedError, Pitfall 1); the two-phase provider.load/find fallback (Pattern 3 / D-09) is the verified runtime-load mechanism"
  - "Task 4 (live mainnet hot-add smoke) deferred per orchestrator/user decision — not executed in this session"

patterns-established:
  - "Pattern 3: runtime instrument load via instrument_provider.load(id) (fire-and-forget on the live loop) + provider.find(id) confirmation on a subsequent poll, then cache.add_instrument + client._cache_instruments() before subscribing"

requirements-completed: []  # HOT-01 NOT complete — Task 4 (live mainnet smoke, the only verification of the runtime-load path) is deferred, not executed.

# Metrics
duration: ~90min
completed: 2026-06-15
---

# Phase 06 Plan 02: Hot-Add Runtime Instrument Load Summary

**Implemented and unit-verified the two-phase runtime instrument-load path (Pattern 3 / D-09) for hot-adding new Bybit instruments via recorder.toml, with the live mainnet smoke (Task 4) deliberately deferred.**

## Performance

- **Duration:** ~90 min
- **Started:** 2026-06-15 (session start)
- **Completed:** 2026-06-15T17:49:45Z (Tasks 1-3; Task 4 deferred)
- **Tasks:** 3 of 4 completed (Task 4 deferred, not executed)
- **Files modified:** 5 (recorder.py, strategy.py, test_recorder_hot_reload.py, conftest.py, deferred-items.md)

## Status: Tasks 1-3 Complete, Task 4 Deferred

**Tasks 1-3 are implemented, committed, and unit-verified:**

- **Task 1** — `recorder.py` now constructs `RecorderStrategy` as a named local, builds the node, then injects the live Bybit data client via `node.kernel.data_engine._clients[ClientId(BYBIT)]` and calls `strategy.set_data_client(...)`. `reload_config_path` and `max_hot_added_instruments` are threaded into `RecorderStrategyConfig`.
- **Task 2** — The on_start per-instrument subscribe block (trade/quote/order-book-deltas/bars, plus linear-only mark/index/funding) was extracted verbatim into a new `_subscribe_instrument(instrument_id, depth, bar_intervals, is_linear)` helper. All existing on_start subscribe-spy tests pass unchanged, confirming no behavior drift.
- **Task 3** — The ADD branch of `_on_config_reload` now performs the two-phase runtime load: poll-N schedules `provider.load(id)` (fire-and-forget, tracked via `_pending_loads`); poll-N+1 confirms via `provider.find(id)`, calls `cache.add_instrument` + `client._cache_instruments()` (additive A1), increments `_hot_added_count` with the D-08 over-threshold WARNING, then reuses `_subscribe_instrument` from Task 2. Unknown ids are logged once as ERROR, added to `_failed_instrument_ids` (D-07/D-11), and not retried until the config signature changes. Per-id try/except prevents one bad id from aborting the cycle.

**Test results:** `uv run pytest tests/unit_tests/persistence/recorder/ -q` — **62 passed, 0 skipped**. All four targeted hot-reload tests (`test_addition_subscribes`, `test_failed_not_retried`, `test_failed_resets_on_change`, `test_threshold_warning`) pass, and the full on_start behavior-preservation suite (`test_recorder_strategy.py`) passes unchanged.

**Task 4 (live mainnet hot-add smoke) is DEFERRED — NOT executed in this session.** This is the only live verification of the runtime instrument-load path (Pattern 3) and A1 cache additivity against a real Bybit connection. Per orchestrator/user decision, it is intentionally not run now. See "Pending / Deferred" below for the exact steps required before HOT-01 can be marked complete.

## Task Commits

1. **Task 1: Inject live Bybit data client + thread reload_config_path/max_hot_added** - `e9317111bd` (feat)
2. **Task 2: Refactor on_start subscribe block into shared _subscribe_instrument helper** - `052603e97b` (refactor)
3. **Task 3: ADDITION branch — two-phase runtime load + D-07/D-11/D-08** - `4655c0cdb4` (feat)

**Task 4: Live mainnet hot-add smoke** — DEFERRED, not started, no commit.

_Note: Task 3's feat commit includes the new/filled unit tests for the ADDITION branch (addition_subscribes, failed_not_retried, failed_resets_on_change, threshold_warning) as part of a single atomic commit per the plan's TDD-flavored task structure._

## Files Created/Modified

- `scripts/bybit_recorder/recorder.py` - Constructs `RecorderStrategy` as a named local; injects the live Bybit data client via `node.kernel.data_engine._clients[ClientId(BYBIT)]` after `node.build()`; threads `reload_config_path` and `max_hot_added_instruments` into `RecorderStrategyConfig`; adds `ClientId` import
- `scripts/bybit_recorder/strategy.py` - Adds `_subscribe_instrument(instrument_id, depth, bar_intervals, is_linear)` shared helper (extracted from on_start); implements the ADD branch's two-phase runtime load (`_load_and_subscribe_addition` / equivalent), `_pending_loads` tracking, D-07/D-11 failure tracking, D-08 threshold WARNING
- `tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py` - Fills the three previously-skipped hot-reload tests (addition_subscribes, failed_not_retried, failed_resets_on_change) and strengthens the threshold_warning test to drive the full two-phase ADD-branch flow via a mocked Bybit data client
- `tests/unit_tests/persistence/recorder/conftest.py` - Extends `_build_strategy` to support injecting a mock data client with controllable `instrument_provider.find`/`load` and a no-op `_cache_instruments`
- `.planning/phases/06-hot-reload-new-instruments/deferred-items.md` - Logs two pre-existing, out-of-scope ruff issues (see Deviations below)

## Decisions Made

- Confirmed `node.kernel.data_engine._clients[ClientId(BYBIT)]` as the injection accessor (A2) — no public `DataEngine.get_client` exists; this stays entirely within `scripts/bybit_recorder/`, no `nautilus_trader/` edits.
- The two-phase poll-N / poll-N+1 load-then-confirm approach (RESEARCH Open Question 2, RESOLVED) was used for the ADD branch, since the reload timer callback is synchronous while `load_async` is a coroutine on the live loop.
- `_subscribe_instrument` is a pure subscribe-call helper with no bookkeeping side effects; the ADD branch owns `_subscribed_params`/`_hot_added_count`/`_pending_loads` bookkeeping around it, keeping the helper reusable and the refactor (Task 2) behavior-preserving.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Normalized isort grouping of the `scripts.bybit_recorder.config` import in recorder.py**
- **Found during:** Task 1
- **Issue:** The new `ClientId` import combined with existing imports left the `scripts.bybit_recorder.config` import in a non-canonical isort group, flagged by `ruff check`.
- **Fix:** Reordered the import per isort's grouping rules (first-party vs local).
- **Files modified:** `scripts/bybit_recorder/recorder.py`
- **Verification:** `uv run ruff check scripts/bybit_recorder/recorder.py` passes.
- **Committed in:** `e9317111bd` (Task 1 commit)

### Logged (not fixed) — out-of-scope pre-existing issues

**2. [Out-of-scope, logged to deferred-items.md] `scripts/bybit_recorder/inspect_catalog.py` — ruff D213 + I001**
- **Found during:** Task 3 (full `ruff check scripts/bybit_recorder/` surfaced it)
- Pre-existing in the Phase-2 ad-hoc smoke script, not edited by this plan, and self-documented as a throwaway script. Logged to `.planning/phases/06-hot-reload-new-instruments/deferred-items.md`, not fixed.

**3. [Out-of-scope, logged to deferred-items.md] `scripts/bybit_recorder/config.py:182` — ruff C901 (`load_recorder_config` complexity 11 > 10)**
- **Found during:** Task 3
- Pre-existing from Plan 01's `max_hot_added_instruments` validation branch, not edited by this plan. Targeted Task-3 verify gates and per-file ruff on this plan's changed files are clean. Logged to deferred-items.md as a candidate for a future refactor (extract per-section parsing/validation into helpers).

---

**Total deviations:** 1 auto-fixed (1 bug/import-order), 2 logged out-of-scope (not fixed)
**Impact on plan:** The isort fix was necessary for the Task-1 verify gate to pass and is purely cosmetic (import ordering). The two logged ruff issues are pre-existing, out of scope, and do not affect this plan's `<verification>` gates (which scope to this plan's changed files / targeted pytest selections). No scope creep.

## Issues Encountered

None beyond the deviations above.

## User Setup Required

None - no external service configuration required.

## Pending / Deferred

**Task 4 (Live mainnet hot-add smoke, `checkpoint:human-verify`, gate="blocking-human") was DEFERRED by explicit orchestrator/user decision and has NOT been executed.**

This is the only verification of:
- The runtime instrument-load path (Pattern 3 / D-09 fallback: `provider.load` -> `provider.find` -> `cache.add_instrument` -> `_cache_instruments`)
- D-10 (wait-until-confirmed-in-cache before subscribing)
- A1 (additive WS/HTTP instrument cache — existing instruments must keep parsing after a hot-add)
- D-07/D-11 (unknown-id single-attempt-per-snapshot failure handling) against real Bybit responses
- D-08 (over-threshold WARNING) in a live run

**HOT-01 cannot be marked complete until Task 4 is run and approved.** `requirements-completed: []` in this summary's frontmatter reflects this — REQUIREMENTS.md HOT-01 checkbox remains unchecked.

### How to run Task 4 (copied from the plan's `<how-to-verify>` block)

Run command:
```
uv run python scripts/bybit_recorder/recorder.py scripts/bybit_recorder/recorder.toml
```

Verification steps:

1. Ensure Bybit API keys are set in the environment (as used in prior phase smokes) and `recorder.toml` lists at least one LINEAR and one SPOT instrument.
2. Start the recorder against mainnet: `uv run python scripts/bybit_recorder/recorder.py scripts/bybit_recorder/recorder.toml`. Confirm in logs that existing instruments subscribe and the `config-reload` timer registers.
3. While it runs, EDIT `recorder.toml` to ADD one new linear instrument (e.g. a liquid USDT perp not already listed, with a valid depth from {1,50,200,1000} and `bar_intervals=["1-MINUTE"]`). Save.
4. Wait for up to two reload polls (~2x heartbeat_interval_seconds). Confirm in logs: a "Config reload: 1 added..." INFO, then the runtime load and the new instrument's subscribe calls firing on the following poll (two-phase). Confirm NO error/skip for the valid id.
5. Confirm A1 (additive cache): the PRE-EXISTING instruments keep producing trades/deltas in logs after the hot-add (no precision-parse failures).
6. Confirm data lands: after a conversion cycle (or inspect the streaming feather under `{streaming_path}/live/{instance_id}/`), the new instrument's feeds are present.
7. (Optional but recommended) Edit `recorder.toml` to ADD a bogus id (e.g. `FAKEFAKE-LINEAR.BYBIT`); confirm a single ERROR + skip and that it is NOT re-attempted on subsequent polls (D-07/D-11), and that valid instruments are unaffected.
8. (Optional) REMOVE an instrument and confirm its feeds unsubscribe and stale-stream WARNINGs do NOT appear for it afterward (D-06/Pitfall 4); change a depth and confirm the clean-swap (D-05).

**Resume signal (from the plan):** Type "approved" if the new instrument's feeds land in the catalog and existing instruments keep recording; otherwise describe what failed (e.g. load never resolved, existing instruments stopped parsing -> A1 violated, error on every poll -> request_instrument path leaked in).

## Next Phase Readiness

- Tasks 1-3 are complete, committed, and unit-verified (62 passed, 0 skipped in the full recorder suite; on_start behavior preservation confirmed).
- **Phase 6 CANNOT be marked fully complete until Task 4 (live mainnet hot-add smoke) is run and approved.** HOT-01 remains incomplete pending this live verification.
- No edits under `nautilus_trader/` — `git diff --name-only` for this plan's commits shows only `scripts/bybit_recorder/*`, `tests/unit_tests/persistence/recorder/*`, and `.planning/phases/06-hot-reload-new-instruments/deferred-items.md`.

---
*Phase: 06-hot-reload-new-instruments*
*Completed: 2026-06-15 (Tasks 1-3 only; Task 4 deferred)*
