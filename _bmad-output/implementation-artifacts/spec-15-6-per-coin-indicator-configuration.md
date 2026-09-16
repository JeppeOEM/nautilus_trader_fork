---
title: 'Story 15.6: Per-coin indicator configuration'
type: 'feature'
created: '2026-09-16'
status: 'done'
baseline_revision: 'f2a095c2755caee07c0b2478c27bc44393bc6aee'
final_revision: '3c0f8f50bc6e5e4c9f090394a130c20a75ef64d1'
review_loop_iteration: 0
followup_review_recommended: false
context: [
  '{project-root}/troll/CLAUDE.md',
  '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md',
]
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The chart page has no UI to add, remove, or reconfigure indicator panes beyond Story 15.4's fixed five, and no persistence for such a choice -- `dashboard.py`'s existing TOML-backed picker/catalog/config logic has never been relocated into the FastAPI Facade.

**Approach:** Relocate `dashboard.py`'s catalog and config-persistence handlers verbatim into a new `data_api/routes/indicators.py` (`GET /api/indicators/catalog`, `GET`/`PUT /api/coin/{iid}/indicators`), add a new cursor-paginated indicator-values route that reuses `chart_indicators.replay_indicator`/`custom_indicators.replay_indicator` unchanged, and build a frontend `IndicatorPicker` that feeds additional entries into the same declarative `panes` array `ChartPage.tsx` already builds for Story 15.4's fixed five.

## Boundaries & Constraints

**Always:**
- `_merged_indicator_catalog`, `coin_indicator_config_handler`, `save_coin_indicator_config_handler` (`ml_signals/dashboard.py`) move verbatim, not reimplemented (AD-F1/AD-F2) -- config persistence is the Facade's one sanctioned write path beyond `/ws/live` relaying.
- The new indicator-values route dispatches to `chart_indicators.replay_indicator` (native) / `custom_indicators.replay_indicator` (custom, via `ReplayWindow`) unchanged -- no new indicator math (AD-F2/SSOT-01).
- Story 15.4's fixed five panes (`MultiLevelOFI`/`MultiLevelOBI`/`microprice`/`spread`/`volume`) are untouched by this story's config; the picker only adds/removes entries from the general native+custom catalog on top of the same `panes` array `ChartPage.tsx` passes to `LightweightChart`.
- A malformed `PUT` payload or corrupt existing `chart_indicators.toml` fails loud with a real error message (400/500, matching today's handler), never a silent empty state (DATA-02).
- `docker-compose.yml`'s `data_api` service gains the same `CHART_INDICATOR_CONFIG_PATH` env var + `chart_indicators.toml:rw` mount `dashboard`'s service block already has (currently absent -- confirmed by inspection).
- OpenAPI schema + generated TS types (`frontend/openapi.json`, `src/api/schema.ts`) are regenerated for every new endpoint (AD-F5).
- Adding/removing/reconfiguring a picker pane never resets the chart's current zoom/pan (same `LightweightChart.tsx` registry-diff discipline Story 15.4 already established -- this story adds entries to `panes`, it does not touch that component).

**Block If:** none identified -- the indicator-values route's exact request shape (server re-derives its own bounded window vs. accepting client-supplied candles) is an implementation-owned design call, not a decision requiring human input.

**Never:**
- No second, hand-duplicated indicator catalog in the frontend -- the picker's list always comes from `GET /api/indicators/catalog`.
- No renaming `"MultiLevelOFI"`/`"MultiLevelOBI"` or `"OrderFlowImbalance"` to "look consistent" -- deliberately distinct ids in the shared pane registry (Story 15.4 precedent).
- No component outside `LightweightChart.tsx` calls `chart.addPane()`/`removePane()` directly -- the picker only ever changes what `ChartPage.tsx` puts in its `panes` array.
- No auto-save-per-keystroke -- `PUT` fires on an explicit picker change (add/remove/param-apply), matching today's explicit-Save semantics (`save_coin_indicator_config_handler`'s docstring).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Catalog fetch | `GET /api/indicators/catalog` | Merged native+custom catalog, each entry tagged `category` | A name registered in both catalogs raises (pre-existing `_merged_indicator_catalog` guard, kept as-is) |
| Coin never configured | `GET /api/coin/{iid}/indicators`, no TOML entry for `iid` | `[]` | Not an error -- "nothing saved yet" is a legitimate state |
| Persist then reload | `PUT` a list, then `GET` the same `iid` | `GET` reflects exactly what was `PUT` | No error expected |
| Malformed PUT payload | Entry missing `name` or `category` key | `400` with the error message | Never a `500` for a client-input problem |
| Chart page reload | Coin with a previously saved config | Same indicators + params re-render as panes, not just round-trip in storage (FR42) | Values route returns `None` per-point for an uncaptured custom indicator, not an error |

