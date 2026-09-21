# Story 24.2: `views/` read models for both UIs, and the reader-side re-validation removed

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 24). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 24.2".

## Story

As a trader reading the web UI and the TUI,
I want every number both surfaces show to come from one function over one input, and the chart to render what the gate approved,
So that the two UIs can never disagree and the reader never second-guesses the gate.

## Acceptance Criteria

1. **Given** `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `data_api/{live_candles,redis_bus}.py`, the `catalog_stats` series reads (`query_second_snapshots`, `query_second_ohlc`, `second_ohlc_arrays`, `overview_table`) and the inline computations in `data_api/routes/*.py` and `bot_tui/*_state.py`
**When** the story ships
**Then** `platform/views/` holds `ranking_columns.py`, `coin_detail.py`, `chart_series.py`, `indicator_picker.py`, `live_candles.py` (declaring the `BarObserver` `Protocol` and keeping `LiveCandleBus`), `rankings_bus.py`, `preferences.py` (the one loader/saver for `chart_indicators.toml` and `screener_columns.toml`, full-rewrite TOML, key sets frozen) and the catalog series reads over `kernel.catalog_files`; an audit list in the story's Dev Notes names every computation found in `data_api/routes` and `bot_tui` state modules and each is moved into `views/` or shown to be pure formatting; `views` parses `snapshots:raw` only through `DydxSecondSnapshot.from_dict`, and `bot_tui`'s hand-indexed parsing is replaced by the same call

2. **Given** `data_api/routes/snapshots.py:129-133` skips empty-top and `bp >= ap` rows (the AD-3 deviation) and `:137-138` inserts gap markers
**When** the story ships
**Then** the two skips are deleted, the gap-marker insertion (`_SNAPSHOT_GAP_THRESHOLD_MS`) moves into `views/chart_series.py` as a rendering rule with its existing test, a test proves a crossed row written by the gate is returned unchanged by `/api/snapshots`, and the parent spine's Deferred entry "Reader-side crossed-book skip survived the `dashboard` → `data_api` move" is struck with an amendment

3. **Given** AD-D2's graph
**When** `test_boundaries.py` runs after the move
**Then** `views` imports only `kernel`, `observability` and the `candles`/`ranking` query services (`window`, `latest`, `forming_bar`, `verified_status`, `history`, `nearest`), `data_api` and `bot_tui` import `views`, `kernel`, `observability` and `alerting`'s application service only, and no `data_api` route or `bot_tui` module imports `ml_signals`, `collector_core`, `ranking_engine` or `common` directly

4. **Given** MR2 and MR4
**When** the story is merged
**Then** the moved `ml_signals` and `data_api` modules are pure re-export shims with `REMOVE_AFTER = "24-4-..."`, the `data_api` and collector images `COPY` `views`, the Makefile test lists include `views/tests`, the frontend's docs page (`frontend/src/pages/docs/`) and `ARCHITECTURE.md` name `views/`, and SSOT-01..05 in `platform/CLAUDE.md` cite `views/` as the one place

## Tasks / Subtasks

- [ ] Task 1 — audit, then move (AC: #1)
  - [ ] Write the audit list first (Dev Notes): every arithmetic/derivation in `data_api/routes/*.py`, `data_api/ws/live.py`, `bot_tui/*_state.py`, `bot_tui/*_pane.py` (grep for `float(`, `sum(`, `max(`, `/`, `*`, indicator calls). Classify each as *compute* (moves to `views/`) or *format* (stays).
  - [ ] Create `platform/views/`: `ranking_columns.py` (+ `screener_columns_config` merged into `preferences.py`), `coin_detail.py` (the per-coin metric set both UIs show — from `routes/indicators.py`, `bot_tui/coin_detail_state.py`), `chart_series.py` (`chart_data.py`, `book_features.py`, `footprint.py`, the series reads over `kernel.catalog_files`, the gap-marker rule), `indicator_picker.py` (`chart_indicators.py`, `custom_indicators.py`, `chart_indicator_config.py`), `live_candles.py` (`LiveCandleBus` + `BarObserver` `Protocol`: `on_bar(instrument_id, bar_seconds, bar: Bar, ts_ns) -> None`), `rankings_bus.py` (`redis_bus.py`), `preferences.py` (load/save `chart_indicators.toml`, `screener_columns.toml`; key sets asserted unchanged).
  - [ ] `snapshots:raw` parsed only via `DydxSecondSnapshot.from_dict` in `views/` and in `bot_tui` (`coin_detail_state.py`, `ranking_state.py`); grep test `snap\[` outside kernel.
- [ ] Task 2 — remove the reader-side re-validation (AC: #2)
  - [ ] Delete the two skips at `data_api/routes/snapshots.py:129-133`; move gap insertion (`_SNAPSHOT_GAP_THRESHOLD_MS`, `:75,137-138`) into `views/chart_series.py`; test: a crossed row round-trips through `/api/snapshots`; strike the parent Deferred entry.
- [ ] Task 3 — boundary (AC: #3)
  - [ ] Remove the now-illegal legacy edges from `LEGACY_EDGES_UNTIL` for this story; `data_api` and `bot_tui` import only `views`, `kernel`, `observability`, `alerting.application` (alerting is still `data_api/alerts.py` until 24.3 — keep its legacy edge until then).
- [ ] Task 4 — shims, images, lists, docs (AC: #4)
  - [ ] Shims (`REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"`); `data_api.dockerfile` + `collector.dockerfile` `COPY platform/views ./views`; Makefile lists add `views/tests`; frontend docs page data (`frontend/src/pages/docs/{data,kbData}.ts`) and `ARCHITECTURE.md` name `views/`; `platform/CLAUDE.md` SSOT-01..05 cite `views/`.

## Dev Notes

Views is CQRS's query side: it may read every store read-only and call the query services of candles/ranking, and it is where SSOT-01..05 become code. Do the audit list before moving anything — the point is that no computation is left in `data_api/routes` or `bot_tui`. The reader-side skips at `snapshots.py:129-133` are the AD-3 deviation the parent spine tracks; deleting them is a behaviour change on the chart (crossed rows now render) and must be stated in Completion Notes.

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

- Spine sections: AD-D11, AD-D3 (from_dict), AD-D2
- Review findings that shaped this story: reviews/review-adversary.md C4, M1, M8; parent spine AD-3 + Deferred 'Reader-side crossed-book skip'
- Code: `data_api/routes/snapshots.py:75,129-138`, `data_api/{live_candles,redis_bus}.py`, `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `bot_tui/*_state.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
