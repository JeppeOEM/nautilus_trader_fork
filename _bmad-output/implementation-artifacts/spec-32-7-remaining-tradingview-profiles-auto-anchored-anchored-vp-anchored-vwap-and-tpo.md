---
title: 'Story 32.7: The rest of the TradingView profile family on the one shared engine: Auto Anchored, Anchored, Anchored VWAP and TPO'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '4b7bac0a9304fbfce83c0d589c7fde2e27a2243d'
final_revision: '96d70b1d83ca69a98c7b098244d89282d90850fa'
review_loop_iteration: 0
followup_review_recommended: true
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

Live anchors (verified at plan time on branch `epic-32`, after 32.6):
- `platform/frontend/src/lib/volumeProfile.ts` -- `buildVolumeProfile(candles, rowCount, valueAreaPct)` (the engine; volume spread evenly over touched rows), `ProfileRow {upVolume, downVolume}`, `buildRangeProfile`, `joinCandlesWithVolume`, `VolumeProfileSettings`.
- `lib/sessionProfile.ts` -- `periodStart`/`SessionPeriod`, `buildSessionProfiles` (per-period cache), `SESSION_PRESETS`, `drawableSpan`.
- `lib/drawings.ts` -- drawing wire types (`Drawing` union of hline/trendline/fib/position), `applyHandleDrag`, `snapIndex`, `storedTime`; `components/chart/primitives/drawingPrimitive.ts` (+ Trendline/Fib/Position primitives = the drawing template and hit-test handle mechanism); `hooks/useChartDrawings.ts`; `components/chart/DrawingSettingsDialog.tsx` + `SettingsDialogShell.tsx` (32.3's dialog).
- `primitives/VolumeProfilePrimitive.ts` (`xAnchor {time}`, `width {toTime}`), `primitives/VerticalMarkerPrimitive.ts`.
- `lib/chartLayout.ts` + `hooks/useChartLayout.ts` + Python `platform/views/preferences.py` (`PROFILE_KINDS`, `_PROFILE_KEYS`, `_validate_profile`, `BUILTIN_DEFAULT_LAYOUT`, `DRAWING_KINDS`, `_DRAWING_KEYS`, `validate_drawing`) -- the server is strict (DATA-07): an unknown kind/key is refused, so both sides change together.
- `pages/ChartPage.tsx` (profile state `sessionCfg`, `frvps`, tool rail, drawings), `components/chart/SessionProfileControl.tsx`, `VolumeProfileSettings.tsx`, `lib/units.ts`, `pages/docs/kbData.ts`, planning spec `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §A7, `platform/CLAUDE.md`.
- Tests: `lib/volumeProfile.test.ts`, `lib/sessionProfile.test.ts`, `lib/drawings.test.ts`, `VolumeProfilePrimitive.test.ts`, `ChartPage.test.tsx`, Python `platform/views/tests` (preferences).

## Design Notes

- Layout wire: the layout's single `volume_profile` table gains `kind` values `auto` and `tpo` (session-type, so still exclusive with `session`) and optional keys `anchor` (`session|week|month|highest_high|lowest_low|auto`, default `auto`), `ib_minutes` (initial balance length in minutes, default 60 = 2 x 30m), `letters` (bool, default false). Old files without the keys load with defaults; the server validates the same closed sets the frontend does.
- Drawings wire: `anchored_vp` {id, time, rows, value_area_pct, up_color, down_color} and `anchored_vwap` {id, time, source (`hlc3|close|ohlc4`), bands bool, color, band_color}; strict key sets in `_DRAWING_KEYS`.
- Engine: `buildVolumeProfile(..., valueAreaPct, weight = "volume")`; time weight sets each usable candle's weight to 1 and ignores volume (zero-volume candles still count), reusing the same row spread -- but the spread rule differs: a candle counts once in EVERY row it touches (not divided). Up/down split stays by close >= open.
- Anchor rules live in one pure helper (`lib/autoAnchor.ts`): `autoPresetFor(barSeconds)` named table (<= 900 s session, <= 14400 s week, else month), `anchorTime(preset, bars, barSeconds)`.
- Anchored VWAP maths in `lib/anchoredVwap.ts`: cumulative Σ(src·v)/Σv, volume-weighted variance Σ(v·(src-vwap_n)^2)/Σv computed with the running-moment form (Σv·src², guarded >= 0), skipping v = 0 points until the first volume.

## Tasks & Acceptance

**Execution:**
- [x] `lib/volumeProfile.ts` + test -- add `weight: "volume" | "time"` (time: one count per touched row) with hand-built-slice tests; volume behaviour unchanged.
- [x] `lib/autoAnchor.ts` + test -- preset table, anchor resolution (session/week/month/highest high/lowest low/auto), re-anchor on new bars and timeframe change.
- [x] `lib/anchoredVwap.ts` + test -- line + ±1σ/±2σ bands vs a hand computation, zero-volume skipping, sources.
- [x] `lib/tpo.ts` (+ test) -- per-row block counts, overflow cap constant (`TPO_MAX_BLOCKS_PER_ROW = 30`) with one longer bar, initial-balance range, letters.
- [x] `VolumeProfilePrimitive.ts` (+ test) -- block/letter rendering for TPO, initial-balance outline; marker via `VerticalMarkerPrimitive` for Auto Anchored.
- [x] `SessionProfileControl.tsx`/`ChartPage.tsx` -- Auto Anchored and TPO presets in the session slot (still one session-type profile), settings in the layout, re-anchor live.
- [x] `lib/drawings.ts`, drawing primitives, `ChartPage.tsx` tools + `DrawingSettingsDialog.tsx` -- `anchored_vp` and `anchored_vwap` one-click drawings, draggable anchor, context menu + settings modal, legend current VWAP value.
- [x] `platform/views/preferences.py` + `lib/chartLayout.ts` + tests -- the wire additions above (mirror tests for the closed sets).
- [x] Docs: DocsPage `kbData.ts` lists all nine profile tools; planning spec §A7; `platform/CLAUDE.md` SSOT note names the engine as the one profile calculation.

**Acceptance Criteria:**
- Given each new profile added, when the chart renders, then its rows come from `buildVolumeProfile` (volume or time weight) and the primitive, anchored as its preset or click defines, and it survives a reload.
- Given the Anchored VWAP with bands on, when computed on a fixture slice, then the line and bands equal a hand computation at the instrument precision, and zero-volume bars leave no NaN point.
- Given TPO rows with 3 and 1 touching candles, when built, then the counts are 3 and 1, the POC is the first, and a 40-touch row draws 30 blocks plus one longer bar.
- Given a timeframe change or a live session rollover with Auto Anchored on, when bars update, then the anchor is re-resolved and the marker moves.
- Given an old layout or drawings file, when loaded, then it loads unchanged; given an unknown kind or key, the server refuses it.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests -q` -- expected: all pass (preferences wire changes).

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 4, low 2)
- defer: 2: (high 0, medium 0, low 2)
- reject: 12
- addressed_findings:
  - `[medium]` `[patch]` Auto Anchored marker ref stale after Candles→Lines→Candles; reset in mode-flip effect + test.
  - `[medium]` `[patch]` TPO dropped candles lacking a volume datum; time weight now keeps them at volume 0 + tests.
  - `[medium]` `[patch]` Anchored VP/VWAP anchored past the newest bar snapped to the last bar; now omitted; anchors snap on the chart's own candle times.
  - `[medium]` `[patch]` Initial-balance input could not be cleared; local draft, commit on valid, restore on blur.
  - `[low]` `[patch]` `anchorBars` skips non-finite high/low.
  - `[low]` `[patch]` `Known limit:` comments for TPO's fixed 30m fetch cost and the per-bar anchored recompute.

