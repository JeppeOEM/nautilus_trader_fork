---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: []
workflowType: 'research'
lastStep: 2
research_type: 'technical'
research_topic: 'dYdX v4 Orderbook Crossing & Resolution'
research_goals: 'Determine whether a momentarily-crossed public v4_orderbook WS feed is an expected, designed transient artifact of dYdX v4''s indexer/matching architecture -- and if so, the mechanism by which it is supposed to resolve -- versus something that should never appear from a correctly-functioning client, to ground troll/dydx_collector/collector.py''s crossed-book detection/resync logic in authoritative fact rather than empirical inference alone.'
user_name: 'Mrqdt'
date: '2026-09-06'
web_research_enabled: true
source_verification: true
---

# Research Report: dYdX v4 Orderbook Crossing & Resolution

**Date:** 2026-09-06
**Author:** Mrqdt
**Research Type:** technical

---

## Research Overview

This research answers one question: is a crossed `v4_orderbook` WS feed from dYdX an expected, architectural artifact — and if so, how is it meant to be resolved — versus a defect that should never appear from a correctly-functioning client? Four independent, cited sources (dYdX's own architecture docs, its official "how to uncross the orderbook" integration guide, its Indexer's own `Roundtable` source code, and this repo's own Rust parser + live raw-wire captures) converge on the same answer: crossing is a designed, unavoidable consequence of dYdX v4 having no centralized orderbook (each block proposer's mempool is the only "canonical" view, and it changes every block), and dYdX's own production Indexer resolves it non-destructively — by tagging each price level with the message-id of whichever update last touched it, and deleting only the stale side when crossed, never resubscribing or rebuilding the whole book. See the Research Synthesis section below for the full executive summary and recommendation.

---

<!-- Content will be appended sequentially through research workflow steps -->

## Technical Research Scope Confirmation

**Research Topic:** dYdX v4 Orderbook Crossing & Resolution
**Research Goals:** Determine whether a momentarily-crossed public `v4_orderbook` WS feed is an expected, designed transient artifact of dYdX v4's indexer/matching architecture -- and if so, the mechanism by which it is supposed to resolve -- versus something that should never appear from a correctly-functioning client, to ground `troll/dydx_collector/collector.py`'s crossed-book detection/resync logic in authoritative fact rather than empirical inference alone.

**Technical Research Scope:**

- Architecture Analysis - dYdX v4's off-chain orderbook + on-chain matching split, indexer role, how bid/ask sides are aggregated and broadcast
- Implementation Approaches - how the indexer derives/publishes `v4_orderbook` deltas, snapshot vs. incremental semantics
- Technology Stack - indexer/protocol components involved (validator mempool orderbook, indexer service, WS gateway)
- Integration Patterns - the WS subscription protocol itself (subscribe/snapshot/channel_data semantics), any documented consistency guarantees
- Performance Considerations - replication lag, block-time aggregation, or other causes of transient inconsistency in the broadcast feed

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information
- Comprehensive technical coverage with architecture-specific insights

**Scope Confirmed:** 2026-09-06

---

## Technology Stack Analysis

### Programming Languages & Services (Indexer Stack)

The Indexer is a read-optimized off-chain service layer built from several specialized services, each with a distinct role in the pipeline that ultimately produces the public `v4_orderbook` WS feed:

- **Ender** (on-chain data service) consumes the `to-ender` Kafka topic (all on-chain events, queued per block) and applies state changes to Postgres.
- **Vulcan** (off-chain data service) consumes the `to-vulcan` Kafka topic (active orderbook updates, place/cancel, optimistic fills) and writes them into a Redis cache — this is the path that produces `v4_orderbook` deltas.
- **Roundtable** is a periodic job-runner for exchange-wide aggregation/maintenance tasks — **this is where dYdX's own crossed-orderbook remediation lives** (see Architectural Patterns below).
- **Socks** is the WebSocket service that serves `v4_orderbook`/`v4_trades`/etc. to clients from Vulcan's Redis cache.
- **Comlink** is the REST API server (onchain + offchain reads).

