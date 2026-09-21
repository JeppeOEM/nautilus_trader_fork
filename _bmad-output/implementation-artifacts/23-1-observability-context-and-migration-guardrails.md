# Story 23.1: `observability/` context, one notifier, and the migration's enforcement tests

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 23). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 23.1".

## Story

As the platform operator,
I want every process to report tolerated failures through one ledger and page me through one notifier, and the migration's boundary, image-closure and hot-path checks to run in `make test` from the first move,
So that the two live image gaps are closed now and every later context move is caught by a test instead of a review.

## Acceptance Criteria

1. **Given** `ml_signals/error_ledger.py`, the module functions `_notify` and `_watchdog_transition` in `collector_core/collector.py`, the dYdX incident-report handler (`dydx_collector/collector.py`, `[WS_RAW]` flush + `_IncidentHandler`) and `data_api/alerts.py`'s `post_webhook`/`post_telegram`
**When** the story ships
**Then** `platform/observability/{error_ledger,notify,watchdog,incidents}.py` exist and import only the standard library (asserted by `test_boundaries.py`); `observability.notify(channel, title, body)` is the one outbound transport with ntfy, Telegram and generic-webhook adapters chosen by env (`WATCHDOG_NTFY_URL`, `TELEGRAM_*`, webhook URL), and both the capture watchdog and `data_api/alerts.py` deliver through it (an `Alert` names a channel, never a transport); `observability.watchdog` holds only the generic `(down_since, reminder)` transition over a boolean, the feed-silence verdict staying in the collector; the incident handler takes its instrument-id pattern from the dYdX entrypoint and holds no venue token; the old paths `ml_signals.error_ledger` and the moved functions are pure re-export shims with `DeprecationWarning` and `REMOVE_AFTER = "24-1-..."`, and every in-repo caller is updated in this story (a `DeprecationWarning` in the test run is a failure, TEST-04)

2. **Given** the AD-D2 dependency graph and the AD-D1 context table
**When** `platform/tests/test_boundaries.py` runs in `make test`
**Then** it walks `ast` imports of every Python module under `platform/` (excluding `frontend/`, `node_modules/`, `data/`), maps every legacy module to its target context through a static `LEGACY_MODULE_TO_CONTEXT` table (an unmapped module is a failure), fails any import edge not in the AD-D2 graph between the two ends' target contexts, fails any import of a `_private` name across two contexts, treats `capture/venues/<v>/policies.py` as `domain/`, exempts only edges within one unmoved package, asserts `observability/` and `kernel/` import no context and `research/` imports no `data_api` symbol, and passes on the tree as of this story

3. **Given** every `command:` in `docker-compose.yml` and every `-m` module in the `Makefile` and the nightly cron line
**When** `platform/tests/test_images.py` runs in `make test`
**Then** it computes each entrypoint's top-level-package import closure and asserts every package is in that service's dockerfile `COPY` set (a shim counts as its target), and the story adds the `COPY` lines the test demands today (`data_api.dockerfile` lacks `collector_core` and `common`; `live_paper.dockerfile` whatever its closure requires) so that the test passes and `make build` succeeds for all three thin images; the parent spine's Deferred entry "`data_api`'s image does not ship the packages its code imports" is struck with an amendment

