# Story 24.2: `views/` read models for both UIs, and the reader-side re-validation removed

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 24). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 24.2".

## Story

As a trader reading the web UI and the TUI,
I want every number both surfaces show to come from one function over one input, and the chart to render what the gate approved,
So that the two UIs can never disagree and the reader never second-guesses the gate.

## Acceptance Criteria

1. **Given** `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `data_api/{live_candles,redis_bus}.py`, the `catalog_stats` series reads (`query_second_snapshots`, `query_second_ohlc`, `second_ohlc_arrays`, `overview_table`) and the inline computations in `data_api/routes/*.py` and `bot_tui/*_state.py`
**When** the story ships
**Then** `platform/views/` holds `ranking_columns.py`, `coin_detail.py`, `chart_series.py`, `indicator_picker.py`, `live_candles.py` (declaring the `BarObserver` `Protocol` and keeping `LiveCandleBus`), `rankings_bus.py`, `preferences.py` (the one loader/saver for `chart_indicators.toml` and `screener_columns.toml`, full-rewrite TOML, key sets frozen) and the catalog series reads over `kernel.catalog_files`; an audit list in the story's Dev Notes names every computation found in `data_api/routes` and `bot_tui` state modules and each is moved into `views/` or shown to be pure formatting; `views` parses `snapshots:raw` only through `DydxSecondSnapshot.from_dict`, and `bot_tui`'s hand-indexed parsing is replaced by the same call

2. **Given** `data_api/routes/snapshots.py:129-133` skips empty-top and `bp >= ap` rows (the AD-3 deviation) and `:137-138` inserts gap markers
**When** the story ships
**Then** the two skips are deleted, the gap-marker insertion (`_SNAPSHOT_GAP_THRESHOLD_MS`) moves into `views/chart_series.py` as a rendering rule with its existing test, a test proves a crossed row written by the gate is returned unchanged by `/api/snapshots`, and the parent spine's Deferred entry "Reader-side crossed-book skip survived the `dashboard` → `data_api` move" is struck with an amendment

3. **Given** AD-D2's graph
**When** `test_boundaries.py` runs after the move
**Then** `views` imports only `kernel`, `observability` and the `candles`/`ranking` query services (`window`, `latest`, `forming_bar`, `verified_status`, `history`, `nearest`), `data_api` and `bot_tui` import `views`, `kernel`, `observability` and `alerting`'s application service only, and no `data_api` route or `bot_tui` module imports `ml_signals`, `collector_core`, `ranking_engine` or `common` directly

4. **Given** MR2 and MR4
**When** the story is merged
**Then** the moved `ml_signals` and `data_api` modules are pure re-export shims with `REMOVE_AFTER = "24-4-..."`, the `data_api` and collector images `COPY` `views`, the Makefile test lists include `views/tests`, the frontend's docs page (`frontend/src/pages/docs/`) and `ARCHITECTURE.md` name `views/`, and SSOT-01..05 in `platform/CLAUDE.md` cite `views/` as the one place

## Tasks / Subtasks

- [x] Task 1 — audit, then move (AC: #1)
  - [x] Write the audit list first (Dev Notes): every arithmetic/derivation in `data_api/routes/*.py`, `data_api/ws/live.py`, `bot_tui/*_state.py`, `bot_tui/*_pane.py` (grep for `float(`, `sum(`, `max(`, `/`, `*`, indicator calls). Classify each as *compute* (moves to `views/`) or *format* (stays).
  - [x] Create `platform/views/`: `ranking_columns.py` (+ `screener_columns_config` merged into `preferences.py`), `coin_detail.py` (the per-coin metric set both UIs show — from `routes/indicators.py`, `bot_tui/coin_detail_state.py`), `chart_series.py` (`chart_data.py`, `book_features.py`, `footprint.py`, the series reads over `kernel.catalog_files`, the gap-marker rule), `indicator_picker.py` (`chart_indicators.py`, `custom_indicators.py`, `chart_indicator_config.py`), `live_candles.py` (`LiveCandleBus` + `BarObserver` `Protocol`: `on_bar(instrument_id, bar_seconds, bar: Bar, ts_ns) -> None`), `rankings_bus.py` (`redis_bus.py`), `preferences.py` (load/save `chart_indicators.toml`, `screener_columns.toml`; key sets asserted unchanged).
  - [x] `snapshots:raw` parsed only via `DydxSecondSnapshot.from_dict` in `views/` and in `bot_tui` (`coin_detail_state.py`, `ranking_state.py`); grep test `snap\[` outside kernel.
- [x] Task 2 — remove the reader-side re-validation (AC: #2)
  - [x] Delete the two skips at `data_api/routes/snapshots.py:129-133`; move gap insertion (`_SNAPSHOT_GAP_THRESHOLD_MS`, `:75,137-138`) into `views/chart_series.py`; test: a crossed row round-trips through `/api/snapshots`; strike the parent Deferred entry.
- [x] Task 3 — boundary (AC: #3)
  - [x] Remove the now-illegal legacy edges from `LEGACY_EDGES_UNTIL` for this story; `data_api` and `bot_tui` import only `views`, `kernel`, `observability`, `alerting.application` (alerting is still `data_api/alerts.py` until 24.3 — keep its legacy edge until then).
- [x] Task 4 — shims, images, lists, docs (AC: #4)
  - [x] Shims (`REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"`); `data_api.dockerfile` + `collector.dockerfile` `COPY platform/views ./views`; Makefile lists add `views/tests`; frontend docs page data (`frontend/src/pages/docs/{data,kbData}.ts`) and `ARCHITECTURE.md` name `views/`; `platform/CLAUDE.md` SSOT-01..05 cite `views/`.

## Dev Notes

Views is CQRS's query side: it may read every store read-only and call the query services of candles/ranking, and it is where SSOT-01..05 become code. Do the audit list before moving anything — the point is that no computation is left in `data_api/routes` or `bot_tui`. The reader-side skips at `snapshots.py:129-133` are the AD-3 deviation the parent spine tracks; deleting them is a behaviour change on the chart (crossed rows now render) and must be stated in Completion Notes.

### Audit list (written before the move, against baseline `f879c11ca3`)

Every arithmetic/derivation found in `data_api/routes/*.py`, `data_api/ws/live.py`, `data_api/app.py`, `bot_tui/*_state.py`, `bot_tui/*_pane.py`, `bot_tui/coin_detail.py` and `bot_tui/app.py`. `compute→views/<module>` moves; `format (why)` stays in the interface adapter.

- `data_api/routes/candles.py:79-84` — `_catalog_plus_recent`, catalog rows ∪ live unflushed tail — compute→views/chart_series (`candle_page`'s archive reader)
- `data_api/routes/candles.py:106-111` — `_window_start_ns`, query-span arithmetic (3× multiplier, 1 h floor, 7 d cap) — compute→views/chart_series
- `data_api/routes/candles.py:114-127` — `_insert_gap_markers`, bar-spaced gap marker — compute→views/chart_series (`with_gap_markers`)
- `data_api/routes/candles.py:130-137` — `_checked`, impossible-candle canary + ledger — compute→views/chart_series (`ImpossibleCandle`; the route maps it to 500)
- `data_api/routes/candles.py:140-165` — `_parquet_page`, archive fold page + `has_more` — compute→views/chart_series
- `data_api/routes/candles.py:168-169` — `_store_path`, per-venue store path — compute→views (through `candles.application.queries.open_store`)
- `data_api/routes/candles.py:172-198` — `_store_page`, store page + coverage start — compute→views/chart_series
- `data_api/routes/candles.py:201-220` — `candle_page`, store/archive merge — compute→views/chart_series (`candle_page`)
- `data_api/routes/candles.py:230-231` — `limit`/`bar_seconds` clamps — format (HTTP param clamp)
- `data_api/routes/candles.py:233-245` — venue/market fields — format (a `kernel.venues` call)
- `data_api/routes/snapshots.py:75` — `_SNAPSHOT_GAP_THRESHOLD_MS` — compute→views/chart_series (`SNAPSHOT_GAP_THRESHOLD_MS`)
- `data_api/routes/snapshots.py:96-112` — `_snapshot_to_row_dict`, snapshot→dict projection — compute→views/chart_series (deleted: `price_series_rows` reads `DydxSecondSnapshot` attributes)
- `data_api/routes/snapshots.py:115-161` — `_price_series_rows`, mid / microprice / CVD-weighted price / gap marker, with the empty-top and `bp >= ap` skips at 129-133 — compute→views/chart_series (`price_series_rows`, both skips deleted)
- `data_api/routes/snapshots.py:164-166` — `_window_start_ns` — compute→views/chart_series
- `data_api/routes/snapshots.py:169-187` — `_take_last_n_real_rows` — compute→views/chart_series
- `data_api/routes/snapshots.py:193-207,217` — `before_ms`, `fetch` closure, page walk, `has_more` — compute→views/chart_series (`snapshot_series_page`)
- `data_api/routes/snapshots.py:192` — `limit` clamp — format (HTTP param clamp)
- `data_api/routes/indicator_series.py:79-81` — `_window_start_ns` — compute→views/chart_series
- `data_api/routes/indicator_series.py:84-126` — `_replay_bucket_samples`, OFI/OBI replay + microprice/spread per bucket (kernel functions) — compute→views/chart_series
- `data_api/routes/indicator_series.py:129-144` — `_insert_gap_markers` — compute→views/chart_series (`with_gap_markers`)
- `data_api/routes/indicator_series.py:155-172` — `fetch` closure, page walk, `has_more` — compute→views/chart_series (`indicator_series_page`)
- `data_api/routes/indicator_series.py:152-153` — clamps — format (HTTP param clamp)
- `data_api/routes/indicators.py:86-110` — `_merged_indicator_catalog`, native+custom merge + collision check — compute→views/indicator_picker (`merged_catalog`)
- `data_api/routes/indicators.py:131-150,153-202` — config GET/PUT — format (transport over `views.preferences`; 400/500 mapping)
- `data_api/routes/indicators.py:226-234` — `_indicator_id`, series key — compute→views/indicator_picker (`indicator_id`)
- `data_api/routes/indicators.py:237-257` — `_parse_entries` — format (untrusted query param → 400)
- `data_api/routes/indicators.py:260-282` — `_replay_entry`, native/custom dispatch — compute→views/indicator_picker (`replay_entry`)
- `data_api/routes/indicators.py:285-311` — `_values_by_time`, per-entry replay merge — compute→views/indicator_picker (`values_by_time`)
- `data_api/routes/indicators.py:314-329` — `_insert_gap_markers` — compute→views/chart_series (`with_gap_markers`)
- `data_api/routes/indicators.py:369-393` — window bounds (`start_ms`/`end_ms`), `ReplayWindow`, values page — compute→views/chart_series (`indicator_values_page`)
- `data_api/routes/indicators.py:356-358,360-367` — clamps, catalog-read 500 mapping — format
- `data_api/routes/rankings.py:62-81` — `get_rankings`, `ranks`→`items` relay — format (verbatim relay)
- `data_api/routes/rankings.py:88-98` — `_TECHNICALS_STORE_BARS/_BARS/_WIDE_BARS/_MAX_CANDLE_AGE_BARS`, `_FALLBACK_MAX_SPAN_S` — compute→views/ranking_columns
- `data_api/routes/rankings.py:92,100-104` — `_TECHNICALS_BAR_SIZES`, TTL cache — format (HTTP validation, transport cache)
- `data_api/routes/rankings.py:107-111` — `_CatalogReadError` — compute→views/ranking_columns (`CatalogReadError`)
- `data_api/routes/rankings.py:125-186` — technicals columns GET/PUT + validation — format (transport over `views.preferences`)
- `data_api/routes/rankings.py:199-217` — `_recent_candles`, store-then-archive read — compute→views/ranking_columns
- `data_api/routes/rankings.py:220-235` — `_read_candles`, archive fold window — compute→views/ranking_columns
- `data_api/routes/rankings.py:238-273` — `_latest_values`, age gate + per-width replay + key remap — compute→views/ranking_columns (`technicals_values`)
- `data_api/routes/rankings.py:276-310` — `get_technicals_values` — format (param validation, cache, per-coin error mapping)
- `data_api/routes/metrics.py:83-84` — `metrics_store.history` read — compute→views/coin_detail (`metrics_history`)
- `data_api/routes/metrics.py:88-90` — `metrics_store.nearest` read — compute→views/coin_detail (`metrics_nearest`)
- `data_api/app.py:73-80,85` — lifespan task wiring, observer attach — format (composition root)
- `data_api/app.py:88-95` — `/metrics/history|nearest` — compute→views/coin_detail
- `data_api/app.py:98-100` — `catalog_chart_series` — compute→views/chart_series (`compute_chart_series`)
- `data_api/app.py:103-122` — `_snapshot_to_dict` projection + `catalog_snapshots` — compute→views/coin_detail (`catalog_snapshot_rows`)
- `data_api/app.py:163-179` — `/api/errors` — format (observability relay)
- `data_api/ws/live.py:55-71` — `_parse_candle_channel`, channel text + `bar_seconds` bound — format (client control-message validation)
- `data_api/ws/live.py:74-80,97-139,142-227` — forwarders, per-connection subscriptions, control dispatch — format (transport)
- `bot_tui/app.py:222,238-277` — `_MIN_INDICATOR_DECIMALS`, `_COIN_DETAIL_INDICATOR_GROUPS` (which values coin detail shows, at what precision) — compute→views/coin_detail (`COIN_DETAIL_GROUPS`, `MIN_INDICATOR_DECIMALS`)
- `bot_tui/app.py:899-903` — rank-row lookup — compute→views/coin_detail (`rank_row_for`)
- `bot_tui/app.py:916-936` — label/value widths, ratchet — format (width math)
- `bot_tui/app.py:941-944` — hand-indexed `snapshot["bid_prices"]` etc. — compute→views/coin_detail (`snapshot_for` decodes via `DydxSecondSnapshot.from_dict`; the ladder reads attributes)
- `bot_tui/app.py:948-989` — ladder levels, box widths — format
- `bot_tui/app.py:1026-1030` — coin-detail stale badge — format (staleness check)
- `bot_tui/coin_detail.py:39-50` — `rank_row_for` — compute→views/coin_detail
- `bot_tui/coin_detail.py:53-66,76-94` — `format_indicator`, `ratchet_width` — format
- `bot_tui/coin_detail.py:97-150` — `order_book_lines` — format (mid via `kernel.indicators.mid_price`, SSOT-01's one function)
- `bot_tui/coin_detail_state.py:68-85` — `_handle_snapshot_batch`, raw-dict `row.get("instrument_id")` match — compute→views/coin_detail (`snapshot_for`)
- `bot_tui/coins_pane.py:57-186` — `fit_instrument_id`, `coin_rows`, `stale_feed_banner_text`, `filter_rows`, `coin_header_text`, `format_coin_row` — format (column metadata from `views.ranking_columns`)
- `bot_tui/ranking_state.py:97` — `is_stale` — format (staleness check)
- `bot_tui/bots_pane.py:111,124,212,254-260` — PnL text, elapsed, trade timestamp, sparkline glyphs — format
- `bot_tui/{bots,bot_history,bot_incidents,collector}_state.py`, `bot_tui/collector_pane.py` — no arithmetic beyond staleness/format — format

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

- Spine sections: AD-D11, AD-D3 (from_dict), AD-D2
- Review findings that shaped this story: reviews/review-adversary.md C4, M1, M8; parent spine AD-3 + Deferred 'Reader-side crossed-book skip'
- Code: `data_api/routes/snapshots.py:75,129-138`, `data_api/{live_candles,redis_bus}.py`, `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `bot_tui/*_state.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

claude-opus-5-5 (Claude Code, bmad-loop dev session)

### Debug Log References

- Baseline (`f879c11ca3`, full `make test` list minus `views/tests`): 5 failed / 1535 passed, no warnings. The
  failures are `ml_signals/tests/test_ofi_strategy.py` x3, `test_ofi_strategy_indicator_consistency.py` x1
  (`TypeError: Unexpected keyword argument 'ma_period'`, Story 24.4's repair) and
  `data_api/tests/test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay` (needs a live
  Redis). The dYdX `trade_ohlc` x5 the spec expected no longer fail on this checkout.
- After (same list + `views/tests`): 5 failed / 1586 passed, the identical five ids, no warnings summary.

### Completion Notes List

- **Chart behaviour change (intended, AC #2):** the chart's Lines mode now renders crossed/touched seconds
  (`bid >= ask`) exactly as the capture gate wrote them -- priced with the same mid/microprice/CVD-weighted
  formulas and counted toward `limit`. The two reader-side skips of `data_api/routes/snapshots.py:129-133`
  are deleted, not moved. A second with an empty bid or ask side (which the gate never writes) now fails the
  `/api/snapshots` request with a 500 naming the instrument and `ts_event`, ledgered at
  `views.snapshot_without_top` (DATA-07), instead of being skipped. The 2500 ms gap marker survives as the
  `views.chart_series` rendering rule, with its test moved to `views/tests/test_chart_series.py`.
- `views/` created (`ranking_columns`, `coin_detail`, `chart_series`, `indicator_picker`, `live_candles` with
  the `BarObserver` Protocol, `rankings_bus`, `preferences`, `catalog_reads`); `data_api` routes and `bot_tui`
  format + transport only; `data_api/buses.py` constructs the two bus instances; `REDIS_URL` moved to
  `data_api/settings.py`; `candles.application.queries.open_store` added. Every moved path is a pure re-export
  shim (`REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"`), renamed successors bound
  with `as`; `bus`/`live_candle_bus`/`REDIS_URL`/`overview_table` raise via `_REPLACED_NAMES`.
- Expired Story 23.2 shims deleted: `ml_signals/{performance_metrics,indicators,venue}.py`,
  `collector_core/{second_snapshot,venue_http,fold,open_interest}.py`, the `common/` package (and its two
  dockerfile `COPY` lines), the `_MOVED_NAMES` blocks of `catalog_stats` (replaced by the 24.2 one) and
  `archive_gaps`.
- `tests/test_boundaries.py`: three 24-2 legacy edges and three private-import entries retired; `(RESEARCH,
  VIEWS)` added until 24-4; `VIEWS_QUERY_SERVICES` plus its two tests, the views forbidden-package test, the
  interface-import test and the snapshot-key-indexing test (with `LEGACY_SNAPSHOT_INDEXING_UNTIL` for
  `ranking_engine.engine`) added; four `data_api` tests registered in `COMPOSITION_ROOTS`.
- **Deviations recorded in the spec's Design Notes, as implemented:** `overview_table` deleted (not moved) and
  served via `_REPLACED_NAMES`; `list_instruments` remapped to `RANKING`; views reaches the candle store only
  through `queries.open_store`; the `BAR_SECONDS` pre-check in `_store_page` dropped (an unkept width has no
  rows and no coverage -- the same result); merged modules renamed only on collision.
- **Implementation deviations (each judged within the spec's intent):**
  1. `indicator_values_page` returns `(rows, has_more, errors)` -- the response carries `errors`, which a
     `(rows, has_more)` pair cannot -- and raises `CandleReadError` so the route keeps its exact
     `"failed to read catalog: ..."` 500 for a candle-read failure (and `ImpossibleCandle` for an impossible
     candle), without widening the old try/except over the replay.
  2. `price_series_rows` calls `DydxSecondSnapshot.to_dict(s)`: `to_dict` is a staticmethod, so the spec's
     `snapshot.to_dict()` would raise.
  3. `views.chart_series.compute_chart_series` (the legacy `/catalog/chart-series` body, moved from
     `ml_signals/chart_data.py`) carried the same empty-top/crossed skips; they were deleted too, since
     the story bans any reader-side skip on the read path: a crossed second now yields a negative spread
     as written, and an empty top raises `EmptyTopOfBook` (ledgered, 500 from `data_api/app.py`).
     `test_compute_chart_series_skips_crossed_snapshot` was rewritten to assert the new behaviour.
  4. `views.indicator_picker._CATALOG_PATH` keeps its verbatim `CATALOG_PATH` env read (same variable and
     default as `data_api.settings`), documented as a `Known limit:` with the `ReplayWindow` upgrade path.
  5. `frontend/openapi.json`: three path `description`s regenerated because the handler docstrings they are
     generated from named moved symbols (`redis_bus.bus`, `replay_indicator`, `_indicator_id`); every schema,
     and `src/api/schema.ts` (regenerated, identical), is unchanged.
  6. `_latest_values` became `technicals_values` + `_latest_of_group` (function-length rule), same logic;
     `routes/indicators._parse_entries` is now generic over its model (fixes the relocated mypy arg-type error).
  7. `COIN_DETAIL_GROUPS` is a tuple-of-tuples (a frozen table; views holds no mutable module state).
  8. `snapshot_for` also ledgers a non-list batch at `views.snapshot_decode` (was a bare `logger.warning`).
  9. `views.chart_series.MAX_QUERY_SPAN_SECONDS` is public (a route test reads it); the two pure
     `_window_start_ns` unit tests moved to `views/tests` with the function, since a `data_api` test may not
     read a views private name.
- **Tests repointed/rewritten, with root causes:** `test_crossed_book_row_is_skipped_and_not_counted_toward_limit`
  replaced by `test_crossed_row_written_by_the_gate_is_returned_unchanged` (the sanctioned rewrite) plus
  `test_empty_top_of_book_row_fails_the_request_loudly`. Four `bot_tui` tests broke because their fixtures
  hand-built `snapshots:raw` rows with venue-less ids (`"BTC-USD-PERP"`) the collector never publishes, which
  `DydxSecondSnapshot.from_dict` rightly rejects; the fixtures now carry real ids / a decoded
  `DydxSecondSnapshot`, assertions unchanged in substance (`_LATEST_SNAPSHOT.instrument_id.value` instead of
  `["instrument_id"]`). `data_api` Technicals tests patch `views.ranking_columns.technicals_values` /
  `_recent_candles` / `catalog_files` (the fakes accept the new path arguments); `test_live_candles.py` moved
  to `views/tests` and passes the catalog path to `LiveCandleBus(...)` instead of patching
  `data_api.settings`. `candles.tests.test_candle_store._second/_DAY0_MS` imports replaced by local helpers.
- New tests: crossed/touched row priced, empty-top ledger + raise (3 cases), gap threshold edge, bar gap
  rule, `snapshot_for` decode/skip/ledger, `COIN_DETAIL_GROUPS` shape, metrics/catalog reads, preferences
  byte-identical round-trip against text recorded from the pre-move writers at `f879c11ca3`, paging helpers,
  `BarObserver` satisfied structurally by a test double.
- Verification: full list 5 failed (the baseline five) / 1586 passed, no `DeprecationWarning`;
  `test_boundaries.py`/`test_images.py`/`test_namespace.py` 192 passed; `ruff format --check` clean; `ruff check`
  and `mypy views data_api bot_tui candles` add no finding beyond relocated baseline ones (ruff 61 -> 51
  platform-wide); the skip grep has no hits. Frontend `vitest` not run (no `node_modules` in the worktree); the
  frontend edits are string literals in the docs page data and code comments only.

### File List

Created:
- platform/views/__init__.py
- platform/views/catalog_reads.py
- platform/views/chart_series.py
- platform/views/coin_detail.py
- platform/views/indicator_picker.py
- platform/views/live_candles.py
- platform/views/preferences.py
- platform/views/ranking_columns.py
- platform/views/rankings_bus.py
- platform/views/tests/__init__.py
- platform/views/tests/test_catalog_reads.py
- platform/views/tests/test_chart_series.py
- platform/views/tests/test_coin_detail.py
- platform/data_api/buses.py

Moved (git mv, then repointed):
- platform/ml_signals/tests/test_book_features.py -> platform/views/tests/test_book_features.py
- platform/ml_signals/tests/test_chart_data.py -> platform/views/tests/test_chart_data.py
- platform/ml_signals/tests/test_chart_indicator_config.py -> platform/views/tests/test_preferences.py
- platform/ml_signals/tests/test_chart_indicators.py -> platform/views/tests/test_indicator_picker_native.py
- platform/ml_signals/tests/test_custom_indicators.py -> platform/views/tests/test_indicator_picker_custom.py
- platform/ml_signals/tests/test_footprint.py -> platform/views/tests/test_footprint.py
- platform/data_api/tests/test_live_candles.py -> platform/views/tests/test_live_candles.py

Converted to re-export shims:
- platform/ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py
- platform/data_api/{live_candles,redis_bus}.py, platform/data_api/routes/paging.py

Deleted:
- platform/ml_signals/{performance_metrics,indicators,venue}.py
- platform/collector_core/{second_snapshot,venue_http,fold,open_interest}.py
- platform/common/__init__.py, platform/common/venues.py

Modified:
- platform/ml_signals/catalog_stats.py, platform/ml_signals/tests/test_catalog_stats.py,
  platform/ml_signals/tests/test_ofi_strategy_indicator_consistency.py, platform/ml_signals/BACKTESTING.md
- platform/candles/__init__.py, platform/candles/application/queries.py
- platform/data_api/{app,alerts,settings}.py, platform/data_api/ws/live.py,
  platform/data_api/routes/{candles,snapshots,indicator_series,indicators,rankings,metrics}.py
- platform/data_api/tests/{test_candles,test_snapshots,test_data_api,test_indicators_config,test_rankings,test_screener_columns,test_ws_live_candles}.py
- platform/bot_tui/{app,coin_detail,coin_detail_state,coins_pane}.py
- platform/bot_tui/tests/{test_app_coin_detail,test_app_stale_badge,test_coin_detail,test_coin_detail_state,test_coins_pane}.py
- platform/collector_core/{archive_gaps,repair_catalog,crosscheck_errors}.py,
  platform/collector_core/tests/{test_collector,test_rebuild_seconds,test_venue_time}.py
- platform/dydx_collector/tests/{test_repair_catalog,test_normalize_snapshot_schema}.py,
  platform/dydx_collector/notebooks/dydx_catalog_pandas.ipynb
- platform/kernel/tests/{test_venues,test_pre_move_fixtures}.py, platform/live_paper/__init__.py
- platform/tests/test_boundaries.py
- platform/collector.dockerfile, platform/data_api.dockerfile, platform/Makefile
- platform/ARCHITECTURE.md, platform/CLAUDE.md, platform/docs/DATA_DICTIONARY.md
- platform/frontend/openapi.json, platform/frontend/src/pages/docs/{data,kbData}.ts,
  platform/frontend/src/pages/RankingsPage.tsx, platform/frontend/src/hooks/useLiveChannel.ts
- _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
- _bmad-output/implementation-artifacts/24-2-views-read-models-and-reader-side-revalidation-removed.md
- _bmad-output/implementation-artifacts/spec-24-2-views-read-models-and-reader-side-revalidation-removed.md
