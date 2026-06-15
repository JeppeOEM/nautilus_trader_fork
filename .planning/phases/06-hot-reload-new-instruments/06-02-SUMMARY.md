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
  - "Task 4 (live mainnet hot-add smoke) executed 2026-06-15 in a follow-up session against real Bybit mainnet, WITHOUT BYBIT_API_KEY/BYBIT_API_SECRET set — public market-data endpoints require no credentials for this read-only recorder. PASSED."

patterns-established:
  - "Pattern 3: runtime instrument load via instrument_provider.load(id) (fire-and-forget on the live loop) + provider.find(id) confirmation on a subsequent poll, then cache.add_instrument + client._cache_instruments() before subscribing"

requirements-completed: [HOT-01]

# Metrics
duration: ~90min
completed: 2026-06-15
---

# Phase 06 Plan 02: Hot-Add Runtime Instrument Load Summary

**Implemented and unit-verified the two-phase runtime instrument-load path (Pattern 3 / D-09) for hot-adding new Bybit instruments via recorder.toml, then validated it end-to-end with a live mainnet hot-add smoke (Task 4).**

## Performance

- **Duration:** ~90 min (Tasks 1-3) + ~10 min (Task 4, follow-up session)
- **Started:** 2026-06-15 (session start)
- **Completed:** 2026-06-15 (Tasks 1-3 at 17:49:45Z; Task 4 live smoke completed ~19:05Z in a follow-up session)
- **Tasks:** 4 of 4 completed
- **Files modified:** 5 (recorder.py, strategy.py, test_recorder_hot_reload.py, conftest.py, deferred-items.md)

## Status: All Tasks Complete, HOT-01 Verified

**Tasks 1-3 are implemented, committed, and unit-verified:**

- **Task 1** — `recorder.py` now constructs `RecorderStrategy` as a named local, builds the node, then injects the live Bybit data client via `node.kernel.data_engine._clients[ClientId(BYBIT)]` and calls `strategy.set_data_client(...)`. `reload_config_path` and `max_hot_added_instruments` are threaded into `RecorderStrategyConfig`.
- **Task 2** — The on_start per-instrument subscribe block (trade/quote/order-book-deltas/bars, plus linear-only mark/index/funding) was extracted verbatim into a new `_subscribe_instrument(instrument_id, depth, bar_intervals, is_linear)` helper. All existing on_start subscribe-spy tests pass unchanged, confirming no behavior drift.
- **Task 3** — The ADD branch of `_on_config_reload` now performs the two-phase runtime load: poll-N schedules `provider.load(id)` (fire-and-forget, tracked via `_pending_loads`); poll-N+1 confirms via `provider.find(id)`, calls `cache.add_instrument` + `client._cache_instruments()` (additive A1), increments `_hot_added_count` with the D-08 over-threshold WARNING, then reuses `_subscribe_instrument` from Task 2. Unknown ids are logged once as ERROR, added to `_failed_instrument_ids` (D-07/D-11), and not retried until the config signature changes. Per-id try/except prevents one bad id from aborting the cycle.

**Test results:** `uv run pytest tests/unit_tests/persistence/recorder/ -q` — **62 passed, 0 skipped**. All four targeted hot-reload tests (`test_addition_subscribes`, `test_failed_not_retried`, `test_failed_resets_on_change`, `test_threshold_warning`) pass, and the full on_start behavior-preservation suite (`test_recorder_strategy.py`) passes unchanged.

**Task 4 (live mainnet hot-add smoke) PASSED in a follow-up session on 2026-06-15.** The recorder was run against real Bybit mainnet (`environment = "mainnet"`, no `BYBIT_API_KEY`/`BYBIT_API_SECRET` set — public market-data endpoints require no credentials for this read-only recorder). With `BTCUSDT-LINEAR.BYBIT` and `ETHUSDT-SPOT.BYBIT` already subscribed and recording, `recorder.toml` was edited live to add `SOLUSDT-LINEAR.BYBIT` (depth=50, bar_intervals=["1-MINUTE"]). On the very next `config-reload` poll (~80s after startup, ~75s after the edit — within the expected two-poll window for `heartbeat_interval_seconds=30`), the two-phase load completed: `provider.load`/`find` resolved the new instrument, `cache.add_instrument` + `_cache_instruments()` ran, and `_subscribe_instrument` fired all 7 `Subscribe*` commands (trades, quotes, order book deltas, 1-minute bars, mark price, index price, funding rate) for `SOLUSDT-LINEAR.BYBIT`. `StreamingFeatherWriter` created writers for all 7 feeds, and their feather files grew with real data over the following ~70s (e.g. trade_tick 67KB → 142KB). Meanwhile `BTCUSDT-LINEAR.BYBIT` and `ETHUSDT-SPOT.BYBIT` continued recording uninterrupted — their trade_tick files also grew over the same window (A1 additivity confirmed). On SIGTERM, the strategy shut down cleanly (STOPPING -> STOPPED -> DISPOSED) and `_run_conversion` ran its existing-file conversion pass without error; `SOLUSDT-LINEAR.BYBIT`'s feather files (created mid-run, still the active writer files at shutdown) were left for conversion on the next cycle — consistent with the existing Phase-3 "one-cycle visibility delay, never data loss" design (D-02/D-03), not a new gap. No errors/exceptions appeared in the log during the run.

