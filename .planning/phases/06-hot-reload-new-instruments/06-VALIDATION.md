---
phase: 6
slug: hot-reload-new-instruments
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-15
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.4.4 (+ pytest-mock, pytest-asyncio 0.23.8) |
| **Config file** | `pyproject.toml` (repo root) |
| **Quick run command** | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -x` |
| **Full suite command** | `pytest tests/unit_tests/persistence/recorder/ -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -x`
- **After every plan wave:** Run `pytest tests/unit_tests/persistence/recorder/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green, plus the live hot-add smoke (human-verify checkpoint)
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | HOT-01 | — / N/A | reload timer registered in on_start | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k timer_registered -x` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | HOT-01 | — / N/A | diff detects an addition | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k detects_addition -x` | ❌ W0 | ⬜ pending |
| 06-01-03 | 01 | 1 | HOT-01 | — / N/A | addition subscribes all feeds after cache-confirm (D-10) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k addition_subscribes -x` | ❌ W0 | ⬜ pending |
| 06-01-04 | 01 | 1 | HOT-01 | — / N/A | removal unsubscribes all feeds + prunes _last_seen (D-06) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k removal_unsubscribes -x` | ❌ W0 | ⬜ pending |
| 06-01-05 | 01 | 1 | HOT-01 | — / N/A | depth change = unsubscribe-then-subscribe in order (D-05/Pitfall 2) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k depth_swap_order -x` | ❌ W0 | ⬜ pending |
| 06-01-06 | 01 | 1 | HOT-01 | — / N/A | bar-interval delta only touches changed intervals (D-05) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k bar_interval_delta -x` | ❌ W0 | ⬜ pending |
| 06-01-07 | 01 | 1 | HOT-01 | — / N/A | invalid/unknown new instrument skipped + not retried (D-07/D-11) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k failed_not_retried -x` | ❌ W0 | ⬜ pending |
| 06-01-08 | 01 | 1 | HOT-01 | — / N/A | failed-set resets when toml changes (D-07) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k failed_resets_on_change -x` | ❌ W0 | ⬜ pending |
| 06-01-09 | 01 | 1 | HOT-01 | — / N/A | hot-add count over threshold logs WARNING (D-08) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k threshold_warning -x` | ❌ W0 | ⬜ pending |
| 06-01-10 | 01 | 1 | HOT-01 | — / N/A | linear-only gating for mark/index/funding on hot-add/remove (D-04/Pitfall 5) | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k linear_gating -x` | ❌ W0 | ⬜ pending |
| 06-01-11 | 01 | 1 | HOT-01 | — / N/A | new D-08 knob validated `> 0` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_config.py -k max_hot_added -x` | ❌ W0 (extend existing file) | ⬜ pending |
| 06-01-12 | 01 | 1 | HOT-01 | — / N/A | end-to-end live hot-add against Bybit mainnet | manual smoke | human-verify checkpoint | N/A (live) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py` — stubs for HOT-01 diff/add/remove/swap/failure/threshold/gating
- [ ] Extend `tests/unit_tests/persistence/recorder/test_recorder_config.py` — `max_hot_added_instruments` validation
- [ ] Reuse existing `conftest.py` fixtures (`mock_cache`, `sample_toml`, `_build_strategy` helper) — extend `_build_strategy` to inject a mock data-client reference for Pattern 3 tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end live hot-add against Bybit mainnet (edit `recorder.toml` while running, confirm new instrument's feeds land in catalog) | HOT-01 | Requires live Bybit WebSocket connection and real instrument-provider load; cannot be mocked meaningfully | Start recorder against mainnet, edit `recorder.toml` to add an instrument, wait for the next reload poll, confirm subscriptions appear in logs and data lands in the streaming catalog for the new instrument |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
