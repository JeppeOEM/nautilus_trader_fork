---
phase: 03-reliability-for-24-7-operation
reviewed: 2026-06-14T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - scripts/bybit_recorder/config.py
  - scripts/bybit_recorder/recorder.py
  - scripts/bybit_recorder/recorder.toml
  - scripts/bybit_recorder/strategy.py
  - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
  - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
findings:
  critical: 1
  warning: 3
  info: 2
  total: 6
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-06-14T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 3 adds an `on_stop()` graceful-shutdown flush+convert (REL-02), an
on-start restart-gap WARNING (D-06), and a per-stream last-seen/heartbeat
stale-stream detector (REL-03), with associated config plumbing and
fail-fast validation.

The overall design is sound and well-documented (each non-obvious decision
has a WHY comment referencing the relevant requirement). However, the new
`_run_conversion()` shared body — the centerpiece of the "graceful shutdown"
guarantee — has an unguarded `ParquetDataCatalog(...)` construction and an
unguarded `self._funding_writer.flush()` call *outside* the per-type
try/except that protects the rest of the function. An exception there would
propagate out of `on_stop()`, faulting the component and undermining the very
reliability goal this phase is meant to deliver. There is also an inconsistency
in the V5 fail-fast validation: `heartbeat_interval_seconds` and
`stale_threshold_default_seconds`/`stale_threshold_seconds` get explicit
non-positive checks, but the structurally identical `restart_gap_threshold_seconds`
does not (and `PositiveInt` is not actually enforced at `NautilusConfig`
construction time, so this is not caught implicitly either).

## Critical Issues

### CR-01: `_run_conversion()` final flush/catalog-open is unguarded, can fault `on_stop()` and break graceful shutdown

**File:** `scripts/bybit_recorder/strategy.py:452-471`

**Issue:**
`_run_conversion()` is the shared body for both the periodic conversion timer
and the new `on_stop()` graceful-shutdown hook (REL-02). Inside the function,
each per-type conversion is wrapped in `try/except Exception` so one type's
failure doesn't block the others — but the two statements that run *before*
that loop are not protected:

```python
catalog = ParquetDataCatalog(self.config.catalog_path)

if self._funding_writer is not None:
    self._funding_writer.flush()
```

