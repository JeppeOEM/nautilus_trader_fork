---
title: 'archive service: the nightly maintenance scheduled in our own code, no host cron'
type: 'feature'
created: '2026-09-26'
status: done
baseline_revision: '0e6c6f76cde70b000ae76922c1013702bdedbcec'
final_revision: 'dd82e5a6e544a11d33148025d6930b161f38cf9d'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-25-context.md'
warnings: ['oversized']
operator_actions:
  - "On the VPS, pull this commit and run `make up` from platform/ (it creates platform/data/archive and the rclone config dir, builds the collector image with rclone, and starts the new `archive` service)."
  - "On the VPS, delete the old nightly crontab line (`crontab -e`), then confirm `crontab -l | grep -E 'make (nightly|consolidate|backup-catalog)'` prints nothing (docs/DEPLOY_CHECKLIST.md section 1)."
  - "On the VPS, confirm RCLONE_REMOTE and RCLONE_BUCKET are set in platform/.env and that the rclone config sits in ~/.config/rclone (or set RCLONE_CONFIG_DIR in .env), then run `make backup-catalog` once and check that it exits 0."
  - "After the first 03:07 UTC slot, open the dashboard (or `docker compose logs archive`) and confirm that archive:status shows last_run with every venue's saga, consolidate_catalog and backup_catalog at exit 0 or 2."
---

<intent-contract>

## Intent

**Problem:** The nightly archive saga (`archive.nightly` per venue, then `consolidate_catalog`, then the rclone backup) only runs because of a host crontab line (`docs/DEPLOY_CHECKLIST.md` §1). That line is not part of our code. A reboot or redeploy at 03:07 loses the night, the operator cannot see whether maintenance ran, and the small types (mark/index, funding, OI, instrument status) pile up ~1–7-row minute files all day.

**Approach:** Add a small long-running asyncio service, `python -m archive.scheduler`, as compose service `archive` on the collector image. It runs the same steps through `archive.application.nightly.run_steps`, every step a subprocess. It catches up missed days from a persisted per-venue watermark, waits for the maintenance lock with a bound, and merges the current day's closed hours of the small types every N hours. It publishes `archive:status` and obeys `archive:control` `run_now`. `data_api` (GET/POST), the web UI and the `bot_tui` Collector pane expose it. The host cron is retired in the docs.

## Boundaries & Constraints

**Always:**
- Every maintenance step is its own subprocess (MEM-01). The scheduler never runs two of its own jobs at once.
- `;` semantics: one venue's FAILED saga never skips the next venue, the consolidate or the backup.
- Every tolerated failure is exactly one `error_ledger.record`. Sites: `archive.catch_up_capped`, `archive.lock_wait`, `archive.lock_timeout`, `archive.control_rejected`, `archive.control_redis`, `archive.status_publish`, `archive.state_write`, `archive.state_unreadable`, `archive.backup_not_configured`, `archive.backup_failed`.
- Domain modules stay pure: stdlib, `kernel` and `archive.domain` only.
- The application layer takes ports (Protocols). Only the composition root `archive/scheduler.py` imports `archive.infrastructure`.
- Writes go through `CatalogFiles` only. No `unlink`/`os.replace` anywhere else in `archive/`; `test_one_deleter_one_rewriter.py` stays green.
- The intraday merge touches only `mark_price_update`, `index_price_update`, `funding_rate_update`, `custom_open_interest` and `instrument_status`. It merges only files lying wholly inside one closed hour of the current UTC day. A file whose span reaches the current hour is never read into a merge, written or removed. The guard is enforced in `CatalogFiles`, not only in the caller.
- `state.json` is a scheduler cursor, never a data verdict:
  - `last_run_day` is the last day whose scheduled run completed.
  - Per venue, `last_success_day` is the last day of an unbroken run of no-FAILED sagas.
  - It is written atomically: temp, fsync, `Path.replace`.
  - Reconcile and prune never read it. `verified_days` stays the only day status (AD-D9 amended to say so).
