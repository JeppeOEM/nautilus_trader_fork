# Story 25.2: `ranking/` context: `RankingBoard` replaces the module globals

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 25). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 25.2".

## Story

As a trader watching the rankings,
I want the ranking engine to be one aggregate whose every input arrives through a named port,
So that its behaviour is unit-testable, a second copy of a rolling metric can never appear, and a venue's volume source is one adapter.

## Acceptance Criteria

1. **Given** `ranking_engine/{engine,metrics_store,price_series,volatility}.py` with twelve mutable module globals (`engine.py:117-196`), `ml_signals/{metrics_computer,rank_history}.py` and the `catalog_stats` price math (`price_series`, `price_stats_from_series`, `price_stats`)
**When** the story ships
**Then** `platform/ranking/` holds `domain/` (`RankingBoard` owning `mode`, per-instrument `InstrumentMetrics`, `RankingsPublisher`; `RankingMode`, `VolumeReading`, `VolatilityScore`; `price_series.py`, `volatility.py`, `metrics.py` — the pct/volatility math, ranking's alone), `application/` (`ports.py` with `VolumeSource`, `PriceHistory`, `RankingHistory`, `LivePublisher`; `engine.py` with `ingest_snapshot_batch`, `switch_mode`, `volume_cycle`, `slow_loop`, `heartbeat`, constructed at `__main__`) and `infrastructure/` (`redis.py`, `metrics_store.py`, `catalog_prices.py` over `kernel.catalog_files`, `volume_<venue>.py` over `kernel.venue_http` — `engine.py`'s own URL maps, `_USER_AGENT` and timeouts retired); `test_boundaries.py` fails any module-level mutable runtime state in `ranking/`; the `ml_signals` package is deleted (its last shims expire here)

2. **Given** AD-9 and Story 22.10's invariants
**When** `RankingBoard` is tested
**Then** invariant tests cover: both scores present on every row; a row without a fresh USD volume absent from volume mode and present in volatility mode with one `ranking_engine.volume24h` ledger entry per poll; stale instruments aged out; mode global and last-write-wins; publish on change and on heartbeat; and a replay test proves the `rankings:live` payload for a recorded `snapshots:raw` burst is byte-identical before and after the move

3. **Given** SSOT-02
**When** the story ships
**Then** `views` and `research` read `pct_1h`/`pct_24h`/`volatility` only from `rankings:live`/`metrics.db` through the ranking query service (`history`, `nearest`), a grep-based test asserts no second implementation of `price_stats_from_series` exists, and `ranking` parses `snapshots:raw` only through `DydxSecondSnapshot.from_dict`

4. **Given** MR2 and MR4
**When** the story is merged
**Then** `ranking_engine` is a pure re-export shim with `REMOVE_AFTER = "25-4-..."`, compose's `ranking_engine` service runs `python -m ranking`, the collector image `COPY`s `ranking`, the Makefile test lists include `ranking/tests`, `docs/DATA_DICTIONARY.md` §3 and `platform/CLAUDE.md` SSOT-02/"Adding a venue" step 7 cite the new paths, and the parent spine's Deferred entry "`open_interest` vs `volume24h` polling live in different namespaces" is struck as resolved by ownership

## Tasks / Subtasks

- [ ] Task 1 — `platform/ranking/` (AC: #1)
  - [ ] `domain/board.py`: `RankingBoard` with `mode`, `metrics: dict[str, InstrumentMetrics]`, `publisher: RankingsPublisher` (moved), methods `ingest(snapshot)`, `switch_mode`, `current_ranks()`, `ranks_by_iid()`, `age_out(now_ns)`; `InstrumentMetrics` holds the per-instrument `MultiLevelOFI`/`MultiLevelOBI` instances, rolling deque, price series, last-seen, `VolumeReading`. `domain/mode.py`, `domain/volatility.py` (`VolatilityTracker`), `domain/price_series.py` (`PriceSeriesStore`, `_RingBuffer`), `domain/metrics.py` (`price_stats_from_series`, `metrics_computer.compute_snapshot/compute_all` — ranking's alone).
  - [ ] `application/ports.py`: `VolumeSource` (`fetch() -> dict[str, float]`), `PriceHistory` (`series(iid, start_ns) -> list[tuple[int, float]]`), `RankingHistory` (`write/latest/history/nearest/price_near_days_ago`), `LivePublisher`. `application/engine.py`: the loops as methods of a `RankingEngine(board, ports, config)` built in `ranking/__main__.py`; every `_GLOBAL` from `engine.py:117-196` becomes state on the board or the engine. `infrastructure/`: `redis.py`, `metrics_store.py`, `catalog_prices.py` (over `kernel.catalog_files`), `volume_dydx.py`/`volume_bybit.py`/`volume_hyperliquid.py` (pure parsers kept; URL maps, UA, timeouts from `kernel.venue_http`).
  - [ ] Delete `ml_signals/` entirely (its remaining shims expire here); the boundary test's module-level-state check covers `ranking/`.
- [ ] Task 2 — invariant and replay tests (AC: #2)
  - [ ] Port `ranking_engine/tests` to `ranking/tests`; add `RankingBoard` invariant tests per AC #2; record a `snapshots:raw` burst + the resulting `rankings:live` message with the pre-move engine as fixtures, assert byte-identical output after.
- [ ] Task 3 — SSOT-02 (AC: #3)
  - [ ] `views`/`research` read pct/volatility through `ranking.application.queries` (`history`, `nearest`); grep test for a second `price_stats_from_series`; `DydxSecondSnapshot.from_dict` for parsing (`engine.py:566-580` hand-indexing removed).
- [ ] Task 4 — shims, compose, images, lists, docs (AC: #4)
  - [ ] `ranking_engine/` shim package (`REMOVE_AFTER = "25-4-collection-control-plan-intent-vs-applied-set"`); compose `ranking_engine` `command: python3 -m ranking`; `collector.dockerfile` `COPY platform/ranking ./ranking`; Makefile lists add `ranking/tests`; `docs/DATA_DICTIONARY.md` §3, `platform/CLAUDE.md` SSOT-02 + "Adding a venue" step 7; strike the parent spine's "`open_interest` vs `volume24h`" Deferred entry.

## Dev Notes

The twelve module globals are listed in the story AC; `RankingBoard` replaces them and the engine is constructed in `__main__`. The pct/volatility math moves *into* ranking (adversary H4): views and research read the published values. `ml_signals` dies in this story — check `grep -rn ml_signals platform` returns only history notes afterwards.

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

- Spine sections: AD-D10, AD-9 (inherited), SSOT-02
- Review findings that shaped this story: reviews/review-adversary.md H4, M5, M8; reviews/review-versions.md L-4
- Code: `ranking_engine/engine.py:117-196,146-162,566-580`, `ranking_engine/{metrics_store,price_series,volatility}.py`, `ml_signals/{metrics_computer,rank_history,catalog_stats}.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
