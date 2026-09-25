# Story 24.4: `research/` as a pure consumer, with its broken tests repaired

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 24). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 24.4".

## Story

As a strategy researcher,
I want the backtest strategies, runners and notebooks in one context that only reads the catalog, the ranking history and the watchlist API,
So that research can never leak a computation back into the live path, and its test suite runs green again.

## Acceptance Criteria

1. **Given** `ml_signals/strategies/*`, `ml_signals/{run_backtest,watchlist}.py`, `ml_signals/BACKTESTING.md`, `ml_signals/backtest.ipynb`, `dydx_collector/notebooks/`
**When** the story ships
**Then** `platform/research/` holds `strategies/`, `run_backtest.py`, `watchlist.py` (HTTP to `/api/rankings` only; `test_boundaries.py` asserts no `data_api` import), `notebooks/` and `BACKTESTING.md`; backtests still reference strategies by `ImportableStrategyConfig` string path (the paths change to `research.strategies...` and the docs say so); every research catalog read goes through `kernel.catalog_files` or `BacktestDataConfig`; research computes no rolling metric of its own (pct/volatility come from `metrics.db` via the ranking query service)

2. **Given** `ml_signals/tests/{test_snapshot_backtest_node,test_timeframe_backtest,test_watchlist_multi_coin_backtest}.py` fail at collection (`from ml_signals import backtest_dydx` while the module lives in `strategies/`) and `test_ofi_strategy*.py` fail on a stale `ma_period` keyword
**When** the story ships
**Then** all five modules are repaired against the real strategy API (never by deleting a test or loosening an assertion), run in `make test` under `research/tests`, and their pass is recorded in the story's Completion Notes together with the root cause of each break (TEST-04)

3. **Given** MR2 and MR4
**When** the story is merged
**Then** the moved `ml_signals` modules are pure re-export shims with `REMOVE_AFTER = "25-2-..."` (the `ml_signals` package is retired for good in Story 25.2), the collector image `COPY`s `research`, the Makefile test lists include `research/tests`, and `platform/CLAUDE.md` NAUT-03 and the README's backtest section cite the new paths

## Tasks / Subtasks

