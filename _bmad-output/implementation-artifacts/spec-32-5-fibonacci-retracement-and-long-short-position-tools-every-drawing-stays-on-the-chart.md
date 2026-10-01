---
title: 'Story 32.5: Fibonacci retracement and Long/Short position tools, and every drawing stays on the chart'
type: 'feature'
created: '2026-09-30'
status: done
review_loop_iteration: 0
followup_review_recommended: false
final_revision: 69afc38cdffce6fd7dc1e3379f0eb9324e3b0e2e
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-32-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
baseline_revision: 9f9acb17524ebcf6d6edd0cc89e8a9337bf36e2b
---

<intent-contract>

## Intent

**Problem:** The chart has trendlines (in-memory only, lost on navigation), horizontal lines (browser `localStorage`), a colour/delete context menu, and drag editing for horizontal lines alone. There is no Fibonacci retracement and no Long/Short position tool, and nothing drawn follows the operator to another browser.

**Approach:** Add Fib (drag), Long and Short (click) tools as series primitives; move every drawing (horizontal lines, trendlines, the new kinds) to one server-side resource per instrument; make every drawing's handles draggable through one hit-test mechanism; give the new kinds a settings modal (Story 32.3's dialog). As a prerequisite, the three UI preference files move from three per-file bind mounts to one mounted directory. As a second prerequisite (amended 2026-10-01 by the operator after the first dev attempt blocked on the Block If below), the chart page is given the instrument's price and size precision from the catalog's own instrument definition, since no web route carried either.

## Boundaries & Constraints

