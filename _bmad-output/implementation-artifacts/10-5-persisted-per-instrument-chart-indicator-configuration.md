---
baseline_commit: 5c133d22ac
---

# Story 10.5: Persisted per-instrument chart indicator configuration

Status: done

## Story

As a user of the chart page,
I want the indicators I've added to a coin's chart to still be there next time I open it — and to be able to check that configuration into git,
so that a chart's setup isn't lost on every page reload or container redeploy, and a useful indicator combination can be shared/reviewed like any other config change.

Depends on Stories 10.1-10.4 (needs the final `_activeIndicators` shape, spanning both catalogs, to persist) — all four are done as of this session.

## Acceptance Criteria

1. A new `ml_signals/chart_indicator_config.py` module mirrors `dydx_collector/config.py`'s `load_config()`/`save_config()` shape (`tomllib.load()` to read, `tomli_w.dump()` to write, full-rewrite-not-patch — same accepted comment-loss tradeoff `config.py:111-118` already documents). It defines a schema keyed by `instrument_id`, each entry a list of `{name, params, category}` objects (one per active indicator instance — `id` is not persisted, since it is only ever a client-side sequence counter for the multi-instance UI, regenerated fresh on load), and reads/writes a new file, `troll/ml_signals/chart_indicators.toml`, confirmed not excluded by any `.gitignore` (root `.gitignore`'s `troll/` entries only exclude generated data directories — `catalog/`, `metrics/`, `bot_tui_logs/`, `live_paper/data/` — never config files, matching `dydx_collector/config.toml`'s own already-committed precedent).
2. `_render_chart_page`'s `init_script` (currently declares `_activeIndicators=[]` as an empty array on every page load) is changed so that a new `GET /data/coin/{id}/indicator-config` endpoint returns that instrument's persisted entries (or an empty list if none saved yet), and the page's init script fetches it and populates `_activeIndicators` from the response before the first render — a coin with no saved config still opens exactly as it does today (empty picker, no regression).
3. The indicator picker's toolbar gains a "Save" control that POSTs the current `_activeIndicators` array (both native and custom entries) to a new `POST /data/coin/{id}/indicator-config` endpoint, which calls `save_config()` for that instrument — saving is explicit/user-triggered, not automatic on every add/remove, so an in-progress exploratory selection is never persisted by accident.
4. `docker-compose.yml`'s `dashboard` service (currently mounts `./dydx_collector/catalog:/app/catalog:ro` and `./dydx_collector/metrics:/app/metrics_dir:ro`, both read-only) gains a new bind mount for `ml_signals/chart_indicators.toml` (or its containing directory), read-write — mirroring the collector service's own single `rw` config mount (`./config.toml:/app/dydx_collector/config.toml:rw`, the only other `rw` config mount in the file) — without this, "Save" would succeed inside the container's writable layer and vanish on the next `make redeploy`.
5. `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q` plus a new `ml_signals/tests/test_chart_indicator_config.py` pass, asserting `load_config()`/`save_config()` round-trip a multi-instrument, mixed-category selection correctly (TEST-01: integration path touching a persisted config file) and that `GET`/`POST /data/coin/{id}/indicator-config` behave correctly against a temp config file (same temp-file test pattern `dydx_collector/tests` already uses for `config.py`).
6. `cd troll && python -m pytest ml_signals/tests -q` passes (same pre-existing unrelated `test_ofi_strategy.py` flake aside).

## Tasks / Subtasks

