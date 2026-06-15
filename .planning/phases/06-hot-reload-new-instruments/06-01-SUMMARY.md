---
phase: 06-hot-reload-new-instruments
plan: 01
subsystem: data
tags: [nautilus, bybit, hot-reload, config, recorder]

requires:
  - phase: 03-reliability-for-24-7-operation
    provides: heartbeat timer cadence + _last_seen stale-stream tracking reused by the config-reload timer and removal pruning
provides:
  - max_hot_added_instruments config knob (D-08), validated > 0, default 50
  - config-reload timer registered at heartbeat cadence (D-01/D-02)
  - _diff_config additions/removals/param-change detection
  - _unsubscribe_instrument removal branch (D-06, linear-only gating, _last_seen pruning)
  - _apply_param_change depth clean-swap (unsubscribe-before-subscribe, Pitfall 2) and bar-interval delta swap (D-05)
  - _config_signature for _failed_instrument_ids reset (D-07)
  - set_data_client setter + bookkeeping fields consumed by Plan 02
affects: [06-02-hot-reload-additions]

tech-stack:
  added: []
  patterns:
    - "config-reload timer reuses heartbeat_interval_seconds cadence"
    - "_on_config_reload wraps its whole body in try/except Exception: logger.exception(...); return (mirrors _run_conversion swallow)"

key-files:
  created:
    - tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py
  modified:
    - scripts/bybit_recorder/config.py
    - scripts/bybit_recorder/strategy.py
    - tests/unit_tests/persistence/recorder/test_recorder_config.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py

key-decisions:
  - "Additions are left as a marked placeholder in _on_config_reload; runtime instrument provider load is Plan 02 (Pattern 3)"
  - "_product_types dict tracks per-instrument product type for linear-only gating on removal, seeded in on_start alongside _subscribed_params"

patterns-established:
  - "Depth-swap order: unsubscribe_order_book_deltas before subscribe_order_book_deltas (Pitfall 2 — DataEngine dedups order-book subs by instrument_id only)"
  - "Removal prunes _last_seen for all 7 stream keys: trade/quote/deltas/bar/mark/index/funding (Pitfall 4)"

requirements-completed: [HOT-01]

duration: ~25min
completed: 2026-06-15
---

# Phase 6 Plan 1: Config-reload timer, diff engine, removal + param-swap branches

**Config-reload timer (heartbeat cadence) re-reads recorder.toml and applies removal/depth/bar-interval diffs live, with the addition path scaffolded for Plan 02**

## Performance

- **Tasks:** 3 completed
- **Files modified:** 4 modified, 1 created

## Accomplishments
- `max_hot_added_instruments` config knob (D-08): `PositiveInt = 50`, fail-fast `> 0` validation in `load_recorder_config`
- `config-reload` timer registered in `on_start` at `heartbeat_interval_seconds` cadence (D-01/D-02)
- `_diff_config` computes additions/removals/param-changes against `_subscribed_params`
- `_unsubscribe_instrument` (D-06): unsubscribes trade/quote/deltas/bars (+ mark/index/funding for linear), prunes all 7 `_last_seen` stream keys
- `_apply_param_change` (D-05): depth clean-swap via unsubscribe-then-subscribe (Pitfall 2 order preserved), bar-interval add/remove deltas only
- `_config_signature` resets `_failed_instrument_ids` on recorder.toml changes (D-07)
- `set_data_client` setter + bookkeeping fields (`_subscribed_params`, `_product_types`, `_failed_instrument_ids`, `_last_config_signature`, `_hot_added_count`, `_bybit_client`) scaffolded for Plan 02

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave-0 test scaffold + max_hot_added_instruments config knob (D-08)** - `77b225133c` (feat)
2. **Task 3: Unit tests for diff, removal, depth-swap order, bar-interval delta, threshold, linear gating** - `2e31658eb0` (test)
3. **Task 2: Reload timer + diff engine + REMOVAL and PARAM-CHANGE branches** - `60dab67ace` (feat)

_Note: Task 3's test scaffold (stubs) was implemented together with its full test bodies in a single commit; Task 2's implementation commit follows so the suite is green at HEAD._

## Files Created/Modified
- `scripts/bybit_recorder/config.py` - `max_hot_added_instruments` field + validation
- `scripts/bybit_recorder/strategy.py` - config-reload timer, diff engine, removal/param-swap branches, bookkeeping fields, `set_data_client`
- `tests/unit_tests/persistence/recorder/test_recorder_config.py` - knob validation + default tests
- `tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py` - new hot-reload test suite (10 tests: 7 implemented, 3 skipped pending Plan 02)
- `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` - `_build_strategy` extended with `reload_config_path`/`max_hot_added_instruments`/`data_client` kwargs

## Decisions Made
- Tracked product type per instrument in a parallel `_product_types: dict[InstrumentId, str]` (rather than overloading `_subscribed_params`'s tuple) for clarity in the linear-gating check on removal.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Initial executor run hit a session/usage limit mid-task without committing; work was verified against the plan's acceptance criteria (ruff clean, full recorder suite green: 58 passed, 3 skipped) and committed task-by-task in this session.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Plan 02 (Wave 2) can now implement the ADDITION branch using `set_data_client`/`_bybit_client`, `_hot_added_count`/`_check_hot_added_threshold`, and `_failed_instrument_ids` scaffolding already in place.
- `reload_config_path` wiring from `recorder.py` into `RecorderStrategyConfig` is still needed (Plan 02 scope).

---
*Phase: 06-hot-reload-new-instruments*
*Completed: 2026-06-15*
