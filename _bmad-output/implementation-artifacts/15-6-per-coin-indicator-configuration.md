# Story 15.6: Per-coin indicator configuration

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want to add, remove, and reconfigure a coin's chart indicators and have that choice persist,
so that I don't have to rebuild my preferred view of a coin every time I revisit it.

## Acceptance Criteria

1. **`PUT /api/coin/{iid}/indicators` persists the config using `dashboard.py`'s existing `save_coin_indicator_config_handler` logic (relocated, not reimplemented)** in `data_api/routes/indicators.py` — the Facade's one sanctioned write path beyond `/ws/live` relaying (AD-F1 verbatim relocation, AD-F2 exception).
2. **`GET /api/indicators/catalog` returns the available indicator catalog (names/params/overlay-vs-oscillator/histogram classification) sourced from `ml_signals`' existing registry — never a second, hand-duplicated list in the frontend.**
3. **Adding, removing, or reconfiguring an indicator via the UI sends the change through `PUT /api/coin/{iid}/indicators`, and the resulting pane set updates per Story 15.4's keyed registry.**
4. **Reloading a coin's chart page restores exactly the same indicators and parameters last configured for that coin** (FR42).
5. **Test:** an integration test covers the GET (catalog) + PUT (persist) + reload-restores-config round trip, using real config objects (TEST-01: integration path touching a persisted resource).

## Tasks / Subtasks