- [x] Task 1 — `platform/research/` (AC: #1)
  - [x] Move `ml_signals/strategies/` → `research/strategies/`, `run_backtest.py`, `watchlist.py`, `BACKTESTING.md`, `backtest.ipynb`, `dydx_collector/notebooks/` → `research/notebooks/`. Update every `ImportableStrategyConfig` string path (`research.strategies.<module>:<Class>`), the README backtest section and `platform/CLAUDE.md` NAUT-03. Catalog reads via `kernel.catalog_files` or `BacktestDataConfig`; pct/volatility via `ranking`'s query service (`ranking_engine.metrics_store.history/nearest` until 25.2 — legacy edge listed).
- [x] Task 2 — repair the five broken test modules (AC: #2)
  - [x] `test_snapshot_backtest_node.py`, `test_timeframe_backtest.py`, `test_watchlist_multi_coin_backtest.py`: fix the imports to the `strategies` package (root cause: modules moved in commit `1031008cdd`, tests never updated); `test_ofi_strategy.py`, `test_ofi_strategy_indicator_consistency.py`: align with the current `OFIStrategyConfig` fields (find what replaced `ma_period`; never delete an assertion). Record each root cause in Completion Notes. All run under `research/tests` in `make test`.
- [x] Task 3 — shims, images, lists (AC: #3)
  - [x] Shims for the moved `ml_signals` modules (`REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"`); `collector.dockerfile` `COPY platform/research ./research`; Makefile lists add `research/tests`.

## Dev Notes

Research is a consumer with no aggregates. The five broken tests predate the migration (root causes in the story ACs); fixing them is in scope because they are research's tests and TEST-04 forbids leaving them. `watchlist.py` uses HTTP, not an import, so the boundary test asserts the absence of a `data_api` import rather than an edge.

### Migration rules that bind every story (spine AD-D12, MR1/MR2/MR4/MR14)

- **Deployable alone.** Frozen for the whole migration: Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`), the SQLite/TOML store schemas, compose service names, env vars, the `platform/data/` bind mounts. A replay/fixture test proving a payload or file is byte-identical before and after the move is the standard evidence.
- **Shims.** The old import path stays as a pure re-export: `from <new> import <names>` + `warnings.warn(..., DeprecationWarning)` + `REMOVE_AFTER = "<story key>"`. It defines nothing (a copied class body would register a second Arrow class and break `is` dispatch). Update every in-repo caller in the same story; a `DeprecationWarning` in the test run is a failure (TEST-04). `platform/tests/test_namespace.py` asserts `old.X is new.X`.
- **Same commit:** `platform/CLAUDE.md` citations, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, both Makefile test lists (`test`, `test-live-paper`). `platform/tests/test_images.py` and `test_boundaries.py` (from 23.1) must pass.
- **Layering (AD-D2):** `domain/` imports only stdlib, `kernel/` and `nautilus_trader.model`/`core` types — no I/O, asyncio, Redis, SQLite, Parquet or the Nautilus runtime; `application/` holds `typing.Protocol` ports, services and the asyncio loops; `infrastructure/` implements ports and is imported only by the composition root. No module-level mutable runtime state (AD-D10). Every aggregate/port docstring names the invariant it protects (DESIGN-01).
- **Parent spine:** when this story resolves one of its Deferred items, strike it there with a `[amended <date>: Story <n>]` note (MR14).
- **Project rules:** `platform/CLAUDE.md` DATA-01..08, DATA-07 (no silent skips; `observability.error_ledger.record`), TEST-01..04 (real Nautilus objects, no mocks of internals, warnings are failures), READ-03 (type hints, mypy), SSOT-01..05, MEM-01..03, NAUT-01..03, FORK-01 (never touch `nautilus_trader/` or `crates/`).
- **Working directory:** `platform/` (`cd platform`); tests run as `python3 -m pytest -o addopts="" --rootdir=. <paths> -q`; `make test` runs the Makefile list inside the collector image.

### Project Structure Notes

- Target tree: spine "Structural Seed". Working dir `platform/` (renamed from `troll/` on 2026-09-21; historical docs cite `troll/`). Durable stores under `platform/data/`, never under a package.
- `platform/` is a namespace directory: never add `platform/__init__.py`; never import with a `platform.` prefix (contexts are top-level packages with `platform/` on `sys.path`).

### References

- Spine sections: AD-D1 research row, AD-6 (inherited), AD-D10 (no recompute)
- Review findings that shaped this story: commit `1031008cdd` (strategies move), test failures recorded in commit `18c12eedf4`'s message
- Code: `ml_signals/strategies/*`, `ml_signals/tests/{test_snapshot_backtest_node,test_timeframe_backtest,test_watchlist_multi_coin_backtest,test_ofi_strategy,test_ofi_strategy_indicator_consistency}.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

claude-opus-5-5

### Debug Log References

- `python3 -m pytest -o addopts="" --rootdir=. research/tests -q` (from `platform/`): 22 passed, no warnings.
- Full `make test` list on the host (`research/tests alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests`), final tree after the expired-shim deletion and the review patches: 1576 passed, 1 failed -- the pre-existing `data_api/tests/test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay` (needs a Redis on 127.0.0.1:6379). No `DeprecationWarning` (no warnings summary at all). The count is lower than the 1638 of the pre-deletion run because `tests/test_namespace.py` no longer parametrizes over the 13 deleted shims' names.
- Manual smoke of `research.strategies.snapshot_backtest.run` against a throwaway catalog: 200 iterations (100 derived quotes + 100 snapshots), identical to the pre-change `catalog.query` implementation run on the same catalog; an empty window still raises `ValueError("No BTC-USD-PERP.DYDX snapshots between 2026-09-07 and 2026-09-08")`. The old `ml_signals.strategies.ofi_strategy:OFIStrategy` string path resolved through the shim.

### Completion Notes List

- **Root cause, `test_snapshot_backtest_node` / `test_timeframe_backtest` / `test_watchlist_multi_coin_backtest`** (collection errors): commit `1031008cdd` moved the `backtest_*` runners into `ml_signals/strategies/` and never updated these three tests' `from ml_signals import backtest_*` imports. Commit `e5c1175c1f` (2026-09-21) had already repointed them; this story moved them to `research/tests/` (imports now `research.strategies`) and re-verified them green.
- **Root cause, `test_ofi_strategy` (x3) and `test_ofi_strategy_indicator_consistency` (x1)**: the same commit `1031008cdd` rewrote `OFIStrategy` from an `OrderBookDeltas` + `OrderFlowImbalance` moving-average design (`ma_period`, `buy_threshold`, `sell_threshold`, trend bars, cancel tracker) into a `DydxSecondSnapshot` + `MultiLevelOFI` z-score design (`ofi_zscore_window`, `ofi_threshold`) and changed only the tests' import lines, so every test failed with `TypeError: Unexpected keyword argument 'ma_period'`.
- **Repair, `test_ofi_strategy.py`**: input is now six `CustomData(DataType(DydxSecondSnapshot))` snapshots (2 bid + 2 ask levels, best-bid size 10, 15, ... 35) plus a `TradeTick` 0.5 s after each, fed with `client_id=ClientId("BINANCE")` on an `L1_MBP` venue. Field mapping `ma_period=2` -> `ofi_zscore_window=2` (with `ofi_window=2` kept), `buy_threshold`/`sell_threshold` -> `ofi_threshold` (0.5 / 999_999), `min_depth_levels` 2 / 10. The z-score fired exactly as the spec predicted: each update contributes +500 USD, the raw OFI reads 500 then 1000, so the third snapshot's z-score is +1 (> 0.5, < 999_999); a replay confirmed the BUY fill at t = 3 s. All assertions kept (>= 1 fill and a BUY fill; 0 fills; 0 fills). One test was renamed `test_ofi_strategy_no_trade_below_ma_threshold` -> `test_ofi_strategy_no_trade_below_threshold` (there is no MA any more); its body and assertion are unchanged.
- **Repair, `test_ofi_strategy_indicator_consistency.py`**: parity partner changed from `views.chart_series.top_of_book_series` + `OrderFlowImbalance` (a research -> views edge) to a direct replay of `kernel.indicators.MultiLevelOFI` built from the strategy config (`ofi_levels=2`, `ofi_window=50`, `usd_notional=True`, `ofi_zscore_window=10`) over the 7 snapshot states (bid add, ask add, same-price bid and ask size changes, a better bid, a better ask, a size change at the new bid). The replay applies the strategy's one input rule (a snapshot with an empty side is not fed; the first snapshot is bid-only) and records `(value, initialized)` after each snapshot, so `[:1]` -> `[(None, False)]` holds and the final state equals the BacktestEngine-run `OFIStrategy._ofi` exactly (`(1.7367..., True)`, a non-trivial z-score). All four assertions kept.
- `snapshot_backtest.run` no longer calls `catalog.query`: it builds its QuoteTicks from the new `kernel.catalog_files.query_top_of_book` (level 0 only, column-projected, empty-side rows omitted, sorted by `ts_event`, same file selection as `query_second_ohlc`), window parsed with `time_object_to_dt` + `dt_to_unix_nanos`, exactly as `ParquetDataCatalog.query` parses it (naive = UTC, tz-aware converted; inclusive); the `ValueError` message is unchanged.
- `run_backtest.py` is now `main(argv)` behind `if __name__ == "__main__"`, run as `python -m research.run_backtest` from `platform/` (the module-level `sys.path` insert was dropped: importing it has no side effect).
- Research computes no rolling metric and reads none today (no pct-change/volatility code exists in it), so no ranking query-service edge was added (spec: do not add one).
- Shims at `ml_signals/strategies/{backtest_dydx,backtest_ofi,backtest_snapshot,example_strategy,ofi_strategy,snapshot_backtest,snapshot_strategy}.py`, `ml_signals/watchlist.py` and `ml_signals/run_backtest.py` (re-exports `main`, runnable), `REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"`; `tests/test_namespace.py` checks identity + warning for each.
- **Expired Story 24.2 shims deleted** (the Story 24.3 precedent; `tests/test_namespace.py::test_shim_expiry_story_is_not_done` would fail for each once this story is `done`): `ml_signals/{book_features,chart_data,chart_indicator_config,chart_indicators,custom_indicators,footprint,ranking_columns,screener_columns_config}.py`, `data_api/{live_candles,redis_bus}.py`, `data_api/routes/paging.py` (no in-repo importer), and the `_MOVED_NAMES` forwarding in `ml_signals/catalog_stats.py` (its `_REPLACED_NAMES` stay) and `bot_tui/coin_detail.py`. Their `test_boundaries.py` map rows are gone; `ml_signals/tests/test_ad8_boundary.py` follows `chart_data` to `views/chart_series.py`.
- **Review pass patches:** `test_research_reads` also flags `generic_data`, `read_pandas`, `scan_parquet`, `iter_batches`, `ParquetDataset` and `dataset`, and blanks `%%` cell magics; `query_top_of_book` gained a read-margin test; the `dydx_catalog_pandas` microprice cell resets per second so a zero-size second is not plotted with the previous value; docs (`research/BACKTESTING.md` bar-feed and delta bullets, `kbData.ts`, `ARCHITECTURE.md` §2 intro) and three docstrings/comments corrected.

### File List

- `platform/kernel/catalog_files.py` (modified: `TopOfBook`, `query_top_of_book`)
- `platform/kernel/tests/test_catalog_files.py` (modified: top-of-book tests)
- `platform/research/__init__.py` (new)
- `platform/research/strategies/{__init__,backtest_dydx,backtest_ofi,backtest_snapshot,example_strategy,ofi_strategy,snapshot_backtest,snapshot_strategy}.py` (moved from `ml_signals/strategies/`; `backtest_dydx`, `backtest_ofi`, `backtest_snapshot`, `snapshot_backtest` modified)
- `platform/research/run_backtest.py`, `platform/research/watchlist.py` (moved from `ml_signals/`; `run_backtest` modified)
- `platform/research/BACKTESTING.md` (moved from `ml_signals/`, modified)
- `platform/research/notebooks/backtest.ipynb` (moved from `ml_signals/`), `platform/research/notebooks/{dydx_catalog_pandas,candlestick_pattern_scanner}.ipynb` (moved from `dydx_collector/notebooks/`); all modified
- `platform/research/tests/{conftest,test_ofi_strategy,test_ofi_strategy_indicator_consistency,test_snapshot_backtest_node,test_snapshot_strategy,test_timeframe_backtest,test_watchlist,test_watchlist_multi_coin_backtest}.py` (moved from `ml_signals/tests/`, imports repointed; the two OFI tests rewritten)
- `platform/research/tests/{__init__,test_ad8_boundary,test_research_reads}.py` (new)
- `platform/ml_signals/strategies/{__init__,backtest_dydx,backtest_ofi,backtest_snapshot,example_strategy,ofi_strategy,snapshot_backtest,snapshot_strategy}.py`, `platform/ml_signals/{watchlist,run_backtest}.py` (new: re-export shims; `__init__` empty)
- `platform/ml_signals/tests/test_ad8_boundary.py` (modified: views/ranking half, `chart_data` -> `views/chart_series.py`)
- `platform/ml_signals/tests/conftest.py` (moved to `research/tests/`)
- `platform/ml_signals/{book_features,chart_data,chart_indicator_config,chart_indicators,custom_indicators,footprint,ranking_columns,screener_columns_config}.py`, `platform/data_api/{live_candles,redis_bus}.py`, `platform/data_api/routes/paging.py` (deleted: expired Story 24.2 shims)
- `platform/ml_signals/catalog_stats.py`, `platform/bot_tui/coin_detail.py` (modified: expired `_MOVED_NAMES` forwarding removed)
- `platform/tests/test_boundaries.py` (modified)
- `platform/collector.dockerfile`, `platform/Makefile` (modified)
- `platform/CLAUDE.md`, `platform/README.md`, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, `platform/docs/BOT_OPERATIONS.md` (modified)
- `platform/frontend/src/pages/docs/{kbData,data}.ts` (modified)
- `platform/live_paper/tests/{conftest,test_strategy}.py`, `platform/bot_tui/tests/conftest.py`, `platform/ranking_engine/tests/conftest.py` (modified: comment paths)
- `_bmad-output/planning-artifacts/epics.md`, `_bmad-output/implementation-artifacts/27-1-research-domain-analysis-values-and-application-ports.md` (modified: "in both Makefile lists" -> "in `make test`", per the spec's Design Notes)
