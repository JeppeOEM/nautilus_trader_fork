---
title: 'Story 33.5: Chart panes for open interest, funding, basis and liquidations, and the mark/index overlay'
type: 'feature'
created: '2026-10-06'
status: 'done'
baseline_revision: 'b9c71f79ffb78a309da22cd5fa2b067d32ce45fe'
final_revision: '17af1f656d2b54c24d21bdd98449c9fbf9eb618b'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Story 33.4 serves funding, open interest, mark, index, basis and liquidations over five routes and two live channels. Nothing on the chart reads them, so a flush, a squeeze or a funding extreme cannot be seen next to the price move it drove.

**Approach:** The Indicators dialog gains a pinned **Derivatives** group with five entries: Open Interest, Funding, Basis, Mark / Index and Liquidations.
- Each entry is drawn through the existing pane mechanism and fed by a paginated hook over the 33.4 routes. The hook is keyed on the candles' cursor and follows the 33.4 live channel for the forming bar.
- Each entry's on/off state, style and pane height persist in the per-coin layout.
- Liquidations also draw series markers on the price pane at their bankruptcy price, and a live Liquidation tape panel lists the latest 50.
- The History page gains OI, funding and liquidation tiles.
- Three small backend additions keep every derived number on the server (SSOT-01/02):
  - per-row and per-side liquidation notionals on the routes;
  - the same notionals on live liquidation frames;
  - `annualised` and `basis_mi_bps` on live derivs frames.

## Boundaries & Constraints

**Always:**
- **No formula in the browser (SSOT-01/02).**
  - Basis, annualised funding, `oi_change` and notionals come only from the server: route fields, or live-frame fields computed in `views/live_derivs.py` with `kernel.indicators` and `Liquidation.notional_units()`.
  - The browser only maps values onto bars, converts to `number` for plotting, and formats.
  - A value the server did not send is null and draws as whitespace. Its legend reads `—`.
- **Formatting goes through `lib/units.ts`.**
  - Exact decimal text (`rate`, `oi`, `oi_change`, `mark`, `index`) is printed by a new exact text helper, never through `Number`.
  - Integer units are printed by `formatUnits`, and floats (bps, annualised) by `formatDecimal`.
  - `Number(text)` and `unitsToNumber` are allowed only to give a plot its y value.
- **Spot fetches nothing.**
  - The candles response's `market` decides spot. Until `useCandles` reports `venueMarket`, no derivatives hook fetches or subscribes.
  - On `market === "spot"`, the Derivatives group is shown disabled with the tag `spot: no derivatives`, its saved on-state is kept but not drawn, and nothing is fetched or subscribed.
- **Gaps are never filled.**
  - A null bucket and a route gap row are whitespace.
  - Between two pages the hook inserts `lib/gaps.ts` `gapRun` slots, as `useCandles` does.
  - Funding is an event series that is change-deduped upstream (audit D-103/D-108). It is mapped onto bars by holding the last event, which decodes "unchanged", not fills. The hold never crosses a candle gap slot, never starts before the first loaded event, and never runs past the newest known event's bar plus the live forming bar.
- **Bar Replay.** While replay is active, every pane's data, the markers and the tape are cut at `cutoffTime` with `trimAfter` (an event is kept when `ts_event` ≤ the cutoff bar's end). Live updates are withheld, as the live candle is.
- **Live forming bar.**
  - A `derivs:{iid}` tick whose `t` falls in the current `liveBar` slot sets that slot's raw value (`oi`, `mark`, `index`, funding `rate`/`annualised`/`next_funding_ns`) and its server-computed `basis_mi_bps`.
  - A tick older than that slot is ignored, because the route is the source for closed bars.
  - `oi_change` and `basis_ml_bps` of the forming slot stay null until the route serves the bar (`Known limit:` in code).
  - When a candle closes (`onBarClosed`) or the socket reconnects, each hook re-reads its newest page and merges by `t`. The route wins for closed slots.
- **Bounded memory (MEM-01).**
  - Bucket hooks page back only while their earliest `t` is later than the earliest loaded candle.
  - Marker liquidations page back to the earliest loaded candle, capped at `MARKER_MAX_ROWS` (named, 5000). When capped, the legend row says so.
  - The tape holds exactly 50 rows.
- **Persistence.**
  - The layout gains an optional `derivatives` table (absent means everything off), validated by the client normaliser and by `views.preferences.validate_layout`, which refuses an unknown key (422).
  - Pane heights are persisted under fixed group ids `deriv_oi`, `deriv_funding`, `deriv_basis` and `deriv_liquidations`.
- **Added fields only (AD-D12).** Routes and live frames only gain keys. `openapi.json` and `schema.ts` are regenerated in the same change.
- **Theme.** Colours come from chart tokens only (`chartTheme.test.ts` refuses literal colours). Up and down are `--chart-up`/`--chart-down`, and gaps are `--chart-gap`.
- **No new dependency.** Never touch `nautilus_trader/` or `crates/`.

