# Project Research Summary

**Project:** Bybit 24/7 live market-data recorder (NautilusTrader → ParquetDataCatalog)
**Domain:** Live market-data archival service (single-process, systemd-managed)
**Researched:** 2026-06-13
**Confidence:** HIGH

## Executive Summary

This project is a 24/7 live market-data recorder for Bybit linear perps (USDT) and spot, built on this fork of NautilusTrader, writing to the official `ParquetDataCatalog` format. Experts build this kind of system by leaning entirely on framework primitives: a single `Actor`-based recorder subscribes to instruments/data-types and the Bybit adapter's existing WS reconnect/resubscribe logic, while a separate buffering/flush layer handles the one hard constraint of the catalog — `write_data()` is a batch, non-threadsafe, append-incompatible writer that enforces monotonic `ts_init` ordering and disjoint time intervals per file.

The recommended approach has two viable persistence paths. STACK.md favors the framework's first-class `StreamingConfig` + `StreamingFeatherWriter` + `convert_stream_to_data()` pipeline (near-zero custom code, automatic daily rotation). ARCHITECTURE/FEATURES/PITFALLS instead describe a custom `CatalogWriter` that buffers native objects per `(data_cls, instrument_id)` and calls `catalog.write_data()` directly on a timer + UTC-midnight rollover + `on_stop`. Both are catalog-format-compatible; the roadmap should pick one early (likely the `StreamingConfig` path for lower complexity, validated against the custom-writer pattern for restart/disjoint-interval safety) since this choice shapes nearly every subsequent phase.