If either of these raises (e.g. a transient I/O error while flushing the
funding writer's feather file during shutdown, or a catalog-open failure),
the exception propagates uncaught out of `_run_conversion()` and therefore out
of `on_stop()`. Per `Component._stop()` semantics, an exception raised from
`on_stop()` is logged, re-raised, and transitions the component to a
Fault/Degraded state — which can interrupt the orderly shutdown sequence
(`Trader.stop()` → kernel closing the `"*"` `StreamingFeatherWriter`) that
this whole phase exists to make reliable. The periodic-timer call path
(`_convert_stream`) has the same exposure on every cycle, not just shutdown.

This is exactly the class of failure the per-type swallow was added to guard
against ("A transient conversion error is logged and swallowed PER TYPE so it
does not crash the recorder") — but the funding-writer flush and catalog
construction are not covered by that guarantee.

**Fix:**
Wrap the catalog construction and funding-writer flush in their own
try/except (or extend a single outer try/except around the whole function
body), logging and continuing rather than propagating:

```python
def _run_conversion(self) -> None:
    try:
        catalog = ParquetDataCatalog(self.config.catalog_path)
    except Exception:
        logger.exception("Failed to open catalog for conversion")
        return

    if self._funding_writer is not None:
        try:
            self._funding_writer.flush()
        except Exception:
            logger.exception("Failed to flush funding writer")

    for data_cls in [*_RECORDED_TYPES, FundingRateUpdate]:
        try:
            self._convert_finalized_feather_files(catalog, data_cls)
        except Exception:
            logger.exception("Failed to convert %s stream to catalog", data_cls.__name__)
```

## Warnings

### WR-01: `restart_gap_threshold_seconds` has no fail-fast validation, unlike the other REL-02/REL-03 thresholds

**File:** `scripts/bybit_recorder/config.py:127, 256-279, 287`

**Issue:**
`heartbeat_interval_seconds`, `stale_threshold_default_seconds`, and each
entry of `stale_threshold_seconds` are explicitly checked for `<= 0` and raise
`ValueError` at load time (lines 264-279), with a comment explaining the V5
fail-fast rationale (T-3-05 DoS-of-logs mitigation: too-small spams logs,
too-large/negative never warns).

`restart_gap_threshold_seconds` (line 287) is read straight from the TOML with
`recorder_raw.get("restart_gap_threshold_seconds", 60)` and passed directly
into `RecorderConfig` with no equivalent check. The field is typed
`PositiveInt` (line 127), but `NautilusConfig`/msgspec does **not** enforce
`Meta(gt=0)` constraints on direct construction (verified: `RecorderConfig(restart_gap_threshold_seconds=0)`
does not raise) — so a `0` or negative value in `recorder.toml` silently
passes through. A value of `0` would make `gap_s > 0` true on essentially
every restart (any nonzero wall-clock gap triggers the warning), and a
negative value has the same effect — both contradict the "configurable
threshold to avoid noise" intent documented for this setting.

**Fix:** Add the same fail-fast check used for the other thresholds:

```python
restart_gap_threshold_seconds = recorder_raw.get("restart_gap_threshold_seconds", 60)
if restart_gap_threshold_seconds <= 0:
    raise ValueError(
        f"Invalid restart_gap_threshold_seconds {restart_gap_threshold_seconds}: "
        "must be positive",
    )
```//and use the local variable when constructing `RecorderConfig`.

### WR-02: `_log_restart_gaps` only checks `TradeTick`, so quiet-trade instruments (e.g. SPOT) can have undetected gaps in quotes/deltas/bars/mark/index

**File:** `scripts/bybit_recorder/strategy.py:227-249`

**Issue:**
The restart-gap WARNING (D-06) is based solely on the most recent `TradeTick.ts_init`
per instrument. For an instrument/market with low trade frequency relative to
quote/orderbook/bar update rates (plausible for the configured `ETHUSDT-SPOT.BYBIT`
versus `BTCUSDT-LINEAR.BYBIT`), the last trade could be old even though quotes,
deltas, and bars were recorded recently — or vice versa, a long gap in
quotes/deltas could go unreported because the trade stream happened to have a
recent tick just before shutdown. The WARNING message ("Resuming after gap of
%.1fs for %s") reads as a per-instrument, all-streams gap indicator, but it
only reflects the trade stream's continuity.

This may be an accepted scope simplification (trades are usually the
highest-frequency / most-representative stream), but it is not documented as
such, and combined with the per-stream heartbeat (REL-03) the operator may be
misled into believing `_log_restart_gaps`'s silence means "no gap in any
stream" when it only means "no gap in trades".

**Fix:** Either (a) document explicitly in the docstring that this check is
trade-tick-specific and is a coarse proxy, not a per-stream guarantee (the
heartbeat/stale-stream WARNING covers the per-stream case going forward), or
(b) extend the check to use the most-recently-updated stream per instrument
(e.g. max across `trade_ticks`, `quote_ticks`, `bars` last `ts_init`).

### WR-03: `stale_threshold_seconds` values are not type-checked, so a non-numeric TOML value raises an unrelated `TypeError` instead of a clear `ValueError`

**File:** `scripts/bybit_recorder/config.py:275-279`

**Issue:**

```python
for stream, threshold in stale_threshold_seconds.items():
    if threshold <= 0:
        raise ValueError(
            f"Invalid stale_threshold_seconds[{stream!r}] {threshold}: must be positive",
        )
```

If a user writes `trade = "90"` (a string) in `[recorder.stale_threshold_seconds]`,
`tomllib` will happily parse it as a `str`, and `threshold <= 0` raises
`TypeError: '<=' not supported between instances of 'str' and 'int'` instead
of the intended descriptive `ValueError`. This is a minor robustness/UX gap in
the V5 fail-fast validation path — the error the operator sees won't point at
the actual misconfiguration as clearly as the other validation errors do.

**Fix:**

```python
for stream, threshold in stale_threshold_seconds.items():
    if not isinstance(threshold, int) or threshold <= 0:
        raise ValueError(
            f"Invalid stale_threshold_seconds[{stream!r}] {threshold!r}: must be a positive integer",
        )
```

## Info

### IN-01: `_heartbeat`'s "active streams" count includes streams that are currently stale

**File:** `scripts/bybit_recorder/strategy.py:302`

**Issue:** `logger.info("Heartbeat: %d active streams", len(self._last_seen))`
reports the total number of `(stream, instrument_id)` pairs ever recorded in
`_last_seen`, including any that were just flagged as stale in the WARNING
loop immediately above. The word "active" is misleading — a stream that has
gone silent for hours still counts toward this number indefinitely.

**Fix:** Either rename the log message (e.g. "Heartbeat: %d tracked streams")
or compute a count of streams currently within their stale threshold and
report that as "active":

```python
active = sum(
    1
    for (stream, _), last_ns in self._last_seen.items()
    if (now_ns - last_ns) / 1e9 <= self._stale_threshold_s(stream)
)
logger.info("Heartbeat: %d/%d streams active", active, len(self._last_seen))
```

### IN-02: `restart_gap_threshold_seconds` default is commented out in `recorder.toml` with no example of a non-default value

**File:** `scripts/bybit_recorder/recorder.toml:11`

**Issue:** The line
`# restart_gap_threshold_seconds = 60  # WARN on on_start if the gap since the last recorded ts_init exceeds this threshold (D-06)`
is purely informational (the default of 60 is applied via `config.py`), which
is consistent with how other defaulted fields are handled. This is fine, but
given WR-01 (no validation of this field), an operator who uncomments this
line and sets `0` to "disable" the warning would get the opposite effect
(WARNING on every restart) with no error raised — worth a short inline note
once WR-01 is fixed, so the documented behavior matches the validated
behavior.

**Fix:** No code change required beyond WR-01; once validation is added,
optionally add a comment noting that `0`/negative values are rejected.

---

_Reviewed: 2026-06-14T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
