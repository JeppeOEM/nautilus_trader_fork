---
title: 'Story 28.2: Remove the measured Python overhead and lower the hot-path baseline'
type: 'refactor'
created: '2026-09-30'
status: 'done'
final_revision: 'd59319364a6a7aa0a81105b23dce6018cf2a03eb'
baseline_revision: '2bab16a304f854482de5bac8185891d40807eae1'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-28-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** One collector must carry 30+ instruments per venue (~1,500 Bybit book messages/s) on a small box without `_second_loop` stalls. Today its Python cost is only measured on a 10-instrument, 20-level burst. The known costs are the per-row dict flush encoder (291 ms for 30 × 60 rows, under the GIL at the `:02` close), a `wait_for` around every queue get, the default asyncio loop, and possibly the Cython book path.

**Approach:**
- Add a `scale` burst (30 instruments per venue, 200-level books), a queued-path variant and a flush-encode timing to `tests/test_hotpath.py`. Record both baselines on the unchanged code.
- Apply the four fixes in the epic's order, each as its own commit carrying its before/after figures. A fix that does not lower its own number is reverted and recorded.
- Re-record the baselines lower and write the closing records.

## Boundaries & Constraints

**Always:**
- **Archive unchanged.** Parquet output stays byte-identical: `kernel` schemas, row values and file names. The existing D-24/schema tests pass unchanged.
- **Every fix keeps both bursts at or below baseline.** Allocations ≤ baseline, and wall time ≤ baseline by the median of ≥ 5 runs on the recording CPU.
- **Commit order:**
  1. The first commit changes only the harness, the Makefile and the two baseline files, with no production code.
  2. Then one commit per fix, in this order: encoder, ingest hand-off, uvloop, book.
  3. Then one closing commit.
- **Commit message figures.** Each fix commit's message carries before/after for both bursts:
  - ns/message, direct and queued;
  - retained and peak bytes/message;
  - flush encode ms and flush write ms;
  - the load average, and a note when contention was visible.
- **Figures are medians.** Take every timing as the median of repeated runs; a single run is never a figure.
- **No data loss.** No drop policy, `maxsize` or backpressure on `_ingest_queue` (DATA-05). The yield-every-64 rule stays.
- **Operator steps follow OPS-01.** VPS steps go to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" under `28-2-capture-python-overhead-removed-baseline-lowered`, and the story finalizes `done`.
- **Docs travel with the code.** The docs a commit touches are updated in that same commit.

**Block If:**
- Byte-identical Parquet cannot be reached with a columnar encoder.

