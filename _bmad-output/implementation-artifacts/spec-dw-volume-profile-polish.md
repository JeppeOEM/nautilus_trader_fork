---
title: 'Volume profile polish: tokens, band runs, pixel snap, VRVP throttle/cue/clamp, session width, re-anchor, incremental paging, full settings persistence'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: 'ecd8d2c848bbc858c4893f47981233eda79e4fcc'
baseline_revision: 'fff23857320b9bee5b10d0c90ac2e91c66a13e92'
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/platform/CLAUDE.md']
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Stories 18.5-18.9 left rough edges in the volume-profile tools (DW-149, DW-151, DW-152, DW-153): the Value Area band paints in `upColor` and bridges rows that are off-scale; row gaps are fractional bitmap pixels; VRVP rebuilds on every pan frame, silently profiles only the loaded part of a range reaching past the oldest bar, and its 150px width overruns narrow panes; a session profile partly loaded (or with one chart bar) draws narrow or zero-width; `sinceSeconds` never re-anchors at a period rollover (items grow forever in a long-open tab); every Sessions/period edit re-pages history from now with no debounce, abort or loading cue; replay far in the past has no session history; only `rows`/`value_area_pct`/period are saved, never the session count, colours or POC/VA toggles; `SESSION_PRESETS.period` means two different things; the PVP dropdown shows raw values.

**Approach:** Fix in place: a `--chart-value-area` token; `layoutProfile` returns one band per contiguous drawable value-area run; bitmap-snapped row rects; `useVisibleRange` coalesces to one update per animation frame and reports whether the view extends past the oldest bar, shown as a cue in `VrvpControl`; a `maxWidthFraction` clamp; session anchors carry a seconds offset converted by bar spacing so a session spans its whole elapsed period; the session anchor time is state re-armed at each UTC period rollover (and is the replay cutoff while replaying); `useSessionCandles` debounces its request, pages only the missing older span (abortable), prunes older items, exposes `loading`; the layout's `volume_profile` table gains optional `sessions`, `up_color`, `down_color`, `show_poc`, `show_value_area`.

## Boundaries & Constraints

**Always:** Colours come only from `--chart-*` tokens via `chartVar` (token added to `chartTheme.ts` and `theme.css`, which `chartTheme.test.ts` pins equal). New layout keys are optional with defaults on both read and PUT, so every existing `chart_layouts.toml` loads unchanged (AD-D12 freeze: add, never rename/drop); strict validation names the bad key. Session coverage stays honest: a period only partly fetched is still omitted (`completeFrom`). MEM-01: session items stay bounded by `HARD_MAX_PAGES * PAGE_LIMIT`. Every deliberate ceiling gets a `Known limit:` comment with the upgrade path.

**Block If:** none -- every item is specified by the ledger entries.

**Never:** No change to `nautilus_trader/`, `crates/`, the candles route or `views/chart_series.py`. No new dependencies. No recompute of an HD profile's rows on zoom (stays a documented Known limit). Not editing the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| VA band with null row | VA rows 3..7, row 5 span null | two bands: rows 3-4 and 6-7 | none |
| Pixel snap | row y=10.3,h=4.6 at vr=1.5 | integer top/height, gap ≥1 device px unless `respondsToZoom` and short | height floor 1 |
| VRVP pan burst | 10 range events in one frame | one recompute after the frame | frame cancelled on unmount |
| VRVP past oldest bar | logical `from` < -0.5 | cue "covers loaded bars only" | none when inside |
| Narrow pane | pane 300px, VRVP 150px, max 0.3 | longest bar 90px | none |
| Partly loaded session | session start before first chart bar | anchor = first bar x minus (gap s / bar s × px per bar); width spans elapsed period × 0.7 | null coordinate → not drawn |
| Single-bar session | one chart bar inside the session | width > 0 (elapsed period) | none |
| Rollover | live tab passes UTC midnight (daily) | `sinceSeconds` moves one day later; items older pruned; no re-page | timer delay capped (setTimeout overflow) |
| Count increase | sessions 5 → 8 | after debounce, pages only from the oldest loaded bar back | in-flight page aborted on another edit |
| Count decrease | sessions 8 → 3 | no fetch, older items pruned | none |
| Replay far past | cutoff 10 days back | since anchored to cutoff; pages back within the hard cap | beyond cap: "Showing n of m" note |
| Restore | layout has sessions/colours/toggles | restored into the active kind's settings | missing keys → defaults |

</intent-contract>

## Code Map

