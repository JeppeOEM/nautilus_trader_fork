---
phase: 02-full-data-type-coverage
plan: 02
subsystem: bybit-recorder
tags: [recorder, funding-rate, dedup, streaming, catalog, tdd]
requires:
  - Phase 2 Plan 01 (widened include_types, linear-only mark/index gating, _RECORDED_TYPES conversion loop)
provides:
  - Deduped funding-rate recording for linear instruments (REC-05, D-01)
  - Resolved persistence + native read-back path for FundingRateUpdate (Open Question 1)
affects:
  - scripts/bybit_recorder/strategy.py
  - tests/unit_tests/persistence/recorder/conftest.py
  - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
  - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
  - scripts/bybit_recorder/recorder.toml
tech-stack:
  added: []
  patterns:
    - "Strategy-owned second StreamingFeatherWriter (include_types=[FundingRateUpdate]) separate from the kernel '*' writer which excludes FundingRateUpdate"
    - "Per-instrument _last_funding_rate dict as the value-change dedup gate (drop unchanged, persist on change)"
    - "FundingRateUpdate appended to the per-type _convert_stream loop alongside _RECORDED_TYPES; class_to_filename resolves it to the native funding_rate_update table"
key-files:
  created: []
  modified:
    - scripts/bybit_recorder/strategy.py
    - tests/unit_tests/persistence/recorder/conftest.py
    - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
    - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
    - scripts/bybit_recorder/recorder.toml
decisions:
  - "Resolved Open Question 1: deduped funding rows persist via a strategy-owned StreamingFeatherWriter (include_types=[FundingRateUpdate]) and read back natively via catalog.funding_rates(...) -- class_to_filename(FundingRateUpdate) == 'funding_rate_update' (no custom_ prefix)"
  - "recorder.py's linear_instrument_ids/instrument_depths/instrument_bar_intervals wiring (added as a Plan 01 deviation) already satisfied this plan's recorder.py requirement -- no further recorder.py changes needed"
metrics:
  duration: ~40m
  completed: 2026-06-14
---

# Phase 2 Plan 02: Funding-Rate Dedup + Recorder Wiring Summary

Resolved the phase's last open architectural question with an empirical spike (a strategy-owned `StreamingFeatherWriter` including `FundingRateUpdate`, reading back natively via `catalog.funding_rates(...)`), then implemented linear-only funding subscription with a per-instrument value-change dedup gate in `strategy.py`, leaving the live ~100ms funding ticker reduced to a handful of persisted rows per instrument per day.

## What Was Built

- **Task 1 (empirical spike):** Added `sample_funding_rates` fixture (two `FundingRateUpdate` objects with distinct `rate` Decimal values, monotonic `ts_init`, constructed directly since no `TestDataStubs` factory exists). Added `test_convert_stream_to_data_roundtrips_funding_rates`, which writes via a `StreamingFeatherWriter` with `include_types=[FundingRateUpdate]` (separate from the kernel `"*"` writer that excludes it per D-01/Plan 01), converts via `catalog.convert_stream_to_data(data_cls=FundingRateUpdate, subdirectory="live")`, and reads back via `catalog.funding_rates(...)` — all elements are native `FundingRateUpdate` instances. This confirms `class_to_filename(FundingRateUpdate) == "funding_rate_update"` (no `custom_` prefix), resolving Open Question 1. The resolved path is documented in a top-of-test comment as the Task 2 contract.
- **Task 2 (TDD — funding dedup + subscription):**
  - RED: added `test_on_start_subscribes_funding_rates_linear_only`, `test_on_funding_rate_dedups_unchanged_rate`, `test_on_funding_rate_persists_changed_rate`, and `test_on_funding_rate_dedup_is_per_instrument` to `test_recorder_strategy.py` — all 4 failed (`_persist_funding_rate`/`subscribe_funding_rates` did not exist).
  - GREEN: `strategy.py` now:
    - imports `FundingRateUpdate` and `StreamingFeatherWriter`
    - `__init__` initializes `self._last_funding_rate: dict[InstrumentId, object] = {}` and `self._funding_writer: StreamingFeatherWriter | None = None`
    - `on_start`'s linear-only loop calls `self.subscribe_funding_rates(instrument_id)` alongside mark/index (D-04)
    - `on_funding_rate` compares the incoming rate against `_last_funding_rate.get(instrument_id)`; drops if unchanged, otherwise updates the dict and calls `_persist_funding_rate`
    - `_persist_funding_rate` lazily creates the strategy-owned `StreamingFeatherWriter` (`include_types=[FundingRateUpdate]`, path `{catalog_path}/live/{instance_id_str}`) and writes + flushes
    - `_convert_stream` flushes the funding writer first, then converts `[*_RECORDED_TYPES, FundingRateUpdate]` into the catalog per-type with the existing try/except pattern
  - `recorder.py` already wired `linear_instrument_ids`, `instrument_depths`, `instrument_bar_intervals` as a Plan 01 deviation — verified present, no further change needed (`grep -n "linear_instrument_ids" scripts/bybit_recorder/recorder.py` → line 92).
