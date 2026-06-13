---
phase: 01-bootstrap-config-end-to-end-slice
plan: 02
subsystem: config
tags: [tomllib, NautilusConfig, StreamingConfig, RotationMode, InstrumentId, PositiveInt]

# Dependency graph
requires:
  - "tests/unit_tests/persistence/recorder/test_recorder_config.py (RED tests from 01-01)"
provides:
  - "scripts.bybit_recorder.config.RecorderConfig + InstrumentEntry frozen NautilusConfig dataclasses"
  - "scripts.bybit_recorder.config.load_recorder_config(path) -> (RecorderConfig, list[InstrumentId])"
  - "scripts.bybit_recorder.config.build_streaming_config(recorder_cfg) -> StreamingConfig (daily SCHEDULED_DATES rotation, include_types=[TradeTick])"
  - "scripts/bybit_recorder/recorder.toml example config"
affects: [01-03, 01-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "tomllib (stdlib) binary-mode TOML loader, [[instruments.linear]]/[[instruments.spot]] array-of-tables -> list[dict] -> InstrumentEntry"
    - "InstrumentId.from_str(...) as the fail-fast malformed-id validation boundary (V5)"
    - "PositiveInt (nautilus_trader.common.config) for depth/conversion_interval_minutes >0 validation"
    - "build_streaming_config() reconciles streaming_path as the single shared StreamingConfig.catalog_path root (Pitfall 5/A4)"

key-files:
  created:
    - scripts/bybit_recorder/__init__.py
    - scripts/bybit_recorder/config.py
    - scripts/bybit_recorder/recorder.toml
  modified: []

key-decisions:
  - "load_recorder_config returns a (RecorderConfig, list[InstrumentId]) tuple — matches the test_recorder_config.py call signature (_, instrument_ids = load_recorder_config(...))"
  - "RecorderConfig.instrument_ids is a derived property (linear-then-spot order) rather than a stored field, computed from RecorderConfig.instruments"
  - "build_streaming_config() sets StreamingConfig.catalog_path = recorder_cfg.streaming_path (not catalog_path) — the streaming root is the single shared root the conversion catalog later reads from /live/{instance_id}/ (Pitfall 5)"

requirements-completed: [CONF-01, CONF-02, REC-07]

# Metrics
duration: 20min
completed: 2026-06-13
---

# Phase 1 Plan 02: Recorder Config Layer Summary

**Implemented `scripts/bybit_recorder/config.py` — a stdlib `tomllib` loader producing frozen `RecorderConfig`/`InstrumentEntry` dataclasses, a `build_streaming_config()` helper that returns a daily-rotating `StreamingConfig`, and an example `recorder.toml` — turning all of `test_recorder_config.py` GREEN.**

## Performance

- **Duration:** 20 min
- **Tasks:** 2 completed
- **Files modified:** 3 created

## Accomplishments
- `InstrumentEntry` (frozen `NautilusConfig`) carries `id: InstrumentId`, `depth: PositiveInt`, `bar_intervals: list[str]`
- `RecorderConfig` (frozen `NautilusConfig`) carries the five `[recorder]` keys (`trader_id`, `catalog_path`, `streaming_path`, `conversion_interval_minutes: PositiveInt = 60`, `environment = "mainnet"`) plus `instruments: list[InstrumentEntry]` and a derived `instrument_ids` property (linear-then-spot order)
- `load_recorder_config(path)` opens the TOML in binary mode, parses `[recorder]` plus `[[instruments.linear]]`/`[[instruments.spot]]` array-of-tables (linear first, then spot), validates each `id` via `InstrumentId.from_str` (raises `ValueError` on malformed ids — V5), and logs only the instrument count (D-09/V7)
- `build_streaming_config(recorder_cfg)` returns a `StreamingConfig` with `catalog_path=recorder_cfg.streaming_path`, `fs_protocol="file"`, `rotation_mode=RotationMode.SCHEDULED_DATES`, `rotation_interval=pd.Timedelta(days=1)`, `rotation_time=time(0,0,0)`, `rotation_timezone="UTC"`, `include_types=[TradeTick]`, with an inline `# WHY:` comment documenting the daily-rotation/day-partitioning link (Pitfall 1)
- `scripts/bybit_recorder/recorder.toml` mirrors the CONTEXT shape exactly: one `[[instruments.linear]]` (`BTCUSDT-LINEAR.BYBIT`, depth 50, `["1-MINUTE"]`) and one `[[instruments.spot]]` (`ETHUSDT-SPOT.BYBIT`, depth 50, `["1-MINUTE"]`), with a top comment noting credentials come from env vars only (D-09)
- All 4 tests in `tests/unit_tests/persistence/recorder/test_recorder_config.py` pass GREEN
- `ruff check`, `ruff format --check`, and `mypy` all pass cleanly on `scripts/bybit_recorder/config.py`

## Task Commits

Each task was committed atomically:

1. **Task 1: Config dataclasses + tomllib loader (CONF-01, CONF-02)** - `b3099b9391` (feat) — also includes `build_streaming_config` since both functions were implemented together in `config.py`
2. **Task 2: Example recorder.toml (REC-07)** - `0d4db5576b` (feat)

**Plan metadata:** (pending — recorded by orchestrator after merge)

## Files Created/Modified
- `scripts/bybit_recorder/__init__.py` - Package marker (14-line copyright header, empty otherwise)
- `scripts/bybit_recorder/config.py` - `InstrumentEntry`/`RecorderConfig` dataclasses, `load_recorder_config()`, `build_streaming_config()`
- `scripts/bybit_recorder/recorder.toml` - Example config matching the D-07/D-08 schema (one linear + one spot instrument)

## Decisions Made
- `load_recorder_config` returns `(RecorderConfig, list[InstrumentId])` — required by the test call shape `_, instrument_ids = load_recorder_config(...)` and `recorder_cfg, _ = load_recorder_config(...)`.
- `instrument_ids` is exposed as a derived property on `RecorderConfig` rather than stored separately, computed in linear-then-spot order directly from `self.instruments`.
- `RecorderConfig.instruments` defaults to `[]` to keep the frozen-config idiom consistent with `StreamingConfig`'s optional-field style, though `load_recorder_config` always populates it.
- Per the PATTERNS.md analog and plan instructions, `build_streaming_config` sets `StreamingConfig.catalog_path` to `recorder_cfg.streaming_path` (the streaming root), not `recorder_cfg.catalog_path` — this is the single shared root that the conversion-side `ParquetDataCatalog` reads `/live/{instance_id}/` from (Pitfall 5/A4). Both Tasks implemented this together since Task 1's verification already exercised `load_recorder_config`, and `build_streaming_config` was a natural co-location in the same module per the plan's read_first/action guidance — both functions landed in the Task 1 commit, with Task 2's commit adding only `recorder.toml`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree missing compiled Rust extension artifacts (`.so`/`.pyi`) — recurrence of 01-01 Deviation 1**
- **Found during:** Task 1 verification (pytest collection failed with `ModuleNotFoundError: No module named 'nautilus_trader.core.data'`)
- **Issue:** This worktree's checkout does not include the gitignored compiled `nautilus_trader.core.*` extension modules present in the main repo checkout, blocking all test collection.
- **Fix:** Recreated symlinks from this worktree's `nautilus_trader/` tree to the corresponding compiled `.so`/`.pyi` artifacts in the main repo checkout (`/home/mrqdt/code/nautilus_trader_fork/nautilus_trader/...`). Gitignored (`*.so`), not committed.
- **Files modified:** None tracked (gitignored symlinks only)
- **Verification:** `python -c "import nautilus_trader"` succeeds; `pytest tests/unit_tests/persistence/recorder/test_recorder_config.py -x -q` runs to completion
- **Committed in:** N/A (not a tracked change)

---

**Total deviations:** 1 auto-fixed (environment/blocking, recurrence of 01-01 Deviation 1 — expected per-worktree, not a code issue).
**Impact on plan:** Zero tracked-file impact. No scope creep; no architectural changes.

## Issues Encountered
None beyond the deviation documented above.

## Known Stubs
None. `depth` and `bar_intervals` are parsed into `InstrumentEntry` per CONF-02 but intentionally not yet consumed (Phase 2 work, as documented in the plan's `<behavior>` section) — this is a documented intentional deferral, not a stub blocking this plan's goal.

## Threat Flags
None — this plan implements exactly the mitigations specified in the plan's `<threat_model>` (T-01-03 via `InstrumentId.from_str`/`PositiveInt`, T-01-04 via the instrument-count-only log line and no `api_key`/`api_secret` fields). No new network endpoints, auth paths, or schema changes introduced.

## User Setup Required
None - no external service configuration required. (Same worktree-local note as 01-01: `.so`/`.pyi` symlinks may need recreating in a fresh worktree — see Deviation 1.)

## Next Phase Readiness
- `scripts.bybit_recorder.config` now provides `RecorderConfig`, `InstrumentEntry`, `load_recorder_config`, and `build_streaming_config` — the exact exports Plan 03 (strategy) and Plan 04 (node bootstrap + conversion) need.
- `RECORDER_INSTANCE_ID` (the fixed UUID4 constant per D-03/PATTERNS.md) is NOT yet defined anywhere — Plan 03/04 must add it as a shared module constant (noted in 01-01 SUMMARY as carried-forward work).
- No blockers for Wave 2 (Plan 03).

---
*Phase: 01-bootstrap-config-end-to-end-slice*
*Completed: 2026-06-13*

## Self-Check: PASSED

All created files and commit hashes verified present on disk / in git log.
