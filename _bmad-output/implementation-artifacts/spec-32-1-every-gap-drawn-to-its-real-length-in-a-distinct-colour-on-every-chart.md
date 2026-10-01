---
title: 'Story 32.1: Every gap drawn to its real length, in a colour nothing else uses, on every chart'
type: 'feature'
created: '2026-09-30'
status: 'done'
final_revision: '8208b8b2f02e18866431fff63c62bc9a49a7769f'
baseline_revision: '1b09f488251dfb4833daa609075f3b67c8ab33b8'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** A hole in the data takes one chart slot, whatever its length. The backend's `with_gap_markers` and the Lines-mode `_gap_row` emit one row per hole, and the five frontend page seams add one whitespace point each. So a 6-hour outage on a 1m chart looks like a single missing candle (DATA-07 violation).

**Approach:** Emit one whitespace row per missing interval on the backend and at every frontend seam, capped by one mirrored constant `MAX_GAP_ROWS_PER_GAP = 720`. Paint every gap slot with a new `GapPrimitive`, using the dedicated `--chart-gap` colour, on the price, volume and indicator panes. The price pane labels each run with "no data · <duration>", and the legend shows the same text under the crosshair.

## Boundaries & Constraints

**Always:**
- **Gap slots stay whitespace.** A gap slot is native whitespace (`{ time }` on the frontend; all-`None` rows with the schema unchanged on the backend). There is never a fabricated OHLC, value or volume (AD-F6).
- **Slot times.** Gap rows go at `earlier + k*interval` for k = 1, 2, … while the time is `< later`, capped at `MAX_GAP_ROWS_PER_GAP`. A hole longer than the cap emits exactly the cap's rows, contiguous from the hole's start.
- **Lines mode.** The `SNAPSHOT_GAP_THRESHOLD_MS = 2500` trigger is kept, and a triggered hole emits one row per missing second. Spacing of 2 s is still not a gap.
- **One constant.** The cap lives in `views/chart_series.py` with a `Known limit:` comment. The ceiling is (limit − 1) × cap rows per page. The upgrade path is a gap row carrying `span_ms`, drawn as one wide band. The frontend mirrors the cap in `lib/gaps.ts`, and a test on each side asserts 720.
- **Shared seam helper.** Every frontend seam builds its run through `gapRun(afterExclusive, beforeExclusive, stepSeconds, cap)`.
- **Gap runs come from the price series only.** Runs are derived from the candles in Candles mode, or from the Lines series in Lines mode. They are never derived from an indicator's own whitespace, because warm-up is not a gap.
- **Colour.** `--chart-gap` is a new chart-only token in `theme.css` that no other code reads. Nothing else uses its colour.
- **Standards.** Keep the existing TEST-04 test style. Add no new dependency.

**Block If:**
- A consumer that skips whitespace starts drawing or counting a gap slot, and fixing it requires changing that consumer's semantics beyond the skip.

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Build a status bar. None exists (verified: the only crosshair readout is `legend.ts`), so the legend is the crosshair readout for this story.
- Delete `useIndicatorSeries.ts`. It is dead code, but the AC names it, so it moves to the helper and gets a test.
- Change the Story 32.2–32.4 scope: pane heights, the volume toggle, legend buttons, the light palette.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two-bar hole | bars at 0, 60 s, 240 s (1m) | gap rows at 120 s and 180 s | none |
| Over the cap | 1m bars 0 and 24 h | 720 rows at 60 s … 43 200 s, then the real row | none |
| Lines hole | seconds at 0 and 5 s | rows at 1, 2, 3, 4 s | none |
| Lines 2 s spacing | seconds 0 and 2 s | no gap row | none |
| Lines cursor after hole | cursor second is preceded by a hole | the page ends on a real row, with no trailing gap rows | snapshots are filtered by `ts < before` before rows are built |
| Seam across pages | newest older bar 0, boundary 300 s (1m) | whitespace at 60, 120, 180, 240 s | none |
| Label | uncompressed run of 5 × 1m | "no data · 5m" | none |
| Compressed label | 720-slot run whose next real bar is 3d 4h later | "no data · 3d 4h (compressed)" | none |

</intent-contract>

## Code Map

