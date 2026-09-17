# Story 17.5: Technicals tab — user-managed indicator columns

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want to add, configure, remove, and reorder indicator columns on the Technicals tab,
so that I can screen every coin on any of the chart's existing indicators without leaving the table.

## Acceptance Criteria

1. **The Technicals tab's column selection is reused from the exact same 37-entry catalog the chart already uses (`GET /api/indicators/catalog` — 34 native `chart_indicators.py` entries + 3 custom `custom_indicators.py` entries) — no second, curated indicator catalog, no calculation reimplementation.**
2. **Critical scope distinction, binding for this story: Technicals columns are screener-wide (one column set applied to every row/coin), NOT per-instrument.** This is a *different persistence scope* from Story 15.6's chart-page indicator config (`PUT /api/coin/{iid}/indicators`, which is per-coin). A new, separate, non-instrument-keyed persistence resource is required — reusing the per-coin endpoint for this would silently couple "which columns the screener shows" to "which panes one coin's chart shows," which is wrong on both sides.
3. **Adding a catalog entry adds it immediately as one or more columns with default parameters — no confirm step**, matching the chart's existing `IndicatorPicker` interaction (Part 0.3's "build once, use in both" rule).
4. **Multi-value entries (MACD, Bollinger Bands, Keltner Channel, Donchian Channel, Ichimoku Cloud, Directional Movement, etc.) render as a group of adjacent columns under one shared header** — not scattered unrelated columns.
5. **Changing a column's settings (gear icon) recalculates that column for every row immediately.**
6. **Columns are removable (×) and drag-reorderable by header** — a per-user view preference only, never touching underlying data.
7. **No new frontend dependency is introduced (NFR11)** — reuse the existing hand-rendered table and the generalized picker component (Task 2), not a grid/tab library.
8. **Test:** an integration test covers the new bulk values route returning correct per-instrument indicator values for at least one single-value and one multi-value catalog entry (TEST-01: touches real indicator calculation, no mocking).

## Tasks / Subtasks

- [ ] Task 1 — New backend: screener-wide (not per-instrument) column persistence (AC: #2)
  - [ ] New `troll/ml_signals/screener_columns_config.py`, modeled directly on `chart_indicator_config.py`'s existing shape (`tomllib`/`tomli_w`, full-file-rewrite, `IndicatorEntry` dataclass) but **flat, not keyed by instrument_id**: `load_config(path) -> list[IndicatorEntry]`, `save_config(path, entries) -> None`. A missing file returns `[]` (nothing configured yet), same "absent = empty" convention as the per-coin config.
  - [ ] New `data_api` routes (in `routes/rankings.py`, since this is a screener-scoped resource, not `routes/indicators.py`'s per-coin one): `GET /api/rankings/technicals-columns` and `PUT /api/rankings/technicals-columns`, wrapping `screener_columns_config.load_config`/`save_config` unchanged (AD-F2's config-persistence exception — same category as `PUT /api/coin/{iid}/indicators`).
  - [ ] New env var constant `SCREENER_COLUMNS_CONFIG_PATH`, same non-circular-import per-file pattern every existing route module uses; add the corresponding `:rw` bind mount to `data_api`'s `docker-compose.yml` service block (Story 15.6's Dev Notes flagged this exact gotcha for `CHART_INDICATOR_CONFIG_PATH` — don't repeat it silently here).

- [ ] Task 2 — Generalize `IndicatorPicker.tsx` for reuse, don't fork it (AC: #1, #3, #7)
  - [ ] `troll/frontend/src/components/chart/IndicatorPicker.tsx` currently hardcodes `fetchCoinIndicatorConfig(instrumentId)`/`saveCoinIndicatorConfig(instrumentId, next)` (per-coin persistence) inside the component. Refactor it to accept `fetchConfig: () => Promise<IndicatorConfigEntry[]>` and `saveConfig: (entries: IndicatorConfigEntry[]) => Promise<void>` as props instead of deriving them from `instrumentId` internally — this is the minimal change that lets the *exact same* add/remove/param-apply UI (lines 97-144 today) serve both the chart page (wrap it with the existing per-coin calls) and this story's Technicals tab (wrap it with Task 1's new screener-wide calls), per Part 0.3's "build once, use in both."
  - [ ] `ChartPage.tsx`'s existing usage passes `fetchConfig={() => fetchCoinIndicatorConfig(instrumentId)}`/`saveConfig={(e) => saveCoinIndicatorConfig(instrumentId, e)}` — no behavior change for the chart page.
  - [ ] `RankingsPage.tsx`'s Technicals tab renders `<IndicatorPicker fetchConfig={fetchTechnicalsColumns} saveConfig={saveTechnicalsColumns} onEntriesChange={setTechnicalsColumns} />` (new `client.ts` functions wrapping Task 1's routes) instead of a second, forked picker component.
  - [ ] Replace Story 17.1's Technicals empty-state placeholder with this picker plus the resulting column table (Task 3).

