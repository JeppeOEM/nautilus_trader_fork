---
baseline_commit: 06219946425ea7faf184dc3df86b24d62c2b32ea
---

# Story 18.10: Placement and operation-parity pass

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want every tool in the same relative slot/group TradingView uses, and every interaction to behave the same way,
so that the chart feels familiar even with a fully custom visual style.

## Acceptance Criteria

1. **Top toolbar clusters, in order: [symbol+timeframe] [chart type] [indicators+fit+jump] [theme]** — reconciled against this app's real navigation model (binding resolution, not left open): the "symbol" slot is a **read-only label + back-to-Rankings link** (not a free symbol/timeframe picker — coin selection already happens on the Rankings/screener page); a **real timeframe selector** is added only if multi-timeframe viewing is explicitly wanted (today's `BAR_SECONDS` is a fixed constant); **no theme toggle** is added (Story 15.9 deliberately fixed the visual identity, no light/dark switch).
2. **Left toolbar clusters, in order: [cursor, crosshair toggle] [line, horizontal line, measurement]** — matches Stories 18.1-18.3's tool ordering; add a crosshair toggle if one doesn't already exist independent of the drawing tools.
3. **Every §A8.2 operation-parity behavior is verified against the real, running app** (not a screenshot/visual comparison) — pan, zoom (time+price axes), fit-view, jump-to-latest, crosshair readout, pane resize, indicator add/configure/toggle/remove, each drawing tool's click/drag mechanics, `Esc`-cancel, replay entry/step/goto, alert creation (deferred to Epic 20 — verify only once that epic ships).
4. **This story is the last one in Epic 18** — it audits and reconciles everything Stories 18.1-18.9 built, it does not introduce new chart features.

## Tasks / Subtasks

