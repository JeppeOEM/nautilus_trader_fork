---
title: 'Story 32.3: The legend is the indicator''s control surface: larger type, an eye to hide it, and a settings modal with inputs, source and style'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: 'e456c00a14c4165039afbb5a32fc39466cccd06f'
review_loop_iteration: 0
final_revision: '1cccb2c401be6d6a2f9a3fed2c98503febc950f8'
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The legend (`legend.ts`, one `div.chart-legend` per pane) is a 12 px, `pointer-events: none` readout. An indicator's parameters can only be edited in the `IndicatorEntryRow` list rendered below the chart, nothing can be hidden without removing it, there is no line-style control, and every close-fed indicator is locked to the close price.

**Approach:** The legend becomes the indicator's control surface, as on TradingView: larger type, an eye (hide/show), a gear that opens a settings modal (parameters, price source, per-output line style) and an × (remove). The backend learns a price `source` for close-fed indicators, the per-coin config carries `source`, `hidden` and `style`, and the list below the chart is deleted.

## Boundaries & Constraints

**Always:**
- **Type.** One token `--legend-font-size: 14px`; titles in `--chart-text`, values in their line colour.
- **Buttons.** Eye, gear and × per legend row as inline SVG (no icon library), visible on hover or focus, keyboard reachable, `aria-label` "Hide <name>"/"Show <name>", "Settings for <name>", "Remove <name>". Pointer events on the row only; a drag or wheel elsewhere on the pane still pans and zooms (test the CSS rule). The Volume row (32.2) gets eye and × only.
- **Eye on an overlay:** every series of that indicator gets `applyOptions({ visible: false })`; the row stays with dimmed values and the crossed icon; the price pane keeps its size; the crosshair readout skips it.
- **Eye on a pane indicator (operator decision 2026-09-30):** the pane collapses (removed from the chart, total height shrinks through 32.2's arithmetic, panes below move up); its legend row is kept, crossed, on the price pane's legend; showing it re-adds the pane at its former position and height without a refetch (series data stays in state).
- **Persistence.** `hidden`, `source` and `style` live in the same `chart_indicators.toml` entry as the params (`views/preferences.py` `IndicatorEntry`: `source: str = "close"`, `hidden: bool = False`, `style: dict` per output with `color`, `line_width`, `line_style`; empty style = pane palette default); the GET/PUT route round-trips them; a pre-story file loads unchanged (fixture test). The screener `ColumnEntry` is untouched.
- **Modal.** A `<dialog>` like the Indicators dialog, titled with the legend title, three sections: **Inputs** (params as text inputs or `<select>` for catalog `choices`, validated by `paramCoercion.ts`; plus a Source `<select>` with `close` (default), `open`, `high`, `low`, `hl2`, `hlc3`, `ohlc4` only when the catalog entry says `source_selectable`), **Style** (per output: colour seeded from the pane palette, width 1–4 px, solid/dashed/dotted; histogram outputs get up and down colours), **Footer** (Apply, Cancel, Remove). `IndicatorEntryRow`'s input logic moves into one shared component (SSOT-02), never a copy.
- **Apply semantics.** Persists through `PUT /api/coin/{iid}/indicators` and refetches values as the old list did; a style-only Apply re-applies `series.applyOptions` without a refetch; a duplicate instance (same name, params and source) is refused as today; Esc, Cancel or a backdrop click closes without changes; Remove and the row's × remove through the same persist path.
- **Source on the backend.** `views/indicator_picker.py`: a native `IndicatorSpec` whose `feed` is exactly `("close",)` is `source_selectable: true` in `native_catalog_json()`; `_feed_values` resolves the source from the candle's `o/h/l/c` with `hl2 = (h+l)/2`, `hlc3 = (h+l+c)/3`, `ohlc4 = (o+h+l+c)/4` in one named helper, computed per candle and never stored; an unknown source, or a source on a non-selectable or custom indicator, is a 422; `IndicatorRequest`, `IndicatorValueRequestEntry` and `IndicatorConfigEntry` gain `source: str = "close"`; `indicator_id(name, params, source)` includes the source only when it is not `close`, so every existing id is byte-identical for the default (test over every catalog entry) and SMA(20) on close and on hl2 are two series with two rows.
- **Cursor button** (survey 2026-09-30: it disarms the armed tool and is the only mode with editable drawings) stays and shows the active state whenever no drawing tool is armed, including after Esc and after a tool completes; tooltip "Select / edit drawings (Esc)".
- **Deletion.** The `<div id="indicators">` list below the chart (`IndicatorEntryRow` list, inline `<select>` and Add) is deleted; the Indicators dialog is the one add path (DESIGN-03).
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- The `chart_indicators.toml` key-set freeze (AD-D12, `views/preferences.py` docstring) is judged to forbid *adding* keys with defaults. (Adding optional keys with defaults keeps every existing file loadable; a reviewer who disagrees blocks here rather than silently dropping persistence.)

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Fabricate a source for an indicator fed `high`/`low`/`volume` or for a custom indicator; those keep a fixed input.
- Keep two copies of the parameter-input logic.
- Persist style, source or hidden anywhere but the indicator entry (no `localStorage`).
- Change the light palette (32.4) or the pane-height arithmetic (32.2) beyond calling it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Hide SMA overlay | eye click | series invisible, row dimmed + crossed, pane count unchanged, `hidden: true` persisted | none |
| Hide RSI pane | eye click | pane removed, height −160 px, row crossed on price-pane legend | none |
| Show RSI | eye click again | pane re-added at former index and height, no refetch | none |
| Gear on SMA(20) | click | modal: period input, Source select (close), Style with one output | none |
| Source → hl2 | Apply | new id `...:hl2`, refetch, two rows if SMA(20) close still exists | none |
| Source on ATR | catalog not selectable | no Source select shown; API request with source → 422 | 422 names `source` |
| Style-only change | width 3, dashed, Apply | `applyOptions` only, no request to indicator-values | none |
| Invalid param text | "abc" for period | Apply disabled / refused, as today | `isValidParamText` |
| Old config file | entry without `source/hidden/style` | loads with defaults; saved back with them | none |
| Duplicate | SMA(20) close added twice | refused as today | message shown |
| Cursor after Trend tool completes | trendline placed | Cursor button active, drawings editable | none |

</intent-contract>

## Code Map

Frontend paths under `platform/frontend/src/`; line numbers from the live tree at plan time.

- `components/chart/legend.ts` -- `LegendSeries` :15-27 (no hidden flag/id), `legendContainer` :54, `legendRow` :68, `renderLegends` :95 rebuilds all with `replaceChildren()` on every crosshair move (buttons must be re-wired per render, or values updated in place). `index.css` `.chart-legend` :353-366 (12px, `pointer-events: none`), `-row` :373, `-title` :378; no `--legend-font-size` token yet. Tests: `legend.test.ts`, `LightweightChart.test.tsx:1447`.
- `components/chart/IndicatorPicker.tsx` -- `hasInstance` :37 (name+params, needs source), `persist` :119, `addByName` :140, `handleRemove` :152, `handleApplyParams` :156, list/select/Add :182-207 (delete), `IndicatorEntryRow` :212-291 (param logic to extract into one shared component), `IndicatorDialog` :301 (`<dialog>` showModal pattern :324-335, Volume row :357). The Technicals tab also uses `IndicatorPicker`: check before deleting its list.
- `components/chart/paramCoercion.ts` -- `isValidParamText` :6, `coerceParamValue` :19.
- `pages/ChartPage.tsx` -- `legendTitle` :99 (matches by name only: must match by instance), `catalogNameForKey` :87, `pickerEntries` :249, `usePickerIndicatorValues` :251, `panes` useMemo :278-310, `SELECT_TOOLS` :125, `selectTool` :568, `renderTool` :598-612 (no `title` yet), `<div id="indicators">` :800 (keep the div: it wraps VrvpControl/SessionProfileControl/AlertDialog and is the top bar anchor; remove only the picker's list).
- `hooks/usePickerIndicatorValues.ts` -- `entriesKey`/`requestEntries` ~:70-80 serialise `{name, params}`: add `source`, exclude `hidden` and `style` (no refetch on hide/style).
- `components/chart/LightweightChart.tsx` -- `IndicatorPaneSpec` :52-71, pane px consts :244-247, `snapshotPaneHeights` :251, `layoutPaneHeights` :274, panes effect ~:684-815 (series creation ~:722, colour `applyOptions` ~:737, `legendItemsRef` :789), crosshair legend `renderLegends` :1183-1191. No memory of a pane's former index/height: build collapse/restore here, keeping data in spec state.
- `api/schema.ts` -- `IndicatorCatalogEntry` :89 (+`source_selectable`), `IndicatorConfigEntry` :96 (+`source`,`hidden`,`style`). `api/client.ts` -- `saveCoinIndicatorConfig` :177, `fetchIndicatorValues` :196 (+`source`).
- `platform/views/indicator_picker.py` -- `IndicatorSpec` :85, `INDICATOR_CATALOG` :98-340, `_FEED_FIELD` :345, `replay_native` :348, `_feed_values` :397, `native_catalog_json` :409, `custom_catalog_json` :494, `IndicatorRequest` :773, `indicator_id` :805 (also called by `views/ranking_columns.py:250,258`: keep a `source="close"` default), `replay_entry` :815, `values_by_time` :839.
- `platform/views/preferences.py` -- AD-D12 docstring :16-35, `IndicatorEntry` :40-44, `ColumnEntry` :47 (inherits new defaults; screener load/save ignore them), `load_chart_indicators` :53, `save_chart_indicators` :76.
- `platform/data_api/routes/indicators.py` -- `IndicatorCatalogEntry` :82, `IndicatorConfigEntry` :103, GET :124-126, PUT :129-177 (raw JSON, 400 on bad payloads), `IndicatorValueRequestEntry` :185, `_parse_entries` :208.
- Tests: `views/tests/test_indicator_picker_native.py`, `test_indicator_picker_custom.py`, `views/tests/test_preferences.py` (`_CHART_INDICATORS_TEXT`, byte-identical round trip :77), `data_api/tests/test_indicators_config.py`, `ChartPage.test.tsx` (dialog describe :427, Cursor tests :183/:217/:590/:649), `paramCoercion.test.ts`.

## Plan-time Findings

- The 422 required by the contract needs an explicit `HTTPException(422)` for source errors in the values route (and the PUT route); other payload errors keep their existing 400.
- The Cursor button already derives active state from `activeTool === "cursor"` and returns to it after Esc/tool completion; only the tooltip and a test pinning the behaviour are missing.
- To keep pre-story files byte-identical (AD-D12 pin), `save_chart_indicators` writes `source`, `hidden`, `style` only when not default; loading fills defaults. Reading a pre-story file and saving it back is therefore unchanged.

## Tasks & Acceptance

**Execution:**
- [x] `platform/views/indicator_picker.py`, `platform/views/preferences.py`, `platform/data_api/routes/indicators.py` + their tests -- backend `source` (catalog flag, `_feed_values` helper, `indicator_id`, 422s) and `source`/`hidden`/`style` config fields with non-default-only writes; test every default id byte-identical, hl2/hlc3/ohlc4 maths, pre-story fixture.
- [x] `api/schema.ts`, `api/client.ts`, `hooks/usePickerIndicatorValues.ts` -- TS types; `source` in the values request key, `hidden`/`style` excluded.
- [x] `components/chart/` shared param-inputs component (extracted from `IndicatorEntryRow`) + `IndicatorSettingsDialog` (Inputs, Style, Footer) + tests.
- [x] `legend.ts`, `index.css`, `LightweightChart.tsx` -- `--legend-font-size`, eye/gear/× row buttons (pointer-events rule tested), hide overlay via `applyOptions({visible:false})`, pane collapse/restore without refetch, style `applyOptions`, Volume row eye+×.
- [x] `pages/ChartPage.tsx`, `IndicatorPicker.tsx` -- wire legend actions and persistence, instance-aware `legendTitle`, Cursor tooltip, delete the below-chart list (keep `div#indicators`), tests.
- [x] Docs: DocsPage chart section, `views/preferences.py` docstring, DATA_DICTIONARY if it lists the config file.

**Acceptance Criteria:**
- Given an indicator on the chart, when its legend eye, gear or × is used, then the indicator hides (collapsing its pane if it has one), opens its settings modal, or is removed, and every change persists in the coin's indicator entry and survives a reload.
- Given a close-fed indicator, when its source is set to `hl2`, then the values are computed from `(h+l)/2` per candle and the series id carries the source, while every default-source id stays unchanged.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.

## Review Triage Log

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 1, medium 5, low 2)
- defer: 3: (high 0, medium 2, low 1)
- reject: ~20
- addressed_findings:
  - `[high]` `[patch]` `source_price` unpacked every OHLC key (broke close on partial/gap candles): reads only needed keys, None component yields a gap.
  - `[medium]` `[patch]` technicals-values lacked the 422 source check; hand-edited TOML types unvalidated on load; stale collapsed pane heights; histogram null painted up colour; line width unclamped; volumeHidden stuck after volume off; stale entries on quick toggles plus `!onDialogClose` proxy; backdrop-drag closed the modal; refusal text; test placement.

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 1, medium 6, low 2)
- defer: 1: (high 0, medium 1, low 0)
- reject: 4
- addressed_findings:
  - `[high]` `[patch]` A configured instance whose replay failed draws no series, so it had no legend row and (with the list below the chart gone) could not be fixed or removed from the chart page: its error alert now carries Settings and Remove buttons.
  - `[medium]` `[patch]` A persisted source the indicator cannot take (hand-edit, catalog change) made every values request and every later save a 422: `GET /api/coin/{iid}/indicators` now serves `close` for it with one warning (the loader's wrong-value rule).
  - `[medium]` `[patch]` The settings modal closed on Apply before the PUT resolved (a failed save lost the drafts, its error out of sight), and the optimistic id change unmounted it mid-save: `persist`/`handleApply` return the save's outcome, the modal awaits it and is tracked by index while open.
  - `[medium]` `[patch]` Width select seeded 1 px while the library draws 3 px, and a cleared width/style stuck on the series: `DEFAULT_LINE_WIDTH`/`DEFAULT_LINE_STYLE` in `indicatorStyle.ts` seed the modal and `lineOptions` always states both.
  - `[medium]` `[patch]` Histogram up/down pickers seeded from `--chart-up/--chart-down` while the bars are painted in the palette colour: seeded with the palette colour.
  - `[medium]` `[patch]` `ColumnEntry`'s fourth positional field silently became `source`: the three Story 32.3 fields are keyword-only.
  - `[medium]` `[patch]` The loader accepted a style the PUT refuses (nested table) and the PUT accepted `NaN` (GET then 500s): one `preferences.is_valid_style` rule (finite scalar leaves) for both.
  - `[low]` `[patch]` Legend buttons invisible but tappable on touch screens: `@media (hover: none)` shows them.
  - `[low]` `[patch]` `outputStyle` dropped an out-of-range width while `lineOptions` clamped it: `outputStyle` clamps to 1..4.

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 0, medium 3, low 4)
- defer: 1: (high 0, medium 0, low 1)
- reject: 17
- addressed_findings:
  - `[medium]` `[patch]` The settings modal tracked its entry by list position, so a failed remove's rollback (the entry put back at its old position) left the open modal editing, applying to and removing a different indicator: the modal now follows the entry's identity (a WeakMap carried across optimistic patches and restored by a rollback), resolves the index at Apply/Remove time, and closes when its entry is gone.
  - `[medium]` `[patch]` A click on the settings dialog's own 12 px padding read as a backdrop click and closed it, losing the drafts: the padding moved from the `<dialog>` to its body.
  - `[medium]` `[patch]` A series no configured entry owns (stale values between an Apply and its refetch) was grouped under the bare catalog name (two stale instances merged into one pane) and drew eye/gear/x buttons that did nothing: it keeps its instance group and its row is a plain readout (`actionable: false` through `IndicatorPaneSpec` and `LegendSeries`).
  - `[low]` `[patch]` Legend rows stretched to the widest row, and as each row takes pointer events, the empty strip beside a short row blocked pan, zoom and crosshair: `.chart-legend` sizes rows to content.
  - `[low]` `[patch]` Remove stayed enabled while an Apply was in flight, chaining a remove onto a save that might still fail: disabled while saving.
  - `[low]` `[patch]` The duplicate check compared params with `JSON.stringify` (key-order sensitive) while ids sort keys: it compares instance ids.
  - `[low]` `[patch]` The Style rows followed the optimistic id during a save ("No outputs drawn yet" mid-save): captured when the modal opens. Also `DEFAULT_SOURCE` instead of `"close"` literals, and the id test no longer pins an id the backend can never produce (`VWAP:ohlc4`).


