---
title: 'Story 33.10: Drawing tools II: ray, extended, vline, rectangle, channel, text, arrow, Fib extension, price/date ranges, with magnet, undo/redo, lock and hide-all'
type: 'feature'
created: '2026-10-07'
status: 'done'
baseline_revision: '4aeb08cf2b356830f84afad97fe705abda991d8b'
final_revision: 'c4294f529bd5b721ebd2698ea112af71cea4813f'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** the chart has six drawing kinds (hline, trendline, fib, position, anchored VP/VWAP). The TradingView set the operator uses beyond them is missing: ray, extended line, vertical line, rectangle, parallel channel, text, arrow, Fibonacci extension and persisted price/date ranges. Editing has no magnet, no undo, no lock, no hide-all and no delete-all.

**Approach:**
- Extend the one `Drawing` union, the server validator and the rail data (`lib/chartTools.ts`) with the ten new kinds. Each new kind gets a primitive behind the one `DrawingPrimitive` hit/handle path, a settings dialog and the 32.5 context menu.
- Add pure editing mechanics in `lib/drawingKit.ts`: magnet snap, Shift angle constraint, generic N-point placement, and a bounded undo/redo reducer.
- Wire the mechanics into `useChartDrawings`, `LightweightChart` and `ChartPage`. Hide-all is a new optional layout key.

## Boundaries & Constraints

**Always:**
- Anchors are `{time, price}` in whole UTC seconds (`storedTime`) and prices are rounded to the instrument precision (`roundPrice`). Labels print only through `lib/units.ts` (`safeDecimal`/`formatPercent`). A paint never throws.
- Added keys only (AD-D12): every existing stored drawing and layout loads unchanged. `locked`/`hidden` are optional on every kind (absent = false). `line_width` (1..4) and `line_style` (`solid|dashed|dotted`) are optional on the line-like kinds, hline and trendline included (absent = 1 / solid). The layout gains the optional `drawings_hidden` (absent = false).
- `views/preferences.py` stays strict: an unknown key, a wrong type or an unknown kind is refused naming the field. Each new closed set is mirrored by a `test_*_mirror_the_frontend` test.
- Magnet and Shift work on the real OHLC (`data`), never Heikin Ashi rows (AD-F6). Magnet does nothing in Lines mode (no OHLC).
- The undo history is page state per coin: it lives in the `useChartDrawings` store above the timeframe remount, is never persisted, and holds at most 100 states. A load (GET) resets it and is not undoable. One handle drag is one undo step.
- A locked drawing draws no handles and ignores grabs, but its menu (Unlock, colour, Settings…, delete) still opens. A hidden drawing, or any drawing while `drawings_hidden` is on, is neither drawn nor hit-tested. Its anchored VP/VWAP profile and line are hidden too.
- The Esc rule of 32.5 holds: Esc disarms the tool and discards pending placement points.
- No new dependency. Dialogs reuse `SettingsDialogShell`, and the confirm is `window.confirm`, as in Save-as-default.

**Block If:** none expected. A requirement that cannot be met without modifying `nautilus_trader/` or `crates/`, or without a new npm dependency, halts.

