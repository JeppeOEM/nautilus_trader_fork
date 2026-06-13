# Architecture Research

**Domain:** Live market-data recorder (Bybit) on NautilusTrader, single-process, Parquet catalog archival
**Researched:** 2026-06-13
**Confidence:** HIGH (verified against in-repo source: `nautilus_trader/persistence/catalog/parquet.py`, `common/actor.pyx`, `adapters/bybit/data.py`, `test_kit/strategies/tester_data.py`)

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                       systemd (Restart=always)                        │
│                            run.py entrypoint                           │
├──────────────────────────────────────────────────────────────────────┤
│                          TradingNode (kernel)                         │
│  ┌────────────────────────┐         ┌──────────────────────────────┐  │
│  │  BybitDataClient (WS)  │         │   In-process MessageBus +    │  │
│  │  - linear + spot       │ ──────► │   DataEngine                 │  │
│  │  - ticker refcounting  │  Data   │   (routes Data to handlers)  │  │
│  └────────────────────────┘         └──────────────┬───────────────┘  │
│                                                     │ callbacks         │
│                          ┌──────────────────────────▼───────────────┐  │
│                          │  RecorderActor(s) / Strategy             │  │
│                          │  on_quote_tick / on_trade_tick /         │  │
│                          │  on_order_book_deltas / on_order_book_   │  │
│                          │  depth / on_bar / on_funding_rate        │  │
│                          │         │ append to per-(type,id) buffer │  │
│                          │         ▼                                 │  │
│                          │  CatalogWriter (buffer + day-flush)      │  │
│                          └──────────────────────┬───────────────────┘  │
│                          clock.set_time_alert ──┘ (UTC midnight rollover)│
├──────────────────────────────────────────────────────────────────────┤
│                       ParquetDataCatalog (disk)                       │
│   {root}/data/{type}/{identifier}/{start_ns}_{end_ns}.parquet         │
└──────────────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ read-only
                          ┌────────┴─────────┐
                          │  inspect.py      │  (separate pandas utility,
                          │  (pandas loader) │   NOT in the node process)
                          └──────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Implementation |
|-----------|----------------|----------------|
| `run.py` entrypoint | Load config file, build `TradingNodeConfig` + `BybitDataClientConfig`, register `BybitLiveDataClientFactory`, add actor(s), `node.run()`. Mirrors `bybit_data_tester.py`. | Thin Python `main()` |
| Config loader | Parse a TOML/YAML file into typed config (instrument list, data dir, depths, bar specs, flush interval). | `tomllib` (stdlib 3.11+) → pass values into frozen `*Config` objects |
| `BybitDataClient` | Owns the WS connection(s), multiplexes topic subscriptions, reconnects automatically. Funding/mark/index all ride the **ticker** channel via refcounting. | Existing adapter — no changes |
| `RecorderActor` (recommended: `Actor`, not `Strategy`) | Subscribe per instrument×data-type in `on_start`; receive typed callbacks; append to in-memory buffers; own the day-rollover timer. | New code — subclass `nautilus_trader.common.actor.Actor` |
| `CatalogWriter` (helper) | Buffer typed objects keyed by `(data_cls, identifier)`; flush sorted batches to `ParquetDataCatalog.write_data`; manage day-aligned file boundaries. | New plain Python class, no Nautilus base class |
| `ParquetDataCatalog` | Canonical writer: serializes Nautilus objects to Arrow, writes `{root}/data/{type}/{id}/{start}_{end}.parquet`, enforces disjoint, monotonic intervals. | Existing — `nautilus_trader.persistence.catalog.parquet` |
| `inspect.py` | Out-of-process pandas utility to load catalog slices for inspection. | Separate script; uses `catalog.query(...)` or `pd.read_parquet` |

## Recommended Project Structure