</intent-contract>

## Code Map

- `troll/data_api/routes/indicators.py` -- NEW: relocates `_merged_indicator_catalog`/`coin_indicator_config_handler`/`save_coin_indicator_config_handler` (`ml_signals/dashboard.py` ~1985-2058) as FastAPI routes with Pydantic models; adds the new indicator-values route.
- `troll/data_api/app.py:163-170` -- register the new router above the `/api/{full_path:path}` catch-all, same pattern as `candles_routes`/`indicator_series_routes`.
- `troll/ml_signals/chart_indicator_config.py` -- reused unchanged: `IndicatorEntry`, `load_config`, `save_config` (full-TOML-rewrite, existing tradeoff).
- `troll/ml_signals/chart_indicators.py:295-347` -- reused unchanged: `replay_indicator`, `catalog_json`, `INDICATOR_CATALOG` (native indicators).
- `troll/ml_signals/custom_indicators.py:56-101` -- reused unchanged: `ReplayWindow`, `replay_indicator`, `catalog_json`, `CUSTOM_INDICATOR_CATALOG`.
- `troll/data_api/routes/indicator_series.py` -- reference pattern only (cursor pagination / gap-marker / `has_more` shape); not reused directly -- it replays OFI/OBI/microprice/spread from snapshots, this story's values route replays arbitrary catalog indicators from candles.
- `troll/data_api/routes/candles.py` -- reference pattern for the values route's own `before_ns`/`limit`/`bar_seconds` window construction (`_window_start_ns`).
- `troll/docker-compose.yml:119-137` (`data_api` service) -- add `CHART_INDICATOR_CONFIG_PATH` env var + `chart_indicators.toml:rw` mount, mirroring `dashboard`'s block above it.
- `troll/frontend/src/pages/ChartPage.tsx` -- extend the `panes` `useMemo` to merge Story 15.4's fixed five with picker-configured entries; mount `IndicatorPicker`.
- `troll/frontend/src/components/chart/IndicatorPicker.tsx` -- NEW: fetches catalog + per-coin config (React Query), add/remove/param controls, `PUT`s the full updated list on any change.
- `troll/frontend/src/components/chart/paneColors.ts` -- reused unchanged: `assignPaneColor` for dynamically-added panes, same function Story 15.4's fixed five already use.
- `troll/frontend/src/api/client.ts`, `schema.ts`, `openapi.json` -- regenerated for the 4 new endpoints.
- `troll/data_api/tests/test_indicators_config.py` -- NEW.

## Tasks & Acceptance