- `platform/views/chart_series.py` -- `with_gap_markers` (:546) and `price_series_rows`/`_gap_row` (:598–655), `snapshot_series_page` filter (:716), module docstring (:34).
- `platform/views/tests/test_chart_series.py` -- gap tests at :159, :185, :194, :205.
- `platform/data_api/tests/test_indicator_series.py:236` and `test_candles.py:169` -- one-row-per-hole assertions.
- `platform/views/live_candles.py` -- the alert feed (`_notify_observers` → `AlertEngine.on_bar`). It carries only real closed bars.
- `platform/frontend/src/hooks/{useCandles,useIndicatorSeries,useSnapshotSeries,usePickerIndicatorValues}.ts` -- the five seam sites. `REFILL_MARGIN_BARS` is in `useCandles.ts:23`.
- `platform/frontend/src/components/chart/primitives/VerticalMarkerPrimitive.ts` -- the template for `GapPrimitive`. `VolumeProfilePrimitive.test.ts` shows the fake-canvas test style.
- `platform/frontend/src/components/chart/LightweightChart.tsx` -- series creation (candles :487, lines :517, pane series :611), the legend subscription (:1004), primitive attach pattern.
- `platform/frontend/src/components/chart/legend.ts` -- `formatLegendValue` "—" (:22), `valueAt` (:35).
- `platform/frontend/src/theme.css` -- tokens. `paneColors.ts`'s `cssVar` reads them.
- Whitespace-skipping consumers, each with its test: `lib/volumeProfile.ts`, `lib/sessionProfile.ts`, `primitives/MeasurementPrimitive.ts`, `legend.ts`, `hooks/useReplay.ts`, `pages/HistoryPage.tsx` (no test yet).
- `platform/frontend/src/pages/docs/kbData.ts` (no chart section yet; `fix-flatline` at :170), `platform/CLAUDE.md` DATA-01 (line 51), `_bmad-output/implementation-artifacts/spec-21-x-candlestick-chart-correctness.md:72`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/views/chart_series.py` -- Add `MAX_GAP_ROWS_PER_GAP = 720` with its Known-limit comment, plus one `_gap_times(earlier_ms, later_ms, interval_ms)` helper. `with_gap_markers` emits `{"t": g}` for each gap time. `price_series_rows` emits `_gap_row(g)` for each gap time at a 1000 ms interval, only when the threshold is exceeded. `snapshot_series_page` filters snapshots (not rows) by `ts < before`. Rewrite the docstrings. -- Makes the backend emit the gap run.
- [x] `platform/views/tests/test_chart_series.py`, `platform/data_api/tests/test_indicator_series.py`, `platform/data_api/tests/test_candles.py` -- Update the tests to one row per interval. Add cases for the cap value, a hole over the cap, a two-bar hole, the Lines per-second run, 2 s not being a gap, and no trailing gap at a Lines cursor. Add one test showing that candles, indicator series and indicator values return identical gap times for one fixture window (collection outage). -- Backend AC.
- [x] `platform/views/tests/test_live_candles.py` (or the existing live-candles test file) -- A batch after a long capture hole calls an observer's `on_bar` only with real bars that each have a numeric `c`. -- Alert-evaluation consumer.
- [x] `platform/frontend/src/lib/gaps.ts` (+ `gaps.test.ts`) -- Add:
  - `MAX_GAP_ROWS_PER_GAP = 720`
  - `gapRun(...)`, returning the times
  - `findGapRuns(data, isReal)`, returning `{times, durationSeconds, compressed}` for each whitespace run that has a real point before it. The step is `times[0] - prevReal`, the duration is `(nextReal ?? last + step) - prevReal - step`, and `compressed` is `count >= cap && nextReal - last > step`.
  - `formatGapDuration(seconds)`, giving the two largest non-zero units of d/h/m/s, e.g. "5m", "1m 30s", "3d 4h".
  - `gapLabel(run)`.
  -- The one gap vocabulary for the frontend.
- [x] The four hooks plus their tests -- Replace each seam with `gapRun`. Snapshots keep the 2.5 s threshold and use step 1, and the stale `_SNAPSHOT_GAP_THRESHOLD_MS` comment gets fixed. Tests assert the full run at each seam (candles prepend and `mergeByTime`, snapshots, picker values). Add a new `useIndicatorSeries.test.ts`. Add a refill test where the visible logical `from` lands inside a long gap run. -- Seam AC.
- [x] `platform/frontend/src/components/chart/primitives/GapPrimitive.ts` (+ test) -- Build it like `VerticalMarkerPrimitive`, with `setRuns(runs)`, `zOrder` `bottom` and colour from the constructor.
  - Each slot is drawn at the `timeToCoordinate` x as a translucent fill of width `max(1px, barSpacing × 0.8)` at full pane height.
  - When a `label` option is set, it draws `gapLabel(run)` at the top of the pane, at the first slot.
- [x] `platform/frontend/src/components/chart/LightweightChart.tsx` (+ test) -- Compute `findGapRuns` from the candle data or from a Lines series. Attach one labelled `GapPrimitive` to the price host series (candle series, or the first Lines series). Attach one unlabelled primitive to the first series of each non-overlay pane (volume and indicators). Update the runs whenever the data changes, and detach them on mode flip and pane removal. Pass a slot-time → run lookup to `renderLegends`. -- Paints every pane.
- [x] `platform/frontend/src/components/chart/legend.ts` (+ test) -- When the crosshair time is a gap slot, each row reads `gapLabel(run)` instead of "—". -- Crosshair readout.
- [x] `platform/frontend/src/theme.css` -- Add a chart-token block with `--chart-gap: #ff9100` (used only by GapPrimitive). The 16 raw `--vga-*` colours stay untouched.
- [x] Consumer tests: `lib/volumeProfile.test.ts`, `lib/sessionProfile.test.ts`, `primitives/MeasurementPrimitive.test.ts`, `legend.test.ts`, `hooks/useReplay.test.ts`, and a new `pages/HistoryPage.test.tsx` -- Each gets one case that feeds a 720-slot whitespace run. Results equal the run-free input, and no gap slot is counted or stepped onto.
- [x] `platform/frontend/src/pages/docs/kbData.ts`, `platform/CLAUDE.md` DATA-01, `spec-21-x-candlestick-chart-correctness.md:72` -- Document gap drawing and the cap (MR4). Close the backlog entry with this story's key.