- `data_api` never writes the catalog. Its POST only publishes the control message.
- Neither `data_api` nor `bot_tui` imports `archive`. Each keeps its own copy of the channel name and shape, as they already do for `ranking:control`/`collector:status`.
- Nothing new in `requirements.txt`.

**Block If:**
- Implementing this would need a new Python dependency.
- It would need a write into the catalog that bypasses `CatalogFiles`.

**Never:**
- APScheduler, cron inside the container, or a Go scheduler.
- The scheduler inside `data_api`.
- Holding `.consolidate.lock` in the scheduler process while its children run. The children take it themselves, and a second open file description would deadlock them.
- Taking the capture lock. Scheduled steps write closed days or closed hours only, and collectors hold it 24/7.
- Changing any existing Redis payload, Parquet schema, SQLite schema, compose service name, or `make nightly`/`consolidate` behaviour.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Normal night | state `last_run_day`=D-2, per venue `last_success_day`=D-2, now D 03:07 UTC | For each venue: `nightly(v, D-1)`. Then `consolidate`, then `backup_catalog`. Watermarks become D-1. Status is published after every step. | none |
| One venue fails | BYBIT's saga has a FAILED step | HYPERLIQUID, consolidate and backup still run. BYBIT's watermark stays. `last_run_day` advances, so there is no tight retry loop. The next night covers BYBIT's missed day plus the new one. | `nightly.<step>` ledger entry (existing) |
| Down 3 nights | `last_success_day`=D-5, start at D 14:00 | Runs D-4, D-3, D-2, D-1 oldest first (all venues per day), then consolidate and backup once, before sleeping. | none |
| Gap > cap | gap 10 days, `catch_up_max_days`=7 | Runs the newest 7 closed days, oldest first. | one `archive.catch_up_capped` naming the skipped range |
| No state file | first start | Target is yesterday only. | unreadable/corrupt file → `archive.state_unreadable`, treated as no state |
| Lock held | a manual `make nightly` holds `.consolidate.lock` | Waits, polling the lock free/held without holding it, up to `lock_wait_minutes`, then runs. | `archive.lock_wait` once per wait; past the bound `archive.lock_timeout` and that job is recorded as a failed step `lock_timeout` |
| Clock jumps | wall clock jumps forward 3 days / back 1 day | Forward: `next_run` is now, and catch-up covers the days. Back: `next_run` is the slot after `last_run_day`, so the service never runs the same day twice from the schedule. | none |
| run_now | `{"command":"run_now","day":null}` | Queued. After any running job it runs yesterday's full sequence. A duplicate queued day is dropped. | bad JSON, unknown command, today or a future day, or bad date → `archive.control_rejected`, ignored |
| Intraday | now 14:20, every 4 h (slots 00:07, 04:07, ...) | At 16:07: consolidate the closed hours 00..15 of today for the 5 small types. | per-day refusals as in consolidate (existing `consolidate.*` sites) |
| Backup unconfigured | `RCLONE_REMOTE` or `RCLONE_BUCKET` empty | Backup step exits 1 and appears as FAILED in status. | `archive.backup_not_configured` |
| API | `GET /api/archive/status` before any message; POST with `{"day":"2026-09-2"}` | GET 503 "not yet available". POST 422. A valid POST returns 202. No subscriber or Redis down returns 503. | FastAPI validation |

</intent-contract>

## Code Map

