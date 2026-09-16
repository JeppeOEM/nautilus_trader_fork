---
baseline_revision: 1ec3aa65957fb0d8a3ca97dcda2e78ac8651f09b
followup_review_recommended: false
final_revision: f877f03fe077bd1d2e38f16c0b83801b0d068ef6
status: done
---

# Story 15.9: Terminal/ANSI visual identity

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want every page to render in a consistent DOS-style terminal aesthetic using only the classic 16-color VGA/ANSI palette,
so that the tool looks and feels like a serious terminal instrument for reading raw market data, not a generic web app.

## Acceptance Criteria

1. **The 16-color VGA/ANSI palette (black, blue, green, cyan, red, magenta, brown/yellow, light gray, dark gray, light blue, light green, light cyan, light red, light magenta, yellow, white) is declared once as design tokens** (CSS custom properties), and every page (rankings, chart, history, docs) sources **all** color — background, text, borders, semantic states, chart series — exclusively from those tokens; no color outside the set appears anywhere (FR46).
2. **A DOS/BIOS-style bitmap terminal font is applied as the sole typeface** for headings, body, tables, and chart labels across all four pages — no page falls back to a proportional/sans-serif face (exact font family is an implementation choice, PRD §8 Open Question 1).
3. **Box-drawing characters and terminal-style loading/empty states (blinking cursor, ASCII progress indicator) replace conventional web-app borders/dividers/spinners/skeleton screens** (exact placement/extent is an implementation choice, PRD §8 Open Question 2).
4. **Semantic color use (stale-data indicator, up/down candle, active/inactive UI) is drawn from the same 16-color token set, not a separate arbitrary palette** — this finalizes the pane-color assignment stubbed in Story 15.4.
5. **No CRT/scanline rendering is added** (explicitly out of scope, PRD Non-Goals).
6. **Verification (SM-3):** a manual palette check confirms no color outside the 16-color set appears anywhere in the built frontend, and the DOS-style font renders with no visible proportional-font fallback.

## Tasks / Subtasks

