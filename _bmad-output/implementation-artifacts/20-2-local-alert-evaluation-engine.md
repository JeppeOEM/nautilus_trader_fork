# Story 20.2: Local alert evaluation engine

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want my saved alerts evaluated automatically against live data,
so that I don't have to watch the chart myself.

## Acceptance Criteria

1. **Built after Epics 17-19, and after Story 20.1** — same deferral rule.
2. **The alert engine is a consumer of `data_api`'s existing live Redis feed** (`troll/data_api/redis_bus.py`'s `RankingsBus`, `ws/live.py`'s multiplexed relay) — not a second, independent polling loop against the catalog or a new WS subscription path.
3. **A condition firing respects its configured frequency** (once per bar close / once per bar / only once) and expiration — an expired or already-fired "only once" alert never fires again.
4. **On fire: a `fetch(POST)`/server-side HTTP POST to the alert's Webhook URL with the templated JSON body** (`{{ticker}}`/`{{close}}`/`{{time}}`/`{{interval}}` substituted), plus an in-app toast/notification.
5. **A failed webhook POST is logged, not retried indefinitely and not silently swallowed** (DATA-02 spirit: a failure should be visible, not invisible).

## Tasks / Subtasks

- [x] Task 1 — Engine wiring (AC: #2)
  - [x] Decide (and document the decision, not leave it implicit) whether evaluation runs server-side in `data_api` (subscribing to the same Redis channels `ws/live.py` already relays) or client-side in the browser (subscribing to `/ws/live` like any other consumer). Server-side is more consistent with "the webhook fires even if no browser tab is open," which is almost certainly the more useful behavior for an alert — recommended default unless there's a reason to prefer client-side.
  - [x] Whichever side, reuse the existing live-data subscription surface — do not add a second Redis subscription or a new polling loop against the catalog.

- [x] Task 2 — Condition evaluation + frequency/expiration state (AC: #3)
  - [x] On each relevant incoming tick/bar-close, evaluate every active alert's condition (Story 20.1's stored conditions); track last-fired state per alert to enforce frequency; check expiration before evaluating at all.

- [x] Task 3 — Fire: webhook POST + template substitution + toast (AC: #4, #5)
  - [x] Template substitution is a plain string-replace over the four documented placeholders — no templating library needed for four fixed tokens (DESIGN-01).
  - [x] POST failures are logged with the alert id and reason; no retry loop (a webhook endpoint that's down stays down until the next natural fire opportunity, per the alert's own frequency — not a reason to build retry/backoff machinery for a personal single-user tool).

- [x] Task 4 — Tests
  - [x] A test for the frequency/expiration state machine (once-per-bar-close vs once-per-bar vs only-once, and an expired alert never firing) using a pure function/class, independent of the actual network POST (mirrors `_watchdog_transition`'s pattern in `dydx_collector/collector.py` — a pure state-machine step, unit-testable without mocking network calls).
  - [x] A test for template substitution given known placeholder values.

## Dev Notes

- **Reuse discipline is the main point of this story** — `troll/data_api` already runs a live Redis-subscriber loop; this engine is a new consumer of that, not a new data path. Check `redis_bus.py`/`ws/live.py` before writing any new subscription code.
- **No multi-condition AND/OR logic, no alert templates library** — explicitly out of scope per the original spec.

### Project Structure Notes

- New: an alert-evaluation module in `data_api` (exact placement depends on Task 1's server-vs-client decision).
- Not modified: `troll/data_api/redis_bus.py`, `ws/live.py` (consumed, not changed, unless Task 1's server-side choice needs a new subscription entry point added to one of them).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 20, Story 20.2] — this story's origin (FR68).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A6] — the local alert engine spec (evaluate on every new bar/tick, webhook POST + toast).
- [Source: troll/data_api/redis_bus.py, ws/live.py] — grepped this session; `RankingsBus`/the multiplexed relay this story's engine consumes.
- [Source: troll/dydx_collector/collector.py `_watchdog_transition`] — read this session (Story 19.3 research); the pure-state-machine testing pattern this story's frequency/expiration logic follows.
- [Source: _bmad-output/implementation-artifacts/20-1-alert-creation-dialog.md] — the persisted alert shape this engine evaluates.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

Decision: evaluation is server-side (webhook fires with no tab open). `AlertEngine.on_snapshot` is registered as an observer on the existing `LiveCandleBus` (`observers` list added in live_candles.py) -- no second Redis subscription. Pure `evaluate()` state machine (cross up/down, once_per_bar_close decided on bucket rollover, once_per_bar, only_once, expiry) and `render()`; webhook POST via stdlib urllib in a daemon thread, failure logged with alert id, no retry. Toast pushed over `/ws/live` (`{"channel":"alerts",...}`) and shown by `useAlertToasts` + `AlertToasts` in App. Known ceiling: run state is in-memory, so the first tick after a restart can't fire.

### File List

- troll/data_api/alerts.py
- troll/data_api/live_candles.py
- troll/data_api/ws/live.py
- troll/data_api/app.py
- troll/data_api/tests/test_alerts.py
- troll/frontend/src/hooks/useAlertToasts.ts
- troll/frontend/src/App.tsx
