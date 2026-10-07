---
title: 'Story 33.11: The indicators the catalog lacks, candle patterns as markers on the candles, and the dead code removed'
type: 'feature'
created: '2026-10-07'
status: 'done'
baseline_revision: 'f0ee90705991a07effcf3a33122b89d12366327c'
final_revision: '03b61d8c0d71127ac0f3bfcf855414789355e729'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** the picker lacks nine indicators TradingView users reach for first: Supertrend, Parabolic SAR, ADX, Williams %R, Pivot Points, MFI, CMF, Awesome Oscillator and ZigZag. None of them exists in `nautilus_trader.indicators` (verified: `DirectionalMovement` gives smoothed `pos`/`neg` only, and its `value` is never set). `CandlePattern` hits draw as ±100 spikes in a pane instead of markers on the candles. Code with no production caller is still in the repo (`kernel.indicators.liquidity_distance`/`LiquidityDistance`, `views.chart_series.replay_bucket_samples`, `hooks/useErrorLog.resetErrorLog`), and the Docs page, the data dictionary and `views/__init__.py` still describe things that do not exist.

**Approach:**
- Add `kernel/ta.py` with nine streaming `Indicator` subclasses and register them in the picker catalog. Six are native `IndicatorSpec`s. Supertrend, PivotPoints and ZigZag are custom specs, because they need split outputs, a store seed or sparse output.
- Extend the catalog with two added fields: a per-output `plot` hint (`line|steps|points|swing`) and a legend `note`.
- Draw `CandlePattern` as series markers through the existing liquidation-marker plugin. The display is chosen by a view-only style key, so the pane stays available.
- Add a dead-code boundary check over `views/`, `kernel/` and `frontend/src/hooks`, delete what fails it, and make the docs truthful.

## Boundaries & Constraints

**Always:**
- Every `kernel/ta.py` class follows the `kernel/candle_patterns.py` contract:
  - `from nautilus_trader.indicators import Indicator`, with params checked by `PyCondition`/`ValueError` and `super().__init__(params=[...])`;
  - `update_raw` is O(1) with bounded state and sets `has_inputs`/`initialized`; `_reset` clears everything;
  - no module-level mutable state;
  - the module docstring defines every formula;
  - where a bar alone suffices, a `handle_bar` built from real `Bar`s.
- Reuse Nautilus pieces wherever they exist, never re-implement them:
  - `DirectionalMovement(period, MovingAverageType.WILDER)` for ±DM;
  - `AverageTrueRange(period, MovingAverageType.WILDER)` for ADX and Supertrend;
  - `SimpleMovingAverage` for AO;
  - `MovingAverageFactory`/`WilderMovingAverage` for ADX's DX smoothing.
- `kernel/` imports neither `candles` nor `views` (test_boundaries). `PivotPoints.update_raw(high, low, close, session)` takes an opaque integer session key from its caller, and the picker supplies `candles.domain.fold.bucket_start_ms(t, session_seconds)`, the one bucket rule of §2.5. The kernel never restates that formula, and `PivotPoints` has no `handle_bar`.
- Catalog extension is added fields only (AD-D12):
  - `IndicatorCatalogEntry` gains `plot: dict[str, Literal["line","steps","points","swing"]]` (default `{}`; an absent output means `line`) and `note: str | None` (default None). They come from new optional fields of the same names on `IndicatorSpec` and `CustomIndicatorSpec`.
  - Every existing entry serializes unchanged apart from the two keys.
  - `frontend/src/api/schema.ts` is regenerated in the same commit.
