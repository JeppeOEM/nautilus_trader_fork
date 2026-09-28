---
title: 'Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: '6c54d98312742802554ca03fe7cee4ffb64da90b'
final_revision: 'f0069697f922b86af1f08838956efad3c61a37a3'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Candlestick patterns exist only in a legacy notebook. It installs TA-Lib and `pandas_ta` with `%pip` at runtime, reads the retired `custom_dydx_minute_bar` directory and resamples bars itself. Nothing on the chart, in the screener or in a strategy can use a pattern.

**Approach:**
- One pure streaming `CandlePattern(Indicator)` lives in `kernel/candle_patterns.py` and covers 23 patterns, with `CandlePatternSet` running all of them over one stream.
- It is registered in views' native indicator catalog, which serves both the chart picker and the screener's Technicals columns. The catalog now also carries the allowed values of enum params, so the picker renders a dropdown for them.
- A new scanner notebook `06_candlestick_scanner` runs on the candle store's bars through a thin `research/application/patterns.py`, with forward returns computed in `research/domain/events.py`.
- The legacy notebook is deleted.
- The detector is checked against TA-Lib once, in a throwaway scratch venv.

## Boundaries & Constraints

**Always:**
- **Detector contract.**
  - `value` is +100 for bullish, −100 for bearish and 0 for none. `DOJI` is non-directional and gives +100 when it fires, as TA-Lib's `CDLDOJI` does.
  - Directions:
    - `DRAGONFLY_DOJI` is +100 and `GRAVESTONE_DOJI` is −100. This follows Nison; TA-Lib gives both +100, and the difference is documented.
    - `MARUBOZU` and `SPINNING_TOP` take the sign of the bar's colour.
    - `HAMMER`, `INVERTED_HAMMER`, `PIERCING`, `TWEEZER_BOTTOM`, `MORNING_STAR`, `THREE_WHITE_SOLDIERS` and `THREE_INSIDE_UP` are +100. Their mirrors are −100.
    - `ENGULFING`, `HARAMI` and `HARAMI_CROSS` are ±100 by direction.
  - **State is O(1).** The detector keeps at most three bar records. Each record carries the prior-trend run lengths as they stood before that bar. Beyond that it keeps two run counters and a warm-up counter, each capped. There is no list that grows.
  - **Warm-up.** `initialized` becomes true only once the pattern's bar count, plus `trend_bars + 1` for trend patterns, has been fed. Before that, the replay shows None rather than 0.
  - **Flat bars.** A bar with `high == low` matches no pattern.
  - **Thresholds.** These are explicit constructor keywords, validated in a frozen `Thresholds`:
    - `body_ratio=0.3`
    - `shadow_ratio=2.0`
    - `doji_body_ratio=0.1`
    - `marubozu_shadow_ratio=0.05`
    - `tweezer_ratio=0.05`
    - `trend_bars=3`, which must be ≥ 1 so that mirror pairs cannot both fire
    - `star_gap=True`
  - **Prior trend.** A downtrend before bar k means each of the `trend_bars` bars before k closed below its predecessor's close. An uptrend is the mirror.
  - **Documentation.** The module docstring defines each pattern in words and by inequality, and gives the TA-Lib `CDL*` name map, with "none" for the tweezers.
- **Catalog entry.** Add `INDICATOR_CATALOG["CandlePattern"]` with `feed` = OHLC, `outputs=("value",)`, `panel="histogram"`, JSON-safe defaults (`pattern: "ENGULFING"` plus every threshold) and `enum_params={"pattern": PatternName}`.
  - `native_catalog_json` adds `choices: {param: [enum names]}` for every enum param. The existing `ma_type`/`price_type` params gain dropdowns this way too.
  - `IndicatorCatalogEntry` gains `choices: dict[str, list[str]] = {}`, and `openapi.json` and `schema.ts` are regenerated to match.
