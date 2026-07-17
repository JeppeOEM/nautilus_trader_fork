---
baseline_commit: 53041f3d086d28502287ccf7fb9d8101e7130450
---

# Story 2.1: Bring the Jupyter research environment in line with Nautilus conventions

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a researcher,
I want the research notebook workflow to follow Nautilus's own example research-notebook conventions and respect the catalog's memory-bounded access rules,
so that I can develop indicators against real catalog data without violating the project's data-access discipline or reinventing tooling.

## Acceptance Criteria

1. **No unbounded catalog reads (NFR3, FR9).** Given the existing `dydx_collector/notebooks/dydx_catalog_pandas.ipynb` notebook, when its catalog reads are reviewed, then any `catalog.trade_ticks()`/similar call with no `start`/`end` bound is replaced with a time-bounded query, consistent with NFR3 and AD-6.
2. **Follows Nautilus's own example research-notebook conventions (FR9).** Given Nautilus's own shipped example research notebooks, when the dYdX research notebook's structure is compared against them, then it follows the same conventions (catalog access pattern, no custom notebook framework) rather than ad-hoc pandas-only exploration.
3. **Indicators imported, never redefined inline (FR9, AD-4).** Given a new indicator authored in the research notebook, when it is imported, then it is imported from `ml_signals.indicators` (or equivalent shared module) — never redefined inline in the notebook.
4. **Runs against a multi-week catalog without unbounded memory load (NFR3).** Given the notebook, when it runs against a multi-week catalog, then it completes without loading the full unbounded catalog into memory — this validates NFR3 specifically in the research context, since it differs from the streaming `BacktestDataConfig` path used by `backtest_dydx.py`.

## Tasks / Subtasks

