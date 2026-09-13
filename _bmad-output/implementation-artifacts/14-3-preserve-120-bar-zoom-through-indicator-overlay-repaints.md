---
baseline_commit: 0bb380b9f4
---

<!-- Standalone bypass-epic story, no epics.md entry -- continues epic-14 (sibling of 14.1/14.2,
     same file: dashboard.py's /chart/{id}), same precedent as epics 5/6/7/9/11 before it
     (sprint-status.yaml's own comments document this precedent). epic-14 was marked "done"
     after 14.1/14.2 shipped; reopened to in-progress for this story since the 120-bar default
     view 14.1 claimed to deliver does not actually hold in a real browser -- see root cause
     below. Created at the user's explicit request ("create story to create this... this was
     supposed to be done but it is not done") after the bug was root-caused and reproduced this
     session, not from a fresh elicitation pass.

     Baseline note: HEAD (0bb380b9f4) is NOT what a dev picking this up will actually see in the
     working tree. This same session left troll/data_api/app.py, troll/ml_signals/candles.py,
     troll/ml_signals/dashboard.py, and 4 test files uncommitted with unrelated fixes (Plotly.js
     served locally instead of CDN; a lean /catalog/candles data_api route replacing a ~12MB
     /catalog/snapshots round-trip that was timing out candlestick loads over the SSH tunnel;
     _get_http_session's timeout raised 10s->60s; a crash guard so _loadDefaultCandleWindow
     never calls Plotly.relayout on a live-chart div that was never plotted; spinners now hide
     on load failure, not just success). None of that touches _renderCandleTraces/_onChartRelayout
     (this story's target functions) or _CANDLE_VISIBLE_BARS/_CANDLE_BUFFER_BARS (untouched,
     still 120/120 from 14.1) -- it's orthogonal, but `git diff` against HEAD will show more than
     this story's own changes. Do not revert or "clean up" that other work. -->

# Story 14.3: Preserve the 120-bar default zoom through indicator-overlay repaints

Status: ready-for-dev

## Story

As a user of the `/chart/{id}` page,
I want the candlestick chart's last-120-bars default zoom (and any pan/zoom I do afterward) to actually stay on screen, even for an instrument that has an active/saved indicator,
so that opening a chart behaves like TradingView's initial view instead of silently reverting to a multi-hour, unusable-at-a-glance span the moment the indicator overlay's own data arrives.

## Acceptance Criteria

1. **Regression guard — no active indicators.** Opening `/chart/{id}` for an instrument with zero active/saved indicators still shows exactly the last `_CANDLE_VISIBLE_BARS` (120) bars as the initial view (this path already works today — do not break it).
2. **The actual bug — an active indicator is present.** Opening `/chart/{id}` for an instrument WITH an active/saved indicator also shows exactly the last 120 bars initially, and **stays that way** after the indicator overlay's own async data fetch (`_fetchIndicatorSeriesForCurrentWindow` → its `.then()` → a second `_renderCandleTraces(data)` call) resolves and re-paints. Today that second paint has no explicit `xaxis.range`, so Plotly autoranges to the full 240-bar buffered window and silently wipes the zoom — proven this session with a real node harness driving the actual `_LIVE_CHART_JS` against a correctly-shaped mock backend (SMA active): captured Plotly call sequence was `[react(AUTORANGE), relayout(narrow-to-120), react(AUTORANGE again)]` — the final on-screen state was always the full window, never 120 bars.
3. **Generalizes to every post-zoom repaint, not just the initial-load race.** Adding/removing an indicator or changing an indicator's params (`_addIndicator`/`_removeIndicator`/`_updateIndicatorParam`, each of which calls `_refreshActiveIndicators()` → `_renderCandleTraces()`) after the chart already has an established zoom (the 120-bar default, or a user's own drag/scroll-zoom) must not reset that zoom either — same root cause, same fix.
4. **A genuine user pan/zoom remains the new "current range" going forward.** This fix must track whatever range is *currently* intended (120-bar default initially, then wherever the user drags/zooms to), not pin the view to the initial 120 bars forever. `_onChartRelayout`/`_wireChartRelayout` already fire for every relayout on `live-chart` — both the initial programmatic zoom from `_loadDefaultCandleWindow` and any subsequent user drag — see Dev Notes for the exact hook point.
5. **A bar-size change, mode switch, explicit date-range Load, or clicking Live starts fresh, not stuck on a stale range.** `onBarChange`, `setCoinMode`, `resetCoinLive`, and `loadCoinDateRange` must clear the tracked range so a new window/bar-size/mode doesn't inherit a range computed for a different window (e.g. a 60s-bar range showing as a nonsensical slice after switching to 1h bars).
6. **No regressions elsewhere.** Lines mode (no bar concept, not driven by `_loadDefaultCandleWindow`), cross-panel pan/zoom sync (`_syncChartXRange`/`_SYNCED_CHART_IDS`, ind-panel/micro-panel), and the existing pan-triggered scroll-back refill (`_maybeLoadOlder`/`_loadOlderChunk`) are all unaffected by this change (verify, don't just assume).
7. **Verified in a real browser, breaking this feature's own documented history.** Every prior chart-page story (7.1/8.1/8.2/8.4/9.1/11.1/12.2/14.1/14.2 — see each story file's Dev Notes) explicitly recorded "not verified in a real browser, no display in this environment" as a known gap. This story must not repeat it: this session already proved a working method (`google-chrome --headless=new --disable-gpu --no-sandbox --run-all-compositor-stages-before-draw --enable-logging=stderr --v=1 --dump-dom` against a real running `python -m ml_signals.dashboard` process, grepping stderr for `INFO:CONSOLE` to catch JS errors and grepping the dumped DOM for rendered Plotly layer classes) — reuse it here, with a real active indicator configured, and confirm both zero console errors and the rendered chart's actual visible span.
8. **Automated regression coverage.** A node-harness test (extend `ml_signals/tests/test_dashboard_chart_pan_js.py`, same style as its existing `_HARNESS_TEMPLATE`/`_CANDLE_LOAD_FAILURE_HARNESS_TEMPLATE`) reproduces the indicator-active scenario and asserts the *final* `Plotly.react`/`Plotly.relayout` call for `'live-chart'` carries the 120-bar range, not an autorange — i.e., a harness that would have caught this bug before it shipped. A second case covers AC #4 (user drag survives a later indicator repaint).
9. **Full suite green.** `cd troll && PYTHONPATH=. python -m pytest ml_signals/tests data_api/tests -q` — 0 new failures (one pre-existing, unrelated failure, `test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`, is already open and out of scope for this story — do not fix it here, do not let it block this story's completion).

## Tasks / Subtasks

- [ ] Task 1 — Track the chart's current intended x-axis range (AC: #3, #4)
  - [ ] Add a new state variable to `_LIVE_CHART_JS` (alongside the other page-level `var`s near `_CANDLE_VISIBLE_BARS`), e.g. `var _liveChartXRange=null;` — null means "no zoom established yet, autorange as today."
  - [ ] In `_onChartRelayout(sourceId, ev)` (dashboard.py, ~line 364), immediately after the existing `if(!rng)return;` guard, add: `if(sourceId==='live-chart')_liveChartXRange=rng;`. This function already fires for BOTH a genuine user drag on `live-chart` AND `_loadDefaultCandleWindow`'s own programmatic `Plotly.relayout('live-chart', ...)` call (confirmed: `_wireChartRelayout()` attaches the `'plotly_relayout'` listener inside `_renderCandleTraces()`, which runs on the very first base paint — i.e. *before* `_loadDefaultCandleWindow`'s relayout call fires — so that relayout already flows through `_onChartRelayout` today). This one line is the only change needed to capture both cases; do not duplicate range-tracking logic inside `_loadDefaultCandleWindow` itself.
  - [ ] Note: `_onChartRelayout` also sets `_coinPanning=true`/clears `timer`/calls `_reconcilePriceBasisOnFirstPan()` as pre-existing side effects of any `live-chart` relayout, including the initial programmatic one. These already happen today, unrelated to this fix, and are safe no-ops in the default-load context (`_reconcilePriceBasisOnFirstPan` bails immediately because `_coinHistStart` is already set by `_loadDefaultCandleWindow` before the relayout fires; `timer` isn't running on a frozen historical load). Do not touch this existing logic — just add the one range-capture line.

- [ ] Task 2 — Thread the tracked range into every candlestick repaint (AC: #2, #3)
  - [ ] In `_renderCandleTraces(indicatorData)` (dashboard.py, ~line 664), change the hardcoded `xaxis:{type:'date',rangeslider:{visible:false}}` passed to `Plotly.react('live-chart', ...)` so it includes `range:_liveChartXRange` only when `_liveChartXRange` is non-null (build the xaxis object conditionally, e.g. `var xaxisLayout={type:'date',rangeslider:{visible:false}}; if(_liveChartXRange)xaxisLayout.range=_liveChartXRange;` then use `xaxis:xaxisLayout` in the `Plotly.react` call) — do NOT pass `range:null`, Plotly may not treat that as "no range" the same as an absent key.
  - [ ] This makes every `_renderCandleTraces()` call — the initial base paint (still autoranges, since `_liveChartXRange` is null before the first relayout), the indicator-data repaint, and any `_addIndicator`/`_removeIndicator`/`_updateIndicatorParam`-triggered repaint — preserve whatever range is currently tracked.

- [ ] Task 3 — Clear the tracked range on genuine "start fresh" actions (AC: #5)
  - [ ] `onBarChange()` (~line 225): set `_liveChartXRange=null` before triggering the refetch (bar-size changed, any previously tracked range is for the old bar size and no longer meaningful).
  - [ ] `setCoinMode()`: same — clear on any mode switch.
  - [ ] `resetCoinLive()` (~line 248): clear when the user clicks Live (fresh live window, no zoom yet).
  - [ ] `loadCoinDateRange()`: clear on an explicit date-range Load (a fresh window the user explicitly chose has no reason to inherit a stale zoom from before).
  - [ ] Verify `_loadDefaultCandleWindow()` itself needs no change beyond what Task 1 already covers — its own relayout call is what SETS `_liveChartXRange` in the first place via the Task 1 hook.

- [ ] Task 4 — Regression tests (AC: #8)
  - [ ] Extend `ml_signals/tests/test_dashboard_chart_pan_js.py` with a new harness (mirror `_CANDLE_LOAD_FAILURE_HARNESS_TEMPLATE`'s keyed-`_els`/mocked-`Plotly` style) that: mocks `/data/indicators/catalog`, `/data/coin/{id}/indicator-config` (returning one active indicator, e.g. SMA), `/data/coin/{id}/candles`, and `/data/coin/{id}/indicators` (the per-window indicator-series fetch) with real-shaped responses (`{SMA:{value:[{time,value}, ...]}}` for the indicators-series response — get this shape right, a wrongly-shaped mock silently no-ops the overlay loop instead of testing it); calls `_fetchIndicatorCatalog()` then `_loadDefaultCandleWindow()` in parallel (mirrors real `init_script` — neither awaits the other); after both settle, asserts the final captured `Plotly.react`/`Plotly.relayout` call for `'live-chart'` carries the 120-bar range.
  - [ ] Add a second case: after the above settles (zoom preserved), simulate a user drag (fire the `'plotly_relayout'` handler directly with a different range, as the existing `_HARNESS_TEMPLATE` already does for its own relayout assertions) then trigger another indicator repaint (e.g. call `_addIndicator` or resolve another indicator-data fetch) — assert the user's dragged-to range survives, not the original 120-bar default (AC #4).
  - [ ] Run the full suite: `cd troll && PYTHONPATH=. python -m pytest ml_signals/tests data_api/tests -q`.

- [ ] Task 5 — Real-browser verification (AC: #7)
  - [ ] Start the dashboard locally against real (or realistically seeded) data with at least one instrument that has a saved active indicator (use the indicator picker's Save button on a running instance, or seed `CHART_INDICATOR_CONFIG_PATH` directly).
  - [ ] `google-chrome --headless=new --disable-gpu --no-sandbox --virtual-time-budget=<generous, 40000+> --run-all-compositor-stages-before-draw --enable-logging=stderr --v=1 --dump-dom "http://127.0.0.1:<port>/chart/<iid-with-active-indicator>"`, redirecting stderr to a log file.
  - [ ] Grep the log for `INFO:CONSOLE` — must be empty (no JS errors, matching this session's verification of the crash-guard/spinner fixes).
  - [ ] Confirm the dumped DOM's rendered Plotly layer count is non-zero (`grep -c "boxlayer\|scatterlayer\|candlestick\|ohlclayer"`) and, ideally, inspect the actual `xaxis.range`/tick span in the dumped SVG or via a small follow-up script — document exactly what was checked and the result in Dev Notes, since "it rendered something" alone doesn't prove the span is 120 bars, not 240.

## Dev Notes

- **Root cause, precisely.** `_renderCandleTraces()` is the sole function backing every `Plotly.react('live-chart', ...)` call in candlestick mode — the initial base paint, the indicator-overlay data repaint, and every indicator add/remove/param-update repaint. It has never carried an explicit `xaxis.range`, so every single call autoranges to fit whatever trace data it's given (the full buffered 240-bar window). `_loadDefaultCandleWindow()`'s `Plotly.relayout('live-chart', {'xaxis.range':[...]})` call narrows the view *after* the base paint — correctly, when nothing repaints afterward — but is silently undone the moment anything else calls `_renderCandleTraces()` again, which happens routinely whenever the instrument has an active/saved indicator (a completely normal, common case — the whole point of the existing indicator-picker feature, Story 8.4/Epic 10). This is why 14.1 "worked" in isolated testing (no indicator active) but not in general use.
- **Why every prior chart-page story missed this:** every one of 7.1/8.1/8.2/8.4/9.1/11.1/12.2/14.1/14.2 explicitly documented "not verified in a real browser" in its own Dev Notes. A node harness *can* catch this (this session proved it, see AC #2's exact reproduction), but only if it specifically simulates an active indicator plus the async timing — the existing 14.1/14.2 harness coverage never did. AC #7/Task 5 exist specifically to break this pattern going forward, not just for this one bug.
- **Implementation is small and precise — resist scope creep.** This is a ~10-15 line change (one new state variable, one line in `_onChartRelayout`, a conditional `xaxis.range` in `_renderCandleTraces`, four one-line resets). Do not build a generic "range persistence" abstraction across all three synced panels (`ind-panel`/`micro-panel` don't have this exact bug — they're synced *from* `live-chart`'s range via `_syncChartXRange`, which already applies `Plotly.relayout` with an explicit range on every sync, not an autoranging `Plotly.react` — verify this holds, but don't build shared machinery for panels that don't need it; DESIGN-01/ponytail YAGNI).
- **Related, unconfirmed interaction worth checking during Task 5 (not a required fix unless found broken):** `Plotly.relayout()` fires a `'plotly_relayout'` DOM event even when called programmatically, so `_loadDefaultCandleWindow`'s own initial zoom call likely already triggers `_onChartRelayout('live-chart', ev)` → `_syncChartXRange('live-chart', rng)` → pushes the narrow range onto `ind-panel`/`micro-panel` too (guarded by their own `!targetEl.data||!targetEl.data.length` check, so only if already plotted). If the candlestick's zoom gets wiped by the indicator-repaint bug (pre-fix) or preserved (post-fix), check whether `ind-panel`/`micro-panel` stay in sync either way — if they silently diverge from `live-chart`'s span, that's a related but separate visual bug, worth a one-line note in Completion Notes, not a blocker for this story unless it's a regression this fix specifically introduces.
- **Baseline / uncommitted context:** see this file's leading HTML comment — several unrelated fixes from this session (Plotly.js local serving, a lean `/catalog/candles` data_api route replacing a slow ~12MB snapshot round-trip, an internal HTTP timeout raise, a crash guard for `Plotly.relayout` on an unplotted div, spinner-hide-on-failure) are uncommitted in the working tree. None touch this story's target functions (`_onChartRelayout`, `_renderCandleTraces`, `onBarChange`/`setCoinMode`/`resetCoinLive`/`loadCoinDateRange`) or `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS` (still 120/120). Leave that other work as-is.
- **`troll/CLAUDE.md` constraints that apply:** DESIGN-01 (no generic range-persistence abstraction beyond what's needed), READ-01 (functions stay under ~30 lines — this fix doesn't grow any function past that), TEST-01 (financial/interactive-chart logic gets tests — Task 4).

### Project Structure Notes

- To modify: `troll/ml_signals/dashboard.py` (`_LIVE_CHART_JS`: new `_liveChartXRange` var, `_onChartRelayout`, `_renderCandleTraces`, `onBarChange`, `setCoinMode`, `resetCoinLive`, `loadCoinDateRange`).
- To modify: `troll/ml_signals/tests/test_dashboard_chart_pan_js.py` (new harness cases per Task 4).
- No changes expected to `data_api/`, `chart_data.py`, `candles.py`, `_build_chart_page_html`'s HTML/CSS, or any Python-side handler — this is purely a client-side JS state-tracking fix.

### References

- [Source: troll/ml_signals/dashboard.py] `_renderCandleTraces` (~line 664), `_onChartRelayout` (~line 364), `_loadDefaultCandleWindow` (~line 301), `_wireChartRelayout` (~line 412), `_syncChartXRange`/`_SYNCED_CHART_IDS` (~line 730), `onBarChange`/`resetCoinLive` (~line 225/248) — all read and analyzed this session.
- [Source: _bmad-output/implementation-artifacts/14-1-chart-default-120-bar-window-with-scroll-back-preload.md] — origin of `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS`/`_loadDefaultCandleWindow`; its own Dev Notes document the "never verified in a real browser" pattern this story's AC #7 breaks.
- [Source: _bmad-output/implementation-artifacts/14-2-async-chart-page-load-with-panel-spinners.md] — `_renderMicroPanel`/`_SYNCED_CHART_IDS` context, same "never verified in a real browser" note.
- Session evidence (this conversation, not yet a committed artifact): a real node harness reproducing the exact `[react(AUTORANGE), relayout(narrow), react(AUTORANGE again)]` sequence against the actual extracted `_LIVE_CHART_JS`; a real headless-Chrome run against a real running dashboard process confirming the broader candlestick-loading crash fix (a separate, already-fixed bug) with zero console errors.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Change Log

- 2026-09-13: Story created. Root cause identified and reproduced (node harness + real headless-Chrome session evidence, not yet formalized as committed test code) for why Story 14.1's 120-bar default view is silently undone whenever the chart's instrument has an active/saved indicator. Status: backlog → ready-for-dev.
