---
baseline_commit: ca51b87400239a2066c0539c238cf8d0ef46831e
---

# Story 18.8: Session Volume Profile and Session Volume Profile HD

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want a volume profile computed per calendar session (day), with a higher-resolution variant available,
so that I can compare volume distribution session-over-session.

## Acceptance Criteria

1. **SVP groups candles by calendar day (session), one profile per day, using the base/finest timeframe data regardless of the chart's current bar size** — Indicators dialog, `overlay: true`.
2. **Recompute trigger: once per session boundary; only the current (in-progress) session's profile updates as new bars arrive** — not a full recompute of every rendered session on each new bar.
3. **SVP HD is a config preset of the *same* component as SVP, not a separate code path**: a higher default `rowCount` (100+ vs SVP's ~24) and a `respondsToZoom: true` flag that redraws (not recomputes) the Primitive on zoom-level changes so bar thickness stays legible.
4. **Settings include "number of past sessions to render"** (e.g. "show last 5"), each session keeping its own independent POC/VAH/VAL — never merged across sessions.

## Tasks / Subtasks

- [x] Task 1 — Calendar-day session grouping (AC: #1)
  - [x] Group the finest-available candle data by UTC calendar day (confirm which day boundary/timezone convention the rest of this codebase already uses for daily rollups — e.g. `troll/ranking_engine`'s or `ml_signals`' existing daily-window logic — and reuse it rather than introducing a second day-boundary convention).
  - [x] Add "Session Volume Profile" to the Indicators-style add dialog; each session's candle group feeds its own `buildVolumeProfile` call (Story 18.5), producing one `VolumeProfileSpec` per rendered session.

- [x] Task 2 — Session-boundary recompute (AC: #2)
  - [x] On a new bar, only the in-progress (current) session's `VolumeProfileSpec` is recomputed/updated; already-closed sessions' specs are untouched until the next session boundary closes and a new in-progress one begins.

- [x] Task 3 — HD preset (AC: #3)
  - [x] Implement as a settings/config variant of the same SVP component (e.g. an `hd: boolean` flag defaulting `rowCount` to 100+ and setting `respondsToZoom: true`) — not a second component or a duplicated recompute-trigger implementation.
  - [x] `respondsToZoom: true` wires a zoom-level subscription that calls the Primitive's redraw (not `buildVolumeProfile` again) — bar thickness/legibility only, no new calculation.

- [x] Task 4 — Multi-session settings (AC: #4)
  - [x] Extend Story 18.5's shared settings panel with a "sessions to render" count specific to SVP/PVP (Story 18.9); each rendered session is an independent `VolumeProfileSpec` entry with its own POC/VAH/VAL, never merged.

- [x] Task 5 — Tests
  - [x] A test confirming session grouping produces one profile per calendar day for a known multi-day candle array, and that only the most recent (in-progress) session's profile changes when a new bar is appended.

## Dev Notes

- **Find and reuse this project's existing calendar-day grouping convention before writing a new one** — daily rollups already exist somewhere in `ranking_engine`/`ml_signals` (per the multi-exchange spec's own guidance); a second, subtly different day-boundary definition would be a real (if quiet) correctness bug (e.g. UTC vs a different day-start convention causing off-by-one session assignment near midnight).
- **HD is explicitly a preset, not a fork** — if implementing SVP HD ever requires copy-pasting SVP's component, that's a signal the abstraction is wrong; go back and parameterize instead.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (SVP/SVP-HD add-dialog entries, session grouping + boundary-recompute logic).
- New (if no existing shared day-boundary utility is found): a small calendar-day-grouping helper, placed alongside `troll/frontend/src/lib/volumeProfile.ts`.
- Not modified: `VolumeProfilePrimitive`, `buildVolumeProfile` (consumed unchanged from Story 18.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.8] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.2 (SVP/SVP-HD rows), #A7.3] — session-boundary trigger, HD-as-preset guidance, multi-session settings.
- [Source: _bmad-output/implementation-artifacts/18-5-volume-profile-shared-engine-and-rendering-primitive.md] — shared engine/Primitive/settings this story extends.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

- **Day boundary convention:** UTC. The only place this codebase has a calendar day is `live_paper/fills_store.py` (per-UTC-day PnL); the rankings use rolling 24h windows, not sessions. So `lib/sessionProfile.ts` groups by UTC period (weeks Monday 00:00 UTC, months by UTC calendar month) -- built generic over `SessionPeriod` (`4h | daily | weekly | monthly`) so Story 18.9 only adds a dropdown.
- **Data source (a real gap in the story):** the chart's own candles are ~2h on open and only grow on scroll-back, so a day's profile can never be built from them. New `useSessionCandles` pages `/api/candles` back from now until the wanted start is covered (bounded: 40 pages x 500 bars), then once per bar interval refreshes only the newest 5 bars (replacing the forming bar) -- closed sessions are never re-fetched. Bar size is 1 minute (the finest the chart uses) for 4h/daily; coarser (5m/15m) for weekly/monthly (helper `sessionBarSeconds`, used by 18.9). If the page cap stops short, the oldest, only-partially-covered session is omitted (`completeFrom`) rather than shown truncated.
- `buildSessionProfiles`: one independent profile per period for the last N sessions with data (own POC/VAH/VAL), with a cache keyed by period/start/settings and validated by bar count + first/last bar, so on a new bar only the in-progress session is rebuilt (test asserts previous sessions keep object identity). Found and fixed a `slice(-0)` bug via the count=0 test.
- Placement: same reason as 18.7 (chart-only overlay, not a server-catalog entry) -- `SessionProfileControl` next to the other chart-overlay controls: two buttons over ONE state slot (SVP / SVP HD are presets in `SESSION_PRESETS`: rows 24 vs 120, `respondsToZoom` false vs true; picking the other switches, never stacks).
- `respondsToZoom` (AC #3, honest scope): implemented as a redraw-time adaptation in `VolumeProfilePrimitive` (drop the 1px inter-row gap when rows fall under 3px) -- no recompute, no extra subscription needed (the library already redraws primitives on every zoom/pan). Primitive also gained `widthFraction` (each session's longest bar spans 70% of the session) -- an additive change to 18.5's primitive, like 18.6's.
- Anchors come from the CHART's bars inside the session (a time anchor only resolves where the chart holds a bar), so a session partly outside the loaded window is drawn over the loaded part; its profile still covers the whole session. Sessions the chart holds none of are skipped until scroll-back loads them.
- Shared settings panel gained an optional "Sessions to render" field (only for settings carrying `sessionCount`, 1..10). Default 5. vitest 227 pass, tsc + oxlint clean. No real-browser check.

### File List

- troll/frontend/src/lib/sessionProfile.ts (new)
- troll/frontend/src/lib/sessionProfile.test.ts (new)
- troll/frontend/src/hooks/useSessionCandles.ts (new)
- troll/frontend/src/hooks/useSessionCandles.test.ts (new)
- troll/frontend/src/components/chart/SessionProfileControl.tsx (new)
- troll/frontend/src/components/chart/VolumeProfileSettings.tsx
- troll/frontend/src/components/chart/VolumeProfileSettings.test.tsx
- troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts
- troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.test.ts
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx

### Review Findings

- [x] [Review][Patch] **Live session profile went stale**: the refresh replaces the forming bar in place (same `t`), so a cache keyed on bar count + first/last time served the old profile [sessionProfile.ts] — fixed: cache validated by a content fingerprint (count, times, total volume, newest bar high/low/close) + test
- [x] [Review][Patch] `covered` was sticky, so asking for more sessions rendered truncated older ones as complete [useSessionCandles.ts] — fixed: coverage derived from the current wanted start + test
- [x] [Review][Patch] A failed page load left `loaded` false forever (refresh never ran) [useSessionCandles.ts] — fixed + test
- [x] [Review][Patch] Refresh limit fixed at 5 could leave a hole after a starved interval; out-of-order refresh responses could overwrite newer data; items of another bar size were merged [useSessionCandles.ts] — fixed (limit widened to bridge, response sequence guard, state tagged by bar size) + tests
- [x] [Review][Patch] Switching SVP <-> SVP HD reset the user's settings [ChartPage.tsx] — fixed (only rowCount takes the preset default) + test
- [x] [Review][Patch] Several settings panels on screen shared one accessible group name [VolumeProfileSettings.tsx] — fixed with a `title` prop
- [x] [Review][Defer] `respondsToZoom` only drops the 1px row gap on short rows (the library already redraws primitives on zoom/pan); a partly loaded session is drawn narrow (70% of its loaded span, zero for one bar); `sinceSeconds` is fixed at add time (no UTC-midnight re-anchor); each Sessions edit re-pages from now; replay far into the past finds no session history older than the fetch window; an empty server page across a long outage ends paging early (the `has_more` ceiling already deferred in 18.5) — deferred, polish for 18.10 / follow-ups

Dismissed as noise/handled: 8 (cross-instrument leakage — `ChartInner` is keyed per instrument; period mixing unreachable with daily-only presets, guarded anyway by the bar-size tag; cache mutation in `useMemo` is idempotent; etc.).
