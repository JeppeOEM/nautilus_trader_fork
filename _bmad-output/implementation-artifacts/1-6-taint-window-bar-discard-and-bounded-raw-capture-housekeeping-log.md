---
baseline_commit: b766e0adbd8721cba878836b4ffddf129718cc06
---

# Story 1.6: Taint-window bar discard and bounded raw-capture housekeeping log

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the collector,
I want to discard 1s snapshots produced during an active resync window and separately capture bounded raw context around the triggering event,
so that ML/backtest consumers see a genuine parquet gap (never a fabricated or silently-wrong bar) and a human can later diagnose exactly what went wrong.

## Acceptance Criteria

1. **Bar discard during resync window (likely already satisfied — verify, don't re-implement).** Given market X is in resync mode (per Story 1.5's `self._resync_buffers`), when the 1s snapshot loop would otherwise emit a bar for X during that window, then the bar is discarded entirely — not written to the catalog, not flagged-but-present — leaving a genuine gap in the parquet output. **Dev note:** `collector.py:466-467`'s `if iid in self._resync_buffers: continue` (added by Story 1.5) already skips snapshot emission for a resyncing market before it reaches the `batch.append(...)`/catalog-write path — this AC may already be satisfied as a side effect of Story 1.5. Confirm by reading `_second_loop` end-to-end and writing a test that proves no `DydxSecondSnapshot` is appended to the flush buffer for a market while it's in `self._resync_buffers`, rather than re-implementing discard logic that already exists.
2. **Bounded rolling raw-message ring buffer, per market.** Given each market being tracked, when WS `OrderBookDeltas` messages arrive during normal operation, then the collector keeps only a small rolling in-memory buffer of raw messages per market covering approximately the last 30-60 seconds, never an unbounded or continuously-archived raw capture — consistent with `troll/CLAUDE.md`'s MEM-02 ("non-configured coins are in-memory rolling-window only, no unbounded accumulation") and NFR3.
3. **Flush to housekeeping log on sequence gap.** Given a sequence gap fires for market X (`_apply_or_flag_gap`, Story 1.5), when the housekeeping log is written, then it flushes that market's ring buffer (raw messages from shortly before and after the trigger) plus the event timestamp and reason to a separate housekeeping log stream, so storage cost scales with the number of corruption events, not with connection uptime.
4. **Ring buffer stays bounded regardless of trigger frequency.** Given repeated sequence gaps for the same or different markets in a short window, when each housekeeping-log flush occurs, then the ring buffer for the affected market is not left to grow across flushes (e.g. cleared or naturally bounded post-flush) and per-market memory usage does not scale with the number of historical corruption events — only the log output does.

## Tasks / Subtasks

- [x] Task 1 — Verify AC #1 is already satisfied by Story 1.5, don't re-implement (AC: #1)
  - [x] Confirmed: `collector.py`'s `_second_loop` (`if iid in self._resync_buffers: continue`, now ~line 489-490) runs before `batch.append(...)`/`self._on_data(snapshot)` (the catalog-write path) for that market — AC #1 is fully satisfied by Story 1.5's existing guard
  - [x] `test_second_loop_skips_snapshot_while_resyncing` (added by Story 1.5, `test_collector_resilience.py`) already proves this: asserts `collector._buffer.get((DydxSecondSnapshot, _IID), []) == []` for a market mid-resync. No new test needed; re-ran it to confirm it still passes post-Story-1.5-review-fixes.
  - [x] No gap found — no fix needed, no second discard mechanism added
