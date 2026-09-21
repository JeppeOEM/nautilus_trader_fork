# Story 24.4: `research/` as a pure consumer, with its broken tests repaired

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 24). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 24.4".

## Story

As a strategy researcher,
I want the backtest strategies, runners and notebooks in one context that only reads the catalog, the ranking history and the watchlist API,
So that research can never leak a computation back into the live path, and its test suite runs green again.

## Acceptance Criteria

1. **Given** `ml_signals/strategies/*`, `ml_signals/{run_backtest,watchlist}.py`, `ml_signals/BACKTESTING.md`, `ml_signals/backtest.ipynb`, `dydx_collector/notebooks/`
**When** the story ships
**Then** `platform/research/` holds `strategies/`, `run_backtest.py`, `watchlist.py` (HTTP to `/api/rankings` only; `test_boundaries.py` asserts no `data_api` import), `notebooks/` and `BACKTESTING.md`; backtests still reference strategies by `ImportableStrategyConfig` string path (the paths change to `research.strategies...` and the docs say so); every research catalog read goes through `kernel.catalog_files` or `BacktestDataConfig`; research computes no rolling metric of its own (pct/volatility come from `metrics.db` via the ranking query service)

2. **Given** `ml_signals/tests/{test_snapshot_backtest_node,test_timeframe_backtest,test_watchlist_multi_coin_backtest}.py` fail at collection (`from ml_signals import backtest_dydx` while the module lives in `strategies/`) and `test_ofi_strategy*.py` fail on a stale `ma_period` keyword
**When** the story ships
**Then** all five modules are repaired against the real strategy API (never by deleting a test or loosening an assertion), run in `make test` under `research/tests`, and their pass is recorded in the story's Completion Notes together with the root cause of each break (TEST-04)

3. **Given** MR2 and MR4
**When** the story is merged
**Then** the moved `ml_signals` modules are pure re-export shims with `REMOVE_AFTER = "25-2-..."` (the `ml_signals` package is retired for good in Story 25.2), the collector image `COPY`s `research`, the Makefile test lists include `research/tests`, and `platform/CLAUDE.md` NAUT-03 and the README's backtest section cite the new paths

## Tasks / Subtasks

- [ ] Task 1 — `platform/research/` (AC: #1)
  - [ ] Move `ml_signals/strategies/` → `research/strategies/`, `run_backtest.py`, `watchlist.py`, `BACKTESTING.md`, `backtest.ipynb`, `dydx_collector/notebooks/` → `research/notebooks/`. Update every `ImportableStrategyConfig` string path (`research.strategies.<module>:<Class>`), the README backtest section and `platform/CLAUDE.md` NAUT-03. Catalog reads via `kernel.catalog_files` or `BacktestDataConfig`; pct/volatility via `ranking`'s query service (`ranking_engine.metrics_store.history/nearest` until 25.2 — legacy edge listed).
- [ ] Task 2 — repair the five broken test modules (AC: #2)
  - [ ] `test_snapshot_backtest_node.py`, `test_timeframe_backtest.py`, `test_watchlist_multi_coin_backtest.py`: fix the imports to the `strategies` package (root cause: modules moved in commit `1031008cdd`, tests never updated); `test_ofi_strategy.py`, `test_ofi_strategy_indicator_consistency.py`: align with the current `OFIStrategyConfig` fields (find what replaced `ma_period`; never delete an assertion). Record each root cause in Completion Notes. All run under `research/tests` in `make test`.
- [ ] Task 3 — shims, images, lists (AC: #3)
  - [ ] Shims for the moved `ml_signals` modules (`REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"`); `collector.dockerfile` `COPY platform/research ./research`; Makefile lists add `research/tests`.

## Dev Notes

Research is a consumer with no aggregates. The five broken tests predate the migration (root causes in the story ACs); fixing them is in scope because they are research's tests and TEST-04 forbids leaving them. `watchlist.py` uses HTTP, not an import, so the boundary test asserts the absence of a `data_api` import rather than an edge.

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

- Spine sections: AD-D1 research row, AD-6 (inherited), AD-D10 (no recompute)
- Review findings that shaped this story: commit `1031008cdd` (strategies move), test failures recorded in commit `18c12eedf4`'s message
- Code: `ml_signals/strategies/*`, `ml_signals/tests/{test_snapshot_backtest_node,test_timeframe_backtest,test_watchlist_multi_coin_backtest,test_ofi_strategy,test_ofi_strategy_indicator_consistency}.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
