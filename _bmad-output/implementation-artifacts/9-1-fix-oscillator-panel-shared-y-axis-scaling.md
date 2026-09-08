---
baseline_commit: 68ea1127136ae9039d51045cbc6133535c7beae8
---

<!-- Standalone bug-fix story, no epics.md entry -- same precedent as epics 5/6/7
     (sprint-status.yaml's own comments document this precedent). Numbered 9 (not folded
     into Epic 8) because Epic 8 is already marked done in sprint-status.yaml.

     IMPORTANT for the dev agent: at story-creation time, `troll/ml_signals/dashboard.py`
     and `troll/ml_signals/tests/test_dashboard_chart_pan_js.py` had UNCOMMITTED local
     changes on top of baseline_commit (verify with `git diff` before starting) --
     an in-progress refactor of the indicator picker from single-checkbox-per-indicator
     to an add-list where the same indicator can be added multiple times as separate
     instances (`_activeIndicators` items gained an `id` field; `_toggleIndicator` was
     replaced by `_addIndicator`/`_removeIndicator`/`_renderIndicatorTable`;
     `_seriesForIndicator` gained `_paramsMatch` to disambiguate same-name instances).
     This story's fix must be built ON TOP of whatever is in the working tree when dev
     starts, not reverted to baseline_commit -- confirm with the user if the working tree
     looks materially different from what this story describes before proceeding. -->

# Story 9.1: Fix oscillator-panel shared y-axis scaling on the chart page's indicator picker

Status: done

## Story

As a user of the `/chart/{id}` page's indicator picker,
I want each active oscillator-panel indicator to render at a readable scale regardless of what else is active,
so that adding more than one indicator doesn't visually flatten most of them to an invisible line near zero.

## Acceptance Criteria