The two biggest risks are: (1) catalog write-contract violations — filename collisions silently drop data, unsorted/overlapping batches raise `ValueError` and can crash the node, especially across restarts (`Restart=always` will repeatedly hit this unless a high-water-mark/disjoint-by-day discipline is built first); and (2) open interest has **no native Nautilus data type** despite being a PROJECT.md requirement — it must be custom-built (custom `Data` subclass + Arrow registration, sourced from the linear ticker or REST polling) and is flagged for a dedicated spike. Secondary risks: spot orderbook depth is capped at 50 (vs. linear's 50/200/500), funding-rate ticker dedup is needed to avoid millions of redundant rows, and all I/O must be buffer-then-flush, never inline in `on_*` callbacks.

## Key Findings

### Recommended Stack

Everything needed already ships in this fork (`nautilus_trader==1.229.0`, Python 3.12, pyarrow>=24, pandas, fsspec) — no new dependencies. The Bybit adapter (`BybitDataClientConfig`, `BybitLiveDataClientFactory`) supports a single data client with `product_types=(LINEAR, SPOT)`, satisfying the single-process/no-Redis constraint.

**Core technologies:**
- `nautilus_trader` (this fork, 1.229.0): TradingNode + Actor + Bybit adapter + persistence — the whole platform
- `BybitDataClientConfig` with `product_types=(LINEAR, SPOT)`: multiplexes both universes over one WS-managed client
- `ParquetDataCatalog` / `StreamingConfig` + `convert_stream_to_data`: official catalog persistence, daily rotation built in
- `pandas` + `catalog.query(...)`: required inspection utility, no hand-rolled parquet reads
- systemd (`Restart=always`) + journald: 24/7 supervision and logging, paired with `LoggingConfig`

### Expected Features

**Must have (table stakes):**
- Explicit configured instrument list (linear-USDT + spot), validated against the loaded instrument cache before subscribing
- Record trades, quotes, order-book deltas, bars, and funding rate as native Nautilus objects via the catalog
- Per-(type, instrument) time-ordered buffering + batched flush (timer + size, never per-message)
- UTC day-partition rollover with forced flush (catalog is not natively day-partitioned — the recorder must cut at midnight)
- Graceful SIGTERM flush in `on_stop`; disjoint/idempotent restart behavior (high-water-mark per stream)
- Heartbeat + stale-stream-warning logging; systemd unit + run guide; pandas catalog-inspection utility
- Rely entirely on the adapter's built-in reconnect/resubscribe — do not reimplement

**Should have (competitive):**
- Open-interest recording via a custom `Data` type (real net-new work — required by PROJECT.md but unsupported natively)
- Mark price + index price recording (native, nearly free once funding wiring exists)
- Reconnect/disconnect counters and gap markers in the catalog around downtime

**Defer (v2+):**
- Nightly per-day consolidation job, disk-usage/retention sweep, Prometheus metrics, historical backfill tool

### Architecture Approach

Single-process `TradingNode` with one Bybit data client (LINEAR+SPOT), feeding a single `RecorderActor` (subclass `Actor`, not `Strategy`) that subscribes to every (instrument, data-type) pair. Callbacks are append-only into in-memory buffers keyed by `(data_cls, identifier)`; a separate `CatalogWriter` (Nautilus-agnostic, unit-testable) sorts and flushes each buffer to `ParquetDataCatalog.write_data()` on a 30-60s timer, at UTC-midnight rollover, and in `on_stop`. A standalone `inspect.py` reads the catalog read-only, in a separate process.

**Major components:**
1. `config.py` — TOML-loaded frozen config (instrument list, depths, bar specs, flush/rollover intervals) — build first, no dependencies
2. `catalog_writer.py` — buffer + sort + day-bucketed flush to `write_data()` — highest-risk correctness component, unit-test in isolation
3. `recorder_actor.py` — subscriptions, typed `on_*` callbacks (append-only), midnight-rollover timer via `clock.set_time_alert`
4. `run.py` — entrypoint wiring `TradingNodeConfig` + `BybitLiveDataClientFactory` + actor + `node.run()`
5. `inspect.py` + systemd unit/README — validation and deployment artifacts, built last

### Critical Pitfalls

1. **Filename-collision silent skip** — `write_data()` skips writes (no error) when the `(start,end)` filename already exists. Avoid by bucketing buffers strictly by UTC day and ensuring flush intervals never produce identical/overlapping ranges; assert file creation after each flush.
2. **Out-of-order `ts_init` raises and kills the flush** — always sort each batch by `ts_init` before calling `write_data`, and write one `(data_cls, instrument_id)` group per call.
3. **Disjoint-interval `ValueError` on restart** — persist a per-(instrument, data_cls) high-water-mark timestamp; on restart discard/hold events at or before it so new files never overlap existing ones.
4. **Open interest has no native type/subscription** — requires a custom `Data` subclass + `register_arrow`, sourced from the linear ticker (or REST polling as fallback). Flag for a dedicated spike; mark/index price as native interim.
5. **Inline I/O in `on_*` callbacks can crash the node** — all parquet writes must be buffer-then-flush on a timer/alert; wrap flush in try/except and only clear buffers after confirmed success, so `Restart=always` doesn't compound data loss.

## Implications for Roadmap

### Phase 1: Bootstrap & Config
**Rationale:** No dependencies; everything else consumes config and the running node skeleton. Validates instrument-list loading for both LINEAR and SPOT before any subscription logic is written.
**Delivers:** `config.py` (TOML → frozen configs), minimal `run.py` that starts a `TradingNode` with `BybitDataClientConfig(product_types=(LINEAR, SPOT))`, instrument validation in `on_start` with fail-fast on missing instruments.
**Addresses:** Explicit instrument list, instrument provider load + validation (FEATURES.md table stakes)
**Avoids:** Pitfall 7 (instrument cache staleness/timing) — both product types loaded, validate-before-subscribe.

### Phase 2: Catalog Write Layer (CatalogWriter)
**Rationale:** PITFALLS.md identifies this as the single highest-risk component and the foundational dependency for everything that writes data. Must be correct and unit-tested with synthetic data before any live feed touches it.
**Delivers:** `catalog_writer.py` — per-(data_cls, identifier) buffers, sort-by-`ts_init`, day-bucketed batched `write_data()` calls, high-water-mark persistence for restart safety.
**Uses:** `ParquetDataCatalog.write_data` (STACK.md), OR `StreamingConfig`/`convert_stream_to_data` if that path is chosen instead — **this decision should be made explicitly at the start of this phase**.
**Implements:** ARCHITECTURE.md Pattern 2 (buffer-and-flush) + Pattern 3 (UTC-midnight rollover via `clock.set_time_alert`)
**Avoids:** Pitfalls 1, 2, 3, 8 (filename collisions, unsorted batches, disjoint-interval errors, wall-clock vs event-ts corruption)

### Phase 3: Live Subscriptions & Recording (Core Data Types)
**Rationale:** Depends on Phase 1 (instruments loaded) and Phase 2 (writer ready). Wires the `RecorderActor` to subscribe and route all required data types.
**Delivers:** `recorder_actor.py` subscribing trades, quotes, order-book deltas (per-product depth config), bars, and funding rate (linear only) for all configured instruments; append-only callbacks into `CatalogWriter`.
**Addresses:** FEATURES.md P1 "native-object recording of 5 data types"
**Avoids:** Pitfall 5 (spot depth ≤50 vs linear 50/200/500 — per-product-type depth validation), Pitfall 6 (no inline I/O — callbacks append only)

### Phase 4: Reliability — Restart Safety, Shutdown, Reconnect Resilience
**Rationale:** Builds on Phases 2-3; this is where the high-water-mark/disjoint logic and graceful shutdown get exercised end-to-end, plus order-book reconnect/gap handling.
**Delivers:** Graceful SIGTERM flush in `on_stop`, verified two-restarts-same-day produces no `ValueError`/duplicates, order-book reset-on-reconnect + recorded gap markers, heartbeat/stale-stream logging.
**Addresses:** FEATURES.md P1 "graceful shutdown", "idempotent restart", "heartbeat + stale-stream warning"
**Avoids:** Pitfalls 3, 6, 10 (disjoint-interval on restart, exception-kills-node, reconnect book corruption/gaps)

### Phase 5: Open Interest Spike + Mark/Index Price
**Rationale:** Isolated, high-uncertainty work that shouldn't block the core pipeline. By this point the writer and actor patterns are proven, so a custom `Data` type can slot in using the same buffer/flush machinery.
**Delivers:** Custom `OpenInterest(Data)` subclass + Arrow serializer registration, sourced from linear ticker or REST polling; `subscribe_mark_prices`/`subscribe_index_prices` wired as low-cost additions.
**Addresses:** FEATURES.md P2 "open-interest custom data type", "mark/index price recording"
**Avoids:** Pitfall 4 (OI not a native type — explicit spike per PITFALLS.md recommendation)

### Phase 6: Inspection Utility & Deployment
**Rationale:** Only makes sense once data is actually landing in the catalog with a stable layout. Produces the explicit operator-facing deliverables.
**Delivers:** `inspect.py` (pandas/`catalog.query()` wrapper, filtered by instrument + time slice), systemd unit (`Restart=always`, `EnvironmentFile`, absolute venv path), README run/deploy guide.
**Addresses:** FEATURES.md "pandas inspection utility", "systemd unit + run guide" (explicit deliverables)
**Avoids:** Integration gotchas around systemd+venv, secrets handling, journald double-logging (PITFALLS.md Integration Gotchas)

### Phase Ordering Rationale

- Config and the catalog writer have no upstream dependencies and carry the riskiest correctness logic (catalog write-contract invariants), so they're built and tested first, in isolation, with synthetic data — matching ARCHITECTURE.md's "Suggested Build Order."
- Core data-type subscriptions come after the writer is proven, so live data flows through an already-correct buffer/flush path.
- Reliability (restart/reconnect/shutdown) is sequenced as its own phase because it requires the full pipeline to exist and is explicitly called out by PITFALLS.md as needing dedicated verification (forced disconnect, two same-day restarts).
- Open interest is isolated late because it is the one requirement with no paved path — isolating it prevents its uncertainty from blocking the rest of the roadmap, while still landing within v1 scope per PROJECT.md.
- Inspection/deployment come last since they validate the final on-disk layout and runtime behavior.

### Research Flags

Needs research (research-phase recommended):
- **Phase 2 (Catalog Write Layer):** STACK.md and ARCHITECTURE.md/FEATURES.md propose two different persistence strategies (`StreamingConfig`+`convert_stream_to_data` vs. custom `CatalogWriter`+`write_data`). This must be resolved with a focused spike before implementation — it determines the shape of Phases 2-4.
- **Phase 5 (Open Interest):** No native Nautilus type/subscription exists; needs a spike to determine the feasible extraction path (ticker field vs. REST polling) and Arrow registration approach.
- **Phase 4 (Reconnect/orderbook gap handling):** Order-book reset-on-snapshot and gap-recording via `write_data(data=[], start=, end=, ...)` needs verification against the live adapter behavior.

Standard patterns (skip research-phase):
- **Phase 1 (Bootstrap & Config):** Directly mirrors `bybit_data_tester.py`, well-documented in STACK.md with exact imports/config fields.
- **Phase 3 (Subscriptions):** `subscribe_*` signatures and data classes are fully verified in `tester_data.py`; depth limits are documented per product type.
- **Phase 6 (Inspection & Deployment):** Standard pandas/systemd patterns, no novel API surface.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All findings grep/read-verified against this fork's source with file:line references; no new dependencies |
| Features | HIGH | Core findings read directly from adapter source and catalog implementation; OI gap independently confirmed |
| Architecture | HIGH | Verified against `parquet.py`, `actor.pyx`, `data.py`, `tester_data.py`; build-order rationale explicit |
| Pitfalls | HIGH | Most pitfalls verified directly against source plus official Bybit V5 docs |

**Overall confidence:** HIGH

### Gaps to Address

- **Persistence strategy conflict:** STACK.md recommends `StreamingConfig`/`convert_stream_to_data` (framework-managed feather→parquet); ARCHITECTURE/FEATURES/PITFALLS describe a hand-built `CatalogWriter` calling `write_data` directly. Resolve explicitly at the start of Phase 2 — likely via a short comparative spike, since the choice affects restart/disjoint-interval handling, file layout, and code volume across multiple phases.
- **Open interest:** Confirmed gap (no native type/subscription); exact extraction mechanism (ticker field availability to Python vs. REST poll) needs a spike before Phase 5 can be scoped accurately.
- **Order-book gap recording:** The `write_data(data=[], start=, end=, ...)` empty-range mechanism for marking gaps is described but not exercised end-to-end; verify during Phase 4.
- **Funding-rate dedup:** Ticker pushes ~100ms but funding rate changes rarely; dedup strategy (compare to last-written value) should be specified during Phase 3 implementation, not left implicit.

## Sources

### Primary (HIGH confidence)
- `pyproject.toml`, `nautilus_trader/adapters/bybit/config.py`, `__init__.py` — stack versions and config surface
- `nautilus_trader/persistence/catalog/parquet.py` — `write_data`/`_write_chunk` contract (disjoint, monotonic, filename-collision skip)
- `nautilus_trader/persistence/writer.py`, `persistence/config.py`, `system/kernel.py` — `StreamingConfig`/`StreamingFeatherWriter` path
- `nautilus_trader/common/actor.pyx`, `common/component.pyx` — subscribe surface and clock/timer API
- `nautilus_trader/test_kit/strategies/tester_data.py`, `examples/live/bybit/bybit_data_tester.py` — live wiring patterns
- `crates/adapters/bybit/src/websocket/{client.rs,parse.rs,messages.rs}`, `http/models.rs` — reconnect/resubscribe, OI/funding/mark/index parsing
- `nautilus_trader/serialization/arrow/serializer.py` — registered Arrow types (no OpenInterest)
- Official Bybit V5 docs — orderbook depth limits, ticker cadence, subscription/connection limits

### Secondary (MEDIUM confidence)
- `examples/live/bybit/bybit_options_data_collector.py` — explicit anti-pattern reference (custom parquet, wall-clock ts, inline I/O)
- `.planning/codebase/CONCERNS.md` — Arrow/Cap'n Proto schema instability across versions

---
*Research completed: 2026-06-13*
*Ready for roadmap: yes*
