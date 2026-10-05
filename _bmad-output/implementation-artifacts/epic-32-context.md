# Epic 32 Context: Chart honesty and cleanup: gaps drawn to length, panes that grow the page, indicator settings on the legend, and the classic light chart

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Make the chart page tell the truth and drop clutter, per the operator's 2026-09-30 review. Today a multi-hour collection hole draws as one missing candle, because the backend emits one gap row per hole, each frontend page seam adds one whitespace point, and lightweight-charts gives every point one slot. Every added pane also shrinks the price pane, because the chart has a fixed 500 px height. Volume is a hard-wired pane with no toggle. Indicator parameters can only be edited in a list below the chart, and the chart shares the app's dark VGA palette. This epic draws every gap at its real length in a dedicated colour, lets panes grow the page, turns the legend into each indicator's control surface, and gives the chart area TradingView's classic white palette. The rest of the app keeps its terminal identity.

## Stories

- Story 32.1: Every gap drawn to its real length, in a colour nothing else uses, on every chart
- Story 32.2: Panes grow the page instead of shrinking each other, and volume is an Indicators-menu entry with a toggle
- Story 32.3: The legend is the indicator's control surface: larger type, an eye to hide it, and a settings modal with inputs, source and style
- Story 32.4: The classic light chart: TradingView's palette inside the dark terminal app
- Story 32.5: Fibonacci retracement and Long/Short position tools, and every drawing stays on the chart
- Story 32.6: A coin's chart comes back exactly as it was left, and a new coin opens with your default setup
- Story 32.7: The rest of the TradingView profile family on the one shared engine: Auto Anchored, Anchored, Anchored VWAP and TPO
- Story 32.8: Volume footprint bars from the raw trade archive, toggled on from the Indicators menu

## Requirements & Constraints

- **A gap is never fabricated.** A gap slot stays native lightweight-charts whitespace (`{ time }`): no OHLC, value or volume. Every consumer that skips whitespace today keeps skipping it: volume/session profiles, measurement, legend, replay, history page and alert evaluation. The visible break comes from drawing on top of the chart, never from data.
- **A gap is shown loudly, never hidden.** A hole of `n` missing intervals takes `n` slots. The cap on slots per hole is one named constant with a `Known limit:` comment. When a hole exceeds the cap, the chart must say it is compressed; it must not silently shorten the hole.
- **One edit path for indicator parameters.** Validation and coercion reuse the existing helpers. The row editing UI becomes one shared component, not a second copy. Persistence stays on the existing per-coin indicator config REST resource, which is the facade's only write path.
- **Prefer deletion.** The indicator list below the chart is removed once the legend's settings modal handles edit, source, style and remove, the eye handles hide, and the Indicators dialog handles add.
- **Source and style live in the one config.** `views/preferences.py`'s `IndicatorEntry` gains `source` (default `close`), `hidden` and per-output `style`; `indicator_id` includes a non-default source; `views/indicator_picker.py` marks specs fed exactly `("close",)` as `source_selectable` and resolves `open/high/low/close/hl2/hlc3/ohlc4` in one helper. Old config files load unchanged.
- **No new dependencies.** Icons are inline SVG.
- **Warnings count as failures.** Any new test warning is treated as a failing test.
- **Docs ship with the change.** The in-app DocsPage chart section, `platform/CLAUDE.md` and the planning spec are updated in the same story as the behaviour they describe.
- **Verification for every story:** `cd platform/frontend && npm test && npm run lint && npm run build`. Story 32.1 also runs `python3 -m pytest views/tests data_api/tests -q` from `platform/`, which needs no Rust build.
- **Scope of touched files:** only `platform/frontend/`, `platform/views/chart_series.py`, `views/indicator_picker.py`, `views/preferences.py` and their tests, `data_api/routes/` (indicators, plus the new drawings, layout and footprint routes) and `data_api/tests`, `docs/`, `platform/CLAUDE.md` and planning artifacts; Story 32.8 alone also touches `kernel/catalog_files.py` and `kernel/tests`. The epic runs on branch `epic-32` in its own worktree and is merged by hand. Never modify `nautilus_trader/` or `crates/`.
- **Price integrity in every printed or served number.** Drawing labels, position R/R and size, and footprint cells format prices and sizes from integer units through `lib/units.ts` at the instrument precision, never with float noise (tests use a precision-2 and a precision-6 instrument). The footprint's trade reader reads stored integer raws, never through `float`, bounded by the caller's window; a bar with no archived trades is flagged `no_trades` and drawn as a gap, never zeros.
- **Server-side preferences fail loudly.** The drawings and layout resources refuse a malformed item or key with a 422 naming it, never silently dropping it; a stale saved timeframe or mode falls back to the built-in default with one `console.error`, never a blank chart.

## Technical Decisions

