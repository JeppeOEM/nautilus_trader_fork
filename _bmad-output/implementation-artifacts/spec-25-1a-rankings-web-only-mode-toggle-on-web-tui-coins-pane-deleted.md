---
title: 'Story 25.1a: Rankings web-only: ranking-mode toggle on the web, TUI Coins pane deleted'
type: 'refactor'
created: '2026-09-26'
status: 'done'
baseline_revision: 'f00ab8aeeab3a15f328c0f1e972e68e3e9ea62dc'
final_revision: '14e77389bce08860e78f6054230fdce31abe7d48'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-25-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Rankings have two renderers (web + `bot_tui` Coins pane/Coin-detail), and the only ranking-mode switch is the TUI's `m` key (`bot_tui/ranking_state.py:publish_mode_toggle`). The operator decided rankings are web-only and the TUI is a control surface for bots and the collector; Stories 25.2/25.4/26.3 must not refactor soon-to-be-deleted TUI rankings code.

**Approach:** Add `PUT /api/rankings/mode` to `data_api` publishing the exact bytes the TUI sends today, plus a two-state mode control on the web rankings page; in the same commit delete the TUI's Coins pane, Coin-detail view, `ranking_state`/`coin_detail_state` listeners and their tests, leaving `app.py` with Bots (start pane) and Collector only; update the docs, the rules and the `epics.md` Story 22.10 AC.

## Boundaries & Constraints

**Always:**
- The mode request body is `{"mode": "volume" | "volatility"}`. Any other value, or an extra/missing key, is a 422 from a pydantic `Literal` model (`extra="forbid"`).
- The Redis message is `ranking:control` ← `json.dumps({"mode": mode})`. That is byte-identical to `publish_mode_toggle` (`'{"mode": "volatility"}'`) and is proven by a replay test that holds the recorded bytes as a literal and cites where they came from.
- Mode stays global and last-write-wins, with publish-and-wait: the web shows the mode from `rankings:live` (`live.latest?.mode ?? data?.mode`) and never flips it optimistically.
- The web control and the removal of `m` ship in one commit.
- A publish that no subscriber received (`publish()` returns 0, i.e. `ranking_engine` is down) or a Redis error returns 503 with a detail. The UI shows the error inline and it reaches `console.error`, so the ErrorBar sees it. It is never swallowed (DATA-07).
- `rankings:live`, `collector:status`, `collector:control` and `bots:*` payloads stay unchanged.
- TUI-01's persistent-ListBox pattern is kept on the Bots and Collector panes. TUI-02 applies, and so does TEST-04 (no new warnings).
- `nautilus_trader/` and `crates/` are never touched. `sprint-status.yaml` is never written.

**Block If:**
- A module outside `bot_tui/` (production code) imports one of the four deleted modules.
- Web code reads `RANKING_COLS`' `color_fn` or `POSITIVE_COLOR`/`NEGATIVE_COLOR`. Evidence so far says it does not.

**Never:**
- Re-add rankings to the TUI, or keep any `rankings:live`/`snapshots:raw` subscription under `bot_tui/`.
- Delete or loosen a surviving test to make the suite pass.
- Let a test publish to the real `ranking:control` channel. The local stack's live `ranking_engine` subscribes to it, so tests use a monkeypatched unique channel.
- Add the Exchange/Symbol columns (29.1) or the multi-venue Collector pane (29.2).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Switch mode | `PUT {"mode":"volatility"}`, ranking_engine subscribed | 202 `{"mode":"volatility"}`; `ranking:control` receives `{"mode": "volatility"}` byte-exact | none |
| Unknown mode | `{"mode":"rank"}` / `{}` / `{"mode":"volume","x":1}` | 422 | nothing published |
| No subscriber | publish returns 0 | 503 "no ranking_engine subscribed to ranking:control" | UI shows error inline, `console.error` |
| Redis down | connection error | 503 with the exception text | same |
| Web cold open | no payload yet | both buttons shown, neither pressed, both enabled | none |
| Web click active mode | already that mode | no request sent | none |

</intent-contract>

## Code Map