- Pattern display lives in the entry's style as `style.value.display`, `"markers" | "pane"`, absent meaning markers. It is not a param, so it never changes `indicator_id`, never refetches, and never reaches the constructor or the Technicals. `preferences.is_valid_style` already accepts string leaves, so the backend validator is unchanged.
- Markers come only from real values the replay served: no marker on a None or gap slot, and none after the Bar Replay cutoff. They attach wherever liquidation markers attach today, through the one plugin (`markersPluginRef`) with one merged array, sorted by time, with unique ids (`pat:<instanceId>:<time>`). The hover tooltip goes through `markerSpecsRef`.
- Pivot levels come only from a session seen from its start. The page's bars before that point read None, never a level from a partial session (DATA-01).
- Prices stay floats only inside the reader's computation; the legend formats through the existing `lib/units.ts` path.
- No new dependency. `nautilus_trader/` and `crates/` are untouched. Warnings are failures (TEST-04).

**Block If:** an AC can only be met by modifying `nautilus_trader/`/`crates/` or by adding a dependency.

**Never:**
- No pre-computed indicator values stored (SIGNAL-01).
- No warm-up prefix added for native entries. Per-page warm-up is the existing behaviour of every native entry, and it is noted for the new ones (D-217).
- No `display` param on `CandlePattern`, and no change to the Technicals' pattern columns.
- No "Auto" pivot session, no pivot kinds beyond the three, and no ZigZag in the signal table or as an alert-worthy series beyond what the generic catalog already gives.
- No allowlist in the dead-code check. Code that fails it is deleted, or it gains a real production caller.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Pivot, store-seeded | 1m page starting 10:00 on day S0; the store covers S0−1 from its 00:00 | S0's bars carry levels from S0−1's H/L/C (stored aggregate), and S1's from S0 (the stored prefix [S0 00:00, 10:00) plus the page) | — |
| Pivot, uncovered | no `candles_dir`, or a store starting after S0−1, or a sub-minute chart | page bars before the first session boundary *and* the whole next session read None. Feeding starts at the first boundary in the page, so the second full session onward is correct | not an error |
| Pivot, bad width | session `D` on a 1W chart | the entry's `errors` message `"pivot session D is narrower than the 604800 s bar"`; other entries still served | `ValueError` from replay |
| Pivot kinds | H=110, L=90, C=100 | standard: PP=100, R1=110, S1=90, R2=120, S2=80, R3=130, S3=70. fibonacci: R1=107.64, R2=112.36, R3=120. camarilla: R1=101.8333…, R4=111; r4/s4 None for the other two kinds | — |
| ZigZag swing | 5 %, highs 100→110 then lows down to 104.4 | a pivot is confirmed at the 110 bar (value 110 there); the last leg's end (the running extreme) is emitted at its bar and moves while unconfirmed | — |
| ZigZag plot | sparse values | the frontend drops whitespace for a `swing` output, so one line joins the swing points | — |
| Supertrend flip | direction −1 → +1 at bar i | `down` has values through i−1 and `up` from i; each is None elsewhere | — |
| Marker | `EVENING_STAR` value −100 at t | `{shape:"arrowDown", position:"aboveBar", id:"pat:<id>:t"}`, tooltip `["Evening star", "bearish"]` | — |
| Marker, non-directional | `DOJI` +100 | `{shape:"circle", position:"aboveBar"}`, tooltip says "neutral" (`NON_DIRECTIONAL`) | — |
| Display pane | `style.value.display = "pane"` | the ±100 histogram pane, exactly as today; no markers | — |
| Unknown display | `style.value.display = "x"` | treated as markers | — |
| Legend in markers mode | entry hidden by the eye | markers removed; the row stays with gear/eye/×; the readout at a hit bar prints the pattern name, `—` elsewhere | — |
| Dead check | a new public function with only test importers | `test_boundaries` fails, naming `path:line -> module.name` | — |

</intent-contract>

## Code Map

All paths are under `platform/`.

