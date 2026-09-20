---
title: 'Story 22.6: live_paper multi-venue paper trading (Sandbox)'
type: feature
created: '2026-09-20'
status: done
review_loop_iteration: 0
followup_review_recommended: false
final_revision: cf9b22123fbd741f38117719f925f81bb8d8d707
context: []
warnings: []
---

Spec of record: `22-6-live-paper-multi-venue-paper-trading-sandbox.md` (story file, all tasks checked).

## Review Triage Log

### 2026-09-20 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 1 (low 1)
- defer: 0
- reject: 22 (design/style/pre-existing or speculative: shared-pool isolation, bybit dual product types, unused-venue check, real-money non-DYDX, case-normalisation, migration hints, doc nits)
- addressed_findings:
  - `[low]` `[patch]` `_parse_venue`/`load_paper_config` accepted non-string environment, empty/non-string starting_balances, arbitrary account_type and non-table `[venues]` → now rejected at load with a `[venues.X]`-naming ValueError; parametrized test added.

## Auto Run Result

Status: done. Per-venue data + Sandbox exec clients, `[venues.*]` config, AD-11 amendment, mixed spot/linear evidence.
- Files: `troll/live_paper/{config,node,venues}.py`, config.toml, README/DEPLOY_CHECKLIST, tests (config 32 passing).
- Review: Blind + Edge Case hunters run; 1 low patch applied, 0 deferred, rest rejected.
- Verification: `pytest live_paper/tests/test_config.py` 32 passed.
- Residual risk: same-venue bots share one Sandbox margin pool; Bybit loads both product types.
