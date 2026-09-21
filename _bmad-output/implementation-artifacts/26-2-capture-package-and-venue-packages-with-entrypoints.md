# Story 26.2: `capture/` package and `capture/venues/<v>/` with new entrypoints

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 26). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 26.2".

## Story

As a maintainer adding a fourth venue,
I want capture and its venue packages laid out as the spine's tree, with a venue being client + trade history + policies + config + entrypoint,
So that "Adding a venue" is a recipe over named files, and no `Collector` subclass exists anywhere.

## Acceptance Criteria

1. **Given** `collector_core/` (post-26.1), `dydx_collector/`, `bybit_collector/`, `hyperliquid_collector/`
**When** the story ships
**Then** `platform/capture/{domain,application,infrastructure}/` and `capture/venues/{dydx,bybit,hyperliquid}/` with `client.py`, `trade_history.py`, `policies.py`, optional `open_interest.py`/`book_snapshot.py`, `config.py` and `__main__.py` exist as the Structural Seed lists; each `__main__.py` is the composition root that builds the client, policies, adapters (`ArchiveWriter`, `LiveStream`, candles `SecondSink`, `Notifier`), the collection-control loops for dYdX, and calls `run_forever`; compose `command:` lines become `python -m capture.venues.<venue>`; `docker-compose.yml`'s `collector` service is renamed `dydx_collector` for symmetry only if the operator checklist records the container-name change, otherwise left as is (decide in the story, record the decision)

2. **Given** MR2 and MR4
**When** the story is merged
**Then** `collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector` are pure re-export shim packages with `REMOVE_AFTER = "26-3-..."`, the collector image `COPY`s `capture`, the Makefile test lists include `capture/tests` and `capture/venues/*/tests`, `test_images.py` and `test_boundaries.py` pass with the full AD-D2 graph active (no unmoved package remains), `platform/CLAUDE.md`'s "Adding a venue" recipe is rewritten over the new files (steps 1–8, same evidence requirements, `capture/venues/<v>/trade_history.py` and `policies.py` added), `ARCHITECTURE.md`'s module map and diagram show the target tree, and `docs/DATA_DICTIONARY.md` §1 cites `capture/`

3. **Given** the deployed collectors
**When** the story is deployed
**Then** `docs/DEPLOY_CHECKLIST.md` gains the redeploy order (all three collectors in one `make redeploy-all`, Dozzle check, `GET /api/errors` flat) as operator actions, and the story parks `awaiting-operator` with those actions

## Tasks / Subtasks

- [ ] Task 1 — package move (AC: #1)
  - [ ] `git mv collector_core platform/capture` reshaped into `capture/{domain,application,infrastructure}/` per the spine's Structural Seed; venue packages → `capture/venues/{dydx,bybit,hyperliquid}/` with `client.py`, `trade_history.py`, `policies.py`, `open_interest.py` (dYdX poll, Bybit poll), `book_snapshot.py` (HL), `config.py`, `__main__.py` (composition root: client, policies, `ArchiveWriter`, `LiveStream`, candles `SecondSink`, `Notifier`, collection-control loops for dYdX, `run_forever`). Compose `command: python3 -m capture.venues.<venue>`; decide and record whether the `collector` service is renamed (container name `dydx-collector` is an operator-visible change).
- [ ] Task 2 — shims, images, lists, docs, recipe (AC: #2)
  - [ ] Shim packages `collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector` (`REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"`); `collector.dockerfile` `COPY platform/capture ./capture`; Makefile lists (`capture/tests`, `capture/venues/*/tests`); remove the last `LEGACY_EDGES_UNTIL` rows so the full AD-D2 graph is active; rewrite `platform/CLAUDE.md` "Adding a venue" over the new files; `ARCHITECTURE.md` module map + diagram; `docs/DATA_DICTIONARY.md` §1.
- [ ] Task 3 — deploy (AC: #3)
  - [ ] `docs/DEPLOY_CHECKLIST.md`: redeploy order for the three collectors; park `awaiting-operator` with those actions in `operator_actions:`.

## Dev Notes

Mechanical move on top of 26.1. Composition roots are the venue `__main__.py` files; `test_boundaries.py` whitelists them. The `collector` compose service name is operator-visible (container `dydx-collector`, Dozzle, `~/.zshrc` helpers) — renaming it is optional and must be recorded either way.

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

- Spine sections: AD-D1, AD-D2, AD-D12, Structural Seed
- Review findings that shaped this story: reviews/review-rubric.md M1
- Code: `docker-compose.yml:69,93,116,149`, `collector.dockerfile`, `platform/CLAUDE.md` 'Adding a venue'
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