- `kernel/indicators.py` (`RollingZScore` ~286, `liquidity_distance`/`LiquidityDistance` ~955-1000) and `kernel/candle_patterns.py` (`CandlePattern` 521-614, `PatternName`, `NON_DIRECTIONAL` 517) are the custom-`Indicator` precedents. Their tests are `kernel/tests/test_candle_patterns.py`, which uses a real `Bar`.
- `views/indicator_picker.py`:
  - `IndicatorSpec` 103-117; `NATIVE_INDICATOR_CATALOG`, with CandlePattern at 352; `replay_native` 422; `native_catalog_json` ~470.
  - `ReplayWindow` 533; `CustomIndicatorSpec` 555; `custom_catalog_json` 605; `_store_prefix` 705 is the store-coverage precedent.
  - `merged_catalog` ~1439; `check_params` 1475; `replay_entry` 1579; `values_by_time` 1608.
- `candles/application/queries.py`: `open_store`, `oldest_t` 170, `flow_totals` 297. `views/chart_series.py`: `stored_bar` 466, `indicator_values_page` 706, `replay_bucket_samples` 647 (dead). `candles/domain/fold.py`: `bucket_start_ms` 135, `WEEK_SECONDS`.
- `data_api/routes/indicators.py`: `IndicatorCatalogEntry` 86-106. The schema is regenerated through `data_api/export_openapi.py` and `npm run codegen`.
- `research/strategies/indicator_signal_strategy.py`: docstring table 19-46, `_Signal` 198, `_signed`/`_oscillator` 229-247, `_SIGNALS` 306-404. `research/README.md` 317/334 says "25 signals".
- Frontend (`frontend/src/`):
  - `pages/ChartPage.tsx`: `legendTitle` 203, the picker panes memo 835-875 (CandlePattern spikes at 856), Style rows ~1052, `liquidationMarkers` prop ~1977.
  - `components/chart/LightweightChart.tsx`:
    - `IndicatorPaneSpec` ~150-190, the series add ~1692-1712;
    - the markers effect 2053-2064 (`markersPluginRef`, `markerSpecsRef`, `markerTip` ~1044);
    - `showMarkerTip` 2393.
  - `components/chart/LiquidationMarkers.ts`: `MarkerSpec` 35.
  - `hooks/usePickerIndicatorValues.ts`; `components/chart/IndicatorSettingsDialog.tsx`.
  - `hooks/useErrorLog.ts` 80 and `components/ErrorBar.test.tsx`.
- `tests/test_boundaries.py`: `KERNEL_MODULES` 916-936; `_IMPORTS`/`_MODULES`/`_site`/`_not_test`; precedent `test_every_listed_views_query_service_is_still_used` 1520. `tests/_source_tree.py` has `python_modules`, `imports_of` and `grep_hits`.
- Docs:
  - `frontend/src/pages/docs/data.ts`: the Indicator Reference. `book_features` 258-271 names `liquidity_distance`; line 46 says "the dashboard's per-coin chart".
  - `frontend/src/pages/docs/kbData.ts`: `data-dictionary` 85-106 is stale and dYdX-only; `make remote-tui` ~123 does not exist.
  - `docs/DATA_DICTIONARY.md`: §2.4 2787, §2.6 2882, §2.7 ~2950, §2.18 is the last section.
  - `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md`: §2, 178-231.
  - `docs/DATA_INTEGRITY_AUDIT.md`: the next row is D-214.
  - `docs/DEPLOY_CHECKLIST.md`: the 33-10 section is the format.
  - `views/__init__.py`: the docstring.
  - `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`: the AD-D3 kernel module list.
  - `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md`: the "Candlestick pattern auto-recognition" exclusion.

## Tasks & Acceptance