**Acceptance Criteria:**
- Given a hole of n ≤ 720 missing intervals, when any bar endpoint or Lines page renders it, then the chart shows n `--chart-gap` placeholder slots on the price pane, the volume pane and every indicator pane, aligned slot for slot.
- Given the crosshair over a gap slot, when the legend renders, then each legend row shows "no data · <duration>" instead of "—".
- Given the verification commands, when they run, then all pass with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 10 (high 1, medium 4, low 5)
- defer: 0
- reject: 5 (low 5)
- addressed_findings:
  - `[high]` `[patch]` A forming live bar after a hole was painted next to the last bar with no gap slots until it closed. Fix: `useCandles.openGapTo(time)` appends the whitespace run up to the forming bar, and `ChartPage` calls it on every live-bar time (tests in `useCandles.test.ts`, `ChartPage.test.tsx`).
  - `[medium]` `[patch]` `mergeByTime` let an incoming gap slot overwrite a real bar the chart already held, and that now happens across a whole run. Fix: incoming whitespace never replaces a real point (test).
  - `[medium]` `[patch]` `GapPrimitive` laid out every slot of every run on each redraw. Fix: it culls to `timeScale().getVisibleRange()` (test).
  - `[medium]` `[patch]` A run whose first slot was scrolled off lost its label, including "(compressed)". Fix: the label rides the first visible slot and is clamped at x ≥ 0 (test).
  - `[medium]` `[patch]` Stepping fractional seconds at the Lines seam could add a stray slot a few ulps before the boundary. Fix: the seam is built in integer ms, like the backend `_gap_times` (test).
  - `[low]` `[patch]` Labels overprinted each other on many short holes. Fix: a label that would overlap the previous one is skipped, and its fill is still drawn (test).
  - `[low]` `[patch]` `formatGapDuration` skipped zero units ("1d 30s"). Fix: the second unit is the one directly below the largest, or nothing (test).
  - `[low]` `[patch]` The cap was mirrored by value only. Fix: `frontend/scripts/gap-cap.test.mjs` reads both definitions and asserts they are equal (wired into `test:codegen`).
  - `[low]` `[patch]` The KB said the gap bar is drawn "on top", but it uses zOrder bottom. Fix: the wording is corrected.
  - `[low]` `[patch]` The label test only checked the first label's x loosely. Fix: it now asserts both label positions exactly.

