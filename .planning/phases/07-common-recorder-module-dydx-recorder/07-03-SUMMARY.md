---
phase: 07-common-recorder-module-dydx-recorder
plan: 03
subsystem: dydx-recorder
tags: [dydx, recorder, trading-node, custom-encodings, funding-dedup, strategy-reuse]
requires:
  - "scripts.common_recorder.strategy.RecorderStrategy (07-01)"
  - "scripts.dydx_recorder.config.load_dydx_recorder_config / build_streaming_config / _map_network (07-02)"
provides:
  - "scripts.dydx_recorder.recorder.main — dYdX recorder entrypoint"
  - "scripts.dydx_recorder.recorder.RECORDER_INSTANCE_ID — fixed dYdX instance id"
  - "CUSTOM_ENCODINGS[DydxNetwork] registration — lowercase-name encoder"
affects:
  - "tests/unit_tests/persistence/recorder/ (new dYdX strategy test file)"
tech-stack:
  added: []
  patterns:
    - "Shared-strategy reuse via composition (no venue subclass)"
    - "CUSTOM_ENCODINGS extension point for pyo3 enum streaming serialization"
    - "Private _clients registry injection for runtime instrument loading"
key-files:
  created:
    - "scripts/dydx_recorder/recorder.py"
    - "tests/unit_tests/persistence/recorder/test_dydx_recorder_strategy.py"
  modified: []
decisions:
  - "All dYdX perps treated as linear (linear_instrument_ids=instrument_ids) per RESEARCH A1"
  - "Dummy depth=50 for all instruments (dYdX is full-depth L2; adapter ignores the knob)"
  - "Distinct RECORDER_INSTANCE_ID (3c4d5e6f-...) to avoid feather-dir collision with Bybit"
  - "dYdX tests use genuine .DYDX ids via to_dict/from_dict retag of a perp stub"
metrics:
  duration: "~25m"
  completed: "2026-06-16"
  tasks: 2
  files: 2
requirements: [DYDX-02, DYDX-03, DYDX-04, DYDX-07]
---

# Phase 7 Plan 3: dYdX Recorder Node Wiring Summary

Wired `scripts/dydx_recorder/recorder.py` to build a dYdX `TradingNode` that reuses the
shared `RecorderStrategy` verbatim (no subclass), registers `CUSTOM_ENCODINGS[DydxNetwork]`
for streaming serialization, and injects the live data client + dYdX config loader after
build — plus a strategy test file proving all 7 feeds subscribe per dYdX perp and that funding
dedup is exchange-agnostic.

## What Was Built

### Task 1 — dYdX recorder.py wiring (commit `5008d14a36`)
- `scripts/dydx_recorder/recorder.py` mirrors the Bybit recorder, swapping venue symbols:
  `DYDX`, `DydxDataClientConfig`, `DydxLiveDataClientFactory`, `DydxNetwork`.
- Module-level `RECORDER_INSTANCE_ID = "3c4d5e6f-7a8b-49c0-a1d2-e3f405162738"` — a valid v4
  UUID distinct from the Bybit recorder's, so the two recorders' feather dirs never collide
  under the shared catalog root (T-07-08).
- `CUSTOM_ENCODINGS[DydxNetwork] = lambda value: value.name` registered at import so
  `TradingNodeConfig.json()` can serialize the pyo3-native enum (T-07-06). `DydxNetwork.MAINNET.name`
  is `"mainnet"` (lowercase — Pitfall 3); verified by the import-time encoder check.
- `main()` builds `TradingNodeConfig` with `DydxDataClientConfig(environment=_map_network(...),
  instrument_provider=InstrumentProviderConfig(load_ids=...))` (NO `product_types` — dYdX has no
  spot axis), reuses `RecorderStrategy` via composition with `linear_instrument_ids=instrument_ids`
  (all perps linear, A1) and a dummy `instrument_depths` of 50 (full-depth L2; adapter ignores it).
- After `node.build()`: injects the live client via the private `_clients[ClientId(DYDX)]`
  registry (`set_data_client`) and the dYdX loader (`set_config_loader(load_dydx_recorder_config)`).
- Runs with `node.run(raise_exception=True)` so an `on_start` failure exits non-zero for systemd.

### Task 2 — dYdX strategy tests (commit `97e8e431b1`)
- `tests/unit_tests/persistence/recorder/test_dydx_recorder_strategy.py` drives the SHARED
  `RecorderStrategy` (no dYdX subclass) with genuine dYdX ids (`BTC-USD-PERP.DYDX`,
  `ETH-USD-PERP.DYDX`), registered in the cache via a `to_dict`/`from_dict` retag of a perp stub.
- DYDX-03: asserts all 7 feeds subscribe for a single perp (trade, quote, order_book_deltas at
  `BookType.L2_MBP`, bars ending `-LAST-EXTERNAL`, mark, index, funding), that the bar type carries
  the dYdX id, and that mark/index/funding fire for BOTH of two perps (all-perps-linear).
- DYDX-04: asserts identical-rate updates dedup (persist once), a changed rate re-persists, and
  that the same numeric rate for two different dYdX ids persists both (per-instrument keying).
- Also folded a C420 ruff fix (`dict.fromkeys`) into recorder.py.

## Verification

- recorder.py imports cleanly; `CUSTOM_ENCODINGS[DydxNetwork](DydxNetwork.MAINNET)` prints `mainnet`.
- New dYdX test file: 5 passed.
- Full recorder suite: 86 passed (no regression to the shared strategy).
- DYDX-07 reliability parity is structural: `grep -rn "reconnect\|resubscribe" scripts/dydx_recorder/`
  returns nothing — zero custom reconnect code; flush+convert/heartbeat/restart-gap are inherited
  from the shared strategy and covered by the full suite.
- `ruff check` and `ruff format --check` pass on both new files.

### Test environment note
The worktree's `.venv` had no compiled Cython extensions. Since CLAUDE.md forbids modifying
`nautilus_trader/` (and the source is byte-identical to the main checkout), the 111 compiled
`.so` artifacts were symlinked from the main repo's built tree into the worktree to run the
suite. These symlinks are gitignored (`*.so`) and were not committed; no source under
`nautilus_trader/` was touched.

## Deviations from Plan

None — both tasks executed as written. One in-scope lint fix (Rule 1): replaced a dict
comprehension with `dict.fromkeys` in recorder.py to satisfy ruff C420; folded into the Task 2
commit. Tests were strengthened to use genuine `.DYDX` instrument ids (via a stub retag helper)
rather than re-using Binance stub ids, fully honoring the plan's "uses dYdX instrument ids" intent.

## Requirements Satisfied

- **DYDX-02** — dYdX `TradingNode` runs reusing the shared strategy, with `DydxNetwork` in
  `CUSTOM_ENCODINGS` and the client + loader injected.
- **DYDX-03** — all 7 dYdX feeds subscribe per perp (proven by the new test file).
- **DYDX-04** — funding dedup keys on `(instrument_id, rate)` for dYdX ids (proven).
- **DYDX-07** — reliability parity inherited from the shared strategy (no custom reconnect code).

## Self-Check: PASSED
- FOUND: scripts/dydx_recorder/recorder.py
- FOUND: tests/unit_tests/persistence/recorder/test_dydx_recorder_strategy.py
- FOUND commit: 5008d14a36
- FOUND commit: 97e8e431b1
