# Roadmap: dYdX Research Platform

## Overview

Milestone v1.0 delivers a single artifact: `troll/CLAUDE.md`, a set of actionable rules for working on this codebase. Every rule maps to a real failure mode — fork contamination, memory leaks, over-engineering, silent Nautilus precision bugs — observed in earlier iterations of this project. One phase, one file, done.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 1: Project Guardrails** - Write `troll/CLAUDE.md` encoding all 17 requirements as enforceable rules for working on this codebase

## Phase Details

### Phase 1: Project Guardrails
**Goal**: `troll/CLAUDE.md` exists and gives Claude clear, enforceable rules across fork safety, design, memory discipline, testing, readability, and Nautilus usage patterns — preventing the specific failure modes documented in this project's history before any feature work starts
**Depends on**: Nothing (first phase)
**Requirements**: FORK-01, FORK-02, DESIGN-01, DESIGN-02, DESIGN-03, MEM-01, MEM-02, MEM-03, TEST-01, TEST-02, TEST-03, READ-01, READ-02, READ-03, NAUT-01, NAUT-02, NAUT-03
**Success Criteria** (what must be TRUE):
  1. `troll/CLAUDE.md` exists with sections covering all 6 rule categories: fork safety, design principles, memory discipline, testing, code readability, Nautilus usage patterns
  2. The fork safety section explicitly names `nautilus_trader/` and `crates/` as off-limits and prohibits `TradingNode`/`DataEngine` instantiation in troll/ code
  3. The memory section names the specific anti-pattern (`catalog.trade_ticks()` with no time bounds) and mandates `BacktestDataConfig` streaming or time-bounded queries as the alternative
  4. The Nautilus section documents the `Price(decimal, precision)` silent precision bug and mandates `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()` for any re-stamping operation
  5. The testing section clearly distinguishes required tests (financial calculations, Nautilus type integrations) from optional tests (trivial glue, config parsing) so the rule is actionable without judgment calls
**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Project Guardrails | 0/1 | Not started | - |
