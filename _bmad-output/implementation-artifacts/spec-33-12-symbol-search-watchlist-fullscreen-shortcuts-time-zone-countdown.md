---
title: 'Story 33.12: symbol search, watchlist, fullscreen chart with its panes, shortcuts, time zone, session breaks, countdown, last-price label'
type: 'feature'
created: '2026-10-07'
status: 'done'
baseline_revision: 'ebe28d85260583000fe73d04b289596c0315bd9e'
final_revision: '5f697e54099ccb333608cd51303987d3548d0de3'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: [multiple-goals, oversized]
---

<intent-contract>

## Intent

**Problem:** The chart page can only switch instrument by going back to Rankings, has no fullscreen, no keyboard shortcuts beyond Esc/undo, prints every time in UTC only, lacks session breaks, a bar countdown and a last-price toggle. Compare (33.9) is a text field whose own `Known limit:` says 33.12 brings the search.

**Approach:** Add a `SymbolSearch` dialog over an enriched `GET /api/markets` (navigate mode, and compare mode replacing `CompareControl`'s text field), a server-persisted watchlist rail (`GET/PUT /api/watchlist`, TOML in the preferences dir) priced from `rankings:live`, a Fullscreen-API hook on one wrapper element, one shortcut module, one `lib/time.ts` time formatter, and four new layout keys (time zone, session breaks, countdown, last price) validated on both sides. Bar Replay stays candles-mode only (operator, 2026-10-07): Lines mode keeps Replay disabled, now with the visible reason "Replay is available on candle charts".

## Boundaries & Constraints

**Always:**
- No new dependency (NFR12): dialog, shortcut sheet and primitives hand-rolled; dialogs use `SettingsDialogShell`.
- Layout additions are added keys only (AD-D12): backend `views/preferences.py` `_OPTIONAL_LAYOUT_KEYS` + `BUILTIN_DEFAULT_LAYOUT` + a `_validate_*` each, frontend `ChartLayout` + `BUILT_IN_LAYOUT` + an `xxxOf` tolerant parser each; an old stored layout without them loads unchanged; the mirror test pins both sides equal.
- Bar/point `time` is never changed by the time zone: zone affects formatting only (`localization.timeFormatter`, `timeScale.tickMarkFormatter`, and every chart-page printed time) through `lib/time.ts`.
- Shortcuts do nothing while focus is in `input, select, textarea, [contenteditable='true']` or a `dialog[open]` exists — one shared guard in `lib/shortcuts.ts`, which the existing undo/redo handler also switches to. Esc (disarm), Ctrl/Cmd+Z/Shift+Z/Y and Shift angle-snap keep working unchanged.
- Fullscreen is view state only: never written to the layout, never in `localStorage`. A rejected `requestFullscreen` (or an absent API) shows an inline message in the top bar.
- Every new export (hooks, `lib/time.ts`, `lib/shortcuts.ts`, preferences functions) has a real non-test caller (test_boundaries dead-module check). No `chart-*` localStorage key (chartBrowserState guard).
- `api/schema.ts` and `frontend/openapi.json` regenerated in the same change as the route changes.
- Session breaks are at UTC day boundaries regardless of the display zone; spot vs perp and missing ranking data show `—`, never 0.

**Block If:**
- Implementing any AC requires modifying `nautilus_trader/` or `crates/`, or adding an npm/PyPI dependency.

**Never:**
- Multi-chart layouts, synced crosshair, CSV/data export (dropped by the operator 2026-10-05).
- Candle colour customisation, Volume candles, Renko/Kagi/P&F/Range bars, order entry, pitchforks/Gann/Elliott, object tree.
- Persisting the watchlist or any chart setting in `localStorage`; persisting fullscreen.
- Interpolating or fabricating values (replay cut, compare, countdown) — cut by time only.
- Bar Replay in Lines mode (the snapshot-seconds view): retracted by the operator 2026-10-07, never built.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Search filter | type `eth by` | rows whose symbol+venue+id contain every whitespace token (case-insensitive) ordered as the server sends; Enter navigates to the highlighted row (`/chart/{iid}`); ↑/↓ move highlight | no match: "No market matches", Enter does nothing |
| Search, markets down | `GET /api/markets` 503/other | inline "No venue's market list is live" / "could not be loaded"; compare mode still accepts a typed id through `validateCompareInput` | no crash |
| Search compare mode | Compare button or `Alt+C` in candles mode | dialog lists markets minus already-compared, Enter calls `addCompare`; refusal reason inline | `Alt+C` in Lines mode: no-op |
| Watchlist PUT | `{instruments:[ids]}` | 200 `{instruments}` written atomically to `chart_watchlist.toml` (`v = 1`) | non-list, unknown key, duplicate, malformed id (no `.VENUE`), > 200 entries, id > 512 chars → 422 naming the path; invalid JSON 400; corrupt file → GET/PUT 500 "is corrupt" |
| Watchlist rail | pinned ids + `rankings:live` | each row: symbol, venue, price, `pct_24h` (sign-coloured); click navigates | id absent from ranks or null price → `—` |
| Fullscreen enter/exit | button or `Shift+F` | `.chart-stage` (top bar + replay controls + tool rail + chart + tape) is `document.fullscreenElement`; `fullscreen` prop to `LightweightChart` flips, chart re-applies container width/height on both transitions; Esc/button/`Shift+F` exits | rejection or missing API → inline "Fullscreen was refused by the browser"; layout save never includes it |
| Timeframe keys | `1`,`5`,`15`,`1h`,`4h`,`d`/`1d`,`w`/`1w` then Enter | timeframe changes (`changeTimeframe`); typed buffer shown in a small chip | unknown buffer + Enter: buffer cleared, nothing changes; buffer clears after 3 s idle or Esc; ignored in Lines mode |
| Countdown | last bar `t`, `bar_seconds`, now | label `mm:ss` (`h:mm:ss` ≥ 1 h, `Nd hh:mm` ≥ 1 d) under the last-price label, ticking 1 s; when now ≥ close it counts to the next whole-bar close after `t` | hidden in Lines mode, during replay, with no bars, or toggled off |
| Replay in Lines mode | Lines mode | Replay button disabled with `title` "Replay is available on candle charts"; `Alt+R` is a no-op; switching to Lines exits a running replay (baseline) | — |

</intent-contract>

## Code Map

- `platform/data_api/routes/markets.py` -- `MarketItem` gains `market` (`kernel.venues.market_kind`) and `volume24h: float | None` (joined from `buses.bus.latest` ranks by id)
- `platform/views/preferences.py` -- layout keys/validators (~832/845/1082); filter presets (1275-1444) = pattern for the watchlist store; `_write_atomic`; `_check_compare_symbol` (1172) id check
- `platform/data_api/routes/rankings.py:321-425` -- filter-preset routes = pattern for `routes/watchlist.py` (raw-body PUT + `openapi_extra`, `PREFERENCES_LOCK`, 500 on corrupt)
- `platform/data_api/settings.py:67-73`, `platform/data_api/app.py:62-73,276-288` -- path constant, router registration
- `platform/frontend/src/pages/ChartPage.tsx` -- top bar 1769-1952, `.chart-workspace` 1954, Esc 1117, undo 1146, `selectTool` 1631, `startReplayPick` 1641, `useReplay(candles)` 622, Lines toggle 1811, replay button 1939, compare 988-1035
- `platform/frontend/src/components/chart/LightweightChart.tsx` -- `createChart` 1256, ResizeObserver 1324, `addMainSeries` ~721, lines series ~1522, marker primitive 2062
- `platform/frontend/src/components/chart/primitives/VerticalMarkerPrimitive.ts` -- dashed vertical line template
- `platform/frontend/src/lib/chartLayout.ts` -- `ChartLayout` 141, `BUILT_IN_LAYOUT` 167, `normalizeLayout` 501
- `platform/frontend/src/components/chart/CompareControl.tsx` -- text field to replace by the search in compare mode
- `platform/frontend/src/components/chart/LiquidationTape.tsx:17`, `VolumeOverlaysDialog.tsx:98` -- UTC time printers to route through `lib/time.ts`
- `platform/frontend/src/pages/RankingsPage.tsx:236-247,467`, `hooks/useLiveChannel.ts` -- `rankings:live` shape (`price`, `pct_24h`)
- `platform/frontend/src/pages/filterPresets.ts`, `api/client.ts:338-355,435` -- client pattern, `fetchMarkets`
- `platform/frontend/src/pages/docs/kbData.ts:188-198` -- Shortcuts section

## Tasks & Acceptance

**Execution:**
- [x] `platform/views/preferences.py` -- add `CHART_WATCHLIST` store: `WatchlistError(ValueError)` with `.field`, `MAX_WATCHLIST = 200`, `validate_watchlist(body)`, `load_watchlist(path)`, `save_watchlist(ids, path)`; add layout keys `time_zone` (`"utc"|"local"|"exchange"`, default `"utc"`), `session_breaks` (bool, default false), `bar_countdown` (bool, default true), `last_price` (`{line: bool, label: bool}`, default both true) with strict validators; docstring lists the new file -- persistence
- [x] `platform/data_api/settings.py`, `platform/data_api/routes/watchlist.py` (new), `platform/data_api/app.py` -- `CHART_WATCHLIST_PATH`; `GET/PUT /api/watchlist` mirroring filter presets -- server-side watchlist
- [x] `platform/data_api/routes/markets.py` -- add `market` and `volume24h` fields (added fields only) -- search shows market and 24h volume
- [x] `platform/views/tests/test_watchlist.py`, `platform/data_api/tests/test_watchlist.py`, `platform/data_api/tests/test_markets.py` (extend), layout tests (`views/tests/test_chart_layouts.py`, `data_api/tests/test_layout.py`, mirror test) -- every Matrix row for the store, route errors, new layout keys accepted/rejected, old layout still loads
- [x] `platform/frontend/openapi.json`, `platform/frontend/src/api/schema.ts`, `platform/frontend/src/api/client.ts` -- regenerate; `fetchWatchlist`/`saveWatchlist` -- typed client
- [x] `platform/frontend/src/lib/time.ts` (+test) -- `TimeZoneSetting`, `formatChartTime(sec, zone)`, `tickMarkFormatter(zone)`, `formatClock(tsNs, zone)`, `formatDateTime(sec, zone)`, `sessionBreakTimes(times)` (first bar of each UTC day, empty when bar ≥ 1 day), `barCountdown(lastT, barSeconds, nowSec)`, `formatCountdown(s)` -- one time module
- [x] `platform/frontend/src/lib/shortcuts.ts` (+test) -- `isTypingContext(event)`, `shortcutFor(event)` → action union (Alt via `event.code`), `timeframeFromBuffer(buf)` -- one shortcut map
- [x] `platform/frontend/src/lib/chartLayout.ts` (+test) -- four fields, defaults, `xxxOf` parsers -- layout mirror
- [x] `platform/frontend/src/hooks/useFullscreen.ts`, `platform/frontend/src/hooks/useWatchlist.ts` -- fullscreen state over `fullscreenchange` with `enter/exit/toggle/error`; watchlist load/pin/unpin/save with inline error -- view hooks
- [x] `platform/frontend/src/components/chart/SymbolSearch.tsx`, `WatchlistRail.tsx`, `ShortcutSheet.tsx` -- search dialog (navigate|compare), rail with pin toggle for the current coin, `?` sheet generated from the same shortcut table -- UI
- [x] `platform/frontend/src/components/chart/CompareControl.tsx` -- Compare button opens `SymbolSearch` in compare mode; remove the datalist field and its `Known limit:` -- one picker
- [x] `platform/frontend/src/components/chart/primitives/SessionBreaksPrimitive.ts`, `CountdownPrimitive.ts` -- dashed day lines at bottom z-order; price-axis view under the last price label -- chart decorations
- [x] `platform/frontend/src/components/chart/LightweightChart.tsx` (+test) -- props `timeZone`, `sessionBreaks`, `countdown` (`{barSeconds, enabled}`), `lastPrice`, `fullscreen`; formatters from `lib/time.ts`; `priceLineVisible`/`lastValueVisible`/`priceLineColor` (up/down by last bar close vs open) on the main series (Lines: on the price line); re-apply size on `fullscreen` change -- rendering
- [x] `platform/frontend/src/pages/ChartPage.tsx` -- wrap top bar + replay controls + workspace in `.chart-stage`; symbol button opens search; Fullscreen button + inline error; watchlist toggle + rail; settings controls (time zone select, session breaks, countdown, last line, last label) persisted via `patchLayout`; one keydown handler using `lib/shortcuts.ts`; Replay button disabled in Lines mode with the visible reason (baseline replay code otherwise unchanged); `LiquidationTape`/`VolumeOverlaysDialog` get the zone -- page wiring
- [x] `platform/frontend/src/pages/ChartPage.test.tsx`, `platform/frontend/src/hooks/chartBrowserState.test.ts`, `platform/frontend/src/components/chart/fullscreen.test.tsx` (new) -- search filter/navigate/compare; fullscreen enter/exit with every connected pane inside the fullscreen element, resize prop on both transitions, refused message, never in the saved layout; shortcut map incl. typing guard; time-zone invariance (chart `data` identical across zones); new keys not in localStorage -- AC tests
- [x] `platform/frontend/src/index.css`/`theme.css` -- `.chart-stage:fullscreen` fills the viewport with `overflow:auto`, rail/search/sheet styles in the existing retro theme -- styling
- [x] `platform/frontend/src/pages/docs/kbData.ts` -- every shortcut, search, watchlist, fullscreen, time zone, session breaks, countdown, last price, and the rule that Replay is on candle charts only -- Docs truthful
- [x] `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` -- amend §A1, §A9, "Right sidebar" and "Watchlist and Screener" exclusions with dated `[amended 2026-10-07: Story 33.12]` notes citing the operator's 2026-10-05 decisions (multi-chart, synced crosshair, export dropped; fullscreen instead) -- spec truth
- [x] `_bmad-output/planning-artifacts/epics.md` (Story 33.12 AC) and `{implementation_artifacts}/deferred-work.md` -- dated `[amended 2026-10-07: operator]` note retracting "Bar Replay works in Lines mode too"; one DW entry recording the retraction as decided (not planned) -- operator decision
- [x] `platform/docs/DATA_DICTIONARY.md` (§2.17 new layout keys, §2.20 chart watchlist store + `/api/markets` fields), `platform/docs/DATA_INTEGRITY_AUDIT.md` (D-218 display zone never shifts `t`; D-219 countdown uses the viewer's clock), `platform/docs/DEPLOY_CHECKLIST.md` (deferred: rebuild `data_api`, `chart_watchlist.toml` appears on first PUT) -- MR4

**Acceptance Criteria:**
- Given the chart page, when Ctrl+K, `/` or the symbol is used, then the search lists every market with venue, market and 24 h volume, and Enter navigates.
- Given a watchlist saved in one browser, when the page is opened elsewhere, then the same pinned list shows (server TOML), with live price and 24 h %.
- Given any key in the shortcut table, when pressed outside a field or dialog, then its action runs, and `?` shows the sheet listing exactly that table.
- Given time zone `local`, when the chart renders, then bar `time` values passed to the series equal those under `utc`.
- Given candles mode with session breaks on at an intraday timeframe, when the visible range spans midnight UTC, then a dashed vertical line sits at the first bar of each UTC day.
- Given every new setting changed, when the page reloads, then the layout restores them; fullscreen is not restored.

## Spec Change Log

### 2026-10-07 — operator decision: Lines-mode replay dropped (product rule)
- **Trigger:** operator decision 17:20 UTC plus clarification 17:25 UTC, during the resumed run. Bar Replay is supported only in candles mode, with any `CHART_TYPES` entry, Line/Area included. Lines mode (the snapshot-seconds view) never gets Replay.
- **Amended:**
  - Removed: the title suffix, the Lines-mode replay Approach clause, the Matrix row, the `useReplay.ts` task and Code Map line, the LightweightChart Lines-replay marker host and the ChartPage Lines-replay wiring.
  - Added: a Matrix row and a Never entry for the rule, the visible disabled reason on the Replay button, and an epics.md/deferred-work task.
  - Intent-contract edits are by the operator's explicit order.
- **Known-bad state avoided:** shipping the replay over the snapshot timeline that the operator retracted.
- **KEEP:** everything else in the prior attempt: search, watchlist, fullscreen, shortcuts, time zone, breaks, countdown, last price.

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 0, medium 7, low 7)
- defer: 0
- reject: 9: (high 0, medium 2, low 7)
- addressed_findings:
  - `[medium]` `[patch]` In `local`, the axis labelled UTC-boundary Day/Month/Year ticks with the local calendar value (UTC−5: "6" for 7 Oct). A tick that is not at local midnight now prints the local date and time.
  - `[medium]` `[patch]` 1D/1W bars showed the previous date in a negative-offset zone. They now print their UTC date (the bucket) in every zone.
  - `[medium]` `[patch]` Under Heikin Ashi, the countdown and the last-price colour followed the raw bar. Both now follow the drawn main-series row.
  - `[medium]` `[patch]` Fullscreen re-applied only the width, against the Matrix. Panes now scale proportionally to fit the stage, refit on viewport resize, and restore on exit; scaled heights are never saved. The Design Note and docs are corrected.
  - `[medium]` `[patch]` Watchlist: PUTs could land out of order, a failed PUT reloaded over a later one, and a failed first GET disabled pinning for good. Saves are now serialized, and the load retries with backoff and an honest title.
  - `[medium]` `[patch]` The rail showed frozen prices when the rankings feed was disconnected or the coin was stale. It now shows `—` with a reason.
  - `[medium]` `[patch]` `/api/markets` ledgered a bad `volume24h` on every GET and served volumes from a stale rankings message. It now ledgers once per message, and a message older than 180 s gives `null`. `with_market_details` moved onto `MarketsBus`.
  - `[low]` `[patch]` Session breaks ignored the live bar's new day.
  - `[low]` `[patch]` Session breaks took their bar size from the countdown prop. They now have their own `barSeconds` prop.
  - `[low]` `[patch]` `/` and timeframe keys failed on layouts that need Shift (German, AZERTY). They are now matched by `event.key`.
  - `[low]` `[patch]` A typed timeframe buffer survived a switch to Lines mode. It is now cleared, and Enter outside candles mode is a no-op.
  - `[low]` `[patch]` With the viewer's clock behind the bar open, the countdown exceeded one bar. It is now clamped.
  - `[low]` `[patch]` The search highlight scrolled off-screen. It is now scrolled into view, with `aria-activedescendant`.
  - `[low]` `[patch]` The `local` time tests could not fail under TZ=UTC. They now pin America/New_York and assert literal strings.

### 2026-10-07 — Review pass (follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 3, low 6)
- defer: 0
- reject: 11: (high 0, medium 1, low 10)
- addressed_findings:
  - `[medium]` `[patch]` Watchlist: a GET retry still pending after a failed save took a newer request number than a later pin, painted the server's older list over it, and the next pin then stored a list without it. A failed save also left the failed pin in the list the next edit built on. An edit now cancels a pending retry (its PUT's answer is the server's list), and a failed newest PUT shows the last server-confirmed list at once.
  - `[medium]` `[patch]` The rail showed frozen prices as live while `/ws/live` stayed connected but the ranking engine had stopped publishing. It now applies the Rankings page's 15 s heartbeat threshold (`RANKING_STALE_MS`, now exported) on a 1 s tick, showing `—` with a reason and a "rankings stale" note.
  - `[medium]` `[patch]` `/api/markets` served `volume24h` for ids in the message's `stale_instrument_ids`, which the rail shows as `—`. Such an id is now `null`; a bad value on it is still ledgered, and a missing or malformed stale list marks nothing stale.
  - `[low]` `[patch]` A divider drag in fullscreen re-rounded every pane (floor, then round of the unscaled height), so untouched panes drifted by about 1 px per drag and the drift was saved. Only a pane the drag resized is unscaled now; the others keep their stored height.
  - `[low]` `[patch]` On a tiny fullscreen budget, the 1 px floor could lift the panes' sum over it. The excess is now taken back off the tallest panes.
  - `[low]` `[patch]` A held key's auto-repeat flipped fullscreen, log scale or replay back and forth. Repeats are now ignored.
  - `[low]` `[patch]` SymbolSearch kept an earlier fetch's load error after a later fetch succeeded, which also hid the stale-venue note. It now clears.
  - `[low]` `[patch]` SymbolSearch's `compared = []` default was a fresh array on every render, which re-ran the row filter and the scroll effect. It is now one module constant.
  - `[low]` `[patch]` `lastPriceOf`'s unknown-key check used `in`, so it missed inherited names such as `constructor`. It now uses `Object.hasOwn`.

## Design Notes

- **Watchlist naming:** the DDD glossary forbids "watchlist" for Collection Plan / Coin Ranking. This is a third, UI-only concept — the operator's pinned chart instruments — so files/types say `chart watchlist` (`chart_watchlist.toml`, `WatchlistError`) and §2.20 states the distinction from `research/watchlist.py`; the route stays `/api/watchlist` as the epic names it.
- **Fullscreen element:** `.chart-stage` includes the top bar so the button that leaves fullscreen stays visible; the watchlist rail stays outside (a click navigates, remounting the chart, which exits fullscreen anyway). In fullscreen `LightweightChart` scales every pane by one factor so the whole chart (panes, separators, time axis) fits the stage below the top bar, refits on a viewport resize (its ResizeObserver watches the fullscreen element then), and restores the stored heights on exit; view state only, never reported through `onPaneHeights` (a divider dragged there is saved unscaled). `Known limit:` content under the chart (load errors, overlay notices) is outside the budget, so the stage scrolls to it. (Corrected 2026-10-07 in review: the earlier "pane heights kept, stage scrolls" limit contradicted the Matrix's height re-apply.)
- **Countdown close:** `close = lastT + barSeconds × (floor((now − lastT) / barSeconds) + 1)`, the first whole-bar boundary strictly after both `lastT` and `now` (so `now` exactly on a boundary already counts the next bar) — works for any bucket alignment, incl. weekly. (Corrected 2026-10-07 to the implemented formula; the earlier `max(1, ceil(...))` wording contradicted its own boundary note.)
- **Timeframe buffer:** `[0-9hdw]` (and `/`, `?`) match by `event.key`, Shift or not, so layouts that need Shift for them work (German `/` = Shift+7, AZERTY digits); with Shift a letter is taken as typed, so `Shift+H` stays out. `Alt+*` and Ctrl/Cmd are checked first, and `Shift+F`/`Shift+L` match by `code` only when the key typed none of those characters. Switching to Lines clears the buffer and Enter there is a no-op. (Corrected 2026-10-07 in review.)
- **Replay is a candle-chart feature (operator, 2026-10-07):** Replay steps whole bars of the candle array, so it runs in candles mode with every chart type, including Line/Area, which are bar-derived. Lines mode shows raw snapshot seconds and keeps Replay disabled, with the visible reason. The epic's "Bar Replay works in Lines mode too" is retracted, not deferred.
- **Prior attempt (2026-10-07):** run c4e7's dev-1 was stopped mid-story; its full WIP (tracked + untracked, this spec included, incl. a stray `zz_debug.test.tsx` to fold or delete) is pinned on branch `33-12-prior-attempt` (parent = baseline `ebe28d8526`). Reuse it: `git read-tree -m -u HEAD 33-12-prior-attempt && git reset -q`, then re-verify every task and AC rather than trusting checkboxes.

## Verification

**Commands:**
- `cd platform && python3 -m pytest views/tests data_api/tests tests/test_boundaries.py -q` -- expected: all pass, no warnings
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && git diff --exit-code src/api/schema.ts` after the commit -- expected: no diff

## Auto Run Result

**Summary:** This was a follow-up review of Story 33.12 (symbol search, server-side chart watchlist, fullscreen, shortcuts, time zone, session breaks, countdown and last-price toggle), already implemented at `0a335376fa`. Blind Hunter and Edge Case Hunter reviewed the whole story diff since `ebe28d8526`. Their 23 raw findings deduplicated to 20: 9 patched, 0 deferred, 11 rejected, with no intent gap and no spec defect.

**Files changed in this pass** (under `platform/`):
- `frontend/src/hooks/useWatchlist.ts` (+test): an edit cancels a pending GET retry, and a failed newest PUT reverts to the last server-confirmed list at once.
- `frontend/src/components/chart/WatchlistRail.tsx` (+test): applies the whole-message heartbeat staleness check (15 s, 1 s tick), with a "rankings stale" note.
- `frontend/src/pages/RankingsPage.tsx`: `RANKING_STALE_MS` exported, so the rail shares the one threshold.
- `views/markets_bus.py` (+test), `data_api/routes/markets.py`: `volume24h` is null for an id in `stale_instrument_ids`.
- `frontend/src/components/chart/LightweightChart.tsx` (+test): a fullscreen drag unscales only the resized panes, and the fit never exceeds its budget.
- `frontend/src/pages/ChartPage.tsx` (+`fullscreen.test.tsx`, `ChartPage.test.tsx` fixture): key auto-repeat is ignored by the shortcut handler.
- `frontend/src/components/chart/SymbolSearch.tsx` (+test): a successful fetch clears an earlier load error, and the `compared` default is stable.
- `frontend/src/lib/chartLayout.ts` (+test): `Object.hasOwn` for the unknown last-price key check.
- `docs/DATA_DICTIONARY.md` §2.20, `frontend/src/pages/docs/kbData.ts`: the rail's staleness rule, the failed-save behaviour and the new null case for `volume24h`.

**Rejected (11):**
- Countdown continuing on a dead feed: the Matrix and the Design Note define the formula.
- Countdown drawn when the last-price label is off: the two settings are independent, so this is cosmetic.
- Alt+C opening the picker at the compare maximum: the button behaves the same way, and the refusal is shown inline.
- An unknown typed timeframe silently cleared: the Matrix specifies it.
- The corrupt-watchlist 500: the Matrix specifies it.
- Fullscreen exiting when another coin is opened: covered by a Design Note.
- Shift+L returning to Normal instead of the previous percent/indexed mode: low impact, and it mirrors the log toggle.
- The select-focus typing guard: the spec lists `select`.
- No typed-id navigation while the market list is down: the Matrix limits that to compare mode.
- Local-zone daily tick labels: by design and documented.
- Collapsed panes scaled in fullscreen: not real, because a collapsed pane is removed from the chart.

**Verification:**
- `python3 -m pytest views/tests data_api/tests tests/test_boundaries.py -q`: 1447 passed, warnings summary clean. Run against the Redis already on 6379.
- The same suite with `-W error` does not get past start-up: pytest stops on a `PytestConfigWarning` (unknown config option `asyncio_default_fixture_loop_scope`) from the environment's pytest config, not from this change.
- vitest: 79 files, 1618 tests. One run failed on a `ChartPage.test.tsx` fixture with `updated_at: 0`, which the new staleness check reads as stale. The fixture now uses a current time, and that test file's watchlist tests then pass (19 passed). The full vitest suite was not re-run after that one-line fixture fix.
- `npm run lint`: only the 3 pre-existing warnings.
- `npm run build` and `tsc -b`: OK.
- ruff 0.15.16 (the pinned pre-commit rev): format and check clean on the touched Python files.
- The regenerated OpenAPI is identical to the committed one.

**Follow-up review recommended:** false. The 9 fixes are localized (3 medium, 6 low), each with a regression test, and none changes an API shape.

**Residual risks:**
- Still no live-browser walkthrough of the Fullscreen API or keyboard layouts; the DEPLOY_CHECKLIST 33-12 smoke checks cover them.
- The rail's 15 s staleness check compares the ranking engine's `updated_at` with the browser's clock, the same assumption the Rankings page makes.
- The time zone is still saved per coin (previous pass).
