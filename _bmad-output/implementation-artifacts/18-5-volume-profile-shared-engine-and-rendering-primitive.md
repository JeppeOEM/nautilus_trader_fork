# Story 18.5: Volume Profile — shared engine and rendering Primitive

Status: ready-for-dev

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

- [ ] Task 1 — `buildVolumeProfile` pure function (AC: #1)
  - [ ] Implement in a new shared module, e.g. `troll/frontend/src/lib/volumeProfile.ts`, operating on the same `ChartDatum`/candle shape `useCandles.ts` already produces (`{ time, open, high, low, close }` — reuse `CandlestickData` fields, not a new candle type).
  - [ ] Unit-test the algorithm directly against known small candle arrays (deterministic bucket assignment, POC selection, Value Area accumulation) — this is real calculation logic (TEST-01: financial/quantitative), not glue code.

- [ ] Task 2 — `VolumeProfilePrimitive` (AC: #2)
  - [ ] A `lightweight-charts` v5 `ISeriesPrimitive` implementation drawing the histogram/POC/Value-Area band, parameterized by `{ profile: VolumeProfile; xAnchor: number | "right"; width: number }` so Fixed Range's fixed-position anchor and Visible-Range/Session-style right-axis anchoring are just different constructor arguments to the same class.
  - [ ] Attached/detached via `LightweightChart.tsx`'s existing declarative-registry pattern (a new `volumeProfiles?: VolumeProfileSpec[]` prop), same as `panes`/`priceLines`/`drawings`.

- [ ] Task 3 — Backend range verification (AC: #3)
  - [ ] Manually (or via an integration test) request `/api/candles/{iid}?before_ns=<old>&limit=<max>&bar_seconds=<small>` for a range comparable to a realistic Fixed Range selection (e.g. several days at 1-minute bars) and confirm the response isn't silently truncated below what was requested by `_MAX_CANDLES_LIMIT`/`_MAX_QUERY_SPAN_SECONDS` (`routes/candles.py`). If it is, raise those constants (a `data_api` change) rather than silently shipping a Volume Profile that can't cover its own advertised range.

- [ ] Task 4 — Shared settings panel (AC: #4)
  - [ ] A reusable settings sub-component (row count input, value area % input, up/down color pickers, POC/Value-Area visibility toggles) mounted from each variant's own gear-icon settings entry (Stories 18.6-18.9), not duplicated five times.

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

### Debug Log References

### Completion Notes List

### File List