**Block If:**
- Lightweight-charts 5.2.1's series-markers plugin cannot place a marker at a price (`atPrice*` positions) or report a hovered marker (`hoveredObjectId`). In that case HALT; do not hand-roll a marker renderer.

**Never:**
- No order-flow indicators (33.6), screener columns or presets (33.7), alert conditions (33.8), price-scale modes (33.9) or fullscreen (33.12).
- No invented `price_kind` column and no Hyperliquid, dYdX or spot liquidation rows. `price_kind` is the route's constant `"bankruptcy"`. `"mark"` is documented as a kind no feed produces today.
- No browser-side basis, annualisation, OI change or notional arithmetic.
- No interpolation between buckets.
- No second dialog system: settings reuse `IndicatorSettingsDialog`/`SettingsDialogShell`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| OI pane | OI page `[{t:600000,oi:"120",oi_change:null},{t:660000},{t:720000,oi:"90",oi_change:"-30"}]` | Line points 600→120, whitespace at 660, 720→90. At 720 the legend reads `90` and `Δ -30`; at 600 it reads `120` and `Δ —` | — |
| Funding hold | Events at 10:00:20 rate "0.0001", 10:03:10 rate "-0.0002", 1m candles 10:00..10:04, with 10:02 a gap slot | 10:00 and 10:01 → 0.0001 (up colour); 10:02 whitespace; 10:03 and 10:04 → -0.0002 (down colour) | — |
| Funding legend | Latest event `next_funding_ns` 3 h 0 m 5 s ahead of the client clock | Reads `rate 0.0100%`, `ann. 10.95%`, `next 03:00:05`, ticking down each second. At or past zero it reads `next 00:00:00` until the next event. With a null `next_funding_ns` it reads `next —` | — |
| Basis pane | `basis_mi_bps:50.0`, `basis_ml_bps:null` | Mark−index line at 50 and whitespace on the mark−last line, both drawn against a zero line | — |
| Liquidation bars, size mode | `long_v:1500, short_v:200, n:3, size_precision:3, notional_units:…` | Bars at -1.5 (down colour) and +0.2 (up colour). The legend shows the notional through `formatUnits` and `n 3` | — |
| Notional mode | Same bar with `long_notional_units`/`short_notional_units` | Mirrored bars of the per-side notionals. A null side draws whitespace | — |
| No liquidation feed | Hyperliquid perp | Bars are null, so the pane is all whitespace and draws no markers. The tape reads "no liquidation feed for this instrument" | — |
| Spot | `BTCUSDT-SPOT.BYBIT` | Group disabled with "spot: no derivatives". No fetch, no subscribe, no pane, no markers. The tape toggle is disabled | — |
| Replay | Cutoff bar 10:02 at 1m | Every pane ends at 10:02, markers and tape rows after 10:02:59.999 are hidden, and live ticks are ignored | — |
| Live tick | Forming slot 10:05, `derivs` oi tick at 10:05:30 "95" | The 10:05 OI point becomes 95 and its `oi_change` is `—`. A tick at 10:04:59 is ignored | — |
| Markers | 2 long liquidations in a bar, bar spacing above `MARKER_MIN_BAR_SPACING_PX` | 2 circles `atPriceBottom` at each bankruptcy price, radius `sqrtRadius(notional)`. The tooltip shows side, size, price, notional and `bankruptcy` | — |
| Merge | 7 short liquidations in a bar (> `MARKER_MERGE_COUNT` 3) | One `aboveBar` marker with text `Σ <notional> · 7`, and its tooltip shows the sum and count | — |
| Narrow bars | Bar spacing at or below the threshold | No markers; the legend notes `markers hidden: zoom in` | — |
| Route error | 5xx | Retried with the `useCandles` backoff. Any other status is final, and the legend row reads `load failed` | Logged once (`console.error`) |
| Bad layout | `derivatives.oi.on: "yes"` | The client falls back to the default for that key and records a fallback. The server returns 422 naming `derivatives.oi.on` | — |

</intent-contract>

## Code Map

- `platform/frontend/src/components/chart/LightweightChart.tsx`:
  - `PaneSeriesKind` (:65), `IndicatorPaneSpec` (:81-116), the panes effect (:1119-1288) and `paintedData` (:453).
  - The legend mapping (:1263-1276): it must pass `format` and `text` from the spec.
  - The live effect (:1309-1336).
  - Pane heights: `layoutPaneHeights` (:516), `currentPaneHeights` (:498) and `INDICATOR_PANE_PX`.