- `platform/archive/application/nightly.py` -- `run_steps(chain, runner, run_id, venue, day)`. Add an optional `on_step` callback, invoked after each step's result is final.
- `platform/archive/nightly.py` -- `steps(...)` builds a venue-day chain. `subprocess_runner` and `peak_child_rss_mb` can be reused by the scheduler root.
- `platform/archive/application/consolidate_day.py` -- `closed_days_needing_work`, `_consolidate_day`, `consolidate_directory`, `run`. Add the closed-hour grouping and run.
- `platform/archive/consolidate_catalog.py` -- the CLI. Add `--closed-hours`.
- `platform/archive/application/ports.py` -- the `CatalogWriter` Protocol, whose methods gain a closed-hour scope.
- `platform/archive/infrastructure/catalog_files.py` -- `assert_span_closed` (day guard). Add the hour guard used by `write_merged`/`remove_merged_sources` in closed-hour scope.
- `platform/archive/infrastructure/maintenance_lock.py` -- `_exclusive`, `MAINTENANCE_LOCK_NAME`. Add a free/held probe.
- `platform/archive/domain/reconciliation.py` -- `VENUES`.
- `platform/tests/test_images.py:281` `_CHILD_PROCESSES` and `:378-386` must-find set; `:438-446` is the cron-line test (rewrite it).
- `platform/archive/tests/test_one_deleter_one_rewriter.py:69-158` -- bans unlink/os.replace and module-level mutable literals outside `CatalogFiles`.
- `platform/collection_control/infrastructure/redis.py:29-93` and `application/control.py:208-230` -- redis status bus / control listener / reconnect pattern to mirror.
- `platform/views/rankings_bus.py:53`, `data_api/buses.py`, `data_api/app.py:83-111,200-211` -- bus cached from pub/sub plus lifespan task. Register routers before the `/api/{full_path}` catch-all.
- `platform/data_api/routes/rankings.py:98-159` and `data_api/tests/test_rankings_mode.py` -- POST-publishes-control precedent and its real-Redis tests.
- `platform/frontend/src/{App.tsx,api/client.ts,components/ErrorBar.tsx,hooks/useErrorLog.ts,components/chart/AlertDialog.tsx}` -- nav/status placement, fetch wrapper, polling hook, native `<dialog>` pattern.
- `platform/bot_tui/{collector_state.py,collector_pane.py,app.py:457-497,1134-1165}` and `bot_tui/tests/{test_collector_pane,test_app_collector,conftest,test_ad8_boundary}.py`.
- Docs:
  - `platform/docs/DEPLOY_CHECKLIST.md` §1 `:6-70` and §5.4 `:320-324`;
  - `platform/README.md` `:219-310`;
  - `platform/ARCHITECTURE.md` `:32,143-155,380-425,442-462`;
  - `platform/CLAUDE.md` DATA-05/06/07 `:54-57`;
  - `platform/docs/DATA_DICTIONARY.md` §1.12 region and §6 `:840`;
  - `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` `:137,250-252,403,420-424`;
  - `platform/.env-example`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/domain/schedule.py` -- Add a frozen `Schedule(nightly_at: datetime.time, intraday_every_hours: int)`.
  - `next_run(now, schedule, last_run_day)` returns `max(now, slot(due_day))`. `due_day` = `last_run_day + 1`, or yesterday when None. `slot(d)` = `d + 1` at `nightly_at` UTC.
  - `next_intraday(now, schedule)` returns the first slot strictly after `now` on the grid `hour % N == 0` at `nightly_at`'s minute.
  - `days_to_run(last_success_day, yesterday, cap)` returns `(days oldest first, skipped: tuple[date, date] | None)`.
  - `advance_watermark(last_success_day, results_by_day)` advances only through contiguous successes.
  - Everything is tz-aware UTC, with no I/O.
- [x] `platform/archive/domain/intraday.py` -- `INTRADAY_DATA_TYPES` frozenset, plus pure `closed_hours_needing_work(spans, now_ns)`. It groups files wholly inside one hour of the current UTC day whose hour is before the current hour, keeping only hours with more than one file.
- [x] `platform/archive/application/ports.py`, `infrastructure/catalog_files.py` -- Add a `MergeScope` enum (`CLOSED_DAY` default, `CLOSED_HOUR`) as a keyword on `write_merged` and `remove_merged_sources`. `CLOSED_HOUR` refuses (`OpenDayWriteError`) any span reaching the current UTC hour. Add `write_json_atomic(path, obj)`: temp, fsync, `Path.replace`, dir fsync. It is the state store's one writer and keeps the one-rewriter rule.
- [x] `platform/archive/application/consolidate_day.py`, `consolidate_catalog.py` -- `run_closed_hours(writer, catalog, now_ns)`:
  - over the `INTRADAY_DATA_TYPES` leaves;
  - reuses the schema/covering/merge/verify path with `CLOSED_HOUR` scope;
  - `--closed-hours` CLI flag, refused together with `--days`/`--data-type`, and taking the maintenance lock like `--apply`.

  Test that no file reaching the current hour is merged, and that a later day consolidate absorbs the hourly files.
- [x] `platform/archive/application/nightly.py` -- Add an `on_step: Callable[[StepResult], None] | None = None` parameter to `run_steps`. The saga's behaviour is otherwise unchanged.
- [x] `platform/archive/application/scheduler.py` -- Ports: `Clock` (`now()`), `JobRunner` (sync argv→exit, run via `asyncio.to_thread`), `LockProbe` (`is_free()`), `StateStore` (`load`/`save`), `StatusBus` (`publish`), `ControlChannel` (`listen`). `ArchiveScheduler`:
  - The main loop sleeps in at most 60 s chunks until the next nightly or intraday slot, or a queued `run_now`.
  - A run is: per day oldest first, then per venue, a `run_steps` of the nightly chain. Then consolidate, then backup, each its own `run_steps` chain.
  - Lock wait before each job.
  - Status is published after every step and on a 30 s heartbeat.
  - The control listener reconnects on failure, ledgered once per failure.
  - `last_run` and `last_intraday` are persisted in state and republished on start.
  - The chain builders are injected callables, so the application never imports `archive.nightly`.
- [x] `platform/archive/infrastructure/{scheduler_config.py,state_store.py,redis_bus.py}` + `maintenance_lock.py` -- Contents:
  - TOML loader rejecting unknown keys, validating `HH:MM`, venues ⊆ `VENUES`, ints ≥ 1 (lock wait ≥ 0), and `24 % intraday_consolidate_hours == 0`;
  - the JSON state store over `CatalogFiles.write_json_atomic`;
  - the redis status bus and control channel (collection_control pattern);
  - `maintenance_free(catalog) -> bool`.
- [x] `platform/archive/config.toml` -- Keys: `nightly_at = "03:07"`, `venues = ["DYDX","BYBIT","HYPERLIQUID"]`, `catch_up_max_days = 7`, `lock_wait_minutes = 60`, `intraday_consolidate_hours = 4`.
- [x] `platform/archive/backup_catalog.py` -- A `python -m archive.backup_catalog --catalog` port of `make backup-catalog`:
  - the same guards, ledgered;
  - the same `rclone sync ... --backup-dir .../catalog-replaced/<stamp> --exclude **/<today>T* --exclude *.tmp --transfers 8 --fast-list`;
  - the target from `RCLONE_REMOTE`/`RCLONE_BUCKET`;
  - the argv builder tested.
- [x] `platform/archive/scheduler.py` -- The composition root. `error_ledger.start()`. It reads the env: `REDIS_URL`, `CATALOG_PATH`, `CANDLES_DIR`, `DYDX_PLAN_PATH`, `ARCHIVE_STATE_DIR`, and `ARCHIVE_CONFIG` (default: the package's `config.toml`). It wires the chains (`archive.nightly.steps`; `archive.consolidate_catalog --apply`; `--closed-hours`; `archive.backup_catalog`) and runs the loop.
- [x] `platform/archive/tests/test_schedule.py`, `test_intraday.py`, `test_scheduler.py`, `test_scheduler_config.py`, `test_backup_catalog.py`, plus additions to `test_catalog_files.py`/`test_consolidate_day.py`/`test_nightly.py` -- Cover the I/O matrix with an injected clock and a fake step runner, lock probe, bus and state store (no Redis, no subprocess).
- [x] `platform/docker-compose.yml` -- Add service `archive`:
  - collector image, `command: python3 -m archive.scheduler`;
  - env: `HOME=/tmp`, `REDIS_URL`, paths, `RCLONE_REMOTE`/`RCLONE_BUCKET` from `.env`, `RCLONE_CONFIG`, `ERROR_LEDGER_*` (service `"archive"`);
  - mounts: catalog rw, candles rw, errors, `./data/archive:/app/archive_state`, `./data/dydx_config.toml` ro, `./archive/config.toml` ro, `${RCLONE_CONFIG_DIR:-${HOME}/.config/rclone}` ro;
  - `user`, `network_mode: host`, `restart: always`, `logging: *default-logging`.
- [x] `platform/collector.dockerfile` -- Install the Debian `rclone` package, apt lists removed. No Python dependency.
- [x] `platform/Makefile` -- Changes:
  - `up`/`redeploy*` `mkdir -p data/archive $(RCLONE_CONFIG_DIR)` and include `archive` where they list services;
  - `build-insecure` lists `archive`;
  - `backup-catalog` becomes `$(COMPOSE) run --rm --no-deps archive python3 -m archive.backup_catalog --catalog /app/catalog`;
  - the comments on `nightly`/`consolidate`/`backup-catalog` say they are manual tools and that the `archive` service is the schedule.
- [x] `platform/views/archive_status_bus.py`, `platform/data_api/{buses.py,app.py,routes/archive.py}` -- `ArchiveStatusBus` caches the latest valid `archive:status`. `GET /api/archive/status` returns it (503 until the first message). `POST /api/archive/run` (`{"day": "YYYY-MM-DD" | null}`, `extra=forbid`) publishes `{"command":"run_now","day":...}`: 202 on success, 503 when there is no subscriber or Redis fails. Tests mirror `test_rankings_mode.py` and `test_rankings.py`. Regenerate `frontend/openapi.json` and the codegen schema.
- [x] `platform/frontend/src/{api/client.ts,hooks/useArchiveStatus.ts,components/ArchiveStatus.tsx(+.test.tsx),App.tsx}` -- A compact maintenance status next to `ErrorBar`, showing last run day and outcome, finished time, next run, and running. A "Run now" button opens a native `<dialog>` confirm, which POSTs; errors show inline.
- [x] `platform/bot_tui/{archive_state.py,collector_pane.py,app.py}` + tests -- A listener for `archive:status` (the collector_state pattern) and a pure `format_archive_line`. The line is appended at the bottom of the Collector pane list, like the unpinned line. Add `archive_state.py` to `test_ad8_boundary.py`'s list, plus a conftest reset.
- [x] `platform/tests/test_images.py` -- Add `archive.scheduler`/`archive.backup_catalog` children to `_CHILD_PROCESSES` and the must-find set. Replace the one-cron-line assertion with one asserting §1 has no crontab line and names the removal check.
- [x] Docs -- Changes:
  - DEPLOY_CHECKLIST §1 becomes "Remove the old cron line" with `crontab -l | grep -E 'make (nightly|consolidate|backup-catalog)'` (must print nothing), and §5.4 is updated;
  - README "Nightly maintenance" describes the service, config, status, run-now and manual tools;
  - ARCHITECTURE.md gets the module map, operator tools, the Redis channel table rows, the deployment row, and the storage `data/archive`;
  - DATA_DICTIONARY records both channels and `state.json`;
  - CLAUDE.md DATA-05/06 name the service as the one place maintenance is scheduled, and DATA-07's service list gains `archive`;
  - the spine gets an amendment striking "the nightly cron" in the `archive` row, AD-D9 and deployment, and noting that the state cursor is not day status;
  - `.env-example` gets the RCLONE comment (service plus the `RCLONE_CONFIG_DIR` mount).

**Acceptance Criteria:**
- Given the service is up, when 03:07 UTC passes, then each venue's nightly saga, then `consolidate_catalog`, then the backup run as subprocesses in that order, and `archive:status` shows every step with its exit and duration.
- Given `python3 -m pytest` over `archive/tests bot_tui/tests data_api/tests views/tests tests`, then every new test passes and no previously passing test fails.
- Given `cd platform/frontend && npm test && npx tsc -b`, then all pass. `data_api/tests/test_app_frontend.py` confirms that `openapi.json` is current.
- Given `docker compose config`, then the `archive` service parses with `restart: always`, the logging anchor and `ERROR_LEDGER_SERVICE: "archive"`, and `make up` starts it with no profile.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13 (high 2, medium 8, low 3)
- defer: 1 (medium 1)
- reject: 10 (medium 3, low 7)
- addressed_findings:
  - `[high]` `[patch]` Any exception in a job (lock probe `OSError`, `/tmp` full for the saga scratch dir) escaped into the `TaskGroup` and crash-looped the service under `restart: always`. Fixed: `_run_job`/`_venue_day` record it as one failed `error` step (`archive.job_error`) and the run goes on (`;`). The job loop also has a last-resort guard with a 300 s backoff.
  - `[high]` `[patch]` A hung child (a stalled rclone, a wedged rebuild) blocked the single job loop forever. Fixed: the composition root's `timed_runner` kills a step after `step_timeout_minutes` (new required config key, 360), with exit 124 and `archive.step_timeout`.
  - `[medium]` `[patch]` A `run_now` of an old day started a venue's watermark there, so the next night falsely caught up and ledgered capped days. Fixed: run_now advances only an existing watermark.
  - `[medium]` `[patch]` A `run_now` for the day the running job already covers ran the whole day twice. Fixed: it is dropped.
  - `[medium]` `[patch]` The run_now queue was unbounded. Fixed: `MAX_QUEUED_RUNS` = 8; past it the request is `archive.control_rejected`.
  - `[medium]` `[patch]` With no or unreadable state, the first start after the slot re-ran last night's maintenance immediately, in the day, at cutover. Fixed: pure `assumed_last_run_day`, so the next run is the next slot ahead.
  - `[medium]` `[patch]` The web served the last cached status forever when the scheduler died. Fixed: the bus tracks `received_at`, and the GET returns 503 "stale" after 120 s (4 heartbeats) of silence.
  - `[medium]` `[patch]` The data_api bus and the TUI listener used a bare `listen()` that never recovers from a half-open connection. Fixed: a `get_message` loop that resubscribes after 120 s of silence.
  - `[medium]` `[patch]` The bus did not validate `days` or a running `finished`, so the typed GET could 500. Fixed, with tests.
  - `[medium]` `[patch]` `archive.backup_catalog` could sync while a consolidate removed minute files. Fixed: it holds the maintenance flock for the sync, and a held lock is `archive.backup_failed`.
  - `[low]` `[patch]` The TUI step label crashed on a non-string truthy `venue`. Fixed.
  - `[low]` `[patch]` POST `/api/archive/run` with `{}` returned 422. Fixed: `day` defaults to null (yesterday). OpenAPI and schema regenerated.
  - `[low]` `[patch]` The intraday Known limit understated the case where a sparse leaf's hourly file spans the whole day. Rewritten.

## Design Notes

- **Deviations from the epic's AC.** Each is deliberate.
  1. **`next_run` keys on the last run, not the last success.** `next_run`'s third argument is `last_run_day` (the last *completed* scheduled run). If it keyed on per-venue success, a standing failure would make `next_run` return "now" forever, a tight retry loop. Success instead drives *which days* a run covers, through the per-venue watermark, so the failed day is retried the next night.
  2. **The backup target stays in `platform/.env`.** It stays as `RCLONE_REMOTE`/`RCLONE_BUCKET`, the one existing personal, gitignored source used by the service and `make backup-catalog`, rather than being duplicated into the committed `config.toml`. rclone runs inside the image, and the operator's rclone config directory is mounted `:ro`.
  3. **Status adds keys.** `last_intraday` and `next_intraday` are added so a 4-hourly merge never hides the nightly's `last_run`. `running` is null or `{run_id, kind, day, started, steps}`.
- **Review-pass additions.**
  - **First start.** A scheduler without a cursor assumes every slot already past was handled by the previous regime (`assumed_last_run_day`). Its first run is the next slot and covers that slot's yesterday only. A night missed across the cutover is one `run_now` away.
  - **Step timeout.** Every step has a timeout: `step_timeout_minutes`, a sixth required config key, default 360. On timeout the step exits 124 and is ledgered as `archive.step_timeout`.
  - **Lock.** `archive.backup_catalog` holds the maintenance lock for its sync.
  - **Stale status.** Both readers treat 120 s of heartbeat silence as stale: the GET answers 503, and the TUI resubscribes.
- **Known limits** (as in-code comments).
  - The lock wait is a probe, not a held lock. A manual run can win the race between the probe and a child step. That step then refuses and fails as it would under cron. Upgrade path: pass an inherited locked fd to children.
  - A standing FAILED day re-runs every night up to `catch_up_max_days`. Upgrade path: a per-day attempt counter with backoff.
  - The rclone config mount is read-only, so OAuth token refresh cannot be saved (R2/B2 use static keys).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests bot_tui/tests data_api/tests views/tests observability/tests kernel/tests tests -q` -- expected: new tests pass, and the only failures are ones already known before this change.