**Never:**
- No object tree or per-drawing visibility list. Per-drawing `hidden` is undone in one place: the rail's "Show hidden (n)" action clears every drawing's `hidden`.
- No alert kinds on the new drawings (only hline/trendline keep "Add alert…").
- No persisted magnet mode or tool memory, and no `localStorage` for drawings or history.
- No change to `MeasurementPrimitive`: it stays the temporary Measure tool.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Unknown kind from server | GET items contain `kind: "zigzag"` | `parseDrawings` throws `UnknownDrawingKindError`. Load fails, `console.error("drawings.unknown_kind: …")` (ErrorBar), status `failed`, tools off. Nothing is ever PUT, and the load is not retried (it is permanent) | loud, never dropped (DATA-07) |
| Round trip | every kind's fixture JSON | `JSON.stringify(parseDrawings([x])[0])` equals the input byte for byte; the Python TOML save→load is equal | — |
| Weak magnet | pointer y 6 px from the bar's high, radius 12 px | the price snaps to the high | no bar under the pointer → raw price |
| Weak magnet far | nearest OHLC 20 px away | raw price | — |
| Strong magnet | any pointer over a bar | the nearest of O/H/L/C, always | whitespace slot → raw |
| Shift on 2nd point | the angle from the 1st point is 30° in px | snapped to the nearest of 0/45/90° in pixel space, then converted back to time/price | — |
| Undo/redo | 3 edits, Ctrl+Z ×2, then a new edit | restores the state 2 back; the new edit clears the redo stack | Ctrl+Z with no history: no-op |
| History bound | 150 edits | 100 undo steps available, the oldest dropped | — |
| Drag | 40 move events of one grab | one undo step | — |
| Locked | grab a locked trendline handle | no drag, no handles drawn; a click opens the menu with "Unlock" | — |
| Delete all | rail "Delete all", confirm cancelled | nothing changes | confirm accepted → `[]`, undoable |
| Channel | clicks A, B, C | `offset = C.price − trendlinePriceAt([A,B], C.time)`; a vertical A–B (equal times) uses `C.price − A.price` | — |
| Fib extension | A, B, C | level price = `C + (B − A) × ratio` | — |
| Zero-size placement | the 2nd click equal to the 1st (time and price) | ignored, the tool stays armed (the trendline rule) | — |

</intent-contract>

## Code Map

All paths are under `platform/`.

- `frontend/src/lib/drawings.ts`:
  - `Drawing` union L137, `DRAWING_KIND_NAMES` L493 (mirrored by `views/tests/test_chart_drawings.py`), `parseDrawings` L500, `applyHandleDrag` L402, `nextDrawingId`, `trendlinePriceAt`, `fibLevelPrices`/`fibPrice`, `safeDecimal`/`formatPercent`, `storedTime`, `snapIndex`.
  - The tests in `drawings.test.ts`.
- `frontend/src/lib/chartTools.ts`: the `ChartTool` union, `DRAWING_GROUPS`, `placesDrawing`/`needsPrecision`/`candlesOnly`; test `chartTools.test.ts`.
- `frontend/src/components/chart/ToolRail.tsx`: the rail (cursor, crosshair toggle, divider, groups). The new toggles and actions go after the groups.
- `frontend/src/components/chart/primitives/`:
  - `drawingPrimitive.ts`: the `DrawingPrimitive` interface, `BarGrid`, `nearestHandle`, `distanceToSegment`.
  - `TrendlinePrimitive.ts` (no test yet), `FibPrimitive.ts` (+test).
  - `MeasurementPrimitive.ts`: `computeMeasurement`, `MeasurementIndex`, the label box.
- `frontend/src/components/chart/LightweightChart.tsx`:
  - `DrawingSpec`/`createDrawingPrimitive`/`updateDrawingPrimitive` L183-250; `findDrawingHit` L755.
  - The drawings registry effect ~2070, handles ~2100, the drag crosshair ~2110, the grab mousedown ~2190, the contextmenu ~2245, click ~2266, the trendline preview ~2305 (replaced by the generic placement preview).
  - The fib drag-placement ~2024 (stays); the menu render ~2343-2480; price lines (`PRICE_LINE_WIDTH` L738).
- `frontend/src/components/chart/DrawingSettingsDialog.tsx`: `FibForm` and others on `SettingsDialogShell`.
- `frontend/src/hooks/useChartDrawings.ts`: the store (load, debounced save, `saveNow`). History goes here.
- `frontend/src/pages/ChartPage.tsx`:
  - `activeTool` 468, `pendingAnchor` 500, the Esc handler 1017, `handlePriceClick` 1041, `handlePointClick` 1060, `handleFibPlace` 1122.
  - `selectTool` 1509, `handleDrawingColor`/`Delete` 1525-1550, `applyDrag` 1552, `requestSettings`, `isToolDisabled` 1589, `<ToolRail>` 1787, `<LightweightChart>` drawing props ~1809, the settings dialog render 1879.
  - `plainDrawings`/`anchored` ~600-690 (hidden filtering); `window.confirm` 2058.