```
examples/live/bybit/recorder/        # new subpackage under existing bybit examples
├── __init__.py
├── run.py                  # entrypoint: build node, register factory, add actor, run
├── config.py              # frozen *Config classes + TOML loader -> config objects
├── recorder_actor.py      # RecorderActor: subscriptions + callbacks + rollover timer
├── catalog_writer.py      # CatalogWriter: buffer + day-partition flush logic
├── inspect.py             # standalone pandas catalog loader/viewer (run separately)
├── recorder.toml          # instrument list, data_dir, depths, bar specs, intervals
├── bybit-recorder.service # systemd unit (Restart=always, journald)
└── README.md              # deployment/run guide (setup, start/stop, monitoring)
```

### Structure Rationale

- **Live under `examples/live/bybit/recorder/`:** This is the established home for Bybit live scripts (`bybit_data_tester.py`, `bybit_options_data_collector.py` already live in `examples/live/bybit/`). A `recorder/` subpackage keeps the multi-file project cohesive without polluting the flat examples dir. **Do NOT** add a top-level `tools/` or `python/examples/` dir — neither exists in this repo and would break convention. The Rust `crates/` tree is irrelevant here (no Rust code).
- **`config.py` separate from `run.py`:** Keeps the TOML→typed-config mapping testable in isolation and matches the repo pattern of dedicated `config.py` modules.
- **`catalog_writer.py` separate from `recorder_actor.py`:** The buffer/flush logic is the one piece with non-trivial correctness constraints (sorting, disjoint intervals, day boundaries) and benefits from being unit-testable without a running node.
- **`inspect.py` is standalone:** Reading the catalog while the recorder writes it must be a separate process — never share the catalog object across the writer and reader.

## Architectural Patterns

### Pattern 1: Single Actor, all instruments × all data types (RECOMMENDED)

**What:** One `RecorderActor` subscribes to every (instrument, data_type) pair and routes each callback to a shared `CatalogWriter`. Internally, buffers are keyed by `(data_cls, identifier)` so a single instance cleanly fans out 10+ instruments × 6 data types.

**When to use:** This case. Single process, single WS connection managed by the adapter, no per-instrument isolation requirement.

**Trade-offs:**
- (+) Simplest lifecycle: one `on_start`, one rollover timer, one buffer dict, one flush path.
- (+) No cross-actor coordination for the shared day-rollover.
- (+) The adapter already multiplexes topics over one WS — splitting actors gives no I/O parallelism (Nautilus dispatches callbacks on a single event loop).
- (−) One slow/buggy handler blocks all data — mitigated by keeping handlers trivial (append only).

**Why not one-actor-per-instrument or per-data-type:** Adds N× lifecycle/timer bookkeeping and N× partial-flush coordination for zero throughput benefit, since the message bus and WS are shared and single-threaded. Reserve multi-actor only if you later exceed one WS connection's topic limit (out of scope per PROJECT.md).

**Example:**
```python
class RecorderActor(Actor):
    def on_start(self) -> None:
        for iid in self._instrument_ids:
            self.subscribe_quote_ticks(iid)
            self.subscribe_trade_ticks(iid)
            self.subscribe_order_book_deltas(iid, BookType.L2_MBP, depth=self._depth)
            for bar_type in self._bar_types_for(iid):
                self.subscribe_bars(bar_type)
            if self._is_linear(iid):
                self.subscribe_funding_rates(iid)   # rides ticker channel
        self._schedule_midnight_flush()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.writer.append(tick)            # append-only, no I/O here
    def on_trade_tick(self, tick: TradeTick) -> None:
        self.writer.append(tick)
    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        self.writer.append_each(deltas.deltas)
    def on_bar(self, bar: Bar) -> None:
        self.writer.append(bar)
    def on_funding_rate(self, fr: FundingRateUpdate) -> None:
        self.writer.append(fr)
```

### Pattern 2: Buffer-and-flush, NOT per-message append (REQUIRED by the catalog API)

**What:** Accumulate typed objects in memory per `(data_cls, identifier)`; flush a sorted batch via `catalog.write_data(batch)` on a timer and at day rollover.

