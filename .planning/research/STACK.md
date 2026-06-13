# Stack Research

**Domain:** NautilusTrader live market-data recorder (Bybit linear perps USDT + spot → official ParquetDataCatalog)
**Researched:** 2026-06-13
**Confidence:** HIGH (verified against actual repo source: `nautilus_trader==1.229.0`, this fork)

> All findings below are grep/read-verified against source in `/home/mrqdt/code/nautilus_trader_fork`, not from training-data recall. Exact file:line references are inline so the roadmap author can re-verify.

---

## Headline Recommendation (read this first)

**Do NOT manually call `catalog.write_data(...)` from inside the live `Strategy`/`Actor` on every tick.** The `ParquetDataCatalog.write_data` API is a **batch** writer, not an append/streaming writer, and is explicitly **not threadsafe** (`parquet.py:131`). It writes one immutable `{start}_{end}.parquet` file per call, refuses overlapping time intervals (raises `ValueError`, `parquet.py:383-387`), and silently skips if the target filename already exists (`parquet.py:376-378`). Calling it per-tick will either explode on the disjoint-interval check or produce thousands of tiny files.

**Instead, use the framework's first-class live-streaming persistence path:**

1. Configure the `TradingNode` with `StreamingConfig` (`nautilus_trader.persistence.config.StreamingConfig`). The kernel auto-subscribes a `StreamingFeatherWriter` to the message bus `"*"` topic (`kernel.py:604`) and writes **every** Nautilus data object to per-instrument **feather** files with **built-in daily rotation** (`RotationMode.SCHEDULED_DATES`, `rotation_time=00:00`, `rotation_timezone="UTC"`).
2. Periodically (and on shutdown) call `ParquetDataCatalog.convert_stream_to_data(...)` (`parquet.py:2523`) to materialize the rotated feather files into the official, query-/backtest-ready parquet catalog.

This gives you the PROJECT.md requirement ("official `ParquetDataCatalog` format, partitioned by day") **for free**, with zero custom serialization, and it captures all six data types via a single message-bus subscription. The strategy then only needs to `subscribe_*` — it does not need any `on_*` persistence logic at all.

This is the single most important architectural decision; PITFALLS.md and ARCHITECTURE.md expand on it.

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| `nautilus_trader` (this fork) | `1.229.0` (`pyproject.toml:3`) | TradingNode + Strategy/Actor + Bybit adapter + persistence | The project IS a Nautilus sub-project; everything below ships in-repo |
| Python | `>=3.12,<3.15`; target `3.12` (`pyproject.toml:25,297`) | Runtime | Repo pins 3.12 as the floor/target; build and CI assume it. Use **3.12** for the recorder to match `ruff target-version` and `mypy python_version` |
| `pyarrow` | `>=24.0.0` (`pyproject.toml:33`) | Arrow/feather/parquet backbone | Already a hard dependency; both `StreamingFeatherWriter` and `ParquetDataCatalog` use it. Do not pin a different version |
| `nautilus_trader.adapters.bybit` | in-repo | Bybit WS/HTTP data client, instrument provider, factories | Already exposes `BybitDataClientConfig`, `BybitProductType`, `BybitLiveDataClientFactory` (`adapters/bybit/__init__.py:28-71`) |

### Supporting Libraries (all already transitive deps — no new installs)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pandas` | `>=2.3.3,<3.0.0` (`pyproject.toml:31`) | The pandas-based catalog inspection utility (PROJECT.md requirement) | Use `catalog.query(...)` → returns objects; or read parquet partitions directly with `pd.read_parquet` for ad-hoc viewing |
| `fsspec` | `==2026.2.0` (`pyproject.toml:28`) | Filesystem abstraction under the catalog | Implicit; only matters if you later move catalog to S3/GCS (catalog supports `fs_protocol`) |
| `msgspec` | `>=0.21.1` (`pyproject.toml:29`) | Config (de)serialization for `*Config` frozen structs | Implicit; all configs are `msgspec` frozen structs |
| `uvloop` | `0.22.1` (`pyproject.toml:36`) | Event loop for the live node (non-Windows) | Auto-used by `TradingNode` on Linux; the systemd target |

