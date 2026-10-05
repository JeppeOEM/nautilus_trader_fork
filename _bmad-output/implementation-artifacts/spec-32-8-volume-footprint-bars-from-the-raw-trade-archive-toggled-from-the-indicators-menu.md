---
title: 'Story 32.8: Volume footprint bars from the raw trade archive, toggled on from the Indicators menu'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '693a55f3de06225cc5ac88eb079e0f3db53f87b8'
final_revision: '4b8678ac16'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Every profile spreads a candle's volume over its range, an approximation. The raw trade archive (Story 22.13: `TradeTick` with price, size, aggressor side, `ts_event`) holds where inside a bar each trade happened, but nothing on the chart shows it.

**Approach:** A column-projected integer trade reader in `kernel/catalog_files.py`, a `views.chart_series.footprint_page` read model that buckets closed bars into integer price rows over the chart's own `candle_page` window, `GET /api/coin/{instrument_id}/footprint`, and a `FootprintPrimitive` on the price pane drawing sell × buy cells, delta and total, imbalances and POC per bar. Toggled from the Indicators dialog next to Volume; historical bars only.

## Boundaries & Constraints

**Always:**
- **Reader.** `kernel/catalog_files.py` `query_trade_columns(catalog_path, instrument_id, start_ns, end_ns)` reads only `price`, `size`, `aggressor_side`, `ts_event` as stored integer raws (never through `float`), over the same file-range discipline as `query_second_ohlc`, bounded by the caller's window (MEM-01). Consolidated day files and live minute files alike.
- **Read model.** `footprint_page(instrument_id, before_ns, limit, bar_seconds, row_ticks, ...)` uses the same `candle_page` window as the chart so bar boundaries agree; per bar, rows keyed by an integer bucket of `row_ticks` price units (auto: the smallest `row_ticks` giving ≤ 24 rows for the bar's high..low; else the operator's fixed value), each row `{p: bucket_low_units, b: buy_units, s: sell_units}` as integers, plus per bar `delta`, `total` (integers), `poc_row`, and the instrument's `price_precision`/`size_precision` on the response; a bar the archive holds no trades for carries `rows: []` and `no_trades: true` (a gap, never zeros as data, AD-F6); `limit` clamped to `MAX_FOOTPRINT_BARS = 200`, span to `MAX_QUERY_SPAN_SECONDS`.
- **Route.** `GET /api/coin/{instrument_id}/footprint` with the candles contract (`before_ns`, `limit`, `bar_seconds`) plus `row_ticks` (`auto` or an int ≥ 1); `views` computes, the route formats (SSOT-02).
- **Proof.** A test on a fixture day shows the sum of a bar's rows equals that bar's candle volume from the same window (Story 31.8's fold), and that a buy and a sell at one price land in the same row on their respective sides.
- **Historical only.** Closed bars from the archive; the forming bar shows no footprint; a `Known limit:` comment names the upgrade path (a fold of the `trades` Redis stream in the candles context). Never a live footprint fabricated from the 1 s snapshot's buy/sell totals.
- **Rendering** (Candles mode only, like FRVP): `FootprintPrimitive` on the price pane; per closed bar a column of cells aligned to the rows; display modes `bid×ask` (default, "sell × buy" text), `delta` (buy − sell), `volume` (total); cells shaded on a heat scale of the bar's largest row; POC row outlined; diagonal imbalances (buy at row n vs sell at row n−1, ratio ≥ `IMBALANCE_RATIO = 3` by default) highlighted in `--chart-up`, the mirror case in `--chart-down`; a footer per bar with delta and total; below a named bar-spacing threshold the text hides and cells stay as heat; candles keep drawing under the cells at reduced opacity; a `no_trades` bar draws `--chart-gap` (32.1).
- **Numbers.** Every printed value from the integer units through `lib/units.ts` (test with precision-2 and precision-6 instruments).
- **Fetching.** One `useFootprint(iid, chart, barSeconds, enabled)` hook with the candles' cursor pagination and seam rule (refills on scroll-back, refetches on timeframe change); it issues no request while off.
- **Toggle and settings.** "Footprint" pinned next to Volume in the Indicators dialog (32.2); on/off and settings (row size auto/fixed ticks, display mode, imbalance ratio, text on/off, colours) persisted in the layout (32.6); the legend row's gear opens 32.3's dialog; Apply refetches only when the row size changed.
- **Docs.** `docs/DATA_DICTIONARY.md` gains the footprint read model's definition; DocsPage chart section documents the modes and the historical-only limit.
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- The trade archive for a collected venue lacks `aggressor_side` on the wire (check `capture/venues/*` and the DATA_DICTIONARY trade section); block rather than infer side from price movement.

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Round-trip any price or size through `float` before the frontend formats it.
- Show a footprint for the forming bar.
- Read trades without a bounded window.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One bar, two rows | trades: buy 3 @ 100.0, sell 1 @ 100.0, buy 2 @ 100.5 (ticks 0.1, row_ticks 5) | rows `[{p:1000,b:3,s:1},{p:1005,b:2,s:0}]`, delta 4, total 6, poc row 1000 | none |
| Auto row size | bar high−low = 480 ticks | row_ticks 20 (24 rows) | none |
| Fixed row size | row_ticks 1 on a 480-tick bar | 480 rows (allowed; primitive hides text) | none |
| No trades in bar | archive empty for that minute | `rows: []`, `no_trades: true`; drawn in `--chart-gap` | none |
| Sum check | fixture day | Σ rows per bar == candle volume | test fails naming the bar |
| Imbalance | buy 9 at row n, sell 2 at row n−1 | row n buy cell highlighted up | none |
| Limit clamp | `limit=1000` | 200 bars | silent clamp, like candles |
| Bad row_ticks | `row_ticks=0` | 422 | 422 |
| Forming bar | live minute | no cells drawn | none |
| Precision-6 | prices as integer units | text at 6 decimals, no noise | none |
| Toggle off | dialog | no request issued, primitive detached, layout saved | none |
| Zoomed out | bar spacing 4 px | heat only, no text | none |

</intent-contract>

## Code Map

Live anchors (verified at plan time on branch `epic-32`, after 32.7):
- `platform/kernel/catalog_files.py` -- read-only file helpers. `snapshot_files`/`query_second_ohlc` (file pick by `CatalogFileSpan.overlaps(start, end, margin)`, rows by exact `ts_event`), `_index_rows` (the raw-decode precedent: schema-metadata `price_precision`, `FIXED_PRECISION_BYTES` = 16, exact or raise). Nothing reads `TradeTick` here yet. Trades live in `data/trade_tick/<iid>/` (`class_to_filename(TradeTick)`), columns `price`/`size` `binary(16)` (i128 LE raw at 10^-16), `aggressor_side` uint8 (0 NO_AGGRESSOR, 1 BUYER, 2 SELLER), `trade_id` string, `ts_event`/`ts_init` uint64; per-file precision in schema metadata. `kernel.clocks.MAX_TS_INIT_SKEW_NS` (300 s) is the trade readers' file-span margin (`archive/application/rebuild_day.py` `_hour_trades` uses it).
- `platform/kernel/fold.py` `fold_trades` -- the trade fold behind candle volume: every non-BUYER aggressor counts as a sell. `rebuild_day._hour_trades` dedups by `trade_id` (a restart replay); dedup is exact inside any `ts_event` partition because both copies share `ts_event`.
- `platform/views/chart_series.py` -- `candle_page(iid, before_ns, limit, bar_seconds, *, catalog_path, candles_dir, recent_rows)` (store first, Parquet fold for the rest; oldest-first, no gap rows; bars `t` in ms, `t < before_ms`), `MAX_QUERY_SPAN_SECONDS` (7 d), `ImpossibleCandle`. NAME CLASH: `FootprintCell`/`build_footprint` (l.377-461) already exist -- an order-book-delta footprint, unrelated; leave them untouched and keep the new names distinct (`footprint_page`, `FootprintBar`).
- `platform/views/catalog_reads.py` `instrument_precision` / `NoInstrumentDefinition` (definition precision, 404 in routes).
- `platform/data_api/routes/candles.py` -- the contract to mirror (clamps `_MAX_CANDLES_LIMIT`, `_MAX_BAR_SECONDS`, 400 on malformed id via `venue_of`, 404 no definition, 500 `ImpossibleCandle`); `data_api/app.py` includes routers (l.55 import, l.214 include); `data_api/tests/test_candles.py` (`_define`, `_client` monkeypatching `CATALOG_PATH`/`CANDLES_DB_DIR`).
- `platform/views/preferences.py` -- `_LAYOUT_KEYS` (all required today), `_PROFILE_OPTIONAL_DEFAULTS` (the optional-key precedent), `validate_layout`, `BUILTIN_DEFAULT_LAYOUT`, `LayoutError`, `_layout_table`/`_read_layout_table`; mirror tests `platform/views/tests/test_chart_layouts.py` (`_ts_int`, `test_profile_kinds_mirror_the_frontend`).
- Fixtures: `kernel/tests/snapshot_factory.py` `make_snapshot(..., trades=fold_trades(...).snapshot_units(p, s))` (importable from views/data_api tests), `kernel/tests/test_fold.py` `_trade` pattern; trades written with `ParquetDataCatalog.write_data`.
- Frontend (`platform/frontend/src/`): `hooks/useCandles.ts` (cursor `loadPage`, `subscribeVisibleLogicalRangeChange` refill at `REFILL_MARGIN_BARS`, a timeframe change remounts `ChartInner` via its `key`), `api/client.ts` (`fetchCandles`, `HttpError`), `api/schema.ts` (codegen from the backend OpenAPI, `npm run codegen`), `hooks/useLiveCandle.ts`, `components/chart/LightweightChart.tsx` (owns the series and attaches every primitive; candle series created l.941-960; `volumeProfiles` effect l.1308; `legendExtras` rows hard-wired `actionable:false` l.1169-1180), `primitives/GapPrimitive.ts` + `VolumeProfilePrimitive.ts` (primitive pattern, `useBitmapCoordinateSpace`, `timeScale().options().barSpacing`), `components/chart/chartTheme.ts` (`CHART_TOKENS`, `chartVar`; `chartTheme.test.ts` forbids hex/`rgb()`/app tokens in chart code incl. every `primitives/*.ts`), `components/chart/IndicatorPicker.tsx` (pinned Volume row l.460-472, props `volumeOn`/`onVolumeChange`), `legend.ts` (`LegendAction`, `configurable`), `SettingsDialogShell.tsx`, `pages/ChartPage.tsx` (`handleLegendAction` volume special case l.655-669, `mode`, `useCandles` call l.429, `ChartForCoin`/`patchLayout`), `lib/chartLayout.ts` (`ChartLayout`, `normalizeLayout` optional-key pattern l.103-113), `lib/units.ts` (`formatUnits`, `unitsToNumber`), `pages/docs/kbData.ts` (`chart-profiles` Known limits l.173 names 32.8), `src/test/drawingKit.ts`, `ChartPage.test.tsx` (mocks `../api/client` and hooks -- new exports must be added to the mocks).

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/catalog_files.py` + `kernel/tests/test_catalog_files.py` -- add `TRADE_DIRNAME`, `trade_files`-style listing, `TradeColumns` (numpy `price` int64 units, `size` int64 units, `buyer` bool, `ts_event` int64) and `query_trade_columns(catalog_path, instrument_id, start_ns, end_ns, price_precision, size_precision)`: half-open `[start, end)` by `ts_event`, files picked by span overlap widened by `MAX_TS_INIT_SKEW_NS`, columns `price, size, aggressor_side, ts_event, trade_id` only, raw -> units exactly by the decimal128 reinterpretation (Design Notes; an inexact raw raises a named `TradeDecodeError`, never rounds), a file whose schema lacks `price_precision`/`size_precision` raises, duplicates (`ts_event`, `trade_id`) dropped once, sorted by `ts_event`. Tests: units at precision 2 and 6, inexact raw refused, half-open window, a file starting after `end` but within the skew margin is read, a replayed duplicate counted once, consolidated + minute files together.
- [x] `platform/views/chart_series.py` + `views/tests/test_footprint_page.py` -- `MAX_FOOTPRINT_BARS = 200`, `FOOTPRINT_MAX_ROWS_AUTO = 24`, `FOOTPRINT_SETTLE_SECONDS`, `footprint_page(instrument_id, before_ns, limit, bar_seconds, row_ticks, *, catalog_path, candles_dir, recent_rows, price_precision, size_precision, now_ns)` per Design Notes. Tests: every I/O-matrix row that is server-side (two rows, auto 24, fixed 1 → 480 rows, no trades, clamp 200, NO_AGGRESSOR counted as sell), forming/unsettled bar absent, 7-day span trim sets `has_more`, and the sum check: a fixture day whose snapshots carry `fold_trades(...)` units -- each bar's `total` equals the same window's `candle_page` volume in size units, and a buy and a sell at one price share one row; failure message names the bar `t`.
- [x] `platform/data_api/routes/footprint.py` (+ register in `data_api/app.py`) + `data_api/tests/test_footprint.py` -- `GET /api/coin/{instrument_id}/footprint?before_ns&limit=120&bar_seconds=60&row_ticks=auto`; `limit` clamped to `MAX_FOOTPRINT_BARS`, `bar_seconds` clamped like candles; `row_ticks` not `auto` and not an int >= 1 → 422 naming it; 400 malformed id, 404 no definition, 500 on `ImpossibleCandle`/`TradeDecodeError`/`FootprintOverflow` (each ledgered by views); response `{items, has_more, price_precision, size_precision}`, items `{t, row_ticks, rows:[{p,b,s}], delta, total, poc_row, no_trades}`. Tests: happy path, 422, clamp, 404.
- [x] `platform/frontend/openapi.json`/`src/api/schema.ts` (regenerate with the repo's codegen script) + `src/api/client.ts` -- `fetchFootprint(iid, beforeNs, limit, barSeconds, rowTicks)`.
- [x] `src/lib/footprint.ts` + test -- pure helpers: merge pages by `t`, per-bar max row for the heat scale, diagonal imbalances (`IMBALANCE_RATIO = 3` default; buy at row n vs sell at row n−1 (one `row_ticks` below), and the mirror sell at row n vs buy at row n+1; a zero opposite side counts as imbalance only when the side itself is non-zero), cell text per mode through `lib/units.ts` (`bid_ask` "sell × buy", `delta`, `volume`) -- tests with precision-2 and precision-6 instruments.
- [x] `src/hooks/useFootprint.ts` + test -- `useFootprint(iid, chart, barSeconds, enabled, rowTicks)`: no request while off; first page from now; scroll-back refill on the same visible-range rule as `useCandles` from its own earliest `t`; refetch newest page every `FOOTPRINT_REFRESH_MS` while on (a closed bar settles server-side); a `rowTicks` change drops the pages and refetches; retry/backoff pattern of `useCandles`.
- [x] `src/components/chart/primitives/FootprintPrimitive.ts` + test -- one primitive on the price series, `zOrder "top"`, rendering per Design Notes; `chartTheme.ts`/`theme.css` gain only tokens it needs (both sides, contrast test passing). Tests: cell rects per row, POC outline, imbalance colours, `no_trades` bar painted `--chart-gap`, text hidden below `FOOTPRINT_TEXT_MIN_BAR_SPACING`, a bar with no item draws nothing.
- [x] `src/components/chart/LightweightChart.tsx` -- a `footprint` prop (data + settings + precision or null); attach/detach the primitive on the candle series in Candles mode only, re-attach on mode change; a price-pane legend row "Footprint" with gear and × (`legendExtras` row made actionable, or a dedicated row), routed through `onLegendAction`.
- [x] `src/components/chart/IndicatorPicker.tsx` -- a second pinned row "Footprint" next to Volume (`footprintOn`/`onFootprintChange`).
- [x] `src/components/chart/FootprintSettingsDialog.tsx` -- on `SettingsDialogShell`: row size (Auto / fixed ticks ≥ 1), display mode, imbalance ratio (≥ 1), text on/off, buy/sell colours; Apply/Cancel/Remove; Apply refetches only when row size changed.
- [x] `src/lib/chartLayout.ts` + `platform/views/preferences.py` + tests (`lib/chartLayout.test.ts`, `views/tests/test_chart_layouts.py`) -- optional layout table `footprint` `{on, row_ticks (0 = auto), mode, imbalance_ratio, text, buy_color?, sell_color?}` absent → defaults (old files load unchanged), present but bad → frontend per-field fallback with one `console.error`, server 422 naming the key, unknown key refused; mirror test for the mode set and `IMBALANCE_RATIO` default.
- [x] `src/pages/ChartPage.tsx` + `ChartPage.test.tsx` -- wire layout ↔ picker ↔ `useFootprint(enabled = on && mode === "candles")` ↔ `LightweightChart`; legend gear opens the dialog, × turns it off; tests: toggle on fetches, off issues no request and detaches (prop null), setting persisted in the saved layout, style-only Apply does not refetch.
- [x] `platform/docs/DATA_DICTIONARY.md` (footprint read model section), `src/pages/docs/kbData.ts` (new `chart-footprint` entry: modes, imbalances, historical-only and retention limits; update `chart-profiles`' "Story 32.8" note and `chart-layout`'s saved fields), `platform/CLAUDE.md` SSOT-06 layout field list gains `footprint`.

**Acceptance Criteria:**
- Given Footprint on and closed bars in view, when the chart renders, then each bar shows its rows' sell × buy from the archive's trades, its delta, total, POC and imbalances, and the rows sum to the candle's volume.
- Given the forming bar or a bar with no archived trades, when the chart renders, then no footprint is fabricated and the empty bar is drawn as a gap.
- Given an old `chart_layouts.toml` without `footprint`, when loaded, then it loads unchanged with Footprint off; given an unknown footprint key, the server refuses it with a 422 naming it.
- Given the verification commands, when they run, then all pass with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 1, medium 3, low 8)
- defer: 0
- reject: 8: (medium 1, low 7)
- addressed_findings:
  - `[high]` `[patch]` Settle window (150 s) was shorter than the reconnect trade backfill's `MAX_TS_INIT_SKEW_NS` (300 s), so a settled bar could still gain trades: `FOOTPRINT_SETTLE_SECONDS` is now derived as skew + two flushes (420 s), with a test pinning settle > skew; docs, hook and primitive comments updated (supersedes the Design Notes' 150 s).
  - `[medium]` `[patch]` Two stored copies of one (`ts_event`, `trade_id`) that disagree on price/size/side were silently de-duplicated: now refused with `TradeDecodeError` (DATA-07), with a test.
  - `[medium]` `[patch]` A fixed row size had no row cap (1-tick rows on wide bars): `FOOTPRINT_MAX_ROWS_FIXED = 1000`, past it the bar is bucketed at the smallest fitting multiple and states its own `row_ticks`; test + docs.
  - `[medium]` `[patch]` The 60 s refresh re-read the whole newest page (up to 7 days of trades): the server now ends the candle page at the settle cutoff so `limit` counts servable bars, and the refresh asks for 10 bars (the hole fill catches up longer absences).
  - `[low]` `[patch]` `row_ticks` route parse: >4300-digit strings raised an unhandled `ValueError`, and no upper bound matched the layout's `MAX_FOOTPRINT_ROW_TICKS`: length + range checked, 422 naming it; tests.
  - `[low]` `[patch]` int64-sum pre-check (`max * len`) could refuse valid slices: replaced by a float64-sum bound at 2^62.
  - `[low]` `[patch]` Peak-memory Known limit understated the real peak: DATA_DICTIONARY wording corrected.
  - `[low]` `[patch]` kbData claimed auto rows line up across bars: corrected (auto is per bar; fixed sizes line up).
  - `[low]` `[patch]` Footer drawn at ±Infinity when no row is on the price scale: `drawBar` returns early; test.
  - `[low]` `[patch]` Bars just off a visible edge popped in/out: one neighbour laid out on each side; test.
  - `[low]` `[patch]` `useFootprint` `.finally` could chain a load after unmount: unmount guard added.
  - `[low]` `[patch]` A scroll-back dropped by the busy guard during a refresh was never re-checked: the range is re-checked after every successful page.
  - Also patched before review (implementation verification): after a long absence the newest refresh left a hole that nothing filled; `useFootprint` now fills back page by page (tests).

### 2026-10-05 — Review pass (follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 4, low 5)
- defer: 1: (medium 1)
- reject: 16: (medium 4, low 12)
- addressed_findings:
  - `[medium]` `[patch]` `useFootprint`: a deterministic error on an older page left `hasMore` true, so every pan near the left edge re-asked the failing page (one trade decode + one ledger entry per frame). An older page's deterministic failure now stops scroll-back, a fill's ends the fill, and an initial page's is asked for again on the next enable; test.
  - `[medium]` `[patch]` `useFootprint`: a hole-fill chain kept requesting pages after the footprint was switched off (spec: "no request while off"). The chain now stops while off and resumes on the next enable; test.
  - `[medium]` `[patch]` `useFootprint`: the 60 s refresh ignored the bar size, so a 1D/1W chart re-read up to 7 days of trades every minute for a bar that cannot exist yet. The refresh now asks only once the bar after the newest held one can have closed (`refreshDue`); test, kbData and DATA_DICTIONARY updated.
  - `[medium]` `[patch]` `query_trade_columns`: a minute file removed by the nightly consolidation between listing and read raised an unledgered `FileNotFoundError` (a bare 500). It now lists again once, and a second loss raises the ledgered `TradeDecodeError`; two tests, docs.
  - `[low]` `[patch]` `footprint_page`: the bar holding the page end was fetched and dropped, so a page held `limit - 1` bars and `limit=1` returned `[]` with `has_more=true`. It now asks `candle_page` for `limit + 1` and keeps the newest `limit`; two tests.
  - `[low]` `[patch]` `useFootprint`: bars held across a change of the definition's precisions were drawn in the new precisions (wrong prices and sizes). A response in other precisions now replaces the held pages; test.
  - `[low]` `[patch]` `_raw_units`: the width guard followed `FIXED_PRECISION_BYTES`, so on a 64-bit-precision build 8-byte raws would have been reinterpreted as `decimal128`. It now requires 16-byte raws; test.
  - `[low]` `[patch]` Undocumented ceiling: after a venue coarsens a tick or lot, trades archived before the change are refused until they leave the retention. Recorded as a `Known limit:` with its upgrade path in `query_trade_columns` and DATA_DICTIONARY §2.14.
  - `[low]` `[patch]` `useFootprint`: the unmount flag was never reset on React StrictMode's dev re-mount, which keeps refs, so the dev build would drop every response. The flag is now reset in the effect body. Not reproduced in vitest, whose StrictMode render does not double-invoke effects, so no regression test was added.

### 2026-10-05 — Review pass (second follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 1, low 7)
- defer: 1: (low 1)
- reject: 11: (medium 3, low 8)
- addressed_findings:
  - `[medium]` `[patch]` `query_trade_columns`: a truncated or corrupt trade file raised a bare `ArrowInvalid`/`OSError`. The route did not map it, so it reached the client as an unledgered 500 (DATA-07). `_trade_part` now raises `TradeDecodeError` naming the file, which `views` ledgers and the route maps to a 500. A `FileNotFoundError` still propagates to the re-listing. Test added.
  - `[low]` `[patch]` `_require_agreeing_copies` compared the collapsed `buyer` bool, so a SELLER copy and a NO_AGGRESSOR copy of one trade passed as agreeing, against the documented "copies that disagree on side are refused". Sides are now compared as stored, and `buyer` is derived after the dedup. Test added.
  - `[low]` `[patch]` The DATA_DICTIONARY §2.14 Parity paragraph claimed `total == v` on every rebuilt day. Bars holding orphan trades, and bars inside an `ArchiveGap` span, are rebuild exceptions where the two differ. The paragraph now names both.
  - `[low]` `[patch]` `useFootprint`: an older-page retry still pending after a pan loaded that page re-fetched held bars and moved `earliestMs` back to a newer bar. Older and fill retries are now dropped when their cursor moved on (`superseded`). Test added.
  - `[low]` `[patch]` `useFootprint`: a second hole that opened while the first one's fill waited on a retry was never filled. `trackHole` now restarts the fill from the new hole and runs it on to the first hole's older edge. Test added.
  - `[low]` `[patch]` `useFootprint`: a newest page starting at the bar right after the newest held one counted as a hole and cost one redundant full fill page. A hole now needs a gap of more than one bar. Test added.
  - `[low]` `[patch]` Route test gap: the `ImpossibleCandle` and `FootprintOverflow` → 500 mappings were untested. Parametrized test added.
  - `[low]` `[patch]` Test gap: no page spanning UTC midnight exercised the `_day_slices` + `_fold_slice` accumulation. Hourly-bar test added.

## Design Notes

- **Exact decode, vectorised.** A `binary(16)` column shares its byte layout with `decimal128`: reinterpret its buffers as `decimal128(38, 16)` (`pa.Array.from_buffers(type, len, arr.buffers(), offset=arr.offset)`), `pc.cast` to `decimal128(38, p)` (Arrow refuses a lossy rescale: "Rescaling Decimal value would cause data loss" → `TradeDecodeError` naming file and column), reinterpret as `decimal128(38, 0)`, `pc.cast` to `int64` (overflow refused). Units are taken at the instrument definition's precision, not the file label, so mixed-label history aggregates on one grid. Bench on a real 808k-trade Bybit file: read + decode + dedup ≈ 0.25 s.
- **Memory (MEM-01).** `footprint_page` calls `query_trade_columns` once per UTC day slice of its window and folds each slice into per-bar accumulators before the next; dedup per slice is exact (copies share `ts_event`). `Known limit:` peak memory is one instrument-day of trades (~65 MB per 800k Bybit BTC trades); upgrade path: row-group streaming, or a stored per-bar footprint folded by the candles context.
- **Bars.** `footprint_page` takes its bar list from `candle_page` (same window → same boundaries, no second bucket rule). A bar is served only when closed and settled: `t + bar_seconds ≤ min(before_ns, now_ns − FOOTPRINT_SETTLE_SECONDS)` (settle = 420 s after review: the 300 s backfill skew plus two collector flushes; `Known limit:` tied to the 60 s default flush) -- the forming bar never appears. Bars are trimmed to the newest `MAX_QUERY_SPAN_SECONDS` of span (`has_more` then true). Trades are assigned to bars by `searchsorted` on bar starts and must fall in `[t, t + bar)`; a trade in a bar `candle_page` does not serve (a gap) is not drawn. `Known limit:` (historical only) with the upgrade path "fold the `trades` Redis stream in the candles context".
- **Rows.** `bucket = units // row_ticks * row_ticks` (a price grid aligned to multiples, stable across bars at a fixed size). Auto: the smallest `row_ticks ≥ 1` with `high//rt − low//rt + 1 ≤ 24` on the bar's trade high/low (480 tick levels 1000..1479 → 20). Only rows with trades are emitted, ascending `p`. POC = largest `b + s`, ties to the lower `p`. NO_AGGRESSOR counts as a sell (the fold's rule) so totals equal candle volume. Values are Python ints; any `|value| > 2^53 − 1` raises `FootprintOverflow` (ledgered; JSON numbers would lose it) -- `Known limit:`, upgrade path: serialise as strings.
- **Parity scope.** `total` equals candle `v` on days whose snapshot trade columns are the trade fold (rebuilt days, 31.8); live seconds can differ by late/orphan trades -- documented in the DATA_DICTIONARY section, not hidden.
- **Rendering.** Per bar column width ≈ `barSpacing`, row y from `priceToCoordinate` of the bucket edges (`unitsToNumber` for coordinates only; printed text always `formatUnits`). A translucent `--chart-bg` veil (canvas `globalAlpha`, no colour literal) under the cells dims the candle beneath. Heat = cell alpha ∝ row total / bar max. Text only at `barSpacing ≥ FOOTPRINT_TEXT_MIN_BAR_SPACING` and when rows are tall enough for the font. Footer below the bar's low: delta (up/down colour) and total.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest kernel/tests views/tests data_api/tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.
- `cd platform && ruff check kernel views data_api && ruff format --check kernel views data_api` -- expected: clean.

## Auto Run Result

**Summary.** This was a second follow-up review of Story 32.8's volume footprint, covering baseline `693a55f3de` to `2d1c2f189f`. The Blind Hunter and Edge Case Hunter reported 20 findings, 19 after deduplication. Triage kept 8 as patches, deferred 1 pre-existing issue and rejected 11. Every patch hardens shipped behaviour or adds missing tests; none changes the intent contract or the response shape.

**Files changed (this pass).**
- `platform/kernel/catalog_files.py`: an unreadable trade file is refused as `TradeDecodeError` naming it, so it is ledgered. Duplicate copies are compared on the stored `aggressor_side`.
- `platform/kernel/tests/test_catalog_files.py`: two tests, SELLER vs NO_AGGRESSOR copies and a truncated file.
- `platform/views/tests/test_footprint_page.py`: a page across UTC midnight.
- `platform/data_api/tests/test_footprint.py`: the `ImpossibleCandle`/`FootprintOverflow` → 500 mappings.
- `platform/frontend/src/hooks/useFootprint.ts`:
  - a superseded retry is dropped;
  - a second hole restarts the fill;
  - a page adjacent to the held bars leaves no hole.
- `platform/frontend/src/hooks/useFootprint.test.ts`: three tests for those cases.
- `platform/docs/DATA_DICTIONARY.md` §2.14: the Parity paragraph names its rebuild exceptions (orphans and `ArchiveGap` spans).
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new entry, the same unreadable-file gap in the pre-existing readers.

**Review.**
- 8 patches applied: 1 medium, 7 low.
- 1 deferred (low): `query_second_ohlc` and the other pre-existing catalog readers do not map an unreadable Parquet file to a ledgered error.
- 11 rejected:
  - Already rejected in earlier passes, with nothing new raised:
    - the 1W/1D page shrinking under the 7-day span cap and the scroll-back chain it causes (spec: "span to `MAX_QUERY_SPAN_SECONDS`");
    - a bar past retention drawn as `no_trades`;
    - the (`ts_event`, `trade_id`) dedup against the rebuild's `trade_id` dedup;
    - an empty neighbour row counting as beaten for imbalances (spec-defined);
    - colour fields accepting any string (project convention, raised by both reviewers);
    - the duplicated `_MAX_BAR_SECONDS`;
    - re-listing the trade directory once per day slice.
  - Imbalance outlines in `--chart-up`/`--chart-down` rather than the user's buy/sell colours: the spec names those tokens.
  - Footprint not consulting `ArchiveGap` markers: outside the intent contract. The parity docs now name the gap case.
  - A consolidation that removes files twice during one read: improbable, and it is already refused loudly.
  - Client-held bars never evicted: the same unbounded-hold convention `useCandles` follows.

**Follow-up review:** not recommended. The patches are localized and low-severity, and each one carries a regression test that fails before the patch. The `useFootprint` changes are two narrow guards in a state machine that has now been reviewed three times.

**Verification.**
- Each new test was confirmed to fail against the pre-patch code: the two kernel tests and the three hook tests failed against the HEAD versions of `catalog_files.py`/`useFootprint.ts`, and all pass now.
- `cd platform && python3 -m pytest kernel/tests views/tests data_api/tests -q`: 1057 passed.
- `cd platform/frontend && npx vitest run`: 46 files, 876 tests passed.
- `npm run lint`: the same 3 pre-existing warnings (`useCandles.ts:200`, `TrustedHtml.tsx` ×2).
- `npm run build`: ok.
- `ruff check` / `ruff format --check` on the changed Python files: clean. The one format fix was applied.
- `mypy kernel/catalog_files.py`: only the pre-existing `catalog_files.py:160` hit (`query_second_ohlc`).

**Residual risks.** Nothing here was run against the live VPS catalog or in a real browser. The second-hole fill re-reads the bars held between the two holes. That is bounded by the fill's progress, and the pages are merged, never doubled.