- [x] Task 2 — Bounded per-market raw-message ring buffer (AC: #2)
  - [x] Added `deque` import; `self._raw_ring: dict[str, deque[tuple[int, OrderBookDeltas]]] = defaultdict(deque)` in `__init__`
  - [x] New `_record_raw(iid, data)` method appends `(time.time_ns(), data)` and prunes entries older than `_RING_BUFFER_NS` from the left; called unconditionally at the top of `_on_data_unsafe`'s `OrderBookDeltas` branch (before the resync-buffer/gap-check branches), so it keeps recording through an active resync too
  - [x] **Chose time-window pruning over fixed `maxlen`** (`_RING_BUFFER_NS = 45_000_000_000`, 45s — middle of the "~30-60s" range): a fixed count would represent wildly different real time spans across a liquid vs. illiquid market's very different message rates; the `(ts_ns, data)` tuple + `popleft`-while-stale approach costs a few more lines but matches the AC's "~30-60s" framing exactly regardless of market. Documented inline at `_RING_BUFFER_NS`'s definition.
- [x] Task 3 — Housekeeping log on sequence-gap trigger (AC: #3, #4)
  - [x] Added `housekeeping_logger = logging.getLogger("dydx_collector.housekeeping")` (module-level, distinct name, no new file/volume — writes to stdout like the existing `logger`, captured by Dozzle same as today)
  - [x] **Deviation from the story's suggested call site, with reason:** flush happens in `_resync_sequence_gap` (after buffer replay completes), not in `_apply_or_flag_gap` at gap-detection time. Reason: AC #3 asks for raw context "shortly before **and after** the trigger" — since `_record_raw` keeps recording every message through the resync window (Task 2), flushing only after replay completes captures both sides of the gap in one dump. Flushing at detection time would only ever capture the "before" half. `_resync_sequence_gap`'s signature gained `expected`/`received` params (the sequence numbers from the gap check) so the housekeeping payload can report them without recomputing.
  - [x] `_flush_housekeeping_log(iid, expected, received)`: pops (not just reads) `self._raw_ring[iid]`, serializes each buffered batch's deltas (action, side, price, size, sequence) plus per-message timestamps into a JSON payload with `instrument_id`, `reason`, `expected_sequence`, `received_sequence`, `ts_event_ns`, logged via `housekeeping_logger.warning(json.dumps(...))`
  - [x] Ring is popped (cleared), not left in place — satisfies AC #4 directly (no cross-flush growth) on top of the Task 2 time-bound already capping steady-state memory regardless of trigger frequency
- [x] Task 4 — Regression check
  - [x] `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector troll/ml_signals -q` → 169 passed (165 pre-existing + 4 new), 1 failed (the same pre-existing, out-of-scope `test_ofi_strategy.py` failure, unchanged)
  - [x] `ruff check`/`ruff format --check` on `collector.py` and the test file → 0 new findings; the 5 pre-existing findings in `collector.py` (docstring formatting, `ASYNC240`) are unchanged in location and count, reconfirmed identical to the pre-Story-1.5 baseline
  - [x] No Rust changes made — confirmed unnecessary, this story's scope stayed entirely within `troll/dydx_collector/collector.py` and its tests

## Dev Notes

**This story depends on Story 1.5, which is done.** The attribute to check membership against is `self._resync_buffers: dict[str, list[OrderBookDeltas]]` — presence of a key means that market is mid-resync. Do not use `self._crossed_since_ns` for this story's gating; that belongs to the separate, symptom-based crossed-book watchdog and to Story 1.7's steady-state escalation, not to this story's sequence-gap-triggered housekeeping log.

**AD-2 tension, not a violation — read before implementing Task 3.** The architecture spine's AD-2 ("fail-closed, never fail-open") states rejected-data logging is a single `logging.WARNING` line with no separate quarantine store. This story's housekeeping log is a different artifact serving a different purpose (bounded raw-context capture for postmortem diagnosis, not a per-rejection audit line) and was explicitly scoped by the user in the `brainstorm-orderbook-data-quality-2026-07-02` session — the architecture spine (finalized the day before that session) didn't anticipate it. Keep it consistent with AD-2's spirit by staying stdout/Dozzle-based (a distinctly-named logger, not a new file or store) rather than treating it as license to add a database, a file-based quarantine, or any new persistent path.

**Where `_apply_or_flag_gap` and `_resync_sequence_gap` currently stand (post-Story-1.5-review-fixes, all in `collector.py`):**
- `_apply_or_flag_gap` (~line 271-311): detects the gap (`sequence != last_sequence + 1`), logs a `logger.warning`, starts `_resync_buffers[iid]`, and spawns the `_resync_sequence_gap` task. This is the natural place to also trigger the housekeeping-log flush, since it's the exact moment the gap is confirmed and it already has `iid` and the expected/received sequence numbers in scope.
- `_resync_sequence_gap` (~line 318-365): does the REST-fetch-retry-then-atomic-replay. Not the gap-detection site — only involve this if the housekeeping log needs post-resync state (e.g. "what the buffer replayed to"), which the AC as written does not ask for (it asks for context "shortly before and after the trigger", i.e. around the gap-detection moment, not the resync's outcome).

**Hot-path cost discipline.** `_on_data_unsafe`'s `OrderBookDeltas` branch runs on every single incoming WS orderbook message for every subscribed market — this is the project's hottest path. Appending to a bounded `deque` is O(1) and acceptable; anything more expensive (serialization, timestamp computation beyond what's already available, dict scans) added to this branch should be justified or avoided. Do the actual (more expensive) serialization/formatting work only inside the rare gap-triggered flush path in Task 3, not on every message.

**Precision rule (AD-5, project-wide) — likely N/A here but flag if wrong.** This story shouldn't construct or re-stamp any `Price`/`Quantity` at a new precision — it's moving already-parsed `OrderBookDelta` objects into a buffer and logging their existing values. If any task ends up needing precision re-stamping, use `Decimal.scaleb()` + `Price.from_raw()`/`Quantity.from_raw()`, never `Price(decimal, precision)`.

**Architecture paradigm (must not violate):** Gatekeeper — `collector.py` remains the only writer to the catalog/Redis and the only place invariants are checked (AD-3: readers trust the gate, never re-validate). This story's changes stay inside `collector.py`; `ml_signals` readers are not touched and must not gain any book-reconstruction or resync-awareness logic.

**Story 1.5's own review pass (2026-07-03) fixed several bugs in the code this story extends** — in particular, `self._last_sequence[iid]` is now written only *after* a batch's deltas apply successfully (not before), and `_resync_book`/`_unsubscribe` now reset `self._last_sequence[iid]` on resubscribe. None of this changes this story's scope, but if Task 1's verification touches `_apply_or_flag_gap`, read the current (post-fix) version, not the version described in Story 1.5's original task list.

### Project Structure Notes

- All changes confined to `troll/dydx_collector/collector.py` (consistent with AD-4's module boundary — `ml_signals` is not touched).
- No new files, no new Docker volumes/mounts, no new dependencies — stdlib `collections.deque` and the existing `logging` module cover this story's needs.
- Tests go in the existing `troll/dydx_collector/tests/test_collector_resilience.py`, following its established `_FakeClient`/`_FakeSnapshotClient`/`_seq_deltas` helper patterns rather than introducing a new test-fixture style.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.6] original story definition (Given/When/Then ACs)
- [Source: _bmad-output/brainstorming/brainstorm-orderbook-data-quality-2026-07-02/brainstorm-intent.md] full first-principles derivation — Must items 4-6 map to this story
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-2, #AD-3, #AD-4] gatekeeper paradigm, rejected-data logging pattern, module boundary
- [Source: troll/CLAUDE.md#MEM-01, #MEM-02, #MEM-03] memory-bounded discipline rules this story must satisfy
- [Source: troll/dydx_collector/collector.py:271-311] `_apply_or_flag_gap` — gap-detection site, natural housekeeping-log trigger point
- [Source: troll/dydx_collector/collector.py:245-270] `_on_data_unsafe` — hot path where the ring buffer must be populated cheaply
- [Source: troll/dydx_collector/collector.py:455-505] `_second_loop` — where AC #1's discard guard already lives (added by Story 1.5)
- [Source: _bmad-output/implementation-artifacts/1-5-sequence-verified-order-book-resync.md] previous story — `self._resync_buffers` naming, the 2026-07-03 review-fix pass, and the pre-existing separate `_resync_book`/`_crossed_since_ns` watchdog this story must not touch or conflict with

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `python -c "import ast; ast.parse(...)"` → syntax check clean after each edit
- `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector -q` → 61 passed (57 pre-existing + 4 new) after initial implementation
- `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector troll/ml_signals -q` → 169 passed, 1 failed (pre-existing `test_ofi_strategy.py`, unrelated) after initial implementation; 171 passed, same 1 failure after the review-fix pass below
- `ruff check`/`ruff format --check` on `collector.py`/test file → 5 pre-existing findings unchanged, 0 new (both before and after the review-fix pass)

**2026-07-03 review pass (2 targeted agents: correctness, memory/cleanup) found 2 real bugs, fixed both:**
1. **CONFIRMED — replay-loop crash on an empty-deltas buffered batch.** `_apply_or_flag_gap`'s own `if not data.deltas: return True` guard (added in Story 1.5's review pass) only covers messages applied directly — a content-less update arriving *while already mid-resync* bypasses it entirely (`_on_data_unsafe` appends straight to `self._resync_buffers[iid]` with no check). `_resync_sequence_gap`'s replay loop then unconditionally read `batch.deltas[-1].sequence` per batch, `IndexError`-ing on an empty one and aborting the resync task silently mid-flight — leaving the market on its stale pre-gap book forever (worse than doing nothing). The reviewing agent confirmed via `crates/adapters/dydx/src/python/websocket.rs` that the Rust `OrderbookUpdate` arm has no `is_empty()` guard (unlike its sibling `OrderbookBatch` arm), so this is a real, not just theoretical, gap. Fixed: `continue` past any buffered batch with empty `.deltas` in the replay loop. New regression test: `test_resync_replay_skips_buffered_batch_with_empty_deltas` (uses a duck-typed `SimpleNamespace(deltas=[])` stand-in, since `OrderBookDeltas` itself rejects an empty list at construction — this exercises what the lower-level Rust/PyO3 path could still hand to Python).
2. **CONFIRMED — `self._raw_ring[iid]` never cleaned up on `_unsubscribe`.** Unlike `_last_sequence` (which the same method already pops, established precedent in this file), an unsubscribed market's ring keeps holding its last ~45s of raw messages forever, since no further `OrderBookDeltas` arrive to trigger `_record_raw`'s prune. Violates `troll/CLAUDE.md`'s MEM-02 ("non-configured coins ... must age out"). Fixed: `self._raw_ring.pop(iid, None)` added to `_unsubscribe`. New regression test: `test_unsubscribe_clears_raw_ring`.

**Also hardened (lower severity, fixed as cheap insurance rather than filed as a separate defect):** `_record_raw`'s pruning cutoff was derived from `time.time_ns()` (wall clock) — a backward wall-clock step (NTP correction, VM pause/resume) would silently stall pruning and let the ring grow past its bound until the clock caught back up. Switched the ring's internal timestamp basis to `time.monotonic_ns()`; `_flush_housekeeping_log` now converts back to a wall-clock anchor once, at flush time, for the logged payload (relative ordering stays exact via the monotonic values; only the log's human-readable timestamp needed wall-clock at all).

**Findings raised but not acted on, with reasoning:**
- *Ring maintained for every subscribed market regardless of `_delta_store` (per-coin catalog-persistence) config* — reviewed and judged intentional, not a bug: the story's AC #2 says "each market being tracked," and this ring's purpose (transient postmortem diagnosis of a sequence gap) is a different concern from `_delta_store`'s scope (permanent catalog persistence). Narrowing the ring to `_delta_store` markets would silently drop postmortem diagnosis for any liquid-but-not-delta-stored market's gap — a regression against the AC's literal wording.
- *Synchronous JSON-serialization of the full ring on flush could block the event loop briefly for a very liquid market* — accepted as a bounded, rare-path cost (only fires on an actual sequence gap, and the ring itself is time-bounded to ~45s) rather than adding `asyncio.to_thread` complexity for a cost that's real but small.

### Completion Notes List

- **AC #1 required no new production code** — Story 1.5's `_second_loop` guard already discards bars for a resyncing market as a side effect of skipping emission entirely. Verified by re-reading the code path end-to-end and re-running the existing `test_second_loop_skips_snapshot_while_resyncing` test rather than writing a redundant near-duplicate.
- **Ring buffer keyed on wall-clock time, not message count**, per the story's own explicit guidance to pick deliberately: `defaultdict(deque)` of `(ts_ns, OrderBookDeltas)` tuples, pruned by `_record_raw` on every append. Chosen over a fixed `maxlen` because dYdX message rates differ enormously between a liquid and illiquid market — a fixed count would silently mean a very different real time window per market, undermining the "~30-60s" intent.
- **Housekeeping flush moved to post-replay (`_resync_sequence_gap`), not gap-detection (`_apply_or_flag_gap`)** — a deliberate deviation from the story's suggested "natural" call site, made because `_record_raw` keeps recording through the whole resync window, so waiting until replay finishes lets one flush capture context from both before and after the trigger, matching the AC's literal wording. Documented inline in both the code and Task 3's checklist.
- **`_resync_sequence_gap` gained two new required params** (`expected: int, received: int`) to carry the gap's sequence numbers into the housekeeping payload without recomputing them. The only caller is `_apply_or_flag_gap`, updated alongside; no other call sites existed (confirmed via grep before changing the signature).
- Housekeeping payload is JSON via a distinctly-named `logging.getLogger("dydx_collector.housekeeping")`, written to stdout like every other log line in this collector (captured by the existing Dozzle container) — no new file, volume mount, or dependency, consistent with AD-2's Dozzle-as-audit-trail pattern despite this being a structurally different artifact (bounded raw-context dump, not a single rejected-item WARNING line); see Dev Notes' "AD-2 tension, not a violation" for the full reasoning.
- Ring is `.pop()`-ed (not just read) on flush, directly satisfying AC #4 rather than relying solely on the natural time-bound to prevent cross-flush growth.
- Did not touch Rust, `ml_signals`, or any file outside `troll/dydx_collector/collector.py` and its test file — matches the story's stated Python-only, single-file scope.

### File List

- `troll/dydx_collector/collector.py` — added `deque` import, `housekeeping_logger`, `_RING_BUFFER_NS` constant, `self._raw_ring` state, `_record_raw` method, `_flush_housekeeping_log` method; `_on_data_unsafe` now calls `_record_raw` unconditionally for every `OrderBookDeltas`; `_apply_or_flag_gap`/`_resync_sequence_gap` updated to thread `expected`/`received` sequence numbers through to the new flush call at the end of a completed resync; review-fix pass added an empty-`deltas` guard in the replay loop, `self._raw_ring.pop(iid, None)` in `_unsubscribe`, and switched the ring's timestamp basis from `time.time_ns()` to `time.monotonic_ns()`
- `troll/dydx_collector/tests/test_collector_resilience.py` — added `json`, `logging`, `SimpleNamespace`, `dydx_collector.collector` (module) imports; 6 new tests: `test_record_raw_prunes_messages_older_than_ring_window`, `test_on_data_unsafe_records_raw_even_while_resyncing`, `test_flush_housekeeping_log_emits_and_clears_ring`, `test_resync_sequence_gap_flushes_housekeeping_log`, `test_resync_replay_skips_buffered_batch_with_empty_deltas`, `test_unsubscribe_clears_raw_ring`; `_FakeClient` gained an `unsubscribe_trades` method

## Change Log

- 2026-07-03 — Story created from `epics.md`'s Story 1.6 definition plus the `brainstorm-orderbook-data-quality-2026-07-02` session and Story 1.5's completed implementation/review-fix state as continuity context. Status → ready-for-dev.
- 2026-07-03 — Implemented all 4 tasks: confirmed AC #1 already satisfied by Story 1.5 (no new code); added a time-bounded per-market raw-message ring (`_record_raw`, `_RING_BUFFER_NS`); added a housekeeping-log flush (`_flush_housekeeping_log`) triggered post-resync-replay so it captures context from both before and after the gap. 4 new tests added, 169 Python tests passing (1 pre-existing unrelated failure), 0 new ruff findings. Status → review.
- 2026-07-03 — Adversarial code review (2 targeted agents: correctness, memory/cleanup). Found and fixed 2 real bugs: a replay-loop crash on an empty-`deltas` buffered batch that would silently abandon a market on its stale pre-gap book forever, and `self._raw_ring[iid]` never being cleaned up on `_unsubscribe` (violating MEM-02). Also hardened the ring's pruning against wall-clock regression by switching to `time.monotonic_ns()`. 2 new regression tests added (63 total in `test_collector_resilience.py`, 171 total across `troll/`), same 1 pre-existing unrelated failure, 0 new ruff findings. Status → done.
