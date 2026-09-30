---
title: 'Story 32.3: The legend is the indicator''s control surface: larger type, an eye to hide it, and a settings modal with inputs, source and style'
type: 'feature'
created: '2026-09-30'
status: 'draft'
review_loop_iteration: 0
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

Filled at plan time from the live code (continuity from the 32.2 spec). Expected anchors: `platform/frontend/src/components/chart/legend.ts` and `index.css` (`.chart-legend*`), `IndicatorPicker.tsx` (`IndicatorEntryRow`, `addByName`, `persist`), `paramCoercion.ts`, `pages/ChartPage.tsx` (`legendTitle`, `panes` builder, `selectTool`, `drawEditable`, `<div id="indicators">`), `LightweightChart.tsx` (series creation and `applyOptions`), `api/schema.ts` and `api/client.ts` (`IndicatorEntry`, `saveConfig`, `fetchIndicatorValues`), `platform/views/indicator_picker.py` (`IndicatorSpec`, `_FEED_FIELD`, `_feed_values`, `native_catalog_json`, `indicator_id`, `IndicatorRequest`), `platform/views/preferences.py` (`IndicatorEntry`, `load_chart_indicators`/`save_chart_indicators`), `platform/data_api/routes/indicators.py` (`IndicatorCatalogEntry`, `IndicatorConfigEntry`, `IndicatorValueRequestEntry`, the PUT body reader), `views/tests/test_indicator_picker.py`, `views/tests/test_preferences.py`, `data_api/tests`, `ChartPage.test.tsx` (indicator dialog tests, Cursor tests), `legend.test.ts`.

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map, ordered: backend source + config fields + tests; API models and TS types; shared param component; legend rows and CSS; eye behaviour (overlay, pane collapse); modal; Cursor active state; delete the below-chart list; docs (DocsPage chart section, `views/preferences.py` docstring, DATA_DICTIONARY if it lists the config file).

**Acceptance Criteria:**
- Given an indicator on the chart, when its legend eye, gear or × is used, then the indicator hides (collapsing its pane if it has one), opens its settings modal, or is removed, and every change persists in the coin's indicator entry and survives a reload.
- Given a close-fed indicator, when its source is set to `hl2`, then the values are computed from `(h+l)/2` per candle and the series id carries the source, while every default-source id stays unchanged.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.
