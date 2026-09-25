---
title: 'Story 24.4 — `research/` as a pure consumer, with its broken tests repaired'
type: 'refactor'
created: '2026-09-25'
status: 'done'
baseline_revision: '6a83dc7d7ee44e1633d2dd8df33c6b4c5d708fb8'
final_revision: 'bc614487bf5b09071470dc109b84b36896fbe5e1'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-24-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/24-4-research-pure-consumer-and-broken-tests-repaired.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Backtest strategies, runners, the watchlist client and the notebooks are scattered
across `ml_signals/` and `dydx_collector/notebooks/`. One runner (`snapshot_backtest.py`) reads the
catalog through `ParquetDataCatalog.query`, outside `kernel.catalog_files`/`BacktestDataConfig`. A
research test and a notebook still import `views` (the `(RESEARCH, VIEWS)` legacy edge that expires
with this story). The four OFI strategy tests fail with `TypeError: Unexpected keyword argument
'ma_period'`.

**Approach:** Create `platform/research/` with `strategies/`, `run_backtest.py`, `watchlist.py`,
`notebooks/`, `BACKTESTING.md` and `tests/`, and leave pure re-export shims at every moved
`ml_signals` module. Route the one direct snapshot read through a new column-projected
`kernel.catalog_files.query_top_of_book`. Rewrite the OFI tests against the current
snapshot-driven `OFIStrategy`, keeping every assertion. Then retire the `(RESEARCH, VIEWS)` edge.

## Boundaries & Constraints

**Always:**
- AD-D12 frozen: no Parquet schema, catalog dir, Redis payload, store schema, compose service, env
  var or bind mount changes. Pure move plus wiring.
- Shim idiom exactly as `data_api/alerts.py`: `from <new> import <names>` +
  `warnings.warn(..., DeprecationWarning)` + `REMOVE_AFTER =
  "25-2-ranking-context-rankingboard-replaces-module-globals"`. Nothing is defined in a shim.
  Every in-repo caller is repointed (`tests/test_namespace.py`).
- `ImportableStrategyConfig` string paths become `research.strategies.<module>:<Class>` in code,
  notebooks and docs.
- Every *market-data row* read in research `.py` modules goes through `kernel.catalog_files` or
  `BacktestDataConfig`. The instrument-definition lookup `ParquetDataCatalog.instruments(
  instrument_ids=[...])` is metadata and stays. Throwaway catalogs written by
  `snapshot_backtest` are allowed (spine AD-D1 research row).
- `research` imports only `kernel`, `observability`, stdlib and `nautilus_trader`. It never
  imports `views`, `data_api`, `ranking_engine` or `ml_signals`. `watchlist.py` stays HTTP.
- Tests are repaired, never deleted or loosened. Each test keeps its assertions (same claims, same
  counts). Only the input construction and config field names change to the real API. Each root
  cause goes in the story's Completion Notes (TEST-04).