- `frontend/src/lib/chartLayout.ts`: `ChartLayout` 140, `BUILT_IN_LAYOUT`, the `volumeColorByOf` optional-key pattern 413, `normalizeLayout`, `layoutForSave`, `layoutKey`/`sameLayout`.
- `views/preferences.py`:
  - drawings: `DRAWING_KINDS` L103, `_check_anchors`, `_DRAWING_KEYS`, `validate_drawing`.
  - layout: `_OPTIONAL_LAYOUT_KEYS` L675, `BUILTIN_DEFAULT_LAYOUT`, `validate_layout` ~934.
  - Tests: `views/tests/test_chart_drawings.py`, `views/tests/test_chart_layouts.py`.
- `data_api/routes/drawings.py:49`: the `DrawingsResponse` comment that lists the kinds.
- Docs:
  - `frontend/src/pages/docs/kbData.ts`: `chart-drawings` L184, `chart-layout`.
  - `docs/DATA_DICTIONARY.md`: new §2.18 after §2.17 (3867).
  - `docs/DATA_INTEGRITY_AUDIT.md`: next row D-210.
  - `docs/DEPLOY_CHECKLIST.md`: the 33-9 section 1707 is the format.
  - `platform/CLAUDE.md` SSOT-06: the drawing kinds and the layout keys.
  - `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md`: the exclusions list (extra drawing tools).

## Tasks & Acceptance

**Execution:**
- [x] `views/preferences.py` + `views/tests/test_chart_drawings.py` + `views/tests/test_chart_layouts.py`:
  - `DRAWING_KINDS` appends `ray, extended, vline, rect, channel, text, arrow, fib_extension, price_range, date_range`, and `LINE_STYLES = ("solid","dashed","dotted")`.
  - Every kind may carry `locked`/`hidden` (bool). The line-like kinds (hline, trendline, ray, extended, vline, rect, channel, arrow, price_range, date_range) may carry `line_width` (int 1..4) and `line_style`.
  - Per kind:
    - `rect` requires `fill_opacity` in [0,1].
    - `channel` requires `offset` (a finite number).
    - `text` requires `anchor` `{time, price}`, `text` (a non-empty str ≤ `MAX_DRAWING_TEXT_LENGTH = 500`) and `font_size` (int 8..72).
    - `vline` requires `time`.
    - `fib_extension` requires 3 `anchors` plus the fib options.
    - The other two-point kinds require 2 `anchors`.
  - `_check_anchors` takes a count. Layout: the optional `drawings_hidden` bool (default False) goes in `BUILTIN_DEFAULT_LAYOUT` and the normalized return.
  - Tests: a round trip per new kind, each refusal naming its field, the mirrors (kinds, line styles, text bound, font bounds), and `drawings_hidden` defaulted/refused/round-tripped.
- [x] `frontend/src/lib/drawings.ts` (+ `drawings.test.ts`):
  - The new interfaces and `DRAWING_KIND_NAMES` in the Python order; `LINE_STYLES`, `MAX_TEXT_LENGTH`, `MIN/MAX_FONT_SIZE`, `DEFAULT_RECT_OPACITY = 0.2`; `extendOf(kind)` (`ray` → right, `extended` → both, else none).
  - `extendedSegment(a, b, extend, width, height)`: the clip to the pane in px.
  - `channelOffsetFor(a, b, c)`; `fibExtensionLevelPrices(d, precision)`.
  - `rectLabel(d, precision)`: the price range and %. `rangeLabels(m, kind, precision)` goes through `safeDecimal`/`formatPercent`: the price change and %, the bars, and the volume at the size precision.
  - `placementOf(tool)`: the number of points 1/2/3.
  - `buildDrawing(tool, id, points, ctx)`: the drawing a finished placement makes, or null for a degenerate one. `previewDrawing` gives the in-progress shape.
  - `applyHandleDrag` handles every new kind:
    - two-point kinds `a`/`b`; rect `a`/`b` corners;
    - channel `a`/`b` (the offset kept) and `offset`;
    - text `anchor`; vline `time`; fib_extension `a`/`b`/`c`;
    - a locked drawing is returned unchanged.
  - `parseDrawings` throws `UnknownDrawingKindError`.
  - Tests: the geometry, placement, each handle, lock, the unknown kind, and a byte-for-byte round trip per kind (16 kinds).
