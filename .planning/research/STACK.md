# Stack Research — dYdX Perpetuals Data Recorder (v1.1)

**Domain:** NautilusTrader live market-data recorder, adding dYdX v4 perpetuals to the existing Bybit collector
**Researched:** 2026-06-15
**Confidence:** HIGH (every class/field name read directly from this fork's source — not training recall)

> All findings are read/grep-verified against source in `/home/mrqdt/code/nautilus_trader_fork`. Exact file:line references are inline so the roadmap author can re-verify. The Bybit recorder, Nautilus catalog mechanics, and StreamingConfig/feather conversion path are NOT re-researched here (validated in the prior milestone).

## Headline

**No new dependencies, no new packages, no Rust changes.** The dYdX v4 adapter is already built into this fork and is Rust-backed via PyO3 (`nautilus_pyo3.DydxHttpClient`). Adding the dYdX recorder is a pure **wiring + config** task: swap the Bybit factory/config classes for the dYdX equivalents, adjust instrument-ID formatting, and surface one network field in TOML. The recorder stays **data-only** — the entire execution/wallet/gRPC surface is untouched.

## Recommended Stack (additions for dYdX)

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| `nautilus_trader.adapters.dydx` | bundled with this fork (`nautilus_trader==1.229.0`) | The dYdX v4 data client, factory, config, provider | Already exists at `nautilus_trader/adapters/dydx/`; data path is validated (trades, quotes, book deltas, bars, funding, mark/index). NEVER modify it (CLAUDE.md core-untouched rule). |
| `DydxDataClientConfig` | same | Python config object for the dYdX data client | The exact config class the `DydxLiveDataClientFactory` expects (`config.py:28`). Drop-in replacement for `BybitDataClientConfig`. |
| `DydxLiveDataClientFactory` | same | Builds the `DydxDataClient` from config | Registered on the node via `node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)` (`factories.py:97`), mirroring the Bybit wiring at `recorder.py:123`. |
| `DydxNetwork` (pyo3 enum) | same | Network selection: `MAINNET` / `TESTNET` | The only env-style field the data client needs (`config.py:60`). Replaces Bybit's `BybitEnvironment`. Resolves default endpoints automatically. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `nautilus_pyo3.DydxHttpClient` | bundled | Rust-backed Indexer HTTP client | Created internally by the factory via `get_cached_dydx_http_client` (`factories.py:38`). The recorder never instantiates it directly. |
| `DydxInstrumentProvider` | bundled | Loads dYdX instrument definitions from the Indexer API | Wired automatically by the factory from `config.instrument_provider` (`factories.py:140`). Used for the same `load_ids` pattern as Bybit and for HOT-add instrument loading. |
| `InstrumentProviderConfig` | core nautilus | `load_ids=frozenset(instrument_ids)` | Identical usage to the Bybit recorder (`recorder.py:82`). No change. |
| `FundingRateUpdate` (model) | core nautilus | dYdX funding-rate data type | Already supported by `DydxDataClient._subscribe_funding_rates` (`data.py:427`). The recorder's existing separate funding-rate writer path applies. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `CUSTOM_ENCODINGS` (`nautilus_trader.common.config`) | Register a JSON encoder for the `DydxNetwork` pyo3 enum | REQUIRED. `NautilusKernel._setup_streaming()` serializes the full `TradingNodeConfig` (incl. `data_clients`) via `config.json()`, and the pyo3 enum has no default encoder — same failure the Bybit recorder hit at `recorder.py:49-50`. Register `CUSTOM_ENCODINGS[DydxNetwork] = lambda v: v.name`. **Gotcha:** `DydxNetwork.MAINNET.name == "mainnet"` (lowercase), unlike Bybit's uppercase `.name`. |
| existing `recorder.toml` loader | TOML-driven config + hot-reload | Reuse the existing `load_recorder_config` shape; add an `environment`/network field that maps to `DydxNetwork`. |

## Installation

```bash
# Nothing to install. The dYdX adapter and its Rust client are already compiled
# into this fork's nautilus_trader build. Verify availability only:
python -c "from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory; \
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig; \
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork; \
print('dydx adapter OK', DydxNetwork.MAINNET, DydxNetwork.TESTNET)"
```

> Confirmed: the dYdX adapter imports **no** Python gRPC, `v4-proto`, `v4-client`, `cosmos`, or `bech32` packages (`grep` over `adapters/dydx/*.py` found zero such imports). All chain/transport logic lives in the Rust crate `crates/adapters/dydx/` and is reached through PyO3. The Python-side gRPC concerns (`base_url_grpc`, `grpc_rate_limit_per_second`) exist **only** on `DydxExecClientConfig`, which the recorder must NOT use.

## dYdX Instrument ID / Symbol Format (vs Bybit)

| Aspect | dYdX | Bybit (existing) |
|--------|------|------------------|
| Venue suffix | `.DYDX` (`constants.py:23-24`) | `.BYBIT` |
| Perpetual symbol pattern | `{BASE}-USD-PERP` | `{BASE}USDT` (linear) / `{BASE}USDT` (spot) |
| Example full `InstrumentId` | `ETH-USD-PERP.DYDX`, `BTC-USD-PERP.DYDX` (verified in `docs/tutorials/grid_market_maker_dydx.md:155,314,620,628`) | `BTCUSDT-LINEAR.BYBIT` |
| Quote currency | Always `USD` (dYdX v4 perps are USDC-margined, symbol uses `USD`) | `USDT` |
| Product types | Perpetuals only — no spot, no product-type axis | LINEAR + SPOT (two axes) |

**Implication for TOML config:** the dYdX instrument list is a single flat list of `{BASE}-USD-PERP.DYDX` ids. There is **no `linear`/`spot` split** and **no per-product depth-validation table** like the Bybit recorder's `_LINEAR_VALID_DEPTHS`/`_SPOT_MAX_DEPTH` (`config.py:43-49`). The `InstrumentId.from_str(...)` fail-fast boundary still applies.

## DydxDataClientConfig — Field Reference (verified `config.py:28-67`)

| Field | Type | Default | Recorder relevance |
|-------|------|---------|--------------------|
| `environment` | `DydxNetwork \| None` | `None` → MAINNET | **Surface in TOML.** Maps the recorder's `environment="mainnet"`/`"testnet"` string to the enum. |
| `wallet_address` | `str \| None` | `None` | Legacy/no-op for the data client ("The public data client does not use wallet credentials"). **Leave unset.** |
| `base_url_http` | `str \| None` | `None` → default per network | Optional override. Default mainnet = `https://indexer.dydx.trade` (verified `crates/adapters/dydx/src/common/consts.rs:62`). |
| `base_url_ws` | `str \| None` | `None` → default per network | Optional override. Default mainnet = `wss://indexer.dydx.trade/v4/ws`. |
| `proxy_url` | `str \| None` | `None` | Optional. |
| `bars_timestamp_on_close` | `bool` | `True` | Keep default for consistency with Bybit bars. |
| `max_retries` | `PositiveInt \| None` | `3` | Reuse Bybit defaults. |
| `retry_delay_initial_ms` | `PositiveInt \| None` | `1_000` | Reuse. |
| `retry_delay_max_ms` | `PositiveInt \| None` | `10_000` | Reuse. |
| `instrument_provider` | (inherited from `LiveDataClientConfig`) | — | Set `InstrumentProviderConfig(load_ids=frozenset(instrument_ids))` exactly as Bybit does. |

Supported data subscriptions on `DydxDataClient` (verified `data.py`): trades (`_subscribe_trade_ticks:407`), quotes (`_subscribe_quote_ticks:394`, synthesized from top-of-book), order-book deltas (`_subscribe_order_book_deltas:376`), bars (`_subscribe_bars:421`), funding rates (`_subscribe_funding_rates:427`), mark prices (`_subscribe_mark_prices:413`), index prices (`_subscribe_index_prices:417`).

## Environment / Endpoint Considerations

| Concern | Finding |
|---------|---------|
| Env vars needed by the **data** client | **None.** `DYDX_WALLET_ADDRESS`, `DYDX_TESTNET_WALLET_ADDRESS`, `DYDX_PRIVATE_KEY`, `DYDX_TESTNET_PRIVATE_KEY` are read only in `execution.py:197-214` — never in `data.py`/`factories.py` data path. |
| Default endpoints | Resolved automatically from `DydxNetwork`. Mainnet HTTP `https://indexer.dydx.trade`, WS `wss://indexer.dydx.trade/v4/ws` (`crates/adapters/dydx/src/common/consts.rs:60-65`). Testnet has its own block (`consts.rs:98+`). |
| Network selection | Single TOML string → `DydxNetwork.MAINNET`/`DydxNetwork.TESTNET`. Default to MAINNET (matches project's 24/7 archival intent). |
| Version pinning | None beyond the fork's existing `nautilus_trader==1.229.0` pin. The dYdX client ships in the same wheel. |

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Reuse the existing TOML loader + add a network field | A brand-new dYdX-specific config module | Only if the dYdX instrument schema diverges enough (e.g. no product types) that overloading `InstrumentEntry` is messier than a parallel `DydxInstrumentEntry`. Likely a clean fork of `load_recorder_config` minus the linear/spot depth tables. |
| `CUSTOM_ENCODINGS[DydxNetwork]` | Pass network as a plain string and convert in `main()` | Conversion still needs the enum on the config object for the factory, and the streaming serializer still serializes the enum — encoder registration is unavoidable. |

## What NOT to Use / NOT to Add

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `DydxExecClientConfig`, `DydxLiveExecClientFactory`, `DydxExecutionClient` | Recorder is **data-only**; execution pulls in wallet creds, private keys, gRPC, rate limiting, subaccounts — none needed and a security/footprint liability | Only `DydxDataClientConfig` + `DydxLiveDataClientFactory`. |
| `wallet_address` on the data config | No-op legacy field for the data client | Leave `None`. |
| Any Python gRPC / `v4-proto` / `v4-client` / cosmos / bech32 package | The adapter is Rust-backed via PyO3; these are not imported anywhere in `adapters/dydx/*.py` | Nothing to install. |
| Open-interest capture | `DydxDataClient` does **not** support open interest (confirmed: no `_subscribe_open_interest` / `OpenInterest` handling in `data.py`) | Record mark price, index price, and funding rate instead; document OI as an explicit gap (carries the Bybit-milestone OI-gap note forward). |
| Bybit's linear/spot depth-validation tables | dYdX has no spot and no product-type axis | Single flat perp list; validate only via `InstrumentId.from_str`. |
| Editing `nautilus_trader/adapters/dydx/` | Fork core-untouched rule (CLAUDE.md) | Wire everything from new code under `scripts/` (new `scripts/dydx_recorder/` or a shared/parameterized recorder). |

## Version Compatibility

| Component | Compatible With | Notes |
|-----------|-----------------|-------|
| `nautilus_trader.adapters.dydx` | this fork's `nautilus_trader==1.229.0` | Same wheel; no separate version to pin. |
| `DydxNetwork` pyo3 enum | `msgspec` config serialization via `CUSTOM_ENCODINGS` | `.name` is lowercase (`"mainnet"`/`"testnet"`) — verified at runtime. Use `lambda v: v.name`; round-trip the lowercase string back to the enum in the loader. |
| Streaming/feather conversion path | unchanged | dYdX produces the same Nautilus data types already in `include_types` (Trade/Quote/OrderBookDeltas/Bar/MarkPriceUpdate/IndexPriceUpdate); funding rate handled by the existing dedicated writer. |

## Sources

- `nautilus_trader/adapters/dydx/config.py:28-67` — `DydxDataClientConfig` fields (HIGH, source-read)
- `nautilus_trader/adapters/dydx/config.py:70-131` — `DydxExecClientConfig` (the surface to AVOID) (HIGH)
- `nautilus_trader/adapters/dydx/factories.py:97-153` — `DydxLiveDataClientFactory.create` wiring (HIGH)
- `nautilus_trader/adapters/dydx/constants.py:23-25` — `DYDX` venue / `.DYDX` suffix (HIGH)
- `nautilus_trader/adapters/dydx/providers.py` — `DydxInstrumentProvider`, Rust-backed, no Python deps (HIGH)
- `nautilus_trader/adapters/dydx/data.py:376-487` — supported subscriptions; no open-interest path (HIGH)
- `nautilus_trader/adapters/dydx/execution.py:197-214` — env vars confined to execution (HIGH)
- `crates/adapters/dydx/src/common/consts.rs:60-98` — default mainnet/testnet endpoints (HIGH)
- `docs/tutorials/grid_market_maker_dydx.md:155,314,620,628` — `ETH-USD-PERP.DYDX` / `BTC-USD-PERP.DYDX` id format (HIGH)
- `scripts/bybit_recorder/recorder.py:49-50,67-123` — existing factory/`CUSTOM_ENCODINGS`/`TradingNodeConfig` pattern to mirror (HIGH)
- Runtime check: `DydxNetwork.MAINNET.name == "mainnet"` (HIGH, executed)

---
*Stack research for: dYdX v4 perpetuals data recorder (v1.1 milestone)*
*Researched: 2026-06-15*