- `platform/data_api/routes/rankings.py` -- rankings router (`GET /api/rankings`, technicals PUT). Add the mode route here.
- `platform/data_api/settings.py:26` -- `REDIS_URL`.
- `platform/data_api/tests/test_rankings.py` -- TestClient and real-Redis precedent (`_publish_until_observed`, `:170-220`).
- `platform/data_api/tests/test_app_frontend.py:103` -- guard that the committed `frontend/openapi.json` matches the app. Regenerate with `PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json`, then `npm run codegen`.
- `platform/data_api/tests/test_frontend_contract.py` -- every `fetch` in `client.ts` must match an OpenAPI route.
- `platform/frontend/src/api/client.ts` -- hand-written fetch helpers. The PUT pattern is `saveTechnicalsColumns` (`:185`).
- `platform/frontend/src/pages/RankingsPage.tsx` -- venue chips at `:296-311` (`filter-panel`, `tabbtn`, `aria-pressed`), live message type at `:119-130`, TS mirror of `RANKING_COLS` at `:56-98`.
- `platform/frontend/src/pages/RankingsPage.test.tsx` -- vitest. The `vi.mock("../api/client")` list is at `:12-27`, and `describe("venue chips")` at `:504`.
- `platform/bot_tui/app.py` -- 1729 lines. Coins/Coin-detail parts: imports `:66-84`, `_SelectableCoinRow`, coins body/filter/mode/stale-banner/coin-detail methods, `_HELP_TEXT`, footers, and `run()` listeners `:1693-1695`.
- `platform/bot_tui/{coins_pane,coin_detail,coin_detail_state,ranking_state}.py` -- to delete. `coin_detail.osc52_copy_sequence` is still used by `_open_dashboard_bot`, so it moves to `bots_pane.py`. `ranking_state.REDIS_URL` is the `BotTuiApp` default and is replaced by `bots_state.REDIS_URL`.
- `platform/bot_tui/tests/` -- delete `test_coins_pane`, `test_coin_detail`, `test_coin_detail_state`, `test_ranking_state`, `test_app_filter`, `test_app_coin_detail` and `test_app_stale_badge`. Rework `conftest.py`, `test_app_body`, `test_app_navigation` and `test_ad8_boundary` (`_READER_MODULES`).
- `platform/views/ranking_columns.py` -- `RANKING_COLS` 4-tuples with a CSS-hex `color_fn`, plus `POSITIVE_COLOR`/`NEGATIVE_COLOR`. The TUI's Coins pane is their only reader.
- `platform/views/coin_detail.py` -- `COIN_DETAIL_GROUPS`, `rank_row_for` and `snapshot_for` are read only by the TUI (data_api uses the `metrics_*` and `catalog_snapshot_rows` functions). Tests are in `views/tests/test_coin_detail.py`.
- `platform/tests/test_boundaries.py` -- `LEGACY_MODULE_TO_CONTEXT` has only `"bot_tui": BOT_TUI` (`:177`); the edge `(BOT_TUI, VIEWS)` is at `:111`.
- `platform/tests/test_images.py:369-390` -- `bot_tui.app` entrypoint plus import closure.
- Docs: `platform/ARCHITECTURE.md` (`:43`, `:68-69`, `:106`, `:321-334`, `:346-348`); `platform/docs/BOT_OPERATIONS.md` (`:46-59`, `:200`); `platform/README.md` (no TUI section yet); `platform/docs/DATA_DICTIONARY.md` (`:526`, `:593-594`, `:667`); `platform/CLAUDE.md` (SSOT-01/04/05 `:169-173`, the Metrics SSOT heading); `platform/frontend/src/pages/docs/data.ts:88`; `_bmad-output/planning-artifacts/epics.md` (22.10 AC `:2505-2507`).

## Tasks & Acceptance

**Execution:**
- [x] Scratch (not committed) -- run the current `ranking_state.publish_mode_toggle` against a capturing fake Redis client and record the `(channel, data)` it publishes -- this gives the replay test its literal.
- [x] `platform/data_api/routes/rankings.py` -- add `RANKING_CONTROL_CHANNEL = "ranking:control"`, a `RankingModeRequest` model (`Literal`, `extra="forbid"`) and `PUT /api/rankings/mode`. The route opens a short-lived `redis.asyncio` client on `settings.REDIS_URL`, publishes `json.dumps({"mode": body.mode})`, returns 202 `{"mode": ...}` and returns 503 on 0 receivers or a Redis error -- makes the mode reachable from the web.
- [x] `platform/data_api/tests/test_rankings_mode.py` -- add tests:
  - A real-Redis replay test: monkeypatch the channel to a unique name, subscribe to it, PUT, then assert the received bytes equal the recorded literal and that the production constant is `"ranking:control"`.
  - 422 cases.
  - A 503-on-no-subscriber test.