- [x] `frontend/src/lib/drawingKit.ts` (new) + `drawingKit.test.ts`:
  - `MAGNET_RADIUS_PX = 12`, `type MagnetMode = "off"|"weak"|"strong"`, `nextMagnetMode`.
  - `magnetPrice(price, y, bar|null, priceToY, mode)`.
  - `constrainAngle(from, to)` (px, 0/45/90°).
  - `HISTORY_LIMIT = 100`. `drawingsReducer(state, action)` over `{drawings, past, future, gesture}` with the actions `load`, `edit{update, gesture?}`, `undo` and `redo` (pure; an edit equal by reference pushes nothing).
  - Tests: the matrix rows for magnet, Shift, undo/redo, the bound and the gesture.
- [x] `frontend/src/hooks/useChartDrawings.ts` (+ test): state through `useReducer(drawingsReducer)`.
  - `setDrawings(update, gesture?)`, `undo`, `redo`, `canUndo`, `canRedo`. The load dispatches `load`.
  - An `UnknownDrawingKindError` is logged `drawings.unknown_kind` and not retried.
  - The save logic is unchanged (an undo is saved like any edit).
- [x] `frontend/src/lib/chartTools.ts` (+ test):
  - The new tool ids. Lines: trendline, ray, extended, hline, vline, channel. Fibonacci: fib, fib_extension.
  - A new `shapes` group, "Shapes / Annotation": rect, text, arrow, placed after Projection.
  - Measure: measure, price_range, date_range. The ranges are `candlesOnly` and `needsPrecision`, and every new tool `placesDrawing`.
- [x] `frontend/src/components/chart/ToolRail.tsx`: after the groups, a divider and then:
  - the Magnet toggle (cycles off/weak/strong, `aria-pressed`, the label shows the mode);
  - Undo and Redo (disabled when empty);
  - "Hide all drawings" (eye, `aria-pressed`);
  - "Show hidden (n)" (only when n > 0);
  - "Delete all drawings".
- [x] `frontend/src/components/chart/primitives/`:
  - `TrendlinePrimitive` takes `{extend, arrow, lineWidth, lineStyle, locked}`. It draws the clipped extension and an arrow head at B, and its hit uses the drawn segment.
  - New `VlinePrimitive`, `RectPrimitive` (the fill at the opacity, a corner label), `ChannelPrimitive` (two parallels, a translucent fill, a, b and offset handles at the midpoint of the parallel), `TextPrimitive` (the measured text box, hit on the box) and `RangePrimitive` (`price_range`/`date_range`: a box, an arrow and the label from a `() => MeasurementIndex` getter).
  - `FibPrimitive` also draws `fib_extension` (3 handles).
  - A shared `lineDash(style, ratio)` goes in `drawingPrimitive.ts`.
  - Each primitive's `setHandlesVisible` honours `locked`. One test file per primitive (TrendlinePrimitive's new) covers the screen geometry, hit and lock.
- [x] `frontend/src/components/chart/LightweightChart.tsx` (+ test):
  - `DrawingSpec` covers the new kinds, created and updated through the two switch functions. Specs with `hidden` are skipped.
  - `PriceLineSpec` gains `lineWidth`/`lineStyle`/`locked`.
  - `findDrawingHit` gives a locked entry its body only.
  - New props:
    - `magnet: MagnetMode`. Each reported point (`onPointClick`, `onDrawingDrag`, the fib range drag) snaps on the real `data` bar under it.
    - `placement: {tool, points} | null`, which replaces `pendingAnchor`. The chart draws `previewDrawing` to the cursor (with Shift and magnet applied).
    - `onDrawingDragStart(id)`.
  - Shift on a click or drag constrains a two-point line kind's second point relative to the first or the other anchor, and is reported through a `shift` flag on the point.
  - The menu adds Lock/Unlock and Hide, and "Settings…" for every kind.
  - Tests: magnet snapping a click, a locked drag ignored, a hidden spec not attached, the preview of a 3-point tool, and the menu Lock.
- [x] `frontend/src/components/chart/DrawingSettingsDialog.tsx`:
  - `LineForm` (colour, width 1..4, style) for the line kinds, plus the rect opacity.
  - `TextForm` (text, font size, colour; it refuses empty or too long text inline).
  - `FibForm` reused for `fib_extension` with its own title.
