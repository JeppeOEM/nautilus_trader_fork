# Phase 6: Hot-Reload Config Changes - Pattern Map

**Mapped:** 2026-06-15
**Files analyzed:** 5 (2 modified, 1 modified-wiring, 2 test files: 1 new + 1 extended)
**Analogs found:** 5 / 5 (all analogs are in-file — the phase extends three existing recorder modules)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `scripts/bybit_recorder/strategy.py` (modify: `_on_config_reload` timer cb + diff/add/remove/swap helpers + bookkeeping + `set_data_client` setter) | strategy / event-driven | event-driven (timer) + request-response (provider load) | `strategy.py::_heartbeat` + `on_start` (same file) | exact |
| `scripts/bybit_recorder/config.py` (modify: `max_hot_added_instruments` knob + `> 0` validation) | config | transform (TOML parse/validate) | `config.py::load_recorder_config` threshold knobs (same file) | exact |
| `scripts/bybit_recorder/recorder.py` (modify: inject Bybit data-client reference into strategy after `node.build()`) | wiring / config | request-response (composition wiring) | `recorder.py::main` node-build block (same file) | exact |
| `tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py` (NEW) | test | event-driven | `test_recorder_strategy.py` (`_build_strategy`, mock-subscribe spies, `timer_names`) | exact |
| `tests/unit_tests/persistence/recorder/test_recorder_config.py` (extend: `max_hot_added_instruments` validation) | test | transform | existing threshold-validation tests in same file | exact |

> Note: every primitive this phase needs already exists in the codebase; there are NO net-new file roles. Analogs are existing methods in the same three recorder modules. Planner should EXTEND these files, mirroring the patterns below verbatim.

## Pattern Assignments

### `scripts/bybit_recorder/strategy.py` (strategy, event-driven + request-response)

**Analog:** `scripts/bybit_recorder/strategy.py` itself (`on_start`, `_heartbeat`, `_convert_stream`, the `_last_seen` machinery) + the Bybit adapter for the runtime instrument load.

**Imports pattern** (lines 16-37) — the module already imports everything the diff/subscribe logic needs (`pd`, `TimeEvent`, `BarType`, `BookType`, `InstrumentId`, `Strategy`). Hot-reload additions needed: the Bybit data-client TYPE for the injected reference (import lazily / under `TYPE_CHECKING` to avoid pulling adapter into the strategy module at import time), and `ClientId`/`BYBIT` only in `recorder.py` (not here).

**Reload timer registration — mirror the existing timer pattern** (lines 200-210, register in `on_start` alongside the other two; reuse heartbeat cadence per D-02):
```python
self.clock.set_timer(
    name="convert-stream",
    interval=pd.Timedelta(minutes=self.config.conversion_interval_minutes),
    callback=self._convert_stream,
)
self.clock.set_timer(
    name="heartbeat",
    interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),
    callback=self._heartbeat,
)
# NEW (Pattern 1): dedicated named timer "config-reload", same cadence as heartbeat (D-02)
# self.clock.set_timer(name="config-reload",
#     interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),
#     callback=self._on_config_reload)
```

**Timer-callback signature + error-swallow pattern — mirror `_heartbeat` / `_convert_stream`** (lines 289-313, 498-503). The reload callback MUST take `event: TimeEvent` and wrap the whole body in try/except so a parse/validation error logs and the recorder keeps running on the last-good config (Security V7 / `_run_conversion` swallow precedent at lines 463-471):
```python
def _heartbeat(self, event: TimeEvent) -> None:
    now_ns = self.clock.timestamp_ns()
    for (stream, instrument_id), last_ns in self._last_seen.items():
        idle_s = (now_ns - last_ns) / 1e9
        threshold_s = self._stale_threshold_s(stream)
        if idle_s > threshold_s:
            logger.warning("Stale stream: %s %s idle %.1fs (> %.0fs threshold)",
                           stream, instrument_id, idle_s, threshold_s)
    logger.info("Heartbeat: %d active streams", len(self._last_seen))
```

