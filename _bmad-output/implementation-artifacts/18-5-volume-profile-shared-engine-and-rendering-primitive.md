---
baseline_commit: 571b914115bbd6fa67fb13a11eb734563a0d652b
---

# Story 18.5: Volume Profile — shared engine and rendering Primitive

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want one consistent Volume Profile calculation and rendering behind every variant,
so that Fixed Range, Visible Range, Session, Session HD, and Periodic profiles behave predictably and share bug fixes.

## Acceptance Criteria

1. **`buildVolumeProfile(candles, rowCount, valueAreaPct)` is one pure function**, consumed by all five variants (Stories 18.6-18.9) — never duplicated per variant. Algorithm (verbatim from spec §A7.0): bucket the candle slice's price range into `rowCount` equal buckets; distribute each candle's volume evenly across every bucket its `low..high` range touches; classify each candle up/down (`close >= open`) and add its volume to that bucket's `upVolume`/`downVolume`; POC = bucket with highest `upVolume + downVolume`; Value Area = accumulate outward from POC (whichever adjacent bucket has more volume) until ≥ `valueAreaPct` of total volume.
2. **A single `VolumeProfilePrimitive` renders any `VolumeProfile` object** as a horizontal histogram (up/down-colored segments per row), with the POC row visually distinct and the Value Area band shaded — parameterized by x-anchor/width so all five variants call the same Primitive.
3. **Backend confirmation (§A7.5): `GET /api/candles/{instrument_id}` is sufficient for every variant** — no new backend route, no raw-snapshot/tick data. Verified in practice that `routes/candles.py`'s `_MAX_CANDLES_LIMIT`/`_MAX_QUERY_SPAN_SECONDS` server-enforced caps don't silently truncate the range a typical Fixed Range selection (Story 18.6) needs — raised if they do.
4. **Settings panel fields common to every variant** (row count, value area %, up/down colors, show/hide POC line, show/hide Value Area shading) are implemented once, reused by every variant's own settings (Stories 18.6-18.9 only add variant-specific fields on top).

## Tasks / Subtasks