### 2026-09-30 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4 (high 0, medium 3, low 1)
- defer: 0
- reject: 11 (low 11)
- addressed_findings:
  - `[medium]` `[patch]` Live path doubled the cap. `mergeByTime` stepped the hole-fill from prev's last point even when that was the last slot of the run `openGapTo` had already opened, so a hole of 3 days took 1440 slots live but 720 after a reload. Fix: the run starts at prev's last *real* point (`lastRealTime`), and `openGapTo` is idempotent (it steps from the last real bar and appends only the slots past history's end). Test in `useCandles.test.ts`.
  - `[medium]` `[patch]` A capped trailing run up to the forming live bar (not in `data`, drawn with `update()`) read "no data · 12h" with no "(compressed)" marker. Fix: `findGapRuns(..., realAfterEnd)` closes a trailing run at a real point past the data's end, and `LightweightChart` passes the live bar's time. Tests in `gaps.test.ts` and `LightweightChart.test.tsx`.
  - `[medium]` `[patch]` When the socket seeded the forming bar before the REST page landed, `openGapTo` returned early and its effect never re-ran, so the hole stayed hidden until the bar closed. Fix: the `ChartPage` effect also depends on `historyLoaded`, which is safe now that `openGapTo` is idempotent. Test in `ChartPage.test.tsx`.
  - `[low]` `[patch]` The gap label of a run whose first visible slot was at the right edge ran past the canvas and was clipped. Fix: the label is clamped inside the pane at both edges. Test in `GapPrimitive.test.ts`.

## Design Notes

Gap runs are computed once from the price series and pushed to every pane's primitive. This keeps the panes aligned and never mistakes indicator warm-up whitespace for a data hole. There is one primitive per pane, not one per series, so translucent fills never stack. A compressed run is detected purely from spacing (the cap is reached and the next real point is more than one step past the last slot), so no new wire field is needed.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests -q` -- expected: pass, except for the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.

## Auto Run Result

**Summary:** A follow-up review of Story 32.1. The earlier result: every hole takes one chart slot per missing interval (per bar on candles, volume and indicator panes; per second in Lines mode), capped at `MAX_GAP_ROWS_PER_GAP = 720`, with the cap mirrored and cross-checked. The slots stay native whitespace (AD-F6), `GapPrimitive` paints them in `--chart-gap`, and the price pane and legend read "no data · <duration>". This pass fixed four live-edge defects so that the live chart matches a reload.

**Files changed (this pass):**
- `platform/frontend/src/hooks/useCandles.ts` (+ test): `mergeByTime` steps from the last real point, and `openGapTo` is idempotent (`lastRealTime`). Holes over the cap keep exactly one capped run live.
- `platform/frontend/src/lib/gaps.ts` (+ test): `findGapRuns` takes an optional `realAfterEnd` to close a trailing run.
- `platform/frontend/src/components/chart/LightweightChart.tsx` (+ test): passes the forming live bar's time into `priceGapRuns`.
- `platform/frontend/src/pages/ChartPage.tsx` (+ test): `openGapTo` re-runs once history lands.
- `platform/frontend/src/components/chart/primitives/GapPrimitive.ts` (+ test): the label is clamped inside the pane at the right edge.

**Review:** 4 patches applied (3 medium, 1 low), 0 deferred, 11 rejected. The rejected findings:
- Lines-mode panes painted with second-resolution runs. The spec says runs come from the price series only, and the pane shares one time scale with it.
- The label overlapping the HTML legend. Cosmetic, and Story 32.3 reworks the legend.
- The legend going stale under a still cursor.
- Cap cross-check regex brittleness, and no cross-check on the pre-existing 2.5 s threshold.
- Lines 2.6 s spacing emitting two slots. This follows the spec's exact slot formula.
- Worst-case page size. This is the documented Known limit.
- The false positive in spacing-based "compressed" detection.
- Whole-second ms/float round-trip in the picker seam. Exact for minute bars.
- A malformed candle labelled as a gap. This is pre-existing behaviour, and the candle is logged.
- NaN durations. Unreachable.
- Test gaps and doc claims that duplicate the findings above.

**Verification:**
- `cd platform/frontend && npm test && npm run lint && npm run build`: 417 vitest and 6 node tests pass with no test warnings. Lint shows the same 3 warnings as baseline, and the build is clean.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. views/tests data_api/tests -q`: 400 passed, 9 failed. The 9 failures are the same pre-existing redis/pubsub failures as the baseline.

**Residual risks:**
- A worst-case page carries (limit − 1) × 720 gap rows. This is the documented Known limit, and the upgrade path is a `span_ms` row.
- `GapPrimitive` drawing (and the label/legend overlap at the top-left) has been checked only with fake-canvas unit tests, never visually in a browser.