4. **Given** the recorded WS fixtures under `collector_core/tests/fixtures/` and the current `Collector._process_data`
**When** the hot-path replay test `platform/tests/test_hotpath.py` runs
**Then** it replays a fixed burst (all three venues, 30 instruments' worth of deltas and trades) through `_process_data`, measures `tracemalloc` allocations per message and `time.perf_counter_ns` wall time per message, writes the first run's numbers to `platform/tests/fixtures/hotpath_baseline.json` and records them in `docs/DATA_INTEGRITY_AUDIT.md`, and on every later run asserts allocations ≤ baseline and wall time ≤ 2× baseline; the test is deterministic across runs on the same host (three consecutive runs agree within the tolerance)

5. **Given** MR4 and AD-D16's per-process Known limit
**When** the story is merged
**Then** `platform/CLAUDE.md` DATA-07 names `observability.error_ledger` and states the Known limit (the ledger is per process; `/api/errors` shows `data_api`'s own sites; upgrade path `errors:ledger`), `ARCHITECTURE.md`'s module map gains the `observability/` row, `docs/DATA_DICTIONARY.md` is unchanged in content but re-cited, the collector image `COPY`s `observability`, and both Makefile test lists (`test`, `test-live-paper`) include `tests`

## Tasks / Subtasks

- [ ] Task 1 — `observability/` package (AC: #1)
  - [ ] Create `platform/observability/{__init__,error_ledger,notify,watchdog,incidents}.py`; move `ml_signals/error_ledger.py` verbatim (API `record(site, detail="", exc=None)`, `counts()`, `last_details()`, `reset()`), the module functions `_notify` (`collector_core/collector.py:388`) and `_watchdog_transition` (`:353`), and the incident-report subsystem from `dydx_collector/collector.py:648-843` (`_classify_incident`, `_write_incident_report`, `_IncidentHandler`, `_prune_incident_reports`, `_prune_stale_ws_raw_logs`, `_ws_raw_debug_flush_loop`), parameterising the handler with `iid_pattern: re.Pattern` supplied by the dYdX entrypoint.
  - [ ] `notify(channel, title, body)` with adapters `ntfy` (`WATCHDOG_NTFY_URL`), `telegram` (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, moved from `data_api/alerts.py:224`), `webhook` (URL); stdlib `urllib` only; the capture watchdog and `data_api/alerts.py`'s `deliver()` call it. Keep the existing payload shapes (test against the current tests in `data_api/tests` and `collector_core/tests/test_watchdog.py`).
  - [ ] Shims at `ml_signals/error_ledger.py` (`REMOVE_AFTER = "24-1-candles-context-behind-the-secondsink-port"`); update every caller (`grep -rn "error_ledger" platform --include=*.py`, ~31 sites).
- [ ] Task 2 — `platform/tests/test_boundaries.py` (AC: #2)
  - [ ] Walk `platform/**/*.py` (skip `frontend/`, `node_modules/`, `data/`, `.planning/`) with `ast`; collect `import`/`from ... import` top-level names; map each module path to a context via `LEGACY_MODULE_TO_CONTEXT` (a dict keyed by module path prefix: `collector_core/*` → capture, except `second_snapshot/open_interest/fold/venue_http/archive_gaps` → kernel, `build_candles` → candles, `rebuild_seconds/consolidate_catalog/prune_catalog/repair_catalog/compare_klines/nightly/backfill_bars/migrate_open_interest/measure_lag/archive_gaps` → archive; `dydx_collector/collector.py` lines are one module → capture (control-plane split happens in 25.4, list it as capture until then); `ml_signals/*` per the spine's AD-D1 table; `ranking_engine` → ranking; `live_paper` → bots; `data_api/alerts.py` → alerting; `data_api/{{live_candles,redis_bus}}.py` → views; `bot_tui` → interface; …). An unmapped module fails the test.
  - [ ] Encode the AD-D2 graph as a set of `(src_ctx, dst_ctx)` edges (copy it from the spine, including the labelled query-service edges as plain edges for now); fail any other cross-context edge; fail any `from x import _private` across contexts; exempt edges whose both ends map to the same *unmoved* package; special-case: `capture/venues/*/policies.py` is domain; `kernel` and `observability` may import no context; `research` may not import `data_api`.
  - [ ] Make it pass on the current tree: the map must be complete (list every module) and every current import must be a legal edge under target contexts — where it is not (e.g. `collector_core → ml_signals.candle_store` = capture → candles), add the edge to an explicit `LEGACY_EDGES_UNTIL = {{(edge): "<story key>"}}` table that the test honours until that story is `done` in `sprint-status.yaml`. This is the mechanism that lets the graph tighten one story at a time without loosening the test.
- [ ] Task 3 — `platform/tests/test_images.py` (AC: #3)
  - [ ] Parse `docker-compose.yml` `command:` lines and `build.dockerfile`, the Makefile's `python3 -m <module>` invocations and the README cron line; for each entrypoint compute the transitive top-level-package closure by `ast` (in-repo packages only); parse the dockerfile's `COPY platform/<pkg> ./<pkg>` lines; assert closure ⊆ COPY set (a shim module counts as its target package too).
  - [ ] Fix the current gaps the test finds (`data_api.dockerfile:27-30` lacks `collector_core`, `common`; `live_paper.dockerfile:17-18` lacks whatever `live_paper` imports — verify), run `make build` for all three images (`--network host`, see memory note on local Docker MTU) and record the result in Completion Notes. Strike the parent spine's Deferred entry.
- [ ] Task 4 — hot-path baseline `platform/tests/test_hotpath.py` (AC: #4)
  - [ ] Build a replay from the recorded fixtures under `collector_core/tests/fixtures/` (WS frames/trades for the three venues); construct a `Collector` with a fake client and a temp catalog; push N messages through `_process_data` (bypassing the queue) while `tracemalloc` is tracing; measure allocations/message and `perf_counter_ns`/message over 3 repetitions, take the median.
  - [ ] First run writes `platform/tests/fixtures/hotpath_baseline.json` (`{{"allocations_per_message": ..., "ns_per_message": ..., "messages": N, "host": ..., "recorded": ISO}}`); later runs assert `alloc <= baseline` and `ns <= 2*baseline`; document the numbers in `docs/DATA_INTEGRITY_AUDIT.md` (new row, "hot-path baseline").
- [ ] Task 5 — docs and lists (AC: #5)
  - [ ] `platform/CLAUDE.md` DATA-07 → `observability.error_ledger`, Known limit (per-process ledger, `/api/errors` shows `data_api` only, upgrade path `errors:ledger`); `ARCHITECTURE.md` module map row; `collector.dockerfile` + `data_api.dockerfile` + `live_paper.dockerfile` `COPY platform/observability ./observability`; Makefile `test` and `test-live-paper` lists include `tests` and `observability/tests`.

## Dev Notes

First story of the migration: it creates the tests every later story is judged by, so their design (the legacy map, the `LEGACY_EDGES_UNTIL` mechanism, the baseline file) matters more than the observability move itself. Read spine AD-D2, AD-D5, AD-D12, AD-D16 fully before starting. The boundary test must be *complete* on day one (every module mapped) and *honest* (legacy edges listed with the story that retires them), never loosened. `pytest-benchmark` is not in the collector image; use stdlib `tracemalloc` + `time.perf_counter_ns`.

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

- Spine sections: AD-D2, AD-D5, AD-D12, AD-D16
- Review findings that shaped this story: reviews/review-adversary.md H3, H6, L2; reviews/review-rubric.md C1, M8
- Code: `collector_core/collector.py:353,388`, `dydx_collector/collector.py:648-843`, `data_api/alerts.py:199-245`, `data_api.dockerfile:27-30`, `live_paper.dockerfile:17-18`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