**Always:**
- **Instrument precision (prerequisite, amended 2026-10-01).** `GET /api/candles/{instrument_id}` gains two integer fields, `price_precision` and `size_precision`, read by one `views/` function (beside `views/catalog_reads.py`'s catalog access) from the catalog's instrument definition (`ParquetDataCatalog.instruments(instrument_ids=[...])`, `Instrument.price_precision`/`size_precision`), never derived from a value's digit count and never a hard-coded default; an instrument with no definition in the catalog is a 404 naming the id, so no label is ever printed at a guessed precision (DATA-01). The TS schema (`api/schema.ts`) carries both, `ChartPage` holds them in state from the first candles response, and every label of this story formats through `lib/units.ts` with them. Tested: a `views` test on a fixture catalog (precision-2 and precision-6 definitions), a `data_api` route test asserting both fields and the 404, a frontend test asserting the label precision.
- **Preferences directory.** `docker-compose.yml` mounts `./data/preferences/:/app/preferences/:rw`; `chart_indicators.toml` and `screener_columns.toml` move there with `git mv`; a tracked empty `chart_drawings.toml` joins them; `data_api/settings.py` derives all three paths from one `CHART_PREFERENCES_DIR` (default `/app/preferences`); the two old path env vars are removed, and a compose file still setting them fails loudly at startup naming the new variable; the `Makefile`'s `VERIFY_KEEP` list and `docs/DEPLOY_CHECKLIST.md`'s Story 25.2 notes are updated; one "Deferred operator actions" entry names the VPS steps (stop `data_api`, copy the two live files into `data/preferences/`, `git pull`, `make up`; OPS-01, the story finalizes `done`).
- **Drawings resource.** `views/preferences.py` `load_chart_drawings`/`save_chart_drawings` (one table per instrument id, `v = 1`, tagged `kind` per item: `hline`, `trendline`, `fib`, `position`); `GET`/`PUT /api/coin/{instrument_id}/drawings`; a malformed item is a 422 naming the field, never dropped silently; the frontend persists on every change (debounced, one PUT per burst) and restores on mount; the first load after this story imports `chart-hlines:{iid}` from `localStorage` once and removes the key.
- **Tools.** Fib (click-drag through `attachRangeDrag`), Long and Short (one click) join `DRAWING_TOOLS`; spec §A8.1's left-rail list is updated; Esc cancels an in-progress placement; Cursor is selected after a placement, as for the other tools.
- **Fib geometry.** From anchor A to anchor B, one level per enabled ratio at `price = B + (A − B) × ratio` (0 on B, 1 on A); default on: 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1; default off: 1.272, 1.618, 2.618, 4.236; label "0.618 (price)" with the price through `lib/units.ts` at the instrument precision; levels extend to the right edge by default; a translucent band between consecutive levels; the A–B segment drawn faintly; both anchors draggable; settings modal: each ratio on/off and colour, extend right, label side, line width.
- **Position geometry.** Entry at the clicked price; stop at `STOP_PCT = 1 %` of entry; target at `2 ×` the stop distance (Long: target above, stop below; Short: mirror); profit zone entry→target in `--chart-up`, loss zone entry→stop in `--chart-down`, ~20 % alpha, from the placed bar to a right edge `DEFAULT_WIDTH_BARS = 40` later; labels "Target: price (+x.xx %)", "Entry: price", "Stop: price (−x.xx %)", "Risk/Reward: r.rr" (`|target − entry| ÷ |entry − stop|`, two decimals, recomputed on every drag); target, stop, entry and the right edge draggable (entry moves the whole box; a target dragged past the entry or a stop past it is refused); settings modal: entry/stop/target prices, width in bars, optional account size and risk % giving "Size: qty" = `account × risk% ÷ |entry − stop|` at the instrument's size precision when both are set (empty by default, persisted with the drawing).
- **Editing.** One hit-test/grab mechanism for every drawing's handles (extend `findClickedDrawingId` and the capture-phase mousedown grab), trendline anchors included; the context menu keeps colour and delete and gains "Settings…" for kinds with a modal; every drawing survives the Candles/Lines switch and the timeframe remount (anchors are time + price; a time between two bars snaps to the earlier bar for drawing only); replay, measurement and the volume profiles ignore drawings.
- **Numbers.** Every printed price goes through `lib/units.ts` at the instrument precision; no float noise in labels (test with a precision-2 and a precision-6 instrument).
- Add no new dependency. Keep the TEST-04 test style.

**Block If:**
- The catalog holds no instrument definition for an instrument the chart serves (check the fixture catalog's `instrument` files and `ParquetDataCatalog.instruments`); if definitions are genuinely absent, block rather than hard-code or infer a precision. (The original "no access to the size precision" clause triggered on 2026-10-01; the operator resolved it with the "Instrument precision" prerequisite above, so the absence of a precision on the current candles response is no longer a block but this story's first task.)

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
| Candles for a defined instrument | catalog definition precision 2 / size precision 3 | response carries `price_precision: 2`, `size_precision: 3` | none |
| Candles for an undefined instrument | no definition in the catalog | 404 naming the instrument id; no label rendered | 404 |
| Reload | drawings saved | all drawings restored from the resource | none |
| Old browser hlines | `chart-hlines:{iid}` present | imported once, key removed | none |
| Malformed PUT item | `kind: "fib"` without anchors | 422 naming `anchors` | 422 |
| Old env var set | `CHART_INDICATOR_CONFIG_PATH` in compose | startup fails naming `CHART_PREFERENCES_DIR` | loud |
| Timeframe 1m → 1H | Fib anchored at 12:37 | drawn at the 12:00 bar; stored anchor unchanged | none |

</intent-contract>

## Code Map

Filled at plan time from the live code (continuity from the 32.4 spec). Expected anchors: `platform/frontend/src/pages/ChartPage.tsx` (`DRAWING_TOOLS`, `pendingAnchor`, `drawings`, hline `localStorage` helpers, context-menu handlers), `components/chart/LightweightChart.tsx` (`DrawingSpec`, `findClickedDrawingId`, the drawings registry effect, mousedown grab, context menu), `components/chart/rangeDrag.ts`, `primitives/TrendlinePrimitive.ts` (template), `lib/units.ts`, `api/client.ts`/`schema.ts`, `platform/views/catalog_reads.py` (catalog access pattern for the precision reader), `platform/data_api/routes/candles.py` (`CandlesResponse`), `platform/views/preferences.py`, `platform/data_api/settings.py`, `data_api/routes/indicators.py` (route pattern), `platform/docker-compose.yml`, `platform/Makefile` (`VERIFY_KEEP`), `platform/docs/DEPLOY_CHECKLIST.md`, `platform/data/` (tracked TOML files), `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §A8.1, `pages/docs/kbData.ts`, tests: `ChartPage.test.tsx`, `views/tests/test_preferences.py`, `data_api/tests`.

## Tasks & Acceptance

**Execution:**
- [x] Planned at dev time per the Code Map, ordered: instrument precision on the candles response (views reader + route + schema + tests); preferences directory (compose, settings, git mv, Makefile, checklist); drawings resource + route + tests; frontend persistence + hline import; unified handle mechanism (trendline anchors); Fib primitive + tool + modal; Position primitive + tools + modal; docs and spec §A8.1.

**Acceptance Criteria:**
- Given a candles request for an instrument the catalog defines, when it answers, then it carries that definition's `price_precision` and `size_precision`, and an undefined instrument answers 404.
- Given a Fib drawn by drag and a Long placed by click, when the page reloads in another browser, then both are in place with the same levels and labels.
- Given a position's target dragged, when it moves, then the R/R and percent labels update and a target past the entry is refused.
- Given the verification commands, when they run, then all pass with no new warnings.

## Verification

**Commands:**
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest views/tests data_api/tests tests -q` -- expected: pass, except the known pre-existing failures listed in memory `reference_platform_tests_no_rust_build`.


## Spec Change Log

(none)

## Review Triage Log

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 1, medium 7, low 5)
- defer: 4: (high 0, medium 2, low 2)
- reject: 8
- addressed_findings:
  - `[high]` `[patch]` failed save after unmount/pagehide re-flushed immediately (tight PUT loop); 4xx retried forever. Fixed: no re-flush on failure, 4xx stops, visible save error.
  - `[medium]` `[patch]` target/stop clamp unrounded (float noise at precision 6); entry drag could reach 0; formatDecimal could throw inside paint. Fixed with rounded clamps, positive floor, `safeDecimal`.
  - `[medium]` `[patch]` server validator vs client disagreement on Fib optional fields; non-positive prices; duplicate ratios; unbounded width/time. Validator tightened.
  - `[medium]` `[patch]` GET OSError / non-UTF-8 PUT unhandled; temp file not fsynced or cleaned. Fixed.
  - `[low]` `[patch]` legacy import dedupe within imported set; settings dialog dangling id; undocumented limits (last-write-wins, keepalive 64 KiB, per-request catalog read) now `Known limit:` comments.

