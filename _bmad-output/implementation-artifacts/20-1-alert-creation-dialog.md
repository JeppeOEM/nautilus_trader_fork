# Story 20.1: Alert creation dialog

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a chart user,
I want to define an alert condition, frequency, expiration, message template, and webhook URL,
so that I can be notified when a price or drawing-line condition is met.

## Acceptance Criteria

1. **This is Epic 20 — deliberately built last, after Epics 17-19 ship.** Do not start this story until those are done, per the user's explicit deferral.
2. **A clock/bell icon opens a "Create Alert" dialog** with a condition builder supporting, for MVP: price crossing a static value, and price crossing a horizontal-line drawing (Story 18.1's `priceLines`).
3. **Frequency options: Once per bar close / Once per bar / Only once.**
4. **An expiration setting (a date, or "never").**
5. **A message template textarea** supporting `{{ticker}}`, `{{close}}`, `{{time}}`, `{{interval}}` placeholders, string-substituted at fire time (Story 20.2).
6. **A Webhook URL field** — plain text input, no validation beyond "looks like a URL." No Telegram bot, no chat-id lookup, no Discord/Slack/email/SMS delivery — this project only ever produces a generic webhook POST (Story 20.2); anything downstream of that is the user's own infrastructure.
7. **Saved alerts persist** (a new small store — see Dev Notes) with all the above fields.

## Tasks / Subtasks

- [x] Task 1 — Dialog UI (AC: #2-6)
  - [x] A new dialog component with the condition builder, frequency/expiration/template/webhook-URL fields described above.
  - [x] Condition builder reads available price-crossing targets: a static value (user-entered) or any currently-placed `priceLines` entry (Story 18.1) on the active chart.

- [x] Task 2 — Persistence (AC: #7)
  - [x] A new small backend resource for saved alerts (per-instrument or global — decide based on whether alerts should follow "one coin's chart" scope or a user-wide list; the original spec's Alerts list view (Story 20.3) suggests a user-wide list is the simpler, correct scope, matching this project's single-operator posture). Model it after the existing config-persistence pattern (`chart_indicator_config.py`'s TOML load/save shape) rather than inventing a new persistence mechanism.
  - [x] New `data_api` routes for create/list/delete (`GET/POST/DELETE /api/alerts` or similar) — the write path is a sanctioned AD-F2 exception (config persistence), same category as `PUT /api/coin/{iid}/indicators`.

- [x] Task 3 — Tests
  - [x] A round-trip test for the alert-persistence store (create, list, delete) against a temp file, real objects, no mocking.

## Dev Notes

- **Do not build any part of Epic 20 before Epics 17-19 are complete** — this is a hard sequencing rule from the user, not a soft preference.
- **No Telegram/Discord/Slack/email/SMS integration anywhere in this epic** — the Webhook URL field is the entire delivery mechanism; what a user points it at is out of scope, matching how TradingView itself has no native Telegram integration.

### Project Structure Notes

- New: an alert-persistence module (TOML/JSON, mirroring `chart_indicator_config.py`'s shape), new `data_api` routes for alerts CRUD, a new dialog component in `troll/frontend/src/components/chart/`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 20, Story 20.1] — this story's origin (FR67).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#A6] — full alert dialog spec, the "no native Telegram integration" reality check.
- [Source: troll/ml_signals/chart_indicator_config.py] — the config-persistence pattern (TOML, full-rewrite) this story's alert store mirrors.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

Implemented: `data_api/alerts.py` (TOML `AlertStore`, mirrors chart_indicator_config), `data_api/routes/alerts.py` (GET/POST/DELETE /api/alerts, pydantic validation: http(s) URL, finite level, bar_seconds 1..86400, frequency enum), `AlertDialog.tsx` + an "Alert" toolbar button on ChartPage. Horizontal-line conditions resolve to the line's price at creation (line state is client-side), so an alert always stores a static `level`. Global (user-wide) list scope. Compose mounts `data_api/alerts.toml` rw. Tests: `test_alerts.py` (store round-trip, routes, validation), `AlertDialog.test.tsx`.

### File List

- troll/data_api/alerts.py
- troll/data_api/alerts.toml
- troll/data_api/routes/alerts.py
- troll/data_api/app.py
- troll/data_api/tests/test_alerts.py
- troll/docker-compose.yml
- troll/frontend/src/components/chart/AlertDialog.tsx
- troll/frontend/src/components/chart/AlertDialog.test.tsx
- troll/frontend/src/pages/ChartPage.tsx
- troll/frontend/src/pages/ChartPage.test.tsx
- troll/frontend/src/api/client.ts
- troll/frontend/src/api/schema.ts
- troll/frontend/openapi.json

### Review Findings

Code review 2026-09-19 (adversarial + edge-case + acceptance layers, run inline over `git diff 14a459ccd6..HEAD`). 0 decision-needed, 3 patch (all applied), 3 defer, rest dismissed.

- [x] [Review][Defer] A horizontal-line condition is resolved to a static price at creation; dragging the line afterwards does not move the alert — deferred, by design (line state is client-side chart state; spec AC allows it)
- [x] [Review][Defer] `AlertStore` rewrites its TOML in place (bind-mounted single file can't be os.replace'd); a crash mid-write leaves a corrupt file that fails loudly on next load — deferred, same accepted trade-off as chart_indicator_config
