---
phase: 03-reliability-for-24-7-operation
reviewed: 2026-06-15T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - scripts/bybit_recorder/config.py
  - scripts/bybit_recorder/recorder.py
  - scripts/bybit_recorder/strategy.py
  - scripts/bybit_recorder/recorder.toml
  - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
  - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
findings:
  critical: 0
  warning: 2
  info: 2
  total: 4
status: issues_found
---

# Phase 03: Code Review Report

**Reviewed:** 2026-06-15T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

This is a fresh review of the current code state for the reliability-hardening phase
(REL-01..REL-04). The previously-flagged CR-01 (unguarded `ParquetDataCatalog`
construction + funding writer flush in `_run_conversion` during `on_stop`) and the
missing `restart_gap_threshold_seconds > 0` validation have both been fixed correctly
in `scripts/bybit_recorder/strategy.py::_run_conversion` and
`scripts/bybit_recorder/config.py::load_recorder_config` respectively, and are
covered by `test_on_stop_swallows_catalog_construction_error`,
`test_on_stop_swallows_funding_writer_flush_error`, and
`test_load_recorder_config_rejects_nonpositive_restart_gap_threshold`.

No new critical/blocker issues were found. Two warnings remain around unbounded
memory use in `_log_restart_gaps` and an inconsistency between
`_log_restart_gaps`'s and `_run_conversion`'s catalog-construction error handling.
Two info-level items concern the strategy-owned funding writer never being closed
and a per-write `flush()` call that defeats the writer's built-in buffering.

## Warnings

### WR-01: `_log_restart_gaps` loads the entire trade-tick history into memory per instrument

**File:** `scripts/bybit_recorder/strategy.py:230-249`
**Issue:** On every `on_start()`, for each configured instrument,
`catalog.trade_ticks(instrument_ids=[str(instrument_id)])` reads **all** trade ticks
ever recorded for that instrument into a Python list just to compute
`max(tick.ts_init for tick in trades)`. For a 24/7 recorder running for weeks/months,
this list can grow to many millions of rows, and every restart will attempt to load
the full trade history for every instrument into memory before subscriptions are
even set up. In a low-memory environment (e.g. a small systemd VPS — the deployment
target per `CLAUDE.md`), this risks a memory exhaustion during `on_start`, which
(per `_log_restart_gaps`'s own try/except) would be caught and logged for an internal
exception — but a process-level OOM kill from the OS would not be caught at all, and
would prevent startup entirely, defeating REL-02's gap-visibility goal and blocking
subscriptions.

**Fix:** Use a catalog query bounded to a recent time window first, only falling back
to a full scan if the instrument has no recent data (e.g. first run after a long
outage):
```python
# Query a recent window first; only fall back to a full scan if the
# instrument has no recent data (e.g. first run after a long outage).
recent_start = pd.Timestamp(now_ns - int(7 * 24 * 3600 * 1e9), unit="ns")
trades = catalog.trade_ticks(
    instrument_ids=[str(instrument_id)],
    start=recent_start,
)
if not trades:
    trades = catalog.trade_ticks(instrument_ids=[str(instrument_id)])
if not trades:
    continue
last_ts_init = max(tick.ts_init for tick in trades)
```

### WR-02: `_log_restart_gaps`'s top-level `ParquetDataCatalog` construction is unguarded, inconsistent with `_run_conversion`

**File:** `scripts/bybit_recorder/strategy.py:227`
**Issue:** `_run_conversion` (lines 452-460) explicitly wraps
`ParquetDataCatalog(self.config.catalog_path)` construction in a try/except with a
comment explaining that a transient I/O error here must not propagate and fault the
component. `_log_restart_gaps` constructs the catalog the same way at line 227, but
with no such guard — only the per-instrument body (lines 231-249) is wrapped. If
`ParquetDataCatalog.__init__` (or any fsspec/filesystem call it triggers, e.g. for
non-`file` protocols) ever raises, this propagates out of `_log_restart_gaps()` and
then out of `on_start()` entirely, aborting before any subscriptions are made — the
opposite of the "must not prevent subscriptions" guarantee documented in
`_log_restart_gaps`'s own docstring (lines 173-175, 223-225).

**Fix:** Move the catalog construction inside a try/except, consistent with
`_run_conversion`:
```python
def _log_restart_gaps(self) -> None:
    try:
        catalog = ParquetDataCatalog(self.config.catalog_path)
    except Exception:
        logger.exception("Failed to open catalog for restart-gap check")
        return

    now_ns = self.clock.timestamp_ns()
    for instrument_id in self.config.instrument_ids:
        ...
```

## Info

### IN-01: Strategy-owned funding writer is never `close()`d

**File:** `scripts/bybit_recorder/strategy.py:430-485, 494-517`
**Issue:** `_run_conversion` (invoked from both the periodic timer and `on_stop`)
calls `self._funding_writer.flush()` but never `self._funding_writer.close()`. The
kernel's `"*"` writer is closed by `NautilusKernel._close_writer()` after `on_stop`
runs, but the strategy-owned funding writer (created lazily in
`_persist_funding_rate`) has no equivalent close path — its underlying
`RecordBatchStreamWriter`/file handles (see `StreamingFeatherWriter.close()` in
`nautilus_trader/persistence/writer.py:586-601`) remain open until process exit.
In practice the OS reclaims these on process termination, so this is not a
data-loss risk given `flush()` is called in `on_stop`, but it is an inconsistency
with how the kernel manages its own writer and could mask resource-cleanup bugs in
future refactors.

**Fix:** In `on_stop`, after the final `_run_conversion()` call, close the funding
writer if it exists:
```python
def on_stop(self) -> None:
    self._run_conversion()
    if self._funding_writer is not None:
        try:
            self._funding_writer.close()
        except Exception:
            logger.exception("Failed to close funding writer")
```

### IN-02: `_persist_funding_rate` flushes on every write, defeating the writer's buffering

**File:** `scripts/bybit_recorder/strategy.py:427-428`
**Issue:** `self._funding_writer.write(funding_rate)` is immediately followed by
`self._funding_writer.flush()`. `StreamingFeatherWriter` has its own
`flush_interval_ms`-based buffering (defaulting to 1000ms, see
`nautilus_trader/persistence/writer.py:157`), which this bypasses entirely —
every deduped funding-rate change (one per instrument per ~8h funding interval, so
low frequency) forces an immediate disk flush. Given the low write frequency this is
not a performance problem in practice, but it is an unnecessary deviation from the
writer's intended buffered-write contract and means the explicit per-write flush call
serves no purpose beyond what the writer would do on its own `flush_interval_ms`
timer or the periodic `_run_conversion` flush.

**Fix:** Drop the explicit per-write `flush()` and rely on the writer's own interval
flush plus the existing `_run_conversion` flush before conversion:
```python
self._funding_writer.write(funding_rate)
```

---

_Reviewed: 2026-06-15T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