### Development / Ops Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| systemd (`Restart=always`) | 24/7 supervision (PROJECT.md constraint) | Run the node script as a service; node handles WS reconnect internally |
| journald | Crash/restart log capture | Pair with Nautilus `LoggingConfig(log_directory=..., log_level_file=...)` for detailed file traces |
| `ruff==0.15.16`, `mypy==1.20.2` | Lint/type the recorder script | Repo-pinned (`pyproject.toml:98,96`); `examples/**` already exempt from several rules (`pyproject.toml:452-457`) |

### Installation

No new packages. The recorder is a script inside the existing fork; build the workspace as usual:

```bash
# From repo root — builds the Rust extensions + installs the Python package
uv sync                      # uv pinned to ==0.11.21 (pyproject.toml:124)
# or the repo's documented build path (make / build.py) if uv sync is insufficient
```

Bybit credentials (data-only needs none for public market data, but the client config reads them if present):

```bash
export BYBIT_API_KEY=...      # optional for public data streams
export BYBIT_API_SECRET=...   # optional for public data streams
```

---

## Exact API Surface (verified)

### 1. Node + client config (linear perps USDT + spot)

Imports (all confirmed in `examples/live/bybit/bybit_data_tester.py` and `adapters/bybit/__init__.py`):

```python
from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId
```

- `BybitProductType` members in scope: **`BybitProductType.LINEAR`** (USDT perps) and **`BybitProductType.SPOT`**. (Confirmed enum source `core.nautilus_pyo3`; `OPTION`/`INVERSE` exist but are out of scope per PROJECT.md.)
- A **single data client can hold multiple product types**: `product_types=(BybitProductType.LINEAR, BybitProductType.SPOT)` — confirmed the field type is `tuple[BybitProductType, ...]` (`config.py:80`). This satisfies the "single-process, no Redis" constraint.
- Instrument-id symbol convention (from `bybit_data_tester.py:38-40` and the options example `:792`):
  - Linear: `f"{SYM}USDT-LINEAR.BYBIT"` e.g. `ETHUSDT-LINEAR.BYBIT`
  - Spot: `f"{SYM}USDT-SPOT.BYBIT"` e.g. `BTCUSDT-SPOT.BYBIT`

`BybitDataClientConfig` fields relevant to a recorder (`config.py:34-90`):

| Field | Default | Use for recorder |
|-------|---------|------------------|
| `product_types` | `None` (→ all) | Set explicitly to `(LINEAR, SPOT)` |
| `environment` | `MAINNET` | `BybitEnvironment.MAINNET` |
| `instrument_provider` | — | `InstrumentProviderConfig(load_all=True)` so instruments are in cache before subscribe |
| `update_instruments_interval_mins` | `60` | Keep — refreshes instrument metadata |
| `bars_timestamp_on_close` | `True` | Keep — kline ts_event on close |
| `api_key`/`api_secret` | `None` (env fallback) | Optional for public data |

`TradingNodeConfig` + `LoggingConfig` (pattern from both examples):

```python
config_node = TradingNodeConfig(
    trader_id=TraderId("BYBIT-RECORDER-001"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_directory="logs",
        log_file_name="bybit_recorder",
        use_pyo3=True,           # examples use the pyo3 logger
    ),
    data_clients={
        BYBIT: BybitDataClientConfig(
            environment=BybitEnvironment.MAINNET,
            instrument_provider=InstrumentProviderConfig(load_all=True),
            product_types=(BybitProductType.LINEAR, BybitProductType.SPOT),
        ),
    },
    streaming=StreamingConfig(...),   # see section 2 — THIS is the recorder's persistence
    timeout_connection=30.0,
    timeout_reconciliation=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)
node = TradingNode(config=config_node)
node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
# No exec client factory needed — data-only recorder.
```

> Note: the recorder needs **only a data client** — drop `BybitLiveExecClientFactory`/`add_exec_client_factory` from the tester example.

