# Phase 2: Full Data-Type Coverage - Research

**Researched:** 2026-06-13
**Domain:** NautilusTrader live data subscriptions + StreamingFeatherWriter/ParquetDataCatalog persistence (Bybit adapter)
**Confidence:** HIGH (all findings verified against in-repo Nautilus source; one venue-limit caveat cited from official Bybit docs)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 (Funding rate dedup):** Funding rate is deduped on value-change, not recorded on every ticker push. The strategy caches the last-seen `FundingRateUpdate.rate` per linear instrument (in-memory, per-instance) and only lets a new `FundingRateUpdate` flow to the streaming writer when the rate differs from the cached value. Goal: a handful of funding rows per instrument per day, not millions (Bybit linear ticker pushes ~100ms).
- **D-02 (Venue klines, additive):** Subscribe to Bybit's venue-native kline stream for each configured `bar_intervals` entry, per instrument — in ADDITION to trades/quotes/order-book (not a replacement, not derived-from-trades). Venue klines are an independent, exchange-accurate stream; bars can be re-derived from recorded trades later. Bar interval strings (e.g. `"1-MINUTE"`) map to a `BarType` with venue-native (EXTERNAL) aggregation source.
- **D-03 (Order-book depth validation):** Spot order-book depth is capped at 50 by the venue (per the user's stated decision). If a `[[instruments.spot]]` entry specifies `depth > 50`, the recorder fails fast at startup with a clear error naming the offending instrument and its configured depth — consistent with Phase 1 fail-fast (D-05/D-06). No silent clamping. Validation must happen before any subscription is issued.
- **D-04 (Linear-only funding/mark/index):** Funding rate, mark price, and index price subscriptions are automatic for every `[[instruments.linear]]` instrument — no new per-instrument opt-in flags in the TOML. Spot instruments never get these subscriptions.
- **Quote ticks (REC-02):** `subscribe_quote_ticks(instrument_id)` for every configured instrument (linear + spot), same pattern as Phase 1's trade-tick subscription. Add `QuoteTick` to `StreamingConfig.include_types`.

### Claude's Discretion

- Exact code organization for the new subscription/handler logic in `RecorderStrategy` (funding-rate dedup state as instance dict attributes, helper methods, etc.).
- Exact `BarType`/`BarSpecification` string construction from the `bar_intervals` config strings.
- Whether order-book-depth validation happens at config-load time (`config.py`) or in `on_start` (`strategy.py`) — as long as it is fail-fast before subscriptions are issued.
- Whether `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate` need any `CUSTOM_ENCODINGS`/Arrow-schema registration beyond Phase 1 (research resolves this below — they do NOT).

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REC-02 | Record quote ticks (best bid/ask) for each configured instrument | `self.subscribe_quote_ticks(instrument_id)` (actor.pyx:1667); add `QuoteTick` to `include_types`; `convert_stream_to_data(data_cls=QuoteTick)`. Native Arrow serializer already registered. |
| REC-03 | Record order-book deltas per configured depth for each instrument | `self.subscribe_order_book_deltas(instrument_id, book_type=BookType.L2_MBP, depth=<n>)` (actor.pyx:1448); add `OrderBookDelta` to `include_types`; `convert_stream_to_data(data_cls=OrderBookDelta)`. Bybit adapter only supports `L2_MBP`. Depth validation per D-03. |
| REC-04 | Record bars/klines per configured intervals for each instrument | One `BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL")` per `bar_intervals` entry; `self.subscribe_bars(bar_type)` (actor.pyx:1907); add `Bar` to `include_types`; `convert_stream_to_data(data_cls=Bar)`. |
| REC-05 | Record funding-rate updates for linear perpetuals (deduped) | `self.subscribe_funding_rates(instrument_id)` (actor.pyx:1862) for linear only; dedup per D-01 (see Pitfall 1 — requires `FundingRateUpdate` to be EXCLUDED from `include_types` and written by the strategy, NOT auto-written). |
| REC-06 | Record mark price and index price updates for linear perpetuals | `self.subscribe_mark_prices(instrument_id)` (actor.pyx:1772) + `self.subscribe_index_prices(instrument_id)` (actor.pyx:1817) for linear only; add `MarkPriceUpdate`, `IndexPriceUpdate` to `include_types`; `convert_stream_to_data` per type. |
</phase_requirements>

## Summary

Phase 2 is purely additive subscription + config-consumption work on top of the proven Phase 1 pipeline. Five of the six new data types — `QuoteTick`, `OrderBookDelta`, `Bar`, `MarkPriceUpdate`, `IndexPriceUpdate` — are **first-class Nautilus `Data` types with native (Rust pyo3) Arrow serializers already registered** in `nautilus_trader/serialization/arrow/serializer.py`. The sixth, `FundingRateUpdate`, is also registered, but via a **Python (not Rust) Arrow serializer** (`implementations/funding_rate_update.py`). All six therefore stream to the `StreamingFeatherWriter` and reload from `ParquetDataCatalog` with **no new `register_arrow` or `CUSTOM_ENCODINGS` work** — Phase 1's `CUSTOM_ENCODINGS` registrations (for `BybitProductType`/`BybitEnvironment`) were about config JSON serialization, a different concern that is already solved and unchanged.

The single non-trivial architectural finding concerns **D-01 (funding-rate dedup)**. The Nautilus kernel wires the writer with `self._trader.subscribe("*", self._writer.write)` (kernel.py:604) — a wildcard subscription that auto-writes EVERY message on the bus whose class is in `include_types`. The data engine **unconditionally publishes every** `FundingRateUpdate` (engine.pyx:2769). Consequently, a strategy `on_funding_rate` handler **cannot** prevent the write — by the time the handler runs, the write has already happened. The only clean dedup mechanism is to **exclude `FundingRateUpdate` from `include_types`** (so the wildcard auto-writer skips it) and have the strategy **re-publish only changed funding rates as a CustomData wrapper** that the writer DOES capture. This is the key thing the planner must get right; everything else follows the Phase 1 pattern mechanically.

**Primary recommendation:** Extend `RecorderStrategy.on_start` to (1) validate spot depth (D-03), (2) issue per-type subscriptions (quotes + book + bars for all; mark/index/funding for linear only), and extend `_convert_stream` to loop `convert_stream_to_data` over each recorded type. Widen `include_types` to `[TradeTick, QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate]` — **deliberately omitting `FundingRateUpdate`** — and persist deduped funding via a separate strategy-driven path.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Quote tick capture | Bybit DataAdapter (WS) | RecorderStrategy (subscribe) | Adapter owns the WS feed; strategy only issues the subscribe command. |
| Order-book delta capture | Bybit DataAdapter (WS) | RecorderStrategy (subscribe + depth validation) | Adapter formats `orderbook.{depth}.{symbol}`; strategy/config validates depth before subscribing. |
| Bar/kline capture | Bybit DataAdapter (WS) | RecorderStrategy (build BarType + subscribe) | Venue-native klines; strategy constructs `BarType` strings from config. |
| Mark/index price capture | Bybit DataAdapter (ticker) | RecorderStrategy (linear-only subscribe) | Delivered via ticker channel; adapter rejects SPOT; strategy gates on linear list. |
| Funding-rate capture (deduped) | RecorderStrategy (dedup + re-publish) | Bybit DataAdapter (ticker source) | Dedup is application policy — the framework auto-writes everything, so the strategy must own the gate. |
| Stream → parquet conversion | RecorderStrategy `_convert_stream` timer | ParquetDataCatalog | Per-type `convert_stream_to_data` calls (one per `data_cls`). |
| `include_types` widening | `config.build_streaming_config` | StreamingConfig | Single point that controls which auto-written types reach disk. |

## Standard Stack

### Core

No new external packages. Everything is already in the repo's pinned `nautilus_trader` build.

| Library / Module | Version | Purpose | Why Standard |
|------------------|---------|---------|--------------|
| `nautilus_trader.trading.strategy.Strategy` (via `Actor`) | repo build | Subscription API + handlers (`subscribe_*`, `on_*`) | The framework's only sanctioned way to subscribe and receive data. `[VERIFIED: nautilus_trader/common/actor.pyx]` |
| `nautilus_trader.model.data` (`QuoteTick`, `OrderBookDelta`, `Bar`, `BarType`, `FundingRateUpdate`, `MarkPriceUpdate`, `IndexPriceUpdate`) | repo build | Native `Data` types for each feed | All are first-class `Data` subclasses with registered Arrow serializers. `[VERIFIED: serialization/arrow/serializer.py]` |
| `nautilus_trader.persistence.config.StreamingConfig` | repo build | `include_types` widening point | Phase 1 persistence mechanism; unchanged. `[VERIFIED: scripts/bybit_recorder/config.py]` |
| `nautilus_trader.persistence.catalog.parquet.ParquetDataCatalog` | repo build | `convert_stream_to_data` (per `data_cls`) + typed readers | Phase 1 conversion mechanism; extended to loop over types. `[VERIFIED: persistence/catalog/parquet.py:2523]` |
| `nautilus_trader.model.enums.BookType` | repo build | `L2_MBP` for order-book subscription | Bybit adapter only accepts `L2_MBP`; rejects others with a warning. `[VERIFIED: adapters/bybit/data.py:322]` |

### Supporting

| Module | Purpose | When to Use |
|--------|---------|-------------|
| `nautilus_trader.model.data.DataType` + `Actor.publish_data` | Publish deduped funding as `CustomData` so the wildcard writer captures it | Only if implementing the "exclude + re-publish" dedup path (see Pitfall 1, Option B). `[VERIFIED: actor.pyx:2925]` |
| `nautilus_trader.test_kit.stubs.data.TestDataStubs` | Fixtures: `quote_tick`, `trade_tick`, `order_book_delta`, `order_book_deltas`, `mark_price`, `index_price`, `bar_spec_1min_last` | Wave 0 unit-test fixtures for each new type. `[VERIFIED: test_kit/stubs/data.py]` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `subscribe_order_book_deltas` (incremental L2 deltas) | `subscribe_order_book_depth` (`OrderBookDepth10` snapshots) | CONTEXT D wants full deltas at configured depth ("full order book and as much data as possible"). Deltas preserve every book mutation; `OrderBookDepth10` is fixed-10 snapshots. **Use deltas.** `[VERIFIED: actor.pyx:1448 vs 1515]` |
| Venue-native (EXTERNAL) bars | Internally-aggregated (INTERNAL) bars from trades | D-02 locks EXTERNAL (additive, exchange-accurate). INTERNAL would double-count and lose exchange semantics. **Use EXTERNAL.** `[VERIFIED: examples/live/bybit/bybit_data_tester.py:76]` |
| Strategy-owned funding dedup | Subscribe + let writer auto-write all funding | Auto-write floods the catalog (~100ms ticker). D-01 forbids this. **Strategy must dedup.** `[VERIFIED: data/engine.pyx:2769 + kernel.py:604]` |

**Installation:** None — no packages installed this phase.

## Architecture Patterns

### System Architecture Diagram

```
                       recorder.toml
                            │
              load_recorder_config() ── parses [[instruments.linear]] / [[instruments.spot]]
                            │                with id, depth, bar_intervals
                            ▼
         ┌──────────────────────────────────────────────┐
         │ RecorderStrategy.on_start()                   │
         │  1. validate instruments in cache (Phase 1)   │
         │  2. validate spot depth <= 50 (D-03)  ◄── fail-fast, before any subscribe
         │  3. per instrument (linear+spot):             │
         │       subscribe_trade_ticks                   │
         │       subscribe_quote_ticks                   │
         │       subscribe_order_book_deltas(depth)      │
         │       for interval in bar_intervals:          │
         │           subscribe_bars(BarType -EXTERNAL)   │
         │  4. linear instruments only:                  │
         │       subscribe_mark_prices                   │
         │       subscribe_index_prices                  │
         │       subscribe_funding_rates  (deduped path) │
         └──────────────────────────────────────────────┘
                            │ Subscribe* commands
                            ▼
              DataEngine ──► BybitDataClient (one WS conn per product type)
                            │  publishes Data on per-topic bus channels
                            ▼
          MessageBus ──┬──► Strategy.on_quote_tick / on_bar / on_funding_rate ...
                       │
                       └──► StreamingFeatherWriter.write   (subscribed to "*" )
                              │  filters by include_types
                              │  writes per-day feather under {streaming_path}/live/{instance_id}/
                              ▼
        RecorderStrategy._convert_stream (timer) ──► ParquetDataCatalog.convert_stream_to_data
                              │  ONE call per data_cls (TradeTick, QuoteTick, OrderBookDelta, Bar,
                              │  MarkPriceUpdate, IndexPriceUpdate, [+ deduped funding])
                              ▼
                 ParquetDataCatalog (day-partitioned parquet)  ◄── backtest/pandas read-back
```

### Recommended Project Structure

No new files required. All changes land in the three existing Phase 1 files:

```
scripts/bybit_recorder/
├── config.py        # widen include_types; (optional) spot-depth validation at load
├── strategy.py      # new subscriptions in on_start; funding dedup state; per-type _convert_stream loop
└── recorder.toml    # already carries depth + bar_intervals (parsed in Phase 1, consumed now)
```

### Pattern 1: Per-instrument subscription loop with linear-vs-spot gating

**What:** Issue universal subscriptions for every instrument, then linear-only subscriptions gated on the config's structural split (`[[instruments.linear]]` vs `[[instruments.spot]]`).
**When to use:** D-04 — no new TOML flags; reuse the existing structural separation.
**Example:**
```python
# Source: pattern derived from nautilus_trader/test_kit/strategies/tester_data.py:123-256 (VERIFIED)
# RecorderStrategyConfig should carry the linear/spot split (e.g. linear_ids + spot_ids,
# or a per-entry product-type tag) so on_start can gate. Discretion on exact shape.
for instrument_id in self.config.instrument_ids:
    self.subscribe_trade_ticks(instrument_id)
    self.subscribe_quote_ticks(instrument_id)
    self.subscribe_order_book_deltas(
        instrument_id=instrument_id,
        book_type=BookType.L2_MBP,   # Bybit only supports L2_MBP
        depth=depth_for[instrument_id],
    )
    for interval in bar_intervals_for[instrument_id]:
        bar_type = BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL")
        self.subscribe_bars(bar_type)

for instrument_id in self.config.linear_instrument_ids:
    self.subscribe_mark_prices(instrument_id)
    self.subscribe_index_prices(instrument_id)
    self.subscribe_funding_rates(instrument_id)   # see funding dedup pattern below
```

### Pattern 2: BarType string from config interval

**What:** Map a `bar_intervals` string like `"1-MINUTE"` to a venue-native `BarType`.
**Canonical format (VERIFIED):** `f"{instrument_id}-{interval}-LAST-EXTERNAL"`, e.g. `BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL`.
**Example:**
```python
# Source: examples/live/bybit/bybit_data_tester.py:76 (VERIFIED — official Bybit example)
bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
```
Notes (VERIFIED, adapters/bybit/data.py:362-368, 806-833):
- The Bybit adapter calls `nautilus_pyo3.BarType.from_str(str(command.bar_type))` and forwards to `ws_client.subscribe_bars`. Invalid/unsupported `BarType` strings raise inside pyo3 — fail-fast.
- `PriceType` must be `LAST` and aggregation must be `EXTERNAL` (the adapter explicitly errors on INTERNAL aggregation and non-LAST price for historical requests; for live subscriptions the venue only emits LAST/EXTERNAL klines).
- The config string `"1-MINUTE"` already encodes `{step}-{aggregation}` matching Nautilus `BarSpecification` step+unit. Construction is pure string interpolation; no manual `BarSpecification` object needed.

### Pattern 3: Funding-rate dedup (the critical one — see Pitfall 1 for why)

**What:** Cache last-seen rate per instrument; only persist on change.
**Recommended mechanism (Option B — "exclude + re-publish"):**
```python
# Source: composed from VERIFIED facts:
#   - kernel wires writer to "*" with include_types filter (kernel.py:604, writer.py:190)
#   - data engine publishes EVERY FundingRateUpdate (engine.pyx:2769)
#   - Actor.publish_data publishes CustomData the "*" writer captures (actor.pyx:2925)
# => FundingRateUpdate is EXCLUDED from include_types so the auto-writer ignores the
#    high-frequency native stream; the strategy re-emits only changed values.

def on_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
    last = self._last_funding_rate.get(funding_rate.instrument_id)
    if last is not None and last == funding_rate.rate:
        return  # unchanged — drop
    self._last_funding_rate[funding_rate.instrument_id] = funding_rate.rate
    # Re-publish so the StreamingFeatherWriter("*") persists it.
    # Discretion: publish as CustomData(FundingRateUpdate) OR write the native
    # FundingRateUpdate to a strategy-held catalog. See Pitfall 1 for the tradeoffs.
    self.publish_data(DataType(FundingRateUpdate), funding_rate)
```
> The planner MUST resolve, during planning, exactly how the deduped funding row is persisted AND how it reads back (native `catalog.funding_rates(...)` vs `catalog.custom_data(FundingRateUpdate)`), because `publish_data` writes under the `custom_*` table namespace, not the native `funding_rate_update` table. This is the one place where a quick empirical spike (write one deduped funding row, convert, read back) is warranted before committing the approach — exactly mirroring Phase 1's A2/A3 empirical resolution discipline. See Open Question 1.

### Anti-Patterns to Avoid

- **Relying on `on_funding_rate` to suppress the write.** The write already happened (writer is upstream on `"*"`, engine publishes unconditionally). Dropping inside the handler does nothing. `[VERIFIED]`
- **Adding `FundingRateUpdate` to `include_types` AND trying to dedup.** Guarantees the un-deduped flood — `include_types` is an allow-list for the auto-writer, not a dedup hook. `[VERIFIED: writer.py:190]`
- **Subscribing mark/index/funding for spot instruments.** The Bybit adapter logs a warning and returns without subscribing (adapters/bybit/data.py:377, 398, 420). Gate on the linear list instead of relying on the warning. `[VERIFIED]`
- **Calling `convert_stream_to_data` once and expecting all types.** It takes a single `data_cls` per call (parquet.py:2526) and only converts feather files for that one type. Must loop. `[VERIFIED]`
- **Using `OrderBookDepth10`/`subscribe_order_book_depth` when deltas are wanted.** Different feed, different table, fixed depth. `[VERIFIED]`

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket subscribe/reconnect/resubscribe for each feed | Custom WS client | `self.subscribe_*` + Bybit adapter | Adapter multiplexes all feeds over one WS per product type and owns reconnect (REL-04, out of this phase). `[VERIFIED]` |
| Arrow schema for any of the 6 types | `register_arrow(...)` calls | Already registered in `serializer.py` | All 6 have serializers (5 Rust-native, funding Python). Re-registering risks "defined twice" conflicts. `[VERIFIED: serializer.py:389-521]` |
| Feather → parquet day-partition writer | Custom writer | `ParquetDataCatalog.convert_stream_to_data` per type | Phase 1 already proved idempotent re-conversion (A2). `[VERIFIED]` |
| BarType / BarSpecification parsing | Manual spec object construction | `BarType.from_str(...)` | Single canonical parse path the adapter also uses. `[VERIFIED]` |
| INTERNAL→EXTERNAL bar relabel at conversion | Manual metadata rewrite | `convert_stream_to_data` does it automatically | `_apply_stream_conversion_transforms(convert_bar_type_to_external=True)` rewrites `-INTERNAL`→`-EXTERNAL` (no-op for us since we subscribe EXTERNAL). `[VERIFIED: parquet.py:2642]` |

**Key insight:** This phase is a thin orchestration layer over already-built framework machinery. The only genuinely new logic is the funding-rate dedup gate (D-01) — and even that is constrained by where the framework writes data, so the "clever" part is choosing the right framework seam, not writing an algorithm.

## Runtime State Inventory

> This is an additive feature phase, not a rename/refactor/migration. No stored data keys, live-service config, OS-registered state, secrets, or build artifacts are being renamed or migrated.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — new data types write to NEW catalog tables (`quote_tick`, `order_book_delta`, `bar`, `mark_price_update`, `index_price_update`, funding). Existing `trade_tick` table untouched. | None |
| Live service config | None — no external service config keys change. Bybit WS subscriptions are issued at runtime, not persisted. | None |
| OS-registered state | None — no systemd unit changes in this phase (OPS-02 is Phase 5). | None |
| Secrets/env vars | None — funding/mark/index are PUBLIC Bybit feeds (no API key, consistent with Phase 1 D-10). | None |
| Build artifacts | None — no package rename; `scripts/bybit_recorder/` package metadata unchanged. | None |

**Nothing found in any category — verified by reviewing the diff surface (config.py, strategy.py, recorder.toml only) and confirming new data types use new catalog table directories.**

## Common Pitfalls

### Pitfall 1: Funding-rate dedup cannot be done in the handler (THE critical pitfall)

**What goes wrong:** You add `FundingRateUpdate` to `include_types`, subscribe, and try to dedup in `on_funding_rate`. The catalog still fills with ~100ms-frequency funding rows; your dedup does nothing.
**Why it happens:** The kernel subscribes the writer to `"*"` (kernel.py:604). The data engine publishes EVERY funding update (engine.pyx:2769). The writer runs on the same bus dispatch and writes before/independently of your strategy handler. `include_types` is an allow-list, not a change-filter (writer.py:190).
**How to avoid:** EXCLUDE `FundingRateUpdate` from `include_types`, and have the strategy re-emit only changed values via `publish_data(DataType(FundingRateUpdate), fr)` (captured by the `"*"` writer as `custom_funding_rate_update`) — OR write the deduped native object via a strategy-held `ParquetDataCatalog`. The two paths differ in read-back API and table name; resolve empirically (Open Question 1).
**Warning signs:** Funding parquet row count grows by thousands/hour per instrument instead of a handful/day; funding rows appear under `funding_rate_update/` despite "dedup" being implemented.

### Pitfall 2: `convert_stream_to_data` is per-type

**What goes wrong:** Only `TradeTick` keeps converting; quotes/bars/book never appear in the catalog despite feather files existing.
**Why it happens:** `convert_stream_to_data(data_cls=...)` converts exactly one type's feather files (parquet.py:2557 lists files filtered by `data_cls`). Phase 1's `_convert_stream` hard-codes `data_cls=TradeTick`.
**How to avoid:** Loop the conversion over every recorded type. Wrap each per-type call in its own try/except so one type's transient error (A2/Pitfall: non-disjoint intervals) does not block the others.
**Warning signs:** `catalog.quote_ticks()` returns empty while `catalog/streaming/live/{id}/quote_tick/...feather` exists on disk.

### Pitfall 3: Spot depth — venue reality differs from the locked assumption (D-03)

**What goes wrong:** The locked decision says "spot is capped at 50." Current official Bybit v5 docs say spot supports the **discrete set {1, 50, 200, 1000}** — the same set as linear/inverse — NOT a continuous "≤ 50" cap.
**Why it happens:** Bybit raised spot orderbook depth support over time; the "spot max 50" belief reflects older API behavior.
**How to avoid:** Implement D-03 as the user specified (fail fast on spot `depth > 50`) to honor the locked decision, BUT surface this discrepancy to the user before finalizing — they may want to (a) allow spot up to 200/1000, and/or (b) validate against the discrete valid set `{1, 50, 200, 1000}` for ALL product types rather than a single spot cap, since a value like `depth=75` is invalid on EVERY product type, not just spot. The Bybit adapter does NOT validate depth itself — it formats `orderbook.{depth}.{symbol}` and a bad value silently yields no data. So validating against the discrete set is materially safer than a single threshold. `[CITED: bybit-exchange.github.io/docs/v5/websocket/public/orderbook]`
**Warning signs:** A configured spot instrument with `depth=200` is rejected by your validation but would actually work on Bybit; or a `depth=75` on a linear instrument passes your "spot-only ≤50" check yet produces no order-book data at runtime.

### Pitfall 4: Mark/index/funding only exist for non-spot

**What goes wrong:** Subscribing mark/index/funding for a spot instrument produces only a warning log and no data; if your code assumes a stream, a quiet-stream alarm (Phase 3) could false-fire.
**Why it happens:** Adapter early-returns for SPOT (data.py:377/398/420).
**How to avoid:** Gate these three subscriptions on the linear list (D-04). Never iterate the full instrument list for them.
**Warning signs:** "Cannot subscribe to mark prices for SPOT instrument ..." warnings in journald.

### Pitfall 5: Spot quote ticks arrive via an order-book depth=1 subscription, not a ticker

**What goes wrong:** You assume spot quotes come from the same ticker channel as linear; depth/refcount bookkeeping surprises you, or a spot quote subscription appears to also occupy a book channel.
**Why it happens:** For SPOT, the adapter implements `subscribe_quote_ticks` as `subscribe_orderbook(depth=1)` (data.py:346-349); for linear it uses the refcounted ticker channel. This is internal to the adapter and transparent to the strategy — but it means a spot instrument with BOTH `subscribe_quote_ticks` and `subscribe_order_book_deltas(depth=N)` opens two separate orderbook subscriptions (depth=1 and depth=N). That is expected and fine for a recorder.
**How to avoid:** Nothing required — just be aware when reasoning about why a spot quote feed behaves like a tiny book. No code change.
**Warning signs:** None harmful; informational.

## Code Examples

### Widen include_types (config.py) — note FundingRateUpdate is intentionally absent

```python
# Source: extends scripts/bybit_recorder/config.py:179-193 (VERIFIED Phase 1 code)
from nautilus_trader.model.data import (
    TradeTick, QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate,
)

return StreamingConfig(
    catalog_path=recorder_cfg.streaming_path,
    fs_protocol="file",
    rotation_mode=RotationMode.SCHEDULED_DATES,
    rotation_interval=pd.Timedelta(days=1),
    rotation_time=time(0, 0, 0),
    rotation_timezone="UTC",
    include_types=[
        TradeTick, QuoteTick, OrderBookDelta, Bar,
        MarkPriceUpdate, IndexPriceUpdate,
        # FundingRateUpdate intentionally OMITTED — deduped via strategy (D-01 / Pitfall 1)
    ],
)
```

### Per-type conversion loop (strategy.py `_convert_stream`)

```python
# Source: extends scripts/bybit_recorder/strategy.py:112-132 (VERIFIED Phase 1 code)
_RECORDED_TYPES = [TradeTick, QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate]
# (+ the funding type / CustomData per the dedup path chosen in planning)

def _convert_stream(self, event: TimeEvent) -> None:
    catalog = ParquetDataCatalog(self.config.catalog_path)
    for data_cls in _RECORDED_TYPES:
        try:
            catalog.convert_stream_to_data(
                instance_id=self.config.instance_id_str,
                data_cls=data_cls,
                subdirectory="live",
            )
        except Exception:
            logger.exception("Failed to convert %s stream to catalog", data_cls.__name__)
```

### Catalog read-back accessors (for verification)

```python
# Source: nautilus_trader/persistence/catalog/base.py:148-218 (VERIFIED)
catalog.quote_ticks(instrument_ids=["BTCUSDT-LINEAR.BYBIT"])           # native accessor
catalog.trade_ticks(instrument_ids=[...])                              # Phase 1
catalog.order_book_deltas(instrument_ids=[...])                        # native accessor
catalog.bars(bar_types=["BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL"]) # native accessor
catalog.funding_rates(instrument_ids=[...])                           # native accessor (if native write path)
# NO mark_prices()/index_prices() accessors exist — use query():
catalog.query(data_cls=MarkPriceUpdate, identifiers=["BTCUSDT-LINEAR.BYBIT"])
catalog.query(data_cls=IndexPriceUpdate, identifiers=["BTCUSDT-LINEAR.BYBIT"])
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| "Bybit spot orderbook max depth = 50" | Bybit v5 spot supports {1, 50, 200, 1000} | Bybit v5 API evolution | D-03 assumption is stale; validate against discrete set, surface to user (Pitfall 3). `[CITED: bybit docs]` |
| Phase 1: `include_types=[TradeTick]`, single-type conversion | Multi-type allow-list + per-type conversion loop | This phase | Mechanical extension; funding is the exception (excluded). |

**Deprecated/outdated:** None relevant. No deprecated Nautilus APIs are in the subscription path.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The cleanest funding dedup is "exclude from include_types + strategy re-publish via `publish_data`", read back as `custom_funding_rate_update` | Pattern 3 / Pitfall 1 | If `publish_data`'s CustomData table doesn't round-trip cleanly for a native `Data` subclass, an alternate path (strategy-held catalog native write) is needed. **Mitigated by Open Question 1 empirical spike.** `[ASSUMED]` |
| A2 | Bybit live klines are emitted with `LAST`/`EXTERNAL` aggregation matching `{iid}-{interval}-LAST-EXTERNAL` | Pattern 2 | If a configured interval string isn't a valid Bybit kline interval, `BarType.from_str`/pyo3 raises at subscribe — fail-fast, low risk. Verified format against official example. `[VERIFIED example, ASSUMED for arbitrary interval strings]` |

**Note:** Bybit's exact set of valid kline interval strings (1/3/5/15/30/60/120/240/360/720-MINUTE, DAY, WEEK, MONTH equivalents) was not enumerated from source in this session; the `"1-MINUTE"` form is verified. If the user configures unusual intervals, validate empirically. `[ASSUMED]`

## Open Questions

1. **Funding dedup persistence + read-back path (D-01).**
   - What we know: auto-writer cannot be deduped; strategy must own the gate; `publish_data` reaches the writer as `CustomData`.
   - What's unclear: whether the deduped funding object round-trips as a native `catalog.funding_rates(...)` read or only as `catalog.custom_data(FundingRateUpdate)`, and which the operator/backtest consumer expects.
   - Recommendation: a tiny empirical spike during planning/execution (write 2 deduped funding rows, convert, read back both ways) — mirrors Phase 1's A2/A3 discipline. Pick the path that reads back as a native `FundingRateUpdate`.

2. **Spot depth validation policy (D-03 vs venue reality).**
   - What we know: locked decision = fail on spot `depth > 50`; venue actually supports {1,50,200,1000} for spot.
   - What's unclear: whether the user wants to keep the strict 50 cap or adopt the discrete valid-set check across all product types.
   - Recommendation: implement D-03 as written to honor the lock, but flag the discrepancy in the plan's checkpoint so the user can confirm or relax. Strongly consider validating `depth ∈ {1,50,200,1000}` for ALL instruments as an additional fail-fast (a bad value yields silent no-data).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `nautilus_trader` build (Cython + pyo3 extensions compiled) | All subscriptions, serializers | ✓ (Phase 1 ran live) | repo build | — |
| Bybit mainnet public WS (no API key) | All six feeds | ✓ (Phase 1 smoke connected) | v5 | testnet if needed |
| `pytest` | Wave 0 unit tests | ✓ | 7.4.4 (CLAUDE.md) | — |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.4.4 |
| Config file | repo `pyproject.toml` / pytest settings (existing) |
| Quick run command | `pytest tests/unit_tests/persistence/recorder/ -q` |
| Full suite command | `pytest tests/unit_tests/persistence/recorder/ -q` (this milestone's scoped suite) |

### How each new type is verified to land in the catalog

The Phase 1 GREEN pattern (`test_recorder_conversion.py`): write sample objects via `StreamingFeatherWriter(include_types=[T])` → `catalog.convert_stream_to_data(data_cls=T, subdirectory="live")` → read back via the typed accessor and assert non-empty. Extend this once per new type.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REC-02 | QuoteTick round-trips feather→parquet→`catalog.quote_ticks()` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_conversion.py -k quote -q` | ❌ Wave 0 |
| REC-03 | OrderBookDelta round-trips → `catalog.order_book_deltas()` | unit | `pytest .../test_recorder_conversion.py -k order_book -q` | ❌ Wave 0 |
| REC-03 | Spot depth > 50 fails fast at startup/config-load (D-03) | unit | `pytest .../test_recorder_strategy.py -k depth -q` (or test_recorder_config.py) | ❌ Wave 0 |
| REC-04 | Bar round-trips → `catalog.bars(bar_types=[...-LAST-EXTERNAL])` | unit | `pytest .../test_recorder_conversion.py -k bar -q` | ❌ Wave 0 |
| REC-04 | `BarType.from_str(f"{iid}-{interval}-LAST-EXTERNAL")` builds for each config interval | unit | `pytest .../test_recorder_strategy.py -k bartype -q` | ❌ Wave 0 |
| REC-05 | Funding dedup: only changed rates persist; unchanged dropped | unit | `pytest .../test_recorder_strategy.py -k funding_dedup -q` | ❌ Wave 0 |
| REC-05 | Deduped funding round-trips and reads back as FundingRateUpdate | unit | `pytest .../test_recorder_conversion.py -k funding -q` | ❌ Wave 0 |
| REC-06 | MarkPriceUpdate + IndexPriceUpdate round-trip → `catalog.query(...)` | unit | `pytest .../test_recorder_conversion.py -k "mark or index" -q` | ❌ Wave 0 |
| REC-04/D-04 | Linear-only mark/index/funding gating (no spot subscribe) | unit | `pytest .../test_recorder_strategy.py -k linear_only -q` | ❌ Wave 0 |
| All | Live smoke: short Bybit run records all six types for 1 linear + 1 spot, converts, reloads | manual (checkpoint:human-verify) | run `python -m scripts.bybit_recorder.recorder` with `conversion_interval_minutes=1` (mirror Phase 1 Plan 04 smoke) | manual |

### Sampling Rate
- **Per task commit:** `pytest tests/unit_tests/persistence/recorder/ -k <new test> -q`
- **Per wave merge:** `pytest tests/unit_tests/persistence/recorder/ -q`
- **Phase gate:** full scoped suite green + one live smoke (human-verify) proving all six feeds reach the catalog, mirroring Phase 1's approved E2E smoke.

### Wave 0 Gaps
- [ ] Conversion round-trip tests for QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate, deduped funding — extend `test_recorder_conversion.py` (reuse the `catalog_dir` fixture + `StreamingFeatherWriter` pattern).
- [ ] Fixtures: sample lists for each new type with strictly monotonic `ts_init`. `TestDataStubs` provides `quote_tick`, `order_book_delta(s)`, `mark_price`, `index_price`, `bar_spec_1min_last`; `FundingRateUpdate` has no stub — construct directly (`instrument_id`, `rate`, `ts_event`, `ts_init`). Add to `conftest.py`.
- [ ] Funding-dedup behavior test (drop unchanged, persist changed) on `RecorderStrategy`.
- [ ] Spot depth-validation fail-fast test.
- [ ] Linear-only gating test (spot instrument does NOT get mark/index/funding subscriptions).
- [ ] Framework install: none — already present.

## Security Domain

> `security_enforcement` is not set in `.planning/config.json` (treat as enabled). This phase adds NO new trust boundaries: all six feeds are PUBLIC Bybit market data over the WS connection already established and reviewed in Phase 1; no API keys, no new network endpoints, no user input beyond the TOML already validated in Phase 1.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Public market data only; no credentials introduced. |
| V3 Session Management | no | N/A (recorder, no sessions). |
| V4 Access Control | no | N/A. |
| V5 Input Validation | yes | TOML `depth` and `bar_intervals` are the only new consumed inputs. Validate `depth` against the venue's discrete valid set (fail fast, D-03); `BarType.from_str` validates interval strings at subscribe (fail fast). |
| V6 Cryptography | no | TLS handled by the Bybit adapter (rustls); nothing hand-rolled. |
| V7 (Logging) | yes (carried from Phase 1 D-09) | Continue logging instrument counts only, never credentials. Unchanged. |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed `depth`/`bar_intervals` in TOML → silent no-data feed | Tampering / DoS-of-data | Fail-fast validation before subscription (D-03 + discrete-set check); `BarType.from_str` pyo3 validation. |
| Unbounded catalog growth from funding flood | Resource exhaustion | D-01 dedup (Pitfall 1) — the core mitigation this phase implements. |

## Sources

### Primary (HIGH confidence — in-repo source verified this session)
- `nautilus_trader/common/actor.pyx:1448–1955` — `subscribe_order_book_deltas`, `subscribe_quote_ticks`, `subscribe_trade_ticks`, `subscribe_mark_prices`, `subscribe_index_prices`, `subscribe_funding_rates`, `subscribe_bars` signatures; `on_*` handlers (430–591); `publish_data` (2925).
- `nautilus_trader/adapters/bybit/data.py:322–432, 806–833, 880–921` — Bybit subscription routing, SPOT early-returns for mark/index/funding, quote-via-orderbook-depth-1, BarType handling, `_handle_msg` native-type dispatch.
- `nautilus_trader/serialization/arrow/serializer.py:64–447, 516–521` — `RUST_SERIALIZERS` set, `NAUTILUS_ARROW_SCHEMA`, and the `FundingRateUpdate` Python-serializer registration; confirms all six types are registered, no extra work.
- `nautilus_trader/persistence/writer.py:57–260` — `StreamingFeatherWriter.write`, `include_types` allow-list filter (190).
- `nautilus_trader/persistence/catalog/parquet.py:2523–2705` — `convert_stream_to_data` (per-`data_cls`), INTERNAL→EXTERNAL bar relabel, idempotent skip.
- `nautilus_trader/persistence/catalog/base.py:148–227` — typed read accessors (no `mark_prices`/`index_prices`).
- `nautilus_trader/data/engine.pyx:2539–2776` — unconditional `FundingRateUpdate` publish.
- `nautilus_trader/system/kernel.py:587–611` — writer wired to `"*"`; path `{catalog_path}/{environment}/{instance_id}`.
- `examples/live/bybit/bybit_data_tester.py:76` + `nautilus_trader/test_kit/strategies/tester_data.py:123–256` — canonical BarType string + subscription call patterns.
- `nautilus_trader/model/data.pyx:6093–6146` — `FundingRateUpdate` (has `.rate`, equality on rate) for dedup.
- Phase 1 artifacts: `scripts/bybit_recorder/{strategy,config,recorder}.py`, `recorder.toml`, `01-04-SUMMARY.md`, `tests/unit_tests/persistence/recorder/`.

### Secondary (MEDIUM confidence — official venue docs)
- Bybit v5 WebSocket Orderbook depth levels — https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook (spot supports {1,50,200,1000}; topic `orderbook.{depth}.{symbol}`).

### Tertiary (LOW confidence)
- General Bybit depth web search (cross-checked against the official docs above).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every module/method read directly from the compiled repo source.
- Architecture (subscriptions, persistence, dedup constraint): HIGH — kernel/engine/writer wiring confirmed in source; dedup constraint is a deduction from three verified facts.
- Pitfalls: HIGH for framework pitfalls (verified); MEDIUM for the spot-depth venue caveat (official docs, contradicts the locked assumption — flagged).
- Funding dedup persistence path: MEDIUM — mechanism is sound but the exact read-back table warrants a small empirical spike (Open Question 1).

**Research date:** 2026-06-13
**Valid until:** ~2026-07-13 (30 days; Nautilus is a pinned in-repo build so the source facts are stable for this milestone; the Bybit venue-depth fact is external and could drift).
