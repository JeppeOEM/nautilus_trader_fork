---
title: 'DW-283: one shared heartbeat-silence pub/sub receive helper'
type: 'refactor'
created: '2026-10-08'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
baseline_revision: '6fb55ec2abb6af0448eb522799f2e4bb29459f79'
final_revision: 'a536c29151bf1b619360e83a4641ee6868d53dc3'
---

<intent-contract>

## Intent

**Problem:** The heartbeat-silence `_receive` loop (poll `pubsub.get_message`, refresh a monotonic `heard` stamp on each `"message"`, raise `ConnectionError("no <channel> message for <N>s")` once silence passes a threshold) is copied verbatim in five subscribers across `views` and `bot_tui`, so a fix to one silently misses the others (DW-283).

**Approach:** Add one stdlib-only async helper in the `observability` context (importable by both `views` and `bot_tui`, which may not import each other) taking the pub/sub, channel name, silence threshold, ingest callback, poll timeout and an optional per-poll hook; each of the five `_receive` functions becomes a thin call to it, keeping its own constants and its own `run`/`_redis_listener` resubscribe loop. `bot_tui/markets_state.py`'s PING variant stays separate, documented as a deliberate exception.

## Boundaries & Constraints

**Always:** `ConnectionError` texts, log texts, poll timeouts (5.0 s) and thresholds stay byte-identical; each caller keeps its `_receive` name and signature (existing tests call them and monkeypatch `<module>.time.monotonic` and the threshold constants, which must still take effect — read the constant at call time, call `time.monotonic` through the `time` module); the helper imports only the standard library and holds no venue token (`tests/test_boundaries.py`); every function fully type-hinted.

**Block If:** a caller's behaviour cannot be expressed through the helper without changing an observable text or timing.

**Never:** no change to `run`/`_redis_listener` loops; no folding of `markets_state`'s PING logic into the helper; no conversion of `bots_state.py` (it uses `listen()`, outside this bundle's listed callers); no edit to the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Silence | `get_message` returns None every poll | Raises once `monotonic - heard > silence_seconds`, never earlier | `ConnectionError("no <channel> message for <silence:.0f>s")` |
| Message | a `{"type": "message", "data": d}` | `ingest(d)` called, `heard` refreshed | none |
| Non-message frame | e.g. `{"type": "pong"}` | not ingested, `heard` not refreshed | silence check applies |
| Per-poll hook | `after_poll` given | called once after every poll, before the silence check | none |

</intent-contract>

## Code Map

- `platform/observability/pubsub_liveness.py` -- NEW: the helper `receive_until_silent` + a `MessagePoller` Protocol for `get_message`.
- `platform/observability/__init__.py` -- context docstring lists its modules; add the helper.
- `platform/views/archive_status_bus.py:173` / `views/markets_bus.py:250` / `views/rankings_bus.py:180` -- three method copies; rankings runs `_flush_suppressed_malformed()` every poll (the `after_poll` hook).
- `platform/bot_tui/archive_state.py:80` / `bot_tui/collector_state.py:351` -- two module-function copies.
- `platform/bot_tui/markets_state.py:135` -- PING variant; any frame (pong included) resets liveness and silence triggers a PING, not a raise.
- `platform/views/tests/test_rankings_bus_hardening.py`, `bot_tui/tests/test_collector_state_silence.py`, `views/tests/test_markets_bus.py` -- existing caller tests that must keep passing unchanged.

## Tasks & Acceptance

**Execution:**
- [x] `platform/observability/pubsub_liveness.py` -- create `async def receive_until_silent(pubsub, channel, silence_seconds, ingest, *, poll_seconds, after_poll=None) -> NoReturn` with the loop semantics above; module docstring names the invariant (a half-open connection never errors, so silence is the only liveness signal) and names `markets_state`'s PING variant as the deliberate exception -- the one implementation.
- [x] `platform/observability/__init__.py` -- mention the pub/sub silence helper in the docstring.
- [x] `platform/views/archive_status_bus.py`, `platform/views/markets_bus.py`, `platform/views/rankings_bus.py` -- replace each `_receive` body with one call to the helper (rankings passes `after_poll=self._flush_suppressed_malformed`); keep docstrings' why.
- [x] `platform/bot_tui/archive_state.py`, `platform/bot_tui/collector_state.py` -- same; `archive_state` gets a `_POLL_SECONDS: float = 5.0` constant in place of the literal.
- [x] `platform/bot_tui/markets_state.py` -- `_receive` docstring states why it does not use the shared helper (legitimate channel-wide silence; PING probe instead of raising).
- [x] `platform/observability/tests/test_pubsub_liveness.py` -- unit tests for every I/O matrix row, importing only `observability`, using a fake poller and a monkeypatched `time.monotonic`.