- [x] `platform/frontend/openapi.json`, `src/api/schema.ts` -- regenerate.
- [x] `platform/frontend/src/api/client.ts` -- add `setRankingMode(mode)`.
- [x] `platform/frontend/src/pages/RankingsPage.tsx` -- add a `role="group" aria-label="Ranking mode"` two-button control (`Volume`/`Volatility`, `tabbtn`/`aria-pressed`) next to the venue chips:
  - Pressed state comes from the payload `mode`.
  - Clicking the other mode calls `setRankingMode`.
  - Buttons are disabled while the request is in flight.
  - An error renders inline and is sent to `console.error`.
  - Update the mirror comment, which says bot_tui mirrors it.
- [x] `platform/frontend/src/pages/RankingsPage.test.tsx` -- add `setRankingMode` to the mock and add tests for pressed-from-payload, click-sends-other-mode, click-active-mode-noop, error-shown and cold-open-none-pressed.
- [x] `platform/bot_tui/` -- delete the four modules and seven tests. Strip the Coins/Coin-detail code from `app.py`:
  - Start view `"bots"`; `_BREADCRUMB_LABELS` keeps bots/collector/help, so the command bar keeps `:bots`, `:data` and `:help`.
  - Drop the `bid`/`ask`/`mid` palette entries, `_SelectableCoinRow`, the filter, mode, stale-banner and coin-detail methods, and the rankings/snapshots listeners.
  - Update the docstring, `_HELP_TEXT` and the footers.
  - Move `osc52_copy_sequence` (with its test) into `bots_pane.py`.
  - Rework the conftest and the surviving tests.
- [x] `platform/bot_tui/tests/test_no_rankings_feed.py` -- add a grep test: no `rankings:live`, `snapshots:raw`, `ranking:control`, `views.coin_detail` or `views.ranking_columns` anywhere in `bot_tui/*.py`.
- [x] `platform/views/ranking_columns.py` -- remove the `color_fn` member and `POSITIVE_COLOR`/`NEGATIVE_COLOR`. The web never read them: its TS mirror has no colours. Keep `(key, label, format_fn)` as the single column source.
- [x] `platform/data_api/tests/test_ranking_columns_mirror.py` -- add a test that the TS mirror in `RankingsPage.tsx` has the same `(key, label)` sequence as `RANKING_COLS`. With the TUI gone, the TUI no longer keeps the Python list honest.
- [x] `platform/views/coin_detail.py`, `views/tests/test_coin_detail.py` -- delete `COIN_DETAIL_GROUPS`, `MIN_INDICATOR_DECIMALS`, `rank_row_for` and `snapshot_for`, and their tests. Their only consumer was TUI Coin-detail, and dead read models would otherwise be carried by 25.2.
- [x] `platform/tests/test_boundaries.py`, `tests/test_images.py` -- drop any now-dead bot_tui expectations. Keep the `(BOT_TUI, VIEWS)` edge only if a surviving bot_tui import needs it; otherwise remove it.
- [x] Docs, as listed in the Code Map:
  - `ARCHITECTURE.md`: the bot_tui row reads `bots:*` and `collector:status` and publishes `bots:control` and `collector:control`; the `ranking:control` publisher is `data_api`.
  - `BOT_OPERATIONS.md` and a README TUI section: describe the two-pane TUI.
  - `DATA_DICTIONARY.md` §3: `ranking:control` publisher and payload, and drop the Coins-pane renderers.
  - `CLAUDE.md` SSOT-01/04/05: web-only renderers, with an `[amended 2026-09-26: Story 25.1a]` tag.
  - `epics.md` 22.10 AC: struck, with an amendment pointing to 25.1a.

