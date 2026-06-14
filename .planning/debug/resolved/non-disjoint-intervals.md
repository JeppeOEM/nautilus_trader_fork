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
  In `_convert_feather_table_to_parquet`, after resolving `identifier`/`directory`,
  fetch `current_intervals = used_catalog._get_directory_intervals(directory)`
  BEFORE computing `(start, end)`. If `current_intervals` is non-empty and the
  re-read table's minimum `ts_init` is `<= max(end for _, end in
  current_intervals)`, filter the table down to rows with
  `ts_init > max_converted_end` via `table.filter(pc.greater(table["ts_init"],
  max_converted_end))`. If the filtered table becomes empty (everything already
  converted), return early (idempotent no-op, matches existing
  test_double_conversion_same_day_behavior). Otherwise recompute `(start, end)`
  from the trimmed table and proceed as before. This makes each conversion cycle
  persist only the genuinely new tail of data, restoring the disjoint-intervals
  invariant without requiring per-cycle feather rotation (preserves REL-02
  same-day-restart / daily SCHEDULED_DATES rotation design).

verification: |
  1. Reproduced the exact failure first (temporary test): write 3 TradeTicks,
     convert (cycle 1, writes 1e9_3e9.parquet), append 3 more TradeTicks with
     later ts_init without rotating the writer, convert again (cycle 2) -> raised
     `ValueError: ... interval (1000000000, 12000000000) would create non-disjoint
     intervals. Existing intervals: [(1000000000, 3000000000)]` (matches reported
     traceback shape).
  2. Applied the fix to nautilus_trader/persistence/catalog/parquet.py.
  3. Re-ran the same reproduction -> PASSED (cycle 2 no longer raises;
     catalog.trade_ticks(...) returns all 6 rows across two disjoint parquet
     files).
  4. Added permanent regression test
     `test_repeated_conversion_with_new_appended_rows_does_not_raise` to
     tests/unit_tests/persistence/recorder/test_recorder_conversion.py (appends
     new rows to an open feather file between two convert_stream_to_data calls,
     asserts no raise and correct cumulative row count).
  5. Full regression suites pass:
     - tests/unit_tests/persistence/recorder/ -> 32 passed
     - tests/unit_tests/persistence/test_catalog.py -> 96 passed, 1 skipped
       (development_only, pre-existing skip)
     - ruff check on modified file -> all checks passed
  6. tests/unit_tests/persistence/test_streaming.py collection error
     (ModuleNotFoundError: aiohttp) confirmed PRE-EXISTING and unrelated (same
     error on `git stash` of this change).

files_changed:
  - nautilus_trader/persistence/catalog/parquet.py
  - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
