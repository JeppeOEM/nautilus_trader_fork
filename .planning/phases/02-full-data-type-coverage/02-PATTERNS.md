# Phase 2: Full Data-Type Coverage - Pattern Map

**Mapped:** 2026-06-13
**Files analyzed:** 7 (3 source modified, 4 test modified/added)
**Analogs found:** 7 / 7 (every file extends an existing Phase 1 file or a verified framework example)

## Overview

This phase is purely additive on top of Phase 1. There are **no new files** — every change extends an existing Phase 1 module or test file. The closest analog for every new subscription / conversion / test is the corresponding Phase 1 `TradeTick` code in the same file. The only genuinely new pattern (no Phase 1 analog) is the funding-rate dedup gate (D-01), whose analog is the verified Nautilus `publish_data` API, not existing recorder code.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `scripts/bybit_recorder/config.py` | config | transform / batch | itself (Phase 1 `build_streaming_config` + `InstrumentEntry`) | exact (same file) |
| `scripts/bybit_recorder/strategy.py` | strategy (Actor) | streaming / event-driven | itself (Phase 1 `on_start` trade-tick subscribe + `_convert_stream`) | exact (same file) |
| `scripts/bybit_recorder/recorder.py` | config / bootstrap | request-response | itself (Phase 1 `CUSTOM_ENCODINGS` + node build) | exact (same file) — likely UNCHANGED |
| `scripts/bybit_recorder/recorder.toml` | config | — | itself (Phase 1 already carries `depth` + `bar_intervals`) | exact — likely UNCHANGED |
| `tests/.../recorder/test_recorder_conversion.py` | test | streaming round-trip | `test_convert_stream_to_data_roundtrips_trade_ticks` (same file) | exact (same file) |
| `tests/.../recorder/test_recorder_strategy.py` | test | event-driven | `test_on_start_subscribes_trade_ticks_per_instrument` (same file) | exact (same file) |
| `tests/.../recorder/conftest.py` | test fixtures | — | `sample_trade_ticks` / `mock_cache` (same file) | exact (same file) |

## Pattern Assignments

### `scripts/bybit_recorder/config.py` (config, transform)

**Analog:** Phase 1 `build_streaming_config` + `InstrumentEntry` (this same file).

**Import pattern to extend** (config.py lines 25-28):
```python
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode
```
Add the new native `Data` types to the `nautilus_trader.model.data` import:
`QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate` (and `BarType` if interval validation lives here). **Do NOT import `FundingRateUpdate` into `include_types`** — it is deliberately omitted (D-01 / Pitfall 1).

**`include_types` widening point** (config.py lines 179-193 — the SINGLE place to widen):
```python
    return StreamingConfig(
        catalog_path=recorder_cfg.streaming_path,
        fs_protocol="file",
        rotation_mode=RotationMode.SCHEDULED_DATES,
        rotation_interval=pd.Timedelta(days=1),
        rotation_time=time(0, 0, 0),
        rotation_timezone="UTC",
        include_types=[TradeTick],   # <-- widen here
    )
```
Target (per RESEARCH §"Widen include_types"):
```python
include_types=[
    TradeTick, QuoteTick, OrderBookDelta, Bar,
    MarkPriceUpdate, IndexPriceUpdate,
    # FundingRateUpdate intentionally OMITTED — deduped via strategy (D-01 / Pitfall 1)
]
```

**Instrument-entry / linear-spot split pattern** (config.py lines 34-53, 85-95, 128-142):
- `InstrumentEntry` already parses `depth: PositiveInt` and `bar_intervals: list[str]` (lines 51-53) — Phase 2 consumes these unused fields. No schema change needed.
- The linear-vs-spot structural split already exists in `load_recorder_config` (lines 128-133: `linear_raw`, `spot_raw`, iterated `(*linear_raw, *spot_raw)`). To gate linear-only subscriptions (D-04) the planner must surface this split to the strategy. **Discretion (per CONTEXT):** add a `product_type` tag to `InstrumentEntry`, OR expose a `linear_instrument_ids` property on `RecorderConfig` mirroring the existing `instrument_ids` property (lines 85-95). The existing `instrument_ids` property is the exact pattern to copy.

**Fail-fast validation pattern to copy** (D-03 spot-depth, if done at load time) — copy the `InstrumentId.from_str` V5-boundary `ValueError` pattern (lines 134-142): raise immediately on bad input, name the offending instrument, never silently clamp. See RESEARCH Pitfall 3 / Open Question 2: prefer validating `depth ∈ {1, 50, 200, 1000}` and flag the stale "spot ≤ 50" assumption at the planning checkpoint.