- `platform/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts` -- `layoutProfile` (band, rows), renderer, `updateAllViews` anchor resolution.
- `platform/frontend/src/components/chart/primitives/VolumeProfilePrimitive.test.ts` -- layout tests.
- `platform/frontend/src/components/chart/chartTheme.ts`, `platform/frontend/src/theme.css`, `chartTheme.test.ts` -- token table.
- `platform/frontend/src/hooks/useVisibleRange.ts` -- VRVP trigger (no test file yet).
- `platform/frontend/src/hooks/useSessionCandles.ts` -- session history pager (no test file yet).
- `platform/frontend/src/lib/sessionProfile.ts` -- periods, presets, `buildSessionProfiles`, `drawableSpan`.
- `platform/frontend/src/lib/chartLayout.ts` (+ its test) -- layout shape and `normalizeLayout`.
- `platform/frontend/src/components/chart/{VrvpControl,SessionProfileControl}.tsx` -- controls.
- `platform/frontend/src/pages/ChartPage.tsx` -- `SessionConfig`/`sessionSince` (~201), VRVP/session memos (~760-830), layout save effect (~850), `allVolumeProfiles` (~892).
- `platform/frontend/src/pages/ChartPage.test.tsx` -- VRVP (~1290), session (~1393), PVP (~1490), layout restore (~2494) blocks; `useSessionCandles` mock (~87).
- `platform/views/preferences.py` (`_PROFILE_KEYS`, `_validate_profile`, `BUILTIN_DEFAULT_LAYOUT`, module docstring) and `platform/views/tests/test_chart_layouts.py`.

## Tasks & Acceptance

**Execution:**
- [x] `chartTheme.ts`/`theme.css` -- add `--chart-value-area` (a blue, drawn at low alpha; not in the contrast-floor `DRAWN` list) -- DW-149.
- [x] `VolumeProfilePrimitive.ts` -- `ProfileLayout.bands[]` (one per contiguous run of drawable rows inside [val, vah]); `maxWidthFraction?` clamps a numeric width to `paneWidth × fraction`; anchors accept `{ time, offsetSeconds? }` / `{ toTime, offsetSeconds? }` with `barSeconds?` on the spec, offset px = offsetSeconds / barSeconds × px-per-bar (from `logicalToCoordinate(1) - logicalToCoordinate(0)`); renderer draws bands in `chartVar("--chart-value-area")` and snaps every rect to integer bitmap pixels via an exported pure helper; document HD respondsToZoom Known limit -- DW-149/151/152.
- [x] `useVisibleRange.ts` -- subscribe to the logical-range change, coalesce to one `requestAnimationFrame` flush that reads `getVisibleRange()` and `getVisibleLogicalRange()`; return `{from, to, pastOldest}`; cancel the frame on cleanup; seed synchronously -- DW-151.
- [x] `useSessionCandles.ts` -- debounce `(barSeconds, sinceSeconds)` (300 ms; first request immediate); keep `{key, items, coveredFromMs}`; page from `coveredFromMs` (or now) back to `since` only when not covered, with an `AbortController`-style cancelled flag per run; prune items older than `since` when it moves later; refresh interval independent of `since`; total items capped; return `loading` -- DW-152/153.
- [x] `sessionProfile.ts` -- `periodEnd()` helper; `SessionProfileEntry.periodEnd`; preset field renamed `defaultPeriod` + `fixedPeriod` flag (SVP/HD fixed, PVP choosable); `SESSION_PERIOD_LABELS` -- DW-152/153.
- [x] `SessionProfileControl.tsx` / `VrvpControl.tsx` -- labelled options; "Loading session history…" status while `loading`; VRVP `pastOldest` cue -- DW-151/153.
- [x] `ChartPage.tsx` -- `SessionConfig` without `sinceSeconds`; anchor state re-armed by a timer at the next period start (delay capped at 24 h) or the replay cutoff; session specs with offsets, `barSeconds`, elapsed-period width; VRVP `maxWidthFraction`; restore/save sessions + colours + toggles for the saved kind -- DW-151/152/153.
- [x] `chartLayout.ts` + `views/preferences.py` -- optional `sessions` (1..10, default 5), `up_color`/`down_color` (`#rrggbb`, defaults = `DEFAULT_VOLUME_PROFILE_SETTINGS`), `show_poc`/`show_value_area` (bool, true) with read/PUT defaults, docstring updated -- DW-151/153.
- [x] Tests: primitive (bands, snap, clamp, offsets), new `useVisibleRange.test.ts` and `useSessionCandles.test.ts`, `sessionProfile` (periodEnd), `chartLayout` (new keys + fallbacks), ChartPage (VRVP rAF, cue, session width/offset, restore/save of new keys, loading), `test_chart_layouts.py` (defaults, bad colour/sessions named, old file loads).

