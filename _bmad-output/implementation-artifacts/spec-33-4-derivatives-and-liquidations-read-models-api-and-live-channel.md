---
title: 'Story 33.4: Derivatives and liquidations as read models: funding, open interest, mark, index, basis and liquidations served by the API and pushed live'
type: 'feature'
created: '2026-10-06'
status: 'done'
baseline_revision: '8c01502eeca190d1d5ec480ae9b38c8cd9f49de1'
final_revision: '1db95b175c61b4cfa3e7abc07072b58ee9dfed6a'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** The collectors have archived mark, index, funding and open interest since Epic 22, and Bybit liquidations since 33.1. Nothing reads them back:
- no API route serves them;
- no live channel pushes them;
- no ranking row carries them.

Meanwhile three legacy code paths with no caller pretend to be features: `/catalog/chart-series`, `/api/indicator-series` and `useIndicatorSeries`.

**Approach:** Add five pieces:
1. One read model, `views/derivatives.py`, behind five `GET /api/coin/{iid}/…` routes.
2. A new `derivs:raw` capture publish, built from one kernel wire codec, plus a `LiveDerivsBus`. Together they feed the `derivs:{iid}` and `liquidations:{iid}` channels on `/ws/live`, and two frontend hooks.
3. Ranking rows gain added fields only, with persisted columns appended by migration.
4. Two pure kernel functions, `basis_bps` and `funding_annualised`.

Then delete the legacy paths and amend the docs.

## Boundaries & Constraints

**Always:**
- **Exact values.** Prices, sizes and rates are `Decimal` or integer units until the edge.
  - Funding rate, OI, mark and index go over the wire and the API as `Decimal` **strings**. `str()` of a `Price` or `Decimal` is exact.
  - Liquidation sizes and prices stay integer units, with `price_precision`/`size_precision` on the response.
  - Basis, annualised funding and every ratio are computed from `Decimal` and emitted as `float` only in the response or the ranking row.
  - Never `float(...)` a stored value before the arithmetic (DATA-04).
- **Bounded reads (MEM-01).**
  - Every page read is bounded by `MAX_QUERY_SPAN_SECONDS` (`views/chart_series.py:601`).
  - `limit` is clamped 1..500 and `bar_seconds` 1..604800, both silently, as `/api/candles` does.
  - Buckets use `candles.domain.fold.bucket_start_ms` (the DATA_DICTIONARY §2.5 rule).
  - Bucketed pages insert gap rows through `with_gap_markers`.
- **Spot.** A spot id (`kernel.venues.market_kind(iid) == "spot"`) gets `200`, `items: []`, `market: "spot"` on every derivatives route, never a 404. No `derivs` row is ever expected for it.
- **Null vs 0.** A missing input is `None` (JSON `null`), never 0. This covers:
  - an id with no liquidation feed (`has_liquidation_feed` false): its liquidation fields are `None`;
  - a bucket before or straddling the feed start: `None`, under D-160's rule, which `liq_*` already encode;
  - a ratio with a zero denominator: `None`.
- **Added fields only (AD-D12).**
  - `rankings:live` keys and metrics columns are appended after every existing key or column. `rank` stays where it is today.
  - Existing payload bytes are unchanged. `test_replay.py` must pass, after it strips the new keys and projects the stored rows onto the recorded columns.
- **Single sources (SSOT-02).**
  - `derivs:raw`'s row format is defined once, in `kernel/derivs_wire.py`, and imported by `capture`, `views` and `ranking`.
  - `basis_bps`/`funding_annualised` are defined once, in `kernel/indicators.py`, and used by both `views/derivatives.py` and `ranking`.
  - A liquidation's notional is `Liquidation.notional_units()` everywhere.
- **One Redis subscriber per channel per process** (`data_api/buses.py` invariant). `LiveCandleBus` stays the sole `liquidations:raw` subscriber and hands decoded rows to `LiveDerivsBus`. `LiveDerivsBus` is the sole `derivs:raw` subscriber.
- **Failures are counted, never silent (DATA-07).**
  - A failed or overflowed `derivs:raw` publish is ledgered at a new `sites.DERIVS_PUBLISH`.
  - An undecodable `derivs:raw` row is ledgered by the reader: `live_derivs.parse` in `views`; in `ranking`, the engine ledgers the board's returned details.
  - The capture hot path (`_process_data`) only appends to a bounded list. Encoding happens on the publish loop, never per message.
- **Boundaries.**
  - `ranking/domain` stays pure (stdlib, numpy, `kernel`, `nautilus_trader.model`/`core`).
  - No new dependency. Never touch `nautilus_trader/` or `crates/`.
  - A `data_api` response change regenerates `frontend/openapi.json` and `frontend/src/api/schema.ts` in the same change.

**Block If:**
- A deletion target turns out to have a non-test caller that the investigation missed and that serves a live feature. In that case, HALT. Do not delete it, and do not rewire the feature.