- [ ] Task 1 — `data_api/routes/indicators.py`: catalog + config persistence (AC: #1, #2)
  - [ ] `GET /api/indicators/catalog`: relocate `dashboard.py`'s `_merged_indicator_catalog()` (`ml_signals/dashboard.py:1987-2008`) — merges `chart_indicators.catalog_json()` (native `nautilus_trader.indicators`, e.g. SMA/RSI/MACD/Bollinger) and `custom_indicators.catalog_json()` (`CumulativeVolumeDelta`/`CancelPressure`/`OrderFlowImbalance`), tagging each with `category: "native"|"custom"`, raising on a name collision between the two catalogs (existing behavior, keep it — it is a real DATA-02 "no mysteries" guard, not incidental). Wrap in a Pydantic response model (AD-F5).
  - [ ] `GET /api/coin/{iid}/indicators`: relocate `coin_indicator_config_handler` (`ml_signals/dashboard.py:2017-2032`) — reads `chart_indicator_config.load_config(Path(CHART_INDICATOR_CONFIG_PATH))` (`ml_signals/chart_indicator_config.py`), returns that instrument's persisted `[{name, params, category}]` list (empty list if never saved — a missing file/entry is a legitimate "nothing configured yet" state, not an error).
  - [ ] `PUT /api/coin/{iid}/indicators`: relocate `save_coin_indicator_config_handler` (`ml_signals/dashboard.py:2035-2058`) — load, update this instrument's entry, `save_config()` (full-TOML-rewrite, existing documented tradeoff, keep it). A malformed payload (missing `name`/`category` key, corrupt existing TOML) returns 400 with the error message, mirroring today's handler — never a 500 for a client-input problem.
  - [ ] `CHART_INDICATOR_CONFIG_PATH` needs its own module-level env-var constant in this new route file (same non-circular-import pattern every prior route file established) — default matches `dashboard.py`'s own default and the compose file's mount path (`troll/docker-compose.yml:66`: `/app/ml_signals/chart_indicators.toml`).
  - [ ] Register the router in `app.py` above the `/api/{full_path:path}` catch-all.

- [ ] Task 2 — **A values-fetching route for the picker's indicators is required for this story to actually work end-to-end, even though epics.md's own ACs only name the catalog/config routes** (AC #3's "pane set updates" implies the pane must have data to draw, not just exist).
  - [ ] **Scope clarification, read first:** Story 15.4's five panes (`MultiLevelOFI`/`MultiLevelOBI`/`microprice`/`spread`/`volume`) are a fixed, always-on set — this story's config does **not** touch them. This story's picker adds *additional* panes from the general Nautilus/custom indicator catalog (SMA, RSI, MACD, `CumulativeVolumeDelta`, `CancelPressure`, `OrderFlowImbalance`, etc. — today's `chart_indicators.toml`-backed picker, `ml_signals/dashboard.py`'s `_toggleIndicatorPicker`/indicator-add UI at lines 486-960) on top of the same `Map<indicatorId, IPaneApi>` registry Story 15.4 built. Do not conflate the two indicator sets or assume 15.6's config can remove/reconfigure the fixed five.
  - [ ] Today's equivalent (`coin_indicators_handler` + `_indicators_json`, `ml_signals/dashboard.py:1962-1935`) computes indicator values **from an already-fetched candle list** (`chart_indicators.replay_indicator(candles, name, params)` for native indicators — pure function of OHLCV) or from a `ReplayWindow` (raw catalog access, for the three delta-replay custom indicators). Add a new cursor-paginated route (e.g. `GET /api/coin/{iid}/indicator-values?names=<comma-separated>&before_ns=&limit=&bar_seconds=`, naming is an implementation detail) that: (a) fetches the same bounded candle window `/api/candles` would for the given `before_ns`/`limit`/`bar_seconds` (or accepts the client's already-fetched candles directly — a design call for the implementer, see below), (b) calls `_indicator_id`-keyed `chart_indicators.replay_indicator`/`custom_indicators.replay_indicator` unchanged for each requested name (AD-F2 — reuse, not reimplementation), (c) returns per-indicator point series keyed by `_indicator_id(name, params)`, matching the existing naming scheme (`ml_signals/dashboard.py:1889-1892,1928`).
  - [ ] **Design call flagged for the implementer, not resolved here:** whether the frontend passes its already-loaded `useCandles` candle array in the request body (avoiding a redundant catalog re-read, since the candles are already in the browser) or the new route re-derives the same window server-side from `before_ns`/`limit` (simpler contract, matches every other route's shape, costs one redundant bounded catalog read). Either is compatible with AD-F2/AD-F3; pick based on what keeps the picker's co-paging behavior (mirroring Story 15.4's AC #5 co-paging requirement — a newly-added picker indicator must not require a full page reload to backfill its history) simplest to implement correctly.

- [ ] Task 3 — Frontend: indicator picker UI (AC: #2, #3, #4)
  - [ ] A picker component (new, e.g. `frontend/src/components/chart/IndicatorPicker.tsx`) fetching `GET /api/indicators/catalog` and `GET /api/coin/{iid}/indicators` (React Query), rendering add/remove/param controls. On any change, `PUT /api/coin/{iid}/indicators` with the full updated list (matching today's "explicit Save" semantics, `ml_signals/dashboard.py:2035`'s docstring — not an auto-save-per-keystroke).
  - [ ] On chart-page mount, after `GET /api/coin/{iid}/indicators` resolves, add a pane (via Story 15.4's registry, Task 2) for each persisted entry, fetching its values via Task 2's new route — this is what makes AC #4 ("reload restores exactly") real rather than just "the config round-trips but nothing visually happens."
  - [ ] Reuse Story 15.4's `assignPaneColor` for these dynamically-added panes too (same registry, same function) — do not build a second color-assignment path.

- [ ] Task 4 — Codegen + Tests (AC: #1, #2, #5)
  - [ ] Regenerate OpenAPI schema/TS types for the new Pydantic models (catalog, config GET/PUT, Task 2's values route).
  - [ ] `data_api/tests/test_indicators_config.py` (new): real `chart_indicator_config.IndicatorEntry`/`save_config`/`load_config` round-trip against a temp file (TEST-01/03 — real config objects, no mocking) covering GET catalog (non-empty, both categories present), PUT persist + GET reflects it, and a malformed PUT payload returning 400 not 500.
  - [ ] Run the full backend + frontend test/build/lint commands per prior stories' convention.

## Dev Notes

- **Sequencing:** depends on Story 15.4's keyed pane registry (`Map<indicatorId, IPaneApi>`) existing first — this story's picker is a second producer of pane-add/remove calls into that same registry, per AD-F4's "no child component creates/destroys a pane directly" rule (the picker calls through the registry's owning component, same as Story 15.4's own mounting code did for the fixed five).
- **The collision-avoidance rule from Story 15.4 still applies here in reverse:** Story 15.4 chose `"MultiLevelOFI"`/`"MultiLevelOBI"` specifically so this story's `"OrderFlowImbalance"` (a *different*, existing custom-catalog id) never collides with it in the shared registry. Do not "helpfully" rename either side to make them look consistent — they are deliberately different indicators with deliberately different ids.
- **`custom_indicators.py`'s three delta-replay indicators (`CumulativeVolumeDelta`/`CancelPressure`/`OrderFlowImbalance`) only produce real data for an instrument with raw-delta capture opted in** (default off, per `ml_signals/chart_data.py`'s docstring) — this is pre-existing, documented behavior (Story 10.2-10.4), not a defect this story introduces or must fix. The picker/catalog should surface these exactly as today (available to pick, honestly `None`-valued/empty when the underlying data isn't captured) — do not silently hide them or fabricate a workaround.
- **`troll/CLAUDE.md` constraints that apply:** AD-F1/AD-F2 (verbatim relocation of `save_coin_indicator_config_handler`/`_merged_indicator_catalog`, the values route reuses `replay_indicator` unchanged), DATA-02 (malformed config payload fails loud with a real error, not a silent empty state), DESIGN-01 (no new abstraction beyond what's needed — the values route's exact shape is left as an implementer design call, not over-specified here).

### Project Structure Notes

- New: `troll/data_api/routes/indicators.py`, `troll/data_api/tests/test_indicators_config.py`, `troll/frontend/src/components/chart/IndicatorPicker.tsx`.
- Modified: `troll/data_api/app.py` (wire the new router), `troll/frontend/src/pages/ChartPage.tsx` (mount the picker + restore persisted panes on load), `troll/frontend/src/api/client.ts`/`schema.ts`/`openapi.json` (new endpoints).
- Not modified: `troll/ml_signals/chart_indicator_config.py`, `troll/ml_signals/chart_indicators.py`, `troll/ml_signals/custom_indicators.py` (all reused unchanged), `troll/ml_signals/dashboard.py` (untouched until Story 15.10), `troll/docker-compose.yml`'s `chart_indicators.toml` mount (`data_api` needs the same `:rw` mount `dashboard` already has — confirm this is present or add it; do not forget it, or `PUT` will fail with a read-only-filesystem error only visible at runtime, not at review time).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 15.6, lines 1415-1441] — this story's origin.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md] — AD-F1 (verbatim relocation), AD-F2 ("the one exception is config persistence... explicitly allowed"), Structural Seed's `routes/indicators.py` (`/api/indicators/catalog`, `/api/coin/{iid}/indicators` GET/PUT — this story's actual home, unlike Story 15.4's deliberately-separate `indicator_series.py`).
- [Source: troll/ml_signals/dashboard.py:1889-2058] — `_indicator_id`, `_merged_indicator_catalog`, `coin_indicators_handler`/`_indicators_json`, `coin_indicator_config_handler`, `save_coin_indicator_config_handler` — full block read this session, all relocated logic sources.
- [Source: troll/ml_signals/chart_indicator_config.py] — full file read this session; `IndicatorEntry`/`load_config`/`save_config`, the exact persistence layer this story's routes call unchanged.
- [Source: troll/ml_signals/chart_indicators.py:1-40,340-360] — `catalog_json()`/`replay_indicator` for native indicators.
- [Source: troll/ml_signals/custom_indicators.py] — full file read in Story 15.4's research; `catalog_json()`/`CUSTOM_INDICATOR_CATALOG` ids this story's catalog route also serves.
- [Source: troll/docker-compose.yml:60-72] — `dashboard`'s `CHART_INDICATOR_CONFIG_PATH` env var and `:rw` bind mount for `chart_indicators.toml` — `data_api`'s own service block (lines 119-137) does not currently mount this file at all, a gap this story must close.
- [Source: _bmad-output/implementation-artifacts/15-4-synced-indicator-panes.md] — the keyed pane registry (`Map<indicatorId, IPaneApi>`) and `assignPaneColor` this story's picker extends, plus the naming-collision precedent (`"MultiLevelOFI"`/`"MultiLevelOBI"` vs. `"OrderFlowImbalance"`) this story must respect, not undo.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