- `platform/frontend/src/components/chart/legend.ts`: `LegendSeries.format`/`text` (:32-56), `renderLegends` (:205) and `formatLegendValue`.
- `platform/frontend/src/pages/ChartPage.tsx`:
  - Pane-spec building (:564-611), `trimAfter` (:198), `useReplay` and the `liveBar` gating (:1437).
  - Legend-action routing (:639-659) and `handlePaneHeights` (:1135-1144).
  - The header clusters (:1268-1375), `.chart-workspace` (:1384-1446), and `changeVolumeOn`/`changeFootprintOn` (:1561).
- `platform/frontend/src/components/chart/IndicatorPicker.tsx`: the pinned toggles (:303-307, :416-504) and the `indicator-dialog-tag` (:500).
- `platform/frontend/src/components/chart/IndicatorSettingsDialog.tsx`: `SettingsOutput`/`SettingsPatch`; `lib/indicatorStyle.ts` `OutputStyle`/`outputStyle`; `ParamInputs.tsx` choices.
- `platform/frontend/src/lib/chartLayout.ts`: `ChartLayout` (:65), `BUILT_IN_LAYOUT` (:78), `footprintOf` (:213, the precedent for an optional table), `normalizeLayout` (:244) and `layoutForSave` (:278).
- `platform/views/preferences.py`: `_LAYOUT_KEYS` (:543), `_validate_footprint` (~:719), `validate_layout` (~:784) and the footprint mirror test.
- `platform/frontend/src/hooks/`:
  - `useCandles.ts`: the pagination, seam, retry and `venueMarket` templates.
  - `useFootprint.ts`: scroll-back keyed on the candles (`timeToIndex`, :201).
  - `useLiveDerivs.ts` and `useLiveLiquidations.ts`: they exist and nothing uses them yet.
- `platform/frontend/src/api/client.ts`:
  - `fetchFootprint` (:127-143) is the template. Pass the URL inline so `test_frontend_contract.py` checks it.
  - `api/schema.ts` already has `FundingItem`, `OpenInterestItem`, `MarkIndexItem`, `LiquidationItem` and `LiquidationBarItem`.
- `platform/frontend/src/lib/units.ts`: `formatUnits`, `formatDecimal` and `unitsToNumber`. There is no decimal-text helper yet.
- `platform/frontend/src/lib/gaps.ts`: `gapRun`.
- `platform/frontend/src/components/chart/chartTheme.ts`: `chartVar`, with tokens `--chart-up`, `--chart-down`, `--chart-gap` and `--chart-pane-*`.
- `platform/frontend/src/components/chart/MetricTile.tsx` and `pages/HistoryPage.tsx`: `METRIC_COLUMNS` (:15). `MetricHistoryItem` already has `open_interest`, `funding_rate`, `funding_annualised` and `liq_notional_1h`.
- `platform/views/derivatives.py`:
  - `_liquidation_item` (:562) and `liquidations_page` (:568).
  - The bucket notional (:758-800) and `liquidation_bars` (:803).
- `platform/data_api/routes/derivatives.py`: `LiquidationItem` (:97) and `LiquidationBarItem` (:109).
- `platform/views/live_derivs.py`: `_relay_tick` (:139), `publish_liquidations` (:145) and `unsubscribe` (:98).
- `platform/kernel/indicators.py`: `basis_bps` and `funding_annualised`. `platform/kernel/liquidation.py`: `notional_units()`.
- `lightweight-charts` 5.2.1:
  - `createSeriesMarkers`; marker positions `atPriceTop|atPriceBottom|atPriceMiddle|aboveBar|belowBar`; `hoveredObjectId` on crosshair params.
  - The circle diameter is `ceiledEven(clamp(barSpacing, 12, 30)) × size × 0.8` (`dist/lightweight-charts.development.mjs:15526-15535`).
- Docs:
  - `pages/docs/kbData.ts`: the `chart-*` KB entries (:128-185).
  - Repo-root `CLAUDE.md` "What is collected" (:26). The epic names `platform/CLAUDE.md`, but the section lives in the root file.
  - `platform/CLAUDE.md` SSOT-06 (:240): the layout field list.

## Tasks & Acceptance

**Execution:**

*Backend: added fields only*
- [x] `platform/views/derivatives.py`: two additions.
  - `_liquidation_item` gains `notional_units` (`row.notional_units()`) and `notional_precision` (`row.price_precision + row.size_precision`).
  - Each `liquidation_bars` row gains `long_notional_units` and `short_notional_units`. Both are summed by side from the same archived rows as `notional_units`, at the same `notional_precision`, and follow the same null rule: null exactly when `notional_units` is null. A side with no rows is 0.
  - Rationale: the browser does no notional arithmetic.