## Auto Run Result

- **Summary:** Added Auto Anchored VP, TPO (session presets in the one session slot), Anchored VP and Anchored VWAP (one-click drawings) on the one `buildVolumeProfile` engine (`weight: "volume" | "time"`), persisted through the layout (`volume_profile.kind` auto/tpo + `anchor`, `ib_minutes`, `letters`) and drawings (`anchored_vp`, `anchored_vwap`) resources, with Python wire validation mirrored.
- **Files:** new `lib/{autoAnchor,anchoredVwap,tpo}.ts`, `primitives/Anchored{Vp,Vwap}Primitive.ts` (+ tests); changed `volumeProfile.ts`, `sessionProfile.ts`, `drawings.ts`, `chartLayout.ts`, `VolumeProfilePrimitive.ts`, `LightweightChart.tsx`, `SessionProfileControl.tsx`, `DrawingSettingsDialog.tsx`, `legend.ts`, `ChartPage.tsx`, `kbData.ts`, `views/preferences.py` (+ tests), planning spec §A7, `platform/CLAUDE.md` SSOT-06.
- **Review:** 6 patches applied, 2 deferred (colours not persisted for Auto/TPO; weak-test/doc wording nits), rest rejected.
- **Verification:** `npm test` 801 passed; `npm run build` clean; `python3 -m pytest views/tests` 362 passed; `npm run lint` 3 pre-existing warnings (`TrustedHtml.tsx`, `useCandles.ts`), none in touched files.
- **Residual risks / deviations:** profile colours for Auto Anchored/TPO are session-only (spec text says layout; the wire design has no colour keys, same as existing profiles). Anchored drawings older than loaded bars are not drawn until scrolled in (Known limit). New drawings/profiles are Candles mode only.
