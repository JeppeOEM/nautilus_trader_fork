---
phase: 02-full-data-type-coverage
plan: 01
subsystem: bybit-recorder
tags: [recorder, streaming, catalog, market-data, tdd]
requires:
  - Phase 1 recorder (config.py / strategy.py / recorder.py + StreamingConfig pipeline)
provides:
  - Widened include_types covering six auto-written data types (REC-02..REC-04, REC-06)
  - Per-product-type order-book depth fail-fast validation (D-03 literal spot; linear discrete-set defense-in-depth)
  - Linear-only mark/index price subscription gating (D-04)
  - Per-type catalog conversion loop (_RECORDED_TYPES)
affects:
  - scripts/bybit_recorder/config.py
  - scripts/bybit_recorder/strategy.py
  - scripts/bybit_recorder/recorder.py
tech-stack:
  added: []
  patterns:
    - "Per-instrument subscription loop (trades/quotes/order-book/bars) + separate linear-only mark/index loop"
    - "BarType.from_str(f'{id}-{interval}-LAST-EXTERNAL') for venue-native kline subscription (D-02)"
    - "Per-type try/except conversion loop so one type's transient error does not block others"
    - "Fail-fast ValueError at config-load before any subscription is issued (T-2-01 mitigation)"
key-files:
  created: []
  modified:
    - scripts/bybit_recorder/config.py
    - scripts/bybit_recorder/strategy.py
    - scripts/bybit_recorder/recorder.py
    - tests/unit_tests/persistence/recorder/conftest.py
    - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
    - tests/unit_tests/persistence/recorder/test_recorder_config.py
decisions:
  - "D-03 honored literally for spot (depth > 50 raises); linear adds discrete-set {1,50,200,1000} defense-in-depth"
  - "FundingRateUpdate intentionally excluded from include_types — deduped in Plan 02 (D-01)"
  - "Mark/index subscriptions iterate linear_instrument_ids only (D-04); never the full list (Pitfall 4)"
metrics:
  duration: ~25m (recovered/continued session)
  completed: 2026-06-14
---

# Phase 2 Plan 01: Full Data-Type Coverage (Quotes, Order-Book, Bars, Mark/Index) Summary

Widened the Bybit recorder from trades-only to record five additional auto-written data types — quote ticks, order-book deltas at configured L2_MBP depth, venue-native bars per interval, and mark + index prices for linear instruments — all flowing through the proven Phase 1 `StreamingConfig` → `StreamingFeatherWriter` → `ParquetDataCatalog` pipeline, with fail-fast per-product-type order-book depth validation at config load.

## What Was Built

- **config.py (Task 2):** `build_streaming_config` now sets `include_types=[TradeTick, QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate]` (funding deliberately omitted). Added `product_type` field to `InstrumentEntry`, a `linear_instrument_ids` property on `RecorderConfig`, module constants `_SPOT_MAX_DEPTH = 50` / `_LINEAR_VALID_DEPTHS = {1, 50, 200, 1000}`, and per-product-type fail-fast depth validation in `load_recorder_config` (spot > 50 raises per D-03 literal; linear off the discrete set raises as defense-in-depth) — both before any subscription is issued.
- **strategy.py (Task 3):** Added `_RECORDED_TYPES` constant; extended `RecorderStrategyConfig` with `linear_instrument_ids`, `instrument_depths`, `instrument_bar_intervals`. `on_start` now subscribes quotes / order-book (`book_type=BookType.L2_MBP`, configured depth) / bars (`BarType.from_str(...-LAST-EXTERNAL)`) per instrument, plus mark/index prices for linear instruments only. `_convert_stream` rewritten to loop over `_RECORDED_TYPES` with per-iteration `try/except`. Added passive debug handlers for the five new types.
- **recorder.py (Task 3):** Wired the new strategy config fields (`linear_instrument_ids`, `instrument_depths`, `instrument_bar_intervals`) from the parsed `InstrumentEntry` list, keeping the live entrypoint functional.
- **tests (Task 1, pre-existing RED + Task 2/3 GREEN):** Five conversion round-trip tests, subscription/BarType/linear-only strategy tests, and per-product-type depth tests now all pass.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Wave 0 test scaffolding (RED) | c1c9efef61 (pre-existing) | conftest.py, test_recorder_conversion.py, test_recorder_strategy.py |
| 2 | Widen include_types + per-product-type depth validation | cba6569d68 | config.py, test_recorder_config.py |
| 3 | Subscriptions + per-type conversion loop | 8f4ea1d090 | strategy.py, recorder.py, test_recorder_config.py (format) |

## Verification

- `python -m pytest tests/unit_tests/persistence/recorder/ -q` → **23 passed**, no Phase 1 regressions.
- `grep -n "include_types" scripts/bybit_recorder/config.py` → six types present, funding excluded.
- `grep -n "_RECORDED_TYPES" scripts/bybit_recorder/strategy.py` → constant + `for data_cls in _RECORDED_TYPES` loop present.
- `python -m pytest ...test_recorder_strategy.py -k depth -q` → 4 passed.
- `ruff check` on changed source files → all checks passed; `ruff format` applied.
- `import scripts.bybit_recorder.recorder` → imports cleanly (live entrypoint consistent).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] Wired new strategy config fields in recorder.py**
- **Found during:** Task 3
- **Issue:** `RecorderStrategyConfig` gained three required fields (`linear_instrument_ids`, `instrument_depths`, `instrument_bar_intervals`). The plan attributed `recorder.py` wiring to a "Task 4" that is not part of this plan's task list. Without wiring, the live entrypoint (`recorder.py`) would fail at strategy construction.
- **Fix:** Populated the three fields from `recorder_cfg.linear_instrument_ids` and the parsed `InstrumentEntry` list (`{entry.id: entry.depth ...}` and `{entry.id: entry.bar_intervals ...}`).
- **Files modified:** scripts/bybit_recorder/recorder.py
- **Commit:** 8f4ea1d090

**2. [Rule 3 - Blocking issue] Updated stale Phase 1 test for widened include_types**
- **Found during:** Task 2 finalization
- **Issue:** `test_build_streaming_config_uses_scheduled_dates_daily_rotation` asserted `include_types == [TradeTick]` — stale after Task 2 widened the list. Expected, intentional drift from this plan, not a regression.
- **Fix:** Updated the assertion to the widened six-type list with an explanatory comment.
- **Files modified:** tests/unit_tests/persistence/recorder/test_recorder_config.py
- **Commit:** cba6569d68 (assertion); 8f4ea1d090 (ruff reformat of unrelated lines in same file)

## Environment Note

The worktree did not contain the compiled Cython `.so` extension modules (build artifacts live in the main repo and are gitignored). Copied the 111 `.so` files from the main repo into the worktree to make `nautilus_trader` importable for the test run. These files are gitignored and do NOT appear in any commit.

## Threat Surface

No new security-relevant surface beyond the plan's `<threat_model>`. T-2-01 (depth/bar_intervals tampering → silent no-data) is mitigated as designed: per-product-type fail-fast `ValueError` in `load_recorder_config` plus `BarType.from_str` raising on invalid interval strings, all before any subscription.

## Known Stubs

None. All five new data types are wired to real subscriptions and the conversion loop; no placeholder/empty data sources.

## Self-Check: PASSED

All modified source files exist and both new commits (cba6569d68, 8f4ea1d090) are present in git history.
