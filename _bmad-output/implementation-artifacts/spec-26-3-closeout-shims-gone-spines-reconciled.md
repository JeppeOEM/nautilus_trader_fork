---
title: 'Story 26.3: Closeout: last shims gone, spines reconciled, guardrails permanent'
type: 'refactor'
created: '2026-09-28'
baseline_revision: 'de73ac829522fa4e2b7334aca4f9d1647b6f94eb'
final_revision: 'b63ec639e190c0ea8a9712aa10f0716bacdfad98'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/26-3-closeout-shims-gone-spines-reconciled.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-26-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The DDD migration (Epics 23–26) is functionally done, but four Story 26.2 re-export shim packages (`collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector`), the migration-era exemption tables in `test_boundaries.py`, stale legacy-package names across live docs/comments/frontend copy, and both architecture spines' `[TARGET]`/`Today`/`troll/` content still describe the pre-migration tree.

**Approach:** Delete every shim and the shim/legacy machinery in the guardrail tests, re-word or re-point every remaining legacy-name reference, lock the result with a permanent legacy-name test whose only allowances are the frozen published-language identifiers (AD-D12), reconcile both spines against the code, and re-record the hot-path baseline from the final tree.

## Boundaries & Constraints

**Always:**
- AD-D12 published language stays byte-identical: compose service names (`collector`, `bybit_collector`, `hyperliquid_collector`, `ranking_engine`, `live-paper`, ...), `ERROR_LEDGER_SERVICE` values and ledger file names, ledger site names (`ranking_engine.*`), env var names, the dYdX plan bind mount `/app/dydx_collector/config.toml`, host bind paths under `platform/data/` (incl. `data/live_paper`), Redis payloads, Parquet/SQLite/TOML schemas. These are the ONLY legacy-token hits AC #1's grep may still return, each matched by an explicit allowlist pattern with its reason.
- Same commit updates `platform/CLAUDE.md`, `platform/ARCHITECTURE.md`, `platform/README.md`, `docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose, both Makefile test lists, and root `CLAUDE.md`/`_bmad-output/project-context.md` mentions of the shims.
- Warnings are failures (TEST-04); real Nautilus objects in tests; FORK-01 (never touch `nautilus_trader/` or `crates/`).
- VPS steps go to `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" as one entry headed `26-3` (OPS-01); story finalizes `done`.

**Block If:**
- Removing a shim requires changing any frozen published-language item listed above.
- The re-recorded hot-path baseline exceeds the Story 23.1 baseline's allocation figures (the ingest path was supposed to be unchanged since 26.2).

