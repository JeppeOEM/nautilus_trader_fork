---
phase: 3
slug: reliability-for-24-7-operation
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-14
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.4.4 (+ pytest-mock) |
| **Config file** | repo-level `pyproject.toml`; recorder tests under `tests/unit_tests/persistence/recorder/` |
| **Quick run command** | `python -m pytest tests/unit_tests/persistence/recorder/ -q` |
| **Full suite command** | `python -m pytest tests/unit_tests/persistence/recorder/ tests/unit_tests/persistence/test_catalog.py -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/unit_tests/persistence/recorder/ -q`
- **After every plan wave:** Run `python -m pytest tests/unit_tests/persistence/recorder/ tests/unit_tests/persistence/test_catalog.py -q`
- **Before `/gsd-verify-work`:** Full suite must be green, plus manual forced-disconnect and stop-then-start (same UTC day) integration checks confirmed
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | REL-02 | — | `on_stop()` flushes funding writer and runs a final per-type `convert_stream_to_data` pass before exit | unit | `python -m pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k on_stop -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | REL-02 | — | Same-day restart (same `instance_id`) with leftover unconverted feather rows converts cleanly: no gap, no overlap | unit | `python -m pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k "repeated_conversion or double_conversion" -x` | ✅ | ⬜ pending |
| 03-02-01 | 02 | 2 | REL-03 | — | Each `on_*` handler updates `last_seen[(stream, instrument)]`; `on_funding_rate` updates before its dedup gate | unit | `python -m pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k "last_seen or heartbeat" -x` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 2 | REL-03 | — | Heartbeat timer logs a WARNING when a stream's idle time exceeds its per-type stale threshold; no warning while fresh | unit | `python -m pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k "heartbeat or stale" -x` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 3 | REL-04 | — | No custom reconnect/resubscribe code exists in the recorder (adapter-driven only) | structural | `! grep -rn "reconnect\|resubscribe" scripts/bybit_recorder/` (must find nothing) | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` — add `on_stop` final-convert test (spy/assert `convert_stream_to_data` called per type; assert funding writer flushed) and heartbeat/stale-threshold tests using `TestClock` to advance time past/under thresholds; add test asserting `on_funding_rate` updates `last_seen` before the dedup early-return.

*Existing `tests/unit_tests/persistence/recorder/test_recorder_conversion.py` regression coverage (from commit c98b1c0f80) already covers the repeated-conversion contract underlying REL-02's restart case — extend with an explicit restart-shaped case rather than re-implementing.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Forced WebSocket disconnect resumes recording automatically with no custom reconnect code | REL-04 | Requires a live Bybit connection and externally triggering/observing a real disconnect/reconnect cycle | Run the recorder against Bybit mainnet, kill the WS connection (e.g. via network interruption or adapter-level disconnect trigger), confirm logs show the adapter's reconnect/resubscribe sequence and data resumes flowing without process restart or custom code paths |
| Stop-then-start within the same UTC day produces no data loss and no disjoint-interval errors end-to-end | REL-02 | Requires a real SIGTERM + process restart against live data | Run the recorder briefly, send SIGTERM, confirm `on_stop` flush+convert logs appear with no errors; restart the process with the same config/instance_id, let it run and convert again, confirm no "non-disjoint intervals" errors and the catalog contains a continuous, gap-free record for that day |

---

## Validation Sign-Off

- [x] All tasks have automated verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
