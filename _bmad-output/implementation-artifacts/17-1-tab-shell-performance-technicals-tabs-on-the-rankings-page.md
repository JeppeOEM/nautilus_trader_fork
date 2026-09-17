# Story 17.1: Tab shell — Performance + Technicals tabs on the Rankings page

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want the Rankings page to show a pinned Rank/Instrument column plus switchable Performance/Technicals tabs,
so that I can see either view without losing track of which coin's row I'm looking at, and without the page refetching or re-filtering rows just because I changed tabs.

## Acceptance Criteria

1. **`RankingsPage.tsx` renders a tab bar with exactly two tabs, "Performance" and "Technicals"; the `Rank` and `Instrument` columns stay pinned and visible regardless of which tab is active.**
2. **Design decision, binding for this story: the Performance tab shows today's existing 13-column `RANKING_COLS` table verbatim (OFI10z, OBI10/5/3, CVD, Spread, Vol delta 60s, Price, 1h %, 24h %, Vol(catalog), Vol Score, Vol24h) — no column is moved, renamed, removed, or reclassified.** Story 17.3 later adds further Performance columns on top of this; this story does not touch column content, only wraps the existing table in a tab.
3. **The Technicals tab renders an empty state ("no columns yet — click + to add one") — no columns exist yet.** Story 17.5 populates it later; this story only needs the empty-state placeholder.
4. **Switching tabs never triggers a refetch of `fetchRankings` or a new `useLiveChannel` subscription** — the row set and live-update behavior are identical regardless of active tab; only the rendered columns change.
5. **All existing behavior is preserved unchanged:** loading state before first message, per-row staleness (`isMessageStale`/`marketDataStale` badges), row click → `/chart/:iid` navigation, row order verbatim from the live message (no client-side re-sort).
6. **No new frontend dependency is introduced (NFR11)** — the tab bar is built with plain React state and the existing hand-rendered table, no grid/tab-component library.
7. **Test:** `RankingsPage.test.tsx` covers tab switching — Performance is the default/active tab on load, its 13 columns render; switching to Technicals hides them and shows the empty state; switching back to Performance shows them again without any additional network request or loss of live data.

## Tasks / Subtasks

