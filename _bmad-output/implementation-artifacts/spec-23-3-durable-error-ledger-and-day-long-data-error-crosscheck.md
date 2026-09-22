---
title: 'Story 23.3: Durable error ledger and the day-long data/error cross-check'
type: 'feature'
created: '2026-09-22'
status: 'in-review'
baseline_revision: '77730e2995beb1e08e296cfc8dcc123ba897edbe'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/23-3-durable-error-ledger-and-day-long-data-error-crosscheck.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-23-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `observability/error_ledger.py` counts tolerated failures in process memory only, so a count resets on every container recreation and `GET /api/errors` shows nothing but `data_api`'s own sites. The Epic 22 operator actions (22.1 #2, 22.5 #1, 22.10 #4, 22.12 #5) all need day-long evidence that cannot be read back after the fact, and a data gap with no ledger entry is a silent loss (DATA-07).

**Approach:** Add a stdlib-only append-only JSONL sink behind the existing `record()` (one file per service under a shared bind-mounted `ERROR_LEDGER_DIR`, `process_start` marker per boot, per-site write cap with an exact `suppressed` carry, size rotation), wire every service in `docker-compose.yml`, expose the files back through `GET /api/errors`'s new `services` block, and add `python3 -m collector_core.crosscheck_errors` — one command that matches a window's archived second-snapshot gaps against those ledgers and exits non-zero on an `UNEXPLAINED` gap or a `--fail-on` site.

## Boundaries & Constraints

**Always:**
- `observability/` imports only the standard library (`tests/test_boundaries.py` asserts it). `record()`'s signature, its ERROR log line and its in-memory counts are unchanged (23.1 contract); `ml_signals.error_ledger` stays a 23.1 shim.
- One `record()` call yields exactly one durable line **or** one `suppressed` increment — never both, never neither. A failed write is itself ledgered at `observability.ledger_write` and logged, never swallowed (DATA-07).
- `record()` never raises into its caller and must not stall `_process_data` under a storm; `tests/test_hotpath.py`'s committed baseline must still pass.
- The cross-check reads the catalog through bounded per-file pyarrow helpers only — no `ParquetDataCatalog` construction, one UTC day and one instrument at a time (MEM-01).
- MR1: compose service names, existing env vars, existing `platform/data/` mounts, Parquet schemas, Redis payloads and SQLite/TOML schemas are frozen. This story only **adds** env vars, one mount and one response field.
- MR2/MR4 same-commit set: `platform/CLAUDE.md`, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, the three dockerfile `COPY` sets, compose `command:` lines, both Makefile test lists. `tests/test_images.py` and `tests/test_boundaries.py` must pass.
- Every deliberate simplification is a `Known limit:` comment naming the ceiling and the upgrade path.

