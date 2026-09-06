---
status: in-progress
trigger: "why do i get crossed book warning constantly in the logs do a deep dive and create a plan to fix it"
created: 2026-09-04
updated: 2026-09-04
standard: "troll/CLAUDE.md DATA-02 -- no mysteries in data ingestion, detect+recover is not sufficient, root cause required"
---

## Symptoms

- expected: dYdX order book reconstruction in `troll/dydx_collector/collector.py` stays consistent with the venue at all times.
- actual: `_second_loop` frequently logs "Crossed book" (bid >= ask) for BTC/ETH/and other liquid instruments. Escalates to a CRITICAL + forced resubscribe (`_resync_book`) if it persists past `_CROSSED_RESYNC_NS`.
- user's bar (explicit, repeated): fast detection+recovery is NOT an acceptable stopping point. The actual mechanism must be known. See DATA-02 in `troll/CLAUDE.md` (added this session at the user's request, codifying this standard permanently).

## Two unrelated timing bugs found and fixed along the way (both verified, both DONE)

These do not explain *why* a desync happens, only whether we detect/recover from one promptly. Both shipped and confirmed working in production.

1. **`_CROSSED_RESYNC_NS` 15s -> 3s.** Once a crossing is confirmed real, waiting 15s before forcing a resubscribe was pure downside -- a real desync never self-heals from more deltas alone. Verified: episodes now resync in ~3.0-3.1s consistently.

2. **`_flush_loop`'s periodic Parquet write was blocking the asyncio event loop.** Every `flush_interval_seconds` (60s default), `_flush_once()` called `self._catalog.write_data()` synchronously on the event loop thread -- real disk I/O (Arrow/zstd), taking multiple seconds, during which `_second_loop`'s crossed-book detection couldn't run at all.
   - **First attempt was WRONG and is an object lesson in DATA-02's "don't declare victory" clause:** assumed the cause was delta-processing bursts monopolizing the loop during high market volatility. Built `_ingest_loop` (an `asyncio.Queue` + dedicated consumer task with periodic `await asyncio.sleep(0)` yields, decoupling `_on_data` from `_apply_deltas`) to fix it. Deployed, verified stable -- but the staleness canary (see below) kept firing with suspiciously *exact* ~69-71s periodicity, which is not what a market-driven burst looks like. That periodicity matched `flush_interval_seconds` (60s) almost exactly, which is what actually pointed at the real cause.
   - **Actual fix:** `_flush_once` is now `async def`; the write is offloaded via `await asyncio.to_thread(self._catalog.write_data, items)`. The buffer swap (`self._buffer[key] = []`) still happens synchronously on the main thread first, so `self._buffer` (shared mutable state) is still only ever touched from one thread -- no new race introduced. Reuses the `asyncio.to_thread` idiom already established elsewhere in this file (`_liquidity_check_loop`).
   - Verified: canary went silent across multiple full flush cycles post-fix; crossed-book episodes resolve in ~3s with no long-tail outliers since.
   - The `_ingest_loop` change was kept anyway (may still help genuine delta bursts, and the decoupling is harmless) -- not reverted.

## Permanent instrumentation added and KEPT in production (all in `collector.py`)

- Per-side (`_last_bid_delta_ns` / `_last_ask_delta_ns`) last-delta timestamps, included in every "Crossed book" WARNING/CRITICAL line. Confirmed deltas keep flowing on both sides throughout crossed episodes -- ruled out "messages stopped arriving for one side."
- `_second_loop` staleness canary (`_SECOND_LOOP_LAG_WARN_NS = 2s` slack): warns if its own tick arrives later than `snapshot_interval_seconds` + slack. This is what caught the wrong first diagnosis above. Per DATA-02, any detection/recovery loop needs one of these.
- `nautilus_pyo3.init_logging()` now called in `main()` (`level_stdout=WARNING`) -- this collector never went through `TradingNode`/Kernel startup, so Rust's `log` crate was a complete no-op the entire time before this: every `log::error!`/`log::warn!` in the whole dYdX Rust adapter was silently dropped, not filtered -- never emitted. Confirmed via `set_boxed_logger` in `crates/common/src/logging/logger.rs` that this bridges the standard `log` facade (used throughout `crates/adapters/dydx/`) to stdout.
  - Result: zero Rust-side errors fired across many real episodes. Ruled out: JSON deserialize failures, reconnect-replay failures, the `call_python_threadsafe` scheduling-failure path (`crates/core/src/python/mod.rs`) as the cause -- all of those would have logged, and didn't.

