---
baseline_commit: ca2c19a5da7490b08bbf92767ff658f45a88e875
---

# Story 1.1: Verify the data-integrity gate end-to-end

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the builder/operator,
I want confirmation that every data-integrity invariant (interval capture, raw-delta opt-in, fail-closed rejection, reconnect/gap resilience, USD-liquidity classification) actually holds in the running collector,
so that I can trust every downstream ranking/backtest/live decision without re-checking data quality myself.

## Acceptance Criteria

1. **Snapshot interval is configurable (FR1).** Given the collector config, when I inspect the snapshot interval, then it is a `CollectorConfig` field (not hardcoded), its default is 0.5s, and changing it requires no code change.
2. **Raw-delta opt-in with per-coin retention (FR2).** Given a coin flagged for Raw Delta Capture, when the collector runs, then raw deltas are captured for that coin only, Snapshot capture for other coins is unaffected, and retention/pruning for that coin's raw-delta data is a per-coin config value including an "unlimited" (never-pruned) setting.
3. **Fail-closed gate (FR3).** Given an incoming tick that would produce a crossed book, a stale book, or a precision-invalid value, when the collector's gate evaluates it, then the item is rejected — never fabricated, clamped, or averaged — and a WARNING log line records the full offending payload and the specific rejection reason, and no validity/flag field is added to any schema.
4. **Reconnect & gap resilience (FR4).** Given a WebSocket/HTTP disconnect and reconnect, when the collector resumes, then no previously stored data is corrupted, and the resulting gap appears as a visible break (e.g. `None`/null) in any downstream chart/read rather than an interpolated or flat line.
5. **USD-denominated liquidity (FR5).** Given open interest and volume data for any coin, when liquidity is classified, then classification uses `volume24H` (USD) or `openInterest × oraclePrice`, and no code path compares raw token-unit `openInterest` directly against a USD threshold.
6. **Gaps fixed, not deferred.** Given any gap found while verifying AC1–AC5, when the gap is confirmed, then it is fixed within this story so all FR1–FR5 consequences hold in the current codebase.

## Tasks / Subtasks

