# Database Setup

Four separate persistence mechanisms exist in `troll/`, each with a distinct job. None
of them overlap in purpose — see [Data Dictionary](DATA_DICTIONARY.md) for the full
schema of what actually flows *through* Redis/the catalog, this doc is about the
storage layer itself: where each store lives, who owns writes, and how to reach it.

| Mechanism | What it's for | Durable? | Network-reachable? |
|---|---|---|---|
| **Redis** | Live pub/sub + small "latest value" cache between services | No (in-memory, no persistence configured) | Yes — `127.0.0.1:6379` only |
| **`metrics.db`** (SQLite) | 31-day rolling per-instrument metrics history | Yes | No (file only) |
| **`fills.db`** (SQLite) | Append-only live-paper trade/fill ledger | Yes | No (file only) |
| **Parquet catalog** | Raw market data archive (trades, book deltas, bars, ...) | Yes | No (file only) |
| **`config.toml`** | Collector's live instrument registry (pin/unpin state) | Yes | No (file only) |

All of this is described by `troll/docker-compose.yml`, and `troll/CLAUDE.md`'s **SEC-01**
governs every port here: *localhost-only, always* — nothing in this stack is ever bound
to a public interface. Remote access is via SSH tunnel only (§6).

---

## 1. Redis — the live message bus

**Image:** `redis:8-alpine`, container `dydx-redis`, bound `127.0.0.1:6379:6379`
(`docker-compose.yml`). Every service connects via `redis.asyncio` using
`REDIS_URL=redis://127.0.0.1:6379`.