- [x] Task 1 — Top toolbar reconciliation (AC: #1)
  - [x] Confirm/build: symbol slot renders `{instrumentId}` as a link back to `/` (Rankings), grouped visually apart from a chart-type toggle (already exists: Candles/Lines buttons), which is grouped apart from Indicators-entry/fit-content/jump-to-latest controls (fit/jump may be net-new if not already present — check `ChartPage.tsx`/`LightweightChart.tsx` for existing fit/jump affordances before building new ones).
  - [x] Explicitly do NOT add a theme toggle — confirm this is documented as an intentional omission, not something a reviewer flags as missing later.

- [x] Task 2 — Left toolbar reconciliation (AC: #2)
  - [x] Confirm the cursor/crosshair cluster sits above the three drawing-tool buttons (Stories 18.1-18.3), in that relative order — reorder if any story landed them out of sequence.

- [ ] Task 3 — Full operation-parity checklist run (AC: #3)
  - [ ] Walk every row of the original spec's §A8.2 table against the real running app (`docker compose`/local dev server), for both Candles and Lines mode where applicable, and note any behavior that doesn't match — fix within this story rather than filing it as a new one, since this story's entire purpose is closing exactly these gaps.
  - [ ] Pay particular attention to interactions spanning multiple stories (e.g. "drawing tools keep working during replay," Story 18.4 AC #5) — this is the story that actually verifies those cross-story claims end-to-end, not just unit-by-unit.

## Dev Notes

- **This is a verification/reconciliation story, not a feature story** — most of its value is in Task 3's checklist walkthrough surfacing anything Stories 18.1-18.9 got subtly wrong in isolation (e.g. a toolbar button in the wrong visual cluster, a keyboard shortcut that stopped working after a later story's change).
- **The top-toolbar reconciliation (AC #1) is the one place this project's real navigation model (table-first: Rankings → `/chart/:iid`) genuinely diverges from the original spec's toolbar-first model** — the resolution here (read-only symbol label + back-link, no theme toggle, timeframe selector only if explicitly wanted) was decided in `spec-multi-exchange-screener-chart.md`'s §A8.1 reconciliation note; this story executes that decision, it doesn't re-litigate it.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (toolbar layout/grouping fixes), potentially `troll/frontend/src/theme.css` for any missing visual-cluster styling.
- Not modified: no new backend routes; this is a frontend layout/behavior audit.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.10] — this story's origin (FR60).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A8.1, #A8.2] — the full placement-ordering and operation-parity tables this story verifies against, including the top-toolbar reconciliation note.
- [Source: _bmad-output/implementation-artifacts/18-1 through 18-9] — every prior Epic 18 story this one audits for placement/behavior conformance.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

**STATUS: review -- NOT done. AC #3's "verified against the real, running app" and the live half of Task 3 could NOT be executed** in the autonomous session that built this: no browser automation is installed (no Playwright/Puppeteer; adding one is a new dependency), and the app needs a running data_api + catalog. Everything below marked "tests" is verified only at the jsdom/mocked-library level. Task 3 stays unchecked. The same "owed: visual/browser check" applies to Stories 18.2-18.9 (all UI, all only jsdom-tested) -- 18.1 already carries that flag in sprint-status.

**Task 1 (top bar, AC #1) -- done.** `chart-topbar` toolbar with three clusters separated by rules, in the spec order: [Symbol: `< Rankings` link to `/` + instrument label + read-only timeframe `1m` (from `BAR_SECONDS`)] [Chart type: Candles / Lines] [Indicators (anchor to `#indicators`, wrapping the picker + chart-overlay controls) / Fit / Latest / Replay]. The fourth cluster (theme) is deliberately omitted (Story 15.9 fixed the visual identity) and no timeframe selector was added (multi-timeframe viewing is not wanted; the bar size is a constant). Test asserts group order, the link, and the absence of any theme/symbol/timeframe control.

**Fit / Latest (net-new, needed for AC #1's third cluster):** `viewCommand` prop on `LightweightChart` (`{kind, seq}` -- `seq` makes a repeated click a new command) -> `timeScale().fitContent()` / `scrollToRealTime()`; ChartPage never touches the chart API (AD-F4). The data/pane effects still never move the view.

**Task 2 (left toolbar, AC #2) -- done.** Order: [Cursor, Crosshair] | [Trendline, Horizontal line, Measurement, FRVP]. Reordered (18.2/18.3 had landed hline before trendline). The crosshair toggle did not exist: added as a view option (aria-pressed), independent of the active tool, via `crosshairVisible` -> hides/shows the crosshair lines and their axis labels (`vertLine`/`horzLine` `visible`+`labelVisible`) only on a real change; the crosshair MODE is deliberately untouched (the library default is Magnet, and hover/drag handling depends on its events still flowing). FRVP (18.6) sits in the drawing cluster after the three spec tools.

**Task 3 -- §A8.2 parity audit (code-level; live walkthrough owed).**

| Behavior | Status |
|---|---|
| Pan / zoom time axis / zoom price axis | library-native (no `handleScroll`/`handleScale` overrides); armed tools intentionally swallow the drag. Live check owed |
| Fit view / Jump to latest | built here; unit-tested against mocked timeScale. Live check owed |
| Crosshair readout (status bar O/H/L/C/time) | **GAP -- not present in the app** (only the library's crosshair lines + axis labels). Not built: outside Epic 18's stories and AC #4 forbids new chart features. Recorded in deferred-work |
| Resize a pane | library-native (v5 pane separators); the wider hit zone is unverified. Live check owed |
| Add indicator | picker is a dropdown + Add (immediate, default settings, no confirm step) -- NOT "click a result in a search dialog". Deviation, recorded |
| Indicator settings / visibility eye / remove | **GAP/deviation**: settings are inline param fields + Apply, remove is a Remove button, both in the picker list -- there is no chart legend with gear / eye / x. No visibility toggle exists at all. Recorded |
| Draw trendline | two-click (click, click), not click-drag (18.2's choice per its Task 3; matches TradingView's own trendline behavior). Tests |
| Horizontal line: single click + draggable | tests (18.1) |
| Measure: click-drag, delta/bars/volume | tests (18.3) |
| Esc cancels an in-progress tool | tests for hline/trendline/measure/FRVP/replay-pick, plus Esc during an active replay leaves the replay running |
| Enter replay / step / play / go-to | tests (18.4) |
| Drawing tools keep working during replay (18.4 AC #5) | new page-level test: trendline + horizontal line placed while replay is active, both survive stepping |
| Alert creation | Epic 20 -- deferred until it ships |

vitest 255 pass, tsc + oxlint clean.

### File List

- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx
- troll/frontend/src/components/chart/LightweightChart.tsx
- troll/frontend/src/components/chart/LightweightChart.test.tsx
- troll/frontend/src/index.css

### Review Findings

- [x] [Review][Patch] Toggling the crosshair off/on via `CrosshairMode` would have silently changed the library default (Magnet) to Normal, and `Hidden` may stop crosshair-move events that price-line hover/drag rely on [LightweightChart.tsx] — fixed: off = hide the lines + labels only, mode untouched + test
- [x] [Review][Patch] Timeframe `aria-label` on a role-less span was ignored by assistive tech; `#indicators` target had no focus target; dangling dividers when the top bar wraps [ChartPage.tsx, index.css] — fixed (title text, `tabIndex=-1`, narrow-viewport rule)
- [x] [Review][Defer] Latest/Fit act on the replay-trimmed data during a replay (scroll to the replay cursor, not the live latest); Fit/Latest enabled with no data (silent no-op); `role="toolbar"` without roving arrow-key navigation; Indicators entry is an anchor link, not an in-chart dialog; FRVP and Replay are extra to the spec's clusters — deferred / accepted

Status: left at `review` deliberately -- AC #3 (live-app walkthrough) is owed, see Completion Notes. Dismissed as noise/handled: 4.
