---
title: 'Volume overlays modal and grouped left tool rail'
type: 'feature'
created: '2026-10-05'
status: 'done'
baseline_commit: 'a038ae331f'
review_loop_iteration: 0
context:
  - '{project-root}/platform/CLAUDE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The chart's volume overlays (VRVP, the session-slot presets, placed FRVPs) are inline controls stacked under the chart, unlike the Indicators dialog; and the left tool rail is a flat list of ten buttons, unlike TradingView's grouped rail (long and short sit apart from each other).

Operator, verbatim: "you need to make a quick-dev singular that makes the volumne overlays modal like the indicators. place the button to open near the indicators one in the top, this modal you have list of ovlays you can add, then they will be added below in the modal and you can set the settings on it, also like tradingview the left buttons needs to be grouped in similarway so you press it and it opens for the diffrent stuff , fx long and short should be in same category"

**Approach:** A "Volume overlays" top-bar button next to "Indicators" opens a modal on the existing `SettingsDialogShell`: an add list on top (VRVP, the five `SESSION_PRESETS`, plus FRVP as a "draw on chart" entry), and below it the overlays on the chart, each with its existing settings inline and a Remove. The rail becomes tool groups declared as data; each group is one button showing its last-used tool plus a flyout menu of the group's tools.

## Boundaries & Constraints

**Always:** reuse `SettingsDialogShell`, `VolumeProfileSettingsPanel` and the existing session option fields; keep every semantics: one session-type slot (adding another preset switches it, said in the modal), VRVP single instance, FRVPs multiple with one shared settings set, candles-only gating, the "Showing N of M" / loading / "Not drawn yet" / "Covers loaded bars only" / "Shown in Candles mode only" messages (now in the modal entry). Rail: Esc disarms, `candlesOnly`/`placesDrawing`/`needsPrecision` gating and titles, crosshair stays its own non-exclusive toggle, flyouts are ARIA menus (aria-haspopup/expanded, Escape and click-outside close). Tool groups are data so Story 33.10 appends tools without touching markup.

