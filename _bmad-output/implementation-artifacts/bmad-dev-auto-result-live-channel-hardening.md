---
status: done
review_loop_iteration: 0
followup_review_recommended: false
final_revision: 0e09188033
---

# BMad Dev Auto Result

Status: done
Blocking condition: none

Bundle live-channel-hardening: DW-3 (throttled malformed warning + counter in views/rankings_bus.py),
DW-4 (jittered reconnect backoff in useLiveChannel.ts), DW-234 (heartbeat-silence resubscribe in
views/rankings_bus.py and bot_tui/collector_state.py). Tests: views + bot_tui pytest (675 passed),
vitest useLiveChannel.test.ts (2 passed).

Residual: DW-234 also names bot_tui/bots_state.py and bot_tui/bot_history_state.py, which are
outside the bundle intent and remain unchanged; the ledger entry should stay open for them.

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 2, low 4)
- defer: 1: (high 0, medium 0, low 1)
- reject: 14: (high 0, medium 0, low 14)
- addressed_findings:
  - `[medium]` `[patch]` Suppressed malformed `rankings:live` warnings were never logged when a burst ended inside the 60 s window: added `RankingsBus._flush_suppressed_malformed()`, called on every `_receive` poll, plus a test.
  - `[medium]` `[patch]` Silence tests advanced the clock per `monotonic()` call, so they checked call counts, not elapsed time, and never showed that a message restarts the window: rewrote both suites with a fake pubsub that waits out each poll timeout on a fake clock; added keep-alive and window-boundary assertions.
  - `[low]` `[patch]` "N similar suppressed" mislabelled mixed parse/shape failures: now "N more malformed suppressed".
  - `[low]` `[patch]` `SILENCE_RESUBSCRIBE_SECONDS` is a constant not derived from `RANKING_HEARTBEAT_SECONDS`: documented as a `Known limit:` with the upgrade path.
  - `[low]` `[patch]` `collector:status` resubscribe window equals the 1 h stale window (slow detection; coupled to `liquidity_check_seconds`): documented as a `Known limit:` with the upgrade path (a short-cadence status heartbeat).
  - `[low]` `[patch]` `test_ingest_swallows_a_malformed_message` asserted nothing, and `test_unparseable_message_is_counted_not_fatal` didn't show ingestion continuing: both now assert the logged warning or the cached payload.

## Auto Run Result

Follow-up review pass (status was `done`, re-reviewed from the uncommitted working tree; no `baseline_revision`, so the diff was taken against HEAD).

Summary: Bundle live-channel-hardening adds a rate-limited malformed-payload warning and a running counter (DW-3), jittered reconnect backoff in the web live channel (DW-4), and a heartbeat-silence resubscribe for `rankings:live` and `collector:status` (DW-234, partial).

Files changed:
- `platform/views/rankings_bus.py`: `_note_malformed` throttle + `malformed_count`, suppressed-tail flush, `_receive` silence watchdog, `_ingest`.
- `platform/bot_tui/collector_state.py`: `_receive` silence watchdog + `_ingest`, with a `Known limit:` note.
- `platform/frontend/src/hooks/useLiveChannel.ts`: `reconnectDelayMs`, linear backoff with 50-100% jitter.
- `platform/views/tests/test_rankings_bus_hardening.py`, `platform/bot_tui/tests/test_collector_state_silence.py`, `platform/frontend/src/hooks/useLiveChannel.test.ts`: tests.
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new appended entry (shared `_receive` helper). Existing entries were not touched; the DW-3/DW-4/DW-234 status edits already in the working tree are the orchestrator's.

Review findings: 6 patches applied, 1 deferred, 14 rejected. The rejected findings were pre-existing behaviour (logging the payload repr, the broad `except` in `collector_state._ingest`, pubsub teardown through the client context manager), by-design channel-wide liveness, and speculative inputs to `reconnectDelayMs`. A redis `health_check_interval` was also suggested as a replacement; it was rejected because a PING written to a half-open socket raises nothing either.

Verification: `python3 -m pytest views/tests bot_tui/tests`: 678 passed. `npx vitest run src/hooks/useLiveChannel.test.ts`: 2 passed. `ruff format --check` and `ruff check` are clean on the touched Python files, and `mypy` reports no issues.

Residual risks: DW-234 also names `bot_tui/bots_state.py` and `bot_tui/bot_history_state.py`, which are still on a bare `listen()`. The orchestrator marked DW-234 done, but the work is only partly done. A `collector:status` half-open connection is still detected only after 1 h (`Known limit:`).