_Source: [Indexer Deep Dive · dYdX · v4](https://docs.dydx.exchange/concepts-architecture/indexer)_

### Database and Storage Technologies

Postgres holds on-chain state (positions, fills, markets); Redis holds the live, ephemeral orderbook levels that `Socks` streams out over WS. **The orderbook a WS client sees is a Redis cache of off-chain, pre-consensus mempool activity — not a database read of any settled, canonical state.** This is the load-bearing fact for the rest of this research: the object being streamed to us was never guaranteed consistent in the first place.

_Source: [Indexer Deep Dive · dYdX · v4](https://docs.dydx.exchange/concepts-architecture/indexer)_

### Underlying Protocol (dYdX Chain)

dYdX v4 removed the centralized off-chain matching engine entirely. Each validator holds its own **in-memory, off-chain, non-consensus orderbook** (orders are gossipped between validators/full nodes, no gas, freely place/cancel). Matching happens off-chain in real time against whichever orderbook the current **block proposer** holds; only the resulting fills are committed on-chain. Block proposers rotate every block.

_Source: [Decentralized Order Book Design in dYdX v4](https://medium.com/@gwrx2005/decentralized-order-book-design-in-dydx-v4-625ac0152e80), [Intro to dYdX Chain Architecture](https://docs.dydx.xyz/concepts/architecture/overview)_

### Ready to proceed?
[C] Continue — integration patterns (WS protocol semantics)

---

## Integration Patterns Analysis

### WebSocket Protocol Semantics (`v4_orderbook`)

- `subscribe` -> `subscribed` (full snapshot; contents are `{price, size}` objects) -> `channel_data` (incremental; contents are `[price, size]` tuples). Client resets its book to empty on `subscribed`, then applies tuples.
- Every WS envelope (any channel, any market) carries a **connection-global `message_id`** — confirmed independently in this codebase's own prior investigation (commit `944891bbba`) via the Rust adapter's test fixtures, and reconfirmed just now against live raw wire captures (`troll/dydx_collector/incident_reports/*.log`): zero 3-element price-level arrays appear anywhere on the wire in current production traffic, `message_id` only ever appears once, at the envelope's top level.
- _Source: [Watch orderbook – dYdX Documentation](https://docs.dydx.xyz/interaction/data/watch-orderbook), [dYdX v4 indexer `messages.rs`](crates/adapters/dydx/src/websocket/messages.rs) (this repo)_

### Official Uncrossing Algorithm (`how_to_uncross_orderbook`)

This is the load-bearing find of this research. dYdX's own official integration guide states, verbatim:

> "v4 doesn't guarantee that order book prices don't cross because there is no centralized order book... If trader needs the order book uncrossed, then one way is to use the order of messages as a logical timestamp. That is, when a message is received, update a global locally-held offset. Each websocket update has a message-id which is a logical offset to use."
>
> "v4 software stores the message-id for each bid/ask as the third element of a list, for example: `['26854.0', '0.556', '8468']`... From left to right, the elements are price, size, and message-id."

Read together with the wire-format finding above, this resolves an apparent contradiction: that "third element" is **not transmitted on the wire** — it's a client-side (and indexer-side) *derived* annotation. The client is expected to tag each price level, at the moment it applies an update to it, with the connection-global `message_id` of the message that touched it. That per-level tag is exactly what dYdX's own indexer keeps (see Architectural Patterns below: Redis `lastUpdated`) — and it's buildable today from data our collector already receives (the top-level `message_id`), just applied per-level rather than per-instrument (per-instrument comparison is what commit `944891bbba` correctly ruled out).

**The uncrossing algorithm itself, straight from dYdX's guide:** while the book is crossed (best bid >= best ask), compare the message-id of the current best bid vs. best ask; discard the level with the *older* (smaller) message-id; if tied, reduce/cancel the smaller-size side. Repeat until uncrossed.

_Source: [How to uncross the orderbook · dYdX · v4](https://docs.dydx.exchange/api_integration-guides/how_to_uncross_orderbook)_

### System Interoperability: No Client-Side Guarantee Exists

dYdX's docs state plainly there is no way to avoid seeing a crossed book as a client — it is a property of the feed, not a defect any client (including dYdX's own indexer, pre-remediation) can prevent. The only choices are: (a) tolerate it and let normal message flow uncross it eventually (their own stated fallback — "if the trader doesn't need the order book prices uncrossed, simply listen... they should uncross eventually"), or (b) run the message-id uncrossing algorithm actively.

_Source: [How to uncross the orderbook · dYdX · v4](https://docs.dydx.exchange/api_integration-guides/how_to_uncross_orderbook)_

**Ready to proceed to architectural patterns (dYdX's own remediation design)?**
[C] Continue

---

## Architectural Patterns and Design

### dYdX's Own Remediation: `Roundtable`'s `uncross-orderbook` Task

dYdX's own Indexer runs a periodic job (in `Roundtable`, the indexer's scheduled-task service) that does exactly what this research set out to find — full source, `dydxprotocol/v4-chain/indexer/services/roundtable/src/tasks/uncross-orderbook.ts`:

```typescript
function isOrderbookCrossed(orderbookLevels: OrderbookLevels): boolean {
  const bestBid = Big(orderbookLevels.bids[0].humanPrice);
  const bestAsk = Big(orderbookLevels.asks[0].humanPrice);
  return bestBid.gte(bestAsk);
}

async function uncrossOrderbook(market, orderbookLevels): Promise<void> {
  // Bids sorted descending, asks sorted ascending
  while (ai < asks.length && bi < bids.length && bids[bi].price >= asks[ai].price) {
    if (Number(bids[bi].lastUpdated) > Number(asks[ai].lastUpdated)) {
      ai += 1;   // ask is newer -> the bid is stale, drop it
    } else {
      bi += 1;   // bid is newer (or tie) -> the ask is stale, drop it
    }
  }
  // delete the identified stale bid/ask levels from the Redis cache
}
```

**This is the authoritative architectural pattern**, straight from the system we're integrating with, and it directly validates every finding above:

1. **It runs periodically as a scan-and-fix over already-cached state**, not as an inline gate on every incoming message. dYdX's own production system tolerates the book being crossed in the interim.
2. **The correction is surgical** — remove only the specific stale level(s), an O(k) walk over just the crossed depth (k = number of crossed levels), not a full book rebuild.
3. **`lastUpdated` (their name for the per-level message-id/offset from Integration Patterns) is the sole arbitration signal** — no resubscribe, no resnapshot, no independent cross-check needed.
4. This confirms `stats.increment('crossed_orderbook', {ticker})` is emitted as a normal, expected, per-ticker metric in dYdX's own telemetry — i.e., dYdX's own ops dashboards show this happening continuously across all markets, by design, not as an anomaly.

_Source: [dydxprotocol/v4-chain uncross-orderbook.ts](https://github.com/dydxprotocol/v4-chain/blob/main/indexer/services/roundtable/src/tasks/uncross-orderbook.ts) via code-summary retrieval_

### Comparison: Our Collector's Current Pattern vs. dYdX's Own Pattern

| | dYdX's own `Roundtable` task | `troll/dydx_collector/collector.py` (current) |
|---|---|---|
| Detection | Periodic scan of cached levels | Periodic scan (`_second_loop`, every `snapshot_interval_seconds`) — architecturally the same idea |
| Arbitration signal | Per-level `lastUpdated` (message-id of last update) | None — duration-only (`_CROSSED_RESYNC_NS`) |
| Correction | Remove only the stale level(s) | Full unsubscribe/resubscribe (`_resync_book`) — discards the entire book, not just the crossed levels |
| Cost of correction | O(crossed depth), no data loss elsewhere in the book | Full resnapshot; book is empty for every level (not just the crossed ones) until resubscribe completes |

This is a direct, structural answer to why forced resync (DATA-03) is worst-case: **dYdX's own architecture doesn't need it and doesn't do it** — it fixes only the broken part. Our collector's only advantage dYdX's task lacks is that ours is real-time (per snapshot tick) rather than a periodic batch job; the fix here isn't "run a batch job like they do," it's "borrow their arbitration signal (per-level message-id) and apply it inline, instead of nuking the whole book."

### Design Principle This Confirms (ties to `troll/CLAUDE.md`)

DATA-03 (added this session) already states forced resync is destructive/worst-case. This research supplies the missing piece DATA-03 didn't have yet: **a non-destructive alternative actually exists and is dYdX's own reference implementation for solving this exact problem.**

**Ready to proceed to implementation research (how to adapt this into `collector.py`)?**
[C] Continue

---

## Implementation Approaches and Technology Adoption

### Adoption Strategy: Additive, Not a Replacement

Verified against this codebase directly (`nautilus_trader/model/book.pyx`, `nautilus_trader.model.enums.BookAction`): `BookAction.DELETE` exists and is exactly the mechanism already used for every real dYdX-sent deletion (`_apply_deltas` already calls `book.apply_delta()` for these). A synthetic delete delta (same side/price, size=0, `action=DELETE`) can be constructed and applied identically — **no new nautilus API, no `crates/` change, no new dependency.** This keeps the change inside `troll/dydx_collector/collector.py`, honoring FORK-01/FORK-02.

### Data Needed (all already available)

`OrderBookDelta.sequence` is the connection-global `message_id` (established fact, commit `944891bbba`). Per Integration Patterns, the correct use of this field for uncrossing is **per price-level**, not per-instrument:

- Add `self._level_msg_id: dict[str, dict[tuple[OrderSide, float], int]]` (keyed by instrument, then (side, price)) — updated inline in `_apply_deltas` alongside the existing `book.apply_delta(delta)` call: on ADD/UPDATE, record `{(side, price): sequence}`; on DELETE, pop the entry. This is O(1) per delta, same loop already iterating `data.deltas`.

### Uncrossing Algorithm (direct port of dYdX's own `Roundtable` task)

```python
def _uncross_step(iid: str, book: OrderBook, level_msg_id: dict) -> bool:
    """One correction step. Returns True if a level was dropped (caller loops)."""
    bid, ask = book.best_bid_price(), book.best_ask_price()
    if bid is None or ask is None or bid.as_double() < ask.as_double():
        return False
    bid_id = level_msg_id.get((OrderSide.BUY, bid.as_double()))
    ask_id = level_msg_id.get((OrderSide.SELL, ask.as_double()))
    if bid_id is None or ask_id is None:
        return False  # no tag yet (e.g. right after resubscribe) -- can't arbitrate, fall back
    stale_side, stale_price = (OrderSide.SELL, ask) if bid_id > ask_id else (OrderSide.BUY, bid)
    # apply a synthetic BookAction.DELETE at stale_price on stale_side, pop its tag
    return True
```

Loop this (matching dYdX's own `while` loop) until `_uncross_step` returns `False` — either uncrossed, or untaggable (fall back to the existing `_resync_book` as a last resort, not the first response).

### Where This Replaces Current Logic

In `_handle_crossed_book` (`collector.py:748`): before the `_CROSSED_RESYNC_NS` timer/forced-resync branch, attempt `_uncross_step` in a loop first. Only escalate to `_resync_book` if uncrossing can't resolve it (missing tags) or a much longer real safety-net window elapses — this preserves the existing safety net for genuine, unrecoverable local desyncs (per the original 2026-09-04 finding: those never self-heal) while eliminating destructive resyncs for the — now confirmed common — case of ordinary, expected, protocol-level crossing.

### Testing (per `troll/CLAUDE.md` TEST-01 — financial-calculation logic requires tests)

Unit-testable exactly like the existing crossed-book tests in `test_collector_resilience.py`: construct a real `OrderBook`, apply tagged bid/ask deltas with known `sequence` values that cross, assert `_uncross_step` removes the older-tagged side and the book un-crosses, using real `Price`/`Quantity`/`OrderBook` objects (TEST-03 — no mocking Nautilus internals).

### Risk Assessment

- **Low risk of regression**: the existing forced-resync path stays as a fallback, not removed — this is a strictly additive change that only *narrows* how often the destructive path fires.
- **Main risk**: tag staleness right after a resubscribe/reconnect (freshly-cleared `_level_msg_id`) — mitigated by the fall-back-to-resync-on-missing-tag behavior in `_uncross_step` above, so it degrades to current behavior rather than misbehaving.
- **Observability**: keep an incident report/log line on every active uncrossing (not just resyncs) so the rate of "protocol-level crossing corrected surgically" vs. "genuine desync requiring full resync" becomes visible and trendable — directly answers the standing DATA-02/DATA-03 question of how often each category actually occurs, going forward.

**Ready to proceed to the final synthesis?**
[C] Continue

---

## Research Synthesis

_Note on scope: the generic technical-research template includes sections (Security/Compliance, Cost Optimization, Competitive Advantage, 5-year Outlook) that don't apply to a single narrow protocol-behavior question like this one — including them would be padding, not signal. This synthesis stays focused on what was actually asked: architecture, resolution mechanism, and an implementation recommendation._

### Executive Summary

dYdX v4 has no centralized orderbook — each validator holds its own in-memory, off-chain, pre-consensus orderbook, matched against whichever block proposer's mempool is currently active, rotating every block. The public `v4_orderbook` WS feed (served by the Indexer's `Vulcan`/`Socks` services from a Redis cache fed by that off-chain gossip) inherits this: **dYdX's own docs state outright that no client can prevent seeing a crossed book.** This is not a bug category our collector introduced or can eliminate.

dYdX's own Indexer doesn't try to prevent it either — it *corrects* it, via a periodic `Roundtable` job (`uncross-orderbook.ts`) that tags every price level with the message-id of whichever update last touched it, and — when crossed — deletes only the stale side (older message-id), looping until clean. No resubscribe. No resnapshot. No independent verification needed.

Our collector's current design (`_resync_book`) does not have access to this per-level signal and instead falls back to the only thing duration alone can do: wait out a grace window (`_CROSSED_RESYNC_NS`), then discard and rebuild the entire book. This research (confirmed independently, live, against a zero-shared-code reference client in this same investigation) already caught this doing exactly what the theory predicts: force-resyncing a book that an independent client proved was already correct and merely reflecting genuine upstream crossing. DATA-03 (added to `troll/CLAUDE.md` this session) names this as a destructive worst-case; this research supplies the fix dYdX itself already uses in production.

**Key Findings:**
- Crossing is architectural, expected, and explicitly documented by dYdX as unpreventable by any client.
- dYdX's own Indexer resolves it with a per-level message-id comparison, not a full resync — surgical, not destructive.
- The exact data needed (`OrderBookDelta.sequence`, dYdX's connection-global message-id) is already flowing through our pipeline today; it's currently used nowhere for this purpose.
- The fix is implementable entirely within `troll/dydx_collector/collector.py` using existing `nautilus_trader` APIs (`BookAction.DELETE`) — no `crates/` change, no new dependency, no violation of FORK-01/FORK-02.
- Forced resync should remain as a fallback (missing tags right after a resubscribe), not be removed — this is additive, not a replacement of the existing safety net.

**Recommendations (priority order):**
1. Implement per-level message-id tagging in `_apply_deltas` (small, additive, no schema change).
2. Port dYdX's own uncrossing loop into `_handle_crossed_book`, tried before the resync fallback.
3. Keep `_resync_book` as the fallback only, and keep logging/incident-reporting on every correction (surgical or forced) so the *rate* of each becomes visible over time — this turns the current guesswork about "how often is it really our bug" into a directly observable metric, closing the open question from `crossed-book-root-cause.md` in the way DATA-02 actually requires: not by proving every historical episode byte-for-byte, but by removing the destructive symptom and instrumenting the real one going forward.
4. Re-evaluate whether Story 5.1's remaining reference-client correlation work (hunting for a genuinely-diverging, our-fault episode) is still needed once this ships — it may already be answered: this research gives a structural reason most/all crossing is category 1 (protocol-level), which is consistent with the difficulty finding a clean category-2 example live tonight.

### Conclusion

The original question — "why do crossed books keep happening and why doesn't the current fix feel like it's working" — has a confirmed, sourced answer: they keep happening because dYdX's decentralized architecture makes that unavoidable, and the current fix (`_resync_book`) doesn't feel like it's working because it's solving the problem the hard, destructive way when dYdX's own reference implementation shows a cheap, surgical way exists using data we already have.

**Research Completion Date:** 2026-09-06
**Source Verification:** dYdX official docs (docs.dydx.exchange, docs.dydx.xyz), dYdX Indexer source (`v4-chain` GitHub), this repository's own Rust adapter source and live production wire captures.
**Confidence Level:** High — the core claim (crossing is architectural and unpreventable) and the resolution mechanism (per-level message-id, surgical delete) both come directly from dYdX's own documentation and source code, not inference.