- [x] `frontend/src/lib/chartLayout.ts` (+ test): `drawings_hidden` in the optional-key pattern, `layoutForSave`, `layoutKey`/`sameLayout`.
- [x] `frontend/src/pages/ChartPage.tsx` (+ test):
  - Generic placement: the `placement` state collects `placementOf(tool)` points, then `buildDrawing` runs and the tool disarms.
  - Text opens its dialog on placement.
  - Every edit goes through `setDrawings(update, gesture?)`, and a drag uses gesture `drag:<n>` from `onDrawingDragStart`.
  - Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z run undo and redo, ignored while focus is in an input/select/textarea or a dialog is open. They close a settings dialog whose drawing is gone.
  - Magnet is state held in `ChartForCoin`'s parent beside `toolMemory`.
  - Hide-all goes through `patchLayout({drawings_hidden})`, and drawing tools are disabled while it is on.
  - Delete-all is behind `window.confirm` naming the count.
  - Lock/Hide come from the menu.
  - Tests: a ray in two clicks, a channel in three, undo/redo by keyboard, delete-all confirm, hide-all persisted and tools disabled, lock.
- [x] Docs:
  - `kbData.ts` `chart-drawings`: every tool by group, how it is placed, magnet, Shift, undo/redo, lock, hide, delete-all and the shortcuts. `chart-layout`'s "What is saved" gains `drawings_hidden`.
  - `docs/DATA_DICTIONARY.md` §2.18: the drawing kinds, their fields and the layout key.
  - `docs/DATA_INTEGRITY_AUDIT.md`:
    - D-210: magnet on HA rows (guarded: snaps on real OHLC).
    - D-211: an unknown kind dropped by a save (guarded: the load refuses, no PUT).
    - D-212: undo restoring a list that overwrites another browser's save (Known limit, last write wins as in 32.5).
    - D-213: a hidden trendline still drives its alert (Known limit, on the Docs page).
  - `docs/DEPLOY_CHECKLIST.md`: a 33-10 section (rebuild `data_api`; no migration; a smoke check placing each tool).
  - `platform/CLAUDE.md` SSOT-06: the kinds and `drawings_hidden` `[amended 2026-10-07: Story 33.10]`.
  - The spec-multi-exchange exclusion is amended in place.
  - `data_api/routes/drawings.py`: the kinds comment.

**Acceptance Criteria:**
- Given the rail, when the operator opens Lines, Fibonacci, Shapes / Annotation and Measure, then every new tool is listed in its group, and placing it draws it with handles in Cursor mode, saves it to `chart_drawings.toml` and restores it on reload.
- Given any new drawing, when it is clicked or right-clicked in Cursor mode, then the 32.5 menu offers colour, Settings…, Lock, Hide and delete. Its dialog sets colour, width, style and the kind's own fields.
- Given Magnet weak or strong, when a point is placed or dragged near a bar, then it lands on that bar's O/H/L/C per the matrix. Shift held constrains a line's second point to 0/45/90°.
- Given edits, when the operator presses Ctrl+Z / Ctrl+Shift+Z, then the drawings step back and forward through at most 100 states, and the restored list is saved.
- Given Hide all on, when the page reloads, then the drawings stay hidden (layout flag) and the drawing tools are disabled until it is turned off.
- Given the backend, when a stored file holds only the six pre-33.10 kinds, then it loads unchanged; when a PUT carries a malformed new-kind item, then it is a 422 naming the field.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 20: (high 0, medium 5, low 15)
- defer: 1: (high 0, medium 1, low 0)
- reject: 4: (high 0, medium 0, low 4)
- addressed_findings:
  - `[medium]` `[patch]` `drawings_hidden` leaked through the `[default]` template: Save as default stores false, and `_template` never copies it (a hand-edited template included); API tests.
  - `[medium]` `[patch]` Ctrl/Cmd+Z/Shift+Z edited invisible drawings: undo/redo (keys and rail) are gated on loaded, Hide all off and no placement in progress; Ctrl+Y redoes and is documented.
  - `[medium]` `[patch]` Under Hide all, "Show hidden" and "Del all" are disabled, and hidden stored-source VWAPs no longer fetch.
  - `[medium]` `[patch]` Degenerate placements and drags (a vertical or zero-offset channel, anchors dragged onto each other) are refused, and the Docs/§2.18 state exactly what is refused.
  - `[medium]` `[patch]` Range drawings in Lines mode printed a measurement from stale candle bars: no index there, so bars/volume read `n/a`.
  - `[low]` `[patch]` Text length counts code points and blank text matches Python `strip()`, in the validator, the client and the counter.
  - `[low]` `[patch]` LineForm/TextSettings write only the fields that changed; the rect opacity is no longer quantised.
  - `[low]` `[patch]` `DERIVATIVE_LINE_STYLES` is an alias of `LINE_STYLES` (one constant).
  - `[low]` `[patch]` The channel offset and `applyHandleDrag` price rounding are throw-safe inside the drag handler.
  - `[low]` `[patch]` A text box hit reports `BODY_TOLERANCE_PX`, so a closer line wins selection.
  - `[low]` `[patch]` The arrow head is capped at 16 px.
  - `[low]` `[patch]` Shift outside the loaded bars keeps the unconstrained point (the angle is never faked by a clamp).
  - `[low]` `[patch]` Magnet past the newest or oldest bar leaves the price raw.
  - `[low]` `[patch]` The placement preview is seeded from the last pointer position when a point is added.
  - `[low]` `[patch]` Tests were added for LineForm/TextSettings (`DrawingSettingsDialog.test.tsx`), and the `precision` prop doc comment is corrected.

