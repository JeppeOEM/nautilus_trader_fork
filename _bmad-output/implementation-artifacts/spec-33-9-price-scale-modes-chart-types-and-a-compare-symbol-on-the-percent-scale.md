---
title: 'Story 33.9: Price-scale modes, chart types, and a compare symbol on the percent scale'
type: 'feature'
created: '2026-10-07'
status: 'done'
baseline_revision: '02396e7ee5739df6f7acf82ef5efccaa54fc5215'
final_revision: '7e0ae60d4452999c85340f4c20d9e06fe9583cbe'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** the chart has one series type (`CandlestickSeries`) and one linear price scale (no `PriceScaleMode` anywhere). A 30 % move and a 3 % move cannot be compared, a long history is unreadable without log, and the same coin on two venues (or two coins) cannot be drawn on one chart. `GET /api/markets` does not exist yet: `markets:live` is read only by `bot_tui`.

**Approach:** add three layout-persisted chart settings on the price pane:
- the price scale: `Normal | Log | Percent | Indexed to 100`, an auto-scale lock and invert;
- the chart type: `Candles | Hollow candles | Bars | Line | Area | Baseline | Heikin Ashi`;
- up to three compare symbols drawn as lines on the Percent (or Indexed) scale, plus an optional cross-venue Spread pane in bps.

Heikin Ashi is a pure display transform that nothing else reads. Compare series align on the main bar times, with gaps and no interpolation. A new `views` bus caches `markets:live` and serves `GET /api/markets`. It lists the same-asset markets first (`kernel.venues.same_asset`) and feeds the Compare picker.

## Boundaries & Constraints

**Always:**
- AD-F6: a derived series (Heikin Ashi, Hollow colours, Line/Area/Baseline close values) is only ever handed to the main series' `setData`/`update`.
  - The real `data` stays the input of everything else. That covers gap runs, measure, volume profile, footprint, markers, price lines, drawings, the legend extras, the alert dialog and every `ChartPage` consumer (`replay.displayed`).
  - Compare and Spread values come only from real closes at the same bar time. Where either side has no bar there is a whitespace gap, never an interpolated, carried or zero-filled value.
- AD-D12 (added keys only):
  - The layout gains the optional keys `chart_type`, `price_scale` and `compare`. A layout saved before them loads with the defaults `candles`, `{mode: "normal", auto_scale: true, invert: false}` and `{symbols: [], spread: false}`.
  - Every existing layout key and its validation is unchanged.
  - The new values are validated strictly on both sides: backend `views/preferences.py` and frontend `normalizeLayout` with `fallbacks`. Frontend/backend constant pairs are pinned by mirror tests.
- Settings persistence:
  - Every setting persists through `useChartLayout` (per coin), and Save as default carries it into the template.
  - A template's compare symbol equal to the coin being opened is dropped on load, without overwriting the stored template.
- Compare data:
  - Each compare symbol is fetched by its own `useCandles(iid, chart, true, barSeconds)`, with the same timeframe and cursor rule.
  - Each follows live through its own `useLiveCandle`, wired like the main one (`onBarClosed` → `appendBar`, `onReconnect` → `refreshNewest`). It lives in a renderless component keyed by iid, because hooks cannot be called in a variable-count loop.
- One formula, named in two docstrings:
  - Spread is `(a / b − 1) × 1e4` bps, where a is the main close and b is the compare close.
  - It is the `research/domain/correlation.py` `basis_bps` formula, which `research/application/aligned.py` uses.
  - The TS function's doc comment names it, and `basis_bps`'s docstring gains a line naming the TS twin.
  - A shared fixture case (hand-computed) is asserted on both sides.