**Execution:**
- [x] `kernel/ta.py` (new) + `kernel/tests/test_ta.py`, holding the nine classes:
  - `Supertrend(period=10, multiplier=3.0)`: `value`, `direction` (±1). Bands are hl2 ± m·ATR with the standard final-band carry rule; it starts at direction +1.
  - `ParabolicSAR(step=0.02, max_step=0.2)`: `value`, Wilder's rule, seeded at the 2nd bar.
  - `AverageDirectionalIndex(period=14)`: `adx`, `plus_di`, `minus_di`.
    - +DI = 100·pos/ATR and −DI = 100·neg/ATR, from `DirectionalMovement`/`AverageTrueRange` (WILDER).
    - DX = 100·|+DI − −DI|/(+DI + −DI), with 0 when the sum is 0.
    - ADX is the Wilder MA of DX.
    - It is initialized when that MA is.
  - `WilliamsPercentR(period=14)`: `value` = −100·(HH − C)/(HH − LL), with −50 when HH = LL.
  - `PivotPoints(kind="standard"|"fibonacci"|"camarilla")`: `pp, r1, r2, r3, r4, s1, s2, s3, s4`, where r4/s4 are None for the non-camarilla kinds (never NaN), exposed through a `levels()` dict. `update_raw(high, low, close, session)`. It is initialized after the first session change, and the levels come from the completed session.
  - `MoneyFlowIndex(period=14)`: on a typical price, raw flow = tp·v; flow is positive or negative by tp change, and 50 when both sums are 0.
  - `ChaikinMoneyFlow(period=20)`: CLV·v summed over a rolling window, divided by Σv. CLV is 0 when H = L, and the result is 0 when Σv = 0.
  - `AwesomeOscillator(fast=5, slow=34)`: SMA(hl2, fast) − SMA(hl2, slow).
  - `ZigZag(deviation_pct=5.0)`, from highs and lows:
    - It tracks the running extreme. A reversal of ≥ deviation % from it confirms that extreme as a pivot and exposes `pivot_price`, `pivot_bar` (an update count) and `confirmed` for that update.
    - `extreme_price`/`extreme_bar` are the repainting last leg.
    - On one bar, extension is checked before reversal. Before the first pivot, both the high and the low extremes are tracked.
  - Tests, per class:
    - a hand-computed fixture: a short series whose expected values are written out step by step in a comment, using Nautilus's seeding, cross-checked by a naive batch computation in the test;
    - param validation, reset, and constant state over 10,000 bars;
    - `handle_bar` on a real `Bar`.
  - The pivot table is the matrix row. Formulas are cited to their published definitions (Wilder 1978; Chaikin; Williams; Bill Williams' AO; StockCharts ChartSchool). No reference values are quoted that the implementer cannot reproduce.
- [x] `tests/test_boundaries.py`: add `"ta"` to `KERNEL_MODULES`, plus the dead-code check (see Design Notes) and its "catches each form" self-test.
- [x] `views/indicator_picker.py` + `views/tests/test_indicator_picker_{native,custom}.py`:
  - `IndicatorSpec` and `CustomIndicatorSpec` gain `plot: dict[str, str] = {}` and `note: str | None = None`, serialized by both `*_catalog_json` functions.
  - Native entries:
    - `ParabolicSAR` (overlay, plot `points`);
    - `AverageDirectionalIndex` (oscillator);
    - `WilliamsPercentR` (oscillator);
    - `MoneyFlowIndex` (oscillator, feed h/l/c/v);
    - `ChaikinMoneyFlow` (oscillator);
    - `AwesomeOscillator` (histogram, feed h/l).
  - Custom entries:
    - `Supertrend`: overlay, outputs `up`/`down` split by direction.
    - `PivotPoints`: overlay, params `kind` and `session` with `D|W` choices, all nine outputs plotted `steps`. Seeded through `queries` (see Design Notes); `check_params` refuses an unknown kind or session.
    - `ZigZag`: overlay, output `value` with plot `swing` and note `"repaints last leg"`. It emits each confirmed pivot at its bar and the final extreme at its bar, and None elsewhere.
  - Tests: the matrix rows for pivot, ZigZag and Supertrend, every new entry replays with its defaults (the existing parametrized tests pick them up), and the `plot`/`note` serialization.
- [x] `candles/application/queries.py` (+ test): `session_hlc(db, iid, bar_seconds, start_ms, end_ms) -> tuple[float, float, float] | None` gives the max h, the min l and the last traded bar's c over traded bars. One SQL aggregate, bounded by its range.
- [x] `data_api/routes/indicators.py` + `frontend/src/api/schema.ts` (regenerated): `plot` and `note` on `IndicatorCatalogEntry`.
- [x] `research/strategies/indicator_signal_strategy.py` + new `research/tests/test_indicator_signal_strategy_ta.py`:
  - The new signals:
    - `supertrend`: the sign of `direction`;
    - `parabolic_sar`: the sign of close − value;
    - `adx`: the sign of +DI − −DI when adx ≥ `adx_min` (default 25), else 0;
    - `mfi`: `_oscillator`, 20/50/80;
    - `cmf`: the sign;
    - `awesome_oscillator`: the sign.
  - Update the docstring table and the README count.
  - Tests: each rule's direction on a hand-built rising and falling series, and the param checks.
- [x] `frontend/src/components/chart/LightweightChart.tsx` (+ test):
  - `IndicatorPaneSpec` gains `plot?: "line"|"steps"|"points"|"swing"`: steps uses `lineType` WithSteps, points uses `lineVisible:false` + `pointMarkersVisible:true`, and swing filters whitespace out of the data.
  - It also gains `markersOnly?: boolean`: an overlay series with no line, crosshair marker, last value or price line, on its own hidden price scale with `autoscaleInfoProvider` returning null, so ±100 never moves the price axis. It hosts the legend row only.
  - New prop `patternMarkers: MarkerSpec[]`, merged with `liquidationMarkers` into the one plugin; `markerSpecsRef` holds both.
- [x] `frontend/src/lib/patternMarkers.ts` (new) + test: `buildPatternMarkers(entryId, patternName, data, colors)` turns ±100 into arrowUp/belowBar, arrowDown/aboveBar or circle/aboveBar for `NON_DIRECTIONAL`. It sets a tooltip of [human name, bullish|bearish|neutral] and skips 0, None and whitespace. `NON_DIRECTIONAL` is mirrored from Python with a mirror test in `views/tests`.
- [x] `frontend/src/pages/ChartPage.tsx` (+ `ChartPage.test.tsx`):
  - In the panes memo, a CandlePattern entry with display markers gets the `markersOnly` spec. Its `format` prints the pattern name at a hit slot and `—` elsewhere.
  - Its markers are built from the trimmed (replay-cut) data and are hidden when the entry is hidden.
  - Pane display is unchanged.
  - `plot` and `note` are carried from the catalog; `legendTitle` appends ` · <note>`.
  - The CandlePattern Style section gains a Display select (Markers/Pane) writing `style.value.display`.
  - Tests:
    - marker placement for bullish, bearish and neutral;
    - the pane option;
    - the replay cutoff;
    - the eye;
    - a ZigZag swing spec and the legend note.
- [x] Dead code:
  - Delete `kernel.indicators.liquidity_distance`/`LiquidityDistance` and `views.chart_series.replay_bucket_samples`, with their tests.
  - Delete `hooks/useErrorLog.resetErrorLog`. `ErrorBar.test.tsx` isolates the module with `vi.resetModules()` plus a dynamic import.
  - Delete anything else the new check finds, or give it a real caller.
- [x] Docs:
  - `data.ts`: add the nine indicators and the pattern-marker display. Remove the `liquidity_distance` text, retitle the `book_features` entry, and fix line 46.
  - `kbData.ts`:
    - rewrite `data-dictionary` to the current multi-venue stored types;
    - replace `make remote-tui` with what exists (`make tui`);
    - add the new indicators to the chart entry with the markers and the ZigZag repaint note.
  - `docs/DATA_DICTIONARY.md`:
    - remove §2.4, leaving a one-line pointer in §2.14;
    - rewrite §2.6 as CancelPressure only;
    - fix §2.7's `replay_bucket_samples` line;
    - add a new §2.19 for the `kernel/ta.py` series, `plot`/`note`, `style.value.display` and the pivot seed rule.
  - `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` §2: a "custom, no built-in" `kernel/ta.py` table, with the title updated.
  - `docs/DATA_INTEGRITY_AUDIT.md`:
    - D-214: pivot levels from a partial session (guarded: covered-seed rule).
    - D-215: ZigZag's last leg repaints (Known limit, legend note).
    - D-216: pattern markers under Heikin Ashi (guarded: computed on real OHLC, AD-F6).
    - D-217: per-page warm-up of the new native entries (Known limit, existing behaviour).
  - `docs/DEPLOY_CHECKLIST.md`: a 33-11 section (rebuild `data_api`; no migration; smoke check each indicator and the marker toggle).
  - `views/__init__.py`: fix the docstring.
  - The AD-D3 list in the spine gains `ta`, and the spec exclusion is amended in place `[amended 2026-10-07: Story 33.11]`.

**Acceptance Criteria:**
- Given the picker, when the operator adds any of the nine indicators, then it draws with its defaults (overlay or its own pane per its panel), its gear edits its params, and it appears as a Technicals column and an alert source through the shared catalog.
- Given a CandlePattern entry, when its display is markers (the default, including entries saved before this story), then each hit bar carries the arrow or circle and the name on hover, the price axis is unchanged, and switching to Pane restores the ±100 pane.
- Given `cd platform && python3 -m pytest tests/test_boundaries.py`, when any public function or class in `views/`/`kernel/`, or any exported function in `frontend/src/hooks`, has no non-test reference, then it fails naming the site; on this story's tree it passes.
- Given the Docs page and DATA_DICTIONARY, when read after this story, then no entry names a function, command or chart that does not exist.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 0, medium 6, low 7)
- defer: 0
- reject: 5: (high 0, medium 0, low 5)
- addressed_findings:
  - `[medium]` `[patch]` A weekly pivot on a 1W chart was refused: the alignment check assumed epoch-anchored bars. It now compares against the bar width's own (Monday) anchor; W-on-1W is tested, and D-on-1W is still refused.
  - `[medium]` `[patch]` A skipped or out-of-order session gave the levels of two sessions back:
    - `PivotPoints.update_raw` refuses a lower session key.
    - The picker's `_feed_pivots` resets when the next session is not the one directly after the last, so the levels read None until a whole session is seen again.
    - Tests cover the gap, the refused key, and a covered-but-untraded previous session. D-214 and §2.19 are updated.
  - `[medium]` `[patch]` `_RollingSum` (MFI/CMF) drifted without bound over long runs. It now does compensated add/remove plus an exact `fsum` re-sum every `period` pushes, and the reviewer's 200k-push scenario is tested.
  - `[medium]` `[patch]` Supertrend started up (+1), where TradingView's `ta.supertrend` starts down. It now starts at −1 on the upper band. A `Known limit:` covers the Wilder ATR first-input seed against Pine's SMA-seeded rma, and the docs and fixtures are updated.
  - `[medium]` `[patch]` ZigZag emitted each older page's unconfirmed tip, which the swing line then joined to the newer page as a fabricated swing.
    - The tip is now emitted only when the page reaches the store's newest bar, with one bar of slack for closed-bar readers. With no store or no stored width, only confirmed pivots are drawn (`Known limit:`).
    - D-215 and D-217 now say that Supertrend and ZigZag warm up per page and that the Technicals and alerts read ZigZag's repainting tip.
  - `[medium]` `[patch]` The dead-code check counted a docstring mention as a caller, and the hooks half counted bare word matches.
    - Docstrings and bare string statements no longer count.
    - A hooks export counts only through a named import or re-export from its file, or a use in its own file.
    - The self-tests are extended. No new offenders.
  - `[low]` `[patch]` Pattern markers ignored the liquidation markers' bar-spacing gate. One shared `markersHiddenAt` now gates both.
  - `[low]` `[patch]` The `_RollingExtreme` docstring said "strictly ordered"; ties are kept, so it now says non-strictly.
  - `[low]` `[patch]` Input validation was inconsistent across `kernel/ta.py`. Every high/low class now uses one `_check_bar`: non-finite input, low > high, and negative volume for MFI/CMF are all refused.
  - `[low]` `[patch]` The signal table cast integer params inconsistently. The mfi/cmf/awesome_oscillator params now go through `int(...)`, tested with whole floats.
  - `[low]` `[patch]` The Docs did not say what pattern markers do in Lines mode. They now say the markers belong to the candle series, so in Lines mode only the legend readout names a hit.
  - `[low]` `[patch]` The pivot seed paths lacked tests; they are added with the two pivot fixes above.