- [x] Task 1 — Design tokens: the 16-color palette as CSS custom properties (AC: #1, #4)
  - [x] New `frontend/src/theme.css` (or extend `frontend/src/index.css`, which today is explicitly a "minimal reset only... the terminal/ANSI visual identity... is Story 15.9's job, not this one's" placeholder) declaring exactly the 16 named colors from the PRD's Aesthetic and Tone section as `:root` custom properties (e.g. `--vga-black`, `--vga-blue`, ... `--vga-white`) — no more, no fewer.
  - [x] **This is a single fixed visual identity, not a light/dark toggle** — the whole point is a consistent VGA-terminal look; there is no PRD requirement to also honor the browser's OS light/dark preference, and doing so would work against FR46's "not merely an accent color layered onto an otherwise conventional web-app look." Do not add a `prefers-color-scheme` branch.
  - [x] Build a small semantic mapping table (e.g. `--color-stale`, `--color-up`, `--color-down`, `--color-active`, `--color-inactive`) each assigned to one of the 16 tokens — every component references the semantic name, never a raw palette token directly, so a future re-mapping (unlikely, but cheap to keep possible) touches one place.

- [x] Task 2 — Typeface: DOS/BIOS bitmap font, applied uniformly (AC: #2, #6)
  - [x] Choose and bundle a DOS/BIOS-style bitmap webfont (PRD names "Perfect DOS VGA 437" as an example direction, not a mandate) as a **local, checked-in font file** (`frontend/public/fonts/` or similar) loaded via `@font-face` — prefer bundling over an external CDN font-loading script: this is a personal, SSH-tunnel-only tool (per `troll/CLAUDE.md`'s Desktop↔VPS Connection section) that should not depend on reaching an external font CDN to render correctly, and the project's own dependency-minimization preference favors a vendored asset over a new runtime dependency. **Confirm the chosen font file's license permits bundling/redistribution before committing it** — this is an implementation-time check the PRD's own Open Question 1 leaves open, not something to assume.
  - [x] Apply via one `--font-terminal` custom property on `:root`, referenced by every page's body/heading/table/chart-label styling — no component introduces its own font-family. Fallback stack should still be monospace (never a proportional/sans-serif fallback, per AC #2) so a slow font load never visibly regresses the identity even momentarily.

- [x] Task 3 — ASCII-art motif: box-drawing borders + terminal-style loading/empty states (AC: #3)
  - [x] Replace conventional CSS borders/dividers on tables and panel edges (`RankingsPage.tsx`'s `<table>`, `ChartPage.tsx`'s panes, `HistoryPage.tsx`'s metric tiles, `DocsPage.tsx`'s sections) with a box-drawing-character treatment (e.g. `┌─┐│└─┘` frame elements or a CSS border-image built from them) — extent is an implementation call per PRD Open Question 2; don't over-build this into a generic "box component library" beyond what these four pages actually need (DESIGN-01).
  - [x] Two concrete, already-known must-fix spots: `frontend/src/App.tsx`'s `<Suspense fallback={<p>Loading…</p>}>` (route-transition loading state) and `frontend/src/pages/RankingsPage.tsx`'s `<p>Loading rankings…</p>` (pre-first-message state) — replace both with a terminal-style loading indicator (a blinking-cursor CSS animation, or an ASCII progress motif), not a web spinner/skeleton.

- [x] Task 4 — Apply tokens across all four pages; finalize Story 15.4's chart colors (AC: #1, #2, #4)
  - [x] `RankingsPage.tsx`, `HistoryPage.tsx`, `DocsPage.tsx`: replace any ad hoc inline styling (e.g. `RankingsPage.tsx`'s `style={{ opacity: rowStale ? 0.5 : 1 }}` stale-row treatment) with the Task 1 semantic tokens.
  - [x] `ChartPage.tsx`/`LightweightChart.tsx`: **`lightweight-charts` has its own default theming (background, grid lines, crosshair, candle up/down colors) that is not VGA-palette-derived** — explicitly configure every one of these via the library's chart/series options to the Task 1 tokens. This is a real, easy-to-miss gap: it's straightforward to reskin the surrounding page chrome and forget the chart library's own internal defaults, leaving the chart itself as the one non-conforming element on an otherwise-restyled page.
  - [x] Replace Story 15.4's placeholder pane-color array (`assignPaneColor`, built explicitly to be "finalized in Story 15.9" per that story's own Dev Notes) with the final 16-color-token-derived palette — this is the one named integration point between the two stories, not a rediscovery task.

- [x] Task 5 — Verification (AC: #5, #6)
  - [x] Manual, real-browser check (`run` skill) across all four pages: no CRT/scanline effect anywhere; the DOS font is visibly applied with no proportional-font fallback flash; box-drawing/terminal-style motifs replace conventional web chrome per Task 3's scope.
  - [x] A lightweight automated guard alongside the manual check: grep the built frontend's CSS/JS output for hex/`rgb(...)` color literals and confirm every one maps to a Task 1 token value — cheap to add, catches an accidental stray color a manual pass might miss, and gives SM-3 something more repeatable than "looked fine to me."

## Dev Notes

- **Sequencing:** this story is explicitly sequenced last among the visual/interaction stories (per `epic-15-context.md`: "Story 15.9 finalizes the pane-color assignment logic stubbed out in 15.4; it is not new logic") — it depends on Stories 15.1-15.8 having built every page it re-skins. Running it earlier would mean re-doing styling work as later stories add more UI surface (the rankings table, chart panes, indicator picker, history tiles, docs sections all need to exist before their final look can be applied).
- **Scope discipline:** this story's job is re-skinning existing, already-functional pages — it must not change behavior (data fetching, pagination, sync mechanics) built by any prior story. If applying the palette surfaces a genuine functional bug in an earlier story (e.g. a hardcoded color that was secretly load-bearing for something), fix the minimum needed and note it in Completion Notes rather than expanding this story's scope.
- **`troll/CLAUDE.md` constraints that apply:** DESIGN-01 (don't over-build the box-drawing motif into more than these four pages need), Minimize-dependencies preference (bundle the font locally rather than add an external CDN font-loading dependency — see Task 2), FR46/NFR-B (the identity must still hold at phone width — a box-drawing border that only looks right above some minimum column width needs a narrow-viewport fallback, verify at phone width during Task 5's manual check, not just desktop).

### Project Structure Notes

- New: `troll/frontend/src/theme.css`, `troll/frontend/public/fonts/<chosen-font-file>`.
- Modified: `troll/frontend/src/index.css` (or superseded by `theme.css` — implementer's call on the split), `troll/frontend/src/App.tsx`, every existing page component (`RankingsPage.tsx`, `ChartPage.tsx`/`LightweightChart.tsx`, `HistoryPage.tsx`, `DocsPage.tsx`), Story 15.4's pane-color-assignment module (e.g. `paneColors.ts`).
- Not modified: any `data_api` backend code — this story is frontend-only.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 15.9, lines 1499-1529] — this story's origin.
- [Source: _bmad-output/planning-artifacts/prds/prd-chart-frontend-rewrite-2026-09-13/prd.md, lines 174-194,225-229] — "Aesthetic and Tone" (full palette list, font direction, motif description, explicit CRT non-goal) and "8. Open Questions" (font family and motif-extent left to implementation), read in full this session.
- [Source: troll/frontend/src/index.css] — full file read this session; its own comment already names this story as the owner of the visual identity it's currently a placeholder for.
- [Source: troll/frontend/src/App.tsx, troll/frontend/src/pages/RankingsPage.tsx] — the two concrete known loading-state spots (Task 3) and the inline-style stale-row treatment (Task 4) that need token-based replacements.
- [Source: _bmad-output/implementation-artifacts/15-4-synced-indicator-panes.md] — "exact palette values finalize in Story 15.9... this story builds the assignment logic" — the exact hand-off point Task 4 completes.
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] — Cross-Story Dependencies confirming this story's sequencing and its relationship to Story 15.4.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- **Design tokens (Task 1):** `frontend/src/theme.css` declares exactly the 16 named VGA/ANSI
  colors (`--vga-black` … `--vga-white`, standard text-mode RGB values) as `:root` custom
  properties, plus a semantic layer (`--color-bg`, `--color-text`, `--color-text-strong`,
  `--color-text-dim`, `--color-border`, `--color-border-strong`, `--color-link`,
  `--color-active`/`--color-active-bg`, `--color-inactive`, `--color-stale`, `--color-warn`,
  `--color-danger`, `--color-positive`/`--color-negative`, `--color-up`/`--color-down`,
  `--color-tag-live`/`-rolling`/`-slow`/`-static`) each aliased onto exactly one `--vga-*`
  token. Every component/page reads only the semantic names. No `prefers-color-scheme`
  branch was added, per the Dev Note. `index.css` `@import`s `theme.css` and sets
  `html`/`body` background/color/font from the tokens.
- **Typeface (Task 2):** Chose **AcPlus IBM VGA 9x16** from VileR's "The Ultimate Oldschool
  PC Font Pack" (int10h.org) — a faithful recreation of the real IBM VGA ROM text-mode font,
  the exact aesthetic PRD Open Question 1 was pointing at. **License confirmed**: CC BY-SA
  4.0, verified two ways — (1) the font file's own embedded OpenType `name` table (nameID
  13/14) states "Creative Commons Attribution-ShareAlike 4.0 International" with the
  `creativecommons.org/licenses/by-sa/4.0/` URL directly in the binary, and (2) cross-checked
  against the font pack's own `LICENSE.TXT`. This permits bundling/redistribution with
  attribution, which is met by `frontend/public/fonts/ATTRIBUTION.txt`. The font was already
  present as a system font at `/usr/local/share/fonts/AcPlus_IBM_VGA_9x16.ttf` in this
  sandbox (matches the "pick a font already available via a system/package font path"
  fallback instruction) and was copied byte-for-byte into `frontend/public/fonts/`, unmodified
  (CC BY-SA's share-alike condition only binds a modified redistribution of the font itself,
  not applications that merely reference it via `@font-face`). Applied as one
  `--font-terminal` custom property (`"AcPlus IBM VGA 9x16", "Courier New", ui-monospace,
  monospace`) on `:root`, `font-display: block` so a slow load never shows a proportional
  fallback flash. `body { font-family: var(--font-terminal) }` plus a global
  `input, select, button, textarea { font-family: inherit }` rule (form controls don't
  inherit font-family from the page by default in browsers) — no component declares its own
  `font-family` (removed two redundant `font-family: monospace` declarations from
  `docs.css`, which now inherits from `.signal-atlas`).
- **Box-drawing motif + loading states (Task 3):** One small `.term-box` utility class
  (theme.css) applied at exactly 4 sites — `RankingsPage.tsx`'s table, `ChartPage.tsx`'s
  chart pane, `HistoryPage.tsx`'s placeholder, plus a docs.css-local equivalent for
  `.formula`/`.refs` — using a floating-legend technique (like an HTML
  `<fieldset>`/`<legend>`): a `::before` pseudo-element with real box-drawing glyphs
  (`┌─ LABEL`) painted over a background-matched gap in the border line. This is
  inherently responsive (no dash-run width computation needed) and was verified at 375px
  viewport width with no breakage (NFR-B) — see verification screenshots. `App.tsx`'s
  Suspense fallback and `RankingsPage.tsx`'s loading paragraph both got a `.term-loading`
  class whose `::after { content: "_"; animation: term-blink }` renders a blinking cursor
  via CSS only — the loading text itself (`"Loading rankings…"`) is untouched, so
  `RankingsPage.test.tsx`'s `getByText(/Loading rankings/i)` still passes (the cursor is a
  pseudo-element, invisible to `textContent`/RTL queries).
- **Tokens applied everywhere + pane colors finalized (Task 4):** `RankingsPage.tsx`'s
  `style={{ opacity: rowStale ? 0.5 : 1 }}` replaced with `tr[data-stale="true"] { color:
  var(--color-stale) }` (index.css). `IndicatorPicker.tsx`'s hardcoded `style={{ color: "red"
  }}` error text (found via a repo-wide grep for stray hex/`rgb`/named colors, not explicitly
  named in the story but a real AC #1 violation) changed to `var(--color-danger)`.
  `docs.css`'s entire local GitHub-dark palette (`--bg: #0d1117` etc., including several
  `rgba(...)` translucent tints) now aliases onto the global semantic tokens; the `*-bg`
  translucent tint variables became `transparent` (16-color set has no alpha channel) except
  `--accent-bg`, kept as a real solid `--color-active-bg` (VGA blue) highlight for
  `.tabbtn.active`/`.navitem.active` — a DOS-menu-style selection bar. `.pill` badges
  changed from filled translucent chips to `border: 1px solid currentColor; background:
  transparent` outline badges. All `border-radius` in `docs.css` zeroed (sharp terminal
  corners) except the circular `.navdot` status dot. `LightweightChart.tsx`'s `createChart()`
  now explicitly sets `layout.background`/`textColor`/`fontFamily`, `grid.vertLines`/
  `horzLines`, `crosshair.vertLine`/`horzLine` (including `labelBackgroundColor`, easy to
  miss — library default `#4c525e`), and `rightPriceScale`/`timeScale` `borderColor`
  (library default `#2B2B43`) — all from tokens via a new shared `cssVar()` helper
  (exported from `paneColors.ts`). The candlestick series now sets
  `upColor`/`downColor`/`borderUpColor`/`borderDownColor`/`wickUpColor`/`wickDownColor`
  plus the vestigial base `borderColor`/`wickColor` fields (library defaults `#378658`/
  `#737375`), verified absent from the built bundle. `paneColors.ts`'s placeholder
  5-color array replaced with an 8-color final palette of the brightest VGA tones
  (light-cyan/light-green/light-red/yellow/light-magenta/light-blue/brown/white, chosen
  for legibility against the black chart background), read via `cssVar()` at call time (not
  module-load time, so it works regardless of CSS-load ordering and degrades to literal
  fallback hex under jsdom in tests).