- [x] Task 1 — Fix the one real NFR3 violation: bound the unbounded `trade_ticks()` call (AC: #1, #4)
  - [x] **Read `troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb` in full before editing.** Confirmed exactly 5 cells (1 markdown, 4 code), violation isolated to cell 4 as expected, no `quote_ticks()`/`bars()`/`order_book_deltas()` calls elsewhere.
  - [x] Added `LOOKBACK_HOURS = 24` constant + `start_ns = time.time_ns() - LOOKBACK_HOURS * 3_600 * 1_000_000_000`, passed as `catalog.trade_ticks(instrument_ids=[instrument_id], start=start_ns)`. Used this codebase's plain-int-ns idiom, not `dt_to_unix_nanos`.
  - [x] No `end` bound added — open-ended `start`-only window, matching `catalog_stats.py`/`metrics_computer.py` precedent.

- [x] Task 2 — Align notebook structure with Nautilus's own example research-notebook conventions (AC: #2)
  - [x] Read `docs/how_to/loading_external_data.py` and `docs/tutorials/backtest_orderbook_binance.py` in full — confirmed the bounded `start=`/`end=` catalog-read convention and the `ImportableStrategyConfig` string-path-reference convention (no inline strategy/indicator redefinition).
  - [x] **Decision made and recorded:** kept the file as raw `.ipynb` rather than converting to jupytext percent-format — adopted the *structural* conventions (bounded reads, explicit narrative markdown about the convention) instead. Converting file format is a materially bigger, separately-decidable change than what this story's ACs require ("conventions... rather than ad-hoc pandas-only exploration" — access pattern and structure, not file format specifically). Flagged here rather than silently guessed; revisit as a separate story if the jupytext format is wanted later.
  - [x] Extended the existing intro markdown cell (cell 0) with an explicit note that catalog reads are always time-bounded and why (NFR3/AD-6) — kept and extended rather than rewritten. Added a second markdown cell (before the new indicator-demo cell) explaining the import-never-redefine convention.

- [x] Task 3 — Establish (or confirm) the "import indicators, never redefine inline" convention (AC: #3)
  - [x] Confirmed `ml_signals/indicators.py` needs no changes — all 5 indicators already correct, importable as-is.
  - [x] Added a new markdown + code cell pair demonstrating the pattern. **Correction made during implementation:** the story's own suggestion of feeding `Microprice`/`OrderFlowImbalance` via `handle_quote_tick(...)` from `catalog.quote_ticks()` would have silently returned zero rows — this collector never writes `QuoteTick` data to the catalog at all (confirmed via `grep` on `collector.py`; per `troll/CLAUDE.md`'s 1s-snapshot signal architecture, microprice/spread are derived from order-book top-of-book state, not quote ticks). Used the actual real, tested, production pattern instead: `catalog.order_book_deltas(..., start=start_ns)` replayed through `ml_signals.book_features.top_of_book_series` (a pure, AD-4-compliant utility already used by `metrics_computer.py`) feeding `Microprice.update_raw(...)` — this is exactly what `metrics_computer._book_metrics` does in production.
  - [x] The new bounded `order_book_deltas()` read reuses the same `start_ns` from Task 1 — no second unbounded read, no new catalog-read pattern introduced.

- [x] Task 4 — Tests / verification (AC: #1, #4)
  - [x] Inspected the final notebook: every catalog time-series read (`trade_ticks`, `order_book_deltas`) carries an explicit `start=` kwarg; no unbounded call remains.
  - [x] **Went beyond "recommended" — actually executed, real environment, no mocks.** Built a synthetic-but-real `ParquetDataCatalog` (real `CryptoPerpetual` instrument, real `TradeTick`/`OrderBookDelta`/`BookOrder` objects, same construction pattern as `test_book_features.py`'s `_delta()` helper) with one out-of-lookback-window trade/delta deliberately included. Ran the **actual notebook file's code cells verbatim** (parsed the real `.ipynb` JSON, `exec()`'d each code cell in order, cwd set to the notebook's own directory so its relative paths resolved exactly as in a real Jupyter session) via Docker (`docker compose run --rm --no-deps collector python3 ...`, `PYTHONPATH=/app`). Confirmed: (a) the bounded `trade_ticks()` call returned 3/4 rows, correctly excluding the deliberately-old trade; (b) the bounded `order_book_deltas()` + `top_of_book_series` + `Microprice` pipeline executed without error and produced a correct, positive size-weighted microprice value. Only the final `.plot(...)` calls were skipped (no `matplotlib` in this production runtime image — expected, plotting isn't part of the AC logic). Synthetic catalog data was written to and cleaned up from the real (gitignored, normally-empty) `troll/dydx_collector/catalog/` directory, then verified restored to its original empty state.
  - [x] Did not invent a new notebook-testing framework/CI wiring — the verification script used for this story is a one-off, not committed as a permanent test file, per the task's own instruction.

### Review Findings

- [x] [Review][Patch] Reused the 24h trade-tick `start_ns` window for `catalog.order_book_deltas()`, which is the wrong scale for raw book-delta replay — this codebase's own established precedent for this exact call is far tighter: `metrics_computer.py`'s `OFI_LOOKBACK_SECONDS = 60` (1 minute) and `chart_data.py`'s documented 4h default, both citing that a full day of raw deltas is slow/heavy to replay. Fixed: gave the deltas query its own `DELTA_LOOKBACK_HOURS = 4` window, matching `chart_data.py`'s default, instead of reusing the trade-tick lookback. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Patch] `instrument_id = instrument_ids[0]` (arbitrary first instrument) is very likely NOT in the collector's opt-in raw-delta-capture set — confirmed via `dydx_collector/config.py:27` (`store_order_book_deltas: bool = False` by default) and `collector.py:274,278`. Fixed: the cell now tries each instrument until one actually has deltas in the window, prints a clear message if none do, and the markdown now names the real precondition (the opt-in gate) alongside the QuoteTick point. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Patch] No `.initialized` check before recording `microprice.value` in the replay loop — `Microprice.update_raw()` is a no-op when `bid_size + ask_size == 0`, leaving `.value` at its prior/default value; the loop appended it unconditionally. Fixed: only appends when `microprice.initialized`. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Patch] No guard against an empty `trades_df` — `pd.DataFrame([])` has no columns and `trades_df["price"]` would raise `KeyError`. Fixed: `trades_df` is now always constructed (possibly empty), a clear message prints when empty, and the plot cell checks `if not trades_df.empty:` before indexing. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Patch] No handling of order-book gap/resync discontinuities across the replay window, unlike `metrics_computer.py`'s `_BOOK_GAP_NS`-based reset. Fixed: added the same `_GAP_NS = 3_000_000_000` reset pattern — `microprice.reset()` when the gap between consecutive top-of-book updates exceeds it. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Patch] `sys.path.insert(0, "../..")` had no guard against duplicate insertion on cell re-run. Fixed: guarded with `if "../.." not in sys.path:`. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Patch] Per-instrument `retain_hours` pruning can make the effective coverage of the nominal 24h `LOOKBACK_HOURS` window shorter than advertised. Fixed: added a one-line comment acknowledging the limitation. [troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb]
- [x] [Review][Dismiss] "No evidence the notebook was actually executed" (`execution_count: null`, empty `outputs`) — confirmed this matches the pre-existing state of every cell in the file before this diff (checked via `git show <baseline_commit>`); not a regression introduced by this story. Real verification was performed via other means and is documented in the Debug Log above.
- [x] [Review][Dismiss] "Overstated always-time-bounded claim" (no `end=` bound) — consistent with this story's own explicit, documented decision not to add an `end` bound (Task 1's third subtask); an open `start`-only window is a valid bounded query, not a defect.
- [x] [Review][Dismiss] "Unsubstantiated AD-4-compliant assertion" in the markdown — the claim is correct and already substantiated via this story's own Dev Notes/References; not something the notebook cell itself needs to re-derive.

