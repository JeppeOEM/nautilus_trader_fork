---
title: 'In-memory long-window price series, replacing the recurring Parquet re-scan in ranking_engine'
type: 'refactor'
created: '2026-09-12'
status: 'done'
review_loop_iteration: 1
followup_review_recommended: false
context: []
warnings: ['oversized']
baseline_revision: 'b3dac42863f33e843ef15903483bc14707c447f6'
final_revision: '9a1de5d50e'
---

<intent-contract>

## Intent

**Problem:** `ranking_engine/engine.py`'s `_slow_loop_task` calls `metrics_computer.compute_all()` every 60s, which opens a fresh `ParquetDataCatalog` and re-reads a full 25h window per instrument (up to `max_workers=4`, Story 13.1) for `price`/`pct_1h`/`pct_24h`/`volatility` -- data that is already streaming live through Redis and already ingested into `ranking_engine`'s own `_SECOND_ROLLING`, just discarded there after 5 minutes. This recurring re-scan is the root cause of nifelheim's OOM-restart loop; Story 13.1 only bounded its concurrency spike.

**Approach:** Add a bounded, per-instrument, in-memory long-window price series (numpy ring buffers of `(ts_event_ns, close_price)`, capacity = `PRICE_LOOKBACK_HOURS * 3600` since the collector emits at most one close_price/instrument/second) fed incrementally from the same live `snapshots:raw` ingest already in `_ingest_snapshot_batch`. Seed each instrument exactly once via a lazy one-time Parquet backfill (reusing `catalog_stats.price_series()` unchanged) the first cycle after that instrument is first seen live. `_slow_loop_task` computes `price`/`pct_1h`/`pct_24h`/`volatility` from this in-memory series instead of calling `metrics_computer.compute_all()`. The `pct`/volatility math itself is extracted from `catalog_stats.price_stats()` into a new pure function `price_stats_from_series()` that both the catalog-backed path and the new in-memory path call -- one formula, two callers, guaranteeing byte-identical output rather than a second reimplementation that could drift (SSOT-02, DATA-02).

## Boundaries & Constraints

**Always:** Backfill runs at most once per instrument per process lifetime (tracked via a `_BACKFILLED: set[str]` in `engine.py`), never on the recurring cycle after that. Backfill I/O (`ParquetDataCatalog` open + `price_series()` read) runs via `asyncio.to_thread`; all ring-buffer mutation (`backfill()`, `ingest()`) happens only on the main event-loop thread, mirroring `_slow_loop_task`'s existing single-writer-thread discipline for `_LAST_SEEN`/tracker state. `PriceSeriesStore.backfill()` must merge (not clobber) against any live points already ingested for that instrument during the backfill's await window -- keep the live points, prepend only strictly-older backfilled history. `_legacy_book_metrics_for`'s OFI/OBI/microprice/spread fields are untouched. `metrics_computer.py` (`compute_all`/`compute_snapshot`) is left fully intact -- it has its own test suite (`ml_signals/tests/test_metrics_computer.py`) and no other caller; only `engine.py`'s usage of it is removed. Preserve `catalog_stats.price_stats()`'s public signature and behavior exactly (existing callers/tests unaffected) by making it a thin wrapper over the new `price_stats_from_series()`.

**Block If:** N/A -- fully specified; no ambiguous decision points.