- [x] `platform/data_api/routes/derivatives.py`: add the fields to `LiquidationItem` and `LiquidationBarItem`, with the D-170 JSON-range note. Then regenerate `frontend/openapi.json` and `frontend/src/api/schema.ts`.
- [x] `platform/views/live_derivs.py`: three additions.
  - A funding frame gains `annualised`: `float(funding_annualised(rate, interval))`, or None.
  - A mark or index frame gains `basis_mi_bps`: `float(basis_bps(last mark, last index))` from the bus's last mark and index of that iid, or None while either is unknown.
    - That per-iid state exists only while `derivs:{iid}` has a listener, and the last `unsubscribe` deletes it (MEM-02).
    - The mark ↔ index pairing `Known limit:` (latest of each, no staleness bound) goes in code.
  - A liquidation frame gains top-level `notional_units` and `notional_precision`.
- [x] `platform/views/tests/test_derivatives.py`, `views/tests/test_live_derivs.py`, `data_api/tests/test_derivatives_routes.py`: tests.
  - Hand-computed per-side notionals, including null and 0.
  - Item notional.
  - Frame `annualised`, `basis_mi_bps` (None before the pair exists, then the hand value) and the liquidation frame notional.
  - The state is dropped on the last unsubscribe.

*Layout persistence*
- [x] `platform/frontend/src/lib/chartLayout.ts`: add an optional `derivatives: DerivativesLayout` to `ChartLayout`.
  - Its keys are `oi`, `funding`, `basis`, `mark_index` and `liquidations`. Each holds `{on: boolean, style?: Record<output, OutputStyle>}`.
  - `liquidations` also holds `measure: "size" | "notional"` (default `"size"`) and `markers: boolean` (default true).
  - Add `derivativesOf` on the `footprintOf` pattern: an absent table means all off, and a bad field falls back by name and is recorded.
  - Wire it into `normalizeLayout`, `layoutForSave` and `sameLayout`.
- [x] `platform/views/preferences.py`: `_validate_derivatives`, accepted in `validate_layout` and the `[default]` table like `footprint`.
  - An unknown key, a wrong type, or a style other than `color`/`line_width`/`line_style`/`up_color`/`down_color` is refused with 422 naming the dotted key.
  - Add tests in `views/tests/test_preferences.py`, plus a frontend↔server mirror test on the footprint one's pattern.
  - Amend SSOT-06 in `platform/CLAUDE.md`.

*Data and formatting*
- [x] `platform/frontend/src/lib/units.ts`: add these helpers, with tests.
  - `formatDecimalText(text: string, places?: number)`: exact, string/BigInt, rejects anything that is not decimal text with `RangeError`.
  - `shiftDecimalText(text, digits)`: exact ×10^n, so a rate prints as a percent.
  - `formatCountdown(ms)`: `HH:MM:SS`, clamped at 0.
- [x] `platform/frontend/src/api/client.ts`: add `fetchFunding`, `fetchOpenInterest`, `fetchMarkIndex`, `fetchLiquidations` and `fetchLiquidationBars`, with inline template URLs and `HttpError` on failure. Export the schema types.
- [x] `platform/frontend/src/lib/derivativeSeries.ts` (new): pure functions, each unit-tested in `lib/derivativeSeries.test.ts`.
  - `bucketPoints(rows, field)`: null to whitespace, `t` from ms to s.
  - `fundingPerBar(events, candleTimes, gapTimes)`: the hold rule.
  - `mirroredLiquidations(rows, measure)`.
  - `eventSlot(tNs, candleTimes)`: binary search for the slot containing an event.
  - `mergeNewest(held, page)`: upsert by `t`, then a seam `gapRun`.
- [x] `platform/frontend/src/hooks/useDerivativePages.ts` (new): one generic hook used for open-interest, mark-index and liquidation-bars (bucket pages) and for funding (event pages).
  - Arguments: `kind`, `iid`, `barSeconds`, `enabled`, `chart`, `earliestCandleTime`.
  - The initial page has limit 120. It pages older on scroll-back the way `useFootprint` does, while its earliest `t` is later than `earliestCandleTime`.
  - It re-reads the newest page through `refreshNewest()`, called on bar close and reconnect.
  - Retries use the `useCandles` policy, and the hook exposes `error`.
  - Funding pages are deduplicated by `t` (D-170).
  - Tests go in `useDerivativePages.test.ts`.
- [x] `platform/frontend/src/hooks/useLiquidationEvents.ts` (new): liquidation rows for markers and the tape, deduplicated by `venue_event_id`.
  - Two modes: `markers` pages back to the earliest candle, capped at `MARKER_MAX_ROWS`; `tape` keeps the newest 50.
  - Live rows come in through `useLiveLiquidations`.
  - Tests cover the dedupe, the cap and 50-row eviction.