- `cd platform/frontend && npm test && npx tsc -b && npm run lint` -- expected: pass.
- `cd platform && docker compose config -q` -- expected: exit 0.

## Auto Run Result

Status: awaiting-operator

**Summary.** Nightly archive maintenance now runs in a service of our own, compose service `archive` (`python3 -m archive.scheduler`, collector image, `restart: always`, started by `make up`). No host crontab is involved.
- It runs, in this order: each venue's `archive.nightly` saga through `run_steps` (every step a subprocess, `;` semantics), then `consolidate_catalog --apply`, then `archive.backup_catalog`. The backup is a rclone port of the old host recipe, now inside the image.
- Catch-up works from a per-venue watermark in `data/archive/state.json` (atomic write), capped at 7 days.
- Before each job it waits for the maintenance lock by probing it, never by holding it.
- Every 4 h it merges the current day's closed hours of mark/index, funding, OI and instrument status. The hour guard is enforced in `CatalogFiles`.
- It publishes `archive:status` (after every step, plus a 30 s heartbeat) and obeys `archive:control` `run_now`.
- Readers:
  - `data_api` serves `GET /api/archive/status` and `POST /api/archive/run`;
  - the web top nav shows the status with a "Run now" button behind a `<dialog>` confirm;
  - the `bot_tui` Collector pane has a status line.