**Never:**
- Write or revert `_bmad-output/implementation-artifacts/sprint-status.yaml` (orchestrator-owned; AC #3's "Epics 23–26 done" is the orchestrator's bookkeeping, recorded in the Auto Run Result as such).
- Rename compose services or ledger sites (post-migration rename is a spine Deferred entry with its upgrade path, not this story).
- Add a new re-export shim, `_MOVED_NAMES`/`_REPLACED_NAMES` table or `LEGACY_*` exemption.
- Edit historical planning artifacts other than the two spines (`troll/` citations in old stories/specs stay).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean tree | `git grep` of the AC #1 pattern over `platform/` minus `docs/`, `.planning/` | every hit matches an allowlisted published-language pattern | none |
| Stray legacy name | a new comment/import naming `collector_core.x` or `live_paper/` | legacy-name test fails naming file:line | fix the reference |
| Old import path | `import dydx_collector.client` | `ModuleNotFoundError` (package gone) | none |
| dYdX plan in compose | collector/archive under compose | plan read from `/app/dydx_collector/config.toml` exactly as before | none |

</intent-contract>

## Code Map

- `platform/{collector_core,dydx_collector,bybit_collector,hyperliquid_collector}/` -- the 26.2 shims to delete; `dydx_collector/config.toml` is the 0-byte placeholder under the frozen mount (deleted: the dYdX plan is operator data); untracked `live_paper/`, `*/tests/__pycache__` leftovers removed from disk.
- `platform/capture/venues/dydx/config.py:29-38` -- `CONFIG_PATH` default resolves via `dydx_collector/`; becomes the literal frozen mount path `/app/dydx_collector/config.toml` (`DYDX_PLAN_PATH` override unchanged), so a missing mount fails loudly; `archive/tools/measure_lag.py` reads the dYdX plan the same way (review pass 1 replaced the drafted placeholder-move, which fell back to an empty plan).
- `platform/tests/test_namespace.py` -- shim scanner, `old.X is new.X`, expiry-vs-sprint-board, `_REPLACED_NAMES` tests: retired; namespace-dir, stdlib-`platform` and one-Arrow-registration-per-kernel-class checks stay.
- `platform/tests/test_boundaries.py:131-158,206-230,291-415,701-760,784,875-890,1166-1352,1454-1594` -- `LEGACY_*` maps/tables, shim exemption, expiry tests, legacy package names in forbidden sets: deleted; every module maps to a context by its top-level package.
- `platform/tests/test_images.py:492`, `test_skew_constants.py:52-55` -- shim/placeholder paths to repoint.
- `platform/live_paper.dockerfile` -- renamed `platform/bots.dockerfile` (not published language; compose `dockerfile:`, Makefile, docs follow). Container-side paths `/app/live_paper/...` in compose/`bot_tui/app.py` repointed (`/app/data/live_paper` for fills, a non-package path for bot_tui's read-only strategy mount).
- `platform/docker-compose.yml`, `Makefile`, `collector.dockerfile`, `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `bots/{README,DEPLOY_CHECKLIST}.md`, `frontend/src/pages/docs/*.ts`, `frontend/openapi.json`, and ~60 module docstrings/comments (see `git grep` list) -- legacy names re-worded ("moved in Story 25.2") or repointed.
- `platform/tests/fixtures/hotpath_baseline.json`, `docs/DATA_INTEGRITY_AUDIT.md` D-65 -- re-recorded baseline + final numbers.
- `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` -- `[TARGET]` (4), AD-D1 `Today` column, `troll/` citations, Deferred list.
- `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md` -- `troll/` citations (20), resolved Deferred entries (writer→reader imports, namespace items) struck `[amended 2026-09-28: Story 26.3]`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/{collector_core,dydx_collector,bybit_collector,hyperliquid_collector}/` -- `git rm -r`; delete the dYdX placeholder; `capture/venues/dydx/config.py` defaults to the frozen mount path (compose unchanged); `collector.dockerfile` drops the four `COPY` lines -- AC #1.
- [x] `platform/tests/test_namespace.py` -- retire all shim machinery and tests, keep the AD-D13 and Arrow-registration checks (registration counted over kernel modules only) -- AC #1.
- [x] `platform/tests/test_legacy_names.py` -- new permanent guard: run the AC #1 `git grep` (skip with reason when not in a git checkout, e.g. the image), assert every hit matches one allowlisted published-language pattern; plus a unit test that the matcher rejects a module-path mention and accepts each allowance -- AC #1.
- [x] every other `git grep` hit (code, tests, docs outside `platform/docs/`, frontend, notebook, compose, Makefile, dockerfiles) -- re-word/repoint; rename `live_paper.dockerfile` → `bots.dockerfile` and repoint container-side `/app/live_paper/*` paths; move `bots/DEPLOY_CHECKLIST.md`'s Story 25.3 rollout history into `platform/docs/DEPLOY_CHECKLIST.md` if it must keep old paths -- AC #1.
- [x] `platform/tests/test_boundaries.py` -- delete every `LEGACY_*` table, the shim exemption and the expiry/"still needed" tests; drop legacy package names from forbidden sets and assertions -- AC #3.
- [x] root `CLAUDE.md`, `_bmad-output/project-context.md`, `platform/CLAUDE.md` -- remove "re-export shims until Story 26.3" wording -- AC #1.
- [x] both spines -- reconcile per AC #2; add Deferred entry for the frozen legacy-named published identifiers (upgrade path: a post-migration rename story with operator actions); run `lint_spine.py` clean and a subagent version-lens review; memlog the outcome -- AC #2.
- [x] `platform/tests/fixtures/hotpath_baseline.json` -- rebuild collector image, `make hotpath-baseline`, then `make test` x1 more; record final numbers + 26.3 run on D-65 -- AC #3.
- [x] `platform/docs/DEPLOY_CHECKLIST.md` -- "Deferred operator actions" entry `26-3` (pull, rebuild `collector`/`live-paper`/`bot_tui` images, recreate services since the dockerfile/mount targets changed; nothing moves on the host) -- OPS-01.

**Acceptance Criteria:**
- Given the final tree, when `grep -rln REMOVE_AFTER platform --include='*.py'` runs, then it returns nothing, and `test_legacy_names.py` passes with only allowlisted published-language hits.
- Given `make test` and `make test-live-paper`, when run on the rebuilt images, then both pass with no warnings, `test_images.py`/`test_hotpath.py` still run in `make test`, and the hot-path allocations are <= the Story 23.1 baseline.
- Given both spines, when inspected, then no `[TARGET]` lacks a reason+Deferred entry, the AD-D1 table has no `Today` column, no `troll/` citation remains outside historical-note context, resolved parent Deferred items are struck with amendments, and `lint_spine.py` exits clean.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 1, medium 3, low 9)
- defer: 0
- reject: 8: (high 0, medium 3, low 5)
- addressed_findings:
  - `[high]` `[patch]` dYdX `CONFIG_PATH` fell back to a committed empty placeholder when `DYDX_PLAN_PATH` was unset (silent empty plan, plan-store writes into the placeholder) and contradicted AD-D12's "bind mount, not env var": default restored to the frozen mount path, placeholder deleted, compose env line dropped; verified in the rebuilt image (29 instruments loaded through the mount).
  - `[medium]` `[patch]` `archive/tools/measure_lag.py` read the empty placeholder for dYdX (it read the mounted plan before): reads `DYDX_PLAN_PATH`/the mount path; a missing plan is a usage error (exit 2); tests added.
  - `[medium]` `[patch]` `test_legacy_names.py` accepted `import bybit_collector`, `from ranking_engine import`, `-m ranking_engine`, `<dir>/ranking_engine`: a stale-context pattern now overrides the service-name allowances; five negative cases added; two slash-joined service lists reworded.
  - `[medium]` `[patch]` the guard skipped in `make test` (no git in the image): falls back to walking the mounted checkout's text files; host test proves `git grep` hits are a subset of walk hits (157 = 157); verified running in the image.
  - `[low]` `[patch]` `git grep` binary-file lines would crash the split: `-I` added.
  - `[low]` `[patch]` spine: pyarrow outside-the-lock drift given a Deferred entry; stale "story-file headers vs `awaiting-operator`" bullet amended (board says `done`).
  - `[low]` `[patch]` DEPLOY_CHECKLIST 26-3 entry: literal "this commit" replaced by the story key + `git log --grep` pointer; pre-pull grep extended to `~/.zshrc` and operator scripts; plan item rewritten for the restored default.
  - `[low]` `[patch]` provenance rewordings that were false ("moved from capture's ... in Story 25.1", before `capture/` existed) corrected to "the collector core".
  - `[low]` `[patch]` `ARCHITECTURE.md` "Two-image split" corrected; `project-context.md` `pony` line made accurate; five new code lines over 100 columns rewrapped.

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 1, medium 4, low 4)
- defer: 0
- reject: 6: (high 0, medium 1, low 5)
- addressed_findings:
  - `[high]` `[patch]` `archive/tools/measure_lag.py`'s dYdX default read the mounted plan's `{ id = ... }` tables as ids, so `iid not in known` raised `TypeError: unhashable dict` (the first review's fixture used a flat list): table entries now yield their `id`; the fixture uses the real shape, and a new test reads the committed `platform/data/dydx_config.toml`.
  - `[medium]` `[patch]` `measure_lag` gave the dYdX `DYDX_PLAN_PATH` hint for a missing Bybit/Hyperliquid config and tracebacked on an unreadable or malformed plan: the hint is dYdX-only and `OSError`/`TOMLDecodeError` are usage errors (exit 2, tested); an empty `DYDX_PLAN_PATH` counts as unset there and in `capture/venues/dydx/config.py` (it named the working directory).
  - `[medium]` `[patch]` `test_legacy_names.py` let stale references through (`ranking_engine.redis.client`, `/app/dydx_collector/config.tomlx`, `` `bybit_collector`.client ``, backslash paths, `import_module("ranking_engine")`): every allowance now refuses a path/module continuation even behind a quote or backtick, the mount path is anchored, and the stale-context pattern covers backslashes and dynamic imports; nine negative cases were added.
  - `[medium]` `[patch]` the image's fallback walk read a different file set from `git grep` (`.mjs`/`.sql`/`.jsonl`/`.svg`/`.gitignore`/`.env-example` and `data/*.toml` were missed, and `splitlines()` renumbered lines): the walk covers them and splits on `\n` like git, and the new `test_walk_reads_every_tracked_text_file` fails the day a tracked text file escapes the walk.
  - `[medium]` `[patch]` `test_no_module_is_a_migration_shim` only read module-level `DeprecationWarning` calls, so a lazy `__getattr__` shim or a `FutureWarning` shim passed: it walks the whole AST again and accepts all three deprecation categories (tested).
  - `[low]` `[patch]` DDD spine citations `kernel/second_snapshot.py:103`, `capture/venues/dydx/config.py:43`, `test_namespace.py:89` and `test_legacy_names.py:59-81` were off; they are re-pointed to :104, :45, :98 and :59-103, and `lint_spine.py` is clean on both spines.
  - `[low]` `[patch]` the DEPLOY_CHECKLIST 26-3 pre-pull grep missed `/app/live_paper/...` and `live_paper.dockerfile`: both are added with their new names, and the `ModuleNotFoundError` claim is qualified (a bare `import dydx_collector` resolves to the plan mount's namespace directory in `collector`).
  - `[low]` `[patch]` `platform/CLAUDE.md` "Adding a venue" step 4 ended with "silently falls back to the baked file", which contradicted the new dYdX wording and matches no loader: reworded (a path nothing mounts fails at start; only an *unset* env var reads the baked file).
  - `[low]` `[patch]` `research/tests/test_ad8_boundary.py`'s docstring claimed `views/tests/test_ad8_boundary.py` covers `catalog_stats`/`metrics_computer`: it now names their successors and the guards that really cover them.

## Design Notes

Why an allowlist test instead of renaming everything: AD-D12 freezes compose service names, ledger identity and the dYdX mount "for the whole migration", and this story is the migration's last step; renaming `ranking_engine` would split the durable error ledger (`ranking_engine.jsonl`) and the `archive` cross-check history. The allowlist is narrow patterns, e.g. `/app/dydx_collector/config\.toml`, `\bdata/live_paper\b`, `\b(bybit_collector|hyperliquid_collector|ranking_engine)\b(?![/\w])` excluding module-dot paths except known ledger-site suffixes — so a prose "moved from `ranking_engine/engine.py`" still fails.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. tests capture kernel observability -q` -- expected: pass, no warnings
- `docker compose -f platform/docker-compose.yml build collector live-paper && make -C platform test && make -C platform test-live-paper` -- expected: pass
- `uv run .claude/skills/bmad-architecture/scripts/lint_spine.py --workspace <ddd spine folder>` -- expected: clean
- `cd platform && ruff check . && ruff format --check .` -- expected: clean


## Auto Run Result

Status: done

**Summary.** This was a follow-up review of Story 26.3, the closeout of the DDD migration: the four re-export shim packages are deleted, `test_legacy_names.py` is the permanent guard, both spines are reconciled and the hot-path baseline is re-recorded. The first run's work stands. This pass fixed one real production-tool bug and tightened both permanent guards so they catch what they claim to catch.

**Files changed in this pass:**
- `platform/archive/tools/measure_lag.py`: the dYdX plan's table entries yield ids; venue-correct usage errors for a missing, unreadable or malformed plan; an empty `DYDX_PLAN_PATH` counts as unset.
- `platform/archive/tests/test_measure_lag.py`: the real plan shape, a test against the committed plan, and a test that a malformed plan is a usage error.
- `platform/capture/venues/dydx/config.py`: an empty `DYDX_PLAN_PATH` counts as unset.
- `platform/tests/test_legacy_names.py`: anchored allowances, broader stale-context detection, a walk that matches git's file set and line numbering, and a walk-coverage test.
- `platform/tests/test_namespace.py`: the no-shim scan walks the whole AST and covers every deprecation category.
- `platform/research/tests/test_ad8_boundary.py`: the docstring names the guards that actually apply.
- `platform/CLAUDE.md`: step 4's config-path sentence corrected.
- `platform/docs/DEPLOY_CHECKLIST.md`: the 26-3 pre-pull grep and the `ModuleNotFoundError` note corrected.
- The DDD `ARCHITECTURE-SPINE.md`: four citations re-pointed.

**Review findings:** 9 patches applied (1 high, 4 medium, 4 low), 0 deferred, 6 rejected. The rejected findings:
- pandas 3.0.4 vs `<3.0.0`: already in the deferred ledger.
- The dYdX skew guard gap: already a documented `Known limit`.
- The `bots/__main__.py` fills path spelled as one string: fine.
- The `.tests.` exclusion that was removed: no test module trips the scan.
- A crash on non-UTF-8 `git grep` output: speculative.
- A rollback step in the operator entry: out of scope.

**Verification:**
- `make test` on the rebuilt collector image: 2024 passed, 2 skipped. The second skip is the new walk-coverage test, which needs git and the image has none.
- `make test-live-paper`: 496 passed, 2 skipped, 2 deselected.
- Warnings in both runs are only the pre-existing pandas-4 `utcnow`/`'d'` and Starlette httpx ones, both already in the deferred-work ledger.
- Host run of `tests archive/tests capture kernel observability research/tests`: 1193 passed, no warnings.
- `ruff check` and `ruff format --check` are clean on the changed Python files.
- `lint_spine.py` is clean on both spines.

**Not done by this run (by design):** `sprint-status.yaml` belongs to the orchestrator, which records AC #3's "Epics 23–26 done" there. This session never wrote it.

**Residual risks:**
- The container-side mount targets changed in this story, so `live-paper` and `bot_tui` must be recreated. The operator entry covers this.
- The legacy-name allowlist is still spelling-based: a future published rename must update it in the same commit.
- The pandas/pyarrow version drift is recorded but not fixed.

**Follow-up review recommended:** false. The fixes are localized, each is covered by a new test, and the one production-facing change is an operator CLI tool that is now tested against the real plan file.
