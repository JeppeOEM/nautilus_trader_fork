---
status: resolved
trigger: |
  Periodic catalog conversion raises "non-disjoint intervals" ValueError for
  IndexPriceUpdate and FundingRateUpdate (and possibly other types) on the
  second/subsequent call to _convert_stream during a long-running recorder
  session. Root cause appears to be: StreamingFeatherWriter defaults to
  RotationMode.NO_ROTATION, so the feather file under
  catalog/streaming/live/{instance_id}/{type}/ keeps growing across conversion
  cycles; catalog.convert_stream_to_data() re-reads the whole feather file each
  time, so the second conversion's (start, end) interval has the same start as
  the first conversion's already-written parquet file but a later end ->
  non-disjoint, raises ValueError. This is in
  scripts/bybit_recorder/strategy.py _convert_stream (called periodically via
  clock.set_timer, REL-01) and
  nautilus_trader/persistence/catalog/parquet.py
  _convert_feather_table_to_parquet. User reproduced it with a live mainnet run
  (conversion_interval_minutes=1) and pasted the full traceback. Investigate
  and fix so periodic mid-day conversion does not raise, without losing data
  (this overlaps with Phase 3 REL-02's same-day-restart requirement, so the fix
  should be compatible with that goal).
created: 2026-06-14
updated: 2026-06-14
---

# Debug Session: non-disjoint-intervals

## Symptoms

