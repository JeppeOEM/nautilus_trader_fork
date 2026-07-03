---
baseline_commit: b766e0adbd8721cba878836b4ffddf129718cc06
---

# Story 1.5: Sequence-verified order book resync

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the collector,
I want to detect a dropped WebSocket message by its exact sequence number and provably resync the local order book afterward,
so that a gap is caught the instant it happens rather than inferred later from a crossed-book symptom, and the book is known-correct again rather than assumed healed.

## Acceptance Criteria

1. **`message_id` reaches Python via the existing `sequence` field (FR3/FR4 extension).** Given the Rust dYdX adapter's WS orderbook envelope, when it is converted to `OrderBookDelta` objects and crosses the PyO3 boundary, then each delta's already-existing `sequence: u64` field (`crates/model/src/data/delta.rs:56`, already exposed to Python as `delta.sequence` — confirmed via `crates/model/src/python/data/delta.rs:190-192`) carries the real dYdX `message_id` instead of the hardcoded `0` it carries today.
2. **Exact per-market gap check.** Given a market with a known last-confirmed sequence number, when the next `OrderBookDeltas` batch for that market arrives, then the collector checks `sequence == last_id + 1` exactly (not `sequence > last_id` regression), and a gap is detected the instant it fails.
3. **Resync-mode buffering.** Given a sequence gap is detected for market X, when the collector enters resync mode for X, then it stops calling `book.apply_delta()` for X and instead buffers incoming `OrderBookDeltas` in arrival order, and halts 1s snapshot emission for X. (Bar-discard/parquet-gap mechanics and the housekeeping log are out of scope — Story 1.6. This AC only requires the halt/buffer behavior.)
4. **REST snapshot + idempotent buffered replay.** Given resync mode is active for market X, when a REST order book snapshot for X is fetched, then X's local `OrderBook` is replaced wholesale with the snapshot's deltas, then every buffered `OrderBookDeltas` batch is replayed on top via `book.apply_delta()` in arrival order — relying on dYdX's confirmed absolute-per-level update semantics (an update replaces a level's size; it is never a relative delta — confirmed in `crates/adapters/dydx/src/websocket/parse.rs`'s `parse_orderbook_deltas_with_flag`, where `qty.is_zero()` maps to `BookAction::Delete` and any other value maps to `BookAction::Update` with no read-modify-write against prior state), so replay is idempotent and no precise anchor/cut-point is required.
5. **No race window.** Given the snapshot-swap-and-replay sequence, when it executes, then it runs as one synchronous block (the entire span from "REST response received" to "buffer fully replayed" contains no `await`), relying on `_on_data`/`_on_data_unsafe` always running on the same asyncio event loop (confirmed: `collector.py:224-226`'s own comment — "Called directly from the Rust WS client's callback thread/loop") so no WS message for that market can be processed concurrently and slip through unbuffered.
6. **Clean exit.** Given replay of the buffer completes, when resync mode exits for market X, then the market's tracked last-sequence value is reset to the last replayed delta's `sequence` and live per-message processing resumes normally for X.

## Tasks / Subtasks

- [x] Task 1 — Rust: thread `message_id` into `OrderBookDelta.sequence` instead of the hardcoded `0` (AC: #1)
  - [x] `crates/adapters/dydx/src/websocket/enums.rs:213-226` — added `message_id: u64` to the `OrderbookSnapshot`, `OrderbookUpdate`, `OrderbookBatch` variants of `DydxWsOutputMessage`
  - [x] `crates/adapters/dydx/src/websocket/handler.rs:541-592` — `deserialize_orderbook_snapshot`/`update`/`batch` now populate `message_id: data.message_id` on each constructed variant
  - [x] `crates/adapters/dydx/src/websocket/parse.rs` — added `message_id: u64` param to `parse_orderbook_snapshot`, `parse_orderbook_deltas`, `parse_orderbook_deltas_with_flag`; replaced all 6 hardcoded `0` sequence args (4× `OrderBookDelta::new`, 2× `OrderBookDelta::clear`) with `message_id`. Decided: yes, thread it into the Clear deltas too — same snapshot message, same sequence, no reason to leave one inconsistent.
  - [x] `crates/adapters/dydx/src/python/websocket.rs:246-298` — all three match arms destructure `message_id` and pass it through
  - [x] **Scope addition found during implementation, not in original task list:** `crates/adapters/dydx/src/data.rs:1127-1230` — the native (non-PyO3) `DydxDataClient::handle_ws_message` also destructures/matches these same three enum variants and calls the same `parse_orderbook_*` functions, for the pure-Rust live/backtest engine path. Adding `message_id` to the enum variants is a breaking signature change that would not compile without updating this file too — fixed all 4 call sites there identically. This wasn't optional scope creep; the crate does not build otherwise.
  - [x] Rebuild: `make build-base` kicked off (background, ~15 min) to get the change into the Docker image used by `make up`
  - [x] Updated 3 existing Rust unit tests (`test_parse_orderbook_snapshot`, `test_parse_orderbook_snapshot_flag_shapes`, `test_parse_orderbook_deltas_update` in `parse.rs`) to pass an explicit `message_id` and assert every resulting delta's `.sequence` equals it. `cargo test -p nautilus-dydx --lib`: 331 passed, 0 failed. `cargo clippy -p nautilus-dydx --lib --no-deps`: clean. `cargo +nightly fmt -p nautilus-dydx -- --check`: clean.
- [x] Task 2 — Python: exact per-market gap check (AC: #2)
  - [x] `_apply_or_flag_gap` (new method, `collector.py`) reads `data.deltas[-1].sequence` and compares against `self._last_sequence: dict[str, int]`; called from `_on_data_unsafe`'s `OrderBookDeltas` branch before any `book.apply_delta()`
  - [x] Gap = `sequence != last_sequence + 1` exactly; `self._last_sequence.get(iid)` being `None` (no prior confirmed sequence) short-circuits to "not a gap" via the `last_sequence is not None and ...` guard
  - [x] `test_apply_or_flag_gap_applies_consecutive_sequence` (first message + next consecutive, both apply, no resync) and the gap-path assertion inside `test_apply_or_flag_gap_detects_gap_and_resyncs` (100→103 triggers resync, does not apply) — `test_collector_resilience.py`
- [x] Task 3 — Python: resync-mode buffering (AC: #3, #5)
  - [x] `self._resync_buffers: dict[str, list[OrderBookDeltas]]` (named `_resync_buffers`, not the story's suggested `_resyncing` — more accurately describes what it holds; **Story 1.6/1.7 should reference `self._resync_buffers`, not `_resyncing`**). Presence of a key = in resync mode.
  - [x] `_apply_or_flag_gap` never calls `book.apply_delta()` on the gap-revealing batch; `_on_data_unsafe` checks `iid in self._resync_buffers` first and appends-and-returns for every subsequent batch while resyncing
  - [x] `_second_loop` gained a guard: `if iid in self._resync_buffers: continue` before any book/snapshot logic for that instrument (placed before the existing crossed-book check, since a resyncing book is not just possibly-crossed, it's known-stale)
  - [x] Verified: `_resync_sequence_gap`'s swap-and-replay (buffer pop → book rebuild → replay loop) contains zero `await` statements — confirmed by direct code inspection, and behaviorally proven by `test_apply_or_flag_gap_detects_gap_and_resyncs` (a message sent mid-resync via `_on_data_unsafe` always lands in the buffer, never partially applied)
- [x] Task 4 — Python: REST snapshot + idempotent replay (AC: #4, #6)
  - [x] `DydxClient.request_orderbook_snapshot` added to `client.py`, matching the `subscribe_orderbook` wrapper pattern. **Correction to the original task plan:** the Rust PyO3 method returns the raw pyo3-native `OrderBookDeltas` (like `fetch_instruments`'s pyo3-native return, per that method's own docstring), not the Cython type every other data path in this collector uses — found by checking `nautilus_trader/adapters/dydx/data.py:527-531`'s official-adapter usage of the same method, which converts via `OrderBookDeltas.from_pyo3(pyo3_deltas)`. The wrapper does that conversion before returning, so the rest of the collector never has to know about the pyo3/Cython split.
  - [x] `_resync_sequence_gap` (new async method): loop-retries `request_orderbook_snapshot` on failure (1s backoff — REST fetch failing must not leave a market stuck in resync forever); on success, pops the buffer, rebuilds `self._live_books[iid]` from the snapshot's deltas, replays every buffered batch's deltas on top in order — all synchronous, no `await`, per Task 3's AC #5 requirement
  - [x] Confirmed no anchor field exists (matches story's prior research) — buffering starts at the gap-revealing message (Task 2/3) and the REST round trip is fully covered since buffering never stops until the synchronous replay block runs
  - [x] After replay: `self._last_sequence[iid]` reset to the last buffered delta's `.sequence`; if nothing was buffered during the round trip (rare — REST was fast), `_last_sequence` is popped instead of reset to a stale value, so the next live message is treated as "first message" rather than gap-checked against pre-resync state
  - [x] `test_apply_or_flag_gap_detects_gap_and_resyncs` — full flow: gap detected → 2 messages buffered (one at gap-detection, one mid-flight) → fake REST snapshot → replay produces a book combining the snapshot's price level with the buffered deltas' price level, proving it was rebuilt not patched; asserts `_last_sequence` reset to the last buffered sequence, buffer cleared, exactly one REST call made
- [x] Task 5 — Full-suite regression check
  - [x] `PYTHONPATH=troll python -m pytest troll/dydx_collector troll/ml_signals -q` → 165 passed, 1 failed (the pre-existing, out-of-scope `test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure` — reconfirmed pre-existing via `git stash`/`git diff` before touching anything, unrelated to this story's files)
  - [x] `cargo test -p nautilus-dydx --lib` → 331 passed, 0 failed. `cargo clippy -p nautilus-dydx --lib --no-deps` → clean. `cargo +nightly fmt -p nautilus-dydx -- --check` → clean. `ruff check`/`ruff format --check` on all touched Python files → 0 new findings (5 pre-existing findings elsewhere in `collector.py`, reconfirmed via `git stash` to predate this story, left untouched per scope discipline)

## Dev Notes

**Fork-boundary ruling for this story (confirmed with the user, not a default assumption):** `troll/CLAUDE.md`'s FORK-01 ("never modify `nautilus_trader/` or `crates/`") does not carve out an adapter exception in its text, but the user explicitly confirmed for this story that `crates/adapters/dydx/` is fair game — additive, venue-specific adapter code is a different concern than core engine crates (`crates/model`, `crates/execution`, `crates/data`, etc.), which remain absolutely off-limits. **Do not touch `crates/model/` or any crate outside `crates/adapters/dydx/`** — you don't need to: `OrderBookDelta.sequence` already exists in Nautilus core (`crates/model/src/data/delta.rs:56`) and is already exposed to Python (`crates/model/src/python/data/delta.rs:190-192`). The entire Rust-side fix is: stop hardcoding it to `0` in the dYdX adapter's parse functions, and thread the real `message_id` through. This is a genuinely minimal, surgical adapter change — if a task ends up touching anything under `crates/` outside `crates/adapters/dydx/`, stop and reconsider the approach.

**Why not build the resync state machine in Rust instead of Python:** it was considered and rejected. The buffer's correctness (AC #5, no race window) depends on the Python collector's single-threaded asyncio guarantee — `_on_data`/`_on_data_unsafe` always runs on the same event loop (`collector.py:224-226`). Rust's WS client runs on a different (tokio) runtime with different concurrency guarantees, so moving the buffering/replay logic into Rust would require re-deriving that safety argument from scratch and would be a much larger, riskier change for no benefit — the data already reaches Python today via `OrderBookDeltas`, only the sequence number was missing.

**This story's job ends at "the book is provably correct again."** It deliberately does not implement: the taint-window bar-discard / genuine-parquet-gap behavior (Story 1.6), the bounded ring-buffer raw-capture housekeeping log (Story 1.6), or the crossed-book CRITICAL-severity escalation (Story 1.7). Story 1.6 and 1.7 both read this story's resync-mode state to know when a market is in an expected transient window — **the actual attribute is `self._resync_buffers: dict[str, list[OrderBookDeltas]]`** (not `_resyncing` as originally sketched here — renamed during implementation since it holds the buffer itself, not just a flag); check membership via `iid in self._resync_buffers`.

**Absolute-per-level semantics (why idempotent replay is safe, don't second-guess this):** confirmed via `crates/adapters/dydx/src/websocket/parse.rs`'s `parse_orderbook_deltas_with_flag` — every update sets a price level's size to exactly the incoming value (`BookAction::Update`) or deletes it if size is zero (`BookAction::Delete`). There is no read-modify-write against prior local state anywhere in this parse path. This means replaying a message that's already reflected in a REST snapshot is a harmless no-op — the buffer does not need a precise start/end boundary, only "definitely covers everything from the gap onward," which starting the buffer at the gap-revealing message and never stopping until replay finishes guarantees.

**There is an unrelated, already in-flight, uncommitted change in `collector.py`** implementing a *different*, symptom-based mechanism: `_resync_book()` (currently ~L290-295) + `_crossed_since_ns` tracking, triggered when a crossed book persists >15s (`_CROSSED_RESYNC_NS`), which forces an unsubscribe/resubscribe. This story's sequence-based mechanism is additive alongside it, not a replacement — do not remove or conflict with the existing crossed-persists-15s watchdog; it stays as a symptom-based backstop for causes this story's sequence check cannot see (matches the Story 1.7 CRITICAL-escalation reasoning). If both mechanisms could ever fire for the same market at the same time, prefer letting the sequence-based resync (this story) win/run first since it's a proven-cause response, not a symptom guess — but this is a corner case, not a hard AC; use judgment and leave a comment if you make a call here.

**Precision rule (AD-5, project-wide, applies if this story touches any price/quantity re-stamping):** never use `Price(decimal, precision)`/`Quantity(decimal, precision)` — silently corrupts values for some inputs. Always `Decimal.scaleb(new_precision)` + `Price.from_raw()`/`Quantity.from_raw()`. This story shouldn't need to re-stamp precision at all (REST snapshot deltas go through the same `parse_orderbook_snapshot` path already using `Price.from_decimal_dp`/`Quantity.from_decimal_dp`), but flag it if a new code path needs it.

**Architecture paradigm (must not violate):** "Gatekeeper: fail-closed single-writer ingestion" — `dydx_collector/collector.py` is the only writer to `ParquetDataCatalog`/Redis and the only place invariants are checked (AD-3: readers trust the gate, never re-validate). This story's Python changes stay inside `collector.py`/`client.py`; never add book-reconstruction logic to `ml_signals` readers.

### Project Structure Notes

- Rust changes confined to `crates/adapters/dydx/src/websocket/{enums,handler,parse}.rs` and `crates/adapters/dydx/src/python/websocket.rs` — no other crate touched.
- Python changes confined to `troll/dydx_collector/collector.py` and `client.py` — consistent with the module boundary convention (AD-4): `ml_signals` is not touched by this story.
- Rust changes require `make build-base` before `make up` will pick them up — this is a real ~15 min step, budget for it; forgetting it means testing against a stale base image that silently still returns `sequence: 0`.

### References

- [Source: crates/model/src/data/delta.rs:46-61] `OrderBookDelta` struct — `sequence: u64` field already exists
- [Source: crates/model/src/python/data/delta.rs:190-192] `sequence` already exposed to Python via `#[pyo3(name = "sequence")]`
- [Source: crates/adapters/dydx/src/websocket/messages.rs:59,77,89,100,112,123] `message_id`/`version` fields on `DydxWsSubscriptionMsg`/`DydxWsConnectedMsg`/`DydxWsChannelDataMsg`/`DydxWsChannelBatchDataMsg`
- [Source: crates/adapters/dydx/src/websocket/handler.rs:86,118,374-412,541-592] existing `book_sequence` regression-only check (keep, don't remove); `deserialize_orderbook_*` functions to modify
- [Source: crates/adapters/dydx/src/websocket/enums.rs:206-226] `DydxWsOutputMessage` enum, Orderbook variants to extend
- [Source: crates/adapters/dydx/src/websocket/parse.rs:515-745] `parse_orderbook_snapshot`/`parse_orderbook_deltas`/`parse_orderbook_deltas_with_flag` — hardcoded `0` sequence args to fix
- [Source: crates/adapters/dydx/src/python/websocket.rs:246-298] PyO3 boundary match arms to extend
- [Source: crates/adapters/dydx/src/http/models.rs:138-154] `OrderbookResponse`/`OrderbookLevel` — confirmed no anchor field
- [Source: crates/adapters/dydx/src/http/client.rs:1404] `request_orderbook_snapshot` — already builds synthetic CLEAR+ADD deltas from REST
- [Source: crates/adapters/dydx/src/python/http.rs:507-517] `request_orderbook_snapshot` already PyO3-exposed
- [Source: troll/dydx_collector/collector.py:212-241,290-295] `_live_books`, `_on_data_unsafe`, existing unrelated `_resync_book` crossed-book watchdog
- [Source: troll/dydx_collector/client.py:69-121] `DydxClient` wrapper pattern to extend
- [Source: troll/dydx_collector/tests/test_collector_resilience.py] existing `_FakeClient` pattern for testing without real network
- [Source: _bmad-output/brainstorming/brainstorm-orderbook-data-quality-2026-07-02/brainstorm-intent.md] full first-principles derivation of this design
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.5] original story definition

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `cargo check -p nautilus-dydx --lib` → clean compile after all Rust changes
- `cargo test -p nautilus-dydx --lib` → 331 passed, 0 failed (first run caught one missed `message_id` threading site — an `OrderBookDelta::clear` call with 4-space indent that a non-`replace_all`-safe edit skipped; fixed, reran clean)
- `cargo clippy -p nautilus-dydx --lib --no-deps` → clean
- `cargo +nightly fmt -p nautilus-dydx -- --check` → clean
- `PYTHONPATH=troll python -m pytest troll/dydx_collector troll/ml_signals -q` → 165 passed, 1 failed (pre-existing `test_ofi_strategy.py` failure, reconfirmed via `git stash` to predate this story)
- `ruff check` / `ruff format --check` on all touched Python files → 0 new findings (5 pre-existing findings elsewhere in `collector.py`, reconfirmed pre-existing via `git stash`)

### Completion Notes List

- **AC #1 closed**, with a materially smaller Rust footprint than the story anticipated: `OrderBookDelta.sequence` already existed in Nautilus core and was already exposed to Python — the fix was removing 6 hardcoded `0` sequence arguments in the dYdX adapter's parse functions and threading `message_id` through, not adding a new field anywhere. No `crates/model/` changes needed.
- **Scope addition, not creep:** `crates/adapters/dydx/src/data.rs` (the native/non-PyO3 `DydxDataClient` used for pure-Rust live/backtest) shares the same `parse_orderbook_*` functions and the same `DydxWsOutputMessage` enum. Adding `message_id` to the enum variants is a breaking signature change — the crate does not compile without updating this file's 4 call sites too. Fixed identically to the PyO3 path; this file wasn't in the original story task list because the create-story research only traced the PyO3 (`python/websocket.rs`) path, not this parallel native path.
- **AC #4's REST wrapper needed a correction the story didn't anticipate:** the Rust PyO3 `request_orderbook_snapshot` returns the raw pyo3-native `OrderBookDeltas`, not the Cython type every other code path in this collector uses. Found by checking how the *official* (unused-by-us) `nautilus_trader/adapters/dydx/data.py` consumes the same method — it converts via `.from_pyo3()`. `client.py`'s wrapper does that conversion, so `collector.py` never has to know about the pyo3/Cython split.
- **AC #2-#6 (gap detection, buffering, atomic replay, clean exit) implemented as one coherent unit** in `collector.py`: `_apply_or_flag_gap` (sync, called from `_on_data_unsafe`) and `_resync_sequence_gap` (async, does the REST-fetch-then-synchronous-replay). Added a retry loop (1s backoff) around the REST fetch — not explicitly required by any AC, but without it a REST failure would leave a market stuck in resync mode forever, which is a real "must leave the system working end-to-end" gap, not scope creep.
- **Naming deviation from the story's sketch:** the resync-buffer attribute is `self._resync_buffers` (holds the buffer), not the story's suggested `_resyncing` (implied a bare flag). Story 1.6/1.7 dev notes updated in this file to reference the correct name.
- **`_second_loop` guard placed before the existing crossed-book check**, not alongside it — a resyncing book isn't merely possibly-crossed, its state is definitionally stale, so it should never reach the crossed-book/staleness checks at all.
- Did not implement: taint-window bar-discard/parquet-gap semantics or the housekeeping log (Story 1.6, explicitly out of scope), or crossed-book CRITICAL escalation (Story 1.7, explicitly out of scope). Used `logger.warning` (not `logger.critical`) for the sequence-gap log line specifically to preserve that distinction for Story 1.7.

### File List

- `crates/adapters/dydx/src/websocket/enums.rs` — added `message_id: u64` to `DydxWsOutputMessage::{OrderbookSnapshot,OrderbookUpdate,OrderbookBatch}`
- `crates/adapters/dydx/src/websocket/handler.rs` — `deserialize_orderbook_{snapshot,update,batch}` populate the new field from `data.message_id`
- `crates/adapters/dydx/src/websocket/parse.rs` — `parse_orderbook_snapshot`/`parse_orderbook_deltas`/`parse_orderbook_deltas_with_flag` gained a `message_id: u64` param; all 6 hardcoded `0` sequence args replaced; 3 existing tests updated to assert `.sequence == message_id`
- `crates/adapters/dydx/src/python/websocket.rs` — 3 match arms + batch loop's 2 internal calls updated to destructure/pass `message_id`
- `crates/adapters/dydx/src/data.rs` — same treatment for the native (non-PyO3) `DydxDataClient::handle_ws_message` path (found necessary during implementation, not in original task list)
- `troll/dydx_collector/client.py` — new `DydxClient.request_orderbook_snapshot()` wrapper, converts pyo3-native response via `OrderBookDeltas.from_pyo3()`
- `troll/dydx_collector/collector.py` — new `_last_sequence`, `_resync_buffers`, `_resync_tasks` state; new `_apply_or_flag_gap`/`_resync_sequence_gap` methods; `_on_data_unsafe` and `_second_loop` updated to route through/skip resyncing markets
- `troll/dydx_collector/tests/test_collector_resilience.py` — new `_seq_deltas` helper, `_FakeSnapshotClient`, and 3 new tests covering gap detection, full resync flow, and `_second_loop`'s resync skip

## Change Log

- 2026-07-02 — Story created from the `brainstorm-orderbook-data-quality-2026-07-02` brainstorming session's locked MoSCoW (Must items 1-4). Status → ready-for-dev.
- 2026-07-02 — Implemented all 5 tasks: threaded dYdX's `message_id` into Nautilus's existing (previously-hardcoded-to-0) `OrderBookDelta.sequence` field across both the PyO3 and native Rust adapter paths; added exact per-market gap detection, resync-mode buffering, and REST-snapshot-plus-idempotent-replay to the Python collector. Found and fixed one missed replacement site and one incorrect pyo3/Cython type assumption during implementation. 331 Rust tests + 165 Python tests passing (1 pre-existing unrelated failure). Status → review.
- 2026-07-03 — Adversarial code review (8-angle parallel finder + direct verification, `troll/dydx_collector/collector.py` only, no Rust changes needed). One theory 4 finder angles converged on ("`message_id` is connection-global, not per-market") was checked against the brainstorm session's original docs+fixtures research and REFUTED — that assumption was already deliberately verified, not an oversight. Five real, confirmed bugs found and fixed:
  1. `_resync_book` (crossed-book watchdog) and `_unsubscribe` resubscribe/unsubscribe without resetting `self._last_sequence[iid]` — the fresh post-resubscribe snapshot's restarted `message_id` almost always fails the gap check against the stale value, misfiring a redundant sequence-resync stacked on top of the resubscribe that just ran. Fixed: both paths now pop the stale entry.
  2. `_apply_or_flag_gap` wrote `self._last_sequence[iid] = sequence` *before* the delta-apply loop instead of after — a mid-batch `apply_delta()` exception (silently swallowed by `_on_data`'s outer try/except) would leave the book partially applied while the tracker claimed the batch fully landed, masking real corruption. Fixed: write moved to after the loop, only on success.
  3. `run()`'s shutdown `finally` block never cancelled `self._resync_tasks` (only the six fixed loop tasks) — a resync task mid-retry-loop at shutdown/restart time leaked indefinitely against a disconnected client. Fixed: now cancelled alongside the fixed tasks.
  4. `_apply_or_flag_gap` indexed `data.deltas[-1]` unconditionally; confirmed via `crates/adapters/dydx/src/python/websocket.rs`'s `OrderbookUpdate` arm (unlike the sibling `OrderbookBatch` arm, it has no `!deltas.is_empty()` guard) that an empty-content update can reach Python as a zero-delta `OrderBookDeltas`, which would `IndexError`. Fixed in Python only (no Rust change): early-return no-op on empty `data.deltas`.
  5. `_resync_sequence_gap`'s REST-retry loop was a fixed 1s-forever retry with no cap, compounding unbounded `_resync_buffers[iid]` growth during a prolonged outage (conflicts with `troll/CLAUDE.md`'s MEM-02). Fixed: capped exponential backoff (1s → 30s max), matching the existing pattern in `main()`.

  Two lower-priority cleanup findings noted but not acted on (out of scope for this fix pass): `self._last_sequence` duplicates state `OrderBook.sequence` already tracks (a larger refactor, deferred); Rust-side `message_id` doc-comment triplication across the 3 enum variants (cosmetic). Re-verified after fixes: 331 Rust tests unchanged/passing, 165 Python tests passing (same 1 pre-existing unrelated failure), 0 new `ruff` findings (confirmed via `git stash` diff against pre-fix baseline). Status → done.
