# Story 24.1: `candles/` context behind capture's `SecondSink` port

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 24). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 24.1".

## Story

As a strategy developer and chart user,
I want the candle store to be its own context that capture feeds through a port with the flushed batch, and the only seconds→bars fold in the platform,
So that a bar is never ahead of the archive, is always rebuildable from seconds, and cannot disagree with the chart's forming candle.

## Acceptance Criteria

1. **Given** `ml_signals/candle_store.py`, `ml_signals/candles.py`, `collector_core/build_candles.py` and the collector's direct `candle_store` calls (`collector_core/collector.py` `_apply_to_candle_store`, `_catch_up_candle_store`, `_candle_prune_loop`)
**When** the story ships
**Then** `platform/candles/` holds `domain/` (`CandleSeries` with the per-instrument watermark and `seconds_observed`/`partial` semantics, `fold_arrays`), `application/` (`apply_seconds` implementing the `SecondSink` `Protocol`, `forming_bar(rows: Sequence[SecondOHLC], bar_seconds) -> Bar | None`, `rebuild_day`, `prune`, the queries `window`/`latest`/`oldest_t`/`watermarks`, and a `VerifiedDays` service exposing `mark_verified`/`verified_status`) and `infrastructure/` (`CandleStore`, the only code that opens `candles_<venue>.db` rw; schema and `verified_days` table byte-identical); `collector_core/ports.py` declares `SecondSink` (the legacy capture module) and the collector calls `self._second_sink.apply(iid, flushed_rows)` only with rows whose `write_data` succeeded (`collector.py:1063-1073` semantics kept, with a test); each venue entrypoint constructs the candles adapter and passes it in, and the candle prune loop runs as a candles process manager started through `extra_loops`; `test_boundaries.py` whitelists the venue entrypoints as composition roots for that wiring

2. **Given** three seconds→bars folds today (`candle_store.fold_arrays`, `candles.aggregate_ohlc`/`candle_dicts_from_snapshots`, and the forming-bar arithmetic in `data_api/live_candles.py`)
**When** the story ships
**Then** exactly two folds exist in `platform/` (`kernel.fold.fold_trades` and `candles.domain.fold_arrays`), `ml_signals/candles.py`'s `aggregate_ohlc`, `candle_dicts_from_snapshots`, `build_candles` and `PARTIAL_OBSERVED_FRACTION` are retired (their callers in `data_api/live_candles.py`, `routes/rankings.py`, `routes/candles.py` call `candles.application.forming_bar`/`window`), and an equivalence test proves that `forming_bar` over a day of recorded seconds equals the stored closed bars for 1 m, 5 m and 1 h (the "source equivalence" of Story 21.2 restated over the single fold)

3. **Given** `compare_klines.py` and `prune_catalog.py` open the candle store themselves (`connect_rw`, `connect_ro` + `verified_status`)
**When** the story ships
**Then** both receive a `VerifiedDays` adapter injected by `nightly.py` and their own CLIs, never a connection; `python -m collector_core.build_candles` becomes `python -m candles.rebuild` with the same arguments (the old module is a shim that forwards `main`), `make nightly` and the README are updated, and the idempotent-rebuild test still passes

4. **Given** MR2 and MR4
**When** the story is merged
**Then** `ml_signals.candle_store`, `ml_signals.candles` and `collector_core.build_candles` are pure re-export shims with `REMOVE_AFTER = "24-3-..."`, every in-repo caller is updated, all three dockerfiles `COPY` `candles`, the Makefile test lists include `candles/tests`, `docs/DATA_DICTIONARY.md` §2.5/§5 and `ARCHITECTURE.md` cite the new paths, and the parent spine's Deferred entry "Writer→reader imports contradict AD-4" is struck as fully resolved

## Tasks / Subtasks