**Never:**
- Never write `sprint-status.yaml`, `operator_actions` or `awaiting-operator`, and never revert a change to `sprint-status.yaml`.
- Never modify `nautilus_trader/` or `crates/`. No new dependency.
- Never stop, restart or wipe the `verify-*` containers or the compose project `verify`, and never `docker compose up`/`down`.
- Never change the dYdX `[WS_RAW]` sink (out of scope).
- Never re-record a baseline to make a regression pass. The only re-recording is the closing one.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Columnar encode | 30 × 60 rows with mixed `None`/set OHLC, several instruments | One `RecordBatch`, equal to the dict path's table; Parquet files byte-identical | No error expected |
| Empty batch | `[]` | An empty batch with the schema (or the catalog's early return); no crash | No error expected |
| Unencodable row | A count over uint32 or a value over int64 | The same refusal the dict path gives today (the existing tests unchanged) | Raises; the flush ledgers `FLUSH_WRITE` as today |
| Stop | `stop()` while the ingest task waits on an empty queue | The ingest loop returns promptly (no 1 s poll) | No error expected |
| Stop with backlog | `stop()` with N queued messages | The N messages are processed, then the loop returns at the sentinel | A processing error is ledgered at `PROCESS` as today |
| Double stop | `stop()` twice | No error; the extra sentinel is harmless | No error expected |
| uvloop missing | `import uvloop` fails | Default asyncio loop; the startup log line names the fallback and its reason | No error expected |

</intent-contract>

## Code Map

- `platform/tests/test_hotpath.py` -- harness. Current state:
  - it drives `_process_data` directly (queue bypassed), 3 venues × 10 instruments, 20 levels, 50 deltas;
  - `measure()` runs in a subprocess started with `--measure`;
  - `_baseline()` self-records a missing file;
  - wall time is asserted ≤ `_WALL_TIME_FACTOR` (2.0) × baseline on the same CPU.
- `platform/tests/fixtures/hotpath_baseline.json` -- the current baseline (3,130 messages, 0.7128 / 89.154 / 104.9495, 2,720 ns). It was recorded on this host's CPU and Python 3.13.13; host allocation figures reproduce it exactly.
- `platform/Makefile:279-291` -- the `hotpath-baseline` target, which today removes and records one file.
- `platform/kernel/second_snapshot.py` -- schema and encoding code:
  - `schema()` :580;
  - `to_dict` :605, which calls `encode_book_prices` :179;
  - `register_arrow(..., encoder=make_dict_serializer(...), decoder=...)` :756-761;
  - no `batch_encoder` is registered yet.
- `nautilus_trader/serialization/arrow/serializer.py` (read-only) -- the catalog side:
  - `register_arrow(..., batch_encoder=)` :89;
  - `serialize_batch` :255 prefers `_ARROW_BATCH_ENCODERS[cls](list) -> RecordBatch`;
  - `dicts_to_record_batch` swallows exceptions (prints them and returns `None`).
- `platform/kernel/tests/test_second_snapshot.py` -- the encode tests: overflow :258/:418 and the Parquet round-trip :390.
- `platform/capture/application/capture_service.py` -- the service:
  - `_INGEST_YIELD_EVERY` :221; `_ingest_queue` :514; `_stop` :525;
  - `_on_data` :723; `_ingest_loop` :730 (`asyncio.wait_for(get(), 1.0)`); `_process_data` :746;
  - `stop()` :2427, `run_forever` :2431 (`basicConfig` :2440), `_stop_on_shutdown` :2503.
- `platform/capture/venues/{dydx,bybit,hyperliquid}/__main__.py` -- `asyncio.run(...)` at dydx :320, bybit :144 and hyperliquid :123.
- `platform/capture/domain/live_book.py` -- `LiveBook`, the only module naming the Cython `OrderBook`:
  - `apply` :136 applies one level at a time;
  - `snapshot_top` :290 reads `book.bids()[:depth]`.
- `platform/capture/domain/sampler.py` -- `SecondSampler`, `_exact_level`.
- Each venue's `client.py` runs `capsule_to_data`: dydx :237, bybit :312, hyperliquid :330.
- Docs:
  - `platform/docs/DATA_INTEGRITY_AUDIT.md` D-65 :202 and D-146 :302 (28.1's VPS-profile row; the epic's "D-66" is D-146);
  - `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" :720, where the 28-1 entry sits at :1197;
  - `platform/CLAUDE.md` "Adding a venue" :93, whose step 5 quotes `asyncio.run(run_forever(...))`;
  - `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` `## Deferred` :508.
- `platform/.planning/debug/capture-profile-*` -- does not exist (28.1's VPS profile has not landed).

## Tasks & Acceptance

**Execution:**
- [x] **Commit 1** -- `platform/tests/test_hotpath.py`, `platform/Makefile`, `platform/tests/fixtures/hotpath_baseline.json` (re-recorded), `platform/tests/fixtures/hotpath_baseline_scale.json` (new). Production code is unchanged.
  - Parametrize the harness by burst:
    - `default`: today's shape;
    - `scale`: 30 instruments per venue, `_BOOK_LEVELS` 200, the same 50 deltas and trades.
    - Scale tickers are generated beyond `_TICKERS`, and ids are valid for each venue's format.
    - `--measure <burst>`.
  - The measured record is nested:
    - `direct`: the three allocation keys plus `ns_per_message`, exactly as today;
    - `queued`: the same keys, on the queued variant (Design Notes);
    - `flush`: `rows`, `encode_ms` and `write_ms` for one venue batch of `instruments_per_venue × 60` rows (Design Notes);
    - `scale` only: `book_path` with `per_message_ns`, `reads_per_second_ns` and `core_share_pct_at_1500` (Design Notes).
  - The baseline paths are `hotpath_baseline.json` and `hotpath_baseline_scale.json`, under the same `PLATFORM_SOURCE_DIR` rule.
  - Tests are parametrized over both bursts:
    - shape and message-count equality;
    - allocations ≤ baseline, for direct and queued;
    - wall time ≤ `_WALL_TIME_FACTOR` × baseline, for direct ns, queued ns and flush `encode_ms`, on the same CPU (see Design Notes on 2×);
    - `book_path` is recorded, never asserted.
  - `make hotpath-baseline` removes and records both files and checks both exist.
  - Update the module docstring.
  - Record both baselines on the unchanged production code:
    - delete the files and run the test on the host, which has the same CPU and Python as the image-recorded 26.3 baseline;
    - run the test again and confirm it passes.
  - The commit message carries the recorded figures and `book_path.core_share_pct_at_1500`, the fix-4 decision number (median of ≥ 5 `--measure scale` runs).
- [x] **Commit 2 (encoder)** -- `platform/kernel/second_snapshot.py`, `platform/kernel/tests/test_second_snapshot.py`.
  - Add `snapshots_to_record_batch(rows) -> pa.RecordBatch`:
    - one `pa.array` per schema column, in schema order and with schema types (`instrument_id` a `dictionary(int8, string)` array);
    - it raises on every value today's `to_dict` path refuses, and never returns `None`.
  - Pass it as `batch_encoder=` in the same `register_arrow` call, and keep `to_dict`/`encoder` for single-row use.
  - Tests:
    - a fixture batch (several instruments, set and `None` OHLC, varied depth including an empty side if the type allows) gives an equal `pa.Table` through both encoders;
    - byte-identical Parquet files written through two `ParquetDataCatalog`s, the reference with the dict encoder forced;
    - decode round-trip equality;
    - overflow refusals through the batch path;
    - an empty-list case.
  - Record `encode_ms` against the ≤ 20 ms target for 30 × 60 rows (scale flush).
  - Update the `ArchiveWriter` port docstring (`capture/application/ports.py:107`), which says the batch encoder lives behind it: it is now registered with the kernel type, and callers are unchanged.
- [x] **Commit 3 (ingest)** -- `platform/capture/application/capture_service.py` plus tests in `platform/capture/tests/`.
  - Add a module sentinel `_INGEST_STOP`.
  - `stop()` sets `_stop` and `put_nowait(_INGEST_STOP)`.
  - `_ingest_loop` becomes `item = await self._ingest_queue.get()`, returns on the sentinel, and keeps the yield-every-64 rule and the `PROCESS` ledger.
  - Route every place that sets `_stop` through `stop()`.
  - Tests cover the matrix rows Stop, Stop-with-backlog and Double-stop, with a real `CaptureService`.
  - Adjust any existing test that relied on the 1 s poll.
- [x] **Commit 4 (uvloop)** -- *measured, then reverted and not committed: queued time fell 7,698 → 5,698 ns (scale), but the scale queued peak rose to 144.0196 B/msg, above the Commit-1 baseline of 141.4897. The binding "allocations ≤ baseline" rule applies, and the figures and upgrade path are recorded in D-65. The patch is kept in the session scratchpad as `uvloop-fix3-reverted.patch`.* -- new `platform/capture/application/event_loop.py` (LGPL header), the three `__main__.py`, `capture_service.py` `run_forever`, `test_hotpath.py`, plus a test.
  - Add `loop_factory()`: `uvloop.new_event_loop` when importable, else `asyncio.new_event_loop`, remembering the fallback reason.
  - Add `run(main)`: `asyncio.run(main, loop_factory=loop_factory())`.
  - Add `describe()`, e.g. `uvloop 0.22.1` or `asyncio (uvloop unavailable: <reason>)`.
  - `run_forever` logs `event loop: <describe()>` at INFO right after `basicConfig`.
  - The entrypoints call `event_loop.run(...)`.
  - The harness's queued variant builds its loop with `loop_factory()`.
  - Tests cover the uvloop-present and uvloop-absent paths (monkeypatched import) and the log text.
  - Update `platform/CLAUDE.md` "Adding a venue" step 5's `asyncio.run(...)` line.
- [x] **Fix 4 (book)** -- decided by Commit 1's `core_share_pct_at_1500`.
  - Below 1 %: no code, and no fix commit. The closing commit records "not taken: <figure> % of a core at the scale burst" and names the pyo3 swap as the next step when the fleet grows past it.
  - At or above 1 %: its own commit per the epic.
    - `LiveBook` keeps `nautilus_pyo3.OrderBook`, with one `apply_deltas(deltas.to_pyo3())` per message and `bids(BOOK_DEPTH)`/`asks(BOOK_DEPTH)` reads.
    - The dYdX tagger/uncross, the Bybit `u` canary and the cross-check captures stay correct on pyo3 types; the DATA-04/DATA-08 tests are unchanged.
    - A test shows that a 500-level book and a 20-level book give identical snapshots.
    - `capsule_to_data` stays for every type (Design Notes).
- [x] **Closing commit** -- the re-recorded baselines and the closing records.
  - Re-record both baselines on the final tree, on the same host and CPU.
  - `platform/docs/DATA_INTEGRITY_AUDIT.md` D-65 gets a "Story 28.2" record:
    - before/after per fix and per venue for both bursts (direct, queued, flush, book_path);
    - the new baselines;
    - the 1× per-fix rule and the 2× test gate;
    - the projected cost of one collector at 30 instruments on one VPS vCPU (dev-box figure × 3, assumed, marked as such, because D-146's profile is absent);
    - the fix-4 outcome;
    - any reverted fix with its figure.
  - D-146 is noted as still open and not cited.
  - Spine `## Deferred` gains the epic's verbatim "Capture in Rust" entry.
  - `platform/CLAUDE.md` "Adding a venue" gains the line: a venue's expected messages/s per instrument is checked against the `scale` burst's per-message figure before deploying.
  - `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" gets a `### 28-2-capture-python-overhead-removed-baseline-lowered (commit: <closing sha or "this story's">)` entry with checkboxes for:
    - rebuilding and redeploying the collectors;
    - confirming `event loop: uvloop 0.22.1` in Dozzle for each collector;
    - comparing `capture:hotpath` `write_data_max_ms` before and after;
    - when D-146's profile lands, re-checking the dev-box ranking and replacing "× 3, assumed".

**Acceptance Criteria:**
- Given the final tree, when `python3 -m pytest -o addopts="" --rootdir=. tests/test_hotpath.py` runs from `platform/`, then both bursts pass against the committed, lowered baselines, and every allocation figure is ≤ its Commit 1 value.
- Given `git log` since the baseline revision, when read, then the commits are ordered harness+baselines → encoder → ingest → uvloop → (book) → closing, and each fix message carries both bursts' before/after figures.
- Given Commit 2 applied, when the scale burst's flush is measured (median of ≥ 5 runs), then the median `encode_ms` for 30 × 60 rows is recorded against the ≤ 20 ms target.
- Given uvloop importable, when a venue entrypoint starts, then the log shows `event loop: uvloop …` (or the named fallback).
- Given the closing commit, when the docs are read, then D-65 holds the 28.2 record, the spine Deferred section holds "Capture in Rust", "Adding a venue" holds the messages/s line, and the deferred operator entry exists under this story's key.

## Spec Change Log

## Review Triage Log

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 3, low 6)
- defer: 1: (high 0, medium 1, low 0)
- reject: 9: (high 0, medium 0, low 9)
- addressed_findings:
  - `[medium]` `[patch]` The `stop()`/`_INGEST_STOP` docstrings and the backlog test said stop drains the backlog, but `run()` cancels the ingest loop first → the docstrings now say "an ingest loop left running". A `Known limit:` on `stop()` names the ceiling and the upgrade path. The drain/ledger work itself is deferred as pre-existing.
  - `[medium]` `[patch]` The flush `encode_ms` gate at 2× a ~3 ms figure ran with gc enabled → gc is off during `_median_ms` passes, as it is for the per-message passes.
  - `[medium]` `[patch]` The spec ticked Commit 4 although uvloop was reverted → the task line and Auto Run Result record the revert and the unmet uvloop AC as a deviation governed by the binding allocation rule.
  - `[low]` `[patch]` `write_ms` timed each `ParquetArchiveWriter`'s construction → the writers are built before any timing.
  - `[low]` `[patch]` `measure()` overwrote `messages` per variant without a check → it now asserts both variants replayed the same burst.
  - `[low]` `[patch]` A failed live-path assertion leaked the queued loops → `_close` now runs in `finally`.
  - `[low]` `[patch]` `_feed` could spin until the 900 s timeout if the ingest task ended → a done-callback installed at setup stops the loop, so the run fails by name. A first version checked inside `_feed` and moved the traced queued peak by 8 bytes per burst, so it was moved out of the measured coroutine.
  - `[low]` `[patch]` A pre-28.2 flat baseline raised a bare `KeyError` → `_baseline` now fails with a re-record instruction.
  - `[low]` `[patch]` The encoder comment claimed a non-int is refused, but pyarrow truncates floats on both paths → the comment now states it and names `__init__` as the guard.

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3: (high 0, medium 0, low 3)
- defer: 0
- reject: 16: (high 0, medium 0, low 16)
- addressed_findings:
  - `[low]` `[patch]` `snapshots_to_record_batch` built the `RecordBatch` outside its `try`. More than 128 distinct instrument ids in one batch (the verifier's `serialize_batch` re-encode can mix instruments; capture's flush groups per instrument) widen pyarrow's dictionary index to int16. The schema check then raised a bare `ArrowInvalid`, which broke the docstring's "every refusal is `SnapshotEncodingError`". → `from_arrays` now runs inside the `try`, and a new test shows 128 ids encode, 129 are refused by name, and the dict path refuses the same batch.
  - `[low]` `[patch]` `test_a_stop_ends_an_idle_ingest_loop_at_once` asserted a < 0.1 s wall time, a flake risk on the loaded dev box (TEST-04). → The wall-clock assertion is removed. The `wait_for(..., 0.5)` against a loop parked ~0.99 s into the old poll still proves the stop wakes it.
  - `[low]` `[patch]` `test_a_stop_before_run_unwinds_it`'s docstring claimed "the ingest loop included", which its assertion cannot tell apart from cancellation. → The docstring now says what is asserted: it reaches its loops, unwinds and disconnects.

## Design Notes

**2× gate vs the 1× rule.** The committed test keeps `_WALL_TIME_FACTOR = 2.0`.
- A 1× wall-time assertion on a shared, intermittently loaded box fails on noise alone.
- The epic's "≤ baseline, not 2×" is enforced per fix instead, by the recorded median-of-≥5 comparison in each commit message. A fix whose target figure does not drop is reverted and recorded.
- Allocations stay exact (≤). D-65 states this split.

**Queued variant.** Per collector:
- Setup, outside the measurement:
  - a fresh loop from the (later helper) factory;
  - `task = loop.create_task(collector._ingest_loop())`;
  - one `run_until_complete(asyncio.sleep(0))`;
  - a warm burst through the same path.
- The measured coroutine calls `loop.call_soon_threadsafe(collector._on_data, m)` for every message, as the Rust clients do. It then awaits `asyncio.sleep(0)` until `qsize() == 0`: `get()` on a non-empty queue does not suspend, so empty means everything was processed. For a venue-timed collector it then calls `_drain_pending_deltas(_SECOND_CLOSE_NS)`.
- Teardown cancels the task and closes the loop, outside the measurement. This works on the pre- and post-Commit-3 code alike.
- `_assert_the_live_path_was_taken` applies unchanged.

**Flush timing.** The rows are deterministic `DydxSecondSnapshot`s: 20 levels a side, 1-second `ts_event`s, alternating set and `None` OHLC, spread across the venue's instruments.
- `encode_ms` is the median of `ArrowSerializer.serialize_batch(rows, DydxSecondSnapshot)`.
- `write_ms` is the median of `ParquetArchiveWriter(<fresh dir>).write(rows)`.
- Planning probe: a single-batch encode wrote byte-identical files (30 instruments, sha256-equal) and cut the write from 429 ms to 89 ms.

**Book-path share (scale burst, Bybit collector, 30 × 200-level live books).**
- `per_message_ns` is the median over the burst's update messages, pre-converted to capsules with `m.to_pyo3().as_pycapsule()`. It times `capsule_to_data(c)` plus `LiveBook.apply(d, now)`.
- `reads_per_second_ns` is one `snapshot_top(BOOK_DEPTH, …)` per live book, with the collector's own sampler parameters and a `now_ns` that reaches `Accepted`.
- The share is `(per_message_ns × 1500 + reads_per_second_ns) / 1e9 × 100`, with 1,500 = 30 instruments × Bybit's 50-level push every 20 ms.
- Planning probe: 0.67–0.71 % today, of which reads are 5.6 ms/s. The pyo3 alternative measured 0.22 %, but 1.3 µs/message, because the Rust clients hand over a `Data_t` capsule: `nautilus_pyo3.OrderBookDeltas.from_pycapsule` expects an `OrderBookDeltas_API` pointer, so no pyo3 decode of the wire capsule exists in this version, and a swap still needs `capsule_to_data` + `to_pyo3()`.
- Record that as the swap's `Known limit:` if it is taken.

**Loop logging.** The log line lives in `run_forever` because logging is configured there. A line logged before `asyncio.run` would be lost at INFO.

## Verification

**Commands** (from `platform/`):
- `python3 -m pytest -o addopts="" --rootdir=. tests/test_hotpath.py -q` -- expected: pass (both bursts).
- `for i in 1 2 3 4 5; do PYTHONHASHSEED=0 PYTHONPATH=. python3 tests/test_hotpath.py --measure scale; done` (and `default`) -- expected: identical allocations; ns medians used in the commit messages.
- `python3 -m pytest -o addopts="" --rootdir=. kernel/tests capture/tests archive/tests tests -q -p no:cacheprovider` -- expected: no new failures against the baseline revision; the pre-existing ones are listed in the result.
- `ruff check` / `ruff format --check` (0.15.16) and `mypy` (1.20.2) on the changed files, from a scratch venv -- expected: clean.

## Auto Run Result

**Summary.**
- **Scale burst added.** `tests/test_hotpath.py` gains the `scale` burst: 30 instruments per venue, 200-level books.
- **New measurements, both bursts.** Both bursts now also measure the queued path (`_on_data` → `_ingest_loop`) and the flush encode/write.
- **Fix-4 decision figure.** The scale burst also carries the book-path decision figure.
- **Baselines.** Both were recorded on the unchanged code first, then the fixes were applied in the epic's order.

| Fix | Result | Scale before → after | Default before → after | Notes |
|---|---|---|---|---|
| 1. Columnar flush encoder | Kept | encode 245.77 → **8.39 ms** (target ≤ 20 ms); write 303.85 → 48.55 ms | encode 79.03 → 3.42 ms | Parquet sha256-identical, proven by a test; allocations unchanged |
| 2. Plain ingest `get()` + stop sentinel | Kept | queued 11,359 → 7,339 ns/msg | queued 9,635 → 5,838 ns/msg | Queued allocations slightly lower |
| 3. uvloop | Measured, **reverted** | queued 7,698 → 5,698 ns/msg | — | Scale queued peak 141.4897 → 144.0196 B/msg (uvloop's larger per-callback handle) broke the binding allocation rule. The spec AC "log shows `event loop: uvloop`" is therefore not met: a recorded deviation, with the figures and upgrade path in D-65 |
| 4. pyo3 book swap | **Not taken** | book path 0.465 % of a dev-box core at 1,500 msg/s (runs 0.434–0.509 %) | — | Below the 1 % rule; the swap is named in D-65 as the next step. A swap would still need `capsule_to_data` + `to_pyo3()`, because no pyo3 decoder exists for the `Data_t` capsule |

Baselines were re-recorded lower on the final tree, on the same CPU and Python. The direct path's wall figure is noise-dominated; D-65 says so.

**Commits** (on `epic-28`, not pushed; all figures are medians of 5 runs, load average in each message)
- `160b168150` test(capture): scale burst, queued variant, flush and book-path figures, and both baselines on the unchanged code
- `d46d76204b` perf(kernel): columnar `DydxSecondSnapshot` batch encoder
- `78c9c297a1` perf(capture): ingest hand-off is a plain `get()` with a stop sentinel
- `cbb619fcea` perf(capture): re-record hot-path baselines lower, close Story 28.2
- the finalize commit: review patches, spec and epic context, spine Deferred entry

**Files changed**
- Hot-path harness:
  - `platform/tests/test_hotpath.py`: both bursts; direct/queued/flush/book-path figures.
  - `platform/tests/fixtures/hotpath_baseline*.json`: two baselines, nested layout.
  - `platform/Makefile`: `hotpath-baseline` records both.
- Encoder:
  - `platform/kernel/second_snapshot.py`: `snapshots_to_record_batch`, registered as `batch_encoder`.
  - `platform/kernel/tests/test_second_snapshot.py`: byte-identity, round-trip, refusal and empty tests.
  - `platform/capture/application/ports.py`: the `ArchiveWriter` docstring.
- Ingest hand-off:
  - `platform/capture/application/capture_service.py`: sentinel stop and plain `get()`; `_ingest_backlog`.
  - `platform/capture/tests/test_collector.py`, `test_poll_loop.py`, `capture/venues/dydx/tests/test_collector_resilience.py`: stop tests and adjustments for the removed 1 s poll.
- Docs:
  - `platform/docs/DATA_INTEGRITY_AUDIT.md` D-65: the Story 28.2 record.
  - `platform/docs/DEPLOY_CHECKLIST.md`: deferred entry `28-2-...`.
  - `platform/CLAUDE.md`: the "Adding a venue" messages/s line.
  - `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`: the "Capture in Rust" Deferred entry.
- Epic context: `_bmad-output/implementation-artifacts/epic-28-context.md`, recompiled from the current `epics.md` (operator note), not from `28-prior-attempt`.

**VPS profile.** 28.1's profile (audit D-146) does not exist yet, so it is not cited. The projection uses "× 3, assumed": about 9.0 % → 4.8 % of one VPS vCPU for one collector at 30 instruments. The deferred entry stays.

**Review.**
- 9 patches applied (3 medium, 6 low), 1 deferred (the shutdown backlog drain/ledger, which predates this story), 9 rejected.
- Rejected, as spec-mandated or already documented:
  - the 2× test gate against the epic's 1× rule;
  - the direct baseline's noise;
  - the Bybit 200-level × 1,500 msg/s pairing, which is the epic's own definition;
  - the spine entry wording, verbatim from the epic;
  - the documented sentinel off-by-one in `queue_depth_max`.

**Verification**
- `python3 -m pytest -o addopts="" --rootdir=. tests/test_hotpath.py kernel/tests capture/tests archive/tests tests -q` from `platform/`: 1,278 passed. `tests/test_hotpath.py`: 20 passed against the committed baselines after the review patches.
- The implementation agent's full run: 1,804 passed, 1 pre-existing failure (`tests/test_legacy_names.py::test_only_published_language_keeps_a_legacy_name`, which also fails at 2bab16a304).
- ruff 0.15.16 check and format: clean on every changed Python file. mypy 1.20.2: no new findings compared with the baseline revision.
- No container was started, stopped or restarted. The `verify` project was untouched.

**Residual risks**
- The box was contended during the measurements. The wall-time baselines will be re-recorded after the epics merge, per the operator note.
- The uvloop gain (~25 % of queued time) is left on the table by the exact peak-allocation rule. If the operator judges a ~2.5 B/msg transient peak acceptable for that gain, the reverted patch can be re-applied in its own change.
- The shutdown backlog is still dropped without a ledger entry: pre-existing, deferred, and documented as a `Known limit:`.

### Follow-up review (2026-09-30)

A fresh adversarial and edge-case review was run on the full diff since `2bab16a304`. It found 19 unique findings after deduplication.

- **3 low patches applied.** They are listed in the Review Triage Log:
  - the batch encoder now refuses more than 128 instrument ids as `SnapshotEncodingError`, with a test;
  - the flaky < 0.1 s wall-clock assertion is removed;
  - an overclaiming stop test docstring is corrected.
- **0 deferred.** The shutdown backlog drain (raised three ways: `run()` cancels ingest, callbacks after the sentinel, and a stop before `run()`) is the entry already deferred by the first pass. It is not duplicated.
- **16 rejected.** They fall into five groups:
  - Spec-mandated or already decided by the first pass: the 2× gate against the 1× rule; direct-baseline noise; the × 3 assumed projection; the uvloop harness shape; the `queue_depth_max` off-by-one; the test's use of the private `_ARROW_BATCH_ENCODERS` to force the dict path.
  - Hypothetical future callers: a direct `_stop.set()`, ingest returning without `_stop`, and a non-loop `stop()` caller. The old `Event.set()` was no more thread-safe.
  - `_book_price_column`'s fallback. It only re-runs the reference encoder.
  - A `None` level. It is already refused, only under a generic message.
  - Harness-internal nits: `_BYBIT` indexing, the `--measure` usage, the `_feed` ordering, the replay timeout, and the `None`-compression leg of the byte-identity test.

Files changed in this pass:
- `platform/kernel/second_snapshot.py`: `RecordBatch.from_arrays` moved inside the refusal `try`.
- `platform/kernel/tests/test_second_snapshot.py`: the 129-instrument refusal test.
- `platform/capture/tests/test_collector.py`: the wall-clock assertion is removed and a docstring corrected.

Verification (from `platform/`):
- `python3 -m pytest -o addopts="" --rootdir=. kernel/tests capture/tests archive/tests tests -q -p no:cacheprovider`: 1,598 passed, including the 20 `tests/test_hotpath.py` tests. 1 failed: the pre-existing `tests/test_legacy_names.py::test_only_published_language_keeps_a_legacy_name`, which also fails at `2bab16a304`.
- `ruff` 0.15.16 check and format: clean on the 3 changed files. `mypy` 1.20.2 on `kernel/second_snapshot.py`: no issues.

Follow-up review recommended: no. The three fixes are small, low-severity and local.
