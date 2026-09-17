# Story 18.1: Horizontal line drawing tool

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to click once to place a draggable horizontal price line,
so that I can mark a price level of interest.

## Acceptance Criteria

1. **A minimal left toolbar is introduced on `ChartPage.tsx`** (none exists today — confirmed by reading the file: only the Candles/Lines mode buttons exist) with a "cursor" (default) and "horizontal line" tool, plus an `Esc`-cancels-active-tool behavior. This is the first of Epic 18's drawing tools; Stories 18.2/18.3 add their buttons to the same toolbar; Story 18.10 later does the full placement/grouping audit — this story only needs a working minimal toolbar, not the final layout.
2. **Selecting the horizontal-line tool and clicking once on the chart places a price line at that price**, via `lightweight-charts`' native `series.createPriceLine({ price, color, lineWidth, axisLabelVisible: true, title })` — no custom Primitive needed for this tool.
3. **A placed line is draggable**; dragging updates the line's `price` live.
4. **`LightweightChart.tsx` remains the sole owner of the chart/series instances (AD-F4's spirit, extended to this new surface)** — price lines are a new declarative prop (e.g. `priceLines?: PriceLineSpec[]`), diffed and applied internally exactly like the existing `panes` prop; `ChartPage.tsx` never calls `series.createPriceLine()` itself.
5. **`Esc` cancels the active tool** and returns to cursor mode.

## Tasks / Subtasks

- [ ] Task 1 — Minimal left toolbar + tool state (AC: #1, #5)
  - [ ] Add `const [activeTool, setActiveTool] = useState<"cursor" | "hline">("cursor")` to `ChartInner` in `ChartPage.tsx`, alongside the existing `mode` state.
  - [ ] Render a small toolbar (cursor button, horizontal-line button) — reuse the docs page's `.tabs`/`.tabbtn` visual pattern if applicable, or a comparably simple existing convention; do not introduce a new UI framework.
  - [ ] A global `keydown` listener for `Escape` resets `activeTool` to `"cursor"`.

- [ ] Task 2 — `LightweightChart.tsx`: declarative `priceLines` prop (AC: #2, #3, #4)
  - [ ] Add `priceLines?: PriceLineSpec[]` prop (`{ id: string; price: number; color: string; title?: string }`), diffed against an internal `Map<string, IPriceLine>` registry the same way `panes` is diffed today (`useEffect` keyed on `[priceLines]`, per-id add/update/remove, never touching `timeScale`/visible range).
  - [ ] On the main series (`seriesRef.current` in Candles mode; note Lines mode has no single "the" main series — flag this as a scope decision: MVP restricts the horizontal-line tool to Candles mode only, matching where a single obvious series to attach the price line to exists), call `series.createPriceLine(spec)` for a new id, `.applyOptions()` for a changed price/color on an existing id, `.remove...PriceLine()`/removePriceLine equivalent for a removed id.
  - [ ] A price-line drag updates `spec.price` for that id in `ChartPage.tsx`'s own `priceLines` state array — `LightweightChart` reports the drag via a callback prop (e.g. `onPriceLineDrag?: (id: string, price: number) => void`), it does not mutate `ChartPage`'s state itself.

- [ ] Task 3 — Wire the click-to-place interaction (AC: #2)
  - [ ] While `activeTool === "hline"`, a chart click handler (via `chart.subscribeClick`, or the container's native click plus `series.coordinateToPrice()`) computes the clicked price and appends a new `PriceLineSpec` to `ChartPage.tsx`'s `priceLines` array, then resets `activeTool` to `"cursor"` (single-click-and-done, per the original spec's tool interaction model — not a persistent multi-click mode).

- [ ] Task 4 — Tests
  - [ ] A test for the tool-selection/Esc-cancel state machine in `ChartPage.tsx` (or wherever the toolbar logic lives).
  - [ ] A test for `LightweightChart.tsx`'s `priceLines` diffing (add/update/remove by id), mirroring the existing `panes`-diffing test pattern if one exists.

## Dev Notes

- **This is the first of three drawing-tool stories (18.1-18.3) sharing one new left toolbar and one new interaction-state pattern (`activeTool`) in `ChartPage.tsx`.** Build the toolbar generically enough that 18.2/18.3 add their tool without restructuring this story's work.
- **Why a new `priceLines` prop instead of `ChartPage.tsx` calling `series.createPriceLine()` directly:** `LightweightChart.tsx`'s docstring and every existing effect (`panes`, `mode`, `data`) enforce "this component is the sole owner of the chart/series instances" — no external caller touches `chart`/`series` APIs directly today (`onChartApi` only hands the parent a reference for read-only subscription, e.g. `useCandles`' pagination listener, never for mutation). Keep that invariant for this new surface rather than making an exception.
- **Native API, not a custom Primitive** — unlike Stories 18.2/18.3, `createPriceLine` is already built into `lightweight-charts`; no Primitive plumbing is needed here.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (toolbar, tool state, priceLines state), `troll/frontend/src/components/chart/LightweightChart.tsx` (new `priceLines` prop + internal registry).
- Not modified: `troll/data_api/*` (purely a frontend/client-side drawing feature, no persistence in this story — persistence isn't specified anywhere in scope for MVP drawing tools).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.1] — this story's origin (FR57).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A3, #A8.2] — horizontal-line tool mechanics; the `Esc`-cancels-tool operation-parity requirement.
- [Source: troll/frontend/src/components/chart/LightweightChart.tsx] — full file read this session; the existing `panes` declarative-prop diffing pattern this story's `priceLines` prop follows, and the "sole owner of the chart instance" invariant (AD-F4) this story extends to a new surface.
- [Source: troll/frontend/src/pages/ChartPage.tsx] — full file read this session; confirms no left toolbar exists today, and the existing Candles/Lines toggle's toolbar-button pattern this story's tool buttons mirror.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