- [x] Task 1 — Close the FR1 gap: config-driven snapshot interval (AC: #1)
  - [x] Add `snapshot_interval_seconds: float` to `CollectorConfig` (`dydx_collector/config.py:30-41`), defaulting to `0.5` in `load_config()` (`config.py:56-69`, alongside the other `raw.get(...)` fields)
  - [x] Replace the hardcoded `await asyncio.sleep(1.0)` in `Collector._second_loop` (`collector.py:264`) with `await asyncio.sleep(self._config.snapshot_interval_seconds)`
  - [x] Add/update a unit test asserting `load_config` defaults `snapshot_interval_seconds` to `0.5` and that a TOML override is honored (pattern: `dydx_collector/tests/test_client.py` or a new `test_config.py` — check for an existing config test file first)
- [x] Task 2 — Close the FR2 gap: per-coin raw-delta retention including "unlimited" (AC: #2)
  - [x] Add `retain_hours: float | None = None` to `InstrumentEntry` (`config.py:24-27`); `None` means unlimited (never pruned)
  - [x] Update `load_config` to parse `retain_hours` per `[[instruments]]` entry (`config.py:48-54`)
  - [x] Update `Collector._prune_loop` (`collector.py:321-333`) so pruning decisions for `store_order_book_deltas` instruments use that instrument's `retain_hours` (skip entirely if `None`) instead of being implicitly exempt from pruning only because they're pinned — today pinned instruments are unconditionally excluded from the `non_pinned` prune set (`collector.py:328`), which happens to make delta-capture data "unlimited" by accident, not by an explicit per-coin config value; make it explicit
  - [x] Add/update a test in `dydx_collector/tests/test_prune_catalog.py` (or `test_collector_snapshot.py` if prune logic is exercised there) covering: a pinned delta-capture instrument with `retain_hours=None` is never pruned, and one with `retain_hours=48` is pruned past that window
- [x] Task 3 — Verify AC3 (fail-closed gate) holds as-is; fix only if a gap is found (AC: #3)
  - [x] Confirm empty top-of-book skip (`collector.py:271`), crossed-book skip+warn (`collector.py:274-281`), and stale-book skip+warn (`collector.py:288-294`) all remain intact and covered by `dydx_collector/tests/test_collector_snapshot.py` (`test_crossed_book_guard_returns_none`, `test_touched_book_guard_returns_none`, `test_stale_book_emits_identical_snapshots_each_second`)
  - [x] Confirm no schema (`second_snapshot.py`, `minute_bars.py`) carries a validity/flag field
  - [x] Confirm precision-invalid values cannot reach the catalog: `client.py`'s `_at_fixed_precision()` unconditionally re-stamps every mark/index tick at `FIXED_PRECISION` (`client.py:45-66`, called at `client.py:147,157`) before it ever reaches `_on_data` — this is a proactive fix, not a reject-on-detect path, so there should be no separate "precision-invalid" rejection branch to find for mark/index. If any other data path (trades, order book deltas) can carry inconsistent precision, add a reject+`logger.warning` branch mirroring the crossed/stale pattern; otherwise no code change needed here — document the finding in Dev Notes either way
- [x] Task 4 — Verify AC4 (reconnect & gap resilience) holds as-is; fix only if a gap is found (AC: #4)
  - [x] Confirm `_STALE_BOOK_NS` skip in `collector.py:74,283-294` and `_CHART_GAP_THRESHOLD_MS` → `None` gap insertion in `ml_signals/dashboard.py:77,578` are both still present and unmodified (per architecture Deferred note: the staleness-gap rendering in the dashboard is a *permanent* reader responsibility, not redundant cleanup — do not remove it)
- [x] Task 5 — Verify AC5 (USD-denominated liquidity) holds as-is; fix only if a gap is found (AC: #5)
  - [x] Confirm `classify_liquidity` (`open_interest.py:109-142`) uses `volume24H` only, and that no call site anywhere compares raw `openInterest` against `liquidity_min_oi_usd` — found and fixed a stale/misplaced test (`test_minute_bars.py`) still asserting the pre-fix `openInterest`-based behavior; moved corrected `volume24H`-based coverage into `test_open_interest.py`
- [x] Task 6 — Run the full `troll/` test suite and confirm no regressions (AC: #6)
  - [x] `pytest troll/dydx_collector troll/ml_signals` — all tests pass, including new tests added in Tasks 1–2 and 5

### Review Findings

- [x] [Review][Patch] Hot-reload never refreshes `_delta_store`/`_delta_retain_hours` [troll/dydx_collector/collector.py:250-264] — fixed: `_reload_config_loop` now rebuilds both from `new_config.instruments`
- [x] [Review][Patch] Prune-loop cadence ignores per-coin `retain_hours`, over-retaining raw deltas beyond their configured window [troll/dydx_collector/collector.py:328-329] — fixed: extracted `_prune_interval_seconds()`, cadence now derived from the shortest active retention window (global or per-coin), recomputed every loop iteration
- [x] [Review][Patch] No validation on `retain_hours`/`snapshot_interval_seconds` for zero/negative values [troll/dydx_collector/config.py] — fixed: `load_config` raises `ValueError` for negative `retain_hours` or non-positive `snapshot_interval_seconds`
- [x] [Review][Patch] Missing direct test for `_prune_loop`'s per-coin `None`-skip branch — Task 2's own subtask required this exact scenario and it was missed [troll/dydx_collector/collector.py:340-343] — fixed: extracted `_prune_delta_retention()`, directly unit-tested for both the `None`-skip and finite-retention-prunes cases
- [x] [Review][Patch] Silent no-op when `data_types` names a nonexistent directory [troll/dydx_collector/prune_catalog.py:113-116] — fixed: logs a `WARNING` per missing type dir and continues with the valid ones; tested
- [x] [Review][Patch] Module docstring "pinned... kept forever" is now imprecise since per-coin `retain_hours` can prune a pinned instrument's raw deltas [troll/dydx_collector/collector.py:23] — fixed: docstring now distinguishes subscription/tier permanence from per-coin raw-delta retention
- [x] [Review][Defer] `classify_liquidity`'s `min_oi_usd` parameter name is stale (function is `volume24H`-based, not OI-based) [troll/dydx_collector/open_interest.py:109] — deferred, pre-existing, out of Story 1.1 scope; logged in `deferred-work.md`

## Dev Notes

**Two confirmed gaps to fix — this is not a pure verification story:**

1. **FR1 gap:** `Collector._second_loop` hardcodes `await asyncio.sleep(1.0)` (`collector.py:264`). There is no `snapshot_interval_seconds` field on `CollectorConfig` at all. The PRD requires this to be config-driven with a 0.5s default. Fix per Task 1.
2. **FR2 gap:** `InstrumentEntry.store_order_book_deltas` (`config.py:24-27`) is a plain bool — raw-delta opt-in itself works — but there is no per-coin retention field. Today, delta-capture instruments happen to never be pruned only because `_prune_loop` (`collector.py:321-333`) excludes all pinned instruments from its prune set (`store_order_book_deltas` is only settable on `[[instruments]]` entries, which are always pinned) — "unlimited" retention is an accident of the pinned/non-pinned split, not an explicit per-coin config value as FR2 requires. Fix per Task 2.

**What already holds (verify, don't rebuild):**
- Fail-closed gate (AC3): empty-book, crossed-book, and stale-book rejections all exist with WARNING logging and no flag-field — see `collector.py:271-294`. Already covered by tests in `test_collector_snapshot.py`.
- Reconnect/gap resilience (AC4): `_STALE_BOOK_NS = 5s` (`collector.py:68-74`) + dashboard's `_CHART_GAP_THRESHOLD_MS = 2.5s` → `None` gap (`dashboard.py:77,578`). The architecture spine explicitly flags the dashboard's gap-rendering as **permanent**, not cleanup — don't touch it. (A separate, already-identified cleanup — removing the dashboard's now-redundant crossed-book skip — is out of scope for this story; it's listed as future cleanup in the architecture's Deferred section.)
- USD-denominated liquidity (AC5): `classify_liquidity` (`open_interest.py:109-142`) already uses `volume24H`, never raw `openInterest`, against `liquidity_min_oi_usd`.

**Architecture paradigm (must not violate):** This codebase follows a "Gatekeeper: fail-closed single-writer ingestion" paradigm — `dydx_collector/collector.py` is the *only* writer to `ParquetDataCatalog`/Redis, and the *only* place invariants are checked. Any fix in this story stays inside `collector.py`/`config.py`/`prune_catalog.py` (the gate); never add validation logic to `ml_signals` readers (AD-3 — readers trust the gate completely).

**Precision rule (AD-5, project-wide):** If Task 3 does surface a need for a new precision check, never use `Price(decimal, precision)`/`Quantity(decimal, precision)` — silently corrupts values for some inputs. Always `Decimal.scaleb(new_precision)` + `Price.from_raw()`/`Quantity.from_raw()`. Reference: `client.py:_at_fixed_precision()`.

### Project Structure Notes

- All changes for Tasks 1–2 are confined to `troll/dydx_collector/` (config.py, collector.py, prune_catalog.py) — consistent with the module boundary convention (AD-4): `ml_signals` must not be touched by this story.
- No new files needed; this story only extends existing dataclasses (`CollectorConfig`, `InstrumentEntry`) and existing loop/prune functions.
- `dydx_collector/config.toml` is the runtime config file (not present in the repo tree — user-supplied/deploy-time file, gitignored). Don't assume a checked-in example exists; if a documented default/example config is needed for the new fields, check `troll/dydx_collector/README*` or docker-compose volume mounts for where it's expected to live before adding one.

### References

- [Source: troll/dydx_collector/collector.py:261-319] `_second_loop` — hardcoded interval, crossed/stale book gate
- [Source: troll/dydx_collector/config.py:24-69] `InstrumentEntry`, `CollectorConfig`, `load_config`
- [Source: troll/dydx_collector/prune_catalog.py:87-113] `prune_instrument` — per-instrument retention-hours pruning
- [Source: troll/dydx_collector/client.py:45-66] `_at_fixed_precision` — proactive precision re-stamping for mark/index
- [Source: troll/dydx_collector/open_interest.py:109-142] `classify_liquidity` — USD-denominated liquidity split
- [Source: troll/ml_signals/dashboard.py:77,570-578] `_CHART_GAP_THRESHOLD_MS`, chart gap/crossed-book handling
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-1] through AD-5, AD-7 and Deferred section
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.1] original story definition

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `PYTHONPATH=. python -m pytest dydx_collector/tests ml_signals/tests --ignore=ml_signals/tests/test_ofi_strategy.py -q` → 140 passed (baseline: 135 passed, 1 pre-existing failure, 1 pre-existing collection error — see Completion Notes)
- Post-code-review (all 6 patches applied) → 149 passed, 0 failed

### Completion Notes List

- **FR1 gap closed:** `snapshot_interval_seconds` added to `CollectorConfig`/`load_config` (default `0.5`), `_second_loop` now reads it instead of a hardcoded `1.0`.
- **FR2 gap closed:** `InstrumentEntry.retain_hours: float | None` added (`None` = unlimited). `prune_instrument()` gained an optional `data_types` filter so per-coin raw-delta retention can be pruned independently of the pinned/non-pinned tier split. `Collector._prune_loop` now prunes `order_book_deltas` per-instrument per `_delta_retain_hours`, skipping instruments configured as unlimited.
- **AC3/AC4/AC5 verified, no code changes required** for the existing crossed/stale-book gate, dashboard gap rendering, or `classify_liquidity`'s USD-denominated split — all already correct and (mostly) tested.
- **Found and fixed a stale test while verifying AC5:** `test_minute_bars.py::test_classify_liquidity_splits_by_threshold` and a sibling test asserted `classify_liquidity`'s pre-AD-7-fix behavior (keyed off `openInterest` instead of `volume24H`) and was failing against current code before this story (confirmed via `git diff` — pre-existing, not introduced by this session). Removed the misplaced/stale tests from `test_minute_bars.py` and added corrected, `volume24H`-based coverage (plus an `exclude` case and the BTC-low-token-count regression case) to `test_open_interest.py`, where `classify_liquidity` actually lives.
- **Pre-existing, out-of-scope issue found and left untouched:** `ml_signals/ofi_strategy.py:203` has a `SyntaxError` (unclosed paren) that breaks collection of `ml_signals/tests/test_ofi_strategy.py`. Confirmed via `git diff`/`git log` that this predates this story and is unrelated to Epic 1's FR1–FR5 scope (it's `ofi_strategy.py`, Epic 3 territory). Flagging for a separate fix — not addressed here per story scope discipline.
- Ran the full suite with that one file ignored: 140 passed, 0 failed.
- Ran `ruff check` and `mypy` on all touched files: findings present are pre-existing (confirmed via `git diff`/`git show HEAD:...` — unrelated import-sort/docstring/complexity/datetime-alias style debt and one pre-existing `_filename_end_ns` return-type mismatch in `prune_catalog.py`); no new lint or type errors introduced by this story's changes.
- **Code review (bmad-code-review, 3 parallel adversarial layers) applied and resolved 6 patch findings:** hot-reload now refreshes `_delta_store`/`_delta_retain_hours`; prune cadence now considers the shortest active per-coin retention window (extracted `_prune_interval_seconds()`); `load_config` now rejects negative `retain_hours`/non-positive `snapshot_interval_seconds`; extracted `_prune_delta_retention()` and added the direct `Collector`-level test Task 2 originally required for the `None`-skip path; `prune_instrument` now logs a `WARNING` and skips (rather than silently no-ops) on a nonexistent `data_types` entry; clarified the "pinned... kept forever" module docstring. One finding deferred (pre-existing `classify_liquidity` param naming, out of scope) — see `deferred-work.md`. Re-ran full suite post-patch: 149 passed.

### File List

- `troll/dydx_collector/config.py` — added `snapshot_interval_seconds` (CollectorConfig) and `retain_hours` (InstrumentEntry); `load_config` parses both
- `troll/dydx_collector/collector.py` — `_second_loop` uses configurable interval; `_delta_retain_hours` map added; `_prune_loop` prunes per-coin raw-delta retention
- `troll/dydx_collector/prune_catalog.py` — `prune_instrument()` gained optional `data_types` filter
- `troll/dydx_collector/tests/test_config.py` — new: snapshot interval + retain_hours config tests
- `troll/dydx_collector/tests/test_prune_catalog.py` — new tests for `prune_instrument`'s `data_types` filter
- `troll/dydx_collector/tests/test_open_interest.py` — added corrected `classify_liquidity` tests (`volume24H`-based)
- `troll/dydx_collector/tests/test_minute_bars.py` — removed stale/misplaced `classify_liquidity` tests (moved to `test_open_interest.py`)
- `troll/dydx_collector/tests/test_collector_snapshot.py` — added tests for `_prune_delta_retention` and `_prune_interval_seconds`

## Change Log

- 2026-07-01 — Implemented Story 1.1: closed FR1 (config-driven snapshot interval) and FR2 (per-coin raw-delta retention) gaps; verified FR3/FR4/FR5 hold; fixed a stale `classify_liquidity` test found during FR5 verification. Status → review.
- 2026-07-01 — Code review: applied 6 patch fixes (hot-reload refresh, prune-cadence fix, config validation, missing test added, silent-no-op logging, docstring clarity); 1 finding deferred. Status → done.
