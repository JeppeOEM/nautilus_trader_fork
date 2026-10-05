---
title: 'DW bundle: research test and path fixes (DW-35, DW-52, DW-200, DW-248, DW-262)'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: 'e6d268dc6de843fa1e245c4a2f4d7f8b0a7cb559'
baseline_revision: 'ac4a6206a3e338fc2e9be74394b2ad73988a6dbb'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Five small defects in `platform/research/`: `fetch_watchlist` leaks raw `URLError`/`JSONDecodeError`/`KeyError` (DW-35); `test_snapshot_strategy.py` lost its negative-case test to a since-root-caused native abort (DW-52); `backtest_dydx.run`/`backtest_snapshot.run` default to the cwd-relative `"platform/data/catalog"`, which breaks under the documented `cd platform` (DW-200); `OFIStrategy.on_data` evaluates its stale pre-gap OFI on the first row after a gap (DW-248); `test_backtest_runner.py::_stepped_snapshot` calls the pre-30.2 `DydxSecondSnapshot` constructor, so 5 parametrizations error (DW-262).

**Approach:** One focused fix per item: a dedicated `WatchlistUnavailableError` naming the URL and cause; restore the "no trade below threshold" test; one shared cwd-independent default catalog resolver (`CATALOG_PATH` env, else `__file__`-anchored `platform/data/catalog`) used by all four `research/strategies/backtest_*.py` runners; skip `_evaluate` on a post-gap baseline row; build `_stepped_snapshot` through `kernel.tests.snapshot_factory.make_snapshot` at the instrument's precision.

## Boundaries & Constraints

**Always:** Real Nautilus types in tests, no mocks of Nautilus internals; pytest functions returning `-> None`; LGPL header on new files; ruff line length 100, single-line imports; update in-code docstrings/`Known limit:` text and the paired notebook prose that describe the old OFI gap behaviour so nothing documents a fixed bug as live.

**Block If:** Fixing DW-248 requires changing `kernel.indicators.MultiLevelOFI` semantics (the fix must stay in the strategy).

**Never:** Do not edit `_bmad-output/implementation-artifacts/deferred-work.md`. Do not modify `nautilus_trader/` or `crates/`. Do not narrow `backtest_dydx.run`'s existing `except Exception` watchlist wrapper (its test pins it). Do not change `MultiLevelOFI` or the `microstructure.ofi_readings` replay arithmetic.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Watchlist happy | `/api/rankings` returns `{"items":[{"instrument_id":...}]}` | list of ids (unchanged) | none |
| data_api down / 503 / timeout | `urlopen` raises `URLError`/`HTTPError`/`TimeoutError` | `WatchlistUnavailableError` (a `RuntimeError`) whose message names the URL | chained `from` the original |
| Bad body | body not JSON, or JSON without a list `items` | `WatchlistUnavailableError` naming the URL | chained where a cause exists |
| Catalog default, env set | `CATALOG_PATH=/app/catalog` | runners use `/app/catalog` | none |
| Catalog default, env unset | any cwd | `<platform>/data/catalog` resolved from the package file | none |
| OFI gap | z-score above threshold before a gap > `OFI_GAP_NS`, warm-up completes on the post-gap row | no order on that row | none |

</intent-contract>

## Code Map