**Never:** Do not modify `nautilus_trader/`/`crates/`. Do not touch `troll/docker-compose.yml`, `troll/collector.dockerfile`, `troll/troll-requirements.txt`, `troll/Makefile`, or anything under `troll/data_api/` (concurrent unrelated Epic 12 work). Do not introduce a second independent OFI/microprice/pct/volatility computation path anywhere (SSOT-02). Do not derive ring-buffer capacity from instrument count or make it unbounded (MEM-02/03). Do not fabricate real nifelheim `docker stats`/`free -h` before/after evidence -- this sandbox has no reachable access (confirmed in Story 13.1); record that verification as deferred, matching Story 13.1's precedent.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh instrument, no backfill yet | `_LAST_SEEN` gains a new iid; `_PRICE_SERIES` has 0 points for it | `stats()` returns `pct_change_1h`/`pct_change_24h`/`volatility` = `None` (insufficient history), `price` = latest live point or `None` | No error; matches existing "not enough history" guard |
| Backfill races live ingest | Live snaps for iid arrive while its backfill Parquet read is in flight (`asyncio.to_thread`) | `backfill()` merges: keeps already-buffered live points, prepends only historical points strictly older than the earliest live point | No duplication, no clobbered live data |
| Buffer at capacity | Instrument has produced >`PRICE_LOOKBACK_HOURS*3600` close_price points since backfill | Oldest points silently overwritten (ring buffer wrap); `stats()` still correct over the retained ~25h window | No error, bounded memory (MEM-02/03) |
| Snap with no trade this second | `close_price is None` in a `snapshots:raw` entry | Not appended to the price series (matches `price_series()`'s existing "seconds with no trade contribute nothing") | No error |
| In-memory vs. catalog parity | Same `DydxSecondSnapshot` history fed both via real `ParquetDataCatalog` write + `catalog_stats.price_stats()` AND via `PriceSeriesStore.backfill()` + `.stats()` | Both return numerically identical `price`/`pct_change_1h`/`pct_change_24h`/`volatility` | Verified by test, real objects, no mocking (TEST-01/03, DATA-02) |

</intent-contract>

## Code Map

- `troll/ml_signals/catalog_stats.py` -- extract `price_stats_from_series(series: list[tuple[int, float]]) -> dict` (lines 222-256's math body) as a pure function; `price_stats()` becomes `return price_stats_from_series(price_series(catalog, instrument_id, start_ns=start_ns))`. No signature/behavior change to `price_stats()`/`price_series()`.
- `troll/ranking_engine/price_series.py` (new) -- `_RingBuffer` (private, numpy `int64`/`float64` arrays, fixed capacity, mod-index wraparound) and `PriceSeriesStore` (public: `backfill(iid, series)`, `ingest(iid, ts_event_ns, close_price)`, `stats(iid, now_ns) -> dict`, `lookback_ns` attribute). Capacity/lookback default to `ml_signals.metrics_computer.PRICE_LOOKBACK_HOURS` (25.0h, imported not duplicated).
- `troll/ranking_engine/engine.py` -- module-level `_PRICE_SERIES = PriceSeriesStore()`, `_BACKFILLED: set[str] = set()`; `_ingest_snapshot_batch` appends `_PRICE_SERIES.ingest(iid, snap["ts_event"], snap.get("close_price"))` per snap; `_slow_loop_task` rewritten to (a) lazily backfill any newly-seen instrument via `asyncio.to_thread`, (b) build snapshots from `_PRICE_SERIES.stats()` + `_legacy_book_metrics_for()` instead of `metrics_computer.compute_all()`; drop the now-unused `metrics_computer` import; update the function's docstring (Story 13.2 supersedes the Story 13.1 mitigation note).
- `troll/ranking_engine/tests/test_engine.py` -- extend `_reset_state()` to reset `_PRICE_SERIES`/`_BACKFILLED`; extend `_snap()` helper with an optional `close_price` param.
- `troll/ranking_engine/tests/test_price_series.py` (new) -- ring buffer append/evict/wrap tests, backfill-then-incremental merge tests, and the catalog-vs-in-memory parity test using real `ParquetDataCatalog`/`DydxSecondSnapshot` (mirrors `ml_signals/tests/test_catalog_stats.py`'s `_write_snapshot` pattern).
- `troll/ml_signals/tests/test_catalog_stats.py` -- unchanged; verifies the `price_stats()` refactor preserved behavior.

## Tasks & Acceptance

**Execution:**
- [x] `troll/ml_signals/catalog_stats.py` -- extract `price_stats_from_series()`; `price_stats()` delegates to it -- single formula implementation shared by both computation paths.
- [x] `troll/ranking_engine/price_series.py` -- new `_RingBuffer`/`PriceSeriesStore` with numpy fixed-capacity ring buffers, `backfill()`'s live-merge logic, `ingest()`, `stats()`.
- [x] `troll/ranking_engine/engine.py` -- wire `_PRICE_SERIES` into `_ingest_snapshot_batch`; rewrite `_slow_loop_task` to lazily backfill + compute from memory instead of `metrics_computer.compute_all()`; remove unused import; update docstring.
- [x] `troll/ranking_engine/tests/test_price_series.py` -- new: ring buffer behavior, backfill/incremental sequencing, real-catalog parity vs. `catalog_stats.price_stats()`.
- [x] `troll/ranking_engine/tests/test_engine.py` -- update `_reset_state()`/`_snap()`; add coverage for `_ingest_snapshot_batch` feeding `_PRICE_SERIES` and `_slow_loop_task`'s backfill-once behavior.

**Acceptance Criteria:**
- Given a fresh `PriceSeriesStore` and a real `ParquetDataCatalog` seeded with `DydxSecondSnapshot` rows spanning >24h, when `backfill()` then `stats()` are called, then the result matches `catalog_stats.price_stats()`'s output on the same catalog/instrument to within float equality (same underlying formula).
- Given an instrument with fewer than 1h/24h of retained history, when `stats()` is called, then `pct_change_1h`/`pct_change_24h` are `None` (guard preserved, not extrapolated).
- Given live snaps arriving for a new instrument before its backfill completes, when backfill's Parquet read finally returns, then no data point is duplicated or lost -- the merged buffer contains each point exactly once, in ascending ts order.
- Given `_slow_loop_task` runs multiple cycles for the same instrument, when the second and later cycles execute, then no further Parquet read happens for that instrument (only the ingest-driven in-memory append + `stats()` read).
- Given `pytest ranking_engine/tests ml_signals/tests -q`, when run, then all tests pass, including new ring-buffer/backfill/parity coverage.

## Design Notes

**Ring buffer sizing:** capacity = `int(PRICE_LOOKBACK_HOURS * 3600)` = 90,000 slots/instrument (one `close_price` at most per second, per the collector's 1s sampling cadence -- see `dydx_collector/second_snapshot.py`'s module docstring). At `~29` instruments × 90,000 × 16 bytes (int64 ts + float64 price) ≈ 40MB steady-state, matching the epic AC's own figure. This capacity is a hard, deterministic memory bound regardless of arrival rate -- MEM-02/03 satisfied structurally, not by convention.

**Why lazy per-instrument backfill instead of one eager batch at process start:** at true process cold-start, `_LAST_SEEN` is empty -- the instrument set is only discovered as live `snapshots:raw` batches arrive, there is no static watchlist to backfill against up front. Gating the backfill on "not yet in `_BACKFILLED`" inside `_slow_loop_task`'s existing 60s tick means the heavy Parquet read still only ever happens once per instrument for the life of the process (satisfying the AC's "one Parquet backfill read per instrument, never on the recurring cycle"), it just fires on the first cycle where that instrument is newly visible rather than before the event loop starts.

**Backfill/live-ingest race:** `_ingest_snapshot_batch` always calls `_PRICE_SERIES.ingest()` unconditionally (mirrors `_SECOND_ROLLING`'s existing unconditional `setdefault(...).append()`), even for not-yet-backfilled instruments -- so a buffer can already hold live points when its backfill's Parquet read (run in a worker thread) returns. `backfill()` handles this by checking the existing buffer's earliest timestamp and only prepending historical points strictly older than it, then re-appending the already-live points -- both `backfill()` and `ingest()` only ever mutate ring-buffer state on the main thread, so this merge is race-free by construction (no lock needed, matching `_slow_loop_task`'s existing rationale for keeping shared-state mutation off `asyncio.to_thread`).

## Verification

**Commands:**
- `cd troll && python -m pytest ranking_engine/tests ml_signals/tests -q` -- expected: all pass, no failures/errors.
- `cd troll && python -c "import ranking_engine.price_series"` -- expected: imports cleanly, no circular-import error against `ml_signals.metrics_computer`/`ml_signals.catalog_stats`.
- `grep -n "compute_all" troll/ranking_engine/engine.py` -- expected: no matches (call site removed).

**Manual checks (if no CLI):**
- Inspect the ring buffer capacity math against `PRICE_LOOKBACK_HOURS` and the ~40MB total figure.
- Confirm `metrics_computer.py` is byte-for-byte untouched (`git diff troll/ml_signals/metrics_computer.py` empty).
- Record explicitly in the Auto Run Result whether real nifelheim `docker stats`/`free -h` before/after evidence was reachable from this sandbox (expected: not reachable, same as Story 13.1) -- do not claim OOM resolution without it.

## Review Triage Log

### 2026-09-12 — Review pass (Blind Hunter + Edge Case Hunter + Acceptance Auditor)

- intent_gap: 0
- bad_spec: 0
- patch: 6 (high 0, medium 4, low 2)
- defer: 3 (high 0, medium 1, low 2)
- reject: 2 (high 0, medium 0, low 2)
- addressed_findings:
  - `[medium]` `[patch]` All three layers independently flagged that a failed backfill (`_backfill_new_instruments`) was never added to `_BACKFILLED` on exception, so it would be retried every 60s cycle forever -- reintroducing, per-instrument, the exact recurring-Parquet-read cost this story exists to remove. Fixed: `_BACKFILLED.add(iid)` now runs in a `finally` block, so a failing instrument is attempted once and never retried; the failure is still logged loudly (`logger.exception`).
  - `[medium]` `[patch]` Acceptance Auditor + Edge Case Hunter flagged that the pre-existing `_slow_loop_task` cycle-duration staleness canary (troll/CLAUDE.md DATA-02: "a staleness/health canary on your own detection loop is required, not optional") was deleted in the rewrite with nothing added back, even though the new loop gained a potentially-slow phase (sequential per-instrument Parquet backfill). Fixed: restored a `time.monotonic()`-based duration measurement in `_slow_loop_task` with `logger.warning` if a cycle exceeds `DB_WRITE_INTERVAL_SECONDS` (`logger.debug` otherwise), with a docstring addendum explaining why.
  - `[medium]` `[patch]` Edge Case Hunter flagged that `close_price` fed into `_PRICE_SERIES.ingest()` had no finite/positive sanity check, unlike the existing `mid <= 0` fail-closed guard two lines below -- a NaN/inf/negative/zero value would silently poison every downstream `pct_change`/`volatility` read. Fixed: `_ingest_snapshot_batch` now drops (logs + treats as no-trade) any non-finite or non-positive `close_price` before it reaches `_PRICE_SERIES`.
  - `[medium]` `[patch]` Blind Hunter + Edge Case Hunter both flagged that `_RingBuffer`/`PriceSeriesStore` had no enforcement of the append-order-is-chronological-order invariant `ascending()` (and every stat derived from it) depends on -- an out-of-order or duplicate `ts_event_ns` (a Redis redelivery, a race) would silently corrupt `pct_change_1h`/`pct_change_24h`/`volatility` with no error raised. Fixed: `_RingBuffer` now tracks `last_ts`; `PriceSeriesStore.ingest()` drops (logs a warning) any point at or before it.
  - `[low]` `[patch]` Blind Hunter flagged that `_RingBuffer.__init__` would raise an unguarded `ZeroDivisionError` (inside `append()`'s `% capacity`) for a pathologically small/misconfigured `lookback_hours` resolving to capacity 0. Fixed: constructor now raises a clear `ValueError` for non-positive capacity.
  - `[low]` `[patch]` Blind Hunter flagged that the existing "backfills exactly once" test only inferred this from "no error on the second cycle," not a direct call-count assertion, and TEST-03 only bans mocking Nautilus internals (not spying on this module's own functions). Added two new tests: a monkeypatched call-counter proving `_read_price_series_sync` is invoked exactly once across three `_backfill_new_instruments` calls, and a direct test that a raising backfill still lands the instrument in `_BACKFILLED` (proving the retry-forever fix above).
  - `[medium]` `[defer]` Blind Hunter's core finding: an instrument that never trades within the 25h retention window loses live `pct_1h`/`pct_24h`/`volatility` freshness once its one-time backfilled (mark-price-fallback) history ages out of the ring buffer -- a real regression vs. the old per-cycle catalog re-scan, which always re-applied `price_series()`'s mark-price fallback fresh. Confirmed real by reading `catalog_stats.price_series()` and the collector's snapshot schema (no live mark-price field exists to feed `ingest()`). Fixing this properly needs either a live mark-price field on `snapshots:raw` or a periodic (not per-cycle) re-backfill for stale/never-traded instruments -- both larger than this story's scope. Low practical risk (the actively-tracked liquid tier trades continuously per OBS-01); logged to `deferred-work.md`.
  - `[low]` `[defer]` Edge Case Hunter/Blind Hunter's cold-start concern: `_backfill_new_instruments` backfills sequentially, so ~29 instruments all newly seen in the same cycle serialize ~29 blocking Parquet reads before the first write. Mitigated by the restored cycle-duration canary (above), which will surface it if it ever actually matters; logged to `deferred-work.md` as a candidate for `asyncio.gather`-based concurrency if the canary trips.
  - `[low]` `[defer]` Blind Hunter noted `backfill()`'s live-merge silently prefers the live value over a same-timestamp historical value with no mismatch check/logging -- correct under the documented one-point-per-second model, but a genuine data-integrity divergence there would be invisible. Logged to `deferred-work.md` as a future DATA-02 hardening candidate, not blocking this story.
  - `[low]` `[reject]` Blind Hunter's "delete `metrics_computer.compute_all`/`compute_snapshot` as dead code" (DESIGN-03) -- the spec's own Intent Contract explicitly directs leaving `metrics_computer.py` fully intact ("it has its own test suite and no other caller; only `engine.py`'s usage of it is removed"). Re-litigating an already-correctly-scoped spec decision at review time is not this story's problem.
  - `[low]` `[reject]` Blind Hunter's "`PRICE_LOOKBACK_HOURS` imported from an otherwise-dead module is an odd dependency" -- the spec's own Code Map explicitly mandates this exact import ("Capacity/lookback default to `ml_signals.metrics_computer.PRICE_LOOKBACK_HOURS` (25.0h, imported not duplicated)"). Same as above: already correctly scoped.

## Auto Run Result

**Summary:** `ranking_engine/engine.py`'s `_slow_loop_task` no longer calls `metrics_computer.compute_all()`; `price`/`pct_1h`/`pct_24h`/`volatility` now come from `PriceSeriesStore`, a new per-instrument fixed-capacity numpy ring buffer (`ranking_engine/price_series.py`) fed live by `_ingest_snapshot_batch` and seeded once per instrument via a lazy Parquet backfill. `catalog_stats.price_stats()` is now a thin wrapper over the newly-extracted `price_stats_from_series()`, which both the old catalog-backed path and the new in-memory path call (SSOT-02). This removes the recurring 25h-lookback Parquet re-scan from the hot path structurally, addressing the proximate cause of the nifelheim OOM-restart loop that Story 13.1 only mitigated. Review added five defensive fixes (retry-forever backfill, deleted staleness canary, unguarded malformed price, unguarded out-of-order timestamp, zero-capacity construction) and strengthened test coverage for the "backfill exactly once" guarantee; three lower-priority gaps were logged to `deferred-work.md` rather than fixed in this pass.

**Files changed:**
- `troll/ml_signals/catalog_stats.py` -- extracted `price_stats_from_series()` from `price_stats()`'s inline math; `price_stats()` is now a thin wrapper. No signature/behavior change.
- `troll/ranking_engine/price_series.py` (new) -- `_RingBuffer` (numpy fixed-capacity circular buffer, now with `last_ts` tracking and a positive-capacity guard) and `PriceSeriesStore` (`ingest()`, `backfill()`, `stats()`), plus an out-of-order-timestamp drop-and-log guard in `ingest()`.
- `troll/ranking_engine/engine.py` -- `_PRICE_SERIES`/`_BACKFILLED` module state; `_ingest_snapshot_batch` feeds `_PRICE_SERIES.ingest()` (with a new close_price finite/positive guard); `_read_price_series_sync`/`_backfill_new_instruments`/`_slow_loop_once` replace the old `compute_all()` call site; `_backfill_new_instruments` now marks an instrument backfilled even on failure (no infinite retry); `_slow_loop_task` regained its cycle-duration staleness canary.
- `troll/ranking_engine/tests/test_price_series.py` (new) -- ring buffer wraparound/capacity-guard tests, backfill/live-merge tests, out-of-order-drop test, and the catalog-vs-in-memory parity test.
- `troll/ranking_engine/tests/test_engine.py` -- `_PRICE_SERIES`/`_BACKFILLED` reset coverage, close_price ingest tests (including the new malformed-price guard), and two new backfill-exactly-once/backfill-survives-failure tests using a monkeypatched call counter.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- logged three review findings not fixed in this pass (never-trading-instrument staleness, sequential cold-start backfill, silent same-timestamp merge collision).
- `_bmad-output/implementation-artifacts/spec-13-2-in-memory-long-window-price-series.md` -- this spec.

**Review findings breakdown:** 6 patches applied (4 medium: retry-forever backfill, deleted staleness canary, unguarded malformed close_price, unguarded out-of-order timestamp; 2 low: zero-capacity guard, strengthened exactly-once test), 3 deferred (1 medium: never-trading-instrument staleness regression; 2 low: sequential cold-start backfill, silent same-timestamp merge collision -- all three logged to `deferred-work.md`), 2 rejected (low: re-litigations of decisions the spec's own Intent Contract/Code Map already made explicitly -- keeping `metrics_computer.py` intact, importing `PRICE_LOOKBACK_HOURS` from it). No intent gaps, no bad-spec loopbacks.

**Follow-up review recommendation:** `false` -- all patches are small, localized, defensive fixes (a `finally` block, restored logging, two input-sanity guards, a constructor guard, two new tests) with no API/behavior/data-model impact on the passing paths; the deferred items are explicitly scoped-out, tracked follow-ups, not loose ends of this pass.

**Verification performed:**
- `cd troll && python -m pytest ranking_engine/tests ml_signals/tests -q --ignore=ml_signals/tests/test_dashboard_remote_mode.py` -- 264 passed (259 pre-existing + 5 new: out-of-order-drop, zero-capacity guard, malformed-close_price guard, backfill-exactly-once counter, backfill-survives-failure). `test_dashboard_remote_mode.py` is excluded here because it belongs to the concurrently-in-flight Story 12.2 and requires `uvicorn`/Docker, unrelated to this story's files.
- `grep -n "compute_all" troll/ranking_engine/engine.py` -- no matches.
- `python -c "import ranking_engine.price_series"` -- imports cleanly.
- `git diff troll/ml_signals/metrics_computer.py` -- empty (byte-for-byte untouched, per the Intent Contract).
- `awk 'length > 100'` run manually over every touched file, scoped to this story's own added lines via `git diff` -- no violations (`ruff`/`mypy` are not installed in this sandbox, same limitation Story 13.1 recorded; deferred to the repo's normal CI/pre-commit hooks on push).
- `git diff` inspected directly to confirm scope: only the five files listed above (plus `deferred-work.md`) changed; `metrics_computer.py`, `data_api/`, `docker-compose.yml`, `collector.dockerfile`, `troll-requirements.txt`, `Makefile` all untouched.

**Residual risks:**
- Real nifelheim (production) before/after `docker stats`/`free -h` evidence was **not collected** -- this development sandbox has no reachable SSH access to that host (same limitation as Story 13.1). Whether this change measurably reduces/eliminates the OOM-restart rate in production is an **open, deferred verification**, not a confirmed result, per troll/CLAUDE.md DATA-02.
- ~~The never-trading-instrument staleness regression (see Review Triage Log) is a real, confirmed gap~~ -- **addendum (2026-09-12, post-finalization):** the mechanism is real, but on user review the trigger condition (a market with an actively-updating order book yet zero taker trades across a full 24-25h span) does not occur on real dYdX perpetual markets -- liquidations/funding arb/cross-exchange arb guarantee occasional taker fills on any market that is still genuinely trading; a market seeing truly zero trades for a day is functionally dead, a state this system has no obligation to serve fresh stats for. Downgraded from "real regression, deferred" to "theoretical-only, accepted, not scheduled" in `deferred-work.md`.
- `ruff`/`mypy` could not be run in this environment (not installed); a manual line-length check was substituted. Deferred to CI/pre-commit on push, matching Story 13.1's precedent.