- **One chart, many panes.** A coin's chart is exactly one `createChart()` instance with N panes. Panes are keyed by indicator id in the single registry owned by the chart component, and no child component creates or destroys a pane. The native time-scale sync is the only sync mechanism. lightweight-charts is pinned at 5.2.1, so check pane-height APIs (`setStretchFactor`, `setHeight`) against that version.
- **Gap rows from the backend.** Gap rows keep the existing row schema with all values `None`. The candle, indicator series and indicator values endpoints share one gap helper. All three must return identical gap times for the same window so pane slots stay aligned. Lines mode uses its own snapshot gap threshold, and 2 s snapshot spacing is not a gap.
- **Cap mirrored on the frontend.** The frontend holds the same cap value as a named constant, and each side has a test asserting it. One shared helper builds whitespace runs at every page or refetch seam, so seams never have ad-hoc gap logic.
- **Gaps are painted by a primitive.** A series primitive in the same family as the existing vertical-marker primitive is attached to every series type: candles, volume, indicator panes and Lines mode.
- **Frontend reads the facade only.** The frontend talks to the backend over HTTP/WS only, and the facade computes no signals. A forming bar comes only from the server's live candle channel.
- **Pagination.** History pages use the `before_ns` + `limit` cursor with a `{items, has_more}` response. Scroll-back refill counts logical slots, so a left edge made of gap slots still triggers a refill.
- **Volume toggle state.** The on/off state is a per-viewer convenience kept in `localStorage`, next to the persisted timeframe. It is not server config. The volume data is still fetched while the pane is off, because profiles and measurement depend on it.
- **Chart colour tokens.** Chart colours become a token set scoped to the chart workspace. The chart reads them from its container element, not from `document.documentElement`. Chart code may use no hard-coded colour literals and no app-palette tokens, and a grep test enforces this.

## UX & Interaction Patterns

- **Gaps:** each gap slot is one placeholder bar, one candle wide (at least 1 px) and full pane height, hatched or translucent so the grid stays visible. The first slot of each run carries a label in the price pane, for example "no data · 5m", or "(compressed)" when the run exceeds the cap. With the crosshair on a gap, the status bar and legend show "no data · <duration>" instead of "—".
- **Pane heights:** adding or removing a pane changes the total chart height. The price pane and existing panes keep their pixel size, and a height the operator set by dragging a divider survives later adds and removes. The page scrolls. Fit, Latest, crosshair sync, replay and primitives behave as before.
- **Volume entry:** Volume is the first entry in the Indicators dialog, on by default, and always the first pane under price when enabled.
- **Drawing tools:** Fib (drag), Long and Short (click) join the left rail's drawing cluster; every drawing's handles are draggable in Cursor mode through one hit-test mechanism, the context menu gains Settings…, prices in labels are formatted through `lib/units.ts` at the instrument precision.
- **Legend:** names are larger. Each legend row has eye, gear and × buttons that are visible on hover or focus, keyboard-reachable and carry aria labels. Only the row takes pointer events, so the rest of the pane still pans and zooms. The eye hides or shows every series of that indicator, persisted as `hidden`: an overlay is hidden in place, a pane indicator collapses its pane (height removed through 32.2's arithmetic, legend row kept crossed on the price pane) and re-adds it on show. The gear opens a `<dialog>` modal with three sections: Inputs (params plus a Source select for `source_selectable` indicators), Style (per output: colour, line width 1–4, solid/dashed/dotted; histogram up/down colours) and a footer with Apply, Cancel and Remove; Esc, Cancel or a backdrop click closes without changes, and a style-only Apply does not refetch. Volume gets eye and × only. This follows the TradingView operation checklist in the spec, where settings, visibility and removal live on the legend entry.
- **Cursor button:** the left-rail Cursor button stays, because it is the disarm and the only mode in which drawings can be edited. It shows as active whenever no tool is armed.
- **Theme:** only the chart area is light, by operator decision. There is still no theme toggle, and the stale "fixed dark, no toggle" notes are updated to say so. Chart colours must reach at least 3:1 contrast against the white background, and the legend's text-shadow becomes a light halo.

## Cross-Story Dependencies

- The stories run strictly in order: 32.1 → 32.2 → 32.3 → 32.4 → 32.5 → 32.6 → 32.7 → 32.8.
- 32.7 adds every new profile on `lib/volumeProfile.ts`'s one engine (a `weight: volume|time` option for TPO) and `VolumeProfilePrimitive`; Anchored VP and Anchored VWAP are drawings in 32.5's resource, Auto Anchored and TPO are session-control presets in 32.6's layout.
- 32.8 is the only story touching `kernel/`: a column-projected integer trade-tick reader in `kernel/catalog_files.py`; `views.chart_series.footprint_page` buckets by integer price units over the chart's own `candle_page` window; historical bars only (Known limit, upgrade path: fold the trades Redis stream); `GET /api/coin/{iid}/footprint`; one `FootprintPrimitive` on the price pane; toggle pinned next to Volume in the Indicators dialog.
- 32.6 adds `chart_layouts.toml` (per-coin timeframe, mode, volume, crosshair, dragged pane heights, visible bar count, volume-profile settings, plus a `[default]` template with `default_indicators`) behind `GET`/`PUT /api/coin/{iid}/layout` and one `useChartLayout` hook; it imports and removes the `chart-timeframe`/`chart-volume` localStorage keys, so 32.2's key is provisional. Indicators and drawings stay in their own files.
- 32.5 reuses 32.3's settings dialog component for the Fib and position modals, and moves every drawing (hlines, trendlines, the new kinds) to one server-side `chart_drawings.toml` resource in `views/preferences.py` with a `GET`/`PUT /api/coin/{iid}/drawings` route, one mounted preferences directory (`./data/preferences/` → `/app/preferences/`, `CHART_PREFERENCES_DIR`; the two existing files `git mv` there, the old path env vars removed so a stale compose fails loudly); the browser's `chart-hlines` key is imported once.
- 32.1 introduces the gap colour token, and 32.4 moves it into the chart token set and re-chooses it for white. 32.4 recolours everything drawn in earlier stories, including gap bars and labels and the legend.
- The Volume legend row in 32.3 depends on 32.2 making volume a toggleable indicator entry.
- The epic runs in parallel with Epics 31 and 28 on separate branches, and the merge is manual.