- **Verification (Task 5):** Ran the app for real via a headless-Chrome screenshot pass
  (`npm run dev`, `google-chrome --headless=new --virtual-time-budget=...`, since
  `chromium-cli` wasn't available in this sandbox) across Rankings (loading state, blinking
  cursor visible), Docs (home, KB detail with the architecture diagram, an indicator detail
  page showing the `.formula` box-drawing legend), History (placeholder in a `.term-box`),
  and Chart (empty chart with token-colored grid/axes, since `data_api` wasn't running to
  supply candle data) — confirmed black background, bitmap DOS font with no proportional
  fallback, VGA-derived colors throughout, DOS-menu-style solid-blue active-tab highlight,
  bordered outline badges, no CRT/scanline effect anywhere, and no breakage at 375px width.
  Grepped the production build (`dist/assets/*.css`, `dist/assets/*.js`) for hex/`rgb(...)`
  literals: `index-*.css` contains exactly the 16 VGA colors (as minified 3-digit shorthand)
  and nothing else; `DocsPage-*.css` contains zero color literals (fully token-derived);
  `ChartPage-*.js` retains `lightweight-charts`' own internal default-value constants as
  dead code (the library's source ships them regardless), but every option this component
  actually sets was audited against the library's type definitions
  (`node_modules/lightweight-charts/dist/typings.d.ts`) to confirm nothing renders from an
  unset default.
- **Deviations / notes:** `HistoryPage.tsx` has no metric tiles to apply a box-drawing
  treatment to — Story 15.8 (the 31-day metrics history page) was deferred per sprint-status
  commit `0157c2b2e9`, so this story only re-skinned the existing placeholder paragraph in a
  `.term-box`, per the Dev Notes' scope-discipline guidance (fix the minimum, note it, don't
  expand scope). `docs.css`'s `.infogrid` deliberately did NOT get the floating-legend
  box-drawing label (it already had a plain border) because it relies on `overflow: hidden`
  for its own 1px cell-gap-line technique, which would clip a label positioned above the
  border the same way the legend technique needs — kept as a plain bordered grid instead of
  forcing the same treatment onto an incompatible layout. `.refs`' initial legend label was
  removed after visual review found it duplicated the section's own pre-existing `<h2>Source</h2>` heading immediately below it; `.formula` (which has no such heading) kept the
  legend treatment. Two existing `LightweightChart.test.tsx` assertions
  (`toHaveBeenCalledWith("CandlestickSeries-sentinel")`, exact-arg-count) were loosened to
  `toHaveBeenCalledWith("CandlestickSeries-sentinel", expect.any(Object))` since the
  candlestick series now legitimately receives a color-options object as its second
  argument — a necessary test update to match the intentional Task 4 change, not a
  behavior/logic change.