- The docs retire the cron line (DEPLOY_CHECKLIST §1 is now "remove the old cron line"). The DDD spine is amended.

**Files.**
- New `archive/` modules:
  - `domain/schedule.py` and `domain/intraday.py`: the pure schedule, watermark and closed-hour grouping.
  - `application/scheduler.py`: `ArchiveScheduler` and its ports.
  - `infrastructure/scheduler_config.py`, `state_store.py`, `redis_bus.py`.
  - `config.toml`, `backup_catalog.py`, and the composition root `scheduler.py`.
- Modified `archive/` modules:
  - `ports.py` and `catalog_files.py`: `MergeScope`, the hour guard, and `write_json_atomic`.
  - `consolidate_day.py` and `consolidate_catalog.py`: `--closed-hours`.
  - `nightly.py`: `run_steps` takes `on_step`.
  - `maintenance_lock.py`: the free/held probe.
- Readers:
  - `views/archive_status_bus.py`;
  - `data_api/{routes/archive.py,buses.py,app.py}`, plus the regenerated `frontend/openapi.json` and `src/api/schema.ts`;
  - `frontend/src/{components/ArchiveStatus.tsx,hooks/useArchiveStatus.ts,api/client.ts,App.tsx}`;
  - `bot_tui/{archive_state.py,collector_pane.py,app.py}`.