### 2026-10-07 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 3, low 2)
- defer: 0
- reject: 10: (high 0, medium 1, low 9)
- addressed_findings:
  - `[medium]` `[patch]` The magnet clamped a click past the newest bar (or on a whitespace slot) onto the last bar's OHLC via `BarGrid.snap`, against the matrix's "whitespace slot → raw": `snapPrice` now snaps only on a real bar at exactly that time (a loaded one or the forming one); chart test added.
  - `[medium]` `[patch]` The drag gesture counter lived in the component remounted on every timeframe change while the history above it survived, so the first drag after a switch coalesced into the last one before it (one Ctrl+Z undid both): `nextDragGesture()` in `drawingKit.ts` is module-wide; test added.
  - `[medium]` `[patch]` A text note holding a lone UTF-16 surrogate passed `_check_text` and crashed the TOML save (500, the client retrying): the validator refuses it naming `text`, and `parseTextForm` refuses it inline; tests on both sides, DATA_DICTIONARY §2.18.
  - `[low]` `[patch]` A dialog Apply with nothing changed, a colour picked again, or a lock/hide that changed nothing recorded an empty undo step and sent a PUT: `replaceDrawing` (`drawingKit.ts`) returns the list itself when the change saves alike; test added.
  - `[low]` `[patch]` `buildDrawing` checked only the first two points, so a Fibonacci extension's C could be placed on A, a shape `dragAnchor` refuses: any two coincident points are refused now; test added.

## Design Notes

- `ray`/`extended` are separate kinds sharing `TrendlinePrimitive`. The extension comes from the kind (`extendOf`), so no stored `extend` field can disagree with it. The trendline alert (`trendline_cross`) stays trendline-only. D-198's upgrade path is unchanged.
- History in the store's reducer, not the page, keeps `setDrawings` pure under StrictMode's double-invoked updaters, and resets on load:
  ```ts
  case "edit": { const next = a.update(s.drawings); if (next === s.drawings) return s;
    const coalesce = a.gesture !== undefined && a.gesture === s.gesture;
    return { drawings: next, future: [], gesture: a.gesture ?? null,
             past: coalesce ? s.past : [...s.past, s.drawings].slice(-HISTORY_LIMIT) }; }
  ```
- Placement unification: `placementOf` drives every click-placed tool, while fib keeps its drag. The chart's preview is `createDrawingPrimitive(previewDrawing(...))`, so the preview is drawn by the same primitive as the final drawing.

## Verification

**Commands:**
- `cd platform && python3 -m pytest views/tests data_api/tests/test_drawings.py data_api/tests/test_layout.py tests/test_boundaries.py -q` -- expected: all pass, no warnings
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass
- `cd platform && ruff check views data_api && mypy views/preferences.py` -- expected: clean