## Dev Notes

**This is a narrow, surgical fix — one real bug (the unbounded `trade_ticks()` call) plus a structural/convention alignment, not a notebook rewrite.** The notebook is 5 cells total. Do not add unrelated cells, do not restructure cells that already work correctly (instrument listing, instrument-dict display), and do not touch `troll/ml_signals/indicators.py`, `backtest_dydx.py`, or any other file — this story's `Project Structure Notes` below list the only files in scope.

**Exact violation, already isolated:** cell 4's `catalog.trade_ticks(instrument_ids=[instrument_id])` has zero `start`/`end` bounds. This is the sole and complete NFR3 violation in the current notebook — there is no `quote_ticks()`, `bars()`, or `order_book_deltas()` call anywhere in it.

**Codebase's own established idiom for bounded catalog reads (follow this, not upstream's `dt_to_unix_nanos`/`pd.Timestamp` form, for internal consistency) — these are direct precedents already in `troll/ml_signals/`:**
- `catalog_stats.py:174`: `catalog.trade_ticks(instrument_ids=[instrument_id], start=start_ns)` (plain int ns)
- `metrics_computer.py:64-66`: `start_ns = now_ns - OFI_LOOKBACK_SECONDS * 1_000_000_000; catalog.order_book_deltas(instrument_ids=[instrument_id], start=start_ns)`
- `dashboard.py:715-722` (`_historical_candles_json`): bounds both `start_ns` and `end_ns` from millisecond inputs before calling `catalog.trade_ticks(instrument_ids=[iid], start=start_ns, end=end_ns)`

All of these compute a plain-integer nanosecond epoch and pass it as `start=`(optionally `end=`) — `catalog.trade_ticks`/`order_book_deltas`/`bars`/`quote_ticks` (defined in `nautilus_trader/persistence/catalog/base.py:169-200`) accept either form (`TimestampLike | None`), so both are technically valid; this codebase's convention is the plain-int form specifically. Use it for consistency rather than importing `dt_to_unix_nanos`.

**What upstream's own "shipped example research notebooks" actually are, and where they live:** there is no second real research `.ipynb` in this repo (only `examples/other/debugging/debug_mixed_jupyter.ipynb`, a Rust/Python mixed-debugging demo — not a research/tutorial pattern, and its own single unbounded `catalog.quote_ticks()` read is fine there only because it's a throwaway single-record temp catalog, not a pattern to imitate). The actual convention-setting material is `docs/how_to/loading_external_data.py`, `docs/how_to/data_catalog_databento.py`, and `docs/tutorials/backtest_orderbook_binance.py`/`backtest_orderbook_bybit.py` — all written as **jupytext percent-format `.py` files** (`# %%` cell markers), not raw `.ipynb`. Every time-series catalog read in these examples passes explicit `start`/`end` via `dt_to_unix_nanos(pd.Timestamp(...))`. None of them define strategy/indicator logic inline for reuse — they reference strategies via `ImportableStrategyConfig(strategy_path="...")`, i.e. import-by-string-path from a real module, never redefined in a notebook cell. This is the direct precedent behind AC #3.

