---
title: 'Story 24.2 — `views/` read models for both UIs, and the reader-side re-validation removed'
type: 'refactor'
created: '2026-09-25'
status: 'done'
baseline_revision: 'f879c11ca3b5f823e0abdccc5fe0ddbba3bf1526'
final_revision: '40b726601ebfcd0e5a807dce77fb414716cc637a'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-24-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/24-2-views-read-models-and-reader-side-revalidation-removed.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The values both UIs show are computed in three places — `ml_signals` modules, inline
in `data_api/routes/*.py`, and in `bot_tui` — so the web UI and the TUI can drift; `data_api`
reaches straight into `candles`' SQLite adapter and `ranking_engine.metrics_store`; `bot_tui`
hand-indexes the `snapshots:raw` payload; and `data_api/routes/snapshots.py:129-133` silently drops
empty-top and crossed rows the capture gate accepted — the AD-3 deviation the parent spine tracks.

**Approach:** Create the flat `platform/views/` read-model context (CQRS query side), relocate the
listed `ml_signals`/`data_api` modules and every route/TUI computation into it, leave pure
re-export shims at the old paths, reduce `data_api` routes and `bot_tui` to format + transport,
delete the two reader-side skips (the gap marker survives as a rendering rule), and tighten
`tests/test_boundaries.py` so the new graph is enforced.

## Boundaries & Constraints

**Always:**
- Relocation, not rewrite: moved function bodies keep their logic verbatim except where a task
  says otherwise. Every `/api/*` and `/ws/live` response body stays byte-identical, except that
  `/api/snapshots` now returns crossed rows (the one intended behaviour change).
- Frozen (AD-D12): Parquet schemas, every Redis payload, the `chart_indicators.toml` /
  `screener_columns.toml` key sets and file text, `metrics.db`/`candles_<venue>.db` schemas, env
  vars (`CATALOG_PATH`, `CANDLES_DB_DIR`, `REDIS_URL`, `METRICS_DB_PATH`,
  `CHART_INDICATOR_CONFIG_PATH`, `SCREENER_COLUMNS_CONFIG_PATH`) with their defaults, compose
  service names, bind mounts.
- `views` is framework-free: no `fastapi`, `pydantic`, `urwid` import; it returns plain
  dicts/dataclasses and raises its own exceptions, which routes map to HTTP status codes.
- `views` holds no module-level runtime state: bus instances are constructed in `data_api`.
- `views` parses `snapshots:raw` only via `DydxSecondSnapshot.from_dict`.
- Every moved import path keeps a pure re-export shim (`REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"`)
  or a `_MOVED_NAMES` entry, defining nothing; every in-repo caller is repointed in the same
  commit (`tests/test_namespace.py`). Renamed successors are bound with `from x import new as old`.
- `platform/CLAUDE.md` DATA-01..08 (DATA-07: every continue-past-failure ledgers via
  `observability.error_ledger.record`), TEST-01..04, READ-03, SSOT-01..05, MEM-01..03, NAUT-01..03,
  FORK-01. Working dir `platform/`; a new `DeprecationWarning` in the run is a failure.

**Block If:**
- A frozen payload, TOML file text or response body cannot be preserved without a contract change
  (other than the sanctioned crossed-row change).
- The suite shows a regression that is not one of the ten pre-existing failures (dydx
  `trade_ohlc` ×5, `ofi_strategy` ×4, rankings redis ×1).

**Never:**
- Touch `nautilus_trader/` or `crates/`; write `sprint-status.yaml`.
- Re-introduce a reader-side data-quality skip anywhere in `views`, `data_api` or `bot_tui`.
- Let `views` import `data_api`, `bot_tui`, `ml_signals`, `collector_core`, `common`, or any
  `candles.infrastructure` module; widen `_exempt`; delete or loosen a failing test (a test whose
  asserted behaviour this story intentionally changes is rewritten to assert the new behaviour).
