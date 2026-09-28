# Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 23.2 (`kernel/`), 24.2 (`views/` indicator catalog), 27.1 and 27.2. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.7".

## Story

As a trader and researcher,
I want the classic candlestick patterns detected by one streaming indicator that the chart, the screener and a strategy all share, with no TA-Lib,
so that I can scan the whole collected universe for a pattern at any timeframe, see it on the chart, and later trade it with the same code.

## Acceptance Criteria

1. **Given** `kernel/indicators.py` (pure `Indicator` classes) and NFR12
**When** the story ships
**Then** `kernel/candle_patterns.py` holds `CandlePattern(Indicator)` with `update_raw(open, high, low, close)` and outputs `value` (`+100` bullish, `-100` bearish, `0` none, TA-Lib's convention so the scanner tables read the same) and a `pattern` parameter selecting one of: single-bar `DOJI`, `DRAGONFLY_DOJI`, `GRAVESTONE_DOJI`, `HAMMER`, `HANGING_MAN`, `INVERTED_HAMMER`, `SHOOTING_STAR`, `MARUBOZU`, `SPINNING_TOP`; two-bar `ENGULFING`, `HARAMI`, `HARAMI_CROSS`, `PIERCING`, `DARK_CLOUD_COVER`, `TWEEZER_TOP`, `TWEEZER_BOTTOM`; three-bar `MORNING_STAR`, `EVENING_STAR`, `THREE_WHITE_SOLDIERS`, `THREE_BLACK_CROWS`, `THREE_INSIDE_UP`, `THREE_INSIDE_DOWN`; the geometric thresholds (`body_ratio`, `shadow_ratio`, `doji_body_ratio`, `trend_bars` for the prior-trend requirement of hammer/hanging-man/star patterns) are explicit constructor parameters with documented defaults, the module docstring defines each pattern in words and by inequality, the detector keeps only the last three bars (`O(1)` per bar, no history list), and `CandlePatternSet` runs every pattern over one bar stream and returns the fired names for the scanner; `kernel/tests/test_candle_patterns.py` has one hand-drawn bar sequence per pattern for the bullish, bearish and negative case, plus a property test that a bar sequence never fires both an `X` and its mirror; the DDD spine's AD-D3 kernel list and MR5 are amended to include `candle_patterns` with the invariant "one pattern definition, shared by views, research and bots"

2. **Given** `views`' native indicator catalog (`chart_indicators.INDICATOR_CATALOG`, an `IndicatorSpec` with `feed`, `outputs`, `panel`) and the screener's Technicals columns (`/api/rankings/technicals-values`, `screener_columns.toml`)
**When** the story ships
**Then** `CandlePattern` is registered in that catalog with `feed = ("open", "high", "low", "close")`, `outputs = ("value",)`, `panel = "histogram"` and its parameters JSON-safe (`pattern` as a string enum, listed in the picker's dropdown like `ma_type`); the chart page's picker offers it under the native category and draws the `±100` spikes in a histogram pane; the Technicals tab offers it as a column with a `pattern` parameter and a timeframe, so the screener becomes a pattern scanner across every collected instrument (the latest closed bar's `value` per instrument; sorting by the column groups the hits); `chart_indicators.toml` and `screener_columns.toml` key sets are unchanged (a new entry uses the existing `name`/`category`/`bar_seconds`/`params` keys); a `Known limit:` comment in `ChartPage.tsx`'s pane code records that pattern hits are histogram spikes, not on-candle markers, with the upgrade path (lightweight-charts series markers when the chart adopts them); frontend tests cover the catalog entry rendering and the column values; `views/tests` and `data_api/tests` cover the replay through the existing `replay_indicator` path with no special case

3. **Given** `dydx_collector/notebooks/candlestick_pattern_scanner.ipynb` (moved by 24.4; it pip-installs TA-Lib and `pandas_ta` at runtime and reads the retired `custom_dydx_minute_bar` directory)
**When** `06_candlestick_scanner` ships
**Then** it scans `INSTRUMENTS` over `START`–`END` at every timeframe in `TIMEFRAMES` from `MarketFrames.bars` (the candle store's fold, never a pandas resample of its own), filters hits by the EMA condition (`above`/`below`/`any` against `nautilus_trader.indicators.ExponentialMovingAverage`) and an optional pattern filter, lists hits in one table tagged by timeframe and direction, renders a candlestick chart centred on a chosen hit with the EMA overlaid and the hit marked (plotly, a parameter cell selecting the hit; no `ipywidgets`), and computes the forward return after each hit at 1, 5 and 20 bars with the hit rate per pattern as the notebook's research output; the old notebook is deleted, the smoke test runs the new one against the fixture catalog, and a one-off parity check against TA-Lib (run locally by the developer where TA-Lib is installed, not in CI, not a dependency) is recorded in the story's Completion Notes with the per-pattern agreement rate and every documented deviation

## Tasks / Subtasks

- [ ] Task 1 — `kernel/candle_patterns.py` (AC: #1)
  - [ ] `PatternName` (`enum.StrEnum`, the 23 names); `Thresholds` (frozen dataclass: `body_ratio=0.3` body/range for "small body", `shadow_ratio=2.0` shadow/body for hammer-family, `doji_body_ratio=0.1`, `marubozu_shadow_ratio=0.05`, `trend_bars=3` bars of prior direction for hammer/hanging-man/star/soldiers/crows, `star_gap=True` whether the star must gap); `CandlePattern(Indicator)` with `__init__(pattern: PatternName | str, **thresholds)`, `update_raw(open, high, low, close)`, `handle_bar(bar)`, `value: int`, `_reset`; state = three `_Bar` tuples + a small prior-trend accumulator (direction counts, bounded by `trend_bars`), nothing else (`O(1)`); one private `_detect_<name>(bars) -> int` per pattern, each ≤ 30 lines (READ-01), driven by a dispatch dict.
  - [ ] Module docstring: each pattern's definition in words and the inequality it tests, plus the TA-Lib naming map (`CDLENGULFING` ↔ `ENGULFING`, …) so the parity check is mechanical.
  - [ ] `CandlePatternSet`: holds one `CandlePattern` per name with shared thresholds; `update_raw` feeds all; `fired() -> list[tuple[PatternName, int]]`.
  - [ ] `kernel/tests/test_candle_patterns.py`: per pattern, hand-drawn bars for bullish, bearish, and a near-miss negative; the mirror property (`HAMMER` and `HANGING_MAN` never both fire on one sequence, `ENGULFING` +100 and −100 never both, …); `O(1)` state (no attribute grows with bars fed).
  - [ ] Spine: AD-D3 kernel list and MR5 amended (`[amended <date>: Story 27.7]`) with the invariant "one pattern definition, shared by views, research and bots"; `test_boundaries.py` kernel row updated; `test_namespace.py` unaffected (no Arrow class).
- [ ] Task 2 — chart picker and screener (AC: #2)
  - [ ] `views/chart_indicators.py` (post-24.2 path; `ml_signals/chart_indicators.py` before): `INDICATOR_CATALOG["CandlePattern"] = IndicatorSpec(candle_patterns.CandlePattern, {"pattern": "ENGULFING", "body_ratio": 0.3, "shadow_ratio": 2.0, "doji_body_ratio": 0.1, "trend_bars": 3}, ("open", "high", "low", "close"), ("value",), "histogram", {"pattern": PatternName})` — the existing `enum_params` mechanism round-trips `pattern` through JSON like `ma_type`/`price_type`; verify `_resolve_enum_params` handles a `StrEnum`.
  - [ ] Confirm the replay path (`replay_indicator` → `_feed_values` in `update_raw` order) needs no change; the technicals route (`data_api/routes/rankings.py:230-307`, `_latest_values`) picks up the new catalog entry through `_require_known_indicators`; add a route test with a `CandlePattern` column producing `±100/0` per instrument on the fixture candles.
  - [ ] Frontend: the picker lists the entry from `/api/indicators/catalog` (verify the enum dropdown renders for `pattern`; extend the param editor if it only handles the two known enums); histogram pane renders the spikes; Technicals column selector offers it; tests in `ChartPage.test.tsx`/`RankingsPage.test.tsx`/`technicals.test.ts`. `Known limit:` comment in `ChartPage.tsx` pane code: spikes, not markers; upgrade path lightweight-charts `createSeriesMarkers`.
  - [ ] TOML key sets unchanged (`name`, `category`, `bar_seconds`, `params`).
- [ ] Task 3 — `06_candlestick_scanner` (AC: #3)
  - [ ] Sections: (1) Parameters (adds `TIMEFRAMES`, `EMA_LEN`, `CONDITION`, `PATTERN_FILTER`, `HIT_INDEX`, `WINDOW_BARS`); (2) Bars per timeframe from `MarketFrames.bars` (candle store; for a timeframe the store lacks, `research.domain` `ReturnSeries`-style OHLC compounding is not allowed — instead state which stored sizes exist and skip the rest with a line); (3) Scan: `CandlePatternSet` streamed over each timeframe's bars; EMA via `nautilus_trader.indicators.ExponentialMovingAverage`; hits table (`timestamp`, `timeframe`, `pattern`, `direction`, `close`, `ema`); (4) Hit chart: plotly candlestick centred on `HIT_INDEX` ± `WINDOW_BARS` with EMA and a marker; (5) Forward returns at 1/5/20 bars per pattern and direction, hit rate and mean, as the research output (`research/domain/events.py`: `forward_returns(prices, hit_indices, horizons)`, numpy, tested); (6) Reading guide, hand-off to 27.8.
  - [ ] Delete `research/notebooks/candlestick_pattern_scanner.ipynb` (post-24.4 path).
  - [ ] Parity check: locally, where TA-Lib is installed, run both detectors over one real day of BTC 1 m bars and record per-pattern agreement and the reasons for each deviation (threshold conventions differ; TA-Lib's are documented per `CDL*` function). Not in CI, not a dependency, results in Completion Notes.

## Dev Notes

- **Why `kernel/`:** the detector is consumed by views (chart, screener), research (scanner, strategy) and bots (paper strategy in 27.8). The kernel is the one place all three may import (spine AD-D3, MR5); the amendment names the invariant.
- **Why a Nautilus `Indicator` subclass:** `IndicatorSpec.cls` is instantiated with `params` and fed by `update_raw` in `feed` order; that is the whole contract the picker, the technicals route and a `Strategy`'s `handle_bar` need. `ml_signals/indicators.py`'s `Microprice`/`OrderFlowImbalance` are the precedent (`_set_has_inputs`, `_set_initialized`, `_reset`).
- **No TA-Lib, no `pandas_ta`** (NFR12, memory "Minimize dependencies", the FR30 decision recorded in Additional Requirements). Definitions come from the standard references (Nison) with explicit thresholds; the docstring is the spec, the tests are the proof, the local TA-Lib parity run is evidence, not a dependency.
- **Prior-trend requirement:** hammer vs hanging man (and morning vs evening star) differ only by prior trend; `trend_bars` consecutive closes in one direction is the documented rule, bounded state.
- **Histogram, not markers:** the frontend has no series-marker support today (`grep setMarkers` finds nothing). A `±100` histogram pane is exact and immediate; markers are the recorded upgrade path.
- **Project rules:** DESIGN-01, DESIGN-02, SSOT-01/03 (the chart and the screener share one catalog entry), TEST-01 (financial calculation), TEST-03, TEST-04, READ-01, READ-03, NFR12, FR30 precedent (indicator classes in the shared place, catalog entry in views).
- **Working directory:** `platform/`; kernel tests `python3 -m pytest -o addopts="" --rootdir=. kernel/tests views/tests data_api/tests research/tests -q`; frontend `npm test` in `platform/frontend`.

### Project Structure Notes

- `kernel/candle_patterns.py`, `kernel/tests/test_candle_patterns.py`; `views/chart_indicators.py` (catalog entry); `data_api/tests/test_technicals_candle_pattern.py`; frontend picker/param-editor/tests; `research/domain/events.py`; `research/notebooks/06_candlestick_scanner.py` + `.ipynb`; delete `research/notebooks/candlestick_pattern_scanner.ipynb`.

### References

- Epic text: "Story 27.7"; FR77, NFR12
- Code: `ml_signals/chart_indicators.py:37-70,295-350` (`IndicatorSpec`, `replay_indicator`, `_resolve_enum_params`, `catalog_json`), `ml_signals/custom_indicators.py:55-107`, `data_api/routes/indicators.py:86-115,261-285`, `data_api/routes/rankings.py:121-307`, `frontend/src/pages/technicals.ts`, `ml_signals/indicators.py:116-160` (Indicator subclass precedent)
- Old notebook: `dydx_collector/notebooks/candlestick_pattern_scanner.ipynb` (patterns, EMA filter, timeframes, hit chart — the behaviours to keep)
- Spine: AD-D3, MR5

## Dev Agent Record

### Agent Model Used

claude-opus-5-5 (bmad-loop dev session, 2026-09-28)

### Debug Log References

- Baseline full suite before any change: 10 failed (9 Redis-dependent `data_api` tests + `ranking/tests/test_metrics_store.py::test_price_near_days_ago_returns_price_at_or_before_target_per_instrument`), 2529 passed, 1 skipped. After: the same 10 failed, 2685 passed, 3 skipped (the 2 new skips are pytest's empty-parameter-set skips of `test_legacy_notebook_exemption_expires_with_its_story` and its `test_research_reads.py` twin, whose tables this story empties).

### Completion Notes List

- 22 patterns, not 23: the story's (and epics.md's) enumerated list holds 22 names (9 single-bar, 7 two-bar, 6 three-bar); the spec's "23" is a count slip. `PatternName` has exactly the 22 listed (spec Change Log).
- Trend patterns (need `trend_bars` closes in one direction before their first bar, and `trend_bars + 1` extra warm-up bars): hammer, hanging man, inverted hammer, shooting star, morning/evening star, three white soldiers/black crows, tweezer top/bottom. Engulfing, harami, harami cross, piercing, dark cloud cover and three inside up/down do not (as TA-Lib).
- The scanner treats a `partial` bar (under 90% of its span observed) as a NaN row, like an absent bucket: its OHLC is not the bucket's, so it resets the pattern set and EMA (as `aligned.bar_returns` blanks it).
- `06_candlestick_scanner` on the fixture: 1.49 s in the harness (limit 60 s), under `warnings.simplefilter("error")`.
- **TA-Lib parity check** (one-off, scratch venv outside the repo, TA-Lib 0.8.1 + numpy 2.5.3; script not committed). Data: Bybit public REST `v5/market/kline?category=linear&symbol=BTCUSDT&interval=1`, the 1440 closed 1 m bars of 2026-09-27 UTC (two requests, contiguous, no flat bar; every bar opens exactly at the previous close). Agreement = share of the 1440 bars where `sign(ours) == sign(TA-Lib)`; our value is 0 before `initialized`.

  | Pattern | TA-Lib | Agree | Ours fired | TA-Lib fired | Both (same sign) |
  |---|---|---:|---:|---:|---:|
  | DOJI | CDLDOJI | 90.69% | 143 | 259 | 134 |
  | DRAGONFLY_DOJI | CDLDRAGONFLYDOJI | 97.29% | 60 | 37 | 29 |
  | GRAVESTONE_DOJI | CDLGRAVESTONEDOJI | 95.35% (96.81% by magnitude) | 51 | 37 | 0 (21 opposite sign) |
  | HAMMER | CDLHAMMER | 94.86% | 8 | 76 | 5 |
  | HANGING_MAN | CDLHANGINGMAN | 96.25% | 12 | 54 | 6 |
  | INVERTED_HAMMER | CDLINVERTEDHAMMER | 99.51% | 7 | 0 | 0 |
  | SHOOTING_STAR | CDLSHOOTINGSTAR | 99.03% | 14 | 0 | 0 |
  | MARUBOZU | CDLMARUBOZU | 81.04% | 537 | 288 | 276 |
  | SPINNING_TOP | CDLSPINNINGTOP | 97.36% | 46 | 84 | 46 |
  | ENGULFING | CDLENGULFING | 98.33% | 323 | 345 | 322 |
  | HARAMI | CDLHARAMI | 87.43% | 127 | 174 | 60 |
  | HARAMI_CROSS | CDLHARAMICROSS | 95.28% | 79 | 59 | 35 |
  | PIERCING | CDLPIERCING | 96.25% | 54 | 0 | 0 |
  | DARK_CLOUD_COVER | CDLDARKCLOUDCOVER | 96.32% | 53 | 0 | 0 |
  | MORNING_STAR | CDLMORNINGSTAR | 100.00% | 0 | 0 | 0 |
  | EVENING_STAR | CDLEVENINGSTAR | 100.00% | 0 | 0 | 0 |
  | THREE_WHITE_SOLDIERS | CDL3WHITESOLDIERS | 99.03% | 7 | 9 | 1 |
  | THREE_BLACK_CROWS | CDL3BLACKCROWS | 99.79% | 3 | 0 | 0 |
  | THREE_INSIDE_UP | CDL3INSIDE (+100) | 99.31% | 10 | 0 | 0 |
  | THREE_INSIDE_DOWN | CDL3INSIDE (-100) | 99.24% | 11 | 0 | 0 |
  | TWEEZER_TOP | none | -- | 13 | -- | -- |
  | TWEEZER_BOTTOM | none | -- | 10 | -- | -- |

  No bar ever got opposite signs except the documented gravestone convention. Every deviation, each checked on the same bars:

  - **Own range vs trailing average (DOJI, SPINNING_TOP, MARUBOZU, HARAMI, HARAMI_CROSS, DRAGONFLY, THREE_INSIDE_*).** TA-Lib calls a body "doji"/"short"/"long" against the average range or body of the previous 10 bars; ours compares with the bar's own range (stateless, O(1)). Verified: TA-Lib's `CDLDOJI` equals "body <= 10% of the prior 10 bars' average range" on 100% of bars, and all 125 TA-Lib-only doji have a body over 10% of their own range. 252 of our 261 MARUBOZU-only hits have a body no longer than the prior 10 bars' average (small bars with no shadows: their median range is $6.2 against $21.2 for all bars), and 64 of our 67 HARAMI-only hits have a first body that TA-Lib does not count as long; 113 of TA-Lib's 114 HARAMI-only hits have a second body over 30% of its own range. The first 10 bars are also TA-Lib's lookback (always 0 there). *Known limit worth stating:* own-range thresholds cannot tell a small bar from a long one, so MARUBOZU and HARAMI fire on quiet bars; upgrade path: a body-vs-trailing-average threshold (bounded state), a 27.8 decision.
  - **Direction convention (GRAVESTONE_DOJI).** TA-Lib gives +100; ours gives -100 (Nison, spec). By magnitude the two agree on 96.81% of bars; all 21 shared hits differ only in sign.
  - **Prior trend (HAMMER, HANGING_MAN, THREE_WHITE_SOLDIERS, THREE_BLACK_CROWS).** TA-Lib has no trend test: its hammer only needs the body near the prior bar's low (hanging man: high), its crows only a white bar before them. Ours needs 3 consecutive falling (rising) closes. Verified: only 10 of TA-Lib's 76 hammers and 10 of its 54 hanging men follow 3 falling (rising) closes.
  - **Gaps (INVERTED_HAMMER, SHOOTING_STAR, PIERCING, DARK_CLOUD_COVER, MORNING/EVENING_STAR).** TA-Lib requires a real-body gap (inverted hammer, shooting star, stars) or an open beyond the prior low/high (piercing, dark cloud). On this day 1439 of 1439 bars open at the previous close: no bar opens beyond the prior low or high and no body gaps, so TA-Lib fires none of them. Ours tests `s.o <= f.c` (`>=`) for piercing/dark cloud (the 24/7 adaptation in the spec) and has no gap test for inverted hammer/shooting star, so it fires; the stars keep `star_gap=True` and fire on neither side.
  - **THREE_INSIDE_*.** TA-Lib's `CDL3INSIDE` needs the first body long against the 10-bar average and the second strictly inside; zero hits that day. Ours uses own-range long/small (above).
  - **Tweezers** have no TA-Lib function.

### File List

Added:
- platform/kernel/candle_patterns.py
- platform/kernel/tests/test_candle_patterns.py
- platform/views/tests/test_ranking_columns_closed_bar.py
- platform/data_api/tests/test_technicals_candle_pattern.py
- platform/research/domain/events.py
- platform/research/application/patterns.py
- platform/research/notebooks/06_candlestick_scanner.py
- platform/research/notebooks/06_candlestick_scanner.ipynb
- platform/research/tests/test_events.py
- platform/research/tests/test_patterns.py
- platform/research/tests/test_notebook_candlestick_scanner.py

Modified:
- platform/views/indicator_picker.py
- platform/views/ranking_columns.py
- platform/views/tests/test_indicator_picker_native.py
- platform/data_api/routes/indicators.py
- platform/frontend/openapi.json
- platform/frontend/src/api/schema.ts
- platform/frontend/src/components/chart/IndicatorPicker.tsx
- platform/frontend/src/components/chart/paramCoercion.ts
- platform/frontend/src/components/chart/paramCoercion.test.ts
- platform/frontend/src/pages/ChartPage.tsx
- platform/frontend/src/pages/ChartPage.test.tsx
- platform/frontend/src/pages/RankingsPage.test.tsx
- platform/frontend/src/pages/technicals.test.ts
- platform/research/tests/test_notebooks.py
- platform/research/tests/test_research_reads.py
- platform/tests/test_boundaries.py
- platform/research/README.md
- platform/ARCHITECTURE.md
- _bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md
- _bmad-output/planning-artifacts/epics.md

Deleted:
- platform/research/notebooks/candlestick_pattern_scanner.ipynb