*Chart*
- [x] `platform/frontend/src/components/chart/LightweightChart.tsx` + `legend.ts`: spec and rendering support.
  - `IndicatorPaneSpec` gains:
    - `format?: (value, time) => string` and `text?: string`, passed through to `LegendSeries`; `LegendSeries.format` gains the `time` argument.
    - `zeroLine?: boolean`: a dashed price line at 0 in the pane's series.
  - The forming-bar values arrive as ordinary `data` changes, with `data` references kept stable.
  - A new `liquidationMarkers?: MarkerSpec[]` prop:
    - one `createSeriesMarkers` plugin on the candle series, `setMarkers` on change, detached on unmount;
    - `hoveredObjectId` drives a small tooltip `div` with the marker's lines;
    - the bar spacing is reported up through a callback (`onBarSpacing`).
  - Add `createSeriesMarkers` to the test mock.
- [x] `platform/frontend/src/components/chart/LiquidationMarkers.ts` (new): pure marker helpers.
  - Named constants: `MARKER_MERGE_COUNT = 3`, `MARKER_MIN_BAR_SPACING_PX = 6`, `MARKER_MIN_RADIUS_PX = 5`, `MARKER_MAX_RADIUS_PX = 18`.
  - `sqrtRadius(notional, maxNotional)`: sqrt scale between the min and max radius.
  - `markerSize(radiusPx, barSpacing)`: inverts the library's circle-diameter formula, with a `Known limit:` that it mirrors lightweight-charts 5.2.1 internals and must be re-checked when the pin moves.
  - `buildLiquidationMarkers(rows, candleTimes, barSpacing)`: per-bar, per-side merge above the count. Individual markers sit at `atPriceBottom` (long) or `atPriceTop` (short) at the decoded bankruptcy price; merged ones at `belowBar` or `aboveBar`. Markers are sorted by time, with stable ids.
  - Tests go in `components/chart/LiquidationMarkers.test.ts` (the AC names this file): the merge rule, the radius scale endpoints and monotonicity, and the size inversion.
- [x] `platform/frontend/src/components/chart/IndicatorPicker.tsx`: a pinned **Derivatives** group under Volume and Footprint.
  - Five checkboxes, driven by `derivatives`/`onDerivativeChange(key, on)` props.
  - When `derivativesDisabled` is set, every checkbox is disabled and the group shows the tag `spot: no derivatives`.
- [x] `platform/frontend/src/components/chart/LiquidationTape.tsx` (new): a panel beside the chart, with a header toggle `Liquidation tape` (`.tabbtn`, `aria-pressed`) as view state.
  - Columns: time, side, size, price and notional, all through `lib/units.ts`.
  - It reads "no liquidation feed for this instrument" for an id with no feed (an empty first page) and is disabled on spot.
  - Add the CSS in `index.css`.
- [x] `platform/frontend/src/pages/ChartPage.tsx`: wire it all together.
  - The hooks, gated by `enabled && market known && market !== "spot"`.
  - The specs: OI is a Line pane `deriv_oi`, with legend `oi` and `Δ`. Funding is a Histogram `deriv_funding` with up/down colours, rate %, `ann.` and the countdown. Basis is two Lines in group `deriv_basis` with `zeroLine`. Mark and Index are two overlays. Liquidations are a Histogram `deriv_liquidations`, mirrored, with the notional and `n`.
  - Forming-slot ticks from `useLiveDerivs`; `trimAfter` on replay; live gated off in replay.
  - A 1 s countdown ticker that runs only while Funding is drawn.
  - Legend routing for the `deriv_*` groups: hide goes to the layout `on` flag, settings opens `IndicatorSettingsDialog` with that entry's outputs (Liquidations adds a `measure` choice and a `markers` toggle), and remove turns the entry off.
  - `handlePaneHeights` keeps the `deriv_*` ids.
  - Markers, the tape, and the pinned-group props.
- [x] `platform/frontend/src/pages/HistoryPage.tsx`: add tiles `open_interest` (OI), `funding_rate` (Funding) and `liq_notional_1h` (Liquidations 1h) to `METRIC_COLUMNS`. An all-null column is skipped, as today. Add tests to `HistoryPage.test.tsx`.

*Tests and docs*
- [x] `platform/frontend/src/components/chart/LightweightChart.test.tsx`: tests.
  - `format`/`text` reach the legend.
  - The zero line.
  - The markers plugin is created, set, cleared and detached.
  - The hover tooltip.
- [x] `platform/frontend/src/pages/ChartPage.test.tsx`: tests.
  - Each of the five entries mounts its spec (ids, kinds, placement) and shows its legend values.
  - The funding countdown ticks (fake timers).
  - Spot disables the group and fetches or subscribes nothing.
  - The replay cut.
  - A forming-slot tick, and an old tick ignored.
  - The persisted on/off, style and pane height round-trip.
  - The tape toggle.
