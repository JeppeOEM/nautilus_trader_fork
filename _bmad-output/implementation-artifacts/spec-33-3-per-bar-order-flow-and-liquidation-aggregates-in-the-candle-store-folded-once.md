---
title: 'Story 33.3: Per-bar order flow and liquidation aggregates in the candle store, folded once'
type: 'feature'
created: '2026-10-05'
status: 'done'
baseline_revision: 'a038ae331f230d27fb2ae230521302102ccdbe0c'
final_revision: '3c472240fac949e13d11135b20007498af6c9ff9'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The candle store keeps only `o,h,l,c,v,seconds_observed`, although the 1 s snapshot holds exact buy/sell volume and counts and Story 33.1 archives every Bybit liquidation. As a result no bar carries delta, counts, a VWAP numerator or forced flow. The picker's CVD replays raw seconds over a 7-day cap and resets at the left edge of the window.

**Approach:**
- Extend the one seconds→bars fold (`candles.domain.fold.fold_arrays`) to emit exact integer per-bar aggregates: `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n`, with per-row `price_precision`/`size_precision`.
- Feed liquidations into the same fold.
- Store, serve, push live and verify the new columns everywhere bars go today.
- Recompute CVD from the bars with an `anchor` parameter.

## Boundaries & Constraints

**Always:**
- **One fold.**
  - The live sink, the rebuild, the archive-side `raw_1s` read and the forming bar all get the new columns from `fold_arrays`, called through `fold_rows`, `CandleSeries.buckets` and `bars_from_rows`.
  - `v`, `o/h/l/c` and `seconds_observed` keep their current computation byte for byte: float sums of the decoded floats, exactly as today.
- **Integers only.**
  - The new columns are exact integer sums of the snapshot's integer units (`DydxSecondSnapshot.*_units`, `buy_count`/`sell_count`) and of the `Liquidation` rows' `size_units`. They never pass through `float`.
  - Values from rows of different precision are rescaled to the bucket's finest (max) precision by multiplying by `10**k`, which is exact.
  - A sum outside int64 raises `CandleOverflowError` (new, in `candles/domain/fold.py`). It never wraps and never rounds.
- **Null vs 0.**
  - A row without the columns is **null** (unknown), never 0. That covers a pre-migration row, or a bucket that merged a pre-migration part.
  - The liquidation columns are **null** for an instrument without a liquidation feed and **0** for one with the feed but no liquidation in the bucket.
  - "Has a feed" is one predicate, `kernel.liquidation.has_liquidation_feed(instrument_id)`: Bybit `-LINEAR.BYBIT` ids only.
- **Units.**
  - `buy_v/sell_v/liq_long_v/liq_short_v` count `10^-size_precision` of the row.
  - `pv = Σ close_price_units × (buy_units + sell_units)` over the bucket's traded seconds, in `10^-(price_precision+size_precision)`.
  - `liq_long_v` sums `LiquidatedSide.LONG` sizes (forced sells) and `liq_short_v` sums `SHORT` sizes (forced buys).
  - A liquidation's bucket is `bucket_start_ms(ts_event // 1_000_000, bar)`.
- **Identity.** `buy_v + sell_v == round(v × 10**size_precision)` on every bar where flow is non-null. `is_valid_candle` enforces it.
- **Added keys only (AD-D12).** Every candle dict, `CandleItem` and the `/ws/live` `bar` payload gain the 10 keys, appended after the existing ones. Every existing key keeps its value.
- **Liquidations exactly once in the store**, by `(instrument_id, venue_event_id)`.
- **Bucket rows.** A fragment that holds only a liquidation may create a bucket row with `seconds_observed = 0`. Every coverage query (`oldest_t(traded_only=False)`, `newest_t`, `bucket_starts`) counts only `seconds_observed > 0`, so a liquidation never makes a span look observed.
- Every new failure is ledgered (DATA-07). Every simplification carries a `Known limit:` comment with its upgrade path.

**Block If:**
- The design needs an edit under `nautilus_trader/` or `crates/`, or a new dependency.
- The SQLite build in use cannot `ALTER TABLE candles ADD COLUMN` on the `WITHOUT ROWID` table.

**Never:**
- Liquidation columns for Hyperliquid, spot or dYdX other than null. No invented rows.
- A second aggregation of seconds into bars outside `candles.domain.fold`. (The verification oracle stays independent and does its own Decimal fold by design.)
- A stored per-bar running total such as a CVD prefix column; see Design Notes.
- Writing `sprint-status.yaml`.
- Frontend chart or indicator UI work, which belongs to 33.6. The frontend changes stop at the regenerated schema and the parser test.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Traded bucket, Bybit linear | 3 s: buy 2/1 trade, sell 3/2 trades; 1 LONG liq of 4 units | `buy_v=2, sell_v=3, buy_n=1, sell_n=2, liq_long_v=4, liq_short_v=0, liq_n=1`, `buy_v+sell_v` = v in units | — |
| No liquidation feed | HL / spot / dYdX id | `liq_*` null; flow columns set | — |
| Feed, quiet bucket | Bybit linear, no liquidation | `liq_long_v=liq_short_v=liq_n=0` | — |
| Mid-bucket precision change | s1 at size_precision 2 (5 units), s2 at 3 (7 units) | bar `size_precision=3`, `buy_v=57` | — |
| Merge across flushes | existing row sp=2, incoming fragment sp=3 | existing units ×10, then summed; row sp=3 | — |
| Pre-migration row | old DB file opened by `connect_rw` | columns added; old rows' new columns null; a later fragment merging into it leaves them null | — |
| Unmigrated file read-only | data_api opens a file the collector has not migrated yet | dicts carry the new keys as null | — |
| Liquidation replayed | same `venue_event_id` applied twice (catch-up overlap) | counted once | — |
| Liquidation before its bucket's first second | fragment with `seconds_observed=0` | row created; not counted as observed coverage; later seconds merge in | — |
| int64 overflow | synthetic sum > 2^63−1 | refused | `CandleOverflowError`; the sink or rebuild failure path ledgers it |
| Impossible served bar | `buy_v+sell_v ≠` v in units, a negative count, `liq_n=0` with a volume > 0 | not served | `ImpossibleCandle` → 500, ledger `candles.invalid_candle` |
| CVD anchors | 1h bars: day1 10:00 delta +3, day1 11:00 flow null, day2 00:00 delta +2; stored prefix before day1 10:00 = +10 | `visible`: `[3, None, 5]`; `session`: `[3, None, 2]`; `all`: `[13, None, 15]` | an unknown anchor → `ValueError` (400) |

