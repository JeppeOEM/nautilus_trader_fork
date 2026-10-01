---
title: 'Story 32.4: The classic light chart: TradingView''s palette inside the dark terminal app'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '8e6335d9cc'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** The chart reads the app's dark VGA tokens (`--color-*`, `--vga-*`) from `document.documentElement` once at mount, so it is a black terminal chart. The operator wants TradingView's classic white chart (decision 2026-09-30) while the rest of the app keeps its terminal identity.

**Approach:** A chart-only token set scoped to `.chart-workspace` in `theme.css`, read by `cssVar` from the chart container instead of the document root, and every colour the chart draws taken from it. No theme toggle.

## Boundaries & Constraints

**Always:**
- **Tokens** (in `theme.css`, scoped to `.chart-workspace`): `--chart-bg #ffffff`, `--chart-grid #f0f3fa`, `--chart-text #131722`, `--chart-text-dim #787b86`, `--chart-border #e0e3eb`, `--chart-up #26a69a`, `--chart-down #ef5350`, `--chart-crosshair #9598a1`, `--chart-volume-up`/`--chart-volume-down` (up/down at ~50 % alpha), `--chart-gap` (Story 32.1's token, moved into this block and re-chosen to read on white), `--chart-marker`, `--chart-drawing`, and an eight-colour pane palette `--chart-line-1..8`: `#2962ff`, `#f23645`, `#089981`, `#ff9800`, `#9c27b0`, `#00bcd4`, `#795548`, `#131722`.
- **One reader.** `paneColors.ts`'s `cssVar` (and `assignPaneColor`) resolve tokens on the chart container element; nothing in `components/chart/` or `pages/ChartPage.tsx` reads `--color-*` or `--vga-*` or uses a hard-coded colour literal (a grep test enforces this).
- **Everything the chart draws** takes its colour from the chart tokens: background, text, grid, price/time scale borders, crosshair, candles, volume, gap bars and labels, legend, replay marker, measurement, trendline and horizontal-line defaults, volume-profile fills, candle-pattern markers, Story 32.3's legend buttons and modal accents.
- **Scope.** Only the chart area is light: the toolbar clusters, rankings, history, alerts and docs pages keep the VGA dark identity untouched. The font stays `--font-terminal`.
- **No toggle.** `ChartPage.test.tsx`'s "no theme toggle" assertion stays; the comments in `index.css` (top) and `theme.css` (top) and `spec-multi-exchange-screener-chart.md` §A8.1 slot 10 are updated to say the chart area alone is light by operator decision (2026-09-30); the DocsPage chart section says the same.
- **Legibility test.** A vitest test parses the chart tokens out of `theme.css` and asserts contrast ≥ 3:1 against `--chart-bg` for text, each palette line, up, down, gap, marker and drawing colours (WCAG 2.1 graphics); the legend's dark `text-shadow` is replaced by a light halo.
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- A colour the chart draws cannot be routed through a token without changing lightweight-charts' behaviour (none known; block rather than leave a literal).

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Add a theme toggle, `prefers-color-scheme` handling, or a light variant of the app shell.
- Change the VGA tokens themselves.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Chart mounts | `.chart-workspace` container | `createChart` layout background `#ffffff`, text `#131722`, grid `#f0f3fa` | none |
| Token missing | a `--chart-*` var unset in a test DOM | `cssVar` fallback used and one `console.error` (visible in ErrorBar) | fallback |
| Pane palette | 9 indicators | colours cycle `--chart-line-1..8` then repeat | none |
| Contrast test | every token pair | ratio ≥ 3:1 | test fails naming the token |
| Grep test | a `--color-active` read in `components/chart/` | test fails naming file and line | none |
| Rankings page | any | unchanged VGA dark, no chart token applied | none |

</intent-contract>

## Code Map

Filled at plan time from the live code (continuity from the 32.3 spec). Expected anchors: `platform/frontend/src/theme.css` (chart-token block started by 32.1), `index.css` (`.chart-legend`, `.chart-workspace`), `components/chart/paneColors.ts` (`cssVar`, `assignPaneColor`, `PANE_PALETTE`), `LightweightChart.tsx` (`createChart` options, candle/line/volume series options, marker and drawing colours), `pages/ChartPage.tsx` (`cssVar("--color-active", ...)` for drawings), `primitives/*` (any colour defaults), `pages/docs/kbData.ts`, `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §A8.1, `ChartPage.test.tsx` (theme-toggle assertion).

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map, ordered: tokens; `cssVar` on the container; route every colour; grep test; contrast test; legend halo; comments, spec and docs.

**Acceptance Criteria:**
- Given the chart page, when it renders, then the chart area is white with TradingView's classic colours and every other page is unchanged.
- Given the grep and contrast tests, when they run, then no chart file reads an app token or a literal colour and every chart colour meets 3:1 on white.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.

## Quick-dev record (2026-10-01)

Built by the operator's request as a quick-dev on branch `epic-32` (not through a bmad-loop
dev/review cycle): "32.4 dont need a story its just a quick change of colors on the chart".

**Shipped**
- `theme.css`: the `--chart-*` token set scoped to `.chart-workspace` (bg, grid, text, text-dim,
  border, crosshair, crosshair-label-bg, up, down, gap, marker, drawing, poc, pane-1..8), replacing
  Story 32.1's root-level `--chart-gap`; the "no toggle" comments in theme.css, index.css,
  `ChartPage.test.tsx` and spec §A8.1 now state that the chart area alone is light by operator
  decision (2026-09-30).
- `components/chart/chartTheme.ts`: the one fallback table, `chartVar` reading from the chart
  container (`.chart-workspace`, root fallback), `chartPalette`.
- Every chart colour read rewired to it: `LightweightChart.tsx` (layout, grid, crosshair, scale
  borders, candles, replay marker, measurement, trendline preview, drawing menu), `paneColors.ts`
  (the eight pane slots), `VolumeProfilePrimitive.ts` (point of control), `pages/ChartPage.tsx`
  (new horizontal lines and trendlines). `index.css`: the legend inside `.chart-workspace` reads
  in the chart text colour with a light halo.
- `chartTheme.test.ts`: the fallback table equals theme.css; every drawn token reads >= 3:1
  against `--chart-bg` (WCAG 2.1 graphics); the gap colour is unique; `chartVar` scoping; and a
  guard that the chart drawing files (LightweightChart, legend, paneColors, ChartPage, every
  primitive) contain no `--color-*`/`--vga-*` read and no colour literal.

**Values that differ from TradingView's, and why:** `--chart-up` #25a399 (TradingView's #26a69a
measures 2.998:1 on white), pane slot 4 #b26a00 (their #ff9800: 2.2:1), pane slot 6 #00838f (their
#00bcd4: 2.3:1). Gap #d84315, marker #455a64, poc #1b5e20, drawing #2962ff.

**Not done, deliberately (quick-dev scope):** `--chart-volume-up/--chart-volume-down` are not
declared: the volume pane is one histogram coloured by its pane slot today (`assignPaneColor`),
so per-bar up/down tokens would be dead config until Story 32.2 reshapes the volume pane; the
DocsPage has no chart section to update. `MetricTile` (history page) keeps the dark identity.

**Verification:** `tsc -p tsconfig.app.json --noEmit` clean; oxlint clean on the new files;
`vitest run` 431 passed (30 files); `npm run test:codegen` passed.