- **Review pass patches (2026-09-16):** Blind Hunter + Edge Case Hunter review surfaced 6
  real, fixable issues, all auto-fixed (see Review Triage Log below): (1) restored
  `LightweightChart.test.tsx`'s weakened `expect.any(Object)` assertions to check the
  actual expected candlestick color options, since jsdom's `cssVar()` fallback path makes
  the resolved values deterministic and assertable; (2) fixed a CSS cascade bug where
  `.rankings-row:hover`'s text color could never win over `.rankings-row[data-stale]`'s
  (equal specificity, wrong source order) by reordering the rules; (3) added
  `color-scheme: dark` to `:root` (theme.css) plus explicit token-derived
  background/color/border/`appearance: none` styling for `select`/`button`/`input`/
  `textarea` (index.css) — native form-control chrome was previously untouched and
  rendered default light-OS colors, a real AC #1 violation ("no color outside the set
  appears anywhere") on IndicatorPicker's `<select>`/ChartPage's mode buttons; (4) forced
  `font-weight: normal` on all form controls since only one weight of the bitmap font is
  bundled, avoiding browser-synthesized fake-bold; (5) added a `document.fonts.ready`
  hook in `LightweightChart.tsx` that reapplies `layout.fontFamily` once the webfont
  finishes loading — canvas text (unlike DOM text) never re-flows on its own when a
  `@font-face` resolves after the chart was already created, so without this the chart's
  price/time-scale labels could permanently stick to the fallback font on a cold load.
  Re-verified after patching: `npm test` (54/54 pass), `npm run build` (clean), `npm run
  lint` (clean), and a second headless-Chrome pass across Rankings/Docs/Chart confirming
  no regressions and the production CSS still contains exactly the 16 VGA hex values.