**Acceptance Criteria:**
- Given the stack runs, when a user clicks the non-active mode on `/rankings`, then `ranking:control` receives the TUI's exact bytes, and the pressed button changes only once `rankings:live` carries the new mode.
- Given `bot_tui` starts, when it renders, then it opens on the Bots pane, `:data` reaches Collector, and `m`, `/`, space and Enter-to-coin do nothing. `:help` and the footers list only existing keys, and nothing under `bot_tui/` subscribes to `rankings:live` or `snapshots:raw`.
- Given the full platform suite, when it runs, then it shows no new failures and no new warnings against the 10-failure baseline, and frontend `npm test`, `npm run lint` and `npx tsc -b` pass.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14 (high 0, medium 4, low 10)
- defer: 1 (high 0, medium 0, low 1)
- reject: 11 (high 0, medium 0, low 11)
- addressed_findings:
  - `[medium]` `[patch]` `RankingModeControl` remounted between the loading and loaded branches, losing an in-flight switch and its error. The loading branch now uses the same element path (term-box > rankings-toolbar > control). Test added: click while loading, payload arrives, control stays disabled.
  - `[medium]` `[patch]` `PUT /api/rankings/mode` had no Redis timeout. Added `socket_connect_timeout`/`socket_timeout` = 2 s, so a blackholed Redis gives a prompt 503.
  - `[medium]` `[patch]` The TUI help, README and BOT_OPERATIONS pointed at a `/rankings` URL that does not exist. They now name the web UI's home page, `/`.
  - `[medium]` `[patch]` The two new data_api test files were missing the closing line of the LGPL header. Added.
  - `[low]` `[patch]` A failed-switch alert outlived a later mode change. Its visibility is now derived from the mode shown when it was sent (no effect, and lint stays clean). Test added.
  - `[low]` `[patch]` Documented a `Known limit:` on the route: `publish()` counts any subscriber, not only `ranking_engine`, with the upgrade path via Story 25.2.
  - `[low]` `[patch]` The listener socket tests could hang on `accept()`. They now have a 5 s timeout and use `with` blocks.
  - `[low]` `[patch]` Fixed the staleness-threshold comment arithmetic (bots_state, RankingsPage), a 114-char docstring line in bots_state, SSOT-03 and the SSOT intro wording in CLAUDE.md, and the open_listener.go caller comment.
  - `[low]` `[patch]` DEPLOY_CHECKLIST gained a web mode-toggle rollout check in place of the struck TUI check.
  - `[low]` `[patch]` The architecture diagram still put bot_tui in the box that reads `rankings:live`. bot_tui is now its own box, wired to `bots:*`.
  - `[low]` `[patch]` The `test_no_rankings_feed.py` docstring now says import separation is enforced by `test_boundaries.py`'s missing `(BOT_TUI, VIEWS)` edge.
  - `[low]` `[patch]` `RANKING_COLS` is now typed `list[tuple[str, str, Callable[[Any], str]]]`.

### 2026-09-26 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7 (high 0, medium 1, low 6)
- defer: 0
- reject: 17 (high 0, medium 0, low 17)
- addressed_findings:
  - `[medium]` `[patch]` Mode-control alert lifecycle: a failure sent on cold open vanished when the first payload merely revealed the unchanged mode, and a superseded failure came back when the mode later returned (A→B→A). The control now tracks the last seen mode during render: the failure is cleared once, at a real mode change (or when the first payload is the failed target). Two regression tests were added, and both fail on the previous logic.
  - `[low]` `[patch]` `setRankingMode` had no client-side timeout, so a request stalled in transit kept both mode buttons disabled. It now aborts after 10 s (`AbortSignal.timeout`).
  - `[low]` `[patch]` The OpenAPI contract omitted the documented 503. The route now declares `responses={503: ...}`, and `openapi.json`/`schema.ts` were regenerated.
  - `[low]` `[patch]` `RANKING_COLS`' `format_fn` has no Python consumer after the TUI removal, and the mirror test holds only `(key, label)`. Added a `Known limit:` naming the ceiling and the upgrade path (a declarative format spec, or serving the column metadata). The spec keeps the 3-tuple.
  - `[low]` `[patch]` The in-app docs (`frontend/src/pages/docs/kbData.ts`) still listed bot_tui as a rankings/snapshots reader and a `ranking:control` publisher, and still described a Coins-pane stale banner. The current-state rows are updated; the dated changelog entries were left as history.
  - `[low]` `[patch]` Two `live_paper` docstrings pointed at the deleted `coin_detail.py`'s `format_indicator` as a live precedent. Both are reworded to "since-deleted".
  - `[low]` `[patch]` `_build_body` guarded unknown views with an `assert`, which `python -O` strips. It is now an explicit `KeyError`, which matches `_FOOTER_HINT_TEXTS`.

## Design Notes