- Add a third seconds→bars fold, or recompute a ranking metric (pct-change, volatility) in `views`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Crossed row the gate wrote | catalog row with `bid_prices[0] >= ask_prices[0]` | `/api/snapshots` returns it with bid/ask/mid/micro/price computed exactly as for any row, counted toward `limit` | None |
| Empty-top row in the archive | a row with an empty `bid_prices` or `ask_prices` | request fails 500 naming the instrument and `ts_event` | `error_ledger.record("views.snapshot_without_top", ...)`; never skipped |
| Snapshot gap | two rows > 2500 ms apart | one all-`None` marker at `t = later - 1` between them | None |
| Bar gap | two kept bars > `bar_seconds` apart (candles, indicator series, indicator values) | one `{"t": earlier + bar_ms}` marker | None |
| Impossible candle | store/archive yields a candle failing `is_valid_candle` | 500, detail unchanged | `candles.invalid_candle` ledgered (site name unchanged) |
| TUI coin detail | `snapshots:raw` batch holding the open coin | stored as a `DydxSecondSnapshot`; ladder reads its attributes | an undecodable entry is ledgered at `views.snapshot_decode` and skipped |
| Preferences round-trip | load then save an existing `chart_indicators.toml` / `screener_columns.toml` | file text byte-identical to the pre-move writer's output | unchanged 400/500 mapping |

</intent-contract>

## Code Map

Moving into `views/`:
- `ml_signals/ranking_columns.py` (126) → `views/ranking_columns.py` verbatim.
- `ml_signals/chart_indicator_config.py`, `ml_signals/screener_columns_config.py` → `views/preferences.py`.
- `ml_signals/chart_indicators.py` (349), `ml_signals/custom_indicators.py` (369) → `views/indicator_picker.py`.
- `ml_signals/chart_data.py`, `book_features.py`, `footprint.py` → `views/chart_series.py`.
- `ml_signals/catalog_stats.py:92-125` `query_second_snapshots`, `data_api/routes/paging.py` → `views/catalog_reads.py`.
- `data_api/live_candles.py` → `views/live_candles.py`; `data_api/redis_bus.py` → `views/rankings_bus.py`.

Route/TUI computation to pull out (the audit — see Tasks):
- `data_api/routes/candles.py:67-209` — `_catalog_plus_recent`, `_window_start_ns`, `_insert_gap_markers`, `_checked`, `_parquet_page`, `_store_path`, `_store_page`, `candle_page`.
- `data_api/routes/snapshots.py:75,102-195` — `_snapshot_to_row_dict`, `_price_series_rows` (skips at 129-133), `_window_start_ns`, `_take_last_n_real_rows`, the `fetch` closure.
- `data_api/routes/indicator_series.py:79-160` — `_window_start_ns`, `_replay_bucket_samples`, `_insert_gap_markers`, `fetch`.
- `data_api/routes/indicators.py:86-110,210-300,332-393` — `_merged_indicator_catalog`, `_indicator_id`, `_replay_entry`, `_values_by_time`, `_insert_gap_markers`, window + page body of `get_indicator_values`.
- `data_api/routes/rankings.py:62-310` — `_CatalogReadError`, `_recent_candles`, `_read_candles`, `_latest_values`, `_TECHNICALS_*` data constants.
- `data_api/routes/metrics.py:83-90`, `data_api/app.py:108-143` — `metrics_store.history/nearest` reads, `_snapshot_to_dict`, `catalog_snapshots`, `catalog_chart_series`.
- `bot_tui/app.py:222-276,941-944` — `_COIN_DETAIL_INDICATOR_GROUPS`, hand-indexed ladder; `bot_tui/coin_detail.py:36-47` `rank_row_for`; `bot_tui/coin_detail_state.py:68-85` raw-dict row match.

Wiring and guards:
- `data_api/app.py` (lifespan, observers), `data_api/ws/live.py`, `data_api/alerts.py:47-48`, `data_api/settings.py`.
- `candles/application/queries.py` — add the read-only opener views may call.
- `tests/test_boundaries.py` — `LEGACY_*` tables, `COMPOSITION_ROOTS`, split-module map.
- Expired Story 23.2 shims (`REMOVE_AFTER`/`MOVED_NAMES_REMOVE_AFTER` = this story):
  `ml_signals/{performance_metrics,indicators,venue}.py`, `collector_core/{second_snapshot,venue_http,fold,open_interest}.py`,
  `common/` (package: `__init__.py` + `venues.py`), the `_MOVED_NAMES` blocks of
  `ml_signals/catalog_stats.py:34-69` and `collector_core/archive_gaps.py:46`.