</intent-contract>

## Code Map

- `platform/kernel/second_snapshot.py`:
  - `SecondRow` Protocol (l.233): the fold's row contract.
  - `SecondOHLC` (l.272): a 7-field NamedTuple.
  - `DydxSecondSnapshot` already carries `price_precision, size_precision, close_price_units, buy_volume_units, sell_volume_units, buy_count, sell_count`.
- `platform/kernel/catalog_files.py`:
  - `query_second_ohlc`/`_ohlc_rows` (l.118-180): the live seed, catch-up and `raw_1s` reads.
  - `second_ohlc_arrays` (l.324): the rebuild read.
  - `query_trade_columns` (l.395): the pattern for a dedup/disagreeing-copy reader.
- `platform/kernel/liquidation.py`: `Liquidation`, `LiquidatedSide`, and `venue_event_id` as the dedup key (D-150).
- `platform/candles/domain/fold.py`: `fold_rows`, `fold_arrays` (output `(bar,t) -> [o,h,l,c,v,observed]`), `bucket_start_ms`, `BAR_SECONDS`.
- `platform/candles/domain/candle.py`: `is_valid_candle`.
- `platform/candles/domain/candle_series.py`: `CandleSeries` (the seconds watermark, `buckets`).
- `platform/candles/infrastructure/sqlite_store.py`:
  - `_SCHEMA` and `_UPSERT` (the NULL-safe SQL merge).
  - `write_buckets`, `apply_seconds`, `rebuild`, `rebuild_from_arrays`, `prune`, `CandleStore`.
- `platform/candles/application/`:
  - `sink.py`: `CandleSink`.
  - `rebuild.py`: `rebuild_instrument`, day by day.
  - `forming.py`: `bars_from_rows`, `forming_bar`.
  - `queries.py`: `_COLUMNS`, `_candle`, `window`, `oldest_t`, `newest_t`, `bucket_starts`, `candle_dicts_for_window`.
- `platform/candles/tests/`:
  - `test_schema_is_frozen.py` + `fixtures/candle_store_schema.sql`.
  - `test_candles.py:235-239` pins the forming-bar key list.
- `platform/capture/application/ports.py:246` `SecondSink`.
- `platform/capture/application/capture_service.py`:
  - `_flush_once` (l.1037): `flushed_seconds`; `Liquidation` batches are keyed `(Liquidation, iid)`.
  - `_apply_to_candle_store` (l.1191).
  - `_catch_up_candle_store` (l.1232).
- `platform/views/live_candles.py`: `LiveCandleBus`.
  - `_second_row` (l.115).
  - `_recent`/`recent_rows`.
  - `seed`, `_apply_to_buffer`, `_publish`.
  - `run`, which subscribes `snapshots:raw` only.
- `platform/views/chart_series.py`:
  - `RecentRows`, `_catalog_plus_recent`, `_checked`, `_parquet_page`, `candle_page` (l.760-950).
  - `indicator_values_page` (l.1055-1113), which builds `ReplayWindow` with `MAX_QUERY_SPAN_SECONDS` and has a Known limit about CVD.
- `platform/views/indicator_picker.py`:
  - `ReplayWindow` (l.515) and `CustomIndicatorSpec` (l.534).
  - `custom_catalog_json` (l.564): no `choices` for custom indicators.
  - `_cvd_replay` (l.603-650), `_second_snapshots`, and `check_params` (l.904).
- `platform/views/ranking_columns.py` `_latest_of_group` (l.241): the second `ReplayWindow` builder.
- `platform/data_api/routes/candles.py`: `CandleItem` (l.57) and `CandlesResponse`.
- `platform/data_api/routes/indicators.py`: `IndicatorCatalogEntry.choices`.
- `platform/data_api/export_openapi.py` → `frontend/openapi.json`, then `npm run codegen` → `frontend/src/api/schema.ts`. `data_api/tests/test_app_frontend.py:103` fails when that output is stale.
- `platform/frontend/src/hooks/useLiveCandle.ts` (+ `.test.ts`): the parser already tolerates extra keys.
- `platform/verification/`:
  - `domain/reference_signals.py`: `fold_candles`, `RefCandle`, `_fold_bucket` (the independent Decimal oracle).
  - `domain/catalog_check.py`: `StoredBar`, `judge_bar`.
  - `domain/candle_check.py`: `ServedBar`, `served_agreement`, `judge_day_width`.
  - `infrastructure/catalog_scan.py:269`: the store SELECT.
  - `infrastructure/served_candles.py:50`: `_ITEM_KEYS`, which refuses unknown keys.
  - `infrastructure/liquidation_reader.py`.
  - `application/candles.py`.
  - `tests/test_candles.py`: its 17 pre-existing failures come from a missing instrument definition (Story 32.5 route check); `data_api/tests/test_candles.py` `_define` is the fix pattern.