**Per-instrument subscribe loop — REUSE verbatim for the ADD branch** (lines 177-198). The hot-add `_subscribe_instrument` helper must issue these EXACT calls, including the `BarType.from_str(f"{id}-{interval}-LAST-EXTERNAL")` form and `BookType.L2_MBP`, with the linear-only gating (D-04 / Pitfall 5):
```python
for instrument_id in self.config.instrument_ids:
    self.subscribe_trade_ticks(instrument_id)
    self.subscribe_quote_ticks(instrument_id)
    self.subscribe_order_book_deltas(
        instrument_id, book_type=BookType.L2_MBP,
        depth=self.config.instrument_depths[instrument_id],
    )
    for interval in self.config.instrument_bar_intervals[instrument_id]:
        self.subscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
# D-04 linear-only gating — iterate linear_instrument_ids, never the full list (Pitfall 5)
for instrument_id in self.config.linear_instrument_ids:
    self.subscribe_mark_prices(instrument_id)
    self.subscribe_index_prices(instrument_id)
    self.subscribe_funding_rates(instrument_id)
```

**Cache-presence guard before subscribing — mirror `on_start`'s missing check, but NON-FATAL** (lines 165-171). `on_start` raises; the hot-add path logs+skips+marks-failed (D-07/D-10/D-11) instead:
```python
missing: list[str] = []
for instrument_id in self.config.instrument_ids:
    if self.cache.instrument(instrument_id) is None:
        missing.append(str(instrument_id))
if missing:
    raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")
```

**`_last_seen` machinery — hot-add joins it, removal prunes it** (init at line 144; key shape `(stream, instrument_id)` used in every `on_*` handler, e.g. line 323). D-04: hot-added instruments auto-join via the normal `on_*` handlers (no special code). D-06 + Pitfall 4: on removal, explicitly prune every `(stream, id)` entry:
```python
# __init__ (line 144)
self._last_seen: dict[tuple[str, InstrumentId], int] = {}
# on_trade_tick (line 323) — auto-populates _last_seen for any subscribed instrument
self._last_seen[("trade", tick.instrument_id)] = self.clock.timestamp_ns()
# REMOVAL prune (D-06) — NEW, mirror the labels used across all on_* handlers:
for stream in ("trade", "quote", "deltas", "bar", "mark", "index", "funding"):
    self._last_seen.pop((stream, instrument_id), None)
```

**New bookkeeping fields — add in `__init__` parallel to `_last_seen`** (init block lines 133-150 is the insertion site; Pattern 2 from research):
```python
self._subscribed_params: dict[InstrumentId, tuple[int, frozenset[str]]] = {}  # depth, bar_intervals
self._failed_instrument_ids: set[InstrumentId] = set()   # cleared when toml signature changes (D-07)
self._last_config_signature: int | None = None
self._hot_added_count: int = 0
self._bybit_client = None  # set via set_data_client() (Pattern 3 wiring)
```

**Runtime instrument-load (ADD branch only) — Pattern 3, the D-09 FALLBACK (request_instrument is non-functional for Bybit, Pitfall 1):**
```python
provider = self._bybit_client.instrument_provider     # property, data.py:184
await provider.load_async(instrument_id)              # providers.py:194 Bybit override; does NOT raise on unknown id
instrument = provider.find(instrument_id)             # providers.py:376 — None if Bybit didn't recognize it (D-11)
if instrument is None:
    ...  # D-11 -> same as D-07: log ERROR, skip, mark failed
else:
    self.cache.add_instrument(instrument)             # satisfies the D-10 cache-confirm guard
    self._bybit_client._cache_instruments()           # data.py:239 — repopulate WS/HTTP precision caches (A1)
    # then issue the on_start-style subscribe block
```
PLANNER MUST PIN at plan time (research Open Q2 / A3): the sync-timer→async-load invocation (two-phase: schedule load on poll N via `provider.load(id)` sync wrapper or `data_client.create_task`; subscribe on poll N+1 once `provider.find(id)`/cache is non-None). This seam is the only non-unit-testable part — gate behind a `checkpoint:human-verify` live smoke.

**Restart-gap method is OUT of scope for hot-adds** (lines 212-260, `_log_restart_gaps`). D-04: do NOT call this for hot-added instruments (no restart, no prior catalog gap). No change needed — just don't invoke it from the reload path.

---

### `scripts/bybit_recorder/config.py` (config, transform)

**Analog:** `config.py::load_recorder_config` existing `> 0` threshold validation (lines 256-286) + the `RecorderConfig`/`RecorderStrategyConfig` `PositiveInt` field pattern.