### 2. Persistence: `StreamingConfig` + `convert_stream_to_data` (the recommended path)

```python
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode
import pandas as pd

streaming = StreamingConfig(
    catalog_path="/var/lib/bybit-recorder/catalog",
    rotation_mode=RotationMode.SCHEDULED_DATES,     # daily rotation
    rotation_interval=pd.Timedelta(days=1),
    rotation_timezone="UTC",
    flush_interval_ms=1000,
)
```

Verified facts:
- `StreamingConfig` fields (`persistence/config.py:62-73`): `catalog_path`, `rotation_mode`, `rotation_interval`, `rotation_time`, `rotation_timezone`, `flush_interval_ms`, `include_types`, `replace_existing`, `fs_protocol`.
- `RotationMode` enum (`writer.py:50-54`): `INTERVAL=1`, `SCHEDULED_DATES=2`, `NO_ROTATION=3`. **`SCHEDULED_DATES` with `rotation_time=00:00 UTC` gives day-boundary rotation** — directly satisfies "partitioned by day".
- Kernel auto-wires it: `_setup_streaming` subscribes `self._writer.write` to bus `"*"` (`kernel.py:587-605`). The writer path is `{catalog_path}/{environment}/{instance_id}`.
- `StreamingFeatherWriter` keeps **per-instrument** writers for exactly the recorder's data types (`writer.py:135-143`): `bar`, `order_book_deltas`, `order_book_depths`, `quote_tick`, `trade_tick`, `funding_rate_update`, `option_greeks`.

Materialize feather → parquet catalog (run on a timer and at shutdown):

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.data import TradeTick, QuoteTick, OrderBookDeltas, Bar, FundingRateUpdate

catalog = ParquetDataCatalog("/var/lib/bybit-recorder/catalog")
for data_cls in (TradeTick, QuoteTick, OrderBookDeltas, Bar, FundingRateUpdate):
    catalog.convert_stream_to_data(
        instance_id=node.kernel.instance_id.value,
        data_cls=data_cls,
        subdirectory="live",          # live runs write under "live" (parquet.py:2458)
    )
