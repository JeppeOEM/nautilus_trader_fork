---
stepsCompleted: [1, 2, 3, 4, 5, 6]
workflowType: 'research'
research_type: 'technical'
research_topic: 'Collector + ranking engine performance and scalability for multi-exchange, more-coins growth'
research_goals: 'Find what limits throughput today, what to fix first, and what architecture supports more exchanges and coins'
user_name: 'Mrqdt'
date: '2026-09-19'
web_research_enabled: true
source_verification: true
note: 'Run autonomously at user request: scope confirmation gate skipped, steps 2-6 condensed into one pass.'
---

# Technical Research: Scaling the Collector and Ranking Engine

## 1. Current state (from code, not assumptions)

| Fact | Where |
|---|---|
| One asyncio process, one thread for all book maintenance, snapshotting, buffering, Redis publish | `dydx_collector/collector.py` (`_ingest_loop`, `_second_loop`) |
| 29 instruments configured; cap was raised 20 -> 30 | `troll/config.toml` |
| Every snapshot for every coin is JSON-encoded into ONE Redis pub/sub message per second | `_publish_snapshot_batch`, `snapshots:raw` |
| Ranking engine is a single process holding ~15 indicator objects per coin, subscribed to that one channel | `ranking_engine/engine.py` `_ingest_snapshot_batch`, `_redis_listener` |
| Ranking metrics persisted to SQLite (WAL), single writer | `ranking_engine/metrics_store.py` |
| Parquet writes use one `write_data()` call per (datatype, instrument) per 60s flush, in `to_thread` | `_flush_once` |
| Past incident: box at load 10.85 on 2 vCPU, `_second_loop` lag 2-21s, ranking engine OOM loop | memory: nifelheim resource exhaustion, 2026-09-12 |
| Dev box now: 8 cores, 15 GiB. Prod box (nifelheim): 2 vCPU / 3.7 GB (as of 2026-09-12; verify if resized) | - |

Key point: the Python GIL means this design uses roughly **one core**, no matter how many cores exist. Adding coins or exchanges to the same process only makes that one core hotter. The 2026-09-12 incident (collector at 105% CPU) is exactly that ceiling.

## 2. Where the time goes (ranked by likely cost, from code reading; NOT profiled)