**Block If:** nothing. The one dependency deviation (Story 23.2's `kernel/` is not in the tree) is resolved in Design Notes, not escalated.

**Never:**
- Never change Redis persistence (`dydx-redis` keeps no volume and `appendonly no` — the whole point is that the ledger does not depend on it).
- Never create `platform/kernel/` in this story: Story 23.2 was deferred by the orchestrator and is re-driven separately; pre-landing any part of it would collide with that re-drive.
- Never run `make up`/`docker compose up` from the worktree (shared compose project name with the operator's running stack). Build images with explicit `-f`/`-t` instead.
- No new third-party dependency; no `logging.handlers.RotatingFileHandler` (it swallows write failures into `handleError`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Durable record | `ERROR_LEDGER_DIR` set, `start()` called, `record("collector.x", detail, exc)` | One JSON line `{ts_ns, service, pid, site, detail, exc_type, suppressed}` appended and flushed before return; `detail` truncated to 2000 chars in the file only | No error expected |
| Ledger disabled | `ERROR_LEDGER_DIR` unset | `start()` returns False; `record()` behaves exactly as 23.1; no file created | No error expected |
| Process start | `start()` at entrypoint | One `process_start` line with `pid` and `revision` (`ERROR_LEDGER_REVISION`, else `None`); a second `start()` is a no-op | Write failure counted at `observability.ledger_write` |
| Error storm | >cap records for one site inside one UTC minute | At most `ERROR_LEDGER_MAX_LINES_PER_SITE_PER_MIN` (60) lines; the next written line for that site carries the exact suppressed count, so `lines + Σsuppressed` is the true total | No error expected |
| Rotation | file would exceed `ERROR_LEDGER_MAX_BYTES` | Rotate `<service>.jsonl` → `.1` .. `.N` (`ERROR_LEDGER_BACKUP_COUNT`); readers return oldest-first across all of them | No error expected |
| Write failure | sink path unwritable | `record()` still counts and logs; `observability.ledger_write` incremented with the cause; the spent cap slot and the lost line are carried into the next line's `suppressed` | ERROR log with `exc_info` |
| Truncated line | crash mid-write leaves a partial JSON line | Readers skip it and return every well-formed line | No error expected |
| Clean window | every instrument has 1 s rows, no ledger entries | `crosscheck_errors` prints `(none)` gaps, exit 0 | No error expected |
| Explained gap | gap between two rows, a ledger entry (or `process_start`) for the owning collector within `MAX_TS_INIT_SKEW_NS` of an edge | Gap printed `restart` or `explained: <site>`, exit 0 | No error expected |
| Unexplained gap | gap with neither | Gap printed `UNEXPLAINED`, exit non-zero | Non-zero exit is the finding |
| Fail-on site | any service's count of a `--fail-on` site is non-zero | exit non-zero, even when `--venue` names a different venue | Non-zero exit is the finding |
| Bad window | `--since` >= `--until`, or a non-ISO instant | Message on stderr, exit 1 | Argument error |
| `/api/errors` | `ERROR_LEDGER_DIR` holds files | `counts`/`last` byte-identical to 23.1 plus `services{<name>: {last_start_ns, since_start, since}}`; `?since_ns=` bounds `since` | Missing dir → empty `services` |

</intent-contract>

## Code Map

- `platform/observability/error_ledger.py` -- 23.1's in-memory ledger; gains `_FileSink`, `start()`, `close`-on-`reset()`, and the reader helpers `ledger_files`/`services`/`iter_records`/`site_counts`/`service_summary`. Stdlib only.
- `platform/observability/tests/test_error_ledger.py` -- existing tests; gains the sink and reader cases (real files under `tmp_path`).
- `platform/collector_core/crosscheck_errors.py` -- **new** CLI (context `archive`).
- `platform/collector_core/tests/test_crosscheck_errors.py` -- **new**; writes a tmp catalog with real `DydxSecondSnapshot` rows via `ParquetDataCatalog.write_data()`.
- `platform/ml_signals/catalog_stats.py:111` `query_second_ohlc` -- the bounded per-file snapshot reader the cross-check uses (a `KERNEL` symbol in `test_boundaries.py`'s split map; no catalog construction).
- `platform/collector_core/archive_gaps.py:49` `ARRIVAL_MARGIN_NS` (300 s) -- today's single skew bound; becomes `kernel.clocks.MAX_TS_INIT_SKEW_NS` in 23.2.
- `platform/ml_signals/venue.py` (`venue_of`, `MalformedInstrumentId`), `platform/common/venues.py` (`VENUE_KINDS`) -- today's venue parsing/registry (both `KERNEL`).
- Entrypoints calling `error_ledger.start()`: `collector_core/collector.py` `run_forever` (all three collectors), `ranking_engine/engine.py` `__main__`, `data_api/app.py` lifespan, `live_paper/node.py` `main`, `bot_tui/app.py` `main`.
- `platform/data_api/{app.py,settings.py}` -- `/api/errors` `services` block + `ERROR_LEDGER_DIR`; `platform/frontend/{openapi.json,src/api/schema.ts}` regenerated.
- `platform/docker-compose.yml`, `.gitignore`, `platform/data/errors/.gitkeep` -- the shared mount on the seven services.
- `platform/tests/test_boundaries.py` -- map the two new modules to `ARCHIVE`.
- Docs: `platform/CLAUDE.md` DATA-07, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, `platform/docs/DEPLOY_CHECKLIST.md` §6, the spine's AD-D16, and the operator-action pointers in stories 22.1/22.5/22.10/22.12.

## Tasks & Acceptance

**Execution:**
- [x] `platform/observability/error_ledger.py` -- add `_FileSink` (append + flush per line, size rotation, per-site per-minute cap with exact `suppressed` carry), `start()`, `PROCESS_START_SITE`/`WRITE_FAILED_SITE`, `_env_int`, and the reader helpers -- the durable half of DATA-07, stdlib only.
- [x] `platform/observability/tests/test_error_ledger.py` -- cover every I/O-matrix row of the sink and readers (line format, flush-before-return, idempotent `start()`, unset-dir no-op, cap + carry across a minute boundary, rotation bound and oldest-first read-back, write failure counted/logged twice, truncated line skipped, `service_summary` since-start and since-bound).
- [x] entrypoints (`collector_core/collector.py`, `ranking_engine/engine.py`, `data_api/app.py`, `live_paper/node.py`, `bot_tui/app.py`) -- one `error_ledger.start()` call each, so every service emits `process_start`.
- [x] `platform/docker-compose.yml`, `.gitignore`, `platform/data/errors/.gitkeep` -- add `./data/errors:/app/errors_dir` (rw) plus `ERROR_LEDGER_DIR`/`ERROR_LEDGER_SERVICE` (= compose service name) to the seven services; change nothing existing.
- [x] `platform/collector_core/crosscheck_errors.py` -- the CLI: ledger reader over rotated files, per-day/per-instrument gap finder, gap↔ledger/restart matcher within the 300 s skew bound, printed report and exit code; `--since/--until/--venue/--fail-on`.
- [x] `platform/collector_core/tests/test_crosscheck_errors.py` -- clean day, explained gap, restart-explained gap, `UNEXPLAINED` + non-zero exit, `--fail-on` exit, `--venue` never narrowing `--fail-on`, venue→service map covers `VENUE_KINDS`, and `main()` argv end-to-end.
- [x] `platform/data_api/{settings.py,app.py}` + `platform/data_api/tests/test_data_api.py` + `platform/frontend/{openapi.json,src/api/schema.ts}` -- the `services` block and its fixture test on the unchanged old shape.
- [x] `platform/tests/test_boundaries.py` -- map `collector_core.crosscheck_errors` and its test module to `ARCHIVE`; add any `LEGACY_EDGES_UNTIL`/`LEGACY_PRIVATE_IMPORTS_UNTIL` entry the new imports require, keyed on the story that retires it.
- [x] Docs + operator pointers -- `CLAUDE.md` DATA-07 (replace the per-process Known limit), `ARCHITECTURE.md` (observability row + the stores table), `docs/DATA_DICTIONARY.md` §1.11, `docs/DEPLOY_CHECKLIST.md` §6 "Day-long clean-run check" with the exact invocation and which Epic 22 action each line closes, one pointer line in each of stories 22.1/22.5/22.10/22.12, and `[amended 2026-09-21: Story 23.3]` on the spine's AD-D16.

**Acceptance Criteria:**
- Given the tree after this story, when the Makefile test-target module list runs in the collector image, then `observability/tests`, `collector_core/tests/test_crosscheck_errors.py`, `data_api/tests/test_data_api.py`, `tests/test_boundaries.py`, `tests/test_images.py`, `tests/test_namespace.py` and `tests/test_hotpath.py` pass with no new failure against the 10 pre-existing ones (dydx trade_ohlc ×5, ofi_strategy ×4, rankings redis ×1).
- Given `ERROR_LEDGER_DIR` unset, when the whole suite runs, then no test needs a ledger directory and no `<service>.jsonl` is created anywhere.
- Given `docker compose config` on the changed compose file, when it is diffed against the pre-story output, then the only differences are the seven added `ERROR_LEDGER_*` env pairs and the seven added `./data/errors` mounts.
- Given the story's acceptance criteria include steps only the operator can take outside the repo (create the VPS directory, redeploy, run the check over a real 24 h window, confirm the frontend renders against live services), when every in-repo part is complete and committed, then the spec closes at `status: awaiting-operator` with those steps enumerated under `operator_actions`.

## Spec Change Log

## Review Triage Log

## Design Notes

- **Starting point (operator decision).** An earlier attempt at this story is preserved at `attempt-preserve/20260921-181822-125a-84a41aca` (its own work is the range `e58b0a0676..84a41aca65`; the older, pre-23.1 attempt it was ported from is `51990335a8`). It carries a full implementation plus one adversarial/edge-case review round. Reuse it, but treat every line as unreviewed and verify it against these ACs. Do **not** cherry-pick the branch: it contains `e58b0a0676`, a merge of Story 23.2's whole `kernel/` package.
- **Why this story does not use `kernel/` (dependency deviation).** Story 23.3 was specified to read the catalog through `kernel.catalog_files` and `kernel.clocks.MAX_TS_INIT_SKEW_NS`. Story 23.2 was deferred by the orchestrator (spec-baseline mismatch) and is being re-driven separately, so `platform/kernel/` does not exist at this baseline. Landing it here would collide with that re-drive. Instead the cross-check uses the exact modules 23.2 will move into the kernel — `ml_signals.catalog_stats.query_second_ohlc` (a `KERNEL` symbol in `test_boundaries.py`'s split map, pure pyarrow, no `ParquetDataCatalog` construction), `collector_core.archive_gaps.ARRIVAL_MARGIN_NS` (the same 300 s bound `MAX_TS_INIT_SKEW_NS` becomes), `ml_signals.venue` and `common.venues`. Every such import carries a one-line `Known limit:`/`23.2` note naming the kernel symbol it becomes, and the deviation is filed as a `deferred:` item so 23.2/25.1 repoints it. The AC5 *intent* — bounded, streaming, catalog-construction-free reads — is met exactly; only the import path differs.
- **Why not `RotatingFileHandler`.** It routes write failures into `handleError` (stderr) and would hide exactly the failure DATA-07 exists to surface. The sink counts a failed write at `observability.ledger_write` and logs it with the cause.
- **Exactness under a cap.** `_admit()` returns the pending `suppressed` carry for a line it admits, or `None` when capped (incrementing `pending`). On a write failure the spent slot *and* the lost line are returned to `pending`, so a write outage neither loses a count nor exhausts the minute's cap for real errors arriving after it clears.
- **Known limits to state in-code.** (1) size rotation can age a day out of a `20 MB × 10` window under a sustained storm — upgrade path: raise the two env vars, or ship the files with story 22.11's rclone catalog backup. (2) a site's pending `suppressed` only reaches disk on that site's *next* write; nothing calls `close()` in production (Docker sends SIGTERM/SIGKILL) — upgrade path: a periodic time-based flush. (3) the gap↔ledger match is time proximity only (the frozen AC1 line schema carries no instrument id), and only gaps *between* two observed rows are found — upgrade path: structured instrument ids in `detail`, and a last-seen watermark across runs. `DEPLOY_CHECKLIST.md` §6 must say that `(none)` alone is not proof, because a wholly dead instrument also shows no gap.

## Verification

**Commands:**
- `docker build -f platform/collector.dockerfile --network host -t story-23-3/collector .` -- expected: success (never `make up`/`docker compose up` from this worktree).
- `cd platform && docker run --rm --network host -v "$REPO":/src:ro -v "$PWD":/work/platform -w /work/platform -e PLATFORM_SOURCE_DIR=/src/platform -e HOME=/tmp -e USER=collector -u 1000:1000 story-23-3/collector:latest python3 -m pytest -o addopts="" --rootdir=. observability/tests collector_core/tests/test_crosscheck_errors.py data_api/tests/test_data_api.py tests -q` -- expected: all pass.
- Same image, the full Makefile `test` module list -- expected: same 10 pre-existing failures, no new ones; `-W default` shows no `DeprecationWarning` from `platform/` code.
- `uvx ruff format --check` + `uvx ruff check` on every changed file, and `mypy --disallow-incomplete-defs --ignore-missing-imports --follow-imports=silent` -- expected: clean on changed files.
- `python3 -m collector_core.crosscheck_errors --catalog <tmp> --errors-dir <tmp> --since ... --until ...` against a hand-built fixture -- expected: exit 1 with one `UNEXPLAINED` gap; exit 0 once a matching ledger line exists.
