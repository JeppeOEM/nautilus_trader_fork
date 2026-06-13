# Phase 1: Bootstrap, Config & End-to-End Slice - Pattern Map

**Mapped:** 2026-06-13
**Files analyzed:** 8 (4 source + 4 test, all new — greenfield collector package)
**Analogs found:** 8 / 8 (all map to existing Nautilus framework examples/tests, none to in-project code)

> **Greenfield note:** This is Walking Skeleton Phase 1 of a brand-new collector package. There is
> **no existing collector code** to copy from. Every "analog" below is an existing NautilusTrader
> example, framework class, or test that demonstrates the exact wiring/idiom the new file should
> mirror. The collector package location (`scripts/bybit_recorder/` per RESEARCH) is planner's
> discretion (CONTEXT D-Discretion); paths below use that proposed layout as a placeholder.

## File Classification

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `scripts/bybit_recorder/recorder.py` (entrypoint: load TOML → build `TradingNodeConfig` → run) | config / bootstrap | request-response (node lifecycle) | `examples/live/bybit/bybit_data_tester.py` | exact (node bootstrap) |
| `scripts/bybit_recorder/config.py` (dataclasses + `tomllib` loader) | config / model | transform (TOML → dataclasses → `InstrumentId`) | `nautilus_trader/persistence/config.py` (`StreamingConfig`) + `tester_data.py` (`DataTesterConfig`) | role-match (no TOML loader exists in repo — greenfield) |
| `scripts/bybit_recorder/strategy.py` (`RecorderStrategy` + `RecorderStrategyConfig`) | strategy / component | event-driven (on_trade_tick) + timer (conversion) | `nautilus_trader/examples/strategies/ema_cross.py` (`EMACross`/`EMACrossConfig`) + `tester_data.py` (`DataTester`) | exact (Strategy+Config+on_start+subscribe+timer) |
| `scripts/bybit_recorder/recorder.toml` (example config) | config | n/a | TOML block in CONTEXT.md `<specifics>` (lines 84-101) | exact (verbatim shape from CONTEXT) |
| `tests/.../test_recorder_config.py` | test (unit) | transform | `tests/unit_tests/trading/test_config.py` style + `tests/acceptance_tests/test_backtest.py:2603` (`StreamingConfig` assertion) | role-match |
| `tests/.../test_recorder_strategy.py` | test (unit, mock cache) | event-driven | `tests/unit_tests/trading/test_strategy.py` (Strategy unit harness) | role-match |
| `tests/.../test_recorder_conversion.py` | test (integration) | file-I/O | `tests/unit_tests/persistence/test_streaming.py` + `test_backtest.py:2715` (`convert_stream_to_data`) | role-match |
| `tests/.../conftest.py` | test fixtures | n/a | `tests/unit_tests/persistence/conftest.py` (`tmp_path` catalog fixtures) | exact |

## Pattern Assignments

### `scripts/bybit_recorder/recorder.py` (bootstrap, request-response)

