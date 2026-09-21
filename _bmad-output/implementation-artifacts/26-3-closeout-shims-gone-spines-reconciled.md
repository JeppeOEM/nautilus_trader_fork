# Story 26.3: Closeout: last shims gone, spines reconciled, guardrails permanent

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 26). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 26.3".

## Story

As the platform owner,
I want the migration declared finished only when no shim remains, every `[TARGET]` in the DDD spine reads `[ADOPTED]` with a citation, and every parent Deferred item the migration resolved is struck,
So that the architecture documents describe the code that runs.

## Acceptance Criteria

1. **Given** the shims created by Stories 23.1–26.2
**When** the story ships
**Then** no module carrying `REMOVE_AFTER` exists under `platform/`, `test_namespace.py`'s shim assertions are retired with it, and `git grep -n "ml_signals\|collector_core\|dydx_collector\|bybit_collector\|hyperliquid_collector\|ranking_engine\|live_paper\|common\.venues"` over `platform/` (excluding `docs/` history notes and `.planning/`) returns nothing

2. **Given** the DDD spine's `[TARGET]` markers and `Today` columns and the parent spine's Deferred list
**When** the story ships
**Then** every `[TARGET]` is re-verified against the code and rewritten `[ADOPTED]` with `path:line` citations (or left `[TARGET]` with the reason and a Deferred entry), the `Today` columns are replaced by the target paths, the parent spine's resolved Deferred entries are struck with amendments, `troll/...` citations in both spines are re-pointed to `platform/...`, and the spine's Reviewer Gate (`lint_spine.py` + the version lens) is re-run clean

3. **Given** the guardrails
**When** the story ships
**Then** `test_boundaries.py`'s legacy map is deleted (every module is in a context), `test_images.py` and `test_hotpath.py` stay in `make test`, the hot-path baseline is re-recorded from the final tree, `docs/DATA_INTEGRITY_AUDIT.md` carries the final numbers, and `_bmad-output/implementation-artifacts/sprint-status.yaml` marks Epics 23–26 `done`

## Tasks / Subtasks

- [ ] Task 1 — remove every shim (AC: #1)
  - [ ] `grep -rn "REMOVE_AFTER" platform` → delete each module/package; retire the shim assertions in `test_namespace.py`; run the grep in AC #1 and fix any straggler (notebooks, scripts, docs).
- [ ] Task 2 — reconcile both spines (AC: #2)
  - [ ] For every `[TARGET]` in the DDD spine: verify against code, rewrite `[ADOPTED]` with `path:line` or keep `[TARGET]` + Deferred entry; replace `Today` columns; re-point `troll/` citations; strike resolved parent Deferred entries; run `uv run .claude/skills/bmad-architecture/scripts/lint_spine.py --workspace <ddd spine folder>` clean and re-run the version lens (a subagent reviewer as in the 2026-09-21 gate) — record the outcome in the memlog (`uv run _bmad/scripts/memlog.py append ... --type event`).
- [ ] Task 3 — guardrails permanent (AC: #3)
  - [ ] Delete `LEGACY_MODULE_TO_CONTEXT`/`LEGACY_EDGES_UNTIL` from `test_boundaries.py` (every module is in a context); keep `test_images.py`, `test_hotpath.py` in `make test`; re-record the baseline; final numbers in `docs/DATA_INTEGRITY_AUDIT.md`; `sprint-status.yaml` epics 23–26 `done`.

## Dev Notes

Closeout is documentation-grade rigor: the spine must describe the code that runs (`[ADOPTED]` with citations), and every guardrail must be permanent. Re-running the reviewer lens is required, not optional.

### Migration rules that bind every story (spine AD-D12, MR1/MR2/MR4/MR14)

- **Deployable alone.** Frozen for the whole migration: Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`), the SQLite/TOML store schemas, compose service names, env vars, the `platform/data/` bind mounts. A replay/fixture test proving a payload or file is byte-identical before and after the move is the standard evidence.
- **Shims.** The old import path stays as a pure re-export: `from <new> import <names>` + `warnings.warn(..., DeprecationWarning)` + `REMOVE_AFTER = "<story key>"`. It defines nothing (a copied class body would register a second Arrow class and break `is` dispatch). Update every in-repo caller in the same story; a `DeprecationWarning` in the test run is a failure (TEST-04). `platform/tests/test_namespace.py` asserts `old.X is new.X`.
- **Same commit:** `platform/CLAUDE.md` citations, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, both Makefile test lists (`test`, `test-live-paper`). `platform/tests/test_images.py` and `test_boundaries.py` (from 23.1) must pass.
- **Layering (AD-D2):** `domain/` imports only stdlib, `kernel/` and `nautilus_trader.model`/`core` types — no I/O, asyncio, Redis, SQLite, Parquet or the Nautilus runtime; `application/` holds `typing.Protocol` ports, services and the asyncio loops; `infrastructure/` implements ports and is imported only by the composition root. No module-level mutable runtime state (AD-D10). Every aggregate/port docstring names the invariant it protects (DESIGN-01).
- **Parent spine:** when this story resolves one of its Deferred items, strike it there with a `[amended <date>: Story <n>]` note (MR14).
- **Project rules:** `platform/CLAUDE.md` DATA-01..08, DATA-07 (no silent skips; `observability.error_ledger.record`), TEST-01..04 (real Nautilus objects, no mocks of internals, warnings are failures), READ-03 (type hints, mypy), SSOT-01..05, MEM-01..03, NAUT-01..03, FORK-01 (never touch `nautilus_trader/` or `crates/`).
- **Working directory:** `platform/` (`cd platform`); tests run as `python3 -m pytest -o addopts="" --rootdir=. <paths> -q`; `make test` runs the Makefile list inside the collector image.

### Project Structure Notes

- Target tree: spine "Structural Seed". Working dir `platform/` (renamed from `troll/` on 2026-09-21; historical docs cite `troll/`). Durable stores under `platform/data/`, never under a package.
- `platform/` is a namespace directory: never add `platform/__init__.py`; never import with a `platform.` prefix (contexts are top-level packages with `platform/` on `sys.path`).

### References

- Spine sections: AD-D12, AD-D13; both spines' Deferred lists
- Review findings that shaped this story: `.claude/skills/bmad-architecture/references/reviewer-gate.md`
- Code: the DDD spine's `[TARGET]` markers and `Today` columns
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