- **Closed bars in Technicals.** `views.ranking_columns.technicals_values` evaluates every column on the latest *closed* bar. It drops a newest candle whose bucket has not closed at `now_ns` before the replay. This applies to every column alike, so patterns get no special case. The docstring states it.
- **Picker dropdown.** The frontend picker renders a `<select>` for any param that has `choices`, and a value outside `choices` is invalid. The chart's histogram mapping is unchanged. `ChartPage.tsx` carries this comment:

  > Known limit: pattern hits are ±100 histogram spikes, not on-candle markers. Upgrade path: lightweight-charts `createSeriesMarkers`.
- **Scanner reads.** The scanner reads bars only through `MarketFrames.bars`, span by span over `bar_coverage`, and only for sizes in `candles.domain.fold.BAR_SECONDS`. A timeframe outside that set is skipped with a printed line.
  - The pattern set and the EMA reset at every hole, meaning a next bar that is not the adjacent bucket.
  - The EMA is `nautilus_trader.indicators.ExponentialMovingAverage`. It is NaN until initialized, and a NaN EMA fails the `above`/`below` filter.
  - A forward return that crosses a hole or runs past the window is NaN, never filled.
- **Notebook cells** hold only calls, prints and plotly. They contain no `np.`, `.mean(`, `sum(`, `.resample(` or `talib`. Constants come through `_params.setting`, and plotly is used without `ipywidgets`.
- **File standards.** LGPL header, full type hints, ruff at line length 100, one import per line, functions under ~30 lines.

**Block If:**
- `06_candlestick_scanner` cannot finish under 60 s on the fixture.

**Never:**
- No TA-Lib, `pandas_ta`, scipy or other new dependency in the repo. The parity check runs only in a scratch venv outside the repo, and its script is not committed.
- No edit under `nautilus_trader/` or `crates/`.
- No write to `sprint-status.yaml`, and no `awaiting-operator` status or `operator_actions:` (OPS-01).
- No client-side re-sort of rankings rows (platform rule). "Sorting groups the hits" is met through the existing filter panel (`tech:CandlePattern.value > 0` / `< 0`), as the Design Notes explain.
- No change to the `chart_indicators.toml` or `screener_columns.toml` key sets, and no special case for patterns in `replay_native`, `values_by_time` or the routes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bullish engulfing | black `(10,10.2,8.9,9)` then white `(8.9,10.6,8.8,10.5)` | `value == 100` | — |
| Near-miss engulfing | the white body closes at 9.9 (< prior open 10) | 0 | — |
| Hammer, uptrend | hammer shape after 3 rising closes | HAMMER 0, HANGING_MAN −100 | — |
| Flat bar | `o=h=l=c` | every pattern 0 | — |
| Warm-up | first bar fed to a 3-bar pattern | `initialized` False; `replay_native` gives None | — |
| Bad threshold | `trend_bars=0`, `body_ratio<=0` or `>=1`, unknown pattern name | — | ValueError / KeyError at construction |
| Replay by name | `replay_native(candles, "CandlePattern", {"pattern": "HAMMER"})` | a list of None/±100/0 aligned with the candles | — |
| Forming bar | the newest store bucket `t + bar_ms > now` | the Technicals value is the previous closed bar's | — |
| Forward return | `closes=[1,2,NaN,4]`, hit 0, horizons (1,2,3) | `[1.0, NaN, 3.0]` | a hit index out of range → ValueError |
| Scanner, no hits | a window with no pattern | an empty hits table with columns, and one printed sentence instead of a chart | — |

</intent-contract>

## Code Map