## Auto Run Result

- Summary: second follow-up review of the done 32.3 change (legend eye/gear/x, settings modal, backend `source`, `source`/`hidden`/`style` config fields). Seven frontend patches around the settings modal's lifecycle, the legend's rows and the duplicate check. No backend change.
- Files changed in this pass:
  - `platform/frontend/src/components/chart/IndicatorPicker.tsx` -- settings modal follows its entry's identity (not its position), resolves the index at Apply/Remove time, captures its Style rows at open; duplicate check by instance id.
  - `platform/frontend/src/components/chart/IndicatorSettingsDialog.tsx` -- Remove disabled while an Apply is in flight.
  - `platform/frontend/src/components/chart/legend.ts`, `LightweightChart.tsx` -- `actionable` flag: a row no configured entry owns draws no buttons.
  - `platform/frontend/src/pages/ChartPage.tsx` -- stale series keep their instance group, `actionable: entry !== undefined`; `DEFAULT_SOURCE` in `legendTitle`.
  - `platform/frontend/src/hooks/usePickerIndicatorValues.ts` -- `DEFAULT_SOURCE` instead of the `"close"` literal.
  - `platform/frontend/src/index.css` -- legend rows sized to content; settings dialog padding on its body.
  - Tests: `ChartPage.test.tsx` (rollback keeps the modal on its entry, Remove disabled mid-save, key-order duplicate, stale rows not actionable), `legend.test.ts` (no buttons when not actionable, the two CSS rules), `indicatorId.test.ts`.
- Review findings: 7 patches applied, 1 deferred (no server-side duplicate-id refusal; the GET source fallback can produce one from a hand edit), 17 rejected (6 already in the deferred ledger: overlapping-save rollback, keyboard focus after an eye toggle, style reset, Volume eye persistence, exotic id spellings, whole-request 422; the rest by design or noise: `price` feed outside the spec's exact `("close",)` rule, None-feed gap skip is the earlier pass's DATA-01 fix, colour picker shows black only for non-`#rrggbb` hand edits (the palette is all `#rrggbb`), unknown-name ordering, orphan style keys, Cancel while saving).
- Follow-up review recommended: false (localized frontend fixes, each pinned by a test that fails without it; no backend, API or data change).
- Verification: `npm test` 515 pass (the 5 new tests fail with the fixes stashed); `npm run lint` 3 pre-existing warnings, none new; `npm run build` ok. Python untouched in this pass (prior pass: `views/tests data_api/tests` green apart from the known Redis-dependent tests).
- Residual risks: the identity tracking and the dialog padding are verified in jsdom, not a real browser. Overlapping saves remain unserialized (deferred ledger). `platform/data/chart_indicators.toml` holds uncommitted operator edits from the running app and was left out of the commit.