### 2026-10-07 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 0
- reject: 10: (high 0, medium 0, low 10)
- addressed_findings:
  - `[medium]` `[patch]` ZigZag drew a pivot on a page's first bar. That bar is an extreme only because the page starts there, so the swing line joined the older page's last pivot to a turn the market never made. `_zigzag_replay` no longer draws a pivot on the page's first fed bar. The fixtures are updated, a test shows the same low drawn once a bar precedes it, and D-217, §2.19 and the Docs page say so.
  - `[low]` `[patch]` A stale CandlePattern series (one with no saved entry) labelled its hits with the catalog's default pattern. `patternOf` now reads `pattern=` from the instance id first, and a ChartPage test covers it.
  - `[low]` `[patch]` `ZigZag` accepted a zero or negative low, where a percent reversal has no meaning. It is now refused before any state changes, and tested.
  - `[low]` `[patch]` `ParabolicSAR` accepted `max_step > 1`, which moves the SAR past its extreme point and leaves it pinned by the clamp. It is now refused, and the native check inherits that through the constructor. Tested.
  - `[low]` `[patch]` The swing plot's whitespace filter kept `NaN`/`Infinity` as joined points. It now uses `Number.isFinite`.
  - `[low]` `[patch]` The hooks dead-code rule reported a function read through `import * as ns` (`ns.fn()`) as dead. Namespace reads now count, and the self-test is extended.

