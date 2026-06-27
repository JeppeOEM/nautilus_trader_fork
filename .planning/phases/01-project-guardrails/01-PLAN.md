---
phase: 01-project-guardrails
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - troll/CLAUDE.md
autonomous: true
requirements:
  - FORK-01
  - FORK-02
  - DESIGN-01
  - DESIGN-02
  - DESIGN-03
  - MEM-01
  - MEM-02
  - MEM-03
  - TEST-01
  - TEST-02
  - TEST-03
  - READ-01
  - READ-02
  - READ-03
  - NAUT-01
  - NAUT-02
  - NAUT-03

must_haves:
  truths:
    - "A reader opening troll/CLAUDE.md sees 6 distinct rule-category sections: fork safety, design, memory, testing, readability, Nautilus usage"
    - "Claude working in troll/ can determine whether a given test is required vs optional without a judgment call"
    - "Claude can determine the correct Price/Quantity re-stamping pattern from the file alone"
    - "Claude knows which directories are off-limits and which Nautilus runtime classes are banned in troll/ code"
    - "Claude knows the banned full-catalog-load anti-pattern and its time-bounded alternative"
  artifacts:
    - path: "troll/CLAUDE.md"
      provides: "Enforceable working rules for troll/ across 6 categories, one rule per requirement ID"
      min_lines: 60
      contains: "Fork Safety"
  key_links:
    - from: "troll/CLAUDE.md"
      to: "CLAUDE.md (repo root) + .planning/REQUIREMENTS.md"
      via: "Each of the 17 requirement statements is encoded as an actionable rule"
      pattern: "Decimal.scaleb"
---

<objective>
Write `troll/CLAUDE.md`: a single, scoped instruction file giving Claude clear, enforceable rules for all work under `troll/` across six categories — fork safety, design principles, memory discipline, testing, code readability, and Nautilus usage patterns. Every rule maps to a documented real failure mode in this project's history (fork contamination, unbounded-memory OOM, over-engineering, silent Nautilus precision bug).

Purpose: Encode the project's hard-won lessons as guardrails BEFORE any feature work begins, so the failure modes do not recur.
Output: `troll/CLAUDE.md` covering all 17 requirements (FORK-01/02, DESIGN-01/02/03, MEM-01/02/03, TEST-01/02/03, READ-01/02/03, NAUT-01/02/03).
</objective>

<execution_context>
@/home/mrqdt/code/nautilus_trader_fork/.claude/gsd-core/workflows/execute-plan.md
@/home/mrqdt/code/nautilus_trader_fork/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/REQUIREMENTS.md
@.planning/STATE.md
</context>

<artifacts_this_phase_produces>
New symbols / files created by this phase (none are code symbols; this is a documentation phase):

- New file: `troll/CLAUDE.md`
- New markdown section headings inside that file: `Fork Safety`, `Design Principles`, `Memory Discipline`, `Testing`, `Code Readability`, `Nautilus Usage Patterns`

No new classes, functions, decorators, CLI flags, or dataclass fields are introduced. All code identifiers referenced in the file (`TradingNode`, `DataEngine`, `catalog.trade_ticks()`, `BacktestDataConfig`, `BacktestNode`, `ImportableStrategyConfig`, `ParquetDataCatalog.write_data()`, `Price.from_raw()`, `Quantity.from_raw()`, `Decimal.scaleb()`, `Price(decimal, precision)`) are pre-existing symbols from `nautilus_trader` / its public API, cited as documentation references, not created here.
</artifacts_this_phase_produces>

<tasks>

