---
title: 'Story 15.2: Live coin-rankings page'
type: 'feature'
created: '2026-09-14'
status: 'done'
baseline_revision: 'd84f8a206e8b364ed95d9f880820bcab3ff537e3'
final_revision: '46537de924'
review_loop_iteration: 0
followup_review_recommended: false # judged: 5 patches (1 medium/4 low), all mechanical/narrow, fully verified by tests; no high-severity or cross-cutting findings warranting independent re-review
context: [
  '{project-root}/troll/CLAUDE.md',
  '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md',
]
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `data_api` (the new Read-Only Facade, Story 15.1) has no rankings route or live relay yet, and `troll/frontend/`'s `RankingsPage.tsx` is a one-line placeholder — the SPA has no way to show the live coin-rankings table that `dashboard.py` today serves via 2s client polling of a reformatted `/api/rankings` payload.

**Approach:** Add `data_api/routes/rankings.py` (`GET /api/rankings`, verbatim passthrough of `ranking_engine`'s cached `rankings:live` message, `ranks`→`items` renamed per epics AC1's literal shape) and `data_api/ws/live.py` (`/ws/live`, relays the same message completely verbatim including the `ranks` key), both fed by one shared `data_api/redis_bus.py` background subscriber (started once via FastAPI lifespan). Build `frontend/src/hooks/useLiveChannel.ts` + a real `RankingsPage.tsx` consuming both endpoints, keyed by `instrument_id`, message-ordered, with heartbeat-timeout stale marking and click-to-chart navigation.

## Boundaries & Constraints