- [x] Task 1 — `buildVolumeProfile` pure function (AC: #1)
  - [x] Implement in a new shared module, e.g. `troll/frontend/src/lib/volumeProfile.ts`, operating on the same `ChartDatum`/candle shape `useCandles.ts` already produces (`{ time, open, high, low, close }` — reuse `CandlestickData` fields, not a new candle type).
  - [x] Unit-test the algorithm directly against known small candle arrays (deterministic bucket assignment, POC selection, Value Area accumulation) — this is real calculation logic (TEST-01: financial/quantitative), not glue code.

- [x] Task 2 — `VolumeProfilePrimitive` (AC: #2)
  - [x] A `lightweight-charts` v5 `ISeriesPrimitive` implementation drawing the histogram/POC/Value-Area band, parameterized by `{ profile: VolumeProfile; xAnchor: number | "right"; width: number }` so Fixed Range's fixed-position anchor and Visible-Range/Session-style right-axis anchoring are just different constructor arguments to the same class.
  - [x] Attached/detached via `LightweightChart.tsx`'s existing declarative-registry pattern (a new `volumeProfiles?: VolumeProfileSpec[]` prop), same as `panes`/`priceLines`/`drawings`.

- [x] Task 3 — Backend range verification (AC: #3)
  - [x] Manually (or via an integration test) request `/api/candles/{iid}?before_ns=<old>&limit=<max>&bar_seconds=<small>` for a range comparable to a realistic Fixed Range selection (e.g. several days at 1-minute bars) and confirm the response isn't silently truncated below what was requested by `_MAX_CANDLES_LIMIT`/`_MAX_QUERY_SPAN_SECONDS` (`routes/candles.py`). If it is, raise those constants (a `data_api` change) rather than silently shipping a Volume Profile that can't cover its own advertised range.

- [x] Task 4 — Shared settings panel (AC: #4)
  - [x] A reusable settings sub-component (row count input, value area % input, up/down color pickers, POC/Value-Area visibility toggles) mounted from each variant's own gear-icon settings entry (Stories 18.6-18.9), not duplicated five times.

## Dev Notes

- **This story is pure infrastructure — it renders nothing on its own.** Stories 18.6-18.9 are what actually place a `VolumeProfilePrimitive` on the chart via a real variant's trigger/window logic. Land this story first; it has no user-visible behavior by itself.
- **Do not build true tick-level footprint math** (a different, heavier feature, explicitly out of scope per the original spec) and **do not try to unify FRVP's "confirm once" model with VRVP's "always recompute" model into one shared "live" component** — they consume the same engine/Primitive but have genuinely different trigger lifecycles (Stories 18.6/18.7 respectively).
- **`troll/CLAUDE.md` constraints that apply:** MEM-01 (a Fixed Range or Session profile calculation must still respect the candles route's bounded response, never an unbounded read), DESIGN-01 (one settings component, not five).

### Project Structure Notes

- New: `troll/frontend/src/lib/volumeProfile.ts` (`buildVolumeProfile`), a `VolumeProfilePrimitive` class (e.g. `troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts`), a shared settings sub-component.
- Modified: `troll/frontend/src/components/chart/LightweightChart.tsx` (new `volumeProfiles` prop + registry), possibly `troll/data_api/routes/candles.py` if Task 3 finds the range caps insufficient.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.5] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.0, #A7.1, #A7.3, #A7.5] — the full algorithm, Primitive rendering spec, shared settings fields, and the backend-sufficiency confirmation this story executes.
- [Source: troll/data_api/routes/candles.py] — partial file read this session (via grep); `_MAX_CANDLES_LIMIT`/`_MAX_QUERY_SPAN_SECONDS`/`before_ns`+`limit` contract this story's Task 3 verifies against.
- [Source: troll/frontend/src/hooks/useCandles.ts] — full file read this session (Story 18.4); the exact candle shape (`ChartDatum`) this story's `buildVolumeProfile` consumes.
- [Source: _bmad-output/implementation-artifacts/18-1-horizontal-line-drawing-tool.md] — the declarative-registry pattern (`priceLines`) this story's `volumeProfiles` prop follows.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

- `lib/volumeProfile.ts`: `buildVolumeProfile(candles, rowCount, valueAreaPct=0.7)` per spec §A7.0 (pure; flat range -> 1 row; empty/zero volume -> empty profile; POC tie -> lowest row; VA tie -> upper neighbour). `ChartDatum` has no volume (it lives in the separate `volume` array), so the engine takes `ProfileCandle` (`open/high/low/close/volume`) and `joinCandlesWithVolume(candles, volume)` pairs them by time, dropping gap entries.
- `VolumeProfilePrimitive` renders any profile from `{profile, xAnchor: number|"right", width, colors, showPoc, showValueArea}`; row geometry is the pure, tested `layoutProfile` (pane width is only known at draw time). New `volumeProfiles?: VolumeProfileSpec[]` prop on `LightweightChart` diffed by id like `drawings`; nothing places one yet (Stories 18.6-18.9).
- Shared settings: `VolumeProfileSettingsPanel` (rows 1..500, VA % 1..100, up/down colors, POC / value-area toggles) + `DEFAULT_VOLUME_PROFILE_SETTINGS` and the settings type in `lib/volumeProfile.ts`. `valueAreaPercent` is a percent in settings, a fraction in the engine.
- Task 3 (backend): no change needed. The caps only bound each response; added `test_multi_page_range_walk_covers_a_multi_day_selection_without_truncation` (3 days of 1-minute bars walked with `limit=500` pages returns every bar exactly once, `has_more` only False at the start). **Known ceiling:** `has_more` probes just one query-window back, so a collector outage longer than that window (~25h at 1-minute bars) stops pagination early even though older data exists -- pre-existing, recorded in deferred-work.
- vitest 166 pass, tsc + oxlint clean, data_api candles tests 9 pass. No browser visual check (nothing renders it yet).

### File List

- troll/frontend/src/lib/volumeProfile.ts (new)
- troll/frontend/src/lib/volumeProfile.test.ts (new)
- troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts (new)
- troll/frontend/src/components/chart/primitives/VolumeProfilePrimitive.test.ts (new)
- troll/frontend/src/components/chart/VolumeProfileSettings.tsx (new)
- troll/frontend/src/components/chart/VolumeProfileSettings.test.tsx (new)
- troll/frontend/src/components/chart/LightweightChart.tsx
- troll/frontend/src/components/chart/LightweightChart.test.tsx
- troll/data_api/tests/test_candles.py

### Review Findings

- [x] [Review][Patch] `rowCount` 0.5/NaN slipped past the guard and threw; huge/Infinity row counts unbounded [volumeProfile.ts] — fixed (floor first, `MAX_PROFILE_ROWS`=500 cap) + tests
- [x] [Review][Patch] `Math.min/max(...spread)` over a long history could exceed the argument limit [volumeProfile.ts] — fixed, loops + 300k-candle test
- [x] [Review][Patch] Non-finite OHLC/volume and low > high candles corrupted or dropped volume; NaN value-area % collapsed the area [volumeProfile.ts] — fixed + tests
- [x] [Review][Patch] A high exactly on a row boundary was credited to the next row too, diluting volume [volumeProfile.ts] — fixed (ceil-1 with float snap) + test
- [x] [Review][Patch] percent-vs-fraction trap: settings field renamed `valueAreaPercent` (engine keeps fraction `valueAreaPct`) [volumeProfile.ts]
- [x] [Review][Patch] Number inputs snapped back when momentarily emptied while typing [VolumeProfileSettings.tsx] — fixed with a local draft + test
- [x] [Review][Patch] Profile drew above the candles; now `zOrder: bottom` [VolumeProfilePrimitive.ts]; mis-described test fixture corrected
- [x] [Review][Defer] `xAnchor` is a pixel value, so a range-pinned profile (FRVP) will need a time->x anchor once 18.6 places one; POC color hardcoded and VA band reuses upColor; rows with null y-spans can bridge a Value Area gap; bar gap not bitmap-snapped — deferred to 18.6-18.10

Dismissed as noise/handled: 6 (BusinessDay time keys — repo uses UTC seconds; zero-volume candles not widening the range — deliberate; etc.).
