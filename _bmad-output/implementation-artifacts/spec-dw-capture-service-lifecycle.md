---
title: 'Capture service lifecycle gaps (DW-189, DW-233, DW-237, DW-241, DW-243, DW-264, DW-267)'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: '83e28ba6c9d6e5c9094d09ecdab73922a7aeaabf'
baseline_revision: 'e6272ad9c60b117d3ece98bfa8cb15bd54fb7963'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** `CaptureService` and `run_forever` have seven lifecycle gaps from the deferred-work ledger. The catch-up ledgers one line per instrument (DW-189). Turning on `store_order_book_deltas` for an id that is already collected starts a headless delta archive (DW-233). `poll_loop` sleeps before its first fetch (DW-237). A `build()` failure exits the process (DW-241). Config accepts `nan`/`inf` and truncated ints (DW-243). The coverage file is missing until the first line (DW-264). The ingest backlog is abandoned unprocessed and unledgered at shutdown (DW-267).

**Approach:** Fix each one where it sits in `capture/application/` (plus the coverage-file adapter). Reuse the patterns already in the code: the flush path's consolidated ledger line, `_resync_wire`, `_number`/`_non_negative`-style strict parsing, and the ingest stop sentinel. Delete each `Known limit:` that the fix retires.

## Boundaries & Constraints

**Always:**
- `CaptureService._ledger` stays capture's single `error_ledger.record` call site (`tests/test_boundaries.py`). Make it a `@staticmethod` so `run_forever` can ledger a build failure without an instance. Every site comes from `capture.application.sites`, and a new site constant must be used.
- The ingest hot path (`_ingest_loop`, `_process_data`) gains no per-message work for ids whose deltas are not stored.
- Every failure is ledgered, never only logged (DATA-07). Nothing queued is dropped silently (DATA-05).
- LGPL header, ruff (line length 100), mypy, cognitive complexity ≤ 10, functions about 30 lines or fewer, pytest function tests without classes.

**Block If:** a fix needs a change under `nautilus_trader/` or `crates/`.

**Never:** edit the deferred-work ledger. Change the coverage line format or verification code. Add a dependency. Touch dYdX-specific behaviour beyond the generic `apply` path (dYdX work is deferred, except where the shared service needs it).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Catch-up all fail | sink `watermarks()` ok, `apply` raises for N ids | one `collector.candle_store_catch_up` line naming all N, with counts per exception type and the traceback of the most frequent type | no raise |
| Deltas newly stored | id already applied and in the plan; `diff.store_deltas` gains it; client has `resync_orderbook` | local book dropped, `_resync_wire` called, messages before the first one whose `deltas[0].is_clear` is set are not buffered, that message and later ones are | resync failure: ledgered by `_resync_wire`, retried by the sampler; the gate stays armed |
| Deltas newly stored, id in `diff.added` | fresh subscribe | no extra resync; the gate passes the subscribe snapshot | — |
| Poll first round | `poll_loop(every_seconds=3600)` | fetch runs immediately, rows buffered, then sleeps | failed round ledgered, loop continues |
| Build raises | `build()` raises on any attempt | ledgered at `collector.crash` ("build failed"), back off (interruptible by shutdown), retry | SIGTERM during backoff ends the loop at once |
| Config nan/inf | any float key = `nan`/`inf` (`stale_book_seconds`, `feed_stale_seconds`, `book_crosscheck_seconds`, ...) | `ValueError` naming the key | — |
| Config int truncation | `flush_interval_seconds = 1.5` or `true`; `seen_trade_ids = 2.0` or `true` | `ValueError` naming the key | — |
| Coverage at start | fresh catalog, `_run` starts | `coverage/<venue>.jsonl` exists (empty), fsync'd with its directory, before the loops start | OSError ledgered at `collector.coverage_write`, run continues |
| Clean stop with backlog | `stop()` with K queued messages | ingest task awaited to its sentinel (bounded by `_INGEST_DRAIN_S`), then the other loops cancelled; after `_disconnect` the post-sentinel residue is processed; the final flush writes all of it | bound hit: one line at a new site naming the abandoned count |
| Crash with backlog | a loop dies, no sentinel | after `_disconnect`, the queue is processed within the same deadline; anything left is ledgered | as above |

</intent-contract>

## Code Map

