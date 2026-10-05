---
title: 'Story 32.7: The rest of the TradingView profile family on the one shared engine: Auto Anchored, Anchored, Anchored VWAP and TPO'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '4b7bac0a9304fbfce83c0d589c7fde2e27a2243d'
final_revision: '7b03e9add262975ed4d1d5563ac49044e12e85f2'
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


### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 0, medium 3, low 4)
- defer: 0
- reject: 11
- addressed_findings:
  - `[medium]` `[patch]` An Anchored VP/VWAP placed (or dragged) onto the live forming bar was skipped as "after the newest bar": invisible, unselectable until the bar closed; both also lagged the forming bar. The anchored memo now appends the forming bar (`withFormingBar`, not under a replay) + tests.
  - `[medium]` `[patch]` An AVP anchored in a gap slot drew its anchor line on the slot while the profile started at the earlier real bar; the primitive is now handed the snapped bar + test.
  - `[medium]` `[patch]` TPO letters and the initial balance were keyed on bar order, so a missing 30m bar shifted every later letter and pulled later bars into the IB; both are now by time from the session start (`TpoClock`, `initialBalance(bars, sessionStart, ibMinutes)`) + tests.
  - `[low]` `[patch]` The initial balance's whole-30m-bar granularity was silent; `Known limit:` in `lib/tpo.ts` and the Docs page say so.
  - `[low]` `[patch]` TPO blocks/letters/IB were recounted for every session on every bar; kept per profile object (`tpoDetail`, WeakMap), so only the forming session recounts.
  - `[low]` `[patch]` The Anchored VP's settings could not open before the instrument precision loaded though its dialog prints no price; now it opens (other kinds still dropped) + test.
  - `[low]` `[patch]` The AVWAP line-colour picker showed black when no colour was stored; it now shows the drawing token the line is drawn in + test.

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 4, low 4)
- defer: 0
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` The anchored memo copied the whole history (`withFormingBar`, `timedBars`) on every forming-bar tick even on a coin with no Anchored VP/VWAP; it now returns the shared empties first (restored from the power-cut prior attempt `32-7-prior-attempt`, re-verified).
  - `[medium]` `[patch]` An Anchored VP placed on the newest/forming bar, and an Auto Anchored profile whose span is one bar (a period whose first bar just closed), resolved to a 0 px `{toTime}` width and drew nothing; `VolumeProfileRenderSpec.throughEndBar` adds the end bar's slot (one bar spacing) for both + primitive and page tests.
  - `[medium]` `[patch]` On a coarse chart (1W, month anchor) the Auto Anchored span and marker started on the first bar opening after the anchor (up to six days late) and the marker stood in at it; the span now starts at the bar holding the anchor and the marker only sits on a bar at or before the anchor + test (fails without the fix).
  - `[medium]` `[patch]` The Anchored VWAP line was drawn across gap slots; `breakAtGaps` marks the first point after a gap so the line and its hit test break there, plus a `Known limit:` on the bar-size-dependent source (restored from the prior attempt, re-verified with its tests).
  - `[low]` `[patch]` The Anchored VWAP anchor handle sat on the first bar with volume, not the stored anchor bar; it is now on the snapped anchor bar (the spec carries it, like the AVP) at the line's first value + test.
  - `[low]` `[patch]` The Anchored VWAP settings dialog (no price printed) could not open before the precision loaded; the prior attempt changed `requestSettings` but not the render guard, so its own test failed; both now admit `anchored_vwap`.
  - `[low]` `[patch]` `MIN/MAX/DEFAULT_IB_MINUTES`, `MIN/MAX_AVP_ROWS` and `MAX_PROFILE_ROWS` claimed to mirror Python but were unpinned; `test_profile_option_bounds_and_defaults_mirror_the_frontend`.
  - `[low]` `[patch]` The Docs tagline said all nine tools are "computed by one engine"; the Anchored VWAP has its own maths, reworded to eight profiles on one engine plus the VWAP line.

## Auto Run Result

- **Summary:** Second follow-up review of Story 32.7 (Auto Anchored VP, Anchored VP, Anchored VWAP, TPO on the one `buildVolumeProfile` engine), resuming the review cut off by a power loss: its pinned WIP (`32-7-prior-attempt`) was restored for the code files only (not `sprint-status.yaml`), re-verified (one of its tests failed and was completed), and extended. Eight patches; no spec or intent changes.
- **Files changed in this pass:** `pages/ChartPage.tsx` (+test: early return without anchored drawings, `breakAtGaps`, AVWAP snapped anchor, `throughEndBar` for AVP and Auto Anchored, Auto span from the bar holding the anchor, AVWAP dialog without precision), `primitives/VolumeProfilePrimitive.ts` (+test: `throughEndBar`), `primitives/AnchoredVwapPrimitive.ts` (+test: gap breaks, handle on the anchor bar), `lib/anchoredVwap.ts` (+test: `breakAtGaps`, source `Known limit:`), `pages/docs/kbData.ts` (tagline), `views/tests/test_chart_layouts.py` (mirror test for the option bounds/defaults).
- **Review:** 8 patches, 0 deferred, 13 rejected (AVWAP legend not following the crosshair: spec says current value; identical `AVWAP (hlc3)` labels: cosmetic; anchor older than the loaded history: documented `Known limit:`; TPO letter wrap on long periods and an IB longer than the period: operator settings, letters off by default; Auto extremes over the loaded set and closed bars only: as specified / the session family's closed-bar convention; IB intermediate keystrokes: save debounced; colour-string validation and the Python defaults literal: existing patterns; legend rebuild per tick: covered by the memo's `Known limit:`; dead `??` fallbacks: harmless; a zero-volume-since-anchor AVWAP having no hit area: spec'd "undefined until first volume"; "1W month anchor draws nothing for days": not reproducible, the anchor resolves from the last closed bar, so the span always holds it).
- **Verification:** `npm test` 43 files / 816 passed; `npm run build` clean; `npm run lint` 3 warnings, all pre-existing (`useCandles.ts`, `TrustedHtml.tsx`); `python3 -m pytest views/tests -q` 363 passed; `ruff check`/`ruff format` clean on the changed test. The new 1W Auto Anchored test was confirmed to fail with the old span rule.
- **Residual risks:** `throughEndBar` widens the AVP and Auto Anchored ranges by one bar spacing (a deliberate visual change, the other profiles unchanged); the anchored memo still recomputes each forming-bar tick when an anchored drawing exists (documented); Auto Anchored still re-anchors at a rollover once the new period's first bar closes.
