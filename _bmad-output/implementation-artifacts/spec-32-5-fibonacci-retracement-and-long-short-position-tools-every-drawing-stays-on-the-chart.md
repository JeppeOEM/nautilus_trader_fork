---
title: 'Story 32.5: Fibonacci retracement and Long/Short position tools, and every drawing stays on the chart'
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

**Problem:** The chart has trendlines (in-memory only, lost on navigation), horizontal lines (browser `localStorage`), a colour/delete context menu, and drag editing for horizontal lines alone. There is no Fibonacci retracement and no Long/Short position tool, and nothing drawn follows the operator to another browser.

**Approach:** Add Fib (drag), Long and Short (click) tools as series primitives; move every drawing (horizontal lines, trendlines, the new kinds) to one server-side resource per instrument; make every drawing's handles draggable through one hit-test mechanism; give the new kinds a settings modal (Story 32.3's dialog). As a prerequisite, the three UI preference files move from three per-file bind mounts to one mounted directory.

## Boundaries & Constraints

**Always:**
- **Preferences directory.** `docker-compose.yml` mounts `./data/preferences/:/app/preferences/:rw`; `chart_indicators.toml` and `screener_columns.toml` move there with `git mv`; a tracked empty `chart_drawings.toml` joins them; `data_api/settings.py` derives all three paths from one `CHART_PREFERENCES_DIR` (default `/app/preferences`); the two old path env vars are removed, and a compose file still setting them fails loudly at startup naming the new variable; the `Makefile`'s `VERIFY_KEEP` list and `docs/DEPLOY_CHECKLIST.md`'s Story 25.2 notes are updated; one "Deferred operator actions" entry names the VPS steps (stop `data_api`, copy the two live files into `data/preferences/`, `git pull`, `make up`; OPS-01, the story finalizes `done`).
- **Drawings resource.** `views/preferences.py` `load_chart_drawings`/`save_chart_drawings` (one table per instrument id, `v = 1`, tagged `kind` per item: `hline`, `trendline`, `fib`, `position`); `GET`/`PUT /api/coin/{instrument_id}/drawings`; a malformed item is a 422 naming the field, never dropped silently; the frontend persists on every change (debounced, one PUT per burst) and restores on mount; the first load after this story imports `chart-hlines:{iid}` from `localStorage` once and removes the key.
- **Tools.** Fib (click-drag through `attachRangeDrag`), Long and Short (one click) join `DRAWING_TOOLS`; spec §A8.1's left-rail list is updated; Esc cancels an in-progress placement; Cursor is selected after a placement, as for the other tools.
- **Fib geometry.** From anchor A to anchor B, one level per enabled ratio at `price = B + (A − B) × ratio` (0 on B, 1 on A); default on: 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1; default off: 1.272, 1.618, 2.618, 4.236; label "0.618 (price)" with the price through `lib/units.ts` at the instrument precision; levels extend to the right edge by default; a translucent band between consecutive levels; the A–B segment drawn faintly; both anchors draggable; settings modal: each ratio on/off and colour, extend right, label side, line width.
- **Position geometry.** Entry at the clicked price; stop at `STOP_PCT = 1 %` of entry; target at `2 ×` the stop distance (Long: target above, stop below; Short: mirror); profit zone entry→target in `--chart-up`, loss zone entry→stop in `--chart-down`, ~20 % alpha, from the placed bar to a right edge `DEFAULT_WIDTH_BARS = 40` later; labels "Target: price (+x.xx %)", "Entry: price", "Stop: price (−x.xx %)", "Risk/Reward: r.rr" (`|target − entry| ÷ |entry − stop|`, two decimals, recomputed on every drag); target, stop, entry and the right edge draggable (entry moves the whole box; a target dragged past the entry or a stop past it is refused); settings modal: entry/stop/target prices, width in bars, optional account size and risk % giving "Size: qty" = `account × risk% ÷ |entry − stop|` at the instrument's size precision when both are set (empty by default, persisted with the drawing).
- **Editing.** One hit-test/grab mechanism for every drawing's handles (extend `findClickedDrawingId` and the capture-phase mousedown grab), trendline anchors included; the context menu keeps colour and delete and gains "Settings…" for kinds with a modal; every drawing survives the Candles/Lines switch and the timeframe remount (anchors are time + price; a time between two bars snaps to the earlier bar for drawing only); replay, measurement and the volume profiles ignore drawings.
- **Numbers.** Every printed price goes through `lib/units.ts` at the instrument precision; no float noise in labels (test with a precision-2 and a precision-6 instrument).
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- The chart page has no access to the instrument's size precision for the "Size" label (check the candles response and `views`; if absent, block rather than guess).