**Execution:**
- [x] `data_api/routes/indicators.py` -- `GET /api/indicators/catalog`, `GET`/`PUT /api/coin/{iid}/indicators` -- relocate the three `dashboard.py` handlers verbatim, wrapped in Pydantic request/response models -- AD-F1/AD-F5.
- [x] same file -- `GET /api/coin/{iid}/indicator-values` (exact name is an implementer call) -- bounded candle window (mirrors `candles.py`'s window/limit/bar_seconds contract) then per-requested-name `replay_indicator` dispatch (native catalog first, then custom via `ReplayWindow`), results keyed by `_indicator_id(name, params)` -- AD-F2/AD-F3.
- [x] `data_api/app.py` -- register the new router above the catch-all route.
- [x] `docker-compose.yml` -- add `data_api`'s missing `CHART_INDICATOR_CONFIG_PATH` env var + `:rw` mount.
- [x] `frontend/openapi.json` + `src/api/schema.ts`/`client.ts` -- regenerate (`python3 -m data_api.export_openapi` + `npm run codegen`); add fetch helpers for the 4 new endpoints.
- [x] `frontend/src/components/chart/IndicatorPicker.tsx` -- NEW picker component (add/remove/param controls), `PUT`s the full list on change.
- [x] `frontend/src/pages/ChartPage.tsx` -- mount the picker; on mount, after `GET /api/coin/{iid}/indicators` resolves, fetch each persisted entry's values via the new route and merge into `panes`; reuse `assignPaneColor`.
- [x] `data_api/tests/test_indicators_config.py` -- real `IndicatorEntry`/`save_config`/`load_config` round trip against a temp file: catalog GET non-empty with both categories present, PUT-then-GET reflects the change, malformed PUT returns 400.

**Acceptance Criteria:**
- Given a coin with a previously `PUT` indicator list, when its chart page is reloaded, then the exact same indicators and params re-render as panes (FR42), not merely round-trip in storage.
- Given the picker adds an indicator through the UI, when the change is sent, then it goes through `PUT /api/coin/{iid}/indicators` (never a separate ad hoc endpoint) and the resulting pane appears via the same registry Story 15.4 built, without resetting other panes' colors or the chart's zoom/pan.
- Given `data_api`'s compose service previously had no config mount, when `PUT` is exercised against the running container, then it succeeds rather than failing with a read-only-filesystem error.

## Spec Change Log

## Review Triage Log

### 2026-09-16 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9 (high 0, medium 2, low 7)
- defer: 7 (high 0, medium 2, low 5)
- reject: 4
- addressed_findings:
  - `[medium]` `[patch]` `_values_by_time`'s dispatch call was wrapped only in `except (ValueError, KeyError, TypeError)`, so a bad param (e.g. `period=0`) raising a different exception type (e.g. `ZeroDivisionError`) inside `replay_indicator` surfaced as an unhandled 500 instead of the documented 400. Widened to a broad `except Exception`, matching the system-boundary rationale the retired `dashboard.py._indicators_json` documented for this exact dispatch call (`indicators.py`).
  - `[low]` `[patch]` `_replay_entry` dispatched native-first/custom-second with no check for a name registered in both catalogs, unlike `_merged_indicator_catalog`'s own guard -- a colliding name would silently use the native replay here while the catalog route fails loud for the same name. Added the same collision check before dispatch (`indicators.py`).
  - `[low]` `[patch]` `test_malformed_put_payload_returns_400_not_500`'s assertion (`"error" not in response.json() or response.json()`) was a tautology that passes for any non-empty body regardless of its shape. Replaced with a real check on the actual FastAPI error envelope (`response.json()["detail"]`).
  - `[low]` `[patch]` No test exercised the values route's custom-indicator dispatch branch (`ReplayWindow`) or its `has_more=True` pagination branch -- both real, untested paths per TEST-01. Added `test_indicator_values_dispatches_custom_indicator_via_replay_window` and `test_indicator_values_reports_has_more_when_older_data_exists`.
  - `[medium]` `[patch]` `IndicatorPicker`'s initial `GET /api/coin/{iid}/indicators` and a fast user Add/Remove/Apply race independently -- if the GET resolves after a local change, its `.then` unconditionally reverted `entries`/panes to the stale snapshot, and any further edit would then persist from that stale base, permanently dropping the earlier change on its own next `PUT`. Added a `hasLocalChangeRef` guard so the initial GET's callback no-ops once a local change has landed (`IndicatorPicker.tsx`).
  - `[low]` `[patch]` Picker panes and the five default panes assigned colors from two separate `assignPaneColor` index domains (`pickerSeriesKeys` vs. `DEFAULT_PANE_IDS`), so a picker pane's color could collide with a default pane's. Combined both into one shared domain for the picker's own color assignment (`ChartPage.tsx`).
  - `[low]` `[patch]` `usePickerIndicatorValues`'s scroll-back `loadPage` had no seam-gap check at the page-fetch boundary, unlike the sibling hooks (`useCandles`/`useIndicatorSeries`) it otherwise mirrors -- a real collection gap exactly at a page boundary would render as an unbroken line. Added the same boundary gap-marker insertion (`usePickerIndicatorValues.ts`).
  - `[low]` `[patch]` `usePickerIndicatorValues`'s entries-change reset effect silently dropped its own reset call when a stale (previous-entries) fetch was still in flight, due to `loadPage`'s shared `loadingRef` guard -- panes stayed empty/stale until an unrelated chart-scroll event happened to retrigger a fetch. Added a `pendingResetRef` that retries the reset from the stale fetch's own `finally` once it clears (`usePickerIndicatorValues.ts`).
  - `[low]` `[patch]` `CHART_INDICATOR_CONFIG_PATH`/the `chart_indicators.toml` bind mount were copy-pasted verbatim across the `dashboard` and `data_api` service blocks with no shared anchor, unlike this file's own `*default-logging` pattern -- a future edit to one that isn't manually mirrored to the other silently reintroduces drift. Added `&chart-indicator-config-path`/`&chart-indicator-config-mount` anchors, referenced from both services (`docker-compose.yml`).
  - 7 items deferred to `deferred-work.md`: the new dual-writer race on `chart_indicators.toml` between `dashboard` and `data_api` (transitional, resolves at Story 15.10's cutover); the catalog's `panel` classification not being used for pane placement (overlay indicators render as detached panes, needs a `LightweightChart.tsx` capability extension); two uncaught-exception-to-500 paths inherited verbatim from the retired aiohttp handlers (pre-existing, not a regression); `IndicatorEntryRow`'s param-input coercion edge cases and missing enum dropdown; the values route's dropped `_coerce_indicator_params`-style type coercion (low risk, the picker already coerces client-side); the picker's one-instance-per-name UI limit (backend already supports more).
  - 4 rejected: Blind Hunter's "diff under review is incomplete" (I deliberately excluded the machine-generated `openapi.json` from the reviewed diff; independently re-verified consistent with `schema.ts`); Edge Case Hunter's "`IndicatorEntryRow` leaks draft state across a coin switch" (refuted -- `ChartPage.tsx` remounts `ChartInner`, and everything under it, via `key={iid}` on every instrument change); Edge Case Hunter's "the `chart_indicators.toml` bind mount may target a nonexistent host file" (refuted -- the file is already committed and tracked in git, confirmed via `git ls-files`); Edge Case Hunter's "an empty `entries=[]` values request is indistinguishable from a gap marker" (real API-level quirk but unreachable -- `usePickerIndicatorValues`'s `loadPage` never issues a request when there are no configured entries).

### 2026-09-16 — Review pass (follow-up, `status: done` -> fresh review)
- intent_gap: 0
- bad_spec: 0
- patch: 11 (high 0, medium 5, low 6)
- defer: 6 (high 0, medium 3, low 3)
- reject: 4 (all low)
- addressed_findings:
  - `[medium]` `[patch]` `IndicatorPicker`'s `coerceParamValue`: `Number("")` is `0`, not `NaN`, so clearing a numeric param field silently coerced it to `0` (the exact `period=0` divide-by-zero class the values route's own docstring calls out). Fixed to keep the prior value on a blank field, extracted to `paramCoercion.ts` alongside two related fixes below; added `paramCoercion.test.ts` (`IndicatorPicker.tsx` previously had zero test coverage for this logic).
  - `[low]` `[patch]` Same `coerceParamValue`: boolean params only matched the exact string `"true"`, silently going `false` for any other input including `"True"`/`"TRUE"`. Made case-insensitive and rejects unrecognized text (keeps prior value) instead of defaulting to `false`.
  - `[low]` `[patch]` Same `coerceParamValue`: a structured (object/array/null) default param fell through to `return raw`, silently replacing it with a plain string on any edit. Now rejects the edit (keeps the structured value) since there is no safe string coercion for it.
  - `[medium]` `[patch]` `ChartPage.tsx`'s `pickerSeriesKeys = Object.keys(pickerValues).sort()` was recomputed fresh every render, so adding/removing one picker indicator could shift another, untouched indicator's alphabetical-sort index and therefore its pane color -- violates `paneColors.ts`'s own documented invariant ("an id's color never changes while it stays in the list"). Fixed with a first-seen stable order tracked via React's "adjust state during render" pattern (a ref-mutation version of this fix was tried first but reverted -- it tripped `oxlint`'s `react(refs)` "don't mutate a ref during render" rule; the state-based version passes clean).
  - `[medium]` `[patch]` `usePickerIndicatorValues`'s `resetAndLoad` always anchored its initial fetch to `Date.now()`, contradicting this spec's own Design Notes claim ("The picker's initial fetch anchors to the chart's current `before_ns`"). A user who had already scrolled back before adding an indicator got a pane anchored to "now," entirely outside their visible window, staying blank until an unrelated scroll event happened to trigger a refill. Fixed to anchor to `chart.timeScale().getVisibleRange()`'s right edge when available, falling back to `Date.now()` only when the chart has no visible range yet.
  - `[low]` `[patch]` `GET /api/coin/{iid}/indicator-values`'s `entries` query param had no upper bound, unlike `limit`/`bar_seconds` (both clamped) -- inconsistent with this route's own bounded-read discipline. Added a `_MAX_INDICATOR_VALUES_ENTRIES = 50` cap, `400` on violation (`indicators.py`, `test_indicators_config.py`).
  - `[medium]` `[patch]` `put_coin_indicator_config`'s except clause caught only client-input error types (`JSONDecodeError`/`KeyError`/`TypeError`/`TOMLDecodeError`); an `OSError`/`PermissionError` from the newly-added `chart_indicators.toml:rw` docker mount actually failing to write (e.g. a host/container UID mismatch) fell through as an unhandled, undetailed `500`. Split into a distinct `except OSError` branch with its own clear `500` detail message, since this is a server-side condition, not a malformed-payload one.
  - `[medium]` `[patch]` `get_indicator_values`'s catalog-read calls (the main window query and the `_has_more` probe) sat outside the route's own `try/except`, which only wrapped `_values_by_time` -- a catalog I/O error (missing/corrupt catalog dir) surfaced as an unhandled `500` with no detail. Wrapped both in their own `try/except Exception -> HTTPException(500, ...)`, distinct from the existing `400` branch for a bad indicator/param (client input vs. server-side failure are now different status codes with real messages, DATA-02).
  - `[medium]` `[patch]` `IndicatorPicker.persist()` computed `next` from the closured `entries` state, which only updated inside a successful PUT's `.then` -- two rapid actions (e.g. Add then Remove before the first PUT resolved) each built `next` from the same stale pre-request list, and whichever PUT response landed last silently discarded the other's change (same data-loss shape as the already-fixed initial-GET-vs-local-edit race from the prior pass, but between two local edits instead). Fixed with an optimistic `setEntries(next)` before firing the PUT, rolled back on failure.
  - `[low]` `[patch]` `test_indicator_values_reload_reproduces_same_series` only asserted key-presence and first==second reproducibility, which would pass identically even if every point were `None` (i.e. dispatch silently broken). Added an assertion that at least one real (non-`None`) value was produced.
  - `[low]` `[patch]` `troll/CLAUDE.md`'s "Desktop <-> VPS Connection" section still described `data_api` as "the read-only FastAPI wrapper," now stale since this story added a write endpoint (`PUT`) and an `rw` bind mount to that same service. Updated the one line to reflect the picker's config-write exception.
  - 6 deferred to `deferred-work.md`: `usePickerIndicatorValues.resetAndLoad` wiping ALL configured indicators' scroll-back history on any single entries change (deliberate per its own docstring, real architecture change to fix properly); one bad persisted indicator entry failing the whole values request with only a `console.error` on the frontend (consistent with every other chart-history hook's identical failure handling in this app, not a one-off fix); two independent `CATALOG_PATH` globals (`indicators.py`/`custom_indicators.py`) needing to agree with no startup assertion (matches the established, deliberate per-route-module-constant pattern); slow-warm-up native indicators showing a long `None`-prefix on a freshly-added picker pane (pre-existing `replay_indicator` behavior, now more visible); `PUT`'s read-modify-write race on `chart_indicators.toml` having no locking, even within `data_api` alone (broader than the already-deferred cross-process dashboard/data_api race, same root cause); `has_more` prematurely reporting `False` for a sparse/gapped instrument when the immediate bounded window is empty (identical pre-existing shape in `candles.py`, this route's own cited reference pattern).
  - 4 rejected: Edge Case Hunter's "`get_indicators_catalog` doesn't wrap `_merged_indicator_catalog`'s `ValueError`" (this spec's own I/O matrix explicitly says a catalog-name collision "raises... kept as-is" -- the 500 is intentional, not a gap); Edge Case Hunter's "`IndicatorEntryRow` keyed by `entry.name` leaks stale draft state across a coin switch" and "picker `error` state isn't reset on `instrumentId` change" (both refuted, same as the prior pass's identical refutation -- `ChartPage.tsx` remounts `ChartInner`, and everything under it including `IndicatorPicker`, via `key={iid}` on every instrument change, so no cross-coin state survives); Edge Case Hunter's "entries-change race briefly renders stale-entries data before the reset overwrites it" (real but minor -- self-corrects within one fetch cycle via the already-existing `pendingResetRef` mechanism, no lasting incorrect state).

