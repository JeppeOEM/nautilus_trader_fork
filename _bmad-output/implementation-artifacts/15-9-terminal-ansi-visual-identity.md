# Story 15.9: Terminal/ANSI visual identity

Status: ready-for-dev

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

- [ ] Task 1 — Design tokens: the 16-color palette as CSS custom properties (AC: #1, #4)
  - [ ] New `frontend/src/theme.css` (or extend `frontend/src/index.css`, which today is explicitly a "minimal reset only... the terminal/ANSI visual identity... is Story 15.9's job, not this one's" placeholder) declaring exactly the 16 named colors from the PRD's Aesthetic and Tone section as `:root` custom properties (e.g. `--vga-black`, `--vga-blue`, ... `--vga-white`) — no more, no fewer.
  - [ ] **This is a single fixed visual identity, not a light/dark toggle** — the whole point is a consistent VGA-terminal look; there is no PRD requirement to also honor the browser's OS light/dark preference, and doing so would work against FR46's "not merely an accent color layered onto an otherwise conventional web-app look." Do not add a `prefers-color-scheme` branch.
  - [ ] Build a small semantic mapping table (e.g. `--color-stale`, `--color-up`, `--color-down`, `--color-active`, `--color-inactive`) each assigned to one of the 16 tokens — every component references the semantic name, never a raw palette token directly, so a future re-mapping (unlikely, but cheap to keep possible) touches one place.

- [ ] Task 2 — Typeface: DOS/BIOS bitmap font, applied uniformly (AC: #2, #6)
  - [ ] Choose and bundle a DOS/BIOS-style bitmap webfont (PRD names "Perfect DOS VGA 437" as an example direction, not a mandate) as a **local, checked-in font file** (`frontend/public/fonts/` or similar) loaded via `@font-face` — prefer bundling over an external CDN font-loading script: this is a personal, SSH-tunnel-only tool (per `troll/CLAUDE.md`'s Desktop↔VPS Connection section) that should not depend on reaching an external font CDN to render correctly, and the project's own dependency-minimization preference favors a vendored asset over a new runtime dependency. **Confirm the chosen font file's license permits bundling/redistribution before committing it** — this is an implementation-time check the PRD's own Open Question 1 leaves open, not something to assume.
  - [ ] Apply via one `--font-terminal` custom property on `:root`, referenced by every page's body/heading/table/chart-label styling — no component introduces its own font-family. Fallback stack should still be monospace (never a proportional/sans-serif fallback, per AC #2) so a slow font load never visibly regresses the identity even momentarily.

- [ ] Task 3 — ASCII-art motif: box-drawing borders + terminal-style loading/empty states (AC: #3)
  - [ ] Replace conventional CSS borders/dividers on tables and panel edges (`RankingsPage.tsx`'s `<table>`, `ChartPage.tsx`'s panes, `HistoryPage.tsx`'s metric tiles, `DocsPage.tsx`'s sections) with a box-drawing-character treatment (e.g. `┌─┐│└─┘` frame elements or a CSS border-image built from them) — extent is an implementation call per PRD Open Question 2; don't over-build this into a generic "box component library" beyond what these four pages actually need (DESIGN-01).
  - [ ] Two concrete, already-known must-fix spots: `frontend/src/App.tsx`'s `<Suspense fallback={<p>Loading…</p>}>` (route-transition loading state) and `frontend/src/pages/RankingsPage.tsx`'s `<p>Loading rankings…</p>` (pre-first-message state) — replace both with a terminal-style loading indicator (a blinking-cursor CSS animation, or an ASCII progress motif), not a web spinner/skeleton.

- [ ] Task 4 — Apply tokens across all four pages; finalize Story 15.4's chart colors (AC: #1, #2, #4)
  - [ ] `RankingsPage.tsx`, `HistoryPage.tsx`, `DocsPage.tsx`: replace any ad hoc inline styling (e.g. `RankingsPage.tsx`'s `style={{ opacity: rowStale ? 0.5 : 1 }}` stale-row treatment) with the Task 1 semantic tokens.
  - [ ] `ChartPage.tsx`/`LightweightChart.tsx`: **`lightweight-charts` has its own default theming (background, grid lines, crosshair, candle up/down colors) that is not VGA-palette-derived** — explicitly configure every one of these via the library's chart/series options to the Task 1 tokens. This is a real, easy-to-miss gap: it's straightforward to reskin the surrounding page chrome and forget the chart library's own internal defaults, leaving the chart itself as the one non-conforming element on an otherwise-restyled page.
  - [ ] Replace Story 15.4's placeholder pane-color array (`assignPaneColor`, built explicitly to be "finalized in Story 15.9" per that story's own Dev Notes) with the final 16-color-token-derived palette — this is the one named integration point between the two stories, not a rediscovery task.

- [ ] Task 5 — Verification (AC: #5, #6)
  - [ ] Manual, real-browser check (`run` skill) across all four pages: no CRT/scanline effect anywhere; the DOS font is visibly applied with no proportional-font fallback flash; box-drawing/terminal-style motifs replace conventional web chrome per Task 3's scope.
  - [ ] A lightweight automated guard alongside the manual check: grep the built frontend's CSS/JS output for hex/`rgb(...)` color literals and confirm every one maps to a Task 1 token value — cheap to add, catches an accidental stray color a manual pass might miss, and gives SM-3 something more repeatable than "looked fine to me."

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

### File List