- [x] Task 1 — `platform/candles/` (AC: #1)
  - [x] `domain/candle_series.py`: `CandleSeries` (per instrument × bar_seconds; watermark; `apply(rows) -> applied_count`, exactly-once by watermark; `seconds_observed`/`partial` per `candle_store.py:10,40,59`), `domain/fold.py`: `fold_arrays` moved verbatim from `ml_signals/candle_store.py:138`. Docstrings name the invariants (exactly once; rebuildable from seconds; never ahead of the archive).
  - [x] `application/ports.py`? No — the `SecondSink` `Protocol` lives in capture (`collector_core/ports.py`, new, `apply(iid: str, rows: Sequence[SecondOHLC]) -> int`); `candles/application/sink.py` implements it; `application/queries.py` (`window`, `latest`, `oldest_t`, `watermarks`), `application/forming.py` (`forming_bar(rows, bar_seconds) -> Bar | None` replacing `ml_signals/candles.py` arithmetic), `application/rebuild.py` (from `collector_core/build_candles.py`, CLI `python -m candles.rebuild`), `application/prune.py` (the `_candle_prune_loop` body, `collector.py:1163`, exposed as a coroutine factory), `application/verified_days.py` (`mark_verified`, `verified_status`).
  - [x] `infrastructure/sqlite_store.py`: `CandleStore` = `connect_rw`/`connect_ro`, `_SCHEMA`, `_UPSERT`, `apply_batch`, `mark_verified`, `verified_status`, `prune` — the only rw opener; schema text unchanged (assert against the recorded `_SCHEMA`).
- [x] Task 2 — capture feeds candles through the port (AC: #1)
  - [x] `Collector.__init__(config, client, extra_loops=(), *, second_sink: SecondSink | None = None)`; `_apply_to_candle_store`/`_catch_up_candle_store` call `self._second_sink.apply(iid, rows)` only with rows whose `write_data` succeeded (keep `collector.py:1063-1073` ordering; test it). Remove the direct `candle_store` import and `CANDLES_DB_PATH` handling from the collector; the venue entrypoints (`dydx_collector/collector.py:main`, `bybit_collector/collector.py`, `hyperliquid_collector/collector.py`) build `CandleStore(os.environ["CANDLES_DB_PATH"])` + sink and pass `extra_loops=(candles.application.prune.loop(store), ...)`. Add the three entrypoints to `test_boundaries.py`'s composition-root whitelist.
- [x] Task 3 — exactly two folds (AC: #2)
  - [x] Delete `aggregate_ohlc`, `candle_dicts_from_snapshots`, `build_candles`, `PARTIAL_OBSERVED_FRACTION` from `ml_signals/candles.py` (leave a shim that raises on import of the deleted names with the new location); rewrite the callers in `data_api/live_candles.py:45,220`, `data_api/routes/rankings.py:38,209`, `data_api/routes/candles.py:43-44` to `candles.application.forming_bar`/`window`.
  - [x] Equivalence test: one recorded day of seconds (fixture) → `forming_bar` per closed bucket == stored bars from `rebuild` for 60/300/3600 s, and `partial` flags agree.
- [x] Task 4 — `VerifiedDays` port for the archive tools (AC: #3)
  - [x] `compare_klines.py:489` and `prune_catalog.py:209-215` take a `VerifiedDays` argument (constructed in their `main` and in `nightly.py` from `CandleStore`); `nightly.py`'s step for build-candles calls `python -m candles.rebuild`; `Makefile` `nightly` target and README updated.
- [x] Task 5 — shims, images, lists, docs, parent spine (AC: #4)
  - [x] Shims: `ml_signals/candle_store.py`, `ml_signals/candles.py`, `collector_core/build_candles.py` (`REMOVE_AFTER = "24-3-alerting-context-as-forming-bar-observer"`); all three dockerfiles `COPY platform/candles ./candles`; Makefile lists add `candles/tests`; `docs/DATA_DICTIONARY.md` §2.5/§5, `ARCHITECTURE.md`; strike the parent spine's "Writer→reader imports contradict AD-4" Deferred entry as fully resolved.

## Dev Notes

Candles is the first *downstream* context; the dependency arrow must point capture → port, candles → kernel (AD-D8). The order invariant 'store never ahead of the archive' (`collector.py:1063-1073`) is easy to lose when the sink call moves — test it explicitly. `verified_days` lives in the candle DB and is the *only* day-status store (AD-D9 C2 in the adversary review): archive tools must not open the DB themselves after this story. Keep `_SCHEMA` text identical (it is frozen by AD-D12).

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

- Spine sections: AD-D8, AD-D9, AD-D12
- Review findings that shaped this story: reviews/review-adversary.md C2, C4; reviews/review-rubric.md L6
- Code: `ml_signals/candle_store.py`, `ml_signals/candles.py`, `collector_core/build_candles.py`, `collector_core/collector.py:1063-1073,1113-1170`, `compare_klines.py:489`, `prune_catalog.py:209-215`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (bmad-dev-auto, run 20260925-165952-8ecc).

### Debug Log References

Spec: `spec-24-1-candles-context-behind-the-secondsink-port.md`.
Test baseline on `7cd2f91aa2` before any change: 10 failed / 1435 passed. After: 10 failed /
1493 passed -- the same ten pre-existing failures (dydx `trade_ohlc` x5, `ofi_strategy` x4,
`test_rankings` redis x1), zero `DeprecationWarning`, `tests/test_{boundaries,images,namespace}.py`
159 passed. `ruff format --check` clean; no new `ruff check` or `mypy` errors against a
`git archive HEAD` control run.

### Completion Notes List

- `platform/candles/` is the first three-layer context (`domain/`, `application/`,
  `infrastructure/`) per the spine's Structural Seed; `kernel/` and `observability/` stay flat.
- `SecondSink` lives in `collector_core/ports.py` (capture has no package of its own until Epic
  26) and carries `apply` plus `watermarks`, because the collector's catch-up needs the watermark
  to know where the archive backfill starts. Candles satisfies it structurally, never by import.
- `forming_bar` returns a dict, not a Nautilus `Bar` as AD-D8's signature reads: the `/ws/live`
  payload and `queries.window`'s shape are frozen by AD-D12. Recorded in-code as a `Known limit:`
  with `Bar` as the upgrade path.
- `SecondSink.apply` commits per instrument where `apply_batch` committed per flush. One
  instrument's failure no longer discards the whole batch; both crash states are recoverable via
  the per-instrument watermark and the idempotent rebuild.
- `fold_arrays` gained a keyword-only `bars=` so one fold serves the store's six widths and an
  arbitrary forming-bar width. Exactly two folds now exist: `kernel.fold.fold_trades` and
  `candles.domain.fold.fold_arrays`.
- The three Story 23.1 shims whose `REMOVE_AFTER` named this story were deleted
  (`ml_signals/error_ledger.py`, `collector_core/collector.py`'s and
  `dydx_collector/collector.py`'s `__getattr__` blocks), as their expiry test requires.
- `test_boundaries.py` gained a narrow `COMPOSITION_ROOTS` map (each venue entrypoint plus the
  one wiring test, allowed `candles` and nothing else) rather than any widening of `_exempt`.
- Two AC texts were followed in substance but not to the letter, both deliberately: AC #4 says
  "all three dockerfiles `COPY` `candles`" -- `live_paper` imports no candles code and
  `test_images.py` requires a target's test paths to be a subset of its image's `COPY` set, so
  copying it there would ship dead weight; and AC #4 says the parent spine's "Writer->reader
  imports contradict AD-4" entry is "struck as fully resolved" -- it could only be *narrowed*,
  because `repair_catalog`'s `catalog_stats.query_second_snapshots` read remains and is retired by
  Story 25.1. The spine entry now names that one remaining site explicitly.
- `candles/tests` was added to the `Makefile` `test` list only. `live_paper` imports no candles
  code and `test_images.py` requires a target's test paths to be a subset of its image's `COPY`
  set, so adding it to `test-live-paper` would force a pointless `COPY` into that image.

### File List

- `ARCHITECTURE.md`
- `CLAUDE.md`
- `Makefile`
- `README.md`
- `_bmad-output/implementation-artifacts/epic-24-context.md`
- `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md`
- `bybit_collector/collector.py`
- `bybit_collector/tests/test_sequence_canary.py`
- `candles/__init__.py`
- `candles/application/`
- `candles/domain/`
- `candles/infrastructure/`
- `candles/rebuild.py`
- `candles/tests/__init__.py`
- `candles/tests/fixtures/`
- `candles/tests/test_candle_series.py`
- `candles/tests/test_forming_matches_stored.py`
- `candles/tests/test_rebuild.py`
- `candles/tests/test_schema_is_frozen.py`
- `candles/tests/test_sink.py`
- `candles/tests/test_verified_days.py`
- `collector.dockerfile`
- `collector_core/backfill_bars.py`
- `collector_core/build_candles.py`
- `collector_core/collector.py`
- `collector_core/compare_klines.py`
- `collector_core/nightly.py`
- `collector_core/ports.py`
- `collector_core/prune_catalog.py`
- `collector_core/rebuild_seconds.py`
- `collector_core/repair_catalog.py`
- `collector_core/tests/test_book_check.py`
- `collector_core/tests/test_collector.py`
- `collector_core/tests/test_compare_klines.py`
- `collector_core/tests/test_consolidate_catalog.py`
- `collector_core/tests/test_nightly.py`
- `collector_core/tests/test_prune_catalog.py`
- `collector_core/tests/test_rebuild_seconds.py`
- `collector_core/tests/test_venue_time.py`
- `data_api.dockerfile`
- `data_api/live_candles.py`
- `data_api/routes/candles.py`
- `data_api/routes/rankings.py`
- `data_api/settings.py`
- `data_api/tests/test_candles.py`
- `data_api/tests/test_live_candles.py`
- `data_api/tests/test_screener_columns.py`
- `docker-compose.yml`
- `docs/DATA_DICTIONARY.md`
- `docs/DATA_INTEGRITY_AUDIT.md`
- `docs/DEPLOY_CHECKLIST.md`
- `dydx_collector/collector.py`
- `dydx_collector/tests/test_build_candles.py`
- `dydx_collector/tests/test_candle_feed.py`
- `dydx_collector/tests/test_repair_catalog.py`
- `hyperliquid_collector/collector.py`
- `kernel/fold.py`
- `kernel/second_snapshot.py`
- `kernel/tests/test_second_snapshot.py`
- `ml_signals/candle_store.py`
- `ml_signals/candles.py`
- `ml_signals/error_ledger.py`
- `ml_signals/footprint.py`
- `ml_signals/tests/test_footprint.py`
- `tests/test_boundaries.py`
- `tests/test_hotpath.py`
- `tests/test_images.py`
- `ml_signals/tests/test_candle_store.py -> platform/candles/tests/test_candle_store.py` (renamed)
- `ml_signals/tests/test_candles.py -> platform/candles/tests/test_candles.py` (renamed)
