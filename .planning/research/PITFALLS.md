# Pitfalls Research

**Domain:** 24/7 live crypto market-data recorder (Bybit + NautilusTrader → ParquetDataCatalog)
**Researched:** 2026-06-13
**Confidence:** HIGH (most pitfalls verified directly against this fork's source: `nautilus_trader/persistence/catalog/parquet.py`, `nautilus_trader/adapters/bybit/data.py`, `crates/adapters/bybit/src/websocket/`, plus official Bybit V5 docs)

> Scope note: The existing `examples/live/bybit/bybit_options_data_collector.py` is the explicit anti-pattern this project replaces. Several pitfalls below are drawn directly from mistakes baked into that example. Where a pitfall is "the example does X", that is a verified observation, not speculation.

---

## Critical Pitfalls

### Pitfall 1: `ParquetDataCatalog.write_data()` silently skips writes on filename collision

**What goes wrong:**
The catalog names each file `{start_ts_init}_{end_ts_init}.parquet`. In `_write_chunk` (parquet.py:376-378), if a file with that exact name already exists, it prints `"File ... already exists, skipping write"` and **returns without writing** — the data is dropped silently. In a 24/7 recorder that flushes batches periodically, this happens whenever two batches for the same instrument/type produce identical `(start, end)` ts_init bounds (e.g. two flushes where the first and last event share a nanosecond, or a single-event batch retried after restart).

**Why it happens:**
The catalog was designed for batch/backtest ingestion where each historical chunk is written once. Live recording calls `write_data` repeatedly from a non-deterministic timer/event context. Developers assume `write_data` appends; it does not — one filename = one immutable file.

**How to avoid:**
- Never write two chunks with overlapping or identical `(start_ts, end_ts)` for the same `(data_cls, instrument_id)`. Buffer strictly by day-partition and flush each partition's data in monotonic, non-overlapping segments.
- Treat the day boundary as the partition key, but within a day write **append-only growing segments** by passing explicit non-overlapping `start`/`end`, or accumulate the whole day in memory/temp and consolidate.
- After each flush, assert the file was actually created (`catalog.fs.exists(expected_path)`); log loudly if a skip occurs. Do not rely on "no exception = data saved".
- Prefer the consolidation API (`consolidate_data_by_period`, which uses `skip_disjoint_check=True`) for merging intra-day segments rather than re-writing the same filename.

**Warning signs:**
`"already exists, skipping write"` in stdout/logs; row counts in parquet < counts in your in-process counters; gaps in recorded data that align with flush boundaries.

**Phase to address:** Catalog write layer / persistence phase (earliest data-writing phase). This is the single highest-risk design decision — get the buffer→flush→partition contract right before recording anything real.

---

### Pitfall 2: Out-of-order `ts_init` makes `write_data()` raise `ValueError` and kill the flush

**What goes wrong:**
`_write_chunk` (parquet.py:400-408) sorts the batch by `ts_init` and raises `ValueError("Data should be monotonically increasing...")` if the input wasn't already non-decreasing. Live market data from Bybit can arrive slightly out of order across instruments, and if you batch multiple instruments or interleave data types into one `write_data` call, or if `ts_init` is assigned from wall-clock at slightly different points, the batch can be non-monotonic and the entire flush throws.

**Why it happens:**
Developers assume the catalog sorts for them silently. It does sort for grouping, but then *validates* strict non-decreasing order and raises if the caller's list wasn't already ordered. An unhandled raise inside a strategy timer callback can propagate.

**How to avoid:**
- Sort every batch by `ts_init` **before** calling `write_data` (the error message itself recommends this).
- Write one `(data_cls, instrument_id)` group per call so cross-instrument interleaving can't violate monotonicity.
- Wrap each flush in try/except, log the failure, and **do not lose the buffer** — retry or quarantine the offending batch rather than dropping it.

**Warning signs:**
`ValueError: Data should be monotonically increasing` in logs; flush failures clustered under high message rates.

**Phase to address:** Catalog write layer / persistence phase, alongside Pitfall 1.

---

### Pitfall 3: Disjoint-interval check raises when new data overlaps an existing partition file

**What goes wrong:**
With `skip_disjoint_check=False` (the default), `_write_chunk` (parquet.py:380-384) collects all existing file intervals in the directory plus the new `(start, end)` and raises `ValueError` if they are not mutually disjoint. After a restart, if the recorder buffered some data, crashed, and replays/re-buffers an overlapping time range — or if a late event has a `ts_init` falling inside an already-written file's range — the write is rejected.

**Why it happens:**
The catalog enforces non-overlapping time intervals per directory as an integrity invariant. Live recording across restarts naturally produces overlapping ranges unless the recorder tracks the high-water-mark timestamp it has already persisted.

**How to avoid:**
- Persist a per-(instrument, data_cls) high-water-mark `ts_init` of the last flushed event. On restart, **discard or hold** any incoming event with `ts_init <=` the high-water-mark for that stream before buffering.
- Partition strictly by UTC day; on restart for the current day, either resume the day's open segment or write only events strictly after the last persisted timestamp.
- Use `skip_disjoint_check=True` only in a controlled consolidation step where you explicitly manage which files you remove/rewrite — never as a blanket workaround for live writes (it disables the integrity guard).

**Warning signs:**
`ValueError` referencing disjoint/overlapping intervals after a restart or reconnect; duplicate rows when you do force the write through.

**Phase to address:** Restart/resume & crash-recovery phase (the "no data loss across restarts" requirement). Must be designed together with Pitfalls 1-2.

---

### Pitfall 4: Open interest is NOT a first-class recordable data type — it rides the ticker stream only

**What goes wrong:**
PROJECT.md requires recording open interest for linear perpetuals. But in this fork there is **no `OpenInterest` data class registered** for arrow/catalog serialization (`serializer.py` registers only OrderBookDelta(s), QuoteTick, TradeTick, Bar, FundingRateUpdate), and the Bybit Python data client has **no `subscribe_open_interest`** method. For linear perps, `openInterest`/`openInterestValue` arrive embedded in the linear **ticker** websocket message (`crates/adapters/bybit/src/websocket/messages.rs:628`, pushed ~every 100ms), not as a standalone subscribable stream. A developer who writes `self.subscribe_*` looking for an OI feed will find nothing and silently ship without OI.

**Why it happens:**
NautilusTrader models funding rate as a first-class type (`FundingRateUpdate`) but does not (yet) model open interest as one. Bybit's API design folds OI into the ticker snapshot/delta. The mismatch is invisible until you check both the adapter surface and the serializer registry.

**How to avoid:**
- Decide the OI strategy explicitly and early. Options, in order of preference:
  1. Define a **custom `OpenInterest` data type** + register it with `register_arrow(...)` so it's catalog-writable, and populate it from the ticker stream (requires extracting OI from the linear ticker — verify how/whether the Rust adapter exposes it to Python; it may only surface OI via funding/ticker handlers, not as a distinct callback).
  2. Record the **full linear ticker** as custom data (carries funding rate + OI + mark/index) and derive OI downstream.
  3. Poll the REST open-interest endpoint on an interval and write custom records (note: REST OI is bucketed at fixed intervals, e.g. 5min/15min/1h — not tick-level).
- Do NOT assume `subscribe_funding_rates` also delivers OI as a writable record — funding and OI are separate fields even though both originate in the ticker.

**Warning signs:**
No OI rows in the catalog despite "subscribed"; searching the adapter for an OI subscribe method returns nothing; assuming `FundingRateUpdate` carries OI (it does not).

**Phase to address:** Data-type coverage / subscription phase. Flag for **deeper spike research** — OI plumbing from ticker→Python→catalog is the least-paved path in the requirement set.

---

### Pitfall 5: Spot order book supports only depth [1, 50]; linear supports [1, 50, 200, 500]

**What goes wrong:**
Per Bybit V5, **spot** orderbook depth is limited to **1 or 50**; **linear** supports 1, 50, 200, 500 (depth 25/100 is options-only). The existing example hardcodes `spot_depth=50` (works) but a developer extending it to `spot_depth=200` for parity with linear will get a rejected/failed subscription. The Bybit Python client also defaults `depth` to 50 when 0 is passed (`data.py:330`) and clamps REST snapshot depth to venue max (`data.py:781`), but a wrong WS depth for spot is not silently corrected.

**Why it happens:**
Developers configure one `depth` value for all instruments and assume spot and linear are symmetric. They are not.

**How to avoid:**
- Use **per-product-type depth config**: linear depth (50 or 200), spot depth (50). Validate depth against an allow-list per product type before subscribing.
- Treat 200/500 linear depth as a deliberate cost choice: depth-200 pushes at 100ms, depth-50 at 20ms — higher levels mean far more delta volume and parquet growth (see Pitfall 9).

**Warning signs:**
Spot orderbook subscription silently produces no deltas; warning logs about unsupported depth; only linear books populate.

**Phase to address:** Subscription/config phase. Encode depth validation in the config schema.

---

### Pitfall 6: One unhandled exception in a strategy callback can take the whole node down

**What goes wrong:**
The recorder does all its parquet I/O inside `on_order_book_deltas` / `on_quote_tick` / timer callbacks (as the example does — it calls `_save_all_data_to_parquet()` synchronously from `_check_and_log_data()` on every event). If `to_parquet`, a disk-full error, a schema mismatch, or the `ValueError`s from Pitfalls 1-3 raise unhandled, the exception propagates into the engine and can stop the node — and `systemd Restart=always` then loops crash/restart, potentially losing buffered in-memory data each cycle.

**Why it happens:**
Strategy callbacks feel like ordinary methods; developers forget they run inside the single-threaded engine event loop. Synchronous blocking I/O on the hot path also stalls message processing (backpressure).

**How to avoid:**
- Wrap all I/O and serialization in try/except inside callbacks; never let recorder logic raise into the engine.
- Move parquet writes **off the hot path**: buffer in callbacks, flush from a periodic timer (`self.clock.set_timer`) or a separate thread/executor, so blocking disk I/O doesn't stall WS message draining.
- On flush failure, preserve the buffer and increment a failure metric; only clear the buffer after a confirmed successful write (the example clears immediately after the write call — if the write partially failed, data is lost).

**Warning signs:**
Node restarts in journald with tracebacks from `on_*` callbacks; growing event-processing latency; "node stopped" without an intentional stop.

**Phase to address:** Recorder core / reliability phase. Define the buffer→flush→clear contract with explicit failure handling.

---

### Pitfall 7: Instrument cache must be loaded before subscribing — staleness and timing

**What goes wrong:**
Subscriptions require the instrument to exist in the cache (the example calls `self.cache.instrument(id)` and stops if `None`). If `InstrumentProviderConfig` doesn't load the needed instruments (wrong `product_types`, wrong filters, `load_all=False`), or if `on_start` subscribes before instruments are loaded, subscriptions silently fail or the strategy stops. Over a 24/7 run, instrument definitions can also go stale (new listings, delistings, contract changes) without a refresh.

**Why it happens:**
The provider loads instruments at startup; the strategy assumes they're present. Filters (`filters={"base_coin": ...}`) can exclude instruments you intended to record. Linear and spot are separate product types and separate WS clients (`data.py` keeps one ws_client per product type) — forgetting to enable both `LINEAR` and `SPOT` product types loads only one universe.

**How to avoid:**
- Configure `InstrumentProviderConfig` to load exactly the instruments you'll subscribe to; enable **both** linear and spot product types if recording both.
- In `on_start`, validate every configured instrument is in cache before subscribing; fail fast with a clear error listing missing IDs (the example does this — keep that pattern).
- For 24/7 runs, decide a re-load/refresh cadence or accept that the configured explicit list is fixed until restart (acceptable for v1's explicit-list scope).

**Warning signs:**
`"Could not find instrument"` warnings; strategy stops at startup; only one product type produces data.

**Phase to address:** Subscription/config phase + node bootstrap phase.

---

### Pitfall 8: Wall-clock timestamps instead of event timestamps corrupt the catalog's time semantics

**What goes wrong:**
The existing example stores `pd.Timestamp.now()` and `time.time()` as the record timestamp. For a Nautilus-catalog-compatible archive that must load back into backtests, the catalog keys files and ordering on **`ts_init`/`ts_event`** (UNIX nanoseconds from the data object), not wall-clock receive time. Mixing wall-clock time into the partition logic produces files whose names/ranges don't match the data's real event times, breaks day-partitioning correctness, and makes backtest replay timing wrong.

**Why it happens:**
Carrying over the custom-parquet mindset from the example, which never had to be catalog-compatible.

**How to avoid:**
- Write the **native Nautilus data objects** (the `OrderBookDeltas`, `QuoteTick`, `TradeTick`, `Bar`, `FundingRateUpdate` you receive) directly via `catalog.write_data([...])`. Let the catalog derive `start`/`end` from `ts_init`. Do not reconstruct dicts with `now()` timestamps.
- Partition by UTC day derived from `ts_init` (nanos), not local wall-clock.

**Warning signs:**
File ranges don't match contained event times; backtest replay produces events at wrong times; timezone drift between day partitions.

**Phase to address:** Catalog write layer phase. This is the core differentiator vs. the example (official format, not custom pandas).

---

### Pitfall 9: Order-book-depth volume explodes parquet size and unbounded buffers explode memory

**What goes wrong:**
Linear depth-50 pushes every 20ms, depth-200 every 100ms; across 10+ instruments this is the dominant data volume by far. If buffers are unbounded (flush only on a long interval, or never under sustained load), memory grows continuously. If files are written too small/too often, you get file-count explosion; too large, you get multi-GB files that are slow to read. The example accumulates `list[dict]` in memory and only flushes every `log_interval` seconds — under high depth volume that buffer can grow large between flushes.

**Why it happens:**
Order book deltas are the highest-frequency stream; developers size buffers/flush intervals for quotes/trades and get surprised by depth.

**How to avoid:**
- Bound buffers by **size AND time**: flush when either N records or T seconds reached, whichever first. Pick the cheaper depth (50 over 200) unless deep book is required.
- Partition by day so each file is naturally bounded; monitor per-day file sizes and row counts.
- Track buffer memory and log it; set a hard cap that triggers an emergency flush.
- Budget disk: estimate bytes/day = (msg rate × avg row size) and provision + plan retention/archival (PROJECT.md defers retention, so at minimum monitor `df` and alert).

**Warning signs:**
RSS grows monotonically over hours/days; single parquet files in the hundreds of MB+; disk filling faster than expected; GC pauses.

**Phase to address:** Recorder core / buffering phase; revisit in a scale/observability phase.

---

### Pitfall 10: WebSocket reconnect edge cases — gaps, sequence resets, and unconfirmed resubscribes

**What goes wrong:**
The Rust adapter does auto-reconnect and **does resubscribe** on reconnect (`client.rs:490+`: marks confirmed subscriptions pending and replays them; re-authenticates for private streams). But order book streams have a **snapshot+delta** model: after a reconnect, Bybit sends a fresh snapshot and the local book must be reset, or applying new deltas onto a stale book produces a corrupt book. A naive recorder that just keeps appending deltas across a reconnect records a discontinuity it can't detect later. Also, the recorder's own "no data for 2 minutes" heuristic (from the example) is a crude liveness check, not a correctness check.

**Why it happens:**
Reconnect is handled at the transport layer, but **book-state continuity** and **gap recording** are the application's responsibility. Developers trust "it reconnects" and don't record the gap or reset book state.

**How to avoid:**
- On reconnect / on receiving an orderbook snapshot, **reset the local book** for that instrument (the framework signals snapshot vs delta — handle the snapshot flag). Don't apply deltas onto a pre-reconnect book.
- **Record the gap explicitly**: the catalog supports writing an empty-data range via `write_data(data=[], start=..., end=..., data_cls=..., identifier=...)` which calls `extend_file_name` to mark the covered range. Use this to make gaps visible to downstream backtests rather than presenting a silent discontinuity.
- Keep a liveness watchdog (the example's `last_data_time` / `connection_warnings`) but treat it as a metric/alert, not as your reconnect mechanism (the adapter already reconnects).

**Warning signs:**
Sequence-number jumps in recorded deltas; crossed/locked books after reconnect; quiet periods in data that don't correspond to recorded gaps; `"No data received for N seconds"` warnings.

**Phase to address:** Reliability / reconnect-resilience phase. Flag gap-recording as a deeper-research item.

---

### Pitfall 11: Arrow/catalog schema mismatches between Nautilus versions break re-reads

**What goes wrong:**
CONCERNS.md explicitly flags Arrow/Cap'n Proto schema instability ("Serialization Format Stability", "Data Engine Bulk Insert Serialization Type Mismatch" — Rust/Python Arrow schema mismatches, commented-out round-trip tests). If you record months of data on one Nautilus version, then upgrade the fork, a changed arrow schema for `OrderBookDelta`/`QuoteTick`/`FundingRateUpdate` can make old partitions unreadable or mis-typed when loaded into a newer backtest.

**Why it happens:**
The wire/arrow schemas are pre-1.0 and not guaranteed stable across releases (CONCERNS.md). A long-running archive spans multiple versions.

**How to avoid:**
- Pin the Nautilus/fork version for the recorder and record the version in a manifest alongside the catalog (e.g. a `RECORDER_VERSION` file per day or in metadata).
- Before upgrading, run a round-trip read test of existing partitions against the new version; if schema changed, plan a migration/consolidation pass.
- Avoid mixing data written by different versions in the same partition directory.

**Warning signs:**
`pyarrow` schema/type errors on read; columns missing or retyped; backtests failing to load older catalog slices after an upgrade.

**Phase to address:** Versioning/observability phase; verification gate before any fork upgrade.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Synchronous parquet write inside `on_*` callbacks (as the example does) | Trivial to implement | Blocks event loop → backpressure → dropped/late data; raises can kill node | Never for depth streams; only tolerable for very low-rate streams in a throwaway prototype |
| Clear in-memory buffer immediately after `to_parquet` call (example pattern) | Simple | Silent data loss if write partially fails or is skipped | Never — clear only after confirmed successful write |
| Wall-clock (`now()`) timestamps in records | Easy | Catalog not backtest-compatible; wrong day partitioning | Never for this project (official-catalog is the whole point) |
| Single `depth` for spot + linear | One config knob | Spot subscription fails for depth>50 | Never — use per-product depth |
| Relying on systemd restart for "reliability" without persisting high-water-mark | Looks resilient | Each restart re-buffers overlapping ranges → disjoint-interval `ValueError` or duplicates | Only after Pitfall 3's high-water-mark is implemented |
| Recording OI by stuffing it into a quote/custom dict without a registered type | Fast | Not catalog-readable; lost on reload | Only as a clearly-labeled interim until a real `OpenInterest` type is registered |
| `skip_disjoint_check=True` on live writes to dodge interval errors | Stops the errors | Disables integrity guard → silent overlapping/duplicate data | Only inside a controlled consolidation step, never on the live write path |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Bybit WS depth | Using depth 200/500 for spot | Spot only supports [1, 50]; linear supports [1, 50, 200, 500] |
| Bybit WS spot quotes | Expecting a bid/ask ticker for spot | Spot ticker has no bid/ask; adapter subscribes orderbook depth=1 to synthesize quotes (`data.py:345-349`) |
| Bybit funding rate | Treating it as a high-frequency stream | Funding rate is constant between 8h funding intervals; ticker pushes 100ms but value rarely changes — dedupe before recording or you store huge redundant runs |
| Bybit open interest | Looking for `subscribe_open_interest` | No such method; OI rides the linear ticker (or REST polling). Needs a custom recordable type (Pitfall 4) |
| Bybit subscription limits | Sending one giant subscribe with all topics | Spot allows ≤10 args/subscribe request; `args` array ≤21,000 chars per connection; ≤500 new connections per 5 min; ≤1,000 connections/IP. Batch subscribes; adapter sends one topic per message on resubscribe (`client.rs:502`) |
| Bybit product types | Enabling only LINEAR | Linear and spot are separate product types → separate WS clients (`data.py` one ws_client per product type). Enable both to record both |
| ParquetDataCatalog | Calling `write_data` repeatedly assuming append | Same-named file is skipped (Pitfall 1); overlapping ranges raise (Pitfall 3); unsorted batch raises (Pitfall 2) |
| systemd + venv | Bare `python script.py` in unit | Use absolute interpreter path `/abs/venv/bin/python`; set `WorkingDirectory`; venv not auto-activated under systemd |
| systemd + secrets | API keys in the unit file / committed `.env` | Use `EnvironmentFile=` (chmod 600, outside git) or a secrets manager; CONCERNS.md flags credential leakage; never log keys |
| journald + Nautilus file logging | Double-logging or unbounded growth | Pick one primary sink; if using Nautilus file logging, configure rotation; journald has its own retention (`SystemMaxUse`) — set both intentionally |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Unbounded in-memory buffer for depth deltas | RSS grows for hours, then OOM kill | Size+time bounded flush; cap with emergency flush | Sustained high depth volume over many instruments; hours-to-days |
| Blocking parquet I/O on event-loop hot path | Rising event latency, dropped/late ticks, WS backpressure | Flush off-thread/timer, never in callback | First high-volatility burst across 10+ instruments |
| Funding-rate ticker stored without dedup | Millions of identical funding rows/day | Record `FundingRateUpdate` only when value/nextFundingTime changes | Immediately at 100ms ticker rate |
| Too-frequent tiny parquet files | Millions of small files, slow reads, inode pressure | Day-partition + consolidation; reasonable flush size | Within days at high flush frequency |
| Re-reading entire parquet to append (example's pattern) | Each flush reads+rewrites whole file; O(n²) over a day | Use catalog's append-by-non-overlapping-segment + consolidate; never read-modify-write a growing file | Within a single day as the file grows |
| Single process near Bybit topic/char limits | Subscribe failures, dropped instruments | Stay well under 21k-char args / 10-arg spot batches; chunk subscribes | When instrument list grows large (PROJECT.md targets 10+, fine; watch at ~hundreds) |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| API key/secret in systemd unit or committed `.env` | Credential leak via repo or `systemctl cat` | `EnvironmentFile=` with 600 perms outside git; `.env` gitignored (CONCERNS.md) |
| Logging config/credentials at startup | Keys in journald/file logs, crash dumps | Never log key/secret; audit error paths (CONCERNS.md flags credential leakage in adapter error paths) |
| World-readable data/log directories | Recorded data + logs exposed | Restrict dir perms; run service as a dedicated unprivileged user |
| Using mainnet keys with trade permissions for a read-only recorder | Compromise → unauthorized trades | Use a **read-only / market-data-only** API key; this project never trades |

## UX Pitfalls

> "Users" here = the operator running/monitoring the recorder and the analyst consuming the catalog.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No visibility into per-stream record counts / gaps | Operator can't tell if data is actually flowing or silently dropped | Periodic per-(instrument,type) counters + last-event-ts in logs (example does counts — keep, add gap visibility) |
| Silent skip/raise on flush swallowed | Analyst discovers missing data months later | Loud alerting on any write skip/failure; expose a health metric |
| pandas inspection util that loads a full day naively | OOM / minutes-long loads on depth data | Inspection util filters by instrument + time slice + columns; reads single partitions, not whole catalog |
| No documented restart/resume semantics | Operator unsure if a restart lost data | Document high-water-mark behavior and how gaps are recorded |

## "Looks Done But Isn't" Checklist

- [ ] **Order book recording:** Often missing book reset + gap-record on reconnect — verify a forced disconnect produces a recorded gap and a clean post-reconnect book, not a corrupt one.
- [ ] **Open interest:** Often missing entirely (no native type) — verify OI rows actually exist in the catalog and are re-readable, not just "subscribed".
- [ ] **Funding rate:** Often recorded as redundant 100ms duplicates — verify dedup; verify it round-trips as `FundingRateUpdate`.
- [ ] **Catalog compatibility:** Often custom-dict parquet that won't load into a backtest — verify a real Nautilus backtest can `catalog.query()` the recorded data.
- [ ] **Restart safety:** Often re-buffers overlapping ranges — verify two restarts in the same day don't raise disjoint-interval `ValueError` or duplicate rows.
- [ ] **Spot vs linear depth:** Often one shared depth — verify spot uses ≤50 and both product types record.
- [ ] **systemd:** Often works manually but fails under systemd — verify absolute venv path, `EnvironmentFile` secrets, `WorkingDirectory`, and that `Restart=always` doesn't lose buffered data.
- [ ] **Disk/retention:** Often no monitoring — verify disk-usage alerting since v1 retains everything.
- [ ] **Crash safety:** Often clears buffer before confirmed write — verify buffer survives a failed flush.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Filename-collision silent skips (Pitfall 1) | MEDIUM | Audit logs for "already exists, skipping"; reconstruct lost windows from any raw backup; redesign flush to non-overlapping segments going forward |
| Disjoint-interval errors on restart (Pitfall 3) | LOW-MEDIUM | Add high-water-mark filtering; consolidate existing day with `consolidate_data_by_period`; resume |
| OI never recorded (Pitfall 4) | MEDIUM | Add custom `OpenInterest` type + register_arrow; backfill from REST OI buckets if needed (lossy/coarse) |
| Corrupt book after reconnect (Pitfall 10) | HIGH (data already wrong) | Cannot fully recover past corruption; fix reset-on-snapshot logic; mark affected ranges as gaps; re-record forward |
| Schema mismatch after upgrade (Pitfall 11) | HIGH | Migrate/rewrite old partitions with a converter; keep version-pinned reader for legacy data |
| Buffer lost on crash (debt pattern) | MEDIUM | Switch to confirm-then-clear; reduce flush interval to bound loss window |
| Memory growth / OOM (Pitfall 9) | LOW | Add size+time bounded flush + emergency cap; restart; data after last good flush may be lost |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1. Filename-collision silent skip | Catalog write layer | Forced duplicate-range write logs+alerts (never silently skips); row counts match counters |
| 2. Out-of-order ts_init ValueError | Catalog write layer | Inject unsorted batch → flush still succeeds (pre-sorted) and buffer not lost |
| 3. Disjoint-interval on restart | Restart/resume & recovery | Two same-day restarts produce no ValueError and no duplicate rows |
| 4. Open interest not a native type | Data-type coverage (spike) | OI rows exist in catalog and re-read into a backtest |
| 5. Spot vs linear depth | Subscription/config | Config rejects spot depth>50; both product types produce data |
| 6. Exception kills node | Recorder core / reliability | Disk-full / forced raise in flush is caught; node keeps running; buffer preserved |
| 7. Instrument cache staleness/timing | Node bootstrap + config | Missing instrument fails fast with clear list; both linear+spot loaded |
| 8. Wall-clock vs event timestamps | Catalog write layer | File ranges match contained event ts_init; backtest replay timing correct |
| 9. Size/memory explosion | Buffering + observability | RSS stable over 24h; per-day file sizes bounded; disk alerting live |
| 10. Reconnect book corruption / gaps | Reliability / reconnect (spike) | Forced disconnect → clean book + recorded gap |
| 11. Arrow schema drift across versions | Versioning / upgrade gate | Round-trip read of old partitions passes before upgrade; version manifest present |

**Suggested phase ordering implication:** The catalog write contract (Pitfalls 1, 2, 8) is foundational and must precede recording any real data. Restart/resume (Pitfall 3) and reliability/reconnect (Pitfalls 6, 10) are the next-hardest and should be their own phases. Open-interest plumbing (Pitfall 4) and reconnect gap-recording (Pitfall 10) are the two items flagged for **deeper spike research** before implementation.

## Sources

- This fork's source (HIGH confidence, read directly):
  - `nautilus_trader/persistence/catalog/parquet.py` — `write_data`/`_write_chunk`: filename-collision skip (376-378), disjoint-interval raise (380-384), monotonic-ts_init raise (400-408), empty-data gap extension (309-316)
  - `nautilus_trader/adapters/bybit/data.py` — per-product-type WS clients, spot quote-via-orderbook-depth-1 (345-349), depth default 50 (330), no `subscribe_open_interest`, funding-rate subscribe path
  - `nautilus_trader/serialization/arrow/serializer.py` — registered types: OrderBookDelta(s), QuoteTick, TradeTick, Bar, FundingRateUpdate (516-518); no OpenInterest type
  - `crates/adapters/bybit/src/websocket/client.rs` — auto-reconnect + resubscribe/re-auth (490+), one-topic-per-subscribe message (502)
  - `crates/adapters/bybit/src/websocket/messages.rs` — linear ticker carries open_interest/openInterestValue (628-630)
  - `examples/live/bybit/bybit_options_data_collector.py` — the anti-pattern: sync in-callback writes, read-modify-write parquet, wall-clock timestamps, immediate buffer clear, `last_data_time` liveness heuristic
  - `.planning/codebase/CONCERNS.md` — Arrow/Cap'n Proto schema instability; serialization type mismatches; credential-leak risk
- Official Bybit V5 docs (HIGH confidence):
  - Orderbook depths — Linear [1,50,200,500], Spot [1,50], Option [25,100]: https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook
  - Linear ticker (snapshot+delta, ~100ms, carries fundingRate/nextFundingTime/openInterest): https://bybit-exchange.github.io/docs/v5/websocket/public/ticker
  - Connection/subscription limits (≤10 args/spot request, ≤21,000-char args, ≤500 conn/5min, ≤1,000 conn/IP): https://bybit-exchange.github.io/docs/v5/ws/connect and https://bybit-exchange.github.io/docs/v5/rate-limit

---
*Pitfalls research for: 24/7 Bybit live market-data recorder on NautilusTrader → ParquetDataCatalog*
*Researched: 2026-06-13*