- [ ] Task 3 — New backend: bulk per-instrument indicator values (AC: #3, #4, #5)
  - [ ] **Required for this story to work end-to-end, even though it's not separately named in epics.md's AC — same "the picker needs data to draw" gap Story 15.6 flagged for its own values route.** A screener showing 20-50 coins × N selected indicators cannot fetch each coin's values individually (that's N separate chart-style history fetches per poll) — add a new bulk route, e.g. `GET /api/rankings/technicals-values?entries=<JSON-encoded [{name,params}]>`, returning `{instrument_id: {indicator_id: value_or_dict}}` for every currently-ranked instrument.
  - [ ] Implementation reuses `chart_indicators.replay_indicator`/`custom_indicators.replay_indicator` **unchanged**, per-instrument, over each instrument's own small recent candle window (only as many bars as the slowest requested indicator's period needs) — never a reimplementation of indicator math (AD-F2/DESIGN-01, same reuse discipline as Story 15.6's Task 2).
  - [ ] **Design call flagged for the implementer, not resolved here (same category as Story 15.6's Task 2 note):** refresh cadence for this route — recomputing N indicators × every ranked coin on every single rankings tick is likely excessive; a sensible poll interval (e.g. matching `RANKING_STALE_MS`'s cadence class, or simple React Query `staleTime`) is an implementation decision, not specified further.
  - [ ] Multi-value catalog entries (MACD, Bollinger Bands, etc.) return their full `Record<string, number>` per instrument; the frontend (Task 4) fans this out into the grouped adjacent columns AC #4 requires.

- [ ] Task 4 — Frontend: render Technicals columns (AC: #4, #5, #6)
  - [ ] **Correction confirmed by reading `chart_indicators.py`: the catalog route (`catalog_json()`) does NOT expose an `outputs` field today — only `params`/`panel`** (`IndicatorSpec.outputs` exists server-side but is never serialized into the catalog JSON). Multi-value grouping must be inferred from the *shape of Task 3's bulk-values response* for each entry (a nested `{line, signal, histogram}`-style object vs. a single number), matching how `ChartPage.tsx` already handles this today via `{indicator_id}.{output_attr}`-keyed pane ids — not from a catalog-declared field that doesn't exist. Render one `<th>`/`<td>` per single-value entry, or a grouped `<th>` spanning N `<td>`s per key present in a multi-value entry's response object.
  - [ ] × removes a column (calls `saveConfig` with the entry filtered out, same `persist()` pattern `IndicatorPicker` already uses internally).
  - [ ] Drag-reorder by header updates the persisted entries' order (a plain array reorder + `saveConfig`) — no new dependency, native HTML5 drag events or equivalent are sufficient for a small column count.

- [ ] Task 5 — Codegen + Tests (AC: #8)
  - [ ] Add Pydantic response models for Task 1's GET/PUT and Task 3's bulk-values route; regenerate `schema.ts`/`openapi.json`; add `fetchTechnicalsColumns`/`saveTechnicalsColumns`/`fetchTechnicalsValues` to `client.ts`.
  - [ ] `data_api/tests/test_screener_columns.py` (new): real `screener_columns_config.save_config`/`load_config` round-trip against a temp file; a bulk-values test asserting at least one single-value (e.g. RSI) and one multi-value (e.g. MACD) catalog entry return correct values for a real instrument's real candle data, matching what the chart page's own indicator route would return for the same instrument/params (cross-check — same computation, different destination, per Part 0.3).

## Dev Notes

- **The single most important design decision in this story is AC #2/Task 1's scope split — do not conflate it with Story 15.6's per-coin config.** A Technicals column exists once, screener-wide; a chart-page indicator exists once per coin. They happen to share a catalog and a UI component, never a persistence resource.
- **Reuse checklist before writing anything new:** indicator math (`chart_indicators.py`/`custom_indicators.py` — reuse, do not reimplement), the catalog route (`GET /api/indicators/catalog` — reuse verbatim, no new catalog), the add/remove/param-apply UI (`IndicatorPicker.tsx` — generalize via props, do not fork into a second component).
- **`troll/CLAUDE.md` constraints that apply:** AD-F2 (config persistence is the one sanctioned write path; the bulk-values route computes nothing new, it calls existing `replay_indicator` functions), DESIGN-01 (the picker is generalized via two injected functions — the smallest change that enables reuse, not a bigger abstraction), NFR11 (no grid framework).
- **Sequencing:** depends on Story 17.1's Technicals empty-state tab existing first (this story replaces that placeholder).

### Project Structure Notes

- New: `troll/ml_signals/screener_columns_config.py`, `troll/data_api/tests/test_screener_columns.py`.
- Modified: `troll/data_api/routes/rankings.py` (new columns GET/PUT + bulk values route), `troll/frontend/src/components/chart/IndicatorPicker.tsx` (generalized via `fetchConfig`/`saveConfig` props), `troll/frontend/src/pages/ChartPage.tsx` (updated call site, no behavior change), `troll/frontend/src/pages/RankingsPage.tsx` (Technicals tab real implementation), `troll/frontend/src/api/client.ts`/`schema.ts`/`openapi.json`, `troll/docker-compose.yml` (new config file `:rw` mount for `data_api`).
- Not modified: `troll/ml_signals/chart_indicators.py`, `troll/ml_signals/custom_indicators.py`, `troll/ml_signals/chart_indicator_config.py` (all reused unchanged), `troll/data_api/routes/indicators.py` (the per-coin resource stays exactly as Story 15.6 built it).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 17, Story 17.5] — this story's origin (FR55).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#0.3, #B2] — "build once, use in both" rule; the add/configure/remove/reorder mechanic this story implements for table columns.
- [Source: troll/frontend/src/components/chart/IndicatorPicker.tsx] — full file read this session; the exact component this story generalizes via dependency-injected `fetchConfig`/`saveConfig`, not forks.
- [Source: troll/ml_signals/chart_indicator_config.py] — full file read this session; `IndicatorEntry`/`load_config` shape this story's new `screener_columns_config.py` mirrors, minus the instrument_id keying.
- [Source: troll/frontend/src/api/schema.ts:22-32] — `IndicatorCatalogEntry`/`IndicatorConfigEntry` TS shapes this story's new routes and generalized picker reuse unchanged.
- [Source: troll/ml_signals/chart_indicators.py:41-48,340-348] — confirmed (validation pass) `IndicatorSpec.outputs` exists but `catalog_json()` does not serialize it; multi-value grouping must come from the bulk-values response shape instead.
- [Source: _bmad-output/implementation-artifacts/15-6-per-coin-indicator-configuration.md] — the precedent for this story's Task 3 (a values-fetching route required for end-to-end function even though not separately named in the epic's ACs) and the exact reuse discipline (`replay_indicator` unchanged) this story's bulk route follows.
- [Source: _bmad-output/implementation-artifacts/17-1-tab-shell-performance-technicals-tabs-on-the-rankings-page.md] — the Technicals empty-state placeholder this story replaces.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