- `platform/capture/application/capture_service.py` -- `_apply_to_candle_store` (consolidation pattern), `_catch_up_candle_store`, `apply`, `_resync`/`_resync_wire`, `_process_data` (buffers deltas when `iid in self._delta_store`), `_ingest_loop`, `poll_loop`, `_run` (and its `finally`), `stop`, `_ingest_backlog`, `run_forever`, `_ledger`
- `platform/capture/application/config.py` -- `core_config_from_dict`, `_check_time_source`
- `platform/capture/application/sites.py` -- site constants (`CRASH`, `COVERAGE_WRITE`, `CANDLE_STORE_CATCH_UP`, ...)
- `platform/capture/application/ports.py` -- `ArchiveWriter` Protocol (`append_coverage`)
- `platform/capture/infrastructure/parquet_writer.py` -- `ParquetArchiveWriter.append_coverage` (`_coverage_lock`)
- `platform/capture/infrastructure/coverage_file.py` -- `coverage_path`, `append_lines`
- `platform/capture/infrastructure/config.py` -- `_number`/`_non_negative` (model for the strict helpers; do not import across the layer)
- `platform/capture/domain/live_book.py` -- `LiveBook.resync()`, `items[0].is_clear` snapshot test
- `platform/capture/tests/` -- `test_poll_loop.py`, `test_config.py`, `test_coverage.py` (catch-up at ~:970), `test_apply.py`, `test_collector.py` (`_collector` helper)

## Tasks & Acceptance

**Execution:**
- [x] `capture/application/capture_service.py` -- DW-189: extract the flush path's failure aggregation into one helper, `(site, what, failures: dict[iid, Exception])`. Use it from both `_apply_to_candle_store` and `_catch_up_candle_store`, which collects failures and ledgers once after its loop -- one line per start, not per instrument.
- [x] `capture/application/capture_service.py` -- DW-233: in `apply`, compute the ids newly in `diff.store_deltas`. Keep a `_delta_head_pending` set, intersected with the new store set, and arm every newly stored id. Under the lock, after removes and adds, for each newly stored id that is collected and not in `diff.added`: if the client has `resync_orderbook`, call `LiveBook.resync()` and then `await self._resync_wire(iid)`. In `_process_data` replace the inline append with a helper called only when `iid in self._delta_store`. The helper drops a message while the id is armed unless `deltas.deltas` is non-empty and `deltas.deltas[0].is_clear`; a head disarms it.
- [x] `capture/application/capture_service.py` -- DW-237: `poll_loop` runs one round (fetch, filter, buffer, malformed report, ledger) before each sleep. Delete its `Known limit:`.
- [x] `capture/application/capture_service.py` -- DW-241: `_ledger` becomes a `@staticmethod`. `run_forever` builds inside the backoff: a raising `build()` is ledgered at `sites.CRASH` with a "build failed" detail, then backs off and retries. Replace both backoff sleeps with a wait on `shutting_down` bounded by the backoff, so SIGTERM ends it. Add a module constant `_RESTART_BACKOFF_S = 1.0` for the initial backoff.
- [x] `capture/application/config.py` -- DW-243: strict helpers. A float key is an int or float (not bool), finite, and goes through `float()`. `flush_interval_seconds` and `seen_trade_ids` must be `type(...) is int`. Apply them to every float key, including `feed_stale_seconds`, `book_crosscheck_seconds` and `hold_back_seconds`. Errors name the key.
- [x] `capture/application/ports.py`, `capture/infrastructure/coverage_file.py`, `capture/infrastructure/parquet_writer.py`, `capture/application/capture_service.py` -- DW-264: add `ArchiveWriter.ensure_coverage(venue) -> None`. It creates the parent directory and the file (append mode, no truncation), fsyncs the file and its directory, and runs under `_coverage_lock`. `_run` awaits it via `asyncio.to_thread` before `_connect`. On failure, ledger `sites.COVERAGE_WRITE` and continue. Update test doubles that implement the full port.
- [x] `capture/application/capture_service.py`, `capture/application/sites.py` -- DW-267: in `_run`'s `finally`, take a deadline of `_INGEST_DRAIN_S` (2.0 s, documented as a `Known limit:` since compose allows a 10 s grace) from now. If `_stop` is set, await the ingest task to its sentinel via `wait_for(shield(...))` within the deadline, before cancelling the other loops. After `_disconnect` and before `_close_report_cycle`, process the remaining queued items synchronously within the deadline, skipping a sentinel (and clearing `_ingest_stop_queued`), with the same per-message try/ledger as `_ingest_loop`. If `_ingest_backlog() > 0` remains, ledger the count once at the new site `INGEST_ABANDONED = "collector.ingest_abandoned"`. Leave `_ingest_loop` itself unchanged. Delete the `Known limit:` on `stop()`.
- [x] `capture/tests/` -- one test (at least) per matrix row: catch-up consolidation, the delta-head gate plus the resync call for an already-collected id (none for an added id), the poll fetching before its sleep, `run_forever` surviving a raising build (a build that raises after `os.kill(os.getpid(), SIGTERM)` returns cleanly with one `collector.crash` line; a raise-then-raise-plus-SIGTERM sequence with `_RESTART_BACKOFF_S` monkeypatched to 0 gives two), nan/inf/bool/float config rejections, the coverage file created at start (and the failure ledgered), and the shutdown drain (backlog processed into the buffer; with the bound at 0 the abandoned count is ledgered).

