---
phase: 01
slug: bootstrap-config-end-to-end-slice
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-13
---

# Phase 01 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.4.4 (per CLAUDE.md) |
| **Config file** | repo `pyproject.toml` (pytest config) |
| **Quick run command** | `pytest <test_file> -x -q` |
| **Full suite command** | `pytest tests/<recorder dir>/ -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest <touched test file> -x -q`
- **After every plan wave:** Run `pytest tests/<recorder dir>/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green, plus one manual live smoke run reloading ≥1 TradeTick
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-* | 01 | 0 | CONF-01/02 | V5 | TOML loads instrument list + depth/bar_intervals into structured config | unit | `pytest tests/.../test_recorder_config.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 01-02-* | 02 | 1 | REC-07 | V5 | StreamingConfig wired on node (no custom writer) | unit (config assertion) | `pytest tests/.../test_recorder_config.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 01-03-* | 03 | 1 | CONF-03 | V7 | Missing instrument validation raises with ALL missing IDs listed | unit (mock cache) | `pytest tests/.../test_recorder_strategy.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 01-04-* | 03 | 1 | REC-01 | — | subscribe_trade_ticks called per configured instrument | unit (mock) | `pytest tests/.../test_recorder_strategy.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 01-05-* | 04 | 2 | REL-01 | V12 | convert_stream_to_data turns feather→parquet; trades reload via catalog.trade_ticks | integration | `pytest tests/.../test_recorder_conversion.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 01-06-* | 05 | 3 | (E2E) | — | live Bybit → feather → parquet → reload as TradeTick | manual smoke | run recorder ~2 min on BTCUSDT-LINEAR.BYBIT, then `catalog.trade_ticks(...)` | manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/.../test_recorder_config.py` — TOML parse, instrument-id construction, StreamingConfig assertions (CONF-01/02, REC-07)
- [ ] `tests/.../test_recorder_strategy.py` — validation raise-all-missing, subscribe calls (CONF-03, REC-01)
- [ ] `tests/.../test_recorder_conversion.py` — feather→parquet integration using a temp catalog + synthetic TradeTicks, reload via `catalog.trade_ticks` (REL-01, criterion 5)
- [ ] `conftest.py` — fixtures: temp catalog dir, mock cache, sample TOML, sample TradeTicks

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end live recording → conversion → reload | (E2E success criteria 3-5) | Requires live Bybit mainnet WebSocket connection and real trade-tick flow | Run recorder ~2 min against BTCUSDT-LINEAR.BYBIT, trigger/await conversion timer, then open the resulting `ParquetDataCatalog` and confirm `catalog.trade_ticks(...)` returns native `TradeTick` objects |
| Non-zero exit on missing instrument (D-05/A3) | CONF-03 | Requires observing `TradingNode.run()` process exit code under a raise from `on_start` | Configure an invalid instrument ID, run the recorder, confirm process exits non-zero and journald-equivalent log shows the missing-instrument error |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
