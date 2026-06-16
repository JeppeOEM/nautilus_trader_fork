---
phase: 07-common-recorder-module-dydx-recorder
plan: 02
subsystem: dydx-recorder-config
tags: [dydx, recorder, config, toml, validation, tdd]
dependency_graph:
  requires:
    - "scripts.common_recorder.config (_resolve_catalog_path, build_streaming_config, _validate_positive_thresholds) — Plan 07-01"
    - "nautilus_trader.core.nautilus_pyo3.DydxNetwork"
    - "nautilus_trader.model.identifiers.InstrumentId"
  provides:
    - "scripts.dydx_recorder.config.load_dydx_recorder_config — flat-perp TOML parser"
    - "scripts.dydx_recorder.config.DydxRecorderConfig — parsed dYdX recorder config"
    - "scripts.dydx_recorder.config.DydxInstrumentEntry — single dYdX perp entry (id + bar_intervals)"
    - "scripts.dydx_recorder.config._DYDX_VALID_INTERVALS — supported resolution whitelist"
    - "scripts.dydx_recorder.config._map_network — case-insensitive env -> DydxNetwork"
    - "scripts/dydx_recorder/recorder.toml — example config with DYDX-05 thresholds"
  affects:
    - "Plan 07-03 recorder.py (consumes load_dydx_recorder_config + DydxRecorderConfig + build_streaming_config re-export)"
tech_stack:
  added: []
  patterns:
    - "Config-load fail-fast whitelist (mirrors Bybit _LINEAR_VALID_DEPTHS) for bar intervals"
    - "Shared duck-typed helpers reused via import, not re-implemented"
    - "msgspec frozen NautilusConfig for instrument entry + recorder config"
key_files:
  created:
    - "scripts/dydx_recorder/__init__.py"
    - "scripts/dydx_recorder/config.py"
    - "scripts/dydx_recorder/recorder.toml"
    - "tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py"
  modified: []
decisions:
  - "dYdX config drops the depth knob (full-depth L2) and linear/spot split (no spot on dYdX v4); validates bar intervals instead of depths"
  - "_map_network defaults non-mainnet strings to TESTNET; the config default (mainnet) covers the missing-environment case, matching 24/7 archival intent"
  - "DYDX-05 thresholds tuned in TOML only: quote relaxed to 600 (event-driven synthesized quotes), deltas tight at 60 (book churn is the real liveness signal)"
metrics:
  duration: "~12 min"
  completed: "2026-06-16"
  tasks: 2
  files: 4
---

# Phase 07 Plan 02: dYdX Recorder Config Layer Summary

dYdX recorder config parser that loads a flat perpetual TOML list, fail-fast-validates bar intervals against the supported dYdX resolution set (DYDX-06) and instrument ids at the `InstrumentId.from_str` boundary, maps the environment string to `DydxNetwork` case-insensitively (DYDX-02), and reuses the Plan 01 shared path/streaming/threshold helpers — shipped with an example `recorder.toml` carrying the DYDX-05 relaxed-quote / tight-deltas tuning.

## What Was Built

- **`scripts/dydx_recorder/__init__.py`** — package marker + docstring describing the thin venue layer over `common_recorder`.
- **`scripts/dydx_recorder/config.py`** (282 lines):
  - `_DYDX_VALID_INTERVALS = {1/5/15/30-MINUTE, 1/4-HOUR, 1-DAY}` whitelist (source cited: `crates/adapters/dydx/src/common/enums.rs` `from_bar_spec`). No depth table.
  - `DydxInstrumentEntry(NautilusConfig, frozen=True)` — `id: InstrumentId`, `bar_intervals: list[str]`. No `depth`, no `product_type`.
  - `DydxRecorderConfig(NautilusConfig, frozen=True)` — mirrors Bybit `RecorderConfig` minus depth-related fields; `instrument_ids` property only (no `linear_instrument_ids`).
  - `_map_network(env) -> DydxNetwork` — case-insensitive (`env.lower() == "mainnet"` → MAINNET else TESTNET).
  - `load_dydx_recorder_config(path) -> tuple[DydxRecorderConfig, list[InstrumentId]]` — reads flat `[[instruments]]`, validates id + intervals, delegates threshold validation to shared `_validate_positive_thresholds`, resolves paths via shared `_resolve_catalog_path`, logs only the instrument count.
  - Re-exports `build_streaming_config` from `common_recorder.config` for Plan 03 symmetry.