- `platform/kernel/indicators.py:126-240` -- the `Indicator` subclass precedent: `super().__init__(params=[...])`, `_set_has_inputs`, `_set_initialized`, `_reset`, `handle_bar`.
- `platform/views/indicator_picker.py:77-400` -- `IndicatorSpec`, `INDICATOR_CATALOG`, `replay_native`, `_resolve_enum_params` (`enum_type[value]` by name), `native_catalog_json`, `merged_catalog`. The module docstring says native covers only `nautilus_trader.indicators`, so amend it to cover kernel OHLC indicators too.
- `platform/views/ranking_columns.py:136-235` -- `_recent_candles` and `technicals_values`, which add the closed-bar cut.
- `platform/data_api/routes/indicators.py:75-87` -- `IndicatorCatalogEntry`. `data_api/export_openapi.py` regenerates `frontend/openapi.json`, and `npm run codegen` regenerates `schema.ts`. `data_api/tests/test_app_frontend.py:103` pins them.
- `platform/data_api/tests/test_screener_columns.py` -- the `_client` and `_seed_recent_minutes` fixtures and the chart-vs-technicals cross-check pattern.
- `platform/views/tests/test_indicator_picker_native.py` -- the native replay tests.
- `platform/frontend/src/components/chart/IndicatorPicker.tsx:203-255` (`IndicatorEntryRow`, free-text inputs) and `paramCoercion.ts` (`isValidParamText`).
- `platform/frontend/src/pages/ChartPage.tsx:280` -- the histogram kind mapping, where the Known limit goes. `RankingsPage.tsx` reuses the picker. Tests: `ChartPage.test.tsx`, `RankingsPage.test.tsx`, `technicals.test.ts`, `components/chart/paramCoercion.test.ts`.
- `platform/research/application/frames.py:193-287` -- `bars` and `bar_coverage`. `research/application/aligned.py:83-104` is the span-by-span read precedent.
- `platform/research/notebooks/05_monte_carlo.py` and `_params.py` -- the notebook and Parameters-cell style.
- `platform/research/tests/test_notebooks.py` -- `NOTEBOOK_ENV` and `LEGACY_NOTEBOOKS_UNTIL`, whose entry is removed. `test_notebook_monte_carlo.py` is the namespace-test precedent. `test_research_reads.py:78` holds the legacy exemption, which is removed.
- `platform/tests/test_boundaries.py:805` -- `KERNEL_MODULES`, which gains `candle_patterns`.
- Docs to amend:
  - DDD spine: the AD-D3 kernel list (:223), the kernel row (:160), the research row (:157) and the tree (:415).
  - `epics.md:292` (MR5).
  - `platform/ARCHITECTURE.md:~307-390`.
  - `platform/research/README.md:12-21`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/candle_patterns.py` -- Build the detector:
  - `PatternName(StrEnum)`, 23 members, each value equal to its name.
  - `Thresholds` (frozen, validated) and `_Bar` (o, h, l, c, rising_before, falling_before).
  - `CandlePattern(Indicator)` with `__init__(pattern: PatternName | str, *, body_ratio=..., ...)`, `update_raw(open, high, low, close)`, `handle_bar`, `value: int` and `_reset`.
  - One `_<name>` detector function per pattern, dispatched through a dict. Shape helpers: `_small`, `_doji`, `_hammer_shape`, `_inverted_shape`, `_harami_inside`.
  - `CandlePatternSet(thresholds)` with `update_raw`, `handle_bar`, `reset` and `fired() -> list[tuple[PatternName, int]]`.
  - The module docstring holds the definitions and the TA-Lib map.
- [x] `platform/kernel/tests/test_candle_patterns.py` -- Cover the detector:
  - per pattern: a hand-drawn bullish case, a bearish case and a near-miss negative case, where the pattern has them;
  - the rows of the matrix;
  - the mirror property: over seeded random walks of 2000 bars, no mirror pair fires on the same bar, and every pattern fires at least once over its random-walk or hand-drawn cases;
  - O(1) state: the size of `vars()` and `len(_bars) <= 3` are unchanged after 10 000 bars;
  - `CandlePatternSet` equals the individual detectors on the same stream.
- [x] `platform/tests/test_boundaries.py` -- Add `"candle_patterns"` to `KERNEL_MODULES`.
- [x] `platform/views/indicator_picker.py` -- Register the catalog entry, add `choices` to `native_catalog_json`, and amend the module docstring.
- [x] `platform/views/ranking_columns.py` -- Add the closed-bar cut in `technicals_values`, with the docstring update.
- [x] `platform/views/tests/test_indicator_picker_native.py` -- Test `replay_native("CandlePattern", ...)` against direct `CandlePattern` values, the string-to-enum round trip, and `choices` in the catalog JSON.
- [x] `platform/views/tests/` (the ranking_columns tests, or a new `test_ranking_columns_closed_bar.py`) -- Test that the forming bucket is excluded while a closed one is used.
- [x] `platform/data_api/routes/indicators.py` -- Add the `choices` field. Then regenerate `frontend/openapi.json` (`PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json`) and `schema.ts` (`npm run codegen`).
- [x] `platform/data_api/tests/test_technicals_candle_pattern.py` -- Test through `/api/rankings/technicals-values`:
  - a `CandlePattern` column returns 0 or ±100 per ranked instrument and equals the chart route's value at the last closed candle;
  - the catalog lists `choices.pattern` with all 23 names;
  - a PUT/GET round trip of a `CandlePattern` column preserves the params.
- [x] `platform/frontend/src/components/chart/IndicatorPicker.tsx` and `paramCoercion.ts` -- Give the row its catalog `choices` and render a `<select>` for those params. `isValidParamText(previous, raw, choices?)` rejects a value outside `choices`.
- [x] `platform/frontend/src/pages/ChartPage.tsx` -- Add the Known-limit comment at the histogram mapping.
- [x] Frontend tests:
  - `ChartPage.test.tsx`: the `CandlePattern` row renders a pattern `<select>` with the fixture's choices, Apply persists the chosen pattern, and the series is drawn as a Histogram pane.
  - `RankingsPage.test.tsx`: a `CandlePattern` column shows ±100/0 values.
  - `technicals.test.ts`: `buildGroups` for a `CandlePattern` value.
  - `paramCoercion.test.ts`: choices validation.
- [x] `platform/research/domain/events.py` -- `forward_returns(closes: np.ndarray, hit_indices: Sequence[int], horizons: Sequence[int]) -> dict[int, np.ndarray]`: simple returns `c[i+h]/c[i]-1`, NaN past the end or where either close is NaN, and a ValueError when an index or horizon is invalid. Plus `hit_rate(returns, direction)`, which returns (hit rate, mean, n) over the finite values, with the rate NaN when n = 0. Pure numpy, docstrings naming invariants.
- [x] `platform/research/tests/test_events.py` -- The matrix row, plus hit rate for a bearish direction and a zero-count case.
- [x] `platform/research/application/patterns.py` -- The scanner service:
  - `bar_grid(frames, iid, bar_seconds, start_ns, end_ns)`: the span-by-span bars on the complete bucket grid, with an absent bucket as a NaN row;
  - `scan(grid, thresholds, ema_len)`: resets at holes, and returns a hits frame with the columns `timestamp`, `ts_ns`, `bar_index`, `pattern`, `direction`, `open`, `high`, `low`, `close` and `ema`;
  - `filter_hits(hits, condition, pattern_filter)`;
  - `forward_table(grid, hits, horizons)`: per timeframe, pattern and direction, the hit rate, mean and n per horizon;
  - `hit_window(grid, bar_index, window_bars)`;
  - `no_hits_note(...)`.
  It computes no statistic of its own.
- [x] `platform/research/tests/test_patterns.py` -- Test on a hand-built grid: a reset at a hole, the EMA filter, the forward table, and a real `CatalogFrames` read on the fixture.
- [x] `platform/research/notebooks/06_candlestick_scanner.py` and `.ipynb` -- The notebook:
  - §1 Parameters: `TIMEFRAMES=[60,300,900,3600,14400,86400]`, `EMA_LEN=50`, `CONDITION="any"`, `PATTERN_FILTER=""`, `HIT_INDEX=0`, `WINDOW_BARS=30` and `HORIZONS=[1,5,20]`, all through `setting`;
  - §2 bars per timeframe;
  - §3 the scan and the hits table;
  - §4 the hit chart;
  - §5 forward returns and hit rate;
  - §6 a reading guide and the hand-off to 27.8.
- [x] Delete `platform/research/notebooks/candlestick_pattern_scanner.ipynb`, and remove its exemptions in `test_notebooks.py` and `test_research_reads.py`.
- [x] `platform/research/tests/test_notebooks.py` and `test_notebook_candlestick_scanner.py` -- Add a `NOTEBOOK_ENV` entry sized to the fixture, and a namespace test covering the hits columns, the forward table's horizons, the figure count, and no forbidden token in a code cell.
- [x] Docs:
  - the spine AD-D3, kernel row, research row and tree, each marked `[amended 2026-09-28: Story 27.7 — …]` with the invariant "one pattern definition, shared by views, research and bots";
  - `epics.md` MR5;
  - `platform/ARCHITECTURE.md`;
  - `research/README.md`, which gains the index row and drops the legacy line.
- [x] Parity check in the scratch venv: TA-Lib against `CandlePattern` over one day of real BTC 1 m bars from Bybit's public kline REST. Record per-pattern agreement and the reasons for each deviation in the story file's Completion Notes and in the Auto Run Result.

**Acceptance Criteria:**
- Given the full platform suite, when it runs, then there are no failures beyond the baseline, and `test_boundaries.py` passes with `candle_patterns` in the kernel.
- Given the fixture, when `06_candlestick_scanner.py` runs through the harness under `simplefilter("error")`, then it finishes in < 60 s and the `.ipynb` pairing test passes.
- Given `npm test` in `platform/frontend`, when it runs, then vitest and codegen pass.

## Spec Change Log

- 2026-09-28 (dev): **22 patterns, not 23.** The Intent, the Tasks and the story all enumerate the same names, and they number 22 (9 single-bar, 7 two-bar, 6 three-bar); "23" is a count slip. `PatternName` holds exactly the 22 listed, and the catalog's `choices.pattern` lists 22 (the data_api test asserts that).
- 2026-09-28 (dev): **Which patterns need a prior trend** was not fixed by the Intent. Following the story's Task 1 and Nison: hammer, hanging man, inverted hammer, shooting star, morning/evening star, three white soldiers/black crows and tweezer top/bottom; the rest (as in TA-Lib) do not. Documented in `kernel/candle_patterns.py`'s docstring.
- 2026-09-28 (dev): **A `partial` bar is a NaN row** in the scanner's grid (not only an absent bucket): its OHLC is not the bucket's, so it resets the pattern set and the EMA, as `aligned.bar_returns` already blanks a partial close.
- 2026-09-28 (dev): **Scanner service surface.** Besides the listed functions, `research/application/patterns.py` has `split_timeframes` (the skip line), `bar_grids`/`scan_grids` (the per-instrument/timeframe loops, so the notebook's §2 and §3 are single calls), `ema_values`/`with_ema` (the EMA the chart overlays), `grid_summary`, `select_hit` and `hit_forward_returns`. `forward_table` takes the grids mapping (each hit is measured on its own grid). `hit_rate` counts a flat return as not a hit.
- 2026-09-28 (dev): **Empty legacy tables.** Removing the last exemption leaves `LEGACY_NOTEBOOKS_UNTIL` and `LEGACY_READS_UNTIL` empty; their expiry tests stay (Story 27.9 closes the epic) and now report pytest's empty-parameter-set skip.

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 1, medium 4, low 4)
- defer: 3
- reject: 7
- addressed_findings:
  - `[high]` `[patch]` `_check_bar` let a NaN close and infinite high/low through (`min`/`max` swallow NaN), which gave a silent "no pattern" or a spurious DOJI. It now rejects any non-finite price, and the malformed-bar test covers a NaN close and ±inf.
  - `[medium]` `[patch]` `Thresholds` accepted `doji_body_ratio >= body_ratio`, which silently disables SPINNING_TOP. It now raises `ValueError`, with two new bad-threshold cases.
  - `[medium]` `[patch]` DOJI's +100 was scored as a bullish hit rate in `forward_table`. Added `kernel.candle_patterns.NON_DIRECTIONAL`: a non-directional pattern now reports its mean and `n` with a NaN hit rate. The docstring, the notebook §5 text and the tests are updated, including a new directional ENGULFING test.
  - `[medium]` `[patch]` The new `ma_type` dropdown offered ADAPTIVE, which `MovingAverageFactory.create` cannot build (it returns None, so the replay crashed). ADAPTIVE is now left out of `choices` and rejected by name with `ValueError` (a 400) in `_resolve_enum_params`, with a test.
  - `[medium]` `[patch]` The `technicals_values` docstring claimed "nothing repaints". A bucket that has closed on the wall clock can still get its last seconds at the next capture flush. The claim is removed and replaced by a `Known limit:` naming the capture-watermark upgrade path.
  - `[low]` `[patch]` The default thresholds existed twice, as the `Thresholds` defaults and the `CandlePattern` keyword defaults. Kernel purity forbids a module-level `Thresholds()` (tried, and `test_kernel_is_pure` failed), so a test now pins the two equal and a comment explains why.
  - `[low]` `[patch]` `hit_forward_returns` assumed unique index labels. It now resets the index and uses positions, with a test on a concatenated table.
  - `[low]` `[patch]` An empty `HORIZONS` or a negative `WINDOW_BARS` was accepted silently. Both now raise `ValueError`, with tests.
  - `[low]` `[patch]` The scanner's per-bar cost and whole-grid memory had no documentation. `scan` now carries a `Known limit:` (~80 µs per bar, measured) with its upgrade path.

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 0, low 4)
- defer: 1
- reject: 8
- addressed_findings:
  - `[low]` `[patch]` `Thresholds` rejected numpy scalars (`np.int64` trend_bars, `np.float32` ratios), so a 27.8 sweep over `np.arange` would raise. Ratios now accept any `numbers.Real` and `trend_bars` any `numbers.Integral` (never `bool`), and both are stored as plain `float`/`int` so `asdict` stays JSON-safe. The ranges moved into one `_RATIO_BOUNDS` table. New test.
  - `[low]` `[patch]` `CandlePatternSet(thresholds=Thresholds())` built a `Thresholds` at import time, which is the module-level kernel state the `CandlePattern` comment says is forbidden. The default is now `None`, resolved to `Thresholds()` per call. New test.
  - `[low]` `[patch]` `research.domain.events.forward_returns` rejected numpy integer horizons, and `patterns.hit_forward_returns` crashed on `if not horizons` for a numpy array. Both accept numpy integers now: `numbers.Integral`, keys stored as `int`, and a `len(...) == 0` check. New tests.
  - `[low]` `[patch]` `ranking_columns._closed_candles` dropped at most the newest candle. The store read is unbounded above, so a capture clock ahead of the API host by more than a bar left a second unclosed candle in the replay. It now drops every candle whose bucket has not closed at `now_ns`. New test.

## Design Notes

- **Why the filter stands in for sorting:** a binding rule says rows are "never re-sorted client-side". A Technicals filter on `CandlePattern.value > 0` (or `< 0`) lists exactly the instruments whose latest closed bar fired, so the screener works as a scanner without breaking the rule. This is recorded as a deviation from AC2's wording.
- **Why closed bars:** the candle store's newest bucket is the forming one, since `CandleSink` upserts every flush. A pattern on a forming bar repaints: it fires and then disappears. Cutting the forming bar for every column keeps one rule for all columns and matches AC2's "latest closed bar".
- **Why holes reset the detector:** a two- or three-bar pattern across an outage or an untraded bucket would compare bars that are not adjacent in time. The reset keeps gaps visible (DATA-01).
- **Gaps in continuous markets:** in 24/7 markets a bar's open usually equals the prior close. `PIERCING` and `DARK_CLOUD_COVER` therefore test `open <= prior close` (or `>=`) instead of Nison's gap beyond the prior low or high. Stars honour `star_gap`. Both are listed as parity deviations.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. kernel/tests views/tests data_api/tests research/tests tests/test_boundaries.py -q` -- expected: pass except the known baseline (Redis `data_api` tests)
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: baseline failures only
- `cd platform/frontend && npm test && npx tsc -b --noEmit` -- expected: pass
- `ruff check`, `ruff format --check` and `mypy` on the changed Python files, plus `uv run jupytext --sync platform/research/notebooks/06_candlestick_scanner.py` -- expected: clean

