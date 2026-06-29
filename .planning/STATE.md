---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: dYdX Research Platform
current_phase: 03
current_phase_name: collector-dashboard-split
status: executing
last_updated: "2026-06-29T17:50:55.949Z"
last_activity: 2026-06-29
last_activity_desc: Phase 03 execution started
progress:
  total_phases: 3
  completed_phases: 2
  total_plans: 4
  completed_plans: 3
  percent: 67
---

# Project State

## Current Position

Phase: 03 (collector-dashboard-split) — EXECUTING
Status: Executing Phase 03
Last activity: 2026-06-29 — Phase 03 execution started

Progress: ██████████ 100%

## Accumulated Context

### Key Decisions

- Single phase for v1.0: all 17 requirements map to writing one file (`troll/CLAUDE.md`)
- No audit/research pre-phase needed: requirements are already grounded in documented real incidents
- Signal architecture: 1s-based snapshots (`DydxSecondSnapshot`), not event-driven
- Derive-don't-store: OFI/OBI/microprice/spread computed from raw level data, never stored
- Dashboard live path: all data from in-process `_second_rolling` deque, no Parquet reads

### Blockers

None.

### Session Continuity

Both phases complete. Next: define Phase 3 (strategy research / backtest pipeline).
