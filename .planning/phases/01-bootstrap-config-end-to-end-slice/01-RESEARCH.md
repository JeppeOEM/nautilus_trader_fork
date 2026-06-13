# Phase 1: Bootstrap, Config & End-to-End Slice - Research

**Researched:** 2026-06-13
**Domain:** NautilusTrader live data collection — `TradingNode` + Bybit adapter + `StreamingConfig`/`StreamingFeatherWriter` + `ParquetDataCatalog.convert_stream_to_data()`
**Confidence:** HIGH (all critical APIs read directly from source in this repo)

## Summary

Phase 1 is a Walking Skeleton that wires an existing NautilusTrader `TradingNode` to Bybit (mainnet), validates a TOML-configured instrument list against the loaded instrument cache, records trade ticks for at least one instrument through Nautilus's native `StreamingFeatherWriter` (enabled by setting `TradingNodeConfig.streaming = StreamingConfig(...)`), and runs an in-process clock timer that periodically calls `ParquetDataCatalog.convert_stream_to_data(...)` to turn the streamed feather files into the official day-partitioned parquet catalog.

The single most important architectural finding: **"day partitioning" is not a directory layout — it is a consequence of feather file rotation plus the catalog's timestamp-interval filenames.** The `StreamingFeatherWriter` rotates feather files (default `RotationMode.NO_ROTATION`; for daily partitions use `RotationMode.SCHEDULED_DATES` with a 1-day interval at 00:00 UTC). `convert_stream_to_data` reads each feather file and writes one parquet file named `{start_ts}_{end_ts}.parquet` under `{catalog}/data/{data_type}/{identifier}/`. So to get one-parquet-file-per-UTC-day, the **feather writer must rotate daily** — otherwise the whole run lands in a single growing parquet file keyed by its full min/max interval. This is the key thing the planner must get right and is currently NOT reflected in the CONTEXT decisions.

The second important finding: the streaming feather path is `{StreamingConfig.catalog_path}/live/{instance_id}/...` where `instance_id` is the kernel's `UUID4`. For conversion the strategy must call `ParquetDataCatalog(path=catalog_path).convert_stream_to_data(instance_id=<same UUID4 string>, data_cls=TradeTick, subdirectory="live")`. Because `UUID4.from_str()` requires a valid RFC-4122 v4 string, D-03's "fixed instance_id" must be a hardcoded valid UUID4 constant passed to both `TradingNodeConfig(instance_id=...)` and the strategy config — it cannot be derived from `trader_id`.