**Real open decision, not a silent call for the dev agent to make alone:** should `dydx_catalog_pandas.ipynb` be converted to the jupytext percent-format `.py` convention (matching upstream exactly, diffable/source-controllable), or should it stay a raw `.ipynb` and only adopt the *structural* conventions (bounded reads, narrative markdown structure)? Both satisfy AC #2's literal wording ("follows the same conventions... rather than ad-hoc pandas-only exploration"), but they're materially different amounts of work and change the file's identity/path. Flag this to the user during implementation rather than guessing; if no response, the lower-risk default is: **keep it as `.ipynb`, adopt the structural/bounded-read conventions only** — since converting format is a bigger, separately-decidable change and the AC text emphasizes "conventions" (access pattern, no custom framework) over file format specifically.

**Why `indicators.py` needs zero changes:** all 5 existing indicators (`OnlineLogisticTrend`, `Microprice`, `OrderFlowImbalance`, `MultiLevelOBI`, `MultiLevelOFI`) already correctly subclass `nautilus_trader.indicators.Indicator`, use `PyCondition` validation, and call `_set_has_inputs(True)`/`_set_initialized(True)` per the base-class contract — they're already directly importable/usable from a notebook exactly as `metrics_computer.py` and `chart_data.py` already consume them. This story only needs to *demonstrate* importing one, not build or fix any indicator.

**No test infrastructure exists for notebooks in this repo** (no `nbclient`/`nbconvert`/`papermill` anywhere) — do not invent CI-level notebook execution testing as part of this story; verify AC #1/#4 by inspection (grep for catalog read calls, confirm all carry `start=`/`end=`) per Task 4.

**Architecture/PRD grounding:**
- **AD-6 (Catalog access only through the official API):** "`[ADOPTED]`... never an unbounded `catalog.trade_ticks()` call." This story is the one place in the codebase where that rule was still being violated.
- **AD-4 (Module boundary):** notebook must import indicators from `ml_signals.indicators`, never reimplement inline — same rule that already governs `ml_signals`/`dydx_collector` cross-imports, applied to notebook consumers.
- **FR-9 (PRD §4.3):** "develop and test indicators and ML signals in Jupyter against catalog data, following the conventions of Nautilus's own example research notebooks... not a custom notebook framework." Consequence: "A new indicator authored in a research notebook imports the same class/function later used in backtest and live contexts (FR-10) — no notebook-local reimplementation."
- **NFR3:** "Memory-bounded access — no unbounded catalog reads... all data access is time-bounded or streamed via `BacktestDataConfig`."

