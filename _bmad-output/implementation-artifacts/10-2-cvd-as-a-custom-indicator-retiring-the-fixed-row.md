---
baseline_commit: 9d422d69ae
---

# Story 10.2: CVD as a custom indicator, retiring the fixed "5-min cumulative delta" row

Status: done

## Story

As a user of the chart page,
I want Cumulative Volume Delta available from the indicator picker instead of a permanently-shown row,
so that I only see CVD when I actually want it, alongside whatever else I've picked, at whatever timeframe I'm viewing.

## Acceptance Criteria

1. `ml_signals/custom_indicators.py`'s `CUSTOM_INDICATOR_CATALOG` gains a `"CumulativeVolumeDelta"` entry (`panel: "oscillator"`, no params) whose `replay` function computes a true per-candle running-cumulative series: bucket the window's `DydxSecondSnapshot` buy/sell volume split into the candle time grid (same `(ts // bar_ns) * bar_ns` bucketing `ml_signals/candles.py`'s `build_candles` already uses), reduce each bucket via `ml_signals.indicators.trade_aggregates()` (SSOT-01 — do not re-derive the buy/sell split), then accumulate `buy_vol - sell_vol` per bucket into a running total starting from 0 at the window's first candle.
2. **Scope decision, not a silent gap:** this indicator only computes for the historical window (`window.start_ms`/`end_ms` both set) — matching the *old* fixed row's own actual behavior, which was never live either (`_render_chart_page`'s `compute_chart_series` call always used the date-range form's explicit window, no live polling). When `window.start_ms is None` (live mode), the replay returns `None` for every candle rather than fabricating a value — a real limitation, stated plainly in Dev Notes/Completion Notes, not hidden.
3. `chart_data.py`'s `cum_delta` computation (the 300-second rolling-sum-from-trade-ticks logic, `_CUM_DELTA_SECONDS`, the `cum_buf`/`running_total` loop) is deleted entirely — not left dead/unreferenced.
4. `_render_chart_page`'s 7-row figure loses its "5-min cumulative delta" row and `_add("cum_delta", 6, ...)` call; the remaining 6 rows renumber to fill 1-6 (`row_heights` trimmed to 6 entries, `subplot_titles` trimmed).
5. `cd troll && python -m pytest ml_signals/tests -q` passes (no `test_chart_data.py` existed before this story — confirmed; `cum_delta` had no prior test coverage there, so none is owed for its removal).

## Tasks / Subtasks

- [x] Task 1 — CVD replay function (AC: #1, #2)
  - [x] Added `"CumulativeVolumeDelta"` to `CUSTOM_INDICATOR_CATALOG` in `custom_indicators.py` (no params, `panel: "oscillator"`).
  - [x] Historical case: `_second_snapshots(window)` queries `DydxSecondSnapshot` from the catalog and unwraps `CustomData`, mirroring `dashboard.py`'s `_historical_lines_json` exactly; `_cvd_replay` buckets by `window.bar_seconds` (same `(ts // bar_ns) * bar_ns` formula as `candles.py`), reduces each bucket via `trade_aggregates()`, accumulates a running total per candle.
  - [x] Live case (`window.start_ms is None`): returns `{"value": [None] * len(candles)}`.
  - [x] `custom_indicators.py` reads its own `_CATALOG_PATH` module-level constant (same env var/default as `dashboard.py`'s `CATALOG_PATH`) — one duplicated line, avoids a circular import.
- [x] Task 2 — Retire the fixed row (AC: #3, #4)
  - [x] Deleted `chart_data.py`'s `cum_delta`/`_CUM_DELTA_SECONDS` block entirely, plus the now-unused `sorted_trades`/`catalog.trade_ticks()` call and `deque`/`AggressorSide` imports it alone required.
  - [x] Removed `dashboard.py`'s row-6 subplot title, `_add("cum_delta", ...)` call, and its `add_hline`; renumbered spread to row 6; `rows=7`→`rows=6`, `row_heights` trimmed to 6 entries; updated `_render_chart_page`'s module docstring to drop the "cum-delta" mention.
- [x] Task 3 — Tests (AC: #5)
  - [x] `test_custom_indicators.py`: `test_cvd_accumulates_buy_minus_sell_volume_per_candle` (hand-built temp catalog with real `DydxSecondSnapshot` rows, TEST-03: no mocking) and `test_cvd_returns_none_for_every_candle_in_live_mode`. Also fixed a pre-existing test (`test_catalog_json_returns_params_and_panel_per_entry`) that asserted exact-dict-equality on the whole catalog — broken by `CumulativeVolumeDelta` now being a permanent entry — switched to a subset assertion.
  - [x] Confirmed via grep: no other module references `chart_data.py`'s removed `cum_delta`/`_CUM_DELTA_SECONDS`/`compute_chart_series`-cum-delta symbols. (`ofi_strategy.py`'s own `cum_delta` is a completely independent, differently-scoped Strategy signal — untouched, out of scope.)
  - [x] Full suite: `cd troll && python -m pytest ml_signals/tests -q` — 173 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py`, confirmed pre-existing via `git stash` in Story 9.1's review, reproduces identically).

### Review Findings

- [x] [Review][Patch] A candle bucket with zero `DydxSecondSnapshot` rows was silently treated as "zero net volume" (running total carried forward unchanged) — indistinguishable from a genuinely quiet second, when it's actually a second-snapshot collection gap (DATA-01 violation) [troll/ml_signals/custom_indicators.py:_cvd_replay] — fixed: an empty bucket now yields `None`, and the running total resumes from its last real value on the next bucket with data. Added 2 tests (gap → `None`, running total resumes correctly after a gap).
- [x] [Review][Patch] Stale module docstring claimed `CUSTOM_INDICATOR_CATALOG` is empty, contradicted by this same diff registering `CumulativeVolumeDelta` [troll/ml_signals/custom_indicators.py] — fixed.
- [x] [Review][Patch] `_cvd_replay`'s docstring justified the per-request reset via an inaccurate SMA/RSI analogy (those don't reset to a value tied to the request window; CVD's does) and cited a story doc by name instead of being self-contained — fixed: rewrote the docstring to state the actual design tradeoff (unbounded, request-anchored sum vs. the old bounded 5-min rolling metric) directly, and to explicitly disambiguate from `ofi_strategy.py`'s unrelated same-named `cum_delta` signal.
- [x] [Review][Patch] No test exercised CVD through the real production dispatch path (`catalog_json()`) — only via a monkeypatched placeholder — fixed: added `test_cvd_registered_in_production_catalog_with_correct_shape`.
- [x] [Review][Patch] `_render_chart_page`'s figure construction (`rows=`/`row_heights=`/`subplot_titles=`, all three must stay the same length or Plotly raises) had zero test coverage before or after this story's row-count change — fixed: added `test_render_chart_page_figure_row_counts_stay_in_lockstep`, monkeypatching `compute_chart_series` and asserting the page renders without raising.
- [x] [Review][Patch] `_second_snapshots`'s "duplicated logic" comment blamed a one-line constant for a ~15-line duplicated function body — fixed: corrected the comment to describe what's actually duplicated and when extracting a shared helper would be warranted (a second identical call site, not yet present).
- [x] [Review][Defer] `_indicators_json`'s broad exception-to-400 handling now has a real trigger (CVD's catalog fetch can raise) rather than the hypothetical one Story 10.1's review flagged — systemic `_indicators_json` concern affecting every custom indicator, not CVD-specific; tracked in deferred-work.md for a proper fix once Stories 10.3/10.4 confirm the pattern recurs. `troll/ml_signals/dashboard.py:_indicators_json`.
- [x] [Review][Defer] No catalog-level signal that CVD (or Cancel Pressure/OFI once shipped) is historical-only, so a user adding it while viewing the live chart gets silent, permanent `None` with no explanation — needs a schema + picker UI addition designed once for all three historical-only indicators, not bolted onto CVD alone; tracked in deferred-work.md. `troll/ml_signals/custom_indicators.py`, picker JS.
- [x] [Review][Dismiss] "buy_count/sell_count fetched but unused" — not dead data: `trade_aggregates()` requires both keys present in every row dict to reduce over, even though `_cvd_replay` only consumes the volume totals from its return value. Added a one-line comment clarifying this rather than removing the fields.

## Dev Notes

- **Read before touching anything:** `ml_signals/chart_data.py` in full (155 lines — the `cum_delta` block is lines ~68-86, confirmed this session; the whole `compute_chart_series` function). `ml_signals/indicators.py`'s `trade_aggregates`/`volume_delta` (lines ~449-467, confirmed this session — pure reduction over a list of snapshot dicts, SSOT-01). `dashboard.py`'s `_historical_lines_json` (~1359-1379, confirmed this session — the exact `ParquetDataCatalog` + `DydxSecondSnapshot` + `CustomData`-unwrap pattern to copy) and `_render_chart_page`'s 7-row figure (`make_subplots`/`_add`/`subplot_titles`, confirmed this session at ~1088-1135). `ml_signals/candles.py`'s `build_candles` bucketing (`(ts // period_ns) * period_ns`) — CVD's own bucketing must use the identical formula so its buckets land on the exact same boundaries as the candle list it's aligned against.
- **Why "historical only" is the right scope, not corner-cutting:** the *old* fixed-row `cum_delta` (and the whole `compute_chart_series` panel it lived in) was **never live** — `_render_chart_page` always calls `compute_chart_series` with the date-range form's explicit `start_ms`/`end_ms`, with no live-polling path at all (confirmed this session — this is unlike the interactive Candles/Lines/Ticks widget above it, which does have live vs. historical modes). Making the new picker-based CVD "historical only for now" is therefore not a regression versus what existed — it's the same actual capability, just reachable through the picker instead of a fixed row. Building live support would need a new mechanism to get `DydxSecondSnapshot` rows for the live window into `custom_indicators.py` without a circular import on `dashboard.py`'s in-process `_second_rolling` buffer — real work, out of proportion to what was asked, and not required by any AC. If a later story wants live CVD, that's a new, explicit story.
- **`ReplayWindow`'s existing 4 fields (`instrument_id`, `bar_seconds`, `start_ms`, `end_ms`) are sufficient for this story** — no need to extend the dataclass Story 10.1 shipped. Do not add new fields to `ReplayWindow` for this story.
- **`troll/CLAUDE.md` constraints:** DESIGN-01 (YAGNI — no shared-constants module for one duplicated `CATALOG_PATH` line; no premature multi-indicator batching of catalog fetches), DESIGN-02 (`custom_indicators.py` still never imports `chart_indicators.py`'s internals; importing `ParquetDataCatalog`/`DydxSecondSnapshot` is fine, those are shared data types, not chart_indicators internals), READ-01 (keep the CVD replay function under ~30 lines — split the catalog-fetch part into a small helper if needed, since Story 10.3/10.4 will each need their own catalog-fetch-and-bucket logic too and a shared helper may be worth extracting once a second one exists — not before, YAGNI), TEST-01/03 (financial calc, real types not mocks), SIGNAL-01 (store raw, compute signals — this story doesn't change what's stored, only what's computed on read).
- **DATA-01 applies to the live-mode `None` output**: returning `None` for every candle when live is the *correct* way to flag "not available in this mode" — do not substitute a stale/last-known value or a fabricated 0, which would misrepresent CVD as computed when it isn't.

### Project Structure Notes

- Modified: `troll/ml_signals/custom_indicators.py`, `troll/ml_signals/chart_data.py`, `troll/ml_signals/dashboard.py` (7-row figure only), `troll/ml_signals/tests/test_custom_indicators.py`.
- No changes to `troll/ml_signals/book_features.py`, `troll/ml_signals/indicators.py`, `troll/ranking_engine/`, or the indicator-picker JS (Story 10.1 already built the histogram/oscillator rendering and category grouping this story's entry uses as-is).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 10, Story 10.2] — authoritative AC source.
- [Source: _bmad-output/implementation-artifacts/10-1-custom-indicator-catalog-category-tagged-picker-and-histogram-panel-type.md] — the framework this story registers into (`CustomIndicatorSpec`, `ReplayWindow`, dispatch, picker grouping) — read its Dev Notes/Completion Notes before starting.
- [Source: troll/ml_signals/chart_data.py, dashboard.py, indicators.py, candles.py] — all read in full this session.
- [Source: troll/CLAUDE.md] — DESIGN-01/02, READ-01, TEST-01/03, SIGNAL-01, DATA-01.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- First test attempt for `test_cvd_accumulates_buy_minus_sell_volume_per_candle` failed (`[0.0, 0.0]` instead of `[0.0, 5.0]`): the fixed test-suite base timestamp `_TS_NS` isn't 3-second-aligned, so candle `t` values built naively from `first_ns` didn't match the snapshot-bucketing formula's actual bucket keys. Fixed by deriving both the snapshot base timestamp and the candle `t` values from the same explicitly bar-aligned base (`(_TS_NS // bar_ns) * bar_ns`), matching how real candles are always bucket-aligned by construction.

### Completion Notes List

- CVD computes a true per-candle running-cumulative buy-minus-sell volume, bucketing `DydxSecondSnapshot` rows by the exact same `(ts // bar_ns) * bar_ns` formula `candles.py` uses, reducing each bucket via `ml_signals.indicators.trade_aggregates()` (SSOT-01 — no new buy/sell-split logic; this is now the third place using that one shared helper).
- **Historical only, by design** (AC #2): live requests get `None` for every candle. This matches the *old* fixed row's own actual capability — `chart_data.compute_chart_series` was never live either, always driven by the date-range form. Not a regression.
- Retired `chart_data.py`'s `cum_delta` row entirely (computation + the row in `dashboard.py`'s figure), dropping now-unused `deque`/`AggressorSide` imports and the `sorted_trades`/`catalog.trade_ticks()` call that only fed it. `ofi_strategy.py`'s own unrelated `cum_delta` signal (a live-paper Strategy's own state) is untouched — confirmed via grep this is a completely independent implementation, not the same code.
- Panel is now 6 rows (was 7): OFI, imbalance, mid-imbalance, depth, cancel pressure, spread. Rows 1 (OFI) and 5 (cancel pressure) are still fixed rows here — Stories 10.4/10.3 retire those next.
- **Not verified in a real browser** — no display available in this environment, same established caveat as every prior chart-page story.
- Tests: `test_custom_indicators.py` +2 tests (CVD math, live-mode gap), 1 pre-existing test fixed for exact-dict-equality drift. Full suite: 173 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py`).

### File List

- Modified: `troll/ml_signals/custom_indicators.py`
- Modified: `troll/ml_signals/chart_data.py`
- Modified: `troll/ml_signals/dashboard.py`
- Modified: `troll/ml_signals/tests/test_custom_indicators.py`

## Change Log

- 2026-09-08: Implemented CVD as a custom picker indicator (historical-only, per its scope decision), retired the fixed "5-min cumulative delta" row from the chart page's static microstructure panel. Status: ready-for-dev → review.
- 2026-09-08: Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) found and fixed a real DATA-01 gap (snapshot-collection gaps were silently treated as zero volume instead of flagged unknown), plus several docstring/comment accuracy issues and two test-coverage gaps (production dispatch path, figure row-count invariant). 2 items deferred as systemic concerns spanning Stories 10.2-10.4, not CVD-specific. Status: review → done.