One minor observability note (non-blocking): the `logger.info("Config reload: %d added, %d removed, %d changed", ...)` line (Python stdlib `logging`, not the Nautilus `Logger`) did not appear in captured output, because the root logger has no INFO-level handler configured by the recorder (only WARNING+ surfaces via Python's lastResort handler — the "Resuming after gap" WARNING and "Stale stream" WARNINGs did appear). The hot-add behavior itself was fully confirmed via the Nautilus-logged `[CMD]--> SubscribeXxx(...)` commands and the new streaming writer/file creation, so this does not affect HOT-01's functional correctness.

## Task Commits

1. **Task 1: Inject live Bybit data client + thread reload_config_path/max_hot_added** - `e9317111bd` (feat)
2. **Task 2: Refactor on_start subscribe block into shared _subscribe_instrument helper** - `052603e97b` (refactor)
3. **Task 3: ADDITION branch — two-phase runtime load + D-07/D-11/D-08** - `4655c0cdb4` (feat)
4. **Task 4: Live mainnet hot-add smoke** — PASSED 2026-06-15 (follow-up session); no code changes, tracking-only commit.

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

## Live Mainnet Smoke Results (Task 4)

**Task 4 (Live mainnet hot-add smoke, `checkpoint:human-verify`, gate="blocking-human") was executed and PASSED on 2026-06-15 in a follow-up session.**

Verified:
- The runtime instrument-load path (Pattern 3 / D-09 fallback: `provider.load` -> `provider.find` -> `cache.add_instrument` -> `_cache_instruments`) — confirmed via successful subscribe of `SOLUSDT-LINEAR.BYBIT` on the first reload poll after the toml edit
- D-10 (wait-until-confirmed-in-cache before subscribing) — subscription only occurred after the load/find/cache_instruments sequence completed
- A1 (additive WS/HTTP instrument cache) — pre-existing `BTCUSDT-LINEAR.BYBIT` and `ETHUSDT-SPOT.BYBIT` continued recording (trade_tick files grew) throughout and after the hot-add
- All 7 feed types for the hot-added instrument (trades, quotes, order book deltas, 1-minute bars, mark/index/funding) subscribed and streaming writers created, with growing feather files confirming live data capture
- Clean shutdown (STOPPING -> STOPPED -> DISPOSED, conversion pass ran without error)
- **No Bybit API credentials required** — public market-data WS/REST endpoints work with `api_key=None`/`api_secret=None`

Not exercised (optional steps from the plan, not required for HOT-01 — D-07/D-11/D-06/D-05 already unit-verified in Tasks 1-3 with 62/62 passing):
- Bogus-id failure path (D-07/D-11) against real Bybit — unit-tested only
- Removal / depth-change clean-swap (D-06/D-05) — unit-tested only

**HOT-01 is now marked complete in REQUIREMENTS.md.** `requirements-completed: [HOT-01]` in this summary's frontmatter reflects this.

## Next Phase Readiness

- All 4 tasks complete, committed, and verified: Tasks 1-3 via 62/62 unit tests; Task 4 via live mainnet smoke (above).
- **Phase 6 is fully complete.** HOT-01 marked complete in REQUIREMENTS.md.
- No edits under `nautilus_trader/` — `git diff --name-only` for this plan's commits shows only `scripts/bybit_recorder/*`, `tests/unit_tests/persistence/recorder/*`, and `.planning/phases/06-hot-reload-new-instruments/*`.
- `recorder.toml` was temporarily edited to add `SOLUSDT-LINEAR.BYBIT` for the smoke test and reverted back to its original `BTCUSDT-LINEAR.BYBIT` + `ETHUSDT-SPOT.BYBIT` configuration afterward.

---
*Phase: 06-hot-reload-new-instruments*
*Completed: 2026-06-15 (all 4 tasks; HOT-01 verified via live mainnet smoke)*