- Deploy: `docker-compose.yml` (the `archive` service), `collector.dockerfile` (Debian rclone), `Makefile` (the `archive` service in the up/redeploy targets; `backup-catalog` now runs in the image).
- Tests: the new archive, views, data_api and bot_tui test files. `tests/test_images.py`'s cron-line test is rewritten to "no crontab line", and the scheduler's children are added to the image checks.
- Docs: DEPLOY_CHECKLIST, README, ARCHITECTURE, platform/CLAUDE.md DATA-05/06/07, DATA_DICTIONARY, DATA_INTEGRITY_AUDIT, `.env-example`, the DDD spine, and `epic-25-context.md` (recompiled).

**Review.** One pass: 13 patches applied (2 high, 8 medium, 3 low), 1 deferred (pre-existing subscribers with no half-open liveness check), 10 rejected, and no intent_gap or bad_spec. Details are in the Review Triage Log.

**Verification.**
- **Python suite.** Command: `REDIS_URL=redis://127.0.0.1:16379 python3 -m pytest -o addopts="" --rootdir=. <all 16 platform test dirs>`. Result: 1922 passed, 1 failed. The failure is `collector_core/tests/test_capture_lock.py::test_a_cancelled_wait_closes_its_descriptor`. It is pre-existing: it fails the same way on a `git archive` export of the baseline, in the same suite ordering, and passes alone.
- **`tests/test_hotpath.py`.** Its wall-time check failed once while the machine was loaded and passes when run alone.
- **Frontend.** vitest: 321/321. `tsc -b` clean. Lint shows only the 3 warnings that were already there.
- **Compose and lint.** `docker compose config -q` is OK. `ruff` and `mypy` are clean on every touched file.
- **Image smoke run** (by the implementation agent). A throwaway collector image with rclone installed ran the service against a temporary Redis. It published status, queued a `run_now`, ledgered a bad message and wrote `state.json`.