**Primary recommendation:** Reuse `examples/live/bybit/bybit_data_tester.py` as the node-bootstrap template (not the options collector's persistence code). Set `TradingNodeConfig.streaming` with `RotationMode.SCHEDULED_DATES`/daily rotation, pass a fixed `instance_id` UUID4 constant, validate instruments in `on_start` via `self.cache.instrument(...)` collecting all misses then raising, subscribe trades, and drive conversion from a `self.clock.set_timer(...)` callback that constructs `ParquetDataCatalog(catalog_path)` and calls `convert_stream_to_data(instance_id_str, TradeTick, subdirectory="live")`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
**Conversion Scheduling**
- **D-01:** The feather→`ParquetDataCatalog` conversion runs in-process, triggered by a Nautilus clock timer (`clock.set_timer`) inside the Strategy — no separate cron/systemd-timer process for Phase 1 (single-process constraint).
- **D-02:** The conversion interval is configurable via a `conversion_interval_minutes` field in the `[recorder]` config section (not hardcoded).
- **D-03:** The recorder uses a fixed `instance_id` (e.g. derived from a constant `trader_id`/instance name, not timestamped per run) so the streaming feather directory is stable across restarts — required for Phase 3's restart-without-data-loss goal.
- **D-04:** Each scheduled run calls `catalog.convert_stream_to_data()` over all available feather data (including the current/partial UTC day) — no special-casing of "today's incomplete partition." Rely on the catalog's `write_data` de-dup/disjoint-interval handling rather than custom partial-day logic.

**Validation Failure Behavior**
- **D-05:** Instrument validation happens in `on_start`, after `InstrumentProviderConfig` has loaded the cache but before any subscriptions are issued. If any configured instrument is missing from `self.cache.instruments()`, the strategy raises (does not silently `stop()`), producing a non-zero exit so systemd `Restart=always` surfaces the failure in journald.
- **D-06:** The error message lists ALL missing instrument IDs in one message (collect all missing IDs before raising, don't fail on the first one).

**TOML Config Schema**
- **D-07:** Per-instrument settings use array-of-tables: `[[instruments.linear]]` and `[[instruments.spot]]`, each entry with `id`, `depth`, `bar_intervals`. Linear and spot kept as separate sections.
- **D-08:** Recorder-wide settings (`trader_id`, `catalog_path`, `streaming_path`, `conversion_interval_minutes`, `environment`) live in a single top-level `[recorder]` table.
- **D-09:** Bybit API credentials are NOT read from the TOML — sourced exclusively via the adapter's env var convention (`BYBIT_API_KEY`/`BYBIT_API_SECRET` or `BYBIT_TESTNET_*`). No `api_key`/`api_secret` fields in config.

**Phase 1 Slice Scope**
- **D-10:** The end-to-end proof uses Bybit **mainnet** with one liquid linear perpetual (e.g. `BTCUSDT-LINEAR.BYBIT`).

### Claude's Discretion
- Exact `[recorder]` field names beyond those listed, internal module/file layout for the collector script, and the precise structure of the array-of-tables entries (additional optional fields) are left to planning/implementation as long as the decisions above hold.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONF-01 | Configure which Bybit instruments to record (linear-USDT + spot) via TOML | `tomllib` (stdlib, Python ≥3.12) parses `[[instruments.linear]]`/`[[instruments.spot]]`; each `id` becomes `InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")`. Symbol convention verified in `bybit_data_tester.py:38-40`. |
| CONF-02 | Configure per-instrument depth + bar intervals via TOML | `depth` (int) and `bar_intervals` (list[str]) fields parsed but not acted on in Phase 1 (Phase 2 consumes them). Schema must exist now per CONTEXT specifics. |
| CONF-03 | Validate all configured instruments against Bybit's instrument cache on startup; fail fast | In `on_start`, `self.cache.instrument(instrument_id)` returns `None` if missing; collect all `None`s, raise with full list (D-05/D-06). Cache populated by `InstrumentProviderConfig(load_all=True)` or `load_ids=...`. |
| REC-01 | Subscribe to + record trade ticks per configured instrument | `self.subscribe_trade_ticks(instrument_id)` → `on_trade_tick(self, tick: TradeTick)`. Verified `actor.pyx:1722` / `:479`. Trade ticks auto-flow to `StreamingFeatherWriter` via the `*` msgbus subscription. |
| REC-07 | Persist via Nautilus `StreamingConfig`/`StreamingFeatherWriter` (no custom writer) | Set `TradingNodeConfig.streaming = StreamingConfig(catalog_path=...)`. Kernel auto-wires `StreamingFeatherWriter` subscribed to `"*"` (`kernel.py:604`). No custom writer. |
| REL-01 | Scheduled job converts streamed data into day-partitioned `ParquetDataCatalog` | `self.clock.set_timer(...)` callback calls `ParquetDataCatalog(catalog_path).convert_stream_to_data(instance_id, TradeTick, subdirectory="live")`. Day-partitioning requires daily feather rotation (see Pitfall 1). |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| TOML config loading | Recorder script (Python entrypoint) | — | Plain stdlib parsing before node construction; not Nautilus's concern |
| Node/adapter bootstrap | `TradingNode` + Bybit `LiveDataClientFactory` | Kernel | Framework owns connection, instrument provider, WS multiplexing |
| Instrument validation | Strategy `on_start` | Cache | Cache is the single source of truth; strategy enforces the config contract |
| Trade-tick capture | Bybit `DataClient` → MessageBus | Strategy `on_trade_tick` | Adapter streams; strategy subscribes; writer persists |
| Feather persistence | `StreamingFeatherWriter` (kernel) | `StreamingConfig` | Native, subscribed to `"*"` — zero custom code |
| Feather→parquet conversion | Strategy timer callback | `ParquetDataCatalog` | In-process per D-01; catalog owns the parquet write/dedup |
| Day partitioning | `StreamingFeatherWriter` rotation | `convert_stream_to_data` filenames | Rotation defines the time slice; conversion names the parquet by interval |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `nautilus_trader` (this repo) | workspace HEAD | TradingNode, Bybit adapter, StreamingConfig, ParquetDataCatalog | Project mandate — official framework, no custom infra |
| `tomllib` | stdlib (Python ≥3.12) | Parse the `[recorder]` + `[[instruments.*]]` TOML | Zero-dependency; `requires-python = ">=3.12,<3.15"` confirmed in `pyproject.toml:25` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pandas` | already a dep | `pd.Timedelta` for `set_timer` interval, `RotationMode` interval | Timer interval construction; daily rotation interval |
| `fsspec` | already a dep | Filesystem abstraction under the catalog | Implicit — catalog/writer use it internally; no direct use needed |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `tomllib` | `tomlkit`/`toml`/`pydantic-settings` | Unnecessary new dependency; `tomllib` is stdlib and read-only (the recorder never writes config) |
| In-process timer (D-01) | systemd timer calling a separate convert script | Violates single-process constraint for Phase 1; deferred to ops phase |
| `RotationMode.SCHEDULED_DATES` | `RotationMode.INTERVAL` (1 day) | INTERVAL rotates 24h after first write (drifts off UTC midnight); SCHEDULED_DATES pins to a wall-clock time (00:00 UTC) → true calendar-day partitions |

**Installation:**
```bash
# No new packages. tomllib is stdlib; nautilus_trader is the workspace package.
# Build/install the workspace package per repo convention (uv + build.py).
```

**Version verification:** No external packages installed — Package Legitimacy Audit is N/A. `tomllib` is a CPython stdlib module (PEP 680, available 3.11+); `requires-python = ">=3.12,<3.15"` confirmed at `pyproject.toml:25`, runtime `python3 --version` = 3.13.13. [VERIFIED: repo grep + runtime]

## Package Legitimacy Audit

No external packages are installed by this phase. All code uses the in-repo `nautilus_trader` workspace package plus Python stdlib (`tomllib`, `uuid`, `pathlib`). **Audit: N/A — no third-party installs.**

## Architecture Patterns

### System Architecture Diagram

```
                         recorder.toml
                              │
                              ▼
                    ┌──────────────────┐
                    │  tomllib.load()  │  (entrypoint, before node build)
                    └────────┬─────────┘
                             │  [recorder] table + [[instruments.linear/spot]] arrays
                             ▼
          ┌─────────────────────────────────────────────┐
          │            TradingNodeConfig                 │
          │  trader_id, instance_id=<fixed UUID4>,       │
          │  streaming=StreamingConfig(catalog_path,     │
          │            rotation_mode=SCHEDULED_DATES),    │
          │  data_clients={BYBIT: BybitDataClientConfig( │
          │     environment=MAINNET,                     │
          │     product_types=(LINEAR, SPOT),            │
          │     instrument_provider=InstrumentProvider   │
          │        Config(load_ids=...))}                │
          └──────────────────────┬──────────────────────┘
                                 │ node.build(); node.run()
                                 ▼
   Bybit WS  ───trades───▶  DataEngine ──▶ MessageBus("*") ──▶ StreamingFeatherWriter
   (mainnet)                     │                                      │
                                 │ on_trade_tick                        ▼
                                 ▼                          {catalog_path}/live/{instance_id}/
                          RecorderStrategy                    trade_tick/{instrument}/*.feather
                          ├─ on_start: validate cache,                  │  (rotates daily, UTC 00:00)
                          │   subscribe_trade_ticks                     │
                          └─ clock.set_timer(every N min) ──────────────┘
                                 │ callback
                                 ▼
              ParquetDataCatalog(catalog_path)
                 .convert_stream_to_data(instance_id, TradeTick, subdirectory="live")
                                 │
                                 ▼
              {catalog_path}/data/trade_tick/{instrument}/{start_ts}_{end_ts}.parquet
                                 │
                                 ▼ (verification)
              catalog.trade_ticks(instrument_ids=[...]) -> list[TradeTick]
```

### Recommended Project Structure
```
scripts/bybit_recorder/      # (location is planner's discretion)
├── recorder.py              # main(): load TOML, build TradingNodeConfig, run node
├── config.py               # dataclasses for [recorder] + instrument entries, TOML loader
├── strategy.py             # RecorderStrategy(Strategy) + RecorderStrategyConfig
└── recorder.toml           # example/default config
```

### Pattern 1: Enable native streaming on the node
**What:** Turn on `StreamingFeatherWriter` by setting `TradingNodeConfig.streaming`. The kernel subscribes the writer to `"*"` automatically — no per-type wiring.
**When to use:** Always, for REC-07.
**Example:**
```python
# Source: nautilus_trader/persistence/config.py (StreamingConfig) + system/kernel.py:587-605
from datetime import time as dt_time
import pandas as pd
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode

streaming = StreamingConfig(
    catalog_path=recorder_cfg.catalog_path,   # writer base; feather lands at {path}/live/{instance_id}
    fs_protocol="file",
    rotation_mode=RotationMode.SCHEDULED_DATES,  # daily calendar partitions (see Pitfall 1)
    rotation_interval=pd.Timedelta(days=1),
    rotation_time=dt_time(0, 0, 0),
    rotation_timezone="UTC",
    include_types=[TradeTick],                  # Phase 1 records only trades
)
```
> Note `kernel.py:589`: `path = f"{config.catalog_path}/{self._environment.value}/{self.instance_id}"`. For a `TradingNode`, `environment.value == "live"`. So feather files live under `{catalog_path}/live/{instance_id}/`.

### Pattern 2: Fixed instance_id for stable streaming directory (D-03)
**What:** A constant valid RFC-4122 v4 UUID passed to both the node config and the strategy config, so the feather dir and the conversion path agree across restarts.
**When to use:** Always, for D-03 + REL-01.
**Example:**
```python
# Source: nautilus_trader/system/config.py:108 (instance_id: UUID4 | None)
#         nautilus_trader/core/uuid.pyx:75-84 (from_str requires v4 RFC-4122)
from nautilus_trader.core.uuid import UUID4

# Generate ONCE (e.g. uuid.uuid4()) and hardcode as a constant — NOT derived from trader_id.
RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"  # any valid v4 UUID

config_node = TradingNodeConfig(
    trader_id=TraderId(recorder_cfg.trader_id),
    instance_id=UUID4.from_str(RECORDER_INSTANCE_ID),
    streaming=streaming,
    ...
)
# Pass the same string to the strategy config so the timer callback can build the path.
```
> `UUID4.from_str` raises if the string is not a valid version-4 RFC-4122 UUID (`uuid.pyx:78-80`). D-03's phrasing "derived from a constant trader_id/instance name" is NOT literally achievable — it must be a fixed valid UUID4 constant. Surface this to the user.

### Pattern 3: Fail-fast instrument validation collecting all misses (D-05/D-06)
**What:** After the provider loads the cache, look up every configured ID; collect all that return `None`; raise once with the full list.
**When to use:** `on_start`, before any `subscribe_*`.
**Example:**
```python
# Source: cache.instrument() pattern in bybit_options_data_collector.py:276-281
def on_start(self) -> None:
    missing: list[str] = []
    for instrument_id in self._configured_ids:           # list[InstrumentId]
        if self.cache.instrument(instrument_id) is None:
            missing.append(str(instrument_id))
    if missing:
        raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")

    for instrument_id in self._configured_ids:
        self.subscribe_trade_ticks(instrument_id)

    # D-01: drive conversion from a repeating timer.
    self.clock.set_timer(
        name="convert-stream",
        interval=pd.Timedelta(minutes=self.config.conversion_interval_minutes),
        callback=self._convert_stream,                   # Callable[[TimeEvent], None]
    )
```
> Raising in `on_start` propagates as a non-zero exit (D-05) rather than a clean `stop()`. Verify the node/runner surfaces the exception to the process exit code during planning (acceptance check).

### Pattern 4: Timer-driven conversion (D-01/D-04/REL-01)
**What:** A repeating timer whose callback opens the catalog and converts all feather data for the fixed instance_id.
**Example:**
```python
# Source: convert_stream_to_data signature parquet.py:2523-2554
#         set_timer signature component.pyx:419-455
#         live-subdirectory usage test_catalog_pyo3.py:1571-1583
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

def _convert_stream(self, event: TimeEvent) -> None:
    catalog = ParquetDataCatalog(self.config.catalog_path)   # path == StreamingConfig.catalog_path
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,   # str form of the fixed UUID4
        data_cls=TradeTick,
        subdirectory="live",                # default is "backtest" — MUST override for live runs
    )
```
> `convert_stream_to_data` is idempotent on already-written intervals: if `{start_ts}_{end_ts}.parquet` already exists it prints "already exists, skipping write" and returns (`parquet.py:2602-2604`). Non-disjoint intervals **raise** `ValueError` (`parquet.py:2608-2612`) — this is why daily feather rotation matters (Pitfall 1).

### Pattern 5: TOML loading (CONF-01/CONF-02, no existing loader in repo)
**What:** Plain stdlib parse — there is **no** existing TOML config-loader pattern in `nautilus_trader` for runtime config (verified: no `import tomllib`/`import toml` in `nautilus_trader/` or `examples/`). This is greenfield.
**Example:**
```python
# Source: greenfield — Python stdlib tomllib (PEP 680), Python >=3.12 per pyproject.toml:25
import tomllib
from nautilus_trader.model.identifiers import InstrumentId

with open(path, "rb") as f:                  # tomllib requires binary mode
    raw = tomllib.load(f)

recorder = raw["recorder"]
linear = raw.get("instruments", {}).get("linear", [])   # list[dict]
spot   = raw.get("instruments", {}).get("spot", [])
ids = [InstrumentId.from_str(e["id"]) for e in (*linear, *spot)]
```

### Anti-Patterns to Avoid
- **Custom pandas/parquet writer** (as in `bybit_options_data_collector.py:608-661`): forbidden by REC-07 and project memory ("prefer Nautilus built-ins"). Use `StreamingConfig` + `convert_stream_to_data`.
- **Per-run timestamped instance_id**: breaks D-03; the conversion path would change every restart and old feather data would be orphaned.
- **Forgetting `subdirectory="live"`**: the default is `"backtest"` (`parquet.py:2528`), which reads from the wrong feather subdirectory and silently converts nothing.
- **`NO_ROTATION` for a 24/7 recorder**: all trades for a given instrument accumulate in one feather file → one giant parquet file spanning the whole run, not day-partitioned (Pitfall 1).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Persisting ticks to disk | Custom parquet appender | `StreamingConfig`/`StreamingFeatherWriter` (auto-wired by kernel) | Arrow-schema-correct, msgbus-subscribed, catalog-compatible; REC-07 mandate |
| Feather→parquet conversion | Manual pyarrow read/write | `ParquetDataCatalog.convert_stream_to_data()` | Handles schema, ts_init monotonicity, disjoint-interval checks, dedup |
| Day partitioning | Custom date-bucketing of rows | `RotationMode.SCHEDULED_DATES` daily feather rotation | Each daily feather → one parquet named by its UTC-day interval |
| Periodic scheduling | `threading.Timer`/asyncio task | `self.clock.set_timer(...)` | Integrates with the single-threaded event loop; deterministic; correct clock |
| Instrument loading | REST calls to Bybit | `InstrumentProviderConfig` on the data client | Adapter loads + caches instruments before `on_start` |
| TOML parsing | Hand-written parser | stdlib `tomllib` | Zero-dependency, correct, read-only |
| Reading data back | pyarrow dataset scan | `catalog.trade_ticks(instrument_ids=[...])` | Returns native `TradeTick` objects (success criterion 5) |

**Key insight:** Every persistence and scheduling primitive this phase needs already exists in the framework. The phase is wiring + one validation loop + one timer callback. The only genuinely new code is the TOML schema/loader and the strategy class.

## Common Pitfalls

### Pitfall 1: "Day-partitioned" requires daily feather rotation — NOT a free property of the catalog
**What goes wrong:** With the default `RotationMode.NO_ROTATION`, the writer keeps one feather file per (type, instrument) for the entire run. `convert_stream_to_data` then produces a single parquet file named by the run's full min/max `ts_init` interval — not one file per UTC day. The roadmap/PROJECT goal ("partitioned by UTC day") is silently unmet.
**Why it happens:** The catalog's "partitioning" is purely the `{start_ts}_{end_ts}.parquet` filename derived from each feather file's min/max ts (`parquet.py:2594-2599`). Day boundaries only appear if the feather files are rotated on day boundaries.
**How to avoid:** Set `StreamingConfig(rotation_mode=RotationMode.SCHEDULED_DATES, rotation_interval=pd.Timedelta(days=1), rotation_time=time(0,0,0), rotation_timezone="UTC")`. Verified fields exist on both `StreamingConfig` (`config.py:69-73`) and `StreamingFeatherWriter` (`writer.py:101-105`).
**Warning signs:** After a multi-day run, `{catalog}/data/trade_tick/{id}/` contains a single parquet file with a multi-day timestamp span.
**Note for planner/user:** CONTEXT decisions D-01..D-10 do not mention rotation mode. This is a gap — confirm daily rotation with the user, or accept that Phase 1's slice produces interval-named (not strictly per-day) parquet files. [ASSUMED that "day-partitioned" is a hard requirement — A1]

### Pitfall 2: Non-disjoint intervals raise on re-conversion
**What goes wrong:** If two feather files for the same instrument cover overlapping `ts_init` ranges (e.g. an in-flight feather file converted now, then again after more rows are appended), `convert_stream_to_data` → `_convert_feather_table_to_parquet` raises `ValueError: ...would create non-disjoint intervals` (`parquet.py:2608-2612`).
**Why it happens:** D-04 converts "all available feather data including the current/partial day" every interval. The current day's feather file grows between runs, so its (start,end) interval expands and overlaps the previously written parquet for that day.
**How to avoid (planner must choose):** (a) Only the daily-rotated *closed* feather files are safely convertible; the *current* open file changes interval each run. Options: convert and rely on the "already exists, skipping write" guard (only works if start AND end are byte-identical — they won't be while the day is open); OR (b) accept that the current-day parquet is overwritten — but the disjoint check fires first. Recommended: with daily rotation, each run re-derives the same closed-day intervals (idempotent skip) and the *open* day produces a new interval each run → **this can raise**. Plan a verification task: run conversion twice mid-day and observe behavior; if it raises, gate conversion to closed days or pass an `other_catalog`/handle the `ValueError`.
**Warning signs:** `ValueError` in the timer callback logs after the second conversion within the same UTC day.
[ASSUMED behavior on partial-day re-conversion — needs an empirical verification task — A2]

### Pitfall 3: `subdirectory` defaults to "backtest"
**What goes wrong:** Omitting `subdirectory="live"` makes the conversion read `{catalog}/backtest/{instance_id}/...`, which is empty for a `TradingNode` run (it writes to `live/`). Conversion silently does nothing.
**How to avoid:** Always pass `subdirectory="live"` (verified default at `parquet.py:2528`; live usage in `test_catalog_pyo3.py:1571-1583`).
**Warning signs:** Conversion runs without error but `{catalog}/data/trade_tick/` stays empty.

### Pitfall 4: `instance_id` must be a real UUID4 string
**What goes wrong:** Trying to set `instance_id` to a human label (e.g. "BYBIT-COLLECTOR-001") fails — `UUID4.from_str` raises (`uuid.pyx:78-80`).
**How to avoid:** Hardcode a generated valid v4 UUID constant; reuse it for the node config and the conversion call (`str(instance_id)`).

### Pitfall 5: `catalog_path` vs `streaming_path` (D-08) must be reconciled
**What goes wrong:** D-08 lists both `catalog_path` and `streaming_path` in `[recorder]`. But the kernel derives the feather path from `StreamingConfig.catalog_path` (`kernel.py:589`), and conversion reads from `ParquetDataCatalog(path).../live/{instance_id}` and writes parquet to `{path}/data/...`. If `streaming_path != catalog_path`, the conversion catalog won't find the feather files unless `ParquetDataCatalog` is constructed at the streaming root.
**How to avoid:** Either (a) set `StreamingConfig.catalog_path = streaming_path` AND construct the conversion `ParquetDataCatalog(streaming_path)` so feather + parquet share a root (parquet goes to `{streaming_path}/data/...`); or (b) keep them equal in Phase 1. Recommend the planner define the relationship explicitly. The feather files MUST be under `{conversion_catalog.path}/live/{instance_id}/` for conversion to see them.
**Warning signs:** Conversion finds zero feather files despite trades flowing.

## Runtime State Inventory

> Greenfield phase — no rename/refactor/migration. This section is included only to note persistent on-disk state created by the recorder (relevant to Phase 3 restart goals).

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Feather stream files at `{catalog_path}/live/{instance_id}/...`; parquet at `{catalog_path}/data/...`; a `config.json` snapshot written to the streaming dir (`kernel.py:608-611`) | None for Phase 1 (greenfield); Phase 3 relies on stable `instance_id` (D-03) |
| Live service config | None — single process, no external service config | None |
| OS-registered state | None in Phase 1 (systemd unit is Phase 5/OPS-02) | None |
| Secrets/env vars | `BYBIT_API_KEY`/`BYBIT_API_SECRET` (and `BYBIT_TESTNET_*`) — sourced by adapter; NOT needed for public market data (D-10) | Document; not required for mainnet trade-tick subscription |
| Build artifacts | None new | None |

**Nothing found requiring migration** — verified: this is the first phase of a new project.

## Code Examples

### Bybit node bootstrap (verified template)
```python
# Source: examples/live/bybit/bybit_data_tester.py:17-68 (verified working pattern)
from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.config import InstrumentProviderConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId

config_node = TradingNodeConfig(
    trader_id=TraderId("BYBIT-COLLECTOR-001"),
    instance_id=UUID4.from_str(RECORDER_INSTANCE_ID),
    logging=LoggingConfig(log_level="INFO", log_level_file="INFO",
                          log_directory="logs", log_file_name="bybit_recorder", use_pyo3=True),
    streaming=streaming,  # from Pattern 1
    data_clients={
        BYBIT: BybitDataClientConfig(
            environment=BybitEnvironment.MAINNET,                 # D-10
            product_types=(BybitProductType.LINEAR, BybitProductType.SPOT),  # D-07
            instrument_provider=InstrumentProviderConfig(
                load_ids=frozenset(configured_instrument_ids),    # or load_all=True
            ),
        ),
    },
    timeout_connection=30.0, timeout_disconnection=10.0, timeout_post_stop=5.0,
)
node = TradingNode(config=config_node)
node.trader.add_strategy(RecorderStrategy(config=strategy_config))
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
node.build()
node.run()
```

### Bybit instrument-id convention (verified)
```python
# Source: examples/live/bybit/bybit_data_tester.py:37-40
product_type = BybitProductType.LINEAR        # .value == "linear" (Display impl, enums.rs:321)
symbol = f"BTCUSDT-{product_type.value.upper()}"   # "BTCUSDT-LINEAR"
instrument_id = InstrumentId.from_str(f"{symbol}.{BYBIT}")  # "BTCUSDT-LINEAR.BYBIT"
# Spot: "ETHUSDT-SPOT.BYBIT"
```

### Reading data back (success criterion 5)
```python
# Source: nautilus_trader/persistence/catalog/base.py:176-181
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
catalog = ParquetDataCatalog(catalog_path)
trades = catalog.trade_ticks(instrument_ids=["BTCUSDT-LINEAR.BYBIT"])  # -> list[TradeTick]
assert all(isinstance(t, TradeTick) for t in trades)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Custom pandas parquet writer (options collector example) | `StreamingConfig` + `convert_stream_to_data` | Project decision (STATE.md) | No bespoke serialization; catalog-native |
| `load_all=True` then filter in code | `InstrumentProviderConfig(load_ids=...)` | Available now | Loads only needed instruments; faster startup, smaller cache |

**Deprecated/outdated:**
- The persistence approach in `bybit_options_data_collector.py` (custom `_append_to_parquet_file`) — do not copy; it predates the catalog-streaming decision.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | "Day-partitioned" is a hard requirement, so daily feather rotation (`SCHEDULED_DATES`) must be configured | Pitfall 1 | If only "interval-named files" are required, rotation config is optional and Phase 1 is simpler. Confirm with user. |
| A2 | Re-converting the open/current-day feather each interval may raise `ValueError` on non-disjoint intervals | Pitfall 2 | If it raises, D-04's "convert all available incl. partial day every run" needs guarding (closed-days-only or catch ValueError). Needs an empirical verification task. |
| A3 | Raising in `on_start` yields a non-zero process exit code under `TradingNode.run()` | Pattern 3 / D-05 | If the runner swallows the exception into a clean stop, systemd won't see failure. Add an acceptance test that asserts non-zero exit. |
| A4 | `streaming_path` and `catalog_path` (D-08) should be reconciled to a shared root so conversion finds feather files | Pitfall 5 | If kept distinct without redirection, conversion finds no feather data. Planner must define the relationship. |

## Open Questions (RESOLVED)

1. **Daily rotation vs CONTEXT silence on rotation mode**
   - What we know: catalog "day partitioning" only emerges from feather rotation; `SCHEDULED_DATES` gives UTC-midnight daily files.
   - What's unclear: whether the user wants strict per-day files or accepts interval-named files for the Phase 1 slice.
   - Recommendation: Plan with `RotationMode.SCHEDULED_DATES` daily/UTC; flag A1 for user confirmation in discuss/plan-check.
   - **RESOLVED:** Plan 01-02's `build_streaming_config()` constructs `StreamingConfig` with `rotation_mode=RotationMode.SCHEDULED_DATES`, `rotation_interval=pd.Timedelta(days=1)`, `rotation_time=time(0,0,0)`, `rotation_timezone="UTC"` (01-02-PLAN.md task verifying `RotationMode\.SCHEDULED_DATES`).

2. **Partial-day re-conversion behavior (D-04)**
   - What we know: non-disjoint intervals raise; identical intervals are skipped idempotently.
   - What's unclear: exact behavior when the current-day feather grows between conversions.
   - Recommendation: Add an explicit verification task (run conversion twice mid-day, observe). If it raises, gate to closed days or catch the `ValueError`.
   - **RESOLVED:** see 01-04 Task 1 (`test_double_conversion_same_day_behavior`) — converts the same UTC-day feather data twice and asserts whichever real behavior the framework exhibits (idempotent skip vs `ValueError` on non-disjoint intervals), pinned with an `# A2 RESULT:` comment.

3. **Exit-code on `on_start` raise**
   - What we know: D-05 wants a non-zero exit.
   - What's unclear: how `TradingNode.run()` propagates an `on_start` exception to the process exit code.
   - Recommendation: Verification task asserting non-zero exit on a deliberately-missing instrument.
   - **RESOLVED:** see 01-04 Task 2 — runs the recorder with a deliberately-missing instrument and asserts the process exits non-zero (D-05/A3).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All | ✓ | 3.13.13 (`requires-python >=3.12`) | — |
| `tomllib` (stdlib) | TOML loading | ✓ | stdlib (3.11+) | — |
| `nautilus_trader` workspace build | Everything | ✓ (repo) | HEAD | rebuild via `uv` + `build.py` if extensions stale |
| Bybit mainnet WS reachability | REC-01 end-to-end proof | ✗ verify at runtime | — | Use a liquid instrument (BTCUSDT-LINEAR) for fast trade flow; no API key needed for public data |
| `BYBIT_API_KEY`/`BYBIT_API_SECRET` | NOT needed for public market data | n/a | — | Public trade-tick subscription works without credentials (D-10) |

**Missing dependencies with no fallback:** None blocking.
**Missing dependencies with fallback:** Live Bybit connectivity is verified only at runtime; planner should make the end-to-end success criteria a runtime smoke test, not a unit-test gate.

## Validation Architecture

> `.planning/config.json` not read (assumed nyquist_validation enabled). Test framework is pytest per CLAUDE.md.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.4.4 (per CLAUDE.md) |
| Config file | repo `pyproject.toml` (pytest config) |
| Quick run command | `pytest <test_file> -x -q` |
| Full suite command | `pytest tests/` (scoped to new recorder tests for this phase) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONF-01 | TOML loads instrument list into InstrumentId list | unit | `pytest tests/.../test_recorder_config.py -x` | ❌ Wave 0 |
| CONF-02 | depth + bar_intervals parsed per instrument | unit | `pytest tests/.../test_recorder_config.py -x` | ❌ Wave 0 |
| CONF-03 | missing instrument → raises with all missing IDs | unit (mock cache) | `pytest tests/.../test_recorder_strategy.py -x` | ❌ Wave 0 |
| REC-01 | subscribe_trade_ticks called per instrument | unit (mock) | `pytest tests/.../test_recorder_strategy.py -x` | ❌ Wave 0 |
| REC-07 | StreamingConfig wired on node (no custom writer) | unit (config assertion) | `pytest tests/.../test_recorder_config.py -x` | ❌ Wave 0 |
| REL-01 | convert_stream_to_data turns feather→parquet; trades reload | integration | `pytest tests/.../test_recorder_conversion.py -x` | ❌ Wave 0 |
| (E2E) | live Bybit → feather → parquet → reload as TradeTick | manual smoke | run recorder ~2 min on BTCUSDT-LINEAR, then `catalog.trade_ticks(...)` | manual |

### Sampling Rate
- **Per task commit:** `pytest <touched test file> -x -q`
- **Per wave merge:** `pytest tests/<recorder dir>/ -q`
- **Phase gate:** recorder test dir green + one manual live smoke run reloading ≥1 TradeTick.

### Wave 0 Gaps
- [ ] `test_recorder_config.py` — TOML parse, instrument-id construction, StreamingConfig assertions (CONF-01/02, REC-07)
- [ ] `test_recorder_strategy.py` — validation raise-all-missing, subscribe calls (CONF-03, REC-01)
- [ ] `test_recorder_conversion.py` — feather→parquet integration using a temp catalog + synthetic TradeTicks, reload via `catalog.trade_ticks` (REL-01, criterion 5)
- [ ] `conftest.py` — fixtures: temp catalog dir, mock cache, sample TOML, sample TradeTicks

## Security Domain

> `security_enforcement` assumed enabled. This phase has a narrow surface: read-only TOML, public market-data subscription, local filesystem writes.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Public market data needs no credentials (D-10); exec/private API out of scope |
| V3 Session Management | no | No user sessions |
| V4 Access Control | no | Single local process, local filesystem |
| V5 Input Validation | yes | Validate TOML: instrument IDs parse via `InstrumentId.from_str` (raises on bad), depth is int, conversion_interval positive; fail-fast on missing instruments (CONF-03) |
| V6 Cryptography | no | None hand-rolled; TLS for Bybit WS handled by adapter (rustls) |
| V7 Error Handling/Logging | yes | Log validation failures with full missing-ID list; non-zero exit on fatal (D-05) so journald surfaces it |
| V12 File/Resources | yes | Write only under configured `catalog_path`/`streaming_path`; do not accept path traversal from TOML without bounding to a configured root |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/hostile TOML (bad instrument id, negative interval, traversal path) | Tampering | `tomllib` (no code exec), validate types, `InstrumentId.from_str` rejects bad ids, clamp interval > 0, resolve+bound output paths |
| Secret leakage (API keys in config or logs) | Info Disclosure | D-09: never read keys from TOML; rely on env vars; never log credentials. Phase 1 needs no keys at all |
| Silent data loss masked as clean exit | Repudiation/Availability | D-05 raise→non-zero exit so systemd `Restart=always` + journald record the failure |

## Sources

### Primary (HIGH confidence — read directly from repo source this session)
- `nautilus_trader/persistence/catalog/parquet.py` — `convert_stream_to_data` (2523-2573), `_convert_feather_table_to_parquet` (2575-2619), `_make_path` (2384-2398), `_list_feather_data_files` (2826-2858), `_timestamps_to_filename` (2861-2865), `write_data` (253-353), `trade_ticks` (base.py:176-181), `__init__`/`from_uri` (141-200)
- `nautilus_trader/persistence/config.py` — `StreamingConfig` fields (28-87)
- `nautilus_trader/persistence/writer.py` — `StreamingFeatherWriter.__init__` (92-159), rotation logic (286-345), `_create_identifier_writer` (405-430), `RotationMode` (50-54)
- `nautilus_trader/system/kernel.py` — `_setup_streaming` path derivation + `"*"` subscription (587-611), `instance_id` (160, 726-735)
- `nautilus_trader/system/config.py` — `NautilusKernelConfig.instance_id/streaming/environment` (106-124)
- `nautilus_trader/adapters/bybit/config.py` — `BybitDataClientConfig` fields incl. env-var sourcing (34-90)
- `nautilus_trader/adapters/bybit/__init__.py` — exports (`BybitProductType`, `BybitEnvironment`, factories) (28-70)
- `nautilus_trader/trading/strategy.pyx` — real `set_timer` usage (1803-1809)
- `nautilus_trader/common/component.pyx` — `set_timer` signature/docs (419-483)
- `nautilus_trader/common/actor.pyx` — `subscribe_trade_ticks` (1722-1770), `on_trade_tick` (479)
- `nautilus_trader/common/config.py` — `InstrumentProviderConfig` (441-483)
- `nautilus_trader/core/uuid.pyx` — `UUID4.from_str` v4 RFC-4122 requirement (75-103)
- `nautilus_trader/common/__init__.py` — `Environment` enum values (33-40)
- `crates/common/src/enums.rs` — `Environment` (200-204); `crates/adapters/bybit/src/common/enums.rs` — `BybitProductType` + Display lowercase (271-323)
- `examples/live/bybit/bybit_data_tester.py` — verified bootstrap + instrument-id convention (17-106)
- `examples/live/bybit/bybit_options_data_collector.py` — node/strategy wiring (NOT persistence) (783-868)
- `tests/acceptance_tests/test_backtest.py` — `StreamingConfig` + `convert_stream_to_data` usage (2603-2727)
- `tests/unit_tests/persistence/test_catalog_pyo3.py` — `subdirectory="live"` usage (1571-1583)
- `pyproject.toml:25` — `requires-python = ">=3.12,<3.15"` (tomllib stdlib availability)

### Secondary (MEDIUM confidence)
- None — all claims verified against repo source.

### Tertiary (LOW confidence)
- A1–A4 in Assumptions Log: inferred behaviors (rotation requirement, partial-day re-conversion, exit-code propagation, path reconciliation) flagged for empirical verification tasks during planning/execution.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all APIs read from source; tomllib stdlib confirmed against pyproject + runtime.
- Architecture: HIGH — streaming path, conversion signature, day-partition mechanism all traced through source.
- Pitfalls: HIGH on mechanism (read from source); the *operational* edges (A1–A4) are MEDIUM and flagged for verification tasks.

**Research date:** 2026-06-13
**Valid until:** 2026-07-13 (stable in-repo APIs; re-verify if the `persistence` or `bybit` adapter modules change)
