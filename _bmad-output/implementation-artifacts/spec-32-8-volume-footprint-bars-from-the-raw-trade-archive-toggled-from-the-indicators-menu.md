---
title: 'Story 32.8: Volume footprint bars from the raw trade archive, toggled on from the Indicators menu'
type: 'feature'
created: '2026-09-30'
status: 'draft'
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

Filled at plan time from the live code (continuity from the 32.7 spec). Expected anchors: `platform/kernel/catalog_files.py` (`query_second_ohlc`, `_read_snapshot_columns`, `files_by_day`), `platform/kernel/second_snapshot.py` (unit conventions), `platform/views/chart_series.py` (`candle_page`, `MAX_QUERY_SPAN_SECONDS`, `with_gap_markers`), `platform/data_api/routes/candles.py` (route contract, clamps), `platform/docs/DATA_DICTIONARY.md` (trade tick section), `platform/frontend/src/hooks/useCandles.ts` (pagination and seam pattern), `components/chart/primitives/GapPrimitive.ts` and `VolumeProfilePrimitive.ts` (drawing patterns), `components/chart/IndicatorPicker.tsx` (Volume entry from 32.2), `hooks/useChartLayout.ts` (32.6), `lib/units.ts`, `pages/docs/kbData.ts`, tests: `kernel/tests`, `views/tests/test_chart_series.py`, `data_api/tests`, `FootprintPrimitive.test.ts`, `ChartPage.test.tsx`.

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map, ordered: kernel reader + tests; `footprint_page` + tests (bucketing, auto rows, no-trades, clamp, sum check); route + tests; TS types; `useFootprint` hook; `FootprintPrimitive` + tests; toggle, legend row, settings; layout persistence; docs.

**Acceptance Criteria:**
- Given Footprint on and closed bars in view, when the chart renders, then each bar shows its rows' sell × buy from the archive's trades, its delta, total, POC and imbalances, and the rows sum to the candle's volume.
- Given the forming bar or a bar with no archived trades, when the chart renders, then no footprint is fabricated and the empty bar is drawn as a gap.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest kernel/tests views/tests data_api/tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.