<task type="auto">
  <name>Task 1: Write troll/CLAUDE.md with all six rule-category sections</name>
  <files>troll/CLAUDE.md</files>
  <read_first>
    - /home/mrqdt/code/nautilus_trader_fork/CLAUDE.md — repo-root instructions; the source of truth for the documented incidents (OOM/unbounded-queue bug, mark/index precision re-stamping incident, `Price(decimal, precision)` silent bug, fork-safety rule, "use Nautilus built-ins first" philosophy). Copy the concrete identifiers and rationale from here.
    - /home/mrqdt/code/nautilus_trader_fork/.planning/REQUIREMENTS.md — the 17 requirement statements (FORK/DESIGN/MEM/TEST/READ/NAUT). Each must become a rule in the file.
    - /home/mrqdt/code/nautilus_trader_fork/.planning/codebase/TESTING.md — existing test conventions (pytest, fixtures) so the Testing section matches repo reality.
    - /home/mrqdt/code/nautilus_trader_fork/.planning/codebase/CONVENTIONS.md — existing naming/style conventions so the Readability section does not contradict the repo.
  </read_first>
  <action>
    Create `troll/CLAUDE.md` as a scoped instruction file for all work under `troll/`. Open with a one-line scope statement: these rules govern `troll/` (the dydx_collector and ml_signals personal-code tree), and `nautilus_trader/` is consumed as a library only. Use exactly these six `##` section headings in this order: `Fork Safety`, `Design Principles`, `Memory Discipline`, `Testing`, `Code Readability`, `Nautilus Usage Patterns`. Write each rule as a short imperative bullet. Encode every requirement:

    Fork Safety — FORK-01: never modify `nautilus_trader/` or `crates/`; all `troll/` code is additive-only. FORK-02: `nautilus_trader` is used as a library only (domain types + `ParquetDataCatalog` API); never instantiate `TradingNode` or `DataEngine` in `troll/` code (names them explicitly, per Success Criterion 2). Briefly state WHY: the live `DataEngine` has a documented unbounded-queue-growth + shutdown-wedge bug that OOM-crashed an earlier recorder.

    Design Principles — DESIGN-01: enforce YAGNI — no abstractions, interfaces, factories, or config for values that don't change; no scaffolding for hypothetical future use. DESIGN-02: decouple at natural component seams (collector / book features / strategy / backtest); components depend on shared data types, not each other's internals. DESIGN-03: prefer deletion over addition when simplification is possible; boring over clever.

    Memory Discipline — MEM-01: never load full catalog slices into memory; name the banned anti-pattern literally as `catalog.trade_ticks()` with no time bounds, and mandate time-bounded queries or `BacktestDataConfig` streaming as the alternative (per Success Criterion 3). MEM-02: non-configured coins are in-memory rolling-window only; no unbounded accumulation. MEM-03: use generator/iterator patterns for large data pipelines; do not materialize full instrument sets into lists.

    Testing — TEST-01: tests are REQUIRED for any function doing financial calculations (OFI, imbalance, microprice, precision conversion) and for integration paths that touch Nautilus types or the catalog. TEST-02: trivial glue code (config parsing, logging setup, simple data routing) does NOT require tests — YAGNI applies to tests too. State the required-vs-optional split plainly enough to apply without a judgment call (per Success Criterion 5). TEST-03: test style — never mock Nautilus internals (use real objects or skip the test); pytest, minimal fixtures, one assertion per logical claim.

    Code Readability — READ-01: functions under ~30 lines; names that explain intent; no clever one-liners that need decoding. READ-02: comments explain WHY (hidden constraint, subtle invariant, workaround), not WHAT. READ-03: type hints required on all function signatures in `troll/` code.

    Nautilus Usage Patterns — NAUT-01: document the `Price(decimal, precision)` silent-precision bug and mandate `Decimal.scaleb()` + `Price.from_raw()` / `Quantity.from_raw()` for any re-stamping operation (per Success Criterion 4); include the concrete failing example from root CLAUDE.md (`Price(Decimal("61090.59855"), 16)` returns a wrong value). NAUT-02: all data written via `ParquetDataCatalog.write_data()`; no hand-rolled Parquet schemas. NAUT-03: backtests use `BacktestNode` + `BacktestDataConfig`; no custom simulation engine; reference strategies via `ImportableStrategyConfig` by string path for sweeps.

    Keep it lazy and skimmable — bullets over prose, no filler. This is an instruction file Claude will load, not a tutorial.
  </action>
  <verify>
    <automated>test -f troll/CLAUDE.md && for h in "## Fork Safety" "## Design Principles" "## Memory Discipline" "## Testing" "## Code Readability" "## Nautilus Usage Patterns"; do grep -qF "$h" troll/CLAUDE.md || { echo "MISSING heading: $h"; exit 1; }; done && grep -qF 'nautilus_trader/' troll/CLAUDE.md && grep -qF 'crates/' troll/CLAUDE.md && grep -qF 'TradingNode' troll/CLAUDE.md && grep -qF 'DataEngine' troll/CLAUDE.md && grep -qF 'catalog.trade_ticks()' troll/CLAUDE.md && grep -qF 'BacktestDataConfig' troll/CLAUDE.md && grep -qF 'Decimal.scaleb()' troll/CLAUDE.md && grep -qF 'Price.from_raw()' troll/CLAUDE.md && grep -qF 'Quantity.from_raw()' troll/CLAUDE.md && grep -qF 'Price(decimal, precision)' troll/CLAUDE.md && grep -qF 'ParquetDataCatalog.write_data()' troll/CLAUDE.md && grep -qF 'BacktestNode' troll/CLAUDE.md && grep -qF 'ImportableStrategyConfig' troll/CLAUDE.md && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `troll/CLAUDE.md` exists (file present).
    - File contains all six headings: `## Fork Safety`, `## Design Principles`, `## Memory Discipline`, `## Testing`, `## Code Readability`, `## Nautilus Usage Patterns`.
    - Fork safety section names `nautilus_trader/` and `crates/` as off-limits and names `TradingNode` and `DataEngine` as banned in troll/ (Success Criterion 2).
    - Memory section contains the literal anti-pattern `catalog.trade_ticks()` and the alternative `BacktestDataConfig` (Success Criterion 3).
    - Nautilus section contains `Price(decimal, precision)`, `Decimal.scaleb()`, `Price.from_raw()`, and `Quantity.from_raw()` (Success Criterion 4).
    - Testing section states both that financial-calculation/Nautilus-integration code requires tests AND that trivial glue does not (Success Criterion 5) — both claims present.
    - Nautilus section contains `ParquetDataCatalog.write_data()`, `BacktestNode`, and `ImportableStrategyConfig`.
    - The verify command exits 0 printing `OK`.
  </acceptance_criteria>
  <done>
    `troll/CLAUDE.md` exists with all 6 category sections, and all 17 requirements (FORK-01/02, DESIGN-01/02/03, MEM-01/02/03, TEST-01/02/03, READ-01/02/03, NAUT-01/02/03) are each represented by an explicit actionable rule. The verify command exits 0.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| (none) | This phase writes a single static documentation file (`troll/CLAUDE.md`). No untrusted input is parsed, no network/process boundary is crossed, no packages are installed, no code executes. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01-01 | Tampering | troll/CLAUDE.md content | accept | Documentation-only artifact under version control; any incorrect rule is caught by code review and git history. No runtime exposure. |
</threat_model>

<verification>
- `troll/CLAUDE.md` exists and is non-empty.
- All six `##` category headings present in order.
- Every literal identifier required by the success criteria is present (see Task 1 verify command).
- Spot-read confirms each of the 17 requirement IDs is represented by a rule (no requirement silently dropped).
</verification>

<success_criteria>
- [ ] `troll/CLAUDE.md` exists with sections covering all 6 rule categories (Success Criterion 1).
- [ ] Fork safety section names `nautilus_trader/`, `crates/`, and bans `TradingNode`/`DataEngine` in troll/ (Success Criterion 2).
- [ ] Memory section names `catalog.trade_ticks()` anti-pattern and mandates `BacktestDataConfig`/time-bounded queries (Success Criterion 3).
- [ ] Nautilus section documents the `Price(decimal, precision)` bug and mandates `Decimal.scaleb()` + `Price.from_raw()`/`Quantity.from_raw()` (Success Criterion 4).
- [ ] Testing section distinguishes required vs optional tests actionably (Success Criterion 5).
- [ ] All 17 requirement IDs represented; verify command exits 0.
</success_criteria>

<output>
Create `.planning/phases/01-project-guardrails/01-SUMMARY.md` when done.
</output>
