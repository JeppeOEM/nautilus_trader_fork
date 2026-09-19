---
baseline_commit: c3409a0bf0eea1aac7a4e0d0df1b6101db5982ff
---

# Story 18.7: Visible Range Volume Profile (VRVP)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want a volume profile that always reflects whatever's currently visible,
so that I get an at-a-glance profile without manually selecting a range.

## Acceptance Criteria

1. **VRVP is an Indicators-dialog entry** (§A4.1's add-dialog, reusing Story 15.6/17.5's existing indicator catalog mechanics as the placement pattern — though VRVP itself is a chart-only feature, not part of the 37-entry TA catalog; it uses the *same dialog interaction*, "click a result → added immediately"), `overlay: true` since it renders on the main price pane.
2. **`buildVolumeProfile` (Story 18.5) runs over whatever candles are currently in the visible time-scale range**, via `chart.timeScale().subscribeVisibleTimeRangeChange()`.
3. **Recomputes on every pan/zoom** — the "always recompute" model, explicitly distinct from Story 18.6 (FRVP)'s confirm-once model, never sharing one merged component with it (§A7.4).
4. **Only one VRVP instance is meaningful per chart** (unlike FRVP's multiple independent instances) — adding it again while already active updates/replaces the existing one rather than stacking a second.

## Tasks / Subtasks

- [x] Task 1 — Add-dialog entry (AC: #1)
  - [x] Add a "Visible Range Volume Profile" entry to wherever the chart's Indicators-style add dialog lives (Story 15.6/17.5's existing add mechanism) — clicking it immediately creates a `VolumeProfileSpec` with `xAnchor` set to auto-track the right edge of the current visible range.

- [x] Task 2 — Recompute on visible-range change (AC: #2, #3)
  - [x] Subscribe to `chart.timeScale().subscribeVisibleTimeRangeChange()`; on each fire, re-slice `candles` to the new visible range, re-run `buildVolumeProfile`, and update the existing `VolumeProfileSpec` entry (same id, new `profile`) — never appending a new entry per pan/zoom event.
  - [x] Unsubscribe cleanly when VRVP is removed or the chart unmounts.

- [x] Task 3 — Tests
  - [x] A test confirming a simulated visible-range change triggers exactly one profile recompute and updates (not duplicates) the existing spec entry.

## Dev Notes

- **Reuses Story 18.5's engine/Primitive/settings verbatim** — the only new logic here is the recompute trigger and its single-instance-per-chart semantics.
- **Do not merge with FRVP (Story 18.6) into one dynamic component** — explicit anti-pattern per §A7.4, restated here since it's the most likely shortcut a dev agent might reach for given how similar the two look superficially.

### Project Structure Notes

- Modified: `troll/frontend/src/pages/ChartPage.tsx` (VRVP add-dialog wiring, visible-range subscription, single-instance replace logic).
- Not modified: `troll/frontend/src/lib/volumeProfile.ts`, `VolumeProfilePrimitive` (consumed unchanged from Story 18.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 18, Story 18.7] — this story's origin (FR59).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A7.2 (VRVP row), #A7.4] — placement (Indicators dialog, `overlay: true`), always-recompute trigger, anti-merge-with-FRVP warning.
- [Source: _bmad-output/implementation-artifacts/18-5-volume-profile-shared-engine-and-rendering-primitive.md, 18-6-fixed-range-volume-profile-frvp.md] — the shared engine this story consumes and the sibling variant it must stay structurally distinct from.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

- `hooks/useVisibleRange.ts`: `subscribeVisibleTimeRangeChange` + an initial seed, returns `{from, to}` UTC seconds; identical repeats keep object identity, so consumers don't re-render on a plain resize.
- ChartPage: a single-instance `vrvp` (active flag + shared settings), profile = `useMemo(buildRangeProfile(replay.displayed, volume, visible.from, visible.to, settings))` -- rebuilt on every visible-range change, updating the same `"vrvp"` spec (`xAnchor: "right"`, 150px), never appended. Kept entirely separate from FRVP's stored `frvps` (§A7.4). Candles mode only. During replay it sees only revealed bars.
- **Placement deviation:** the story says "Indicators-dialog entry". The existing `IndicatorPicker` is a server-catalog-driven, per-coin persisted list (and the catalog must not be hand-duplicated on the frontend), while VRVP is chart-only with no backend counterpart. So it is a small "Chart overlays" control (`VrvpControl`) next to the picker: Add (idempotent -- adding while active does not stack), Remove, and the shared 18.5 settings panel. Same click-to-add interaction, no server round trip.
- Engine, primitive and 18.5 settings consumed unchanged. vitest 200 pass, tsc + oxlint clean. No real-browser check (profile drawing along the right axis unverified visually).

### File List

- troll/frontend/src/hooks/useVisibleRange.ts (new)
- troll/frontend/src/hooks/useVisibleRange.test.ts (new)
- troll/frontend/src/components/chart/VrvpControl.tsx (new)
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx

### Review Findings

- [x] [Review][Patch] The visible-range subscription stayed live after VRVP was removed (Task 2: unsubscribe when removed) [ChartPage.tsx] — fixed: the hook only gets a chart while VRVP is active in Candles mode + test
- [x] [Review][Patch] Add was a silent no-op while active, and an "active" VRVP drew nothing in Lines mode with no explanation [VrvpControl.tsx] — fixed: Add disabled while active/outside Candles mode, hint text in Lines mode + test
- [x] [Review][Patch] No test covering VRVP with replay (lookahead) [ChartPage.test.tsx] — added: bars past the replay cutoff are excluded
- [x] [Review][Defer] Profile rebuilt on every pan frame with no rAF throttle / range quantization; range beyond loaded candles is silently clamped to the loaded part; `Time` cast to number in the hook; fixed 150px width not clamped to narrow panes; settings not persisted across remount — deferred, polish for 18.10 (cost is O(visible bars) per frame)

Dismissed as noise/handled: 7 (stale range on chart swap / unsubscribe after remove — `ChartInner` is keyed per instrument and `useCandles` has the identical unsubscribe pattern; id-collision with FRVP — no `edges`, ids can't collide; etc.).
