# Multi-Venue Market Data Collector

Continuously records dYdX, Bybit and Hyperliquid market data to one local `ParquetDataCatalog`: 1-second book/trade snapshots (`DydxSecondSnapshot` — top-20 levels plus that second's trade OHLC/volume/counts), mark/index prices, funding rates, open interest and instrument definitions. Individual `TradeTick`s are folded into the second's snapshot and **not** stored raw, and `order_book_deltas` are a per-instrument opt-in (`store_order_book_deltas`) that is off by default and exists **for dYdX only** — `bybit_collector`/`hyperliquid_collector` take a flat `instruments = ["..."]` list with no per-instrument options. See `docs/DATA_INTEGRITY_AUDIT.md` D-45. Coverage is not uniform across venues either: a Bybit **spot** id yields trades and book only — no mark/index price, no funding rate (`bybit_collector/client.py:124-125` subscribes the ticker for `LINEAR` alone) and no open interest (Bybit spot has none). The catalog is Nautilus-native — load it directly into backtests with zero conversion.

All three collectors are the same class: `collector_core.collector.Collector` (the write gate and every invariant check), subclassed by a thin per-venue package (`dydx_collector/`, `bybit_collector/`, `hyperliquid_collector/`) that supplies the venue's client and at most a few hook overrides. `make up` starts one container per venue from one image.

---

## Prerequisites

- Docker + Docker Compose
- The `nautilus-trader-base` image built once (see below)

---

## Quick reference

Run all `make` commands from `troll/`:

| Command | What it does |
|---|---|
| `make build-base` | Build the base Nautilus image (~15 min, once only) |
| `make up` | Build collector image and start collecting |
| `make down` | Stop containers (catalog data is preserved) |
| `make logs` | Tail collector logs |
| `make web` | Open Dozzle log viewer in browser |
| `make prune` | Delete `order_book_deltas` older than 14 days |
| `make prune-dry` | Preview what `prune` would delete |

---

## First-time setup: build the base image

The base image compiles Nautilus from source. It takes ~15 minutes but only needs to be rebuilt when `nautilus_trader` core changes.

From `troll/`:

```bash
make build-base
```

---

## Configure instruments

Each venue has its own `config.toml`; `docker-compose.yml` mounts each one into its own container:

| Venue | File on the host | Mounted as |
|---|---|---|
| dYdX | `troll/config.toml` | `/app/dydx_collector/config.toml` (`rw` — the control plane writes it back) |
| Bybit | `troll/bybit_collector/config.toml` | `/app/bybit_config.toml` (`:ro`) |
| Hyperliquid | `troll/hyperliquid_collector/config.toml` | `/app/hyperliquid_config.toml` (`:ro`) |

The thresholds every venue shares are the fields of `collector_core/config.py`'s `CoreConfig` (`environment`, `catalog_path`, `flush_interval_seconds`, `snapshot_interval_seconds`, `stale_book_seconds`, `crossed_resync_seconds`, `stale_trade_seconds`, `seen_trade_ids`, `feed_stale_seconds`, `book_crosscheck_seconds`, `instruments`); a venue adds keys only where it needs them (Bybit's `open_interest_poll_seconds`, dYdX's control-plane keys). One name does not carry over: dYdX's TOML key for the network is `network`, not `environment` — `DydxConfig.__post_init__` derives `environment` from it, so an `environment = ...` line in `troll/config.toml` is read by nothing.

**Two loaders, and only one of them is strict.** Bybit and Hyperliquid go through `core_config_from_dict`, which rejects an unknown key outright rather than letting a misspelt threshold fall back to a default. dYdX's `load_config` (`dydx_collector/config.py`) predates the core and still reads key-by-key: it ignores unknown keys silently, and it never reads `stale_book_seconds`, `crossed_resync_seconds`, `stale_trade_seconds`, `seen_trade_ids`, `feed_stale_seconds` or `book_crosscheck_seconds` at all — dYdX always runs `CoreConfig`'s defaults for those six. Setting one of them in `troll/config.toml` neither takes effect nor errors.

dYdX (`troll/config.toml`) — hot-reloaded every `config_reload_seconds`, no restart needed:

```toml
network = "mainnet"
catalog_path = "catalog"          # relative to the container's /app — maps to ./catalog on the host
flush_interval_seconds = 60
config_reload_seconds = 30        # hot-reload: edit this file while running to add/remove instruments
open_interest_poll_seconds = 300
instruments = [
    { id = "BTC-USD-PERP.DYDX" },
    { id = "ETH-USD-PERP.DYDX", store_order_book_deltas = true, retain_hours = 48 },
]
```

Bybit and Hyperliquid take a plain list of ids and are read once at startup (no hot-reload):

```toml
environment = "mainnet"
catalog_path = "/app/catalog"
instruments = ["BTCUSDT-LINEAR.BYBIT", "BTCUSDT-SPOT.BYBIT"]
```

Add any dYdX perpetual in `<BASE>-USD-PERP.DYDX` format, any Bybit linear/spot id in `<SYMBOL>-LINEAR.BYBIT`/`<SYMBOL>-SPOT.BYBIT` format, and any Hyperliquid perp in `<BASE>-USD-PERP.HYPERLIQUID` format.

---

## Deploy

From `troll/`:

```bash
make up        # build collector image (seconds) and start everything
make logs      # tail live collector output
make web       # open Dozzle log viewer (http://localhost:8080)
```

The catalog appears at `troll/dydx_collector/catalog/` on the host, owned by your user (uid 1000). Under `docker-compose.yml` all three collectors mount that one root (and the `troll/dydx_collector/candles/` directory, with a separate `candles_<venue>.db` per collector via `CANDLES_DB_PATH`), so `data_api` and every backtest read every venue from a single catalog — the path keeps its historical `dydx_collector/` name. The sharing is the compose mounts, not the config: Bybit's and Hyperliquid's `catalog_path` is the container-absolute `/app/catalog`, so running either outside Docker needs that value changed.

**Web dashboard:** `make up` starts `data_api`, which serves the React UI on `http://localhost:9100` — rankings, per-coin candlestick/indicator charts, 31-day metrics history, and docs. Reads directly from the catalog — no collector restart needed.

---

## Remote access via Tailscale + SSH tunnel

The dashboard, Redis, and Dozzle all bind to `127.0.0.1` only (see `docker-compose.yml`) —
nothing is reachable from the public internet or even the tailnet directly. Access is via
an SSH tunnel over Tailscale, so the only thing ever exposed is SSH itself.

**One-time VPS setup:**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up          # follow the auth link, note the VPS's tailscale IP (tailscale ip -4)
make enable-boot           # docker starts on reboot
make enable-firewall       # default-deny inbound + rate-limited SSH + tailscale0
make harden-ssh            # key-only SSH auth -- confirm your key works first!
```

**From your PC**, with Tailscale running locally too:

```bash
ssh -N -L 9100:127.0.0.1:9100 -L 8080:127.0.0.1:8080 you@<vps-tailscale-ip>
```

Leave that running, then open `http://localhost:9100` (dashboard) or
`http://localhost:8080` (Dozzle logs) in your own browser. `-N` means the SSH session
does nothing but hold the tunnel open — no shell needed.

**Feed-health alerts:** since nobody is watching the dashboard continuously behind
the tunnel, set `WATCHDOG_NTFY_URL` (e.g. `https://ntfy.sh/<your-private-topic>`) as
an environment variable on the `collector` service in `docker-compose.yml` to get a
push notification if every subscribed instrument's order book goes stale for 30s+
(see OBS-01 in `CLAUDE.md`). Without it, the same condition is only logged.

---

## Stop / restart

```bash
make down   # stop containers, catalog persists
make up     # restart (rebuilds collector image automatically)
```

---

## Rebuild the base image

Only needed when `nautilus_trader` Python/Rust core changes (e.g. after a `git pull` that touches `crates/` or `nautilus_trader/`):

```bash
make build-base   # ~15 min
make up           # rebuild collector layer on top and restart
```

---

## Inspect the catalog

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog

catalog = ParquetDataCatalog("troll/dydx_collector/catalog")
catalog.instruments()
catalog.trade_ticks(instrument_ids=["BTC-USD-PERP.DYDX"])
catalog.order_book_deltas(instrument_ids=["BTC-USD-PERP.DYDX"])
```

---

## Run a backtest

```bash
# from the repo root (default catalog path is troll/dydx_collector/catalog)
PYTHONPATH=troll python -m ml_signals.strategies.backtest_dydx
```

Streams trade ticks from the catalog and aggregates bars internally at a configurable wall-clock interval. Adjust `bar_interval` (e.g. `"1-SECOND"`, `"1-MINUTE"`, `"5-MINUTE"`), `buy_threshold`, `sell_threshold` by passing args to `run()`.

By default `run()` backtests every coin in the live Watchlist (requires `data_api` running -- if it's not reachable, `run()` raises a clear error rather than a raw connection traceback) and returns a `dict[str, BacktestResult]` keyed by symbol. Pass `symbols=["BTC-USD-PERP.DYDX", ...]` to backtest an explicit coin-set instead. A Watchlist coin with no matching catalog instrument yet is skipped (logged as a warning, not a crash) rather than aborting the whole run -- check the logs if the returned dict has fewer entries than expected.

See [`ml_signals/BACKTESTING.md`](ml_signals/BACKTESTING.md) for the other backtest runners, how to build a new strategy, and which data feed to subscribe to.

---

## What gets collected

| Data type | Nautilus type | Source |
|---|---|---|
| Trades | `TradeTick` | WebSocket trades channel |
| Order book | `OrderBookDelta` | WebSocket orderbook channel |
| Bars | `Bar` | WebSocket candles channel |
| Mark price | `MarkPriceUpdate` | WebSocket markets channel |
| Index price | `IndexPriceUpdate` | WebSocket markets channel |
| Funding rate | `FundingRateUpdate` | WebSocket markets channel |
| Instruments | `CryptoPerpetual` | REST on startup |
| Open interest | `OpenInterest` | REST poll every 5 min |