- `platform/tests/test_boundaries.py:1442` `VIEWS_QUERY_SERVICES`: the allow-list of candles query services views may call.
- `platform/archive/nightly.py:140`: `build_candles` runs `candles.rebuild --day D` for closed days while the collectors run.
- Docs:
  - `platform/docs/DATA_DICTIONARY.md`: §2.5 (l.2613-2683, with the CVD Known limit at l.2668), §2.7 (l.2697-2727), §2.14 (l.2992-3068), §4 lineage (l.3278-3306), §1.21 class table (l.1712-1725).
  - `platform/docs/DATA_INTEGRITY_AUDIT.md`: last row D-153 (l.329), with `## 3. Conclusions` after it.
  - `platform/docs/DEPLOY_CHECKLIST.md` `## Deferred operator actions` (l.721): entries `### <story-key> <title> (commit: …)` + `- [ ]`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/second_snapshot.py`:
  - Add to `SecondRow` the read-only properties `price_precision`, `size_precision`, `close_price_units`, `buy_volume_units`, `sell_volume_units`, `buy_count`, `sell_count`.
  - Append the same fields to `SecondOHLC`, all required.
  - Update every `SecondOHLC`/stand-in construction, test helpers included.
- [x] `platform/kernel/catalog_files.py`:
  - `_ohlc_rows`/`query_second_ohlc` fill the new `SecondOHLC` fields from the integer columns.
  - `second_ohlc_arrays` also returns int64 arrays `close_units, buy_units, sell_units, buy_n, sell_n, price_precision, size_precision`.
  - New `query_liquidations(catalog_path, iid, start_ns, end_ns) -> list[Liquidation]`:
    - `ts_event` in the inclusive window; files chosen by `ts_init` span ± `MAX_TS_INIT_SKEW_NS`; sorted by `ts_event`.
    - Identical copies of one `venue_event_id` are kept once. Copies that disagree raise `ValueError` (the caller ledgers it).
    - Returns `[]` when the directory is absent.
- [x] `platform/kernel/liquidation.py`: add `has_liquidation_feed(instrument_id: str) -> bool`, true for ids ending `-LINEAR.BYBIT`, with a docstring that cites §1.26 and Story 33.2.
- [x] `platform/candles/domain/fold.py`:
  - `fold_arrays` takes a required keyword `flow: FlowArrays` and an optional keyword `liquidations: LiquidationArrays | None`.
    - `FlowArrays` (NamedTuple of int64 arrays) holds `close_units, buy_units, sell_units, buy_n, sell_n, price_precision, size_precision`.
    - `LiquidationArrays` holds `ts_ms, long: bool, size_units, size_precision, venue_event_id`.
    - `None` means no feed, so the `liq_*` columns are null. An empty `LiquidationArrays` means feed but no rows, so they are 0.
  - Return `(bar, t) -> FoldedBucket`, a small mutable dataclass with the 6 existing fields followed by the 10 new ones.
  - `fold_rows(rows, *, liquidations=None, bars=...)` builds both from rows.
  - Per-bucket precision is the max over its seconds and liquidations. Sums are exact, and an overflow raises `CandleOverflowError`.
  - Update the module docstring's Known limit: bars stay floats, the new columns are integers. Add a `pv` Known limit: the second-close VWAP, with Story 32.8's trade reader as the upgrade path.
  - Add `merge_buckets(old, new) -> FoldedBucket`, a pure function:
    - Today's NULL-safe OHLC rules, `v`/`seconds_observed` added.
    - Precision rescale to the max.
    - Null propagates per group (flow, liq).
    - The int64 check.
- [x] `platform/candles/domain/candle_series.py`: `buckets(rows, liquidations=None)` passes through to the fold.
- [x] `platform/candles/domain/candle.py`: `is_valid_candle` additionally refuses a dict where:
  - the flow keys are present but `buy_v + sell_v != round(v * 10**size_precision)`;
  - any of `buy_v, sell_v, buy_n, sell_n, liq_long_v, liq_short_v, liq_n` is negative;
  - `liq_n == 0` while a liquidation volume is > 0, or `liq_n > 0` while both volumes are 0;
  - flow columns are partly null.

  Absent or all-null new keys stay valid, so old shapes pass.
- [x] `platform/candles/infrastructure/sqlite_store.py`:
  - `_SCHEMA` declares the 10 nullable INTEGER columns for a new file, plus `liquidations_applied(instrument_id, venue_event_id, ts_event, PRIMARY KEY(instrument_id, venue_event_id)) WITHOUT ROWID`.
  - `_migrate(db)`, run by `connect_rw`, adds missing columns with `ALTER TABLE … ADD COLUMN` (null on old rows).
  - Replace `_UPSERT` with read-merge-write through `merge_buckets`: one SELECT per width over the keys' `t` range, then `INSERT OR REPLACE`.
  - `apply_seconds` folds with `liquidations=empty` when `has_liquidation_feed(iid)`, else `None`.
  - New `apply_liquidations(db, iid, liqs)`: `INSERT OR IGNORE` into `liquidations_applied`, fold only the newly inserted ones with `FlowArrays` of zero length, merge, and return the count. A no-feed id is refused with `ValueError`.
  - `rebuild`/`rebuild_from_arrays`:
    - Take the day's liquidations.
    - Delete that day's `liquidations_applied` rows with the day's candles.
    - Re-insert the day's ids.
    - Write the fold.
  - `prune` also drops `liquidations_applied` rows older than 2 days (the catch-up horizon is 1 day).
  - Update the frozen fixture and its test with the new text.
- [x] `platform/candles/application/sink.py`: add `CandleSink.apply_liquidations(iid, rows)`.
- [x] `platform/candles/application/rebuild.py`: `rebuild_instrument` passes each day's `query_liquidations` (feed ids only) to `rebuild_day`.
- [x] `platform/candles/application/queries.py`:
  - `_candle`, `window` and `candle_dicts_for_window` emit the 10 keys after the existing ones.
  - `candle_dicts_for_window` gains `liquidation_rows_fn` (None → no feed).
  - A reader over an unmigrated file selects NULL for missing columns (via `PRAGMA table_info`, cached per connection).
  - Coverage queries add `seconds_observed > 0`.
  - New `flow_delta_before(db, iid, bar_seconds, before_ms) -> tuple[int, int] | None`: the exact Σ`(buy_v − sell_v)` of stored non-null bars with `t < before_ms`, from `GROUP BY size_precision` rescaled, returned as `(units, precision)`. It returns `None` when the store holds none.
- [x] `platform/candles/application/forming.py`: `bars_from_rows`/`forming_bar` gain `liquidations=None` and emit the new keys. Update the frozen-shape docstrings and the `test_candles.py:239` key-list pin to the extended order.
- [x] `platform/capture/application/ports.py` + `capture_service.py`:
  - `SecondSink.apply_liquidations`.
  - `_flush_once` collects flushed `(Liquidation, iid)` batches. After the seconds, `_apply_to_candle_store` hands them over per instrument with the same isolation and ledgering (one line per flush).
  - `_catch_up_candle_store` also applies `query_liquidations(now − 1 day, now)` for each watermark id with `has_liquidation_feed`; the dedup table makes the overlap harmless.
  - Update the test doubles.
- [x] `platform/views/live_candles.py`:
  - `_second_row` copies the new fields.
  - `run` also subscribes `liquidations:raw` (a JSON array of `Liquidation.to_dict`). Each decoded row (a malformed one is ledgered `live_candles.liquidation`) goes to:
    - a per-instrument `RECENT_SECONDS`-bounded deque (`recent_liquidations(iid, start, end)`);
    - every buffer of that iid whose current bucket holds its `ts_event`. That buffer is deduped by `venue_event_id`, reset at bucket roll and republished.
  - `seed` also reads `query_liquidations` for the bucket.
  - `_publish` passes the buffer's liquidations (or `None` for a no-feed id) to `forming_bar`.
- [x] `platform/views/chart_series.py`:
  - `_catalog_plus_recent` gets a liquidation twin: catalog plus recent tail, deduped by `venue_event_id`.
  - `_parquet_page` passes it when `has_liquidation_feed`.
  - `RecentRows` gets a `RecentLiquidations` sibling. `candle_page`, `indicator_values_page` and any other `recent_rows`-taking page function take `recent_liquidations` as a keyword. It is wired from `buses.live_candle_bus.recent_liquidations` everywhere `recent_rows` is: `data_api/routes/candles.py:107`, `indicators.py:400` and `footprint.py:124`.
  - `indicator_values_page` passes `candles_dir` into `ReplayWindow`. Remove its CVD Known limit text; the limit stays for the other raw replays.
- [x] `platform/views/indicator_picker.py`:
  - `ReplayWindow.candles_dir: str | None = None`.
  - `CustomIndicatorSpec.choices` is emitted by `custom_catalog_json`, so CVD's `{"params": {"anchor": "visible"}, "panel": "oscillator", "choices": {"anchor": ["session", "visible", "all"]}}`; `check_params` checks membership.
  - `_cvd_replay` reads `buy_v − sell_v` from the bars:
    - It keeps exact integer totals rescaled to the max precision and emits floats at the edge.
    - A null bar → None, and the total carries on.
    - `session` = the UTC day of the bar start (`bucket_start_ms(t, 86_400)`), so on 1D and wider every bar resets.
    - `all` = `flow_delta_before` at the widest stored width dividing `bar_seconds` (10m→5m, 30m/45m→15m, 1W→1D) + the running total.
    - `all` without `candles_dir` raises `ValueError`.
    - Live mode computes too.
  - Delete `_second_snapshots` if it has no other user.
  - `ranking_columns._latest_of_group` passes `candles_dir`.
  - Add the new query service to `tests/test_boundaries.py` `VIEWS_QUERY_SERVICES`.
- [x] `platform/data_api/routes/candles.py`:
  - `CandleItem` gains `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n, price_precision, size_precision: int | None = None`. The item precisions govern the item's units.
  - Wire `RecentLiquidations` from `data_api/buses.py`'s bus.
  - Regenerate `frontend/openapi.json` and `frontend/src/api/schema.ts`.
- [x] `platform/frontend/src/hooks/useLiveCandle.test.ts`: a bar with the new keys and one without both parse to the same chart datum.
- [x] `platform/verification/`:
  - `RefCandle`/`_fold_bucket` (Decimal) add `buy_v, sell_v, buy_n, sell_n, pv` and the `liq_*` group from the day's `Liquidation` rows (`liquidation_reader`), null for a no-feed venue (`LIQUIDATION_VENUES` + LINEAR).
  - `StoredBar` + the SELECT (missing columns → NULL) + `judge_bar` compare them exactly in units, null ≠ 0.
  - `served_candles._ITEM_KEYS`/`ServedBar`/`served_agreement` accept and compare the new keys.
  - Fix the `tests/test_candles.py` fixture to write an instrument definition.
  - Update DATA_DICTIONARY §1.21 for the new comparison.
- [x] Tests (TEST-01/03/04, hand-computed):
  - `candles/tests/`: fold units; precision change; merge rescale; null propagation; null/0 by feed; liquidation dedup and liquidation-only rows; migration of a recorded old-schema file; unmigrated read-only reads; the `v` identity over the `test_forming_matches_stored` day; overflow; coverage queries ignoring `seconds_observed = 0`; rebuild with liquidations; `flow_delta_before` across mixed precision.
  - `views/tests/`:
    - CVD anchors against a hand fold (updating `test_indicator_picker_custom.py`'s CVD tests to bar-based input).
    - The live bus folding a `liquidations:raw` frame and its malformed-frame ledger.
    - `_parquet_page` with liquidations.
  - `data_api/tests/`:
    - `/api/candles` items carry the keys.
    - An invalid identity → 500.
    - The catalog entry with `choices`.
  - `capture/tests/`: flushed liquidations reach the sink, and a sink failure is ledgered.
  - `verification/tests/`: the new columns are proven, and a tampered `buy_v` fails.
- [x] Docs (MR4, DESIGN-03):
  - `DATA_DICTIONARY.md`:
    - §2.5: the shapes gain the keys.
    - New **§2.15 "Per-bar order flow and liquidation aggregates"**, placed after §2.14: each column, its units, the null/0 rule, the merge, the `v` identity, the `pv` Known limit, the CVD anchors and the "all" retention edge.
    - §2.7: remove the CVD 7-day limit.
    - §4: lineage rows.
  - `DATA_INTEGRITY_AUDIT.md`: section "2026-10-05 — Story 33.3", rows D-154 onward:
    - migration null, never 0;
    - the `v` identity;
    - liquidation null vs 0, and that a feed-outage window reads 0 in the bar (Known limit; the coverage record names it; upgrade path: the rebuild nulls buckets overlapping `liquidations_unrecoverable`);
    - liquidation exactly-once and liquidation-only rows not counted as coverage;
    - `pv` as the second-close VWAP;
    - CVD `all` anchoring at the retention edge for 1m/5m/10m.
  - `DEPLOY_CHECKLIST.md` deferred actions `### 33-3 …`: after deploy, run `python3 -m candles.rebuild --venue BYBIT` and `--venue HYPERLIQUID` over the whole history to fill the null columns. Today's pre-deploy part fills at that day's nightly. Add a verify step.
  - The Docs page, if it describes CVD's window.