**Always:**
- `GET /api/rankings` and `/ws/live` never recompute or resort — they relay `ranking_engine`'s existing message content only (AD-F2). The only sanctioned reshape anywhere is `GET /api/rankings`'s `ranks`→`items` rename (epics AC1's literal `{"items": [...], "updated_at": ...}` shape); `/ws/live` keeps the raw `ranks` key (epics AC2: "no reshaping beyond channel subscription").
- One shared Redis subscriber (`redis_bus.py`'s `RankingsBus`, started once at app startup via FastAPI `lifespan`) feeds both the route's cache and every `/ws/live` connection's per-listener queue — no per-request or per-websocket Redis connection.
- New routes register in `app.py` **above** the existing `GET /api/{full_path:path}` 404 catch-all (violating this order silently 404s the new route — confirmed failure mode from Story 15.1).
- Frontend table row order = message order verbatim; React key = `instrument_id`; no client-side re-sort beyond the active Ranking Mode already reflected in that order.
- Malformed/non-dict or missing-`ranks` Redis payloads are logged and skipped, keeping the last good cache — mirrors `dashboard.py:2242-2261`'s `_handle_rankings_message` validation exactly.
- Redis reconnect-on-error loop mirrors `dashboard._redis_listener`'s discipline (`dashboard.py:2280-2312`): reconnect forever, 2s sleep between attempts, last-cached snapshot keeps serving `GET /api/rankings` through an outage.
- `docker-compose.yml`'s `data_api` service gets `REDIS_URL: "redis://127.0.0.1:${REDIS_PORT:-6379}"` added, matching every sibling Redis-consuming service's exact existing line.
- Rankings table shows exactly the columns `ml_signals/ranking_columns.py`'s `RANKING_COLS` defines (13 metrics + rank + `instrument_id`) — no more, no fewer; the wire payload carries additional fields (e.g. `microprice`, `microprice_lean`) that pass through but are not rendered as columns (parity is about visible columns, not wire content).

**Block If:** None identified — WS message-envelope shape and the exact stale-threshold constant value are implementation-owned per the established convention below (Design Notes), not decisions requiring human input.

**Never:**
- No new column/metric beyond `RANKING_COLS` (epics AC7 parity gate — any future addition must land in `bot_tui` too, out of this story's scope).
- No polling fallback once `/ws/live` is connected — polling is dashboard.py's current (superseded) mechanism, not preserved.
- No auth/session logic on `/ws/live` (SEC-01's `127.0.0.1` bind is the only access control, unchanged).
- No reimplementation of `ranking_engine`'s rank/stale computation anywhere in `data_api` or the frontend.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | `rankings:live` message arrives, ≥1 WS client connected | Bus caches it, fans out to every listener queue; `/ws/live` clients receive it verbatim (with `ranks`); table re-renders in message order | No error expected |
| First load, before any message cached | `data_api` just started, bus has not yet received a message | `GET /api/rankings` returns `503` (cache empty is an honest transient state, not a fabricated empty snapshot) | Frontend shows a loading state, not an empty table |
| New WS client connects mid-stream | Bus already has a cached message | On `accept()`, immediately `send_json` the cached `latest` message before waiting on the listener queue, so a new client doesn't wait a full heartbeat for its first paint | None |
| Malformed Redis payload | Non-dict JSON, or dict missing list-shaped `ranks` | Logged and skipped; previous good cache is kept, unmodified | Logged via `logger.warning`, no crash, no stale-cache overwrite |
| Redis connection drops | `aioredis` raises during `pubsub.listen()` | Bus reconnects after a 2s sleep, forever; `GET /api/rankings` keeps serving the last-cached snapshot throughout | Logged via `logger.info`/`warning`, no process crash |
| Row exceeds heartbeat timeout | A row's `updated_at` (from the last received message) is older than the configured stale threshold | Row renders visibly dimmed/badged as stale | Never silently frozen in last position, never dropped from the table |

</intent-contract>

## Code Map

- `troll/data_api/redis_bus.py` -- NEW: `RankingsBus` class (`REDIS_URL` env constant, `RANKINGS_CHANNEL = "rankings:live"`, `.latest` cached message, `.subscribe()`/`.unsubscribe()` per-listener `asyncio.Queue`, `.handle_message()` validation, `.run(redis_url)` reconnect-forever loop mirroring `dashboard.py:2280-2312`); module-level `bus = RankingsBus()` instance the app uses; plain class (not singleton-baked) so tests can construct isolated instances.
- `troll/data_api/routes/rankings.py` -- NEW: `GET /api/rankings` returning `RankingsResponse(items, updated_at, mode, stale_instrument_ids)`, `ranks`→`items` renamed, `503` if `bus.latest` is `None`.
- `troll/data_api/ws/live.py` -- NEW: `/ws/live` WebSocket endpoint; on connect, sends `bus.latest` if present, then relays every subsequent message from its `bus.subscribe()` queue verbatim (unrenamed); `bus.unsubscribe()` on `WebSocketDisconnect`.
- `troll/data_api/app.py:33-58,126-151` -- wire new router + websocket route above the `/api/{full_path:path}` catch-all (line ~126); add a `lifespan` context manager starting `asyncio.create_task(bus.run(REDIS_URL))` on startup and cancelling it on shutdown.
- `troll/docker-compose.yml` -- add `REDIS_URL` to the `data_api` service block (matches `ranking_engine`/`bot_tui`/`dashboard`/`collector`'s existing lines).
- `troll/ranking_engine/engine.py:605-687` -- reference only, unmodified; source of the `rankings:live` wire format (`mode`, `updated_at`, `ranks`, `stale_instrument_ids`).
- `troll/ml_signals/dashboard.py:1642-1683,2242-2312` -- reference only; existing (superseded) `_rankings_json`/listener/reconnect pattern this story's backend mirrors for validation/reconnect discipline, not for its reshaping.
- `troll/bot_tui/ranking_state.py:41,59,81-97,133-163` -- reference only; `_RANKING_STALE_SECONDS = 15.0` convention and reconnect pattern to mirror in the frontend hook.
- `troll/ml_signals/ranking_columns.py:51-64` -- reference only; `RANKING_COLS` is the authoritative column list the frontend table hand-mirrors (same pattern `bot_tui`'s own urwid renderer already uses independently).
- `troll/frontend/src/hooks/useLiveChannel.ts` -- NEW: opens one WS to `/ws/live`, exposes latest parsed message + connection state, reconnect-on-close with backoff.
- `troll/frontend/src/pages/RankingsPage.tsx` -- replace placeholder: React Query `useQuery` seeds initial state from `GET /api/rankings`; `useLiveChannel` drives subsequent updates; renders the table (rank, instrument_id, `RANKING_COLS`-mirrored metrics), row `key={instrument_id}`, stale-row marking, `onClick` navigates to `/chart/:iid`.
- `troll/frontend/src/api/client.ts` -- add `fetchRankings()`; add a Pydantic response model export for `/api/rankings` and regenerate `frontend/src/api/schema.ts` via the existing `npm run codegen` pipeline (Story 15.1).
- `troll/data_api/tests/test_rankings.py` -- NEW.
- `troll/frontend/src/pages/RankingsPage.test.tsx` -- NEW.

## Tasks & Acceptance

**Execution:**
- [x] `troll/data_api/redis_bus.py` -- implement `RankingsBus` (cache + fan-out + reconnect-forever subscriber) -- feeds both the REST cache and the WS relay from one Redis connection
- [x] `troll/data_api/routes/rankings.py` -- implement `GET /api/rankings` (items-renamed passthrough, 503 before first message) -- epics AC1
- [x] `troll/data_api/ws/live.py` -- implement `/ws/live` (verbatim relay, immediate cached-message send on connect) -- epics AC2
- [x] `troll/data_api/app.py` -- wire router + websocket route above the catch-all; add `lifespan` starting/stopping `bus.run()` -- required ordering constraint
- [x] `troll/docker-compose.yml` -- add `REDIS_URL` to `data_api` service -- infra parity with sibling services
- [x] `troll/frontend/src/hooks/useLiveChannel.ts` -- WS hook with reconnect -- epics AC2/AC3
- [x] `troll/frontend/src/pages/RankingsPage.tsx` -- real table: initial fetch + live updates, message-order rendering, `instrument_id` keys, stale marking, click-to-chart -- epics AC3/AC4/AC5/AC6/AC7
- [x] `troll/frontend/src/api/client.ts` + codegen -- typed `fetchRankings()` -- consistency with Story 15.1's generated-types rule
- [x] `troll/data_api/tests/test_rankings.py` -- one real-Redis integration test (publish a synthetic message to the real `dydx-redis` container, assert `GET /api/rankings` + `/ws/live` relay both reflect it) plus pure-logic tests for the 503-before-cache and malformed-payload-skip edge cases -- epics AC8/TEST-03
- [x] `troll/frontend/src/pages/RankingsPage.test.tsx` -- Vitest/RTL: stale-row marking and click-to-navigate, with a mocked `useLiveChannel`

**Acceptance Criteria:**
- Given `ranking_engine`'s `rankings:live` publish, when `GET /api/rankings` is called, then the response is `{"items": [...], "updated_at": ..., "mode": ..., "stale_instrument_ids": [...]}` with every `ranks` entry's fields passed through unchanged
- Given a client subscribed to `/ws/live`, when a `rankings:live` message arrives, then it is relayed with its original `ranks` key, no rename, no other reshaping
- Given the table is rendered, when it re-renders on a live tick, then row order matches the message's `items`/`ranks` order exactly and each row's React key is its `instrument_id`
- Given a row's `updated_at` age exceeds the configured stale threshold, when the table renders, then that row is visibly marked stale, never silently frozen in place
- Given the operator clicks a rankings row, when navigation occurs, then the app routes to `/chart/:iid` for that row's `instrument_id`
- Given `bot_tui`'s `RANKING_COLS`-driven column set, when `RankingsPage` renders its table, then no column appears beyond that same set
- Given the rankings query/relay path, when tests run, then `ranking_engine`'s existing `rankings:live`-shape test coverage is unaffected and a new real-Redis integration test exercises both `GET /api/rankings` and `/ws/live`

## Spec Change Log

- 2026-09-14 (implementation): Two small, implementation-owned deviations discovered while implementing, neither changing the intent-contract:
  1. **`routes/rankings.py` and `ws/live.py` reference `redis_bus.bus` via the module (`from data_api import redis_bus; redis_bus.bus...`), not `from data_api.redis_bus import bus`.** A direct name import would bind the original singleton at import time, making it impossible for a test to `monkeypatch.setattr(redis_bus, "bus", RankingsBus())` and have the route actually see the isolated instance. This is exactly what the Design Notes' "plain class, not a baked-in singleton" already called for -- just the specific import shape needed to make it work, not a design change.
  2. **`RankingsPage.tsx`'s client-side staleness check uses a 1s `setInterval` tick (`now` state) instead of calling `Date.now()` directly during render.** Found via `npm run lint` (oxlint's `react(purity)` warning against impure calls during render), but it's a real correctness gap, not just a lint nit: a row must go visibly stale from wall-clock time passing alone, even if the feed goes completely dead and no new message (WS or otherwise) ever triggers a re-render again. Reading `Date.now()` inline would only recheck staleness incidentally, on some other re-render; the 1s tick guarantees it's rechecked on its own. Also fixed a pre-existing test-isolation gap surfaced by this page's own tests: `frontend/src/test/setup.ts` doesn't register React Testing Library's automatic per-test `cleanup()` (Story 15.1's `DocsPage.test.tsx` never needed it, since its two tests query disjoint text) -- added an explicit `afterEach(() => cleanup())` locally in `RankingsPage.test.tsx` rather than touching the shared setup file, since this story's tests are the first to reuse the same instrument id text across cases.

## Review Triage Log

### 2026-09-14 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 1, low 4)
- defer: 4: (high 0, medium 1, low 3)
- reject: 5: (high 0, medium 0, low 5)
- addressed_findings:
  - `[medium]` `[patch]` `redis_bus.py:handle_message` validated only `dict` + list-shaped `ranks`; a cached message missing `mode`/`updated_at` (or with a non-dict `ranks` row) would make `routes/rankings.py`'s plain-index reads raise `KeyError`/a pydantic error -> 500, contradicting the "honest 503/skip, never crash" design. Tightened validation to require `mode: str`, `updated_at: int`, and `ranks: list[dict]`; added 2 regression tests.
  - `[low]` `[patch]` `ws/live.py` read `bus.latest` and subscribed to the fan-out queue in two separate steps, leaving a narrow window where a message published in between was neither in the initial send nor yet queued -- silently lost for that connection. Reordered to subscribe first (a message published in the gap now arrives twice, harmless for a full-snapshot relay, instead of zero times).
  - `[low]` `[patch]` `ws/live.py`'s except clause only caught `WebSocketDisconnect`; a network-level drop mid-`send_json` can raise a different exception on some ASGI transports, which would have propagated as an unhandled crash instead of the same graceful disconnect+log path. Broadened to catch and log any non-cancellation exception as a disconnect.
  - `[low]` `[patch]` `test_rankings.py`'s integration test hardcoded `_REDIS_URL = "redis://127.0.0.1:6379"` for its publisher instead of reading `redis_bus.REDIS_URL` like the app does -- a `REDIS_URL`/`REDIS_PORT` override in the test environment would make the test silently publish to a different Redis than the app subscribes to. Now reads `redis_bus.REDIS_URL` directly.
  - `[low]` `[patch]` `RankingsPage.tsx`'s `formatCell` swallowed every formatting exception into a bare `"ERR"` string with no trace. Added a `console.error` before returning `"ERR"` so a real upstream shape/type mismatch is visible, not silently inert.
  - `[medium]` `[defer]` Unbounded per-`/ws/live`-listener `asyncio.Queue` (no `maxsize`/backpressure) and no cap on concurrent listeners -- real growth-shape concern given this project's documented `DataEngine`-OOM history, but low urgency at `rankings:live`'s actual ~5s-heartbeat message rate on a single-operator, localhost-only service. Logged to `deferred-work.md`, not fixed this pass.
  - `[low]` `[defer]` Unthrottled `logger.warning` on every malformed `rankings:live` payload (log-spam risk if a publisher misbehaves continuously); no jitter on `useLiveChannel`'s reconnect backoff (thundering-herd risk on a `data_api` restart, negligible at this deployment's scale); no explicit graceful-drain of open `/ws/live` connections on shutdown beyond uvicorn/Starlette's own ASGI teardown. All three logged to `deferred-work.md`, not fixed this pass.
  - `[reject]` No `pytest.mark.skipif`/connectivity guard if Redis isn't running for the integration test -- matches this repo's existing established convention (real infra assumed available in dev/test, no fakeredis), not a regression.
  - `[reject]` Frontend `useLiveChannel`'s `JSON.parse(...) as T` has no runtime shape validation, and `RankingsPage` doesn't defensively dedupe/fallback on `instrument_id` -- both trust `ranking_engine` as the sole SSOT for this data's shape/uniqueness (AD-F2), consistent with this story's explicit "never reimplement upstream's computation/validation" boundary; defensively re-validating a trusted upstream contract on every reader would cut against DESIGN-01 YAGNI.
  - `[reject]` Browser background-tab timer throttling can delay `RankingsPage`'s 1s staleness-recheck tick -- a universal browser platform limitation affecting any timer-based UI, not specific to this implementation, and not practically fixable within this story's scope.
  - `[reject]` Test-only theoretical fragility: `websocket.receive_json()` in the integration test has no explicit timeout, relying on `handle_message`'s cache-then-fan-out ordering never changing. True today (verified by reading the function), and the preceding `_publish_until_observed` assertion already proves delivery timing works before this call runs.

## Design Notes

- **Two distinct staleness signals, do not conflate:** `ranking_engine` already computes per-instrument `stale_instrument_ids` inside the message itself (a market-data staleness judgment); this story's stale marking (epics AC4) is a *second*, client-side signal — whether the *whole message* (`updated_at`) is older than a heartbeat timeout, i.e. `data_api`/the browser hasn't heard from `ranking_engine` recently at all. Both are real and both should be visually distinguishable if both fire, but they answer different questions.
- **Stale threshold constant:** mirror `bot_tui/ranking_state.py`'s existing `_RANKING_STALE_SECONDS = 15.0` (3× the engine's heartbeat) as the frontend's threshold, for cross-surface consistency (SSOT-03 spirit) — a hand-declared `RANKING_STALE_MS = 15_000` TS constant, not imported (no cross-language import path exists).
- **`RankingsBus` is a plain class**, with a module-level `bus` instance the running app imports — not a global singleton baked into class definition — so tests can construct an isolated instance without sharing state across the pytest session.
- **`GET /api/rankings`'s `items` rename is the one AC1-sanctioned reshape**; everything else (field names/values inside each row, `mode`, `stale_instrument_ids`) passes through byte-for-byte from the cached Redis payload. `/ws/live` doesn't even do that rename — it relays the original message unchanged, since epics AC2 explicitly scopes its reshaping to "channel subscription" only.
- **Frontend column list is a hand-declared TS mirror of `RANKING_COLS`**, not a codegen'd import (Python source, no build-time bridge) — same precedent as `bot_tui`'s own urwid renderer already independently mirroring the same metadata; keep it a short flat array of `{key, label, format}` so a future `ranking_columns.py` change is easy to notice and port.

## Verification

**Commands:**
- `cd troll && PYTHONPATH=. python3 -m pytest data_api/tests -q` -- expected: all pass, including new `test_rankings.py` (real Redis container already running per `docker ps`, `dydx-redis`)
- `cd troll/frontend && npm run build && npm run test` -- expected: build succeeds (new page/hook each their own chunk per existing code-splitting), Vitest suite passes including new `RankingsPage.test.tsx`
- `cd troll/frontend && npm run lint` -- expected: clean (oxlint, per Story 15.1 precedent)

**Manual checks (if no CLI):**
- `docker compose up -d data_api` then `curl http://127.0.0.1:9100/api/rankings` and connect a WS client to `ws://127.0.0.1:9100/ws/live`; confirm both reflect the real `ranking_engine`'s live output and that killing/restarting the `dydx-redis` container doesn't crash `data_api` (reconnect-forever behavior).

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `PYTHONPATH=. python3 -m pytest data_api/tests -q` -- 19/19 passed (11 pre-existing from Story 15.1 + 8 new in `test_rankings.py`), including the real-Redis integration test against the already-running `dydx-redis` container (`redis://127.0.0.1:6379`, confirmed via `docker ps` before starting).
- `PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json` then `cd frontend && node scripts/gen-api-types.mjs openapi.json src/api/schema.ts` -- regenerated after adding `GET /api/rankings`'s `RankingsResponse` Pydantic model; confirmed the committed `openapi.json` matches `app.openapi()` byte-for-byte via the existing Story-15.1 drift test (`test_committed_openapi_json_matches_the_live_schema`).
- `cd frontend && npm run build` -- clean, strict TypeScript; `RankingsPage` and `useLiveChannel` bundle into the existing per-page code-split chunk (`RankingsPage-*.js`, ~10.8kB), never pulled into the shared `index.js` entry.
- `cd frontend && npm run test` -- 7/7 passed (2 pre-existing `DocsPage` + 5 new `RankingsPage` tests) + `test:codegen` 4/4 (unchanged, pre-existing).
- `cd frontend && npm run lint` -- exit 0; only the 2 pre-existing Story 15.1 warnings remain (`TrustedHtml.tsx`'s `only-export-components` advisories). A new warning this story introduced (`react(purity)`: calling `Date.now()` during render) was found and fixed properly, not suppressed -- see Spec Change Log entry #2.
- **Real test-isolation bug caught by actual test runs, not assumed:** the first `RankingsPage.test.tsx` run failed 3/5 tests with `TestingLibraryElementError: Found multiple elements with the text: BTC-USD-PERP.DYDX` -- `frontend/src/test/setup.ts` never registered React Testing Library's automatic per-test `cleanup()`, so a previous test's rendered DOM was still present when the next test queried by text. Fixed locally in the new test file (`afterEach(() => cleanup())`), verified the full 5/5 pass afterward. See Spec Change Log entry #2 for why this was fixed locally rather than in the shared setup file.
- **Real pub/sub race avoided, not assumed away:** the integration test in `test_rankings.py` retry-publishes the synthetic `rankings:live` message in a loop (`_publish_until_observed`, up to 10s) rather than sleeping a fixed duration before publishing once -- Redis pub/sub only delivers to already-subscribed clients, and the bus's background subscriber task needs a moment after the app's `lifespan` starts it to actually connect and subscribe. Verified this matters: an earlier draft using a single fixed `time.sleep(0.3)` before one publish was flaky under load; the retry-loop version passed reliably across repeated runs.
- Full regression: `PYTHONPATH=. python3 -m pytest data_api/tests ml_signals/tests -q` -- 233 passed, 1 pre-existing unrelated failure (`test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`, already tracked as open/out-of-scope since Story 14.3 -- confirmed not caused by this story's changes).
- Cleaned up stale `data_api/**/__pycache__` directories left over from a prior, lost attempt at this same story (compiled `.pyc` files for `redis_bus.py`/`routes/rankings.py`/`ws/live.py` existed on disk with no corresponding `.py` source -- forensic evidence noted at task start) -- harmless but superseded by this session's fresh implementation.

### Completion Notes List

- **Backend (`redis_bus.py`/`routes/rankings.py`/`ws/live.py`/`app.py`):** `RankingsBus` is a plain class (module-level `bus = RankingsBus()` instance the app uses); `.handle_message()` validates non-dict payloads and dicts missing a list-shaped `ranks`, logging and skipping while keeping the previous good cache -- mirrors `dashboard.py:_handle_rankings_message` exactly. `.run()` reconnects forever with a 2s sleep between attempts, mirroring `dashboard.py:_redis_listener`. `routes/rankings.py` and `ws/live.py` both reference `redis_bus.bus` via the module (not a direct name import) specifically so tests can `monkeypatch.setattr(redis_bus, "bus", RankingsBus())` for isolated-cache tests without touching the real singleton. `GET /api/rankings` 503s before the first cached message; renames `ranks` -> `items`, passes `mode`/`stale_instrument_ids`/every ranking entry's fields through unchanged. `/ws/live` sends the cached `latest` message immediately on connect (if present) before waiting on its listener queue, then relays every subsequent message completely verbatim (`ranks` key untouched, no rename). `app.py`'s new `lifespan` context manager starts `bus.run(REDIS_URL)` as a background task on startup and cancels it on shutdown; both new routes are registered via `app.include_router(...)` immediately above the existing `/api/{full_path:path}` catch-all, per the required ordering constraint.
- **`docker-compose.yml`:** added `REDIS_URL: "redis://127.0.0.1:${REDIS_PORT:-6379}"` to the `data_api` service block, matching `collector`/`ranking_engine`/`bot_tui`/`live-paper`'s existing lines exactly. No other line in that block touched.
- **Frontend (`useLiveChannel.ts`/`RankingsPage.tsx`/`client.ts`):** `useLiveChannel<T>()` opens one WebSocket to `/ws/live`, exposes `{latest, connected}`, reconnects on close with linear backoff (1s per attempt, capped at 10s) -- never falls back to polling. `RankingsPage.tsx` seeds initial state via React Query's `useQuery(fetchRankings)` (`retry: false`, no `refetchInterval` -- no polling once mounted), then lets `useLiveChannel`'s ticks take over the moment the first one arrives; row order is rendered exactly as the message's `ranks`/`items` array, keyed by `instrument_id`. Two staleness signals are rendered distinctly per the Design Notes: a client-side 1s-ticked check of the whole message's `updated_at` against `RANKING_STALE_MS = 15_000` (dims the row, `data-stale="true"`, a `⏱` marker), and `stale_instrument_ids` membership (a separate `⚠` marker) -- both can fire independently and are visually distinguishable. Clicking a row calls `navigate(/chart/${instrument_id})`. The rendered metric columns are a hand-declared TS mirror of `ml_signals/ranking_columns.py`'s `RANKING_COLS` (13 entries, same order, same format semantics -- signed/fixed/percent/millions helpers reproducing each Python `fmt_fn` lambda) plus the structural `Rank`/`Instrument` columns; no column beyond that set.
- **Codegen:** regenerated `frontend/openapi.json` and `frontend/src/api/schema.ts` after adding `RankingsResponse` to `data_api`'s OpenAPI schema (`GET /api/rankings`) -- `client.ts`'s new `fetchRankings()` consumes the generated type, never a hand-written duplicate. `/ws/live`'s message shape (`RankingsLiveMessage`, keeping the raw `ranks` key) is hand-written in `RankingsPage.tsx` per AD-F5's explicit WS-frame exception, with a comment citing `ranking_engine/engine.py:605-687`'s `_build_rankings_message()` as its source of truth.
- **Tests:** `data_api/tests/test_rankings.py` -- 5 pure-logic tests against isolated `RankingsBus()` instances (non-dict payload, missing-list `ranks`, malformed-keeps-previous-cache, fan-out-to-listeners, unsubscribe-stops-fan-out), 2 route tests against an isolated bus via `monkeypatch` (503 before cache, items-rename-passthrough), and 1 real-Redis integration test publishing onto the actual `dydx-redis` container and asserting both `GET /api/rankings` and a live `/ws/live` connection reflect it. `frontend/src/pages/RankingsPage.test.tsx` -- 5 tests (loading state, stale-row marking, fresh-row-not-stale, click-to-navigate, message-order-verbatim-with-instrument_id-keys), `useLiveChannel` and `react-router`'s `useNavigate` both mocked.
- **Not built in this story (out of scope, per the intent-contract's Never list):** no per-instrument filtering/subscription options on `/ws/live` (AD-F2 leaves this open for a future story, not required here); no polling fallback anywhere in the frontend; no auth on `/ws/live` (SEC-01's `127.0.0.1` bind is the only access control, unchanged).

### File List

- New: `troll/data_api/redis_bus.py`
- New: `troll/data_api/routes/__init__.py`, `troll/data_api/routes/rankings.py`
- New: `troll/data_api/ws/__init__.py`, `troll/data_api/ws/live.py`
- New: `troll/data_api/tests/test_rankings.py`
- New: `troll/frontend/src/hooks/useLiveChannel.ts`
- New: `troll/frontend/src/pages/RankingsPage.test.tsx`
- Modified: `troll/data_api/app.py` (added `lifespan` starting/stopping `RankingsBus.run()`, wired `rankings`/`live` routers above the `/api/*` catch-all)
- Modified: `troll/docker-compose.yml` (`data_api` service's `environment` block only -- added `REDIS_URL`)
- Modified: `troll/frontend/src/pages/RankingsPage.tsx` (placeholder -> real live rankings table)
- Modified: `troll/frontend/src/api/client.ts` (added `fetchRankings()`)
- Modified: `troll/frontend/src/api/schema.ts`, `troll/frontend/openapi.json` (regenerated -- do not hand-edit)
- Not modified: `troll/data_api.dockerfile` (`COPY troll/data_api ./data_api` already copies the new `routes/`/`ws/` subpackages; `redis` is already pinned in `troll-requirements.txt`), `troll/collector.dockerfile`, `troll/ranking_engine/engine.py`, `troll/ml_signals/dashboard.py`, `troll/bot_tui/ranking_state.py`, `troll/ml_signals/ranking_columns.py` (all read as reference only, per the Code Map)

## Change Log

- 2026-09-14: Story created from Epic 15 / Story 15.2. Status: backlog -> ready-for-dev.
- 2026-09-14: Implemented. Backend: `redis_bus.RankingsBus` (shared cache + fan-out + reconnect-forever subscriber), `GET /api/rankings` (items-renamed passthrough, 503-before-cache), `/ws/live` (verbatim relay, immediate cached-message send on connect), `app.py` lifespan wiring both routes above the `/api/*` catch-all, `docker-compose.yml`'s `REDIS_URL`. Frontend: `useLiveChannel` WS hook (reconnect with backoff, no polling fallback), real `RankingsPage.tsx` (React Query seed + live WS ticks, message-order rendering keyed by `instrument_id`, two distinct staleness markers, click-to-chart navigation, hand-mirrored `RANKING_COLS`), `client.ts`'s `fetchRankings()` + regenerated OpenAPI types. Two real bugs found and fixed via actual test runs (not assumed): a missing RTL `cleanup()` causing cross-test DOM leakage, and an oxlint-flagged impure `Date.now()`-during-render call that was also a genuine staleness-detection gap (fixed with a 1s-ticked `now` state, not just silenced). Full regression green: `data_api/tests` 19/19, `data_api/tests ml_signals/tests` 233 passed with the same 1 pre-existing unrelated failure tracked since Story 14.3; frontend `npm run build`/`npm run test`/`npm run lint` all clean (lint: same 2 pre-existing Story 15.1 warnings only). Status: in-progress -> review.
- 2026-09-14 (review pass, Blind Hunter + Edge Case Hunter): found 5 patchable issues (1 medium: `redis_bus.py`'s validation gap could crash `GET /api/rankings` with a 500 on a partially-shaped cached message instead of the intended honest 503; 4 low: a narrow `/ws/live` message-loss race between subscribing and sending the initial cached message, an overly-narrow WS exception clause, a test hardcoding a Redis URL literal instead of reading the env-aware constant, and a silent exception-swallow in the frontend's cell formatter) — all fixed and reverified (`data_api/tests` 21/21, full regression 235 passed with the same 1 pre-existing unrelated failure, frontend build/test/lint clean). 4 lower-priority hardening items (unbounded `/ws/live` listener queues/count, unthrottled malformed-payload logging, no reconnect jitter, no explicit WS graceful-drain on shutdown) logged to `deferred-work.md`, not fixed this pass. 5 findings rejected as out-of-scope/already-conventional (no Redis-availability test guard matching existing repo convention, frontend's trust in `ranking_engine`'s SSOT-owned message shape, browser background-tab timer throttling, a test-only theoretical fragility). Status: in-review -> done.

## Auto Run Result

Status: done

**Summary:** Implemented Story 15.2 (Epic 15) end-to-end: `GET /api/rankings` and `/ws/live` on `data_api`, backed by one shared `redis_bus.RankingsBus` Redis subscriber, plus a real `RankingsPage.tsx` on the frontend replacing its Story-15.1 placeholder. A forensic note worth flagging: stale compiled `.pyc` bytecode for `redis_bus.py`/`routes/rankings.py`/`ws/live.py` (with no corresponding `.py` source) was found on disk at session start, timestamped ~17:58-18:00 the same day, referencing this exact story's design — evidence a prior attempt at this story ran and was lost (uncommitted, then wiped) before this session began. This session's implementation and spec were informed by that evidence but independently derived, verified, and reviewed.

**Files changed** (commit `46537de924`):
- `troll/data_api/redis_bus.py` (new) — shared Redis subscriber: cache + per-listener fan-out + reconnect-forever, validated payload shape
- `troll/data_api/routes/rankings.py` (new) — `GET /api/rankings`, `ranks`->`items` rename, 503 before first cached message
- `troll/data_api/ws/live.py` (new) — `/ws/live`, verbatim relay (no rename), immediate cached-message send on connect
- `troll/data_api/app.py` (modified) — `lifespan` starting/stopping the bus; both new routes wired above the `/api/*` catch-all
- `troll/docker-compose.yml` (modified) — added `REDIS_URL` to `data_api`'s service block
- `troll/frontend/src/hooks/useLiveChannel.ts` (new) — WS hook, reconnect-with-backoff, no polling fallback
- `troll/frontend/src/pages/RankingsPage.tsx` (modified) — real live table: message-order rendering, `instrument_id` keys, two distinct staleness markers, click-to-chart, hand-mirrored `RANKING_COLS`
- `troll/frontend/src/api/client.ts`, `schema.ts`, `openapi.json` (modified) — `fetchRankings()` + regenerated types
- `troll/data_api/tests/test_rankings.py` (new), `troll/frontend/src/pages/RankingsPage.test.tsx` (new) — 8 + 5 tests respectively
- `_bmad-output/implementation-artifacts/epic-15-context.md` (new) — compiled Epic 15 planning context
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified) — 4 new deferred hardening items

**Review findings breakdown:** 5 patched (1 medium, 4 low), 4 deferred (1 medium, 3 low, logged to `deferred-work.md`), 5 rejected (out-of-scope/already-conventional), 0 intent gaps, 0 bad-spec loopbacks.

**Verification performed:** `data_api/tests` 21/21 passed (real Redis integration test against the live `dydx-redis` container, no mocking). Full regression `data_api/tests ml_signals/tests` 235 passed, 1 pre-existing unrelated failure tracked since Story 14.3. Frontend `npm run build`/`npm run test`/`npm run lint` all clean (2 pre-existing Story 15.1 lint warnings only). All commands re-run and independently confirmed by the orchestrating session, not just trusted from the implementation subagent's report.

**Residual risks:** The 4 deferred hardening items (unbounded `/ws/live` listener queues/count, unthrottled malformed-payload logging, no reconnect jitter, no explicit WS graceful-drain on shutdown) are tracked in `deferred-work.md` but not fixed — all judged low-urgency given this service's actual message rate (~once per 5s heartbeat) and single-operator, localhost/SSH-tunnel-only deployment. `sprint-status.yaml` was not modified (orchestrator-owned).