---

### `scripts/bybit_recorder/strategy.py` (strategy/Actor, streaming/event-driven)

**Analog:** Phase 1 `RecorderStrategy.on_start` + `on_trade_tick` + `_convert_stream` (this same file).

**`on_start` subscription loop** (strategy.py lines 72-100) — the analog for ALL new subscriptions:
```python
    def on_start(self) -> None:
        missing: list[str] = []
        for instrument_id in self.config.instrument_ids:
            if self.cache.instrument(instrument_id) is None:
                missing.append(str(instrument_id))
        if missing:
            raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")

        for instrument_id in self.config.instrument_ids:
            self.subscribe_trade_ticks(instrument_id)

        self.clock.set_timer(
            name="convert-stream",
            interval=pd.Timedelta(minutes=self.config.conversion_interval_minutes),
            callback=self._convert_stream,
        )
```
Extend the existing per-instrument loop (lines 93-94) with the new universal subscriptions, then add a separate linear-only loop. Verified `Actor` subscribe signatures (`nautilus_trader/common/actor.pyx`):

| Subscription | Method (actor.pyx line) | Scope |
|--------------|-------------------------|-------|
| Quote ticks | `self.subscribe_quote_ticks(instrument_id)` (1667) | all instruments |
| Order book deltas | `self.subscribe_order_book_deltas(instrument_id, book_type=BookType.L2_MBP, depth=<n>)` (1448) | all instruments — Bybit only accepts `L2_MBP` |
| Bars | `self.subscribe_bars(bar_type)` (1907) | all instruments, one per interval |
| Mark prices | `self.subscribe_mark_prices(instrument_id)` (1772) | **linear only** |
| Index prices | `self.subscribe_index_prices(instrument_id)` (1817) | **linear only** |
| Funding rates | `self.subscribe_funding_rates(instrument_id)` (1862) | **linear only** (deduped path) |

`BookType` and `BarType` must be imported (currently only `TradeTick`, `InstrumentId` on lines 23-24): add `from nautilus_trader.model.data import BarType, ...` and `from nautilus_trader.model.enums import BookType`.

**BarType construction pattern** (VERIFIED — `examples/live/bybit/bybit_data_tester.py:76`):
```python
for interval in bar_intervals_for[instrument_id]:
    bar_type = BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL")
    self.subscribe_bars(bar_type)
```
e.g. `BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL`. `from_str` fails fast inside pyo3 on an invalid interval. Must be `LAST` price + `EXTERNAL` aggregation (D-02).

**Passive handler pattern** (strategy.py lines 102-110) — analog for new `on_quote_tick`/`on_bar`/etc.:
```python
    def on_trade_tick(self, tick: TradeTick) -> None:
        # No manual persistence — auto-flows to StreamingFeatherWriter via "*" msgbus.
        logger.debug("Received %s", tick)
```
The 5 auto-written types (`QuoteTick`, `OrderBookDelta`, `Bar`, `MarkPriceUpdate`, `IndexPriceUpdate`) need NO handler logic — they auto-flow. Verified handler names if a debug handler is desired: `on_quote_tick` (actor.pyx 463), `on_order_book_deltas` (430), `on_bar` (575), `on_mark_price` (495), `on_index_price` (511).

**`_convert_stream` per-type loop** (strategy.py lines 112-132) — the analog hard-codes a single `data_cls=TradeTick`:
```python
    def _convert_stream(self, event: TimeEvent) -> None:
        try:
            catalog = ParquetDataCatalog(self.config.catalog_path)
            catalog.convert_stream_to_data(
                instance_id=self.config.instance_id_str,
                data_cls=TradeTick,        # <-- only TradeTick today
                subdirectory="live",
            )
        except Exception:
            logger.exception("Failed to convert stream data to catalog")
```
Target (per RESEARCH §"Per-type conversion loop", Pitfall 2): loop over every recorded type, **each in its own try/except** so one type's transient error does not block the others:
```python
_RECORDED_TYPES = [TradeTick, QuoteTick, OrderBookDelta, Bar, MarkPriceUpdate, IndexPriceUpdate]
for data_cls in _RECORDED_TYPES:
    try:
        catalog.convert_stream_to_data(instance_id=..., data_cls=data_cls, subdirectory="live")
    except Exception:
        logger.exception("Failed to convert %s stream to catalog", data_cls.__name__)
```

---

### Funding-rate dedup (NEW logic — no Phase 1 analog)

**Analog:** Nautilus `Actor.publish_data(DataType, data)` (actor.pyx 2925) — NOT existing recorder code. This is the one genuinely new pattern.

