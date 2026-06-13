---
phase: 2
slug: full-data-type-coverage
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-13
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.4.4 |
| **Config file** | repo `pyproject.toml` / pytest settings (existing) |
| **Quick run command** | `pytest tests/unit_tests/persistence/recorder/ -q` |
| **Full suite command** | `pytest tests/unit_tests/persistence/recorder/ -q` (this milestone's scoped suite) |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit_tests/persistence/recorder/ -k <new test> -q`
- **After every plan wave:** Run `pytest tests/unit_tests/persistence/recorder/ -q`
- **Before `/gsd-verify-work`:** Full scoped suite must be green + one live smoke (human-verify) proving all six feeds reach the catalog, mirroring Phase 1's approved E2E smoke.
- **Max feedback latency:** ~30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-XX | 01 | 0 | REC-02 | V5 | QuoteTick round-trips feather→parquet→`catalog.quote_ticks()` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k quote -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-03 | V5 / T-2-01 | OrderBookDelta round-trips → `catalog.order_book_deltas()` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k order_book -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-03 | V5 / T-2-01 | Spot depth fails fast at startup/config-load (D-03 + discrete-set check) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k depth -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-04 | V5 / T-2-01 | Bar round-trips → `catalog.bars(bar_types=[...-LAST-EXTERNAL])` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k bar -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-04 | V5 / T-2-01 | `BarType.from_str(f"{iid}-{interval}-LAST-EXTERNAL")` builds for each config interval | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k bartype -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-05 | T-2-02 | Funding dedup: only changed rates persist; unchanged dropped | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k funding_dedup -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-05 | T-2-02 | Deduped funding round-trips and reads back as FundingRateUpdate | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k funding -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-06 | V5 | MarkPriceUpdate + IndexPriceUpdate round-trip → `catalog.query(...)` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k "mark or index" -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | 0 | REC-04/D-04 | — | Linear-only mark/index/funding gating (no spot subscribe) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k linear_only -q` | ❌ W0 | ⬜ pending |
| 02-01-XX | 01 | final | All | — | Live smoke: short Bybit run records all six types for 1 linear + 1 spot, converts, reloads | manual (checkpoint:human-verify) | `python -m scripts.bybit_recorder.recorder` with `conversion_interval_minutes=1` (mirror Phase 1 Plan 04 smoke) | manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit_tests/persistence/recorder/test_recorder_conversion.py` — extend with round-trip tests for QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate, deduped funding (reuse `catalog_dir` fixture + `StreamingFeatherWriter` pattern)
- [ ] `tests/unit_tests/persistence/recorder/conftest.py` — add fixtures for sample lists of each new type with strictly monotonic `ts_init`; `TestDataStubs` provides `quote_tick`, `order_book_delta(s)`, `mark_price`, `index_price`, `bar_spec_1min_last`; `FundingRateUpdate` has no stub — construct directly (`instrument_id`, `rate`, `ts_event`, `ts_init`)
- [ ] `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` — funding-dedup behavior test (drop unchanged, persist changed), spot depth-validation fail-fast test, linear-only gating test (spot instrument does NOT get mark/index/funding subscriptions), BarType construction test

*Framework install: none — pytest already present.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live smoke: all six data types reach the catalog from real Bybit feeds | REC-02 to REC-06 | Requires live network connection to Bybit mainnet WS; cannot be fully simulated in unit tests | Run `python -m scripts.bybit_recorder.recorder` with `conversion_interval_minutes=1` for 1 linear + 1 spot instrument, let it run ~2 minutes, stop, then load the catalog and confirm non-empty `quote_ticks()`, `order_book_deltas()`, `bars()`, `query(MarkPriceUpdate)`, `query(IndexPriceUpdate)`, and deduped funding rows |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
