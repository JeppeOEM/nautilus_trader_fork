# Story 23.3: Durable error ledger and the day-long data/error cross-check

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 23), added 2026-09-21 at the operator's request after the Epic 22 operator-action pass. Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` (AD-D16's per-process Known limit is what this story lifts). Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 23.3". Runs after 23.2 (it reads the catalog through `kernel.catalog_files`).

## Story

As the platform operator,
I want every tolerated failure written to a file that survives restarts, rebuilds and Redis loss, and one command that cross-checks a day of archived data against those errors,
So that "leave it running for a day and look at the logs" is real evidence, not a count that silently reset when a container was recreated.

## Background: why this exists

On 2026-09-21 the Epic 22 operator actions needed day-long clean-run evidence (22.5 book cross-check, 22.1 error-ledger silence, 22.10 volume24h growth, 22.12 late-trade and book-sequence counts). Today none of it can be read back after the fact:

- `ml_signals/error_ledger.py` (moved to `observability/error_ledger.py` by 23.1) counts in process memory. Counts reset on restart, and `GET /api/errors` shows only `data_api`'s own sites, never the collectors'.
- The log lines exist only in Docker's json-file logs, which are deleted when a container is recreated (`make up`, `docker compose up --build`, a bmad-loop dev session).
- Redis is not durable: `dydx-redis` has no volume and `appendonly no`, so its RDB snapshot lives in the container and is lost with it. The spine's named upgrade path (`errors:ledger` in Redis) would inherit that loss, and `observability/` may import only the standard library (23.1 AC1).

A zero count after a restart proves nothing, and a data gap with no ledger entry is a silent loss (DATA-07). This story makes both checkable over any window.

## Acceptance Criteria

1. **Given** `observability.error_ledger.record(site, detail="", exc=None)` from 23.1
   **When** a process calls it
   **Then** besides the ERROR log line and the in-memory count (both unchanged), it appends one JSON line `{"ts_ns", "service", "pid", "site", "detail", "exc_type", "suppressed"}` to `<ERROR_LEDGER_DIR>/<service>.jsonl`, using the standard library only (the `test_boundaries.py` stdlib assertion for `observability/` still passes); `service` comes from `ERROR_LEDGER_SERVICE`; the write is flushed per line and never raises into the caller (a failed write is counted under the site `observability.ledger_write` in memory and logged, never swallowed, DATA-07); with `ERROR_LEDGER_DIR` unset the ledger behaves exactly as today and tests need no directory

2. **Given** a process starts
   **When** its ledger is initialised
   **Then** it writes a `{"site": "process_start", ...}` line with the pid and image/code revision if available, so a reader can tell a zero-error window from a restarted one; the cross-check (AC5) reports every restart inside the window

