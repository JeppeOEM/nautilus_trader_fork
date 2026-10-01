---
title: 'Story 28.1: Measure the capture hot path and give capture CPU priority'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: 'fbfea6a15f3904daae35962f7687c8dfb2fcd0c4'
final_revision: 'd50ce6bb07526f00230b3b66f62375b82e7fbe87'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-28-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The collectors report no hot-path figures, so a `_second_loop` stall (audit D-10) or an ingest-queue backlog (D-07) cannot be told apart from contention. They also compete for CPU as equals with the box's batch services. Story 28.2 needs measured numbers, not guesses.

**Approach:**
- Each periodic flush logs one line and publishes one Redis record per collector. The record carries: max ingest-queue depth, messages processed, max and p99 second-loop wake-up lag, and the wall time of the last catalog write.
- Compose enables the prepared `cpu_shares` weights and adds evidence-based `mem_limit`s.
- The VPS profile and redeploy checks become one deferred operator entry. The story finalizes `done` and never parks.

## Boundaries & Constraints

**Always:**
- The per-message addition to `_process_data` is `qsize()`, a compare and a counter increment. It adds no allocation per message.
- `tests/test_hotpath.py` passes unchanged against the committed `tests/fixtures/hotpath_baseline.json`: allocations ≤ baseline, wall time ≤ 2× baseline.
- Wake-up lag is `actual wake ns − the scheduled wake ns the loop slept toward`, clamped at ≥ 0.
  - Arrival mode sleeps toward `_next_sample_at`; venue mode toward `_next_close_at`.
  - A venue-mode early wake (`due` empty) records nothing.
- Lag samples are held per flush window, bounded by the wake count, and reset at each report. p99 uses the nearest rank.
- Write time: `time.perf_counter_ns` around `self._archive.write(items)` inside the worker thread, not around `to_thread`. The last successful call of the window is reported, `None` if there was none.
- The existing `_warn_if_late` canary line keeps its text and threshold.
- A failed hot-path publish is ledgered at a new `capture/application/sites.py` constant, `collector.hotpath_publish`. It never raises out of the flush loop and never affects Parquet.
- Operator steps follow OPS-01: they go to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" under the key `28-1-capture-hotpath-metrics-cpu-priority-and-vps-profile`. Never write `operator_actions`, `awaiting-operator` or `sprint-status.yaml`.
- The docs change in the same commit as the code.

**Block If:**
- The hot-path figures cannot stay ≤ baseline without re-recording `hotpath_baseline.json`. That is 28.2's first commit, not this story's.

**Never:**
- No drop policy, `maxsize` or backpressure on `_ingest_queue` (DATA-05).
- No `cpus:` hard cap and no `deploy.resources`.
- No new dependency. `py-spy` stays a host tool.
- No change to `nautilus_trader/` or `crates/`.
- No new HTTP endpoint.
- Never stop, restart or wipe the `verify-*` containers or the compose project `verify`.
- None of 28.2's fixes (encoder, queue hand-off, uvloop, book).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Normal flush | 500 msgs processed, peak `qsize` 37, 60 wakes, 2 writes | Log line and record: `queue_depth_max=37`, `messages_processed=500`, lag max/p99 in ms, `write_data_ms` of the 2nd write, `wakes=60` | No error expected |
| Idle window | 0 msgs, no batch written | `messages_processed=0`, `queue_depth_max=0`, `write_data_ms=None` | No error expected |
| No wakes yet | Flush before the first loop wake | lag max/p99 `None` | No error expected |
| Late wake | One wake 3.2 s after its target | `lag_max_ms≈3200`; the canary WARNING still fires | No error expected |
| Redis down | `publish_hotpath` raises | Log line still emitted, next flush unaffected | Ledgered once per failed flush at `collector.hotpath_publish` |
| Failed write | `archive.write` raises | That call does not set `write_data_ms` | Existing `FLUSH_WRITE` ledger path unchanged |
| No live stream | `live_stream=None` (tests/tools) | Log line only | No error expected |

</intent-contract>

## Code Map