- `GET /api/markets`:
  - It is served from a `views` bus. It is the one `markets:live` subscriber of the process, started in `app.py`'s lifespan beside `archive_bus`. The channel name is kept as a local copy, so `views` imports no `ranking` and no `bot_tui`.
  - The whole message is validated (bot_tui `_validated`'s rules). A malformed message is ledgered (DATA-07) and the venue's previous list is kept.
  - A venue older than 180 s is reported in `stale_venues`. One older than 900 s is dropped (bot_tui's constants, copied).
  - When no venue is live the route returns 503, never an empty list posing as "no markets".
- Platform rules: no new dependency (NFR12); the LGPL header on new files; MR4/OPS-01 docs; the Docs page is kept truthful.

**Block If:**
- lightweight-charts 5.2.1 turns out not to normalise each series by its own first visible value in Percent/IndexedTo100 mode (the analysis of `development.mjs` says it does). If so, compare-on-percent needs a design decision.

**Never:**
- Modify `nautilus_trader/` or `crates/`.
- Persist layout or compare state in `localStorage`.
- Feed a Heikin Ashi value to an indicator, drawing, alert, profile, measure or gap computation.
- Interpolate a compare value.
- Add Renko, Kagi, Point & Figure, Range bars or Volume candles (they stay excluded).
- Build symbol search, a watchlist or keyboard shortcuts (Story 33.12; the Compare field is the AC's "text field" fallback).
- Change the existing Lines (snapshot-seconds) mode's behaviour.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Heikin Ashi chain | candles `[{o10,h12,l9,c11},{o11,h13,l10,c12}]` | bar0: haC=10.5, haO=10.5 ((o+c)/2), haH=12, haL=9. bar1: haC=11.5, haO=10.5, haH=13, haL=10 | none |
| HA across a gap | candle, whitespace `{time}`, candle | whitespace stays whitespace; the bar after the gap restarts its haO at (o+c)/2 | none |
| HA live update | forming bar ticks | the last HA bar is recomputed from the previous HA bar and the forming OHLC; earlier bars are unchanged | none |
| Hollow rule | close ≥ open; close < open | close ≥ open is a hollow body (transparent fill). close < open is a filled body. The colour is up when close ≥ the previous bar's close (the first bar or the first after a gap compares to its own open), else down. | none |
| Compare align | main times t1,t2,t3; compare has t1,t3 plus t4 | `[{t1,v},{t2} whitespace,{t3,v}]`; t4 is ignored (not a main time) | none |
| Compare gap bar | compare bar at t2 with null OHLC (a gap) | `{t2}` whitespace | none |
| Spread | main c=101, compare c=100 at t | 100 bps (1 % of b); whitespace where either side is whitespace | b ≤ 0 or non-finite: whitespace at that bar and a console error naming the bar (a defect, mirrors `basis_bps` raising) |
| Compare forces percent | stored mode `normal`/`log`, one compare present | the effective mode is Percent; the Normal/Log choices are disabled with a title saying why; the stored mode is kept, and removing the last compare restores it | none |
| Compare refused | iid = the main iid, a duplicate, a 4th symbol, or text without a `.VENUE` suffix | the add is refused with an inline message; nothing is saved | none |
| Compare load fails | `useCandles` reports `loadFailed` | the legend row stays and shows `no data`; it is removable | none |
| Spread availability | 0 or ≥2 compares | the Spread toggle is disabled; the stored `spread` is kept and no pane is drawn | none |
| Lines mode | `mode = "lines"` | the chart type control and Compare are disabled; compares are not drawn (kept in the layout); the scale controls still apply | none |
| Markets bus | a valid message per venue | the venue's list replaces its previous one | malformed: ledgered `views.markets`, previous list kept |
| `GET /api/markets?instrument_id=X` | live lists | items `{instrument_id, symbol, venue, same_asset}`: X itself is omitted, `same_asset` rows come first, then by venue, then by id | no live venue: 503; X malformed: 400 |
| Layout PUT | `chart_type: "renko"`, `price_scale.mode: "x"`, more than 3 compare symbols, a duplicate, an unknown key | 422 naming the key | strict |
| Scale double-click | the operator dragged the scale (auto off), then double-clicks the right scale | auto-scale is back on (the library default) and the persisted `auto_scale` becomes true | none |

</intent-contract>

## Code Map

All paths are under `platform/`.

- `frontend/src/components/chart/LightweightChart.tsx`:
  - `ChartMode` L71; props L236-412; `seriesRef` L734 (typed Candlestick, widen it to the main series union).
  - The `[mode]` effect L1029-1109 adds and removes the main series. It must also recreate the series on a `chartType` change, with the same registry/plugin teardown as the mode switch. Primitives, markers, the gap painter and price lines all re-attach through their existing `[mode]` deps, so extend each dep list.
  - `setData` L1113/1133, live `update` L1386/1398 (the only two OHLC readers); the panes effect L1188-1357 (overlay series on pane 0, the legend build L1297-1357); `findDrawingHit` L634, `overPriceLine` L665 (widen types).
  - Context menu: the `menu` state L773, the right-click handler L1968-1988, the render L2100-2151. It has no outside-click/Esc close.
  - `createChart` options L831-946 (`rightPriceScale` L864). No dblclick handling exists.
- `frontend/src/components/chart/rangeDrag.ts:65`: the host series type, to widen.
- `frontend/src/components/chart/legend.ts`: `LegendAction`, `iconButton`, rows by `group`, `valueAt` L80.
- `frontend/src/components/chart/LightweightChart.test.tsx`: `vi.mock("lightweight-charts")` L173-180 (sentinels; add `BarSeries`/`AreaSeries`/`BaselineSeries`/`PriceScaleMode`), `addSeriesMock` L36, `makeSeriesMock` L134 (add `priceScale()`), the chart mock's `priceScale` L311 (add `applyOptions` recording), mode-switch tests L632-686.
- `frontend/src/pages/ChartPage.tsx`:
  - `ChartForCoin` 1867 / `ChartInner` keyed by `instrumentId:bar_seconds:revision`; the header `chart-topbar` 1509 (the Candles/Lines pair 1533-1555, Alert 1607); `mode` state 447 + `patchLayout` 436.
  - `useCandles` 513, `useLiveCandle` 553, `replay` 534; `chartPanes` ~861; `handleLegendAction` 870-898; `<LightweightChart …>` ~1652.
- `frontend/src/lib/chartLayout.ts`: `ChartLayout` 117, `BUILT_IN_LAYOUT` 134, `volumeColorByOf` 380 (the optional-key pattern), `normalizeLayout` 394, `layoutForSave` 431, `layoutKey`/`sameLayout` 446/453; tests `lib/chartLayout.test.ts`, `hooks/useChartLayout.test.ts`.
- `frontend/src/hooks/useCandles.ts`: `ChartDatum` 39, `isValidOhlc` 47, `useCandles` 173 (per-instance state; it pages on the shared chart's logical range). `hooks/useLiveCandle.ts`.
- `frontend/src/api/client.ts`: the layout functions 490-520; add `fetchMarkets`. `frontend/src/api/schema.ts` is regenerated.
- `frontend/src/lib/units.ts`: `formatDecimal`, `formatPercent`. `components/chart/derivativePanes.ts:20` formats bps.
- `frontend/src/pages/docs/kbData.ts`: `chart-legend` 154, `chart-layout` 164 (the entry format).
- `views/preferences.py`: `_LAYOUT_KEYS` 557, `VOLUME_COLOR_MODES` + `BUILTIN_DEFAULT_LAYOUT` ~650-680, `validate_layout` 892 (the optional-key set at 909-911, the normalized return ~922), `_validate_volume_color_by`, `_layout_table` (TOML: no null), `load_chart_layouts`. `views/tests/test_chart_layouts.py`: the `test_*_mirror_the_frontend` pattern.
- `views/archive_status_bus.py`: the bus template (validate, ledger, `run`/`_receive` liveness, `latest`). `views/tests/test_archive_status_bus.py`.
- `bot_tui/markets_state.py`: `_validated` (the message rules to mirror), `MARKETS_STALE_SECONDS=180`, the expiry 900.
- `ranking/application/engine.py:84` `markets_message`: the payload `{venue, ts, markets:[{instrument_id, symbol}]}`.
- `kernel/venues.py`: `same_asset` 193, `venue_of`, `has_venue`, `MalformedInstrumentId`.
- `data_api/buses.py`, `data_api/app.py` (lifespan ~112-134, `include_router` 274-283), `data_api/routes/archive.py` (the 503-on-None route pattern), `data_api/export_openapi.py`.
- `research/domain/correlation.py:348` `basis_bps`.
- Docs:
  - `docs/DATA_DICTIONARY.md`: new §2.17 after §2.16 (3740).
  - `docs/DATA_INTEGRITY_AUDIT.md`: next row D-206.
  - `docs/DEPLOY_CHECKLIST.md`: the 33-8 section at 1674 is the format.
  - `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md`: §A2 (190) and the exclusions (729).

## Tasks & Acceptance

**Execution:**
- [x] `views/preferences.py` + `views/tests/test_chart_layouts.py`:
  - Add `CHART_TYPES = ("candles","hollow","bars","line","area","baseline","heikin_ashi")`, `PRICE_SCALE_MODES = ("normal","log","percent","indexed")`, `PRICE_SCALE_DEFAULTS` and `COMPARE_DEFAULTS`, and `MAX_COMPARE_SYMBOLS = 3`.
  - Make the three keys optional in `validate_layout`, with validators that refuse a wrong type, an unknown sub-key, a bad enum, more than 3 symbols, a duplicate, or an id `venue_of` rejects (each a `LayoutError` naming the key). Add their defaults to `BUILTIN_DEFAULT_LAYOUT`. An empty `symbols` list is written as `[]`.
  - Tests: defaults when absent, each refusal, a TOML round trip, and the mirror tests for the frontend constants.
- [x] `views/markets_bus.py` (new) + `views/tests/test_markets_bus.py`:
  - `MarketsBus`: a per-venue cache of valid `markets:live` messages, with a monotonic received-at time.
  - `handle_message` validates the whole message and ledgers a malformed one at `views.markets`.
  - `listing(instrument_id, now)` returns the ordered items and `stale_venues` per the matrix, or None when no venue is live.
  - `run`/`_receive` follow `ArchiveStatusBus`'s reconnect discipline with a 180 s silence liveness.
  - Tests cover every matrix row of the bus: valid, malformed (ledgered, previous list kept), wrong-venue id, stale, expired, the same-asset ordering, and self omitted.
- [x] `data_api/buses.py`, `data_api/app.py`, `data_api/routes/markets.py` (new) + `data_api/tests/test_markets.py`:
  - Add `markets_bus` and start it in the lifespan.
  - Add `GET /api/markets` with an optional `instrument_id` query and a typed response `{items, stale_venues}`: 503 when the bus has no live venue, 400 for a malformed `instrument_id`.
  - Include the router. Then regenerate `frontend/openapi.json` + `frontend/src/api/schema.ts`.
- [x] `research/domain/correlation.py`: add one docstring line to `basis_bps` naming its TS twin `frontend/src/lib/compare.ts` `spreadBps`. Add a test case on the shared fixture values (main 101, compare 100 → 100 bps) to its existing tests.
- [x] `frontend/src/lib/heikinAshi.ts` (new) + test: a pure `heikinAshi(data: ChartDatum[]): ChartDatum[]` and `heikinAshiNext(prevHa, bar)` for the live update, per the matrix. A fixture test is hand-computed, covering gap restart and whitespace.
- [x] `frontend/src/lib/chartTypes.ts` (new) + test:
  - The `ChartType` and `PriceScaleMode` string unions and their constants (the mirror of the backend).
  - `seriesRows(type, data)`, which maps real candles to the main series' rows (OHLC for candles/bars, per-bar colours for hollow, `{time, value: close}` for line/area/baseline, HA for heikin_ashi; whitespace kept).
  - `hollowColors` per the matrix, and the scale-mode mapping to the library's `PriceScaleMode`.
  - Tests: every type on one fixture.
- [x] `frontend/src/lib/compare.ts` (new) + test:
  - `COMPARE_PALETTE`: 3 named colours via CSS vars `--chart-compare-1..3`, defined for both themes where the chart vars live.
  - `alignCompare(mainTimes, compareCandles, liveBar?)` and `spreadBps(mainRows, compareRows)` per the matrix. The doc comment names `basis_bps`.
  - `validateCompareInput(text, mainIid, current)`.
  - Tests: alignment, the gap bar, spread, b ≤ 0, and each refusal.
- [x] `frontend/src/lib/chartLayout.ts` (+ test): add `chart_type`, `price_scale` and `compare` to `ChartLayout`/`BUILT_IN_LAYOUT`, with `…Of(raw, present, fallbacks)` readers in the `volumeColorByOf` pattern, `layoutForSave`, and `layoutKey`/`sameLayout` coverage. Drop a compare symbol equal to the coin on load (not a fallback).
- [x] `frontend/src/components/chart/LightweightChart.tsx` + `rangeDrag.ts` + `legend.ts`:
  - New props `chartType`, `priceScale` (mode, autoScale, invert), `onPriceScale(patch)` and `heikinLabel`.
  - The main series is created by type (Candlestick for candles/hollow/heikin_ashi, Bar, Line, Area, Baseline), recreated on a type change without a view reset. Its data comes from `seriesRows`, and the live `update` uses the type's row, including `heikinAshiNext`.
  - Baseline `baseValue` is the close of the first visible bar, re-applied on a visible logical range change.
  - The right price scale gets `chart.priceScale("right").applyOptions({mode, autoScale, invertScale})`.
  - A right-click inside the right scale strip (x ≥ width − `priceScale("right").width()`) opens a scale menu (the 4 modes, Auto, Invert) in the existing menu style, closed by Esc or an outside click.
  - A dblclick inside the strip reports `{auto_scale: true}`. The Auto toggle off calls `setAutoScale(false)`.
  - Widen the host series types. The legend gets a read-only "Heikin Ashi (derived)" row while that type is active.
- [x] `frontend/src/components/chart/LightweightChart.test.tsx`: extend the mock and add tests:
  - each type adds the right series definition and options, and a type switch removes the old series and keeps the view;
  - each scale mode and auto/invert reach `priceScale("right").applyOptions`;
  - a right-click on the scale opens the menu and a dblclick reports auto;
  - the HA isolation: the main `setData` receives HA rows, while the overlay and pane series data, the price lines and a drawing click's reported price stay the real values;
  - a compare overlay spec draws a Line on pane 0 with its whitespace kept.
- [x] `frontend/src/pages/ChartPage.tsx` (+ test):
  - Header controls in the chart-type cluster: a `Chart type` select, a `Price scale` select, `Auto` and `Invert` toggles (`aria-pressed`) and a `Compare` button. Compare opens an inline field with a datalist from `fetchMarkets(instrumentId)`, the same-asset entries first; if the markets fetch fails, free text is still accepted and the failure is shown inline.
  - A renderless `CompareFeed` per symbol, keyed by iid, wired as the Always rule says.
  - Compare overlays go into `chartPanes` with `placement: "overlay"`, group `compare:<iid>`, colour from `COMPARE_PALETTE`, the label = the iid, and the value formatted with that feed's precision. Aligned to `replay.displayed` times plus the live bar time when one is shown.
  - The `Spread` toggle, enabled for exactly one compare, adds a `compare-spread` pane (bps, zero line).
  - `handleLegendAction` removes `compare:*` and turns spread off on `compare-spread` removal.
  - The effective scale mode is forced as the matrix says. Every change goes through `patchLayout`.
  - Lines mode disables type and Compare.
  - Tests: a compare add, remove and persist, the forced percent, spread enablement, and the persisted type/scale.
- [x] `frontend/src/api/client.ts`: `fetchMarkets(instrumentId?)` typed from `schema.ts`.
- [x] `frontend/src/pages/docs/kbData.ts`:
  - a new `chart-type-scale` entry: the types, HA derived-only, Hollow's rule, the scale modes, the per-series first-visible-value normalisation, compare alignment and gaps, the spread formula, and the limits;
  - `chart-layout`'s "What is saved" gains the three keys;
  - `chart-legend` gains the compare rows.
- [x] Docs:
  - `docs/DATA_DICTIONARY.md` §2.17: `GET /api/markets` and the markets bus, plus the three layout keys.
  - `docs/DATA_INTEGRITY_AUDIT.md` D-206: HA mistaken for price, closed by the isolation tests.
  - D-207: Percent/Indexed normalises every price-pane series (a compare and an overlay indicator alike) by its own first visible value, so an overlay indicator sits offset from the candles by its first-value ratio. A Known limit, stated on the Docs page.
  - D-208: compare history pages only while the shared visible range nears the left edge, so it can trail the main series with whitespace. A Known limit.
  - D-209: a stale markets list.
  - `docs/DEPLOY_CHECKLIST.md`: a 33-9 section with deferred operator actions (rebuild `data_api`; a smoke check of `curl /api/markets`; the layout file needs no migration).
  - The spec-multi-exchange §A2 and the exclusions list are amended in place with `[amended 2026-10-07: Story 33.9]`.

**Acceptance Criteria:**
- Given the price pane, when the operator picks Log, Percent or Indexed to 100 from the header or the scale's right-click menu, then the right scale switches mode, and Auto off keeps the vertical range through scrolls until a scale double-click resets it. Invert flips the scale, and each persists per coin and through Save as default.
- Given any of the 7 chart types, when selected, then the main series redraws in that type, and the volume and indicator panes, drawings, price lines, markers and the gap painter are unaffected and stay attached.
- Given Heikin Ashi, when active, then the legend shows "Heikin Ashi (derived)" and no consumer other than the main series' data receives HA values (tested).
- Given one to three compare symbols, when added, then each draws as a coloured line on the price pane in Percent (or Indexed) mode, aligned on bar time with gaps. Each is removable from its legend row, persists in the layout and follows live bars.
- Given exactly one compare, when Spread is on, then a bps pane shows `(a/b − 1)·1e4` with gaps where either side has none.
- Given the backend, when `GET /api/markets?instrument_id=BTCUSDT-LINEAR.BYBIT` runs with live lists, then the same-asset markets of other venues are listed first, and when no venue is live it answers 503.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 19: (high 0, medium 3, low 16)
- defer: 0
- reject: 6: (high 0, medium 2, low 4)
- addressed_findings:
  - `[medium]` `[patch]` `MarketsBus.listing` iterated dicts the loop mutates while the threadpool route read them; it now reads snapshots (threaded test).
  - `[medium]` `[patch]` The `/api/markets` integration test published a fake venue on the real `markets:live` channel; it now uses a unique monkeypatched channel and an isolated bus, and skips without redis.
  - `[medium]` `[patch]` A chart-type switch while the history was still empty lost the kept visible range; the range now stays pending until the new series has rows.
  - `[low]` `[patch]` `PUT /api/coin/{iid}/layout` refuses the coin's own id in `compare.symbols` (422); the `[default]` template still accepts it and it is dropped on load.
  - `[low]` `[patch]` An explicit `null` `price_scale`/`compare` is refused; only an absent key defaults.
  - `[low]` `[patch]` Compare ids have their own `MAX_INSTRUMENT_ID_LENGTH`, mirrored by `validateCompareInput` and pinned by a mirror test.
  - `[low]` `[patch]` A compare add re-checks duplicate, self and the 3-symbol cap inside the state updater (`withCompareAdded`), so a double Enter cannot add twice.
  - `[low]` `[patch]` Removing a compare clears its hidden flag.
  - `[low]` `[patch]` `CompareControl` clears text and refusal on close, and closes when it is disabled (Lines mode).
  - `[low]` `[patch]` The right-scale hit-test is written in container coordinates, with the left scale's width included (left-scale test).
  - `[low]` `[patch]` A drag ending in `pointercancel` reports the auto-scale change, like `pointerup`.
  - `[low]` `[patch]` `chartVarAlpha` parses `#rgb`, `#rrggbb`, `rgb()` and `rgba()`. An unparseable token is a `console.error` with a transparent fallback, never an opaque fill.
  - `[low]` `[patch]` The scale menu's `top` is clamped to the viewport.
  - `[low]` `[patch]` The Baseline's base value finds the first visible bar by time, not by assuming logical index = data index.
  - `[low]` `[patch]` The markets bus refuses a message repeating an id (ledgered, previous list kept). A venue with an empty list is not counted live. An unparseable payload's repr is truncated in the ledger.
  - `[low]` `[patch]` A `Known limit:` was added on the per-tick O(n) compare realignment.
  - `[low]` `[patch]` D-208, the Docs page and `CompareFeed` now say the compare paging deficit can persist, with an upgrade path to page by time.
  - `[low]` `[patch]` The layout 422 test asserts the exact key and the duplicate-specific reason.

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 2, low 10)
- defer: 0
- reject: 6: (high 0, medium 2, low 4)
- addressed_findings:
  - `[medium]` `[patch]` A main series recreated by a type switch (or a return from Lines) was the pane's last series: it drew over the compare and indicator lines, and the right scale took its formatter from an overlay. It is now moved back to index 0 with `setSeriesOrder(0)` (test).
  - `[medium]` `[patch]` A compare whose first visible slot is whitespace is rebased on a later bar than the main series, so its line is offset by the main's move in between. This is now stated in D-207 and on the Docs page's Known limits.
  - `[low]` `[patch]` A `chartType` change in Lines mode re-ran the host swap. That cleared the primitive registries without detaching them, so the effects re-attached duplicates. `mainKind` is now null outside Candles mode (a test, shown to fail without the fix).
  - `[low]` `[patch]` A seed or reset copied a template compare equal to the coin itself into the stored per-coin layout. That coin's own PUT refuses such a layout. `_template` now drops the id from the copy and the template keeps it (a test).
  - `[low]` `[patch]` The held compare live bar could overwrite a closed close when it was older than the compare's newest bar. `alignCompare` now skips it, as the main series' live edge does (a test).
  - `[low]` `[patch]` The scale strip's right-click suppressed the browser menu even with no `onPriceScale` to act on. It now opens only when a handler exists (a test).
  - `[low]` `[patch]` A failed markets refetch kept the earlier, possibly expired suggestions and stale-venue note. Both are now cleared (a test).
  - `[low]` `[patch]` The datalist suggested symbols that were already compared. They are filtered out (a test).
  - `[low]` `[patch]` The Compare placeholder and the test fixtures used `BTC-USD.HYPERLIQUID`, a format no venue uses. Both now use the real `BTC-USD-PERP.HYPERLIQUID`.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST 33-9 smoke command raised `KeyError` on the 503 it calls expected. It now prints the 503 detail.
  - `[low]` `[patch]` Across quote currencies (USDT against USD) the Spread also holds the stablecoin rate. This is now stated in `spreadBps`'s doc, DATA_DICTIONARY §2.17 and the Docs page.
  - `[low]` `[patch]` Heikin Ashi re-seeds when older history pages in. This is now a `Known limit:` in `heikinAshi.ts` with an upgrade path, and is on the Docs page.

## Design Notes

**Why compares ride the panes mechanism:** an `overlay` `IndicatorPaneSpec` already lands on pane 0 on the right scale. It gets a legend row with eye/× (routed through `handleLegendAction`), hide support and data diffing. The library normalises every series on a Percent/IndexedTo100 scale by **its own** first visible close, which is exactly TradingView's compare behaviour (each line starts at 0 % at the left edge). Making compares overlays needs no second legend or registry.

**Why align on main times:** feeding a compare's own times would extend the shared time scale with bars the main series lacks, which moves the main series' logical indices (paging, the visible-bars report). Aligning to the main times keeps one time axis. A venue's missing bar is whitespace, and a compare bar at a time the main lacks is not drawn. The Docs page states this.

**Effective vs stored scale mode:** with a compare present, `effectiveMode = stored === "indexed" ? "indexed" : "percent"`. Only an explicit user pick writes `price_scale.mode`, so removing the compares returns the operator's own mode.

## Verification

**Commands:**
- `cd platform && python3 -m pytest views/tests data_api/tests research/tests tests -q -p no:cacheprovider`: expected all pass with no warnings. Redis-backed `data_api` tests need a throwaway redis on 6379.
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && git diff --stat`: expected `/api/markets` in the schema, stable on a rerun.
- `cd platform/frontend && npm test && npm run lint && npm run build`: expected green.
- `ruff format --check` and `ruff check` on the touched Python: expected clean.

## Auto Run Result

**Status:** done. This was a follow-up review pass on the implemented story.

**Summary:** the follow-up review (Blind Hunter and Edge Case Hunter over `02396e7ee5..HEAD`) found nothing that needed the spec changed. It produced 12 patches:
- **Chart behaviour:**
  - the main series is put back first on its pane after any recreation, which restores the z-order and the scale formatter;
  - a chart-type change in Lines mode no longer duplicates primitives;
  - the compare's held live bar never overwrites a closed close;
  - the scale strip's right-click opens a menu only when a handler exists.
- **Compare picker:** a failed refetch clears the earlier list, symbols already compared are not suggested, and the placeholder shows a real Hyperliquid id.
- **Server:** a seed or reset strips the coin's own id from the template compare it stores.
- **Docs:** three limits are now documented (the compare offset, quote currencies in the Spread, Heikin Ashi re-seeding), and the DEPLOY_CHECKLIST smoke command now handles the 503.

**Files:**
- `platform/frontend/src/components/chart/LightweightChart.tsx`: `mainKind` is null outside Candles, `setSeriesOrder(0)` on recreation, and the scale menu is gated on `onPriceScale`.
- `platform/frontend/src/lib/compare.ts`: skips the stale live bar; the quote-currency note.
- `platform/frontend/src/lib/heikinAshi.ts`: the re-seeding `Known limit:`.
- `platform/frontend/src/components/chart/CompareControl.tsx`: failure clears the list, compared symbols are filtered, the placeholder.
- `platform/data_api/routes/layout.py`: `_template(layouts, instrument_id)` drops the coin's own compare id from the copy.
- Tests:
  - `LightweightChart.test.tsx`: Lines-mode type change, series order, no-handler right-click;
  - `compare.test.ts`: the stale live bar;
  - `CompareControl.test.tsx`: suggestions, failed refetch;
  - `data_api/tests/test_layout.py`: seed and reset;
  - the HL id fixtures in `chartLayout.test.ts` and `ChartPage.test.tsx`.
- Docs: `DATA_INTEGRITY_AUDIT.md` D-207, `DATA_DICTIONARY.md` §2.17, `DEPLOY_CHECKLIST.md` 33-9, and `kbData.ts` `chart-type-scale`.

**Review:** 12 patches applied (2 medium, 10 low), 0 deferred, 6 rejected:
- the shared stale/liveness constant: the existing bus pattern, rejected before;
- the GIL "weak" threading test: it still catches the iteration race;
- a negative monotonic offset in a test: harmless;
- a pick during a forced scale mode (two findings): an explicit pick by design, rejected before;
- an empty markets list wiping the venue: a venue with no markets is truthfully not live, rejected before.

**Verification:**
- Frontend: `npx vitest run` gives 1343 passed. `npm run build` is clean. `npm run lint` shows 0 errors (the same 3 warnings in untouched files).
- `python3 -m pytest views/tests data_api/tests research/tests tests -q`: 2229 passed, 4 skipped, 10 failed.
  - 9 failures were `Connection refused` on redis 6379, which was down during the run. Those 5 files rerun with a throwaway redis: 86 passed, including `test_markets.py` and `test_layout.py`.
  - The 10th is the pre-existing `tests/test_legacy_names.py` failure on `Makefile:219`, which this story does not touch.
- The Lines-mode test was checked to fail with the fix reverted.
- `ruff format --check` and `ruff check` are clean on the touched Python.

**Residual risks:**
- The documented limits remain:
  - D-207 offsets (overlays, and a compare starting later);
  - D-208 compare paging;
  - Heikin Ashi re-seeding on older pages;
  - the Spread including the quote rate.
- A pick of a scale mode while a compare forces Percent still overwrites the stored mode (by design).
- Deferred operator actions are listed in DEPLOY_CHECKLIST 33-9.