1. **Reproduction confirmed, root cause is scale, not data.** With two or more `panel: "oscillator"` indicators active whose native output ranges differ by an order of magnitude or more (e.g. `LinearRegression` — price-scale, tens of thousands for a BTC-like instrument — alongside `RelativeStrengthIndex` or `EfficiencyRatio` — 0 to 1), the smaller-range indicator's trace is currently rendered as a flat line pinned near y=0 in `#ind-panel` because every active oscillator trace shares one Plotly y-axis that auto-ranges to the largest series. This story fixes that; it does not touch `chart_indicators.py`'s computation (already verified correct end-to-end — see Dev Notes).
2. **Each active oscillator indicator instance is independently readable**, regardless of how many others are active or what their native ranges are. Small-range and large-range indicators must both show visible variation, not one dominating the shared axis.
3. **No visual clutter added.** The fix must not add a second full axis with its own tick labels/gridlines competing for space per indicator — `#ind-panel` stays one compact panel below `live-chart`, not N stacked sub-panels. (YAGNI/DESIGN-01: a per-indicator sub-panel with its own domain slice is a bigger change than this bug needs; prefer Plotly's own per-trace secondary-axis overlay, `yaxis: 'y2'/'y3'/...` with `overlaying: 'y'`, `visible: false` on axes beyond the first, keeping one shared plot area.)
4. **Hover still shows each trace's real (unscaled) value.** Plotly's hover tooltip reads from the trace's own y-values regardless of which axis it's bound to — do not substitute a normalized/rescaled value into the trace's `y` array to solve this, since that would make hover lie about the indicator's actual value. If the chosen fix does rescale `y` for display, the real value must still surface (e.g. via `hovertemplate` + `customdata`), but the simpler `overlaying:'y'` multi-axis approach (AC #3) avoids this tradeoff entirely by leaving each trace's real `y` values untouched and letting Plotly autorange each axis independently.
5. **Overlay-panel indicators (drawn on `live-chart`, not `#ind-panel`) are out of scope.** They already share a sensible common scale (price), since every overlay indicator's output is itself a price-scale value (moving averages, bands, VWAP, Ichimoku) — this bug is specific to `#ind-panel`'s heterogeneous oscillator outputs.
6. **No regressions.** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_dashboard_chart_pan_js.py ml_signals/tests/test_chart_indicators.py -q` passes.

## Tasks / Subtasks

- [x] Task 1 — Confirm root cause against the current working tree (AC: #1)
  - [x] Re-ran `chart_indicators.replay_indicator()` for all 34 catalog entries against the current working tree (uncommitted multi-instance refactor present) — confirmed unchanged output ranges from story-creation session; the refactor only touches JS-side instance identity/labeling, not the Python computation path. Also corrected the story's earlier "33 catalog entries" note to the actual count: 34 total, 21 oscillator, 13 overlay.
- [x] Task 2 — Per-trace independent y-axis scaling in `_renderOscillatorPanel` (AC: #2, #3, #4)
  - [x] `_renderOscillatorPanel` now assigns each active oscillator **indicator instance** (not each trace) its own axis key (`'y'` for the first, `'y2'`/`'y3'`/... for subsequent instances), via a running `axisN` counter incremented once per instance that has data. A new 3-line helper `_oscillatorAxisLayout(n)` returns the layout entry for axis `n` (`{visible:false}` for axis 1, `{visible:false,overlaying:'y'}` for axis ≥2), added to `layout['yaxis'+n]` only for n>1 (axis 1's `{visible:false}` is set directly on `layout.yaxis`). Every trace's `y` array is untouched — only a `yaxis:` key was added to each trace object.
  - [x] Chose indicator-instance granularity (not per-output-attribute) per the story's own guidance — all of an instance's output attributes (e.g. `LinearRegression`'s 6 outputs) share its one axis. Known remaining gap, not fixed here (out of scope per story guidance): an instance whose own outputs span wildly different scales (e.g. `LinearRegression`'s price-scale `value`/`intercept` vs its small-scale `degree`/`cfo`/`R2`) will still show intra-instance squashing. Not observed as a complaint and not required by any AC.
  - [x] Legend (`name:` per trace, from `_indicatorLabel`, unchanged) still lists every trace distinctly — confirmed by reading the unmodified code path; axis visibility has no effect on legend rendering.
- [x] Task 3 — Tests (AC: #6)
  - [x] Added `test_oscillator_panel_gives_each_active_indicator_its_own_axis` to `test_dashboard_chart_pan_js.py` (new `_OSCILLATOR_AXIS_HARNESS_TEMPLATE`, same Node-harness pattern): stubs `Plotly.react` to capture its `(traces, layout)` arguments, activates two fake oscillator indicators with disparate ranges (0.1–0.3 vs 60,000–60,020, mirroring the real `RelativeStrengthIndex`-vs-`LinearRegression` disparity), and asserts trace 1 gets `yaxis:'y'`, trace 2 gets `yaxis:'y2'`, and `layout.yaxis`/`layout.yaxis2` are both `visible:false` with axis 2 `overlaying:'y'`.
  - [x] `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_dashboard_chart_pan_js.py ml_signals/tests/test_chart_indicators.py -q` — 51 passed, no regressions.
- [x] Task 4 — Manual browser verification (recommended, not blocking per this repo's established caveat — see Dev Notes)
  - [ ] Not performed — no browser/display available in this environment, same caveat every prior Epic 8 story (7.1/8.1/8.2/8.4) documented. Flagged in Completion Notes below as outstanding.

### Review Findings

- [x] [Review][Patch] Primary axis was hidden, contradicting AC #3/Dev Notes ("the first trace can stay on the default 'y'") and regressing the single-active-indicator case's readability (it had a normal visible axis before this story; the fix made it hidden too) [troll/ml_signals/dashboard.py:_renderOscillatorPanel] — fixed: axis 1 now keeps Plotly's normal default (no `visible:false`); only axes 2+ are overlaid/hidden.
- [x] [Review][Patch] `_oscillatorAxisLayout`'s `n===1` branch was dead code (never called with n=1; axis-1 config was hand-duplicated inline in the base `layout` object instead), risking silent drift between the two representations [troll/ml_signals/dashboard.py:_oscillatorAxisLayout] — fixed: removed the dead branch, renamed to `_oscillatorOverlayAxis()` (no `n` param, called only for axes 2+).
- [x] [Review][Patch] No comment explained why axis 1 is special-cased (Plotly addresses its first y-axis as plain `'y'`/`'yaxis'`, never `'y1'`/`'yaxis1'`) — a future "simplification" of the `axisN>1` guard could silently break the primary axis [troll/ml_signals/dashboard.py:_renderOscillatorPanel] — fixed: added an explicit comment stating this constraint.
- [x] [Review][Patch] Single-active-oscillator-indicator case (the most common real case, and the one that never had the scaling bug) had zero test coverage [troll/ml_signals/tests/test_dashboard_chart_pan_js.py] — fixed: added an assertion block covering exactly one active indicator, confirming no `yaxis2` key exists.
- [x] [Review][Patch] No test exercised 3+ simultaneous oscillator indicators, so an off-by-one in the `'y'+axisN`/`'yaxis'+axisN` naming scheme wouldn't be caught [troll/ml_signals/tests/test_dashboard_chart_pan_js.py] — fixed: extended the test to three active indicators, asserting `y3`/`yaxis3` are distinct from `y2`/`yaxis2`.
- [x] [Review][Patch] No assertion pinned the "hover still shows real values" claim stated in the code's own comment — the fix's core no-normalization guarantee was undocumented by a test [troll/ml_signals/tests/test_dashboard_chart_pan_js.py] — fixed: added an assertion that a trace's `y` array retains its original, unscaled values.
- [x] [Review][Patch] Test fixture's `_activeIndicators` entries omitted the `id` field that real production code (`_addIndicator`) always attaches, letting the test drift from the actual runtime shape [troll/ml_signals/tests/test_dashboard_chart_pan_js.py] — fixed: added `id` to the test fixtures.
- [x] [Review][Defer] Stale axis key when the active-oscillator-indicator count shrinks across re-renders (e.g. 3→1, `yaxis3` present in a prior `Plotly.react` call but omitted from the new `layout` object) — real Plotly.react layout-diffing behavior here can't be verified without a live browser session, consistent with this page's existing "not verified in a real browser" precedent (Stories 7.1/8.1/8.2/8.4). `troll/ml_signals/dashboard.py:_renderOscillatorPanel`.
- [x] [Review][Defer] Two active instances of the same indicator with identical name+params both resolve to the same backend data key in `_seriesForIndicator`/`_paramsMatch`, producing duplicate overlapping traces on separate axes — this is a bug in the unrelated, pre-existing multi-instance indicator-picker WIP refactor (not introduced by story 9.1), belongs to whichever story finishes that feature. `troll/ml_signals/dashboard.py:_seriesForIndicator,_paramsMatch`.
- [x] [Review][Defer] No upper bound on the number of oscillator indicators (hence overlaid axes) a user can add via the WIP add-list UI — stems from the unrelated multi-instance refactor's `_addIndicator` having no limit/dedupe, not from this story's axis-scaling fix itself. `troll/ml_signals/dashboard.py:_addIndicator`.
- [x] [Review][Defer] `_indicatorLabel`'s `.attr` suffix is appended unconditionally in the oscillator panel but only when an indicator has >1 output in the overlay panel (`_renderCandleTraces`), a pre-existing inconsistency from when the WIP refactor introduced `_indicatorLabel` — this story's diff only renamed the call site (`a.name`→`_indicatorLabel(a)`), it did not introduce the asymmetry. `troll/ml_signals/dashboard.py:_renderCandleTraces,_renderOscillatorPanel`.

## Dev Notes

- **This bug is specific to `_renderOscillatorPanel`, not the backend.** Verified this session by running `chart_indicators.replay_indicator()` for every one of the 33 `INDICATOR_CATALOG` entries end-to-end through `dashboard._indicators_json()` with default params against synthetic BTC-scale candles (px ~60,000, random walk) — all 33 returned correct, non-empty, correctly-keyed output series. The defect is purely presentational: `_renderOscillatorPanel` (dashboard.py, `_LIVE_CHART_JS`, currently ~line 574 in the working tree — **search by name, not line number**, per every prior Epic 8 story's own warning, doubly true here given the uncommitted refactor already shifted these numbers once this session) pushes every active oscillator indicator's output as a `scattergl` trace into one `traces` array with a single, shared, auto-ranging y-axis (no `yaxis` key set on any trace, meaning they all implicitly bind to the default `'y'` axis).
- **Confirmed this session — actual output ranges (synthetic BTC-scale candles, px~60,000, 300 bars):**
  - `LinearRegression.value`: ~59,700–60,700 (price-scale)
  - `LinearRegression.intercept`: ~59,700–60,700 (price-scale)
  - `LinearRegression.slope`: ~-15 to 18
  - `LinearRegression.degree`: ~-86 to 87
  - `LinearRegression.cfo`: ~-0.12 to 0.10
  - `LinearRegression.R2`: 0–0.98
  - `RelativeStrengthIndex.value`: 0.18–0.92 (this indicator returns a 0–1 fraction, not the conventional 0–100 RSI scale — a fact, not itself a bug, but worth knowing so nobody "fixes" it by multiplying by 100)
  - `MovingAverageConvergenceDivergence.value`: ~-64 to 81
  - `AverageTrueRange.value`: ~33–48 (price-difference scale)
  - `CommodityChannelIndex.value`: ~-249 to 230
  - `EfficiencyRatio.value`: 0.0005–0.999
  - `VerticalHorizontalFilter.value`: 0–0.72
  - Selecting `LinearRegression` alongside any of the 0–1-scale indicators on the current shared-axis panel makes the 0–1 ones render as a flat line at the bottom of the panel — this is what the user is seeing as "almost all of the indicators don't work."
- **Confirmed by direct count (`chart_indicators.INDICATOR_CATALOG`): 21 of 34 catalog entries are `panel: "oscillator"`, 13 are `panel: "overlay"`.** Most of what a user picks from the picker's list lands in the oscillator panel, which is why the bug reads as "almost all" rather than "some."
- **Read before touching anything:** `_renderOscillatorPanel`, `_renderCandleTraces` (the overlay equivalent — out of scope per AC #5, but read it for contrast: it already works correctly because every overlay indicator's output is price-scale), `_refreshActiveIndicators` (the caller), and `_seriesForIndicator`/`_paramsMatch` (uncommitted-refactor additions — read these since they determine which data key each trace's values come from; this story doesn't need to change them). All in `troll/ml_signals/dashboard.py`'s `_LIVE_CHART_JS` module-level string.
- **Do not touch `chart_indicators.py`.** No computation is wrong; `catalog_json()`'s `panel` classification (`overlay`/`oscillator`) is also correct and doesn't need a third category or per-indicator scale metadata for the recommended fix (AC #3's `overlaying:'y'` approach needs no new catalog data — it's a pure rendering-layer fix).
- **Plotly per-trace secondary-axis pattern (the recommended fix, AC #3/#4):** set `yaxis:'y'+n` on the n-th trace (n≥2; the first trace can stay on the default `'y'`), and add matching layout keys `layout['yaxis'+n] = {overlaying:'y', visible:false}` for each n≥2 used. This is a standard, well-documented Plotly.js pattern for "same plot area, independently-scaled series" — do not reach for `make_subplots`-style stacked domains (that's a Python-side Plotly Express/graph_objects pattern anyway; this whole page is hand-built HTML/JS strings, not a Python-rendered figure for this panel) or a JS charting library change (troll's charting-library decision is pinned to Plotly, reconfirmed twice already per epics.md — do not revisit that here).
- **`troll/CLAUDE.md` constraints that apply:** DESIGN-01 (YAGNI — don't build a generalized "axis assignment strategy" abstraction for what's a ~5-line change to one function), READ-01 (keep `_renderOscillatorPanel` under ~30 lines after the change — extract a small helper if the axis-assignment logic makes the function grow past that), TEST-01 (this is genuinely new branching logic — which trace gets which axis — so it needs the JS-harness test in Task 3, this isn't "trivial glue" under TEST-02).
- **No JS test framework in this repo** — reuse `test_dashboard_chart_pan_js.py`'s established pattern (extract the real `_LIVE_CHART_JS` string from `dashboard.py`, stub `document`/`Plotly`/`fetch`/globals, run a small `assert`-only Node harness via `subprocess`). Do not introduce Jest/Mocha/etc. (DESIGN-01).
- **Not verified in a real browser is the norm here, not an exception** — Stories 7.1, 8.1, 8.2, and 8.4 all flagged the same limitation (no display available in this environment). Task 4 is recommended, not blocking, consistent with that established precedent — but flag its outcome explicitly in Completion Notes either way (done or "still needs a human to check in-browser").
- **Uncommitted working-tree state at story-creation time** (see top-of-file note): `git diff ml_signals/dashboard.py ml_signals/tests/test_dashboard_chart_pan_js.py` showed an in-progress multi-instance-indicator refactor not yet committed. This story's diff should layer on top of that, not conflict with or revert it. If dev-story starts and finds the working tree materially different (e.g. already committed, or reverted), re-verify this story's line-number references (none are load-bearing — every reference above says "search by name") and the exact current shape of `_seriesForIndicator`/`_paramsMatch` before proceeding.

### Project Structure Notes

- Single file for the fix: `troll/ml_signals/dashboard.py` (`_LIVE_CHART_JS`'s `_renderOscillatorPanel`). No new Python module, no changes to `chart_indicators.py`, no changes to `dydx_collector/`, `bot_tui/`, `live_paper/`, or `ranking_engine/`.
- Test file: extend `troll/ml_signals/tests/test_dashboard_chart_pan_js.py`.

### References

- [Source: troll/ml_signals/dashboard.py] — `_renderOscillatorPanel`, `_renderCandleTraces`, `_refreshActiveIndicators`, `_seriesForIndicator`, `_paramsMatch` (uncommitted refactor) — all read in full this session, both at `baseline_commit` and against the uncommitted working tree.
- [Source: troll/ml_signals/chart_indicators.py] — `INDICATOR_CATALOG`, `replay_indicator`, `catalog_json` — read in full this session; confirmed correct via direct execution, not just inspection.
- [Source: _bmad-output/implementation-artifacts/8-4-indicator-picker-on-the-chart-page-settings-toolbar.md] — establishes the indicator-picker architecture this bug lives in, the "not verified in a real browser" precedent, and the JS-harness testing pattern this story's Task 3 extends.
- [Source: _bmad-output/implementation-artifacts/8-2-technical-indicator-computation-via-nautilus-trader-indicators.md] — establishes `INDICATOR_CATALOG`/`replay_indicator`, confirming this story correctly scopes the fix away from the computation layer.
- [Source: troll/CLAUDE.md] — DESIGN-01 (YAGNI), READ-01 (function length), TEST-01/TEST-03 (test the new branching logic, no mocking, no new framework).
- [Source: _bmad-output/planning-artifacts/epics.md] — "Charting library decision stands (Story 7.1, reconfirmed 2026-09-06): stay on Plotly" — this story's fix must stay within Plotly's own API, not introduce or evaluate an alternative.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `ruff`/`mypy` not installed anywhere in this environment (no `.venv/bin/ruff`, no `python -m ruff`/`mypy` module) — same gotcha Story 8.4's Debug Log flagged, but this session had no working ruff binary at all, not even the `.venv/bin/ruff` fallback 8.4 found. Did not run automated lint/format; manually checked new/changed lines are ≤100 chars (dashboard.py) and consistent with this file's existing long-line-inside-JS-string convention (test file's harness templates already contain many 140-170+ char lines pre-existing).
- Confirmed via direct execution (not just inspection) that the file had uncommitted local changes (multi-instance indicator-picker refactor) on top of `baseline_commit` before starting — built this fix on top of that working tree as instructed by the story's top-of-file note; did not revert or conflict with it.

### Completion Notes List

- Root cause was purely presentational, confirmed by direct execution: `chart_indicators.replay_indicator()` produces correct values for all 34 catalog indicators; `_renderOscillatorPanel` (dashboard.py's `_LIVE_CHART_JS`) put every active oscillator indicator's trace on one shared, auto-ranging Plotly y-axis, so a price-scale indicator (e.g. `LinearRegression`, ~60,000) squashed any 0-1-scale indicator (e.g. `RelativeStrengthIndex`, `EfficiencyRatio`) to an invisible flat line.
- Fix: each active oscillator indicator instance now gets its own overlaid, hidden Plotly axis (`yaxis:'y'`/`'y2'`/`'y3'`/...), added via a new 3-line `_oscillatorAxisLayout(n)` helper. No indicator's real values are touched — only axis binding — so Plotly's hover tooltip still shows each trace's true (unscaled) value, and the panel stays one compact plot area (no per-indicator sub-panels, no visible extra axis clutter).
- **Known remaining gap, out of scope per story guidance:** a single indicator instance whose own multiple output attributes span very different scales (e.g. `LinearRegression`'s price-scale `value`/`intercept` next to its small-scale `slope`/`degree`/`cfo`/`R2`) will still show intra-instance squashing, since this fix assigns one axis per instance, not per output attribute. Not required by any AC; flagging per the story's own instruction not to scope-creep into it.
- **Not verified in a real browser** — no display available in this environment, same caveat every prior Epic 8 story (7.1/8.1/8.2/8.4) documented. Recommend a manual pass before treating this fully done end-to-end: open `/chart/{id}`, add `LinearRegression` and `RelativeStrengthIndex` (or `EfficiencyRatio`) to the oscillator panel together, confirm both show visible variation and hover shows real values.
- Tests: added `test_oscillator_panel_gives_each_active_indicator_its_own_axis` to `test_dashboard_chart_pan_js.py`. Full relevant suite (`test_dashboard_chart.py`, `test_dashboard_chart_pan_js.py`, `test_chart_indicators.py`) — 51 passed, no regressions.

### File List

- Modified: `troll/ml_signals/dashboard.py`
- Modified: `troll/ml_signals/tests/test_dashboard_chart_pan_js.py`

## Change Log

- 2026-09-08: Fixed oscillator panel's shared-y-axis scaling bug by giving each active oscillator indicator instance (beyond the first) its own overlaid, hidden Plotly y-axis in `_renderOscillatorPanel`; added `test_oscillator_panel_gives_each_active_indicator_its_own_axis` regression test. Status: ready-for-dev → review.
- 2026-09-08: Adversarial code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) found the initial fix hid axis 1 too, contradicting the story's own AC #3/Dev Notes guidance and regressing the single-indicator case's readability — fixed by leaving axis 1 at Plotly's normal default and removing the now-dead `n===1` branch (renamed `_oscillatorAxisLayout`→`_oscillatorOverlayAxis`, called only for axes 2+). Also added test coverage for the single-indicator and 3-indicator cases and a hover-value-integrity assertion, per review findings. 4 items deferred as pre-existing/out-of-scope (see deferred-work.md and this story's Review Findings). Status: review → done.