- `platform/capture/application/capture_service.py`
  - `_ingest_queue` :503, `_on_data`/`_ingest_loop`/`_process_data` :702-725
  - flush reports :827-960, `_flush_once` :963 (the write at :981)
  - `_flush_loop` :1173, `_publish` :1441, `_second_loop` :1454, `_venue_second_loop` :1485, `_warn_if_late` :1514
  - `self._venue` :469
- `platform/capture/application/ports.py:199` -- the `LiveStream` Protocol, which gains `publish_hotpath`.
- `platform/capture/infrastructure/redis_stream.py` -- `RedisLiveStream`, owns the lazy Redis client.
- `platform/capture/application/sites.py:57` -- site constants (`SNAPSHOT_PUBLISH` pattern).
- `platform/tests/test_hotpath.py`, `tests/fixtures/hotpath_baseline.json` -- the budget. It drives `_process_data` with the queue bypassed.
- `platform/capture/tests/test_collector.py:147` -- the `_collector(tmp_path, ...)` construction helper; `test_redis_pub.py` has the publish tests.
- `platform/docker-compose.yml` -- CPU-priority rationale :18-31 and commented `# cpu_shares:` lines on every service except redis.
- `platform/tests/test_compose_verify.py:95-230` -- the `_yaml`/`_services` compose parser to reuse.
- `platform/docs/DATA_DICTIONARY.md` -- per-context Redis sections: §1.12 is at :559, §1.13 at :637.
- `platform/docs/DATA_INTEGRITY_AUDIT.md` -- D-07 :54, D-10 :57; D-66 is taken, the highest ID is D-135.
- `platform/docs/DEPLOY_CHECKLIST.md` -- §6 :403, §8 :483 (no §7), "Deferred operator actions" :657.

## Tasks & Acceptance

**Execution:**
- [x] `platform/capture/application/hotpath_metrics.py` (new, LGPL header) -- a pure window type:
  - `note_lag(ns)`, `note_write(ns)` and `take(depth_max, processed) -> HotPathReport`;
  - `HotPathReport` is a frozen dataclass with `to_dict()` and a `log_text()`, plus nearest-rank p99; `take` resets the window.
  -- Keeps the arithmetic testable, out of the 2400-line service.
- [x] `platform/capture/application/capture_service.py`:
  - per message in `_process_data`: sample `qsize()` into `_queue_depth_max` and increment `_messages_processed`;
  - lag: both loops compute their sleep target and call a `_note_wake(now_ns, target_ns)` helper next to `_warn_if_late`;
  - write time: the timed write is a small sync wrapper passed to `to_thread`;
  - reporting: a `_report_hotpath()` runs in `_flush_loop` right after `_flush_once()`. It logs at INFO (prefix `hotpath:`), resets the per-message counters and publishes through `_live_stream.publish_hotpath(self._venue, report.to_dict())`, with failures ledgered.
  -- AC 1.
- [x] `platform/capture/application/sites.py` -- add `HOTPATH_PUBLISH = "collector.hotpath_publish"`.
- [x] `platform/capture/application/ports.py`, `platform/capture/infrastructure/redis_stream.py` -- `publish_hotpath(venue: str, report: dict) -> None` is added to the Protocol and to `RedisLiveStream`. `RedisLiveStream` sends one pipeline: `PUBLISH capture:hotpath <json>` and `SET capture:hotpath:<venue lower> <json>`. The payload carries `venue` and `ts` (ns). Update the docstrings; they currently say the port is snapshot-only. -- This is the carrier (see Design Notes).
- [x] `platform/capture/tests/test_hotpath_metrics.py` (new) and additions to `capture/tests/test_collector.py` / `test_redis_pub.py` -- cover every I/O-matrix row:
  - p99 nearest rank on known lists, empty window, reset after `take`;
  - `_process_data` depth and count through a real `CaptureService`;
  - `write_data_ms` set on a successful write only;
  - a recording `LiveStream` receives the dict; a raising one ledgers `collector.hotpath_publish`;
  - `_note_wake` clamps and records;
  - `RedisLiveStream.publish_hotpath` issues PUBLISH and SET (fake client, as the existing redis_pub tests do).
- [x] `platform/tests/test_compose_cpu_budget.py` (new) -- reuses `test_compose_verify`'s parser by importing it, never a copy. It asserts:
  - `collector`, `bybit_collector` and `hyperliquid_collector` have `cpu_shares: 1024` and a `mem_limit`;
  - `archive`, `ranking_engine`, `data_api`, `bot_tui`, `live-paper` and `dozzle` have `cpu_shares: 256`;
  - no service sets `cpus` or `deploy`.
