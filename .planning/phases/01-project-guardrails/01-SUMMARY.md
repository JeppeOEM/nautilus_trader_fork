---
phase: 01-project-guardrails
plan: "01"
subsystem: troll/
tags: [guardrails, documentation, project-rules]
requires: []
provides: [troll/CLAUDE.md]
affects: [troll/dydx_collector, troll/ml_signals]
tech_stack:
  added: []
  patterns: []
key_files:
  created:
    - troll/CLAUDE.md
  modified: []
decisions:
  - "Force-added troll/CLAUDE.md via git add -f because root .gitignore has CLAUDE.md pattern; the file is an intentional guardrails artifact"
metrics:
  duration: "~6 minutes"
  completed: "2026-06-27"
status: complete
---

# Phase 01 Plan 01: Project Guardrails Summary

**One-liner:** Encoded 17 hard-won project guardrails into `troll/CLAUDE.md` across fork safety, design, memory, testing, readability, and Nautilus usage categories.

## What Was Built

Created `troll/CLAUDE.md` — a scoped instruction file for all work under `troll/`. It covers all 17 requirements (FORK-01/02, DESIGN-01/02/03, MEM-01/02/03, TEST-01/02/03, READ-01/02/03, NAUT-01/02/03) organised into six `##` sections:

| Section | Requirements | Key rules |
|---------|-------------|-----------|
| Fork Safety | FORK-01, FORK-02 | Never touch `nautilus_trader/` or `crates/`; ban `TradingNode`/`DataEngine` |
| Design Principles | DESIGN-01/02/03 | YAGNI; natural seam decoupling; boring over clever |
| Memory Discipline | MEM-01/02/03 | Ban `catalog.trade_ticks()` without bounds; mandate `BacktestDataConfig` |
| Testing | TEST-01/02/03 | Required for financial calcs / Nautilus integration; not required for glue code |
| Code Readability | READ-01/02/03 | ~30-line functions; WHY comments; type hints required |
| Nautilus Usage | NAUT-01/02/03 | `Price(decimal,precision)` bug documented; mandate `Decimal.scaleb()` + `from_raw()` |

## Tasks

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Write troll/CLAUDE.md | fa70707bc0 | troll/CLAUDE.md (created, 64 lines) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Root .gitignore blocks troll/CLAUDE.md**
- **Found during:** Task 1 commit
- **Issue:** The root `.gitignore` contains the pattern `CLAUDE.md` (line 165), which gitignores all files named `CLAUDE.md` project-wide. `git status` showed nothing to commit despite the file existing on disk.
- **Fix:** Used `git add -f troll/CLAUDE.md` to force-stage the file. The file is an intentional guardrails artifact, not an incidental AI instruction file to be hidden from history.
- **Files modified:** none beyond the intended `troll/CLAUDE.md`
- **Commit:** fa70707bc0

## Verification

All 14 literal identifiers required by the plan's verify command are present in `troll/CLAUDE.md`:

```
## Fork Safety, ## Design Principles, ## Memory Discipline, ## Testing,
## Code Readability, ## Nautilus Usage Patterns, nautilus_trader/, crates/,
TradingNode, DataEngine, catalog.trade_ticks(), BacktestDataConfig,
Decimal.scaleb(), Price.from_raw(), Quantity.from_raw(), Price(decimal, precision),
ParquetDataCatalog.write_data(), BacktestNode, ImportableStrategyConfig
```

Verify command output: `OK`

## Known Stubs

None — this is a documentation-only plan. No data sources, no rendering, no stubs.

## Threat Flags

None — static documentation file under version control; no runtime exposure.

## Self-Check: PASSED

- [x] `troll/CLAUDE.md` exists: `fa70707bc0` (confirmed via git log)
- [x] All 6 section headings present
- [x] All 17 requirement IDs represented
- [x] Verify command exits 0
- [x] Task committed individually