**Acceptance Criteria:**
- Given an older `chart_layouts.toml` without the new keys, when loaded and saved back, then it loads and validates with defaults.
- Given all edits, when `npx vitest run`, `npx tsc -b`, `npm run lint` (in `platform/frontend`) and `python3 -m pytest views/tests/test_chart_layouts.py data_api/tests/test_layout.py` run, then all pass with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 1, medium 3, low 4)
- defer: 0
- reject: 8: (high 0, medium 0, low 8)
- addressed_findings:
  - `[high]` `[patch]` The refresh's "starved past one page" reset measured the newest held bar's age, so a quiet market or capture outage over 500 bars reset and re-paged everything every minute -- `FetchState.syncedMs` (when the last fetch reaching now was sent) now drives the bridge size and the reset; test added for a quiet market.
  - `[medium]` `[patch]` The reset cleared state before the old paging run was aborted, so a page landing in between could seed a stale cursor -- the run's controller is held in `runRef` and aborted before the clear; test added.
  - `[medium]` `[patch]` The paging loop's `MAX_ITEMS` guard read the previous key's items, so a period switch at the cap never loaded -- reads `forKey(...)`.
  - `[medium]` `[patch]` `useVisibleRange` listened to logical-range changes only, missing new data under unchanged indices (replay step into right whitespace, same-length setData) -- also subscribes the time-range change into the same per-frame read; test added.
  - `[low]` `[patch]` An aborted paging run left `loading` true -- cleared in the effect cleanup.
  - `[low]` `[patch]` A failed first page made every later edit skip the debounce -- "first request" is now the key never requested (`requestedKeyRef`), reset by the starvation reset.
  - `[low]` `[patch]` A session's left edge was the period start, so a coin listed mid-period spanned empty space -- anchored at the session's first bar (`entry.startTime`).
  - `[low]` `[patch]` The VRVP cue's comment claimed the gap is always temporary -- reworded (it can be the start of the history).

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 0
- reject: 16: (high 0, medium 2, low 14)
- addressed_findings:
  - `[medium]` `[patch]` A refresh in flight across a starvation reset merged the live tail into the emptied state (`coveredFromMs` null reads as fully covered), so a fragment drew as a complete session -- the refresh now drops its response when the state holds no paged coverage; test added (fails without the guard).
  - `[low]` `[patch]` The paging loop re-requested an identical page while a response reached no older than its cursor (up to 80 requests) -- breaks when `earliest >= cursorMs`; the partial-coverage test now asserts 2 calls, the page-budget test serves progressively older pages.
  - `[low]` `[patch]` The POC outline's `2 * hr` stroke straddled the snapped edges at fractional ratios -- even whole-pixel width.
  - `[low]` `[patch]` The session clock stopped during replay, so leaving replay rendered once from a stale period -- the rollover timer now keeps running (the anchor already prefers the cutoff).
  - `[low]` `[patch]` The seconds-offset extrapolation over missing/unloaded bars had no `Known limit:` -- added with its upgrade path in `timeX`.
  - `[low]` `[patch]` The monthly-rollover test's `setTimeout` spy was never restored -- restored in `finally`.

## Design Notes

Already resolved before this bundle: POC colour (`--chart-poc`, Story 32.4); PVP period and VRVP rows/VA% persistence (Story 32.6, one saved profile -- its Known limit stays); the empty-page `has_more` ceiling (DW-147, `has_older_data` in 31.8), so no client change for it.

Session geometry: anchor the drawable span's first chart bar with `offsetSeconds = periodStart - span.startTime` (≤ 0) and the last with `offsetSeconds = extentEnd - span.endTime`, where `extentEnd = min(periodEnd, entry.endTime + sessionBarSeconds)` -- the elapsed part of the period, so the forming session does not stretch into the future.

## Verification

**Commands:**
- `cd platform/frontend && npx vitest run && npx tsc -b && npm run lint` -- expected: all green
- `cd platform && python3 -m pytest views/tests/test_chart_layouts.py data_api/tests/test_layout.py -q` -- expected: pass


## Auto Run Result

**Summary:** Follow-up review of the volume-profile polish (DW-149, DW-151, DW-152, DW-153; commit e3c145aa49). A fresh Blind Hunter + Edge Case Hunter pass found no spec or intent problems; 6 localized patches were applied.

**Files changed (this pass):**
- `platform/frontend/src/hooks/useSessionCandles.ts` -- refresh dropped after a reset; paging stops on a non-advancing cursor.
- `platform/frontend/src/hooks/useSessionCandles.test.ts` -- reset/refresh race test; non-advancing-cursor assertion; page-budget test serves older pages.
- `platform/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts` -- POC stroke whole-pixel width; offset-extrapolation Known limit.
- `platform/frontend/src/pages/ChartPage.tsx` -- session rollover clock runs through replay.
- `platform/frontend/src/pages/ChartPage.test.tsx` -- `setTimeout` spy restored.

**Review findings:** 6 patches applied (1 medium, 5 low), 0 deferred, 16 rejected. Rejected items included:
- Claims the code already handles: `merge()` filters gap markers, and the settings panel clamps the session count.
- Existing documented Known limits: one saved profile kind per layout; the 40k-bar cap.
- Behaviour the spec mandates: elapsed-period width; `extentEnd` per the Design Notes.
- Problems that predate this change: 4xx retry; sub-bar periods on a coarser chart; `hd` with a non-daily period restored from a hand-edited file.
- Cosmetic or test-only nits.

**Verification:**
- `npx vitest run`: 40 files, 807 tests passed.
- `npx tsc -b`: clean.
- `npm run lint`: the same 3 warnings as at baseline.
- `pytest views/tests/test_chart_layouts.py data_api/tests/test_layout.py`: 67 passed.

**Residual risks:**
- An empty page with `has_more` true, or a page that does not advance, still ends the paging run with no retry until the wanted start or the key changes. This behaviour predates the change; the "Showing n of m" note surfaces it.
- Session edges over missing bars remain approximate (documented Known limit).
- The other residual risks from the first run still apply: the HD zoom Known limit, and one saved profile kind.