### 2026-10-01 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 4, medium 7, low 3)
- defer: 0
- reject: 8
- addressed_findings:
  - `[high]` `[patch]` client/server validation mismatches that made one drawing refuse every later save of the coin (422, no retry): a position's right edge dragged or typed past the server's 10 000 bars; a form price rounding to 0 on the grid; a target/stop dragged onto the entry with no precision yet (tick 0); a horizontal line dragged, clicked or imported at or below zero. Fixed: `MAX_WIDTH_BARS` cap in drag and form, a post-rounding one-tick check, an ordering guard on target/stop drags, positive-price guards on hline drag/placement/legacy import; tests for each.
  - `[medium]` `[patch]` `keepalive` was set on every save, so a list past 64 KiB could never save; and a `pagehide` while a save was in flight dropped the last edit. Fixed: keepalive only for an unload flush within `KEEPALIVE_MAX_BYTES`; the unload flush sends the latest list even with a save in flight; tests.
  - `[medium]` `[patch]` a failed drawings GET was never retried (tools off until a reload). Fixed: retried every `SAVE_RETRY_MS` while the chart lives; test.
  - `[medium]` `[patch]` `formatDecimal`/`roundToPrecision` rounded a decimal half-tick down (`1.005 -> 1.00`) and a negative half towards zero, contradicting its own doc. Fixed via a 15-significant-digit cut before rounding, half away from zero; tests.
  - `[medium]` `[patch]` `fibLevelPrices` could throw (RangeError) inside a paint. Fixed: `safeRound` in the renderer path.
  - `[medium]` `[patch]` with the Fibonacci tool armed, a press on a price line both moved the line and started a fib (sibling capture listeners). Fixed: no hline grab while `fibActive`.
  - `[medium]` `[patch]` a stored drawing anchored before the oldest loaded bar is not drawn. Documented as a `Known limit:` on `BarGrid.snap` with the upgrade path (negative logical index).
  - `[low]` `[patch]` Fib preview drawn without extend-right unlike the placed drawing; position settings form showed prices as `String(n)` (`5e-7`); a flat fib (equal rounded prices, different times) was stored. Fixed; tests for the latter two.