- [x] **Review-loop 1 amendments** (see Spec Change Log, 2026-10-05). Do these on top of the restored prior attempt:
  - **Liquidation feed start (the bad_spec fix).** Add `kernel.catalog_files.liquidation_feed_since_ns(catalog_path, iid) -> int | None`: the earliest archived `Liquidation.ts_event` of the id, read column-projected from the earliest file(s) by filename span. It returns None when nothing is archived. Every archive-side fold of a feed id passes it as a bound:
    - the rebuild (`rebuild_instrument` → `rebuild_day`), the `raw_1s` page (`_parquet_page`) and the technicals fallback (`ranking_columns._read_candles`);
    - the fold gets `LiquidationArrays.known_from_ms` (or an equivalent keyword). A bucket whose start is before `bucket_start_ms(known_from_ms, bar)` gets **null** `liq_*`, never 0. None (nothing archived) gives null for every bucket.
    - The live sink and the live bus are unaffected: their feed is running, so their 0 stays.

    The verification oracle derives the same bound independently from its own raw liquidation reader and agrees. Add a `Known limit:` (kernel + §2.15): the archive cannot say when the feed started, so the span between the feed's start and the id's first liquidation is null (conservative). The bucket holding the first liquidation counts only what was archived. Upgrade path: a durable per-id "feed confirmed since" marker written by capture. Add an audit row for it, and a test: a pre-feed day rebuilds null, not 0; the day of the first liquidation is null before its bucket and known from it.
  - **`v` identity ceiling.** Keep the exact check. In `candle.py` and §2.15/D-155, add a `Known limit:` with the numbers. The float sum's worst-case error is about `n · 2^-53 · U` units: 1D n=86,400 → U ≈ 5.2e10 units, 1W n=604,800 → U ≈ 7.4e9. The typical (random-walk) error is far below that. State the collected instruments' magnitude: BTC/ETH linear and spot, HL SOL, about 1e8 to 1e10 units a week. A breach is loud (500 + `candles.invalid_candle`), never silent. Upgrade path: derive `v` from the units once the store is integer.
  - **Atomic merge.** `write_buckets`' read-merge-write, and every caller path (`apply_seconds`, `apply_liquidations`, `rebuild*`), runs inside `BEGIN IMMEDIATE … COMMIT`, so the SELECT and the REPLACE are one transaction. Test: a second connection cannot interleave.
  - **No silent null→0 in the rebuild read.** `second_ohlc_arrays`' flow columns refuse a null (a `LegacySnapshotLayoutError`/`ValueError` naming the file and column). Only the OHLC price columns may be null (no trade). Test it.
  - **Capture.** Flushed liquidation rows of an id where `has_liquidation_feed` is false are ledgered (one line per flush, `collector.candle_store`), never skipped quietly.
  - **Live bus.**
    - A liquidation-driven republish goes to listeners only, not to `BarObserver`s, which keep one `on_bar` per second tick with monotonic `ts_ns`.
    - `_dispatch` ledgers an unknown channel (`live_candles.payload`) instead of treating it as `snapshots:raw`.
    - Add `Known limit:` text for two cases: a liquidation that arrives after its bucket rolled is in the store, not the last published forming bar; an observer-only pair's buffer misses liquidations from before it existed.
  - **Predicates pinned.** `kernel.liquidation.has_liquidation_feed`'s docstring names the verification oracle's independent restatement. A `platform/tests/` test asserts the two agree on a table of ids (BTC/ETH linear/spot Bybit, HL, dYdX).
  - **CVD documentation.** `_cvd_replay`'s docstring, §2.15 and the audit row get `Known limit:` text:
    - a null-flow bar is None and the total carries on without its term (the contract's rule), so a level after a pre-migration gap omits that bar until the operator's history rebuild;
    - `all`'s prefix sums known stored bars only;
    - page bars older than the store accumulate from 0.
  - **JSON range.** §2.15 and `CandleItem`'s docstring get a `Known limit:`: `pv` (and very large volumes) can exceed 2^53, which a browser's `JSON.parse` rounds. Python consumers get exact ints. The UI uses `pv` only as a VWAP numerator (`pv / (buy_v + sell_v)`), where a 2^-53 relative error is invisible. Upgrade path: a string encoding.

- [x] **Review-loop 2 amendments** (see Spec Change Log, loop 2). Do these on top of the restored prior attempt 2; they supersede loop 1's bucket rule:
  - **One feed-start rule everywhere.** A feed id's liquidation columns are known for a bucket only if the bucket **starts at or after** the id's feed start `since_ns`. A bucket that straddles `since_ns`, or lies before it, is **null** at every width. This replaces loop 1's "known from the bucket holding the first liquidation", which made the 1D bar known while that day's earlier 1m bars were null. Where `since_ns` comes from on each path:
    - **Rebuild, `raw_1s` and technicals fallback:** `since_ns` = min(`liquidation_feed_since_ns` from the archive, earliest supplied row), so a live-tail row older than the archive bound moves the bound instead of raising. The fold derives the effective bound itself and never raises on a row before the given bound.
    - **Live sink:** a persisted `liquidation_feed_since(instrument_id PRIMARY KEY, since_ns)` table in the store. `apply_liquidations` sets it to the min of the stored value and the rows' min `ts_event`. The rebuild sets it to min(stored, archive bound). `apply_seconds` gives a fragment liquidations=empty (known 0) only for buckets starting at or after it, and None otherwise; no row means None everywhere. Null then propagates through `merge_buckets`, so the live store and a later rebuild of the same day agree bucket for bucket.
    - **Live bus forming bar:** `since_ns` = min(archive `liquidation_feed_since_ns`, read once per instrument and refreshed with the seed; earliest live liquidation seen), with the same bucket rule.
    - **Oracle:** the same rule, derived independently.

    Tests:
    - a hand-computed day where the first liquidation is at 15:00:30. The 1m bucket 15:00 is null, 15:01 onward is known, and the 1D bucket is null.
    - live sink then rebuild of the same seconds/liquidations: identical rows.
    - a tail row older than the archive bound: no error, and the bound moves.

    Update the Known-limit and audit text (D-160) and the DEPLOY_CHECKLIST verify step.
  - **Migration race.** `_migrate` runs under `BEGIN IMMEDIATE` and re-reads `PRAGMA table_info` inside the lock, so a collector and a rebuild opening an unmigrated file together cannot both `ADD COLUMN`. Test it with two connections.
  - **Live bus cost and robustness.**
    - `handle_liquidations` applies every row of one frame, then republishes each affected buffer **once** (not per row).
    - `_remember_liquidation` keeps an incremental max and dedups by `venue_event_id`, with no O(n) `max` per row.
    - A fresh buffer's first tick pulls the pair's recent liquidations of its bucket.
    - An exception while republishing one buffer, from the liquidation path or the snapshot path, is ledgered (`live_candles.publish`) for that pair and does not escape to `run()`, so one bad pair never drops the Redis subscription.
    - Tests for the batching (one publish per frame) and the isolation.
  - **Capture catch-up.** Liquidation catch-up covers every feed id among the watermarks ∪ the plan's ids. It is bounded to the last day and runs independently of the seconds-behind branch. The `CANDLE_STORE_BEHIND` line says liquidations are left to the rebuild too. Add a test or assertion that `liquidations_applied` retention (2 days) exceeds `_CATCH_UP_MAX_NS` plus flush lag.
  - **Store hygiene.**
    - `apply_liquidations` refuses (`ValueError`) a row whose `instrument_id` is not the `iid` it is applied under.
    - Fix `platform/candles/__init__.py`'s stale `_UPSERT` wording.
  - **Verification.**
    - The oracle reports disagreeing copies of one `venue_event_id` (it never silently keeps the last).
    - `first_ts_event` is computed once per instrument per run (cached), not once per day or per caller.
  - **Known limit (doc).** A liquidation in a bucket with no trade is stored but not served, since a bar exists only for a traded bucket, so a 1m sum can fall short of the 1h value. Story 33.4's `liquidation_bars` reads the columns without the `o IS NOT NULL` filter. Add this to `fold.py`/§2.15 and an audit row.

**Acceptance Criteria:**
- Given a store file created before this story, when a collector opens it, then the 10 columns exist, every old row's new columns are null, and both the live sink and `python -m candles.rebuild --day D` fill them for new or rebuilt buckets.
- Given any bar served by `/api/candles`, built by the rebuild or published on `/ws/live`, when flow is non-null, then `buy_v + sell_v` equals `v` in units, and the bar equals the oracle's fold of the same seconds and liquidations (`verification.candles`).
- Given a Bybit linear id whose archive holds liquidations only from day F onward, when any day before F is rebuilt or served from `raw_1s`, then its bars' `liq_*` are null, never 0, and the oracle agrees.
- Given a feed id, when the same seconds and liquidations are applied live and then the day is rebuilt, then every bucket's `liq_*` is identical on both paths, and a bucket straddling the feed start is null at every width.
- Given CVD on a 1D chart over more than 7 days, when values are requested, then every bar has a value from its stored flow, with no raw-second replay and none of the 7-day None.
- Given the change, when the verification commands run, then every touched package's suite passes. The only pre-existing failure allowed is `tests/test_legacy_names.py` ×1; the verification `test_candles.py` ×17 are fixed.

## Spec Change Log

### 2026-10-05 — review loop 1 (bad_spec)
- **Trigger** (`[high]` `[bad_spec]`): `has_liquidation_feed` is a static per-id predicate. A full-history rebuild, which the deferred action asks for, therefore stored `liq_*` = 0 ("known: none") for every Bybit linear day archived before the Story 33.1 feed existed. `raw_1s` pages served the same false zeros, and the oracle uses the same predicate, so it agreed. That fills a capture gap with 0 (DATA-01), the very danger D-156 names.
- **Amended** (outside the intent contract, whose predicate stays the venue/market rule):
  - Added the task "Review-loop 1 amendments": the archive-side known-from bound `liquidation_feed_since_ns`, plus 12 review patches folded in as tasks;
  - added an acceptance criterion for the pre-feed days.
- **Known-bad state avoided:** archive-side bars claiming zero liquidations for time no feed captured.
- **KEEP:** the prior attempt is pinned on branch `33-3-prior-attempt` (commit e96ee6352e). It passed the full suite (2851 passed; only the pre-existing redis/legacy-names failures remained). Restore it whole: `git checkout 33-3-prior-attempt -- platform`. Keep all of these and change only what the amendment task names:
  - the fold, `FoldedBucket` and `merge_buckets`;
  - the migration and `liquidations_applied`;
  - the `seconds_observed > 0` coverage queries;
  - `flow_delta_before` and the CVD anchors;
  - the live bus `liquidations:raw` path;
  - the verification oracle extension and its instrument-definition fixture fix;
  - the docs.

### 2026-10-05 — review loop 2 (bad_spec)
- **Trigger:**
  - `[medium]` The live sink wrote `liq_*` = 0 for a feed id while the rebuild wrote null before its first archived liquidation, so served history changed meaning after the nightly rebuild.
  - `[medium]` Loop 1's rule ("known from the bucket holding the first liquidation") made the 1D bar known while that day's earlier 1m bars were null.
  - `[medium]` A live-tail liquidation older than the archive bound raised a bare `ValueError` (500, not ledgered).
- **Amended:** added the task "Review-loop 2 amendments". It defines one feed-start rule for every path (a bucket is known only if it starts at or after `since_ns`; `since_ns` is the min over the archive, the tail and the store's persisted table), plus 12 review patches folded in. Also added an acceptance criterion for live/rebuild agreement.
- **Known-bad state avoided:** the same bucket read 0 live, null after a rebuild, and known at 1D but null at 1m.
- **KEEP:** restore prior attempt 2 whole: `git checkout 33-3-prior-attempt-2 -- platform` (commit e0f7f70a75), then `git reset -q -- platform`. Then remove any leftover `platform/candles/tests/fixtures/candle_store_upsert.sql`. It passed 4211 tests, with only the 10 known failures. Keep everything, including all of loop 1's amendments, except what the loop-2 task changes.

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 1 (high 1, medium 0, low 0)
- patch: 12 (high 0, medium 4, low 8)
- defer: 0
- reject: 5 (low 5)
- addressed_findings:
  - `[high]` `[bad_spec]` Liquidation columns were 0 for history archived before the feed existed (static predicate) → spec amended with an archive-side known-from bound; code reverted (WIP pinned on `33-3-prior-attempt`) and re-derived.
  - The 12 patch findings were folded into the amendment task so the re-derivation carries them:
    - `[medium]` the `v` identity ceiling;
    - `[medium]` a non-atomic read-merge-write;
    - `[medium]` `fill_null(0)` on flow columns;
    - `[medium]` non-feed liquidations dropped unledgered;
    - `[low]` observers notified with an older `ts_ns`;
    - `[low]` unknown-channel fallback;
    - `[low]` late-liquidation and observer-only known limits;
    - `[low]` duplicated predicate unpinned;
    - `[low]` technicals fallback without liquidations;
    - `[low]` CVD null/`all`/older-than-store semantics undocumented;
    - `[low]` JSON > 2^53.

### 2026-10-05 — Review pass (loop 1 re-derivation)
- intent_gap: 0
- bad_spec: 3 (high 0, medium 3, low 0)
- patch: 12 (high 0, medium 3, low 9)
- defer: 1 (low 1)
- reject: 2 (low 2)
- addressed_findings:
  - `[medium]` `[bad_spec]` Live 0 vs rebuild null before the first archived liquidation → one feed-start rule with a persisted live bound; re-derived.
  - `[medium]` `[bad_spec]` The bucket straddling the feed start was known at wide widths and null at narrow ones → straddling buckets are null at every width.
  - `[medium]` `[bad_spec]` A tail liquidation older than the archive bound raised a bare 500 → the bound is the min over all sources and the fold never raises.
  - The 12 patches were folded into the loop-2 task:
    - `[medium]` migration race;
    - `[medium]` per-row refold in a liquidation cascade;
    - `[medium]` a republish exception kills the Redis subscription;
    - `[low]` O(n²) tail max and duplicates;
    - `[low]` a fresh buffer misses tail liquidations;
    - `[low]` catch-up only covers ids with a watermark;
    - `[low]` retention constant not bound to the catch-up window;
    - `[low]` no instrument check in `apply_liquidations`;
    - `[low]` stale `_UPSERT` doc;
    - `[low]` the oracle silently resolves conflicting duplicates;
    - `[low]` repeated `first_ts_event` scans;
    - `[low]` untraded-bucket liquidations unserved (Known limit).

### 2026-10-05 — Review pass (loop 2 re-derivation, final)
- intent_gap: 0
- bad_spec: 0
- patch: 14 (high 0, medium 4, low 10)
- defer: 0
- reject: 2 (low 2)
- addressed_findings:
  - `[medium]` `[patch]` `SecondSink`/`CandleSink` docstrings stated the call order backwards. Fixed: liquidations go before seconds, with the reason (D-160).
  - `[medium]` `[patch]` The `v`-identity margin used wrong magnitudes. Corrected: BTC spot is about 1e11 units/week, which exceeds the 1W worst case; the typical error is about 1e-2 units. Updated in `candle.py`, §2.15 and D-155.
  - `[medium]` `[patch]` Read-time folds refused an int64 overflow that never reaches SQLite. Fixed: the fold sums exact Python ints, and only `check_storable` refuses, at the store write. The stored ceiling is documented.
  - `[medium]` `[patch]` A fresh buffer's first tick wiped liquidations the seed had already placed. Fixed: a reset happens only on a real bucket change.
  - `[low]` `[patch]` Ten more fixes:
    - Known limit for a null-group bucket with a claimed id;
    - the ledger counts instruments, not entries;
    - the failed-liquidation-apply recovery path is documented;
    - the feed start is read off the event loop, refreshed at most every 300 s, and the store's persisted start is used first on pages and technicals (new `queries.liquidation_feed_since`);
    - the oracle reads span-pruned files once per run;
    - `flow_delta_before` counts observed rows only, and flow without a precision raises;
    - the `CandleStore` docstring on closed-day rebuilds is corrected;
    - `_ohlc_rows` refuses null flow columns;
    - the seed publishes through the isolated publish and un-marks a failed seed;
    - the module-state boundary test is fixed.

### 2026-10-06 — Review pass (follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 4 (high 0, medium 0, low 4)
- defer: 0
- reject: 9 (low 9)
- addressed_findings:
  - `[low]` `[patch]` `_immediate` committed outside its `try`, so a failed COMMIT (`SQLITE_BUSY`, I/O) left the transaction open and every later `BEGIN IMMEDIATE` on that connection failed. Fixed: the commit is inside the `try`, with a rollback when a transaction is still open. Test `test_a_failed_commit_rolls_back_so_the_connection_takes_the_next_write`.
  - `[low]` `[patch]` A rebuild reads the archive before taking its lock. A closed day's liquidation flushed and applied live in between would have its claim deleted and be refolded out of the bar, permanently short. Fixed: `_replace_applied` refuses with `RebuildRaceError`, the rebuild rolls back, the live rows keep it, and the failure is ledgered by the rebuild CLI. Test `test_a_rebuild_missing_a_liquidation_the_live_sink_applied_is_refused`; CandleStore docstring and audit D-157 updated.
  - `[low]` `[patch]` The live bus, §2.15 and D-160 claimed the forming bar is "never the reverse" (never known where stored is null). That is false until a rebuild lowers the store's feed start to the archive's. Corrected in the `live_candles` module and class docstrings, DATA_DICTIONARY and D-160.
  - `[low]` `[patch]` "Never a false 0" on the forming bar overlooked unreceived `liquidations:raw` frames (pub/sub is at most once; a data_api restart before the flush). Added a `Known limit:` with upgrade paths to `LiveCandleBus`, and qualified the same claim in §2.15 and D-160.

## Design Notes

- **Why a Python merge replaces `_UPSERT`.** Rows with different precisions need rescaling, and SQLite silently turns an int64 overflow in `+` into REAL. A pure `merge_buckets` is exact and testable. Python and SQLite add `v` the same way (IEEE double), so `v` stays byte for byte.
  - The SELECT runs before the implicit write transaction. That is safe under the store's existing invariant (one writer per instrument-day: the collector writes the open day, the nightly rebuild writes closed ones).
- **Why there is no stored CVD prefix.** A per-bar running total would have to be rewritten for every later bar whenever the nightly rebuild recomputes a closed day. That means the rebuild writes today's live rows and breaks the one-writer-per-instrument-day invariant, and the prefix can drift.
  - `flow_delta_before` is one indexed SQLite aggregate. It reads no Python rows (MEM-01) and replays no raw seconds.
  - `Known limit:` it is O(stored bars of that width before the window): ≤ 43,200 rows at 1m. Upgrade path: a per-day delta table summed by day.
- **Liquidation exactly-once.** `venue_event_id` is the designated dedup key (D-150). The `liquidations_applied` table makes the live apply, catch-up overlap and rebuild idempotent. A watermark on `ts_event` or `ts_init` would drop or double-count around batch and day edges.
- **Golden fold** (one 60 s bucket, size_precision 3, price_precision 1):

  ```
  s0: close 1000 units, buy 2000, sell 0,    buy_n 1, sell_n 0
  s1: close 1001 units, buy 0,    sell 3000, buy_n 0, sell_n 2
  liq: LONG 500 units at sp 3
  -> buy_v 2000, sell_v 3000, buy_n 1, sell_n 2,
     pv = 1000*2000 + 1001*3000 = 5_003_000, liq_long_v 500, liq_short_v 0, liq_n 1,
     v = 5.0 (float, as today)
  ```

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests candles/tests views/tests data_api/tests capture/tests verification/tests tests -q -p no:cacheprovider`. Expected: green except the baseline-recorded pre-existing failures. The bots tests need a redis on 6379 and are untouched.
- `cd platform && ruff format --check <touched> && ruff check <touched> && mypy <touched python packages>`. Expected: clean.
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && npm test && npm run lint && npm run build`. Expected: success, and `git diff` shows only additive schema changes.

## Auto Run Result

Status: done

**Summary:**
- Follow-up review (fresh pass over `a038ae331f..969e75b9ca`), with Blind Hunter and Edge Case Hunter. The story itself is unchanged: every bar carries exact integer `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n` plus per-row precisions, from the one fold, stored, served, pushed live, verified, and CVD reads the bars with `anchor`.
- This pass fixed 4 low findings: two code hardenings and two corrections to documented guarantees.

**Files changed (this pass):**
- `platform/candles/infrastructure/sqlite_store.py`: rollback on a failed COMMIT in `_immediate`; `RebuildRaceError` refusal in `_replace_applied`; CandleStore docstring.
- `platform/candles/tests/test_order_flow.py`: two tests for the above.
- `platform/views/live_candles.py`: corrected bus-vs-store bound text; new Known limit for unreceived liquidation frames.
- `platform/docs/DATA_DICTIONARY.md` §2.15, `platform/docs/DATA_INTEGRITY_AUDIT.md` D-157 and D-160: the same corrections.

**Review findings:** 4 patches applied (all low), 0 deferred. 9 rejected, mostly already documented Known limits:
- feed outage reads 0 (D-156);
- a failed liquidation apply;
- the first liquidation is in a straddling bucket;
- CVD over pre-migration bars (D-161);
- the `v` identity ceiling (D-155);
- the store's feed start is preferred on pages (`liquidation_feed_start`);
- by design: a liquidation-only row with `seconds_observed = 0`;
- a day with liquidations but no snapshot file;
- the rebuild folding under its write lock (pre-existing lock pattern, bounded by the busy timeout).

**Verification:**
- `cd platform && python3 -m pytest kernel candles views capture verification tests -q` → 2913 passed, 5 skipped, 1 failed. The failure is the pre-existing `tests/test_legacy_names.py`. `data_api` is untouched this pass; its suite needs Redis on 6379.
- `uvx ruff format` + `uvx ruff check` are clean on the touched Python files.

**Residual risks:**
- The same late-flush race for *seconds* is pre-existing (before 33.3), not guarded, and practically unreachable with the nightly at 03:07 UTC.
- A `RebuildRaceError` fails that nightly step loudly; a rerun fixes it.
- The Known limits listed above stand as documented.
