---
phase: 01-project-guardrails
verified: 2026-06-27T00:00:00Z
status: passed
score: 5/5 must-haves verified
behavior_unverified: 0
overrides_applied: 0
---

# Phase 01: Project Guardrails Verification Report

**Phase Goal:** `troll/CLAUDE.md` exists and gives Claude clear, enforceable rules across fork safety, design, memory discipline, testing, readability, and Nautilus usage patterns — preventing the specific failure modes documented in this project's history before any feature work starts
**Verified:** 2026-06-27
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A reader opening troll/CLAUDE.md sees 6 distinct rule-category sections: fork safety, design, memory, testing, readability, Nautilus usage | VERIFIED | All six `##` headings present in order: Fork Safety, Design Principles, Memory Discipline, Testing, Code Readability, Nautilus Usage Patterns |
| 2 | Claude working in troll/ can determine whether a given test is required vs optional without a judgment call | VERIFIED | TEST-01 enumerates required cases (financial calcs, Nautilus integration); TEST-02 enumerates non-required cases with the explicit heuristic "no branching logic and no arithmetic" — no judgment call needed |
| 3 | Claude can determine the correct Price/Quantity re-stamping pattern from the file alone | VERIFIED | NAUT-01 documents the `Price(decimal, precision)` silent bug with the concrete failing example (`Price(Decimal("61090.59855"), 16)` → `61090.5985500000026624`), provides the exact code snippet using `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()`, and says "Never use this constructor for re-stamping" |
| 4 | Claude knows which directories are off-limits and which Nautilus runtime classes are banned in troll/ code | VERIFIED | FORK-01 names `nautilus_trader/` and `crates/` as off-limits; FORK-02 names `TradingNode` and `DataEngine` as banned with the documented OOM rationale |
| 5 | Claude knows the banned full-catalog-load anti-pattern and its time-bounded alternative | VERIFIED | MEM-01 explicitly names `catalog.trade_ticks()` with no time bounds as the anti-pattern and mandates time-bounded queries or `BacktestDataConfig` streaming |

**Score:** 5/5 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `troll/CLAUDE.md` | Enforceable rules across 6 categories, all 17 requirement IDs, min 60 lines | VERIFIED | 64 lines, all 6 sections, all 17 IDs explicitly labeled (`**FORK-01**` through `**NAUT-03**`), committed as `fa70707bc0` via `git add -f` (root .gitignore has `CLAUDE.md` pattern at line 165; force-add is the correct fix) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `troll/CLAUDE.md` | CLAUDE.md (repo root) + .planning/REQUIREMENTS.md | Each of the 17 requirement statements encoded as an actionable rule; pattern `Decimal.scaleb` present | VERIFIED | All 17 requirement IDs are present verbatim as bold bullet labels in the file. `Decimal.scaleb()` present at line 57. The file traces directly to REQUIREMENTS.md coverage: one rule per requirement ID, no ID dropped. |

### Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| FORK-01 | `nautilus_trader/` and `crates/` must never be modified | SATISFIED | Line 10: "Never modify `nautilus_trader/` or `crates/`. All `troll/` code is additive-only." |
| FORK-02 | `nautilus_trader` as library only; ban `TradingNode`/`DataEngine` | SATISFIED | Line 11: "Never instantiate `TradingNode` or `DataEngine` in `troll/` code." with OOM rationale |
| DESIGN-01 | YAGNI — no abstractions for values that don't change | SATISFIED | Line 17: "YAGNI. No abstractions, interfaces, factories, or config for values that don't change." |
| DESIGN-02 | Decouple at natural component seams | SATISFIED | Line 18: "Decouple at natural seams: `collector` / `book_features` / strategy / backtest." |
| DESIGN-03 | Prefer deletion over addition; boring over clever | SATISFIED | Line 19: "Prefer deletion over addition when simplification is possible. Prefer boring over clever." |
| MEM-01 | Prohibit full catalog slice loads; name anti-pattern and alternative | SATISFIED | Line 25: names `catalog.trade_ticks()` with no time bounds; mandates time-bounded queries or `BacktestDataConfig` streaming |
| MEM-02 | Non-configured coins in-memory rolling-window only | SATISFIED | Line 26: "Non-configured coins are in-memory rolling-window only. No unbounded accumulation." |
| MEM-03 | Generator/iterator patterns for large data pipelines | SATISFIED | Line 27: "Use generator/iterator patterns for large data pipelines." |
| TEST-01 | Tests required for financial calcs and Nautilus integration | SATISFIED | Lines 33-35: required for OFI, imbalance, microprice, precision conversion, and Nautilus/catalog integration paths |
| TEST-02 | Trivial glue code does not require tests | SATISFIED | Line 36: "not required for trivial glue code: config parsing, logging setup, simple data routing" with actionable heuristic |
| TEST-03 | No mocking Nautilus internals; pytest, minimal fixtures, one assertion | SATISFIED | Lines 38-40: never mock Nautilus internals; use real objects or skip; pytest; one assertion per logical claim |
| READ-01 | Functions under ~30 lines; names explain intent | SATISFIED | Line 46: "Functions should be under ~30 lines. Names explain intent..." |
| READ-02 | Comments explain WHY, not WHAT | SATISFIED | Line 47: "Comments explain **WHY**: hidden constraints, subtle invariants, workarounds." |
| READ-03 | Type hints required on all function signatures | SATISFIED | Line 48: "Type hints required on all function signatures in `troll/` code." with mypy enforcement noted |
| NAUT-01 | Document `Price(decimal, precision)` bug; mandate `Decimal.scaleb()` + `from_raw()` | SATISFIED | Lines 54-60: bug documented with concrete failing example; re-stamping code snippet provided; "Never use this constructor for re-stamping" explicit |
| NAUT-02 | All data written via `ParquetDataCatalog.write_data()` | SATISFIED | Line 62: "All data written via `ParquetDataCatalog.write_data()`. No hand-rolled Parquet schemas." |
| NAUT-03 | Backtests use `BacktestNode` + `BacktestDataConfig`; `ImportableStrategyConfig` by string path | SATISFIED | Line 64: "Backtests use `BacktestNode` + `BacktestDataConfig`... Reference strategies via `ImportableStrategyConfig` by string path" |

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| (none) | No TBD, FIXME, XXX, placeholders, or stubs found | — | — |

### Behavioral Spot-Checks

Step 7b: SKIPPED — documentation-only phase. No runnable entry points introduced.

### Probe Execution

Step 7c: SKIPPED — no probes declared in PLAN or SUMMARY; documentation-only phase.

### Human Verification Required

None — all checks are binary (present/absent, literal string match). No visual, real-time, or external service behavior to verify.

### Gaps Summary

No gaps. All 5 observable truths verified, all 17 requirement IDs confirmed present with actionable rule text, artifact exists and is committed to git (force-added past `.gitignore` correctly). Phase goal is fully achieved.

---

_Verified: 2026-06-27_
_Verifier: Claude (gsd-verifier)_