- **Expected behavior**: Periodic `_convert_stream` conversion (via `clock.set_timer`,
  interval configured by `recorder.toml`'s `conversion_interval_minutes`) succeeds
  repeatedly over the course of a long-running session, with each cycle converting
  newly-written feather data into parquet without data loss or errors.
- **Actual behavior**: On the second (and subsequent) periodic conversion call,
  `catalog.convert_stream_to_data()` raises `ValueError: ... would create non-disjoint
  intervals` for `IndexPriceUpdate` and `FundingRateUpdate` (possibly other types too).
- **Error messages** (from live run, `conversion_interval_minutes = 1`):
  ```
  ValueError: Writing file 2026-06-14T10-47-00-992247449Z_2026-06-14T11-26-50-206147636Z.parquet
  with interval (1781434020992247449, 1781436410206147636) would create non-disjoint
  intervals. Existing intervals: [(1781434020992247449, 1781434080402873706)]
  Failed to convert IndexPriceUpdate stream to catalog
    File ".../scripts/bybit_recorder/strategy.py", line 300, in _convert_stream
      catalog.convert_stream_to_data(...)
    File ".../nautilus_trader/persistence/catalog/parquet.py", line 2567, in convert_stream_to_data
      self._convert_feather_table_to_parquet(...)
    File ".../nautilus_trader/persistence/catalog/parquet.py", line 2609, in _convert_feather_table_to_parquet
      raise ValueError(...)

  ValueError: Writing file 2026-06-14T10-47-00-992247449Z_2026-06-14T11-26-47-807941266Z.parquet
  with interval (1781434020992247449, 1781436407807941266) would create non-disjoint
  intervals. Existing intervals: [(1781434020992247449, 1781434080703694783)]
  Failed to convert FundingRateUpdate stream to catalog
    (same traceback shape, strategy.py line 300 -> parquet.py convert_stream_to_data
     -> _convert_feather_table_to_parquet -> raise ValueError)
  ```
- **Timeline**: First observed now, during a live Bybit mainnet test run with
  `conversion_interval_minutes = 1` (temporarily lowered for testing). Phase 2's live
  smoke test (~100s) only triggered the conversion timer once, so this multi-cycle
  scenario was never exercised before.
- **Reproduction**: Run `python -m scripts.bybit_recorder.recorder` against Bybit
  mainnet with `conversion_interval_minutes` set low (e.g. 1) and let it run past two
  conversion cycles. The `_convert_stream` timer (`scripts/bybit_recorder/strategy.py`)
  fires `catalog.convert_stream_to_data(...)` per data type each cycle.

## Hypothesis (initial)

`StreamingFeatherWriter` defaults to `RotationMode.NO_ROTATION` (see
`nautilus_trader/persistence/writer.py`), so the feather file under
`catalog/streaming/live/{instance_id}/{type}/...` is never rotated/truncated between
conversion cycles — it keeps accumulating all rows since the writer was created.
`ParquetDataCatalog.convert_stream_to_data()` (`nautilus_trader/persistence/catalog/parquet.py`,
`_convert_feather_table_to_parquet`) reads the *entire* feather file each call and
computes `(start, end) = (min(ts_init), max(ts_init))` over all rows in it. On cycle 1
this produces interval `(t0, t1)` and writes `t0_t1.parquet`. On cycle 2, the feather
file now contains rows from `t0` through `t2` (t2 > t1), so the computed interval is
`(t0, t2)`, which overlaps the already-written `(t0, t1)` parquet file ->
`_are_intervals_disjoint` fails -> `ValueError`.

This affects every `data_cls` converted via `_convert_stream`'s per-type loop
(`[*_RECORDED_TYPES, FundingRateUpdate]`), not just `IndexPriceUpdate` /
`FundingRateUpdate` — those two may just be the ones the user happened to see in the
pasted (possibly truncated) output, or other types may have been suppressed by the
`if used_catalog.fs.exists(parquet_file): ... return` early-exit if their filename
happened to collide (unlikely) or by different per-type data volumes changing which
errors surfaced first in the log.

## Evidence

- timestamp: 2026-06-14
  checked: Wrote and ran
  `tests/unit_tests/persistence/recorder/test_repro_nondisjoint.py` (temporary):
  write 3 TradeTicks via StreamingFeatherWriter, call
  `catalog.convert_stream_to_data(...)` (cycle 1, writes
  `1e9_3e9.parquet`), then append 3 more TradeTicks with later ts_init
  WITHOUT closing/rotating the writer, call `convert_stream_to_data(...)` again
  (cycle 2).
  found: Cycle 2 raises
  `ValueError: Writing file 1970-01-01T00-00-01-000000000Z_1970-01-01T00-00-12-000000000Z.parquet
  with interval (1000000000, 12000000000) would create non-disjoint intervals.
  Existing intervals: [(1000000000, 3000000000)]` — identical shape to the live
  traceback in Symptoms.errors (same `start`, larger `end`, overlapping existing
  interval).
  implication: Confirms the hypothesis mechanism exactly.
  `_convert_feather_table_to_parquet` re-reads the whole feather file each cycle
  and computes (start,end)=(min,max ts_init) with no awareness of
  already-converted intervals. Root cause confirmed.

## Current Focus

- reasoning_checkpoint:
    hypothesis: "_convert_feather_table_to_parquet always computes (start,end) =
      (min(ts_init), max(ts_init)) over the ENTIRE feather table re-read each
      cycle (because the kernel/strategy writers use NO_ROTATION /
      SCHEDULED_DATES daily rotation, not per-cycle). When new rows are appended
      between cycle N and cycle N+1 without the file being rotated, cycle N+1's
      interval (start, end_new) starts at the same `start` already covered by
      cycle N's written parquet file (start, end_old), so
      _are_intervals_disjoint([(start,end_old), (start,end_new)]) is False ->
      ValueError, for every data_cls/identifier that received new rows."
    confirming_evidence:
      - "Reproduced exactly in tests/unit_tests/persistence/recorder/test_repro_nondisjoint.py:
        write 3 TradeTicks (ts_init 1e9..3e9), convert (writes 1e9_3e9.parquet),
        append 3 more TradeTicks (ts_init 10e9..12e9) without closing/rotating the
        writer, convert again -> raises 'ValueError: ... interval (1000000000,
        12000000000) would create non-disjoint intervals. Existing intervals:
        [(1000000000, 3000000000)]' — the exact same shape as the live traceback
        in Symptoms.errors (same start, larger end, overlapping existing interval)."
      - "_convert_feather_table_to_parquet (parquet.py ~2593-2612) computes start/end
        via pa.compute.min/max over the WHOLE re-read feather table with no
        filtering against already-converted intervals, and convert_stream_to_data
        has no parameter to bound the read by ts_init."
    falsification_test: "If the fix filters the feather table to only rows with
      ts_init > max(existing interval end) before computing start/end, the second
      convert_stream_to_data call in the repro test must succeed (no raise) and
      catalog.trade_ticks(...) must return all 6 rows (3 from cycle 1 parquet + 3
      from cycle 2 parquet)."
    fix_rationale: "Filtering the re-read feather table to rows strictly newer
      than the latest already-converted interval addresses the root cause
      directly: it makes each conversion cycle only persist the genuinely NEW
      tail of data, restoring the disjoint-intervals invariant without requiring
      per-cycle feather rotation/truncation (which would conflict with REL-02's
      same-day-restart design and the daily SCHEDULED_DATES rotation already in
      place). This is a minimal, targeted change to
      _convert_feather_table_to_parquet in nautilus_trader/persistence/catalog/parquet.py."
    blind_spots: "Per-instrument / per-bar-type feather files (Bar, QuoteTick,
      OrderBookDeltas, etc.) use _identifier_from_table_or_path to pick the
      directory — need to verify the new ts_init filter is applied using the
      SAME directory/identifier's existing intervals (not a global filter), so
      multi-identifier feather files aren't incorrectly trimmed against another
      identifier's interval. Also must handle the case where the entire table is
      <= the existing max end (filtered table becomes empty) -> skip silently
      (matches existing idempotent-skip behavior documented in
      test_double_conversion_same_day_behavior)."