- `platform/research/watchlist.py` -- `fetch_watchlist`, DW-35 target.
- `platform/research/tests/test_watchlist.py` -- its tests (`_FakeResponse`, monkeypatched `urllib.request.urlopen`).
- `platform/research/tests/test_snapshot_strategy.py` -- single positive test; docstring still claims one-engine-per-file limit; `research/tests/conftest.py::_keep_nautilus_log_guard_alive` already fixed the abort.
- `platform/research/strategies/backtest_{dydx,snapshot,ofi,candle_pattern}.py` -- the four runners; ofi/candle_pattern hold duplicate `_CATALOG = parents[2]/data/catalog`, dydx/snapshot hold the cwd-relative string.
- `platform/research/notebooks/_params.py` -- precedent: `CATALOG_PATH` env else `DATA_DIR / "catalog"`.
- `platform/research/strategies/ofi_strategy.py` -- `OFIStrategy.on_data`, DW-248.
- `platform/research/application/microstructure.py:300-320` -- `ofi_readings` docstring `Known limit` describing DW-248.
- `platform/research/notebooks/02_microstructure.{py,ipynb}` -- markdown cell (~line 178) describing DW-248 as a known limit.
- `platform/research/tests/test_ofi_strategy.py` -- engine-based OFIStrategy tests (`_snapshot`, `_trade`, `_engine`).
- `platform/research/tests/test_backtest_runner.py:424-430` -- `_stepped_snapshot`, DW-262; instrument precision 1/3.
- `platform/research/README.md:286`, `platform/docs/BOT_OPERATIONS.md:255` -- document the default catalog path.

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/watchlist.py` -- add `WatchlistUnavailableError(RuntimeError)`; wrap fetch+parse catching `OSError`/`ValueError`, validate the payload is a dict with a list `items`, raise with a message naming the URL and suggesting data_api/ranking is not up -- DW-35.
- [x] `platform/research/tests/test_watchlist.py` -- add tests: unreachable (`URLError`), HTTP 503 (`HTTPError`), non-JSON body, payload without `items`; each asserts `WatchlistUnavailableError`, URL in message, and (where applicable) the cause type.
- [x] `platform/research/tests/test_snapshot_strategy.py` -- restore `test_snapshot_strategy_no_trade_below_threshold` (unreachable thresholds, same data, zero fills); share engine/run setup via a helper; replace the "only ONE test function" docstring paragraph with the conftest explanation; update `__main__` -- DW-52.
- [x] `platform/research/strategies/catalog_location.py` (new) -- `default_catalog_path() -> str`: non-empty `CATALOG_PATH` env, else `Path(__file__).resolve().parents[2] / "data" / "catalog"`; resolved at call time -- DW-200.
- [x] `platform/research/strategies/backtest_{dydx,snapshot,ofi,candle_pattern}.py` -- `catalog_path: str | None = None`, resolved via `default_catalog_path()` when None; delete the duplicated `_CATALOG` constants.
- [x] `platform/research/tests/test_catalog_location.py` (new) -- env set, env unset/empty -> absolute `platform/data/catalog` independent of `monkeypatch.chdir`.
- [x] `platform/research/README.md`, `platform/docs/BOT_OPERATIONS.md` -- state the default (`$CATALOG_PATH`, else `platform/data/catalog`, cwd-independent).
- [x] `platform/research/strategies/ofi_strategy.py` -- in `on_data`, a row that cleared prev state (gap) is a baseline row: skip `_evaluate` on it -- DW-248.
- [x] `platform/research/tests/test_ofi_strategy.py` -- add a gap test: readings push the z-score above threshold, then a gap > `OFI_GAP_NS`, warm-up ending exactly on the post-gap row with a trade after it; assert zero fills. Must fail on the pre-fix strategy.
- [x] `platform/research/application/microstructure.py`, `platform/research/notebooks/02_microstructure.py` + `.ipynb` -- drop the "strategy still evaluates pre-gap value" known-limit text; say the strategy and replay both skip that row.
- [x] `platform/research/tests/test_backtest_runner.py` -- `_stepped_snapshot` via `make_snapshot(..., price_precision=1, size_precision=3)` -- DW-262.

**Acceptance Criteria:**
- Given the research suite, when run with `python3 -m pytest -o addopts="" --rootdir=. research/tests`, then 0 errors and 0 failures (baseline: 564 passed, 3 skipped, 5 errors).
- Given cwd `platform/` and no `CATALOG_PATH`, when `backtest_snapshot.run()`/`backtest_dydx.run()` resolve their default, then it is `<repo>/platform/data/catalog`, never `platform/platform/data/catalog`.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8 (high 0, medium 2, low 6)
- defer: 1 (high 0, medium 0, low 1)
- reject: 8 (high 0, medium 0, low 8)
- addressed_findings:
  - `[medium]` `[patch]` `SnapshotStrategy.on_data` had the same DW-248 stale post-gap evaluation; now returns on the post-gap row, red/green engine test added.
  - `[medium]` `[patch]` The OFIStrategy gap gate also blocked the OFI-independent trend-flip exit; `_evaluate(fresh_ofi=...)` now skips only entries and the zero-cross exit, red/green engine test added; `_prev_ofi` comment reworded as a deliberate choice.
  - `[low]` `[patch]` `fetch_watchlist` let `http.client.HTTPException` (IncompleteRead) and a scheme-less URL's `ValueError` from `Request` escape; both now become `WatchlistUnavailableError`, tests added.
  - `[low]` `[patch]` Runner-default test only checked the signature; now proves each of the four runners passes `default_catalog_path()`'s result to its first collaborator.
  - `[low]` `[patch]` `backtest_snapshot.run` raised a bare `IndexError` for a symbol missing from the catalog; now a `ValueError` naming symbol and catalog path, test added.
  - `[low]` `[patch]` Restored snapshot no-trade test used unreachable ±999 999 thresholds; now sits just beyond the data's replayed MultiLevelOFI extremes.
  - `[low]` `[patch]` Gap test's `assert` precondition replaced by deriving the gap row from `OFI_GAP_NS`.
  - `[low]` `[patch]` Docs and `catalog_location` docstring now say only the unset-`CATALOG_PATH` fallback is cwd-independent.

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5 (high 0, medium 1, low 4)
- defer: 0
- reject: 13 (high 0, medium 0, low 13)
- addressed_findings:
  - `[medium]` `[patch]` The OFIStrategy post-gap test asserted only zero fills, with no proof the same data could ever fill. Added a positive control: same data and config with `OFI_GAP_NS` raised past the hole enters. The last row's bid step became 10 so its fresh reading is a +1 z-score (it was a z-score of 0). Red against the pre-fix strategy, green now.
  - `[low]` `[patch]` The shared `snapshot_backtest.run` (behind `backtest_ofi`) still raised a bare `IndexError` for a symbol missing from the catalog. It now raises the same `ValueError` naming symbol and catalog as `backtest_snapshot`. The missing-symbol test is parametrized over both runners.
  - `[low]` `[patch]` The snapshot no-trade test's `assert highest + eps > lowest - eps` was a tautology. It now asserts the fixture's readings are not constant.
  - `[low]` `[patch]` The trend-flip fixture now asserts its minute-1 row lands inside minute 1, which needs `OFI_GAP_NS` < 58 s.
  - `[low]` `[patch]` `test_ofi_strategy.py`'s `__main__` now holds a logging-guard engine outside pytest, as `test_snapshot_strategy.py` does.

## Design Notes

DW-248: on the gap row `MultiLevelOFI.update_raw` only sets the baseline and leaves `value` untouched, so `_prev_ofi = ofi` is a no-op there; keeping `_prev_ofi` as the last real reading matches `MultiLevelOFI`, which keeps its contribution/z-score history across a gap. Only evaluation is skipped.

DW-200: env first mirrors `notebooks/_params.py` and lets the images address their `/app/catalog` mount (where `parents[2]` is `/app`, giving a non-existent `/app/data/catalog`).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests -q -p no:cacheprovider` -- expected: no errors/failures.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. tests kernel/tests -q -p no:cacheprovider` -- expected: unchanged from baseline.

## Auto Run Result

**Summary:** A follow-up review pass over the done DW-35/52/200/248/262 bundle (baseline `ac4a6206a3`..`171c998287`). Five review patches, all in tests or error reporting. No change to strategy decisions.

**Files changed (this pass):**
- `platform/research/strategies/snapshot_backtest.py`: a missing symbol raises a `ValueError` naming the symbol and catalog, not a bare `IndexError`.
- `platform/research/tests/test_catalog_location.py`: the missing-symbol test is parametrized over `backtest_snapshot` and `backtest_ofi` (shared runner).
- `platform/research/tests/test_ofi_strategy.py`: positive control for the post-gap test, fixture tweak, fixture precondition, logging guard in `__main__`.
- `platform/research/tests/test_snapshot_strategy.py`: the tautological fixture assert is replaced.

**Review:** 5 patches applied, 0 deferred, 13 rejected. The rejects were: spec-mandated items (`except Exception` in `backtest_dydx`, no `MultiLevelOFI` change, the module location, `_prev_ofi` carried across the gap); the watchlist item-filter finding, already deferred by the first pass; and low-consequence edge cases (empty/whitespace/`~` `CATALOG_PATH`, explicit `catalog_path=""`, a missing fallback directory, out-of-order timestamps). SnapshotStrategy skipping exits on the post-gap row is also rejected: its exits read the stale OFI value.

**Verification:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests -q`: 595 passed, 3 skipped. That is 593 from the prior pass plus the 2 new parametrizations/tests.
- `tests kernel/tests`: 746 passed, 3 failed. These are the same baseline failures: `test_legacy_names` x2 and `test_notebook_rules::test_the_sweep_without_git_sees_what_git_sees`.
- The post-gap test fails against the baseline `ofi_strategy.py` and passes on the fixed one. The new control passes.
- `python3 -m research.tests.test_ofi_strategy` and `python3 -m research.tests.test_snapshot_strategy` (script mode) both print `ok`.
- `ruff check`/`format --check` are clean on the touched files. `mypy` on `snapshot_backtest.py` shows the same 3 errors as before this pass.

**Residual risks:** The prior pass's residual risks still stand: the notebook pairing test is skipped here, and post-gap decisions change backtest results over gappy windows.