- [x] `platform/docker-compose.yml`:
  - uncomment the `cpu_shares` lines and rewrite the rationale header from "not enabled yet" to "applied by Story 28.1";
  - add a `mem_limit` per collector, with its evidence comment (values in Design Notes) and a `Known limit:` comment on plan growth;
  - `redis` stays at the default weight (1024), with a one-line reason: it is on capture's publish path.
- [x] `platform/docs/DATA_DICTIONARY.md` -- a new §1 subsection after the last §1.x for the `capture:hotpath` channel and `capture:hotpath:<venue>` key:
  - payload fields with their units, cadence (per periodic flush; not the final shutdown flush), producer, and "no reader yet: `redis-cli GET`";
  - `collector.hotpath_publish` added to the per-flush site list at :12.
- [x] `platform/docs/DATA_INTEGRITY_AUDIT.md`:
  - D-07: strike "Needs a queue-depth metric first" in `~~…~~`, citing Story 28.1's `queue_depth_max` and §1.x; the status stays OPEN (measured, no policy);
  - D-10: add the lag max/p99 figures;
  - new row D-146: the "VPS capture profile (Story 28.1)" is OPEN, with the planned README path, and is filled by the deferred entry;
  - note in D-146 that the epic text's "D-66" was already taken.
- [x] `platform/docs/DEPLOY_CHECKLIST.md`:
  - new `## 7. Capture CPU budget (Story 28.1)` before §8, covering:
    - redeploy order: `make build` if needed, then `docker compose up -d` recreating the collectors first, then the batch services;
    - the one-line acceptance check: `uptime` load ≤ cores over one hour, and zero `_second_loop tick arrived … late` lines in Dozzle over that hour;
    - an OOM check: `docker inspect -f '{{.State.OOMKilled}}'`;
    - how to read `capture:hotpath`.
  - one entry under "Deferred operator actions" headed `### 28-1-capture-hotpath-metrics-cpu-priority-and-vps-profile (commit: this story's)` with checkboxes for:
    - the §7 redeploy and checks;
    - one `py-spy record --pid <pid> --duration 300 --format speedscope` per running collector, plus `py-spy dump` during a logged late tick, alongside `uptime`, `free -h` and `docker stats --no-stream`;
    - committing them under `platform/.planning/debug/capture-profile-2026-<date>/` with a README that ranks the top-10 self-time frames per venue as (a) our Python, (b) Nautilus Cython/Rust, or (c) interpreter/asyncio, and states whether the box was contended;
    - updating D-146;
    - a note that 28.2 does not wait for this.

**Acceptance Criteria:**
- Given a running collector, when a periodic flush completes, then exactly one `hotpath:` INFO line and one `capture:hotpath` publish plus `SET` carry the six figures (depth max, processed, lag max, lag p99, wakes, last write ms), and the counters restart from zero.
- Given the unchanged baseline file, when `python3 -m pytest tests/test_hotpath.py` runs, then all its tests pass.
- Given `docker-compose.yml`, when parsed, then the collectors have weight 1024 and a `mem_limit`, the batch services 256, and there is no `cpus`/`deploy`; each `mem_limit` has its evidence comment.
- Given the docs, when read, then DATA_DICTIONARY names the channel and key, D-07's clause is struck with a citation, D-146 exists, §7 exists, and the deferred entry exists under this story's key.

## Spec Change Log