**Acceptance Criteria:**
- Given the full capture test suite and `tests/test_boundaries.py`, when run, then everything passes, with no new warnings.
- Given the changed code, when `ruff check`, `ruff format --check` and `mypy` run on the touched files, then they report no new findings.
- Given the retired gaps, when `capture_service.py` is grepped, then no `Known limit:` still describes the first-fetch sleep or the undrained shutdown.

## Design Notes

The delta-head gate costs nothing for unstored ids because it runs only inside the existing `if iid in self._delta_store:` branch. A full-snapshot venue (no `resync_orderbook`) needs no wire call: its next message is a Clear-headed snapshot, which disarms the gate. Messages dropped while armed predate the head, so a backtest could not use them, and the archive starts at the head by definition.

The drain runs in two phases. Phase 1 is bounded and runs before the cancel, so the sampler and flush keep running while the backlog clears. Phase 2 runs after `_disconnect`, so the queue is finite. Phase 2 processes what arrived behind the sentinel, because raw trades buffered on accept reach the final flush and the id-by-id trade verification. One deadline covers both phases, so a slow processor cannot eat the final flush's share of compose's stop grace.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests tests/test_boundaries.py -q` -- expected: all pass (compare any failure with a clean-HEAD run)
- ruff `0.15.16` / mypy `1.20.2` from a throwaway scratchpad venv on the touched files -- expected: no findings beyond the HEAD baseline

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 0, medium 1, low 1)
- defer: 1: (high 0, medium 1, low 0)
- reject: 20: (high 0, medium 4, low 16)
- addressed_findings:
  - `[medium]` `[patch]` A stored id re-added while its old subscription still lingers (a failed unsubscribe kept it in `_applied`) was never armed for its head. That subscription's mid-stream deltas, spanning the time the id was out of the plan, were archived before the resync snapshot. Fixed: `apply` arms the gate for these ids (`_arm_lingering_heads`), so the archive resumes at the resync's Clear-headed snapshot. A regression test was added; it fails without the fix.
  - `[low]` `[patch]` `_run` found the ingest task as `tasks[0]`, so it depended on where `_ingest_loop` sat in the loop tuple; a reorder would make `_unwind` spend the drain budget waiting on the wrong loop. Fixed: the ingest task is created and held by name.

## Auto Run Result

**Summary:** a follow-up review of the finished lifecycle change (DW-189, DW-233, DW-237, DW-241, DW-243, DW-264, DW-267), using the diff from `e6272ad9c6` to `5f18706dfe`. It found two small defects, both now patched, and nothing that needs a re-derivation.

**Files changed (this pass):**
- `platform/capture/application/capture_service.py`:
  - added `_arm_lingering_heads`, called under the lock at the start of `apply`'s wire work;
  - the ingest task is now held by name rather than as `tasks[0]`;
  - the `apply` docstring now notes the lingering case.
- `platform/capture/tests/test_apply.py`: added `test_a_stored_id_re_added_while_lingering_archives_from_its_resync_head`.
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new flat entry appended (left uncommitted with the orchestrator's own pending ledger edits). It covers a failure between `_connect` and `_run`'s `try`, which skips the disconnect, drain and flush; this was already there before the change.

**Review:** 2 patches applied (medium 1, low 1), 1 deferred, 20 rejected. Among the rejections:
- The single 2 s budget shared with the disconnect, the 2 s cap after a crash, retrying `build()` on a config error, strict `flush_interval_seconds`, and the immediate first poll after each restart: all of these are what the intent contract asks for. Every deployed TOML uses an integer `flush_interval_seconds`.
- The extra `sleep(0)` cancel point: the same class as the `gather`/disconnect awaits that came before it in the baseline.
- The no-`resync_orderbook` gate: raw deltas are stored for dYdX only, and that client has the method.
- The restart and lock policy items that were already present at the baseline.
- Speculative late-callback and same-name exception cases.

**Follow-up review recommended:** false. The two fixes are small and local, and each is covered by a test.

**Verification:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. capture/tests capture/venues/{dydx,bybit,hyperliquid}/tests tests/test_boundaries.py -q`: 739 passed (738 before, plus the new test).
- The new test was run against the code with the fix stubbed out, and it fails there.
- ruff 0.15.16 check and format: clean on both touched files.
- mypy 1.20.2: only the baseline error in the untouched `kernel/catalog_files.py`.

**Residual risks:**
- The 2 s drain budget is shared with `_disconnect`, which has no timeout of its own, so a slow disconnect turns the residue into ledgered `ingest_abandoned`.
- The delta-head resync briefly drops that instrument's live book (a `Known limit:`).
- The deferred `_run` setup window above.