- [x] `platform/frontend/src/pages/docs/kbData.ts`: a new KB entry `chart-derivatives` (group `chart`).
  - The five entries and their sources.
  - The two price kinds: `bankruptcy` is what every row carries today; `mark` is defined for a feed that sends it and no current feed does.
  - The countdown's clock rule: the client clock against the server's `next_funding_ns`, so client skew shifts it.
  - The funding hold rule, the null forming values, the marker merge and scale, and the tape.
  - Cross-link it from `chart-legend` and `chart-layout`.
- [x] Repo-root `CLAUDE.md` "What is collected": state where each derivative is shown (chart panes and overlay, markers, tape, History tiles, ranking fields).
- [x] `platform/docs/DATA_DICTIONARY.md`: amend §2.16 with the item, bar and frame fields and the frame enrichment. Amend §1.27 with the live frame keys.
- [x] `platform/docs/DATA_INTEGRITY_AUDIT.md`: rows from D-180.
  - The funding hold across change-deduped events.
  - The forming-slot nulls (`oi_change`, `basis_ml_bps`).
  - The live mark ↔ index pairing.
  - Client-clock countdown skew.
  - The marker size mirroring library internals.
  - The marker row cap.
- [x] `platform/docs/DEPLOY_CHECKLIST.md`: a 33-5 deferred operator action (rebuild `data_api`), with verify steps.

**Acceptance Criteria:**
- Given a Bybit linear coin with Derivatives on, when the chart loads and is scrolled back, then the OI, Funding, Basis and Liquidations panes and the Mark/Index overlay page back with the candles. The seams between pages are whitespace, never joined.
- Given a reload after any entry's on/off, style or pane height changed, when the coin is reopened, then the same state comes back from `chart_layouts.toml`.
- Given `grep -rnE "basis|annuali|notional" platform/frontend/src --include=*.ts --include=*.tsx`, when the arithmetic sites are inspected, then no file computes a basis, an annualisation or a notional. Formatting and plotting conversions only.
- Given the backend and frontend suites, when they run, then all pass and the regenerated `openapi.json`/`schema.ts` match the export.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 1, medium 6, low 9)
- defer: 0
- reject: 4: (high 0, medium 0, low 4)
- addressed_findings:
  - `[high]` `[patch]` The newest-page refresh at bar close read the archive before the 60 s flush landed, and the closed slot's live value was dropped, so funding could hold a stale rate for a whole bar. Added a 60 s newest poll and a re-read `ARCHIVE_LAG_MS` after each close. Live funding events are kept and deduplicated by `t`. Closed-slot live values stay until the route serves that slot.
  - `[medium]` `[patch]` A refresh that arrived while a page was loading was lost; it is now flagged pending and run when the page settles.
  - `[medium]` `[patch]` A transient failure of a newest re-read blanked a loaded pane with "load failed". It now retries with backoff and shows the error only while no rows are held.
  - `[medium]` `[patch]` In Lines mode the Derivatives toggles and the tape did nothing. The group is now disabled and tagged "Candles mode only", and the tape is disabled until it can draw.
  - `[medium]` `[patch]` In replay the tape showed only the newest 50 rows, cut to nothing. It now loads the 50 before the replay cutoff.
  - `[medium]` `[patch]` A full `setData` ran on every derivative series each second (countdown and live ticks). Each series' data is now memoised separately, with a test.
  - `[medium]` `[patch]` A `RangeError` from `formatUnits`, `unitsToNumber` or `formatDecimalText` (above 2^53, a precision over 16, or `NaN` text) could crash the chart. The call sites are now guarded with `safeText` (`—`, logged once).
  - `[low]` `[patch]` Settings defaults showed the wrong colours (shared `DERIVATIVE_OUTPUT_TOKENS`). A marker past the last slot landed on the wrong bar (`eventSlot` now gets `barSeconds`). The countdown during replay now reads `next —`. A funding event inside a gap slot now restarts the hold at the next real bar. Re-enabling an entry now re-reads the newest page. Group lookup uses `Object.hasOwn`. The DATA_DICTIONARY table cells are fixed. History tiles are labelled with units. A colour-validation mirror test was added.

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 4, low 4)
- defer: 0
- reject: 13: (high 0, medium 3, low 10)
- addressed_findings:
  - `[medium]` `[patch]` On a 1m chart each bar close cancelled the previous close's `ARCHIVE_LAG_MS` (75 s) re-read, so it never fired. Each close now keeps its own timer (`useDerivativePages.ts`), with a test.
  - `[medium]` `[patch]` Turning the markers or the tape off and on never re-read the newest page, though live rows were refused while off. Re-enabling now re-reads, like `useDerivativePages` (`useLiquidationEvents.ts`), with a test.
  - `[medium]` `[patch]` A `refreshNewest` (reconnect, bar close) that arrived while a marker page was in flight was dropped. It is now flagged pending and run when that page settles, with a test.
  - `[medium]` `[patch]` A failed Bar Replay tape page became an empty tape reading "none up to the replay time". It is now `replayError` ("load failed") shown as the tape's error (DATA-07), with a test.
  - `[low]` `[patch]` A liquidation row with a side other than `long`/`short` was drawn as a long. It is now left out of the markers and logged to the ErrorBar (`LiquidationMarkers.ts`), with a test.
  - `[low]` `[patch]` Every live liquidation re-sorted up to 5000 held rows. A row newer than everything held is now appended without a sort (`mergeLiquidations`), with a test.
  - `[low]` `[patch]` A marker tooltip outlived its marker when the markers changed under a still pointer. A markers change now closes it (`LightweightChart.tsx`).
  - `[low]` `[patch]` `eventSlot` put an event that fell in a hole wider than the gap cap between two slots into the earlier slot. Each found slot's end is now checked (`derivativeSeries.ts`), with a test.