### File List

- `troll/frontend/src/theme.css` (new) — 16-color VGA/ANSI tokens, semantic mapping, font-face, `.term-box`/`.term-loading` utilities
- `troll/frontend/public/fonts/AcPlus_IBM_VGA_9x16.ttf` (new) — bundled bitmap terminal font
- `troll/frontend/public/fonts/ATTRIBUTION.txt` (new) — font license/attribution
- `troll/frontend/src/index.css` — imports theme.css, base body/link/table/form-control styling from tokens, rankings-table/row styles
- `troll/frontend/src/App.tsx` — terminal-style Suspense loading fallback, token-based nav border
- `troll/frontend/src/pages/RankingsPage.tsx` — `.term-box` table wrapper, `.term-loading` state, removed inline opacity style
- `troll/frontend/src/pages/HistoryPage.tsx` — `.term-box` wrapper for the (still-placeholder) content
- `troll/frontend/src/pages/ChartPage.tsx` — `.term-box` wrapper around `LightweightChart`
- `troll/frontend/src/components/chart/LightweightChart.tsx` — explicit token-derived `createChart`/candlestick theming options
- `troll/frontend/src/components/chart/LightweightChart.test.tsx` — updated 3 assertions for the new candlestick options argument
- `troll/frontend/src/components/chart/paneColors.ts` — final 8-color token-derived palette, new shared `cssVar()` helper
- `troll/frontend/src/components/chart/IndicatorPicker.tsx` — replaced hardcoded `"red"` error text color with `var(--color-danger)`
- `troll/frontend/src/pages/docs/docs.css` — replaced local hardcoded/`rgba()` palette with token aliases, transparent pill backgrounds, zeroed border-radius, box-drawing legend on `.formula`