**`PositiveInt` field on the config model — mirror sibling threshold fields** (`RecorderConfig` lines 125-131; `RecorderStrategyConfig` lines 112-117). Add `max_hot_added_instruments: PositiveInt = 50` (default from research Open Q1 / A4) to BOTH `RecorderConfig` and `RecorderStrategyConfig`:
```python
conversion_interval_minutes: PositiveInt = 60
rotation_interval_minutes: PositiveInt = 1440
restart_gap_threshold_seconds: PositiveInt = 60
heartbeat_interval_seconds: PositiveInt = 30
# NEW: max_hot_added_instruments: PositiveInt = 50  (D-08, WARNING-only)
```

**Fail-fast `> 0` validation in `load_recorder_config` — mirror exactly** (lines 256-286). Read with `recorder_raw.get(..., 50)`, then the `<= 0` raise:
```python
restart_gap_threshold_seconds = recorder_raw.get("restart_gap_threshold_seconds", 60)
if restart_gap_threshold_seconds <= 0:
    raise ValueError(
        f"Invalid restart_gap_threshold_seconds {restart_gap_threshold_seconds}: must be positive",
    )
# NEW (D-08): same shape for max_hot_added_instruments (default 50)
```
Then thread it into the `RecorderConfig(...)` constructor (lines 288-300).

**Re-callability (D-01/D-02):** `load_recorder_config(path)` is ALREADY safely re-callable each poll (pure parse + validate, no side effects beyond the INFO log at lines 302-307). Reuse VERBATIM as the reload re-read — no changes for that purpose. Note the count-only log at line 303 (never logs credentials — Security V7) is the precedent for reload-time logging.

---

### `scripts/bybit_recorder/recorder.py` (wiring, request-response composition)

**Analog:** `recorder.py::main` node-build + strategy-add block (lines 113-116) and the existing `BYBIT`/`ClientId`-style adapter imports (lines 19-23).

**Inject the Bybit data-client reference AFTER `node.build()`** (Pattern 3 wiring; insertion site is right after line 116):
```python
node = TradingNode(config=config_node)
node.trader.add_strategy(RecorderStrategy(config=strategy_cfg))   # keep a ref to the strategy instance
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.build()
# NEW: hand the strategy a reference to the live Bybit data client so it can call
# provider.load_async() at runtime (request_instrument() is non-functional for Bybit, Pitfall 1).
# data_client = node.kernel.data_engine.<accessor>(ClientId(BYBIT))  -- see ACCESSOR NOTE below
# strategy.set_data_client(data_client)
```
Thread the new `max_hot_added_instruments` from `recorder_cfg` into `RecorderStrategyConfig(...)` (lines 89-111), exactly like the other thresholds at lines 103-110.

**ACCESSOR NOTE (research A2 — pin at plan time):** there is NO public `get_client` on `DataEngine`; the registry is the private `self._clients: dict[ClientId, DataClient]` (`engine.pyx:208`). The accessor will be `node.kernel.data_engine` (public property `kernel.py:882`) → reach the client via the registry (e.g. `._clients[ClientId(BYBIT)]`). Confirm the exact public/least-private path at plan time; this stays entirely in `scripts/bybit_recorder/` (no core edit). `BYBIT` is already imported (line 19); `ClientId` is not yet imported here.

---

### `tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py` (NEW test, event-driven)

**Analog:** `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` — the `_build_strategy` helper (lines 36-77) and the mock-subscribe-spy pattern.

**Strategy build harness — reuse `_build_strategy`** (lines 36-77): real `TestClock` + `MessageBus` + `Portfolio` + real `Cache`, `strategy.register(...)`. The research Wave-0 note says EXTEND `_build_strategy` to also inject a mock data-client reference (`strategy.set_data_client(mock_client)`) for Pattern-3 tests.
```python
clock = TestClock()
msgbus = MessageBus(trader_id=trader_id, clock=clock)
portfolio = Portfolio(msgbus=msgbus, cache=mock_cache, clock=clock)
strategy = RecorderStrategy(config=config)
strategy.register(trader_id=trader_id, portfolio=portfolio, msgbus=msgbus, cache=mock_cache, clock=clock)
```

**Timer-registered assertion — mirror `test_on_start_sets_conversion_timer`** (lines 135-146):
```python
strategy.on_start()
assert "convert-stream" in clock.timer_names
# NEW: assert "config-reload" in clock.timer_names
```

**Subscribe/unsubscribe spy pattern — mirror `mocker.patch.object`** (lines 121-134, 164-188): patch each `subscribe_*`/`unsubscribe_*` and assert call args/order. For the depth clean-swap test (Pitfall 2), assert `unsubscribe_order_book_deltas` is called BEFORE `subscribe_order_book_deltas` (load-bearing order).