## Design Notes

- **Why live frames are enriched server-side.** The routes read the archive, which lags the flush (60 s). The forming bar therefore needs the live channel. Basis and annualised funding are stateless kernel derivations (SSOT-01), so `views/live_derivs.py` computes them, as `views/derivatives.py` does for pages. `oi_change` and `basis_ml_bps` depend on the bucket or the candle store, so they stay null on the forming slot until the bar is served.
- **Spot is decided by the server's `market`.** The browser never parses ids.
- **Marker size.** lightweight-charts sizes markers by a multiplier of a bar-spacing-derived height. The radius scale is defined in px, which is what the AC names, and converted to that multiplier:
  ```ts
  const diameter = (bs: number) => ceilEven(Math.min(Math.max(bs, 12), 30));
  export const markerSize = (r: number, bs: number) => (2 * r) / (0.8 * diameter(bs));
  ```
- Implementation decisions (dev, 2026-10-06):
  - `ChartLayout.derivatives` is required on the client type and always served by the server (absent on the wire = `DERIVATIVES_DEFAULTS`, every entry off), the `footprint` precedent: no `?.` at every reader. A present table carries all five entries; per entry `on` is required (Liquidations also `measure`, `markers`), `style` optional. Output labels are pinned per entry (`DERIVATIVE_OUTPUTS`: `oi`; `rate`; `mark_index`/`mark_last`; `mark`/`index`; `liquidations`), so the server also refuses an unknown output; the mirror test checks keys, outputs, measures and the line styles/widths.
  - Liquidations are two Histogram series (`deriv_liquidations.long`/`.short`) in one pane, since one series holds one value per bar; their style is one `liquidations` output with `up_color` (shorts) / `down_color` (longs), which `IndicatorSettingsDialog` already edits.
  - The legend's `Δ`, `ann.` and countdown are part of one member's formatted text (`90 · Δ -30`, `rate … · ann. … · next …`); status notes (`markers hidden: zoom in`, `markers capped at 5000`) join the Liquidations legend title; `load failed` is the spec's `text`.
  - The eye and the x of a `deriv_*` row both turn the layout `on` flag off (the spec routes both there); the gear opens `IndicatorSettingsDialog` with Liquidations' `measure` and `markers` as choice params.
  - The derivatives draw in Candles mode only (they share the candles' bar axis; Lines mode's axis is snapshot seconds), like Footprint.
  - Data flow: `hooks/useChartDerivatives.ts` composes the four `useDerivativePages`, two `useLiquidationEvents` (markers, tape) and the two live hooks; pane specs are pure builders in `components/chart/derivativePanes.ts`. Pages older than the first loaded candle are not plotted (`fromTime`) so the candles keep owning the time axis; the hooks page back on the candles' earliest time and on visible-range moves.
  - Funding under replay: every loaded event feeds `fundingPerBar` (a bar only sees events before its end, so nothing leaks backwards) and the displayed bars are already cut; the countdown reads the newest event up to the cutoff.
  - The merged marker's `Σ <notional>` is the server's per-side notional of that bar (`/liquidation-bars`' `long_notional_units`/`short_notional_units` at the row's `notional_precision`, the rows the Liquidations pane already loads, passed to `buildLiquidationMarkers` as a `t -> row` map); nothing is summed in the browser. A missing row or a null side reads `Σ — · <count>` (tooltip `notional Σ —`) at `MARKER_MIN_RADIUS_PX`, a `Known limit:` naming the live-edge lag (D-172) and the upgrade path (a server-side running per-bar notional on the live channel). The count is a plain row count.
  - Retries follow `useCandles`' policy literally (502/503/504 and network errors retried with capped backoff; any other status, a DATA-07 500 included, final with `load failed`), logged once per failing streak.
  - Layout tests live in `views/tests/test_chart_layouts.py` (where every layout/footprint validation test and the mirror tests are), not `test_preferences.py` (indicator config); the PUT/GET 422 and round-trip are in `data_api/tests/test_layout.py`.
  - Funding older pages ask for 500 rows (the route max), a `Known limit:` in `useDerivativePages.ts`; the markers' older pages likewise.
  - Review patches (dev, 2026-10-06): (1) the archive lags the live feed by up to a flush (`flush_interval_seconds` 60), so each drawn route's newest page is re-read every `NEWEST_POLL_MS` (60 s), at each bar close and once more `ARCHIVE_LAG_MS` (75 s) after it; a closed bucket keeps its live raw value (`LiveSlots`, `lib/derivativeSeries.ts`) until the route serves that `t` with a non-null value, and live funding ticks are kept as events (deduplicated by `t`, the route winning) so the hold uses them after the slot closes. (5) Under Bar Replay the tape reads its own page of the 50 before the cutoff bar's end (`before_ns = (cutoff + barSeconds)·1e9`), dropped when the replay ends; the funding countdown reads `—` there.
