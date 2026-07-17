---
baseline_commit: 53041f3d086d28502287ccf7fb9d8101e7130450
---

# Story 2.2: Single indicator implementation reused across research, backtest, and live

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a strategy developer,
I want every indicator (`Microprice`, `OrderFlowImbalance`, `MultiLevelOBI`, `MultiLevelOFI`, `OnlineLogisticTrend`, and future signals) to have exactly one implementation reused unmodified in Jupyter, backtest, and live contexts,
so that a signal validated in research behaves identically everywhere it's used.

## Acceptance Criteria

1. **No parallel/duplicate implementation, wherever an indicator is actually used (FR10).** Given the existing indicators in `ml_signals/indicators.py`, when they are used in the research notebook, a `BacktestNode`-run strategy, and (once built in Epic 3) the live Dummy Strategy, then the same class/import is used in all contexts where an indicator is actually consumed — this is a conditional consistency rule, not a mandate that every indicator already be used in all three contexts today (Epic 3's live strategy doesn't exist yet).
2. **New indicators go in the shared module (FR10).** Given a new indicator built for the first time, when it is authored, then it is added to `ml_signals/indicators.py` as a Nautilus `Indicator` subclass importable by all consuming contexts, never defined locally in a notebook or strategy file.
3. **Cross-context consistency is actually verified, not just assumed.** Given an indicator's behavior is validated in the research notebook (or an equivalent direct-replay path), when the same indicator is instantiated inside a `BacktestNode`/`BacktestEngine`-run strategy, then it produces identical output for identical input data — a real regression/consistency test asserting this, not a code-reading argument.
4. **No consumer reimplements indicator logic locally (AD-4, SIGNAL-01).** Given the module-boundary convention, when indicators are imported by `ml_signals` consumers, then no consumer reimplements indicator logic locally — always imported from `ml_signals/indicators.py`.

## Tasks / Subtasks