3. **Given** an error storm (a flapping feed, a reconnect loop)
   **When** one site records faster than the write cap
   **Then** at most `ERROR_LEDGER_MAX_LINES_PER_SITE_PER_MIN` (default 60) lines per site per minute are written, the next written line for that site carries the exact number of suppressed records in `suppressed`, so persisted totals stay exact; files rotate by size (`<service>.jsonl`, `.1` .. `.N`, default 20 MB × 10, mirroring the compose `x-logging` policy) and the bound is a documented `Known limit:` comment naming the ceiling and the upgrade path (object-storage shipping with the catalog backup, Story 22.11's rclone remote)

4. **Given** every service that calls `record()` (`collector`, `bybit_collector`, `hyperliquid_collector`, `ranking_engine`, `data_api`, `live-paper`, `bot_tui`)
   **When** the story ships
   **Then** `docker-compose.yml` bind-mounts `./data/errors:/app/errors_dir` into each and sets `ERROR_LEDGER_DIR=/app/errors_dir` and a distinct `ERROR_LEDGER_SERVICE` equal to the compose service name (new env vars and one new mount are additive; every existing env var, mount and service name is unchanged, MR1); `.gitignore` covers `platform/data/errors/`; `platform/data/` stays the only place durable stores live (AD-D13)

5. **Given** a UTC window (`--since`/`--until`, default the last 24 h) and optional `--venue`
   **When** `python3 -m collector_core.crosscheck_errors` runs (mapped to the `archive` context in `LEGACY_MODULE_TO_CONTEXT`; it moves with archive in 25.1)
   **Then** it reads the ledger files (rotated ones included) and, through `kernel.catalog_files` only (no `ParquetDataCatalog` construction, no unbounded loads, one day and one instrument at a time, MEM-01), the `custom_dydx_second_snapshot` rows of every collected instrument in the window, and prints: per service, restarts and per-site counts (suppressed included); per instrument, missing 1 s snapshot seconds grouped into gap intervals; and for every gap interval, the ledger entries of the owning collector within `MAX_TS_INIT_SKEW_NS` of it. A gap with no matching ledger entry and no restart is reported as `UNEXPLAINED` (a DATA-07 finding, never tolerated); the exit code is non-zero when any `UNEXPLAINED` gap or any site in a `--fail-on` list (default `collector.book_crosscheck`, `collector.book_sequence`, `collector.pending_deltas`) is non-zero

6. **Given** `GET /api/errors`
   **When** `ERROR_LEDGER_DIR` is set for `data_api`
   **Then** the response keeps its current fields unchanged and adds a `services` object with, per service file, the per-site counts since that service's last `process_start` and since a `?since_ns=` bound; the frontend error bar keeps working unmodified (fixture test on the old response shape)

7. **Given** the Epic 22 operator checks that need a clean day
   **When** the story is merged and deployed
   **Then** `platform/docs/DEPLOY_CHECKLIST.md` gains a "Day-long clean-run check" section with the exact `crosscheck_errors` invocation that closes each of: 22.5 #1 (`collector.book_crosscheck` zero on Bybit and Hyperliquid for a day), 22.1 #2 (no `[collector.*]` ledger lines, snapshots 1 s apart), 22.10 #4 (`ranking_engine.volume24h` not growing), 22.12 #5 (`collector.late_trade`, `collector.pending_deltas`, `collector.book_sequence` for Bybit and Hyperliquid); each of those story files gets a one-line pointer to it under its operator actions

8. **Given** MR4
   **When** the story is merged
   **Then** `platform/CLAUDE.md` DATA-07 replaces the per-process Known limit with the durable-file behaviour and its new ceiling, `ARCHITECTURE.md` and `docs/DATA_DICTIONARY.md` document `platform/data/errors/*.jsonl` (fields, rotation, cap), the three dockerfiles and both Makefile test lists stay consistent (`test_images.py`, `test_boundaries.py` pass), and the spine's AD-D16 Known limit is amended with a `[amended 2026-09-21: Story 23.3]` note

## Tasks / Subtasks

- [ ] Task 1 — durable writer in `observability/error_ledger.py` (AC: #1, #2, #3)
  - [ ] File sink: open append, one `json.dumps` line per record, `flush()` per line; rotation by size; per-site per-minute cap with an exact `suppressed` carry; `process_start` line at init. Stdlib only. The sink is created at init from env; no module-level mutable state beyond what 23.1's ledger already sanctions (AD-D10), documented with its invariant (DESIGN-01).
  - [ ] Tests (real files in `tmp_path`, no mocks of internals, TEST-01..04): line format, flush-before-return, rotation boundary, cap and exact suppressed carry across a minute boundary, write failure counted and logged, unset dir is a no-op.
- [ ] Task 2 — compose wiring (AC: #4)
  - [ ] `./data/errors:/app/errors_dir` + `ERROR_LEDGER_DIR` + `ERROR_LEDGER_SERVICE` on the seven services; `.gitignore`; `platform/docs/DEPLOY_CHECKLIST.md` notes the VPS needs `mkdir -p platform/data/errors` owned by the container user before `make redeploy-all`.
- [ ] Task 3 — `collector_core/crosscheck_errors.py` (AC: #5)
  - [ ] Ledger reader over rotated files; gap finder over `kernel.catalog_files` per day per instrument (bounded memory, streaming); gap ↔ ledger/restart matcher using `kernel.clocks.MAX_TS_INIT_SKEW_NS`; report + exit code. Add it to `LEGACY_MODULE_TO_CONTEXT` as `archive`.
  - [ ] Tests on a tmp catalog written with real `DydxSecondSnapshot` rows via `ParquetDataCatalog.write_data()`: a clean day passes; a gap with a matching ledger entry is explained; a gap across a `process_start` is explained as a restart; a gap with neither is `UNEXPLAINED` and exits non-zero; a `--fail-on` site with a non-zero count exits non-zero.
- [ ] Task 4 — `/api/errors` `services` block (AC: #6) with a fixture test of the unchanged old fields.
- [ ] Task 5 — docs and operator pointers (AC: #7, #8).

## Dev Notes

- Keep 23.1's API and shim contract exactly; this story only adds a sink behind `record()`. `ml_signals.error_ledger` is still a shim until 24.1.
- Hot path: `record()` is on failure paths only, but a storm must not stall `_process_data`. The per-site cap bounds writes; 23.1's `test_hotpath.py` baseline must still pass (a storm fixture that calls `record()` in the burst is a good extra case).
- Redis is not the persistence layer here on purpose: it has no volume and `appendonly no` in `docker-compose.yml`, and `observability/` is stdlib-only. Do not change Redis persistence in this story.
- Frozen for the whole migration (MR1): Parquet schemas and catalog directory names, every Redis payload, SQLite/TOML store schemas, compose service names, existing env vars, the existing `platform/data/` bind mounts. This story only adds.

### Migration rules that bind every story (spine AD-D12, MR1/MR2/MR4/MR14)

- **Deployable alone.** A replay/fixture test proving a payload or file is byte-identical before and after is the standard evidence.
- **Same commit:** `platform/CLAUDE.md` citations, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, both Makefile test lists (`test`, `test-live-paper`). `platform/tests/test_images.py` and `test_boundaries.py` must pass.
- **Layering (AD-D2):** `observability/` and `kernel/` import no context; `observability/` imports only the standard library.
- **Project rules:** `platform/CLAUDE.md` DATA-01..08, DATA-07, TEST-01..04 (warnings are failures), READ-03, SSOT-01..05, MEM-01..03, NAUT-01..03, FORK-01.
- **Working directory:** `platform/`; tests run as `python3 -m pytest -o addopts="" --rootdir=. <paths> -q`; `make test` runs the Makefile list inside the collector image.

### References

- Spine: AD-D2, AD-D10, AD-D13, AD-D16
- Code: `observability/error_ledger.py` (after 23.1), `data_api/app.py` `/api/errors`, `docker-compose.yml` (`x-logging`, per-service `./data/*` mounts), `kernel/catalog_files.py` and `kernel/clocks.py` (after 23.2)
- Operator actions this closes: stories 22.1, 22.5, 22.10, 22.12 in `_bmad-output/implementation-artifacts/`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