### 2026-10-01 — Review pass (follow-up 2)
- intent_gap: 0
- bad_spec: 0
- patch: 11: (high 1, medium 4, low 6)
- defer: 0
- reject: 15
- addressed_findings:
  - `[high]` `[patch]` In the Lines view every bar is a 1-second snapshot stamped at `second + 0.5 s` (`capture/domain/sampler.py`), so a trendline, fib or position placed or dragged there stored a fractional time; the server only accepts whole seconds, so it answered 422 and every later save of that coin was refused. Fixed: `storedTime` (`Math.ceil`, which still snaps to the same bar) at every stored time, in placement and drag; test.
  - `[medium]` `[patch]` An unmount during an in-flight save sent a second PUT at once, so the older one could land last and win. Fixed: an unmount now waits for the in-flight save's `finally`, which sends the newer list in order. Only `pagehide` still sends at once, documented as a `Known limit:` with the versioned-PUT upgrade path; test.
  - `[medium]` `[patch]` A drawings GET the server refused (4xx) was retried every 5 s forever. Fixed: only a network error or a 5xx is retried. The banner no longer says "Reload to try again" while the hook retries on its own; test.
  - `[medium]` `[patch]` `save_chart_indicators` (which truncates before serializing) and `save_screener_columns` still wrote in place. They cited a single-file bind mount that this story removed. Fixed: one `_write_atomic` (temp file, fsync, rename) for all three preference files; tests.
  - `[medium]` `[patch]` A right or middle press on a drawing handle or price line started a drag that moved and saved the drawing. Fixed: only the primary button grabs; test.
  - `[low]` `[patch]` Loader tightened: a drawings table without exactly `v` and `items` is now refused instead of defaulting to empty. A typed position price too large to round no longer throws in Apply; it is refused with "too large". The Fibonacci drag preview now uses the placed drawing's per-ratio colours (`fibLevelColor` in `chartTheme.ts`). Test fixtures now build increments from exact decimal strings instead of floats. Ruff D401/C901 in `views/preferences.py` fixed (`_check_fib_levels` split out). Tests added for the first two.

## Auto Run Result

- Summary: follow-up review pass on Story 32.5 (the precision on the candles response, the preferences directory, the server-side drawings resource, the shared handle grab, and the Fib/Long/Short tools). This pass fixed a Lines-view persistence bug that blocked saving, made unmount saves ordered, stopped retrying refused loads, made every preference write atomic, and closed smaller edge cases.
- Files changed in this pass:
  - `platform/frontend/src/lib/drawings.ts`: `storedTime` used at every stored time (placement and drag); position form refuses an unroundable price.
  - `platform/frontend/src/pages/ChartPage.tsx`: `storedTime` on trendline and fib anchors; fib colours from `fibLevelColor`; banner text.
  - `platform/frontend/src/components/chart/chartTheme.ts`: `FIB_LEVEL_TOKENS` and `fibLevelColor` (moved from ChartPage, now shared with the preview).
  - `platform/frontend/src/components/chart/LightweightChart.tsx`: primary-button-only grab; the fib preview uses the placed colours.
  - `platform/frontend/src/hooks/useChartDrawings.ts`: unmount flush ordered after the in-flight save; no load retry on a 4xx; `Known limit:` for the `pagehide` race.
  - `platform/views/preferences.py`: `_write_atomic` for all three files; strict drawings table; `_check_fib_levels` split out; docstring mood.
  - tests: `lib/drawings.test.ts`, `pages/ChartPage.test.tsx`, `components/chart/LightweightChart.test.tsx`, `views/tests/test_chart_drawings.py`, `views/tests/test_preferences.py`; `data_api/tests/test_candles.py` and `views/tests/test_catalog_reads.py` build fixture increments from exact decimal strings.
- Review: 11 patches applied (1 high), 0 deferred, 15 rejected:
  - spec-mandated, or already settled in earlier passes: the candles 404, `width_bars` in bars, whole-file validation, the blocking async PUT, the unbounded PUT body, the `/app/preferences` default, the per-request catalog read, the unstructured 500;
  - not reachable in practice: a release outside the window (the window `mouseup` already ends the drag), a drag price beyond safe integers, an hline or trendline placed before the precision is known (there are no bars to click before the first candles response);
  - low value: the colour pick recolouring every fib level (it is the menu's one colour), imported legacy lines left unrounded (pre-existing data), the grid set during render, positions with no bars.
- Verification:
  - `npm test`: 631 passed.
  - `npm run lint`: only the 3 pre-existing warnings.
  - `npm run build`: passed.
  - `pytest views/tests data_api/tests tests`: 831 passed, 10 failed. These are the same pre-existing failures as before: 9 need Redis on 6379, plus `test_legacy_names::test_only_published_language_keeps_a_legacy_name`.
  - `ruff check`/`ruff format --check`/`mypy` on the changed Python: clean.
- Residual risks:
  - a `pagehide` PUT can still race one in flight (`Known limit:`);
  - drawings anchored before the loaded history stay hidden (`Known limit:`);
  - prices snap to `10^-price_precision`, not to the tick size (`Known limit:`);
  - VPS migration steps are still owed (DEPLOY_CHECKLIST "Deferred operator actions" 32-5).