- [x] Task 1 — Audit: confirm AC1/AC2/AC4 already hold (no code changes expected here) (AC: #1, #2, #4)
  - [x] Re-verified via fresh `grep` across every `ml_signals/*.py` and the Story 2.1 notebook: all 8 `from ml_signals.indicators import ...` sites are exactly as previously audited (`dashboard.py`: `MultiLevelOBI`/`MultiLevelOFI`; `chart_data.py`/`metrics_computer.py`: `Microprice`/`OrderFlowImbalance`; `example_strategy.py`: `OnlineLogisticTrend`; `ofi_strategy.py`: `OrderFlowImbalance`; notebook: `Microprice`). No local reimplementation of any indicator-like logic found anywhere.
  - [x] Confirmed `backtest_dydx.py` → `ml_signals.example_strategy:LogisticTrendStrategy`/`LogisticTrendConfig` and `backtest_ofi.py` → `ml_signals.ofi_strategy:OFIStrategy`/`OFIStrategyConfig` all resolve to real classes at the referenced paths — no dangling references.
  - [x] No new violation found — no code changes needed for this task.

- [x] Task 2 — Add the missing cross-context consistency test (AC: #3) — **this is the one real piece of new work in this story**
  - [x] Read `ml_signals/ofi_strategy.py`'s `on_order_book_deltas` and `test_ofi_strategy.py`'s `_engine()`/`_delta()`/`_batch()` helpers in full. Used `OrderFlowImbalance` as the test subject, per the story's own reasoning.
  - [x] Handled the batching subtlety via option (a): single-delta-per-`OrderBookDeltas`-batch synthetic data, sidestepping the per-batch-vs-per-delta update-timing mismatch entirely.
  - [x] Built the synthetic delta sequence and fed it through both the direct-replay path (`top_of_book_series` + fresh `OrderFlowImbalance`) and a real `BacktestEngine` run of `OFIStrategy`. Asserted final-state equality (`value`, `initialized`) — confirmed passing.
  - [x] **Real infrastructure bug found during implementation, not anticipated in the story:** a second `BacktestEngine`-constructing test file, if it collects (alphabetically) *before* `test_ofi_strategy.py`, causes a fatal native abort partway through `test_ofi_strategy.py`'s own engine construction. Reproduced deterministically, and confirmed via a byte-for-byte copy of `test_ofi_strategy.py`'s own first test (same config, same data, run from a separate module) that this is **not** specific to this test's content/parameters — it's a pre-existing fragility in how this pinned `nautilus_trader` version's `BacktestEngine`/kernel logging initialization behaves across module boundaries within one pytest process. Tried and ruled out: explicit `engine.reset()`/`dispose()` ordering (already correct), forced `gc.collect()` after disposal (no effect), reducing to a single engine construction in the new file (still crashes). **Mitigation applied:** the new test file is named `test_ofi_strategy_indicator_consistency.py` instead of the originally planned `test_indicator_consistency.py`, specifically so it collects *after* `test_ofi_strategy.py` alphabetically — confirmed this avoids the crash across repeated runs. Logged as a real, unresolved finding in `deferred-work.md` since it's a genuine constraint on this test suite going forward (any future `BacktestEngine`-based test file must be named/ordered to collect after `test_ofi_strategy.py`, or the underlying issue needs proper root-causing), not something specific to this story.
  - [x] Test file: `ml_signals/tests/test_ofi_strategy_indicator_consistency.py` (renamed from the story's originally planned `test_indicator_consistency.py` for the reason above — same one-file-per-concern intent, just a different filename to avoid the ordering-sensitive crash).

- [x] Task 3 — Documentation note, not a functional requirement (AC: #1)
  - [x] Added the per-indicator context-coverage note to `ml_signals/indicators.py`'s module docstring (one of the two locations the task allowed) — documents that `OrderFlowImbalance` has both a backtest and direct-replay consumer, `Microprice` has direct-replay + notebook but no backtest consumer, `OnlineLogisticTrend` only has a backtest consumer, and `MultiLevelOBI`/`MultiLevelOFI` only have the dashboard's live-monitor consumer. No new notebook/backtest/dashboard consumption added — out of scope, as specified.

### Review Findings

- [x] [Review][Patch] `ml_signals/indicators.py`'s new docstring incorrectly claims `OrderFlowImbalance` has a research-notebook consumer — confirmed by two independent reviewers (and contradicts this story's own Dev Notes, which correctly state "no notebook demo today" for this indicator) that only `Microprice` appears in `dydx_catalog_pandas.ipynb`. Fixed: docstring now attributes the notebook consumer to `Microprice` only. [troll/ml_signals/indicators.py]
- [x] [Review][Patch] No `try`/`finally` around `_run_backtest_strategy`'s `engine.reset()`/`engine.dispose()`. Fixed: `engine.run()` + state read now wrapped in `try`, cleanup moved to `finally`. [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] The test/module docstring overclaimed cross-context equivalence — `top_of_book_series` skips crossed-book states while `OFIStrategy.on_order_book_deltas` doesn't. Fixed: module docstring now states the narrower, accurate claim (integration-code parity on well-formed books) and explicitly names the crossed-book asymmetry as a separate, pre-existing, out-of-scope gap. [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] Assertion message said "5 updates" but only 4 `update_raw()` calls occurred. Fixed: message now uses `len(direct_results)` dynamically instead of a hardcoded count (also correct after Task's price-move-branch fix changed the actual count to 6). [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] Inline comment mischaracterized the crash as an engine-count cap rather than cross-module ordering. Fixed: comment corrected to describe the actual, confirmed root cause. [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] Missing `if __name__ == "__main__":` block. Fixed: added, matching `test_ofi_strategy.py`'s convention. [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] Alphabetical-filename mitigation isn't robust to all invocation methods (repo-root `pyproject.toml`'s `addopts` could reorder collection under a host-venv invocation). Fixed: documented explicitly in `deferred-work.md`, including confirmation that the currently-used Docker invocation path is unaffected. [deferred-work.md]
- [x] [Review][Patch] Synthetic data only exercised size changes at a constant price, never a price move. Fixed: added a better-priced bid `ADD` and a better-priced ask `ADD` to the sequence, exercising the price-improved branches on both sides; re-verified the test still passes. [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] `ma_period=1` was dead/confusing configuration. Fixed: added a comment explaining it's irrelevant to what this test validates (the test reads `_ofi` directly, never the MA/history-append path). [troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py]
- [x] [Review][Patch] `test_ofi_strategy.py` carried no warning about the ordering constraint. Fixed: added a one-line protective comment to its module docstring (comment-only, no functional change — a deliberate, narrow, well-justified exception to this story's "don't touch" scope note). [troll/ml_signals/tests/test_ofi_strategy.py]

## Dev Notes

**This story is mostly an audit that already came back clean, plus one real new test.** Task 1 re-confirms what was already verified during story creation: every current consumer of `ml_signals/indicators.py` imports correctly, with zero local reimplementation anywhere. Task 2 is the one substantive piece of new work — AC3's cross-context consistency test does not exist yet (`ml_signals/tests/test_indicators.py` only tests indicators in pure isolation, never comparing behavior across two different consuming contexts).

**Per-indicator context coverage today (informational, not a gap to close in this story):**
- `Microprice`: research notebook (Story 2.1) + dashboard (`metrics_computer.py`/`chart_data.py`, a live-monitoring read path, not a `Strategy`) — no `BacktestNode`/`Strategy` consumer today.
- `OrderFlowImbalance`: dashboard (`metrics_computer.py`/`chart_data.py`) + real backtest `Strategy` (`ofi_strategy.py` via `backtest_ofi.py`) — no notebook demo today. **This is the indicator to use for Task 2's consistency test** — it's the only one with a genuine second distinct consuming context to compare against.
- `OnlineLogisticTrend`: only `example_strategy.py`/`backtest_dydx.py` (backtest). No notebook, no dashboard/live consumption. Its `publish_signal(...)` output is also not wired into `dashboard.record()` anywhere yet — these are two currently-disconnected mechanisms (relevant context for Epic 3's live Dummy Strategy, not something this story needs to fix).
- `MultiLevelOBI`/`MultiLevelOFI`: only `dashboard.py`'s `_ingest_batch()` (live monitor). No notebook, no backtest strategy use anywhere.

**AC1's literal wording is conditional, not a coverage mandate — read it carefully.** "Given the existing indicators... **when** they are used in [notebook/backtest/live]... **then** the same class/import is used" is a consistency rule that applies whenever/wherever an indicator is actually used — it is not asserting every indicator must already be demonstrated in all three contexts today (the epics.md text itself says the live Dummy Strategy context only exists "once built in Epic 3"). Do not misread this as a mandate to add new notebook/backtest consumption for `OnlineLogisticTrend`/`MultiLevelOBI`/`MultiLevelOFI` — that would be scope creep. Task 3 just documents the current state honestly.

**Real subtlety found during research (not obvious from reading either file in isolation) — read Task 2's second subtask carefully before writing the test.** `ofi_strategy.py`'s `on_order_book_deltas` updates the indicator once per received `OrderBookDeltas` *batch* (after applying every delta in that batch to its book), while `book_features.top_of_book_series` yields a value once per *individual* delta. A multi-delta test batch would make the two paths call `update_raw()` a different number of times — use single-delta-per-batch synthetic data to sidestep this cleanly.

**Precision rule (AD-5) — not applicable.** This story only threads existing float-based indicator values (rank/OFI/microprice already established as plain floats in prior stories); it constructs no new `Price`/`Quantity`.

**Architecture grounding:** AD-4 (module boundary) as literally written binds the `dydx_collector` ↔ `ml_signals` boundary specifically, not the indicator-reuse rule per se — the epics.md AC4 cites it by analogy. The more precise citation for "one indicator, reused everywhere" is FR10 (PRD §4.3) plus `troll/CLAUDE.md`'s `SIGNAL-01` rule ("If a value can be derived exactly from stored level data, do not store it. Store raw inputs; compute signals" — naming `ml_signals/indicators.py` as the single place `microprice`/`ofi_N`/`obi_N` are computed). Cite both in any references, but don't over-rely on AD-4 alone as if it were the primary rule.

### Project Structure Notes

- **In scope:** new file `troll/ml_signals/tests/test_indicator_consistency.py` (Task 2). Possibly a one-line docstring/comment addition to `ml_signals/indicators.py` or the new test file (Task 3) — not a functional change.
- **Explicitly out of scope, do not touch:** `ml_signals/indicators.py`'s indicator classes themselves (no changes needed — audit confirmed all correct), `ofi_strategy.py`, `example_strategy.py`, `metrics_computer.py`, `chart_data.py`, `dashboard.py`, `backtest_dydx.py`, `backtest_ofi.py` (all confirmed compliant already — this story verifies, does not modify, these files), the Story 2.1 notebook.
- No new third-party dependencies.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.2] original story definition (Given/When/Then ACs), lines 305-327
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2] Epic 2 intro, line 279
- [Source: _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md#FR-10] "Single indicator implementation, three consumption contexts" — the primary rule this story verifies/tests
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-4] Module boundary — cited by analogy in epics.md's AC4; binds `dydx_collector`↔`ml_signals` specifically
- [Source: troll/CLAUDE.md#Signal-Architecture] `SIGNAL-01` — the more precise "compute signals once, in `ml_signals/indicators.py`" rule
- [Source: troll/ml_signals/indicators.py] all 5 indicator classes — confirmed correct, no changes needed
- [Source: troll/ml_signals/ofi_strategy.py:217-256] `on_order_book_deltas` — the real `Strategy`-context OFI-feeding pattern Task 2's test must mirror, including the per-batch (not per-delta) update timing
- [Source: troll/ml_signals/tests/test_ofi_strategy.py] `_engine()`/`_delta()`/`_batch()` helpers — the `BacktestEngine` harness pattern to reuse for Task 2, not reinvent
- [Source: troll/ml_signals/book_features.py:48-75] `top_of_book_series` — the direct-replay path's per-delta feeding pattern
- [Source: troll/ml_signals/metrics_computer.py:64-90] `_book_metrics` — direct-replay consumer precedent (dashboard context)
- [Source: troll/ml_signals/chart_data.py] direct-replay consumer precedent (dashboard chart context); also where the unrelated-but-similarly-named `ExponentialMovingAverage`-based `trend_fast`/`trend_slow` lives — do not confuse with `OnlineLogisticTrend`
- [Source: troll/ml_signals/backtest_dydx.py, troll/ml_signals/backtest_ofi.py] confirmed both correctly reference `example_strategy.py`/`ofi_strategy.py` respectively — no dangling references
- [Source: troll/ml_signals/tests/test_indicators.py] existing per-indicator isolated-behavior tests — confirmed no cross-context test exists here; Task 2 is a new, separate file, not an addition to this one
- [Source: _bmad-output/implementation-artifacts/2-1-bring-the-jupyter-research-environment-in-line-with-nautilus-conventions.md] Story 2.1 — confirms the notebook demonstrates `Microprice` via `top_of_book_series`, the same direct-replay pattern this story's Task 2 test uses for `OrderFlowImbalance`
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-07-16.md] Epic 1 retrospective — two standing process action items apply: (1) lifecycle-cleanup checklist (not directly applicable — no subscribe/unsubscribe lifecycle in this story's scope) and (2) integration-test convention for dual-path handlers (directly applicable — this story's entire point is adding exactly that kind of test for `OrderFlowImbalance`'s two consuming paths)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- Fresh `grep` re-audit of every `from ml_signals.indicators import ...` site across `troll/ml_signals/*.py` and the Story 2.1 notebook, plus `strategy_path`/`config_path` resolution checks for `backtest_dydx.py`/`backtest_ofi.py` — all clean, matching story-creation-time research.
- `docker compose run --rm --no-deps -v troll/ml_signals:/app/ml_signals collector python3 -m pytest ml_signals/tests/test_ofi_strategy_indicator_consistency.py -v` → both tests passed (final-state consistency, uninitialized-after-one-update).
- **Crash investigation** (see Completion Notes / `deferred-work.md`): `docker compose run ... pytest ml_signals/tests -q` initially crashed with `Fatal Python error: Aborted` inside `test_ofi_strategy.py`'s own 2nd `BacktestEngine` construction. Isolated via: (1) running `test_ofi_strategy.py` alone → only the known pre-existing assertion failure, no crash; (2) running the new file + `test_ofi_strategy.py` together → reproduced; (3) reversing file order → no crash; (4) a byte-for-byte copy of `test_ofi_strategy.py`'s own first test, run from a separate module → still crashes, proving it's not specific to this story's test content. Fix applied: renamed the test file to collect after `test_ofi_strategy.py`.
- `make build-insecure` (rebuilt `troll-collector`/`troll-dashboard` from current code) → built clean.
- `docker compose run --rm --no-deps collector python3 -m pytest ml_signals/tests dydx_collector/tests -q` → **176 passed** (1 more than Story 2.1's baseline of 175, from this story's new test), 1 failed (`test_ofi_strategy.py`, same pre-existing/documented/unrelated failure), 30 errors (same pre-existing `dydx_collector/tests` container-environment issue, untouched by this story). No crash.

**Code review follow-up (2026-07-16):** 9 patch findings applied. Most significant: a factual error in my own docstring (claimed `OrderFlowImbalance` had a notebook consumer that doesn't exist — only `Microprice` does, caught independently by two reviewers), a missing `try`/`finally` around engine cleanup, an overclaimed equivalence guarantee (crossed-book handling genuinely differs between the two consuming paths — narrowed the claim and logged the asymmetry separately), and a test-coverage gap (only size changes were exercised, never a genuine price move — added one to each side). Re-ran `make build-insecure` + full suite after fixes: **176 passed**, same 1 pre-existing failure, same 30 pre-existing errors, no crash.

### Completion Notes List

- **Task 1** — Re-audit confirmed the original story-creation-time research still holds exactly: zero duplicate indicator implementations anywhere, all strategy/backtest cross-references resolve correctly. No code changes needed.
- **Task 2** — Added `ml_signals/tests/test_ofi_strategy_indicator_consistency.py`, proving `OrderFlowImbalance` reaches identical final state whether fed via the direct-replay path (`top_of_book_series`) or a real `BacktestEngine`-run `OFIStrategy`. **Real infrastructure bug found during implementation, not anticipated in the story:** constructing a second `BacktestEngine`-based test file that collects alphabetically before `test_ofi_strategy.py` causes a fatal native abort in `test_ofi_strategy.py`'s own subsequent engine construction — confirmed via a byte-identical reproduction that this is an environmental fragility (pinned `nautilus_trader` version's kernel/logging init across module boundaries), not a defect in this story's test logic. Mitigated by naming the file to collect after `test_ofi_strategy.py`; logged as an unresolved, real finding in `deferred-work.md` for future test-suite growth.
- **Task 3** — Added the per-indicator context-coverage note to `ml_signals/indicators.py`'s module docstring, documenting today's real reuse breadth honestly without implying AC1 requires closing those gaps.
- No new third-party dependencies. No changes to any indicator's implementation, any strategy file, or any backtest script — confirmed unnecessary by the audit.

### File List

- `troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py` — new file: cross-context consistency test for `OrderFlowImbalance` (AC3); revised during review (try/finally, price-move branch coverage, docstring/comment corrections, `__main__` block)
- `troll/ml_signals/indicators.py` — module docstring only: added per-indicator context-coverage note (Task 3), corrected during review; no class/logic changes
- `troll/ml_signals/tests/test_ofi_strategy.py` — module docstring only: added a one-line protective comment about the `BacktestEngine` cross-module ordering constraint (review follow-up); no functional changes
- `_bmad-output/implementation-artifacts/deferred-work.md` — documented the `BacktestEngine` crash finding and its mitigation-robustness caveat (review follow-up)