**Removed (was itself part of the problem, not the solution):** a raw-per-delta dump gated on crossed state, added mid-investigation to inspect exactly what arrives during an episode. On one high-throughput ETH episode it generated 6803 log lines in <20s and *itself* starved the event loop for ~17s -- a diagnostic distorting the thing it measured. Confirmed useful once (proved a frozen price gets zero subsequent deltas during its whole crossed window), then deleted.

## THE decisive experiment: independent reference WS client

REST-polling cross-checks (dYdX's public indexer REST orderbook) gave the first real evidence this was a genuine local desync and not real venue crossing -- but REST has its own polling lag, so some comparisons were ambiguous (both sides looked "a bit off" vs REST).

Built `crossed-book-artifacts/reference_ws_check.py` (copy of what's running -- pure Python + `aiohttp`, zero shared code with `nautilus_trader`/our Rust client): connects directly to `wss://indexer.dydx.trade/v4/ws`, subscribes to `v4_orderbook` for BTC-USD/ETH-USD independently, maintains its own naive book from raw JSON. Concurrently tails `docker logs -f dydx-collector`; the instant a "Crossed book" line appears, dumps an episode file: what the collector claimed, what this independent client's book shows for the same instrument at that exact moment, and every raw WS message in a rolling 300-message buffer that touched either frozen price.

Launched 2026-09-04 14:37. **Still running as of this writeup** (background process, will die if the machine/session ends -- see "To resume" below).

### Results across 57 captured episodes (see `crossed-book-artifacts/episodes_summary_2026-09-04.log`)

| | count | meaning |
|---|---|---|
| Reference client MATCHES collector's (wrong) reading | 16 | genuine venue-side crossing -- dYdX's own served data is crossed; both independent clients correctly reproduce it |
| Reference client DIVERGES (shows correct data collector doesn't) | **41** | **our own pipeline provably lost/failed to apply data the venue actually sent** |

**41 of 57 (72%) are our own pipeline's fault, not the venue's.** This is the majority cause, not an edge case -- raises the priority of finding it precisely.

### Byte-level proof of category 2 (`example_pipeline_bug_episode.json`, BTC-USD, 14:40:39)

```
collector claimed:  bid=79271.000000  ask=79269.000000  (crossed)
reference client:   bid=79271         ask=79273         (correctly ordered)
```

Reference client's raw message log for that exact price:
```
14:40:38.62  {"asks":[["79269","0.063"]]}   <- ask level added
14:40:39.17  {"asks":[["79269","0"]]}        <- SAME level deleted, 551ms later
```

dYdX sent the removal. An independent connection received it and correctly moved its ask to 79273. **Our collector's book never reflected it** -- provably wrong, provably not the exchange's fault. Cross-referenced against the earlier raw-delta-dump finding (zero parsed deltas ever reached our own Python callback for a frozen price during a crossed window, from the pre-removal investigation) -- combined, this means the delete message existed at the source and never reached our own pipeline's callback. Narrows the cause to exactly two possibilities:
1. A connection-specific transport-level loss between dYdX and *our specific* WS connection (not shared by other clients).
2. A silent failure inside the Rust WS handler that drops a message without logging -- argued against (not ruled out) by an audit of `crates/adapters/dydx/src/websocket/handler.rs`: every deserialize/dispatch path found either succeeds or explicitly `log::error!`/`log::warn!`s; found no silent-drop code path.

### Confirmed example of category 1 (`example_genuine_venue_crossing_episode.json`, ETH-USD, 14:41:03)

```
collector: bid=2451.7  ask=2450.4   |   reference: bid=2451.7  ask=2450.4   (MATCH)
```

Raw messages show dYdX itself sent a new ask order (`2450.4`) *below* the current resting bid (`2451.7`) -- the venue's own served book was internally crossed. Two independent clients reproduce the identical crossed state from identical wire data. Nothing to fix here; current behavior (skip storage, wait, escalate-if-sustained) is already correct for this category.

## Open question: pin down category 2's exact locus (why 72% of episodes are us, specifically)

Three options were laid out for the user, with real scope/effort attached to each:

- **Option A -- TLS-intercept our own collector's connection.** Would give literal byte-level proof of what our specific connection received. **Blocker found this session:** `crates/network/src/tls.rs` hardcodes the trust store to `webpki_roots::TLS_SERVER_ROOTS` with no override point -- no custom-CA injection, no `KeyLogFile`/`SSLKEYLOGFILE` wiring exists anywhere in `crates/` (grepped, confirmed absent). Making this work requires patching `crates/` (forbidden by FORK-01/FORK-02 as a hard rule, not a preference) -- this is a real policy exception to request, not just an engineering task. Scope if approved: new proxy container/routing in docker-compose, careful testing on a non-production path first (real risk to the live collector's connectivity while testing), revert the crates/ patch after. Hours of work, medium risk.
- **Option B -- stop here.** Zero further work. Document category 2 as "proven our pipeline, narrowed by elimination to most-likely a connection-specific transport hiccup, not proven at the byte level." Given DATA-02 and the user's explicit rejection of residual mysteries, this was NOT the chosen path, but is documented for completeness.
- **Option C -- run 2-3 independent reference clients against EACH OTHER (not just vs. collector).** No `crates/` changes, no proxy, no CA trust issues -- pure extension of `reference_ws_check.py` already running. If independent connections occasionally disagree with each other too, that's evidence of a general per-connection quirk in dYdX's public infra (nautilus isn't uniquely at fault). If independent connections always agree with each other but our collector still diverges from all of them, that isolates the fault specifically to nautilus's Rust client/our pipeline. Statistical, not byte-level, but real evidence, low risk, ~1 hour reusing existing code. **Recommended next step, not yet built** -- this is where the user was mid-decision when this session paused.

## To resume

1. Reference client may or may not still be running (`ps aux | grep reference_ws_check`) -- it lives only in `/tmp` scratchpad and does NOT survive a session/machine restart. If dead, restart from the durable copy: `nohup uv run --project /home/mrqdt/code/nautilus_trader_fork python3 troll/.planning/debug/crossed-book-artifacts/reference_ws_check.py > /tmp/reference_ws_check.out 2>&1 &` (note: it hardcodes its `OUT_DIR` to next to itself, and its `docker_log_watcher` shells out to `docker logs -f dydx-collector` directly -- both still valid from this new location).
2. More episodes will keep accumulating in `reference_check_out/` next to wherever the script runs from -- re-run the tally script shown in-session (glob `episode_*.json`, compare `collector_claim` vs `reference_book` per instrument) for an updated match/diverge count before deciding next steps.
3. Next decision point: build Option C (recommended), or get explicit user sign-off for a scoped, temporary `crates/` exception to run Option A properly.
4. `troll/CLAUDE.md`'s DATA-02 rule (added this session) governs the standard of proof for closing this out -- don't mark this resolved without either a confirmed root cause or hard evidence it's outside our control.

## Files changed this session (already committed to working tree, not yet git-committed as of this writeup)

- `troll/dydx_collector/collector.py` -- `_CROSSED_RESYNC_NS` 15s->3s, `_ingest_loop`/`_ingest_queue`/`_process_data` split, `_second_loop` staleness canary, `_flush_once` async + `to_thread` offload, per-side delta timestamps, `nautilus_pyo3.init_logging()` call.
- `troll/dydx_collector/tests/test_collector_resilience.py` -- updated/added tests for the `_on_data`/`_ingest_loop` split.
- `troll/CLAUDE.md` -- new DATA-02 rule.
- This file + `crossed-book-artifacts/` -- investigation record and diagnostic tooling.
