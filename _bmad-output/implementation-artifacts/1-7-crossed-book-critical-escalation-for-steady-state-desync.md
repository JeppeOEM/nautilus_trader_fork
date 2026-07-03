---
baseline_commit: b766e0adbd8721cba878836b4ffddf129718cc06
---

# Story 1.7: Crossed-book CRITICAL escalation for steady-state desync

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a crossed book detected during steady-state (no known gap, no active resync, no active reconnect) to be loud and unmistakable,
so that I am alerted to failure causes no existing mechanism predicted, instead of it blending into routine gap-triggered bar discards.

## Acceptance Criteria

1. **Expected transient window: silent skip, unchanged.** Given a market's local book is observed crossed (`best_bid >= best_ask`), when this occurs while the market is in an expected transient window (mid-resync buffer-replay per Story 1.5, or the existing brief window immediately after a WS reconnect's CLEAR+snapshot replay), then it is a silent skip exactly as today — no escalation, no snapshot emitted, no behavior change from current code.
2. **Steady-state desync: CRITICAL escalation.** Given a market's local book is observed crossed, when this occurs in steady state — no known sequence gap (Story 1.5's `_resync_buffers`), not mid-resync, not within the expected post-reconnect transient window — then it is logged at `CRITICAL` severity (Python `logging.CRITICAL`, not `WARNING`) to a distinct, separately-named event stream from routine gap-triggered bar discards (Story 1.6's housekeeping log), and is immediately visible rather than buried in routine INFO/WARNING volume.
3. **Escalation is additive, not a replacement.** Given a steady-state crossed book has just been escalated to CRITICAL, when the existing crossed-book recovery mechanism (`_resync_book`, the 15s-persistence-triggered forced resubscribe) would otherwise fire, then it still fires exactly as before — this story adds visibility, it does not change or remove any existing recovery behavior.

## Tasks / Subtasks

- [x] Task 1 — Design decision: how "not mid-reconnect" is detected (read before coding) (AC: #1, #2)
  - [x] Confirmed via `grep -n reconnect collector.py client.py`: no tracked flag exists, only comments
  - [x] Confirmed `_second_loop`'s crossed-book branch matches the description exactly; the 15s `_CROSSED_RESYNC_NS` grace window is already the system's de facto "expected transient" tolerance
  - [x] Implemented per the decision: escalate at the same `_CROSSED_RESYNC_NS` threshold that already gates `_resync_book`; no new reconnect-tracking state added
- [x] Task 2 — Confirm the mid-resync exclusion needs no new code (AC: #1)
  - [x] Confirmed: `_second_loop`'s `if iid in self._resync_buffers: continue` (Story 1.5) runs before the crossed-book check; verified with `test_second_loop_never_reads_book_for_market_mid_resync`. No new code needed for this exclusion.
- [x] Task 3 — CRITICAL escalation logger and call site (AC: #2, #3)
  - [x] Added `critical_logger = logging.getLogger("dydx_collector.critical")`, module-level, alongside `logger`/`housekeeping_logger`
  - [x] Added `critical_logger.critical(json.dumps(...))` inside the existing `if now_ns - crossed_since > _CROSSED_RESYNC_NS:` branch, immediately before `await self._resync_book(iid)`. Payload: `instrument_id`, `reason: "steady_state_crossed_book"`, `best_bid`, `best_ask`, `crossed_duration_ns`, `ts_event_ns`
  - [x] `await self._resync_book(iid)` untouched — still fires unconditionally alongside the new escalation (AC #3)
  - [x] **Decision made, then corrected after review (see the 2026-07-03 review-fix entry below): escalate on every occurrence of the threshold being crossed, not via a dedicated suppression flag.** `_resync_book` already resets `_crossed_since_ns` on every call, so re-entering the CRITICAL branch naturally takes another full `_CROSSED_RESYNC_NS` (~15s) — matching `_resync_book`'s own retry cadence. No separate "already escalated" state is needed or present.
- [x] Task 4 — Regression check
  - [x] `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector troll/ml_signals -q` → 174 passed (171 + 3 new), 1 failed (same pre-existing, unrelated `test_ofi_strategy.py` failure)
  - [x] `ruff check`/`ruff format --check` → 0 new findings (5 pre-existing findings in `collector.py` unchanged; one new finding introduced in the test file during authoring — `D401` imperative-mood docstring — fixed immediately, confirmed clean)
  - [x] 3 new tests added: `test_crossed_book_within_grace_window_does_not_escalate`, `test_crossed_book_past_grace_window_escalates_critical` (also asserts `_resync_book` still fires), `test_second_loop_never_reads_book_for_market_mid_resync`
  - [x] No Rust changes made — confirmed unnecessary; scope stayed entirely within `_second_loop`'s existing crossed-book branch

## Dev Notes

**This story depends on Story 1.5 (done) and pairs with Story 1.6 (done), both already merged into the working tree.** `self._resync_buffers` (1.5) and `_flush_housekeeping_log`/`housekeeping_logger` (1.6) are established patterns to follow, not to duplicate. This story's escalation is a *third* distinct severity/purpose tier:
- Story 1.5: `logger.warning` — routine sequence-gap detection, known cause, known fix (resync)
- Story 1.6: `housekeeping_logger.warning` — bounded raw-context dump for postmortem, triggered by the same known-cause sequence gap
- Story 1.7 (this story): a new `critical_logger.critical` — **unknown cause**, no existing mechanism explains it, the single loudest signal in the system

**Why steady-state crossed-book is the highest-severity case, and why `message_id` checking structurally cannot catch it (read this before treating it as "just another gap"):** Story 1.5's sequence-gap detection only catches *known* data loss (a `message_id` discontinuity). A crossed book with an *intact, gap-free* sequence means either (a) a bug in this collector's own book-reconstruction logic, or (b) dYdX sent genuinely bad/self-contradictory data with no detectable transport-level cause. Neither is explained or fixed by anything Story 1.5 or 1.6 built — hence CRITICAL, and hence a distinct log stream that won't get lost in the (comparatively routine, expected-to-happen) volume of sequence-gap WARNING lines.

**No new "mid-reconnect" tracking state — reuse the existing 15s grace window (`_CROSSED_RESYNC_NS`), don't build parallel state for it.** The codebase has no existing signal for "the WS client is currently mid-reconnect" and this story should not invent one. `_CROSSED_RESYNC_NS`'s existing 15-second grace period (already used to gate the pre-existing `_resync_book` call) already encodes the system's operational definition of "this is plausibly still an expected transient, don't panic yet." Escalate at the same threshold, not a new one. See Task 1 for the full reasoning — this is a genuine design decision this story makes, not something spelled out mechanically in the epics text, so don't second-guess it without a concrete reason the existing threshold is wrong.

**AD-2 tension, same shape as Story 1.6's, already resolved once — don't re-relitigate.** The architecture spine's AD-2 envisions a single `logging.WARNING` rejected-data line with no separate quarantine mechanism. Story 1.6 already established the precedent (accepted by the user via the brainstorm session that spawned both 1.6 and 1.7) that a distinctly-named logger writing structured content to stdout — not a new file, not a new store — is the acceptable pattern for a purpose-specific event stream that predates AD-2's original scope. Follow that same shape here: `critical_logger`, stdout, Dozzle-visible, no new persistent path.

**Precision rule (AD-5) — not applicable.** This story reads `book.best_bid_price()`/`best_ask_price()` (already-parsed `Price` objects) for logging; it does not construct or re-stamp any `Price`/`Quantity`.

**Architecture paradigm (must not violate):** Gatekeeper — `collector.py` remains the only writer to the catalog/Redis and the only place invariants are checked (AD-3). This story's change is entirely inside `_second_loop`'s existing crossed-book branch; `ml_signals` readers are not touched.

**Post-Story-1.5-review-fix and Story 1.6 state to be aware of (both already in the working tree):** `_resync_book` now also pops `self._last_sequence[iid]` (Story 1.5 review fix) and does not touch `self._raw_ring` (Story 1.6 — that ring is only cleaned up on permanent `_unsubscribe`, not on a resync-triggered resubscribe, since the market stays actively tracked). Neither of these interacts with this story's change, but read the current `_resync_book`/`_second_loop` code directly rather than relying on old line-number references, since both have shifted across the last two stories' edits.

### Project Structure Notes

- All changes confined to `troll/dydx_collector/collector.py` (module boundary, AD-4) — `ml_signals` untouched.
- No new files, no new dependencies. `logging.getLogger` is the only new primitive needed, matching Story 1.6's `housekeeping_logger` precedent exactly.
- Tests go in the existing `troll/dydx_collector/tests/test_collector_resilience.py`, using `caplog` the same way Story 1.6's `test_flush_housekeeping_log_emits_and_clears_ring` does to assert on a specific named logger's output.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.7] original story definition (Given/When/Then ACs)
- [Source: _bmad-output/brainstorming/brainstorm-orderbook-data-quality-2026-07-02/brainstorm-intent.md] Must item 7 — "Crossed-book escalation" — full first-principles derivation
- [Source: troll/dydx_collector/collector.py] `_second_loop`'s crossed-book branch (`_CROSSED_RESYNC_NS`, `_crossed_since_ns`, `_resync_book`) — read current state directly, do not trust stale line numbers from other stories' notes
- [Source: _bmad-output/implementation-artifacts/1-5-sequence-verified-order-book-resync.md] `self._resync_buffers` — the mid-resync exclusion this story relies on but does not modify
- [Source: _bmad-output/implementation-artifacts/1-6-taint-window-bar-discard-and-bounded-raw-capture-housekeeping-log.md] `housekeeping_logger` pattern — the distinctly-named-stdout-logger precedent this story's `critical_logger` follows
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-2, #AD-3] gatekeeper paradigm, rejected-data logging pattern (and its already-accepted extension in Story 1.6)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `python -c "import ast; ast.parse(...)"` → syntax check clean after each edit
- `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector -q` → 66 passed (63 pre-existing + 3 new)
- `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector troll/ml_signals -q` → 174 passed, 1 failed (pre-existing `test_ofi_strategy.py`, unrelated)
- `ruff check`/`ruff format --check` → 5 pre-existing findings in `collector.py` unchanged, 0 new (both before and after the review-fix pass); test file: 1 new finding (`D401`) introduced during initial authoring then immediately fixed, confirmed clean

### Completion Notes List

- **No new "mid-reconnect" detection state added, by design** — confirmed there was no existing Python-visible reconnect signal to build on, and reused the existing `_CROSSED_RESYNC_NS` 15s grace window (already the system's operational tolerance for "reconnect/resync-replay noise") as the escalation threshold rather than inventing new, untested reconnect-tracking machinery. Documented in Dev Notes as a genuine design decision, not a mechanical translation of the epic's wording.
- **Mid-resync exclusion (AC #1's other half) required zero new code** — Story 1.5's `if iid in self._resync_buffers: continue` already runs before this story's logic can ever be reached. Verified with a new test rather than assumed.
- **Escalation is strictly additive** — the pre-existing `await self._resync_book(iid)` call was not moved, reordered, or wrapped; the new `critical_logger.critical(...)` call sits immediately before it in the same branch.
- Reused Story 1.6's exact pattern for the new logger (distinctly-named, stdout, JSON payload via `json.dumps`) rather than inventing a different shape for this story's severity tier.
- Did not touch Rust, `ml_signals`, or any file outside `troll/dydx_collector/collector.py` and its test file.

**2026-07-03 review pass (1 targeted correctness-focused agent) found 1 real bug, fixed:**
- **CONFIRMED — the initial "escalate once per episode" implementation was dead code.** The first implementation added `self._critical_escalated: set[str]`, added `iid` to it at escalation time, and — following the same "fresh episode" reasoning already applied to `_last_sequence`/`_raw_ring` in earlier stories — also cleared it inside `_resync_book`. The bug: `_resync_book` runs synchronously right after the `.add(iid)` in the very same code path (only an `await` between them, no other code touches the set in between), so the flag was cleared moments after being set, on every single occurrence — making the "once per episode" guard permanently inert. Real-world effect: CRITICAL would repeat every time `_resync_book` fires (~every `_CROSSED_RESYNC_NS`, since `_resync_book` also resets `_crossed_since_ns`), which is actually the operationally *correct* behavior for a genuinely unresolved incident (a CRITICAL alert that fires once and then goes silent forever for a permanently-broken market is worse than one that repeats) — but the dead `_critical_escalated` state and its misleading docstring ("cleared as soon as the book is seen uncrossed again") no longer matched what the code actually did. **Fixed by deletion, not by patching the guard:** removed `self._critical_escalated` entirely (declaration, add, discard×2) and let the pre-existing `_crossed_since_ns` reset (inside `_resync_book`) be the sole natural spacing mechanism — simpler, correct, and no state whose invariant could drift from its comment. Updated the affected test's name/docstring/assertion message from "escalates once" to "not re-escalated on every tick within one `_CROSSED_RESYNC_NS` window" to describe the actual mechanism rather than a flag that no longer exists.

### File List

- `troll/dydx_collector/collector.py` — added `critical_logger` (module-level); `_second_loop`'s crossed-book branch now calls `critical_logger.critical(...)` every time `_CROSSED_RESYNC_NS` is exceeded, immediately before the unchanged `await self._resync_book(iid)` call — natural repeat spacing comes from `_resync_book`'s pre-existing `_crossed_since_ns.pop(iid, None)`, no new suppression state
- `troll/dydx_collector/tests/test_collector_resilience.py` — added `_crossed_book` helper; 3 new tests: `test_crossed_book_within_grace_window_does_not_escalate`, `test_crossed_book_past_grace_window_escalates_critical`, `test_second_loop_never_reads_book_for_market_mid_resync`

## Change Log

- 2026-07-03 — Story created from `epics.md`'s Story 1.7 definition, the `brainstorm-orderbook-data-quality-2026-07-02` session, and Stories 1.5/1.6's completed implementation state as continuity context. Status → ready-for-dev.
- 2026-07-03 — Implemented all 4 tasks: added `critical_logger` CRITICAL-severity escalation for steady-state crossed books (no known sequence gap, past the existing 15s grace window), additive alongside the existing `_resync_book` recovery. 3 new tests, 174 Python tests passing (1 pre-existing unrelated failure), 0 new ruff findings. Status → review.
- 2026-07-03 — Adversarial code review (1 targeted correctness agent). Found and fixed 1 real bug: an "escalate once per episode" suppression flag (`self._critical_escalated`) that was cleared in the same synchronous path it was set in, making it permanently inert — removed entirely rather than patched, relying on the pre-existing `_crossed_since_ns` reset for natural repeat spacing instead. Updated 1 test's naming/assertions to match. Same 174 passing / 1 pre-existing failure, 0 new ruff findings. Status → done.
- 2026-07-03 — Implemented all 4 tasks: confirmed no existing reconnect-tracking signal and reused the existing 15s crossed-book grace window as the escalation threshold; confirmed the mid-resync exclusion needed no new code; added a `critical_logger` CRITICAL-severity escalation (once per episode, re-escalating across repeated failed resubscribe attempts) additive to the existing `_resync_book` recovery. 3 new tests added, 174 Python tests passing (1 pre-existing unrelated failure), 0 new ruff findings. Status → review.
