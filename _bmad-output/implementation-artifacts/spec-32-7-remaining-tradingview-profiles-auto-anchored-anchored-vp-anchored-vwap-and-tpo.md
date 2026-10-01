---
title: 'Story 32.7: The rest of the TradingView profile family on the one shared engine: Auto Anchored, Anchored, Anchored VWAP and TPO'
type: 'feature'
created: '2026-09-30'
status: 'draft'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The chart has five profiles from Epic 18 (FRVP, VRVP, SVP, SVP HD, PVP) on one engine (`lib/volumeProfile.ts` `buildVolumeProfile`) and one primitive. TradingView's family also has an Auto Anchored Volume Profile, an Anchored Volume Profile drawing, an Anchored VWAP drawing and a Time Price Opportunity (TPO) profile, which the operator reads charts with.

**Approach:** Add the four on the same engine and primitive (spec §A7.0: one calculation). The engine gains `weight: "volume" | "time"` for TPO. Auto Anchored VP and TPO are session-control presets whose settings live in the layout (32.6); Anchored VP and Anchored VWAP are single-click drawings in the drawings resource (32.5) with draggable anchors and settings modals (32.3's dialog).

## Boundaries & Constraints

**Always:**
- **One engine.** Every new profile is computed by `buildVolumeProfile` and drawn by `VolumeProfilePrimitive`; no second implementation. `weight: "time"` counts each candle once per row it touches; `"volume"` is today's behaviour; unit-tested against a hand-built slice. The candle-level spread stays the documented `Known limit:` with Story 32.8's per-trade footprint as its upgrade path.
- **Auto Anchored VP** (session-control preset, one session-type profile per chart as today): presets `session`, `week`, `month`, `highest high`, `lowest low`, `auto` (`auto` by bar size: session for ≤ 15m, week for ≤ 4H, month above; a named table); spans anchor → latest bar, re-anchors on new bars and on every timeframe change; the anchor is marked by a `VerticalMarkerPrimitive`; settings (preset, rows, value-area %, colours) in the layout.
- **Anchored VP** (drawing, `kind: "anchored_vp"`, one click at a bar): the engine's profile from that bar to the latest bar, growing rightward from the anchor with FRVP's `{time}` anchoring, following new bars; anchor draggable through 32.5's handle mechanism; context menu + settings modal (rows, value area, colours).
- **Anchored VWAP** (drawing, `kind: "anchored_vwap"`, one click): `Σ(source × volume) / Σ volume` from the anchor bar onward as a line, optional ±1σ and ±2σ bands (volume-weighted standard deviation, a named helper unit-tested against a hand computation), current value in the legend; settings modal: bands on/off, source `hlc3` (default) / `close` / `ohlc4`, colours; anchor draggable.
- **TPO** (session-control preset `tpo`, per session or per day like SVP): engine with `weight: "time"`; each row's count drawn as blocks (one per candle-touch, capped per row at a named constant with the overflow drawn as one longer bar); POC and value area (70 % default) marked as for volume profiles; an "initial balance" band (first N bars of the session, default the 2 × 30m equivalent, a setting) outlined; `letters` display option off by default (blocks), letters A.. per candle when on.
- **Numbers.** Every printed price through `lib/units.ts` at the instrument precision.
- **Persistence.** Auto Anchored and TPO settings in `chart_layouts.toml` (32.6); the two drawings in `chart_drawings.toml` (32.5); nothing in `localStorage`.
- **Docs.** DocsPage chart section lists all nine profile tools (what each anchors to and what it counts); spec `spec-multi-exchange-screener-chart.md` §A7 amended for the four; `platform/CLAUDE.md`'s SSOT note names the engine as the one profile calculation.
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- Stories 32.5 or 32.6 are not merged (their resources are where these settings persist); block rather than persist elsewhere.

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Compute a profile from trades or seconds in this story (32.8 owns per-trade data).
- Allow two session-type profiles at once (today's rule stands).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Auto preset at 5m | bars over 3 sessions | anchor = current session start; profile to latest bar | none |
| Auto preset at 1D | same | anchor = month start | none |
| Highest-high preset | loaded bars | anchor = bar with the highest high in the loaded set | none |
| New session starts | live bar crosses session boundary | Auto Anchored re-anchors; marker moves | none |
| Anchored VP click | bar at t0 | profile from t0 to latest, grows with new bars | none |
| Anchored VWAP | anchor t0, source hlc3 | line = cumulative Σ(hlc3·v)/Σv; bands from weighted σ | none |
| VWAP with zero volume bars | v = 0 since anchor | line undefined until first volume; no NaN drawn | skipped points |
| TPO count | 3 candles touch row r, 1 touches row s | row r = 3 blocks, row s = 1; POC = r | none |
| TPO overflow | 40 touches, cap 30 | 30 blocks + one longer bar | none |
| Initial balance at 5m | first 12 bars of session | band spans their high..low | none |
| Timeframe change | Auto Anchored on | re-anchored by the new bar size's rule | none |
| Reload | drawings + layout saved | all four restored | none |

</intent-contract>

## Code Map

Filled at plan time from the live code (continuity from the 32.6 spec). Expected anchors: `platform/frontend/src/lib/volumeProfile.ts` (`buildVolumeProfile`, `joinCandlesWithVolume`), `lib/sessionProfile.ts` (session slicing, `SESSION_PRESETS`), `components/chart/SessionProfileControl.tsx`, `components/chart/VolumeProfileSettings.tsx`, `primitives/VolumeProfilePrimitive.ts` (`xAnchor: {time}`, `width: {toTime}`), `primitives/VerticalMarkerPrimitive.ts`, `primitives/TrendlinePrimitive.ts` (drawing template), `pages/ChartPage.tsx` (profile state, drawings, tools), `hooks/useChartLayout.ts` (32.6), `lib/units.ts`, `pages/docs/kbData.ts`, `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §A7, `platform/CLAUDE.md`, tests: `lib/volumeProfile.test.ts`, `lib/sessionProfile.test.ts`, `VolumeProfilePrimitive.test.ts`, `ChartPage.test.tsx`.

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map, ordered: engine `weight` option + tests; Auto Anchored preset + anchor rules + marker; TPO preset + block rendering + initial balance; Anchored VP drawing; Anchored VWAP drawing + σ helper; settings modals; layout/drawings persistence; docs and spec §A7.

**Acceptance Criteria:**
- Given each new profile added, when the chart renders, then its rows come from `buildVolumeProfile` (volume or time weight) and the primitive, anchored as its preset or click defines, and it survives a reload.
- Given the Anchored VWAP, when bands are on, then the line and bands equal a hand computation on a fixture slice at the instrument precision.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