**Ask First:** (non-interactive run: none — the operator's intent is final; open calls are recorded in Design Notes.)

**Never:** change the saved layout shape (`lib/chartLayout.ts`, `views/preferences.py`), touch `lib/sessionProfile.ts`'s one-line `SESSION_PERIODS` declaration, move the Footprint out of the Indicators dialog, add npm packages, invent a second dialog system.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Add | Candles, nothing on, Add SVP | SVP appears under "On the chart" with period/settings/Remove; its Add disabled ("On the chart") | N/A |
| Switch slot | SVP on, Add TPO | TPO replaces SVP in the one slot; the add list said "replaces …" beforehand | N/A |
| Lines mode | mode = lines | every Add disabled with "Candles mode only"; an entry already on says "Shown in Candles mode only" | N/A |
| FRVP | Draw FRVP from the modal | modal closes, FRVP tool armed; entry with shared settings shows (DW-150), placed ranges listed with Remove | N/A |
| Rail flyout | click Lines expander, pick HLine | HLine armed, group button now shows HLine; Esc/click-outside closes the menu | disabled items keep their titles |

</frozen-after-approval>

## Code Map

- `platform/frontend/src/pages/ChartPage.tsx` -- owns overlay state (`vrvpActive`, `sessionCfg`, `frvps`), `SELECT_TOOLS`/`DRAWING_TOOLS`, `renderTool`, top bar, inline controls to remove.
- `platform/frontend/src/components/chart/VrvpControl.tsx`, `SessionProfileControl.tsx` -- inline controls, folded into the dialog and deleted.
- `platform/frontend/src/components/chart/SettingsDialogShell.tsx` -- the one modal shell to reuse.
- `platform/frontend/src/components/chart/IndicatorPicker.tsx` -- look of the Indicators dialog (`indicator-dialog-*` classes).
- `platform/frontend/src/index.css` -- `.chart-toolbar`, `.indicator-dialog-*` styles.
- `platform/frontend/src/pages/ChartPage.test.tsx` -- page tests to move onto the modal/flyouts.

## Tasks & Acceptance

**Execution:**
- [x] `platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx` -- new: add list + "On the chart" entries (VRVP, session slot with period/anchor/IB/letters/status fields, FRVP ranges + shared settings) on `SettingsDialogShell` (which gains an optional `className`) -- one modal like Indicators.
- [x] `platform/frontend/src/lib/chartTools.ts` -- new: `ChartTool`/`ChartToolDef`/`TOOL_GROUPS` data and lookups (`groupOfTool`, `shownTool`, `toolDef`); `components/chart/ToolRail.tsx` -- new: the grouped rail with flyout menus (components only, so Fast Refresh/oxlint stay clean).
- [x] `platform/frontend/src/pages/ChartPage.tsx` -- wire both; top-bar "Volume overlays" button after "Indicators"; last-used tool per group held in `ChartPage` above the per-coin/timeframe remounts; drop the inline controls and the `#indicators` wrapper (nothing links to it).
- [x] delete `VrvpControl.tsx`, `SessionProfileControl.tsx`; flyout and dialog CSS in `index.css`.
- [x] tests: `lib/chartTools.test.ts`; `ChartPage.test.tsx` moved onto the modal/flyouts (helpers `armTool`/`toolControl`/`openOverlays`/`overlayClick`) plus new page tests (dialog open from top bar, add→settings→remove, slot switch, Lines disabling, close/Esc, status messages, FRVP draw/list; flyout groups, open/pick/remember, Escape/click-outside, one-open-at-a-time + arrows, Lines-mode items).

**Acceptance Criteria:**
- Given the chart page, when the operator presses "Volume overlays" in the top bar, then a modal opens listing the six overlays plus FRVP, with nothing of them left under the chart.
- Given the rail, when the operator opens the Projection group, then Long and Short are both in its menu, and the group button shows whichever was used last.
- Given a reload, when the layout is saved, then its JSON shape is unchanged.

## Design Notes

- Group names (TradingView-like): Cursor; Lines (Trend line default, Horizontal line); Fibonacci; Projection (Long default, Short); Measure; Volume-based (FRVP default, Anchored VP, Anchored VWAP). A one-tool group renders no expander.
- Last-used memory lives in `ChartPage` (survives timeframe/coin remounts); not persisted. Known limit: it resets on page reload; upgrade path: a per-viewer preference once the layout table may grow.
- The group button is disabled only when its shown tool is; its expander stays usable so another tool of the group can be picked (e.g. Trend line while HLine is shown in Lines mode).
- Esc inside an open flyout closes only the flyout (stopped before the page's disarm handler); Esc elsewhere disarms as before.
- FRVP is in the add list as "Draw on chart" (arms the rail tool, closes the modal); its entry shows while ranges exist or the tool is armed (DW-150). Anchored VP/VWAP stay drawings edited from their context menu (follow-up: list them in the modal).
- Review patch (DW-151/153): the dialog is closed most of the time, so a partial profile also gets a one-line notice under the chart (`VolumeOverlayNotices`, Candles mode only), beside the full messages in the dialog entry.
- Review patch: an Esc inside the dialog closes only the dialog (an armed FRVP stays armed); a flyout whose items are all disabled focuses the menu itself so Escape still closes it; focus leaving a group by keyboard closes its menu; FRVP ranges are labelled to the second.
- No long-press opener (expander arrow only). Known limit; upgrade path: pointer-hold timer on the group button.

## Verification

**Commands:**
- `cd platform/frontend && npx tsc -b --noEmit` -- clean
- `cd platform/frontend && npx vitest run` -- all green (baseline 991)
- `cd platform/frontend && npm run build && npm run test:codegen` -- succeeds

## Suggested Review Order

**Volume overlays dialog**

- Entry point: one modal on the shared shell, add list over the overlays on the chart.
  [`VolumeOverlaysDialog.tsx:114`](../../platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx#L114)

- Page wiring: same state and handlers as the old inline controls, now passed as grouped props.
  [`ChartPage.tsx:1507`](../../platform/frontend/src/pages/ChartPage.tsx#L1507)

- Add list: tags say on / Candles-only / "replaces X" for the one session slot.
  [`VolumeOverlaysDialog.tsx:214`](../../platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx#L214)

- Session entry keeps period/anchor/IB/letters and the status messages.
  [`VolumeOverlaysDialog.tsx:394`](../../platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx#L394)

- FRVP entry: placed ranges with Remove, one shared settings set (DW-150 kept).
  [`VolumeOverlaysDialog.tsx:420`](../../platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx#L420)

- Partial-data notices stay visible under the chart with the dialog closed.
  [`VolumeOverlaysDialog.tsx:149`](../../platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx#L149)

- Top-bar button right after Indicators.
  [`ChartPage.tsx:1321`](../../platform/frontend/src/pages/ChartPage.tsx#L1321)

**Grouped tool rail**

- Groups as data; Story 33.10 appends tools here, markup untouched.
  [`chartTools.ts:60`](../../platform/frontend/src/lib/chartTools.ts#L60)

- Group button + flyout menu: focus, arrows, Escape, click-outside, focus-out.
  [`ToolRail.tsx:27`](../../platform/frontend/src/components/chart/ToolRail.tsx#L27)

- Arming records the group's last-used tool; held above the remounts.
  [`ChartPage.tsx:1178`](../../platform/frontend/src/pages/ChartPage.tsx#L1178)

- Existing gating (candles-only, drawings loaded, precision) unchanged, now a predicate.
  [`ChartPage.tsx:1258`](../../platform/frontend/src/pages/ChartPage.tsx#L1258)

**Peripherals**

- Shell gains an optional class for the wider dialog.
  [`SettingsDialogShell.tsx:35`](../../platform/frontend/src/components/chart/SettingsDialogShell.tsx#L35)

- Flyout and dialog styles.
  [`index.css:236`](../../platform/frontend/src/index.css#L236)

- Test helpers arm tools through groups and open the dialog.
  [`ChartPage.test.tsx:296`](../../platform/frontend/src/pages/ChartPage.test.tsx#L296)

- New dialog page tests.
  [`ChartPage.test.tsx:1669`](../../platform/frontend/src/pages/ChartPage.test.tsx#L1669)

- New rail page tests.
  [`ChartPage.test.tsx:789`](../../platform/frontend/src/pages/ChartPage.test.tsx#L789)