(Review pass additionally modified, already listed above: `theme.css` — `color-scheme: dark`; `index.css` — form-control token styling + hover/stale cascade fix; `LightweightChart.tsx` — `document.fonts.ready` hook; `LightweightChart.test.tsx` — exact candlestick-options assertion.)

## Review Triage Log

### 2026-09-16 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6 (high 1, medium 3, low 2)
- defer: 0
- reject: 9
- addressed_findings:
  - `[high]` `[patch]` Native `<select>`/`<button>`/`<input>` controls (IndicatorPicker's dropdown, ChartPage's Candles/Lines buttons) had no explicit color/background/border — they rendered default OS chrome colors, a direct AC #1 violation ("no color outside the set appears anywhere") on everyday-use interactive elements. Added token-derived styling + `appearance: none` (index.css) and `color-scheme: dark` (theme.css) so native popups/scrollbars also render dark.
  - `[medium]` `[patch]` `LightweightChart.test.tsx`'s candlestick-series assertions were weakened to `expect.any(Object)` during implementation, losing coverage of the actual token-derived color values (the literal point of AC #4's chart-theming requirement). Restored exact-value assertions, using the fact that `cssVar()`'s fallback path resolves deterministically under jsdom.
  - `[medium]` `[patch]` Canvas-rendered chart text (`lightweight-charts`) doesn't re-flow when a `@font-face` resolves after chart creation, unlike DOM text — a cold load could permanently show the fallback font on chart labels, the one page area AC #2 names explicitly. Added a `document.fonts.ready` hook that reapplies `layout.fontFamily` once the webfont loads.
  - `[medium]` `[patch]` Only one font weight was bundled; any `font-weight: 600` usage (e.g. docs.css `.pill`) would trigger browser-synthesized fake-bold on a pixel font. Forced `font-weight: normal` on form controls where this was reachable.
  - `[low]` `[patch]` `.rankings-row:hover` and `.rankings-row[data-stale="true"]` have equal CSS specificity; the stale rule's later source-order position meant hover text color could never apply to a stale row. Reordered so hover wins.
  - `[low]` `[patch]` `index.css`'s header comment claimed "no `color-scheme: light dark`" as justification for omitting `color-scheme` entirely, conflating it with the (correctly avoided) `prefers-color-scheme` toggle — corrected the comment once `color-scheme: dark` was added for the finding above.