- **Task 3 prep (config only, NOT executed):** `recorder.toml` `conversion_interval_minutes` changed 60 -> 1 (temporary, for the smoke window). Confirmed one linear instrument (`BTCUSDT-LINEAR.BYBIT`, depth=50, valid in `{1,50,200,1000}`) and one spot instrument (`ETHUSDT-SPOT.BYBIT`, depth=50, <=50 per D-03), each with `bar_intervals = ["1-MINUTE"]`.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Empirical spike — resolve deduped-funding persistence path | 6c3a367127 | conftest.py, test_recorder_conversion.py |
| 2 (RED) | Failing tests for funding dedup + subscription | ae98652ea7 | test_recorder_strategy.py |
| 2 (GREEN) | Funding subscription + dedup gate + persistence | 1e8cbc9b20 | strategy.py |
| 3 (prep) | recorder.toml smoke-run config | b15d1136b4 | recorder.toml |

## Verification

- `python -m pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k funding -q` → 1 passed.
- `python -m pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k funding -q` → 4 passed.
- `python -m pytest tests/unit_tests/persistence/recorder/ -q` → **28 passed** (24 from Plan 01 + 4 new), no regressions.
- `grep -n "on_funding_rate\|_last_funding_rate\|subscribe_funding_rates" scripts/bybit_recorder/strategy.py` → all three present.
- `grep -n "linear_instrument_ids" scripts/bybit_recorder/recorder.py` → present (Plan 01 wiring).
- `ruff check` / `ruff format` on all changed source files → all checks passed.

## TDD Gate Compliance

- RED commit: `ae98652ea7` (`test(02-02): add failing tests for funding-rate dedup and subscription`) — 4 tests failed with `AttributeError` before implementation.
- GREEN commit: `1e8cbc9b20` (`feat(02-02): record deduped funding-rate updates for linear instruments`) — all 4 tests pass, full scoped suite green (28 passed).
- No REFACTOR commit needed — implementation was clean on first pass.

## Deviations from Plan

### Auto-fixed Issues

None beyond the Plan 01 deviation already noted (recorder.py wiring was completed in Plan 01's Task 3 as a Rule 2 fix and required no further changes here).

## Environment Note

This worktree did not contain the compiled Cython `.so` extension modules (gitignored build artifacts). Copied 111 `.so` files from the main repo checkout (`/home/mrqdt/code/nautilus_trader_fork/nautilus_trader/`) into the matching relative paths under this worktree's `nautilus_trader/` so `import nautilus_trader` and the test suite work, as documented in Plan 02-01's SUMMARY. These files are gitignored and do NOT appear in any commit.

## Threat Surface

No new surface beyond the plan's `<threat_model>`. T-2-02 (funding-flood resource exhaustion) is mitigated as designed: `FundingRateUpdate` remains excluded from the kernel `"*"` writer's `include_types` (Plan 01), and the new `on_funding_rate` change-gate (`_last_funding_rate`) ensures only value-changes reach the strategy-owned funding writer. T-2-04 (mark/index/funding subscribed for spot) remains mitigated by linear-only gating — `subscribe_funding_rates` is now part of that same linear-only loop (Pitfall 4).

## Known Stubs

None. The funding subscription, dedup gate, persistence path, and conversion-loop wiring are all real, wired to the live Bybit data path.

## Checkpoint State: Task 3 (human-verify) — APPROVED

**Status: PASSED.** A live Bybit mainnet run (`python -m scripts.bybit_recorder.recorder`, ~100s) confirmed all seven feeds land in the catalog with funding deduped.

**Live run results** (`scripts/bybit_recorder/inspect_catalog.py`, after fixing its catalog root to `catalog/streaming`):

| Check | Result |
| ----- | ------ |
| trade_ticks | 1474 — OK |
| quote_ticks | 2193 — OK |
| order_book_deltas | 21814 — OK |
| bars (1-MINUTE) | 1 — OK |
| mark_price_updates | 29 — OK |
| index_price_updates | 70 — OK |
| funding_rates | 1 (deduped, < 50) — OK |

`order_book_deltas` landed under both `catalog/streaming/data/order_book_deltas/<instrument>/` (parquet) and `catalog/streaming/live/<instance>/order_book_deltas/<instrument>/` (feather) for both `BTCUSDT-LINEAR.BYBIT` and `ETHUSDT-SPOT.BYBIT`, confirming the Plan 01 `OrderBookDeltas` include_types fix works end-to-end. No "Cannot subscribe to mark/index/funding for SPOT instrument" errors — linear-only gating held.

**Post-approval cleanup (commits ab7dfeae65, e588d4bdb4):**
- Fixed `scripts/bybit_recorder/inspect_catalog.py`: `ParquetDataCatalog("catalog")` → `ParquetDataCatalog("catalog/streaming")` (the actual data root; `catalog_path` in `recorder.toml` is dead).
- Restored `conversion_interval_minutes` from `1` back to `60` in `recorder.toml`.
- Removed temporary on-disk test artifacts (`catalog/`, `logs/bybit_recorder.log`, `/tmp/debug_catalog/`) and a throwaway debug script.

## Self-Check: PASSED

All modified source files exist and all six commits (6c3a367127, ae98652ea7, 1e8cbc9b20, b15d1136b4, ab7dfeae65, e588d4bdb4) are present in git history.

## Completed Tasks

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Empirical spike — resolve deduped-funding persistence path | 6c3a367127 | conftest.py, test_recorder_conversion.py |
| 2 | Funding subscription + dedup gate + persistence (TDD) | ae98652ea7 (RED), 1e8cbc9b20 (GREEN) | test_recorder_strategy.py, strategy.py |
| 3 | Live smoke (prep + run + approval + cleanup) | b15d1136b4, ab7dfeae65, e588d4bdb4 | recorder.toml, inspect_catalog.py |

**Progress:** 3/3 tasks complete. Plan 02-02 done.