## Design Notes

- **Dead-code rule.** "Has a non-test importer" is read as "has a non-test reference outside its own definition". Such a reference is an import or `module.attr` from a non-test module, a use elsewhere in its own module, or a string path (`"kernel.ta:X"`). A literal "imported by another module" rule would force about 140 renames of live helpers that are exported only so tests can reach them. That is not dead code, and it is not what DESIGN-03 targets.
  - Scope: top-level public functions and classes in non-test `views/*.py` and `kernel/*.py` modules; every such module must have a non-test importer; and exported functions in `frontend/src/hooks/*.ts(x)`, where `export function`/`export const X = (…) =>` counts and types and constants do not. Each hooks file must be imported by some non-test file.
  - Python is checked through `_IMPORTS` plus an AST name walk; TS by regex over `frontend/src` excluding `*.test.*`.
- **Pivot seed** (`_pivot_replay`): `S = bucket_start_ms(first.t, session_s)` and `P = bucket_start_ms(S − 1, session_s)`.
  - If `stored_bar(bar_seconds)` exists, the store exists and `oldest_t(…, traded_only=False) ≤ P`: feed `session_hlc([P, S))` with session P, then `session_hlc([S, first.t))` with session S when it is non-empty, then the page.
  - Otherwise, the page bars before the first session boundary are not fed (they read None), and feeding starts at that boundary.
  - A session narrower than the bar, or one not dividing into whole bars, raises `ValueError`.
