---
title: 'Story 27.1: research/domain analysis value objects and research/application ports'
type: 'feature'
created: '2026-09-28'
status: 'done'
final_revision: '0020d8ac00b905810c5b71795a50fb30738e457c'
baseline_revision: 'f70aee8c8eb71f28e226665d8a605327b2c07930'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-1-research-domain-analysis-values-and-application-ports.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Notebooks, backtest reports and (later) bot reports each need returns, drawdowns, trade stats and correlations, and today every caller would re-derive them ad hoc; there is also no single typed, time-bounded way for a notebook to read market frames or run a backtest.

**Approach:** Add `research/domain/` (pure numpy value objects with named invariants, wrapping `kernel.performance_metrics` rather than reimplementing it) and `research/application/` (`typing.Protocol` ports `MarketFrames`, `RankingHistory`, `BacktestRunner` plus their implementations over the catalog, the candle store, data_api's metrics HTTP API and `BacktestNode`), then amend the spine, boundary tests and docs.

## Boundaries & Constraints

**Always:**
- `research/domain/` imports only stdlib, numpy, `kernel`, `nautilus_trader.model`/`core`; no pandas, no I/O. Every class docstring names its invariant (DESIGN-01).
- Statistics come from `kernel.performance_metrics` only (`MetricReport` calls `all_metrics`; `rolling_sharpe` calls `return_stats`); no local mean/std Sharpe formula (SSOT-02).
- Every `MarketFrames` read takes required keyword-only `start`/`end` (no defaults) and reads through `kernel.catalog_files`, a `ParquetDataCatalog.query(..., start=, end=)`, or `candles.application.queries.window` (MEM-01). Gaps stay gaps: NaN/None, never forward-filled or interpolated.
- Derived columns (`mid`, `spread`, `microprice`, `obi_N`) come from `kernel.indicators` (`mid_price`/`spread`/`microprice` functions, `MultiLevelOBI`), never an inline formula.
- `BacktestRunner` uses `BacktestNode` + `BacktestDataConfig` + `ImportableStrategyConfig` string paths; results attributed by `config_id`, never list position; a missing result raises (DATA-07). `LoggingConfig(bypass_logging=True)`.
- Real Nautilus objects in tests, catalogs built with `ParquetDataCatalog.write_data()`; no mocks (TEST-03). LGPL header on every new file; ruff line length 100; full type hints.

**Block If:**
- The pinned Nautilus version cannot yield per-run positions/account reports from `BacktestNode` at all (no `get_engine` path with `dispose_on_completion=False`).

**Never:**
- No new dependency (NFR12). No edit under `nautilus_trader/` or `crates/`. No `research` import of `ranking`, `views` or `data_api` (ranking history is HTTP). No pandas resample or seconds→bars fold in research. No notebooks in this story (27.2+). Never write `sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Equity drawdown | curve 100 → 120 → 90 | underwater `[0, 0, -0.25]`; one episode `(peak_ts=t1, trough_ts=t2, recovery_ts=None, depth=0.25)` | — |
| Bad equity | non-increasing `ts_ns` or NaN value | — | `ValueError` in `__post_init__` |
| Price gap | prices `[1, None, 2, 4]` period 1s | returns `[nan, nan, 1.0]` (never bridges a gap) | — |
| Resample | period 1s → 3s, or 2s → 3s | compounded buckets / — | non-multiple period → `ValueError` |
| Correlation | `b = -a`, and `b = 2a + 1` | `rho = -1` / `+1`, pairwise-complete over finite rows | — |
| Lead-lag | `b[t] = a[t-3]` | best lag = `+3` (a leads) | — |
| Trade ledger | `exit_ts < entry_ts` | — | `ValueError` |
| Unbounded read | `frames.seconds(iid)` without start/end | — | `TypeError` (keyword-only, no default) |
| Missing candle store | `CANDLES_DIR` has no `candles_<venue>.db` | — | `FileNotFoundError` naming the path |
| Multi-venue run | `RunSpec` ids on two venues | — | `ValueError` (Known limit: one venue per run) |

</intent-contract>

## Code Map

- `platform/kernel/performance_metrics.py` -- `trade_stats`/`equity_returns`/`return_stats`/`all_metrics`; the only statistics home. Nautilus bins returns to UTC days, annualises by 365.
- `platform/kernel/indicators.py` -- `mid_price`/`spread`/`microprice(snapshot_dict)`, `MultiLevelOBI.update_raw`.
- `platform/kernel/catalog_files.py` -- `query_top_of_book` (bounded level-0 read) used for derived quotes.
- `platform/kernel/venues.py` -- `venue_of(iid)` for the candle-store venue.
- `platform/candles/application/queries.py` -- `open_store(candles_dir, venue)`, `window(db, iid, bar_seconds, before_ms, limit)`.
- `platform/data_api/routes/metrics.py` -- `GET /api/metrics/history/{symbol}?days=N` → `{"items": [...]}`.
- `platform/research/strategies/snapshot_backtest.py` -- derived-QuoteTick catalog trick, bypass_logging comment.
- `platform/research/strategies/backtest_dydx.py` -- config-id attribution rule and its docstring.
- `platform/research/strategies/ofi_strategy.py` -- `OFIStrategy`/`OFIStrategyConfig` for the runner test.
- `platform/bots/infrastructure/fills_store.py:185` -- `pnl_by_day` shape: net realized PnL per UTC day bucket.
- `platform/tests/test_boundaries.py` -- `GRAPH`, domain purity rule (numpy already a domain-safe root), research no-views/ranking test.
- `platform/research/tests/test_research_reads.py` -- forbidden catalog-read scanner (currently forbids every `query` call).
- `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` -- AD-D1 research row (line ~156), AD-D2 rule + mermaid graph.

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/domain/__init__.py`, `returns.py` -- `ReturnSeries` (frozen; `values` float64, `ts_ns` int64 on the period grid, `period_seconds`); `from_prices(prices, ts_ns, period_seconds)` (floor ts to grid; return only between adjacent grid buckets, else NaN; duplicate bucket raises), `from_equity(curve, period_seconds)` (equity is a step-function state sampled at each bucket's last value), `resample` (compound; NaN poisons its bucket), `annualisation_factor`, `rolling_sharpe(window)` via `return_stats`, NaN window → NaN.
- [x] `platform/research/domain/equity.py` -- `EquityCurve`, `DrawdownEpisode`, `drawdowns()`, `from_pnl_by_day` (cumulative equity at each `period_start`, identical arithmetic to `equity_returns`), `returns_by_ts()` equal to `equity_returns` output.
- [x] `platform/research/domain/trades.py` -- `ClosedTrade`, `TradeLedger` (`realized_pnls`, `holding_times_s`, `by_hour_of_day`, `by_weekday`, `pnl_by_day` matching the fills-store shape).
- [x] `platform/research/domain/report.py` -- `MetricReport` typed fields = `all_metrics` keys; `from_ledger(ledger, starting_balance)`, `as_table()`.
- [x] `platform/research/domain/correlation.py` -- `AlignedReturns`, `align`, `CorrelationMatrix`, `correlation_matrix`, `lead_lag`, `cluster(matrix, threshold)` with `Known limit:` O(n^3).
- [x] `platform/research/application/{__init__,ports,frames,ranking_history,backtest_runner}.py` -- ports + `RunSpec`/`RunResult`; `CatalogFrames`; `HttpRankingHistory`; `NodeRunner` (`dispose_on_completion=False`, reports via `node.get_engine(config_id)`, shared derived-quote temp catalog for `seconds`, `bars:<spec>` = TradeTick stream + INTERNAL `bar_type` param; one venue per run).
- [x] `platform/research/strategies/snapshot_backtest.py` -- expose its quote derivation as a public helper the runner reuses (no behaviour change).
- [x] `platform/research/tests/test_{returns,equity,trades,report,correlation,frames,ranking_history,backtest_runner}.py` -- the matrix cases; frames over a `write_data` catalog + a real sqlite candle store; ranking history over a real local `http.server`; runner over a two-day synthetic catalog running `OFIStrategy` by string path (single + 2-point sweep).
- [x] `platform/tests/test_boundaries.py` -- add `(RESEARCH, CANDLES)` edge (query services `open_store`/`window`), docstring updated; `research/tests/test_research_reads.py` -- a catalog-object query is allowed only with both `start=` and `end=` keywords (Parquet file reads stay forbidden), with a scanner test.
- [x] Spine AD-D1 research row + AD-D2 (numpy sentence, research→candles edge) amended `[amended 2026-09-28: Story 27.1]`; `platform/ARCHITECTURE.md` module map + §2b; `platform/research/__init__.py` docstring; `platform/research/BACKTESTING.md` "Run from a notebook" over `BacktestRunner`; `platform/CLAUDE.md` citations if any rule text names research reads.

**Acceptance Criteria:**
- Given the new domain package, when `test_boundaries.py` runs, then every `research.domain` module passes the domain purity rule and research reaches only `kernel`, `observability`, `candles`.
- Given a `pnl_by_day` list and starting balance, when `EquityCurve.from_pnl_by_day(...).returns_by_ts()` is compared to `equity_returns(...)`, then they are equal exactly (`==`, not approx).
- Given `MetricReport`, when its field names are compared to `all_metrics([], [], 1.0).keys()`, then they are identical.
- Given a two-day synthetic catalog, when `NodeRunner.sweep(spec, grid)` runs two grid points, then two `RunResult`s return with distinct `config_id`s each equal to its `BacktestRunConfig.id`, each with an `EquityCurve`, `TradeLedger`, `MetricReport`.
- Given the full suite, when run, then no new failures versus baseline.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 20: (high 0, medium 9, low 11)
- defer: 0
- reject: 4
- addressed_findings:
  - `[medium]` `[patch]` Frames windowed on `ts_init` (catalog filter) not `ts_event`; now skew-widened `ts_init` query, filtered `[start, end)` on `ts_event`, sorted; marks and index share the window
  - `[medium]` `[patch]` `bars()` sub-ms start admitted an earlier bar and retention loss looked like "no trades"; exact ns bound, raises before store coverage (`oldest_t`)
  - `[medium]` `[patch]` `RunSpec` window/balance/duplicate-id validation only on the `seconds` path; now validated for every kind
  - `[medium]` `[patch]` Sweep silently de-duplicated grid points (zip mis-attribution); duplicates now raise, results in grid order
  - `[medium]` `[patch]` Commissions summed across currencies / gross PnL for non-settlement fees; now raises (`Known limit:`)
  - `[medium]` `[patch]` Equity "keep last" relied on an unstable report sort; built from the account's own event order
  - `[medium]` `[patch]` research→candles edge opened wholesale; `RESEARCH_CANDLES_SERVICES` symbol allowlist
  - `[medium]` `[patch]` Reads scanner accepted `start=None`; literal `None` bound no longer counts
  - `[medium]` `[patch]` `resample` compounded partial buckets (bridged gaps); incomplete bucket → NaN
  - `[low]` `[patch]` one-sided book gave OBI 0/1 → None; index reader missing-precision/raw-width checks; `_pearson` near-zero variance; `cluster` threshold validation; `rolling_sharpe` sub-2-day window raises; empty ranking frame columns; `RunResult` metrics-vs-equity docstring; quote derivation moved to `research/application/quotes.py`; `bypass_logging` `Known limit:`; duplicate instrument ids

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 6, low 6)
- defer: 1
- reject: 15
- addressed_findings:
  - `[medium]` `[patch]` `CatalogFrames.bars` checked store coverage at the window start only; a window past the newest bucket (store behind, future `end`) now raises too, via a new `candles.application.queries.newest_t` (allowlisted in `RESEARCH_CANDLES_SERVICES`)
  - `[medium]` `[patch]` A `trades`/`bars:` run over an empty window finished as a zero-trade result; a run that streamed no data (`iterations == 0`) now raises
  - `[medium]` `[patch]` Runner window was inclusive of `end` (BacktestDataConfig) and derived quotes were selected on `ts_event` while snapshots replay on `ts_init`; data configs now take `[start_ns, end_ns - 1]`, quotes are derived over the skew-widened window so every streamed snapshot has its quote
  - `[medium]` `[patch]` `RunResult` docstring claimed equity includes open-position effects; corrected to balance `total`, with a `Known limit:` (not marked to market; three drawdown views) and upgrade path
  - `[medium]` `[patch]` `HttpRankingHistory` frame columns depended on the payload and order was assumed; now exactly `METRIC_COLUMNS` (omitted field = NaN), unknown fields and non-increasing `ts` raise
  - `[medium]` `[patch]` `bars:` kind had no end-to-end proof; new test runs a probe strategy by string path that round-trips only when INTERNAL 1-minute bars arrive
  - `[low]` `[patch]` unknown positions-report `entry` was recorded as SHORT; now raises
  - `[low]` `[patch]` `ReturnSeries.from_prices` turned an infinite price into a gap; now raises (DATA-07)
  - `[low]` `[patch]` frame query start could go negative near epoch; clamped at 0
  - `[low]` `[patch]` `query_index_prices` trusted the file's precision label; a raw value finer than the label now raises
  - `[low]` `[patch]` `TradeLedger` built from a list stayed mutable; frozen into a tuple
  - `[low]` `[patch]` `rolling_sharpe` O(len * window) cost now a documented `Known limit:` with upgrade path

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 15: (high 0, medium 4, low 11)
- defer: 2
- reject: 8
- addressed_findings:
  - `[medium]` `[patch]` `CatalogFrames.bars` checked coverage only at the window's edges, so an interior collector outage (no stored bucket at all) read as "no trades"; every bucket in the window must now be stored (traded or not) via a new read-only `candles.application.queries.bucket_starts` (allowlisted in `RESEARCH_CANDLES_SERVICES`), else `ValueError` naming the first missing bucket
  - `[medium]` `[patch]` `bars` kept a bar whose open time was inside the window but whose span ran past `end`, leaking trades after `end`; only bars whose whole span lies in `[start, end)` are returned
  - `[medium]` `[patch]` `ReturnSeries.from_equity` dropped the move from `starting_balance` to the first bucket's equity (disagreeing with `EquityCurve.returns_by_ts`); the first bucket's return is now against `starting_balance`
  - `[medium]` `[patch]` `EquityCurve.drawdowns`/`underwater` ignored `starting_balance`, so a strategy losing from day one showed no drawdown; the running peak starts at `starting_balance` and such an episode carries `peak_ts=None`
  - `[low]` `[patch]` `AlignedReturns` from plain lists raised `AttributeError`; coerced first; `HttpRankingHistory` all-None field stayed object dtype (now float64), a row without `ts` or a payload without `items` raises a `ValueError` naming the URL; `query_index_prices` guards a precision label outside `0..FIXED_PRECISION` and a null raw value; `RunSpec` refuses a bare `str` for `instrument_ids` and documents its `ts_init` vs frames' `ts_event` edges; `ClosedTrade.qty` documented as peak size; `seconds` `Known limit:` (bounded, not capped); ARCHITECTURE/BACKTESTING/spine candles allowlist lists `newest_t`/`bucket_starts`

## Design Notes

- **Graph decision:** the story names `candles.application.window` for bars; research→candles joins the graph as a read-only query-service edge like `views`/`archive` already have. This supersedes AC #3's "nothing else new" (written in the legacy-map era) and is recorded in the spine. Ranking stays forbidden: `RankingHistory` reads data_api's `/api/metrics/history` (itself ranking's query service via views), exactly as `rank_history.py` already does.
- **Reads scanner:** AC #2 allows "the catalog's typed `query` with `start`/`end`"; the Story 24.4 scanner forbade all `query` calls. The amended rule keeps MEM-01's intent: an unbounded catalog query still fails.
- **Report accessor:** `backtest_dydx.py` found engine reports empty after `run()`; root cause is `dispose_on_completion=True` disposing the engine (`BacktestNode._run`). The runner sets it False, reads reports via `node.get_engine(config_id)`, then `node.dispose()`. Known limit: a sweep holds N engines until reports are read.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests tests/test_boundaries.py -q` -- expected: all pass
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: no failures beyond the pre-change baseline
- ruff/mypy via a scratchpad venv (`ruff==0.15.16`, `mypy==1.20.2`) on the new files -- expected: clean


## Auto Run Result

Status: done

**Summary:** Third review pass (fresh Blind Hunter + Edge Case Hunter over `f70aee8c8e..d766eb45c8`) of Story 27.1 (`research/domain/` analysis values; `research/application/` ports `MarketFrames`/`RankingHistory`/`BacktestRunner` with `CatalogFrames`, `HttpRankingHistory`, `NodeRunner`). 15 patches applied.

**Files changed in this pass:**
- `platform/candles/application/queries.py` -- new `bucket_starts` (every stored bucket start, traded or not, in a range); `candles/tests/test_candle_store.py` proves it skips an outage.
- `platform/research/application/frames.py` -- `bars` raises on any never-observed bucket in the window and returns only bars wholly inside `[start, end)`; `seconds` `Known limit:`.
- `platform/research/domain/equity.py` -- running peak starts at `starting_balance`; `DrawdownEpisode.peak_ts` may be None (the starting balance).
- `platform/research/domain/returns.py` -- `from_equity` emits the first bucket's return against `starting_balance`.
- `platform/research/domain/correlation.py` -- `AlignedReturns` coerces inputs before validating.
- `platform/research/domain/trades.py` -- `qty` documented as peak position size.
- `platform/research/application/ranking_history.py` -- float64 metric columns; missing `items`/`ts` raise.
- `platform/research/application/ports.py` -- `RunSpec` rejects a `str` id list; `ts_init` vs `ts_event` edge note.
- `platform/kernel/catalog_files.py` -- precision-range and null-raw guards in `_index_rows`.
- `platform/tests/test_boundaries.py`, `platform/ARCHITECTURE.md`, `platform/research/BACKTESTING.md`, DDD `ARCHITECTURE-SPINE.md` -- candles allowlist (`newest_t`, `bucket_starts`).
- `platform/research/tests/test_{frames,equity,returns,correlation,ranking_history,backtest_runner}.py`, `platform/kernel/tests/test_catalog_files.py` -- a test per guard/behaviour change.

**Review:** 15 patches (medium 4, low 11), 2 deferred (crossed-book derived quotes; `snapshot_backtest.run`'s parallel semantics -- both pre-existing), 8 rejected (resample stamping is consistent with the domain's last-in-bucket convention; daily-return binning of flat days is Nautilus/kernel SSOT behaviour; a forming newest bucket is flagged `partial`; all-or-nothing sweep is DATA-07; `RunSpec` hashing, static-scanner looseness, empty-ledger message and a `None` realized PnL are speculative).

**Verification:** `research/tests tests/test_boundaries.py kernel/tests/test_catalog_files.py candles/tests/test_candle_store.py` -- 212 passed. Full platform suite -- `9 failed, 2115 passed`: all 9 are the baseline Redis-dependent `data_api` tests. ruff check + format clean on all touched files (run from the repo root); mypy clean on `research/application`, `research/domain`, `candles/application/queries.py` (only the pre-existing `kernel/catalog_files.py:129` error remains, line untouched).

**Follow-up review recommended:** false -- four medium fixes, each narrow and pinned by a new test; the only new cross-context surface is one read-only query service, already enforced by the boundary allowlist.

**Residual risks / Known limits:** `bars` now refuses any window spanning a collector outage (read around it); equity is balance-based, not marked to market; `seconds` materialises its whole window; one venue and one settlement currency per run; derived quotes pass crossed books through (deferred).
