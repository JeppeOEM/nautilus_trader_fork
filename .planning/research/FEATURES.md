# Feature Research

**Domain:** 24/7 live market-data recorder (Bybit linear perps + spot) on NautilusTrader, writing to `ParquetDataCatalog`
**Researched:** 2026-06-13
**Confidence:** HIGH (core findings read directly from the fork's adapter source and catalog implementation)

## Context Anchors (verified from this codebase)

These facts drive every categorization below. All read directly from source — HIGH confidence.

- **Reconnect + resubscribe is fully owned by the adapter.** `crates/adapters/bybit/src/websocket/client.rs` implements exponential backoff (`reconnect_delay_initial_ms: 500`, `reconnect_delay_max_ms: 5000`, `backoff_factor: 1.5`, `jitter_ms: 250`, unlimited attempts) and a `resubscribe_all()` closure that replays every tracked topic after a reconnect, re-authenticates first if needed. `data.rs` clears quote/funding caches on `Reconnected`. **The recorder must NOT reimplement any of this.**
- **`ParquetDataCatalog.write_data()` is the official sink** (`nautilus_trader/persistence/catalog/parquet.py:253`). Each call writes **one parquet file per (data_cls, identifier)** named `{start_ts}-{end_ts}.parquet` under `{path}/data/{type}/{identifier}/`.
- **Two hard write invariants:** (1) data in a single write must be **monotonically non-decreasing by `ts_init`** or it raises `ValueError`; (2) the new file's `(start, end)` interval must be **disjoint** from all existing files in that directory or it raises `ValueError` (`_are_intervals_disjoint`). If the exact filename already exists, the write is silently skipped (prints, returns).
- **The catalog is NOT natively day-partitioned.** Layout is `{path}/data/{type}/{identifier}/{ts}-{ts}.parquet`. "Partition by day" (PROJECT.md) means *the recorder* must cut buffers at UTC midnight so each flush file covers one day — disjointness then falls out naturally.
- **Funding rate, mark price, index price ARE first-class:** `actor.pyx` exposes `subscribe_funding_rates`, `subscribe_mark_prices`, `subscribe_index_prices`; the Bybit adapter parses the linear ticker into `FundingRateUpdate` / `MarkPriceUpdate` / `IndexPriceUpdate`.
- **Open interest is NOT a first-class Nautilus data type.** OI exists in the raw Bybit ticker payload (`messages.rs: open_interest`) but there is no `subscribe_open_interest`, no `OpenInterestUpdate` class, and `FundingRateUpdate` has no OI field. Capturing OI requires a **custom `Data` subclass** recorded via `catalog.write_data` as custom data. This is the single biggest hidden-complexity item in the project.

## Feature Landscape

### Table Stakes (Users Expect These)

Missing any of these and the recorder is not trustworthy for 24/7 archival / backtest reuse.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Configurable explicit instrument list (linear-USDT + spot) | Core requirement; no auto-discovery in scope | LOW | Config field `list[InstrumentId]`; validate each exists in cache before subscribing |
| Load instrument definitions before subscribing | Subscriptions and book/serialization need instrument metadata (price/size precision); the options example pulls from `self.cache.instrument(...)` after the provider loads | MEDIUM | Use `InstrumentProviderConfig(load_ids=...)` (preferred) or `load_all` with filters. On `on_start`, assert each configured instrument resolved; stop with a clear error if not. **Also write the `Instrument` objects to the catalog** so backtests can load them. |
| Subscribe + record: trades, quotes, order-book deltas, bars/klines, funding rate | The explicit data-type list in PROJECT.md | MEDIUM | One `on_*` handler per type appends to a per-(type,instrument) buffer of native Nautilus objects (`TradeTick`, `QuoteTick`, `OrderBookDeltas`, `Bar`, `FundingRateUpdate`) — NOT pandas dicts like the options example |
| Write to official `ParquetDataCatalog` (not custom pandas parquet) | Key decision in PROJECT.md; lets data load straight into backtests | MEDIUM | Buffer native objects, `catalog.write_data(buffer)` per flush. The options example's custom `pd.read_parquet`+concat+overwrite pattern is explicitly the thing to replace |
| Time-ordered buffering per (type, instrument) | `write_data` raises if not monotonically non-decreasing by `ts_init` | MEDIUM | Append in arrival order (already ts-ordered per stream); sort defensively before flush. Never merge two instruments' data into one write — group by identifier |
| Day-partition rollover at UTC midnight | "Partitioned by day" requirement; also keeps files disjoint and query-friendly | HIGH | On a clock timer (or on first message whose UTC date > current partition date), flush all buffers for the closing day, then start new day's buffers. This is the rollover behavior the question asks about — see Pitfalls dependency |
| Batched flush cadence (not per-message) | Per-message writes = one tiny parquet file per message = catastrophic small-file explosion and I/O; per-day-only writes = up to 24h of data lost on crash | MEDIUM | Flush on a timer (e.g. every 30–60s) OR size threshold (e.g. N records), whichever first, **plus** a forced flush at day rollover. Each timed flush makes a file covering `[prev_flush_end, now]` — disjoint by construction. Tune so files are MBs not KBs |
| Graceful shutdown / flush on SIGTERM | systemd sends SIGTERM on stop/restart; un-flushed in-memory buffers = silent data loss every restart | MEDIUM | Flush all buffers in `on_stop()`. Ensure `node.run()` is wrapped so SIGTERM → `node.stop()`/`dispose()` path runs `on_stop`. Set `timeout_post_stop` generously enough for the final flush. The options example does flush in `on_stop` — keep that behavior |
| Auto-reconnect + auto-resubscribe across drops | 24/7 reliability requirement | LOW (adapter-provided) | **Do nothing in the recorder** beyond letting the adapter reconnect. Confirm via integration test that data resumes after a forced disconnect. See Anti-Features |
| Idempotent / crash-safe restart (no overwrite, no dup) | systemd `Restart=always` will restart mid-day; must not clobber the day's already-written files | MEDIUM | Catalog skips writing a filename that already exists and rejects overlapping intervals. Design flush intervals so a restart resumes with a *later* start ts (new disjoint interval). Accept a small gap at the crash point rather than risk overlap errors that halt the process |
| systemd unit with `Restart=always` + run guide | Explicit deliverable | LOW | `Restart=always`, `RestartSec`, `KillSignal=SIGTERM`, `TimeoutStopSec` ≥ flush time, `EnvironmentFile` for API keys, journald logging |
| pandas inspection utility over the catalog | Explicit deliverable | LOW | Thin wrapper around `ParquetDataCatalog.query(...)` / `quote_ticks(...)` returning DataFrames for a time slice + instrument; do NOT hand-roll parquet reads |
| Health/heartbeat logging on an interval | Operator needs to see it's alive and ingesting; journald is the monitoring surface | LOW | Per-interval log: per-instrument counts since last interval, totals, last-message age. The options example's `log_interval` summary is a good template |
| Stale-stream / no-data warning | A silently-connected-but-dead socket looks "up" to systemd but records nothing | LOW | Track `last_data_time`; warn if no messages for N seconds (options example does exactly this with a 120s threshold). Distinct from reconnect — adapter reconnects, but a topic can go quiet without a drop |

### Differentiators (Competitive Advantage)

Valuable, not required for a correct v1. Most are v1.x/v2.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Open-interest recording via custom `Data` type | PROJECT.md lists OI as a required data type, but Nautilus has no native OI object — so this is real net-new work, sitting between "table stake by requirement" and "differentiator by effort" | HIGH | Define an `OpenInterest(Data)` subclass + Arrow serializer registration, source OI from the ticker. **Flag for deeper phase research.** If too costly for v1, the honest fallback is recording mark/index price (which ARE native) and deferring OI |
| Mark price + index price recording | Native and cheap once funding is wired; valuable context for perps research | LOW | `subscribe_mark_prices` / `subscribe_index_prices` already exist; near-free incremental data type |
| Gap detection + gap markers in catalog | Backtests need to distinguish "no trades" from "recorder was down" | MEDIUM | `write_data` supports the empty-data + `start/end/data_cls` form to record a gap by extending filenames. Emit a gap marker around known downtime windows |
| Reconnect / disconnect event counters in health log | Quantifies connection quality over a 24h window | LOW | Count `Reconnected` events surfaced by the adapter; include in heartbeat log |
| Per-day file consolidation pass | Many small timed-flush files per day → consolidate into one file/day/type for query speed | MEDIUM | Catalog already ships `consolidate_data_by_period` / `consolidate_catalog`; run as a nightly post-rollover job, not inline |
| Disk-usage guard / retention sweep | 24/7 capture fills disks; PROJECT.md leaves pruning manual | MEDIUM | Warn at a free-space threshold; optional age-based partition deletion. v2 |
| Prometheus/textfile metrics export | Real monitoring beyond grepping journald | MEDIUM | v2; journald + heartbeat logs are sufficient for v1 |
| Config-driven data-type selection per instrument | Some instruments may only need trades, not full depth | LOW | Per-instrument toggle of which streams to subscribe; reduces topic count and disk |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Custom reconnect/resubscribe logic in the recorder | "We need 24/7 resilience" | The adapter already does exponential-backoff reconnect, topic replay, and re-auth (`client.rs`). Reimplementing it duplicates state, fights the adapter, and causes double-subscribes | Rely on the adapter; only *observe* `Reconnected` for metrics and verify resumption in a test |
| Per-message / per-tick parquet writes | "Don't lose any data on crash" | Each `write_data` = one file; per-message = millions of KB-sized files, disk-killing I/O, and the small-file problem destroys query performance | Timed/size batched flush + flush-on-rollover + flush-on-SIGTERM. Bounded loss window (seconds), not data integrity loss |
| Custom pandas-parquet format (the options example's approach) | It exists and "works" | Read-existing→concat→overwrite is O(file size) per flush, not crash-safe (overwrite can corrupt), and produces files that backtests can't load. Rejected in PROJECT.md Key Decisions | `ParquetDataCatalog.write_data` with native objects |
| Maintaining a live `OrderBook` just to record depth | The options example rebuilds books and stores best bid/ask snapshots | For *recording*, you want the raw `OrderBookDeltas` (full fidelity, replayable), not a derived top-of-book snapshot. Rebuilding the book wastes CPU and loses data | Subscribe to and record `OrderBookDeltas` directly; reconstruct books later at read time if needed |
| Storing redundant `pd.Timestamp.now()` wall-clock fields | "Know when we received it" | Duplicates `ts_init`; native objects already carry `ts_event`/`ts_init`. Extra columns break catalog schema compatibility | Trust native `ts_event`/`ts_init`; the catalog records both |
| Historical backfill in v1 | "Fill gaps after downtime" | Out of scope per PROJECT.md; mixing backfilled REST data with live WS data risks overlapping (non-disjoint) intervals → write errors | Live-stream only for v1; gap markers note downtime; backfill is a separate v2 tool that writes to a staging path |
| Redis / multi-process message bus | "Scale to many instruments" | Out of scope; a single WS connection multiplexes 10+ topics. Redis adds ops burden with no benefit at this scale | Single-process in-memory bus (PROJECT.md decision) |
| Auto-discover "all instruments" | Convenience | Out of scope; explodes topic count, disk, and risks hitting WS topic limits unpredictably | Explicit configured list |
| Writing on a wall-clock timer ignoring message `ts_init` for partition boundaries | Simpler to code | Partitioning by *receipt* wall-clock instead of event `ts_init` can split a day's data across files in a way that conflicts with how the catalog queries by `ts_init` | Cut day boundaries on event/init timestamps consistent with what `write_data` records |

## Feature Dependencies

```
Instrument provider load (load_ids)
    └──requires──> Instrument validation in on_start
                       └──requires──> Stream subscriptions (trades/quotes/depth/bars/funding)
                                          └──requires──> Per-(type,instrument) time-ordered buffers
                                                             └──requires──> Batched flush to catalog (write_data)
                                                                                ├──requires──> Day-partition rollover (UTC midnight cut)
                                                                                ├──requires──> Disjoint-interval discipline (no overlap on restart)
                                                                                └──requires──> Graceful SIGTERM flush (on_stop)

Adapter auto-reconnect/resubscribe ──enables──> 24/7 capture  (recorder is a passive beneficiary)
Reconnected event ──feeds──> reconnect counters & gap detection (differentiators)

Open-interest recording ──requires──> custom OpenInterest(Data) subclass + Arrow serializer
                                          (no native path; HIGH complexity)

Write Instrument objects to catalog ──enables──> backtest loadability of recorded data
Heartbeat logging ──enhances──> stale-stream detection (share last_data_time state)
Per-day consolidation ──conflicts──> inline frequent flushing (run consolidation AFTER rollover, never during active write)
```

### Dependency Notes

- **Buffers require time-ordering because** `write_data` raises `ValueError` on non-monotonic `ts_init` and on non-disjoint intervals. Day-rollover and restart logic both exist primarily to keep written intervals disjoint.
- **Day-partition rollover requires SIGTERM-flush coordination:** both flush the same buffers. Centralize flush in one method called from (a) timer, (b) rollover, (c) `on_stop` to avoid double-write / overlap bugs.
- **Open-interest recording is the critical-path unknown:** it is required by PROJECT.md but unsupported natively. Sequence a focused spike for it; it may slip to v1.x with mark/index price recorded in its place.
- **Consolidation conflicts with active writes:** never consolidate a directory the recorder is currently flushing into — schedule it for completed (prior-day) partitions only.

## MVP Definition

### Launch With (v1)

- [ ] Explicit instrument-list config (linear-USDT + spot) — core requirement
- [ ] Instrument provider load + `on_start` validation + write `Instrument`s to catalog — prerequisite for everything and for backtest loadability
- [ ] Record trades, quotes, order-book **deltas**, bars, funding rate as native objects via `write_data` — the required data types
- [ ] Per-(type,instrument) time-ordered buffering — required by catalog invariants
- [ ] Batched flush (timer + size) — avoids small-file explosion and bounds data-loss window
- [ ] UTC day-partition rollover with forced flush — the "partitioned by day" requirement
- [ ] Graceful SIGTERM flush in `on_stop` — prevents per-restart data loss
- [ ] Disjoint/idempotent restart behavior — survives `Restart=always` mid-day
- [ ] Heartbeat + stale-stream warning logging — minimum 24/7 observability
- [ ] systemd unit + deployment/run guide — explicit deliverable
- [ ] pandas catalog-inspection utility — explicit deliverable

### Add After Validation (v1.x)

- [ ] Open-interest recording via custom `Data` type — trigger: spike confirms feasible cost; until then record mark/index price as the native stand-in
- [ ] Mark price + index price recording — trigger: nearly free; can land in v1 if funding wiring is done early
- [ ] Reconnect/disconnect counters in heartbeat — trigger: after observing real connection behavior in prod
- [ ] Gap markers in catalog around downtime — trigger: first observed multi-minute outage

### Future Consideration (v2+)

- [ ] Nightly per-day consolidation job — defer: only matters once query speed degrades from many small files
- [ ] Disk-usage guard + retention sweep — defer: manual pruning acceptable until disk pressure appears
- [ ] Prometheus/textfile metrics — defer: journald sufficient for single-host v1
- [ ] Historical backfill tool — defer: explicitly out of scope; needs separate staging-path design to avoid interval overlaps

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Instrument load + validation + catalog-write | HIGH | MEDIUM | P1 |
| Native-object recording of 5 data types | HIGH | MEDIUM | P1 |
| Batched flush + day rollover | HIGH | HIGH | P1 |
| Graceful SIGTERM flush | HIGH | MEDIUM | P1 |
| Disjoint/idempotent restart | HIGH | MEDIUM | P1 |
| Heartbeat + stale-stream logging | MEDIUM | LOW | P1 |
| systemd unit + run guide | HIGH | LOW | P1 |
| pandas inspection utility | MEDIUM | LOW | P1 |
| Rely on adapter reconnect (do nothing) | HIGH | LOW | P1 |
| Open-interest custom data type | MEDIUM | HIGH | P2 |
| Mark/index price recording | MEDIUM | LOW | P2 |
| Reconnect counters / gap markers | MEDIUM | LOW | P2 |
| Per-day consolidation | MEDIUM | MEDIUM | P3 |
| Disk guard / retention | MEDIUM | MEDIUM | P3 |
| Prometheus metrics | LOW | MEDIUM | P3 |
| Historical backfill | MEDIUM | HIGH | P3 |

**Priority key:** P1 = must have for launch · P2 = should have, add when possible · P3 = nice to have, future.

## Competitor Feature Analysis

"Competitors" here are reference recorder patterns rather than products.

| Feature | Existing options collector (`bybit_options_data_collector.py`) | Generic exchange tick-recorder norm | Our Approach |
|---------|----------------------------------------------------------------|-------------------------------------|--------------|
| Storage format | Custom pandas parquet (read→concat→overwrite) | Often raw JSON/CSV or custom parquet | Official `ParquetDataCatalog`, native objects, backtest-loadable |
| Order book | Rebuilds `OrderBook`, stores top-of-book snapshot | Varies | Record raw `OrderBookDeltas` (full fidelity) |
| Flush cadence | Timer (`log_interval`) on whole-file rewrite | Per-batch append | Timer+size batched `write_data`, one file per flush, disjoint intervals |
| Day partitioning | None (single growing file per instrument) | Sometimes hourly/daily dirs | Explicit UTC-midnight rollover → one file/day/type/instrument |
| Reconnect | Relies on adapter; adds a 120s stale-data warning | Custom reconnect loops (anti-pattern) | Rely on adapter; keep the stale-data warning |
| Shutdown flush | Flushes in `on_stop` (good) | Often missing → data loss | Flush in `on_stop`, SIGTERM-driven, generous `timeout_post_stop` |
| Open interest | Not recorded | Rarely | Custom `Data` type (P2); mark/index price native stand-in |

## Sources

- `crates/adapters/bybit/src/websocket/client.rs` (reconnect backoff + `resubscribe_all`) — HIGH
- `crates/adapters/bybit/src/data.rs` (cache clear on `Reconnected`) — HIGH
- `crates/adapters/bybit/src/websocket/parse.rs` & `messages.rs` (linear ticker → funding/mark/index; OI only raw) — HIGH
- `nautilus_trader/persistence/catalog/parquet.py` (`write_data`, `_write_chunk`, disjoint-interval check, `_make_path`, `consolidate_*`) — HIGH
- `nautilus_trader/common/actor.pyx` (subscribe methods incl. funding/mark/index; no open-interest) — HIGH
- `nautilus_trader/model/data.pyx` (`FundingRateUpdate`; no native `OpenInterest`) — HIGH
- `examples/live/bybit/bybit_options_data_collector.py` + README (reference patterns: log_interval, stale-data warning, on_stop flush; anti-patterns: custom parquet, book rebuild) — HIGH
- `.planning/PROJECT.md` (scope, data types, key decisions) — HIGH

---
*Feature research for: Bybit 24/7 market-data recorder on NautilusTrader*
*Researched: 2026-06-13*