- next_action: COMPLETE - fix applied and verified, see Resolution below.

## Resolution

root_cause: |
  `_convert_feather_table_to_parquet` (nautilus_trader/persistence/catalog/parquet.py)
  computes `(start, end) = (min(ts_init), max(ts_init))` over the ENTIRE feather
  table re-read on every conversion cycle. Because the writers use daily
  (SCHEDULED_DATES) or no rotation, the feather file is not truncated between
  conversion cycles, so cycle N+1 re-reads cycle N's rows plus any newly appended
  rows. The recomputed interval (start, end_new) has the same `start` as the
  already-written parquet file's interval (start, end_old), and
  `_are_intervals_disjoint` correctly detects the overlap and raises
  `ValueError: ... would create non-disjoint intervals`.

fix: |
  REVISED AGAIN (2026-06-14): the previous revision introduced a
  `RecorderParquetDataCatalog(ParquetDataCatalog)` subclass overriding
  `_convert_feather_table_to_parquet` with trimming logic, to avoid touching core
  while still re-converting the still-growing active feather file each cycle. On
  further review (user: "the code works perfectly as IS, no need to change
  anything except just use it the right way"), that override is unnecessary: the
  REAL fix is to never re-convert a feather file that can still receive new rows
  in the first place.

  `scripts/bybit_recorder/strategy.py`'s `_convert_stream` now calls a new
  recorder-only method, `_convert_finalized_feather_files`, which:
    - Lists feather files for a `data_cls` via the existing (unmodified)
      `catalog._list_feather_data_files(...)`.
    - Groups them by directory (one group per instrument/bar-type identifier).
    - Within each group, skips the LAST (most-recently-created, still-active)
      file and converts every earlier file via the existing, unmodified
      `catalog._read_feather_file(...)` + `catalog._convert_feather_table_to_parquet(...)`.

  A file is only "earlier" (finalized) once `StreamingFeatherWriter` has rotated
  to a new file for that identifier, so a finalized file's row set -- and
  therefore its `(start, end)` interval -- never changes again. Conversion of
  each file happens exactly once and repeat cycles are naturally idempotent (the
  file is simply absent from `files[:-1]` once it's no longer last, but by then
  it's already converted; if a cycle is missed, it just converts on a later
  cycle, still as an `files[:-1]` entry, with the same unchanged content).

  To make this rotation-based finalization actually happen on a useful cadence
  (and to make it testable without waiting a full day), `rotation_interval_minutes`
  (default 1440 = 1 day, REL-02) is now a config knob threaded through to BOTH
  feather writers that matter for `_convert_stream`:
    - the kernel "*" writer, via `StreamingConfig` in
      `scripts/bybit_recorder/config.py: build_streaming_config`
      (`rotation_mode=SCHEDULED_DATES`, `rotation_interval=Timedelta(minutes=...)`,
      `rotation_time=00:00 UTC`)
    - the strategy-owned funding writer, via
      `RecorderStrategyConfig.rotation_interval_minutes` in
      `scripts/bybit_recorder/strategy.py: _persist_funding_rate` (same
      SCHEDULED_DATES/00:00 UTC settings)

  Both writers must rotate on the same schedule so `_convert_finalized_feather_files`
  sees a consistent "finalized" cutoff across all converted types each cycle.

  `nautilus_trader/persistence/catalog/parquet.py` remains completely unmodified
  (core untouched) -- no override, no subclass, no core edits at all. The
  previously-introduced `scripts/bybit_recorder/catalog.py`
  (`RecorderParquetDataCatalog`) and its test
  (`tests/unit_tests/persistence/recorder/test_recorder_catalog.py`) have been
  deleted as no longer needed.

verification: |
  1. Reproduced the original failure (prior revision): write 3 TradeTicks,
     convert (cycle 1, writes 1e9_3e9.parquet), append 3 more TradeTicks with
     later ts_init without rotating the writer, convert again (cycle 2) -> raised
     `ValueError: ... interval (1000000000, 12000000000) would create non-disjoint
     intervals. Existing intervals: [(1000000000, 3000000000)]`.
  2. Implemented `_convert_finalized_feather_files` in `strategy.py` (no override,
     no subclass) and removed `scripts/bybit_recorder/catalog.py` +
     `RecorderParquetDataCatalog`.
  3. Made rotation configurable end-to-end via `rotation_interval_minutes`
     (`recorder.toml` -> `RecorderConfig` -> `build_streaming_config` for the
     kernel writer, and `RecorderStrategyConfig` -> `_persist_funding_rate` for
     the funding writer).
  4. Added permanent regression test
     `tests/unit_tests/persistence/recorder/test_recorder_rotation_conversion.py`
     (`test_convert_finalized_feather_files_skips_active_file`): writes ticks
     across multiple 60s `RotationMode.INTERVAL` rotations, runs
     `_convert_finalized_feather_files` twice across cycles, asserts the active
     (most-recently-created) file is always skipped, finalized files convert
     exactly once, no data loss, and no non-disjoint-interval raise -> PASSED.
  5. tests/unit_tests/persistence/recorder/ -> 31 passed.
  6. ruff check on `config.py`, `strategy.py`, `recorder.py`, and the new test
     file -> all checks passed.
  7. nautilus_trader/persistence/catalog/parquet.py is unmodified (core
     untouched), and no subclass/override of any core class exists either.
  8. `rotation_interval_minutes` defaults to 1440 (1 day, REL-02 daily
     partitioning) but is fully testable at short intervals -- the regression
     test exercises a 60s rotation interval, and `recorder.toml` can set
     `rotation_interval_minutes = 1` for a live mainnet smoke test without
     waiting a full day.

files_changed:
  - scripts/bybit_recorder/strategy.py
  - scripts/bybit_recorder/config.py
  - scripts/bybit_recorder/recorder.py
  - scripts/bybit_recorder/recorder.toml
  - tests/unit_tests/persistence/recorder/test_recorder_rotation_conversion.py (new)
  - scripts/bybit_recorder/catalog.py (deleted, was added in prior revision)
  - tests/unit_tests/persistence/recorder/test_recorder_catalog.py (deleted, was added in prior revision)