### 2026-09-16 — Review pass (third, `status: done` -> fresh review)
- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 8 (high 0, medium 1, low 7)
- reject: 6 (high 0, medium 0, low 6)
- addressed_findings:
  - none
  - 8 deferred to `deferred-work.md`: PUT never validates a saved entry's `name`/`category` against the merged catalog (new finding, confirmed pre-existing/verbatim-inherited from the retired `dashboard.py` handler); `GET .../indicators`'s corrupt-TOML except tuple doesn't cover `UnicodeDecodeError` (new finding, confirmed pre-existing/inherited); `PUT .../indicators`'s payload-parsing except tuple doesn't cover `AttributeError` from a non-dict entry (new finding, confirmed pre-existing/inherited); `IndicatorEntryRow`'s Apply gives no visual feedback when a keystroke is silently coerced back to its prior value (new finding, low severity); plus four re-confirmations of already-logged, still-unaddressed items (dual-writer `chart_indicators.toml` race, unused catalog `panel` field, one-instance-per-name picker limit, single-bad-entry failing a whole batched values request).
  - 6 rejected: Blind Hunter's and Edge Case Hunter's "a fast coin switch races `IndicatorPicker`'s stale `entries` into a PUT for the wrong instrument" (refuted -- `ChartPage.tsx`'s `ChartInner` fully remounts via `key={iid}` on every instrument change, so `IndicatorPicker`'s `instrumentId` prop never actually changes within a mounted instance; the effect's `[instrumentId]` dependency is unreachable in practice); Blind Hunter's "`IndicatorEntryRow` keyed only by `entry.name` leaks state across coins" (same remount refutation, restates the already-twice-rejected finding from prior passes); Blind Hunter's "`get_indicators_catalog`'s catalog-collision `ValueError` is unhandled" (this spec's own I/O matrix explicitly authorizes this exact behavior as intentional -- already explicitly rejected in the second pass, still stands); Blind Hunter's "the cross-catalog collision check is redundantly recomputed every request" (negligible at this single-operator tool's real request volume, not a correctness issue); Edge Case Hunter's "an empty `entries=[]` JSON array wastes a full catalog query" (harmless, returns immediately with no real consequence); Edge Case Hunter's "no test exercises the concurrent-writer scenario" (not a standalone finding -- redundant restatement of the already-tracked dual-writer race, not itself a new defect).

## Design Notes

- **Values route re-derives its own bounded window server-side** from `before_ns`/`limit`/`bar_seconds` rather than accepting client-supplied candles in the request body -- one redundant bounded catalog read per newly-added picker indicator is an acceptable cost against introducing a second, differently-shaped request contract nowhere else in this API uses (DESIGN-01). The picker's initial fetch anchors to the chart's current `before_ns`, so a newly-added indicator still backfills its visible history without a full page reload.
- **Custom indicators that need raw-delta capture** (`CumulativeVolumeDelta`/`CancelPressure`/`OrderFlowImbalance`) are surfaced exactly as today: available to pick, `None`-valued when the underlying instrument hasn't opted into delta capture (pre-existing, Story 10.2-10.4 behavior) -- not hidden or special-cased by this story.

## Verification

**Commands:**
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` -- expected: all pass except any pre-existing unrelated failure already tracked from a prior story
- `cd troll/frontend && npm run build && npm run test && npm run lint` -- expected: clean build, Vitest + codegen tests pass, lint clean

**Manual checks (if no CLI):**
- Real browser: add an indicator via the picker, reload the chart page, confirm it re-renders identically; remove it, confirm the pane disappears and the persisted config reflects the removal.

## Auto Run Result

Status: `done`

**Summary:** Third review pass on an already-`done` story (both the original implementation and two prior follow-up review passes were already committed). Re-ran Blind Hunter + Edge Case Hunter adversarial review against the full story diff since baseline (`f2a095c275`). No new code-level bug was found that warranted a patch: every finding either (a) was refuted by direct code inspection (the "coin-switch race" claims don't hold because `ChartPage.tsx`'s `key={iid}` fully remounts `ChartInner`/`IndicatorPicker` on every instrument change), (b) restates behavior the spec's own intent-contract explicitly authorizes (`_merged_indicator_catalog`'s collision `ValueError`), or (c) is a real, pre-existing/architecturally-scoped issue already appropriate for `deferred-work.md` rather than a same-pass patch. No intent_gap, no bad_spec, no patch -- no code was modified this pass, no loopback to step-03 was needed.

**Files changed this pass:**
- `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` -- this Review Triage Log entry + this Auto Run Result.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- appended a new dated section: 4 new findings (PUT's missing catalog-name/category validation; two narrow inherited uncaught-exception gaps in the corrupt-TOML/malformed-payload paths; the picker's missing coercion-rejection feedback) plus 4 re-confirmations that already-logged items (dual-writer TOML race, unused `panel` field, one-instance-per-name limit, single-bad-entry-fails-whole-batch) remain unaddressed.

**Review findings breakdown (2026-09-16 third pass, see Review Triage Log above for full detail):**
- 0 patches applied -- no correctness bug survived verification against the actual code.
- 8 deferred: 4 new (all low severity except the re-confirmed dual-writer race, which stays medium), 4 re-confirmations of already-tracked items.
- 6 rejected: two refuted coin-switch-race claims, one already-authorized-by-spec "gap", one cosmetic perf nitpick, one no-consequence edge case, one redundant restatement.
- No intent_gap, no bad_spec.

**Verification performed:** No code changed this pass, so the full test/build/lint suite was not re-run -- the second pass's verification (69/283 pytest passed with the same 2 pre-existing unrelated failures; clean `npm run build`/`test`/`lint`) still describes the current code exactly as it was left.

**Residual risks:** Unchanged from the second pass's residual-risk assessment -- the newly-deferred items in this pass are all low-severity/likelihood for a single-operator internal tool (the catalog-validation gap and the two exception-type gaps are pre-existing, verbatim-inherited behavior, not new exposure). `followup_review_recommended: false` -- this pass made no code changes, so there is nothing new for an independent follow-up review to check.