**Residual risks.**
- The lock wait is a probe, so there is a race with a manual run. If the race is lost, that step fails loudly.
- A standing FAILED venue-day re-runs every night, up to 7 days.
- The intraday late-file refusal on a sparse leaf (see the Known limit).
- The rclone config is mounted read-only, so no OAuth refresh.
- The `archive` service has not yet run on the VPS, and the cron line is still installed there. See `operator_actions`.

## Operator Confirmation

Confirmed 2026-09-26: the external actions this story owed were carried out.

- On the VPS, pull this commit and run `make up` from platform/ (it creates platform/data/archive and the rclone config dir, builds the collector image with rclone, and starts the new `archive` service).
- On the VPS, delete the old nightly crontab line (`crontab -e`), then confirm `crontab -l | grep -E 'make (nightly|consolidate|backup-catalog)'` prints nothing (docs/DEPLOY_CHECKLIST.md section 1).
- On the VPS, confirm RCLONE_REMOTE and RCLONE_BUCKET are set in platform/.env and that the rclone config sits in ~/.config/rclone (or set RCLONE_CONFIG_DIR in .env), then run `make backup-catalog` once and check that it exits 0.
- After the first 03:07 UTC slot, open the dashboard (or `docker compose logs archive`) and confirm that archive:status shows last_run with every venue's saga, consolidate_catalog and backup_catalog at exit 0 or 2.

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