**Never:**
- No chart panes, screener columns, alert conditions or `HistoryPage` tiles. Those are 33.5, 33.7 and 33.8; this story only adds the data.
- No Hyperliquid or dYdX liquidation rows, and no invented `price_kind` column. `price_kind` is the constant `"bankruptcy"` derived in the read model, because Bybit is the only feed.
- No change to the candle fold or the candle schema from 33.3.
- No interpolation or fill across a gap. A bucket without data is a gap row.
- `views` must not import `verification/` (the oracle) or `research/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Funding page | Bybit linear id, `before_ns`, `limit=3` | Up to 3 newest `FundingRateUpdate` before `before_ns`, oldest first. Each is `{t, rate:"0.0001", interval, next_funding_ns}`. Response has `has_more`, `venue`, `market:"perp"` | No error expected |
| OI bucketed | OI rows at 10:00:05 (100) and 10:00:50 (120), 10:01:30 (90); `bar_seconds=60` | Buckets 10:00 → `oi:"120"`, `oi_change:null` (no earlier OI in the bounded read); 10:01 → `oi:"90"`, `oi_change:"-30"` | No error expected |
| OI gap | Buckets 10:00 and 10:03 only | `with_gap_markers` inserts `{t}` for 10:01 and 10:02. `oi_change` at 10:03 is measured against 10:00, the last known bucket | No error expected |
| Mark/index basis | mark 100.5, index 100.0, candle close 100.4 in the bucket | `basis_mi_bps = 50.0`, `basis_ml_bps ≈ 9.96` (float at the edge). `mark`/`index` are strings | No error expected |
| Basis missing input | Mark present, no index in the bucket, or no candle | `basis_mi_bps: null`, or `basis_ml_bps: null` | No error expected |
| Liquidation bars | Bucket with only a liquidation and no trade | Served (D-162's upgrade: no `o IS NOT NULL` filter): `long_v, short_v, n` from the stored columns, plus `notional_units` summed from the raw rows | No error expected |
| Liquidation bars, no feed | Hyperliquid perp id | Rows with `long_v/short_v/n/notional_units: null` and an empty page. Never 0 | No error expected |
| Spot | `BTCUSDT-SPOT.BYBIT` on any of the 5 routes | `200 {items: [], has_more: false, market: "spot"}`; nothing is read | No error expected |
| Live derivs | `{subscribe:"derivs:X"}`, then a `derivs:raw` funding row for X | `{"channel":"derivs:X","kind":"funding","t",…,"value":"0.0001","interval","next_funding_ns"}` | Malformed channel: ignored and logged, as candles are today |
| Live liquidation | `{subscribe:"liquidations:X"}`, then a `liquidations:raw` row for X | `{"channel":"liquidations:X","liq":{to_dict row}}` | — |
| Subscription cap | 33rd subscription of any kind on one socket | Ignored. The cap (`_MAX_SUBSCRIPTIONS = 32`) counts candles, derivs and liquidations together | — |
| Bad derivs row | `derivs:raw` entry missing `value` or with an unknown `kind` | Skipped; other rows in the batch are still forwarded | Ledgered (`live_derivs.parse`; the ranking engine's site) |
| Publish failure | Redis down during a `derivs:raw` publish | The archive is unaffected and the rows are dropped from the live push | Ledgered `DERIVS_PUBLISH` with the row count. Overflow past the pending cap is also ledgered |
| Ratio zero denom | `liq_long+liq_short = 0`, or zero traded volume in 1 h | `liq_ratio_1h: null`, `forced_share_1h: null` | — |
| Ranking OI change | Stored OI reaches only 30 min back | `oi_change_1h: null`, `oi_change_24h: null` | — |

</intent-contract>

## Code Map

- `platform/views/chart_series.py`:
  - `MAX_QUERY_SPAN_SECONDS` (:601), `with_gap_markers` (:577), `candle_page` (:959) and `footprint_page` (:1382) are kept.
  - **Delete:** `compute_chart_series` (:493), `build_footprint`/`FootprintCell` (:386-404), `compute_features` (:342), `indicator_series_page` (:1074).
  - **Delete** the helpers that only the deleted code uses: `depth_profile`, `top_of_book_series`, `book_imbalance` (after grep). `CancellationTracker` (:272) and `replay_bucket_samples` (:1020) stay. `liquidity_distance` (:229) **moves** to `kernel/indicators.py`.
- `platform/views/catalog_reads.py`: `instrument_precision`, `fetch_page`, `has_older_data`, `liquidation_feed_start`.
- `platform/kernel/catalog_files.py`: `query_index_prices` (:258) and `query_liquidations` (:651). This is the pattern to follow for the new `query_open_interest`.
- `platform/research/application/frames.py:132` `CatalogFrames._query`: the `ts_init`-skew-widened `catalog.query` pattern for `MarkPriceUpdate` and `FundingRateUpdate`. Copy the pattern; do not import it.
- `platform/candles/application/queries.py`: `window` (:117, traded bars only), `open_store`, `liquidation_feed_since`.
- `platform/kernel/indicators.py`: `depth_within_bps` (:636).
- `platform/kernel/liquidation.py`: `Liquidation`, `to_dict`/`from_dict`, `notional_units()` (:190), `has_liquidation_feed` (:75).
- `platform/kernel/venues.py:100` `market_kind`.
- `platform/data_api/`:
  - `routes/candles.py`: the contract and models to mirror.
  - `app.py`: the legacy route (:191-199), router registration (:276-287, above the catch-all) and the lifespan (:104-135).
  - `routes/indicator_series.py`: delete.
  - `buses.py`.
  - `ws/live.py`: `_parse_candle_channel` (:67), `_CandleSubscriptions` (:108), `_handle_control_message` (:140), `_MAX_SUBSCRIPTIONS` (:56).
  - `routes/metrics.py:51` `MetricHistoryItem`, which mirrors `COLS`.
- `platform/views/live_candles.py`: `LiveCandleBus`, `run` (:822), `handle_liquidations` (:710), `_dispatch` (:852). `views/rankings_bus.py` has `QUEUE_MAX` and `put_drop_oldest`.
- `platform/capture/`:
  - `application/capture_service.py`: `_process_data` (:854, whose `else` branch at :894 buffers mark/index/funding/WS OI), `ingest_rows` (:2574, REST OI), `_publish` (:1728) and `_second_loop` (:1765/:1800), `_ledger` (:713).
  - `application/ports.py:219` `LiveStream`.
  - `application/sites.py`.
  - `infrastructure/redis_stream.py`: `publish_liquidation_batch`, `RedisLiveStream`.
  - Stream doubles: `capture/tests/test_coverage.py:901`, `capture/tests/test_collector.py:1625`.
- `platform/ranking/`:
  - `domain/board.py`: `InstrumentMetrics`, `_rank_row` (:458), `_slow_row` (:417), `backfill`, `ingest`.
  - `application/engine.py`: `handle` (:122), `_backfill_new_instruments` (:279).
  - `application/ports.py`: channel constants and the `PriceHistory` port.
  - `infrastructure/redis.py:253` `listen`.
  - `infrastructure/catalog_prices.py`: `CatalogPriceHistory`.
  - `infrastructure/metrics_store.py`: `COLS` (:33), `_migrate` (:65).
  - `tests/test_replay.py`: `_without_symbol` (:232), `_stored_rows`.
- `platform/frontend/src/`:
  - `hooks/useLiveCandle.ts`: the template for the new hooks.
  - `hooks/useIndicatorSeries.ts` and `api/client.ts:150-163`: delete. Move `IndicatorDatum` into `components/chart/legend.ts`, which is used by `legend.ts:3` and `LightweightChart.tsx:22`.
  - `pages/docs/data.ts`: `book_features` (:258), `footprint` (:274), `ema_trend` (:288).

## Tasks & Acceptance

**Execution:**

*Kernel*
- [x] `platform/kernel/indicators.py` -- Add `basis_bps(mark: Decimal, ref: Decimal) -> Decimal | None` (`(mark-ref)/ref×10⁴`; None when `ref <= 0`) and `funding_annualised(rate: Decimal, interval_s: int | None) -> Decimal | None` (`rate × 31_536_000 / interval_s`; None when the interval is None or ≤ 0). Move `liquidity_distance` here from views. -- One computer for 33.5/33.7/33.8.
- [x] `platform/kernel/derivs_wire.py` (new) -- Define the `derivs:raw` row.
  - `to_wire(data) -> dict | None` takes `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate`/`OpenInterest` and returns `{instrument_id, kind: mark|index|funding|oi, t: ts_event, ts_init, value: exact str}`. Funding also carries `interval` (seconds, converted from the type's minutes; None if absent) and `next_funding_ns`. Any other type returns None.
  - `DerivsTick` (frozen dataclass) and `from_wire(dict) -> DerivsTick` raise `ValueError` on any malformed row.
  - Pin the actual unit of `FundingRateUpdate.interval` with a test on a real object.
  - -- The single wire format.
- [x] `platform/kernel/catalog_files.py` -- Add `query_open_interest(catalog_path, iid, start_ns, end_ns) -> list[OpenInterest]`: column-projected, inclusive and bounded like `query_liquidations`. -- Ranking can import only `kernel`.
- [x] `platform/kernel/tests/` -- Tests: hand-computed `basis_bps`/`funding_annualised` including the None cases, a wire round-trip per kind plus malformed rows, `query_open_interest` on a written fixture, and the moved `liquidity_distance` test.

*Capture*
- [x] `platform/capture/application/sites.py`, `ports.py`, `capture_service.py`, `infrastructure/redis_stream.py` -- Add the derivs publish.
  - `DERIVS_PUBLISH` site, `LiveStream.publish_derivs(rows: list[dict])`, and `publish_derivs_batch` on `derivs:raw` (`DERIVS_CHANNEL`).
  - `CaptureService` keeps a bounded pending list. It is appended in `_process_data`'s `else` branch and for `OpenInterest` in `ingest_rows`, only when `live_stream` is set.
  - The list is drained, encoded through `to_wire` and published once per `_second_loop` tick next to `_publish`.
  - A failure or an overflow is ledgered with its count. The archive path is unchanged.
  - Update every stream double.
- [x] `platform/capture/tests/` -- Tests: a funding row and a REST OI row reach `publish_derivs`; a failed publish is ledgered and the rows are still archived; overflow is ledgered; no accumulation without a live stream.

*Read model and routes*
- [x] `platform/views/derivatives.py` (new) -- The one read model. Every page returns `(items oldest-first, has_more)`, reads only inside the bounded window (walking back with `fetch_page` + data-file ranges, like the candles' Parquet page), and short-circuits spot to `([], False)`.
  - `funding_page(iid, before_ns, limit)`: event rows.
  - `open_interest_page(iid, before_ns, limit, bar_seconds)`: the last OI per bucket, `oi_change` against the previous *known* bucket (one bucket read before the window), gap-marked.
  - `mark_index_page(...)`: last mark/index per bucket, `basis_mi_bps`, and `basis_ml_bps` where the store holds that bucket's candle (close via `Decimal(str(c)).quantize(10^-pp)`), gap-marked.
  - `liquidations_page(iid, before_ns, limit)`: the `to_dict` rows minus `instrument_id`, plus `price_kind: "bankruptcy"`.
  - `liquidation_bars(...)`: `long_v, short_v, n` from the store's columns, read **including untraded buckets** through a new `candles.application.queries.liquidation_window` without the `o IS NOT NULL` filter (D-162's upgrade path). `notional_units` is `Σ notional_units()` of that bucket's raw rows, rescaled to the finest `pp+sp`, with `notional_precision`. Null when the bucket's `liq_n` is null; 0 when `liq_n` is 0.
  - Mark and index are read **column-projected** through `kernel.catalog_files.query_price_columns` (numpy `value`/`ts_event`/`ts_init`, exact integer units at the file's precision label, `MAX_TS_INIT_SKEW_NS` widening, inclusive bounds, a row stored twice kept once and a disagreeing copy refused), one UTC day at a time, cut to each bucket's last value (review fix, MEM-01). Funding stays on a bounded `ParquetDataCatalog.query` with `MAX_TS_INIT_SKEW_NS` widening (change-deduped upstream: a `Known limit:` in-code).
  - An empty walk-back window jumps straight to the newest older row (`kernel.catalog_files.newest_ts_event_before`, one `ts_event` scan) instead of `fetch_page`'s window-by-window step (review fix: a 1 s window over a 300 s OI poll re-read one file per empty window).
- [x] `platform/candles/application/queries.py` -- Add `liquidation_window(db, iid, bar_seconds, start_ms, end_ms) -> list[dict]`: `t, liq_long_v, liq_short_v, liq_n, size_precision`, with no traded-bar filter.
- [x] `platform/views/tests/test_derivatives.py` (new) -- Test every I/O-matrix row: hand-computed buckets, gaps, basis, null-vs-0 liquidation bars, untraded liquidation bucket, spot, span cap, limit clamp.
- [x] `platform/data_api/routes/derivatives.py` (new) + `app.py` -- `GET /api/coin/{instrument_id}/funding|open-interest|mark-index|liquidations|liquidation-bars`.
  - Same `before_ns`/`limit`/`bar_seconds` contract as `/api/candles`.
  - Pydantic response models carry `venue`, `market`, `has_more`, and `price_precision`/`size_precision` where units are returned.
  - Register the router above the catch-all.
- [x] `platform/data_api/tests/test_derivatives_routes.py` (new) -- Each route's contract, the spot case and the clamps.

*Live*
- [x] `platform/views/live_derivs.py` (new) -- `LiveDerivsBus`, in the `LiveCandleBus` shape:
  - per-iid listener queues with `put_drop_oldest`, and teardown on the last unsubscribe;
  - `run(redis_url)` subscribes `derivs:raw` only, decodes through `from_wire` and ledgers bad rows;
  - `publish_liquidations(rows)` takes the rows `LiveCandleBus.handle_liquidations` already decoded.
- [x] `platform/views/live_candles.py` -- Add an attach hook through which `handle_liquidations` forwards the decoded rows to `LiveDerivsBus`.
- [x] `platform/data_api/buses.py`, `app.py` lifespan -- Add the `live_derivs_bus` singleton, its run task, and the attach call. Update the invariant docstring.
- [x] `platform/data_api/ws/live.py` -- Parse `derivs:{iid}` and `liquidations:{iid}`. Generalise the per-connection subscriptions so the cap counts every kind.
- [x] `platform/views/tests/test_live_derivs.py` + `platform/data_api/tests/test_ws_live_candles.py` -- Fan-out per iid, a bad row ledgered, liquidation forwarding, parsing of the two channel names, the shared cap, and malformed subscribes.
- [x] `platform/frontend/src/hooks/useLiveDerivs.ts`, `useLiveLiquidations.ts` + tests -- Mirror `useLiveCandle`: subscribe on open, a channel guard, ignore foreign or malformed frames, unsubscribe on cleanup, reconnect.

*Ranking*
- [x] `platform/ranking/domain/derivs.py` (new) + `board.py` -- Pure per-instrument state and the new row fields, appended after every existing key and before `rank`. Fields:
  - `funding_rate` and `funding_annualised` (float), `next_funding_ns`;
  - `open_interest`, `oi_change_1h`, `oi_change_24h`: the OI ring covers 25 h; a change is None when the series does not reach back to the target time;
  - `basis_mi_bps`, and `basis_ml_bps` (mark against the last trade close);
  - `liq_long_1h`, `liq_short_1h`, `liq_notional_1h`, `liq_ratio_1h`, `forced_share_1h`: base and quote floats from a 1 h liquidation deque; None when the id has no feed;
  - `relative_volume`: the last-hour traded volume over the trailing-24 h hourly mean; None when under 2 h of data;
  - `high_24h`, `low_24h`, `range_position_24h` (None on a flat range), from the 25 h price store.

  `avg_trade_size` stays where it is today. `_slow_row` carries the persistable fields. Commands take `now_ns` and return error details.
- [x] `platform/ranking/application/engine.py`, `ports.py`, `infrastructure/redis.py` -- Changes:
  - Subscribe to `derivs:raw` and `liquidations:raw`, dispatch them to the board, and ledger the returned details.
  - At first backfill, read OI over 25 h (`query_open_interest`) and liquidations over 1 h (`query_liquidations`) for feed ids, through ports and catalog adapters, read once and never retried.
  - Feed the hourly volume from the existing seconds backfill (extend `CatalogPriceHistory.series` to return volume), not from a second read.
- [x] `platform/ranking/infrastructure/metrics_store.py` + `data_api/routes/metrics.py` -- Append the numeric new fields to `COLS`; `_migrate` adds them as nullable columns. Extend `MetricHistoryItem` to match.
- [x] `platform/ranking/tests/` -- Domain tests:
  - hand-computed values for every field and every None case;
  - engine dispatch and a bad row ledgered;
  - the migration adds the columns.

  `test_replay.py`: generalise the stripping to the tuple of new keys, assert where they sit, and project `_stored_rows` onto the recorded columns. The recorded bytes are unchanged.

*Deletions and docs*
- [x] Delete the legacy routes and dead code:
  - `/catalog/chart-series` (`app.py`; keep `_check_catalog_window`/`_read_catalog`) and `routes/indicator_series.py`;
  - `views/chart_series.py`'s `compute_chart_series`, `build_footprint`, `compute_features`, `indicator_series_page` and their now-dead helpers;
  - `hooks/useIndicatorSeries.ts` + its test, and `fetchIndicatorSeries`.

  Then delete or trim their tests (`test_chart_data.py`, `test_footprint.py`, `test_book_features.py`, `test_indicator_series.py`, `test_data_api.py` cases, `test_indicators_config.py`'s three-way test, the `test_settings.py` import) and regenerate `openapi.json`/`schema.ts`. -- The AC requires the deletion; no caller remains.
- [x] `platform/frontend/src/pages/docs/data.ts` -- Rewrite `footprint`, `book_features` and `ema_trend` to what exists (32.8's trade footprint and `kernel` depth functions). Remove any mention of the deleted routes. Add the five routes and two channels wherever the Docs page lists routes or channels. -- DESIGN-03.
- [x] `platform/docs/DATA_DICTIONARY.md`:
  - new §2.16 "Derivatives and liquidations read models": routes, fields, nulls, buckets, `price_kind`;
  - a `derivs:raw` wire entry;
  - §3.2/§3.3/§3.4 amended field by field;
  - §2.4/§2.7 marked as removed.
- [x] `platform/docs/DATA_INTEGRITY_AUDIT.md` -- Rows from D-163 for:
  - `derivs:raw` publication being at most once (Known limit);
  - notional priced at bankruptcy;
  - a dropped live derivs frame;
  - D-162 GUARDED through `liquidation_bars`.
- [x] `platform/docs/DEPLOY_CHECKLIST.md` -- Add a 33-4 deferred operator action: rebuild and restart the collectors, `data_api` and `ranking`. The metrics migration runs on its own; give the verify commands.
- [x] `platform/ARCHITECTURE.md` (:78) -- Drop the `compute_chart_series` mention.

**Acceptance Criteria:**
- Given the five routes, when the OpenAPI schema is exported, then the committed `openapi.json`/`schema.ts` match it and `test_frontend_contract.py` passes, including the rule that every client fetch hits a route.
- Given the deletions, when `grep -rn "compute_chart_series\|build_footprint\|compute_features\|indicator-series\|useIndicatorSeries\|chart-series" platform --include=*.py --include=*.ts --include=*.tsx` is run, then nothing matches outside docs that record the removal.
- Given a running collector with a live stream, when funding, mark, index or OI arrives, then one `derivs:raw` message per second carries those rows, and `/ws/live` subscribers to `derivs:{iid}` receive exactly their iid's rows.
- Given the replay fixture, when `test_replay.py` runs, then the recorded bytes and the recorded metrics columns match after the new keys are stripped and the stored rows are projected.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 11 (high 1, medium 4, low 6)
- defer: 1 (high 0, medium 1, low 0)
- reject: 7 (high 0, medium 0, low 7)
- addressed_findings:
  - `[high]` `[patch]` Mark (and index) pages decoded every raw tick into Python objects, up to 7 days per request (MEM-01). Now read through a column-projected `kernel.catalog_files.query_price_columns`, exact integer units, last-per-bucket in numpy. Disagreeing duplicates are refused, and index is now deduplicated too. The spec's Code Map has a one-line read-path change (no re-derivation needed).
  - `[medium]` `[patch]` A sparse series (OI) with a small window re-read one file per empty window. `_first_window` jumps to the newest older row (`newest_ts_event_before`); `fetch_page` is unchanged for candles.
  - `[medium]` `[patch]` A composed wide liquidation bar (1W, 10m...) summed only the constituents present. It is now null unless every constituent up to the store's live edge is present (audit D-171).
  - `[medium]` `[patch]` The live-edge liquidation-bar notional was routinely null because the tail is ahead of the store. It now sums archived rows only (the archive is written before the store apply) and is served only when they match `liq_n` exactly (audit D-172).
  - `[medium]` `[patch]` Decimal text could be emitted in scientific notation (`1.2E-7`, `0E-8`). Now `kernel.derivs_wire.exact_text` (positional) on the wire and in the routes.
  - `[low]` `[patch]` Unparseable `/ws/live` control frames logged a WARNING each. Now the first is logged and the rest counted; the hooks never subscribe with an empty id.
  - `[low]` `[patch]` A shutdown `CancelledError` mid-publish was ledgered as a `derivs:raw` failure. It now propagates, with an info line.
  - `[low]` `[patch]` A partially null stored liquidation group raised an unledgered `TypeError`. It is now a ledgered read error.
  - `[low]` `[patch]` The ranking backfill could move the derivs state's `last_seen_ns` backwards. Now `max()`.
  - `[low]` `[patch]` ns event timestamps above 2^53 in JSON. The cursor reasoning is verified (a tie group is served whole, and Bybit stamps whole ms) and recorded as a `Known limit:` (audit D-170), with a string cursor as the upgrade path.
  - `[low]` `[patch]` Index ticks were not deduplicated. Fixed by the shared reader above.

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 0, medium 2, low 12)
- defer: 0
- reject: 44: (high 0, medium 0, low 44)
- addressed_findings:
  - `[medium]` `[patch]` A replayed `liquidations:raw` frame was deduplicated for the tail and the bars but still forwarded to `LiveDerivsBus`, so `liquidations:{iid}` listeners got the venue event twice. `_remember_liquidation` now returns whether the event is new, and only new events are forwarded (`views/tests/test_live_derivs.py::test_a_replayed_liquidation_is_forwarded_once`).
  - `[medium]` `[patch]` The new kernel readers (`query_open_interest`, `query_price_columns`, `newest_ts_event_before`), and `query_liquidations` that this story newly reads from the read model and the ranking backfill, did not list the files again when the consolidation removed a minute file mid-read (`query_trade_columns` already did). This caused a spurious ledgered 500 or a lost one-time ranking backfill. They now go through one `_relisting` helper, bounded by `_TRADE_LISTING_ATTEMPTS`, which refuses with a `ValueError` after that. Tested both ways, and the test fails with one attempt.
  - `[low]` `[patch]` `liquidation_bars` read up to 7 days of liquidations in one `query_liquidations` call, against that reader's "a day at most" bound. It now reads one UTC day at a time (`day_slices`).
  - `[low]` `[patch]` `_unique_price_rows` compared every duplicated row in a Python loop (a day held twice is ~864k copies). It is now vectorised: only copies whose units or precision label differ are compared as `Decimal`.
  - `[low]` `[patch]` `_bucket_window`'s Known limit named only 1W. It now states the general bound (at most `MAX_QUERY_SPAN_SECONDS / bar_seconds` buckets per page: 42 at 4h, 7 at 1D, 1 at 1W) and that `has_more` stays true.
  - `[low]` `[patch]` The deletion left `data_api.app._read_catalog`'s `EmptyTopOfBook` branch unreachable, and a test still covered it through a monkeypatch. The branch, the test and both now-unused `chart_series` imports are removed.
  - `[low]` `[patch]` Audit D-88 and D-121 cited tests this story deleted. Both rows now say so and name the pins that remain.
  - `[low]` `[patch]` DEPLOY_CHECKLIST 33-4 told the operator to check the uncollected `BTC-USD-PERP.HYPERLIQUID`. It now names the collected `SOL-USD-PERP.HYPERLIQUID`.
  - `[low]` `[patch]` `test_a_weekly_bar_straddling_the_feed_start_is_null` passed on an incomplete week, so it could not catch a broken feed-start rule. It now builds a complete, known week 1 and adds a control (feed start moved to Monday gives `n == 1`). Verified to fail with the rule disabled.
  - `[low]` `[patch]` The docstring of `test_a_partially_null_stored_liquidation_group_is_ledgered_and_raised` said "no 500", contradicting §2.16 and the route. It is corrected.
  - `[low]` `[patch]` `test_the_recent_tail_read_survives_the_loop_appending_while_it_filters` could pass with no append. It now asserts that the race ran.
  - `[low]` `[patch]` The engine's capped-ledger test claimed the 200-character repr cut without asserting it. It now records the details and asserts the cut.
  - `[low]` `[patch]` `useLiveLiquidations.test.ts` lacked the stale-socket frame check that the derivs hook's test has. Added.
  - `[low]` `[patch]` `test_ws_live_candles.py`'s "(see the next test)" pointed the wrong way, and its log count depended on the ambient log level. It now names the pinning test and uses `caplog.at_level`.

## Design Notes

- **Prior attempt (2026-10-06):** the first dev session was cut off by a host power-off at 01:01 UTC with its work uncommitted. That work (tracked + untracked, this spec included) is pinned on branch `33-4-prior-attempt`. Start from it: `git read-tree -m -u HEAD 33-4-prior-attempt && git reset -q`, restamp `baseline_revision` with `git rev-parse HEAD`, then re-verify every task and AC rather than trusting the checkboxes.

- **Prior attempt 2 (2026-10-06, dev-2):** the second dev session was cut off by the 06:58 UTC host shutdown (power cut) with its work uncommitted, spec status already `in-review`. That tree (87 modified + 20 new files, this spec included, superseding `33-4-prior-attempt`) is pinned on branch `33-4-prior-attempt-2`. Start from it: `git read-tree -m -u HEAD 33-4-prior-attempt-2 && git reset -q`, restamp `baseline_revision` with `git rev-parse HEAD`, then re-verify every task and AC (run the full suites: platform pytest, ruff, mypy, frontend vitest) rather than trusting the checkboxes.

- **`liquidation_bars` notional comes from the raw rows, not the candle store.** 33.3 stores no notional: size × bankruptcy price is a per-row product, so it cannot be folded from the bar's size sums.
  - Both the chart (`liquidation_bars`) and the screener (`liq_notional_1h`) therefore sum `Liquidation.notional_units()`, and they agree.
  - `long_v/short_v/n` and null-ness come from the store, so D-160's feed-start rule holds.
- **Close for `basis_ml_bps`.** The store's `c` is the decoded float of an integer-unit price, so quantizing `Decimal(str(c))` at the row's `price_precision` recovers it exactly. The `Known limit` note goes in-code.
- **Live liquidations reuse `LiveCandleBus`'s subscription.** The buses invariant forbids a second `liquidations:raw` subscriber, so `LiveDerivsBus` is fed through the hook, the same way `attach(alert_engine)` already works.
- **Wire row example:** `{"instrument_id":"BTCUSDT-LINEAR.BYBIT","kind":"funding","t":1759700000000000000,"ts_init":…,"value":"0.0001","interval":28800,"next_funding_ns":1759708800000000000}`.

- **Implementation decisions (dev-2, 2026-10-06), re-verified against this spec rather than trusted from the prior attempt:**
  - Route `t` is ns on event rows (funding, liquidations, matching the live frames) and ms on bucketed rows (as `/api/candles`). Funding items also carry `annualised` (float or null).
  - `liquidation_bars.notional_units` is null when a bucket's raw rows disagree with its stored `liq_n` (never a partial sum); `Known limit:` in `views/derivatives.py`. Review fix: the raw rows are the **archived** ones only (capture archives a flush before the store applies it, so the counted set is archived; the live tail is never counted), and the Known limit names the live-edge lag (audit D-172). A composed wide bucket is null unless every constituent up to the store's live edge is stored (audit D-171).
  - `liquidation_window` has the spec signature `(db, iid, bar_seconds, start_ms, end_ms)`; `newest_row_t` was added for gap jumps. Boundaries test lists both as views query services.
  - Ranking: `oi_change_*` are absolute contract changes (as the read model's `oi_change`); OI has a 900 s staleness bound; the new fields refresh with the 60 s slow row. Known limits (D-166..D-169): the 1 h liquidation window ignores D-160's feed-start rule, funding/mark/index carry no staleness bound, volume windows count whole minutes, the 24 h range covers the history held.
  - Out-of-listed-scope edits needed to keep the suite green: `research/application/ranking_history.py` (`METRIC_COLUMNS` mirrors `COLS`), `verification/tests/test_ssot_trace.py` (accounts for the new keys), `tests/test_legacy_names.py` (`_RANKING_SITES` gains the three new `ranking_engine.*` ledger sites, as its own comment prescribes).
  - Docs: `derivs:raw` is DATA_DICTIONARY §1.27, the read models §2.16; audit rows D-163..D-169, D-162 GUARDED, D-122 FIXED (its code was deleted). The Docs page channel table and route list live in `pages/docs/kbData.ts`.
  - Verification run (re-run by dev-3, 2026-10-06): backend (kernel, views, data_api, ranking, candles, capture + all three venues, verification, research, alerting, archive, tests) 4682 passed / 8 skipped / 1 failed (`test_legacy_names::test_only_published_language_keeps_a_legacy_name`: the same 6 `Makefile`/untouched-file hits fail identically on the baseline `8c01502eec` export), no warnings; frontend 1027 vitest tests (50 files) + 7 codegen tests, lint 0 errors (3 warnings, all in untouched files), build green; `openapi.json`/`schema.ts` stable on regeneration; ruff format clean and ruff check clean but for the S603 on unchanged `data_api/tests/test_settings.py:16` (fails at baseline too); mypy 1.20.2 errors identical to the baseline's on the same files, nothing new.

- **Re-verification (dev-3, 2026-10-06):** every task and AC re-checked against the code, not the checkboxes; all hold. Found and fixed:
  - mypy, new in this change: `ranking/application/engine.py`'s `_ingest_batch` rebound the `except` variable `exc` in its ledger loop (renamed `cause`), and `ranking/tests/test_engine.py`'s `prices` parameter was typed `object` after its `type: ignore` was dropped (now `PriceHistory | None`).
  - SSOT: `views/derivatives.py` carried `_days`, a copy of `views.chart_series.day_slices`; it now imports `day_slices`.
  - Stale comments: `data_api/routes/indicators.py` still pointed at the deleted `indicator_series.py` (two spots; one is the `/indicator-values` docstring, so `openapi.json` was regenerated, `schema.ts` unchanged).

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests views/tests data_api/tests ranking/tests candles/tests capture/tests capture/venues/bybit/tests tests -q -p no:cacheprovider` -- expected: all pass, no warnings promoted to errors (TEST-04).
- `cd platform && ruff check . && ruff format --check . && mypy <touched python files>` -- expected: clean.
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass.
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && git diff --stat` -- expected: regenerated files committed with the change.


## Auto Run Result

Status: done

**Summary:**
- This was a follow-up review pass on the delivered story (spec `done` at `eae98bea0f`): a fresh adversarial and edge-case review of `8c01502eec..eae98bea0f`. The diff was split into three shards: core backend, views/API/frontend, and tests/docs.
- There was no intent gap and no bad spec. 14 patches were applied: 2 medium, 12 low.
  - The two medium patches are a live `liquidations:{iid}` duplicate on a replayed frame, and the new kernel readers not listing again across a consolidation mid-read.
  - The rest are a day-bounded liquidation read, a vectorised duplicate check, an accurate wide-page Known limit, dead code left by the deletion, two stale audit citations, a wrong instrument id in the deploy checklist, and five tests that could not fail or claimed more than they asserted.

**Files changed (this pass):**
- `platform/views/live_candles.py`: `_remember_liquidation` returns whether the event is new; only new events are forwarded to `LiveDerivsBus`.
- `platform/kernel/catalog_files.py`: the `_relisting` helper wraps `query_open_interest`, `query_price_columns`, `newest_ts_event_before` and `query_liquidations`. `_unique_price_rows` compares only differing copies.
- `platform/views/derivatives.py`: `liquidation_bars` reads the archive one UTC day at a time; `_bucket_window`'s Known limit states the general wide-page bound.
- `platform/data_api/app.py`: the unreachable `EmptyTopOfBook` branch and the unused import are removed.
- `platform/docs/DATA_INTEGRITY_AUDIT.md` (D-88, D-121), `platform/docs/DEPLOY_CHECKLIST.md` (33-4): the stale citations and the instrument id are corrected.
- Tests:
  - `kernel/tests/test_catalog_files.py`: relisting, both ways.
  - `views/tests/test_live_derivs.py`: replay forwarded once.
  - `views/tests/test_live_liquidations.py`: the race really runs.
  - `views/tests/test_derivatives.py`: the feed-start week is complete, with a control; a docstring is corrected.
  - `ranking/tests/test_engine.py`: the repr cut is asserted.
  - `data_api/tests/test_ws_live_candles.py`: comment and log level.
  - `data_api/tests/test_data_api.py`: the dead-path test is removed.
  - `frontend/src/hooks/useLiveLiquidations.test.ts`: the stale-socket frame.

**Review findings:** 14 patches applied (high 0, medium 2, low 12), 0 deferred, 44 rejected.
- Several rejects were refuted against the code:
  - The capture shutdown publish is bounded by the client's 1 s socket timeouts.
  - Bybit REST open interest stamps `ts_init = ts_event`.
  - `close_price` and `close_price_units` are null together.
  - The window-count bound `sqrt(7d / bar_seconds)` is correct.
- Others match the existing pattern of the candles route or the buses, or are Known limits the story already documents.

**Verification:**
- **Backend.** The kernel, views, data_api, ranking, candles, capture, Bybit and Hyperliquid venues, verification, archive, research, alerting and tests suites gave 4586 passed, 8 skipped, 10 failed.
  - Nine failures are `ConnectionRefusedError` on Redis 6379 (`data_api/tests/test_archive.py`, `test_rankings.py`, `test_rankings_mode.py`). With a throwaway Redis all 45 tests in those files pass.
  - The tenth is the pre-existing `tests/test_legacy_names.py::test_only_published_language_keeps_a_legacy_name`, which also fails at baseline.
- **Non-vacuity.** The relisting test fails with `_TRADE_LISTING_ATTEMPTS = 1`. The feed-start test fails with the straddle rule disabled.
- **Lint and types.** ruff 0.15.16 check and format are clean on all 11 touched Python files. mypy 1.20.2 on the same files under one config gives the same set of errors as the HEAD export.
- **Frontend.** vitest: 50 files, 1029 tests pass, and codegen passes. oxlint exits 0 (the 3 warnings are in untouched files).
- **OpenAPI.** The export equals the committed `frontend/openapi.json`; no response changed.

**Residual risks:**
- `query_liquidations` now raises `ValueError` rather than `FileNotFoundError` after two lost listings. Every caller already handles `ValueError` (a disagreeing duplicate).
- The documented Known limits stand unchanged: `derivs:raw` at most once, the funding/mark/index staleness bound, the liquidation window straddling a start, the wide-page bound and ns cursors in JSON.
