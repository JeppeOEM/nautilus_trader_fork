# Story 18.10: Placement and operation-parity pass

Status: ready-for-dev

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

- [ ] Task 1 — Top toolbar reconciliation (AC: #1)
  - [ ] Confirm/build: symbol slot renders `{instrumentId}` as a link back to `/` (Rankings), grouped visually apart from a chart-type toggle (already exists: Candles/Lines buttons), which is grouped apart from Indicators-entry/fit-content/jump-to-latest controls (fit/jump may be net-new if not already present — check `ChartPage.tsx`/`LightweightChart.tsx` for existing fit/jump affordances before building new ones).
  - [ ] Explicitly do NOT add a theme toggle — confirm this is documented as an intentional omission, not something a reviewer flags as missing later.

- [ ] Task 2 — Left toolbar reconciliation (AC: #2)
  - [ ] Confirm the cursor/crosshair cluster sits above the three drawing-tool buttons (Stories 18.1-18.3), in that relative order — reorder if any story landed them out of sequence.

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

### Debug Log References

### Completion Notes List

### File List