**Acceptance Criteria:**
- Given the refactor, when `grep -n "heard = time.monotonic()" platform/views platform/bot_tui` runs, then only `bot_tui/markets_state.py` matches.
- Given the full `make test` path set for views, bot_tui, data_api, observability and tests, when run, then everything passes with no new warnings, `tests/test_boundaries.py` included.

## Verification

**Commands:**
- `cd platform && python3 -m pytest observability/tests views/tests bot_tui/tests data_api/tests tests/test_boundaries.py -q` -- expected: all pass, no new warnings
- `cd platform && ruff check <touched files> && ruff format --check <touched files> && mypy <touched files>` -- expected: clean

## Spec Change Log

## Review Triage Log

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 0, low 5)
- defer: 1: (high 0, medium 0, low 1)
- reject: 12: (high 0, medium 0, low 12)
- addressed_findings:
  - `[low]` `[patch]` The generic `observability` docstring named a private `bot_tui` function. It now describes the PING exception generically, and `markets_state._receive` keeps its own note.
  - `[low]` `[patch]` The helper's contract didn't state its preconditions (`decode_responses=True`, and `subscribe` rather than `psubscribe`). Both are now documented in `receive_until_silent`'s docstring.
  - `[low]` `[patch]` The docstring gave one reason for reading the clock through `time` where there are two: it is monotonic, and tests can patch it. Those are now separate clauses.
  - `[low]` `[patch]` Nothing tested a message arriving after the threshold. Added `test_a_message_after_the_threshold_is_ingested_not_raised_on`.
  - `[low]` `[patch]` The wiring of `bot_tui.archive_state._receive` and `ArchiveStatusBus._receive` had no tests. Each caller's test file now has a test that silence raises an error naming the channel.


### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 2: (high 0, medium 0, low 2)
- reject: 17: (high 0, medium 0, low 17)
- addressed_findings:
  - none

## Auto Run Result

**Summary:** This was a follow-up review of the finished DW-283 refactor, and it found nothing to fix in this story's code. The heartbeat-silence receive loop exists once, as `observability.pubsub_liveness.receive_until_silent`, which uses only the standard library. Five callers delegate to it: `views/{archive_status_bus,markets_bus,rankings_bus}.py` and `bot_tui/{archive_state,collector_state}.py`. Each keeps its own `_receive` name, constants, texts and resubscribe loop. `bot_tui/markets_state.py` stays on its PING variant and documents why.

**Files changed (unchanged by this pass, from `dda0c573c9`):** see the Code Map. This pass changed only this spec and appended two entries to the deferred-work ledger.

**Review (follow-up pass):** Blind Hunter and Edge Case Hunter ran on the diff from `6fb55ec2ab` to `dda0c573c9`.
- **Patched:** 0.
- **Deferred (appended as new entries at the end of `deferred-work.md`; no existing entry was touched):**
  - The two `markets:live` subscribers disagree about whether that channel can be legitimately silent. `views/markets_bus.py` raises after 180 s of silence; `bot_tui/markets_state.py` sends a PING instead. This predates DW-283, which kept behaviour byte-identical.
  - The PING-after-silence loop is still copied four times: `archive/infrastructure/redis_bus.py`, `collection_control/infrastructure/redis.py`, `bots/infrastructure/liquidation_data_client.py` and `bot_tui/markets_state.py`.
- **Rejected:**
  - Changes that would break limits the intent contract sets:
    - the `:.0f` error formatting (the texts are frozen);
    - callers' `-> None` versus `NoReturn` (the signatures are frozen);
    - the `markets_state` 5.0 literal (out of scope).
  - Argument validation for `poll_seconds` and `silence_seconds`, plus guards against a missing `type` key. Every caller passes module constants, and redis-py always sets `type`.
  - The exception contract for `ingest` and `after_poll`. Behaviour is unchanged, and each caller's `run` catches the error and resubscribes.
  - The `rankings_bus` `after_poll` wiring being untested. That claim is wrong: `test_rankings_bus_hardening.py::test_suppressed_tail_is_flushed_once_its_window_closes` drives `_receive`.
  - The `bots_state` `listen()` loop. It is already tracked as an existing ledger entry and is in the intent's Never list.
  - Weak caller-test, test-clock and docstring-duplication remarks (cosmetic).

**Verification:**
- `python3 -m pytest observability/tests views/tests bot_tui/tests data_api/tests tests/test_boundaries.py -q` with a throwaway Redis on 6379: 1944 passed, no warnings.
- `grep -rn "heard = time.monotonic()" views bot_tui` matches only `bot_tui/markets_state.py:147`.

**Residual risk:** low. The `markets:live` silence discrepancy is real but predates this change and is now tracked.