- **Funding hold example.** `[10:00:20 → 0.0001, 10:03:10 → -0.0002]` on bars `10:00, 10:01, (gap 10:02), 10:03, 10:04` gives `[0.0001, 0.0001, ws, -0.0002, -0.0002]`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest views/tests data_api/tests kernel/tests tests -q -p no:cacheprovider` -- expected: all pass, no warnings. A Redis-dependent test needs a throwaway redis on 6379.
- `cd platform && ruff check <touched py> && ruff format --check <touched py> && mypy <touched py>` -- expected: clean, or the same as baseline.
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && git diff --stat` -- expected: the regenerated files are committed with the change.
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass.

## Auto Run Result

Status: done (follow-up review pass)

**Summary:** This pass was a fresh adversarial review (Blind Hunter and Edge Case Hunter) of the whole story diff since `b9c71f79ff`. It applied 8 patches, all in the frontend's derivatives data flow:
- per-close archive re-reads that survive the next close;
- the marker and tape hooks re-reading on re-enable and keeping a re-read asked for mid-page;
- a failed replay tape shown as a failure rather than as an empty tape;
- unknown liquidation sides logged rather than drawn as longs;
- an append fast path for live liquidations;
- a stale marker tooltip closed;
- `eventSlot` honouring each slot's end.

**Files changed:**
- `platform/frontend/src/hooks/useDerivativePages.ts`: one `ARCHIVE_LAG_MS` timer per bar close, all cleared when the hook is turned off or unmounts.
- `platform/frontend/src/hooks/useLiquidationEvents.ts`: re-read on re-enable, a pending newest re-read, `replayError`, and the append fast path in `mergeLiquidations`.
- `platform/frontend/src/hooks/useChartDerivatives.ts`: the tape's error includes `replayError` under a replay.
- `platform/frontend/src/components/chart/LiquidationMarkers.ts`: an unknown side is logged once and not drawn.
- `platform/frontend/src/components/chart/LightweightChart.tsx`: the marker tooltip closes when the markers change.
- `platform/frontend/src/lib/derivativeSeries.ts`: `eventSlot` checks the found slot's end for every slot, not only the last.
- Tests: `useDerivativePages.test.ts`, `useLiquidationEvents.test.ts`, `LiquidationMarkers.test.ts`, `derivativeSeries.test.ts`.

**Review findings:** 8 patches applied (medium 4, low 4), 0 deferred, 13 rejected:
- No funding hold past the newest event's bar while `liveBar` is null: the contract's rule.
- "No feed" taken from an empty first page: the spec's rule.
- The live-edge seam after a long sleep or outage left as whitespace, for both bucket and marker pages: this mirrors the candles' own `mergeByTime` seam rule (Story 32.1, AD-F6).
- The ns cursor's float rounding: already registered as D-170.
- A null precision on a known-zero bucket: 33.4 behaviour that predates this story.
- A derivs tick that arrives before a trade opens the forming bar: the spec's forming-slot rule.
- The module-wide log-once flags: deliberate, to avoid flooding the console.
- A re-render on each crosshair move over a marker: tolerable cost.
- The remaining items were duplicates.

**Follow-up review recommended:** false. The fixes are localized to two hooks and three helpers, each covered by a new test, and change no API, schema or backend behaviour.

**Verification:**
- `npx tsc -b` is clean.
- `npm run lint`: 0 errors and the 3 baseline warnings, all in untouched files.
- `npm test`: vitest gave 1133 passed across 54 files, and the 7 codegen tests passed.
- `npm run build` passes.
- No backend file changed in this pass.

**Residual risks:**
- The residual risks of the first pass still stand: D-181..D-185, and the chart has not been exercised in a real browser.
- A live-edge seam after a sleep or outage longer than one page draws as whitespace until a remount, the same as the candles.