- **Why 503 on zero receivers.** The TUI ignored the receiver count, so a mode switch sent while `ranking_engine` was down vanished silently. The web reports it instead. This changes nothing on the wire.
- **Why the test uses a unique channel.** The local dev stack runs a live `ranking_engine` on the same Redis. The byte-identity claim is about the payload, and the channel name is asserted as a constant.
- **Recorded decision** (AC: "record which"): `RANKING_COLS`' `color_fn` member and `POSITIVE_COLOR`/`NEGATIVE_COLOR` are removed because only `bot_tui/coins_pane.py` read them. Key, label and `format_fn` stay.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests views/tests research/tests tests -q` -- expected: no failures beyond the pre-existing baseline, with the baseline measured first on a clean HEAD.
- `cd platform/frontend && npm test && npm run lint && npx tsc -b` -- expected: pass.
- `grep -rnE "rankings:live|snapshots:raw" platform/bot_tui --include=*.py` -- expected: matches only in the grep-test file itself.

## Auto Run Result

Status: done

**Summary.** This was a follow-up review pass on the shipped Story 25.1a: rankings are web-only, a `PUT /api/rankings/mode` route plus a web Volume/Volatility control replace the TUI `m` key, and bot_tui's Coins and Coin-detail views are deleted. Two fresh reviewers (Blind Hunter, Edge Case Hunter) found nothing at intent or spec level. 7 patches were applied: 1 medium (the mode-control failure alert lifecycle) and 6 low (a client timeout, the 503 in OpenAPI, a `Known limit:` on the dead `format_fn`, and stale docs/docstrings/assert).

**Files changed in this pass**
- `platform/frontend/src/pages/RankingsPage.tsx`: the failure alert is cleared only by a real mode change, tracked during render.
- `platform/frontend/src/pages/RankingsPage.test.tsx`: two regression tests (a cold-open failure survives the first payload; a superseded failure never comes back).
- `platform/frontend/src/api/client.ts`: `setRankingMode` aborts after 10 s.
- `platform/data_api/routes/rankings.py`: the route declares its 503 response.
- `platform/frontend/openapi.json`, `platform/frontend/src/api/schema.ts`: regenerated.
- `platform/views/ranking_columns.py`: a `Known limit:` on `format_fn`, which no Python code reads any more.
- `platform/frontend/src/pages/docs/kbData.ts`: the module map and Redis channel rows no longer list bot_tui for rankings.
- `platform/live_paper/bot_status.py`, `platform/live_paper/trade_history.py`: docstring references to the deleted module reworded.
- `platform/bot_tui/app.py`: the unknown-view `assert` replaced by an explicit `KeyError`.

**Review:** 7 patches applied (1 medium, 6 low), 0 deferred, 17 rejected. The rejects were:
- Spec-directed choices: publish-and-wait with no local flip, `color_fn`/`COIN_DETAIL_GROUPS` deleted, `osc52_copy_sequence` moved into `bots_pane`.
- Pre-existing posture: unauthenticated loopback `data_api` writes, codegen typing `Literal` as `string`, stale-`t`-key docstring.
- Low-value hardening: the regex mirror test (a partial match already fails, because sequences are compared), grep-guard evasion (the docstring already delegates to `test_boundaries`), the ledger for a request that fails loudly, the exception repr in a 503, and the diagram's missing collector edge.

**Follow-up review:** not recommended. The fixes are localized; the one behaviour change is small, UI-only and covered by tests.

**Verification**
- Full platform Python suite with `REDIS_URL=redis://127.0.0.1:16379`: 1668 passed, 0 failed, 1 deselected (the pre-existing `test_rankings_live_message_reflected_by_rest_and_ws_relay`, which publishes onto live `rankings:live`). `test_app_frontend`'s OpenAPI guard passes with the regenerated contract.
- Frontend: `npm test` passed (23 files, 310 tests). `npx tsc -b` is clean. `npm run lint` shows 3 warnings, all in files this story does not touch (`TrustedHtml.tsx` ×2, `useCandles.ts`).
- `ruff format --check` is clean on the touched Python files. `ruff check` shows the same 4 findings as before the patch (PT018 and 3× D401), none introduced.

**Residual risks**
- Unchanged from the first pass: the real-Redis tests need the local `REDIS_URL` shift, and the mode resets to `volume` on a `ranking_engine` restart (Story 25.2).
- `format_fn` remains an unenforced record of each column's text format (it is now documented as a Known limit).
