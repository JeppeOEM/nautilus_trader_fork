---
baseline_commit: 944891bbbafa9a2869d7b988910b478dcf647576
---

# Story 5.2: Per-level message-id uncrossing for crossed order books

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- No epics.md entry exists for this story -- standalone fix story, same precedent as epic-5/6/7
     (see sprint-status.yaml comments), created directly via create-story at the user's request. This
     is the fix Story 5.1's investigation (troll/.planning/debug/crossed-book-root-cause.md) and this
     session's technical research (see References) point to. -->

## Story

As a maintainer of the dYdX collector's data-integrity gate,
I want the collector to resolve a crossed order book the same way dYdX's own Indexer does — by dropping only the stale price level, using each level's last-touching message-id to decide which side is stale — instead of forcing a full unsubscribe/resubscribe,
so that the collector stops performing a destructive, whole-book resync (`troll/CLAUDE.md` DATA-03) for what is, per DATA-04, mostly an expected, architectural property of dYdX v4's decentralized orderbook, not a local pipeline bug.

## Acceptance Criteria

1. **Per-level tagging.** Given `_apply_deltas` applies a delta to the live book, when the delta is an ADD/UPDATE (i.e. `delta.is_add or delta.is_update`), then `Collector` records the delta's `sequence` (dYdX's connection-global `message_id`) keyed by `(iid, delta.order.side, delta.order.price.as_double())` in a new `self._level_msg_id: dict[str, dict[tuple[OrderSide, float], int]]`; when the delta `is_delete` (or `is_clear`, which clears the whole side/book), the corresponding tag(s) are removed instead.
2. **Active uncrossing before resync.** Given `_handle_crossed_book` detects `best_bid >= best_ask`, when both the best bid's and best ask's price levels have a recorded tag in `_level_msg_id[iid]`, then it removes the level with the strictly older (smaller) `sequence` via a synthetic `OrderBookDelta(action=BookAction.DELETE, ...)` applied through `book.apply_delta()` — mirroring exactly how a real dYdX-sent deletion is already applied elsewhere in this file — and repeats while still crossed, up to a small fixed iteration cap (e.g. 5) as a defensive bound against a malformed/pathological book.
3. **Tie-break matches dYdX's own algorithm.** Given the best bid and best ask levels have the *same* `sequence` (a tie), when uncrossing runs, then the side with the smaller size is treated as stale and removed (ties broken by size, per `uncross-orderbook.ts`'s own tie-break — see Dev Notes for the exact reference logic).
4. **Resync remains the fallback, not the first response.** Given `_handle_crossed_book` detects a crossed book where either side's level is *not* found in `_level_msg_id[iid]` (e.g. immediately after a `_resync_book` call, before any tags have been (re)populated) — or the book is still crossed after the iteration cap in AC #2 is exhausted — then the existing `_CROSSED_RESYNC_NS` timer / CRITICAL escalation / `_resync_book` path fires exactly as it does today, unchanged.
5. **Successful active uncrossing is observable and does not escalate.** Given active uncrossing (AC #2) fully resolves the crossing before `_CROSSED_RESYNC_NS` elapses, then no CRITICAL log/incident report fires (that log line is reserved for the resync fallback, per existing `_classify_incident` behavior) and a new INFO-level log line records that the book was actively uncrossed (instrument, dropped side, dropped price, both sequence numbers) — distinct wording from both the existing "resolved after Xs" (passive self-heal) and "Resyncing desynced order book" (forced fallback) lines, so all three outcomes are distinguishable in logs/incident classification without changing `_classify_incident`'s existing patterns for the other two.
6. **No regressions.** Given `python3 -m pytest dydx_collector/tests -q` (see Dev Notes for how to run without a full env), when run after this change, then all existing tests pass unchanged, plus new tests for AC #1–5 (see Testing below).

## Tasks / Subtasks

- [x] Task 1 — Per-level message-id tagging (AC: #1)
  - [x] Add `self._level_msg_id: dict[str, dict[tuple[OrderSide, float], int]] = {}` to `Collector.__init__` near the other crossed-book state (`_crossed_since_ns`, `_crossed_prices`, `collector.py:419-422`)
  - [x] In `_apply_deltas` (`collector.py:493-522`), inside the existing `for delta in data.deltas:` loop, after `book.apply_delta(delta)`: on `delta.is_clear`, clear `self._level_msg_id[iid]` entirely (or pop the dict) — a Clear wipes the whole book, so all prior tags are stale by definition; otherwise on `delta.is_delete`, pop `(delta.order.side, delta.order.price.as_double())`; otherwise (add/update) set that key to `delta.sequence`
- [x] Task 2 — Active uncrossing algorithm (AC: #2, #3)
  - [x] Add a helper (e.g. `_uncross_step(self, iid: str, book: OrderBook) -> bool`) implementing one correction step: read `book.best_bid_price()`/`book.best_ask_price()`, return `False` if not crossed; look up both levels' tags in `self._level_msg_id.get(iid, {})`, return `False` if either is missing (AC #4's fallback trigger); otherwise pick the stale side (older `sequence`, tie -> smaller size per AC #3 — read the actual resting `Quantity` for both sides off the `book` to compare, not a hardcoded value), apply a synthetic `BookAction.DELETE` for that side/price via `book.apply_delta(...)`, pop its tag, return `True`
  - [x] In `_handle_crossed_book` (`collector.py:795`), before the existing crossed-book WARNING/timer logic, loop `_uncross_step` up to a small fixed cap (e.g. `for _ in range(5): if not self._uncross_step(iid, book): break`), then re-check crossed state — if now uncrossed, log the new AC #5 INFO line and return `False` (uncrossed, caller proceeds with a normal snapshot this tick) without touching `_crossed_since_ns`/timer state
  - [x] If still crossed after the loop (missing tags or cap exhausted), fall through to the existing WARNING/`_CROSSED_RESYNC_NS`/CRITICAL/`_resync_book` logic completely unchanged (AC #4)
- [x] Task 3 — Tests (AC: #1-#6)
  - [x] `test_apply_deltas_tags_and_untags_price_levels` — apply an ADD delta with a known `sequence`, assert `_level_msg_id[iid][(side, price)] == sequence`; apply a DELETE at that price, assert the key is gone; apply a Clear delta (or call the book's clear path), assert `_level_msg_id[iid]` is empty
  - [x] `test_crossed_book_actively_uncrossed_when_both_levels_tagged` — build a crossed book (reuse/adapt `_crossed_book()` from `test_collector_resilience.py`, but apply the two ADD deltas through `collector._apply_deltas` instead of directly via `book.apply_delta`, with *different* `sequence` values per side so tagging is meaningful) with the ask's `sequence` newer than the bid's; call `_handle_crossed_book`; assert the book is no longer crossed, the stale (bid) level was removed, no CRITICAL log fired, and `fake_client.calls == []` (no resync)
  - [x] `test_crossed_book_tie_break_uses_smaller_size` — same as above but equal `sequence` on both sides, differing sizes; assert the smaller-size side is the one removed
  - [x] `test_uncross_step_falls_back_when_a_level_is_untagged` (renamed from the story's originally-suggested name during implementation — see Completion Notes) — a crossed book where `_level_msg_id[iid]` has no entry for either level (simulating "right after `_resync_book`, before deltas repopulate tags"); asserts `_uncross_step` returns `False` immediately and leaves the book/tags untouched; existing resync-path tests (`test_crossed_book_within_grace_window_does_not_escalate`, `test_crossed_book_past_grace_window_escalates_critical`) verified to still pass unmodified — confirms this story is additive, not a replacement
  - [x] Ran `python3 -m pytest dydx_collector/tests -q` directly (host Python has `nautilus_trader` importable, so `make test`'s Docker requirement wasn't actually needed) and confirmed the full suite passes: 104 passed (100 pre-existing + 4 new)

## Dev Notes

- **This is additive, not a rewrite.** `_resync_book`, `_CROSSED_RESYNC_NS`, `_crossed_since_ns`, `_crossed_prices`, and the existing passive self-heal / CRITICAL escalation paths in `_handle_crossed_book` (`collector.py:795-869`) stay exactly as they are today — this story inserts an earlier, cheaper resolution attempt *before* that existing logic, per DATA-03/DATA-04 in `troll/CLAUDE.md`. Do not delete or restructure the existing fallback machinery.
- **Why this is safe to trust `sequence` for, when the earlier per-instrument use of it wasn't:** commit `944891bbba` correctly removed per-*instrument* sequence-gap detection because `OrderBookDelta.sequence` is a WS-connection-global counter shared by every market/channel — comparing consecutive values *for one instrument* false-positives on any other channel's interleaved traffic. This story uses the *same field* for a different, valid purpose: as a monotonic "which of these two specific price levels was touched more recently" comparator, exactly as dYdX's own Indexer does (see below) — not as a per-instrument gap detector. There is no contradiction between the two.
- **The exact reference implementation (dYdX's own `Roundtable` `uncross-orderbook.ts`, from `dydxprotocol/v4-chain`), confirming both the algorithm and the tie-break in AC #3:**
  ```typescript
  // Bids sorted descending, asks sorted ascending
  while (ai < asks.length && bi < bids.length && bids[bi].price >= asks[ai].price) {
    if (Number(bids[bi].lastUpdated) > Number(asks[ai].lastUpdated)) {
      ai += 1;   // ask is newer -> the bid is stale, drop it
    } else {
      bi += 1;   // bid is newer OR TIE -> the ask is stale, drop it
    }
  }
  ```
  Note the tie-break as literally written (`>` not `>=`) makes the *ask* stale on a tie by that exact code — but dYdX's own docs describe comparing size on a tie (see below), and this story's AC #3 follows the docs' documented behavior (size-based tie-break), which is more correct for our purposes than blindly porting a `>`-vs-`>=` quirk from one specific implementation. Use judgement here: implement the size-based tie-break from the docs; if in doubt, the `Roundtable` source is the fallback authority since it's what's actually running in production.
- **dYdX's own docs on the mechanism** (`docs.dydx.exchange/api_integration-guides/how_to_uncross_orderbook`): "v4 doesn't guarantee that order book prices don't cross because there is no centralized order book... If trader needs the order book uncrossed... use the order of messages as a logical timestamp... Each websocket update has a message-id which is a logical offset to use." This is a client-side/indexer-side derived annotation (confirmed empirically this session: zero 3-element `[price, size, offset]` arrays appear on the actual wire in this collector's raw `[WS_RAW]` captures — the "third element" the docs describe is something the *consumer* attaches, using the top-level per-message `message_id`, not something dYdX transmits per level).
- **Nautilus API used, already verified to exist in this repo:** `nautilus_trader.model.enums.BookAction.DELETE` and `OrderBook.apply_delta()` — the exact same call already used for every real dYdX-sent deletion in `_apply_deltas`. No `crates/` change, no new dependency (FORK-01/FORK-02 compliant).
- **`OrderBookDelta` fields actually available** (verified via `python3 -c "from nautilus_trader.model.data import OrderBookDelta; print(dir(OrderBookDelta))"` in this repo's env): `.action`, `.order` (a `BookOrder` with `.side`, `.price`, `.size`), `.sequence`, `.is_add`, `.is_update`, `.is_delete`, `.is_clear`, `.instrument_id`, `.flags`, `.ts_event`, `.ts_init`. Use `.is_add`/`.is_update`/`.is_delete`/`.is_clear` rather than comparing `.action` to enum values directly — matches the accessor style already used elsewhere in this file (e.g. `_apply_deltas` already uses `delta.is_clear`).
- **Log line wording (AC #5):** keep it visually distinct from the two existing crossed-book log lines in `_handle_crossed_book` (`"Crossed book for %s resolved after %.2fs"` for passive self-heal, `"Resyncing desynced order book for %s"` for the forced fallback) — something like `"Crossed book for %s actively uncrossed: dropped stale %s @ %.6f (seq %d < %d)"` at INFO level. Do **not** add a new branch to `_classify_incident` (`collector.py:1062`) for this — it already falls to `"unclassified"` for anything not matching `"Crossed book"`/`"Stale book"`/`"Resyncing"`/the JSON-`reason` path; an unmatched INFO line never reaches the incident-report machinery anyway since that's driven by the logging `Handler` on WARNING+ (`_IncidentHandler`, `collector.py:1185`) — this new line should stay INFO, same level as the existing "resolved after Xs" line, so it's visible in logs/Dozzle but doesn't spam `incident_reports/`.
- **Testing conventions** (`troll/CLAUDE.md` TEST-01/TEST-03): use real `OrderBook`/`Price`/`Quantity`/`OrderBookDelta`/`BookOrder` objects exactly as `test_collector_resilience.py`'s existing `_crossed_book()`/`_uncrossed_book()` helpers do (`test_collector_resilience.py:210-261`) — do not mock Nautilus internals. The existing helpers apply deltas directly via `book.apply_delta(...)`, bypassing `Collector._apply_deltas` entirely (so they never populate the new `_level_msg_id` dict) — the new tests need a variant that routes through `collector._apply_deltas(iid, OrderBookDeltas(...))` instead, so tagging actually happens, with deliberately different `sequence` values per side (the existing helpers hardcode `sequence=1` for both, which can't exercise this story's logic).
- **`_FakeClient` and `_make_config`** (`test_collector_resilience.py:53-` and the `_FakeClient` class used throughout that file) are the established fixtures for constructing a `Collector` in tests without a live connection/catalog — reuse them, don't build new ones.
- **Running tests:** per this repo's own tooling, `nautilus_trader` is only installed inside the collector's Docker image (`troll/Makefile`'s `test` target: `make test` from `troll/`, which runs `docker compose run --rm --no-deps ... collector python3 -m pytest dydx_collector/tests ...`). If a bare `python3 -m pytest` is attempted outside that image and `nautilus_trader`/`dydx_collector` aren't importable, use `make test` from `troll/` instead of trying to fix the host environment.

### Project Structure Notes

- All changes are confined to `troll/dydx_collector/collector.py` and `troll/dydx_collector/tests/test_collector_resilience.py` — no new files needed, consistent with DESIGN-01 (YAGNI) and this being a small, additive, self-contained fix.
- Do not touch `crates/adapters/dydx/` — nothing here requires a wire-format or parser change; the `message_id`/`sequence` field this story uses is already fully parsed and exposed today.

### References

- [Source: troll/CLAUDE.md#Data Integrity, DATA-03, DATA-04] — the standing rules this story implements: forced resync is destructive/worst-case (DATA-03); crossing is architectural/expected and dYdX's own resolution mechanism is the correct one to port (DATA-04)
- [Source: _bmad-output/planning-artifacts/research/technical-dydx-v4-orderbook-crossing-resolution-research-2026-09-06.md] — full research: dYdX architecture, the official uncrossing guide, the `Roundtable` source code, and the implementation plan this story is built from
- [Source: troll/.planning/debug/crossed-book-root-cause.md] — the original investigation this story's fix descends from (Story 5.1); note per the research doc's synthesis, this fix may make Story 5.1's remaining reference-client correlation work moot — re-evaluate that story once this ships
- [Source: troll/dydx_collector/collector.py:354-422] — `Collector.__init__`, existing crossed-book state to extend
- [Source: troll/dydx_collector/collector.py:493-522] — `_apply_deltas`, where tagging is added
- [Source: troll/dydx_collector/collector.py:577-584] — `_resync_book`, unchanged, stays as fallback
- [Source: troll/dydx_collector/collector.py:795-869] — `_handle_crossed_book`, where the active-uncrossing attempt is inserted before the existing timer/escalation logic
- [Source: troll/dydx_collector/collector.py:1062-1083] — `_classify_incident`, confirmed no change needed (see Dev Notes)
- [Source: troll/dydx_collector/tests/test_collector_resilience.py:210-356] — existing crossed-book test patterns and fixtures to extend
- [Source: dydxprotocol/v4-chain, indexer/services/roundtable/src/tasks/uncross-orderbook.ts] — the reference algorithm being ported
- [Source: docs.dydx.exchange/api_integration-guides/how_to_uncross_orderbook] — the official protocol documentation of the crossing mechanism and message-id-based resolution

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5

### Debug Log References

- `book.best_bid_price()`/`book.best_ask_price()` can return `None` after `_uncross_step` removes the last level on a side (e.g. a one-sided book, or the losing side had exactly one resting level). The initial `_handle_crossed_book` loop re-check (`book.best_bid_price().as_double() < book.best_ask_price().as_double()`) crashed with `AttributeError: 'NoneType' object has no attribute 'as_double'` on the first test run. Fixed by capturing both prices into locals first and treating either being `None` as "not crossed" (consistent with `_uncross_step`'s own entry guard, which already did this correctly).
- Verified empirically (ad hoc script, not committed) against real `Collector`/`OrderBook`/`BookOrder` objects before writing the pytest tests: older-sequence-side drop, size-based tie-break, and the missing-tag no-op all behave as the story's ACs specify.

### Completion Notes List

- Task 1: `_level_msg_id` added to `Collector.__init__`; `_apply_deltas` now tags/untags per (side, price) inline in its existing per-delta loop, using `delta.is_clear`/`delta.is_delete` accessors (matching this file's existing style) rather than comparing `.action` directly. `_resync_book` also now pops `_level_msg_id[iid]` on forced resync, for hygiene — not in the story's original task list, but a small, obviously-correct addition: stale tags from before a resubscribe must not survive to be reused after it (the story's own AC #4 already assumes tags are absent right after a resync; this makes that true rather than merely likely).
- Task 2: `_uncross_step` implements the ported dYdX `Roundtable` algorithm, with the size-based tie-break from dYdX's own docs (AC #3) rather than the literal `>`/`>=` quirk in the `uncross-orderbook.ts` snippet — as the story's own Dev Notes flagged as the correct call. Logs one INFO line per dropped level (satisfies AC #5 without a separate summary log). `_handle_crossed_book` tries this in a bounded loop (`_UNCROSS_MAX_STEPS = 5`) before falling through to the pre-existing WARNING/timer/CRITICAL/`_resync_book` logic, which is completely unmodified below the insertion point.
- Task 3: 4 new tests added to `test_collector_resilience.py`, all using real Nautilus objects (`OrderBook`/`Price`/`Quantity`/`OrderBookDelta`/`BookOrder`) per TEST-03 — no mocking. One test was implemented as a direct `_uncross_step` unit test rather than driving it through `_handle_crossed_book`/`_second_loop`, since that's the more precise place to assert the "declines to guess, leaves state untouched" contract in AC #4; the two pre-existing resync-path tests were run alongside it (both still pass) to confirm the additive claim.
- Full `dydx_collector/tests` suite: 104 passed, 0 failed (100 pre-existing + 4 new), run directly via host `python3 -m pytest` (nautilus_trader was already importable outside Docker in this environment — the story's Dev Notes caveat about `make test` was a documented fallback, not a hard requirement, and wasn't needed).
- Manually verified line-length compliance (100 cols) for every line this story added, since `ruff`/`mypy` were not available on the host to run directly.

### File List

- `troll/dydx_collector/collector.py` — imports (`BookOrder`, `OrderBookDelta`, `BookAction`, `Price`, `Quantity`); `_UNCROSS_MAX_STEPS` constant; `Collector.__init__`'s `_level_msg_id`; `_apply_deltas` per-level tagging; `_resync_book` now also clears `_level_msg_id[iid]`; new `_uncross_step` method; `_handle_crossed_book` tries active uncrossing before the existing resync fallback
- `troll/dydx_collector/tests/test_collector_resilience.py` — new `_side_delta` helper; `test_apply_deltas_tags_and_untags_price_levels`; `test_crossed_book_actively_uncrossed_when_both_levels_tagged`; `test_crossed_book_tie_break_uses_smaller_size`; `test_uncross_step_falls_back_when_a_level_is_untagged`

## Change Log

- 2026-09-06: Story implemented end-to-end (Tasks 1-3, all ACs) in a single session. No deviations from the planned approach beyond the tie-break judgement call already flagged in Dev Notes, the `_resync_book` hygiene addition noted above, and one test being structured as a direct `_uncross_step` unit test instead of going through the full `_handle_crossed_book` path (equivalent coverage, more precise assertion). Status set to review.