1. **Book maintenance in Python.** `_apply_deltas` loops every delta in Python and calls `.as_double()` twice per delta for a dict of level->msg_id (`level_msg_id`). This is per-message, per-level Python work on the hot path. Cost scales with message rate, i.e. coins x exchanges.
2. **Per-second snapshot build.** For each coin: `book.bids()[:20]`, `book.asks()[:20]` materialise Python objects, then 4 list comprehensions with `.price.as_double()` / `.size()`. ~80 attribute calls per coin per second, then `json.dumps` of all of it.
3. **Buffering deltas nobody stores.** `_process_data` appends every `OrderBookDeltas` to `_buffer` before `_flush_once` discards those for coins not in `_delta_store`. That holds up to 60s of deltas for all coins in RAM only to throw most away. Cheap fix: check `_delta_store` before appending.
4. **Ranking engine per-message recompute.** `_fast_metrics_for` rebuilds a 300-element list, recomputes mids and `statistics.stdev` for every coin on each publish, and OFI is fed 4 `MultiLevelOFI` + 3 `MultiLevelOBI` objects per coin per second. Fine at 30 coins, linear in coins.
5. **Parquet small files.** 29 coins x (snapshot, rollup, mark, index, funding, deltas...) x 1 file per minute = many tiny files -> slow reads for backtest/dashboard and slow prune walks. Compaction is the standard answer (Nautilus catalog has `consolidate_catalog`-style helpers; verify exact API in pinned 1.229.0 before relying on it).
6. **Redis pub/sub as the only bus.** Fire-and-forget, no backpressure: a slow subscriber (ranking engine while the box is starved) is disconnected once its output buffer hits the hard limit (default 32 MB, or 8 MB soft for 60 s) and everything in between is lost. [Redis pub/sub slow-consumer behaviour](https://oneuptime.com/blog/post/2026-03-31-redis-pubsub-slow-consumer-memory/view), [pub/sub fundamentals](https://neuralengineer595.substack.com/p/redis-pubsub-fundamentals). With more exchanges and coins the single-message payload also grows linearly (all coins in one JSON array).

## 3. External constraints that shape the design

- **dYdX indexer WS limits per connection: 32 channels each for orderbook, trades, markets and candles.** [dYdX FAQ/limits](https://docs.dydx.community/dydx-chain-technical-docs/front-end-and-wallets/faq-and-resources). So ~30 coins x orderbook is already at one connection's ceiling; going past ~32 coins requires a second connection (sharding), which fits the earlier diagnosed 2/sec subscribe-limit sharding plan. REST is limited to 100 requests / 10 s per IP. [dYdX rate limits](https://docs.dydx.xyz/concepts/trading/limits/rate-limits). Confidence: medium (limits quoted from a community mirror of the docs; confirm against the live docs before coding).
- **Nautilus has Rust adapters for Binance, Bybit, OKX, Hyperliquid, Kraken** among others. [Nautilus integrations](https://nautilustrader.io/docs/latest/integrations/). Adding an exchange is therefore mostly "instantiate that adapter's PyO3 WS client", not writing a websocket client, consistent with your "use Nautilus built-ins first" rule. Caveat: their PyO3 client surfaces differ per venue, so `client.py` needs a per-venue wrapper. Confidence: medium; check that each venue's PyO3 data client is actually exposed in 1.229.0.
- **Process-per-exchange with asyncio inside each is the standard pattern** for collectors that need to use several cores. [asyncio+multiprocessing](https://medium.com/@nbasker/python-asyncio-with-multiprocessing-2595f8ee3f8), [overview](https://www.pyquantnews.com/free-python-resources/python-in-high-frequency-trading-low-latency-techniques). Generic guidance, low specificity.
- **Redis Streams** add persistence, consumer groups, and resume-from-last-ID, which fixes the lost-messages-on-reconnect problem of pub/sub. [Streams vs pub/sub](https://codingmart.com/redis-streams-vs-pub-sub-a-performance-perspective/), [pub/sub with persistence](https://oneuptime.com/blog/post/2026-03-31-redis-pubsub-with-persistence/view).

## 4. Recommendations

Ordered by value per effort. Nothing here has been measured yet, hence step 0.

**Step 0 - Measure first (half a day, no design risk).** Run `py-spy record` (or `py-spy top`) against the live collector and ranking_engine for 5 min each. Every ranking in section 2 is inferred from code; profile before touching anything. Also log per-loop lag already emitted by `_second_loop`.

**Tier 1 - cheap, low risk, do now**
- Skip buffering deltas for coins not in `_delta_store` (section 2.3). Frees RAM and GC churn immediately.
- Give the deploy box real headroom: the past incidents were host oversubscription, not code. Resize/move off the 2 vCPU box before adding coins; this is the only fix that helps *all* growth. (You previously chose to document rather than fix; this is the reminder that the ceiling still applies.)
- Publish per-coin or per-shard, not one giant array (see Tier 2), or at minimum switch `json.dumps` to `orjson` if it profiles hot. Note your "minimize dependencies" rule: `orjson` is well maintained and high-value, but only justified if the profile shows JSON dominates.

**Tier 2 - structural, needed before the second exchange**
- **One collector process per venue (and per 32-coin shard for dYdX).** Same code, `--venue` and `--shard` args, own catalog partition, own Redis channel/stream key, e.g. `snapshots:raw:dydx:0`. This is the only way to use more than one core, it isolates a misbehaving venue from the rest, and it matches the 32-channel dYdX limit. Docker Compose already runs one service per role, so this is more `docker-compose` entries than new code.
- **Venue-prefixed instrument IDs everywhere.** Nautilus `InstrumentId` already carries the venue (`BTC-USD-PERP.DYDX`), so catalog, Redis payloads, and `metrics.db` keys are mostly ready. Audit the places that assume `.DYDX` (grep for the suffix and `DYDX` constants) before adding a venue.
- **Replace pub/sub with Redis Streams (`XADD` with `MAXLEN ~`)** for `snapshots:raw`. Consumers (ranking engine, data_api, TUI) get resume-on-reconnect and cannot be silently disconnected for being slow. Keep pub/sub only for `rankings:live`, where the latest value is all that matters and a lost message self-heals in 5 s (heartbeat). Cost: consumer code changes in 3 modules. Skip if profiling shows the box is never the bottleneck.

**Tier 3 - only if the profile says so**
- Move snapshot construction (`bids()[:20]`, list comps) into a tighter path or skip Python objects by reading the levels once per coin; if book maintenance itself dominates, that is where a Rust helper would pay off, but **you cannot modify `nautilus_trader/` or `crates/`**, so this would mean a separate small PyO3/`cffi` module or NumPy vectorisation. Speculative; do not start here.
- Ranking engine: make `_fast_metrics_for` incremental (running sums instead of rebuilding a 300-list and `stdev` each publish), and shard the engine by venue the same way as the collector. Also consider `sqlite` batching writes. Only at 100+ coins.
- Parquet: scheduled compaction of the 60 s files into hourly/daily files (verify the catalog API in 1.229.0), plus stagger flushes per instrument so 29 writes don't land in the same second.

## 5. What NOT to do

- Don't rewrite in Go/Rust "for speed": the Go rebuild was already abandoned, and the hot networking/decode is already Rust. The remaining cost is Python glue you can shard.
- Don't add Kafka/NATS: Redis Streams covers the need at this scale and Redis is already deployed.
- Don't add threads for CPU work: the GIL makes it pointless; processes are the lever.

## 6. Risks and open questions

1. Are the section 2 hot spots real? Unknown until Step 0's profile.
2. Which exchanges are planned? Their WS limits and PyO3 client availability decide the wrapper cost. Not researched per-venue yet.
3. Target coin count per venue? 32 is the dYdX per-connection cap; 100+ changes the ranking-engine advice.
4. Prod box size today (memory is from 2026-09-12).
5. Cross-exchange ranking semantics (is BTC on dYdX vs Binance one row or two?) is a product question that affects the Redis key design and is not answered here.

## 7. Suggested next BMad step
Run profile (Step 0), then use `bmad-create-epics-and-stories` or `bmad-quick-dev` for: (a) skip-unstored-deltas fix, (b) `--venue/--shard` collector process split, (c) Streams migration as its own story if profiling justifies it.