**No persistence, no TTLs, no complex types.** Nothing here uses Redis's own
persistence (RDB/AOF) — if the container restarts, everything in it is gone and
services simply republish on their next cycle. No key anywhere has an expiry set
(no `.expire()`/`.setex()`); staleness is instead judged consumer-side (age of the
payload's own timestamp field). Only plain string `GET`/`SET` and `PUBLISH`/`SUBSCRIBE`
are used — no hashes, sorted sets, streams, or lists.

Think of Redis here purely as **a live wire between processes**, not a database you'd
ever back up or migrate.

### 1.1 Pub/Sub channels

| Channel | Publisher | Subscribers | Payload |
|---|---|---|---|
| `snapshots:raw` | `dydx_collector/collector.py` — every ~1s tick | `ranking_engine`, `dashboard`, `bot_tui` | JSON list of `DydxSecondSnapshot` dicts (book top-20 + trade volume/count), one per collected instrument |
| `rankings:live` | `ranking_engine/engine.py` (**sole publisher**, AD-9) | `dashboard`, `bot_tui` | JSON: `{mode, updated_at, ranks: [...], stale_instrument_ids: [...]}` — every rank row carries volume/volatility/OFI/OBI/microprice/spread/CVD/price/pct-change fields |
| `ranking:control` | `bot_tui` (mode toggle) | `ranking_engine` | `{"mode": "volume"\|"volatility"}` |
| `collector:control` | `bot_tui` | `dydx_collector/collector.py` | `{"action": "start"\|"unpin"\|"stop"\|"pin_top_liquid", "id": "<instrument_id>"|null}` |
| `collector:status` | `dydx_collector/collector.py` | `bot_tui` | Per-instrument `{id, pinned, liquid, last_trade_ts}`, or removal/unpin summaries |
| `bots:control` | `bot_tui` | `live_paper/bot_status.py` | `{"bot_id": "...", "action": "start"\|"stop"}` |
| `bots:status` | `live_paper/bot_status.py` — every 5s heartbeat | `bot_tui` | `{bot_id, strategy, symbol, running, position_side, net_exposure, realized_pnl, unrealized_pnl, win_rate, closed_trades, ...}` |

### 1.2 Plain keys (GET/SET, no TTL)

| Key | Writer | Reader | Shape |
|---|---|---|---|
| `bots:incidents:{bot_id}` | `live_paper/bot_status.py` | `bot_tui` (polled) | JSON list of `{type: "data_stale"\|"process_start", started_at, ended_at}`, capped at 50 entries |
| `bots:history:{bot_id}:{day\|week\|month\|all}` | `live_paper/trade_history.py` | `bot_tui` (polled) | JSON blob of performance stats derived from `fills.db` |

### 1.3 Who owns what

- **`ranking_engine`** is the sole computer of every rolling-window ranking indicator
  (OFI/OBI z-scores, volatility). `dashboard`/`bot_tui` only ever parse
  `rankings:live` — neither runs its own copy of these indicators (`troll/CLAUDE.md`
  SSOT-02).
- **`dydx_collector`** is the sole writer of `snapshots:raw`/`collector:status`, and
  the sole actor on `collector:control`.
- **`live_paper`** is the sole writer of `bots:status`/`bots:incidents:*`/
  `bots:history:*`, and the sole actor on `bots:control`.
- **`bot_tui`** never writes status data — it only publishes control messages and
  reads everything else. It never touches SQLite or the catalog directly either;
  every value it shows arrives via Redis.

### 1.4 Nautilus's own internal Cache also lives here

Separately from everything above, `live_paper/node.py` configures Nautilus's built-in
`CacheConfig(database=DatabaseConfig(type="redis", ...))` for its `TradingNode` —
this is `nautilus_trader`'s own order/position/account state persistence, not
`troll/`-authored code, and it's wired to the *same* `dydx-redis` container rather
than a separate store. `DatabaseConfig` generically supports Postgres too (it's
listed in the top-level tech stack for that reason), but nothing in `troll/` ever
connects to Postgres — there's no `postgres` service in `docker-compose.yml` and no
Postgres client anywhere in the code. If you're looking for a Postgres instance to
tunnel to or inspect, there isn't one.

---

## 2. SQLite databases

Both use raw `sqlite3` (no ORM) with `PRAGMA journal_mode=WAL`. WAL mode is why both
are bind-mounted as **directories**, not single files, in `docker-compose.yml` — WAL
creates `<name>.db-wal`/`<name>.db-shm` sidecar files next to the main one, which a
single-file mount can't expose.

### 2.1 `metrics.db` — owned by `ranking_engine/metrics_store.py`

- **Path:** `./dydx_collector/metrics/metrics.db` on the host (`METRICS_DB_PATH` env,
  mounted `/app/metrics_dir/metrics.db` in the `ranking_engine`/`dashboard` containers).
- **Table:** `snapshots(ts, instrument_id, price, pct_1h, pct_24h, volatility, ofi,
  microprice, spread, rank, volume24h)`, primary key `(ts, instrument_id)`.
- **Purpose:** 31-day rolling per-instrument history, upserted every 60s, pruned to
  `retain_days=31` on every write. Powers the dashboard's per-coin history chart.
- **Writer:** `ranking_engine` exclusively. **Reader:** `dashboard` only (mounted
  `:ro`).
- Note: the `ofi` column here is top-of-book-only `OrderFlowImbalance` — a *different*
  metric from the multi-level `ofi_10_z`/`ofi_3/5/10` fields that live only in
  `rankings:live`. See [Data Dictionary](DATA_DICTIONARY.md) if that distinction
  matters for what you're building.

### 2.2 `fills.db` — owned by `live_paper/fills_store.py`

- **Path:** `./live_paper/data/fills.db` on the host (`FILLS_DB_PATH` env).
- **Table:** `fills(ts, bot_id, side, price, qty, realized_pnl,
  position_realized_pnl)`.
- **Purpose:** append-only, event-sourced fill log — one row per `OrderFilled` event,
  written directly off the strategy's message bus. This exists specifically because
  Nautilus's `Cache.positions_closed()` silently discards prior closed positions on a
  NETTING-mode position reopen — `fills.db` is the durable source of truth trade
  history is rebuilt from, not the Nautilus cache.
- **Writer:** `live_paper/trade_history.py`. **Readers:** `live_paper/bot_status.py`
  (win-rate stats) and `trade_history.py` itself, to build the `bots:history:*` Redis
  blobs above. Nothing outside `live_paper/` reads this file directly — `bot_tui`/
  `dashboard` only ever see it via Redis.

---

## 3. Parquet catalog

`ParquetDataCatalog`, mounted at `./dydx_collector/catalog` (read-write for
`collector`, read-only for `dashboard`/`ranking_engine`). This is the append-only
archive of every raw market data type the collector ingests — trades, order-book
deltas, bars, mark/index price, funding rate, second-snapshots, open interest,
instrument definitions — written exclusively via `ParquetDataCatalog.write_data()`
(`troll/CLAUDE.md` NAUT-02: no hand-rolled schemas). Its full schema, per type, is
already documented in [Data Dictionary](DATA_DICTIONARY.md) §1 — this doc won't
repeat it.

---

## 4. `config.toml` — the collector's instrument registry

Easy to mistake for static config, but `troll/config.toml` is actually **mutable,
persisted runtime state** — the one file the running system rewrites on its own.

- **Owner:** `dydx_collector/config.py` (`load_config()`/`save_config()`, via
  `tomllib`/`tomli_w`). `save_config()` does a full rewrite, not a patch — hand-added
  comments won't survive a control action.
- **Read:** `dydx_collector/collector.py` hot-reloads it every 30s, so a hand-edit is
  picked up without a restart.
- **Write:** every `collector:control` action (start/unpin/stop/pin_top_liquid)
  triggers a rewrite.
- **Content:** network, catalog path, flush/snapshot intervals, liquidity threshold,
  and the `[[instruments]]` array (`id`, `pinned`, `store_order_book_deltas`,
  `retain_hours`) — this array is the single authoritative source of what the
  collector currently subscribes to.
- **Docker mount:** the only `rw` config mount in `docker-compose.yml` — every other
  config file (`live_paper/config.toml` included) is mounted `:ro` and never written
  back by the running process.
- `bot_tui` never edits this file directly — it only publishes `collector:control`
  messages, keeping filesystem access to a single container.

---

## 5. Local layout on disk

```
troll/
├── config.toml                    # collector instrument registry (rw, §4)
├── dydx_collector/
│   ├── catalog/                   # Parquet catalog (§3)
│   └── metrics/
│       ├── metrics.db             # + -wal/-shm sidecars (§2.1)
├── live_paper/
│   ├── config.toml                # static per-bot config (ro)
│   └── data/
│       └── fills.db               # + -wal/-shm sidecars (§2.2)
```

Redis has no on-disk footprint here (no RDB/AOF configured) — it's purely in-memory,
per §1.

---

## 6. Viewing this remotely

Every port above is bound `127.0.0.1`-only on the host (SEC-01) — none of it is
reachable from outside the box directly. The way in is an SSH tunnel, via the
`nifelheim` alias already set up in `~/.ssh/config` and wired into `troll/Makefile`:

| Command | What it does |
|---|---|
| `make remote-db` | Tunnels Redis (6379) to `localhost:6379` so a GUI client (RedisInsight, TablePlus, Another Redis Desktop Manager, ...) on your machine can connect to it as if it were local. |
| `make remote-db-stop` | Kills that tunnel. |
| `make remote-web` | Tunnels the dashboard (8765) and opens it in your browser. |
| `make remote-web-stop` | Kills that tunnel. |
| `make remote-tui` | SSHes in with a real TTY and runs `bot_tui` interactively — no port involved, it's a terminal app, not a network service. |

The two SQLite files and the Parquet catalog have no server to tunnel to — they're
just files on the remote host. To inspect them locally, `scp`/`rsync` a copy down
(e.g. `rsync -avz nifelheim:~/nautilus_trader_fork/troll/dydx_collector/metrics/ ./metrics/`)
rather than trying to tunnel a port for them.
