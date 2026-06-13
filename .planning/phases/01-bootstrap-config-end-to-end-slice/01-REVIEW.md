---
phase: 01-bootstrap-config-end-to-end-slice
reviewed: 2026-06-13T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - scripts/bybit_recorder/__init__.py
  - scripts/bybit_recorder/config.py
  - scripts/bybit_recorder/recorder.py
  - scripts/bybit_recorder/recorder.toml
  - scripts/bybit_recorder/strategy.py
  - tests/unit_tests/persistence/recorder/__init__.py
  - tests/unit_tests/persistence/recorder/conftest.py
  - tests/unit_tests/persistence/recorder/test_recorder_config.py
  - tests/unit_tests/persistence/recorder/test_recorder_conversion.py
  - tests/unit_tests/persistence/recorder/test_recorder_strategy.py
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-06-13T00:00:00Z
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

Reviewed the bootstrap/config/strategy slice for the Bybit data collector. The
overall structure follows the documented Nautilus bootstrap pattern
(`TradingNodeConfig` -> `TradingNode` -> `add_strategy` -> `build()` ->
`run()`), and the CUSTOM_ENCODINGS workaround for pyo3-native enum
serialization is a justified, documented use of the framework's official
extension point (per the task brief, not flagged here).

No security or correctness issues that would crash the process or corrupt data
were found. The main issues are: a configured-but-unused `environment` field
that silently diverges from the TOML (a real "config says X, code does Y"
trap), an unguarded empty-instrument-list scenario that would build a node
with no subscriptions and no useful failure signal, and a couple of
maintainability/robustness gaps around error context and config validation.

## Warnings

### WR-01: `environment` config field is parsed but never used — hardcoded to MAINNET regardless of TOML value

**File:** `scripts/bybit_recorder/recorder.py:79`
**Issue:** `RecorderConfig.environment` is loaded from `recorder.toml` (`config.py:82,149`, default `"mainnet"`, documented as "The Bybit environment to connect to"), but `recorder.py` hardcodes
`BybitDataClientConfig(environment=BybitEnvironment.MAINNET, ...)` and never reads `recorder_cfg.environment`. If an operator sets `environment = "testnet"` in `recorder.toml`, the recorder will silently continue connecting to mainnet — a confusing, hard-to-diagnose misconfiguration with no warning or error. This is exactly the kind of "config says X but code does Y" defect that's easy to miss because the field name and default value happen to coincide with the hardcoded behavior.

Either this field is intentionally deferred to a later phase (in which case it should not yet be present in `RecorderConfig`/`recorder.toml`, or should be clearly marked "not yet consumed" the same way `InstrumentEntry.depth`/`bar_intervals` are documented at `config.py:42-47`), or it needs to be wired through, e.g.:
```python
ENVIRONMENT_MAP = {
    "mainnet": BybitEnvironment.MAINNET,
    "testnet": BybitEnvironment.TESTNET,
}

data_clients={
    BYBIT: BybitDataClientConfig(
        environment=ENVIRONMENT_MAP[recorder_cfg.environment],
        ...
    ),
},
```
**Fix:** Wire `recorder_cfg.environment` into `BybitEnvironment` selection, or add a docstring note (matching the `depth`/`bar_intervals` pattern at `config.py:42-47`) stating this field is parsed but not yet consumed, and raise/validate against unsupported values rather than letting them be silently ignored.

---

### WR-02: Empty `instruments` list produces a node with no subscriptions and a misleading "all instruments missing" error — or no error at all

**File:** `scripts/bybit_recorder/config.py:128-151`, `scripts/bybit_recorder/strategy.py:85-91`
**Issue:** `load_recorder_config` does not validate that at least one instrument is configured. If `[instruments]` is empty/absent in the TOML, `instruments` ends up `[]`, `instrument_ids` is `[]`, and:
- `RecorderStrategy.on_start` iterates an empty `self.config.instrument_ids`, `missing` stays empty, no `RuntimeError` is raised, and the strategy proceeds to subscribe to nothing.
- The recorder then runs "successfully" — connected, no errors logged, but recording zero instruments.

Given the project's emphasis on fail-fast/loud failure for misconfiguration (D-05/D-06, `recorder.py:103-109` raising non-zero exit on `on_start` errors), a silently-empty instrument list is the kind of misconfiguration that should be caught at config-load time rather than producing a "successfully running but doing nothing" service that's hard to notice is broken.
**Fix:** Add a check in `load_recorder_config` (or `RecorderConfig`) that raises if `instruments` is empty:
```python
if not instruments:
    raise ValueError("recorder.toml must configure at least one instrument")
```

---

### WR-03: `_convert_stream` swallows all exceptions with no rate-limit/backoff — repeated failures will spam logs every interval indefinitely

**File:** `scripts/bybit_recorder/strategy.py:122-133`
**Issue:** The broad `except Exception: logger.exception(...)` is documented as intentional ("transient conversion error... does not crash the recorder"), which is reasonable for a 24/7 service. However, there's no distinction between a truly transient error (e.g., a momentarily locked file) and a persistent/structural error (e.g., `catalog_path` points to a non-writable or non-existent directory, or a permissions issue). In the persistent case, this will log a full exception traceback every `conversion_interval_minutes` (default hourly) forever, without ever surfacing as a critical alert distinguishable from a one-off transient blip — both look identical in the logs.