- [x] Task 1 — `chart_indicator_config.py` module (AC: #1)
  - [x] `IndicatorEntry` frozen dataclass: `name: str`, `params: dict[str, Any]`, `category: str` (matches `{name, params, category}` schema — no `id` field, per AC #1's reasoning).
  - [x] `load_config(path: Path) -> dict[str, list[IndicatorEntry]]`: `tomllib.load()`, keyed by `instrument_id`; missing file → empty dict (verified via `test_load_config_missing_file_returns_empty_dict`).
  - [x] `save_config(config: dict[str, list[IndicatorEntry]], path: Path) -> None`: `tomli_w.dump()`, full rewrite (same documented tradeoff as `dydx_collector/config.py:save_config`'s docstring, copied). Confirmed `tomli_w` correctly quotes dotted instrument-id keys (e.g. `"BTC-USD-PERP.DYDX"`) rather than parsing the dot as TOML nested-table syntax — verified via a scratch round-trip before writing tests.
  - [ ] Module-level `CHART_INDICATOR_CONFIG_PATH` constant in `dashboard.py` (deferred to Task 2, where it's first used).
- [x] Task 2 — `GET /data/coin/{id}/indicator-config` (AC: #2)
  - [x] `coin_indicator_config_handler` reads `CHART_INDICATOR_CONFIG_PATH` via `load_config()`, returns that instrument's entries as JSON (`[]` if the instrument or the file itself doesn't exist yet, since `load_config` already returns `{}` for a missing file and `dict.get(symbol, [])` covers a missing instrument key) — shape matches what the JS expects (`[{name, params, category}]`).
  - [x] `_render_chart_page`'s init_script: `_fetchIndicatorCatalog()`'s success callback now chains a new `_loadSavedIndicatorConfig()` (not fired in parallel) — sequencing matters because `_renderIndicatorTable` reads `_indicatorCatalog[a.name]`, which must already be populated. `_loadSavedIndicatorConfig` populates `_activeIndicators` with fresh `_indSeq`-based `id`s, then calls `_renderIndicatorTable()`/`_refreshActiveIndicators()` only if the saved list is non-empty.
  - [x] A coin with no saved config (`GET` returns `[]`) triggers `_loadSavedIndicatorConfig`'s early return (`if(!saved||!saved.length)return;`) — no render call at all, so the page opens exactly as it does today (empty picker, no regression, verified by inspection since no browser is available in this environment — same established caveat as every prior chart-page story).
- [x] Task 3 — `POST /data/coin/{id}/indicator-config` + Save button (AC: #3)
  - [x] `save_coin_indicator_config_handler`: `await request.json()`, converts each entry to `IndicatorEntry` (client-side `id` simply not read — the dict comprehension only pulls `name`/`params`/`category`), loads the existing full config, replaces this instrument's key, calls `save_config()`. Malformed payload (`JSONDecodeError`/`KeyError`/`TypeError`) → 400 with an error message.
  - [x] JS: `#btn-save-indicators` ("Save") added next to `#btn-indicators` in the toolbar, `onclick="_saveIndicatorConfig()"`. New `_saveIndicatorConfig()` maps `_activeIndicators` to `{name, params, category}` (category looked up from `_indicatorCatalog[a.name].category` at send time, per Dev Notes) and POSTs it. Explicit click only — not called from `_addIndicator`/`_removeIndicator`/`_updateIndicatorParam`.
  - [x] Save success/failure feedback via the existing `setStatus(...)` pattern.
- [x] Task 4 — Docker read-write mount (AC: #4)
  - [x] `dashboard` service gains `- ./ml_signals/chart_indicators.toml:/app/ml_signals/chart_indicators.toml:rw`, with a one-line comment stating why (mirrors the collector service's `config.toml` mount comment style).
  - [x] `CHART_INDICATOR_CONFIG_PATH: "/app/ml_signals/chart_indicators.toml"` added to the `dashboard` service's `environment:` block.
  - [x] Confirmed via `git check-ignore -v` that `troll/ml_signals/chart_indicators.toml` is not matched by any `.gitignore` pattern (exit code 1, no match). Created an empty file (valid TOML — `tomllib.loads('')` returns `{}`, verified) at that path rather than leaving it absent: a single-file Docker bind mount of a path that doesn't exist on the host creates a *directory* there instead, silently breaking the mount — committing an empty file up front avoids that footgun.
- [x] Task 5 — Tests (AC: #5, #6)
  - [x] New `ml_signals/tests/test_chart_indicator_config.py` (3 tests): `load_config` on a missing file returns `{}`; `save_config`/`load_config` round-trip a multi-instrument, mixed-category (native + custom) selection; `save_config` is confirmed a full rewrite (a second save with a different instrument drops the first). Mirrored `dydx_collector/tests/test_config.py`'s `tmp_path`/plain-function fixture pattern exactly.
  - [x] `test_dashboard_chart.py` (4 new tests, using `aiohttp.test_utils.TestClient`/`TestServer` against a real `make_app(...)` instance, mirroring `test_rank_history.py`'s established real-HTTP-round-trip pattern rather than inventing a new one): GET on a fresh/missing file returns `[]` (and per-instrument: a different instrument's config is untouched); POST then GET round-trips a two-entry, mixed-category payload exactly; POST tolerates and never persists a client-sent `id` field; POST with a missing required field (`category`) returns 400 with an `error` key.
  - [x] `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q` — 54 passed. `cd troll && python -m pytest ml_signals/tests -q` — 193 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py`, same documented flake as Stories 9.1/10.2/10.3/10.4).

### Review Findings

- [x] [Review][Patch] `_saveIndicatorConfig`/`_loadSavedIndicatorConfig` don't guard against an active or saved indicator `name` no longer present in `_indicatorCatalog` (e.g. an indicator renamed/removed after a coin's config was saved — a real possibility given how often Epic 10 itself has changed which indicators exist) [troll/ml_signals/dashboard.py:_saveIndicatorConfig,_loadSavedIndicatorConfig] — fixed: both functions now skip/filter out any entry whose name isn't in `_indicatorCatalog` instead of throwing.
- [x] [Review][Patch] `save_coin_indicator_config_handler`'s `try/except` wraps only request-JSON parsing, not the subsequent `load_config()`/`save_config()` calls [troll/ml_signals/dashboard.py:save_coin_indicator_config_handler] — fixed: widened the try block to cover `load_config`/`save_config` too, added `tomllib.TOMLDecodeError` to the caught exceptions. New test: `test_indicator_config_post_toml_unrepresentable_param_returns_400_not_500`.
- [x] [Review][Patch] `coin_indicator_config_handler` has no error handling around `load_config()` at all [troll/ml_signals/dashboard.py:coin_indicator_config_handler] — fixed: wrapped in try/except, returns a 500 with a diagnosable error message (DATA-02: fail loud, don't silently swallow into an empty list). New test: `test_indicator_config_get_on_corrupt_file_returns_500_not_crash`.
- [x] [Review][Patch] Completion Notes overclaimed "Committed an empty `troll/ml_signals/chart_indicators.toml`" while the file (and `test_chart_indicator_config.py`) were still untracked (`git status` showed `??`) — the single-file-bind-mount-creates-a-directory footgun the note claims is "avoided" is only actually avoided once the file is git-tracked. Fixed: wording corrected to "created," and both files are included in this story's commit (verified via `git status` before committing).
- [x] [Review][Defer] `chart_indicator_config.save_config` writes directly (`path.open("wb")` + `tomli_w.dump`, no temp-file+rename, no file lock) — a crash mid-write or two near-simultaneous saves could corrupt/lose an update. Confirmed identical, pre-existing pattern in `dydx_collector/config.py`'s own `save_config` (this story's AC #1 explicitly directs mirroring that shape), not a regression this story introduces; acceptable risk profile for a personal single-user localhost tool with no comparable hardening anywhere else in this codebase's config-writing code. `troll/ml_signals/chart_indicator_config.py`.
- [x] [Review][Dismiss] "No server-side validation of `name`/`category`/`symbol` against the real indicator/instrument catalogs" — consistent with the dashboard's established trust model: every other endpoint in this file (`coin_indicators_handler`'s `symbol`, `_parse_indicator_spec`'s indicator names, etc.) accepts arbitrary strings unchecked too. Not a new gap this story introduces.
- [x] [Review][Dismiss] "No auth/CSRF on the POST endpoint" — matches SEC-01's established localhost-only trust boundary (troll/CLAUDE.md); no endpoint on this dashboard has auth, by design (personal single-user tool, no exposed port).
- [x] [Review][Dismiss] "No confirmation dialog/undo on Save" — unrequested UX scope, not specified by any AC; AC #3 only requires explicit/click-triggered, which is satisfied.
- [x] [Review][Dismiss] "No payload size cap on the POST body" — consistent with every other JSON-accepting code path in this codebase; none cap size either. Not a regression.
- [x] [Review][Dismiss] "Concurrent-write lost-update race between two saves" — same risk profile as the identical pre-existing `dydx_collector/config.py` pattern (see the deferred atomic-write finding above); "last write wins" on a personal preferences file is acceptable behavior, not corruption.
- [x] [Review][Dismiss] "Task 1's deferred `CHART_INDICATOR_CONFIG_PATH` subtask is unchecked under a checked `[x]` Task 1 header" — cosmetic story-doc bookkeeping only; the constant is correctly present (added in Task 2, as the subtask itself states it would be), no code defect.

## Dev Notes

- **Read before touching anything:** `dydx_collector/config.py` in full (confirmed this session: `load_config`/`save_config` at lines 64-99/111-134 — full-rewrite tradeoff documented at 111-118) and whichever `dydx_collector/tests/test_*config*.py` file covers it, for the exact temp-file test fixture pattern to mirror. `dashboard.py`'s indicator-picker JS in full (confirmed this session: `_activeIndicators`/`_indicatorCatalog`/`_indSeq` declared at 376-378; `_addIndicator`/`_removeIndicator`/`_indicatorLabel`/`_renderIndicatorTable`/`_updateIndicatorParam` at 429-466; `_buildIndicatorSpecString` at 481-486; the toolbar `widget` HTML string and `init_script` at 1148-1181). `dashboard.py`'s `coin_indicators_handler`/`_merged_indicator_catalog`/`indicators_catalog_handler` (1699-1745) and the route-registration block near the bottom of the file (`app.router.add_get(...)`, confirmed this session at 1916-1917) for where to wire the two new routes. `docker-compose.yml`'s `collector`/`dashboard` service blocks in full (confirmed this session: collector's `rw` config mount at 35-39, dashboard's current `ro`-only mounts at 64-77).
- **`category` is not currently stored on `_activeIndicators` entries.** Client-side, an entry is `{id, name, params}` (`dashboard.py:434`); `category` is looked up on demand via `_indicatorCatalog[a.name].category` wherever needed (e.g. `_renderIndicatorPicker`'s grouping, `dashboard.py:426`). The persisted schema requires `category` per AC #1's literal `{name, params, category}` shape — the Save handler's JS must look it up from `_indicatorCatalog` at send time rather than assuming `_activeIndicators` entries already carry it; do not add a `category` field to `_activeIndicators` itself unless a genuine second use case needs it (DESIGN-01 — the lookup-at-send-time is simpler and sufficient).
- **`id` is deliberately not persisted** (AC #1) — it is purely a client-side `_indSeq`-based counter for the multi-instance add/remove/edit UI (`dashboard.py:429,434,438-439,460-461`). On load, each persisted entry gets a fresh `id` assigned the same way `_addIndicator` does (`++_indSeq`), so loaded and freshly-added instances are indistinguishable to the rest of the UI.
- **Full-rewrite-not-patch is a deliberate, already-precedented tradeoff, not new scope to design here.** `dydx_collector/config.py:save_config`'s docstring already states and accepts this for the collector's own config; `chart_indicator_config.py`'s `save_config` should state the same tradeoff for the same reason (`tomli_w` has no comment-preservation), not attempt to solve comment preservation as part of this story.
- **No `id` collision risk across instruments:** the schema is keyed by `instrument_id`, so each instrument's indicator list is independent — loading one coin's chart never touches another's persisted entries.
- **This is the last story in Epic 10.** After this story, Epic 10 (FR31-33: the custom-indicator category + CVD/Cancel Pressure/OFI as picker indicators + persisted configuration) is complete — no further stories are planned against it at this time.
- **`troll/CLAUDE.md` constraints:** DESIGN-01 (YAGNI — don't add a `category` field to `_activeIndicators` itself, don't build comment-preserving TOML round-tripping, don't add config validation beyond what `dydx_collector/config.py` itself does for its own schema), DESIGN-02 (`chart_indicator_config.py` stays a standalone module — it does not import from or get imported by `chart_indicators.py`/`custom_indicators.py`; `dashboard.py` is the only call site that touches both the indicator catalogs and this new config module, same pattern as `_merged_indicator_catalog`), READ-01 (new functions under ~30 lines), TEST-01/03, NAUT-02 (n/a — this story touches no `ParquetDataCatalog`/Nautilus data types at all, pure TOML + JSON), SEC-01 (n/a — no new port, this reuses the existing dashboard aiohttp app on its existing `127.0.0.1`-bound port).

### Project Structure Notes

- New: `troll/ml_signals/chart_indicator_config.py`, `troll/ml_signals/tests/test_chart_indicator_config.py`.
- Modified: `troll/ml_signals/dashboard.py` (new module-level `CHART_INDICATOR_CONFIG_PATH` constant, two new route handlers + registrations, toolbar/init_script JS changes), `troll/docker-compose.yml` (new `rw` mount + env var on the `dashboard` service), `troll/ml_signals/tests/test_dashboard_chart.py` (new endpoint tests).
- No changes to `troll/ml_signals/chart_indicators.py`, `troll/ml_signals/custom_indicators.py`, `troll/dydx_collector/` (its own `config.py`/`config.toml` are a separate, unrelated config — do not conflate the two), `troll/ranking_engine/`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 10, Story 10.5] — authoritative AC source (Given/When/Then format transcribed to numbered ACs above).
- [Source: _bmad-output/implementation-artifacts/10-1-...md through 10-4-...md] — the framework and precedent (historical-only scope decisions don't apply here; DESIGN-01/02 discipline and READ-01 line-count target do) these stories established.
- [Source: troll/dydx_collector/config.py, troll/ml_signals/dashboard.py, troll/docker-compose.yml] — read in full this session.
- [Source: troll/CLAUDE.md] — DESIGN-01/02, READ-01, TEST-01/03, SEC-01.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- Confirmed `tomli_w` correctly quotes dotted instrument-id keys (e.g. `"BTC-USD-PERP.DYDX"`) as TOML string keys rather than parsing the dot as nested-table syntax — verified via a scratch round-trip (`tomli_w.dumps`/`tomllib.loads`) before writing any code.
- Confirmed no async handler test harness existed yet in `test_dashboard_chart.py` — found and reused `test_rank_history.py`'s established `aiohttp.test_utils.TestClient`/`TestServer` + `make_app(...)` pattern instead of inventing a new one.
- Confirmed via `git check-ignore -v troll/ml_signals/chart_indicators.toml` (exit code 1) that the new config file is not excluded by any `.gitignore` pattern.
- Full suite: 193 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure`), same documented flake as every prior Epic 10 story.

### Completion Notes List

- `chart_indicator_config.py` mirrors `dydx_collector/config.py`'s `load_config`/`save_config` shape exactly, including the same documented full-rewrite-not-patch tradeoff.
- `category` is not stored on client-side `_activeIndicators` entries (only `id`/`name`/`params` are) — `_saveIndicatorConfig()`'s JS looks it up from `_indicatorCatalog[a.name].category` at send time, per this story's own Dev Notes.
- `_loadSavedIndicatorConfig()` is deliberately chained *after* `_fetchIndicatorCatalog()`'s promise resolves, not fired in parallel — `_renderIndicatorTable` reads `_indicatorCatalog[a.name]`, so sequencing (not just "eventually both load") is required for correctness.
- Created an empty `troll/ml_signals/chart_indicators.toml` (valid TOML, parses to `{}`) rather than leaving the path absent — a single-file Docker bind mount of a host path that doesn't exist yet creates a *directory* there instead, which would have silently broken the `rw` mount on first `make up`. This only actually prevents the footgun once the file is git-tracked (code review caught the file was still untracked at time of writing) — confirmed staged and committed as part of this story's commit.
- Save is explicit/click-only (AC #3) — verified by inspection that `_saveIndicatorConfig()` is only wired to `#btn-save-indicators`'s `onclick`, never called from `_addIndicator`/`_removeIndicator`/`_updateIndicatorParam`.
- **Not verified in a real browser** — no display available in this environment, same established caveat as every prior chart-page story (7.1/8.1/8.2/8.4/9.1/10.1-10.4).
- This is the last story in Epic 10 — no further stories are currently planned against it.
- Tests: `test_chart_indicator_config.py` (new, 3 tests). `test_dashboard_chart.py` +6 tests (GET fresh-file, POST/GET round-trip, POST ignores client `id`, POST 400 on malformed payload, POST 400 on a TOML-unrepresentable value, GET 500 on a corrupt file). Full suite (post-review): 195 passed, 1 pre-existing unrelated failure.
- **Code review (2026-09-09)** fixed three gaps: JS crash on a stale/renamed indicator name in `_saveIndicatorConfig`/`_loadSavedIndicatorConfig`, an unhandled 500 on `load_config`/`save_config` failures in the POST handler, and no error handling at all in the GET handler. 3 patches applied (2 with new regression tests), 1 deferred (pre-existing non-atomic-write pattern, mirrors `dydx_collector/config.py` deliberately), 6 dismissed (consistent with this dashboard's established no-auth/no-validation trust model, or unrequested scope).

### File List

- New: `troll/ml_signals/chart_indicator_config.py`
- New: `troll/ml_signals/chart_indicators.toml` (empty, valid TOML — avoids the Docker missing-file-bind-mount-creates-directory footgun)
- New: `troll/ml_signals/tests/test_chart_indicator_config.py`
- Modified: `troll/ml_signals/dashboard.py` (new `CHART_INDICATOR_CONFIG_PATH` constant, two new route handlers + registrations, toolbar Save button, `_loadSavedIndicatorConfig`/`_saveIndicatorConfig` JS)
- Modified: `troll/docker-compose.yml` (new `rw` mount + env var on the `dashboard` service)
- Modified: `troll/ml_signals/tests/test_dashboard_chart.py` (6 new endpoint tests)

## Change Log

- 2026-09-09: Story created via create-story, following Stories 10.1-10.4's precedent. Status: backlog → ready-for-dev.
- 2026-09-09: Implemented persisted per-instrument chart indicator configuration — new `chart_indicator_config.py` TOML module, GET/POST `/data/coin/{id}/indicator-config` endpoints, a Save button, and a read-write Docker mount. Last story in Epic 10. Status: ready-for-dev → review.
- 2026-09-09: Code review. Fixed a JS crash on stale/renamed indicator names and widened error handling on both endpoints (corrupt file, TOML-unrepresentable values) with 2 new regression tests. 3 patches applied, 1 deferred (pre-existing pattern), 6 dismissed. Full suite: 195 passed, 1 pre-existing unrelated failure. Status: review → done. **Epic 10 complete** (all 5 stories done).