- Same commit: `platform/CLAUDE.md` NAUT-03, `platform/README.md` backtest section,
  `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, `docs/BOT_OPERATIONS.md`, the frontend docs refs,
  `collector.dockerfile` `COPY`, and the Makefile `test` list.
- Working dir `platform/`. No new `DeprecationWarning` in the test run. Rules: READ-03 (types,
  mypy), TEST-01..04, MEM-01, FORK-01.

**Block If:**
- A moved module cannot be shimmed without defining something in the shim.
- The suite shows failures beyond the pre-existing list, which is: rankings redis ×1 and dydx
  `trade_ohlc` ×5 when present. After this story the `ofi_strategy` ×4 must pass.

**Never:**
- Touch `nautilus_trader/`, `crates/`, or write `sprint-status.yaml`.
- Recompute pct-change or volatility in research. Research has none today; do not add one.
- Add a `(RESEARCH, *)` legacy edge or widen `_exempt`/`GRAPH`.
- Rebuild the notebooks. Stories 27.2, 27.5 and 27.7 replace them. Only fix what the move breaks,
  plus the views import and the reads named below.
- Add `research/tests` to `make test-live-paper`. The live-paper image does not ship research
  until Story 27.8; see Design Notes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Top-of-book read | snapshot files spanning the window | `TopOfBook` rows with `ts_event` in `[start, end]`, sorted, each carrying level-0 bid/ask price and size plus `ts_init` | None |
| Empty side | a row with an empty `bid_prices` or `ask_prices` | the row is omitted. There is no top of book to quote, which matches the old `_quotes` filter | None |
| No files / empty window | instrument has no snapshots in range | `[]`; `snapshot_backtest.run` raises its existing `ValueError("No … snapshots between …")` | unchanged message |
| Old import path | `from ml_signals.strategies.ofi_strategy import OFIStrategy` | the same object as `research.strategies.ofi_strategy.OFIStrategy`, plus a `DeprecationWarning` | None |
| Old string path | `ImportableStrategyConfig("ml_signals.strategies.ofi_strategy:OFIStrategy")` | resolves through the shim to the same class | None |

</intent-contract>

## Code Map

- `ml_signals/strategies/{backtest_dydx,backtest_ofi,backtest_snapshot,example_strategy,ofi_strategy,snapshot_backtest,snapshot_strategy}.py`, `ml_signals/{run_backtest,watchlist}.py`, `ml_signals/{BACKTESTING.md,backtest.ipynb}`, `dydx_collector/notebooks/*.ipynb`: the sources to move.
- `ml_signals/tests/{conftest,test_ad8_boundary,test_ofi_strategy,test_ofi_strategy_indicator_consistency,test_snapshot_backtest_node,test_snapshot_strategy,test_timeframe_backtest,test_watchlist,test_watchlist_multi_coin_backtest}.py`: research's tests. `conftest.py` holds the session BacktestEngine log guard. The rest stay (`test_catalog_stats`, `test_metrics_computer`, `test_rank_history`).
- `kernel/catalog_files.py`: `query_second_ohlc`/`_ohlc_rows` is the idiom for the new top-of-book reader. `kernel/tests/test_catalog_files.py` is its test home.
- `kernel/indicators.py:274` `MultiLevelOFI`: the indicator `OFIStrategy` drives, used for the parity test.
- `tests/test_boundaries.py:58` (`THIS_STORY`), `:166-195` (ml_signals map rows), `:247-253` (`(RESEARCH, VIEWS)` edge), `:543` (research→data_api test), `:931` (`NON_VENUE_HTTP_CLIENTS`).
- `tests/test_images.py:396`: the Makefile test dirs must be `COPY`ed.
- `collector.dockerfile:13-26`, `Makefile:147-149`.
- Docs: `CLAUDE.md:143` (NAUT-03), `README.md:274-281`, `ARCHITECTURE.md:18-19,37,176-205,327`, `docs/BOT_OPERATIONS.md:7,79-142`, `docs/DATA_DICTIONARY.md:511-514`, `frontend/src/pages/docs/kbData.ts:12,123-135`, `frontend/src/pages/docs/data.ts:296`, and the `live_paper/tests/{test_strategy.py:17,conftest.py:79-83}` comments.

## Tasks & Acceptance

**Execution:**
- [x] `kernel/catalog_files.py` -- add `TopOfBook(NamedTuple)` (`ts_event, ts_init, bid_price, bid_size, ask_price, ask_size`) and `query_top_of_book(catalog_path, instrument_id, start_ns, end_ns)`, projecting the four book columns plus both timestamps, with the same file selection as `query_second_ohlc` and rows sorted by `ts_event`. Rows with an empty side are skipped. -- The one kernel read research needs, and it avoids decoding the 20-level book.
- [x] `kernel/tests/test_catalog_files.py` -- test the matrix rows for `query_top_of_book`, using a catalog written through `ParquetDataCatalog.write_data()`: window bounds, sort order, empty-side omission, empty window.
- [x] `research/__init__.py` -- context docstring: a consumer with no aggregates; reads the catalog via `kernel.catalog_files`/`BacktestDataConfig`, rankings via HTTP; computes no rolling metric; strategies are referenced by string path.
- [x] `research/strategies/*` (`git mv` of the 7 modules and `__init__.py`) -- string paths become `research.strategies…`. `backtest_dydx` imports `research.watchlist`. `backtest_ofi` points `_CATALOG` at `platform/data/catalog`: the old `dydx_collector/catalog` predates commit `18c12eedf4`. `snapshot_backtest.run` builds its QuoteTicks from `query_top_of_book`, with the window from `pd.Timestamp(start/end, tz="UTC").value`, and no longer calls `catalog.query`. Update the docstrings' run commands.
- [x] `research/run_backtest.py`, `research/watchlist.py` -- `git mv`. Wrap `run_backtest`'s argparse in `main(argv: list[str] | None = None) -> None` behind `if __name__ == "__main__"`, so importing it has no side effect and a shim can re-export it.
- [x] `research/BACKTESTING.md`, `research/notebooks/{backtest,dydx_catalog_pandas,candlestick_pattern_scanner}.ipynb` -- `git mv`. Every path and command in `BACKTESTING.md` names `research/`. In `backtest.ipynb`: `research.strategies` string paths, the `platform/data/catalog` path, and rename `TROLL` → `PLATFORM`. In `dydx_catalog_pandas`: catalog `../../data/catalog`; its trade-tick cell becomes a bounded `query_second_ohlc` close series; its microprice cell uses `query_top_of_book` + `kernel.indicators.Microprice`, with no `views` import. Adjust its markdown cells to match. In `candlestick_pattern_scanner`: catalog `../../data/catalog`, plus a leading markdown `Known limit:` cell saying it reads the retired `custom_dydx_minute_bar` directory and is replaced by Story 27.7.
- [x] `research/tests/` -- `git mv` the eight research test modules plus `conftest.py`, add `__init__.py`, and repoint imports to `research.*`.
- [x] Split `test_ad8_boundary` -- `research/tests/test_ad8_boundary.py` checks the three `backtest_*` modules at `research/strategies/`. `ml_signals/tests/test_ad8_boundary.py` keeps checking `catalog_stats`, `chart_data` and `metrics_computer`. No check is dropped.
- [x] `research/tests/test_ofi_strategy.py` -- rewrite the three tests against the snapshot API:
  - Data: `CustomData(DataType(DydxSecondSnapshot))` snapshots with 2 bid and 2 ask levels and a growing best-bid size, plus a TradeTick 0.5 s after each snapshot so fills happen, fed with `client_id`.
  - Field mapping: `ma_period=2` → `ofi_zscore_window=2`, and `buy_threshold`/`sell_threshold` → `ofi_threshold`. Thresholds stay 0.5 / 999_999; depth stays 2 / 10.
  - Assertions stay the same: at least one fill and a BUY fill; zero fills; zero fills.
  - Update the docstring and record the root cause.
- [x] `research/tests/test_ofi_strategy_indicator_consistency.py` -- parity now means a direct replay of `kernel.indicators.MultiLevelOFI` over the snapshot arrays, built with the strategy config's levels, window, `usd_notional=True` and zscore window. That replay must equal the final `(value, initialized)` of the BacktestEngine-run `OFIStrategy`'s `_ofi` on the same 7 snapshots: bid add, ask add, same-price size changes, a better bid, a better ask, and a size change at the new bid. Keep all four assertions, with first-state-only being `[:1]` → `[(None, False)]`. No `views` import.
- [x] Shims -- `ml_signals/strategies/<each of 7>.py`, `ml_signals/watchlist.py` and `ml_signals/run_backtest.py` (re-exporting `main`, guarded by `if __name__ == "__main__": main()`) re-export their public names. `ml_signals/strategies/__init__.py` stays empty.
- [x] Expired Story 24.2 shims (`REMOVE_AFTER`/`MOVED_NAMES_REMOVE_AFTER` = this story; `test_namespace.py` fails once it is done, and Story 24.3 set the precedent of deleting them) -- delete `ml_signals/{book_features,chart_data,chart_indicator_config,chart_indicators,custom_indicators,footprint,ranking_columns,screener_columns_config}.py` and `data_api/{live_candles,redis_bus}.py`, `data_api/routes/paging.py`, along with their `test_boundaries.py` map rows. Drop the `_MOVED_NAMES` serving from `ml_signals/catalog_stats.py` (keep `_REPLACED_NAMES`) and `bot_tui/coin_detail.py`. The ml_signals `test_ad8_boundary` half follows `chart_data` to `views/chart_series.py`. Update docs that cite these as live re-exports.
- [x] `research/tests/test_research_reads.py` -- an AST guard over every research non-test `.py`, and over the code cells of every research notebook with magics stripped. It fails on a call to `.query`, `.trade_ticks`, `.quote_ticks`, `.order_book_deltas`, `.order_book_depth10`, `.bars`, `.custom_data`, `read_parquet`, `read_table` or `ParquetFile`. The only exemption is `{"candlestick_pattern_scanner.ipynb": "27-7-candlestick-pattern-detector-kernel-chart-screener-scanner"}`, and the guard fails once that story is done (the `unknown_or_done` idiom).
- [x] `tests/test_boundaries.py`:
  - `THIS_STORY` = this story; delete the `(RESEARCH, VIEWS)` edge and its comment.
  - Drop the map rows of moved test modules. Keep the shim rows (`ml_signals.strategies`, `.watchlist`, `.run_backtest` → RESEARCH) and `ml_signals.tests.test_ad8_boundary` → VIEWS (it now guards views/ranking files).
  - `NON_VENUE_HTTP_CLIENTS` gets `research.watchlist` in place of `ml_signals.watchlist`.
  - Add a test that `research` imports no `views`, `ranking_engine` or `ml_signals`.
- [x] `collector.dockerfile` `COPY platform/research ./research`; add `research/tests` to the `Makefile` `test` list.
- [x] Docs:
  - Update NAUT-03 in `CLAUDE.md` and the `README.md` backtest section.
  - `ARCHITECTURE.md`: a research row/section replaces the strategies/watchlist bullets under `ml_signals`, and the catalog readers' row names research.
  - Update `DATA_DICTIONARY.md` §2.9 path, `BOT_OPERATIONS.md` paths and commands, the frontend `kbData.ts`/`data.ts` refs, and the `live_paper/tests` comment paths.
  - Tick the story file's tasks and fill in its Completion Notes: the root causes, and the pass of all 9 research test modules.

**Acceptance Criteria:**
- Given `make test`'s list with `research/tests`, when it runs, then `test_boundaries.py`, `test_images.py` and `test_namespace.py` pass, all research tests pass (including the four OFI ones), no `DeprecationWarning` appears, and only pre-existing failures remain.
- Given `platform/research`, when searched, then no `views`, `data_api`, `ranking_engine` or `ml_signals` import exists and no `ml_signals.strategies` string path remains in the repo outside the shims.
- Given `git grep -n "ml_signals/strategies\|ml_signals.strategies\|ml_signals/watchlist\|ml_signals.watchlist\|dydx_collector/notebooks" platform`, then the only hits are the shims and dated historical notes.

## Spec Change Log

## Review Triage Log

### 2026-09-25 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 0, medium 2, low 12)
- defer: 0
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` The story file's Completion Notes still called the expired Story 24.2 shims an open item after they were deleted, and its File List and test evidence predated the deletion. Notes, File List and the final full-suite run are now recorded.
  - `[medium]` `[patch]` The `dydx_catalog_pandas` microprice cell plotted the previous second's value for a zero-size second (`initialized` stays true). It now resets `Microprice` per second.
  - `[low]` `[patch]` `snapshot_backtest.run` raised on tz-aware `start`/`end`. The window is now parsed with Nautilus's own `time_object_to_dt` + `dt_to_unix_nanos`, exactly as `catalog.query` parses it.
  - `[low]` `[patch]` `test_research_reads` missed `generic_data`, `read_pandas`, `scan_parquet`, `iter_batches`, `ParquetDataset` and `pyarrow.dataset`, and would error on a `%%` cell magic. Names added, `%%` cells blanked, with a test.
  - `[low]` `[patch]` `query_top_of_book` had no read-margin test. Added one, mirroring `query_second_ohlc`'s.
  - `[low]` `[patch]` `research/BACKTESTING.md` still said 1-minute bars are collected (retired, D-35), and its delta bullet lost its example. Both rewritten.
  - `[low]` `[patch]` `kbData.ts`: the `ml_signals` row still claimed a `ranking:control` publish, and the page said 1-minute bars exist. Both corrected.
  - `[low]` `[patch]` `ARCHITECTURE.md` §2 intro still called `ml_signals` "the one place indicators are implemented". Rewritten.
  - `[low]` `[patch]` Stale docstrings and comments corrected: `research/tests/test_ad8_boundary.py` (chart_data → `views/chart_series.py`), `tests/test_boundaries.py`'s `catalog_stats.__getattr__` comment, and the parity test's docstring (it compares the final state).

Rejected, with reasons:
- The `ofi_zscore_window=2` negative test was called "vacuous", but it fails if the threshold gate is removed. It is the old MA test's exact analogue.
- The parity replay "copies" the strategy's input rule. That rule is the documented contract being compared, and final-state parity is the original test's design.
- `TopOfBook` floats: the stored schema is `float64`, and the old code passed the same floats to `make_price`, so there is no new round trip.
- `backtest_dydx` "has no trade archive": wrong, since the Story 22.13 raw `trade_tick/` archive exists.
- The views AD-8 guard staying in `ml_signals/tests`: it still guards two `ml_signals` files and moves with them in 25.2.
- Notebooks not executed, the `backtest.ipynb` root-walk loop, and `instrument_ids[0]`: pre-existing, and Stories 27.2, 27.5 and 27.7 replace these notebooks.
- `REMOVE_AFTER` 25-2: story-mandated.
- Empty or null size lists, null element 0, fsspec URIs: re-validating what the gate writes is barred (AD-3), and every `kernel.catalog_files` helper is local-path.
- The script-path invocation of the `run_backtest` shim: documented `-m` form.

### 2026-09-25 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 1, low 3)
- defer: 1
- reject: 16
- addressed_findings:
  - `[medium]` `[patch]` `snapshot_backtest._quotes` kept `query_top_of_book`'s `ts_event` order, but `ParquetDataCatalog.write_data` raises unless `ts_init` is non-decreasing. A writer's `ts_init` may trail `ts_event` by up to `MAX_TS_INIT_SKEW_NS`, so an inversion in the window aborted the backtest, where `catalog.query` had returned `ts_init` order. The quotes are now sorted by `ts_init`, with a new test (`research/tests/test_snapshot_backtest_quotes.py`) that writes an inverted pair through `write_data`.
  - `[low]` `[patch]` The `ml_signals.strategies.backtest_{dydx,ofi,snapshot}` shims dropped their modules' `__main__` blocks, so the formerly documented `python -m ml_signals.strategies.backtest_*` warned and exited 0 without running. Each shim now `runpy.run_module`s its research module under `__main__`. The `runpy` import sits inside the guard, so the shim still binds only re-exported names.
  - `[low]` `[patch]` The `ARCHITECTURE.md` catalog row this story amended still said "No `trade_tick/`". It now names the Story 22.13 raw trade archive and its nightly 7-day prune.
  - `[low]` `[patch]` Two stale comments were corrected: `backtest_dydx` cited `catalog_stats.py` (outside research) for "no Bar objects" and now cites D-35, and `backtest_snapshot` said it mirrored `backtest_ofi.py`'s multi-config pattern, which is now `snapshot_backtest.py`'s.

Deferred: the cwd-relative `catalog_path` default in `backtest_dydx`/`backtest_snapshot` against BOT_OPERATIONS.md's `cd platform` (pre-existing).

Rejected, with reasons:
- Quote and snapshot windows use different clocks (`ts_event` vs `ts_init`): Design Notes accept this, since the gap is bounded by `MAX_TS_INIT_SKEW_NS` and harmless for quote seeding.
- Duplicate rows from overlapping files: the catalog's non-overlap is kept by consolidation, and `catalog.query` did not dedupe either.
- Missing book columns in old files, and a null element 0: the four list columns are in every `DydxSecondSnapshot` schema, and re-validating gate output is barred (AD-3).
- `test_research_reads` alias, `getattr` and `DataFrame.query` gaps, extra read names, and inline magics: a static tripwire over this context's own code; nothing in research hits them today.
- OFI test depth (exits, gates, gap reset), `zscore_window=2`, and the parity replay omitting the gap rule: the story repairs the four existing tests with their assertions kept, and these were rejected in the previous pass for the same reasons.
- Notebook cwd, `CryptoPerpetual.to_dict` over a multi-venue catalog, `instrument_ids[0]`, and the `backtest.ipynb` root walk: pre-existing and replaced by Stories 27.2, 27.5 and 27.7 (Never: rebuild the notebooks).
- `backtest_ofi._CATALOG` inside the image, and `data_api.dockerfile` shipping shims without `research/`: the spec mandates that path, research enters images in Story 27.8, and nothing in `data_api` imports the shims.
- The `REMOVE_AFTER` 25-2 tie, the `ml_signals.tests.test_ad8_boundary` → VIEWS label, `_build_run_config` not re-exported (private name), and the notebook recorded as a delete plus add: spec-mandated or cosmetic.
- The `run_backtest` shim's script-path invocation: the `-m` form is documented.

## Design Notes

**Root causes (for Completion Notes).**
- `test_snapshot_backtest_node`, `test_timeframe_backtest` and `test_watchlist_multi_coin_backtest` were collection errors. Commit `1031008cdd` moved `backtest_*` into `strategies/` and never updated these three imports. Commit `e5c1175c1f` (2026-09-21) already repointed them, so this story moves them and re-verifies them green.
- `test_ofi_strategy*` broke because the same commit rewrote `OFIStrategy` from an `OrderBookDeltas` + `OrderFlowImbalance` moving-average design (`ma_period`, `buy_threshold`, `sell_threshold`, trend bars, cancel tracker) into a `DydxSecondSnapshot` + `MultiLevelOFI` z-score design (`ofi_zscore_window`, `ofi_threshold`), and changed only the tests' import lines. The repair maps the claims onto the real API. Example: `ofi_zscore_window=2` makes a rising OFI read z = +1 on the third snapshot, so 0.5 fires and 999_999 does not.

**Why only `make test`.** Stories 24.1–24.3 added their context tests to `test` only. The
live-paper image deliberately ships no research code (`live_paper.dockerfile` comment, AD-8).
`test_images.py` would force a `COPY platform/research` there, which Story 27.8 introduces with its
string-path strategy. Story 27.1's line "already in both Makefile lists (24.4)" should read "in
`make test`".

**Why a kernel reader and not `catalog.query`.** The runner needs level 0 only. The catalog decoder
builds every row's 20-level book as Python objects, the cost Story 21.5 measured for candles. A
column projection keeps MEM-01 for multi-day windows too. `ts_event` filtering differs from
`BacktestDataConfig`'s `ts_init` bound by at most `MAX_TS_INIT_SKEW_NS`, which is harmless for
quote seeding.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -rw 2>&1 | tail -60` -- expected: only the pre-existing failures (the rankings redis test; dydx `trade_ohlc` if present) and no `DeprecationWarning` in the warnings summary.
- `cd platform && uvx ruff format --check research kernel ml_signals tests && uvx ruff check research kernel ml_signals tests` -- expected: clean.
- `cd platform && grep -rnE '^(from|import) (views|data_api|ranking_engine|ml_signals)' research` -- expected: no hits.


## Auto Run Result

Status: done

### Summary of implemented change

Follow-up review of the finished Story 24.4 (`research/` as a pure consumer, OFI tests repaired), over the diff `6a83dc7d7e..1c728732a2`. The story's code stands as delivered, with four review patches. The main one: `snapshot_backtest` now writes its derived QuoteTicks in `ts_init` order. `ParquetDataCatalog.write_data` requires that order, and `query_top_of_book` returns rows in `ts_event` order.

### Files changed

- `platform/research/strategies/snapshot_backtest.py`: `_quotes` sorts by `ts_init`, with a docstring explaining why.
- `platform/research/tests/test_snapshot_backtest_quotes.py`: new test. An inverted `ts_event`/`ts_init` pair is ordered by `ts_init` and accepted by `write_data`.
- `platform/ml_signals/strategies/backtest_{dydx,ofi,snapshot}.py`: shims gain a `__main__` guard that runs the research module, and their docstrings say so.
- `platform/research/strategies/backtest_{dydx,snapshot}.py`: stale comment references corrected.
- `platform/ARCHITECTURE.md`: catalog row names the raw `trade_tick/` archive in place of "No `trade_tick/`".
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new entry (the cwd-relative `catalog_path` default).

### Review findings breakdown

- 4 patches applied (medium 1, low 3), 1 deferred, 16 rejected (reasons in the triage log).

### Verification

- The full `make test` list on the host: 1577 passed, 1 failed. The failure is the pre-existing `data_api/tests/test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay`, which needs Redis. No warnings summary.
- The new test fails without the sort: its input has a decreasing `ts_init`, which `_objects_to_table` rejects. It passes with the sort.
- `uvx ruff format --check`: clean. `uvx ruff check` on `research kernel ml_signals tests`: only the 3 pre-existing notebook-cell findings.
- Importing `ml_signals.strategies.backtest_ofi` still emits its `DeprecationWarning`, and `test_namespace.py`'s shim scanner passes.

### Residual risks

- The shims' `python -m` path was not run end-to-end (it would run a real backtest). It delegates through `runpy.run_module` to the research module's own `__main__`.
- `make test` was not run inside the Docker image, and mypy is not installed on the host.
- Carried over: `candlestick_pattern_scanner.ipynb` reads the retired minute-bar directory until Story 27.7, and `research/tests` stays out of `make test-live-paper` until Story 27.8.