**Precision rule (AD-5) — not applicable.** This story only reads/plots already-decoded `Price`/`Quantity` values via pandas float conversion for display purposes (matching the existing notebook's own pattern); it constructs no new `Price`/`Quantity` values.

### Project Structure Notes

- **In scope:** `troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb` only (or its jupytext-format replacement, pending the open decision above).
- **Explicitly out of scope, do not touch:** `troll/ml_signals/indicators.py` (already correct, no changes needed), `troll/ml_signals/backtest_dydx.py` (separate story's concern — Story 2.3/2.4 territory, not 2.1), `troll/ml_signals/catalog_stats.py`/`metrics_computer.py`/`dashboard.py` (referenced only as *precedent* for the bounded-query idiom, not files this story modifies).
- No new test file is expected (see Task 4) — if the dev agent judges a lightweight sanity check is warranted, it should be a plain inspection/assertion script, not a new pytest suite invented for notebook execution.
- No new third-party dependencies.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.1] original story definition (Given/When/Then ACs), lines 281-304
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2] Epic 2 intro, line 279 — names the exact two gaps (`trade_ticks()` unbounded; `backtest_dydx.py` single-symbol) found during epic scoping review
- [Source: _bmad-output/planning-artifacts/epics.md#NFR3] line 40 — full NFR3 definition
- [Source: _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md#FR-9] "Jupyter research environment" — full FR text + consequence
- [Source: _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md#FR-10] "Single indicator implementation, three consumption contexts" — the reuse rule this story's Task 3 sets up for
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-4] Module boundary — governs indicator import discipline
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-6] Catalog access only through the official API — the rule this story's Task 1 fixes a real violation of
- [Source: troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb] existing notebook — cell 4 is the exact violation; cells 1-3, 5 are unaffected
- [Source: docs/how_to/loading_external_data.py] upstream jupytext-format example — bounded `catalog.quote_ticks(..., start=start, end=end)` pattern, `ImportableStrategyConfig` string-path reference pattern
- [Source: docs/tutorials/backtest_orderbook_binance.py, docs/tutorials/backtest_orderbook_bybit.py] upstream jupytext-format examples — same bounded-read and string-path-reference conventions
- [Source: troll/ml_signals/catalog_stats.py:174] `catalog.trade_ticks(instrument_ids=[instrument_id], start=start_ns)` — this codebase's own plain-int-ns bounded-read idiom to follow
- [Source: troll/ml_signals/metrics_computer.py:46-48,64-66] `PRICE_LOOKBACK_HOURS`/`OFI_LOOKBACK_SECONDS` bounded-window pattern and its documented rationale
- [Source: troll/ml_signals/dashboard.py:715-722] `_historical_candles_json` — closest direct analogue, bounds both `start_ns` and `end_ns`
- [Source: troll/ml_signals/indicators.py] `Microprice`, `OrderFlowImbalance`, `MultiLevelOBI`, `MultiLevelOFI`, `OnlineLogisticTrend` — existing, unmodified, directly importable indicators for Task 3's demonstration cell
- [Source: troll/CLAUDE.md#Memory-Discipline] MEM-01 — "the anti-pattern is `catalog.trade_ticks()` with no time bounds" (names this exact violation)
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-07-16.md] Epic 1 retrospective — two standing process action items apply to this story: (1) lifecycle-cleanup checklist (not directly applicable here — no subscribe/unsubscribe lifecycle in a notebook) and (2) integration-test convention for dual-path handlers (not directly applicable — no dual code path here, single notebook consumer)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `python3 -c "import json; ...; ast.parse(...)"` on every code cell's source, extracted from the notebook JSON → clean, no syntax errors.
- Synthetic-catalog verification script (`verify_notebook_2_1.py`, one-off, not committed): built a real `ParquetDataCatalog` with a `CryptoPerpetual` instrument, 4 `TradeTick`s (one 48h out of the 24h lookback window), and 3 `OrderBookDelta`s (one out of window) — mirrors `test_book_features.py`'s real-object construction pattern, no mocks. Run via `docker compose run --rm --no-deps -e PYTHONPATH=/app collector python3 ...`. Result: bounded `trade_ticks()` returned 3/4 rows (old trade correctly excluded); bounded `order_book_deltas()` → `top_of_book_series` → `Microprice.update_raw()` pipeline produced 1 correct microprice point (100.3125).
- Follow-up, stronger verification (`run_notebook_cells.py`, one-off, not committed): parsed the actual `.ipynb` file's JSON and `exec()`'d each real code cell in order (not a hand-copied mirror), with cwd set to the notebook's own directory so its `"../catalog"`/`"../.."` relative paths resolved exactly as in a real Jupyter session, against the same synthetic data written to and cleaned up from the real (gitignored) `troll/dydx_collector/catalog/`. All 4 code cells executed correctly; the two cells ending in `.plot(...)` were skipped only because this production runtime image has no `matplotlib` installed (expected — not part of AC logic, and all statements prior to the `.plot()` call in each of those cells, including the `micro_df`/`trades_df` construction, executed and asserted correctly before the plot call raised).
- `git status`/`ls` confirmed `troll/dydx_collector/catalog/` was restored to its original empty state after verification, and that only the notebook file changed within this story's scope.

**Code review follow-up (2026-07-16):** 7 patch findings applied (see Review Findings above). Re-verified with an extended `run_notebook_cells.py` covering two scenarios: (1) normal data including an out-of-window trade, an out-of-window delta, *and* a resync-sized gap (3600s) between two valid top-of-book episodes within the delta window — confirmed `micro_df` correctly produced 2 points (one per side of the gap-reset, via `microprice.reset()`), not one spliced series; (2) an instrument with zero trades and zero deltas — confirmed both new empty-guard paths print a clear message and complete without exceptions (`trades_df.empty`, `deltas == []`, no `micro_df` created). Both scenarios run via Docker against a real `ParquetDataCatalog`, executing the actual notebook file's cells verbatim. `troll/dydx_collector/catalog/` re-confirmed restored to its original empty state after both scenarios.

### Completion Notes List

- **Task 1** — Fixed the one real NFR3/MEM-01 violation: cell 4's unbounded `catalog.trade_ticks()` now takes a 24h trailing `start_ns` bound, following this codebase's own plain-int-nanosecond idiom (not upstream's `dt_to_unix_nanos`).
- **Task 2** — Read upstream's actual jupytext-format research-notebook examples (`docs/how_to/loading_external_data.py`, `docs/tutorials/backtest_orderbook_binance.py`) and confirmed the bounded-read + string-path-reference conventions. **Explicit decision recorded, not silently made:** kept the file as raw `.ipynb` rather than converting to jupytext percent-format — that's a bigger, separately-decidable change than this story's ACs require. Extended (not rewrote) the existing intro markdown cell with an explicit statement of the bounded-query convention, and added a second markdown cell narrating the indicator-reuse convention.
- **Task 3** — **Real correction found during implementation, not anticipated in the story's own Dev Notes:** the story's suggested demo (`Microprice`/`OrderFlowImbalance` fed via `handle_quote_tick()` from `catalog.quote_ticks()`) would have silently produced zero rows — this collector never writes `QuoteTick` data to the catalog at all (confirmed via `grep` on `collector.py`). Used the actual, real, already-production-proven pattern instead: `catalog.order_book_deltas()` replayed through `ml_signals.book_features.top_of_book_series` (pure AD-4-compliant utility) feeding `Microprice.update_raw(...)`, exactly matching `metrics_computer._book_metrics`. No changes to `ml_signals/indicators.py` — confirmed unnecessary.
- **Task 4** — Went beyond the task's "recommended, not required" bar: actually executed the real notebook file's cells (not a mirror script) against a real, synthetically-populated `ParquetDataCatalog`, via Docker, and confirmed correct bounded-query and indicator-pipeline behavior end-to-end. No notebook-testing framework/CI was introduced, per the task's explicit instruction.
- No new third-party dependencies. No changes to `ml_signals/indicators.py`, `backtest_dydx.py`, or any file outside the notebook.
- **Code review follow-up** — fixed a lookback-window scale mismatch (deltas now use their own 4h window instead of reusing the trade-tick 24h window), an instrument-selection gap (deltas cell now tries each instrument rather than assuming `instrument_ids[0]` has opt-in delta capture), a data-integrity gap (skip uninitialized/stale `Microprice` values instead of plotting them), an empty-result crash risk (`trades_df`), a gap/resync-discontinuity gap (mirrors `metrics_computer.py`'s `_BOOK_GAP_NS` reset), a `sys.path` duplicate-insert guard, and a retention-window documentation gap. All re-verified via real Docker execution against synthetic catalog data, including a dedicated gap-reset scenario and a dedicated empty-data scenario.

### File List

- `troll/dydx_collector/notebooks/dydx_catalog_pandas.ipynb` — bounded the previously-unbounded `trade_ticks()` call (Task 1); extended the intro markdown cell with the bounded-query convention (Task 2); added a markdown + code cell pair demonstrating indicator reuse via `ml_signals.indicators.Microprice` + `ml_signals.book_features.top_of_book_series` (Task 3)