- [ ] Task 1 — Tab state and tab bar (AC: #1, #6)
  - [ ] Add `const [activeTab, setActiveTab] = useState<"performance" | "technicals">("performance")` to `RankingsPage.tsx`, alongside the existing `now`/live-channel state — no new dependency, no router change (this is in-page state, not a new route).
  - [ ] **Reuse the existing `.tabs`/`.tabbtn`/`.tabbtn.active` tab visual pattern already established by `troll/frontend/src/pages/docs/DocsPage.tsx` (`docs.css`), rather than inventing a second tab visual style.** That CSS is currently scoped under `.signal-atlas` (the docs page's own wrapper class) — either move the three rules to a shared stylesheet (e.g. `theme.css` or a small shared CSS file) so both pages reference the same unscoped `.tabs`/`.tabbtn` classes, or duplicate the three rules under a Rankings-specific wrapper selector; do not leave two independently-defined but visually-identical tab implementations. Markup mirrors `DocsPage.tsx`'s pattern: `<div className="tabs"><div className={\`tabbtn${activeTab === "performance" ? " active" : ""}\`} onClick={() => setActiveTab("performance")}>Performance</div>...</div>`.

- [ ] Task 2 — Column-set switch, pinned columns preserved (AC: #2, #3, #5)
  - [ ] Keep the `<thead>`'s `Rank`/`Instrument` `<th>` and each row's `<td>{index + 1}</td>`/instrument-id `<td>` exactly as they are today — these render unconditionally regardless of `activeTab`.
  - [ ] Wrap the existing `RANKING_COLS.map(...)` header-cell and body-cell rendering (today's only column set) in `activeTab === "performance"` — no changes to `RANKING_COLS`, `formatCell`, `fmtSigned`/`fmtFixed`/`fmtPercent`/`fmtMillions`, or any staleness/formatting logic.
  - [ ] When `activeTab === "technicals"`, render a single empty-state row/message reading "no columns yet — click + to add one" (exact copy from the original build brief's §B7 step 3) spanning the table width — no `+`/add-column control yet, that arrives in Story 17.5.

- [ ] Task 3 — Confirm no refetch on tab switch (AC: #4)
  - [ ] `activeTab` must be a plain `useState` that only affects the JSX branch rendered — it must not appear in the `useQuery` `queryKey: ["rankings"]` array or in any effect dependency array that would cause `fetchRankings`/`useLiveChannel` to re-run. Verify this explicitly (e.g. a test asserting `fetchRankings` mock call count is unchanged across a tab switch).

- [ ] Task 4 — Tests (AC: #7)
  - [ ] Extend `troll/frontend/src/pages/RankingsPage.test.tsx` (currently covers: loading state, stale-row marking x2, row-click navigation, row-order-verbatim) with: Performance tab active by default and its columns visible; clicking Technicals hides Performance's columns and shows the empty-state text; clicking back to Performance restores the columns; a live update arriving while Technicals is active does not throw and is reflected immediately if the user switches back to Performance (no stale/cached render).
  - [ ] Run the existing frontend test/build/lint commands per prior stories' convention (Vitest + React Testing Library, already the project's pairing — no new test tooling).

## Dev Notes

- **Why Performance keeps every existing column verbatim, rather than "cleanly" splitting microstructure metrics (OFI10z/OBI/CVD/spread/volume_delta) away from true % -change metrics (pct_1h/pct_24h):** the original build brief's §B3 Performance tab is only % change columns, but this project's existing 13-column table already mixes ranking/microstructure metrics the operator relies on daily with the two % change columns. Moving or removing any of them is an operator-visible regression with no upside for this story, and none of the epics.md ACs for Epic 17 ask for it. Keeping them all under "Performance" verbatim is the zero-risk resolution — Story 17.3 adds new % change windows to this same tab; it does not reorganize what's already there.
- **`RANKING_COLS` in `RankingsPage.tsx` is a hand-declared TS mirror of `troll/ml_signals/ranking_columns.py`'s `RANKING_COLS` (SSOT-03) — this story does not touch either side of that mirror.** No column is added/removed/renamed, so there is no `bot_tui` parity action needed for this story (SSOT-04/05 only applies when a column changes — none does here).
- **This is a frontend-only story — no `data_api` route changes.** `fetchRankings` (`GET /api/rankings`) and the `/ws/live` `rankings:live` relay are both unchanged; the tab only changes which of the already-fetched row's fields are rendered.
- **Architecture spine convention respected:** "Live-refreshing list identity" (Consistency Conventions table) already requires rows keyed by `instrument_id`, not index — unaffected by this story, since the `key={row.instrument_id}` on `<tr>` doesn't change.
- **Sequencing:** Story 17.5 (Technicals tab population) depends on this story's empty-state Technicals tab existing first. Story 17.3 (Performance multi-window %) and Story 17.2 (unpark 15.8) can proceed in parallel with or after this story — neither depends on this one.
- **`troll/CLAUDE.md`/NFR11 constraints that apply:** DESIGN-01 (no new abstraction — plain `useState`, no tab-library), NFR11 (no grid/table framework).

### Project Structure Notes

- Modified: `troll/frontend/src/pages/RankingsPage.tsx`, `troll/frontend/src/pages/RankingsPage.test.tsx`; either `troll/frontend/src/theme.css` (or a new small shared CSS file) if the `.tabs`/`.tabbtn` rules are relocated out of `docs.css`'s `.signal-atlas` scope, or a Rankings-page-local CSS addition if they're duplicated instead — implementer's choice, but not both independently reinvented.
- Not modified: `troll/data_api/routes/rankings.py`, `troll/ml_signals/ranking_columns.py`, `troll/frontend/src/api/client.ts`/`schema.ts` (no API shape change), `troll/frontend/src/hooks/useLiveChannel.ts`, `troll/frontend/src/pages/docs/DocsPage.tsx`/`docs.css` (read for the reused tab pattern, not otherwise touched).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 17, Story 17.1] — this story's origin (FR51).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#B0, #B1] — the Rankings→Screener connection design and the pinned-column/tab-bar layout this story implements the shell for.
- [Source: troll/frontend/src/pages/RankingsPage.tsx] — full file read this session; current single-table implementation (`RANKING_COLS`, `formatCell`, staleness logic, row-click navigation) this story wraps in tabs without modifying its content.
- [Source: troll/frontend/src/pages/RankingsPage.test.tsx] — full file read this session; existing five tests (loading state, two staleness cases, row-click nav, row-order-verbatim) this story's Task 4 extends.
- [Source: troll/ml_signals/ranking_columns.py] — the true SSOT `RANKING_COLS`/`ranking_engine`-published column list `RankingsPage.tsx`'s array hand-mirrors (SSOT-03); confirmed unchanged by this story.
- [Source: troll/frontend/src/pages/docs/DocsPage.tsx:57-64, docs.css:73-79] — the existing `.tabs`/`.tabbtn`/`.tabbtn.active` tab pattern this story reuses rather than reinventing; confirmed currently scoped under `.signal-atlas`.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md] — "Live-refreshing list identity" and "State" conventions (Consistency Conventions table); AD-F2 (facade computes/relays only — reconfirms this story needs no backend change).

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