**"Present vs missing" instrument simulation — register via `cache.add_instrument`** (conftest `mock_cache` docstring, lines 72-81): Cython `cdef` methods can't be monkeypatched, so an instrument IS present iff `cache.add_instrument(...)` was called; leave others out to simulate Bybit-unknown ids (D-11). For Pattern-3 load tests, the injected mock client's `provider.find()` returns an instrument (or `None`) to drive the success/skip branches.

Tests to cover (from Phase Requirements → Test Map, HOT-01): `timer_registered`, `detects_addition`, `addition_subscribes` (after cache-confirm), `removal_unsubscribes` (+ `_last_seen` prune), `depth_swap_order`, `bar_interval_delta`, `failed_not_retried`, `failed_resets_on_change`, `threshold_warning`, `linear_gating`.

---

### `tests/unit_tests/persistence/recorder/test_recorder_config.py` (extend, transform)

**Analog:** the existing threshold-validation tests in the same file (the `restart_gap_threshold_seconds` / `heartbeat_interval_seconds` `<= 0` raise tests).

Add a `max_hot_added` validation test mirroring the existing `> 0` threshold tests: write a TOML with `max_hot_added_instruments = 0` (or negative) and assert `load_recorder_config` raises `ValueError`; assert the default (`50`) applies when the key is omitted. Reuse the `sample_toml` fixture (conftest lines 46-69) as the base.

---

## Shared Patterns

### Timer registration + callback signature
**Source:** `scripts/bybit_recorder/strategy.py` lines 200-210 (`set_timer`) and 289 / 498 (`def _x(self, event: TimeEvent)`)
**Apply to:** the new `config-reload` timer + `_on_config_reload` callback
```python
self.clock.set_timer(name=..., interval=pd.Timedelta(seconds=...), callback=self._cb)
def _cb(self, event: TimeEvent) -> None: ...
```

### Error-swallow wrapper (component must survive a bad reload)
**Source:** `scripts/bybit_recorder/strategy.py` lines 463-471 / 487-496 (`_run_conversion` per-step try/except + `logger.exception`)
**Apply to:** `_on_config_reload` body (parse + per-instrument load/subscribe). A parse/validation/load error logs via `logger.exception(...)` and is swallowed so the recorder keeps running on the last-good config (Security V7, TOCTOU mitigation).
```python
try:
    ...
except Exception:
    logger.exception("Failed to ...")
    return
```

### Fail-fast `> 0` config validation
**Source:** `scripts/bybit_recorder/config.py` lines 282-286
**Apply to:** the new `max_hot_added_instruments` knob (D-08)
```python
if value <= 0:
    raise ValueError(f"Invalid {name} {value}: must be positive")
```

### Count-only / id-only logging (never credentials)
**Source:** `scripts/bybit_recorder/config.py` lines 302-307
**Apply to:** all reload-path logging — log instrument COUNT and ids only (Security V7).

### Linear-only gating for mark/index/funding
**Source:** `scripts/bybit_recorder/strategy.py` lines 195-198 (iterate `linear_instrument_ids`, never the full list)
**Apply to:** every hot-add AND hot-remove of mark/index/funding feeds (D-04 / Pitfall 5). Determine product type from the parsed `InstrumentEntry.product_type`.

### Test harness (`_build_strategy` + mock-subscribe spies + real Cache)
**Source:** `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` lines 36-77, 121-146; conftest `mock_cache` lines 72-81
**Apply to:** both new/extended test files (extend `_build_strategy` to inject a mock data-client for Pattern-3 tests).

## No Analog Found

None — every file is an extension of an existing recorder module, and every primitive (timer, subscribe/unsubscribe, `_last_seen`, config validation, provider load, test harness) already exists in the codebase. The only genuinely new LOGIC (not new patterns) is the diff computation, the bookkeeping fields, and the small Pattern-3 composition wiring.

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | — | — | — |

## Metadata

**Analog search scope:** `scripts/bybit_recorder/` (strategy.py, config.py, recorder.py), `tests/unit_tests/persistence/recorder/` (conftest.py, test_recorder_strategy.py), and verification reads of `nautilus_trader/adapters/bybit/{data.py,providers.py}` + `nautilus_trader/data/engine.pyx` + `nautilus_trader/system/kernel.py` for the Pattern-3 wiring accessor.
**Files scanned:** 8
**Pattern extraction date:** 2026-06-15