- **Why `style.value.display`:** a param would change `indicator_id`, reach `CandlePattern.__init__` and appear in the Technicals; style is per-view, persisted, and already validated.
- **The swing plot** removes whitespace client-side only for `plot: swing`. Gaps are not drawn as a break there, and a ZigZag line spanning a data gap is the TradingView behaviour.

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests views/tests candles/tests research/tests data_api/tests tests/test_boundaries.py -q` -- expected: all pass, no warnings
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass
- `cd platform && ruff check kernel views candles research data_api tests && ruff format --check kernel views candles research data_api tests && mypy kernel/ta.py views/indicator_picker.py` -- expected: clean (pre-existing failures named, not introduced)

## Auto Run Result

**Summary:** this was a follow-up review of Story 33.11. The story added nine `kernel/ta.py` indicators to the picker, drew CandlePattern hits as markers on the candles, and removed dead code behind a new boundary check. The previous pass had recommended a follow-up because its fixes changed behaviour across the kernel, views and frontend.

Two fresh reviewers (Blind Hunter, Edge Case Hunter) reviewed the whole diff from `f0ee907059` and raised 17 findings, 16 after deduplication. 6 were patched and 10 rejected; the Review Triage Log lists the follow-up pass.

**Files changed in this pass** (under `platform/`):
- `kernel/ta.py`: refuses `ParabolicSAR` with `max_step > 1` and `ZigZag` with a non-positive low; the docstrings say so.
- `kernel/tests/test_ta.py`: tests for both refusals.
- `views/indicator_picker.py`: `_zigzag_replay` no longer draws a pivot on the page's first fed bar.
- `views/tests/test_indicator_picker_custom.py`: the fixtures updated, plus a first-bar test.
- `frontend/src/pages/ChartPage.tsx` (+test): a stale pattern series takes its pattern from its instance id.
- `frontend/src/components/chart/LightweightChart.tsx`: the swing filter keeps finite values only.
- `tests/test_boundaries.py`: the hooks rule counts `import * as` namespace reads, with an extended self-test.
- Docs: D-217 in `docs/DATA_INTEGRITY_AUDIT.md`, §2.19 of `docs/DATA_DICTIONARY.md`, and the ZigZag notes on the Docs page (`frontend/src/pages/docs/data.ts`, `kbData.ts`).

**Rejected (10):**
- Supertrend's seeded first direction and the SAR's 2-bar re-seed after a strategy reset. These are TradingView-matching seeds and the existing reset behaviour.
- The `test_ta.py` batch references not being external. This was rejected before: there is no network, and the spec asks for hand-computed fixtures.
- The deleted `replay_bucket_samples` verification test. Its subject was dead code.
- PivotPoints `D` failing on a 1W chart. The spec's matrix sets that error.
- Three gaps in the dead-check, all accepted heuristics:
  - recursion within a hooks file;
  - string constants counting as references (the design notes count string paths);
  - dead code calling dead code (rejected before).
- ZigZag and Supertrend reading None as Technicals columns or alert inputs. This is spec-sanctioned and documented in D-215/§2.19.
- Markers drawing nowhere in Lines mode. This was already stated on the Docs page.

**Verification:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. kernel/tests views/tests candles/tests research/tests data_api/tests tests/test_boundaries.py verification/tests/test_reference_signals.py -q`: 2897 passed, 3 skipped (jupytext is absent), no warnings. A throwaway redis was run for the data_api tests.
- vitest: 1507 passed (73 files).
- `npm run lint`: only the 3 pre-existing warnings.
- `npm run build`: ok.
- `ruff check`/`ruff format --check` (0.15.16) on the touched Python: clean.
- `mypy kernel/ta.py`: clean.

**Follow-up review recommended:** false. The fixes are local: one medium change to how ZigZag pages are drawn, plus five low guards. Each is tested.

**Residual risks:**
- ZigZag's first pivot on a page can still differ from a longer replay's (D-217 `Known limit:`; the upgrade path is a stored warm-up prefix).
- No live browser walkthrough was run; DEPLOY_CHECKLIST 33-11 covers the smoke check.