## Review Triage Log

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 4, low 8)
- defer: 1: (high 0, medium 1, low 0)
- reject: 13: (high 0, medium 0, low 13)
- addressed_findings:
  - `[medium]` `[patch]` `write_data_ms` (last write only) hid a slow earlier batch -> added `writes` and `write_data_max_ms` (kept the last-write figure the AC names); §7 reads the max.
  - `[medium]` `[patch]` `queue_depth_max` was blind to a backlog behind a starved ingest loop -> the report also samples `qsize()`; test added.
  - `[medium]` `[patch]` a raising `_flush_once` skipped the report and merged windows -> report in `finally`; the final flush of a stop/crash now reports its partial window too; test added.
  - `[medium]` `[patch]` `mem_limit` without `memswap_limit` lets a collector swap (stall) instead of failing visibly -> `memswap_limit` equal on all three; test and §7 inspect line updated.
  - `[low]` `[patch]` no window length in the record -> `window_s` (monotonic).
  - `[low]` `[patch]` p99 equals max at ~60 wakes -> documented in `HotPathReport` and DATA_DICTIONARY; a test shows where they part.
  - `[low]` `[patch]` `perf_counter_ns` read through the faked `time` module -> module-level `from time import perf_counter_ns`; the `test_stale_trade_burst.py` fake edit reverted.
  - `[low]` `[patch]` dYdX 1690m from a D-06/D-07-era RSS -> `Known limit:` with re-measure upgrade path in compose.
  - `[low]` `[patch]` per-instrument slope overstated -> reworded as a cross-venue estimate from short dev-box peaks.
  - `[low]` `[patch]` no rollback in §7 -> rollback bullet added.
  - `[low]` `[patch]` `messages_processed` includes messages that then failed -> semantics documented in DATA_DICTIONARY.
  - `[low]` `[patch]` hot-path wall-time cost unrecorded -> measured base vs new under the same load, recorded in audit D-65.

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 0, low 8)
- defer: 0
- reject: 18: (high 0, medium 0, low 18)
- addressed_findings:
  - `[low]` `[patch]` p99 boundary off by one in `HotPathReport` and DATA_DICTIONARY ("from 101 wakes" / "up to 100") -> "from 100 wakes" / "up to 99"; parametrized boundary test at 99 and 100 wakes.
  - `[low]` `[patch]` a stop that cancelled `_flush_loop` mid-flush emitted two records (loop `finally` plus `run()`'s final report) -> the loop reports on success or `Exception` only, re-raising `CancelledError`; test added.
  - `[low]` `[patch]` a second cancellation during the final report's publish skipped `_live_stream.close()` -> close moved into an outer `finally` in `run()`; test added.
  - `[low]` `[patch]` wake lag is wall-clock (an NTP step reads as lag) -> `Known limit:` with upgrade path in `_note_wake` and DATA_DICTIONARY §1.25.
  - `[low]` `[patch]` arrival mode's target is recomputed after the tick's own work, so a stall inside `_sample_tick`/`_publish` shows as `missed_tick`, not lag (venue mode counts it) -> same `Known limit:` documents the asymmetry.
  - `[low]` `[patch]` `HotPathWindow`'s "bounded by the window" invariant fails while a flush hangs -> `Known limit:` with upgrade path in its docstring.
  - `[low]` `[patch]` docs disagreed with code: the site list said "once per failed periodic flush" (the final report also publishes), D-146 omitted `window_s`/`writes`/`write_data_max_ms` -> both corrected.
  - `[low]` `[patch]` `hotpath_metrics.py` module docstring line at 130 columns -> reflowed to 100.

## Design Notes

**Carrier.** The AC names "the existing per-flush Redis channel the dashboard reads", but none exists. Capture publishes only `snapshots:raw` (every second). `collector:status` is collection-control's publisher with a 30 s/1800 s cadence and an append-only replay-tested contract, and `/api/errors` reads ledger files. The intent is visibility with no new endpoint.
- A dedicated channel plus a latest-value key on capture's existing Redis client meets that intent without coupling to another context's contract.
- The key makes it pull-readable (`redis-cli GET capture:hotpath:bybit`).
- Record this deviation in the DATA_DICTIONARY section.

**mem_limit evidence** (collect a fresh `docker stats --no-stream` of `verify-bybit-collector`/`verify-hyperliquid-collector` read-only before committing; the numbers below were taken 2026-09-30 on the dev box; limit = cgroup `memory.peak` × 1.5, rounded up to a whole MiB):

| Service | `docker stats` | cgroup peak | `mem_limit` | Instruments |
|---|---|---|---|---|
| `bybit_collector` | 204.9 MiB | 234.5 MiB | `352m` | 4 |
| `hyperliquid_collector` | 157.7 MiB | 165.2 MiB | `248m` | 1 |
| `collector` (dYdX) | 1.1 GiB (nifelheim RSS, ~25 markets, `.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md`) | — | `1690m` | ~25 |

**Known limit (compose comment and §7):**
- The limits are sized for today's plan, and `collector:control` can grow a plan at runtime without a redeploy.
- Rough scaling: ~16 MiB per Bybit instrument (the (205−158)/3 difference). The Bybit limit is therefore reached at about 12 instruments.
- An exceeded limit OOM-kills the collector and `restart: always` restarts it, which leaves a visible gap and `OOMKilled=true`.
- Upgrade path: re-measure and raise `mem_limit` before growing a plan; 28.2's scale burst gives per-instrument figures.

## Verification

**Commands:**
- `cd platform && python3 -m pytest capture/tests/test_hotpath_metrics.py capture/tests/test_collector.py capture/tests/test_redis_pub.py capture/tests/test_venue_time.py capture/tests/test_coverage.py tests/test_compose_cpu_budget.py tests/test_compose_verify.py tests/test_compose_profiles.py tests/test_images.py tests/test_boundaries.py -q` -- expected: all pass.
- `cd platform && python3 -m pytest tests/test_hotpath.py -q` -- expected: pass against the unchanged baseline.
- `cd platform && ruff check . && ruff format --check . && mypy capture` (or the repo's pre-commit on changed files) -- expected: clean.
- `docker compose -f platform/docker-compose.yml config -q` -- expected: valid (config only; never `up`/`down` against `verify`).

## Auto Run Result

**Summary.** Follow-up review pass on the committed Story 28.1 (`863dc0abe3`): per-flush hot-path figures (`hotpath:` INFO line, `capture:hotpath` publish plus `SET capture:hotpath:<venue>`), compose `cpu_shares` 1024/256 and measured `mem_limit`/`memswap_limit` on the collectors, and the OPS-01 deferred operator entry. This pass fixed two shutdown-path edge cases and six documentation inaccuracies; no behaviour on the per-message hot path changed.

**Files changed in this pass**
- `platform/capture/application/capture_service.py`: `_flush_loop` no longer reports on a cancelled flush (one record per stop); `run()` closes the live stream in an outer `finally`; `_note_wake` documents the wall-clock and arrival-mode limits.
- `platform/capture/application/hotpath_metrics.py`: p99 boundary wording fixed, hang `Known limit:` on the window invariant, docstring reflowed to 100 columns.
- `platform/capture/tests/test_collector.py`: tests for the cancelled-flush and cancelled-final-report paths.
- `platform/capture/tests/test_hotpath_metrics.py`: p99 boundary test at 99 and 100 wakes.
- `platform/docs/DATA_DICTIONARY.md`: site-list cadence for `collector.hotpath_publish`, p99 boundary, lag `Known limit:`.
- `platform/docs/DATA_INTEGRITY_AUDIT.md`: D-146 lists every published figure.

**Review.** 8 patches applied (all low), 0 deferred, 18 rejected (mostly spec-mandated choices already recorded as `Known limit:`s or triaged in the previous pass: mem_limit sizing and OOM on plan growth, the new channel instead of a dashboard feed, p99 redundancy at 60 wakes, the per-message cost). No bad_spec or intent_gap.

**Verification**
- `python3 -m pytest capture tests` (from `platform/`): 871 passed, 1 failed, the same pre-existing `tests/test_legacy_names.py::test_only_published_language_keeps_a_legacy_name` failure as on the baseline tree.
- The story's targeted command list: 393 passed; `tests/test_hotpath.py` passes against the unchanged baseline (5 passed).
- `ruff check` / `ruff format --check` (v0.15.16, the pre-commit pin) clean on the changed Python files; `mypy` 1.20.2 reports nothing in the changed sources.
- `docker compose -f platform/docker-compose.yml config -q` passes. No container was started, stopped or restarted.

**Residual risks**
- Unchanged from the first pass: `mem_limit`s sized for today's plan (Known limit plus the deferred memory-pressure canary), `lag_p99_ms` equal to `lag_max_ms` at the default window, and no consumer of `capture:hotpath` beyond `redis-cli` and Dozzle.
- Lag stays wall-clock and blind to stalls inside the arrival-mode tick itself; both are now documented `Known limit:`s with an upgrade path.