- **`scripts/dydx_recorder/recorder.toml`** — `[recorder]` table (DYDX-COLLECTOR-001, shared catalog root, mainnet), `[recorder.stale_threshold_seconds]` with DYDX-05 tuning (quote=600 relaxed, deltas=60 tight), and two flat perp entries (BTC-USD-PERP.DYDX, ETH-USD-PERP.DYDX).
- **`tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py`** (19 test cases) — flat-perp id parsing, interval whitelist accept/reject (parametrized over all 7 supported intervals), malformed-id rejection, `_map_network` case-insensitivity (parametrized), default-environment, threshold round-trip, non-positive-threshold rejection, and a structural assertion that no depth/product axis exists.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 (RED) | Failing dYdX config tests | d014e275c7 | `__init__.py`, `test_dydx_recorder_config.py` |
| 1 (GREEN) | dYdX config parser + interval whitelist + DydxNetwork mapping | a7f51a0a1f | `config.py` |
| 2 | Example `recorder.toml` with DYDX-05 thresholds | d572fdccf0 | `recorder.toml` |

## Verification

- `tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py` — 19 passed.
- Full recorder suite (`tests/unit_tests/persistence/recorder/`) — 81 passed (Plan 01 shared helpers stay green; no behavior change).
- Task 2 verify command prints `2 600` (two perps parsed, relaxed quote threshold present).
- `ruff format` clean; `ruff check` clean (isort `__all__` fix applied).
- Acceptance greps: `_DYDX_VALID_INTERVALS` present; shared-helper import present; runtime import prints the 7-interval set; no `instruments.linear`/`instruments.spot`/`depth` knobs in the TOML; `BTC-USD-PERP.DYDX`, `quote=600`, `deltas=60` all present.

## TDD Gate Compliance

RED (`test(07-02)` d014e275c7) → GREEN (`feat(07-02)` a7f51a0a1f) → Task 2 feature commit. Gate sequence satisfied; RED was confirmed failing (`ModuleNotFoundError: scripts.dydx_recorder.config`) before implementation.

## Deviations from Plan

### Test-Environment Setup (not a code deviation)

The git worktree spawned for this wave had an empty `.venv` (3 packages) and no compiled `nautilus_trader` Rust extensions (`*.so`), so `nautilus_trader` could not import. To run the plan's `pytest` verification I:
- Synced the 111 gitignored `*.so` build artifacts from the main repo (`/home/mrqdt/code/nautilus_trader_fork/nautilus_trader/`) into the worktree (build artifacts only — no source change, nothing committed).
- Ran tests with the main repo's fully-provisioned venv interpreter (`/home/mrqdt/code/.../.venv/bin/python -m pytest`) from the worktree cwd, so the worktree's `scripts/` and `nautilus_trader/` take import precedence.
No project code was changed by this; it only made the plan's own verification commands runnable.

### Acceptance-grep wording vs. explanatory comments (Rule 1-adjacent clarification)

The plan's literal acceptance criteria for Task 1 said `grep -in "depth"` and `grep -in "linear\|spot"` on `config.py` "returns nothing", and for Task 2 `grep -c "...depth" recorder.toml` "returns 0". However the same plan's `<action>` blocks explicitly instruct adding comments/docstrings such as "DO NOT include a depth table — dYdX is full-depth L2" and "No depth, no product_type axis", which necessarily contain those substrings.

Resolution: the *substantive* intent (no depth knob, no linear/spot product split in the actual config surface) is enforced structurally by `test_dydx_config_has_no_depth_or_product_split` (asserts neither `depth` nor `product_type` is a struct field and `DydxRecorderConfig` has no `linear_instrument_ids`). For the TOML I reworded the single explanatory comment to avoid the `depth`/`spot` substrings, so the Task 2 grep now returns 0 cleanly. For `config.py` the remaining matches are exclusively in module/class docstrings and the mandated whitelist comment — they document the *absence* of those knobs, not their presence. Kept per the explicit `<action>` instruction; structural test is the real gate.

## Known Stubs

None — the config layer is fully wired and exercised by tests; the node-wiring `recorder.py` that consumes it is intentionally deferred to Plan 07-03 (documented in the plan).

## Self-Check: PASSED

- FOUND: `scripts/dydx_recorder/__init__.py`
- FOUND: `scripts/dydx_recorder/config.py`
- FOUND: `scripts/dydx_recorder/recorder.toml`
- FOUND: `tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py`
- Commit d014e275c7 (RED), a7f51a0a1f (GREEN), d572fdccf0 (Task 2) all present in `git log`.
