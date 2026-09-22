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

- [x] Task 1 — durable writer in `observability/error_ledger.py` (AC: #1, #2, #3)
  - [x] File sink: open append, one `json.dumps` line per record, `flush()` per line; rotation by size; per-site per-minute cap with an exact `suppressed` carry; `process_start` line at init. Stdlib only. The sink is created at init from env; no module-level mutable state beyond what 23.1's ledger already sanctions (AD-D10), documented with its invariant (DESIGN-01).
  - [x] Tests (real files in `tmp_path`, no mocks of internals, TEST-01..04): line format, flush-before-return, rotation boundary, cap and exact suppressed carry across a minute boundary, write failure counted and logged, unset dir is a no-op.
- [x] Task 2 — compose wiring (AC: #4)
  - [x] `./data/errors:/app/errors_dir` + `ERROR_LEDGER_DIR` + `ERROR_LEDGER_SERVICE` on the seven services; `.gitignore`; `platform/docs/DEPLOY_CHECKLIST.md` notes the VPS needs `mkdir -p platform/data/errors` owned by the container user before `make redeploy-all`.
- [x] Task 3 — `collector_core/crosscheck_errors.py` (AC: #5)
  - [x] Ledger reader over rotated files; gap finder over `kernel.catalog_files` per day per instrument (bounded memory, streaming); gap ↔ ledger/restart matcher using `kernel.clocks.MAX_TS_INIT_SKEW_NS`; report + exit code. Add it to `LEGACY_MODULE_TO_CONTEXT` as `archive`.
  - [x] Tests on a tmp catalog written with real `DydxSecondSnapshot` rows via `ParquetDataCatalog.write_data()`: a clean day passes; a gap with a matching ledger entry is explained; a gap across a `process_start` is explained as a restart; a gap with neither is `UNEXPLAINED` and exits non-zero; a `--fail-on` site with a non-zero count exits non-zero.
- [x] Task 4 — `/api/errors` `services` block (AC: #6) with a fixture test of the unchanged old fields.
- [x] Task 5 — docs and operator pointers (AC: #7, #8).

## Dev Notes

### Start from the prior attempt (operator decision, 2026-09-22)

An earlier, unreviewed attempt at this story exists as one commit, `51990335a8`, on branch `wip/23-3-main-checkout-20260921`. It adds about 1,100 lines across 11 files: `ml_signals/error_ledger.py` JSONL sink + tests, `collector_core/crosscheck_errors.py` + tests, `data_api/app.py` `/api/errors`, `ml_signals/catalog_stats.py`, `docker-compose.yml` mounts, `.gitignore` `platform/data/errors/`, and `error_ledger.start()` in `collector_core/collector.py` and `ranking_engine/engine.py`. The operator wants it used as the starting point, not rebuilt from scratch:

1. First action in the worktree: `git cherry-pick 51990335a8`. Resolve any conflicts against what 23.1/23.2 landed.
2. The attempt was written against the pre-23.1 layout. Port it onto this story's contract: the sink goes behind `observability/error_ledger.py`'s `record()`, stdlib-only (AD-D2), with `ml_signals.error_ledger` left as 23.1's shim; the cross-check reads the catalog through `kernel.catalog_files` (23.2). Move or rewrite anything that breaks `test_boundaries.py`, the shim contract or the same-commit rules below.
3. Treat every line as unreviewed. Verify it against each AC and the migration rules; keep what is correct, fix or drop what is not. List what was kept, changed and dropped in Completion Notes.

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

claude-opus-5 (Claude Code, bmad-loop dev session, 2026-09-22)

### Debug Log References

- Local `python3 -m pytest -o addopts="" --rootdir=.` from `platform/` (the environment has
  `nautilus_trader` importable, so the collector-image fallback was not needed).
- Full Makefile `test` module list: **10 failed, 1279 passed** -- exactly the 10 known
  pre-existing failures (`dydx_collector/tests/test_collector_trade_ohlc.py` x5,
  `ml_signals/tests/test_ofi_strategy*.py` x4, `data_api/tests/test_rankings.py` redis x1).
- `tests/test_hotpath.py`: all allocation assertions pass. Its wall-time assertion
  (`test_wall_time_per_message_is_within_twice_the_baseline`) passed on the first run and then
  began failing as the dev box's load average climbed past ~9 on 8 cores (other concurrent
  sessions). Reproduced identically on a pristine detached worktree at the story's baseline
  `9a067eb991` with no changes applied, so the failure is host contention, not this story.

### Completion Notes List

Ported from the preserved attempt `attempt-preserve/20260921-181822-125a-84a41aca` (its own
range `e58b0a0676..84a41aca65`), never cherry-picked (that range contains `e58b0a0676`, a merge
of Story 23.2's whole `kernel/` package, which must not land here).

**Kept (verified line by line against the ACs, unchanged in substance):**

- `observability/error_ledger.py`'s `_FileSink` design: append + `flush()` per line, size
  rotation (`.1`..`.N`), per-site per-minute cap with an exact `suppressed` carry, the
  write-failure path that returns the spent cap slot *and* the lost line to `pending`,
  `start()`/`PROCESS_START_SITE`/`WRITE_FAILED_SITE`/`_env_int`, and the reader helpers
  `ledger_files`/`services`/`iter_records`/`site_counts`/`service_summary`. Stdlib only;
  `record()`'s signature, ERROR log line and in-memory counts are byte-for-byte the 23.1
  contract; `ml_signals.error_ledger` is untouched as 23.1's shim.
- The `_FileSink`/reader test cases, the `crosscheck_errors` module structure
  (`ServiceReport`/`Gap`/`Report`, `build_report`, `_explain_gap`, `_exit_code`, `main`), the
  `--venue`-never-narrows-`--fail-on` rule and its test, `/api/errors`'s `services` block and
  `ServiceErrorSummary`, `data_api/settings.py`'s `ERROR_LEDGER_DIR`, the compose wiring
  (7 services x `ERROR_LEDGER_DIR`/`ERROR_LEDGER_SERVICE` + `./data/errors:/app/errors_dir`),
  `.gitignore`, `platform/data/errors/.gitkeep`, `test_boundaries.py`'s two `ARCHIVE` rows,
  `DEPLOY_CHECKLIST.md` §6, `DATA_DICTIONARY.md` §1.11, the AD-D16 amendment and the four
  Epic 22 operator-action pointer lines.

**Changed:**

- **`kernel.*` -> today's modules (the one dependency deviation, spec Design Notes).**
  `kernel.catalog_files.query_second_ohlc` -> `ml_signals.catalog_stats.query_second_ohlc`;
  `kernel.catalog_files.SNAPSHOT_DIRNAME` -> a module constant `SNAPSHOT_DIRNAME =
  "custom_dydx_second_snapshot"`; `kernel.clocks.MAX_TS_INIT_SKEW_NS` ->
  `collector_core.archive_gaps.ARRIVAL_MARGIN_NS` (the same 300 s bound), aliased locally as
  `_MAX_TS_INIT_SKEW_NS`; `kernel.clocks.NS_PER_S`/`NS_PER_DAY` -> module constants;
  `kernel.venues` -> `ml_signals.venue` + `common.venues`; `kernel.second_snapshot` ->
  `collector_core.second_snapshot`. Every site carries a `# 23.2 moves this to kernel.<symbol>.`
  comment and the repoint is filed in `deferred-work.md`. `platform/kernel/` is **not** created.
  `tests/test_boundaries.py` needed no new `LEGACY_EDGES_UNTIL`/`LEGACY_PRIVATE_IMPORTS_UNTIL`
  entry: every substituted symbol is already `KERNEL` in its split map, so ARCHIVE->KERNEL is a
  legal edge (the test was run and confirms it).
- `_FileSink.last_error` typed `BaseException | None` (the preserved `OSError | None` is wrong:
  the `except` clause also catches `UnicodeError`, which is not an `OSError` -- a real mypy
  error).
- `_emit`/`_rotate` restructured so `_rotate` takes and returns the open file instead of
  asserting on `self._file`. In the preserved version an `assert` there would have raised
  `AssertionError` **through** `record()`'s `except (OSError, UnicodeError)` guard, breaking the
  "never raises into the caller" invariant.
- `site_counts()` takes an `Iterable` (every caller passes a list; the preserved `Iterator`
  annotation forced a pointless `iter(...)` at each call site).
- `ledger_files()` returns `[]` when the directory does not exist, and `iter_records()` narrows
  its skip to `FileNotFoundError` (the documented rotate-out-from-under-us race) instead of any
  `OSError`, so a permission error stays loud rather than being silently skipped (DATA-07).
- `_print_report` split into `_print_services`/`_print_gaps`/`_fail_on_totals`, and `main()`
  split with `_build_parser`/`_window`, to keep every function under the cognitive-complexity
  and ~30-line limits and to stop `_exit_code` and `_print_report` duplicating the fail-on sum.
- Lint fixes over the preserved text: `datetime.fromisoformat(text)` directly (Python 3.11+
  parses `Z`; FURB162), `itertools.pairwise` (RUF007), import ordering, and a non-D401 docstring
  on `_count_write_failure`.
- `ARCHITECTURE.md`: only the `observability/` row edit and the new `data/errors/<service>.jsonl`
  stores-table row were taken; the preserved attempt's `kernel/` table row was dropped.
  `DATA_DICTIONARY.md` §1.11 cites `collector_core.archive_gaps.ARRIVAL_MARGIN_NS`
  (`kernel.clocks.MAX_TS_INIT_SKEW_NS` after 23.2) instead of the kernel symbol.
- `CLAUDE.md` DATA-07 rewritten against this tree's current text (the preserved diff was against
  a 23.2 baseline and did not apply). The new Known limit additionally records that the in-memory
  `counts()` the `<ErrorBar>` polls is still `data_api`'s own process, and keeps the Redis
  `errors:ledger` channel as the upgrade path for *live* cross-process visibility.
- `live_paper/node.py`: only the `error_ledger.start()` line (and its import) was taken; the
  preserved attempt's `kernel.venues` import was not.

**Added beyond the preserved attempt (test coverage the I/O matrix demanded):**

- `test_malformed_env_int_falls_back_to_the_default` (`_env_int` was untested).
- `test_write_failure_returns_the_spent_cap_slot_and_the_lost_line` (I/O matrix row 6's exactness
  claim was asserted nowhere).
- `test_errors_route_services_block_is_empty_without_a_ledger_dir` (the missing-dir row).
- `crosscheck_errors`: `test_a_ledger_entry_outside_the_skew_bound_does_not_explain_a_gap`,
  `test_suppressed_carries_are_folded_into_the_printed_counts`,
  `test_main_rejects_an_inverted_or_unparseable_window`,
  `test_venue_filter_selects_only_that_venues_instruments`,
  `test_missing_catalog_or_errors_dir_is_an_empty_report`.
- Three `deferred-work.md` entries (the kernel repoint, plus the two carried over from the
  preserved attempt's review round).

**Dropped:**

- Everything under `platform/kernel/` (never created).
- `platform/ARCHITECTURE.md`'s `kernel/` table row from the preserved diff.
- `live_paper/node.py`'s `kernel.venues` import from the preserved diff.
- The preserved attempt's own edits to the spec file's frontmatter, Review Triage Log and Auto
  Run Result (commits `6511bb0578`/`84a41aca65`): this session does not own the spec's status or
  its `<intent-contract>`.

**Known limits added in code** (each names its ceiling and upgrade path):

1. `error_ledger` module docstring: size rotation can age a day's lines out of the 20 MB x 10
   window under a sustained storm -- raise the two env vars, or ship the files with story
   22.11's rclone catalog backup.
2. `error_ledger` module docstring: a site's pending `suppressed` carry only reaches disk on that
   site's next write and nothing calls `close()` in production -- a periodic time-based flush.
3. `service_summary`: every call re-reads the service's whole file set -- an mtime/offset cache
   if the poll shows up in `data_api`'s CPU.
4. `crosscheck_errors` module docstring: the gap-to-ledger match is time proximity only (the
   frozen AC1 line schema carries no instrument id) -- carry a structured instrument id in
   `detail` and match on it too.
5. `crosscheck_errors` module docstring: only gaps *between* two observed rows are found, so a
   wholly dead instrument shows none -- persist a per-instrument last-seen watermark across runs.
   `DEPLOY_CHECKLIST.md` §6 states this explicitly next to the `(none)` check.

**Operator steps that remain (outside the repo):** create `platform/data/errors` on the VPS owned
by uid 1000 before the first `make redeploy-all`, redeploy, let it run a full day, then run the
§6 invocation and confirm exit 0; and confirm the frontend error bar still renders against the
live `services`-carrying `/api/errors`.

### File List

- `platform/observability/error_ledger.py` -- durable `_FileSink`, `start()`, reader helpers.
- `platform/observability/tests/test_error_ledger.py` -- sink + reader cases.
- `platform/collector_core/crosscheck_errors.py` -- **new** CLI.
- `platform/collector_core/tests/test_crosscheck_errors.py` -- **new** tests.
- `platform/collector_core/collector.py` -- `error_ledger.start()` in `run_forever`.
- `platform/ranking_engine/engine.py` -- `error_ledger.start()` in `__main__`.
- `platform/bot_tui/app.py` -- `error_ledger.start()` in `main()`.
- `platform/live_paper/node.py` -- `error_ledger.start()` in `main()`.
- `platform/data_api/app.py` -- lifespan `start()`, `ServiceErrorSummary`, `/api/errors`
  `services` block + `?since_ns=`.
- `platform/data_api/settings.py` -- `ERROR_LEDGER_DIR`.
- `platform/data_api/tests/test_data_api.py` -- old-shape fixture test + two `services` tests.
- `platform/frontend/openapi.json`, `platform/frontend/src/api/schema.ts` -- regenerated.
- `platform/docker-compose.yml` -- 7 services x env pair + `./data/errors` mount.
- `.gitignore`, `platform/data/errors/.gitkeep` -- the new store.
- `platform/tests/test_boundaries.py` -- the two `ARCHIVE` rows.
- `platform/CLAUDE.md`, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`,
  `platform/docs/DEPLOY_CHECKLIST.md` -- DATA-07, the observability/stores rows, §1.11, §6.
- `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`
  -- AD-D16 `[amended 2026-09-21: Story 23.3]`.
- `_bmad-output/implementation-artifacts/22-1-*.md`, `22-5-*.md`, `spec-22-10-*.md`,
  `spec-22-12-*.md` -- one operator-action pointer line each.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- three entries.