## Auto Run Result

Status: done

**Summary:** A follow-up review of Story 27.7 (the `CandlePattern` kernel detector, its catalog entry for the chart picker and the screener, the closed-bar Technicals cut, and the `06_candlestick_scanner` notebook) confirms the implementation. It applies four low-severity hardening patches. The detector logic itself had no finding.

**Files changed in this pass:**
- `platform/kernel/candle_patterns.py`: `Thresholds` accepts numpy scalars and normalises them to `float`/`int` through one `_RATIO_BOUNDS` table. `CandlePatternSet`'s default is `None`, so no `Thresholds` is built at import time.
- `platform/research/domain/events.py`: numpy integer horizons are accepted, and the result is keyed by `int`.
- `platform/research/application/patterns.py`: the empty-horizons check works on a numpy array.
- `platform/views/ranking_columns.py`: `_closed_candles` drops every unclosed candle, not only the newest.
- Tests: `kernel/tests/test_candle_patterns.py`, `research/tests/test_events.py`, `research/tests/test_patterns.py` and `views/tests/test_ranking_columns_closed_bar.py` (6 new tests).

**Review findings:** 13 raw findings (Blind Hunter 8, Edge Case Hunter 5), deduplicated to 11.
- **4 patches applied, all low.**
- **1 deferred:** the chart and screener replay feeds `partial` candles as real OHLC.
- **8 rejected:**
  - Two duplicate existing ledger entries: the replay across untraded holes, and the PUT that validates names only.
  - A Technicals GET with a bad param returns 200 when no coin has candles. This is a facet of that same PUT/param-validation ledger entry.
  - The 90 s technicals cache TTL is already named in the `technicals_values` Known limit.
  - The chart shows a pattern on the forming bar. This is by design: the chart draws the forming candle, and the closed-bar cut is scoped to Technicals.
  - A malformed stored candle turns into a 400. This is speculative: the fold keeps OHLC consistent, and the error names the bar (DATA-07 loud).
  - The data_api cross-check test uses closed-only fixture data. The closed-bar cut has its own tests.

**Follow-up review recommended:** false. The four patches are local input-type and filter hardening with tests. No contract or behaviour changes for valid inputs.

**Verification:**
- **Affected suites:** kernel, research events and patterns, views closed-bar and picker, the data_api candle-pattern test and boundaries: 240 passed.
- **Full platform suite:** 10 failed, 2699 passed, 3 skipped. The 10 failures are exactly the known baseline: 9 `data_api` tests that need Redis, plus `ranking/tests/test_metrics_store.py::test_price_near_days_ago_returns_price_at_or_before_target_per_instrument`. `test_boundaries.py` passes (kernel purity holds with `import numbers`).
- **Lint:** `ruff check` and `ruff format --check` are clean on the 8 changed files. `mypy` is clean on the 4 changed source modules.
- **Notebook:** its source is unchanged, and it runs in the research suite.
- **Frontend:** not touched in this pass.

**Residual risks:**
- These carry over from the first pass: size thresholds relative to the bar's own range (for 27.8 to decide), one-flush Technicals repaint (a Known limit), and the hole-unaware chart and screener replay (deferred).
- New in the ledger: `partial` bars in the chart and screener replay.