**When to use:** Always, with `ParquetDataCatalog`. The API enforces this:
- `write_data` rejects an empty list (calls `PyCondition.not_empty`).
- It requires data **monotonically non-decreasing by `ts_init`** (raises `ValueError` otherwise) — so you sort the batch before writing.
- `_write_chunk` writes a **whole new parquet file** named `{start_ns}_{end_ns}.parquet`; it refuses to write if intervals overlap (the disjoint check) and **skips silently if the exact file already exists**. There is no row-level append.

**Trade-offs:**
- (+) Aligns with how the catalog is designed to be read back into backtests.
- (+) Batched Arrow writes are far cheaper than per-message file rewrites (the existing options collector's read-concat-rewrite-per-flush approach in `bybit_options_data_collector.py` is an anti-pattern at scale — see below).
- (−) In-memory buffer = data-loss window on crash. Mitigate with a short periodic flush interval (e.g. 30–60s) **in addition to** the day-rollover flush, and flush in `on_stop`.

**Example:**
```python
def flush(self) -> None:
    for (data_cls, identifier), buf in self._buffers.items():
        if not buf:
            continue
        buf.sort(key=lambda o: o.ts_init)           # required: non-decreasing ts_init
        self.catalog.write_data(buf)                 # one parquet file per call
        buf.clear()
```

### Pattern 3: Day-partition rollover via clock time-alert (NOT wall-clock polling)

**What:** Partitioning is by **filename time-range**, not directory. The catalog path is `{root}/data/{type}/{id}/{start_ns}_{end_ns}.parquet`; "partition by day" means flushing a separate file per UTC day per (type, instrument). Drive the boundary off the Nautilus clock.

**When to use:** This recorder. Schedule a `set_time_alert` for the next UTC midnight; in the callback, flush all buffers (closing the day's files) and re-arm for the following midnight. A separate shorter `set_timer` (e.g. 30–60s) handles the crash-safety periodic flush.

**Trade-offs:**
- (+) `clock.set_time_alert_ns(name, alert_time_ns, callback)` is the idiomatic Nautilus timer (confirmed in `common/component.pyx`); it fires on the engine loop, so flush runs on the same thread as handlers — no locking needed.
- (+) Keeps disjoint-interval invariant trivially satisfied: each day's file covers `[day_start, day_end)`, so consecutive days never overlap.
- (−) Live `ts_init` is real time, so a buffer spanning midnight must be split. Simplest correct rule: at the midnight alert, flush everything buffered so far (its max `ts_init` < midnight), then the next batch naturally starts in the new day. Bucket by `unix_nanos_to_dt(ts_init).date()` on append to guarantee no single flushed batch straddles a day boundary.

**Example:**
```python
def _schedule_midnight_flush(self) -> None:
    now = self.clock.utc_now()
    next_midnight = (now.normalize() + pd.Timedelta(days=1))
    self.clock.set_time_alert("day_rollover", next_midnight, self._on_rollover)

def _on_rollover(self, event) -> None:
    self.writer.flush()                 # close out the finished day's files
    self._schedule_midnight_flush()     # re-arm for the next UTC midnight
```

## Data Flow

### Message-to-Parquet Flow

```
Bybit WS frame
    ↓ (BybitDataClient parses -> Nautilus Data object)
DataEngine / MessageBus
    ↓ (routes by subscription)
RecorderActor.on_<type>(obj)         # trivial: append only
    ↓
CatalogWriter buffer[(data_cls, day, identifier)].append(obj)
    ↓ (on 30-60s timer OR UTC-midnight alert OR on_stop)
buffer.sort(key=ts_init)
    ↓
ParquetDataCatalog.write_data(batch)
    ↓ (ArrowSerializer -> pyarrow Table -> pq.write_table)
{root}/data/{type}/{id}/{start_ns}_{end_ns}.parquet
```

### Key Data Flows

1. **Quote/Trade/Bar/Deltas:** Direct typed callback → buffer → batched `write_data`. `OrderBookDeltas` is decomposed into its constituent `OrderBookDelta` rows before buffering (catalog stores deltas, not the composite).
2. **Funding rate (linear only):** `subscribe_funding_rates` → adapter subscribes the **ticker** channel (refcounted with quotes/mark/index) → `on_funding_rate(FundingRateUpdate)` → buffered like any other type. Spot instruments must be excluded (adapter raises for SPOT funding).
3. **Open interest:** GAP — see Pitfalls. The Rust adapter parses `open_interest` from the ticker payload (`crates/adapters/bybit/src/websocket/parse.rs`) but there is **no first-class Nautilus data type, subscription, or `on_open_interest` callback** exposed to Python. Recording OI requires either a custom-data path or treating it as a known limitation for v1.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 10–30 instruments × 6 types | Single actor + single WS handles comfortably. Periodic flush 30–60s. |
| ~100+ instruments / approaching topic limit | Still single process; verify against one connection's Bybit topic cap. May need to tune flush interval (memory) and `max_rows_per_group`. |
| Beyond one WS connection's topic limit | Out of scope (PROJECT.md). Would require multiple data clients / processes — only then consider Redis or sharding by instrument set. |

### Scaling Priorities

1. **First bottleneck: flush memory pressure / pause.** High-frequency deltas dominate buffer size. Fix: shorter periodic flush, and keep handlers append-only (no per-message catalog I/O).
2. **Second bottleneck: many tiny parquet files.** Day-partition + per-type/per-instrument dirs already keep file count bounded (≈ types × instruments × days). If sub-day flushing creates many files, rely on the catalog's `consolidate`/`extend_file_name` facilities offline rather than writing more frequently.

## Anti-Patterns

### Anti-Pattern 1: Read-concat-rewrite the whole parquet file on every flush

**What people do:** The existing `bybit_options_data_collector.py` does `pd.read_parquet(file)` → `concat` → `to_parquet(file)` each interval.
**Why it's wrong:** O(file_size) per flush; rewrites grow unboundedly, and it produces custom (non-catalog) parquet that backtests can't load. PROJECT.md explicitly rejects this format.
**Do this instead:** Buffer typed Nautilus objects and call `catalog.write_data(batch)`, which writes a new immutable time-ranged file per batch.

### Anti-Pattern 2: Doing I/O inside the data callback

**What people do:** Write to disk directly in `on_quote_tick`/`on_order_book_deltas`.
**Why it's wrong:** Callbacks run on the engine event loop; blocking I/O there stalls all data ingestion and risks WS backpressure/drops.
**Do this instead:** Append to an in-memory buffer in the callback; flush on a timer/alert.

### Anti-Pattern 3: Ignoring the monotonic / disjoint-interval contract

**What people do:** Pass unsorted batches or let two flushes cover overlapping time ranges.
**Why it's wrong:** `write_data` raises `ValueError` on non-decreasing `ts_init` and on non-disjoint intervals; a re-write of an identical filename is silently skipped (data loss).
**Do this instead:** Sort each batch by `ts_init` before writing; bucket buffers by UTC day so consecutive flushes never overlap; never rely on `skip_disjoint_check=True` for the live path.

### Anti-Pattern 4: Using a `Strategy` when an `Actor` suffices

**What people do:** Subclass `Strategy` (as the options collector does) for a pure recorder.
**Why it's wrong:** `Strategy` carries order-management surface this project never uses; `Actor` is the lighter, correct base for data subscription + handling (the in-repo `DataTester` uses `Actor`).
**Do this instead:** Subclass `nautilus_trader.common.actor.Actor`; all needed `subscribe_*` and `on_*` callbacks live there.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Bybit WS (linear + spot) | `BybitDataClientConfig` + `BybitLiveDataClientFactory`, registered on the node | Funding/mark/index multiplexed on the refcounted ticker channel; auto-reconnect handled by adapter |
| Filesystem (catalog) | `ParquetDataCatalog(path=...)` local `file` protocol | Writer process owns it exclusively; reader runs out-of-process |
| systemd / journald | `Restart=always` unit; Nautilus `LoggingConfig` for file logs | journald captures crash/restart; file logging for detailed traces |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| DataEngine ↔ RecorderActor | Nautilus message bus (subscribe + typed callbacks) | In-process, single-threaded loop — no locking required |
| RecorderActor ↔ CatalogWriter | Direct method call (`append`, `flush`) | Keep writer Nautilus-agnostic for unit testing |
| CatalogWriter ↔ ParquetDataCatalog | `write_data(list)` | The one hard API contract: non-empty, sorted, disjoint |
| Writer process ↔ inspect.py | Shared filesystem, read-only | Separate process; never share the catalog object |

## Suggested Build Order (dependencies)

Build bottom-up so each layer can be validated before the next depends on it:

1. **`config.py` (TOML loader + frozen config classes)** — no dependencies. Defines instrument list, data dir, depths, bar specs, flush/rollover intervals. Everything else consumes it.
2. **`catalog_writer.py` (buffer + day-flush)** — depends only on `ParquetDataCatalog` and Nautilus data types. The highest-risk correctness component (sort, disjoint, day-bucketing); build and **unit-test it in isolation** with synthetic ticks before wiring a live feed. This is the critical path.
3. **`recorder_actor.py` (subscriptions + callbacks + rollover timer)** — depends on config (for instrument list) and the writer (for `append`/`flush`). Instrument loading/validation from `self.cache.instrument(...)` must succeed in `on_start` **before** issuing subscriptions (mirror the validate-then-subscribe order in the options collector).
4. **`run.py` (entrypoint)** — depends on config + actor; wires `TradingNodeConfig`, registers `BybitLiveDataClientFactory`, adds the actor, calls `node.run()`. Smallest piece, built last for the live path.
5. **`inspect.py` (pandas utility)** — depends only on the catalog format produced by step 2; can be built in parallel with step 3/4 once the writer's output layout is fixed.
6. **`bybit-recorder.service` + `README.md`** — depend on a working `run.py`; produced once the runtime entrypoint and its CLI/config invocation are stable.

**Ordering rationale:** Config and the catalog writer have no upstream dependencies and carry the only non-trivial correctness logic, so they come first and get the most test coverage. The actor and entrypoint are thin glue over already-existing Nautilus/adapter machinery. The pandas inspector and ops artifacts (systemd unit, run guide) are validation/deployment concerns that only make sense once data is actually landing on disk.

## Sources

- `nautilus_trader/persistence/catalog/parquet.py` — `write_data`, `_write_chunk`, `_make_path`, `_timestamps_to_filename` (catalog write contract, file layout, disjoint/monotonic enforcement) [HIGH, in-repo source]
- `nautilus_trader/common/actor.pyx` — confirmed callbacks: `on_quote_tick`, `on_trade_tick`, `on_order_book_deltas`, `on_order_book_depth`, `on_bar`, `on_funding_rate`, `on_data` [HIGH]
- `nautilus_trader/common/component.pyx` — `set_time_alert` / `set_timer` clock API [HIGH]
- `nautilus_trader/adapters/bybit/data.py` — ticker-channel refcounting; funding/mark/index multiplexing; no separate funding subscription; SPOT funding rejected [HIGH]
- `crates/adapters/bybit/src/websocket/parse.rs`, `http/models.rs` — open interest parsed in ticker but not surfaced as a Python data type [HIGH]
- `nautilus_trader/test_kit/strategies/tester_data.py` — `Actor`-based subscription pattern and full `subscribe_*` surface [HIGH]
- `examples/live/bybit/bybit_data_tester.py`, `bybit_options_data_collector.py` — node wiring conventions; the read-concat-rewrite anti-pattern to avoid [HIGH]

---
*Architecture research for: Bybit live market-data recorder on NautilusTrader*
*Researched: 2026-06-13*