## Auto Run Result

**Summary:** Story 33.10 adds ten drawing kinds: ray, extended, vline, rect, channel, text, arrow, fib_extension, price_range and date_range. Each has its own primitive, a settings dialog, the 32.5 context menu and handles, and sits in its rail group (Lines, Fibonacci, the new Shapes / Annotation group, and Measure). Every kind gains optional `locked`/`hidden`, and the line kinds gain optional `line_width`/`line_style`. Editing gains a magnet (off/weak/strong on the real OHLC), Shift's 0/45/90° constraint, a 100-state undo/redo in the `useChartDrawings` reducer (one step per drag; Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z, Ctrl/Cmd+Y), lock, hide, "Show hidden", Hide all (the per-coin layout flag `drawings_hidden`) and Delete all behind a confirm. The parser refuses an unknown kind loudly (`drawings.unknown_kind`, no PUT, no retry).

This run was a follow-up review of the finished story (status was `done`, final revision 528ec69b8c).

**Files changed in this follow-up pass** (under `platform/`):
- `views/preferences.py`: `_check_text` refuses a lone surrogate; test row in `views/tests/test_chart_drawings.py`.
- `frontend/src/lib/drawingKit.ts` (+ test): `replaceDrawing` (no-op edits are no edit) and `nextDragGesture` (page-unique drag gestures).
- `frontend/src/lib/drawings.ts` (+ test): `buildDrawing` refuses any two coincident points; `parseTextForm` refuses a lone surrogate.
- `frontend/src/components/chart/LightweightChart.tsx` (+ test): `snapPrice` snaps only on a real bar at the exact time.
- `frontend/src/pages/ChartPage.tsx`: colour, Apply, lock and hide go through `replaceDrawing`; drag gestures from `nextDragGesture`.
- `docs/DATA_DICTIONARY.md` §2.18: the lone-surrogate refusal.

The story's full file list is in the first pass's commit 528ec69b8c.

**Review (follow-up pass):** two reviewers (Blind Hunter, Edge Case Hunter) produced 18 findings, 15 after deduplication.
- 5 patched (3 medium, 2 low), listed in the Review Triage Log.
- 0 deferred. `nextDrawingId` reusing a deleted id (which can re-target a `trendline_cross` alert, amplified by Delete all and undo) was raised again. It is already in `deferred-work.md` from the first pass, so no second entry was added.
- 10 rejected:
  - the server accepting channels the client refuses (the server only loads what is stored);
  - the placeholder "Text" note kept on Cancel (rejected in the first pass too);
  - the id computed outside the updater (unreachable: tools are disabled until the load completes);
  - the text hit-box pixel ratio (a false positive: the measured width over `hr` is the drawn width);
  - an equal-price Fibonacci extension A–B, and a zero-width rect or date range (the spec defines degenerate as time and price equal);
  - negative extension levels;
  - a ray whose anchor predates the loaded bars;
  - the future-time bar snap (the BarGrid convention, rejected in the first pass too).

**Verification:**
- `python3 -m pytest views/tests data_api/tests/test_drawings.py data_api/tests/test_layout.py data_api/tests/test_alerts.py data_api/tests/test_alert_inputs.py tests/test_boundaries.py -q`: 1024 passed.
- vitest: 1486 passed (72 files).
- `npm run lint`: 0 errors (3 pre-existing warnings).
- `npm run build`: ok.
- `mypy views/preferences.py`: clean. `ruff format --check` on the touched Python: clean.
- `ruff check views data_api`: 2 errors, both pre-existing in files this story did not touch (`data_api/tests/test_settings.py`, `views/tests/test_indicator_picker_native.py`).

**Follow-up review recommended:** false. The five patches are localized (one helper module, one validator rule, one lookup and one guard), each covered by a new test, and none changes a stored format or an API.

**Residual risks:**
- No live browser walkthrough was run; the interaction is tested through the mocked chart only. It is owed in the DEPLOY_CHECKLIST 33-10 smoke check.
- Undo is last-write-wins across browsers (D-212).
- A hidden trendline still drives its alert (D-213).
- Drawing-id reuse can re-target an alert (the open deferred-work entry).