Rejected (9): dim-text WCAG contrast ratio (intentional retro low-contrast dim text, no stated a11y requirement), box-drawing label text existing only in generated `::before` content (cosmetic, sighted single-operator tool), JS `cssVar()` fallback literals duplicating theme.css values (negligible drift risk, no practical failure mode), `paneColors.ts` keeping `--vga-brown` while excluding other non-"light" tones (subjective color-choice nitpick), uncached `getComputedStyle` calls in `palette()` (negligible cost, premature optimization per DESIGN-01), `.pill`'s `border: currentColor` with no base fallback (every current usage supplies a modifier class; theoretical-only), unverifiable-from-diff license claim (font metadata was inspected out-of-band, sufficient diligence for a personal project), unsubsetted 70KB font file (trivial size, no measurable impact), `HistoryPage`'s placeholder looking "finished" once boxed (cosmetic judgment call, not a defect). None of these are pre-existing issues surfaced incidentally (this story's diff caused all of them), so they were rejected rather than deferred — each was judged cosmetic/negligible-consequence for this personal, single-operator tool rather than a real defect.

## Change Log

- 2026-09-16: Story routed to dev-auto. Status: ready-for-dev → in-progress.
- 2026-09-16: Implemented via subagent. 16-color VGA/ANSI token set + semantic mapping (`theme.css`), bundled "AcPlus IBM VGA 9x16" bitmap font (CC BY-SA 4.0, license verified in-file), box-drawing `.term-box` motif + blinking-cursor loading states applied to all four pages, `lightweight-charts` fully re-themed, `paneColors.ts` finalized to an 8-color token-derived palette. Full frontend suite green (54/54), build/lint clean. Status: in-progress → in-review.
- 2026-09-16: Review pass (Blind Hunter + Edge Case Hunter in parallel). 6 patches applied (native form-control chrome unstyled — a real AC #1 violation; weakened test assertions restored to exact-value checks; canvas chart font not reapplied after async webfont load; faux-bold risk on form controls; stale-row hover CSS cascade bug; a stale/inaccurate code comment), 9 findings rejected as cosmetic/negligible for this personal single-operator tool. Re-verified: 54/54 tests, clean build/lint, second headless-Chrome pass across Rankings/Docs/Chart. Status: in-review → done.

## Auto Run Result

Status: done

**Summary:** Implemented Story 15.9 end-to-end: a single fixed DOS/BIOS terminal visual identity across all four dashboard pages (Rankings, Chart, History, Docs). Exactly the 16 classic VGA/ANSI colors are declared once as CSS custom properties (`theme.css`) with a semantic mapping layer every component reads instead of raw tokens; "AcPlus IBM VGA 9x16" (a faithful IBM VGA ROM font recreation, CC BY-SA 4.0, license verified against the font's own embedded metadata) is bundled locally and applied as the sole typeface everywhere, with no proportional fallback; box-drawing-character borders (a floating-legend `.term-box` technique, inherently responsive) and CSS-only blinking-cursor loading states replace conventional web chrome on the rankings table, chart pane, history tile, and docs sections; `lightweight-charts`' own internal defaults (background/grid/crosshair/candle colors) are now fully overridden from the same tokens, and Story 15.4's placeholder pane-color array is replaced with a final 8-color token-derived palette. No CRT/scanline effect was added (explicitly out of scope). A review pass then caught and fixed a real AC #1 gap (unstyled native `<select>`/`<button>` chrome rendering default OS colors) plus 5 smaller correctness issues.

**Files changed:**
- New: `troll/frontend/src/theme.css`, `troll/frontend/public/fonts/AcPlus_IBM_VGA_9x16.ttf`, `troll/frontend/public/fonts/ATTRIBUTION.txt`
- Modified: `troll/frontend/src/index.css`, `troll/frontend/src/App.tsx`, `troll/frontend/src/pages/RankingsPage.tsx`, `troll/frontend/src/pages/HistoryPage.tsx`, `troll/frontend/src/pages/ChartPage.tsx`, `troll/frontend/src/pages/docs/docs.css`, `troll/frontend/src/components/chart/LightweightChart.tsx`, `troll/frontend/src/components/chart/LightweightChart.test.tsx`, `troll/frontend/src/components/chart/paneColors.ts`, `troll/frontend/src/components/chart/IndicatorPicker.tsx`
- Not touched: `_bmad-output/implementation-artifacts/sprint-status.yaml` (orchestrator-owned, per this run's explicit instruction), any `data_api` backend code (frontend-only story, per spec)

**Review findings:** 6 patch (1 high: unstyled native form controls violating AC #1; 3 medium: weakened test assertions, canvas-font-reflow gap, faux-bold risk; 2 low: CSS cascade bug, stale comment), 0 defer, 9 reject (all cosmetic/negligible-consequence judgment calls appropriate for a personal, single-operator tool — see Review Triage Log for the full list). `followup_review_recommended: false` — all fixes were small, localized, contained entirely to frontend styling/tests, with no API, security, or data-model impact, and were re-verified by the full test/build/lint suite plus a second visual pass.

**Verification performed:** `npm test` (54/54, both before and after the review-pass patches), `npm run build` (clean, both passes), `npm run lint` (clean, pre-existing unrelated warnings only). Grepped the production CSS bundle for hex/`rgb(...)` literals both before and after patching: exactly the 16 VGA hex values present, nothing else, `DocsPage-*.css` has zero color literals. Ran the built app via `vite preview` + headless-Chrome screenshots (`google-chrome --headless=new --virtual-time-budget=...`) across Rankings (loading state, blinking cursor), Docs (full indicator-reference page with box-drawing card borders, tag pills, active-tab highlight), and Chart (empty chart — no `data_api` backend in this sandbox — showing token-colored grid/axes and the box-drawing pane legend); confirmed black background, bitmap DOS font rendering with no proportional fallback, no CRT/scanline effect, and no breakage at 375px width.

**Residual risk:** None rising to the level of a defer entry. The 9 rejected findings (WCAG contrast on intentionally-dim retro text, screen-reader accessibility of CSS-generated box labels, minor architectural nitpicks) are documented in the Review Triage Log if reconsidered later, but none affect this tool's actual single-operator usage.

This story has no acceptance criteria requiring a human/operator action outside the repo (no domain/DNS/API-key/vendor-console steps) — this is a normal `done`, not `awaiting-operator`.