**Never:**
- Touch `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Keep any drawing in `localStorage` after the one-time import.
- Keep per-file bind mounts for the preference files.
- Build the layout resource (32.6), Anchored VP/VWAP (32.7) or footprint (32.8).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fib down-drag | A = 100 at t1, B = 90 at t2 | levels 90, 92.36, 93.82, 95, 96.18, 97.86, 100 | none |
| Fib up-drag | A = 90, B = 100 | levels 100, 97.64, … , 90 (0 on B) | none |
| Long placed | click at 100 | stop 99, target 102, R/R 2.00 | none |
| Short placed | click at 100 | stop 101, target 98, R/R 2.00 | none |
| Target dragged to 103 | Long at 100/99 | R/R 3.00, "+3.00 %" | none |
| Target dragged below entry | Long | drag refused at entry + 1 tick | none |
| Size | account 10 000, risk 1 %, entry 100, stop 99 | "Size: 100" at size precision | none |
| Precision-6 instrument | entry 0.123456 | labels show 6 decimals, no noise | none |
| Reload | drawings saved | all drawings restored from the resource | none |
| Old browser hlines | `chart-hlines:{iid}` present | imported once, key removed | none |
| Malformed PUT item | `kind: "fib"` without anchors | 422 naming `anchors` | 422 |
| Old env var set | `CHART_INDICATOR_CONFIG_PATH` in compose | startup fails naming `CHART_PREFERENCES_DIR` | loud |
| Timeframe 1m → 1H | Fib anchored at 12:37 | drawn at the 12:00 bar; stored anchor unchanged | none |

</intent-contract>

## Code Map

Filled at plan time from the live code (continuity from the 32.4 spec). Expected anchors: `platform/frontend/src/pages/ChartPage.tsx` (`DRAWING_TOOLS`, `pendingAnchor`, `drawings`, hline `localStorage` helpers, context-menu handlers), `components/chart/LightweightChart.tsx` (`DrawingSpec`, `findClickedDrawingId`, the drawings registry effect, mousedown grab, context menu), `components/chart/rangeDrag.ts`, `primitives/TrendlinePrimitive.ts` (template), `lib/units.ts`, `api/client.ts`/`schema.ts`, `platform/views/preferences.py`, `platform/data_api/settings.py`, `data_api/routes/indicators.py` (route pattern), `platform/docker-compose.yml`, `platform/Makefile` (`VERIFY_KEEP`), `platform/docs/DEPLOY_CHECKLIST.md`, `platform/data/` (tracked TOML files), `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §A8.1, `pages/docs/kbData.ts`, tests: `ChartPage.test.tsx`, `views/tests/test_preferences.py`, `data_api/tests`.

## Tasks & Acceptance

**Execution:**
- [ ] Planned at dev time per the Code Map, ordered: preferences directory (compose, settings, git mv, Makefile, checklist); drawings resource + route + tests; frontend persistence + hline import; unified handle mechanism (trendline anchors); Fib primitive + tool + modal; Position primitive + tools + modal; docs and spec §A8.1.

**Acceptance Criteria:**
- Given a Fib drawn by drag and a Long placed by click, when the page reloads in another browser, then both are in place with the same levels and labels.
- Given a position's target dragged, when it moves, then the R/R and percent labels update and a target past the entry is refused.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.