**Why no handler-based dedup works** (VERIFIED — RESEARCH Pitfall 1): the kernel wires the writer to `"*"` (kernel.py:604) and the data engine publishes EVERY `FundingRateUpdate` (engine.pyx:2769), so the write happens before/independent of `on_funding_rate`. Dedup MUST happen by excluding `FundingRateUpdate` from `include_types` (done in config.py above) and re-emitting only changed values.

**Dedup state** — add an instance dict in `__init__`/`on_start` keyed by `InstrumentId` (CONTEXT D-01, Claude's discretion on exact shape):
```python
def on_funding_rate(self, funding_rate: FundingRateUpdate) -> None:   # actor.pyx 527
    last = self._last_funding_rate.get(funding_rate.instrument_id)
    if last is not None and last == funding_rate.rate:
        return  # unchanged — drop
    self._last_funding_rate[funding_rate.instrument_id] = funding_rate.rate
    self.publish_data(DataType(FundingRateUpdate), funding_rate)   # captured by "*" writer
```
`FundingRateUpdate` equality is on `(instrument_id, rate, interval, next_funding_ns)` (data.pyx:6130-6138); `.rate` is the dedup key per D-01.

> **PLANNER MUST RESOLVE (Open Question 1):** `publish_data` writes under the `custom_*` table namespace, so read-back is `catalog.custom_data(FundingRateUpdate)` NOT `catalog.funding_rates(...)`. Run the small empirical spike (write 2 deduped rows → convert → read back both ways) before committing the path, mirroring Phase 1's A2/A3 discipline. Pick the path that reads back as a native `FundingRateUpdate`.

---

### `scripts/bybit_recorder/recorder.py` (bootstrap) — likely UNCHANGED

**Analog:** itself. RESEARCH confirms NO new `CUSTOM_ENCODINGS` work: the Phase 1 registrations (recorder.py lines 48-49) are for config-JSON serialization of `BybitProductType`/`BybitEnvironment` — unrelated to the new data-type Arrow serializers, all 6 of which are already registered (serializer.py). The node build (lines 66-101) already configures both `LINEAR` and `SPOT` product types (line 80). Touch this file only if the funding-dedup path needs a strategy-held catalog wired through `RecorderStrategyConfig`.

---

### `tests/unit_tests/persistence/recorder/test_recorder_conversion.py` (test, round-trip)

**Analog:** `test_convert_stream_to_data_roundtrips_trade_ticks` (same file, lines 28-56) — the GREEN round-trip pattern to clone once per new type:
```python
def test_convert_stream_to_data_roundtrips_trade_ticks(catalog_dir, sample_trade_ticks):
    catalog = ParquetDataCatalog(str(catalog_dir))
    cache = TestComponentStubs.cache()
    cache.add_instrument(TestInstrumentProvider.btcusdt_perp_binance())
    clock = TestClock()
    writer = StreamingFeatherWriter(
        path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}",
        cache=cache, clock=clock, fs_protocol="file",
        include_types=[TradeTick],
    )
    for tick in sample_trade_ticks:
        writer.write(tick)
    writer.close()
    catalog.convert_stream_to_data(instance_id=RECORDER_INSTANCE_ID, data_cls=TradeTick, subdirectory="live")
    trades = catalog.trade_ticks(instrument_ids=[str(sample_trade_ticks[0].instrument_id)])
    assert len(trades) > 0
    assert all(isinstance(t, TradeTick) for t in trades)
```
Clone per type, swapping `data_cls` and the read accessor (RESEARCH §"Catalog read-back accessors"):
- `QuoteTick` → `catalog.quote_ticks(instrument_ids=[...])`
- `OrderBookDelta` → `catalog.order_book_deltas(instrument_ids=[...])`
- `Bar` → `catalog.bars(bar_types=["...-1-MINUTE-LAST-EXTERNAL"])`
- `MarkPriceUpdate` → `catalog.query(data_cls=MarkPriceUpdate, identifiers=[...])` (NO `mark_prices()` accessor exists)
- `IndexPriceUpdate` → `catalog.query(data_cls=IndexPriceUpdate, identifiers=[...])`
- deduped funding → `catalog.custom_data(FundingRateUpdate)` or `catalog.funding_rates(...)` per Open Question 1.

---

### `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` (test, event-driven)

**Analog:** `test_on_start_subscribes_trade_ticks_per_instrument` (same file, lines 97-108) + `_build_strategy` helper (lines 30-53). Copy the `mocker.patch.object(strategy, "subscribe_*")` + `call_count` assert pattern:
```python
def test_on_start_subscribes_trade_ticks_per_instrument(mocker, mock_cache):
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(mocker, mock_cache, [instrument.id, instrument.id])
    subscribe_spy = mocker.patch.object(strategy, "subscribe_trade_ticks")
    strategy.on_start()
    assert subscribe_spy.call_count == 2
```
New tests to add (per RESEARCH test map): per-type subscribe spies, `BarType.from_str` construction, spot-depth fail-fast (mirror `test_on_start_raises_listing_all_missing_instruments` lines 56-67 `pytest.raises` pattern), linear-only gating (assert spot id does NOT trigger mark/index/funding spies), funding-dedup behavior (call `on_funding_rate` twice same rate → `publish_data` spy called once; changed rate → called again).

---

### `tests/unit_tests/persistence/recorder/conftest.py` (fixtures)

**Analog:** `sample_trade_ticks` (lines 75-91) + `mock_cache` (lines 63-72). Clone `sample_trade_ticks` per type, keeping **strictly monotonic `ts_init`** (catalog contract). Verified `TestDataStubs` factories available (`nautilus_trader/test_kit/stubs/data.py`):

| Type | Stub factory (line) |
|------|---------------------|
| `QuoteTick` | `TestDataStubs.quote_tick(...)` (70) |
| `OrderBookDelta` | `TestDataStubs.order_book_delta(...)` (396) / `order_book_deltas(...)` (503) |
| `Bar` | `TestDataStubs.bar_5decimal(ts_event, ts_init)` (199) + `bar_spec_1min_last` (135) |
| `MarkPriceUpdate` | `TestDataStubs.mark_price(...)` (251) |
| `IndexPriceUpdate` | `TestDataStubs.index_price(...)` (263) |
| `FundingRateUpdate` | **NO stub** — construct directly: `FundingRateUpdate(instrument_id, rate, ts_event, ts_init)` (data.pyx:6114) |

## Shared Patterns

### Fail-fast validation (Phase 1 D-05/D-06)
**Source:** `strategy.py:85-91` (missing-instrument raise) and `config.py:134-142` (`InstrumentId.from_str` ValueError boundary).
**Apply to:** D-03 spot-depth validation. Raise `RuntimeError`/`ValueError` naming the offending instrument + its depth; never call `self.stop()`; never silently clamp. Must run before any `subscribe_*`.
```python
if missing:
    raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")
```

### Linear-vs-spot gating (D-04)
**Source:** `config.py:128-133` structural split (`linear_raw` / `spot_raw`).
**Apply to:** mark/index/funding subscriptions (linear-only). Gate on a linear-id list derived from the existing split — never iterate the full instrument list for these three (Pitfall 4: adapter only warns + early-returns for SPOT, data.py:377/398/420).

### Auto-write via `include_types` (REC-07)
**Source:** `config.py:192` `include_types` + `strategy.py:106-110` passive `on_trade_tick`.
**Apply to:** all 5 auto-written types. Adding a type to `include_types` is the ONLY action needed for it to persist; handlers stay passive. `FundingRateUpdate` is the deliberate exception (excluded + strategy-driven).

### Per-type stream conversion (Pitfall 2)
**Source:** `strategy.py:122-132` `convert_stream_to_data(data_cls=TradeTick)`.
**Apply to:** every recorded type, looped, each wrapped in its own try/except.

### Shared streaming/catalog root (Pitfall 5 / A4)
**Source:** `recorder.py:93` + `config.py:183` — `catalog_path` for both streaming and conversion is `recorder_cfg.streaming_path`. Test fixture `catalog_dir` (conftest.py:26-34) and `StreamingFeatherWriter(path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}")` (conversion test:35-41) encode the same invariant. Unchanged in Phase 2.

## No Analog Found

| File / Logic | Role | Data Flow | Reason |
|--------------|------|-----------|--------|
| Funding-rate dedup gate (`on_funding_rate` + `_last_funding_rate` cache + `publish_data`) | strategy | event-driven filter | No Phase 1 dedup logic exists. Analog is the framework `Actor.publish_data` API (actor.pyx:2925), not recorder code. Persistence/read-back path needs an empirical spike (Open Question 1) before the plan locks it. |

## Metadata

**Analog search scope:** `scripts/bybit_recorder/`, `tests/unit_tests/persistence/recorder/`, `nautilus_trader/common/actor.pyx`, `nautilus_trader/adapters/bybit/data.py`, `nautilus_trader/model/data.pyx`, `nautilus_trader/test_kit/stubs/data.py`, `examples/live/bybit/bybit_data_tester.py`.
**Files scanned:** 11
**Pattern extraction date:** 2026-06-13