This is a robustness/observability gap rather than a crash risk, but for a service meant to run unattended for long periods, a persistent conversion failure (e.g., disk full, permission denied) should be more visible than "exception logged hourly."
**Fix:** Consider tracking consecutive failure counts and escalating log level (e.g., `logger.error` with a distinct "N consecutive conversion failures" message) after a threshold, or at minimum log `logger.exception` with enough context (catalog_path, instance_id) to make repeated occurrences greppable/alertable:
```python
except Exception:
    logger.exception(
        "Failed to convert stream data to catalog (catalog_path=%s, instance_id=%s)",
        self.config.catalog_path,
        self.config.instance_id_str,
    )
```

---

### WR-04: `load_recorder_config` raises raw `KeyError`/`TypeError` for missing required TOML keys, with no contextual error message

**File:** `scripts/bybit_recorder/config.py:126,138-141,145-147`
**Issue:** Accessing `raw["recorder"]`, `recorder_raw["trader_id"]`, `recorder_raw["catalog_path"]`, `recorder_raw["streaming_path"]`, and `entry_raw["id"]`/`entry_raw["depth"]`/`entry_raw["bar_intervals"]` via plain `[...]` indexing means that a TOML file missing any of these required keys raises a bare `KeyError: 'trader_id'` (or similar) with no indication of *which file* or *which instrument entry* is malformed. For a config file hand-edited by an operator, this produces a confusing traceback rather than an actionable error message. The docstring (`config.py:113-119`) documents `KeyError` for the missing `[recorder]` table as expected behavior, but doesn't cover the per-field or per-instrument-entry case, and the resulting errors give no context about which entry (e.g., the 2nd linear instrument) is missing `depth`.
**Fix:** Wrap the per-instrument-entry construction with a clearer error, e.g.:
```python
for idx, entry_raw in enumerate((*linear_raw, *spot_raw)):
    try:
        instruments.append(
            InstrumentEntry(
                id=InstrumentId.from_str(entry_raw["id"]),
                depth=entry_raw["depth"],
                bar_intervals=entry_raw["bar_intervals"],
            ),
        )
    except KeyError as exc:
        raise KeyError(f"instrument entry {idx} missing required field: {exc}") from exc
```

## Info

### IN-01: `InstrumentEntry.depth` and `bar_intervals` are parsed and validated but entirely unused (dead config surface)

**File:** `scripts/bybit_recorder/config.py:42-53`, `scripts/bybit_recorder/recorder.py`, `scripts/bybit_recorder/strategy.py`
**Issue:** This is explicitly documented as intentional ("parsed now per CONF-02, not yet consumed in Phase 1"), so it's not a defect — but worth flagging for the reviewer's awareness: `PositiveInt` validation on `depth` means a TOML with `depth = 0` or `depth = -1` will fail at config-load time even though `depth` does nothing yet in Phase 1. This is fine as designed, just noting it as a forward-reference that should be revisited when Phase 2 consumes these fields, to ensure the validation semantics (e.g., is `depth` bounded to Bybit's supported depth values: 1/50/200/500?) match what the adapter actually accepts.
**Fix:** No action needed for Phase 1; tracked here for Phase 2 follow-up.

---

### IN-02: `RECORDER_INSTANCE_ID` duplicated as a string literal across `recorder.py` and `test_recorder_conversion.py`

**File:** `scripts/bybit_recorder/recorder.py:39`, `tests/unit_tests/persistence/recorder/test_recorder_conversion.py:25`
**Issue:** The same UUID string `"8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"` is hardcoded independently in both `recorder.py` (as `RECORDER_INSTANCE_ID`) and the test file (re-declared as a module constant with a comment "same constant used by the recorder strategy"), and again inline in `test_recorder_strategy.py:42` (`instance_id_str="8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"`). If this ID is ever changed in `recorder.py` (e.g., regenerated), the tests would silently continue passing against the stale value unless someone remembers to update all three locations — there's no single source of truth enforcing they stay in sync.
**Fix:** Consider importing `RECORDER_INSTANCE_ID` from `scripts.bybit_recorder.recorder` in the test files rather than re-declaring it, so a future change is enforced by a single constant:
```python
from scripts.bybit_recorder.recorder import RECORDER_INSTANCE_ID
```

---

### IN-03: `main()` argv handling has no validation of `config_arg` before passing to `load_recorder_config`

**File:** `scripts/bybit_recorder/recorder.py:114-116`
**Issue:** `sys.argv[1]` is passed straight through to `main()` -> `load_recorder_config()`, which opens the path with `open(path, "rb")`. If the path doesn't exist, this raises a raw `FileNotFoundError` with the standard Python traceback — acceptable for a systemd-run script (the traceback will be in the journal), but the error message gives no hint that the issue is specifically "config file not found at `<path>`" versus any other I/O error during config processing. Minor, since journald output would still show the path in the `FileNotFoundError` message itself.
**Fix:** Optional — wrap `load_recorder_config` call in `main()` with a clearer error message, or leave as-is since `FileNotFoundError` already includes the path.

---

_Reviewed: 2026-06-13T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