```

Verified: `convert_stream_to_data(instance_id, data_cls, other_catalog=None, subdirectory="backtest", ...)` exists at `parquet.py:2523`; reads rotated feather files and writes parquet partitions (`parquet.py:2557-2573`). For live runs use `subdirectory="live"`.

### 3. Data types ↔ Nautilus classes ↔ subscribe methods

All subscribe methods are inherited by `Strategy` from `Actor`; all confirmed used against Bybit in `test_kit/strategies/tester_data.py` (`DataTester(Actor)`), which `bybit_data_tester.py` runs live against `ETHUSDT-LINEAR.BYBIT`.

| Requirement | Nautilus data class (`nautilus_trader.model.data`) | Subscribe call | Verified at |
|-------------|---------------------------------------------------|----------------|-------------|
| Trade ticks | `TradeTick` | `self.subscribe_trade_ticks(instrument_id)` | tester_data.py:167-172 |
| Quote ticks (BBO) | `QuoteTick` | `self.subscribe_quote_ticks(instrument_id)` | tester_data.py:160-165 |
| Order book deltas | `OrderBookDeltas` | `self.subscribe_order_book_deltas(instrument_id, book_type=BookType.L2_MBP, depth=...)` | tester_data.py:127-133; options example :351 |
| Order book depth (fixed N) | `OrderBookDepth10` | `self.subscribe_order_book_depth(instrument_id, book_type=BookType.L2_MBP, depth=10)` | tester_data.py:151-158 |
| Bars / klines | `Bar` (+ `BarType`) | `self.subscribe_bars(bar_type)` with `BarType.from_str(f"{iid}-1-MINUTE-LAST-EXTERNAL")` | tester_data.py:250-256; bybit_data_tester.py:76 |
| Funding rate | `FundingRateUpdate` | `self.subscribe_funding_rates(instrument_id)` | tester_data.py:188-193; parse.rs:449-502 |
| Mark price (bonus, linear) | `MarkPriceUpdate` | `self.subscribe_mark_prices(instrument_id)` | tester_data.py:174-179; parse.rs:512 |
| Index price (bonus, linear) | `IndexPriceUpdate` | `self.subscribe_index_prices(instrument_id)` | tester_data.py:181-186; parse.rs:539 |

Bar/orderbook notes:
- `BarType` string grammar (Bybit kline): `"{INSTRUMENT_ID}-{step}-{aggregation}-{price_type}-EXTERNAL"`, e.g. `ETHUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL`. `EXTERNAL` = venue-aggregated (Bybit sends the kline), which is what you want for recording. Confirmed `bybit_data_tester.py:76`.
- Bybit WS depth levels are constrained by the venue. The options example uses `depth=50` for spot and `depth=25` for options (`:355-363`). For **linear/spot recording**, valid Bybit linear/spot depths are typically 1/50/200 (spot) and 1/50/200/500 (linear); confirm against the adapter's accepted depth at subscribe time. Bybit topic is `orderbook.{depth}.{symbol}` (`client.rs:969-987`).

### 4. Open interest — IMPORTANT GAP (flag for roadmap)

**There is no first-class `OpenInterest` Nautilus data type and no `subscribe_open_interest` method.** Verified:
- No `subscribe_open_interest` / `OpenInterest` symbol anywhere in `model/data` or strategy/actor (grep returned nothing).
- In the Bybit WS parser, `open_interest` is **only** emitted as a field of `OptionGreeks` (`parse.rs:647-667`) — options-only, out of scope.
- For **linear** tickers, `open_interest` is present on the raw Bybit ticker payload (`http/models.rs:176`, `websocket/messages.rs:628`) but is **not** mapped to any subscribable Nautilus data type in the linear WS path (the linear ticker parse yields `FundingRateUpdate` / `MarkPriceUpdate` / `IndexPriceUpdate` only — `parse.rs:449-558`).

**Implication for roadmap:** Capturing linear-perp open interest is **not a simple `subscribe_*` call**. Options to surface in roadmap (decreasing effort):
1. Define a small **custom data type** + record it via the catalog's custom-data path (the streaming writer supports custom types; catalog `write_data` handles `CustomData`). Requires emitting OI from the adapter or polling Bybit's `/v5/market/open-interest` HTTP endpoint on a timer from the strategy.
2. Treat OI as out-of-scope for v1 (PROJECT.md lists it as an Active requirement, so prefer option 1).

This is the one PROJECT.md requirement that the existing adapter does **not** satisfy out of the box. STACK confidence on OI specifically: **MEDIUM** (verified the gap exists; the exact best fix needs a phase-level spike).

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| `StreamingConfig` (feather) + `convert_stream_to_data` | Manual `catalog.write_data(...)` batched from `on_*` handlers | Only if you need parquet **immediately** (not after rotation) AND you implement your own per-instrument buffering + monotonic-ts batching + disjoint-interval management. Higher complexity, easy to corrupt the catalog. Not recommended for v1 |
| `RotationMode.SCHEDULED_DATES` (daily) | `RotationMode.SIZE` / `INTERVAL` | Use SIZE if disk-bound and you don't care about day partitions; INTERVAL for sub-daily rotation |
| `OrderBookDeltas` (full incremental book) | `OrderBookDepth10` (fixed 10-level snapshots) | Use `OrderBookDepth10` if you only need top-of-book depth and want smaller, fixed-schema files; deltas give full reconstructable book |
| Single data client, multi-product | Two separate data clients | Never needed here — `product_types` tuple multiplexes LINEAR+SPOT in one client/WS pool |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| The custom pandas-parquet pattern from `bybit_options_data_collector.py` (`_append_to_parquet_file`, `_save_all_data_to_parquet`) | Read-modify-write of a whole parquet file every interval (`:632-656`) is O(file) per flush, **not** the official catalog schema, **not** loadable into Nautilus backtests, and loses data on crash mid-write. Directly contradicts the PROJECT.md "official `ParquetDataCatalog`" decision | `StreamingConfig` + `convert_stream_to_data` |
| Per-tick `catalog.write_data()` from `on_trade_tick`/`on_quote_tick` | Not threadsafe (`parquet.py:131`); raises on overlapping intervals (`:383`); skips existing filenames (`:376`); one file per call → tiny-file explosion | Stream to feather, batch-convert |
| Custom Python `logging.FileHandler` rotation (options example `:729-776`) | Duplicates what Nautilus `LoggingConfig` + journald already do; competes with the pyo3 logger | `LoggingConfig(log_directory=, log_level_file=, log_file_name=)` + systemd/journald |
| `subscribe_open_interest(...)` | Does not exist | Custom data type or HTTP poll (see section 4) |
| Adding an exec client | Recorder is data-only | Only `BybitLiveDataClientFactory` |

---

## Stack Patterns by Variant

**If you need parquet available continuously (not only after daily rotation):**
- Run `convert_stream_to_data` on a strategy timer (e.g. hourly) over the closed feather files, not just at shutdown.
- Because rotation closes the prior day's feather at 00:00 UTC, a daily post-rotation conversion job is the simplest correct cadence.

**If disk fills up:**
- Old **parquet** partitions are independent `{start}_{end}.parquet` files per instrument/type — safe to archive/delete manually (PROJECT.md decision). Don't delete the active feather instance dir while the node runs.

**If you later scale beyond one WS connection's topic limit:**
- Out of scope for v1 (PROJECT.md). The single-client/`product_types`-tuple design holds for 10+ instruments.

---

## Version Compatibility

| Package | Version | Notes |
|---------|---------|-------|
| `nautilus_trader` | `1.229.0` | This fork; API surface above is pinned to this version |
| Python | `3.12` | Match repo target (`ruff`/`mypy` configured for 3.12); 3.13/3.14 classified-supported but 3.12 is safest |
| `pyarrow` | `>=24.0.0` | Do not downgrade; feather+parquet writers depend on it |
| `pandas` | `>=2.3.3,<3.0.0` | For the inspection utility |
| `fsspec` | `==2026.2.0` | Pinned exact; catalog filesystem layer |

---

## Sources

- `pyproject.toml` (this repo) — versions: `nautilus_trader 1.229.0`, Python `>=3.12,<3.15`, pyarrow `>=24.0.0`, pandas, fsspec — HIGH
- `nautilus_trader/adapters/bybit/config.py:34-90` — `BybitDataClientConfig` fields incl. `product_types: tuple[BybitProductType, ...]` — HIGH
- `nautilus_trader/adapters/bybit/__init__.py:28-71` — public exports (`BybitProductType`, factories, configs) — HIGH
- `examples/live/bybit/bybit_data_tester.py` — live LINEAR node setup, BarType grammar, subscribe wiring — HIGH
- `nautilus_trader/test_kit/strategies/tester_data.py:127-256` — exact `subscribe_*` signatures + data classes for all six+ types — HIGH
- `nautilus_trader/persistence/catalog/parquet.py:103-394,2523-2573` — `ParquetDataCatalog` ctor, `write_data` batch semantics, not-threadsafe warning, `convert_stream_to_data` — HIGH
- `nautilus_trader/persistence/writer.py:50-148` — `StreamingFeatherWriter`, `RotationMode`, per-instrument data types — HIGH
- `nautilus_trader/persistence/config.py:28-87` — `StreamingConfig` fields + `as_catalog()` — HIGH
- `nautilus_trader/system/kernel.py:587-605` — kernel auto-wiring of streaming writer to bus `"*"` — HIGH
- `crates/adapters/bybit/src/websocket/parse.rs:449-667` — linear ticker → FundingRate/Mark/Index; open_interest only on OptionGreeks — HIGH (this is the open-interest gap evidence)
- `examples/live/bybit/bybit_options_data_collector.py:608-776` — the custom-parquet anti-pattern to avoid — HIGH

---
*Stack research for: NautilusTrader Bybit live data recorder*
*Researched: 2026-06-13*