**Analog:** `examples/live/bybit/bybit_data_tester.py` (verified bootstrap template; the options collector's persistence code is explicitly NOT copied — RESEARCH anti-patterns).

**Imports pattern** (`bybit_data_tester.py:17-31`):
```python
from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
```
> One import per line, alphabetized (ruff `isort`/`I`), absolute package paths. Add `from nautilus_trader.core.uuid import UUID4`, `from nautilus_trader.persistence.config import StreamingConfig`, `from nautilus_trader.persistence.writer import RotationMode`, and `from nautilus_trader.model.data import TradeTick` for this phase.

**Node bootstrap pattern** (`bybit_data_tester.py:48-98`) — copy the `TradingNodeConfig` → `TradingNode` → `add_strategy` → `add_data_client_factory` → `build()` → `run()` sequence:
```python
config_node = TradingNodeConfig(
    trader_id=TraderId(recorder_cfg.trader_id),
    instance_id=UUID4.from_str(RECORDER_INSTANCE_ID),   # NEW vs analog — see Shared Pattern: Fixed instance_id
    logging=LoggingConfig(log_level="INFO", use_pyo3=True),
    streaming=streaming,                                 # NEW vs analog — see Shared Pattern: Streaming
    data_clients={
        BYBIT: BybitDataClientConfig(
            environment=BybitEnvironment.MAINNET,                                   # D-10
            product_types=(BybitProductType.LINEAR, BybitProductType.SPOT),         # D-07
            instrument_provider=InstrumentProviderConfig(load_ids=frozenset(ids)),  # or load_all=True
        ),
    },
    timeout_connection=20.0,
    timeout_disconnection=10.0,
    timeout_post_stop=1.0,
)
node = TradingNode(config=config_node)
node.trader.add_strategy(RecorderStrategy(config=strategy_config))   # analog uses add_actor(DataTester); use add_strategy for a Strategy
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.build()
```

**Run/dispose pattern** (`bybit_data_tester.py:102-106`):
```python
if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
```
> Credentials: NOT passed here (D-09). `BybitDataClientConfig` sources `BYBIT_API_KEY`/`BYBIT_API_SECRET`/`BYBIT_TESTNET_*` from env internally; public trade-tick subscription on mainnet needs none (D-10).

---

### `scripts/bybit_recorder/config.py` (config/model, transform)

**Analog:** `nautilus_trader/persistence/config.py` (`StreamingConfig`, lines 28-73) for the frozen-config idiom; `tester_data.py:43-81` (`DataTesterConfig`) for typed optional fields with defaults. **No existing `tomllib` loader exists anywhere in `nautilus_trader/` or `examples/`** — the loader itself is greenfield (RESEARCH Pattern 5).

**Frozen-config field pattern** (`persistence/config.py:62-73`) — for the `[recorder]` dataclass; note `pd.Timedelta`/`time` are accepted field types, and defaults are inline:
```python
class StreamingConfig(NautilusConfig, frozen=True):
    catalog_path: str
    fs_protocol: str | None = None
    rotation_mode: RotationMode = RotationMode.NO_ROTATION
    rotation_interval: pd.Timedelta | None = None
    rotation_time: time = time(0, 0, 0, 0)
    rotation_timezone: str = "UTC"
```
> Match the type-hint style: `T | None = ...` unions (PEP 604), every field typed (mypy `disallow_incomplete_defs`). Use `PositiveInt` (from `nautilus_trader.common.config`, seen `tester_data.py:23`) for `conversion_interval_minutes`/`depth` to get >0 validation for free (Security V5).

**TOML loader pattern** (greenfield — RESEARCH Pattern 5, `01-RESEARCH.md:254-266`):
```python
import tomllib
from nautilus_trader.model.identifiers import InstrumentId

with open(path, "rb") as f:                 # tomllib REQUIRES binary mode
    raw = tomllib.load(f)

recorder = raw["recorder"]
linear = raw.get("instruments", {}).get("linear", [])   # list[dict] — [[instruments.linear]]
spot   = raw.get("instruments", {}).get("spot", [])      # list[dict] — [[instruments.spot]]
ids = [InstrumentId.from_str(e["id"]) for e in (*linear, *spot)]   # raises on malformed id (V5)
```
> `[[instruments.linear]]` array-of-tables parses to `raw["instruments"]["linear"]` = `list[dict]` (D-07). Parse `depth`/`bar_intervals` into the dataclass now (CONF-02) but do not act on them (Phase 2 consumes them). `InstrumentId.from_str` is the input-validation boundary (V5 / fail-fast on bad id).

**Error handling:** Loader raises on malformed TOML (`tomllib.TOMLDecodeError`), missing `[recorder]` key (`KeyError`), or bad instrument id (`InstrumentId.from_str` ValueError). Per CLAUDE.md Python convention — propagate naturally / raise typed exceptions; log at appropriate level before raising.

---

### `scripts/bybit_recorder/strategy.py` (strategy/component, event-driven + timer)

**Analog:** `nautilus_trader/examples/strategies/ema_cross.py` (`EMACross`/`EMACrossConfig`, lines 46-158) for `StrategyConfig`+`on_start`+`cache.instrument()`+`subscribe_trade_ticks`; `tester_data.py:160-172` for the per-instrument subscribe loop; `strategy.pyx:1802-1810` for real `set_timer` call shape.

**Config pattern** (`ema_cross.py:46-93`) — frozen `StrategyConfig` subclass with typed fields:
```python
from nautilus_trader.config import StrategyConfig

class RecorderStrategyConfig(StrategyConfig, frozen=True):
    instrument_ids: list[InstrumentId]          # cf. DataTesterConfig.instrument_ids (tester_data.py:48)
    catalog_path: str
    instance_id_str: str                        # fixed UUID4 str (D-03) — same value as node instance_id
    conversion_interval_minutes: PositiveInt = 60
```

**Fail-fast validation pattern (D-05/D-06)** — adapt `ema_cross.py:132-136` (single-instrument `cache.instrument()` check) BUT change the failure mode from `log.error + self.stop()` to **collect-all-then-raise** (the options collector at `bybit_options_data_collector.py:276-280` uses `stop()` — do NOT copy that; D-05 mandates raising):
```python
def on_start(self) -> None:
    missing: list[str] = []
    for instrument_id in self.config.instrument_ids:
        if self.cache.instrument(instrument_id) is None:   # cache.instrument() pattern: ema_cross.py:132
            missing.append(str(instrument_id))
    if missing:
        raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")   # D-06: all IDs, one message

    for instrument_id in self.config.instrument_ids:       # subscribe loop: tester_data.py:167-172
        self.subscribe_trade_ticks(instrument_id)          # REC-01

    self.clock.set_timer(                                  # D-01 / REL-01
        name="convert-stream",
        interval=pd.Timedelta(minutes=self.config.conversion_interval_minutes),
        callback=self._convert_stream,
    )
```
> **Deviation from analog (important):** `ema_cross.py:133-136` and `bybit_options_data_collector.py:277-280` both do `log.error(...); self.stop(); return` on a missing instrument. D-05 explicitly forbids the silent `stop()` — the new strategy must `raise` so the process exits non-zero (systemd surfaces it). [Verify exit-code propagation under `TradingNode.run()` — RESEARCH A3.]

**`set_timer` call shape** (real usage `strategy.pyx:1802-1810`): name, `pd.Timedelta` interval, then the callback `Callable[[TimeEvent], None]`. The `component.pyx:419-455` signature documents the kwargs.

**Timer callback / conversion pattern (D-01/D-04/REL-01)** — `convert_stream_to_data` signature at `parquet.py:2523-2531`; real call at `test_backtest.py:2715-2718`:
```python
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

def _convert_stream(self, event: TimeEvent) -> None:
    catalog = ParquetDataCatalog(self.config.catalog_path)
    catalog.convert_stream_to_data(
        instance_id=self.config.instance_id_str,   # str form of the fixed UUID4 (D-03)
        data_cls=TradeTick,
        subdirectory="live",                        # MUST override — default is "backtest" (parquet.py:2528) → Pitfall 3
    )
```
> **Idempotency / disjoint-interval behavior** (`parquet.py:2602-2612`): identical `{start}_{end}.parquet` → prints "already exists, skipping write" and returns; **non-disjoint** intervals → `raise ValueError`. This is why daily feather rotation matters and why mid-day re-conversion of the open feather may raise (RESEARCH Pitfall 2 / A2) — plan an empirical verification task.

**Data handler:** `on_trade_tick(self, tick: TradeTick) -> None` (handler signature per `actor.pyx:479`). For Phase 1 it can be a no-op / debug log — trades auto-flow to the `StreamingFeatherWriter` via the kernel's `"*"` msgbus subscription (REC-07), so no manual write in the handler.

---

### `scripts/bybit_recorder/recorder.toml` (config example)

**Analog:** the verbatim TOML block confirmed in `01-CONTEXT.md:84-101`. Copy that shape exactly:
```toml
[recorder]
trader_id = "BYBIT-COLLECTOR-001"
catalog_path = "catalog"
streaming_path = "catalog/streaming"
conversion_interval_minutes = 60
environment = "mainnet"

[[instruments.linear]]
id = "BTCUSDT-LINEAR.BYBIT"
depth = 50
bar_intervals = ["1-MINUTE"]

[[instruments.spot]]
id = "ETHUSDT-SPOT.BYBIT"
depth = 50
bar_intervals = ["1-MINUTE"]
```
> No `api_key`/`api_secret` keys (D-09). `catalog_path` vs `streaming_path` must be reconciled so conversion finds the feather files (RESEARCH Pitfall 5 / A4) — planner defines the relationship.

---

### `tests/.../test_recorder_config.py` (unit, transform)

**Analog:** `tests/unit_tests/trading/test_config.py` (frozen-config construction/assertion style) + `StreamingConfig` instantiation at `test_backtest.py:2603-2607`.

**What to assert (CONF-01/02, REC-07):** TOML string → loader → expected `list[InstrumentId]` (`InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")` convention verified `bybit_data_tester.py:37-40`); `depth`/`bar_intervals` parsed onto the dataclass; `StreamingConfig` built with `rotation_mode=RotationMode.SCHEDULED_DATES` (REC-07 / Pitfall 1). Use `tmp_path` to write a sample `.toml` then load it (binary mode).

**Test conventions** (CLAUDE.md): `test_*.py`, AAA pattern, `test_{function}_{scenario}_{expected}` names, one logical assertion per test, copyright header (lines 1-14 of any repo `.py`).

---

### `tests/.../test_recorder_strategy.py` (unit, mock cache, event-driven)

**Analog:** `tests/unit_tests/trading/test_strategy.py` (Strategy unit harness — clock/cache/msgbus wiring for a Strategy under test).

**What to assert (CONF-03/REC-01):**
- Missing instrument → `on_start` raises `RuntimeError` whose message contains ALL missing IDs (D-06). Use `pytest.raises(RuntimeError, match=...)`. Mock the cache so `cache.instrument(id)` returns `None` for configured ids (`pytest-mock` per CLAUDE.md).
- All present → `subscribe_trade_ticks` called once per configured instrument (assert via mock/spy on the subscribe call).

---

### `tests/.../test_recorder_conversion.py` (integration, file-I/O)

**Analog:** `tests/unit_tests/persistence/test_streaming.py` (StreamingFeatherWriter + catalog round-trip imports at lines 22-56) + the `convert_stream_to_data` → reload flow at `test_backtest.py:2715-2727`.

**What to assert (REL-01 + success criterion 5):** write synthetic `TradeTick`s to a feather stream under a temp catalog at `{path}/live/{instance_id}/`, call `ParquetDataCatalog(path).convert_stream_to_data(instance_id, TradeTick, subdirectory="live")`, then reload via `catalog.trade_ticks(instrument_ids=[...])` and assert `all(isinstance(t, TradeTick) for t in trades)` (reload pattern `base.py:176-181`, RESEARCH lines 380-385). Also add a task to exercise twice-in-one-day re-conversion to observe the disjoint-interval behavior (Pitfall 2 / A2).

---

### `tests/.../conftest.py` (fixtures)

**Analog:** `tests/unit_tests/persistence/conftest.py:26-37` — `tmp_path`-based catalog fixtures via `setup_catalog`:
```python
@pytest.fixture(name="catalog")
def fixture_catalog(tmp_path) -> ParquetDataCatalog:
    return setup_catalog(protocol="file", path=tmp_path / "catalog_file")
```
> Provide fixtures: temp catalog dir (`tmp_path`), mock cache, sample TOML text, sample `TradeTick`s (use `nautilus_trader.test_kit.stubs.data.TestDataStubs` / `TestInstrumentProvider`, imported in `test_streaming.py:50-54`). `@pytest.fixture` per CLAUDE.md testing conventions.

## Shared Patterns

### Streaming enablement (REC-07) — applies to recorder.py
**Source:** `StreamingConfig` (`persistence/config.py:62-73`); kernel auto-wires the writer to `"*"` (`kernel.py:587-605`, per RESEARCH). Daily rotation required for day-partitioning (Pitfall 1).
```python
from datetime import time as dt_time
import pandas as pd
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode
from nautilus_trader.model.data import TradeTick

streaming = StreamingConfig(
    catalog_path=recorder_cfg.streaming_path,           # reconcile with catalog_path — Pitfall 5
    fs_protocol="file",
    rotation_mode=RotationMode.SCHEDULED_DATES,          # daily calendar partitions (Pitfall 1)
    rotation_interval=pd.Timedelta(days=1),
    rotation_time=dt_time(0, 0, 0),
    rotation_timezone="UTC",
    include_types=[TradeTick],                           # Phase 1 records only trades
)
```
> Feather lands at `{catalog_path}/live/{instance_id}/` (kernel derives `live` for a `TradingNode`). No custom writer (REC-07 + project memory "prefer Nautilus built-ins").

### Fixed instance_id (D-03) — applies to recorder.py + strategy.py
**Source:** `system/config.py` (`instance_id: UUID4 | None`); `core/uuid.pyx:75-103` (`UUID4.from_str` requires valid v4 RFC-4122).
```python
from nautilus_trader.core.uuid import UUID4
RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"   # generated ONCE, hardcoded — NOT derived from trader_id
# node:    instance_id=UUID4.from_str(RECORDER_INSTANCE_ID)
# convert: instance_id=RECORDER_INSTANCE_ID  (str form)
```
> **Surface to user/planner:** D-03's "derived from trader_id/instance name" is NOT literally achievable — `UUID4.from_str` rejects "BYBIT-COLLECTOR-001" (Pitfall 4). Must be a fixed valid UUID4 constant shared by node + strategy.

### Error handling / logging — applies to all source files
**Source:** CLAUDE.md Python conventions. Module logger `logger = logging.getLogger(__name__)`; standard levels; log before raising. Fail-fast validation (`on_start` raise, TOML `InstrumentId.from_str`) is the primary error boundary. Never log credentials (D-09 / Security V7).

### Bybit instrument-id convention — applies to config.py + tests
**Source:** `bybit_data_tester.py:37-40`.
```python
symbol = f"BTCUSDT-{BybitProductType.LINEAR.value.upper()}"     # "BTCUSDT-LINEAR"
instrument_id = InstrumentId.from_str(f"{symbol}.{BYBIT}")      # "BTCUSDT-LINEAR.BYBIT"; spot → "ETHUSDT-SPOT.BYBIT"
```

## No Analog Found

| File | Role | Reason | Planner Guidance |
|------|------|--------|------------------|
| `config.py` TOML loader function | config/transform | No `import tomllib`/`toml` anywhere in `nautilus_trader/` or `examples/` — runtime TOML config is greenfield for this repo | Use RESEARCH Pattern 5 (`01-RESEARCH.md:251-266`); frozen-config field idiom borrows from `StreamingConfig` but the loader body is net-new |

> All other files have strong framework/example/test analogs (table above). The only genuinely novel logic is the TOML schema+loader and the `RecorderStrategy` glue (validation loop + timer callback).

## Metadata

**Analog search scope:** `examples/live/bybit/`, `nautilus_trader/persistence/{config.py,catalog/parquet.py}`, `nautilus_trader/examples/strategies/`, `nautilus_trader/test_kit/strategies/`, `nautilus_trader/trading/strategy.pyx`, `tests/unit_tests/{persistence,trading}/`, `tests/acceptance_tests/test_backtest.py`.
**Files scanned (read):** 8 source/test files + 2 grep sweeps for `tomllib`/`set_timer`/`StreamingConfig`.
**Pattern extraction date:** 2026-06-13
**Cross-check:** RESEARCH.md line citations verified against live source (`config.py:62-73`, `parquet.py:2523-2612`, `bybit_data_tester.py:37-98`, `ema_cross.py:46-158`, `strategy.pyx:1802-1810` all confirmed accurate).
