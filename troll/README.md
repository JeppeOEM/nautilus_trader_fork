# dYdX Market Data Collector

Continuously records dYdX market data (trades, order book deltas, bars, mark/index prices, funding rates, open interest) to a local `ParquetDataCatalog`. The catalog is Nautilus-native — load it directly into backtests with zero conversion.

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
| `make dashboard` | Start the ml_signals dashboard on port 8765 |

---

## First-time setup: build the base image

The base image compiles Nautilus from source. It takes ~15 minutes but only needs to be rebuilt when `nautilus_trader` core changes.

From `troll/`:

```bash
make build-base
```

---

## Configure instruments

Edit `troll/dydx_collector/config.toml`:

```toml
network = "mainnet"
catalog_path = "catalog"          # relative to the container's /app — maps to ./catalog on the host
flush_interval_seconds = 60
config_reload_seconds = 30        # hot-reload: edit this file while running to add/remove instruments
open_interest_poll_seconds = 300

[[instruments]]
id = "BTC-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]

[[instruments]]
id = "ETH-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]
```

Add any dYdX perpetual in `<BASE>-USD-PERP.DYDX` format. The collector hot-reloads this file every `config_reload_seconds` — no restart needed.

---

## Deploy

From `troll/`:

```bash
make up        # build collector image (seconds) and start everything
make logs      # tail live collector output
make web       # open Dozzle log viewer (http://localhost:8080)
```

The catalog appears at `troll/dydx_collector/catalog/` on the host, owned by your user (uid 1000).

**OFI / data dashboard:** run `make dashboard` in a separate terminal to start the ml_signals dashboard on `http://localhost:8765`. Shows per-coin footprint charts, Microprice, Order Flow Imbalance, and data coverage/gap detection. Reads directly from the catalog — no collector restart needed.

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
ssh -N -L 8765:127.0.0.1:8765 -L 8080:127.0.0.1:8080 you@<vps-tailscale-ip>
```

Leave that running, then open `http://localhost:8765` (dashboard) or
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
cd troll/ml_signals
python backtest_dydx.py
```

Streams trade ticks from the catalog and aggregates bars internally at a configurable wall-clock interval. Adjust `bar_interval` (e.g. `"1-SECOND"`, `"1-MINUTE"`, `"5-MINUTE"`), `buy_threshold`, `sell_threshold` by passing args to `run()`.

By default `run()` backtests every coin in the live Watchlist (requires `make dashboard` running -- if it's not reachable, `run()` raises a clear error rather than a raw connection traceback) and returns a `dict[str, BacktestResult]` keyed by symbol. Pass `symbols=["BTC-USD-PERP.DYDX", ...]` to backtest an explicit coin-set instead. A Watchlist coin with no matching catalog instrument yet is skipped (logged as a warning, not a crash) rather than aborting the whole run -- check the logs if the returned dict has fewer entries than expected.

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
| Open interest | `DydxOpenInterest` | REST poll every 5 min |
