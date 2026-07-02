# Brainstorm Intent: Orderbook Data Quality

## 1. Problem

The dYdX collector's 1s orderbook snapshots can silently contain corrupted data (crossed books, resync artifacts) caused by dropped/out-of-order WS messages, with no mechanism today to detect gaps or distinguish known-cause corruption (a missed message) from unknown-cause corruption (a real bug or bad venue data). Goal: always get correct data into the 1s snapshots, and reliably flag ticks/snapshots that are wrong due to external/venue causes.

## 2. Key technical facts

- dYdX WS envelope carries `message_id` (monotonic, **per-subscription/per-market channel**, not connection-global) + version string on every orderbook message. Confirmed via docs + fixtures (each channel starts at `message_id=1` independently).
- Rust `handler.rs` already tracks a `book_sequence` map per market and detects regression/dupes via `message_id<=last_id` (L381-402), but only logs a warning — never checks for **gaps** (`message_id==last_id+1`), never drops/resyncs.
- The sequence field is stripped before reaching Python — `enums.rs`'s `DydxWsOutputMessage::Orderbook*` carries no sequence field. The Python collector currently has zero access to this signal; it relies purely on heuristic crossed-book + staleness checks.
- dYdX orderbook updates are **absolute-per-level** (size replaces, size=0 deletes), NOT relative deltas — confirmed in `parse.rs` L668-720 (no read-modify-write). A missed message only staleifies the specific levels it would have touched: bounded, self-healing damage, not systemic corruption.
- dYdX's REST orderbook snapshot has **no anchor/sequence field** (unlike Binance's `lastUpdateId`) — just bids/asks + a coarse `isoTimestamp`. Precise Binance-style anchored buffer+replay is not directly possible.
- Because updates are absolute-per-level, replay is **idempotent**: reapplying an already-included update is a harmless no-op. This sidesteps the missing-anchor problem.

## 3. Design: resync mechanism

1. **Gap detection**: per-market, check `message_id` **exactly** (`expected == received`), not just regression. Any mismatch = gap.
2. **On gap**: instantly taint that market's book; stop emitting 1s snapshots for it.
3. **Resync**: fetch a REST snapshot as the new base, then replay **all buffered WS messages** from slightly before the REST call onward, with generous overlap. Idempotent replay means overlap is safe — no precise anchor needed.
4. **Bounded raw capture**: keep a small rolling in-memory ring buffer (last ~30-60s) per market. Only flush it to a housekeeping log when a gap/corruption is actually detected — storage cost scales with number of corruption *events*, not connection uptime.
5. **On confirmed corruption**: discard that 1s bar entirely from the Parquet catalog (leave a genuine gap, not a flagged-but-present row) so ML/backtest naturally skip forward. Separately log full orderbook context (before + after, timestamped) to the housekeeping log for postmortem diagnosis.
6. **Trade-print reconciliation**: run as a continuous free cross-check against orderbook consistency (independent signal, not gap-dependent).
7. **Crossed-book escalation**: if a crossed book fires in steady state (no known gap, not mid-reconnect, not mid-resync), this is a **CRITICAL/high-danger** event — must be loud, logged to a separate high-severity event stream (not mixed with routine gap-triggered bar discards), since it means either a local bug or an undetectable bad-data send from dYdX.
8. Single-threaded asyncio collector loop guarantees no race between taint-set, buffer-replay, and snapshot-resume — resync is safe without explicit locking.

## 4. Scope (MoSCoW)

**Must**
- [ ] Per-market exact sequence gap detection (`message_id` expected==received)
- [ ] Snapshot+buffer+replay resync on gap (REST snapshot + idempotent buffered WS replay with overlap)
- [ ] Taint affected market immediately on gap; suppress 1s snapshot emission until resync completes
- [ ] Discard corrupted 1s bar entirely from the Parquet catalog (genuine gap, not a flagged row)
- [ ] Bounded rolling ring buffer (~30-60s) per market, flushed to housekeeping log only on detected corruption
- [ ] Full orderbook context (before+after, timestamped) logged for postmortem on every detected corruption
- [ ] Crossed-book steady-state detection escalated as CRITICAL, separate high-severity log stream from routine gap-discard events (escalated from Should → Must)

**Should**
- [ ] Trade-print reconciliation as a continuous free cross-check

**Could**
- (none identified in session)

**Won't**
- Blind resubscribe on gap detection — book self-heals since updates are absolute-per-level, not relative deltas; a missed message only staleifies the specific levels it touched
- Cooldown-based healing heuristic — rejected in favor of the first-principles resync mechanism above
- 24/7 immutable raw WS capture — would cause TB-scale storage bloat; replaced by the bounded ring buffer
- Precise anchor-based (Binance-style) buffer+replay — not possible: dYdX's REST snapshot has no anchor/sequence field

## 5. Open questions / non-goals

- No anchor field on dYdX REST snapshots means resync correctness depends entirely on idempotent replay with generous overlap — exact overlap window size is not specified in the session and needs to be defined during story creation.
- "Could" tier is empty; nothing was deferred as optional — treat the Must/Should list above as the full backlog.