- Docs: `ARCHITECTURE.md`, `CLAUDE.md` SSOT-01..05, `docs/DATA_DICTIONARY.md`,
  `frontend/src/pages/docs/{data,kbData}.ts`, parent spine
  `.../architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md:100,297`.

## Tasks & Acceptance

**Execution:**

- [x] `_bmad-output/implementation-artifacts/24-2-views-read-models-and-reader-side-revalidation-removed.md` Dev Notes -- write the audit list FIRST: every arithmetic/derivation in `data_api/routes/*.py`, `data_api/ws/live.py`, `data_api/app.py`, `bot_tui/*_state.py`, `bot_tui/*_pane.py`, `bot_tui/coin_detail.py`, `bot_tui/app.py`, one line each, `file:line — what — compute→views/<module> | format (why)`. The Code Map list above is the known compute set; staleness checks, width/ratchet/sparkline glyph math, PnL/elapsed text and HTTP param clamps are format/transport.
- [x] `views/__init__.py` -- context docstring in the `candles/__init__.py` idiom (charter: one function over one input per shown value; invariants: the reader never re-validates the gate, no module state, framework-free; dependency direction; `tests/test_boundaries.py` enforcement note). No code.
- [x] `views/ranking_columns.py` -- `ml_signals/ranking_columns.py` verbatim, plus the technicals read model from `routes/rankings.py`: `technicals_values(instrument_id, entries, now_ns, *, catalog_path, candles_dir) -> dict[str, float | None]` (body of `_latest_values`/`_recent_candles`/`_read_candles`), `CatalogReadError`, and the `_TECHNICALS_STORE_BARS/_BARS/_WIDE_BARS/_MAX_CANDLE_AGE_BARS`, `_FALLBACK_MAX_SPAN_S` constants. `entries` are plain objects with `name`, `params`, `bar_seconds`.
- [x] `views/preferences.py` -- `IndicatorEntry`, `ColumnEntry`, `DEFAULT_BAR_SECONDS`, `load_chart_indicators`/`save_chart_indicators` (was `chart_indicator_config.load_config/save_config`), `load_screener_columns`/`save_screener_columns` (was `screener_columns_config.load_config/save_config`); bodies verbatim, full-rewrite TOML. Docstring names both files and "key sets frozen (AD-D12)".
- [x] `views/indicator_picker.py` -- `chart_indicators.py` + `custom_indicators.py` merged verbatim; the two colliding public names get prefixes: `replay_native`/`native_catalog_json` (was `chart_indicators.replay_indicator/catalog_json`), `replay_custom`/`custom_catalog_json` (was `custom_indicators.replay_indicator/catalog_json`); every other name keeps its spelling. Add the dispatch moved out of `routes/indicators.py`: `merged_catalog() -> dict[str, dict]` (`{params, panel, category}`), `indicator_id(name, params)`, `replay_entry(candles, name, params, window)`, `values_by_time(candles, entries, window)`; collision `ValueError`s unchanged. `_second_snapshots` imports `views.catalog_reads.query_second_snapshots`.
- [x] `views/catalog_reads.py` -- `query_second_snapshots` verbatim from `catalog_stats`, and `fetch_page`/`has_older_data` verbatim from `data_api/routes/paging.py`.
- [x] `views/chart_series.py` -- `compute_chart_series` (chart_data), all of `book_features`, all of `footprint` (`Candle` from `candles.domain.candle`), plus: `with_gap_markers(rows, bar_seconds)` (the one bar-spaced rule replacing the three route copies, marker `{"t": prev_t + bar_ms}`); `SNAPSHOT_GAP_THRESHOLD_MS = 2500` and `price_series_rows(snapshots)` over `DydxSecondSnapshot` attributes (microprice via `kernel.indicators.microprice(snapshot.to_dict())`), **without** the empty-top and `bp >= ap` skips, raising `EmptyTopOfBook` after ledgering `views.snapshot_without_top` on an empty side; `snapshot_series_page(catalog_path, iid, before_ns, limit)`; `candle_page(iid, before_ns, limit, bar_seconds, *, catalog_path, candles_dir, recent_rows)` with `ImpossibleCandle` (ledger site `candles.invalid_candle` unchanged) and store reads through `candles.application.queries` only (the `BAR_SECONDS` pre-check is dropped: a width the store does not keep yields an empty window and no coverage, the same result); `indicator_series_page(catalog_path, iid, before_ns, limit, bar_seconds)`; `indicator_values_page(iid, before_ns, limit, bar_seconds, entries, *, catalog_path, candles_dir, recent_rows)`. Each page returns `(rows, has_more)` of dicts. Internal helpers get distinct names (no two `_window_start_ns`).
- [x] `views/coin_detail.py` -- `COIN_DETAIL_GROUPS` (was `bot_tui/app.py`'s `_COIN_DETAIL_INDICATOR_GROUPS`, with `MIN_INDICATOR_DECIMALS`), `rank_row_for` (from `bot_tui/coin_detail.py`), `snapshot_for(batch, instrument_id) -> DydxSecondSnapshot | None` (decodes each entry with `from_dict`, ledgers `views.snapshot_decode` per undecodable entry), `metrics_history(symbol, db_path, days)` / `metrics_nearest(symbol, ts_ns, db_path)` over `ranking_engine.metrics_store.history/nearest`, `catalog_snapshot_rows(catalog_path, iid, start_ns, end_ns)` (app.py's `_snapshot_to_dict` projection).
- [x] `views/live_candles.py` -- `data_api/live_candles.py` verbatim except: `LiveCandleBus(catalog_path: str)` replaces the `data_api.settings` read (module-level `query_second_ohlc` name kept so tests patch it), `QUEUE_MAX`/`put_drop_oldest` from `views.rankings_bus`, the module-level `live_candle_bus` instance removed. Declare `BarObserver(Protocol)`: `on_bar(instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None`, docstring naming its invariant (alerting sees the same forming bar the chart sees, without importing views' internals), that Story 24.3 attaches `AlertEngine`, and a `Known limit:` that `bar` is the frozen `{t,o,h,l,c,v}` dict of `forming_bar` (upgrade path: a Nautilus `Bar` once views owns the wire format).
- [x] `views/rankings_bus.py` -- `data_api/redis_bus.py` verbatim minus `REDIS_URL` and the `bus` instance.
- [x] `candles/application/queries.py` -- add `open_store(candles_dir, venue)`: a context manager yielding `connect_ro(db_path_for_venue(candles_dir, venue))`'s connection or `None`, so readers never import `candles.infrastructure`; note it under the module's existing layering `Known limit:`.
- [x] `data_api/settings.py` -- add `REDIS_URL` (same env var and default as `redis_bus.py:40`).
- [x] `data_api/buses.py` -- new composition module: `bus = RankingsBus()`, `live_candle_bus = LiveCandleBus(settings.CATALOG_PATH)`. Docstring: the single process-wide instances, constructed here because `views` holds no module state.
- [x] `data_api/app.py`, `data_api/ws/live.py`, `data_api/alerts.py`, `data_api/routes/{candles,snapshots,indicator_series,indicators,rankings,metrics}.py` -- call the `views` functions above, passing each route module's own `CATALOG_PATH`/`CANDLES_DB_DIR`/`METRICS_DB_PATH`/config path at call time (so existing test monkeypatches keep working) and `buses.live_candle_bus.recent_rows`; keep only pydantic models, param clamps, `_parse_entries`, the technicals TTL cache, and exception→HTTP mapping (`ImpossibleCandle`/`EmptyTopOfBook`→500, `CatalogReadError` per-coin error). Routes/app reference `buses.bus`/`buses.live_candle_bus` through the module so tests can monkeypatch them. No `ml_signals`, `ranking_engine`, `candles`, `collector_core`, `common` import remains in `data_api` (tests excepted per the boundary task).
- [x] `bot_tui/coin_detail_state.py`, `bot_tui/app.py`, `bot_tui/coin_detail.py`, `bot_tui/coins_pane.py` -- `_handle_snapshot_batch` stores `views.coin_detail.snapshot_for(batch, _CURRENT_INSTRUMENT_ID)` (no raw-dict indexing); the ladder reads `snapshot.bid_prices` etc.; `_COIN_DETAIL_INDICATOR_GROUPS` and `rank_row_for` come from `views.coin_detail`; `coins_pane` imports `views.ranking_columns`.
- [x] Shims (`REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"`, 24.1 `ml_signals/candles.py` idiom): whole-module re-exports at `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `data_api/{live_candles,redis_bus}.py`, `data_api/routes/paging.py`. Instances and env constants that no longer exist there (`bus`, `live_candle_bus`, `REDIS_URL`) go in `_REPLACED_NAMES`, naming `data_api.buses`/`data_api.settings`. `ml_signals/catalog_stats.py`: replace the expired 23.2 `_MOVED_NAMES` with `{"query_second_snapshots": "views.catalog_reads.query_second_snapshots"}` under the new expiry; delete `overview_table` and add it to `_REPLACED_NAMES` (see Design Notes). `bot_tui/coin_detail.py`: `_MOVED_NAMES = {"rank_row_for": "views.coin_detail.rank_row_for"}` with `MOVED_NAMES_REMOVE_AFTER` and a warning `__getattr__` (the `catalog_stats` idiom).
- [x] Expired 23.2 shims -- delete the files and `_MOVED_NAMES` blocks listed in the Code Map (and the `common` package, its two dockerfile `COPY` lines and `LEGACY_PACKAGES`/map entries); repoint anything that still names them (e.g. `kernel/tests/test_venues.py`, `kernel/tests/test_pre_move_fixtures.py`, `live_paper/__init__.py` prose, `collector_core/crosscheck_errors.py`).
- [x] `tests/test_boundaries.py` -- map `views.*` implicitly (context package) and the new shims; delete `(DATA_API, CANDLES)`, `(DATA_API, RANKING)`, `(VIEWS, DATA_API)` and the three 24-2 private-import entries; add `(RESEARCH, VIEWS)` until `24-4-research-pure-consumer-and-broken-tests-repaired` (the one newly judged edge: `ml_signals.tests.test_ofi_strategy_indicator_consistency` → `book_features.top_of_book_series`, previously intra-package); remap `("ml_signals.catalog_stats", "list_instruments")` → `RANKING`, `("ml_signals.catalog_stats", "__getattr__")` → `VIEWS` (it now serves a views name), and drop the `overview_table`/`query_second_snapshots` split entries; register `data_api.tests.{test_candles,test_screener_columns}` → `{CANDLES}` and `data_api.tests.{test_data_api,test_metrics}` → `{RANKING}` in `COMPOSITION_ROOTS` (they seed the upstream store through its only writer); add `VIEWS_QUERY_SERVICES: dict[str, frozenset[str]]` = `candles.application.queries`: `{window, oldest_t, candle_dicts_for_window, open_store}`, `candles.application.forming`: `{forming_bar, bars_from_rows}`, `candles.domain.candle`: `{Candle, is_valid_candle}`, `ranking_engine.metrics_store`: `{history, nearest}` with tests that a non-test `views` module imports from `candles`/`ranking` only those names (and every listed name is still used); a test that no non-test `data_api` or `bot_tui` module imports `ml_signals`, `collector_core`, `ranking_engine`, `candles` or `common`; a test that no production module outside `kernel` reads a snapshot payload by key (`x["bid_prices"]`/`.get("bid_prices")` over the `DydxSecondSnapshot` book/trade field names), with `LEGACY_SNAPSHOT_INDEXING_UNTIL = {"ranking_engine.engine": "25-2-ranking-context-rankingboard-replaces-module-globals"}` expiring like the other tables. Replace `candles.tests.test_candle_store._second/_DAY0_MS` imports in `data_api/tests` with local helpers.
- [x] `views/tests/` -- `__init__.py` (LGPL header) plus the tests of moved code moved here and repointed: `ml_signals/tests/test_{book_features,chart_data,chart_indicator_config,chart_indicators,custom_indicators,footprint}.py`, the `query_second_snapshots` part of `test_catalog_stats.py`, `data_api/tests/test_live_candles.py`; new tests for the I/O matrix rows (crossed row priced like any row, empty-top ledger + raise, snapshot gap marker with the existing gap test moved here, bar-gap rule, `snapshot_for` decode/skip, preferences byte-identical round-trip against recorded file text, `BarObserver` satisfied structurally by a test double).
- [x] `data_api/tests/test_snapshots.py` -- replace `test_crossed_book_row_is_skipped_and_not_counted_toward_limit` with `test_crossed_row_written_by_the_gate_is_returned_unchanged` (through `/api/snapshots`, asserting bid/ask as written and that it counts toward `limit`), and add the empty-top 500 case. Repoint every other test file's imports/monkeypatch targets (`buses.bus`, `views.*` attributes) without weakening an assertion.
- [x] `collector_core/repair_catalog.py` and the capture/archive tests importing `query_second_snapshots` -- repoint to `views.catalog_reads` (legacy `ARCHIVE→VIEWS`/`CAPTURE→VIEWS` edges stay until 25-1/26-2).
- [x] `collector.dockerfile`, `data_api.dockerfile` -- `COPY platform/views ./views`; `Makefile` `test` list adds `views/tests` (not `test-live-paper`: `live_paper` imports no views code, same reasoning as 24.1's candles).
- [x] Docs, same commit -- `ARCHITECTURE.md` (views row, module table, reader list, the AD-3 deviation now gone), `CLAUDE.md` SSOT-01..05 cite `views/` as the one place (and any `ml_signals.ranking_columns`/`chart_indicators` citation), `docs/DATA_DICTIONARY.md` paths, `frontend/src/pages/docs/{data,kbData}.ts` name `views/` where they name the moved modules, parent spine: strike the Deferred entry at `:297` and the AD-3 "Known deviation" at `:100` with `[amended 2026-09-25: Story 24.2 — skips deleted; gap marker kept as views.chart_series rendering rule]`. Story file: tick tasks, Completion Notes stating the chart behaviour change (crossed rows now render), and the deviations recorded in Design Notes.

**Acceptance Criteria:**
- Given a crossed row written to the catalog, when `/api/snapshots` serves its window, then the row is present with its written bid/ask and is counted toward `limit`.
- Given `make test`'s list (`views/tests` included), when it runs, then `test_boundaries.py`, `test_images.py` and `test_namespace.py` pass, no `DeprecationWarning` is raised, and the only failures are the ten pre-existing ones.
- Given `platform/`, when searched, then no non-test `data_api`/`bot_tui` module imports `ml_signals`, `collector_core`, `ranking_engine`, `candles` or `common`, and no module outside `kernel` (bar the listed legacy one) indexes a snapshot payload by key.
- Given each pre-existing route test, when run against the moved code, then it passes with its assertions unchanged apart from import paths and monkeypatch targets (the crossed-row test excepted, replaced as above).

## Spec Change Log

## Review Triage Log

### 2026-09-25 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 2, low 3)
- defer: 1: (high 0, medium 1, low 0)
- reject: 16
- addressed_findings:
  - `[medium]` `[patch]` `views.chart_series.compute_chart_series` (legacy `/catalog/chart-series`) kept the moved-verbatim empty-top and crossed skips behind a `Known limit:`, contradicting the contract's "never re-introduce a reader-side skip in views". Deleted: a crossed second is fed through as written (negative spread), an empty top raises `EmptyTopOfBook` (ledgered, 500 from `data_api/app.py`); `test_compute_chart_series_skips_crossed_snapshot` rewritten to the new behaviour plus an empty-top test. (Found during step-03 verification, before the reviewers ran.)
  - `[medium]` `[patch]` Docstrings, tests, `docs/DATA_DICTIONARY.md` §2.7 called a crossed row "data the gate accepted (DATA-04)", but today's gate skips crossed seconds (`_handle_crossed_book` returns True and ledgers `collector.crossed_book`). Reworded everywhere to the actual rationale: a crossed row the archive holds (pre-gate days or a capture bug) is representable and rendered as written — its fix is at the gate or via `repair_catalog`, never a reader filter (AD-3) — while an empty-top row cannot be drawn at all and fails loudly rather than fabricating a value.
  - `[low]` `[patch]` `ARCHITECTURE.md` still said `compute_chart_series` keeps its skip; corrected.
  - `[low]` `[patch]` `ml_signals/catalog_stats.py` imported `views` at module top, so `ranking_engine` (which imports that module in production) loaded the views context for a name it never reads; the shim's successor is now imported inside `__getattr__` (a literal import, so `tests/test_images.py` still sees the closure).
  - `[low]` `[patch]` `ml_signals/BACKTESTING.md` and the frontend KB told strategy authors to reuse `views/chart_series.py`, an edge research may not take (tolerated only until 24.4); reworded. `views.chart_series.top_of_book_series`' crossed skip documented as the gate of a raw-delta replay (no capture gate upstream), not the AD-3 reader re-check.

Rejected, with reasons: the empty-top rule in `indicator_series_page` (an existing route test pins honest `None` microprice/spread for an empty book — nothing skipped or invented — and the contract requires pre-existing assertions unchanged; a patch was tried and reverted); the crossed row's inverted CVD skew and the empty-top 500's page-wide blast radius (both the contract's I/O matrix); `BarObserver` not yet dispatched (Story 24.3 wires it, per spec); the `data_api.redis_bus` shim raising for `bus`/`REDIS_URL` (Design Notes: views may not import `data_api`); the dropped `BAR_SECONDS` pre-check (spec decision; a corrupt store is loud either way); `buses.py` binding `CATALOG_PATH` at import (production-equivalent); `snapshot_for` decoding every entry and `to_dict` allocation cost (AC forbids key-indexing to pre-filter; tens of rows per second); level-length mismatch in the TUI ladder, `CandleReadError`'s broad wrap, `indicator_picker`'s env `CATALOG_PATH` and `metrics_store._conn`'s read-write open (all pre-existing, unchanged by the move); the empty-top-at-`before_ns` boundary 500 (a malfunction row the next page would fail on anyway; filtering first would change gap-marker output).

### 2026-09-25 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 0, medium 0, low 2)
- defer: 0
- reject: 9
- addressed_findings:
  - `[low]` `[patch]` `docs/DATA_DICTIONARY.md` §2.7 (edited by this story) still described `compute_chart_series` as an `OrderBookDelta`/`TradeTick` replay emitting OFI, cancel pressure, candles and EMAs; rewritten to what it computes (per-second microprice, spread, imbalance, mid_imbalance, bid/ask depth over archived snapshots, `EmptyTopOfBook` on an empty top).
  - `[low]` `[patch]` `dydx_collector/notebooks/dydx_catalog_pandas.ipynb` was repointed to `views.chart_series.top_of_book_series`, a research→views edge the `.py`-only boundary scan cannot see; the `(RESEARCH, VIEWS)` legacy-edge comment in `tests/test_boundaries.py` now names it so 24-4 repoints it before deleting the entry, and the notebook's prose no longer cites `book_features`.

Rejected, with reasons: `snapshot_for` ledgering another coin's undecodable entry every second (spec mandates decode-and-ledger per entry; an undecodable entry is a real malfunction DATA-07 wants recorded); the `data_api.live_candles` shim re-exporting a `LiveCandleBus` whose constructor now takes `catalog_path` (spec-mandated signature, no in-repo caller); `_HISTORY_ONLY_COLS` absent from the `ml_signals.ranking_columns` shim (private name, no importer; docs cite `views/ranking_columns.py`); bot_tui's import weight via `views.ranking_columns` (measured: bot_tui already loads nautilus/pyarrow through the spec-mandated `DydxSecondSnapshot`/`catalog_reads`; technicals placement is spec-mandated); `views/__init__.py` dependency prose (already lists `candles.domain.candle`); an empty-top row anywhere in the fetch window failing the page, historical empty-top rows 500-ing permanently, the dropped `BAR_SECONDS` pre-check, and `buses.py` binding `CATALOG_PATH` at import (all already judged in the previous pass / the I/O matrix).

## Design Notes

**Framework-free views, routes keep HTTP.** Views returns dicts shaped exactly like today's
response items; the route builds the same pydantic models, so JSON output is unchanged. Routes
pass their own module constants at call time (`catalog_path=CATALOG_PATH`) instead of views reading
env, which keeps AD-D12's env contract in `data_api` and every existing monkeypatch valid.

**Why views calls `candles.application.queries.open_store`.** AC #3 limits views to candles/ranking
query services; `connect_ro`/`db_path_for_venue` are infrastructure, which the spine reserves for
composition roots. A one-line application-layer opener keeps the edge at the query-service layer.
`VIEWS_QUERY_SERVICES` is the enforced upper bound; it names what the move actually needs rather
than the spine's illustrative label (`latest`, `verified_status` are unused by views, so unlisted).

**`overview_table` is deleted, not moved.** It has no caller since Story 15.10 retired the dashboard,
and it recomputes `price_stats` per instrument — the pct-change/volatility math AD-D10 forbids views
to recompute. Moving it would plant that violation; `_REPLACED_NAMES` points a stale caller at
`rankings:live`/`metrics.db`. `list_instruments` therefore stays in `catalog_stats` for its only
caller, ranking's `metrics_computer`, and is remapped to `RANKING`.

**Empty top of book is a loud failure, not a skip.** The gate (`collector.py:1227`) never writes a
one-sided book, so such a row is a malfunction (DATA-07): ledger it and fail the request, the same
treatment `_checked` already gives an impossible candle. A crossed row, by contrast, is data the
gate accepted and is rendered.

**Bus instances live in `data_api/buses.py`.** `views` may not read `data_api.settings` and may hold
no module state; the composition module constructs both buses once. The shim therefore cannot serve
`bus`/`live_candle_bus` and lists them in `_REPLACED_NAMES`.

**Merged modules rename only on collision.** `chart_indicators`+`custom_indicators` and the two
config modules each define `replay_indicator`/`catalog_json` or `load_config`/`save_config`; only
those get prefixed names, and the shims bind them back under the old names.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: only the ten pre-existing failures, and no `DeprecationWarning` in the warnings summary.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. tests/test_boundaries.py tests/test_images.py tests/test_namespace.py -q` -- expected: all pass.
- `cd platform && ruff format --check . && ruff check . && mypy views data_api bot_tui candles` -- expected: clean (no new errors versus baseline).
- `cd platform && grep -rnE 'if bp >= ap|not s\["bid_prices"\]' --include='*.py' views data_api bot_tui` -- expected: no hits.


## Auto Run Result

Status: done

### Summary of implemented change

Story 24.2 (implemented at `6a53169bb0`) created the flat `platform/views/` read-model context
(`ranking_columns`, `coin_detail`, `chart_series`, `indicator_picker`, `preferences`,
`catalog_reads`, `live_candles`, `rankings_bus`), reduced `data_api` routes and `bot_tui` to
format + transport, deleted the reader-side empty-top/crossed skips (crossed seconds now render;
an empty-top second fails loudly at `views.snapshot_without_top`), left re-export shims expiring
after 24-4, deleted the expired 23.2 shims, and tightened `tests/test_boundaries.py`. This
follow-up pass re-reviewed the whole diff from `f879c11ca3` and applied two low documentation
patches.

### Files changed (this pass)

- `platform/docs/DATA_DICTIONARY.md` — §2.7 `compute_chart_series` paragraph matches the code.
- `platform/tests/test_boundaries.py` — `(RESEARCH, VIEWS)` comment names the notebook edge the scan cannot see.
- `platform/dydx_collector/notebooks/dydx_catalog_pandas.ipynb` — prose cites `views.chart_series.top_of_book_series`.

### Review findings breakdown

- Patches applied: 2 (low).
- Deferred: 0.
- Rejected: 9 (spec-mandated behaviour, already-judged items, or not real).

### Verification

- `python3 -m pytest -o addopts="" --rootdir=. tests/test_boundaries.py tests/test_images.py tests/test_namespace.py views/tests -q`: 300 passed.
- `uvx ruff format --check` / `uvx ruff check` on `tests/test_boundaries.py`: clean. Notebook JSON re-parsed OK.
- The full suite was not re-run: this pass changed only a comment, a Markdown paragraph and notebook prose.

### Residual risks

- Historical empty-top seconds in the archive make chart windows covering them return 500 until repaired (intended loud failure).
- `BarObserver` is not dispatched until Story 24.3.
- The notebook's research→views import is tracked only by a comment until 24-4.
